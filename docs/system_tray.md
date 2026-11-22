# System Tray & Background Notifications

League Voice Recorder is built to operate completely hands-free. Players can minimize the application to the Windows System Notification Area (System Tray) where it quietly monitors for League matches and replays with minimal system footprint.

---

## 1. Lifecycle & Minimization Behavior

League Voice Recorder cleanly distinguishes between closing and minimizing:
- **Close Button (`✕`)**: Completely terminates and exits the application (`app.exit(0)`), closing all windows, stopping background workers, and removing tray icons.
- **Minimize Button (`_`)**: When the user clicks the titlebar minimize button (and `minimize_to_tray` is enabled in Settings):
  1. **Window Cloaking**: The window is hidden from the Windows Taskbar and Alt-Tab switcher (`win.hide()`).
  2. **Tray Presence**: The application continues running silently in the Windows system tray notification area.
  3. **Audio Announcement**:
     - Plays a native Windows system chime via Win32 `MessageBeep(0x00000040)` (`MB_ICONASTERISK`).
  4. **Desktop Toast Notification**:
     - Displays a dedicated floating Hextech toast banner (`toast.html`) in the bottom-right corner directly above the system tray.
     - Shows the application logo, title (`League Voice Recorder`), and description (*"Running in background. Click tray icon to restore."*).
     - Clicking the notification instantly unminimizes and restores the main application window.
     - Automatically auto-dismisses after 3.5 seconds.

---

## 2. Dynamic Tooltip & Context Menu

The tray icon continuously updates its hover tooltip and provides a native Windows context menu:

### 2.1 Dynamic Tooltip States
- **IDLE**: `League Voice Recorder — Idle (Watching)`
- **RECORDING**: `League Voice Recorder — REC (12:45) - 8052864504`
- **PAUSED**: `League Voice Recorder — PAUSED (12:45)`
- **REPLAY SYNC**: `League Voice Recorder — SYNC (08:20) - 8052864504`

### 2.2 Context Menu Options
Right-clicking the tray icon opens the context menu:
- **Show App**: Restores and focuses the main desktop window.
- **Recording Status**: Displays the current match state, active Game ID, and clock.
- **Quit**: Safely flushes in-flight audio buffers, terminates background threads, and exits the process.

---

## 3. Left-Click Quick Toggle

Left-clicking the system tray icon performs an instant visibility toggle:
- If hidden: calls `win.show()` and `win.set_focus()`.
- If visible: calls `win.hide()`.
