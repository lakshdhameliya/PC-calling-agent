"""Live chat screen: what the caller says (as the bot hears it) and what the bot answers.

Read-only viewer of live_chat.json, written by the Frontbit Link bot.
It does not touch the call, audio devices or the bot.
"""
import json
from pathlib import Path

try:
    import pythonnet
    if pythonnet.get_runtime_info() is None:
        _cfg = Path(__file__).resolve().parent / "runtimeconfig.json"
        if _cfg.exists():
            pythonnet.load('coreclr', runtime_config=str(_cfg))
except Exception:
    pass

import webview

ROOT = Path(__file__).resolve().parent
CHAT_FILE = ROOT / "live_chat.json"
STATUS_FILE = ROOT / "status.json"
HTML_FILE = ROOT / "live_chat_viewer.html"


def _read_json(path):
    if path.exists():
        for _ in range(3):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return data
            except Exception:
                time.sleep(0.02)
    return {}


class ChatApi:
    def get_chat(self, after=0):
        data = _read_json(CHAT_FILE) or {}
        events = [e for e in data.get("events", []) if e.get("seq", 0) > int(after or 0)]
        status = _read_json(STATUS_FILE) or {}
        return {
            "events": events,
            "last_seq": max([e.get("seq", 0) for e in data.get("events", [])] or [0]),
            "status": {
                "ready": status.get("ready"),
                "call_active": status.get("call_active"),
                "state": status.get("state"),
                "partial_text": status.get("partial_text"),
            },
        }


if __name__ == "__main__":
    webview.create_window("Live Call Chat | BestZone Voice Bot", str(HTML_FILE), js_api=ChatApi(),
                          width=760, height=900, min_size=(420, 500), resizable=True, text_select=True)
    webview.start(gui="edgechromium", debug=False)
