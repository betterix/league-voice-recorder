# GLOSSARY: League Voice Recorder

| Domain Concept | Python Original | Rust Port (Target) | Description |
|---|---|---|---|
| Game ID | `game_id: str` | `GameId(String)` / `&str` | Riot match identifier extracted from LCU or ROFL replay file (e.g. `8018190134` or `EUW1-8018190134`). |
| LCU Lockfile | `lockfile` parsing (`find_league_install_dir`) | `lcu::lockfile::LcuCredentials` | Riot Client lockfile credentials (`riot:<password>` on port). |
| Live Client Data | `requests.Session` polling `127.0.0.1:2999` | `lcu::live_api` via `reqwest` | Polling League in-game state (`/liveclientdata/allgamedata`). |
| Replay Playback API | `127.0.0.1:2999/replay/playback` | `replay::api` via `reqwest` | In-game replay playback clock, paused flag, and speed multiplier. |
| ROFL Replay | `psutil` process inspecting `League of Legends.exe` | `replay::process::RoflProcessDetector` | Detection of active `.rofl` replay process and extraction of Game ID. |
| WASAPI Loopback | `pyaudiowpatch` process loopback | `audio::capture::AudioCaptureManager` via `cpal` | Hardware WASAPI loopback audio capture from Discord and mic. |
| Pause Lag Truncation | `truncate_to_game_time` | `audio::timeline::TimelineWavWriter::truncate_to_game_time` | Slices detection lag audio in-place when match pauses to eliminate time drift. |
| Replay Auto-Sync | `ReplaySynchronizer` + VLC engine | `replay::engine::ReplaySynchronizer` + `RodioPlayer` | Continuous sync of recorded audio with the League replay playback clock. |
| Replay Config | `app/replay_config.py` | `replay::config::ReplayConfigManager` | Safe backup and injection of `EnableReplayApi=1` into `game.cfg`. |
| Scrim Metadata | `app/scrim_meta.py` | `scrim_meta::id3` via `id3` crate | ID3v2 metadata embedding into recorded scrim MP3 files. |
| Secret Encryption | Windows DPAPI / CryptProtectData | `config::secret::SecretStorage` via DPAPI / AES-GCM-SIV | Disk encryption for sensitive API keys. |
| Floating Overlay | `app/overlay.py` (Tkinter borderless HUD) | Webview overlay or Tauri HUD window | Translucent in-game HUD with `WS_EX_TRANSPARENT` click-through. |
