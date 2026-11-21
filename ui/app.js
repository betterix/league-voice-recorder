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

// Multi-lingual translation dictionaries & state
let currentTranslations = {};
let fallbackTranslations = {
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
  }
};
let availableLanguages = {};

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
  delete_confirm: "recordings.delete_confirm_msg",
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
    const dbgAudioOffset = document.getElementById("dbg-audio-offset");
    const dbgGameId = document.getElementById("dbg-game-id");
    const dbgGamePatch = document.getElementById("dbg-game-patch");

    if (dbgMatchState) dbgMatchState.innerText = telem.match_state;
    if (dbgGameTime) dbgGameTime.innerText = telem.game_time;
    if (dbgBaseTime) dbgBaseTime.innerText = telem.base_time;
    if (dbgAudioOffset) dbgAudioOffset.innerText = telem.audio_offset;
    if (dbgGameId) dbgGameId.innerText = telem.game_id;
    if (dbgGamePatch) dbgGamePatch.innerText = telem.game_patch;

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
    setStatusPollingRate(250);
    updateAudioPolling();
    await invokeTauri("start_recording_watcher");
  } else {
    isRecording = false;
    if (btn) btn.classList.remove("active");
    if (text) text.innerText = t("btn_start_rec");
    if (pill) {
      pill.innerText = "● IDLE (Click Start to monitor)";
      pill.className = "action-card-status";
    }
    if (!isReplaySync) setStatusPollingRate(800);
    updateAudioPolling();
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
    if (btn) btn.classList.add("active");
    if (text) text.innerText = t("btn_stop_rep");
    if (pill) {
      pill.innerText = "● LISTENING: Waiting for replay...";
      pill.className = "action-card-status watching";
    }
    setStatusPollingRate(250);
    updateAudioPolling();
    await invokeTauri("start_replay_watcher");
  } else {
    isReplaySync = false;
    if (btn) btn.classList.remove("active");
    if (text) text.innerText = t("btn_start_rep");
    if (pill) {
      pill.innerText = "● IDLE (Click Start to detect .rofl)";
      pill.className = "action-card-status";
    }
    if (!isRecording) setStatusPollingRate(800);
    updateAudioPolling();
    await invokeTauri("stop_replay_watcher");
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

    // Adapt polling rate based on live activity
    const isAnyActive = st.is_recording_watcher || st.is_replay_watcher;
    if (isAnyActive && currentPollInterval !== 250) {
      setStatusPollingRate(250);
      updateAudioPolling();
    } else if (!isAnyActive && currentPollInterval !== 800) {
      setStatusPollingRate(800);
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

    // 3. Match Clock
    const clockEl = document.getElementById("match-clock");
    if (st.match_clock) {
      if (clockEl) clockEl.innerText = `Match Time: ${st.match_clock}`;
    } else {
      if (clockEl) clockEl.innerText = "Match Time: --:--";
    }

    // 4. Update Telemetry & Debug Panel if visible
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
  const cloudUrl = document.getElementById("cfg-cloud-url");
  const cloudKey = document.getElementById("cfg-cloud-key");

  const rawKey = cloudKey ? cloudKey.value.trim() : "";
  const effectiveKey = rawKey || (currentConfig ? currentConfig.remote_api_key || "" : "");

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
    hud_opacity: hudOpacity ? parseInt(hudOpacity.value, 10) / 100.0 : (currentConfig ? currentConfig.hud_opacity : 0.85),
    hud_x: currentConfig ? currentConfig.hud_x : -1,
    hud_y: currentConfig ? currentConfig.hud_y : -1,
    audio_format: "mp3",
    remote_server_url: cloudUrl ? cloudUrl.value.trim() : (currentConfig ? currentConfig.remote_server_url : ""),
    remote_api_key: effectiveKey,
    discord_ipc_token: (currentConfig && currentConfig.discord_ipc_token) ? currentConfig.discord_ipc_token : "",
    target_audio_pid: -1,
    target_audio_name: "Discord",
    mic_device_index: -1,
    show_debug_panel: false
  };

  currentConfig = newConfig;
  await invokeTauri("save_config", { config: newConfig });
  if (hudLocked) {
    invokeTauri("overlay_set_locked", { locked: hudLocked.checked });
  }
}

function onOpacityInput(val) {
  const disp = document.getElementById("hud-opacity-display");
  const lbl = document.getElementById("lbl-hud-opacity");
  if (disp) disp.innerText = `${val}%`;
  if (lbl) lbl.innerText = `HUD Opacity (${val}%):`;
  invokeTauri("overlay_set_opacity", { opacity: parseFloat(val) / 100.0 });
}

// -------------------------------------------------------------
// LOAD RECORDINGS & METADATA MANAGEMENT (matching Python)
// -------------------------------------------------------------
let currentRecordings = [];
let remoteRecordings = [];
let cloudRole = "unknown";
let isSyncingRemote = false;

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
  const promptMsg = t("recordings.delete_confirm_msg", { game_id: gameId }) ||
    `Are you sure you want to permanently delete recording ${gameId}.mp3 from the remote server?`;
  if (!confirm(promptMsg)) {
    return;
  }
  if (btn) {
    btn.innerText = t("recordings.deleting");
    btn.disabled = true;
  }
  try {
    await invokeTauri("cloud_delete_recording", { gameId });
    appendLog(`✓ Deleted ${gameId}.mp3 from remote server.`);
    remoteRecordings = remoteRecordings.filter(it => String(it.game_id).trim() !== String(gameId).trim());
    await loadRecordings();
  } catch (err) {
    appendLog(`⚠️ Failed to delete ${gameId}.mp3 from remote server: ${err}`);
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
    const dateDisplay = remotePatch ? `${dateStr}  |  Patch: ${remotePatch}` : dateStr;
    const team = item.team_tag;

    const row = document.createElement("div");
    row.className = "recording-row remote-missing";

    const badgeTeam = team ? ` • vs ${escapeHtml(team)}` : "";
    const badgeText = `[${t("recordings.status_remote")}]${badgeTeam}`;

    row.innerHTML = `
      <div class="recording-info">
        <div class="recording-title-row">
          <span class="recording-name remote">☁️ ${escapeHtml(gameId)}.mp3</span>
          <span class="recording-badge-remote">${badgeText}</span>
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

    const row = document.createElement("div");
    row.className = "recording-row";

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
        <button class="btn-small" style="color: #F87171;" onclick="deleteRecording('${fnEscaped}')">🗑</button>
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
  if (confirm(t("delete_confirm", { file: filename }))) {
    await invokeTauri("delete_recording", { filename });
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
  }
}

async function loadTranslations(lang) {
  try {
    const data = await invokeTauri("get_translations", { lang });
    if (data && typeof data === "object") {
      currentTranslations = data;
    }
  } catch (err) {
    console.error(`Failed to load translations for ${lang}:`, err);
  }

  if (lang !== "en" && Object.keys(fallbackTranslations).length === 0) {
    try {
      const enData = await invokeTauri("get_translations", { lang: "en" });
      if (enData && typeof enData === "object") {
        fallbackTranslations = enData;
      }
    } catch (e) {}
  } else if (lang === "en") {
    fallbackTranslations = currentTranslations;
  }
}

async function changeLanguage(lang) {
  currentLanguage = lang;
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
  const appVersion = "0.6.0";
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
  const key = document.getElementById("cfg-cloud-key")?.value.trim() || "";

  if (!url || !key) {
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

async function checkForUpdates() {
  const msg = document.getElementById("update-status-msg");
  if (msg) msg.innerText = "Checking GitHub Releases...";
  const info = await invokeTauri("check_for_updates");
  if (info) {
    if (info.available) {
      if (msg) msg.innerHTML = `🌟 <b>New version available: ${info.latest_version}</b> (Current: ${info.current_version})`;
    } else {
      if (msg) msg.innerText = `✓ You are using the latest version (${info.current_version}).`;
    }
  } else {
    if (msg) msg.innerText = "⚠️ Could not check for updates (offline or rate limited).";
  }
}

// -------------------------------------------------------------
// LIVE EVENT STREAM & AUDIO METERS POLLING (Adaptive low-CPU)
// -------------------------------------------------------------
let audioPollTimer = null;
let statusPollTimer = null;
let currentPollInterval = 800;

function updateAudioPolling() {
  // If window is minimized / hidden to tray, or user is not on the dashboard tab, pause VU meter polling completely!
  if (document.hidden || (currentActiveTab !== "log" && currentActiveTab !== "main")) {
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
      statusPollTimer = setInterval(pollStatus, 2000);
    }
  } else {
    updateAudioPolling();
    setStatusPollingRate((isRecording || isReplaySync) ? 250 : 800);
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

// Initial startup - Instant & Non-blocking parallel initialization
window.addEventListener("DOMContentLoaded", async () => {
  // 1. Immediately apply dark theme
  applyTheme("dark");

  // 2. Fetch config and available languages concurrently
  try {
    const [cfg] = await Promise.all([
      invokeTauri("get_config"),
      loadAvailableLanguages(),
    ]);

    if (cfg) {
      currentConfig = cfg;
      currentLanguage = cfg.language || "en";
      currentTheme = cfg.theme || "dark";
      applyTheme(currentTheme);

      if (document.getElementById("cfg-folder")) document.getElementById("cfg-folder").value = currentConfig.record_folder || "";
      if (document.getElementById("cfg-volume")) document.getElementById("cfg-volume").value = currentConfig.replay_volume || 80;
      if (document.getElementById("quick-vol")) document.getElementById("quick-vol").value = currentConfig.replay_volume || 80;
      if (document.getElementById("quick-vol-display")) document.getElementById("quick-vol-display").innerText = `${currentConfig.replay_volume || 80}%`;
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
      if (document.getElementById("cfg-auto-champ")) document.getElementById("cfg-auto-champ").checked = currentConfig.auto_start_champ_select;
      if (document.getElementById("cfg-auto-replay")) document.getElementById("cfg-auto-replay").checked = currentConfig.auto_start_replay;
      if (document.getElementById("cfg-minimize-tray")) document.getElementById("cfg-minimize-tray").checked = currentConfig.minimize_to_tray;
      if (document.getElementById("cfg-hud-enabled")) document.getElementById("cfg-hud-enabled").checked = currentConfig.hud_overlay_enabled;
      if (document.getElementById("cfg-hud-locked")) document.getElementById("cfg-hud-locked").checked = currentConfig.hud_locked;
      if (document.getElementById("cfg-cloud-url")) document.getElementById("cfg-cloud-url").value = currentConfig.remote_server_url || "";
      if (document.getElementById("cfg-cloud-key")) document.getElementById("cfg-cloud-key").value = currentConfig.remote_api_key || "";
    }

    // Await translations load BEFORE anything renders text to prevent any code names!
    await loadTranslations(currentLanguage);
    applyTranslations();
  } catch (e) {}

  // Reveal main window once clean translated UI is ready
  requestAnimationFrame(() => {
    invokeTauri("show_main_window");
  });

  appendLog("🎮 League Voice Recorder initialized. Zero-latency WASAPI loopback active.");

  // 3. Deferred background loaders: start status bar and health a little bit later (350ms) to ensure clean translations
  setTimeout(() => {
    loadAudioDevices();
    loadRecordings();
    refreshHealth();
    setStatusPollingRate(800);
  }, 350);

  // 4. Cloud role discovery (no auto remote sync - only syncs when user clicks the Sync button)
  setTimeout(async () => {
    try {
      const role = await invokeTauri("cloud_get_role");
      if (role && role !== "unknown") cloudRole = role;
    } catch (e) {}
  }, 500);

  // 5. Start log and audio polling loops
  setInterval(pollLogs, 500);
  updateAudioPolling();
});
