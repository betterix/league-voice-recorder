fn main() {
    tauri_build::build();

    #[cfg(windows)]
    {
        // Ensure WebView2Loader.dll is copied alongside the target binary
        if let Ok(out_dir) = std::env::var("OUT_DIR") {
            let mut target_dir = std::path::PathBuf::from(out_dir);
            while target_dir.file_name().map(|n| n != "build").unwrap_or(false) {
                if !target_dir.pop() {
                    break;
                }
            }
            if target_dir.file_name().map(|n| n == "build").unwrap_or(false) {
                target_dir.pop(); // target/debug or target/release
                let dll_src = std::path::Path::new("assets/WebView2Loader.dll");
                if dll_src.exists() {
                    let _ = std::fs::copy(dll_src, target_dir.join("WebView2Loader.dll"));
                }
            }
        }
    }
}
