"""Scrim recording metadata manager and ID3 tag embedder.

Stores local metadata (team tag, notes, recorded date) in a persistent JSON sidecar
and embeds standard ID3v2 tags directly into .mp3 files via FFmpeg stream copy.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
from pathlib import Path
import subprocess

from app.config import get_ffmpeg_path

logger = logging.getLogger("scrim_meta")
METADATA_FILENAME = "scrim_metadata.json"


def get_metadata_path(record_folder: str | Path) -> Path:
    """Returns the path to the scrim metadata JSON file."""
    return Path(record_folder) / METADATA_FILENAME


def read_mp3_metadata(mp3_path: str | Path) -> dict:
    """Reads embedded ID3 tags from an MP3 file (Title, Artist, Album/Patch, Comment, Date).

    Returns a dictionary with keys: game_id, team_tag, notes, game_patch, recorded_at.
    """
    target = Path(mp3_path)
    if not target.exists() or not target.is_file():
        return {}

    clean_id = target.stem
    result = {
        "game_id": clean_id,
        "team_tag": "",
        "notes": "",
        "game_patch": "",
        "recorded_at": "",
    }

    try:
        mtime = os.path.getmtime(target)
        result["recorded_at"] = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
    except Exception:
        pass

    ffmpeg_cmd = get_ffmpeg_path()
    try:
        proc = subprocess.run(
            [ffmpeg_cmd, "-i", str(target), "-f", "ffmetadata", "-"],
            capture_output=True,
            text=True,
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            timeout=3.0,
        )
        if proc.returncode == 0 and proc.stdout:
            for line in proc.stdout.splitlines():
                if "=" not in line or line.startswith(";"):
                    continue
                k, v = line.split("=", 1)
                k = k.strip().lower()
                v = v.strip()
                if k == "artist":
                    result["team_tag"] = v
                elif k == "comment":
                    result["notes"] = v
                elif k == "album":
                    if v.lower().startswith("patch"):
                        v = v[5:].strip()
                    result["game_patch"] = v
                elif k == "date":
                    result["recorded_at"] = v[:10]
                elif k == "title" and v.lower().startswith("game "):
                    parsed_id = v[5:].strip()
                    if parsed_id:
                        result["game_id"] = parsed_id
    except Exception as exc:
        logger.debug("Failed to read metadata via ffmpeg from %s: %s", target, exc)

    return result


def load_all_scrim_metadata(record_folder: str | Path) -> dict[str, dict]:
    """Loads all saved scrim metadata from the recordings folder.

    If the metadata JSON does not exist or is missing entries for existing MP3 files,
    it automatically scans and reconstructs metadata by reading embedded ID3 tags directly from the MP3s.
    """
    folder = Path(record_folder)
    meta_path = get_metadata_path(folder)
    data = {}
    if meta_path.exists():
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    data = loaded
        except Exception as exc:
            logger.warning("Failed to load scrim metadata from %s: %s", meta_path, exc)

    # Check if folder exists and has MP3 files missing from data
    if folder.exists() and folder.is_dir():
        needs_persist = False
        try:
            for entry in os.scandir(folder):
                if entry.is_file() and entry.name.lower().endswith(".mp3"):
                    gid = entry.name[:-4]
                    if gid not in data:
                        parsed = read_mp3_metadata(entry.path)
                        if parsed:
                            data[gid] = parsed
                            needs_persist = True
                    else:
                        # If existing JSON entry is missing patch or team_tag, attempt to fill from ID3
                        entry_meta = data[gid]
                        if not entry_meta.get("game_patch") or not entry_meta.get("team_tag"):
                            parsed = read_mp3_metadata(entry.path)
                            updated = False
                            if parsed.get("game_patch") and not entry_meta.get("game_patch"):
                                entry_meta["game_patch"] = parsed["game_patch"]
                                updated = True
                            if parsed.get("team_tag") and not entry_meta.get("team_tag"):
                                entry_meta["team_tag"] = parsed["team_tag"]
                                updated = True
                            if parsed.get("notes") and not entry_meta.get("notes"):
                                entry_meta["notes"] = parsed["notes"]
                                updated = True
                            if updated:
                                needs_persist = True
            if needs_persist:
                temp_meta = meta_path.with_suffix(".tmp_meta")
                with open(temp_meta, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                temp_meta.replace(meta_path)
        except Exception as exc:
            logger.debug("Error while scanning MP3 files for metadata reconstruction: %s", exc)

    return data


def get_scrim_metadata(
    record_folder: str | Path,
    game_id: str,
    filepath: str | Path | None = None,
) -> dict:
    """Retrieves metadata for a specific game_id, populating defaults if missing."""
    clean_id = str(game_id).strip()
    all_meta = load_all_scrim_metadata(record_folder)
    if clean_id in all_meta and isinstance(all_meta[clean_id], dict):
        item = dict(all_meta[clean_id])
        if not item.get("game_id"):
            item["game_id"] = clean_id
        if item.get("recorded_at"):
            item["recorded_at"] = str(item["recorded_at"])[:10]
        return item

    # If not in all_meta, check filepath directly via read_mp3_metadata
    if filepath and os.path.exists(filepath) and str(filepath).lower().endswith(".mp3"):
        parsed = read_mp3_metadata(filepath)
        if parsed:
            return parsed

    # Fallback to automatic date extraction
    recorded_at = ""
    if filepath and os.path.exists(filepath):
        try:
            mtime = os.path.getmtime(filepath)
            recorded_at = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
        except Exception:
            pass

    if not recorded_at:
        recorded_at = datetime.datetime.now().strftime("%Y-%m-%d")

    return {
        "game_id": clean_id,
        "team_tag": "",
        "notes": "",
        "game_patch": "",
        "recorded_at": recorded_at,
    }


def save_scrim_metadata(
    record_folder: str | Path,
    game_id: str,
    meta: dict,
    embed_id3: bool = True,
    filepath: str | Path | None = None,
) -> bool:
    """Saves or updates metadata for a game_id and optionally embeds ID3 tags."""
    clean_id = str(game_id).strip()
    folder = Path(record_folder)
    folder.mkdir(parents=True, exist_ok=True)
    meta_path = get_metadata_path(folder)

    all_meta = load_all_scrim_metadata(folder)
    current = dict(all_meta.get(clean_id, {}))
    current.update(meta)
    current["game_id"] = clean_id
    if "recorded_at" not in current or not current["recorded_at"]:
        current["recorded_at"] = datetime.datetime.now().strftime("%Y-%m-%d")
    else:
        current["recorded_at"] = str(current["recorded_at"])[:10]
    current["updated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    all_meta[clean_id] = current

    # Atomic write to metadata JSON
    temp_meta = meta_path.with_suffix(".tmp_meta")
    try:
        with open(temp_meta, "w", encoding="utf-8") as f:
            json.dump(all_meta, f, indent=2, ensure_ascii=False)
        temp_meta.replace(meta_path)
    except Exception as exc:
        logger.error("Failed to save scrim metadata to %s: %s", meta_path, exc)
        if temp_meta.exists():
            try:
                temp_meta.unlink()
            except OSError:
                pass
        return False

    # Embed ID3 tags into MP3 file
    if embed_id3:
        target_file = Path(filepath) if filepath else folder / f"{clean_id}.mp3"
        if target_file.exists():
            embed_mp3_metadata(
                mp3_path=target_file,
                game_id=clean_id,
                team_tag=current.get("team_tag", ""),
                notes=current.get("notes", ""),
                recorded_at=current.get("recorded_at", ""),
                game_patch=current.get("game_patch", ""),
            )

    return True


def embed_mp3_metadata(
    mp3_path: str | Path,
    game_id: str,
    team_tag: str = "",
    notes: str = "",
    recorded_at: str = "",
    game_patch: str = "",
) -> bool:
    """Uses FFmpeg stream copy to write standard ID3 tags into the MP3 without re-encoding.

    Preserves original file modification time.
    """
    target = Path(mp3_path)
    if not target.exists():
        return False

    ffmpeg_cmd = get_ffmpeg_path()
    temp_out = target.with_suffix(".tmp_tag.mp3")

    try:
        mtime_orig = os.path.getmtime(target)
    except Exception:
        mtime_orig = None

    date_str = (recorded_at or "")[:10]
    cmd = [
        ffmpeg_cmd,
        "-y",
        "-i",
        str(target),
        "-codec",
        "copy",
        "-metadata",
        f"title=Game {game_id}",
    ]
    if team_tag:
        cmd.extend(["-metadata", f"artist={team_tag}"])
    if notes:
        cmd.extend(["-metadata", f"comment={notes}"])
    if date_str:
        cmd.extend(["-metadata", f"date={date_str}"])
    if game_patch:
        cmd.extend(["-metadata", f"album=Patch {game_patch}"])

    cmd.append(str(temp_out))

    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if res.returncode == 0 and temp_out.exists() and temp_out.stat().st_size > 0:
            temp_out.replace(target)
            if mtime_orig is not None:
                try:
                    os.utime(target, (mtime_orig, mtime_orig))
                except Exception:
                    pass
            return True
        else:
            logger.warning("FFmpeg metadata tag embedding failed: %s", res.stderr)
            if temp_out.exists():
                temp_out.unlink()
            return False
    except Exception as exc:
        logger.error("Exception embedding ID3 tags via FFmpeg: %s", exc)
        if temp_out.exists():
            try:
                temp_out.unlink()
            except OSError:
                pass
        return False
