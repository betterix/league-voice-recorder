@echo off
cd /d "%~dp0"
title Build League Voice Recorder with Nuitka
echo ========================================================
echo Building League Voice Recorder with Nuitka (Python 3.14.8 - Standalone)...
echo ========================================================

rem Clean previous dist and build folders to prevent file mixing
if exist "dist\LeagueVoiceRecorder" (
    echo Cleaning existing dist\LeagueVoiceRecorder directory...
    rd /s /q "dist\LeagueVoiceRecorder"
)
if exist "dist\LeagueVoiceRecorder.dist" (
    rd /s /q "dist\LeagueVoiceRecorder.dist"
)
if exist "dist\main.dist" (
    rd /s /q "dist\main.dist"
)
if exist "dist\build" (
    rd /s /q "dist\build"
)

rem Resolve Tcl and Tk library directories for Python 3.14.8
for /f "tokens=*" %%i in ('uv run --python 3.14.8 python -c "import sys, os; print(os.path.join(sys.base_prefix, 'tcl'))"') do set "TCL_DIR=%%i"
for /f "tokens=*" %%i in ('uv run --python 3.14.8 python -c "import sys, os; tcl = os.path.join(sys.base_prefix, 'tcl'); tk9 = os.path.join(tcl, 'tk9.0'); tk8 = os.path.join(tcl, 'tk8.6'); print(tk9 if os.path.exists(tk9) else (tk8 if os.path.exists(tk8) else tcl))"') do set "TK_DIR=%%i"

echo Tcl directory: %TCL_DIR%
echo Tk directory:  %TK_DIR%

rem Compile with Nuitka native C compiler (Standalone mode)
uv run --python 3.14.8 --with nuitka python -m nuitka ^
    --standalone ^
    --windows-console-mode=disable ^
    --assume-yes-for-downloads ^
    --windows-icon-from-ico=icon.ico ^
    --company-name="betterix" ^
    --product-name="League Voice Recorder" ^
    --file-version=0.2.1.0 ^
    --product-version=0.2.1.0 ^
    --file-description="League Voice Recorder & Replay Synchronizer" ^
    --copyright="Copyright (c) 2026 betterix. GPL-3.0 License." ^
    --enable-plugin=tk-inter ^
    --tcl-library-dir="%TCL_DIR%" ^
    --tk-library-dir="%TK_DIR%" ^
    --include-data-dir=locales=locales ^
    --include-data-file=icon.ico=icon.ico ^
    --output-dir=dist ^
    --output-filename=LeagueVoiceRecorder.exe ^
    main.py

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Nuitka compilation failed with errors.
    pause
    exit /b %ERRORLEVEL%
)

rem Nuitka standalone creates dist\LeagueVoiceRecorder.dist or dist\main.dist
if exist "dist\LeagueVoiceRecorder.dist" (
    if exist "dist\LeagueVoiceRecorder" rd /s /q "dist\LeagueVoiceRecorder"
    ren "dist\LeagueVoiceRecorder.dist" "LeagueVoiceRecorder"
) else if exist "dist\main.dist" (
    if exist "dist\LeagueVoiceRecorder" rd /s /q "dist\LeagueVoiceRecorder"
    ren "dist\main.dist" "LeagueVoiceRecorder"
)

echo.
echo Copying ffmpeg.exe to output directory if present...
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
echo Nuitka Build Succeeded!
echo Standalone distribution created at: dist\LeagueVoiceRecorder\
echo Executable: dist\LeagueVoiceRecorder\LeagueVoiceRecorder.exe
echo ========================================================
pause
