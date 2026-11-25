"""Test dialing 199 on OPPO A6 5G and checking audio on PC loopback."""
import os
import subprocess
import sys
import time
import wave
from pathlib import Path
import numpy as np
import pyaudiowpatch as pa
import speech_recognition as sr

ADB = r"C:\Users\DREAMWORLD\Desktop\platform-tools\adb.exe"
DEVICE = "192.168.29.165:42085"
os.environ["ANDROID_USER_HOME"] = r"c:\Frontbit Link\.android"

def run_adb(*args):
    cmd = [ADB, "-s", DEVICE] + list(args)
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    return res.stdout.strip()

def find_loopback():
    engine = pa.PyAudio()
    try:
        for idx in range(engine.get_device_count()):
            info = engine.get_device_info_by_index(idx)
            name = info.get("name", "")
            if "realtek" in name.lower() and "loopback" in name.lower():
                return idx, name
        for idx in range(engine.get_device_count()):
            info = engine.get_device_info_by_index(idx)
            if info.get("isLoopbackDevice"):
                return idx, info.get("name")
    finally:
        engine.terminate()
    raise RuntimeError("No loopback device found")

def main():
    print("=== Step 1: Checking Loopback Device on PC ===")
    idx, name = find_loopback()
    print(f"Recording device: {idx} - {name}")

    print("\n=== Step 2: Placing Outgoing Call to 199 ===")
    dial_out = run_adb("shell", "am", "start", "-a", "android.intent.action.CALL", "-d", "tel:199")
    print(f"Dial command output: {dial_out}")

    print("\n=== Step 3: Monitoring Call and Recording Audio for 15 seconds ===")
    engine = pa.PyAudio()
    stream = engine.open(
        format=pa.paInt16,
        channels=2,
        rate=48000,
        input=True,
        input_device_index=idx,
        frames_per_buffer=2048
    )

    recorded_chunks = []
    r = sr.Recognizer()
    deadline = time.time() + 15
    route_checked = False

    try:
        while time.time() < deadline:
            data = stream.read(2048, exception_on_overflow=False)
            recorded_chunks.append(data)

            if not route_checked and time.time() > deadline - 10:
                telecom = run_adb("shell", "dumpsys", "telecom")
                for line in telecom.splitlines():
                    if "Active" in line or "mCallAudioRoute" in line or "ROUTE_" in line or "Connecting" in line:
                        print(f"[Phone Telecom Route]: {line.strip()}")
                route_checked = True

    finally:
        stream.stop_stream()
        stream.close()
        engine.terminate()
        print("\n=== Step 4: Ending Call ===")
        end_out = run_adb("shell", "input", "keyevent", "KEYCODE_ENDCALL")
        print(f"End call key sent: {end_out}")

    all_bytes = b"".join(recorded_chunks)
    samples = np.frombuffer(all_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    rms = float(np.sqrt(np.mean(samples ** 2)))
    peak = float(np.max(np.abs(samples)))
    print(f"\nPC Loopback Audio Total: {len(all_bytes)} bytes")
    print(f"PC Loopback Audio RMS: {rms:.6f}")
    print(f"PC Loopback Audio Peak: {peak:.6f}")

    # Check if there was actual audio on PC
    if peak > 0.01:
        print("\n=== Step 5: Transcribing PC Loopback Audio with Google STT ===")
        audio_data = sr.AudioData(all_bytes, 48000, 2)
        try:
            trans = r.recognize_google(audio_data, language="hi-IN")
            print(f"Google STT (Hindi) Transcription: {trans}")
        except sr.UnknownValueError:
            try:
                trans = r.recognize_google(audio_data, language="en-IN")
                print(f"Google STT (English) Transcription: {trans}")
            except Exception as e:
                print(f"Google STT could not understand audio: {e}")
        except Exception as e:
            print(f"Google STT Error: {e}")
    else:
        print("\n[NOTE] Audio peak is near 0.00 (silent on PC loopback).")
        print("This confirms audio is playing on the physical phone speaker/earpiece, not through PC Bluetooth.")

if __name__ == "__main__":
    main()
