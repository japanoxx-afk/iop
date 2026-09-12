"""Explicit administrator actions invoked by launcher buttons only."""
import ctypes
import os
from pathlib import Path
import subprocess
import sys
import tkinter as tk
from tkinter import messagebox
from iop_network import update_hosts,SERVER_NAME

def run_admin_action(args):
    root=tk.Tk(); root.withdraw()
    try:
        if not ctypes.windll.shell32.IsUserAnAdmin(): raise PermissionError('관리자 권한이 필요합니다.')
        if len(args)!=2: raise ValueError('잘못된 관리자 작업 인수')
        flag,value=args
        if flag in ('--set-hosts','--set-hosts-auto'):
            backup=update_hosts(value)
            subprocess.run(['ipconfig','/flushdns'],capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW,timeout=10)
            if flag!='--set-hosts-auto': messagebox.showinfo('hosts 등록 완료',f'{value}  {SERVER_NAME}\n\n'+(f'기존 파일 백업: {backup}' if backup else '이미 같은 설정입니다.'))
        elif flag=='--radmin-firewall':
            game=Path(value).resolve()/'iop.exe'
            if not (game.parent/'iop.exe').is_file(): raise ValueError('게임 폴더를 확인하세요.')
            program=Path(sys.executable).resolve()
            def quote(s): return "'"+str(s).replace("'","''")+"'"
            script="""$ErrorActionPreference='Stop'
$adapters=@(Get-NetAdapter | Where-Object { $_.InterfaceDescription -match 'Radmin' } | Select-Object -ExpandProperty Name)
if (-not $adapters.Count) { throw 'Radmin adapter not found' }
"""+f"$launcher={quote(program)}\n$game={quote(game)}\n"+"""
$rules=@(
 @{Name='IOP-Private-Lobby'; Program=$launcher; Protocol='TCP'; LocalPort=@('6300','6302','6305','6306')},
 @{Name='IOP-Private-Game-TCP'; Program=$game; Protocol='TCP'; LocalPort='Any'},
 @{Name='IOP-Private-Game-UDP'; Program=$game; Protocol='UDP'; LocalPort='Any'}
)
foreach($rule in $rules) {
 Get-NetFirewallRule -Name $rule.Name -ErrorAction SilentlyContinue | Remove-NetFirewallRule
 New-NetFirewallRule @rule -DisplayName $rule.Name -Direction Inbound -InterfaceAlias $adapters -Action Allow -Profile Any | Out-Null
}
"""
            result=subprocess.run(['powershell.exe','-NoProfile','-Command',script],capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW,timeout=30)
            if result.returncode: raise RuntimeError(result.stderr.decode('utf-8',errors='replace'))
            messagebox.showinfo('방화벽 설정 완료','Radmin 어댑터에서만 런처 서버와 프리서버 게임의 수신을 허용했습니다.\n방화벽은 끄지 않았습니다.')
        else: raise ValueError('지원하지 않는 작업')
    except Exception as exc:
        if '--set-hosts-auto' not in args: messagebox.showerror('작업 실패',str(exc))
        return 1
    finally: root.destroy()

    return 0
