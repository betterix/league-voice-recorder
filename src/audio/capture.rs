use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use cpal::{SampleFormat, Stream};
use tracing::{info, warn};

pub struct AudioCaptureManager {
    is_capturing: Arc<AtomicBool>,
    is_paused: Arc<AtomicBool>,
    mic_buffer: Arc<Mutex<Vec<i16>>>,
    loopback_buffer: Arc<Mutex<Vec<i16>>>,
    _mic_stream: Option<Stream>,
    _loopback_stream: Option<Stream>,
    mic_peak: Arc<Mutex<f32>>,
    loopback_peak: Arc<Mutex<f32>>,
}

impl AudioCaptureManager {
    pub fn new() -> Self {
        Self {
            is_capturing: Arc::new(AtomicBool::new(false)),
            is_paused: Arc::new(AtomicBool::new(false)),
            mic_buffer: Arc::new(Mutex::new(Vec::new())),
            loopback_buffer: Arc::new(Mutex::new(Vec::new())),
            _mic_stream: None,
            _loopback_stream: None,
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
                    list.push(desc.name().to_string());
                }
            }
        }
        if list.is_empty() {
            list.push("Default Microphone (WASAPI)".to_string());
        }
        list
    }

    /// Starts audio recording for Microphone and Loopback.
    pub fn start(&mut self, chosen_mic: Option<&str>) -> Result<(), String> {
        self.is_capturing.store(true, Ordering::SeqCst);
        self.is_paused.store(false, Ordering::SeqCst);
        self.mic_buffer.lock().unwrap().clear();
        self.loopback_buffer.lock().unwrap().clear();

        let host = cpal::default_host();

        // 1. Microphone setup
        let mic_device = if let Some(target) = chosen_mic {
            host.input_devices()
                .ok()
                .and_then(|mut devs| {
                    devs.find(|d| {
                        d.description()
                            .map(|desc| desc.name() == target)
                            .unwrap_or(false)
                    })
                })
                .or_else(|| host.default_input_device())
        } else {
            host.default_input_device()
        };

        if let Some(device) = mic_device {
            let buf_clone = self.mic_buffer.clone();
            let pause_clone = self.is_paused.clone();
            let peak_clone = self.mic_peak.clone();

            if let Ok(config) = device.default_input_config() {
                let channels = config.channels() as usize;
                let sample_format = config.sample_format();

                let err_fn = |err| warn!("Microphone stream error: {}", err);

                let stream_res: Option<Stream> = match sample_format {
                    SampleFormat::F32 => {
                        device.build_input_stream(
                            config.into(),
                            move |data: &[f32], _| {
                                if pause_clone.load(Ordering::Relaxed) {
                                    return;
                                }
                                let mut max_abs: f32 = 0.0;
                                let mut local_samples = Vec::with_capacity(data.len() / channels * 2);
                                for frame in data.chunks(channels) {
                                    let sample_f32 = frame[0];
                                    let abs_val = sample_f32.abs();
                                    if abs_val > max_abs {
                                        max_abs = abs_val;
                                    }
                                    let sample_i16 = (sample_f32.clamp(-1.0, 1.0) * 32767.0) as i16;
                                    // Stereo interleaved
                                    local_samples.push(sample_i16);
                                    local_samples.push(sample_i16);
                                }
                                if let Ok(mut pk) = peak_clone.lock() {
                                    *pk = max_abs;
                                }
                                if let Ok(mut buf) = buf_clone.lock() {
                                    buf.extend(local_samples);
                                }
                            },
                            err_fn,
                            None,
                        ).ok()
                    }
                    SampleFormat::I16 => {
                        device.build_input_stream(
                            config.into(),
                            move |data: &[i16], _| {
                                if pause_clone.load(Ordering::Relaxed) {
                                    return;
                                }
                                let mut max_abs: f32 = 0.0;
                                let mut local_samples = Vec::with_capacity(data.len() / channels * 2);
                                for frame in data.chunks(channels) {
                                    let sample_i16 = frame[0];
                                    let abs_val = (sample_i16 as f32).abs() / 32768.0;
                                    if abs_val > max_abs {
                                        max_abs = abs_val;
                                    }
                                    local_samples.push(sample_i16);
                                    local_samples.push(sample_i16);
                                }
                                if let Ok(mut pk) = peak_clone.lock() {
                                    *pk = max_abs;
                                }
                                if let Ok(mut buf) = buf_clone.lock() {
                                    buf.extend(local_samples);
                                }
                            },
                            err_fn,
                            None,
                        ).ok()
                    }
                    other => {
                        warn!("Unsupported microphone sample format: {:?}", other);
                        None
                    }
                };

                if let Some(st) = stream_res {
                    let _ = st.play();
                    self._mic_stream = Some(st);
                    info!("Mic capture stream started successfully.");
                }
            }
        }

        // 2. Loopback setup (Windows default output device as input)
        if let Some(out_dev) = host.default_output_device() {
            let buf_clone = self.loopback_buffer.clone();
            let pause_clone = self.is_paused.clone();
            let peak_clone = self.loopback_peak.clone();

            if let Ok(config) = out_dev.default_input_config() {
                let channels = config.channels() as usize;
                let sample_format = config.sample_format();
                let err_fn = |err| warn!("Loopback stream error: {}", err);

                let stream_res: Option<Stream> = match sample_format {
                    SampleFormat::F32 => {
                        out_dev.build_input_stream(
                            config.into(),
                            move |data: &[f32], _| {
                                if pause_clone.load(Ordering::Relaxed) {
                                    return;
                                }
                                let mut max_abs: f32 = 0.0;
                                let mut local_samples = Vec::with_capacity(data.len() / channels * 2);
                                for frame in data.chunks(channels) {
                                    let left = frame[0];
                                    let right = if channels > 1 { frame[1] } else { frame[0] };
                                    let abs_val = left.abs().max(right.abs());
                                    if abs_val > max_abs {
                                        max_abs = abs_val;
                                    }
                                    local_samples.push((left.clamp(-1.0, 1.0) * 32767.0) as i16);
                                    local_samples.push((right.clamp(-1.0, 1.0) * 32767.0) as i16);
                                }
                                if let Ok(mut pk) = peak_clone.lock() {
                                    *pk = max_abs;
                                }
                                if let Ok(mut buf) = buf_clone.lock() {
                                    buf.extend(local_samples);
                                }
                            },
                            err_fn,
                            None,
                        ).ok()
                    }
                    SampleFormat::I16 => {
                        out_dev.build_input_stream(
                            config.into(),
                            move |data: &[i16], _| {
                                if pause_clone.load(Ordering::Relaxed) {
                                    return;
                                }
                                let mut max_abs: f32 = 0.0;
                                let mut local_samples = Vec::with_capacity(data.len() / channels * 2);
                                for frame in data.chunks(channels) {
                                    let left = frame[0];
                                    let right = if channels > 1 { frame[1] } else { frame[0] };
                                    let abs_val = (left as f32).abs().max((right as f32).abs()) / 32768.0;
                                    if abs_val > max_abs {
                                        max_abs = abs_val;
                                    }
                                    local_samples.push(left);
                                    local_samples.push(right);
                                }
                                if let Ok(mut pk) = peak_clone.lock() {
                                    *pk = max_abs;
                                }
                                if let Ok(mut buf) = buf_clone.lock() {
                                    buf.extend(local_samples);
                                }
                            },
                            err_fn,
                            None,
                        ).ok()
                    }
                    other => {
                        warn!("Unsupported loopback sample format: {:?}", other);
                        None
                    }
                };

                if let Some(st) = stream_res {
                    let _ = st.play();
                    self._loopback_stream = Some(st);
                    info!("Loopback capture stream started successfully.");
                }
            }
        }

        Ok(())
    }

    /// Pauses audio capture and truncates detection lag (default ~450ms).
    pub fn pause_and_truncate_lag(&self, lag_ms: u32, sample_rate: u32) {
        self.is_paused.store(true, Ordering::SeqCst);
        let frames_to_cut = ((lag_ms as f64 / 1000.0) * sample_rate as f64) as usize;
        let samples_to_cut = frames_to_cut * 2; // stereo

        if let Ok(mut mic) = self.mic_buffer.lock() {
            if mic.len() > samples_to_cut {
                let new_len = mic.len() - samples_to_cut;
                mic.truncate(new_len);
            }
        }

        if let Ok(mut loopback) = self.loopback_buffer.lock() {
            if loopback.len() > samples_to_cut {
                let new_len = loopback.len() - samples_to_cut;
                loopback.truncate(new_len);
            }
        }
    }

    /// Resumes audio recording following an in-game unpause.
    pub fn resume(&self) {
        self.is_paused.store(false, Ordering::SeqCst);
    }

    /// Stops audio capture and returns raw PCM byte buffers (Discord/Loopback, Mic).
    pub fn stop_and_take_buffers(&mut self) -> (Vec<u8>, Vec<u8>) {
        self.is_capturing.store(false, Ordering::SeqCst);
        self.is_paused.store(false, Ordering::SeqCst);

        self._mic_stream = None;
        self._loopback_stream = None;

        let to_bytes = |samples: &[i16]| -> Vec<u8> {
            let mut bytes = Vec::with_capacity(samples.len() * 2);
            for s in samples {
                bytes.extend_from_slice(&s.to_le_bytes());
            }
            bytes
        };

        let mic_pcm = {
            let mut lock = self.mic_buffer.lock().unwrap();
            let b = to_bytes(&lock);
            lock.clear();
            b
        };

        let loopback_pcm = {
            let mut lock = self.loopback_buffer.lock().unwrap();
            let b = to_bytes(&lock);
            lock.clear();
            b
        };

        (loopback_pcm, mic_pcm)
    }

    pub fn get_peaks(&self) -> (f32, f32) {
        let m = self.mic_peak.lock().map(|p| *p).unwrap_or(0.0);
        let l = self.loopback_peak.lock().map(|p| *p).unwrap_or(0.0);
        (m, l)
    }
}
