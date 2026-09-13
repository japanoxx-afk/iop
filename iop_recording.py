"""Game-window video recording. No desktop or microphone capture."""
import ctypes
from ctypes import wintypes
import datetime
import hashlib
import os
from pathlib import Path
import queue
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.request
import uuid
import zipfile

URL='https://files.pythonhosted.org/packages/2c/c6/fa760e12a2483469e2bf5058c5faff664acf66cadb4df2ad6205b016a73d/imageio_ffmpeg-0.6.0-py3-none-win_amd64.whl'
SHA256='02fa47c83703c37df6bfe4896aab339013f62bf02c5ebf2dce6da56af04ffc0a'

def storage():
    return Path(os.environ.get('LOCALAPPDATA',str(Path.home())))/'IOPLauncher'/'recording'

def ensure_encoder():
    folder=storage()/'tools';folder.mkdir(parents=True,exist_ok=True)
    target=folder/'ffmpeg.exe'
    if target.is_file():return target
    with tempfile.TemporaryDirectory(dir=folder) as tmp:
        wheel=Path(tmp)/'encoder.whl';digest=hashlib.sha256();size=0
        with urllib.request.urlopen(URL,timeout=30) as src,wheel.open('wb') as dst:
            while chunk:=src.read(1024*1024):
                size+=len(chunk)
                if size>40*1024*1024:raise ValueError('녹화 도구 다운로드 크기 오류')
                digest.update(chunk);dst.write(chunk)
        if digest.hexdigest()!=SHA256:raise ValueError('녹화 도구 다운로드 검증 실패')
        with zipfile.ZipFile(wheel) as archive:
            name=next(n for n in archive.namelist() if n.startswith('imageio_ffmpeg/binaries/') and n.endswith('.exe'))
            binary=Path(tmp)/'ffmpeg.exe';binary.write_bytes(archive.read(name))
            for n in archive.namelist():
                if 'license' in n.lower() or 'copying' in n.lower():
                    (folder/Path(n).name).write_bytes(archive.read(n))
            os.replace(binary,target)
    return target

def game_window(pid):
    user=ctypes.WinDLL('user32',use_last_error=True)
    callback=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
    user.IsWindowVisible.argtypes=[wintypes.HWND]
    user.GetWindowThreadProcessId.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]
    user.GetClientRect.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.RECT)]
    found=[]
    @callback
    def visit(hwnd,param):
        owner=wintypes.DWORD();user.GetWindowThreadProcessId(hwnd,ctypes.byref(owner))
        rect=wintypes.RECT()
        if owner.value==pid and user.IsWindowVisible(hwnd) and user.GetClientRect(hwnd,ctypes.byref(rect)):
            area=rect.right*rect.bottom
            if rect.right>=320 and rect.bottom>=200:found.append((area,int(hwnd)))
        return True
    user.EnumWindows.argtypes=[callback,wintypes.LPARAM]
    user.EnumWindows(visit,0)
    return max(found)[1] if found else None

def command(encoder,hwnd,target):
    return [str(encoder),'-hide_banner','-loglevel','warning','-f','gdigrab','-framerate','20',
            '-i',f'hwnd={hwnd}','-an','-vf','pad=ceil(iw/2)*2:ceil(ih/2)*2',
            '-c:v','libx264','-preset','ultrafast','-crf','25','-pix_fmt','yuv420p',
            '-g','40','-movflags','+frag_keyframe+empty_moov','-n',str(target)]

class Recorder:
    def __init__(self):
        self.events=queue.Queue();self.thread=None;self.stop_event=threading.Event()

    @property
    def active(self):return self.thread is not None and self.thread.is_alive()

    def start(self,game,encoder,folder):
        if self.active:raise RuntimeError('이전 녹화를 저장 중입니다.')
        self.stop_event.clear()
        self.thread=threading.Thread(target=self._run,args=(game,encoder,Path(folder)),daemon=True)
        self.thread.start()

    def stop(self):self.stop_event.set()

    def _run(self,game,encoder,folder):
        process=None;target=None
        try:
            folder.mkdir(parents=True,exist_ok=True)
            self.events.put('게임 창 대기 중…')
            deadline=time.monotonic()+90;hwnd=None
            while game.poll() is None and not self.stop_event.wait(.25):
                hwnd=game_window(game.pid)
                if hwnd:break
                if time.monotonic()>deadline:raise RuntimeError('게임 창을 찾지 못해 녹화를 시작하지 못했습니다.')
            if not hwnd:return
            name=datetime.datetime.now().strftime('IOP-%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6]
            target=folder/(name+'.mp4')
            with (folder/(name+'.log')).open('wb') as log:
                process=subprocess.Popen(command(encoder,hwnd,target),stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                self.events.put('녹화 중 · 게임 종료 시 자동 저장 (무음)')
                while process.poll() is None and game.poll() is None and not self.stop_event.wait(.25):
                    if shutil.disk_usage(folder).free<256*1024*1024:
                        self.events.put('저장 공간 부족: 녹화를 마무리합니다.');break
                if process.poll() is None:
                    try:process.stdin.write(b'q\n');process.stdin.flush()
                    except (BrokenPipeError,OSError):pass
                    try:process.wait(timeout=15)
                    except subprocess.TimeoutExpired:process.kill();process.wait()
            valid=False
            if target.exists() and target.stat().st_size>1024:
                check=subprocess.run([str(encoder),'-v','error','-i',str(target),'-frames:v','1','-f','null','-'],capture_output=True,timeout=15,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                valid=check.returncode==0
            if valid:self.events.put('영상 저장 완료: '+str(target))
            else:raise RuntimeError('녹화 영상을 확인하지 못했습니다. 녹화 폴더의 .log 파일을 확인하세요.')
        except Exception as exc:self.events.put('녹화 오류: '+str(exc))
        finally:
            if process:
                if process.poll() is None:process.kill();process.wait()
                if process.stdin:process.stdin.close()
