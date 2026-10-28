# Security Policy

The **League Voice Recorder** team takes security and privacy seriously. This document outlines our security policy, supported versions, and the process for responsibly reporting potential vulnerabilities.

---

## Supported Versions

We actively provide security patches, dependency updates, and bug fixes for the following versions:

| Version | Supported | Status |
| :--- | :---: | :--- |
| `0.2.x` | :white_check_mark: | **Current Active Release** |
| `0.1.x` | :x: | End of Life (upgrade recommended) |
| `< 0.1.0` | :x: | Unsupported |

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
- **Discord IPC & Tokens**: The application stores your local Discord IPC token in `config.json` next to the executable. Never share your `config.json` file publicly or commit it to version control.
