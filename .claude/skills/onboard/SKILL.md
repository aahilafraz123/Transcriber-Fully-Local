---
name: onboard
description: Walk a new user through installing and configuring the local meeting transcriber on macOS (BlackHole, Multi-Output Device, Python venv, model download) and verify each stage. Use when the user asks to set up, install, onboard, or says recording/Record button/BlackHole/localhost is not working.
---

# Onboarding: local meeting transcriber on macOS

You are guiding someone who may never have used a terminal or Audio MIDI
Setup. Be concrete. Never assume a stage is done: run the check.

## Ground rules

- Always run Python as `.venv/bin/python`. Never `python` or `python3`
  (macOS ships Python 3.9 with no packages; `python` does not exist).
- Two things need the user, not you: anything with `sudo` (their password) and
  clicks inside Audio MIDI Setup. Give exact click-by-click steps, then verify
  from the shell afterwards.
- After BlackHole is loaded or any audio device changes, the app must be
  restarted. PortAudio snapshots the device list at process start.
- Read the README's "Setup Playbook" section for the canonical steps.

## Step 0: run the health check first

```bash
.venv/bin/python doctor.py
```

If `.venv` does not exist yet, that fails; go to Stage 4 first, then rerun.
The output tells you which stages below are outstanding. Fix them in order,
rerun `doctor.py` after each, and stop only when it reports 0 fail.

## Stage 1: command-line tools

Check: `which brew SwitchAudioSource uv`.
Fix: `brew install switchaudio-osx uv` (no password needed).
If Homebrew is missing, send the user to https://brew.sh; its installer
needs their password.

## Stage 2: BlackHole driver

Check installed: `ls /Library/Audio/Plug-Ins/HAL/BlackHole2ch.driver`.
Check loaded: `SwitchAudioSource -a` lists `BlackHole 2ch`.

Fix if not installed: the user runs `brew install --cask blackhole-2ch` in
their own terminal and enters their password. You cannot do this.

Fix if installed but not loaded: coreaudiod predates the install. Compare
`ps -o lstart= -p $(pgrep -x coreaudiod)` with the driver's install time. The
user runs `sudo killall coreaudiod` or reboots. Then they must quit and reopen
Audio MIDI Setup, and you must restart the app.

## Stage 3: Multi-Output Device (GUI, user does it)

Check: `SwitchAudioSource -a -t output` lists exactly `Multi-Output Device`.

Give these steps verbatim if it is missing or wrong:
1. Open Audio MIDI Setup (Spotlight, type "Audio MIDI Setup").
2. Click the `+` at the bottom-left, choose "Create Multi-Output Device".
3. In the table on the right, tick "Use" on BlackHole 2ch and on the
   speakers/headphones they listen through (usually MacBook Pro Speakers).
4. Set "Primary Device" at the top to those speakers/headphones.
5. Tick "Drift Correction" on the BlackHole 2ch row only.
6. Confirm the name is exactly "Multi-Output Device".

You cannot verify the ticked sub-devices from the shell. Ask the user to
confirm BlackHole 2ch and their speakers are both ticked, or ask for a
screenshot. An Aggregate Device is NOT needed; tell the user so if they ask.

## Stage 4: Python environment

Check: `.venv/bin/python --version` is 3.10 or newer, and
`.venv/bin/python -c "import sounddevice, faster_whisper, pyannote.audio"`.

Fix:
```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
```
requirements.txt pins torch/torchaudio to 2.8.x because pyannote.audio 3.x
breaks on torchaudio 2.9+. Do not "upgrade" them.

Pre-download the model so the first recording does not stall:
```bash
.venv/bin/python -c "from faster_whisper import WhisperModel as M; M('large-v3-turbo', compute_type='int8')"
```

## Stage 4b: microphone check (always do this)

Users often ask whether their own voice is captured. It is, via the Mac's
microphone, as a separate input from BlackHole. No Aggregate Device is needed.
Prove it rather than asserting it. With the app running and nothing recording,
have macOS speak through the speakers while the mic-test endpoint listens:

```bash
(sleep 0.5; say "The quick brown fox jumps over the lazy dog") & curl -s -X POST -H 'Content-Type: application/json' -d '{"seconds": 5}' http://localhost:5001/api/mic-test; wait
```

Expected: `"silent": false` and `"text"` containing the sentence. If
`"silent": true`, macOS microphone permission is off for the app that launched
the server (Terminal, or the Claude app). The user enables it in System
Settings > Privacy & Security > Microphone and restarts the app. The same
check is available to the user as the "Test microphone" button under Audio
Devices on the main page.

## Stage 5: run and smoke-test

The user's normal way to run it is the menu bar app. Build it (idempotent)
and open it; it starts the server itself and opens the browser:
```bash
./make_app.sh && open ~/Applications/Transcriber.app
```
Only one server can own port 5001. If the menu bar app is running, do not
also start the `transcriber` preview server from `.claude/launch.json`; use
the one that is up. From a terminal the equivalent is `./start.sh`.
Its log is at `~/Library/Logs/Transcriber/server.log`.

Recording has two modes, and the UI asks every time: `virtual` (BlackHole
call audio + mic, switches output to Multi-Output Device) and `in_person`
(100% mic, nothing else touched; works without BlackHole). Smoke test both
from the shell, then report the result to the user:
```bash
curl -s -X POST -H 'Content-Type: application/json' -d '{"mode":"virtual"}' http://localhost:5001/start; sleep 3; curl -s -X POST http://localhost:5001/stop; SwitchAudioSource -c
curl -s -X POST -H 'Content-Type: application/json' -d '{"mode":"in_person"}' http://localhost:5001/start; sleep 3; curl -s -X POST http://localhost:5001/stop; SwitchAudioSource -c
```
Expected: start returns `"status": "recording"`, stop returns a meeting id,
and the final line is the user's normal speakers, not Multi-Output Device.

If start returns `"BlackHole device not found"`, the response includes
`inputs_visible`. If BlackHole is absent there but present in
`SwitchAudioSource -a`, restart the app. If absent in both, go to Stage 2.

If the output is stuck on Multi-Output Device afterwards:
`SwitchAudioSource -s "MacBook Pro Speakers"`.

## Resuming a stopped meeting

POST /start accepts `resume_id`; the stop appends audio and transcript to that
meeting instead of creating a new one. Smoke test (replace 1 with a real id):
```bash
curl -s -X POST -H 'Content-Type: application/json' -d '{"mode":"in_person","resume_id":1}' http://localhost:5001/start; sleep 3; curl -s -X POST http://localhost:5001/stop
```
Expected: the response has a `parts` list and the transcript contains
"— Resumed". A 409 `still_transcribing` means the previous part's final
transcription is still running; wait a few seconds.

## Optional: diarization

Only if the user wants speaker labels. They need a free Hugging Face token
and must accept the terms of `pyannote/speaker-diarization-3.1` on the site.
They set `export HF_TOKEN=hf_...` before launching. Never ask them to paste
the token into chat; have them export it in their own terminal.

## Finish

Rerun `doctor.py`, paste a short summary of PASS/WARN/FAIL to the user, and
tell them: open http://localhost:5001, click Record, play audio, talk, click
Stop, then open the meeting to read the transcript.
