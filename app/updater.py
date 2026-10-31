"""
Automatic update checker and in-place executable updater for League Voice Recorder.
Queries GitHub Releases for newer semantic versions and handles seamless Windows
distribution extraction (.zip or standalone .exe), in-place replacement, and relaunch.
"""

import os
import re
import subprocess
import sys
import tempfile
import time
import zipfile

import requests

GITHUB_REPO = "betterix/league-voice-recorder"
RELEASES_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"


def parse_version_tuple(version_str):
    """
    Parses a version string like 'v0.3.1', '0.3.2-beta', or '1.0' into an integer tuple.
    Example: 'v0.3.1' -> (0, 3, 1)
    """
    if not version_str:
        return (0, 0, 0)
    cleaned = str(version_str).strip().lstrip("vV")
    match = re.match(r"^(\d+(?:\.\d+)*)", cleaned)
    if not match:
        return (0, 0, 0)
    parts = match.group(1).split(".")
    return tuple(int(p) for p in parts)


def is_running_as_binary():
    """Returns True if the application is executing as a standalone compiled executable."""
    exe = sys.executable or ""
    if not exe.lower().endswith(".exe"):
        return False
    base = os.path.basename(exe).lower()
    return "python" not in base and "pytest" not in base


def clean_old_executables():
    """
    Deletes any leftover *.old files in the executable's directory
    from previous in-place updates.
    """
    if not is_running_as_binary():
        return
    try:
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        old_exe = sys.executable + ".old"
        if os.path.exists(old_exe):
            os.remove(old_exe)
        for f in os.listdir(exe_dir):
            if f.endswith(".old") and f.startswith(("LeagueVoiceRecorder", "main")):
                try:
                    os.remove(os.path.join(exe_dir, f))
                except Exception:
                    pass
    except Exception:
        pass


def check_for_update(current_version, repo=GITHUB_REPO, timeout=5.0):
    """
    Queries GitHub Releases for the latest version and compares with current_version.
    Returns a dict with update status, version details, and asset download URL.
    Prioritizes .zip distribution archives, falling back to standalone .exe binaries.
    """
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "LeagueVoiceRecorder-Updater",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
        if resp.status_code != 200:
            return {
                "available": False,
                "error": f"HTTP {resp.status_code}",
                "current_version": current_version,
            }

        data = resp.json()
        latest_tag = data.get("tag_name", "").strip()
        latest_tuple = parse_version_tuple(latest_tag)
        current_tuple = parse_version_tuple(current_version)

        # Look for release assets: prefer .zip distribution archives, fallback to .exe
        chosen_asset = None
        for asset in data.get("assets", []):
            name = asset.get("name", "").lower()
            if name.endswith(".zip"):
                chosen_asset = asset
                break
            elif name.endswith(".exe") and not chosen_asset:
                chosen_asset = asset

        is_newer = latest_tuple > current_tuple
        is_zip = chosen_asset.get("name", "").lower().endswith(".zip") if chosen_asset else False

        return {
            "available": is_newer,
            "latest_version": latest_tag.lstrip("vV"),
            "current_version": str(current_version).lstrip("vV"),
            "download_url": chosen_asset.get("browser_download_url") if chosen_asset else None,
            "asset_name": chosen_asset.get("name") if chosen_asset else None,
            "asset_size": chosen_asset.get("size", 0) if chosen_asset else 0,
            "is_zip": is_zip,
            "html_url": data.get("html_url"),
            "notes": data.get("body", ""),
            "published_at": data.get("published_at", ""),
        }
    except Exception as e:
        return {
            "available": False,
            "error": str(e),
            "current_version": current_version,
        }


def download_and_install_update(download_url, progress_callback=None, timeout=60.0):
    """
    Downloads update binary or .zip archive with progress tracking and performs in-place replacement.
    - If .zip: extracts the package to locate updated executables, DLLs, locales, and assets.
    - On compiled Windows binary: launches a silent background updater script that waits for current process
      exit, copies all updated files cleanly using robocopy into the installation directory, relaunches
      the application, and cleans up temporary archives.
    - In Python dev mode: extracts/saves the update into a temporary directory without modifying the python runtime.
    """
    if not download_url:
        raise ValueError("No download URL provided for update.")

    target_exe = os.path.abspath(sys.executable)
    target_dir = os.path.dirname(target_exe).rstrip("\\/")
    is_binary = is_running_as_binary()
    current_pid = os.getpid()

    temp_dir = tempfile.gettempdir()
    url_lower = download_url.lower().split("?")[0]
    if url_lower.endswith(".zip"):
        ext = ".zip"
    elif url_lower.endswith(".exe"):
        ext = ".exe"
    else:
        ext = ".tmp"

    download_path = os.path.join(temp_dir, f"lvr_update_{int(time.time())}{ext}")

    # 1. Download stream with progress reporting
    headers = {"User-Agent": "LeagueVoiceRecorder-Updater"}
    with requests.get(download_url, headers=headers, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        total_size = int(r.headers.get("content-length", 0))
        downloaded = 0

        with open(download_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback and total_size > 0:
                        pct = int((downloaded / total_size) * 100)
                        progress_callback(min(100, pct))

    if progress_callback:
        progress_callback(100)

    # 2. Check if archive is a .zip distribution package
    is_zip = zipfile.is_zipfile(download_path)
    temp_extract_dir = None
    extracted_app_dir = None

    if is_zip:
        temp_extract_dir = tempfile.mkdtemp(prefix="lvr_extract_")
        with zipfile.ZipFile(download_path, "r") as z:
            z.extractall(temp_extract_dir)

        # Locate the directory containing LeagueVoiceRecorder.exe or any .exe inside extracted folder
        for root, _dirs, files in os.walk(temp_extract_dir):
            for f in files:
                if f.lower() in ("leaguevoicerecorder.exe", "main.exe"):
                    extracted_app_dir = root
                    break
                elif f.lower().endswith(".exe") and not extracted_app_dir:
                    extracted_app_dir = root
            if extracted_app_dir and os.path.exists(
                os.path.join(extracted_app_dir, "LeagueVoiceRecorder.exe")
            ):
                break

        if not extracted_app_dir:
            extracted_app_dir = temp_extract_dir

        extracted_app_dir = extracted_app_dir.rstrip("\\/")

    # 3. Development / Test mode safety check
    if not is_binary:
        # Dev mode: return extracted folder or download path without touching python interpreter
        return extracted_app_dir or download_path

    # 4. Windows Binary In-Place Update via Background Batch Updater
    bat_path = os.path.join(temp_dir, f"lvr_apply_update_{int(time.time())}.bat")

    if is_zip and extracted_app_dir:
        bat_content = f"""@echo off
setlocal
timeout /t 1 /nobreak >nul

set RETRIES=0
:WAIT_LOOP
tasklist /fi "PID eq {current_pid}" 2>nul | find "{current_pid}" >nul
if not errorlevel 1 (
    timeout /t 1 /nobreak >nul
    set /a RETRIES+=1
    if %RETRIES% lss 8 goto WAIT_LOOP
)

robocopy "{extracted_app_dir}" "{target_dir}" /E /IS /IT /R:3 /W:1 /NFL /NDL /NJH /NJS /nc /ns /np

if exist "{target_exe}" (
    start "" "{target_exe}"
)

rd /s /q "{temp_extract_dir}" >nul 2>&1
del /f /q "{download_path}" >nul 2>&1
del "%~f0" >nul 2>&1
"""
    else:
        bat_content = f"""@echo off
setlocal
timeout /t 1 /nobreak >nul

set RETRIES=0
:WAIT_LOOP
tasklist /fi "PID eq {current_pid}" 2>nul | find "{current_pid}" >nul
if not errorlevel 1 (
    timeout /t 1 /nobreak >nul
    set /a RETRIES+=1
    if %RETRIES% lss 8 goto WAIT_LOOP
)

copy /y "{download_path}" "{target_exe}" >nul

if exist "{target_exe}" (
    start "" "{target_exe}"
)

del /f /q "{download_path}" >nul 2>&1
del "%~f0" >nul 2>&1
"""

    with open(bat_path, "w", encoding="utf-8") as f:
        f.write(bat_content)

    # Detached background process with hidden console
    CREATE_NO_WINDOW = 0x08000000
    subprocess.Popen(
        ["cmd.exe", "/c", bat_path],
        creationflags=CREATE_NO_WINDOW,
        close_fds=True,
    )
    time.sleep(0.1)
    os._exit(0)
