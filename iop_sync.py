"""Local flame repair and read-only peer content verification."""
import asyncio
import hashlib
import json
from pathlib import Path
import socket
import struct
import time

SYNC_PORT = 6306
LIMIT = 1024 * 1024

def is_multiplayer_map(name):
    return str(name).replace('\\','/').lower().startswith('map/multi/')

def fix_flame(game_dir):
    path=Path(game_dir)/'data/Gweapon.res'
    raw=bytearray(path.read_bytes())
    if len(raw)<4: raise ValueError('Gweapon.res 헤더 오류')
    count=struct.unpack_from('<I',raw)[0]
    if not 0<count<10000 or 4+count*8>len(raw): raise ValueError('Gweapon.res 인덱스 오류')
    entries=[struct.unpack_from('<HHI',raw,4+i*8) for i in range(count)]
    offsets=sorted(e[2] for e in entries)
    if len(set(offsets))!=count or offsets[0]<4+count*8 or offsets[-1]>=len(raw):
        raise ValueError('Gweapon.res 범위 오류')
    matches=[off for uid,typ,off in entries if uid==7 and (typ>>8)&255==7]
    if len(matches)!=1: raise ValueError('화염병 레코드를 확인할 수 없습니다.')
    off=matches[0]; end=(offsets+[len(raw)])[offsets.index(off)+1]
    if end-off<0x2c: raise ValueError('화염병 레코드 크기 오류')
    flags=struct.unpack_from('<I',raw,off+0x28)[0]
    if flags&0x20000: return False
    # Loaded iop.exe denies this writable open on Windows: never alter live game data.
    with (Path(game_dir)/'iop.exe').open('r+b'):
        backup=path.with_name('Gweapon.res.before-flame-'+str(time.time_ns())+'.bak')
        backup.write_bytes(raw)
        struct.pack_into('<I',raw,off+0x28,flags|0x20000)
        path.write_bytes(raw)
    return True

def manifest(game_dir):
    from iop_network import patched_bytes
    root=Path(game_dir)
    files={p.relative_to(root).as_posix().lower():p for folder in ('data','map','script','scripts')
           for p in (root/folder).rglob('*') if p.is_file() and p.suffix.lower() in ('.res','.map','.scr','.dll')
           and not is_multiplayer_map(p.relative_to(root).as_posix())}
    if 'data/gweapon.res' not in files: raise ValueError('게임 데이터 폴더가 올바르지 않습니다.')
    from iop_hotkeys import normalize_exe,normalize_labels
    result={}
    for name,path in sorted(files.items()):
        data=path.read_bytes()
        if name in ('data/button.res','data/kbutton.res'): data=normalize_labels(data)
        result[name]=hashlib.sha256(data).hexdigest()
    result['iop.exe']=hashlib.sha256(normalize_exe(patched_bytes((root/'iop.exe').read_bytes(),'127.0.0.1'))).hexdigest()
    return {'protocol':1,'files':result}

def compare(local, remote):
    if not isinstance(remote,dict) or remote.get('protocol')!=1 or not isinstance(remote.get('files'),dict):
        raise ValueError('서버 런처를 v0.003 이상으로 업데이트하세요.')
    local_files={k:v for k,v in local['files'].items() if not is_multiplayer_map(k)}
    remote_files={k:v for k,v in remote['files'].items() if not is_multiplayer_map(k)}
    mismatches=[name for name in sorted(set(local_files)|set(remote_files)) if local_files.get(name)!=remote_files.get(name)]
    if mismatches:
        raise ValueError('A·B 게임 파일 불일치. 양쪽 게임을 종료하고 A의 동일한 데이터/패치를 적용하세요.\n'+'\n'.join(mismatches[:20]))
    return len(local_files)

async def serve_manifest(writer, game_dir):
    try:
        try: data=await asyncio.to_thread(manifest,game_dir)
        except Exception as exc: data={'error':str(exc)}
        payload=json.dumps(data,ensure_ascii=False).encode('utf-8')
        if len(payload)>LIMIT: return
        writer.write(struct.pack('<I',len(payload))+payload)
        await asyncio.wait_for(writer.drain(),5)
    except (ConnectionError, asyncio.TimeoutError): pass
    finally:
        writer.close()
        try: await writer.wait_closed()
        except ConnectionError: pass

def fetch_manifest(ip, port=SYNC_PORT):
    with socket.create_connection((ip,port),5) as sock:
        deadline=time.monotonic()+15
        def exact(size):
            result=bytearray()
            while len(result)<size:
                sock.settimeout(max(.01,deadline-time.monotonic()))
                chunk=sock.recv(size-len(result))
                if not chunk: raise ValueError('서버 동기화 응답이 끊겼습니다.')
                result.extend(chunk)
            return bytes(result)
        size=struct.unpack('<I',exact(4))[0]
        if size>LIMIT: raise ValueError('동기화 응답 크기 오류')
        return json.loads(exact(size))

def display_mode(path, mode):
    path=Path(path)
    if not path.exists(): return
    lines=path.read_bytes().decode('latin-1').splitlines()
    start=next((i for i,line in enumerate(lines) if line.strip().lower()=='[iop]'),None)
    if start is None:
        lines+=['','[iop]']; start=len(lines)-1
    end=next((i for i in range(start+1,len(lines)) if lines[i].strip().startswith('[')),len(lines))
    body=[line for line in lines[start+1:end] if line.split('=',1)[0].strip().lower() not in ('windowed','fullscreen')]
    body+=['windowed='+('false' if mode=='full' else 'true'),'fullscreen='+('true' if mode=='full' else 'false')]
    path.write_bytes(('\r\n'.join(lines[:start+1]+body+lines[end:])+'\r\n').encode('latin-1'))
