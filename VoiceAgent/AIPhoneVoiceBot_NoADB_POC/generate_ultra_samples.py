import asyncio
import os
import re
import subprocess
from pathlib import Path
import edge_tts
import av
import numpy as np

DESKTOP = Path(r"C:\Users\DREAMWORLD\Desktop")

def clean_speech_text(text):
    t = text.replace("।", ", ").replace("!", ", ").replace("?", ", ")
    t = re.sub(r",\s*,+", ", ", t)
    t = re.sub(r"\s+", " ", t).strip(" ,")
    return t

async def generate_mastered_sample(title, voice, text, rate="+8%", pitch="-1Hz"):
    cleaned = clean_speech_text(text)
    temp_raw = DESKTOP / f"{title}_raw.mp3"
    final_mp3 = DESKTOP / f"{title}.mp3"
    
    # 1. Edge-TTS Free Neural Synthesis
    comm = edge_tts.Communicate(cleaned, voice, rate=rate, pitch=pitch)
    await comm.save(str(temp_raw))
    
    # 2. Ultra-Studio Mastering Filter:
    # - silenceremove: removes awkward pauses & trailing blank space
    # - highpass=f=80: eliminates low-end room rumble
    # - lowpass=f=8200: removes digital harshness & metallic hiss
    # - equalizer 260Hz: adds warm human chest resonance
    # - equalizer 4500Hz: de-esser to soften harsh 's/sh' sibilants
    # - loudnorm: broadcast EBU R128 standard loudness (-16 LUFS)
    cmd = [
        "ffmpeg", "-y", "-i", str(temp_raw),
        "-af", "silenceremove=stop_periods=-1:stop_duration=0.15:stop_threshold=-40dB,highpass=f=80,lowpass=f=8200,equalizer=f=260:t=q:w=1.2:g=2.5,equalizer=f=4500:t=q:w=1.5:g=-2.0,loudnorm=I=-16:TP=-1.5:LRA=7",
        "-b:a", "192k", str(final_mp3)
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    temp_raw.unlink(missing_ok=True)
    
    # Check stats
    c = av.open(str(final_mp3))
    dur = c.duration / 1000000.0
    c.close()
    print(f"Generated: {final_mp3.name} | Duration: {dur:.2f}s | Size: {final_mp3.stat().st_size} bytes")
    return final_mp3

async def main():
    samples = [
        {
            "title": "1_Ultra_Natural_Hindi_Female_Swara_Studio",
            "voice": "hi-IN-SwaraNeural",
            "rate": "+6%",
            "pitch": "-1Hz",
            "text": "नमस्ते जी, बेस्टज़ोन में आपका बहुत-बहुत स्वागत है, बताइए आज मैं आपकी क्या मदद कर सकती हूँ?"
        },
        {
            "title": "2_Expressive_Human_Hinglish_Neerja_Studio",
            "voice": "en-IN-NeerjaExpressiveNeural",
            "rate": "+5%",
            "pitch": "+0Hz",
            "text": "Namaste ji, BestZone mein aapka swagat hai, bataiye aaj main aapki kya help kar sakti hoon?"
        },
        {
            "title": "3_Ultra_Natural_Hindi_Male_Madhur_Studio",
            "voice": "hi-IN-MadhurNeural",
            "rate": "+5%",
            "pitch": "-1Hz",
            "text": "हेलो जी नमस्ते, बेस्टज़ोन में आपका स्वागत है, बताइए आज आपकी क्या सेवा कर सकते हैं?"
        },
        {
            "title": "4_Ultra_Natural_Gujarati_Female_Dhwani_Studio",
            "voice": "gu-IN-DhwaniNeural",
            "rate": "+5%",
            "pitch": "-1Hz",
            "text": "નમસ્તે જી, બેસ્ટઝોનમાં તમારું ખૂબ ખૂબ સ્વાગત છે, કહો આજે હું તમારી શું મદદ કરી શકું?"
        }
    ]
    
    for s in samples:
        await generate_mastered_sample(s["title"], s["voice"], s["text"], rate=s["rate"], pitch=s["pitch"])

if __name__ == "__main__":
    asyncio.run(main())
