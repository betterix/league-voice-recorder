@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0\.."
title Build League Voice Recorder with Cargo (Rust)

echo ========================================================
echo   League Voice Recorder - Native Rust Release Builder
echo   Rust Edition 2021 ^| Windows x64 Standalone
echo ========================================================
echo.

if /i "%~1"=="clean" (
    echo [INFO] Clean build requested. Purging target directory...
    cargo clean
)

echo [1/3] Building release binary with Cargo (LTO ^& Size Optimized)...
cargo build --release
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Cargo release build failed!
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo [2/3] Staging distribution directory...
if not exist "dist" mkdir "dist"
copy /y "target\release\league-voice-recorder.exe" "dist\LeagueVoiceRecorder.exe"

echo.
echo [3/3] Build complete!
echo Binary output: dist\LeagueVoiceRecorder.exe
echo.
pause
