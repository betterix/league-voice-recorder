import json
import os
from pathlib import Path
import shutil

from dotenv import load_dotenv, set_key
import sys

if getattr(sys, "frozen", False) or "__compiled__" in globals() or hasattr(sys.modules.get("__main__", None), "__compiled__") or (sys.executable and sys.executable.lower().endswith(".exe") and "python" not in os.path.basename(sys.executable).lower()):
    PROJECT_ROOT = os.path.dirname(sys.executable)
    APP_DIR = os.path.join(PROJECT_ROOT, "app")
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
    PROJECT_ROOT = os.path.dirname(APP_DIR)

DOTENV_PATH = os.path.join(PROJECT_ROOT, ".env")
CONFIG_FILE = os.path.join(PROJECT_ROOT, "config.json")

# Load .env if present
load_dotenv(DOTENV_PATH)


def get_current_user_documents_recordings_dir():
    """
    Finds or creates the 'League Voice Recorder/recordings' folder
    inside the CURRENT user's Documents directory.
    """
    user_home = Path.home()
    user_docs = user_home / "Documents"

    # Fallback for OneDrive Documents redirection if standard doesn't exist
    if not user_docs.exists() and (user_home / "OneDrive" / "Documents").exists():
        user_docs = user_home / "OneDrive" / "Documents"

    target_dir = user_docs / "League Voice Recorder" / "recordings"
    os.makedirs(target_dir, exist_ok=True)
    return str(target_dir)


def resolve_path(path_str):
    """
    Expands environment variables (like %USERPROFILE%) and user home (~)
    into a canonical absolute path.
    """
    if not path_str or not str(path_str).strip():
        return get_current_user_documents_recordings_dir()
    expanded = os.path.expandvars(os.path.expanduser(str(path_str).strip()))
    return os.path.abspath(expanded)


def get_default_config():
    """Builds default config taking environment variables and current user into account."""
    default_rec_dir = get_current_user_documents_recordings_dir()

    env_rec_folder = os.getenv("RECORD_FOLDER")
    if env_rec_folder:
        rec_folder = resolve_path(env_rec_folder)
    else:
        rec_folder = default_rec_dir

    return {
        "obs_host": os.getenv("OBS_HOST", "localhost"),
        "obs_port": int(os.getenv("OBS_PORT", "4455")),
        "obs_password": os.getenv("OBS_PASSWORD", ""),
        "scene_name": os.getenv("OBS_SCENE_NAME", "Discord Only"),
        "record_folder": rec_folder,
        "vlc_dir": resolve_path(os.getenv("VLC_DIR", r"C:\Program Files\VideoLAN\VLC")),
        "check_time": float(os.getenv("CHECK_TIME", "0.2")),
        "replay_volume": int(os.getenv("REPLAY_VOLUME", "80")),
        "language": os.getenv("LANGUAGE", "en"),
    }


def load_config():
    """Load configuration from .env / config.json and ensure target folders exist."""
    config = get_default_config()

    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                user_config = json.load(f)
                config.update(user_config)
        except Exception as e:
            print(f"⚠️ Failed to load config.json: {e}")

    # Always resolve record_folder dynamically and ensure it exists on current user's system
    config["record_folder"] = resolve_path(config.get("record_folder"))
    os.makedirs(config["record_folder"], exist_ok=True)

    return config


def save_config(config_data):
    """Save configuration dictionary to config.json and synchronize .env."""
    success = True
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"❌ Error saving config.json: {e}")
        success = False

    # Also update .env file if it exists
    if os.path.exists(DOTENV_PATH):
        try:
            key_mappings = {
                "obs_host": "OBS_HOST",
                "obs_port": "OBS_PORT",
                "obs_password": "OBS_PASSWORD",
                "scene_name": "OBS_SCENE_NAME",
                "record_folder": "RECORD_FOLDER",
                "vlc_dir": "VLC_DIR",
                "check_time": "CHECK_TIME",
                "replay_volume": "REPLAY_VOLUME",
                "language": "LANGUAGE",
            }
            for cfg_key, env_key in key_mappings.items():
                if cfg_key in config_data:
                    set_key(DOTENV_PATH, env_key, str(config_data[cfg_key]))
        except Exception as e:
            print(f"⚠️ Warning: Failed to sync .env: {e}")

    return success


def get_ffmpeg_path():
    """Finds the ffmpeg executable (system PATH or local executable)."""
    if shutil.which("ffmpeg"):
        return "ffmpeg"

    for candidate in [
        os.path.join(PROJECT_ROOT, "ffmpeg.exe"),
        os.path.join(os.path.dirname(sys.executable), "ffmpeg.exe"),
    ]:
        if os.path.exists(candidate):
            return candidate

    return "ffmpeg"
