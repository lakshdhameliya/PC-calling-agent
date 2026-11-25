@echo off
cd /d "%~dp0"
..\venv\Scripts\python.exe call_qwen_voice_bot.py --voice-device "CABLE Input" --faq-only --stt-model tiny.en --threshold 0.003 --auto-call-state
pause
