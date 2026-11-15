@echo off
set "PATH=C:\Users\Zsolt\AppData\Local\Nuitka\Nuitka\Cache\downloads\gcc\x86_64\15.2.0posix-13.0.0-msvcrt-r6\mingw64\bin;%PATH%"

if "%1"=="--cli" goto cli
if "%1"=="--headless" goto cli

:: Build if binary is missing
if not exist "D:\cargo_target\league-voice-recorder\debug\league-voice-recorder.exe" (
    echo Compiling League Voice Recorder...
    cargo build
)

:: Launch desktop GUI detached without opening or holding a console window
start "" "D:\cargo_target\league-voice-recorder\debug\league-voice-recorder.exe"
exit /b

:cli
cargo run -- %*
