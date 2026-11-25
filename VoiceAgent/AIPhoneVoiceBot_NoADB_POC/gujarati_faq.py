"""Gujarati FAQ matching for the no-ADB Frontbit Link bot.

The aliases include Gujarati script and common Roman Gujarati because callers
may speak Gujarati while the recognizer returns either form.
"""
import re
import unicodedata
from difflib import SequenceMatcher


GUJARATI_FAQS = [
    (("નમસ્તે", "નમસ્કાર", "હેલો", "hello", "namaste", "kem cho"),
     "નમસ્તે! હું આજે તમારી કેવી મદદ કરી શકું?"),
    (("તમે કેમ છો", "તમે કેવી રીતે છો", "કેમ છો", "tame kem cho", "kem cho"),
     "હું મજામાં છું, આભાર. હું તમારી કેવી મદદ કરી શકું?"),
    (("તમારું નામ શું છે", "તમારું નામ શુ છે", "તમે કોણ છો", "tamaru naam shu che", "tame kon cho"),
     "હું તમારો લોકલ વોઇસ આસિસ્ટન્ટ છું."),
    (("તમે શું કરી શકો", "તમે મારી કેવી મદદ કરી શકો", "tame shu kari shako", "tame mari kevi madad kari shako"),
     "હું આ કોલ પર સામાન્ય પ્રશ્નોના ઝડપી જવાબ આપી શકું છું."),
    (("તમારા કામના કલાકો શું છે", "તમારા કામનો સમય શું છે", "તમે ક્યારે ખુલ્લા રહો છો", "tamara kaam na kalako shu che", "tamaro kaam no samay shu che"),
     "અમે સોમવારથી શનિવાર સુધી સવારે નવથી સાંજે છ વાગ્યા સુધી ઉપલબ્ધ છીએ."),
    (("તમે ક્યાં સ્થિત છો", "તમારું સ્થાન ક્યાં છે", "તમારી ઓફિસ ક્યાં છે", "tame kya sthit cho", "tamari office kya che"),
     "અમારું ટેસ્ટ સ્થાન અમદાવાદ, ગુજરાત છે."),
    (("હું તમારો સંપર્ક કેવી રીતે કરું", "તમારો સંપર્ક નંબર શું છે", "તમારો સંપર્ક કેવી રીતે કરવો", "hu tamaro sampark kevi rite karu", "tamaro contact number shu che"),
     "તમે આ જ ફોન નંબર પર અમારો સંપર્ક કરી શકો છો."),
    (("કિંમત શું છે", "આની કિંમત કેટલી છે", "આનો ખર્ચ કેટલો છે", "kimat shu che", "aa ni kimat ketli che", "aano kharch ketlo che"),
     "કિંમત સર્વિસ પર આધારિત છે. અમારી ટીમ તમને ચોક્કસ માહિતી આપશે."),
    (("શું હું એપોઇન્ટમેન્ટ બુક કરી શકું", "મારે એપોઇન્ટમેન્ટ જોઈએ", "એપોઇન્ટમેન્ટ બુક કરવી છે", "shu hu appointment book kari shaku", "mare appointment joie", "appointment book karvi che"),
     "હા. કૃપા કરીને તમારી પસંદગીની તારીખ અને સમય જણાવો."),
    (("આભાર", "ધન્યવાદ", "બાય", "આવજો", "aabhar", "dhanyavaad", "bye", "aavjo"),
     "આભાર. તમારો દિવસ શુભ રહે!"),
]

UNKNOWN_GUJARATI = "મારી પાસે હાલમાં આ પ્રશ્નનો જવાબ નથી. કૃપા કરીને બીજો પ્રશ્ન પૂછો."


def normalize(value):
    value = unicodedata.normalize("NFC", value.casefold())
    return re.sub(
        r"\s+", " ",
        "".join(
            c if c.isalnum() or c.isspace() or unicodedata.category(c).startswith("M") else " "
            for c in value
        ),
    ).strip()


def looks_gujarati(value):
    return bool(re.search(r"[\u0a80-\u0aff]", value))


def match(question):
    normalized = normalize(question)
    if not normalized:
        return None, 0.0, None
    for aliases, answer in GUJARATI_FAQS:
        for alias in aliases:
            candidate = normalize(alias)
            if normalized == candidate or normalized.replace(" ", "") == candidate.replace(" ", ""):
                return answer, 1.0, candidate
    for aliases, answer in GUJARATI_FAQS:
        for alias in aliases:
            candidate = normalize(alias)
            if len(candidate.split()) >= 2 and candidate in normalized:
                return answer, 0.96, candidate
    if looks_gujarati(question):
        ranked = []
        for aliases, answer in GUJARATI_FAQS:
            candidates = [
                (SequenceMatcher(None, normalized.replace(" ", ""), normalize(alias).replace(" ", "")).ratio(), alias)
                for alias in aliases if looks_gujarati(alias)
            ]
            if candidates:
                similarity, alias = max(candidates)
                ranked.append((similarity, answer, alias))
        ranked.sort(reverse=True)
        if ranked:
            best, second = ranked[0], ranked[1] if len(ranked) > 1 else (0.0, None, None)
            if best[0] >= 0.86 and best[0] - second[0] >= 0.10:
                return best[1], best[0], best[2]
    return None, 0.0, None
