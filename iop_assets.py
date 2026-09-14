"""Hash-verified game compatibility assets published by the launcher repository."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import time
import urllib.request
import uuid

MANIFEST_URL = "https://raw.githubusercontent.com/japanoxx-afk/iop/main/game-assets/manifest.json"
ALLOWED_PREFIX = "https://raw.githubusercontent.com/japanoxx-afk/iop/"
MAX_FILE = 10 * 1024 * 1024
CANONICAL_IOP_SHA256 = "489f2217dc13e9b7b8b3fee97bd78843e70a2166f4ce50efc429c5cb1ec86028"


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _read_response(opener, url, limit):
    request = urllib.request.Request(url, headers={"User-Agent": "IOPLauncher"})
    with opener(request, timeout=30) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("다운로드 파일이 허용 크기를 초과했습니다.")
    return data


def fetch_manifest(opener=urllib.request.urlopen):
    data = json.loads(_read_response(opener, MANIFEST_URL, 64 * 1024).decode("utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("게임 파일 배포 정보 형식이 올바르지 않습니다.")
    files = data.get("files")
    if data.get("protocol") != 1 or not isinstance(files, dict):
        raise ValueError("게임 파일 배포 정보 형식이 올바르지 않습니다.")
    for name in ("iop.exe", "ddraw.dll", "ddraw.ini"):
        item = files.get(name)
        if not isinstance(item, dict) or set(("url", "size", "sha256")) - set(item):
            raise ValueError(f"게임 파일 배포 정보가 없습니다: {name}")
        if not str(item["url"]).startswith(ALLOWED_PREFIX):
            raise ValueError(f"허용되지 않은 게임 파일 주소입니다: {name}")
        size = int(item["size"])
        digest = str(item["sha256"]).lower()
        if not 1 <= size <= MAX_FILE or len(digest) != 64:
            raise ValueError(f"게임 파일 크기 또는 해시가 올바르지 않습니다: {name}")
        int(digest, 16)
        item["size"], item["sha256"] = size, digest
    if files["iop.exe"]["sha256"] != CANONICAL_IOP_SHA256:
        raise ValueError("기준 iop.exe 해시가 런처와 일치하지 않습니다.")
    return data


def _download(item, opener=urllib.request.urlopen):
    data = _read_response(opener, item["url"], item["size"])
    if len(data) != item["size"] or _sha(data) != item["sha256"]:
        raise ValueError("GitHub 게임 파일의 크기 또는 SHA-256 검증에 실패했습니다.")
    return data


def normalized_iop(data):
    from iop_hotkeys import normalize_exe
    from iop_stability import normalize
    from iop_network import ADDRESS_OFFSET, ORIGINAL_ADDRESS
    base = normalize(normalize_exe(data))
    return base[:ADDRESS_OFFSET] + ORIGINAL_ADDRESS + base[ADDRESS_OFFSET + 16:]


def iop_is_canonical(path):
    try:
        return _sha(normalized_iop(Path(path).read_bytes())) == CANONICAL_IOP_SHA256
    except (OSError, ValueError):
        return False


def _replace(path, data, backup_label):
    path = Path(path)
    # Windows denies this open while iop.exe is loaded. Check before staging anything.
    if path.exists():
        with path.open("r+b"):
            pass
    stage = path.with_name(path.name + ".new-" + uuid.uuid4().hex)
    try:
        with stage.open("xb") as output:
            output.write(data); output.flush(); os.fsync(output.fileno())
        if path.exists():
            backup = path.with_name(path.name + f".{backup_label}-{time.time_ns()}.bak")
            shutil.copy2(path, backup)
        os.replace(stage, path)
    finally:
        stage.unlink(missing_ok=True)


def ensure_iop(game_dir, manifest=None, opener=urllib.request.urlopen):
    path = Path(game_dir) / "iop.exe"
    if iop_is_canonical(path):
        return False
    manifest = manifest or fetch_manifest(opener)
    data = _download(manifest["files"]["iop.exe"], opener)
    if _sha(normalized_iop(data)) != CANONICAL_IOP_SHA256:
        raise ValueError("내려받은 iop.exe가 지원 기준과 일치하지 않습니다.")
    _replace(path, data, "before-github-sync")
    return True


def ensure_graphics(game_dir, manifest=None, opener=urllib.request.urlopen):
    root = Path(game_dir)
    wanted = ("ddraw.dll", "ddraw.ini")
    missing = [name for name in wanted if not (root / name).is_file()]
    if not missing:
        return []
    manifest = manifest or fetch_manifest(opener)
    installed = []
    for name in missing:
        data = _download(manifest["files"][name], opener)
        _replace(root / name, data, "before-graphics-update")
        installed.append(name)
    return installed


def ensure_game_files(game_dir, opener=urllib.request.urlopen):
    root = Path(game_dir)
    need_iop = not iop_is_canonical(root / "iop.exe")
    need_graphics = any(not (root / name).is_file() for name in ("ddraw.dll", "ddraw.ini"))
    manifest = fetch_manifest(opener) if need_iop or need_graphics else None
    return {
        "iop_restored": ensure_iop(root, manifest, opener) if need_iop else False,
        "graphics_installed": ensure_graphics(root, manifest, opener) if need_graphics else [],
    }
