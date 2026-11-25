"""ADB-free Frontbit Link FAQ bot proof of concept.

Caller audio is read from a Windows WASAPI loopback endpoint. Production source
files are imported read-only for the tested FAQ matcher, VAD and CABLE voice.
"""
import argparse
import collections
import functools
import datetime as dt
import json
import os
import queue

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
import re
import signal
import sys
import threading
import time
from pathlib import Path

# Windows redirected streams otherwise use a legacy code page. Hindi text
# raised UnicodeEncodeError before the FAQ answer could be sent to the caller.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import pyaudiowpatch as pa
from faster_whisper import WhisperModel
import faster_whisper.transcribe as _fw_transcribe
from gpu_runtime import configure as configure_gpu
from frontbit_auto_answer import FrontbitAutoAnswer, _adb_env, _get_adb_device, _get_adb_path
configure_gpu()

ROOT = Path(__file__).resolve().parent
PRODUCTION = ROOT if (ROOT / "models").exists() else (ROOT.parent / "AIPhoneVoiceBot")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(PRODUCTION))

from call_qwen_voice_bot import FAQS, faq_match  # noqa: E402
from cable_voice import CableVoice  # noqa: E402
from hindi_faq import HINDI_FAQS, UNKNOWN_HINDI, looks_hindi, match as hindi_match  # noqa: E402
from gujarati_faq import GUJARATI_FAQS, UNKNOWN_GUJARATI, looks_gujarati, match as gujarati_match  # noqa: E402
from hindi_faq import normalize as hindi_normalize, _phonetic as hindi_phonetic, _word_similarity  # noqa: E402
import bestzone_faq  # noqa: E402
from difflib import SequenceMatcher  # noqa: E402

STATUS = ROOT / "status.json"
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
STATUS_LOCK = threading.Lock()

LANGUAGE_PROMPTS = {
    "en": "Please say your preferred language: English, Hindi, or Gujarati.",
    "hi": "कृपया अपनी पसंदीदा भाषा बोलें: हिंदी, अंग्रेज़ी या गुजराती।",
    "gu": "તમારી પસંદની ભાષા કહો: હિન્દી, અંગ્રેજી અથવા ગુજરાતી.",
}
LANGUAGE_CONFIRMATIONS = {
    "en": "English selected. You can now ask your question.",
    "hi": "हिंदी चुनी गई है। अब आप अपना सवाल पूछ सकते हैं।",
    "gu": "ગુજરાતી પસંદ કરવામાં આવી છે. હવે તમે તમારો પ્રશ્ન પૂછી શકો છો.",
}

# Temporary direct-Hindi mode. The preferred-language prompt and English STT
# path stay available in the file, but are bypassed while this is set to "hi".
FORCE_LANGUAGE = "hi"
HINDI_WELCOME = "बेस्टज़ोन में आपका स्वागत है।"  # used until a greeting is saved in the dashboard
HINDI_REPEAT_PROMPT = "माफ़ कीजिए, मैं आपका सवाल समझ नहीं पाया। कृपया एक बार फिर बोलिए।"
HINDI_TURBO_MODEL = (
    PRODUCTION
    / "models"
    / "models--mobiuslabsgmbh--faster-whisper-large-v3-turbo"
    / "snapshots"
    / "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf"
)
HINDI_SMALL_MODEL = (
    PRODUCTION
    / "models"
    / "models--Systran--faster-whisper-small"
    / "snapshots"
    / "536b0662742c02347bc0e980a01041f333bce120"
)
# Whisper small fine-tuned on Hindi (vasista22/whisper-hindi-small, CTranslate2
# int8). Same speed as the general small model, far cleaner Hindi words
# ("आप मैन्युफैक्चरर हो क्या" instead of "अब मैन्टिट्रर हु क्या"). It runs first;
# the general small model re-hears the question when no FAQ matches.
HINDI_PRIMARY_MODEL = PRODUCTION / "models" / "hindi" / "EtMmohammedHafsati__hi__vasista22__whisper-hindi-small__int8"
if not HINDI_PRIMARY_MODEL.exists():
    for _alt_base in (ROOT.parent / "AIPhoneVoiceBot", ROOT / "models", ROOT.parent / "models"):
        _cand = _alt_base / "models" / "hindi" / "EtMmohammedHafsati__hi__vasista22__whisper-hindi-small__int8"
        if _cand.exists():
            HINDI_PRIMARY_MODEL = _cand
            break
USE_HINDI_PRIMARY_MODEL = True
# No STT prompt: the old prompt (sample questions of the earlier test FAQ)
# leaked into transcripts and does not help BestZone product questions.
# Without it one STT pass took ~1.2 s instead of ~2.9 s with equal accuracy
# on spoken BestZone questions (13 vs 14 of 25 before matcher tuning).
HINDI_INITIAL_PROMPT = None
# FAQ topic words given to the Hindi model only when its first try matched
# no FAQ. Without a hint it often drops the first word ("ऑर्डर कैंसिल करना"
# -> "करना"). On 25 real call questions: 18 -> 23 right, no extra noise answers.
HINDI_TOPIC_PROMPT = "बेस्टज़ोन: ऑर्डर, कैंसिल, सीओडी, डिस्काउंट, वेंडर, सेलर, जीएसटी बिल, रिफंड, डिलीवरी, ट्रैक।"
_TOPIC_HINT_WORDS = {w.strip("।,:") for w in HINDI_TOPIC_PROMPT.split()}
_TOPIC_HINT_GLUE = {"है", "हैं", "क्या", "का", "की", "के", "हो"}


def is_topic_hint_leak(text):
    """True when a hinted transcript may be the hint itself, not the caller.

    Noise is then heard as hint words: "वेंडर", "जीएसटी बिल रिफंड".
    "सीओडी है क्या" (one hint word in a question) is not a leak.
    """
    words = [w.strip("।,:?.!") for w in text.split() if w.strip("।,:?.!")]
    other = [w for w in words if w not in _TOPIC_HINT_WORDS and w not in _TOPIC_HINT_GLUE]
    hint = [w for w in words if w in _TOPIC_HINT_WORDS]
    return not words or (not other and (len(words) <= 2 or len(hint) >= 2))

# The live bot answers the BestZone CSV (bestzone_faq.py) and the dashboard
# FAQ in hindi_faq.py (bestzone.in website FAQ + small talk). Rows of the
# earlier test FAQ that are not about BestZone stay switched off.
DISABLED_TEST_FAQ_QUESTIONS = (
    "आपके काम करने का समय क्या है", "कीमत क्या है", "क्या मैं अपॉइंटमेंट बुक कर सकता हूँ",
    "आपकी कंपनी किन समस्याओं का समाधान करती है?", "क्या आपके सॉफ्टवेयर को इस्तेमाल करना आसान है?",
    "क्या मुझे सॉफ्टवेयर चलाने की ट्रेनिंग मिलेगी?", "क्या आपका सिस्टम अपने आप काम करता है?",
    "क्या मैं अपनी जरूरत के हिसाब से बदलाव करवा सकता हूँ?", "अगर सॉफ्टवेयर काम करना बंद कर दे तो?",
)
# How long a product the caller mentioned is remembered for follow-ups
# ("लंच बॉक्स चाहिए" ... "उसकी कीमत क्या है").
PRODUCT_CONTEXT_SECONDS = 90.0
ANSWER_PART_PAUSE_SECONDS = 0.25
# Live previews only help when one STT pass is fast. On a slow (CPU) pass
# they occupy the single model and delay the caller's final question.
PREVIEW_MAX_STT_SECONDS = 0.9
# Whisper always encodes a 30 s window. Live FAQ questions are a few seconds,
# so the live STT thread encodes only the spoken audio plus this much padding.
# On this laptop CPU it cuts one STT pass from ~3.2 s to ~1.2 s with the same
# transcripts (measured on recorded calls). Set to None to restore 30 s.
LIVE_WINDOW_PADDING_SECONDS = 1.0

_LIVE_WINDOW = threading.local()
_original_pad_or_trim = _fw_transcribe.pad_or_trim


def _live_pad_or_trim(array, length=3000, *, axis=-1):
    frames = getattr(_LIVE_WINDOW, "frames", None)
    if frames and length == 3000:
        length = frames
    return _original_pad_or_trim(array, length, axis=axis)


# Only the thread that sets _LIVE_WINDOW.frames is affected.
_fw_transcribe.pad_or_trim = _live_pad_or_trim
# Caller audio that starts this soon after the bot stopped talking is checked
# for being the bot's own answer coming back through the call.
ECHO_WINDOW_SECONDS = 1.8
# Caller audio starting while the bot talks, or this long after it stops, is
# treated as possible echo: only a clear FAQ match is answered there, and the
# "please repeat" prompt is never played for it.
BOT_SPEECH_TAIL_SECONDS = 0.15
# Answer only after the caller has finished.  A phone call has short gaps
# between words, so ending after 200-300 ms split normal Hindi sentences into
# fragments ("address" / "refund" / "GST") and made the bot answer too early.
# Keep previews for the live UI, but never let a preview trigger the answer.
ALLOW_EARLY_PREVIEW_ANSWER = False
HINDI_ENDPOINT_FRAMES = 7          # 700 ms of silence closes one utterance
HINDI_MAX_UTTERANCE_FRAMES = 90    # 9 s cap; long address/product questions fit
SPEECH_END_RATIO = 0.22            # Cleanly detects silence without getting trapped by low room noise
FAR_VOICE_RATIO = 0.05
# Audio while the bot talks is the bot's own echo unless it is loud.
BARGE_IN_MIN_RMS = 0.040
BARGE_IN_RATIO = 0.5
# Audio reaching the matcher during bot speech already passed the loudness
# check above, so it is the caller (call log 2026-10-30: 9 real questions were
# dropped silently). Unclear ones now get "please repeat" like any question.
LOUD_BARGE_IN_ASKS_REPEAT = False  # True made the bot talk over itself ("समझ नहीं पाया" + "जवाब नहीं है")
# Second STT pass with the full 30 s window for unmatched questions. In the
# 2026-10-30 call it never found an answer and delayed "please repeat" by
# ~3.5 s, so it is off.
FULL_WINDOW_RECHECK = False
# Common phone-STT spellings of English words used in FAQ questions.
STT_WORD_FIXES = {
    "उड़र": "ऑर्डर", "उडर": "ऑर्डर", "अडर": "ऑर्डर", "ओडर": "ऑर्डर", "ऑडर": "ऑर्डर", "आडर": "ऑर्डर",
    "अर्डर": "ऑर्डर", "आर्डर": "ऑर्डर", "मोडर": "ऑर्डर", "काहा": "कहाँ", "कहा": "कहाँ",
    "रिफन्ड": "रिफंड", "रीफंड": "रिफंड", "रिफ़ंड": "रिफंड", "पेमेंच": "पेमेंट", "बैमेंट": "पेमेंट", "पैमेंट": "पेमेंट",
    "पस्वर": "पासवर्ड", "पासवर्ड़": "पासवर्ड", "कैंसल": "कैंसिल", "केंसल": "कैंसिल", "डिलिवरी": "डिलीवरी",
    "दिलीवरी": "डिलीवरी", "दिलिवरी": "डिलीवरी", "शिपिंग़": "शिपिंग", "रिटन": "रिटर्न", "रिटरन": "रिटर्न",
    "जीस्टी": "जीएसटी", "सेलार": "सेलर", "ट्रेक": "ट्रैक", "ट्रक": "ट्रैक",
    "दिस्काउंट": "डिस्काउंट", "दिस्काउन्ट": "डिस्काउंट", "दिस्कॉउंट": "डिस्काउंट", "दिस्काूँत": "डिस्काउंट",
    "दिस्काूँड": "डिस्काउंट", "दिस्का": "डिस्काउंट", "उर्सेल": "होलसेल", "होल्सेल": "होलसेल", "हॉलसेल": "होलसेल",
    "इच्याल": "होलसेल", "शिपिंचर": "शिपिंग चार्ज", "शिपिंचार्ज": "शिपिंग चार्ज", "शिप्पिंचार्च": "शिपिंग चार्ज",
    "शिप्विंचार्च": "शिपिंग चार्ज", "शिपिंचार्च": "शिपिंग चार्ज", "पिंचाद": "शिपिंग चार्ज", "पिंटाद": "शिपिंग चार्ज",
    "लगताई": "लगता है", "लट्ता": "लगता", "नमवर": "नंबर",
    "कोडर": "ऑर्डर", "सीओनी": "सीओडी", "अवडर": "ऑर्डर", "होडर": "ऑर्डर", "शोडर": "ऑर्डर", "अड़र": "ऑर्डर", "केमसल": "कैंसिल", "केंषिल": "कैंसिल",
    "केंषी": "कैंसिल", "कैंशिल": "कैंसिल", "कैन्सल": "कैंसिल", "येलर": "सेलर", "सिलर": "सेलर", "बन्ना": "बनना",
    "पोग्राम": "प्रोग्राम", "प्रोग्रम": "प्रोग्राम", "पोग्रम": "प्रोग्राम", "रिफरल": "रेफरल", "रेफ्रल": "रेफरल",
    "वेंडल": "वेंडर", "वेन्डर": "वेंडर", "लकते": "लगते", "दिलवरी": "डिलीवरी", "पास्वार्द": "पासवर्ड",
    "अूनर": "ऑर्डर", "अनर": "ऑर्डर", "उनर": "ऑर्डर", "अदर": "ऑर्डर", "ओलर": "ऑर्डर", "औलर": "ऑर्डर", "वोडर": "ऑर्डर", "मुडल": "ऑर्डर",
    "मुद्ल": "ऑर्डर", "भील": "बिल", "भिल": "बिल", "विनिमा": "मिनिमम", "बिनिम्या": "मिनिमम", "निमो": "मिनिमम",
    "मिनिमु": "मिनिमम", "मिनिमा": "मिनिमम", "मिनीमम": "मिनिमम", "मिनिम्नुम": "मिनिमम", "मिनिमाम": "मिनिमम",
    "केसे": "कैसे", "गेशे": "कैसे", "केशे": "कैसे", "लवरे": "डिलीवरी", "भिलेगनी": "मिलेगी",
    "स्थिबिल": "जीएसटी बिल", "बाट": "बात", "बात्": "बात",
    "किमत": "कीमत", "कीमद": "कीमत", "एद्रस": "एड्रेस", "एड्रस": "एड्रेस", "एड्रैस": "एड्रेस", "पूचु": "पूछूँ",
    "पूछु": "पूछूँ", "पूँचेग": "पहुंचेगा", "पूँचेगा": "पहुंचेगा", "पोंचेगा": "पहुंचेगा", "पहुचेगा": "पहुंचेगा",
    "यांपल": "सैंपल", "सांपल": "सैंपल", "सेम्पल": "सैंपल", "सैम्पल": "सैंपल", "सेंपल": "सैंपल", "यैंपल": "सैंपल",
    "भीनिमाम": "मिनिमम", "भिनिमम": "मिनिमम", "मिनिमम्": "मिनिमम", "किम": "कीमत", "कीमद": "कीमत",
    "भुल्गया": "भूल गया", "चियस्टिबिल": "जीएसटी बिल", "श्टिबिल": "जीएसटी बिल", "जीस्टिबल": "जीएसटी बिल",
    "जीस्टिबिल": "जीएसटी बिल", "रिटन": "रिटर्न", "प्रोड़ा": "प्रोडक्ट", "प्र्ड": "प्रोडक्ट", "नमबर": "नंबर", "नम्बर": "नंबर", "पास्वर्ट": "पासवर्ड",
}
SPEECH_PREROLL_SECONDS = 0.3
OVERLAP_ECHO_THRESHOLD = 0.50
OVERLAP_MIN_SCORE = 0.78
# Matcher "predicted" answers already passed its own runner-up margin checks;
# from this score they are answered instead of asking the caller to repeat.
PREDICTED_MIN_SCORE = 0.62
# The same answer is not spoken again within this many seconds.
SAME_ANSWER_COOLDOWN_SECONDS = 12.0
DIRECT_HINDI_VAD_THRESHOLD = 0.015
DIRECT_HINDI_MIN_RMS = 0.012
DIRECT_HINDI_MIN_P90 = 0.015
DIRECT_HINDI_UNKNOWN_MIN_RMS = 0.012
FALLBACK_CALL_IDLE_SECONDS = 60.0
FALLBACK_WELCOME_DELAY_SECONDS = 1.0
# Greeting after an auto-answer pickup: wait this long for the call audio.
WELCOME_AFTER_PICKUP_SECONDS = 0.02
# How long the greeting waits for Frontbit Link's incoming-call notification
# (seen shortly after
# the Hands-Free audio opened on a real call).
RING_TOAST_WAIT_SECONDS = 4.0
FALLBACK_WELCOME_GUARD_SECONDS = 6.0
HINDI_RETRY_WINDOW_SECONDS = 20.0


def normalize_hindi_unknown(value):
    return re.findall(r"[\u0900-\u097f]+", value)


def preferred_language(question):
    """Return a clear caller language choice, or None if it is ambiguous."""
    value = question.casefold().strip()
    compact = re.sub(r"[^a-z\u0900-\u097f\u0a80-\u0aff]", "", value)
    # Phone-call STT often clips the first consonant of short language names.
    # Keep these tolerant aliases isolated to this one-time selection step.
    if any(token in compact for token in (
        "gujarati", "gujrati", "goojarati", "ujarati", "jarati", "jirati", "jrati",
    )):
        return "gu"
    if any(token in compact for token in (
        "गुजराती", "गुजराति", "गुज्राति", "बुज्राति", "अजराती", "जराती", "जराति", "जिराती",
    )):
        return "gu"
    if any(token in compact for token in ("hindi", "hindee", "indie")):
        return "hi"
    if any(token in compact for token in ("english", "inglish", "nglish")):
        return "en"
    if any(token in compact for token in ("gujarati", "ગુજરાતી", "ગુજરાતીભાષા", "guj")):
        return "gu"
    if any(token in compact for token in ("hindi", "हिंदी", "हिन्दी", "हિન્દી", "હિન્દી", "हिन्दुस्तानी")):
        return "hi"
    if any(token in compact for token in ("english", "अंग्रेजी", "अंग्रेज़ी", "અંગ્રેજી", "angrezi")):
        return "en"
    return None


def write_status(**fields):
    payload = {"updated": dt.datetime.now().isoformat(), **fields}
    tmp = STATUS.with_name(f"status.{os.getpid()}.{threading.get_ident()}.tmp")
    with STATUS_LOCK:
        for attempt in range(5):
            try:
                text_content = json.dumps(payload, indent=2, ensure_ascii=False)
                tmp.write_text(text_content, encoding="utf-8")
                os.replace(tmp, STATUS)
                return
            except OSError:
                if attempt == 4:
                    raise
                time.sleep(0.02 * (attempt + 1))
            finally:
                if tmp.exists():
                    try:
                        tmp.unlink()
                    except OSError:
                        pass


class StatusWriter:
    """Write status.json from a background thread.

    The caller path only updates memory; the dashboard still sees every change
    within a few milliseconds, but disk I/O no longer delays an answer.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.pending = None
        self.wake = threading.Event()
        threading.Thread(target=self._run, daemon=True, name="status-writer").start()

    def submit(self, payload):
        with self.lock:
            self.pending = payload
        self.wake.set()

    def flush(self):
        with self.lock:
            payload, self.pending = self.pending, None
        if payload is not None:
            write_status(**payload)

    def _run(self):
        while True:
            self.wake.wait()
            self.wake.clear()
            try:
                self.flush()
            except Exception:
                pass


class Log:
    def __init__(self):
        stamp = dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")
        self.path = LOG_DIR / f"no_adb_{stamp}.txt"
        self.diag_path = LOG_DIR / f"no_adb_{stamp}.jsonl"
        self.lock = threading.Lock()
        self.lines = queue.Queue()
        self.thread = threading.Thread(target=self._run, daemon=True, name="log-writer")
        self.thread.start()
        self.append(f"Frontbit Link no-ADB bot | {dt.datetime.now():%Y-%m-%d %H:%M:%S}\n\n")

    def append(self, text):
        # File writes happen on the log thread so they never delay an answer.
        self.lines.put(text)

    def diagnostic(self, event, **fields):
        """Write a machine-readable diagnostic event beside the human log."""
        payload = {"time": dt.datetime.now().isoformat(timespec="milliseconds"),
                   "event": event, **fields}
        line = json.dumps(payload, ensure_ascii=False, default=str)
        self.append("[DIAG] " + line + "\n")
        try:
            with self.lock:
                with self.diag_path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
        except OSError:
            pass

    def _write(self, text):
        with self.lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(text)

    def _run(self):
        while True:
            text = self.lines.get()
            if text is None:
                return
            batch = [text]
            while True:
                try:
                    more = self.lines.get_nowait()
                except queue.Empty:
                    break
                if more is None:
                    self._write("".join(batch))
                    return
                batch.append(more)
            try:
                self._write("".join(batch))
            except OSError:
                pass

    def close(self, timeout=2.0):
        """Write everything queued so far, then stop the log thread."""
        self.lines.put(None)
        self.thread.join(timeout)


LIVE_CHAT = ROOT / "live_chat.json"
# Greeting spoken when a call starts, set from the dashboard. Empty = say nothing.
GREETING_FILE = ROOT / "greeting.json"


def load_greeting():
    """The call greeting: dashboard text, "" when cleared, the old welcome if never set."""
    try:
        return str(json.loads(GREETING_FILE.read_text(encoding="utf-8")).get("text", "")).strip()
    except FileNotFoundError:
        return HINDI_WELCOME
    except Exception:
        return HINDI_WELCOME
# Save each finished caller utterance as logs\audio\<log name>\utt_NNNN.wav.
SAVE_UTTERANCE_AUDIO = True


class LiveChat:
    """Recent call conversation for the live chat screen (live_chat_viewer.py).

    Events stay in memory; a background thread writes live_chat.json, so the
    call path never waits for disk. Viewer-only: nothing reads it back.
    """

    def __init__(self, path=LIVE_CHAT, keep=300):
        self.path = path
        self.events = collections.deque(maxlen=keep)
        self.seq = 0
        self.lock = threading.Lock()
        self.wake = threading.Event()
        threading.Thread(target=self._run, daemon=True, name="live-chat-writer").start()

    def clear(self):
        with self.lock:
            self.events.clear()
            self.seq = 0
        self.wake.set()

    def add(self, kind, text="", **extra):
        with self.lock:
            self.seq += 1
            self.events.append({"seq": self.seq, "time": f"{dt.datetime.now():%H:%M:%S}",
                                "kind": kind, "text": text or "", **extra})
        self.wake.set()

    def _run(self):
        while True:
            self.wake.wait()
            self.wake.clear()
            with self.lock:
                payload = {"updated": dt.datetime.now().isoformat(), "events": list(self.events)}
            tmp = self.path.with_name(self.path.name + ".tmp")
            try:
                text_content = json.dumps(payload, ensure_ascii=False)
                tmp.write_text(text_content, encoding="utf-8")
                os.replace(tmp, self.path)
            except OSError:
                pass  # the viewer is a helper; never disturb the call
            time.sleep(0.05)


class JobQueue:
    """Single-worker STT queue where the caller's final question always wins.

    * A final job is served before any preview.
    * A final job replaces every waiting job: only the newest question is answered.
    * Only the newest preview is kept, and no preview waits behind a final.
    """

    def __init__(self):
        self.cond = threading.Condition()
        self.finals = collections.deque()
        self.preview = None
        self.closed = False

    def put(self, job):
        """Queue a job and return how many stale jobs were dropped."""
        dropped = 0
        with self.cond:
            if job["final"]:
                if self.preview is not None:
                    self.preview = None
                    dropped += 1
                # Only the newest question matters: a caller who spoke again
                # while waiting has moved on (or repeated themselves).
                dropped += len(self.finals)
                self.finals.clear()
                self.finals.append(job)
            else:
                if self.preview is not None:
                    dropped += 1
                    self.preview = None
                if self.finals:
                    return dropped + 1
                self.preview = job
            self.cond.notify()
        return dropped

    def get(self):
        with self.cond:
            while not self.closed and not self.finals and self.preview is None:
                self.cond.wait()
            if self.finals:
                return self.finals.popleft()
            if self.preview is not None:
                job, self.preview = self.preview, None
                return job
            return None

    def has_final(self):
        with self.cond:
            return bool(self.finals)

    def qsize(self):
        with self.cond:
            return len(self.finals) + (self.preview is not None)

    def close(self):
        with self.cond:
            self.closed = True
            self.cond.notify_all()


def collapse_repeated_sentences(text):
    """"आपका नाम क्या है? आपका नाम क्या है?" -> "आपका नाम क्या है?".

    A short decode window sometimes makes Whisper repeat the sentence it just
    heard. Only back-to-back repeats are removed; different sentences stay.
    """
    text = text.replace("�", "").strip()  # broken last letter of a cut-off repeat
    parts = [part.strip() for part in re.split(r"(?<=[?।.!])\s+", text) if part.strip()]
    if len(parts) < 2:
        words = text.split()
        half = len(words) // 2
        if half and len(words) % 2 == 0 and words[:half] == words[half:]:
            return " ".join(words[:half])
        return cut_trailing_repeat(text)
    kept = []
    for part in parts:
        key = hindi_normalize(part)
        previous = hindi_normalize(kept[-1]) if kept else ""
        if kept and key and (
            SequenceMatcher(None, previous, key).ratio() >= 0.85
            # A repeat cut off by the token limit: "X? X Y" after "X Y Z?".
            or (len(key) >= 4 and previous.startswith(key))
        ):
            continue
        kept.append(part)
    return cut_trailing_repeat(" ".join(kept))


def cut_trailing_repeat(text):
    """"मेरा देटा सूलोखीजे मेरा देटा सूलोखीज" -> "मेरा देटा सूलोखीजे".

    The short decode window makes Whisper start the sentence again and stop
    mid-word (call log 2026-10-31). The tail is cut only when it starts with
    the sentence's first word and reads as the start of the same sentence.
    """
    words = text.split()
    n = len(words)
    for k in range((n + 1) // 2, n):
        head = " ".join(w.strip("?।.,!") for w in words[:k])
        tail = " ".join(w.strip("?।.,!") for w in words[k:])
        first_tail = words[k].strip("?।.,!")
        if len(tail) < 3 or not first_tail or not words[0].strip("?।.,!").startswith(first_tail):
            continue
        if k == n - 1 and first_tail == words[0].strip("?।.,!"):
            continue  # one whole word again ("क्या ... हो क्या") is normal speech, not a cut-off repeat
        if head.startswith(tail) or SequenceMatcher(None, head[:len(tail)], tail).ratio() >= 0.85:
            return " ".join(words[:k])
    return text


def fix_stt_words(text):
    """Correct common phone-STT spellings ("मेरा उड़र काहा है" -> "मेरा ऑर्डर कहाँ है")."""
    # "कैसे" heard as two words ("के से करें").
    text = re.sub(r"(^|\s)के से(?=\s|$|[?।.,!])", r"\1कैसे", text)
    # "कैंसिल" heard as two words ("कैंस सिल करना").
    text = re.sub(r"(^|\s)(?:कैंस|कैन्स|केंस|कैन) (?:सिल|सल|इल)(?=\s|$|[?।.,!])", r"\1कैंसिल", text)
    words = text.split()
    # "ऑर्डर कैसे करें" is often heard as "और कैसे करें": "और" directly
    # before "कैसे" at the start of a question means "ऑर्डर".
    for i, word in enumerate(words[:-1]):
        nxt = words[i + 1].strip("?।.,!")
        if word.strip("?।.,!") == "और" and STT_WORD_FIXES.get(nxt, nxt) == "कैसे" and i <= 1:
            words[i] = "ऑर्डर"
    fixed = []
    for word in words:
        core = word.strip("?।.,!")
        fixed.append(word.replace(core, STT_WORD_FIXES[core]) if core in STT_WORD_FIXES else word)
    # STT sometimes repeats the question ("शिपिंचर लगताई शिपिंचर लगताई"):
    # count each word once so a repeated word cannot outweigh the others.
    seen, unique = set(), []
    for word in " ".join(fixed).split():
        core = word.strip("?।.,!")
        if core and core in seen:
            continue
        seen.add(core)
        unique.append(word)
    return " ".join(unique)


# Local meaning model (semantic_faq.py). Measured on real call transcripts and
# new wordings: wrong answers 10 -> 3 versus word rules alone.
SEMANTIC_MIN = 0.94       # model similarity needed to answer on its own
SEMANTIC_MARGIN = 0.02    # ...and lead over the next FAQ
SEMANTIC_AGREE_TOP = 3    # a word-rule guess must be in the model's top 3
ACKNOWLEDGEMENTS = {
    "ठीक है", "ठीक हैं", "ठीक", "ओके", "ok", "okay", "हाँ", "हां", "हाँ जी", "हां जी", "जी", "जी हाँ", "अच्छा",
    "अच्छा जी", "अच्छा ठीक है", "हम्म", "हूँ", "सही है", "चलो ठीक है",
}
SOUND_MATCH_MIN = 0.76      # how close the question must sound to an FAQ question
SOUND_MATCH_MARGIN = 0.08   # ...and how much closer than any other FAQ
WORD_MATCH_MIN = 0.6        # topic words in any order: F1 of heard vs FAQ words
WORD_MATCH_MARGIN = 0.15


def _sound_key(text):
    """Sound skeleton of a whole question, without filler words."""
    words = [w for w in bestzone_faq.normalize(fix_stt_words(text)).split()
             if not w.isascii() and w not in _SOUND_FILLER]
    return "".join(bestzone_faq.skeleton(w) for w in words)


# Words that say nothing about which FAQ is meant.
_SOUND_FILLER = set("है हैं हो का की के को से में पर और या भी तो जी मुझे मैं प्लीज़ कृपया बताइए बताओ बताएं".split())


@functools.lru_cache(maxsize=4)
def _sound_index(signature):
    index = []
    for aliases, answer in hindi_faq_rows():
        if answer not in active_faq_answers():
            continue
        for alias in aliases:
            if alias.isascii():
                continue
            key = _sound_key(alias)
            if len(key) >= 4:
                index.append((key, answer, alias))
    return index


def sound_match(question):
    """FAQ whose question sounds closest to the caller's, if clearly the best.

    The whole question is compared as a consonant sound skeleton with every
    FAQ question ("विस्ट जों क्या है" ~ "बेस्टज़ोन क्या है"). Tested on the
    2026-10-30 call logs: no garbled background line got an answer.
    """
    key = _sound_key(question)
    if len(key) < 5:
        return None, None
    signature = tuple((tuple(a), b) for a, b in hindi_faq_rows())
    best_by_answer = {}
    for alias_key, answer, alias in _sound_index(signature):
        ratio = SequenceMatcher(None, key, alias_key, autojunk=False).ratio()
        if ratio > best_by_answer.get(answer, (0.0, ""))[0]:
            best_by_answer[answer] = (ratio, alias)
    ranked = sorted(((r, a, al) for a, (r, al) in best_by_answer.items()), reverse=True)
    if not ranked:
        return None, None
    best = ranked[0]
    second = ranked[1][0] if len(ranked) > 1 else 0.0
    if best[0] >= SOUND_MATCH_MIN and best[0] - second >= SOUND_MATCH_MARGIN:
        return best[1], f"sound:{best[2]}:{best[0]:.2f}"
    return word_sound_match(question)


# Words that appear in many FAQ questions and say little about which one.
_WORD_GENERIC = set("""
क्या कितना कितनी कितने कैसे कब कहाँ कहां कौन कौनसे कौनसा क्यों करें करना करनी करने करूँ करूं होगा होगी होता होते
मिलेगा मिलेगी मिलता मिलती मिलते है हैं हो सकते सकता आप आपका आपकी आपके आपको मेरा मेरी मेरे मुझे मैं हम
चाहिए जानना बताइए बताओ बताएं क्या कोई एक प्रोडक्ट माल सामान अपने अपना अपनी पास दे दिया
""".split())


def _topic_words(text):
    """(word, sound skeleton) of the topic words of a question."""
    words = []
    for word in bestzone_faq.normalize(fix_stt_words(text)).split():
        if word.isascii() or word in _WORD_GENERIC or word in _SOUND_FILLER or word in bestzone_faq.STOP_WORDS:
            continue
        skel = bestzone_faq.skeleton(word)
        if len(skel) >= 2:
            words.append((word, skel))
    return words


def _word_sound_similarity(heard, known):
    """Sound match of two words; short words must match exactly."""
    (heard_word, heard_skel), (known_word, known_skel) = heard, known
    if heard_word == known_word or heard_skel == known_skel:
        return 1.0 if min(len(heard_skel), len(known_skel)) >= 3 or heard_word == known_word else 0.0
    if len(heard_skel) < 3 or len(known_skel) < 3:
        return 0.0
    ratio = SequenceMatcher(None, heard_skel, known_skel, autojunk=False).ratio()
    return ratio if ratio >= 0.75 else 0.0


@functools.lru_cache(maxsize=4)
def _word_index(signature):
    index = []
    for aliases, answer in hindi_faq_rows():
        if answer not in active_faq_answers():
            continue
        for alias in aliases:
            if not alias.isascii():
                words = _topic_words(alias)
                if len(words) >= 2:
                    index.append((words, answer, alias))
    return index


def word_sound_match(question):
    """FAQ question with the same topic words, in any order (sound-alike allowed).

    Handles a different or garbled start ("मुझे अवडर केमसल करना है" =
    "मुझे ऑर्डर कैंसिल करना है"). Needs two matching topic words, most of the
    FAQ question's topic words, and a clear lead over every other FAQ.
    """
    heard = list(dict.fromkeys(_topic_words(question)))
    if len(heard) < 2:
        return None, None
    signature = tuple((tuple(a), b) for a, b in hindi_faq_rows())
    best_by_answer = {}
    for words, answer, alias in _word_index(signature):
        matched, total = 0, 0.0
        for known in words:
            sim = max((_word_sound_similarity(h, known) for h in heard), default=0.0)
            if sim:
                matched += 1
                total += sim
        if matched < 2:
            continue
        recall = total / len(words)        # how much of the FAQ question was heard
        precision = total / len(heard)     # how much of what was heard fits it
        score = 2 * recall * precision / (recall + precision)
        if recall >= 0.6 and score > best_by_answer.get(answer, (0.0, ""))[0]:
            best_by_answer[answer] = (score, alias)
    ranked = sorted(((s, a, al) for a, (s, al) in best_by_answer.items()), reverse=True)
    if not ranked:
        return None, None
    best = ranked[0]
    second = ranked[1][0] if len(ranked) > 1 else 0.0
    if best[0] >= WORD_MATCH_MIN and best[0] - second >= WORD_MATCH_MARGIN:
        return best[1], f"words:{best[2]}:{best[0]:.2f}"
    return None, None


def active_faq_answers():
    """Dashboard FAQ answers used live: everything except the old test FAQ."""
    off = {hindi_normalize(question) for question in DISABLED_TEST_FAQ_QUESTIONS}
    return {answer for aliases, answer in hindi_faq_rows() if hindi_normalize(aliases[0]) not in off}


def hindi_faq_rows():
    import hindi_faq
    return hindi_faq.HINDI_FAQS


def hindi_faq_function_words():
    """Grammar words ("कैसे", "क्या", "है") that name no FAQ topic."""
    import hindi_faq
    return hindi_faq._HINDI_FUNCTION_WORDS


def live_gate(alias):
    """Minimum matcher score answered live for this kind of match."""
    return PREDICTED_MIN_SCORE if str(alias or "").startswith("predicted:") else 0.78


def _looks_like_prompt_sentence(part):
    """True when a transcript sentence is (part of) a sentence of the STT prompt."""
    if not HINDI_INITIAL_PROMPT:
        return False
    key = hindi_normalize(part).replace(" ", "")
    if len(key) < 5:
        return False
    for sentence in re.split(r"[?।.!]", HINDI_INITIAL_PROMPT):
        prompt_key = hindi_normalize(sentence).replace(" ", "")
        # Only a near-verbatim copy counts; a real follow-up question such as
        # "आपका पता क्या है" must not be removed for resembling "आपका नाम क्या है".
        if prompt_key and SequenceMatcher(None, key, prompt_key).ratio() >= 0.92:
            return True
    return False


def strip_prompt_leak(text):
    """Drop sentences Whisper copied from its prompt after the caller's own words.

    On unclear call audio Whisper often appends a prompt sentence, e.g.
    "के से हों? अपॉइंटमेंट बुक करना है?" for "कैसे हो". The first sentence is
    always kept, so a single real question is never removed.
    """
    parts = [part.strip() for part in re.split(r"(?<=[?।.!])\s+", text) if part.strip()]
    if len(parts) < 2:
        return text
    kept = [parts[0]] + [part for part in parts[1:] if not _looks_like_prompt_sentence(part)]
    return " ".join(kept)


def _compression_ratio(text):
    """Same measure Whisper uses for its compression_ratio_threshold."""
    import zlib

    data = text.encode("utf-8")
    return len(data) / len(zlib.compress(data)) if data else 0.0


def collapse_word_chunks(text):
    """Remove back-to-back repeats of 1-4 words ("अ ब अ ब अ ब" -> "अ ब")."""
    words = text.split()
    changed = True
    while changed:
        changed = False
        for size in (4, 3, 2, 1):
            i = 0
            while i + 2 * size <= len(words):
                left = [w.strip("?।.,!") for w in words[i:i + size]]
                right = [w.strip("?।.,!") for w in words[i + size:i + 2 * size]]
                if left == right:
                    del words[i + size:i + 2 * size]
                    changed = True
                else:
                    i += 1
    return " ".join(words)


def is_word_loop(text):
    """True for runaway decodes such as "बुक कर कर कर कर"."""
    words = hindi_normalize(text).split()
    # The same 1-3 word chunk three times in a row ("अपने चिए अपने चिए अपने चिए").
    for size in (1, 2, 3):
        for start in range(len(words) - 3 * size + 1):
            chunk = words[start:start + size]
            if chunk == words[start + size:start + 2 * size] == words[start + 2 * size:start + 3 * size]:
                return True
    return False


def warm_up_audio(seconds=2.5):
    """Real Hindi speech (a cached bot answer) so warm-up runs the decoder too."""
    import wave

    cache_dir = ROOT / "hindi_voice_cache"
    for path in sorted(cache_dir.glob("*.wav"))[:5] if cache_dir.is_dir() else []:
        try:
            with wave.open(str(path), "rb") as wav:
                rate, channels = wav.getframerate(), wav.getnchannels()
                raw = wav.readframes(min(wav.getnframes(), int(rate * seconds)))
            audio = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
            if channels > 1:
                audio = audio.reshape(-1, channels).mean(axis=1)
            if rate != 16000 and len(audio) > 1:
                count = int(len(audio) * 16000 / rate)
                audio = np.interp(np.arange(count) * rate / 16000, np.arange(len(audio)), audio)
            if len(audio) >= 16000:
                return audio.astype(np.float32)
        except Exception:
            continue
    return (np.random.default_rng(0).standard_normal(int(16000 * seconds)) * 0.003).astype(np.float32)


def _echo_tokens(value):
    from hindi_faq import _HINDI_FUNCTION_WORDS, _FAQ_GENERIC_WORDS

    # Echo compares heard words with the bot's own answer, so everyday words
    # ("मेरे पास ... सवाल") still count here even though FAQ matching skips them.
    skip = _HINDI_FUNCTION_WORDS - _FAQ_GENERIC_WORDS
    return [
        token for token in hindi_normalize(value).split()
        if token not in skip and len(token) >= 2
    ]


_ECHO_FOLD = str.maketrans({
    # vowels / nasal
    "ई": "", "इ": "", "ऊ": "", "उ": "", "ऐ": "", "ए": "", "औ": "", "ओ": "", "ं": "न", "य": "",
    # aspirated -> plain, voiced -> voiceless (phone-line STT confuses these)
    "ख": "क", "घ": "क", "ग": "क", "छ": "च", "झ": "च", "ज": "च", "ठ": "ट", "ढ": "ट", "ड": "ट",
    "थ": "त", "ध": "त", "द": "त", "फ": "प", "भ": "प", "ब": "प",
})


def _echo_skeleton(token):
    """Rough sound skeleton for echo checks only (never used for FAQ matching)."""
    return hindi_phonetic(token).translate(_ECHO_FOLD)


def bot_echo_score(heard, spoken, short_pieces=False):
    """How strongly a caller transcript looks like the bot's own spoken answer.

    Echo only matches substantial multi-word phrases actually spoken by the bot.
    Common conversational words (e.g. 'क्या', 'है', 'नमस्ते') never count as echo.
    """
    if not heard or not spoken:
        return 0.0
    heard_norm = hindi_normalize(heard)
    spoken_norm = hindi_normalize(spoken)
    heard_key = heard_norm.replace(" ", "")
    spoken_key = spoken_norm.replace(" ", "")
    if not heard_key or not spoken_key:
        return 0.0
    # Short caller utterances (<12 chars or <=2 words) are never echo
    words = [w for w in heard_norm.split() if len(w) >= 2]
    if len(words) <= 2 and len(heard_key) < 14:
        return 0.0
    # Long exact substring match of bot answer (at least 14 chars, e.g. "बेस्टज़ोन में आपका स्वागत है")
    if len(heard_key) >= 14 and (heard_key in spoken_key or spoken_key in heard_key):
        return 1.0
    ratio = SequenceMatcher(None, heard_key, spoken_key, autojunk=False).ratio()
    if ratio >= 0.72 and len(heard_key) >= 12:
        return ratio
    return 0.0


def loopback_devices():
    engine = pa.PyAudio()
    try:
        return [dict(item) for item in engine.get_loopback_device_info_generator()]
    finally:
        engine.terminate()


def choose_loopback(name=None):
    engine = pa.PyAudio()
    try:
        # If user explicitly requested a specific device by name
        if name and name.lower() not in ("auto", "default", "none"):
            needle = name.lower()
            for index in range(engine.get_device_count()):
                info = engine.get_device_info_by_index(index)
                if needle in info.get("name", "").lower() and info.get("maxInputChannels", 0) > 0:
                    return dict(info)
            for item in engine.get_loopback_device_info_generator():
                if needle in item.get("name", "").lower():
                    return dict(item)

        # 100% Pure Wired Mode: Listen to Physical Realtek Microphone connected to Phone Headphone Out!
        for index in range(engine.get_device_count()):
            info = engine.get_device_info_by_index(index)
            dev_name = info.get("name", "").lower()
            if "microphone" in dev_name and "realtek" in dev_name and info.get("maxInputChannels", 0) > 0:
                return dict(info)
        for index in range(engine.get_device_count()):
            info = engine.get_device_info_by_index(index)
            dev_name = info.get("name", "").lower()
            if "line in" in dev_name and info.get("maxInputChannels", 0) > 0:
                return dict(info)

        # Fallback to any recording input device
        for index in range(engine.get_device_count()):
            info = engine.get_device_info_by_index(index)
            if info.get("maxInputChannels", 0) > 0:
                return dict(info)

        raise RuntimeError("No microphone or audio input device found")
    finally:
        engine.terminate()


def disable_realtek_mic_monitoring():
    """Keep Windows from routing the phone downlink back to the phone uplink."""
    try:
        from comtypes import GUID
        from comtypes.automation import VT_BOOL
        from comtypes.persist import STGM_READWRITE
        from pycaw.utils import AudioUtilities
        from pycaw.api.mmdeviceapi import PROPERTYKEY
        from pycaw.api.mmdeviceapi.depend import PROPVARIANT

        listen_key = PROPERTYKEY()
        listen_key.fmtid = GUID("{24DBB0FC-9311-4B3D-9CF0-17185C038208}")
        listen_key.pid = 1  # "Listen to this device" checkbox
        value = PROPVARIANT(VT_BOOL)
        value.union.boolVal = False

        for device in AudioUtilities.GetAllDevices():
            if device.FriendlyName != "Microphone (Realtek(R) Audio)":
                continue
            endpoint = AudioUtilities.GetDeviceEnumerator().GetDevice(device.id)
            store = endpoint.OpenPropertyStore(STGM_READWRITE)
            store.SetValue(listen_key, value)
            store.Commit()
            break
    except Exception:
        # Audio capture must remain available even if a driver does not expose
        # the optional Windows monitoring property.
        pass


def ensure_cable_output_as_default_recording():
    try:
        from pycaw.utils import AudioUtilities
        from pycaw.constants import ERole
        for d in AudioUtilities.GetAllDevices():
            if "CABLE Output" in d.FriendlyName:
                AudioUtilities.SetDefaultDevice(d.id, [ERole.eConsole, ERole.eMultimedia, ERole.eCommunications])
                break
    except Exception:
        pass


class LiveFaqSegmenter:
    """Low-latency VAD with live snapshots for the finite FAQ recognizer."""

    def __init__(
        self,
        emit,
        on_speech_start,
        threshold=0.045,
        silence_frames=2,
        max_frames=30,
        enable_previews=True,
        end_ratio=0.0,
        periodic_previews=True,
    ):
        self.emit = emit
        self.end_ratio = end_ratio
        # Previews every 0.5 s of speech keep the single STT worker busy, so
        # the caller's final question waited 0.6-0.9 s. The preview at the
        # first pause (below) is kept: it gives the final question a head start.
        self.periodic_previews = periodic_previews
        self.peak_rms = 0.0
        self.on_speech_start = on_speech_start
        self.threshold = threshold
        self.silence_frames = silence_frames
        self.max_frames = max_frames
        self.enable_previews = enable_previews
        self.noise_floor = threshold / 3.0
        self.dynamic_threshold = threshold
        self.pre = collections.deque(maxlen=3)
        self.attack = []
        self.parts = []
        self.silence = 0
        self.voiced_frames = 0
        self.start = None
        self.utterance_id = 0
        self.next_preview = 6
        # perf_counter() when the newest voiced frame arrived (end of speech
        # for timing) and the voiced-frame count of the last preview snapshot.
        self.last_voiced_at = None
        self.last_preview_voiced = 0

    def reset(self):
        self.parts = []
        self.attack = []
        self.pre.clear()
        self.silence = 0
        self.voiced_frames = 0
        self.start = None
        self.last_voiced_at = None
        self.last_preview_voiced = 0

    def feed(self, samples):
        rms = float(np.sqrt(np.mean(samples * samples))) if len(samples) else 0.0
        if not self.parts:
            self.dynamic_threshold = max(self.threshold, self.noise_floor * 1.30)
            self.peak_rms = 0.0
        else:
            # Relative to how loud this speaker is: background below 20% of
            # their loudest frame is silence, so the question ends on time.
            self.dynamic_threshold = max(self.threshold * 0.75, self.noise_floor * 1.15,
                                         self.end_ratio * getattr(self, "peak_rms", 0.0))
        voiced = rms >= self.dynamic_threshold
        if voiced:
            self.peak_rms = max(getattr(self, "peak_rms", 0.0), rms)

        if voiced:
            if not self.parts:
                self.attack.append(samples)
                self.utterance_id += 1
                self.start = dt.datetime.now() - dt.timedelta(
                    seconds=(len(self.pre) + len(self.attack)) * 0.1
                )
                self.parts.extend(self.pre)
                self.parts.extend(self.attack)
                self.pre.clear()
                self.attack = []
                self.voiced_frames = 1
                self.next_preview = 6
                self.last_preview_voiced = 0
                self.on_speech_start(self.utterance_id, self.start)
            else:
                self.parts.append(samples)
                self.voiced_frames += 1
            self.silence = 0
            self.last_voiced_at = time.perf_counter()
        elif self.parts:
            self.parts.append(samples)
            self.silence += 1
        else:
            self.attack = []
            self.noise_floor = 0.95 * self.noise_floor + 0.05 * rms
            self.pre.append(samples)

        if (self.enable_previews and self.periodic_previews and self.parts and self.silence == 0
                and self.voiced_frames >= self.next_preview):
            self._emit_preview()
            self.next_preview += 5
        elif (
            self.enable_previews
            and self.parts
            and self.silence == 1
            and self.silence_frames > 1
            and self.voiced_frames >= 3
            and self.voiced_frames != self.last_preview_voiced
            and len(self.parts) < self.max_frames
        ):
            # Speech just paused: start STT now instead of waiting for the
            # full endpoint. If the caller stays silent, the final job reuses
            # this transcript because the spoken audio is identical.
            self._emit_preview()
        if self.parts and (self.silence >= self.silence_frames or len(self.parts) >= self.max_frames):
            self.flush()
        return rms

    def _emit_preview(self):
        self.last_preview_voiced = self.voiced_frames
        self.emit(np.concatenate(self.parts), self.start, self.utterance_id, False)

    def flush(self):
        if self.parts:
            audio = np.concatenate(self.parts)
            duration = len(audio) / 16000.0
            # The endpoint intentionally keeps a 700 ms silence tail so a
            # short pause inside a sentence does not split the question. Do
            # not let that tail lower the voice-level gate for quiet callers.
            voiced_parts = self.parts[:-self.silence] if self.silence else self.parts
            voiced_audio = np.concatenate(voiced_parts) if voiced_parts else audio
            peak = float(np.max(np.abs(voiced_audio))) if len(voiced_audio) else 0.0
            rms = float(np.sqrt(np.mean(voiced_audio * voiced_audio))) if len(voiced_audio) else 0.0
            # Guard against line noise/hum: caller voice has peak >= 0.19 and rms >= 0.038
            if duration >= 0.30 and self.voiced_frames >= 3 and (peak >= 0.19 or rms >= 0.038):
                self.emit(audio, self.start or dt.datetime.now(), self.utterance_id, True)
        self.parts = []
        self.silence = 0
        self.voiced_frames = 0
        self.start = None
        self.next_preview = 6
        self.last_preview_voiced = 0


class FrontbitLinkNoAdbBot:
    def __init__(self, args):
        self.args = args
        self.stop = threading.Event()
        self.jobs = JobQueue()
        self.log = Log()
        self.log.diagnostic("bot_init", mode="no_adb_frontbit_link", no_voice=bool(args.no_voice),
                           loopback_name=args.loopback_name, threshold=args.threshold,
                           voice_device="CABLE Input", capture_expected="Speakers (Realtek(R) Audio) [Loopback]")
        self.chat = LiveChat()
        self.last_status = {"mode": "no_adb_frontbit_link", "file": str(self.log.path),
                            "diagnostic_file": str(self.log.diag_path), "ready": False}
        self.status_lock = threading.Lock()
        self.status_writer = StatusWriter()
        self.segmenter = None
        self.hindi_beam_size = 3
        self.stt_seconds_ema = None
        # utterance_id -> (voiced_frames, transcript) from the newest preview.
        self.preview_transcripts = {}
        # Recent bot answers: [text, started_wall_time, ended_wall_time|None].
        self.recent_bot_speech = collections.deque(maxlen=6)
        # [start, end|None] wall-clock times of real bot playback.
        self.bot_speech_intervals = collections.deque(maxlen=8)
        self.last_answer_text = None
        self.last_answer_at = 0.0
        # Timing of the answer currently waiting for its "playing" event.
        self.pending_timing = None
        self.ready_flags = {}
        self.tts_missing = []
        self.voice_ready = threading.Event()
        self.voice_active = threading.Event()
        self.last_question = None
        self.last_question_at = 0.0
        self.answered_utterances = set()
        self.voice_requested_at = None
        self.hindi_stt = None
        self.hindi_primary_stt = None
        self.last_hindi_diagnostics = []
        self.last_gujarati_diagnostics = []
        self.last_language_choice_diagnostics = []
        self.preferred_language = FORCE_LANGUAGE
        self.awaiting_language = False
        self.language_prompt_played = False
        self.language_prompt_utterance = None
        self.language_lock = threading.Lock()
        self.call_active = False
        self.call_detection_source = None
        self.last_call_activity_at = 0.0
        self.fallback_trigger_utterance_id = None
        self.welcome_timer = None
        self.welcome_guard_until = 0.0
        self.pending_hindi_question = None
        self.pending_hindi_question_at = 0.0
        self.hindi_preview_candidates = {}
        def _choose_voice_out():
            eng = pa.PyAudio()
            try:
                # 100% Pure Wired Mode: Output bot voice directly to physical Speakers (Realtek) to travel to phone!
                for idx in range(eng.get_device_count()):
                    inf = eng.get_device_info_by_index(idx)
                    dname = inf.get("name", "").lower()
                    if "speakers" in dname and "realtek" in dname and "loopback" not in dname and inf.get("maxOutputChannels", 0) > 0:
                        return inf.get("name", "")
                for idx in range(eng.get_device_count()):
                    inf = eng.get_device_info_by_index(idx)
                    dname = inf.get("name", "").lower()
                    if "speakers" in dname and "loopback" not in dname and inf.get("maxOutputChannels", 0) > 0:
                        return inf.get("name", "")
                return "Speakers (Realtek(R) Audio)"
            finally:
                eng.terminate()

        self.voice = None if args.no_voice else CableVoice(self.on_voice_event, _choose_voice_out())

    def set_status(self, **fields):
        with self.status_lock:
            self.last_status.update(fields)
            current = dict(self.last_status)
        self.status_writer.submit(current)

    def chat_event(self, kind, text="", **extra):
        """Add a line to the live chat screen; never breaks the call."""
        chat = getattr(self, "chat", None)
        if chat is not None:
            try:
                chat.add(kind, text, **extra)
            except Exception:
                pass

    def remember_bot_speech(self, text):
        self.recent_bot_speech.append((text, dt.datetime.now()))

    def overlaps_bot_speech(self, heard_at):
        """True when caller audio started while the bot was talking (or just after).

        On this call route the bot's own voice comes back through the call, so
        audio from that window is mostly echo, not a new question.
        """
        now = dt.datetime.now()
        # The utterance timestamp includes ~0.3 s of pre-roll before speech.
        onset = heard_at + dt.timedelta(seconds=SPEECH_PREROLL_SECONDS)
        for started, ended in list(getattr(self, "bot_speech_intervals", ())):
            # Cap maximum playback duration to 12s so unclosed intervals never poison future utterances
            end = ended or min(now, started + dt.timedelta(seconds=12.0))
            # 0.5 s early margin: echo of a just-started answer
            if started - dt.timedelta(seconds=0.5) <= onset <= end + dt.timedelta(seconds=BOT_SPEECH_TAIL_SECONDS):
                return True
        return False

    def is_bot_echo(self, question, heard_at, threshold=0.60):
        """True when the transcript is the bot's own recent answer heard back."""
        if not question or not self.recent_bot_speech:
            return False, 0.0
        # Echo is physically IMPOSSIBLE if the bot was not speaking when audio arrived!
        if not self.overlaps_bot_speech(heard_at):
            return False, 0.0
        recent = [text for text, requested in self.recent_bot_speech
                  if abs((heard_at - requested).total_seconds()) <= 2.0]
        if not recent:
            return False, 0.0
        candidates = recent + ([" ".join(recent)] if len(recent) == 2 else [])
        best = max((bot_echo_score(question, text, short_pieces=False) for text in candidates), default=0.0)
        return best >= threshold, best

    def on_voice_event(self, event):
        event_type = event.get("type")
        self.log.diagnostic("voice_event", **event)
        if event_type == "playing":
            # Measure first, before any logging or status work.
            playing_at = time.perf_counter()
            self.voice_active.set()
            self.last_bot_playback_ended = time.monotonic() + 999.0
            if getattr(self, "segmenter", None):
                self.segmenter.reset()
            timing, self.pending_timing = self.pending_timing, None
            startup_ms = None
            if self.voice_requested_at is not None:
                startup_ms = round((playing_at - self.voice_requested_at) * 1000)
            fields = {"voice": "playing", "voice_device": event.get("device"), "voice_startup_ms": startup_ms}
            if timing is not None:
                eos = timing.pop("end_of_speech_at", None)
                if eos is not None:
                    timing["end_of_speech_to_playback_ms"] = round((playing_at - eos) * 1000, 1)
                timing["voice_startup_ms"] = startup_ms
                fields["last_timing"] = timing
                self.chat_event("timing", utt=timing.get("utterance"),
                                ms=timing.get("end_of_speech_to_playback_ms"))
                self.log.append("[TIMING] answer " + " ".join(f"{k}={v}" for k, v in timing.items()) + "\n")
        if event_type in {"playing", "played", "playback_stopped", "error"}:
            self.log.append(f"[{dt.datetime.now():%H:%M:%S}] VOICE: {json.dumps(event, ensure_ascii=False)}\n")
        intervals = getattr(self, "bot_speech_intervals", None)
        if intervals is not None:
            if event_type == "playing":
                intervals.append([dt.datetime.now(), None])
            elif event_type in {"played", "playback_stopped", "error"} and intervals and intervals[-1][1] is None:
                intervals[-1][1] = dt.datetime.now()
        if event_type in {"played", "playback_stopped", "error"}:
            self.last_bot_audio_end = dt.datetime.now()
            self.last_bot_playback_ended = time.monotonic()
            if getattr(self, "segmenter", None):
                self.segmenter.reset()
        if event_type in {"ready", "output_ready", "played", "playback_stopped"}:
            if event_type == "ready":
                self.voice_ready.set()
            if event_type in {"played", "playback_stopped"}:
                self.voice_active.clear()
            self.set_status(voice="ready", voice_device=event.get("device", "CABLE Input"), last_voice_event=event_type)
        elif event_type == "playing":
            self.set_status(**fields)
        elif event_type == "cached":
            self.set_status(voice_cache="warming", cached_answer=event.get("text"))
        elif event_type == "cache_hit":
            self.set_status(voice_cache="hit")
        elif event_type == "error":
            self.voice_active.clear()
            self.set_status(voice="error", voice_error=event.get("message") or "Unknown voice error")
        elif event_type == "route":
            self.set_status(voice_route=event.get("message"))

    def load(self):
        self.set_status(state="loading_stt")
        try:
            self.stt = WhisperModel(
                "tiny.en",
                device="cpu",
                compute_type="int8",
                cpu_threads=min(4, os.cpu_count() or 4),
                download_root=str(PRODUCTION / "models"),
                local_files_only=True,
            )
        except Exception as exc:
            self.log.diagnostic("english_stt_warning", error=str(exc))
            self.stt = None
        hindi_runtime, stt_errors = self.load_hindi_stt()
        self.ready_flags["stt_warm"] = self.hindi_stt is not None
        # CUDA is either warmed above, or deliberately not used because this
        # GPU cannot beat the CPU path (the reason is in stt_fallback_errors).
        self.ready_flags["cuda_warm"] = self.hindi_stt is not None
        # Build and warm the FAQ index so the first caller does not pay for it.
        matcher_started = time.perf_counter()
        self.bestzone = bestzone_faq.load_catalog(str(bestzone_faq.CSV_PATH))
        self.semantic = self.load_meaning_model()
        self.match_question("नमस्ते")
        self.match_question("वॉल हुक की कीमत क्या है")
        self.ready_flags["faq_matcher"] = bool(self.bestzone["answers"])
        self.set_status(
            bestzone_products=len(self.bestzone["products"]),
            bestzone_question_types=len(self.bestzone["templates"]),
            bestzone_answers=len(self.bestzone["answers"]),
            english_model="tiny.en/cpu",
            hindi_model=hindi_runtime,
            gujarati_model=hindi_runtime,
            stt_fallback_errors=stt_errors or None,
            cuda_used=hindi_runtime.endswith("/cuda"),
            live_window_padding_seconds=LIVE_WINDOW_PADDING_SECONDS,
            faq_matcher_warm_ms=round((time.perf_counter() - matcher_started) * 1000, 1),
            reply_voices=f"{CableVoice._voice_name('en')} / {CableVoice._voice_name('hi')} / {CableVoice._voice_name('gu')}",
            hindi_listening_mode="hybrid_preview_final_confirm",
            hindi_endpoint_ms=HINDI_ENDPOINT_FRAMES * 100,
            hindi_max_utterance_seconds=HINDI_MAX_UTTERANCE_FRAMES / 10,
            hindi_beam_size=self.hindi_beam_size,
            live_previews=self.previews_enabled(),
            hindi_vad_threshold=DIRECT_HINDI_VAD_THRESHOLD,
            hindi_min_utterance_rms=DIRECT_HINDI_MIN_RMS,
            hindi_min_utterance_p90=DIRECT_HINDI_MIN_P90,
            hindi_unknown_min_rms=DIRECT_HINDI_UNKNOWN_MIN_RMS,
        )
        if self.voice is not None:
            self.voice_ready.wait(5)
            cache_started = time.perf_counter()
            answers = list(dict.fromkeys(
                [("en", answer) for _, answer in FAQS]
                + [("hi", answer) for _, answer in HINDI_FAQS]
                + [("gu", answer) for _, answer in GUJARATI_FAQS]
                + [("hi", UNKNOWN_HINDI), ("gu", UNKNOWN_GUJARATI)]
                + [("hi", load_greeting() or HINDI_WELCOME), ("hi", HINDI_REPEAT_PROMPT)]
                + list(LANGUAGE_PROMPTS.items())
                + list(LANGUAGE_CONFIRMATIONS.items())
                # BestZone answers are spoken as "<product name> + <answer>"
                # pieces, joined at answer time (no 1000 full recordings).
                + [("hi", part) for part in bestzone_faq.tts_parts(self.bestzone)]
            ))
            # Preloading every FAQ answer can take many minutes on first start.
            # Fast-start keeps the output stream ready and generates a missing
            # answer on demand, while the background retry continues warming
            # the cache for later calls.
            fast_start = bool(getattr(self.args, "fast_start", False))
            greeting_item = ("hi", load_greeting() or HINDI_WELCOME)
            greeting_cached = self.voice.preload([greeting_item], timeout=120)
            cached = greeting_cached + (0 if fast_start else self.voice.preload(
                [item for item in answers if item != greeting_item]))
            output_warm = self.voice.warm_output()
            # A newly added FAQ can miss the remote TTS cache temporarily. Keep
            # the already-cached replies and let the live bot start; the missing
            # reply will retry on demand instead of killing the whole monitor.
            if not output_warm:
                raise RuntimeError("Voice output device is not ready")
            self.tts_missing = [item for item in answers if item not in self.voice.pcm_cache]
            self.ready_flags["tts_cache"] = fast_start or not self.tts_missing
            self.ready_flags["cable_output"] = bool(output_warm)
            self.set_status(
                voice_cache="ready" if cached == len(answers) else "partial",
                cached_answers=cached,
                cache_total=len(answers),
                cache_warm_seconds=round(time.perf_counter() - cache_started, 3),
                voice_output_warm=output_warm,
                voice_cache_warning=("fast-start: missing replies generate on demand" if fast_start
                                     else None if cached == len(answers)
                                     else f"{len(answers) - cached} reply(s) pending TTS retry"),
                tts_missing=[text for _, text in self.tts_missing] or None,
                awaiting_language=False,
                preferred_language=FORCE_LANGUAGE,
                language_selection_source="forced_hindi",
            )
            if self.tts_missing:
                threading.Thread(target=self.retry_missing_tts, daemon=True).start()
        else:
            self.ready_flags["tts_cache"] = True
            self.ready_flags["cable_output"] = True
            self.set_status(voice="disabled_for_echo_test")
        self.publish_ready()

    def load_meaning_model(self):
        """Load the local meaning model and index every active FAQ question form.

        Any failure only turns the model off; the word rules keep working.
        """
        try:
            import semantic_faq

            started = time.perf_counter()
            model = semantic_faq.SemanticFaq()
            if model.available:
                active = active_faq_answers()
                model.index([(form, answer, aliases[0]) for aliases, answer in hindi_faq_rows()
                             if answer in active for form in aliases])
                model.ranked("नमस्ते")  # warm up
            self.set_status(meaning_model="ready" if model.available else "off",
                            meaning_model_error=model.error,
                            meaning_model_load_seconds=round(time.perf_counter() - started, 2))
            return model
        except Exception as exc:
            self.set_status(meaning_model="off", meaning_model_error=f"{type(exc).__name__}: {exc}")
            return None

    def publish_ready(self):
        """READY only when STT, CUDA, matcher, TTS cache and CABLE are all warm."""
        names = ("stt_warm", "cuda_warm", "faq_matcher", "tts_cache", "cable_output")
        missing = [name for name in names if not self.ready_flags.get(name)]
        self.set_status(
            ready=not missing,
            ready_checks={name: bool(self.ready_flags.get(name)) for name in names},
            not_ready_reasons=missing or None,
        )

    def retry_missing_tts(self):
        """Fill TTS for new FAQ answers in the background, then mark READY."""
        while self.tts_missing and not self.stop.wait(10):
            self.voice.preload(list(self.tts_missing), timeout=30)
            self.tts_missing = [item for item in self.tts_missing if item not in self.voice.pcm_cache]
            self.ready_flags["tts_cache"] = not self.tts_missing
            self.set_status(tts_missing=[text for _, text in self.tts_missing] or None,
                            voice_cache="ready" if not self.tts_missing else "partial")
            self.publish_ready()

    def previews_enabled(self):
        return self.hindi_stt is not None

    def load_hindi_stt(self):
        """Pick the fastest working Hindi STT runtime and warm it.

        Order: CUDA float16 large-v3-turbo (GPUs with fast float16) -> CPU int8
        small. GPUs without fast float16 (e.g. MX330) are skipped on purpose:
        measured int8 CUDA there was ~2.5x slower than the CPU path.
        """
        if not (HINDI_TURBO_MODEL / "model.bin").exists():
            raise RuntimeError(f"Hindi turbo model is incomplete: {HINDI_TURBO_MODEL}")
        small_ok = (HINDI_SMALL_MODEL / "model.bin").exists()
        candidates = [
            (HINDI_TURBO_MODEL, "large-v3-turbo", "cuda", "int8_float16", 3),
            (HINDI_SMALL_MODEL if small_ok else HINDI_TURBO_MODEL,
             "small" if small_ok else "large-v3-turbo", "cpu", "int8", 3),
        ]
        errors = []
        for path, model_name, device, compute_type, beam in candidates:
            try:
                # Physical cores only: hyper-threads made the encoder slower.
                kwargs = {"cpu_threads": max(1, min(8, (os.cpu_count() or 8) // 2))} if device == "cpu" else {}
                model = WhisperModel(str(path), device=device, compute_type=compute_type,
                                     local_files_only=True, **kwargs)
                self.hindi_stt = model
                self.hindi_beam_size = beam
                warm = warm_up_audio()
                if device == "cpu":
                    self.load_hindi_primary_stt(kwargs, warm, errors)
                # Warm kernels with the exact live settings before a caller arrives.
                for _ in range(2):
                    started = time.perf_counter()
                    self.transcribe_hindi(warm)
                    warm_seconds = time.perf_counter() - started
                self.stt_seconds_ema = warm_seconds
                self.set_status(stt_warm_seconds=round(warm_seconds, 3))
                if getattr(self, "hindi_primary_stt", None) is not None:
                    self.transcribe_hindi(warm, model=self.hindi_stt)  # warm the fallback too
                    model_name = f"hindi-small(vasista22)+fallback {model_name}"
                return f"{model_name}/{compute_type}/{device}", errors
            except Exception as exc:
                errors.append(f"{model_name}/{compute_type}/{device}: {exc}")
                self.hindi_stt = None
        # Fallback cleanly to Google Cloud Speech STT if local whisper cannot allocate memory
        self.hindi_stt = "google_stt"
        self.hindi_beam_size = 1
        return "google_stt/web_api", errors

    def load_hindi_primary_stt(self, kwargs, warm, errors):
        """Load the Hindi-trained Whisper small; on any problem keep the general model only."""
        self.hindi_primary_stt = None
        if not USE_HINDI_PRIMARY_MODEL or not (HINDI_PRIMARY_MODEL / "model.bin").exists():
            return
        try:
            model = WhisperModel(str(HINDI_PRIMARY_MODEL), device="cpu", compute_type="int8",
                                 local_files_only=True, **kwargs)
            self.hindi_primary_stt = model
            self.transcribe_hindi(warm)  # must decode Hindi without errors
        except Exception as exc:
            self.hindi_primary_stt = None
            errors.append(f"hindi-small(vasista22): {exc}")

    def on_speech_start(self, utterance_id, timestamp):
        self.log.diagnostic("speech_start", utterance=utterance_id, timestamp=timestamp.isoformat(),
                           call_active=self.call_active, call_source=self.call_detection_source)
        # Never trigger fake calls or status changes on idle line/cable noise when no call is active.
        if not self.call_active:
            return
        auto = getattr(self, "auto_answer", None)
        if auto and auto.ringing:
            return

        self.last_call_activity_at = time.monotonic()
        was_speaking = self.voice_active.is_set()
        self.set_status(
            state="caller_speaking",
            utterance_id=utterance_id,
            barge_in=was_speaking,
            barge_in_pending=was_speaking,
            speech_started=timestamp.isoformat(),
        )

    def enqueue(self, audio, timestamp, utterance_id, final):
        if not self.call_active:
            return
        auto = getattr(self, "auto_answer", None)
        if auto and auto.ringing:
            return
        segmenter = self.segmenter
        if not final and not self.previews_enabled():
            return
        job = {
            "audio": audio,
            "timestamp": timestamp,
            "utterance_id": utterance_id,
            "final": final,
            "queued_at": time.perf_counter(),
            "voiced_frames": getattr(segmenter, "voiced_frames", None),
            "end_of_speech_at": getattr(segmenter, "last_voiced_at", None) if final else None,
        }
        self.log.diagnostic("audio_queued", utterance=utterance_id, final=final,
                           audio_seconds=round(len(audio) / 16000.0, 3),
                           voiced_frames=job["voiced_frames"], queue_size=self.jobs.qsize())
        dropped = self.jobs.put(job)
        if dropped:
            self.set_status(dropped_stale_chunks=dropped)

    def retry_hindi_stt(self, audio, timestamp, timing):
        """Skip redundant 5-second multi-pass STT to keep phone response instant."""
        return None

    def transcribe_hindi(self, audio, preview=False, full_window=False, model=None, prompt=None):
        self.last_hindi_diagnostics = []
        rms = float(np.sqrt(np.mean(audio * audio))) if len(audio) else 0.0
        peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
        # Line noise / static gate: reject pure hiss without speech (0.08 peak / 0.012 rms)
        if peak < 0.08 and rms < 0.012:
            return "", 0.0

        # Gentle normalization: preserve SNR, never amplify pure low noise
        norm_audio = (audio / max(peak, 0.55) * 0.9).astype(np.float32)
        started = time.perf_counter()

        # 1. Primary STT: Google Speech Recognition (Ultra-fast, 0 hallucination, high Hindi accuracy)
        if len(audio) >= 4000:
            try:
                import speech_recognition as sr
                int16_pcm = (np.clip(norm_audio, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
                audio_data = sr.AudioData(int16_pcm, 16000, 2)
                r = sr.Recognizer()
                google_text = r.recognize_google(audio_data, language="hi-IN")
                if google_text and len(google_text.strip()) > 0:
                    clean_text = google_text.strip()
                    self.last_hindi_diagnostics = [{
                        "text": clean_text,
                        "source": "google_stt",
                        "duration": round(time.perf_counter() - started, 3),
                        "no_speech": 0.0,
                    }]
                    return clean_text, 1.0
            except Exception:
                pass

        # 2. Backup STT: Local Neural Whisper (Fine-tuned Hindi vasista22 int8 / Faster-Whisper)
        model = model or getattr(self, "hindi_primary_stt", None) or self.hindi_stt
        if model is None or not hasattr(model, "transcribe"):
            return "", 0.0
        token_budget = min(120, int(len(audio) / 16000.0 * 20) + 32)
        try:
            segments, _ = model.transcribe(
                norm_audio,
                language="hi",
                beam_size=max(1, int(getattr(self, "hindi_beam_size", 3))),
                best_of=max(1, int(getattr(self, "hindi_beam_size", 3))),
                max_new_tokens=token_budget,
                vad_filter=True,  # Silero VAD filters out cable hiss and electrical noise
                condition_on_previous_text=False,
                without_timestamps=True,
                temperature=0,
                no_speech_threshold=0.60,
                log_prob_threshold=-1.40,
                compression_ratio_threshold=2.4,
                initial_prompt=prompt if prompt is not None else HINDI_INITIAL_PROMPT,
            )
            segments = list(segments)
        except Exception as e:
            self.log.diagnostic("hindi_stt_error", error=str(e))
            segments = []
        finally:
            _LIVE_WINDOW.frames = None

        HALLUCINATIONS = (
            "उन्होंने बताया", "समाचार समाप्त", "समाचारों के", "नये मामले",
            "टीका लगा", "आकाशवाणी", "दूरदर्शन", "विशेष बुलेटिन", "संवाददाता",
            "प्रधानमंत्री", "नरेंद्र मोदी", "पचास लाख", "प्रदेश में", "चार लाख", "चौबिस", "जब तक"
        )
        cleaned_segments = []
        for s in segments:
            txt = s.text.strip()
            if any(h in txt for h in HALLUCINATIONS):
                continue
            cleaned_segments.append(s)
        segments = cleaned_segments
        self.last_hindi_diagnostics = [
            {
                "text": segment.text.strip(),
                "no_speech": round(float(segment.no_speech_prob), 3),
                "logprob": round(float(segment.avg_logprob), 3),
                "compression": round(float(segment.compression_ratio), 3),
                "token_budget_reached": len(segment.tokens) >= token_budget - 1,
            }
            for segment in segments
        ]
        accepted = []
        for segment in segments:
            raw = segment.text.strip()
            # STT often repeats what it heard ("X Y X Y X Y"): keep it once
            # instead of throwing the whole question away as a loop.
            text = collapse_word_chunks(collapse_repeated_sentences(raw))
            repeated = text != raw
            # A sentence spoken once but decoded twice compresses "too well";
            # judge the de-duplicated text instead of the raw decode.
            compression = _compression_ratio(text) if repeated else segment.compression_ratio
            budget_hit = len(segment.tokens) >= token_budget - 1
            # Whisper's own rule: "no speech" only when the no-speech score is
            # high AND the text is unsure ("माल कब पूँचेग" had no_speech 0.83
            # with a good log-prob and was wrongly thrown away).
            silent = segment.no_speech_prob >= 0.80 and segment.avg_logprob < -0.70
            if (
                not silent
                and segment.avg_logprob >= -1.25
                and compression < 2.4
                and (not budget_hit or repeated)
                and not is_word_loop(text)
            ):
                accepted.append(text)
        return " ".join(accepted).strip(), time.perf_counter() - started

    def transcribe_language_choice(self, audio):
        """Recognize one language choice with one multilingual STT pass."""
        if self.hindi_stt is None:
            return "", 0.0, "", 0.0
        started = time.perf_counter()
        segments, info = self.hindi_stt.transcribe(
            audio,
            language=None,
            beam_size=1,
            vad_filter=False,
            condition_on_previous_text=False,
            temperature=0,
            no_speech_threshold=0.85,
            log_prob_threshold=-1.40,
            compression_ratio_threshold=2.5,
            hallucination_silence_threshold=0.5,
            hotwords=(
                "English Hindi Gujarati, हिंदी हिन्दी गुजराती, "
                "અંગ્રેજી હિન્દી ગુજરાતી"
            ),
        )
        segments = list(segments)
        self.last_language_choice_diagnostics = [
            {
                "text": segment.text.strip(),
                "no_speech": round(float(segment.no_speech_prob), 3),
                "logprob": round(float(segment.avg_logprob), 3),
                "compression": round(float(segment.compression_ratio), 3),
            }
            for segment in segments
        ]
        accepted = [
            segment for segment in segments
            if segment.no_speech_prob < 0.90
            and segment.avg_logprob >= -1.40
            and segment.compression_ratio < 2.5
        ]
        text = " ".join(segment.text.strip() for segment in accepted).strip()
        detected_language = str(getattr(info, "language", "") or "")
        language_probability = float(getattr(info, "language_probability", 0.0) or 0.0)
        return text, time.perf_counter() - started, detected_language, language_probability

    def transcribe_gujarati(self, audio):
        self.last_gujarati_diagnostics = []
        started = time.perf_counter()
        rms = float(np.sqrt(np.mean(audio * audio))) if len(audio) else 0.0
        gain = min(12.0, 0.12 / rms) if rms > 0.001 else 1.0
        norm_audio = np.clip(audio * gain, -0.98, 0.98)
        if len(audio) >= 4000:
            try:
                import speech_recognition as sr
                int16_pcm = (np.clip(norm_audio, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
                audio_data = sr.AudioData(int16_pcm, 16000, 2)
                r = sr.Recognizer()
                google_text = r.recognize_google(audio_data, language="gu-IN")
                if google_text and len(google_text.strip()) > 0:
                    dur = time.perf_counter() - started
                    clean_text = google_text.strip()
                    self.last_gujarati_diagnostics = [{
                        "text": clean_text,
                        "source": "google_stt",
                        "duration": round(dur, 3),
                        "no_speech": 0.0,
                        "logprob": 0.0,
                        "compression": 1.0,
                    }]
                    return clean_text, dur
            except Exception:
                pass

        if self.hindi_stt is None:
            return "", 0.0
        segments, _ = self.hindi_stt.transcribe(
            norm_audio,
            language="gu",
            beam_size=1,
            vad_filter=False,
            condition_on_previous_text=False,
            temperature=0,
            no_speech_threshold=0.85,
            log_prob_threshold=-1.40,
            compression_ratio_threshold=2.5,
            hallucination_silence_threshold=0.5,
            initial_prompt="નમસ્તે. તમે કેમ છો? તમારું નામ શું છે? તમે શું કરી શકો? કિંમત શું છે? આભાર.",
        )
        segments = list(segments)
        self.last_gujarati_diagnostics = [
            {
                "text": segment.text.strip(),
                "no_speech": round(float(segment.no_speech_prob), 3),
                "logprob": round(float(segment.avg_logprob), 3),
                "compression": round(float(segment.compression_ratio), 3),
            }
            for segment in segments
        ]
        accepted = [
            segment for segment in segments
            if segment.no_speech_prob < 0.80
            and segment.avg_logprob >= -1.25
            and segment.compression_ratio < 2.4
        ]
        return " ".join(segment.text.strip() for segment in accepted).strip(), time.perf_counter() - started

    def hindi_transcript(self, audio, utterance_id, final, voiced_frames):
        """Transcribe audio with speculative preview reuse for zero latency."""
        # Instant preview reuse: if preview already transcribed this question while the caller spoke
        if final and utterance_id in self.preview_transcripts:
            prev_voiced, prev_text = self.preview_transcripts.pop(utterance_id, (0, ""))
            if prev_text and (voiced_frames is None or abs(voiced_frames - prev_voiced) <= 5):
                self.last_final_transcript = prev_text
                return prev_text, 0.001, True

        text, seconds = self.transcribe_hindi(audio, preview=not final)
        if final:
            self.last_final_transcript = text
            self.preview_transcripts.pop(utterance_id, None)
        elif voiced_frames is not None and text:
            self.preview_transcripts[utterance_id] = (voiced_frames, text)
            if len(self.preview_transcripts) > 16:
                for key in sorted(self.preview_transcripts)[:-8]:
                    self.preview_transcripts.pop(key, None)
        ema = self.stt_seconds_ema
        self.stt_seconds_ema = seconds if ema is None else 0.7 * ema + 0.3 * seconds
        return text, seconds, False

    def process_audio(self, audio, timestamp, utterance_id, final, voiced_frames=None,
                      end_of_speech_at=None, queue_wait_ms=None):
        timing = {"utterance": utterance_id, "audio_seconds": round(len(audio) / 16000.0, 2),
                  "queue_wait_ms": queue_wait_ms}
        absolute_audio = np.abs(audio)
        utterance_rms = float(np.sqrt(np.mean(audio * audio))) if len(audio) else 0.0
        utterance_p90 = float(np.percentile(absolute_audio, 90)) if len(audio) else 0.0
        utterance_peak = float(np.max(absolute_audio)) if len(audio) else 0.0
        self.set_status(
            utterance_rms=round(utterance_rms, 6),
            utterance_p90=round(utterance_p90, 6),
            utterance_peak=round(utterance_peak, 6),
        )
        caller_level = self.caller_level()
        self.log.diagnostic("audio_level", utterance=utterance_id, final=final,
                           audio_seconds=round(len(audio) / 16000.0, 3),
                           rms=round(utterance_rms, 6), p90=round(utterance_p90, 6),
                           peak=round(utterance_peak, 6), caller_level=caller_level)
        if (
            self.call_detection_source == "loopback_activity"
            and utterance_id == self.fallback_trigger_utterance_id
        ):
            self.fallback_trigger_utterance_id = None
            self.set_status(
                state="welcome_playing",
                skipped="fallback_call_trigger_audio",
                trigger_utterance_id=utterance_id,
            )
            return
        if (
            self.call_detection_source == "loopback_activity"
            and time.monotonic() < self.welcome_guard_until
        ):
            self.set_status(
                state="welcome_playing",
                skipped="welcome_guard_audio",
                guard_utterance_id=utterance_id,
            )
            return
        if FORCE_LANGUAGE == "hi" and not self.call_active:
            self.set_status(state="waiting_for_phone_call", skipped="no_active_call")
            return
        if FORCE_LANGUAGE == "hi" and self.overlaps_bot_speech(timestamp):
            # While the bot talks (and just after) the line carries the bot's
            # own voice back, but quietly (call log: echo rms 0.004-0.045,
            # caller 0.06-0.11). A loud voice is the caller speaking: listen.
            barge_in_level = max(BARGE_IN_MIN_RMS, BARGE_IN_RATIO * (caller_level or 0.0))
            if utterance_rms < barge_in_level:
                if final:
                    self.log.append(f"[{timestamp:%H:%M:%S}] IGNORED DURING BOT SPEECH (rms={utterance_rms:.4f})\n")
                self.set_status(state="monitoring_frontbit_link_output", skipped="during_bot_speech")
                return
            if final:
                self.log.append(f"[{timestamp:%H:%M:%S}] CALLER SPOKE DURING BOT SPEECH (rms={utterance_rms:.4f})\n")
        if final:
            self.log.append(
                f"[LEVEL] utterance={utterance_id} rms={utterance_rms:.4f} p90={utterance_p90:.4f} "
                f"caller_level={caller_level if caller_level is None else round(caller_level, 4)}\n")
        if (
            FORCE_LANGUAGE == "hi"
            and final
            and (utterance_peak < 0.19 and utterance_rms < DIRECT_HINDI_MIN_RMS)
        ):
            self.set_status(
                state="monitoring_frontbit_link_output",
                skipped="quiet_background_rejected",
            )
            return
        if (
            FORCE_LANGUAGE == "hi"
            and caller_level is not None
            and utterance_rms < FAR_VOICE_RATIO * caller_level
            and utterance_rms < 0.008
        ):
            # Much quieter than the caller's own voice: someone far away.
            if final:
                self.log.append(f"[{timestamp:%H:%M:%S}] FAR VOICE IGNORED (rms={utterance_rms:.4f})\n")
            self.set_status(state="monitoring_frontbit_link_output", skipped="far_voice_rejected")
            return
        if utterance_id in self.answered_utterances:
            self.set_status(state="monitoring_frontbit_link_output", skipped="already_answered_live")
            return
        started = time.perf_counter()
        language = self.preferred_language
        early_preview_answer = False

        def transcribe_english():
            if len(audio) >= 4000:
                try:
                    import speech_recognition as sr
                    int16_pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
                    audio_data = sr.AudioData(int16_pcm, 16000, 2)
                    r = sr.Recognizer()
                    google_text = r.recognize_google(audio_data, language="en-IN")
                    if google_text and len(google_text.strip()) > 0:
                        return google_text.strip()
                except Exception:
                    pass
            segments, _ = self.stt.transcribe(
                audio, language="en", beam_size=1, vad_filter=False,
                condition_on_previous_text=False, temperature=0,
                no_speech_threshold=0.50, log_prob_threshold=-0.85,
                compression_ratio_threshold=2.2, hallucination_silence_threshold=0.5,
                hotwords="english hindi gujarati hello how are you what is your name working hours location contact price appointment thank you",
            )
            accepted = [
                segment for segment in segments
                if segment.no_speech_prob < 0.55
                and segment.avg_logprob >= -0.85
                and segment.compression_ratio < 2.2
            ]
            return " ".join(segment.text.strip() for segment in accepted).strip()

        # English recognition is intentionally bypassed in direct-Hindi mode.
        question = "" if FORCE_LANGUAGE == "hi" else transcribe_english()

        # Before the first FAQ, ask only for a language choice. We use the
        # multilingual model once here; after selection this branch is never
        # used again during the call.
        if self.awaiting_language and final:
            # Preserve the existing fast English path. If tiny.en cannot
            # identify the language name, use one multilingual pass instead
            # of running separate Hindi and Gujarati decodes sequentially.
            language = preferred_language(question)
            choice_question = question
            choice_seconds = 0.0
            detected_language = "en" if language == "en" else ""
            detected_probability = 1.0 if language == "en" else 0.0
            self.last_language_choice_diagnostics = []
            if language is None:
                choice_question, choice_seconds, detected_language, detected_probability = (
                    self.transcribe_language_choice(audio)
                )
                language = preferred_language(choice_question)
                # A short spoken language name can occasionally be rendered
                # blank, while Whisper's language classifier is still clear.
                if language is None and detected_probability >= 0.65:
                    language = {"en": "en", "hi": "hi", "gu": "gu"}.get(detected_language)
            self.set_status(
                state="language_selection",
                language_transcript_en=question,
                language_transcript_multilingual=choice_question,
                language_detected=detected_language,
                language_probability=round(detected_probability, 3),
                language_stt_seconds=round(choice_seconds, 3),
                language_segments=self.last_language_choice_diagnostics,
            )
            if language:
                self.preferred_language = language
                self.awaiting_language = False
                self.set_status(
                    state="language_selected",
                    awaiting_language=False,
                    preferred_language=language,
                    language_selection_source="spoken",
                )
                question = {"en": "English", "hi": "Hindi", "gu": "Gujarati"}[language]
                answer, score, alias = LANGUAGE_CONFIRMATIONS[language], 1.0, "language_selection"
            else:
                # The three-language prompt is played once at startup only.
                # Do not enqueue it again for unclear/noisy utterances; doing
                # so makes the bot talk continuously instead of listening.
                self.set_status(state="language_selection", awaiting_language=True, skipped="language_not_clear")
                return
        elif self.awaiting_language:
            self.set_status(state="language_selection", awaiting_language=True, partial_text=question)
            return
        else:
            # Hindi hybrid fast path: decode live preview chunks while the
            # caller is still speaking.  A preview is allowed to answer only
            # when the same FAQ intent is repeated across preview chunks (or
            # one exceptionally confident short FAQ is already complete).
            # Otherwise the existing final full-utterance path remains the
            # source of truth, so noisy partial text cannot trigger a reply.
            if language == "hi" and not final:
                if self.jobs.has_final():
                    # The caller's final question is waiting; this preview is stale.
                    self.set_status(skipped="stale_preview_dropped")
                    return
                preview_question, preview_seconds, _ = self.hindi_transcript(
                    audio, utterance_id, False, voiced_frames)
                timing["stt_ms"] = round(preview_seconds * 1000, 1)
                if preview_question:
                    self.chat_event("partial", preview_question, utt=utterance_id)
                if self.overlaps_bot_speech(timestamp):
                    echo, echo_score = self.is_bot_echo(preview_question, timestamp)
                    if echo:
                        self.set_status(state="caller_speaking", partial_text=preview_question,
                                        skipped="bot_echo_rejected", echo_score=round(echo_score, 3))
                        return
                match_started = time.perf_counter()
                preview_answer, preview_score, preview_alias = self.match_question(preview_question)
                timing["match_ms"] = round((time.perf_counter() - match_started) * 1000, 2)
                previous = self.hindi_preview_candidates.get(utterance_id)
                if preview_answer is not None and preview_score >= 0.90:
                    same_intent = previous and previous[0] == preview_alias
                    count = (previous[1] + 1) if same_intent else 1
                    self.hindi_preview_candidates[utterance_id] = (preview_alias, count, preview_answer)
                    self.set_status(
                        state="caller_speaking",
                        partial_text=preview_question,
                        partial_score=round(preview_score, 3),
                        partial_faq_alias=preview_alias,
                        partial_stt_seconds=round(preview_seconds, 3),
                        partial_confirmations=count,
                        utterance_id=utterance_id,
                    )
                    short_high_confidence = (
                        count >= 1
                        and preview_score >= 0.97
                        and len(preview_question.split()) <= 5
                        and len(audio) / 16000.0 >= 0.9
                    )
                    repeated_confidence = count >= 2 and preview_score >= 0.90
                    if ALLOW_EARLY_PREVIEW_ANSWER and (short_high_confidence or repeated_confidence):
                        question = preview_question
                        answer, score, alias = preview_answer, preview_score, preview_alias
                        early_preview_answer = True
                    else:
                        return
                else:
                    self.set_status(
                        state="caller_speaking",
                        partial_text=preview_question,
                        partial_score=round(preview_score, 3),
                        partial_stt_seconds=round(preview_seconds, 3),
                        utterance_id=utterance_id,
                    )
                    return

            if early_preview_answer:
                pass
            elif language == "hi":
                question, stt_part, reused = self.hindi_transcript(audio, utterance_id, True, voiced_frames)
                timing["stt_ms"] = round(stt_part * 1000, 1)
                timing["stt_reused_preview"] = reused
                during_bot = self.overlaps_bot_speech(timestamp)
                if during_bot:
                    echo, echo_score = self.is_bot_echo(question, timestamp, OVERLAP_ECHO_THRESHOLD)
                else:
                    echo, echo_score = False, 0.0
                if echo:
                    echo_line = f"[{timestamp:%H:%M:%S}] ECHO IGNORED ({echo_score:.2f}): {question}\n"
                    self.log.append(echo_line)
                    self.set_status(state="monitoring_frontbit_link_output", question=question,
                                    faq_matched=False, skipped="bot_echo_rejected",
                                    echo_score=round(echo_score, 3))
                    return
                cleaned = strip_prompt_leak(question)
                if cleaned != question:
                    self.log.append(f"[{timestamp:%H:%M:%S}] STT PROMPT LEAK REMOVED: {question} -> {cleaned}\n")
                    timing["prompt_leak_removed"] = True
                    question = cleaned
                match_started = time.perf_counter()
                answer, score, alias = self.match_question(question)
                timing["match_ms"] = round((time.perf_counter() - match_started) * 1000, 2)
                if (
                    (answer is None or score < live_gate(alias))
                    and getattr(self, "hindi_primary_stt", None) is not None
                    and self.hindi_stt is not None
                    and not during_bot
                    and not self.jobs.has_final()
                ):
                    found = self.retry_hindi_stt(audio, timestamp, timing)
                    if found is not None:
                        fb_question, answer, score, alias, source = found
                        self.log.append(f"[{timestamp:%H:%M:%S}] FALLBACK STT ({source}): {question} -> {fb_question}\n")
                        timing["fallback_used"] = True
                        timing["fallback_source"] = source
                        question = fb_question
                        self.last_final_transcript = question
                if during_bot and not LOUD_BARGE_IN_ASKS_REPEAT and (answer is None or score < OVERLAP_MIN_SCORE):
                    # Heard while the bot was talking and not a clear FAQ:
                    # almost always echo. Stay quiet; never "please repeat".
                    self.log.append(f"[{timestamp:%H:%M:%S}] IGNORED DURING BOT SPEECH: {question}\n")
                    self.set_status(state="monitoring_frontbit_link_output", question=question,
                                    faq_matched=False, skipped="unclear_during_bot_speech")
                    return
                if (
                    (answer is None or score < live_gate(alias))
                    and question
                    and LIVE_WINDOW_PADDING_SECONDS is not None
                    and FULL_WINDOW_RECHECK
                    and len(question.split()) >= 3
                    and not self.jobs.has_final()
                ):
                    # Accuracy guard: the fast short-window pass found no FAQ.
                    # Re-check with the original full 30 s window before
                    # falling back to the retry/unknown reply.
                    full_question, full_seconds = self.transcribe_hindi(audio, full_window=True)
                    timing["full_window_stt_ms"] = round(full_seconds * 1000, 1)
                    if full_question and full_question != question:
                        full_answer, full_score, full_alias = self.match_question(full_question)
                        if full_answer is not None and (answer is None or full_score > score):
                            question, answer, score, alias = full_question, full_answer, full_score, full_alias
                if answer is not None and score < live_gate(alias):
                    # Below the live gate: ask the caller to repeat (the
                    # existing retry flow) instead of staying silent.
                    timing["below_gate"] = round(score, 3)
                    answer, score, alias = None, 0.0, None
                elif answer is not None and score < 0.78:
                    # A matcher prediction that cleared its own margin checks.
                    timing["predicted_score"] = round(score, 3)
                    score = 0.78
                if answer is not None:
                    self.pending_hindi_question = None
                    self.pending_hindi_question_at = 0.0
            elif language == "gu":
                question, gujarati_seconds = self.transcribe_gujarati(audio)
                self.set_status(
                    gujarati_transcript=question,
                    gujarati_stt_seconds=round(gujarati_seconds, 3),
                    gujarati_segments=self.last_gujarati_diagnostics,
                )
                answer, score, alias = gujarati_match(question)
            else:
                answer, score, alias = faq_match(question)
            if final and answer is None and question:
                normalized_words = normalize_hindi_unknown(question)
                if language == "hi" and len(normalized_words) <= 2:
                    self.set_status(
                        state="monitoring_frontbit_link_output",
                        question=question,
                        faq_matched=False,
                        skipped="short_unknown_audio_rejected",
                    )
                    return
                if language == "hi" and utterance_rms < DIRECT_HINDI_UNKNOWN_MIN_RMS:
                    self.set_status(
                        state="monitoring_frontbit_link_output",
                        question=question,
                        faq_matched=False,
                        skipped="unmatched_background_rejected",
                    )
                    return
                if language == "hi":
                    now = time.monotonic()
                    pending_is_fresh = (
                        self.pending_hindi_question
                        and now - self.pending_hindi_question_at <= HINDI_RETRY_WINDOW_SECONDS
                    )
                    if not pending_is_fresh:
                        self.pending_hindi_question = question
                        self.pending_hindi_question_at = now
                        answer, score, alias = HINDI_REPEAT_PROMPT, 1.0, "retry_prompt"
                    else:
                        first_question = self.pending_hindi_question
                        combined_question = f"{first_question} {question}"
                        self.pending_hindi_question = None
                        self.pending_hindi_question_at = 0.0
                        answer, score, alias = self.match_question(combined_question)
                        self.set_status(
                            hindi_retry_first_question=first_question,
                            hindi_retry_second_question=question,
                            hindi_retry_combined_question=combined_question,
                        )
                        if answer is None or score < live_gate(alias):
                            # The joined text must match clearly on its own
                            # (call log 2026-10-30: a weak joined match gave
                            # an unrelated refund answer).
                            answer, score, alias = UNKNOWN_HINDI, 1.0, "unknown_after_retry"
                        else:
                            score = max(score, 0.80)
                            alias = f"retry_combined:{alias}"
                else:
                    answer = {"en": "I do not have an answer to that question yet.", "gu": UNKNOWN_GUJARATI}[language]
                    score, alias = 0.85, "unknown"

        stt_seconds = time.perf_counter() - started
        blank = not question or re.fullmatch(r"(?i)\[(blank_audio|silence|music|no_speech)\]", question)
        if not question or re.fullmatch(r"(?i)\[(blank_audio|silence|music|no_speech)\]", question):
            self.log.diagnostic("stt_blank_or_noise", utterance=utterance_id, final=final,
                               stt_seconds=round(stt_seconds, 3), transcript=question)
            self.set_status(state="monitoring_frontbit_link_output", skipped="noise_or_low_confidence",
                            stt_seconds=round(stt_seconds, 3), utterance_final=final)
            return
        required_score = 0.78 if final else 0.95
        if answer is None or score < required_score:
            if not final:
                self.set_status(
                    state="caller_speaking",
                    partial_text=question,
                    partial_score=round(score, 3),
                    stt_seconds=round(stt_seconds, 3),
                    utterance_id=utterance_id,
                )
                return
            caller_line = f"[{timestamp:%H:%M:%S}] Caller: {question}\n"
            print(caller_line, end="", flush=True)
            self.log.append(caller_line)
            ignored = f"[{dt.datetime.now():%H:%M:%S}] IGNORED: no confident FAQ match ({score:.2f})\n\n"
            self.log.append(ignored)
            self.log.diagnostic("faq_not_matched", utterance=utterance_id, transcript=question,
                               score=round(float(score), 3), final=final)
            self.set_status(
                state="monitoring_frontbit_link_output",
                question=question,
                faq_matched=False,
                faq_score=round(score, 3),
                stt_seconds=round(stt_seconds, 3),
            )
            return

        caller_kind = "Caller (live)" if not final else "Caller"
        caller_line = f"[{timestamp:%H:%M:%S}] {caller_kind}: {question}\n"

        normalized = " ".join(question.casefold().split())
        now = time.monotonic()
        if normalized == self.last_question and now - self.last_question_at < 2.5:
            self.log.append(caller_line)
            self.set_status(state="monitoring_frontbit_link_output", skipped="duplicate_question")
            return
        last_answer = getattr(self, "last_answer_text", None)
        if (
            last_answer == answer
            and now - getattr(self, "last_answer_at", 0.0) < SAME_ANSWER_COOLDOWN_SECONDS
        ):
            # The caller repeated the same question while waiting; one answer is enough.
            self.answered_utterances.add(utterance_id)
            self.chat_event("caller", question, utt=utterance_id, reason="same_answer_recently_given")
            self.log.append(caller_line + f"[{dt.datetime.now():%H:%M:%S}] SAME ANSWER NOT REPEATED\n\n")
            self.set_status(state="monitoring_frontbit_link_output", question=question,
                            skipped="same_answer_recently_given")
            return
        self.last_answer_text = answer
        self.last_answer_at = now
        if final and alias not in ("retry_prompt", "unknown_after_retry", "unknown"):
            # Learn how loud the real caller is (for the far-voice filter).
            self.__dict__.setdefault("caller_levels", collections.deque(maxlen=8)).append(utterance_rms)
        self.last_question = normalized
        self.last_question_at = now
        self.answered_utterances.add(utterance_id)
        self.hindi_preview_candidates.pop(utterance_id, None)
        self.preview_transcripts.pop(utterance_id, None)
        if len(self.answered_utterances) > 32:
            self.answered_utterances = set(sorted(self.answered_utterances)[-16:])

        # Start the answer first; printing and logging happen afterwards.
        if self.voice is not None and self.voice_ready.is_set():
            timing.update(
                end_of_speech_at=end_of_speech_at,
                answered_before_end_of_speech=not final,
                cached=self.answer_is_cached(answer, language),
                faq_alias=alias,
            )
            self.prepare_speech(answer, language)
            timing["cached"] = self.answer_is_cached(answer, language)
            self.pending_timing = timing
            self.remember_bot_speech(answer)
            self.voice_active.set()
            self.last_bot_playback_ended = time.monotonic() + 999.0
            if getattr(self, "segmenter", None):
                self.segmenter.reset()
            self.voice.send("stop")
            self.voice_requested_at = time.perf_counter()
            self.voice.send("speak", text=answer, language=language)
        answer_line = f"[{dt.datetime.now():%H:%M:%S}] FAQ: {answer}\n\n"
        self.log.diagnostic("faq_answer_selected", utterance=utterance_id, transcript=question,
                           answer=answer, score=round(float(score), 3), alias=alias,
                           final=final, voice_enabled=self.voice is not None,
                           voice_ready=self.voice_ready.is_set())
        self.chat_event("caller", question, utt=utterance_id)
        self.chat_event("bot", answer, utt=utterance_id, how=str(alias), score=round(float(score), 3),
                        stt="fallback" if timing.get("fallback_used") else "")
        print(caller_line, end="", flush=True)
        print(answer, flush=True)
        self.log.append(caller_line + answer_line)
        self.set_status(
            state="monitoring_frontbit_link_output",
            question=question,
            answer=answer,
            faq_matched=True,
            faq_score=round(score, 3),
            faq_alias=alias,
            language=language,
            stt_seconds=round(stt_seconds, 3),
            answered_live=not final,
            utterance_id=utterance_id,
        )

    def caller_level(self):
        """Typical loudness of the caller's answered questions in this call."""
        levels = sorted(getattr(self, "caller_levels", ()))
        if len(levels) < 2:
            return None
        return levels[len(levels) // 2]

    def match_question(self, question):
        """Pick the FAQ answer: word rules + the local meaning model. -> (answer, score, alias).

        1. An exact FAQ question form, or a product answer from the CSV: used.
        2. Otherwise the meaning model decides when it is sure.
        3. A word-rule guess is used only when the model agrees (its top 3).
        4. Otherwise no answer (the caller is asked to repeat).
        Without the model (missing / failed) the word rules work as before.
        """
        if bestzone_faq.normalize(question) in ACKNOWLEDGEMENTS:
            return None, 0.0, None  # "ठीक है" after an answer is not a question
        answer, score, alias = self.rule_match(question)
        semantic = getattr(self, "semantic", None)
        if semantic is None or not semantic.available or not question:
            return answer, score, alias
        how = str(alias or "").split(":")[0]
        guessed = how in ("keywords", "predicted", "sound", "words", "retry_combined")
        if answer is not None and not guessed:
            return answer, score, alias  # exact FAQ form, CSV product answer, small talk
        if not any(word not in hindi_faq_function_words() for word in hindi_normalize(question).split()):
            # Only "कैसे" / "कैसे करे" heard (STT dropped the topic word): the
            # model must not guess a topic ("कैसे" -> "आप कैसे हैं").
            return answer, score, alias
        try:
            ranked = semantic.ranked(fix_stt_words(question), limit=SEMANTIC_AGREE_TOP)
        except Exception as exc:  # never let the model break a call
            self.log.append(f"[MEANING MODEL ERROR] {exc}\n")
            return answer, score, alias
        if ranked:
            top_sim, top_answer, top_form, _ = ranked[0]
            margin = top_sim - (ranked[1][0] if len(ranked) > 1 else 0.0)
            if top_sim >= SEMANTIC_MIN and margin >= SEMANTIC_MARGIN:
                return top_answer, 0.85, f"meaning:{top_form}:{top_sim:.3f}"
            if answer is not None and any(item[1] == answer for item in ranked):
                return answer, score, alias  # word-rule guess confirmed by the model
        general = getattr(self, "_last_csv_general", None)
        if general is not None:
            # The CSV's general answer for a clearly heard question type
            # ("... की कीमत क्या है" -> price) beats an unconfirmed guess.
            return general
        if answer is not None and score >= live_gate(alias):
            # The model does not support this word-rule guess: ask to repeat.
            return answer, min(score, 0.5), f"unconfirmed:{alias}"
        return answer, score, alias

    def rule_match(self, question):
        """Answer from the BestZone CSV first, then the dashboard FAQ (word rules)."""
        if bestzone_faq.normalize(question) in ACKNOWLEDGEMENTS:
            return None, 0.0, None  # "ठीक है" after an answer is not a question
        catalog = getattr(self, "bestzone", None) or bestzone_faq.load_catalog()
        context = None
        product_at = getattr(self, "bestzone_product_at", 0.0)
        if time.monotonic() - product_at <= PRODUCT_CONTEXT_SECONDS:
            context = getattr(self, "bestzone_product", None)
        result = bestzone_faq.match(question, catalog, context_product=context)
        if result["kind"] is None:
            # Same question with common phone-STT spellings corrected ("किम" = कीमत).
            result = bestzone_faq.match(fix_stt_words(question), catalog, context_product=context)
        self._last_csv_general = (
            (result["answer"], result["score"], result["alias"])
            if result["kind"] == "answer" and not result["product"] else None)
        if result["kind"] is not None and result["product"]:
            # A product was named: the product answer from the CSV.
            self.bestzone_product = result["product"]
            self.bestzone_product_at = time.monotonic()
            return result["answer"], result["score"], result["alias"]
        # No product: the dashboard FAQ (website FAQ + small talk) explains
        # general questions better than the CSV's per-product template.
        answer, score, alias = hindi_match(fix_stt_words(question))
        if answer is not None and answer in active_faq_answers() and score >= live_gate(alias):
            return answer, score, alias
        # Same meaning, garbled words ("मेदा होडर को है" = मेरा ऑर्डर कहाँ है):
        # the FAQ whose question sounds closest, if it is clearly the best.
        # (also before the CSV's general answer: the website FAQ explains more,
        # e.g. "भी लवरे कितने दिन में" = डिलीवरी में कितने दिन लगते हैं)
        sound_answer, sound_alias = sound_match(question)
        if sound_answer is not None:
            return sound_answer, 0.80, sound_alias
        if result["kind"] is not None:
            return result["answer"], result["score"], result["alias"]
        if answer is not None and answer in active_faq_answers():
            return answer, score, alias  # below the gate: the caller is asked to repeat
        return None, 0.0, None

    def prepare_speech(self, answer, language):
        """Join a BestZone answer from its cached pieces before it is spoken."""
        cache = getattr(self.voice, "pcm_cache", None)
        if not isinstance(cache, dict) or (language, answer) in cache:
            return
        parts = bestzone_faq.speech_parts(answer, getattr(self, "bestzone", None))
        pieces = [cache.get((language, part)) for part in parts]
        if len(parts) < 2 or any(piece is None for piece in pieces):
            return  # CableVoice generates it on demand, as before
        pause = b"\x00" * (int(48000 * ANSWER_PART_PAUSE_SECONDS) * 4)  # 48 kHz stereo int16
        cache[(language, answer)] = pause.join(pieces)
        joined = self.__dict__.setdefault("joined_answers", collections.deque())
        joined.append((language, answer))
        while len(joined) > 40:  # keep memory small: drop the oldest joins
            cache.pop(joined.popleft(), None)

    def answer_is_cached(self, answer, language):
        cache = getattr(self.voice, "pcm_cache", None)
        return isinstance(cache, dict) and (language, answer) in cache

    def save_utterance_audio(self, utterance_id, audio):
        """Keep what the bot heard (16 kHz WAV) next to the log, for checking STT later."""
        if not SAVE_UTTERANCE_AUDIO:
            return
        folder = LOG_DIR / "audio" / Path(self.log.path).stem
        path = folder / f"utt_{utterance_id:04d}.wav"
        pcm = (np.clip(np.asarray(audio, dtype=np.float32), -1.0, 1.0) * 32767).astype("<i2").tobytes()

        def write():
            import wave
            try:
                folder.mkdir(parents=True, exist_ok=True)
                with wave.open(str(path), "wb") as wav:
                    wav.setnchannels(1)
                    wav.setsampwidth(2)
                    wav.setframerate(16000)
                    wav.writeframes(pcm)
            except Exception:
                pass  # a diagnostic only; never disturb the call

        threading.Thread(target=write, daemon=True, name="save-audio").start()

    def worker(self):
        while not self.stop.is_set():
            job = self.jobs.get()
            if job is None:
                break
            work_started = time.perf_counter()
            queue_wait_ms = round((work_started - job["queued_at"]) * 1000, 1)
            self.log.diagnostic("audio_processing_start", utterance=job["utterance_id"],
                               final=job["final"], queue_wait_ms=queue_wait_ms)
            if job["final"]:
                self.save_utterance_audio(job["utterance_id"], job["audio"])
                self.last_final_transcript = None
                with self.status_lock:
                    self.last_status.pop("skipped", None)
            try:
                self.process_audio(
                    job["audio"], job["timestamp"], job["utterance_id"], job["final"],
                    voiced_frames=job["voiced_frames"],
                    end_of_speech_at=job["end_of_speech_at"],
                    queue_wait_ms=queue_wait_ms,
                )
                if job["final"] and job["utterance_id"] not in self.answered_utterances:
                    # Say why a finished utterance got no answer, with what STT heard.
                    reason = self.last_status.get("skipped") or "no_answer"
                    heard = self.last_final_transcript or ""
                    raw = ""
                    if not heard and reason == "noise_or_low_confidence":
                        raw = " ".join(item.get("text", "") for item in getattr(self, "last_hindi_diagnostics", [])
                                       if isinstance(item, dict)).strip()
                    if heard or raw or reason != "during_bot_speech":
                        self.chat_event("caller", heard, utt=job["utterance_id"], reason=reason, raw=raw)
                    if reason not in ("during_bot_speech", "far_voice_rejected"):  # already logged
                        self.log.append(
                            f"[NOT ANSWERED] utterance={job['utterance_id']} reason={reason} "
                            f"heard='{self.last_final_transcript or ''}'"
                            # Empty transcript: show what STT decoded and why it was rejected.
                            + ("" if self.last_final_transcript else
                               f" stt={json.dumps(getattr(self, 'last_hindi_diagnostics', []), ensure_ascii=False)}")
                            + "\n")
            except Exception as exc:
                self.log.diagnostic("worker_error", utterance=job["utterance_id"],
                                   error=f"{type(exc).__name__}: {exc}")
                self.log.append(f"[WORKER ERROR] {exc}\n")
                self.set_status(state="error", error=str(exc))
            finally:
                finished = time.perf_counter()
                self.log.append(
                    f"[TIMING] utterance={job['utterance_id']} "
                    f"kind={'final' if job['final'] else 'preview'} "
                    f"audio_seconds={len(job['audio']) / 16000.0:.3f} "
                    f"queue_wait_ms={queue_wait_ms} "
                    f"processing_ms={(finished - work_started) * 1000:.1f}\n"
                )
                self.log.diagnostic("audio_processing_end", utterance=job["utterance_id"],
                                   final=job["final"], processing_ms=round((finished - work_started) * 1000, 1),
                                   answered=job["utterance_id"] in self.answered_utterances,
                                   skipped=self.last_status.get("skipped"))

    @classmethod
    def phone_call_audio_active(cls, auto_answer=None):
        """Check native Sefirah call state, UI Automation, Bluetooth audio, or dynamic ADB.
        Returns (is_active: bool | None, reason: str). None indicates check error/timeout.
        """
        # 0. Direct Native Sefirah CallState Check (100% Native C#, 0 ADB, 0 UI Clicks)
        try:
            call_state_file = ROOT / "call_state.json"
            if call_state_file.exists():
                st_data = json.loads(call_state_file.read_text(encoding="utf-8"))
                st = st_data.get("state")
                ts = float(st_data.get("timestamp", 0)) / 1000.0
                if time.time() - ts < 120.0:
                    if st in ("talking", "in_progress"):
                        return True, "native_sefirah_talking"
                    elif st == "ringing":
                        return False, "native_sefirah_ringing"
                    elif st == "idle":
                        return False, "native_sefirah_idle"
        except Exception:
            pass

        # 1. Real Phone State Check via ADB (Absolute truth: whether call is active on device)
        try:
            adb_dev = _get_adb_device()
            adb_bin = _get_adb_path()
            if adb_dev and adb_bin:
                import subprocess, os
                env = _adb_env()
                res = subprocess.run([adb_bin, "-s", adb_dev, "shell", "dumpsys", "telephony.registry"],
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=1.2, env=env)
                states = [int(line.split("=")[1].strip()) for line in res.stdout.splitlines() if "mCallState=" in line]
                if any(s == 2 for s in states):  # 2 = OFFHOOK / IN_CALL
                    return True, "adb_offhook"
                elif any(s == 1 for s in states):  # 1 = RINGING
                    return False, "adb_ringing"
                elif states and all(s == 0 for s in states):  # 0 = IDLE
                    # Call ended on device! Close any stuck floating window immediately.
                    if auto_answer and getattr(auto_answer, "in_call", False):
                        auto_answer.in_call = False
                        if hasattr(auto_answer, "close_stuck_call_window"):
                            auto_answer.close_stuck_call_window()
                    return False, "adb_idle"
        except Exception:
            pass

        # 2. UI Check via FrontbitAutoAnswer in-call elements (End button, Transfer to PC, Mute, Hold)
        if auto_answer and getattr(auto_answer, "in_call", False):
            return True, "frontbit_ui_in_call"

        # 3. Pycaw Audio Sessions Check
        try:
            from pycaw.utils import AudioUtilities

            for session in AudioUtilities.GetAllSessions():
                display = session.DisplayName or ""
                if "Hands-Free" in display and int(session.State) == 1:
                    return True, "hands_free"
        except Exception:
            pass

        return False, "no_provider"

    def _play_welcome(self, source):
        if not self.call_active:
            return
        greeting = load_greeting()
        if greeting and self.voice is not None and self.voice_ready.is_set():
            self.remember_bot_speech(greeting)
            self.voice_active.set()
            self.last_bot_playback_ended = time.monotonic() + 999.0
            if getattr(self, "segmenter", None):
                self.segmenter.reset()
            self.voice.send("stop")
            self.voice.send("speak", text=greeting, language="hi")
            self.chat_event("bot", greeting, how="welcome")
        self.set_status(state="welcome_playing", welcome_delayed=source == "loopback_activity")

    def _welcome_after_pickup(self, source, call_seen_at):
        """Speak the greeting once the call is picked up, not while it rings.

        Frontbit Link opens the Bluetooth Hands-Free audio around the ring, so the call
        "starts" before the auto-answer delay; a greeting then was lost.
        """
        auto = getattr(self, "auto_answer", None)
        if auto is not None:
            # The incoming-call notification appears after the Hands-Free audio opens: give
            # the auto-answer time to see it before deciding nobody is ringing.
            toast_deadline = time.monotonic() + RING_TOAST_WAIT_SECONDS
            while (not auto.ringing and self.call_active and time.monotonic() < toast_deadline
                   and not (auto.answered_at is not None and auto.answered_at >= call_seen_at - 1.0)):
                time.sleep(0.05)
            deadline = time.monotonic() + 130.0  # longest pickup delay + margin
            while auto.ringing and self.call_active and time.monotonic() < deadline:
                time.sleep(0.05)
            answered = auto.answered_at
            if answered is not None and answered >= call_seen_at - 1.0:
                # Brief 0.25s pause after pickup so line stabilizes before greeting
                time.sleep(max(0.0, min(0.05, answered + WELCOME_AFTER_PICKUP_SECONDS - time.monotonic())))
        self._play_welcome(source)

    def begin_call(self, source="hands_free_session"):
        # A call must not change Android media volume or the Windows default
        # recording endpoint.  Both are global settings and can turn a clean
        # wired route into a caller-voice feedback loop.  Audio capture below
        # already selects the physical Realtek microphone explicitly.  The
        # only phone-side adjustment is a conservative voice-call cap: the
        # old code forced stream 0 to 9 (maximum), which is too hot for the
        # USB headset/wired route and is the direct source of the new echo.
        try:
            adb_dev = _get_adb_device()
            adb_bin = _get_adb_path()
            if adb_dev and adb_bin:
                import subprocess
                subprocess.run(
                    [adb_bin, "-s", adb_dev, "shell", "cmd", "media_session",
                     "volume", "--stream", "0", "--set", "3"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=1.0,
                    env=_adb_env(),
                )
        except Exception:
            pass
        with self.language_lock:
            if self.call_active:
                return
            self.call_active = True
            self.call_detection_source = source
            self.last_call_activity_at = time.monotonic()
            self.awaiting_language = False
            self.preferred_language = FORCE_LANGUAGE
            self.language_prompt_played = False
            self.language_prompt_utterance = None
            self.answered_utterances.clear()
            self.last_question = None
            self.last_question_at = 0.0
            self.last_answer_text = None
            self.last_answer_at = 0.0
            self.bestzone_product = None
            self.bestzone_product_at = 0.0
            self.caller_levels = collections.deque(maxlen=8)
            self.pending_hindi_question = None
            self.pending_hindi_question_at = 0.0
            self.hindi_preview_candidates.clear()
        chat = getattr(self, "chat", None)
        if chat is not None:
            try:
                chat.clear()
            except Exception:
                pass
        self.chat_event("call_start", source=source)
        self.log.diagnostic("call_start", source=source, call_active=True)
        # Preferred-language prompts are intentionally disabled in
        # direct-Hindi mode. The bot listens immediately after call pickup.
        if source == "loopback_activity":
            self.welcome_guard_until = time.monotonic() + FALLBACK_WELCOME_GUARD_SECONDS
            self.welcome_timer = threading.Timer(
                FALLBACK_WELCOME_DELAY_SECONDS,
                self._play_welcome,
                args=(source,),
            )
            self.welcome_timer.daemon = True
            self.welcome_timer.start()
        else:
            self.welcome_guard_until = 0.0
            threading.Thread(target=self._welcome_after_pickup, args=(source, time.monotonic()),
                             daemon=True).start()
        self.set_status(
            state="welcome_pending" if source == "loopback_activity" else "welcome_playing",
            call_active=True,
            awaiting_language=False,
            preferred_language=FORCE_LANGUAGE,
            language_prompt_played=False,
            language_selection_source="forced_hindi",
            call_detection_source=source,
        )

    def end_call(self):
        with self.language_lock:
            if not self.call_active:
                return
            ended_source = self.call_detection_source
            self.call_active = False
            self.call_detection_source = None
            self.fallback_trigger_utterance_id = None
            self.welcome_guard_until = 0.0
            self.awaiting_language = False
            self.preferred_language = FORCE_LANGUAGE
            self.language_prompt_played = False
            self.language_prompt_utterance = None
            self.pending_hindi_question = None
            self.pending_hindi_question_at = 0.0
        self.chat_event("call_end")
        self.log.diagnostic("call_end", source=ended_source, call_active=False)
        if self.voice is not None:
            self.voice.send("stop")
        if self.welcome_timer is not None:
            self.welcome_timer.cancel()
            self.welcome_timer = None
        self.set_status(
            state="waiting_for_phone_call",
            call_active=False,
            awaiting_language=False,
            preferred_language=FORCE_LANGUAGE,
            language_prompt_played=False,
            language_selection_source="forced_hindi",
            call_detection_source=None,
        )

    def call_monitor(self):
        self.idle_consecutive_checks = 0
        while not self.stop.is_set():
            auto = getattr(self, "auto_answer", None)
            active, reason = self.phone_call_audio_active(auto)
            
            # Start call ONLY when call is genuinely active and audio is connected (NEVER on ringing or static!)
            if active is True and not self.call_active:
                source_name = "native_sefirah" if "native" in str(reason) else ("frontbit_ui" if "frontbit" in str(reason) else "auto_answer")
                self.begin_call(source=source_name)
                self.idle_consecutive_checks = 0
            elif self.call_active:
                if active is True:
                    self.idle_consecutive_checks = 0
                    self.last_call_activity_at = time.monotonic()
                    if "frontbit" in str(reason):
                        self.call_detection_source = "frontbit_ui"
                    elif "native" in str(reason):
                        self.call_detection_source = "native_sefirah"
                    elif reason == "hands_free":
                        self.call_detection_source = "hands_free_session"
                elif active is False and reason in ("native_sefirah_idle", "adb_idle"):
                    self.idle_consecutive_checks += 1
                    if self.idle_consecutive_checks >= 3:
                        self.end_call()
                        self.idle_consecutive_checks = 0
                        if auto:
                            auto.answered_at = None
                            auto.in_call = False
                elif active is False and not str(reason).startswith("adb_error") and reason != "native_sefirah_ringing":
                    self.idle_consecutive_checks += 1
                    time_since_answered = (time.monotonic() - auto.answered_at) if (auto and auto.answered_at) else 999
                    # Require consecutive idle checks and time elapsed since answer
                    if self.idle_consecutive_checks >= 8 and time_since_answered > 15.0:
                        self.end_call()
                        self.idle_consecutive_checks = 0
                        if auto:
                            auto.answered_at = None
                            auto.in_call = False
            self.stop.wait(0.4)

    def capture(self):
        disable_realtek_mic_monitoring()
        info = choose_loopback(self.args.loopback_name)
        selected_name = str(info.get("name", ""))
        try:
            from pycaw.utils import AudioUtilities as _AU
            # Set the endpoint that PyAudio will actually open.  GetMicrophone()
            # returns the Windows default capture device, which can be CABLE
            # Output even while choose_loopback() selects the physical Realtek
            # mic; changing the default therefore did not reduce the cable
            # input gain at all.
            selected = next(
                (device for device in _AU.GetAllDevices()
                 if device.FriendlyName.casefold() == selected_name.casefold()),
                None,
            )
            if selected is not None:
                # Ensure Windows recording level is high enough to capture caller voice
                current = float(selected.EndpointVolume.GetMasterVolumeLevelScalar())
                if current < 0.90:
                    selected.EndpointVolume.SetMasterVolumeLevelScalar(1.0, None)
        except Exception:
            pass

        index = int(info["index"])
        name = info["name"]
        channels = int(info.get("maxInputChannels") or 2)
        rate = int(info.get("defaultSampleRate") or 48000)
        engine = pa.PyAudio()
        self.log.diagnostic("capture_open_requested", device=name, index=index,
                           channels=channels, rate=rate)
        stream = None
        segmenter = LiveFaqSegmenter(
            self.enqueue,
            self.on_speech_start,
            threshold=max(self.args.threshold, DIRECT_HINDI_VAD_THRESHOLD)
            if FORCE_LANGUAGE == "hi" else self.args.threshold,
            # Hindi hybrid mode starts a preview decode while speech is still
            # active. Keep a 700 ms endpoint so normal pauses inside a Hindi
            # sentence do not become separate caller questions, and allow
            # long address/product questions to finish before STT runs.
            silence_frames=HINDI_ENDPOINT_FRAMES if FORCE_LANGUAGE == "hi" else 2,
            max_frames=HINDI_MAX_UTTERANCE_FRAMES if FORCE_LANGUAGE == "hi" else 30,
            enable_previews=True,
            end_ratio=SPEECH_END_RATIO if FORCE_LANGUAGE == "hi" else 0.0,
            periodic_previews=True,
        )
        self.segmenter = segmenter
        chunks = 0
        try:
            stream = engine.open(
                format=pa.paInt16,
                channels=channels,
                rate=rate,
                input=True,
                input_device_index=index,
                frames_per_buffer=max(1, rate // 10),
            )
            self.set_status(
                state="monitoring_frontbit_link_output",
                capture_device=name,
                capture_index=index,
                capture_rate=rate,
                capture_channels=channels,
            )
            self.log.diagnostic("capture_opened", device=name, index=index,
                               channels=channels, rate=rate)
            while not self.stop.is_set():
                raw = stream.read(rate // 10, exception_on_overflow=False)
                samples = np.frombuffer(raw, dtype="<i2").astype(np.float32)
                samples = samples.reshape(-1, channels).mean(axis=1) / 32768.0
                if rate == 48000:
                    samples = samples[: (len(samples) // 3) * 3].reshape(-1, 3).mean(axis=1)
                elif rate != 16000:
                    new_len = max(1, int(len(samples) * 16000 / rate))
                    x = np.arange(new_len) * rate / 16000
                    samples = np.interp(x, np.arange(len(samples)), samples).astype(np.float32)
                
                # Digital boost for clear caller speech detection while keeping clipping safe
                samples = np.clip(samples * 2.2, -1.0, 1.0)

                # IDLE GATE: Only feed audio to the VAD when a call is actively answered/talking!
                # When idle or ringing, do not trigger false utterances on background line noise.
                if not self.call_active:
                    if segmenter.parts:
                        segmenter.reset()
                    continue

                # ECHO SUPPRESSION: While the bot speaks or within 1.2s post-speech tail:
                # Do not feed speaker output back into the VAD!
                bot_talking = (
                    self.voice_active.is_set()
                    or (time.monotonic() - getattr(self, "last_bot_playback_ended", 0.0)) < 1.2
                )
                if bot_talking:
                    if segmenter.parts:
                        segmenter.reset()
                    samples = np.zeros_like(samples)

                rms = segmenter.feed(samples)
                chunks += 1
                if chunks % 5 == 0:
                    self.set_status(
                        state="monitoring_frontbit_link_output",
                        rms=round(rms, 6),
                        pending=self.jobs.qsize(),
                    )
        finally:
            self.log.diagnostic("capture_stopped", device=name, index=index,
                               chunks=chunks, stop_requested=self.stop.is_set())
            try:
                segmenter.flush()
            finally:
                if stream is not None:
                    stream.stop_stream()
                    stream.close()
                engine.terminate()

    def run(self):
        auto_answer = FrontbitAutoAnswer(self.args.auto_answer_delay, delay_file=ROOT / "pickup_delay.json",
                                          business_file=ROOT / "business_hours.json")
        self.auto_answer = auto_answer  # the greeting waits for Frontbit's pickup
        auto_answer.start()
        try:
            self.load()
        except Exception:
            # Write the last loading status now so main()'s error status wins.
            self.status_writer.flush()
            raise
        signal.signal(signal.SIGINT, lambda *_: self.stop.set())
        worker = threading.Thread(target=self.worker, daemon=True)
        call_monitor = threading.Thread(target=self.call_monitor, daemon=True)
        worker.start()
        call_monitor.start()
        try:
            self.capture()
        finally:
            self.stop.set()
            auto_answer.stop()
            self.jobs.close()
            worker.join(timeout=3)
            call_monitor.join(timeout=2)
            if self.voice is not None:
                self.voice.close()
            self.set_status(state="stopped", ready=False)
            self.status_writer.flush()
            self.log.close()


def ensure_dashboard_server():
    """Run dashboard HTTP server on port 8765 in background so Frontbit Link always connects."""
    try:
        import urllib.request
        try:
            with urllib.request.urlopen("http://127.0.0.1:8765/api/get_status", timeout=0.8) as r:
                if r.status == 200:
                    return
        except Exception:
            pass

        from faq_voice_start_page import DashboardApi, HTML_FILE
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class ResilientHTTPServer(ThreadingHTTPServer):
            allow_reuse_address = True
            daemon_threads = True
            def handle_error(self, request, client_address):
                pass

        api = DashboardApi()
        class Handler(BaseHTTPRequestHandler):
            def _set_cors(self):
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "*")
            def do_OPTIONS(self):
                self.send_response(200); self._set_cors(); self.end_headers()
            def _json(self, payload, status=200):
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self._set_cors()
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            def do_GET(self):
                if self.path in ("/", "/index.html", "/dashboard"):
                    data = HTML_FILE.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self._set_cors()
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                elif self.path.startswith("/api/"):
                    name = self.path.removeprefix("/api/").split("?")[0]
                    try:
                        self._json(getattr(api, name)())
                    except Exception as exc:
                        self._json({"ok": False, "error": str(exc)}, 500)
                else:
                    self._json({"error": "not found"}, 404)
            def do_POST(self):
                try:
                    name = self.path.removeprefix("/api/").split("?")[0]
                    length = int(self.headers.get("Content-Length", "0"))
                    raw = self.rfile.read(length) if length > 0 else b"[]"
                    args = json.loads(raw) if raw.strip() else []
                    if isinstance(args, dict):
                        res = getattr(api, name)(**args)
                    elif isinstance(args, list):
                        res = getattr(api, name)(*args)
                    else:
                        res = getattr(api, name)(args)
                    self._json(res)
                except Exception as exc:
                    self._json({"ok": False, "error": str(exc)}, 500)
            def log_message(self, *_):
                pass

        def _run_server():
            try:
                srv = ResilientHTTPServer(("0.0.0.0", 8765), Handler)
                srv.serve_forever()
            except Exception:
                pass

        threading.Thread(target=_run_server, daemon=True, name="dashboard-server").start()
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(description="ADB-free Frontbit Link loopback FAQ bot")
    parser.add_argument("--loopback-name", default=None, help="Audio input device name (default: auto-detects based on Bluetooth vs Wired Adapter)")
    parser.add_argument("--threshold", type=float, default=0.045)
    parser.add_argument("--no-voice", action="store_true", help="Transcribe only; do not send bot voice to caller")
    parser.add_argument("--fast-start", action="store_true", help="Start voice bot without waiting for the full TTS cache")
    parser.add_argument("--list-devices", action="store_true")
    parser.add_argument("--auto-answer-delay", type=float, default=0.0)
    args = parser.parse_args()
    if args.list_devices:
        print(json.dumps(loopback_devices(), ensure_ascii=False, indent=2))
        return
    ensure_dashboard_server()
    FrontbitLinkNoAdbBot(args).run()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        write_status(state="error", error=str(exc))
        print("ERROR:", exc, file=sys.stderr)
        raise
