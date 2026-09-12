"""Bounded, hash-verified self-update from the project's GitHub repository."""
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import urllib.request
import uuid

MANIFEST_URL = "https://raw.githubusercontent.com/japanoxx-afk/iop/main/update-manifest.json"
ALLOWED_PREFIX = "https://raw.githubusercontent.com/japanoxx-afk/iop/"
MAX_DOWNLOAD = 50 * 1024 * 1024


def version_tuple(value):
    try:
        parts = str(value).strip().lstrip("v").split(".")
        if not 1 <= len(parts) <= 4:
            raise ValueError
        return tuple(int(part) for part in parts)
    except (TypeError, ValueError):
        raise ValueError("업데이트 버전 형식이 올바르지 않습니다.")


def fetch_manifest(opener=urllib.request.urlopen):
    request = urllib.request.Request(MANIFEST_URL, headers={"User-Agent": "IOPLauncher"})
    with opener(request, timeout=10) as response:
        payload = response.read(64 * 1024 + 1)
    if len(payload) > 64 * 1024:
        raise ValueError("업데이트 정보가 너무 큽니다.")
    data = json.loads(payload.decode("utf-8-sig"))
    required = {"version", "url", "sha256", "size"}
    if not isinstance(data, dict) or not required <= data.keys():
        raise ValueError("업데이트 정보에 필수 항목이 없습니다.")
    version_tuple(data["version"])
    if not isinstance(data["url"], str) or not data["url"].startswith(ALLOWED_PREFIX):
        raise ValueError("허용되지 않은 업데이트 다운로드 주소입니다.")
    if not isinstance(data["sha256"], str) or len(data["sha256"]) != 64:
        raise ValueError("업데이트 SHA-256 형식이 올바르지 않습니다.")
    int(data["sha256"], 16)
    size = int(data["size"])
    if not 1_000_000 <= size <= MAX_DOWNLOAD:
        raise ValueError("업데이트 파일 크기가 허용 범위를 벗어났습니다.")
    data["size"] = size
    return data


def _validate_pe(path):
    data = path.read_bytes()
    if len(data) < 512 or data[:2] != b"MZ":
        raise ValueError("다운로드 파일이 Windows 실행파일이 아닙니다.")
    offset = struct.unpack_from("<I", data, 0x3C)[0]
    if offset + 6 > len(data) or data[offset:offset + 4] != b"PE\0\0":
        raise ValueError("다운로드 파일의 PE 헤더가 손상되었습니다.")


def download(manifest, opener=urllib.request.urlopen, directory=None):
    target = Path(directory or tempfile.gettempdir()) / ("IOPLauncher-" + uuid.uuid4().hex + ".exe")
    request = urllib.request.Request(manifest["url"], headers={"User-Agent": "IOPLauncher"})
    digest = hashlib.sha256()
    total = 0
    try:
        with opener(request, timeout=30) as response, target.open("xb") as output:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_DOWNLOAD or total > manifest["size"]:
                    raise ValueError("다운로드 크기가 업데이트 정보와 다릅니다.")
                digest.update(chunk)
                output.write(chunk)
        if total != manifest["size"]:
            raise ValueError("다운로드가 완료되지 않았습니다.")
        if digest.hexdigest().lower() != manifest["sha256"].lower():
            raise ValueError("업데이트 SHA-256 검증에 실패했습니다.")
        _validate_pe(target)
        return target
    except Exception:
        target.unlink(missing_ok=True)
        raise


def schedule_replace(downloaded, destination, version):
    destination = Path(destination).resolve()
    marker = destination.with_name("iop_update_result.json")
    script = Path(tempfile.gettempdir()) / ("IOPLauncher-update-" + uuid.uuid4().hex + ".ps1")
    # Arguments are passed separately so paths are never interpolated into PowerShell source.
    body = r'''param([int]$OldPid,[string]$Source,[string]$Destination,[string]$Marker,[string]$Version)
$ErrorActionPreference='Stop'
try {
  Wait-Process -Id $OldPid -Timeout 30 -ErrorAction SilentlyContinue
  $backup=$Destination+'.update-backup'
  if (Test-Path -LiteralPath $backup) { Remove-Item -LiteralPath $backup -Force }
  if (Test-Path -LiteralPath $Destination) { Copy-Item -LiteralPath $Destination -Destination $backup -Force }
  Move-Item -LiteralPath $Source -Destination $Destination -Force
  @{ok=$true;version=$Version;time=(Get-Date).ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath $Marker -Encoding UTF8
  Start-Process -FilePath $Destination -WorkingDirectory (Split-Path -LiteralPath $Destination)
} catch {
  @{ok=$false;version=$Version;time=(Get-Date).ToString('o');error=$_.Exception.Message} | ConvertTo-Json | Set-Content -LiteralPath $Marker -Encoding UTF8
} finally { Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue }
'''
    script.write_text(body, encoding="utf-8-sig")
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([
        "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", str(script), "-OldPid", str(os.getpid()), "-Source", str(downloaded),
        "-Destination", str(destination), "-Marker", str(marker), "-Version", str(version),
    ], creationflags=flags, close_fds=True)


def consume_result(home):
    marker = Path(home) / "iop_update_result.json"
    if not marker.exists():
        return None
    try:
        return json.loads(marker.read_text(encoding="utf-8-sig"))
    finally:
        marker.unlink(missing_ok=True)
