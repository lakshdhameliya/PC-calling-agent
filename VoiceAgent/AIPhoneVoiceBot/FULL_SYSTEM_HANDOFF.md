# AI Phone FAQ Voice Bot — Complete Technical Handoff

Last verified: 2026-10-06, Asia/Calcutta  
Workspace: `C:\My - Voice Agent`  
Main project: `C:\My - Voice Agent\AIPhoneVoiceBot`

## 1. Purpose and current behavior

This Windows system handles a normal cellular phone call through a connected Android phone. It captures only the remote caller's downlink audio, recognizes the caller's English speech locally, matches the recognized sentence against a small local FAQ table, converts the fixed answer to Windows speech, and routes that speech back into the cellular call so the remote caller hears it.

The current active mode is deliberately an FAQ-only fast path. It does not call ChatGPT, OpenAI, a cloud STT service, or Qwen for answers. The local Qwen code remains available as an optional fallback mode, but the production launcher currently skips it.

Current high-level flow:

```text
Remote caller
  -> cellular call to Android phone
  -> Android voice-call-downlink captured by scrcpy over wireless ADB
  -> 48 kHz stereo raw PCM sent to PC
  -> PC converts it to 16 kHz mono
  -> adaptive energy VAD creates short speech segments
  -> local faster-whisper tiny.en converts speech to text
  -> confidence/noise/duplicate filters
  -> strict local FAQ match
  -> fixed answer text
  -> Windows SAPI creates a temporary WAV
  -> conditioned audio is played to CABLE Input
  -> CABLE Output acts as the Windows microphone
  -> Phone Link/Bluetooth call uses that microphone
  -> remote caller hears the bot
```

The caller audio and bot audio use separate paths. This separation is the reason the current call does not echo:

```text
Caller -> phone downlink -> ADB/scrcpy -> STT
Bot -> CABLE Input -> CABLE Output -> Phone Link microphone -> caller
```

Caller/downlink audio must never be mixed into `CABLE Input`. Only the bot voice goes into `CABLE Input`.

## 2. Current verified machine and phone

- Windows PC with Python `3.11.9`.
- Python virtual environment: `C:\My - Voice Agent\venv`.
- Android phone seen by scrcpy: Xiaomi Redmi `22087RA4DI`, Android 13.
- Current wireless ADB endpoint at the time of verification: `192.168.1.31:39059`.
- The wireless ADB IP/port is not permanent. It may change when Wireless debugging is re-paired or reconnected.
- scrcpy/ADB bundle: `C:\SMS - ADB\tools\scrcpy-win64-v4.1`.
- Phone Link process: `PhoneExperienceHost`.
- VB-Audio Virtual Cable endpoints:
  - Playback/render endpoint: `CABLE Input (VB-Audio Virtual Cable)`.
  - Recording/microphone endpoint: `CABLE Output (VB-Audio Virtual Cable)`.
- Windows speech synthesis: COM `SAPI.SpVoice`.
- Current FAQ STT model: `tiny.en`, stored locally under `AIPhoneVoiceBot\models`.
- Optional Qwen model: Ollama `qwen3.5:4b` at `http://127.0.0.1:11434/api/chat`.

## 3. Main files and responsibilities

### `call_qwen_voice_bot.py`

This is the main controller. Despite its historical name, the current launcher starts it with `--faq-only`, so Qwen is bypassed.

It contains:

- The ten FAQ questions, aliases, and fixed answers in `FAQS` near the top of the file.
- FAQ normalization and strict/fuzzy matching.
- Local Ollama/Qwen request code for optional non-FAQ mode.
- Adaptive speech segmentation.
- faster-whisper model loading and transcription.
- Confidence, background-noise, duplicate, and stale-queue filtering.
- Voice playback state tracking so bot output is not reprocessed as caller speech.
- UTF-8 call transcript logging.
- Atomic/thread-safe status-file updates.

### `audio_bridge.py`

This opens an independent audio-only scrcpy 4.1 server connection. It does not depend on the visible scrcpy screen-mirror session.

Important details:

- Uses `C:\SMS - ADB\tools\scrcpy-win64-v4.1\adb.exe`.
- Pushes `scrcpy-server` to `/data/local/tmp/call-transcriber-server.jar`.
- Starts scrcpy with `video=false`, `audio=true`, `control=false`, and `audio_codec=raw`.
- Default source is `voice-call-downlink`, intended to capture the other person only.
- Raw stream is 48 kHz, 16-bit, two-channel PCM.
- Uses a unique scrcpy connection ID and a temporary forwarded TCP port.
- Writes scrcpy diagnostics to `bridge.log`.
- Cleans up only its own ADB forwarding and server process.

### `cable_voice.py`

This routes SAPI speech to the virtual cable instead of the laptop speakers.

Important behavior:

- Finds a Windows output device containing `CABLE Input`.
- Prefers the WASAPI endpoint and excludes loopback endpoints.
- Maintains one persistent PyAudio/WASAPI output stream for the bot session.
- Reusing the stream is critical. The old design opened and closed PyAudio for each answer; the first answer worked, but later playback could stop or Windows could raise native access violation `0xC0000005`.
- Retries once after a playback/device error.
- Emits `output_ready`, `playing`, `played`, and `playback_stopped` events.
- Deletes temporary WAV files after playback.
- Automatically searches for and mutes a `CABLE Output` monitoring session on the `CABLE Input` render endpoint. This prevents the caller signal from feeding back into the bot-uplink route.

Phone-call audio conditioning currently does the following:

- Removes DC offset.
- Applies a short fade-in/fade-out of up to 8 ms.
- Peak-normalizes the speech.
- Targets RMS `0.045`.
- Uses output gain `0.50`.
- Applies a soft `tanh` limiter.
- Hard-limits the waveform to `-0.18 .. +0.18`.
- Converts output to 48 kHz, stereo, signed 16-bit PCM.

This conditioning removed the rough `ghad-ghad` distortion and avoids clipping.

### `sapi_wav.ps1`

This is a persistent PowerShell helper using `SAPI.SpVoice` and `SAPI.SpFileStream`.

- Receives JSON lines over stdin.
- `speak` generates a temporary WAV.
- Prints a JSON `wav` event containing the path.
- `stop` prints a stopped event.
- `close` exits.
- It does not execute model output as PowerShell code.

### `Start Call Qwen Voice Bot To Caller.cmd`

This is the current production launcher:

```bat
@echo off
cd /d "%~dp0"
..\venv\Scripts\python.exe call_qwen_voice_bot.py --voice-device "CABLE Input" --faq-only --stt-model tiny.en --threshold 0.003 --auto-call-state
pause
```

The active options mean:

- `--voice-device "CABLE Input"`: send answers to the virtual cable/caller.
- `--faq-only`: do not call Qwen; use fixed FAQ answers.
- `--stt-model tiny.en`: use the fast local English model.
- `--threshold 0.003`: base audio energy threshold for speech activation.
- `--auto-call-state`: remain in standby while idle, start capture only after Android reports an answered call, and stop that call session only after confirmed idle.

### `test_cable_route.py` and `Test Caller Voice Route.cmd`

The route test sends synthetic bot speech into `CABLE Input`, records it from `CABLE Output`, and reports:

- RMS level.
- Peak level.
- Clipping percentage.
- Number of completed playback events.
- `PASS` or `FAIL`.

The repeated-playback test passed `3/3`, with peak approximately `0.180023` and `0%` clipping.

### `Fast Qwen Chat\voice.py`

The call bot imports `SpeechChunks` and the standard `Voice` helper from this sibling project. `SpeechChunks` groups streamed Qwen text at sentence boundaries. In FAQ mode, fixed answers are sent directly and Qwen streaming is not used.

## 4. Current FAQ table

The canonical FAQ table is inside `call_qwen_voice_bot.py` in `FAQS`.

Current questions/aliases and responses:

1. `hello`, `hi`, `hey`
   - `Hello! How can I help you today?`
2. `how are you`, `how are you doing`
   - `I am doing well, thank you. How can I help you?`
3. `what is your name`, `whats your name`, `who are you`
   - `I am your local Qwen voice assistant.`
4. `what can you do`, `how can you help me`
   - `I can answer common questions quickly over this call.`
5. `what are your working hours`, `what time are you open`, `when are you open`
   - `We are available Monday to Saturday, from nine A M to six P M.`
6. `where are you located`, `what is your location`, `where is your office`
   - `Our test location is Ahmedabad, Gujarat.`
7. `how can i contact you`, `what is your contact number`, `how do i contact you`
   - `You can contact us on this phone number.`
8. `what is the price`, `how much does it cost`, `tell me the price`
   - `Pricing depends on the service. Our team will share the exact details.`
9. `can i book an appointment`, `book an appointment`, `i want an appointment`
   - `Yes. Please share your preferred date and time.`
10. `thank you`, `thanks`, `bye`, `goodbye`
    - `You are welcome. Have a nice day!`

To change an answer, edit only the answer string in `FAQS`. To improve recognition of a wording variation, add another alias to the tuple. Restart the bot after editing.

## 5. FAQ matching rules

Recognized text is converted to lowercase. Punctuation is removed and repeated whitespace is collapsed.

The matcher tries, in order:

1. Exact normalized alias match: score `1.00`.
2. Multiword alias contained inside a longer recognized sentence: score `0.96`.
3. Fuzzy comparison using both character-sequence similarity and token overlap.

The answer is allowed only when the final score is at least `0.84`.

When the score is below `0.84`, the bot does not speak. It records an `IGNORED: no confident FAQ match` line with the score. Silence is intentional here: it prevents background noise or a badly recognized sentence from producing a confident but wrong business answer.

Identical recognized questions inside four seconds are suppressed as duplicates.

## 6. Audio segmentation and background-noise rejection

The PC reads 100 ms of phone audio at a time.

Conversion:

- Input: 48 kHz stereo, signed 16-bit PCM.
- Stereo channels are averaged to mono.
- Every three 48 kHz samples are averaged to produce 16 kHz mono for Whisper.

The adaptive segmenter uses these current rules:

- Launcher base RMS threshold: `0.003`.
- Maintains an estimated background noise floor.
- Idle speech threshold: maximum of base threshold and noise floor multiplied by `3.2`.
- Active speech threshold: maximum of 80% of base threshold and noise floor multiplied by `2.2`.
- Keeps up to 300 ms of pre-roll.
- Requires two consecutive voiced 100 ms frames before opening a speech segment.
- Requires at least three voiced frames before emitting a segment.
- Rejects speech shorter than approximately 350 ms.
- Ends after 400 ms of silence.
- Forces a maximum segment length of approximately 3.5 seconds.

These rules reject short clicks, steady background noise, and many previous Whisper hallucinations such as repeated `Thank you` during silence.

## 7. STT configuration

Current FAQ model:

- Model: faster-whisper `tiny.en`.
- Device: CPU.
- Compute type: `int8`.
- CPU threads: up to 8 on this 12-logical-CPU machine.
- Language: English.
- Beam size: 1 for low latency.
- Temperature: 0.
- Previous-text conditioning: disabled.
- Internal Silero VAD: enabled.
- Minimum VAD speech: 250 ms.
- Internal VAD silence: 180 ms.
- Speech padding: 60 ms.
- No-speech threshold: `0.50`.
- Log-probability threshold: `-0.85`.
- Compression-ratio threshold: `2.2`.
- Hallucination silence threshold: `0.5`.
- FAQ keywords are supplied as Whisper hotwords.

After transcription, a segment is accepted only if:

- `no_speech_prob < 0.55`;
- `avg_logprob >= -0.85`;
- `compression_ratio < 2.2`.

Measured synthetic FAQ test:

- Spoken input: `What is your name?`
- Recognized text: `what is your name?`
- tiny.en STT time: approximately `0.974` seconds.
- The old `small` configuration was observed taking approximately `2.96` seconds for a 0.6-second captured segment on this PC.

The tiny model is faster and English-only. The strict FAQ matcher compensates for some small-model transcription errors. This exact configuration is not intended for open-ended multilingual dictation.

## 8. Queue and latency behavior

The STT worker is single-threaded because parallel Whisper decoding on this CPU can create contention and out-of-order answers.

The audio job queue has a small maximum size. Before a new speech segment is queued, older waiting audio segments are removed. The segment currently being decoded is allowed to finish, and the newest pending caller question is retained. This avoids the earlier behavior where answers became 6, 14, and 18 seconds late as old noise chunks accumulated.

Status field `dropped_stale_chunks` records when old waiting chunks were discarded.

Expected latency is approximately:

```text
speech-end detection (~0.4 s)
+ tiny.en STT (~1–2 s on this PC and phone-call audio)
+ FAQ matching (effectively immediate)
+ SAPI WAV generation/playback startup
```

Actual cellular, Bluetooth, Windows, and Phone Link buffering can add more time.

## 9. Bot-voice self-protection

`cable_voice.py` reports the actual start and completion of each playback. The controller marks that interval as a bot-voice window and ignores caller-audio segments overlapping it, with a short 350 ms tail.

This prevents the bot from interpreting its own answer and entering an automatic conversation loop. It also means the current system is half-duplex: the caller should wait for the bot answer to finish before speaking the next question. Speech spoken over the bot may be intentionally ignored.

## 10. Echo and distortion fix

The clean route depends on all of the following:

1. Only bot speech is sent to `CABLE Input`.
2. Phone Link uses `CABLE Output` as its microphone path.
3. Caller/downlink audio comes directly from ADB/scrcpy and is not sent into `CABLE Input`.
4. The Windows audio monitoring session that sends `CABLE Output` back into `CABLE Input` is muted.
5. Bot audio is amplitude-conditioned and limited before entering the cable.
6. One persistent WASAPI stream is reused for all answers.

The feedback bug was directly measured before the fix:

- CABLE idle RMS was about `0.2157`.
- CABLE idle peak was about `0.9679`.
- A Windows session named `CABLE Output (VB-Audio Virtual Cable)` was active on the `CABLE Input` render endpoint.

After muting that feedback-monitor session:

- CABLE idle RMS was approximately `0.0000146`.
- CABLE idle peak was approximately `0.0000305`.
- Repeated playback peak was approximately `0.180023`.
- Clipping was `0%`.

If echo returns, inspect the Windows `Listen to this device`/monitoring path and the pycaw session mute before changing STT or FAQ code.

## 11. Status, logs, and diagnostics

### Main status

`qwen_call_status.json` is updated throughout operation. Useful fields include:

- `state`: loading, ready, listening, stt, qwen, error, stopped.
- `mode`: `faq_only` or `qwen`.
- `serial`: selected ADB device.
- `voice`: ready, playing, or error.
- `voice_device`: actual selected Windows device.
- `rms`: recent captured audio level.
- `pending`: audio jobs waiting.
- `audio_seconds`: length of latest speech segment.
- `stt_seconds`: latest STT processing time.
- `question`: recognized caller text.
- `answer`: selected answer.
- `faq_matched`: true or false.
- `faq_score`: match confidence.
- `faq_alias`: matched canonical phrase.
- `skipped`: why audio was rejected.
- `dropped_stale_chunks`: old waiting segments discarded.

Status writes use a lock, a unique temporary filename containing process/thread IDs, atomic `os.replace`, and retry logic. Status-file contention must never stop the call bot. This fixed an earlier Windows file-lock crash.

### Call transcripts

Directory: `qwen_call_logs`.

Each bot process creates one UTF-8 text file. FAQ mode lines look like:

```text
[16:15:29] Caller: what is your name?
[16:15:31] FAQ: I am your local Qwen voice assistant.
```

Rejected speech looks like:

```text
[16:16:04] Caller: can i book a look?
[16:16:05] IGNORED: no confident FAQ match (0.67)
```

### Process logs

- `bot_start_stdout.log`: caller text and answers printed by the bot.
- `bot_start_stderr.log`: fatal/runtime errors.
- `bridge.log`: scrcpy phone-audio diagnostics.

### Cable route test

Run:

```powershell
cd "C:\My - Voice Agent\AIPhoneVoiceBot"
..\venv\Scripts\python.exe test_cable_route.py
```

Healthy output requires detectable RMS, peak below clipping, clipping percentage `0`, and `PASS`.

## 12. Call-state lifecycle and phone-audio recovery

An earlier build exited after a call when the scrcpy phone-audio socket raised `timed out`. That was the reason a first call could work while a later call produced no answer.

The current build does not exit on this error. The main run loop now:

1. Closes the stopped scrcpy audio session.
2. Keeps the loaded STT model, FAQ state, worker thread, and voice process alive.
3. Reports `state: reconnecting_phone_audio` and the exact `audio_error`.
4. Retries with a short bounded backoff up to five seconds.
5. Returns to `state: listening` after a fresh phone-audio stream is established.

`audio_bridge.py` also converts the raw socket exception into the explicit message `Phone audio stream timed out`. A temporary phone-audio interruption must not kill the whole bot.

The production launcher also enables Android call-state control. The long-running service loads STT and voice once, then uses `dumpsys telephony.registry`:

- `0`: idle; service waits and does not capture call audio.
- `1`: ringing; status becomes `ringing_waiting_answer`.
- `2`: answered/off-hook; the call session and phone-audio capture start.

Two consecutive idle observations are required before the active call session stops. This avoids a temporary state flicker stopping the bot during a real call. If audio times out while state remains off-hook, only the audio bridge reconnects. When the call is cut and idle is confirmed, queued speech and bot playback are stopped and the service returns to `waiting_for_call` for the next call. The main process remains alive.

## 13. Normal run procedure on this PC

1. Enable Wireless debugging on the Android phone.
2. Confirm the phone is connected/authorized through ADB.
3. Confirm Phone Link shows the phone connected through Bluetooth.
4. Confirm Windows microphone/input used by Phone Link is `CABLE Output (VB-Audio Virtual Cable)`.
5. Double-click `Start Call Qwen Voice Bot To Caller.cmd` and leave the service running.
6. While no call exists, expect `state: waiting_for_call`. When a call rings expect `ringing_waiting_answer`; after answering expect `listening`, `voice: ready`, and `mode: faq_only`.
7. From another phone, call the connected Android phone.
8. Receive/handle the call through Phone Link on the PC so the PC virtual microphone is the call-uplink path.
9. Ask one FAQ question.
10. Wait until the bot finishes speaking before asking the next question.
11. After the call, inspect the newest file in `qwen_call_logs`.
12. If the phone audio stops after a call, allow the automatic reconnect loop to return status to `listening` before the next call.

## 14. Recreating the same system on another Windows PC

### Required software

1. Windows 10/11 with Phone Link.
2. Python 3.11.x; this system uses Python 3.11.9.
3. VB-Audio Virtual Cable installed; reboot if its installer requests it.
4. scrcpy 4.1 with ADB.
5. An Android phone whose firmware exposes `voice-call-downlink` to scrcpy.
6. Bluetooth/Phone Link pairing capable of handling calls on the PC.
7. Optional only for non-FAQ mode: Ollama with `qwen3.5:4b`.

### Copy the project

Copy at least:

```text
AIPhoneVoiceBot\
  audio_bridge.py
  cable_voice.py
  call_qwen_voice_bot.py
  sapi_wav.ps1
  test_cable_route.py
  Start Call Qwen Voice Bot To Caller.cmd
  Test Caller Voice Route.cmd
Fast Qwen Chat\
  voice.py
```

Copying the whole `AIPhoneVoiceBot` and `Fast Qwen Chat` folders is safer.

### Create the Python environment

From `C:\My - Voice Agent`:

```powershell
py -3.11 -m venv venv
.\venv\Scripts\python.exe -m pip install --upgrade pip
.\venv\Scripts\python.exe -m pip install faster-whisper==1.2.1 PyAudioWPatch==0.2.12.8 numpy==2.4.6 pycaw==20260927 comtypes==1.4.17
```

The current `requirements.txt` contains faster-whisper, PyAudioWPatch, and NumPy. `pycaw` and `comtypes` are also required for the automatic feedback-session mute and should be installed explicitly.

### Install/download the local tiny English model

Run once with internet access:

```powershell
.\venv\Scripts\python.exe -c "from faster_whisper import WhisperModel; WhisperModel('tiny.en', device='cpu', compute_type='int8', cpu_threads=8, download_root=r'C:\My - Voice Agent\AIPhoneVoiceBot\models')"
```

After the model is stored under `AIPhoneVoiceBot\models`, normal FAQ operation is offline.

### Install and configure scrcpy/ADB

Install scrcpy 4.1 at:

```text
C:\SMS - ADB\tools\scrcpy-win64-v4.1
```

If it is installed elsewhere, edit `SCRCPY` in `audio_bridge.py`.

Enable Android Developer options and Wireless debugging. Pair and connect using the ADB executable from the scrcpy folder. Typical commands are:

```powershell
& "C:\SMS - ADB\tools\scrcpy-win64-v4.1\adb.exe" pair PHONE_IP:PAIR_PORT
& "C:\SMS - ADB\tools\scrcpy-win64-v4.1\adb.exe" connect PHONE_IP:CONNECT_PORT
& "C:\SMS - ADB\tools\scrcpy-win64-v4.1\adb.exe" devices
```

`adb devices` must show exactly the intended phone with state `device`, not `unauthorized` or `offline`.

### Configure the Windows call microphone

1. Install VB-CABLE.
2. Open Windows Settings -> System -> Sound.
3. In Input, select `CABLE Output (VB-Audio Virtual Cable)`.
4. Apply it for normal and communications input roles when available.
5. Pair the Android phone with Phone Link/Bluetooth.
6. Ensure calls can be answered on the PC.
7. Do not enable a route that monitors `CABLE Output` back into `CABLE Input`.
8. Do not route the caller/downlink audio to `CABLE Input`.

### Validate before a real call

1. Run `Test Caller Voice Route.cmd`.
2. Require `PASS`, zero clipping, and a peak safely below `0.95`.
3. Start the production launcher.
4. Confirm status shows `listening`, `voice: ready`, and `mode: faq_only`.
5. Make a real cellular call and test `What is your name?`.

### Optional local Qwen mode

To enable open-ended local Qwen answers:

1. Install Ollama.
2. Run `ollama pull qwen3.5:4b`.
3. Ensure Ollama serves `127.0.0.1:11434`.
4. Start `call_qwen_voice_bot.py` without `--faq-only`.

Example:

```powershell
..\venv\Scripts\python.exe call_qwen_voice_bot.py --voice-device "CABLE Input" --stt-model tiny.en --threshold 0.003 --auto-call-state
```

This enables Qwen response generation and adds model latency. The current tested fast behavior is FAQ-only mode.

## 15. Troubleshooting map

### Caller hears their own voice/echo

Likely cause: caller/downlink audio or the `CABLE Output` monitoring session is being routed into `CABLE Input`.

Checks:

- Keep the caller path only in ADB/scrcpy.
- Keep the bot path only in `CABLE Input`.
- Disable Windows `Listen to this device` for the cable microphone.
- Inspect whether the automatic pycaw mute ran; status may include `Muted CABLE feedback monitor`.

### Caller hears rough `ghad-ghad` noise

Likely cause: feedback or clipping.

- Run `Test Caller Voice Route.cmd`.
- Check peak and clipping percentage.
- Confirm the conditioner still limits to `0.18`.
- Confirm only one bot process is running.

### First bot answer works, later answers are silent

Checks:

- Confirm the current persistent-stream version of `cable_voice.py` is present.
- Inspect `bot_start_stderr.log`.
- Inspect `qwen_call_status.json` for `voice:error`.
- Do not revert to opening/terminating PyAudio for every answer.
- Check whether status is temporarily `reconnecting_phone_audio`; it should recover to `listening` automatically.

### Caller speech appears in logs but caller hears no answer

STT/FAQ is working; the fault is after answer selection.

- Check `voice` and `last_voice_played` in status.
- Run the cable route test.
- Confirm Phone Link is using `CABLE Output` as its microphone.
- Confirm the call is handled through the PC/Phone Link.

### Nothing appears in caller logs

The fault is before STT.

- Check wireless ADB authorization.
- Check `bridge.log`.
- Confirm the phone supports `voice-call-downlink` capture.
- Confirm the bot process is running and status is `listening`.

### Wrong FAQ answer

Open the newest call log and inspect the exact `Caller:` text and `faq_score` in status.

- If caller text is wrong, tune STT/VAD or add a valid alias only when appropriate.
- If caller text is correct but no match occurs, add that wording to the matching FAQ tuple.
- Do not lower the global `0.84` threshold aggressively; that increases wrong answers from noise.

### Bot remains silent for an unknown phrase

This is current intended behavior. A match below `0.84` is ignored to prevent a wrong answer.

### Answers become progressively late

- Inspect `stt_seconds`, `pending`, and `dropped_stale_chunks`.
- Confirm the launcher uses `tiny.en`, not `small`.
- Confirm no second Whisper/bot process is consuming CPU.
- Keep the newest-segment queue policy.

### Second call does not work

Inspect status and stderr. The current build automatically reconnects after a phone-audio timeout. If status remains `reconnecting_phone_audio`, verify Wireless debugging, the current ADB connection, and `bridge.log`. Restart only if ADB itself is unavailable or recovery remains unsuccessful.

### Phone is connected but the serial changed

Run the bundled ADB `devices` command. The bot normally selects the connected authorized device automatically. Avoid connecting multiple phones unless `--serial` is supplied explicitly.

## 16. Safety and privacy properties

- Current FAQ STT and FAQ matching run locally.
- Current answers are fixed local strings.
- No OpenAI/ChatGPT API is used in the active flow.
- No API key is required.
- Qwen is also local when optional Qwen mode is used.
- Normal operation processes call audio in memory and saves text logs, not full call recordings.
- Call transcripts remain on disk until manually deleted.
- Recording/transcribing calls may require consent depending on jurisdiction; deployment responsibility belongs to the operator.

## 17. Exact current package versions

Verified in the active virtual environment:

```text
Python            3.11.9
faster-whisper    1.2.1
ctranslate2       4.8.2
numpy             2.4.6
PyAudioWPatch     0.2.12.8
pycaw             20260927
comtypes          1.4.17
```

## 18. Handoff instructions for another AI or technician

Treat this document and the current source files as the source of truth. Preserve these verified design choices unless a measured failure requires changing them:

- Keep caller capture on `voice-call-downlink` through scrcpy/ADB.
- Keep bot output exclusively on `CABLE Input`.
- Keep Phone Link call input on `CABLE Output`.
- Preserve feedback-session muting and audio limiting.
- Preserve the persistent PyAudio output stream.
- Preserve thread-safe status writes.
- Preserve adaptive VAD, confidence filters, strict FAQ threshold, duplicate suppression, stale-queue dropping, and bot-playback ignore windows.
- Measure the exact failure stage before editing: capture, segmentation, STT, FAQ, TTS, CABLE playback, or Phone Link uplink.
- Inspect logs/status before guessing.
- After every audio-route change, run the cable route test and verify zero clipping.
- After every STT change, verify recognized caller text and latency using a real call.
- Preserve automatic recovery after `PhoneAudio`/scrcpy timeout so multiple calls can be handled without restarting the process.
- Preserve Android call-state gating: start capture on answered/off-hook, require confirmed idle before ending the call session, and keep the service alive between calls.

This is the complete state of the system as verified on 2026-10-06.
