# Call Live Text

Double-click **Start Call Transcriber.cmd**. Keep the window running and keep the
phone connected through wireless ADB. Answer calls normally. The default setting
starts capture when the connected phone reports an answered call and finishes
when the call ends. Each session has its own UTF-8 file in `transcripts/`.

The large text area updates live; each recognized sentence is immediately saved
to disk. **Open TXT in Notepad** opens the saved file. Notepad is a snapshot and
may need reopening to see later text; the Call Live Text window updates itself.

Choose **English**, **Gujarati**, **Hindi**, or **Auto** before the call. Selecting
the language works better for short utterances. The initial live test uses English.
Expect a pause plus recognition time, not instantaneous word-by-word dictation.

## Audio routes

- **Phone: caller only**: direct wireless `voice-call-downlink`, aimed at the other
  party. This depends on the phone firmware allowing actual call audio capture.
- **Phone: both sides**: `voice-call`, useful to compare devices that expose a mix.
- **PC: default speaker audio**: records the default Windows output's loopback.
  Use when Phone Link already plays call audio on the PC. Other PC audio is also
  captured, so pause music and videos. This does not set up Phone Link pairing.

A stream starting does not prove that caller audio is available. The audio level
and a real caller transcript must be checked. Silence produces no transcript.
This program does not root the phone, change call permissions, or answer calls.

## Storage and privacy

Recognition runs locally using the downloaded multilingual Whisper small model.
No API key or paid cloud service is used. Call audio is processed in memory;
normal use saves text, not call recordings. `status.json` includes the last
recognized line and operating status for diagnostics. `bridge.log` contains
scrcpy server diagnostics. Text files remain until you delete them yourself.

## Installed configuration

- Python environment: `../venv`
- scrcpy 4.1 / ADB: `C:\SMS - ADB\tools\scrcpy-win64-v4.1`
- Downloaded model: `models/`
- Current phone discovery: exactly one authorized ADB device
- CPU inference: int8, four threads

The existing scrcpy screen mirror can stay open. This app uses an independent
audio-only server session and cleans up only its own forwarded port.

## Checks

Run from this directory with `..\venv\Scripts\python.exe -m unittest -v`.
Synthetic English speech recognition and direct raw stream transport were checked
during setup. A real cellular call is required to establish caller capture support.

Reference: https://github.com/Genymobile/scrcpy/blob/v4.1/doc/audio.md
## Realtime API bridge

For lower latency than the Android APK recognizer, run
`Start Realtime API Bridge.cmd` on the PC. It captures caller audio from the
connected phone with scrcpy audio, converts it to 24 kHz mono PCM, and streams
100 ms chunks to the OpenAI Realtime API.

Set `OPENAI_API_KEY` before starting the bridge. The default transcription model
is `gpt-4o-mini-transcribe`; output is saved in `api_transcripts`.

Dry capture test:

```powershell
..\venv\Scripts\python.exe realtime_api_bridge.py --dry-run --dry-seconds 5
```

The current capture test passed with 49 chunks in 5.06 seconds, so the phone to
PC audio path is already running at about 100 ms per chunk.

## Local Qwen voice bot

Use `Start Call Qwen Voice Bot.cmd` for the no-ChatGPT flow:

1. Phone caller audio is captured on the PC.
2. PC faster-whisper converts caller speech to text in short chunks.
3. The text is sent to local Ollama `qwen3.5:4b`.
4. Qwen streams the answer.
5. The existing Windows voice helper speaks the answer on the PC speaker.
6. Caller text and Qwen answers are saved in `qwen_call_logs`.

This path uses local Ollama and Windows speech. It does not use ChatGPT or an
OpenAI API key. Current benchmark from this script: `hello` returns first Qwen
text in about 0.63 seconds and a full short answer in about 1.18 seconds.
Offline test on 6 seconds of captured call audio took 2.606 seconds for STT, then
0.587 seconds to first Qwen text.

Use `Start Call Qwen Voice Bot To Caller.cmd` when the bot answer should be routed
to a virtual microphone instead of the laptop speakers. This launcher sends the
bot voice to `CABLE Input (VB-Audio Virtual Cable)`. The matching recording side
is `CABLE Output (VB-Audio Virtual Cable)`.

For the caller to hear the bot cleanly, the cellular call must be using the PC as
its microphone path, for example through Phone Link/Bluetooth hands-free or a
Voicemeeter route, and that app/device must use `CABLE Output` as the microphone.
ADB/scrcpy can capture call audio, but it cannot directly inject audio into a
normal cellular call uplink on a non-rooted phone.

Before testing with a real caller, run `Test Caller Voice Route.cmd`. It plays a
short bot-voice sentence into `CABLE Input` and records from `CABLE Output`.
The current route test passed with RMS `0.290495`, which confirms the bot voice
is present on the virtual microphone path. For echo-free routing, do not route
the caller/downlink audio into `CABLE Input`; only the bot voice should go there.

