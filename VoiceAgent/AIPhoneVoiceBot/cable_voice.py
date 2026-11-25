"""Windows SAPI voice routed to a selected PyAudio output device."""
import asyncio
import hashlib
import json
import os
import queue
import subprocess
import threading
import time
import wave
from pathlib import Path

import numpy as np
import pyaudiowpatch as pa


class CableVoice:
    def __init__(self, on_event, device_name="CABLE Input", gain=0.50):
        self.on_event = on_event
        self.device_name = device_name
        self.gain = gain
        self.process = None
        self.closed = False
        self.lock = threading.Lock()
        self.play_queue = queue.Queue()
        self.stop_token = 0
        self.pcm_cache = {}
        self.hindi_pending = set()
        cache_local = Path(__file__).resolve().parent / "hindi_voice_cache"
        cache_poc = Path(__file__).resolve().parent.parent / "AIPhoneVoiceBot_NoADB_POC" / "hindi_voice_cache"
        self.hindi_cache_dir = cache_local if cache_local.exists() else cache_poc
        self.hindi_cache_dir.mkdir(exist_ok=True)
        self.cache_condition = threading.Condition()
        self.output_ready = threading.Event()
        self._mute_cable_feedback()
        threading.Thread(target=self._player, daemon=True).start()
        threading.Thread(target=self._start, daemon=True).start()

    def _mute_cable_feedback(self):
        if "cable input" not in self.device_name.lower():
            return
        try:
            from pycaw.api.audioclient import ISimpleAudioVolume
            from pycaw.api.audiopolicy import IAudioSessionControl2
            from pycaw.utils import AudioUtilities

            for device in AudioUtilities.GetAllDevices():
                try:
                    if AudioUtilities.GetEndpointDataFlow(device.id) != "eRender":
                        continue
                    if "cable input" not in (device.FriendlyName or "").lower():
                        continue
                    manager = device.AudioSessionManager
                    sessions = manager.GetSessionEnumerator()
                    for index in range(sessions.GetCount()):
                        control = sessions.GetSession(index)
                        control2 = control.QueryInterface(IAudioSessionControl2)
                        display = control2.GetDisplayName() or ""
                        if "cable output" not in display.lower():
                            continue
                        volume = control2.QueryInterface(ISimpleAudioVolume)
                        volume.SetMute(1, None)
                        volume.SetMasterVolume(0.0, None)
                        self.on_event({"type": "route", "message": "Muted CABLE feedback monitor"})
                except Exception:
                    continue
        except Exception as exc:
            self.on_event({"type": "route", "message": f"CABLE feedback mute skipped: {exc}"})

    def _find_output_device(self):
        engine = pa.PyAudio()
        try:
            needle = self.device_name.lower()
            fallback = None
            wasapi = None
            for index in range(engine.get_device_count()):
                info = engine.get_device_info_by_index(index)
                name = info.get("name", "")
                if info.get("maxOutputChannels", 0) <= 0:
                    continue
                if needle in name.lower():
                    if fallback is None and "loopback" not in name.lower():
                        fallback = index
                    if info.get("hostApi") == 2 and "loopback" not in name.lower():
                        wasapi = index
            if wasapi is not None:
                name = engine.get_device_info_by_index(wasapi).get("name", str(wasapi))
                return engine, wasapi, name
            if fallback is not None:
                name = engine.get_device_info_by_index(fallback).get("name", str(fallback))
                return engine, fallback, name
            for index in range(engine.get_device_count()):
                info = engine.get_device_info_by_index(index)
                if info.get("maxOutputChannels", 0) > 0:
                    fallback = index
                    break
            if fallback is None:
                raise RuntimeError("No output audio device found")
            name = engine.get_device_info_by_index(fallback).get("name", str(fallback))
            return engine, fallback, name
        except Exception:
            engine.terminate()
            raise

    def _start(self):
        try:
            process = subprocess.Popen(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(Path(__file__).with_name("sapi_wav.ps1")),
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            with self.lock:
                self.process = process
                if self.closed:
                    process.terminate()
                    return
            for line in process.stdout:
                event = json.loads(line)
                if event.get("type") == "wav":
                    path = event["path"]
                    token = int(event.get("token", -1))
                    try:
                        pcm = self._wav_to_stereo48(path)
                        if event.get("purpose") == "cache":
                            with self.cache_condition:
                                self.pcm_cache[event["text"]] = pcm
                                self.cache_condition.notify_all()
                            self.on_event({"type": "cached", "text": event["text"]})
                        elif token == self.stop_token:
                            self.play_queue.put((pcm, token))
                    finally:
                        try:
                            Path(path).unlink(missing_ok=True)
                        except Exception:
                            pass
                elif event.get("type") == "ready":
                    self.on_event({"type": "ready", "voices": event.get("voices", []), "selected": event.get("selected", ""), "device": self.device_name})
                elif event.get("type") == "error":
                    self.on_event({"type": "error", "message": event.get("message", "voice error")})
            if not self.closed:
                self.on_event({"type": "error", "message": "Cable voice helper stopped"})
        except Exception as exc:
            self.on_event({"type": "error", "message": str(exc)})

    def _condition_for_phone_mic(self, data):
        if len(data) == 0:
            return data

        # Remove DC offset
        data = data.astype(np.float32, copy=False)
        data = data - float(np.mean(data))

        # Smooth 8ms fade in/out to prevent boundary clicks/pops
        fade = min(len(data) // 8, int(0.008 * 48000))
        if fade > 1:
            ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
            data[:fade] *= ramp
            data[-fade:] *= ramp[::-1]

        peak = float(np.max(np.abs(data))) if len(data) else 0.0
        if peak > 0.001:
            # Deliver broadcast/telephony peak volume (0.92 = -0.7 dBFS)
            # Full, rich, human voice clarity with zero clipping distortion
            data = (data / peak) * 0.92

        return np.clip(data, -0.95, 0.95).astype(np.float32)

    def _wav_to_stereo48(self, path):
        with wave.open(str(path), "rb") as wav:
            channels = wav.getnchannels()
            width = wav.getsampwidth()
            rate = wav.getframerate()
            raw = wav.readframes(wav.getnframes())
        if width == 1:
            data = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
        elif width == 2:
            data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
        elif width == 4:
            data = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
        else:
            raise RuntimeError(f"Unsupported WAV sample width: {width}")
        if channels > 1:
            data = data.reshape(-1, channels).mean(axis=1)
        target_rate = 48000
        if rate != target_rate and len(data) > 1:
            old = np.arange(len(data), dtype=np.float32)
            new_len = max(1, int(len(data) * target_rate / rate))
            new = np.arange(new_len, dtype=np.float32) * (len(data) - 1) / max(1, new_len - 1)
            data = np.interp(new, old, data).astype(np.float32)
        data = self._condition_for_phone_mic(data)
        stereo = np.column_stack((data, data))
        return (stereo * 32767.0).astype("<i2").tobytes()

    @staticmethod
    def _get_voice_config():
        paths = [
            Path(__file__).resolve().parent / "voice_settings.json",
            Path(__file__).resolve().parent.parent / "AIPhoneVoiceBot_NoADB_POC" / "voice_settings.json",
        ]
        for p in paths:
            if p.exists():
                try:
                    return json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    pass
        return {"gender": "male", "speed": "+2%", "pitch": "+0Hz"}

    @staticmethod
    def _voice_name(language):
        cfg = CableVoice._get_voice_config()
        gender = cfg.get("gender", "male").lower()
        if gender == "female":
            return {
                "en": "en-IN-NeerjaExpressiveNeural",
                "hi": "hi-IN-SwaraNeural",
                "gu": "gu-IN-DhwaniNeural",
            }.get(language, "en-IN-NeerjaExpressiveNeural")
        else:
            return {
                "en": "en-IN-PrabhatNeural",
                "hi": "hi-IN-MadhurNeural",
                "gu": "gu-IN-NiranjanNeural",
            }.get(language, "hi-IN-MadhurNeural")

    @staticmethod
    def _voice_candidates(language):
        """Return equivalent voices so a temporary Edge voice failure is recoverable."""
        primary = CableVoice._voice_name(language)
        if language == "hi":
            fallback = "hi-IN-SwaraNeural" if primary == "hi-IN-MadhurNeural" else "hi-IN-MadhurNeural"
            return (primary, fallback)
        if language == "gu":
            fallback = "gu-IN-DhwaniNeural" if primary == "gu-IN-NiranjanNeural" else "gu-IN-NiranjanNeural"
            return (primary, fallback)
        if language == "en":
            fallback = "en-IN-NeerjaExpressiveNeural" if primary == "en-IN-PrabhatNeural" else "en-IN-PrabhatNeural"
            return (primary, fallback)
        return (primary,)

    @staticmethod
    def _is_hindi(text):
        return any("\u0900" <= char <= "\u097f" for char in text)

    @staticmethod
    def _clean_for_speech(text):
        """Remove artificial halting punctuation that causes robotic dead silences.
        Replaces danda (।), harsh exclamation (!), and question marks (?) with light
        conversational breath pauses so the voice flows smoothly without pausing."""
        import re
        t = str(text or "")
        t = t.replace("।", ", ")
        t = t.replace("!", ", ")
        t = t.replace("?", ", ")
        t = re.sub(r",\s*,+", ", ", t)
        t = re.sub(r"\s+", " ", t).strip(" ,")
        return t

    def _hindi_cache_path(self, text, language="hi"):
        voice_name = self._voice_name(language)
        cfg = self._get_voice_config()
        speed = cfg.get("speed", "+8%")
        digest = hashlib.sha256((f"{voice_name}:{speed}:v7:{text}").encode("utf-8")).hexdigest()
        return self.hindi_cache_dir / f"{digest}.wav"

    def _generate_elevenlabs(self, text, language="hi", api_key=None):
        import av
        import requests
        import tempfile
        cfg = self._get_voice_config()
        # Default Indian voices or friendly multilingual voices
        # e.g. "Rachel" (21m00Tcm4TlvDq8ikWAM) or user-configured
        voice_id = cfg.get("elevenlabs_voice_id", "").strip() or ("21m00Tcm4TlvDq8ikWAM" if cfg.get("gender") == "female" else "pNInz6obpgDQGcFmaJgB")
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
        headers = {"xi-api-key": api_key, "Content-Type": "application/json"}
        payload = {
            "text": text,
            "model_id": "eleven_multilingual_v2",
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.8,
                "style": 0.35,
                "use_speaker_boost": True
            }
        }
        res = requests.post(url, json=payload, headers=headers, timeout=20)
        res.raise_for_status()
        fd, temporary = tempfile.mkstemp(prefix="eleven_", suffix=".mp3")
        try:
            with open(temporary, "wb") as f:
                f.write(res.content)
            container = av.open(temporary)
            resampler = av.AudioResampler(format="s16", layout="mono", rate=48000)
            pieces = []
            for frame in container.decode(audio=0):
                converted = resampler.resample(frame)
                if not isinstance(converted, list):
                    converted = [converted]
                for item in converted:
                    pieces.append(item.to_ndarray()[0].astype("<i2").tobytes())
            for item in resampler.resample(None):
                pieces.append(item.to_ndarray()[0].astype("<i2").tobytes())
            container.close()
            pcm = b"".join(pieces)
            temp_wav = self._hindi_cache_path(text, language)
            with wave.open(str(temp_wav), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(48000)
                wav.writeframes(pcm)
            return self._wav_to_stereo48(temp_wav)
        finally:
            try:
                os.close(fd)
                Path(temporary).unlink(missing_ok=True)
            except Exception:
                pass

    def _generate_sarvam(self, text, language="hi", api_key=None):
        import base64
        import requests
        import tempfile
        lang_code = {"hi": "hi-IN", "gu": "gu-IN", "en": "en-IN"}.get(language, "hi-IN")
        cfg = self._get_voice_config()
        speaker = "meera" if cfg.get("gender", "female") == "female" else "arvind"
        payload = {
            "inputs": [text],
            "target_language_code": lang_code,
            "speaker": speaker,
            "pitch": 0,
            "pace": 1.05,
            "loudness": 1.5,
            "speech_sample_rate": 22050,
            "enable_preprocessing": True,
            "model": "bulbul:v1"
        }
        res = requests.post("https://api.sarvam.ai/text-to-speech", json=payload,
                            headers={"api-subscription-key": api_key, "Content-Type": "application/json"}, timeout=15)
        res.raise_for_status()
        raw_audio = base64.b64decode(res.json()["audios"][0])
        fd, temporary = tempfile.mkstemp(prefix="sarvam_", suffix=".wav")
        try:
            with open(temporary, "wb") as f:
                f.write(raw_audio)
            return self._wav_to_stereo48(temporary)
        finally:
            try:
                os.close(fd)
                Path(temporary).unlink(missing_ok=True)
            except Exception:
                pass

    def _generate_hindi(self, text, language="hi"):
        import av
        import edge_tts
        import edge_tts.communicate as ec
        import tempfile

        cfg = self._get_voice_config()
        eleven_key = cfg.get("elevenlabs_api_key", "").strip() or os.environ.get("ELEVENLABS_API_KEY", "").strip()
        if eleven_key:
            try:
                path = self._hindi_cache_path(text, language)
                if path.exists():
                    return self._wav_to_stereo48(path)
                pcm = self._generate_elevenlabs(text, language, eleven_key)
                if pcm:
                    return pcm
            except Exception as exc:
                self.on_event({"type": "error", "message": f"ElevenLabs fallback: {exc}"})

        sarvam_key = cfg.get("sarvam_api_key", "").strip() or os.environ.get("SARVAM_API_KEY", "").strip()
        if sarvam_key:
            try:
                path = self._hindi_cache_path(text, language)
                if path.exists():
                    return self._wav_to_stereo48(path)
                pcm = self._generate_sarvam(text, language, sarvam_key)
                if pcm:
                    return pcm
            except Exception as exc:
                self.on_event({"type": "error", "message": f"Sarvam AI fallback to Edge: {exc}"})

        # Native Indic locale SSML patch: Azure Neural synthesizes Indic phonemes,
        # natural prosodic question contours, and correct number pronunciation natively
        def native_mkssml(tc, escaped_text):
            if isinstance(escaped_text, bytes):
                escaped_text = escaped_text.decode("utf-8")
            locale = "-".join(tc.voice.split("-")[:2]) if "-" in tc.voice else "en-US"
            return (
                f"<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='{locale}'>"
                f"<voice name='{tc.voice}'>"
                f"<prosody pitch='{tc.pitch}' rate='{tc.rate}' volume='{tc.volume}'>"
                f"{escaped_text}"
                "</prosody>"
                "</voice>"
                "</speak>"
            )
        ec.mkssml = native_mkssml

        rate = cfg.get("speed", "+8%")
        pitch = cfg.get("pitch", "+0Hz")
        speech_text = self._clean_for_speech(text)
        last_error = None
        for voice_name in self._voice_candidates(language):
            path = self._hindi_cache_path(text, language) if voice_name == self._voice_name(language) else self.hindi_cache_dir / (
                hashlib.sha256((f"{voice_name}:{rate}:v7:{text}").encode("utf-8")).hexdigest() + ".wav"
            )
            if path.exists():
                return self._wav_to_stereo48(path)
            for attempt, delay in enumerate((0, 2, 5), start=1):
                if delay:
                    time.sleep(delay)
                fd, temporary = tempfile.mkstemp(prefix="hindi_voice_", suffix=".mp3")
                os.close(fd)
                mp3_path = Path(temporary)
                try:
                    async def save_audio():
                        await asyncio.wait_for(
                            edge_tts.Communicate(
                                speech_text,
                                voice_name,
                                rate=rate,
                                pitch=pitch,
                            ).save(str(mp3_path)),
                            timeout=30,
                        )

                    asyncio.run(save_audio())
                    container = av.open(str(mp3_path))
                    resampler = av.AudioResampler(format="s16", layout="mono", rate=48000)
                    pieces = []
                    for frame in container.decode(audio=0):
                        converted = resampler.resample(frame)
                        if not isinstance(converted, list):
                            converted = [converted]
                        for item in converted:
                            pieces.append(item.to_ndarray()[0].astype("<i2").tobytes())
                    for item in resampler.resample(None):
                        pieces.append(item.to_ndarray()[0].astype("<i2").tobytes())
                    container.close()
                    pcm = b"".join(pieces)
                    if len(pcm) < 4800:
                        raise RuntimeError("TTS returned empty or truncated audio")

                    temp_wav = path.with_suffix(".tmp.wav")
                    with wave.open(str(temp_wav), "wb") as wav:
                        wav.setnchannels(1)
                        wav.setsampwidth(2)
                        wav.setframerate(48000)
                        wav.writeframes(pcm)
                    temp_wav.replace(path)
                    return self._wav_to_stereo48(path)
                except Exception as exc:
                    last_error = exc
                finally:
                    mp3_path.unlink(missing_ok=True)

        # Fallback to Google Text-to-Speech (gTTS) if Edge-TTS fails
        try:
            from gtts import gTTS
            lang_code = "hi" if language == "hi" else ("gu" if language == "gu" else "en")
            fd, temporary = tempfile.mkstemp(prefix="gtts_voice_", suffix=".mp3")
            os.close(fd)
            mp3_path = Path(temporary)
            gTTS(text=text, lang=lang_code).save(str(mp3_path))
            container = av.open(str(mp3_path))
            resampler = av.AudioResampler(format="s16", layout="mono", rate=48000)
            pieces = []
            for frame in container.decode(audio=0):
                converted = resampler.resample(frame)
                if not isinstance(converted, list):
                    converted = [converted]
                for item in converted:
                    pieces.append(item.to_ndarray()[0].astype("<i2").tobytes())
            for item in resampler.resample(None):
                pieces.append(item.to_ndarray()[0].astype("<i2").tobytes())
            container.close()
            pcm = b"".join(pieces)
            mp3_path.unlink(missing_ok=True)
            if len(pcm) >= 4800:
                temp_wav = path.with_suffix(".tmp.wav")
                with wave.open(str(temp_wav), "wb") as wav:
                    wav.setnchannels(1)
                    wav.setsampwidth(2)
                    wav.setframerate(48000)
                    wav.writeframes(pcm)
                temp_wav.replace(path)
                return self._wav_to_stereo48(path)
        except Exception:
            pass

        raise last_error or RuntimeError("Hindi TTS generation failed")

    def _request_hindi(self, text, token, cache_only=False, language="hi"):
        cache_key = (language, text)
        with self.cache_condition:
            if cache_key in self.hindi_pending or cache_key in self.pcm_cache:
                return
            self.hindi_pending.add(cache_key)

        def worker():
            try:
                pcm = self._generate_hindi(text, language)
                with self.cache_condition:
                    self.pcm_cache[cache_key] = pcm
                    self.hindi_pending.discard(cache_key)
                    self.cache_condition.notify_all()
                if cache_only:
                    self.on_event({"type": "cached", "text": text})
                elif token == self.stop_token and not self.closed:
                    self.play_queue.put((pcm, token))
            except Exception as exc:
                with self.cache_condition:
                    self.hindi_pending.discard(cache_key)
                    self.cache_condition.notify_all()
                self.on_event({"type": "error", "message": f"Hindi TTS: {exc}"})

        threading.Thread(target=worker, daemon=True).start()

    def _player(self):
        engine = None
        stream = None
        name = self.device_name

        def close_output():
            nonlocal engine, stream
            if stream is not None:
                try:
                    stream.stop_stream()
                except Exception:
                    pass
                try:
                    stream.close()
                except Exception:
                    pass
                stream = None
            if engine is not None:
                try:
                    engine.terminate()
                except Exception:
                    pass
                engine = None

        try:
            while True:
                item = self.play_queue.get()
                if item is None:
                    return
                pcm, token = item
                try:
                    played = False
                    # Keep one WASAPI stream for the full bot session. Reopening
                    # PyAudio for every reply can crash VB-CABLE after reply one.
                    for attempt in range(2):
                        try:
                            if stream is None:
                                engine, index, name = self._find_output_device()
                                dev_info = engine.get_device_info_by_index(index)
                                dev_rate = int(dev_info.get("defaultSampleRate", 48000))
                                rates_to_try = [48000]
                                if dev_rate not in rates_to_try:
                                    rates_to_try.append(dev_rate)
                                if 44100 not in rates_to_try:
                                    rates_to_try.append(44100)
                                for r in rates_to_try:
                                    try:
                                        stream = engine.open(
                                            format=pa.paInt16,
                                            channels=2,
                                            rate=r,
                                            output=True,
                                            output_device_index=index,
                                            frames_per_buffer=2048,
                                        )
                                        break
                                    except Exception:
                                        if r == rates_to_try[-1]:
                                            raise
                                self.output_ready.set()
                                self.on_event({"type": "output_ready", "device": name})
                            self.on_event({"type": "playing", "device": name})
                            frame_bytes = 2048 * 2 * 2
                            for offset in range(0, len(pcm), frame_bytes):
                                if token != self.stop_token:
                                    break
                                stream.write(pcm[offset:offset + frame_bytes])
                            played = token == self.stop_token
                            break
                        except Exception:
                            close_output()
                            if attempt:
                                raise
                    if played:
                        self.on_event({"type": "played", "device": name})
                    else:
                        self.on_event({"type": "playback_stopped", "device": name})
                except Exception as exc:
                    self.on_event({"type": "error", "message": str(exc) or type(exc).__name__})
        finally:
            close_output()

    def preload(self, texts, timeout=60.0):
        """Generate fixed replies once so a live call can start playback immediately."""
        unique = list(dict.fromkeys(
            (item if isinstance(item, tuple) else ("en", item))
            for item in texts if item and (item[1] if isinstance(item, tuple) else item)
        ))
        with self.lock:
            if self.closed or self.process is None:
                return 0
            missing = [item for item in unique if item not in self.pcm_cache]
        # Generate fixed replies one at a time. The service can throttle
        # concurrent requests; sequential generation is reliable and the
        # resulting WAVs are persisted for instant future playback. FAQ text,
        # matching, and English STT flow remain unchanged; this is only the
        # local cached WAV backend.
        for language, text in missing:
            try:
                self.pcm_cache[(language, text)] = self._generate_hindi(text, language)
                self.on_event({"type": "cached", "text": text})
            except Exception as exc:
                self.on_event({"type": "error", "message": f"Hindi TTS: {exc}"})
        deadline = time.monotonic() + timeout
        with self.cache_condition:
            while any(item not in self.pcm_cache for item in unique):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self.cache_condition.wait(remaining)
            return sum(item in self.pcm_cache for item in unique)

    def warm_output(self, timeout=5.0):
        """Open the persistent WASAPI stream before the first live answer."""
        if self.output_ready.is_set():
            return True
        self.play_queue.put((b"", self.stop_token))
        return self.output_ready.wait(timeout)

    def send(self, action, **fields):
        with self.lock:
            if self.closed or self.process is None:
                return
            if action == "stop":
                self.stop_token += 1
                while True:
                    try:
                        self.play_queue.get_nowait()
                    except queue.Empty:
                        break
            elif action == "speak":
                text = fields.get("text", "")
                language = fields.get("language", "en")
                cache_key = (language, text)
                if language in {"hi", "gu", "en"}:
                    pcm = self.pcm_cache.get(cache_key)
                    if pcm is not None:
                        self.play_queue.put((pcm, self.stop_token))
                        self.on_event({"type": "cache_hit", "text": text})
                    else:
                        self._request_hindi(text, self.stop_token, language=language)
                    return
                pcm = self.pcm_cache.get(cache_key)
                if pcm is not None:
                    self.play_queue.put((pcm, self.stop_token))
                    self.on_event({"type": "cache_hit", "text": text})
                    return
                fields["token"] = self.stop_token
            try:
                self.process.stdin.write(json.dumps({"action": action, **fields}, ensure_ascii=True) + "\n")
                self.process.stdin.flush()
            except (OSError, ValueError) as exc:
                self.on_event({"type": "error", "message": str(exc)})

    def close(self):
        with self.lock:
            self.closed = True
            self.stop_token += 1
            if self.process is not None:
                try:
                    self.process.stdin.write(json.dumps({"action": "close"}) + "\n")
                    self.process.stdin.flush()
                    self.process.stdin.close()
                except OSError:
                    pass
        self.play_queue.put(None)
