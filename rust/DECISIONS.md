# Architecture Decision Records (ADRs)

## ADR-001: Maintain 1:1 Parity with Python Desktop App
Status: accepted (2026-11-20)
Context: The user requested a complete migration of the Python application to Rust, matching both the exact UI and functionality from the `main` branch.
Decision: Port each backend component into modular Rust units with direct test coverage matching the original 140 Python unit tests. Preserve the 2-card desktop action layout (Recording Watcher + Replay Auto-Sync), live audio VU meters, health diagnostics ribbon, monospace activity log, recordings browser, and settings editor.
Consequences: Every unit is verified against original test expectations before marking done.

## ADR-002: GUI Framework Selection (Tauri 2 vs Native)
Status: proposed (2026-11-20)
Context: The Python GUI in `main.py` is written in CustomTkinter with a custom dark Hextech esports theme, monospace event log, tabs, and settings.
Decision: Use Tauri 2 with an exact pixel-accurate HTML5/CSS3 frontend modeled after the CustomTkinter dark Hextech esports design.
Consequences: Provides cross-platform rendering fidelity, responsive styling matching the original Hextech palette (`#060B18`, `#1E293B`, `#0AC8B9`), native Windows tray, and low CPU footprint.

## ADR-003: Pure Rust Audio Playback Engine (Rodio vs LibVLC)
Status: accepted (2026-11-20)
Context: The original Python app uses `python-vlc` which requires an external 64-bit VLC installation.
Decision: Utilize pure-Rust `rodio` with dynamic speed/rate multiplier (`set_speed`) and volume control (`set_volume`), with instant seek and pause.
Consequences: Eliminates the external VLC runtime dependency, reducing setup friction and potential crash vectors while enabling sub-millisecond audio sync.

## ADR-004: Audio Capture via WASAPI and CPAL
Status: accepted (2026-11-20)
Context: Python uses `pyaudiowpatch` for Windows WASAPI loopback capture.
Decision: Use `cpal` with WASAPI loopback support in Rust to capture both output loopback (Discord/Game audio) and input device (Microphone).
Consequences: Direct native hardware loopback without third-party audio drivers.

## ADR-005: Strict Git Branching and Remote Push Policy
Status: accepted (2026-11-20)
Context: User explicitly instructed never to push to GitHub, to ignore `agents/` and `serena/`, and to keep all work on the `rust` branch.
Decision: Never invoke `git push`. All commits stay local on branch `rust`.
Consequences: Remote repository remains untouched.
