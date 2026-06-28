"""faster-whisper transcription: model loading, file/array helpers, and a
streaming live transcriber that finalizes utterances on VAD silence
boundaries (no more disk-write-per-tick, no run-on/duplicated fragments).
"""
import logging
import threading
from math import gcd

import numpy as np
from scipy.signal import resample_poly

import config

logger = logging.getLogger(__name__)

_models = {}
_models_lock = threading.Lock()


def _load(model_size):
    from faster_whisper import WhisperModel

    logger.info(
        "Loading faster-whisper model '%s' on %s (%s)",
        model_size, config.TRANSCRIBE_DEVICE, config.TRANSCRIBE_COMPUTE_TYPE,
    )
    return WhisperModel(
        model_size,
        device=config.TRANSCRIBE_DEVICE,
        compute_type=config.TRANSCRIBE_COMPUTE_TYPE,
    )


def get_model(model_size=None):
    """Lazily load and cache a model. One turbo model serves live + final."""
    size = model_size or config.TRANSCRIBE_MODEL
    model = _models.get(size)
    if model is None:
        with _models_lock:
            model = _models.get(size)
            if model is None:
                model = _load(size)
                _models[size] = model
    return model


def get_live_model():
    return get_model(config.LIVE_MODEL or config.TRANSCRIBE_MODEL)


def to_whisper_audio(mono, sample_rate):
    """Resample mono float32 to 16 kHz for faster-whisper array input."""
    mono = np.asarray(mono, dtype=np.float32)
    if mono.size == 0 or sample_rate == config.WHISPER_SAMPLE_RATE:
        return mono
    g = gcd(int(sample_rate), config.WHISPER_SAMPLE_RATE)
    up = config.WHISPER_SAMPLE_RATE // g
    down = int(sample_rate) // g
    return resample_poly(mono, up, down).astype(np.float32)


def transcribe_file(path, word_timestamps=False, initial_prompt=None):
    """Final transcription from a WAV path (faster-whisper decodes/resamples)."""
    model = get_model()
    segments, info = model.transcribe(
        path,
        beam_size=5,
        word_timestamps=word_timestamps,
        vad_filter=True,
        condition_on_previous_text=True,
        initial_prompt=initial_prompt or None,
    )
    seg_list = list(segments)
    text = " ".join(s.text.strip() for s in seg_list).strip()
    words = []
    if word_timestamps:
        for s in seg_list:
            for w in (s.words or []):
                words.append({"start": float(w.start), "end": float(w.end), "word": w.word})
    return {"text": text, "words": words, "language": getattr(info, "language", None)}


def transcribe_array(mono, sample_rate, model_size=None, initial_prompt=None):
    """Quick transcription of an in-memory mono buffer (live path)."""
    model = get_model(model_size) if model_size else get_live_model()
    audio = to_whisper_audio(mono, sample_rate)
    if audio.size == 0:
        return ""
    segments, _ = model.transcribe(
        audio,
        beam_size=1,
        word_timestamps=False,
        vad_filter=False,
        condition_on_previous_text=False,
        initial_prompt=initial_prompt or None,
    )
    return " ".join(s.text.strip() for s in segments).strip()


def _speech_timestamps(audio_16k, min_silence_ms):
    """Silero VAD speech regions (in 16 kHz samples). Empty list if unavailable."""
    from faster_whisper.vad import get_speech_timestamps, VadOptions

    opts = VadOptions(min_silence_duration_ms=min_silence_ms)
    return get_speech_timestamps(audio_16k, opts)


class LiveTranscriber:
    """Maintains an in-memory 16 kHz buffer of un-finalized audio.

    Each `process()` finalizes the speech that has been followed by enough
    silence (transcribed exactly once and committed), and re-transcribes only
    the in-progress tail as a tentative preview. Committed audio is dropped
    from the buffer, so memory stays bounded.
    """

    def __init__(self,
                 min_silence_ms=None,
                 max_buffer_sec=None,
                 min_speech_sec=None):
        self.sr = config.WHISPER_SAMPLE_RATE
        self.min_silence_ms = min_silence_ms or config.LIVE_MIN_SILENCE_MS
        self.max_buffer_samples = int((max_buffer_sec or config.LIVE_MAX_BUFFER_SEC) * self.sr)
        self.min_speech_samples = int((min_speech_sec or config.LIVE_MIN_SPEECH_SEC) * self.sr)
        self.buf = np.zeros(0, dtype=np.float32)
        self.committed = []
        self.tentative = ""
        self.lock = threading.Lock()

    def add_audio(self, mono, sample_rate):
        chunk = to_whisper_audio(mono, sample_rate)
        if chunk.size == 0:
            return
        with self.lock:
            self.buf = np.concatenate([self.buf, chunk]) if self.buf.size else chunk

    def _prompt(self):
        return " ".join(self.committed[-3:]) if self.committed else None

    def full_text(self):
        parts = list(self.committed)
        if self.tentative:
            parts.append(self.tentative)
        return " ".join(parts).strip()

    def process(self):
        with self.lock:
            audio = self.buf.copy()
        if audio.size < self.min_speech_samples:
            return self.full_text()

        try:
            segs = _speech_timestamps(audio, self.min_silence_ms)
        except Exception as e:  # VAD unavailable / version mismatch
            logger.debug("VAD unavailable, treating buffer as tentative: %s", e)
            segs = None

        cut = None
        silence_samples = int(self.sr * self.min_silence_ms / 1000)
        if segs:
            trailing_silence = audio.size - segs[-1]["end"]
            if trailing_silence >= silence_samples:
                cut = segs[-1]["end"]

        # Force a cut if the buffer grows too large with no natural pause.
        if cut is None and audio.size > self.max_buffer_samples:
            cut = segs[-1]["start"] if (segs and len(segs) > 1) else audio.size

        if cut and cut > 0:
            stable = audio[:cut]
            text = transcribe_array(stable, self.sr, initial_prompt=self._prompt())
            if text:
                self.committed.append(text)
            with self.lock:
                self.buf = self.buf[cut:] if cut <= self.buf.size else np.zeros(0, dtype=np.float32)
            self.tentative = ""
        else:
            self.tentative = transcribe_array(audio, self.sr, initial_prompt=self._prompt())

        return self.full_text()
