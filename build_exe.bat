@echo off
cd /d "%~dp0"
title Build League Voice Recorder with Nuitka
echo ========================================================
echo Building League Voice Recorder with Nuitka (Python 3.15)...
echo ========================================================

rem Clean previous dist folder to prevent file mixing
if exist "dist\LeagueVoiceRecorder" (
    echo Cleaning existing dist\LeagueVoiceRecorder directory...
    rd /s /q "dist\LeagueVoiceRecorder"
)

rem Resolve Tcl and Tk library directories for Python 3.15
for /f "tokens=*" %%i in ('uv run --python 3.15 python -c "import sys, os; print(os.path.join(sys.base_prefix, 'tcl'))"') do set "TCL_DIR=%%i"
for /f "tokens=*" %%i in ('uv run --python 3.15 python -c "import sys, os; tk = os.path.join(sys.base_prefix, 'tcl', 'tk9.0'); print(tk if os.path.exists(tk) else os.path.join(sys.base_prefix, 'tcl'))"') do set "TK_DIR=%%i"

echo Tcl directory: %TCL_DIR%
echo Tk directory:  %TK_DIR%

rem Compile with Nuitka native C compiler
uv run --python 3.15 --with nuitka python -m nuitka ^
    --standalone ^
    --windows-console-mode=disable ^
    --assume-yes-for-downloads ^
    --windows-icon-from-ico=icon.ico ^
    --enable-plugin=tk-inter ^
    --tcl-library-dir="%TCL_DIR%" ^
    --tk-library-dir="%TK_DIR%" ^
    --include-package-data=customtkinter ^
    --include-data-dir=locales=locales ^
    --include-data-file=icon.ico=icon.ico ^
    --output-dir=dist ^
    --output-folder-name=LeagueVoiceRecorder ^
    --output-filename=LeagueVoiceRecorder.exe ^
    main.py

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Nuitka compilation failed with errors.
    pause
    exit /b %ERRORLEVEL%
)

rem Nuitka appends .dist to folder name, rename to expected destination
if exist "dist\LeagueVoiceRecorder.dist" (
    if exist "dist\LeagueVoiceRecorder" rd /s /q "dist\LeagueVoiceRecorder"
    ren "dist\LeagueVoiceRecorder.dist" "LeagueVoiceRecorder"
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
echo Nuitka Build Succeeded!
echo Executable location: dist\LeagueVoiceRecorder\LeagueVoiceRecorder.exe
echo ========================================================
pause
