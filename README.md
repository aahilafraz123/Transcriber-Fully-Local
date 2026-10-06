# Transcriber — Fully Local

> A fully local, on-device meeting transcriber for **macOS**. Captures your
> call audio and microphone, mixes them, and transcribes everything **on your
> own machine** — no cloud, no APIs, no data leaving your computer.

![Platform](https://img.shields.io/badge/platform-macOS-black)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![Privacy](https://img.shields.io/badge/data-100%25%20local-success)
![Cloud](https://img.shields.io/badge/cloud-none-lightgrey)

---

## Overview

Transcriber records a meeting by capturing two audio streams — the **system
audio** of your call (the remote participants) and your **microphone** — blends
them into a single clean mix, and transcribes it locally with
[faster-whisper](https://github.com/SYSTRAN/faster-whisper). A live preview
appears as you talk, and a higher-accuracy transcript is produced in the
background once you stop. Optional, fully-local **speaker diarization** can label
*who said what*.

Everything runs on your Mac. There are no API keys to send your audio anywhere,
no usage metering, and no telemetry.

---

## 🔒 Privacy & Enterprise Use

This project is built for environments where **data leakage is unacceptable**.

- **100% on-device processing.** Audio capture, mixing, transcription, and
  diarization all run locally. Your meeting audio and transcripts never leave
  your machine.
- **No cloud, no APIs, no telemetry.** There are no outbound calls to any
  transcription service. Transcripts and recordings are stored locally
  (`meetings.json` and the `recordings/` folder).
- **Designed for confidential settings.** Because nothing is uploaded, it suits
  enterprise and regulated contexts — legal, finance, healthcare, R&D, and any
  internal meeting where confidentiality matters and third-party transcription
  services are prohibited.

> **The only external network access** is a **one-time download of the
> speech-recognition model weights** on first run (and, *if you choose* to
> enable diarization, the diarization model weights). After those are cached
> locally, the app runs entirely offline. No audio is ever part of those
> downloads.

---

## ⚠️ Responsible Use & Compliance

Recording meetings and conversations may be subject to consent requirements and
privacy laws that vary by jurisdiction, industry, and organization.

**Use this software at your own discretion and in accordance with your
company's privacy policies, IT/security rules, and all applicable laws and
regulations.** Where required, notify participants that the meeting is being
recorded and obtain any necessary consent. You are solely responsible for how
you record, store, and handle meeting data with this tool.

---

## 🖥️ Platform — macOS Only

This application is **designed and supported for macOS only**. It depends on
macOS-specific audio infrastructure:

- [**BlackHole**](https://github.com/ExistentialAudio/BlackHole) — a virtual
  audio driver used to capture system/call audio.
- **Audio MIDI Setup** — the built-in macOS utility used to create the
  Multi-Output and Aggregate devices.
- [**SwitchAudioSource**](https://github.com/deweller/switchaudio-osx) — used to
  route audio output automatically while recording.

It will not run on Windows or Linux as-is.

---

## How It Works

```
 ┌──────────────────────┐        ┌──────────────────────┐
 │  Remote participants  │        │         You           │
 │  (Teams / Slack call) │        │   (your microphone)   │
 └──────────┬───────────┘        └──────────┬───────────┘
            │ system audio                   │ mic audio
            ▼                                ▼
     ┌─────────────┐                  ┌─────────────┐
     │  BlackHole   │                  │  Microphone  │
     └──────┬──────┘                  └──────┬──────┘
            │                                │
            └──────────────┬─────────────────┘
                           ▼
                 80% system / 20% mic mix
                           ▼
            ┌──────────────────────────────┐
            │  faster-whisper (on-device)   │
            │       large-v3-turbo          │
            └───────────────┬──────────────┘
                            ▼
              Transcript  (+ optional diarization)
```

- **System audio** is captured through BlackHole and contains the remote
  participants of a Teams/Slack call.
- **In-person mode** skips BlackHole entirely and records 100% from the
  microphone, for meetings in a room or on your phone.
- **Your microphone** is captured separately.
- The two are blended into a proven **80% system / 20% mic** mix, and that mix
  is what produces the transcript.
- During recording, a streaming **live preview** transcribes utterances as you
  go (VAD-segmented on silence boundaries, so no duplicated/run-on fragments).
- After you stop, the full recording is transcribed in the background with the
  same local **large-v3-turbo** model — using beam search, VAD filtering, and
  whole-audio context for a cleaner, more accurate final transcript that
  replaces the live preview.

---

## Features

- **Fully local transcription** with faster-whisper — nothing is uploaded.
- **Live preview** while you record, plus a higher-accuracy background pass on
  stop.
- **Mid-meeting "Copy transcript"** snapshot, so you can grab the transcript
  so-far without stopping the recording.
- **Virtual or In-person, asked every time you hit Record.** Virtual records
  the call audio (BlackHole) plus your mic; In-person records 100% from the
  Mac's microphone for meetings in a room or on your phone, and leaves your
  speakers untouched.
- **Microphone check** in the Audio Devices panel: records 4 seconds, shows
  the level, and tells you what it heard.
- **Resume a stopped meeting.** Hover a meeting in the list (or open it) and
  click **Resume**. New audio and transcript are appended to that meeting,
  marked with a "Resumed" line; the duration adds up and the original name
  and date stay.
- **Optional meeting names**, set before or during the recording; unnamed
  meetings get a date/time title automatically.
- **Navigate while recording**: browse past transcripts and come back to the
  live page without interrupting the capture.
- **Optional speaker diarization** ("who said what"), running locally via
  pyannote.
- **Automatic meeting detection** for Teams/Slack calls.
- **Automatic cleanup** of old recordings (configurable).
- **Menu bar launcher** (`Transcriber.app`): one click starts the server and
  opens the page; the icon shows a REC timer while recording.

---

## Prerequisites

- **macOS** with [BlackHole](https://github.com/ExistentialAudio/BlackHole)
  (2ch) installed.
- [**SwitchAudioSource**](https://github.com/deweller/switchaudio-osx) for audio
  routing:
  ```bash
  brew install switchaudio-osx
  ```
- **Python 3.10+** (macOS ships 3.9, which is too old — the playbook installs
  3.12 via `uv`).
- *(Optional, for diarization only)* a free
  [Hugging Face](https://huggingface.co) account and access token.

---

## 🚀 Setup Playbook (do these in order)

Five stages. Each one has a check so you know it worked before moving on.
If anything looks off at any point, run the health check and it will tell you
which stage to go back to:

```bash
.venv/bin/python doctor.py
```

> Using Claude Code? Type `/onboard` in the project and it walks you through
> this playbook, runs the checks for you, and fixes what it can.

### Stage 1 — Command-line tools (5 min)

You need [Homebrew](https://brew.sh). Then:

```bash
brew install switchaudio-osx uv
```

- `SwitchAudioSource` lets the app flip your output device while recording.
- `uv` installs a modern Python for you. The Python that ships with macOS is
  3.9 and **will not work**; this app needs 3.10+.

**Check:** `SwitchAudioSource -c` prints your current output device.

### Stage 2 — BlackHole virtual audio driver (needs your password + a reboot)

```bash
brew install --cask blackhole-2ch
```

Homebrew asks for your macOS login password to run the installer. Then
**reboot** (or run `sudo killall coreaudiod`). Until the audio system
restarts, macOS does not load the driver and nothing downstream can see it.

**Check:** `SwitchAudioSource -a` lists `BlackHole 2ch`. Also open **Audio
MIDI Setup** (Spotlight → "Audio MIDI Setup") and confirm **BlackHole 2ch**
appears in the left column with "2 ins / 2 outs".

### Stage 3 — Multi-Output Device in Audio MIDI Setup (2 min, GUI)

This is the one device you must create. It sends call audio to **two places at
once**: your speakers (so you hear it) and BlackHole (so the app records it).

1. Open **Audio MIDI Setup**.
2. Click **`+`** (bottom-left) → **Create Multi-Output Device**.
3. In the table on the right, tick **Use** on both **BlackHole 2ch** and your
   speakers/headphones (e.g. **MacBook Pro Speakers**).
4. Set **Primary Device** (dropdown at the top) to your speakers/headphones.
5. Tick **Drift Correction** on the **BlackHole 2ch** row only.
6. Make sure the device is named exactly **`Multi-Output Device`**. That is
   the default name; if you changed it, right-click → Rename.

You do **not** need an Aggregate Device. The app captures BlackHole and your
microphone as two separate inputs and mixes them itself. If you have an empty
Aggregate Device lying around, it is harmless.

**Check:** `SwitchAudioSource -a -t output` lists `Multi-Output Device`.

### Stage 4 — Python environment (5 min, ~3 GB download)

From the project folder:

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
```

Optional but recommended: pre-download the speech model (~1.5 GB) so the
first recording doesn't stall:

```bash
.venv/bin/python -c "from faster_whisper import WhisperModel as M; M('large-v3-turbo', compute_type='int8')"
```

**Check:** `.venv/bin/python doctor.py` shows PASS for Python, packages, and
model.

### Stage 5 — Run it

**Easiest: the menu bar app.** Build it once:

```bash
./make_app.sh
```

That creates **Transcriber.app** in `~/Applications`. Open it (Spotlight →
"Transcriber"). A 🎙 icon appears in the menu bar at the top right, the server
starts, and your browser opens to http://localhost:5001. The dropdown has
**Open in Browser**, **Stop/Start Transcriber**, **Show Server Log**, and
**Quit**. While a meeting records, the icon shows a live **REC** timer. To have
it start every time you log in: System Settings → General → Login Items → `+`
→ Transcriber. The first recording will ask for microphone permission for
"Transcriber"; click Allow.

**From a terminal instead:**

```bash
./start.sh            # same as: .venv/bin/python app.py
```

Always use `.venv/bin/python` (or the scripts above), not `python` or
`python3`. Those point at the system Python, which has none of the packages.

Open **http://localhost:5001**. First, expand **Audio Devices** at the bottom
and click **Test microphone**: speak for 4 seconds and it shows what it heard.
If it reports silence, macOS has not given microphone permission to the app
that launched the server (System Settings → Privacy & Security → Microphone).

Then click **Record**, choose **Virtual**, play any audio, talk, and click
**Stop**. You should still hear the audio while recording, the live preview
should show text, and your output device should switch back when you stop.
For a room or phone meeting choose **In-person** instead; only the microphone
is used.

### Common mistakes

| Symptom | Cause | Fix |
| --- | --- | --- |
| `zsh: command not found: python` | macOS has no `python`, only `python3` | use `.venv/bin/python app.py` |
| `ModuleNotFoundError: No module named 'sounddevice'` | ran with system Python | use `.venv/bin/python app.py` |
| BlackHole missing from Audio MIDI Setup after install | driver not loaded yet | reboot, or `sudo killall coreaudiod` |
| Record button does nothing / "BlackHole device not found" | app started before BlackHole was loaded | restart the app (`Ctrl+C`, run again) |
| Output stuck on "Multi-Output Device" after a failed start | restore didn't run | `SwitchAudioSource -s "MacBook Pro Speakers"` |
| You can't hear the call while recording | speakers not ticked in Multi-Output Device | Stage 3, step 3 |
| Transcript has only your voice / only theirs | BlackHole not ticked, or mic name mismatch | Stage 3, or set `MIC_DEVICE` |
| Your voice is missing; **Test microphone** says silence | macOS mic permission off for Terminal (or whatever launched the app) | System Settings → Privacy & Security → Microphone → enable it, restart the app |
| `pyannote.audio` import error mentioning `AudioMetaData` | torchaudio too new | reinstall from `requirements.txt` (pins torch 2.8) |

---

## 🎧 macOS Audio Setup — Step by Step

To capture call audio while still hearing it yourself, you configure devices in
**Audio MIDI Setup** (found in `/Applications/Utilities`, or via Spotlight).
This is the most important part of getting good recordings.

### 1. Install BlackHole

```bash
brew install blackhole-2ch
```

This adds a virtual audio device named **BlackHole 2ch**. Anything routed to it
can be recorded; nothing is played out loud through it.

### 2. Create a Multi-Output Device  *(required — output side)*

A **Multi-Output Device** lets your Mac send audio to **two places at once**:
your speakers/headphones (so *you* hear the call) **and** BlackHole (so the app
can *capture* it). Without this, you'd have to choose between hearing the call
or recording it.

1. Open **Audio MIDI Setup**.
2. Click the **`+`** button in the bottom-left corner → **Create Multi-Output
   Device**.
3. In the device list on the right, **check both**:
   - **BlackHole 2ch**
   - your normal output (e.g. **MacBook Pro Speakers** or your headphones)
4. Set your **real speakers/headphones as the Primary (Master) Device** (use the
   dropdown at the top). This keeps audio in sync and avoids glitches.
5. Enable **Drift Correction** on **BlackHole 2ch** (the checkbox in its row).
6. Rename the device to exactly **`Multi-Output Device`** (right-click → Rename,
   or rename in the left sidebar). **The app looks for a device with this
   name** and switches to it automatically while recording, then restores your
   previous output when you stop.

> While recording, you'll continue to hear the call normally — the Multi-Output
> Device plays to your speakers/headphones and feeds BlackHole simultaneously.

### 3. Create an Aggregate Device  *(optional — input side)*

A **Multi-Output Device handles *output* (playback)**. An **Aggregate Device
handles *input* (recording)** — it combines multiple input sources, such as
**BlackHole** and your **microphone**, into one logical input device. This is
useful if you prefer to expose a single combined input to the system, or use
other recording tools alongside this app.

> **Note:** This app captures **BlackHole and your microphone as two separate
> inputs by default** and mixes them itself (the 80/20 blend), so an Aggregate
> Device is **not required** for normal use. Create one only if you specifically
> want a single combined input device.

1. Open **Audio MIDI Setup**.
2. Click the **`+`** button → **Create Aggregate Device**.
3. **Check** the inputs you want to combine:
   - **BlackHole 2ch**
   - your **microphone** (e.g. **MacBook Pro Microphone**)
4. Enable **Drift Correction** on the secondary device(s) to keep them in sync.
5. Optionally rename it for clarity (e.g. `Aggregate Input`).

**Quick distinction:**

| Device type | Direction | Purpose |
| --- | --- | --- |
| **Multi-Output Device** | Output (playback) | Hear the call *and* feed BlackHole at the same time. **Required.** |
| **Aggregate Device** | Input (recording) | Combine multiple inputs (BlackHole + mic) into one. **Optional.** |

### 4. Verify device names

The app finds devices by **name substring** (case-insensitive). Make sure these
match your hardware (override via environment variables if not — see
[Configuration](#configuration)):

- `BLACKHOLE_DEVICE` (default: `blackhole`)
- `MIC_DEVICE` (default: `macbook pro microphone`)

---

## Installation & Running

See the [Setup Playbook](#-setup-playbook-do-these-in-order) above for the
full ordered walkthrough. The short version, once BlackHole and the
Multi-Output Device exist:

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
./make_app.sh                      # builds ~/Applications/Transcriber.app (menu bar launcher)
open ~/Applications/Transcriber.app
```

Or without the menu bar app: `./start.sh` (serves http://localhost:5001).

Health check at any time:

```bash
.venv/bin/python doctor.py
```

> On first run, faster-whisper downloads the `large-v3-turbo` weights
> (~1.5 GB), cached locally afterward. This is a one-time download; everything
> runs offline once cached. With the default settings, a single model is loaded
> and shared by both the live preview and the final transcript.

---

## Enabling Diarization (optional)

Speaker diarization labels *who said what*. It runs **locally**, but the model
weights are gated behind a free Hugging Face account.

> If you skip this, the app still produces the full transcript — just without
> per-speaker labels. (It is off unless a token is provided.)

1. Create a free [Hugging Face](https://huggingface.co) account and an access
   token.
2. Accept the user conditions for
   [`pyannote/speaker-diarization-3.1`](https://huggingface.co/pyannote/speaker-diarization-3.1)
   (and its segmentation dependency).
3. Export the token before launching:

   ```bash
   export HF_TOKEN=hf_xxx
   python app.py
   ```

**How it stays accurate:** your own voice is never in the system-audio stream
(the call app doesn't echo you back), so the app diarizes the **clean BlackHole
recording** to separate the remote speakers, and attributes anything spoken when
the system audio is silent to **You**. You rename `Speaker 1/2/3` once in the UI
and it sticks.

---

## Configuration

All settings are environment variables (see `config.py`):

| Variable | Default | Purpose |
| --- | --- | --- |
| `TRANSCRIBE_MODEL` | `large-v3-turbo` | faster-whisper model (live + final) |
| `TRANSCRIBE_DEVICE` | `cpu` | `cpu` or `cuda` |
| `TRANSCRIBE_COMPUTE_TYPE` | `int8` | `int8`, `int8_float16`, `float16`… |
| `LIVE_MODEL` | (reuse main model) | optional lighter model for the live preview |
| `MIX_BH_WEIGHT` / `MIX_MIC_WEIGHT` | `0.8` / `0.2` | system/mic mix blend |
| `BLACKHOLE_DEVICE` | `blackhole` | input device name substring |
| `MIC_DEVICE` | `macbook pro microphone` | input device name substring |
| `DEFAULT_OUTPUT_DEVICE` | (none) | fallback output to restore on stop |
| `SAMPLE_RATE` | `48000` | capture sample rate |
| `LIVE_MIN_SILENCE_MS` | `600` | pause length that finalizes a live utterance |
| `LIVE_MAX_BUFFER_SEC` | `30` | force a cut if no natural pause occurs |
| `LIVE_MIN_SPEECH_SEC` | `0.4` | minimum speech before transcribing |
| `HF_TOKEN` | (none) | enables diarization |
| `DIARIZATION_ENABLED` | `1` | turn diarization off entirely |
| `DIARIZATION_MODEL` | `pyannote/speaker-diarization-3.1` | diarization model |
| `YOU_LABEL` | `You` | label for your own speech |
| `WAV_MAX_AGE_DAYS` | `5` | auto-delete recordings older than this |

> **Memory note:** by default a single `large-v3-turbo` model (~1.5 GB on disk,
> a few GB of RAM under `int8`) is loaded and shared by the live and final
> passes. Set `LIVE_MODEL` to a smaller model if you want the live preview to be
> even lighter at the cost of a second loaded model.

---

## Usage

1. Click **Record**. You are asked **every time** how you are meeting:
   - **Virtual**: a call on this Mac (Teams, Slack, Zoom...). Records the call
     audio through BlackHole plus your microphone, and switches your output to
     the Multi-Output Device for the duration.
   - **In-person**: a meeting in the room or on your phone. Records **100%
     from the Mac's microphone**. BlackHole and your speakers are not touched,
     so this mode works even without the audio setup above.

   Optionally type a name in the same dialog. Leave it blank and the meeting is
   named by date and time (e.g. "Meeting September 20 5:48pm"). Naming never
   blocks recording.
2. The live page opens. Watch the **live preview** transcribe as the meeting
   goes. Use **Copy transcript** any time to grab the transcript so-far without
   stopping.
3. **Name or rename the meeting while it records.** The name box on the live
   page (and on the in-progress row of the meetings list) saves as you type.
   Whatever it says when you stop becomes the meeting title.
4. **Navigate freely while recording.** Click **← All meetings** to go back to
   the list; the recording keeps going and shows as a red in-progress row at
   the top with a live timer. Open any past meeting to read its transcript,
   then use the **Live transcript** link (top right on every page) to jump
   back. Stop works from the live page or the list.
5. Click **Stop**. Your previous output device is restored, and a more accurate
   transcript is generated in the background.
6. Open the meeting to read the transcript, **rename the meeting** (click the
   title), and (if diarization is enabled) **rename speakers**.
7. **Need to continue a meeting you already stopped** (a lecture after the
   break, a call that reconnected)? Click **Resume recording** on the meeting
   page, or hover the meeting in the list and click **Resume**. You are asked
   Virtual or In-person again, then recording continues into the same
   meeting: audio is appended to its recording file, the new transcript is
   added under a "— Resumed 1:05pm —" line, and the list shows "2 parts".
   The live page can show the earlier transcript above the new one.

---

## Limitations

- **macOS only** — depends on BlackHole, Audio MIDI Setup, and
  `SwitchAudioSource`.
- **Diarization** attributes simultaneous talk-over to one speaker, may merge
  very similar voices, and can't infer real names (you rename speakers once in
  the UI).

---

## Disclaimer

This software is provided **"as is", without warranty of any kind**. You are
responsible for using it in compliance with your organization's policies and all
applicable laws, including obtaining any required consent before recording. See
[Responsible Use & Compliance](#️-responsible-use--compliance) above.
