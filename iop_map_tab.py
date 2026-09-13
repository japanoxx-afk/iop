import tkinter as tk
from tkinter import ttk, messagebox

import iop_maps
from iop_map_format import MapDocument


class MapViewerTab:
    def _build_map_viewer(self, parent):
        self._map_items = {}
        self._map_photo = None
        top = ttk.Frame(parent, padding=10); top.pack(fill="x")
        ttk.Label(top, text="멀티플레이 맵을 선택하면 지형 미니맵을 표시합니다.").pack(side="left")
        ttk.Button(top, text="맵 목록 새로고침", command=self._refresh_maps).pack(side="right")

        body = ttk.Panedwindow(parent, orient="horizontal"); body.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        left = ttk.Frame(body, width=260); right = ttk.Frame(body)
        body.add(left, weight=1); body.add(right, weight=3)
        self.map_tree = ttk.Treeview(left, columns=("size", "players"), show="tree headings", selectmode="browse")
        self.map_tree.heading("#0", text="맵 이름"); self.map_tree.heading("size", text="크기"); self.map_tree.heading("players", text="인원")
        self.map_tree.column("#0", width=155); self.map_tree.column("size", width=70, anchor="center"); self.map_tree.column("players", width=45, anchor="center")
        scroll = ttk.Scrollbar(left, orient="vertical", command=self.map_tree.yview)
        self.map_tree.configure(yscrollcommand=scroll.set)
        self.map_tree.pack(side="left", fill="both", expand=True); scroll.pack(side="right", fill="y")
        self.map_tree.bind("<<TreeviewSelect>>", self._show_selected_map)

        controls = ttk.Frame(right); controls.pack(fill="x", pady=(0, 6))
        self.map_info_var = tk.StringVar(value="맵을 선택하세요.")
        ttk.Label(controls, textvariable=self.map_info_var).pack(side="left")
        ttk.Label(controls, text="확대:").pack(side="right", padx=(6, 2))
        self.map_zoom = tk.IntVar(value=3)
        zoom = ttk.Combobox(controls, textvariable=self.map_zoom, values=(2, 3, 4, 5, 6), width=3, state="readonly")
        zoom.pack(side="right"); zoom.bind("<<ComboboxSelected>>", self._show_selected_map)
        frame = ttk.Frame(right); frame.pack(fill="both", expand=True)
        self.map_canvas = tk.Canvas(frame, bg="#151515", highlightthickness=0)
        sx = ttk.Scrollbar(frame, orient="horizontal", command=self.map_canvas.xview)
        sy = ttk.Scrollbar(frame, orient="vertical", command=self.map_canvas.yview)
        self.map_canvas.configure(xscrollcommand=sx.set, yscrollcommand=sy.set)
        self.map_canvas.grid(row=0, column=0, sticky="nsew"); sy.grid(row=0, column=1, sticky="ns"); sx.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1); frame.columnconfigure(0, weight=1)
        if self.game_dir: self._refresh_maps()

    def _refresh_maps(self):
        if self._need_game_dir(): return
        self.map_tree.delete(*self.map_tree.get_children()); self._map_items = {}
        try:
            maps = iop_maps.list_multiplayer(self.game_dir)
            for number, info in enumerate(maps):
                ident = f"map-{number}"
                self._map_items[ident] = info
                self.map_tree.insert("", "end", iid=ident, text=info.path.stem,
                                     values=(f"{info.width}×{info.height}", info.players))
            self.map_info_var.set(f"멀티플레이 맵 {len(maps)}개")
            if maps:
                first = self.map_tree.get_children()[0]; self.map_tree.selection_set(first); self.map_tree.focus(first); self._show_selected_map()
        except Exception as exc:
            self.map_info_var.set("맵 목록을 읽지 못했습니다.")
            messagebox.showerror("맵 뷰어", str(exc))

    def _show_selected_map(self, event=None):
        selected = self.map_tree.selection()
        if not selected: return
        info = self._map_items.get(selected[0])
        if not info: return
        try:
            palette = iop_maps.read_palette(self.game_dir, info.theme_id)
            doc = MapDocument.load(info.path)
            ppm = doc.preview(palette, self.map_zoom.get())
            self._map_photo = tk.PhotoImage(data=ppm, format="PPM")
            self.map_canvas.delete("all")
            self.map_canvas.create_image(0, 0, image=self._map_photo, anchor="nw")
            draw_resources(self.map_canvas, doc.resources, self.map_zoom.get())
            draw_starts(self.map_canvas, info.starts, self.map_zoom.get())
            self.map_canvas.configure(scrollregion=(0, 0, self._map_photo.width(), self._map_photo.height()))
            kb = info.file_size / 1024
            self.map_info_var.set(f"{info.path.name} | {doc.width}×{doc.height} | {info.players}명 | 자원 {len(doc.resources)}개 (청록 ◆)")
        except Exception as exc:
            self.map_info_var.set(f"미리보기 오류: {exc}")


def draw_starts(canvas, positions, scale):
    canvas.delete('starts')
    for number, (x, y) in enumerate(positions, 1):
        x, y = (x + 0.5) * scale, (y + 0.5) * scale
        canvas.create_oval(x-10, y-10, x+10, y+10, fill='#ffdf45', outline='black', width=2, tags='starts')
        canvas.create_text(x, y, text=str(number), fill='black', font=('Arial', 10, 'bold'), tags='starts')


def draw_resources(canvas, positions, scale):
    canvas.delete('resources')
    for x,y in positions:
        x,y=(x+.5)*scale,(y+.5)*scale
        canvas.create_polygon(x,y-4,x+4,y,x,y+4,x-4,y,fill='#21fff0',outline='#002b28',tags='resources')
