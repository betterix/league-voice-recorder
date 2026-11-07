@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0\.."
title Build League Voice Recorder with Nuitka

set "START_TIME=%TIME%"
echo ========================================================
echo   League Voice Recorder - Accelerated Nuitka Builder
echo   Python 3.14.10 Standalone ^| Windows x64
echo ========================================================
echo.

rem Check for clean build argument (e.g. "build_exe.bat clean")
if /i "%~1"=="clean" (
    echo [INFO] Clean build requested. Purging build caches and dist folders...
    if exist "dist\main.build" rd /s /q "dist\main.build"
    if exist "dist\build" rd /s /q "dist\build"
) else (
    echo [INFO] Incremental build mode active - preserving compiler cache.
    echo [INFO] Pass 'clean' argument to force a full re-compilation.
)

rem Clean previous dist output directories to avoid stale artifacts
if exist "dist\LeagueVoiceRecorder" rd /s /q "dist\LeagueVoiceRecorder"
if exist "dist\LeagueVoiceRecorder.dist" rd /s /q "dist\LeagueVoiceRecorder.dist"
if exist "dist\main.dist" rd /s /q "dist\main.dist"

rem Ensure all dependencies are synchronized and downloaded
echo.
echo [1/5] Synchronizing dependencies via uv...
call uv sync --dev --python 3.14.10
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] uv sync failed. Please check internet connection or pyproject.toml.
    pause
    exit /b %ERRORLEVEL%
)

rem Resolve Tcl and Tk library directories for Python 3.14.10
for /f "tokens=*" %%i in ('uv run --python 3.14.10 python -c "import sys, os; print(os.path.join(sys.base_prefix, 'tcl'))"') do set "TCL_DIR=%%i"
for /f "tokens=*" %%i in ('uv run --python 3.14.10 python -c "import sys, os; print(os.path.join(sys.base_prefix, 'tcl', 'tk9.0'))"') do set "TK_DIR=%%i"
if not exist "%TK_DIR%" set "TK_DIR=%TCL_DIR%"

echo [OK] Tcl directory: %TCL_DIR%
echo [OK] Tk directory:  %TK_DIR%

echo.
echo [2/5] Compiling standalone executable via Nuitka C Compiler...
echo       Threads: %NUMBER_OF_PROCESSORS% cores ^| Mode: Standalone ^| Cache: Enabled
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
    --file-version=0.4.2.0 ^
    --product-version=0.4.2.0 ^
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
    --nofollow-import-to=tkinter.test ^
    --nofollow-import-to=test ^
    --nofollow-import-to=multiprocessing.test ^
    --nofollow-import-to=pydoc ^
    --nofollow-import-to=pdb ^
    --nofollow-import-to=difflib ^
    --nofollow-import-to=sqlite3 ^
    --nofollow-import-to=email ^
    --nofollow-import-to=distutils ^
    --nofollow-import-to=setuptools ^
    --nofollow-import-to=pip ^
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
    echo [ERROR] Nuitka compilation failed.
    pause
    exit /b %ERRORLEVEL%
)

rem Normalize folder naming if Nuitka created .dist folder
if exist "dist\LeagueVoiceRecorder.dist" (
    if exist "dist\LeagueVoiceRecorder" rd /s /q "dist\LeagueVoiceRecorder"
    ren "dist\LeagueVoiceRecorder.dist" "LeagueVoiceRecorder"
) else if exist "dist\main.dist" (
    if exist "dist\LeagueVoiceRecorder" rd /s /q "dist\LeagueVoiceRecorder"
    ren "dist\main.dist" "LeagueVoiceRecorder"
)

echo.
echo [3/5] Bundling application assets and metadata...
if exist "locales" xcopy /y /s /i "locales" "dist\LeagueVoiceRecorder\locales" >nul
if exist "assets" xcopy /y /s /i "assets" "dist\LeagueVoiceRecorder\assets" >nul
if exist "LICENSE" copy /y "LICENSE" "dist\LeagueVoiceRecorder\LICENSE" >nul
if exist "README.md" copy /y "README.md" "dist\LeagueVoiceRecorder\README.md" >nul

echo [OK] Standalone distribution assembled in dist\LeagueVoiceRecorder\

echo.
echo [4/5] Checking for Inno Setup compiler...
set "ISCC_EXE="
where iscc >nul 2>nul && set "ISCC_EXE=iscc"
if not defined ISCC_EXE if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC_EXE=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not defined ISCC_EXE if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC_EXE=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC_EXE if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC_EXE=%ProgramFiles%\Inno Setup 6\ISCC.exe"

if defined ISCC_EXE (
    echo Compiling Windows Setup Installer with Inno Setup...
    "%ISCC_EXE%" installer.iss
    if %ERRORLEVEL% EQU 0 (
        echo [OK] Installer created successfully!
    ) else (
        echo [WARN] Inno Setup compilation reported warnings/issues.
    )
) else (
    echo [INFO] Inno Setup compiler (ISCC) not found on PATH.
    echo        To build installer: winget install JRSoftware.InnoSetup
)

echo.
echo [5/5] Finalizing build summary...
echo ========================================================
echo   BUILD COMPLETED SUCCESSFULLY!
echo ========================================================
echo   Binary:    dist\LeagueVoiceRecorder\LeagueVoiceRecorder.exe
if defined ISCC_EXE echo   Installer: dist\LeagueVoiceRecorder-Setup-v0.4.2.exe
echo   Started:   %START_TIME%
echo   Finished:  %TIME%
echo ========================================================
pause
