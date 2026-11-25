"""Local call transcription with a live text window and durable UTF-8 files."""
import collections
import datetime as dt
import json
import os
import queue
import re
import subprocess
import threading
import time
import tkinter as tk
from tkinter import ttk, scrolledtext
from pathlib import Path

import numpy as np
import pyaudiowpatch as pa
from faster_whisper import WhisperModel
from audio_bridge import ROOT, PhoneAudio, adb, devices

os.environ.setdefault('HF_HOME', str(ROOT / 'models' / 'cache'))
TRANSCRIPTS = ROOT / 'transcripts'
TRANSCRIPTS.mkdir(exist_ok=True)


class Transcript:
    def __init__(self, source, language):
        now = dt.datetime.now()
        self.path = TRANSCRIPTS / (now.strftime('call_%Y-%m-%d_%H-%M-%S_%f') + '.txt')
        self.append(f'Call transcript | {now:%Y-%m-%d %H:%M:%S}\n'
                    f'Source: {source} | Language: {language}\n'
                    'Automatic transcription; recognition errors are possible.\n\n')

    def append(self, text):
        with self.path.open('a', encoding='utf-8') as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())


class Segmenter:
    """Keep pre-roll, split at pauses, bound long speech without dropping samples."""
    def __init__(self, emit, threshold=.0015):
        self.emit, self.threshold = emit, threshold
        self.pre = collections.deque(maxlen=3)
        self.parts, self.silence, self.start = [], 0, None

    def feed(self, samples):
        rms = float(np.sqrt(np.mean(samples ** 2)))
        if rms >= self.threshold:
            if not self.parts:
                self.start = dt.datetime.now() - dt.timedelta(seconds=len(self.pre) * .1)
                self.parts.extend(self.pre)
                self.pre.clear()
            self.parts.append(samples)
            self.silence = 0
        elif self.parts:
            self.parts.append(samples)
            self.silence += 1
        else:
            self.pre.append(samples)
        if self.parts and (self.silence >= 7 or len(self.parts) >= 65):
            self.flush()
        return rms

    def flush(self):
        if self.parts:
            audio = np.concatenate(self.parts)
            if len(audio) >= 4800:
                self.emit(audio, self.start)
        self.parts, self.silence, self.start = [], 0, None


class WindowsAudio:
    def __init__(self, index):
        self.index = index
        self.pa = self.stream = None

    def start(self):
        self.pa = pa.PyAudio()
        d = self.pa.get_device_info_by_index(self.index)
        self.rate = int(d['defaultSampleRate'])
        self.channels = min(2, int(d['maxInputChannels']))
        try:
            self.stream = self.pa.open(format=pa.paInt16, channels=self.channels,
                                       rate=self.rate, input=True,
                                       input_device_index=self.index,
                                       frames_per_buffer=self.rate // 10)
        except Exception:
            self.close()
            raise
        return self

    def read(self, frames):
        return self.stream.read(frames, exception_on_overflow=True)

    def close(self):
        if self.stream:
            self.stream.close()
        if self.pa:
            self.pa.terminate()


class App:
    def __init__(self, root):
        self.root = root
        self.events, self.jobs = queue.Queue(), queue.Queue(maxsize=128)
        self.model = None
        self.stop_event = threading.Event()
        self.shutdown = threading.Event()
        self.capturing = False
        self.finishing = False
        self.current = None
        self.serial = None
        self.suppressed = False
        self.sources = {'Phone: caller only': 'voice-call-downlink',
                        'Phone: both sides': 'voice-call',
                        'PC: default speaker audio': 'loopback'}
        self.status_data = {'state': 'Loading model', 'rms': 0, 'call_state': 'unknown'}
        root.title('Call Live Text — automatic TXT saving')
        root.geometry('1000x690')
        root.configure(bg='#f4f6fa')
        main = ttk.Frame(root, padding=18)
        main.pack(fill='both', expand=True)
        ttk.Label(main, text='Call Live Text', font=('Segoe UI', 23, 'bold')).pack(anchor='w')
        ttk.Label(main, text='Local speech recognition • Text is saved automatically after every sentence').pack(anchor='w', pady=(2, 14))
        row = ttk.Frame(main)
        row.pack(fill='x')
        self.source = tk.StringVar(value='Phone: caller only')
        self.source_box = ttk.Combobox(row, textvariable=self.source, values=list(self.sources), state='readonly', width=33)
        self.source_box.pack(side='left')
        self.lang = tk.StringVar(value='English')
        self.lang_box = ttk.Combobox(row, textvariable=self.lang, values=['English', 'Gujarati', 'Hindi', 'Auto'], state='readonly', width=12)
        self.lang_box.pack(side='left', padx=8)
        self.auto = tk.BooleanVar(value=True)
        ttk.Checkbutton(row, text='Auto start on answered calls', variable=self.auto).pack(side='left', padx=5)
        row2 = ttk.Frame(main)
        row2.pack(fill='x', pady=12)
        self.start_button = ttk.Button(row2, text='Start listening', command=self.start, state='disabled')
        self.start_button.pack(side='left')
        self.stop_button = ttk.Button(row2, text='Stop & save', command=self.stop, state='disabled')
        self.stop_button.pack(side='left', padx=8)
        ttk.Button(row2, text='Open TXT in Notepad', command=self.open_notepad).pack(side='left')
        ttk.Button(row2, text='Open saved folder', command=lambda: os.startfile(TRANSCRIPTS)).pack(side='left', padx=8)
        self.status = tk.StringVar(value='Loading local model…')
        ttk.Label(main, textvariable=self.status, wraplength=940).pack(anchor='w', pady=4)
        self.phone_status = tk.StringVar(value='Checking phone connection…')
        ttk.Label(main, textvariable=self.phone_status).pack(anchor='w')
        meter = ttk.Frame(main)
        meter.pack(fill='x', pady=8)
        ttk.Label(meter, text='Audio level').pack(side='left')
        self.level = ttk.Progressbar(meter, maximum=100)
        self.level.pack(side='left', fill='x', expand=True, padx=10)
        self.file_label = tk.StringVar(value='A new TXT file is created for each listening session / call.')
        ttk.Label(main, textvariable=self.file_label, wraplength=940).pack(anchor='w', pady=4)
        self.text = scrolledtext.ScrolledText(main, font=('Nirmala UI', 14), wrap='word', state='disabled')
        self.text.pack(fill='both', expand=True, pady=8)
        ttk.Label(main, text='Answer the phone normally. Text appears after a short pause. Stop finishes pending recognition and saves it.').pack(anchor='w')
        root.protocol('WM_DELETE_WINDOW', self.close)
        threading.Thread(target=self.load_model, daemon=True).start()
        threading.Thread(target=self.monitor_phone, daemon=True).start()
        threading.Thread(target=self.recognize, daemon=True).start()
        root.after(100, self.poll)

    def emit(self, kind, value=None):
        self.events.put((kind, value))

    def load_model(self):
        try:
            self.model = WhisperModel('small', device='cpu', compute_type='int8',
                                     cpu_threads=4, download_root=str(ROOT / 'models'),
                                     local_files_only=True)
            self.emit('ready')
        except Exception as e:
            self.emit('error', 'Model could not load: ' + str(e))

    def monitor_phone(self):
        while not self.shutdown.is_set():
            try:
                connected = devices()
                if len(connected) != 1:
                    self.emit('phone', (None, None, 'Connect exactly one phone via ADB.'))
                else:
                    serial = connected[0]
                    result = adb('shell', 'dumpsys', 'telephony.registry', serial=serial)
                    states = [int(x) for x in re.findall(r'\bmCallState\s*=\s*(\d+)', result)]
                    state = max(states) if states else None
                    label = {0: 'Idle', 1: 'Incoming call', 2: 'Call answered'}.get(state, 'Call state unavailable')
                    self.emit('phone', (serial, state, label))
            except Exception as e:
                self.emit('phone', (None, None, 'Phone disconnected: ' + str(e)[:130]))
            self.shutdown.wait(1.2)

    def start(self):
        if self.capturing or self.finishing or self.model is None:
            return
        source = self.sources[self.source.get()]
        if source.startswith('voice-') and not self.serial:
            self.set_status('No connected phone. Reconnect wireless debugging first.')
            return
        lang = {'English': 'en', 'Gujarati': 'gu', 'Hindi': 'hi', 'Auto': None}[self.lang.get()]
        try:
            transcript = Transcript(self.source.get(), self.lang.get())
        except Exception as e:
            self.set_status('Cannot create transcript: ' + str(e))
            return
        self.current = transcript
        self.stop_event.clear()
        self.capturing = True
        self.suppressed = False
        self.started = time.monotonic()
        self.heard_audio = False
        self.start_button.configure(state='disabled')
        self.stop_button.configure(state='normal')
        self.source_box.configure(state='disabled')
        self.lang_box.configure(state='disabled')
        self.text.configure(state='normal')
        self.text.delete('1.0', 'end')
        self.text.configure(state='disabled')
        self.file_label.set(str(transcript.path))
        self.status_data['file'] = str(transcript.path)
        self.set_status('Connecting audio…')
        threading.Thread(target=self.capture, args=(source, self.serial, transcript, lang), daemon=True).start()

    def capture(self, source, serial, transcript, language):
        capture = None
        def send(audio, timestamp):
            self.jobs.put_nowait(('audio', transcript, language, audio, timestamp))
        segmenter = Segmenter(send)
        try:
            if source.startswith('voice-'):
                capture = PhoneAudio(serial, source).start()
            else:
                with pa.PyAudio() as engine:
                    index = int(engine.get_default_wasapi_loopback()['index'])
                capture = WindowsAudio(index).start()
            self.emit('listening')
            while not self.stop_event.is_set():
                raw = capture.read(capture.rate // 10)
                audio = np.frombuffer(raw, '<i2').astype(np.float32).reshape(-1, capture.channels).mean(axis=1) / 32768
                # 0.1-second blocks: low-pass resampling via libswresample would be ideal;
                # mean each group of 3 samples for the phone's fixed 48 kHz stream.
                if capture.rate == 48000:
                    audio = audio.reshape(-1, 3).mean(axis=1)
                elif capture.rate != 16000:
                    audio = np.interp(np.arange(1600) * capture.rate / 16000,
                                      np.arange(len(audio)), audio).astype(np.float32)
                rms = segmenter.feed(audio)
                self.emit('level', rms)
            segmenter.flush()
        except Exception as e:
            self.emit('error', 'Audio stopped: ' + str(e))
            try:
                segmenter.flush()
            except Exception:
                self.emit('error', 'Recognition backlog exceeded capacity. Some speech was not processed.')
        finally:
            if capture:
                capture.close()
            self.emit('capture_done')
            self.jobs.put(('end', transcript, None, None, None))

    def recognize(self):
        while not self.shutdown.is_set():
            try:
                kind, transcript, language, audio, timestamp = self.jobs.get(timeout=.5)
            except queue.Empty:
                continue
            try:
                if kind == 'end':
                    transcript.append(f'\n[Ended {dt.datetime.now():%H:%M:%S}]\n')
                    self.emit('saved', transcript.path)
                    continue
                began = time.monotonic()
                segments, _ = self.model.transcribe(audio, language=language, beam_size=1,
                    vad_filter=True, vad_parameters={'min_silence_duration_ms': 350},
                    condition_on_previous_text=False, temperature=0,
                    no_speech_threshold=.65)
                text = ' '.join(s.text.strip() for s in segments if s.no_speech_prob < .8).strip()
                if text:
                    line = f'[{timestamp:%H:%M:%S}] {text}\n'
                    transcript.append(line)
                    self.emit('text', (transcript.path, line, round(time.monotonic() - began, 2)))
            except Exception as e:
                self.emit('error', 'Recognition/save error: ' + str(e))
            finally:
                self.jobs.task_done()

    def stop(self):
        if self.capturing:
            self.suppressed = True
            self.stop_event.set()
            self.finishing = True
            self.stop_button.configure(state='disabled')
            self.set_status('Finishing recognition and saving…')

    def close(self):
        self.stop()
        if self.capturing or self.finishing or self.jobs.unfinished_tasks:
            self.root.after(250, self.close)
            return
        self.shutdown.set()
        self.root.destroy()

    def open_notepad(self):
        if self.current:
            subprocess.Popen(['notepad.exe', str(self.current.path)])

    def set_status(self, message):
        self.status.set(message)
        self.status_data['state'] = message

    def poll(self):
        for _ in range(200):
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == 'ready':
                self.start_button.configure(state='normal')
                self.set_status('Ready. Waiting for an answered call, or press Start listening.')
            elif kind == 'phone':
                self.serial, call_state, label = value
                self.phone_status.set(label + (f' — {self.serial}' if self.serial else ''))
                self.status_data['call_state'] = call_state
                if call_state == 0:
                    self.suppressed = False
                    if self.capturing and getattr(self, 'auto_session', False):
                        self.stop()
                        self.suppressed = False
                if call_state == 2 and self.auto.get() and not self.suppressed and not self.capturing and not self.finishing and self.model:
                    self.auto_session = True
                    self.start()
                elif not self.capturing and not self.finishing:
                    self.auto_session = False
            elif kind == 'listening':
                self.set_status('Listening. Speak on the other phone, then pause briefly.')
            elif kind == 'level':
                self.status_data['rms'] = round(value, 6)
                self.level['value'] = min(100, value * 1500)
                if value > .0015:
                    self.heard_audio = True
                if not self.heard_audio and time.monotonic() - self.started > 15:
                    self.set_status('Audio stream is silent. The phone may block call audio; test PC speaker audio via Phone Link.')
            elif kind == 'text':
                path, line, elapsed = value
                if self.current and path == self.current.path:
                    self.text.configure(state='normal')
                    self.text.insert('end', line)
                    self.text.see('end')
                    self.text.configure(state='disabled')
                self.status_data['last_text'] = line
                self.status_data['recognition_seconds'] = elapsed
                if self.capturing and not self.finishing:
                    self.set_status('Listening • last sentence saved')
            elif kind == 'capture_done':
                self.capturing = False
                self.finishing = True
                self.stop_button.configure(state='disabled')
            elif kind == 'saved':
                self.finishing = False
                self.start_button.configure(state='normal')
                self.source_box.configure(state='readonly')
                self.lang_box.configure(state='readonly')
                self.level['value'] = 0
                self.set_status('Saved. Ready for the next call.')
            elif kind == 'error':
                self.set_status(value)
                self.status_data['last_error'] = value
                self.suppressed = True
        self.status_data.update(capturing=self.capturing, finishing=self.finishing,
                                pending=self.jobs.qsize(), updated=dt.datetime.now().isoformat())
        try:
            temp = ROOT / 'status.tmp'
            temp.write_text(json.dumps(self.status_data, ensure_ascii=False, indent=2), encoding='utf-8')
            temp.replace(ROOT / 'status.json')
        except OSError:
            pass
        self.root.after(150, self.poll)


if __name__ == '__main__':
    window = tk.Tk()
    App(window)
    window.mainloop()
