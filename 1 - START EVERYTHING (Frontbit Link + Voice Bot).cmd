@echo off
title Frontbit Link Plus AI Voice Bot
cd /d "%~dp0"

echo ==========================================================
echo    FRONTBIT LINK AND AI VOICE BOT (ALL-IN-ONE)
echo ==========================================================
echo Starting system...
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0VoiceAgent\smart_launcher.ps1"
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] An error occurred while launching.
    pause
)
