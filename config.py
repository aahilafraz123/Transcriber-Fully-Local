"""Central configuration for the local transcriber.

Every value can be overridden with an environment variable so the app no
longer hardcodes machine-specific device names or model choices.
"""
import os


def _get_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _get_int(name, default):
    try:
        return int(float(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


def _get_bool(name, default):
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() not in ("0", "false", "no", "off", "")


# Audio ------------------------------------------------------------------
SAMPLE_RATE = _get_int("SAMPLE_RATE", 48000)
WHISPER_SAMPLE_RATE = 16000  # faster-whisper works at 16 kHz

# The proven blend: system audio dominant, mic as a quiet supporting layer.
MIX_BH_WEIGHT = _get_float("MIX_BH_WEIGHT", 0.8)
MIX_MIC_WEIGHT = _get_float("MIX_MIC_WEIGHT", 0.2)

# Capture device name substrings (case-insensitive). Override per machine.
BLACKHOLE_DEVICE = os.environ.get("BLACKHOLE_DEVICE", "blackhole").lower()
MIC_DEVICE = os.environ.get("MIC_DEVICE", "macbook pro microphone").lower()

# Output device to restore after recording if the original can't be read.
# Empty string => best effort (leave whatever macOS selects).
DEFAULT_OUTPUT_DEVICE = os.environ.get("DEFAULT_OUTPUT_DEVICE", "")

# Transcription (faster-whisper) -----------------------------------------
# large-v3-turbo: ~809M params, ~99% of large-v3 accuracy, fast int8 on CPU.
TRANSCRIBE_MODEL = os.environ.get("TRANSCRIBE_MODEL", "large-v3-turbo")
TRANSCRIBE_DEVICE = os.environ.get("TRANSCRIBE_DEVICE", "cpu")
TRANSCRIBE_COMPUTE_TYPE = os.environ.get("TRANSCRIBE_COMPUTE_TYPE", "int8")
# Optional lighter model for the live tail. Empty => reuse the main model.
LIVE_MODEL = os.environ.get("LIVE_MODEL", "")

# Live streaming / VAD ----------------------------------------------------
LIVE_MIN_SILENCE_MS = _get_int("LIVE_MIN_SILENCE_MS", 600)
LIVE_MAX_BUFFER_SEC = _get_float("LIVE_MAX_BUFFER_SEC", 30.0)
LIVE_MIN_SPEECH_SEC = _get_float("LIVE_MIN_SPEECH_SEC", 0.4)

# Diarization (pyannote) --------------------------------------------------
HF_TOKEN = os.environ.get("HF_TOKEN", "") or os.environ.get("HUGGINGFACE_TOKEN", "")
DIARIZATION_ENABLED = _get_bool("DIARIZATION_ENABLED", True)
DIARIZATION_MODEL = os.environ.get("DIARIZATION_MODEL", "pyannote/speaker-diarization-3.1")
YOU_LABEL = os.environ.get("YOU_LABEL", "You")

# Housekeeping ------------------------------------------------------------
WAV_MAX_AGE_DAYS = _get_int("WAV_MAX_AGE_DAYS", 5)
