@echo off
cd /d "%~dp0\.."
title Build League Voice Recorder with Nuitka
echo ========================================================
echo Building League Voice Recorder with Nuitka (Python 3.14.10 - Standalone)...
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

rem Ensure all dependencies are synchronized and downloaded
echo Synchronizing and downloading required packages via uv...
call uv sync --dev --python 3.14.10
if %ERRORLEVEL% NEQ 0 (
    echo uv sync failed. Please check your internet connection or pyproject.toml.
    pause
    exit /b %ERRORLEVEL%
)

rem Resolve Tcl and Tk library directories for Python 3.14.10
for /f "tokens=*" %%i in ('uv run --python 3.14.10 python -c "import sys, os; print(os.path.join(sys.base_prefix, 'tcl'))"') do set "TCL_DIR=%%i"
for /f "tokens=*" %%i in ('uv run --python 3.14.10 python -c "import sys, os; tcl = os.path.join(sys.base_prefix, 'tcl'); tk9 = os.path.join(tcl, 'tk9.0'); tk8 = os.path.join(tcl, 'tk8.6'); print(tk9 if os.path.exists(tk9) else (tk8 if os.path.exists(tk8) else tcl))"') do set "TK_DIR=%%i"

echo Tcl directory: %TCL_DIR%
echo Tk directory:  %TK_DIR%

rem Compile with Nuitka native C compiler (Standalone mode)
uv run --python 3.14.10 python -m nuitka ^
    --standalone ^
    --jobs=%NUMBER_OF_PROCESSORS% ^
    --lto=no ^
    --windows-console-mode=disable ^
    --assume-yes-for-downloads ^
    --include-windows-runtime-dlls=yes ^
    --windows-icon-from-ico=assets\icon.ico ^
    --company-name="betterix" ^
    --product-name="League Voice Recorder" ^
    --file-version=0.4.1.0 ^
    --product-version=0.4.1.0 ^
    --file-description="League Voice Recorder & Replay Synchronizer" ^
    --copyright="Copyright (c) 2026 betterix. GPL-3.0 License." ^
    --enable-plugin=tk-inter ^
    --no-prefer-source-code ^
    --include-package=proctap ^
    --include-package=pyaudiowpatch ^
    --include-package=pycaw ^
    --nofollow-import-to=scipy ^
    --nofollow-import-to=unittest ^
    --nofollow-import-to=doctest ^
    --nofollow-import-to=av ^
    --nofollow-import-to=audiolab ^
    --nofollow-import-to=matplotlib ^
    --nofollow-import-to=fontTools ^
    --nofollow-import-to=PIL ^
    --nofollow-import-to=soundfile ^
    --tcl-library-dir="%TCL_DIR%" ^
    --tk-library-dir="%TK_DIR%" ^
    --include-data-dir=locales=locales ^
    --include-data-dir=assets=assets ^
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
echo Copying locales to output directory...
if exist "locales" (
    xcopy /y /s /i "locales" "dist\LeagueVoiceRecorder\locales" >nul
)

echo Copying assets to output directory...
if exist "assets" (
    xcopy /y /s /i "assets" "dist\LeagueVoiceRecorder\assets" >nul
)

echo Copying LICENSE to output directory...
if exist "LICENSE" (
    copy /y "LICENSE" "dist\LeagueVoiceRecorder\LICENSE" >nul
)

echo Copying README.md to output directory...
if exist "README.md" (
    copy /y "README.md" "dist\LeagueVoiceRecorder\README.md" >nul
)

echo Cleaning up temporary build artifacts...
if exist "dist\main.build" (
    rd /s /q "dist\main.build"
)

rem Check for Inno Setup compiler (ISCC)
set "ISCC_EXE="
where iscc >nul 2>nul && set "ISCC_EXE=iscc"
if not defined ISCC_EXE if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC_EXE=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not defined ISCC_EXE if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC_EXE=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC_EXE if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC_EXE=%ProgramFiles%\Inno Setup 6\ISCC.exe"

if defined ISCC_EXE (
    echo.
    echo ========================================================
    echo Compiling Windows Setup Installer with Inno Setup...
    echo ========================================================
    "%ISCC_EXE%" installer.iss
    if %ERRORLEVEL% EQU 0 (
        echo [OK] Installer created in dist\
    ) else (
        echo [WARN] Inno Setup compilation had issues.
    )
) else (
    echo [INFO] Inno Setup not found. Skipping installer generation. (Install with: winget install JRSoftware.InnoSetup)
)

echo.
echo ========================================================
echo Build Succeeded!
echo Standalone distribution: dist\LeagueVoiceRecorder\
if defined ISCC_EXE echo Setup Installer:         dist\LeagueVoiceRecorder-Setup-v0.4.1.exe
echo ========================================================
pause
