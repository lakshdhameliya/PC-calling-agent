import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import call_transcriber as app


class Tests(unittest.TestCase):
    def test_silence_never_becomes_a_sentence(self):
        out = []
        s = app.Segmenter(lambda *x: out.append(x))
        for _ in range(100):
            s.feed(np.zeros(1600, dtype=np.float32))
        s.flush()
        self.assertEqual(out, [])

    def test_pause_flushes_once_and_preserves_speech(self):
        out = []
        s = app.Segmenter(lambda audio, at: out.append(audio))
        for _ in range(3):
            s.feed(np.zeros(1600, dtype=np.float32))
        for _ in range(10):
            s.feed(np.full(1600, .2, dtype=np.float32))
        for _ in range(10):
            s.feed(np.zeros(1600, dtype=np.float32))
        s.flush()
        self.assertEqual(len(out), 1)
        self.assertEqual(np.count_nonzero(out[0]), 16000)

    def test_continuous_speech_is_bounded_and_preserved(self):
        out = []
        s = app.Segmenter(lambda audio, at: out.append(audio))
        for _ in range(150):
            s.feed(np.full(1600, .2, dtype=np.float32))
        s.flush()
        self.assertEqual(sum(map(len, out)), 150 * 1600)
        self.assertTrue(all(len(x) <= 65 * 1600 for x in out))

    def test_files_are_unique_and_unicode_is_saved_immediately(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(app, 'TRANSCRIPTS', Path(folder)):
            a = app.Transcript('test', 'Gujarati')
            b = app.Transcript('test', 'Gujarati')
            self.assertNotEqual(a.path, b.path)
            a.append('હેલો નમસ્તે Hello\n')
            self.assertIn('હેલો નમસ્તે Hello', a.path.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
