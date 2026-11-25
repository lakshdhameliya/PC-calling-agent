"""End-to-End Live Cable Call Test with OPPO A6 5G over ADB."""
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
DEVICE = "192.168.29.165:44551"
os.environ["ANDROID_USER_HOME"] = r"c:\Frontbit Link\.android"

def run_adb(*args):
    cmd = [ADB, "-s", DEVICE] + list(args)
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    return res.stdout.strip()

def find_device(kind, needle):
    p = pa.PyAudio()
    try:
        for i in range(p.get_device_count()):
            info = p.get_device_info_by_index(i)
            name = info.get("name", "")
            if needle.lower() in name.lower() and "loopback" not in name.lower():
                if kind == "input" and info.get("maxInputChannels", 0) > 0:
                    return i, name
                if kind == "output" and info.get("maxOutputChannels", 0) > 0:
                    return i, name
        raise RuntimeError(f"{kind} device matching '{needle}' not found")
    finally:
        p.terminate()

def main():
    print("=== Step 1: Locating Audio Endpoints ===")
    mic_idx, mic_name = find_device("input", "Microphone (Realtek")
    spk_idx, spk_name = find_device("output", "Speakers (Realtek")
    print(f"INPUT (Phone Audio into PC): {mic_idx} - {mic_name}")
    print(f"OUTPUT (PC Voice into Phone): {spk_idx} - {spk_name}")

    print("\n=== Step 2: Dialing 199 on OPPO A6 5G ===")
    dial_out = run_adb("shell", "am", "start", "-a", "android.intent.action.CALL", "-d", "tel:199")
    print(f"Dial command: {dial_out}")

    print("\n=== Step 3: Recording Audio from Cable for 15 Seconds ===")
    p = pa.PyAudio()
    stream = p.open(
        format=pa.paInt16,
        channels=2,
        rate=48000,
        input=True,
        input_device_index=mic_idx,
        frames_per_buffer=2048
    )

    chunks = []
    max_peak = 0.0
    deadline = time.time() + 15

    try:
        while time.time() < deadline:
            data = stream.read(2048, exception_on_overflow=False)
            chunks.append(data)
            samples = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
            peak = float(np.max(np.abs(samples)))
            if peak > max_peak:
                max_peak = peak
            if peak > 0.02:
                print(f"[LIVE AUDIO DETECTED!] Peak: {peak:.4f}")
    finally:
        stream.stop_stream()
        stream.close()
        p.terminate()

        print("\n=== Step 4: Hanging Up Call ===")
        end_out = run_adb("shell", "input", "keyevent", "KEYCODE_ENDCALL")
        print(f"Call hung up: {end_out}")

    raw_audio = b"".join(chunks)
    samples = np.frombuffer(raw_audio, dtype=np.int16).astype(np.float32) / 32768.0
    rms = float(np.sqrt(np.mean(samples ** 2)))
    print(f"\nTotal bytes recorded: {len(raw_audio)}")
    print(f"Audio Peak: {max_peak:.4f}")
    print(f"Audio RMS: {rms:.4f}")

    # Save to a temporary WAV for inspection & STT
    wav_path = Path("live_199_cable_record.wav")
    with wave.open(str(wav_path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(raw_audio)
    print(f"Saved audio recording to: {wav_path.resolve()}")

    if max_peak > 0.015:
        print("\n=== Step 5: Transcribing 199 IVR Audio with Google STT ===")
        r = sr.Recognizer()
        with sr.AudioFile(str(wav_path)) as source:
            audio_data = r.record(source)
        try:
            t0 = time.perf_counter()
            text = r.recognize_google(audio_data, language="hi-IN")
            dur = time.perf_counter() - t0
            print(f">>> [Google STT Hindi] ({dur:.2f}s): '{text}' <<<")
        except sr.UnknownValueError:
            try:
                t0 = time.perf_counter()
                text = r.recognize_google(audio_data, language="gu-IN")
                dur = time.perf_counter() - t0
                print(f">>> [Google STT Gujarati] ({dur:.2f}s): '{text}' <<<")
            except sr.UnknownValueError:
                try:
                    t0 = time.perf_counter()
                    text = r.recognize_google(audio_data, language="en-IN")
                    dur = time.perf_counter() - t0
                    print(f">>> [Google STT English] ({dur:.2f}s): '{text}' <<<")
                except Exception as e:
                    print(f"Speech detected but could not decode words: {e}")
        except Exception as e:
            print(f"Google STT Error: {e}")
    else:
        print("\n[NOTE] Audio peak was below 0.015 (quiet).")

if __name__ == "__main__":
    main()
