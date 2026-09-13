"""Skip the isolated 0x07000037 screen distortion renderer that reads before the framebuffer."""
from pathlib import Path
import os
import struct
OFFSET=0x6f750
ORIGINAL=bytes.fromhex('8b50088b8c2a64ffffff')
PATCH=b'\xe9'+struct.pack('<i',0x46f981-(0x46f750+5))

def normalize(data):
    # Only the first complete instruction group (3+7 bytes) is replaced.
    old=data[OFFSET:OFFSET+10]
    if old==PATCH+b'\x90'*5:return data[:OFFSET]+ORIGINAL+data[OFFSET+10:]
    return data

def apply(game_dir):
    path=Path(game_dir)/'iop.exe';data=path.read_bytes()
    if data[OFFSET:OFFSET+10]==PATCH+b'\x90'*5:return False
    if data[OFFSET:OFFSET+10]!=ORIGINAL:raise ValueError('지원되지 않는 화면 효과 코드입니다. 실행파일을 변경하지 않았습니다.')
    from iop_network import patched_bytes
    patched_bytes(data,'127.0.0.1')
    with path.open('r+b') as stream:
        backup=path.with_name('iop.exe.before-render-fix.bak')
        if not backup.exists():backup.write_bytes(data)
        stream.seek(OFFSET);stream.write(PATCH+b'\x90'*5);stream.flush();os.fsync(stream.fileno())
    return True
