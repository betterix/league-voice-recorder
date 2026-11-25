#!/usr/bin/env python3
"""
scripts/bump_version.py — Unified Version Bumper for League Voice Recorder

Usage:
    python scripts/bump_version.py 0.6.1
    python scripts/bump_version.py --patch
    python scripts/bump_version.py --minor
    python scripts/bump_version.py --major
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CARGO_TOML = ROOT / "Cargo.toml"
INSTALLER_NSI = ROOT / "installer.nsi"


def get_current_version() -> str:
    content = CARGO_TOML.read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', content, re.MULTILINE)
    if not m:
        raise ValueError("Could not find version in Cargo.toml")
    return m.group(1)


def parse_semver(v: str) -> tuple[int, int, int]:
    clean = v.lstrip("vV").strip()
    parts = clean.split(".")
    if len(parts) != 3:
        raise ValueError(f"Version must follow semantic versioning (X.Y.Z), got: {v}")
    return int(parts[0]), int(parts[1]), int(parts[2])


def bump_version_str(current: str, part: str) -> str:
    major, minor, patch = parse_semver(current)
    if part == "--major":
        return f"{major + 1}.0.0"
    elif part == "--minor":
        return f"{major}.{minor + 1}.0"
    elif part == "--patch":
        return f"{major}.{minor}.{patch + 1}"
    else:
        # Explicit version string provided
        parse_semver(part)  # validate
        return part.lstrip("vV").strip()


def update_cargo_toml(new_ver: str):
    content = CARGO_TOML.read_text(encoding="utf-8")
    updated = re.sub(r'^(version\s*=\s*)"[^"]+"', rf'\g<1>"{new_ver}"', content, flags=re.MULTILINE)
    CARGO_TOML.write_text(updated, encoding="utf-8")
    print(f"  ✓ Cargo.toml -> {new_ver}")


def update_installer_nsi(new_ver: str):
    content = INSTALLER_NSI.read_text(encoding="utf-8")
    updated = re.sub(r'(!define\s+APP_VERSION\s+)"[^"]+"', rf'\g<1>"{new_ver}"', content)
    INSTALLER_NSI.write_text(updated, encoding="utf-8")
    print(f"  ✓ installer.nsi -> {new_ver}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/bump_version.py <X.Y.Z | --patch | --minor | --major>")
        current = get_current_version()
        print(f"Current version: {current}")
        sys.exit(1)

    arg = sys.argv[1].strip()
    current_ver = get_current_version()
    new_ver = bump_version_str(current_ver, arg)

    print(f"🚀 Bumping League Voice Recorder version: {current_ver} -> {new_ver}\n")
    update_cargo_toml(new_ver)
    update_installer_nsi(new_ver)

    print(f"\n✅ All unified version definitions updated to v{new_ver}!")
    print(f"\nNext steps to commit and tag:")
    print(f"  git commit -am \"chore(release): bump version to {new_ver}\"")
    print(f"  git tag v{new_ver}")


if __name__ == "__main__":
    main()
