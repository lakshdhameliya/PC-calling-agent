"""Mithi v2 Flash Local Hindi TTS Engine.

Integrates Oomni/mithi-v2-flash (Apache 2.0, 99.1M params) for natural,
offline, zero-API-key Indian language speech synthesis.
Supports 'malini' (female) and 'anirban' (male) voices at 24 kHz, resampled
to 48 kHz for the Frontbit Link audio pipeline.
"""

import hashlib
import json
import os
import sys
import threading
import time
import wave
from pathlib import Path

import numpy as np

# Default directories
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
MODEL_DIR = PROJECT_ROOT / "models" / "mithi-v2-flash"
MODEL_REPO = "Oomni/mithi-v2-flash"
DEFAULT_VOICE = "malini"  # "malini" (female) or "anirban" (male)

_MITHI_INSTANCE = None
_MITHI_LOCK = threading.Lock()
_INIT_ATTEMPTED = False
_INIT_ERROR = None


def ensure_mithi_model(model_dir=MODEL_DIR, progress_callback=None):
    """Ensure Mithi v2 Flash weights are downloaded to model_dir with resume support."""
    model_dir = Path(model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)
    required_files = [
        "config.json",
        "decoder.safetensors",
        "voices.safetensors",
        "writer.safetensors",
        "run.py",
        os.path.join("indic_encoder", "model.safetensors"),
        os.path.join("indic_encoder", "spiece.model"),
    ]

    all_exist = all((model_dir / f).exists() for f in required_files)
    if all_exist:
        return model_dir

    if progress_callback:
        progress_callback("Downloading Mithi v2 Flash model from Hugging Face (resumable)...")
    else:
        print(f"[Mithi TTS] Downloading Mithi v2 Flash weights to {model_dir}...")

    from huggingface_hub import snapshot_download

    snapshot_download(
        repo_id=MODEL_REPO,
        local_dir=str(model_dir),
        resume_download=True,
    )

    if progress_callback:
        progress_callback("Mithi v2 Flash model download complete!")
    else:
        print("[Mithi TTS] Model download verified!")
    return model_dir


class MithiTTSEngine:
    def __init__(self, model_dir=MODEL_DIR, voice=DEFAULT_VOICE):
        self.model_dir = Path(model_dir)
        self.voice = voice if voice in ("malini", "anirban", "charvi", "satvik", "vaidehi") else DEFAULT_VOICE
        self.tts = None
        self._load_model()

    def _load_model(self):
        ensure_mithi_model(self.model_dir)

        # Import run.py from model_dir dynamically
        run_py = self.model_dir / "run.py"
        if not run_py.exists():
            raise FileNotFoundError(f"Mithi run.py not found in {self.model_dir}")

        model_dir_str = str(self.model_dir)
        if model_dir_str not in sys.path:
            sys.path.insert(0, model_dir_str)

        import importlib.util

        spec = importlib.util.spec_from_file_location("mithi_run", str(run_py))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        Mithi = getattr(module, "Mithi")
        self.tts = Mithi(model_dir=str(self.model_dir), device="cpu")
        print(f"[Mithi TTS] Successfully loaded Mithi v2 Flash engine on CPU (Voice: {self.voice})")

    def synthesize_wav(self, text, lang="hi", voice=None, out_wav_path=None):
        """Synthesize text and return 48kHz mono 16-bit PCM bytes and WAV file."""
        if self.tts is None:
            raise RuntimeError("Mithi TTS model is not loaded")

        voice = voice or self.voice
        if voice not in ("malini", "anirban", "charvi", "satvik", "vaidehi", "nakul", "vasant", "siddhant", "ahana", "jahnavi"):
            voice = DEFAULT_VOICE

        # Synthesize float32 numpy array at 24 kHz
        audio_24k = self.tts.synthesize(text, lang=lang, voice=voice)

        # Resample from 24000 Hz to 48000 Hz using linear interpolation or scipy
        import scipy.signal
        num_samples_48k = int(len(audio_24k) * 48000 / 24000)
        audio_48k = scipy.signal.resample(audio_24k, num_samples_48k)
        audio_48k = np.clip(audio_48k, -1.0, 1.0)
        pcm_16 = (audio_48k * 32767.0).astype("<i2").tobytes()

        if out_wav_path:
            out_wav_path = Path(out_wav_path)
            out_wav_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = out_wav_path.with_suffix(".tmp.wav")
            with wave.open(str(temp_path), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(48000)
                wav.writeframes(pcm_16)
            temp_path.replace(out_wav_path)

        return pcm_16


def get_mithi_engine(voice=DEFAULT_VOICE, model_dir=MODEL_DIR):
    """Thread-safe singleton getter for MithiTTSEngine."""
    global _MITHI_INSTANCE, _INIT_ATTEMPTED, _INIT_ERROR
    with _MITHI_LOCK:
        if _MITHI_INSTANCE is not None:
            return _MITHI_INSTANCE
        if _INIT_ATTEMPTED and _INIT_ERROR:
            return None
        _INIT_ATTEMPTED = True
        try:
            _MITHI_INSTANCE = MithiTTSEngine(model_dir=model_dir, voice=voice)
            _INIT_ERROR = None
            return _MITHI_INSTANCE
        except Exception as exc:
            _INIT_ERROR = exc
            print(f"[Mithi TTS] Initialization warning: {exc}")
            return None


def synthesize_hindi(text: str, voice: str = DEFAULT_VOICE, out_wav_path=None) -> str:
    """Convenience helper to synthesize text using Mithi v2 Flash to a WAV file."""
    engine = get_mithi_engine(voice=voice)
    if engine is None:
        raise RuntimeError("Failed to initialize MithiTTSEngine")
    if out_wav_path is None:
        import tempfile
        import os
        fd, out_wav_path = tempfile.mkstemp(prefix=f"mithi_{voice}_", suffix=".wav")
        os.close(fd)
    engine.synthesize_wav(text, lang="hi", voice=voice, out_wav_path=Path(out_wav_path))
    return str(out_wav_path)
