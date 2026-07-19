# -*- coding: utf-8 -*-
# iop.exe 프로세스 메모리에서 특정 32비트 값의 출현 횟수를 센다.
# 목적: 편집한 HP=9999(0x270F)가 게임 엔진에 실제 로드됐는지 확인.
import ctypes, sys, struct
from ctypes import wintypes

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
MEM_COMMIT = 0x1000
PAGE_READABLE = (0x02, 0x04, 0x20, 0x40)  # RO, RW, EXEC_R, EXEC_RW

k32 = ctypes.WinDLL("kernel32", use_last_error=True)

class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_void_p),
                ("AllocationBase", ctypes.c_void_p),
                ("AllocationProtect", wintypes.DWORD),
                ("RegionSize", ctypes.c_size_t),
                ("State", wintypes.DWORD),
                ("Protect", wintypes.DWORD),
                ("Type", wintypes.DWORD)]

def scan(pid, target_u32):
    h = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not h:
        print("OpenProcess 실패", ctypes.get_last_error()); return
    target = struct.pack("<I", target_u32)
    addr = 0
    max_addr = 0x7FFFFFFF
    mbi = MEMORY_BASIC_INFORMATION()
    count = 0
    scanned = 0
    while addr < max_addr:
        r = k32.VirtualQueryEx(h, ctypes.c_void_p(addr), ctypes.byref(mbi),
                               ctypes.sizeof(mbi))
        if not r:
            break
        base = mbi.BaseAddress or 0
        size = mbi.RegionSize
        if mbi.State == MEM_COMMIT and mbi.Protect in PAGE_READABLE:
            buf = (ctypes.c_char * size)()
            read = ctypes.c_size_t(0)
            if k32.ReadProcessMemory(h, ctypes.c_void_p(base), buf, size,
                                     ctypes.byref(read)):
                data = bytes(buf[:read.value])
                scanned += len(data)
                start = 0
                while True:
                    i = data.find(target, start)
                    if i < 0:
                        break
                    count += 1
                    start = i + 1
        addr = base + size
    k32.CloseHandle(h)
    print(f"값 {target_u32}(0x{target_u32:04X}) 출현: {count}회  (스캔 {scanned//(1024*1024)}MB)")
    return count

if __name__ == "__main__":
    pid = int(sys.argv[1])
    for v in sys.argv[2:]:
        scan(pid, int(v))
