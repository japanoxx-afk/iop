"""A small, click-through elapsed-time overlay attached to the game window."""
from __future__ import annotations

import ctypes
import sys
import time
import tkinter as tk
from ctypes import wintypes


def format_elapsed(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def overlay_position(client_rect: tuple[int, int, int, int], width: int, margin: int = 14) -> tuple[int, int]:
    left, top, right, _bottom = client_rect
    return max(left + margin, right - width - margin), top + margin


class _Windows:
    GW_OWNER = 4
    GWL_EXSTYLE = -20
    WS_EX_TRANSPARENT = 0x00000020
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_LAYERED = 0x00080000
    WS_EX_NOACTIVATE = 0x08000000

    def __init__(self) -> None:
        self.user32 = ctypes.windll.user32
        self.user32.GetWindow.restype = wintypes.HWND
        self.user32.GetParent.restype = wintypes.HWND
        self.user32.GetForegroundWindow.restype = wintypes.HWND

    def game_window(self, pid: int) -> int | None:
        candidates: list[int] = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        @callback_type
        def visit(hwnd, _lparam):
            window_pid = wintypes.DWORD()
            self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
            if (window_pid.value == pid and self.user32.IsWindowVisible(hwnd)
                    and not self.user32.GetWindow(hwnd, self.GW_OWNER)):
                candidates.append(int(hwnd))
            return True

        self.user32.EnumWindows(visit, 0)
        return candidates[0] if candidates else None

    def client_rect(self, hwnd: int) -> tuple[int, int, int, int] | None:
        rect = wintypes.RECT()
        origin = wintypes.POINT(0, 0)
        if not self.user32.GetClientRect(hwnd, ctypes.byref(rect)):
            return None
        if not self.user32.ClientToScreen(hwnd, ctypes.byref(origin)):
            return None
        return origin.x, origin.y, origin.x + rect.right, origin.y + rect.bottom

    def should_show(self, hwnd: int) -> bool:
        return bool(self.user32.IsWindowVisible(hwnd) and not self.user32.IsIconic(hwnd)
                    and self.user32.GetForegroundWindow() == hwnd)

    def make_click_through(self, hwnd: int) -> None:
        # Tk exposes its client HWND; the extended styles belong on its wrapper.
        hwnd = self.user32.GetParent(hwnd) or hwnd
        style = self.user32.GetWindowLongW(hwnd, self.GWL_EXSTYLE)
        style |= self.WS_EX_TRANSPARENT | self.WS_EX_TOOLWINDOW | self.WS_EX_LAYERED | self.WS_EX_NOACTIVATE
        self.user32.SetWindowLongW(hwnd, self.GWL_EXSTYLE, style)


class GameTimerOverlay:
    """Show elapsed process time at the top-right of the game's client area."""

    def __init__(self, root: tk.Misc, process, *, clock=time.monotonic) -> None:
        self.root = root
        self.process = process
        self.clock = clock
        self.started_at = clock()
        self.window: tk.Toplevel | None = None
        self.label: tk.Label | None = None
        self.after_id = None
        self.api = _Windows() if sys.platform == "win32" else None
        self._stopped = False

    def start(self) -> None:
        if self.api is None or self._stopped:
            return
        self.window = tk.Toplevel(self.root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.configure(bg="#090909")
        self.window.attributes("-topmost", True)
        try:
            self.window.attributes("-alpha", 0.84)
        except tk.TclError:
            pass
        self.label = tk.Label(
            self.window, text="00:00", bg="#090909", fg="#ffffff",
            font=("Consolas", 16, "bold"), padx=11, pady=5,
        )
        self.label.pack()
        self.window.update_idletasks()
        self.api.make_click_through(int(self.window.winfo_id()))
        self._tick()

    def stop(self) -> None:
        self._stopped = True
        if self.after_id is not None:
            try:
                self.root.after_cancel(self.after_id)
            except (tk.TclError, RuntimeError):
                pass
            self.after_id = None
        if self.window is not None:
            try:
                self.window.destroy()
            except tk.TclError:
                pass
            self.window = None

    def _tick(self) -> None:
        if self._stopped:
            return
        try:
            if self.process.poll() is not None:
                self.stop()
                return
            hwnd = self.api.game_window(self.process.pid)
            rect = self.api.client_rect(hwnd) if hwnd else None
            if not hwnd or not rect or not self.api.should_show(hwnd):
                self.window.withdraw()
            else:
                self.label.config(text=format_elapsed(self.clock() - self.started_at))
                self.window.update_idletasks()
                width = self.window.winfo_reqwidth()
                height = self.window.winfo_reqheight()
                x, y = overlay_position(rect, width)
                self.window.geometry(f"{width}x{height}+{x}+{y}")
                self.window.deiconify()
                self.window.lift()
        except (tk.TclError, OSError, AttributeError):
            self.stop()
            return
        self.after_id = self.root.after(250, self._tick)
