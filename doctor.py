#!/usr/bin/env python3
"""Setup health check for the local transcriber.

Run with the project's virtualenv:  .venv/bin/python doctor.py
Each line is PASS / WARN / FAIL with the fix for anything that is not PASS.
Exit code is 1 if any check FAILs.
"""
import os
import shutil
import warnings
import subprocess
import sys
import time

warnings.filterwarnings("ignore")

ROOT = os.path.dirname(os.path.abspath(__file__))
HAL = "/Library/Audio/Plug-Ins/HAL/BlackHole2ch.driver"
results = []


def report(status, name, detail="", fix=""):
    results.append(status)
    line = f"[{status}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)
    if fix and status != "PASS":
        print(f"       fix: {fix}")


def run(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return ""


# 1. Python -----------------------------------------------------------------
v = sys.version_info
in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
if v >= (3, 10) and in_venv:
    report("PASS", "Python", f"{v.major}.{v.minor} in virtualenv {sys.prefix}")
elif v >= (3, 10):
    report("WARN", "Python", f"{v.major}.{v.minor} but not the project venv",
           "run this with .venv/bin/python doctor.py")
else:
    report("FAIL", "Python", f"{v.major}.{v.minor} is too old (need 3.10+)",
           "uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt")

# 2. Python packages ---------------------------------------------------------
missing = []
for mod in ("flask", "numpy", "scipy", "sounddevice", "soundfile", "faster_whisper"):
    try:
        __import__(mod)
    except Exception:
        missing.append(mod)
if missing:
    report("FAIL", "Required packages", f"missing: {', '.join(missing)}",
           "uv pip install --python .venv/bin/python -r requirements.txt")
else:
    report("PASS", "Required packages", "flask, numpy, scipy, sounddevice, soundfile, faster_whisper")

try:
    import pyannote.audio  # noqa: F401
    report("PASS", "Diarization package (optional)", "pyannote.audio imports")
except Exception as e:
    report("WARN", "Diarization package (optional)", f"pyannote.audio failed: {type(e).__name__}: {e}",
           "uv pip install --python .venv/bin/python -r requirements.txt  (torch/torchaudio must be 2.8.x)")

# 3. Homebrew tools ----------------------------------------------------------
if shutil.which("SwitchAudioSource"):
    report("PASS", "SwitchAudioSource", shutil.which("SwitchAudioSource"))
else:
    report("FAIL", "SwitchAudioSource", "not on PATH", "brew install switchaudio-osx")

# 4. BlackHole driver installed + loaded -------------------------------------
if os.path.isdir(HAL):
    report("PASS", "BlackHole driver installed", HAL)
else:
    report("FAIL", "BlackHole driver installed", "not found in /Library/Audio/Plug-Ins/HAL",
           "brew install --cask blackhole-2ch  (needs your password), then reboot")

outputs = run(["SwitchAudioSource", "-a", "-t", "output"]).splitlines() if shutil.which("SwitchAudioSource") else []
inputs = run(["SwitchAudioSource", "-a", "-t", "input"]).splitlines() if shutil.which("SwitchAudioSource") else []
bh_loaded = any("blackhole" in d.lower() for d in inputs + outputs)
if bh_loaded:
    report("PASS", "BlackHole loaded by macOS", "visible as an audio device")
elif os.path.isdir(HAL):
    # Driver on disk but not loaded: coreaudiod predates the install.
    pid = run(["pgrep", "-x", "coreaudiod"]).split("\n")[0]
    started = run(["ps", "-o", "lstart=", "-p", pid]) if pid else "?"
    report("FAIL", "BlackHole loaded by macOS", f"driver on disk but not loaded (coreaudiod started {started})",
           "reboot, or run: sudo killall coreaudiod   — then restart this app")
else:
    report("FAIL", "BlackHole loaded by macOS", "driver not installed", "see previous check")

# 5. Multi-Output Device -----------------------------------------------------
if any(d.strip() == "Multi-Output Device" for d in outputs):
    report("PASS", "Multi-Output Device exists", "named exactly 'Multi-Output Device'")
    print("       verify by hand in Audio MIDI Setup: BlackHole 2ch AND your speakers are ticked under Use;")
    print("       Primary Device = your speakers; Drift Correction ticked on the BlackHole row.")
else:
    report("FAIL", "Multi-Output Device exists", "no output device with that exact name",
           "Audio MIDI Setup > + > Create Multi-Output Device; tick BlackHole 2ch + speakers; name it 'Multi-Output Device'")

# 6. Devices as the app will see them ----------------------------------------
try:
    import sounddevice as sd
    import config
    names = [d["name"] for d in sd.query_devices() if d["max_input_channels"] > 0]
    bh = [n for n in names if config.BLACKHOLE_DEVICE in n.lower()]
    mic = [n for n in names if config.MIC_DEVICE in n.lower()]
    if bh:
        report("PASS", "App can see BlackHole input", bh[0])
    else:
        report("FAIL", "App can see BlackHole input", f"inputs visible: {names}",
               "if BlackHole was just loaded, restart the app; else set BLACKHOLE_DEVICE=<substring>")
    if mic:
        report("PASS", "App can see microphone", mic[0])
    else:
        report("WARN", "App can see microphone", f"no input matches '{config.MIC_DEVICE}'; inputs: {names}",
               "export MIC_DEVICE='<substring of your mic name>'")
except Exception as e:
    report("FAIL", "Audio device enumeration", f"{type(e).__name__}: {e}")

# 7. Whisper model cached ----------------------------------------------------
cache = os.path.expanduser("~/.cache/huggingface/hub")
try:
    import config
    model = config.TRANSCRIBE_MODEL
    hit = [d for d in os.listdir(cache) if d.startswith("models--") and model in d] if os.path.isdir(cache) else []
    if hit:
        report("PASS", "Whisper model cached", hit[0])
    else:
        report("WARN", "Whisper model cached", f"'{model}' not in {cache}",
               "first Record will download ~1.5 GB; or pre-download with: "
               ".venv/bin/python -c \"from faster_whisper import WhisperModel as M; M('large-v3-turbo', compute_type='int8')\"")
except Exception as e:
    report("WARN", "Whisper model cached", f"could not check: {e}")

# 8. App running -------------------------------------------------------------
try:
    import urllib.request
    with urllib.request.urlopen("http://localhost:5001/", timeout=3) as r:
        report("PASS" if r.status == 200 else "WARN", "App running", f"http://localhost:5001 -> HTTP {r.status}")
except Exception:
    report("WARN", "App running", "nothing on http://localhost:5001", ".venv/bin/python app.py")

# 9. Diarization token (optional) --------------------------------------------
if os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN"):
    report("PASS", "Diarization token (optional)", "HF_TOKEN set")
else:
    report("WARN", "Diarization token (optional)", "HF_TOKEN not set; transcripts will have no speaker labels",
           "export HF_TOKEN=hf_xxx after accepting pyannote/speaker-diarization-3.1 terms on huggingface.co")

print()
fails = results.count("FAIL")
print(f"{results.count('PASS')} pass, {results.count('WARN')} warn, {fails} fail")
sys.exit(1 if fails else 0)
