"""Phone caller -> local STT -> local Qwen -> Windows voice.

This is the fast local call-bot path. It does not call ChatGPT/OpenAI.
"""
import argparse
import collections
import datetime as dt
import difflib
import json
import os
import queue
import re
import signal
import sys
import threading
import time
import urllib.request
from pathlib import Path

import numpy as np
from faster_whisper import WhisperModel

from audio_bridge import ROOT, PhoneAudio, adb, devices
from cable_voice import CableVoice

FAST_QWEN = ROOT.parent / "Fast Qwen Chat"
sys.path.insert(0, str(FAST_QWEN))
try:
    from voice import SpeechChunks, Voice  # noqa: E402
except ImportError:
    SpeechChunks = None
    Voice = None

MODEL = "qwen3.5:4b"
OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
SYSTEM = (
    "You are a fast phone-call assistant. Reply directly in 1 short sentence "
    "unless the caller asks for detail. Match the caller's language. "
    "Keep answers easy to speak aloud."
)
LOG_DIR = ROOT / "qwen_call_logs"
LOG_DIR.mkdir(exist_ok=True)
STATUS = ROOT / "qwen_call_status.json"
STATUS_WRITE_LOCK = threading.Lock()

FAQS = [
    (("hello", "hi", "hey"), "Hello! How can I help you today?"),
    (("how are you", "how are you doing"), "I am doing well, thank you. How can I help you?"),
    (("what is your name", "whats your name", "who are you"), "I am your local Qwen voice assistant."),
    (("what can you do", "how can you help me"), "I can answer common questions quickly over this call."),
    (("what are your working hours", "what time are you open", "when are you open"), "We are available Monday to Saturday, from nine A M to six P M."),
    (("where are you located", "what is your location", "where is your office"), "Our test location is Ahmedabad, Gujarat."),
    (("how can i contact you", "what is your contact number", "how do i contact you"), "You can contact us on this phone number."),
    (("what is the price", "how much does it cost", "tell me the price"), "Pricing depends on the service. Our team will share the exact details."),
    (("can i book an appointment", "book an appointment", "i want an appointment"), "Yes. Please share your preferred date and time."),
    (("thank you", "thanks", "bye", "goodbye"), "You are welcome. Have a nice day!"),
    (("can you understand hindi",), "Yes, I understand Hindi very well."),
    (("can you understand gujarati",), "Yes, I understand Gujarati very well."),
]

# High-signal intent words make the small, fast STT model tolerant of harmless
# wording errors without letting unrelated speech trigger an answer.  The
# integer points to the canonical answer in FAQS.
FAQ_INTENTS = [
    (2, lambda words: ("name" in words and bool(words & {"what", "your", "you"})) or {"who", "you"} <= words, "intent:name"),
    (3, lambda words: {"what", "you", "do"} <= words or {"help", "me"} <= words, "intent:capabilities"),
    (4, lambda words: bool(words & {"hours", "timing", "timings"}) or ("open" in words and bool(words & {"when", "time"})), "intent:hours"),
    (5, lambda words: bool(words & {"location", "located", "address", "office"}), "intent:location"),
    (6, lambda words: "contact" in words or ({"phone", "number"} <= words) or ({"reach", "you"} <= words), "intent:contact"),
    (7, lambda words: bool(words & {"price", "pricing", "cost", "charge", "charges", "fee", "fees"}), "intent:price"),
    (8, lambda words: "appointment" in words or ("book" in words and bool(words & {"date", "time", "meeting"})), "intent:appointment"),
    (9, lambda words: bool(words & {"thanks", "goodbye", "bye"}) or "thank" in words, "intent:thanks"),
]


def normalize_faq_text(value):
    value = re.sub(r"[^a-z0-9\s]", " ", value.lower())
    return re.sub(r"\s+", " ", value).strip()


def faq_answer(question):
    answer, score, _ = faq_match(question)
    return answer if score >= 0.84 else None


def faq_match(question):
    normalized = normalize_faq_text(question)
    for aliases, answer in FAQS:
        for alias in aliases:
            normalized_alias = normalize_faq_text(alias)
            if normalized == normalized_alias:
                return answer, 1.0, normalized_alias
    for aliases, answer in FAQS:
        for alias in aliases:
            normalized_alias = normalize_faq_text(alias)
            if len(normalized_alias.split()) >= 2 and normalized_alias in normalized:
                return answer, 0.96, normalized_alias
    question_tokens = set(normalized.split())
    for faq_index, matches, label in FAQ_INTENTS:
        if matches(question_tokens):
            return FAQS[faq_index][1], 0.91, label
    best = (None, 0.0, None)
    for aliases, answer in FAQS:
        for alias in aliases:
            normalized_alias = normalize_faq_text(alias)
            alias_tokens = set(normalized_alias.split())
            sequence_score = difflib.SequenceMatcher(None, normalized, normalized_alias).ratio()
            token_score = (
                2.0 * len(question_tokens & alias_tokens) / (len(question_tokens) + len(alias_tokens))
                if question_tokens and alias_tokens else 0.0
            )
            score = max(sequence_score, token_score)
            if score > best[1]:
                best = (answer, score, normalized_alias)
    return best


def write_status(**fields):
    data = {"updated": dt.datetime.now().isoformat(), **fields}
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    tmp = STATUS.with_name(
        f"{STATUS.stem}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    # Voice, capture and STT callbacks can update status concurrently. Keep the
    # diagnostic file from ever stopping the live call path.
    with STATUS_WRITE_LOCK:
        for attempt in range(5):
            try:
                tmp.write_text(payload, encoding="utf-8")
                os.replace(tmp, STATUS)
                return True
            except OSError as exc:
                if attempt == 4:
                    print(f"WARNING: status update failed: {exc}", file=sys.stderr)
                    return False
                time.sleep(0.02 * (attempt + 1))
            finally:
                if tmp.exists():
                    try:
                        tmp.unlink()
                    except OSError:
                        pass


def choose_serial(preferred=None):
    connected = devices()
    if preferred and preferred in connected:
        return preferred
    if not connected:
        raise RuntimeError("No ADB phone connected")
    hardware = os.environ.get("CALLTEXT_PHONE_SERIAL", "K7NVQWYTWCFIPN85")
    for serial in connected:
        if hardware in serial:
            return serial
    return connected[0]


def phone_call_state(serial):
    """Return Android telephony state: 0 idle, 1 ringing, 2 off-hook."""
    result = adb("shell", "dumpsys", "telephony.registry", serial=serial, timeout=6)
    states = [int(value) for value in re.findall(r"\bmCallState\s*=\s*(\d+)", result)]
    return max(states) if states else None


def qwen_request(messages, stream=True):
    payload = {
        "model": MODEL,
        "messages": messages,
        "think": False,
        "stream": stream,
        "keep_alive": "24h",
    }
    req = urllib.request.Request(
        OLLAMA_URL,
        json.dumps(payload).encode("utf-8"),
        {"Content-Type": "application/json"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return opener.open(req, timeout=180)


def warm_qwen():
    with qwen_request([], stream=False) as response:
        result = json.load(response)
        if result.get("error"):
            raise RuntimeError(result["error"])


def stream_qwen(messages, on_text):
    started = time.perf_counter()
    first = None
    parts = []
    with qwen_request(messages, stream=True) as response:
        for raw in response:
            if not raw.strip():
                continue
            item = json.loads(raw)
            if item.get("error"):
                raise RuntimeError(item["error"])
            text = item.get("message", {}).get("content", "")
            if text:
                if first is None:
                    first = time.perf_counter() - started
                parts.append(text)
                on_text(text)
            if item.get("done"):
                return "".join(parts), first, time.perf_counter() - started
    raise RuntimeError("Qwen stream ended before completion")


class Transcript:
    def __init__(self):
        stamp = dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")
        self.path = LOG_DIR / f"qwen_call_{stamp}.txt"
        self.lock = threading.Lock()
        self.append(f"Qwen call bot | {dt.datetime.now():%Y-%m-%d %H:%M:%S}\n\n")

    def append(self, value):
        with self.lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(value)
                handle.flush()
                os.fsync(handle.fileno())


class FastSegmenter:
    """Adaptive low-latency VAD which rejects steady noise and short spikes."""
    def __init__(self, emit, threshold=0.0015):
        self.emit = emit
        self.threshold = threshold
        self.noise_floor = threshold / 3.0
        self.dynamic_threshold = threshold
        self.pre = collections.deque(maxlen=3)
        self.attack = []
        self.parts = []
        self.silence = 0
        self.voiced_frames = 0
        self.start = None

    def feed(self, samples):
        rms = float(np.sqrt(np.mean(samples * samples))) if len(samples) else 0.0
        if not self.parts:
            self.dynamic_threshold = max(self.threshold, self.noise_floor * 3.2)
        else:
            self.dynamic_threshold = max(self.threshold * 0.8, self.noise_floor * 2.2)
        voiced = rms >= self.dynamic_threshold
        if voiced:
            if not self.parts:
                self.attack.append(samples)
                if len(self.attack) < 2:
                    return rms
                self.start = dt.datetime.now() - dt.timedelta(seconds=(len(self.pre) + len(self.attack)) * 0.1)
                self.parts.extend(self.pre)
                self.parts.extend(self.attack)
                self.pre.clear()
                self.attack = []
                self.voiced_frames = 2
            else:
                self.parts.append(samples)
                self.voiced_frames += 1
            self.silence = 0
        elif self.parts:
            self.parts.append(samples)
            self.silence += 1
        else:
            self.attack = []
            self.noise_floor = 0.97 * self.noise_floor + 0.03 * min(rms, self.threshold * 2.0)
            self.pre.append(samples)
        if self.parts and (self.silence >= 4 or len(self.parts) >= 35):
            self.flush()
        return rms

    def flush(self):
        if self.parts:
            audio = np.concatenate(self.parts)
            voiced_seconds = len(audio) / 16000.0
            if voiced_seconds >= 0.35 and self.voiced_frames >= 3:
                self.emit(audio, self.start or dt.datetime.now())
        self.parts = []
        self.silence = 0
        self.voiced_frames = 0
        self.start = None


class CallQwenBot:
    def __init__(self, args):
        self.args = args
        self.jobs = queue.Queue(maxsize=3)
        self.stop = threading.Event()
        self.history = []
        self.transcript = Transcript()
        self.last_status = {"state": "starting", "file": str(self.transcript.path)}
        self.status_lock = threading.Lock()
        self.ignore_windows = collections.deque(maxlen=16)
        self.voice_active = threading.Event()
        self.voice_started_at = None
        self.call_active = threading.Event()
        self.last_accepted_question = None
        self.last_accepted_at = 0.0
        self.voice_ready = threading.Event()
        self.voice = CableVoice(self.on_voice_event, args.voice_device) if args.voice_device else Voice(self.on_voice_event)
        self.speech = SpeechChunks(self.speak)

    def set_status(self, **fields):
        with self.status_lock:
            self.last_status.update(fields)
            snapshot = dict(self.last_status)
        write_status(**snapshot)

    def on_voice_event(self, event):
        event_type = event.get("type")
        if event_type == "ready":
            self.voice_ready.set()
            self.set_status(voice="ready", voice_name=event.get("selected"))
        elif event_type == "error":
            self.voice_active.clear()
            self.voice_started_at = None
            self.set_status(voice="error", voice_error=event.get("message") or "Unknown voice error")
        elif event_type == "played":
            ended = dt.datetime.now()
            if self.voice_started_at is not None:
                self.ignore_windows.append((self.voice_started_at - dt.timedelta(seconds=0.1), ended + dt.timedelta(seconds=0.35)))
            self.voice_active.clear()
            self.voice_started_at = None
            self.set_status(voice="ready", voice_device=event.get("device"), last_voice_played=dt.datetime.now().isoformat())
        elif event_type == "playing":
            self.voice_started_at = dt.datetime.now()
            self.voice_active.set()
            self.set_status(voice="playing", voice_device=event.get("device"))
        elif event_type == "playback_stopped":
            self.voice_active.clear()
            self.voice_started_at = None
            self.set_status(voice="ready", voice_device=event.get("device"))
        elif event_type == "output_ready":
            self.set_status(voice="ready", voice_device=event.get("device"))
        elif event_type == "route":
            self.set_status(voice_route=event.get("message"))

    def speak(self, text):
        if not self.args.no_voice and self.voice_ready.is_set():
            self.voice.send("speak", text=text)

    def load(self):
        self.set_status(state="loading_stt")
        self.stt = WhisperModel(
            self.args.stt_model,
            device="cpu",
            compute_type="int8",
            cpu_threads=min(8, os.cpu_count() or 4),
            download_root=str(ROOT / "models"),
            local_files_only=True,
        )
        if not self.args.faq_only:
            self.set_status(state="warming_qwen")
            warm_qwen()
        self.voice_ready.wait(5)
        self.set_status(state="ready", mode="faq_only" if self.args.faq_only else "qwen")

    def wait_for_answered_call(self):
        last_status = None
        while not self.stop.is_set():
            try:
                serial = choose_serial(self.args.serial)
                call_state = phone_call_state(serial)
                state_name = {
                    0: "waiting_for_call",
                    1: "ringing_waiting_answer",
                    2: "call_answered",
                }.get(call_state, "waiting_for_call_state")
                current = (serial, call_state, state_name)
                if current != last_status:
                    self.set_status(
                        state=state_name,
                        serial=serial,
                        call_state=call_state,
                        audio_error=None,
                        reconnect_attempt=0,
                    )
                    last_status = current
                if call_state == 2:
                    return serial
            except Exception as exc:
                current = (None, None, str(exc))
                if current != last_status:
                    self.set_status(
                        state="waiting_for_phone",
                        call_state=None,
                        audio_error=str(exc) or type(exc).__name__,
                    )
                    last_status = current
            self.stop.wait(0.8)
        return None

    def capture_loop(self, serial=None):
        serial = serial or choose_serial(self.args.serial)
        self.set_status(state="connecting_phone_audio", serial=serial)
        capture = PhoneAudio(serial, self.args.source).start()

        def emit(audio, timestamp):
            dropped = 0
            while self.jobs.qsize() >= 1:
                try:
                    kind, _, _ = self.jobs.get_nowait()
                    self.jobs.task_done()
                    if kind == "audio":
                        dropped += 1
                except queue.Empty:
                    break
            self.jobs.put_nowait(("audio", audio, timestamp))
            if dropped:
                self.set_status(dropped_stale_chunks=dropped)

        segmenter = FastSegmenter(emit, threshold=self.args.threshold)
        chunks = 0
        next_call_check = time.monotonic()
        idle_confirmations = 0
        try:
            self.set_status(state="listening", serial=serial, call_state=2)
            while not self.stop.is_set():
                raw = capture.read(capture.rate // 10)
                samples = np.frombuffer(raw, "<i2").astype(np.float32).reshape(-1, capture.channels).mean(axis=1) / 32768.0
                if capture.rate == 48000:
                    samples = samples.reshape(-1, 3).mean(axis=1)
                elif capture.rate != 16000:
                    x = np.arange(1600) * capture.rate / 16000
                    samples = np.interp(x, np.arange(len(samples)), samples).astype(np.float32)
                rms = segmenter.feed(samples)
                chunks += 1
                if chunks % 5 == 0:
                    self.set_status(state="listening", rms=round(rms, 6), pending=self.jobs.qsize())
                if self.args.auto_call_state and time.monotonic() >= next_call_check:
                    call_state = phone_call_state(serial)
                    next_call_check = time.monotonic() + 0.7
                    if call_state == 0:
                        idle_confirmations += 1
                        if idle_confirmations >= 2:
                            return "call_ended"
                    else:
                        idle_confirmations = 0
                    self.set_status(state="listening", call_state=call_state)
        finally:
            try:
                segmenter.flush()
            finally:
                capture.close()

    def worker_loop(self):
        while not self.stop.is_set():
            kind, audio, timestamp = self.jobs.get()
            if kind == "end":
                break
            try:
                self.process_audio(audio, timestamp)
            except Exception as exc:
                self.set_status(state="error", error=str(exc))
                self.transcript.append(f"\n[ERROR] {exc}\n")
            finally:
                self.jobs.task_done()

    def process_audio(self, audio, timestamp):
        if self.args.auto_call_state and not self.call_active.is_set():
            self.set_status(state="waiting_for_call", skipped="call_not_active")
            return
        audio_end = timestamp + dt.timedelta(seconds=len(audio) / 16000.0)
        if self.voice_active.is_set() or any(timestamp <= end and audio_end >= start for start, end in self.ignore_windows):
            self.set_status(state="listening", skipped="bot_voice_window")
            return
        began = time.perf_counter()
        audio_rms = float(np.sqrt(np.mean(audio * audio))) if len(audio) else 0.0
        audio_peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
        self.set_status(state="stt", audio_seconds=round(len(audio) / 16000.0, 2), audio_rms=round(audio_rms, 6))
        segments, _ = self.stt.transcribe(
            audio,
            language=None if self.args.language == "auto" else self.args.language,
            beam_size=1,
            vad_filter=True,
            vad_parameters={"min_speech_duration_ms": 250, "min_silence_duration_ms": 180, "speech_pad_ms": 60},
            condition_on_previous_text=False,
            temperature=0,
            no_speech_threshold=0.50,
            log_prob_threshold=-0.85,
            compression_ratio_threshold=2.2,
            hallucination_silence_threshold=0.5,
            hotwords="hello how are you what is your name who are you working hours location contact price appointment thank you goodbye",
        )
        candidates = [
            segment for segment in segments
            if segment.no_speech_prob < 0.55
            and segment.avg_logprob >= -0.85
            and segment.compression_ratio < 2.2
        ]
        question = " ".join(segment.text.strip() for segment in candidates).strip()
        stt_seconds = time.perf_counter() - began
        if self.args.auto_call_state and not self.call_active.is_set():
            self.set_status(state="waiting_for_call", skipped="call_ended_during_stt")
            return
        if not question or re.fullmatch(r"(?i)\[(blank_audio|silence|music|no_speech)\]", question):
            self.set_status(
                state="listening",
                last_stt_seconds=round(stt_seconds, 3),
                skipped="noise_or_low_confidence",
                audio_rms=round(audio_rms, 6),
                audio_peak=round(audio_peak, 6),
            )
            return

        caller_line = f"[{timestamp:%H:%M:%S}] Caller: {question}\n"
        print(caller_line, end="", flush=True)
        self.transcript.append(caller_line)
        self.voice.send("stop")
        self.speech.clear()

        if self.args.faq_only:
            answer, match_score, matched_alias = faq_match(question)
            if answer is None or match_score < 0.84:
                self.transcript.append(
                    f"[{dt.datetime.now():%H:%M:%S}] IGNORED: no confident FAQ match ({match_score:.2f})\n\n"
                )
                self.set_status(
                    state="listening",
                    mode="faq_only",
                    question=question,
                    faq_matched=False,
                    faq_score=round(match_score, 3),
                    skipped="no_confident_faq_match",
                    stt_seconds=round(stt_seconds, 3),
                )
                return
            normalized_question = normalize_faq_text(question)
            now = time.monotonic()
            if normalized_question == self.last_accepted_question and now - self.last_accepted_at < 4.0:
                self.set_status(state="listening", skipped="duplicate_question", question=question)
                return
            self.last_accepted_question = normalized_question
            self.last_accepted_at = now
            print(answer + "\n", flush=True)
            self.speak(answer)
            answer_line = f"[{dt.datetime.now():%H:%M:%S}] FAQ: {answer}\n\n"
            self.transcript.append(answer_line)
            self.set_status(
                state="listening",
                mode="faq_only",
                question=question,
                answer=answer,
                faq_matched=True,
                faq_score=round(match_score, 3),
                faq_alias=matched_alias,
                stt_seconds=round(stt_seconds, 3),
            )
            return

        self.set_status(state="qwen", question=question, stt_seconds=round(stt_seconds, 3))

        messages = [{"role": "system", "content": SYSTEM}] + self.history[-6:] + [{"role": "user", "content": question}]
        answer_parts = []

        def on_text(text):
            answer_parts.append(text)
            print(text, end="", flush=True)
            self.speech.feed(text)

        q_started = time.perf_counter()
        answer, first, total = stream_qwen(messages, on_text)
        self.speech.feed("", final=True)
        print("\n", flush=True)
        qwen_seconds = time.perf_counter() - q_started
        answer_line = f"[{dt.datetime.now():%H:%M:%S}] Qwen: {answer.strip()}\n\n"
        self.transcript.append(answer_line)
        self.history.extend([{"role": "user", "content": question}, {"role": "assistant", "content": answer}])
        self.history = self.history[-6:]
        self.set_status(
            state="listening",
            question=question,
            answer=answer.strip(),
            stt_seconds=round(stt_seconds, 3),
            qwen_first_seconds=round(first or 0, 3),
            qwen_full_seconds=round(total, 3),
            qwen_total_wall=round(qwen_seconds, 3),
        )

    def run(self):
        self.load()
        signal.signal(signal.SIGINT, lambda *_: self.stop.set())
        worker = threading.Thread(target=self.worker_loop, daemon=True)
        worker.start()
        try:
            while not self.stop.is_set():
                serial = self.wait_for_answered_call() if self.args.auto_call_state else choose_serial(self.args.serial)
                if serial is None or self.stop.is_set():
                    break

                self.call_active.set()
                self.last_accepted_question = None
                self.last_accepted_at = 0.0
                self.transcript.append(f"\n[CALL START {dt.datetime.now():%Y-%m-%d %H:%M:%S}] {serial}\n")
                reconnect_attempt = 0

                while self.call_active.is_set() and not self.stop.is_set():
                    try:
                        reason = self.capture_loop(serial)
                        reconnect_attempt = 0
                        if reason == "call_ended":
                            break
                    except Exception as exc:
                        if self.stop.is_set():
                            break
                        message = str(exc) or type(exc).__name__
                        try:
                            call_state = phone_call_state(serial)
                        except Exception:
                            call_state = None
                        if self.args.auto_call_state and call_state == 0:
                            break
                        reconnect_attempt += 1
                        delay = min(5.0, 0.75 * reconnect_attempt)
                        self.transcript.append(
                            f"\n[AUDIO RECONNECT {dt.datetime.now():%H:%M:%S}] {message}; retry in {delay:.2f}s\n"
                        )
                        self.set_status(
                            state="reconnecting_phone_audio",
                            call_state=call_state,
                            reconnect_attempt=reconnect_attempt,
                            reconnect_delay_seconds=round(delay, 2),
                            audio_error=message,
                        )
                        self.stop.wait(delay)

                self.call_active.clear()
                self.voice.send("stop")
                self.speech.clear()
                while True:
                    try:
                        self.jobs.get_nowait()
                        self.jobs.task_done()
                    except queue.Empty:
                        break
                self.transcript.append(f"[CALL END {dt.datetime.now():%Y-%m-%d %H:%M:%S}]\n")
                if self.args.auto_call_state and not self.stop.is_set():
                    self.set_status(
                        state="waiting_for_call",
                        call_state=0,
                        pending=0,
                        audio_error=None,
                        reconnect_attempt=0,
                    )
        finally:
            self.stop.set()
            while True:
                try:
                    self.jobs.get_nowait()
                    self.jobs.task_done()
                except queue.Empty:
                    break
            self.jobs.put(("end", None, None))
            worker.join(timeout=3)
            self.voice.close()
            self.set_status(state="stopped")


def benchmark(question):
    started = time.perf_counter()
    warm_qwen()
    warm = time.perf_counter() - started
    answer, first, total = stream_qwen(
        [{"role": "system", "content": SYSTEM}, {"role": "user", "content": question}],
        lambda text: None,
    )
    print(json.dumps({
        "answer": answer,
        "warmup_seconds": warm,
        "first_text_seconds": first,
        "full_answer_seconds": total,
    }, ensure_ascii=False))


def offline_audio_test(path, seconds, stt_model, language):
    audio_path = Path(path)
    raw = np.fromfile(audio_path, dtype="<i2")
    if raw.ndim != 1 or len(raw) < 2:
        raise RuntimeError("Audio file is empty")
    stereo = raw.reshape(-1, 2).astype(np.float32)
    mono48 = stereo.mean(axis=1) / 32768.0
    mono16 = mono48[: (len(mono48) // 3) * 3].reshape(-1, 3).mean(axis=1).astype(np.float32)
    if seconds:
        mono16 = mono16[: int(seconds * 16000)]
    stt = WhisperModel(
        stt_model,
        device="cpu",
        compute_type="int8",
        cpu_threads=4,
        download_root=str(ROOT / "models"),
        local_files_only=True,
    )
    began = time.perf_counter()
    segments, _ = stt.transcribe(
        mono16,
        language=None if language == "auto" else language,
        beam_size=1,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 250},
        condition_on_previous_text=False,
        temperature=0,
    )
    question = " ".join(s.text.strip() for s in segments if s.no_speech_prob < 0.8).strip()
    stt_seconds = time.perf_counter() - began
    q_answer, q_first, q_total = stream_qwen(
        [{"role": "system", "content": SYSTEM}, {"role": "user", "content": question}],
        lambda text: None,
    )
    print(json.dumps({
        "audio_seconds": round(len(mono16) / 16000, 3),
        "stt_seconds": round(stt_seconds, 3),
        "question": question,
        "qwen_first_seconds": round(q_first or 0, 3),
        "qwen_full_seconds": round(q_total, 3),
        "answer": q_answer,
    }, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description="Caller speech to local Qwen voice bot")
    parser.add_argument("--serial")
    parser.add_argument("--source", default="voice-call-downlink", choices=["voice-call-downlink", "voice-call"])
    parser.add_argument("--stt-model", default="small")
    parser.add_argument("--language", default="en")
    parser.add_argument("--threshold", type=float, default=0.0015)
    parser.add_argument("--no-voice", action="store_true")
    parser.add_argument("--voice-device", help="Route bot voice to this Windows output device, e.g. CABLE Input")
    parser.add_argument("--faq-only", action="store_true", help="Answer from local FAQ data and skip Qwen")
    parser.add_argument("--auto-call-state", action="store_true", help="Capture only while Android reports an answered call")
    parser.add_argument("--benchmark", metavar="QUESTION")
    parser.add_argument("--offline-audio-test")
    parser.add_argument("--offline-seconds", type=float, default=6.0)
    args = parser.parse_args()
    if args.benchmark:
        benchmark(args.benchmark)
    elif args.offline_audio_test:
        offline_audio_test(args.offline_audio_test, args.offline_seconds, args.stt_model, args.language)
    else:
        CallQwenBot(args).run()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        write_status(state="error", error=str(exc))
        print("ERROR:", exc, file=sys.stderr)
        sys.exit(1)
