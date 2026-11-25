"""Stream caller audio from the phone to a realtime STT API.

This bypasses the Android APK recognizer. The phone supplies raw caller audio,
the PC streams small PCM chunks to the API, and transcripts are saved locally.
"""
import argparse
import base64
import datetime as dt
import json
import os
import queue
import signal
import sys
import threading
import time
from pathlib import Path

import numpy as np
import websocket

from audio_bridge import ROOT, PhoneAudio, devices

TRANSCRIPTS = ROOT / "api_transcripts"
TRANSCRIPTS.mkdir(exist_ok=True)
STATUS = ROOT / "api_status.json"


def choose_serial(preferred=None):
    connected = devices()
    if preferred and preferred in connected:
        return preferred
    if not connected:
        raise RuntimeError("No ADB phone connected")
    hardware = os.environ.get("CALLTEXT_PHONE_SERIAL", "K7NVQWYTWCFIPN85")
    for item in connected:
        if hardware in item:
            return item
    return connected[0]


def stereo48_to_mono24_pcm(raw):
    samples = np.frombuffer(raw, dtype="<i2").reshape(-1, 2).astype(np.int32)
    mono = ((samples[:, 0] + samples[:, 1]) // 2).astype(np.int16)
    mono = mono[: (len(mono) // 2) * 2].reshape(-1, 2).mean(axis=1).astype("<i2")
    return mono.tobytes()


class Status:
    def __init__(self):
        self.lock = threading.Lock()
        self.data = {
            "state": "starting",
            "captured_chunks": 0,
            "sent_chunks": 0,
            "partial": "",
            "final": "",
            "updated": dt.datetime.now().isoformat(),
        }

    def update(self, **kwargs):
        with self.lock:
            self.data.update(kwargs)
            self.data["updated"] = dt.datetime.now().isoformat()
            tmp = STATUS.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(STATUS)


class RealtimeTranscriber:
    def __init__(self, args, status):
        self.args = args
        self.status = status
        self.done = threading.Event()
        self.ready = threading.Event()
        self.audio = queue.Queue(maxsize=200)
        self.ws = None
        stamp = dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")
        self.path = TRANSCRIPTS / f"api_call_{stamp}.txt"
        self.path.write_text(
            f"Realtime API call transcript | {dt.datetime.now():%Y-%m-%d %H:%M:%S}\n"
            f"Model: {args.transcribe_model} | Language: {args.language}\n\n",
            encoding="utf-8",
        )
        self.status.update(file=str(self.path))

    def append_file(self, value):
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())

    def connect(self):
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("OPENAI_API_KEY is not set")
        url = f"{self.args.url}?model={self.args.realtime_model}"
        headers = [
            f"Authorization: Bearer {key}",
            "OpenAI-Beta: realtime=v1",
        ]
        self.ws = websocket.WebSocketApp(
            url,
            header=headers,
            on_open=self.on_open,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close,
        )
        threading.Thread(target=self.ws.run_forever, kwargs={"ping_interval": 20, "ping_timeout": 10}, daemon=True).start()
        if not self.ready.wait(15):
            raise RuntimeError("Realtime API did not become ready")

    def on_open(self, ws):
        session = {
            "type": "session.update",
            "session": {
                "type": "realtime",
                "audio": {
                    "input": {
                        "format": {"type": "audio/pcm", "rate": 24000},
                        "noise_reduction": {"type": "far_field"},
                        "transcription": {
                            "model": self.args.transcribe_model,
                            "language": self.args.language,
                            "prompt": "Phone call audio. Transcribe fast short caller phrases accurately.",
                        },
                        "turn_detection": {
                            "type": "server_vad",
                            "threshold": self.args.vad_threshold,
                            "prefix_padding_ms": 120,
                            "silence_duration_ms": self.args.silence_ms,
                        },
                    }
                },
                "instructions": "Transcribe caller speech. Do not generate a spoken response.",
                "modalities": ["text"],
            },
        }
        ws.send(json.dumps(session))
        self.status.update(state="api_connected")
        self.ready.set()

    def on_message(self, ws, message):
        try:
            event = json.loads(message)
        except json.JSONDecodeError:
            return
        kind = event.get("type", "")
        if kind == "conversation.item.input_audio_transcription.delta":
            delta = event.get("delta", "")
            if delta:
                self.status.update(partial=self.status.data.get("partial", "") + delta)
                print(delta, end="", flush=True)
        elif kind == "conversation.item.input_audio_transcription.completed":
            text = event.get("transcript", "").strip()
            if text:
                line = f"[{dt.datetime.now():%H:%M:%S}] {text}\n"
                self.append_file(line)
                self.status.update(final=line, partial="")
                print("\n" + line, end="", flush=True)
        elif kind == "error":
            self.status.update(state="api_error", error=event)
            print("\nAPI error:", json.dumps(event, ensure_ascii=False), flush=True)

    def on_error(self, ws, error):
        self.status.update(state="api_error", error=str(error))

    def on_close(self, ws, code, reason):
        self.status.update(state="api_closed", close_code=code, close_reason=reason)
        self.done.set()

    def sender(self):
        sent = 0
        while not self.done.is_set():
            try:
                chunk = self.audio.get(timeout=0.5)
            except queue.Empty:
                continue
            if chunk is None:
                break
            payload = {
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(chunk).decode("ascii"),
            }
            self.ws.send(json.dumps(payload))
            sent += 1
            self.status.update(sent_chunks=sent)
        try:
            self.ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
        except Exception:
            pass


def run_api(args):
    status = Status()
    transcriber = RealtimeTranscriber(args, status)
    transcriber.connect()
    sender = threading.Thread(target=transcriber.sender, daemon=True)
    sender.start()
    serial = choose_serial(args.serial)
    status.update(state="capturing", serial=serial)
    capture = PhoneAudio(serial, args.source).start()
    stop = threading.Event()

    def handle_signal(signum, frame):
        stop.set()

    signal.signal(signal.SIGINT, handle_signal)
    captured = 0
    try:
        while not stop.is_set():
            raw = capture.read(4800)
            pcm24 = stereo48_to_mono24_pcm(raw)
            try:
                transcriber.audio.put(pcm24, timeout=1)
            except queue.Full:
                status.update(state="audio_queue_full")
                raise RuntimeError("API sender cannot keep up")
            captured += 1
            status.update(captured_chunks=captured)
    finally:
        capture.close()
        transcriber.audio.put(None)
        status.update(state="stopping")
        time.sleep(1.0)
        if transcriber.ws:
            transcriber.ws.close()


def run_dry(args):
    status = Status()
    serial = choose_serial(args.serial)
    capture = PhoneAudio(serial, args.source).start()
    deadline = time.monotonic() + args.dry_seconds
    chunks = 0
    audio_bytes = 0
    began = time.monotonic()
    try:
        while time.monotonic() < deadline:
            raw = capture.read(4800)
            pcm24 = stereo48_to_mono24_pcm(raw)
            chunks += 1
            audio_bytes += len(pcm24)
            status.update(state="dry_capture", serial=serial, captured_chunks=chunks, audio_bytes=audio_bytes)
    finally:
        capture.close()
    elapsed = time.monotonic() - began
    print(f"dry_capture_ok chunks={chunks} seconds={elapsed:.2f} pcm24_bytes={audio_bytes}")


def main():
    parser = argparse.ArgumentParser(description="Stream phone caller audio to realtime STT API")
    parser.add_argument("--serial")
    parser.add_argument("--source", default="voice-call-downlink", choices=["voice-call-downlink", "voice-call"])
    parser.add_argument("--url", default="wss://api.openai.com/v1/realtime")
    parser.add_argument("--realtime-model", default="gpt-realtime-mini")
    parser.add_argument("--transcribe-model", default="gpt-4o-mini-transcribe")
    parser.add_argument("--language", default="en")
    parser.add_argument("--silence-ms", type=int, default=250)
    parser.add_argument("--vad-threshold", type=float, default=0.35)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--dry-seconds", type=float, default=5.0)
    args = parser.parse_args()
    if args.dry_run:
        run_dry(args)
    else:
        run_api(args)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        Status().update(state="error", error=str(exc))
        print("ERROR:", exc, file=sys.stderr)
        sys.exit(1)
