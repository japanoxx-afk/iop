"""HTTPS fetch with a Windows fallback.

Some launcher builds could not create a Python SSL context at all
([Errno 2] inside SSLContext), which broke every GitHub download. When Python's
own HTTPS stack fails before any HTTP response, fetch the same URL with
PowerShell, which uses the Windows TLS stack instead of the bundled OpenSSL.
"""
import io
import os
from pathlib import Path
import subprocess
import tempfile
import urllib.error
import urllib.request
import uuid

ALLOWED_PREFIX = "https://raw.githubusercontent.com/japanoxx-afk/iop/"
MAX_FALLBACK = 64 * 1024 * 1024

_SCRIPT = (
    "$ErrorActionPreference='Stop';"
    "$ProgressPreference='SilentlyContinue';"
    "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12;"
    "Invoke-WebRequest -UseBasicParsing -UserAgent 'IOPLauncher' "
    "-TimeoutSec $env:IOP_FETCH_TIMEOUT -Uri $env:IOP_FETCH_URL -OutFile $env:IOP_FETCH_OUT"
)


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def powershell_fetch(url, timeout=30, runner=subprocess.run):
    if not url.startswith(ALLOWED_PREFIX):
        raise ValueError("허용되지 않은 다운로드 주소입니다.")
    target = Path(tempfile.gettempdir()) / ("IOPLauncher-fetch-" + uuid.uuid4().hex)
    # Values travel through the environment, never interpolated into script text.
    env = dict(os.environ, IOP_FETCH_URL=url, IOP_FETCH_OUT=str(target),
               IOP_FETCH_TIMEOUT=str(max(5, int(timeout))))
    try:
        result = runner(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-Command", _SCRIPT],
            capture_output=True, timeout=max(30, int(timeout) * 4), env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode or not target.is_file():
            detail = (result.stderr or b"").decode("utf-8", "replace").strip().splitlines()
            raise OSError("Windows 다운로드 실패: " + (detail[0] if detail else str(result.returncode)))
        if target.stat().st_size > MAX_FALLBACK:
            raise ValueError("다운로드 파일이 허용 크기를 초과했습니다.")
        return target.read_bytes()
    finally:
        target.unlink(missing_ok=True)


def urlopen(request, timeout=30):
    """Drop-in for urllib.request.urlopen used for GitHub reads."""
    try:
        return urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError:
        raise  # the server answered; the fallback would get the same answer
    except Exception as first:
        url = request.full_url if isinstance(request, urllib.request.Request) else str(request)
        try:
            return _Response(powershell_fetch(url, timeout))
        except Exception as second:
            raise OSError(f"{first} / {second}") from second
