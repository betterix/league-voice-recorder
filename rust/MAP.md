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
| `config` | `app/config.py` | `src/config/mod.rs`, `src/config/secret.rs` | `tests/test_config.py` | **Claimed / In-Progress** (secret roundtrip & validation passing) |
| `replay_config` | `app/replay_config.py` | `src/replay/config.rs` | `tests/test_replay_config.py` | **Claimed / In-Progress** (EnableReplayApi=1 injection test passing) |
| `i18n` | `app/i18n.py` | `src/i18n/mod.rs` | `tests/test_i18n.py` | **Open** |
| `scrim_meta` | `app/scrim_meta.py` | `src/scrim_meta/id3.rs` | `tests/test_scrim_meta.py` | **Claimed / In-Progress** (ID3 tagging implemented) |

### Wave 1: Process Inspection & Live APIs
| Unit | Python Source | Rust Target | Tests | Status |
|---|---|---|---|---|
| `lcu_lockfile` | `app/record.py` (lockfile/session) | `src/lcu/lockfile.rs`, `src/lcu/live_api.rs` | `tests/test_recorder.py` | **Claimed / In-Progress** |
| `replay_process` | `app/play_replay.py` (.rofl detection) | `src/replay/process.rs`, `src/replay/api.rs` | `tests/test_replay_sync.py` | **Claimed / In-Progress** (Game ID regex parsing tests passing) |
| `discord_ipc` | `app/discord_ipc.py` | `src/discord_ipc/mod.rs` | `tests/test_discord_ipc.py` | **Open** |

### Wave 2: Audio Engines & Sinks
| Unit | Python Source | Rust Target | Tests | Status |
|---|---|---|---|---|
| `audio_timeline` | `app/native_audio.py` | `src/audio/timeline.rs`, `src/audio/encoder.rs` | `tests/test_timing_accuracy.py` | **Claimed / In-Progress** (pause lag truncation & MP3 test passing) |
| `audio_capture` | `app/native_audio.py` | `src/audio/capture.rs` | `tests/test_recorder.py` | **Claimed / In-Progress** (device enum & loopback tests passing) |
| `record_engine` | `app/record.py` | `src/record/engine.rs` | `tests/test_recorder.py` | **Claimed / In-Progress** (record loop & MP3 save test passing) |
| `replay_engine` | `app/play_replay.py` | `src/replay/engine.rs`, `src/replay/player.rs` | `tests/test_replay_sync.py` | **Claimed / In-Progress** |

### Wave 3: Network & System Integration
| Unit | Python Source | Rust Target | Tests | Status |
|---|---|---|---|---|
| `cloud_sync` | `app/cloud_sync.py` | `src/cloud_sync/mod.rs` | `tests/test_cloud_sync.py` | **Open** |
| `tray` | `app/tray.py` | `src/tray/mod.rs` | `tests/test_tray.py` | **Open** |
| `updater` | `app/updater.py` | `src/updater/mod.rs` | `tests/test_updater.py` | **Open** |
| `overlay` | `app/overlay.py` | `src/overlay/mod.rs` | `tests/test_overlay.py` | **Open** |

### Wave 4: Desktop UI & Coordinator
| Unit | Python Source | Rust Target | Tests | Status |
|---|---|---|---|---|
| `ui_coordinator` | `main.py` | `src/main.rs`, `ui/` | `tests/test_ui.py` | **Claimed / In-Progress** (2-card layout, audio VU meters, health ribbon, activity log) |

---

## Open Questions & Grilling Frontier
See conversation grilling round below.
