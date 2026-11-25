"""Run the replay section inside the real iop.exe image under Unicorn.

Records one tick through the game's own command queue/executor (0x435B20 ->
patched 0x435BEB), then plays the file back in a fresh playback image and
checks the executor receives the same player and packet, the setup block and
rand states are restored, and pause/end/vision behave. Not a full match test.
"""
import struct
import unittest
from pathlib import Path

try:
    from unicorn import Uc, UC_ARCH_X86, UC_MODE_32, UC_HOOK_CODE
    from unicorn.x86_const import (UC_X86_REG_EAX, UC_X86_REG_ECX, UC_X86_REG_EDX, UC_X86_REG_ESI,
                                   UC_X86_REG_EDI, UC_X86_REG_ESP, UC_X86_REG_EFLAGS, UC_X86_REG_EIP)
except ImportError:  # pragma: no cover
    Uc = None

import iop_replay_patch as rp

ORIGINAL = Path('game-assets/v0.019/iop.exe').read_bytes()
STUBS = 0x9F0000
STACK = 0xB00000
DONE = 0x9FF000
PTD = 0xA80000
HEAP = 0xC00000
TM, P0, P1, Q0, Q1, ROOT = 0xA00000, 0xA01000, 0xA02000, 0xA10000, 0xA20000, 0xA40000
SEC = rp.SECTION_VA
CTRL = rp.CTRL_VA


def entry(index):
    return SEC + 5 * index


class Game:
    def __init__(self, mode, files=None):
        import pefile
        pe = pefile.PE(data=rp.patched_bytes(ORIGINAL, mode))
        image = pe.get_memory_mapped_image()
        self.u = u = Uc(UC_ARCH_X86, UC_MODE_32)
        u.mem_map(0x400000, 0x400000)
        u.mem_write(0x400000, image[:0x396000 - 0x0])
        for base, size in ((STUBS, 0x10000), (0xA00000, 0xE0000), (STACK - 0x10000, 0x20000), (HEAP, 0x100000)):
            u.mem_map(base, size)
        self.files = files if files is not None else {}
        self.written = bytearray()
        self.calls = []
        self.stub_names = {}
        self.time = 5000
        stubs = {
            0x4C80B4: ('EnterCriticalSection', 1, lambda a: 0),
            0x4C80B8: ('LeaveCriticalSection', 1, lambda a: 0),
            0x4C80C0: ('CreateFileA', 7, self.create_file),
            0x4C809C: ('WriteFile', 5, self.write_file),
            0x4C80C4: ('ReadFile', 5, self.read_file),
            0x4C80CC: ('CloseHandle', 1, lambda a: 1),
            0x4C8098: ('GetFileSize', 2, lambda a: len(self.files[self.opened])),
            0x4C81E0: ('GetTickCount', 0, lambda a: 0x1234ABCD),
            0x4C8298: ('timeGetTime', 0, lambda a: self.time),
            0x4C80AC: ('Sleep', 1, lambda a: 0),
            0x4C807C: ('ExitProcess', 1, self.exit),
            0x4C827C: ('MessageBoxA', 4, lambda a: 0),
        }
        for i, (iat, spec) in enumerate(stubs.items()):
            address = STUBS + i * 16
            u.mem_write(iat, struct.pack('<I', address))
            u.mem_write(address, b'\xcc')
            self.stub_names[address] = spec
        # The CRT per-thread data and malloc are replaced by fixed memory.
        u.mem_write(0x4BD603, b'\xb8' + struct.pack('<I', PTD) + b'\xc3')
        u.mem_write(0x4B3180, b'\xb8' + struct.pack('<I', HEAP) + b'\xc3')
        # Game functions outside this test become recorders that return.
        self.fakes = {0x435C50: 4, 0x4055E0: 0, 0x48F0A0: 0, 0x405CE0: 0, 0x405F30: 0,
                      0x48C2D0: 0, 0x434065: 8, 0x48E990: 0, 0x42DA60: 0, 0x46D320: 0,
                      STUBS + 0x900: 4}
        self.fake_ret = {0x46D320: 77, STUBS + 0x900: 0x7D3}
        for address in self.fakes:
            u.mem_write(address, b'\xcc')
        u.hook_add(UC_HOOK_CODE, self.on_code, begin=0x400000, end=0x9FFFFF)
        self.handle = 0x4440
        self.opened = None
        self.read_pos = 0

    # --- stubs
    def create_file(self, a):
        name = bytes(self.u.mem_read(a[0], 64)).split(b'\0')[0].decode()
        access = a[1]
        if access == 0x80000000 and name not in self.files:
            return 0xFFFFFFFF
        self.opened = name
        if access != 0x80000000:
            self.files[name] = b''
        return self.handle

    def write_file(self, a):
        data = bytes(self.u.mem_read(a[1], a[2]))
        self.files[self.opened] += data
        self.u.mem_write(a[3], struct.pack('<I', a[2]))
        return 1

    def read_file(self, a):
        data = self.files[self.opened][:a[2]]
        self.u.mem_write(a[1], data)
        self.u.mem_write(a[3], struct.pack('<I', len(data)))
        return 1

    def exit(self, a):
        self.calls.append(('ExitProcess', a[0]))
        self.u.emu_stop()
        return 0

    def on_code(self, u, address, size, _):
        if address in (0x403F96, 0x403F50, DONE, 0x4495CA, 0x449B39, 0x449CD9, 0x4247D7):
            u.emu_stop()
        elif address in self.stub_names:
            name, argc, fn = self.stub_names[address]
            esp = u.reg_read(UC_X86_REG_ESP)
            ret, *args = struct.unpack('<%dI' % (argc + 1), u.mem_read(esp, 4 * (argc + 1)))
            result = fn(args)
            self.calls.append((name, args))
            u.reg_write(UC_X86_REG_EAX, result & 0xFFFFFFFF)
            u.reg_write(UC_X86_REG_ESP, esp + 4 + 4 * argc)
            u.reg_write(UC_X86_REG_EIP, ret)
        elif address in self.fakes:
            esp = u.reg_read(UC_X86_REG_ESP)
            ret = struct.unpack('<I', u.mem_read(esp, 4))[0]
            argc = self.fakes[address] // 4
            args = struct.unpack('<%dI' % argc, u.mem_read(esp + 4, 4 * argc)) if argc else ()
            self.calls.append((hex(address), u.reg_read(UC_X86_REG_ECX), args))
            if address in self.fake_ret:
                u.reg_write(UC_X86_REG_EAX, self.fake_ret[address])
            u.reg_write(UC_X86_REG_ESP, esp + 4 + 4 * argc)
            u.reg_write(UC_X86_REG_EIP, ret)

    # --- helpers
    def w32(self, address, value):
        self.u.mem_write(address, struct.pack('<I', value & 0xFFFFFFFF))

    def r32(self, address):
        return struct.unpack('<I', self.u.mem_read(address, 4))[0]

    def call(self, address, ecx=0, esi=0, edi=0, eax=0, edx=0, ret=DONE):
        u = self.u
        esp = STACK - 0x100
        u.mem_write(esp, struct.pack('<I', ret))
        for reg, value in ((UC_X86_REG_ECX, ecx), (UC_X86_REG_ESI, esi), (UC_X86_REG_EDI, edi),
                           (UC_X86_REG_EAX, eax), (UC_X86_REG_EDX, edx), (UC_X86_REG_ESP, esp)):
            u.reg_write(reg, value)
        u.emu_start(address, 0xFFFFFFFF, count=200000)
        return esp

    def rand(self):
        return self.r32(PTD + 0x14)

    def team(self, players=2):
        self.w32(0x508300, TM)
        self.u.mem_write(TM + 1, struct.pack('<I', P0))  # local player pointer
        self.w32(TM + 5, players)
        self.w32(TM + 9, P0)
        self.w32(TM + 13, P1)
        self.u.mem_write(TM, b'\x00')
        for obj, queue, index in ((P0, Q0, 0), (P1, Q1, 1)):
            self.u.mem_write(obj, bytes([index]))
            self.w32(obj + 0x1A, 500 + index)      # adamas
            self.w32(obj + 0x1E, 200 + index)      # platinum
            self.w32(obj + 0xA9, queue)
            self.w32(queue, 1)  # ready


def packet(player, tick, ident):
    data = bytearray(0x6F)
    struct.pack_into('<BBIBIHHI', data, 0, 2, player, tick, 0, 5001, ident, ident + 1, 0x05000002)
    return bytes(data)


@unittest.skipIf(Uc is None, 'unicorn not installed')
class ReplayEmulation(unittest.TestCase):
    def record(self):
        g = Game(0)
        g.team()
        block = bytes((i * 7 + 3) & 0xFF for i in range(0x1B0))
        g.u.mem_write(0x6C92B0, block)
        g.u.mem_write(0x4E53CC, b'\x01')
        g.w32(0x4DE0F8, 15)
        g.w32(0x4E4678, 0x77)
        g.w32(PTD + 0x14, 0x1111)
        g.call(entry(0))                     # H1 -> builder
        self.assertIn(('0x4055e0', 0, ()), [c[:3] for c in g.calls if c[0] == '0x4055e0'])
        g.w32(PTD + 0x14, 0x2222)
        g.call(entry(3), ecx=ROOT)           # H4 -> header
        self.assertEqual(g.r32(CTRL + 0x34), 1)
        # one queued command for player 1 at tick 7, executed by the real 0x435B20
        g.w32(0x6C946C, 7)
        g.u.mem_write(Q1 + 0x10, packet(1, 7, 42))
        g.w32(Q1 + 8, 1)
        g.w32(PTD + 0x14, 0x3333)
        g.call(entry(5), ecx=TM)             # H6 -> 0x435B20 -> H7 -> 0x435C50
        executed = [c for c in g.calls if c[0] == '0x435c50']
        self.assertEqual(len(executed), 1)
        self.assertEqual(executed[0][1], P1)
        g.w32(0x6C946C, 8)
        g.w32(PTD + 0x14, 0x4444)
        g.call(entry(5), ecx=TM)
        g.call(entry(9), ecx=ROOT)           # H10 -> close
        self.assertEqual(g.r32(CTRL + 0x34), 0)
        (name, data), = g.files.items()
        self.assertRegex(name, r'^Replays\\_rec_1234ABCD\.iopraw$')
        return data, block

    def test_record_layout(self):
        data, block = self.record()
        self.assertEqual(data[:4], b'IOPR')
        mode, pre, post, local, tps = struct.unpack_from('<5I', data, 8)
        self.assertEqual((mode, pre, post, local, tps), (1, 0x1111, 0x2222, 0x77, 15))
        self.assertEqual(data[0x40:0x40 + 0x1B0], block)
        body = data[0x200:]
        h = 0
        for value in (500, 200, 501, 201):
            h = (h * 31 + value) & 0xFFFFFFFF
        self.assertEqual(body[:13], b'\x01' + struct.pack('<III', 7, 0x3333, h))
        self.assertEqual(body[13:15], b'\x02\x01')
        self.assertEqual(body[15:15 + 0x6F], packet(1, 7, 42))
        self.assertEqual(body[0x7E:0x8B], b'\x01' + struct.pack('<III', 8, 0x4444, h))
        self.assertEqual(body[0x8B:], b'\x04' + struct.pack('<I', 8))

    def test_playback(self):
        data, block = self.record()
        g = Game(1, {'Replays\\_play.iopraw': data})
        g.team()
        g.w32(ROOT + 0xC2C, 0)
        esp = g.call(entry(8), esi=ROOT, edi=3, ret=0x403F50)  # H9: never returns to the menu loop
        self.assertEqual(g.r32(esp), 0x403F96)
        self.assertEqual(g.r32(ROOT + 0x41CD), 2)
        self.assertEqual(bytes(g.u.mem_read(0x4E53CC, 1)), b'\x01')
        self.assertEqual(g.r32(CTRL + 0x24), 8)               # end tick
        g.u.mem_write(0x6C92B0, bytes(0x1B0))
        g.call(entry(2), ecx=ROOT)                            # H3: rebuild
        self.assertEqual(bytes(g.u.mem_read(0x6C92B0, 0x1B0)), block)
        self.assertEqual(g.r32(0x4E4678), 0x77)
        self.assertTrue(any(c[0] == '0x4055e0' for c in g.calls))
        self.assertFalse(any(c[0] == '0x4058b0' for c in g.calls))
        g.w32(PTD + 0x14, 0xDEAD)
        g.call(entry(3), ecx=ROOT)                            # H4: rand after setup, speed
        self.assertEqual(g.rand(), 0x2222)
        self.assertEqual(g.r32(0x4DE0F8), 15)
        # tick 6: nothing yet
        g.w32(0x6C946C, 6)
        g.call(entry(5), ecx=TM)
        self.assertFalse(any(c[0] == '0x435c50' for c in g.calls))
        # tick 7: rand forced, command for player 1
        g.w32(0x6C946C, 7)
        g.w32(PTD + 0x14, 0x3333)
        g.call(entry(5), ecx=TM)
        executed = [c for c in g.calls if c[0] == '0x435c50']
        self.assertEqual(len(executed), 1)
        self.assertEqual(executed[0][1], P1)
        self.assertEqual(bytes(g.u.mem_read(executed[0][2][0], 0x6F)), packet(1, 7, 42))
        self.assertEqual(g.r32(CTRL + 0x28), 0)
        self.assertEqual(g.r32(CTRL + 0x30), 0)
        # tick 8: rand differs (corrected silently) and resources differ (desync), then the end
        g.w32(0x6C946C, 8)
        g.w32(PTD + 0x14, 0x9999)
        g.w32(P0 + 0x1A, 499)
        g.call(entry(5), ecx=TM)
        self.assertEqual(g.rand(), 0x4444)
        self.assertEqual(g.r32(CTRL + 0x40), 1)
        self.assertEqual(g.r32(CTRL + 0x28), 1)
        self.assertEqual(g.r32(CTRL + 0x2C), 9)
        self.assertEqual(g.r32(CTRL + 0x30), 1)
        # ended: tick advance is held
        g.call(entry(4))
        self.assertFalse(any(c[0] == '0x48e990' for c in g.calls))
        self.assertEqual(g.r32(0x6C946C), 8)
        # second menu entry after the battle exits the replay process
        g.call(entry(8), esi=ROOT, edi=3, ret=0x403F50)
        self.assertIn(('ExitProcess', 0), g.calls)

    def test_pause_step_speed(self):
        data, _ = self.record()
        g = Game(1, {'Replays\\_play.iopraw': data})
        g.team()
        g.call(entry(8), esi=ROOT, edi=3, ret=0x403F50)
        g.w32(0x6C946C, 3)
        g.w32(CTRL + 0x0C, 1)
        g.call(entry(4))
        self.assertEqual(g.r32(0x6C946C), 3)
        g.w32(CTRL + 0x18, 1)
        g.call(entry(4))
        self.assertEqual(g.r32(0x6C946C), 4)
        self.assertEqual(g.r32(CTRL + 0x18), 0)
        g.w32(CTRL + 0x0C, 0)
        g.w32(CTRL + 0x10, 60)
        g.call(entry(4))
        self.assertEqual(g.r32(0x4DE0F8), 60)
        self.assertTrue(any(c[0] == '0x48e990' for c in g.calls))

    def test_vision(self):
        g = Game(1)
        obj = 0xA05000
        g.u.mem_write(obj + 0x11, bytes([1, 0, 0]))   # shares with viewer 0 only
        stub = STUBS + 0x800
        # call vision; setz al; then stop
        code = b'\xe8' + struct.pack('<i', entry(10) - (stub + 5)) + b'\x0f\x94\xc0'
        g.u.mem_write(stub, code)

        def seen(viewer, mode):
            g.w32(CTRL + 0x14, mode)
            g.u.reg_write(UC_X86_REG_EAX, viewer)
            g.u.reg_write(UC_X86_REG_EDX, obj)
            g.u.reg_write(UC_X86_REG_ESP, STACK - 0x100)
            g.u.emu_start(stub, stub + len(code))
            return g.u.reg_read(UC_X86_REG_EAX) & 0xFF

        self.assertEqual(seen(0, 0), 1)
        self.assertEqual(seen(1, 0), 0)
        self.assertEqual(seen(1, 1), 1)       # everyone
        self.assertEqual(seen(1, 2), 1)       # as player 0
        self.assertEqual(seen(0, 3), 0)       # as player 1

    def make_object(self, g, base, kind, x, y):
        vb, tr, vt = base + 0x200, base + 0x210, base + 0x220
        g.w32(base, vt)
        g.w32(base + 4, vb)
        g.w32(vb + 4, 0x40)
        g.w32(vb + 8, 0x80)
        g.w32(base + 0x40 + 8, tr)
        g.w32(tr + 4, kind)
        g.w32(base + 0x80 + 0x1D, x)
        g.w32(base + 0x80 + 0x21, y)
        g.w32(vt + 0x6C, STUBS + 0x900)
        g.u.mem_write(STUBS + 0x900, b'\xcc')
        return base

    def rally(self, unit_kind, command=0xBB8, x=1000, y=2000, entry_index=12, stop=0x4495CA):
        g = Game(0)
        worker = self.make_object(g, 0xA60000, unit_kind, 0, 0)
        refinery = self.make_object(g, 0xA61000, 0x18000004, 1030, 2010)
        other = self.make_object(g, 0xA62000, 0x18000001, 1001, 2001)   # closer, not a refinery
        table, array = 0xA63000, 0xA64000
        g.w32(0x507FA8, table)
        g.w32(table, array)
        g.w32(array + 5 * 4, other)
        g.w32(array + 9 * 4, refinery)
        rec = 0xA70000
        g.u.mem_write(rec, bytes(0x32))
        g.w32(rec + 5, command)
        g.w32(rec + 0x2A, x)
        g.w32(rec + 0x2E, y)
        esp = STACK - 0x100
        g.u.mem_write(esp, struct.pack('<II', DONE, rec))
        g.u.reg_write(UC_X86_REG_ESP, esp)
        g.u.reg_write(UC_X86_REG_ECX, worker + 0x4F7)
        g.u.emu_start(entry(entry_index), 0xFFFFFFFF, count=200000)
        self.assertEqual(g.u.reg_read(UC_X86_REG_EIP), stop)
        # the original prologue ran: push ebx (+ push ebp for vt+0x78), ecx kept
        self.assertEqual(g.u.reg_read(UC_X86_REG_ESP), esp - (8 if entry_index == 12 else 4))
        return g, rec, refinery

    def test_rally_on_refinery_becomes_harvest(self):
        for kind in (0x05000001, 0x13400011, 0x05200024):
            g, rec, refinery = self.rally(kind)
            self.assertEqual(g.r32(rec + 5), 0x7D3)
            self.assertEqual(g.r32(rec + 0x12), 77)
            chooser = [c for c in g.calls if c[0] == hex(STUBS + 0x900)]
            self.assertEqual(chooser[0][1], 0xA60000)      # worker asked about the refinery
            self.assertEqual(chooser[0][2], (refinery,))

    def test_rally_other_cases_untouched(self):
        g, rec, _ = self.rally(0x05000002)                  # not a worker
        self.assertEqual(g.r32(rec + 5), 0xBB8)
        g, rec, _ = self.rally(0x05000001, x=5000)          # nothing near
        self.assertEqual(g.r32(rec + 5), 0xBB8)
        g, rec, _ = self.rally(0x05000001, command=0x7D0)   # not a move
        self.assertEqual(g.r32(rec + 5), 0x7D0)
        for index, stop in ((13, 0x449B39), (14, 0x449CD9)):
            g, rec, _ = self.rally(0x13400011, entry_index=index, stop=stop)
            self.assertEqual(g.r32(rec + 5), 0x7D3)

    def test_replay_controls_everything_and_reveals_map(self):
        g = Game(1)
        g.call(entry(11), ret=DONE)
        self.assertEqual(g.u.reg_read(UC_X86_REG_EAX), 1)
        g2 = Game(0)
        g2.call(entry(11), ret=DONE)
        self.assertEqual(g2.u.reg_read(UC_X86_REG_EIP), 0x4247D7)   # recording PC: original check
        g.w32(0x4DC668, 1)
        g.w32(CTRL + 0x14, 1)
        g.call(entry(4))
        self.assertEqual(g.r32(0x4DC668), 0)
        g.w32(CTRL + 0x14, 2)
        g.call(entry(4))
        self.assertEqual(g.r32(0x4DC668), 1)

    def test_normal_game_untouched(self):
        g = Game(0)
        g.u.mem_write(0x4E53CC, b'\x00')     # campaign: no record file
        g.call(entry(3), ecx=ROOT)
        self.assertEqual(g.files, {})
        esp = g.call(entry(8), esi=ROOT, edi=3, ret=0x403F50)
        self.assertEqual(g.r32(ROOT + 0x41CD), 3)
        self.assertEqual(g.r32(esp), 0x403F50)


if __name__ == '__main__':
    unittest.main()
