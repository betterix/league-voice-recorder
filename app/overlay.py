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
WS_EX_NOACTIVATE = 0x08000000

# Cache of HWNDs configured with WS_EX_NOACTIVATE to avoid redundant Win32 system calls
_CONFIGURED_NOACTIVATE_HWNDS: set[int] = set()


def set_window_click_through(hwnd: int, enable: bool = False) -> None:
    """Configures a Windows window for click-through transparency."""
    if sys.platform != "win32" or not hwnd:
        return
    try:
        import ctypes

        u32 = ctypes.windll.user32
        get_long = getattr(u32, "GetWindowLongPtrW", u32.GetWindowLongW)
        set_long = getattr(u32, "SetWindowLongPtrW", u32.SetWindowLongW)

        style = get_long(hwnd, GWL_EXSTYLE)
        if enable:
            new_style = style | WS_EX_TRANSPARENT
        else:
            new_style = style & ~WS_EX_TRANSPARENT
        if new_style != style:
            set_long(hwnd, GWL_EXSTYLE, new_style)
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            SWP_NOZORDER = 0x0004
            SWP_FRAMECHANGED = 0x0020
            u32.SetWindowPos(
                hwnd,
                0,
                0,
                0,
                0,
                0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED,
            )
    except Exception:
        pass


def set_window_no_activate(hwnd: int) -> None:
    """Configures a Windows window with WS_EX_NOACTIVATE so clicking it does not steal focus."""
    if sys.platform != "win32" or not hwnd or hwnd in _CONFIGURED_NOACTIVATE_HWNDS:
        return
    try:
        import ctypes

        u32 = ctypes.windll.user32
        get_long = getattr(u32, "GetWindowLongPtrW", u32.GetWindowLongW)
        set_long = getattr(u32, "SetWindowLongPtrW", u32.SetWindowLongW)

        style = get_long(hwnd, GWL_EXSTYLE)
        if not (style & WS_EX_NOACTIVATE):
            set_long(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE)
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            SWP_NOZORDER = 0x0004
            SWP_FRAMECHANGED = 0x0020
            u32.SetWindowPos(
                hwnd,
                0,
                0,
                0,
                0,
                0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED,
            )
        _CONFIGURED_NOACTIVATE_HWNDS.add(hwnd)
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


class LeagueOverlay:
    """
    Floating, translucent, borderless in-game overlay matching esports Hextech designs.
    Supports two modes: 'RECORD' and 'REPLAY'.
    """

    WIDTH = 350
    HEIGHT = 76
    KEY_COLOR = "#010101"  # Cutout color for transparent window corners

    # Precise Layout Coordinates (Extra-wide layout preventing any label collision)
    TRACK_START_X = 88.0
    TRACK_END_X = 240.0
    TRACK_WIDTH = 152.0
    NUM_BARS = 36
    DIVIDER_X = 250
    SLIDER_X1 = 88.0
    SLIDER_X2 = 240.0
    LOCK_X = 332
    LOCK_Y = 22
    TIMER_X = 300
    TIMER_Y = 52

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

    # Precomputed GradeX Waveform Constants & Geometry
    _ACTIVE_BAR_CONSTANTS: tuple[tuple[float, float, float, float, float], ...] = ()
    _RESTING_MIC_BARS: tuple[tuple[float, float, float], ...] = ()
    _RESTING_DISCORD_BARS: tuple[tuple[float, float, float], ...] = ()
    _REPLAY_HARMONICS: tuple[tuple[float, float, float], ...] = ()

    # Precomputed Hit Target Boundaries
    _LOCK_HIT_X1 = LOCK_X - 14
    _LOCK_HIT_X2 = LOCK_X + 14
    _LOCK_HIT_Y1 = LOCK_Y - 14
    _LOCK_HIT_Y2 = LOCK_Y + 14
    _SLIDER_HIT_X1 = SLIDER_X1 - 6
    _SLIDER_HIT_X2 = SLIDER_X2 + 6
    _SLIDER_HIT_Y1 = 42
    _SLIDER_HIT_Y2 = 66

    def __init__(
        self,
        master: tk.Tk,
        opacity: float = 0.85,
        locked: bool = False,
        click_through: bool = False,
        initial_x: int = -1,
        initial_y: int = -1,
        on_volume_changed: Callable[[int], None] | None = None,
        on_position_changed: Callable[[int, int], None] | None = None,
        on_lock_changed: Callable[[bool], None] | None = None,
        on_voice_mode_toggle: Callable[[], None] | None = None,
    ) -> None:
        self.master = master
        self.opacity = max(0.3, min(1.0, float(opacity)))
        self.locked = bool(locked or click_through)
        self.click_through = self.locked
        self.on_volume_changed = on_volume_changed
        self.on_position_changed = on_position_changed
        self.on_lock_changed = on_lock_changed
        self.on_voice_mode_toggle = on_voice_mode_toggle

        self.mode = "RECORD"  # "RECORD" or "REPLAY"
        self.is_visible = False
        self.prefer_suppressed = False

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
        self.canvas.bind("<Motion>", self._on_mouse_hover)

        # Dedicated transparent hit-test windows for Lock icon and Volume slider in click-through mode
        self.lock_hit_win = None
        self.volume_hit_win = None
        self._init_lock_hit_window()
        self._init_volume_hit_window()

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

    def get_all_hwnds(self) -> set[int]:
        """Returns the set of all Win32 HWNDs associated with this overlay and its hit-windows."""
        hwnds: set[int] = set()
        windows = (
            getattr(self, "window", None),
            getattr(self, "lock_hit_win", None),
            getattr(self, "volume_hit_win", None),
        )
        for win in windows:
            if win:
                try:
                    wid = win.winfo_id()
                    if wid:
                        hwnds.add(int(wid))
                        if sys.platform == "win32":
                            import ctypes

                            u32 = ctypes.windll.user32
                            parent = u32.GetParent(wid)
                            if parent:
                                hwnds.add(int(parent))
                            GA_ROOT = 2
                            root = getattr(u32, "GetAncestor", lambda w, f: 0)(wid, GA_ROOT)
                            if root:
                                hwnds.add(int(root))
                except Exception:
                    pass
        return hwnds

    def _init_lock_hit_window(self) -> None:
        """Creates a tiny transparent hit-test window over the lock icon for click-through mode."""
        try:
            self.lock_hit_win = tk.Toplevel(self.master)
            self.lock_hit_win.overrideredirect(True)
            self.lock_hit_win.withdraw()
            try:
                self.lock_hit_win.attributes("-topmost", True)
                self.lock_hit_win.attributes("-alpha", 0.01)
            except Exception:
                pass
            self.lock_hit_win.config(cursor="hand2")
            self.lock_hit_win.bind("<ButtonPress-1>", self._on_lock_hit_click)
            self.lock_hit_win.bind("<Button-3>", lambda e: None)
        except Exception:
            self.lock_hit_win = None

    def _sync_lock_hit_window(self) -> None:
        """Positions the lock hit window precisely over the lock button when locked and visible."""
        if not getattr(self, "lock_hit_win", None):
            return
        if getattr(self, "is_visible", False) and self.locked:
            try:
                wx = self.window.winfo_x()
                wy = self.window.winfo_y()
                hx = wx + self.LOCK_X - 14
                hy = wy + self.LOCK_Y - 14
                self.lock_hit_win.geometry(f"28x28+{hx}+{hy}")
                self.lock_hit_win.deiconify()
                self.lock_hit_win.attributes("-topmost", True)
                self.lock_hit_win.attributes("-alpha", 0.01)
                try:
                    wid = self.lock_hit_win.winfo_id()
                    if sys.platform == "win32":
                        import ctypes

                        parent = ctypes.windll.user32.GetParent(wid)
                        set_window_no_activate(int(parent if parent else wid))
                except Exception:
                    pass
            except Exception:
                pass
        else:
            try:
                self.lock_hit_win.withdraw()
            except Exception:
                pass

    def _on_lock_hit_click(self, event: tk.Event) -> None:
        """Called when clicking the lock icon while overlay is locked to unlock drag mode."""
        self.set_locked(False)

    def _init_volume_hit_window(self) -> None:
        """Creates a transparent hit-test window over the volume slider in Replay mode for click-through mode."""
        try:
            self.volume_hit_win = tk.Toplevel(self.master)
            self.volume_hit_win.overrideredirect(True)
            self.volume_hit_win.withdraw()
            try:
                self.volume_hit_win.attributes("-topmost", True)
                self.volume_hit_win.attributes("-alpha", 0.01)
            except Exception:
                pass
            self.volume_hit_win.config(cursor="hand2")
            self.volume_hit_win.bind("<ButtonPress-1>", self._on_volume_hit_event)
            self.volume_hit_win.bind("<B1-Motion>", self._on_volume_hit_event)
            self.volume_hit_win.bind("<ButtonRelease-1>", self._on_volume_hit_event)
            self.volume_hit_win.bind("<Button-3>", lambda e: None)
        except Exception:
            self.volume_hit_win = None

    def _sync_volume_hit_window(self) -> None:
        """Positions the volume hit window precisely over the volume slider when locked, visible, and in REPLAY mode."""
        if not getattr(self, "volume_hit_win", None):
            return
        if getattr(self, "is_visible", False) and self.locked and getattr(self, "mode", "") == "REPLAY":
            try:
                wx = self.window.winfo_x()
                wy = self.window.winfo_y()
                vx = wx + int(self.SLIDER_X1 - 6)
                vy = wy + 42
                vw = int(self.SLIDER_X2 - self.SLIDER_X1 + 12)
                vh = 24
                self.volume_hit_win.geometry(f"{vw}x{vh}+{vx}+{vy}")
                self.volume_hit_win.deiconify()
                self.volume_hit_win.attributes("-topmost", True)
                self.volume_hit_win.attributes("-alpha", 0.01)
                try:
                    wid = self.volume_hit_win.winfo_id()
                    if sys.platform == "win32":
                        import ctypes

                        parent = ctypes.windll.user32.GetParent(wid)
                        set_window_no_activate(int(parent if parent else wid))
                except Exception:
                    pass
            except Exception:
                pass
        else:
            try:
                self.volume_hit_win.withdraw()
            except Exception:
                pass

    def _on_volume_hit_event(self, event: tk.Event) -> None:
        """Called when clicking or dragging the volume slider while overlay is locked in Replay mode."""
        canvas_x = self.SLIDER_X1 - 6 + event.x
        self._update_volume_from_mouse(canvas_x)

    def set_voice_mode(self, prefer_suppressed: bool) -> None:
        """Legacy helper for voice mode state compatibility."""
        self.prefer_suppressed = bool(prefer_suppressed)

    def _sync_hit_windows(self) -> None:
        """Synchronizes all hit-test windows (lock icon, volume slider) with the overlay position."""
        self._sync_lock_hit_window()
        self._sync_volume_hit_window()

    def _apply_click_through(self, enable: bool) -> None:
        """Applies or removes the Windows WS_EX_TRANSPARENT style strictly on the top-level window."""
        try:
            if sys.platform != "win32":
                return
            if getattr(self, "_current_click_through_style", None) == enable:
                return
            parent = self.get_hwnd()
            if parent:
                set_window_click_through(parent, enable)
                self._current_click_through_style = enable
        except Exception:
            pass

    def set_locked(self, locked: bool) -> None:
        """Dynamically locks or unlocks the overlay position and toggles click-through mode."""
        self.locked = bool(locked)
        self.click_through = self.locked
        self._apply_click_through(self.locked)
        self._sync_hit_windows()
        if getattr(self, "is_visible", False):
            self.render()
        if self.on_lock_changed:
            self.on_lock_changed(self.locked)

    def set_click_through(self, enable: bool) -> None:
        """Alias for set_locked to maintain full backward compatibility."""
        self.set_locked(enable)

    def set_opacity(self, opacity: float) -> None:
        """Dynamically adjusts overlay transparency."""
        self.opacity = max(0.3, min(1.0, float(opacity)))
        try:
            self.window.attributes("-alpha", self.opacity)
            self.window.attributes("-topmost", True)
        except Exception:
            pass
        if getattr(self, "is_visible", False):
            self.render()

    def set_mode(self, mode: str) -> None:
        """Switches between 'RECORD' and 'REPLAY' modes."""
        mode_upper = mode.upper()
        if mode_upper in ("RECORD", "REPLAY"):
            self.mode = mode_upper
            self._sync_hit_windows()
            self.render()

    def show(self) -> None:
        """Displays the floating HUD."""
        self.is_visible = True
        self.window.deiconify()
        try:
            self.window.attributes("-alpha", self.opacity)
            self.window.attributes("-topmost", True)
        except Exception:
            pass
        self.window.update_idletasks()
        self._apply_click_through(self.locked)
        self._sync_hit_windows()
        self.render()

    def hide(self) -> None:
        """Hides the floating HUD."""
        self.is_visible = False
        self.window.withdraw()
        if getattr(self, "lock_hit_win", None):
            try:
                self.lock_hit_win.withdraw()
            except Exception:
                pass
        if getattr(self, "volume_hit_win", None):
            try:
                self.volume_hit_win.withdraw()
            except Exception:
                pass

    def reset_position(self) -> None:
        """Resets HUD position back to top-right screen corner."""
        x, y = self._calculate_initial_position(-1, -1)
        self.window.geometry(f"{self.WIDTH}x{self.HEIGHT}+{x}+{y}")
        self.window.update_idletasks()
        self._sync_hit_windows()
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
        self._wave_phase = (self._wave_phase + 0.14) % (math.pi * 200.0)
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
        self.volume = max(0, min(200, int(volume)))
        self.is_paused = bool(is_paused)
        self.is_muted = bool(is_muted)
        self.speed = float(speed)
        self.audio_level = max(0.0, min(1.0, float(audio_level)))
        self._wave_phase = (self._wave_phase + 0.14) % (math.pi * 200.0)
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
            self.DIVIDER_X,
            10,
            self.DIVIDER_X,
            self.HEIGHT - 10,
            fill=self.COLOR_DIVIDER,
            width=1,
        )

        if self.mode == "RECORD":
            self._render_recorder_content()
        else:
            self._render_replay_content()

    def _render_lock_icon(self) -> None:
        """Renders the Lock/Unlock status button on the top right corner."""
        lock_bg = "#261E14" if self.locked else "#131B2E"
        lock_border = "#78350F" if self.locked else "#2A3547"
        self.canvas.create_oval(
            self.LOCK_X - 11,
            self.LOCK_Y - 11,
            self.LOCK_X + 11,
            self.LOCK_Y + 11,
            fill=lock_bg,
            outline=lock_border,
            width=1,
        )
        lock_icon = "🔒" if self.locked else "🔓"
        lock_color = "#F59E0B" if self.locked else "#38BDF8"
        self.canvas.create_text(
            self.LOCK_X,
            self.LOCK_Y,
            text=lock_icon,
            anchor="center",
            font=("Segoe UI Emoji", 10),
            fill=lock_color,
        )

    def _render_recorder_content(self) -> None:
        """Renders the Recorder HUD (MIC, DISCORD, REC, Timer, Lock)."""
        # Row 1: MIC Label
        self.canvas.create_text(
            16,
            22,
            text="MIC",
            anchor="w",
            fill=self.COLOR_LABEL,
            font=("Segoe UI", 10, "bold"),
        )

        # Row 1: MIC Animated Vertical Visualizer Bars
        mic_color = "#EF4444" if self.is_muted else self.COLOR_TEAL
        mic_bars = self._generate_active_waveform(
            center_y=22.0,
            level=0.0 if self.is_muted else self.mic_level,
            phase=self._wave_phase,
            seed=0.0,
        )
        for x, y1, y2 in mic_bars:
            self.canvas.create_line(
                x,
                y1,
                x,
                y2,
                fill=mic_color,
                width=2.2,
                capstyle="round",
            )

        # Row 2: DISCORD Label
        self.canvas.create_text(
            16,
            54,
            text="DISCORD",
            anchor="w",
            fill=self.COLOR_LABEL,
            font=("Segoe UI", 10, "bold"),
        )

        # Row 2: DISCORD Animated Vertical Visualizer Bars
        discord_bars = self._generate_active_waveform(
            center_y=54.0,
            level=self.discord_level,
            phase=self._wave_phase * 1.15,
            seed=2.5,
        )
        for x, y1, y2 in discord_bars:
            self.canvas.create_line(
                x,
                y1,
                x,
                y2,
                fill=self.COLOR_PERIWINKLE,
                width=2.2,
                capstyle="round",
            )

        # Right Section: Status Indicator (REC or PAUSED)
        if self.is_paused:
            # Amber dot + PAUSED
            self.canvas.create_oval(
                258, 18, 266, 26, fill=self.COLOR_AMBER, outline=""
            )
            self.canvas.create_text(
                270,
                22,
                text="PAUSED",
                anchor="w",
                fill=self.COLOR_AMBER,
                font=("Segoe UI", 9, "bold"),
            )
        else:
            # Coral/red dot + REC
            self.canvas.create_oval(
                258, 18, 266, 26, fill=self.COLOR_RED, outline=""
            )
            self.canvas.create_text(
                270,
                22,
                text="REC",
                anchor="w",
                fill=self.COLOR_RED,
                font=("Segoe UI", 10, "bold"),
            )

        # Lock button (top right)
        self._render_lock_icon()

        # Right Section: Digital Match Timer
        self.canvas.create_text(
            self.TIMER_X,
            self.TIMER_Y,
            text=self.time_str,
            anchor="center",
            fill=self.COLOR_TIMER,
            font=("Segoe UI", 19, "bold"),
        )

    def _render_replay_content(self) -> None:
        """Renders the Replay Overlay (AUDIO wave with playhead, VOLUME slider, REPLAY, Timer, Lock)."""
        # Row 1: AUDIO Label
        self.canvas.create_text(
            16,
            22,
            text="AUDIO",
            anchor="w",
            fill=self.COLOR_LABEL,
            font=("Segoe UI", 10, "bold"),
        )

        # Row 1: Audio waveform vertical bars with moving playhead cursor
        progress = max(0.0, min(1.0, self.current_time / self.total_duration))
        cursor_x = self.TRACK_START_X + progress * self.TRACK_WIDTH

        # Draw played portion (Teal) and upcoming portion (Muted slate)
        played_bars, upcoming_bars = self._split_waveform(cursor_x, audio_level=self.audio_level)
        for x, y1, y2 in played_bars:
            self.canvas.create_line(
                x,
                y1,
                x,
                y2,
                fill=self.COLOR_TEAL,
                width=2.2,
                capstyle="round",
            )
        for x, y1, y2 in upcoming_bars:
            self.canvas.create_line(
                x,
                y1,
                x,
                y2,
                fill=self.COLOR_UPCOMING_WAVE,
                width=2.2,
                capstyle="round",
            )

        # White playhead cursor needle
        self.canvas.create_line(
            cursor_x,
            13,
            cursor_x,
            31,
            fill="#FFFFFF",
            width=2.2,
            capstyle="round",
        )

        # Row 2: VOLUME Label
        self.canvas.create_text(
            16,
            54,
            text="VOLUME",
            anchor="w",
            fill=self.COLOR_LABEL,
            font=("Segoe UI", 10, "bold"),
        )

        # Row 2: Volume Slider Track
        slider_x1, slider_x2 = self.SLIDER_X1, self.SLIDER_X2
        effective_vol = 0 if self.is_muted else self.volume
        vol_pct = max(0.0, min(1.0, effective_vol / 200.0))
        knob_x = slider_x1 + vol_pct * self.TRACK_WIDTH

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
                258,
                22,
                text="⏸ PAUSED",
                anchor="w",
                fill=self.COLOR_AMBER,
                font=("Segoe UI", 9, "bold"),
            )
        else:
            speed_text = "REPLAY" if self.speed == 1.0 else f"{self.speed:.1f}x"
            self.canvas.create_text(
                258,
                22,
                text=f"▶ {speed_text}",
                anchor="w",
                fill=self.COLOR_TEAL,
                font=("Segoe UI", 9, "bold"),
            )

        # Lock button (top right)
        self._render_lock_icon()

        # Right Section: Replay Timer
        self.canvas.create_text(
            self.TIMER_X,
            self.TIMER_Y,
            text=self.time_str,
            anchor="center",
            fill=self.COLOR_TIMER,
            font=("Segoe UI", 19, "bold"),
        )

    # ==========================================
    # WAVEFORM ALGORITHMS (PURE VERTICAL BARS)
    # ==========================================
    def _generate_static_waveform(self) -> list[tuple[float, float, float]]:
        """Generates baseline vertical bar coordinates (x, y1, y2) for the replay audio track."""
        width = self.TRACK_WIDTH
        start_x = self.TRACK_START_X
        center_y = 22.0
        num_bars = self.NUM_BARS
        step = width / (num_bars - 1)
        bars = []

        for i in range(num_bars):
            x = start_x + i * step
            norm = i / (num_bars - 1)
            envelope = math.sin(norm * math.pi) ** 0.85
            # Dynamic sound profile with realistic peaks and troughs
            profile = (
                math.sin(norm * 4.0 * math.pi) * 0.45
                + math.sin(norm * 9.0 * math.pi) * 0.35
                + math.cos(norm * 2.0 * math.pi) * 0.20
            )
            h = 2.0 + abs(profile) * envelope * 5.6
            h = min(7.5, max(1.8, h))
            bars.append((x, center_y - h, center_y + h))
        return bars

    def _split_waveform(
        self, cursor_x: float, audio_level: float = 0.0
    ) -> tuple[list[tuple[float, float, float]], list[tuple[float, float, float]]]:
        """Splits the replay waveform into played (left) and upcoming (right) vertical bars."""
        pts = self._replay_wave_points
        clamped_level = max(0.0, min(1.0, float(audio_level)))
        eff_audio = min(1.0, (clamped_level ** 0.45) * 1.55) if clamped_level > 0.012 else 0.0

        if eff_audio <= 0.0:
            played = []
            upcoming = []
            for pt in pts:
                if pt[0] <= cursor_x:
                    played.append(pt)
                else:
                    upcoming.append(pt)
            return played, upcoming

        played = []
        upcoming = []
        center_y = 22.0
        mod_phase = self._wave_phase
        neg_phase_term = -mod_phase * 0.8
        harmonics = self._REPLAY_HARMONICS

        for i, (x, y1, y2) in enumerate(pts):
            if x <= cursor_x:
                env_boost, f1, f2 = harmonics[i]
                bounce = math.sin(f1 + mod_phase) * 0.6 + math.cos(f2 + neg_phase_term) * 0.4
                h = (y2 - center_y) + abs(bounce) * eff_audio * env_boost
                h = min(9.2, max(1.8, h))
                played.append((x, center_y - h, center_y + h))
            else:
                upcoming.append((x, y1, y2))

        return played, upcoming

    def _generate_active_waveform(
        self,
        center_y: float,
        level: float = 0.0,
        phase: float = 0.0,
        seed: float = 0.0,
    ) -> list[tuple[float, float, float]]:
        """Generates sleek, vertical visualizer bars (x, y_top, y_bottom) modulated by live VU levels."""
        clamped_level = max(0.0, min(1.0, float(level)))
        if clamped_level <= 0.012:
            if center_y == 22.0:
                return list(self._RESTING_MIC_BARS)
            elif center_y == 54.0:
                return list(self._RESTING_DISCORD_BARS)
            return [(x, center_y - 1.8, center_y + 1.8) for x, *_ in self._ACTIVE_BAR_CONSTANTS]

        eff_level = min(1.0, (clamped_level ** 0.45) * 1.55)
        p1 = phase + seed
        p2 = -phase * 0.9 + seed * 1.6
        p3 = phase * 1.4
        bars = []
        for x, f1, f2, f3, env_boost in self._ACTIVE_BAR_CONSTANTS:
            w1 = math.sin(f1 + p1)
            w2 = math.cos(f2 + p2) * 0.45
            w3 = math.sin(f3 + p3) * 0.25
            var = 0.35 + 0.3823529411764706 * abs(w1 + w2 + w3)
            h = min(9.2, 1.8 + eff_level * env_boost * var)
            bars.append((x, center_y - h, center_y + h))

        return bars

    # ==========================================
    # MOUSE INTERACTION & DRAGGING
    # ==========================================
    def _is_lock_hit(self, x: float, y: float) -> bool:
        return self._LOCK_HIT_X1 <= x <= self._LOCK_HIT_X2 and self._LOCK_HIT_Y1 <= y <= self._LOCK_HIT_Y2

    def _is_volume_slider_hit(self, x: float, y: float) -> bool:
        return self.mode == "REPLAY" and self._SLIDER_HIT_X1 <= x <= self._SLIDER_HIT_X2 and self._SLIDER_HIT_Y1 <= y <= self._SLIDER_HIT_Y2

    def _forward_click(self, button: str = "right") -> None:
        """Forwards mouse clicks through the locked overlay into the game window beneath."""
        if getattr(self, "_is_forwarding_click", False):
            return
        self._is_forwarding_click = True
        try:
            if sys.platform != "win32":
                return
            import ctypes

            u32 = ctypes.windll.user32
            parent = self.get_hwnd()
            if parent:
                # 1. Enable WS_EX_TRANSPARENT so the overlay is ignored by OS hit testing
                set_window_click_through(parent, enable=True)

                # 2. Fire the synthesized mouse event directly to the window beneath the cursor
                if button == "right":
                    MOUSEEVENTF_RIGHTDOWN = 0x0008
                    MOUSEEVENTF_RIGHTUP = 0x0010
                    u32.mouse_event(MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, 0)
                    u32.mouse_event(MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0)
                else:
                    MOUSEEVENTF_LEFTDOWN = 0x0002
                    MOUSEEVENTF_LEFTUP = 0x0004
                    u32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
                    u32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)

                # 3. Schedule restoring interactivity so the Lock icon and Volume slider stay clickable
                if hasattr(self, "window") and self.window:
                    self.window.after(30, self._restore_click_transparency)
        except Exception:
            pass
        finally:
            self._is_forwarding_click = False

    def _restore_click_transparency(self) -> None:
        try:
            if not getattr(self, "window", None):
                return
            parent = self.get_hwnd()
            if parent:
                set_window_click_through(parent, enable=False)
        except Exception:
            pass

    def _on_mouse_down(self, event: tk.Event) -> None:
        # 1. Click on Lock button
        if self._is_lock_hit(event.x, event.y):
            self.set_locked(not self.locked)
            return

        # 2. Click on Volume slider in Replay mode
        # ALLOWED EVEN WHEN LOCKED! (User requirement: able to change sound while locked)
        if self._is_volume_slider_hit(event.x, event.y):
            self._is_dragging_volume = True
            self._update_volume_from_mouse(event.x)
            return

        # 3. If locked, window cannot be dragged! Instead, forward the left-click into the game!
        if self.locked:
            self._forward_click(button="left")
            return

        # 4. Unlocked: Drag the overlay window
        self._is_dragging = True
        self._drag_start_x = event.x
        self._drag_start_y = event.y
        self._drag_start_x_root = getattr(event, "x_root", 0)
        self._drag_start_y_root = getattr(event, "y_root", 0)
        self._drag_win_start_x = self.window.winfo_x()
        self._drag_win_start_y = self.window.winfo_y()

    def _on_mouse_move(self, event: tk.Event) -> None:
        if self._is_dragging_volume:
            self._update_volume_from_mouse(event.x)
        elif self._is_dragging and not self.locked:
            x_root = getattr(event, "x_root", 0)
            y_root = getattr(event, "y_root", 0)
            if x_root and y_root and getattr(self, "_drag_start_x_root", 0):
                dx = x_root - self._drag_start_x_root
                dy = y_root - self._drag_start_y_root
                new_x = self._drag_win_start_x + dx
                new_y = self._drag_win_start_y + dy
            else:
                cur_x = self.window.winfo_x()
                cur_y = self.window.winfo_y()
                new_x = cur_x + (event.x - getattr(self, "_drag_start_x", event.x))
                new_y = cur_y + (event.y - getattr(self, "_drag_start_y", event.y))
            self.window.geometry(f"+{new_x}+{new_y}")

    def _on_mouse_up(self, event: tk.Event) -> None:
        if self._is_dragging_volume:
            self._is_dragging_volume = False
            self._update_volume_from_mouse(event.x)
        elif self._is_dragging:
            self._is_dragging = False
            final_x = self.window.winfo_x()
            final_y = self.window.winfo_y()
            self._sync_hit_windows()
            if self.on_position_changed:
                self.on_position_changed(final_x, final_y)

    def _on_mouse_hover(self, event: tk.Event) -> None:
        """Dynamically updates mouse cursor to reflect actionable elements."""
        try:
            if self._is_lock_hit(event.x, event.y) or (
                self.mode == "REPLAY" and self._is_volume_slider_hit(event.x, event.y)
            ):
                self.canvas.config(cursor="hand2")
            elif not self.locked:
                self.canvas.config(cursor="fleur")
            else:
                self.canvas.config(cursor="")
        except Exception:
            pass

    def _update_volume_from_mouse(self, mouse_x: float) -> None:
        """Computes volume percentage from mouse X coordinate in slider track."""
        clamped_x = max(self.SLIDER_X1, min(self.SLIDER_X2, mouse_x))
        pct = round(((clamped_x - self.SLIDER_X1) / self.TRACK_WIDTH) * 200)
        self.volume = pct
        self.render()
        if self.on_volume_changed:
            self.on_volume_changed(pct)

    def _on_right_click(self, event: tk.Event) -> None:
        """Pops up a contextual control menu when unlocked; suppressed when locked."""
        # When locked: Right-click panel is permanently disabled!
        if self.locked:
            return

        menu = tk.Menu(self.window, tearoff=0)
        lock_label = "🔓 Unlock Position" if self.locked else "🔒 Lock Position"
        menu.add_command(
            label=lock_label,
            command=lambda: self.set_locked(not self.locked),
        )
        menu.add_command(
            label="🎯 Reset Position",
            command=self.reset_position,
        )
        if self.mode == "RECORD":
            menu.add_command(
                label="🔇 Toggle Mic Mute",
                command=lambda: setattr(self, "is_muted", not self.is_muted) or self.render(),
            )
        menu.add_separator()
        menu.add_command(label="✕ Close Overlay", command=self.hide)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def destroy(self) -> None:
        """Destroys the overlay toplevel window cleanly."""
        try:
            if getattr(self, "lock_hit_win", None):
                self.lock_hit_win.destroy()
                self.lock_hit_win = None
            if getattr(self, "volume_hit_win", None):
                self.volume_hit_win.destroy()
                self.volume_hit_win = None
        except Exception:
            pass
        try:
            self.window.destroy()
        except Exception:
            pass


# GradeX Precomputed Waveform Geometry & Harmonics Table for LeagueOverlay
_step = LeagueOverlay.TRACK_WIDTH / (LeagueOverlay.NUM_BARS - 1)
LeagueOverlay._ACTIVE_BAR_CONSTANTS = tuple(
    (
        LeagueOverlay.TRACK_START_X + _i * _step,
        (_i / (LeagueOverlay.NUM_BARS - 1)) * 4.2 * math.pi,
        (_i / (LeagueOverlay.NUM_BARS - 1)) * 8.4 * math.pi,
        (_i / (LeagueOverlay.NUM_BARS - 1)) * 12.6 * math.pi,
        (0.40 + 0.60 * (math.sin((_i / (LeagueOverlay.NUM_BARS - 1)) * math.pi) ** 0.6)) * 7.5,
    )
    for _i in range(LeagueOverlay.NUM_BARS)
)
LeagueOverlay._RESTING_MIC_BARS = tuple(
    (_x, 22.0 - 1.8, 22.0 + 1.8) for _x, *_ in LeagueOverlay._ACTIVE_BAR_CONSTANTS
)
LeagueOverlay._RESTING_DISCORD_BARS = tuple(
    (_x, 54.0 - 1.8, 54.0 + 1.8) for _x, *_ in LeagueOverlay._ACTIVE_BAR_CONSTANTS
)
LeagueOverlay._REPLAY_HARMONICS = tuple(
    (
        (0.40 + 0.60 * (math.sin((_i / (LeagueOverlay.NUM_BARS - 1)) * math.pi) ** 0.6)) * 4.5,
        (_i / (LeagueOverlay.NUM_BARS - 1)) * 5.0 * math.pi,
        (_i / (LeagueOverlay.NUM_BARS - 1)) * 10.0 * math.pi,
    )
    for _i in range(LeagueOverlay.NUM_BARS)
)

# Backward-compatibility alias
LeagueHUDWidget = LeagueOverlay
