"""Live replay controls: a small always-on-top panel on the game window, plus click-through
overlays for the game clock (top-left) and every player's resources (top-right).

The playback exe exposes its control block at a fixed address (see
iop_replay_patch.CTRL); this panel reads progress from it and writes pause,
speed, single-step and viewpoint requests. Keys while the game window is in
front: P pause, + / - speed, N one tick (paused), V viewpoint, H hide panel.
"""
from __future__ import annotations

import ctypes
import struct
import sys
import tkinter as tk
from ctypes import wintypes

from iop_replay import SPEED_STEPS, format_duration
from iop_replay_patch import CTRL, CTRL_VA

TEAM_MANAGER_POINTER = 0x00508300
RACE_LABELS = {1: "종족1", 2: "종족2", 3: "종족3"}

VK = {"P": 0x50, "N": 0x4E, "V": 0x56, "H": 0x48, "PAUSE": 0x13,
      "PLUS": 0xBB, "MINUS": 0xBD, "ADD": 0x6B, "SUB": 0x6D}


def speed_label(factor: float) -> str:
    return f"×{factor:g}"


def vision_options(players: list[dict], local_index: int) -> list[tuple[int, str]]:
    """(C_VISION value, label). 1 everyone, 0 recorder, 2+index one player's view."""
    options = [(1, "전체 시야 (안개 없음)")]
    for p in players:
        if p.get("type") == 6:
            continue
        index = p.get("index", p["slot"])
        mark = " (기록자)" if index == local_index else ""
        options.append((2 + index, f"{p['name']}{mark} 시야"))
    return options


class ProcessMemory:
    ACCESS = 0x0010 | 0x0020 | 0x0008 | 0x0400   # VM_READ | VM_WRITE | VM_OPERATION | QUERY

    def __init__(self, pid: int):
        self.k32 = ctypes.windll.kernel32
        self.k32.OpenProcess.restype = wintypes.HANDLE
        self.handle = self.k32.OpenProcess(self.ACCESS, False, pid)
        if not self.handle:
            raise OSError("게임 프로세스에 연결할 수 없습니다.")

    def read(self, address: int, size: int) -> bytes | None:
        buffer = ctypes.create_string_buffer(size)
        done = ctypes.c_size_t()
        if not self.k32.ReadProcessMemory(self.handle, ctypes.c_void_p(address), buffer, size,
                                          ctypes.byref(done)) or done.value != size:
            return None
        return buffer.raw

    def u32(self, address: int) -> int | None:
        data = self.read(address, 4)
        return struct.unpack("<I", data)[0] if data else None

    def write_u32(self, address: int, value: int) -> bool:
        data = struct.pack("<I", value & 0xFFFFFFFF)
        done = ctypes.c_size_t()
        return bool(self.k32.WriteProcessMemory(self.handle, ctypes.c_void_p(address), data, 4,
                                                ctypes.byref(done)))

    def close(self):
        if self.handle:
            self.k32.CloseHandle(self.handle)
            self.handle = None


class ReplayState:
    """Pure bookkeeping, separated from Windows calls for testing."""

    def __init__(self, tps: int, start_tick: int, end_tick: int, speed_index: int = SPEED_STEPS.index(1.0)):
        self.tps = max(1, tps)
        self.start_tick = start_tick
        self.end_tick = end_tick
        self.speed_index = speed_index
        self.paused = False

    @property
    def factor(self) -> float:
        return SPEED_STEPS[self.speed_index]

    def requested_tps(self) -> int:
        return max(1, round(self.tps * self.factor))

    def faster(self):
        self.speed_index = min(len(SPEED_STEPS) - 1, self.speed_index + 1)

    def slower(self):
        self.speed_index = max(0, self.speed_index - 1)

    def progress(self, tick: int) -> float:
        span = self.end_tick - self.start_tick
        return 1.0 if span <= 0 else min(1.0, max(0.0, (tick - self.start_tick) / span))

    def clock(self, tick: int) -> str:
        now = max(0, tick - self.start_tick) / self.tps
        total = max(0, self.end_tick - self.start_tick) / self.tps
        return f"{format_duration(now)} / {format_duration(total)}"


class ReplayPanel:
    BG, FG, DIM, ACCENT, WARN = "#11161f", "#e6edf6", "#8796ab", "#7ed7b5", "#ffb86b"

    def __init__(self, root, process, playback, on_restart=None, on_closed=None):
        self.root = root
        self.process = process
        self.playback = playback
        self.on_restart = on_restart
        self.on_closed = on_closed
        raw = playback.raw
        self.state = ReplayState(raw.tps, raw.start_tick, raw.end_tick)
        self.players = raw.players
        self.options = vision_options(self.players, raw.local_index)
        self.vision_pos = 0
        self.memory = None
        self.window = None
        self.after_id = None
        self.keys_down = set()
        self.hidden = False
        self.closed = False
        self.ready = False

    # ------------------------------------------------------------- window
    def start(self):
        if sys.platform != "win32":
            return
        try:
            self.memory = ProcessMemory(self.process.pid)
        except OSError:
            self.memory = None
        w = self.window = tk.Toplevel(self.root)
        w.withdraw()
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        try:
            w.attributes("-alpha", 0.93)
        except tk.TclError:
            pass
        w.configure(bg=self.BG)
        font = ("맑은 고딕", 9)
        head = tk.Frame(w, bg="#1b2433")
        head.pack(fill="x")
        meta = self.playback.meta
        title = f"리플레이 · {meta.get('map_name', '')}"
        self.title = tk.Label(head, text=title, bg="#1b2433", fg=self.ACCENT, font=("맑은 고딕", 9, "bold"),
                              anchor="w", padx=8, pady=3)
        self.title.pack(side="left", fill="x", expand=True)
        tk.Button(head, text="✕", command=self.close_game, bg="#1b2433", fg=self.FG, bd=0,
                  activebackground="#a33", font=font, padx=6).pack(side="right")
        tk.Button(head, text="_", command=self.toggle_hidden, bg="#1b2433", fg=self.FG, bd=0,
                  font=font, padx=6).pack(side="right")
        for widget in (head, self.title):
            widget.bind("<ButtonPress-1>", self._drag_start)
            widget.bind("<B1-Motion>", self._drag_move)
        self.body = tk.Frame(w, bg=self.BG, padx=8, pady=6)
        self.body.pack(fill="both")
        controls = tk.Frame(self.body, bg=self.BG)
        controls.pack(fill="x")
        button = dict(bg="#243045", fg=self.FG, activebackground="#33425c", activeforeground=self.FG,
                      bd=0, font=("맑은 고딕", 10, "bold"), width=3, pady=2)
        tk.Button(controls, text="⟲", command=self.restart, **button).pack(side="left", padx=(0, 3))
        self.play_button = tk.Button(controls, text="❚❚", command=self.toggle_pause, **button)
        self.play_button.pack(side="left", padx=3)
        tk.Button(controls, text="▸|", command=self.step, **button).pack(side="left", padx=3)
        tk.Button(controls, text="−", command=self.slower, **button).pack(side="left", padx=(10, 2))
        self.speed = tk.Label(controls, text=speed_label(self.state.factor), width=5, bg=self.BG, fg=self.FG,
                              font=("Consolas", 11, "bold"))
        self.speed.pack(side="left")
        tk.Button(controls, text="+", command=self.faster, **button).pack(side="left", padx=2)
        self.bar = tk.Canvas(self.body, height=8, bg="#26303f", highlightthickness=0, width=300)
        self.bar.pack(fill="x", pady=(7, 2))
        self.bar.bind("<Button-1>", lambda e: None)
        self.clock = tk.Label(self.body, text="", bg=self.BG, fg=self.DIM, font=("Consolas", 9), anchor="w")
        self.clock.pack(fill="x")
        vision = tk.Frame(self.body, bg=self.BG)
        vision.pack(fill="x", pady=(4, 2))
        tk.Button(vision, text="◀", command=lambda: self.cycle_vision(-1), **{**button, "width": 2}).pack(side="left")
        self.vision = tk.Label(vision, text="", bg=self.BG, fg=self.FG, font=font, anchor="center")
        self.vision.pack(side="left", fill="x", expand=True)
        tk.Button(vision, text="▶", command=lambda: self.cycle_vision(1), **{**button, "width": 2}).pack(side="right")
        self.status = tk.Label(self.body, text="P 일시정지 · +/- 속도 · N 한 틱 · V 시야 · H 숨기기",
                               bg=self.BG, fg=self.DIM, font=("맑은 고딕", 8), anchor="w", justify="left",
                               wraplength=330)
        self.status.pack(fill="x", pady=(4, 0))
        w.update_idletasks()
        self._no_activate()
        self._placed = False
        from iop_timer import OverlayWindow, ResourceTable, _Windows
        self.api = _Windows()
        self.clock_overlay = OverlayWindow(self.root, self.api)
        self.clock_label = tk.Label(self.clock_overlay.window, text="00:00", bg="#090909", fg="#ffffff",
                                    font=("Consolas", 16, "bold"), padx=11, pady=5)
        self.clock_label.pack()
        self.clock_overlay.finish()
        self.board_overlay = OverlayWindow(self.root, self.api, bg="#101820", alpha=0.88)
        self.table = ResourceTable(self.board_overlay.window, "플레이어")
        self.board_overlay.finish()
        self._tick()

    def _no_activate(self):
        try:
            user32 = ctypes.windll.user32
            hwnd = user32.GetParent(self.window.winfo_id()) or self.window.winfo_id()
            style = user32.GetWindowLongW(hwnd, -20)
            user32.SetWindowLongW(hwnd, -20, style | 0x08000000 | 0x00000080)  # NOACTIVATE | TOOLWINDOW
        except Exception:
            pass

    def _drag_start(self, event):
        self._drag = (event.x_root - self.window.winfo_x(), event.y_root - self.window.winfo_y())
        self._placed = True

    def _drag_move(self, event):
        dx, dy = self._drag
        self.window.geometry(f"+{event.x_root - dx}+{event.y_root - dy}")

    def _place(self):
        from iop_timer import _Windows
        api = _Windows()
        hwnd = api.game_window(self.process.pid)
        rect = api.client_rect(hwnd) if hwnd else None
        if not rect:
            return False
        left, top, right, bottom = rect
        # under the game clock at the top-left (the resource table owns the top-right)
        self.window.geometry(f"+{left + 14}+{top + 58}")
        return True

    # ------------------------------------------------------------- actions
    def _write(self, field, value):
        if self.memory:
            self.memory.write_u32(CTRL_VA + CTRL[field], value)

    def toggle_pause(self):
        self.state.paused = not self.state.paused
        self._write("paused", int(self.state.paused))
        self.play_button.config(text="▶" if self.state.paused else "❚❚")

    def step(self):
        if not self.state.paused:
            self.toggle_pause()
        if self.memory:
            value = self.memory.u32(CTRL_VA + CTRL["step"]) or 0
            self._write("step", value + 1)

    def faster(self):
        self.state.faster()
        self._apply_speed()

    def slower(self):
        self.state.slower()
        self._apply_speed()

    def _apply_speed(self):
        self._write("speed", self.state.requested_tps())
        self.speed.config(text=speed_label(self.state.factor))

    def cycle_vision(self, delta):
        self.vision_pos = (self.vision_pos + delta) % len(self.options)
        self._apply_vision()

    def _apply_vision(self):
        value, label = self.options[self.vision_pos]
        self._write("vision", value)
        self.vision.config(text=label)

    def toggle_hidden(self):
        self.hidden = not self.hidden
        if self.hidden:
            self.body.pack_forget()
        else:
            self.body.pack(fill="both")

    def restart(self):
        if self.on_restart:
            self.close_game()
            self.root.after(800, self.on_restart)

    def close_game(self):
        try:
            if self.process.poll() is None:
                self.process.terminate()
        except OSError:
            pass
        self.stop()

    def stop(self):
        if self.closed:
            return
        self.closed = True
        if self.after_id is not None:
            try:
                self.root.after_cancel(self.after_id)
            except tk.TclError:
                pass
        if self.window is not None:
            try:
                self.window.destroy()
            except tk.TclError:
                pass
        for overlay in (getattr(self, "clock_overlay", None), getattr(self, "board_overlay", None)):
            if overlay is not None:
                overlay.destroy()
        if self.memory:
            self.memory.close()
        if self.on_closed:
            self.on_closed()

    # ------------------------------------------------------------- polling
    def _keys(self):
        try:
            user32 = ctypes.windll.user32
            from iop_timer import _Windows
            hwnd = _Windows().game_window(self.process.pid)
            if not hwnd or user32.GetForegroundWindow() != hwnd:
                self.keys_down.clear()
                return
            for name, code in VK.items():
                down = bool(user32.GetAsyncKeyState(code) & 0x8000)
                if down and name not in self.keys_down:
                    self._key(name)
                if down:
                    self.keys_down.add(name)
                else:
                    self.keys_down.discard(name)
        except Exception:
            pass

    def _key(self, name):
        if name in ("P", "PAUSE"):
            self.toggle_pause()
        elif name in ("PLUS", "ADD"):
            self.faster()
        elif name in ("MINUS", "SUB"):
            self.slower()
        elif name == "N":
            self.step()
        elif name == "V":
            self.cycle_vision(1)
        elif name == "H":
            self.toggle_hidden()

    def _rows(self):
        manager = self.memory.u32(TEAM_MANAGER_POINTER) if self.memory else None
        if not manager:
            return []
        rows = []
        for p in self.players:
            if p.get("type") == 6:
                continue
            team = self.memory.u32(manager + 9 + p.get("index", p["slot"]) * 4)
            data = self.memory.read(team + 0x1A, 8) if team else None
            if not data:
                continue
            adamas, platinum = struct.unpack("<II", data)
            rows.append((p["name"], adamas, platinum))
        return rows

    def _overlays(self, tick):
        hwnd = self.api.game_window(self.process.pid)
        rect = self.api.client_rect(hwnd) if hwnd else None
        if not rect or not self.api.should_show(hwnd) or not self.ready:
            self.clock_overlay.hide()
            self.board_overlay.hide()
            return
        game_seconds = max(0, tick - self.state.start_tick) / self.state.tps
        self.clock_label.config(text=format_duration(game_seconds))
        self.clock_overlay.place(rect, "left")
        rows = self._rows()
        if rows:
            self.table.update(rows)
            self.board_overlay.place(rect, "right")
        else:
            self.board_overlay.hide()

    def _tick(self):
        if self.closed:
            return
        if self.process.poll() is not None:
            self.stop()
            return
        try:
            if self.memory and not self.ready:
                started = self.memory.u32(CTRL_VA + CTRL["started"])
                if started:
                    self.ready = True
                    self._apply_speed()
                    self._apply_vision()
            if not self._placed:
                self._place_tries = getattr(self, "_place_tries", 0) + 1
                if self._place() or self._place_tries > 50:
                    self._placed = True
                    self.window.deiconify()
            self._keys()
            if self.memory:
                tick = self.memory.u32(CTRL_VA + CTRL["tick"]) or self.state.start_tick
                end = self.memory.u32(CTRL_VA + CTRL["end_tick"])
                if end:
                    self.state.end_tick = end
                desync = self.memory.u32(CTRL_VA + CTRL["desync"]) or 0
                first = self.memory.u32(CTRL_VA + CTRL["first_desync"]) or 0
                ended = self.memory.u32(CTRL_VA + CTRL["ended"]) or 0
                width = max(1, self.bar.winfo_width())
                self.bar.delete("all")
                self.bar.create_rectangle(0, 0, int(width * self.state.progress(tick)), 8, fill=self.ACCENT, width=0)
                self.clock.config(text=self.state.clock(tick) + (f"   틱 {tick}" if tick else ""))
                self._overlays(tick)
                if ended:
                    self.status.config(text="재생이 끝났습니다. ⟲ 다시 보기 · ✕ 닫기", fg=self.ACCENT)
                elif desync:
                    secs = max(0, first - 1 - self.state.start_tick) / self.state.tps
                    self.status.config(
                        text=f"⚠ 기록과 다른 진행 감지 {desync}회 (처음 {format_duration(secs)}). "
                             "게임 데이터·맵이 기록 당시와 같은지 확인하세요.", fg=self.WARN)
        except (tk.TclError, OSError):
            self.stop()
            return
        self.after_id = self.root.after(200, self._tick)
