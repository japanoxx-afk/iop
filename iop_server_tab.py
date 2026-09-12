"""Private server controls mixed into the existing Tk launcher."""
import ctypes
import ipaddress
import json
import os
from pathlib import Path
import queue
import socket
import subprocess
import sys
import threading
import traceback
import iop_diagnostics as diagnostics
import tkinter as tk
from tkinter import ttk,messagebox

from iop_network import SERVER_NAME,PORTS,ServerController,prepare_private_exe,resolve_server,hosts_mapping

class ServerTab:
    def _build_server(self, p):
        self.server=ServerController()
        self.diagnostic_dir=diagnostics.configure(self._launcher_home())
        self.report_callback_exception=lambda typ,value,tb: diagnostics.event("tk_error",traceback="".join(traceback.format_exception(typ,value,tb)))
        self._closing=False
        self._launch_pending=False
        self._game_process=None
        self.host_ip=tk.StringVar(value=self.cfg.get('host_ip','127.0.0.1'))
        self.connect_to=tk.StringVar(value=self.cfg.get('connect_to',SERVER_NAME))
        self.mapping_ip=tk.StringVar(value=self.cfg.get('mapping_ip',''))
        self.server_state=tk.StringVar(value='OFF')
        box=ttk.LabelFrame(p,text='A PC · 이 PC에서 서버 운영',padding=12)
        box.pack(fill='x',padx=18,pady=(14,8))
        ttk.Label(box,text='내 Radmin / LAN IP').grid(row=0,column=0,sticky='w')
        self.host_combo=ttk.Combobox(box,textvariable=self.host_ip,width=20,state='readonly',values=['127.0.0.1'])
        self.host_combo.grid(row=0,column=1,padx=8)
        ttk.Button(box,text='IP 새로 찾기',command=self._find_server_ips).grid(row=0,column=2,padx=4)
        self.on_button=tk.Button(box,text='서버 ON',bg='#238451',fg='white',width=12,command=self._server_on)
        self.on_button.grid(row=0,column=3,padx=(24,4))
        self.off_button=tk.Button(box,text='서버 OFF',width=12,state='disabled',command=self._server_off)
        self.off_button.grid(row=0,column=4,padx=4)
        ttk.Label(box,textvariable=self.server_state,font=('맑은 고딕',11,'bold')).grid(row=0,column=5,padx=12)
        ttk.Label(box,text='ON: 런처를 열어 두세요. OFF 또는 런처 종료 시 접속이 끊기고 서버도 종료됩니다.').grid(row=1,column=0,columnspan=6,sticky='w',pady=(10,0))

        join=ttk.LabelFrame(p,text='A·B PC · 프리서버 접속',padding=12)
        join.pack(fill='x',padx=18,pady=6)
        ttk.Label(join,text='접속 주소').grid(row=0,column=0,sticky='w')
        ttk.Entry(join,textvariable=self.connect_to,width=25).grid(row=0,column=1,padx=8,sticky='w')
        ttk.Button(join,text='접속 검사',command=self._check_server).grid(row=0,column=2,padx=4)
        tk.Button(join,text='게임 시작',bg='#3b6ea5',fg='white',command=self.launch_game).grid(row=0,column=3,padx=8,ipadx=12)
        ttk.Label(join,text='A PC의 Radmin IP').grid(row=1,column=0,sticky='w',pady=(12,0))
        ttk.Entry(join,textvariable=self.mapping_ip,width=25).grid(row=1,column=1,padx=8,pady=(12,0))
        ttk.Button(join,text='hosts 등록 (관리자)',command=self._register_hosts).grid(row=1,column=2,padx=4,pady=(12,0))
        ttk.Button(join,text='접속 안내 복사',command=self._copy_connection).grid(row=1,column=3,padx=8,pady=(12,0))
        ttk.Label(join,text='B PC: 같은 Radmin 네트워크 참가 → A PC의 Radmin IP 입력 → 게임 시작.\nhosts 자동 등록(필요 시 관리자 확인)과 A·B 게임 파일 비교 후 실행됩니다.').grid(row=2,column=0,columnspan=4,sticky='w',pady=(10,0))

        row=ttk.Frame(p); row.pack(fill='x',padx=18,pady=5)
        ttk.Button(row,text='Radmin 방화벽 허용 (관리자)',command=self._server_firewall).pack(side='left')
        ttk.Button(row,text='서버 로그 폴더',command=lambda:self.open_folder(str(self._server_data()))).pack(side='left',padx=8)
        ttk.Label(row,text='로비 동작 검증 완료 · 두 PC 대전 완주 미검증',foreground='#666').pack(side='right')
        diag=ttk.Frame(p); diag.pack(fill='x',padx=18,pady=4)
        ttk.Button(diag,text='검은 화면 / 오류 진단 ZIP 저장',command=self._diagnostic_report).pack(side='left')
        ttk.Button(diag,text='진단 로그 폴더',command=lambda:self.open_folder(str(self.diagnostic_dir.parent))).pack(side='left',padx=8)
        self.server_log=tk.Text(p,height=9,wrap='word',state='disabled',font=('맑은 고딕',9))
        self.server_log.pack(fill='both',expand=True,padx=18,pady=(5,12))
        self._log_server('서버 OFF. A PC는 IP를 선택해 ON, B PC는 A의 Radmin IP로 접속하세요.')
        self.protocol('WM_DELETE_WINDOW',self._close_launcher)
        self.after(200,self._poll_server)
        self.after(500,self._find_server_ips)

    def _server_data(self):
        return Path(self._launcher_home())/'server_data'

    @staticmethod
    def _launcher_home():
        return Path(sys.executable).parent if getattr(sys,'frozen',False) else Path(__file__).parent

    def _save_network(self):
        self.cfg.update(host_ip=self.host_ip.get(),connect_to=self.connect_to.get(),mapping_ip=self.mapping_ip.get())
        self.save_network_config()

    def _log_server(self,text):
        diagnostics.event('launcher_message',message=text)
        self.server_log.config(state='normal')
        self.server_log.insert('end',text+'\n')
        if int(self.server_log.index('end-1c').split('.')[0])>350:
            self.server_log.delete('1.0','100.0')
        self.server_log.see('end'); self.server_log.config(state='disabled')

    def _job(self, fn, done):
        def work():
            try: result,error=fn(),None
            except Exception as exc:
                diagnostics.event("worker_error",traceback=traceback.format_exc())
                result,error=None,str(exc)
            self.server.events.put(('result',(done,result,error)))
        threading.Thread(target=work,daemon=True).start()

    def _poll_server(self):
        try:
            for _ in range(100):
                kind,value=self.server.events.get_nowait()
                if kind=='result':
                    done,result,error=value
                    if not self._closing: done(result,error)
                elif kind=='error': self._log_server('서버 시작/실행 실패: '+value)
                elif kind=='log': self._log_server(value)
        except queue.Empty: pass
        self.server_state.set(self.server.state)
        active=self.server.alive
        self.on_button.config(state='disabled' if active or self._closing else 'normal')
        self.off_button.config(state='normal' if active and not self._closing else 'disabled')
        if self._closing and not active:
            self.destroy(); return
        self.after(200,self._poll_server)

    def _find_server_ips(self):
        def read():
            script="[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; Get-NetIPAddress -AddressFamily IPv4 | Select-Object InterfaceAlias,IPAddress | ConvertTo-Json -Compress"
            result=subprocess.run(['powershell.exe','-NoProfile','-Command',script],capture_output=True,timeout=15,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if result.returncode: raise ValueError('네트워크 어댑터 조회 실패')
            data=json.loads(result.stdout.decode('utf-8-sig'))
            return data if isinstance(data,list) else [data]
        def done(data,error):
            if error: self._log_server(error); return
            data.sort(key=lambda x:('radmin' not in x['InterfaceAlias'].lower(),x['IPAddress']=='127.0.0.1'))
            ips=[x['IPAddress'] for x in data if not x['IPAddress'].startswith('169.254.')]
            self.host_combo['values']=ips
            if not self.server.alive and (self.host_ip.get() not in ips or not self.cfg.get('host_ip')):
                self.host_ip.set(ips[0] if ips else '127.0.0.1')
            for item in data:
                self._log_server(f"{item['InterfaceAlias']}: {item['IPAddress']}")
        self._job(read,done)

    def _server_on(self):
        try:
            ip=resolve_server(self.host_ip.get())
            if self._need_game_dir(): return
            from iop_sync import fix_flame, manifest
            fix_flame(self.game_dir)
            manifest(self.game_dir)
            self.server.start(ip,self._server_data(),game_dir=self.game_dir)
            self.connect_to.set(ip) # local host also connects over VPN, never advertises loopback to B
            self.mapping_ip.set(ip)
            self._save_network()
        except Exception as exc: messagebox.showerror('서버 시작 실패',str(exc))

    def _server_off(self):
        self.server.stop()
        self._log_server('서버 연결을 정리하고 있습니다…')

    def _check_server(self):
        address=self.connect_to.get(); self._save_network()
        def check():
            ip=resolve_server(address)
            results=[]
            for port in (*PORTS,6306):
                try:
                    with socket.create_connection((ip,port),timeout=2): pass
                    results.append(f'TCP {port}: 연결 성공')
                except OSError as exc: results.append(f'TCP {port}: 실패 ({exc})')
            return f'{address} → {ip}\n'+'\n'.join(results)
        self._job(check,lambda result,error:self._log_server(error or result))

    def _check_local_sync(self):
        from iop_sync import fix_flame,manifest,fetch_manifest,compare
        game_dir=self.game_dir
        address=self.mapping_ip.get().strip()
        def check():
            fixed=fix_flame(game_dir)
            local=manifest(game_dir)
            status=('화염병 수정 적용 완료' if fixed else '화염병 수정 확인 완료')
            if address:
                ip=resolve_server(address)
                try: remote=fetch_manifest(ip)
                except OSError: return status+' · A 서버 연결 대기. 게임 시작 때 다시 비교합니다.'
                count=compare(local,remote)
                status+=f' · A·B {count}개 파일 일치'
            return status
        self._job(check,lambda result,error:self._log_server(error or result))

    def launch_private_game(self):
        if self._need_game_dir() or self._launch_pending: return
        if self._game_process and self._game_process.poll() is None:
            messagebox.showinfo('게임 실행 중','실행 중인 게임을 종료한 뒤 다시 시작하세요.'); return
        game_dir=self.game_dir; mode=self.mode_var.get()
        address=self.mapping_ip.get().strip() or self.connect_to.get().strip()
        network=bool(self.mapping_ip.get().strip() or address not in ('',SERVER_NAME) or hosts_mapping())
        self.cfg['mode']=mode
        self._save_network()
        hotkeys=dict(self.cfg.get("production_hotkeys",{}))
        if getattr(self,"_hotkeys_dirty",False):
            messagebox.showinfo("단축키 저장 필요","생산 단축키 탭에서 설정 저장을 누른 뒤 게임을 시작하세요."); return
        diagnostics.event("launch_requested",game_dir=game_dir,mode=mode,server=address,network=network)
        self._launch_pending=True
        self._log_server('게임 준비: 화염병 수정 / hosts 자동 등록 / A·B 파일 비교…')
        def prepare():
            from iop_sync import fix_flame,manifest,fetch_manifest,compare
            from iop_hotkeys import apply,is_applied
            changed=0
            if not is_applied(game_dir,hotkeys):
                changed=apply(game_dir,hotkeys)
            diagnostics.event("production_hotkeys_checked",profile=hotkeys,restored=bool(changed),changed_files=changed)
            fix_flame(game_dir)
            if not network: return str(Path(game_dir)/'iop.exe'),None,0
            ip=resolve_server(address)
            if hosts_mapping()!=ip:
                self._admin_action('--set-hosts-auto',ip,wait=True)
            if hosts_mapping()!=ip or resolve_server(SERVER_NAME)!=ip:
                raise ValueError('hosts 등록 결과를 확인하지 못했습니다. 게임을 실행하지 않았습니다.')
            with socket.create_connection((ip,6305),timeout=3): pass
            count=compare(manifest(game_dir),fetch_manifest(ip))
            return str(prepare_private_exe(game_dir,ip)),ip,count
        def done(result,error):
            self._launch_pending=False
            if error:
                messagebox.showerror('게임 시작 실패',error+'\n\nA 서버 ON, 양쪽 새 런처, Radmin 연결 및 방화벽(TCP 6306 포함)을 확인하세요.'); return
            exe,ip,count=result
            if self.game_dir!=game_dir:
                self._log_server('게임 폴더가 변경되어 실행을 취소했습니다. 다시 실행하세요.'); return
            try:
                self.set_display_mode(mode)
                diagnostics.event("launch_ready",mode=mode,server=ip,matched_files=count)
                self._game_process=diagnostics.launch(exe,game_dir)
                if ip:
                    self.mapping_ip.set(ip); self.connect_to.set(SERVER_NAME); self._save_network()
                self._log_server(f'게임 시작: iop.exe · {mode} · '+(f'A·B {count}개 파일 일치 / {ip}' if ip else '로컬 실행'))
            except Exception as exc:
                diagnostics.event('launch_error',traceback=traceback.format_exc())
                messagebox.showerror('실행 실패',str(exc)+'\n진단 로그 폴더에서 로그를 확인하세요.')
        self._job(prepare,done)

    def _diagnostic_report(self):
        if self._need_game_dir(): return
        process=self._game_process
        pid=process.pid if process and process.poll() is None else None
        game_dir=self.game_dir
        self._log_server('진단 ZIP 생성 중… 게임이 검은 화면이면 그 상태로 잠시 기다리세요.')
        def done(path,error):
            if error: messagebox.showerror('진단 저장 실패',error); return
            self._log_server('진단 ZIP 저장: '+str(path))
            messagebox.showinfo('진단 저장 완료',str(path)+'\n\n이 ZIP을 전달해 주세요. PC/게임 경로, 서버 IP, 그래픽 정보와 오류 로그가 포함됩니다.')
            self.open_folder(str(path.parent))
        self._job(lambda:diagnostics.report(game_dir,pid),done)

    def _admin_action(self, flag, value, wait=False):
        if getattr(sys,'frozen',False):
            executable=sys.executable; args=[flag,value]
        else:
            executable=sys.executable; args=[str(Path(__file__).parent/'iop_launcher.py'),flag,value]
        if wait:
            from iop_elevation import elevated_wait
            return elevated_wait(executable,args,self._launcher_home())
        result=ctypes.windll.shell32.ShellExecuteW(None,'runas',executable,subprocess.list2cmdline(args),str(self._launcher_home()),0)
        if result<=32: raise OSError('관리자 작업을 시작하지 못했거나 취소했습니다.')

    def _register_hosts(self):
        try:
            ip=str(ipaddress.IPv4Address(self.mapping_ip.get().strip()))
            self._admin_action('--set-hosts',ip)
            self.connect_to.set(SERVER_NAME); self._save_network()
            self._log_server(f'hosts 등록 요청: {ip} {SERVER_NAME}. 관리자 창에서 완료 후 접속 검사를 누르세요.')
        except Exception as exc: messagebox.showerror('hosts 등록 실패',str(exc))

    def _copy_connection(self):
        ip=self.mapping_ip.get() or self.host_ip.get()
        try: ipaddress.IPv4Address(ip)
        except ValueError:
            messagebox.showerror('IP 필요','A PC의 Radmin IP를 입력하세요.'); return
        text=f'같은 Radmin VPN 네트워크에 참가하세요.\nA 서버 IP: {ip}\nB 런처의 A PC Radmin IP에 {ip} 입력 후 게임 시작.\nhosts는 자동 등록되며 관리자 확인이 필요할 수 있습니다.\n양쪽 v0.003 이상 런처와 동일한 게임 데이터를 사용하세요.\nA PC는 서버 ON 상태로 런처를 열어 두세요.'
        self.clipboard_clear(); self.clipboard_append(text)
        self._log_server('접속 안내를 클립보드에 복사했습니다.')

    def _server_firewall(self):
        if self._need_game_dir(): return
        try: self._admin_action('--radmin-firewall',self.game_dir)
        except Exception as exc: messagebox.showerror('방화벽 설정 실패',str(exc))

    def _close_launcher(self):
        if self._launch_pending:
            messagebox.showinfo('게임 준비 중','관리자 확인 또는 게임 준비 작업이 끝난 뒤 런처를 닫으세요.')
            return
        self._save_network()
        self._closing=True
        self.server.stop()
