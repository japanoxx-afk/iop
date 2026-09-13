"""Read-only parser and renderer for Impact of Power multiplayer maps."""
from dataclasses import dataclass
from pathlib import Path
import struct


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

    @property
    def starts(self):
        data = self.path.read_bytes()
        if len(data) < 38 or not 2 <= self.players <= 8:
            raise ValueError('지원하지 않는 멀티플레이 시작 위치 헤더입니다.')
        return [struct.unpack_from('<hh', data, 6 + i * 4) for i in range(self.players)]


def save_starts(info, positions, destination):
    if len(positions) != info.players:
        raise ValueError('시작 위치 개수가 참가 인원과 다릅니다.')
    if len(set(positions)) != len(positions):
        raise ValueError('시작 위치가 중복됩니다.')
    data = bytearray(info.path.read_bytes())
    for i, (x, y) in enumerate(positions):
        if not (0 <= x < info.width and 0 <= y < info.height):
            raise ValueError('시작 위치가 맵 범위를 벗어났습니다.')
        struct.pack_into('<hh', data, 6 + i * 4, x, y)
    destination = Path(destination)
    if destination.suffix.lower() != '.map':
        raise ValueError('.map 확장자로 저장하세요.')
    # Exclusive creation protects original maps and previously saved work.
    with destination.open('xb') as stream:
        stream.write(data)


def read_map(path):
    from iop_map_format import MapDocument
    path = Path(path)
    doc = MapDocument.load(path)
    theme_id = doc.theme
    theme = THEMES.get(theme_id, (f"알 수 없음 ({theme_id})", ""))[0]
    header_size = int.from_bytes(doc.fixed[:2], 'little')
    return MapInfo(path, doc.width, doc.height, doc.players, theme_id, theme,
                   header_size, path.stat().st_size, bytes(doc.minimap))


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
