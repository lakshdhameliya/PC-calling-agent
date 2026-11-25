import asyncio
import subprocess
from pathlib import Path
import edge_tts

DESKTOP = Path(r"C:\Users\DREAMWORLD\Desktop")

async def generate_pure_sample(filename, voice, text, rate="+0%", pitch="+0Hz"):
    temp_mp3 = DESKTOP / f"temp_{filename}"
    final_mp3 = DESKTOP / filename

    # Pure high-definition Azure Neural synthesis
    comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
    await comm.save(str(temp_mp3))

    # Only trim empty silence at the tail, keeping audio 100% natural and uncorrupted
    cmd = [
        "ffmpeg", "-y", "-i", str(temp_mp3),
        "-af", "silenceremove=stop_periods=-1:stop_duration=0.25:stop_threshold=-40dB",
        "-c:a", "libmp3lame", "-q:a", "0", str(final_mp3)
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    temp_mp3.unlink(missing_ok=True)
    print(f"Generated: {final_mp3.name} ({final_mp3.stat().st_size} bytes)")

async def main():
    samples = [
        (
            "1_Pure_HD_Hindi_Female_Swara.mp3",
            "hi-IN-SwaraNeural",
            "नमस्ते, बेस्टज़ोन में आपका स्वागत है, बताइए आज मैं आपकी क्या मदद कर सकती हूँ?",
            "+0%",
            "+0Hz"
        ),
        (
            "2_Pure_HD_Hindi_Male_Madhur.mp3",
            "hi-IN-MadhurNeural",
            "नमस्ते, बेस्टज़ोन में आपका स्वागत है, बताइए आज मैं आपकी क्या सहायता करूँ?",
            "+0%",
            "+0Hz"
        ),
        (
            "3_Pure_HD_Gujarati_Female_Dhwani.mp3",
            "gu-IN-DhwaniNeural",
            "નમસ્તે, બેસ્ટઝોનમાં તમારું સ્વાગત છે, કહો આજે હું તમારી શું મદદ કરી શકું?",
            "+0%",
            "+0Hz"
        ),
        (
            "4_Pure_HD_Gujarati_Male_Niranjan.mp3",
            "gu-IN-NiranjanNeural",
            "નમસ્તે, બેસ્ટઝોનમાં તમારું સ્વાગત છે, કહો આજે હું તમારી શું સેવા કરી શકું?",
            "+0%",
            "+0Hz"
        )
    ]

    for fname, voice, text, rate, pitch in samples:
        await generate_pure_sample(fname, voice, text, rate, pitch)

    print("All Pure HD Samples Generated Successfully!")

if __name__ == "__main__":
    asyncio.run(main())
