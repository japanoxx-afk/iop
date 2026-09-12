"""Read-only parser and renderer for Impact of Power multiplayer maps."""
from dataclasses import dataclass
from pathlib import Path


THEMES = {0: ("아트로스", "a.pal"), 1: ("다크존", "b.pal"), 2: ("노블어스", "c.pal")}


@dataclass(frozen=True)
class MapInfo:
    path: Path
    width: int
    height: int
    players: int
    theme_id: int
    theme: str
    header_size: int
    file_size: int
    terrain: bytes


def read_map(path):
    path = Path(path)
    data = path.read_bytes()
    if len(data) < 24:
        raise ValueError("맵 파일이 너무 작습니다.")
    width = int.from_bytes(data[-8:-4], "little") + 1
    height = int.from_bytes(data[-4:], "little") + 1
    if not (1 <= width <= 512 and 1 <= height <= 512):
        raise ValueError(f"지원하지 않는 맵 크기입니다: {width}×{height}")
    cells = width * height
    if cells + 8 > len(data):
        raise ValueError("지형 레이어가 잘렸습니다.")
    theme_id = data[4]
    theme = THEMES.get(theme_id, (f"알 수 없음 ({theme_id})", ""))[0]
    header_size = max(0, int.from_bytes(data[:2], "little") - 3)
    return MapInfo(path, width, height, data[5], theme_id, theme,
                   header_size, len(data), data[-8-cells:-8])


def list_multiplayer(game_dir):
    folder = Path(game_dir) / "map" / "multi"
    if not folder.is_dir():
        return []
    return [read_map(path) for path in sorted(folder.glob("*.map"), key=lambda p: p.name.lower())]


def read_palette(game_dir, theme_id):
    if theme_id not in THEMES:
        return [(v, v, v) for v in range(256)]
    path = Path(game_dir) / "map" / "mappal" / THEMES[theme_id][1]
    raw = path.read_bytes()
    if len(raw) < 768:
        raise ValueError(f"팔레트 파일이 잘렸습니다: {path.name}")
    scale = 4 if max(raw[:768]) <= 63 else 1
    return [tuple(min(255, value * scale) for value in raw[i:i+3])
            for i in range(0, 768, 3)]


def ppm_preview(info, palette, scale=4):
    """Return binary PPM bytes enlarged with nearest-neighbour sampling."""
    scale = max(1, min(8, int(scale)))
    rows = []
    for y in range(info.height):
        source = info.terrain[y * info.width:(y + 1) * info.width]
        row = b"".join(bytes(palette[index]) * scale for index in source)
        rows.extend([row] * scale)
    header = f"P6\n{info.width * scale} {info.height * scale}\n255\n".encode("ascii")
    return header + b"".join(rows)
