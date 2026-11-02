# Contributing to League Voice Recorder

Thank you for your interest in contributing to **League Voice Recorder & Replay Synchronizer**! 🚀

This project is built to empower competitive League of Legends teams, coaches, and players with automated scrim voice recording and frame-accurate replay synchronization. We welcome contributions from developers, translators, designers, and esports enthusiasts of all skill levels.

---

## 📜 Code of Conduct

By participating in this project, you agree to abide by our [Code of Conduct](CODE_OF_CONDUCT.md). Please report any unacceptable behavior to **szabozsolt430@gmail.com**.

---

## 🗺️ How Can You Contribute?

### 1. 🌐 Contributing Translations (i18n)
You don't need to know Python to contribute! Anyone can add a new language translation:
1. Navigate to the [`locales/`](locales/) directory.
2. Copy [`locales/en.json`](locales/en.json) to a new file named with your ISO language code (e.g., `locales/de.json`, `locales/fr.json`, `locales/es.json`).
3. Set the `"__language_name__"` property to your language's native name (e.g., `"Deutsch"`, `"Français"`, `"Español"`).
4. Translate the string values while keeping the `{placeholder}` variables intact.
5. Submit a Pull Request. The application dynamically scans the `locales/` directory at startup and will automatically list your language in the Settings dropdown!

### 2. 🐛 Reporting Bugs
Before creating an issue, please:
- Check existing [GitHub Issues](https://github.com/betterix/league-voice-recorder/issues) to ensure it hasn't already been reported.
- Verify that your prerequisites are installed:
  - Windows 10 or 11 (64-bit)
  - VLC Media Player 64-bit
  - FFmpeg in system PATH
  - `EnableReplayApi=1` in League's `Config/game.cfg`
- Use our [Bug Report Form](https://github.com/betterix/league-voice-recorder/issues/new?template=bug_report.yml) and include relevant logs from the in-app Activity Log tab.

### 3. 💡 Suggesting Enhancements
Have an idea to improve scrimmage reviews, hotkeys, or performance?
- Submit a [Feature Request Form](https://github.com/betterix/league-voice-recorder/issues/new?template=feature_request.yml).
- Describe the problem it solves and how you envision the feature working during scrims or VOD reviews.

### 4. 💻 Code Contributions

#### Development Prerequisites
- **Python**: 3.12, 3.13, or 3.14 (Python 3.14.10 recommended)
- **uv**: Modern, blazing-fast Python package and virtualenv manager ([Install uv](https://docs.astral.sh/uv/getting-started/installation/))
- **Git**: For version control

#### Local Setup Workflow
```powershell
# 1. Fork and clone your repository fork
git clone https://github.com/<your-username>/league-voice-recorder.git
cd league-voice-recorder

# 2. Create the virtual environment and install all dependencies (including dev tools)
uv sync --dev

# 3. Launch the desktop GUI locally
uv run main.py

# Or launch via helper script
.\scripts\start_ui.bat
```

#### Running Tests & Linters
Every Pull Request must pass our automated test suite and linter checks:

```powershell
# Run the automated test suite (70 unit tests)
uv run pytest

# Or run via Windows helper script
.\scripts\run_tests.bat

# Run Ruff linter
uvx ruff check .

# Automatically apply Ruff lint fixes
uvx ruff check --fix .
```

---

## 🏛️ Core Architectural Invariants

When submitting code changes, please keep these design rules in mind:

1. **GUI Thread Safety**: Never call Tkinter / CustomTkinter widget methods directly from background worker threads (audio capture, process watcher, or replay polling). Always dispatch events through `self.msg_queue` and process them on the main GUI thread in `_process_queue`.
2. **CLI Executability**: Both `app/record.py` and `app/play_replay.py` must remain independently runnable via the command line (`python -m app.record` and `python -m app.play_replay`). Standalone CLI support must be preserved.
3. **Recording Safety**: When the recorder engine stops (via the UI toggle or window close event), active audio sessions must always be finalized via `stop_recording_and_rename()` to ensure `.mp3` export completes without data loss.
4. **Fast Process Inspection**: Always prioritize `psutil` cached PID checking over subprocess / PowerShell commands for game client discovery. Background polling loops must stay below 0.2% CPU utilization.
5. **Vanguard & Riot API Compliance**: Never introduce DLL injection, memory modification, or game hook mechanisms. All live game communication must use Riot's sanctioned local HTTP endpoints (:2999) and Windows WASAPI audio APIs.

---

## 🔀 Pull Request Guidelines

1. **Branch Naming**: Use descriptive branch names:
   - `feat/feature-name`
   - `fix/bug-description`
   - `i18n/language-code`
   - `docs/documentation-update`
2. **Commit Messages**: Follow [Conventional Commits](https://www.conventionalcommits.org/):
   - `feat: add push-to-talk hotkey toggle`
   - `fix: resolve mute desync on audio buffer wrap`
   - `docs: update setup prerequisites for Windows 11`
   - `i18n: add French community translation`
3. **Verify Tests**: Ensure `uv run pytest` reports 70/70 passing tests and `uvx ruff check .` reports 0 errors before opening your PR.

---

Thank you for helping make scrimmage voice review seamless for the League esports community! 🎮🎙️
