@echo off
cd /d "%~dp0\.."
title League Voice Recorder (Rust Core)

echo ========================================================
echo   Starting League Voice Recorder (Native Rust GUI)
echo ========================================================
echo.

if exist "target\release\league-voice-recorder.exe" (
    start "" "target\release\league-voice-recorder.exe"
    exit /b 0
)

if exist "target\debug\league-voice-recorder.exe" (
    start "" "target\debug\league-voice-recorder.exe"
    exit /b 0
)

echo Building and starting League Voice Recorder via cargo...
cargo run
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Application exited with an error. Press any key to close...
    pause >nul
)
