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
    "name": "",  # optional, set before or during the recording
    "mode": "virtual",  # "virtual" (call on this Mac) or "in_person" (room mic)
    "resume_id": None,  # set when continuing an existing (stopped) meeting
}

# Meetings whose final transcription is still running (can't be resumed yet).
_finalizing = set()

MODES = ("virtual", "in_person")
MODE_LABELS = {"virtual": "Virtual", "in_person": "In-person"}


def _recording_status():
    """Public snapshot of the in-progress recording for the UI."""
    start = _recording_state.get("start_time")
    is_rec = bool(_recording_state["is_recording"])
    return {
        "is_recording": is_rec,
        "is_paused": bool(_recording_state["is_paused"]),
        "name": _recording_state.get("name") or "",
        "mode": _recording_state.get("mode") or "virtual",
        "mode_label": MODE_LABELS.get(_recording_state.get("mode") or "virtual", "Virtual"),
        "elapsed_seconds": round(time.time() - start) if (is_rec and start) else 0,
        "meeting_app": _recording_state.get("meeting_app"),
        "resume_id": _recording_state.get("resume_id") if is_rec else None,
    }


def _clean_name(value, limit=120):
    return " ".join(str(value or "").split())[:limit]

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


def _refresh_devices():
    """Re-enumerate audio devices.

    PortAudio snapshots the device list once at initialization, so a device
    that appears after the app starts (e.g. BlackHole loaded by a reboot or a
    coreaudiod restart, or headphones plugged in) is invisible until PortAudio
    is re-initialized. Only safe while no input streams are open.
    """
    if _recording_state.get("bh_stream") or _recording_state.get("mic_stream"):
        return
    try:
        sd._terminate()
        sd._initialize()
    except Exception as e:  # pragma: no cover - best effort
        logger.warning("Could not refresh audio device list: %s", e)


def _input_device_names():
    return [d["name"] for d in sd.query_devices() if d["max_input_channels"] > 0]


def _find_device(name_substr):
    devices = sd.query_devices()
    for i, d in enumerate(devices):
        if name_substr in d["name"].lower() and d["max_input_channels"] > 0:
            return i, d["max_input_channels"]
    return None, 0


_VIRTUAL_OUTPUT_MARKERS = ("multi-output", "aggregate", "blackhole")


def _guess_real_output():
    """Best real output to restore when we can't tell what the user had.

    Happens when a recording starts while the output is already the
    Multi-Output Device (e.g. a previous run died mid-recording). Prefer
    DEFAULT_OUTPUT_DEVICE, then built-in speakers, then any non-virtual output.
    """
    if config.DEFAULT_OUTPUT_DEVICE:
        return config.DEFAULT_OUTPUT_DEVICE
    try:
        result = subprocess.run(["SwitchAudioSource", "-a", "-t", "output"],
                                capture_output=True, text=True, timeout=5)
        outputs = [o.strip() for o in result.stdout.splitlines() if o.strip()]
    except Exception:
        return None
    real = [o for o in outputs if not any(m in o.lower() for m in _VIRTUAL_OUTPUT_MARKERS)]
    for o in real:
        if "speakers" in o.lower():
            return o
    return real[0] if real else None


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
                           is_recording=_recording_state["is_recording"],
                           recording=_recording_status(), mode_labels=MODE_LABELS)


@app.route("/start", methods=["POST"])
def start():
    if _recording_state["is_recording"]:
        return jsonify({"status": "already_recording"}), 409

    body = request.get_json(silent=True) or {}
    mode = str(body.get("mode") or "virtual").strip().lower().replace("-", "_")
    if mode not in MODES:
        return jsonify({"error": "invalid_mode", "hint": "mode must be 'virtual' or 'in_person'"}), 400
    resume_id = body.get("resume_id")
    resume_meeting = None
    if resume_id not in (None, "", 0):
        try:
            resume_id = int(resume_id)
        except (TypeError, ValueError):
            return jsonify({"error": "invalid_resume_id"}), 400
        resume_meeting = _get_meeting(resume_id)
        if resume_meeting is None:
            return jsonify({"error": "not_found", "hint": "That meeting no longer exists."}), 404
        if resume_id in _finalizing:
            return jsonify({
                "error": "still_transcribing",
                "hint": "The previous part of this meeting is still being transcribed. Try again in a moment.",
            }), 409
    else:
        resume_id = None

    _recording_state["mode"] = mode
    _recording_state["resume_id"] = resume_id
    _recording_state["name"] = _clean_name(body.get("name")) or (resume_meeting["name"] if resume_meeting else "")
    _recording_state["meeting_app"] = None
    _recording_state["previous_output"] = None

    _refresh_devices()
    mic_idx, mic_ch = _find_device(config.MIC_DEVICE)

    if mode == "in_person":
        # Room / phone meeting: 100% microphone. BlackHole and the output
        # routing are not touched at all.
        bh_idx, bh_ch = None, 0
        if mic_idx is None:
            logger.error(
                "No input device matching '%s'. Inputs visible: %s",
                config.MIC_DEVICE, _input_device_names(),
            )
            return jsonify({
                "error": "Microphone not found",
                "hint": (
                    "No input device matches MIC_DEVICE. Set MIC_DEVICE to a "
                    "substring of your microphone's name and restart the app."
                ),
                "inputs_visible": _input_device_names(),
            }), 500
    else:
        meeting_info = _detect_meeting_app()
        _recording_state["meeting_app"] = meeting_info.get("app")

        # Remember the real current output so we can restore it on stop.
        try:
            result = subprocess.run(["SwitchAudioSource", "-c"], capture_output=True, text=True)
            current = result.stdout.strip()
            if current and current != "Multi-Output Device":
                _recording_state["previous_output"] = current
            else:
                _recording_state["previous_output"] = _guess_real_output()
        except Exception:
            _recording_state["previous_output"] = _guess_real_output()

        bh_idx, bh_ch = _find_device(config.BLACKHOLE_DEVICE)

    if mode == "virtual" and bh_idx is None:
        logger.error(
            "No input device matching '%s'. Inputs visible: %s",
            config.BLACKHOLE_DEVICE, _input_device_names(),
        )
        return jsonify({
            "error": "BlackHole device not found",
            "hint": (
                "Install BlackHole 2ch (brew install --cask blackhole-2ch), then "
                "reboot or run 'sudo killall coreaudiod' so macOS loads the "
                "driver. Check it appears in Audio MIDI Setup."
            ),
            "inputs_visible": _input_device_names(),
        }), 500
    if mic_idx is None:
        logger.warning(
            "No input device matching '%s'; recording system audio only. "
            "Inputs visible: %s", config.MIC_DEVICE, _input_device_names(),
        )

    # Only touch the output routing once we know we can actually record.
    if mode == "virtual":
        _switch_output("Multi-Output Device")

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
            "bh_writer": audio_io.open_writer(ACTIVE_BH_PATH, bh_ch) if bh_idx is not None else None,
            "mic_writer": audio_io.open_writer(ACTIVE_MIC_PATH, mic_ch) if mic_idx is not None else None,
        })

    _start_streams(bh_idx, bh_ch, mic_idx, mic_ch)
    logger.info("Recording started (%s mode, name=%r, resume_id=%s)", mode,
                _recording_state["name"], resume_id)
    return jsonify({"status": "recording", "meeting_app": _recording_state["meeting_app"],
                    "name": _recording_state["name"], "mode": mode, "resume_id": resume_id})


def _start_streams(bh_idx, bh_ch, mic_idx, mic_ch):
    def bh_callback(indata, frames, time_info, status):
        if _recording_state["is_recording"] and not _recording_state["is_paused"]:
            frame = indata.copy()
            writer = _recording_state["bh_writer"]
            if writer is not None:
                writer.write(frame)
            _recording_state["bh_live"].append(frame)

    if bh_idx is not None:
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
        chosen_name = _recording_state.get("name") or ""
        _recording_state["name"] = ""
        mode = _recording_state.get("mode") or "virtual"
        resume_id = _recording_state.get("resume_id")
        _recording_state["resume_id"] = None

    # Restore the user's previous output device; never leave them on the
    # Multi-Output Device. In-person mode never changed it.
    if mode == "virtual":
        if not _switch_output(_recording_state.get("previous_output")):
            _switch_output(_guess_real_output())

    duration_minutes = round(elapsed / 60, 1)

    if resume_id is not None and _get_meeting(resume_id) is not None:
        return _stop_resumed(resume_id, mode, live_text, duration_minutes, chosen_name)

    with _meetings_lock:
        meeting_id = (max((m["id"] for m in meetings), default=0)) + 1

    mixed_path = os.path.join(RECORDINGS_DIR, f"meeting_{meeting_id}.wav")
    bh_keep_path = os.path.join(RECORDINGS_DIR, f"meeting_{meeting_id}_bh.wav")

    # Build the proven 80/20 mix for playback + final transcript, and keep the
    # clean BlackHole stream for diarization.
    try:
        if mode == "in_person":
            # 100% microphone: normalize the mic file on its own.
            audio_io.mix_files(None, ACTIVE_MIC_PATH, mixed_path, bh_w=0.0, mic_w=1.0)
        else:
            audio_io.mix_files(ACTIVE_BH_PATH, ACTIVE_MIC_PATH, mixed_path)
    except Exception as e:
        logger.exception("Mixing failed: %s", e)
    try:
        if mode == "virtual" and os.path.exists(ACTIVE_BH_PATH):
            os.replace(ACTIVE_BH_PATH, bh_keep_path)
        else:
            bh_keep_path = None
            os.path.exists(ACTIVE_BH_PATH) and os.unlink(ACTIVE_BH_PATH)
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
    if chosen_name:
        name = chosen_name

    meeting = {
        "id": meeting_id,
        "name": name,
        "date": now.isoformat(),
        "duration_minutes": duration_minutes,
        "transcript": live_text or "Transcribing in background...",
        "diarized": None,
        "speaker_names": {},
        "mode": mode,
    }
    with _meetings_lock:
        meetings.append(meeting)
        _save_meetings()

    _finalizing.add(meeting_id)
    threading.Thread(
        target=_final_transcribe,
        args=(meeting_id, mixed_path, bh_keep_path, live_text, mode),
        daemon=True,
    ).start()

    return jsonify(meeting)


RESUME_SEPARATOR = "\n\n— Resumed {when} —\n\n"


def _stop_resumed(meeting_id, mode, live_text, duration_minutes, chosen_name):
    """Finish a resumed recording: append audio + transcript to the meeting."""
    mixed_path = os.path.join(RECORDINGS_DIR, f"meeting_{meeting_id}.wav")
    bh_full_path = os.path.join(RECORDINGS_DIR, f"meeting_{meeting_id}_bh.wav")
    part_mixed = os.path.join(RECORDINGS_DIR, f"meeting_{meeting_id}_part.wav")
    part_bh = os.path.join(RECORDINGS_DIR, f"meeting_{meeting_id}_part_bh.wav")

    # Where the new part starts inside the full recording (for diarization
    # timestamps), measured before we append.
    prior_seconds = audio_io.wav_seconds(mixed_path)

    try:
        if mode == "in_person":
            audio_io.mix_files(None, ACTIVE_MIC_PATH, part_mixed, bh_w=0.0, mic_w=1.0)
        else:
            audio_io.mix_files(ACTIVE_BH_PATH, ACTIVE_MIC_PATH, part_mixed)
    except Exception as e:
        logger.exception("Mixing failed: %s", e)
    try:
        if mode == "virtual" and os.path.exists(ACTIVE_BH_PATH):
            os.replace(ACTIVE_BH_PATH, part_bh)
        else:
            os.path.exists(ACTIVE_BH_PATH) and os.unlink(ACTIVE_BH_PATH)
    except OSError:
        pass
    try:
        os.path.exists(ACTIVE_MIC_PATH) and os.unlink(ACTIVE_MIC_PATH)
    except OSError:
        pass

    # The final pass transcribes a private copy of the part, then the part is
    # appended onto the full recording so meeting_<id>.wav stays complete.
    part_for_transcribe = part_mixed + ".final.wav"
    try:
        import shutil
        shutil.copyfile(part_mixed, part_for_transcribe)
    except Exception:
        part_for_transcribe = None
    try:
        audio_io.append_wav(mixed_path, part_mixed)
    except Exception as e:
        logger.exception("Could not append resumed audio: %s", e)
    part_bh_for_diar = None
    if os.path.exists(part_bh):
        part_bh_for_diar = part_bh + ".final.wav"
        try:
            import shutil
            shutil.copyfile(part_bh, part_bh_for_diar)
            audio_io.append_wav(bh_full_path, part_bh)
        except Exception as e:
            logger.exception("Could not append resumed BlackHole audio: %s", e)

    now = datetime.now()
    when = now.strftime("%-I:%M%p").replace("AM", "am").replace("PM", "pm")
    sep = RESUME_SEPARATOR.format(when=when)

    with _meetings_lock:
        m = _get_meeting(meeting_id)
        prior_transcript = (m.get("transcript") or "").strip()
        parts = list(m.get("parts") or [])
        if not parts:
            parts.append({"started": m["date"], "duration_minutes": m.get("duration_minutes", 0),
                          "mode": m.get("mode", "virtual")})
        parts.append({"started": now.isoformat(), "duration_minutes": duration_minutes, "mode": mode})
        m["parts"] = parts
        m["duration_minutes"] = round(sum(p["duration_minutes"] for p in parts), 1)
        m["transcript"] = prior_transcript + sep + (live_text or "Transcribing in background...")
        if chosen_name:
            m["name"] = chosen_name
        prior_diarized = m.get("diarized")
        _save_meetings()
        result = dict(m)

    _finalizing.add(meeting_id)
    threading.Thread(
        target=_final_transcribe_part,
        args=(meeting_id, part_for_transcribe, part_bh_for_diar, live_text, mode,
              prior_transcript, sep, prior_seconds, prior_diarized),
        daemon=True,
    ).start()
    logger.info("Resumed meeting %s: +%s min (part %d)", meeting_id, duration_minutes, len(parts))
    return jsonify(result)


def _final_transcribe_part(meeting_id, part_path, part_bh_path, live_text, mode,
                           prior_transcript, sep, prior_seconds, prior_diarized):
    """Final pass for a resumed part: transcribe just the new audio and append."""
    try:
        if not part_path or not os.path.exists(part_path):
            raise FileNotFoundError("resumed part audio missing")
        diar_source = part_bh_path if mode == "virtual" else part_path
        want_words = diarization.available() and bool(diar_source) and os.path.exists(diar_source)
        try:
            result = transcription.transcribe_file(part_path, word_timestamps=want_words)
            text = result["text"] or live_text or "[No speech detected]"
        except Exception as e:
            logger.exception("Final transcription (resumed part) failed: %s", e)
            if live_text:
                return
            result, text = {"words": []}, f"[Transcription failed: {e}]"
        _update_meeting(meeting_id, transcript=prior_transcript + sep + text)
        logger.info("Final transcription complete for resumed part of meeting %s", meeting_id)

        if prior_diarized:
            # Keep speaker numbering distinct from earlier parts: shift the new
            # part's SPEAKER_n labels past the ones already used.
            used = {seg["speaker"] for seg in prior_diarized if str(seg["speaker"]).startswith("SPEAKER_")}
            offset = len(used)
            new_segments = None
            if want_words and result.get("words"):
                try:
                    turns = diarization.diarize_turns(diar_source)
                    new_segments = diarization.assign_speakers(
                        result["words"], turns, nearest=(mode == "in_person"))
                except Exception as e:
                    logger.exception("Diarization failed for resumed part of meeting %s: %s", meeting_id, e)
            if not new_segments:
                new_segments = [{"speaker": "Resumed", "start": 0.0, "end": 0.0, "text": text}]
            for seg in new_segments:
                spk = str(seg["speaker"])
                if spk.startswith("SPEAKER_"):
                    try:
                        seg["speaker"] = f"SPEAKER_{int(spk.split('_')[-1]) + offset:02d}"
                    except ValueError:
                        pass
                seg["start"] = round(float(seg.get("start", 0.0)) + prior_seconds, 2)
                seg["end"] = round(float(seg.get("end", 0.0)) + prior_seconds, 2)
            _update_meeting(meeting_id, diarized=list(prior_diarized) + new_segments)
    finally:
        _finalizing.discard(meeting_id)
        for p in (part_path, part_bh_path):
            try:
                p and os.path.exists(p) and os.unlink(p)
            except OSError:
                pass


def _final_transcribe(meeting_id, mixed_path, bh_path, live_text, mode="virtual"):
    try:
        _final_transcribe_inner(meeting_id, mixed_path, bh_path, live_text, mode)
    finally:
        _finalizing.discard(meeting_id)


def _final_transcribe_inner(meeting_id, mixed_path, bh_path, live_text, mode="virtual"):
    # Virtual: diarize the clean BlackHole stream (remote voices), the rest is
    # "You". In-person: everyone is on the room mic, so diarize the mic
    # recording itself and label every voice as a speaker.
    diar_source = bh_path if mode == "virtual" else mixed_path
    want_words = diarization.available() and bool(diar_source)
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
            turns = diarization.diarize_turns(diar_source)
            segments = diarization.assign_speakers(
                result["words"], turns, nearest=(mode == "in_person"),
            )
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

    _refresh_devices()
    mic_idx, mic_ch = _find_device(config.MIC_DEVICE)
    if _recording_state.get("mode") == "in_person":
        bh_idx, bh_ch = None, 0
        if mic_idx is None:
            return jsonify({"error": "Microphone not found"}), 500
    else:
        bh_idx, bh_ch = _find_device(config.BLACKHOLE_DEVICE)
        if bh_idx is None:
            return jsonify({"error": "BlackHole device not found"}), 500
    with _state_lock:
        _recording_state["is_paused"] = False
        # Writers stay open across pause; just restart the input streams.
        _start_streams(bh_idx, bh_ch, mic_idx if _recording_state["mic_writer"] else None, mic_ch)
    return jsonify({"status": "recording"})


@app.route("/api/recording", methods=["GET"])
def api_recording():
    """Status of the in-progress recording (used by every page's nav)."""
    return jsonify(_recording_status())


@app.route("/api/recording", methods=["PATCH"])
def api_recording_name():
    """Set or change the name of the in-progress recording. Optional; never
    blocks recording. An empty name means "use the default on stop"."""
    if not _recording_state["is_recording"]:
        return jsonify({"error": "not_recording"}), 409
    data = request.get_json(silent=True) or {}
    _recording_state["name"] = _clean_name(data.get("name"))
    return jsonify({"status": "ok", "name": _recording_state["name"]})


@app.route("/live")
def live():
    if not _recording_state["is_recording"]:
        return redirect(url_for("index"))
    resume = None
    rid = _recording_state.get("resume_id")
    if rid is not None:
        m = _get_meeting(rid)
        if m:
            resume = {"id": rid, "name": m["name"], "transcript": m.get("transcript") or "",
                      "parts": len(m.get("parts") or []) or 1}
    return render_template("live.html", recording=_recording_status(), resume=resume)


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
    return render_template("meeting.html", meeting=meeting_obj,
                           recording=_recording_status(),
                           mode_label=MODE_LABELS.get(meeting_obj.get("mode") or "virtual", "Virtual"))


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
    for suffix in ("", "_bh", "_part", "_part_bh", "_part.wav.final", "_part_bh.wav.final"):
        path = os.path.join(RECORDINGS_DIR, f"meeting_{meeting_id}{suffix}.wav")
        try:
            os.path.exists(path) and os.unlink(path)
        except OSError:
            pass
    return jsonify({"status": "deleted"})


@app.route("/api/devices")
def api_devices():
    """List output devices usable in the multi-output group."""
    _refresh_devices()
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


@app.route("/api/mic-test", methods=["POST"])
def api_mic_test():
    """Record a few seconds from the microphone, report its level, and
    transcribe it, so the user can confirm their voice reaches the app."""
    if _recording_state["is_recording"]:
        return jsonify({"error": "recording_in_progress"}), 409
    data = request.get_json(silent=True) or {}
    try:
        seconds = float(data.get("seconds", 3))
    except (TypeError, ValueError):
        seconds = 3.0
    seconds = max(1.0, min(seconds, 8.0))

    _refresh_devices()
    mic_idx, mic_ch = _find_device(config.MIC_DEVICE)
    if mic_idx is None:
        return jsonify({
            "error": "Microphone not found",
            "hint": "No input device matches MIC_DEVICE.",
            "inputs_visible": _input_device_names(),
        }), 500
    device_name = sd.query_devices()[mic_idx]["name"]

    try:
        frames = sd.rec(int(seconds * SAMPLE_RATE), samplerate=SAMPLE_RATE,
                        channels=mic_ch, device=mic_idx, dtype="float32")
        sd.wait()
    except Exception as e:
        logger.exception("Mic test capture failed: %s", e)
        return jsonify({"error": f"Could not open microphone: {e}"}), 500

    mono = audio_io.mix_frames([], [frames], bh_w=0.0, mic_w=1.0)
    raw = frames.reshape(-1) if frames.ndim == 1 else frames.mean(axis=1)
    peak = float(abs(raw).max()) if raw.size else 0.0
    rms = float((raw.astype("float64") ** 2).mean() ** 0.5) if raw.size else 0.0
    silent = peak < 1e-4

    text = ""
    if not silent:
        try:
            text = (transcription.transcribe_array(mono, SAMPLE_RATE) or "").strip()
        except Exception as e:
            logger.warning("Mic test transcription failed: %s", e)

    hint = ""
    if silent:
        hint = ("The microphone delivered pure silence. On macOS that almost always "
                "means microphone permission is off for the app that launched this "
                "server (e.g. Terminal). Open System Settings > Privacy & Security > "
                "Microphone and enable it, then restart the app.")
    elif not text:
        hint = "Audio was captured but no speech was recognised. Speak clearly during the countdown and try again."

    return jsonify({
        "device": device_name,
        "seconds": seconds,
        "peak": round(peak, 4),
        "rms": round(rms, 5),
        "level_percent": int(min(100, round(peak * 100))),
        "silent": silent,
        "text": text,
        "hint": hint,
    })


@app.route("/api/meeting-status")
def api_meeting_status():
    return jsonify(_detect_meeting_app())


if __name__ == "__main__":
    os.makedirs(RECORDINGS_DIR, exist_ok=True)
    _cleanup_old_recordings()
    # The menu bar launcher sets TRANSCRIBER_RELOAD=0 so there is a single
    # process to manage; on the command line the auto-reloader stays on.
    use_reloader = os.environ.get("TRANSCRIBER_RELOAD", "1").strip().lower() not in ("0", "false", "no", "off")
    app.run(debug=True, port=5001, use_reloader=use_reloader)
