from collections.abc import Callable
import json
import os
import sys

_MODULE_FILE_DIR = os.path.dirname(os.path.abspath(__file__))

_PARENT_DIR = os.path.dirname(_MODULE_FILE_DIR)

if getattr(sys, "frozen", False):
    PROJECT_ROOT = os.path.dirname(sys.executable)
    BUNDLE_DIR = getattr(sys, "_MEIPASS", PROJECT_ROOT)
    LOCALES_DIR = os.path.join(PROJECT_ROOT, "locales")
    if not os.path.isdir(LOCALES_DIR):
        LOCALES_DIR = os.path.join(BUNDLE_DIR, "locales")
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
    PROJECT_ROOT = os.path.dirname(APP_DIR)
    LOCALES_DIR = os.path.join(PROJECT_ROOT, "locales")
    if not os.path.isdir(LOCALES_DIR):
        LOCALES_DIR = os.path.join(APP_DIR, "locales")

DEFAULT_LANGUAGE = "en"

_current_language: str = DEFAULT_LANGUAGE
_translations: dict[str, dict] = {}
_available_languages: dict[str, str] = {}
_listeners: list[Callable[[], None]] = []


def _get_locales_search_dirs() -> list[str]:
    dirs = []
    # 1. External locales folder next to executable (for community drop-in translations)
    nuitka_bin = os.environ.get("NUITKA_ONEFILE_BINARY")
    exe_dir = (
        os.path.dirname(os.path.abspath(nuitka_bin))
        if nuitka_bin
        else os.path.dirname(sys.executable)
    )
    exe_locales = os.path.join(exe_dir, "locales")
    if os.path.isdir(exe_locales):
        dirs.append(exe_locales)

    # 2. Bundled / package locales (Nuitka onefile temp dir, PyInstaller MEIPASS, or source paths)
    nuitka_dir = os.environ.get("NUITKA_ONEFILE_DIRECTORY")
    if nuitka_dir:
        nuitka_locales = os.path.join(nuitka_dir, "locales")
        if os.path.isdir(nuitka_locales) and nuitka_locales not in dirs:
            dirs.append(nuitka_locales)

    for candidate in [
        LOCALES_DIR,
        os.path.join(_MODULE_FILE_DIR, "locales"),
        os.path.join(_PARENT_DIR, "locales"),
        os.path.join(getattr(sys, "_MEIPASS", ""), "locales"),
    ]:
        if candidate and os.path.isdir(candidate) and candidate not in dirs:
            dirs.append(candidate)
    return dirs


def _resolve_key(d: dict, keys: list[str]) -> str | None:
    curr = d
    for k in keys:
        try:
            curr = curr[k]
        except (KeyError, TypeError):
            return None
    return curr if isinstance(curr, str) else None


def get_available_languages() -> dict[str, str]:
    """
    Scans the locales/ directory and returns a dictionary of
    language code -> display name (e.g. {'en': 'English', 'hu': 'Magyar'}).
    Any contributor can drop a new .json file here without touching code.
    """
    global _available_languages
    langs: dict[str, str] = {}

    for l_dir in _get_locales_search_dirs():
        if os.path.isdir(l_dir):
            for fname in sorted(os.listdir(l_dir)):
                if fname.lower().endswith(".json"):
                    code = os.path.splitext(fname)[0]
                    if code in langs:
                        continue
                    full_path = os.path.join(l_dir, fname)
                    display_name = code.capitalize()
                    try:
                        with open(full_path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                            if isinstance(data, dict) and "__language_name__" in data:
                                display_name = str(data["__language_name__"])
                    except Exception:
                        pass
                    langs[code] = display_name

    if not langs:
        langs = {DEFAULT_LANGUAGE: "English"}

    _available_languages = langs
    return _available_languages


def load_locale(lang_code: str) -> dict:
    """Loads and caches translation dictionary for a given language code."""
    if lang_code in _translations:
        return _translations[lang_code]

    for l_dir in _get_locales_search_dirs():
        path = os.path.join(l_dir, f"{lang_code}.json")
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        _translations[lang_code] = data
                        return data
            except Exception as e:
                print(f"⚠️ Failed to load locale {lang_code}: {e}")

    _translations[lang_code] = {}
    return _translations[lang_code]


def get_language() -> str:
    """Returns the currently active language code."""
    return _current_language


def set_language(lang_code: str) -> None:
    """
    Sets the active language and notifies all registered UI listeners.
    Falls back to DEFAULT_LANGUAGE if the specified language cannot be found.
    """
    global _current_language
    available = get_available_languages()
    if lang_code not in available:
        lang_code = DEFAULT_LANGUAGE

    # Ensure current and fallback locales are loaded
    load_locale(lang_code)
    if DEFAULT_LANGUAGE not in _translations:
        load_locale(DEFAULT_LANGUAGE)

    old_lang = _current_language
    _current_language = lang_code

    if old_lang != _current_language:
        for cb in list(_listeners):
            try:
                cb()
            except Exception as e:
                # If listener callback failed because widget was destroyed, unregister it
                err_str = str(e).lower()
                if "application has been destroyed" in err_str or "invalid command name" in err_str:
                    if cb in _listeners:
                        _listeners.remove(cb)
                else:
                    print(f"⚠️ Error executing i18n change listener: {e}")


def on_language_change(callback: Callable[[], None]) -> None:
    """Registers a listener to be notified when language changes."""
    if callback not in _listeners:
        _listeners.append(callback)


def remove_language_change_listener(callback: Callable[[], None]) -> None:
    """Unregisters a listener."""
    if callback in _listeners:
        _listeners.remove(callback)


def reset_listeners() -> None:
    """Clears all registered listeners (primarily for testing)."""
    global _listeners
    _listeners = []


def t(key: str, **kwargs) -> str:
    """
    Translates a dotted key (e.g. 'record.start_btn', 'app.title').
    Falls back to English if missing in the active language, or the key string itself.
    Formats placeholders if kwargs are provided.
    """
    # Ensure current language is loaded
    if _current_language not in _translations:
        load_locale(_current_language)
    if DEFAULT_LANGUAGE not in _translations:
        load_locale(DEFAULT_LANGUAGE)

    parts = key.split(".")

    # 1. Try active language
    val = _resolve_key(_translations.get(_current_language, {}), parts)

    # 2. Fallback to English
    if val is None and _current_language != DEFAULT_LANGUAGE:
        val = _resolve_key(_translations.get(DEFAULT_LANGUAGE, {}), parts)

    # 3. Fallback to raw key
    if val is None:
        return key

    # Format if keyword arguments are passed
    if kwargs:
        try:
            return val.format(**kwargs)
        except Exception:
            return val

    return val


# Initialize defaults on import
get_available_languages()
load_locale(DEFAULT_LANGUAGE)
