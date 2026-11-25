"""Offline timing comparison using identical saved caller WAVs; no audio routing."""
import argparse
import json
import time
import wave
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from phone_link_no_adb_bot import HINDI_TURBO_MODEL, FrontbitLinkNoAdbBot, WhisperModel


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--compute', default='int8_float16')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    session = root / 'call_backlog' / 'no_adb_2026-10-14_12-01-07_487984'
    ids = {6, 7, 9, 25, 26, 30}
    paths = sorted(p for p in session.rglob('*.wav') if int(p.name.split('_')[1]) in ids)
    if not paths:
        raise RuntimeError('Saved benchmark call WAVs missing')
    started = time.perf_counter()
    model = WhisperModel(str(HINDI_TURBO_MODEL), device='cuda', compute_type=args.compute, local_files_only=True)
    load = time.perf_counter()-started
    bot = SimpleNamespace(hindi_stt=model)
    rows = []
    for repeat in range(2):
        for path in paths:
            with wave.open(str(path)) as w:
                assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 16000)
                audio = np.frombuffer(w.readframes(w.getnframes()), dtype='<i2').astype(np.float32)/32768
            text, seconds = FrontbitLinkNoAdbBot.transcribe_hindi(bot, audio)
            row = dict(wav=str(path), repeat=repeat, text=text, seconds=seconds, diagnostics=bot.last_hindi_diagnostics)
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
            (root / ('speed_profile_' + args.compute + '.json')).write_text(json.dumps(dict(compute=args.compute, load_seconds=load, rows=rows), ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
