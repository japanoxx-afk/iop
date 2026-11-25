"""[리플레이] tab: StarCraft-style replay library, playback and recording settings."""
from __future__ import annotations

from pathlib import Path
import shutil
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

import iop_diagnostics as diagnostics
import iop_replay as replays

AUTO_KEEP_DEFAULT = 50


def register_file_type(executable: str) -> None:
    """Open .ioprep files with this launcher (current user only)."""
    import winreg
    command = f'"{executable}" "%1"' if executable.lower().endswith(".exe") else \
        f'"{sys.executable}" "{executable}" "%1"'
    root = winreg.HKEY_CURRENT_USER
    with winreg.CreateKey(root, r"Software\Classes\.ioprep") as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "IOP.Replay")
    with winreg.CreateKey(root, r"Software\Classes\IOP.Replay") as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "임팩트 오브 파워 리플레이")
    with winreg.CreateKey(root, r"Software\Classes\IOP.Replay\shell\open\command") as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, command)
    try:
        import ctypes
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)
    except Exception:
        pass


class ReplayTab:
    # ------------------------------------------------------------------ build
    def _build_replays(self, p):
        self._replay_entries = {}
        self._replay_panel = None
        self._replay_process = None
        self._replay_playback = None
        self._last_replay_path = None
        self._finalizing = False
        top = tk.Frame(p)
        top.pack(fill="x", padx=8, pady=(8, 4))
        tk.Button(top, text="▶  재생", font=("맑은 고딕", 11, "bold"), bg="#3b6ea5", fg="white",
                  width=10, command=self.play_selected_replay).pack(side="left")
        tk.Button(top, text="마지막 경기 보기", command=self.play_last_replay).pack(side="left", padx=(8, 0))
        tk.Button(top, text="새로고침", command=self.refresh_replays).pack(side="left", padx=(16, 0))
        tk.Button(top, text="이름 바꾸기", command=self.rename_replay).pack(side="left", padx=(4, 0))
        tk.Button(top, text="보관(다른 이름으로 저장)", command=self.keep_replay).pack(side="left", padx=(4, 0))
        tk.Button(top, text="삭제", command=self.delete_replay).pack(side="left", padx=(4, 0))
        tk.Button(top, text="가져오기…", command=self.import_replays).pack(side="left", padx=(16, 0))
        tk.Button(top, text="내보내기…", command=self.export_replay).pack(side="left", padx=(4, 0))
        tk.Button(top, text="폴더 열기", command=self.open_replay_folder).pack(side="left", padx=(4, 0))

        body = tk.PanedWindow(p, orient="horizontal", sashrelief="flat")
        body.pack(fill="both", expand=True, padx=8, pady=4)
        left = tk.Frame(body)
        columns = ("date", "map", "players", "mode", "length", "state")
        self.replay_tree = ttk.Treeview(left, columns=columns, show="tree headings", selectmode="browse")
        self.replay_tree.heading("#0", text="파일")
        self.replay_tree.column("#0", width=260)
        for col, text, width in (("date", "날짜", 130), ("map", "맵", 120), ("players", "플레이어", 260),
                                 ("mode", "방식", 110), ("length", "길이", 64), ("state", "상태", 70)):
            self.replay_tree.heading(col, text=text)
            self.replay_tree.column(col, width=width, anchor="w")
        scroll = ttk.Scrollbar(left, orient="vertical", command=self.replay_tree.yview)
        self.replay_tree.configure(yscrollcommand=scroll.set)
        self.replay_tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.replay_tree.bind("<<TreeviewSelect>>", lambda e: self._show_replay_detail())
        self.replay_tree.bind("<Double-1>", lambda e: self.play_selected_replay())
        self.replay_tree.bind("<Return>", lambda e: self.play_selected_replay())
        self.replay_tree.bind("<Delete>", lambda e: self.delete_replay())
        body.add(left, stretch="always")
        right = tk.Frame(body, width=340)
        self.replay_detail = tk.Text(right, height=20, width=44, wrap="word", font=("맑은 고딕", 9),
                                     bg="#f6f8fb", relief="flat", padx=8, pady=8)
        self.replay_detail.pack(fill="both", expand=True)
        self.replay_detail.configure(state="disabled")
        body.add(right)

        opts = tk.LabelFrame(p, text=" 녹화 설정 ", padx=10, pady=6)
        opts.pack(fill="x", padx=8, pady=(2, 8))
        self.replay_auto = tk.BooleanVar(value=bool(self.cfg.get("replay_auto_save", True)))
        self.replay_keep = tk.IntVar(value=int(self.cfg.get("replay_auto_keep", AUTO_KEEP_DEFAULT)))
        self.replay_record = tk.BooleanVar(value=bool(self.cfg.get("replay_record", True)))
        tk.Checkbutton(opts, text="경기 자동 녹화 (마지막 경기는 LastReplay)", variable=self.replay_record,
                       command=self._save_replay_options).pack(side="left")
        tk.Checkbutton(opts, text="경기마다 Auto 폴더에 따로 보관", variable=self.replay_auto,
                       command=self._save_replay_options).pack(side="left", padx=(16, 4))
        tk.Label(opts, text="최대").pack(side="left")
        spin = tk.Spinbox(opts, from_=1, to=1000, width=5, textvariable=self.replay_keep,
                          command=self._save_replay_options)
        spin.pack(side="left", padx=2)
        spin.bind("<FocusOut>", lambda e: self._save_replay_options())
        tk.Label(opts, text="개 (오래된 자동 저장부터 정리)").pack(side="left")
        tk.Button(opts, text=".ioprep 더블클릭 연결", command=self.associate_replays).pack(side="right")
        tk.Label(p, justify="left", fg="#555", font=("맑은 고딕", 9), text=(
            "• 재생 중 키: P 일시정지 · +/- 속도(×¼~×16) · N 한 틱 진행 · V 시야 전환 · H 조작창 숨기기. "
            "기본은 안개 없는 전체 시야이며 ◀ ▶ 로 플레이어별 시야를 고릅니다.\n"
            "• 재생 중에는 상대 건물·유닛도 선택할 수 있어 생산·연구 진행이 보입니다. 경기 시간은 왼쪽 위, 자원표는 오른쪽 위에 표시됩니다.\n"
            "• 리플레이는 영상이 아니라 시작 상태와 명령 기록입니다. 기록 당시와 같은 게임 데이터(밸런스 포함)가 필요하며, "
            "맵은 파일 안에 함께 저장됩니다. 다르면 재생 전에 알려 줍니다.\n"
            "• 컴퓨터 대전(싱글)과 멀티플레이 경기를 녹화합니다. 캠페인·불러온 게임은 녹화하지 않습니다.")
        ).pack(anchor="w", padx=10, pady=(0, 8))

    def _save_replay_options(self):
        try:
            keep = max(1, min(1000, int(self.replay_keep.get())))
        except (tk.TclError, ValueError):
            keep = AUTO_KEEP_DEFAULT
        self.cfg["replay_record"] = bool(self.replay_record.get())
        self.cfg["replay_auto_save"] = bool(self.replay_auto.get())
        self.cfg["replay_auto_keep"] = keep
        import iop_config
        iop_config.save(str(iop_config.config_path()), self.cfg)

    # ------------------------------------------------------------------ library
    def refresh_replays(self):
        if not getattr(self, "replay_tree", None):
            return
        tree = self.replay_tree
        selected = self._selected_replay()
        tree.delete(*tree.get_children())
        self._replay_entries = {}
        if not self.game_dir:
            return
        root = Path(self.game_dir) / replays.REPLAY_DIR
        for path, meta, error in replays.list_replays(self.game_dir):
            rel = path.relative_to(root)
            label = str(rel.with_suffix(""))
            if meta is None:
                values = ("", "", error, "", "", "손상")
            else:
                values = (meta.get("recorded_at", "").replace("T", " ")[:16], meta.get("map_name", ""),
                          replays.describe_players(meta.get("players", [])), meta.get("mode_name", ""),
                          replays.format_duration(meta.get("duration_seconds", 0)),
                          "완료" if meta.get("complete") else "중단됨")
            iid = tree.insert("", "end", text=label, values=values)
            self._replay_entries[iid] = (path, meta)
            if selected and path == selected:
                tree.selection_set(iid)
        if not tree.selection() and tree.get_children():
            tree.selection_set(tree.get_children()[0])
        self._show_replay_detail()

    def _selected_replay(self):
        tree = getattr(self, "replay_tree", None)
        if not tree or not tree.selection():
            return None
        entry = self._replay_entries.get(tree.selection()[0])
        return entry[0] if entry else None

    def _show_replay_detail(self):
        text = self.replay_detail
        text.configure(state="normal")
        text.delete("1.0", "end")
        sel = self.replay_tree.selection()
        if sel and sel[0] in self._replay_entries:
            path, meta = self._replay_entries[sel[0]]
            if meta is None:
                text.insert("end", f"{path.name}\n\n읽을 수 없는 리플레이입니다.")
            else:
                lines = [path.name, "",
                         f"맵: {meta.get('map_name', '')}  ({meta.get('map_path', '')})",
                         f"방식: {meta.get('mode_name', '')}",
                         f"날짜: {meta.get('recorded_at', '').replace('T', ' ')}",
                         f"길이: {replays.format_duration(meta.get('duration_seconds', 0))}"
                         f"  ·  명령 {meta.get('commands', 0):,}개",
                         f"상태: {'정상 종료' if meta.get('complete') else '중간에 끝남(게임 비정상 종료)'}",
                         f"런처 버전: v{meta.get('launcher_version', '?')}", "", "플레이어"]
                for p in meta.get("players", []):
                    me = "  ★기록자" if p.get("index", p.get("slot")) == meta.get("local_index") else ""
                    lines.append(f"  {p.get('slot', 0) + 1}. {p.get('name')}  [{p.get('kind')}]"
                                 f"  종족 {p.get('race')}  팀 {p.get('team')}{me}")
                if self.game_dir:
                    try:
                        problems = replays.compare_environment(meta.get("environment", {}),
                                                               replays.environment(self.game_dir))
                    except OSError:
                        problems = []
                    if problems:
                        lines += ["", "⚠ 재생 주의"] + [f"  - {x}" for x in problems]
                text.insert("end", "\n".join(lines))
        text.configure(state="disabled")

    def open_replay_folder(self):
        if self._need_game_dir():
            return
        self.open_folder(str(replays.replay_dir(self.game_dir)))

    def rename_replay(self):
        path = self._selected_replay()
        if not path:
            return
        name = simpledialog.askstring("이름 바꾸기", "새 이름", initialvalue=path.stem, parent=self)
        if not name:
            return
        target = path.with_name(replays._safe(name, 120) + replays.EXTENSION)
        if target.exists():
            messagebox.showerror("이름 바꾸기", "같은 이름의 리플레이가 있습니다.")
            return
        path.rename(target)
        self.refresh_replays()

    def keep_replay(self):
        path = self._selected_replay()
        if not path:
            return
        meta = replays.read_meta(path)
        name = simpledialog.askstring("리플레이 보관", "저장할 이름 (Replays 폴더)",
                                      initialvalue=replays.default_name(meta), parent=self)
        if not name:
            return
        target = replays._unique(replays.replay_dir(self.game_dir) / (replays._safe(name, 120) + replays.EXTENSION))
        shutil.copy2(path, target)
        self.refresh_replays()
        self.set_status(f"리플레이 보관: {target.name}")

    def delete_replay(self):
        path = self._selected_replay()
        if not path:
            return
        if not messagebox.askyesno("리플레이 삭제", f"{path.name}\n\n이 리플레이를 삭제할까요?"):
            return
        try:
            path.unlink()
        except OSError as exc:
            messagebox.showerror("삭제 실패", str(exc))
        self.refresh_replays()

    def import_replays(self):
        if self._need_game_dir():
            return
        files = filedialog.askopenfilenames(title="리플레이 가져오기",
                                            filetypes=[("IOP 리플레이", "*" + replays.EXTENSION)])
        added, failed = 0, []
        for name in files:
            try:
                replays.import_replay(self.game_dir, name)
                added += 1
            except (OSError, ValueError) as exc:
                failed.append(f"{Path(name).name}: {exc}")
        self.refresh_replays()
        if failed:
            messagebox.showwarning("가져오기", "\n".join(failed))
        elif added:
            self.set_status(f"리플레이 {added}개를 가져왔습니다.")

    def export_replay(self):
        path = self._selected_replay()
        if not path:
            return
        target = filedialog.asksaveasfilename(title="리플레이 내보내기", initialfile=path.name,
                                              defaultextension=replays.EXTENSION,
                                              filetypes=[("IOP 리플레이", "*" + replays.EXTENSION)])
        if target:
            shutil.copy2(path, target)
            self.set_status("리플레이 내보내기: " + target)

    def associate_replays(self):
        try:
            exe = sys.executable if getattr(sys, "frozen", False) else str(Path(sys.argv[0]).resolve())
            register_file_type(exe)
            messagebox.showinfo("연결 완료", ".ioprep 파일을 더블클릭하면 이 런처로 바로 재생합니다.")
        except Exception as exc:
            messagebox.showerror("연결 실패", str(exc))

    # ------------------------------------------------------------------ recording
    def finalize_recordings(self, quiet=True):
        if not self.game_dir or self._finalizing:
            return
        game_dir = self.game_dir
        keep = int(self.cfg.get("replay_auto_keep", AUTO_KEEP_DEFAULT))
        auto = bool(self.cfg.get("replay_auto_save", True))
        self._finalizing = True

        version = getattr(self, "launcher_version", "")

        def work():
            return replays.finalize_pending(game_dir, version, keep, auto)

        def done(result, error):
            self._finalizing = False
            if error:
                diagnostics.event("replay_finalize_error", error=error)
                return
            if result:
                diagnostics.event("replay_saved", files=[str(x) for x in result])
                self.set_status("리플레이 저장: " + result[-1].name)
                self.refresh_replays()
        self._job(work, done)

    def watch_recordings(self, process):
        """Save each finished match while the game keeps running, and once more on exit."""
        def poll():
            if self._closing:
                return
            self.finalize_recordings()
            if process.poll() is None:
                self.after(4000, poll)
            else:
                self.after(1500, self.finalize_recordings)
        self.after(4000, poll)

    # ------------------------------------------------------------------ playback
    def play_last_replay(self):
        if self._need_game_dir():
            return
        path = Path(self.game_dir) / replays.REPLAY_DIR / replays.LAST_REPLAY
        if not path.is_file():
            messagebox.showinfo("리플레이", "아직 저장된 마지막 경기가 없습니다.")
            return
        self.play_replay(path)

    def play_selected_replay(self):
        path = self._selected_replay()
        if path:
            self.play_replay(path)

    def play_replay(self, path):
        if self._need_game_dir():
            return
        if getattr(self, "_game_process", None) and self._game_process.poll() is None:
            messagebox.showinfo("게임 실행 중", "실행 중인 게임을 종료한 뒤 리플레이를 재생하세요.")
            return
        if self._replay_process and self._replay_process.poll() is None:
            messagebox.showinfo("리플레이 재생 중", "재생 중인 리플레이를 닫은 뒤 다시 시도하세요.")
            return
        game_dir = self.game_dir
        self._last_replay_path = Path(path)
        self.set_status("리플레이 준비 중…")

        def work():
            from iop_assets import ensure_game_files
            ensure_game_files(game_dir)
            from iop_hill_vision import apply as apply_hill
            apply_hill(game_dir)
            return replays.prepare_playback(game_dir, path)

        def done(playback, error):
            if error:
                messagebox.showerror("리플레이 재생 실패", error)
                self.set_status("리플레이 재생 실패")
                return
            if playback.warnings and not messagebox.askyesno(
                    "리플레이 재생", "\n\n".join(playback.warnings) +
                    "\n\n기록과 다르게 진행될 수 있습니다. 그래도 재생할까요?"):
                replays.cleanup_playback(game_dir, playback)
                return
            try:
                mode = self.mode_var.get() if hasattr(self, "mode_var") else "window"
                self.set_display_mode(mode)
                process = diagnostics.launch(playback.exe, game_dir)
            except Exception as exc:
                replays.cleanup_playback(game_dir, playback)
                messagebox.showerror("리플레이 재생 실패", str(exc))
                return
            diagnostics.event("replay_play", file=str(path), pid=process.pid)
            self._replay_process = process
            self._replay_playback = playback
            from iop_replay_control import ReplayPanel
            self._replay_panel = ReplayPanel(self, process, playback,
                                             on_restart=lambda: self.play_replay(path),
                                             on_closed=lambda: None)
            self._replay_panel.start()
            self.set_status(f"리플레이 재생: {Path(path).name}")
            self._watch_playback(process, playback)
        self._job(work, done)

    def _watch_playback(self, process, playback):
        def poll():
            if process.poll() is None:
                self.after(1000, poll)
                return
            replays.cleanup_playback(self.game_dir, playback)
            if self._replay_panel:
                self._replay_panel.stop()
            self.set_status("리플레이 재생 종료")
        self.after(1000, poll)

    def open_replay_argument(self, argument):
        """Launcher started by double-clicking a .ioprep file."""
        path = Path(argument)
        if not path.is_file() or path.suffix.lower() != replays.EXTENSION:
            return
        if hasattr(self, "notebook") and hasattr(self, "tab_replays"):
            self.notebook.select(self.tab_replays)
        self.play_replay(path)
