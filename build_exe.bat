@echo off
cd /d "%~dp0"
title Build League Voice Recorder .EXE
echo ========================================================
echo Building League Voice Recorder Standalone Executable...
echo ========================================================

uv run pyinstaller --noconfirm --onedir --windowed ^
    --name "LeagueVoiceRecorder" ^
    --collect-data customtkinter ^
    --add-data "locales;locales" ^
    main.py

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Build failed with errors.
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo Copying ffmpeg.exe to output directory...
if exist "ffmpeg.exe" (
    copy /y "ffmpeg.exe" "dist\LeagueVoiceRecorder\" >nul
)

echo Copying locales to output directory...
if exist "locales" (
    xcopy /y /s /i "locales" "dist\LeagueVoiceRecorder\locales" >nul
)

echo Copying .env.example to output directory...
if exist ".env.example" (
    copy /y ".env.example" "dist\LeagueVoiceRecorder\.env.example" >nul
)

echo Copying LICENSE to output directory...
if exist "LICENSE" (
    copy /y "LICENSE" "dist\LeagueVoiceRecorder\LICENSE" >nul
)

echo Copying README.md to output directory...
if exist "README.md" (
    copy /y "README.md" "dist\LeagueVoiceRecorder\README.md" >nul
)

echo.
echo ========================================================
echo Build Succeeded!
echo Executable location: dist\LeagueVoiceRecorder\LeagueVoiceRecorder.exe
echo ========================================================
pause
