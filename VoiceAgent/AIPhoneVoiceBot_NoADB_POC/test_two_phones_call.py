import os
import subprocess
import time
import wave
from pathlib import Path
import numpy as np
import pyaudiowpatch as pa
from gtts import gTTS

ADB = r"C:\Users\DREAMWORLD\Desktop\platform-tools\adb.exe"
CALLER_DEV = "192.168.29.209:37865"
BOT_DEV = "192.168.29.165:44551"
BOT_NUMBER = "9081819676"
os.environ["ANDROID_USER_HOME"] = r"c:\Frontbit Link\.android"

def adb(device, *args):
    cmd = [ADB, "-s", device] + list(args)
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    return res.stdout.strip()

def get_call_state(device):
    out = adb(device, "shell", "dumpsys", "telephony.registry")
    for line in out.splitlines():
        if "mCallState=" in line:
            try:
                return int(line.split("=")[1].strip())
            except Exception:
                pass
    return None

def play_audio_to_speakers(wav_path):
    p = pa.PyAudio()
    spk_idx = None
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        name = info.get("name", "")
        if "Speakers (Realtek" in name and "loopback" not in name.lower() and info.get("maxOutputChannels", 0) > 0:
            spk_idx = i
            break
    print(f"Playing to PC Speakers (Index: {spk_idx} - {p.get_device_info_by_index(spk_idx)['name']})")
    
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
    print("=== Step 1: Preparing Loud Bot Greeting ===")
    tts_text = "नमस्ते! मैं फ्रंटबिट एआई वॉइस असिस्टेंट हूँ। क्या आपको मेरी आवाज़ साफ़ सुनाई दे रही है?"
    tts_mp3 = Path("bot_test_greeting.mp3")
    tts_wav = Path("bot_test_greeting.wav")
    tts = gTTS(text=tts_text, lang="hi")
    tts.save(str(tts_mp3))
    subprocess.run(["ffmpeg", "-y", "-i", str(tts_mp3), "-ar", "48000", "-ac", "2", str(tts_wav)], capture_output=True)
    print("Greeting WAV generated successfully.")

    # Maximize volumes
    print("\n=== Step 2: Maximizing Audio Volumes ===")
    adb(CALLER_DEV, "shell", "cmd", "media_session", "volume", "--stream", "0", "--set", "15")
    adb(BOT_DEV, "shell", "cmd", "media_session", "volume", "--stream", "0", "--set", "15")

    print(f"\n=== Step 3: Dialing {BOT_NUMBER} from Caller Phone ({CALLER_DEV}) ===")
    adb(CALLER_DEV, "shell", "am", "start", "-a", "android.intent.action.CALL", "-d", f"tel:{BOT_NUMBER}")
    
    print("Waiting for Bot Phone to Ring...")
    ring_detected = False
    for _ in range(20):
        s_bot = get_call_state(BOT_DEV)
        print(f"Bot Phone State: {s_bot} (1=Ringing, 2=InCall, 0=Idle)")
        if s_bot == 1:
            ring_detected = True
            break
        time.sleep(1.0)

    if not ring_detected:
        print("[NOTE] Did not see state 1; attempting dial via keyevent 5 on caller phone")
        adb(CALLER_DEV, "shell", "input", "keyevent", "5")
        time.sleep(2.0)

    print("\n=== Step 4: Answering Call on Bot Phone ===")
    time.sleep(1.0)
    adb(BOT_DEV, "shell", "input", "keyevent", "5")
    adb(BOT_DEV, "shell", "input", "keyevent", "79")
    time.sleep(2.0)
    print(f"Bot Phone In-Call State: {get_call_state(BOT_DEV)}")
    print(f"Caller Phone In-Call State: {get_call_state(CALLER_DEV)}")

    print("\n=== Step 5: Playing Loud Bot Voice into Phone via Cable 2 ===")
    play_audio_to_speakers(tts_wav)
    print("Bot speech playback finished!")

    print("\n=== Step 6: Recording from Cable 1 (Caller -> Bot Phone -> PC Mic) for 5s ===")
    p = pa.PyAudio()
    mic_idx = None
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        if "Microphone (Realtek" in info.get("name", "") and "loopback" not in info.get("name", "").lower() and info.get("maxInputChannels", 0) > 0:
            mic_idx = i
            break
    
    stream = p.open(format=pa.paInt16, channels=2, rate=48000, input=True, input_device_index=mic_idx, frames_per_buffer=2048)
    chunks = []
    t_end = time.time() + 5.0
    while time.time() < t_end:
        data = stream.read(2048, exception_on_overflow=False)
        chunks.append(data)
    stream.stop_stream()
    stream.close()
    p.terminate()

    raw = b"".join(chunks)
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    print(f"Recorded from Cable 1: {len(raw)} bytes, Max Peak: {np.max(np.abs(samples)):.4f}, RMS: {np.sqrt(np.mean(samples**2)):.4f}")

    print("\n=== Step 7: Hanging Up Call ===")
    adb(CALLER_DEV, "shell", "input", "keyevent", "6")
    adb(BOT_DEV, "shell", "input", "keyevent", "6")
    print("Test Completed!")

if __name__ == "__main__":
    main()
