@echo off
cd /d "%~dp0\.."
title League Voice Recorder - Rust Test Suite

echo ========================================================
echo   Running League Voice Recorder Automated Test Suite
echo ========================================================
echo.

cargo test -- --nocapture
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Tests finished with errors.
    pause
    exit /b %ERRORLEVEL%
) else (
    echo.
    echo All Rust tests passed successfully!
    pause
    exit /b 0
)
