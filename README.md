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
- **Optional speaker diarization** ("who said what"), running locally via
  pyannote.
- **Automatic meeting detection** for Teams/Slack calls.
- **Automatic cleanup** of old recordings (configurable).

---

## Prerequisites

- **macOS** with [BlackHole](https://github.com/ExistentialAudio/BlackHole)
  (2ch) installed.
- [**SwitchAudioSource**](https://github.com/deweller/switchaudio-osx) for audio
  routing:
  ```bash
  brew install switchaudio-osx
  ```
- **Python 3.10+**.
- *(Optional, for diarization only)* a free
  [Hugging Face](https://huggingface.co) account and access token.

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

```bash
pip install -r requirements.txt
python app.py            # serves http://localhost:5001
```

Open **http://localhost:5001** in your browser and click **Record**.

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

1. Click **Record**. The app switches your output to the Multi-Output Device and
   starts capturing.
2. Watch the **live preview** transcribe as the meeting goes. Use **Copy
   transcript** any time to grab the transcript so-far without stopping.
3. Click **Stop**. Your previous output device is restored, and a more accurate
   transcript is generated in the background.
4. Open the meeting to read the transcript, **rename the meeting**, and (if
   diarization is enabled) **rename speakers**.

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
