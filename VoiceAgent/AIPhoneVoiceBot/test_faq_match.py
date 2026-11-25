import unittest

from call_qwen_voice_bot import faq_match


class FaqMatchTests(unittest.TestCase):
    def answer_for(self, text):
        answer, score, alias = faq_match(text)
        self.assertIsNotNone(answer)
        self.assertGreaterEqual(score, 0.84)
        return answer, alias

    def test_price_paraphrases_survive_small_stt_errors(self):
        expected, _ = self.answer_for("what is the price")
        for text in (
            "what is your price",
            "what is good price",
            "you know i say what is your price",
            "please explain the charges",
        ):
            with self.subTest(text=text):
                answer, _ = self.answer_for(text)
                self.assertEqual(answer, expected)

    def test_contact_paraphrase(self):
        answer, alias = self.answer_for("ok contact call")
        self.assertIn("contact", answer.lower())
        self.assertEqual(alias, "intent:contact")

    def test_unrelated_sentence_does_not_trigger(self):
        answer, score, _ = faq_match("why are you going to understand it")
        self.assertTrue(answer is None or score < 0.84)


if __name__ == "__main__":
    unittest.main()
