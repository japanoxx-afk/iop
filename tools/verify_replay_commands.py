"""Emulate native command encoding/queueing, without modifying or launching IOP.

This verifies research findings, NOT complete replay determinism.
"""
import struct
import sys
from pathlib import Path
sys.path[:0]=[str(Path(__file__).resolve().parents[2]/'analysis_lib')]
from unicorn import Uc,UC_ARCH_X86,UC_MODE_32
from unicorn.x86_const import UC_X86_REG_ESP

raw=Path(r'C:\Users\seo\Downloads\DGGL\Games\IOP_Win\iop.exe').read_bytes()
u=Uc(UC_ARCH_X86,UC_MODE_32);u.mem_map(0x400000,0x800000);u.mem_write(0x400000,raw)
def write32(address,value):u.mem_write(address,struct.pack('<I',value))
def call(address,*args):
    stack=0xb00000
    u.mem_write(stack,struct.pack('<'+'I'*(len(args)+1),0x9ff000,*args))
    u.reg_write(UC_X86_REG_ESP,stack);u.emu_start(address,0x9ff000,count=10000)

records=bytearray(100)
for i,ident in enumerate((42,43)):
    offset=i*50
    struct.pack_into('<BII B I',records,offset,0,123,5001,3,ident)
    struct.pack_into('<I',records,offset+18,0x05000002)
u.mem_write(0xa00000,bytes(records))
call(0x423e20,0xa00000,0xa01000,2)
expected=struct.pack('<BBIBIHHI',2,3,123,0,5001,42,43,0x05000002)
assert bytes(u.mem_read(0xa01000,len(expected)))==expected

# The real native per-player queue calls only locking and the native copy routine.
# Mock Enter/LeaveCriticalSection; keep queue arithmetic/copying original.
u.mem_write(0x9f0000,b'\xc2\x04\x00')
write32(0x4c80b4,0x9f0000);write32(0x4c80b8,0x9f0000)
write32(0x508300,0xa02000)
write32(0xa02000+9+3*4,0xa03000)
write32(0xa03000+0xa9,0xa10000)
write32(0xa10000,1) # ready
call(0x43ac50,3,0xa01000)
assert struct.unpack('<I',u.mem_read(0xa10008,4))[0]==1
assert bytes(u.mem_read(0xa10010,len(expected)))==expected
print('Native production command: two unit IDs, scheduled tick, player, resource ID verified.')
print('Native 111-byte per-player queue slot verified. Complete match replay NOT verified.')
