import os
import subprocess
import time
import wave
from pathlib import Path
import numpy as np
import pyaudiowpatch as pa
import speech_recognition as sr
from gtts import gTTS

ADB = r"C:\Users\DREAMWORLD\Desktop\platform-tools\adb.exe"
CALLER_DEV = "192.168.29.209:37865"
BOT_DEV = "192.168.29.165:44551"
os.environ["ANDROID_USER_HOME"] = r"c:\Frontbit Link\.android"

def adb(dev, *args):
    cmd = [ADB, "-s", dev] + list(args)
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    return res.stdout.strip()

def get_call_state(dev):
    out = adb(dev, "shell", "dumpsys", "telephony.registry")
    for line in out.splitlines():
        if "mCallState=" in line:
            try:
                return int(line.split("=")[1].strip())
            except Exception:
                pass
    return 0

def play_audio(wav_path):
    p = pa.PyAudio()
    spk_idx = None
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        if "Speakers (Realtek" in info.get("name", "") and "loopback" not in info.get("name", "").lower() and info.get("maxOutputChannels", 0) > 0:
            spk_idx = i
            break
    wf = wave.open(str(wav_path), "rb")
    stream = p.open(
        format=p.get_format_from_width(wf.getsampwidth()),
        channels=wf.getnchannels(),
        rate=wf.getframerate(),
        output=True,
        output_device_index=spk_idx
    )
    chunk = 2048
    data = wf.readframes(chunk)
    while data:
        stream.write(data)
        data = wf.readframes(chunk)
    stream.stop_stream()
    stream.close()
    p.terminate()
    wf.close()

def main():
    print("=== [1/6] Preparing Bot Speech WAV ===")
    tts_text = "नमस्ते! फ्रंटबिट टेक्नोलॉजीज रेस्टोरेंट में आपका स्वागत है। मैं आपकी क्या सहायता कर सकता हूँ?"
    tts_mp3 = Path("bot_call_greeting.mp3")
    tts_wav = Path("bot_call_greeting.wav")
    tts = gTTS(text=tts_text, lang="hi")
    tts.save(str(tts_mp3))
    subprocess.run(["ffmpeg", "-y", "-i", str(tts_mp3), "-ar", "48000", "-ac", "2", str(tts_wav)], capture_output=True)
    print(f"TTS Audio Ready: '{tts_text}'")

    print("\n=== [2/6] Tapping Call Button on Caller Phone (540, 1956) ===")
    adb(CALLER_DEV, "shell", "input", "tap", "540", "1956")
    time.sleep(1.0)
    # Check if a SIM chooser appeared on caller phone
    adb(CALLER_DEV, "shell", "input", "tap", "540", "1956")

    print("Waiting for Bot Phone to Ring...")
    connected = False
    for attempt in range(25):
        s_bot = get_call_state(BOT_DEV)
        s_caller = get_call_state(CALLER_DEV)
        print(f"[{attempt+1}s] Caller State: {s_caller} | Bot Phone State: {s_bot}")
        if s_bot == 1:
            print(">>> BOT PHONE IS RINGING! <<<")
            time.sleep(2.0)  # Wait 2 seconds of ring
            print(">>> Answering on Bot Phone (Tapping 554, 1238) <<<")
            adb(BOT_DEV, "shell", "input", "tap", "554", "1238")
            adb(BOT_DEV, "shell", "input", "swipe", "554", "1238", "554", "800", "250")
            time.sleep(2.0)
            if get_call_state(BOT_DEV) == 2 or get_call_state(CALLER_DEV) == 2:
                print(">>> CALL CONNECTED SUCCESSFULLY! <<<")
                connected = True
                break
        time.sleep(1.0)

    if not connected:
        print("[WARNING] Call did not connect in 25 seconds.")
        return

    print("\n=== [3/6] Maximizing Volume on Caller Phone Speaker ===")
    adb(CALLER_DEV, "shell", "cmd", "media_session", "volume", "--stream", "0", "--set", "15")

    print("\n=== [4/6] Playing Bot Voice into Phone Mic via Cable 2 ===")
    t0 = time.perf_counter()
    play_audio(tts_wav)
    print(f"Bot voice played in {time.perf_counter()-t0:.2f}s!")

    time.sleep(3.0)

    print("\n=== [5/6] Recording Audio from Cable 1 (Microphone Realtek) for 6s ===")
    p = pa.PyAudio()
    mic_idx = None
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        if "Microphone (Realtek" in info.get("name", "") and "loopback" not in info.get("name", "").lower() and info.get("maxInputChannels", 0) > 0:
            mic_idx = i
            break
    stream = p.open(format=pa.paInt16, channels=2, rate=48000, input=True, input_device_index=mic_idx, frames_per_buffer=2048)
    chunks = []
    t_end = time.time() + 6.0
    while time.time() < t_end:
        data = stream.read(2048, exception_on_overflow=False)
        chunks.append(data)
    stream.stop_stream()
    stream.close()
    p.terminate()

    raw = b"".join(chunks)
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    print(f"Recorded {len(raw)} bytes from Cable 1. Max Peak: {np.max(np.abs(samples)):.4f}, RMS: {np.sqrt(np.mean(samples**2)):.4f}")

    print("\n=== [6/6] Hanging up Call ===")
    adb(CALLER_DEV, "shell", "input", "keyevent", "6")
    adb(BOT_DEV, "shell", "input", "keyevent", "6")
    print("Call hung up successfully!")

if __name__ == "__main__":
    main()
