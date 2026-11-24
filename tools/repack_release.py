"""Rebuild a onefile launcher exe from a previous release with updated project modules.

Used when no Windows build machine is available: the PyInstaller bootloader,
python312.dll, Tcl/Tk and every library stay byte-identical to the base
release; only the project's own modules (PYZ entries named iop_*), the entry
script and the bundled project data files are replaced. Must run under
CPython 3.12 (same bytecode as the embedded python312.dll) with PyInstaller
installed:  python3.12 tools/repack_release.py BASE.exe OUT.exe
"""
import marshal
from pathlib import Path
import struct
import sys
import tempfile
import zlib

from PyInstaller.archive.readers import CArchiveReader
from PyInstaller.archive.writers import ZlibArchiveWriter
from PyInstaller.loader.pyimod01_archive import PYZ_ITEM_PKG, PYZ_ITEM_NSPKG

ROOT = Path(__file__).resolve().parents[1]
ENTRY = "iop_launcher"
DATA_FILES = {"iop_balance.py": "iop_balance.py",
              "map_editor_web\\IOPMapEditor.html": "map_editor_web/IOPMapEditor.html"}
TOC_FORMAT = "!IIIIBc"
COOKIE_FORMAT = "!8sIIII64s"
COOKIE_MAGIC = b"MEI\014\013\012\013\016"


def project_modules():
    modules = {}
    for path in ROOT.glob("iop_*.py"):
        if path.stem != ENTRY:
            modules[path.stem] = (path, False)
    package = ROOT / "iop_server"
    for path in package.glob("*.py"):
        name = "iop_server" if path.stem == "__init__" else "iop_server." + path.stem
        modules[name] = (path, path.stem == "__init__")
    return modules


def compile_source(path, filename):
    return compile(path.read_text(encoding="utf-8"), filename, "exec", optimize=0, dont_inherit=True)


def build_pyz(reader, target):
    old = reader.open_embedded_archive("PYZ.pyz")
    entries, code = [], {}
    project = project_modules()
    for name, (typecode, _offset, _length) in old.toc.items():
        if name in project or name.startswith("iop_"):
            continue
        if typecode == PYZ_ITEM_NSPKG:
            entries.append((name, "-", "PYMODULE"))
            continue
        code[name] = old.extract(name)
        entries.append((name, "__init__.py" if typecode == PYZ_ITEM_PKG else name + ".py", "PYMODULE"))
    for name, (path, package) in sorted(project.items()):
        code[name] = compile_source(path, path.name)
        entries.append((name, "__init__.py" if package else path.name, "PYMODULE"))
    ZlibArchiveWriter(str(target), entries, code)
    return sorted(project)


def serialize_toc(toc):
    out = []
    for offset, length, ulength, compress, typecode, name in toc:
        raw = name.encode("utf-8")
        size = len(raw) + 1
        entry = struct.calcsize(TOC_FORMAT) + size
        if entry % 16:
            size += 16 - entry % 16
        out.append(struct.pack(TOC_FORMAT + f"{size}s", struct.calcsize(TOC_FORMAT) + size,
                               offset, length, ulength, compress, typecode.encode("ascii"), raw))
    return b"".join(out)


def main(base, output):
    data = Path(base).read_bytes()
    reader = CArchiveReader(base)
    start = reader._start_offset
    with tempfile.TemporaryDirectory() as tmp:
        pyz_path = Path(tmp) / "PYZ.pyz"
        modules = build_pyz(reader, pyz_path)
        pyz = pyz_path.read_bytes()
    body, toc = bytearray(), []
    for name, (offset, length, ulength, compress, typecode) in reader.toc.items():
        if name == ENTRY:
            blob = marshal.dumps(compile_source(ROOT / "iop_launcher.py", "iop_launcher.py"))
            stored, compress = zlib.compress(blob, 9), 1
            ulength = len(blob)
        elif name == "PYZ.pyz":
            stored, ulength, compress = pyz, len(pyz), 0
        elif name in DATA_FILES:
            blob = (ROOT / DATA_FILES[name]).read_bytes()
            stored = zlib.compress(blob, 9) if compress else blob
            ulength = len(blob)
        else:
            stored = data[start + offset:start + offset + length]
        toc.append((len(body), len(stored), ulength, compress, typecode, name))
        body += stored
    toc_data = serialize_toc(toc)
    toc_offset = len(body)
    cookie_len = struct.calcsize(COOKIE_FORMAT)
    old_cookie = struct.unpack(COOKIE_FORMAT, data[-cookie_len:])
    cookie = struct.pack(COOKIE_FORMAT, COOKIE_MAGIC, toc_offset + len(toc_data) + cookie_len,
                         toc_offset, len(toc_data), old_cookie[4], old_cookie[5])
    Path(output).write_bytes(data[:start] + bytes(body) + toc_data + cookie)
    check = CArchiveReader(output)
    assert set(check.toc) == set(reader.toc)
    assert check.open_embedded_archive("PYZ.pyz").extract("iop_replay") is not None
    print(f"{output}: {len(modules)} project modules, {Path(output).stat().st_size} bytes")


if __name__ == "__main__":
    if sys.version_info[:2] != (3, 12):
        raise SystemExit("python 3.12 required (embedded runtime is python312.dll)")
    main(sys.argv[1], sys.argv[2])
