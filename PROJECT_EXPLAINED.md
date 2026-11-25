# FRONTBIT LINK PLUS AI VOICE BOT - COMPLETE PROJECT GUIDE
> **AI INSTRUCTION:** When directed to read this file, absorb all architecture, constraints, directory layout, and operational logic below. Strictly communicate with the user in **GUJARATI (ગુજરાતી)**.

---

## 1. PROJECT OVERVIEW
- **Project Name:** Frontbit Link Plus AI Voice Bot
- **Root Directory:** `C:\Frontbit Link`
- **OS:** Windows 10/11 (64-bit)
- **Primary Mission:** An autonomous, 100% free, production-ready telephonic customer care voice agent. It connects a physical Android smartphone (OPPO Reno8 5G) to a Windows PC via Frontbit Link (`Sefirah.exe`), answers incoming calls automatically in 1.5 to 3 seconds, speaks to callers in high-definition natural human voice (Hindi/Gujarati), answers customer questions using an offline FAQ database (`bestzoneFAQ.csv`), and automatically hangs up / resets when the call ends.

---

## 2. STRICT RULES & AI MODELS
1. **Always communicate with the user in GUJARATI (ગુજરાતી).**
2. **The 3 AI Models Used in This System:**
| Component | Model Name / Library | Description |
| :--- | :--- | :--- |
| **TTS (બોલવું)** | **Mithi v2 Flash** (`models\mithi-v2-flash`, voice: `anirban` / `malini`) | સ્થાનિક હાઇ-સ્પીડ નેચરલ હ્યુમન વૉઇસ (Fallback: Edge-TTS) |
| **STT (સાંભળવું)** | **Google Speech Recognition** + **Faster-Whisper large-v3-turbo** | કૉલરનો અવાજ ટેક્સ્ટમાં કન્વર્ટ કરે છે (CUDA/Offline) |
| **Agent (સમજવું / Brain)** | **Multilingual-E5-Small** (`intfloat/multilingual-e5-small`) | સવાલનો અર્થ (Semantic Meaning) સમજી FAQ માંથી સાચો જવાબ શોધે છે |

3. **100% Free & Zero API Keys:**
   - No paid cloud services (no ElevenLabs, no OpenAI, no Sarvam AI, no Twilio).
   - STT: Google Speech Recognition (`speech_recognition`, hi-IN) primary + Faster-Whisper fallback.
   - TTS: Mithi v2 Flash local neural TTS primary + Edge-TTS fallback.
   - Knowledge Base: Offline semantic matching using Multilingual-E5-Small + `bestzoneFAQ.csv`.
4. **100% Direct Wired HD Audio (Zero Delay & Zero Echo):**
   - Wired Audio Route: Phone 3.5mm/Type-C Audio Splitter <-> PC Realtek Audio (Pink Mic In & Green Speaker Out).
   - Zero Bluetooth for voice (Bluetooth HFP has terrible audio compression and delay; wired cable ensures crystal clear studio voice).

---

## 3. HOW THE ENTIRE CALL WORKFLOW OPERATES

### A. Incoming Call & Auto-Receive (0% Bug Solution):
1. Customer calls the Android phone.
2. The phone sends a socket event to PC on Port 5150 and triggers Android telephony.
3. **ADB Direct Auto-Answer (Primary - 0% Bug Solution):**
   - Both Sefirah (`WirelessAdbCallController.cs`) and Python Voice Bot (`frontbit_auto_answer.py`) immediately send `adb shell input keyevent 224` (wake up screen) and `adb shell input keyevent 5` (KEYCODE_CALL) directly to Android OS.
   - Answering occurs in 0.1s directly at the OS level, bypassing Android 13/14 permission restrictions completely.
4. **Android Hardware & UI Fallback:**
   - Sefirah UI invokes visible `Accept` button if ADB is not yet paired.
   - In ColorOS Settings: `Auto answer calls when connected to an external audio device` acts as an additional safety net.
5. **Call Connected (`Talking`):**
   - Phone transitions to in-call (`CallState.InProgress`).
   - Sefirah receives event, sets `IsIncoming = false`, starts the duration timer (`0:01`, `0:02`...).
   - Sefirah writes `{"state":"talking"}` to `call_state.json`.
   - Voice Bot (`phone_link_no_adb_bot.py`) detects `talking`, plays greeting audio via Mithi TTS into the call, and begins listening.

### B. Conversation Cycle:
1. Caller speaks into phone -> audio travels via wired cable to PC Mic In (`Realtek(R) Audio`).
2. Google Speech Recognition transcribes caller query instantly into text.
3. Semantic matcher (`bestzone_faq.py`) searches `bestzoneFAQ.csv` for the most accurate answer.
4. Mithi v2 Flash TTS synthesizes speech and plays it out through PC Speaker Out -> into Phone Mic In.
5. All live dialogue is displayed in real time on the Live Call Chat Monitor (Port 8765 / Tkinter Viewer).

### C. Call Cut / Hangup:
1. When caller ends call (or bot / user clicks 'End' on PC):
   - Phone sends `CallState.Ended` to PC Sefirah.
   - PC immediately sends `adb shell cmd telecom end-call; input keyevent 6` to hang up instantly with 0 delay.
   - `CallFeature.cs` triggers `EndAllCalls()`.
   - Floating `CallWindow` is cleanly closed (`ActiveCallWindow?.Close()`).
   - `call_state.json` is set to `{"state":"idle"}`.
   - Voice bot cleanly returns to `Bot is LIVE! Waiting for incoming calls...`.

### D. Simple 2-Step Phone Connection Setup:
- **Step 1 (Frontbit Link Link)**: Open Frontbit Link on PC -> click `Scan QR Code` -> scan from Frontbit Link Android App.
- **Step 2 (Wireless ADB Pairing)**: Click `Pair Wireless ADB` (right next to `Scan QR Code`) -> on phone go to *Developer Options > Wireless Debugging > Pair device with QR code* -> scan the ADB QR code (or enter the 6-digit pairing code).
- Once connected, both Frontbit Link and ADB show green connected indicators and all incoming calls are 100% handled via ADB.

---

## 4. PHONE SETTINGS (OPPO Reno8 5G)
1. **Frontbit Link App:** Phone Settings -> Apps -> App management -> Frontbit Link -> Permissions -> **Phone: Allow**.
2. **ColorOS Auto-Answer:** Phone App -> Settings (3 dots) -> Calling accounts -> Answer/End calls -> **Auto answer calls when connected to an external audio device: ON (3 Seconds)**.

---

## 5. FILE & FOLDER STRUCTURE (`C:\Frontbit Link\`)
```
C:\Frontbit Link\
├── 1 - START EVERYTHING (Frontbit Link + Voice Bot).cmd  <-- 1-CLICK MASTER LAUNCHER
├── PROJECT_EXPLAINED.md                                  <-- THIS MASTER GUIDE FILE
├── Sefirah.exe                                          <-- Native WinUI 3 Phone Link App (.NET 10 x64)
├── Frontbit_Link_Android_Companion.apk                  <-- Android companion app
├── models\
│   └── mithi-v2-flash\                                  <-- Local Hindi neural TTS weights
├── platform-tools\                                      <-- adb.exe
├── src\Sefirah\                                         <-- C# Source code of desktop app
│   ├── Features\CallFeature.cs                          <-- Native 1.5s auto-answer & state dispatcher
│   ├── ViewModels\CallSessionViewModel.cs               <-- Call state machine & buttons
│   ├── Data\Models\RemotePhoneCall.cs                   <-- Socket RPC (accept, end) to port 5150
│   └── UserControls\CallView.xaml                       <-- CallWindow UI
└── VoiceAgent\
    ├── smart_launcher.ps1                               <-- Auto-installer & smart launcher
    ├── venv\                                            <-- Isolated Python virtual environment
    ├── AIPhoneVoiceBot\
    │   └── requirements.txt                             <-- Python package dependencies
    └── AIPhoneVoiceBot_NoADB_POC\
        ├── phone_link_no_adb_bot.py                     <-- Main Voice Bot orchestrator
        ├── bestzone_faq.py                              <-- Semantic FAQ search
        ├── bestzoneFAQ.csv                              <-- Business FAQ catalog
        ├── call_state.json                              <-- IPC bridge between Sefirah C# & Python
        ├── live_chat_viewer.py                          <-- Live desktop chat viewer
        └── faq_voice_start_page.py                      <-- Web dashboard on port 8765
```

---

## 6. HOW THIS RUNS ON A NEW PC (ZERO-TOUCH SETUP)
1. Copy the entire `C:\Frontbit Link` folder to the new PC at `C:\Frontbit Link`.
2. Connect the physical audio cable (Mic In & Speaker Out).
3. Connect the phone and PC to the same Wi-Fi.
4. Double-click `1 - START EVERYTHING (Frontbit Link + Voice Bot).cmd`.
   - `smart_launcher.ps1` detects if Python exists.
   - If Python is missing, it displays a GUI progress bar and automatically downloads official Python 3.11, configures PATH, creates venv, and installs requirements with 0 user intervention.
   - It then automatically creates Start Menu & Desktop shortcuts and launches the entire system!
