"""Safe text-only demo for English/Hindi/Gujarati FAQ routing.

It does not open the microphone, Frontbit Link, CABLE, or stop the live bot.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PRODUCTION = ROOT.parent / "AIPhoneVoiceBot"
sys.path.insert(0, str(PRODUCTION))
sys.path.insert(0, str(ROOT))

from call_qwen_voice_bot import faq_match  # noqa: E402
from hindi_faq import match as hindi_match  # noqa: E402
from gujarati_faq import match as gujarati_match  # noqa: E402


SAMPLES = [
    ("English", "What is your name?", faq_match),
    ("Hindi", "आपका नाम क्या है?", hindi_match),
    ("Hindi Roman", "aap kaise hain", hindi_match),
    ("Gujarati", "તમારું નામ શું છે?", gujarati_match),
    ("Gujarati Roman", "appointment book karvi che", gujarati_match),
]


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("Multilingual FAQ demo (text-only; live bot is not touched)\n")
    passed = 0
    for expected_language, question, matcher in SAMPLES:
        answer, score, alias = matcher(question)
        ok = answer is not None and score >= 0.84
        passed += int(ok)
        print(f"[{expected_language}] {question}")
        print(f"  matched={ok} score={score:.2f} alias={alias}")
        print(f"  answer={answer}\n")
    print(f"RESULT: {passed}/{len(SAMPLES)} language FAQ routes passed")
    return 0 if passed == len(SAMPLES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
