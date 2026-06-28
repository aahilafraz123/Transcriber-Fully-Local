"""Audio I/O helpers: constant-memory streaming capture to disk, two-pass
file mixing for the final recording, and in-memory frame mixing for the live
preview.
"""
import logging
import os

import numpy as np
import soundfile as sf

import config

logger = logging.getLogger(__name__)


def open_writer(path, channels):
    """Open a streaming float32 WAV writer (frames written straight to disk)."""
    return sf.SoundFile(
        path, mode="w", samplerate=config.SAMPLE_RATE,
        channels=max(1, int(channels)), subtype="FLOAT",
    )


def _to_mono(block):
    if block is None or len(block) == 0:
        return np.zeros(0, dtype=np.float32)
    return (block.mean(axis=1) if block.ndim > 1 else block.ravel()).astype(np.float32)


def _peak(path, block_size):
    peak = 0.0
    with sf.SoundFile(path) as f:
        while True:
            data = f.read(block_size, dtype="float32")
            if len(data) == 0:
                break
            mono = _to_mono(data)
            if mono.size:
                peak = max(peak, float(np.max(np.abs(mono))))
    return peak


def mix_files(bh_path, mic_path, out_path, bh_w=None, mic_w=None):
    """Mix BlackHole + mic files into a mono 80/20 WAV with bounded memory.

    Two passes (peak then mix) so each channel is normalized to 0.9 peak over
    the whole signal — matching the previous in-memory behavior — without ever
    loading the full recording into RAM.
    """
    bh_w = config.MIX_BH_WEIGHT if bh_w is None else bh_w
    mic_w = config.MIX_MIC_WEIGHT if mic_w is None else mic_w
    block = config.SAMPLE_RATE  # 1 second per block

    has_bh = bool(bh_path) and os.path.exists(bh_path) and os.path.getsize(bh_path) > 0
    has_mic = bool(mic_path) and os.path.exists(mic_path) and os.path.getsize(mic_path) > 0

    bh_peak = _peak(bh_path, block) if has_bh else 0.0
    mic_peak = _peak(mic_path, block) if has_mic else 0.0
    bh_scale = (0.9 / bh_peak) if bh_peak > 0 else 0.0
    mic_scale = (0.9 / mic_peak) if mic_peak > 0 else 0.0

    bh_f = sf.SoundFile(bh_path) if has_bh else None
    mic_f = sf.SoundFile(mic_path) if has_mic else None
    out = sf.SoundFile(out_path, mode="w", samplerate=config.SAMPLE_RATE,
                       channels=1, subtype="FLOAT")
    try:
        while True:
            bh_block = bh_f.read(block, dtype="float32") if bh_f else None
            mic_block = mic_f.read(block, dtype="float32") if mic_f else None
            bh_mono = _to_mono(bh_block) * bh_scale
            mic_mono = _to_mono(mic_block) * mic_scale
            n = max(bh_mono.size, mic_mono.size)
            if n == 0:
                break
            mixed = np.zeros(n, dtype=np.float32)
            if bh_mono.size:
                mixed[: bh_mono.size] += bh_mono * bh_w
            if mic_mono.size:
                mixed[: mic_mono.size] += mic_mono * mic_w
            out.write(mixed)
            bh_done = (bh_f is None) or (bh_block is not None and len(bh_block) < block)
            mic_done = (mic_f is None) or (mic_block is not None and len(mic_block) < block)
            if bh_done and mic_done:
                break
    finally:
        out.close()
        if bh_f:
            bh_f.close()
        if mic_f:
            mic_f.close()


def mix_frames(bh_frames, mic_frames, bh_w=None, mic_w=None):
    """Mix in-memory frame lists into mono float32 (live preview path)."""
    bh_w = config.MIX_BH_WEIGHT if bh_w is None else bh_w
    mic_w = config.MIX_MIC_WEIGHT if mic_w is None else mic_w

    def prep(frames):
        if not frames:
            return None
        mono = _to_mono(np.concatenate(frames, axis=0))
        peak = float(np.max(np.abs(mono))) if mono.size else 0.0
        if peak > 0:
            mono = mono / peak * 0.9
        return mono

    bh_mono = prep(bh_frames)
    mic_mono = prep(mic_frames)

    if bh_mono is not None and mic_mono is not None:
        n = min(bh_mono.size, mic_mono.size)
        mixed = bh_mono[:n] * bh_w + mic_mono[:n] * mic_w
    elif bh_mono is not None:
        mixed = bh_mono
    elif mic_mono is not None:
        mixed = mic_mono
    else:
        mixed = np.zeros(0, dtype=np.float32)
    return mixed.astype(np.float32)
