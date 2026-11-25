"""Bot answers with the local meaning model (semantic_faq.py) switched on."""
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hindi_faq  # noqa: E402
import phone_link_no_adb_bot as bot_module  # noqa: E402
import semantic_faq  # noqa: E402

FIRST = {answer: aliases[0] for aliases, answer in hindi_faq.HINDI_FAQS}


class MeaningModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bot = bot_module.FrontbitLinkNoAdbBot.__new__(bot_module.FrontbitLinkNoAdbBot)
        cls.bot.set_status = Mock()
        cls.bot.log = Mock()
        cls.bot.semantic = cls.bot.load_meaning_model()
        if cls.bot.semantic is None or not cls.bot.semantic.available:
            raise unittest.SkipTest("meaning model files not present")

    def ask(self, text):
        self.bot.bestzone_product = None
        answer, score, alias = self.bot.match_question(text)
        if answer is None or score < bot_module.live_gate(alias):
            return None
        return FIRST.get(answer, "CSV")

    def test_every_faq_question_form_still_reaches_its_answer(self):
        wrong = []
        for aliases, answer in hindi_faq.HINDI_FAQS:
            for form in aliases:
                if not form.isascii() and self.ask(form) != aliases[0]:
                    wrong.append(form)
        self.assertEqual(wrong, [])

    def test_new_wording_with_same_meaning(self):
        # Word rules alone answered these wrongly (e.g. "आपसे बात कैसे होगी"
        # -> "आप कैसे हैं").
        cases = {
            "आपसे बात कैसे होगी": "मैं आपसे कैसे संपर्क करूँ",
            "पैसा वापस कब आएगा": "रिफंड कब और कहाँ मिलेगा",
            "माल कब तक पहुंचेगा": "डिलीवरी में कितने दिन लगते हैं",
            "किसी आदमी से बात करवाओ": "एजेंट से बात करनी है",
            "मेरी जानकारी सुरक्षित रहेगी": "मेरा डेटा सुरक्षित है क्या",
            "आपका माल अच्छा है क्या": "प्रोडक्ट की क्वालिटी कैसी है",
            "कैटलॉग भेज दीजिए": "कैटलॉग कैसे डाउनलोड करें",
        }
        answers = {text: self.ask(text) for text in cases}
        wrong = {t: a for t, a in answers.items() if a is not None and a != cases[t]}
        self.assertEqual(wrong, {})  # never a wrong FAQ; unsure -> "please repeat"
        self.assertGreaterEqual(sum(answers[t] == cases[t] for t in cases), 6, answers)

    def test_garbled_call_transcripts(self):
        cases = {
            "विस्ट जों क्या है?": "बेस्टज़ोन क्या है",
            "मुझे अवडर केमसल करना है.": "ऑर्डर कैंसिल कैसे करें",
            "पास्वार्द भुल्गया हु": "पासवर्ड भूल गया",
            "यांपल मिलेगा": "सैंपल मिल सकता है क्या",
        }
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertEqual(self.ask(text), want)

    def test_noise_gets_no_answer(self):
        for text in ("आज मौसम कैसा है", "यर यर सब्च्ट्रीं", "अदेज योर माइद किने", "बच्चे स्कूल गए हैं",
                     "क्रिकेट का स्कोर क्या है", "ठीक है"):
            with self.subTest(text=text):
                self.assertIsNone(self.ask(text))

    def test_without_model_word_rules_still_answer(self):
        bot = bot_module.FrontbitLinkNoAdbBot.__new__(bot_module.FrontbitLinkNoAdbBot)
        bot.semantic = None
        self.assertEqual(FIRST.get(bot.match_question("ऑर्डर कैसे करें")[0]), "ऑर्डर कैसे करें")


if __name__ == "__main__":
    unittest.main()
