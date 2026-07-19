# -*- coding: utf-8 -*-
"""
임팩트 오브 파워 - 밸런스 편집기 (GUI)
표에서 셀을 더블클릭해 바로 수정하고 [게임에 적용]을 누르면 반영됩니다.
검증된 iop_balance.py 의 읽기/쓰기 로직을 그대로 사용합니다.
"""
import os, sys, csv, tempfile
import tkinter as tk
from tkinter import ttk, messagebox

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import iop_balance as iob   # 같은 폴더의 검증된 모듈 재사용

# 편집 가능한 숫자 컬럼과 화면 표시 이름
EDITABLE = ["HP", "gnd_atk", "air_atk", "gnd_range", "air_range",
            "build_time", "adamas", "platinum"]
HEADERS = {
    "name_kr": "유닛", "race": "종족", "kind": "분류",
    "HP": "체력", "gnd_atk": "지상공격", "air_atk": "공중공격",
    "gnd_range": "지상사거리", "air_range": "공중사거리",
    "build_time": "생산시간", "adamas": "아다마스", "platinum": "플래티넘",
}
COLS = ["name_kr", "race", "kind", "HP", "gnd_atk", "air_atk",
        "gnd_range", "air_range", "build_time", "adamas", "platinum"]
# 숨겨서 보관할 식별자(저장 시 필요)
HIDDEN = ["id", "type", "gnd_wid", "air_wid", "name_en"]


class Editor(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("임팩트 오브 파워 - 밸런스 편집기")
        self.geometry("1050x640")
        self.rows_meta = {}   # item_id -> dict(hidden fields)
        self._build_ui()
        self.load_from_game()

    # ---------- UI ----------
    def _build_ui(self):
        top = tk.Frame(self); top.pack(fill="x", padx=8, pady=6)
        tk.Button(top, text="현재값 불러오기", command=self.load_from_game,
                  width=14).pack(side="left", padx=3)
        tk.Button(top, text="게임에 적용", command=self.apply_to_game,
                  width=14, bg="#d0e8ff").pack(side="left", padx=3)
        tk.Button(top, text="원본으로 복구", command=self.restore_original,
                  width=14).pack(side="left", padx=3)
        tk.Label(top, text="  셀을 더블클릭 → 숫자 수정 → Enter",
                 fg="#555").pack(side="left", padx=10)

        # 종족 필터
        tk.Label(top, text="종족:").pack(side="left", padx=(20, 2))
        self.race_var = tk.StringVar(value="전체")
        race_cb = ttk.Combobox(top, textvariable=self.race_var, width=9,
                               values=["전체", "노블어스", "다크존", "아트로스"],
                               state="readonly")
        race_cb.pack(side="left")
        race_cb.bind("<<ComboboxSelected>>", lambda e: self.refresh_view())

        # 표
        frame = tk.Frame(self); frame.pack(fill="both", expand=True, padx=8, pady=4)
        self.tree = ttk.Treeview(frame, columns=COLS, show="headings",
                                 selectmode="browse")
        for c in COLS:
            self.tree.heading(c, text=HEADERS[c])
            w = 130 if c == "name_kr" else (68 if c == "race" else
                                            (52 if c == "kind" else 72))
            self.tree.column(c, width=w, anchor="center")
        self.tree.column("name_kr", anchor="w")
        vsb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", self.on_double_click)

        self.status = tk.Label(self, text="", anchor="w", fg="#333")
        self.status.pack(fill="x", padx=8, pady=(0, 6))

    # ---------- 데이터 로드 ----------
    def load_from_game(self):
        tmp = os.path.join(tempfile.gettempdir(), "_iop_edit.csv")
        iob.cmd_export(tmp)
        self.all_rows = []
        with open(tmp, "r", encoding="utf-8-sig") as fp:
            for r in csv.DictReader(fp):
                self.all_rows.append(r)
        self.refresh_view()
        self.set_status(f"{len(self.all_rows)}개 유닛을 불러왔습니다.")

    def refresh_view(self):
        self.tree.delete(*self.tree.get_children())
        self.rows_meta.clear()
        rf = self.race_var.get()
        for r in self.all_rows:
            if rf != "전체" and r["race"] != rf:
                continue
            vals = [r[c] for c in COLS]
            iid = self.tree.insert("", "end", values=vals)
            self.rows_meta[iid] = {h: r[h] for h in HIDDEN}
            self.rows_meta[iid]["_ref"] = r   # 원본 dict 참조

    # ---------- 셀 인라인 편집 ----------
    def on_double_click(self, event):
        if self.tree.identify("region", event.x, event.y) != "cell":
            return
        col = self.tree.identify_column(event.x)     # '#3'
        item = self.tree.identify_row(event.y)
        cidx = int(col[1:]) - 1
        colname = COLS[cidx]
        if colname not in EDITABLE:
            self.set_status("이 항목은 편집할 수 없습니다 (이름/종족/분류).")
            return
        x, y, w, h = self.tree.bbox(item, col)
        cur = self.tree.set(item, colname)
        ent = tk.Entry(self.tree)
        ent.place(x=x, y=y, width=w, height=h)
        ent.insert(0, cur); ent.select_range(0, "end"); ent.focus()

        def commit(_=None):
            val = ent.get().strip()
            ent.destroy()
            if not val.lstrip("-").isdigit():
                self.set_status("숫자만 입력할 수 있습니다."); return
            self.tree.set(item, colname, val)
            self.rows_meta[item]["_ref"][colname] = val   # 원본 반영
        ent.bind("<Return>", commit)
        ent.bind("<FocusOut>", commit)
        ent.bind("<Escape>", lambda e: ent.destroy())

    # ---------- 적용/복구 ----------
    def apply_to_game(self):
        if not messagebox.askyesno("적용", "수정한 값을 게임에 반영할까요?\n"
                                   "(최초 1회 원본이 자동 백업됩니다)"):
            return
        tmp = os.path.join(tempfile.gettempdir(), "_iop_apply.csv")
        fields = list(self.all_rows[0].keys())
        with open(tmp, "w", newline="", encoding="utf-8-sig") as fp:
            w = csv.DictWriter(fp, fieldnames=fields)
            w.writeheader(); w.writerows(self.all_rows)
        try:
            iob.cmd_import(tmp)
        except Exception as e:
            messagebox.showerror("오류", f"적용 중 오류:\n{e}"); return
        messagebox.showinfo("완료", "게임에 반영했습니다.\n게임을 실행해 확인하세요.")
        self.set_status("적용 완료.")

    def restore_original(self):
        if not messagebox.askyesno("복구", "게임 밸런스를 원본으로 되돌릴까요?"):
            return
        bak = os.path.join(os.path.dirname(iob.GAME), "data_backup_original")
        import shutil
        ok = True
        for fn in ("Gubattle.res", "Gweapon.res"):
            bp = os.path.join(bak, fn)
            if os.path.exists(bp):
                shutil.copy(bp, os.path.join(iob.GAME, fn))
            else:
                ok = False
        if ok:
            messagebox.showinfo("완료", "원본으로 복구했습니다.")
            self.load_from_game()
        else:
            messagebox.showwarning("백업 없음",
                "백업 파일이 없습니다. 아직 한 번도 적용하지 않았다면\n"
                "현재 상태가 곧 원본입니다.")

    def set_status(self, msg):
        self.status.config(text=msg)


if __name__ == "__main__":
    Editor().mainloop()
