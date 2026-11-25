# AI MASTER HANDOFF & SYSTEM ARCHITECTURE
## Frontbit Link Plus AI Voice Bot (All-in-One Autonomous Phone Agent)

> **યુઝર માટે સૂચના (INSTRUCTION FOR USER):**
> જ્યારે તમે આ આખું ફોલ્ડર (`C:\Frontbit Link`) કોઈ નવા કમ્પ્યુટરમાં મૂકો અને ત્યાં કોઈ પણ નવી AI (Antigravity, Claude, ChatGPT, Cursor, વગેરે) સાથે વાત કરો, ત્યારે તમારે માત્ર નીચે આપેલો **<PROJECT_CONTEXT_HANDOFF>** બોક્સનો પ્રોમ્પ્ટ તે AI ને આપી દેવાનો છે. તે AI આખી સિસ્ટમ, આર્કિટેક્ચર, કોડ અને સેટિંગ્સ ૧ સેકન્ડમાં સમજી જશે!

---

```markdown
<PROJECT_CONTEXT_HANDOFF>
You are an expert AI software engineer and system architect working on the "Frontbit Link Plus AI Voice Bot" project.
The root workspace directory is strictly: `C:\Frontbit Link` on a 64-bit Windows operating system.
Language requirement: ALWAYS communicate with the user in GUJARATI (ગુજરાતી).

================================================================================
1. CORE MISSION & VALUE PROPOSITION
================================================================================
This system is an autonomous, 100% FREE, production-grade Telephonic AI Voice Agent.
It connects a physical Android smartphone (OPPO Reno8 5G / any Android device) to a Windows PC via Frontbit Link (`Sefirah.exe`), automatically receives incoming cellular calls within 1.5 - 3.0 seconds, transcribes caller speech in real time (Hindi, Gujarati, English), understands customer intent using semantic embeddings, matches questions against an offline business FAQ catalog (`bestzoneFAQ.csv`), and speaks back replies in high-definition natural human voice using local neural TTS (Mithi v2 Flash). When the call ends, it cleanly dismisses the call window and resets to idle.

================================================================================
2. NON-NEGOTIABLE CORE CONSTRAINTS
================================================================================
1. STRICTLY 100% FREE & ZERO API KEYS:
   - Zero paid cloud subscriptions (No ElevenLabs, no OpenAI API, no Sarvam AI, no Twilio).
   - Primary STT: Google Speech Recognition (`speech_recognition`, hi-IN) - Fast, accurate, free.
   - Backup STT: Faster-Whisper (`large-v3-turbo` / `small` on CUDA/CPU).
   - Primary TTS: Mithi v2 Flash local neural TTS (`models\mithi-v2-flash`, voice: `anirban`).
   - Backup TTS: Microsoft Edge-TTS (`hi-IN-SwaraNeural`, `hi-IN-MadhurNeural`, `gu-IN-DhwaniNeural`).
   - Agent Semantic Brain: Multilingual-E5-Small (`intfloat/multilingual-e5-small`) + `bestzone_faq.py`.
2. ZERO BLUETOOTH FOR VOICE (100% DIRECT WIRED HD AUDIO):
   - Phone 3.5mm/Type-C Audio Cable <-> Realtek PC Audio (Mic In & Speaker Out).
   - Absolutely NO Bluetooth Hands-Free Profile (HFP) or Bluetooth audio degradation.
3. LANGUAGE:
   - All conversations with the user MUST be in clear, polite, and technical GUJARATI.

================================================================================
3. SYSTEM ARCHITECTURE & DATA FLOW
================================================================================
Incoming Call Flow:
[Customer Calls Phone]
        │
        ▼
[OPPO Reno8 5G Phone Rings]
        │
        ├── Socket Port 5150 Notification ──> [Sefirah Desktop C# (C:\Frontbit Link\Sefirah.exe)]
        │                                              │
        │                                              ├─ In-process C# Auto-Answer (1.5s):
        │                                              │  PrimaryCall.AcceptCallCommand.ExecuteAsync()
        │                                              │  -> Sends {Action: "accept"} to phone port 5150
        │                                              │
        │                                              └─ Writes {"state":"ringing"} to call_state.json
        │
        ├── OPPO Hardware Auto-Answer (3.0s Fallback):
        │   ColorOS "Auto answer when connected to external audio device" = ON (3s).
        │   Wired cable detected as external headset -> Phone answers natively!
        │
        ▼
[Call Connected / Talking State]
        │
        ├── Sefirah detects CallState.InProgress -> Sets IsIncoming = false -> Starts duration timer (0:01...)
        │   Writes {"state":"talking"} to C:\Frontbit Link\VoiceAgent\AIPhoneVoiceBot_NoADB_POC\call_state.json
        │
        ▼
[Voice Bot Orchestrator (phone_link_no_adb_bot.py)]
        │
        ├─ Detects state: "talking" via call_state.json
        ├─ Plays Welcome Greeting via Mithi v2 Flash TTS directly into PC Speaker Out -> Phone Mic In
        │
        ├─ Listens to Caller Voice from PC Mic In (Phone Headphone Out)
        ├─ STT Engine (Google Speech Recognition hi-IN) transcribes caller query
        │  (Silences Whisper static noise / hallucinations)
        ├─ Live Call Chat Monitor (Port 8765 / Tkinter Viewer) displays conversation in real time
        │
        ├─ FAQ Engine (bestzone_faq.py) matches intent against bestzoneFAQ.csv
        │
        └─ TTS Engine (Mithi v2 Flash / Edge-TTS) synthesizes audio answer and speaks to caller.

Call Hangup Flow:
[Caller or Bot Ends Call]
        │
        ├── Phone sends CallState.Ended / Disconnected to Sefirah port 5150
        │   (OR PC user / bot clicks "End", sending {Action: "end"} to Android telecomManager.endCall())
        │
        ▼
[Sefirah CallFeature.cs]
        │
        ├─ EndAllCalls() / OnSessionEnded() executes
        ├─ ActiveCallWindow?.Close(); ActiveCallWindow = null;
        └─ Writes {"state":"idle"} to call_state.json
        │
        ▼
[Voice Bot resets cleanly to "Waiting for incoming calls..."]

================================================================================
4. KEY CODEBASE & FILE LOCATIONS (`C:\Frontbit Link\`)
================================================================================
- `1 - START EVERYTHING (Frontbit Link + Voice Bot).cmd`: Master 1-click launcher.
- `Sefirah.exe`: Compiled native Windows WinUI 3 Desktop App (.NET 10 x64, self-contained).
- `src\Sefirah\`: C# source code for Sefirah.
  - `Features\CallFeature.cs`: In-process auto-answer timer (1.5s), call state dispatcher, window lifecycle.
  - `ViewModels\CallSessionViewModel.cs`: Call UI state machine (IsIncoming button visibility, duration timer, Accept/End commands).
  - `Data\Models\RemotePhoneCall.cs`: Socket RPC commands (accept, decline, end) sent to Android port 5150.
  - `UserControls\CallView.xaml`: UI layout for CallWindow (Accept green button, End red button, hold, mute).
- `VoiceAgent\`:
  - `smart_launcher.ps1`: Intelligent auto-installer and zero-touch runner.
    - If on a brand-new PC: opens dark GUI installer with progress bar (% and MB/s), automatically downloads official Python 3.11.9, installs requirements, sets up isolated venv, verifies models, and creates Desktop/Start Menu shortcuts.
    - If environment is already ready: launches everything instantly in < 1 second.
  - `venv\`: Dedicated virtual environment with all required AI audio packages.
  - `AIPhoneVoiceBot\requirements.txt`: Python package dependencies.
  - `AIPhoneVoiceBot_NoADB_POC\`:
    - `phone_link_no_adb_bot.py`: Main bot orchestrator (wired audio capture, Google STT, Mithi TTS, call loop).
    - `bestzone_faq.py`: Semantic FAQ matching engine.
    - `bestzoneFAQ.csv`: Business knowledge base (pricing, delivery, orders, returns, support).
    - `call_state.json`: High-speed IPC state file between native Sefirah C# and Python bot.
    - `live_chat.json` & `live_chat_viewer.py`: Real-time chat monitor UI.
    - `faq_voice_start_page.py`: Web dashboard on port 8765.
- `models\`:
  - `models\mithi-v2-flash\`: High-speed local neural Hindi TTS weights.
- `platform-tools\`: Android Debug Bridge (`adb.exe`) if USB/Wi-Fi ADB debugging is ever needed.
- `Frontbit_Link_Android_Companion.apk`: Android companion app.

================================================================================
5. MOBILE DEVICE CONFIGURATION (OPPO Reno8 5G)
================================================================================
For 100% robust, zero-bug automatic call answering:
1. Android Companion App Permission:
   - Phone Settings -> Apps -> App management -> Frontbit Link -> Permissions.
   - Set **Phone** permission to **Allow** (Grants `android.permission.ANSWER_PHONE_CALLS`).
2. Native ColorOS Hardware Auto-Answer (Crucial Bulletproof Fallback):
   - Phone App -> Settings (3 dots) -> Calling accounts -> Answer/End calls.
   - Turn **Auto answer calls when connected to an external audio device** = **ON (3 Seconds)**.
   - Because the 3.5mm/Type-C audio cable is plugged in, ColorOS treats it as an external audio device and answers automatically in 3s!

================================================================================
6. RUNNING ON A NEW PC
================================================================================
To run this entire project on any new Windows computer:
1. Copy the entire folder `C:\Frontbit Link` onto the new PC at `C:\Frontbit Link`.
2. Connect the physical audio cable:
   - Phone Headphone Out -> PC Mic In (Pink port).
   - PC Speaker Out (Green port) -> Phone Mic In (via CTIA splitter).
3. Connect the phone and new PC to the SAME Wi-Fi network.
4. Double-click `1 - START EVERYTHING (Frontbit Link + Voice Bot).cmd`.
   - The smart launcher will automatically set up Python and dependencies if not already present.
   - Pair the phone once in Frontbit Link Desktop app.
   - System is 100% LIVE and operational!

You now have the full context, code layout, architecture, and behavioral guidelines.
</PROJECT_CONTEXT_HANDOFF>
```
