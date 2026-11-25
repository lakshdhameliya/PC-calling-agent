# Frontbit Link Calling: Agent Handoff

## Objective

Make calls to the Android phone visible in Frontbit Link on Windows, enable answering from the PC, and use PC audio/microphone where Windows Bluetooth supports it.

## Current machine and devices

- Workspace: `C:\Frontbit Link`
- Windows: 10.0.26300.9974
- Frontbit Windows package family: `shrimqy.Seki-PhoneLink_9yhjgvpvzzxz2`
- Installed Windows app: **3.2.0.9**, status `Ok`
- Main / linked Android phone: OPPO A6 5G, ADB `192.168.29.165:42085`, number `9081819676`
- Source Android phone used to call it: ADB `192.168.29.209:41479`
- Android package: `com.frontbit.link`
- ADB executable: `C:\Users\DREAMWORLD\Desktop\platform-tools\adb.exe`
- Windows Bluetooth adapter: Cambridge Silicon Radio `USB\VID_0A12&PID_0001`; status `OK`
- Paired Bluetooth services are present and `OK`:
  - `Headset Audio Gateway Service`
  - `OPPO A6 5G Hands-Free HF Audio`
  - `OPPO A6 5G A2DP SNK`

## What was verified live

1. The linked OPPO is connected to Frontbit Link over Wi-Fi.
2. A call started from the source phone to `9081819676` reaches the OPPO.
3. Android `CallStateFeature` sends `Ringing`, `InProgress`, and `MissedCall` events to Windows.
4. Frontbit Windows log records these events, for example:
   - `Received Android call-state event (Ringing) from OPPO A6 5G; HFP status=NotLinked.`
   - `Received Android call-state event (InProgress) from OPPO A6 5G; HFP status=NotLinked.`
5. When the call was answered on the phone through ADB (`KEYCODE_CALL`), Android telecom recorded:
   - `AUDIO_ROUTE_BT`
   - `SET_ANSWERED`
   - `SET_ACTIVE`
6. The Windows `PhoneLineTransportDevice.ConnectAsync()` path repeatedly returns `false`, even after retrying pairing and after stopping `PhoneExperienceHost` (Phone Link background host). Consequently `PhoneLineService.LineStatus` is `NotLinked` and Windows' native dialer/accept API cannot be used.

## Important root cause

Windows reports the Bluetooth bond as connected, but its phone-line transport API refuses to connect the HFP transport. This is why the Calls view can show **Pair mobile device and PC** while the Bluetooth quick panel and device manager show OPPO connected.

The failing service ID has consistently been:

```text
\\?\BTHENUM#{0000111f-0000-1000-8000-00805f9b34fb}_VID&000100e0_PID&0000#7&34311fb5&0&3C32B9921B14_C00000000#{bd41df2d-addd-4fc9-a194-b9881d2a2efa}\service
```

The Windows log is at:

```text
C:\Users\DREAMWORLD\AppData\Local\Packages\shrimqy.Seki-PhoneLink_9yhjgvpvzzxz2\LocalState\Logs\Log_20261120.log
```

## Implemented Windows fixes

### Bluetooth discovery and pairing resilience

- `BluetoothRadioManager` now falls back from `Radio.GetRadiosAsync()` to `BluetoothAdapter.GetDefaultAsync()` / `GetRadioAsync()`.
- Call watchers start even when the first radio enumeration is empty.
- Call state refresh re-runs radio discovery rather than treating the first miss as permanent.
- Existing Windows Bluetooth pairings are found by phone name/model and reused. This avoids asking Android to become discoverable again when it is already paired.
- After reusing a pairing, the phone-line state is refreshed immediately.

Relevant files:

- `src/Sefirah/Platforms/Windows/Bluetooth/BluetoothRadioManager.cs`
- `src/Sefirah/Platforms/Windows/Bluetooth/BluetoothPairingService.cs`
- `src/Sefirah/Platforms/Windows/Calling/PhoneLineService.cs`
- `src/Sefirah/ViewModels/Dialogs/BluetoothSetupViewModel.cs`

### Direct Android call-control fallback (new, not yet installed/tested on Android)

Native Windows HFP transport is unreliable on this machine, so a fallback was implemented:

1. On incoming Android `Ringing`, if Windows phone-line status is not `Ready`, Frontbit shows a Windows toast with **Accept** and **Decline** buttons.
2. Clicking a button sends `CallControlRequest { Action = "accept" | "decline" }` through the existing encrypted Frontbit connection to the Android companion.
3. Android calls `TelecomManager.acceptRingingCall()` or `endCall()`.
4. Android requests `ANSWER_PHONE_CALLS` permission.

Changed files for this fallback:

- `src/Sefirah/Data/Models/SocketMessage.cs`
- `src/Sefirah/Data/Contracts/IPlatformNotificationHandler.cs`
- `src/Sefirah/Constants.cs`
- `src/Sefirah/Features/CallFeature.cs`
- `src/Sefirah/Platforms/Windows/Services/NotificationHandler.cs`
- `src/Sefirah/Platforms/Desktop/Services/NotificationHandler.cs`
- `android-upstream/domain/src/main/java/sefirah/domain/model/SocketMessage.kt`
- `android-upstream/domain/src/main/java/sefirah/domain/util/MessageSerializer.kt`
- `android-upstream/core/network/src/main/java/sefirah/network/NetworkService.kt`
- `android-upstream/core/network/src/main/java/sefirah/network/extensions/MessageHandler.kt`
- `android-upstream/features/src/main/java/sefirah/communication/call/CallControlHandler.kt` (new)
- `android-upstream/app/src/main/AndroidManifest.xml`

## Build status

### Windows

- Version bumped to **3.2.0.9** in `src/Sefirah/Package.appxmanifest`.
- MSIX build, signing, verification, and installation succeeded.
- Bundle:

```text
C:\Frontbit Link\src\Sefirah\AppPackages\Sefirah_3.2.0.9_Debug_Test\Sefirah_3.2.0.9_x64_Debug.msixbundle
```

### Android

- `:app:assembleDebug` succeeded after using Android Studio's JDK.
- APK expected at:

```text
C:\Frontbit Link\android-upstream\app\build\outputs\apk\debug\app-debug.apk
```

- The new Android APK has **not yet been installed** on `192.168.29.165:42085`.

## Next steps for the next agent

1. Confirm the APK exists:

```powershell
Get-Item 'C:\Frontbit Link\android-upstream\app\build\outputs\apk\debug\app-debug.apk'
```

2. Install it over the linked OPPO and grant the permission:

```powershell
$adb = 'C:\Users\DREAMWORLD\Desktop\platform-tools\adb.exe'
& $adb -s 192.168.29.165:42085 install -r 'C:\Frontbit Link\android-upstream\app\build\outputs\apk\debug\app-debug.apk'
& $adb -s 192.168.29.165:42085 shell pm grant com.frontbit.link android.permission.ANSWER_PHONE_CALLS
& $adb -s 192.168.29.165:42085 shell pm grant com.frontbit.link android.permission.READ_PHONE_STATE
```

3. Relaunch/confirm Frontbit Link 3.2.0.9 is connected to OPPO.

4. Start a call from the source phone:

```powershell
& $adb -s 192.168.29.209:41479 shell am start -a android.intent.action.CALL -d tel:9081819676
```

5. On the Windows notification toast, click **Accept**. Verify:

```powershell
& $adb -s 192.168.29.165:42085 shell dumpsys telecom
```

Expected signs: `REQUEST_ACCEPT`, `SET_ANSWERED`, then `SET_ACTIVE`.

6. Inspect Windows app log for:

```text
Sent remote call action accept to OPPO A6 5G.
```

7. Then test **Decline** with another incoming call and verify Android telecom shows a disconnect.

## Remaining limitation to investigate after control fallback works

The fallback enables answer/decline from the PC, but PC microphone and speaker audio still depend on Windows HFP/SCO routing. During active phone calls Android reports `AUDIO_ROUTE_BT`, yet Windows does not expose an OPPO endpoint in `Get-PnpDevice -Class AudioEndpoint -PresentOnly`.

Inspect this after accepting a call:

```powershell
Get-PnpDevice -Class MEDIA -PresentOnly | Where-Object {$_.FriendlyName -match 'OPPO|Hands-Free'} | Format-Table Status,FriendlyName,InstanceId -Wrap
Get-PnpDevice -Class AudioEndpoint -PresentOnly | Format-Table Status,FriendlyName
```

Do not claim PC audio is working until both playback and microphone are verified by a person on the active call.

## Useful build commands

### Windows

```powershell
dotnet publish src/Sefirah/Sefirah.csproj -f net10.0-windows10.0.26100 -c Debug -r win-x64 -p:Platform=x64 -p:GenerateAppxPackageOnBuild=true -p:AppxBundle=Always -p:UapAppxPackageBuildMode=CI -p:AppxPackageSigningEnabled=false --no-restore
```

### Android

```powershell
$env:JAVA_HOME = 'C:\Program Files\Android\Android Studio\jbr'
$env:Path = "$env:JAVA_HOME\bin;$env:Path"
.\gradlew.bat :app:assembleDebug
```

## Existing warnings / non-blocking items

- Android `local.properties` contains an invalid `sdk.dir`, but Gradle found the SDK at `C:\Users\DREAMWORLD\AppData\Local\Android\Sdk` and built successfully.
- Windows build has existing nullable/XAML/resource warnings; no build errors.
- `PhoneExperienceHost` was stopped for one diagnostic attempt; it did not change `ConnectAsync=false`.
- The system has a generic CSR Bluetooth USB adapter using the Microsoft driver `10.0.26100.9974`.
