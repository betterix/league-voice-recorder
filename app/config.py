import json
import os
from pathlib import Path
import shutil

from dotenv import load_dotenv, set_key
import sys

_KEY_MAPPINGS = (
    ("record_folder", "RECORD_FOLDER"),
    ("vlc_dir", "VLC_DIR"),
    ("check_time", "CHECK_TIME"),
    ("replay_volume", "REPLAY_VOLUME"),
    ("sync_offset_ms", "SYNC_OFFSET_MS"),
    ("language", "LANGUAGE"),
    ("auto_start_champ_select", "AUTO_START_CHAMP_SELECT"),
    ("auto_start_replay", "AUTO_START_REPLAY"),
)


def _detect_project_root():
    """Detects the real application directory whether running from source or compiled binary."""
    # 1. Nuitka compiled information (item 4 / containing_dir is the real exe folder)
    compiled_info = getattr(sys.modules.get("__main__", None), "__compiled__", None)
    if compiled_info is None:
        import builtins

        compiled_info = getattr(builtins, "__compiled__", None)
    if compiled_info is not None:
        containing_dir = getattr(compiled_info, "containing_dir", None)
        if not containing_dir and len(compiled_info) > 4:
            containing_dir = compiled_info[4]
        if containing_dir and os.path.isdir(containing_dir):
            return os.path.abspath(containing_dir)

    # 2. Nuitka onefile environment variables
    for env_var in ["NUITKA_ONEFILE_BINARY", "NUITKA_ORIGINAL_ARGV0"]:
        val = os.environ.get(env_var)
        if val and os.path.exists(val):
            return os.path.dirname(os.path.abspath(val))

    # 3. sys.argv[0] if invoked as standalone .exe
    if (
        sys.argv
        and sys.argv[0]
        and sys.argv[0].lower().endswith(".exe")
        and os.path.exists(sys.argv[0])
    ):
        return os.path.dirname(os.path.abspath(sys.argv[0]))

    # 4. Standard frozen executable (PyInstaller, etc.)
    if getattr(sys, "frozen", False) or (
        sys.executable
        and sys.executable.lower().endswith(".exe")
        and "python" not in os.path.basename(sys.executable).lower()
    ):
        return os.path.dirname(os.path.abspath(sys.executable))

    # 5. Development mode: parent directory of app package
    app_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(app_dir)


_CACHED_USER_DOCS_APP_DIR = None
_CACHED_USER_DOCS_RECORDINGS_DIR = None


def get_user_documents_app_dir():
    """
    Finds or creates the 'League Voice Recorder' base directory
    inside the CURRENT user's Windows Documents folder.
    """
    global _CACHED_USER_DOCS_APP_DIR
    if _CACHED_USER_DOCS_APP_DIR and os.path.exists(_CACHED_USER_DOCS_APP_DIR):
        return _CACHED_USER_DOCS_APP_DIR

    app_dir = Path.home() / "Documents" / "League Voice Recorder"
    os.makedirs(app_dir, exist_ok=True)
    _CACHED_USER_DOCS_APP_DIR = str(app_dir)
    return _CACHED_USER_DOCS_APP_DIR


def get_current_user_documents_recordings_dir():
    """
    Finds or creates the 'League Voice Recorder/recordings' folder
    inside the CURRENT user's Windows Documents directory.
    """
    global _CACHED_USER_DOCS_RECORDINGS_DIR
    if _CACHED_USER_DOCS_RECORDINGS_DIR and os.path.exists(_CACHED_USER_DOCS_RECORDINGS_DIR):
        return _CACHED_USER_DOCS_RECORDINGS_DIR

    target_dir = os.path.join(get_user_documents_app_dir(), "recordings")
    os.makedirs(target_dir, exist_ok=True)
    _CACHED_USER_DOCS_RECORDINGS_DIR = str(target_dir)
    return _CACHED_USER_DOCS_RECORDINGS_DIR


def get_config_file_path():
    """
    Returns the persistent user-level path to config.json located in
    Documents/League Voice Recorder/config.json (next to recordings/).
    """
    return os.path.join(get_user_documents_app_dir(), "config.json")


PROJECT_ROOT = _detect_project_root()
APP_DIR = os.path.join(PROJECT_ROOT, "app")

DOTENV_PATH = os.path.join(PROJECT_ROOT, ".env")
CONFIG_FILE = get_config_file_path()

# Load .env if present
load_dotenv(DOTENV_PATH)


def resolve_path(path_str):
    """
    Expands environment variables (like %USERPROFILE%) and user home (~)
    into a canonical absolute path.
    """
    if not path_str or not str(path_str).strip():
        return get_current_user_documents_recordings_dir()
    expanded = os.path.expandvars(os.path.expanduser(str(path_str).strip()))
    return os.path.abspath(expanded)


def get_clean_default_config():
    """Builds a clean, safe template config for distribution and new setups."""
    return {
        "target_audio_pid": -1,
        "target_audio_name": "Discord",
        "mic_device_index": -1,
        "mic_device_name": "default",
        "record_folder": get_current_user_documents_recordings_dir(),
        "vlc_dir": r"C:\Program Files\VideoLAN\VLC",
        "check_time": 0.2,
        "replay_volume": 100,
        "sync_offset_ms": 0,
        "language": "en",
        "auto_start_champ_select": True,
        "auto_start_replay": True,
    }


def get_default_config():
    """Builds default config taking environment variables and current user into account."""
    default_rec_dir = get_current_user_documents_recordings_dir()

    env_rec_folder = os.getenv("RECORD_FOLDER")
    if env_rec_folder:
        rec_folder = resolve_path(env_rec_folder)
    else:
        rec_folder = default_rec_dir

    return {
        "target_audio_pid": int(os.getenv("TARGET_AUDIO_PID", "-1")),
        "target_audio_name": os.getenv("TARGET_AUDIO_NAME", "Discord"),
        "mic_device_index": int(os.getenv("MIC_DEVICE_INDEX", "-1")),
        "mic_device_name": os.getenv("MIC_DEVICE_NAME", "default"),
        "record_folder": rec_folder,
        "vlc_dir": resolve_path(os.getenv("VLC_DIR", r"C:\Program Files\VideoLAN\VLC")),
        "check_time": float(os.getenv("CHECK_TIME", "0.2")),
        "replay_volume": int(os.getenv("REPLAY_VOLUME", "100")),
        "sync_offset_ms": int(os.getenv("SYNC_OFFSET_MS", "0")),
        "language": os.getenv("LANGUAGE", "en"),
        "auto_start_champ_select": os.getenv("AUTO_START_CHAMP_SELECT", "true").lower() in ("true", "1", "yes"),
        "auto_start_replay": os.getenv("AUTO_START_REPLAY", "true").lower() in ("true", "1", "yes"),
    }


def load_config():
    """Load configuration from Documents/League Voice Recorder/config.json and ensure target folders exist."""
    config = get_default_config()
    config_path = get_config_file_path()
    legacy_config_path = os.path.join(PROJECT_ROOT, "config.json")

    # Migrate from legacy project root config.json to Documents folder if not yet migrated
    if not os.path.exists(config_path) and os.path.exists(legacy_config_path):
        try:
            with open(legacy_config_path, "r", encoding="utf-8") as f:
                legacy_cfg = json.load(f)
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(legacy_cfg, f, indent=4, ensure_ascii=False)
        except Exception:
            pass

    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                user_config = json.load(f)
                config.update(user_config)
        except Exception as e:
            print(f"⚠️ Failed to load config.json: {e}")
    else:
        # Create initial default config.json in Documents/League Voice Recorder/ if not present
        try:
            clean_cfg = get_clean_default_config()
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(clean_cfg, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"⚠️ Failed to create default config.json: {e}")

    # Always resolve record_folder dynamically and ensure it exists on current user's system
    config["record_folder"] = resolve_path(config.get("record_folder"))
    os.makedirs(config["record_folder"], exist_ok=True)

    return config


def save_config(config_data):
    """Save configuration dictionary to Documents/League Voice Recorder/config.json and synchronize .env."""
    success = True
    config_path = get_config_file_path()
    try:
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(config_data, indent=4, ensure_ascii=False))
    except Exception as e:
        print(f"❌ Error saving config.json: {e}")
        success = False

    # Also keep legacy project root config.json in sync if present
    legacy_config_path = os.path.join(PROJECT_ROOT, "config.json")
    if os.path.exists(legacy_config_path):
        try:
            with open(legacy_config_path, "w", encoding="utf-8") as f:
                f.write(json.dumps(config_data, indent=4, ensure_ascii=False))
        except Exception:
            pass

    # Also update .env file if it exists
    if os.path.exists(DOTENV_PATH):
        try:
            for cfg_key, env_key in _KEY_MAPPINGS:
                if cfg_key in config_data:
                    set_key(DOTENV_PATH, env_key, str(config_data[cfg_key]))
        except Exception as e:
            print(f"⚠️ Warning: Failed to sync .env: {e}")

    return success


def get_ffmpeg_path():
    """Finds the ffmpeg executable (system PATH, local, or winget install)."""
    if shutil.which("ffmpeg"):
        return "ffmpeg"

    for candidate in [
        os.path.join(PROJECT_ROOT, "ffmpeg.exe"),
        os.path.join(os.path.dirname(sys.executable), "ffmpeg.exe"),
    ]:
        if os.path.exists(candidate):
            return candidate

    # Fallback for WinGet if terminal hasn't refreshed PATH yet
    import glob

    winget_matches = glob.glob(
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\*FFmpeg*\**\bin\ffmpeg.exe"),
        recursive=True,
    )
    if winget_matches and os.path.exists(winget_matches[0]):
        return winget_matches[0]

    return "ffmpeg"
