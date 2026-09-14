"""Build repository game assets from a supported local client and official cnc-ddraw files."""
import argparse
import hashlib
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from iop_assets import CANONICAL_IOP_SHA256, normalized_iop


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("game_dir", type=Path)
    parser.add_argument("cnc_ddraw_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    client = normalized_iop((args.game_dir / "iop.exe").read_bytes())
    if hashlib.sha256(client).hexdigest() != CANONICAL_IOP_SHA256:
        raise ValueError("지원되는 기준 iop.exe를 만들 수 없습니다.")
    (args.output_dir / "iop.exe").write_bytes(client)
    for name in ("ddraw.dll", "ddraw.ini"):
        shutil.copy2(args.cnc_ddraw_dir / name, args.output_dir / name)
    for path in args.output_dir.iterdir():
        print(path.name, path.stat().st_size, hashlib.sha256(path.read_bytes()).hexdigest())
