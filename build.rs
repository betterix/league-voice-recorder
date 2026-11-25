fn main() {
    #[cfg(windows)]
    {
        let mut res = winres::WindowsResource::new();
        if std::path::Path::new("assets/icon.ico").exists() {
            res.set_icon("assets/icon.ico");
        }
        res.set("ProductName", "League Voice Recorder");
        res.set("FileDescription", "League Voice Recorder & Replay Synchronizer");
        let _ = res.compile();
    }
}
