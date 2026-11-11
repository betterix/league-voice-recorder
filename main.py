import datetime
import io
import os
import queue
import signal
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk
import psutil

from app import __version__
from app.config import (
    get_current_user_documents_recordings_dir,
    get_ffmpeg_path,
    load_config,
    save_config,
)
from app.native_audio import get_available_audio_targets, get_available_microphones
from app.i18n import (
    get_available_languages,
    get_language,
    on_language_change,
    remove_language_change_listener,
    set_language,
    t,
)
from app.play_replay import ReplaySynchronizer
from app.record import LeagueRecorder
from app.tray import TrayManager

if sys.platform.startswith("win"):
    if sys.stdout is None:
        sys.stdout = io.StringIO()
    if sys.stderr is None:
        sys.stderr = io.StringIO()
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            f"leaguevoicerecorder.app.v{__version__}"
        )
    except Exception:
        pass

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class LeagueVoiceRecorderApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        is_test = bool(os.environ.get("LVR_TEST_MODE") or os.environ.get("PYTEST_CURRENT_TEST"))
        self._in_test_suite = is_test
        if is_test:
            self.withdraw()

        # Load Configuration
        self.config = load_config()

        # Appearance mode (dark / light / system)
        self.current_theme = self.config.get("theme", "dark")
        ctk.set_appearance_mode(self.current_theme)

        # Initialize i18n
        set_language(self.config.get("language", "en"))
        on_language_change(self._update_ui_texts)

        self.title(f"{t('app.window_title')} v{__version__}")
        if not is_test:
            screen_width = self.winfo_screenwidth()
            screen_height = self.winfo_screenheight()
            app_width = min(1100, max(920, screen_width - 80))
            app_height = min(960, max(750, screen_height - 100))
            x = max(0, int((screen_width - app_width) / 2))
            y = max(0, int((screen_height - app_height) / 2))
            self.geometry(f"{app_width}x{app_height}+{x}+{y}")
            self.minsize(920, 750)
            self.configure(fg_color=("#F1F5F9", "#060B18"))

        is_test = bool(os.environ.get("LVR_TEST_MODE") or os.environ.get("PYTEST_CURRENT_TEST"))

        # Set Window & Taskbar Icon
        if not is_test:
            self._apply_window_icon()
            self.after(300, self._apply_window_icon)

        # Threading & Workers
        self.msg_queue = queue.Queue()
        self.recorder_worker = None
        self.recorder_thread = None
        self.replay_worker = None
        self.replay_thread = None

        # State tracking for status and health
        self._is_destroyed = False
        self._queue_timer = None
        self.last_rec_status = ("IDLE", "")
        self.last_replay_status = ("IDLE", "")
        self.health_state = (None, None, None, None)
        self._remote_recordings = []
        self._cloud_syncing = False
        self._cloud_role = "unknown"

        # Build UI
        self._setup_ui()

        self._pending_update = None
        self._last_auto_started_phase = None
        self._last_auto_started_rofl = None

        if not is_test:
            # Periodic queue checker and health check
            self._queue_timer = self.after(50, self._process_queue)
            self.after(500, self._check_system_health)
            self.after(200, self._refresh_audio_targets)
            self.after(200, self._refresh_microphones)
            self._start_audio_meter_loop()

            # Automatic Champion Select & Replay match detection monitor
            self.after(1000, self._start_auto_detect_monitor)

            # Clean leftover *.old files from previous updates and schedule background update check
            try:
                from app.updater import clean_old_executables

                clean_old_executables()
            except Exception:
                pass
            self.after(3000, self._check_update_background)

        # Replay mute state & hotkeys
        self._replay_muted = False
        self.bind("<Key-m>", self._on_key_m)
        self.bind("<Key-M>", self._on_key_m)

        # In-Game Floating Overlay (Translucent overlay for live scrim recording & replays)
        self._is_hud_previewing = False
        try:
            from app.overlay import LeagueOverlay

            is_locked = bool(self.config.get("hud_locked", self.config.get("hud_click_through", False)))
            self.overlay = LeagueOverlay(
                self,
                opacity=float(self.config.get("hud_opacity", 0.85)),
                locked=is_locked,
                click_through=is_locked,
                initial_x=int(self.config.get("hud_x", -1)),
                initial_y=int(self.config.get("hud_y", -1)),
                on_volume_changed=self._on_hud_volume_changed,
                on_position_changed=self._on_hud_position_changed,
                on_lock_changed=self._on_hud_lock_changed_from_overlay,
            )
            self.hud = self.overlay
        except Exception:
            self.overlay = None
            self.hud = None

        # Handle window close
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # System Tray Manager & Window Minimize Handler
        self.tray_manager = TrayManager(
            on_restore=self._restore_from_tray_safe,
            on_exit=self._on_close,
            get_status_text=self._get_tray_status_text,
        )
        self.bind("<Unmap>", self._on_window_unmap)

    def _apply_window_icon(self):
        """Set window title bar and taskbar icon with multi-resolution support."""
        app_dir = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        nuitka_dir = os.environ.get("NUITKA_ONEFILE_DIRECTORY")
        ico_path = None
        for candidate in [
            os.path.join(nuitka_dir, "assets", "icon.ico") if nuitka_dir else "",
            os.path.join(app_dir, "assets", "icon.ico"),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "icon.ico"),
        ]:
            if candidate and os.path.exists(candidate):
                ico_path = candidate
                break

        if ico_path:
            try:
                self.iconbitmap(ico_path)
            except Exception:
                pass
        elif getattr(sys, "frozen", False) or (sys.executable and sys.executable.lower().endswith(".exe")):
            try:
                self.iconbitmap(sys.executable)
            except Exception:
                pass

        if sys.platform.startswith("win"):
            try:
                import ctypes

                self.update_idletasks()
                wid = self.winfo_id()
                hwnd = ctypes.windll.user32.GetParent(wid) or wid

                h_sm = None
                h_lg = None
                if ico_path and os.path.exists(ico_path):
                    IMAGE_ICON = 1
                    LR_LOADFROMFILE = 0x10
                    h_sm = ctypes.windll.user32.LoadImageW(None, ico_path, IMAGE_ICON, 16, 16, LR_LOADFROMFILE)
                    h_lg = ctypes.windll.user32.LoadImageW(None, ico_path, IMAGE_ICON, 32, 32, LR_LOADFROMFILE)
                elif getattr(sys, "frozen", False):
                    h1 = ctypes.c_void_p()
                    h2 = ctypes.c_void_p()
                    if (
                        ctypes.windll.shell32.ExtractIconExW(
                            sys.executable, 0, ctypes.byref(h1), ctypes.byref(h2), 1
                        )
                        > 0
                    ):
                        h_lg = h1.value
                        h_sm = h2.value

                WM_SETICON = 0x80
                if h_sm:
                    ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, 0, h_sm)
                    ctypes.windll.user32.SendMessageW(wid, WM_SETICON, 0, h_sm)
                if h_lg:
                    ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, 1, h_lg)
                    ctypes.windll.user32.SendMessageW(wid, WM_SETICON, 1, h_lg)
            except Exception:
                pass

    def _get_engine_display_text(self):
        return t("record.target_scene", scene="Discord + Mic")

    # ==========================================
    # SYSTEM TRAY & WINDOW MINIMIZE HANDLING
    # ==========================================
    def _on_window_unmap(self, event):
        """Intercepts window minimize to hide to system tray if enabled."""
        if event.widget == self:
            try:
                if self.state() == "iconic" and self.config.get("minimize_to_tray", True):
                    self.withdraw()
                    if hasattr(self, "tray_manager") and self.tray_manager:
                        self.tray_manager.show()
                        self._update_tray_status()
            except Exception:
                pass

    def _restore_from_tray_safe(self):
        """Thread-safe trigger from tray menu/click to restore UI."""
        try:
            self.after(0, self._restore_from_tray)
        except Exception:
            pass

    def _restore_from_tray(self):
        """Restores and brings main window to front."""
        try:
            self.deiconify()
            self.state("normal")
            self.lift()
            self.focus_force()
        except Exception:
            pass

    def _get_tray_status_text(self) -> str:
        """Returns dynamic live status string for the tray context menu."""
        if hasattr(self, "recorder_worker") and self.recorder_worker and getattr(self.recorder_worker, "recording", False):
            return t("tray.status_recording")
        elif hasattr(self, "replay_worker") and self.replay_worker and getattr(self.replay_worker, "syncing", False):
            return t("tray.status_syncing")
        return t("tray.status_idle")

    def _update_tray_status(self):
        """Updates tray tooltip to match current active state."""
        if not hasattr(self, "tray_manager") or not self.tray_manager:
            return
        if hasattr(self, "recorder_worker") and self.recorder_worker and getattr(self.recorder_worker, "recording", False):
            tooltip = t("tray.tooltip_recording")
        elif hasattr(self, "replay_worker") and self.replay_worker and getattr(self.replay_worker, "syncing", False):
            tooltip = t("tray.tooltip_syncing")
        else:
            tooltip = t("tray.tooltip_idle")
        self.tray_manager.update_tooltip(tooltip)

    def _setup_ui(self):
        # Configure Grid Layout
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)  # Tabview expands

        # ==========================================
        # 1. TOP HEADER & SYSTEM HEALTH BAR
        # ==========================================
        header_frame = ctk.CTkFrame(
            self,
            fg_color=("#FFFFFF", "#0B1329"),
            corner_radius=12,
            border_width=1,
            border_color=("#E2E8F0", "#1E2A4A"),
        )
        header_frame.grid(row=0, column=0, padx=15, pady=(10, 6), sticky="ew")
        header_frame.grid_columnconfigure(0, weight=1)
        header_frame.grid_columnconfigure(1, weight=0)

        title_box = ctk.CTkFrame(header_frame, fg_color="transparent")
        title_box.grid(row=0, column=0, padx=14, pady=8, sticky="w")

        self.title_lbl = ctk.CTkLabel(
            title_box,
            text=t("app.title"),
            font=ctk.CTkFont(family="Segoe UI", size=18, weight="bold"),
            text_color=("#A07832", "#C8AA6E"),
        )
        self.title_lbl.pack(anchor="w")

        self.sub_lbl = ctk.CTkLabel(
            title_box,
            text=t("app.subtitle"),
            font=ctk.CTkFont(size=11),
            text_color=("#64748B", "#94A3B8"),
        )
        self.sub_lbl.pack(anchor="w")

        # Health Badges Frame
        health_frame = ctk.CTkFrame(
            header_frame,
            fg_color=("#F8FAFC", "#070D1E"),
            corner_radius=8,
            border_width=1,
            border_color=("#E2E8F0", "#162038"),
        )
        health_frame.grid(row=0, column=1, padx=12, pady=8, sticky="e")

        self.discord_badge = ctk.CTkLabel(
            health_frame,
            text=f"{t('health.discord')} {t('health.checking')}",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=("#64748B", "#CBD5E1"),
        )
        self.discord_badge.pack(side="left", padx=(10, 6), pady=6)

        self.lol_badge = ctk.CTkLabel(
            health_frame,
            text=f"{t('health.league')} {t('health.checking')}",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=("#64748B", "#CBD5E1"),
        )
        self.lol_badge.pack(side="left", padx=6, pady=6)

        self.vlc_badge = ctk.CTkLabel(
            health_frame,
            text=f"{t('health.vlc')} {t('health.checking')}",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=("#64748B", "#CBD5E1"),
        )
        self.vlc_badge.pack(side="left", padx=6, pady=6)

        self.ffmpeg_badge = ctk.CTkLabel(
            health_frame,
            text=f"{t('health.ffmpeg')} {t('health.checking')}",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=("#64748B", "#CBD5E1"),
        )
        self.ffmpeg_badge.pack(side="left", padx=6, pady=6)

        self.theme_toggle_btn = ctk.CTkButton(
            health_frame,
            text="☀️" if self.current_theme == "light" else "🌙",
            width=32,
            height=26,
            font=ctk.CTkFont(size=13),
            text_color=("#D97706", "#FBBF24"),
            fg_color=("#F1F5F9", "#0F1A36"),
            hover_color=("#E2E8F0", "#1E2A4A"),
            border_width=1,
            border_color=("#CBD5E1", "#1E2A4A"),
            corner_radius=6,
            command=self._toggle_theme,
        )
        self.theme_toggle_btn.pack(side="right", padx=(4, 8), pady=4)

        self.refresh_health_btn = ctk.CTkButton(
            health_frame,
            text=t("health.refresh"),
            width=75,
            height=26,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=("#F1F5F9", "#0F1A36"),
            hover_color=("#E2E8F0", "#1E2A4A"),
            border_width=1,
            border_color=("#CBD5E1", "#1E2A4A"),
            text_color=("#0F172A", "#E2E8F0"),
            corner_radius=6,
            command=self._check_system_health,
        )
        self.refresh_health_btn.pack(side="right", padx=(4, 4), pady=4)

        self.update_header_badge = ctk.CTkButton(
            health_frame,
            text="",
            height=26,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color="#059669",
            hover_color="#047857",
            border_width=1,
            border_color="#10B981",
            corner_radius=6,
            command=self._on_header_update_badge_clicked,
        )

        # ==========================================
        # 2. MAIN ACTION BUTTONS SECTION (2 CARDS)
        # ==========================================
        action_frame = ctk.CTkFrame(self, fg_color="transparent")
        action_frame.grid(row=1, column=0, padx=15, pady=(2, 6), sticky="ew")
        action_frame.grid_columnconfigure(0, weight=1)
        action_frame.grid_columnconfigure(1, weight=1)

        # --- CARD 1: RECORD ---
        rec_card = ctk.CTkFrame(
            action_frame,
            fg_color=("#FFFFFF", "#0B1329"),
            corner_radius=12,
            border_width=1,
            border_color=("#E2E8F0", "#1E2A4A"),
        )
        rec_card.grid(row=0, column=0, padx=(0, 6), pady=0, sticky="nsew")

        rec_top_row = ctk.CTkFrame(rec_card, fg_color="transparent")
        rec_top_row.pack(fill="x", padx=14, pady=(10, 6))

        self.rec_header_lbl = ctk.CTkLabel(
            rec_top_row,
            text=t("record.title"),
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=("#E11D48", "#F43F5E"),
        )
        self.rec_header_lbl.pack(side="left")

        self.rec_status_pill = ctk.CTkLabel(
            rec_top_row,
            text=t("record.status_idle"),
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=("#64748B", "#94A3B8"),
            fg_color=("#F8FAFC", "#070D1E"),
            corner_radius=6,
            padx=10,
            pady=4,
        )
        self.rec_status_pill.pack(side="right")

        self.rec_btn = ctk.CTkButton(
            rec_card,
            text=t("record.start_btn"),
            font=ctk.CTkFont(size=13, weight="bold"),
            height=42,
            fg_color="#E11D48",
            hover_color="#BE123C",
            border_width=1,
            border_color=("#BE123C", "#F43F5E"),
            corner_radius=8,
            command=self._toggle_recording,
        )
        self.rec_btn.pack(fill="x", padx=14, pady=(0, 6))

        # Selectors side-by-side row (Target program + Microphone)
        selectors_frame = ctk.CTkFrame(rec_card, fg_color="transparent")
        selectors_frame.pack(fill="x", padx=14, pady=(0, 6))
        selectors_frame.grid_columnconfigure(0, weight=1)
        selectors_frame.grid_columnconfigure(1, weight=1)

        prog_frame = ctk.CTkFrame(
            selectors_frame,
            fg_color=("#F8FAFC", "#070D1E"),
            corner_radius=6,
            border_width=1,
            border_color=("#E2E8F0", "#162038"),
        )
        prog_frame.grid(row=0, column=0, padx=(0, 4), sticky="nsew")
        prog_frame.grid_columnconfigure(0, weight=1)
        prog_frame.grid_columnconfigure(1, weight=0)

        self.target_prog_lbl = ctk.CTkLabel(
            prog_frame,
            text=t("record.target_app"),
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=("#0891B2", "#0AC8B9"),
        )
        self.target_prog_lbl.grid(row=0, column=0, columnspan=2, padx=8, pady=(4, 1), sticky="w")

        self.available_targets = []
        self.target_prog_menu = ctk.CTkOptionMenu(
            prog_frame,
            values=["Discord (Auto Detect Active Audio) 🎯"],
            command=self._on_target_prog_selected,
            height=28,
            fg_color=("#FFFFFF", "#0B1329"),
            text_color=("#0F172A", "#F8FAFC"),
            button_color=("#0891B2", "#0AC8B9"),
            button_hover_color=("#0E7490", "#0397AB"),
            dropdown_fg_color=("#FFFFFF", "#0B1329"),
            dropdown_text_color=("#0F172A", "#F8FAFC"),
            dropdown_hover_color=("#F1F5F9", "#162038"),
            corner_radius=6,
        )
        self.target_prog_menu.grid(row=1, column=0, padx=(6, 3), pady=(0, 6), sticky="ew")

        self.refresh_progs_btn = ctk.CTkButton(
            prog_frame,
            text="🔄",
            width=28,
            height=28,
            font=ctk.CTkFont(size=12),
            text_color=("#0F172A", "#E2E8F0"),
            fg_color=("#F1F5F9", "#0F1A36"),
            hover_color=("#E2E8F0", "#1E2A4A"),
            border_width=1,
            border_color=("#CBD5E1", "#1E2A4A"),
            corner_radius=6,
            command=self._refresh_audio_targets,
        )
        self.refresh_progs_btn.grid(row=1, column=1, padx=(0, 6), pady=(0, 6))

        mic_frame = ctk.CTkFrame(
            selectors_frame,
            fg_color=("#F8FAFC", "#070D1E"),
            corner_radius=6,
            border_width=1,
            border_color=("#E2E8F0", "#162038"),
        )
        mic_frame.grid(row=0, column=1, padx=(4, 0), sticky="nsew")
        mic_frame.grid_columnconfigure(0, weight=1)
        mic_frame.grid_columnconfigure(1, weight=0)

        self.mic_input_lbl = ctk.CTkLabel(
            mic_frame,
            text=t("record.mic_input"),
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=("#0891B2", "#0AC8B9"),
        )
        self.mic_input_lbl.grid(row=0, column=0, columnspan=2, padx=8, pady=(4, 1), sticky="w")

        self.available_mics = []
        self.mic_input_menu = ctk.CTkOptionMenu(
            mic_frame,
            values=[t("record.mic_default")],
            command=self._on_mic_input_selected,
            height=28,
            fg_color=("#FFFFFF", "#0B1329"),
            text_color=("#0F172A", "#F8FAFC"),
            button_color=("#0891B2", "#0AC8B9"),
            button_hover_color=("#0E7490", "#0397AB"),
            dropdown_fg_color=("#FFFFFF", "#0B1329"),
            dropdown_text_color=("#0F172A", "#F8FAFC"),
            dropdown_hover_color=("#F1F5F9", "#162038"),
            corner_radius=6,
        )
        self.mic_input_menu.grid(row=1, column=0, padx=(6, 3), pady=(0, 6), sticky="ew")

        self.refresh_mics_btn = ctk.CTkButton(
            mic_frame,
            text="🔄",
            width=28,
            height=28,
            font=ctk.CTkFont(size=12),
            text_color=("#0F172A", "#E2E8F0"),
            fg_color=("#F1F5F9", "#0F1A36"),
            hover_color=("#E2E8F0", "#1E2A4A"),
            border_width=1,
            border_color=("#CBD5E1", "#1E2A4A"),
            corner_radius=6,
            command=self._refresh_microphones,
        )
        self.refresh_mics_btn.grid(row=1, column=1, padx=(0, 6), pady=(0, 6))

        # Live Audio VU Meter Frame (Hangcsik)
        meter_frame = ctk.CTkFrame(
            rec_card,
            fg_color=("#F8FAFC", "#070D1E"),
            corner_radius=6,
            border_width=1,
            border_color=("#E2E8F0", "#162038"),
        )
        meter_frame.pack(fill="x", padx=14, pady=(0, 6))
        meter_frame.grid_columnconfigure(1, weight=1)

        # App Audio Meter
        self.app_meter_title = ctk.CTkLabel(
            meter_frame,
            text="🔊 " + t("record.app_sound") + ":",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=("#64748B", "#94A3B8"),
        )
        self.app_meter_title.grid(row=0, column=0, padx=(10, 6), pady=(4, 2), sticky="w")

        self.app_meter_bar = ctk.CTkProgressBar(
            meter_frame,
            height=6,
            corner_radius=3,
            fg_color=("#E2E8F0", "#0B1329"),
            progress_color="#10B981",
        )
        self.app_meter_bar.set(0.0)
        self.app_meter_bar.grid(row=0, column=1, padx=(0, 10), pady=(4, 2), sticky="ew")

        # Mic Meter
        self.mic_meter_title = ctk.CTkLabel(
            meter_frame,
            text="🎤 " + t("record.microphone") + ":",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=("#64748B", "#94A3B8"),
        )
        self.mic_meter_title.grid(row=1, column=0, padx=(10, 6), pady=(2, 6), sticky="w")

        self.mic_meter_bar = ctk.CTkProgressBar(
            meter_frame,
            height=6,
            corner_radius=3,
            fg_color=("#E2E8F0", "#0B1329"),
            progress_color=("#0891B2", "#0AC8B9"),
        )
        self.mic_meter_bar.set(0.0)
        self.mic_meter_bar.grid(row=1, column=1, padx=(0, 10), pady=(2, 6), sticky="ew")

        rec_footer_frame = ctk.CTkFrame(rec_card, fg_color="transparent")
        rec_footer_frame.pack(fill="x", padx=14, pady=(0, 8))

        self.rec_scene_lbl = ctk.CTkLabel(
            rec_footer_frame,
            text=self._get_engine_display_text(),
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=("#E11D48", "#F43F5E"),
        )
        self.rec_scene_lbl.pack(side="left")

        self.rec_info_lbl = ctk.CTkLabel(
            rec_footer_frame,
            text=t("record.info"),
            font=ctk.CTkFont(size=10),
            text_color="#64748B",
            justify="right",
        )
        self.rec_info_lbl.pack(side="right")

        # --- CARD 2: PLAY REPLAY ---
        rep_card = ctk.CTkFrame(
            action_frame,
            fg_color=("#FFFFFF", "#0B1329"),
            corner_radius=12,
            border_width=1,
            border_color=("#E2E8F0", "#1E2A4A"),
        )
        rep_card.grid(row=0, column=1, padx=(6, 0), pady=0, sticky="nsew")

        rep_top_row = ctk.CTkFrame(rep_card, fg_color="transparent")
        rep_top_row.pack(fill="x", padx=14, pady=(10, 6))

        self.rep_header_lbl = ctk.CTkLabel(
            rep_top_row,
            text=t("replay.title"),
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=("#0891B2", "#0AC8B9"),
        )
        self.rep_header_lbl.pack(side="left")

        self.rep_status_pill = ctk.CTkLabel(
            rep_top_row,
            text=t("replay.status_idle"),
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=("#64748B", "#94A3B8"),
            fg_color=("#F8FAFC", "#070D1E"),
            corner_radius=6,
            padx=10,
            pady=4,
        )
        self.rep_status_pill.pack(side="right")

        self.rep_btn = ctk.CTkButton(
            rep_card,
            text=t("replay.start_btn"),
            font=ctk.CTkFont(size=13, weight="bold"),
            height=42,
            fg_color="#0284C7",
            hover_color="#0369A1",
            border_width=1,
            border_color=("#0284C7", "#38BDF8"),
            corner_radius=8,
            command=self._toggle_replay,
        )
        self.rep_btn.pack(fill="x", padx=14, pady=(0, 6))

        # Replay Volume Slider Control
        vol_frame = ctk.CTkFrame(
            rep_card,
            fg_color=("#F8FAFC", "#070D1E"),
            corner_radius=6,
            border_width=1,
            border_color=("#E2E8F0", "#162038"),
        )
        vol_frame.pack(fill="x", padx=14, pady=(0, 6))
        vol_frame.grid_columnconfigure(1, weight=1)

        self.current_vol = int(self.config.get("replay_volume", 80))
        vol_icon = (
            "🔊"
            if self.current_vol >= 70
            else ("🔉" if self.current_vol >= 35 else ("🔈" if self.current_vol > 0 else "🔇"))
        )
        self.vol_label = ctk.CTkLabel(
            vol_frame,
            text=f"{vol_icon} {self.current_vol}%",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=("#0891B2", "#0AC8B9"),
            width=75,
            cursor="hand2",
        )
        self.vol_label.bind("<Button-1>", lambda e: self._toggle_replay_mute())
        self.vol_label.grid(row=0, column=0, padx=(10, 4), pady=6, sticky="w")

        self.vol_slider = ctk.CTkSlider(
            vol_frame,
            from_=0,
            to=200,
            number_of_steps=200,
            command=self._on_replay_volume_slider_changed,
            progress_color=("#0891B2", "#0AC8B9"),
            button_color=("#06B6D4", "#5EEAD4"),
            button_hover_color=("#0891B2", "#0AC8B9"),
            fg_color=("#E2E8F0", "#0B1329"),
            height=14,
        )
        self.vol_slider.set(self.current_vol)
        self.vol_slider.grid(row=0, column=1, padx=(4, 10), pady=6, sticky="ew")

        # 1-Click Replay API Auto-Configuration Row (Under Replay Controls)
        self.replay_api_row = ctk.CTkFrame(
            rep_card,
            fg_color=("#F8FAFC", "#070D1E"),
            corner_radius=6,
            border_width=1,
            border_color=("#E2E8F0", "#162038"),
        )
        self.replay_api_row.pack(fill="x", padx=14, pady=(0, 6))
        self.replay_api_row.grid_columnconfigure(0, weight=1)
        self.replay_api_row.grid_columnconfigure(1, weight=0)

        self.replay_api_badge = ctk.CTkLabel(
            self.replay_api_row,
            text=t("settings.replay_api_status_not_found"),
            font=ctk.CTkFont(size=10, weight="bold"),
            corner_radius=6,
            padx=8,
            pady=3,
        )
        self.replay_api_badge.grid(row=0, column=0, padx=(8, 4), pady=5, sticky="w")

        self.replay_api_btn = ctk.CTkButton(
            self.replay_api_row,
            text=t("settings.enable_replay_api_btn"),
            font=ctk.CTkFont(size=11, weight="bold"),
            height=26,
            fg_color=("#0284C7", "#0284C7"),
            hover_color=("#0369A1", "#0369A1"),
            border_width=1,
            border_color=("#38BDF8", "#0284C7"),
            corner_radius=6,
            command=self._on_enable_replay_api_clicked,
        )
        self.replay_api_btn.grid(row=0, column=1, padx=(4, 8), pady=5, sticky="e")
        self._refresh_replay_api_ui_status()

        rep_info_frame = ctk.CTkFrame(
            rep_card,
            fg_color=("#F8FAFC", "#070D1E"),
            corner_radius=6,
            border_width=1,
            border_color=("#E2E8F0", "#162038"),
        )
        rep_info_frame.pack(fill="both", expand=True, padx=14, pady=(0, 8))

        self.rep_info_lbl = ctk.CTkLabel(
            rep_info_frame,
            text=f"{t('replay.info')}\n• {t('replay.hotkey_hint')}",
            font=ctk.CTkFont(size=10),
            text_color="#64748B",
            justify="left",
        )
        self.rep_info_lbl.pack(padx=10, pady=6, anchor="w")

        # ==========================================
        # 3. TABBED VIEW (ACTIVITY LOG, RECORDINGS, SETTINGS)
        # ==========================================
        self.tabview = ctk.CTkTabview(
            self,
            fg_color=("#FFFFFF", "#0B1329"),
            border_width=1,
            border_color=("#E2E8F0", "#1E2A4A"),
            segmented_button_fg_color=("#F1F5F9", "#070D1E"),
            segmented_button_selected_color=("#0891B2", "#0AC8B9"),
            segmented_button_selected_hover_color=("#0E7490", "#0397AB"),
            segmented_button_unselected_hover_color=("#E2E8F0", "#0F1A36"),
            corner_radius=14,
        )
        self.tabview.grid(row=2, column=0, padx=15, pady=(4, 15), sticky="nsew")

        tab_log = self.tabview.add("log")
        tab_recordings = self.tabview.add("recordings")
        tab_settings = self.tabview.add("settings")

        self.tabview._segmented_button.configure(text_color=("#0F172A", "#F8FAFC"))
        self.tabview._segmented_button._buttons_dict["log"].configure(text=t("tabs.log"))
        self.tabview._segmented_button._buttons_dict["recordings"].configure(
            text=t("tabs.recordings")
        )
        self.tabview._segmented_button._buttons_dict["settings"].configure(text=t("tabs.settings"))

        self._setup_tab_log(tab_log)
        self._setup_tab_recordings(tab_recordings)
        self._setup_tab_settings(tab_settings)

    # -------------------------------------------------------------
    # TAB 1: ACTIVITY LOG
    # -------------------------------------------------------------
        # ==========================================
    # TARGET AUDIO PROGRAM & LIVE VU METER
    # ==========================================
    def _refresh_audio_targets(self):
        try:
            self.available_targets = get_available_audio_targets()
            displays = [t_item["display"] for t_item in self.available_targets]
            self.target_prog_menu.configure(values=displays)

            saved_pid = int(self.config.get("target_audio_pid", -1))
            current_choice = displays[0]
            for t_item in self.available_targets:
                if t_item["pid"] == saved_pid and saved_pid != -1:
                    current_choice = t_item["display"]
                    break

            self.target_prog_menu.set(current_choice)
            self._on_target_prog_selected(current_choice)
        except Exception as e:
            self.log_message(f"Error listing audio targets: {e}")

    def _on_target_prog_selected(self, chosen_display):
        for t_item in getattr(self, "available_targets", []):
            if t_item["display"] == chosen_display:
                pid = t_item["pid"]
                name = t_item["name"]
                prev_pid = self.config.get("target_audio_pid")
                self.config["target_audio_pid"] = pid
                self.config["target_audio_name"] = name
                if hasattr(self, "recorder_worker") and self.recorder_worker and hasattr(self.recorder_worker, "native_recorder"):
                    self.recorder_worker.native_recorder.set_target_process(pid, name)
                if hasattr(self, "_preview_native_recorder") and self._preview_native_recorder:
                    self._preview_native_recorder.set_target_process(pid, name)
                if prev_pid != pid:
                    save_config(self.config)
                break

    def _refresh_microphones(self):
        try:
            self.available_mics = get_available_microphones()
            displays = [m["display"] for m in self.available_mics]
            if hasattr(self, "mic_input_menu"):
                self.mic_input_menu.configure(values=displays)
            if hasattr(self, "settings_mic_menu"):
                self.settings_mic_menu.configure(values=displays)

            saved_name = str(self.config.get("mic_device_name", "default"))
            saved_idx = int(self.config.get("mic_device_index", -1))
            current_choice = displays[0]
            for m in self.available_mics:
                if (saved_name != "default" and m["name"] == saved_name) or (
                    saved_idx != -1 and m["index"] == saved_idx
                ):
                    current_choice = m["display"]
                    break

            if hasattr(self, "mic_input_menu"):
                self.mic_input_menu.set(current_choice)
            if hasattr(self, "settings_mic_menu"):
                self.settings_mic_menu.set(current_choice)
            self._on_mic_input_selected(current_choice)
        except Exception as e:
            self.log_message(f"Error listing microphones: {e}")

    def _on_mic_input_selected(self, chosen_display):
        for m in getattr(self, "available_mics", []):
            if m["display"] == chosen_display:
                idx = m["index"]
                name = m["name"]
                prev_idx = self.config.get("mic_device_index")
                prev_name = self.config.get("mic_device_name")
                self.config["mic_device_index"] = idx
                self.config["mic_device_name"] = name

                if hasattr(self, "mic_input_menu") and self.mic_input_menu.get() != chosen_display:
                    self.mic_input_menu.set(chosen_display)
                if hasattr(self, "settings_mic_menu") and self.settings_mic_menu.get() != chosen_display:
                    self.settings_mic_menu.set(chosen_display)

                if hasattr(self, "recorder_worker") and self.recorder_worker and hasattr(self.recorder_worker, "native_recorder"):
                    self.recorder_worker.native_recorder.set_mic_device(idx, name)
                if hasattr(self, "_preview_native_recorder") and self._preview_native_recorder:
                    self._preview_native_recorder.set_mic_device(idx, name)

                if prev_idx != idx or prev_name != name:
                    save_config(self.config)
                break

    def _start_audio_meter_loop(self):
        self._update_audio_meters()

    def _update_audio_meters(self):
        if getattr(self, "_is_destroyed", False):
            return
        try:
            # We can use recorder_worker's native_recorder if active, or inspect via NativeAudioRecorder directly
            native_rec = None
            if hasattr(self, "recorder_worker") and self.recorder_worker and hasattr(self.recorder_worker, "native_recorder"):
                if hasattr(self, "_preview_native_recorder") and self._preview_native_recorder:
                    try:
                        self._preview_native_recorder.close()
                    except Exception:
                        pass
                    self._preview_native_recorder = None
                native_rec = self.recorder_worker.native_recorder
            elif not hasattr(self, "_preview_native_recorder") or self._preview_native_recorder is None:
                from app.native_audio import NativeAudioRecorder
                self._preview_native_recorder = NativeAudioRecorder(
                    record_folder=self.config.get("record_folder"),
                    mic_device=self.config.get("mic_device_index"),
                    mic_device_name=self.config.get("mic_device_name"),
                    target_pid=int(self.config.get("target_audio_pid", -1)),
                    target_name=self.config.get("target_audio_name", "Discord"),
                )
                native_rec = self._preview_native_recorder
            else:
                native_rec = self._preview_native_recorder

            if native_rec:
                raw_app_l, raw_mic_l = native_rec.poll_audio_levels()
                is_discord_muted = getattr(native_rec, "is_discord_muted", lambda: False)()

                # Smooth ballistic interpolation for App audio (Discord)
                if not hasattr(self, "_smooth_app_l"):
                    self._smooth_app_l = 0.0
                if raw_app_l > self._smooth_app_l:
                    # Snappy attack: jump towards peak quickly
                    self._smooth_app_l = self._smooth_app_l * 0.35 + raw_app_l * 0.65
                else:
                    # Silky exponential decay
                    self._smooth_app_l = max(0.0, self._smooth_app_l * 0.84)
                if self._smooth_app_l < 0.002:
                    self._smooth_app_l = 0.0

                disp_app = round(self._smooth_app_l, 4)
                last_app_l = getattr(self, "_last_rendered_app_l", -1.0)
                if abs(disp_app - last_app_l) > 0.003 or (disp_app == 0.0 and last_app_l > 0.0):
                    app_color = "#22C55E" if disp_app < 0.65 else ("#EAB308" if disp_app < 0.85 else "#EF4444")
                    if getattr(self, "_last_app_color", None) != app_color:
                        self.app_meter_bar.configure(progress_color=app_color)
                        self._last_app_color = app_color
                    self.app_meter_bar.set(disp_app)
                    self._last_rendered_app_l = disp_app

                # Smooth ballistic interpolation for Microphone
                if not hasattr(self, "_smooth_mic_l"):
                    self._smooth_mic_l = 0.0

                if is_discord_muted:
                    self._smooth_mic_l = max(0.0, self._smooth_mic_l * 0.70)
                    if self._smooth_mic_l < 0.002:
                        self._smooth_mic_l = 0.0
                    if getattr(self, "_last_mic_muted_state", None) is not True:
                        self._last_mic_muted_state = True
                        self.mic_meter_title.configure(
                            text=f"🎤 {t('record.microphone')} [{t('record.muted')} 🔇]:",
                            text_color="#EF4444",
                        )
                        self.mic_meter_bar.configure(progress_color="#EF4444")
                        self._last_mic_color = "#EF4444"
                    if getattr(self, "_last_rendered_mic_l", -1.0) != 0.0:
                        self.mic_meter_bar.set(0.0)
                        self._last_rendered_mic_l = 0.0
                else:
                    if getattr(self, "_last_mic_muted_state", None) is not False:
                        self._last_mic_muted_state = False
                        self.mic_meter_title.configure(
                            text=f"🎤 {t('record.microphone')}:",
                            text_color="#94A3B8",
                        )
                    if raw_mic_l > self._smooth_mic_l:
                        # Snappy attack: jump towards peak quickly
                        self._smooth_mic_l = self._smooth_mic_l * 0.35 + raw_mic_l * 0.65
                    else:
                        # Silky exponential decay
                        self._smooth_mic_l = max(0.0, self._smooth_mic_l * 0.84)
                    if self._smooth_mic_l < 0.002:
                        self._smooth_mic_l = 0.0

                    disp_mic = round(self._smooth_mic_l, 4)
                    last_mic_l = getattr(self, "_last_rendered_mic_l", -1.0)
                    if abs(disp_mic - last_mic_l) > 0.003 or (disp_mic == 0.0 and last_mic_l > 0.0):
                        mic_color = "#0284C7" if disp_mic < 0.65 else ("#EAB308" if disp_mic < 0.85 else "#EF4444")
                        if getattr(self, "_last_mic_color", None) != mic_color:
                            self.mic_meter_bar.configure(progress_color=mic_color)
                            self._last_mic_color = mic_color
                        self.mic_meter_bar.set(disp_mic)
                        self._last_rendered_mic_l = disp_mic

                app_l = disp_app
                mic_l = disp_mic

            # Live HUD overlay visibility & waveform synchronization (in-game only)
            cur_mic = mic_l if "mic_l" in locals() else 0.0
            cur_app = app_l if "app_l" in locals() else 0.0
            cur_muted = is_discord_muted if "is_discord_muted" in locals() else False
            self._sync_hud_visibility_and_state(
                mic_l=cur_mic, app_l=cur_app, is_discord_muted=cur_muted
            )
        except Exception:
            pass
        self._meter_timer = self.after(30, self._update_audio_meters)

    def _setup_tab_log(self, parent):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(0, weight=1)

        # Log Text Box (expands vertically and horizontally)
        self.log_textbox = ctk.CTkTextbox(
            parent,
            font=ctk.CTkFont(family="Consolas", size=12),
            fg_color=("#FFFFFF", "#060B18"),
            border_width=1,
            border_color=("#E2E8F0", "#162038"),
            text_color=("#0F172A", "#CBD5E1"),
            corner_radius=10,
            wrap="word",
        )
        self.log_textbox.grid(row=0, column=0, padx=10, pady=(10, 8), sticky="nsew")
        self.log_textbox.configure(state="disabled")

        # Control Bar below log
        log_ctrl_frame = ctk.CTkFrame(parent, fg_color="transparent")
        log_ctrl_frame.grid(row=1, column=0, padx=10, pady=(0, 10), sticky="ew")

        self.autoscroll_var = tk.BooleanVar(value=True)
        self.autoscroll_cb = ctk.CTkCheckBox(
            log_ctrl_frame,
            text=t("log.autoscroll"),
            variable=self.autoscroll_var,
            font=ctk.CTkFont(size=12),
            checkmark_color=("#FFFFFF", "#060B18"),
            fg_color=("#0891B2", "#0AC8B9"),
            hover_color=("#0E7490", "#0397AB"),
            text_color=("#0F172A", "#CBD5E1"),
        )
        self.autoscroll_cb.pack(side="left")

        self.clear_btn = ctk.CTkButton(
            log_ctrl_frame,
            text=t("log.clear"),
            width=100,
            height=30,
            font=ctk.CTkFont(size=12),
            fg_color=("#F1F5F9", "#0F1A36"),
            hover_color=("#E2E8F0", "#1E2A4A"),
            border_width=1,
            border_color=("#CBD5E1", "#1E2A4A"),
            corner_radius=6,
            text_color=("#0F172A", "#E2E8F0"),
            command=self._clear_log,
        )
        self.clear_btn.pack(side="right", padx=(6, 0))

        self.copy_btn = ctk.CTkButton(
            log_ctrl_frame,
            text=t("log.copy"),
            width=100,
            height=30,
            font=ctk.CTkFont(size=12),
            fg_color=("#F1F5F9", "#0F1A36"),
            hover_color=("#E2E8F0", "#1E2A4A"),
            border_width=1,
            border_color=("#CBD5E1", "#1E2A4A"),
            corner_radius=6,
            text_color=("#0F172A", "#E2E8F0"),
            command=self._copy_log,
        )
        self.copy_btn.pack(side="right")

    # -------------------------------------------------------------
    # TAB 2: RECORDINGS EXPLORER
    # -------------------------------------------------------------
    def _setup_tab_recordings(self, parent):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(1, weight=1)

        # Top Bar
        rec_top_bar = ctk.CTkFrame(parent, fg_color="transparent")
        rec_top_bar.grid(row=0, column=0, padx=10, pady=(10, 6), sticky="ew")
        rec_top_bar.grid_columnconfigure(0, weight=1)

        self.rec_count_lbl = ctk.CTkLabel(
            rec_top_bar,
            text=t("recordings.count_label", count=0, size=0.0),
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=("#A07832", "#C8AA6E"),
        )
        self.rec_count_lbl.grid(row=0, column=0, sticky="w")

        # Download All Missing button (initially hidden, placed dynamically when missing remote files exist)
        self.download_all_btn = ctk.CTkButton(
            rec_top_bar,
            text=t("recordings.download_all", count=0),
            height=32,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=("#059669", "#10B981"),
            hover_color=("#047857", "#059669"),
            border_width=1,
            border_color=("#10B981", "#34D399"),
            corner_radius=8,
            command=self._download_all_missing_recordings,
        )

        self.sync_remote_btn = ctk.CTkButton(
            rec_top_bar,
            text=t("recordings.sync_remote"),
            height=32,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=("#4F46E5", "#6366F1"),
            hover_color=("#4338CA", "#4F46E5"),
            border_width=1,
            border_color=("#6366F1", "#818CF8"),
            corner_radius=8,
            command=self._sync_remote_recordings,
        )
        self.sync_remote_btn.grid(row=0, column=2, padx=4, sticky="e")

        self.open_folder_btn = ctk.CTkButton(
            rec_top_bar,
            text=t("recordings.open_folder"),
            height=32,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=("#0284C7", "#0284C7"),
            hover_color=("#0369A1", "#0369A1"),
            border_width=1,
            border_color=("#0284C7", "#38BDF8"),
            corner_radius=8,
            command=self._open_recordings_folder,
        )
        self.open_folder_btn.grid(row=0, column=3, padx=4, sticky="e")

        self.refresh_rec_btn = ctk.CTkButton(
            rec_top_bar,
            text=t("recordings.refresh"),
            width=85,
            height=32,
            font=ctk.CTkFont(size=12),
            fg_color=("#F1F5F9", "#0F1A36"),
            hover_color=("#E2E8F0", "#1E2A4A"),
            border_width=1,
            border_color=("#CBD5E1", "#1E2A4A"),
            text_color=("#0F172A", "#E2E8F0"),
            corner_radius=8,
            command=self._refresh_recordings_list,
        )
        self.refresh_rec_btn.grid(row=0, column=4, padx=4, sticky="e")

        # Scrollable Frame for List of Recordings
        self.rec_scroll_frame = ctk.CTkScrollableFrame(
            parent,
            fg_color=("#F8FAFC", "#070D1E"),
            corner_radius=10,
            border_width=1,
            border_color=("#E2E8F0", "#162038"),
        )
        self.rec_scroll_frame.grid(row=1, column=0, padx=10, pady=(0, 10), sticky="nsew")
        self.rec_scroll_frame.grid_columnconfigure(0, weight=1)

        self._refresh_recordings_list()

    # -------------------------------------------------------------
    # TAB 3: SETTINGS
    # -------------------------------------------------------------
    def _setup_tab_settings(self, parent):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(0, weight=1)

        scroll = ctk.CTkScrollableFrame(
            parent,
            fg_color=("#F8FAFC", "#070D1E"),
            corner_radius=10,
            border_width=1,
            border_color=("#E2E8F0", "#162038"),
        )
        scroll.grid(row=0, column=0, padx=8, pady=8, sticky="nsew")
        scroll.grid_columnconfigure(0, weight=1)

        # -------------------------------------------------------------
        # CARD 1: DIRECTORIES & STORAGE
        # -------------------------------------------------------------
        card_storage = ctk.CTkFrame(
            scroll,
            fg_color=("#FFFFFF", "#0B1329"),
            corner_radius=10,
            border_width=1,
            border_color=("#E2E8F0", "#1E2A4A"),
        )
        card_storage.pack(fill="x", padx=10, pady=(6, 10))
        card_storage.grid_columnconfigure(0, weight=0)
        card_storage.grid_columnconfigure(1, weight=1)
        card_storage.grid_columnconfigure(2, weight=0)

        self.dir_title_lbl = ctk.CTkLabel(
            card_storage,
            text=f"📁 {t('settings.paths_title')}",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=("#0891B2", "#0AC8B9"),
        )
        self.dir_title_lbl.grid(row=0, column=0, columnspan=3, padx=14, pady=(10, 8), sticky="w")

        # Recordings Folder
        self.rec_folder_lbl = ctk.CTkLabel(
            card_storage,
            text=t("settings.rec_folder"),
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=("#0F172A", "#CBD5E1"),
        )
        self.rec_folder_lbl.grid(row=1, column=0, padx=(14, 8), pady=6, sticky="w")

        self.rec_folder_entry = ctk.CTkEntry(
            card_storage,
            height=32,
            fg_color=("#F8FAFC", "#070D1E"),
            border_color=("#CBD5E1", "#1E2A4A"),
            border_width=1,
            text_color=("#0F172A", "#F8FAFC"),
            corner_radius=6,
        )
        self.rec_folder_entry.insert(
            0, str(self.config.get("record_folder") or get_current_user_documents_recordings_dir())
        )
        self.rec_folder_entry.grid(row=1, column=1, padx=6, pady=6, sticky="ew")
        self.rec_folder_entry.bind("<FocusOut>", self._on_settings_entry_changed)
        self.rec_folder_entry.bind("<Return>", self._on_settings_entry_changed)
        self.rec_folder_entry.bind("<KeyRelease>", self._on_settings_entry_debounced)

        self.browse_rec_btn = ctk.CTkButton(
            card_storage,
            text=t("settings.browse"),
            width=80,
            height=32,
            fg_color=("#F1F5F9", "#0F1A36"),
            hover_color=("#E2E8F0", "#1E2A4A"),
            border_width=1,
            border_color=("#CBD5E1", "#1E2A4A"),
            text_color=("#0F172A", "#E2E8F0"),
            corner_radius=6,
            command=self._browse_rec_folder,
        )
        self.browse_rec_btn.grid(row=1, column=2, padx=(4, 14), pady=6, sticky="w")

        # VLC Directory
        self.vlc_dir_lbl = ctk.CTkLabel(
            card_storage,
            text=t("settings.vlc_dir"),
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=("#0F172A", "#CBD5E1"),
        )
        self.vlc_dir_lbl.grid(row=2, column=0, padx=(14, 8), pady=6, sticky="w")

        self.vlc_dir_entry = ctk.CTkEntry(
            card_storage,
            height=32,
            fg_color=("#F8FAFC", "#070D1E"),
            border_color=("#CBD5E1", "#1E2A4A"),
            border_width=1,
            text_color=("#0F172A", "#F8FAFC"),
            corner_radius=6,
        )
        self.vlc_dir_entry.insert(
            0, str(self.config.get("vlc_dir", r"C:\Program Files\VideoLAN\VLC"))
        )
        self.vlc_dir_entry.grid(row=2, column=1, padx=6, pady=6, sticky="ew")
        self.vlc_dir_entry.bind("<FocusOut>", self._on_settings_entry_changed)
        self.vlc_dir_entry.bind("<Return>", self._on_settings_entry_changed)
        self.vlc_dir_entry.bind("<KeyRelease>", self._on_settings_entry_debounced)

        self.browse_vlc_btn = ctk.CTkButton(
            card_storage,
            text=t("settings.browse"),
            width=80,
            height=32,
            fg_color=("#F1F5F9", "#0F1A36"),
            hover_color=("#E2E8F0", "#1E2A4A"),
            border_width=1,
            border_color=("#CBD5E1", "#1E2A4A"),
            text_color=("#0F172A", "#E2E8F0"),
            corner_radius=6,
            command=self._browse_vlc_folder,
        )
        self.browse_vlc_btn.grid(row=2, column=2, padx=(4, 14), pady=6, sticky="w")

        # Replay Volume
        self.replay_vol_lbl = ctk.CTkLabel(
            card_storage,
            text=t("settings.replay_volume"),
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=("#0F172A", "#CBD5E1"),
        )
        self.replay_vol_lbl.grid(row=3, column=0, padx=(14, 8), pady=6, sticky="w")

        self.replay_vol_entry = ctk.CTkEntry(
            card_storage,
            height=32,
            width=100,
            fg_color=("#F8FAFC", "#070D1E"),
            border_color=("#CBD5E1", "#1E2A4A"),
            border_width=1,
            text_color=("#0F172A", "#F8FAFC"),
            corner_radius=6,
        )
        self.replay_vol_entry.insert(0, str(self.config.get("replay_volume", 80)))
        self.replay_vol_entry.grid(row=3, column=1, padx=6, pady=6, sticky="w")
        self.replay_vol_entry.bind("<FocusOut>", self._on_settings_entry_changed)
        self.replay_vol_entry.bind("<Return>", self._on_settings_entry_changed)
        self.replay_vol_entry.bind("<KeyRelease>", self._on_settings_entry_debounced)

        # -------------------------------------------------------------
        # CARD 2: INTERFACE & AUDIO INPUT
        # -------------------------------------------------------------
        card_interface = ctk.CTkFrame(
            scroll,
            fg_color=("#FFFFFF", "#0B1329"),
            corner_radius=10,
            border_width=1,
            border_color=("#E2E8F0", "#1E2A4A"),
        )
        card_interface.pack(fill="x", padx=10, pady=(0, 10))
        card_interface.grid_columnconfigure(0, weight=0)
        card_interface.grid_columnconfigure(1, weight=1)

        self.interface_title_lbl = ctk.CTkLabel(
            card_interface,
            text=f"🌐 {t('settings.interface_title')}",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=("#0891B2", "#0AC8B9"),
        )
        self.interface_title_lbl.grid(row=0, column=0, columnspan=2, padx=14, pady=(10, 8), sticky="w")

        # Language Selector
        self.lang_code_to_name = get_available_languages()
        self.lang_name_to_code = {v: k for k, v in self.lang_code_to_name.items()}
        current_code = self.config.get("language", "en")
        current_display = self.lang_code_to_name.get(current_code, "English")

        self.lang_lbl = ctk.CTkLabel(
            card_interface,
            text=t("settings.language"),
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=("#0F172A", "#CBD5E1"),
        )
        self.lang_lbl.grid(row=1, column=0, padx=(14, 8), pady=6, sticky="w")

        self.lang_menu = ctk.CTkOptionMenu(
            card_interface,
            values=list(self.lang_name_to_code.keys()),
            command=self._on_language_selected,
            height=32,
            fg_color=("#F8FAFC", "#070D1E"),
            text_color=("#0F172A", "#F8FAFC"),
            button_color=("#0891B2", "#0AC8B9"),
            button_hover_color=("#0E7490", "#0397AB"),
            dropdown_fg_color=("#FFFFFF", "#0B1329"),
            dropdown_text_color=("#0F172A", "#F8FAFC"),
            dropdown_hover_color=("#F1F5F9", "#162038"),
            corner_radius=6,
        )
        self.lang_menu.set(current_display)
        self.lang_menu.grid(row=1, column=1, padx=(6, 14), pady=6, sticky="ew")

        # Appearance Theme Selector
        self.theme_lbl = ctk.CTkLabel(
            card_interface,
            text=t("settings.theme"),
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=("#0F172A", "#CBD5E1"),
        )
        self.theme_lbl.grid(row=2, column=0, padx=(14, 8), pady=6, sticky="w")

        self.theme_options = {
            t("settings.theme_dark"): "dark",
            t("settings.theme_light"): "light",
            t("settings.theme_system"): "system",
        }
        self.theme_code_to_display = {v: k for k, v in self.theme_options.items()}
        curr_theme_display = self.theme_code_to_display.get(self.current_theme, t("settings.theme_dark"))

        self.theme_menu = ctk.CTkOptionMenu(
            card_interface,
            values=list(self.theme_options.keys()),
            command=self._on_theme_selected,
            height=32,
            fg_color=("#F8FAFC", "#070D1E"),
            text_color=("#0F172A", "#F8FAFC"),
            button_color=("#0891B2", "#0AC8B9"),
            button_hover_color=("#0E7490", "#0397AB"),
            dropdown_fg_color=("#FFFFFF", "#0B1329"),
            dropdown_text_color=("#0F172A", "#F8FAFC"),
            dropdown_hover_color=("#F1F5F9", "#162038"),
            corner_radius=6,
        )
        self.theme_menu.set(curr_theme_display)
        self.theme_menu.grid(row=2, column=1, padx=(6, 14), pady=6, sticky="ew")

        # Microphone Input
        self.settings_mic_lbl = ctk.CTkLabel(
            card_interface,
            text=t("settings.mic_device"),
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=("#0F172A", "#CBD5E1"),
        )
        self.settings_mic_lbl.grid(row=3, column=0, padx=(14, 8), pady=6, sticky="w")

        self.settings_mic_menu = ctk.CTkOptionMenu(
            card_interface,
            values=[t("record.mic_default")],
            command=self._on_mic_input_selected,
            height=32,
            fg_color=("#F8FAFC", "#070D1E"),
            text_color=("#0F172A", "#F8FAFC"),
            button_color=("#0891B2", "#0AC8B9"),
            button_hover_color=("#0E7490", "#0397AB"),
            dropdown_fg_color=("#FFFFFF", "#0B1329"),
            dropdown_text_color=("#0F172A", "#F8FAFC"),
            dropdown_hover_color=("#F1F5F9", "#162038"),
            corner_radius=6,
        )
        self.settings_mic_menu.grid(row=3, column=1, padx=(6, 14), pady=6, sticky="ew")

        # -------------------------------------------------------------
        # CARD 3: MATCH AUTOMATION & WORKFLOW
        # -------------------------------------------------------------
        card_auto = ctk.CTkFrame(
            scroll,
            fg_color=("#FFFFFF", "#0B1329"),
            corner_radius=10,
            border_width=1,
            border_color=("#E2E8F0", "#1E2A4A"),
        )
        card_auto.pack(fill="x", padx=10, pady=(0, 10))
        card_auto.grid_columnconfigure(0, weight=0)
        card_auto.grid_columnconfigure(1, weight=1)

        self.automation_title_lbl = ctk.CTkLabel(
            card_auto,
            text=f"⚡ {t('settings.automation_title')}",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=("#0891B2", "#0AC8B9"),
        )
        self.automation_title_lbl.grid(row=0, column=0, columnspan=2, padx=14, pady=(10, 8), sticky="w")

        # Auto-Start in Champion Select
        self.auto_champ_select_lbl = ctk.CTkLabel(
            card_auto,
            text=t("settings.auto_start_champ_select"),
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=("#0F172A", "#CBD5E1"),
        )
        self.auto_champ_select_lbl.grid(row=1, column=0, padx=(14, 8), pady=6, sticky="w")

        self.auto_champ_select_var = ctk.BooleanVar(
            value=bool(self.config.get("auto_start_champ_select", True))
        )
        self.auto_champ_select_switch = ctk.CTkSwitch(
            card_auto,
            text=t("settings.auto_start_champ_select_info"),
            variable=self.auto_champ_select_var,
            onvalue=True,
            offvalue=False,
            fg_color=("#CBD5E1", "#1E2A4A"),
            progress_color=("#0891B2", "#0AC8B9"),
            button_color=("#FFFFFF", "#FFFFFF"),
            button_hover_color=("#F8FAFC", "#E2E8F0"),
            text_color=("#334155", "#94A3B8"),
            command=self._on_auto_champ_select_toggled,
        )
        self.auto_champ_select_switch.grid(row=1, column=1, padx=(6, 14), pady=6, sticky="w")

        # Auto-Start Replay Sync
        self.auto_replay_lbl = ctk.CTkLabel(
            card_auto,
            text=t("settings.auto_start_replay"),
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=("#0F172A", "#CBD5E1"),
        )
        self.auto_replay_lbl.grid(row=2, column=0, padx=(14, 8), pady=6, sticky="w")

        self.auto_replay_var = ctk.BooleanVar(
            value=bool(self.config.get("auto_start_replay", True))
        )
        self.auto_replay_switch = ctk.CTkSwitch(
            card_auto,
            text=t("settings.auto_start_replay_info"),
            variable=self.auto_replay_var,
            onvalue=True,
            offvalue=False,
            fg_color=("#CBD5E1", "#1E2A4A"),
            progress_color=("#0891B2", "#0AC8B9"),
            button_color=("#FFFFFF", "#FFFFFF"),
            button_hover_color=("#F8FAFC", "#E2E8F0"),
            text_color=("#334155", "#94A3B8"),
            command=self._on_auto_replay_toggled,
        )
        self.auto_replay_switch.grid(row=2, column=1, padx=(6, 14), pady=6, sticky="w")

        # Minimize to System Tray
        self.minimize_to_tray_lbl = ctk.CTkLabel(
            card_auto,
            text=t("settings.minimize_to_tray"),
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=("#0F172A", "#CBD5E1"),
        )
        self.minimize_to_tray_lbl.grid(row=3, column=0, padx=(14, 8), pady=(6, 12), sticky="w")

        self.minimize_to_tray_var = ctk.BooleanVar(
            value=bool(self.config.get("minimize_to_tray", True))
        )
        self.minimize_to_tray_switch = ctk.CTkSwitch(
            card_auto,
            text=t("settings.minimize_to_tray_info"),
            variable=self.minimize_to_tray_var,
            onvalue=True,
            offvalue=False,
            fg_color=("#CBD5E1", "#1E2A4A"),
            progress_color=("#0891B2", "#0AC8B9"),
            button_color=("#FFFFFF", "#FFFFFF"),
            button_hover_color=("#F8FAFC", "#E2E8F0"),
            text_color=("#334155", "#94A3B8"),
            command=self._on_minimize_to_tray_toggled,
        )
        self.minimize_to_tray_switch.grid(row=3, column=1, padx=(6, 14), pady=(6, 12), sticky="w")

        # -------------------------------------------------------------
        # CARD 4: IN-GAME FLOATING HUD OVERLAY
        # -------------------------------------------------------------
        card_hud = ctk.CTkFrame(
            scroll,
            fg_color=("#FFFFFF", "#0B1329"),
            corner_radius=10,
            border_width=1,
            border_color=("#E2E8F0", "#1E2A4A"),
        )
        card_hud.pack(fill="x", padx=10, pady=(0, 10))
        card_hud.grid_columnconfigure(0, weight=0)
        card_hud.grid_columnconfigure(1, weight=1)

        self.hud_title_lbl = ctk.CTkLabel(
            card_hud,
            text=f"🎮 {t('settings.hud_section_title')}",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=("#0891B2", "#0AC8B9"),
        )
        self.hud_title_lbl.grid(row=0, column=0, columnspan=2, padx=14, pady=(10, 8), sticky="w")

        # Enable HUD Overlay Switch
        self.hud_enable_lbl = ctk.CTkLabel(
            card_hud,
            text=t("settings.hud_overlay_enabled"),
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=("#0F172A", "#CBD5E1"),
        )
        self.hud_enable_lbl.grid(row=1, column=0, padx=(14, 8), pady=6, sticky="w")

        self.hud_enable_var = ctk.BooleanVar(
            value=bool(self.config.get("hud_overlay_enabled", True))
        )
        self.hud_enable_switch = ctk.CTkSwitch(
            card_hud,
            text=t("settings.hud_overlay_info"),
            variable=self.hud_enable_var,
            onvalue=True,
            offvalue=False,
            fg_color=("#CBD5E1", "#1E2A4A"),
            progress_color=("#0891B2", "#0AC8B9"),
            button_color=("#FFFFFF", "#FFFFFF"),
            button_hover_color=("#F8FAFC", "#E2E8F0"),
            text_color=("#334155", "#94A3B8"),
            command=self._on_hud_enabled_toggled,
        )
        self.hud_enable_switch.grid(row=1, column=1, padx=(6, 14), pady=6, sticky="w")

        # Lock Position / Click-Through Mode Switch
        self.hud_lock_lbl = ctk.CTkLabel(
            card_hud,
            text=t("settings.hud_locked"),
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=("#0F172A", "#CBD5E1"),
        )
        self.hud_lock_lbl.grid(row=2, column=0, padx=(14, 8), pady=6, sticky="w")

        self.hud_lock_var = ctk.BooleanVar(
            value=bool(self.config.get("hud_locked", self.config.get("hud_click_through", False)))
        )
        self.hud_lock_switch = ctk.CTkSwitch(
            card_hud,
            text=t("settings.hud_locked_info"),
            variable=self.hud_lock_var,
            onvalue=True,
            offvalue=False,
            fg_color=("#CBD5E1", "#1E2A4A"),
            progress_color=("#0891B2", "#0AC8B9"),
            button_color=("#FFFFFF", "#FFFFFF"),
            button_hover_color=("#F8FAFC", "#E2E8F0"),
            text_color=("#334155", "#94A3B8"),
            command=self._on_hud_lock_toggled,
        )
        self.hud_lock_switch.grid(row=2, column=1, padx=(6, 14), pady=6, sticky="w")

        # Backward compatibility aliases
        self.hud_click_through_lbl = self.hud_lock_lbl
        self.hud_click_through_var = self.hud_lock_var
        self.hud_click_through_switch = self.hud_lock_switch

        # HUD Opacity Slider
        curr_opacity = float(self.config.get("hud_opacity", 0.85))
        self.hud_opacity_lbl = ctk.CTkLabel(
            card_hud,
            text=t("settings.hud_opacity", percent=int(curr_opacity * 100)),
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=("#0F172A", "#CBD5E1"),
        )
        self.hud_opacity_lbl.grid(row=3, column=0, padx=(14, 8), pady=6, sticky="w")

        self.hud_opacity_slider = ctk.CTkSlider(
            card_hud,
            from_=0.40,
            to=1.00,
            number_of_steps=12,
            fg_color=("#CBD5E1", "#1E2A4A"),
            progress_color=("#0891B2", "#0AC8B9"),
            button_color=("#0891B2", "#0AC8B9"),
            button_hover_color=("#0E7490", "#0397AB"),
            command=self._on_hud_opacity_slider_changed,
            height=16,
        )
        self.hud_opacity_slider.set(curr_opacity)
        self.hud_opacity_slider.grid(row=3, column=1, padx=(6, 14), pady=6, sticky="ew")

        # HUD Action / Preview Buttons
        hud_btn_frame = ctk.CTkFrame(card_hud, fg_color="transparent")
        hud_btn_frame.grid(row=4, column=0, columnspan=2, padx=14, pady=(10, 12), sticky="ew")

        self.hud_preview_rec_btn = ctk.CTkButton(
            hud_btn_frame,
            text=t("settings.hud_preview_rec_btn"),
            font=ctk.CTkFont(size=11, weight="bold"),
            height=30,
            fg_color=("#F1F5F9", "#0F1A36"),
            hover_color=("#E2E8F0", "#1E2A4A"),
            border_width=1,
            border_color=("#CBD5E1", "#1E2A4A"),
            text_color=("#0F172A", "#E2E8F0"),
            corner_radius=6,
            command=self._preview_rec_hud,
        )
        self.hud_preview_rec_btn.pack(side="left", padx=(0, 8))

        self.hud_preview_replay_btn = ctk.CTkButton(
            hud_btn_frame,
            text=t("settings.hud_preview_replay_btn"),
            font=ctk.CTkFont(size=11, weight="bold"),
            height=30,
            fg_color=("#F1F5F9", "#0F1A36"),
            hover_color=("#E2E8F0", "#1E2A4A"),
            border_width=1,
            border_color=("#CBD5E1", "#1E2A4A"),
            text_color=("#0F172A", "#E2E8F0"),
            corner_radius=6,
            command=self._preview_replay_hud,
        )
        self.hud_preview_replay_btn.pack(side="left", padx=(0, 8))

        self.hud_hide_preview_btn = ctk.CTkButton(
            hud_btn_frame,
            text=t("settings.hud_hide_preview_btn"),
            font=ctk.CTkFont(size=11, weight="bold"),
            height=30,
            fg_color=("#F1F5F9", "#0F1A36"),
            hover_color=("#E2E8F0", "#1E2A4A"),
            border_width=1,
            border_color=("#CBD5E1", "#1E2A4A"),
            text_color=("#0F172A", "#E2E8F0"),
            corner_radius=6,
            command=self._hide_hud_preview,
        )
        self.hud_hide_preview_btn.pack(side="left", padx=(0, 8))

        self.hud_reset_pos_btn = ctk.CTkButton(
            hud_btn_frame,
            text=t("settings.hud_reset_pos_btn"),
            font=ctk.CTkFont(size=11, weight="bold"),
            height=30,
            fg_color=("#F1F5F9", "#0F1A36"),
            hover_color=("#E2E8F0", "#1E2A4A"),
            border_width=1,
            border_color=("#CBD5E1", "#1E2A4A"),
            text_color=("#0F172A", "#E2E8F0"),
            corner_radius=6,
            command=self._reset_hud_position,
        )
        self.hud_reset_pos_btn.pack(side="left", padx=(0, 8))

        # -------------------------------------------------------------
        # CARD 5: REMOTE SCRIM SERVER (DIRECT PORT)
        # -------------------------------------------------------------
        card_cloud = ctk.CTkFrame(
            scroll,
            fg_color=("#FFFFFF", "#0B1329"),
            corner_radius=10,
            border_width=1,
            border_color=("#E2E8F0", "#1E2A4A"),
        )
        card_cloud.pack(fill="x", padx=10, pady=(0, 10))
        card_cloud.grid_columnconfigure(0, weight=0)
        card_cloud.grid_columnconfigure(1, weight=1)
        card_cloud.grid_columnconfigure(2, weight=0)

        self.cloud_title_lbl = ctk.CTkLabel(
            card_cloud,
            text=f"☁️ {t('settings.cloud_title')}",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=("#0891B2", "#0AC8B9"),
        )
        self.cloud_title_lbl.grid(row=0, column=0, columnspan=3, padx=14, pady=(10, 8), sticky="w")

        # Server URL
        self.cloud_url_lbl = ctk.CTkLabel(
            card_cloud,
            text=t("settings.cloud_url"),
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=("#0F172A", "#CBD5E1"),
        )
        self.cloud_url_lbl.grid(row=1, column=0, padx=(14, 8), pady=6, sticky="w")

        self.cloud_url_entry = ctk.CTkEntry(
            card_cloud,
            height=32,
            placeholder_text="https://your-server-ip-or-domain:8443",
            fg_color=("#F8FAFC", "#070D1E"),
            border_color=("#CBD5E1", "#1E2A4A"),
            border_width=1,
            text_color=("#0F172A", "#F8FAFC"),
            corner_radius=6,
        )
        self.cloud_url_entry.insert(0, str(self.config.get("remote_server_url") or ""))
        self.cloud_url_entry.grid(row=1, column=1, columnspan=2, padx=(6, 14), pady=6, sticky="ew")
        self.cloud_url_entry.bind("<FocusOut>", self._on_settings_entry_changed)
        self.cloud_url_entry.bind("<Return>", self._on_settings_entry_changed)
        self.cloud_url_entry.bind("<KeyRelease>", self._on_settings_entry_debounced)

        # API Secret Key
        self.cloud_key_lbl = ctk.CTkLabel(
            card_cloud,
            text=t("settings.cloud_api_key"),
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=("#0F172A", "#CBD5E1"),
        )
        self.cloud_key_lbl.grid(row=2, column=0, padx=(14, 8), pady=6, sticky="w")

        self.cloud_key_entry = ctk.CTkEntry(
            card_cloud,
            height=32,
            show="*",
            placeholder_text="Paste your UPLOAD_KEY or PULL_KEY",
            fg_color=("#F8FAFC", "#070D1E"),
            border_color=("#CBD5E1", "#1E2A4A"),
            border_width=1,
            text_color=("#0F172A", "#F8FAFC"),
            corner_radius=6,
        )
        self.cloud_key_entry.insert(0, str(self.config.get("remote_api_key") or ""))
        self.cloud_key_entry.grid(row=2, column=1, padx=(6, 4), pady=6, sticky="ew")
        self.cloud_key_entry.bind("<FocusOut>", self._on_settings_entry_changed)
        self.cloud_key_entry.bind("<Return>", self._on_settings_entry_changed)
        self.cloud_key_entry.bind("<KeyRelease>", self._on_settings_entry_debounced)

        self._cloud_key_revealed = False
        self.cloud_key_reveal_btn = ctk.CTkButton(
            card_cloud,
            text="👁️",
            width=40,
            height=32,
            font=ctk.CTkFont(size=13),
            fg_color=("#F1F5F9", "#0F1A36"),
            hover_color=("#E2E8F0", "#1E2A4A"),
            border_width=1,
            border_color=("#CBD5E1", "#1E2A4A"),
            corner_radius=6,
            command=self._toggle_cloud_key_visibility,
        )
        self.cloud_key_reveal_btn.grid(row=2, column=2, padx=(0, 14), pady=6, sticky="w")

        # Test Connection button & status label
        cloud_actions_frame = ctk.CTkFrame(card_cloud, fg_color="transparent")
        cloud_actions_frame.grid(row=3, column=0, columnspan=3, padx=14, pady=(4, 8), sticky="w")

        self.cloud_test_btn = ctk.CTkButton(
            cloud_actions_frame,
            text=t("settings.cloud_test_btn"),
            width=140,
            height=30,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=("#4F46E5", "#6366F1"),
            hover_color=("#4338CA", "#4F46E5"),
            corner_radius=6,
            command=self._test_cloud_connection,
        )
        self.cloud_test_btn.pack(side="left", padx=(0, 12))

        self.cloud_status_lbl = ctk.CTkLabel(
            cloud_actions_frame,
            text="",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#64748B",
        )
        self.cloud_status_lbl.pack(side="left")


        # -------------------------------------------------------------
        # CARD 6: SOFTWARE & UPDATES
        # -------------------------------------------------------------
        card_updates = ctk.CTkFrame(
            scroll,
            fg_color=("#FFFFFF", "#0B1329"),
            corner_radius=10,
            border_width=1,
            border_color=("#E2E8F0", "#1E2A4A"),
        )
        card_updates.pack(fill="x", padx=10, pady=(0, 10))
        card_updates.grid_columnconfigure(0, weight=0)
        card_updates.grid_columnconfigure(1, weight=1)

        self.updates_title_lbl = ctk.CTkLabel(
            card_updates,
            text=f"🔄 {t('settings.updates_title')}",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=("#0891B2", "#0AC8B9"),
        )
        self.updates_title_lbl.grid(row=0, column=0, columnspan=2, padx=14, pady=(10, 8), sticky="w")

        self.app_version_lbl = ctk.CTkLabel(
            card_updates,
            text=t("settings.app_version"),
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=("#0F172A", "#CBD5E1"),
        )
        self.app_version_lbl.grid(row=1, column=0, padx=(14, 8), pady=6, sticky="w")

        version_frame = ctk.CTkFrame(card_updates, fg_color="transparent")
        version_frame.grid(row=1, column=1, padx=(6, 14), pady=6, sticky="w")

        self.version_val_lbl = ctk.CTkLabel(
            version_frame,
            text=f"v{__version__}",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=("#64748B", "#94A3B8"),
        )
        self.version_val_lbl.pack(side="left", padx=(0, 12))

        self.check_update_btn = ctk.CTkButton(
            version_frame,
            text=t("settings.check_updates_btn"),
            height=28,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=("#F1F5F9", "#0F1A36"),
            hover_color=("#E2E8F0", "#1E2A4A"),
            border_width=1,
            border_color=("#CBD5E1", "#1E2A4A"),
            text_color=("#0F172A", "#E2E8F0"),
            corner_radius=6,
            command=self._on_check_update_clicked,
        )
        self.check_update_btn.pack(side="left")

        self.update_status_lbl = ctk.CTkLabel(
            card_updates,
            text="",
            font=ctk.CTkFont(size=11),
            text_color=("#64748B", "#94A3B8"),
        )
        self.update_status_lbl.grid(row=2, column=0, columnspan=2, padx=14, pady=(4, 0), sticky="w")

        self.update_action_btn = ctk.CTkButton(
            card_updates,
            text="",
            height=34,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#10B981",
            hover_color="#059669",
            corner_radius=6,
            command=self._on_perform_update_clicked,
        )

        # -------------------------------------------------------------
        # BOTTOM SAVE SETTINGS BUTTON (Removed: settings save instantly upon edit)
        # -------------------------------------------------------------
        self.save_btn = None

    # ==========================================
    # IN-GAME FLOATING HUD WIDGET HANDLERS
    # ==========================================
    def _on_hud_volume_changed(self, vol_pct):
        """Called when player interacts with the volume slider on the Replay HUD."""
        if self.replay_worker and hasattr(self.replay_worker, "set_volume"):
            self.replay_worker.set_volume(vol_pct)
        self.config["replay_volume"] = vol_pct
        if hasattr(self, "vol_slider"):
            self.vol_slider.set(vol_pct)
        self._replay_muted = False
        self._update_volume_display()

    def _on_hud_position_changed(self, x, y):
        """Saves repositioned HUD coordinates."""
        self.config["hud_x"] = x
        self.config["hud_y"] = y
        save_config(self.config)

    def _on_auto_champ_select_toggled(self):
        val = bool(self.auto_champ_select_var.get())
        self.config["auto_start_champ_select"] = val
        save_config(self.config)

    def _on_auto_replay_toggled(self):
        val = bool(self.auto_replay_var.get())
        self.config["auto_start_replay"] = val
        save_config(self.config)

    def _on_minimize_to_tray_toggled(self):
        val = bool(self.minimize_to_tray_var.get())
        self.config["minimize_to_tray"] = val
        save_config(self.config)
        if not val and hasattr(self, "tray_manager") and self.tray_manager:
            self.tray_manager.hide()

    def _toggle_cloud_key_visibility(self):
        self._cloud_key_revealed = not getattr(self, "_cloud_key_revealed", False)
        if hasattr(self, "cloud_key_entry"):
            self.cloud_key_entry.configure(show="" if self._cloud_key_revealed else "*")
        if hasattr(self, "cloud_key_reveal_btn"):
            self.cloud_key_reveal_btn.configure(text="🔒" if self._cloud_key_revealed else "👁️")

    def _test_cloud_connection(self):
        url = self.cloud_url_entry.get().strip() if hasattr(self, "cloud_url_entry") else ""
        key = self.cloud_key_entry.get().strip() if hasattr(self, "cloud_key_entry") else ""
        if not url or not key:
            if hasattr(self, "cloud_status_lbl"):
                self.cloud_status_lbl.configure(
                    text="⚠️ Please enter Server URL and API Key",
                    text_color="#F59E0B",
                )
            return

        if hasattr(self, "cloud_status_lbl"):
            self.cloud_status_lbl.configure(text=t("settings.cloud_testing"), text_color="#38BDF8")
        if hasattr(self, "cloud_test_btn"):
            self.cloud_test_btn.configure(state="disabled")

        def _worker():
            from app.cloud_sync import CloudSyncClient

            client = CloudSyncClient(server_url=url, api_key=key)
            success, info = client.check_health()

            def _ui():
                if hasattr(self, "cloud_test_btn"):
                    self.cloud_test_btn.configure(state="normal")
                if not hasattr(self, "cloud_status_lbl"):
                    return
                if success and isinstance(info, dict):
                    role = str(info.get("authenticated_role") or "unknown").lower()
                    self._cloud_role = role
                    if role == "upload":
                        txt = t("settings.cloud_connected_upload")
                    elif role == "pull":
                        txt = t("settings.cloud_connected_pull")
                    elif role == "admin":
                        txt = t("settings.cloud_connected_admin")
                    else:
                        txt = f"✔ Connected ({role})"
                    self.cloud_status_lbl.configure(text=txt, text_color="#10B981")
                    self.log_message(
                        f"☁ [Cloud] Connected to {url} (Role: {role}, Scrims: {info.get('recording_count', 0)})"
                    )
                    self._refresh_recordings_list()
                else:
                    err_msg = str(info)
                    self.cloud_status_lbl.configure(
                        text=t("settings.cloud_failed", error=err_msg[:35]),
                        text_color="#EF4444",
                    )
                    self.log_message(f"❌ [Cloud] Connection test failed: {err_msg}")

            self.after(0, _ui)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_hud_enabled_toggled(self):
        enabled = bool(self.hud_enable_var.get())
        self.config["hud_overlay_enabled"] = enabled
        save_config(self.config)
        if not enabled and self.hud:
            self.hud.hide()

    def _on_hud_lock_toggled(self):
        locked = bool(self.hud_lock_var.get())
        self.config["hud_locked"] = locked
        self.config["hud_click_through"] = locked
        save_config(self.config)
        if self.hud:
            self.hud.set_locked(locked)

    def _on_hud_click_through_toggled(self):
        self._on_hud_lock_toggled()

    def _on_hud_lock_changed_from_overlay(self, locked: bool):
        """Called when player clicks the lock/unlock icon directly on the overlay."""
        self.config["hud_locked"] = locked
        self.config["hud_click_through"] = locked
        save_config(self.config)
        if hasattr(self, "hud_lock_var"):
            self.hud_lock_var.set(locked)

    def _on_hud_opacity_slider_changed(self, val):
        op = round(float(val), 2)
        self.config["hud_opacity"] = op
        if hasattr(self, "_save_hud_op_timer") and self._save_hud_op_timer:
            try:
                self.after_cancel(self._save_hud_op_timer)
            except Exception:
                pass
        self._save_hud_op_timer = self.after(500, lambda: save_config(self.config))
        if hasattr(self, "hud_opacity_lbl"):
            self.hud_opacity_lbl.configure(
                text=t("settings.hud_opacity", percent=int(op * 100))
            )
        if self.hud:
            self.hud.set_opacity(op)

    def _preview_rec_hud(self):
        if not self.hud:
            return
        self._is_hud_previewing = True
        self.hud.set_mode("RECORD")
        self.hud.show()
        self.hud.update_recorder_state(
            time_str="12:48",
            mic_level=0.75,
            discord_level=0.45,
            is_paused=False,
            is_muted=False,
        )

    def _preview_replay_hud(self):
        if not self.hud:
            return
        self._is_hud_previewing = True
        self.hud.set_mode("REPLAY")
        self.hud.show()
        self.hud.update_replay_state(
            time_str="12:48",
            current_time=768.0,
            total_duration=2250.0,
            volume=int(self.config.get("replay_volume", 80)),
            is_paused=False,
            is_muted=bool(getattr(self, "_replay_muted", False)),
            speed=1.0,
        )

    def _hide_hud_preview(self):
        self._is_hud_previewing = False
        if self.hud:
            self.hud.hide()

    def _reset_hud_position(self):
        if self.hud:
            self.hud.reset_position()

    def _refresh_replay_api_ui_status(self):
        """Refreshes the Replay API status badge and button state in Settings."""
        if not hasattr(self, "replay_api_badge") or getattr(self, "_is_destroyed", False):
            return
        if getattr(self, "_in_test_suite", False):
            self.replay_api_badge.configure(
                text=t("settings.replay_api_status_enabled"),
                text_color=("#059669", "#10B981"),
                fg_color=("#DCFCE7", "#064E3B"),
            )
            return
        try:
            from app.replay_config import check_replay_api_status

            is_enabled, path = check_replay_api_status()
            if path is None:
                self.replay_api_badge.configure(
                    text=t("settings.replay_api_status_not_found"),
                    text_color=("#64748B", "#94A3B8"),
                    fg_color=("#F1F5F9", "#0F1A36"),
                )
                self.replay_api_btn.configure(
                    state="normal",
                    text=t("settings.enable_replay_api_btn"),
                    fg_color=("#0284C7", "#0284C7"),
                )
            elif is_enabled:
                self.replay_api_badge.configure(
                    text=t("settings.replay_api_status_enabled"),
                    text_color=("#059669", "#10B981"),
                    fg_color=("#DCFCE7", "#064E3B"),
                )
                self.replay_api_btn.configure(
                    state="normal",
                    text=f"✔ {t('settings.enable_replay_api_btn').replace('⚡ ', '')}",
                    fg_color=("#059669", "#065F46"),
                )
            else:
                self.replay_api_badge.configure(
                    text=t("settings.replay_api_status_disabled"),
                    text_color=("#D97706", "#F59E0B"),
                    fg_color=("#FEF3C7", "#78350F"),
                )
                self.replay_api_btn.configure(
                    state="normal",
                    text=t("settings.enable_replay_api_btn"),
                    fg_color=("#0284C7", "#0284C7"),
                )
        except Exception:
            pass

    def _on_enable_replay_api_clicked(self):
        """1-Click Replay API Auto-Configuration handler."""
        try:
            from app.replay_config import enable_replay_api

            success, msg = enable_replay_api()
            self._refresh_replay_api_ui_status()
            if success:
                self.log_message(f"[SYSTEM] {msg}")
                messagebox.showinfo(t("settings.replay_api_success_title"), msg)
            else:
                self.log_message(f"[ERROR] {msg}")
                messagebox.showerror(t("tabs.settings"), msg)
        except Exception as exc:
            err_msg = f"Failed to configure Replay API: {exc}"
            self.log_message(f"[ERROR] {err_msg}")
            messagebox.showerror(t("tabs.settings"), err_msg)

    def _is_league_game_foreground(self) -> bool:
        """
        Checks if the active foreground window is strictly the game process
        'League of Legends.exe' (or our HUD overlay when interacting).
        Returns False for launcher/client, Discord, Chrome, desktop, etc.
        """
        if not sys.platform.startswith("win"):
            return True
        try:
            import ctypes

            u32 = ctypes.windll.user32
            hwnd = u32.GetForegroundWindow()
            if not hwnd:
                return False

            # If user is directly interacting with or focusing the HUD overlay or hit-windows, keep it visible!
            if getattr(self, "hud", None):
                hud_hwnds = (
                    self.hud.get_all_hwnds()
                    if hasattr(self.hud, "get_all_hwnds")
                    else {getattr(self.hud, "get_hwnd", lambda: 0)()}
                )
                if hwnd in hud_hwnds and hwnd != 0:
                    return True

            fg_pid = ctypes.c_ulong()
            u32.GetWindowThreadProcessId(hwnd, ctypes.byref(fg_pid))
            target_pid = fg_pid.value
            if not target_pid or target_pid == os.getpid():
                return False

            # Check cached result for identical hwnd
            if hwnd == getattr(self, "_last_fg_hwnd", None):
                return getattr(self, "_last_fg_is_game", False)

            self._last_fg_hwnd = hwnd

            # 1. Fast process name check via psutil
            try:
                proc = psutil.Process(target_pid)
                name = (proc.name() or "").lower()
                # ONLY 'League of Legends.exe' (the in-game match or replay engine)
                if name == "league of legends.exe":
                    self._last_fg_is_game = True
                    return True

                # Reject launcher client, desktop, Discord, Chrome, etc.
                self._last_fg_is_game = False
                return False
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

            # 2. Kernel32 QueryFullProcessImageNameW check (bypasses elevation / token restrictions)
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            h_proc = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, target_pid
            )
            if h_proc:
                try:
                    img_buf = ctypes.create_unicode_buffer(512)
                    img_size = ctypes.c_ulong(512)
                    if ctypes.windll.kernel32.QueryFullProcessImageNameW(
                        h_proc, 0, img_buf, ctypes.byref(img_size)
                    ):
                        exe_path = img_buf.value.lower()
                        if (
                            "league of legends.exe" in exe_path
                            and "leagueclient" not in exe_path
                        ):
                            self._last_fg_is_game = True
                            return True
                        # Another application
                        self._last_fg_is_game = False
                        return False
                finally:
                    ctypes.windll.kernel32.CloseHandle(h_proc)

            # 3. Window title fallback
            length = u32.GetWindowTextLengthW(hwnd)
            if length > 0:
                title_buf = ctypes.create_unicode_buffer(length + 1)
                u32.GetWindowTextW(hwnd, title_buf, length + 1)
                title = title_buf.value
                if "League of Legends (TM) Client" in title:
                    self._last_fg_is_game = True
                    return True

            self._last_fg_is_game = False
            return False
        except Exception:
            return False

    def _sync_hud_visibility_and_state(self, mic_l=0.0, app_l=0.0, is_discord_muted=False):
        """
        ONLY shows the HUD widget when actively RECORDING a live match or REPLAYING.
        Hides in EVERYTHING else (WATCHING, client lobby, Discord, desktop, Chrome, etc.).
        """
        if not getattr(self, "hud", None):
            return

        # Check if HUD overlay is disabled in Settings
        if not self.config.get("hud_overlay_enabled", True):
            if self.hud.is_visible:
                self.hud.hide()
            return

        # If in preview mode from Settings, keep visible
        if getattr(self, "_is_hud_previewing", False):
            if not self.hud.is_visible:
                self.hud.show()
            return

        rec_w = getattr(self, "recorder_worker", None)
        rep_w = getattr(self, "replay_worker", None)

        rec_status = (
            self.last_rec_status[0]
            if getattr(self, "last_rec_status", None)
            else "IDLE"
        )
        rep_status = (
            self.last_replay_status[0]
            if getattr(self, "last_replay_status", None)
            else "IDLE"
        )

        is_rep_active = rep_w is not None
        is_rec_active = rec_w is not None

        # 1. If neither match recording nor replay is running -> Hide HUD
        if not (is_rec_active or is_rep_active):
            if self.hud.is_visible:
                self.hud.hide()
            return

        # 2. STRICT LEAGUE OF LEGENDS.EXE FOREGROUND CHECK:
        # The HUD overlay must ONLY be displayed when in League of Legends.exe (Loading Screen or Game).
        # It must be 100% hidden in ChampSelect, client launcher, Discord, Chrome, and desktop.
        # It also hides immediately when Alt-Tabbing away from League of Legends.exe.
        if not self._is_league_game_foreground():
            if self.hud.is_visible:
                self.hud.hide()
            return

        # 3. Inside League of Legends.exe -> Show HUD in appropriate mode
        target_mode = "REPLAY" if (is_rep_active and not is_rec_active) else "RECORD"
        if is_rep_active and is_rec_active:
            if rep_status in ("SYNCING", "PAUSED"):
                target_mode = "REPLAY"
            else:
                target_mode = "RECORD"

        if self.hud.mode != target_mode:
            self.hud.set_mode(target_mode)

        if not self.hud.is_visible:
            self.hud.show()

        if target_mode == "RECORD":
            try:
                game_time = float(getattr(rec_w, "last_game_time", 0.0) or 0.0)
            except (TypeError, ValueError):
                game_time = 0.0
            time_str = (
                f"{int(game_time // 60):02d}:{int(game_time % 60):02d}"
                if game_time > 0
                else "00:00"
            )
            is_p = (rec_status == "PAUSED") or (
                getattr(rec_w, "is_paused", False) if rec_w else False
            )
            self.hud.update_recorder_state(
                time_str=time_str,
                mic_level=mic_l,
                discord_level=app_l,
                is_paused=bool(is_p),
                is_muted=is_discord_muted,
            )
        else:
            if rep_w:
                try:
                    game_time = float(getattr(rep_w, "last_game_time", 0.0) or 0.0)
                except (TypeError, ValueError):
                    game_time = 0.0
                time_str = (
                    f"{int(game_time // 60):02d}:{int(game_time % 60):02d}"
                    if game_time > 0
                    else "00:00"
                )
                is_p = (rep_status == "PAUSED") or getattr(rep_w, "is_paused", False)
                audio_l = rep_w.get_audio_level() if hasattr(rep_w, "get_audio_level") else 0.0
                try:
                    duration = float(getattr(rep_w, "duration", 1800.0) or 1800.0)
                except (TypeError, ValueError):
                    duration = 1800.0
                try:
                    speed = float(getattr(rep_w, "speed", 1.0) or 1.0)
                except (TypeError, ValueError):
                    speed = 1.0
                try:
                    audio_l = float(audio_l or 0.0)
                except (TypeError, ValueError):
                    audio_l = 0.0
                self.hud.update_replay_state(
                    time_str=time_str,
                    current_time=game_time,
                    total_duration=duration,
                    volume=int(self.config.get("replay_volume", 80)),
                    is_paused=bool(is_p),
                    is_muted=getattr(self, "_replay_muted", False),
                    speed=speed,
                    audio_level=audio_l,
                )

    # ==========================================
    # I18N DYNAMIC TEXT REFRESH
    # ==========================================
    def _toggle_theme(self):
        new_theme = "light" if self.current_theme == "dark" else "dark"
        self._set_theme(new_theme)

    def _on_theme_selected(self, chosen_display):
        if hasattr(self, "theme_options"):
            new_theme = self.theme_options.get(chosen_display, "dark")
            self._set_theme(new_theme)

    def _set_theme(self, theme_code):
        self.current_theme = theme_code
        ctk.set_appearance_mode(theme_code)
        self.config["theme"] = theme_code
        save_config(self.config)
        if hasattr(self, "theme_toggle_btn"):
            self.theme_toggle_btn.configure(
                text="☀️" if theme_code == "light" else "🌙",
                text_color=("#D97706", "#FBBF24"),
            )
        if hasattr(self, "theme_menu") and hasattr(self, "theme_code_to_display"):
            disp = self.theme_code_to_display.get(theme_code)
            if disp and self.theme_menu.get() != disp:
                self.theme_menu.set(disp)

    def _on_language_selected(self, selected_name):
        code = self.lang_name_to_code.get(selected_name, "en")
        if code != get_language():
            self.config["language"] = code
            set_language(code)
            save_config(self.config)

    def _update_ui_texts(self):
        self.title(f"{t('app.window_title')} v{__version__}")
        if hasattr(self, "title_lbl"):
            self.title_lbl.configure(text=t("app.title"))
            self.sub_lbl.configure(text=t("app.subtitle"))
            self.refresh_health_btn.configure(text=t("health.refresh"))

            self._apply_health_badges()

            self.rec_header_lbl.configure(text=t("record.title"))
            if self.recorder_worker is not None:
                self.rec_btn.configure(text=t("record.stop_btn"))
            else:
                self.rec_btn.configure(text=t("record.start_btn"))
            self.rec_scene_lbl.configure(
                text=t("record.target_scene", scene=self.config.get("scene_name", "Discord Only"))
            )
            self.rec_info_lbl.configure(text=t("record.info"))
            if hasattr(self, "target_prog_lbl"):
                self.target_prog_lbl.configure(text=t("record.target_app"))
                self.app_meter_title.configure(text="🔊 " + t("record.app_sound") + ":")
                self._last_mic_muted_state = None
                self.mic_meter_title.configure(text="🎤 " + t("record.microphone") + ":", text_color="#94A3B8")
            if hasattr(self, "mic_input_lbl"):
                self.mic_input_lbl.configure(text=t("record.mic_input"))
            self._render_rec_status(*self.last_rec_status)

            self.rep_header_lbl.configure(text=t("replay.title"))
            if self.replay_worker is not None:
                self.rep_btn.configure(text=t("replay.stop_btn"))
            else:
                self.rep_btn.configure(text=t("replay.start_btn"))
            self.rep_info_lbl.configure(text=f"{t('replay.info')}\n• {t('replay.hotkey_hint')}")
            self._update_volume_display()
            self._render_replay_status(*self.last_replay_status)

            # Tabs
            if hasattr(self, "tabview"):
                self.tabview._segmented_button._buttons_dict["log"].configure(text=t("tabs.log"))
                self.tabview._segmented_button._buttons_dict["recordings"].configure(
                    text=t("tabs.recordings")
                )
                self.tabview._segmented_button._buttons_dict["settings"].configure(
                    text=t("tabs.settings")
                )

            # Log tab
            if hasattr(self, "autoscroll_cb"):
                self.autoscroll_cb.configure(text=t("log.autoscroll"))
                self.copy_btn.configure(text=t("log.copy"))
                self.clear_btn.configure(text=t("log.clear"))

            # Recordings tab
            if hasattr(self, "open_folder_btn"):
                self.open_folder_btn.configure(text=t("recordings.open_folder"))
                self.refresh_rec_btn.configure(text=t("recordings.refresh"))
                if hasattr(self, "sync_remote_btn"):
                    self.sync_remote_btn.configure(text=t("recordings.sync_remote"))
                self._refresh_recordings_list()

            # Settings tab
            if hasattr(self, "rec_scene_lbl"):
                self.rec_scene_lbl.configure(text=self._get_engine_display_text())

            if hasattr(self, "dir_title_lbl"):
                self.dir_title_lbl.configure(text=f"📁 {t('settings.paths_title')}")
                self.rec_folder_lbl.configure(text=t("settings.rec_folder"))
                self.browse_rec_btn.configure(text=t("settings.browse"))
                self.vlc_dir_lbl.configure(text=t("settings.vlc_dir"))
                self.browse_vlc_btn.configure(text=t("settings.browse"))
                self.replay_vol_lbl.configure(text=t("settings.replay_volume"))
                if hasattr(self, "interface_title_lbl"):
                    self.interface_title_lbl.configure(text=f"🌐 {t('settings.interface_title')}")
                self.lang_lbl.configure(text=t("settings.language"))
                if hasattr(self, "theme_lbl"):
                    self.theme_lbl.configure(text=t("settings.theme"))
                    self.theme_options = {
                        t("settings.theme_dark"): "dark",
                        t("settings.theme_light"): "light",
                        t("settings.theme_system"): "system",
                    }
                    self.theme_code_to_display = {v: k for k, v in self.theme_options.items()}
                    self.theme_menu.configure(values=list(self.theme_options.keys()))
                    curr_theme_disp = self.theme_code_to_display.get(self.current_theme, t("settings.theme_dark"))
                    if self.theme_menu.get() != curr_theme_disp:
                        self.theme_menu.set(curr_theme_disp)
                if hasattr(self, "settings_mic_lbl"):
                    self.settings_mic_lbl.configure(text=t("settings.mic_device"))
                if hasattr(self, "automation_title_lbl"):
                    self.automation_title_lbl.configure(text=f"⚡ {t('settings.automation_title')}")
                if hasattr(self, "auto_champ_select_lbl"):
                    self.auto_champ_select_lbl.configure(text=t("settings.auto_start_champ_select"))
                    self.auto_champ_select_switch.configure(text=t("settings.auto_start_champ_select_info"))
                if hasattr(self, "auto_replay_lbl"):
                    self.auto_replay_lbl.configure(text=t("settings.auto_start_replay"))
                    self.auto_replay_switch.configure(text=t("settings.auto_start_replay_info"))
                if hasattr(self, "minimize_to_tray_lbl"):
                    self.minimize_to_tray_lbl.configure(text=t("settings.minimize_to_tray"))
                    self.minimize_to_tray_switch.configure(text=t("settings.minimize_to_tray_info"))
                if hasattr(self, "replay_api_btn"):
                    self.replay_api_btn.configure(text=t("settings.enable_replay_api_btn"))
                if hasattr(self, "hud_title_lbl"):
                    self.hud_title_lbl.configure(text=f"🎮 {t('settings.hud_section_title')}")
                    self.hud_enable_lbl.configure(text=t("settings.hud_overlay_enabled"))
                    self.hud_enable_switch.configure(text=t("settings.hud_overlay_info"))
                    if hasattr(self, "hud_lock_lbl"):
                        self.hud_lock_lbl.configure(text=t("settings.hud_locked"))
                        self.hud_lock_switch.configure(text=t("settings.hud_locked_info"))
                    elif hasattr(self, "hud_click_through_lbl"):
                        self.hud_click_through_lbl.configure(text=t("settings.hud_locked"))
                        self.hud_click_through_switch.configure(text=t("settings.hud_locked_info"))
                    self.hud_opacity_lbl.configure(
                        text=t("settings.hud_opacity", percent=int(self.hud_opacity_slider.get() * 100))
                    )
                    self.hud_preview_rec_btn.configure(text=t("settings.hud_preview_rec_btn"))
                    self.hud_preview_replay_btn.configure(text=t("settings.hud_preview_replay_btn"))
                    self.hud_hide_preview_btn.configure(text=t("settings.hud_hide_preview_btn"))
                    self.hud_reset_pos_btn.configure(text=t("settings.hud_reset_pos_btn"))
                if hasattr(self, "cloud_title_lbl"):
                    self.cloud_title_lbl.configure(text=f"☁️ {t('settings.cloud_title')}")
                    self.cloud_url_lbl.configure(text=t("settings.cloud_url"))
                    self.cloud_key_lbl.configure(text=t("settings.cloud_api_key"))
                    self.cloud_test_btn.configure(text=t("settings.cloud_test_btn"))
                if hasattr(self, "save_btn") and self.save_btn:
                    self.save_btn.configure(text=t("settings.save_btn"))
                if hasattr(self, "updates_title_lbl"):
                    self.updates_title_lbl.configure(text=f"🔄 {t('settings.updates_title')}")
                if hasattr(self, "app_version_lbl"):
                    self.app_version_lbl.configure(text=t("settings.app_version"))
                if hasattr(self, "check_update_btn"):
                    self.check_update_btn.configure(text=t("settings.check_updates_btn"))

                curr_code = get_language()
                curr_name = self.lang_code_to_name.get(curr_code, curr_code.capitalize())
                if self.lang_menu.get() != curr_name:
                    self.lang_menu.set(curr_name)

    # ==========================================
    # LOGGING & QUEUE HANDLING
    # ==========================================
    def log_message(self, message):
        """Thread-safe log submission."""
        now = datetime.datetime.now().strftime("%H:%M:%S")
        self.msg_queue.put(("LOG", f"[{now}] {message}"))

    _log_safe = log_message

    def set_rec_status(self, status, detail=""):
        """Thread-safe status update for recorder."""
        self.msg_queue.put(("STATUS_REC", status, detail))

    def set_replay_status(self, status, detail=""):
        """Thread-safe status update for replay."""
        self.msg_queue.put(("STATUS_REPLAY", status, detail))

    def set_replay_mute_state(self, is_muted):
        """Thread-safe mute update for replay."""
        self.msg_queue.put(("REPLAY_MUTE", is_muted))

    def _process_queue(self):
        """Processes pending UI updates on the main thread."""
        if getattr(self, "_is_destroyed", False):
            return

        try:
            while not self.msg_queue.empty():
                item = self.msg_queue.get_nowait()
                action = item[0]

                if action == "LOG":
                    text = item[1]
                    self.log_textbox.configure(state="normal")
                    self.log_textbox.insert("end", text + "\n")
                    if self.autoscroll_var.get():
                        self.log_textbox.see("end")
                    self.log_textbox.configure(state="disabled")

                elif action == "STATUS_REC":
                    status, detail = item[1], item[2]
                    self._render_rec_status(status, detail)
                    if getattr(self, "hud", None) and self.config.get("hud_overlay_enabled", True):
                        if status in ("SAVED", "STOPPED", "IDLE") and not self._is_hud_previewing:
                            self.hud.hide()
                        elif status in ("RECORDING", "PAUSED", "WATCHING"):
                            self._sync_hud_visibility_and_state()

                elif action == "STATUS_REPLAY":
                    status, detail = item[1], item[2]
                    self._render_replay_status(status, detail)
                    if getattr(self, "hud", None) and self.config.get("hud_overlay_enabled", True):
                        if status in ("STOPPED", "IDLE") and not self._is_hud_previewing:
                            self.hud.hide()
                        elif status in ("SYNCING", "PAUSED", "WATCHING"):
                            self._sync_hud_visibility_and_state()

                elif action == "REPLAY_MUTE":
                    self._replay_muted = bool(item[1])
                    self._update_volume_display()
                    if getattr(self, "hud", None):
                        self.hud.is_muted = self._replay_muted
                        self.hud.render()

                elif action == "AUTO_START_REC":
                    if not self.recorder_worker:
                        self.log_message(t("logs.champ_select_detected"))
                        self._start_recording()

                elif action == "AUTO_START_REPLAY":
                    if not self.replay_worker:
                        self.log_message(t("logs.replay_detected_autostart"))
                        self._start_replay()

                elif action == "UPDATE_CHECK_RESULT":
                    res = item[1]
                    manual = item[2] if len(item) > 2 else False
                    self._handle_update_result(res, manual=manual)

                elif action == "UPDATE_DOWNLOAD_PROGRESS":
                    pct = item[1]
                    # Drain intermediate progress updates in queue to prevent Tkinter redraw lag
                    while not self.msg_queue.empty():
                        try:
                            next_item = self.msg_queue.queue[0]
                            if next_item[0] == "UPDATE_DOWNLOAD_PROGRESS":
                                pct = self.msg_queue.get_nowait()[1]
                            else:
                                break
                        except Exception:
                            break
                    if hasattr(self, "update_action_btn"):
                        self.update_action_btn.configure(
                            text=t("settings.downloading_update", percent=pct)
                        )

                elif action == "UPDATE_DOWNLOAD_COMPLETE":
                    target_path = item[1]
                    if hasattr(self, "update_action_btn"):
                        self.update_action_btn.configure(
                            text=t("settings.restarting")
                        )
                    # If in dev/script mode and download_and_install_update returned a file, launch it and exit
                    if target_path and os.path.exists(str(target_path)):
                        try:
                            p = str(target_path)
                            if p.lower().endswith(".exe"):
                                os.startfile(p)
                            elif os.path.isdir(p):
                                exe_cand = os.path.join(p, "LeagueVoiceRecorder.exe")
                                if os.path.exists(exe_cand):
                                    os.startfile(exe_cand)
                                else:
                                    os.startfile(p)
                            self.after(1200, lambda: os._exit(0))
                        except Exception:
                            pass

        except Exception:
            pass

        if not getattr(self, "_is_destroyed", False):
            try:
                self._queue_timer = self.after(50, self._process_queue)
            except Exception:
                pass

    def _render_rec_status(self, status, detail):
        prev_status = getattr(self, "last_rec_status", (None, None))[0]
        self.last_rec_status = (status, detail)
        self._update_tray_status()
        if status == "RECORDING":
            text = (
                t("record.status_recording", detail=detail)
                if detail
                else t("record.status_recording", detail=t("record.recording_active"))
            )
            self.rec_status_pill.configure(
                text=text,
                text_color=("#BE123C", "#F43F5E"),
                fg_color=("#FFE4E6", "#3B0713"),
            )
        elif status == "PAUSED":
            text = (
                t("record.status_paused", detail=detail)
                if detail
                else t("record.status_paused", detail=t("record.paused_time", time=""))
            )
            self.rec_status_pill.configure(
                text=text,
                text_color=("#B45309", "#FBBF24"),
                fg_color=("#FEF3C7", "#451A03"),
            )
        elif status == "WATCHING":
            text = (
                t("record.status_listening", detail=detail)
                if detail
                else t("record.status_listening", detail=t("record.waiting_for_match"))
            )
            self.rec_status_pill.configure(
                text=text,
                text_color=("#0D9488", "#0AC8B9"),
                fg_color=("#CCFBF1", "#042F2E"),
            )
        elif status in ("SAVING", "CONVERTING"):
            text = (
                t("record.status_saving", detail=detail)
                if detail
                else t("record.status_saving", detail=t("record.saving_audio"))
            )
            self.rec_status_pill.configure(
                text=text,
                text_color=("#7E22CE", "#A855F7"),
                fg_color=("#F3E8FF", "#3B0764"),
            )
        elif status == "SAVED":
            self.rec_status_pill.configure(
                text=f"● SAVED: {detail}" if detail else "● SAVED",
                text_color=("#15803D", "#22C55E"),
                fg_color=("#DCFCE7", "#052E16"),
            )
            self._reset_rec_button_state()
            self._refresh_recordings_list()
        elif status in ("STOPPED", "IDLE"):
            if prev_status != "SAVED":
                self.rec_status_pill.configure(
                    text=t("record.status_stopped", detail=detail).strip()
                    if detail
                    else t("record.status_idle"),
                    text_color=("#64748B", "#94A3B8"),
                    fg_color=("#F1F5F9", "#070D1E"),
                )
            self._reset_rec_button_state()
            self._refresh_recordings_list()
        elif status == "ERROR":
            self.rec_status_pill.configure(
                text=t("record.status_error", detail=detail),
                text_color=("#B91C1C", "#EF4444"),
                fg_color=("#FEE2E2", "#450A0A"),
            )

    def _render_replay_status(self, status, detail):
        self.last_replay_status = (status, detail)
        self._update_tray_status()
        if status == "SYNCING":
            text = (
                t("replay.status_syncing", detail=detail)
                if detail
                else t("replay.status_syncing", detail=t("replay.syncing_active"))
            )
            self.rep_status_pill.configure(
                text=text,
                text_color=("#0D9488", "#0AC8B9"),
                fg_color=("#CCFBF1", "#042F2E"),
            )
        elif status == "PAUSED":
            text = (
                t("replay.status_paused", detail=detail)
                if detail
                else t("replay.status_paused", detail="")
            )
            self.rep_status_pill.configure(
                text=text,
                text_color=("#B45309", "#FBBF24"),
                fg_color=("#FEF3C7", "#451A03"),
            )
        elif status == "WATCHING":
            text = (
                t("replay.status_watching", detail=detail)
                if detail
                else t("replay.status_watching", detail=t("replay.waiting_for_replay"))
            )
            self.rep_status_pill.configure(
                text=text,
                text_color=("#0284C7", "#38BDF8"),
                fg_color=("#E0F2FE", "#082F49"),
            )
        elif status in ("STOPPED", "IDLE"):
            self.rep_status_pill.configure(
                text=t("replay.status_stopped", detail=detail).strip()
                if detail
                else t("replay.status_idle"),
                text_color=("#64748B", "#94A3B8"),
                fg_color=("#F1F5F9", "#070D1E"),
            )
            self._reset_replay_button_state()
        elif status == "ERROR":
            self.rep_status_pill.configure(
                text=t("replay.status_error", detail=detail),
                text_color=("#B91C1C", "#EF4444"),
                fg_color=("#FEE2E2", "#450A0A"),
            )

    # ==========================================
    # RECORDER CONTROL
    # ==========================================
    def _toggle_recording(self):
        if self.recorder_worker is None:
            self._start_recording()
        else:
            self._stop_recording()

    def _start_recording(self):
        # Cleanly stop any idle preview stream to ensure single-stream audio safety
        if hasattr(self, "_preview_native_recorder") and self._preview_native_recorder:
            try:
                self._preview_native_recorder.close()
            except Exception:
                pass
            self._preview_native_recorder = None

        self.log_message(t("logs.ui_rec_start"))
        self.rec_btn.configure(
            text=t("record.stop_btn"),
            fg_color="#475569",
            hover_color="#334155",
        )
        self.rec_status_pill.configure(
            text=t("record.status_initializing"),
            text_color=("#0284C7", "#38BDF8"),
            fg_color=("#E0F2FE", "#0C4A6E"),
        )

        if hasattr(self, "_preview_native_recorder") and self._preview_native_recorder:
            try:
                self._preview_native_recorder.close()
            except Exception:
                pass
            self._preview_native_recorder = None

        self.recorder_worker = LeagueRecorder(
            config=self.config,
            log_callback=self.log_message,
            status_callback=self.set_rec_status,
        )

        self.recorder_thread = threading.Thread(
            target=self.recorder_worker.run_loop,
            daemon=True,
        )
        self.recorder_thread.start()

        if getattr(self, "hud", None) and self.config.get("hud_overlay_enabled", True):
            self.hud.set_mode("RECORD")
            self.hud.update_recorder_state(
                time_str="00:00",
                mic_level=0.0,
                discord_level=0.0,
            )
            if self._is_league_game_foreground():
                self.hud.show()
            else:
                self.hud.hide()

    def _stop_recording(self):
        if getattr(self, "hud", None) and not getattr(self, "_is_hud_previewing", False):
            self.hud.hide()
        if self.recorder_worker:
            self.log_message(t("logs.ui_rec_stop"))
            self.rec_btn.configure(state="disabled", text=t("record.stopping_btn"))
            threading.Thread(target=self._stop_recorder_worker_async, daemon=True).start()

    def _stop_recorder_worker_async(self):
        if self.recorder_worker:
            self.recorder_worker.stop()
            if self.recorder_thread:
                self.recorder_thread.join(timeout=5)
            self.recorder_worker = None
            self.recorder_thread = None
        self.msg_queue.put(("STATUS_REC", "STOPPED", ""))

    def _reset_rec_button_state(self):
        self.rec_btn.configure(
            state="normal",
            text=t("record.start_btn"),
            fg_color="#E11D48",
            hover_color="#BE123C",
        )
        self.recorder_worker = None
        self.recorder_thread = None

    def _start_auto_detect_monitor(self):
        self._auto_detect_thread = threading.Thread(
            target=self._auto_detect_monitor_loop,
            daemon=True,
            name="AutoDetectMonitor",
        )
        self._auto_detect_thread.start()

    def _auto_detect_monitor_loop(self):
        """Continuously monitors LCU gameflow and running replay processes to auto-start watchers."""
        import time
        from app.record import get_lcu_gameflow_phase
        from app.play_replay import detect_running_rofl_process

        while not getattr(self, "_is_destroyed", False):
            # 1. Champion Select & Active Match Live Match Watcher Auto-Start
            if self.config.get("auto_start_champ_select", True) and not self.recorder_worker:
                try:
                    phase = get_lcu_gameflow_phase()
                    if phase in ("ChampSelect", "InProgress"):
                        if getattr(self, "_last_auto_started_phase", None) != phase:
                            self._last_auto_started_phase = phase
                            self.msg_queue.put(("AUTO_START_REC",))
                    elif phase in ("None", "Lobby", "EndOfGame", "WaitingForStats", None):
                        self._last_auto_started_phase = None
                except Exception:
                    pass

                # Fallback: If League of Legends.exe process is already running a live match
                if not self.recorder_worker and not self.replay_worker and not getattr(self, "_auto_started_match", False):
                    try:
                        for proc in psutil.process_iter(["pid", "name"]):
                            if (proc.info.get("name") or "").lower() == "league of legends.exe":
                                rofl, _ = detect_running_rofl_process()
                                if not rofl:
                                    self._auto_started_match = True
                                    self.msg_queue.put(("AUTO_START_REC",))
                                    break
                    except Exception:
                        pass

            # 2. Replay Match Auto-Sync Auto-Start
            if self.config.get("auto_start_replay", True) and not self.replay_worker:
                try:
                    rofl_path, _ = detect_running_rofl_process()
                    if rofl_path:
                        if getattr(self, "_last_auto_started_rofl", None) != rofl_path:
                            self._last_auto_started_rofl = rofl_path
                            self.msg_queue.put(("AUTO_START_REPLAY",))
                    else:
                        self._last_auto_started_rofl = None
                except Exception:
                    pass

            time.sleep(1.2)

    # ==========================================
    # REPLAY CONTROL
    # ==========================================
    def _toggle_replay(self):
        if self.replay_worker is None:
            self._start_replay()
        else:
            self._stop_replay()

    def _start_replay(self):
        self.log_message(t("logs.ui_replay_start"))
        self.rep_btn.configure(
            text=t("replay.stop_btn"),
            fg_color="#475569",
            hover_color="#334155",
        )
        self.rep_status_pill.configure(
            text=t("replay.status_initializing"),
            text_color=("#0284C7", "#38BDF8"),
            fg_color=("#E0F2FE", "#0C4A6E"),
        )

        self.replay_worker = ReplaySynchronizer(
            config=self.config,
            log_callback=self.log_message,
            status_callback=self.set_replay_status,
            mute_callback=self.set_replay_mute_state,
        )
        if getattr(self, "_replay_muted", False):
            self.replay_worker.set_mute(True)
        else:
            self.replay_worker.set_volume(int(self.vol_slider.get()))

        self.replay_thread = threading.Thread(
            target=self.replay_worker.run_loop,
            daemon=True,
        )
        self.replay_thread.start()

        if getattr(self, "hud", None) and self.config.get("hud_overlay_enabled", True):
            self.hud.set_mode("REPLAY")
            self.hud.update_replay_state(
                time_str="00:00",
                current_time=0.0,
                total_duration=1800.0,
                volume=int(self.vol_slider.get()),
                is_paused=False,
                is_muted=bool(getattr(self, "_replay_muted", False)),
                speed=1.0,
                audio_level=0.0,
            )
            if self._is_league_game_foreground():
                self.hud.show()
            else:
                self.hud.hide()

    def _update_volume_display(self, current_vol=None):
        vol = int(current_vol if current_vol is not None else self.vol_slider.get())
        if getattr(self, "_replay_muted", False):
            self.vol_label.configure(
                text=f"🔇 {t('replay.muted_label')}",
                text_color=("#DC2626", "#EF4444"),
            )
        else:
            if vol == 0:
                icon = "🔇"
            elif vol < 35:
                icon = "🔈"
            elif vol < 70:
                icon = "🔉"
            else:
                icon = "🔊"
            self.vol_label.configure(
                text=f"{icon} {vol}%",
                text_color=("#0284C7", "#38BDF8"),
            )

    def _on_key_m(self, event=None):
        focus_widget = self.focus_get()
        if focus_widget:
            from tkinter import Entry, Text

            if isinstance(focus_widget, (Entry, Text)):
                return
            if hasattr(focus_widget, "_entry") or "entry" in str(type(focus_widget)).lower():
                return
        self._toggle_replay_mute()

    def _toggle_replay_mute(self):
        if self.replay_worker:
            self.replay_worker.toggle_mute()
        else:
            self._replay_muted = not getattr(self, "_replay_muted", False)
            self._update_volume_display()

    def _on_replay_volume_slider_changed(self, value):
        vol = int(value)
        if getattr(self, "_replay_muted", False):
            self._replay_muted = False
            if self.replay_worker:
                self.replay_worker.set_mute(False)

        self._update_volume_display(current_vol=vol)

        if self.replay_worker:
            self.replay_worker.set_volume(vol)

        self.config["replay_volume"] = vol
        if hasattr(self, "replay_vol_entry"):
            try:
                self.replay_vol_entry.delete(0, "end")
                self.replay_vol_entry.insert(0, str(vol))
            except Exception:
                pass
        self._debounced_save_volume()

    def _debounced_save_volume(self):
        if hasattr(self, "_save_vol_timer") and self._save_vol_timer is not None:
            self.after_cancel(self._save_vol_timer)
        self._save_vol_timer = self.after(800, self._save_volume_to_config)

    def _save_volume_to_config(self):
        save_config(self.config)
        self._save_vol_timer = None

    def _stop_replay(self):
        if getattr(self, "hud", None) and not getattr(self, "_is_hud_previewing", False):
            self.hud.hide()
        if self.replay_worker:
            self.log_message(t("logs.ui_replay_stop"))
            self.rep_btn.configure(state="disabled", text=t("replay.stopping_btn"))
            threading.Thread(target=self._stop_replay_worker_async, daemon=True).start()

    def _stop_replay_worker_async(self):
        if self.replay_worker:
            self.replay_worker.stop()
            if self.replay_thread:
                self.replay_thread.join(timeout=3)
            self.replay_worker = None
            self.replay_thread = None
        self.msg_queue.put(("STATUS_REPLAY", "STOPPED", ""))

    def _reset_replay_button_state(self):
        self.rep_btn.configure(
            state="normal",
            text=t("replay.start_btn"),
            fg_color="#0284C7",
            hover_color="#0369A1",
        )
        self.replay_worker = None
        self.replay_thread = None

    # ==========================================
    # SYSTEM HEALTH CHECK
    # ==========================================
    def _check_system_health(self):
        threading.Thread(target=self._run_health_checks_worker, daemon=True).start()

    def _run_health_checks_worker(self):
        # 1. Check Discord Process & Audio
        discord_ok = False
        try:
            from app.native_audio import find_discord_active_pid
            if find_discord_active_pid() is not None:
                discord_ok = True
            else:
                for proc in psutil.process_iter(["name"]):
                    if (proc.info.get("name") or "").lower() == "discord.exe":
                        discord_ok = True
                        break
        except Exception:
            discord_ok = False

        # 2. Check League of Legends Client
        lol_ok = False
        try:
            for proc in psutil.process_iter(["name"]):
                name = proc.info.get("name") or ""
                if "LeagueClient" in name:
                    lol_ok = True
                    break
        except Exception:
            pass

        # 3. Check VLC
        vlc_ok = os.path.exists(self.config.get("vlc_dir", r"C:\Program Files\VideoLAN\VLC"))

        # 4. Check FFmpeg
        ffmpeg_ok = False
        try:
            ffmpeg_path = get_ffmpeg_path()
            res = subprocess.run(
                [ffmpeg_path, "-version"],
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            ffmpeg_ok = res.returncode == 0
        except Exception:
            ffmpeg_ok = False

        # Store state and apply to UI badges safely via main thread
        self.health_state = (discord_ok, lol_ok, vlc_ok, ffmpeg_ok)
        if not getattr(self, "_is_destroyed", False):
            try:
                self.after(0, self._apply_health_badges)
            except Exception:
                pass

    def _apply_health_badges(self):
        if getattr(self, "_is_destroyed", False):
            return
        discord_ok, lol_ok, vlc_ok, ffmpeg_ok = self.health_state
        discord_prefix = t("health.discord")
        lol_prefix = t("health.league")
        vlc_prefix = t("health.vlc")
        ffmpeg_prefix = t("health.ffmpeg")

        ok_color = ("#16A34A", "#4ADE80")
        offline_color = ("#64748B", "#94A3B8")
        checking_color = ("#64748B", "#CBD5E1")
        error_color = ("#DC2626", "#F87171")

        if discord_ok is True:
            self.discord_badge.configure(
                text=f"{discord_prefix} {t('health.ready')}", text_color=ok_color
            )
        elif discord_ok is False:
            self.discord_badge.configure(
                text=f"{discord_prefix} {t('health.client_offline')}", text_color=offline_color
            )
        else:
            self.discord_badge.configure(
                text=f"{discord_prefix} {t('health.checking')}", text_color=checking_color
            )

        if lol_ok is True:
            self.lol_badge.configure(
                text=f"{lol_prefix} {t('health.running')}", text_color=ok_color
            )
        elif lol_ok is False:
            self.lol_badge.configure(
                text=f"{lol_prefix} {t('health.client_offline')}", text_color=offline_color
            )
        else:
            self.lol_badge.configure(
                text=f"{lol_prefix} {t('health.checking')}", text_color=checking_color
            )

        if vlc_ok is True:
            self.vlc_badge.configure(
                text=f"{vlc_prefix} {t('health.ready')}", text_color=ok_color
            )
        elif vlc_ok is False:
            self.vlc_badge.configure(
                text=f"{vlc_prefix} {t('health.not_found')}", text_color=error_color
            )
        else:
            self.vlc_badge.configure(
                text=f"{vlc_prefix} {t('health.checking')}", text_color=checking_color
            )

        if ffmpeg_ok is True:
            self.ffmpeg_badge.configure(
                text=f"{ffmpeg_prefix} {t('health.ready')}", text_color=ok_color
            )
        elif ffmpeg_ok is False:
            self.ffmpeg_badge.configure(
                text=f"{ffmpeg_prefix} {t('health.not_found')}", text_color=error_color
            )
        else:
            self.ffmpeg_badge.configure(
                text=f"{ffmpeg_prefix} {t('health.checking')}", text_color=checking_color
            )

        self._refresh_replay_api_ui_status()

    # ==========================================
    # SOFTWARE UPDATES (ONE-CLICK IN-PLACE UPDATER)
    # ==========================================
    def _check_update_background(self):
        """Silently checks GitHub Releases in the background."""
        def worker():
            from app.updater import check_for_update

            res = check_for_update(__version__)
            self.msg_queue.put(("UPDATE_CHECK_RESULT", res, False))

        threading.Thread(target=worker, daemon=True, name="UpdateCheckWorker").start()

    def _on_check_update_clicked(self):
        """User explicitly clicked 'Check for Updates' in Settings."""
        self.update_status_lbl.configure(
            text=t("settings.checking_updates"), text_color="#38BDF8"
        )
        self.check_update_btn.configure(state="disabled")

        def worker():
            from app.updater import check_for_update

            res = check_for_update(__version__)
            self.msg_queue.put(("UPDATE_CHECK_RESULT", res, True))

        threading.Thread(target=worker, daemon=True, name="ManualUpdateCheckWorker").start()

    def _handle_update_result(self, res, manual=False):
        """Processes update check result on GUI main thread."""
        if hasattr(self, "check_update_btn"):
            self.check_update_btn.configure(state="normal")

        if res.get("available"):
            self._pending_update = res
            ver = res.get("latest_version", "")

            # Show prominent header notification badge
            if hasattr(self, "update_header_badge"):
                self.update_header_badge.configure(text=f"🚀 Update: v{ver}")
                self.update_header_badge.pack(side="right", padx=(4, 6), pady=4)

            # Show update prompt in Settings tab
            if hasattr(self, "update_status_lbl"):
                self.update_status_lbl.configure(
                    text=t("settings.update_available", version=ver),
                    text_color="#10B981",
                )
            if hasattr(self, "update_action_btn"):
                self.update_action_btn.configure(
                    text=t("settings.update_now_btn", version=ver),
                    state="normal",
                )
                self.update_action_btn.grid(row=3, column=0, columnspan=2, padx=14, pady=(6, 12), sticky="ew")

        elif manual:
            if res.get("error"):
                if hasattr(self, "update_status_lbl"):
                    self.update_status_lbl.configure(
                        text=t("settings.update_failed", error=res["error"]),
                        text_color="#F87171",
                    )
            else:
                if hasattr(self, "update_status_lbl"):
                    self.update_status_lbl.configure(
                        text=t("settings.up_to_date", version=__version__),
                        text_color="#4ADE80",
                    )
            if hasattr(self, "update_action_btn"):
                self.update_action_btn.grid_forget()

    def _on_header_update_badge_clicked(self):
        """Clicking update notification badge in header opens Settings tab."""
        if hasattr(self, "tabview"):
            self.tabview.set("settings")

    def _on_perform_update_clicked(self):
        """Downloads update and performs seamless in-place replacement and restart."""
        update_info = getattr(self, "_pending_update", None)
        if not update_info or not update_info.get("download_url"):
            # Fallback: open release page in web browser if direct binary asset is missing
            if update_info and update_info.get("html_url"):
                import webbrowser

                webbrowser.open(update_info["html_url"])
            return

        self.update_action_btn.configure(
            state="disabled", text=t("settings.downloading_update", percent=0)
        )
        self.update_status_lbl.configure(text="")

        def worker():
            from app.updater import download_and_install_update

            def on_progress(pct):
                self.msg_queue.put(("UPDATE_DOWNLOAD_PROGRESS", pct))

            try:
                res = download_and_install_update(
                    update_info["download_url"], progress_callback=on_progress
                )
                self.msg_queue.put(("UPDATE_DOWNLOAD_COMPLETE", res))
            except Exception as e:
                self.msg_queue.put(
                    ("UPDATE_CHECK_RESULT", {"available": False, "error": str(e)}, True)
                )

        threading.Thread(target=worker, daemon=True, name="UpdateDownloadWorker").start()

    # ==========================================
    # RECORDINGS TAB FUNCTIONS
    # ==========================================
    def _open_recordings_folder(self):
        folder = self.config.get("record_folder") or get_current_user_documents_recordings_dir()
        os.makedirs(folder, exist_ok=True)
        if os.name == "nt":
            os.startfile(folder)
        else:
            subprocess.run(["xdg-open", folder])

    def _refresh_recordings_list(self):
        if getattr(self, "_in_test_suite", False):
            if hasattr(self, "rec_count_lbl"):
                self.rec_count_lbl.configure(text=t("recordings.count_label", count=0, size=0.0))
            return

        for widget in self.rec_scroll_frame.winfo_children():
            widget.destroy()

        folder = self.config.get("record_folder") or get_current_user_documents_recordings_dir()
        if not os.path.exists(folder):
            os.makedirs(folder, exist_ok=True)

        files = [f for f in os.listdir(folder) if f.lower().endswith(".mp3")]
        files.sort(key=lambda x: os.path.getmtime(os.path.join(folder, x)), reverse=True)

        total_size_bytes = sum(os.path.getsize(os.path.join(folder, f)) for f in files)
        total_size_mb = total_size_bytes / (1024 * 1024)
        self.rec_count_lbl.configure(
            text=t("recordings.count_label", count=len(files), size=total_size_mb)
        )

        local_game_ids = set()
        for f in files:
            if f.lower().endswith(".mp3"):
                local_game_ids.add(f[:-4])

        remote_items = getattr(self, "_remote_recordings", []) or []
        missing_remote_items = [
            it for it in remote_items
            if str(it.get("game_id", "")).strip() and str(it.get("game_id", "")).strip() not in local_game_ids
        ]

        if hasattr(self, "download_all_btn"):
            if missing_remote_items:
                self.download_all_btn.configure(
                    text=t("recordings.download_all", count=len(missing_remote_items)),
                    state="normal",
                )
                self.download_all_btn.grid(row=0, column=1, padx=4, sticky="e")
            else:
                self.download_all_btn.grid_remove()

        if not files and not missing_remote_items:
            empty_lbl = ctk.CTkLabel(
                self.rec_scroll_frame,
                text=t("recordings.empty", folder=folder),
                font=ctk.CTkFont(size=12, slant="italic"),
                text_color="#64748B",
            )
            empty_lbl.pack(pady=20)
            return

        is_admin = getattr(self, "_cloud_role", "") == "admin"
        row_idx = 0

        # 1. Render missing remote scrim recordings with prominent Download action
        for item in missing_remote_items:
            game_id = str(item.get("game_id")).strip()
            size_mb = (item.get("file_size") or 0) / (1024 * 1024)
            date_raw = str(item.get("created_at") or item.get("upload_timestamp") or "")
            date_str = date_raw[:10] if date_raw else "Recent"
            team = item.get("team_tag")

            row = ctk.CTkFrame(
                self.rec_scroll_frame,
                fg_color=("#EFF6FF", "#0A1733") if row_idx % 2 == 0 else ("#E0F2FE", "#0D1E42"),
                corner_radius=8,
                border_width=1,
                border_color=("#38BDF8", "#1E3A8A"),
            )
            row.pack(fill="x", padx=6, pady=4)
            row.grid_columnconfigure(0, weight=1)

            title_frame = ctk.CTkFrame(row, fg_color="transparent")
            title_frame.grid(row=0, column=0, padx=14, pady=(8, 2), sticky="w")

            name_lbl = ctk.CTkLabel(
                title_frame,
                text=f"☁️ {game_id}.mp3",
                font=ctk.CTkFont(size=13, weight="bold"),
                text_color=("#0284C7", "#38BDF8"),
            )
            name_lbl.pack(side="left")

            badge_text = f"[{t('recordings.status_remote')}]"
            if team:
                badge_text += f" • vs {team}"
            badge = ctk.CTkLabel(
                title_frame,
                text=badge_text,
                font=ctk.CTkFont(size=11, weight="bold"),
                text_color=("#0284C7", "#38BDF8"),
            )
            badge.pack(side="left", padx=8)

            remote_patch = item.get("patch") or item.get("game_patch") or ""
            date_display = f"{date_str}  |  Patch: {remote_patch}" if remote_patch else date_str
            meta_lbl = ctk.CTkLabel(
                row,
                text=t("recordings.remote_meta", size=size_mb, date=date_display),
                font=ctk.CTkFont(size=11),
                text_color=("#64748B", "#94A3B8"),
            )
            meta_lbl.grid(row=1, column=0, padx=14, pady=(0, 8), sticky="w")

            actions_frame = ctk.CTkFrame(row, fg_color="transparent")
            actions_frame.grid(row=0, column=1, rowspan=2, padx=12, pady=8, sticky="e")

            dl_btn = ctk.CTkButton(
                actions_frame,
                text=t("recordings.download"),
                width=90,
                height=30,
                font=ctk.CTkFont(size=11, weight="bold"),
                fg_color=("#0284C7", "#0284C7"),
                hover_color=("#0369A1", "#0369A1"),
                text_color="#FFFFFF",
                corner_radius=6,
            )
            dl_btn.configure(command=lambda g=game_id, b=dl_btn: self._download_single_recording(g, b))
            dl_btn.pack(side="left", padx=(0, 6) if is_admin else 0)

            # Special Admin ability: delete recording from remote server
            if is_admin:
                del_btn = ctk.CTkButton(
                    actions_frame,
                    text=t("recordings.delete_remote"),
                    width=80,
                    height=30,
                    font=ctk.CTkFont(size=11, weight="bold"),
                    fg_color=("#DC2626", "#DC2626"),
                    hover_color=("#B91C1C", "#B91C1C"),
                    text_color="#FFFFFF",
                    corner_radius=6,
                )
                del_btn.configure(command=lambda g=game_id, b=del_btn: self._delete_remote_recording(g, b))
                del_btn.pack(side="left")

            row_idx += 1

        # Set of game IDs known to be on the remote server
        remote_game_ids = {
            str(it.get("game_id")).strip() for it in remote_items if str(it.get("game_id", "")).strip()
        }

        from app.scrim_meta import load_all_scrim_metadata

        all_local_meta = load_all_scrim_metadata(folder)
        can_organize = self._can_upload_to_cloud()

        # 2. Render local scrim recordings
        for filename in files:
            filepath = os.path.join(folder, filename)
            mtime = os.path.getmtime(filepath)
            dt_str = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
            size_mb = os.path.getsize(filepath) / (1024 * 1024)
            game_id = filename[:-4] if filename.lower().endswith(".mp3") else filename

            is_on_server = game_id in remote_game_ids
            meta = all_local_meta.get(game_id, {})
            saved_team = meta.get("team_tag", "")
            saved_notes = meta.get("notes", "")
            recorded_date = str(meta.get("recorded_at") or dt_str)[:10]

            row = ctk.CTkFrame(
                self.rec_scroll_frame,
                fg_color=("#F1F5F9", "#0B1329") if row_idx % 2 == 0 else ("#E2E8F0", "#0E1833"),
                corner_radius=8,
                border_width=1,
                border_color=("#CBD5E1", "#162038"),
            )
            row.pack(fill="x", padx=6, pady=4)
            row.grid_columnconfigure(0, weight=1)

            title_frame = ctk.CTkFrame(row, fg_color="transparent")
            title_frame.grid(row=0, column=0, padx=14, pady=(8, 2), sticky="w")

            name_lbl = ctk.CTkLabel(
                title_frame,
                text=f"🎵 {filename}",
                font=ctk.CTkFont(size=13, weight="bold"),
                text_color=("#0F172A", "#F8FAFC"),
            )
            name_lbl.pack(side="left")

            # Clicking on the .mp3 file name opens details dialog if user has upload/admin access
            if can_organize:
                name_lbl.configure(cursor="hand2")
                name_lbl.bind(
                    "<Button-1>",
                    lambda e, g=game_id, f=filepath, m=mtime: self._open_edit_details_dialog(g, f, m),
                )

            badge_text = f"[{t('recordings.status_local')}]"
            if is_on_server:
                badge_text += f" • [{t('recordings.status_synced')}]"
            if saved_team:
                badge_text += f" • vs {saved_team}"
            badge = ctk.CTkLabel(
                title_frame,
                text=badge_text,
                font=ctk.CTkFont(size=11, weight="bold"),
                text_color=("#059669", "#10B981") if is_on_server else ("#0284C7", "#38BDF8"),
            )
            badge.pack(side="left", padx=8)

            patch = meta.get("game_patch", "")
            date_display = f"{recorded_date}  |  Patch: {patch}" if patch else recorded_date
            notes_preview = (
                f"  |  \"{saved_notes[:35]}...\""
                if len(saved_notes) > 35
                else (f"  |  \"{saved_notes}\"" if saved_notes else "")
            )
            meta_lbl = ctk.CTkLabel(
                row,
                text=t("recordings.meta", size=size_mb, date=date_display) + notes_preview,
                font=ctk.CTkFont(size=11),
                text_color=("#64748B", "#94A3B8"),
            )
            meta_lbl.grid(row=1, column=0, padx=14, pady=(0, 8), sticky="w")

            actions_frame = ctk.CTkFrame(row, fg_color="transparent")
            actions_frame.grid(row=0, column=1, rowspan=2, padx=12, pady=8, sticky="e")

            # Show Edit Details button for users with upload/admin permissions
            if can_organize:
                details_btn = ctk.CTkButton(
                    actions_frame,
                    text=t("recordings.edit_details"),
                    width=75,
                    height=30,
                    font=ctk.CTkFont(size=11, weight="bold"),
                    fg_color=("#2563EB", "#3B82F6"),
                    hover_color=("#1D4ED8", "#2563EB"),
                    text_color="#FFFFFF",
                    corner_radius=6,
                )
                details_btn.configure(
                    command=lambda g=game_id, f=filepath, m=mtime: self._open_edit_details_dialog(g, f, m)
                )
                details_btn.pack(side="left", padx=(0, 6))

            # Show Upload button if not yet on server and client has upload permissions
            if not is_on_server and can_organize:
                upload_btn = ctk.CTkButton(
                    actions_frame,
                    text=t("recordings.upload"),
                    width=85,
                    height=30,
                    font=ctk.CTkFont(size=11, weight="bold"),
                    fg_color=("#4F46E5", "#6366F1"),
                    hover_color=("#4338CA", "#4F46E5"),
                    text_color="#FFFFFF",
                    corner_radius=6,
                )
                upload_btn.configure(
                    command=lambda g=game_id, f=filepath, b=upload_btn: self._upload_single_recording(g, f, b)
                )
                upload_btn.pack(side="left", padx=(0, 6))

            play_btn = ctk.CTkButton(
                actions_frame,
                text=t("recordings.play"),
                width=75,
                height=30,
                font=ctk.CTkFont(size=11, weight="bold"),
                fg_color="#0AC8B9",
                hover_color="#0397AB",
                text_color="#030712",
                corner_radius=6,
                command=lambda p=filepath: self._play_audio_file(p),
            )
            play_btn.pack(side="left")

            # Special Admin ability: delete recording from remote server
            if is_admin and is_on_server:
                del_btn = ctk.CTkButton(
                    actions_frame,
                    text=t("recordings.delete_remote"),
                    width=80,
                    height=30,
                    font=ctk.CTkFont(size=11, weight="bold"),
                    fg_color=("#DC2626", "#DC2626"),
                    hover_color=("#B91C1C", "#B91C1C"),
                    text_color="#FFFFFF",
                    corner_radius=6,
                )
                del_btn.configure(command=lambda g=game_id, b=del_btn: self._delete_remote_recording(g, b))
                del_btn.pack(side="left", padx=(6, 0))

            row_idx += 1

    def _can_upload_to_cloud(self) -> bool:
        """Determines whether the client has permission to upload recordings to the remote server.

        Returns False if the active API key has the read-only 'pull' role, or if no key/URL is configured.
        Returns True if the role is 'upload' or 'admin'.
        """
        key = self.config.get("remote_api_key", "").strip()
        url = self.config.get("remote_server_url", "").strip()
        if not key or not url:
            return False

        role = getattr(self, "_cloud_role", "unknown")
        if role == "pull":
            return False
        if role in ("upload", "admin"):
            return True

        # Fallback inspection on the key naming pattern if health check hasn't run yet
        key_lower = key.lower()
        if "pull" in key_lower:
            return False
        if "upload" in key_lower or "admin" in key_lower:
            return True

        # If role is unknown and not yet checked, trigger background check to discover role
        if role == "unknown" and not getattr(self, "_checking_cloud_role", False):
            self._checking_cloud_role = True

            def _check():
                try:
                    from app.cloud_sync import CloudSyncClient

                    client = CloudSyncClient(server_url=url, api_key=key)
                    ok, info = client.check_health()
                    if ok and isinstance(info, dict):
                        self._cloud_role = str(info.get("authenticated_role") or "unknown").lower()
                        self.after(0, self._refresh_recordings_list)
                except Exception:
                    pass
                finally:
                    self._checking_cloud_role = False

            threading.Thread(target=_check, daemon=True).start()

        return False

    def _sync_remote_recordings(self):
        """Fetches recordings index from the remote server."""
        url = self.config.get("remote_server_url", "").strip()
        key = self.config.get("remote_api_key", "").strip()
        if not url or not key:
            self.log_message(t("recordings.remote_sync_failed", error="Server URL or API Key not configured in Settings."))
            return

        if hasattr(self, "sync_remote_btn"):
            self.sync_remote_btn.configure(text=t("recordings.syncing"), state="disabled")

        def _worker():
            from app.cloud_sync import CloudSyncClient

            client = CloudSyncClient(server_url=url, api_key=key)
            health_ok, health_info = client.check_health()
            if health_ok and isinstance(health_info, dict):
                self._cloud_role = str(health_info.get("authenticated_role") or "unknown").lower()
            items = client.list_recordings()

            def _ui():
                if hasattr(self, "sync_remote_btn"):
                    self.sync_remote_btn.configure(text=t("recordings.sync_remote"), state="normal")
                self._remote_recordings = items

                # Sync remote metadata (opponent, patch, notes) to any existing local files
                folder = self.config.get("record_folder") or get_current_user_documents_recordings_dir()
                try:
                    from app.scrim_meta import get_scrim_metadata, save_scrim_metadata

                    for it in items:
                        gid = str(it.get("game_id", "")).strip()
                        if not gid:
                            continue
                        loc_file = os.path.join(folder, f"{gid}.mp3")
                        if os.path.exists(loc_file):
                            loc_meta = get_scrim_metadata(folder, gid, loc_file)
                            updated = False
                            remote_team = it.get("team_tag")
                            remote_patch = it.get("patch") or it.get("game_patch")
                            remote_notes = it.get("notes")
                            if remote_team and loc_meta.get("team_tag") != remote_team:
                                loc_meta["team_tag"] = remote_team
                                updated = True
                            if remote_patch and loc_meta.get("game_patch") != remote_patch:
                                loc_meta["game_patch"] = remote_patch
                                updated = True
                            if remote_notes and loc_meta.get("notes") != remote_notes:
                                loc_meta["notes"] = remote_notes
                                updated = True
                            if updated:
                                save_scrim_metadata(folder, gid, loc_meta, embed_id3=True, filepath=loc_file)
                except Exception:
                    pass

                self._refresh_recordings_list()
                local_files = set(os.listdir(folder)) if os.path.exists(folder) else set()
                missing = sum(1 for it in items if f"{it.get('game_id')}.mp3" not in local_files)
                self.log_message(t("recordings.remote_sync_success", total=len(items), missing=missing))

            self.after(0, _ui)

        threading.Thread(target=_worker, daemon=True).start()

    def _download_single_recording(self, game_id, btn_widget=None):
        """Downloads one missing recording from the remote server."""
        url = self.config.get("remote_server_url", "").strip()
        key = self.config.get("remote_api_key", "").strip()
        if not url or not key:
            return

        if btn_widget:
            btn_widget.configure(text=t("recordings.downloading"), state="disabled")

        folder = self.config.get("record_folder") or get_current_user_documents_recordings_dir()
        dest_path = os.path.join(folder, f"{game_id}.mp3")

        def _worker():
            from app.cloud_sync import CloudSyncClient

            client = CloudSyncClient(server_url=url, api_key=key)
            success = client.download_recording(game_id, dest_path)

            if success:
                try:
                    from app.scrim_meta import save_scrim_metadata

                    item = next(
                        (
                            it
                            for it in getattr(self, "_remote_recordings", [])
                            if str(it.get("game_id", "")).strip() == str(game_id).strip()
                        ),
                        None,
                    )
                    if item:
                        update_meta = {}
                        if item.get("team_tag"):
                            update_meta["team_tag"] = item.get("team_tag")
                        if item.get("notes"):
                            update_meta["notes"] = item.get("notes")
                        patch_val = item.get("patch") or item.get("game_patch")
                        if patch_val:
                            update_meta["game_patch"] = patch_val
                        date_val = item.get("created_at") or item.get("upload_timestamp")
                        if date_val:
                            update_meta["recorded_at"] = str(date_val)[:10]

                        if update_meta:
                            save_scrim_metadata(
                                folder,
                                game_id,
                                update_meta,
                                embed_id3=True,
                                filepath=dest_path,
                            )
                except Exception:
                    pass

            def _ui():
                if success:
                    self.log_message(t("recordings.download_success", filename=f"{game_id}.mp3"))
                else:
                    self.log_message(
                        t("recordings.download_failed", filename=f"{game_id}.mp3", error="Network or server error")
                    )
                self._refresh_recordings_list()

            self.after(0, _ui)

        threading.Thread(target=_worker, daemon=True).start()

    def _download_all_missing_recordings(self):
        """Downloads all detected missing remote recordings sequentially."""
        url = self.config.get("remote_server_url", "").strip()
        key = self.config.get("remote_api_key", "").strip()
        if not url or not key or not getattr(self, "_remote_recordings", None):
            return

        folder = self.config.get("record_folder") or get_current_user_documents_recordings_dir()
        local_files = set(os.listdir(folder)) if os.path.exists(folder) else set()
        missing = [
            str(it["game_id"]).strip()
            for it in self._remote_recordings
            if f"{it.get('game_id')}.mp3" not in local_files
        ]

        if not missing:
            return

        if hasattr(self, "download_all_btn"):
            self.download_all_btn.configure(text=t("recordings.downloading"), state="disabled")

        def _worker():
            from app.cloud_sync import CloudSyncClient

            client = CloudSyncClient(server_url=url, api_key=key)
            success_count = 0
            for gid in missing:
                dest = os.path.join(folder, f"{gid}.mp3")
                if client.download_recording(gid, dest):
                    success_count += 1
                    try:
                        from app.scrim_meta import save_scrim_metadata

                        item = next(
                            (
                                it
                                for it in getattr(self, "_remote_recordings", [])
                                if str(it.get("game_id", "")).strip() == str(gid).strip()
                            ),
                            None,
                        )
                        if item:
                            update_meta = {}
                            if item.get("team_tag"):
                                update_meta["team_tag"] = item.get("team_tag")
                            if item.get("notes"):
                                update_meta["notes"] = item.get("notes")
                            patch_val = item.get("patch") or item.get("game_patch")
                            if patch_val:
                                update_meta["game_patch"] = patch_val
                            date_val = item.get("created_at") or item.get("upload_timestamp")
                            if date_val:
                                update_meta["recorded_at"] = str(date_val)[:10]

                            if update_meta:
                                save_scrim_metadata(
                                    folder,
                                    gid,
                                    update_meta,
                                    embed_id3=True,
                                    filepath=dest,
                                )
                    except Exception:
                        pass
                    self.log_message(t("recordings.download_success", filename=f"{gid}.mp3"))

            def _ui():
                if hasattr(self, "download_all_btn"):
                    self.download_all_btn.configure(state="normal")
                self._refresh_recordings_list()
                self.log_message(f"⬇ Finished batch download: {success_count}/{len(missing)} files downloaded.")

            self.after(0, _ui)

        threading.Thread(target=_worker, daemon=True).start()

    def _upload_single_recording(self, game_id, file_path, btn_widget=None):
        """Uploads a local recording to the remote server after verifying if it's already on the server."""
        if not self._can_upload_to_cloud():
            self.log_message(
                t("recordings.upload_failed", filename=f"{game_id}.mp3", error="Pull API key has read-only permissions.")
            )
            return

        url = self.config.get("remote_server_url", "").strip()
        key = self.config.get("remote_api_key", "").strip()
        if not url or not key:
            self.log_message(
                t("recordings.upload_failed", filename=f"{game_id}.mp3", error="Server URL or API Key not configured.")
            )
            return

        if btn_widget:
            btn_widget.configure(text=t("recordings.uploading"), state="disabled")

        def _worker():
            from app.cloud_sync import CloudSyncClient
            from app.scrim_meta import get_scrim_metadata

            folder = self.config.get("record_folder") or get_current_user_documents_recordings_dir()
            meta = get_scrim_metadata(folder, game_id, file_path)
            team_tag = meta.get("team_tag", "")
            notes = meta.get("notes", "")
            patch = meta.get("game_patch", "")

            client = CloudSyncClient(server_url=url, api_key=key)

            # Check first if the recording is already on the remote server
            exists = client.check_recording_exists(game_id)
            if exists:
                def _on_already_exists():
                    self.log_message(t("recordings.already_on_server", filename=f"{game_id}.mp3"))
                    if hasattr(self, "_remote_recordings"):
                        if not any(it.get("game_id") == game_id for it in self._remote_recordings):
                            self._remote_recordings.append({
                                "game_id": game_id,
                                "file_size": os.path.getsize(file_path),
                                "team_tag": team_tag,
                                "notes": notes,
                                "patch": patch,
                            })
                    self._refresh_recordings_list()

                self.after(0, _on_already_exists)
                return

            # Perform the upload
            self.log_message(t("logs.cloud_upload_start", filename=f"{game_id}.mp3"))
            success, res = client.upload_recording(
                game_id, file_path, team_tag=team_tag, notes=notes, game_patch=patch, check_first=False
            )

            def _ui():
                if success:
                    self.log_message(t("recordings.upload_success", filename=f"{game_id}.mp3"))
                    if hasattr(self, "_remote_recordings"):
                        if not any(it.get("game_id") == game_id for it in self._remote_recordings):
                            self._remote_recordings.append({
                                "game_id": game_id,
                                "file_size": os.path.getsize(file_path),
                                "team_tag": team_tag,
                                "notes": notes,
                                "patch": patch,
                                "created_at": datetime.datetime.now().strftime("%Y-%m-%d"),
                            })
                else:
                    self.log_message(t("recordings.upload_failed", filename=f"{game_id}.mp3", error=str(res)))
                self._refresh_recordings_list()

            self.after(0, _ui)

        threading.Thread(target=_worker, daemon=True).start()

    def _open_edit_details_dialog(self, game_id: str, file_path: str, mtime: float | None = None):
        """Opens a modal dialog allowing users with upload/admin access to edit team tag, notes, and recorded date."""
        if not self._can_upload_to_cloud():
            return None

        from app.scrim_meta import get_scrim_metadata, save_scrim_metadata

        folder = self.config.get("record_folder") or get_current_user_documents_recordings_dir()
        meta = get_scrim_metadata(folder, game_id, file_path)

        # Dialog window
        dialog = ctk.CTkToplevel(self)
        dialog.title(f"{t('recordings.details_title')} — {game_id}.mp3")
        dialog.geometry("490x495")
        dialog.resizable(False, False)
        dialog.transient(self)
        if not getattr(self, "_in_test_suite", False):
            dialog.grab_set()

        # Center over parent
        try:
            px = self.winfo_x() + (self.winfo_width() - 490) // 2
            py = self.winfo_y() + (self.winfo_height() - 495) // 2
            dialog.geometry(f"+{max(0, px)}+{max(0, py)}")
        except Exception:
            pass

        # Card container
        container = ctk.CTkFrame(dialog, fg_color=("#FFFFFF", "#0A1128"), corner_radius=12)
        container.pack(fill="both", expand=True, padx=14, pady=14)
        container.grid_columnconfigure(1, weight=1)

        # Header Title
        title_lbl = ctk.CTkLabel(
            container,
            text=f"📝 {game_id}.mp3",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=("#0F172A", "#F8FAFC"),
        )
        title_lbl.grid(row=0, column=0, columnspan=2, padx=16, pady=(14, 2), sticky="w")

        sub_lbl = ctk.CTkLabel(
            container,
            text=t("recordings.details_subtitle"),
            font=ctk.CTkFont(size=12),
            text_color=("#64748B", "#94A3B8"),
        )
        sub_lbl.grid(row=1, column=0, columnspan=2, padx=16, pady=(0, 14), sticky="w")

        # Row 1: Date Recorded (Automatically stamped, editable)
        date_lbl = ctk.CTkLabel(
            container,
            text=t("recordings.field_date"),
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        date_lbl.grid(row=2, column=0, padx=16, pady=6, sticky="w")

        date_entry = ctk.CTkEntry(
            container,
            font=ctk.CTkFont(size=12),
            height=32,
        )
        default_date = meta.get("recorded_at")
        if default_date:
            default_date = str(default_date)[:10]
        elif mtime:
            default_date = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
        else:
            default_date = datetime.datetime.now().strftime("%Y-%m-%d")
        date_entry.insert(0, default_date)
        date_entry.grid(row=2, column=1, padx=16, pady=6, sticky="ew")

        # Row 2: Game Patch (e.g. 14.22)
        patch_lbl = ctk.CTkLabel(
            container,
            text=t("recordings.field_patch"),
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        patch_lbl.grid(row=3, column=0, padx=16, pady=6, sticky="w")

        patch_entry = ctk.CTkEntry(
            container,
            placeholder_text=t("recordings.field_patch_placeholder"),
            font=ctk.CTkFont(size=12),
            height=32,
        )
        saved_patch = meta.get("game_patch", "")
        if saved_patch:
            patch_entry.insert(0, saved_patch)
        patch_entry.grid(row=3, column=1, padx=16, pady=6, sticky="ew")

        # Row 3: VS Opponent Team
        team_lbl = ctk.CTkLabel(
            container,
            text=t("recordings.field_team"),
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        team_lbl.grid(row=4, column=0, padx=16, pady=6, sticky="w")

        team_entry = ctk.CTkEntry(
            container,
            placeholder_text=t("recordings.field_team_placeholder"),
            font=ctk.CTkFont(size=12),
            height=32,
        )
        saved_vs_team = meta.get("team_tag", "")
        if saved_vs_team:
            team_entry.insert(0, saved_vs_team)
        team_entry.grid(row=4, column=1, padx=16, pady=6, sticky="ew")

        # Row 4: Scrim Notes
        notes_lbl = ctk.CTkLabel(
            container,
            text=t("recordings.field_notes"),
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        notes_lbl.grid(row=5, column=0, padx=16, pady=(8, 2), sticky="nw")

        notes_box = ctk.CTkTextbox(
            container,
            height=100,
            font=ctk.CTkFont(size=12),
        )
        if meta.get("notes"):
            notes_box.insert("1.0", meta.get("notes"))
        notes_box.grid(row=5, column=1, padx=16, pady=6, sticky="nsew")

        # Button Bar
        btn_bar = ctk.CTkFrame(container, fg_color="transparent")
        btn_bar.grid(row=6, column=0, columnspan=2, padx=16, pady=(16, 12), sticky="e")

        def _save(and_upload=False):
            from app.record import format_patch_version

            raw_p = patch_entry.get().strip()
            new_meta = {
                "recorded_at": (date_entry.get().strip() or datetime.datetime.now().strftime("%Y-%m-%d"))[:10],
                "game_patch": format_patch_version(raw_p),
                "team_tag": team_entry.get().strip(),
                "notes": notes_box.get("1.0", "end-1c").strip(),
            }
            save_scrim_metadata(folder, game_id, new_meta, embed_id3=True, filepath=file_path)
            self.log_message(t("recordings.details_saved", filename=f"{game_id}.mp3"))
            self._refresh_recordings_list()
            dialog.destroy()
            if and_upload:
                self._upload_single_recording(game_id, file_path)

        cancel_btn = ctk.CTkButton(
            btn_bar,
            text=t("recordings.cancel"),
            width=80,
            height=32,
            fg_color="transparent",
            border_width=1,
            text_color=("#475569", "#CBD5E1"),
            command=dialog.destroy,
        )
        cancel_btn.pack(side="left", padx=(0, 8))

        save_btn = ctk.CTkButton(
            btn_bar,
            text=t("recordings.save_details"),
            width=110,
            height=32,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=("#059669", "#10B981"),
            hover_color=("#047857", "#059669"),
            text_color="#FFFFFF",
            command=lambda: _save(and_upload=False),
        )
        save_btn.pack(side="left")

        save_and_upload_btn = ctk.CTkButton(
            btn_bar,
            text=t("recordings.save_and_upload"),
            width=135,
            height=32,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=("#0284C7", "#38BDF8"),
            hover_color=("#0369A1", "#0284C7"),
            text_color="#FFFFFF",
            command=lambda: _save(and_upload=True),
        )
        save_and_upload_btn.pack(side="left", padx=(8, 0))

        # Expose widgets on dialog for test assertions
        dialog._date_entry = date_entry
        dialog._patch_entry = patch_entry
        dialog._team_entry = team_entry
        dialog._notes_box = notes_box
        dialog._save_btn = save_btn
        dialog._save_and_upload_btn = save_and_upload_btn

        if getattr(self, "_in_test_suite", False):
            dialog.withdraw()

        return dialog

    def _delete_remote_recording(self, game_id, btn_widget=None):
        """Prompts confirmation and permanently deletes recording from remote server (Admin only)."""
        clean_id = str(game_id).strip()
        if not getattr(self, "_in_test_suite", False):
            import tkinter.messagebox as mb

            confirmed = mb.askyesno(
                title=t("recordings.delete_confirm_title"),
                message=t("recordings.delete_confirm_msg", game_id=clean_id),
                parent=self,
            )
            if not confirmed:
                return

        url = self.config.get("remote_server_url", "").strip()
        key = self.config.get("remote_api_key", "").strip()
        if not url or not key:
            return

        if btn_widget:
            btn_widget.configure(text=t("recordings.deleting"), state="disabled")

        def _worker():
            from app.cloud_sync import CloudSyncClient

            client = CloudSyncClient(server_url=url, api_key=key)
            success, msg = client.delete_recording(clean_id)

            def _ui():
                if success:
                    self.log_message(t("recordings.delete_success", filename=f"{clean_id}.mp3"))
                    if hasattr(self, "_remote_recordings"):
                        self._remote_recordings = [
                            it for it in self._remote_recordings
                            if str(it.get("game_id", "")).strip() != clean_id
                        ]
                else:
                    self.log_message(t("recordings.delete_failed", filename=f"{clean_id}.mp3", error=str(msg)))
                self._refresh_recordings_list()

            self.after(0, _ui)

        threading.Thread(target=_worker, daemon=True).start()

    def _play_audio_file(self, audio_path):
        """Plays selected audio file via default application or VLC."""
        try:
            if os.name == "nt":
                os.startfile(audio_path)
            else:
                subprocess.run(["xdg-open", audio_path])
        except Exception as e:
            self.log_message(t("recordings.open_failed", error=e))

    # ==========================================
    # SETTINGS TAB FUNCTIONS
    # ==========================================
    def _browse_rec_folder(self):
        curr = self.rec_folder_entry.get().strip() or get_current_user_documents_recordings_dir()
        selected = filedialog.askdirectory(initialdir=curr, title=t("settings.rec_folder"))
        if selected:
            self.rec_folder_entry.delete(0, "end")
            self.rec_folder_entry.insert(0, selected)
            self.config["record_folder"] = selected
            save_config(self.config)
            self._refresh_recordings_list()

    def _browse_vlc_folder(self):
        curr = self.vlc_dir_entry.get().strip() or r"C:\Program Files\VideoLAN\VLC"
        selected = filedialog.askdirectory(initialdir=curr, title=t("settings.vlc_dir"))
        if selected:
            self.vlc_dir_entry.delete(0, "end")
            self.vlc_dir_entry.insert(0, selected)
            self.config["vlc_dir"] = selected
            save_config(self.config)
            self._check_system_health()

    def _on_settings_entry_changed(self, event=None):
        """Immediately saves modified setting entries to config.json without dialog popups."""
        try:
            changed = False
            refresh_rec = False
            refresh_health = False

            if hasattr(self, "rec_folder_entry"):
                f = self.rec_folder_entry.get().strip()
                if f and f != self.config.get("record_folder"):
                    self.config["record_folder"] = f
                    changed = True
                    refresh_rec = True

            if hasattr(self, "vlc_dir_entry"):
                vd = self.vlc_dir_entry.get().strip()
                if vd and vd != self.config.get("vlc_dir"):
                    self.config["vlc_dir"] = vd
                    changed = True
                    refresh_health = True

            if hasattr(self, "replay_vol_entry"):
                v_str = self.replay_vol_entry.get().strip()
                if v_str.isdigit():
                    v_val = max(0, min(200, int(v_str)))
                    if v_val != self.config.get("replay_volume"):
                        self.config["replay_volume"] = v_val
                        if hasattr(self, "vol_slider"):
                            self.vol_slider.set(v_val)
                        if hasattr(self, "_on_replay_volume_slider_changed"):
                            self._on_replay_volume_slider_changed(v_val)
                        changed = True

            if hasattr(self, "cloud_url_entry"):
                u = self.cloud_url_entry.get().strip()
                if u != self.config.get("remote_server_url"):
                    self.config["remote_server_url"] = u
                    changed = True

            if hasattr(self, "cloud_key_entry"):
                k = self.cloud_key_entry.get().strip()
                if k != self.config.get("remote_api_key"):
                    self.config["remote_api_key"] = k
                    changed = True

            if changed:
                save_config(self.config)
                if refresh_rec and hasattr(self, "_refresh_recordings_list"):
                    try:
                        self._refresh_recordings_list()
                    except Exception:
                        pass
                if refresh_health and hasattr(self, "_check_system_health"):
                    try:
                        self._check_system_health()
                    except Exception:
                        pass
        except Exception:
            pass

    def _on_settings_entry_debounced(self, event=None):
        """Debounces entry keystrokes to commit to config.json 500ms after user pauses typing."""
        if hasattr(self, "_save_entry_timer") and self._save_entry_timer:
            try:
                self.after_cancel(self._save_entry_timer)
            except Exception:
                pass
        self._save_entry_timer = self.after(500, self._on_settings_entry_changed)

    def _save_settings(self):
        try:
            vol_val = int(self.replay_vol_entry.get().strip() or 80)
            vol_val = max(0, min(200, vol_val))
            selected_lang_name = self.lang_menu.get()
            selected_lang_code = self.lang_name_to_code.get(selected_lang_name, "en")

            selected_theme_display = self.theme_menu.get() if hasattr(self, "theme_menu") else None
            selected_theme_code = (
                self.theme_options.get(selected_theme_display, self.current_theme)
                if hasattr(self, "theme_options") and selected_theme_display
                else self.current_theme
            )

            new_config = {
                **self.config,
                "recording_mode": "native",
                "record_folder": self.rec_folder_entry.get().strip(),
                "vlc_dir": self.vlc_dir_entry.get().strip(),
                "replay_volume": vol_val,
                "audio_format": "mp3",
                "language": selected_lang_code,
                "theme": selected_theme_code,
                "auto_start_champ_select": bool(self.auto_champ_select_var.get()),
                "auto_start_replay": bool(self.auto_replay_var.get()),
                "hud_overlay_enabled": bool(self.hud_enable_var.get()) if hasattr(self, "hud_enable_var") else True,
                "hud_locked": bool(self.hud_lock_var.get()) if hasattr(self, "hud_lock_var") else False,
                "hud_click_through": bool(self.hud_lock_var.get()) if hasattr(self, "hud_lock_var") else False,
                "hud_opacity": round(float(self.hud_opacity_slider.get()), 2) if hasattr(self, "hud_opacity_slider") else 0.85,
                "remote_server_url": self.cloud_url_entry.get().strip() if hasattr(self, "cloud_url_entry") else "",
                "remote_api_key": self.cloud_key_entry.get().strip() if hasattr(self, "cloud_key_entry") else "",
            }
            if save_config(new_config):
                self.config = new_config
                set_language(selected_lang_code)
                self._set_theme(selected_theme_code)
                if hasattr(self, "recorder_worker") and self.recorder_worker and hasattr(self.recorder_worker, "native_recorder"):
                    self.recorder_worker.native_recorder.audio_format = "mp3"
                    self.recorder_worker.native_recorder.set_ai_noise_suppression(new_config["ai_noise_suppression"])
                if self.hud:
                    self.hud.set_locked(new_config["hud_locked"])
                    self.hud.set_opacity(new_config["hud_opacity"])
                    if not new_config["hud_overlay_enabled"]:
                        self.hud.hide()
                self.vol_slider.set(vol_val)
                self._on_replay_volume_slider_changed(vol_val)
                self.rec_scene_lbl.configure(
                    text=self._get_engine_display_text()
                )
                self._check_system_health()
                self._refresh_recordings_list()
                self.log_message(f"[SETTINGS] {t('settings.saved_success')}")
            else:
                self.log_message(f"[SETTINGS] {t('settings.saved_error')}")
        except Exception as e:
            self.log_message(f"[ERROR] {e}")

    # ==========================================
    # LOG UTILITIES
    # ==========================================
    def _copy_log(self):
        text = self.log_textbox.get("1.0", "end-1c")
        self.clipboard_clear()
        self.clipboard_append(text)
        self.log_message(t("log.copied"))

    def _clear_log(self):
        self.log_textbox.configure(state="normal")
        self.log_textbox.delete("1.0", "end")
        self.log_textbox.configure(state="disabled")

    # ==========================================
    # CLEAN SHUTDOWN
    # ==========================================
    def _flush_pending_settings_to_disk(self):
        """Immediately flushes all settings and debounced sliders to config.json."""
        try:
            if hasattr(self, "_save_hud_op_timer") and self._save_hud_op_timer:
                try:
                    self.after_cancel(self._save_hud_op_timer)
                except Exception:
                    pass
                self._save_hud_op_timer = None

            if hasattr(self, "_save_vol_timer") and self._save_vol_timer:
                try:
                    self.after_cancel(self._save_vol_timer)
                except Exception:
                    pass
                self._save_vol_timer = None

            if hasattr(self, "hud_opacity_slider"):
                self.config["hud_opacity"] = round(float(self.hud_opacity_slider.get()), 2)
            if hasattr(self, "hud_enable_var"):
                self.config["hud_overlay_enabled"] = bool(self.hud_enable_var.get())
            if hasattr(self, "hud_lock_var"):
                self.config["hud_locked"] = bool(self.hud_lock_var.get())
                self.config["hud_click_through"] = bool(self.hud_lock_var.get())
            if hasattr(self, "auto_champ_select_var"):
                self.config["auto_start_champ_select"] = bool(self.auto_champ_select_var.get())
            if hasattr(self, "auto_replay_var"):
                self.config["auto_start_replay"] = bool(self.auto_replay_var.get())
            if hasattr(self, "minimize_to_tray_var"):
                self.config["minimize_to_tray"] = bool(self.minimize_to_tray_var.get())
            if hasattr(self, "rec_folder_entry"):
                folder = self.rec_folder_entry.get().strip()
                if folder:
                    self.config["record_folder"] = folder
            if hasattr(self, "vlc_dir_entry"):
                vlc_d = self.vlc_dir_entry.get().strip()
                if vlc_d:
                    self.config["vlc_dir"] = vlc_d

            save_config(self.config)
        except Exception:
            pass

    def _on_close(self, force: bool = False):
        # 1. Warn if active match recording is in progress (unless forced via Ctrl+C / SIGINT)
        if not force and self.recorder_worker and self.recorder_worker.recording:
            if not messagebox.askyesno(
                t("record.title"),
                t("app.active_rec_warning"),
            ):
                return
            try:
                self.recorder_worker.stop()
                if self.recorder_thread:
                    self.recorder_thread.join(timeout=4)
            except Exception:
                pass
        else:
            if self.recorder_worker:
                self.recorder_worker.stop()

        if self.replay_worker:
            self.replay_worker.stop()

        # Clean up System Tray icon
        if hasattr(self, "tray_manager") and self.tray_manager:
            try:
                self.tray_manager.stop()
            except Exception:
                pass

        # 2. Persist any uncommitted slider or setting state before exit
        if not getattr(self, "_in_test_suite", False):
            self._flush_pending_settings_to_disk()

        # 3. Instant visual responsiveness: hide window and taskbar icon immediately (< 1ms)
        try:
            if hasattr(self, "hud") and self.hud:
                self.hud.hide()
            self.withdraw()
            if not getattr(self, "_in_test_suite", False):
                self.update_idletasks()
        except Exception:
            pass

        # 3. Clean up preview audio monitor
        if hasattr(self, "_preview_native_recorder") and self._preview_native_recorder:
            try:
                self._preview_native_recorder.close()
            except Exception:
                pass
            self._preview_native_recorder = None

        self.destroy()

        # 4. Fast exit: terminate process immediately to bypass C extension teardown hangs
        if not getattr(self, "_in_test_suite", False):
            try:
                os._exit(0)
            except Exception:
                pass

    def report_callback_exception(self, exc, val, tb):
        """Catches Ctrl+C KeyboardInterrupt inside Tk event loop and exits immediately."""
        if issubclass(exc, KeyboardInterrupt):
            self._on_close(force=True)
            return
        super().report_callback_exception(exc, val, tb)

    def destroy(self):
        self._is_destroyed = True
        if hasattr(self, "hud") and self.hud:
            try:
                self.hud.destroy()
            except Exception:
                pass
            self.hud = None
        if hasattr(self, "_preview_native_recorder") and self._preview_native_recorder:
            try:
                self._preview_native_recorder.close()
            except Exception:
                pass
            self._preview_native_recorder = None
        remove_language_change_listener(self._update_ui_texts)
        if hasattr(self, "_queue_timer") and self._queue_timer:
            try:
                self.after_cancel(self._queue_timer)
            except Exception:
                pass
        if hasattr(self, "_meter_timer") and self._meter_timer:
            try:
                self.after_cancel(self._meter_timer)
            except Exception:
                pass
        super().destroy()


def main():
    app = LeagueVoiceRecorderApp()

    def _sig_handler(sig, frame):
        try:
            app.after(0, lambda: app._on_close(force=True))
        except Exception:
            try:
                app._on_close(force=True)
            except Exception:
                os._exit(0)

    try:
        signal.signal(signal.SIGINT, _sig_handler)
        if hasattr(signal, "SIGBREAK"):
            signal.signal(signal.SIGBREAK, _sig_handler)
    except Exception:
        pass

    if sys.platform.startswith("win"):
        try:
            import ctypes
            from ctypes import wintypes

            PHANDLER_ROUTINE = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

            def _console_ctrl_handler(ctrl_type):
                if ctrl_type in (0, 1, 2):  # 0: CTRL_C, 1: CTRL_BREAK, 2: CTRL_CLOSE
                    try:
                        app.after(0, lambda: app._on_close(force=True))
                    except Exception:
                        try:
                            app._on_close(force=True)
                        except Exception:
                            os._exit(0)
                    return True
                return False

            _c_handler = PHANDLER_ROUTINE(_console_ctrl_handler)
            ctypes.windll.kernel32.SetConsoleCtrlHandler(_c_handler, True)
            app._console_ctrl_handler_ref = _c_handler
        except Exception:
            pass

    try:
        app.mainloop()
    except KeyboardInterrupt:
        try:
            app._on_close(force=True)
        except Exception:
            os._exit(0)
    finally:
        try:
            os._exit(0)
        except Exception:
            pass


if __name__ == "__main__":
    main()
