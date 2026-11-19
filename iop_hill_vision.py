"""Terrain-aware fog of war for maps carrying the custom 0x20 high-ground flag.

Rule: a ground unit or building standing on low ground does not reveal tiles
marked high ground. Observers on high ground and air units (object category
0x13) keep their normal sight. Fixed reveal points (0x42E0CC) are unchanged.

Hook points in the original v0.019 iop.exe:
- 0x4276FB  map loader: tile attribute bit 0x20 -> runtime attribute 0x100.
            Every game reader of the tile grid masks with 0x0F / 0x70, so the
            high byte is invisible to pathing and building checks.
- 0x42E003 / 0x42E072  unit and building calls into fog reveal 0x42D940
            (args: tile x, tile y, sight index 0..5; callee pops 12 bytes).
            The wrapper re-pushes the three arguments; the v0.026-v0.029 hook
            called through without doing so, shifting tile y into the sight
            index and tripping the game's BBSIM ERROR #847234 (0xCED82) check.
- 0x42DA18  per-cell reveal write inside 0x42D940.

The per-call state byte lives past .data's VirtualSize (0x782DD8) inside the
same zero-filled, writable page, so no game variable can share it.
"""
from pathlib import Path
import os
import struct

# File offsets are equal to RVA offsets in this PE's .text section.
CALL_SITES = (0x2E003, 0x2E072)
ORIGINAL_CALLS = (bytes.fromhex("e838f9ffff"), bytes.fromhex("e8c9f8ffff"))
LOADER_OFFSET = 0x276FB
LOADER_ORIGINAL = bytes.fromhex("6689028b471c")
REVEAL_OFFSET = 0x2DA18
REVEAL_ORIGINAL = bytes.fromhex("03d63a0a7610880a")
CAVE_OFFSET = 0xC7F10
CAVE_SIZE = 0xF0
CAVE_VA = 0x4C7F10
STATE_VA = 0x782DE0
# Compared between A and B launchers; bump when the fog rule changes.
FEATURE_VERSION = "2"
FOG_REVEAL_VA = 0x42D940
TILE_ATTR_VA = 0x495CF0
LOADER_ENTRY = 0x58
REVEAL_ENTRY = 0x70

# push eax / air check / observer tile lookup / re-push args / call / clear / ret 0xc
WRAPPER = bytes.fromhex(
    "508b46048b40048b443020c1e8183c1374215152ff742414ff742414b8f05c4900"
    "ffd083c40866a900010f9405e02d78005a5958ff74240cff74240cff74240cb840"
    "d94200ffd0c605e02d780000c20c00"
)
LOADER = bytes.fromhex("66f7c1200074050d000100006689028b471cc3")
REVEAL = bytes.fromhex(
    "01f23a0a7633803de02d78000074275051525389c389f031d2b913000000f7f150"
    "53b8f05c4900ffd083c40866a900015b5a59587503880ac3c704242eda4200c3"
)


def _call(source_va, target_va):
    return b"\xe8" + struct.pack("<i", target_va - (source_va + 5))


def _cave(blocks):
    data = bytearray(CAVE_SIZE)
    end = 0
    for offset, block in sorted(blocks):
        if offset < end or offset + len(block) > CAVE_SIZE:
            raise AssertionError("hill vision code blocks overlap")
        end = offset + len(block)
        data[offset:offset + len(block)] = block
    return bytes(data)


def _layout(loader_entry, reveal_entry, cave):
    return {
        "calls": tuple(_call(0x400000 + site, CAVE_VA) for site in CALL_SITES),
        "loader": _call(0x400000 + LOADER_OFFSET, CAVE_VA + loader_entry) + b"\x90",
        "reveal": _call(0x400000 + REVEAL_OFFSET, CAVE_VA + reveal_entry) + b"\x90" * 3,
        "cave": cave,
    }


CAVE_PATCH = _cave(((0, WRAPPER), (LOADER_ENTRY, LOADER), (REVEAL_ENTRY, REVEAL)))
CURRENT = _layout(LOADER_ENTRY, REVEAL_ENTRY, CAVE_PATCH)
PATCHED_CALLS = CURRENT["calls"]
LOADER_PATCH = CURRENT["loader"]
REVEAL_PATCH = CURRENT["reveal"]

# Broken v0.026-v0.029 hook, recognised only so it can be removed.
LEGACY = _layout(0x28, 0x40, _cave((
    (0, bytes.fromhex("508b46048b40048b443020c1e8183c130f9505f02f4e0058"
                      "e8135af6ffc605f02f4e0000c3")),
    (0x28, LOADER),
    (0x40, bytes.fromhex(
        "01f23a0a765e803df02f4e0000744b505351525657558b44243c508b44243c50"
        "e87bddfcff83c40866a900010f95c38b44240899b913000000f7f9508b44241c"
        "50e85addfcff83c40866a90001740408db740a5d5f5e5a595b58880ac35d5f5e5a595b58c7"
        "04242eda4200c3")),
)))
# v0.026 was the same code with its flag at 0x4C7FF0 (inside read-only .text).
LEGACY_V026 = dict(LEGACY, cave=LEGACY["cave"].replace(bytes.fromhex("f02f4e00"),
                                                        bytes.fromhex("f07f4c00")))
LEGACIES = (LEGACY, LEGACY_V026)


def _matches(data, layout):
    return (tuple(data[o:o + 5] for o in CALL_SITES) == layout["calls"]
            and data[LOADER_OFFSET:LOADER_OFFSET + 6] == layout["loader"]
            and data[REVEAL_OFFSET:REVEAL_OFFSET + 8] == layout["reveal"]
            and data[CAVE_OFFSET:CAVE_OFFSET + CAVE_SIZE] == layout["cave"])


def _is_original(data):
    return (tuple(data[o:o + 5] for o in CALL_SITES) == ORIGINAL_CALLS
            and data[LOADER_OFFSET:LOADER_OFFSET + 6] == LOADER_ORIGINAL
            and data[REVEAL_OFFSET:REVEAL_OFFSET + 8] == REVEAL_ORIGINAL
            and not any(data[CAVE_OFFSET:CAVE_OFFSET + CAVE_SIZE]))


def _state(data):
    """(original, current patch) for callers that predate the legacy split."""
    return _is_original(data), _matches(data, CURRENT)


def is_legacy(data):
    return any(_matches(data, layout) for layout in LEGACIES)


def _restore(data):
    result = bytearray(data)
    for offset, block in zip(CALL_SITES, ORIGINAL_CALLS):
        result[offset:offset + 5] = block
    result[LOADER_OFFSET:LOADER_OFFSET + 6] = LOADER_ORIGINAL
    result[REVEAL_OFFSET:REVEAL_OFFSET + 8] = REVEAL_ORIGINAL
    result[CAVE_OFFSET:CAVE_OFFSET + CAVE_SIZE] = b"\0" * CAVE_SIZE
    return bytes(result)


def normalize(data):
    """Strip the current or a legacy hill hook; leave anything else untouched."""
    if _matches(data, CURRENT) or is_legacy(data):
        return _restore(data)
    return data


def patched_bytes(data):
    if _matches(data, CURRENT):
        return data
    if is_legacy(data):
        data = _restore(data)
    if not _is_original(data):
        raise ValueError("지원되지 않는 시야 코드입니다. 실행파일을 변경하지 않았습니다.")
    result = bytearray(data)
    for offset, block in zip(CALL_SITES, PATCHED_CALLS):
        result[offset:offset + 5] = block
    result[LOADER_OFFSET:LOADER_OFFSET + 6] = LOADER_PATCH
    result[REVEAL_OFFSET:REVEAL_OFFSET + 8] = REVEAL_PATCH
    result[CAVE_OFFSET:CAVE_OFFSET + CAVE_SIZE] = CAVE_PATCH
    return bytes(result)


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
    """Install the hill hook (replacing a broken legacy one). True if changed."""
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
    _write(path, data, _restore(data), "iop.exe.before-hill-vision-removal.bak")
    return True
