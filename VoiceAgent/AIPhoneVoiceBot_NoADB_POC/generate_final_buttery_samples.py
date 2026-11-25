import asyncio
import os
import subprocess
from pathlib import Path
import av
import edge_tts
from gtts import gTTS
import numpy as np
import soundfile as sf

DESKTOP = Path(r"C:\Users\DREAMWORLD\Desktop")
SR = 48000

def compress_dead_silence(audio, sr=48000, max_silence_s=0.18, keep_silence_s=0.10):
    """Eliminates unnatural robot pauses by compressing dead silence down to a natural human breath pause (100ms)."""
    # Amplitude threshold for active speech vs silence
    is_speech = np.abs(audio) > 0.02
    win = int(sr * 0.04)
    speech_smoothed = np.convolve(is_speech.astype(np.float32), np.ones(win)/win, mode='same') > 0.04

    max_silence = int(sr * max_silence_s)
    keep_silence = int(sr * keep_silence_s)

    out_pieces = []
    i = 0
    while i < len(audio):
        if not speech_smoothed[i]:
            j = i
            while j < len(audio) and not speech_smoothed[j]:
                j += 1
            sil_len = j - i
            if sil_len > max_silence:
                sil_chunk = audio[i:i+keep_silence]
                fade = len(sil_chunk) // 2
                ramp = np.linspace(1.0, 0.0, fade)
                sil_chunk[:fade] *= ramp
                sil_chunk[-fade:] *= ramp[::-1]
                out_pieces.append(sil_chunk)
            else:
                out_pieces.append(audio[i:j])
            i = j
        else:
            j = i
            while j < len(audio) and speech_smoothed[j]:
                j += 1
            out_pieces.append(audio[i:j])
            i = j

    smooth = np.concatenate(out_pieces)
    # Trim leading & trailing silence
    start = 0
    while start < len(smooth) and abs(smooth[start]) < 0.015:
        start += 1
    end = len(smooth) - 1
    while end > 0 and abs(smooth[end]) < 0.015:
        end -= 1

    return smooth[max(0, start - int(sr*0.03)):min(len(smooth), end + int(sr*0.05))]

def master_audio_file(in_wav, out_mp3):
    cmd = [
        "ffmpeg", "-y", "-i", str(in_wav),
        "-af", "highpass=f=75,equalizer=f=240:t=q:w=1.2:g=2.8,equalizer=f=4000:t=q:w=1.5:g=-2.5,lowpass=f=7800,acompressor=threshold=-20dB:ratio=2.5:attack=15:release=200:makeup=2,loudnorm=I=-16:TP=-1.5:LRA=6",
        "-b:a", "192k", str(out_mp3)
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

async def synthesize_edge(title, voice, text, rate="+4%", pitch="+1Hz"):
    temp_mp3 = DESKTOP / f"{title}_raw.mp3"
    temp_wav = DESKTOP / f"{title}_flow.wav"
    final_mp3 = DESKTOP / f"{title}.mp3"

    comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
    await comm.save(str(temp_mp3))

    c = av.open(str(temp_mp3))
    frames = [f.to_ndarray() for f in c.decode(audio=0)]
    c.close()
    audio = np.concatenate(frames, axis=1)[0].astype(np.float32)

    flowing_audio = compress_dead_silence(audio, sr=SR)
    sf.write(str(temp_wav), flowing_audio, SR)
    master_audio_file(temp_wav, final_mp3)

    temp_mp3.unlink(missing_ok=True)
    temp_wav.unlink(missing_ok=True)
    print(f"Generated {final_mp3.name} ({len(flowing_audio)/SR:.2f}s)")
    return final_mp3

def synthesize_gtts(title, lang, text):
    temp_mp3 = DESKTOP / f"{title}_raw.mp3"
    temp_wav = DESKTOP / f"{title}_flow.wav"
    final_mp3 = DESKTOP / f"{title}.mp3"

    tts = gTTS(text=text, lang=lang, slow=False)
    tts.save(str(temp_mp3))

    c = av.open(str(temp_mp3))
    resampler = av.AudioResampler(format="flt", layout="mono", rate=SR)
    frames = []
    for f in c.decode(audio=0):
        c_f = resampler.resample(f)
        if not isinstance(c_f, list):
            c_f = [c_f]
        for p in c_f:
            frames.append(p.to_ndarray()[0])
    for p in resampler.resample(None):
        frames.append(p.to_ndarray()[0])
    c.close()
    audio = np.concatenate(frames)

    flowing_audio = compress_dead_silence(audio, sr=SR)
    sf.write(str(temp_wav), flowing_audio, SR)
    master_audio_file(temp_wav, final_mp3)

    temp_mp3.unlink(missing_ok=True)
    temp_wav.unlink(missing_ok=True)
    print(f"Generated {final_mp3.name} ({len(flowing_audio)/SR:.2f}s)")
    return final_mp3

async def main():
    samples = [
        {
            "type": "edge",
            "title": "1_Super_Smooth_Hindi_Female_Swara",
            "voice": "hi-IN-SwaraNeural",
            "rate": "+4%",
            "pitch": "+1Hz",
            "text": "नमस्ते जी! बेस्टज़ोन में आपका बहुत स्वागत है, कहिए आज मैं आपकी क्या सेवा करूँ?"
        },
        {
            "type": "edge",
            "title": "2_Super_Smooth_Hindi_Male_Madhur",
            "voice": "hi-IN-MadhurNeural",
            "rate": "+3%",
            "pitch": "-1Hz",
            "text": "हेलो नमस्कार जी! बेस्टज़ोन में आपका स्वागत है, बताइए आज मैं आपकी क्या सहायता करूँ?"
        },
        {
            "type": "edge",
            "title": "3_Super_Smooth_Gujarati_Female_Dhwani",
            "voice": "gu-IN-DhwaniNeural",
            "rate": "+3%",
            "pitch": "+1Hz",
            "text": "નમસ્તે જી! બેસ્ટઝોનમાં તમારું ખૂબ સ્વાગત છે, કહો આજે હું તમારી શું મદદ કરી શકું?"
        },
        {
            "type": "gtts",
            "title": "4_Google_WaveNet_Hindi_Female",
            "lang": "hi",
            "text": "नमस्ते, बेस्टज़ोन में आपका बहुत स्वागत है। कहिए आज मैं आपकी क्या सहायता करूँ?"
        }
    ]

    for s in samples:
        if s["type"] == "edge":
            await synthesize_edge(s["title"], s["voice"], s["text"], rate=s["rate"], pitch=s["pitch"])
        else:
            synthesize_gtts(s["title"], s["lang"], s["text"])

    print("All Super Smooth Mastered Samples Ready on Desktop!")

if __name__ == "__main__":
    asyncio.run(main())
