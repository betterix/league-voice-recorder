"""
In-game floating HUD overlay widget for League of Legends live recording and replay sync.

Renders a translucent, borderless, non-intrusive HUD matching the exact design mockups:
- Recorder HUD: Live animated waveforms for Microphone (mint/teal) & Discord (periwinkle),
  REC badge with pulsing indicator, and live digital match timer.
- Replay HUD: Waveform with progress playhead (teal played, white cursor, grey unplayed),
  interactive replay volume slider, REPLAY/PAUSED badge, and replay timer.
- Semi-transparent (default 85% opacity), topmost, repositionable by drag,
  and supports Windows click-through mode so it never blocks gameplay input.
"""

from __future__ import annotations

from collections.abc import Callable
import math
import sys
import tkinter as tk

# Windows API constants for Click-Through overlay
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020


def set_window_click_through(hwnd: int, enable: bool = True) -> None:
    """Configures a Windows window for click-through transparency via SetWindowLongW."""
    if sys.platform != "win32" or not hwnd:
        return
    try:
        import ctypes

        style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if enable:
            ctypes.windll.user32.SetWindowLongW(
                hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED | WS_EX_TRANSPARENT
            )
        else:
            ctypes.windll.user32.SetWindowLongW(
                hwnd, GWL_EXSTYLE, style & ~WS_EX_TRANSPARENT
            )
    except Exception:
        pass


def draw_rounded_rect(
    canvas: tk.Canvas,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    radius: float = 10.0,
    **kwargs,
) -> int:
    """Draws a smooth rounded rectangle on a Tkinter canvas."""
    points = [
        x1 + radius, y1,
        x1 + radius, y1,
        x2 - radius, y1,
        x2 - radius, y1,
        x2, y1,
        x2, y1 + radius,
        x2, y1 + radius,
        x2, y2 - radius,
        x2, y2 - radius,
        x2, y2,
        x2 - radius, y2,
        x2 - radius, y2,
        x1 + radius, y2,
        x1 + radius, y2,
        x1, y2,
        x1, y2 - radius,
        x1, y2 - radius,
        x1, y1 + radius,
        x1, y1 + radius,
        x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


class LeagueHUDWidget:
    """
    Floating, translucent, borderless in-game HUD overlay matching the PNG mockups.
    Supports two modes: 'RECORD' and 'REPLAY'.
    """

    WIDTH = 280
    HEIGHT = 76
    KEY_COLOR = "#010101"  # Cutout color for transparent window corners

    # Design color palette from mockups
    COLOR_BG = "#0E1116"
    COLOR_BORDER = "#232B37"
    COLOR_DIVIDER = "#232B37"
    COLOR_LABEL = "#9CA3AF"
    COLOR_TIMER = "#F2F5F9"
    COLOR_TEAL = "#3DDCC4"
    COLOR_PERIWINKLE = "#8B93FF"
    COLOR_RED = "#EB6372"
    COLOR_AMBER = "#F59E0B"
    COLOR_UPCOMING_WAVE = "#3C4655"
    COLOR_TRACK = "#232B37"

    def __init__(
        self,
        master: tk.Tk,
        opacity: float = 0.85,
        click_through: bool = False,
        initial_x: int = -1,
        initial_y: int = -1,
        on_volume_changed: Callable[[int], None] | None = None,
        on_position_changed: Callable[[int, int], None] | None = None,
    ) -> None:
        self.master = master
        self.opacity = max(0.3, min(1.0, float(opacity)))
        self.click_through = bool(click_through)
        self.on_volume_changed = on_volume_changed
        self.on_position_changed = on_position_changed

        self.mode = "RECORD"  # "RECORD" or "REPLAY"
        self.is_visible = False

        # Live state variables
        self.time_str = "00:00"
        self.mic_level = 0.0
        self.discord_level = 0.0
        self.is_paused = False
        self.is_muted = False
        self.current_time = 0.0
        self.total_duration = 1800.0
        self.volume = 100
        self.speed = 1.0
        self.audio_level = 0.0

        # Animation state
        self._wave_phase = 0.0
        self._is_dragging = False
        self._is_dragging_volume = False
        self._drag_start_x = 0
        self._drag_start_y = 0

        # Precompute replay baseline waveform points
        self._replay_wave_points = self._generate_static_waveform()

        # Build window
        self.window = tk.Toplevel(self.master)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)

        try:
            self.window.attributes("-alpha", self.opacity)
        except Exception:
            pass

        try:
            self.window.attributes("-transparentcolor", self.KEY_COLOR)
            self.window.configure(bg=self.KEY_COLOR)
        except Exception:
            self.window.configure(bg=self.COLOR_BG)

        # Set position
        x, y = self._calculate_initial_position(initial_x, initial_y)
        self.window.geometry(f"{self.WIDTH}x{self.HEIGHT}+{x}+{y}")

        # Canvas
        self.canvas = tk.Canvas(
            self.window,
            width=self.WIDTH,
            height=self.HEIGHT,
            bg=self.KEY_COLOR,
            highlightthickness=0,
        )
        self.canvas.pack(fill="both", expand=True)

        # Mouse event bindings
        self.canvas.bind("<ButtonPress-1>", self._on_mouse_down)
        self.canvas.bind("<B1-Motion>", self._on_mouse_move)
        self.canvas.bind("<ButtonRelease-1>", self._on_mouse_up)
        self.canvas.bind("<Button-3>", self._on_right_click)

        # Apply click-through if configured
        if self.click_through:
            self._apply_click_through(True)

        # Initial render
        self.render()

    def _calculate_initial_position(self, x: int, y: int) -> tuple[int, int]:
        """Calculates screen placement, defaulting to top-right corner."""
        try:
            screen_w = self.master.winfo_screenwidth()
            screen_h = self.master.winfo_screenheight()
        except Exception:
            screen_w, screen_h = 1920, 1080

        if x is not None and x >= 0 and y is not None and y >= 0:
            # Constrain to visible screen bounds
            final_x = max(0, min(screen_w - self.WIDTH, x))
            final_y = max(0, min(screen_h - self.HEIGHT, y))
            return final_x, final_y

        # Default: Top-right corner with 30px margin
        return max(20, screen_w - self.WIDTH - 40), 35

    def get_hwnd(self) -> int:
        """Returns the Win32 HWND of this HUD overlay window."""
        try:
            wid = self.window.winfo_id()
            if sys.platform == "win32":
                import ctypes

                parent = ctypes.windll.user32.GetParent(wid)
                return int(parent if parent else wid)
            return int(wid)
        except Exception:
            return 0

    def _apply_click_through(self, enable: bool) -> None:
        """Applies or removes the Windows WS_EX_TRANSPARENT style."""
        try:
            hwnd = self.window.winfo_id()
            if sys.platform == "win32":
                import ctypes

                parent = ctypes.windll.user32.GetParent(hwnd)
                target_hwnd = parent if parent else hwnd
                set_window_click_through(target_hwnd, enable)
        except Exception:
            pass

    def set_click_through(self, enable: bool) -> None:
        """Dynamically enables or disables click-through mode."""
        self.click_through = bool(enable)
        self._apply_click_through(self.click_through)

    def set_opacity(self, opacity: float) -> None:
        """Dynamically adjusts overlay transparency."""
        self.opacity = max(0.3, min(1.0, float(opacity)))
        try:
            self.window.attributes("-alpha", self.opacity)
        except Exception:
            pass

    def set_mode(self, mode: str) -> None:
        """Switches between 'RECORD' and 'REPLAY' modes."""
        mode_upper = mode.upper()
        if mode_upper in ("RECORD", "REPLAY"):
            self.mode = mode_upper
            self.render()

    def show(self) -> None:
        """Displays the floating HUD."""
        self.is_visible = True
        self.window.deiconify()
        self.window.attributes("-topmost", True)
        self.render()

    def hide(self) -> None:
        """Hides the floating HUD."""
        self.is_visible = False
        self.window.withdraw()

    def reset_position(self) -> None:
        """Resets HUD position back to top-right screen corner."""
        x, y = self._calculate_initial_position(-1, -1)
        self.window.geometry(f"{self.WIDTH}x{self.HEIGHT}+{x}+{y}")
        if self.on_position_changed:
            self.on_position_changed(x, y)

    def update_recorder_state(
        self,
        time_str: str = "00:00",
        mic_level: float = 0.0,
        discord_level: float = 0.0,
        is_paused: bool = False,
        is_muted: bool = False,
    ) -> None:
        """Updates live match recording state and redraws waveforms."""
        self.time_str = time_str
        self.mic_level = max(0.0, min(1.0, float(mic_level)))
        self.discord_level = max(0.0, min(1.0, float(discord_level)))
        self.is_paused = bool(is_paused)
        self.is_muted = bool(is_muted)
        self._wave_phase += 0.14
        if self._wave_phase > 200.0:
            self._wave_phase = 0.0
        if self.is_visible and self.mode == "RECORD":
            self.render()

    def update_replay_state(
        self,
        time_str: str = "00:00",
        current_time: float = 0.0,
        total_duration: float = 1800.0,
        volume: int = 100,
        is_paused: bool = False,
        is_muted: bool = False,
        speed: float = 1.0,
        audio_level: float = 0.0,
    ) -> None:
        """Updates replay playback synchronization state."""
        self.time_str = time_str
        self.current_time = max(0.0, float(current_time))
        self.total_duration = max(1.0, float(total_duration))
        self.volume = max(0, min(100, int(volume)))
        self.is_paused = bool(is_paused)
        self.is_muted = bool(is_muted)
        self.speed = float(speed)
        self.audio_level = max(0.0, min(1.0, float(audio_level)))
        self._wave_phase += 0.14
        if self._wave_phase > 200.0:
            self._wave_phase = 0.0
        if self.is_visible and self.mode == "REPLAY":
            self.render()

    # ==========================================
    # RENDERING ENGINE
    # ==========================================
    def render(self) -> None:
        """Redraws the entire HUD matching the PNG designs."""
        self.canvas.delete("all")

        # 1. Base card background & outer border
        draw_rounded_rect(
            self.canvas,
            1,
            1,
            self.WIDTH - 1,
            self.HEIGHT - 1,
            radius=10,
            fill=self.COLOR_BG,
            outline=self.COLOR_BORDER,
            width=1.5,
        )

        # 2. Vertical divider line
        self.canvas.create_line(
            202,
            10,
            202,
            self.HEIGHT - 10,
            fill=self.COLOR_DIVIDER,
            width=1,
        )

        if self.mode == "RECORD":
            self._render_recorder_content()
        else:
            self._render_replay_content()

    def _render_recorder_content(self) -> None:
        """Renders the Recorder HUD (MIC, DISCORD, REC, Timer)."""
        # Row 1: MIC Label
        self.canvas.create_text(
            14,
            22,
            text="MIC",
            anchor="w",
            fill=self.COLOR_LABEL,
            font=("Segoe UI", 10, "bold"),
        )

        # Row 1: MIC Animated Waveform
        mic_pts = self._generate_active_waveform(
            center_y=22,
            level=0.0 if self.is_muted else self.mic_level,
            phase=self._wave_phase,
            seed=0.0,
        )
        mic_glow = "#5A1E28" if self.is_muted else "#155E54"
        mic_color = "#EF4444" if self.is_muted else self.COLOR_TEAL
        self.canvas.create_line(
            mic_pts,
            fill=mic_glow,
            width=3.2,
            smooth=True,
            capstyle="round",
            joinstyle="round",
        )
        self.canvas.create_line(
            mic_pts,
            fill=mic_color,
            width=1.8,
            smooth=True,
            capstyle="round",
            joinstyle="round",
        )

        # Row 2: DISCORD Label
        self.canvas.create_text(
            14,
            54,
            text="DISCORD",
            anchor="w",
            fill=self.COLOR_LABEL,
            font=("Segoe UI", 10, "bold"),
        )

        # Row 2: DISCORD Animated Waveform
        discord_pts = self._generate_active_waveform(
            center_y=54,
            level=self.discord_level,
            phase=self._wave_phase * 1.15,
            seed=2.5,
        )
        self.canvas.create_line(
            discord_pts,
            fill="#343864",
            width=3.2,
            smooth=True,
            capstyle="round",
            joinstyle="round",
        )
        self.canvas.create_line(
            discord_pts,
            fill=self.COLOR_PERIWINKLE,
            width=1.8,
            smooth=True,
            capstyle="round",
            joinstyle="round",
        )

        # Right Section: Status Indicator (REC or PAUSED)
        if self.is_paused:
            # Amber dot + PAUSED
            self.canvas.create_oval(
                211, 19, 219, 27, fill=self.COLOR_AMBER, outline=""
            )
            self.canvas.create_text(
                223,
                23,
                text="PAUSED",
                anchor="w",
                fill=self.COLOR_AMBER,
                font=("Segoe UI", 9, "bold"),
            )
        else:
            # Coral/red dot + REC
            self.canvas.create_oval(
                218, 19, 226, 27, fill=self.COLOR_RED, outline=""
            )
            self.canvas.create_text(
                230,
                23,
                text="REC",
                anchor="w",
                fill=self.COLOR_RED,
                font=("Segoe UI", 10, "bold"),
            )

        # Right Section: Digital Match Timer
        self.canvas.create_text(
            241,
            52,
            text=self.time_str,
            anchor="center",
            fill=self.COLOR_TIMER,
            font=("Segoe UI", 19, "bold"),
        )

    def _render_replay_content(self) -> None:
        """Renders the Replay HUD (AUDIO wave with playhead, VOLUME slider, REPLAY, Timer)."""
        # Row 1: AUDIO Label
        self.canvas.create_text(
            14,
            22,
            text="AUDIO",
            anchor="w",
            fill=self.COLOR_LABEL,
            font=("Segoe UI", 10, "bold"),
        )

        # Row 1: Audio waveform with moving playhead cursor
        progress = max(0.0, min(1.0, self.current_time / self.total_duration))
        cursor_x = 68.0 + progress * 126.0

        # Draw played portion (Teal)
        played_pts, upcoming_pts = self._split_waveform(cursor_x, audio_level=self.audio_level)
        if len(played_pts) >= 4:
            self.canvas.create_line(
                played_pts,
                fill="#155E54",
                width=3.2,
                smooth=True,
                capstyle="round",
                joinstyle="round",
            )
            self.canvas.create_line(
                played_pts,
                fill=self.COLOR_TEAL,
                width=1.8,
                smooth=True,
                capstyle="round",
                joinstyle="round",
            )

        # Draw upcoming portion (Muted slate)
        if len(upcoming_pts) >= 4:
            self.canvas.create_line(
                upcoming_pts,
                fill="#1A2332",
                width=3.2,
                smooth=True,
                capstyle="round",
                joinstyle="round",
            )
            self.canvas.create_line(
                upcoming_pts,
                fill=self.COLOR_UPCOMING_WAVE,
                width=1.8,
                smooth=True,
                capstyle="round",
                joinstyle="round",
            )

        # White playhead cursor needle
        self.canvas.create_line(
            cursor_x,
            14,
            cursor_x,
            30,
            fill="#FFFFFF",
            width=2,
            capstyle="round",
        )

        # Row 2: VOLUME Label
        self.canvas.create_text(
            14,
            54,
            text="VOLUME",
            anchor="w",
            fill=self.COLOR_LABEL,
            font=("Segoe UI", 10, "bold"),
        )

        # Row 2: Volume Slider Track
        slider_x1, slider_x2 = 68.0, 194.0
        slider_w = slider_x2 - slider_x1
        effective_vol = 0 if self.is_muted else self.volume
        vol_pct = max(0.0, min(1.0, effective_vol / 100.0))
        knob_x = slider_x1 + vol_pct * slider_w

        # Background track capsule
        self.canvas.create_line(
            slider_x1,
            54,
            slider_x2,
            54,
            fill=self.COLOR_TRACK,
            width=7,
            capstyle="round",
        )

        # Active filled track
        active_color = "#64748B" if self.is_muted else self.COLOR_TEAL
        if knob_x > slider_x1:
            self.canvas.create_line(
                slider_x1,
                54,
                knob_x,
                54,
                fill=active_color,
                width=7,
                capstyle="round",
            )

        # Round thumb knob
        r = 6.5
        self.canvas.create_oval(
            knob_x - r,
            54 - r,
            knob_x + r,
            54 + r,
            fill=active_color,
            outline=active_color,
        )

        # Right Section: Status (▶ REPLAY or ⏸ PAUSED)
        if self.is_paused:
            self.canvas.create_text(
                210,
                23,
                text="⏸",
                anchor="w",
                fill=self.COLOR_AMBER,
                font=("Segoe UI", 10, "bold"),
            )
            self.canvas.create_text(
                224,
                23,
                text="PAUSED",
                anchor="w",
                fill=self.COLOR_AMBER,
                font=("Segoe UI", 9, "bold"),
            )
        else:
            self.canvas.create_text(
                211,
                23,
                text="▶",
                anchor="w",
                fill=self.COLOR_TEAL,
                font=("Segoe UI", 10, "bold"),
            )
            speed_text = "REPLAY" if self.speed == 1.0 else f"{self.speed:.1f}x"
            self.canvas.create_text(
                225,
                23,
                text=speed_text,
                anchor="w",
                fill=self.COLOR_TEAL,
                font=("Segoe UI", 10, "bold"),
            )

        # Right Section: Replay Timer
        self.canvas.create_text(
            241,
            52,
            text=self.time_str,
            anchor="center",
            fill=self.COLOR_TIMER,
            font=("Segoe UI", 19, "bold"),
        )

    # ==========================================
    # WAVEFORM ALGORITHMS
    # ==========================================
    def _generate_static_waveform(self) -> list[tuple[float, float]]:
        """Generates a smooth, organic audio waveform track profile for replay."""
        width = 126.0
        start_x = 68.0
        center_y = 22.0
        num_points = 90
        step = width / (num_points - 1)
        pts = []

        for i in range(num_points):
            x = start_x + i * step
            norm = i / (num_points - 1)
            envelope = math.sin(norm * math.pi) ** 1.15
            h = (
                math.sin(norm * 4.0 * math.pi)
                + math.sin(norm * 8.0 * math.pi) * 0.4
                + math.cos(norm * 2.0 * math.pi) * 0.3
            ) * 5.8 * envelope
            pts.append((x, center_y + h))
        return pts

    def _split_waveform(
        self, cursor_x: float, audio_level: float = 0.0
    ) -> tuple[list[float], list[float]]:
        """Splits the replay waveform into played (left) and upcoming (right) coordinates."""
        pts = self._replay_wave_points
        played = []
        upcoming = []
        split_done = False

        mod_phase = self._wave_phase if audio_level > 0.02 else 0.0

        for i in range(len(pts) - 1):
            x1, y1 = pts[i]
            x2, y2 = pts[i + 1]

            if audio_level > 0.02 and x1 <= cursor_x:
                norm = i / max(1, len(pts) - 1)
                envelope = math.sin(norm * math.pi) ** 1.15
                disp = math.sin(norm * 5.0 * math.pi + mod_phase) * audio_level * 5.0 * envelope
                y1 = y1 + disp

            if x2 < cursor_x:
                if not played:
                    played.extend([x1, y1])
                played.extend([x2, y2])
            elif not split_done:
                t = (cursor_x - x1) / (x2 - x1) if x2 != x1 else 0.0
                mid_y = y1 + t * (y2 - y1)
                if not played:
                    played.extend([x1, y1])
                played.extend([cursor_x, mid_y])
                upcoming.extend([cursor_x, mid_y, x2, y2])
                split_done = True
            else:
                upcoming.extend([x2, y2])

        if not split_done:
            flat_pts = [coord for pt in pts for coord in pt]
            return flat_pts, []

        return played, upcoming

    def _generate_active_waveform(
        self,
        center_y: float,
        level: float = 0.0,
        phase: float = 0.0,
        seed: float = 0.0,
    ) -> list[float]:
        """Generates a silky smooth oscillating audio waveform modulated by live VU levels."""
        width = 126.0
        start_x = 68.0
        num_points = 90
        step = width / (num_points - 1)
        points = []

        base_amp = 0.8 if level < 0.02 else 1.2
        active_amp = level * 7.0

        for i in range(num_points):
            x = start_x + i * step
            norm = i / (num_points - 1)
            envelope = math.sin(norm * math.pi) ** 1.25

            w1 = math.sin(norm * 3.6 * math.pi + phase + seed)
            w2 = math.sin(norm * 7.2 * math.pi - phase * 0.75 + seed * 1.4) * 0.32
            w3 = math.cos(norm * 1.8 * math.pi + phase * 0.4) * 0.22

            disp = (w1 + w2 + w3) * (base_amp + active_amp) * envelope
            points.extend([x, center_y + disp])

        return points

    # ==========================================
    # MOUSE INTERACTION & DRAGGING
    # ==========================================
    def _on_mouse_down(self, event: tk.Event) -> None:
        if self.click_through:
            return

        # Check if clicking the volume slider in Replay mode
        if self.mode == "REPLAY" and 65 <= event.x <= 198 and 42 <= event.y <= 66:
            self._is_dragging_volume = True
            self._update_volume_from_mouse(event.x)
            return

        # Window drag
        self._is_dragging = True
        self._drag_start_x = event.x
        self._drag_start_y = event.y

    def _on_mouse_move(self, event: tk.Event) -> None:
        if self.click_through:
            return

        if self._is_dragging_volume:
            self._update_volume_from_mouse(event.x)
        elif self._is_dragging:
            cur_x = self.window.winfo_x()
            cur_y = self.window.winfo_y()
            new_x = cur_x + (event.x - self._drag_start_x)
            new_y = cur_y + (event.y - self._drag_start_y)
            self.window.geometry(f"+{new_x}+{new_y}")

    def _on_mouse_up(self, event: tk.Event) -> None:
        if self.click_through:
            return

        if self._is_dragging_volume:
            self._is_dragging_volume = False
            self._update_volume_from_mouse(event.x)
        elif self._is_dragging:
            self._is_dragging = False
            final_x = self.window.winfo_x()
            final_y = self.window.winfo_y()
            if self.on_position_changed:
                self.on_position_changed(final_x, final_y)

    def _update_volume_from_mouse(self, mouse_x: float) -> None:
        """Computes volume percentage from mouse X coordinate in slider track."""
        slider_x1, slider_x2 = 68.0, 194.0
        slider_w = slider_x2 - slider_x1
        clamped_x = max(slider_x1, min(slider_x2, mouse_x))
        pct = round(((clamped_x - slider_x1) / slider_w) * 100)
        self.volume = pct
        self.render()
        if self.on_volume_changed:
            self.on_volume_changed(pct)

    def _on_right_click(self, event: tk.Event) -> None:
        """Pops up a contextual control menu."""
        if self.click_through:
            return

        menu = tk.Menu(self.window, tearoff=0)
        menu.add_command(
            label="🎯 Reset Position",
            command=self.reset_position,
        )
        if self.mode == "RECORD":
            menu.add_command(
                label="👁️ Switch to Replay HUD",
                command=lambda: self.set_mode("REPLAY"),
            )
        else:
            menu.add_command(
                label="👁️ Switch to Recorder HUD",
                command=lambda: self.set_mode("RECORD"),
            )
        menu.add_separator()
        menu.add_command(label="✕ Close HUD", command=self.hide)

        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def destroy(self) -> None:
        """Destroys the HUD toplevel window cleanly."""
        try:
            self.window.destroy()
        except Exception:
            pass
