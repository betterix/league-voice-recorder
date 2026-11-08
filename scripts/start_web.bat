@echo off
title League Voice Recorder - Web Showcase Server
cd /d "%~dp0\..\web"
echo ============================================================
echo   League Voice Recorder - Local Web Showcase Server
echo ============================================================
echo.
echo   Local Address: http://localhost:8080
echo   Docs Address:  http://localhost:8080/docs.html
echo.
echo   Opening browser in 2 seconds...
echo   Press Ctrl+C in this console to stop the server.
echo ============================================================
echo.
start "" "http://localhost:8080"
python -m http.server 8080
pause
