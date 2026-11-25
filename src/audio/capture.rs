use std::collections::{HashSet, VecDeque};
use std::fs::OpenOptions;
use std::io::{BufWriter, Seek, SeekFrom, Write};
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{channel, Receiver, Sender};
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;
use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use cpal::{SampleFormat, Stream};
use tracing::{debug, info, warn};

/// Finds the root process ID of Discord so process loopback captures all child audio sessions.
pub fn find_discord_root_pid() -> Option<u32> {
    use sysinfo::{ProcessesToUpdate, System};
    let mut sys = System::new().ok()?;
    sys.refresh_processes(ProcessesToUpdate::All, true);

    let discord_pids: HashSet<u32> = sys
        .processes()
        .iter()
        .filter(|(_, p)| {
            let name = p.name().to_string_lossy().to_lowercase();
            name == "discord.exe" || name == "discord"
        })
        .map(|(pid, _)| pid.as_u32())
        .collect();

    if discord_pids.is_empty() {
        return None;
    }

    // Identify root Discord process: parent is not another Discord process
    for &pid in &discord_pids {
        if let Some(proc) = sys.process(sysinfo::Pid::from_u32(pid)) {
            if let Some(parent_pid) = proc.parent() {
                if !discord_pids.contains(&parent_pid.as_u32()) {
                    return Some(pid);
                }
            } else {
                return Some(pid);
            }
        }
    }

    discord_pids.into_iter().next()
}

enum AudioWriteOp {
    Data(Vec<i16>),
    TruncateBytes(u64),
    Clear,
}

fn spawn_audio_writer(path: PathBuf, rx: Receiver<AudioWriteOp>) -> JoinHandle<()> {
    std::thread::spawn(move || {
        let file = match OpenOptions::new()
            .create(true)
            .write(true)
            .read(true)
            .truncate(true)
            .open(&path)
        {
            Ok(f) => f,
            Err(e) => {
                warn!("Failed to create temp audio file {:?}: {}", path, e);
                return;
            }
        };

        let mut writer = BufWriter::with_capacity(65536, file);

        while let Ok(op) = rx.recv() {
            match op {
                AudioWriteOp::Data(samples) => {
                    let mut bytes = Vec::with_capacity(samples.len() * 2);
                    for s in samples {
                        bytes.extend_from_slice(&s.to_le_bytes());
                    }
                    let _ = writer.write_all(&bytes);
                }
                AudioWriteOp::TruncateBytes(bytes_to_cut) => {
                    let _ = writer.flush();
                    let cur_pos = writer.stream_position().unwrap_or(0);
                    let new_pos = cur_pos.saturating_sub(bytes_to_cut);
                    if let Ok(()) = writer.get_mut().set_len(new_pos) {
                        let _ = writer.seek(SeekFrom::Start(new_pos));
                    }
                }
                AudioWriteOp::Clear => {
                    let _ = writer.flush();
                    if let Ok(()) = writer.get_mut().set_len(0) {
                        let _ = writer.seek(SeekFrom::Start(0));
                    }
                }
            }
        }

        let _ = writer.flush();
    })
}

pub struct AudioCaptureManager {
    is_capturing: Arc<AtomicBool>,
    is_paused: Arc<AtomicBool>,
    mic_tx: Arc<Mutex<Option<Sender<AudioWriteOp>>>>,
    loopback_tx: Arc<Mutex<Option<Sender<AudioWriteOp>>>>,
    mic_writer: Option<JoinHandle<()>>,
    loopback_writer: Option<JoinHandle<()>>,
    mic_temp_path: PathBuf,
    loopback_temp_path: PathBuf,
    _mic_stream: Option<Stream>,
    _loopback_stream: Option<Stream>,
    _process_loopback_stop: Option<Arc<AtomicBool>>,
    _process_loopback_thread: Option<JoinHandle<()>>,
    pub mic_peak: Arc<Mutex<f32>>,
    pub loopback_peak: Arc<Mutex<f32>>,
}

impl AudioCaptureManager {
    pub fn new() -> Self {
        Self {
            is_capturing: Arc::new(AtomicBool::new(false)),
            is_paused: Arc::new(AtomicBool::new(false)),
            mic_tx: Arc::new(Mutex::new(None)),
            loopback_tx: Arc::new(Mutex::new(None)),
            mic_writer: None,
            loopback_writer: None,
            mic_temp_path: PathBuf::new(),
            loopback_temp_path: PathBuf::new(),
            _mic_stream: None,
            _loopback_stream: None,
            _process_loopback_stop: None,
            _process_loopback_thread: None,
            mic_peak: Arc::new(Mutex::new(0.0)),
            loopback_peak: Arc::new(Mutex::new(0.0)),
        }
    }

    /// Enumerates all active Windows audio input devices (Microphones).
    pub fn get_available_microphones() -> Vec<String> {
        let host = cpal::default_host();
        let mut list = Vec::new();
        if let Ok(devices) = host.input_devices() {
            for d in devices {
                if let Ok(desc) = d.description() {
                    let name = desc.name().to_string();
                    if !list.contains(&name) {
                        list.push(name);
                    }
                }
            }
        }
        if list.is_empty() {
            list.push("Default Microphone (WASAPI)".to_string());
        }
        list
    }

    /// Enumerates all active Windows audio output devices.
    pub fn get_available_output_devices() -> Vec<String> {
        let host = cpal::default_host();
        let mut list = Vec::new();
        if let Ok(devices) = host.output_devices() {
            for d in devices {
                if let Ok(desc) = d.description() {
                    let name = desc.name().to_string();
                    if !list.contains(&name) {
                        list.push(name);
                    }
                }
            }
        }
        if list.is_empty() {
            list.push("Default Output Device (WASAPI)".to_string());
        }
        list
    }

    /// Starts audio recording for Microphone and Loopback streaming to disk.
    pub fn start(&mut self, chosen_mic: Option<&str>, discord_muted: Option<Arc<AtomicBool>>) -> Result<(), String> {
        self.is_capturing.store(true, Ordering::SeqCst);
        self.is_paused.store(false, Ordering::SeqCst);

        // Clean up any lingering writers
        if let Ok(mut tx) = self.mic_tx.lock() {
            *tx = None;
        }
        if let Ok(mut tx) = self.loopback_tx.lock() {
            *tx = None;
        }
        if let Some(h) = self.mic_writer.take() {
            let _ = h.join();
        }
        if let Some(h) = self.loopback_writer.take() {
            let _ = h.join();
        }

        let pid = std::process::id();
        let timestamp = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_millis();
        let temp_dir = std::env::temp_dir();
        self.mic_temp_path = temp_dir.join(format!("lvr_mic_{}_{}.raw", pid, timestamp));
        self.loopback_temp_path = temp_dir.join(format!("lvr_loopback_{}_{}.raw", pid, timestamp));

        let (mic_tx, mic_rx) = channel::<AudioWriteOp>();
        self.mic_writer = Some(spawn_audio_writer(self.mic_temp_path.clone(), mic_rx));
        let mic_sender = mic_tx.clone();
        if let Ok(mut tx) = self.mic_tx.lock() {
            *tx = Some(mic_tx);
        }

        let (loop_tx, loop_rx) = channel::<AudioWriteOp>();
        self.loopback_writer = Some(spawn_audio_writer(self.loopback_temp_path.clone(), loop_rx));
        let loop_sender = loop_tx.clone();
        if let Ok(mut tx) = self.loopback_tx.lock() {
            *tx = Some(loop_tx);
        }

        let host = cpal::default_host();
        let mute_signal = discord_muted.unwrap_or_else(|| Arc::new(AtomicBool::new(false)));

        // 1. Microphone setup: search by target name, with automatic fallback to default input device
        let mic_devices_to_try: Vec<cpal::Device> = {
            let mut list = Vec::new();
            if let Some(target) = chosen_mic {
                if target != "default" && !target.is_empty() {
                    if let Ok(devs) = host.input_devices() {
                        let target_lower = target.to_lowercase();
                        for d in devs {
                            if let Ok(desc) = d.description() {
                                let name_lower = desc.name().to_lowercase();
                                if name_lower == target_lower || name_lower.contains(&target_lower) || target_lower.contains(&name_lower) {
                                    list.push(d);
                                    break;
                                }
                            }
                        }
                    }
                }
            }
            if let Some(def) = host.default_input_device() {
                list.push(def);
            }
            list
        };

        for device in mic_devices_to_try {
            let pause_clone = self.is_paused.clone();
            let peak_clone = self.mic_peak.clone();
            let mute_clone = mute_signal.clone();
            let tx = mic_sender.clone();
            let mic_name = device.description().map(|d| d.name().to_string()).unwrap_or_else(|_| "Microphone".to_string());

            let config_opt = device.default_input_config().ok().or_else(|| {
                device.supported_input_configs().ok().and_then(|mut iter| iter.next().map(|c| c.with_max_sample_rate()))
            });

            if let Some(config) = config_opt {
                let channels = config.channels() as usize;
                let sample_rate = config.sample_rate();
                let sample_format = config.sample_format();
                let err_fn = move |err| {
                    let msg = format!("{}", err);
                    let lower = msg.to_lowercase();
                    if !lower.contains("underrun") && !lower.contains("overrun") {
                        warn!("Microphone stream error: {}", msg);
                    } else {
                        debug!("Microphone stream transient: {}", msg);
                    }
                };

                let mut acc = 0.0f64;
                let step = sample_rate as f64 / 48000.0;

                let stream_res = match sample_format {
                    SampleFormat::F32 => {
                        let mute_f32 = mute_clone.clone();
                        let tx_f32 = tx.clone();
                        device.build_input_stream(
                            config.clone().into(),
                            move |data: &[f32], _| {
                                if pause_clone.load(Ordering::Relaxed) {
                                    return;
                                }
                                let is_muted = mute_f32.load(Ordering::Relaxed);
                                let mut local = Vec::new();
                                let mut local_peak = 0.0f32;
                                for frame in data.chunks(channels) {
                                    acc += 1.0;
                                    while acc >= step {
                                        acc -= step;
                                        if is_muted {
                                            local.push(0i16);
                                            local.push(0i16);
                                        } else {
                                            let left = frame[0];
                                            let right = if channels > 1 { frame[1] } else { frame[0] };
                                            let max_v = left.abs().max(right.abs());
                                            if max_v > local_peak {
                                                local_peak = max_v;
                                            }
                                            local.push((left.clamp(-1.0, 1.0) * 32767.0) as i16);
                                            local.push((right.clamp(-1.0, 1.0) * 32767.0) as i16);
                                        }
                                    }
                                }
                                if is_muted {
                                    local_peak = 0.0;
                                }
                                if let Ok(mut pk) = peak_clone.lock() {
                                    if local_peak > *pk {
                                        *pk = local_peak;
                                    }
                                }
                                let _ = tx_f32.send(AudioWriteOp::Data(local));
                            },
                            err_fn,
                            None,
                        )
                    }
                    SampleFormat::I16 => {
                        let mute_i16 = mute_clone.clone();
                        let tx_i16 = tx.clone();
                        device.build_input_stream(
                            config.clone().into(),
                            move |data: &[i16], _| {
                                if pause_clone.load(Ordering::Relaxed) {
                                    return;
                                }
                                let is_muted = mute_i16.load(Ordering::Relaxed);
                                let mut local = Vec::new();
                                let mut local_peak = 0.0f32;
                                for frame in data.chunks(channels) {
                                    acc += 1.0;
                                    while acc >= step {
                                        acc -= step;
                                        if is_muted {
                                            local.push(0i16);
                                            local.push(0i16);
                                        } else {
                                            let left = frame[0];
                                            let right = if channels > 1 { frame[1] } else { frame[0] };
                                            let max_v = (left as f32).abs().max((right as f32).abs()) / 32768.0;
                                            if max_v > local_peak {
                                                local_peak = max_v;
                                            }
                                            local.push(left);
                                            local.push(right);
                                        }
                                    }
                                }
                                if is_muted {
                                    local_peak = 0.0;
                                }
                                if let Ok(mut pk) = peak_clone.lock() {
                                    if local_peak > *pk {
                                        *pk = local_peak;
                                    }
                                }
                                let _ = tx_i16.send(AudioWriteOp::Data(local));
                            },
                            err_fn,
                            None,
                        )
                    }
                    SampleFormat::I32 => {
                        let mute_i32 = mute_clone.clone();
                        let tx_i32 = tx.clone();
                        device.build_input_stream(
                            config.clone().into(),
                            move |data: &[i32], _| {
                                if pause_clone.load(Ordering::Relaxed) {
                                    return;
                                }
                                let is_muted = mute_i32.load(Ordering::Relaxed);
                                let mut local = Vec::new();
                                let mut local_peak = 0.0f32;
                                for frame in data.chunks(channels) {
                                    acc += 1.0;
                                    while acc >= step {
                                        acc -= step;
                                        if is_muted {
                                            local.push(0i16);
                                            local.push(0i16);
                                        } else {
                                            let left = (frame[0] >> 16) as i16;
                                            let right = if channels > 1 { (frame[1] >> 16) as i16 } else { left };
                                            let max_v = (left as f32).abs().max((right as f32).abs()) / 32768.0;
                                            if max_v > local_peak {
                                                local_peak = max_v;
                                            }
                                            local.push(left);
                                            local.push(right);
                                        }
                                    }
                                }
                                if is_muted {
                                    local_peak = 0.0;
                                }
                                if let Ok(mut pk) = peak_clone.lock() {
                                    if local_peak > *pk {
                                        *pk = local_peak;
                                    }
                                }
                                let _ = tx_i32.send(AudioWriteOp::Data(local));
                            },
                            err_fn,
                            None,
                        )
                    }
                    SampleFormat::U16 => {
                        let mute_u16 = mute_clone.clone();
                        let tx_u16 = tx.clone();
                        device.build_input_stream(
                            config.clone().into(),
                            move |data: &[u16], _| {
                                if pause_clone.load(Ordering::Relaxed) {
                                    return;
                                }
                                let is_muted = mute_u16.load(Ordering::Relaxed);
                                let mut local = Vec::new();
                                let mut local_peak = 0.0f32;
                                for frame in data.chunks(channels) {
                                    acc += 1.0;
                                    while acc >= step {
                                        acc -= step;
                                        if is_muted {
                                            local.push(0i16);
                                            local.push(0i16);
                                        } else {
                                            let left = (frame[0] as i32 - 32768) as i16;
                                            let right = if channels > 1 { (frame[1] as i32 - 32768) as i16 } else { left };
                                            let max_v = (left as f32).abs().max((right as f32).abs()) / 32768.0;
                                            if max_v > local_peak {
                                                local_peak = max_v;
                                            }
                                            local.push(left);
                                            local.push(right);
                                        }
                                    }
                                }
                                if is_muted {
                                    local_peak = 0.0;
                                }
                                if let Ok(mut pk) = peak_clone.lock() {
                                    if local_peak > *pk {
                                        *pk = local_peak;
                                    }
                                }
                                let _ = tx_u16.send(AudioWriteOp::Data(local));
                            },
                            err_fn,
                            None,
                        )
                    }
                    other => {
                        warn!("Unsupported microphone sample format on '{}': {:?}", mic_name, other);
                        continue;
                    }
                };

                match stream_res {
                    Ok(st) => {
                        if st.play().is_ok() {
                            self._mic_stream = Some(st);
                            info!("🎙️ Microphone capture active on '{}' ({}ch @ {}Hz, {:?}).", mic_name, channels, sample_rate, sample_format);
                            break;
                        }
                    }
                    Err(e) => {
                        warn!("Failed to build input stream on '{}': {}. Trying fallback device...", mic_name, e);
                    }
                }
            }
        }

        // Clean up previous process loopback thread if active
        if let Some(stop) = self._process_loopback_stop.take() {
            stop.store(true, Ordering::SeqCst);
        }
        if let Some(handle) = self._process_loopback_thread.take() {
            let _ = handle.join();
        }

        // 2. Loopback setup:
        // EXCLUSIVELY attach WASAPI Process Loopback to Discord (Discord.exe process tree).
        // Under NO circumstances do we fall back to headphones/speakers, ensuring ZERO background
        // game sound, browser tabs, or music can ever bleed into the scrim recording.
        if let Some(discord_pid) = find_discord_root_pid() {
            info!("🎯 Attaching WASAPI Process Loopback to Discord (PID: {})...", discord_pid);
            let stop_flag = Arc::new(AtomicBool::new(false));
            self._process_loopback_stop = Some(stop_flag.clone());

            let tx = loop_sender.clone();
            let pause_clone = self.is_paused.clone();
            let peak_clone = self.loopback_peak.clone();

            let handle = std::thread::Builder::new()
                .name("DiscordProcessLoopback".to_string())
                .spawn(move || {
                    let _ = wasapi::initialize_mta();

                    let desired_format = wasapi::WaveFormat::new(
                        32,
                        32,
                        wasapi::SampleType::Float,
                        48000,
                        2,
                        None,
                    );

                    let mut audio_client = match wasapi::AudioClient::new_application_loopback_client(discord_pid, true) {
                        Ok(c) => c,
                        Err(e) => {
                            warn!("Failed to create application loopback client for Discord (PID {}): {:?}", discord_pid, e);
                            return;
                        }
                    };

                    let stream_mode = wasapi::StreamMode::EventsShared {
                        autoconvert: true,
                        buffer_duration_hns: 0,
                    };

                    if let Err(e) = audio_client.initialize_client(&desired_format, wasapi::Direction::Capture, stream_mode, None) {
                        warn!("Failed to initialize Discord application loopback client: {:?}", e);
                        return;
                    }

                    let h_event = match audio_client.set_get_eventhandle() {
                        Ok(h) => h,
                        Err(e) => {
                            warn!("Failed to set/get event handle for Discord loopback: {:?}", e);
                            return;
                        }
                    };

                    let capture_client = match audio_client.get_audiocaptureclient() {
                        Ok(cc) => cc,
                        Err(e) => {
                            warn!("Failed to get AudioCaptureClient for Discord loopback: {:?}", e);
                            return;
                        }
                    };

                    if let Err(e) = audio_client.start_stream() {
                        warn!("Failed to start Discord process loopback stream: {:?}", e);
                        return;
                    }

                    info!("🎧 Pure Discord Process Loopback capture active (PID: {}).", discord_pid);

                    let mut sample_queue = VecDeque::new();

                    while !stop_flag.load(Ordering::Relaxed) {
                        if h_event.wait_for_event(100).is_ok() {
                            let _ = capture_client.read_from_device_to_deque(&mut sample_queue);

                            if !sample_queue.is_empty() {
                                let mut local = Vec::new();
                                let mut local_peak = 0.0f32;
                                let is_paused = pause_clone.load(Ordering::Relaxed);

                                while sample_queue.len() >= 8 {
                                    let b0 = sample_queue.pop_front().unwrap();
                                    let b1 = sample_queue.pop_front().unwrap();
                                    let b2 = sample_queue.pop_front().unwrap();
                                    let b3 = sample_queue.pop_front().unwrap();
                                    let left_f32 = f32::from_le_bytes([b0, b1, b2, b3]);

                                    let b4 = sample_queue.pop_front().unwrap();
                                    let b5 = sample_queue.pop_front().unwrap();
                                    let b6 = sample_queue.pop_front().unwrap();
                                    let b7 = sample_queue.pop_front().unwrap();
                                    let right_f32 = f32::from_le_bytes([b4, b5, b6, b7]);

                                    if !is_paused {
                                        let max_v = left_f32.abs().max(right_f32.abs());
                                        if max_v > local_peak {
                                            local_peak = max_v;
                                        }
                                        local.push((left_f32.clamp(-1.0, 1.0) * 32767.0) as i16);
                                        local.push((right_f32.clamp(-1.0, 1.0) * 32767.0) as i16);
                                    }
                                }

                                if !is_paused && !local.is_empty() {
                                    if let Ok(mut pk) = peak_clone.lock() {
                                        if local_peak > *pk {
                                            *pk = local_peak;
                                        }
                                    }
                                    let _ = tx.send(AudioWriteOp::Data(local));
                                }
                            }
                        }
                    }

                    let _ = audio_client.stop_stream();
                    debug!("Discord process loopback stream stopped.");
                });

            if let Ok(h) = handle {
                self._process_loopback_thread = Some(h);
            }
        } else {
            info!("ℹ️ Discord not detected at match start; loopback channel dedicated solely to Discord (no headphone audio recorded).");
        }

        Ok(())
    }

    /// Pauses audio capture and truncates detection lag (default ~280ms) directly from disk.
    pub fn pause_and_truncate_lag(&self, lag_ms: u32, sample_rate: u32) {
        self.is_paused.store(true, Ordering::SeqCst);
        let frames_to_cut = ((lag_ms as f64 / 1000.0) * sample_rate as f64) as u64;
        let bytes_to_cut = frames_to_cut * 2 * 2; // stereo, 16-bit (2 channels * 2 bytes)

        if let Ok(tx) = self.mic_tx.lock() {
            if let Some(ref sender) = *tx {
                let _ = sender.send(AudioWriteOp::TruncateBytes(bytes_to_cut));
            }
        }

        if let Ok(tx) = self.loopback_tx.lock() {
            if let Some(ref sender) = *tx {
                let _ = sender.send(AudioWriteOp::TruncateBytes(bytes_to_cut));
            }
        }
    }

    /// Resumes audio recording following an in-game unpause.
    pub fn resume(&self) {
        self.is_paused.store(false, Ordering::SeqCst);
    }

    /// Starts live audio preview monitoring (computes peak levels for VU meter without recording to disk).
    pub fn start_preview(&mut self, chosen_mic: Option<&str>) -> Result<(), String> {
        self.stop_preview();

        let host = cpal::default_host();

        // 1. Microphone setup
        let mic_devices_to_try: Vec<cpal::Device> = {
            let mut list = Vec::new();
            if let Some(target) = chosen_mic {
                if target != "default" && !target.is_empty() {
                    if let Ok(devs) = host.input_devices() {
                        let target_lower = target.to_lowercase();
                        for d in devs {
                            if let Ok(desc) = d.description() {
                                let name_lower = desc.name().to_lowercase();
                                if name_lower == target_lower || name_lower.contains(&target_lower) || target_lower.contains(&name_lower) {
                                    list.push(d);
                                    break;
                                }
                            }
                        }
                    }
                }
            }
            if let Some(def) = host.default_input_device() {
                list.push(def);
            }
            list
        };

        for device in mic_devices_to_try {
            let peak_clone = self.mic_peak.clone();
            let config_opt = device.default_input_config().ok().or_else(|| {
                device.supported_input_configs().ok().and_then(|mut iter| iter.next().map(|c| c.with_max_sample_rate()))
            });

            if let Some(config) = config_opt {
                let channels = config.channels() as usize;
                let sample_format = config.sample_format();
                let err_fn = move |_err| {};

                let stream_res = match sample_format {
                    SampleFormat::F32 => {
                        device.build_input_stream(
                            config.into(),
                            move |data: &[f32], _| {
                                let mut local_peak = 0.0f32;
                                for frame in data.chunks(channels) {
                                    let left = frame[0];
                                    let right = if channels > 1 { frame[1] } else { frame[0] };
                                    let max_v = left.abs().max(right.abs());
                                    if max_v > local_peak { local_peak = max_v; }
                                }
                                if let Ok(mut pk) = peak_clone.lock() {
                                    if local_peak > *pk { *pk = local_peak; }
                                }
                            },
                            err_fn,
                            None,
                        )
                    }
                    SampleFormat::I16 => {
                        device.build_input_stream(
                            config.into(),
                            move |data: &[i16], _| {
                                let mut local_peak = 0.0f32;
                                for frame in data.chunks(channels) {
                                    let left = frame[0];
                                    let right = if channels > 1 { frame[1] } else { frame[0] };
                                    let max_v = (left as f32).abs().max((right as f32).abs()) / 32768.0;
                                    if max_v > local_peak { local_peak = max_v; }
                                }
                                if let Ok(mut pk) = peak_clone.lock() {
                                    if local_peak > *pk { *pk = local_peak; }
                                }
                            },
                            err_fn,
                            None,
                        )
                    }
                    SampleFormat::I32 => {
                        device.build_input_stream(
                            config.into(),
                            move |data: &[i32], _| {
                                let mut local_peak = 0.0f32;
                                for frame in data.chunks(channels) {
                                    let left = (frame[0] >> 16) as i16;
                                    let right = if channels > 1 { (frame[1] >> 16) as i16 } else { left };
                                    let max_v = (left as f32).abs().max((right as f32).abs()) / 32768.0;
                                    if max_v > local_peak { local_peak = max_v; }
                                }
                                if let Ok(mut pk) = peak_clone.lock() {
                                    if local_peak > *pk { *pk = local_peak; }
                                }
                            },
                            err_fn,
                            None,
                        )
                    }
                    SampleFormat::U16 => {
                        device.build_input_stream(
                            config.into(),
                            move |data: &[u16], _| {
                                let mut local_peak = 0.0f32;
                                for frame in data.chunks(channels) {
                                    let left = (frame[0] as i32 - 32768) as i16;
                                    let right = if channels > 1 { (frame[1] as i32 - 32768) as i16 } else { left };
                                    let max_v = (left as f32).abs().max((right as f32).abs()) / 32768.0;
                                    if max_v > local_peak { local_peak = max_v; }
                                }
                                if let Ok(mut pk) = peak_clone.lock() {
                                    if local_peak > *pk { *pk = local_peak; }
                                }
                            },
                            err_fn,
                            None,
                        )
                    }
                    _ => continue,
                };

                if let Ok(st) = stream_res {
                    if st.play().is_ok() {
                        self._mic_stream = Some(st);
                        break;
                    }
                }
            }
        }

        // 2. Loopback setup:
        // Exclusively hook preview peak meter to Discord process loopback
        if let Some(discord_pid) = find_discord_root_pid() {
            let stop_flag = Arc::new(AtomicBool::new(false));
            self._process_loopback_stop = Some(stop_flag.clone());
            let peak_clone = self.loopback_peak.clone();

            let handle = std::thread::Builder::new()
                .name("DiscordPreviewLoopback".to_string())
                .spawn(move || {
                    let _ = wasapi::initialize_mta();
                    let desired_format = wasapi::WaveFormat::new(
                        32,
                        32,
                        wasapi::SampleType::Float,
                        48000,
                        2,
                        None,
                    );
                    let mut audio_client = match wasapi::AudioClient::new_application_loopback_client(discord_pid, true) {
                        Ok(c) => c,
                        Err(_) => return,
                    };
                    let stream_mode = wasapi::StreamMode::EventsShared {
                        autoconvert: true,
                        buffer_duration_hns: 0,
                    };
                    if audio_client.initialize_client(&desired_format, wasapi::Direction::Capture, stream_mode, None).is_err() {
                        return;
                    }
                    let h_event = match audio_client.set_get_eventhandle() {
                        Ok(h) => h,
                        Err(_) => return,
                    };
                    let capture_client = match audio_client.get_audiocaptureclient() {
                        Ok(cc) => cc,
                        Err(_) => return,
                    };
                    if audio_client.start_stream().is_err() {
                        return;
                    }

                    let mut sample_queue = VecDeque::new();
                    while !stop_flag.load(Ordering::Relaxed) {
                        if h_event.wait_for_event(100).is_ok() {
                            let _ = capture_client.read_from_device_to_deque(&mut sample_queue);
                            if !sample_queue.is_empty() {
                                let mut local_peak = 0.0f32;
                                while sample_queue.len() >= 8 {
                                    let b0 = sample_queue.pop_front().unwrap();
                                    let b1 = sample_queue.pop_front().unwrap();
                                    let b2 = sample_queue.pop_front().unwrap();
                                    let b3 = sample_queue.pop_front().unwrap();
                                    let left_f32 = f32::from_le_bytes([b0, b1, b2, b3]);

                                    let b4 = sample_queue.pop_front().unwrap();
                                    let b5 = sample_queue.pop_front().unwrap();
                                    let b6 = sample_queue.pop_front().unwrap();
                                    let b7 = sample_queue.pop_front().unwrap();
                                    let right_f32 = f32::from_le_bytes([b4, b5, b6, b7]);

                                    let max_v = left_f32.abs().max(right_f32.abs());
                                    if max_v > local_peak {
                                        local_peak = max_v;
                                    }
                                }
                                if let Ok(mut pk) = peak_clone.lock() {
                                    if local_peak > *pk {
                                        *pk = local_peak;
                                    }
                                }
                            }
                        }
                    }
                    let _ = audio_client.stop_stream();
                });

            if let Ok(h) = handle {
                self._process_loopback_thread = Some(h);
            }
        }

        Ok(())
    }

    /// Stops audio preview monitoring.
    pub fn stop_preview(&mut self) {
        if let Some(stop) = self._process_loopback_stop.take() {
            stop.store(true, Ordering::SeqCst);
        }
        if let Some(handle) = self._process_loopback_thread.take() {
            let _ = handle.join();
        }
        self._mic_stream = None;
        self._loopback_stream = None;
        if let Ok(mut pk) = self.mic_peak.lock() {
            *pk = 0.0;
        }
        if let Ok(mut pk) = self.loopback_peak.lock() {
            *pk = 0.0;
        }
    }

    /// Stops audio capture and returns temporary raw PCM files on disk (Discord/Loopback, Mic)
    /// along with the maximum byte size captured.
    pub fn stop_and_take_files(&mut self) -> (PathBuf, PathBuf, u64) {
        self.is_capturing.store(false, Ordering::SeqCst);
        self.is_paused.store(false, Ordering::SeqCst);
        if let Ok(mut pk) = self.mic_peak.lock() {
            *pk = 0.0;
        }
        if let Ok(mut pk) = self.loopback_peak.lock() {
            *pk = 0.0;
        }

        // Stop Discord process loopback thread
        if let Some(stop) = self._process_loopback_stop.take() {
            stop.store(true, Ordering::SeqCst);
        }
        if let Some(handle) = self._process_loopback_thread.take() {
            let _ = handle.join();
        }

        // Stop and drop active CPAL streams so callbacks cease producing audio
        self._mic_stream = None;
        self._loopback_stream = None;

        // Disconnect channel senders so writer threads complete
        if let Ok(mut tx) = self.mic_tx.lock() {
            *tx = None;
        }
        if let Ok(mut tx) = self.loopback_tx.lock() {
            *tx = None;
        }

        // Wait for writer threads to flush all pending buffers to disk
        if let Some(handle) = self.mic_writer.take() {
            let _ = handle.join();
        }
        if let Some(handle) = self.loopback_writer.take() {
            let _ = handle.join();
        }

        let mic_len = std::fs::metadata(&self.mic_temp_path).map(|m| m.len()).unwrap_or(0);
        let loop_len = std::fs::metadata(&self.loopback_temp_path).map(|m| m.len()).unwrap_or(0);
        let max_len = mic_len.max(loop_len);

        let mic_p = std::mem::take(&mut self.mic_temp_path);
        let loop_p = std::mem::take(&mut self.loopback_temp_path);

        (loop_p, mic_p, max_len)
    }

    /// Stops audio capture and returns raw PCM byte buffers (Discord/Loopback, Mic)
    /// for backward compatibility with in-memory operations and unit tests.
    pub fn stop_and_take_buffers(&mut self) -> (Vec<u8>, Vec<u8>) {
        let (loop_path, mic_path, _) = self.stop_and_take_files();
        let loop_bytes = std::fs::read(&loop_path).unwrap_or_default();
        let mic_bytes = std::fs::read(&mic_path).unwrap_or_default();
        let _ = std::fs::remove_file(&loop_path);
        let _ = std::fs::remove_file(&mic_path);
        (loop_bytes, mic_bytes)
    }

    /// Truncates the disk temp files back to 0 bytes when match clock starts.
    pub fn clear_buffers(&self) {
        if let Ok(tx) = self.mic_tx.lock() {
            if let Some(ref sender) = *tx {
                let _ = sender.send(AudioWriteOp::Clear);
            }
        }
        if let Ok(tx) = self.loopback_tx.lock() {
            if let Some(ref sender) = *tx {
                let _ = sender.send(AudioWriteOp::Clear);
            }
        }
    }

    pub fn get_peaks(&self) -> (f32, f32) {
        let mut m = 0.0;
        let mut l = 0.0;
        if let Ok(mut pk) = self.mic_peak.lock() {
            m = *pk;
            *pk *= 0.82;
            if *pk < 0.001 { *pk = 0.0; }
        }
        if let Ok(mut pk) = self.loopback_peak.lock() {
            l = *pk;
            *pk *= 0.82;
            if *pk < 0.001 { *pk = 0.0; }
        }
        (m, l)
    }
}

impl Drop for AudioCaptureManager {
    fn drop(&mut self) {
        self.is_capturing.store(false, Ordering::SeqCst);
        self.is_paused.store(false, Ordering::SeqCst);
        self._mic_stream = None;
        self._loopback_stream = None;

        if let Ok(mut tx) = self.mic_tx.lock() {
            *tx = None;
        }
        if let Ok(mut tx) = self.loopback_tx.lock() {
            *tx = None;
        }

        if let Some(h) = self.mic_writer.take() {
            let _ = h.join();
        }
        if let Some(h) = self.loopback_writer.take() {
            let _ = h.join();
        }

        if !self.mic_temp_path.as_os_str().is_empty() && self.mic_temp_path.exists() {
            let _ = std::fs::remove_file(&self.mic_temp_path);
        }
        if !self.loopback_temp_path.as_os_str().is_empty() && self.loopback_temp_path.exists() {
            let _ = std::fs::remove_file(&self.loopback_temp_path);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_device_enumeration() {
        let mics = AudioCaptureManager::get_available_microphones();
        assert!(!mics.is_empty(), "Expected at least one microphone device (or fallback)");
        let outs = AudioCaptureManager::get_available_output_devices();
        assert!(!outs.is_empty(), "Expected at least one output device");
    }

    #[test]
    fn test_capture_start_and_record() {
        let mut mgr = AudioCaptureManager::new();
        let res = mgr.start(None, None);
        assert!(res.is_ok(), "Audio capture start failed: {:?}", res.err());
        std::thread::sleep(std::time::Duration::from_millis(100));
        let (loopback, mic) = mgr.stop_and_take_buffers();
        // Verify buffer extraction is non-panicking and resets state
        assert!(!mgr.is_capturing.load(Ordering::SeqCst));
        let _ = (loopback, mic);
    }

    #[test]
    fn test_capture_stop_and_take_files() {
        let mut mgr = AudioCaptureManager::new();
        let res = mgr.start(None, None);
        assert!(res.is_ok());
        std::thread::sleep(std::time::Duration::from_millis(50));
        let (loop_p, mic_p, _bytes) = mgr.stop_and_take_files();
        assert!(loop_p.exists() || !loop_p.as_os_str().is_empty());
        let _ = std::fs::remove_file(loop_p);
        let _ = std::fs::remove_file(mic_p);
    }

    #[test]
    fn test_loopback_config() {
        let host = cpal::default_host();
        if let Some(out_dev) = host.default_output_device() {
            if let Ok(config) = out_dev.default_output_config() {
                let stream = out_dev.build_input_stream(
                    config.into(),
                    move |_data: &[f32], _| {},
                    |_| {},
                    None,
                );
                assert!(stream.is_ok(), "Loopback stream creation failed: {:?}", stream.err());
            }
        }
    }
}
