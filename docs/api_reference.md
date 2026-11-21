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

### `set_overlay_preview`
Toggles preview mode for the floating in-game HUD.
- **Parameters**:
  - `mode`: `String` (`"record"`, `"replay"`, or `"off"`)
- **Return**: `Result<(), String>`

### `set_overlay_locked`
Toggles click-through mode for the overlay.
- **Parameters**:
  - `locked`: `bool`
- **Return**: `Result<(), String>`
- **Description**: When `locked == true`, activates `WS_EX_TRANSPARENT` via Win32 API.

### `reset_overlay_position`
Resets the HUD overlay position back to default coordinates (`x = 100, y = 100`).
- **Parameters**: None
- **Return**: `Result<(), String>`
