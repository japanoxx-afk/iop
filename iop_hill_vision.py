"""Terrain-aware fog of war for maps carrying the custom 0x20 high-ground flag."""
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
# Reserved zero padding at the end of the writable .data raw block.
FLAG_VA = 0x4E2FF0

WRAPPER = bytes.fromhex(
    "508b46048b40048b443020c1e8183c130f9505f02f4e0058"
    "e8135af6ffc605f02f4e0000c3"
)
LOADER = bytes.fromhex("66f7c1200074050d000100006689028b471cc3")
REVEAL = bytes.fromhex(
    "01f23a0a765e803df02f4e0000744b505351525657558b44243c508b44243c50"
    "e87bddfcff83c40866a900010f95c38b44240899b913000000f7f9508b44241c"
    "50e85addfcff83c40866a90001740408db740a5d5f5e5a595b58880ac35d5f5e5a595b58c7"
    "04242eda4200c3"
)


def _call(source_va, target_va):
    return b"\xe8" + struct.pack("<i", target_va - (source_va + 5))


PATCHED_CALLS = tuple(_call(0x42E003 if i == 0 else 0x42E072, CAVE_VA)
                      for i in range(2))
LOADER_PATCH = _call(0x4276FB, CAVE_VA + 0x28) + b"\x90"
REVEAL_PATCH = _call(0x42DA18, CAVE_VA + 0x40) + b"\x90" * 3


def _cave():
    data = bytearray(CAVE_SIZE)
    data[:len(WRAPPER)] = WRAPPER
    data[0x28:0x28 + len(LOADER)] = LOADER
    data[0x40:0x40 + len(REVEAL)] = REVEAL
    return bytes(data)


CAVE_PATCH = _cave()


def _state(data):
    original = (tuple(data[o:o+5] for o in CALL_SITES) == ORIGINAL_CALLS
                and data[LOADER_OFFSET:LOADER_OFFSET+6] == LOADER_ORIGINAL
                and data[REVEAL_OFFSET:REVEAL_OFFSET+8] == REVEAL_ORIGINAL
                and not any(data[CAVE_OFFSET:CAVE_OFFSET+CAVE_SIZE]))
    patched = (tuple(data[o:o+5] for o in CALL_SITES) == PATCHED_CALLS
               and data[LOADER_OFFSET:LOADER_OFFSET+6] == LOADER_PATCH
               and data[REVEAL_OFFSET:REVEAL_OFFSET+8] == REVEAL_PATCH
               and data[CAVE_OFFSET:CAVE_OFFSET+CAVE_SIZE] == CAVE_PATCH)
    return original, patched


def normalize(data):
    original, patched = _state(data)
    if original:
        return data
    if not patched:
        return data
    result = bytearray(data)
    for offset, block in zip(CALL_SITES, ORIGINAL_CALLS):
        result[offset:offset+5] = block
    result[LOADER_OFFSET:LOADER_OFFSET+6] = LOADER_ORIGINAL
    result[REVEAL_OFFSET:REVEAL_OFFSET+8] = REVEAL_ORIGINAL
    result[CAVE_OFFSET:CAVE_OFFSET+CAVE_SIZE] = b"\0" * CAVE_SIZE
    return bytes(result)


def patched_bytes(data):
    original, patched = _state(data)
    if patched:
        return data
    if not original:
        raise ValueError("지원되지 않는 시야 코드입니다. 실행파일을 변경하지 않았습니다.")
    result = bytearray(data)
    for offset, block in zip(CALL_SITES, PATCHED_CALLS):
        result[offset:offset+5] = block
    result[LOADER_OFFSET:LOADER_OFFSET+6] = LOADER_PATCH
    result[REVEAL_OFFSET:REVEAL_OFFSET+8] = REVEAL_PATCH
    result[CAVE_OFFSET:CAVE_OFFSET+CAVE_SIZE] = CAVE_PATCH
    return bytes(result)


def apply(game_dir):
    path = Path(game_dir) / "iop.exe"
    data = path.read_bytes()
    result = patched_bytes(data)
    if result == data:
        return False
    backup = path.with_name("iop.exe.before-hill-vision.bak")
    if not backup.exists():
        backup.write_bytes(data)
    with path.open("r+b") as stream:
        stream.write(result)
        stream.truncate()
        stream.flush()
        os.fsync(stream.fileno())
    return True


def remove(game_dir):
    """Remove this experimental hook while preserving all unrelated patches."""
    path = Path(game_dir) / "iop.exe"
    data = path.read_bytes()
    original, patched = _state(data)
    if original:
        return False
    if not patched:
        raise ValueError("고지대 시야 패치 상태를 확인할 수 없습니다. iop.exe를 복구하세요.")
    result = normalize(data)
    backup = path.with_name("iop.exe.before-hill-vision-removal.bak")
    if not backup.exists():
        backup.write_bytes(data)
    with path.open("r+b") as stream:
        stream.write(result)
        stream.truncate()
        stream.flush()
        os.fsync(stream.fileno())
    return True
