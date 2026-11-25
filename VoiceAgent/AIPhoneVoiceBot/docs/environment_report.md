# AIPhoneVoiceBot Environment Report

Generated: 2026-10-01
Step: STEP 0 - Environment inspection

## Goal

Know the exact target machine before writing product code.

## Why Now

This project depends on real GSM/SIM call audio moving between an Android phone and Windows. The environment must be documented before requirements freeze and before the critical two-way audio feasibility gate.

## Scope

This step only inspected the Windows PC, installed tools, audio/Bluetooth devices, and current workspace. No Android app, Python service, AI pipeline, dashboard, or audio bridge was built.

## Files Created Or Changed

- Created `AIPhoneVoiceBot/docs/environment_report.md`

## System Summary

| Item | Result |
| --- | --- |
| OS product | Windows 10 Home Single Language |
| OS/kernel version observed | 10.0.26200.0 from .NET/ADB; PowerShell build table reports 10.0.26100.9444 |
| PowerShell | 5.1.26100.9444, Desktop edition |
| UI culture | en-US |
| Physical memory | 16,541,872,128 bytes, about 15.4 GiB |
| Available memory during inspection | 4,364,967,936 bytes, about 4.1 GiB |
| System drive | `C:\`, NTFS |
| Disk total | 510,273,064,960 bytes, about 475.1 GiB |
| Disk free | 48,218,415,104 bytes, about 44.9 GiB |

Notes:
- Some WMI/CIM system queries returned `Access denied` in this sandbox, so the report uses lower-permission .NET and direct executable checks where possible.
- Disk and memory are enough for early inspection and audio proof-of-concept work. Large AI/STT/TTS models should not be installed blindly because only about 44.9 GiB was free at inspection time.

## Python And Virtual Environment

| Item | Result |
| --- | --- |
| Active Python | `C:\My - Voice Agent\venv\Scripts\python.exe` |
| Python version | 3.11.9 |
| pip | 24.0 from `C:\My - Voice Agent\venv\Lib\site-packages\pip` |
| venv | Present at `C:\My - Voice Agent\venv` |
| venv base Python | `C:\Users\laksh\AppData\Local\Programs\Python\Python311\python.exe` |
| include-system-site-packages | false |
| installed packages | `pip 24.0`, `setuptools 65.5.0` only |

Python is ready for a small Phase 2 audio test utility, but audio packages are not installed yet.

## Java, Android Studio, SDK, And ADB

| Item | Result |
| --- | --- |
| `java` on PATH | Not found |
| Android Studio | Found at `C:\Program Files\Android\Android Studio\bin\studio64.exe` |
| Android Studio product version | `261.26222.65.0-AI` |
| Android Studio bundled Java | Found at `C:\Program Files\Android\Android Studio\jbr\bin\java.exe` |
| Bundled Java version | OpenJDK 25.0.3, 64-bit |
| Android SDK | Found at `C:\Users\laksh\AppData\Local\Android\Sdk` |
| ADB on PATH | Not found |
| ADB direct path | `C:\Users\laksh\AppData\Local\Android\Sdk\platform-tools\adb.exe` |
| ADB version | 1.0.41, version `37.0.1-15733141` |
| Installed Android platforms | `android-34`, `android-35`, `android-36`, `android-37.0` |
| SDK folders present | `build-tools`, `cmake`, `cmdline-tools`, `emulator`, `extras`, `licenses`, `ndk`, `platform-tools`, `platforms`, `sources` |

Notes:
- Android development tooling is installed.
- Java and ADB are not currently on PATH, but direct paths work.
- A future Android build step should use Android Studio/Gradle paths explicitly or add SDK tools to PATH in a controlled setup step.

## Chrome

| Item | Result |
| --- | --- |
| Chrome executable | Found at `C:\Program Files\Google\Chrome\Application\chrome.exe` |
| Chrome version | 154.0.8037.58 |

Chrome is present for the future local dashboard at `http://127.0.0.1:8080`.

## Bluetooth

Bluetooth device enumeration succeeded with `pnputil`.

Key Bluetooth adapter/device findings:

| Device | Status / Driver |
| --- | --- |
| MediaTek Bluetooth Adapter | Started, driver `oem64.inf` |
| OPPO Reno8 5G | Started |
| OPPO Reno8 5G Hands-Free HF Audio | Started, driver `microsoft_bluetooth_hfp.inf` |
| OPPO Reno8 5G A2DP SNK | Started |
| Redmi Note 11 SE | Started |
| Redmi Note 11 SE Hands-Free HF Audio | Started, driver `microsoft_bluetooth_hfp.inf` |
| vivo 1915 | Started |
| vivo 1915 Hands-Free HF Audio | Started, driver `microsoft_bluetooth_hfp.inf` |
| AB-BT-CM01 Hands-Free | Started, driver `microsoft_bluetooth_hfp.inf` |
| Airpods500 Hands-Free | Started, driver `microsoft_bluetooth_hfp.inf` |
| Airdopes 161 Probuds Hands-Free | Started, driver `microsoft_bluetooth_hfp.inf` |
| Wireless 3 Hands-Free | Started, driver `microsoft_bluetooth_hfp.inf` |

Important observation:
- Windows already exposes several Bluetooth Hands-Free/HFP audio devices. This is promising for Phase 2, but it does not prove live GSM call audio works. The exact phone and route must still be tested manually.

## Audio Devices

Audio endpoint enumeration succeeded with `pnputil`.

Detected endpoints include:

| Endpoint | Direction marker from MMDevice ID | Status |
| --- | --- | --- |
| Speakers (Realtek(R) Audio) | `0.0.0` | Started |
| 2 - 22B1W (AMD High Definition Audio Device) | `0.0.0` | Started |
| Stereo Mix (Realtek(R) Audio) | `0.0.1` | Started |
| CABLE Input (VB-Audio Virtual Cable) | `0.0.0` | Started |
| CABLE Output (VB-Audio Virtual Cable) | `0.0.1` | Started |
| CABLE In 16ch (VB-Audio Virtual Cable) | `0.0.0` | Started |
| Voicemeeter Input | `0.0.0` | Started |
| Voicemeeter AUX Input | `0.0.0` | Started |
| Voicemeeter VAIO3 Input | `0.0.0` | Started |
| Voicemeeter In 1/2/3/4/5 | `0.0.0` | Started |
| Voicemeeter Out A1/A2/A3/A4/A5 | `0.0.1` | Started |
| Voicemeeter Out B1/B2/B3 | `0.0.1` | Started |

VB-Audio driver/device findings:

| Item | Result |
| --- | --- |
| `C:\Program Files\VB\CABLE` | Present |
| `C:\Program Files (x86)\VB\Voicemeeter` | Present |
| VB-Audio Virtual Cable | One device started; one root media instance has problem code 10 |
| VB-Audio Voicemeeter VAIO | Started |

Important observation:
- VB-Cable and Voicemeeter are installed and visible.
- One `VB-Audio Virtual Cable` media instance reports `Problem Code: 10`, while endpoints still appear as started. Phase 2 should verify actual capture/playback rather than trusting friendly names.

## Phone Link / Existing Automation

| Item | Result |
| --- | --- |
| Phone Link / YourPhone AppX package | Not found by `Get-AppxPackage *YourPhone*` or `Get-AppxPackage *Phone*` |
| Existing non-venv workspace files | None found |
| Existing phone/audio automation scripts in workspace | None found |

The current workspace only contained the existing `venv` before this step created `AIPhoneVoiceBot/docs/environment_report.md`.

## Existing Workspace Structure

Before STEP 0 file creation:

```text
C:\My - Voice Agent\
  venv\
```

After STEP 0 file creation:

```text
C:\My - Voice Agent\
  AIPhoneVoiceBot\
    docs\
      environment_report.md
  venv\
```

No Git repository is initialized in `C:\My - Voice Agent`.

## Commands Run

Representative commands used during inspection:

```powershell
Get-ComputerInfo | Select-Object WindowsProductName,WindowsVersion,OsBuildNumber,OsArchitecture,CsTotalPhysicalMemory
python --version
pip --version
Get-ChildItem -Force
java -version
where.exe java
where.exe adb
where.exe python
systeminfo
Get-PSDrive -PSProvider FileSystem
$PSVersionTable
Get-Command python,pip,java,adb,studio64,chrome -ErrorAction SilentlyContinue
[System.IO.DriveInfo]::GetDrives()
Test-Path 'C:\Program Files\Android\Android Studio\bin\studio64.exe'
Test-Path 'C:\Users\laksh\AppData\Local\Android\Sdk\platform-tools\adb.exe'
Test-Path 'C:\Program Files\Google\Chrome\Application\chrome.exe'
& 'C:\Users\laksh\AppData\Local\Android\Sdk\platform-tools\adb.exe' version
& 'C:\Program Files\Android\Android Studio\jbr\bin\java.exe' -version
pnputil /enum-devices /class Bluetooth
pnputil /enum-devices /class AudioEndpoint
pnputil /enum-devices /class Media
rg --files -g '!venv/**'
rg -i "phone link|phonelink|android|bluetooth|voicemeeter|vb-cable|vbcable|hfp|call|audio" -g '!venv/**'
Get-AppxPackage *YourPhone*
Get-AppxPackage *Phone*
pip list
git status --short
```

Commands that returned access denied:

```powershell
Get-CimInstance Win32_OperatingSystem
Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3"
systeminfo
```

## User Verification Needed

Please verify:

- The target Windows PC is this machine.
- The target Android phone for the first feasibility test should be named explicitly. Candidate paired phones visible here include `OPPO Reno8 5G`, `Redmi Note 11 SE`, and `vivo 1915`.
- Confirm whether the intended call-audio route should start with Bluetooth HFP using one of the detected phone hands-free endpoints.
- Confirm whether VB-Cable/Voicemeeter is intentionally installed and can be used during Phase 2.
- Confirm whether it is acceptable that Java/ADB are not on PATH as long as direct paths are used.
- Confirm whether Phone Link is expected; it was not detected as an installed AppX package by this inspection.

## Pass Criteria For STEP 0

STEP 0 is considered passable if:

- The report matches the PC.
- The Android/Windows development environment is sufficiently identified.
- The likely audio/Bluetooth surfaces are documented.
- No hidden dependency is assumed.

## Known Limitations

- No real call-audio test was performed in this step.
- No Android device connection over ADB was tested in this step.
- No audio capture/playback was tested in this step.
- Some OS details could not be collected through WMI/CIM due access restrictions.
- Bluetooth/HFP presence does not prove GSM call audio routing. It only means Windows exposes relevant device classes to test in Phase 2.

## Next Step Preview

After you verify this report and say `OK`, the next step is STEP 1 / Phase 1: create `docs/requirements.md` and freeze V1 requirements before any implementation work.
