import os
import sys
import time
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ADB = r"C:\Users\DREAMWORLD\Desktop\platform-tools\adb.exe"
CALLER = "192.168.29.209:37865"
BOT = "192.168.29.165:44551"
os.environ["ANDROID_USER_HOME"] = r"c:\Frontbit Link\.android"

def adb(dev, *args):
    cmd = [ADB, "-s", dev] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8").stdout.strip()

def get_call_state(dev):
    out = adb(dev, "shell", "dumpsys", "telephony.registry")
    states = [int(line.split("=")[1].strip()) for line in out.splitlines() if "mCallState=" in line]
    return max(states) if states else 0

def main():
    print("=== 1. Starting LakshVoiceAgent Bot ===")
    python_exe = str(ROOT.parent / "venv" / "Scripts" / "python.exe")
    bot_proc = subprocess.Popen([python_exe, "-u", "phone_link_no_adb_bot.py", "--fast-start"],
                                cwd=str(ROOT), creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))

    time.sleep(5.0)

    status_file = ROOT / "status.json"
    status = json.loads(status_file.read_text(encoding="utf-8"))
    print(f"Bot ready: {status.get('ready')}, state: {status.get('state')}")

    chat_file = ROOT / "live_chat.json"
    chat = json.loads(chat_file.read_text(encoding="utf-8"))
    print(f"Live chat events before call: {len(chat.get('events', []))}")

    print("\n=== 2. Dialing from Caller Phone via CALL intent ===")
    adb(CALLER, "shell", "am", "start", "-a", "android.intent.action.CALL", "-d", "tel:9081819676")

    ringing_detected = False
    answered = False
    t_ring = None
    for i in range(25):
        s_bot = get_call_state(BOT)
        s_caller = get_call_state(CALLER)
        print(f"[{i+1}s] Caller state: {s_caller} | Bot state: {s_bot}")
        if s_bot == 1 and not ringing_detected:
            ringing_detected = True
            t_ring = time.time()
            print(">>> Bot phone is RINGING! Waiting for auto-answer... <<<")
        if s_bot == 2:
            ring_duration = round(time.time() - t_ring, 2) if t_ring else 0
            print(f">>> Call ANSWERED! (Ringing lasted: {ring_duration}s) <<<")
            answered = True
            break
        time.sleep(1.0)

    if not answered:
        print("[FAIL] Call was not answered.")
    else:
        print("\n=== 3. Call Connected! Monitoring stability for 15s ===")
        for sec in range(15):
            time.sleep(1.0)
            s_bot = get_call_state(BOT)
            s_caller = get_call_state(CALLER)
            status = json.loads(status_file.read_text(encoding="utf-8"))
            print(f"In-Call [{sec+1}s/15s]: Caller={s_caller}, BotState={s_bot}, BotCallActive={status.get('call_active')}")
            if s_bot != 2 and s_caller != 2:
                print(f"[FAIL] Call dropped prematurely at {sec+1}s!")
                break
        else:
            print("[SUCCESS] Call stayed connected continuously for 15s without dropping!")

        time.sleep(1.0)
        chat = json.loads(chat_file.read_text(encoding="utf-8"))
        print("\n=== Live Chat Content: ===")
        for ev in chat.get("events", []):
            print(f"[{ev.get('time')}] {ev.get('kind')}: {ev.get('text')} (how: {ev.get('how')})")

    print("\n=== 4. Hanging up ===")
    adb(CALLER, "shell", "input", "keyevent", "6")
    adb(BOT, "shell", "input", "keyevent", "6")
    time.sleep(2.0)
    bot_proc.terminate()
    try:
        bot_proc.wait(timeout=3.0)
    except Exception:
        bot_proc.kill()
    print("Test Completed!")

if __name__ == "__main__":
    main()
