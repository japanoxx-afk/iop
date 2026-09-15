"""Bounded per-session diagnostics, without passwords or account databases."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import threading
import time
import traceback
import zipfile
import shutil

_directory=None
_lock=threading.Lock()

def configure(home):
    global _directory
    name=datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+str(os.getpid())
    base=Path(home)/'diagnostics'
    try: (base/name).mkdir(parents=True,exist_ok=True)
    except OSError:
        base=Path(os.environ.get('LOCALAPPDATA',str(Path.home())))/'IOPLauncher/diagnostics'
        (base/name).mkdir(parents=True,exist_ok=True)
    _directory=base/name
    event('launcher_start',version='0.023',windows=platform.platform(),python=platform.python_version(),architecture=platform.machine())
    return _directory

def event(kind,**fields):
    if _directory is None: return
    record={'time':datetime.datetime.now().astimezone().isoformat(),'event':kind,**fields}
    try:
        with _lock:
            with (_directory/'launcher.jsonl').open('a',encoding='utf-8') as out:
                out.write(json.dumps(record,ensure_ascii=False,default=str)+'\n')
    except OSError: pass

def powershell(script):
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',
        '[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; '+script],capture_output=True,timeout=20,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    return {'exit_code':result.returncode,'output':result.stdout.decode('utf-8-sig',errors='replace')[-100000:],
            'error':result.stderr.decode('utf-8-sig',errors='replace')[-8000:]}

def snapshot(game_dir,pid=None):
    root=Path(game_dir)
    files={}
    for name in ('iop.exe','ddraw.dll','d3d8.dll','d3d9.dll','dxgi.dll','winmm.dll','dinput.dll','dinput8.dll','data/Gweapon.res','data/Button.res','data/Kbutton.res'):
        p=root/name
        if p.is_file():
            files[name]={'size':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
    configs={}
    for name in ('ddraw.ini','dxwnd.ini'):
        p=root/name
        if p.is_file(): configs[name]=p.read_bytes()[:65536].decode('latin-1')
    event('game_environment',game_dir=str(root),pid=pid,files=files,configs=configs)
    maps={}
    for p in (root/'map'/'multi').glob('*.map'):
        if p.stat().st_size<=32*1024*1024:maps[p.name]=hashlib.sha256(p.read_bytes()).hexdigest()
    event('map_inventory',files=maps)
    if os.name!='nt': return
    scripts={
        'graphics': 'Get-CimInstance Win32_VideoController | Select-Object Name,DriverVersion,DriverDate,VideoModeDescription | ConvertTo-Json -Depth 3',
        'display_environment': "Get-CimInstance Win32_DesktopMonitor | Select-Object Name,ScreenWidth,ScreenHeight,PixelsPerXLogicalInch,PixelsPerYLogicalInch | ConvertTo-Json -Depth 3",
        'iop_process_candidates': "Get-CimInstance Win32_Process -Filter \"Name='iop.exe'\" | Select-Object ProcessId,ExecutablePath,CreationDate,CommandLine | ConvertTo-Json -Depth 3",
        'game_windows_events': "$since=(Get-Date).AddMinutes(-15); Get-WinEvent -FilterHashtable @{LogName='Application'; StartTime=$since; Id=1000,1001,1002} -MaxEvents 100 -ErrorAction SilentlyContinue | Where-Object { $_.Message -match '(?i)iop[.]exe|iop_private[.]exe|IOPLauncher_Server' } | Select-Object -First 10 TimeCreated,Id,ProviderName,Message | ConvertTo-Json -Depth 3"
    }
    if pid:
        scripts['game_process']=f"$p=Get-Process -Id {int(pid)} -ErrorAction Stop; $p | Select-Object Id,ProcessName,Responding,CPU,WorkingSet64,MainWindowHandle,MainWindowTitle,StartTime | ConvertTo-Json; $p.Modules | Select-Object ModuleName,FileName,FileVersionInfo | ConvertTo-Json -Depth 2"
    for name,script in scripts.items():
        try: event(name,**powershell(script))
        except Exception: event('diagnostic_error',stage=name,traceback=traceback.format_exc())
    # Copy only known graphics-wrapper logs, bounded to the latest 1 MB.
    for name in ('ddraw.log','cnc-ddraw.log','dxwnd.log'):
        p=root/name
        if p.is_file() and _directory:
            try:
                with p.open('rb') as stream:
                    stream.seek(max(0,p.stat().st_size-1024*1024))
                    (_directory/name).write_bytes(stream.read(1024*1024))
            except OSError: pass

def background_snapshot(game_dir,pid=None):
    def work():
        try: snapshot(game_dir,pid)
        except Exception: event('diagnostic_error',traceback=traceback.format_exc())
    threading.Thread(target=work,daemon=True).start()

def launch(exe,game_dir,on_report=None):
    output=(_directory/'game-output.log').open('ab') if _directory else subprocess.DEVNULL
    try:
        process=subprocess.Popen([str(exe)],cwd=game_dir,stdout=output,stderr=subprocess.STDOUT)
    finally:
        if hasattr(output,'close'): output.close()
    event('game_started',pid=process.pid,executable=str(exe),working_directory=game_dir)
    def monitor():
        started=time.monotonic(); next_check=5
        while process.poll() is None:
            elapsed=time.monotonic()-started
            if elapsed>=next_check:
                event('game_alive',pid=process.pid,elapsed_seconds=round(elapsed))
                if os.name=='nt':
                    try: event('game_health',**powershell(f'Get-Process -Id {process.pid} -ErrorAction Stop | Select-Object Id,Responding,CPU,WorkingSet64,MainWindowHandle,MainWindowTitle | ConvertTo-Json'))
                    except Exception: event('health_error',traceback=traceback.format_exc())
                next_check=elapsed+30
            time.sleep(1)
        code=process.returncode
        event('game_exited',pid=process.pid,exit_code=code,exit_hex=f'0x{code&0xffffffff:08X}',elapsed_seconds=round(time.monotonic()-started))
        try:
            if code!=0:
                time.sleep(3) # Allow Windows Error Reporting to finalize its report.
                snapshot(game_dir)
                target=archive_exit(game_dir,process.pid,code)
                event('automatic_crash_report',path=str(target))
                if on_report:on_report(target)
            else:snapshot(game_dir)
        except Exception:event('diagnostic_error',stage='exit_report',traceback=traceback.format_exc())
    threading.Thread(target=monitor,daemon=True).start()
    background_snapshot(game_dir,process.pid)
    return process

def archive_exit(game_dir,pid,code):
    if _directory is None:raise RuntimeError('진단 폴더가 없습니다.')
    dump=Path(os.environ.get('LOCALAPPDATA',str(Path.home())))/'CrashDumps'/f'iop.exe.{int(pid)}.dmp'
    if dump.is_file() and dump.stat().st_size<=128*1024*1024:
        shutil.copy2(dump,_directory/dump.name)
    (_directory/f'exit-{pid}.json').write_text(json.dumps({'pid':pid,'exit_code':code,'exit_hex':f'0x{code&0xffffffff:08X}','dump_found':(_directory/dump.name).exists(),'game_dir':str(game_dir)},indent=2),encoding='utf-8')
    target=_directory.parent/f'{_directory.name}-exit-{pid}.zip'
    with _lock,zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as archive:
        for p in _directory.iterdir():
            if p.is_file() and p.stat().st_size<=128*1024*1024:archive.write(p,p.name)
    return target

def report(game_dir,pid=None):
    if _directory is None: raise RuntimeError('진단 로그 폴더가 초기화되지 않았습니다.')
    event('user_report',reason='검은 화면 또는 오류 진단 요청',pid=pid)
    snapshot(game_dir,pid)
    target=_directory.parent/(_directory.name+'-report-'+datetime.datetime.now().strftime('%H%M%S')+'.zip')
    with _lock,zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as archive:
        for p in _directory.iterdir():
            if p.is_file(): archive.write(p,p.name)
    return target
