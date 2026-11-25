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
DEVICE = "192.168.29.165:44551"
os.environ["ANDROID_USER_HOME"] = r"c:\Frontbit Link\.android"

def run_adb(*args):
    cmd = [ADB, "-s", DEVICE] + list(args)
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    return res.stdout.strip()

def play_audio(file_path, output_index):
    wf = wave.open(str(file_path), "rb")
    p = pa.PyAudio()
    stream = p.open(
        format=p.get_format_from_width(wf.getsampwidth()),
        channels=wf.getnchannels(),
        rate=wf.getframerate(),
        output=True,
        output_device_index=output_index
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
    print("=== [1/6] Preparing Bot TTS Response ===")
    tts_text = "नमस्ते! फ्रंटबिट टेक्नोलॉजीज में आपका स्वागत है। मैं आपकी क्या सहायता कर सकता हूँ?"
    tts_wav = Path("bot_reply.wav")
    tts_mp3 = Path("bot_reply.mp3")
    
    # Generate TTS using gTTS
    tts = gTTS(text=tts_text, lang="hi")
    tts.save(str(tts_mp3))
    
    # Convert MP3 to 48kHz WAV
    subprocess.run([
        "ffmpeg", "-y", "-i", str(tts_mp3),
        "-ar", "48000", "-ac", "2", str(tts_wav)
    ], capture_output=True)
    print(f"TTS ready: '{tts_text}'")

    print("\n=== [2/6] Detecting Audio Endpoints ===")
    p = pa.PyAudio()
    mic_idx = None
    spk_idx = None
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        name = info.get("name", "")
        if "Microphone (Realtek" in name and "loopback" not in name.lower() and info.get("maxInputChannels", 0) > 0:
            mic_idx = i
        if "Speakers (Realtek" in name and "loopback" not in name.lower() and info.get("maxOutputChannels", 0) > 0:
            spk_idx = i
    p.terminate()

    print(f"INPUT (Phone -> PC via Cable 1): {mic_idx}")
    print(f"OUTPUT (PC -> Phone via Cable 2): {spk_idx}")

    # Set Call stream volume to max on phone
    run_adb("shell", "cmd", "media_session", "volume", "--stream", "0", "--set", "9")

    print("\n=== [3/6] Dialing 198 (Vi Helpline) on Phone ===")
    run_adb("shell", "am", "start", "-a", "android.intent.action.DIAL", "-d", "tel:198")
    time.sleep(1.0)
    run_adb("shell", "input", "tap", "359", "1275")
    time.sleep(2.0)

    print("\n=== [4/6] Listening to Caller IVR Audio (10 Seconds) ===")
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
    deadline = time.time() + 10
    while time.time() < deadline:
        data = stream.read(2048, exception_on_overflow=False)
        chunks.append(data)
    stream.stop_stream()
    stream.close()
    p.terminate()

    ivr_raw = b"".join(chunks)
    ivr_wav = Path("live_ivr_received.wav")
    with wave.open(str(ivr_wav), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(ivr_raw)

    print("Transcribing IVR with Google STT...")
    r = sr.Recognizer()
    with sr.AudioFile(str(ivr_wav)) as src:
        aud = r.record(src)
    try:
        t0 = time.perf_counter()
        txt = r.recognize_google(aud, language="hi-IN")
        print(f">>> [IVR Transcript] ({time.perf_counter()-t0:.2f}s): '{txt}' <<<")
    except Exception as e:
        print(f"IVR STT Info: {e}")

    print("\n=== [5/6] Sending Bot Voice Into Phone Mic via AUX Cable 2 ===")
    t_start_speak = time.perf_counter()
    play_audio(tts_wav, spk_idx)
    speak_dur = time.perf_counter() - t_start_speak
    print(f"Bot voice played into phone call in {speak_dur:.2f}s!")

    time.sleep(2.0)

    print("\n=== [6/6] Hanging up Call ===")
    run_adb("shell", "input", "keyevent", "6")
    print("Call completed and hung up!")

if __name__ == "__main__":
    main()
