@echo off
title Setup Frontbit Link Voice Agent
cd /d "%~dp0"

echo ==========================================================
echo    FRONTBIT LINK - VOICE AGENT SETUP (FOR NEW PC)
echo ==========================================================
echo.

where python >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Python is not installed or not in system PATH!
    echo.
    echo Please install Python 3.10, 3.11, or 3.12 from:
    echo https://www.python.org/downloads/
    echo.
    echo IMPORTANT: Make sure to check "Add python.exe to PATH" during installation!
    echo.
    start "" "https://www.python.org/downloads/"
    pause
    exit /b 1
)

echo [1/3] Python found. Creating virtual environment in VoiceAgent\venv...
if not exist "%~dp0VoiceAgent\venv" (
    python -m venv "%~dp0VoiceAgent\venv"
)

echo [2/3] Upgrading pip...
"%~dp0VoiceAgent\venv\Scripts\python.exe" -m pip install --upgrade pip

echo [3/3] Installing required packages (faster-whisper, edge-tts, pyaudiowpatch, scipy, soundfile, bottle)...
"%~dp0VoiceAgent\venv\Scripts\python.exe" -m pip install -r "%~dp0VoiceAgent\AIPhoneVoiceBot\requirements.txt" edge-tts bottle scipy soundfile

echo.
echo ==========================================================
echo    SETUP COMPLETE! VOICE AGENT IS READY TO USE!
echo ==========================================================
echo You can now run "2 - Start Voice Bot Dashboard.cmd"
pause
