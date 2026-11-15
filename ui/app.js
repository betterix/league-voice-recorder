let isRecording = false;
let isReplaySync = false;
let currentConfig = null;
let currentLanguage = "en";
let isMuted = false;
let previousVolume = 100;

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

// Tauri IPC helper
async function invokeTauri(cmd, args = {}) {
  if (window.__TAURI__ && window.__TAURI__.core && window.__TAURI__.core.invoke) {
    try {
      return await window.__TAURI__.core.invoke(cmd, args);
    } catch (err) {
      appendLog(`[IPC Error] ${cmd}: ${err}`, "warn");
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

  if (!isRecording) {
    isRecording = true;
    btn.classList.add("active");
    text.innerText = t("btn_stop_rec");
    updateStatus(t("status_rec_active"));
    await invokeTauri("start_recording_watcher");
  } else {
    isRecording = false;
    btn.classList.remove("active");
    text.innerText = t("btn_start_rec");
    updateStatus(t("status_standby"));
    await invokeTauri("stop_recording_watcher");
  }
}

async function toggleReplayWatcher() {
  const btn = document.getElementById("btn-rep");
  const text = document.getElementById("btn-rep-text");

  if (!isReplaySync) {
    isReplaySync = true;
    btn.classList.add("active");
    text.innerText = t("btn_stop_rep");
    updateStatus(t("status_rep_active"));
    await invokeTauri("start_replay_watcher");
  } else {
    isReplaySync = false;
    btn.classList.remove("active");
    text.innerText = t("btn_start_rep");
    updateStatus(t("status_standby"));
    await invokeTauri("stop_replay_watcher");
  }
}

function updateStatus(msg) {
  const banner = document.getElementById("status-text");
  if (banner) banner.innerText = msg;
}

function updateVolume(val) {
  document.getElementById("vol-display").innerText = `${val}%`;
  invokeTauri("set_replay_volume", { volume: parseInt(val, 10) });
  if (currentConfig) {
    currentConfig.replay_volume = parseInt(val, 10);
  }
}

function toggleMute() {
  const slider = document.getElementById("cfg-volume");
  if (!isMuted) {
    previousVolume = parseInt(slider.value, 10);
    slider.value = 0;
    updateVolume(0);
    isMuted = true;
    appendLog("Replay voice muted (M)", "info");
  } else {
    slider.value = previousVolume || 100;
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

    const volSlider = document.getElementById("cfg-volume");
    if (volSlider && cfg.replay_volume !== undefined) {
      volSlider.value = cfg.replay_volume;
      document.getElementById("vol-display").innerText = `${cfg.replay_volume}%`;
    }

    const autoChamp = document.getElementById("cfg-auto-champ");
    if (autoChamp && cfg.auto_start_champ_select !== undefined) {
      autoChamp.checked = cfg.auto_start_champ_select;
    }

    const autoRep = document.getElementById("cfg-auto-replay");
    if (autoRep && cfg.auto_start_replay !== undefined) {
      autoRep.checked = cfg.auto_start_replay;
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
  const micSelect = document.getElementById("cfg-mic");
  if (devices && micSelect && devices.length > 0) {
    micSelect.innerHTML = "";
    devices.forEach((dev, idx) => {
      const opt = document.createElement("option");
      opt.value = dev;
      opt.innerText = dev;
      micSelect.appendChild(opt);
    });
  }
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
  // Can provide visual cue
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

// Periodic log streamer (every 250ms)
setInterval(async () => {
  const logs = await invokeTauri("get_logs");
  if (logs && logs.length > 0) {
    logs.forEach(msg => appendLog(msg));
  }
}, 250);

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
    if (state.is_recording_watcher && !isRecording) {
      isRecording = true;
      btnRec.classList.add("active");
      txtRec.innerText = t("btn_stop_rec");
      updateStatus(t("status_rec_active"));
    } else if (!state.is_recording_watcher && isRecording) {
      isRecording = false;
      btnRec.classList.remove("active");
      txtRec.innerText = t("btn_start_rec");
      updateStatus(t("status_standby"));
    }

    const btnRep = document.getElementById("btn-rep");
    const txtRep = document.getElementById("btn-rep-text");
    if (state.is_replay_watcher && !isReplaySync) {
      isReplaySync = true;
      btnRep.classList.add("active");
      txtRep.innerText = t("btn_stop_rep");
      updateStatus(t("status_rep_active"));
    } else if (!state.is_replay_watcher && isReplaySync) {
      isReplaySync = false;
      btnRep.classList.remove("active");
      txtRep.innerText = t("btn_start_rep");
      updateStatus(t("status_standby"));
    }

    if (state.match_clock) {
      document.getElementById("match-clock").innerText = `Match Time: ${state.match_clock}`;
    }
  }
}, 1000);

// On page load
window.addEventListener("DOMContentLoaded", () => {
  loadConfig();
  loadAudioDevices();
  checkReplayApi();
  loadRecordings();
});
