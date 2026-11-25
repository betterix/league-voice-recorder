# Security Policy

The **League Voice Recorder** team takes security and player safety seriously. This document outlines our security policy, supported versions, anti-cheat & Vanguard compliance, and the process for responsibly reporting vulnerabilities.

---

## Supported Versions

Only the current native Rust release (`0.6.x`) is maintained. All previous legacy Python releases are permanently deprecated and unsupported.

| Version | Supported | Status |
| :--- | :---: | :--- |
| `0.6.x` | :white_check_mark: | **Current Active Release (Only Maintained Version)** |
| `< 0.6.2` | :x: | **End of Life (Legacy, Unsupported & No Maintenance)** |

---

## 🛡️ Anti-Cheat & Riot Vanguard Compliance

League Voice Recorder is built from the ground up to be **100% Riot Vanguard safe and compliant with Riot Games Developer Terms of Service**:

1. **Zero Memory Injection or Inspection**:
   - The application **NEVER** inspects, reads, hooks, or injects code into `League of Legends.exe` process memory.
   - It **NEVER** interacts with or bypasses the Riot Vanguard anti-cheat kernel driver (`vgk.sys`).

2. **Official Local APIs Only**:
   - Match state detection and replay synchronization communicate strictly with official localhost endpoints:
     - `https://127.0.0.1:2999/liveclientdata/allgamedata` (Official Live Client Data API)
     - `https://127.0.0.1:2999/replay/playback` (Official Replay API)
   - All localhost TLS connections are verified against the official pinned Riot Games Root Certificate Authority (`riotgames.pem`). Insecure certificate bypasses (`danger_accept_invalid_certs`) are eliminated.

3. **Pure Windows WASAPI Audio Capture**:
   - Voice and microphone capture rely exclusively on standard Windows Audio Session API (WASAPI) loopback and input endpoints.
   - The application does not install, package, or utilize virtual audio drivers or kernel-level audio filters.

4. **Secret Storage & DPAPI Encryption**:
   - Remote scrim server API tokens are cryptographically secured at rest on Windows using native DPAPI (`CryptProtectData` / `CryptUnprotectData`).
   - Plaintext credentials and secrets are never committed to version control or written unencrypted into `config.json`.

---

## Reporting a Vulnerability

> [!IMPORTANT]
> **Please do not report security vulnerabilities through public GitHub issues, pull requests, or community discussions.**

We have enabled **GitHub Private Vulnerability Reporting** for this repository. This allows you to disclose security vulnerabilities privately and securely directly to the maintainers:

### How to Submit a Private Vulnerability Report:

1. Navigate to the **[Security Advisories](https://github.com/betterix/league-voice-recorder/security/advisories/new)** tab of this repository.
2. Click the green **"Report a vulnerability"** button.
3. Fill in the advisory form with as much detail as possible:
   - **Summary**: A concise overview of the vulnerability.
   - **Severity**: Your estimate of the severity (CVSS score if known).
   - **Description**: Detailed description of the vulnerability and attack vector.
   - **Proof of Concept (PoC)**: Step-by-step instructions or minimal code/script to reproduce the issue.
   - **Impact**: What an attacker could achieve if the vulnerability is exploited.
   - **Suggested Mitigation**: (Optional) Recommended patches or code changes if you have identified a solution.
4. Submit the report. A private discussion channel will be opened between you and the project maintainers.

---

## Response & Disclosure Process

Once a vulnerability report is submitted:

1. **Acknowledgment**: We aim to acknowledge receipt of your report within **48 hours**.
2. **Investigation & Triage**: We will investigate and verify the report, determine its severity, and keep you updated on progress within **5 business days**.
3. **Patch Development**: If verified, we will develop and test a fix in a private temporary fork provided by GitHub Advisories.
4. **Coordinated Disclosure**: 
   - A patched release will be published.
   - A public GitHub Security Advisory (GHSA / CVE) will be published crediting your contribution (unless you prefer to remain anonymous).
   - We kindly ask that you refrain from publicly discussing or disclosing the vulnerability until the patch has been released.

---

## Security Best Practices for Users

- **Keep Your Installation Updated**: Always run the latest release to ensure you have the latest security patches and dependency updates.
- **Run Official Binaries**: Only download releases directly from our [official GitHub Releases page](https://github.com/betterix/league-voice-recorder/releases).
- **Credentials & API Keys**: Scrim server API keys and Discord credentials should never be shared publicly or published in screenshot/video streams.
