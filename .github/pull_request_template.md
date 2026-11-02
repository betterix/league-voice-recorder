## 📋 Description

A clear and concise description of the changes introduced in this pull request.

- What problem does this address, or what feature does this add?
- Why is this approach taken?

Fixes #(issue number) <!-- or Relates to #(issue number) -->

---

## 🏷️ Type of Change

- [ ] 🐛 **Bug fix** (non-breaking change fixing an issue or audio sync drift)
- [ ] ✨ **New feature** (non-breaking change adding new functionality or scrim tooling)
- [ ] 🌐 **Translation / i18n** (new language file or string update in `locales/`)
- [ ] ⚡ **Performance optimization** (GradeX benchmark improvements, reduced CPU/latency)
- [ ] 📝 **Documentation** (updates to README, AGENTS.md, or user guides)
- [ ] 🧪 **Tests / CI** (new unit tests or GitHub Actions workflow enhancements)

---

## 🏛️ Architectural Verification

Please check the applicable items:

- [ ] **GUI Thread Safety**: UI updates are dispatched via `self.msg_queue` and not called directly from background worker threads.
- [ ] **CLI Executability**: Standalone execution (`python -m app.record` and `python -m app.play_replay`) remains fully functional.
- [ ] **Recording Safety**: Audio session termination properly finalizes via `stop_recording_and_rename()`.
- [ ] **Riot & Vanguard Compliance**: No DLL injection or memory hooks; uses only sanctioned local HTTP endpoints (`:2999`) and Windows WASAPI.

---

## 🧪 Testing Checklist

- [ ] I have run the unit test suite and all tests passed:
  ```powershell
  uv run pytest
  ```
- [ ] I have run the Ruff linter and verified 0 warnings/errors:
  ```powershell
  uvx ruff check .
  ```
- [ ] *(If UI change)* Manually tested the desktop GUI (`main.py` / `scripts/start_ui.bat`).
- [ ] *(If audio/replay change)* Verified with sample `.rofl` replay or live client recording.

---

## 📸 Screenshots / Video (Optional)

If this PR introduces visual UI changes or audio diagnostics, please include screenshots or GIFs below.
