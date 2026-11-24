"""Replay files: raw game recordings -> .ioprep library files -> playback input.

Raw recording (written by the game, see tools/replay_patch.asm):
    0x000 'IOPR', u32 version, u32 mode, u32 rand_pre, u32 rand_post,
          u32 local DirectPlay id, u32 ticks/second, u32 local index, u32 start tick
    0x040 setup block 0x6C92B0..0x6C9460 (0x1B0 bytes)
    0x200 records: 01 tick rand hash | 02 player packet[0x6F] | 03 player a1 a2 tick | 04 tick

Library file (.ioprep):
    b'IOPREP1\\0', u32 meta length, UTF-8 JSON meta,
    u32 length + zlib(raw), u32 length + zlib(map file)   (map may be empty)
"""
from __future__ import annotations

from dataclasses import dataclass, field
import datetime as _dt
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import struct
import zlib

EXTENSION = ".ioprep"
CONTAINER_MAGIC = b"IOPREP1\0"
RAW_MAGIC = b"IOPR"
RAW_VERSION = 1
HEADER_SIZE = 0x200
BLOCK_OFFSET = 0x40
BLOCK_LEN = 0x1B0
PACKET_LEN = 0x6F
RECORD_SIZES = {1: 13, 2: 2 + PACKET_LEN, 3: 14, 4: 5}
REPLAY_DIR = "Replays"
AUTO_DIR = "Auto"
LAST_REPLAY = "LastReplay" + EXTENSION
PLAY_RAW = "_play.iopraw"
RAW_PATTERN = "_rec_*.iopraw"
MAP_PATH_LIMIT = 0x47             # 0x6C9419..0x6C9460
EMBEDDED_MAP_PREFIX = "_replay_"
TYPE_NAMES = {1: "컴퓨터", 4: "사람", 6: "관전"}
MODE_NAMES = {1: "싱글(컴퓨터 대전)", 2: "멀티플레이"}
SPEED_STEPS = (0.25, 0.5, 1.0, 1.5, 2.0, 4.0, 8.0, 16.0)


# setup block layout (offsets from 0x6C92B0)
B_COUNT, B_SEED, B_IDS, B_TYPES = 0x00, 0x01, 0x05, 0x25
B_TEAMS, B_RACES, B_NAMES, B_TEAMIDX, B_MAP = 0x4D, 0x8D, 0x95, 0x13D, 0x169
NAME_LEN = 21


class ReplayError(ValueError):
    pass


def _cstr(data: bytes) -> str:
    raw = data.split(b"\0", 1)[0]
    try:
        return raw.decode("cp949").strip()
    except UnicodeDecodeError:
        return raw.decode("latin-1", "replace").strip()


@dataclass
class RawReplay:
    data: bytes
    mode: int
    rand_pre: int
    rand_post: int
    local_id: int
    tps: int
    local_index: int
    start_tick: int
    end_tick: int
    commands: int
    leaves: int
    complete: bool
    valid_length: int
    players: list = field(default_factory=list)
    map_path: str = ""

    @property
    def block(self) -> bytes:
        return self.data[BLOCK_OFFSET:BLOCK_OFFSET + BLOCK_LEN]

    @property
    def duration_seconds(self) -> float:
        return max(0, self.end_tick - self.start_tick) / max(1, self.tps)


def parse_players(block: bytes, mode: int = 2) -> list[dict]:
    """Players in game order. "index" is the in-game player number (team manager slot):
    the builders number only the slots they create (skirmish: human/computer, network: + observers)."""
    valid = (1, 4, 6) if mode == 2 else (1, 4)
    players = []
    for slot in range(8):
        kind = block[B_TYPES + slot]
        if kind not in valid:
            continue
        name = _cstr(block[B_NAMES + slot * NAME_LEN:B_NAMES + (slot + 1) * NAME_LEN])
        if kind == 1 and not name:
            name = f"컴퓨터 {slot + 1}"
        players.append({
            "index": len(players),
            "slot": slot,
            "name": name or f"플레이어 {slot + 1}",
            "type": kind,
            "kind": TYPE_NAMES[kind],
            "race": block[B_RACES + slot],
            "team": struct.unpack_from("<I", block, B_TEAMIDX + slot * 4)[0],
        })
    return players


def parse_raw(data: bytes) -> RawReplay:
    if len(data) < HEADER_SIZE or data[:4] != RAW_MAGIC:
        raise ReplayError("리플레이 기록 형식이 아닙니다.")
    version, mode, rand_pre, rand_post, local_id, tps, local_index, start_tick = struct.unpack_from("<8I", data, 4)
    if version != RAW_VERSION:
        raise ReplayError(f"지원하지 않는 리플레이 기록 버전입니다: {version}")
    if mode not in (1, 2):
        raise ReplayError(f"지원하지 않는 경기 방식입니다: {mode}")
    pos, end_tick, commands, leaves, complete = HEADER_SIZE, start_tick, 0, 0, False
    while pos < len(data):
        kind = data[pos]
        size = RECORD_SIZES.get(kind)
        if size is None or pos + size > len(data):
            break
        if kind == 1:
            end_tick = struct.unpack_from("<I", data, pos + 1)[0]
        elif kind == 2:
            commands += 1
        elif kind == 3:
            leaves += 1
        else:
            complete = True
            pos += size
            break
        pos += size
    block = data[BLOCK_OFFSET:BLOCK_OFFSET + BLOCK_LEN]
    return RawReplay(data=data, mode=mode, rand_pre=rand_pre, rand_post=rand_post, local_id=local_id,
                     tps=tps or 15, local_index=local_index, start_tick=start_tick, end_tick=end_tick,
                     commands=commands, leaves=leaves, complete=complete, valid_length=pos,
                     players=parse_players(block, mode), map_path=_cstr(block[B_MAP:B_MAP + MAP_PATH_LIMIT]))


def with_map_path(raw: bytes, path: str) -> bytes:
    encoded = path.encode("cp949")
    if len(encoded) >= MAP_PATH_LIMIT:
        raise ReplayError("맵 경로가 너무 깁니다.")
    data = bytearray(raw)
    start = BLOCK_OFFSET + B_MAP
    data[start:start + MAP_PATH_LIMIT] = encoded.ljust(MAP_PATH_LIMIT, b"\0")
    return bytes(data)


# ---------------------------------------------------------------- container
def pack(meta: dict, raw: bytes, map_bytes: bytes = b"") -> bytes:
    body = json.dumps(meta, ensure_ascii=False, sort_keys=True).encode("utf-8")
    parts = [CONTAINER_MAGIC, struct.pack("<I", len(body)), body]
    for blob in (raw, map_bytes):
        compressed = zlib.compress(blob, 9) if blob else b""
        parts += [struct.pack("<I", len(compressed)), compressed]
    return b"".join(parts)


def unpack(data: bytes) -> tuple[dict, bytes, bytes]:
    if not data.startswith(CONTAINER_MAGIC):
        raise ReplayError("IOP 리플레이 파일(.ioprep)이 아닙니다.")
    try:
        pos = len(CONTAINER_MAGIC)
        size = struct.unpack_from("<I", data, pos)[0]; pos += 4
        meta = json.loads(data[pos:pos + size].decode("utf-8")); pos += size
        blobs = []
        for _ in range(2):
            size = struct.unpack_from("<I", data, pos)[0]; pos += 4
            chunk = data[pos:pos + size]; pos += size
            if len(chunk) != size:
                raise ReplayError("리플레이 파일이 잘렸습니다.")
            blobs.append(zlib.decompress(chunk) if size else b"")
    except (struct.error, zlib.error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReplayError(f"리플레이 파일이 손상되었습니다: {exc}") from exc
    raw, map_bytes = blobs
    if meta.get("raw_sha256") and hashlib.sha256(raw).hexdigest() != meta["raw_sha256"]:
        raise ReplayError("리플레이 데이터 검증(SHA-256)에 실패했습니다.")
    return meta, raw, map_bytes


def read_meta(path) -> dict:
    """Metadata only (fast enough for the list: decompression is skipped)."""
    with open(path, "rb") as stream:
        head = stream.read(len(CONTAINER_MAGIC) + 4)
        if len(head) < 12 or not head.startswith(CONTAINER_MAGIC):
            raise ReplayError("IOP 리플레이 파일이 아닙니다.")
        size = struct.unpack_from("<I", head, len(CONTAINER_MAGIC))[0]
        if size > 1 << 20:
            raise ReplayError("리플레이 정보가 너무 큽니다.")
        return json.loads(stream.read(size).decode("utf-8"))


# ---------------------------------------------------------------- environment fingerprints
def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def resolve_game_path(game_dir, relative: str) -> Path:
    parts = PureWindowsPath(relative).parts
    if not parts or PureWindowsPath(relative).is_absolute() or ".." in parts:
        raise ReplayError("맵 경로가 올바르지 않습니다.")
    return Path(game_dir).joinpath(*parts)


def environment(game_dir) -> dict:
    """Simulation-relevant fingerprints compared when a replay is played back."""
    root = Path(game_dir)
    data = {}
    folder = root / "data"
    if folder.is_dir():
        for path in sorted(folder.glob("*")):
            if path.is_file() and path.suffix.lower() == ".res":
                data[path.name.lower()] = _sha(path.read_bytes())
    result = {"data": data}
    try:
        from iop_assets import normalized_iop
        result["iop_exe"] = _sha(normalized_iop((root / "iop.exe").read_bytes()))
    except (OSError, ValueError, ImportError):
        result["iop_exe"] = ""
    try:
        from iop_hill_vision import FEATURE_VERSION as hill
    except ImportError:
        hill = ""
    from iop_replay_patch import FEATURE_VERSION as replay
    result["features"] = {"hill_vision": hill, "replay": replay}
    return result


def compare_environment(recorded: dict, current: dict) -> list[str]:
    problems = []
    if not recorded:
        return problems
    if recorded.get("iop_exe") and current.get("iop_exe") and recorded["iop_exe"] != current["iop_exe"]:
        problems.append("게임 실행 파일(iop.exe)이 기록 당시와 다릅니다.")
    changed = sorted(name for name, digest in recorded.get("data", {}).items()
                     if current.get("data", {}).get(name) not in (None, digest))
    missing = sorted(set(recorded.get("data", {})) - set(current.get("data", {})))
    if changed:
        problems.append("밸런스·게임 데이터가 기록 당시와 다릅니다: " + ", ".join(changed[:6]) + (" …" if len(changed) > 6 else ""))
    if missing:
        problems.append("게임 데이터 파일이 없습니다: " + ", ".join(missing[:6]))
    for name, version in recorded.get("features", {}).items():
        now = current.get("features", {}).get(name)
        if version and now and version != now:
            label = {"hill_vision": "고지대 시야 규칙", "replay": "리플레이 형식"}.get(name, name)
            problems.append(f"{label} 버전이 기록 당시({version})와 현재({now})가 다릅니다.")
    return problems


# ---------------------------------------------------------------- naming / library
def _safe(text: str, limit: int = 24) -> str:
    text = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "", text).strip().strip(".")
    return text[:limit] or "기록"


def describe_players(players: list[dict]) -> str:
    fighters = [p for p in players if p["type"] != 6]
    teams = {}
    for p in fighters:
        teams.setdefault(p["team"], []).append(p["name"])
    groups = [", ".join(names) for _, names in sorted(teams.items())]
    if len(groups) > 1 and len(fighters) == len(teams):
        return " vs ".join(p["name"] for p in fighters)
    return " vs ".join(groups) if len(groups) > 1 else ", ".join(p["name"] for p in fighters)


def default_name(meta: dict) -> str:
    when = meta.get("recorded_at", "")[:16].replace("T", " ").replace(":", "")
    map_name = Path(PureWindowsPath(meta.get("map_path", "")).name).stem
    who = describe_players(meta.get("players", []))
    return _safe(f"{when} {_safe(map_name, 20)} {who}", 80)


def build_meta(raw: RawReplay, game_dir, launcher_version: str, recorded_at: str | None = None,
               map_bytes: bytes = b"") -> dict:
    return {
        "format": 1,
        "launcher_version": launcher_version,
        "recorded_at": recorded_at or _dt.datetime.now().isoformat(timespec="seconds"),
        "mode": raw.mode,
        "mode_name": MODE_NAMES.get(raw.mode, str(raw.mode)),
        "map_path": raw.map_path,
        "map_name": Path(PureWindowsPath(raw.map_path).name).stem,
        "map_sha256": _sha(map_bytes) if map_bytes else "",
        "players": raw.players,
        "local_index": raw.local_index,
        "tps": raw.tps,
        "start_tick": raw.start_tick,
        "end_tick": raw.end_tick,
        "duration_seconds": round(raw.duration_seconds, 1),
        "commands": raw.commands,
        "complete": raw.complete,
        "raw_sha256": _sha(raw.data[:raw.valid_length]),
        "environment": environment(game_dir),
    }


def replay_dir(game_dir) -> Path:
    path = Path(game_dir) / REPLAY_DIR
    path.mkdir(exist_ok=True)
    return path


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(2, 10000):
        candidate = path.with_name(f"{path.stem} ({index}){path.suffix}")
        if not candidate.exists():
            return candidate
    raise ReplayError("저장할 파일 이름을 만들 수 없습니다.")


def finalize_pending(game_dir, launcher_version: str, keep_auto: int = 50, save_auto: bool = True) -> list[Path]:
    """Turn finished raw recordings into library files. Files still being written are skipped."""
    root = replay_dir(game_dir)
    produced = []
    for path in sorted(root.glob(RAW_PATTERN), key=lambda p: p.stat().st_mtime):
        claimed = path.with_suffix(".claim")
        try:
            os.replace(path, claimed)          # fails while the game holds the file open
        except OSError:
            continue
        try:
            data = claimed.read_bytes()
            try:
                raw = parse_raw(data)
            except ReplayError:
                claimed.unlink(missing_ok=True)
                continue
            if raw.end_tick <= raw.start_tick and raw.commands == 0:
                claimed.unlink(missing_ok=True)   # left the battle at once
                continue
            stamp = _dt.datetime.fromtimestamp(claimed.stat().st_mtime).isoformat(timespec="seconds")
            map_bytes = b""
            try:
                map_bytes = resolve_game_path(game_dir, raw.map_path).read_bytes()
            except (OSError, ReplayError):
                pass
            trimmed = data[:raw.valid_length]
            meta = build_meta(parse_raw(trimmed), game_dir, launcher_version, stamp, map_bytes)
            blob = pack(meta, trimmed, map_bytes)
            last = root / LAST_REPLAY
            stage = last.with_suffix(".tmp")
            stage.write_bytes(blob)
            os.replace(stage, last)
            produced.append(last)
            if save_auto:
                auto = root / AUTO_DIR
                auto.mkdir(exist_ok=True)
                target = _unique(auto / (default_name(meta) + EXTENSION))
                target.write_bytes(blob)
                produced.append(target)
            claimed.unlink(missing_ok=True)
        except OSError:
            try:
                os.replace(claimed, path)
            except OSError:
                pass
            continue
    prune_auto(game_dir, keep_auto)
    return produced


def prune_auto(game_dir, keep: int) -> int:
    folder = Path(game_dir) / REPLAY_DIR / AUTO_DIR
    if keep <= 0 or not folder.is_dir():
        return 0
    files = sorted(folder.glob("*" + EXTENSION), key=lambda p: p.stat().st_mtime, reverse=True)
    removed = 0
    for path in files[keep:]:
        try:
            path.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def list_replays(game_dir) -> list[tuple[Path, dict | None, str]]:
    root = Path(game_dir) / REPLAY_DIR
    if not root.is_dir():
        return []
    entries = []
    for path in root.rglob("*" + EXTENSION):
        try:
            entries.append((path, read_meta(path), ""))
        except (OSError, ValueError) as exc:
            entries.append((path, None, str(exc)))
    entries.sort(key=lambda e: e[0].stat().st_mtime if e[0].exists() else 0, reverse=True)
    return entries


def format_duration(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


# ---------------------------------------------------------------- playback preparation
@dataclass
class Playback:
    exe: Path
    meta: dict
    raw: RawReplay
    warnings: list
    temp_map: Path | None


def prepare_playback(game_dir, replay_path) -> Playback:
    """Write Replays\\_play.iopraw (+ the embedded map if needed) and the playback exe."""
    from iop_replay_patch import make_play_exe
    meta, raw_bytes, map_bytes = unpack(Path(replay_path).read_bytes())
    raw = parse_raw(raw_bytes)
    warnings = compare_environment(meta.get("environment", {}), environment(game_dir))
    if not raw.complete:
        warnings.append("게임이 비정상 종료되어 기록이 중간에서 끝납니다. 마지막 부분까지만 재생됩니다.")
    temp_map = None
    target = None
    try:
        target = resolve_game_path(game_dir, raw.map_path)
    except ReplayError:
        pass
    have = target.read_bytes() if target and target.is_file() else None
    if have is None or (map_bytes and _sha(have) != _sha(map_bytes)):
        if not map_bytes:
            raise ReplayError(f"맵 파일이 없습니다: {raw.map_path}")
        folder = PureWindowsPath(raw.map_path).parent if raw.map_path else PureWindowsPath("map", "multi")
        name = f"{EMBEDDED_MAP_PREFIX}{_sha(map_bytes)[:10]}{PureWindowsPath(raw.map_path).suffix or '.map'}"
        relative = str(folder / name)
        if len(relative.encode("cp949")) >= MAP_PATH_LIMIT:
            relative = str(PureWindowsPath("map", "multi", name))
        temp_map = resolve_game_path(game_dir, relative)
        temp_map.parent.mkdir(parents=True, exist_ok=True)
        temp_map.write_bytes(map_bytes)
        raw_bytes = with_map_path(raw_bytes, relative)
        if have is not None:
            warnings.append("설치된 맵이 기록 당시와 달라 리플레이에 포함된 맵으로 재생합니다.")
    stage = replay_dir(game_dir) / (PLAY_RAW + ".tmp")
    stage.write_bytes(raw_bytes)
    os.replace(stage, replay_dir(game_dir) / PLAY_RAW)
    exe = make_play_exe(game_dir)
    return Playback(exe=exe, meta=meta, raw=raw, warnings=warnings, temp_map=temp_map)


def cleanup_playback(game_dir, playback: Playback | None = None) -> None:
    for path in (replay_dir(game_dir) / PLAY_RAW,):
        try:
            path.unlink()
        except OSError:
            pass
    if playback and playback.temp_map:
        try:
            playback.temp_map.unlink()
        except OSError:
            pass


def import_replay(game_dir, source) -> Path:
    data = Path(source).read_bytes()
    unpack(data)
    target = _unique(replay_dir(game_dir) / Path(source).name)
    target.write_bytes(data)
    return target
