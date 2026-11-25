"""LakshVoiceAgent FAQ dashboard. Changes only the copied Hindi FAQ table."""
from __future__ import annotations

import ast
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FAQ_FILE = ROOT / "hindi_faq.py"
HTML_FILE = ROOT / "faq_voice_dashboard.html"
BOT_FILE = ROOT / "phone_link_no_adb_bot.py"
BOT_PYTHON = (
    ROOT.parent / "venv" / "Scripts" / "python.exe"
    if (ROOT.parent / "venv" / "Scripts" / "python.exe").exists()
    else (
        ROOT / "venv" / "Scripts" / "python.exe"
        if (ROOT / "venv" / "Scripts" / "python.exe").exists()
        else (
            Path(r"C:\Frontbit Link\VoiceAgent\venv\Scripts\python.exe")
            if Path(r"C:\Frontbit Link\VoiceAgent\venv\Scripts\python.exe").exists()
            else Path(sys.executable)
        )
    )
)
STATUS_FILE = ROOT / "status.json"


def project_bots():
    try:
        import psutil
        found = []
        for process in psutil.process_iter(["pid", "cmdline"]):
            try:
                cmdline = process.info.get("cmdline") or []
                if any(str(part).lower().endswith(("phone_link_no_adb_bot.py", "frontbit_link_bot.py")) for part in cmdline):
                    found.append(process)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return found
    except Exception:
        return []


class DashboardApi:
    def load_faqs(self):
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        importlib.invalidate_caches()
        module = importlib.import_module("hindi_faq")
        module = importlib.reload(module)
        rows = list(module.HINDI_FAQS)
        keyword_rows = list(getattr(module, "HINDI_FAQ_KEYWORDS", []))
        result = []
        for i, (aliases, answer) in enumerate(rows):
            groups = keyword_rows[i] if i < len(keyword_rows) else ((), (), ())
            result.append({"id": i, "question": aliases[0], "answer": answer,
                           "variations": list(aliases[1:]),
                           "keywords": {"hindi": list(groups[0]), "english": list(groups[1]), "hinglish": list(groups[2])}})
        return result

    def refresh_faqs(self):
        try:
            return {"ok": True, "faqs": self.load_faqs(), "file": str(FAQ_FILE)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @staticmethod
    def auto_variations(question):
        q = " ".join(str(question).split()).strip(" ?।")
        if not q:
            return []
        values = [q, f"मुझे जानना है कि {q}", f"कृपया बताइए {q}",
                  f"आप मुझे बताइए {q}", f"इसके बारे में जानकारी चाहिए",
                  f"मुझे इसके बारे में बताइए", f"ज़रा बताइए {q}",
                  f"क्या आप बता सकते हैं {q}"]
        roman_map = {"क्ष":"ksh", "ज्ञ":"gya", "त्र":"tra", "च":"ch", "छ":"chh", "श":"sh", "ष":"sh", "थ":"th", "ध":"dh", "भ":"bh", "फ":"ph", "ख":"kh", "घ":"gh", "ठ":"th", "ढ":"dh", "ट":"t", "ड":"d", "ण":"n", "क":"k", "ग":"g", "ज":"j", "झ":"jh", "त":"t", "द":"d", "न":"n", "प":"p", "ब":"b", "म":"m", "य":"y", "र":"r", "ल":"l", "व":"v", "स":"s", "ह":"h", "आ":"aa", "अ":"a", "इ":"i", "ई":"ee", "उ":"u", "ऊ":"oo", "ए":"e", "ऐ":"ai", "ओ":"o", "औ":"au", "ा":"aa", "ि":"i", "ी":"ee", "ु":"u", "ू":"oo", "े":"e", "ै":"ai", "ो":"o", "ौ":"au", "ं":"n", "ँ":"n", "ः":"h", "्":""}
        roman = re.sub(r"\s+", " ", "".join(roman_map.get(c, c) for c in q)).strip().lower()
        if roman != q:
            values += [roman, f"mujhe jaanna hai ki {roman}", f"kripya bataiye {roman}",
                       f"aap mujhe bataiye {roman}", f"please bataiye {roman}",
                       f"can you tell me {roman}", f"i want to know {roman}"]
        if q.startswith("क्या "):
            body = q[4:].strip()
            values += [f"{body} बताइए", f"{body} के बारे में बताइए"]
        elif q.endswith("क्या है"):
            body = q[:-6].strip()
            values += [f"{body} बताइए", f"{body} के बारे में जानकारी चाहिए"]
        return list(dict.fromkeys(v.strip(" ?।") for v in values if v.strip(" ?।") != q))

    def generate_variations(self, question):
        return {"ok": True, "variations": self.auto_variations(question)}

    def generate_ai_variations(self, question, answer):
        key = os.environ.get("GROQ_API_KEY", "").strip()
        if not key:
            return {"ok": False, "error": "GROQ_API_KEY is not set. Restart dashboard after setting it."}
        model = os.environ.get("GROQ_VARIATION_MODEL", "qwen/qwen3.8-27b")
        greeting_words = ("नमस्ते", "नमस्कार", "hello", "hi", "हेलो", "हैलो", "हाय")
        greeting_rules = (
            "Focus on greetings and opening conversation phrases only.\n"
            "Do not include questions such as 'आप कैसे हैं?' or 'क्या हाल है?' as automatic matches unless the context clearly shows that they function as a greeting."
            if any(word in question.lower() for word in greeting_words)
            else
            "Focus only on the intent of this FAQ. Do not include unrelated FAQ intents or generic questions from other topics."
        )
        prompt = f'''FAQ QUESTION:
{question}

FAQ ANSWER:
{answer}

LANGUAGE:
Hindi + Hinglish + Roman Hindi + English

BUSINESS CONTEXT:
An Indian business voice-call agent that answers incoming phone calls and speaks naturally with callers.

ADDITIONAL INSTRUCTIONS:

- {greeting_rules}
- Include realistic caller pronunciation mistakes and likely STT transcription errors.
- Include Devanagari Hindi and Roman Hindi variations.
- Include common Indian English phrases where appropriate.
- Include short phrases that a caller might say on a real phone call.
- Include natural polite, casual, reordered and shortened forms.
- Include important words individually and in meaningful two-word combinations.
- Include missing matras, similar-sounding letters, joined/split words and spelling mistakes.
- Avoid duplicate variations and meaningless spelling changes.
- Keep every variation suitable for a real-time voice-call FAQ matcher.
- Keep the same intent as the FAQ question; never invent a different answer or topic.

OUTPUT SECTIONS (use these exact headings):
[FULL_HINDI]
[FULL_ENGLISH]
[FULL_HINGLISH]
[KEYWORDS_HINDI]
[KEYWORDS_ENGLISH]
[KEYWORDS_HINGLISH]
[STT_VARIANTS]

FULL QUESTION VARIATIONS:
Generate natural, meaning-preserving alternative ways of asking the same question.
Use different wording and sentence structures, not only transliteration.

KEYWORD VARIATIONS:
Extract only informative 1-3 word keywords and short phrases. Avoid generic words.
Prefer two related keywords; use one keyword only when highly specific.

Generate approximately 8-10 full variations in each full-question section,
5-8 focused keywords in each keyword section, and only useful STT variants.
Put one item per line under each heading. No bullets, numbering, JSON, markdown,
explanations, or answers outside the sections.'''
        payload = {"model": model, "messages": [
            {"role": "system", "content": "You are a precise multilingual FAQ variation and keyword generator. Follow the requested section headings exactly."},
            {"role": "user", "content": prompt}], "temperature": 0.35,
                   "max_tokens": 5000}
        request = urllib.request.Request(
            "https://api.groq.com/openai/v1/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "User-Agent": "LakshVoiceAgent/1.0",
                "Accept": "application/json",
            }, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=35) as response:
                data = json.loads(response.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"]
            section_names = ("FULL_HINDI", "FULL_ENGLISH", "FULL_HINGLISH", "KEYWORDS_HINDI", "KEYWORDS_ENGLISH", "KEYWORDS_HINGLISH", "STT_VARIANTS")
            sections = {name: [] for name in section_names}
            current = None
            for raw_line in str(content).splitlines():
                heading = re.match(r"^\s*\[([A-Z_]+)\]\s*$", raw_line)
                if heading and heading.group(1) in sections:
                    current = heading.group(1)
                    continue
                if current:
                    value = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", raw_line)
                    value = " ".join(value.strip().strip('\\\"').split()).strip(" ?।")
                    if value and value not in sections[current] and value != question:
                        sections[current].append(value)
            full = []
            for name in ("FULL_HINDI", "FULL_ENGLISH", "FULL_HINGLISH", "STT_VARIANTS"):
                full.extend(sections[name])
            keywords = {"hindi": sections["KEYWORDS_HINDI"], "english": sections["KEYWORDS_ENGLISH"], "hinglish": sections["KEYWORDS_HINGLISH"]}
            if full or any(keywords.values()):
                return {"ok": True, "variations": full, "keywords": keywords, "model": model}
            try:
                parsed = json.loads(content)
                values = parsed.get("variations", []) if isinstance(parsed, dict) else parsed
            except json.JSONDecodeError:
                values = re.split(r"\r?\n", content)
            cleaned = []
            for value in values:
                value = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", str(value))
                value = " ".join(value.strip().strip('\\\"').split()).strip(" ?।")
                if value and value not in cleaned and value != question:
                    cleaned.append(value)
            return {"ok": bool(cleaned), "variations": cleaned, "model": model} if cleaned else {"ok": False, "error": "Groq returned no usable variations."}
        except urllib.error.HTTPError as exc:
            return {"ok": False, "error": f"Groq HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')[:400]}"}
        except Exception as exc:
            return {"ok": False, "error": f"Groq request failed: {type(exc).__name__}: {exc}"}

    def save_faqs(self, faqs):
        cleaned = []
        keyword_rows = []
        try:
            for index, item in enumerate(faqs, 1):
                question, answer = str(item.get("question", "")).strip(), str(item.get("answer", "")).strip()
                if not question or not answer:
                    return {"ok": False, "error": f"FAQ {index}: question and answer are required."}
                variations = item.get("variations", [])
                if item.get("_regenerate_variations") or not variations:
                    variations = self.auto_variations(question)
                aliases = list(dict.fromkeys([question] + [str(v).strip() for v in variations if str(v).strip()]))
                cleaned.append((tuple(aliases), answer))
                raw = item.get("keywords", {})
                if not isinstance(raw, dict):
                    raw = {}
                keyword_rows.append(tuple(
                    tuple(dict.fromkeys(str(v).strip() for v in raw.get(language, []) if str(v).strip()))
                    for language in ("hindi", "english", "hinglish")
                ))
            if not cleaned:
                return {"ok": False, "error": "At least one FAQ is required."}
            source = FAQ_FILE.read_text(encoding="utf-8")
            start, end = source.find("HINDI_FAQS = ["), source.find("\n\nUNKNOWN_HINDI =")
            if start < 0 or end < 0:
                return {"ok": False, "error": "Could not locate HINDI_FAQS safely."}
            candidate = source[:start] + "HINDI_FAQS = " + repr(cleaned) + source[end:]
            keyword_literal = "HINDI_FAQ_KEYWORDS = " + repr(keyword_rows)
            keyword_start = candidate.find("HINDI_FAQ_KEYWORDS = [")
            if keyword_start >= 0:
                keyword_end = candidate.find("\n\ndef normalize", keyword_start)
                if keyword_end >= 0:
                    candidate = candidate[:keyword_start] + keyword_literal + candidate[keyword_end:]
            else:
                marker = "\n\nUNKNOWN_HINDI ="
                marker_at = candidate.find(marker)
                candidate = candidate[:marker_at] + "\n\n" + keyword_literal + candidate[marker_at:]
            ast.parse(candidate)
            FAQ_FILE.write_text(candidate, encoding="utf-8", newline="\n")
            importlib.invalidate_caches()
            return {"ok": True, "faqs": self.load_faqs(), "file": str(FAQ_FILE)}
        except Exception as exc:
            return {"ok": False, "error": f"Could not save FAQ: {type(exc).__name__}: {exc}"}

    def get_status(self):
        try:
            status = json.loads(STATUS_FILE.read_text(encoding="utf-8")) if STATUS_FILE.exists() else {}
        except Exception:
            status = {}
        status["bot_running"] = bool(project_bots())
        return status

    def start_bot(self, auto_answer_delay=0):
        running = project_bots()
        if running:
            return {"ok": True, "already_running": True, "message": "Voice Bot is already active and LIVE!"}
        if not BOT_PYTHON.exists():
            return {"ok": False, "error": f"Python environment missing: {BOT_PYTHON}"}
        try:
            delay = max(0.0, min(120.0, float(auto_answer_delay)))
            self.save_pickup_delay(delay)  # the bot re-reads this file at every ring
            subprocess.Popen([str(BOT_PYTHON), "-u", str(BOT_FILE), "--fast-start", "--auto-answer-delay", str(delay)], cwd=str(ROOT), creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
            return {"ok": True, "already_running": False, "message": "Bot is starting."}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def get_greeting(self):
        """Greeting spoken when a call starts ("" = none)."""
        try:
            path = ROOT / "greeting.json"
            if path.exists():
                text = str(json.loads(path.read_text(encoding="utf-8")).get("text", "")).strip()
            else:
                text = "बेस्टज़ोन में आपका स्वागत है।"  # the bot's built-in welcome until one is saved
            return {"ok": True, "text": text}
        except Exception as exc:
            return {"ok": False, "error": f"Could not read greeting: {exc}"}

    def save_greeting(self, text):
        """Save the call greeting; the bot reads it at the start of every call."""
        try:
            text = str(text or "").strip()
            path = ROOT / "greeting.json"
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"text": text}, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
            return {"ok": True, "text": text}
        except Exception as exc:
            return {"ok": False, "error": f"Could not save greeting: {exc}"}

    def get_voice_settings(self):
        """Voice settings: gender, speed, pitch."""
        try:
            path = ROOT / "voice_settings.json"
            if path.exists():
                return {"ok": True, **json.loads(path.read_text(encoding="utf-8"))}
            return {"ok": True, "gender": "male", "speed": "+2%", "pitch": "+0Hz"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def save_voice_settings(self, gender="male", speed="+0%", pitch="+0Hz", mithi_voice=None):
        """Save voice settings; used for Mithi local neural TTS and fallback."""
        try:
            gender = "female" if str(gender).lower() in ("female", "woman", "malini", "swara", "dhwani") else "male"
            mithi_voice = str(mithi_voice).lower() if mithi_voice else ("malini" if gender == "female" else "anirban")
            if mithi_voice not in ("malini", "anirban"):
                mithi_voice = "anirban" if gender == "male" else "malini"
            data = {"gender": gender, "mithi_voice": mithi_voice, "speed": speed, "pitch": pitch}
            path = ROOT / "voice_settings.json"
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            os.replace(tmp, path)
            return {"ok": True, **data}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def get_pickup_delay(self):
        """Seconds the bot waits before picking up a call (dashboard setting)."""
        try:
            path = ROOT / "pickup_delay.json"
            seconds = float(json.loads(path.read_text(encoding="utf-8"))["seconds"]) if path.exists() else 5.0
            return {"ok": True, "seconds": seconds}
        except Exception as exc:
            return {"ok": False, "error": f"Could not read pickup delay: {exc}"}

    def save_pickup_delay(self, seconds):
        """Save the pickup delay; the running bot uses it from the next ring."""
        try:
            seconds = max(0.0, min(120.0, float(seconds)))
            path = ROOT / "pickup_delay.json"
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"seconds": seconds}), encoding="utf-8")
            os.replace(tmp, path)
            return {"ok": True, "seconds": seconds}
        except Exception as exc:
            return {"ok": False, "error": f"Could not save pickup delay: {exc}"}

    def get_business_hours(self):
        """Business days/hours and their own pickup delay (dashboard)."""
        days = {key: {"open": False, "hours": []} for key in ("sun", "mon", "tue", "wed", "thu", "fri", "sat")}
        try:
            path = ROOT / "business_hours.json"
            data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            for key, day in (data.get("days") or {}).items():
                if key in days:
                    days[key] = {"open": bool(day.get("open")), "hours": list(day.get("hours") or [])}
            return {"ok": True, "seconds": float(data.get("seconds", 5.0)), "days": days}
        except Exception as exc:
            return {"ok": False, "error": f"Could not read business hours: {exc}"}

    def save_business_hours(self, seconds, days):
        """Save business hours; the running bot uses them from the next ring."""
        try:
            seconds = max(0.0, min(120.0, float(seconds)))
            clean = {}
            for key in ("sun", "mon", "tue", "wed", "thu", "fri", "sat"):
                day = (days or {}).get(key) or {}
                hours = []
                for pair in day.get("hours") or []:
                    start, end = (str(pair[0]).strip(), str(pair[1]).strip()) if len(pair) == 2 else ("", "")
                    if not start and not end:
                        continue  # empty row
                    if not re.fullmatch(r"\d{2}:\d{2}", start) or not re.fullmatch(r"\d{2}:\d{2}", end):
                        return {"ok": False, "error": f"{key.title()}: opens at / closes at both are needed."}
                    if start == end:
                        return {"ok": False, "error": f"{key.title()}: opens and closes at the same time."}
                    hours.append([start, end])
                clean[key] = {"open": bool(day.get("open")), "hours": hours}
            path = ROOT / "business_hours.json"
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"seconds": seconds, "days": clean}, indent=1), encoding="utf-8")
            os.replace(tmp, path)
            return {"ok": True, "seconds": seconds, "days": clean}
        except Exception as exc:
            return {"ok": False, "error": f"Could not save business hours: {exc}"}

    def open_live_chat(self):
        """Open the live call chat window (read-only viewer of live_chat.json)."""
        try:
            import psutil
            for p in psutil.process_iter(["pid", "cmdline"]):
                try:
                    cmdline = p.info.get("cmdline") or []
                    if any("live_chat_viewer.py" in str(x).lower() for x in cmdline):
                        p.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

            viewer_script = ROOT / "live_chat_viewer.py"

            python_exe = BOT_PYTHON
            subprocess.Popen(
                [str(python_exe), str(viewer_script)],
                cwd=str(viewer_script.parent),
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
            return {"ok": True, "message": "Live Chat window opened"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def stop_bot(self):
        stopped = 0
        for process in project_bots():
            try:
                process.terminate(); stopped += 1
            except Exception:
                pass
        return {"ok": True, "message": f"Stopped {stopped} bot process(es)."}


if __name__ == "__main__":
    if "--server" in sys.argv:
        port = int(sys.argv[sys.argv.index("--server") + 1]) if len(sys.argv) > sys.argv.index("--server") + 1 else 8765
        api = DashboardApi()
        class Handler(BaseHTTPRequestHandler):
            def _set_cors(self):
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "*")

            def do_OPTIONS(self):
                self.send_response(200)
                self._set_cors()
                self.end_headers()

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
                        result = getattr(api, name)()
                        self._json(result)
                    except Exception as exc:
                        self._json({"ok": False, "error": str(exc)}, 500)
                else:
                    self._json({"error": "not found"}, 404)

            def do_POST(self):
                try:
                    name = self.path.removeprefix("/api/").split("?")[0]
                    content_length = int(self.headers.get("Content-Length", "0"))
                    raw = self.rfile.read(content_length) if content_length > 0 else b"[]"
                    args = json.loads(raw) if raw.strip() else []
                    if isinstance(args, dict):
                        result = getattr(api, name)(**args)
                    elif isinstance(args, list):
                        result = getattr(api, name)(*args)
                    else:
                        result = getattr(api, name)(args)
                    self._json(result)
                except Exception as exc:
                    self._json({"ok": False, "error": str(exc)}, 500)

            def log_message(self, *_): pass
        class ResilientHTTPServer(ThreadingHTTPServer):
            allow_reuse_address = True
            daemon_threads = True

            def handle_error(self, request, client_address):
                pass

        try:
            httpd = ResilientHTTPServer(("0.0.0.0", port), Handler)
            httpd.serve_forever()
        except OSError as exc:
            if getattr(exc, "winerror", None) == 10048 or "10048" in str(exc):
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/get_status", timeout=1.5) as resp:
                        if resp.status == 200:
                            while True:
                                time.sleep(3600)
                except Exception:
                    pass
            raise
        raise SystemExit
    # The embedded HTTP server is used by Frontbit Link and does not require
    # pywebview. Import it only for the standalone desktop-window mode.
    try:
        import pythonnet
        if pythonnet.get_runtime_info() is None:
            _cfg = Path(__file__).resolve().parent / "runtimeconfig.json"
            pythonnet.load('coreclr', runtime_config=str(_cfg))
    except Exception:
        pass
    import webview
    webview.create_window("LakshVoiceAgent | Hindi FAQ Voice Bot", str(HTML_FILE), js_api=DashboardApi(), width=1480, height=930, min_size=(1100, 700), resizable=True, text_select=True)
    webview.start(gui="edgechromium", debug=False)
