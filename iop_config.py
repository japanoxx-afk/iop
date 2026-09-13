"""User settings shared by every launcher version, with atomic saves."""
import json
import os
from pathlib import Path
import shutil
import tempfile

def config_path():
    return Path(os.environ.get('LOCALAPPDATA',str(Path.home())))/'IOPLauncher'/'config.json'

def read(path):
    data=json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if not isinstance(data,dict):raise ValueError('설정 파일 형식 오류')
    return data

def save(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='config-',suffix='.tmp',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            json.dump(data,stream,ensure_ascii=False,indent=2);stream.flush();os.fsync(stream.fileno())
        if path.exists():
            try:read(path)
            except (ValueError,OSError):pass
            else:shutil.copy2(path,path.with_suffix('.json.bak'))
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

def load(path,legacy_home):
    path=Path(path);home=Path(legacy_home)
    for candidate in (path,path.with_suffix('.json.bak')):
        if candidate.exists():
            try:return read(candidate)
            except (ValueError,OSError):pass
    candidates=[home/'iop_launcher_config.json',home.parent/'iop_launcher_config.json']
    candidates+=list(home.parent.glob('v*/iop_launcher_config.json'))
    candidates+=list((home/'release').glob('v*/iop_launcher_config.json'))
    profiles=[]
    for candidate in set(candidates):
        try:profiles.append((candidate.stat().st_mtime,read(candidate)))
        except (ValueError,OSError):pass
    profiles.sort(key=lambda p:p[0],reverse=True)
    data=profiles[0][1].copy() if profiles else {}
    # A new version folder may contain a fresh profile without saved hotkeys.
    for _,profile in profiles:
        if 'production_hotkeys' in profile:
            data['production_hotkeys']=profile['production_hotkeys'];break
    if data:save(path,data)
    return data
