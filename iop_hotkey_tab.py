from pathlib import Path
import tkinter as tk
from tkinter import ttk,messagebox
import iop_hotkeys as hotkeys

class HotkeyTab:
    def _build_hotkeys(self,parent):
        self._hotkey_profile=dict(self.cfg.get('production_hotkeys',{}));self._hotkeys_dirty=False
        self._hotkey_rows={}
        ttk.Label(parent,text='게임 시작 전에 유닛별 생산 키를 설정하세요. 설정 저장 후 게임 시작 시 적용됩니다.',padding=12).pack(anchor='w')
        actions=ttk.Frame(parent);actions.pack(fill='x',padx=12,pady=5)
        ttk.Button(actions,text='게임 폴더에서 목록 불러오기',command=self._load_hotkeys).pack(side='left')
        ttk.Button(actions,text='전체 기본값',command=self._reset_hotkeys).pack(side='left',padx=8)
        ttk.Button(actions,text='설정 저장',command=self._save_hotkeys).pack(side='left')
        self.hotkey_status=tk.StringVar(value='목록을 불러오세요.')
        ttk.Label(actions,textvariable=self.hotkey_status).pack(side='left',padx=12)
        frame=ttk.Frame(parent);frame.pack(fill='both',expand=True,padx=12,pady=8)
        self.hotkey_tree=ttk.Treeview(frame,columns=('race','building','unit','default','new'),show='headings',selectmode='browse')
        for key,title,width in [('race','종족',90),('building','생산 건물',190),('unit','유닛',160),('default','기본 키',70),('new','설정 키',70)]:
            self.hotkey_tree.heading(key,text=title);self.hotkey_tree.column(key,width=width,anchor='w')
        self.hotkey_tree.pack(side='left',fill='both',expand=True)
        scroll=ttk.Scrollbar(frame,orient='vertical',command=self.hotkey_tree.yview);scroll.pack(side='right',fill='y');self.hotkey_tree.configure(yscrollcommand=scroll.set)
        edit=ttk.Frame(parent);edit.pack(fill='x',padx=12,pady=8)
        self.hotkey_selected=tk.StringVar(value='목록에서 유닛을 선택하세요.')
        ttk.Label(edit,textvariable=self.hotkey_selected,width=40).pack(side='left')
        self.hotkey_value=tk.StringVar()
        combo=ttk.Combobox(edit,textvariable=self.hotkey_value,values=hotkeys.KEYS,width=5,state='readonly');combo.pack(side='left',padx=10)
        combo.bind('<<ComboboxSelected>>',self._edit_hotkey)
        self.hotkey_tree.bind('<<TreeviewSelect>>',self._select_hotkey)
        ttk.Label(parent,text='영문 단일 키 지원 (J/Q 제외). 같은 건물의 중복 키 및 기존 명령과 충돌하면 저장할 수 없습니다.\nA·B는 각자 다른 키를 사용할 수 있습니다. 게임을 종료한 뒤 설정을 변경하세요.',padding=12).pack(anchor='w')
        if self.game_dir:self._load_hotkeys()

    def _load_hotkeys(self):
        if self._need_game_dir():return
        try: rows=hotkeys.catalog(self.game_dir)
        except Exception as exc:
            self.hotkey_status.set('목록 오류: '+str(exc));return
        for item in self.hotkey_tree.get_children():self.hotkey_tree.delete(item)
        self._hotkey_rows={r['id']:r for r in rows}
        for r in sorted(rows,key=lambda r:(r['race'],r['building'],r['unit'])):
            self.hotkey_tree.insert('', 'end',iid=r['id'],values=(r['race'],r['building'],r['unit'],r['default'],self._hotkey_profile.get(r['id'],r['default'])))
        self.hotkey_status.set(f'{len(rows)}개 생산 명령 · '+('저장하지 않은 변경 있음' if self._hotkeys_dirty else '저장된 설정'))

    def _select_hotkey(self,event=None):
        selected=self.hotkey_tree.selection()
        if not selected:return
        r=self._hotkey_rows[selected[0]]
        self.hotkey_selected.set(r['building']+' / '+r['unit'])
        self.hotkey_value.set(self._hotkey_profile.get(r['id'],r['default']))

    def _edit_hotkey(self,event=None):
        selected=self.hotkey_tree.selection()
        if not selected:return
        ident=selected[0];value=self.hotkey_value.get()
        self._hotkey_profile[ident]=value
        self.hotkey_tree.set(ident,'new',value)
        self._hotkeys_dirty=True;self.hotkey_status.set('변경됨 · 설정 저장을 눌러주세요.')

    def _reset_hotkeys(self):
        self._hotkey_profile={};self._hotkeys_dirty=True
        for ident,r in self._hotkey_rows.items():self.hotkey_tree.set(ident,'new',r['default'])
        self._select_hotkey();self.hotkey_status.set('기본값 선택됨 · 저장 후 다음 게임 시작에 적용')

    def _save_hotkeys(self):
        if self._launch_pending or (self._game_process and self._game_process.poll() is None):
            messagebox.showinfo('게임 실행 중','게임 준비/실행을 마친 뒤 설정을 저장하세요.');return
        try:
            hotkeys.validate(self._hotkey_profile)
            profile={k:v for k,v in self._hotkey_profile.items() if v!=hotkeys.DEFAULTS[k]}
            self.cfg['production_hotkeys']=profile;self.save_network_config()
            self._hotkey_profile=profile;self._hotkeys_dirty=False
            self.hotkey_status.set('저장 완료 · 다음 게임 시작 시 적용')
            self._log_server(f'생산 단축키 저장: {len(profile)}개 변경 · 다음 게임 시작 시 적용')
        except Exception as exc:messagebox.showerror('단축키 저장 실패',str(exc))
