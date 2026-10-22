@echo off
cd /d "%~dp0"
title Build League Voice Recorder with Nuitka
echo ========================================================
echo Building League Voice Recorder with Nuitka (Python 3.15)...
echo ========================================================

rem Clean previous dist and build folders to prevent file mixing
if exist "dist\LeagueVoiceRecorder" (
    echo Cleaning existing dist\LeagueVoiceRecorder directory...
    rd /s /q "dist\LeagueVoiceRecorder"
)
if exist "dist\build" (
    rd /s /q "dist\build"
)

rem Resolve Tcl and Tk library directories for Python 3.15
for /f "tokens=*" %%i in ('uv run --python 3.15 python -c "import sys, os; print(os.path.join(sys.base_prefix, 'tcl'))"') do set "TCL_DIR=%%i"
for /f "tokens=*" %%i in ('uv run --python 3.15 python -c "import sys, os; tk = os.path.join(sys.base_prefix, 'tcl', 'tk9.0'); print(tk if os.path.exists(tk) else os.path.join(sys.base_prefix, 'tcl'))"') do set "TK_DIR=%%i"

echo Tcl directory: %TCL_DIR%
echo Tk directory:  %TK_DIR%

rem Compile with Nuitka native C compiler (Onefile portable mode)
uv run --python 3.15 --with nuitka python -m nuitka ^
    --onefile ^
    --windows-console-mode=disable ^
    --assume-yes-for-downloads ^
    --windows-icon-from-ico=icon.ico ^
    --enable-plugin=tk-inter ^
    --tcl-library-dir="%TCL_DIR%" ^
    --tk-library-dir="%TK_DIR%" ^
    --include-package-data=customtkinter ^
    --include-data-dir=locales=locales ^
    --include-data-file=icon.ico=icon.ico ^
    --output-dir=dist\build ^
    --output-filename=LeagueVoiceRecorder.exe ^
    main.py

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Nuitka compilation failed with errors.
    pause
    exit /b %ERRORLEVEL%
)

if not exist "dist\LeagueVoiceRecorder" mkdir "dist\LeagueVoiceRecorder"

echo.
echo Moving compiled executable to dist\LeagueVoiceRecorder...
if exist "dist\build\LeagueVoiceRecorder.exe" (
    move /y "dist\build\LeagueVoiceRecorder.exe" "dist\LeagueVoiceRecorder\LeagueVoiceRecorder.exe" >nul
)
if exist "dist\build" rd /s /q "dist\build"

echo Copying .env.example to output directory...
if exist ".env.example" (
    copy /y ".env.example" "dist\LeagueVoiceRecorder\.env.example" >nul
)

echo Creating clean default config.json with blank password in output directory...
uv run --python 3.15 python -c "import json; from app.config import get_clean_default_config; json.dump(get_clean_default_config(), open(r'dist\LeagueVoiceRecorder\config.json', 'w', encoding='utf-8'), indent=4)"

echo.
echo ========================================================
echo Nuitka Build Succeeded!
echo Clean portable distribution created at: dist\LeagueVoiceRecorder\
echo Files in package:
dir /b "dist\LeagueVoiceRecorder"
echo ========================================================
pause
