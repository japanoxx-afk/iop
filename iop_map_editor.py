"""Native terrain, resource and spawn editing."""
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from iop_map_format import MapDocument
from iop_map_tab import draw_starts, draw_resources
import iop_maps


class MapEditor(ttk.Frame):
    def __init__(self, parent, launcher):
        super().__init__(parent, padding=8)
        self.launcher=launcher; self.doc=None; self.dirty=False
        self.history=[]; self.tile=0; self.patch=None; self.anchor=None
        self.pack(fill='both', expand=True)
        bar=ttk.Frame(self);bar.pack(fill='x')
        for label,command in [('맵 열기',self.open_map),('선택 타일로 빈 맵',self.blank),('되돌리기',self.undo),('새 맵으로 저장',self.save)]:
            ttk.Button(bar,text=label,command=command).pack(side='left',padx=2)
        self.mode=tk.StringVar(value='시작 위치')
        tool=ttk.Combobox(bar,textvariable=self.mode,values=('시작 위치','타일 브러시','자원 추가','자원 삭제','언덕/영역 복사'),state='readonly',width=16)
        tool.pack(side='left',padx=5)
        self.slot=ttk.Combobox(bar,state='readonly',width=4);self.slot.pack(side='left')
        self.zoom=tk.IntVar(value=4)
        zoom=ttk.Combobox(bar,textvariable=self.zoom,values=(2,4,8),state='readonly',width=3)
        zoom.pack(side='left',padx=5);zoom.bind('<<ComboboxSelected>>',lambda e:self.redraw())
        ttk.Label(self,text='우클릭: 타일 채집 / 영역 첫 모서리 · Shift+우클릭: 영역 반대 모서리 · 좌클릭: 적용\n언덕은 절벽·경사로까지 포함해 영역 복사하세요. 청록 ◆ 자원 / 노랑 숫자 시작 위치',justify='left').pack(anchor='w',pady=4)
        self.status=tk.StringVar(value='맵을 열어 타일을 선택하세요.')
        ttk.Label(self,textvariable=self.status).pack(anchor='w')
        body=ttk.Panedwindow(self,orient='horizontal');body.pack(fill='both',expand=True)
        left=ttk.Frame(body,width=190);right=ttk.Frame(body);body.add(left,weight=1);body.add(right,weight=4)
        self.tile_info=tk.StringVar(value='원본 맵의 타일 목록')
        ttk.Label(left,textvariable=self.tile_info).pack(anchor='w')
        self.swatch=ttk.Label(left);self.swatch.pack(anchor='w',pady=4)
        self.tiles=tk.Listbox(left,exportselection=False,width=23)
        scroll=ttk.Scrollbar(left,command=self.tiles.yview);self.tiles.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right',fill='y');self.tiles.pack(fill='both',expand=True)
        self.tiles.bind('<<ListboxSelect>>',self.select_tile)
        self.canvas=tk.Canvas(right,bg='#151515',highlightthickness=0)
        self.canvas.grid(row=0,column=0,sticky='nsew')
        sx=ttk.Scrollbar(right,orient='horizontal',command=self.canvas.xview)
        sy=ttk.Scrollbar(right,orient='vertical',command=self.canvas.yview)
        sx.grid(row=1,column=0,sticky='ew');sy.grid(row=0,column=1,sticky='ns')
        right.rowconfigure(0,weight=1);right.columnconfigure(0,weight=1)
        self.canvas.configure(xscrollcommand=sx.set,yscrollcommand=sy.set)
        self.canvas.bind('<Button-1>',self.place)
        self.canvas.bind('<Button-3>',self.pick)
        self.canvas.bind('<Shift-Button-3>',self.copy_end)

    def open_map(self):
        if self.dirty and not messagebox.askyesno('맵 열기','저장하지 않은 변경을 버리고 다른 맵을 여시겠습니까?'):return
        if self.launcher._need_game_dir():return
        path=filedialog.askopenfilename(initialdir=self.launcher.gp('map','multi'),filetypes=[('IOP 맵','*.map')])
        if not path:return
        try:
            doc=MapDocument.load(path)
            palette=iop_maps.read_palette(self.launcher.game_dir,doc.theme)
            self.doc=doc;self.palette=palette
            self.dirty=False;self.history=[];self.patch=None;self.anchor=None
            self.slot['values']=[f'P{i+1}' for i in range(doc.players)];self.slot.current(0)
            self.tiles.delete(0,'end')
            for i,tile in enumerate(doc.tiles):
                flags=int.from_bytes(tile[:2],'little')
                self.tiles.insert('end',f'타일 {i:04d} · 속성 {flags:04X}')
            self.tiles.selection_set(0);self.select_tile();self.redraw()
        except Exception as exc:messagebox.showerror('맵 열기 실패',str(exc))

    def select_tile(self,event=None):
        selected=self.tiles.curselection()
        if self.doc is None or not selected:return
        self.tile=selected[0]
        self.tile_photo=tk.PhotoImage(data=self.doc.tile_ppm(self.tile,self.palette),format='PPM').zoom(3)
        self.swatch.configure(image=self.tile_photo)
        self.tile_info.set(f'타일 {self.tile} · 그래픽+통행 속성')

    def coords(self,event):
        scale=self.zoom.get()
        return int(self.canvas.canvasx(event.x)//scale),int(self.canvas.canvasy(event.y)//scale)

    def pick(self,event):
        if self.doc is None:return
        x,y=self.coords(event)
        if not (0<=x<self.doc.width and 0<=y<self.doc.height):return
        if self.mode.get()=='언덕/영역 복사':
            self.anchor=(x,y);self.status.set(f'복사 시작 ({x},{y}) · 반대 모서리에 Shift+우클릭');return
        self.tiles.selection_clear(0,'end');self.tiles.selection_set(self.doc.tile_id(x,y))
        self.tiles.see(self.doc.tile_id(x,y));self.select_tile()

    def copy_end(self,event):
        if self.doc is None or self.anchor is None:return 'break'
        try:
            self.patch=self.doc.copy_region(*self.anchor,*self.coords(event))
            self.mode.set('언덕/영역 복사')
            self.status.set(f'{self.patch[0]}×{self.patch[1]} 타일 복사됨 · 좌클릭 위치에 붙여넣기 (자원·장식 제외)')
        except Exception as exc:self.status.set(str(exc))
        return 'break'

    def checkpoint(self):
        self.history.append(self.doc.snapshot());self.history=self.history[-30:]

    def place(self,event):
        if self.doc is None:return
        x,y=self.coords(event)
        if not (0<=x<self.doc.width and 0<=y<self.doc.height):return
        mode=self.mode.get()
        if mode=='언덕/영역 복사' and self.patch is None:
            self.status.set('우클릭과 Shift+우클릭으로 복사 영역을 먼저 지정하세요.');return
        self.checkpoint()
        try:
            if mode=='시작 위치':self.doc.starts[self.slot.current()]=(x,y)
            elif mode=='타일 브러시':self.doc.paint(x,y,self.tile)
            elif mode=='자원 추가':
                if len(self.doc.resources)>=1000:raise ValueError('자원은 최대 1,000개입니다.')
                if (x,y) not in self.doc.resources:self.doc.resources.append((x,y))
            elif mode=='자원 삭제':
                nearby=[p for p in self.doc.resources if abs(p[0]-x)<=1 and abs(p[1]-y)<=1]
                if nearby:self.doc.resources.remove(min(nearby,key=lambda p:(p[0]-x)**2+(p[1]-y)**2))
            elif mode=='언덕/영역 복사':self.doc.stamp(x,y,self.patch)
            self.dirty=True;self.redraw()
        except Exception as exc:
            self.doc.restore(self.history.pop());self.status.set(str(exc))

    def blank(self):
        if self.doc is None:return
        if not messagebox.askyesno('빈 맵 만들기','현재 크기를 유지하고 선택 타일로 전체 지형을 채웁니다. 자원·장식은 제거되며 시작 위치는 유지됩니다. 진행할까요?'):return
        self.checkpoint();self.doc.blank(self.tile);self.dirty=True;self.redraw()

    def undo(self):
        if not self.history:return
        self.doc.restore(self.history.pop());self.dirty=True;self.redraw()

    def redraw(self):
        if self.doc is None:return
        scale=self.zoom.get()
        self.photo=tk.PhotoImage(data=self.doc.preview(self.palette,scale),format='PPM')
        self.canvas.delete('all');self.canvas.create_image(0,0,image=self.photo,anchor='nw')
        draw_resources(self.canvas,self.doc.resources,scale);draw_starts(self.canvas,self.doc.starts,scale)
        self.canvas.configure(scrollregion=(0,0,self.photo.width(),self.photo.height()))
        self.status.set(f'{self.doc.path.name} | {self.doc.width}×{self.doc.height} | 자원 {len(self.doc.resources)}개'+(' · 저장하지 않은 변경' if self.dirty else ''))

    def save(self):
        if self.doc is None:return
        if self.launcher.server.alive or self.launcher._launch_pending or (self.launcher._game_process and self.launcher._game_process.poll() is None):
            messagebox.showinfo('저장 대기','게임과 서버를 종료한 뒤 맵을 저장하세요.');return
        path=filedialog.asksaveasfilename(initialdir=self.launcher.gp('map','multi'),initialfile=self.doc.path.stem+'_custom.map',defaultextension='.map',filetypes=[('IOP 맵','*.map')])
        if not path:return
        try:
            self.doc.save(path);self.dirty=False;self.redraw();self.launcher._refresh_maps()
            messagebox.showinfo('저장 완료','A·B PC에 같은 맵 파일을 배치하세요.\n새 지형의 이동·자원 채취·시작 배치는 게임에서 확인하세요.')
        except Exception as exc:messagebox.showerror('저장 실패',str(exc))
