import os
import subprocess
import time
import wave
from pathlib import Path
import numpy as np
import pyaudiowpatch as pa
import speech_recognition as sr

ADB = r"C:\Users\DREAMWORLD\Desktop\platform-tools\adb.exe"
DEVICE = "192.168.29.165:44551"
os.environ["ANDROID_USER_HOME"] = r"c:\Frontbit Link\.android"

def run_adb(*args):
    cmd = [ADB, "-s", DEVICE] + list(args)
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    return res.stdout.strip()

def get_call_state():
    out = run_adb("shell", "dumpsys", "telephony.registry")
    state = "0"
    for line in out.splitlines():
        if "mPreciseCallState" in line:
            return line.strip()
    return ""

def main():
    print("=== Step 1: Locating Realtek Microphone ===")
    p = pa.PyAudio()
    mic_idx = 2
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        if "Microphone (Realtek" in info.get("name", "") and "loopback" not in info.get("name", "").lower() and info.get("maxInputChannels", 0) > 0:
            mic_idx = i
            break
    print(f"Using Microphone Index: {mic_idx} - {p.get_device_info_by_index(mic_idx).get('name')}")

    # Set Call stream volume to max on phone
    run_adb("shell", "cmd", "media_session", "volume", "--stream", "0", "--set", "9")

    print("\n=== Step 2: Opening Dialer with 198 ===")
    run_adb("shell", "am", "start", "-a", "android.intent.action.DIAL", "-d", "tel:198")
    time.sleep(1.0)

    print("=== Step 3: Tapping Call Button ===")
    run_adb("shell", "input", "tap", "359", "1275")
    time.sleep(2.0)

    print(f"Call State after tap: {get_call_state()}")

    print("\n=== Step 4: Recording from Cable Audio for 15 Seconds ===")
    stream = p.open(
        format=pa.paInt16,
        channels=2,
        rate=48000,
        input=True,
        input_device_index=mic_idx,
        frames_per_buffer=2048
    )

    chunks = []
    peaks = []
    deadline = time.time() + 15
    while time.time() < deadline:
        data = stream.read(2048, exception_on_overflow=False)
        chunks.append(data)
        samples = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
        pk = float(np.max(np.abs(samples)))
        peaks.append(pk)
        if pk > 0.02:
            print(f"[AUDIO ACTIVITY] Peak: {pk:.4f}")

    stream.stop_stream()
    stream.close()
    p.terminate()

    print("\n=== Step 5: Hanging up Call ===")
    run_adb("shell", "input", "keyevent", "6")

    raw_audio = b"".join(chunks)
    samples = np.frombuffer(raw_audio, dtype=np.int16).astype(np.float32) / 32768.0
    max_pk = float(np.max(np.abs(samples)))
    rms = float(np.sqrt(np.mean(samples ** 2)))
    print(f"\nTotal bytes recorded: {len(raw_audio)}")
    print(f"Max Peak: {max_pk:.4f}, RMS: {rms:.4f}")

    wav_path = Path("live_cable_198_test.wav")
    with wave.open(str(wav_path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(raw_audio)
    print(f"Saved audio to: {wav_path.resolve()}")

    if max_pk > 0.01:
        print("\n=== Step 6: Google STT Decoding ===")
        r = sr.Recognizer()
        with sr.AudioFile(str(wav_path)) as src:
            audio_data = r.record(src)
        for lang in ["hi-IN", "gu-IN", "en-IN"]:
            try:
                t0 = time.perf_counter()
                text = r.recognize_google(audio_data, language=lang)
                dur = time.perf_counter() - t0
                print(f">>> [Google STT {lang}] ({dur:.2f}s): '{text}' <<<")
                break
            except sr.UnknownValueError:
                print(f"No speech detected in {lang}")
            except Exception as e:
                print(f"Error {lang}: {e}")
    else:
        print("[WARNING] Audio peak was too low (< 0.01)")

if __name__ == "__main__":
    main()
