@echo off
cd /d "%~dp0\.."
title League Voice Recorder - Test Suite
echo ========================================================
echo Running League Voice Recorder Automated Test Suite...
echo ========================================================
uv run pytest
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Tests finished with errors.
    pause
) else (
    echo.
    echo All tests passed successfully!
    pause
)
