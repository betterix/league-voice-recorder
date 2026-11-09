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

rem Read version from app package constant (Single Source of Truth)
for /f "tokens=*" %%v in ('uv run --python 3.14.10 python -c "import app; print(app.__version__)"') do set "APP_VERSION=%%v"

rem Pre-stage Visual C++ Redistributable DLLs (msvcp140*.dll, etc.) into Python base_prefix for Nuitka scanner discovery
uv run --python 3.14.10 python -c "import sys, os, shutil; sys_dir = os.path.join(os.environ.get('SYSTEMROOT', r'C:\Windows'), 'System32'); dlls = ('msvcp140.dll', 'msvcp140_1.dll', 'msvcp140_2.dll', 'msvcp140_atomic_wait.dll', 'msvcp140_codecvt_ids.dll', 'vcruntime140.dll', 'vcruntime140_1.dll', 'vcruntime140_threads.dll', 'concrt140.dll', 'vcomp140.dll'); [shutil.copy2(os.path.join(sys_dir, d), os.path.join(sys.base_prefix, d)) for d in dlls if os.path.exists(os.path.join(sys_dir, d)) and not os.path.exists(os.path.join(sys.base_prefix, d))]"

echo [OK] Tcl directory: %TCL_DIR%
echo [OK] Tk directory:  %TK_DIR%
echo [OK] App Version:   %APP_VERSION%

echo.
echo [2/5] Compiling standalone executable via Nuitka C Compiler...
echo       Version: %APP_VERSION% ^| Threads: %NUMBER_OF_PROCESSORS% cores ^| Mode: Standalone ^| Cache: Enabled
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
    --file-version=%APP_VERSION%.0 ^
    --product-version=%APP_VERSION%.0 ^
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
    --nofollow-import-to=curses ^
    --nofollow-import-to=wsgiref ^
    --nofollow-import-to=ensurepip ^
    --nofollow-import-to=idlelib ^
    --nofollow-import-to=distutils ^
    --nofollow-import-to=setuptools ^
    --nofollow-import-to=pip ^
    --nofollow-import-to=av ^
    --nofollow-import-to=audiolab ^
    --nofollow-import-to=matplotlib ^
    --nofollow-import-to=fontTools ^
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

rem Ensure Visual C++ runtime DLLs are present in standalone distribution
for %%d in (msvcp140.dll msvcp140_1.dll msvcp140_2.dll msvcp140_atomic_wait.dll msvcp140_codecvt_ids.dll vcruntime140.dll vcruntime140_1.dll vcruntime140_threads.dll concrt140.dll vcomp140.dll) do (
    if not exist "dist\LeagueVoiceRecorder\%%d" (
        if exist "%SYSTEMROOT%\System32\%%d" copy /y "%SYSTEMROOT%\System32\%%d" "dist\LeagueVoiceRecorder\%%d" >nul
    )
)


echo [OK] Standalone distribution assembled in dist\LeagueVoiceRecorder\

echo.
echo [4/5] Checking for Inno Setup compiler...
set "ISCC_EXE="
if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC_EXE=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" set "ISCC_EXE=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if exist "C:\Program Files\Inno Setup 6\ISCC.exe" set "ISCC_EXE=C:\Program Files\Inno Setup 6\ISCC.exe"
if not defined ISCC_EXE (
    where iscc >nul 2>nul
    if !ERRORLEVEL! EQU 0 set "ISCC_EXE=iscc"
)

if defined ISCC_EXE (
    echo Compiling Windows Setup Installer with Inno Setup...
    "%ISCC_EXE%" /DMyAppVersion="%APP_VERSION%" installer.iss
    if !ERRORLEVEL! EQU 0 (
        echo [OK] Installer created successfully!
    ) else (
        echo [WARN] Inno Setup compilation reported warnings or issues.
    )
) else (
    echo [INFO] Inno Setup compiler ISCC not found on PATH.
    echo        To build installer run: winget install JRSoftware.InnoSetup
)

echo.
echo [5/5] Finalizing build summary...
echo ========================================================
echo   BUILD COMPLETED SUCCESSFULLY!
echo ========================================================
echo   Binary:    dist\LeagueVoiceRecorder\LeagueVoiceRecorder.exe
if defined ISCC_EXE echo   Installer: dist\LeagueVoiceRecorder-Setup-v%APP_VERSION%.exe
echo   Started:   %START_TIME%
echo   Finished:  %TIME%
echo ========================================================
pause
