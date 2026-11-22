# IPC & Tauri Command Reference

League Voice Recorder uses Tauri v2's type-safe inter-process communication (IPC) channel. All communication between the frontend webview and the native Rust backend is executed through asynchronously invoked Tauri commands.

---

## 1. Engine Control Commands

### `start_recording_watcher`
Starts the live match watcher thread.
- **Parameters**: None
- **Return**: `Result<(), String>`
- **Description**: Begins polling Riot's Live Client Data API (`:2999`) and LCU Gameflow API. Automatically initiates WASAPI capture on match start.

### `stop_recording_watcher`
Stops the live match watcher.
- **Parameters**: None
- **Return**: `Result<(), String>`
- **Description**: Stops polling and finalizes any active match recording in progress.

### `start_replay_watcher`
Starts the replay synchronization watcher.
- **Parameters**: None
- **Return**: `Result<(), String>`
- **Description**: Begins inspecting processes for running `League of Legends.exe` instances with `.rofl` command lines.

### `stop_replay_watcher`
Stops the replay synchronization watcher.
- **Parameters**: None
- **Return**: `Result<(), String>`
- **Description**: Stops replay process inspection and halts any active audio playback.

---

## 2. Replay Playback Commands

### `set_replay_volume`
Adjusts the replay audio volume in real-time.
- **Parameters**:
  - `volume`: `u8` (0 to 100)
- **Return**: `Result<(), String>`
- **Description**: Directly updates the Rodio audio sink volume (`volume / 100.0`) and updates `replay_volume` in `config.json`.

### `toggle_replay_mute`
Toggles audio mute during replay playback.
- **Parameters**: None
- **Return**: `Result<bool, String>` (Returns `true` if muted, `false` if unmuted)

---

## 3. Configuration & Diagnostics Commands

### `get_config`
Loads the current application configuration.
- **Parameters**: None
- **Return**: `Result<ConfigDto, String>`

### `save_config`
Saves updated configuration preferences to `Documents/League Voice Recorder/config.json`.
- **Parameters**:
  - `config`: `ConfigDto`
- **Return**: `Result<(), String>`

### `get_audio_devices`
Discovers all available WASAPI input and output devices.
- **Parameters**: None
- **Return**: `Result<AudioDevicesDto, String>`
- **Fields**:
  - `input_devices`: `Vec<String>` (Microphones)
  - `output_devices`: `Vec<String>` (Loopback devices)

### `get_system_health`
Returns live status diagnostics for all core subsystems.
- **Parameters**: None
- **Return**: `SystemHealthDto`
- **Fields**:
  - `discord_connected`: `bool`
  - `league_running`: `bool`
  - `audio_engine_ready`: `bool`
  - `lcu_connected`: `bool`

---

## 4. Floating HUD Commands

### `overlay_preview_rec`
Activates live recorder HUD simulation in the overlay window.
- **Parameters**: None
- **Return**: `Result<(), String>`

### `overlay_preview_replay`
Activates replay timeline waveform simulation in the overlay window.
- **Parameters**: None
- **Return**: `Result<(), String>`

### `overlay_hide_preview`
Hides any active preview simulation in the HUD.
- **Parameters**: None
- **Return**: `Result<(), String>`

### `overlay_set_locked`
Toggles click-through mode for the overlay.
- **Parameters**:
  - `locked`: `bool`
- **Return**: `Result<(), String>`
- **Description**: When `locked == true`, activates `WS_EX_TRANSPARENT` via Win32 API.

### `overlay_set_opacity`
Adjusts global composite opacity for the floating HUD overlay.
- **Parameters**:
  - `opacity`: `f64` (0.40 to 1.00)
- **Return**: `Result<(), String>`

### `overlay_reset_position`
Resets the HUD overlay position back to default coordinates (`x = 100, y = 100`).
- **Parameters**: None
- **Return**: `Result<(), String>`

### `overlay_start_dragging`
Hooks into Windows OS window dragging for buttery-smooth movement across monitors.
- **Parameters**: None
- **Return**: `Result<(), String>`

---

## 5. Recordings Library Commands

### `get_recordings`
Scans `record_folder` and returns all indexed scrimmage `.mp3` files with duration and metadata.
- **Parameters**: None
- **Return**: `Result<Vec<RecordingDto>, String>`

### `save_recording_metadata`
Updates ID3 metadata tags (Game Patch, Scrim Title, Team Notes) for a specific recording.
- **Parameters**:
  - `game_id`: `String`
  - `metadata`: `RecordingMetadataDto`
- **Return**: `Result<(), String>`

### `open_recordings_folder`
Opens the local recordings directory in Windows File Explorer.
- **Parameters**: None
- **Return**: `Result<(), String>`

### `play_recording`
Launches the selected recording in the system default or configured media player.
- **Parameters**:
  - `game_id`: `String`
- **Return**: `Result<(), String>`

### `delete_recording`
Permanently deletes a scrimmage recording file from disk.
- **Parameters**:
  - `game_id`: `String`
- **Return**: `Result<(), String>`

---

## 6. Remote Scrim Cloud Sync Commands

### `test_cloud_connection`
Validates the backend URL and DPAPI-decrypted API credentials.
- **Parameters**: None
- **Return**: `Result<CloudHealthDto, String>`

### `cloud_get_role`
Retrieves the effective RBAC permission role (`admin`, `upload`, or `pull`) for the configured token.
- **Parameters**: None
- **Return**: `Result<String, String>`

### `cloud_list_recordings`
Fetches the catalog of team scrimmage recordings stored on the remote server.
- **Parameters**: None
- **Return**: `Result<Vec<CloudRecordingDto>, String>`

### `cloud_download_recording`
Streams a remote recording to the local `recordings/` directory with SHA-256 verification.
- **Parameters**:
  - `game_id`: `String`
- **Return**: `Result<(), String>`

### `cloud_upload_recording`
Uploads a local scrimmage recording with ID3 tags to the team server.
- **Parameters**:
  - `game_id`: `String`
- **Return**: `Result<(), String>`

### `cloud_delete_recording`
Deletes a recording from the team server (`admin` role required).
- **Parameters**:
  - `game_id`: `String`
- **Return**: `Result<(), String>`

---

## 7. Localization & Updates

### `get_available_languages`
Lists all supported interface languages.
- **Parameters**: None
- **Return**: `Vec<LanguageInfoDto>`

### `get_translations`
Returns the translation dictionary for the requested language code (`en`, `hu`).
- **Parameters**:
  - `lang`: `String`
- **Return**: `Result<Value, String>`

### `check_for_updates`
Checks the GitHub releases API for newer application builds.
- **Parameters**: None
- **Return**: `Result<UpdateCheckDto, String>`
