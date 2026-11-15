let isRecording = false;
let isReplaySync = false;

// Tauri IPC helper
async function invokeTauri(cmd, args = {}) {
  if (window.__TAURI__ && window.__TAURI__.core && window.__TAURI__.core.invoke) {
    try {
      return await window.__TAURI__.core.invoke(cmd, args);
    } catch (err) {
      appendLog(`Error invoking ${cmd}: ${err}`, "warn");
      return null;
    }
  } else {
    // Fallback if running outside Tauri webview
    return null;
  }
}

function appendLog(msg, level = "info") {
  const terminal = document.getElementById("terminal");
  if (!terminal) return;

  const now = new Date();
  const timeStr = `[${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}:${String(now.getSeconds()).padStart(2, "0")}]`;

  const line = document.createElement("div");
  line.className = `log-line ${level}`;
  line.innerHTML = `<span class="log-time">${timeStr}</span> ${msg}`;
  terminal.appendChild(line);
  terminal.scrollTop = terminal.scrollHeight;
}

function clearLog() {
  const terminal = document.getElementById("terminal");
  if (terminal) terminal.innerHTML = "";
}

function switchTab(tabId) {
  document.querySelectorAll(".tab-btn").forEach(btn => btn.classList.remove("active"));
  document.querySelectorAll(".tab-content").forEach(c => c.classList.remove("active"));

  const targetTab = document.getElementById(`tab-${tabId}`);
  if (targetTab) targetTab.classList.add("active");

  const activeBtn = Array.from(document.querySelectorAll(".tab-btn")).find(b => b.getAttribute("onclick").includes(tabId));
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
    text.innerText = "Stop Recording Watcher";
    appendLog("🔴 Recording watcher started. Polling :2999/liveclientdata/allgamedata (10ms-50ms)...", "rec");
    updateStatus("Recording watcher active. Waiting for League match...");
    await invokeTauri("start_recording_watcher");
  } else {
    isRecording = false;
    btn.classList.remove("active");
    text.innerText = "Start Recording Watcher";
    appendLog("⏹ Recording watcher stopped.", "info");
    updateStatus("Standby.");
    await invokeTauri("stop_recording_watcher");
  }
}

async function toggleReplayWatcher() {
  const btn = document.getElementById("btn-rep");
  const text = document.getElementById("btn-rep-text");

  if (!isReplaySync) {
    isReplaySync = true;
    btn.classList.add("active");
    text.innerText = "Stop Replay Auto-Sync";
    appendLog("▶ Replay synchronizer started. Watching for active ROFL replays & sync API...", "sync");
    updateStatus("Replay synchronizer active. Open any .rofl replay in League.");
    await invokeTauri("start_replay_watcher");
  } else {
    isReplaySync = false;
    btn.classList.remove("active");
    text.innerText = "Start Replay Auto-Sync";
    appendLog("⏹ Replay synchronizer stopped.", "info");
    updateStatus("Standby.");
    await invokeTauri("stop_replay_watcher");
  }
}

function updateStatus(msg) {
  const banner = document.getElementById("status-text");
  if (banner) banner.innerText = msg;
}

function updateVolume(val) {
  document.getElementById("vol-display").innerText = `${val}%`;
  invokeTauri("set_volume", { volume: parseInt(val) / 100 });
}

async function loadRecordings() {
  const list = document.getElementById("recordings-list");
  if (!list) return;

  const recordings = await invokeTauri("get_recordings");
  if (!recordings || recordings.length === 0) {
    list.innerHTML = `<div class="empty-state">No recordings found in recordings directory.</div>`;
    return;
  }

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
      </div>
    `;
    list.appendChild(item);
  });
}

async function playAudio(filename) {
  appendLog(`Playing ${filename} using native Rust audio engine...`, "sync");
  await invokeTauri("play_recording", { filename });
}

async function openRecordingsFolder() {
  await invokeTauri("open_recordings_folder");
}

async function saveSettings() {
  const folder = document.getElementById("cfg-folder").value;
  await invokeTauri("save_config", { recordFolder: folder });
  appendLog("Configuration saved successfully.", "info");
}

// Initial status polling
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
      txtRec.innerText = "Stop Recording Watcher";
      appendLog("🔴 Auto-started recording watcher (ChampSelect / InProgress detected).", "rec");
      updateStatus("Recording watcher active. Capturing match...");
    } else if (!state.is_recording_watcher && isRecording) {
      isRecording = false;
      btnRec.classList.remove("active");
      txtRec.innerText = "Start Recording Watcher";
      updateStatus("Standby.");
    }

    const btnRep = document.getElementById("btn-rep");
    const txtRep = document.getElementById("btn-rep-text");
    if (state.is_replay_watcher && !isReplaySync) {
      isReplaySync = true;
      btnRep.classList.add("active");
      txtRep.innerText = "Stop Replay Auto-Sync";
      appendLog("▶ Auto-started replay synchronizer (ROFL replay detected).", "sync");
      updateStatus("Replay synchronizer active.");
    } else if (!state.is_replay_watcher && isReplaySync) {
      isReplaySync = false;
      btnRep.classList.remove("active");
      txtRep.innerText = "Start Replay Auto-Sync";
      updateStatus("Standby.");
    }

    if (state.match_clock) {
      document.getElementById("match-clock").innerText = `Match Time: ${state.match_clock}`;
    }
  }
}, 1000);
