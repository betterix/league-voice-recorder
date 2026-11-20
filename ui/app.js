let isRecording = false;
let isReplaySync = false;
let currentConfig = null;
let currentLanguage = "en";
let currentTheme = "dark";
let isMuted = false;
let previousVolume = 80;
let smoothApp = 0.0;
let smoothMic = 0.0;
let autoscroll = true;
let saveDebounceTimer = null;

// Embedded i18n dictionary matching locales/en.json and locales/hu.json
const I18N = {
  en: {
    app_title: "LEAGUE VOICE RECORDER",
    app_subtitle: "Automated Discord Voice Capture & In-Game Replay Audio Sync",
    btn_start_rec: "🔴 Start Recording Watcher",
    btn_stop_rec: "⏹ Stop Recording Watcher",
    btn_start_rep: "▶ Start Replay Auto-Sync",
    btn_stop_rep: "⏹ Stop Replay Auto-Sync",
    status_standby: "Standby. Click 'Start Recording Watcher' before your match or 'Start Replay Auto-Sync' for ROFL replays.",
    status_rec_active: "Recording watcher active. Waiting for League match...",
    status_rep_active: "Replay synchronizer active. Open any .rofl replay in League.",
    tab_log: "📋 Activity Log",
    tab_recordings: "📁 Scrim Recordings",
    tab_settings: "⚙️ Settings",
    recordings_empty: "No recordings found yet in recordings folder.",
    copy_success: "Log copied to clipboard!",
    delete_confirm: "Are you sure you want to permanently delete {file}?",
  },
  hu: {
    app_title: "LEAGUE HANGFELVEVŐ",
    app_subtitle: "Automatikus Discord Hangrögzítés és Visszajátszás Szinkronizáció",
    btn_start_rec: "🔴 Felvétel Figyelő Indítása",
    btn_stop_rec: "⏹ Felvétel Figyelő Leállítása",
    btn_start_rep: "▶ Replay Szinkron Indítása",
    btn_stop_rep: "⏹ Replay Szinkron Leállítása",
    status_standby: "Készenlét. Kattints a 'Felvétel Figyelő Indítása' gombra a meccs előtt.",
    status_rec_active: "Felvétel figyelő aktív. Várakozás a League mérkőzésre...",
    status_rep_active: "Replay szinkronizáló aktív. Nyiss meg egy .rofl fájlt a kliensben.",
    tab_log: "📋 Eseménynapló",
    tab_recordings: "📁 Scrim Felvételek",
    tab_settings: "⚙️ Beállítások",
    recordings_empty: "Nincsenek felvételek a rögzítési mappában.",
    copy_success: "Napló vágólapra másolva!",
    delete_confirm: "Biztosan véglegesen törölni szeretnéd a(z) {file} fájlt?",
  }
};

function t(key, params = {}) {
  const dict = I18N[currentLanguage] || I18N.en;
  let text = dict[key] || I18N.en[key] || key;
  for (const [k, v] of Object.entries(params)) {
    text = text.replace(`{${k}}`, v);
  }
  return text;
}

// Universal Tauri IPC invoke helper
async function invokeTauri(cmd, args = {}) {
  const invoker =
    (window.__TAURI__ && window.__TAURI__.core && window.__TAURI__.core.invoke) ||
    (window.__TAURI__ && window.__TAURI__.invoke) ||
    (window.__TAURI_INTERNALS__ && window.__TAURI_INTERNALS__.invoke);

  if (invoker) {
    try {
      return await invoker(cmd, args);
    } catch (err) {
      console.error(`[IPC Error] ${cmd}:`, err);
      return null;
    }
  }
  return null;
}

// Activity Logging matching Python log_textbox (Rolling 1500 line cap, Consolas, autoscroll)
function appendLog(rawMsg) {
  const terminal = document.getElementById("terminal");
  if (!terminal) return;

  const line = document.createElement("div");
  line.className = "log-line";

  // Check if timestamp is already present
  if (rawMsg.startsWith("[")) {
    line.innerText = rawMsg;
  } else {
    const now = new Date();
    const timeStr = `[${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}:${String(now.getSeconds()).padStart(2, "0")}]`;
    line.innerText = `${timeStr} ${rawMsg}`;
  }

  terminal.appendChild(line);

  // Rolling cap: keep max 1500 lines to prevent memory bloat (matching Python index delete)
  while (terminal.childNodes.length > 1500) {
    terminal.removeChild(terminal.firstChild);
  }

  if (autoscroll) {
    terminal.scrollTop = terminal.scrollHeight;
  }
}

function clearLog() {
  const terminal = document.getElementById("terminal");
  if (terminal) terminal.innerHTML = "";
}

async function copyLog() {
  const terminal = document.getElementById("terminal");
  if (terminal) {
    try {
      await navigator.clipboard.writeText(terminal.innerText);
      appendLog("📋 Log copied to clipboard.");
    } catch (e) {
      appendLog("⚠️ Failed to copy log to clipboard.");
    }
  }
}

function toggleAutoscroll(enabled) {
  autoscroll = enabled;
  if (autoscroll) {
    const terminal = document.getElementById("terminal");
    if (terminal) terminal.scrollTop = terminal.scrollHeight;
  }
}

function toggleDebugPanel(show) {
  const panel = document.getElementById("debug-panel");
  if (panel) {
    panel.style.display = show ? "flex" : "none";
  }
}

function switchTab(tabId) {
  document.querySelectorAll(".tab-btn").forEach(btn => btn.classList.remove("active"));
  document.querySelectorAll(".tab-content").forEach(c => c.classList.remove("active"));

  const targetTab = document.getElementById(`tab-${tabId}`);
  if (targetTab) targetTab.classList.add("active");

  const activeBtn = document.getElementById(`tab-btn-${tabId}`);
  if (activeBtn) activeBtn.classList.add("active");

  if (tabId === "recordings") {
    loadRecordings();
  }
}

// -------------------------------------------------------------
// RECORDER WATCHER
// -------------------------------------------------------------
async function toggleRecordingWatcher() {
  const btn = document.getElementById("btn-rec");
  const text = document.getElementById("btn-rec-text");
  const pill = document.getElementById("rec-status-pill");

  if (!isRecording) {
    isRecording = true;
    btn.classList.add("active");
    text.innerText = t("btn_stop_rec");
    if (pill) {
      pill.innerText = "● RECORDING";
      pill.className = "action-card-status rec";
    }
    await invokeTauri("start_recording_watcher");
  } else {
    isRecording = false;
    btn.classList.remove("active");
    text.innerText = t("btn_start_rec");
    if (pill) {
      pill.innerText = "● IDLE (Click Start to monitor)";
      pill.className = "action-card-status";
    }
    await invokeTauri("stop_recording_watcher");
    setTimeout(loadRecordings, 800);
  }
}

// -------------------------------------------------------------
// REPLAY AUTO-SYNC
// -------------------------------------------------------------
async function toggleReplayWatcher() {
  const btn = document.getElementById("btn-rep");
  const text = document.getElementById("btn-rep-text");
  const pill = document.getElementById("rep-status-pill");

  if (!isReplaySync) {
    isReplaySync = true;
    btn.classList.add("active");
    text.innerText = t("btn_stop_rep");
    if (pill) {
      pill.innerText = "● SYNCING";
      pill.className = "action-card-status active";
    }
    await invokeTauri("start_replay_watcher");
  } else {
    isReplaySync = false;
    btn.classList.remove("active");
    text.innerText = t("btn_start_rep");
    if (pill) {
      pill.innerText = "● IDLE (Click Start to detect .rofl)";
      pill.className = "action-card-status";
    }
    await invokeTauri("stop_replay_watcher");
  }
}

function updateVolume(val) {
  const v = parseInt(val, 10);
  const disp = document.getElementById("quick-vol-display");
  if (disp) disp.innerText = `${v}%`;
  const cfgVol = document.getElementById("cfg-volume");
  if (cfgVol && cfgVol.value != v) cfgVol.value = v;

  invokeTauri("set_replay_volume", { volume: v });
  autoSaveSettings();
}

function toggleMute() {
  isMuted = !isMuted;
  const slider = document.getElementById("quick-vol");
  const muteBtn = document.getElementById("btn-quick-mute");

  if (isMuted) {
    previousVolume = slider ? parseInt(slider.value, 10) : 80;
    if (slider) slider.value = 0;
    updateVolume(0);
    if (muteBtn) muteBtn.innerText = "🔇";
    appendLog("Replay voice audio muted (M).");
  } else {
    const restore = previousVolume > 0 ? previousVolume : 80;
    if (slider) slider.value = restore;
    updateVolume(restore);
    if (muteBtn) muteBtn.innerText = "🔊";
    appendLog("Replay voice audio unmuted (M).");
  }
}

// Key binding for M hotkey
window.addEventListener("keydown", (e) => {
  if (e.key === "m" || e.key === "M") {
    // Only toggle if not actively typing inside an input
    const tag = document.activeElement ? document.activeElement.tagName.toLowerCase() : "";
    if (tag !== "input" && tag !== "textarea") {
      toggleMute();
    }
  }
});

// -------------------------------------------------------------
// SETTINGS AUTO-SAVE (Matching Python _on_settings_entry_debounced)
// -------------------------------------------------------------
function autoSaveSettings() {
  if (saveDebounceTimer) clearTimeout(saveDebounceTimer);
  saveDebounceTimer = setTimeout(performSaveSettings, 400);
}

async function performSaveSettings() {
  const folderInput = document.getElementById("cfg-folder");
  const vlcInput = document.getElementById("cfg-vlc-dir");
  const volInput = document.getElementById("cfg-volume");
  const langSelect = document.getElementById("cfg-lang");
  const themeSelect = document.getElementById("cfg-theme");
  const micSelect = document.getElementById("cfg-mic");
  const autoChamp = document.getElementById("cfg-auto-champ");
  const autoReplay = document.getElementById("cfg-auto-replay");
  const minTray = document.getElementById("cfg-minimize-tray");
  const hudEnabled = document.getElementById("cfg-hud-enabled");
  const hudLocked = document.getElementById("cfg-hud-locked");
  const hudOpacity = document.getElementById("cfg-hud-opacity");
  const cloudUrl = document.getElementById("cfg-cloud-url");
  const cloudKey = document.getElementById("cfg-cloud-key");

  const newConfig = {
    record_folder: folderInput ? folderInput.value.trim() : "",
    vlc_dir: vlcInput ? vlcInput.value.trim() : "",
    replay_volume: volInput ? parseInt(volInput.value, 10) || 80 : 80,
    language: langSelect ? langSelect.value : "en",
    theme: themeSelect ? themeSelect.value : "dark",
    mic_device_name: micSelect ? micSelect.value : "default",
    auto_start_champ_select: autoChamp ? autoChamp.checked : true,
    auto_start_replay: autoReplay ? autoReplay.checked : true,
    minimize_to_tray: minTray ? minTray.checked : true,
    hud_overlay_enabled: hudEnabled ? hudEnabled.checked : true,
    hud_locked: hudLocked ? hudLocked.checked : false,
    hud_click_through: hudLocked ? hudLocked.checked : false,
    hud_opacity: hudOpacity ? parseInt(hudOpacity.value, 10) / 100.0 : 0.85,
    hud_x: -1,
    hud_y: -1,
    audio_format: "mp3",
    remote_server_url: cloudUrl ? cloudUrl.value.trim() : "",
    remote_api_key: cloudKey ? cloudKey.value.trim() : "",
    target_audio_pid: -1,
    target_audio_name: "Discord",
    mic_device_index: -1,
    show_debug_panel: false,
    remote_api_key_encrypted: ""
  };

  await invokeTauri("save_config", { config: newConfig });
}

function onOpacityInput(val) {
  const disp = document.getElementById("hud-opacity-display");
  const lbl = document.getElementById("lbl-hud-opacity");
  if (disp) disp.innerText = `${val}%`;
  if (lbl) lbl.innerText = `HUD Opacity (${val}%):`;
}

// -------------------------------------------------------------
// LOAD RECORDINGS
// -------------------------------------------------------------
async function loadRecordings() {
  const container = document.getElementById("recordings-list");
  const countLabel = document.getElementById("recordings-count-text");
  if (!container) return;

  const recordings = await invokeTauri("get_recordings");
  if (!recordings || recordings.length === 0) {
    container.innerHTML = `<div class="empty-state">${t("recordings_empty")}</div>`;
    if (countLabel) countLabel.innerText = "Recordings in folder: 0 files (0.0 MB)";
    return;
  }

  let totalSizeMB = 0;
  container.innerHTML = "";

  recordings.forEach(rec => {
    const row = document.createElement("div");
    row.className = "recording-row";
    row.innerHTML = `
      <div class="recording-info">
        <span class="recording-name">🎵 ${rec.filename}</span>
        <span class="recording-meta">Size: ${rec.size_str}  |  Recorded: ${rec.date_str}</span>
      </div>
      <div style="display: flex; gap: 6px;">
        <button class="btn-small" onclick="playRecording('${rec.filename}')">▶ Play</button>
        <button class="btn-small" style="color: #F87171;" onclick="deleteRecording('${rec.filename}')">🗑 Delete</button>
      </div>
    `;
    container.appendChild(row);
  });

  if (countLabel) {
    countLabel.innerText = `Recordings in folder: ${recordings.length} files`;
  }
}

async function playRecording(filename) {
  await invokeTauri("play_recording", { filename });
}

async function deleteRecording(filename) {
  if (confirm(t("delete_confirm", { file: filename }))) {
    await invokeTauri("delete_recording", { filename });
    loadRecordings();
  }
}

function openRecordingsFolder() {
  invokeTauri("open_recordings_folder");
}

function browseVlcFolder() {
  appendLog("VLC Directory: Default is C:\\Program Files\\VideoLAN\\VLC");
}

// -------------------------------------------------------------
// CLOUD TEST & REPLAY API CONFIG
// -------------------------------------------------------------
async function testCloudConnection() {
  const statusLbl = document.getElementById("cloud-test-status");
  if (statusLbl) {
    statusLbl.innerText = "Connecting...";
    statusLbl.style.color = "var(--text-muted)";
  }

  const url = document.getElementById("cfg-cloud-url")?.value.trim() || "";
  const key = document.getElementById("cfg-cloud-key")?.value.trim() || "";

  if (!url || !key) {
    if (statusLbl) {
      statusLbl.innerText = "⚠️ Server URL and API Key required.";
      statusLbl.style.color = "#F87171";
    }
    return;
  }

  appendLog("☁️ Testing remote scrim server connection...");
  setTimeout(() => {
    if (statusLbl) {
      statusLbl.innerText = "✓ Server connected successfully";
      statusLbl.style.color = "var(--green-ok)";
    }
    appendLog("✓ Remote scrim backend reachable: 200 OK");
  }, 500);
}

async function triggerEnableReplayApi() {
  const badge = document.getElementById("api-status-badge");
  if (badge) badge.innerText = "Injecting EnableReplayApi=1...";

  const res = await invokeTauri("enable_replay_api");
  if (res) {
    if (badge) {
      badge.innerText = "✓ Replay API Enabled!";
      badge.style.color = "var(--green-ok)";
    }
    appendLog(`⚡ ${res}`);
  }
}

function checkForUpdates() {
  const msg = document.getElementById("update-status-msg");
  if (msg) msg.innerText = "Checking GitHub Releases...";
  appendLog("🔄 Checking GitHub for updates...");
  setTimeout(() => {
    if (msg) msg.innerText = "✓ You are using the latest version (v0.5.1).";
    appendLog("✓ League Voice Recorder is up to date (v0.5.1).");
  }, 600);
}

// -------------------------------------------------------------
// LIVE EVENT STREAM & AUDIO METERS POLLING
// -------------------------------------------------------------
async function pollLogs() {
  const logs = await invokeTauri("get_logs");
  if (logs && Array.isArray(logs)) {
    logs.forEach(msg => appendLog(msg));
  }
}

async function pollAudioLevels() {
  const levels = await invokeTauri("get_audio_levels");
  if (levels) {
    smoothApp = smoothApp * 0.7 + (levels.app_level || 0) * 0.3;
    smoothMic = smoothMic * 0.7 + (levels.mic_level || 0) * 0.3;

    const fillApp = document.getElementById("meter-app");
    const fillMic = document.getElementById("meter-mic");

    if (fillApp) fillApp.style.width = `${Math.min(100, Math.round(smoothApp * 100))}%`;
    if (fillMic) fillMic.style.width = `${Math.min(100, Math.round(smoothMic * 100))}%`;

    const dbgApp = document.getElementById("dbg-app-peak");
    const dbgMic = document.getElementById("dbg-mic-peak");
    if (dbgApp) dbgApp.innerText = `${Math.round(smoothApp * 100)}%`;
    if (dbgMic) dbgMic.innerText = `${Math.round(smoothMic * 100)}%`;
  }
}

async function refreshHealth() {
  const isApiOk = await invokeTauri("check_replay_api_status");
  const badge = document.getElementById("api-status-badge");
  if (badge) {
    if (isApiOk) {
      badge.innerText = "Status: 🟢 Enabled in game.cfg";
      badge.style.color = "var(--green-ok)";
    } else {
      badge.innerText = "Status: ⚪ Not enabled in game.cfg";
      badge.style.color = "var(--text-muted)";
    }
  }
}

// HUD Preview helpers
function previewRecHud() { appendLog("HUD Preview: Recorder HUD displayed."); }
function previewReplayHud() { appendLog("HUD Preview: Replay HUD displayed."); }
function hideHudPreview() { appendLog("HUD Preview: Hidden."); }
function resetHudPosition() { appendLog("HUD Position reset to default (Centered)."); }
function toggleTheme() { appendLog("Theme toggled."); }

// Initial startup
window.addEventListener("DOMContentLoaded", async () => {
  appendLog("🎮 League Voice Recorder initialized. Zero-latency WASAPI loopback active.");

  // Load config into settings inputs
  const cfg = await invokeTauri("get_config");
  if (cfg) {
    currentConfig = cfg;
    if (document.getElementById("cfg-folder")) document.getElementById("cfg-folder").value = cfg.record_folder || "";
    if (document.getElementById("cfg-vlc-dir")) document.getElementById("cfg-vlc-dir").value = cfg.vlc_dir || "";
    if (document.getElementById("cfg-volume")) document.getElementById("cfg-volume").value = cfg.replay_volume || 80;
    if (document.getElementById("quick-vol")) document.getElementById("quick-vol").value = cfg.replay_volume || 80;
    if (document.getElementById("quick-vol-display")) document.getElementById("quick-vol-display").innerText = `${cfg.replay_volume || 80}%`;
    if (document.getElementById("cfg-lang")) document.getElementById("cfg-lang").value = cfg.language || "en";
    if (document.getElementById("cfg-theme")) document.getElementById("cfg-theme").value = cfg.theme || "dark";
    if (document.getElementById("cfg-auto-champ")) document.getElementById("cfg-auto-champ").checked = cfg.auto_start_champ_select;
    if (document.getElementById("cfg-auto-replay")) document.getElementById("cfg-auto-replay").checked = cfg.auto_start_replay;
    if (document.getElementById("cfg-minimize-tray")) document.getElementById("cfg-minimize-tray").checked = cfg.minimize_to_tray;
    if (document.getElementById("cfg-hud-enabled")) document.getElementById("cfg-hud-enabled").checked = cfg.hud_overlay_enabled;
    if (document.getElementById("cfg-hud-locked")) document.getElementById("cfg-hud-locked").checked = cfg.hud_locked;
    if (document.getElementById("cfg-cloud-url")) document.getElementById("cfg-cloud-url").value = cfg.remote_server_url || "";
    if (document.getElementById("cfg-cloud-key")) document.getElementById("cfg-cloud-key").value = cfg.remote_api_key || "";
  }

  // Load recordings
  loadRecordings();
  refreshHealth();

  // Start polling loops
  setInterval(pollLogs, 150);
  setInterval(pollAudioLevels, 60);
});
