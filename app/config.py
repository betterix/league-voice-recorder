import functools
import json
import os
from pathlib import Path
import shutil
import sys

from dotenv import load_dotenv, set_key

_KEY_MAPPINGS = (
    ("record_folder", "RECORD_FOLDER"),
    ("vlc_dir", "VLC_DIR"),
    ("replay_volume", "REPLAY_VOLUME"),
    ("language", "LANGUAGE"),
    ("theme", "THEME"),
    ("auto_start_champ_select", "AUTO_START_CHAMP_SELECT"),
    ("auto_start_replay", "AUTO_START_REPLAY"),
    ("hud_overlay_enabled", "HUD_OVERLAY_ENABLED"),
    ("hud_click_through", "HUD_CLICK_THROUGH"),
    ("hud_locked", "HUD_LOCKED"),
    ("hud_opacity", "HUD_OPACITY"),
    ("hud_x", "HUD_X"),
    ("hud_y", "HUD_Y"),
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
    Supports LVR_CONFIG_PATH environment variable override for test isolation.
    """
    override = os.environ.get("LVR_CONFIG_PATH")
    if override:
        return os.path.abspath(override)
    return os.path.join(get_user_documents_app_dir(), "config.json")


PROJECT_ROOT = _detect_project_root()
APP_DIR = os.path.join(PROJECT_ROOT, "app")

DOTENV_PATH = os.path.join(PROJECT_ROOT, ".env")
CONFIG_FILE = get_config_file_path()

# Load .env if present
load_dotenv(DOTENV_PATH)


@functools.lru_cache(maxsize=128)
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
        "replay_volume": 100,
        "language": "en",
        "theme": "dark",
        "auto_start_champ_select": True,
        "auto_start_replay": True,
        "hud_overlay_enabled": True,
        "hud_click_through": False,
        "hud_locked": False,
        "hud_opacity": 0.85,
        "hud_x": -1,
        "hud_y": -1,
    }


def get_default_config():
    """Builds default config taking environment variables and current user into account."""
    default_rec_dir = get_current_user_documents_recordings_dir()

    env_rec_folder = os.getenv("RECORD_FOLDER")
    if env_rec_folder:
        rec_folder = resolve_path(env_rec_folder)
    else:
        rec_folder = default_rec_dir

    def _parse_int(val, default):
        try:
            return int(val)
        except (ValueError, TypeError):
            return default

    def _parse_float(val, default):
        try:
            return float(val)
        except (ValueError, TypeError):
            return default

    return {
        "target_audio_pid": int(os.getenv("TARGET_AUDIO_PID", "-1")),
        "target_audio_name": os.getenv("TARGET_AUDIO_NAME", "Discord"),
        "mic_device_index": int(os.getenv("MIC_DEVICE_INDEX", "-1")),
        "mic_device_name": os.getenv("MIC_DEVICE_NAME", "default"),
        "record_folder": rec_folder,
        "vlc_dir": resolve_path(os.getenv("VLC_DIR", r"C:\Program Files\VideoLAN\VLC")),
        "replay_volume": int(os.getenv("REPLAY_VOLUME", "100")),
        "language": os.getenv("LANGUAGE", "en"),
        "theme": os.getenv("THEME", "dark"),
        "auto_start_champ_select": os.getenv("AUTO_START_CHAMP_SELECT", "true").lower() in ("true", "1", "yes"),
        "auto_start_replay": os.getenv("AUTO_START_REPLAY", "true").lower() in ("true", "1", "yes"),
        "hud_overlay_enabled": os.getenv("HUD_OVERLAY_ENABLED", "true").lower() in ("true", "1", "yes"),
        "hud_click_through": os.getenv("HUD_CLICK_THROUGH", "false").lower() in ("true", "1", "yes"),
        "hud_locked": os.getenv("HUD_LOCKED", "false").lower() in ("true", "1", "yes"),
        "hud_opacity": _parse_float(os.getenv("HUD_OPACITY", "0.85"), 0.85),
        "hud_x": _parse_int(os.getenv("HUD_X", "-1"), -1),
        "hud_y": _parse_int(os.getenv("HUD_Y", "-1"), -1),
    }


_CONFIG_CACHE = None
_CONFIG_MTIME = 0.0


def load_config():
    """Load configuration from Documents/League Voice Recorder/config.json and ensure target folders exist."""
    global _CONFIG_CACHE, _CONFIG_MTIME

    config_path = get_config_file_path()
    try:
        current_mtime = os.path.getmtime(config_path)
        if _CONFIG_CACHE is not None and current_mtime == _CONFIG_MTIME:
            return _CONFIG_CACHE.copy()
    except OSError:
        current_mtime = 0.0

    config = get_default_config()
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

    _CONFIG_CACHE = config.copy()
    _CONFIG_MTIME = current_mtime

    return config


def save_config(config_data):
    """Save configuration dictionary to Documents/League Voice Recorder/config.json and synchronize .env."""
    global _CONFIG_CACHE, _CONFIG_MTIME

    # Always merge into current config so partial updates never erase other settings
    current_cfg = load_config()
    merged_data = {**current_cfg, **config_data}

    # Normalize numeric and boolean types
    if "hud_opacity" in merged_data:
        try:
            merged_data["hud_opacity"] = round(float(merged_data["hud_opacity"]), 2)
        except (ValueError, TypeError):
            merged_data["hud_opacity"] = 0.85
    if "replay_volume" in merged_data:
        try:
            merged_data["replay_volume"] = max(0, min(100, int(merged_data["replay_volume"])))
        except (ValueError, TypeError):
            merged_data["replay_volume"] = 100

    _CONFIG_CACHE = merged_data.copy()
    _CONFIG_MTIME = 0.0

    success = True
    config_path = get_config_file_path()
    try:
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(merged_data, indent=4, ensure_ascii=False))
        try:
            _CONFIG_MTIME = os.path.getmtime(config_path)
        except OSError:
            pass
    except Exception as e:
        print(f"❌ Error saving config.json: {e}")
        success = False

    # Also keep legacy project root config.json in sync if present
    legacy_config_path = os.path.join(PROJECT_ROOT, "config.json")
    if os.path.exists(legacy_config_path):
        try:
            with open(legacy_config_path, "w", encoding="utf-8") as f:
                f.write(json.dumps(merged_data, indent=4, ensure_ascii=False))
        except Exception:
            pass

    # Also update .env file if it exists
    if os.path.exists(DOTENV_PATH):
        try:
            for cfg_key, env_key in _KEY_MAPPINGS:
                if cfg_key in merged_data:
                    set_key(DOTENV_PATH, env_key, str(merged_data[cfg_key]))
        except Exception as e:
            print(f"⚠️ Warning: Failed to sync .env: {e}")

    return success


@functools.lru_cache(maxsize=1)
def get_ffmpeg_path():
    """Finds the ffmpeg executable (system PATH, local, or winget install)."""
    which_path = shutil.which("ffmpeg")
    if which_path:
        return which_path

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
