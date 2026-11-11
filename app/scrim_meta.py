"""Scrim recording metadata manager and ID3 tag embedder.

Stores all metadata (Match ID, opponent VS team, game patch, notes, and recorded date)
directly inside the .mp3 audio files as standard ID3v2 tags.
No separate JSON sidecar file is needed.
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
    """Returns the legacy path to scrim_metadata.json for backwards compatibility."""
    return Path(record_folder) / METADATA_FILENAME


def build_id3_tag(
    game_id: str,
    team_tag: str = "",
    notes: str = "",
    recorded_at: str = "",
    game_patch: str = "",
) -> bytes:
    """Constructs a standard ID3v2.4 binary tag containing scrim metadata."""
    frames_data = bytearray()

    def add_text_frame(fid: str, text: str) -> None:
        if not text:
            return
        payload = b"\x03" + text.encode("utf-8")
        sz = len(payload)
        synch_sz = bytes([(sz >> 21) & 0x7F, (sz >> 14) & 0x7F, (sz >> 7) & 0x7F, sz & 0x7F])
        frames_data.extend(fid.encode("ascii") + synch_sz + b"\x00\x00" + payload)

    add_text_frame("TIT2", f"Game {game_id}")
    if team_tag:
        add_text_frame("TPE1", team_tag)
    if game_patch:
        add_text_frame("TALB", f"Patch {game_patch}")
    if recorded_at:
        add_text_frame("TDRC", recorded_at[:10])
    if notes:
        # TXXX comment frame
        payload = b"\x03comment\x00" + notes.encode("utf-8")
        sz = len(payload)
        synch_sz = bytes([(sz >> 21) & 0x7F, (sz >> 14) & 0x7F, (sz >> 7) & 0x7F, sz & 0x7F])
        frames_data.extend(b"TXXX" + synch_sz + b"\x00\x00" + payload)

    tag_sz = len(frames_data)
    hdr_sz = bytes([(tag_sz >> 21) & 0x7F, (tag_sz >> 14) & 0x7F, (tag_sz >> 7) & 0x7F, tag_sz & 0x7F])
    return b"ID3\x04\x00\x00" + hdr_sz + bytes(frames_data)


def _parse_id3_frames(data: bytes) -> dict[str, str]:
    """Fast in-memory ID3v2 parser to read frames in <0.1ms without launching subprocesses."""
    if not data.startswith(b"ID3") or len(data) < 10:
        return {}
    ver = data[3]
    size = ((data[6] & 0x7F) << 21) | ((data[7] & 0x7F) << 14) | ((data[8] & 0x7F) << 7) | (data[9] & 0x7F)
    tag_data = data[10 : 10 + size]
    pos = 0
    frames = {}
    while pos + 10 <= len(tag_data):
        fid = tag_data[pos : pos + 4]
        if fid[0] == 0 or not fid.isalnum():
            break
        fsize_bytes = tag_data[pos + 4 : pos + 8]
        if ver == 4:
            fsize = (
                ((fsize_bytes[0] & 0x7F) << 21)
                | ((fsize_bytes[1] & 0x7F) << 14)
                | ((fsize_bytes[2] & 0x7F) << 7)
                | (fsize_bytes[3] & 0x7F)
            )
        else:
            fsize = int.from_bytes(fsize_bytes, "big")
        pos += 10
        content = tag_data[pos : pos + fsize]
        pos += fsize
        if not content:
            continue
        enc = content[0]
        val = content[1:]
        tag_name = fid.decode("ascii", errors="replace")
        try:
            if enc == 3:
                text = val.decode("utf-8", errors="replace").rstrip("\x00")
            elif enc == 1:
                text = val.decode("utf-16", errors="replace").rstrip("\x00")
            else:
                text = val.decode("latin1", errors="replace").rstrip("\x00")

            if tag_name == "TXXX" and "\x00" in text:
                _, text = text.split("\x00", 1)
            elif tag_name == "COMM" and len(val) > 3:
                comm_body = val[3:]
                if enc == 3:
                    b_text = comm_body.decode("utf-8", errors="replace").rstrip("\x00")
                elif enc == 1:
                    b_text = comm_body.decode("utf-16", errors="replace").rstrip("\x00")
                else:
                    b_text = comm_body.decode("latin1", errors="replace").rstrip("\x00")
                if "\x00" in b_text:
                    _, text = b_text.split("\x00", 1)
                else:
                    text = b_text
            frames[tag_name] = text
        except Exception:
            pass
    return frames


def read_mp3_metadata(mp3_path: str | Path) -> dict:
    """Reads embedded ID3 tags directly from an MP3 file (Title, Artist, Album/Patch, Comment, Date).

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

    # 1. Fast in-memory ID3 parse
    try:
        with open(target, "rb") as f:
            header_bytes = f.read(16384)
        if header_bytes.startswith(b"ID3"):
            frames = _parse_id3_frames(header_bytes)
            if frames:
                if "TPE1" in frames:
                    result["team_tag"] = frames["TPE1"]
                if "TALB" in frames:
                    alb = frames["TALB"].strip()
                    if alb.lower().startswith("patch"):
                        alb = alb[5:].strip()
                    result["game_patch"] = alb
                if "TDRC" in frames and len(frames["TDRC"]) >= 10:
                    result["recorded_at"] = frames["TDRC"][:10]
                elif "TYER" in frames and "TDAT" in frames and len(frames["TYER"]) >= 4 and len(frames["TDAT"]) >= 4:
                    y = frames["TYER"][:4]
                    d = frames["TDAT"][:2]
                    m = frames["TDAT"][2:4]
                    result["recorded_at"] = f"{y}-{m}-{d}"
                elif "TYER" in frames and len(frames["TYER"]) >= 10:
                    result["recorded_at"] = frames["TYER"][:10]
                if "TXXX" in frames:
                    result["notes"] = frames["TXXX"]
                elif "COMM" in frames:
                    result["notes"] = frames["COMM"]
                if "TIT2" in frames and frames["TIT2"].lower().startswith("game "):
                    gid = frames["TIT2"][5:].strip()
                    if gid:
                        result["game_id"] = gid
                return result
    except Exception:
        pass

    # 2. Fallback to FFmpeg ffmetadata extraction
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


def embed_mp3_metadata(
    mp3_path: str | Path,
    game_id: str,
    team_tag: str = "",
    notes: str = "",
    recorded_at: str = "",
    game_patch: str = "",
) -> bool:
    """Embeds standard ID3v2.3 tags directly into an MP3 file via FFmpeg (with pure Python fallback).

    Uses ID3v2.3 for 100% native Windows Explorer, Groove, and VLC Album/Artist tag compatibility.
    """
    target = Path(mp3_path)
    if not target.exists():
        return False

    try:
        mtime_orig = os.path.getmtime(target)
    except Exception:
        mtime_orig = None

    ffmpeg_cmd = get_ffmpeg_path()
    temp_out = target.with_suffix(".tmp_tag.mp3")

    date_str = (recorded_at or "")[:10]
    album_val = f"Patch {game_patch}" if game_patch else ""

    cmd = [
        ffmpeg_cmd,
        "-y",
        "-i",
        str(target),
        "-codec",
        "copy",
        "-id3v2_version",
        "3",
        "-metadata",
        f"title=Game {game_id}",
    ]
    if team_tag:
        cmd.extend(["-metadata", f"artist={team_tag}"])
    if notes:
        cmd.extend(["-metadata", f"comment={notes}"])
    if date_str:
        cmd.extend(["-metadata", f"date={date_str}"])
    if album_val:
        cmd.extend(["-metadata", f"album={album_val}"])

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
    except Exception as exc:
        logger.debug("FFmpeg metadata tag embedding failed: %s", exc)
    finally:
        if temp_out.exists():
            try:
                temp_out.unlink()
            except OSError:
                pass

    # Fallback to direct byte writing if FFmpeg fails or file is a mock/test stub
    tag_bytes = build_id3_tag(
        game_id=game_id,
        team_tag=team_tag,
        notes=notes,
        recorded_at=recorded_at,
        game_patch=game_patch,
    )
    try:
        data = target.read_bytes()
        if data.startswith(b"ID3") and len(data) >= 10:
            sz = ((data[6] & 0x7F) << 21) | ((data[7] & 0x7F) << 14) | ((data[8] & 0x7F) << 7) | (data[9] & 0x7F)
            audio = data[10 + sz :]
        else:
            audio = data

        target.write_bytes(tag_bytes + audio)

        if mtime_orig is not None:
            try:
                os.utime(target, (mtime_orig, mtime_orig))
            except Exception:
                pass
        return True
    except Exception as exc:
        logger.error("Failed to embed ID3 tags directly into %s: %s", target, exc)
        return False


def load_all_scrim_metadata(record_folder: str | Path) -> dict[str, dict]:
    """Loads metadata for all MP3 recordings in the folder directly from their embedded ID3 tags."""
    folder = Path(record_folder)
    results = {}
    if not folder.exists() or not folder.is_dir():
        return results

    # If any legacy scrim_metadata.json exists, migrate it into the MP3s and delete it
    legacy_json = folder / METADATA_FILENAME
    legacy_data = {}
    if legacy_json.exists():
        try:
            with open(legacy_json, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    legacy_data = loaded
        except Exception:
            pass

    try:
        for entry in os.scandir(folder):
            if entry.is_file() and entry.name.lower().endswith(".mp3"):
                gid = entry.name[:-4]
                meta = read_mp3_metadata(entry.path)
                if gid in legacy_data:
                    leg = legacy_data[gid]
                    updated = False
                    if leg.get("team_tag") and not meta.get("team_tag"):
                        meta["team_tag"] = leg["team_tag"]
                        updated = True
                    if leg.get("notes") and not meta.get("notes"):
                        meta["notes"] = leg["notes"]
                        updated = True
                    if (leg.get("game_patch") or leg.get("patch")) and not meta.get("game_patch"):
                        meta["game_patch"] = leg.get("game_patch") or leg.get("patch")
                        updated = True
                    if leg.get("recorded_at") and not meta.get("recorded_at"):
                        meta["recorded_at"] = leg["recorded_at"][:10]
                        updated = True
                    if updated:
                        embed_mp3_metadata(
                            entry.path,
                            game_id=gid,
                            team_tag=meta.get("team_tag", ""),
                            notes=meta.get("notes", ""),
                            recorded_at=meta.get("recorded_at", ""),
                            game_patch=meta.get("game_patch", ""),
                        )
                results[gid] = meta
    except Exception as exc:
        logger.debug("Error loading scrim metadata from MP3 files: %s", exc)

    # Clean up obsolete legacy JSON file once migrated
    if legacy_json.exists():
        try:
            legacy_json.unlink()
        except Exception:
            pass

    return results


def get_scrim_metadata(
    record_folder: str | Path,
    game_id: str,
    filepath: str | Path | None = None,
) -> dict:
    """Retrieves metadata for a specific game_id directly from its MP3 file."""
    clean_id = str(game_id).strip()
    target = Path(filepath) if filepath else (Path(record_folder) / f"{clean_id}.mp3")
    if target.exists() and target.is_file():
        meta = read_mp3_metadata(target)
        if meta:
            return meta

    return {
        "game_id": clean_id,
        "team_tag": "",
        "notes": "",
        "game_patch": "",
        "recorded_at": datetime.datetime.now().strftime("%Y-%m-%d"),
    }


def save_scrim_metadata(
    record_folder: str | Path,
    game_id: str,
    meta: dict,
    embed_id3: bool = True,
    filepath: str | Path | None = None,
) -> bool:
    """Saves metadata directly into the MP3 file using embedded ID3 tags."""
    clean_id = str(game_id).strip()
    target_file = Path(filepath) if filepath else (Path(record_folder) / f"{clean_id}.mp3")
    if not target_file.exists():
        return False

    current = read_mp3_metadata(target_file)
    for k, v in meta.items():
        if v or k not in current:
            current[k] = v
    current["game_id"] = clean_id
    if not current.get("recorded_at"):
        current["recorded_at"] = datetime.datetime.now().strftime("%Y-%m-%d")
    else:
        current["recorded_at"] = str(current["recorded_at"])[:10]

    return embed_mp3_metadata(
        mp3_path=target_file,
        game_id=clean_id,
        team_tag=current.get("team_tag", ""),
        notes=current.get("notes", ""),
        recorded_at=current.get("recorded_at", ""),
        game_patch=current.get("game_patch", ""),
    )
