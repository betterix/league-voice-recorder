/**
 * LEAGUE VOICE RECORDER & REPLAY SYNCHRONIZER
 * Official Interactive Web Showcase
 */

// ============================================================================
// 1. I18N LOCALIZATION DICTIONARY (EN & HU)
// ============================================================================
const i18n = {
  en: {
    nav_features: "Features",
    nav_how_it_works: "How It Works",
    nav_simulator: "Live Simulator",
    nav_setup: "Setup Guide",
    nav_architecture: "Architecture",
    nav_faq: "FAQ",
    nav_download: "Download v0.3.2",

    hero_badge: "v0.3.2 Released • Windows 10/11 64-bit • Open Source",
    hero_title_1: "Frame-Accurate Scrim Comms &",
    hero_title_2: "Replay Synchronization",
    hero_title_3: "For League of Legends",
    hero_subtitle: "Zero-latency automated Discord voice recording coupled with frame-accurate League of Legends replay audio lock. Scrub at 0.5x, 1x, 2x, or 4x without losing comms sync.",
    hero_cta_download: "Download Standalone (.ZIP)",
    hero_cta_github: "View on GitHub",
    hero_cta_sim: "Try Interactive App",
    hero_meta_vanguard: "100% Vanguard & Riot API Safe",
    hero_meta_drivers: "No Virtual Audio Cables Needed",
    hero_meta_license: "Free & Open Source (GPL-3.0)",

    health_discord: "Discord IPC:",
    health_league: "League Client:",
    health_vlc: "VLC Engine:",
    health_ffmpeg: "FFmpeg:",
    health_ready: "🟢 Ready",
    health_connected: "🟢 Connected",
    health_running: "🟢 Running",
    health_refresh: "Refresh",

    sim_tag: "INTERACTIVE DESKTOP APP PREVIEW",
    sim_title: "Test Drive League Voice Recorder",
    sim_desc: "Experience the real desktop GUI right in your browser. Start a mock scrim recording, test Discord mute synchronization, scrub replay timelines, and inspect recordings.",
    
    sim_rec_title: "🎙️ SCRIM RECORDER",
    sim_rec_idle: "● IDLE (Click Start to monitor)",
    sim_rec_listening: "● LISTENING: Waiting for match...",
    sim_rec_recording: "● RECORDING: Live Match (14:32)",
    sim_rec_start_btn: "🔴 Start Recording Watcher",
    sim_rec_stop_btn: "⏹ Stop Recording Watcher",
    sim_target_scene: "🎙️ Target: Discord + Mic",
    sim_discord_audio: "Discord Audio Loopback",
    sim_mic_audio: "Microphone Input (Default)",
    sim_test_mute: "Simulate Discord Mute",
    sim_muted_state: "🔇 MUTED (Silence byte injection active)",

    sim_rep_title: "🎬 REPLAY AUTO-SYNC",
    sim_rep_idle: "● IDLE (Click Start to detect .rofl)",
    sim_rep_syncing: "● SYNCING: Match #3194821034",
    sim_rep_start_btn: "▶ Start Replay Auto-Sync",
    sim_rep_stop_btn: "⏹ Stop Replay Auto-Sync",
    sim_rep_vol: "Replay Volume:",
    sim_rep_hotkey: "Press M to Mute/Unmute",
    sim_rep_hint_auto: "Auto-detects running League replay process and locks VLC playback clock.",
    sim_rep_hint_m: "Press 'M' hotkey while watching to mute/unmute synced voice comms.",

    sim_tab_log: "📋 Activity Log",
    sim_tab_recordings: "📁 Scrim Recordings",
    sim_tab_settings: "⚙️ Settings",
    sim_mic_select: "Microphone Device:",
    sim_settings_update: "Software Updates:",
    sim_settings_update_status: "● You are running the latest version (v0.3.2)",
    sim_settings_update_btn: "🔄 Check for Updates",

    engines_tag: "THE DUAL-ENGINE ARCHITECTURE",
    engines_title: "Engineered For Competitive Esports",
    engines_desc: "Two autonomous, ultra-reliable engines working in tandem to eliminate manual recording and review friction forever.",
    
    engine_rec_badge: "ENGINE 1 • RECORDING",
    engine_rec_title: "Zero-Latency Scrim Watcher",
    engine_rec_desc: "Directly monitors League's Gameflow and Live Client Data APIs. Captures your Discord call and player microphone with sample-accurate timestamping.",
    engine_rec_s1_title: "Sub-Millisecond Match Start",
    engine_rec_s1_desc: "Attaches instantly when the game launches. Detects live games vs. replays with process guard.",
    engine_rec_s2_title: "Discord Local IPC Live Mute Sync",
    engine_rec_s2_desc: "Connects via Windows named pipe. When you mute or deafen in Discord, silence bytes are injected to preserve timeline lock.",
    engine_rec_s3_title: "Nexus Auto-Finalization",
    engine_rec_s3_desc: "Nexus explodes -> saves compressed <GameID>.mp3 aligned precisely to 00:00:00 without clicking any buttons.",

    engine_rep_badge: "ENGINE 2 • REPLAY SYNCHRONIZATION",
    engine_rep_title: "Frame-Accurate Replay Lock",
    engine_rep_desc: "Hooks into the League Replay Playback API (:2999/replay/playback) and synchronizes audio via VLC Media Player.",
    engine_rep_s1_title: "Instant .ROFL Detection",
    engine_rep_s1_desc: "Opening any match replay in League automatically locates the matching <GameID>.mp3 in your recordings.",
    engine_rep_s2_title: "Dynamic Variable Rate Sync (0.5x - 8x)",
    engine_rep_s2_desc: "Fast forward or rewind during review; VLC dynamically adjusts voice playback speed in microsecond intervals.",
    engine_rep_s3_title: "In-Game Observer Mute ('M' Hotkey)",
    engine_rep_s3_desc: "Press 'M' while watching in League to instantly silence voice comms to hear important in-game audio cues.",

    timeline_tag: "TIMELINE PRECISION",
    timeline_title: "Why Frame-Accuracy Matters",
    timeline_desc: "Manual recordings always drift. League Voice Recorder uses FFmpeg sample math to lock audio frame-by-frame.",
    timeline_track_game: "In-Game Scrim Timeline",
    timeline_track_bad: "Standard Manual Recording (Disaster)",
    timeline_track_good: "League Voice Recorder (Frame Locked)",

    feat_tag: "CORE CAPABILITIES",
    feat_title: "Built For Scrim Perfection",
    feat_1_title: "Zero Third-Party Drivers",
    feat_1_desc: "Direct WASAPI loopback capture. No virtual audio cables, no Voicemeeter crashes, no audio driver conflicts.",
    feat_2_title: "Discord IPC Integration",
    feat_2_desc: "Uses Discord's first-party StreamKit Overlay RPC client ID. Clean, authorized, local named pipe communication.",
    feat_3_title: "100% Vanguard Safe",
    feat_3_desc: "Zero memory injection, zero DLL hooking. Communicates strictly with Riot's approved local HTTP endpoints.",
    feat_4_title: "Replay Guard Protection",
    feat_4_desc: "Inspects running process trees to ensure you never accidentally record over an existing file when reviewing replays.",
    feat_5_title: "Integrated Scrim Browser",
    feat_5_desc: "Browse, audit, and play recordings directly inside the app with instant metadata, file sizes, and timestamps.",
    feat_6_title: "Drop-In Translations (i18n)",
    feat_6_desc: "Full internationalization support. English & Hungarian built-in; add any language via a simple JSON file.",

    setup_tag: "INSTALLATION & CONFIGURATION",
    setup_title: "Get Running in Under 2 Minutes",
    setup_desc: "Available as a precompiled standalone Windows executable or run directly from source with uv.",
    setup_tab_release: "1. Standalone Release",
    setup_tab_winget: "2. Winget Prerequisites",
    setup_tab_gamecfg: "3. Enable Replay API",
    setup_tab_discord: "4. Discord Auth",
    setup_tab_source: "5. Run from Source",

    faq_tag: "FREQUENTLY ASKED QUESTIONS",
    faq_title: "Answers & Technical Details",

    cta_title: "Ready To Elevate Your Scrim Reviews?",
    cta_desc: "Download League Voice Recorder for Windows 10 & 11 today. Completely free, open source, and built for competitive League.",
    cta_btn_download: "Download v0.3.2 Release",
    cta_btn_repo: "Star on GitHub",

    footer_desc: "Automated Discord voice comms capture & frame-accurate replay audio synchronization for League of Legends esports teams.",
    footer_disclaimer: "League Voice Recorder is an independent open-source project and is not endorsed by, directly affiliated with, maintained, authorized, or sponsored by Riot Games, Inc. League of Legends is a trademark or registered trademark of Riot Games, Inc."
  },

  hu: {
    nav_features: "Funkciók",
    nav_how_it_works: "Hogyan működik",
    nav_simulator: "Élő Szimulátor",
    nav_setup: "Telepítés",
    nav_architecture: "Architektúra",
    nav_faq: "GYIK",
    nav_download: "Letöltés v0.3.2",

    hero_badge: "v0.3.2 Megjelent • Windows 10/11 64-bit • Nyílt forráskód",
    hero_title_1: "Képkocka-Pontos Scrim Hang &",
    hero_title_2: "Visszajátszás Szinkronizáció",
    hero_title_3: "League of Legends-hez",
    hero_subtitle: "Zéró késleltetésű automatikus Discord hangrögzítés és képkocka-pontos League visszajátszás szinkronizáció. Tekerj 0.5x, 1x, 2x vagy 4x sebességgel anélkül, hogy a hang elcsúszna.",
    hero_cta_download: "Önálló Letöltés (.ZIP)",
    hero_cta_github: "Megtekintés GitHubon",
    hero_cta_sim: "Interaktív Alkalmazás Kipróbálása",
    hero_meta_vanguard: "100% Vanguard & Riot API Biztonságos",
    hero_meta_drivers: "Nincs szükség virtuális audiokábelre",
    hero_meta_license: "Ingyenes és Nyílt Forráskód (GPL-3.0)",

    health_discord: "Discord IPC:",
    health_league: "League Kliens:",
    health_vlc: "VLC Motor:",
    health_ffmpeg: "FFmpeg:",
    health_ready: "🟢 Kész",
    health_connected: "🟢 Csatlakozva",
    health_running: "🟢 Fut",
    health_refresh: "Frissítés",

    sim_tag: "INTERAKTÍV ASZTALI ALKALMAZÁS ELŐNÉZET",
    sim_title: "Próbáld ki a League Voice Recordert",
    sim_desc: "Tapasztald meg a valódi asztali kezelőfelületet a böngésződben. Indíts el egy próbafelvételt, teszteld a Discord némítás szinkront, tekerd a visszajátszási idősávot és nézd meg a felvételeket.",
    
    sim_rec_title: "🎙️ SCRIM FELVÉTEL",
    sim_rec_idle: "● KÉSZENLÉT (Kattints az indításhoz)",
    sim_rec_listening: "● FIGYELÉS: Várakozás meccsre...",
    sim_rec_recording: "● FELVÉTEL: Élő mérkőzés (14:32)",
    sim_rec_start_btn: "🔴 Felvétel figyelő indítása",
    sim_rec_stop_btn: "⏹ Felvétel figyelő leállítása",
    sim_target_scene: "🎙️ Cél: Discord + Mikrofon",
    sim_discord_audio: "Discord Hang (WASAPI Loopback)",
    sim_mic_audio: "Mikrofon Bemenet (Alapértelmezett)",
    sim_test_mute: "Discord némítás szimulálása",
    sim_muted_state: "🔇 NÉMÍTVA (Csendbájt-injektálás aktív)",

    sim_rep_title: "🎬 REPLAY AUTO-SZINKRON",
    sim_rep_idle: "● KÉSZENLÉT (Kattints az indításhoz)",
    sim_rep_syncing: "● SZINKRONBAN: Meccs #3194821034",
    sim_rep_start_btn: "▶ Visszajátszás szinkronizáló indítása",
    sim_rep_stop_btn: "⏹ Replay Auto-Sync leállítása",
    sim_rep_vol: "Visszajátszási hangerő:",
    sim_rep_hotkey: "M billentyű: Némítás / Visszakapcsolás",
    sim_rep_hint_auto: "Automatikusan felismeri a futó League replay-t és zárja a VLC órát.",
    sim_rep_hint_m: "Nyomd meg az 'M' billentyűt nézői módban a hang némításához.",

    sim_tab_log: "📋 Tevékenységnapló",
    sim_tab_recordings: "📁 Scrim Felvételek",
    sim_tab_settings: "⚙️ Beállítások",
    sim_mic_select: "Mikrofon Eszköz:",
    sim_settings_update: "Szoftverfrissítések:",
    sim_settings_update_status: "● A legfrissebb verziót használod (v0.3.2)",
    sim_settings_update_btn: "🔄 Frissítések keresése",

    engines_tag: "A KETTŐS MOTOR ARCHITEKTÚRA",
    engines_title: "Versenyszintű Esportra Tervezve",
    engines_desc: "Két független, rendkívül megbízható motor működik együtt, megszüntetve a manuális felvételkészítés és elemzés minden bosszúságát.",
    
    engine_rec_badge: "1. MOTOR • FELVÉTEL",
    engine_rec_title: "Zéró Késleltetésű Scrim Figyelő",
    engine_rec_desc: "Közvetlenül figyeli a League Gameflow és Live Client Data API-kat. Rögzíti a Discord hívást és a mikrofont mintapontos időzítéssel.",
    engine_rec_s1_title: "Ezredmásodperces Meccsindítás",
    engine_rec_s1_desc: "A játék elindulásakor azonnal csatlakozik. Folyamatvédelemmel megkülönbözteti az élő játékot a visszajátszástól.",
    engine_rec_s2_title: "Discord Local IPC Élő Némítás Szinkron",
    engine_rec_s2_desc: "Windows named pipe-on keresztül kapcsolódik. Ha Discordban némítasz, csendbájtokat szúr be az idősáv megtartásához.",
    engine_rec_s3_title: "Nexus Auto-Véglegesítés",
    engine_rec_s3_desc: "Felrobban a nexus -> azonnal tömörített <GameID>.mp3-at ment pontosan 00:00:00-hoz igazítva, gombnyomás nélkül.",

    engine_rep_badge: "2. MOTOR • REPLAY SZINKRONIZÁCIÓ",
    engine_rep_title: "Képkocka-Pontos Replay Zárás",
    engine_rep_desc: "Rácsatlakozik a League Replay Playback API-ra (:2999/replay/playback) és VLC-n keresztül szinkronizálja a hangot.",
    engine_rep_s1_title: "Azonnali .ROFL Felismerés",
    engine_rep_s1_desc: "Bármely meccs visszajátszásának megnyitásakor automatikusan megkeresi a hozzá tartozó <GameID>.mp3 fájlt.",
    engine_rep_s2_title: "Dinamikus Változó Sebesség (0.5x - 8x)",
    engine_rep_s2_desc: "Tekerj előre vagy vissza; a VLC mikromásodperces időközökkel igazítja a hangsebességet a képkockákhoz.",
    engine_rep_s3_title: "Játékon Belüli Némítás ('M' Billentyű)",
    engine_rep_s3_desc: "Nyomd meg az 'M' billentyűt a League-ben az azonnali némításhoz, ha fontos in-game hangeffekteket figyelnél.",

    timeline_tag: "IDŐVONAL PONTOSSÁG",
    timeline_title: "Miért Számít a Képkocka-Pontosság?",
    timeline_desc: "A manuális felvételek mindig elcsúsznak. A League Voice Recorder FFmpeg mintamatematikával képkockánként zárja a hangot.",
    timeline_track_game: "Játékbeli Mérkőzés Idővonala",
    timeline_track_bad: "Hagyományos Manuális Felvétel (Katasztrófa)",
    timeline_track_good: "League Voice Recorder (Képkocka-Zárolt)",

    feat_tag: "FŐBB KÉPESSÉGEK",
    feat_title: "Tökéletes Scrim Elemzésre Alkotva",
    feat_1_title: "Zéró Külső Illesztőprogram",
    feat_1_desc: "Közvetlen natív WASAPI rögzítés. Nincs virtuális audiokábel, nincs Voicemeeter összeomlás, nincsenek driver-ütközések.",
    feat_2_title: "Discord IPC Integráció",
    feat_2_desc: "A Discord hivatalos StreamKit Overlay RPC azonosítóját használja. Biztonságos, engedélyezett helyi named pipe kommunikáció.",
    feat_3_title: "100% Vanguard Biztonság",
    feat_3_desc: "Nincs memóriainjekció, nincs DLL-hookolás. Kizárólag a Riot jóváhagyott helyi HTTP végpontjaival kommunikál.",
    feat_4_title: "Replay Guard Védelem",
    feat_4_desc: "Folyamatfa-ellenőrzéssel garantálja, hogy visszajátszások megtekintésekor véletlenül se írd felül a felvételeket.",
    feat_5_title: "Beépített Scrim Kezelő",
    feat_5_desc: "Böngészd és játszd le a rögzített meccseket közvetlenül az appban részletes metaadatokkal és fájlméretekkel.",
    feat_6_title: "Könnyű Bővíthetőség (i18n)",
    feat_6_desc: "Teljes nemzetköziesítés. Angol és Magyar beépítve; bármilyen nyelv hozzáadható egyetlen JSON fájllal.",

    setup_tag: "TELEPÍTÉS ÉS BEÁLLÍTÁS",
    setup_title: "Indulásra Kész Kevesebb Mint 2 Perc Alatt",
    setup_desc: "Elérhető előre lefordított önálló Windows programként (.exe), vagy forrásból futtatva uv segítségével.",
    setup_tab_release: "1. Letöltés (.ZIP)",
    setup_tab_winget: "2. Winget Előfeltételek",
    setup_tab_gamecfg: "3. Replay API Engedélyezés",
    setup_tab_discord: "4. Discord Engedély",
    setup_tab_source: "5. Futtatás Forrásból",

    faq_tag: "GYAKRAN ISMÉTELT KÉRDÉSEK",
    faq_title: "Válaszok és Technikai Részletek",

    cta_title: "Készen Állsz Szintre Emelni a Scrim Elemzést?",
    cta_desc: "Töltsd le a League Voice Recorder-t Windows 10 és 11 rendszerekre még ma. Teljesen ingyenes, nyílt forráskódú és esportra szabott.",
    cta_btn_download: "v0.3.2 Kiadás Letöltése",
    cta_btn_repo: "Csillagozd GitHubon",

    footer_desc: "Automatizált Discord hangrögzítő és képkocka-pontos League of Legends visszajátszás-szinkronizáló esport csapatoknak.",
    footer_disclaimer: "A League Voice Recorder független nyílt forráskódú projekt, és nem áll kapcsolatban a Riot Games, Inc.-vel, nem a Riot Games hagyta jóvá vagy támogatja azt. A League of Legends a Riot Games, Inc. bejegyzett védjegye."
  }
};

let currentLang = localStorage.getItem("lvr_lang") || "en";

function setLanguage(lang) {
  if (!i18n[lang]) return;
  currentLang = lang;
  localStorage.setItem("lvr_lang", lang);

  // Update Buttons
  document.querySelectorAll(".lang-btn").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.lang === lang);
  });

  // Update all [data-i18n] text (preserving icons if present)
  document.querySelectorAll("[data-i18n]").forEach(el => {
    const key = el.dataset.i18n;
    if (i18n[lang][key]) {
      const textSpan = el.querySelector("span");
      if (textSpan && el.querySelector("svg")) {
        textSpan.textContent = i18n[lang][key];
      } else {
        el.textContent = i18n[lang][key];
      }
    }
  });

  // Update attributes like placeholder, title
  document.querySelectorAll("[data-i18n-title]").forEach(el => {
    const key = el.dataset.i18nTitle;
    if (i18n[lang][key]) {
      el.setAttribute("title", i18n[lang][key]);
    }
  });
}

// ============================================================================
// 2. HEXTECH PARTICLE & ENERGY BACKGROUND CANVAS
// ============================================================================
function initHextechCanvas() {
  const canvas = document.getElementById("hextech-canvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");

  let width = (canvas.width = window.innerWidth);
  let height = (canvas.height = window.innerHeight);

  window.addEventListener("resize", () => {
    width = canvas.width = window.innerWidth;
    height = canvas.height = window.innerHeight;
  });

  const particles = [];
  const particleCount = Math.min(width > 768 ? 65 : 25, 75);

  for (let i = 0; i < particleCount; i++) {
    particles.push({
      x: Math.random() * width,
      y: Math.random() * height,
      vx: (Math.random() - 0.5) * 0.45,
      vy: (Math.random() - 0.5) * 0.45,
      radius: Math.random() * 2.2 + 0.8,
      color: Math.random() > 0.45 ? "rgba(10, 200, 185, " : "rgba(200, 170, 110, ",
      alpha: Math.random() * 0.5 + 0.2,
      pulseSpeed: Math.random() * 0.02 + 0.01
    });
  }

  let mouseX = -1000;
  let mouseY = -1000;
  window.addEventListener("mousemove", e => {
    mouseX = e.clientX;
    mouseY = e.clientY;
  });

  function render() {
    ctx.clearRect(0, 0, width, height);

    // Render connecting constellation lines
    for (let i = 0; i < particles.length; i++) {
      for (let j = i + 1; j < particles.length; j++) {
        const dx = particles[i].x - particles[j].x;
        const dy = particles[i].y - particles[j].y;
        const dist = Math.sqrt(dx * dx + dy * dy);

        if (dist < 130) {
          ctx.beginPath();
          ctx.moveTo(particles[i].x, particles[i].y);
          ctx.lineTo(particles[j].x, particles[j].y);
          const lineAlpha = (1 - dist / 130) * 0.18;
          ctx.strokeStyle = `rgba(10, 200, 185, ${lineAlpha})`;
          ctx.lineWidth = 0.8;
          ctx.stroke();
        }
      }
    }

    // Render particles
    for (let i = 0; i < particles.length; i++) {
      const p = particles[i];

      p.x += p.vx;
      p.y += p.vy;

      if (p.x < 0) p.x = width;
      if (p.x > width) p.x = 0;
      if (p.y < 0) p.y = height;
      if (p.y > height) p.y = 0;

      // Mouse repulsion softly
      const mdx = p.x - mouseX;
      const mdy = p.y - mouseY;
      const mdist = Math.sqrt(mdx * mdx + mdy * mdy);
      if (mdist < 100) {
        p.x += (mdx / mdist) * 0.8;
        p.y += (mdy / mdist) * 0.8;
      }

      ctx.beginPath();
      ctx.arc(p.x, p.y, p.radius, 0, Math.PI * 2);
      ctx.fillStyle = p.color + p.alpha + ")";
      ctx.shadowBlur = 8;
      ctx.shadowColor = p.color + "0.6)";
      ctx.fill();
      ctx.shadowBlur = 0;
    }

    requestAnimationFrame(render);
  }

  render();
}

// ============================================================================
// 3. INTERACTIVE DESKTOP APP SIMULATOR STATE & LOGIC
// ============================================================================
const simState = {
  recording: false,
  listening: false,
  discordMuted: false,
  replaySyncing: false,
  replaySpeed: 1.0,
  replaySeconds: 872, // 14:32 in seconds
  replayVolume: 85,
  replayMuted: false,
  activeTab: "log",
  recordings: [
    { id: "EUN1_3194821034", date: "2026-10-31 21:14", duration: "28:45", size: "39.4 MB" },
    { id: "EUW1_4920192841", date: "2026-10-30 19:40", duration: "32:10", size: "44.1 MB" },
    { id: "KR_7810294102",  date: "2026-10-29 18:22", duration: "24:18", size: "33.2 MB" }
  ]
};

// Procedural Web Audio Sound Generator for "Play" simulation
let audioCtx = null;
function playSynthesizedChime(type = "scrim") {
  try {
    if (!audioCtx) {
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    }
    if (audioCtx.state === "suspended") {
      audioCtx.resume();
    }

    const osc = audioCtx.createOscillator();
    const gain = audioCtx.createGain();
    osc.connect(gain);
    gain.connect(audioCtx.destination);

    const now = audioCtx.currentTime;
    if (type === "play") {
      osc.type = "sine";
      osc.frequency.setValueAtTime(440, now);
      osc.frequency.exponentialRampToValueAtTime(880, now + 0.2);
      gain.gain.setValueAtTime(0.12, now);
      gain.gain.exponentialRampToValueAtTime(0.001, now + 0.35);
      osc.start(now);
      osc.stop(now + 0.35);
    } else if (type === "mute") {
      osc.type = "triangle";
      osc.frequency.setValueAtTime(320, now);
      osc.frequency.exponentialRampToValueAtTime(160, now + 0.15);
      gain.gain.setValueAtTime(0.1, now);
      gain.gain.exponentialRampToValueAtTime(0.001, now + 0.18);
      osc.start(now);
      osc.stop(now + 0.18);
    } else if (type === "start") {
      osc.type = "sine";
      osc.frequency.setValueAtTime(523.25, now);
      osc.frequency.setValueAtTime(659.25, now + 0.1);
      gain.gain.setValueAtTime(0.12, now);
      gain.gain.exponentialRampToValueAtTime(0.001, now + 0.25);
      osc.start(now);
      osc.stop(now + 0.25);
    }
  } catch (e) {
    // Web audio not permitted or failed
  }
}

function appendSimLog(msg, level = "info") {
  const logBox = document.getElementById("sim-log-content");
  if (!logBox) return;

  const now = new Date();
  const timeStr = now.toTimeString().split(" ")[0];

  const row = document.createElement("div");
  row.className = "log-entry";
  row.innerHTML = `<span class="log-time">[${timeStr}]</span> <span class="log-msg ${level}">${msg}</span>`;
  logBox.appendChild(row);
  logBox.scrollTop = logBox.scrollHeight;
}

function formatSeconds(sec) {
  const m = Math.floor(sec / 60).toString().padStart(2, "0");
  const s = Math.floor(sec % 60).toString().padStart(2, "0");
  return `${m}:${s}`;
}

function updateSimUI() {
  // Recorder Card
  const recPill = document.getElementById("sim-rec-status-pill");
  const recBtn = document.getElementById("sim-rec-toggle-btn");
  const muteBtn = document.getElementById("sim-test-mute-btn");
  const discordVu = document.getElementById("sim-vu-discord");
  const micVu = document.getElementById("sim-vu-mic");

  if (recPill && recBtn) {
    if (simState.recording) {
      recPill.className = "sim-status-pill recording";
      recPill.innerHTML = `<span class="status-dot" style="background:#ef4444;box-shadow:0 0 8px #ef4444"></span> <span>${i18n[currentLang].sim_rec_recording}</span>`;
      recBtn.textContent = i18n[currentLang].sim_rec_stop_btn;
      recBtn.classList.add("active");
    } else if (simState.listening) {
      recPill.className = "sim-status-pill listening";
      recPill.innerHTML = `<span class="status-dot" style="background:#0ac8b9;box-shadow:0 0 8px #0ac8b9"></span> <span>${i18n[currentLang].sim_rec_listening}</span>`;
      recBtn.textContent = i18n[currentLang].sim_rec_stop_btn;
      recBtn.classList.add("active");
    } else {
      recPill.className = "sim-status-pill";
      recPill.innerHTML = `<span class="status-dot" style="background:#94a3b8"></span> <span>${i18n[currentLang].sim_rec_idle}</span>`;
      recBtn.textContent = i18n[currentLang].sim_rec_start_btn;
      recBtn.classList.remove("active");
    }
  }

  // Mute Button & VU display
  if (muteBtn) {
    muteBtn.classList.toggle("muted-active", simState.discordMuted);
    muteBtn.textContent = simState.discordMuted ? "Unmute Discord (Active)" : i18n[currentLang].sim_test_mute;
  }

  if (simState.discordMuted) {
    if (micVu) micVu.classList.add("muted");
  } else {
    if (micVu) micVu.classList.remove("muted");
  }

  // Replay Card
  const repPill = document.getElementById("sim-rep-status-pill");
  const repBtn = document.getElementById("sim-rep-toggle-btn");
  const repVolSlider = document.getElementById("sim-volume-slider");
  const muteHotkeyBtn = document.getElementById("sim-mute-hotkey-btn");

  if (repPill && repBtn) {
    if (simState.replaySyncing) {
      repPill.className = "sim-status-pill syncing";
      repPill.innerHTML = `<span class="status-dot" style="background:#22c55e;box-shadow:0 0 8px #22c55e"></span> <span>${i18n[currentLang].sim_rep_syncing}</span>`;
      repBtn.textContent = i18n[currentLang].sim_rep_stop_btn;
      repBtn.classList.add("active");
    } else {
      repPill.className = "sim-status-pill";
      repPill.innerHTML = `<span class="status-dot" style="background:#94a3b8"></span> <span>${i18n[currentLang].sim_rep_idle}</span>`;
      repBtn.textContent = i18n[currentLang].sim_rep_start_btn;
      repBtn.classList.remove("active");
    }
  }

  if (repVolSlider) {
    repVolSlider.value = simState.replayVolume;
  }
  if (muteHotkeyBtn) {
    const volIcon = simState.replayVolume >= 70 ? "🔊" : (simState.replayVolume >= 35 ? "🔉" : (simState.replayVolume > 0 ? "🔈" : "🔇"));
    muteHotkeyBtn.classList.toggle("active", simState.replayMuted);
    muteHotkeyBtn.textContent = simState.replayMuted ? "🔇 MUTED (M)" : `${volIcon} ${simState.replayVolume}% [M]`;
  }
}

// Live VU Meter Simulation Loop
function startVuMeters() {
  const discordBar = document.getElementById("sim-vu-discord");
  const micBar = document.getElementById("sim-vu-mic");

  setInterval(() => {
    if (simState.recording || simState.replaySyncing) {
      // Natural speech oscillating randoms
      const discordVol = Math.floor(Math.random() * 45 + (Math.random() > 0.3 ? 35 : 5));
      const micVol = simState.discordMuted ? 0 : Math.floor(Math.random() * 55 + (Math.random() > 0.4 ? 25 : 5));

      if (discordBar) discordBar.style.width = discordVol + "%";
      if (micBar && !simState.discordMuted) micBar.style.width = micVol + "%";
    } else {
      if (discordBar) discordBar.style.width = "4%";
      if (micBar) micBar.style.width = "2%";
    }
  }, 100);
}

function initSimulator() {
  // Recorder Toggle
  const recBtn = document.getElementById("sim-rec-toggle-btn");
  if (recBtn) {
    recBtn.addEventListener("click", () => {
      playSynthesizedChime("start");
      if (simState.recording || simState.listening) {
        simState.recording = false;
        simState.listening = false;
        appendSimLog("⏹ Match ended — stopping recording and finalizing audio...", "info");
        setTimeout(() => {
          const newId = "EUN1_" + Math.floor(3000000000 + Math.random() * 900000000);
          appendSimLog(`🎵 Converting to MP3: ${newId}.mp3 (time aligned to 00:00:00)...`, "info");
          appendSimLog(`✅ Audio saved successfully: Documents/League Voice Recorder/recordings/${newId}.mp3`, "success");
          simState.recordings.unshift({
            id: newId,
            date: "Just now",
            duration: "18:42",
            size: "25.6 MB"
          });
          renderRecordingsList();
          updateSimUI();
        }, 600);
      } else {
        simState.listening = true;
        appendSimLog("▶ [UI] Starting recording watcher...", "info");
        appendSimLog("🎙️ League voice recorder started. Connecting to LCU lockfile & Discord IPC...", "info");
        appendSimLog("● LISTENING: Waiting for live match to launch...", "warn");
        updateSimUI();

        // Simulate game match start in 1.8 seconds
        setTimeout(() => {
          if (simState.listening) {
            simState.listening = false;
            simState.recording = true;
            appendSimLog("🎯 Match launched! Riot Live Client Data API (:2999) detected active game.", "success");
            appendSimLog("🔴 Match started (GameTime: 0.000s) — direct WASAPI recording started.", "success");
            appendSimLog("⏱ Time alignment buffer prepended (+0.000s). Frame accurate 00:00:00 lock established.", "info");
            updateSimUI();
          }
        }, 1800);
      }
      updateSimUI();
    });
  }

  // Discord Mute Test Button
  const muteBtn = document.getElementById("sim-test-mute-btn");
  if (muteBtn) {
    muteBtn.addEventListener("click", () => {
      simState.discordMuted = !simState.discordMuted;
      playSynthesizedChime("mute");
      if (simState.discordMuted) {
        appendSimLog("🔇 [DISCORD IPC] Mute state active -> feeding silence frame-aligned bytes into mic channel.", "warn");
      } else {
        appendSimLog("🔊 [DISCORD IPC] Unmuted -> microphone stream restored with zero timeline drift.", "success");
      }
      updateSimUI();
    });
  }

  // Replay Sync Toggle
  const repBtn = document.getElementById("sim-rep-toggle-btn");
  if (repBtn) {
    repBtn.addEventListener("click", () => {
      playSynthesizedChime("start");
      simState.replaySyncing = !simState.replaySyncing;
      if (simState.replaySyncing) {
        appendSimLog("▶ [UI] Starting Replay Auto-Sync...", "info");
        appendSimLog("🎬 Detected replay file: EUN1_3194821034.rofl in League Client", "info");
        appendSimLog("🎵 Matched audio recording: EUN1_3194821034.mp3", "success");
        appendSimLog("🔊 Loaded in 64-bit VLC engine. Lock established with :2999/replay/playback", "success");
      } else {
        appendSimLog("⏹ Replay closed in League. Stopping synchronized VLC playback...", "info");
      }
      updateSimUI();
    });
  }

  // Replay Volume Slider Control
  const repVolSlider = document.getElementById("sim-volume-slider");
  if (repVolSlider) {
    repVolSlider.addEventListener("input", e => {
      const newVol = parseInt(e.target.value);
      simState.replayVolume = newVol;
      if (simState.replayMuted && newVol > 0) {
        simState.replayMuted = false;
      }
      updateSimUI();
    });
    repVolSlider.addEventListener("change", e => {
      if (simState.replaySyncing) {
        appendSimLog(`🔊 Replay volume set to ${e.target.value}%`, "info");
      }
    });
  }

  // Mute Hotkey Button
  const muteHotkeyBtn = document.getElementById("sim-mute-hotkey-btn");
  if (muteHotkeyBtn) {
    muteHotkeyBtn.addEventListener("click", toggleReplayMute);
  }

  // Global 'M' hotkey listener for desktop simulation
  window.addEventListener("keydown", e => {
    if (e.key === "m" || e.key === "M") {
      // Don't trigger if user is typing in an input
      if (["INPUT", "TEXTAREA"].includes(document.activeElement.tagName)) return;
      toggleReplayMute();
    }
  });

  function toggleReplayMute() {
    simState.replayMuted = !simState.replayMuted;
    playSynthesizedChime("mute");
    if (simState.replayMuted) {
      appendSimLog("🔇 Replay audio muted (press M to unmute).", "warn");
    } else {
      appendSimLog("🔊 Replay audio unmuted.", "success");
    }
    updateSimUI();
  }

  // Tabs Switcher inside simulator
  document.querySelectorAll(".sim-tab-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const tabId = btn.dataset.simTab;
      document.querySelectorAll(".sim-tab-btn").forEach(b => b.classList.remove("active"));
      document.querySelectorAll(".sim-tab-content").forEach(c => c.classList.remove("active"));

      btn.classList.add("active");
      const targetContent = document.getElementById("sim-tab-" + tabId);
      if (targetContent) targetContent.classList.add("active");
    });
  });

  // Microphone Device Selection Simulation
  const micSelect = document.getElementById("sim-mic-select");
  if (micSelect) {
    micSelect.addEventListener("change", () => {
      const selectedName = micSelect.options[micSelect.selectedIndex].text;
      appendSimLog(`🎤 Active microphone changed to: ${selectedName}`, "info");
      const statusLabel = document.getElementById("sim-mic-status-label");
      if (statusLabel) {
        statusLabel.textContent = "Active";
      }
    });
  }

  // Check For Updates simulation
  const checkUpdateBtn = document.getElementById("sim-check-update-btn");
  if (checkUpdateBtn) {
    checkUpdateBtn.addEventListener("click", () => {
      const statusEl = document.getElementById("sim-update-status");
      checkUpdateBtn.disabled = true;
      if (statusEl) statusEl.innerHTML = `<span style="color:#0ac8b9">🔄 Checking GitHub Releases...</span>`;
      appendSimLog("🔍 [UPDATER] Checking GitHub Releases for updates...", "info");

      setTimeout(() => {
        checkUpdateBtn.disabled = false;
        if (statusEl) {
          statusEl.innerHTML = `<span style="color:#4ade80">● You are running the latest version (v0.3.2)</span>`;
        }
        appendSimLog("✅ [UPDATER] Version check complete. Running latest release v0.3.2.", "success");
      }, 900);
    });
  }

  // Render Recordings Tab
  renderRecordingsList();
  startVuMeters();
}

function renderRecordingsList() {
  const container = document.getElementById("sim-recordings-list");
  if (!container) return;
  container.innerHTML = "";

  simState.recordings.forEach(rec => {
    const item = document.createElement("div");
    item.className = "recording-item";
    item.innerHTML = `
      <div class="rec-item-info">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#0ac8b9" stroke-width="2">
          <path d="M9 18V5l12-2v13"></path>
          <circle cx="6" cy="18" r="3"></circle>
          <circle cx="18" cy="16" r="3"></circle>
        </svg>
        <div>
          <div class="rec-item-name">${rec.id}.mp3</div>
          <div class="rec-item-meta">${rec.duration} • ${rec.size} • ${rec.date}</div>
        </div>
      </div>
      <div class="rec-item-actions">
        <button class="btn-rec-play" data-rec-id="${rec.id}">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor">
            <polygon points="5 3 19 12 5 21 5 3"></polygon>
          </svg>
          Play
        </button>
      </div>
    `;
    container.appendChild(item);
  });

  // Bind Play buttons
  container.querySelectorAll(".btn-rec-play").forEach(btn => {
    btn.addEventListener("click", () => {
      const recId = btn.dataset.recId;
      playSynthesizedChime("play");
      appendSimLog(`▶ Playing sample audio for match ${recId} in simulator...`, "success");
    });
  });
}

// ============================================================================
// 4. SYSTEM HEALTH RIBBON SIMULATOR
// ============================================================================
function initHealthRibbon() {
  const refreshBtn = document.getElementById("health-refresh-btn");
  if (!refreshBtn) return;

  refreshBtn.addEventListener("click", () => {
    refreshBtn.classList.add("spinning");
    const badges = document.querySelectorAll(".health-badge-val");
    badges.forEach(b => (b.textContent = "Checking..."));

    setTimeout(() => {
      refreshBtn.classList.remove("spinning");
      document.getElementById("health-discord-val").textContent = i18n[currentLang].health_connected;
      document.getElementById("health-league-val").textContent = i18n[currentLang].health_running;
      document.getElementById("health-vlc-val").textContent = i18n[currentLang].health_ready;
      document.getElementById("health-ffmpeg-val").textContent = i18n[currentLang].health_ready;
    }, 600);
  });
}

// ============================================================================
// 5. SETUP & INSTALLATION TABBED TERMINAL
// ============================================================================
function initSetupTabs() {
  const tabs = document.querySelectorAll(".setup-tab-btn");
  tabs.forEach(tab => {
    tab.addEventListener("click", () => {
      const panelId = tab.dataset.panel;
      tabs.forEach(t => t.classList.remove("active"));
      document.querySelectorAll(".setup-tab-panel").forEach(p => p.classList.remove("active"));

      tab.classList.add("active");
      const targetPanel = document.getElementById("setup-panel-" + panelId);
      if (targetPanel) targetPanel.classList.add("active");
    });
  });

  // 1-Click Code Copy
  document.querySelectorAll(".btn-copy").forEach(btn => {
    btn.addEventListener("click", () => {
      const targetId = btn.dataset.copyTarget;
      const codeEl = document.getElementById(targetId);
      if (!codeEl) return;

      const text = codeEl.innerText.trim();
      navigator.clipboard.writeText(text).then(() => {
        btn.classList.add("copied");
        const originalText = btn.innerHTML;
        btn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"></polyline></svg> Copied!`;
        setTimeout(() => {
          btn.classList.remove("copied");
          btn.innerHTML = originalText;
        }, 2000);
      });
    });
  });
}

// ============================================================================
// 6. FAQ ACCORDION
// ============================================================================
function initFaq() {
  document.querySelectorAll(".faq-item").forEach(item => {
    const btn = item.querySelector(".faq-question-btn");
    if (!btn) return;

    btn.addEventListener("click", () => {
      const isOpen = item.classList.contains("open");
      // Close other open accordions
      document.querySelectorAll(".faq-item").forEach(other => {
        if (other !== item) other.classList.remove("open");
      });
      item.classList.toggle("open", !isOpen);
    });
  });
}

// ============================================================================
// 7. NAVBAR SCROLL EFFECT & MOBILE TOGGLE
// ============================================================================
function initNavbar() {
  const header = document.querySelector(".site-header");
  window.addEventListener("scroll", () => {
    if (window.scrollY > 40) {
      header.classList.add("scrolled");
    } else {
      header.classList.remove("scrolled");
    }
  });

  const mobileToggle = document.querySelector(".mobile-toggle");
  const navLinks = document.querySelector(".nav-links");
  if (mobileToggle && navLinks) {
    mobileToggle.addEventListener("click", () => {
      const isVisible = navLinks.style.display === "flex";
      navLinks.style.display = isVisible ? "none" : "flex";
      if (!isVisible) {
        navLinks.style.position = "absolute";
        navLinks.style.top = "74px";
        navLinks.style.left = "0";
        navLinks.style.right = "0";
        navLinks.style.flexDirection = "column";
        navLinks.style.background = "#030712";
        navLinks.style.padding = "24px";
        navLinks.style.borderBottom = "1px solid var(--border-gold)";
      }
    });
  }

  // Smooth scroll offset for hash links
  document.querySelectorAll('a[href^="#"]').forEach(anchor => {
    anchor.addEventListener("click", function (e) {
      const targetId = this.getAttribute("href");
      if (targetId === "#") return;
      const targetEl = document.querySelector(targetId);
      if (targetEl) {
        e.preventDefault();
        targetEl.scrollIntoView({ behavior: "smooth", block: "start" });
        if (window.innerWidth <= 768 && navLinks) {
          navLinks.style.display = "none";
        }
      }
    });
  });
}

// ============================================================================
// INITIALIZATION
// ============================================================================
document.addEventListener("DOMContentLoaded", () => {
  // Init Hextech Particle Canvas
  initHextechCanvas();

  // Language Switcher
  document.querySelectorAll(".lang-btn").forEach(btn => {
    btn.addEventListener("click", () => setLanguage(btn.dataset.lang));
  });
  setLanguage(currentLang);

  // Components
  initNavbar();
  initHealthRibbon();
  initSimulator();
  initSetupTabs();
  initFaq();
});
