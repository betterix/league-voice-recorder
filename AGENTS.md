# AGENTS.md — League Voice Recorder Architecture & Operations Manual

Welcome to the **League Voice Recorder** developer and AI agent manual. This document explains the architecture, data flows, APIs, operational nuances, and engineering invariants of the native Rust + Tauri v2 implementation.

---

## 1. Project Purpose & Core Concept

In competitive League of Legends scrimmages and tournament matches, reviewing gameplay with synchronized team communications is critical for team improvement.

This project solves two primary problems:
1. **Automated Scrim Voice Recording (`src/record/`)**: Monitors the active League of Legends client. When a match starts, it automatically captures team Discord voice chat (via WASAPI loopback) along with the player's microphone, dynamically handles in-game pauses, stops when the game ends, extracts the official Riot Game ID, and converts the recording into a compressed `.mp3` file named `<GameID>.mp3`.
2. **Automated Replay Audio Synchronization (`src/replay/`)**: When the user opens any `.rofl` replay in the League client, it detects the running replay, parses the Game ID from the file name, locates the matching `<GameID>.mp3`, and plays the audio via the embedded Rodio audio engine while continuously synchronizing playback time and playback speed (0.5x, 1x, 2x, 4x, etc.) with the League replay playback API.

The project provides:
- A modern desktop GUI built with **Tauri v2** and vanilla HTML/CSS/JS with zero heavy frontend framework bloat.
- An in-game floating HUD overlay (`ui/overlay.html`) supporting click-through transparency (`WS_EX_TRANSPARENT`), native window dragging, and dual visualization modes.
- System tray minimization with native Windows alerts (`MessageBeep`, balloon notifications).

---

## 2. Repository & Package Structure

```text
league-voice-recorder/
│
├── Cargo.toml               # Rust dependencies (Rust 2024 Edition), workspace profile, and Tauri features
├── tauri.conf.json          # Tauri v2 application configuration, multi-window definitions
│
├── src/                     # Core Native Rust Application Engine
│   ├── main.rs              # Application entrypoint, Tauri commands, system tray setup
│   │
│   ├── config/              # Configuration & DPAPI secret management
│   │   ├── mod.rs           # Config module exports
│   │   ├── manager.rs       # config.json load/save & path resolver
│   │   └── dpapi.rs         # Windows CryptProtectData / CryptUnprotectData encryption
│   │
│   ├── record/              # Live Scrim Recording Engine
│   │   ├── mod.rs           # Module exports & shared recording types
│   │   ├── engine.rs        # LiveClientData polling loop & zero-latency trigger
│   │   ├── wasapi.rs        # Native WASAPI loopback and microphone capture (cpal)
│   │   ├── wav_streamer.rs  # Zero-RAM disk-streaming PCM writer & pause lag truncation
│   │   ├── lcu.rs           # LCU lockfile parser & lol-gameflow Game ID query
│   │   └── encoder.rs       # Background MP3 mixer and encoder
│   │
│   ├── replay/              # Frame-Accurate Replay Synchronization
│   │   ├── mod.rs           # Module exports & replay state structures
│   │   ├── sync.rs          # Replay API (:2999/replay/playback) polling & Rodio sync
│   │   ├── process.rs       # psutil-style process detection for League of Legends .rofl
│   │   └── player.rs        # Rodio audio player wrapper (seek, speed, volume, mute)
│   │
│   ├── discord/             # Discord Local IPC
│   │   ├── mod.rs           # Module exports
│   │   └── client.rs        # Named pipe client (\\.\pipe\discord-ipc-*) for live mute sync
│   │
│   ├── cloud/               # Scrim Cloud Synchronization
│   │   ├── mod.rs           # Module exports
│   │   └── client.rs        # REST client for league-voice-recorder-backend
│   │
│   └── overlay/             # In-Game Floating HUD Controller
│       ├── mod.rs           # Module exports
│       └── win32.rs         # Win32 SetWindowLongW WS_EX_TRANSPARENT hit-testing
│
├── ui/                      # Tauri Frontend Webview Layer
│   ├── index.html           # Desktop application interface
│   ├── style.css            # Hextech Dark esports design system
│   ├── app.js               # Reactive state bindings, event listeners, Tauri IPC calls
│   └── overlay.html         # Borderless floating HUD with GradeX waveform canvas
│
├── docs/                    # Exhaustive Architectural Documentation
│   ├── architecture.md      # System architecture, memory model, and tech stack
│   ├── recording.md         # Zero-latency match start, pause lag slicing, sample alignment
│   ├── replay_sync.md       # Replay process detection, Game ID extraction, Rodio player
│   ├── overlay.md           # In-game floating HUD overlay, dual modes, click-through
│   ├── cloud_sync.md        # FastAPI backend integration, DPAPI encryption, RBAC
│   ├── system_tray.md       # Tray minimization, tooltip states, alert sound
│   ├── configuration.md     # config.json schema, storage directory hierarchy
│   └── api_reference.md     # Tauri IPC commands and DTO payloads
│
├── locales/                 # Community i18n JSON translation files
│   ├── en.json              # English (default fallback)
│   └── hu.json              # Hungarian
│
├── assets/                  # High-resolution branding assets
│   ├── icon.ico             # Windows multi-resolution icon
│   ├── icon.png             # UI branding logo
│   └── icon.svg             # Scalable vector logo
│
├── tests/                   # Automated Rust Integration & Unit Tests
│   └── engine_tests.rs      # Engine unit tests & alignment verification
│
└── dist/                    # Compiled distribution binaries (staged releases)
    └── LeagueVoiceRecorder.exe
```

---

## 3. System Architecture & Component Reference

### 3.1 Recording Engine (`src/record/`)
- **Zero-Latency Match Start**: Uses a persistent HTTP keep-alive connection polling `https://127.0.0.1:2999/liveclientdata/allgamedata`. When the match begins, audio recording starts in under 1ms.
- **Sample-Accurate Audio Alignment**:
  - `first_game_time` is recorded at the exact moment capture begins.
  - If recording starts after `00:00:00`, exact silence frames are prepended.
  - If recording starts before `00:00:00` during pre-game countdown, the initial frames are trimmed.
  - Result: `00:00:00` in the exported MP3 matches in-game `game_time = 0.000` frame-by-frame.
- **Zero-Drift Pause Detection & WAV Lag Truncation**:
  - Live client data API polling detects match pause freezes.
  - The ~450ms detection lag is permanently sliced out by seeking both Discord and mic WAV streams back to `44 + target_frame * frame_size` and truncating the files in-place (`truncate_to_game_time`).
- **Hands-Free Match Auto-Stop**: When the match finishes and `<GameID>.mp3` is saved, the engine automatically signals completion and resets UI state.

### 3.2 Replay Synchronizer (`src/replay/`)
- **ROFL Detection & Game ID Parsing**: Inspects active processes for `League of Legends.exe` containing `.rofl` command lines, parses the Game ID (e.g. `EUW1-8052864504.rofl`), and loads `<GameID>.mp3`.
- **Replay API Polling (`:2999/replay/playback`)**:
  - Polls replay time, speed, and pause state every 50ms.
  - **Zero-Latency Unpause**: If unpausing at the same timestamp (`abs(game_time - paused_game_time) < 0.25`), calls `player.play()` immediately without an unnecessary pipeline seek.
  - **Scrubbing & Drift Compensation**: Re-syncs on scrubs or drift (>1.0s) with an 800ms cooldown to avoid seek storms.
  - **Replay Volume & Mute (`M` Hotkey)**: Allows live volume adjustment and toggling mute via the `M` hotkey inside the League client.

### 3.3 In-Game Floating Overlay (`src/overlay/`, `ui/overlay.html`)
- **Hardware-Accelerated Dual Mode**:
  - **Recorder HUD Mode**: Pulsing REC pill, match timer, dual-channel audio visualizers using 24-step GradeX harmonic modulation.
  - **Replay HUD Mode**: Needle waveform canvas, interactive volume slider, and playback status pill.
- **Anti-Bleed Focus Isolation**: Overlay is only visible when actively recording or syncing AND `League of Legends.exe` is the active foreground window. Alt-tabbing hides it immediately.
- **Native Dragging & Click-Through**: Supports Windows `WS_EX_TRANSPARENT` for complete click-through into the game, while preserving dedicated hit targets for the lock toggle and volume slider.

### 3.4 System Tray (`src/main.rs`)
- Left-click toggles app window visibility.
- Minimizing cloaks the window, plays a native `MessageBeep(0x00000040)` chime, and sends a Windows toast/balloon notification.
- Dynamic hover tooltip reflects live recording or syncing state and game clock.

### 3.5 Remote Scrim Cloud Sync (`src/cloud/`, `docs/cloud_sync.md`)
- Seamless client for [league-voice-recorder-backend](https://github.com/betterix/league-voice-recorder-backend).
- Encrypts API keys using Windows DPAPI (`CryptProtectData`).
- Role-based UI adaptation (hiding upload controls for `pull` keys).

---

## 4. Engineering Invariants & Coding Guidelines

1. **Vanguard Safety**: NEVER read or inject into League of Legends process memory. NEVER use virtual audio drivers. Only query official localhost endpoints (`:2999`) and Windows WASAPI.
2. **Audio Timing Integrity**: NEVER alter the sample-accurate match alignment formulas in `src/record/engine.rs` or `src/record/wav_streamer.rs`.
3. **Zero-RAM Streaming**: Audio capture must always stream directly to disk chunks (`.raw_audio/`); never accumulate uncompressed audio buffers in system RAM.
4. **Git Safety**: Never push or commit without explicit user authorization. Never include secrets in `config.json`.
