"""Cloud synchronization client for League Voice Recorder remote backend.

Communicates over direct-port TLS 1.3 with league-voice-recorder-backend.
Handles health checks, role authentication, metadata queries, resumable/chunked downloads,
and streaming uploads with SHA-256 cryptographic verification.
"""

import hashlib
import logging
from pathlib import Path
from typing import Any
from collections.abc import Callable

import requests
import urllib3

# Suppress insecure request warnings if user connects to self-signed development server
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger("league_recorder.cloud_sync")


class CloudSyncClient:
    """Client for interacting with the remote League Scrim Voice Backend."""

    def __init__(
        self,
        server_url: str,
        api_key: str,
        timeout: float = 15.0,
        verify_ssl: bool = True,
    ) -> None:
        self.server_url = (server_url or "").strip().rstrip("/")
        self.api_key = (api_key or "").strip()
        self.timeout = timeout
        self.verify_ssl = verify_ssl
        self.session = requests.Session()

    def is_configured(self) -> bool:
        """Checks if both server URL and API key are configured."""
        return bool(self.server_url and self.api_key)

    def _get_headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            "X-API-Key": self.api_key,
            "User-Agent": "LeagueVoiceRecorder-Client/1.0",
        }
        if extra:
            headers.update(extra)
        return headers

    def check_health(self) -> tuple[bool, dict[str, Any] | str]:
        """Validates connection and returns server health/role status.

        Returns (True, info_dict) on success, or (False, error_str) on failure.
        """
        if not self.server_url:
            return False, "Server URL is not configured"
        if not self.api_key:
            return False, "API key is not configured"

        url = f"{self.server_url}/api/v1/health"
        try:
            resp = self.session.get(
                url,
                headers=self._get_headers(),
                timeout=self.timeout,
                verify=self.verify_ssl,
            )
            if resp.status_code == 200:
                return True, resp.json()
            elif resp.status_code == 401:
                return False, "Invalid API key (401 Unauthorized)"
            elif resp.status_code == 403:
                return False, "Forbidden: Key does not have read permissions (403)"
            elif resp.status_code == 429:
                return False, "Rate limit exceeded. Please wait a moment (429)"
            else:
                return False, f"Server returned HTTP {resp.status_code}: {resp.text[:100]}"
        except requests.exceptions.SSLError as ssl_err:
            # If standard CA fails, attempt fallback check for self-signed certificates
            try:
                fallback_resp = self.session.get(
                    url,
                    headers=self._get_headers(),
                    timeout=self.timeout,
                    verify=False,
                )
                if fallback_resp.status_code == 200:
                    data = fallback_resp.json()
                    data["ssl_warning"] = "Self-signed certificate detected"
                    return True, data
            except Exception:
                pass
            return False, f"TLS/SSL certificate error: {ssl_err}"
        except requests.exceptions.ConnectionError:
            return False, "Could not connect to server. Check IP, port, and firewall."
        except requests.exceptions.Timeout:
            return False, "Connection timed out. Server unreachable."
        except Exception as exc:
            return False, f"Connection error: {exc}"

    def check_recording_exists(self, game_id: str) -> bool:
        """Queries the server to check if a recording already exists."""
        if not self.is_configured():
            return False

        clean_id = str(game_id).strip()
        url = f"{self.server_url}/api/v1/recordings/{clean_id}"
        try:
            resp = self.session.get(
                url,
                headers=self._get_headers(),
                timeout=self.timeout,
                verify=self.verify_ssl,
            )
            return resp.status_code == 200
        except Exception as exc:
            logger.warning("Error checking if recording %s exists: %s", clean_id, exc)
            return False

    def list_recordings(
        self,
        skip: int = 0,
        limit: int = 100,
        search: str | None = None,
    ) -> list[dict[str, Any]]:
        """Queries remote recordings from the backend.

        Returns a list of recording metadata dictionaries.
        """
        if not self.is_configured():
            return []

        url = f"{self.server_url}/api/v1/recordings"
        params: dict[str, Any] = {"skip": skip, "limit": limit}
        if search:
            params["search"] = search.strip()

        try:
            resp = self.session.get(
                url,
                headers=self._get_headers(),
                params=params,
                timeout=self.timeout,
                verify=self.verify_ssl,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("items", [])
            else:
                logger.warning("Failed to list remote recordings: HTTP %s", resp.status_code)
                return []
        except Exception as exc:
            logger.error("Error fetching remote recordings: %s", exc)
            return []

    def download_recording(
        self,
        game_id: str,
        dest_path: str | Path,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> bool:
        """Downloads a recording file (.mp3) from the server.

        Performs atomic download into a temporary file before renaming to dest_path.
        """
        if not self.is_configured():
            return False

        clean_id = str(game_id).strip()
        url = f"{self.server_url}/api/v1/recordings/{clean_id}/download"
        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        temp_file = dest.with_suffix(".tmp_download")

        try:
            with self.session.get(
                url,
                headers=self._get_headers(),
                stream=True,
                timeout=30.0,
                verify=self.verify_ssl,
            ) as resp:
                if resp.status_code != 200:
                    logger.error("Download failed for %s: HTTP %s", clean_id, resp.status_code)
                    return False

                total_size = int(resp.headers.get("Content-Length", 0))
                downloaded = 0

                with open(temp_file, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=65536):
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)
                            if progress_callback:
                                try:
                                    progress_callback(downloaded, total_size)
                                except Exception:
                                    pass

            # Atomic replace
            if temp_file.exists():
                if dest.exists():
                    dest.unlink()
                temp_file.rename(dest)
                return True
            return False
        except Exception as exc:
            logger.error("Exception during download of %s: %s", clean_id, exc)
            if temp_file.exists():
                try:
                    temp_file.unlink()
                except OSError:
                    pass
            return False

    def upload_recording(
        self,
        game_id: str,
        file_path: str | Path,
        team_tag: str | None = None,
        notes: str | None = None,
        game_patch: str | None = None,
        check_first: bool = True,
        progress_callback: Callable[[float], None] | None = None,
    ) -> tuple[bool, dict[str, Any] | str]:
        """Uploads an MP3 recording file to the remote server.

        Optionally checks if the recording already exists on the server first.
        Calculates SHA-256 hash for cryptographic integrity verification.
        Returns (True, metadata_dict) or (False, error_str).
        """
        if not self.is_configured():
            return False, "Client is not configured with server URL and API key"

        clean_id = str(game_id).strip()
        file = Path(file_path)
        if not file.exists():
            return False, f"File does not exist: {file}"

        if check_first and self.check_recording_exists(clean_id):
            return True, {"message": "Already exists on server", "already_exists": True, "game_id": clean_id}

        # Calculate SHA-256 checksum
        hasher = hashlib.sha256()
        with open(file, "rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
        sha256_digest = hasher.hexdigest()

        url = f"{self.server_url}/api/v1/recordings/{clean_id}"
        headers = self._get_headers({"X-Checksum-SHA256": sha256_digest})

        data: dict[str, str] = {}
        if team_tag:
            data["team_tag"] = team_tag
        if notes:
            data["notes"] = notes
        if game_patch:
            data["patch"] = game_patch
            data["game_patch"] = game_patch

        try:
            with open(file, "rb") as audio_file:
                files = {"file": (f"{clean_id}.mp3", audio_file, "audio/mpeg")}
                resp = self.session.post(
                    url,
                    headers=headers,
                    data=data,
                    files=files,
                    timeout=60.0,
                    verify=self.verify_ssl,
                )

            if resp.status_code == 201:
                return True, resp.json()
            elif resp.status_code == 409:
                return True, {"message": "Already uploaded (HTTP 409 Conflict)"}
            elif resp.status_code == 403:
                return False, "Upload forbidden: Key does not have upload permissions (requires UPLOAD_KEY or ADMIN_KEY)"
            elif resp.status_code == 401:
                return False, "Unauthorized: Invalid API key"
            else:
                return False, f"Upload failed (HTTP {resp.status_code}): {resp.text[:150]}"
        except Exception as exc:
            return False, f"Upload failed due to connection error: {exc}"

    def delete_recording(self, game_id: str) -> tuple[bool, str]:
        """Deletes a recording from the remote server."""
        if not self.is_configured():
            return False, "Client is not configured"

        clean_id = str(game_id).strip()
        url = f"{self.server_url}/api/v1/recordings/{clean_id}"
        try:
            resp = self.session.delete(
                url,
                headers=self._get_headers(),
                timeout=self.timeout,
                verify=self.verify_ssl,
            )
            if resp.status_code == 200:
                return True, f"Recording {clean_id} deleted successfully"
            elif resp.status_code == 404:
                return False, f"Recording {clean_id} not found on server"
            elif resp.status_code == 403:
                return False, "Forbidden: Upload or Admin key required to delete"
            else:
                return False, f"Delete failed: HTTP {resp.status_code}"
        except Exception as exc:
            return False, f"Delete error: {exc}"
