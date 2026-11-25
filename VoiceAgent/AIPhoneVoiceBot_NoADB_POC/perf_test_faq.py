"""Local timing/accuracy test for the live FAQ path (no phone, no CABLE output).

Simulates a caller in real time:
    synthetic phone-quality Hindi speech -> LiveFaqSegmenter -> worker STT
    -> FAQ matcher -> cached TTS lookup -> "playing"

Question audio is generated once with a different voice than the bot
(hi-IN-MadhurNeural) and cached in perf_test_audio/. Nothing is played to the
call: the voice object is a stand-in that reports "playing" as soon as a cached
answer would start, exactly like CableVoice's cache-hit path.

Usage:
    python perf_test_faq.py                      # test this folder's bot
    python perf_test_faq.py --bot-dir <folder>   # test another copy (before/after)
"""
import argparse
import asyncio
import hashlib
import importlib
import json
import os
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path
from types import SimpleNamespace

import numpy as np

HERE = Path(__file__).resolve().parent
AUDIO_DIR = HERE / "perf_test_audio"
TEST_VOICE = "hi-IN-MadhurNeural"

# (id, kind, spoken text, expected answer)
#   ("bz", product or None, question type index) -> BestZone CSV answer
#   ("talk", first question of a small-talk FAQ) -> that answer
#   None -> must not give a FAQ answer
CASES = [
    ("bz_price", "short Hindi", "वॉल हुक की कीमत क्या है?", ("bz", "wall hook", 0)),
    ("bz_stock", "short Hindi", "लंच बॉक्स स्टॉक में है क्या?", ("bz", "lunch box", 1)),
    ("bz_colour", "short Hindi", "सोप डिस्पेंसर किन रंगों में मिलता है?", ("bz", "soap dispenser", 8)),
    ("bz_delivery_long", "long Hindi",
     "मुझे लॉन्ड्री बास्केट मंगवानी है, उसकी डिलीवरी कितने दिन में हो जाएगी?", ("bz", "laundry basket", 15)),
    ("bz_damage_long", "long Hindi", "मेरा सोप होल्डर टूटा हुआ आया है, अब मैं क्या करूँ?", ("bz", "soap holder", 21)),
    ("bz_hinglish_shipping", "Hinglish", "cable organizer का shipping charge कितना है?", ("bz", "cable organizer", 16)),
    ("bz_hinglish_gst", "Hinglish", "spice organizer का GST bill मिलेगा?", ("bz", "spice organizer", 18)),
    ("bz_roman_price", "Roman Hindi", "dustbin ka rate kya hai", ("bz", "dustbin", 0)),
    ("bz_type_only", "keyword-only", "जीएसटी बिल", ("bz", None, 18)),
    ("bz_warranty", "STT error", "क्लीनिंग ब्रश की वारंटी कितने समय की है?", ("bz", "cleaning brush", 22)),
    ("talk_hello", "short Hindi", "नमस्ते", ("talk", "नमस्ते")),
    ("talk_thanks", "keyword-only", "धन्यवाद", ("talk", "धन्यवाद")),
    ("ambiguous_box", "ambiguous", "मुझे बॉक्स के बारे में पूछना है", None),
    ("unrelated", "ambiguous", "आज मौसम कैसा है", None),
]


def expected_answer(spec):
    import bestzone_faq
    from hindi_faq import HINDI_FAQS

    if spec is None:
        return None
    if spec[0] == "talk":
        return next(answer for aliases, answer in HINDI_FAQS if aliases[0] == spec[1])
    catalog = bestzone_faq.load_catalog()
    _, product, intent = spec
    if product is None:
        return catalog["templates"][intent]
    return catalog["answers"][f"{catalog['products'].index(product)}|{intent}"]


def synth(case_id, text):
    """Generate (once) a 16 kHz phone-band version of the question."""
    AUDIO_DIR.mkdir(exist_ok=True)
    digest = hashlib.sha1(f"{TEST_VOICE}:{text}".encode("utf-8")).hexdigest()[:10]
    path = AUDIO_DIR / f"{case_id}_{digest}.wav"
    if not path.exists():
        import av
        import edge_tts

        handle, name = tempfile.mkstemp(suffix=".mp3")
        os.close(handle)
        mp3 = Path(name)
        try:
            asyncio.run(edge_tts.Communicate(text, TEST_VOICE).save(str(mp3)))
            container = av.open(str(mp3))
            resampler = av.AudioResampler(format="s16", layout="mono", rate=8000)
            pieces = []
            for frame in container.decode(audio=0):
                for item in resampler.resample(frame):
                    pieces.append(item.to_ndarray()[0])
            for item in resampler.resample(None):
                pieces.append(item.to_ndarray()[0])
            container.close()
        finally:
            mp3.unlink(missing_ok=True)
        pcm8 = np.concatenate(pieces).astype(np.float32) / 32768.0
        # Telephone band (300-3400 Hz) at 8 kHz, then back to 16 kHz.
        spectrum = np.fft.rfft(pcm8)
        freqs = np.fft.rfftfreq(len(pcm8), 1 / 8000)
        spectrum[(freqs < 300) | (freqs > 3400)] = 0
        pcm8 = np.fft.irfft(spectrum, len(pcm8)).astype(np.float32)
        pcm16 = np.interp(np.arange(len(pcm8) * 2) / 2, np.arange(len(pcm8)), pcm8)
        peak = float(np.max(np.abs(pcm16))) or 1.0
        pcm16 = pcm16 / peak * 0.35
        pcm16 += np.random.default_rng(len(text)).standard_normal(len(pcm16)) * 0.0015
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes((np.clip(pcm16, -1, 1) * 32767).astype("<i2").tobytes())
    with wave.open(str(path), "rb") as wav:
        return np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").astype(np.float32) / 32768.0


class FakeVoice:
    """Stand-in for CableVoice: cache-hit answers report "playing" at once."""

    def __init__(self, bot_dir, on_event):
        self.on_event = on_event
        self.cache_dir = bot_dir / "hindi_voice_cache"
        self.pcm_cache = {}
        self.spoken = []
        try:  # answer pieces already recorded on disk count as cached, like CableVoice
            import bestzone_faq
            for part in bestzone_faq.tts_parts():
                if self.is_cached("hi", part):
                    self.pcm_cache[("hi", part)] = b"\x00" * 4
        except ImportError:
            pass

    def is_cached(self, language, text):
        voice = {"hi": "hi-IN-SwaraNeural", "gu": "gu-IN-DhwaniNeural"}.get(language, "en-IN-NeerjaNeural")
        digest = hashlib.sha256((voice + ":v3:" + text).encode("utf-8")).hexdigest()
        return (self.cache_dir / f"{digest}.wav").exists()

    def send(self, action, **fields):
        if action != "speak":
            return
        text, language = fields.get("text", ""), fields.get("language", "en")
        cached = (language, text) in self.pcm_cache or self.is_cached(language, text)
        if cached:
            self.pcm_cache[(language, text)] = b""
        self.spoken.append((time.perf_counter(), text, cached))

        def play():
            self.on_event({"type": "playing", "device": "test"})
            time.sleep(0.05)
            self.on_event({"type": "played", "device": "test"})

        threading.Thread(target=play, daemon=True).start()

    def close(self):
        pass


def load_bot(bot_dir, workdir):
    sys.path.insert(0, str(bot_dir))
    sys.path.insert(0, str(bot_dir.parent / "AIPhoneVoiceBot"))
    module = importlib.import_module("phone_link_no_adb_bot")
    module.STATUS = workdir / "status.json"
    module.LOG_DIR = workdir
    bot = module.FrontbitLinkNoAdbBot(SimpleNamespace(no_voice=True, loopback_name=None, threshold=0.0015,
                                                    auto_answer_delay=0.0))
    started = time.perf_counter()
    bot.load()
    load_seconds = time.perf_counter() - started
    bot.voice = FakeVoice(bot_dir, bot.on_voice_event)
    bot.voice_ready.set()
    bot.call_active = True
    bot.call_detection_source = "hands_free_session"
    return module, bot, load_seconds


def run_case(module, bot, audio):
    """Feed one question in real time and return (eos_time, answer info)."""
    threshold = max(0.0015, module.DIRECT_HINDI_VAD_THRESHOLD)
    extra = {"end_ratio": module.SPEECH_END_RATIO} if hasattr(module, "SPEECH_END_RATIO") else {}
    segmenter = module.LiveFaqSegmenter(bot.enqueue, bot.on_speech_start, threshold=threshold,
                                        silence_frames=getattr(module, "HINDI_ENDPOINT_FRAMES", 3),
                                        max_frames=getattr(module, "HINDI_MAX_UTTERANCE_FRAMES", 50),
                                        enable_previews=True, **extra)
    bot.segmenter = segmenter
    bot.last_question = None
    bot.pending_hindi_question = None
    bot.answered_utterances.clear()
    bot.voice.spoken.clear()
    if hasattr(bot, "recent_bot_speech"):
        bot.recent_bot_speech.clear()  # each case is a separate call
    bot.bestzone_product = None
    bot.last_answer_text = None
    frame = 1600
    rng = np.random.default_rng(7)
    lead = (rng.standard_normal(frame * 5) * 0.0005).astype(np.float32)
    tail = (rng.standard_normal(frame * 25) * 0.0005).astype(np.float32)
    stream = np.concatenate([lead, audio, tail]).astype(np.float32)
    eos = None
    clock = time.perf_counter()
    for offset in range(0, len(stream) - frame + 1, frame):
        clock += 0.1
        delay = clock - time.perf_counter()
        if delay > 0:
            time.sleep(delay)
        chunk = stream[offset:offset + frame]
        segmenter.feed(chunk)
        if float(np.sqrt(np.mean(chunk * chunk))) >= segmenter.dynamic_threshold:
            eos = time.perf_counter()
    # Let the worker finish (cap 15 s).
    deadline = time.perf_counter() + 15
    while time.perf_counter() < deadline and (bot.jobs.qsize() or getattr(bot, "_busy", False)):
        time.sleep(0.02)
    time.sleep(0.3)
    return eos, list(bot.voice.spoken)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bot-dir", default=str(HERE))
    parser.add_argument("--report", default=str(HERE / "perf_test_report.json"))
    args = parser.parse_args()
    bot_dir = Path(args.bot_dir).resolve()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    audios = {case_id: synth(case_id, text) for case_id, _, text, _ in CASES}
    workdir = Path(tempfile.mkdtemp(prefix="faq_perf_"))
    module, bot, load_seconds = load_bot(bot_dir, workdir)

    # Track the worker being busy so we know when a case is complete.
    original = bot.process_audio

    def tracked(*a, **k):
        bot._busy = True
        try:
            return original(*a, **k)
        finally:
            bot._busy = False

    bot.process_audio = tracked
    threading.Thread(target=bot.worker, daemon=True).start()

    status = getattr(bot, "last_status", {})
    print(f"Bot: {bot_dir}")
    print(f"STT runtime: {status.get('hindi_model')} | load {load_seconds:.1f}s | ready={status.get('ready')}")
    rows = []
    for case_id, kind, text, expected in CASES:
        for key in ("question", "last_timing", "skipped"):
            bot.last_status.pop(key, None)
        eos, spoken = run_case(module, bot, audios[case_id])
        breakdown = bot.last_status.get("last_timing") or {}
        transcript = bot.last_status.get("question") or bot.last_status.get("partial_text") or ""
        first = spoken[0] if spoken else None
        wanted = expected_answer(expected)
        if expected is None:
            # Not a FAQ: silence, "please repeat" or "no answer" are all fine.
            correct = first is None or first[1] in (module.HINDI_REPEAT_PROMPT, module.UNKNOWN_HINDI)
        else:
            correct = first is not None and first[1] == wanted
        latency = round((first[0] - eos) * 1000) if first and eos else None
        rows.append({
            "id": case_id, "kind": kind, "spoken": text, "transcription": transcript,
            "answer": first[1] if first else None, "expected": wanted,
            "correct": correct, "cached": first[2] if first else None,
            "end_of_speech_to_playback_ms": latency,
            "audio_seconds": round(len(audios[case_id]) / 16000, 2),
            "stt_ms": breakdown.get("stt_ms"),
            "queue_wait_ms": breakdown.get("queue_wait_ms"),
            "match_ms": breakdown.get("match_ms"),
            "skipped": bot.last_status.get("skipped") if not first else None,
        })
        mark = "OK " if correct else "BAD"
        print(f"{mark} {case_id:22s} {str(latency):>6} ms  stt={breakdown.get('stt_ms')} "
              f"queue={breakdown.get('queue_wait_ms')} match={breakdown.get('match_ms')} "
              f"cached={rows[-1]['cached']} skipped={rows[-1]['skipped']}  "
              f"heard='{transcript}' -> {(first[1][:30] if first else '-')}")

    normal = [r for r in rows if r["expected"] is not None and r["end_of_speech_to_playback_ms"] is not None]
    fast = [r for r in normal if r["end_of_speech_to_playback_ms"] <= 1500]
    over2 = [r for r in normal if r["end_of_speech_to_playback_ms"] > 2000]
    accuracy = sum(r["correct"] for r in rows) / len(rows)
    latencies = sorted(r["end_of_speech_to_playback_ms"] for r in normal)
    summary = {
        "bot_dir": str(bot_dir),
        "stt_runtime": status.get("hindi_model"),
        "cases": len(rows),
        "accuracy": round(accuracy, 3),
        "normal_answered": len(normal),
        "within_1500ms": f"{len(fast)}/{len(normal)}",
        "over_2000ms": len(over2),
        "median_ms": latencies[len(latencies) // 2] if latencies else None,
        "max_ms": latencies[-1] if latencies else None,
        "pass_80pct_under_1500": bool(normal) and len(fast) / len(normal) >= 0.8,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    Path(args.report).write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2),
                                 encoding="utf-8")


if __name__ == "__main__":
    main()
