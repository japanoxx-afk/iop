"""A small, click-through elapsed-time overlay attached to the game window."""
from __future__ import annotations

import ctypes
import sys
import time
import tkinter as tk
from ctypes import wintypes

# The original 32-bit client has no ASLR.  Its root object stores the current
# battle object at 0x4e53c8.  Runtime verification shows state 3 while the
# client/setup loop is active and state 4 after the skirmish enters battle.
MATCH_OBJECT_POINTER = 0x004E53C8
MATCH_STATE_OFFSET = 0x41CD
MATCH_RUNNING = 4
TEAM_MANAGER_POINTER = 0x00508300
SLOT_TYPE_BASE = 0x006C92D5
SLOT_NAME_BASE = 0x006C9345
SLOT_NAME_SIZE = 21
LOCAL_SLOT = 0x006C940D
PLAYER_TYPES = (1, 4)  # computer, human
PLAYABLE_RACES = (1, 2, 3)


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


def active_player_rows(slots: list[dict]) -> list[tuple[str, int, int]]:
    """Return resource rows while excluding empty and observer slots."""
    return [
        (slot['name'], slot['adamas'], slot['platinum'])
        for slot in slots
        if slot.get('type') in PLAYER_TYPES
        and slot.get('race') in PLAYABLE_RACES
        and slot.get('name')
    ]


def format_resource_board(rows: list[tuple[str, int, int]]) -> str:
    lines=['관전 현황  |  아다마스 / 플래티넘']
    for name,adamas,platinum in rows:
        lines.append(f'{name[:16]}  |  {adamas:,} / {platinum:,}')
    return '\n'.join(lines)


class MatchClock:
    """Turn the engine's match state into a resettable elapsed clock."""

    def __init__(self) -> None:
        self.started_at: float | None = None

    def update(self, state: int | None, now: float) -> tuple[float | None, bool]:
        started = False
        if state == MATCH_RUNNING:
            if self.started_at is None:
                self.started_at = now
                started = True
            return max(0.0, now - self.started_at), started
        self.started_at = None
        return None, False


class _Windows:
    GW_OWNER = 4
    GWL_EXSTYLE = -20
    WS_EX_TRANSPARENT = 0x00000020
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_LAYERED = 0x00080000
    WS_EX_NOACTIVATE = 0x08000000
    PROCESS_VM_READ = 0x0010
    PROCESS_QUERY_INFORMATION = 0x0400

    def __init__(self) -> None:
        self.user32 = ctypes.windll.user32
        self.user32.GetWindow.restype = wintypes.HWND
        self.user32.GetParent.restype = wintypes.HWND
        self.user32.GetForegroundWindow.restype = wintypes.HWND
        self.kernel32 = ctypes.windll.kernel32
        self.kernel32.OpenProcess.restype = wintypes.HANDLE

    def open_process(self, pid: int):
        return self.kernel32.OpenProcess(
            self.PROCESS_VM_READ | self.PROCESS_QUERY_INFORMATION, False, pid
        )

    def close_process(self, handle) -> None:
        if handle:
            self.kernel32.CloseHandle(handle)

    def match_state(self, handle) -> int | None:
        if not handle:
            return None
        pointer = ctypes.c_uint32()
        read = ctypes.c_size_t()
        if not self.kernel32.ReadProcessMemory(
            handle, ctypes.c_void_p(MATCH_OBJECT_POINTER), ctypes.byref(pointer),
            ctypes.sizeof(pointer), ctypes.byref(read),
        ) or read.value != ctypes.sizeof(pointer) or not pointer.value:
            return None
        state = ctypes.c_uint32()
        if not self.kernel32.ReadProcessMemory(
            handle, ctypes.c_void_p(pointer.value + MATCH_STATE_OFFSET), ctypes.byref(state),
            ctypes.sizeof(state), ctypes.byref(read),
        ) or read.value != ctypes.sizeof(state):
            return None
        return state.value

    def _read(self, handle, address: int, size: int) -> bytes | None:
        value = ctypes.create_string_buffer(size)
        read = ctypes.c_size_t()
        if not self.kernel32.ReadProcessMemory(
            handle, ctypes.c_void_p(address), value, size, ctypes.byref(read)
        ) or read.value != size:
            return None
        return value.raw

    def _u32(self, handle, address: int) -> int | None:
        data=self._read(handle,address,4)
        return int.from_bytes(data,'little') if data is not None else None

    def spectator_rows(self, handle) -> list[tuple[str, int, int]] | None:
        """Read scoreboard rows only when this client occupies an observer slot."""
        manager=self._u32(handle,TEAM_MANAGER_POINTER)
        local_data=self._read(handle,LOCAL_SLOT,1)
        if not manager or local_data is None:
            return None
        local_slot=local_data[0]
        count=self._u32(handle,manager+5)
        if local_slot>=8 or count is None or count<1 or count>8:
            return None
        local_type_data=self._read(handle,SLOT_TYPE_BASE+local_slot,1)
        local_team=self._u32(handle,manager+9+local_slot*4)
        local_race_data=self._read(handle,local_team+2,1) if local_team else None
        local_type=local_type_data[0] if local_type_data else 0
        local_race=local_race_data[0] if local_race_data else 0
        if local_type!=4 or local_race in PLAYABLE_RACES:
            return None
        slots=[]
        for index in range(count):
            type_data=self._read(handle,SLOT_TYPE_BASE+index,1)
            team=self._u32(handle,manager+9+index*4)
            if type_data is None or not team:
                continue
            race_data=self._read(handle,team+2,1)
            resources=self._read(handle,team+0x1A,8)
            name_data=self._read(handle,SLOT_NAME_BASE+index*SLOT_NAME_SIZE,SLOT_NAME_SIZE)
            if race_data is None or resources is None or name_data is None:
                continue
            raw_name=name_data.split(b'\0',1)[0]
            try:name=raw_name.decode('cp949').strip()
            except UnicodeDecodeError:name=raw_name.decode('latin-1','replace').strip()
            slots.append(dict(type=type_data[0],race=race_data[0],name=name,
                              adamas=int.from_bytes(resources[:4],'little'),
                              platinum=int.from_bytes(resources[4:],'little')))
        return active_player_rows(slots)

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

    def __init__(self, root: tk.Misc, process, *, clock=time.monotonic,
                 on_match_start=None) -> None:
        self.root = root
        self.process = process
        self.clock = clock
        self.match_clock = MatchClock()
        self.on_match_start = on_match_start
        self.window: tk.Toplevel | None = None
        self.label: tk.Label | None = None
        self.board: tk.Label | None = None
        self.after_id = None
        self.api = _Windows() if sys.platform == "win32" else None
        self.process_handle = self.api.open_process(process.pid) if self.api else None
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
        self.board = tk.Label(
            self.window, text='', bg="#101820", fg="#d8f3ff",
            font=("맑은 고딕", 10, "bold"), justify='left', anchor='w',
            padx=11, pady=7,
        )
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
        if self.api is not None and self.process_handle:
            self.api.close_process(self.process_handle)
            self.process_handle = None

    def _tick(self) -> None:
        if self._stopped:
            return
        try:
            if self.process.poll() is not None:
                self.stop()
                return
            state = self.api.match_state(self.process_handle)
            elapsed, match_started = self.match_clock.update(state, self.clock())
            if match_started:
                if self.on_match_start:
                    self.on_match_start()
            hwnd = self.api.game_window(self.process.pid)
            rect = self.api.client_rect(hwnd) if hwnd else None
            rows=self.api.spectator_rows(self.process_handle) if elapsed is not None else None
            if rows is None:
                if self.board.winfo_manager(): self.board.pack_forget()
            else:
                self.board.config(text=format_resource_board(rows))
                if not self.board.winfo_manager(): self.board.pack(fill='x')
            if elapsed is None or not hwnd or not rect or not self.api.should_show(hwnd):
                self.window.withdraw()
            else:
                self.label.config(text=format_elapsed(elapsed))
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
