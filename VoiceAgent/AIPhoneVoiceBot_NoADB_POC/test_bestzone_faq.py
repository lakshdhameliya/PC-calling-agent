"""Checks the BestZone CSV matcher against the CSV's own questions."""
import csv
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bestzone_faq as bz  # noqa: E402


def devanagari(product):
    return " ".join(bz.PRODUCT_WORDS[word][0] for word in product.split())


def csv_cases(step=97):
    """(question, product, template index) from every step-th CSV row."""
    catalog = bz.load_catalog()
    cases = []
    with bz.CSV_PATH.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        next(reader)
        for number, row in enumerate(reader):
            if number % step:
                continue
            product, template = (part.strip() for part in row[0].split(":", 1))
            intent = catalog["templates"].index(template)
            for question in row[1:]:
                cases.append((question, product, intent))
    return cases


class BestZoneCsvTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = bz.load_catalog()
        cls.cases = csv_cases()

    def check(self, transform):
        wrong = []
        for question, product, intent in self.cases:
            text = transform(question, product)
            result = bz.match(text, self.catalog)
            expected = self.catalog["answers"][f"{self.catalog['products'].index(product)}|{intent}"]
            generic = self.catalog["templates"][intent]
            # A product-free CSV question ("GST bill के लिए क्या details चाहिए")
            # correctly gets the general answer for its question type.
            ok = result["answer"] == expected or (product not in question and result["answer"] == generic)
            if not ok:
                wrong.append((text, result["alias"]))
        accuracy = 1 - len(wrong) / len(self.cases)
        return accuracy, wrong

    def test_csv_questions_with_english_product_names(self):
        accuracy, wrong = self.check(lambda q, p: q)
        self.assertGreaterEqual(accuracy, 0.99, wrong[:15])

    def test_csv_questions_with_devanagari_product_names(self):
        accuracy, wrong = self.check(lambda q, p: q.replace(p, devanagari(p)))
        self.assertGreaterEqual(accuracy, 0.99, wrong[:15])

    def test_spoken_hindi_questions(self):
        cases = {
            "वॉल हुक की कीमत क्या है": ("wall hook", 0),
            "लंच बॉक्स स्टॉक में है क्या": ("lunch box", 1),
            "पानी की बोतल का रंग कौन सा मिलेगा": ("water bottle", 8),
            "डस्टबिन की डिलीवरी कब तक होगी": ("dustbin", 15),
            "डस्टबिन का डिलीवरी चार्ज कितना है": ("dustbin", 16),
            "किचन ऑर्गनाइजर का साइज क्या है": ("kitchen organizer", 6),
            "सोप डिस्पेंसर टूटा हुआ आया है": ("soap dispenser", 21),
            "मोबाइल होल्डर की वारंटी है क्या": ("mobile holder", 22),
            "स्टोरेज बॉक्स का बल्क रेट क्या है": ("storage box", 3),
        }
        for text, (product, intent) in cases.items():
            with self.subTest(text=text):
                result = bz.match(text, self.catalog)
                self.assertEqual(result["product"], product)
                self.assertEqual(result["intent"], intent)

    def test_real_phone_stt_transcripts(self):
        # Hindi STT output of spoken questions over a phone-quality line.
        cases = {
            "वाल हुक्की की मत क्या है?": ("wall hook", 0),
            "लंज बोग स्टोप में है क्या?": ("lunch box", 1),
            "स्तोरीज बोख्स का बल्ख कोटेशन चाही.": ("storage box", 3),
            "किजन हुख का समपल में सता है": ("kitchen hook", 4),
            "दिश लाग किस मटेरिल का बना है.": ("dish rack", 5),
            "शो और्गिनाईजर का साइस क्या है?": ("shoe organizer", 6),
            "सोब दिस्पन्सर किन रंगो में मिलता है.": ("soap dispenser", 8),
            "तूथब्रष शूलनर की पैकिं कैसी है?": ("toothbrush holder", 9),
            "वाल मून्तेड शेल्स कितना लोड सहसकता है.": ("wall mounted shelf", 14),
            "केबल और्गेनाईजर का शिप्विंचार्च कितना है.": ("cable organizer", 16),
            "मेज्रिं स्पून के लिए बैमेंट कैसे करें?": ("measuring spoon", 19),
            # "फुल्डर"/"तूता" are only sound-alikes and no product is clear:
            # no answer (the caller is asked to repeat) instead of a guess.
            "मेरा सोप फुल्डर तूता हूँ आखाया है क्या करू?": (None, None),
        }
        for text, (product, intent) in cases.items():
            with self.subTest(text=text):
                result = bz.match(text, self.catalog)
                self.assertEqual((result["product"], result["intent"]), (product, intent))

    def test_everyday_speech_is_not_a_product_question(self):
        for text in ("आज मौसम कैसा है", "मेरे पास पैसे नहीं है", "आपका नाम क्या है", "आप कैसे हैं",
                     "मुझे पानी चाहिए", "आप क्या कर सकते हैं", "मैं आपका सवाल समझ नहीं पाया",
                     "बेस्टज़ोन में आपका स्वागत है", "ठीक है", "हाँ जी बोलिए", "हुक चाहिए"):
            with self.subTest(text=text):
                self.assertIsNone(bz.match(text, self.catalog)["kind"])

    def test_answer_audio_pieces(self):
        answer = bz.match("वॉल हुक की कीमत क्या है", self.catalog)["answer"]
        self.assertEqual(bz.speech_parts(answer, self.catalog),
                         ["wall hook", self.catalog["templates"][0]])
        for part in bz.speech_parts(answer, self.catalog):
            self.assertIn(part, bz.tts_parts(self.catalog))

    def test_type_only_and_product_only(self):
        self.assertEqual(bz.match("GST बिल कैसे मिलेगा", self.catalog)["answer"], self.catalog["templates"][18])
        ask = bz.match("मुझे लंच बॉक्स चाहिए", self.catalog)
        self.assertEqual(ask["kind"], "ask_topic")
        self.assertEqual(ask["product"], "lunch box")
        followup = bz.match("इसकी कीमत क्या है", self.catalog, context_product="lunch box")
        self.assertEqual(followup["product"], "lunch box")
        self.assertEqual(followup["intent"], 0)

    def test_unrelated_and_ambiguous_get_nothing(self):
        for text in ("आज मौसम कैसा है", "नमस्ते", "आपका नाम क्या है", "बॉक्स चाहिए"):
            with self.subTest(text=text):
                self.assertIsNone(bz.match(text, self.catalog)["kind"])


if __name__ == "__main__":
    unittest.main()
