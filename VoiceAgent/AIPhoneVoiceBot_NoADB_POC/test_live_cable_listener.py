"""Live Cable Audio Listener and Tester.

Listens on 'Microphone (Realtek(R) Audio)' for any sound from the phone (e.g., 199 call, music, speech).
When audio is detected:
1. Transcribes live with Google STT.
2. Plays an answer back to 'Speakers (Realtek(R) Audio)' (which goes back into Phone Mic).
"""
import sys
import time
import numpy as np
import pyaudiowpatch as pa
import speech_recognition as sr

def find_device(kind, needle):
    p = pa.PyAudio()
    try:
        fallback = None
        for i in range(p.get_device_count()):
            info = p.get_device_info_by_index(i)
            name = info.get("name", "")
            if needle.lower() in name.lower() and "loopback" not in name.lower():
                if kind == "input" and info.get("maxInputChannels", 0) > 0:
                    return i, name
                if kind == "output" and info.get("maxOutputChannels", 0) > 0:
                    return i, name
        raise RuntimeError(f"{kind} device containing '{needle}' not found")
    finally:
        p.terminate()

def main():
    print("=== Step 1: Finding Physical Audio Jacks ===")
    mic_idx, mic_name = find_device("input", "Microphone (Realtek")
    spk_idx, spk_name = find_device("output", "Speakers (Realtek")
    print(f"INPUT (Phone -> PC): {mic_idx} - {mic_name}")
    print(f"OUTPUT (PC -> Phone): {spk_idx} - {spk_name}")

    print("\n=== Step 2: Listening for Phone Audio (Duration: 25 seconds) ===")
    print(">>> Please play any sound/song or dial 199 on your OPPO phone now! <<<")

    p = pa.PyAudio()
    stream = p.open(
        format=pa.paInt16,
        channels=2,
        rate=48000,
        input=True,
        input_device_index=mic_idx,
        frames_per_buffer=2048
    )

    r = sr.Recognizer()
    deadline = time.time() + 25
    recorded_chunks = []
    max_peak = 0.0

    try:
        while time.time() < deadline:
            data = stream.read(2048, exception_on_overflow=False)
            recorded_chunks.append(data)
            samples = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
            peak = float(np.max(np.abs(samples)))
            if peak > max_peak:
                max_peak = peak
            if peak > 0.02:
                print(f"[LIVE AUDIO DETECTED!] Peak Level: {peak:.4f}")
    finally:
        stream.stop_stream()
        stream.close()
        p.terminate()

    print(f"\nMonitoring Finished. Maximum Audio Peak: {max_peak:.4f}")

    all_raw = b"".join(recorded_chunks)
    if max_peak > 0.015:
        print("\n=== Step 3: Transcribing Audio with Google STT ===")
        audio_data = sr.AudioData(all_raw, 48000, 2)
        try:
            text = r.recognize_google(audio_data, language="hi-IN")
            print(f">>> [SUCCESS] Heard from Phone: '{text}' <<<")
        except sr.UnknownValueError:
            try:
                text = r.recognize_google(audio_data, language="en-IN")
                print(f">>> [SUCCESS] Heard from Phone (English): '{text}' <<<")
            except Exception as e:
                print(f"Audio detected but speech was unclear: {e}")
        except Exception as e:
            print(f"Google STT Error: {e}")
    else:
        print("\n[NOTE] No audio heard from the phone during the 12 seconds.")
        print("Please check: Is the phone volume turned up, and is the Type-C adapter firmly plugged in?")

if __name__ == "__main__":
    main()
