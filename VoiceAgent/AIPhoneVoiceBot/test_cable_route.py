"""Verify bot voice is routed into VB-CABLE, not the laptop speaker path."""
import argparse
import math
import queue
import sys
import time

import numpy as np
import pyaudiowpatch as pa

from cable_voice import CableVoice


def find_device(kind, needle):
    engine = pa.PyAudio()
    try:
        fallback = None
        for index in range(engine.get_device_count()):
            info = engine.get_device_info_by_index(index)
            name = info.get("name", "")
            if needle.lower() not in name.lower():
                continue
            if "loopback" in name.lower():
                continue
            if kind == "input" and info.get("maxInputChannels", 0) > 0:
                if fallback is None:
                    fallback = (index, name)
                if info.get("hostApi") == 2:
                    return index, name
            if kind == "output" and info.get("maxOutputChannels", 0) > 0:
                if fallback is None:
                    fallback = (index, name)
                if info.get("hostApi") == 2:
                    return index, name
        if fallback:
            return fallback
    finally:
        engine.terminate()
    raise RuntimeError(f"{kind} device containing {needle!r} not found")


def record_input(index, seconds=4.0, rate=48000):
    engine = pa.PyAudio()
    frames = []
    try:
        stream = engine.open(
            format=pa.paInt16,
            channels=2,
            rate=rate,
            input=True,
            input_device_index=index,
            frames_per_buffer=1024,
        )
        try:
            deadline = time.perf_counter() + seconds
            while time.perf_counter() < deadline:
                frames.append(stream.read(1024, exception_on_overflow=False))
        finally:
            stream.stop_stream()
            stream.close()
    finally:
        engine.terminate()
    raw = b"".join(frames)
    if not raw:
        return {"rms": 0.0, "peak": 0.0, "clip_percent": 0.0}
    audio = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
    clip_percent = float(np.mean(np.abs(audio) >= 0.98) * 100.0) if len(audio) else 0.0
    return {
        "rms": float(math.sqrt(float(np.mean(audio * audio)))),
        "peak": peak,
        "clip_percent": clip_percent,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="CABLE Input")
    parser.add_argument("--input", default="CABLE Output")
    parser.add_argument("--text", default="Cable route test. The caller should hear this voice.")
    parser.add_argument("--repeats", type=int, default=1)
    args = parser.parse_args()
    input_index, input_name = find_device("input", args.input)
    out_index, out_name = find_device("output", args.output)
    events = queue.Queue()
    voice = CableVoice(lambda event: events.put(event), args.output)
    time.sleep(1.0)
    for number in range(args.repeats):
        voice.send("speak", text=f"{args.text} Reply {number + 1}.")
    stats = record_input(input_index, seconds=max(8.0, args.repeats * 6.0))
    time.sleep(0.5)
    voice.close()
    received = []
    while True:
        try:
            received.append(events.get_nowait())
        except queue.Empty:
            break
    played = sum(event.get("type") == "played" for event in received)
    print(f"output_device={out_index}:{out_name}")
    print(f"input_device={input_index}:{input_name}")
    print(f"cable_output_rms={stats['rms']:.6f}")
    print(f"cable_output_peak={stats['peak']:.6f}")
    print(f"cable_output_clip_percent={stats['clip_percent']:.6f}")
    print(f"played_events={played}/{args.repeats}")
    print("event_types=" + ",".join(str(event.get("type")) for event in received))
    if stats["rms"] <= 0.002:
        print("FAIL: no voice detected on CABLE Output")
    elif stats["peak"] >= 0.95 or stats["clip_percent"] > 0.0:
        print("FAIL: clipping risk on CABLE Output")
    elif played != args.repeats:
        print("FAIL: one or more repeated replies did not play")
    else:
        print("PASS")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("ERROR:", exc, file=sys.stderr)
        sys.exit(1)
