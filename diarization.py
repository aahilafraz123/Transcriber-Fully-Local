"""Speaker diarization via pyannote.

Design: the BlackHole stream contains only the *remote* participants (Teams
never echoes your own voice back to system output). So we diarize the clean
BlackHole recording to separate the remote speakers, and attribute any
transcribed words that fall outside every remote turn to the local user
("You"). This keeps the proven 80/20 mixed transcript untouched while adding
who-said-what, and works whether you're on headphones or speakers.
"""
import logging
import threading

import config

logger = logging.getLogger(__name__)

_pipeline = None
_lock = threading.Lock()


def available():
    """Diarization is opt-in: needs a Hugging Face token and the feature on."""
    return bool(config.DIARIZATION_ENABLED and config.HF_TOKEN)


def _get_pipeline():
    global _pipeline
    if _pipeline is None:
        with _lock:
            if _pipeline is None:
                from pyannote.audio import Pipeline

                logger.info("Loading diarization pipeline '%s'", config.DIARIZATION_MODEL)
                _pipeline = Pipeline.from_pretrained(
                    config.DIARIZATION_MODEL,
                    use_auth_token=config.HF_TOKEN,
                )
    return _pipeline


def diarize_turns(wav_path):
    """Return remote speaker turns as [{start, end, speaker}], time-sorted."""
    annotation = _get_pipeline()(wav_path)
    turns = [
        {"start": float(seg.start), "end": float(seg.end), "speaker": speaker}
        for seg, _, speaker in annotation.itertracks(yield_label=True)
    ]
    turns.sort(key=lambda t: t["start"])
    return turns


def _nearest_turn(turns, mid):
    best, best_dist = None, None
    for t in turns:
        dist = 0.0 if t["start"] <= mid <= t["end"] else min(abs(mid - t["start"]), abs(mid - t["end"]))
        if best is None or dist < best_dist:
            best, best_dist = t, dist
    return best


def assign_speakers(words, turns, you_label=None, nearest=False):
    """Label transcript words by overlap with diarized turns.

    Virtual mode (`nearest=False`): `turns` come from the clean BlackHole
    stream, so any word outside a turn is the local user ("You").
    In-person mode (`nearest=True`): everyone is on the room mic, so a word
    outside every turn is attributed to the closest turn in time instead.

    `words`: [{start, end, word}] from the transcript (word timestamps).
    Returns grouped segments: [{speaker, start, end, text}].
    """
    you_label = you_label or config.YOU_LABEL
    segments = []
    for w in words:
        mid = (w["start"] + w["end"]) / 2.0
        speaker = you_label
        matched = False
        for t in turns:
            if t["start"] <= mid <= t["end"]:
                speaker = t["speaker"]
                matched = True
                break
        if not matched and nearest and turns:
            speaker = _nearest_turn(turns, mid)["speaker"]
        if segments and segments[-1]["speaker"] == speaker:
            segments[-1]["end"] = w["end"]
            segments[-1]["text"] += w["word"]
        else:
            segments.append({
                "speaker": speaker,
                "start": w["start"],
                "end": w["end"],
                "text": w["word"],
            })
    for s in segments:
        s["text"] = s["text"].strip()
    return [s for s in segments if s["text"]]
