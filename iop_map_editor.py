"""Native terrain, resource and spawn editing."""
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from iop_map_format import MapDocument
from iop_map_tab import draw_starts, draw_resources
import iop_maps
import hashlib


class MapEditor(ttk.Frame):
    def __init__(self, parent, launcher):
        super().__init__(parent, padding=8)
        self.launcher=launcher; self.doc=None; self.dirty=False
        self.history=[]; self.tile=0; self.patch=None; self.anchor=None
        self._stroke=False; self._last_cell=None
        self.tile_order=[]
        self.pack(fill='both', expand=True)
        bar=ttk.Frame(self);bar.pack(fill='x')
        for label,command in [('맵 열기',self.open_map),('평지 빈 맵',self.blank),('시작 주변 평지',self.clear_spawns),('되돌리기',self.undo),('저장',self.save),('다른 이름으로 저장',lambda:self.save(True))]:
            ttk.Button(bar,text=label,command=command).pack(side='left',padx=2)
        bar=ttk.Frame(self);bar.pack(fill='x',pady=3)
        self.mode=tk.StringVar(value='시작 위치')
        tool=ttk.Combobox(bar,textvariable=self.mode,values=('시작 위치','타일 브러시','자원 추가','자원 삭제','언덕/영역 복사'),state='readonly',width=16)
        tool.pack(side='left',padx=5)
        self.slot=ttk.Combobox(bar,state='readonly',width=4);self.slot.pack(side='left')
        self.zoom=tk.IntVar(value=4)
        zoom=ttk.Combobox(bar,textvariable=self.zoom,values=(2,4,8,16,24,32,48,64),state='readonly',width=3)
        zoom.pack(side='left',padx=5);zoom.bind('<<ComboboxSelected>>',lambda e:self.redraw())
        ttk.Label(self,text='우클릭: 평지로 지우기 · Ctrl+우클릭: 타일 채집 / 영역 첫 모서리 · Shift+우클릭: 영역 반대 모서리 · 좌클릭: 적용\n언덕은 절벽·경사로까지 포함해 영역 복사하세요. 청록 ◆ 자원 / 노랑 숫자 시작 위치',justify='left').pack(anchor='w',pady=4)
        self.status=tk.StringVar(value='맵을 열어 타일을 선택하세요.')
        ttk.Label(self,textvariable=self.status).pack(anchor='w')
        body=ttk.Panedwindow(self,orient='horizontal');body.pack(fill='both',expand=True)
        left=ttk.Frame(body,width=190);right=ttk.Frame(body);body.add(left,weight=1);body.add(right,weight=4)
        self.tile_info=tk.StringVar(value='원본 맵의 타일 목록')
        ttk.Label(left,textvariable=self.tile_info).pack(anchor='w')
        self.swatch=ttk.Label(left);self.swatch.pack(anchor='w',pady=4)
        self.sort_order=tk.StringVar(value='번호순')
        category=ttk.Combobox(left,textvariable=self.sort_order,values=('번호순','일반 먼저','언덕 먼저','입구 먼저','지상형 먼저','장애물형 먼저'),state='readonly')
        category.pack(fill='x');category.bind('<<ComboboxSelected>>',self.sort_tiles)
        ttk.Label(left,text='타일 정렬 · 지도 위 휠: 확대/축소').pack(anchor='w')
        self.gallery=ttk.Frame(left);self.gallery.pack(fill='x')
        paging=ttk.Frame(left);paging.pack(fill='x')
        ttk.Button(paging,text='◀',width=4,command=lambda:self.tile_page(-1)).pack(side='left')
        ttk.Button(paging,text='▶',width=4,command=lambda:self.tile_page(1)).pack(side='right')
        self.page=0;self.gallery_images=[]
        ttk.Button(left,text='◆ 자원 타일 선택',command=lambda:self.choose_tool('자원 추가')).pack(fill='x',pady=3)
        ttk.Button(left,text='자원 지우개',command=lambda:self.choose_tool('자원 삭제')).pack(fill='x',pady=3)
        ttk.Label(left,text='타일 선택 → 왼쪽 드래그\n자원 선택 → 지도 클릭').pack(anchor='w')
        self.tiles=tk.Listbox(left,exportselection=False,width=23)
        scroll=ttk.Scrollbar(left,command=self.tiles.yview);self.tiles.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right',fill='y');self.tiles.pack(fill='both',expand=True)
        self.tiles.bind('<<ListboxSelect>>',self.select_tile)
        self.canvas=tk.Canvas(right,bg='#151515',highlightthickness=0)
        self.canvas.grid(row=0,column=0,sticky='nsew')
        sx=ttk.Scrollbar(right,orient='horizontal',command=lambda *a:self.scroll('x',*a))
        sy=ttk.Scrollbar(right,orient='vertical',command=lambda *a:self.scroll('y',*a))
        sx.grid(row=1,column=0,sticky='ew');sy.grid(row=0,column=1,sticky='ns')
        right.rowconfigure(0,weight=1);right.columnconfigure(0,weight=1)
        self.canvas.configure(xscrollcommand=sx.set,yscrollcommand=sy.set)
        self.canvas.bind('<Button-1>',self.stroke_start)
        self.canvas.bind('<B1-Motion>',self.stroke_move)
        self.canvas.bind('<ButtonRelease-1>',self.stroke_end)
        self.canvas.bind('<Button-3>',self.erase)
        self.canvas.bind('<Control-Button-3>',self.pick)
        self.canvas.bind('<Shift-Button-3>',self.copy_end)
        self.canvas.bind('<MouseWheel>',self.wheel_zoom)
        self.canvas.bind('<Configure>',lambda e:self.redraw())

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
            self.tile=0;self.sort_tiles();self.select_tile();self.redraw()
            self.page=0;self.show_gallery()
        except Exception as exc:messagebox.showerror('맵 열기 실패',str(exc))

    def select_tile(self,event=None):
        selected=self.tiles.curselection()
        if self.doc is None or not selected:return
        self.tile=self.tile_order[selected[0]]
        self.tile_photo=tk.PhotoImage(data=self.doc.tile_ppm(self.tile,self.palette),format='PPM').zoom(2)
        self.swatch.configure(image=self.tile_photo)
        self.tile_info.set(self.tile_label(self.tile))
        self.mode.set('타일 브러시')

    def tile_key(self,tile):
        return hashlib.sha256(bytes([self.doc.theme])+self.doc.tiles[tile]).hexdigest()

    def tile_label(self,tile):
        category=self.launcher.cfg.get('tile_categories',{}).get(self.tile_key(tile),'미분류')
        ground='지상형' if self.doc.is_ground(tile) else '장애물형'
        return f'{category} · {ground} #{tile:04d}'

    def sort_tiles(self,event=None):
        if self.doc is None:return
        order=self.sort_order.get()
        def key(tile):
            category=self.launcher.cfg.get('tile_categories',{}).get(self.tile_key(tile),'미분류')
            if order=='번호순':return (0,tile)
            if order=='지상형 먼저':return (not self.doc.is_ground(tile),tile)
            if order=='장애물형 먼저':return (self.doc.is_ground(tile),tile)
            return (category!=order.split()[0],tile)
        self.tile_order=sorted(range(len(self.doc.tiles)),key=key)
        self.tiles.delete(0,'end')
        for tile in self.tile_order:self.tiles.insert('end',self.tile_label(tile))
        index=self.tile_order.index(self.tile)
        self.tiles.selection_set(index);self.tiles.see(index)
        self.page=0;self.show_gallery()

    def choose_tool(self, mode):
        self.mode.set(mode)
        self.status.set('◆ 자원 선택됨 · 지도에 왼쪽 클릭하면 배치됩니다.' if mode=='자원 추가' else '지울 자원을 클릭하세요.')

    def tile_page(self,delta):
        if self.doc is None:return
        self.page=max(0,min((len(self.doc.tiles)-1)//8,self.page+delta));self.show_gallery()

    def gallery_pick(self,tile):
        index=self.tile_order.index(tile)
        self.tiles.selection_clear(0,'end');self.tiles.selection_set(index);self.tiles.see(index);self.select_tile()

    def show_gallery(self):
        for child in self.gallery.winfo_children():child.destroy()
        self.gallery_images=[]
        for j,tile in enumerate(self.tile_order[self.page*8:self.page*8+8]):
            photo=tk.PhotoImage(data=self.doc.tile_ppm(tile,self.palette),format='PPM')
            self.gallery_images.append(photo)
            category=self.launcher.cfg.get('tile_categories',{}).get(self.tile_key(tile),'미분류')
            ttk.Button(self.gallery,image=photo,text=f'{category}\n{tile}',compound='top',command=lambda t=tile:self.gallery_pick(t)).grid(row=j//4,column=j%4)

    def stroke_start(self,event):
        self._stroke=False; self._last_cell=None
        self.place(event)
        self._stroke=True; self._last_cell=self.coords(event)

    def stroke_move(self,event):
        if not self._stroke or self.mode.get()!='타일 브러시' or self.doc is None:return
        end=self.coords(event);start=self._last_cell
        if start is None or end==start:return
        count=max(abs(end[0]-start[0]),abs(end[1]-start[1]))
        for step in range(1,count+1):
            x=round(start[0]+(end[0]-start[0])*step/count)
            y=round(start[1]+(end[1]-start[1])*step/count)
            if 0<=x<self.doc.width and 0<=y<self.doc.height:self.doc.paint(x,y,self.tile)
        self._last_cell=end; self.dirty=True;self.redraw()

    def stroke_end(self,event=None):
        self._stroke=False;self._last_cell=None

    def clear_spawns(self):
        if self.doc is None:return
        try:
            self.checkpoint();self.doc.clear_spawns();self.dirty=True;self.redraw()
        except Exception as exc:messagebox.showerror('시작 위치 확인',str(exc))

    def coords(self,event):
        scale=self.zoom.get()
        return int(self.canvas.canvasx(event.x)//scale),int(self.canvas.canvasy(event.y)//scale)

    def pick(self,event):
        if self.doc is None:return
        x,y=self.coords(event)
        if not (0<=x<self.doc.width and 0<=y<self.doc.height):return
        if self.mode.get()=='언덕/영역 복사':
            self.anchor=(x,y);self.status.set(f'복사 시작 ({x},{y}) · 반대 모서리에 Shift+우클릭');return
        self.gallery_pick(self.doc.tile_id(x,y))

    def erase(self,event):
        if self.doc is None:return 'break'
        x,y=self.coords(event)
        if 0<=x<self.doc.width and 0<=y<self.doc.height:
            tile=self.doc.ground_tile()
            if self.doc.tile_id(x,y)!=tile:
                self.checkpoint();self.doc.paint(x,y,tile);self.dirty=True;self.redraw()
        return 'break'

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
            self.status.set('Ctrl+우클릭과 Shift+우클릭으로 복사 영역을 먼저 지정하세요.');return
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
        if not messagebox.askyesno('빈 맵 만들기','지상용 평지로 전체 지형을 채웁니다. 자원·장식은 제거되며 시작 위치는 유지됩니다. 진행할까요?'):return
        self.checkpoint();self.doc.blank(self.doc.ground_tile());self.dirty=True;self.redraw()

    def undo(self):
        if not self.history:return
        self.doc.restore(self.history.pop());self.dirty=True;self.redraw()

    def scroll(self,axis,*args):
        getattr(self.canvas,axis+'view')(*args);self.redraw()

    def wheel_zoom(self,event):
        if self.doc is None or not event.delta:return 'break'
        old=self.zoom.get()
        levels=(2,4,8,16,24,32,48,64)
        index=levels.index(old)
        new=levels[max(0,min(len(levels)-1,index+(1 if event.delta>0 else -1)))]
        if new==old:return 'break'
        x=self.canvas.canvasx(event.x)/old;y=self.canvas.canvasy(event.y)/old
        self.stroke_end();self.zoom.set(new)
        self.canvas.configure(scrollregion=(0,0,self.doc.width*new,self.doc.height*new))
        self.canvas.xview_moveto(max(0,x*new-event.x)/(self.doc.width*new))
        self.canvas.yview_moveto(max(0,y*new-event.y)/(self.doc.height*new))
        self.redraw()
        return 'break'

    def redraw(self):
        if self.doc is None:return
        scale=self.zoom.get()
        self.canvas.configure(scrollregion=(0,0,self.doc.width*scale,self.doc.height*scale))
        x0=max(0,min(self.doc.width-1,int(self.canvas.canvasx(0)//scale)))
        y0=max(0,min(self.doc.height-1,int(self.canvas.canvasy(0)//scale)))
        x1=min(self.doc.width,x0+max(1,self.canvas.winfo_width())//scale+2)
        y1=min(self.doc.height,y0+max(1,self.canvas.winfo_height())//scale+2)
        self.photo=tk.PhotoImage(data=self.doc.preview(self.palette,scale,(x0,y0,x1,y1)),format='PPM')
        self.canvas.delete('all');self.canvas.create_image(x0*scale,y0*scale,image=self.photo,anchor='nw')
        draw_resources(self.canvas,self.doc.resources,scale);draw_starts(self.canvas,self.doc.starts,scale)
        self.status.set(f'{self.doc.path.name} | {self.doc.width}×{self.doc.height} | 자원 {len(self.doc.resources)}개'+(' · 저장하지 않은 변경' if self.dirty else ''))

    def save(self, save_as=False):
        if self.doc is None:return
        issues=self.doc.spawn_issues()
        if issues:
            messagebox.showerror('시작 위치 확인 필요','\n'.join(issues));return
        if self.launcher.server.alive or self.launcher._launch_pending or (self.launcher._game_process and self.launcher._game_process.poll() is None):
            messagebox.showinfo('저장 대기','게임과 서버를 종료한 뒤 맵을 저장하세요.');return
        path=self.doc.path
        if save_as:
            path=filedialog.asksaveasfilename(initialdir=self.launcher.gp('map','multi'),initialfile=self.doc.path.stem+'_custom.map',defaultextension='.map',filetypes=[('IOP 맵','*.map')])
        if not path:return
        try:
            self.doc.save(path,overwrite=not save_as);self.dirty=False;self.redraw();self.launcher._refresh_maps()
            self.status.set(f'저장 완료: {self.doc.path.name} · A·B PC에 같은 맵 파일을 배치하세요.')
        except Exception as exc:messagebox.showerror('저장 실패',str(exc))
