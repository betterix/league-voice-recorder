"""System Tray integration for League Voice Recorder.

Manages background notification area icon, dynamic status tooltip,
context menu (Open / Status / Exit), and single/double-click window restoration.
"""

import os
import threading
from typing import Optional
from collections.abc import Callable

from PIL import Image

from app.i18n import t

try:
    import pystray
    PYSTRAY_AVAILABLE = True
except Exception:
    pystray = None
    PYSTRAY_AVAILABLE = False


def _find_tray_icon_path() -> str | None:
    """Locates the application icon on disk."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(base_dir, "..", "assets", "icon.ico"),
        os.path.join(base_dir, "assets", "icon.ico"),
        os.path.abspath("assets/icon.ico"),
    ]
    for p in candidates:
        if os.path.exists(p):
            return os.path.abspath(p)
    return None


def _load_tray_image() -> Image.Image:
    """Loads application icon or generates a fallback RGBA bitmap."""
    icon_path = _find_tray_icon_path()
    if icon_path:
        try:
            return Image.open(icon_path)
        except Exception:
            pass

    # Fallback procedural Hextech cyan/gold icon (64x64)
    img = Image.new("RGBA", (64, 64), (10, 200, 185, 255))
    return img


class TrayManager:
    """Controls the background Windows System Tray icon."""

    def __init__(
        self,
        on_restore: Callable[[], None],
        on_exit: Callable[[], None],
        get_status_text: Callable[[], str] | None = None,
    ):
        self.on_restore = on_restore
        self.on_exit = on_exit
        self.get_status_text = get_status_text

        self._icon: pystray.Icon | None = None
        self._lock = threading.Lock()
        self._is_running = False
        self._tooltip = t("tray.tooltip_idle")

    def _menu_open_clicked(self, icon=None, item=None):
        if self.on_restore:
            self.on_restore()

    def _menu_exit_clicked(self, icon=None, item=None):
        if self.on_exit:
            self.on_exit()

    def _dynamic_status(self, item=None) -> str:
        if self.get_status_text:
            try:
                return self.get_status_text()
            except Exception:
                pass
        return t("tray.status_idle")

    def _build_menu(self) -> Optional["pystray.Menu"]:
        if not PYSTRAY_AVAILABLE:
            return None

        return pystray.Menu(
            pystray.MenuItem(t("tray.open"), self._menu_open_clicked, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(self._dynamic_status, None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(t("tray.exit"), self._menu_exit_clicked),
        )

    def start(self) -> bool:
        """Starts the tray icon in a background detached thread."""
        if not PYSTRAY_AVAILABLE:
            return False

        with self._lock:
            if self._is_running and self._icon is not None:
                return True

            try:
                img = _load_tray_image()
                menu = self._build_menu()
                self._icon = pystray.Icon(
                    "league_voice_recorder",
                    img,
                    self._tooltip,
                    menu=menu,
                )
                self._icon.run_detached()
                self._is_running = True
                return True
            except Exception as e:
                print(f"[TrayManager] Failed to start tray icon: {e}")
                self._icon = None
                self._is_running = False
                return False

    def show(self) -> None:
        """Ensures the system tray icon is active and visible."""
        if not self._is_running or self._icon is None:
            self.start()
        elif self._icon is not None:
            try:
                self._icon.visible = True
                self._icon.update_menu()
            except Exception:
                pass

    def hide(self) -> None:
        """Hides the tray icon without destroying the runner."""
        with self._lock:
            if self._icon is not None:
                try:
                    self._icon.visible = False
                except Exception:
                    pass

    def update_tooltip(self, text: str) -> None:
        """Updates the hover tooltip text and refreshes context menu status."""
        self._tooltip = text
        with self._lock:
            if self._icon is not None:
                try:
                    self._icon.title = text
                    self._icon.update_menu()
                except Exception:
                    pass

    def notify(self, message: str, title: str = "League Voice Recorder") -> None:
        """Displays a native Windows toast/balloon notification."""
        with self._lock:
            if self._icon is not None:
                try:
                    self._icon.notify(message, title)
                except Exception:
                    pass

    def stop(self) -> None:
        """Permanently stops and removes the system tray icon."""
        with self._lock:
            if self._icon is not None:
                try:
                    self._icon.stop()
                except Exception:
                    pass
                self._icon = None
            self._is_running = False
