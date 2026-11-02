# AGENTS.md — League Voice Recorder & Replay Synchronizer

Welcome to the **League Voice Recorder** project documentation. This document is designed for AI agents and developers to understand the architecture, data flows, APIs, and operational nuances of the entire codebase.

---

## 1. Project Purpose & Core Concept

In competitive League of Legends scrimmages and team matches, reviewing gameplay with synchronized team communications is critical. 

This project solves two primary problems:
1. **Automated Scrim Voice Recording (`record.py`)**: Monitors the active League of Legends client. When a match starts, it automatically captures team Discord voice chat (via WASAPI loopback) along with the player's microphone and records them directly via native WASAPI loopback. It dynamically pauses/unpauses recording if the game pauses, stops when the game ends, extracts the official Riot Game ID, and converts the recording into a compressed `.mp3` file named `<GameID>.mp3`.
2. **Automated Replay Audio Synchronization (`play_replay.py`)**: When the user opens any `.rofl` replay in the League client, it detects the running replay, parses the Game ID from the file name, locates the matching `<GameID>.mp3`, and plays the audio in VLC while continuously synchronizing playback time and playback speed (0.5x, 1x, 2x, 4x, etc.) with the League replay playback API.

The project provides both:
- A modern dark-mode desktop GUI (`main.py` using `customtkinter`) with **2 primary buttons** ("Start Recording Watcher" and "Start Replay Auto-Sync").
- Standalone CLI execution for headless or script-based workflows.

---

## 2. Repository & Package Structure

```text
League Voice Recorder/
│
├── main.py                 # Primary Desktop GUI application entrypoint
├── installer.iss           # Inno Setup 6 dual-language Windows installer script
├── scripts/                # Windows automation & build scripts
│   ├── start_ui.bat        # Windows quick-launcher for the GUI
│   ├── run_tests.bat       # Automated test suite runner batch script
│   └── build_exe.bat       # Standalone Windows .exe compiler batch script
│
├── app/                    # Core Python application package
│   ├── __init__.py         # Package marker
│   ├── config.py           # Configuration loader, saver, and path resolver
│   ├── discord_ipc.py      # Discord Local IPC client (live mute/deafen tracking)
│   ├── i18n.py             # Internationalization and community translation loader
│   ├── record.py           # LeagueRecorder engine (WASAPI capture & FFmpeg alignment)
│   ├── native_audio.py     # Native WASAPI audio capture (Discord loopback & mic)
│   ├── play_replay.py      # ReplaySynchronizer engine (process watcher & VLC sync)
│   └── updater.py          # Auto-updater (GitHub releases, zip extraction, in-place replace)
│
├── assets/                 # Visual branding assets (icons, logos)
│   ├── icon.ico            # Windows executable multi-resolution icon
│   ├── icon.png            # PNG logo graphic
│   └── icon.svg            # Scalable vector logo
│
├── locales/                # Community and built-in i18n translation JSON files
│   ├── en.json             # English (default fallback)
│   └── hu.json             # Hungarian
│
├── web/                    # Official landing page & interactive desktop app simulator
│   ├── assets/             # Web logos and visual assets
│   ├── css/style.css       # Hextech esports design system & responsive styling
│   ├── js/app.js           # Particle canvas, i18n dictionary, interactive app simulator
│   ├── index.html          # Semantic showcase landing page
│   └── README.md           # Web documentation & local preview guide
│
├── tests/                  # Automated test suite (70 unittests)
│   ├── __init__.py
│   ├── test_config.py      # Config defaults, path resolution, and directory creation
│   ├── test_discord_ipc.py # Discord Local IPC & mute silence injection
│   ├── test_i18n.py        # Locale discovery, translation lookup, and key parity
│   ├── test_recorder.py    # FFmpeg sample-accurate offset alignment & replay guard
│   ├── test_replay_sync.py # ROFL Game ID extraction & volume clamping
│   ├── test_ui.py          # CustomTkinter headless UI & live language switching
│   └── test_updater.py     # Release asset check, zip extraction, and in-place update
│
├── .github/
│   ├── ISSUE_TEMPLATE/     # Structured GitHub Issue Form templates
│   │   ├── bug_report.yml  # Interactive bug report form
│   │   ├── feature_request.yml # Feature & scrim enhancement request form
│   │   └── config.yml      # Issue template configuration & security redirect
│   └── workflows/
│       └── release.yml     # Automated GitHub Actions release compilation
│
├── .gitattributes          # Cross-platform LF/CRLF normalization
├── .env.example            # Environment variables template
├── CODE_OF_CONDUCT.md     # Community guidelines (Contributor Covenant v2.1)
├── CONTRIBUTING.md        # Contribution guide (workflow, setup, invariants)
├── SECURITY.md             # Security policy & private vulnerability reporting
├── pyproject.toml          # Project dependencies and tool configurations
├── ruff.toml               # Linter rules and exclusions
└── requirements.txt        # Frozen dependencies export
```

---

## 3. System Architecture

```mermaid
flowchart TD
    subgraph UI_Layer ["User Interface (main.py)"]
        GUI["CustomTkinter App"]
        RecBtn["🔴 Start/Stop Recording Watcher"]
        RepBtn["▶ Start/Stop Replay Auto-Sync"]
        LogBox["Activity Log (Thread-safe Queue)"]
        HealthRibbon["System Health Diagnostics"]
        RecExplorer["Recordings Browser & Player"]
    end

    subgraph Config_Layer ["Configuration (app/config.py & config.json)"]
        Cfg["Config Loader / Saver"]
        FFmpegHelper["FFmpeg Path Resolver"]
    end

    subgraph Recorder_Engine ["Recorder Engine (app/record.py & app/native_audio.py)"]
        LeagueRec["LeagueRecorder"]
        LiveAPI["Live Client Data (:2999)"]
        LCULockfile["LCU Lockfile / Gameflow API"]
        DiscordIPC["Discord Local IPC (Live Mute/Deafen Sync)"]
        NativeWASAPI["Native WASAPI (Discord Loopback + Mic)"]
        FFmpegProc["FFmpeg MP3 Converter"]
    end

    subgraph Replay_Engine ["Replay Engine (app/play_replay.py)"]
        ReplaySync["ReplaySynchronizer"]
        ProcIter["psutil Process Inspector (rofl detector)"]
        ReplayAPI["League Replay API (:2999/replay/playback)"]
        VLC["VLC Player Engine (python-vlc)"]
    end

    subgraph Storage ["Storage (recordings/)"]
        AudioFiles["<GameID>.mp3"]
    end

    GUI --> RecBtn & RepBtn & LogBox & HealthRibbon & RecExplorer
    RecBtn --> LeagueRec
    RepBtn --> ReplaySync
    Cfg --> LeagueRec & ReplaySync & GUI

    LeagueRec --> LiveAPI
    LeagueRec --> LCULockfile
    DiscordIPC --> NativeWASAPI
    LeagueRec --> NativeWASAPI
    LeagueRec --> FFmpegProc
    FFmpegProc --> AudioFiles

    ReplaySync --> ProcIter
    ReplaySync --> ReplayAPI
    ReplaySync --> VLC
    AudioFiles --> VLC
```

---

## 3. Component Reference

### 3.1 `main.py`
The desktop GUI application built using `customtkinter`.
- **Primary Controls**:
  - `🔴 Start Recording Watcher` / `⏹ Stop Recording Watcher`: Toggles the recording watcher thread (`LeagueRecorder.run_loop()`).
  - `▶ Start Replay Auto-Sync` / `⏹ Stop Replay Auto-Sync`: Toggles the replay sync watcher thread (`ReplaySynchronizer.run_loop()`). No manual file selection is needed; it detects and synchronizes automatically.
- **Header & System Health**:
  - Real-time diagnostics for Discord voice capture, League Client process, VLC 64-bit engine, and FFmpeg binary.
- **Tabbed Center Area**:
  - **Activity Log**: Thread-safe monospace log terminal displaying live events with timestamps and auto-scroll.
  - **Scrim Recordings**: File manager showing recorded `.mp3` files, sizes, creation timestamps, with "Play in VLC" and "Open in Explorer" actions.
  - **Settings**: Configuration editor for recording folder, VLC directory, microphone device, polling intervals, replay volume, and interface language.

### 3.2 `record.py` (`LeagueRecorder`)
Responsible for live match detection, native WASAPI voice capture, and audio export.
- **League Match Detection & Zero-Latency Start**:
  - Uses a persistent HTTP keep-alive session (`requests.Session()`) polling `https://127.0.0.1:2999/liveclientdata/allgamedata`.
  - **Zero-Latency Start**: When a live match begins, `start_recording(initial_game_time)` triggers Native WASAPI recording directly in under 1 millisecond.
  - **Sample-Accurate Audio Alignment**:
    - Stores `first_game_time` at the exact moment recording begins.
    - If recording starts after 00:00 (`first_game_time > 0`), FFmpeg automatically prepends exact silence using `-af adelay=delays={delay_ms}:all=1`.
    - If recording starts before 00:00 during countdown (`first_game_time < 0`), FFmpeg automatically trims the pre-game countdown using `-ss {abs(offset):.3f}`.
    - This guarantees that 00:00:00 in the MP3 file is aligned frame-by-frame with League match time `game_time = 0.000`.
  - **Replay Protection (`check_if_game_is_replay`)**: Checks process command-line arguments and cached PID for `.rofl` files. If a replay is active, live match recording is aborted/prevented.
  - **Zero-Drift Pause Detection & WAV Lag Truncation (`truncate_to_game_time`)**:
    - When Riot's Live Client Data API detects that `gameTime == last_game_time` (elapsed >= 0.45s), an in-game pause is triggered.
    - Because API polling takes ~450ms to confirm the freeze, `TimelineWavWriter.truncate_to_game_time(pause_game_time)` seeks both Discord and microphone streams back to `44 + target_frame * frame_size` and truncates the files in-place, permanently slicing out the detection lag audio.
    - When the game resumes (`gameTime > last_game_time`), audio recording resumes sequentially without any time drift relative to the League match clock.
  - Match end: When the game client closes, the request raises `RequestException`, which triggers `stop_recording_and_rename()`.
  - **Hands-Free Match Auto-Stop**: When the scrim finishes and `<GameID>.mp3` is saved, the watcher automatically signals completion, exits the listening loop, and resets the UI button back to `Start Recording Watcher` without requiring manual intervention.
- **Game ID Extraction**:
  - Uses `find_league_install_dir()` (prioritizing fast `psutil` lookup of `LeagueClientUx`, falling back to PowerShell and common drive paths).
  - Reads `lockfile` (`<name>:<pid>:<port>:<password>:<protocol>`).
  - Authenticates with Basic Auth `riot:<password>` to query `https://127.0.0.1:{port}/lol-gameflow/v1/session` to obtain `gameData.gameId`.
- **Native WASAPI Capture (`native_audio.py`)**:
  - Captures Discord voice chat directly via Windows WASAPI Process Loopback.
  - Captures player microphone via native WASAPI (pyaudiowpatch) with dynamic input device selection and live VU visualizers.
  - Tracks Discord mute & deafen state in real time via local IPC (\\.\pipe\discord-ipc-*). When muted, feeds synchronized silence bytes to keep timeline aligned.
  - Automatically mixes and normalizes both streams into sample-accurate <GameID>.mp3.

### 3.3 `play_replay.py` (`ReplaySynchronizer`)
Responsible for detecting opened `.rofl` replays and synchronizing audio playback in VLC.
- **Replay Process Detection (`detect_running_rofl_file`)**:
  - Scans active processes using `psutil.process_iter(["name", "cmdline"])`.
  - Looks for `League of Legends.exe` containing command-line arguments ending in `.rofl`.
- **Game ID Parsing (`parse_rofl_game_id`)**:
  - Parses replay filenames such as `EUW1-8018190134.rofl` or `8018190134.rofl` to extract `8018190134`.
  - Matches the file directly to `recordings/8018190134.mp3`.
- **Volume Control & Mute Hotkey (`set_volume`, `set_mute`, `toggle_mute`)**:
  - Exposes dynamic volume control (0% to 100%) controllable via the UI slider.
  - Automatically updates VLC player volume in real-time (`player.audio_set_volume(self.volume)`).
  - Preserves user preference in `REPLAY_VOLUME` (`.env` & `config.json`).
  - **Press `M` Hotkey**: Allows toggling replay voice mute directly by pressing `M` (both within the League replay client and in the desktop app), debounced and foreground-window guarded to prevent interfering with typing in external apps.
- **Replay Playback API Polling (`run_sync_loop`)**:
  - Polls `https://127.0.0.1:2999/replay/playback` every 50ms.
  - JSON payload fields:
    - `time`: Current replay playback time in seconds (float).
    - `paused`: Boolean flag indicating if replay is currently paused.
    - `speed`: Playback rate multiplier (e.g. 0.5, 1.0, 2.0, 4.0).
  - Sync actions:
    - Rate change: Calls `player.set_rate(replay_speed)`.
    - Zero-Latency Unpause: If unpausing at the same timestamp (`abs(game_time - paused_game_time) < 0.25`), calls `player.play()` and `player.set_rate(replay_speed)` immediately without issuing a pipeline-flushing `set_time()` seek.
    - Large Drift & Scrub Seeking: For timeline scrubs or large drift (>1.0s), seeks with an 800ms cooldown to permanently eliminate seek storms caused by VLC asynchronous time reporting.
    - Rewind handling: If `player.get_state() == vlc.State.Ended`, automatically reloads media before seeking.
    - Pause: If `paused == True` or `game_time <= 0.5`, calls `player.pause()`.
  - Auto-exit: If the Replay API fails 8 consecutive times (replay closed), it stops VLC and returns to the watcher loop.

### 3.4 Configuration & Storage (`config.py`, `.env`, `config.json`)
- **Configuration Storage**:
  - `config.json` is stored cleanly in standard Windows `Documents/League Voice Recorder/config.json` alongside `recordings/`.
  - Automatically migrates existing configuration files to the Documents directory on first run.
  - Zero third-party cloud/OneDrive redirection dependencies.
- **Environment & Secrets (`.env` and `.env.example`)**:
  - Configuration values are safely stored in `.env` and `config.json`.
  - `.env.example` provides a tracked template for new setups with placeholder values.
  - `.env` and `config.json` are both excluded in `.gitignore` to prevent credential exposure.
- **Config Manager (`config.py`)**:
  - Dynamically loads `.env` variables via `python-dotenv`.
  - Falls back to `config.json` or built-in defaults.
  - Exposes `load_config()`, `save_config(dict)` (which keeps `.env` and `config.json` synchronized), and `get_ffmpeg_path()`.
- **Git Ignore Policy (`.gitignore`)**:
  - Ignores secrets (`.env`, `config.json`).
  - Ignores large local binaries (`ffmpeg.exe`, `redist/` installer).
  - Ignores recordings (`recordings/*`, preserving `.gitkeep`).

### 3.5 Internationalization & Community Translations (`i18n.py` & `locales/`)
- **Default Language**: English (`en.json`) is the default language.
- **Built-in Translations**:
  - English (`locales/en.json`)
  - Hungarian (`locales/hu.json`)
- **Extensible Architecture for Community Contributions**:
  - Anyone can contribute a new translation simply by adding a new `<code_name>.json` file inside `locales/` (e.g. `locales/de.json`, `locales/fr.json`, `locales/es.json`).
  - Each JSON file should define `"__language_name__": "<Native Name>"` (e.g., `"Deutsch"`, `"Français"`, `"Español"`).
  - The application automatically scans the `locales/` directory on startup and populates the **Interface Language** dropdown menu in Settings with all detected languages—**no Python code modifications required**.
- **Dynamic Language Switching**:
  - Changing the language dropdown immediately translates all widgets, status pills, health badges, button labels, and tabs in real-time via `on_language_change` listeners.
  - Automatically persists the language preference to `config.json` and `.env` (`LANGUAGE=<code_name>`).
- **Safe Fallback**:
  - `t(key, **kwargs)` resolves nested dot-notated keys (e.g. `record.start_btn`).
  - If a key is missing in a community translation, it automatically falls back to English (`en.json`). If missing in both, it returns the raw key.

### 3.6 Automatic Updates & Packaging (`updater.py`)
- **GitHub Releases Integration**:
  - Checks GitHub Releases API for newer semantic version tags (`check_for_update`).
  - Prioritizes `.zip` release distribution packages (`LeagueVoiceRecorder-Windows-x64.zip`), falling back to standalone `.exe` binaries.
- **Stream Download with Progress Callback**:
  - Downloads release assets into a temporary directory while reporting real-time percentage progress (0–100%) to the UI.
- **Distribution Package Unpacking**:
  - Uses standard library `zipfile` to unpack archives and find the target executable and updated dependencies (DLLs, `locales/`, assets).
- **Silent Background In-Place Replacement**:
  - In compiled Windows binary mode: spawns a detached background updater script (`cmd.exe /c updater.bat` with `CREATE_NO_WINDOW`) that waits for the active application process to exit, copies all updated files cleanly via `robocopy` into the app directory, relaunches `LeagueVoiceRecorder.exe`, and cleans up temporary archives.
  - In development mode: returns extracted target directory without modifying the local Python runtime environment.

---

## 4. League of Legends APIs Reference

| API | URL / Protocol | Authentication | Purpose |
| :--- | :--- | :--- | :--- |
| **Live Client Data API** | `https://127.0.0.1:2999/liveclientdata/allgamedata` | None (SSL verify=False) | Read `gameData.gameTime` during live games |
| **Replay Playback API** | `https://127.0.0.1:2999/replay/playback` | None (SSL verify=False) | Read `time`, `paused`, `speed` during replay (requires `EnableReplayApi=1` in `Config\game.cfg`) |
| **LCU Gameflow API** | `https://127.0.0.1:{port}/lol-gameflow/v1/session` | Basic Auth (`riot:<lockfile_pw>`) | Retrieve real match `gameId` from client |

> [!NOTE]
> Port `2999` uses a self-signed Riot Games SSL certificate. All HTTP requests must set `verify=False` and disable `urllib3` warnings.

---

## 5. Development & Execution Guide

### 5.1 Environment & Package Manager
The project uses `uv` for Python virtual environment and dependency management.

```powershell
# Run the UI application
uv run main.py

# Or run via launcher script
.\scripts\start_ui.bat

# Run the recorder standalone (CLI)
uv run python -m app.record

# Run the replay synchronizer standalone (CLI)
uv run python -m app.play_replay
```

### 5.2 Dependency Management
Dependencies are defined in `pyproject.toml` and locked in `uv.lock`. `requirements.txt` is exported via:
```powershell
uv export --no-dev --no-hashes -o requirements.txt
```

Core libraries:
- `customtkinter`: Modern dark-themed UI components.
- `psutil`: High-performance cross-platform process management.
- `python-vlc`: libvlc bindings for audio playback and rate synchronization.
- `requests` & `urllib3`: HTTPS communication with League local APIs.

### 5.3 Automated Testing & Code Quality
The project includes a comprehensive suite of 70 unit tests covering configuration, Discord Local IPC mute/deafen sync (auto-skipped when Discord is offline), microphone input enumeration and selection, i18n key parity and translations, recorder sample-accurate audio alignment, pause detection WAV truncation lag trimming, replay protection, replay synchronizer zero-latency unpausing, seek cooldown protection, game ID parsing, volume clamping and hotkey mute, automatic release checks, zip extraction, in-place binary updater scripts, and headless CustomTkinter UI rendering.

```powershell
# Run the test suite via the Windows batch launcher
.\scripts\run_tests.bat

# Or run tests directly with uv (Pytest)
uv run pytest

# Run the linter
uvx ruff check .
```

### 5.4 Standalone Executable & Installer Packaging
The application is compiled into a standalone Windows binary distribution using `Nuitka` in `--standalone` mode and packaged into an installer via `Inno Setup`.
- Built via batch script: `.\scripts\build_exe.bat`
- Output artifacts in `dist/`:
  - `dist/LeagueVoiceRecorder/`: Standalone folder containing `LeagueVoiceRecorder.exe`, Python runtime DLLs, dependency libraries, and `locales/`.
  - `dist/LeagueVoiceRecorder-Setup-v*.exe`: 1-click Inno Setup Windows installer wizard with dual-language support (English/Hungarian), desktop & Start Menu shortcuts, per-user `%LOCALAPPDATA%` installation, and automatic uninstaller.
- Automatically cleans up `dist/main.build` after compilation to conserve disk space.
- Automatically creates `config.json` next to the binary on first run.

### 5.5 Autonomous Performance Profiling & Optimization with GradeX
The project utilizes **[GradeX](https://github.com/betterix/gradex)** for continuous AI-driven performance profiling, hot-path discovery, and latency benchmark regression tracking.

- **Benchmark Suite (`.gradex/`)**: Contains executable benchmark harnesses (such as `.gradex/benchmark.py`) that profile critical loops including configuration loading, process polling, and audio buffer math.
- **Key Measured Optimizations**:
  - `load_config()`: In-memory cache with file modification time (`mtime`) checks achieves `0.01ms` latency (95% speedup).
  - `is_league_game_running()`: Cached PID check via `psutil.pid_exists()` reduces CPU time by 99.7%.
  - `scale_vu()` & Audio Peak Math: Vectorized extrema (`max(floats.max(), -floats.min())`) avoid buffer copies, delivering a 36% speedup.
- **GradeX CLI Commands**:
  ```powershell
  # Discover latency targets and capture baseline benchmark score
  uv run gradex discover

  # Run an autonomous optimization loop on current hot paths
  uv run gradex optimize

  # Display run statistics, percentile distributions, and timing improvements
  uv run gradex stats

  # Export a shareable HTML performance report
  uv run gradex report
  ```
- **Agent Guidelines for Performance**:
  - Before and after modifying tight loops in `native_audio.py`, `record.py`, or `config.py`, verify that changes do not regress baseline latency.
  - Keep all `.gradex/` benchmark and cache artifacts untracked (enforced via `.gitignore`).

---

## 6. Key Invariants & Design Rules

1. **Thread Safety**: Never call Tkinter/CustomTkinter widget manipulation methods directly from background worker threads. Always post updates through `self.msg_queue` and consume them in `_process_queue` on the main GUI thread.
2. **CLI Executability**: `app/record.py` and `app/play_replay.py` remain directly executable from the command line (`if __name__ == "__main__":` via `python -m app.record` and `python -m app.play_replay`). Never remove standalone support when adding UI capabilities.
3. **Recording Safety**: When stopping the recorder (either via the UI button or on application close), if an active recording is in progress, `stop_recording_and_rename()` must be called to ensure the recording is finalized and converted to MP3.
4. **VLC Initialization**: On 64-bit Windows, VLC requires explicit DLL directory registration before importing `vlc`. Always configure `os.add_dll_directory(VLC_DIR)` and ensure fallback audio output drivers (`--quiet`, `--quiet --aout=mmdevice`, `--quiet --aout=directsound`) are handled.
5. **Fast Process Lookup**: Always use `psutil` before falling back to PowerShell subprocess calls for detecting `LeagueClientUx.exe` and `League of Legends.exe`. `psutil` executes in single-digit milliseconds without spawning console windows.


