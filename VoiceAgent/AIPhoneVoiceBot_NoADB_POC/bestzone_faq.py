"""BestZone product FAQ (bestzoneFAQ.csv) for the live Hindi call bot.

The CSV has 100,000 rows, but every answer is "<product>: <answer template>"
for 40 products x 25 question types. Matching 100k sentences one by one
would be slow and fragile on phone-quality speech, so this module recognises
the two parts separately:

    question -> product (wall hook, lunch box, ...) + question type (price,
    stock, delivery, ...) -> the CSV answer for that pair.

Product names are English in the CSV, but Hindi speech-to-text writes them
in Devanagari ("वॉल हुक"), so each product word has spoken variants below.
"""
import csv
import json
import re
import unicodedata
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CSV_PATH = ROOT / "bestzoneFAQ.csv"
INDEX_PATH = ROOT / "bestzoneFAQ.index.json"

# English product word -> how a caller / Hindi STT may say or write it.
PRODUCT_WORDS = {
    "adhesive": ["एडहेसिव", "अडहेसिव", "एढेसिव", "ऐडहेसिव", "एडेसिव", "एढ़ेसिव", "चिपकने", "चिपकाने"],
    "bathroom": ["बाथरूम", "बाथ रूम", "बातरूम"],
    "hook": ["हुक", "हूक", "हुक्स"],
    "wall": ["वॉल", "वाल", "वोल", "बॉल", "दीवार"],  # Hindi STT model: "wall" -> "बॉल"
    "kitchen": ["किचन", "किचेन", "रसोई"],
    "towel": ["टॉवेल", "टावेल", "टॉवल", "टोवेल", "तौलिया", "तोलिया"],
    "door": ["डोर", "दरवाजा", "दरवाज़ा", "दरवाजे"],
    "utility": ["यूटिलिटी", "युटिलिटी", "यूटिलिटि"],
    "sticker": ["स्टिकर", "स्टीकर"],
    "transparent": ["ट्रांसपेरेंट", "ट्रांसपेरंट", "ट्रांसपैरेंट", "ट्रान्सपेरेंट", "पारदर्शी"],
    "self": ["सेल्फ", "सेल्फ़"],
    "holder": ["होल्डर", "होलडर", "होल्डर्स"],
    "organizer": ["ऑर्गेनाइज़र", "ऑर्गनाइज़र", "ऑर्गेनाइजर", "ऑर्गनाइजर", "आर्गनाइजर", "ऑर्गेनाइसर",
                  "ऑर्गनाइसर", "आर्गेनाइजर", "ऑर्गेनाइज़र्स", "ओर्गनाइजर"],
    "sink": ["सिंक", "सिंग"],
    "soap": ["सोप", "साबुन"],
    "toothbrush": ["टूथब्रश", "टूथ ब्रश", "टुथब्रश"],
    "sponge": ["स्पंज", "स्पॉन्ज"],
    "mounted": ["माउंटेड", "माउंटिड", "माउन्टेड"],
    "shelf": ["शेल्फ", "शेल्फ़", "शैल्फ"],
    "storage": ["स्टोरेज", "स्टोरिज"],
    "basket": ["बास्केट", "बास्किट", "टोकरी"],
    "box": ["बॉक्स", "बाक्स", "बोक्स", "बोख्स", "भोख", "बोग", "डिब्बा", "डब्बा"],
    "drawer": ["ड्रॉअर", "ड्रावर", "ड्रॉर", "ड्रॉवर", "दराज"],
    "cutlery": ["कटलरी", "कटलेरी"],
    "spice": ["स्पाइस", "मसाला", "मसाले"],
    "bottle": ["बोतल", "बॉटल", "बोटल", "बोटन", "भोटल", "बोतन"],
    "food": ["फूड", "फ़ूड", "खाना"],
    "container": ["कंटेनर", "कन्टेनर", "कंटेनर्स"],
    "lunch": ["लंच", "लन्च", "लंज", "लेंज", "लेंच", "टिफिन", "टिफ़िन"],
    "water": ["वॉटर", "वाटर", "वोटर", "वोतर", "पानी"],
    "strainer": ["स्ट्रेनर", "स्ट्रेनेर", "छलनी", "छन्नी"],
    "vegetable": ["वेजिटेबल", "वेजीटेबल", "वेजटेबल", "सब्ज़ी", "सब्जी"],
    "peeler": ["पीलर", "छीलने", "छिलका"],
    "cleaning": ["क्लीनिंग", "क्लिनिंग", "सफाई", "सफ़ाई"],
    "brush": ["ब्रश", "ब्रुश"],
    "microfiber": ["माइक्रोफाइबर", "माइक्रो फाइबर", "माइक्रोफ़ाइबर", "माइक्रोफायबर"],
    "cloth": ["क्लॉथ", "क्लोथ", "कपड़ा", "कपडा"],
    "dish": ["डिश", "बर्तन"],
    "rack": ["रैक", "रेक", "रॅक"],
    "dispenser": ["डिस्पेंसर", "डिस्पेन्सर", "डिसपेंसर"],
    "dustbin": ["डस्टबिन", "डस्ट बिन", "दस्टबिन", "दस्ट्बिन", "नस्टबिन", "नस्द्विन", "कूड़ेदान", "कूड़ादान", "कचरा"],
    "laundry": ["लॉन्ड्री", "लांड्री", "लॉंड्री", "लौंड्री"],
    "shoe": ["शू", "शूज़", "शूज", "शो", "शु", "जूता", "जूते", "जूतों"],
    "cable": ["केबल", "केबिल"],
    "mobile": ["मोबाइल", "मोबाईल", "फोन", "फ़ोन"],
    "plastic": ["प्लास्टिक", "प्लासटिक"],
    "tray": ["ट्रे", "ट्रेय"],
    "measuring": ["मेज़रिंग", "मेजरिंग", "मेझरिंग", "नापने"],
    "spoon": ["स्पून", "चम्मच", "चमच"],
    "silicone": ["सिलिकॉन", "सिलिकोन", "सिलीकॉन"],
    "spatula": ["स्पैचुला", "स्पेचुला", "स्पैटुला", "स्पेटुला", "पलटा"],
}

# Question type keywords (index = order of answer templates in the CSV).
# Each entry: (Hindi/Devanagari or English keyword, weight). Phrases count
# more than single generic words.
INTENT_KEYWORDS = [
    # 0 price
    [("कीमत", 2), ("दाम", 2), ("रेट", 2), ("rate", 2), ("price", 2), ("प्राइस", 2), ("भाव", 2),
     ("कितने का", 2), ("कितने रुपये", 3), ("कितने रुपए", 3), ("कितने का है", 2), ("cost", 1), ("कॉस्ट", 1)],
    # 1 stock
    [("stock", 3), ("स्टॉक", 3), ("स्टोक", 3), ("स्टॉप", 3), ("स्टोप", 3),  # phone STT: stock -> stop
     ("स्तोप", 3), ("स्तोग", 3), ("स्टोग", 3), ("स्तोक", 3), ("टॉप में", 3),
     ("available", 2), ("अवेलेबल", 2), ("उपलब्ध", 1),
     ("है या नहीं", 1), ("मिल जाएगा", 1)],
    # 2 minimum order
    [("minimum", 3), ("मिनिमम", 3), ("न्यूनतम", 3), ("moq", 4), ("एमओक्यू", 4), ("कम से कम", 3)],
    # 3 bulk quotation
    [("bulk", 3), ("बल्क", 3), ("quotation", 3), ("कोटेशन", 3), ("wholesale", 3), ("होलसेल", 3),
     ("थोक", 3), ("ज्यादा quantity", 2)],
    # 4 sample ("sample की कीमत और shipping" is still a sample question)
    [("sample", 7), ("सैंपल", 7), ("सेंपल", 7), ("सैम्पल", 7), ("नमूना", 7)],
    # 5 material
    [("material", 4), ("मटेरियल", 4), ("मटीरियल", 4), ("मैटेरियल", 4), ("किस चीज़ का", 3),
     ("किस चीज का", 3), ("बना है", 2), ("बना हुआ", 2)],
    # 6 size
    [("size", 4), ("sizes", 4), ("dimensions", 4), ("साइज़", 4), ("साइस", 4), ("सैज", 4), ("साइज", 4), ("आकार", 4), ("dimension", 4), ("डायमेंशन", 4),
     ("लंबाई", 3), ("चौड़ाई", 3), ("ऊँचाई", 3), ("ऊंचाई", 3), ("length", 3), ("width", 3),
     ("height", 3), ("कितना बड़ा", 3)],
    # 7 weight
    [("weight", 6), ("वेट", 6), ("वजन", 6), ("वज़न", 6), ("कितना भारी", 5)],
    # 8 colour
    [("colour", 4), ("colours", 4), ("color", 4), ("colors", 4), ("कलर", 4), ("रंग", 4), ("रंगों", 4)],
    # 9 packing
    [("packing", 4), ("पैकिंग", 4), ("पेकिंग", 4), ("packaging", 4), ("पैकेजिंग", 4),
     ("inner pack", 6), ("इनर पैक", 6), ("single piece", 4), ("सिंगल पीस", 4), ("पैक", 2)],
    # 10 carton
    [("carton", 5), ("कार्टन", 5), ("कार्टून", 4), ("कितने पीस", 4), ("pieces", 3), ("पीस", 3)],
    # 11 photos
    [("photo", 4), ("photos", 4), ("फोटो", 4), ("फ़ोटो", 4), ("तस्वीर", 4), ("तस्वीरें", 4),
     ("picture", 4), ("पिक्चर", 4), ("image", 4), ("इमेज", 4), ("angle", 2)],
    # 12 video
    [("video", 8), ("वीडियो", 8), ("विडियो", 8)],
    # 13 usage
    [("उपयोग", 3), ("इस्तेमाल", 3), ("use", 2), ("यूज़", 2), ("यूज", 2), ("instructions", 3),
     ("इंस्ट्रक्शन", 3), ("तरीका", 2), ("कैसे करें", 1), ("कैसे करना", 1), ("कैसे लगाएं", 2)],
    # 14 capacity
    [("capacity", 5), ("कैपेसिटी", 5), ("क्षमता", 5), ("load", 4), ("लोड", 4), ("सह सकता", 4),
     ("कितना सामान", 3)],
    # 15 delivery time
    [("delivery", 3), ("डिलीवरी", 3), ("डिलिवरी", 3), ("कितने दिन", 3), ("कब तक", 2), ("कब होगी", 2),
     ("कब आएगा", 3), ("पहुँचने", 2), ("पहुंचने", 2), ("पहुँचेगा", 3), ("पहुंचेगा", 3), ("dispatch", 3),
     ("डिस्पैच", 3)],
    # 16 shipping charge
    [("shipping", 4), ("शिपिंग", 4), ("transport", 4), ("ट्रांसपोर्ट", 4), ("courier", 4), ("कूरियर", 4),
     ("charge", 2), ("चार्ज", 2), ("भाड़ा", 3), ("खर्च", 1)],
    # 17 pickup
    [("pickup", 5), ("पिकअप", 5), ("पिक अप", 5), ("warehouse", 4), ("वेयरहाउस", 4), ("गोदाम", 4),
     ("खुद ले", 3), ("ले जा सकते", 3)],
    # 18 GST invoice
    [("gst", 5), ("जीएसटी", 5), ("जीएसटी बिल", 5), ("invoice", 5), ("इनवॉइस", 5), ("इनवाइस", 5), ("bill", 3), ("बिल", 3),
     ("tax", 3), ("टैक्स", 3)],
    # 19 payment
    [("payment", 5), ("पेमेंट", 5), ("भुगतान", 5), ("upi", 4), ("यूपीआई", 4), ("cod", 3),
     ("पैसे कैसे", 3), ("पे कैसे", 3)],
    # 20 return
    [("return", 5), ("रिटर्न", 5), ("वापस", 4), ("refund", 4), ("रिफंड", 4), ("exchange", 3),
     ("एक्सचेंज", 3)],
    # 21 damaged / wrong product
    [("damage", 5), ("damaged", 5), ("डैमेज", 5), ("डेमेज", 5), ("टूटा", 5), ("टूटी", 5), ("टूट", 4),
     ("खराब", 4), ("ख़राब", 4), ("गलत", 3), ("complaint", 4), ("कंप्लेंट", 4), ("शिकायत", 4)],
    # 22 warranty
    [("warranty", 5), ("वारंटी", 5), ("वॉरंटी", 5), ("वारन्टी", 5), ("वारन्ती", 5), ("वारन्दी", 5), ("guarantee", 4), ("गारंटी", 4)],
    # 23 dropshipping
    [("dropshipping", 6), ("ड्रॉपशिपिंग", 6), ("ड्रॉप शिपिंग", 6), ("ड्रापशिपिंग", 6), ("ड्रॉप", 3)],
    # 24 agent support
    [("agent", 5), ("एजेंट", 5), ("support", 3), ("सपोर्ट", 3), ("customer care", 5),
     ("कस्टमर केयर", 5), ("बात करनी", 3), ("बात करना", 3), ("इंसान", 3)],
]

# Spoken while the bot waits for the rest of a product question.
ASK_TOPIC_SUFFIX = "के बारे में आप क्या जानना चाहते हैं? जैसे कीमत, स्टॉक, डिलीवरी या साइज़।"


def normalize(value):
    value = unicodedata.normalize("NFC", str(value).lower())
    value = re.sub(r"[^\w\sऀ-ॿ]|[।॥]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _compact(value):
    return normalize(value).replace(" ", "")


@lru_cache(maxsize=1)
def load_catalog(csv_path=str(CSV_PATH)):
    """Read the CSV once (cached in bestzoneFAQ.index.json for fast restarts)."""
    path = Path(csv_path)
    stamp = f"{path.stat().st_size}:{int(path.stat().st_mtime)}"
    index = Path(str(path).rsplit(".", 1)[0] + ".index.json")
    if index.exists():
        try:
            data = json.loads(index.read_text(encoding="utf-8"))
            if data.get("stamp") == stamp:
                return data
        except (OSError, ValueError):
            pass
    products, templates, answers = [], [], {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        next(reader, None)
        for row in reader:
            if not row or ":" not in row[0]:
                continue
            product, template = (part.strip() for part in row[0].split(":", 1))
            if product not in products:
                products.append(product)
            if template not in templates:
                templates.append(template)
            answers[f"{products.index(product)}|{templates.index(template)}"] = row[0].strip()
    data = {"stamp": stamp, "products": products, "templates": templates, "answers": answers}
    try:
        index.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return data


_SKELETON_FOLD = str.maketrans({
    # aspirated / voiced / retroflex / sibilant / nukta variants that phone
    # STT mixes up ("बैमेंट" = पेमेंट, "तूता" = टूटा, "दिश" = डिश)
    # (nukta letters are split first, so क़/ज़/ड़/फ़ fold like क/ज/ड/फ)
    "ख": "क", "ग": "क", "घ": "क",
    "छ": "च", "ज": "च", "झ": "च",
    "ट": "त", "ठ": "त", "ड": "त", "ढ": "त", "थ": "त", "द": "त", "ध": "त",
    "फ": "प", "ब": "प", "भ": "प",
    "श": "स", "ष": "स", "ण": "न", "ं": "न", "ऋ": "र",
})
_DROP = set("ािीुूृेैोौॅॉँः़्अआइईउऊएऐओऔऍऑ")


@lru_cache(maxsize=8192)
def skeleton(word):
    """Consonant sound skeleton of a Devanagari word ("स्टॉक" -> "सतक")."""
    word = unicodedata.normalize("NFC", word)
    # Split precomposed nukta letters so they fold like their base letter.
    word = unicodedata.normalize("NFD", word)
    word = word.translate(_SKELETON_FOLD)
    return "".join(c for c in word if c not in _DROP).translate(_SKELETON_FOLD)


@lru_cache(maxsize=4096)
def _similar(left, right):
    return SequenceMatcher(None, left, right).ratio()


# Letters phone STT swaps at the start of a word ("दिश" = डिश, "तूता" = टूटा).
_FIRST_FOLD = str.maketrans({"ड": "द", "ट": "त", "ठ": "थ", "ढ": "ध", "श": "स", "ष": "स", "झ": "ज", "भ": "ब"})


def _first_letter(word):
    for char in unicodedata.normalize("NFD", word):
        if "अ" <= char <= "ह":
            return char.translate(_FIRST_FOLD)
    return ""


_VOWEL_FOLD = str.maketrans({
    "ी": "ि", "ू": "ु", "ै": "े", "ौ": "ो", "ॉ": "ो", "ॅ": "े", "ई": "इ", "ऊ": "उ", "ऐ": "ए", "औ": "ओ",
    "ं": "न", "ँ": "न",
})


@lru_cache(maxsize=8192)
def vowel_light(word):
    """Sound form that keeps vowels but not their length ("पैसे" != "पीस")."""
    word = unicodedata.normalize("NFD", unicodedata.normalize("NFC", word))
    word = word.translate(_SKELETON_FOLD).translate(_VOWEL_FOLD)
    return "".join(c for c in word if c not in "़्ः").translate(_SKELETON_FOLD)


def _token_matches(token_skel, form_skel, token_raw="", form_raw=""):
    """Sound-skeleton match that tolerates one or two mis-heard letters."""
    if not token_skel or len(form_skel) < 2:
        return False
    if len(form_skel) <= 3:
        # Short words are compared with their vowels, so everyday words do
        # not pass for product words ("पैसे" is not "पीस", "मौसम" is not
        # "मसाला"). Allowed: an exact sound match ("दिश" = डिश, "तूता" =
        # टूटा) or a joined suffix ("हुक्की" = हुक + की).
        if _first_letter(token_raw) != _first_letter(form_raw):
            return False
        heard, form = vowel_light(token_raw), vowel_light(form_raw)
        if len(form_skel) == 3 and token_skel == form_skel and _similar(heard, form) >= 0.7:
            return True  # same three consonants, similar vowels ("किबल" = केबल, not "शल्पा" = सेल्फ)
        if heard == form:
            return True
        return (heard.startswith(form) and len(heard) - len(form) <= 2
                and len(token_skel) - len(form_skel) in (0, 1))
    if token_skel == form_skel:
        return True
    if len(token_skel) < 4:
        return False  # too short to guess safely
    return abs(len(token_skel) - len(form_skel)) <= 2 and _similar(token_skel, form_skel) >= 0.85


# Everyday words that carry no product / topic meaning. Their sound skeletons
# collide with short product words ("से" ~ "शू", "बताइए" ~ "बोतल").
STOP_WORDS = set("""
है हैं हो था थी थे का की के को से में पर और या भी तो ही न ना नहीं क्या कैसे कैसा कैसी कौन कौनसा कौनसी
कितना कितनी कितने कब कहाँ कहां क्यों मैं मुझे मेरा मेरी मेरे हम हमें आप आपका आपकी आपके आपको
यह ये वह वो इस इसका इसकी इसके उस उसका उसकी उसके एक दो जी हाँ हां अब अभी फिर बार
बताइए बताइये बताएं बताएँ बताओ बताना बता बतायें बताये दीजिए दें देना दे दो करें करे करना करनी करो करूँ करूं करु
चाहिए चाहता चाहती चाहते जानना जानकारी जाना सकता सकती सकते सके मिलेगा मिलेगी मिलेंगे मिल मिलता मिलती
होगा होगी होंगे होता होती रहा रही रहे लिए वाला वाली वाले कृपया प्लीज़ please बारे विषय समझाइए विवरण सही
पूछ पूछना पूछताछ प्रश्न सवाल जवाब आगे बढ़ूं खरीदने खरीदना खरीदारी विचार पहले ऑर्डर order
जुड़ी जुड़ा जुड़े तरह तरीका तरीके समस्या दर्ज भेजें भेजिए भेज भेजो लगेंगे लगेगा लगेगी आता आती आते
""".split())

# Topic words that sound like a product word but never name one
# ("ट्रांसपोर्ट" is shipping, not "ट्रांसपेरेंट" = transparent hook).
NOT_PRODUCT_WORDS = {"ट्रांसपोर्ट", "ट्रान्सपोर्ट", "ट्रांस्पोर्ट", "ट्रासपोर्ट"}


def _spoken_units(text):
    """Latin tokens, and Devanagari units (word or two joined neighbours).

    Each unit is (skeleton, spoken text, source words), e.g. "वेर हाँउस"
    is also tried as one word.
    """
    tokens = [token for token in normalize(text).split()
              if token not in STOP_WORDS and token not in NOT_PRODUCT_WORDS]
    latin = {token for token in tokens if token.isascii()}
    words = [token for token in tokens if not token.isascii()]
    units = [(skeleton(w), w, (w,)) for w in words if skeleton(w)]
    for left, right in zip(words, words[1:]):
        units.append((skeleton(left + right), left + right, (left, right)))
    return latin, units


@lru_cache(maxsize=1)
def _word_forms():
    """English product word -> [(exact spoken form, its skeleton), ...]."""
    return {word: [(_compact(form), skeleton(form.replace(" ", ""))) for form in forms]
            for word, forms in PRODUCT_WORDS.items()}


def _product_words_in(text, with_strength=False):
    """English product words heard, and the spoken words used for them.

    with_strength=True also returns the words heard through a real product
    form (not only an everyday Hindi word such as "पानी" or "फोन").
    """
    latin, units = _spoken_units(text)
    found, used, strong = set(), set(), set()
    for word, forms in _word_forms().items():
        if word in latin:
            found.add(word)
            used.add(word)
            strong.add(word)
            continue
        for skel, spoken, sources in units:
            joined = len(sources) > 1  # two words tried as one: no guessing
            hit = next((exact for exact, form_skel in forms
                        if spoken == exact or (skel == form_skel and len(skel) >= 4 if joined
                                               else _token_matches(skel, form_skel, spoken, exact))), None)
            if hit is not None:
                found.add(word)
                used.update(sources)
                # "strong": a real product form, heard precisely (same sounds
                # incl. vowels, or a long word), not a short sound-alike.
                precise = (spoken == hit or vowel_light(spoken) == vowel_light(hit)
                           or len(skeleton(hit)) >= 4)
                if hit not in _WEAK_FORMS and precise:
                    strong.add(word)
                break
    return (found, used, strong) if with_strength else (found, used)


# Everyday Hindi words that also name a product part. Alone they do not
# identify a product ("मेरा फोन खराब है" is not about the mobile holder).
_WEAK_FORMS = {_compact(w) for w in """
पानी फोन फ़ोन खाना कपड़ा कपडा साबुन दरवाजा दरवाज़ा दरवाजे दीवार रसोई कचरा सफाई सफ़ाई तौलिया तोलिया
जूता जूते जूतों बर्तन चिपकने चिपकाने मसाला मसाले टोकरी डिब्बा डब्बा छलनी छन्नी सब्ज़ी सब्जी चम्मच चमच
पलटा नापने छीलने छिलका दराज पारदर्शी
""".split()}


@lru_cache(maxsize=4)
def _word_weights(products):
    """Rare words identify a product: "lunch" -> 1.0, "hook" (8 products) -> 0.125."""
    counts = {}
    for product in products:
        for word in set(product.split()):
            counts[word] = counts.get(word, 0) + 1
    return {word: 1.0 / count for word, count in counts.items()}


def find_product(text, catalog):
    """Return (product, confidence, whole name heard) or (None, 0, False)."""
    words, _, strong = _product_words_in(text, with_strength=True)
    if not words:
        return None, 0.0, False
    weights = _word_weights(tuple(catalog["products"]))
    scored = []
    for product in catalog["products"]:
        parts = product.split()
        heard = sum(weights[p] for p in parts if p in words)
        if not heard:
            continue
        total = sum(weights[p] for p in parts)
        complete = all(p in words for p in parts)
        # A partly heard name needs a word only this product has ("लाअडरी
        # बास्केत": "basket" alone could be laundry or storage basket).
        if not complete and not any(p in strong and weights[p] == 1.0 for p in parts):
            continue
        # A fully heard name wins. Otherwise: rare words heard x share of the
        # name heard ("वाल मून्तेड शेल्स" = wall mounted shelf, not wall hook).
        score = 1.0 + heard if complete else heard * heard / total
        scored.append((score, complete, product))
    if not scored:
        return None, 0.0, False
    scored.sort(reverse=True)
    best_score, complete, best = scored[0]
    second = scored[1][0] if len(scored) > 1 else 0.0
    # "बॉक्स" alone fits storage box and lunch box equally: do not guess.
    if best_score < 0.3 or best_score - second < 0.1:
        return None, 0.0, False
    return best, min(1.0, best_score), complete


def find_intent(text, exclude=(), with_exact=False):
    """Return (template index, score) or (None, 0).

    exclude: spoken words already used for the product name, so that e.g.
    "ट्रांसपेरेंट" is not also read as "ट्रांसपोर्ट" (shipping).
    """
    normal = normalize(text)
    words = [t for t in normal.split() if t not in exclude]
    normal = " ".join(words)
    compact = "".join(t for t in words if not t.isascii())
    # Words STT split in two ("की मत" = कीमत) are also tried joined.
    tokens = set(words) | {a + b for a, b in zip(words, words[1:]) if not a.isascii() and not b.isascii()}
    token_skels = {t: skeleton(t) for t in set(words) if not t.isascii() and t not in STOP_WORDS}
    # A word STT split in two ("जी एस्टी" = जीएसटी): only an exact sound match.
    split_skels = {skeleton(a + b) for a, b in zip(words, words[1:])
                   if not a.isascii() and not b.isascii() and len(skeleton(a + b)) >= 4}
    # Several words STT ran together ("शिप्विंचार्च"): look inside.
    long_skels = [s for s in token_skels.values() if len(s) >= 6]
    joined_skel = "".join(skeleton(t) for t in words if not t.isascii())
    totals = []
    exact_totals = {}
    for index, keywords in enumerate(INTENT_KEYWORDS):
        total = 0
        exact_total = 0  # weight from keywords heard exactly (not sound-alike)
        used = set()  # one spoken word counts once per question type
        for keyword, weight in keywords:
            key = normalize(keyword)
            exact = False
            if " " in key:
                key_skel = "" if key.isascii() else skeleton(key.replace(" ", ""))
                exact = key in normal or key.replace(" ", "") in compact
                hit = key not in used and (exact or (len(key_skel) >= 6 and key_skel in joined_skel))
                source = key
            else:
                source = key if key in tokens else None
                if source is None and not key.isascii() and len(key) >= 5 and key in compact:
                    source = key
                exact = source is not None
                if source is None and not key.isascii():
                    key_skel = skeleton(key)
                    source = next((t for t, s in token_skels.items() if _token_matches(s, key_skel, t, key)),
                                  None)
                    if source is None and len(key_skel) >= 4 and key_skel in split_skels:
                        source = key_skel
                    if source is None and len(key_skel) >= 3:
                        source = next((s for s in long_skels
                                       if len(s) >= len(key_skel) + 3 and key_skel in s), None)
                hit = source is not None and source not in used
            if hit:
                used.add(source)
                total += weight
                if exact:
                    exact_total += weight
        totals.append((total, index))
        exact_totals[index] = exact_total
    totals.sort(reverse=True)
    best, second = totals[0], totals[1]
    if best[0] < 2 or best[0] == second[0]:
        return (None, 0.0, 0) if with_exact else (None, 0.0)
    return (best[1], best[0], exact_totals[best[1]]) if with_exact else (best[1], best[0])


def _resolve_conflicts(text, intent):
    """Small rules where two question types share words."""
    normal = normalize(text)
    has = lambda *words: any(normalize(w) in normal for w in words)  # noqa: E731
    if intent == 15 and has("charge", "चार्ज", "खर्च", "कितना लगेगा", "cost", "कॉस्ट"):
        return 16  # "delivery charge" is a shipping-charge question
    if intent == 0 and has("bulk", "बल्क", "wholesale", "होलसेल", "थोक"):
        return 3
    if intent == 0 and has("sample", "सैंपल", "सेंपल"):
        return 4
    if intent == 7 and has("load", "लोड", "capacity", "कैपेसिटी", "क्षमता", "सह सकता"):
        return 14
    return intent


_REFERENCE_WORDS = set("""
इस उस इसकी उसकी इसका उसका इसके उसके इसमें उसमें इसे उसे ये यह वो वह इन उन इनकी उनकी इनका उनका
""".split())


def _is_follow_up(text):
    words = normalize(text).split()
    if any(word in _REFERENCE_WORDS for word in words):
        return True
    content = [w for w in words if w not in STOP_WORDS]
    return len(content) <= 2


def match(text, catalog=None, context_product=None):
    """Answer a caller question from the BestZone CSV.

    Returns a dict with keys: answer, product, intent, score, kind, alias.
    kind is "answer" (product + type, or type only), "ask_topic" (product
    only: ask what they want to know), or None (no BestZone question).
    """
    catalog = catalog or load_catalog()
    product, product_score, complete_name = find_product(text, catalog)
    _, product_tokens = _product_words_in(text) if product else (None, set())
    intent, intent_score, intent_exact = find_intent(text, exclude=product_tokens, with_exact=True)
    if intent is not None:
        intent = _resolve_conflicts(text, intent)
    used_context = False
    # Use the remembered product only for a real follow-up: no product word
    # heard, and either "उसकी/इसका/..." or a very short question ("डिलीवरी
    # कब होगी?"). An unclear new product ("सुप फुल्डर टूटा आया") must not get
    # the previous product's answer.
    if (intent is not None and intent_exact >= 2 and product is None and context_product
            and not _product_words_in(text)[0] and _is_follow_up(text)):
        product, used_context = context_product, True
    if intent is not None and product is not None:
        key = f"{catalog['products'].index(product)}|{intent}"
        answer = catalog["answers"].get(key)
        if answer:
            return {"answer": answer, "product": product, "intent": intent, "kind": "answer",
                    "score": 0.95 if not used_context else 0.90,
                    "alias": f"bestzone:{product}|{intent}{'|context' if used_context else ''}"}
    if intent is not None and intent_exact >= 2:
        # Without a product, answer only a clearly heard question word
        # ("खर्षंताव" sounding a bit like "गारंटी" is not enough).
        return {"answer": catalog["templates"][intent], "product": None, "intent": intent,
                "kind": "answer", "score": 0.90, "alias": f"bestzone:any|{intent}"}
    if product is not None and complete_name:
        # Only a fully heard product name starts "what do you want to know?";
        # a lone everyday word ("पानी", "फोन") must not.
        return {"answer": f"{product} {ASK_TOPIC_SUFFIX}", "product": product, "intent": None,
                "kind": "ask_topic", "score": 0.90 * product_score, "alias": f"bestzone:{product}|ask"}
    return {"answer": None, "product": None, "intent": None, "kind": None, "score": 0.0, "alias": None}


def speech_parts(answer, catalog=None):
    """Split an answer into reusable audio pieces: [product name, rest]."""
    catalog = catalog or load_catalog()
    for product in catalog["products"]:
        if answer.startswith(product + ":"):
            return [product, answer[len(product) + 1:].strip()]
        if answer.startswith(product + " " + ASK_TOPIC_SUFFIX):
            return [product, ASK_TOPIC_SUFFIX]
    return [answer]


def tts_parts(catalog=None):
    """All audio pieces needed to speak every BestZone answer."""
    catalog = catalog or load_catalog()
    return list(catalog["products"]) + list(catalog["templates"]) + [ASK_TOPIC_SUFFIX]
