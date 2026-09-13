"""Production-only hotkeys using the game's existing context command tables."""
import hashlib
import os
from pathlib import Path
import re
import struct
import time
from iop_hotkey_layout import START,END,ORIGINAL,TABLES

KEYS=tuple(TABLES)
PRODUCTION=[(key,tuple(row)) for key,t in TABLES.items() for row in t['rows'] if row[1]==23]
DEFAULTS={f'{row[0]:08x}:{row[2]:08x}':key for key,row in PRODUCTION}

def validate(mapping):
    if not isinstance(mapping,dict) or set(mapping)-set(DEFAULTS): raise ValueError('알 수 없는 유닛 단축키 설정입니다. 목록을 다시 불러오세요.')
    effective={**DEFAULTS,**mapping}
    for key in effective.values():
        if key not in KEYS: raise ValueError('지원 키: '+', '.join(KEYS)+' (영문 한 글자)')
    occupied={(key,row[0]) for key,t in TABLES.items() for row in t['rows'] if row[1]!=23}
    for _,row in PRODUCTION:
        ident=f'{row[0]:08x}:{row[2]:08x}'; key=effective[ident]
        if (key,row[0]) in occupied: raise ValueError(f'{key}: 같은 생산 건물의 다른 유닛 또는 기존 명령과 중복됩니다. ({ident})')
        occupied.add((key,row[0]))
    return effective

def render(mapping):
    effective=validate(mapping)
    if effective==DEFAULTS:
        return ORIGINAL,{key:t['ptr'] for key,t in TABLES.items()}
    tables={key:[tuple(row) for row in t['rows'] if row[1]!=23] for key,t in TABLES.items()}
    for _,row in PRODUCTION: tables[effective[f'{row[0]:08x}:{row[2]:08x}']].append(row)
    region=bytearray(); pointers={}
    for key,rows in tables.items():
        pointers[key]=0x400000+START+len(region)
        for row in rows: region.extend(struct.pack('<IBII',*row))
        region.extend(b'\0'*13)
    if len(region)>END-START: raise ValueError('단축키 테이블 공간 부족')
    return bytes(region).ljust(END-START,b'\0'),pointers

def normalize_exe(data):
    if len(data)<END: raise ValueError('지원되지 않는 실행파일')
    original_refs=all(struct.unpack_from('<I',data,off)[0]==t['ptr'] for t in TABLES.values() for off in t['refs'])
    if data[START:END]==ORIGINAL and original_refs: return data
    mapping={}
    for key,t in TABLES.items():
        ptr=struct.unpack_from('<I',data,t['refs'][0])[0]-0x400000
        if not START<=ptr<END: raise ValueError('단축키 테이블 포인터 오류')
        while True:
            if ptr+13>END: raise ValueError('단축키 테이블 범위 오류')
            row=struct.unpack_from('<IBII',data,ptr);ptr+=13
            if not row[0]: break
            if row[1]==23:
                ident=f'{row[0]:08x}:{row[2]:08x}'
                if ident in mapping: raise ValueError('중복 생산 명령')
                mapping[ident]=key
    if set(mapping)!=set(DEFAULTS): raise ValueError('생산 명령 누락 또는 변경')
    region,pointers=render(mapping)
    if region!=data[START:END] or any(struct.unpack_from('<I',data,off)[0]!=pointers[k] for k,t in TABLES.items() for off in t['refs']):
        raise ValueError('지원되지 않는 명령표 변경입니다. 단축키 이외의 변경을 보존하기 위해 중단합니다.')
    result=bytearray(data);result[START:END]=ORIGINAL
    for t in TABLES.values():
        for off in t['refs']:struct.pack_into('<I',result,off,t['ptr'])
    return bytes(result)

def patch_exe(data,mapping):
    from iop_network import ADDRESS_OFFSET,ORIGINAL_ADDRESS,ORIGINAL_HASH
    base=normalize_exe(data)
    from iop_stability import normalize
    normalized=normalize(base[:ADDRESS_OFFSET]+ORIGINAL_ADDRESS+base[ADDRESS_OFFSET+16:])
    if hashlib.sha256(normalized).hexdigest()!=ORIGINAL_HASH: raise ValueError('지원되지 않는 iop.exe 버전')
    region,pointers=render(mapping)
    result=bytearray(base);result[START:END]=region
    for k,t in TABLES.items():
        for off in t['refs']:struct.pack_into('<I',result,off,pointers[k])
    return bytes(result)

def button_records(data, production_only=False):
    if len(data)<4: raise ValueError('버튼 파일 헤더 오류')
    count=struct.unpack_from('<I',data)[0]; header=4+count*8
    if not 0<count<10000 or header>len(data): raise ValueError('버튼 인덱스 오류')
    indexes=[struct.unpack_from('<HHI',data,4+i*8) for i in range(count)]
    offsets=sorted(x[2] for x in indexes)
    if len(set(offsets))!=count or offsets[0]<header or offsets[-1]>=len(data):raise ValueError('버튼 레코드 범위 오류')
    ends=dict(zip(offsets,offsets[1:]+[len(data)]))
    for uid,typ,off in indexes:
        if off+16>ends[off]:raise ValueError('버튼 레코드 크기 오류')
        size=struct.unpack_from('<I',data,off+12)[0]
        if not 2<=size<=4096 or off+16+size+8>ends[off]:raise ValueError('버튼 이름 범위 오류')
        if production_only and (struct.unpack_from('<I',data,off+4)[0]>>24!=0x15 or struct.unpack_from('<I',data,off+20+size)[0]!=2): continue
        label=data[off+17:off+16+size]
        target=struct.unpack_from('<I',data,off+16+size)[0]
        yield target,label,off+16+size-3

def patch_labels(data,mapping):
    effective=validate(mapping)
    byunit={row[2]:effective[f'{row[0]:08x}:{row[2]:08x}'] for _,row in PRODUCTION}
    result=bytearray(data)
    for unit,label,offset in button_records(data,production_only=True):
        if unit in byunit and re.search(rb'\([A-Z]\)\x00$',label): result[offset]=ord(byunit[unit])
    return bytes(result)

def normalize_labels(data): return patch_labels(data,{})

def is_applied(game_dir,mapping):
    """Check the persisted executable and labels without writing them again."""
    validate(mapping);root=Path(game_dir)
    exe=(root/'iop.exe').read_bytes()
    if patch_exe(exe,mapping)!=exe:return False
    for name in ('Button.res','Kbutton.res'):
        data=(root/'data'/name).read_bytes()
        if patch_labels(data,mapping)!=data:return False
    return True

def catalog(game_dir):
    names={}
    for target,label,_ in button_records((Path(game_dir)/'data/Kbutton.res').read_bytes()):
        names.setdefault(target,re.sub(r'\([A-Z]\)$','',label.rstrip(b'\0').decode('cp949',errors='replace')))
    races={0:'노블어스',0x20:'다크존',0x40:'아트로스'}
    return [{'id':ident,'race':races.get((row[0]>>16)&255,'기타'),'building':names.get(row[0],hex(row[0])),
             'unit':names.get(row[2],hex(row[2])),'default':key}
            for key,row in PRODUCTION for ident in [f'{row[0]:08x}:{row[2]:08x}']]

def apply(game_dir,mapping):
    validate(mapping);root=Path(game_dir);exe=root/'iop.exe'
    old={exe:exe.read_bytes()}
    new={exe:patch_exe(old[exe],mapping)}
    for name in ('Button.res','Kbutton.res'):
        p=root/'data'/name;old[p]=p.read_bytes();new[p]=patch_labels(old[p],mapping)
    changed=[p for p in new if new[p]!=old[p]]
    if not changed:return 0
    # Denied for a running Windows image, before touching any resource.
    with exe.open('r+b') as guard:
        stamp=str(time.time_ns())
        for p in changed:p.with_name(p.name+'.before-hotkeys-'+stamp+'.bak').write_bytes(old[p])
        try:
            for p in changed:
                if p==exe:
                    guard.seek(0);guard.write(new[p]);guard.flush();os.fsync(guard.fileno())
                else:p.write_bytes(new[p])
        except Exception:
            for p in changed:
                if p==exe:guard.seek(0);guard.write(old[p]);guard.flush()
                else:p.write_bytes(old[p])
            raise
    return len(changed)
