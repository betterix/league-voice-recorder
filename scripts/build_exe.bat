@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0\.."
title Build League Voice Recorder with Cargo (Rust)

echo ========================================================
echo   League Voice Recorder - Native Rust Release Builder
echo   Rust Edition 2024 ^| Windows x64 Standalone
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
echo [2/3] Staging standalone release executable...
if exist "D:\cargo_target\league-voice-recorder\release\league-voice-recorder.exe" (
    copy /y "D:\cargo_target\league-voice-recorder\release\league-voice-recorder.exe" "LeagueVoiceRecorder.exe"
) else (
    copy /y "target\release\league-voice-recorder.exe" "LeagueVoiceRecorder.exe"
)

echo.
echo [3/4] Compiling NSIS Setup Wizard...
set "MAKENSIS="
if exist "C:\Program Files (x86)\NSIS\makensis.exe" set "MAKENSIS=C:\Program Files (x86)\NSIS\makensis.exe"
if exist "C:\Program Files\NSIS\makensis.exe" set "MAKENSIS=C:\Program Files\NSIS\makensis.exe"

if defined MAKENSIS (
    "%MAKENSIS%" installer.nsi
    echo [OK] NSIS Installer: LeagueVoiceRecorder_0.6.1_x64-setup.exe
) else (
    echo [SKIP] makensis.exe not found. Standalone binary is ready.
)

echo.
echo [4/4] Build complete!
echo Binary output:    LeagueVoiceRecorder.exe
if defined MAKENSIS echo Installer output: LeagueVoiceRecorder_0.6.1_x64-setup.exe
echo.
pause
