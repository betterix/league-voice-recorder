"""
Replay API Configuration Manager
Safely auto-detects and configures EnableReplayApi=1 in League of Legends Config/game.cfg.
"""

from __future__ import annotations

import configparser
import logging
import os
import shutil

from app.record import find_league_install_dir

logger = logging.getLogger(__name__)


def locate_game_cfg(install_dir: str | None = None) -> str | None:
    """
    Locates the game.cfg configuration file for League of Legends.

    Returns:
        Absolute path to game.cfg if found, or None.
    """
    # 1. Check provided install_dir
    if install_dir and os.path.isdir(install_dir):
        for candidate in (
            os.path.join(install_dir, "Config", "game.cfg"),
            os.path.join(install_dir, "game.cfg"),
        ):
            if os.path.exists(candidate):
                return os.path.abspath(candidate)

    # 2. Check via find_league_install_dir()
    detected_dir = find_league_install_dir()
    if detected_dir and os.path.isdir(detected_dir):
        for candidate in (
            os.path.join(detected_dir, "Config", "game.cfg"),
            os.path.join(detected_dir, "game.cfg"),
        ):
            if os.path.exists(candidate):
                return os.path.abspath(candidate)

    # 3. Direct drive search fallback
    drives = list("CDEFGHIJ")
    subpaths = [
        r"Riot Games\League of Legends\Config\game.cfg",
        r"Program Files\Riot Games\League of Legends\Config\game.cfg",
        r"Program Files (x86)\Riot Games\League of Legends\Config\game.cfg",
        r"Games\League of Legends\Config\game.cfg",
    ]
    for drive in drives:
        for sub in subpaths:
            cand = f"{drive}:\\{sub}"
            if os.path.exists(cand):
                return os.path.abspath(cand)

    return None


def check_replay_api_status(cfg_path: str | None = None) -> tuple[bool, str | None]:
    """
    Checks if EnableReplayApi=1 is currently set in the [General] section of game.cfg.

    Args:
        cfg_path: Optional direct path to game.cfg. If omitted, auto-locates it.

    Returns:
        Tuple of (is_enabled: bool, located_cfg_path: str | None).
    """
    resolved_path = cfg_path or locate_game_cfg()
    if not resolved_path or not os.path.exists(resolved_path):
        return False, None

    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str  # Preserve case

    encodings = ["utf-8-sig", "utf-8", "cp1252", "latin-1"]
    loaded = False
    for enc in encodings:
        try:
            parser.read(resolved_path, encoding=enc)
            loaded = True
            break
        except Exception as exc:
            logger.debug("Encoding %s failed for %s: %s", enc, resolved_path, exc)
            continue

    if not loaded:
        logger.warning("Failed to parse game.cfg with any standard encoding: %s", resolved_path)
        return False, resolved_path

    # Search for [General] section case-insensitively
    general_section = None
    for sec in parser.sections():
        if sec.strip().lower() == "general":
            general_section = sec
            break

    if not general_section:
        return False, resolved_path

    val = parser.get(general_section, "EnableReplayApi", fallback="0").strip()
    is_enabled = val == "1"
    return is_enabled, resolved_path


def enable_replay_api(cfg_path: str | None = None) -> tuple[bool, str]:
    """
    Safely parses game.cfg with configparser and injects EnableReplayApi=1
    into the [General] section with an automatic backup (game.cfg.bak).

    Args:
        cfg_path: Optional explicit path to game.cfg.

    Returns:
        Tuple of (success: bool, status_message: str).
    """
    resolved_path = cfg_path or locate_game_cfg()
    if not resolved_path or not os.path.exists(resolved_path):
        return (
            False,
            "Could not locate League of Legends game.cfg automatically. Please verify your installation.",
        )

    # Check if already enabled
    is_enabled, _ = check_replay_api_status(resolved_path)
    if is_enabled:
        return True, f"Replay API is already enabled in game.cfg:\n{resolved_path}"

    # Create backup: game.cfg.bak
    bak_path = resolved_path + ".bak"
    try:
        shutil.copy2(resolved_path, bak_path)
    except Exception as exc:
        logger.error("Failed to create backup of %s: %s", resolved_path, exc)
        return False, f"Failed to create backup file ({bak_path}): {exc}"

    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str  # Preserve case of existing keys

    encodings = ["utf-8-sig", "utf-8", "cp1252", "latin-1"]
    loaded = False
    for enc in encodings:
        try:
            parser.read(resolved_path, encoding=enc)
            loaded = True
            break
        except Exception as exc:
            logger.debug("Encoding %s failed for %s: %s", enc, resolved_path, exc)
            continue

    if not loaded:
        return False, f"Failed to parse {resolved_path} with configparser."

    # Identify or create [General] section
    general_sec_name = "General"
    for sec in parser.sections():
        if sec.strip().lower() == "general":
            general_sec_name = sec
            break

    if not parser.has_section(general_sec_name):
        parser.add_section(general_sec_name)

    parser.set(general_sec_name, "EnableReplayApi", "1")

    try:
        with open(resolved_path, "w", encoding="utf-8", newline="\r\n") as f:
            parser.write(f, space_around_delimiters=False)
    except Exception as exc:
        logger.error("Failed writing updated game.cfg to %s: %s", resolved_path, exc)
        # Attempt to restore backup if write failed
        try:
            shutil.copy2(bak_path, resolved_path)
        except Exception:
            pass
        return False, f"Failed writing to {resolved_path}: {exc}"

    # Verify write
    verified, _ = check_replay_api_status(resolved_path)
    if not verified:
        return False, f"Replay API injection could not be verified in {resolved_path}."

    logger.info("Replay API enabled successfully in %s (backup: %s)", resolved_path, bak_path)
    return (
        True,
        f"Successfully enabled Replay API in game.cfg!\n\nLocation: {resolved_path}\nBackup: {bak_path}",
    )
