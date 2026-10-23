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
from app.native_audio import get_available_audio_targets
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

        # Initialize i18n
        set_language(self.config.get("language", "en"))
        on_language_change(self._update_ui_texts)

        self.title(f"{t('app.window_title')} v{__version__}")
        self.geometry("900x740")
        self.minsize(720, 520)

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
        self._start_audio_meter_loop()

        # Handle window close
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _apply_window_icon(self):
        """Set window title bar and taskbar icon with multi-resolution support."""
        app_dir = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        nuitka_dir = os.environ.get("NUITKA_ONEFILE_DIRECTORY")
        ico_path = None
        for candidate in [
            os.path.join(nuitka_dir, "assets", "icon.ico") if nuitka_dir else "",
            os.path.join(nuitka_dir, "icon.ico") if nuitka_dir else "",
            os.path.join(app_dir, "assets", "icon.ico"),
            os.path.join(app_dir, "icon.ico"),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "icon.ico"),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.ico"),
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
        header_frame = ctk.CTkFrame(self, fg_color="#1E293B", corner_radius=10)
        header_frame.grid(row=0, column=0, padx=15, pady=(15, 8), sticky="ew")
        header_frame.grid_columnconfigure(0, weight=1)

        title_box = ctk.CTkFrame(header_frame, fg_color="transparent")
        title_box.grid(row=0, column=0, padx=16, pady=(12, 6), sticky="w")

        self.title_lbl = ctk.CTkLabel(
            title_box,
            text=t("app.title"),
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color="#38BDF8",
        )
        self.title_lbl.pack(anchor="w")

        self.sub_lbl = ctk.CTkLabel(
            title_box,
            text=t("app.subtitle"),
            font=ctk.CTkFont(size=12),
            text_color="#94A3B8",
        )
        self.sub_lbl.pack(anchor="w")

        # Health Badges Frame
        health_frame = ctk.CTkFrame(header_frame, fg_color="#0F172A", corner_radius=8)
        health_frame.grid(row=1, column=0, padx=16, pady=(0, 12), sticky="ew")

        self.discord_badge = ctk.CTkLabel(
            health_frame,
            text=f"{t('health.discord')} {t('health.checking')}",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#CBD5E1",
        )
        self.discord_badge.pack(side="left", padx=12, pady=6)

        self.lol_badge = ctk.CTkLabel(
            health_frame,
            text=f"{t('health.league')} {t('health.checking')}",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#CBD5E1",
        )
        self.lol_badge.pack(side="left", padx=12, pady=6)

        self.vlc_badge = ctk.CTkLabel(
            health_frame,
            text=f"{t('health.vlc')} {t('health.checking')}",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#CBD5E1",
        )
        self.vlc_badge.pack(side="left", padx=12, pady=6)

        self.ffmpeg_badge = ctk.CTkLabel(
            health_frame,
            text=f"{t('health.ffmpeg')} {t('health.checking')}",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#CBD5E1",
        )
        self.ffmpeg_badge.pack(side="left", padx=12, pady=6)

        self.refresh_health_btn = ctk.CTkButton(
            health_frame,
            text=t("health.refresh"),
            width=80,
            height=26,
            font=ctk.CTkFont(size=11),
            fg_color="#334155",
            hover_color="#475569",
            command=self._check_system_health,
        )
        self.refresh_health_btn.pack(side="right", padx=10, pady=4)

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
            fg_color="#1E293B",
            corner_radius=12,
            border_width=1,
            border_color="#334155",
        )
        rec_card.grid(row=0, column=0, padx=(0, 8), pady=0, sticky="nsew")

        self.rec_header_lbl = ctk.CTkLabel(
            rec_card,
            text=t("record.title"),
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#F43F5E",
        )
        self.rec_header_lbl.pack(anchor="w", padx=16, pady=(12, 4))

        self.rec_status_pill = ctk.CTkLabel(
            rec_card,
            text=t("record.status_idle"),
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#94A3B8",
            fg_color="#0F172A",
            corner_radius=6,
            padx=10,
            pady=4,
        )
        self.rec_status_pill.pack(anchor="w", padx=16, pady=(0, 10))

        self.rec_btn = ctk.CTkButton(
            rec_card,
            text=t("record.start_btn"),
            font=ctk.CTkFont(size=14, weight="bold"),
            height=50,
            fg_color="#E11D48",
            hover_color="#BE123C",
            corner_radius=8,
            command=self._toggle_recording,
        )
        self.rec_btn.pack(fill="x", padx=16, pady=(0, 10))

        # Target Voice Capture Pill
        rec_scene_pill = ctk.CTkFrame(rec_card, fg_color="#0F172A", corner_radius=8)
        rec_scene_pill.pack(fill="x", padx=16, pady=(0, 10))
        self.rec_scene_lbl = ctk.CTkLabel(
            rec_scene_pill,
            text=self._get_engine_display_text(),
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#F43F5E",
        )
        self.rec_scene_lbl.pack(padx=10, pady=6, anchor="w")

        # Program selector frame
        prog_frame = ctk.CTkFrame(rec_card, fg_color="#0F172A", corner_radius=8)
        prog_frame.pack(fill="x", padx=16, pady=(0, 10))
        prog_frame.grid_columnconfigure(0, weight=1)
        prog_frame.grid_columnconfigure(1, weight=0)

        self.target_prog_lbl = ctk.CTkLabel(
            prog_frame,
            text=t("record.target_app"),
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#38BDF8",
        )
        self.target_prog_lbl.grid(row=0, column=0, columnspan=2, padx=10, pady=(6, 2), sticky="w")

        self.available_targets = []
        self.target_prog_menu = ctk.CTkOptionMenu(
            prog_frame,
            values=["Discord (Auto Detect Active Audio) 🎯"],
            command=self._on_target_prog_selected,
            fg_color="#1E293B",
            button_color="#0284C7",
            button_hover_color="#0369A1",
        )
        self.target_prog_menu.grid(row=1, column=0, padx=(10, 4), pady=(0, 8), sticky="ew")

        self.refresh_progs_btn = ctk.CTkButton(
            prog_frame,
            text="🔄",
            width=36,
            fg_color="#334155",
            hover_color="#475569",
            command=self._refresh_audio_targets,
        )
        self.refresh_progs_btn.grid(row=1, column=1, padx=(0, 10), pady=(0, 8))

        # Live Audio VU Meter Frame (Hangcsik)
        meter_frame = ctk.CTkFrame(rec_card, fg_color="#0F172A", corner_radius=8)
        meter_frame.pack(fill="x", padx=16, pady=(0, 10))
        meter_frame.grid_columnconfigure(1, weight=1)

        # App Audio Meter
        self.app_meter_title = ctk.CTkLabel(
            meter_frame,
            text="🔊 " + t("record.app_sound") + ":",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color="#94A3B8",
        )
        self.app_meter_title.grid(row=0, column=0, padx=(10, 6), pady=(6, 2), sticky="w")

        self.app_meter_bar = ctk.CTkProgressBar(
            meter_frame,
            height=8,
            corner_radius=4,
            fg_color="#1E293B",
            progress_color="#22C55E",
        )
        self.app_meter_bar.set(0.0)
        self.app_meter_bar.grid(row=0, column=1, padx=(0, 10), pady=(6, 2), sticky="ew")

        # Mic Meter
        self.mic_meter_title = ctk.CTkLabel(
            meter_frame,
            text="🎤 " + t("record.microphone") + ":",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color="#94A3B8",
        )
        self.mic_meter_title.grid(row=1, column=0, padx=(10, 6), pady=(2, 8), sticky="w")

        self.mic_meter_bar = ctk.CTkProgressBar(
            meter_frame,
            height=8,
            corner_radius=4,
            fg_color="#1E293B",
            progress_color="#0284C7",
        )
        self.mic_meter_bar.set(0.0)
        self.mic_meter_bar.grid(row=1, column=1, padx=(0, 10), pady=(2, 8), sticky="ew")

        self.rec_info_lbl = ctk.CTkLabel(
            rec_card,
            text=t("record.info"),
            font=ctk.CTkFont(size=11),
            text_color="#94A3B8",
            justify="left",
        )
        self.rec_info_lbl.pack(anchor="w", padx=16, pady=(0, 12))

        # --- CARD 2: PLAY REPLAY ---
        rep_card = ctk.CTkFrame(
            action_frame,
            fg_color="#1E293B",
            corner_radius=12,
            border_width=1,
            border_color="#334155",
        )
        rep_card.grid(row=0, column=1, padx=(8, 0), pady=0, sticky="nsew")

        self.rep_header_lbl = ctk.CTkLabel(
            rep_card,
            text=t("replay.title"),
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#38BDF8",
        )
        self.rep_header_lbl.pack(anchor="w", padx=16, pady=(12, 4))

        self.rep_status_pill = ctk.CTkLabel(
            rep_card,
            text=t("replay.status_idle"),
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#94A3B8",
            fg_color="#0F172A",
            corner_radius=6,
            padx=10,
            pady=4,
        )
        self.rep_status_pill.pack(anchor="w", padx=16, pady=(0, 10))

        self.rep_btn = ctk.CTkButton(
            rep_card,
            text=t("replay.start_btn"),
            font=ctk.CTkFont(size=14, weight="bold"),
            height=50,
            fg_color="#0284C7",
            hover_color="#0369A1",
            corner_radius=8,
            command=self._toggle_replay,
        )
        self.rep_btn.pack(fill="x", padx=16, pady=(0, 10))

        # Replay Volume Slider Control
        vol_frame = ctk.CTkFrame(rep_card, fg_color="#0F172A", corner_radius=8)
        vol_frame.pack(fill="x", padx=16, pady=(0, 10))
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
            text_color="#38BDF8",
            width=65,
        )
        self.vol_label.grid(row=0, column=0, padx=(10, 4), pady=6, sticky="w")

        self.vol_slider = ctk.CTkSlider(
            vol_frame,
            from_=0,
            to=100,
            number_of_steps=100,
            command=self._on_replay_volume_slider_changed,
            progress_color="#0284C7",
            button_color="#38BDF8",
            button_hover_color="#0EA5E9",
            height=16,
        )
        self.vol_slider.set(self.current_vol)
        self.vol_slider.grid(row=0, column=1, padx=(4, 10), pady=6, sticky="ew")

        self.rep_info_lbl = ctk.CTkLabel(
            rep_card,
            text=t("replay.info"),
            font=ctk.CTkFont(size=11),
            text_color="#94A3B8",
            justify="left",
        )
        self.rep_info_lbl.pack(anchor="w", padx=16, pady=(0, 12))

        # ==========================================
        # 3. TABBED VIEW (ACTIVITY LOG, RECORDINGS, SETTINGS)
        # ==========================================
        self.tabview = ctk.CTkTabview(
            self,
            fg_color="#1E293B",
            segmented_button_fg_color="#0F172A",
            segmented_button_selected_color="#0284C7",
            segmented_button_selected_hover_color="#0369A1",
        )
        self.tabview.grid(row=2, column=0, padx=15, pady=(4, 15), sticky="nsew")

        tab_log = self.tabview.add("log")
        tab_recordings = self.tabview.add("recordings")
        tab_settings = self.tabview.add("settings")

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

    def _start_audio_meter_loop(self):
        self._update_audio_meters()

    def _update_audio_meters(self):
        if getattr(self, "_is_destroyed", False):
            return
        try:
            # We can use recorder_worker's native_recorder if active, or inspect via NativeAudioRecorder directly
            native_rec = None
            if hasattr(self, "recorder_worker") and self.recorder_worker and hasattr(self.recorder_worker, "native_recorder"):
                native_rec = self.recorder_worker.native_recorder
            elif not hasattr(self, "_preview_native_recorder") or self._preview_native_recorder is None:
                from app.native_audio import NativeAudioRecorder
                self._preview_native_recorder = NativeAudioRecorder(
                    record_folder=self.config.get("record_folder"),
                    mic_device=self.config.get("mic_device_index"),
                    target_pid=int(self.config.get("target_audio_pid", -1)),
                    target_name=self.config.get("target_audio_name", "Discord"),
                )
                native_rec = self._preview_native_recorder
            else:
                native_rec = self._preview_native_recorder

            if native_rec:
                app_l, mic_l = native_rec.poll_audio_levels()

                # Dynamic color based on amplitude, only reconfigure when changed
                app_color = "#22C55E" if app_l < 0.65 else ("#EAB308" if app_l < 0.85 else "#EF4444")
                if getattr(self, "_last_app_color", None) != app_color:
                    self.app_meter_bar.configure(progress_color=app_color)
                    self._last_app_color = app_color
                self.app_meter_bar.set(app_l)

                mic_color = "#0284C7" if mic_l < 0.65 else ("#EAB308" if mic_l < 0.85 else "#EF4444")
                if getattr(self, "_last_mic_color", None) != mic_color:
                    self.mic_meter_bar.configure(progress_color=mic_color)
                    self._last_mic_color = mic_color
                self.mic_meter_bar.set(mic_l)
        except Exception:
            pass
        self._meter_timer = self.after(60, self._update_audio_meters)

    def _setup_tab_log(self, parent):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(0, weight=1)

        # Log Text Box (expands vertically and horizontally)
        self.log_textbox = ctk.CTkTextbox(
            parent,
            font=ctk.CTkFont(family="Consolas", size=12),
            fg_color="#0F172A",
            text_color="#F1F5F9",
            corner_radius=8,
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
        )
        self.autoscroll_cb.pack(side="left")

        self.clear_btn = ctk.CTkButton(
            log_ctrl_frame,
            text=t("log.clear"),
            width=100,
            height=28,
            font=ctk.CTkFont(size=12),
            fg_color="#334155",
            hover_color="#475569",
            command=self._clear_log,
        )
        self.clear_btn.pack(side="right", padx=(6, 0))

        self.copy_btn = ctk.CTkButton(
            log_ctrl_frame,
            text=t("log.copy"),
            width=100,
            height=28,
            font=ctk.CTkFont(size=12),
            fg_color="#334155",
            hover_color="#475569",
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
            text_color="#E2E8F0",
        )
        self.rec_count_lbl.grid(row=0, column=0, sticky="w")

        self.open_folder_btn = ctk.CTkButton(
            rec_top_bar,
            text=t("recordings.open_folder"),
            height=30,
            font=ctk.CTkFont(size=12),
            fg_color="#0284C7",
            hover_color="#0369A1",
            command=self._open_recordings_folder,
        )
        self.open_folder_btn.grid(row=0, column=1, padx=6, sticky="e")

        self.refresh_rec_btn = ctk.CTkButton(
            rec_top_bar,
            text=t("recordings.refresh"),
            width=80,
            height=30,
            font=ctk.CTkFont(size=12),
            fg_color="#334155",
            hover_color="#475569",
            command=self._refresh_recordings_list,
        )
        self.refresh_rec_btn.grid(row=0, column=2, padx=4, sticky="e")

        # Scrollable Frame for List of Recordings
        self.rec_scroll_frame = ctk.CTkScrollableFrame(
            parent,
            fg_color="#0F172A",
            corner_radius=8,
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

        scroll = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        scroll.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")
        scroll.grid_columnconfigure(0, weight=0)
        scroll.grid_columnconfigure(1, weight=1)
        scroll.grid_columnconfigure(2, weight=0)

        # --- Directory Settings ---
        self.dir_title_lbl = ctk.CTkLabel(
            scroll,
            text=t("settings.paths_title"),
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#38BDF8",
        )
        self.dir_title_lbl.grid(row=0, column=0, columnspan=3, padx=10, pady=(12, 8), sticky="w")

        # Recordings Folder
        self.rec_folder_lbl = ctk.CTkLabel(scroll, text=t("settings.rec_folder"))
        self.rec_folder_lbl.grid(row=1, column=0, padx=10, pady=6, sticky="w")
        self.rec_folder_entry = ctk.CTkEntry(scroll)
        self.rec_folder_entry.insert(
            0, str(self.config.get("record_folder", os.path.abspath("recordings")))
        )
        self.rec_folder_entry.grid(row=1, column=1, padx=(10, 4), pady=6, sticky="ew")

        self.browse_rec_btn = ctk.CTkButton(
            scroll,
            text=t("settings.browse"),
            width=80,
            fg_color="#334155",
            hover_color="#475569",
            command=self._browse_rec_folder,
        )
        self.browse_rec_btn.grid(row=1, column=2, padx=(4, 10), pady=6, sticky="w")

        # VLC Directory
        self.vlc_dir_lbl = ctk.CTkLabel(scroll, text=t("settings.vlc_dir"))
        self.vlc_dir_lbl.grid(row=2, column=0, padx=10, pady=6, sticky="w")
        self.vlc_dir_entry = ctk.CTkEntry(scroll)
        self.vlc_dir_entry.insert(
            0, str(self.config.get("vlc_dir", r"C:\Program Files\VideoLAN\VLC"))
        )
        self.vlc_dir_entry.grid(row=2, column=1, padx=(10, 4), pady=6, sticky="ew")

        self.browse_vlc_btn = ctk.CTkButton(
            scroll,
            text=t("settings.browse"),
            width=80,
            fg_color="#334155",
            hover_color="#475569",
            command=self._browse_vlc_folder,
        )
        self.browse_vlc_btn.grid(row=2, column=2, padx=(4, 10), pady=6, sticky="w")

        # Polling Interval
        self.check_time_lbl = ctk.CTkLabel(scroll, text=t("settings.check_interval"))
        self.check_time_lbl.grid(row=3, column=0, padx=10, pady=6, sticky="w")
        self.check_time_entry = ctk.CTkEntry(scroll)
        self.check_time_entry.insert(0, str(self.config.get("check_time", 0.2)))
        self.check_time_entry.grid(row=3, column=1, columnspan=2, padx=10, pady=6, sticky="ew")

        # Replay Volume
        self.replay_vol_lbl = ctk.CTkLabel(scroll, text=t("settings.replay_volume"))
        self.replay_vol_lbl.grid(row=4, column=0, padx=10, pady=6, sticky="w")
        self.replay_vol_entry = ctk.CTkEntry(scroll)
        self.replay_vol_entry.insert(0, str(self.config.get("replay_volume", 80)))
        self.replay_vol_entry.grid(row=4, column=1, columnspan=2, padx=10, pady=6, sticky="ew")

        # Language Selector
        self.lang_code_to_name = get_available_languages()
        self.lang_name_to_code = {v: k for k, v in self.lang_code_to_name.items()}
        current_code = self.config.get("language", "en")
        current_display = self.lang_code_to_name.get(current_code, "English")

        self.lang_lbl = ctk.CTkLabel(scroll, text=t("settings.language"))
        self.lang_lbl.grid(row=5, column=0, padx=10, pady=6, sticky="w")

        self.lang_menu = ctk.CTkOptionMenu(
            scroll,
            values=list(self.lang_name_to_code.keys()),
            command=self._on_language_selected,
            fg_color="#334155",
            button_color="#0284C7",
            button_hover_color="#0369A1",
        )
        self.lang_menu.set(current_display)
        self.lang_menu.grid(row=5, column=1, columnspan=2, padx=10, pady=6, sticky="ew")

        # Save Button
        self.save_btn = ctk.CTkButton(
            scroll,
            text=t("settings.save_btn"),
            height=38,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#0284C7",
            hover_color="#0369A1",
            command=self._save_settings,
        )
        self.save_btn.grid(row=6, column=0, columnspan=3, padx=10, pady=(20, 10), sticky="ew")

    # ==========================================
    # I18N DYNAMIC TEXT REFRESH
    # ==========================================
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
                self.mic_meter_title.configure(text="🎤 " + t("record.microphone") + ":")
                self._refresh_audio_targets()
            self._render_rec_status(*self.last_rec_status)

            self.rep_header_lbl.configure(text=t("replay.title"))
            if self.replay_worker is not None:
                self.rep_btn.configure(text=t("replay.stop_btn"))
            else:
                self.rep_btn.configure(text=t("replay.start_btn"))
            self.rep_info_lbl.configure(text=t("replay.info"))
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
                self.check_time_lbl.configure(text=t("settings.check_interval"))
                self.replay_vol_lbl.configure(text=t("settings.replay_volume"))
                self.lang_lbl.configure(text=t("settings.language"))
                self.save_btn.configure(text=t("settings.save_btn"))

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

                elif action == "STATUS_REPLAY":
                    status, detail = item[1], item[2]
                    self._render_replay_status(status, detail)

        except Exception:
            pass

        if not getattr(self, "_is_destroyed", False):
            try:
                self._queue_timer = self.after(50, self._process_queue)
            except Exception:
                pass

    def _render_rec_status(self, status, detail):
        self.last_rec_status = (status, detail)
        if status == "RECORDING":
            text = (
                t("record.status_recording", detail=detail)
                if detail
                else t("record.status_recording", detail=t("record.recording_active"))
            )
            self.rec_status_pill.configure(
                text=text,
                text_color="#F43F5E",
                fg_color="#3B0713",
            )
        elif status == "PAUSED":
            text = (
                t("record.status_paused", detail=detail)
                if detail
                else t("record.status_paused", detail=t("record.paused_time", time=""))
            )
            self.rec_status_pill.configure(
                text=text,
                text_color="#FBBF24",
                fg_color="#451A03",
            )
        elif status == "WATCHING":
            text = (
                t("record.status_listening", detail=detail)
                if detail
                else t("record.status_listening", detail=t("record.waiting_for_match"))
            )
            self.rec_status_pill.configure(
                text=text,
                text_color="#38BDF8",
                fg_color="#0C4A6E",
            )
        elif status in ("SAVING", "CONVERTING"):
            text = (
                t("record.status_saving", detail=detail)
                if detail
                else t("record.status_saving", detail=t("record.saving_audio"))
            )
            self.rec_status_pill.configure(
                text=text,
                text_color="#A855F7",
                fg_color="#3B0764",
            )
        elif status == "SAVED":
            self.rec_status_pill.configure(
                text=f"● SAVED: {detail}" if detail else "● SAVED",
                text_color="#22C55E",
                fg_color="#052E16",
            )
            self._refresh_recordings_list()
        elif status in ("STOPPED", "IDLE"):
            self.rec_status_pill.configure(
                text=t("record.status_stopped", detail=detail).strip()
                if detail
                else t("record.status_idle"),
                text_color="#94A3B8",
                fg_color="#0F172A",
            )
            if not self.recorder_worker or getattr(self.recorder_worker, "stop_event", None) is None or self.recorder_worker.stop_event.is_set():
                self._reset_rec_button_state()
            self._refresh_recordings_list()
        elif status == "ERROR":
            self.rec_status_pill.configure(
                text=t("record.status_error", detail=detail),
                text_color="#EF4444",
                fg_color="#450A0A",
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
                text_color="#38BDF8",
                fg_color="#082F49",
            )
        elif status == "PAUSED":
            text = (
                t("replay.status_paused", detail=detail)
                if detail
                else t("replay.status_paused", detail="")
            )
            self.rep_status_pill.configure(
                text=text,
                text_color="#FBBF24",
                fg_color="#451A03",
            )
        elif status == "WATCHING":
            text = (
                t("replay.status_watching", detail=detail)
                if detail
                else t("replay.status_watching", detail=t("replay.waiting_for_replay"))
            )
            self.rep_status_pill.configure(
                text=text,
                text_color="#38BDF8",
                fg_color="#0C4A6E",
            )
        elif status in ("STOPPED", "IDLE"):
            self.rep_status_pill.configure(
                text=t("replay.status_stopped", detail=detail).strip()
                if detail
                else t("replay.status_idle"),
                text_color="#94A3B8",
                fg_color="#0F172A",
            )
            self._reset_replay_button_state()
        elif status == "ERROR":
            self.rep_status_pill.configure(
                text=t("replay.status_error", detail=detail),
                text_color="#EF4444",
                fg_color="#450A0A",
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
            text_color="#38BDF8",
            fg_color="#0C4A6E",
        )

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
            text_color="#38BDF8",
            fg_color="#0C4A6E",
        )

        self.replay_worker = ReplaySynchronizer(
            config=self.config,
            log_callback=self.log_message,
            status_callback=self.set_replay_status,
        )
        self.replay_worker.set_volume(int(self.vol_slider.get()))

        self.replay_thread = threading.Thread(
            target=self.replay_worker.run_loop,
            daemon=True,
        )
        self.replay_thread.start()

    def _on_replay_volume_slider_changed(self, value):
        vol = int(value)
        if vol == 0:
            icon = "🔇"
        elif vol < 35:
            icon = "🔈"
        elif vol < 70:
            icon = "🔉"
        else:
            icon = "🔊"
        self.vol_label.configure(text=f"{icon} {vol}%")

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

        if discord_ok is True:
            self.discord_badge.configure(
                text=f"{discord_prefix} {t('health.ready')}", text_color="#4ADE80"
            )
        elif discord_ok is False:
            self.discord_badge.configure(
                text=f"{discord_prefix} {t('health.client_offline')}", text_color="#94A3B8"
            )
        else:
            self.discord_badge.configure(
                text=f"{discord_prefix} {t('health.checking')}", text_color="#CBD5E1"
            )

        if lol_ok is True:
            self.lol_badge.configure(
                text=f"{lol_prefix} {t('health.running')}", text_color="#4ADE80"
            )
        elif lol_ok is False:
            self.lol_badge.configure(
                text=f"{lol_prefix} {t('health.client_offline')}", text_color="#94A3B8"
            )
        else:
            self.lol_badge.configure(
                text=f"{lol_prefix} {t('health.checking')}", text_color="#CBD5E1"
            )

        if vlc_ok is True:
            self.vlc_badge.configure(
                text=f"{vlc_prefix} {t('health.ready')}", text_color="#4ADE80"
            )
        elif vlc_ok is False:
            self.vlc_badge.configure(
                text=f"{vlc_prefix} {t('health.not_found')}", text_color="#F87171"
            )
        else:
            self.vlc_badge.configure(
                text=f"{vlc_prefix} {t('health.checking')}", text_color="#CBD5E1"
            )

        if ffmpeg_ok is True:
            self.ffmpeg_badge.configure(
                text=f"{ffmpeg_prefix} {t('health.ready')}", text_color="#4ADE80"
            )
        elif ffmpeg_ok is False:
            self.ffmpeg_badge.configure(
                text=f"{ffmpeg_prefix} {t('health.not_found')}", text_color="#F87171"
            )
        else:
            self.ffmpeg_badge.configure(
                text=f"{ffmpeg_prefix} {t('health.checking')}", text_color="#CBD5E1"
            )

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
                fg_color="#1E293B" if idx % 2 == 0 else "#243248",
                corner_radius=6,
            )
            row.pack(fill="x", padx=4, pady=3)
            row.grid_columnconfigure(0, weight=1)

            name_lbl = ctk.CTkLabel(
                row,
                text=f"🎵 {filename}",
                font=ctk.CTkFont(size=13, weight="bold"),
                text_color="#F8FAFC",
            )
            name_lbl.grid(row=0, column=0, padx=12, pady=(6, 2), sticky="w")

            meta_lbl = ctk.CTkLabel(
                row,
                text=t("recordings.meta", size=size_mb, date=dt_str),
                font=ctk.CTkFont(size=11),
                text_color="#94A3B8",
            )
            meta_lbl.grid(row=1, column=0, padx=12, pady=(0, 6), sticky="w")

            play_btn = ctk.CTkButton(
                row,
                text=t("recordings.play"),
                width=70,
                height=28,
                font=ctk.CTkFont(size=11),
                fg_color="#0284C7",
                hover_color="#0369A1",
                command=lambda p=filepath: self._play_audio_file(p),
            )
            play_btn.grid(row=0, column=1, rowspan=2, padx=10, pady=6, sticky="e")

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

            new_config = {
                **self.config,
                "recording_mode": "native",
                "record_folder": self.rec_folder_entry.get().strip(),
                "vlc_dir": self.vlc_dir_entry.get().strip(),
                "check_time": float(self.check_time_entry.get().strip()),
                "replay_volume": vol_val,
                "language": selected_lang_code,
            }
            if save_config(new_config):
                self.config = new_config
                set_language(selected_lang_code)
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
        if self.recorder_worker and self.recorder_worker.recording:
            if not messagebox.askyesno(
                t("record.title"),
                t("app.active_rec_warning"),
            ):
                return

        if self.recorder_worker:
            self.recorder_worker.stop()
        if self.replay_worker:
            self.replay_worker.stop()

        self.destroy()

    def destroy(self):
        self._is_destroyed = True
        if hasattr(self, "_preview_native_recorder") and self._preview_native_recorder:
            try:
                self._preview_native_recorder.close()
            except Exception:
                pass
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


if __name__ == "__main__":
    main()
