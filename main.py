import datetime
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk
import psutil

from app import __version__
from app.config import get_ffmpeg_path, load_config, save_config
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

if sys.platform.startswith("win"):
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

        # Load Configuration
        self.config = load_config()

        # Appearance mode (dark / light / system)
        self.current_theme = self.config.get("theme", "dark")
        ctk.set_appearance_mode(self.current_theme)

        # Initialize i18n
        set_language(self.config.get("language", "en"))
        on_language_change(self._update_ui_texts)

        self.title(f"{t('app.window_title')} v{__version__}")
        self.geometry("1040x960")
        self.minsize(850, 750)
        self.configure(fg_color=("#F1F5F9", "#060B18"))

        # Set Window & Taskbar Icon
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

        # Build UI
        self._setup_ui()

        # Periodic queue checker and health check
        self._queue_timer = self.after(50, self._process_queue)
        self.after(500, self._check_system_health)
        self.after(200, self._refresh_audio_targets)
        self.after(200, self._refresh_microphones)
        self._start_audio_meter_loop()

        # Automatic Champion Select & Replay match detection monitor
        self._last_auto_started_phase = None
        self._last_auto_started_rofl = None
        self.after(1000, self._start_auto_detect_monitor)

        # Clean leftover *.old files from previous updates and schedule background update check
        try:
            from app.updater import clean_old_executables

            clean_old_executables()
        except Exception:
            pass
        self._pending_update = None
        self.after(3000, self._check_update_background)

        # Replay mute state & hotkeys
        self._replay_muted = False
        self.bind("<Key-m>", self._on_key_m)
        self.bind("<Key-M>", self._on_key_m)

        # In-Game Floating HUD Widget (Translucent overlay for live scrim recording & replays)
        self._is_hud_previewing = False
        try:
            from app.hud_widget import LeagueHUDWidget

            self.hud = LeagueHUDWidget(
                self,
                opacity=float(self.config.get("hud_opacity", 0.85)),
                click_through=bool(self.config.get("hud_click_through", False)),
                initial_x=int(self.config.get("hud_x", -1)),
                initial_y=int(self.config.get("hud_y", -1)),
                on_volume_changed=self._on_hud_volume_changed,
                on_position_changed=self._on_hud_position_changed,
            )
        except Exception:
            self.hud = None

        # Handle window close
        self.protocol("WM_DELETE_WINDOW", self._on_close)

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
            corner_radius=14,
            border_width=1,
            border_color=("#E2E8F0", "#1E2A4A"),
        )
        header_frame.grid(row=0, column=0, padx=15, pady=(15, 8), sticky="ew")
        header_frame.grid_columnconfigure(0, weight=1)

        title_box = ctk.CTkFrame(header_frame, fg_color="transparent")
        title_box.grid(row=0, column=0, padx=18, pady=(14, 8), sticky="w")

        self.title_lbl = ctk.CTkLabel(
            title_box,
            text=t("app.title"),
            font=ctk.CTkFont(family="Segoe UI", size=21, weight="bold"),
            text_color=("#A07832", "#C8AA6E"),
        )
        self.title_lbl.pack(anchor="w")

        self.sub_lbl = ctk.CTkLabel(
            title_box,
            text=t("app.subtitle"),
            font=ctk.CTkFont(size=12),
            text_color=("#64748B", "#94A3B8"),
        )
        self.sub_lbl.pack(anchor="w")

        # Health Badges Frame
        health_frame = ctk.CTkFrame(
            header_frame,
            fg_color=("#F8FAFC", "#070D1E"),
            corner_radius=10,
            border_width=1,
            border_color=("#E2E8F0", "#162038"),
        )
        health_frame.grid(row=1, column=0, padx=18, pady=(0, 14), sticky="ew")

        self.discord_badge = ctk.CTkLabel(
            health_frame,
            text=f"{t('health.discord')} {t('health.checking')}",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=("#64748B", "#CBD5E1"),
        )
        self.discord_badge.pack(side="left", padx=14, pady=8)

        self.lol_badge = ctk.CTkLabel(
            health_frame,
            text=f"{t('health.league')} {t('health.checking')}",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=("#64748B", "#CBD5E1"),
        )
        self.lol_badge.pack(side="left", padx=14, pady=8)

        self.vlc_badge = ctk.CTkLabel(
            health_frame,
            text=f"{t('health.vlc')} {t('health.checking')}",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=("#64748B", "#CBD5E1"),
        )
        self.vlc_badge.pack(side="left", padx=14, pady=8)

        self.ffmpeg_badge = ctk.CTkLabel(
            health_frame,
            text=f"{t('health.ffmpeg')} {t('health.checking')}",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=("#64748B", "#CBD5E1"),
        )
        self.ffmpeg_badge.pack(side="left", padx=14, pady=8)

        self.theme_toggle_btn = ctk.CTkButton(
            health_frame,
            text="☀️" if self.current_theme == "light" else "🌙",
            width=36,
            height=28,
            font=ctk.CTkFont(family="Segoe UI Emoji", size=14),
            text_color=("#D97706", "#FBBF24"),
            fg_color=("#F1F5F9", "#0F1A36"),
            hover_color=("#E2E8F0", "#1E2A4A"),
            border_width=1,
            border_color=("#CBD5E1", "#1E2A4A"),
            corner_radius=6,
            command=self._toggle_theme,
        )
        self.theme_toggle_btn.pack(side="right", padx=(4, 10), pady=6)

        self.refresh_health_btn = ctk.CTkButton(
            health_frame,
            text=t("health.refresh"),
            width=85,
            height=28,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=("#F1F5F9", "#0F1A36"),
            hover_color=("#E2E8F0", "#1E2A4A"),
            border_width=1,
            border_color=("#CBD5E1", "#1E2A4A"),
            text_color=("#0F172A", "#E2E8F0"),
            corner_radius=6,
            command=self._check_system_health,
        )
        self.refresh_health_btn.pack(side="right", padx=(4, 6), pady=6)

        self.update_header_badge = ctk.CTkButton(
            health_frame,
            text="",
            height=28,
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
        action_frame.grid(row=1, column=0, padx=15, pady=8, sticky="ew")
        action_frame.grid_columnconfigure(0, weight=1)
        action_frame.grid_columnconfigure(1, weight=1)

        # --- CARD 1: RECORD ---
        rec_card = ctk.CTkFrame(
            action_frame,
            fg_color=("#FFFFFF", "#0B1329"),
            corner_radius=14,
            border_width=1,
            border_color=("#E2E8F0", "#1E2A4A"),
        )
        rec_card.grid(row=0, column=0, padx=(0, 8), pady=0, sticky="nsew")

        self.rec_header_lbl = ctk.CTkLabel(
            rec_card,
            text=t("record.title"),
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=("#E11D48", "#F43F5E"),
        )
        self.rec_header_lbl.pack(anchor="w", padx=18, pady=(14, 6))

        self.rec_status_pill = ctk.CTkLabel(
            rec_card,
            text=t("record.status_idle"),
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=("#64748B", "#94A3B8"),
            fg_color=("#F8FAFC", "#070D1E"),
            corner_radius=8,
            padx=12,
            pady=6,
        )
        self.rec_status_pill.pack(anchor="w", padx=18, pady=(0, 12))

        self.rec_btn = ctk.CTkButton(
            rec_card,
            text=t("record.start_btn"),
            font=ctk.CTkFont(size=14, weight="bold"),
            height=52,
            fg_color="#E11D48",
            hover_color="#BE123C",
            border_width=1,
            border_color=("#BE123C", "#F43F5E"),
            corner_radius=10,
            command=self._toggle_recording,
        )
        self.rec_btn.pack(fill="x", padx=18, pady=(0, 12))

        # Target Voice Capture Pill
        rec_scene_pill = ctk.CTkFrame(
            rec_card,
            fg_color=("#F8FAFC", "#070D1E"),
            corner_radius=8,
            border_width=1,
            border_color=("#E2E8F0", "#162038"),
        )
        rec_scene_pill.pack(fill="x", padx=18, pady=(0, 10))
        self.rec_scene_lbl = ctk.CTkLabel(
            rec_scene_pill,
            text=self._get_engine_display_text(),
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=("#E11D48", "#F43F5E"),
        )
        self.rec_scene_lbl.pack(padx=12, pady=7, anchor="w")

        # Program selector frame
        prog_frame = ctk.CTkFrame(
            rec_card,
            fg_color=("#F8FAFC", "#070D1E"),
            corner_radius=8,
            border_width=1,
            border_color=("#E2E8F0", "#162038"),
        )
        prog_frame.pack(fill="x", padx=18, pady=(0, 10))
        prog_frame.grid_columnconfigure(0, weight=1)
        prog_frame.grid_columnconfigure(1, weight=0)

        self.target_prog_lbl = ctk.CTkLabel(
            prog_frame,
            text=t("record.target_app"),
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=("#0891B2", "#0AC8B9"),
        )
        self.target_prog_lbl.grid(row=0, column=0, columnspan=2, padx=12, pady=(6, 2), sticky="w")

        self.available_targets = []
        self.target_prog_menu = ctk.CTkOptionMenu(
            prog_frame,
            values=["Discord (Auto Detect Active Audio) 🎯"],
            command=self._on_target_prog_selected,
            fg_color=("#FFFFFF", "#0B1329"),
            text_color=("#0F172A", "#F8FAFC"),
            button_color=("#0891B2", "#0AC8B9"),
            button_hover_color=("#0E7490", "#0397AB"),
            dropdown_fg_color=("#FFFFFF", "#0B1329"),
            dropdown_text_color=("#0F172A", "#F8FAFC"),
            dropdown_hover_color=("#F1F5F9", "#162038"),
            corner_radius=6,
        )
        self.target_prog_menu.grid(row=1, column=0, padx=(10, 4), pady=(0, 8), sticky="ew")

        self.refresh_progs_btn = ctk.CTkButton(
            prog_frame,
            text="🔄",
            width=36,
            font=ctk.CTkFont(family="Segoe UI Emoji", size=13),
            text_color=("#0F172A", "#E2E8F0"),
            fg_color=("#F1F5F9", "#0F1A36"),
            hover_color=("#E2E8F0", "#1E2A4A"),
            border_width=1,
            border_color=("#CBD5E1", "#1E2A4A"),
            corner_radius=6,
            command=self._refresh_audio_targets,
        )
        self.refresh_progs_btn.grid(row=1, column=1, padx=(0, 10), pady=(0, 8))

        # Microphone selector frame
        mic_frame = ctk.CTkFrame(
            rec_card,
            fg_color=("#F8FAFC", "#070D1E"),
            corner_radius=8,
            border_width=1,
            border_color=("#E2E8F0", "#162038"),
        )
        mic_frame.pack(fill="x", padx=18, pady=(0, 10))
        mic_frame.grid_columnconfigure(0, weight=1)
        mic_frame.grid_columnconfigure(1, weight=0)

        self.mic_input_lbl = ctk.CTkLabel(
            mic_frame,
            text=t("record.mic_input"),
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=("#0891B2", "#0AC8B9"),
        )
        self.mic_input_lbl.grid(row=0, column=0, columnspan=2, padx=12, pady=(6, 2), sticky="w")

        self.available_mics = []
        self.mic_input_menu = ctk.CTkOptionMenu(
            mic_frame,
            values=[t("record.mic_default")],
            command=self._on_mic_input_selected,
            fg_color=("#FFFFFF", "#0B1329"),
            text_color=("#0F172A", "#F8FAFC"),
            button_color=("#0891B2", "#0AC8B9"),
            button_hover_color=("#0E7490", "#0397AB"),
            dropdown_fg_color=("#FFFFFF", "#0B1329"),
            dropdown_text_color=("#0F172A", "#F8FAFC"),
            dropdown_hover_color=("#F1F5F9", "#162038"),
            corner_radius=6,
        )
        self.mic_input_menu.grid(row=1, column=0, padx=(10, 4), pady=(0, 8), sticky="ew")

        self.refresh_mics_btn = ctk.CTkButton(
            mic_frame,
            text="🔄",
            width=36,
            font=ctk.CTkFont(family="Segoe UI Emoji", size=13),
            text_color=("#0F172A", "#E2E8F0"),
            fg_color=("#F1F5F9", "#0F1A36"),
            hover_color=("#E2E8F0", "#1E2A4A"),
            border_width=1,
            border_color=("#CBD5E1", "#1E2A4A"),
            corner_radius=6,
            command=self._refresh_microphones,
        )
        self.refresh_mics_btn.grid(row=1, column=1, padx=(0, 10), pady=(0, 8))

        # Live Audio VU Meter Frame (Hangcsik)
        meter_frame = ctk.CTkFrame(
            rec_card,
            fg_color=("#F8FAFC", "#070D1E"),
            corner_radius=8,
            border_width=1,
            border_color=("#E2E8F0", "#162038"),
        )
        meter_frame.pack(fill="x", padx=18, pady=(0, 10))
        meter_frame.grid_columnconfigure(1, weight=1)

        # App Audio Meter
        self.app_meter_title = ctk.CTkLabel(
            meter_frame,
            text="🔊 " + t("record.app_sound") + ":",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=("#64748B", "#94A3B8"),
        )
        self.app_meter_title.grid(row=0, column=0, padx=(12, 6), pady=(8, 3), sticky="w")

        self.app_meter_bar = ctk.CTkProgressBar(
            meter_frame,
            height=8,
            corner_radius=4,
            fg_color=("#E2E8F0", "#0B1329"),
            progress_color="#10B981",
        )
        self.app_meter_bar.set(0.0)
        self.app_meter_bar.grid(row=0, column=1, padx=(0, 12), pady=(8, 3), sticky="ew")

        # Mic Meter
        self.mic_meter_title = ctk.CTkLabel(
            meter_frame,
            text="🎤 " + t("record.microphone") + ":",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=("#64748B", "#94A3B8"),
        )
        self.mic_meter_title.grid(row=1, column=0, padx=(12, 6), pady=(3, 10), sticky="w")

        self.mic_meter_bar = ctk.CTkProgressBar(
            meter_frame,
            height=8,
            corner_radius=4,
            fg_color=("#E2E8F0", "#0B1329"),
            progress_color=("#0891B2", "#0AC8B9"),
        )
        self.mic_meter_bar.set(0.0)
        self.mic_meter_bar.grid(row=1, column=1, padx=(0, 12), pady=(3, 10), sticky="ew")

        self.rec_info_lbl = ctk.CTkLabel(
            rec_card,
            text=t("record.info"),
            font=ctk.CTkFont(size=11),
            text_color="#64748B",
            justify="left",
        )
        self.rec_info_lbl.pack(anchor="w", padx=18, pady=(0, 14))

        # --- CARD 2: PLAY REPLAY ---
        rep_card = ctk.CTkFrame(
            action_frame,
            fg_color=("#FFFFFF", "#0B1329"),
            corner_radius=14,
            border_width=1,
            border_color=("#E2E8F0", "#1E2A4A"),
        )
        rep_card.grid(row=0, column=1, padx=(8, 0), pady=0, sticky="nsew")

        self.rep_header_lbl = ctk.CTkLabel(
            rep_card,
            text=t("replay.title"),
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=("#0891B2", "#0AC8B9"),
        )
        self.rep_header_lbl.pack(anchor="w", padx=18, pady=(14, 6))

        self.rep_status_pill = ctk.CTkLabel(
            rep_card,
            text=t("replay.status_idle"),
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=("#64748B", "#94A3B8"),
            fg_color=("#F8FAFC", "#070D1E"),
            corner_radius=8,
            padx=12,
            pady=6,
        )
        self.rep_status_pill.pack(anchor="w", padx=18, pady=(0, 12))

        self.rep_btn = ctk.CTkButton(
            rep_card,
            text=t("replay.start_btn"),
            font=ctk.CTkFont(size=14, weight="bold"),
            height=52,
            fg_color="#0284C7",
            hover_color="#0369A1",
            border_width=1,
            border_color=("#0284C7", "#38BDF8"),
            corner_radius=10,
            command=self._toggle_replay,
        )
        self.rep_btn.pack(fill="x", padx=18, pady=(0, 12))

        # Replay Volume Slider Control
        vol_frame = ctk.CTkFrame(
            rep_card,
            fg_color=("#F8FAFC", "#070D1E"),
            corner_radius=8,
            border_width=1,
            border_color=("#E2E8F0", "#162038"),
        )
        vol_frame.pack(fill="x", padx=18, pady=(0, 12))
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
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=("#0891B2", "#0AC8B9"),
            width=80,
            cursor="hand2",
        )
        self.vol_label.bind("<Button-1>", lambda e: self._toggle_replay_mute())
        self.vol_label.grid(row=0, column=0, padx=(12, 4), pady=8, sticky="w")

        self.vol_slider = ctk.CTkSlider(
            vol_frame,
            from_=0,
            to=100,
            number_of_steps=100,
            command=self._on_replay_volume_slider_changed,
            progress_color=("#0891B2", "#0AC8B9"),
            button_color=("#06B6D4", "#5EEAD4"),
            button_hover_color=("#0891B2", "#0AC8B9"),
            fg_color=("#E2E8F0", "#0B1329"),
            height=16,
        )
        self.vol_slider.set(self.current_vol)
        self.vol_slider.grid(row=0, column=1, padx=(4, 12), pady=8, sticky="ew")

        self.rep_info_lbl = ctk.CTkLabel(
            rep_card,
            text=f"{t('replay.info')}\n• {t('replay.hotkey_hint')}",
            font=ctk.CTkFont(size=11),
            text_color="#64748B",
            justify="left",
        )
        self.rep_info_lbl.pack(anchor="w", padx=18, pady=(0, 14))

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
                app_l, mic_l = native_rec.poll_audio_levels()
                is_discord_muted = getattr(native_rec, "is_discord_muted", lambda: False)()

                # Throttled redraw: only redraw app meter if level changed significantly
                last_app_l = getattr(self, "_last_rendered_app_l", -1.0)
                if abs(app_l - last_app_l) > 0.015 or (app_l == 0.0 and last_app_l > 0.0):
                    app_color = "#22C55E" if app_l < 0.65 else ("#EAB308" if app_l < 0.85 else "#EF4444")
                    if getattr(self, "_last_app_color", None) != app_color:
                        self.app_meter_bar.configure(progress_color=app_color)
                        self._last_app_color = app_color
                    self.app_meter_bar.set(app_l)
                    self._last_rendered_app_l = app_l

                if is_discord_muted:
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
                    # Throttled redraw: only redraw mic meter if level changed significantly
                    last_mic_l = getattr(self, "_last_rendered_mic_l", -1.0)
                    if abs(mic_l - last_mic_l) > 0.015 or (mic_l == 0.0 and last_mic_l > 0.0):
                        mic_color = "#0284C7" if mic_l < 0.65 else ("#EAB308" if mic_l < 0.85 else "#EF4444")
                        if getattr(self, "_last_mic_color", None) != mic_color:
                            self.mic_meter_bar.configure(progress_color=mic_color)
                            self._last_mic_color = mic_color
                        self.mic_meter_bar.set(mic_l)
                        self._last_rendered_mic_l = mic_l

                # Live HUD wave & status updates
                if getattr(self, "hud", None) and self.hud.is_visible:
                    if self.hud.mode == "RECORD":
                        rec_w = getattr(self, "recorder_worker", None)
                        game_time = getattr(rec_w, "last_game_time", 0.0) if rec_w else 0.0
                        time_str = (
                            f"{int(game_time // 60):02d}:{int(game_time % 60):02d}"
                            if game_time > 0
                            else "00:00"
                        )
                        is_p = getattr(rec_w, "is_paused", False) if rec_w else False
                        self.hud.update_recorder_state(
                            time_str=time_str,
                            mic_level=mic_l,
                            discord_level=app_l,
                            is_paused=is_p,
                            is_muted=is_discord_muted,
                        )
                    elif self.hud.mode == "REPLAY":
                        rep_w = getattr(self, "replay_worker", None)
                        if rep_w:
                            game_time = getattr(rep_w, "last_game_time", 0.0)
                            time_str = (
                                f"{int(game_time // 60):02d}:{int(game_time % 60):02d}"
                                if game_time > 0
                                else "00:00"
                            )
                            is_p = getattr(rep_w, "is_paused", False)
                            self.hud.update_replay_state(
                                time_str=time_str,
                                current_time=game_time,
                                total_duration=getattr(rep_w, "duration", 1800.0),
                                volume=self.config.get("replay_volume", 80),
                                is_paused=is_p,
                                is_muted=getattr(self, "_replay_muted", False),
                                speed=getattr(rep_w, "speed", 1.0),
                            )
        except Exception:
            pass
        self._meter_timer = self.after(75, self._update_audio_meters)

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
        self.open_folder_btn.grid(row=0, column=1, padx=6, sticky="e")

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
        self.refresh_rec_btn.grid(row=0, column=2, padx=4, sticky="e")

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
        scroll.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")
        scroll.grid_columnconfigure(0, weight=0)
        scroll.grid_columnconfigure(1, weight=1)
        scroll.grid_columnconfigure(2, weight=0)

        # --- Directory Settings ---
        self.dir_title_lbl = ctk.CTkLabel(
            scroll,
            text=t("settings.paths_title"),
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=("#0891B2", "#0AC8B9"),
        )
        self.dir_title_lbl.grid(row=0, column=0, columnspan=3, padx=10, pady=(12, 8), sticky="w")

        # Recordings Folder
        self.rec_folder_lbl = ctk.CTkLabel(scroll, text=t("settings.rec_folder"), text_color=("#0F172A", "#CBD5E1"))
        self.rec_folder_lbl.grid(row=1, column=0, padx=10, pady=6, sticky="w")
        self.rec_folder_entry = ctk.CTkEntry(
            scroll,
            fg_color=("#FFFFFF", "#0B1329"),
            border_color=("#CBD5E1", "#1E2A4A"),
            border_width=1,
            text_color=("#0F172A", "#F8FAFC"),
            corner_radius=6,
        )
        self.rec_folder_entry.insert(
            0, str(self.config.get("record_folder", os.path.abspath("recordings")))
        )
        self.rec_folder_entry.grid(row=1, column=1, padx=(10, 4), pady=6, sticky="ew")

        self.browse_rec_btn = ctk.CTkButton(
            scroll,
            text=t("settings.browse"),
            width=80,
            fg_color=("#F1F5F9", "#0F1A36"),
            hover_color=("#E2E8F0", "#1E2A4A"),
            border_width=1,
            border_color=("#CBD5E1", "#1E2A4A"),
            text_color=("#0F172A", "#E2E8F0"),
            corner_radius=6,
            command=self._browse_rec_folder,
        )
        self.browse_rec_btn.grid(row=1, column=2, padx=(4, 10), pady=6, sticky="w")

        # VLC Directory
        self.vlc_dir_lbl = ctk.CTkLabel(scroll, text=t("settings.vlc_dir"), text_color=("#0F172A", "#CBD5E1"))
        self.vlc_dir_lbl.grid(row=2, column=0, padx=10, pady=6, sticky="w")
        self.vlc_dir_entry = ctk.CTkEntry(
            scroll,
            fg_color=("#FFFFFF", "#0B1329"),
            border_color=("#CBD5E1", "#1E2A4A"),
            border_width=1,
            text_color=("#0F172A", "#F8FAFC"),
            corner_radius=6,
        )
        self.vlc_dir_entry.insert(
            0, str(self.config.get("vlc_dir", r"C:\Program Files\VideoLAN\VLC"))
        )
        self.vlc_dir_entry.grid(row=2, column=1, padx=(10, 4), pady=6, sticky="ew")

        self.browse_vlc_btn = ctk.CTkButton(
            scroll,
            text=t("settings.browse"),
            width=80,
            fg_color=("#F1F5F9", "#0F1A36"),
            hover_color=("#E2E8F0", "#1E2A4A"),
            border_width=1,
            border_color=("#CBD5E1", "#1E2A4A"),
            text_color=("#0F172A", "#E2E8F0"),
            corner_radius=6,
            command=self._browse_vlc_folder,
        )
        self.browse_vlc_btn.grid(row=2, column=2, padx=(4, 10), pady=6, sticky="w")

        # Replay Volume
        self.replay_vol_lbl = ctk.CTkLabel(scroll, text=t("settings.replay_volume"), text_color=("#0F172A", "#CBD5E1"))
        self.replay_vol_lbl.grid(row=3, column=0, padx=10, pady=6, sticky="w")
        self.replay_vol_entry = ctk.CTkEntry(
            scroll,
            fg_color=("#FFFFFF", "#0B1329"),
            border_color=("#CBD5E1", "#1E2A4A"),
            border_width=1,
            text_color=("#0F172A", "#F8FAFC"),
            corner_radius=6,
        )
        self.replay_vol_entry.insert(0, str(self.config.get("replay_volume", 80)))
        self.replay_vol_entry.grid(row=3, column=1, columnspan=2, padx=10, pady=6, sticky="ew")

        # Language Selector
        self.lang_code_to_name = get_available_languages()
        self.lang_name_to_code = {v: k for k, v in self.lang_code_to_name.items()}
        current_code = self.config.get("language", "en")
        current_display = self.lang_code_to_name.get(current_code, "English")

        self.lang_lbl = ctk.CTkLabel(scroll, text=t("settings.language"), text_color=("#0F172A", "#CBD5E1"))
        self.lang_lbl.grid(row=4, column=0, padx=10, pady=6, sticky="w")

        self.lang_menu = ctk.CTkOptionMenu(
            scroll,
            values=list(self.lang_name_to_code.keys()),
            command=self._on_language_selected,
            fg_color=("#FFFFFF", "#0B1329"),
            text_color=("#0F172A", "#F8FAFC"),
            button_color=("#0891B2", "#0AC8B9"),
            button_hover_color=("#0E7490", "#0397AB"),
            dropdown_fg_color=("#FFFFFF", "#0B1329"),
            dropdown_text_color=("#0F172A", "#F8FAFC"),
            dropdown_hover_color=("#F1F5F9", "#162038"),
            corner_radius=6,
        )
        self.lang_menu.set(current_display)
        self.lang_menu.grid(row=4, column=1, columnspan=2, padx=10, pady=6, sticky="ew")

        # Appearance Theme Selector
        self.theme_lbl = ctk.CTkLabel(scroll, text=t("settings.theme"), text_color=("#0F172A", "#CBD5E1"))
        self.theme_lbl.grid(row=5, column=0, padx=10, pady=6, sticky="w")

        self.theme_options = {
            t("settings.theme_dark"): "dark",
            t("settings.theme_light"): "light",
            t("settings.theme_system"): "system",
        }
        self.theme_code_to_display = {v: k for k, v in self.theme_options.items()}
        curr_theme_display = self.theme_code_to_display.get(self.current_theme, t("settings.theme_dark"))

        self.theme_menu = ctk.CTkOptionMenu(
            scroll,
            values=list(self.theme_options.keys()),
            command=self._on_theme_selected,
            fg_color=("#FFFFFF", "#0B1329"),
            text_color=("#0F172A", "#F8FAFC"),
            button_color=("#0891B2", "#0AC8B9"),
            button_hover_color=("#0E7490", "#0397AB"),
            dropdown_fg_color=("#FFFFFF", "#0B1329"),
            dropdown_text_color=("#0F172A", "#F8FAFC"),
            dropdown_hover_color=("#F1F5F9", "#162038"),
            corner_radius=6,
        )
        self.theme_menu.set(curr_theme_display)
        self.theme_menu.grid(row=5, column=1, columnspan=2, padx=10, pady=6, sticky="ew")

        # Microphone Input
        self.settings_mic_lbl = ctk.CTkLabel(scroll, text=t("settings.mic_device"), text_color=("#0F172A", "#CBD5E1"))
        self.settings_mic_lbl.grid(row=6, column=0, padx=10, pady=6, sticky="w")

        self.settings_mic_menu = ctk.CTkOptionMenu(
            scroll,
            values=[t("record.mic_default")],
            command=self._on_mic_input_selected,
            fg_color=("#FFFFFF", "#0B1329"),
            text_color=("#0F172A", "#F8FAFC"),
            button_color=("#0891B2", "#0AC8B9"),
            button_hover_color=("#0E7490", "#0397AB"),
            dropdown_fg_color=("#FFFFFF", "#0B1329"),
            dropdown_text_color=("#0F172A", "#F8FAFC"),
            dropdown_hover_color=("#F1F5F9", "#162038"),
            corner_radius=6,
        )
        self.settings_mic_menu.grid(row=6, column=1, columnspan=2, padx=10, pady=6, sticky="ew")

        # Auto-Start in Champion Select
        self.auto_champ_select_lbl = ctk.CTkLabel(scroll, text=t("settings.auto_start_champ_select"), text_color=("#0F172A", "#CBD5E1"))
        self.auto_champ_select_lbl.grid(row=7, column=0, padx=10, pady=6, sticky="w")
        self.auto_champ_select_var = ctk.BooleanVar(
            value=bool(self.config.get("auto_start_champ_select", True))
        )
        self.auto_champ_select_switch = ctk.CTkSwitch(
            scroll,
            text=t("settings.auto_start_champ_select_info"),
            variable=self.auto_champ_select_var,
            onvalue=True,
            offvalue=False,
            fg_color=("#CBD5E1", "#1E2A4A"),
            progress_color=("#0891B2", "#0AC8B9"),
            button_color=("#FFFFFF", "#FFFFFF"),
            button_hover_color=("#F8FAFC", "#E2E8F0"),
            text_color=("#334155", "#94A3B8"),
        )
        self.auto_champ_select_switch.grid(row=7, column=1, columnspan=2, padx=10, pady=6, sticky="w")

        # Auto-Start Replay Sync
        self.auto_replay_lbl = ctk.CTkLabel(scroll, text=t("settings.auto_start_replay"), text_color=("#0F172A", "#CBD5E1"))
        self.auto_replay_lbl.grid(row=8, column=0, padx=10, pady=6, sticky="w")
        self.auto_replay_var = ctk.BooleanVar(
            value=bool(self.config.get("auto_start_replay", True))
        )
        self.auto_replay_switch = ctk.CTkSwitch(
            scroll,
            text=t("settings.auto_start_replay_info"),
            variable=self.auto_replay_var,
            onvalue=True,
            offvalue=False,
            fg_color=("#CBD5E1", "#1E2A4A"),
            progress_color=("#0891B2", "#0AC8B9"),
            button_color=("#FFFFFF", "#FFFFFF"),
            button_hover_color=("#F8FAFC", "#E2E8F0"),
            text_color=("#334155", "#94A3B8"),
        )
        self.auto_replay_switch.grid(row=8, column=1, columnspan=2, padx=10, pady=6, sticky="w")

        # --- In-Game HUD Overlay Section ---
        self.hud_title_lbl = ctk.CTkLabel(
            scroll,
            text=t("settings.hud_section_title"),
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=("#0891B2", "#0AC8B9"),
        )
        self.hud_title_lbl.grid(row=9, column=0, columnspan=3, padx=10, pady=(16, 6), sticky="w")

        # Enable HUD Overlay Switch
        self.hud_enable_lbl = ctk.CTkLabel(scroll, text=t("settings.hud_overlay_enabled"), text_color=("#0F172A", "#CBD5E1"))
        self.hud_enable_lbl.grid(row=10, column=0, padx=10, pady=6, sticky="w")
        self.hud_enable_var = ctk.BooleanVar(
            value=bool(self.config.get("hud_overlay_enabled", True))
        )
        self.hud_enable_switch = ctk.CTkSwitch(
            scroll,
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
        self.hud_enable_switch.grid(row=10, column=1, columnspan=2, padx=10, pady=6, sticky="w")

        # Click-Through Mode Switch
        self.hud_click_through_lbl = ctk.CTkLabel(scroll, text=t("settings.hud_click_through"), text_color=("#0F172A", "#CBD5E1"))
        self.hud_click_through_lbl.grid(row=11, column=0, padx=10, pady=6, sticky="w")
        self.hud_click_through_var = ctk.BooleanVar(
            value=bool(self.config.get("hud_click_through", False))
        )
        self.hud_click_through_switch = ctk.CTkSwitch(
            scroll,
            text=t("settings.hud_click_through_info"),
            variable=self.hud_click_through_var,
            onvalue=True,
            offvalue=False,
            fg_color=("#CBD5E1", "#1E2A4A"),
            progress_color=("#0891B2", "#0AC8B9"),
            button_color=("#FFFFFF", "#FFFFFF"),
            button_hover_color=("#F8FAFC", "#E2E8F0"),
            text_color=("#334155", "#94A3B8"),
            command=self._on_hud_click_through_toggled,
        )
        self.hud_click_through_switch.grid(row=11, column=1, columnspan=2, padx=10, pady=6, sticky="w")

        # HUD Opacity Slider
        curr_opacity = float(self.config.get("hud_opacity", 0.85))
        self.hud_opacity_lbl = ctk.CTkLabel(
            scroll,
            text=t("settings.hud_opacity", percent=int(curr_opacity * 100)),
            text_color=("#0F172A", "#CBD5E1"),
        )
        self.hud_opacity_lbl.grid(row=12, column=0, padx=10, pady=6, sticky="w")

        hud_slider_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        hud_slider_frame.grid(row=12, column=1, columnspan=2, padx=10, pady=6, sticky="ew")
        hud_slider_frame.grid_columnconfigure(0, weight=1)

        self.hud_opacity_slider = ctk.CTkSlider(
            hud_slider_frame,
            from_=0.40,
            to=1.00,
            number_of_steps=12,
            fg_color=("#CBD5E1", "#1E2A4A"),
            progress_color=("#0891B2", "#0AC8B9"),
            button_color=("#0891B2", "#0AC8B9"),
            button_hover_color=("#0E7490", "#0397AB"),
            command=self._on_hud_opacity_slider_changed,
        )
        self.hud_opacity_slider.set(curr_opacity)
        self.hud_opacity_slider.pack(fill="x", expand=True)

        # HUD Action / Preview Buttons
        hud_btn_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        hud_btn_frame.grid(row=13, column=0, columnspan=3, padx=10, pady=(8, 12), sticky="w")

        self.hud_preview_rec_btn = ctk.CTkButton(
            hud_btn_frame,
            text=t("settings.hud_preview_rec_btn"),
            font=ctk.CTkFont(size=12, weight="bold"),
            width=140,
            height=32,
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
            font=ctk.CTkFont(size=12, weight="bold"),
            width=150,
            height=32,
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
            font=ctk.CTkFont(size=12, weight="bold"),
            width=120,
            height=32,
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
            font=ctk.CTkFont(size=12, weight="bold"),
            width=130,
            height=32,
            fg_color=("#F1F5F9", "#0F1A36"),
            hover_color=("#E2E8F0", "#1E2A4A"),
            border_width=1,
            border_color=("#CBD5E1", "#1E2A4A"),
            text_color=("#0F172A", "#E2E8F0"),
            corner_radius=6,
            command=self._reset_hud_position,
        )
        self.hud_reset_pos_btn.pack(side="left", padx=(0, 8))

        # Save Button
        self.save_btn = ctk.CTkButton(
            scroll,
            text=t("settings.save_btn"),
            height=40,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color=("#0891B2", "#0AC8B9"),
            hover_color=("#0E7490", "#0397AB"),
            text_color=("#FFFFFF", "#030712"),
            corner_radius=8,
            command=self._save_settings,
        )
        self.save_btn.grid(row=14, column=0, columnspan=3, padx=10, pady=(20, 10), sticky="ew")

        # --- Software Updates Section ---
        self.updates_title_lbl = ctk.CTkLabel(
            scroll,
            text=t("settings.updates_title"),
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=("#0891B2", "#0AC8B9"),
        )
        self.updates_title_lbl.grid(row=15, column=0, columnspan=3, padx=10, pady=(18, 6), sticky="w")

        self.app_version_lbl = ctk.CTkLabel(scroll, text=t("settings.app_version"), text_color=("#0F172A", "#CBD5E1"))
        self.app_version_lbl.grid(row=16, column=0, padx=10, pady=6, sticky="w")

        version_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        version_frame.grid(row=16, column=1, columnspan=2, padx=10, pady=6, sticky="ew")

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
            font=ctk.CTkFont(size=12),
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
            scroll,
            text="",
            font=ctk.CTkFont(size=11),
            text_color=("#64748B", "#94A3B8"),
        )
        self.update_status_lbl.grid(row=17, column=0, columnspan=3, padx=10, pady=(4, 0), sticky="w")

        self.update_action_btn = ctk.CTkButton(
            scroll,
            text="",
            height=36,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#10B981",
            hover_color="#059669",
            corner_radius=8,
            command=self._on_perform_update_clicked,
        )

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
        save_config({"hud_x": x, "hud_y": y})

    def _on_hud_enabled_toggled(self):
        enabled = bool(self.hud_enable_var.get())
        self.config["hud_overlay_enabled"] = enabled
        if not enabled and self.hud:
            self.hud.hide()

    def _on_hud_click_through_toggled(self):
        ct = bool(self.hud_click_through_var.get())
        self.config["hud_click_through"] = ct
        if self.hud:
            self.hud.set_click_through(ct)

    def _on_hud_opacity_slider_changed(self, val):
        op = round(float(val), 2)
        self.config["hud_opacity"] = op
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
                self._refresh_audio_targets()
            if hasattr(self, "mic_input_lbl"):
                self.mic_input_lbl.configure(text=t("record.mic_input"))
                self._refresh_microphones()
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
                self._refresh_recordings_list()

            # Settings tab
            if hasattr(self, "rec_scene_lbl"):
                self.rec_scene_lbl.configure(text=self._get_engine_display_text())

            if hasattr(self, "dir_title_lbl"):
                self.dir_title_lbl.configure(text=t("settings.paths_title"))
                self.rec_folder_lbl.configure(text=t("settings.rec_folder"))
                self.browse_rec_btn.configure(text=t("settings.browse"))
                self.vlc_dir_lbl.configure(text=t("settings.vlc_dir"))
                self.browse_vlc_btn.configure(text=t("settings.browse"))
                self.replay_vol_lbl.configure(text=t("settings.replay_volume"))
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
                if hasattr(self, "auto_champ_select_lbl"):
                    self.auto_champ_select_lbl.configure(text=t("settings.auto_start_champ_select"))
                    self.auto_champ_select_switch.configure(text=t("settings.auto_start_champ_select_info"))
                if hasattr(self, "auto_replay_lbl"):
                    self.auto_replay_lbl.configure(text=t("settings.auto_start_replay"))
                    self.auto_replay_switch.configure(text=t("settings.auto_start_replay_info"))
                if hasattr(self, "hud_title_lbl"):
                    self.hud_title_lbl.configure(text=t("settings.hud_section_title"))
                    self.hud_enable_lbl.configure(text=t("settings.hud_overlay_enabled"))
                    self.hud_enable_switch.configure(text=t("settings.hud_overlay_info"))
                    self.hud_click_through_lbl.configure(text=t("settings.hud_click_through"))
                    self.hud_click_through_switch.configure(text=t("settings.hud_click_through_info"))
                    self.hud_opacity_lbl.configure(
                        text=t("settings.hud_opacity", percent=int(self.hud_opacity_slider.get() * 100))
                    )
                    self.hud_preview_rec_btn.configure(text=t("settings.hud_preview_rec_btn"))
                    self.hud_preview_replay_btn.configure(text=t("settings.hud_preview_replay_btn"))
                    self.hud_hide_preview_btn.configure(text=t("settings.hud_hide_preview_btn"))
                    self.hud_reset_pos_btn.configure(text=t("settings.hud_reset_pos_btn"))
                self.save_btn.configure(text=t("settings.save_btn"))
                if hasattr(self, "updates_title_lbl"):
                    self.updates_title_lbl.configure(text=t("settings.updates_title"))
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
                        if status in ("RECORDING", "PAUSED"):
                            self.hud.set_mode("RECORD")
                            self.hud.show()
                        elif status in ("SAVED", "STOPPED", "IDLE") and not self._is_hud_previewing:
                            self.hud.hide()

                elif action == "STATUS_REPLAY":
                    status, detail = item[1], item[2]
                    self._render_replay_status(status, detail)
                    if getattr(self, "hud", None) and self.config.get("hud_overlay_enabled", True):
                        if status in ("SYNCING", "PAUSED"):
                            self.hud.set_mode("REPLAY")
                            self.hud.show()
                            game_time = (
                                getattr(self.replay_worker, "last_game_time", 0.0)
                                if self.replay_worker
                                else 0.0
                            )
                            time_str = (
                                f"{int(game_time // 60):02d}:{int(game_time % 60):02d}"
                                if game_time > 0
                                else "00:00"
                            )
                            is_p = (status == "PAUSED") or (
                                self.replay_worker and getattr(self.replay_worker, "is_paused", False)
                            )
                            self.hud.update_replay_state(
                                time_str=time_str,
                                current_time=game_time,
                                total_duration=getattr(self.replay_worker, "duration", 1800.0),
                                volume=int(self.config.get("replay_volume", 80)),
                                is_paused=is_p,
                                is_muted=getattr(self, "_replay_muted", False),
                                speed=getattr(self.replay_worker, "speed", 1.0)
                                if self.replay_worker
                                else 1.0,
                            )
                        elif status in ("STOPPED", "IDLE") and not self._is_hud_previewing:
                            self.hud.hide()

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

    def _stop_recording(self):
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
            # 1. Champion Select Live Match Watcher Auto-Start
            if self.config.get("auto_start_champ_select", True) and not self.recorder_worker:
                try:
                    phase = get_lcu_gameflow_phase()
                    if phase == "ChampSelect":
                        if getattr(self, "_last_auto_started_phase", None) != "ChampSelect":
                            self._last_auto_started_phase = "ChampSelect"
                            self.msg_queue.put(("AUTO_START_REC",))
                    elif phase in ("None", "Lobby", "EndOfGame", "WaitingForStats", None):
                        self._last_auto_started_phase = None
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
                self.update_action_btn.grid(row=14, column=0, columnspan=3, padx=10, pady=(6, 12), sticky="ew")

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
        folder = self.config.get("record_folder", os.path.abspath("recordings"))
        os.makedirs(folder, exist_ok=True)
        if os.name == "nt":
            os.startfile(folder)
        else:
            subprocess.run(["xdg-open", folder])

    def _refresh_recordings_list(self):
        for widget in self.rec_scroll_frame.winfo_children():
            widget.destroy()

        folder = self.config.get("record_folder", os.path.abspath("recordings"))
        if not os.path.exists(folder):
            os.makedirs(folder, exist_ok=True)

        files = [f for f in os.listdir(folder) if f.lower().endswith(".mp3")]
        files.sort(key=lambda x: os.path.getmtime(os.path.join(folder, x)), reverse=True)

        total_size_bytes = sum(os.path.getsize(os.path.join(folder, f)) for f in files)
        total_size_mb = total_size_bytes / (1024 * 1024)
        self.rec_count_lbl.configure(
            text=t("recordings.count_label", count=len(files), size=total_size_mb)
        )

        if not files:
            empty_lbl = ctk.CTkLabel(
                self.rec_scroll_frame,
                text=t("recordings.empty", folder=folder),
                font=ctk.CTkFont(size=12, slant="italic"),
                text_color="#64748B",
            )
            empty_lbl.pack(pady=20)
            return

        for idx, filename in enumerate(files):
            filepath = os.path.join(folder, filename)
            mtime = os.path.getmtime(filepath)
            dt_str = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
            size_mb = os.path.getsize(filepath) / (1024 * 1024)

            row = ctk.CTkFrame(
                self.rec_scroll_frame,
                fg_color=("#F1F5F9", "#0B1329") if idx % 2 == 0 else ("#E2E8F0", "#0E1833"),
                corner_radius=8,
                border_width=1,
                border_color=("#CBD5E1", "#162038"),
            )
            row.pack(fill="x", padx=6, pady=4)
            row.grid_columnconfigure(0, weight=1)

            name_lbl = ctk.CTkLabel(
                row,
                text=f"🎵 {filename}",
                font=ctk.CTkFont(size=13, weight="bold"),
                text_color=("#0F172A", "#F8FAFC"),
            )
            name_lbl.grid(row=0, column=0, padx=14, pady=(8, 2), sticky="w")

            meta_lbl = ctk.CTkLabel(
                row,
                text=t("recordings.meta", size=size_mb, date=dt_str),
                font=ctk.CTkFont(size=11),
                text_color=("#64748B", "#94A3B8"),
            )
            meta_lbl.grid(row=1, column=0, padx=14, pady=(0, 8), sticky="w")

            play_btn = ctk.CTkButton(
                row,
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
            play_btn.grid(row=0, column=1, rowspan=2, padx=12, pady=8, sticky="e")

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
        curr = self.rec_folder_entry.get().strip() or os.path.abspath("recordings")
        selected = filedialog.askdirectory(initialdir=curr, title=t("settings.rec_folder"))
        if selected:
            self.rec_folder_entry.delete(0, "end")
            self.rec_folder_entry.insert(0, selected)

    def _browse_vlc_folder(self):
        curr = self.vlc_dir_entry.get().strip() or r"C:\Program Files\VideoLAN\VLC"
        selected = filedialog.askdirectory(initialdir=curr, title=t("settings.vlc_dir"))
        if selected:
            self.vlc_dir_entry.delete(0, "end")
            self.vlc_dir_entry.insert(0, selected)

    def _save_settings(self):
        try:
            vol_val = int(self.replay_vol_entry.get().strip() or 80)
            vol_val = max(0, min(100, vol_val))
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
                "check_time": float(self.config.get("check_time", 0.2)),
                "replay_volume": vol_val,
                "language": selected_lang_code,
                "theme": selected_theme_code,
                "auto_start_champ_select": bool(self.auto_champ_select_var.get()),
                "auto_start_replay": bool(self.auto_replay_var.get()),
                "hud_overlay_enabled": bool(self.hud_enable_var.get()) if hasattr(self, "hud_enable_var") else True,
                "hud_click_through": bool(self.hud_click_through_var.get()) if hasattr(self, "hud_click_through_var") else False,
                "hud_opacity": round(float(self.hud_opacity_slider.get()), 2) if hasattr(self, "hud_opacity_slider") else 0.85,
            }
            if save_config(new_config):
                self.config = new_config
                set_language(selected_lang_code)
                self._set_theme(selected_theme_code)
                if self.hud:
                    self.hud.set_click_through(new_config["hud_click_through"])
                    self.hud.set_opacity(new_config["hud_opacity"])
                    if not new_config["hud_overlay_enabled"]:
                        self.hud.hide()
                self.vol_slider.set(vol_val)
                self._on_replay_volume_slider_changed(vol_val)
                self.rec_scene_lbl.configure(
                    text=self._get_engine_display_text()
                )
                messagebox.showinfo(
                    t("tabs.settings"),
                    t("settings.saved_success"),
                )
                self._check_system_health()
                self._refresh_recordings_list()
            else:
                messagebox.showerror(
                    t("tabs.settings"),
                    t("settings.saved_error"),
                )
        except Exception as e:
            messagebox.showerror(
                t("tabs.settings"),
                t("settings.invalid_input", error=e),
            )

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
    def _on_close(self):
        # 1. Warn if active match recording is in progress
        if self.recorder_worker and self.recorder_worker.recording:
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

        # 2. Instant visual responsiveness: hide window and taskbar icon immediately (< 1ms)
        try:
            if hasattr(self, "hud") and self.hud:
                self.hud.hide()
            self.withdraw()
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
    app.mainloop()
    try:
        os._exit(0)
    except Exception:
        pass


if __name__ == "__main__":
    main()
