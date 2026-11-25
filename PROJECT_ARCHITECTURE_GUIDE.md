# Frontbit Link + AI Voice Agent - Master Architecture & System Guide

> **Target Audience**: Human Developers & AI Coding Agents.  
> **Purpose**: This single document gives any AI or developer 100% complete understanding of the entire repository, codebase architecture, hardware wiring, audio pipeline, auto-answer mechanism, and how to run or migrate it on any PC.

---

## 1. High-Level System Architecture

The project is an autonomous, free, 100% offline-capable **AI Phone Voice Receptionist** integrated with **Frontbit Link** (an open-source Windows Phone Link desktop client based on Uno Platform / WinUI 3 in C#).

```
                      +---------------------------------------+
                      |       Android Mobile Phone            |
                      | (Any Brand: OPPO, Redmi, Samsung, etc)|
                      +-------------------+-------------------+
                                          |
        +---------------------------------+---------------------------------+
        | (1) Wi-Fi Local Network         | (2) 3.5mm Analog Audio Cable    |
        | - Frontbit Link Protocol (TCP)  | - Phone Headphone -> PC Mic In  |
        | - Android Wireless ADB (TCP)    | - PC Speaker Out -> Phone Mic In|
        v                                 v                                 v
+-----------------------+      +-----------------------+      +-----------------------+
|  Frontbit Link (C#)   |      |   PC Realtek Audio    |      |  AI Voice Bot (Python)|
|  Sefirah.exe (.NET 10)|<====>|  Line In / Line Out   |<====>|  phone_link_no_adb_...|
|  - Call Window UI     |      |  - Speakers (Realtek) |      |  - Whisper STT (Local)|
|  - Wireless ADB Ctrl  |      |  - Mic (Realtek)      |      |  - Mithi v2 TTS (Hi)  |
|  - Telephony Events   |      +-----------------------+      |  - BestZone FAQ Match |
+-----------+-----------+                                     +-----------+-----------+
            |                                                             |
            +--------------> call_state.json (IPC) <----------------------+
```

### Key Pillars:
1. **Zero Cloud API Costs / 100% Free**: No Twilio, no OpenAI API keys, no ElevenLabs fees.
2. **Pure 3.5mm Wired HD Audio (Zero Bluetooth)**: Bluetooth Hands-Free Profile (HFP/HSP) causes 300–800ms lag, metallic mono sound, and random drops. Using a direct 3.5mm audio route provides 0ms delay and crystal-clear voice clarity.
3. **Universal Android Wireless ADB**: Works across all Android 11+ smartphones without vendor lock-in. Uses standard ADB keyevents (`keyevent 5` to answer, `keyevent 6` to hang up).
4. **Local Neural Voice**: Uses `Oomni/mithi-v2-flash` for realistic Indian Hindi male voice (`anirban`), with Microsoft Edge-TTS as fallback.

---

## 2. Directory Structure & Key Files

```
c:\Frontbit Link\
├── 1 - START EVERYTHING (Frontbit Link + Voice Bot).cmd  <-- 1-Click Master Launcher
├── Sefirah.exe                                         <-- Frontbit Link compiled WinUI3 executable
├── Sefirah.dll                                         <-- Main application assembly
├── PROJECT_ARCHITECTURE_GUIDE.md                       <-- This comprehensive guide
├── platform-tools\
│   └── adb.exe                                         <-- Android Debug Bridge executable
├── models\
│   └── mithi-v2-flash\                                 <-- Offline Hindi Neural TTS weights
├── src\
│   └── Sefirah\                                        <-- C# .NET 10 WinUI 3 Desktop App
│       ├── Features\
│       │   └── CallFeature.cs                          <-- Incoming/active call controller & state sync
│       ├── Data\Models\
│       │   ├── WirelessAdbCallController.cs            <-- Wireless ADB pairing & keyevent dispatcher
│       │   └── RemotePhoneCall.cs                      <-- Phone call abstraction (Accept/End/Mute)
│       ├── Views\
│       │   ├── WindowViews\CallWindow.xaml / .cs       <-- Floating floating incoming/active call popup
│       │   ├── Settings\DeviceDiscoveryPage.xaml / .cs <-- "Pair Wireless ADB" UI with 1-Click connect
│       │   └── MessagesPage.xaml                       <-- Default startup page
│       └── UserControls\
│           └── CallView.xaml                           <-- Call action buttons (Accept, End, Mute, Hold)
└── VoiceAgent\
    ├── smart_launcher.ps1                              <-- Portable auto-installer & startup orchestrator
    ├── venv\                                           <-- Isolated Python 3.11 environment
    └── AIPhoneVoiceBot_NoADB_POC\                      <-- Core Python Voice Agent
        ├── phone_link_no_adb_bot.py                    <-- Main bot loop: STT, VAD, state machine, chat writer
        ├── cable_voice.py                              <-- Audio player (Mithi v2 TTS & Realtek line-out)
        ├── frontbit_auto_answer.py                     <-- UI Automation & ADB watchdog helper
        ├── live_chat_viewer.py                         <-- Tkinter GUI window showing live dialogue bubbles
        ├── faq_voice_start_page.py                     <-- Web dashboard on port 8765
        ├── bestzone_catalog_matcher.py                 <-- Product search & semantic question answering
        ├── product_data.py                             <-- BestZone catalog database
        ├── call_state.json                             <-- IPC file: Sefirah C# -> Python bot
        ├── live_chat.json                              <-- Real-time chat history displayed in viewer
        └── status.json                                 <-- Bot health & hardware telemetry
```

---

## 3. Hardware & Audio Cable Configuration

```
      PC Back/Front Panel                           Android Smartphone
+------------------------------+             +-------------------------------+
| Pink Jack: Mic / Line In     |<============| 3.5mm Headphone Jack (Audio)  |
| (Receives Caller's Voice)    |  Cable #1   | (Phone Call Ear-speaker Out)  |
+------------------------------+             +-------------------------------+
| Green Jack: Speaker Out      |============>| 3.5mm Mic Input (Mic)         |
| (Plays Bot's Reply Voice)    |  Cable #2   | (Phone Call Microphone In)    |
+------------------------------+             +-------------------------------+
```

### Cabling Options:
- **Option A (Audio Splitter Cable)**: A single 3.5mm TRRS male plug into the phone, splitting into:
  - Green (Headphone) -> PC Mic In (Pink jack).
  - Pink (Mic) -> PC Speaker Out (Green jack).
- **Option B (Separate USB-C / 3.5mm DAC)**: For phones without a 3.5mm jack, use a standard Type-C to 3.5mm converter.

### Audio Pipeline & Gain Calibration:
1. **Line-Out Volume (`cable_voice.py`)**:
   - `PEAK_TARGET = 0.85` (scaled to 85% peak amplitude).
   - This prevents distortion while ensuring the caller hears the bot loud and clear on any mobile network.
2. **Caller Microphone Input (`phone_link_no_adb_bot.py`)**:
   - Captured via `pyaudiowpatch` from `Microphone (Realtek(R) Audio)`.
   - Windows recording volume scalar is auto-pinned to `1.0` (100%).
   - Digital boost: `samples = np.clip(samples * 2.2, -1.0, 1.0)`.
   - VAD Sensitivity: `DIRECT_HINDI_VAD_THRESHOLD = 0.015`, `DIRECT_HINDI_MIN_RMS = 0.012`.
3. **Echo Suppression (Zero Interference)**:
   - `call_active` gating: When idle or ringing, the VAD ignores line noise.
   - Acoustic tail suppression: While the bot speaks and for 1.2s post-speech, the incoming audio buffer is zeroed out to prevent the bot from hearing its own voice.

---

## 4. Wireless ADB Call Control System

Bluetooth HFP is completely bypassed for call handling. Instead, Android's native input event subsystem handles answering and ending calls.

### Commands Used:
| Action | Shell Command | Notes |
| :--- | :--- | :--- |
| **Answer Call** | `input keyevent 224; input keyevent 5; input tap 554 1238; input swipe 554 1238 554 800 250; input keyevent 5` | Wakes screen, triggers KEYCODE_CALL, touches/swipes common lockscreen sliders. |
| **Hangup Call** | `cmd telecom end-call; input keyevent 6` | Universally terminates active call on any Android skin. |
| **Call State** | `dumpsys telephony.registry` | Scans for `mCallState`: `0` = Idle, `1` = Ringing, `2` = Talking/Offhook. |

### Pairing Flow:
1. In Frontbit Link -> Settings -> Add Device -> click **"Pair Wireless ADB"**.
2. On Android Phone -> Settings -> Developer Options -> enable **Wireless Debugging**.
3. Choose **"Pair device with pairing code"** on phone.
4. Enter the IP:Port and 6-digit code into Frontbit Link and click **Pair & Connect**.
5. Once paired, the phone auto-reconnects over Wi-Fi whenever on the same network.

---

## 5. Frontbit Link (C#) Call Lifecycle & IPC

### 1. Incoming Call:
1. Android sends a socket notification to Sefirah (`CallState.Ringing`).
2. `CallFeature.HandleCallInfoAsync()` runs:
   - Writes `{"state":"ringing", "phoneNumber":"..."}` to `call_state.json`.
   - Creates `CallSessionViewModel` and shows the floating `CallWindow` popup at the bottom-right.
   - Invokes `ScheduleAutoAnswer()` -> retries calling `AcceptIncomingCallAsync()` via Wireless ADB.

### 2. Active Call (Talking):
1. Android confirms call answered (`CallState.InProgress`).
2. Sefirah writes `{"state":"talking", ...}` to `call_state.json`.
3. Python `phone_call_audio_active()` detects state `talking` -> triggers `begin_call()`.
4. Bot plays the greeting (`"नमस्ते, बेस्टज़ોન મેં આપકા સ્વાગત હૈ..."`).
5. Live conversation begins.

### 3. Call Ended:
1. When caller hangs up on phone, Android sends `CallState.Ended` or ADB reports `mCallState=0`.
2. Sefirah writes `{"state":"idle", ...}` to `call_state.json` and closes `ActiveCallWindow`.
3. Python bot triggers `end_call()`, stops audio output, and resets live chat.

---

## 6. How to Deploy on a Brand-New PC

### Prerequisites on New PC:
1. Windows 10 or 11 (64-bit).
2. Direct 3.5mm audio splitter / cables connected between PC and Phone.
3. Phone connected to the same Wi-Fi router.

### Step-by-Step Setup:
1. **Copy the Folder**:
   - Copy the entire `Frontbit Link` folder to `C:\Frontbit Link` (or any drive).
2. **Double-click `1 - START EVERYTHING (Frontbit Link + Voice Bot).cmd`**:
   - If Python or AI models are missing, `smart_launcher.ps1` opens an **animated GUI progress window** showing live download speed (MB/s) and percentage.
   - It will automatically:
     - Download and install official Python 3.11 silently.
     - Create the virtual environment in `VoiceAgent\venv`.
     - Install all required Python packages (`pip install -r requirements.txt`).
     - Download Faster-Whisper and Mithi v2 Flash TTS neural weights.
     - Create Desktop and Start Menu shortcuts.
     - Launch Frontbit Link desktop app, dashboard, and the AI Voice Bot.
3. **If Models & Venv are already copied**:
   - The launcher detects all components and **starts in under 1 second with 0 downloads!**

---

## 7. Troubleshooting Cheatsheet for Future AI Agents

| Symptom | Root Cause | Solution |
| :--- | :--- | :--- |
| **`adb: more than one device/emulator`** | Multiple devices or mDNS TLS endpoints online in `adb devices`. | Always pass `-s <serial>` in every ADB command. |
| **Caller speech not appearing in Live Chat** | Stale duplicate Python process locking the microphone WASAPI stream. | Kill all python processes (`Get-Process python \| Stop-Process -Force`) and restart. |
| **Bot speech is too quiet** | Pre-amplification peak target was low. | Keep `data = (data / peak) * 0.85` in `cable_voice.py`. |
| **Call popup closes immediately after ringing** | Watchdog in `CallFeature.cs` mistaking ringing state for idle. | Do NOT re-introduce `StartActiveCallWatchdog()`. Rely on authoritative `CallState.Ended`. |
| **Hardcoded path failure on other PCs** | Hardcoded `C:\Frontbit Link` used instead of relative paths. | Use `AppContext.BaseDirectory` in C# and `Path(__file__).resolve().parent` in Python. |

---
*Created by Google Antigravity Agent for Frontbit Link & AI Voice Agent.*
