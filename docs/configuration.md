# Configuration & Storage Architecture

League Voice Recorder stores all settings, device preferences, and cloud credentials cleanly in standard Windows user directories with zero third-party cloud synchronization or registry pollution.

---

## 1. File Locations & Directory Hierarchy

```text
%USERPROFILE%/Documents/League Voice Recorder/
│
├── config.json              # Primary user configuration & preferences
│
├── recordings/              # Output directory for processed scrim recordings
│   ├── 8052864504.mp3       # Final 160kbps studio-grade MP3 scrim recording
│   └── 8052864505.mp3
│
└── .raw_audio/              # Temporary stream-to-disk staging cache
    ├── 8052864504_mic.pcm   # Raw 32-bit float mono mic capture stream
    └── 8052864504_loop.pcm  # Raw 32-bit float stereo Discord loopback stream
```

> [!NOTE]
> The `.raw_audio/` directory is purely a zero-RAM streaming cache. Raw PCM chunks are immediately flushed to disk during recording and permanently cleaned up as soon as the final `<GameID>.mp3` is encoded.

---

## 2. Configuration Schema (`config.json`)

```json
{
  "record_folder": "C:\\Users\\Zsolt\\Documents\\League Voice Recorder\\recordings",
  "vlc_path": "C:\\Program Files\\VideoLAN\\VLC",
  "selected_mic": "Default Microphone (System)",
  "selected_loopback": "Default Output (System)",
  "replay_volume": 85,
  "language": "en",
  "remote_api_url": "https://scrims.myteam.gg",
  "remote_api_key_encrypted": "AQAAANCMnd8BFdERjHoAwE/Cl+sBAAAA...",
  "hud_opacity": 0.85,
  "hud_locked": false,
  "hud_x": 100,
  "hud_y": 100,
  "auto_start_record": true,
  "auto_start_replay": true
}
```

### Key Reference

| Key | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `record_folder` | `String` | `~/Documents/League Voice Recorder/recordings` | Directory where finalized scrim MP3 files are saved. |
| `vlc_path` | `String` | Auto-detected | Path to VLC install directory (used for standalone external playback). |
| `selected_mic` | `String` | `"Default Microphone (System)"` | Name of the active WASAPI microphone recording device. |
| `selected_loopback`| `String` | `"Default Output (System)"` | Name of the WASAPI loopback device capturing Discord voice chat. |
| `replay_volume` | `Integer` | `80` | Default audio volume level (0–100%) during replay reviews. |
| `language` | `String` | `"en"` | Interface language code (e.g. `"en"`, `"hu"`). |
| `remote_api_url` | `String` | `""` | Backend server URL for team scrim cloud synchronization. |
| `remote_api_key_encrypted` | `String` | `""` | Base64 Windows DPAPI-encrypted API authorization key. |
| `hud_opacity` | `Float` | `0.85` | Global composite opacity for the floating in-game HUD overlay (0.40–1.00). |
| `hud_locked` | `Boolean`| `false` | When true, enables click-through mode (`WS_EX_TRANSPARENT`). |
| `hud_x`, `hud_y` | `Integer` | `100, 100` | Saved screen coordinate offsets for the floating HUD window. |
| `auto_start_record`| `Boolean`| `true` | Automatically starts recording when a live match is detected. |
| `auto_start_replay`| `Boolean`| `true` | Automatically synchronizes audio when a `.rofl` replay is launched. |

---

## 3. Audio Device Selection & Fallback

- **Dynamic Discovery**:
  - The application scans all active Windows audio endpoints using native Windows WASAPI APIs.
  - Lists friendly device names (e.g., `SteelSeries Sonar - Microphone`, `Realtek High Definition Audio`).
- **Graceful Fallbacks**:
  - If a previously selected device is unplugged or disconnected, the engine seamlessly falls back to the system's default multimedia capture endpoint without throwing runtime errors or interrupting the app.
