"""
Automated VirusTotal scanner and release verification reporter.
Scans compiled Windows binaries, uploads to VirusTotal API v3,
and appends verified scan links and SHA256 checksums to the GitHub Release.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import time

# Ensure Windows consoles don't crash on unicode emojis (e.g. 🛡️)
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

try:
    import requests
except ImportError:
    print("[ERROR] 'requests' library is required. Install with 'uv pip install requests'.", file=sys.stderr)
    sys.exit(1)

VT_API_BASE = "https://www.virustotal.com/api/v3"


def compute_sha256(filepath: str | Path) -> str:
    """Computes the SHA256 checksum of a file in streaming chunks."""
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(1024 * 1024):
            hasher.update(chunk)
    return hasher.hexdigest().lower()


def scan_file_virustotal(filepath: str | Path, api_key: str) -> dict:
    """
    Checks or uploads a binary to VirusTotal API v3.
    Handles files <= 32MB and > 32MB (via upload_url).
    """
    path = Path(filepath)
    if not path.exists():
        return {"error": f"File not found: {filepath}"}

    file_size = path.stat().st_size
    file_size_mb = file_size / (1024 * 1024)
    sha256 = compute_sha256(path)
    filename = path.name
    headers = {"x-apikey": api_key}
    report_url = f"https://www.virustotal.com/gui/file/{sha256}"

    print(f"\n[VT] Processing '{filename}' ({file_size_mb:.1f} MB, SHA256: {sha256})...")

    # Step 1: Check if file was already analyzed on VirusTotal
    try:
        check_resp = requests.get(f"{VT_API_BASE}/files/{sha256}", headers=headers, timeout=20)
        if check_resp.status_code == 200:
            data = check_resp.json().get("data", {})
            stats = data.get("attributes", {}).get("last_analysis_stats", {})
            malicious = stats.get("malicious", 0)
            total = sum(stats.values()) if stats else 0
            print(f"[VT] File already analyzed on VirusTotal: {malicious}/{total} detections.")
            return {
                "name": filename,
                "sha256": sha256,
                "size_mb": file_size_mb,
                "url": report_url,
                "stats": stats,
                "malicious": malicious,
                "total": total,
                "status": "analyzed",
            }
        elif check_resp.status_code == 404:
            print("[VT] File not previously seen on VirusTotal. Uploading...")
        else:
            print(f"[VT] Warning: Unexpected response checking hash: {check_resp.status_code} {check_resp.text}")
    except Exception as e:
        print(f"[VT] Warning during hash lookup: {e}")

    # Step 2: Upload file
    try:
        if file_size <= 32 * 1024 * 1024:
            # Direct upload for files <= 32MB
            upload_endpoint = f"{VT_API_BASE}/files"
        else:
            # For files > 32MB, request large file upload URL
            print("[VT] File is > 32 MB. Requesting dedicated upload URL from VirusTotal...")
            url_resp = requests.get(f"{VT_API_BASE}/files/upload_url", headers=headers, timeout=20)
            if url_resp.status_code != 200:
                print(f"[VT] Error getting upload URL: {url_resp.status_code} {url_resp.text}")
                return {
                    "name": filename,
                    "sha256": sha256,
                    "size_mb": file_size_mb,
                    "url": report_url,
                    "status": "error",
                    "error": f"Failed to get upload URL: {url_resp.status_code}",
                }
            upload_endpoint = url_resp.json().get("data")

        print(f"[VT] Uploading '{filename}' to VirusTotal...")
        with open(path, "rb") as f:
            upload_resp = requests.post(
                upload_endpoint,
                headers=headers,
                files={"file": (filename, f)},
                timeout=180,
            )

        if upload_resp.status_code in (200, 201):
            analysis_id = upload_resp.json().get("data", {}).get("id")
            print(f"[VT] Upload successful! Analysis queued (ID: {analysis_id}).")
            return {
                "name": filename,
                "sha256": sha256,
                "size_mb": file_size_mb,
                "url": report_url,
                "analysis_id": analysis_id,
                "status": "queued",
            }
        else:
            print(f"[VT] Error uploading file: {upload_resp.status_code} {upload_resp.text}")
            return {
                "name": filename,
                "sha256": sha256,
                "size_mb": file_size_mb,
                "url": report_url,
                "status": "submitted",
            }
    except Exception as e:
        print(f"[VT] Error uploading '{filename}': {e}")
        return {
            "name": filename,
            "sha256": sha256,
            "size_mb": file_size_mb,
            "url": report_url,
            "status": "error",
            "error": str(e),
        }


def format_markdown_report(results: list[dict]) -> str:
    """Formats the VirusTotal scan results into a clean markdown section."""
    lines = [
        "---",
        "",
        "### 🛡️ VirusTotal Clean Security Verification",
        "Official automated antivirus scans performed during the GitHub Actions release build:",
        "",
    ]

    for item in results:
        name = item.get("name", "Unknown")
        sha256 = item.get("sha256", "")
        url = item.get("url", "")
        status = item.get("status", "")
        stats = item.get("stats")

        if stats and "malicious" in stats:
            malicious = item["malicious"]
            total = item["total"]
            verdict = f"**{malicious}/{total} Clean**" if malicious == 0 else f"**{malicious}/{total} Flags**"
            status_desc = f"{verdict} • [View VirusTotal Report]({url})"
        elif status == "queued":
            status_desc = f"Queued / Analyzing • [View VirusTotal Report]({url})"
        else:
            status_desc = f"[View VirusTotal Report]({url})"

        lines.append(f"- **`{name}`** ({item.get('size_mb', 0):.1f} MB):")
        lines.append(f"  {status_desc}")
        lines.append(f"  *SHA256: `{sha256}`*")
        lines.append("")

    return "\n".join(lines)


def append_to_github_release(tag: str, report_markdown: str) -> bool:
    """Appends the VirusTotal security section to an existing GitHub release body."""
    try:
        # Check current release notes
        res = subprocess.run(
            ["gh", "release", "view", tag, "--json", "body", "-q", ".body"],
            capture_output=True,
            text=True,
            check=True,
        )
        current_body = res.stdout.strip()

        # Avoid duplicating if already present
        if "### 🛡️ VirusTotal Clean Security Verification" in current_body:
            # Replace existing section
            idx = current_body.find("### 🛡️ VirusTotal Clean Security Verification")
            base_body = current_body[:idx].rstrip("- \n\r")
            new_body = f"{base_body}\n\n{report_markdown}"
        else:
            new_body = f"{current_body}\n\n{report_markdown}"

        temp_file = Path("vt_release_body.md")
        temp_file.write_text(new_body, encoding="utf-8")

        subprocess.run(
            ["gh", "release", "edit", tag, "--notes-file", str(temp_file)],
            check=True,
        )
        temp_file.unlink(missing_ok=True)
        print(f"[SUCCESS] Successfully updated GitHub Release '{tag}' with VirusTotal verification links!")
        return True
    except Exception as e:
        print(f"[ERROR] Failed to update GitHub Release via 'gh' CLI: {e}", file=sys.stderr)
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="VirusTotal CI Scanner & Release Reporter")
    parser.add_argument("--files", nargs="+", required=True, help="File paths or glob patterns to scan")
    parser.add_argument("--tag", default=os.getenv("GITHUB_REF_NAME", ""), help="GitHub release tag to update")
    parser.add_argument("--api-key", default=os.getenv("VIRUSTOTAL_API_KEY", ""), help="VirusTotal API key")

    args = parser.parse_args()

    api_key = args.api_key.strip()
    if not api_key:
        print("[WARNING] VIRUSTOTAL_API_KEY not provided. Skipping VirusTotal scan.", file=sys.stderr)
        sys.exit(0)

    # Resolve glob patterns
    matched_files: list[Path] = []
    for pattern in args.files:
        for match in glob.glob(pattern):
            p = Path(match)
            if p.is_file() and p not in matched_files:
                matched_files.append(p)

    if not matched_files:
        print("[WARNING] No files matched for VirusTotal scanning.", file=sys.stderr)
        sys.exit(0)

    print(f"[VT] Found {len(matched_files)} files to verify on VirusTotal.")
    results = []

    for idx, filepath in enumerate(matched_files):
        # Rate limit protection: VirusTotal free tier allows 4 requests per minute.
        if idx > 0:
            print("[VT] Waiting 16s between file submissions to respect VirusTotal free API rate limit...")
            time.sleep(16)

        res = scan_file_virustotal(filepath, api_key)
        results.append(res)

    # Format Markdown
    report_md = format_markdown_report(results)
    print("\n--- Generated Markdown ---")
    try:
        print(report_md)
    except Exception:
        try:
            sys.stdout.buffer.write((report_md + "\n").encode("utf-8", errors="replace"))
        except Exception:
            pass

    # Update release if tag is specified
    if args.tag:
        append_to_github_release(args.tag, report_md)


if __name__ == "__main__":
    main()
