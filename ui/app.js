// -------------------------------------------------------------
// INSTANT UI CACHE (Persistent localStorage for Frame-0 Render)
// -------------------------------------------------------------
const LVR_CACHE = {
  get(key, fallback = null) {
    try {
      const val = localStorage.getItem("lvr_" + key);
      return val ? JSON.parse(val) : fallback;
    } catch (e) {
      return fallback;
    }
  },
  set(key, val) {
    try {
      localStorage.setItem("lvr_" + key, JSON.stringify(val));
    } catch (e) {}
  }
};

let isRecording = false;
let isReplaySync = false;
let currentConfig = LVR_CACHE.get("config", null);
let currentLanguage = LVR_CACHE.get("lang", "en");
let currentTheme = LVR_CACHE.get("theme", "dark");
let isMuted = false;
let previousVolume = 80;
let smoothApp = 0.0;
let smoothMic = 0.0;
let autoscroll = true;
let saveDebounceTimer = null;
let currentAppVersion = LVR_CACHE.get("app_version", "0.6.1");

// Immediate Frame-0 Theme Application
try {
  let initialEffective = currentTheme;
  if (initialEffective === "system") {
    initialEffective = (window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches) ? "light" : "dark";
  }
  document.documentElement.setAttribute("data-theme", initialEffective);
} catch (e) {}

// Global bridge functions callable via win.eval from Rust
window.onVolumeChanged = function(vol) {
  const v = typeof vol === "number" ? vol : parseInt(vol, 10);
  const qVol = document.getElementById("quick-vol");
  const qDisp = document.getElementById("quick-vol-display");
  const cfgVol = document.getElementById("cfg-volume");
  const muteBtn = document.getElementById("btn-quick-mute");

  if (qVol && document.activeElement !== qVol) qVol.value = v;
  if (cfgVol && document.activeElement !== cfgVol) cfgVol.value = v;
  if (qDisp) qDisp.innerText = `${v}%`;
  if (muteBtn) muteBtn.innerText = v === 0 ? "🔇" : "🔊";
  isMuted = (v === 0);
};

window.onReplayStopped = function() {
  isReplaySync = false;
  const repBtn = document.getElementById("btn-rep");
  const repBtnText = document.getElementById("btn-rep-text");
  const repPill = document.getElementById("rep-status-pill");
  if (repBtn) repBtn.classList.remove("active");
  if (repBtnText) repBtnText.innerText = (typeof t === "function") ? t("btn_start_rep") : "▶ Start Replay Auto-Sync";
  if (repPill) {
    repPill.className = "action-card-status";
    repPill.innerText = "● IDLE (Click Start to detect .rofl)";
  }
  const clockEl = document.getElementById("match-clock");
  if (clockEl) clockEl.innerText = "Match Time: --:--";
  if (typeof setStatusPollingRate === "function") setStatusPollingRate(10000);
  if (typeof updateAudioPolling === "function") updateAudioPolling();
};

// Multi-lingual translation dictionaries & state with instant cache rehydration
let currentTranslations = LVR_CACHE.get("trans_" + currentLanguage, {});
let fallbackTranslations = LVR_CACHE.get("trans_en", {
  health: {
    discord: "Discord:",
    league: "League Client:",
    ready: "Ready",
    running: "Running",
    offline: "Offline",
  },
  record: {
    status_idle: "● IDLE (Click Start to monitor)",
    start_btn: "🔴 Start Recording Watcher",
    stop_btn: "⏹ Stop Recording Watcher",
  },
  replay: {
    status_idle: "● IDLE (Click Start to detect .rofl)",
    start_btn: "▶ Start Replay Auto-Sync",
    stop_btn: "⏹ Stop Replay Auto-Sync",
  },
  settings: {
    replay_api_status_enabled: "● Enabled",
    replay_api_status_disabled: "● Disabled",
  },
  recordings: {
    delete_local: "🗑 Delete Local",
    delete_local_confirm_msg: "Are you sure you want to delete {filename} from your local disk?",
    delete_remote: "☁ Delete from Server",
    delete_remote_confirm_msg: "Are you sure you want to permanently delete {filename} from the remote server?",
    delete_confirm_msg: "Are you sure you want to permanently delete {filename} from the remote server?",
  }
});
let availableLanguages = LVR_CACHE.get("langs", { en: "English", hu: "Magyar (Hungarian)" });

const I18N_ALIASES = {
  app_title: "app.title",
  app_subtitle: "app.subtitle",
  btn_start_rec: "record.start_btn",
  btn_stop_rec: "record.stop_btn",
  btn_start_rep: "replay.start_btn",
  btn_stop_rep: "replay.stop_btn",
  tab_log: "tabs.log",
  tab_recordings: "tabs.recordings",
  tab_settings: "tabs.settings",
  recordings_empty: "recordings.empty",
  delete_confirm: "recordings.delete_local_confirm_msg",
};

function getNestedValue(obj, path) {
  if (!obj || typeof obj !== "object") return null;
  const parts = path.split(".");
  let curr = obj;
  for (const p of parts) {
    if (!curr || typeof curr !== "object") return null;
    curr = curr[p];
  }
  return typeof curr === "string" ? curr : null;
}

function t(key, params = {}) {
  const resolvedKey = I18N_ALIASES[key] || key;
  let text = getNestedValue(currentTranslations, resolvedKey);
  if (!text) {
    text = getNestedValue(fallbackTranslations, resolvedKey);
  }
  if (!text) {
    text = key;
  }
  for (const [k, v] of Object.entries(params)) {
    text = text.replace(new RegExp(`\\{${k}\\}`, "g"), v);
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
    if (show) updateTelemetryPanel();
  }
}

async function updateTelemetryPanel() {
  try {
    const telem = await invokeTauri("get_telemetry");
    if (!telem) return;

    // 1. Match & Recorder Engine Card
    const dbgMatchState = document.getElementById("dbg-match-state");
    const dbgGameTime = document.getElementById("dbg-game-time");
    const dbgBaseTime = document.getElementById("dbg-base-time");
    const dbgGameId = document.getElementById("dbg-game-id");
    const dbgGamePatch = document.getElementById("dbg-game-patch");

    if (dbgMatchState) dbgMatchState.innerText = telem.match_state;
    if (dbgGameTime) dbgGameTime.innerText = telem.game_time;
    if (dbgBaseTime) dbgBaseTime.innerText = telem.base_time;
    if (dbgGameId) dbgGameId.innerText = telem.game_id;
    if (dbgGamePatch) dbgGamePatch.innerText = telem.game_patch;
    if (telem.game_patch && telem.game_patch !== "—" && telem.game_patch !== "Detecting...") {
      if (currentClientPatch !== telem.game_patch) {
        currentClientPatch = telem.game_patch;
        loadRecordings();
      }
    }

    // 2. Replay Sync Engine Card
    const dbgReplayFile = document.getElementById("dbg-replay-file");
    const dbgReplayTime = document.getElementById("dbg-replay-time");
    const dbgPlayerTime = document.getElementById("dbg-player-time");
    const dbgReplayDrift = document.getElementById("dbg-replay-drift");
    const dbgReplaySpeed = document.getElementById("dbg-replay-speed");
    const dbgReplayAudio = document.getElementById("dbg-replay-audio");

    if (dbgReplayFile) dbgReplayFile.innerText = telem.replay_file;
    if (dbgReplayTime) dbgReplayTime.innerText = telem.replay_time;
    if (dbgPlayerTime) dbgPlayerTime.innerText = telem.player_time;
    if (dbgReplayDrift) dbgReplayDrift.innerText = telem.replay_drift;
    if (dbgReplaySpeed) dbgReplaySpeed.innerText = telem.replay_speed;
    if (dbgReplayAudio) dbgReplayAudio.innerText = telem.replay_audio;

    // 3. System Usage & Version Card
    const dbgRam = document.getElementById("dbg-ram-usage");
    const dbgCpu = document.getElementById("dbg-cpu-usage");
    const dbgVer = document.getElementById("dbg-app-version");

    if (dbgRam) dbgRam.innerText = telem.ram_usage;
    if (dbgCpu) dbgCpu.innerText = telem.cpu_usage;
    if (dbgVer) dbgVer.innerText = telem.app_version;
  } catch (err) {}
}

let currentActiveTab = "log";

function switchTab(tabId) {
  currentActiveTab = tabId;
  document.querySelectorAll(".tab-btn").forEach(btn => btn.classList.remove("active"));
  document.querySelectorAll(".tab-content").forEach(c => c.classList.remove("active"));

  const targetTab = document.getElementById(`tab-${tabId}`);
  if (targetTab) targetTab.classList.add("active");

  const activeBtn = document.getElementById(`tab-btn-${tabId}`);
  if (activeBtn) activeBtn.classList.add("active");

  if (tabId === "recordings") {
    loadRecordings();
  }
  updateAudioPolling();
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
    if (btn) btn.classList.add("active");
    if (text) text.innerText = t("btn_stop_rec");
    if (pill) {
      pill.innerText = "● LISTENING: Waiting for match...";
      pill.className = "action-card-status watching";
    }
    setStatusPollingRate(10000);
    updateAudioPolling();
    await invokeTauri("start_recording_watcher");
    pollStatus();
  } else {
    isRecording = false;
    if (btn) btn.classList.remove("active");
    if (text) text.innerText = t("btn_start_rec");
    if (pill) {
      pill.innerText = "● IDLE (Click Start to monitor)";
      pill.className = "action-card-status";
    }
    if (!isReplaySync) setStatusPollingRate(10000);
    updateAudioPolling();
    await invokeTauri("stop_recording_watcher");
    pollStatus();
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
    if (btn) btn.classList.add("active");
    if (text) text.innerText = t("btn_stop_rep");
    if (pill) {
      pill.innerText = "● LISTENING: Waiting for replay...";
      pill.className = "action-card-status watching";
    }
    setStatusPollingRate(10000);
    updateAudioPolling();
    await invokeTauri("start_replay_watcher");
    pollStatus();
  } else {
    isReplaySync = false;
    if (btn) btn.classList.remove("active");
    if (text) text.innerText = t("btn_start_rep");
    if (pill) {
      pill.innerText = "● IDLE (Click Start to detect .rofl)";
      pill.className = "action-card-status";
    }
    if (!isRecording) setStatusPollingRate(10000);
    updateAudioPolling();
    await invokeTauri("stop_replay_watcher");
    pollStatus();
  }
}

// -------------------------------------------------------------
// STATUS & LIVE GAME TIMER POLLING
// -------------------------------------------------------------
async function pollStatus() {
  try {
    const st = await invokeTauri("get_status");
    if (!st) return;

    // 1. Recorder pill and button state
    const recPill = document.getElementById("rec-status-pill");
    const recBtn = document.getElementById("btn-rec");
    const recBtnText = document.getElementById("btn-rec-text");

    if (st.is_recording_watcher) {
      if (!isRecording) {
        isRecording = true;
        if (recBtn) recBtn.classList.add("active");
        if (recBtnText) recBtnText.innerText = t("btn_stop_rec");
      }
      if (recPill) {
        if (st.rec_status === "RECORDING") {
          recPill.className = "action-card-status rec";
          recPill.innerText = `● RECORDING: ${st.rec_detail || "Match in progress"}`;
        } else if (st.rec_status === "PAUSED") {
          recPill.className = "action-card-status paused";
          recPill.innerText = `⏸ PAUSED: ${st.rec_detail || "Match paused"}`;
        } else if (st.rec_status === "SAVING") {
          recPill.className = "action-card-status saving";
          recPill.innerText = `🎵 SAVING: ${st.rec_detail || "Finalizing audio..."}`;
        } else if (st.rec_status === "WATCHING") {
          recPill.className = "action-card-status watching";
          recPill.innerText = `● LISTENING: ${st.rec_detail || "Waiting for match..."}`;
        } else {
          recPill.className = "action-card-status watching";
          recPill.innerText = `● LISTENING: Waiting for match...`;
        }
      }
    } else {
      if (isRecording) {
        // Hands-free auto-stop after match ends & saves
        isRecording = false;
        if (recBtn) recBtn.classList.remove("active");
        if (recBtnText) recBtnText.innerText = t("btn_start_rec");
        loadRecordings();
      }
      if (recPill) {
        recPill.className = "action-card-status";
        recPill.innerText = "● IDLE (Click Start to monitor)";
      }
    }

    // Adaptive polling rate: 500ms when actively recording or syncing replay, 10s when idle
    const isLive = isRecording || isReplaySync || (st.is_recording_watcher && st.rec_status === "RECORDING") || (st.is_replay_watcher && st.rep_status === "SYNCING");
    const targetInterval = isLive ? 500 : 10000;
    if (currentPollInterval !== targetInterval) {
      setStatusPollingRate(targetInterval);
      updateAudioPolling();
    }

    // 2. Replay pill and button state
    const repPill = document.getElementById("rep-status-pill");
    const repBtn = document.getElementById("btn-rep");
    const repBtnText = document.getElementById("btn-rep-text");

    if (st.is_replay_watcher) {
      if (!isReplaySync) {
        isReplaySync = true;
        if (repBtn) repBtn.classList.add("active");
        if (repBtnText) repBtnText.innerText = t("btn_stop_rep");
      }
      if (repPill) {
        if (st.rep_status === "SYNCING") {
          repPill.className = "action-card-status active";
          repPill.innerText = `● SYNCING: ${st.rep_detail || "Active"}`;
        } else if (st.rep_status === "PAUSED") {
          repPill.className = "action-card-status paused";
          repPill.innerText = `⏸ PAUSED: ${st.rep_detail || "Replay paused"}`;
        } else {
          repPill.className = "action-card-status watching";
          repPill.innerText = `● LISTENING: ${st.rep_detail || "Waiting for replay..."}`;
        }
      }
    } else {
      if (isReplaySync) {
        isReplaySync = false;
        if (repBtn) repBtn.classList.remove("active");
        if (repBtnText) repBtnText.innerText = t("btn_start_rep");
      }
      if (repPill) {
        repPill.className = "action-card-status";
        repPill.innerText = "● IDLE (Click Start to detect .rofl)";
      }
    }

    // 3. Replay Volume & Offset sync from backend
    if (typeof st.replay_volume === "number") {
      const qVol = document.getElementById("quick-vol");
      const qDisp = document.getElementById("quick-vol-display");
      const cfgVol = document.getElementById("cfg-volume");
      const muteBtn = document.getElementById("btn-quick-mute");

      if (document.activeElement !== qVol && document.activeElement !== cfgVol) {
        if (qVol && parseInt(qVol.value, 10) !== st.replay_volume) {
          qVol.value = st.replay_volume;
        }
        if (cfgVol && parseInt(cfgVol.value, 10) !== st.replay_volume) {
          cfgVol.value = st.replay_volume;
        }
        if (qDisp) {
          qDisp.innerText = `${st.replay_volume}%`;
        }
        if (muteBtn) {
          muteBtn.innerText = st.replay_volume === 0 ? "🔇" : "🔊";
        }
        isMuted = (st.replay_volume === 0);
      }
    }

    // 4. Match Clock
    const clockEl = document.getElementById("match-clock");
    if (st.match_clock) {
      if (clockEl) clockEl.innerText = `Match Time: ${st.match_clock}`;
    } else {
      if (clockEl) clockEl.innerText = "Match Time: --:--";
    }

    // 5. Update Telemetry & Debug Panel if visible
    const debugPanel = document.getElementById("debug-panel");
    if (debugPanel && debugPanel.style.display !== "none") {
      updateTelemetryPanel();
    }
  } catch (err) {
    // Ignore polling errors
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
let isApiKeyDirty = false;

function onApiKeyInput() {
  isApiKeyDirty = true;
}

function autoSaveSettings() {
  if (saveDebounceTimer) clearTimeout(saveDebounceTimer);
  saveDebounceTimer = setTimeout(performSaveSettings, 400);
}

async function performSaveSettings() {
  const folderInput = document.getElementById("cfg-folder");
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
  const offsetInput = document.getElementById("cfg-replay-offset");
  const cloudUrl = document.getElementById("cfg-cloud-url");
  const cloudKey = document.getElementById("cfg-cloud-key");

  let effectiveKey = "";
  if (isApiKeyDirty && cloudKey) {
    const trimmed = cloudKey.value.trim();
    effectiveKey = trimmed.length === 0 ? "__CLEAR__" : trimmed;
  }

  const newConfig = {
    ...(currentConfig || {}),
    record_folder: folderInput ? folderInput.value.trim() : (currentConfig ? currentConfig.record_folder : ""),
    vlc_dir: "",
    replay_volume: volInput ? parseInt(volInput.value, 10) || 80 : (currentConfig ? currentConfig.replay_volume : 80),
    language: langSelect ? langSelect.value : (currentConfig ? currentConfig.language : "en"),
    theme: themeSelect ? themeSelect.value : (currentConfig ? currentConfig.theme : "dark"),
    mic_device_name: micSelect ? micSelect.value : (currentConfig ? currentConfig.mic_device_name : "default"),
    auto_start_champ_select: autoChamp ? autoChamp.checked : true,
    auto_start_replay: autoReplay ? autoReplay.checked : true,
    minimize_to_tray: minTray ? minTray.checked : true,
    hud_overlay_enabled: hudEnabled ? hudEnabled.checked : true,
    hud_locked: hudLocked ? hudLocked.checked : false,
    hud_click_through: hudLocked ? hudLocked.checked : false,
    hud_opacity: hudOpacity ? parseInt(hudOpacity.value, 10) / 100.0 : (currentConfig && typeof currentConfig.hud_opacity === "number" ? currentConfig.hud_opacity : 0.85),
    hud_x: currentConfig ? currentConfig.hud_x : -1,
    hud_y: currentConfig ? currentConfig.hud_y : -1,
    audio_format: "mp3",
    remote_server_url: cloudUrl ? cloudUrl.value.trim() : (currentConfig ? currentConfig.remote_server_url : ""),
    remote_api_key: effectiveKey,
    remote_api_key_encrypted: (effectiveKey === "__CLEAR__") ? "" : ((currentConfig && currentConfig.remote_api_key_encrypted) ? currentConfig.remote_api_key_encrypted : ""),
    discord_ipc_token: (currentConfig && currentConfig.discord_ipc_token) ? currentConfig.discord_ipc_token : "",
    target_audio_pid: -1,
    target_audio_name: "Discord",
    mic_device_index: -1,
    show_debug_panel: false
  };

  currentConfig = newConfig;
  LVR_CACHE.set("config", newConfig);
  await invokeTauri("save_config", { config: newConfig });
  if (effectiveKey !== "") {
    isApiKeyDirty = false;
  }
  if (hudLocked) {
    invokeTauri("overlay_set_locked", { locked: hudLocked.checked });
  }
}

function onOpacityInput(val) {
  const disp = document.getElementById("hud-opacity-display");
  const lbl = document.getElementById("lbl-hud-opacity");
  const num = parseFloat(val) / 100.0;
  if (disp) disp.innerText = `${val}%`;
  if (lbl) lbl.innerText = `HUD Opacity (${val}%):`;
  if (currentConfig) currentConfig.hud_opacity = num;
  invokeTauri("overlay_set_opacity", { opacity: num });
}

// -------------------------------------------------------------
// LOAD RECORDINGS & METADATA MANAGEMENT (matching Python)
// -------------------------------------------------------------
let currentRecordings = [];
let remoteRecordings = [];
let cloudRole = "unknown";
let isSyncingRemote = false;
let currentClientPatch = "";

function normalizePatch(p) {
  if (!p) return "";
  let clean = String(p).trim().toLowerCase();
  if (clean.startsWith("patch ")) {
    clean = clean.substring(6).trim();
  }
  const parts = clean.split(".");
  if (parts.length >= 2 && !isNaN(parts[0]) && !isNaN(parts[1])) {
    return `${parts[0]}.${parts[1]}`;
  }
  return clean;
}

function canUploadToCloud() {
  if (cloudRole === "pull") return false;
  if (cloudRole === "upload" || cloudRole === "admin") return true;
  const key = (currentConfig?.remote_api_key || document.getElementById("cfg-cloud-key")?.value || "").trim().toLowerCase();
  const url = (currentConfig?.remote_server_url || document.getElementById("cfg-cloud-url")?.value || "").trim();
  if (!key || !url) return false;
  if (key.includes("pull")) return false;
  if (key.includes("upload") || key.includes("admin")) return true;
  return false;
}

function isAdminRole() {
  if (cloudRole === "admin") return true;
  const key = (currentConfig?.remote_api_key || document.getElementById("cfg-cloud-key")?.value || "").trim().toLowerCase();
  return key.includes("admin");
}

function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

async function syncRemoteRecordings() {
  const syncBtn = document.getElementById("btn-sync-remote");
  if (isSyncingRemote) return;
  isSyncingRemote = true;

  if (syncBtn) {
    syncBtn.innerText = t("recordings.syncing");
    syncBtn.disabled = true;
  }

  try {
    const role = await invokeTauri("cloud_get_role");
    if (role && role !== "unknown") {
      cloudRole = role;
    }
    const items = await invokeTauri("cloud_list_recordings");
    remoteRecordings = Array.isArray(items) ? items : [];
    await loadRecordings();
    appendLog(`☁️ Remote scrims synced: ${remoteRecordings.length} recording(s) available on server.`);
  } catch (err) {
    appendLog(`⚠️ Cloud sync failed: ${err}`);
  } finally {
    isSyncingRemote = false;
    if (syncBtn) {
      syncBtn.innerText = t("recordings.sync_remote");
      syncBtn.disabled = false;
    }
  }
}

async function downloadSingleRecording(gameId, btn) {
  if (btn) {
    btn.innerText = t("recordings.downloading");
    btn.disabled = true;
  }
  try {
    await invokeTauri("cloud_download_recording", { gameId });
    appendLog(`✓ Downloaded ${gameId}.mp3 successfully.`);
    await loadRecordings();
  } catch (err) {
    appendLog(`⚠️ Download failed for ${gameId}.mp3: ${err}`);
    if (btn) {
      btn.innerText = t("recordings.download");
      btn.disabled = false;
    }
  }
}

async function downloadAllMissingRecordings() {
  const btn = document.getElementById("btn-download-all");
  if (btn) {
    btn.innerText = t("recordings.downloading");
    btn.disabled = true;
  }
  try {
    const count = await invokeTauri("cloud_download_all");
    appendLog(`✓ Batch download finished: ${count} scrim file(s) downloaded.`);
    await loadRecordings();
  } catch (err) {
    appendLog(`⚠️ Batch download failed: ${err}`);
    if (btn) btn.disabled = false;
  }
}

async function uploadSingleRecording(gameId, btn) {
  if (!canUploadToCloud()) {
    appendLog(`⚠️ Upload not permitted with active role: ${cloudRole}`);
    return;
  }
  if (btn) {
    btn.innerText = t("recordings.uploading");
    btn.disabled = true;
  }
  try {
    await invokeTauri("cloud_upload_recording", { gameId });
    appendLog(`✓ Uploaded ${gameId}.mp3 successfully to server.`);
    if (!remoteRecordings.some(it => String(it.game_id).trim() === String(gameId).trim())) {
      remoteRecordings.push({ game_id: gameId });
    }
    await loadRecordings();
  } catch (err) {
    appendLog(`⚠️ Upload failed for ${gameId}.mp3: ${err}`);
    if (btn) {
      btn.innerText = t("recordings.upload");
      btn.disabled = false;
    }
  }
}

async function deleteRemoteRecording(gameId, btn) {
  if (!isAdminRole()) {
    appendLog("⚠️ Only Admin role can delete recordings from the remote server.");
    return;
  }
  const cleanId = String(gameId || "").replace(/\.mp3$/i, "").trim();
  const fileName = `${cleanId}.mp3`;
  const promptParams = {
    game_id: cleanId,
    game_name: cleanId,
    filename: fileName,
    file: fileName,
  };
  let promptMsg = t("recordings.delete_remote_confirm_msg", promptParams);
  if (!promptMsg || promptMsg === "recordings.delete_remote_confirm_msg") {
    promptMsg = t("recordings.delete_confirm_msg", promptParams);
  }
  if (!promptMsg || promptMsg.includes("{game_name}") || promptMsg.includes("{game_id}")) {
    promptMsg = `Are you sure you want to permanently delete recording ${fileName} from the remote server?`;
  }
  if (!confirm(promptMsg)) {
    return;
  }
  if (btn) {
    btn.innerText = t("recordings.deleting");
    btn.disabled = true;
  }
  try {
    await invokeTauri("cloud_delete_recording", { gameId: cleanId });
    appendLog(`✓ Deleted ${fileName} from remote server.`);
    remoteRecordings = remoteRecordings.filter(it => String(it.game_id).trim() !== cleanId);
    await loadRecordings();
  } catch (err) {
    appendLog(`⚠️ Failed to delete ${fileName} from remote server: ${err}`);
    if (btn) {
      btn.innerText = t("recordings.delete_remote");
      btn.disabled = false;
    }
  }
}

async function loadRecordings() {
  const container = document.getElementById("recordings-list");
  const countLabel = document.getElementById("recordings-count-text");
  const downloadAllBtn = document.getElementById("btn-download-all");
  if (!container) return;

  if (!currentClientPatch) {
    try {
      const p = await invokeTauri("get_current_patch");
      if (p && p !== "—" && p !== "Detecting...") {
        currentClientPatch = p;
      }
    } catch (_) {}
  }
  const normCurrent = normalizePatch(currentClientPatch);

  const recordings = await invokeTauri("get_recordings");
  currentRecordings = recordings || [];

  const localGameIds = new Set();
  let totalSizeBytes = 0;
  currentRecordings.forEach(r => {
    const gid = r.game_id || r.filename.replace(/\.mp3$/i, "");
    localGameIds.add(String(gid).trim());
    totalSizeBytes += (r.size_bytes || 0);
  });

  const missingRemoteItems = (remoteRecordings || []).filter(it => {
    const gid = String(it.game_id || "").trim();
    return gid && !localGameIds.has(gid);
  });

  if (downloadAllBtn) {
    if (missingRemoteItems.length > 0) {
      downloadAllBtn.style.display = "inline-block";
      downloadAllBtn.innerText = t("recordings.download_all", { count: missingRemoteItems.length });
    } else {
      downloadAllBtn.style.display = "none";
    }
  }

  const totalSizeMb = totalSizeBytes / (1024 * 1024);
  if (countLabel) {
    countLabel.innerText = t("recordings.count_label", { count: currentRecordings.length, size: totalSizeMb });
  }

  if (currentRecordings.length === 0 && missingRemoteItems.length === 0) {
    container.innerHTML = `<div class="empty-state">${t("recordings.empty", { folder: "recordings" })}</div>`;
    return;
  }

  container.innerHTML = "";
  const fragment = document.createDocumentFragment();
  const remoteGameIds = new Set(
    (remoteRecordings || []).map(it => String(it.game_id || "").trim()).filter(Boolean)
  );

  const canOrganize = canUploadToCloud();
  const isAdmin = isAdminRole();

  // 1. Render Missing Remote Recordings First (prominent download action)
  missingRemoteItems.forEach(item => {
    const gameId = String(item.game_id).trim();
    const sizeMb = (item.file_size || 0) / (1024 * 1024);
    const dateRaw = String(item.created_at || item.upload_timestamp || "");
    const dateStr = dateRaw.length >= 10 ? dateRaw.substring(0, 10) : "Recent";
    const remotePatch = item.patch || item.game_patch || "";
    const normRemote = normalizePatch(remotePatch);
    const isOutdated = Boolean(normCurrent && normRemote && normRemote !== normCurrent);
    const dateDisplay = remotePatch ? `${dateStr}  |  Patch: ${remotePatch}` : dateStr;
    const team = item.team_tag;

    const row = document.createElement("div");
    row.className = `recording-row remote-missing${isOutdated ? " outdated-patch" : ""}`;

    const badgeTeam = team ? ` • vs ${escapeHtml(team)}` : "";
    let badgesHtml = `<span class="recording-badge-remote">[${t("recordings.status_remote")}]${badgeTeam}</span>`;
    if (remotePatch) {
      if (isOutdated) {
        badgesHtml += `<span class="badge-patch-outdated" title="Recorded on Patch ${escapeHtml(remotePatch)} (Current client patch: ${escapeHtml(currentClientPatch)})">${t("recordings.patch_outdated", { patch: escapeHtml(remotePatch) })}</span>`;
      } else {
        badgesHtml += `<span class="badge-patch-current">${t("recordings.patch_current", { patch: escapeHtml(remotePatch) })}</span>`;
      }
    }

    row.innerHTML = `
      <div class="recording-info">
        <div class="recording-title-row">
          <span class="recording-name remote">☁️ ${escapeHtml(gameId)}.mp3</span>
          ${badgesHtml}
        </div>
        <span class="recording-meta">${t("recordings.remote_meta", { size: sizeMb, date: dateDisplay })}</span>
      </div>
      <div class="recording-actions">
        <button class="btn-small btn-download" onclick="downloadSingleRecording('${escapeHtml(gameId)}', this)">${t("recordings.download")}</button>
        ${isAdmin ? `<button class="btn-small btn-delete-remote" onclick="deleteRemoteRecording('${escapeHtml(gameId)}', this)">${t("recordings.delete_remote")}</button>` : ""}
      </div>
    `;
    fragment.appendChild(row);
  });

  // 2. Render Local Scrim Recordings
  currentRecordings.forEach(rec => {
    const gameId = String(rec.game_id || rec.filename.replace(/\.mp3$/i, "")).trim();
    const isOnServer = remoteGameIds.has(gameId);

    const normRec = normalizePatch(rec.game_patch);
    const isOutdated = Boolean(normCurrent && normRec && normRec !== normCurrent);

    const row = document.createElement("div");
    row.className = `recording-row${isOutdated ? " outdated-patch" : ""}`;

    const savedTeam = rec.team_tag ? ` • vs ${escapeHtml(rec.team_tag)}` : "";
    const patchDisplay = rec.game_patch ? `  |  Patch: ${escapeHtml(rec.game_patch)}` : "";
    const recordedDate = rec.recorded_at ? rec.recorded_at.substring(0, 10) : rec.date_str;
    let cleanNotes = rec.notes ? rec.notes.replace(/^\uFEFF/, "").trim() : "";
    if (cleanNotes.toLowerCase().includes("match length") || cleanNotes.toLowerCase().startsWith("match ended at")) {
      cleanNotes = "";
    }
    const notesPreview = cleanNotes
      ? (cleanNotes.length > 35
          ? `  |  "${escapeHtml(cleanNotes.substring(0, 35))}..."`
          : `  |  "${escapeHtml(cleanNotes)}"`)
      : "";

    const fnEscaped = escapeHtml(rec.filename);
    const gidEscaped = escapeHtml(gameId);

    let badgesHtml = `<span class="recording-badge-local">[${t("recordings.status_local")}]</span>`;
    if (isOnServer) {
      badgesHtml += `<span class="recording-badge-synced" style="margin-left: 6px;">• [${t("recordings.status_synced")}]</span>`;
    }
    if (savedTeam) {
      badgesHtml += `<span class="recording-badge-local" style="margin-left: 6px;">${savedTeam}</span>`;
    }
    if (rec.game_patch) {
      if (isOutdated) {
        badgesHtml += `<span class="badge-patch-outdated" title="Recorded on Patch ${escapeHtml(rec.game_patch)} (Current client patch: ${escapeHtml(currentClientPatch)})">${t("recordings.patch_outdated", { patch: escapeHtml(rec.game_patch) })}</span>`;
      } else {
        badgesHtml += `<span class="badge-patch-current">${t("recordings.patch_current", { patch: escapeHtml(rec.game_patch) })}</span>`;
      }
    }

    const nameClickAttr = canOrganize
      ? `onclick="openEditDetailsModal('${fnEscaped}')" style="cursor: pointer;" title="Click to edit details"`
      : `style="cursor: default;"`;

    row.innerHTML = `
      <div class="recording-info">
        <div class="recording-title-row">
          <span class="recording-name" ${nameClickAttr}>🎵 ${fnEscaped}</span>
          ${badgesHtml}
        </div>
        <span class="recording-meta">Size: ${escapeHtml(rec.size_str)}  •  ${escapeHtml(recordedDate)}${patchDisplay}${notesPreview}</span>
      </div>
      <div class="recording-actions">
        ${canOrganize ? `<button class="btn-small btn-edit-details" onclick="openEditDetailsModal('${fnEscaped}')">${t("recordings.edit_details")}</button>` : ""}
        ${(!isOnServer && canOrganize) ? `<button class="btn-small btn-upload" onclick="uploadSingleRecording('${gidEscaped}', this)">${t("recordings.upload")}</button>` : ""}
        <button class="btn-small" onclick="playRecording('${fnEscaped}')">${t("recordings.play")}</button>
        ${(isAdmin && isOnServer) ? `<button class="btn-small btn-delete-remote" onclick="deleteRemoteRecording('${gidEscaped}', this)">${t("recordings.delete_remote")}</button>` : ""}
        <button class="btn-small btn-delete-local" onclick="deleteRecording('${fnEscaped}')">${t("recordings.delete_local")}</button>
      </div>
    `;
    fragment.appendChild(row);
  });

  container.appendChild(fragment);
}

function openEditDetailsModal(filename) {
  if (!canUploadToCloud()) return;
  const rec = currentRecordings.find(r => r.filename === filename);
  if (!rec) return;

  const modal = document.getElementById("modal-details");
  const modalTitle = document.getElementById("modal-title");
  const fileInput = document.getElementById("modal-filename");
  const gidInput = document.getElementById("modal-game-id");
  const dateInput = document.getElementById("modal-date");
  const patchInput = document.getElementById("modal-patch");
  const teamInput = document.getElementById("modal-team");
  const notesInput = document.getElementById("modal-notes");
  const saveUploadBtn = document.getElementById("btn-modal-save-upload");

  if (modalTitle) modalTitle.innerText = `📝 ${t("recordings.details_title")} — ${rec.filename}`;
  if (fileInput) fileInput.value = rec.filename;
  if (gidInput) gidInput.value = rec.game_id || rec.filename.replace(/\.mp3$/i, "");
  if (dateInput) dateInput.value = rec.recorded_at ? rec.recorded_at.substring(0, 10) : rec.date_str;
  if (patchInput) patchInput.value = rec.game_patch || "";
  if (teamInput) teamInput.value = rec.team_tag || "";
  let modalNotes = rec.notes ? rec.notes.replace(/^\uFEFF/, "").trim() : "";
  if (modalNotes.toLowerCase().includes("match length") || modalNotes.toLowerCase().startsWith("match ended at")) {
    modalNotes = "";
  }
  if (notesInput) notesInput.value = modalNotes;

  if (saveUploadBtn) {
    saveUploadBtn.style.display = canUploadToCloud() ? "inline-block" : "none";
  }

  if (modal) modal.style.display = "flex";
}

function closeEditDetailsModal() {
  const modal = document.getElementById("modal-details");
  if (modal) modal.style.display = "none";
}

async function saveDetailsFromModal(andUpload = false) {
  const fileInput = document.getElementById("modal-filename");
  const gidInput = document.getElementById("modal-game-id");
  const dateInput = document.getElementById("modal-date");
  const patchInput = document.getElementById("modal-patch");
  const teamInput = document.getElementById("modal-team");
  const notesInput = document.getElementById("modal-notes");

  const filename = fileInput ? fileInput.value : "";
  if (!filename) return;

  let rawPatch = patchInput ? patchInput.value.trim() : "";
  if (rawPatch.toLowerCase().startsWith("patch ")) {
    rawPatch = rawPatch.substring(6).trim();
  }
  const gid = gidInput ? gidInput.value.trim() : filename.replace(/\.mp3$/i, "");

  const metadata = {
    game_id: gid,
    team_tag: teamInput ? teamInput.value.trim() : "",
    notes: notesInput ? notesInput.value.trim() : "",
    game_patch: rawPatch,
    recorded_at: dateInput ? dateInput.value.trim() : "",
  };

  try {
    await invokeTauri("save_recording_metadata", { filename, metadata });
    closeEditDetailsModal();
    if (andUpload && canUploadToCloud()) {
      await uploadSingleRecording(gid);
    } else {
      await loadRecordings();
    }
  } catch (err) {
    appendLog(`⚠️ Failed to save details: ${err}`);
  }
}

async function playRecording(filename) {
  await invokeTauri("play_recording", { filename });
}

async function deleteRecording(filename) {
  const cleanFn = String(filename || "").trim();
  const cleanId = cleanFn.replace(/\.mp3$/i, "");
  const promptParams = {
    filename: cleanFn,
    file: cleanFn,
    game_id: cleanId,
    game_name: cleanId,
  };
  let promptMsg = t("recordings.delete_local_confirm_msg", promptParams);
  if (!promptMsg || promptMsg === "recordings.delete_local_confirm_msg") {
    promptMsg = t("delete_confirm", promptParams);
  }
  if (!promptMsg || promptMsg.includes("{game_name}") || promptMsg.includes("{game_id}")) {
    promptMsg = `Are you sure you want to delete ${cleanFn} from your local disk?`;
  }
  if (confirm(promptMsg)) {
    await invokeTauri("delete_recording", { filename: cleanFn });
    loadRecordings();
  }
}

function openRecordingsFolder() {
  invokeTauri("open_recordings_folder");
}

// -------------------------------------------------------------
// AUDIO DEVICES & INPUT MANAGEMENT
// -------------------------------------------------------------
async function loadAudioDevices() {
  appendLog("🔄 Refreshing audio input devices...");
  try {
    const mics = await invokeTauri("get_audio_devices");
    const cfgMic = document.getElementById("cfg-mic");
    const quickMic = document.getElementById("quick-mic");
    const targetVal = currentConfig?.mic_device_name || "default";

    const populate = (sel) => {
      if (!sel) return;
      sel.innerHTML = `<option value="default" data-i18n="record.mic_default">${t("record.mic_default")}</option>`;
      if (Array.isArray(mics)) {
        mics.forEach(m => {
          const opt = document.createElement("option");
          opt.value = m;
          opt.innerText = `🎙️ ${m}`;
          if (m === targetVal) opt.selected = true;
          sel.appendChild(opt);
        });
      }
      sel.value = targetVal;
      if (sel.selectedIndex === -1) {
        sel.selectedIndex = 0;
      }
    };

    populate(cfgMic);
    populate(quickMic);
    const selectedName = (cfgMic && cfgMic.selectedIndex >= 0) ? cfgMic.options[cfgMic.selectedIndex].text : "Default System Microphone";
    appendLog(`🎙️ Discovered ${Array.isArray(mics) ? mics.length : 0} audio input device(s). Selected: ${selectedName}`);
  } catch (err) {
    appendLog(`⚠️ Failed to enumerate audio devices: ${err}`);
  }
}

function onSettingsMicChange(val) {
  const quickMic = document.getElementById("quick-mic");
  if (quickMic && quickMic.value !== val) {
    quickMic.value = val;
  }
  appendLog(`🎙️ Microphone target set to: ${val === 'default' ? 'Default System Microphone' : val}`);
  autoSaveSettings();
}

function onQuickMicChange(val) {
  const cfgMic = document.getElementById("cfg-mic");
  if (cfgMic && cfgMic.value !== val) {
    cfgMic.value = val;
  }
  appendLog(`🎙️ Microphone target set to: ${val === 'default' ? 'Default System Microphone' : val}`);
  autoSaveSettings();
}

// -------------------------------------------------------------
// APPEARANCE & THEME MANAGEMENT
// -------------------------------------------------------------
function applyTheme(theme) {
  let effectiveTheme = theme;
  if (theme === "system") {
    effectiveTheme = (window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches) ? "light" : "dark";
  }
  document.documentElement.setAttribute("data-theme", effectiveTheme);
  const themeToggle = document.getElementById("btn-theme-toggle");
  if (themeToggle) {
    themeToggle.innerText = effectiveTheme === "light" ? "☀️" : "🌙";
  }
}

function onThemeChanged(theme) {
  currentTheme = theme;
  LVR_CACHE.set("theme", theme);
  applyTheme(theme);
  const cfgTheme = document.getElementById("cfg-theme");
  if (cfgTheme && cfgTheme.value !== theme) {
    cfgTheme.value = theme;
  }
  appendLog(`🎨 Appearance theme set to: ${theme}`);
  autoSaveSettings();
}

function toggleTheme() {
  const currentEffective = document.documentElement.getAttribute("data-theme") || "dark";
  const next = currentEffective === "light" ? "dark" : "light";
  onThemeChanged(next);
}

if (window.matchMedia) {
  window.matchMedia("(prefers-color-scheme: light)").addEventListener("change", () => {
    if (currentTheme === "system") {
      applyTheme("system");
    }
  });
}

// -------------------------------------------------------------
// MULTI-LINGUAL INTERNATIONALIZATION (i18n)
// -------------------------------------------------------------
async function loadAvailableLanguages() {
  const select = document.getElementById("cfg-lang");
  if (!select) return;

  let langs = { en: "English", hu: "Magyar (Hungarian)" };
  try {
    const backendLangs = await invokeTauri("get_available_languages");
    if (backendLangs && typeof backendLangs === "object" && Object.keys(backendLangs).length > 0) {
      langs = backendLangs;
    }
  } catch (err) {
    console.error("Failed to load available languages:", err);
  }

  availableLanguages = langs;
  LVR_CACHE.set("langs", langs);
  select.innerHTML = "";
  for (const [code, name] of Object.entries(langs)) {
    const opt = document.createElement("option");
    opt.value = code;
    opt.innerText = name;
    if (code === currentLanguage) opt.selected = true;
    select.appendChild(opt);
  }
  select.value = currentLanguage;
  if (select.selectedIndex === -1) {
    select.selectedIndex = 0;
    currentLanguage = select.value;
    LVR_CACHE.set("lang", currentLanguage);
  }
}

async function loadTranslations(lang) {
  try {
    const data = await invokeTauri("get_translations", { lang });
    if (data && typeof data === "object") {
      currentTranslations = data;
      LVR_CACHE.set("trans_" + lang, data);
    }
  } catch (err) {
    console.error(`Failed to load translations for ${lang}:`, err);
  }

  if (lang !== "en" && Object.keys(fallbackTranslations).length === 0) {
    try {
      const enData = await invokeTauri("get_translations", { lang: "en" });
      if (enData && typeof enData === "object") {
        fallbackTranslations = enData;
        LVR_CACHE.set("trans_en", enData);
      }
    } catch (e) {}
  } else if (lang === "en") {
    fallbackTranslations = currentTranslations;
  }
}

async function changeLanguage(lang) {
  currentLanguage = lang;
  LVR_CACHE.set("lang", lang);
  const cfgLang = document.getElementById("cfg-lang");
  if (cfgLang && cfgLang.value !== lang) {
    cfgLang.value = lang;
  }
  await loadTranslations(lang);
  applyTranslations();
  refreshHealth();
  const displayName = (availableLanguages && availableLanguages[lang]) || (lang === "hu" ? "Magyar" : "English");
  appendLog(`🌐 Interface language switched to: ${displayName}`);
  autoSaveSettings();
}

function applyTranslations() {
  // Update dynamic window title matching Python: self.title(f"{t('app.window_title')} v{__version__}")
  const appVersion = currentAppVersion;
  const windowTitle = `${t("app.window_title")} v${appVersion}`;
  document.title = windowTitle;
  const currentWin = (window.__TAURI__ && window.__TAURI__.window && window.__TAURI__.window.getCurrentWindow) ?
    window.__TAURI__.window.getCurrentWindow() : null;
  if (currentWin && currentWin.setTitle) {
    try {
      currentWin.setTitle(windowTitle);
    } catch (e) {}
  }

  // 1. Translate all elements with data-i18n attribute
  document.querySelectorAll("[data-i18n]").forEach(el => {
    const key = el.getAttribute("data-i18n");
    const text = t(key);
    if (text && text !== key) {
      if (el.tagName === "INPUT" && (el.type === "button" || el.type === "submit")) {
        el.value = text;
      } else if (el.tagName === "OPTION") {
        const val = el.value;
        el.innerText = text;
        el.value = val;
      } else {
        el.innerText = text;
      }
    }
  });

  // 2. Translate all elements with data-i18n-title attribute
  document.querySelectorAll("[data-i18n-title]").forEach(el => {
    const key = el.getAttribute("data-i18n-title");
    const text = t(key);
    if (text && text !== key) {
      el.title = text;
    }
  });

  // 3. Translate all elements with data-i18n-placeholder attribute
  document.querySelectorAll("[data-i18n-placeholder]").forEach(el => {
    const key = el.getAttribute("data-i18n-placeholder");
    const text = t(key);
    if (text && text !== key) {
      el.placeholder = text;
    }
  });

  // 4. Translate dynamic button states
  const recBtnText = document.getElementById("btn-rec-text");
  if (recBtnText) {
    recBtnText.innerText = isRecording ? t("record.stop_btn") : t("record.start_btn");
  }

  const repBtnText = document.getElementById("btn-rep-text");
  if (repBtnText) {
    repBtnText.innerText = isReplaySync ? t("replay.stop_btn") : t("replay.start_btn");
  }

  const recStatus = document.getElementById("rec-status-pill");
  if (recStatus && !isRecording && recStatus.innerText.includes("IDLE")) {
    recStatus.innerText = t("record.status_idle");
  }

  const repStatus = document.getElementById("rep-status-pill");
  if (repStatus && !isReplaySync && repStatus.innerText.includes("IDLE")) {
    repStatus.innerText = t("replay.status_idle");
  }

  const recCount = document.getElementById("recordings-count-text");
  if (recCount && (!currentRecordings || currentRecordings.length === 0)) {
    recCount.innerText = t("recordings.count_label", { count: 0, size: 0.0 });
  }

  const emptyRecordings = document.querySelector("#recordings-list .empty-state");
  if (emptyRecordings) {
    emptyRecordings.innerText = t("recordings.empty", { folder: "recordings" });
  }
}

// -------------------------------------------------------------
// CLOUD TEST & REPLAY API CONFIG
// -------------------------------------------------------------
async function testCloudConnection() {
  const statusLbl = document.getElementById("cloud-test-status");
  if (statusLbl) {
    statusLbl.innerText = t("settings.cloud_testing") || "Connecting...";
    statusLbl.style.color = "var(--text-muted)";
  }

  const url = document.getElementById("cfg-cloud-url")?.value.trim() || "";
  let key = document.getElementById("cfg-cloud-key")?.value.trim() || "";
  if (!key && currentConfig?.remote_api_key) {
    key = currentConfig.remote_api_key;
  }

  if (!url || (!key && !currentConfig?.remote_api_key_encrypted)) {
    if (statusLbl) {
      statusLbl.innerText = "⚠️ Server URL and API Key required.";
      statusLbl.style.color = "#F87171";
    }
    return;
  }

  try {
    const res = await invokeTauri("test_cloud_connection", { url, key });
    if (res && res.role) {
      cloudRole = res.role;
      let msg = t("settings.cloud_connected_pull");
      if (res.role === "admin") {
        msg = t("settings.cloud_connected_admin");
      } else if (res.role === "upload") {
        msg = t("settings.cloud_connected_upload");
      }
      if (statusLbl) {
        statusLbl.innerText = msg;
        statusLbl.style.color = "var(--green-ok)";
      }
      await performSaveSettings();
      syncRemoteRecordings();
    } else {
      if (statusLbl) {
        statusLbl.innerText = "❌ Connection failed. Check URL or key.";
        statusLbl.style.color = "#F87171";
      }
    }
  } catch (err) {
    if (statusLbl) {
      statusLbl.innerText = t("settings.cloud_failed", { error: err }) || `✖ Connection Failed: ${err}`;
      statusLbl.style.color = "#F87171";
    }
  }
}

async function triggerEnableReplayApi() {
  const badge = document.getElementById("api-status-badge");
  const cardBadge = document.getElementById("card-api-status");
  if (badge) badge.innerText = "Injecting EnableReplayApi=1...";
  if (cardBadge) cardBadge.innerText = "● Enabling...";

  const res = await invokeTauri("enable_replay_api");
  if (res) {
    refreshHealth();
    appendLog(`⚡ ${res}`);
  }
}

function dismissUpdateAnnouncement() {
  const bar = document.getElementById("update-announcement-bar");
  if (bar) bar.style.display = "none";
}

let latestDownloadUrl = null;

async function checkForUpdates(silent = false) {
  const msg = document.getElementById("update-status-msg");
  if (!silent && msg) msg.innerText = "Checking GitHub Releases...";
  const info = await invokeTauri("check_for_updates");
  if (info) {
    if (info.available) {
      latestDownloadUrl = info.download_url;
      const verText = info.latest_version.startsWith("v") ? info.latest_version : `v${info.latest_version}`;

      // 1. Show Top Announcement Bar
      const annBar = document.getElementById("update-announcement-bar");
      const annText = document.getElementById("update-announcement-text");
      const annBtn = document.getElementById("btn-banner-update");
      if (annBar && annText && annBtn) {
        annText.innerHTML = `<b>New Version Available: ${verText}</b> — An update is ready to install!`;
        annBar.style.display = "block";
        if (info.download_url) {
          annBtn.style.display = "inline-flex";
          annBtn.onclick = () => applyUpdate(info.download_url);
        } else {
          annBtn.style.display = "none";
        }
      }

      // 2. Show Header Badge
      const badge = document.getElementById("btn-header-update-badge");
      if (badge) {
        badge.innerText = `🚀 Update: ${verText}`;
        badge.style.display = "inline-flex";
      }

      // 3. Settings Card Message
      if (msg) {
        if (info.download_url) {
          msg.innerHTML = `🌟 <b>New version available: ${info.latest_version}</b> (Current: ${info.current_version}) <button id="btn-apply-update" class="btn-update-apply" onclick="applyUpdate('${info.download_url}')">⬇️ Download & Install Update</button>`;
        } else {
          msg.innerHTML = `🌟 <b>New version available: ${info.latest_version}</b> (Current: ${info.current_version}) <a href="https://github.com/betterix/league-voice-recorder/releases/latest" target="_blank" style="color: var(--accent-gold); margin-left: 8px; text-decoration: underline; font-weight: bold;">🔗 View Release</a>`;
        }
      }

      appendLog(`🚀 New version available: ${verText} (Current: ${info.current_version})`);
    } else {
      if (!silent && msg) {
        msg.innerText = `✓ You are using the latest version (${info.current_version}).`;
      }
    }
  } else {
    if (!silent && msg) {
      msg.innerText = "⚠️ Could not check for updates (offline or rate limited).";
    }
  }
}

async function applyUpdate(downloadUrl) {
  const targetUrl = downloadUrl || latestDownloadUrl;
  if (!targetUrl) return;

  const btn = document.getElementById("btn-apply-update");
  const bannerBtn = document.getElementById("btn-banner-update");
  [btn, bannerBtn].forEach(b => {
    if (b) {
      b.disabled = true;
      b.innerText = "⏳ Initializing download...";
      b.style.opacity = "0.7";
    }
  });

  const progressContainers = [
    document.getElementById("update-progress-container"),
    document.getElementById("banner-update-progress-container")
  ];
  progressContainers.forEach(c => { if (c) c.style.display = "block"; });

  const progressFills = [
    document.getElementById("update-progress-bar-fill"),
    document.getElementById("banner-update-progress-bar-fill")
  ];
  progressFills.forEach(f => {
    if (f) {
      f.classList.remove("installing");
      f.style.width = "0%";
    }
  });

  const statusEls = [
    document.getElementById("update-progress-status"),
    document.getElementById("banner-update-progress-status")
  ];
  statusEls.forEach(s => { if (s) s.innerText = "Starting download..."; });

  const byteEls = [
    document.getElementById("update-progress-bytes"),
    document.getElementById("banner-update-progress-bytes")
  ];
  byteEls.forEach(b => { if (b) b.innerText = "Connecting..."; });

  const barTextEls = [
    document.getElementById("update-progress-bar-text"),
    document.getElementById("banner-update-progress-bar-text")
  ];
  barTextEls.forEach(t => { if (t) t.innerText = "Connecting..."; });

  try {
    await invokeTauri("apply_update", { downloadUrl: targetUrl });
  } catch (err) {
    statusEls.forEach(s => { if (s) s.innerText = `⚠️ Update failed: ${err}`; });
    [btn, bannerBtn].forEach(b => {
      if (b) {
        b.disabled = false;
        b.innerText = "⬇️ Download & Install Update";
        b.style.opacity = "1";
      }
    });
  }
}

// -------------------------------------------------------------
// LIVE EVENT STREAM & AUDIO METERS POLLING (Adaptive low-CPU)
// -------------------------------------------------------------
let audioPollTimer = null;
let statusPollTimer = null;
let currentPollInterval = 10000;

function updateAudioPolling() {
  // If window is minimized / hidden to tray, pause VU meter polling
  if (document.hidden) {
    if (audioPollTimer) {
      clearInterval(audioPollTimer);
      audioPollTimer = null;
    }
    return;
  }
  const interval = (isRecording || isReplaySync) ? 60 : 120;
  if (audioPollTimer) clearInterval(audioPollTimer);
  audioPollTimer = setInterval(pollAudioLevels, interval);
}

// Window visibility state observer: pauses polling loops when minimized to tray
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    if (audioPollTimer) {
      clearInterval(audioPollTimer);
      audioPollTimer = null;
    }
    if (statusPollTimer) {
      clearInterval(statusPollTimer);
      statusPollTimer = setInterval(pollStatus, 10000);
    }
  } else {
    updateAudioPolling();
    setStatusPollingRate(10000);
  }
});

function scaleVu(peak) {
  if (peak <= 0.0005) return 0.0;
  return Math.max(0.0, Math.min(1.0, Math.pow((peak - 0.0005) / 0.9995, 0.42)));
}

function setStatusPollingRate(intervalMs) {
  if (currentPollInterval === intervalMs && statusPollTimer) return;
  currentPollInterval = intervalMs;
  if (statusPollTimer) clearInterval(statusPollTimer);
  statusPollTimer = setInterval(pollStatus, intervalMs);
}

async function pollLogs() {
  const logs = await invokeTauri("get_logs");
  if (logs && Array.isArray(logs)) {
    logs.forEach(msg => appendLog(msg));
  }
}

async function pollAudioLevels() {
  const fillApp = document.getElementById("meter-app");
  const fillMic = document.getElementById("meter-mic");
  const lblMic = document.getElementById("lbl-meter-mic");
  const trackMic = document.getElementById("track-meter-mic");

  const levels = await invokeTauri("get_audio_levels");
  if (levels) {
    const rawApp = levels.app_level || 0;
    const rawMic = levels.mic_level || 0;
    const isDiscordMuted = Boolean(levels.is_discord_muted);

    const targetApp = scaleVu(rawApp);
    const targetMic = scaleVu(rawMic);

    // Ballistic smoothing matching Python: snappy attack, silky exponential decay
    if (targetApp > smoothApp) {
      smoothApp = smoothApp * 0.35 + targetApp * 0.65;
    } else {
      smoothApp = Math.max(0.0, smoothApp * 0.84);
    }

    if (targetMic > smoothMic) {
      smoothMic = smoothMic * 0.35 + targetMic * 0.65;
    } else {
      smoothMic = Math.max(0.0, smoothMic * 0.84);
    }

    if (smoothApp < 0.005) smoothApp = 0;
    if (smoothMic < 0.005) smoothMic = 0;

    if (fillApp) fillApp.style.width = `${Math.min(100, Math.round(smoothApp * 100))}%`;

    const dbgApp = document.getElementById("dbg-app-peak");
    const dbgMic = document.getElementById("dbg-mic-peak");
    if (dbgApp) dbgApp.innerText = `${Math.round(smoothApp * 100)}%`;

    if (isDiscordMuted) {
      if (fillMic) {
        fillMic.classList.add("muted");
        fillMic.style.width = "100%";
      }
      if (lblMic) {
        lblMic.classList.add("muted");
        lblMic.innerText = "🔇 Mic (Muted):";
      }
      if (trackMic) trackMic.classList.add("muted");
      if (dbgMic) dbgMic.innerText = "MUTED";
    } else {
      if (fillMic) {
        fillMic.classList.remove("muted");
        fillMic.style.width = `${Math.min(100, Math.round(smoothMic * 100))}%`;
      }
      if (lblMic) {
        lblMic.classList.remove("muted");
        lblMic.innerText = "🎤 Mic:";
      }
      if (trackMic) trackMic.classList.remove("muted");
      if (dbgMic) dbgMic.innerText = `${Math.round(smoothMic * 100)}%`;
    }
  }
}

async function refreshHealth() {
  const health = await invokeTauri("get_system_health");
  if (!health) return;

  const discordLbl = t("health.discord");
  const leagueLbl = t("health.league");
  const readyTxt = t("health.ready");
  const runningTxt = t("health.running");
  const offlineTxt = t("health.offline");

  // 1. Discord Health Pill (green if running, red if offline)
  const pillDiscord = document.getElementById("pill-discord");
  const txtDiscord = document.getElementById("pill-discord-text");
  if (pillDiscord && txtDiscord) {
    if (health.discord_running) {
      pillDiscord.className = "health-pill online";
      txtDiscord.innerText = `${discordLbl} ${readyTxt}`;
    } else {
      pillDiscord.className = "health-pill offline";
      txtDiscord.innerText = `${discordLbl} ${offlineTxt}`;
    }
  }

  // 2. League Client Health Pill (green if running, red if closed)
  const pillLeague = document.getElementById("pill-league");
  const txtLeague = document.getElementById("pill-league-text");
  if (pillLeague && txtLeague) {
    if (health.league_running) {
      pillLeague.className = "health-pill online";
      txtLeague.innerText = `${leagueLbl} ${runningTxt}`;
    } else {
      pillLeague.className = "health-pill offline";
      txtLeague.innerText = `${leagueLbl} ${offlineTxt}`;
    }
  }

  // 3. Settings & Card Replay API Status Badge
  const badge = document.getElementById("api-status-badge");
  const cardBadge = document.getElementById("card-api-status");

  if (health.replay_api_enabled) {
    if (badge) {
      badge.innerText = t("settings.replay_api_status_enabled");
      badge.style.color = "var(--green-ok)";
    }
    if (cardBadge) {
      cardBadge.className = "api-card-pill enabled";
      cardBadge.innerText = t("settings.replay_api_status_enabled");
    }
  } else {
    if (badge) {
      badge.innerText = t("settings.replay_api_status_disabled");
      badge.style.color = "#F87171";
    }
    if (cardBadge) {
      cardBadge.className = "api-card-pill disabled";
      cardBadge.innerText = t("settings.replay_api_status_disabled");
    }
  }
}

// HUD Preview helpers
async function previewRecHud() {
  await invokeTauri("overlay_preview_rec");
  appendLog("HUD Preview: Recorder HUD displayed.");
}

async function previewReplayHud() {
  await invokeTauri("overlay_preview_replay");
  appendLog("HUD Preview: Replay HUD displayed.");
}

async function hideHudPreview() {
  await invokeTauri("overlay_hide_preview");
  appendLog("HUD Preview: Hidden.");
}

async function resetHudPosition() {
  await invokeTauri("overlay_reset_position");
  appendLog("HUD Position reset to top-right screen corner.");
}

function populateConfigToDom(cfg) {
  if (!cfg) return;
  if (document.getElementById("cfg-folder")) document.getElementById("cfg-folder").value = cfg.record_folder || "";
  if (document.getElementById("cfg-volume")) document.getElementById("cfg-volume").value = cfg.replay_volume || 80;
  if (document.getElementById("quick-vol")) document.getElementById("quick-vol").value = cfg.replay_volume || 80;
  if (document.getElementById("quick-vol-display")) document.getElementById("quick-vol-display").innerText = `${cfg.replay_volume || 80}%`;
  const cfgLang = document.getElementById("cfg-lang");
  if (cfgLang) {
    cfgLang.value = currentLanguage;
    if (cfgLang.selectedIndex === -1) cfgLang.selectedIndex = 0;
  }
  const cfgTheme = document.getElementById("cfg-theme");
  if (cfgTheme) {
    cfgTheme.value = currentTheme;
    if (cfgTheme.selectedIndex === -1) cfgTheme.selectedIndex = 0;
  }
  if (document.getElementById("cfg-auto-champ")) document.getElementById("cfg-auto-champ").checked = Boolean(cfg.auto_start_champ_select);
  if (document.getElementById("cfg-auto-replay")) document.getElementById("cfg-auto-replay").checked = Boolean(cfg.auto_start_replay);
  if (document.getElementById("cfg-minimize-tray")) document.getElementById("cfg-minimize-tray").checked = Boolean(cfg.minimize_to_tray);
  if (document.getElementById("cfg-hud-enabled")) document.getElementById("cfg-hud-enabled").checked = Boolean(cfg.hud_overlay_enabled);
  if (document.getElementById("cfg-hud-locked")) document.getElementById("cfg-hud-locked").checked = Boolean(cfg.hud_locked);

  const savedOpacity = typeof cfg.hud_opacity === "number" ? cfg.hud_opacity : 0.85;
  const opacityPct = Math.round(savedOpacity * 100);
  const opacitySlider = document.getElementById("cfg-hud-opacity");
  const opacityDisp = document.getElementById("hud-opacity-display");
  const opacityLbl = document.getElementById("lbl-hud-opacity");
  if (opacitySlider) opacitySlider.value = opacityPct;
  if (opacityDisp) opacityDisp.innerText = `${opacityPct}%`;
  if (opacityLbl) opacityLbl.innerText = `HUD Opacity (${opacityPct}%):`;

  if (document.getElementById("cfg-cloud-url")) document.getElementById("cfg-cloud-url").value = cfg.remote_server_url || "";
  const cloudKeyEl = document.getElementById("cfg-cloud-key");
  if (cloudKeyEl) {
    if (cfg.remote_api_key) {
      cloudKeyEl.value = cfg.remote_api_key;
    } else if (cfg.remote_api_key_encrypted) {
      cloudKeyEl.value = "";
      cloudKeyEl.placeholder = "•••••••••••••••• (Encrypted key saved)";
    } else {
      cloudKeyEl.value = "";
      cloudKeyEl.placeholder = "Paste your UPLOAD_KEY or PULL_KEY";
    }
  }
}

// Initial startup - Instant Frame-0 Render & Stale-While-Revalidate Background Sync
window.addEventListener("DOMContentLoaded", () => {
  // 1. Frame-0 instant visual setup from persistent local storage cache
  applyTheme(currentTheme);
  if (currentConfig) {
    populateConfigToDom(currentConfig);
  }
  applyTranslations();

  appendLog("🎮 League Voice Recorder initialized. Zero-latency WASAPI loopback active.");

  // 2. Post-paint window reveal and deferred services
  requestAnimationFrame(() => {
    invokeTauri("show_main_window");
    setTimeout(() => {
      refreshHealth();
      loadAudioDevices();
      loadRecordings();
      setStatusPollingRate(10000);
      (async () => {
        try {
          const role = await invokeTauri("cloud_get_role");
          if (role && role !== "unknown") cloudRole = role;
        } catch (e) {}
      })();
    }, 300);

    setTimeout(() => checkForUpdates(true), 3000);

    setInterval(pollLogs, 500);
    updateAudioPolling();
  });

  // 3. Asynchronous background revalidation (Stale-While-Revalidate)
  (async () => {
    try {
      const [cfg, _] = await Promise.all([
        invokeTauri("get_config"),
        loadAvailableLanguages(),
      ]);

      if (cfg) {
        currentConfig = cfg;
        currentLanguage = cfg.language || "en";
        currentTheme = cfg.theme || "dark";
        LVR_CACHE.set("config", cfg);
        LVR_CACHE.set("lang", currentLanguage);
        LVR_CACHE.set("theme", currentTheme);
        applyTheme(currentTheme);
        populateConfigToDom(currentConfig);
      }

      // Fetch dynamic app version from backend
      try {
        const ver = await invokeTauri("get_app_version");
        if (ver) {
          currentAppVersion = ver;
          LVR_CACHE.set("app_version", ver);
          const dbgVer = document.getElementById("dbg-app-version");
          if (dbgVer) dbgVer.innerText = `v${ver}`;
          const settingsVer = document.getElementById("settings-app-version");
          if (settingsVer) settingsVer.innerText = `v${ver}`;
        }
      } catch (e) {}

      await loadTranslations(currentLanguage);
      applyTranslations();
    } catch (e) {}
  })();

  // 2b. Listen for reactive events from Tauri backend (overlay volume changes, hotkeys, update progress)
  if (window.__TAURI__ && window.__TAURI__.event) {
    window.__TAURI__.event.listen("update-download-progress", (event) => {
      const data = event.payload;
      const progressContainers = [
        document.getElementById("update-progress-container"),
        document.getElementById("banner-update-progress-container")
      ];
      progressContainers.forEach(c => { if (c) c.style.display = "block"; });

      const progressFills = [
        document.getElementById("update-progress-bar-fill"),
        document.getElementById("banner-update-progress-bar-fill")
      ];
      const statusEls = [
        document.getElementById("update-progress-status"),
        document.getElementById("banner-update-progress-status")
      ];
      const byteEls = [
        document.getElementById("update-progress-bytes"),
        document.getElementById("banner-update-progress-bytes")
      ];
      const barTextEls = [
        document.getElementById("update-progress-bar-text"),
        document.getElementById("banner-update-progress-bar-text")
      ];

      if (data.status === "downloading") {
        progressFills.forEach(f => {
          if (f) {
            f.classList.remove("installing");
            f.style.width = `${data.percentage}%`;
          }
        });
        statusEls.forEach(s => {
          if (s) s.innerText = `Downloading update... ${data.percentage}%`;
        });
        if (data.total > 0) {
          const dlMb = (data.downloaded / (1024 * 1024)).toFixed(1);
          const totalMb = (data.total / (1024 * 1024)).toFixed(1);
          byteEls.forEach(b => {
            if (b) b.innerText = `${dlMb} MB / ${totalMb} MB`;
          });
          barTextEls.forEach(t => {
            if (t) t.innerText = `${dlMb} MB / ${totalMb} MB (${data.percentage}%)`;
          });
        } else {
          barTextEls.forEach(t => {
            if (t) t.innerText = `${data.percentage}%`;
          });
        }
      } else if (data.status === "installing") {
        progressFills.forEach(f => {
          if (f) {
            f.classList.add("installing");
            f.style.width = "100%";
          }
        });
        statusEls.forEach(s => {
          if (s) s.innerText = "Installing update & restarting application...";
        });
        byteEls.forEach(b => {
          if (b) b.innerText = "Finalizing...";
        });
        barTextEls.forEach(t => {
          if (t) t.innerText = "Installing... (100%)";
        });
      }
    });

    window.__TAURI__.event.listen("volume-updated", (event) => {
      const vol = event.payload;
      const qVol = document.getElementById("quick-vol");
      const qDisp = document.getElementById("quick-vol-display");
      const cfgVol = document.getElementById("cfg-volume");
      const muteBtn = document.getElementById("btn-quick-mute");

      if (document.activeElement !== qVol && qVol) qVol.value = vol;
      if (document.activeElement !== cfgVol && cfgVol) cfgVol.value = vol;
      if (qDisp) qDisp.innerText = `${vol}%`;
      if (muteBtn) muteBtn.innerText = vol === 0 ? "🔇" : "🔊";
      isMuted = (vol === 0);
    });

    window.__TAURI__.event.listen("replay-stopped", () => {
      isReplaySync = false;
      const repBtn = document.getElementById("btn-rep");
      const repBtnText = document.getElementById("btn-rep-text");
      const repPill = document.getElementById("rep-status-pill");
      if (repBtn) repBtn.classList.remove("active");
      if (repBtnText) repBtnText.innerText = t("btn_start_rep");
      if (repPill) {
        repPill.className = "action-card-status";
        repPill.innerText = "● IDLE (Click Start to detect .rofl)";
      }
      const clockEl = document.getElementById("match-clock");
      if (clockEl) clockEl.innerText = "Match Time: --:--";
      setStatusPollingRate(10000);
      updateAudioPolling();
    });
  }
});
