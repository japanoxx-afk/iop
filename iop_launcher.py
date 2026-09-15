# -*- coding: utf-8 -*-
"""
임팩트 오브 파워 - 통합 런처 (GUI)
 - 게임 폴더(iop.exe 위치) 지정 → 그 폴더 기준으로 실행/밸런스/패치
 - 창모드 / 전체화면 선택 실행
 - 밸런스 편집기 (표에서 직접 수정) + 패치 내보내기/적용/폴더 열기
 - 화염병 데미지 항상 유지
exe를 게임 폴더에 두면 자동 인식, 아니면 [게임 폴더 지정]으로 선택 → config.json에 저장.
"""
import os, sys, json, csv, shutil, subprocess, tempfile
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from iop_server_tab import ServerTab

VERSION = "0.022"

# ---- IPX 네트워크(Radmin) 진단/적용용 PowerShell 스크립트 ----
PS_CHECK_IPX = r"""
$ErrorActionPreference = 'SilentlyContinue'
$base = 'HKCU:\Software\IPXWrapper'
$list = @()
foreach ($a in (Get-NetAdapter | Where-Object { $_.Status -eq 'Up' })) {
    $ipObj = Get-NetIPAddress -InterfaceIndex $a.IfIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
             Where-Object { $_.IPAddress -notlike '169.254.*' } | Select-Object -First 1
    $node = ($a.MacAddress -replace '-', ':').ToUpper()
    $enabled = $null
    $key = Join-Path $base $node
    if (Test-Path $key) { $enabled = (Get-ItemProperty $key -ErrorAction SilentlyContinue).enabled }
    $list += [ordered]@{
        name = $a.InterfaceDescription
        node = $node
        ip = $ipObj.IPAddress
        enabled = $enabled
    }
}
$primaryNode = $null
if (Test-Path $base) {
    $pb = (Get-ItemProperty $base -ErrorAction SilentlyContinue).primary
    if ($pb) { $primaryNode = ($pb | ForEach-Object { '{0:X2}' -f $_ }) -join ':' }
}
$result = [ordered]@{
    adapters = $list
    primaryNode = $primaryNode
    firewallPort = [bool](Get-NetFirewallRule -DisplayName 'IPXWrapper UDP' -ErrorAction SilentlyContinue)
    firewallExe = [bool](Get-NetFirewallRule -DisplayName 'Impact of Power' -ErrorAction SilentlyContinue)
}
$result | ConvertTo-Json -Depth 4
"""

PS_APPLY_IPX = r"""
$ErrorActionPreference = 'Stop'
try {
    $exePath = $args[0]
    $base = 'HKCU:\Software\IPXWrapper'
    if (-not (Test-Path $base)) { New-Item -Path $base -Force | Out-Null }

    $radmin = Get-NetAdapter | Where-Object { $_.Status -eq 'Up' -and $_.InterfaceDescription -match 'Radmin' } |
              Select-Object -First 1
    if (-not $radmin) { throw 'Radmin VPN 어댑터를 찾을 수 없습니다. Radmin VPN이 실행되고 상대와 연결되어 있는지 확인하세요.' }

    $node = ($radmin.MacAddress -replace '-', ':').ToUpper()
    $bytes = ($node -split ':') | ForEach-Object { [Convert]::ToByte($_, 16) }
    Set-ItemProperty -Path $base -Name primary -Value ([byte[]]$bytes) -Type Binary

    $radminKey = Join-Path $base $node
    if (-not (Test-Path $radminKey)) { New-Item -Path $radminKey -Force | Out-Null }
    Set-ItemProperty -Path $radminKey -Name enabled -Value 1 -Type DWord
    if (-not (Get-ItemProperty $radminKey -ErrorAction SilentlyContinue).net) {
        Set-ItemProperty -Path $radminKey -Name net -Value ([byte[]](0,0,0,1)) -Type Binary
    }

    $disabled = @()
    Get-ChildItem $base -ErrorAction SilentlyContinue | ForEach-Object {
        if ($_.PSChildName -ne $node) {
            $p = Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue
            if ($p.enabled -eq 1) {
                Set-ItemProperty -Path $_.PSPath -Name enabled -Value 0 -Type DWord
                $disabled += $_.PSChildName
            }
        }
    }

    $fwWarn = ''
    try {
        if (-not (Get-NetFirewallRule -DisplayName 'IPXWrapper UDP' -ErrorAction SilentlyContinue)) {
            New-NetFirewallRule -DisplayName 'IPXWrapper UDP' -Direction Inbound -Protocol UDP -LocalPort 54792 -Action Allow -Profile Any | Out-Null
            New-NetFirewallRule -DisplayName 'IPXWrapper UDP Out' -Direction Outbound -Protocol UDP -LocalPort 54792 -Action Allow -Profile Any | Out-Null
        }
        if ($exePath -and (Test-Path $exePath) -and -not (Get-NetFirewallRule -DisplayName 'Impact of Power' -ErrorAction SilentlyContinue)) {
            New-NetFirewallRule -DisplayName 'Impact of Power' -Direction Inbound -Program $exePath -Action Allow -Profile Any | Out-Null
            New-NetFirewallRule -DisplayName 'Impact of Power Out' -Direction Outbound -Program $exePath -Action Allow -Profile Any | Out-Null
        }
    } catch {
        $fwWarn = $_.Exception.Message
    }

    $result = [ordered]@{
        ok = $true
        adapterName = $radmin.InterfaceDescription
        node = $node
        disabledOthers = $disabled
        firewallWarning = $fwWarn
    }
    $result | ConvertTo-Json -Depth 4
} catch {
    ([ordered]@{ ok = $false; error = $_.Exception.Message }) | ConvertTo-Json -Depth 4
}
"""

# ---- exe/py 자기 위치 (설정 파일 저장 위치) ----
if getattr(sys, "frozen", False):
    EXE_DIR = os.path.dirname(sys.executable)
else:
    EXE_DIR = os.path.dirname(os.path.abspath(__file__))
import iop_config
import iop_diagnostics as diagnostics
CONFIG = str(iop_config.config_path())

sys.path.insert(0, EXE_DIR)
import iop_balance as iob          # 밸런스 읽기/쓰기 로직 재사용

RES_FILES = ("Gubattle.res", "Gweapon.res")
DEFAULTS = {"mode": "window", "game_dir": ""}

def load_cfg():
    return {**DEFAULTS, **iop_config.load(CONFIG,EXE_DIR)}

def save_cfg(cfg):
    iop_config.save(CONFIG,cfg)

def is_game_dir(d):
    return bool(d) and os.path.exists(os.path.join(d, "iop.exe"))

# ---- 밸런스 편집 컬럼 ----
EDITABLE = ["HP", "gnd_atk", "air_atk", "gnd_range", "air_range",
            "build_time", "adamas", "platinum"]
COLS = ["name_kr", "race", "kind", "HP", "gnd_atk", "air_atk",
        "gnd_range", "air_range", "build_time", "adamas", "platinum"]
HEADERS = {"name_kr": "유닛", "race": "종족", "kind": "분류", "HP": "체력",
           "gnd_atk": "지상공격", "air_atk": "공중공격", "gnd_range": "지상사거리",
           "air_range": "공중사거리", "build_time": "생산시간",
           "adamas": "아다마스", "platinum": "플래티넘"}


from iop_hotkey_tab import HotkeyTab
from iop_map_tab import MapViewerTab

class Launcher(MapViewerTab, HotkeyTab, ServerTab, tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("임팩트 오브 파워 - 런처")
        self.geometry(f"{min(1440,self.winfo_screenwidth()-80)}x{min(900,self.winfo_screenheight()-100)}")
        self.cfg = load_cfg()
        self.all_rows = []
        self.rows_meta = {}
        self.sort_col = None
        self.sort_desc = False
        self.game_dir = self._resolve_game_dir()
        self._sync_iob()
        self._build()
        self._refresh_gamedir_label()
        # 창이 뜬 직후 게임 폴더 미지정이면 경고 + 지정 안내
        self.after(300, self._startup_check)

    def _startup_check(self):
        from iop_update import consume_result
        result = consume_result(EXE_DIR)
        if result:
            if result.get("ok"):
                messagebox.showinfo("업데이트 완료", f"v{result.get('version')} 업데이트가 적용되었습니다.")
            else:
                messagebox.showerror("업데이트 실패", str(result.get("error", "알 수 없는 오류")))
        if not self.game_dir:
            messagebox.showwarning(
                "게임 폴더 지정 필요",
                "게임 폴더가 지정되지 않았습니다.\n\n"
                "이어서 열리는 창에서 iop.exe 가 있는\n"
                "게임 설치 폴더를 선택해주세요.")
            self.choose_game_dir()
            if not self.game_dir:      # 그래도 미지정이면 재안내
                messagebox.showinfo(
                    "안내",
                    "게임 폴더를 지정하지 않으면 실행/밸런스 기능을 쓸 수 없습니다.\n"
                    "우측 상단 [게임 폴더 지정] 버튼으로 언제든 다시 지정할 수 있습니다.")

        if self.game_dir:
            if result and result.get("ok"):
                self._install_update_graphics()
            self._check_local_sync()

    def _install_update_graphics(self):
        game_dir = self.game_dir
        self.set_status("게임 그래픽 호환 파일 확인 중...")
        def work():
            from iop_assets import ensure_graphics
            return ensure_graphics(game_dir)
        def done(installed, error):
            if error:
                diagnostics.event("graphics_install_error",error=error)
                messagebox.showerror("그래픽 파일 설치 실패", error)
                return
            diagnostics.event("graphics_files_checked",installed=installed)
            if installed:
                self.set_status("게임 그래픽 호환 파일 설치: " + ", ".join(installed))
            else:
                self.set_status("게임 그래픽 호환 파일 확인 완료")
        self._job(work,done)

    # ---- 게임 폴더 결정/경로 ----
    def _resolve_game_dir(self):
        gd = self.cfg.get("game_dir", "")
        if is_game_dir(gd):
            return gd
        if is_game_dir(EXE_DIR):        # exe를 게임 폴더에 둔 경우
            return EXE_DIR
        return ""                        # 미지정

    def _sync_iob(self):
        iob.GAME = os.path.join(self.game_dir, "data") if self.game_dir else ""

    def gp(self, *a):
        return os.path.join(self.game_dir, *a) if self.game_dir else ""

    def _need_game_dir(self):
        if not self.game_dir:
            messagebox.showwarning("게임 폴더 필요",
                "먼저 [게임 폴더 지정] 으로 iop.exe 가 있는 폴더를 선택하세요.")
            return True
        return False

    # ---------------- UI ----------------
    def _build(self):
        head = tk.Frame(self, bg="#2b2b3a"); head.pack(fill="x")
        tk.Label(head, text="  IMPACT of POWER", bg="#2b2b3a", fg="#e8e8f0",
                 font=("Arial Black", 16)).pack(side="left", pady=10)
        tk.Label(head, text="임팩트 오브 파워 런처   ", bg="#2b2b3a", fg="#9aa0b5",
                 font=("맑은 고딕", 10)).pack(side="right", pady=14)
        tk.Button(head, text="업데이트 확인", command=self.check_update,
                  font=("맑은 고딕", 9)).pack(side="right", pady=10, padx=(0, 6))
        tk.Label(head, text=f"v{VERSION}   ", bg="#2b2b3a", fg="#6a7a95",
                 font=("맑은 고딕", 9)).pack(side="right", pady=14)

        # 게임 폴더 줄
        gf = tk.Frame(self); gf.pack(fill="x", padx=10, pady=(8, 2))
        tk.Label(gf, text="게임 폴더:", font=("맑은 고딕", 9, "bold")).pack(side="left")
        self.gd_label = tk.Label(gf, text="", fg="#0a5", font=("맑은 고딕", 9), anchor="w")
        self.gd_label.pack(side="left", fill="x", expand=True, padx=6)
        tk.Button(gf, text="게임 폴더 지정", command=self.choose_game_dir).pack(side="right")

        nb = ttk.Notebook(self); nb.pack(fill="both", expand=True, padx=6, pady=6)
        self.tab_run = tk.Frame(nb); nb.add(self.tab_run, text="  게임 실행  ")
        self.tab_bal = tk.Frame(nb); nb.add(self.tab_bal, text="  밸런스 편집  ")
        self.tab_server = tk.Frame(nb); nb.add(self.tab_server, text="  프리서버 ON/OFF  ")
        self.tab_maps = tk.Frame(nb); nb.add(self.tab_maps, text="  멀티 맵 뷰어  ")
        self._build_run(self.tab_run)
        self._build_balance(self.tab_bal)
        self._build_server(self.tab_server)
        self._build_map_viewer(self.tab_maps)
        from iop_map_editor import MapEditor
        self.tab_map_editor = tk.Frame(nb)
        nb.add(self.tab_map_editor, text='  멀티 맵 에디터  ')
        self.map_editor = MapEditor(self.tab_map_editor, self)
        self.tab_hotkeys=tk.Frame(nb); nb.add(self.tab_hotkeys,text="  생산 단축키  ")
        self._build_hotkeys(self.tab_hotkeys)

        self.status = tk.Label(self, text="준비됨", anchor="w", fg="#333",
                               bd=1, relief="sunken")
        self.status.pack(fill="x", side="bottom")

    def _refresh_gamedir_label(self):
        if self.game_dir:
            self.gd_label.config(text=self.game_dir, fg="#0a5")
        else:
            self.gd_label.config(text="(지정 안 됨 — [게임 폴더 지정]을 눌러 iop.exe 폴더 선택)",
                                 fg="#c00")

    def choose_game_dir(self):
        init = self.game_dir or EXE_DIR
        d = filedialog.askdirectory(title="iop.exe 가 있는 게임 폴더 선택", initialdir=init)
        if not d:
            return
        if not is_game_dir(d):
            messagebox.showerror("오류", "선택한 폴더에 iop.exe 가 없습니다.\n게임 설치 폴더를 선택하세요.")
            return
        self.game_dir = d
        self.cfg["game_dir"] = d; save_cfg(self.cfg)
        self._sync_iob()
        self._refresh_gamedir_label()
        self.set_status("게임 폴더 지정됨: " + d)
        # 밸런스가 열려있으면 새로 로드
        if self.all_rows:
            self.load_balance()
        if hasattr(self, "map_tree"):
            self._refresh_maps()

    # ---------------- 실행 탭 ----------------
    def _build_run(self, p):
        box = tk.LabelFrame(p, text=" 화면 모드 ", font=("맑은 고딕", 11), padx=16, pady=12)
        box.pack(fill="x", padx=30, pady=(24, 12))
        self.mode_var = tk.StringVar(value="window")
        tk.Radiobutton(box, text="창 모드", variable=self.mode_var, value="window",
                       font=("맑은 고딕", 11)).pack(side="left", padx=20)
        tk.Radiobutton(box, text="전체 화면", variable=self.mode_var, value="full",
                       font=("맑은 고딕", 11)).pack(side="left", padx=20)
        tk.Label(box, text="(게임 중 Alt+Enter 로도 전환)", fg="#888").pack(side="left", padx=16)

        tk.Button(p, text="▶  게임 시작", font=("맑은 고딕", 18, "bold"),
                  bg="#3b6ea5", fg="white", height=2, command=self.launch_game
                  ).pack(fill="x", padx=30, pady=10)

        tk.Button(p, text="세이브 폴더 열기", height=2,
                  command=lambda: self.open_folder(self.gp("savegame"))
                  ).pack(fill="x", padx=30, pady=6)

        tk.Label(p, justify="left", fg="#555", font=("맑은 고딕", 9),
                 text=("• 런처를 열 때마다 창 모드가 기본입니다.\n"
                       "• 게임 폴더만 지정하면 어디서 실행하든 동작합니다.\n"
                       "• 다른 PC로 옮길 때는 게임 폴더를 통째로 복사하고, 그 폴더를 지정하세요.")
                 ).pack(anchor="w", padx=32, pady=18)

    def set_display_mode(self, mode):
        from iop_sync import display_mode
        display_mode(self.gp("ddraw.ini"), mode)

    def launch_game(self):
        self.launch_private_game()

    def open_folder(self, path):
        if path and os.path.isdir(path):
            os.startfile(path)
        else:
            messagebox.showinfo("안내", "폴더가 없습니다:\n" + str(path))

    # ---------------- 밸런스 탭 ----------------
    def _build_balance(self, p):
        top = tk.Frame(p); top.pack(fill="x", padx=8, pady=6)
        tk.Button(top, text="현재값 불러오기", command=self.load_balance, width=13).pack(side="left", padx=3)
        tk.Button(top, text="변경 저장(게임 반영)", command=self.save_balance,
                  width=16, bg="#d0e8ff").pack(side="left", padx=3)
        tk.Button(top, text="원본 복구", command=self.restore_balance, width=10).pack(side="left", padx=3)
        tk.Label(top, text=" | ").pack(side="left")
        tk.Button(top, text="패치 내보내기", command=self.export_patch, width=12).pack(side="left", padx=3)
        tk.Button(top, text="패치 적용", command=self.apply_patch, width=9).pack(side="left", padx=3)
        tk.Button(top, text="패치 폴더 열기", command=self.open_patch_folder, width=12).pack(side="left", padx=3)
        tk.Label(top, text="  더블클릭→수정→Enter", fg="#666").pack(side="left", padx=6)

        # 종족 필터 라디오
        filt = tk.Frame(p); filt.pack(fill="x", padx=10, pady=(2, 0))
        tk.Label(filt, text="종족 필터:", font=("맑은 고딕", 9, "bold")).pack(side="left")
        self.race_filter = tk.StringVar(value="전체")
        for rc in ["전체", "노블어스", "다크존", "아트로스"]:
            tk.Radiobutton(filt, text=rc, variable=self.race_filter, value=rc,
                           command=self.refresh_tree).pack(side="left", padx=4)
        tk.Label(filt, text="   (컬럼 헤더 클릭 → 오름/내림 정렬)", fg="#888").pack(side="left", padx=8)

        fr = tk.Frame(p); fr.pack(fill="both", expand=True, padx=8, pady=4)
        self.tree = ttk.Treeview(fr, columns=COLS, show="headings", selectmode="browse")
        for c in COLS:
            self.tree.heading(c, text=HEADERS[c], command=lambda cc=c: self.sort_by(cc))
            w = 120 if c == "name_kr" else (66 if c == "race" else (48 if c == "kind" else 70))
            self.tree.column(c, width=w, anchor="center")
        self.tree.column("name_kr", anchor="w")
        vsb = ttk.Scrollbar(fr, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", self.on_edit)

    def load_balance(self):
        if self._need_game_dir():
            return
        try:
            tmp = os.path.join(tempfile.gettempdir(), "_iop_lc.csv")
            iob.cmd_export(tmp)
            self.all_rows = list(csv.DictReader(open(tmp, encoding="utf-8-sig")))
        except Exception as e:
            messagebox.showerror("오류", f"밸런스 데이터를 불러올 수 없습니다.\n"
                                 f"게임 폴더가 올바른지 확인하세요.\n\n{e}")
            return
        self.sort_col = None; self.sort_desc = False
        self.race_filter.set("전체")
        self.refresh_tree()

    def refresh_tree(self):
        """현재 종족 필터 + 정렬 상태로 표를 다시 그린다."""
        self.tree.delete(*self.tree.get_children()); self.rows_meta.clear()
        rows = list(self.all_rows)
        rf = self.race_filter.get()
        if rf != "전체":
            rows = [r for r in rows if r.get("race") == rf]
        if self.sort_col:
            numeric = self.sort_col in EDITABLE
            def key(r):
                v = r.get(self.sort_col, "")
                if numeric:
                    try: return (0, int(v))
                    except Exception: return (0, 0)
                return (1, str(v))
            rows.sort(key=key, reverse=self.sort_desc)
        for r in rows:
            iid = self.tree.insert("", "end", values=[r[c] for c in COLS])
            self.rows_meta[iid] = r
        # 헤더에 정렬 방향 표시
        for c in COLS:
            arrow = ""
            if c == self.sort_col:
                arrow = "  ▼" if self.sort_desc else "  ▲"
            self.tree.heading(c, text=HEADERS[c] + arrow)
        self.set_status(f"{len(rows)}개 표시 (전체 {len(self.all_rows)}) "
                        f"{'· 필터:'+rf if rf!='전체' else ''}")

    def sort_by(self, col):
        if self.sort_col == col:
            self.sort_desc = not self.sort_desc
        else:
            self.sort_col = col; self.sort_desc = False
        self.refresh_tree()

    def on_edit(self, ev):
        if self.tree.identify("region", ev.x, ev.y) != "cell":
            return
        col = self.tree.identify_column(ev.x); item = self.tree.identify_row(ev.y)
        ci = int(col[1:]) - 1; name = COLS[ci]
        if name not in EDITABLE:
            self.set_status("이름/종족/분류는 편집할 수 없습니다."); return
        x, y, w, h = self.tree.bbox(item, col)
        cur = self.tree.set(item, name)
        e = tk.Entry(self.tree); e.place(x=x, y=y, width=w, height=h)
        e.insert(0, cur); e.select_range(0, "end"); e.focus()
        def commit(_=None):
            v = e.get().strip(); e.destroy()
            if not v.lstrip("-").isdigit():
                self.set_status("숫자만 입력하세요."); return
            self.tree.set(item, name, v); self.rows_meta[item][name] = v
        e.bind("<Return>", commit); e.bind("<FocusOut>", commit)
        e.bind("<Escape>", lambda x: e.destroy())

    def save_balance(self):
        if self._need_game_dir():
            return
        if not self.all_rows:
            messagebox.showinfo("안내", "먼저 '현재값 불러오기'를 하세요."); return
        if not messagebox.askyesno("반영", "수정값을 게임에 반영할까요?\n(원본 자동 백업, 화염병 데미지 유지)"):
            return
        tmp = os.path.join(tempfile.gettempdir(), "_iop_apply.csv")
        with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(self.all_rows[0].keys()))
            w.writeheader(); w.writerows(self.all_rows)
        try:
            iob.cmd_import(tmp)
        except Exception as e:
            messagebox.showerror("오류", f"반영 실패:\n{e}"); return
        messagebox.showinfo("완료", "게임에 반영했습니다."); self.set_status("밸런스 반영 완료")

    def restore_balance(self):
        if self._need_game_dir():
            return
        if not messagebox.askyesno("복구", "원본 밸런스로 되돌릴까요? (화염병 데미지는 유지)"):
            return
        rdir = self.gp("_original_flame")
        if os.path.isdir(rdir):
            for fn in RES_FILES:
                sp = os.path.join(rdir, fn)
                if os.path.exists(sp):
                    shutil.copy(sp, self.gp("data", fn))
            messagebox.showinfo("완료", "원본으로 복구했습니다."); self.load_balance()
        else:
            messagebox.showwarning("안내", "_original_flame 폴더가 없습니다.")

    def export_patch(self):
        if self._need_game_dir():
            return
        pdir = self.gp("_balance_patch"); os.makedirs(pdir, exist_ok=True)
        for fn in RES_FILES:
            shutil.copy(self.gp("data", fn), os.path.join(pdir, fn))
        messagebox.showinfo("완료",
            f"현재 밸런스를 패치로 내보냈습니다:\n{pdir}\n\n"
            "이 폴더의 파일을 다른 사람에게 전달하면 됩니다.")
        self.set_status("패치 내보내기 완료")

    def apply_patch(self):
        if self._need_game_dir():
            return
        pdir = self.gp("_balance_patch")
        if not os.path.exists(os.path.join(pdir, "Gubattle.res")):
            messagebox.showinfo("안내", "_balance_patch 폴더에 패치 파일이 없습니다."); return
        for fn in RES_FILES:
            sp = os.path.join(pdir, fn)
            if os.path.exists(sp):
                shutil.copy(sp, self.gp("data", fn))
        try:                                    # 패치에도 화염병 데미지 유지
            d, n, i, b = iob.load(self.gp("data", "Gweapon.res"))
            iob.apply_flame_fix(d, i, b, n)
            open(self.gp("data", "Gweapon.res"), "wb").write(d)
        except Exception:
            pass
        messagebox.showinfo("완료", "패치를 적용했습니다."); self.load_balance()

    def open_patch_folder(self):
        if self._need_game_dir():
            return
        pdir = self.gp("_balance_patch")          # 설정된 게임 폴더 기준
        if not os.path.isdir(pdir):
            os.makedirs(pdir, exist_ok=True)
            try:                                   # 상대방 안내용 설명 파일
                guide = os.path.join(pdir, "사용법.txt")
                with open(guide, "w", encoding="utf-8") as f:
                    f.write("[임팩트 오브 파워 - 밸런스 패치 폴더]\n\n"
                            "1) 상대에게 받은 Gubattle.res / Gweapon.res 를 이 폴더에 넣으세요.\n"
                            "2) 런처의 [패치 적용] 버튼을 누르면 게임에 반영됩니다.\n\n"
                            "※ 멀티플레이는 양쪽의 이 두 파일이 같아야 싱크가 맞습니다.\n")
            except Exception:
                pass
            self.set_status("패치 폴더가 없어 새로 만들었습니다: " + pdir)
        else:
            self.set_status("패치 폴더를 열었습니다: " + pdir)
        os.startfile(pdir)

    # ---------------- 네트워크(IPX) 탭 ----------------
    def _build_network(self, p):
        box = tk.LabelFrame(p, text=" IPX 네트워크 (Radmin VPN) ", font=("맑은 고딕", 11), padx=16, pady=12)
        box.pack(fill="x", padx=20, pady=(20, 10))
        tk.Label(box, justify="left", fg="#555", font=("맑은 고딕", 9),
                 text=("Radmin·Hamachi 등 VPN이 여러 개 설치되어 있으면 IPXWrapper가\n"
                       "엉뚱한 네트워크로 통신해 상대와 연결되지 않을 수 있습니다.\n"
                       "[자동 설정 적용]을 누르면 Radmin만 사용하도록 자동으로 맞춰줍니다.")
                 ).pack(anchor="w")

        btns = tk.Frame(p); btns.pack(fill="x", padx=20, pady=4)
        tk.Button(btns, text="상태 확인", width=14, command=self.check_ipx).pack(side="left", padx=3)
        tk.Button(btns, text="Radmin으로 자동 설정 적용", width=22, bg="#3b6ea5", fg="white",
                  command=self.apply_ipx_fix).pack(side="left", padx=3)

        txtfr = tk.Frame(p); txtfr.pack(fill="both", expand=True, padx=20, pady=10)
        self.net_text = tk.Text(txtfr, height=18, font=("Consolas", 10), wrap="word", state="disabled")
        vsb2 = ttk.Scrollbar(txtfr, orient="vertical", command=self.net_text.yview)
        self.net_text.configure(yscrollcommand=vsb2.set)
        self.net_text.pack(side="left", fill="both", expand=True)
        vsb2.pack(side="right", fill="y")
        self._net_show("[상태 확인]을 눌러 현재 네트워크 설정을 확인하세요.")

    def _net_show(self, text):
        self.net_text.config(state="normal")
        self.net_text.delete("1.0", "end")
        self.net_text.insert("1.0", text)
        self.net_text.config(state="disabled")

    def run_ps(self, script, args=None):
        """PowerShell 스크립트를 임시파일로 실행하고 stdout/stderr 반환. 콘솔 창은 띄우지 않음."""
        fd, path = tempfile.mkstemp(suffix=".ps1")
        try:
            with os.fdopen(fd, "w", encoding="utf-8-sig") as f:
                f.write(script)
            cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path]
            if args:
                cmd += [a for a in args if a]
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=25,
                               creationflags=flags)
            return r.stdout, r.stderr
        except Exception as e:
            return "", str(e)
        finally:
            try:
                os.remove(path)
            except Exception:
                pass

    @staticmethod
    def _as_list(v):
        """PowerShell ConvertTo-Json 은 원소 1개짜리 배열을 객체/문자열로 풀어버리므로 보정."""
        if v is None:
            return []
        if isinstance(v, list):
            return v
        return [v]

    def check_ipx(self):
        self.set_status("네트워크 상태 확인 중...")
        out, err = self.run_ps(PS_CHECK_IPX)
        try:
            data = json.loads(out)
        except Exception:
            self._net_show("상태 확인 실패\n\n" + (err or out or "알 수 없는 오류"))
            self.set_status("네트워크 확인 실패")
            return

        adapters = self._as_list(data.get("adapters"))
        primary_node = data.get("primaryNode")
        radmin = None
        conflicts = []
        lines = ["[네트워크 어댑터]"]
        for a in adapters:
            name = a.get("name") or "(알 수 없음)"
            node = a.get("node")
            en = a.get("enabled")
            en_str = {1: "예", 0: "아니오"}.get(en, "미설정")
            mark = "  ★ 현재 주 통신망" if node and node == primary_node else ""
            is_radmin = "radmin" in name.lower()
            if is_radmin:
                radmin = a
            if en == 1 and not is_radmin:
                conflicts.append(name)
            lines.append(f"- {name}")
            lines.append(f"    IP: {a.get('ip') or '없음'}   IPX 활성화: {en_str}{mark}")

        lines.append("")
        if not radmin:
            lines.append("⚠ Radmin VPN 어댑터를 찾지 못했습니다.")
            lines.append("   Radmin VPN이 실행되고 상대와 연결되어 있는지 확인하세요.")
        elif radmin.get("node") != primary_node:
            lines.append("⚠ 주 통신망(primary)이 Radmin이 아닙니다.")
            lines.append("   [Radmin으로 자동 설정 적용]을 눌러 고치세요.")
        else:
            lines.append("✓ 주 통신망이 Radmin으로 정상 설정되어 있습니다.")

        if conflicts:
            lines.append("⚠ 다른 네트워크가 활성화되어 있어 충돌할 수 있습니다: " + ", ".join(conflicts))

        lines.append("")
        lines.append("[방화벽]")
        lines.append("  IPX 포트(UDP 54792): " + ("허용됨" if data.get("firewallPort") else "규칙 없음"))
        lines.append("  iop.exe 자체: " + ("허용됨" if data.get("firewallExe") else "규칙 없음"))

        self._net_show("\n".join(lines))
        self.set_status("네트워크 상태 확인 완료")

    def apply_ipx_fix(self):
        if not messagebox.askyesno(
            "적용",
            "Radmin VPN만 사용하도록 네트워크 설정을 바꿉니다.\n"
            "(다른 VPN 인터페이스는 비활성화되고, 방화벽 예외가 추가됩니다)\n\n"
            "진행할까요?"):
            return
        self.set_status("네트워크 설정 적용 중...")
        exe = self.gp("iop.exe") if self.game_dir else ""
        out, err = self.run_ps(PS_APPLY_IPX, args=[exe] if exe else None)
        try:
            data = json.loads(out)
        except Exception:
            messagebox.showerror("오류", "적용 실패:\n" + (err or out or "알 수 없는 오류"))
            self.set_status("네트워크 설정 적용 실패")
            return
        if not data.get("ok"):
            messagebox.showerror("오류", data.get("error", "알 수 없는 오류"))
            self.set_status("네트워크 설정 적용 실패")
            return

        disabled = self._as_list(data.get("disabledOthers"))
        msg = [f"Radmin 어댑터: {data.get('adapterName')}",
               f"주 통신망으로 설정 완료 ({data.get('node')})"]
        if disabled:
            msg.append("비활성화된 다른 네트워크: " + ", ".join(disabled))
        if data.get("firewallWarning"):
            msg.append("\n⚠ 방화벽 규칙 추가 중 경고:\n" + data["firewallWarning"] +
                       "\n(런처를 관리자 권한으로 실행하면 해결됩니다)")
        messagebox.showinfo("완료", "\n".join(msg))
        self.check_ipx()
        self.set_status("네트워크 설정 적용 완료")

    # ---------------- 자동 업데이트 ----------------
    def check_update(self):
        if getattr(self,'_update_pending',False):return
        if self.server.alive:
            messagebox.showinfo('서버 실행 중','서버 OFF 후 업데이트를 확인하세요.'); return
        self._update_pending=True
        self.set_status("업데이트 확인 중...")
        from iop_update import fetch_manifest, version_tuple
        def read():
            return fetch_manifest()
        def done(manifest, error):
            self._update_pending=False
            if error:
                messagebox.showerror("업데이트 오류", "GitHub 업데이트 확인 실패:\n" + error)
                self.set_status("업데이트 확인 실패")
                return
            remote_ver = manifest["version"]
            if version_tuple(remote_ver) <= version_tuple(VERSION):
                messagebox.showinfo("업데이트 확인", f"현재 최신 버전입니다. (v{VERSION})")
                self.set_status(f"최신 버전 (v{VERSION})")
                return
            if not messagebox.askyesno("업데이트", f"새 버전 v{remote_ver}을 GitHub에서 내려받을까요?\n현재 버전: v{VERSION}"):
                self.set_status("업데이트 보류됨")
                return
            self.do_update(manifest)
        self._job(read, done)

    def do_update(self, manifest):
        if not getattr(sys, "frozen", False):
            messagebox.showinfo(
                "안내",
                "개발 모드(.py 실행)에서는 자동 업데이트를 지원하지 않습니다.\n"
                "GitHub 저장소에서 최신 소스를 받아주세요.")
            return

        from iop_update import download, schedule_replace
        self._update_pending=True
        self.set_status(f"업데이트 다운로드 중... (v{manifest['version']})")
        def receive():
            return download(manifest)
        def done(path, error):
            if error:
                self._update_pending=False
                messagebox.showerror("업데이트 오류", "다운로드 또는 SHA-256 검증 실패:\n" + error)
                self.set_status("업데이트 다운로드 실패")
                return
            try:
                self._save_network()
                messagebox.showinfo("업데이트", "확인을 누르면 업데이트를 적용하고 런처를 자동 재시작합니다.")
                schedule_replace(path, sys.executable, manifest["version"])
            except Exception as exc:
                self._update_pending=False
                path.unlink(missing_ok=True)
                messagebox.showerror("업데이트 오류", str(exc))
                return
            self.destroy()
        self._job(receive, done)

    def set_status(self, m):
        self.status.config(text=m)

    def save_network_config(self):
        save_cfg(self.cfg)


if __name__ == "__main__":
    if len(sys.argv)>1 and sys.argv[1] in ('--set-hosts','--set-hosts-auto','--radmin-firewall'):
        from iop_admin import run_admin_action
        sys.exit(run_admin_action(sys.argv[1:]))
    else:
        Launcher().mainloop()
