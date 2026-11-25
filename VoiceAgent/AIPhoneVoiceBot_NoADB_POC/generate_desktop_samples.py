import asyncio
import os
import re
import subprocess
from pathlib import Path
import edge_tts

DESKTOP = Path(r"C:\Users\DREAMWORLD\Desktop")

def clean_speech_text(text):
    # Convert halting punctuation into natural light conversational breath commas
    t = text.replace("।", ", ").replace("!", ", ").replace("?", ", ")
    t = re.sub(r",\s*,+", ", ", t)
    t = re.sub(r"\s+", " ", t).strip(" ,")
    return t

async def generate_mastered_sample(title, voice, text, rate="+4%", pitch="-1Hz"):
    cleaned = clean_speech_text(text)
    temp_raw = DESKTOP / f"{title}_raw.mp3"
    final_mp3 = DESKTOP / f"{title}.mp3"
    
    # 1. Edge-TTS Free Neural Synthesis
    comm = edge_tts.Communicate(cleaned, voice, rate=rate, pitch=pitch)
    await comm.save(str(temp_raw))
    
    # 2. Studio Mastering Filter Chain:
    # - highpass=f=75: eliminates sub-bass rumble
    # - lowpass=f=7800: eliminates synthetic metallic/digital sibilance
    # - equalizer=f=280: adds human chest warmth & presence
    # - loudnorm=I=-16:TP=-1.5:LRA=7: professional broadcast leveling for rich, punchy volume
    cmd = [
        "ffmpeg", "-y", "-i", str(temp_raw),
        "-af", "highpass=f=75,lowpass=f=7800,equalizer=f=280:t=q:w=1.0:g=2.2,loudnorm=I=-16:TP=-1.5:LRA=7",
        "-b:a", "192k", str(final_mp3)
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    temp_raw.unlink(missing_ok=True)
    print(f"Generated mastered sample: {final_mp3} ({final_mp3.stat().st_size} bytes)")
    return final_mp3

async def main():
    samples = [
        {
            "title": "1_Sample_Hindi_Female_Swara_Natural",
            "voice": "hi-IN-SwaraNeural",
            "rate": "+4%",
            "pitch": "-1Hz",
            "text": "नमस्ते जी, बेस्टज़ोन में आपका बहुत-बहुत स्वागत है, कहिए आज मैं आपकी क्या सहायता करूँ?"
        },
        {
            "title": "2_Sample_Hindi_Male_Madhur_Natural",
            "voice": "hi-IN-MadhurNeural",
            "rate": "+4%",
            "pitch": "-1Hz",
            "text": "हेलो जी नमस्ते, बेस्टज़ोन में आपका स्वागत है, बताइए आज आपकी क्या सेवा कर सकते हैं?"
        },
        {
            "title": "3_Sample_Gujarati_Female_Dhwani_Natural",
            "voice": "gu-IN-DhwaniNeural",
            "rate": "+4%",
            "pitch": "-1Hz",
            "text": "નમસ્તે જી, બેસ્ટઝોનમાં તમારું ખૂબ ખૂબ સ્વાગત છે, કહો આજે હું તમારી શું મદદ કરી શકું?"
        }
    ]
    
    for s in samples:
        await generate_mastered_sample(s["title"], s["voice"], s["text"], rate=s["rate"], pitch=s["pitch"])

if __name__ == "__main__":
    asyncio.run(main())
