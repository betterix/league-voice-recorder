@echo off
cd /d "%~dp0"
title Build League Voice Recorder with Nuitka
echo ========================================================
echo Building League Voice Recorder with Nuitka (Python 3.14.8)...
echo ========================================================

rem Clean previous dist and build folders to prevent file mixing
if exist "dist\LeagueVoiceRecorder" (
    echo Cleaning existing dist\LeagueVoiceRecorder directory...
    rd /s /q "dist\LeagueVoiceRecorder"
)
if exist "dist\build" (
    echo Cleaning previous dist\build...
    rd /s /q "dist\build"
)

rem Resolve Tcl and Tk library directories for Python 3.14.8
for /f "tokens=*" %%i in ('uv run --python 3.14.8 python -c "import sys, os; print(os.path.join(sys.base_prefix, 'tcl'))"') do set "TCL_DIR=%%i"
for /f "tokens=*" %%i in ('uv run --python 3.14.8 python -c "import sys, os; tcl = os.path.join(sys.base_prefix, 'tcl'); tk9 = os.path.join(tcl, 'tk9.0'); tk8 = os.path.join(tcl, 'tk8.6'); print(tk9 if os.path.exists(tk9) else (tk8 if os.path.exists(tk8) else tcl))"') do set "TK_DIR=%%i"

echo Tcl directory: %TCL_DIR%
echo Tk directory:  %TK_DIR%

rem Compile with Nuitka native C compiler (Onefile portable mode)
uv run --python 3.14.8 --with nuitka python -m nuitka ^
    --onefile ^
    --windows-console-mode=disable ^
    --assume-yes-for-downloads ^
    --windows-icon-from-ico=icon.ico ^
    --enable-plugin=tk-inter ^
    --tcl-library-dir="%TCL_DIR%" ^
    --tk-library-dir="%TK_DIR%" ^
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

echo.
echo ========================================================
echo Nuitka Build Succeeded!
echo Portable executable created at: dist\LeagueVoiceRecorder\
echo Files in package:
dir /b "dist\LeagueVoiceRecorder"
echo ========================================================
pause
