"""Install the replay recorder/player section into iop.exe (v0.019 base).

The code lives in a new PE section ".iopr" appended after the file's
existing overlay (the original 0xD07D3-byte overlay is kept byte for byte)
and eleven hook sites call into a jump table at its start; see
tools/replay_patch.asm for the contract of every hook.

Recording is always on in the patched iop.exe: a skirmish (mode 1) or a
network game (mode 2) writes Replays\\_rec_XXXXXXXX.iopraw in the game folder
(header = setup block + rand states, then per tick the rand state and every
executed command). Playback runs a copy, iop_replay.exe, whose control word
C_MODE is 1: it skips the menu, rebuilds the recorded match from
Replays\\_play.iopraw and feeds the commands tick by tick.

normalize() strips the section and hooks exactly, so the A/B file comparison
and the canonical hash checks keep working on a patched iop.exe.
"""
from __future__ import annotations

import os
from pathlib import Path
import struct

from iop_replay_code import CODE, CTRL_INIT

BASE = 0x400000
SECTION_NAME = b".iopr\0\0\0"
SECTION_RVA = 0x384000
SECTION_VA = BASE + SECTION_RVA
VIRTUAL_SIZE = 0x12000
RAW_SIZE = 0x2000
CHARACTERISTICS = 0xE0000060          # code | initialised data | exec | read | write
ORIGINAL_SIZE = 0x1B47D3              # v0.019 iop.exe incl. overlay
RAW_OFFSET = 0x1B5000                 # first FileAlignment boundary after the overlay
NUMBER_OF_SECTIONS = 0x106
SIZE_OF_IMAGE = 0x150
ORIGINAL_SIZE_OF_IMAGE = 0x38378E
HEADER_SLOT = 0x298                   # fifth section header
ORIGINAL_HEADER_SLOT = bytes(24) + b"Safedisc2Cleaner"
FEATURE_VERSION = "2"   # 2: rally on a refinery makes new workers harvest

CTRL_VA = SECTION_VA + 0x1000
CTRL = {
    "magic": 0x00, "version": 0x04, "mode": 0x08, "paused": 0x0C, "speed": 0x10,
    "vision": 0x14, "step": 0x18, "tick": 0x20, "end_tick": 0x24, "desync": 0x28,
    "first_desync": 0x2C, "ended": 0x30, "recording": 0x34, "commands": 0x38,
    "started": 0x3C, "rand_fixed": 0x40, "no_record": 0x44,
}

# hook site file offset (== RVA for .text) -> original bytes
ORIGINAL = {
    0x0059F3: bytes.fromhex("e8e8fbffff"),      # H1  call 0x4055E0 (skirmish builder)
    0x08F3D9: bytes.fromhex("e8c2fcffff"),      # H2  call 0x48F0A0 (network builder)
    0x004E51: bytes.fromhex("e85a0a0000"),      # H3  call 0x4058B0 (skirmish setup)
    0x004EAC: bytes.fromhex("e82f0e0000"),      # H4  call 0x405CE0 (after setup)
    0x0044F7: bytes.fromhex("e894a40800"),      # H5  call 0x48E990 (tick advance)
    0x004510: bytes.fromhex("e80b160300"),      # H6  call 0x435B20 (tick commands)
    0x035BEB: bytes.fromhex("e860000000"),      # H7  call 0x435C50 (command)
    0x034060: bytes.fromhex("83ec0c5355"),      # H8  0x434060 entry (player leaves)
    0x003F4B: bytes.fromhex("89becd410000"),    # H9  mov [esi+0x41CD], edi (menu)
    0x004783: bytes.fromhex("e8a8170000"),      # H10 call 0x405F30 (battle end)
    0x02DF95: bytes.fromhex("807c101101"),      # H11 cmp byte [eax+edx+0x11], 1 (fog)
    0x0247D0: bytes.fromhex("568b3500835000"),  # H12 0x4247D0 entry (player controllable)
    0x0495C0: bytes.fromhex("53558be98b850dfbffff"),  # H13 unit order vt+0x78
    0x049B30: bytes.fromhex("538bd98b830dfbffff"),    # H14 unit order vt+0x7C
    0x049CD0: bytes.fromhex("538bd98b830dfbffff"),    # H15 unit order vt+0x80
}
# hook site -> (entry index, opcode, trailing nops)
HOOKS = {
    0x0059F3: (0, 0xE8, 0), 0x08F3D9: (1, 0xE8, 0), 0x004E51: (2, 0xE8, 0),
    0x004EAC: (3, 0xE8, 0), 0x0044F7: (4, 0xE8, 0), 0x004510: (5, 0xE8, 0),
    0x035BEB: (6, 0xE8, 0), 0x034060: (7, 0xE9, 0), 0x003F4B: (8, 0xE8, 1),
    0x004783: (9, 0xE8, 0), 0x02DF95: (10, 0xE8, 0), 0x0247D0: (11, 0xE9, 2),
    0x0495C0: (12, 0xE9, 5), 0x049B30: (13, 0xE9, 4), 0x049CD0: (14, 0xE9, 4),
}


def _hook_bytes(offset):
    index, opcode, nops = HOOKS[offset]
    target = SECTION_VA + 5 * index
    return bytes([opcode]) + struct.pack("<i", target - (BASE + offset + 5)) + b"\x90" * nops


PATCHED = {offset: _hook_bytes(offset) for offset in HOOKS}


def _section_header():
    return SECTION_NAME + struct.pack(
        "<IIIIIIHHI", VIRTUAL_SIZE, SECTION_RVA, RAW_SIZE, RAW_OFFSET, 0, 0, 0, 0, CHARACTERISTICS)


def section_image(mode=0, record=True):
    ctrl = bytearray(CTRL_INIT.ljust(0x1000, b"\0"))
    struct.pack_into("<I", ctrl, CTRL["mode"], mode)
    struct.pack_into("<I", ctrl, CTRL["no_record"], 0 if record else 1)
    return CODE.ljust(0x1000, b"\0") + bytes(ctrl)


def _matches(data, layout):
    return all(data[o:o + len(b)] == b for o, b in layout.items())


def installed_mode(data):
    """None when the section is absent, else the C_MODE word (0 record, 1 play)."""
    if len(data) < RAW_OFFSET + RAW_SIZE or data[HEADER_SLOT:HEADER_SLOT + 8] != SECTION_NAME:
        return None
    return struct.unpack_from("<I", data, RAW_OFFSET + 0x1000 + CTRL["mode"])[0]


def is_current(data, mode=0, record=True):
    return (installed_mode(data) == mode and _matches(data, PATCHED)
            and data[HEADER_SLOT:HEADER_SLOT + 40] == _section_header()
            and data[RAW_OFFSET:RAW_OFFSET + RAW_SIZE] == section_image(mode, record)
            and len(data) == RAW_OFFSET + RAW_SIZE)


LEGACY_HOOKS = (
    # v0.041: H1-H11 only
    {o: b for o, b in ORIGINAL.items() if o not in (0x0247D0, 0x0495C0, 0x049B30, 0x049CD0)},
)


def normalize(data):
    """Remove the replay section and hooks (current or v0.041); anything else is returned untouched."""
    if installed_mode(data) is None:
        return data
    if not _matches(data, PATCHED):
        old = {o: _hook_bytes(o) for o in LEGACY_HOOKS[0]}
        if not _matches(data, old):
            return data
    result = bytearray(data[:ORIGINAL_SIZE])
    for offset, block in ORIGINAL.items():
        result[offset:offset + len(block)] = block
    result[HEADER_SLOT:HEADER_SLOT + 40] = ORIGINAL_HEADER_SLOT
    struct.pack_into("<H", result, NUMBER_OF_SECTIONS, 4)
    struct.pack_into("<I", result, SIZE_OF_IMAGE, ORIGINAL_SIZE_OF_IMAGE)
    return bytes(result)


def patched_bytes(data, mode=0, record=True):
    if is_current(data, mode, record):
        return data
    base = normalize(data)
    if (len(base) != ORIGINAL_SIZE or not _matches(base, ORIGINAL)
            or base[HEADER_SLOT:HEADER_SLOT + 40] != ORIGINAL_HEADER_SLOT
            or struct.unpack_from("<H", base, NUMBER_OF_SECTIONS)[0] != 4):
        raise ValueError("지원되지 않는 iop.exe입니다. 리플레이 코드를 설치하지 않았습니다.")
    result = bytearray(base)
    for offset, block in PATCHED.items():
        result[offset:offset + len(block)] = block
    result[HEADER_SLOT:HEADER_SLOT + 40] = _section_header()
    struct.pack_into("<H", result, NUMBER_OF_SECTIONS, 5)
    struct.pack_into("<I", result, SIZE_OF_IMAGE, SECTION_RVA + VIRTUAL_SIZE)
    result.extend(bytes(RAW_OFFSET - len(result)))
    result.extend(section_image(mode, record))
    return bytes(result)


def _write(path, old, new, backup_name):
    backup = path.with_name(backup_name)
    if not backup.exists():
        backup.write_bytes(old)
    # Opening a running Windows image for writing fails before anything changes.
    with path.open("r+b") as stream:
        stream.write(new)
        stream.truncate()
        stream.flush()
        os.fsync(stream.fileno())


def apply(game_dir, record=True):
    """Install the section (rally harvest + recorder) into iop.exe. True when the file changed.

    The section always stays installed because the rally rule changes the simulation and
    every PC in a match must have it; record=False only switches the recorder off."""
    path = Path(game_dir) / "iop.exe"
    data = path.read_bytes()
    result = patched_bytes(data, 0, record)
    if result == data:
        return False
    _write(path, data, result, "iop.exe.before-replay.bak")
    (Path(game_dir) / "Replays").mkdir(exist_ok=True)
    return True


def remove(game_dir):
    path = Path(game_dir) / "iop.exe"
    data = path.read_bytes()
    result = normalize(data)
    if result == data:
        return False
    _write(path, data, result, "iop.exe.before-replay-removal.bak")
    return True


PLAY_EXE = "iop_replay.exe"


def make_play_exe(game_dir):
    """Write iop_replay.exe = current iop.exe (all other patches kept) in playback mode."""
    root = Path(game_dir)
    data = patched_bytes((root / "iop.exe").read_bytes(), 1)
    target = root / PLAY_EXE
    if target.exists() and target.read_bytes() == data:
        return target
    stage = target.with_name(PLAY_EXE + ".new")
    stage.write_bytes(data)
    os.replace(stage, target)
    return target
