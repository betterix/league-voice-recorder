@echo off
cd /d "%~dp0"
title League Voice Recorder
echo Starting League Voice Recorder UI...
uv run main.py
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Application exited with an error. Press any key to close...
    pause >nul
)
