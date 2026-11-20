"""Hill / ramp stamps for the web map editor.

A stamp is a rectangle of map cells carried with its own tile graphics so it
can be placed on any map of the same theme:

    {id, name, kind, theme, w, h, ids: [w*h tile indexes, column-major],
     tiles: base64 of len(unique tiles) * 1026 bytes, builtin: bool}

kind: 'high' (고지대 언덕: plateau hides vision), 'low' (낮은 언덕: vision open),
'ramp' (입구).  High-ground cells are carried in the tile flags (0x20), exactly
as on a map.  User stamps are JSON files in the launcher's settings folder, not
in the game folder, so they never affect A/B file sync.

Built-in stamps are cut from the installed multiplayer maps on demand (the
editor does not ship game graphics).
"""
from pathlib import Path
import base64
import hashlib
import json
import re
import struct
import threading

from iop_map_format import MapDocument, HIGH_GROUND_FLAG

TILE = 1026
KINDS = ('high', 'low', 'ramp')
MAX_SIDE = 64
GROUND, BLOCKED = 0x3, 0x8  # 0x1/0x2: passable terrain kinds, 0x8: cliff/obstacle

# (map file, x1, y1, x2, y2, kind, name).  Rectangles were picked by looking
# at the installed maps; entries whose map is missing are skipped.
BUILTIN = (
    ('treebrownhill.map', 0, 0, 21, 20, 'high', '모서리 기지 (오른쪽·아래 절벽)'),
    ('treebrownhill.map', 50, 0, 79, 25, 'high', '기지 (양옆·아래 절벽)'),
    ('treebrownhill.map', 11, 11, 21, 20, 'ramp', '대각선 입구'),
    ('Aladderc2.map', 2, 45, 44, 71, 'high', '긴 언덕 (닫힌 모양)'),
    ('newladder04b.map', 18, 50, 52, 84, 'high', '언덕 (닫힌 모양)'),
    ('newladder05c.map', 33, 1, 61, 29, 'high', '언덕 (닫힌 모양)'),
)

_lock = threading.Lock()


def _flags(tile):
    return tile[0] | tile[1] << 8


def cut(doc, x1, y1, x2, y2):
    """Cells x1..x2, y1..y2 of a MapDocument as (w, h, ids, unique tiles)."""
    if not (0 <= x1 <= x2 < doc.width and 0 <= y1 <= y2 < doc.height):
        raise ValueError('견본 영역이 맵 밖입니다.')
    index, tiles, ids = {}, [], []
    for x in range(x1, x2 + 1):
        for y in range(y1, y2 + 1):
            raw = bytes(doc.tiles[doc.grid[x * doc.height + y]])
            if raw not in index:
                index[raw] = len(tiles)
                tiles.append(raw)
            ids.append(index[raw])
    return x2 - x1 + 1, y2 - y1 + 1, ids, tiles


def enclosed_mask(w, h, flags, ramp):
    """Walkable cells not reachable from the stamp border without crossing
    blocked or ramp cells: the top of a closed hill."""
    open_ = [bool(f & GROUND) and not (f & BLOCKED) and not r for f, r in zip(flags, ramp)]
    outside = [False] * (w * h)
    stack = [x * h + y for x in range(w) for y in range(h)
             if (x in (0, w - 1) or y in (0, h - 1)) and open_[x * h + y]]
    for i in stack:
        outside[i] = True
    while stack:
        i = stack.pop()
        x, y = divmod(i, h)
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            j = nx * h + ny
            if 0 <= nx < w and 0 <= ny < h and open_[j] and not outside[j]:
                outside[j] = True
                stack.append(j)
    return [open_[i] and not outside[i] for i in range(w * h)]


def plateau_mask(w, h, flags, ramp):
    """High-ground guess for a stamp: closed hill tops, plus the open region
    that sits on top of the cliff faces.  The game draws cliff faces below
    (south of) the plateau, so the region found just north of blocked cells
    more often than just south of them is the top."""
    inner = enclosed_mask(w, h, flags, ramp)
    open_ = [bool(f & GROUND) and not (f & BLOCKED) and not r for f, r in zip(flags, ramp)]
    blocked = [bool(f & BLOCKED) for f in flags]
    comp = [-1] * (w * h)
    scores = []
    for start in range(w * h):
        if not open_[start] or comp[start] >= 0 or inner[start]:
            continue
        label = len(scores)
        score, stack = 0, [start]
        comp[start] = label
        while stack:
            i = stack.pop()
            x, y = divmod(i, h)
            score += any(y + k < h and blocked[x * h + y + k] for k in (1, 2))
            score -= any(y - k >= 0 and blocked[x * h + y - k] for k in (1, 2))
            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                j = nx * h + ny
                if 0 <= nx < w and 0 <= ny < h and open_[j] and comp[j] < 0 and not inner[j]:
                    comp[j] = label
                    stack.append(j)
        scores.append(score)
    top = max(range(len(scores)), key=scores.__getitem__) if scores and max(scores) > 0 and len(scores) > 1 else -1
    return [inner[i] or (top >= 0 and comp[i] == top) for i in range(w * h)]


def ramp_mask(w, h, flags):
    """Walkable cells in a gap of a cliff band: cliff within three cells on
    both sides along a row, column or diagonal, while two cells on each side
    across that line are open (so the gap leads somewhere, unlike a narrow hill top
    that has cliffs all around)."""
    blocked = [bool(f & BLOCKED) for f in flags]

    def cliff(x, y):
        return 0 <= x < w and 0 <= y < h and blocked[x * h + y]

    def hit(x, y, dx, dy):
        for k in range(1, 4):
            nx, ny = x + dx * k, y + dy * k
            if not (0 <= nx < w and 0 <= ny < h):
                return False
            if blocked[nx * h + ny]:
                return True
        return False

    axes = (((1, 0), (0, 1)), ((0, 1), (1, 0)), ((1, 1), (1, -1)), ((1, -1), (1, 1)))
    out = []
    for x in range(w):
        for y in range(h):
            f = flags[x * h + y]
            out.append(bool(f & GROUND) and not f & BLOCKED and any(
                hit(x, y, dx, dy) and hit(x, y, -dx, -dy)
                and not any(cliff(x + k * px, y + k * py) or cliff(x - k * px, y - k * py) for k in (1, 2))
                for (dx, dy), (px, py) in axes))
    return out


def with_high(w, h, ids, tiles, mask):
    """Return (ids, tiles) where masked cells use the high-ground variant."""
    tiles = list(tiles)
    index = {t: i for i, t in enumerate(tiles)}
    out = []
    for cell, tid in enumerate(ids):
        raw = tiles[tid]
        if mask[cell] and not _flags(raw) & HIGH_GROUND_FLAG:
            f = _flags(raw) | HIGH_GROUND_FLAG
            raw = bytes((f & 255, f >> 8)) + raw[2:]
            if raw not in index:
                index[raw] = len(tiles)
                tiles.append(raw)
            tid = index[raw]
        out.append(tid)
    return out, tiles


def skip_mask(w, h, flags, ramp):
    """Plain ground of the stamp: walkable, not high, not a ramp and more
    than one cell from any cliff.  Placing a stamp leaves those cells alone
    so the target map keeps its own ground texture around the hill."""
    near = [False] * (w * h)
    for i, f in enumerate(flags):
        if f & BLOCKED or ramp[i]:
            x, y = divmod(i, h)
            for nx in range(max(0, x - 1), min(w, x + 2)):
                for ny in range(max(0, y - 1), min(h, y + 2)):
                    near[nx * h + ny] = True
    return [not near[i] and not f & BLOCKED and not f & HIGH_GROUND_FLAG for i, f in enumerate(flags)]


def to_json(stamp_id, name, kind, theme, w, h, ids, tiles, builtin=False):
    flags = [_flags(tiles[i]) for i in ids]
    ramp = ramp_mask(w, h, flags)
    skip = skip_mask(w, h, flags, ramp)
    return {'id': stamp_id, 'name': name, 'kind': kind, 'theme': theme, 'w': w, 'h': h,
            'ids': ids, 'tiles': base64.b64encode(b''.join(tiles)).decode('ascii'),
            'ramp': [int(r) for r in ramp], 'skip': [int(s) for s in skip], 'builtin': builtin}


def auto_high(kind, w, h, ids, tiles):
    """A 'high' stamp with no high-ground cells gets its plateau marked."""
    flags = [_flags(tiles[i]) for i in ids]
    if kind != 'high' or any(f & HIGH_GROUND_FLAG for f in flags):
        return ids, tiles
    return with_high(w, h, ids, tiles, plateau_mask(w, h, flags, ramp_mask(w, h, flags)))


def validate(data):
    name = str(data.get('name', '')).strip()
    if not 0 < len(name) <= 40:
        raise ValueError('견본 이름은 1~40자로 정하세요.')
    kind = data.get('kind')
    if kind not in KINDS:
        raise ValueError('견본 종류가 올바르지 않습니다.')
    theme, w, h = int(data['theme']), int(data['w']), int(data['h'])
    if not (1 <= w <= MAX_SIDE and 1 <= h <= MAX_SIDE):
        raise ValueError(f'견본은 가로·세로 {MAX_SIDE}칸 이하만 저장할 수 있습니다.')
    raw = base64.b64decode(data['tiles'], validate=True)
    if not raw or len(raw) % TILE:
        raise ValueError('견본 타일 데이터가 올바르지 않습니다.')
    count = len(raw) // TILE
    if count > 2 * w * h:
        raise ValueError('견본 타일 데이터가 올바르지 않습니다.')
    ids = [int(i) for i in data['ids']]
    if len(ids) != w * h or any(not 0 <= i < count for i in ids):
        raise ValueError('견본 칸 데이터가 올바르지 않습니다.')
    tiles = [raw[i * TILE:(i + 1) * TILE] for i in range(count)]
    return name, kind, theme, w, h, ids, tiles


class StampStore:
    def __init__(self, folder, maps_folder):
        self.folder = Path(folder) if folder else None
        self.maps_folder = maps_folder          # callable -> Path of map/multi
        self._builtin = {}

    def _builtins(self, theme):
        folder = self.maps_folder()
        result = []
        for name, x1, y1, x2, y2, kind, label in BUILTIN:
            path = folder / name
            try:
                key = (str(path), path.stat().st_mtime_ns, x1, y1, x2, y2, kind)
                if key not in self._builtin:
                    doc = MapDocument.load(path)
                    w, h, ids, tiles = cut(doc, x1, y1, x2, y2)
                    ids, tiles = auto_high(kind, w, h, ids, tiles)
                    sid = 'b-' + hashlib.sha1(repr(key[2:]).encode() + name.encode()).hexdigest()[:10]
                    self._builtin[key] = (doc.theme, to_json(sid, label, kind, doc.theme, w, h, ids, tiles, True))
                stamp_theme, stamp = self._builtin[key]
                if stamp_theme == theme:
                    result.append(stamp)
            except (OSError, ValueError):
                continue
        return result

    def list(self, theme):
        stamps = self._builtins(theme)
        if self.folder and self.folder.is_dir():
            for path in sorted(self.folder.glob('*.json')):
                try:
                    data = json.loads(path.read_text(encoding='utf-8'))
                    if data.get('theme') == theme:
                        name, kind, theme_, w, h, ids, tiles = validate(data)
                        stamps.append(to_json(path.stem, name, kind, theme_, w, h, ids, tiles))
                except (OSError, ValueError, KeyError, TypeError):
                    continue
        return {'stamps': stamps}

    def save(self, data):
        if not self.folder:
            raise ValueError('견본 저장 폴더가 없습니다.')
        name, kind, theme, w, h, ids, tiles = validate(data)
        ids, tiles = auto_high(kind, w, h, ids, tiles)
        stamp = to_json('', name, kind, theme, w, h, ids, tiles)
        stamp_id = 'u-' + hashlib.sha1(json.dumps(stamp, sort_keys=True).encode()).hexdigest()[:12]
        stamp['id'] = stamp_id
        with _lock:
            self.folder.mkdir(parents=True, exist_ok=True)
            tmp = self.folder / (stamp_id + '.tmp')
            tmp.write_text(json.dumps(stamp, ensure_ascii=False), encoding='utf-8')
            tmp.replace(self.folder / (stamp_id + '.json'))
        return stamp

    def delete(self, stamp_id):
        if not re.fullmatch(r'u-[0-9a-f]{12}', stamp_id or ''):
            raise ValueError('기본 견본은 지울 수 없습니다.')
        path = self.folder / (stamp_id + '.json') if self.folder else None
        if not path or not path.is_file():
            raise FileNotFoundError(stamp_id)
        path.unlink()
        return {'ok': True}
