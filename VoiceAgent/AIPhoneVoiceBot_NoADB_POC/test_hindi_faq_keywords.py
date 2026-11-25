import unittest

import hindi_faq as faq


class HindiKeywordMatcherTests(unittest.TestCase):
    def setUp(self):
        self.old_faqs = faq.HINDI_FAQS
        self.old_keywords = faq.HINDI_FAQ_KEYWORDS
        faq.HINDI_FAQS = [
            (("आपका नाम क्या है",), "NAME"),
            (("कीमत क्या है",), "PRICE"),
            (("सेवा की कीमत क्या है",), "SERVICE_PRICE"),
        ]
        faq.HINDI_FAQ_KEYWORDS = [
            (("नाम",), ("name",), ("naam",)),
            (("दाम",), ("price",), ("keemat",)),
            (("कीमत सेवा",), ("service price",), ("service keemat",)),
        ]
        faq._FAQ_INDEX = None
        faq._FAQ_SIGNATURE = None

    def tearDown(self):
        faq.HINDI_FAQS = self.old_faqs
        faq.HINDI_FAQ_KEYWORDS = self.old_keywords
        faq._FAQ_INDEX = None
        faq._FAQ_SIGNATURE = None

    def test_full_question(self):
        self.assertEqual(faq.match("आपका नाम क्या है")[0], "NAME")

    def test_unique_short_keywords_in_three_languages(self):
        self.assertEqual(faq.match("नाम")[0], "NAME")
        self.assertEqual(faq.match("name")[0], "NAME")
        self.assertEqual(faq.match("naam")[0], "NAME")

    def test_ambiguous_shared_keyword_does_not_guess(self):
        self.assertIsNone(faq.match("कीमत")[0])

    def test_related_keyword_phrase_wins(self):
        self.assertEqual(faq.match("कीमत सेवा")[0], "SERVICE_PRICE")


if __name__ == "__main__":
    unittest.main()
