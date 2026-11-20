# Migration Port Map: League Voice Recorder

**Goal**: Full 1:1 migration of League Voice Recorder from Python to Rust, preserving exact UI appearance and feature parity, verified unit by unit against the Python reference implementation.

## Decisions Index
- [ADR-001: Maintain 1:1 Parity with Python Desktop App](DECISIONS.md#adr-001-maintain-11-parity-with-python-desktop-app)
- [ADR-002: GUI Framework Selection (Tauri 2 vs Native)](DECISIONS.md#adr-002-gui-framework-selection-tauri-2-vs-native)
- [ADR-003: Pure Rust Audio Playback Engine (Rodio vs LibVLC)](DECISIONS.md#adr-003-pure-rust-audio-playback-engine-rodio-vs-libvlc)
- [ADR-004: Audio Capture via WASAPI and CPAL](DECISIONS.md#adr-004-audio-capture-via-wasapi-and-cpal)
- [ADR-005: Strict Git Branching and Remote Push Policy](DECISIONS.md#adr-005-strict-git-branching-and-remote-push-policy)

---

## Migration Waves & Units

### Wave 0: Core Foundation & Configuration
| Unit | Python Source | Rust Target | Tests | Status |
|---|---|---|---|---|
| `config` | `app/config.py` | `src/config/mod.rs`, `src/config/secret.rs` | `tests/test_config.py` | **Done** (DPAPI/PBKDF2/legacy secret roundtrip & config path override verified) |
| `replay_config` | `app/replay_config.py` | `src/replay/config.rs` | `tests/test_replay_config.py` | **Done** (EnableReplayApi=1 injection & backup verified) |
| `i18n` | `app/i18n.py` | `src/i18n/mod.rs` | `tests/test_i18n.py` | **Done** (Dynamic locale scanning, fallback, and placeholder formatting verified) |
| `scrim_meta` | `app/scrim_meta.py` | `src/scrim_meta/id3.rs` | `tests/test_scrim_meta.py` | **Done** (ID3v23 tag roundtrip read/write verified) |

### Wave 1: Process Inspection & Live APIs
| Unit | Python Source | Rust Target | Tests | Status |
|---|---|---|---|---|
| `lcu_lockfile` | `app/record.py` (lockfile/session) | `src/lcu/lockfile.rs`, `src/lcu/live_api.rs` | `tests/test_recorder.py` | **Done** (Lockfile parsing & gameflow extraction verified) |
| `replay_process` | `app/play_replay.py` (.rofl detection) | `src/replay/process.rs`, `src/replay/api.rs` | `tests/test_replay_sync.py` | **Done** (Game ID regex parsing & process inspection verified) |
| `discord_ipc` | `app/discord_ipc.py` | `src/discord_ipc/mod.rs` | `tests/test_discord_ipc.py` | **Done** (IPC binary packet codec & mute/deaf event tracking verified) |

### Wave 2: Audio Engines & Sinks
| Unit | Python Source | Rust Target | Tests | Status |
|---|---|---|---|---|
| `audio_timeline` | `app/native_audio.py` | `src/audio/timeline.rs`, `src/audio/encoder.rs` | `tests/test_timing_accuracy.py` | **Done** (Sample-accurate pause lag truncation & MP3 encoding verified) |
| `audio_capture` | `app/native_audio.py` | `src/audio/capture.rs` | `tests/test_recorder.py` | **Done** (Device enumeration & loopback audio capture verified) |
| `record_engine` | `app/record.py` | `src/record/engine.rs` | `tests/test_recorder.py` | **Done** (Live client poller, replay guard, and MP3 export verified) |
| `replay_engine` | `app/play_replay.py` | `src/replay/engine.rs`, `src/replay/player.rs` | `tests/test_replay_sync.py` | **Done** (Rodio pure Rust player, drift seek, and speed sync verified) |

### Wave 3: Network & System Integration
| Unit | Python Source | Rust Target | Tests | Status |
|---|---|---|---|---|
| `cloud_sync` | `app/cloud_sync.py` | `src/cloud_sync/mod.rs` | `tests/test_cloud_sync.py` | **Done** (FastAPI health checks, SHA256 checksums, and list verified) |
| `tray` | `app/tray.py` | `src/tray/mod.rs` | `tests/test_tray.py` | **Done** (Tray state, dynamic tooltip, and minimize-to-tray verified) |
| `updater` | `app/updater.py` | `src/updater/mod.rs` | `tests/test_updater.py` | **Done** (Semver tuple parser and GitHub release query verified) |
| `overlay` | `app/overlay.py` | `src/overlay/mod.rs` | `tests/test_overlay.py` | **Done** (HUD overlay state, WS_EX_TRANSPARENT click-through verified) |

### Wave 4: Desktop UI & Coordinator
| Unit | Python Source | Rust Target | Tests | Status |
|---|---|---|---|---|
| `ui_coordinator` | `main.py` | `src/main.rs`, `ui/` | `tests/test_ui.py` | **Claimed / In-Progress** (2-card layout, audio VU meters, health ribbon, activity log) |

---

## Open Questions & Grilling Frontier
See conversation grilling round below.
