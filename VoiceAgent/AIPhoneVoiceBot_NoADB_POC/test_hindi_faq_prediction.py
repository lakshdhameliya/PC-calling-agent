import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fixture_test_faq  # noqa: E402
import hindi_faq  # noqa: E402
from hindi_faq import match  # noqa: E402

# The matcher algorithm is tested on a frozen copy of the original FAQ table,
# so editing the live FAQ in the dashboard does not break these tests.
HINDI_FAQS = list(fixture_test_faq.HINDI_FAQS)
_saved = {}


def setUpModule():
    _saved["faqs"], _saved["keywords"] = hindi_faq.HINDI_FAQS, hindi_faq.HINDI_FAQ_KEYWORDS
    hindi_faq.HINDI_FAQS = HINDI_FAQS
    hindi_faq.HINDI_FAQ_KEYWORDS = list(fixture_test_faq.HINDI_FAQ_KEYWORDS)


def tearDownModule():
    hindi_faq.HINDI_FAQS, hindi_faq.HINDI_FAQ_KEYWORDS = _saved["faqs"], _saved["keywords"]


class HindiFaqPredictionTests(unittest.TestCase):
    def test_every_faq_primary_alias(self):
        for aliases, expected_answer in HINDI_FAQS:
            with self.subTest(question=aliases[0]):
                answer, score, _ = match(aliases[0])
                self.assertEqual(expected_answer, answer)
                self.assertEqual(1.0, score)

    def test_recovers_real_noisy_call_transcripts(self):
        cases = {
            "आप लोग कब तक खुले रहते हैं": 4,
            "मुझे एक अपॉइमेंट करवन है": 8,
            "मुझे एक अपॉइंट करवानी थी": 8,
            "आपके का समय क्या है": 4,
            "कितने पैसे अलगते हैं": 7,
            "मैं आपका सम्पर के सकते हूं": 6,
            "अब मेरी मुदुद कैसे कर सकते हैं": 3,
            "मुझे अपॉइंट बुक करवानी है": 8,
            "इसमें कितने बैसे अलगते हैं": 7,
            "आपका नाम करने का समय क्या है": 4,
        }
        for question, faq_index in cases.items():
            with self.subTest(question=question):
                answer, score, alias = match(question)
                self.assertEqual(HINDI_FAQS[faq_index][1], answer)
                self.assertGreaterEqual(score, 0.54)
                self.assertTrue(alias.startswith("predicted:") or alias.startswith("keywords:"))

    def test_ambiguous_or_unrelated_speech_stays_unknown(self):
        questions = (
            "आप",
            "इसमे कि अवसे लगते हैं",
            "मैं आपके कैसे करूँ",
            "आज मौसम कैसा है",
            "मुझे क्रिकेट का स्कोर बताओ",
            "आपके मालिक का नाम क्या है",
        )
        for question in questions:
            with self.subTest(question=question):
                self.assertIsNone(match(question)[0])

    def test_rephrased_questions_match_dynamic_faq_words(self):
        cases = {
            "मुझे आपके ऑफिस का समय जानना है": 4,
            "मुझे आपकी ऑफिस का समय जानना है": 4,
            "आपकी ऑफिस की टाइमिंग बता दीजिए": 4,
            "मुझे आपके ऑफिस आना है कब आ सकता हूँ": 4,
            "ऑफिस कब खुलता है": 4,
            "मुझे समय बताओ": 4,
            "मुझे पहले कीमत बताओ": 7,
            "आपकी ओफिस के टाइमिंग बता देजिए": 4,
            "मुझे आपके ओफिस का समय जानना है": 4,
            "मुझे आपका समय जानना है": 4,
            "आपका समय बताइए": 4,
            "आपके का समय क्या है": 4,
            "आपका पता कैसे हैं": 5,
        }
        for question, faq_index in cases.items():
            with self.subTest(question=question):
                answer, score, alias = match(question)
                self.assertEqual(HINDI_FAQS[faq_index][1], answer)
                self.assertGreaterEqual(score, 0.78)
                self.assertTrue(alias.startswith("keywords:") or alias.startswith("predicted:"))

    def test_retry_text_can_recover_a_clipped_first_transcript(self):
        self.assertIsNone(match("इमत कितनी है")[0])
        answer, score, alias = match("इमत कितनी है कीमत बताओ")
        self.assertEqual(HINDI_FAQS[7][1], answer)
        self.assertGreaterEqual(score, 0.62)
        self.assertTrue(alias.startswith("keywords:") or alias.startswith("predicted:"))

    def test_new_faq_row_is_indexed_without_matcher_code_changes(self):
        new_answer = "रिफंड की जानकारी हमारी टीम आपको देगी।"
        HINDI_FAQS.append((("रिफंड नीति क्या है", "पैसे वापस करने की नीति"), new_answer))
        try:
            answer, score, alias = match("मुझे रिफंड नीति बताइए")
            self.assertEqual(new_answer, answer)
            self.assertGreaterEqual(score, 0.72)
            self.assertTrue(alias.startswith("keywords:"))
        finally:
            HINDI_FAQS.pop()


if __name__ == "__main__":
    unittest.main()
