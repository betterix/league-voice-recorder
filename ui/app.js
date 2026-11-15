let isRecording = false;
let isReplaySync = false;
let currentConfig = null;
let currentLanguage = "en";
let isMuted = false;
let previousVolume = 100;
let smoothApp = 0.0;
let smoothMic = 0.0;

// Embedded i18n dictionary for instant client-side localization
const I18N = {
  en: {
    app_title: "LEAGUE VOICE RECORDER",
    app_subtitle: "Automated Discord Voice Capture & In-Game Replay Audio Sync",
    btn_start_rec: "Start Recording Watcher",
    btn_stop_rec: "Stop Recording Watcher",
    btn_start_rep: "Start Replay Auto-Sync",
    btn_stop_rep: "Stop Replay Auto-Sync",
    status_standby: "Standby. Click 'Start Recording Watcher' before your match or 'Start Replay Auto-Sync' for ROFL replays.",
    status_rec_active: "Recording watcher active. Waiting for League match...",
    status_rep_active: "Replay synchronizer active. Open any .rofl replay in League.",
    tab_log: "📋 Activity Log",
    tab_recordings: "📁 Scrim Recordings",
    tab_settings: "⚙️ Settings",
    live_log_title: "LIVE EVENT LOG",
    recordings_empty: "No recordings found in recordings directory.",
    copy_success: "Log copied to clipboard!",
    delete_confirm: "Are you sure you want to permanently delete {file}?",
  },
  hu: {
    app_title: "LEAGUE HANGFELVEVŐ",
    app_subtitle: "Automatikus Discord Hangrögzítés és Visszajátszás Szinkronizáció",
    btn_start_rec: "Felvétel Figyelő Indítása",
    btn_stop_rec: "Felvétel Figyelő Leállítása",
    btn_start_rep: "Replay Szinkron Indítása",
    btn_stop_rep: "Replay Szinkron Leállítása",
    status_standby: "Készenlét. Kattints a 'Felvétel Figyelő Indítása' gombra a meccs előtt.",
    status_rec_active: "Felvétel figyelő aktív. Várakozás a League mérkőzésre...",
    status_rep_active: "Replay szinkronizáló aktív. Nyiss meg egy .rofl fájlt a kliensben.",
    tab_log: "📋 Eseménynapló",
    tab_recordings: "📁 Scrim Felvételek",
    tab_settings: "⚙️ Beállítások",
    live_log_title: "ÉLŐ ESEMÉNYNAPLÓ",
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

function applyLanguage(lang) {
  currentLanguage = lang;
  document.getElementById("txt-app-title").innerText = t("app_title");
  document.getElementById("txt-app-subtitle").innerText = t("app_subtitle");
  document.getElementById("tab-btn-log").innerText = t("tab_log");
  document.getElementById("tab-btn-recordings").innerText = t("tab_recordings");
  document.getElementById("tab-btn-settings").innerText = t("tab_settings");
  document.getElementById("txt-live-log").innerText = t("live_log_title");

  const btnRecText = document.getElementById("btn-rec-text");
  if (btnRecText) btnRecText.innerText = isRecording ? t("btn_stop_rec") : t("btn_start_rec");

  const btnRepText = document.getElementById("btn-rep-text");
  if (btnRepText) btnRepText.innerText = isReplaySync ? t("btn_stop_rep") : t("btn_start_rep");
}

// Universal Tauri IPC helper compatible with Tauri v2
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

function appendLog(msg, level = "info") {
  const terminal = document.getElementById("terminal");
  if (!terminal) return;

  const line = document.createElement("div");
  line.className = `log-line ${level}`;

  if (msg.startsWith("[")) {
    line.innerText = msg;
  } else {
    const now = new Date();
    const timeStr = `[${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}:${String(now.getSeconds()).padStart(2, "0")}]`;
    line.innerHTML = `<span class="log-time">${timeStr}</span> ${msg}`;
  }

  terminal.appendChild(line);
  terminal.scrollTop = terminal.scrollHeight;
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
      appendLog(t("copy_success"), "info");
    } catch (e) {
      appendLog("Failed to copy log to clipboard.", "warn");
    }
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
    updateStatus(t("status_rec_active"));
    await invokeTauri("start_recording_watcher");
  } else {
    isRecording = false;
    btn.classList.remove("active");
    text.innerText = t("btn_start_rec");
    if (pill) {
      pill.innerText = "● IDLE";
      pill.className = "action-card-status";
    }
    updateStatus(t("status_standby"));
    await invokeTauri("stop_recording_watcher");
    setTimeout(loadRecordings, 600);
  }
}

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
    updateStatus(t("status_rep_active"));
    await invokeTauri("start_replay_watcher");
  } else {
    isReplaySync = false;
    btn.classList.remove("active");
    text.innerText = t("btn_start_rep");
    if (pill) {
      pill.innerText = "● IDLE";
      pill.className = "action-card-status";
    }
    updateStatus(t("status_standby"));
    await invokeTauri("stop_replay_watcher");
  }
}

function updateStatus(msg) {
  const banner = document.getElementById("status-text");
  if (banner) banner.innerText = msg;
}

function updateVolume(val) {
  const volInt = parseInt(val, 10);
  const disp = `${volInt}%`;
  
  const v1 = document.getElementById("vol-display");
  if (v1) v1.innerText = disp;

  const v2 = document.getElementById("quick-vol-display");
  if (v2) v2.innerText = disp;

  const sl1 = document.getElementById("cfg-volume");
  if (sl1) sl1.value = volInt;

  const sl2 = document.getElementById("quick-vol");
  if (sl2) sl2.value = volInt;

  invokeTauri("set_replay_volume", { volume: volInt });
  if (currentConfig) {
    currentConfig.replay_volume = volInt;
  }
}

function onQuickMicChange(val) {
  const cfgMic = document.getElementById("cfg-mic");
  if (cfgMic) cfgMic.value = val;
  if (currentConfig) {
    currentConfig.mic_device_name = val;
    invokeTauri("save_config", { config: currentConfig });
  }
}

function toggleMute() {
  const slider = document.getElementById("quick-vol") || document.getElementById("cfg-volume");
  if (!isMuted) {
    previousVolume = parseInt(slider.value, 10) || 100;
    updateVolume(0);
    isMuted = true;
    appendLog("Replay voice muted (M)", "info");
  } else {
    updateVolume(previousVolume || 100);
    isMuted = false;
    appendLog("Replay voice unmuted (M)", "info");
  }
}

async function loadRecordings() {
  const list = document.getElementById("recordings-list");
  if (!list) return;

  const recordings = await invokeTauri("get_recordings");
  if (!recordings || recordings.length === 0) {
    list.innerHTML = `<div class="empty-state">${t("recordings_empty")}</div>`;
    document.getElementById("recordings-count-text").innerText = "Recordings: 0 files (0.0 MB)";
    return;
  }

  let totalBytes = 0;
  recordings.forEach(r => {
    const mb = parseFloat(r.size_str) || 0;
    totalBytes += mb;
  });

  document.getElementById("recordings-count-text").innerText = `Recordings: ${recordings.length} files (${totalBytes.toFixed(1)} MB)`;

  list.innerHTML = "";
  recordings.forEach(rec => {
    const item = document.createElement("div");
    item.className = "recording-card";
    item.innerHTML = `
      <div class="recording-info">
        <div class="rec-title">${rec.filename}</div>
        <div class="rec-meta">${rec.size_str} &bull; ${rec.date_str}</div>
      </div>
      <div class="rec-actions">
        <button class="btn-small" onclick="playAudio('${rec.filename}')">▶ Play</button>
        <button class="btn-small" style="color: #E84057;" onclick="deleteAudio('${rec.filename}')">🗑 Delete</button>
      </div>
    `;
    list.appendChild(item);
  });
}

async function playAudio(filename) {
  await invokeTauri("play_recording", { filename });
}

async function deleteAudio(filename) {
  if (confirm(t("delete_confirm", { file: filename }))) {
    await invokeTauri("delete_recording", { filename });
    loadRecordings();
  }
}

async function openRecordingsFolder() {
  await invokeTauri("open_recordings_folder");
}

async function loadConfig() {
  const cfg = await invokeTauri("get_config");
  if (cfg) {
    currentConfig = cfg;
    const folderInput = document.getElementById("cfg-folder");
    if (folderInput && cfg.record_folder) {
      folderInput.value = cfg.record_folder;
    }

    if (cfg.replay_volume !== undefined) {
      updateVolume(cfg.replay_volume);
    }

    const autoChamp = document.getElementById("cfg-auto-champ");
    if (autoChamp && cfg.auto_start_champ_select !== undefined) {
      autoChamp.checked = cfg.auto_start_champ_select;
    }

    const autoRep = document.getElementById("cfg-auto-replay");
    if (autoRep && cfg.auto_start_replay !== undefined) {
      autoRep.checked = cfg.auto_start_replay;
    }

    const minTray = document.getElementById("cfg-minimize-tray");
    if (minTray && cfg.minimize_to_tray !== undefined) {
      minTray.checked = cfg.minimize_to_tray;
    }

    const cloudUrl = document.getElementById("cfg-cloud-url");
    if (cloudUrl && cfg.remote_server_url) {
      cloudUrl.value = cfg.remote_server_url;
    }

    const langSelect = document.getElementById("cfg-lang");
    if (langSelect && cfg.language) {
      langSelect.value = cfg.language;
      applyLanguage(cfg.language);
    }
  }
}

async function loadAudioDevices() {
  const devices = await invokeTauri("get_audio_devices");
  const selects = [document.getElementById("cfg-mic"), document.getElementById("quick-mic")];
  
  selects.forEach(select => {
    if (devices && select && devices.length > 0) {
      const currentVal = select.value;
      select.innerHTML = "";
      devices.forEach((dev) => {
        const opt = document.createElement("option");
        opt.value = dev;
        opt.innerText = dev;
        select.appendChild(opt);
      });
      if (currentVal && devices.includes(currentVal)) {
        select.value = currentVal;
      } else if (currentConfig && currentConfig.mic_device_name && devices.includes(currentConfig.mic_device_name)) {
        select.value = currentConfig.mic_device_name;
      }
    }
  });
}

async function checkReplayApi() {
  const isEnabled = await invokeTauri("check_replay_api_status");
  const badge = document.getElementById("api-status-badge");
  if (badge) {
    if (isEnabled) {
      badge.innerText = "Status: Enabled in game.cfg ✓";
      badge.style.color = "var(--green-ok)";
    } else {
      badge.innerText = "Status: Not Enabled";
      badge.style.color = "var(--text-muted)";
    }
  }
}

async function triggerEnableReplayApi() {
  const res = await invokeTauri("enable_replay_api");
  if (res) {
    alert(res);
    checkReplayApi();
  }
}

function markSettingsChanged() {
  // Visual feedback
}

function changeLanguage(lang) {
  applyLanguage(lang);
  if (currentConfig) {
    currentConfig.language = lang;
  }
}

async function saveSettings() {
  if (!currentConfig) {
    currentConfig = await invokeTauri("get_config") || {};
  }

  currentConfig.record_folder = document.getElementById("cfg-folder").value.trim();
  currentConfig.replay_volume = parseInt(document.getElementById("cfg-volume").value, 10);
  currentConfig.auto_start_champ_select = document.getElementById("cfg-auto-champ").checked;
  currentConfig.auto_start_replay = document.getElementById("cfg-auto-replay").checked;
  currentConfig.minimize_to_tray = document.getElementById("cfg-minimize-tray").checked;
  currentConfig.remote_server_url = document.getElementById("cfg-cloud-url").value.trim();
  
  const cloudKey = document.getElementById("cfg-cloud-key").value.trim();
  if (cloudKey && !cloudKey.startsWith("••••")) {
    currentConfig.remote_api_key = cloudKey;
  }
  
  currentConfig.language = document.getElementById("cfg-lang").value;

  const micSelect = document.getElementById("cfg-mic");
  if (micSelect) {
    currentConfig.mic_device_name = micSelect.value;
  }

  await invokeTauri("save_config", { config: currentConfig });
  appendLog("Configuration saved successfully.", "info");
}

// Global hotkey 'M' for replay mute/unmute
window.addEventListener("keydown", (e) => {
  if ((e.key === "m" || e.key === "M") && document.activeElement.tagName !== "INPUT") {
    toggleMute();
  }
});

// Periodic log streamer (every 200ms)
setInterval(async () => {
  const logs = await invokeTauri("get_logs");
  if (logs && logs.length > 0) {
    logs.forEach(msg => appendLog(msg));
  }
}, 200);

// Live VU Audio Meter loop (every 50ms)
setInterval(async () => {
  const lv = await invokeTauri("get_audio_levels");
  if (lv) {
    // Smooth ballistic interpolation (mimicking Python main.py)
    if (lv.app_level > smoothApp) {
      smoothApp = smoothApp * 0.35 + lv.app_level * 0.65;
    } else {
      smoothApp = Math.max(0.0, smoothApp * 0.84);
    }

    if (lv.mic_level > smoothMic) {
      smoothMic = smoothMic * 0.35 + lv.mic_level * 0.65;
    } else {
      smoothMic = Math.max(0.0, smoothMic * 0.84);
    }

    const appPct = Math.min(100, Math.round(smoothApp * 100));
    const micPct = Math.min(100, Math.round(smoothMic * 100));

    const mApp = document.getElementById("meter-app");
    if (mApp) mApp.style.width = `${appPct}%`;

    const mMic = document.getElementById("meter-mic");
    if (mMic) mMic.style.width = `${micPct}%`;
  }
}, 50);

// Status polling (every 1000ms)
setInterval(async () => {
  const state = await invokeTauri("get_status");
  if (state) {
    const pillLeague = document.getElementById("pill-league");
    if (state.league_running) {
      pillLeague.className = "health-pill online";
      if (state.phase && state.phase !== "Offline") {
        pillLeague.querySelector(".text").innerText = `League: ${state.phase}`;
      } else {
        pillLeague.querySelector(".text").innerText = state.in_game ? "League: In-Game" : "League: Client Active";
      }
    } else {
      pillLeague.className = "health-pill";
      pillLeague.querySelector(".text").innerText = "League: Idle";
    }

    // Sync button state if auto-started
    const btnRec = document.getElementById("btn-rec");
    const txtRec = document.getElementById("btn-rec-text");
    const recPill = document.getElementById("rec-status-pill");
    if (state.is_recording_watcher && !isRecording) {
      isRecording = true;
      btnRec.classList.add("active");
      txtRec.innerText = t("btn_stop_rec");
      if (recPill) {
        recPill.innerText = "● RECORDING";
        recPill.className = "action-card-status rec";
      }
      updateStatus(t("status_rec_active"));
    } else if (!state.is_recording_watcher && isRecording) {
      isRecording = false;
      btnRec.classList.remove("active");
      txtRec.innerText = t("btn_start_rec");
      if (recPill) {
        recPill.innerText = "● IDLE";
        recPill.className = "action-card-status";
      }
      updateStatus(t("status_standby"));
    }

    const btnRep = document.getElementById("btn-rep");
    const txtRep = document.getElementById("btn-rep-text");
    const repPill = document.getElementById("rep-status-pill");
    if (state.is_replay_watcher && !isReplaySync) {
      isReplaySync = true;
      btnRep.classList.add("active");
      txtRep.innerText = t("btn_stop_rep");
      if (repPill) {
        repPill.innerText = "● SYNCING";
        repPill.className = "action-card-status active";
      }
      updateStatus(t("status_rep_active"));
    } else if (!state.is_replay_watcher && isReplaySync) {
      isReplaySync = false;
      btnRep.classList.remove("active");
      txtRep.innerText = t("btn_start_rep");
      if (repPill) {
        repPill.innerText = "● IDLE";
        repPill.className = "action-card-status";
      }
      updateStatus(t("status_standby"));
    }

    if (state.match_clock) {
      document.getElementById("match-clock").innerText = `Match Time: ${state.match_clock}`;
    }
  }
}, 1000);

// On page load
window.addEventListener("DOMContentLoaded", async () => {
  // Immediately poll initial logs so live event log is populated at startup
  const initLogs = await invokeTauri("get_logs");
  if (initLogs && initLogs.length > 0) {
    initLogs.forEach(msg => appendLog(msg));
  }
  loadConfig();
  loadAudioDevices();
  checkReplayApi();
  loadRecordings();
});
