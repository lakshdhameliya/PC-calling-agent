import sys
import os
from pathlib import Path

sys.path.insert(0, r"c:\Frontbit Link\VoiceAgent\AIPhoneVoiceBot_NoADB_POC")
from cable_voice import CableVoice

print("[+] Initializing CableVoice with Mithi v2 Flash...")
v = CableVoice(lambda e: print("EVENT:", e), "Speakers (Realtek(R) Audio)")

# Test 1: Phrase 1
p1 = "नमस्ते, मैं बेस्ट ज़ोन का वॉइस एजेंट हूँ।"
print(f"\n[+] Testing Phrase 1 (malini voice): {p1}")
pcm1 = v._generate_hindi(p1)
cpath1 = v._mithi_cache_path(p1, "hi", "malini")
print(f"    -> Synthesized PCM bytes: {len(pcm1)} bytes")
print(f"    -> Cache file: {cpath1}")
print(f"    -> Cache file exists: {cpath1.exists()}, size: {cpath1.stat().st_size} bytes")

# Test 2: Phrase 2
p2 = "आप मुझसे कुछ भी पूछ सकते हैं।"
print(f"\n[+] Testing Phrase 2 (malini voice): {p2}")
pcm2 = v._generate_hindi(p2)
cpath2 = v._mithi_cache_path(p2, "hi", "malini")
print(f"    -> Synthesized PCM bytes: {len(pcm2)} bytes")
print(f"    -> Cache file: {cpath2}")
print(f"    -> Cache file exists: {cpath2.exists()}, size: {cpath2.stat().st_size} bytes")

# Test 3: anirban voice (male)
print(f"\n[+] Testing anirban voice (male) on Phrase 2: {p2}")
from mithi_tts_engine import synthesize_hindi
f3 = synthesize_hindi(p2, voice="anirban")
print(f"    -> anirban WAV path: {f3}")
print(f"    -> anirban file exists: {Path(f3).exists()}, size: {Path(f3).stat().st_size} bytes")

print("\n[+] ALL TESTS COMPLETED SUCCESSFULLY!")
