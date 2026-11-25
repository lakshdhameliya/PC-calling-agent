import asyncio
import os
import re
from pathlib import Path
import numpy as np
import soundfile as sf
import edge_tts
from scipy import signal

DESKTOP = Path(r"C:\Users\DREAMWORLD\Desktop")

def master_audio(data, sr=48000):
    """Acoustic mastering to turn synthetic TTS into warm, human-like voice."""
    data = data.astype(np.float32, copy=False)
    # Remove DC offset
    data = data - float(np.mean(data))

    # Gentle high-cut (Butterworth 2nd order at 7600 Hz) to eliminate synthetic digital hiss
    sos_lp = signal.butter(2, 7600, 'lp', fs=sr, output='sos')
    data = signal.sosfilt(sos_lp, data)

    # High-pass filter at 70 Hz to remove sub-bass rumble
    sos_hp = signal.butter(2, 70, 'hp', fs=sr, output='sos')
    data = signal.sosfilt(sos_hp, data)

    # Soft 12ms fade in and out to eliminate pops/clicks
    fade_len = min(len(data) // 6, int(0.012 * sr))
    if fade_len > 1:
        ramp = np.linspace(0.0, 1.0, fade_len, dtype=np.float32)
        data[:fade_len] *= ramp
        data[-fade_len:] *= ramp[::-1]

    # Peak normalization to -0.7 dB (0.92) for rich broadcast volume
    peak = float(np.max(np.abs(data))) if len(data) else 0.0
    if peak > 0.001:
        data = (data / peak) * 0.92

    return np.clip(data, -0.98, 0.98)

async def synthesize(text, voice, rate="+4%", pitch="-1Hz", out_path=None):
    # Clean text to ensure natural conversational pauses without robotic dead silences
    clean_text = text.replace("।", ", ").replace("!", ", ").replace("?", ", ")
    clean_text = re.sub(r",\s*,+", ", ", clean_text)
    clean_text = re.sub(r"\s+", " ", clean_text).strip(" ,")

    temp_raw = out_path.with_suffix(".tmp.mp3")
    comm = edge_tts.Communicate(clean_text, voice, rate=rate, pitch=pitch)
    await comm.save(str(temp_raw))

    # Read and master audio
    import av
    container = av.open(str(temp_raw))
    resampler = av.AudioResampler(format="flt", layout="mono", rate=48000)
    frames = []
    for frame in container.decode(audio=0):
        c = resampler.resample(frame)
        if not isinstance(c, list):
            c = [c]
        for part in c:
            frames.append(part.to_ndarray()[0])
    for part in resampler.resample(None):
        frames.append(part.to_ndarray()[0])
    container.close()
    temp_raw.unlink(missing_ok=True)

    raw_audio = np.concatenate(frames)
    mastered = master_audio(raw_audio, sr=48000)

    # Save as high-quality WAV and MP3
    sf.write(str(out_path.with_suffix(".wav")), mastered, 48000, subtype='PCM_16')
    # Convert WAV to MP3 using av
    out_mp3 = out_path.with_suffix(".mp3")
    
    # Save stereo mastered MP3
    out_container = av.open(str(out_mp3), mode="w", format="mp3")
    stream = out_container.add_stream("mp3", rate=48000)
    stream.bit_rate = 192000
    
    # Reshape for mono or stereo
    pcm_s16 = (mastered * 32767.0).astype(np.int16)
    # create AudioFrame
    frame = av.AudioFrame.from_ndarray(pcm_s16.reshape(1, -1), format='s16', layout='mono')
    frame.rate = 48000
    for packet in stream.encode(frame):
        out_container.mux(packet)
    for packet in stream.encode(None):
        out_container.mux(packet)
    out_container.close()

    print(f"Generated: {out_mp3} ({len(mastered)/48000:.2f}s)")
    return out_mp3

async def main():
    samples = [
        {
            "name": "1_Sample_Hindi_Female_Swara_Warm_Natural",
            "voice": "hi-IN-SwaraNeural",
            "rate": "+4%",
            "pitch": "-1Hz",
            "text": "नमस्ते जी, बेस्टज़ोन में आपका बहुत-बहुत स्वागत है, कहिए आज मैं आपकी क्या सहायता करूँ?"
        },
        {
            "name": "2_Sample_Hindi_Male_Madhur_Friendly_Natural",
            "voice": "hi-IN-MadhurNeural",
            "rate": "+4%",
            "pitch": "-1Hz",
            "text": "हेलो जी नमस्ते, बेस्टज़ोन में आपका स्वागत है, बताइए आज आपकी क्या सेवा कर सकते हैं?"
        },
        {
            "name": "3_Sample_Gujarati_Female_Dhwani_Warm_Natural",
            "voice": "gu-IN-DhwaniNeural",
            "rate": "+4%",
            "pitch": "-1Hz",
            "text": "નમસ્તે જી, બેસ્ટઝોનમાં તમારું ખૂબ ખૂબ સ્વાગત છે, કહો આજે હું તમારી શું મદદ કરી શકું?"
        }
    ]

    for s in samples:
        dest = DESKTOP / s["name"]
        print(f"Synthesizing {s['name']}...")
        await synthesize(s["text"], s["voice"], rate=s["rate"], pitch=s["pitch"], out_path=dest)

    print("All 3 natural samples generated successfully!")

if __name__ == "__main__":
    asyncio.run(main())
