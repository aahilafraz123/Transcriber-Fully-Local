import glob
import logging
import os
import subprocess
import threading
import time
from datetime import datetime, date, timedelta

import sounddevice as sd
from flask import Flask, render_template, redirect, url_for, jsonify, request

import config
import audio_io
import transcription
import diarization

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("transcriber")

app = Flask(__name__)

RECORDINGS_DIR = os.path.join(os.path.dirname(__file__), "recordings")
MEETINGS_FILE = os.path.join(os.path.dirname(__file__), "meetings.json")
SAMPLE_RATE = config.SAMPLE_RATE

ACTIVE_BH_PATH = os.path.join(RECORDINGS_DIR, "_active_bh.wav")
ACTIVE_MIC_PATH = os.path.join(RECORDINGS_DIR, "_active_mic.wav")

_meetings_lock = threading.RLock()


def _load_meetings():
    if os.path.exists(MEETINGS_FILE):
        try:
            import json
            with open(MEETINGS_FILE, "r") as f:
                return json.load(f)
        except (ValueError, OSError) as e:
            logger.warning("Could not read meetings file: %s", e)
    return []


def _save_meetings():
    """Atomic write so a crash mid-save can't corrupt meetings.json."""
    import json
    with _meetings_lock:
        tmp = MEETINGS_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(meetings, f, indent=2)
        os.replace(tmp, MEETINGS_FILE)


meetings = _load_meetings()


def _get_meeting(meeting_id):
    with _meetings_lock:
        return next((m for m in meetings if m["id"] == meeting_id), None)


def _update_meeting(meeting_id, **fields):
    with _meetings_lock:
        m = next((m for m in meetings if m["id"] == meeting_id), None)
        if m is None:
            return None
        m.update(fields)
        _save_meetings()
        return m


@app.template_filter("meeting_date_label")
def meeting_date_label(iso_date_str):
    d = date.fromisoformat(iso_date_str)
    today = date.today()
    if d == today:
        return "Today"
    if d == today - timedelta(days=1):
        return "Yesterday"
    return d.strftime("%a, %b %-d")


@app.template_filter("format_duration")
def format_duration(minutes):
    m = int(minutes)
    s = int(round((minutes - m) * 60))
    return f"{m}:{s:02d}"


@app.template_filter("format_time")
def format_time(iso_str):
    dt = datetime.fromisoformat(iso_str)
    return dt.strftime("%-I:%M%p").lower()


@app.template_filter("speaker_label")
def speaker_label(raw, names=None):
    """Map raw diarization labels to friendly display names."""
    if names and raw in names:
        return names[raw]
    if raw == config.YOU_LABEL:
        return config.YOU_LABEL
    if raw.startswith("SPEAKER_"):
        try:
            return f"Speaker {int(raw.split('_')[-1]) + 1}"
        except ValueError:
            return raw
    return raw


_state_lock = threading.RLock()
_recording_state = {
    "is_recording": False,
    "is_paused": False,
    "bh_stream": None,
    "mic_stream": None,
    "bh_writer": None,
    "mic_writer": None,
    "bh_ch": 0,
    "mic_ch": 0,
    "bh_live": [],
    "mic_live": [],
    "start_time": None,
    "previous_output": None,
    "meeting_app": None,
    "live": None,
    "live_text": "",
    "transcribing": False,
}

_device_prefs = {"active_output": None}


def _cleanup_old_recordings():
    """Delete recording .wav files older than WAV_MAX_AGE_DAYS."""
    cutoff = time.time() - config.WAV_MAX_AGE_DAYS * 86400
    for wav_path in glob.glob(os.path.join(RECORDINGS_DIR, "meeting_*.wav")):
        try:
            if os.path.getmtime(wav_path) < cutoff:
                os.unlink(wav_path)
                logger.info("Cleaned up old recording: %s", os.path.basename(wav_path))
        except OSError:
            pass


def _detect_meeting_app():
    """Detect if Teams or Slack is in an active call."""
    try:
        result = subprocess.run(["ps", "aux"], capture_output=True, text=True)
        lines = result.stdout.lower()
        teams_running = "microsoft teams" in lines or "msteams" in lines or "teams.exe" in lines
        slack_running = "slack" in lines

        for app_name, running, needle in (
            ("Teams", teams_running, ("teams", "msteams")),
            ("Slack", slack_running, ("slack",)),
        ):
            if not running:
                continue
            try:
                lsof = subprocess.run(
                    ["lsof", "-i", "UDP", "-n", "-P"],
                    capture_output=True, text=True, timeout=5,
                )
                for line in lsof.stdout.split("\n"):
                    ll = line.lower()
                    if "udp" in ll and any(n in ll for n in needle):
                        return {"active": True, "app": app_name}
            except Exception:
                pass
    except Exception:
        pass
    return {"active": False, "app": None}


def _find_device(name_substr):
    devices = sd.query_devices()
    for i, d in enumerate(devices):
        if name_substr in d["name"].lower() and d["max_input_channels"] > 0:
            return i, d["max_input_channels"]
    return None, 0


def _switch_output(device_name):
    if not device_name:
        return False
    try:
        subprocess.run(["SwitchAudioSource", "-s", device_name], check=True)
        return True
    except Exception as e:
        logger.warning("Could not switch output to '%s': %s", device_name, e)
        return False


@app.route("/")
def index():
    return render_template("index.html", meetings=meetings,
                           is_recording=_recording_state["is_recording"])


@app.route("/start", methods=["POST"])
def start():
    if _recording_state["is_recording"]:
        return jsonify({"status": "already_recording"}), 409

    meeting_info = _detect_meeting_app()
    _recording_state["meeting_app"] = meeting_info.get("app")

    # Remember the real current output so we can restore it on stop.
    try:
        result = subprocess.run(["SwitchAudioSource", "-c"], capture_output=True, text=True)
        current = result.stdout.strip()
        if current and current != "Multi-Output Device":
            _recording_state["previous_output"] = current
        else:
            _recording_state["previous_output"] = config.DEFAULT_OUTPUT_DEVICE or None
    except Exception:
        _recording_state["previous_output"] = config.DEFAULT_OUTPUT_DEVICE or None

    _switch_output("Multi-Output Device")

    bh_idx, bh_ch = _find_device(config.BLACKHOLE_DEVICE)
    mic_idx, mic_ch = _find_device(config.MIC_DEVICE)

    if bh_idx is None:
        return jsonify({"error": "BlackHole device not found"}), 500

    # Fresh streaming writers (constant memory: frames go straight to disk).
    for p in (ACTIVE_BH_PATH, ACTIVE_MIC_PATH):
        try:
            os.path.exists(p) and os.unlink(p)
        except OSError:
            pass

    with _state_lock:
        _recording_state.update({
            "is_recording": True,
            "is_paused": False,
            "start_time": time.time(),
            "bh_ch": bh_ch,
            "mic_ch": mic_ch,
            "bh_live": [],
            "mic_live": [],
            "live": transcription.LiveTranscriber(),
            "live_text": "",
            "transcribing": False,
            "bh_writer": audio_io.open_writer(ACTIVE_BH_PATH, bh_ch),
            "mic_writer": audio_io.open_writer(ACTIVE_MIC_PATH, mic_ch) if mic_idx is not None else None,
        })

    _start_streams(bh_idx, bh_ch, mic_idx, mic_ch)
    return jsonify({"status": "recording", "meeting_app": _recording_state["meeting_app"]})


def _start_streams(bh_idx, bh_ch, mic_idx, mic_ch):
    def bh_callback(indata, frames, time_info, status):
        if _recording_state["is_recording"] and not _recording_state["is_paused"]:
            frame = indata.copy()
            writer = _recording_state["bh_writer"]
            if writer is not None:
                writer.write(frame)
            _recording_state["bh_live"].append(frame)

    bh_stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=bh_ch,
                               device=bh_idx, callback=bh_callback)
    _recording_state["bh_stream"] = bh_stream
    bh_stream.start()

    if mic_idx is not None:
        def mic_callback(indata, frames, time_info, status):
            if _recording_state["is_recording"] and not _recording_state["is_paused"]:
                frame = indata.copy()
                writer = _recording_state["mic_writer"]
                if writer is not None:
                    writer.write(frame)
                _recording_state["mic_live"].append(frame)

        mic_stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=mic_ch,
                                    device=mic_idx, callback=mic_callback)
        _recording_state["mic_stream"] = mic_stream
        mic_stream.start()


def _stop_streams():
    for key in ("bh_stream", "mic_stream"):
        stream = _recording_state.get(key)
        if stream:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
            _recording_state[key] = None


@app.route("/stop", methods=["POST"])
def stop():
    if not _recording_state["is_recording"]:
        return jsonify({"status": "not_recording"}), 409

    with _state_lock:
        _recording_state["is_recording"] = False
        _recording_state["is_paused"] = False
        _stop_streams()
        for key in ("bh_writer", "mic_writer"):
            writer = _recording_state.get(key)
            if writer is not None:
                try:
                    writer.close()
                except Exception:
                    pass
                _recording_state[key] = None
        live = _recording_state.get("live")
        live_text = live.full_text() if live else _recording_state.get("live_text", "")
        elapsed = time.time() - _recording_state["start_time"]
        app_label = _recording_state.get("meeting_app") or ""

    # Restore the user's previous output device.
    if not _switch_output(_recording_state.get("previous_output")):
        _switch_output(config.DEFAULT_OUTPUT_DEVICE)

    duration_minutes = round(elapsed / 60, 1)

    with _meetings_lock:
        meeting_id = (max((m["id"] for m in meetings), default=0)) + 1

    mixed_path = os.path.join(RECORDINGS_DIR, f"meeting_{meeting_id}.wav")
    bh_keep_path = os.path.join(RECORDINGS_DIR, f"meeting_{meeting_id}_bh.wav")

    # Build the proven 80/20 mix for playback + final transcript, and keep the
    # clean BlackHole stream for diarization.
    try:
        audio_io.mix_files(ACTIVE_BH_PATH, ACTIVE_MIC_PATH, mixed_path)
    except Exception as e:
        logger.exception("Mixing failed: %s", e)
    try:
        if os.path.exists(ACTIVE_BH_PATH):
            os.replace(ACTIVE_BH_PATH, bh_keep_path)
    except OSError:
        bh_keep_path = None
    try:
        os.path.exists(ACTIVE_MIC_PATH) and os.unlink(ACTIVE_MIC_PATH)
    except OSError:
        pass

    now = datetime.now()
    name = now.strftime("Meeting %B %-d %-I:%M%p").replace("AM", "am").replace("PM", "pm")
    if app_label:
        name = f"{app_label} {name}"

    meeting = {
        "id": meeting_id,
        "name": name,
        "date": now.isoformat(),
        "duration_minutes": duration_minutes,
        "transcript": live_text or "Transcribing in background...",
        "diarized": None,
        "speaker_names": {},
    }
    with _meetings_lock:
        meetings.append(meeting)
        _save_meetings()

    threading.Thread(
        target=_final_transcribe,
        args=(meeting_id, mixed_path, bh_keep_path, live_text),
        daemon=True,
    ).start()

    return jsonify(meeting)


def _final_transcribe(meeting_id, mixed_path, bh_path, live_text):
    want_words = diarization.available() and bool(bh_path)
    try:
        result = transcription.transcribe_file(mixed_path, word_timestamps=want_words)
        text = result["text"] or live_text or "[No speech detected]"
        _update_meeting(meeting_id, transcript=text)
        logger.info("Final transcription complete for meeting %s", meeting_id)
    except Exception as e:
        logger.exception("Final transcription failed: %s", e)
        if not live_text:
            _update_meeting(meeting_id, transcript=f"[Transcription failed: {e}]")
        return

    if want_words and result["words"]:
        try:
            turns = diarization.diarize_turns(bh_path)
            segments = diarization.assign_speakers(result["words"], turns)
            _update_meeting(meeting_id, diarized=segments)
            logger.info("Diarization complete for meeting %s (%d turns)", meeting_id, len(turns))
        except Exception as e:
            logger.exception("Diarization failed for meeting %s: %s", meeting_id, e)


@app.route("/pause", methods=["POST"])
def pause():
    if not _recording_state["is_recording"] or _recording_state["is_paused"]:
        return jsonify({"status": "not_recording"}), 409
    with _state_lock:
        _recording_state["is_paused"] = True
        _stop_streams()
    return jsonify({"status": "paused"})


@app.route("/resume", methods=["POST"])
def resume():
    if not _recording_state["is_recording"] or not _recording_state["is_paused"]:
        return jsonify({"status": "not_paused"}), 409

    bh_idx, bh_ch = _find_device(config.BLACKHOLE_DEVICE)
    mic_idx, mic_ch = _find_device(config.MIC_DEVICE)
    with _state_lock:
        _recording_state["is_paused"] = False
        # Writers stay open across pause; just restart the input streams.
        _start_streams(bh_idx, bh_ch, mic_idx if _recording_state["mic_writer"] else None, mic_ch)
    return jsonify({"status": "recording"})


@app.route("/live")
def live():
    if not _recording_state["is_recording"]:
        return redirect(url_for("index"))
    return render_template("live.html")


@app.route("/transcript")
def transcript():
    has_frames = bool(_recording_state["bh_live"]) or bool(_recording_state["mic_live"])
    if not _recording_state["is_recording"] or not has_frames or _recording_state["transcribing"]:
        return jsonify({"text": _recording_state.get("live_text", "")})

    _recording_state["transcribing"] = True

    def _do_transcribe():
        try:
            with _state_lock:
                bh_frames = _recording_state["bh_live"]
                mic_frames = _recording_state["mic_live"]
                _recording_state["bh_live"] = []
                _recording_state["mic_live"] = []
                live_obj = _recording_state["live"]
            mono = audio_io.mix_frames(bh_frames, mic_frames)
            if live_obj is not None and mono.size:
                live_obj.add_audio(mono, SAMPLE_RATE)
            if live_obj is not None:
                _recording_state["live_text"] = live_obj.process()
        except Exception as e:
            logger.warning("Live transcription error: %s", e)
        finally:
            _recording_state["transcribing"] = False

    threading.Thread(target=_do_transcribe, daemon=True).start()
    return jsonify({"text": _recording_state.get("live_text", "")})


@app.route("/transcript/snapshot")
def transcript_snapshot():
    """Authoritative copy of the full transcript-so-far for mid-meeting export.

    Read-only: never pauses or affects the recording. Returns the freshest
    text straight from the live transcriber (committed + in-progress).
    """
    live_obj = _recording_state.get("live")
    text = live_obj.full_text() if live_obj is not None else _recording_state.get("live_text", "")
    start = _recording_state.get("start_time")
    elapsed = round(time.time() - start) if (start and _recording_state["is_recording"]) else None
    return jsonify({
        "text": text,
        "elapsed_seconds": elapsed,
        "meeting_app": _recording_state.get("meeting_app"),
        "is_recording": _recording_state["is_recording"],
    })



@app.route("/meeting/<int:meeting_id>")
def meeting(meeting_id):
    meeting_obj = _get_meeting(meeting_id)
    if meeting_obj is None:
        return "Meeting not found", 404
    return render_template("meeting.html", meeting=meeting_obj)


@app.route("/meeting/<int:meeting_id>", methods=["PATCH"])
def rename_meeting(meeting_id):
    data = request.get_json(force=True)
    new_name = data.get("name", "").strip()
    if not new_name:
        return jsonify({"error": "name_required"}), 400
    if _update_meeting(meeting_id, name=new_name) is None:
        return jsonify({"error": "not_found"}), 404
    return jsonify({"status": "ok", "name": new_name})


@app.route("/meeting/<int:meeting_id>/speakers", methods=["PATCH"])
def rename_speaker(meeting_id):
    data = request.get_json(force=True)
    raw = (data.get("speaker") or "").strip()
    label = (data.get("label") or "").strip()
    if not raw or not label:
        return jsonify({"error": "speaker_and_label_required"}), 400
    with _meetings_lock:
        m = next((m for m in meetings if m["id"] == meeting_id), None)
        if m is None:
            return jsonify({"error": "not_found"}), 404
        names = m.get("speaker_names") or {}
        names[raw] = label
        m["speaker_names"] = names
        _save_meetings()
    return jsonify({"status": "ok", "speaker_names": names})


@app.route("/meeting/<int:meeting_id>", methods=["DELETE"])
def delete_meeting(meeting_id):
    global meetings
    with _meetings_lock:
        if not any(m["id"] == meeting_id for m in meetings):
            return jsonify({"error": "not_found"}), 404
        meetings = [m for m in meetings if m["id"] != meeting_id]
        _save_meetings()
    for suffix in ("", "_bh"):
        path = os.path.join(RECORDINGS_DIR, f"meeting_{meeting_id}{suffix}.wav")
        try:
            os.path.exists(path) and os.unlink(path)
        except OSError:
            pass
    return jsonify({"status": "deleted"})


@app.route("/api/devices")
def api_devices():
    """List output devices usable in the multi-output group."""
    devices = sd.query_devices()
    output_devices = []
    for i, d in enumerate(devices):
        if d["max_output_channels"] > 0:
            name = d["name"]
            if "multi-output" in name.lower() or "aggregate" in name.lower():
                continue
            output_devices.append({
                "index": i, "name": name, "uid": name,
                "channels": d["max_output_channels"], "enabled": True,
                "required": "blackhole" in name.lower(),
            })
    return jsonify({"devices": output_devices})


@app.route("/api/devices/toggle", methods=["POST"])
def api_devices_toggle():
    data = request.get_json(force=True)
    uid = data.get("uid")
    enabled = data.get("enabled", True)
    _device_prefs["active_output"] = uid if enabled else None
    return jsonify({"status": "ok", "active_output": _device_prefs["active_output"]})


@app.route("/api/meeting-status")
def api_meeting_status():
    return jsonify(_detect_meeting_app())


if __name__ == "__main__":
    os.makedirs(RECORDINGS_DIR, exist_ok=True)
    _cleanup_old_recordings()
    app.run(debug=True, port=5001)
