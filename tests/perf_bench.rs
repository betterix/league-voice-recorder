use std::collections::HashMap;
use std::time::{Duration, Instant};

use league_voice_recorder::audio::AudioCaptureManager;
use league_voice_recorder::config::AppConfig;
use league_voice_recorder::lcu::LcuCredentials;
use league_voice_recorder::scrim_meta::ScrimMetadata;

fn bench_iterations<F: FnMut()>(name: &str, iters: usize, mut f: F) -> Duration {
    let start = Instant::now();
    for _ in 0..iters {
        f();
    }
    let elapsed = start.elapsed();
    let per_iter = elapsed / iters as u32;
    println!(
        "{:<42} | {:>5} iters | total: {:>9.3?} | avg: {:>9.3?}",
        name, iters, elapsed, per_iter
    );
    elapsed
}

#[test]
fn run_performance_benchmarks() {
    println!("\n=== League Voice Recorder — Performance & Cache Benchmark ===");
    println!("{:-<88}", "");

    // 1. AppConfig::load()
    bench_iterations("AppConfig::load() (disk JSON)", 50, || {
        let _ = std::hint::black_box(AppConfig::load());
    });

    // 2. ScrimMetadata ID3v2 cold disk read vs cached HashMap lookup
    let mut cache: HashMap<String, (u64, u64, ScrimMetadata)> = HashMap::new();
    let mut sample_mp3s = Vec::new();
    if let Ok(cfg) = AppConfig::load() {
        if let Ok(entries) = std::fs::read_dir(&cfg.record_folder) {
            for entry in entries.flatten().take(20) {
                let path = entry.path();
                if path
                    .extension()
                    .and_then(|s| s.to_str())
                    .map(|e| e.eq_ignore_ascii_case("mp3"))
                    .unwrap_or(false)
                {
                    sample_mp3s.push(path);
                }
            }
        }
    }

    if !sample_mp3s.is_empty() {
        let cold = bench_iterations(
            &format!("ID3v2 Cold Disk Parse ({} files)", sample_mp3s.len()),
            5,
            || {
                for path in &sample_mp3s {
                    let meta = ScrimMetadata::read_from_mp3(path).unwrap_or_else(|_| ScrimMetadata {
                        game_id: String::new(),
                        team_tag: String::new(),
                        notes: String::new(),
                        game_patch: String::new(),
                        recorded_at: String::new(),
                    });
                    let fname = path.file_name().unwrap().to_string_lossy().to_string();
                    cache.insert(fname, (1234, 5678, meta));
                }
            },
        );

        let warm = bench_iterations(
            &format!("ID3v2 Cached Lookup ({} files)", sample_mp3s.len()),
            5,
            || {
                for path in &sample_mp3s {
                    let fname = path.file_name().unwrap().to_string_lossy().to_string();
                    let _ = std::hint::black_box(cache.get(&fname));
                }
            },
        );

        if warm.as_nanos() > 0 {
            let speedup = cold.as_secs_f64() / warm.as_secs_f64();
            println!(
                "  -> ID3v2 Cache Speedup: {:.1}x faster ({:?} -> {:?})",
                speedup, cold, warm
            );
        }
    } else {
        println!("(Skipped ID3v2 benchmark: no .mp3 files found in record_folder)");
    }

    // 3. WASAPI Microphone Enumeration
    bench_iterations("AudioCaptureManager::get_available_microphones()", 5, || {
        let _ = std::hint::black_box(AudioCaptureManager::get_available_microphones());
    });

    // 4. LCU & Game Process Detection
    bench_iterations("LcuCredentials::is_client_running()", 10, || {
        let _ = std::hint::black_box(LcuCredentials::is_client_running());
    });
    bench_iterations("LcuCredentials::is_game_process_running()", 10, || {
        let _ = std::hint::black_box(LcuCredentials::is_game_process_running());
    });

    println!("{:-<88}\n", "");
}
