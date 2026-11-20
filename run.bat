@echo off
cd /d "%~dp0"

if "%1"=="--cli" goto cli
if "%1"=="--headless" goto cli

echo Starting League Voice Recorder (Rust GUI)...
cargo run
exit /b %ERRORLEVEL%

:cli
cargo run -- %*
