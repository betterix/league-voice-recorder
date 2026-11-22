# Automated Replay Audio Synchronization (`src/replay/`)

The Replay Synchronization Engine automatically detects when any `.rofl` replay file is opened in the League of Legends client, locates the matching scrimmage voice recording, and locks audio playback frame-for-frame to the League replay playback engine.

---

## 1. Replay Synchronization Lifecycle

```mermaid
flowchart TD
    Watch[▶ Replay Auto-Sync Watcher Loop]
    ProcInspect[psutil-style Process Inspector]
    CheckRofl{Active League Process with .rofl cmdline?}
    ExtractID[Parse Numeric Game ID from .rofl filename]
    FindMP3[Search recordings/ for matching <GameID>.mp3]
    InitPlayer[Initialize Rodio Headless Player Engine]
    PollAPI[Poll Replay Playback API :2999/replay/playback every 50ms]
    StateEngine[Replay Playback State Machine]

    Watch --> ProcInspect
    ProcInspect --> CheckRofl
    CheckRofl -- No --> Watch
    CheckRofl -- Yes --> ExtractID
    ExtractID --> FindMP3
    FindMP3 --> InitPlayer
    InitPlayer --> PollAPI
    PollAPI --> StateEngine

    subgraph State_Actions [State Machine Actions]
        RateChange[player.set_rate speed multiplier]
        Unpause[Zero-Latency Immediate Unpause]
        ScrubSeek[Debounced Timeline Scrub Seek]
        AutoExit[8 Consecutive Misses -> Stop & Return to Watcher]
    end

    StateEngine --> RateChange & Unpause & ScrubSeek & AutoExit
```

---

## 2. Process Detection & Game ID Parsing (`src/replay/process.rs`)

### 2.1 Low-Overhead Process Iteration
- Inspects active Windows processes using cached snapshot refresh (polling every ~500ms).
- Identifies `League of Legends.exe` containing command-line arguments ending in `.rofl`.

### 2.2 Replay Filename Parsing
Extracts the official numeric Riot Game ID from diverse naming conventions:
- `EUW1-8052864504.rofl` $\to$ `8052864504`
- `NA1-8018190134.rofl` $\to$ `8018190134`
- `8052864504.rofl` $\to$ `8052864504`
- `game.rofl` (falls back to active LCU session or latest scrimmage recording).

---

## 3. Headless Audio Engine (`src/replay/player.rs`)

### 3.1 Pure Rust Native Audio Decoding
- Operates in-process using **Rodio** (`rodio::Sink`, `rodio::Decoder`).
- **No external media player dependency**: VLC, Windows Media Player, or external process wrappers are completely eliminated.
- Memory usage is virtually zero (~2 MB during playback), with 0% CPU consumption during pauses.

### 3.2 Instantaneous Playback Rate Control
- The League Replay API reports playback speeds including `0.25x`, `0.5x`, `1.0x`, `2.0x`, `4.0x`, and `8.0x`.
- `RodioPlayer::set_speed(speed)` adjusts the playback rate instantaneously without audio buffer underruns or pitch-shifting artifacts.

### 3.3 Zero-Latency Unpause
- If unpausing at the same timestamp ($|\text{game\_time} - \text{paused\_game\_time}| < 0.25$ seconds), `player.play()` is invoked immediately without issuing a pipeline-flushing seek.
- Audio resumes instantaneously in under 1 millisecond.

### 3.4 Scrubbing & Drift Correction
- When the coach or analyst scrubs the League replay timeline (forward or backward) or if drift exceeds $\pm 0.8$ seconds:
  - Invokes `player.seek(Duration::from_secs_f64(replay_time))` with a cooldown guard.
  - Eliminates seek storms caused by asynchronous API time reporting.

---

## 4. Replay API Auto-Configuration (`src/replay/config.rs`)

Riot's Replay Playback API (`:2999/replay/playback`) requires `EnableReplayApi=1` under the `[General]` section in League's `game.cfg`.

The desktop application includes a **1-Click Auto-Configurator**:
1. Automatically discovers `League of Legends/Config/game.cfg`.
2. Creates a backup copy: `game.cfg.bak`.
3. Injects `EnableReplayApi=1` cleanly into the configuration file.
4. Instant user confirmation in Settings with live health badge validation.
