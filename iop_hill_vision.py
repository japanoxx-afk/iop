"""Terrain-aware fog of war for maps carrying the custom 0x20 high-ground flag.

Rule: a ground unit or building standing on low ground does not reveal tiles
marked high ground. Observers on high ground and air units (object category
0x13) keep their normal sight. Fixed reveal points (0x42E0CC) are unchanged.

High ground is kept in a bitmap owned by this patch, never in the game's
runtime tile grid. The grid word at [0x6C97A4]+0x18 is shared with object
footprints: 0x4459E0 ORs each object's +0x42 bits (0x0010-0x8000, copied from
its type record +0x70) into it through 0x495C90, and resources use that path.
v0.032 stored high ground as grid bit 0x100, so occupied tiles such as
resources looked like high ground and low-ground units could not see them.

Hook points in the original v0.019 iop.exe (file offset == RVA in .text):
- 0x4276FB  map loader: raw tile bit 0x20 -> 0x100 in the tileset record.
            The only reader of that record word is the grid fill (0x480A82).
- 0x480A8E  grid fill call to 0x495C60: copy 0x100 into the bitmap, strip it
            from the value written to the game grid.
- 0x42E003 / 0x42E072  unit and building calls into fog reveal 0x42D940
            (x, y, sight 0..5; callee pops 12 bytes). The wrapper decides
            whether the observer is a low-ground ground unit and tail-jumps,
            so the original arguments reach 0x42D940 untouched. (v0.026-v0.029
            called through instead, turning tile y into the sight index:
            BBSIM ERROR #847234 / 0xCED82.)
- 0x42E0CC  fixed reveal points: clear the restriction, then tail-jump.
- 0x42DA18  per-cell reveal write inside 0x42D940.

Bitmap: game malloc 0x4B3180, 0x26000 bytes, bit index (y*19 << 8) | x, the
same y*19 the fog loop keeps in esi; maps up to 256x256 tiles. Its pointer and
the per-call restriction byte live past .data's VirtualSize (0x782DD8) in the
same zero-filled writable page, where no game variable can be.
"""
from pathlib import Path
import os
import struct

BASE = 0x400000
STATE_VA = 0x782DE0
BITMAP_PTR_VA = 0x782DE4
# Compared between A and B launchers; bump when the fog rule changes.
FEATURE_VERSION = "4"

CAVE_OFFSET = 0xC7F0A
CAVE_SIZE = 0xC8000 - CAVE_OFFSET
CAVE_VA = BASE + CAVE_OFFSET

ORIGINAL = {
    0x2E003: bytes.fromhex("e838f9ffff"),
    0x2E072: bytes.fromhex("e8c9f8ffff"),
    0x2E0CC: bytes.fromhex("e86ff8ffff"),
    0x2DA18: bytes.fromhex("03d63a0a7610880a"),
    0x276FB: bytes.fromhex("6689028b471c"),
    0x80A8E: bytes.fromhex("e8cd510100"),
    CAVE_OFFSET: bytes(CAVE_SIZE),
}

# Assembled at CAVE_VA (see module docstring for each block's contract).
CAVE_CODE = bytes.fromhex(
    # is_high: eax=x, edx=y*19 -> eax != 0 when high; clobbers edx
    "a900ffffff751cc1e20888c281faffff1200770fa1e42d780085c074080fa31019c0c331c0c3"
    # fixed: clear restriction, jmp 0x42D940
    "c605e02d780000e9045af6ff"
    # wrapper: air check, observer is_high, sete restriction, jmp 0x42D940
    "508b46048b40048b443020c1e8183c135874e150528b44240c8b5424106bd213e8a9ffffff"
    "85c00f9405e02d78005a58e9cf59f6ff"
    # reveal: original max-write unless restricted and target is high
    "01f23a0a761b803de02d780000740f505289f2e881ffffff85c05a587503880ac383042411c3"
    # setcell: lazy malloc, record/strip 0x100, jmp 0x495C60
    "a1e42d780085c075166800600200e8d6b1feff83c404a3e42d780085c0742d8b4c24088b54"
    "2404f7c200ffffff751d6bc913c1e10888d181f9ffff1200770d0fb308f644240d0174030f"
    "ab088064240dfee973dcfcff"
    # loader: tile bit 0x20 -> 0x100 in the tileset record
    "f6c120740380cc016689028b471cc3"
)
ENTRY = {"ishigh": 0x00, "fixed": 0x26, "wrapper": 0x32, "reveal": 0x67,
         "setcell": 0x8D, "loader": 0xE3}


def _call(offset, target_va):
    return b"\xe8" + struct.pack("<i", target_va - (BASE + offset + 5))


def _entry(name):
    return CAVE_VA + ENTRY[name]


# v0.038 line of sight. A low-ground ground observer also misses every cell
# whose line from the observer crosses a high-ground cell, so a ridge hides
# what lies behind it. (The fog renderer 0x42E310 blends cells up to 3 apart,
# so a thin unrevealed ridge between two revealed sides still looked visible.)
# The .text cave is full; this code sits in .rdata's zero file padding past
# its VirtualSize (same page, mapped from the file), and .rdata gains
# IMAGE_SCN_MEM_EXECUTE so it also runs where DEP is enforced.
LOS_OFFSET = 0xD0990
LOS_VA = BASE + LOS_OFFSET
LOS_SIZE = 0xD1000 - LOS_OFFSET
RDATA_FLAGS_OFFSET = 0x244
OBSERVER_X_VA = 0x782DE8
OBSERVER_Y_VA = 0x782DEC
LOS_CODE = bytes.fromhex(
    # wrapper: air -> fixed; store observer x, y; restriction = !is_high; jmp 0x42D940
    "508b46048b40048b443020c1e8183c13580f848975ffff50528b44240ca3e82d78008b5424"
    "108915ec2d78006bd213e84675ffff85c00f9405e02d78005a58e96ccff5ff"
    # reveal: max-write unless restricted and (target high or line crosses high)
    "01f23a0a7628803de02d780000741c505289f2e81e75ffff85c0750b8b442404e80e000000"
    "85c05a587503880ac383042411c3"
    # los: eax=x, esi=y*19 -> eax != 0 when a cell strictly between the
    # observer and (x, y) is high; 16.16 steps, n = max(|dx|, |dy|) <= 64
    "5351575589c389f031d2b913000000f7f189c12b1de82d78002b0dec2d780089d89931d029"
    "d089c789c89931d029d039f87e0289c783ff017e5783ff407f5289d8c1e01099f7ff89c389"
    "c8c1e01099f7ff89c18b2de82d7800c1e51081c500800000a1ec2d7800c1e0100500800000"
    "504f01dd010c2489e8c1f8108b1424c1fa106bd213e87a74ffff85c075084f75e15831c0eb"
    "015a5d5f595bc3"
)
LOS_ENTRY = {"wrapper": 0x00, "reveal": 0x44, "los": 0x77}


def _los(name):
    return LOS_VA + LOS_ENTRY[name]


ORIGINAL[LOS_OFFSET] = bytes(LOS_SIZE)
ORIGINAL[RDATA_FLAGS_OFFSET] = bytes.fromhex("40000040")

# v0.034-v0.037: cave wrapper/reveal without line of sight.
V034 = {
    0x2E003: _call(0x2E003, _entry("wrapper")),
    0x2E072: _call(0x2E072, _entry("wrapper")),
    0x2E0CC: _call(0x2E0CC, _entry("fixed")),
    0x2DA18: _call(0x2DA18, _entry("reveal")) + b"\x90" * 3,
    0x276FB: _call(0x276FB, _entry("loader")) + b"\x90",
    0x80A8E: _call(0x80A8E, _entry("setcell")),
    CAVE_OFFSET: CAVE_CODE + bytes(CAVE_SIZE - len(CAVE_CODE)),
}
CURRENT = {
    **V034,
    0x2E003: _call(0x2E003, _los("wrapper")),
    0x2E072: _call(0x2E072, _los("wrapper")),
    0x2DA18: _call(0x2DA18, _los("reveal")) + b"\x90" * 3,
    LOS_OFFSET: LOS_CODE + bytes(LOS_SIZE - len(LOS_CODE)),
    RDATA_FLAGS_OFFSET: bytes.fromhex("40000060"),
}
CAVE_PATCH = CURRENT[CAVE_OFFSET]
CALL_SITES = (0x2E003, 0x2E072, 0x2E0CC)
PATCHED_CALLS = tuple(CURRENT[o] for o in CALL_SITES)
REVEAL_OFFSET = 0x2DA18


def _legacy(loader_entry, reveal_entry, cave):
    """Earlier hooks used 0xF0 bytes at 0x4C7F10 and only four sites."""
    old_cave = 0x4C7F10
    return {
        **ORIGINAL,
        0x2E003: _call(0x2E003, old_cave),
        0x2E072: _call(0x2E072, old_cave),
        0x2DA18: _call(0x2DA18, old_cave + reveal_entry) + b"\x90" * 3,
        0x276FB: _call(0x276FB, old_cave + loader_entry) + b"\x90",
        CAVE_OFFSET: bytes(6) + cave,
    }


def _blocks(*parts):
    data = bytearray(0xF0)
    for offset, hexcode in parts:
        code = bytes.fromhex(hexcode)
        data[offset:offset + len(code)] = code
    return bytes(data)


_OLD_LOADER = "66f7c1200074050d000100006689028b471cc3"
_V027_REVEAL = ("01f23a0a765e803df02f4e0000744b505351525657558b44243c508b44243c50"
                "e87bddfcff83c40866a900010f95c38b44240899b913000000f7f9508b44241c"
                "50e85addfcff83c40866a90001740408db740a5d5f5e5a595b58880ac35d5f5e"
                "5a595b58c704242eda4200c3")
_V027_WRAPPER = ("508b46048b40048b443020c1e8183c130f9505f02f4e0058"
                 "e8135af6ffc605f02f4e0000c3")
LEGACY_V027 = _legacy(0x28, 0x40, _blocks(
    (0, _V027_WRAPPER), (0x28, _OLD_LOADER), (0x40, _V027_REVEAL)))
LEGACY_V026 = _legacy(0x28, 0x40, _blocks(
    (0, _V027_WRAPPER.replace("f02f4e00", "f07f4c00")), (0x28, _OLD_LOADER),
    (0x40, _V027_REVEAL.replace("f02f4e00", "f07f4c00"))))
LEGACY_V032 = _legacy(0x58, 0x70, _blocks(
    (0, "508b46048b40048b443020c1e8183c1374215152ff742414ff742414b8f05c4900"
        "ffd083c40866a900010f9405e02d78005a5958ff74240cff74240cff74240cb840"
        "d94200ffd0c605e02d780000c20c00"),
    (0x58, _OLD_LOADER),
    (0x70, "01f23a0a7633803de02d78000074275051525389c389f031d2b913000000f7f150"
           "53b8f05c4900ffd083c40866a900015b5a59587503880ac3c704242eda4200c3")))
LEGACY_V034 = {**ORIGINAL, **V034}
LEGACIES = (LEGACY_V026, LEGACY_V027, LEGACY_V032, LEGACY_V034)


def _matches(data, layout):
    return all(data[o:o + len(b)] == b for o, b in layout.items())


def _is_original(data):
    return _matches(data, ORIGINAL)


def _state(data):
    """(original, current patch)."""
    return _is_original(data), _matches(data, CURRENT)


def is_legacy(data):
    return any(_matches(data, layout) for layout in LEGACIES)


def _write_layout(data, layout):
    result = bytearray(data)
    for offset, block in layout.items():
        result[offset:offset + len(block)] = block
    return bytes(result)


def normalize(data):
    """Strip the current or a legacy hill hook; leave anything else untouched."""
    if _matches(data, CURRENT) or is_legacy(data):
        return _write_layout(data, ORIGINAL)
    return data


def patched_bytes(data):
    if _matches(data, CURRENT):
        return data
    if is_legacy(data):
        data = _write_layout(data, ORIGINAL)
    if not _is_original(data):
        raise ValueError("지원되지 않는 시야 코드입니다. 실행파일을 변경하지 않았습니다.")
    return _write_layout(data, CURRENT)


def _write(path, data, result, backup_name):
    backup = path.with_name(backup_name)
    if not backup.exists():
        backup.write_bytes(data)
    with path.open("r+b") as stream:
        stream.write(result)
        stream.truncate()
        stream.flush()
        os.fsync(stream.fileno())


def apply(game_dir):
    """Install the hill hook (replacing any legacy one). True if changed."""
    path = Path(game_dir) / "iop.exe"
    data = path.read_bytes()
    result = patched_bytes(data)
    if result == data:
        return False
    _write(path, data, result, "iop.exe.before-hill-vision.bak")
    return True


def remove(game_dir):
    """Remove any hill hook while preserving all unrelated patches."""
    path = Path(game_dir) / "iop.exe"
    data = path.read_bytes()
    if _is_original(data):
        return False
    if not (_matches(data, CURRENT) or is_legacy(data)):
        raise ValueError("고지대 시야 패치 상태를 확인할 수 없습니다. iop.exe를 복구하세요.")
    _write(path, data, _write_layout(data, ORIGINAL), "iop.exe.before-hill-vision-removal.bak")
    return True
