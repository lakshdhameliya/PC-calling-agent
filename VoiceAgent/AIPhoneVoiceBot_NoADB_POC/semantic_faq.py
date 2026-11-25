"""Meaning-based FAQ matching with a small local multilingual model.

Model: intfloat/multilingual-e5-small (ONNX, int8, ~120 MB), run with
onnxruntime on the CPU. Every FAQ question form is turned into a "meaning
vector" once; a caller question is matched to the FAQ whose question means
the most similar thing, even with other words, another word order or a few
mis-heard words. No internet is used.

If the model files or onnxruntime are missing, SemanticFaq.available is False
and the bot keeps using its other matchers.
"""
import threading
from pathlib import Path

import numpy as np

MODEL_DIR = Path(__file__).resolve().parent.parent / "AIPhoneVoiceBot" / "models" / "multilingual-e5-small"
MAX_TOKENS = 64


class SemanticFaq:
    def __init__(self, model_dir=MODEL_DIR, threads=2):
        self.available = False
        self.error = None
        self.lock = threading.Lock()
        self._signature = None
        self._vectors = None
        self._items = []
        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer

            self.tokenizer = Tokenizer.from_file(str(Path(model_dir) / "tokenizer.json"))
            self.tokenizer.enable_truncation(max_length=MAX_TOKENS)
            self.tokenizer.enable_padding(pad_id=1, pad_token="<pad>")
            options = ort.SessionOptions()
            options.intra_op_num_threads = threads  # leave the other cores to speech-to-text
            options.inter_op_num_threads = 1
            self.session = ort.InferenceSession(str(Path(model_dir) / "onnx" / "model_quantized.onnx"),
                                                options, providers=["CPUExecutionProvider"])
            self.input_names = {i.name for i in self.session.get_inputs()}
            self.available = True
        except Exception as exc:  # missing model / library: bot falls back
            self.error = f"{type(exc).__name__}: {exc}"

    def embed(self, texts):
        """L2-normalised meaning vectors for a list of texts."""
        encodings = self.tokenizer.encode_batch([f"query: {t}" for t in texts])
        ids = np.array([e.ids for e in encodings], dtype=np.int64)
        mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
        feeds = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self.input_names:
            feeds["token_type_ids"] = np.zeros_like(ids)
        hidden = self.session.run(None, feeds)[0]
        summed = (hidden * mask[:, :, None]).sum(axis=1)
        vectors = summed / np.maximum(mask.sum(axis=1, keepdims=True), 1)
        return vectors / np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-9)

    def index(self, items):
        """items: list of (question form, answer, label). Re-embeds only when they change."""
        signature = tuple(items)
        with self.lock:
            if signature == self._signature:
                return
            vectors = []
            texts = [text for text, _, _ in items]
            for start in range(0, len(texts), 64):
                vectors.append(self.embed(texts[start:start + 64]))
            self._vectors = np.vstack(vectors) if vectors else np.zeros((0, 384), dtype=np.float32)
            self._items = list(items)
            self._signature = signature

    def ranked(self, question, limit=3):
        """[(similarity, answer, matched question form, label)], best answer first."""
        if not self.available or self._vectors is None or not len(self._items):
            return []
        query = self.embed([question])[0]
        with self.lock:
            sims = self._vectors @ query
            items = self._items
        best = {}
        for sim, (text, answer, label) in zip(sims.tolist(), items):
            if sim > best.get(answer, (-1.0,))[0]:
                best[answer] = (sim, answer, text, label)
        return sorted(best.values(), reverse=True)[:limit]
