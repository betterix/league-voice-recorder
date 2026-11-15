@echo off
set "PATH=C:\Users\Zsolt\AppData\Local\Nuitka\Nuitka\Cache\downloads\gcc\x86_64\15.2.0posix-13.0.0-msvcrt-r6\mingw64\bin;%PATH%"
cargo test -- --nocapture
