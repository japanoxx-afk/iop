import os
from pathlib import Path
import queue
import tkinter as tk
from tkinter import ttk,messagebox
from iop_recording import Recorder,storage

class RecordingTab(ttk.Frame):
    def __init__(self,parent,launcher):
        super().__init__(parent,padding=16);self.pack(fill='both',expand=True)
        self.launcher=launcher;self.recorder=Recorder();self.folder=storage()/'videos'
        self.enabled=tk.BooleanVar(value=launcher.cfg.get('auto_record_video',True))
        ttk.Checkbutton(self,text='게임 시작 시 자동 녹화 · 게임 프로그램 종료 시 자동 저장',variable=self.enabled,command=self.save_setting).pack(anchor='w')
        ttk.Label(self,text='영상 녹화입니다. 시점 변경 리플레이가 아니며 소리는 녹음하지 않습니다.\n창모드에서 사용하고 게임 창을 최소화하지 마세요. 첫 녹화 준비 시 도구 약 30 MB를 다운로드합니다.\n런처를 닫으면 녹화를 마무리합니다. 한 판씩이 아닌 게임 실행 1회당 영상 1개를 저장합니다.',justify='left').pack(anchor='w',pady=10)
        row=ttk.Frame(self);row.pack(fill='x')
        ttk.Button(row,text='선택 영상 재생',command=self.play).pack(side='left')
        ttk.Button(row,text='녹화 중지 및 저장',command=self.recorder.stop).pack(side='left',padx=8)
        ttk.Button(row,text='영상 폴더 열기',command=self.open_folder).pack(side='left')
        ttk.Button(row,text='새로고침',command=self.refresh).pack(side='left',padx=8)
        self.status=tk.StringVar(value='자동 녹화 준비됨' if self.enabled.get() else '자동 녹화 꺼짐')
        ttk.Label(self,textvariable=self.status,wraplength=950).pack(anchor='w',pady=10)
        self.list=ttk.Treeview(self,columns=('size',),show='tree headings');self.list.heading('#0',text='저장 영상');self.list.heading('size',text='크기 (MB)');self.list.column('size',width=100)
        self.list.pack(fill='both',expand=True);self.list.bind('<Double-1>',lambda e:self.play())
        self.refresh();self.after(500,self.poll)

    def save_setting(self):
        self.launcher.cfg['auto_record_video']=self.enabled.get();self.launcher.save_network_config()

    def refresh(self):
        self.list.delete(*self.list.get_children());self.paths={}
        if self.folder.exists():
            for path in sorted(self.folder.glob('*.mp4'),reverse=True):
                ident=self.list.insert('','end',text=path.name,values=(f'{path.stat().st_size/1048576:.1f}',));self.paths[ident]=path

    def play(self):
        selected=self.list.selection()
        if selected:
            try:os.startfile(str(self.paths[selected[0]]))
            except OSError as exc:messagebox.showerror('영상 재생 실패',str(exc))

    def open_folder(self):
        self.folder.mkdir(parents=True,exist_ok=True);os.startfile(str(self.folder))

    def poll(self):
        try:
            while True:
                text=self.recorder.events.get_nowait();self.status.set(text)
                self.launcher._log_server(text);self.refresh()
        except queue.Empty:pass
        self.after(500,self.poll)
