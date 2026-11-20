"""Hill autotiling learned from the installed multi maps.

IOP has no terrain height: a hill is a walkable top surrounded by blocked
cliff tiles, drawn in a pseudo-isometric style where the cliff face hangs two
or three cells below (south of) the top and the far rim is one cell thick.

learn() finds the hill tops of every installed map, groups the hills into
styles by their cliff graphics, and records for each style which tile appears
at each local pattern of "top" cells.  The web editor paints a top mask and
looks every cell up in that table (backing off to smaller windows, then to the
nearest known pattern), which reproduces the cliffs of the original maps for
any outline, diagonal edges included.  Entrances (ramps) are learned as small
patches together with the side the top lies on.

Hill graphics are 64-pixel blocks drawn as two cells side by side (left and
right half, e.g. tiles 10 and 11), and every hill keeps its blocks on one
column phase.  Each key therefore ends with the cell's half ('a' left, 'b'
right) relative to its hill's phase, so a generated hill never splits a block.
"""
from collections import Counter, defaultdict
import base64
import hashlib
import os
from pathlib import Path

from iop_map_format import MapDocument
import iop_stamps

GROUND, BLOCKED, HIGH = 0x3, 0x8, 0x20
# Windows (dx range, dy range) from most to least specific.  Faces hang up
# to three cells below a top edge, rims sit one cell above it.
WINDOWS = (((-2, 2), (-4, 2)), ((-1, 1), (-3, 1)), ((-1, 1), (-1, 1)))
NEAR = 4          # cells farther than this from any top are left alone
MAX_TOP_SHARE = 0.4
STYLE_KEYS = 12    # a hill's most common cliff graphics
SAME_STYLE = 0.75  # share of those two hills must have in common (same art)
MIN_STYLE_CELLS = 20
MAX_STYLES = 16

# Hills picked from the user's tile-ID screenshots: (map, a top cell near the
# hill, name).  Entries with one name form a style learned from those hills
# alone (each with its own block phase), listed first.  Entries whose map or hill is missing are skipped.
CURATED = (
    ('treesnowopen1.1.map', 110, 84, '회색 돌 언덕'),
    ('treesnowopen1.1.map', 72, 87, '회색 돌 언덕'),
    ('newgreen.map', 33, 68, '눈 언덕 (흙 절벽)'),
    ('newplanet.map', 35, 44, '사막 초록 기둥 언덕'),
    ('newladder05c.map', 23, 8, '사막 언덕'),
    ('newladder05c.map', 42, 25, '사막 바위 언덕'),
    ('newladder04b.map', 38, 75, '보라 언덕'),
    ('newladder04b.map', 75, 55, '보라 바위 언덕'),
)
# Click-placed hills are iso diamonds in block units: |column pair| + |row|
# <= r, which is the outline the game's own hills have.
PREVIEW_R = 3


def diamond(w, h, cx, cy, r, phase=0):
    """Top mask of a diamond hill centred on block column (cx - phase) // 2
    and row cy, r blocks from centre to tip."""
    uc = (cx - phase) // 2
    return [abs((x - phase) // 2 - uc) + abs(y - cy) <= r for x in range(w) for y in range(h)]


def _flags(tile):
    return tile[0] | tile[1] << 8


def _key(top, w, h, x, y, window):
    (x0, x1), (y0, y1) = window
    bits = []
    for dx in range(x0, x1 + 1):
        for dy in range(y0, y1 + 1):
            nx, ny = x + dx, y + dy
            bits.append('1' if 0 <= nx < w and 0 <= ny < h and top[nx * h + ny] else '0')
    return ''.join(bits)


def _band(blocked, w, h, x, y, dy):
    """Length of the blocked run starting next to (x, y) going in dy."""
    n, ny = 0, y + dy
    while 0 <= ny < h and blocked[x * h + ny] and n < 6:
        n += 1
        ny += dy
    return n


def top_mask(doc):
    """Walkable cells on hill tops (False elsewhere), plus the ramp mask."""
    w, h = doc.width, doc.height
    flags = [_flags(doc.tiles[t]) for t in doc.grid]
    if any(f & HIGH for f in flags):  # maps marked in the editor: trust the marks
        top = [bool(f & HIGH) and not f & BLOCKED and bool(f & GROUND) for f in flags]
        return top, iop_stamps.ramp_mask(w, h, flags)
    ramp = iop_stamps.ramp_mask(w, h, flags)
    blocked = [bool(f & BLOCKED) for f in flags]
    open_ = [bool(f & GROUND) and not b and not r for f, b, r in zip(flags, blocked, ramp)]
    comp, sizes, scores = [-1] * (w * h), [], []
    for start in range(w * h):
        if not open_[start] or comp[start] >= 0:
            continue
        label, stack, score, size = len(sizes), [start], 0, 0
        comp[start] = label
        while stack:
            i = stack.pop()
            size += 1
            x, y = divmod(i, h)
            # top: thick face below, thin rim above; low ground: the reverse
            south, north = _band(blocked, w, h, x, y, 1), _band(blocked, w, h, x, y, -1)
            if south:
                score += 1 if south >= 2 else -1
            if north:
                score += 1 if north == 1 else -1
            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                j = nx * h + ny
                if 0 <= nx < w and 0 <= ny < h and open_[j] and comp[j] < 0:
                    comp[j] = label
                    stack.append(j)
        sizes.append(size)
        scores.append(score)
    good = {c for c, (n, s) in enumerate(zip(sizes, scores))
            if s > 0 and 4 <= n <= MAX_TOP_SHARE * w * h}
    return [comp[i] in good for i in range(w * h)], ramp


def pair_halves(doc):
    """{left tile: right tile} for tiles that are always drawn as a pair."""
    w, h = doc.width, doc.height
    right, left, count = defaultdict(Counter), defaultdict(Counter), Counter()
    for x in range(w - 1):
        for y in range(h):
            a, b = doc.grid[x * h + y], doc.grid[(x + 1) * h + y]
            right[a][b] += 1
            left[b][a] += 1
            count[a] += 1
    out = {}
    for a, c in right.items():
        b, n = c.most_common(1)[0]
        if a != b and n >= 2 and n >= 0.9 * count[a] and left[b][a] >= 0.9 * sum(left[b].values()):
            out[a] = b
    return out


def hill_phases(doc, top=None):
    """Per cell: 0/1 = the column parity of left block halves of the hill this
    top cell belongs to, 2 = not a top cell or unknown."""
    w, h = doc.width, doc.height
    if top is None:
        top = top_mask(doc)[0]
    lefts = pair_halves(doc)
    _, tops = _components(top, w, h)
    out = bytearray([2]) * (w * h)
    for cells in tops:
        votes = Counter()
        for i in cells:
            x, y = divmod(i, h)
            for dx in range(-2, 3):  # the hill's own cliff blocks vote too
                for dy in range(-1, 4):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < w and 0 <= ny < h and doc.grid[nx * h + ny] in lefts:
                        votes[nx % 2] += 1
        if votes:
            phase = votes.most_common(1)[0][0]
            for i in cells:
                out[i] = phase
    return out


def _inner(top, w, h, x, y):
    """A top cell whose eight neighbours are top as well."""
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            nx, ny = x + dx, y + dy
            if not (0 <= nx < w and 0 <= ny < h and top[nx * h + ny]):
                return False
    return True


def _half(phase, x):
    return 'a' if (x - phase) % 2 == 0 else 'b'


def _hash(tile):
    return hashlib.md5(tile[2:]).hexdigest()[:12]


def _components(mask, w, h):
    comp, out = [-1] * (w * h), []
    for start, m in enumerate(mask):
        if not m or comp[start] >= 0:
            continue
        cells, stack = [], [start]
        comp[start] = len(out)
        while stack:
            i = stack.pop()
            cells.append(i)
            x, y = divmod(i, h)
            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                j = nx * h + ny
                if 0 <= nx < w and 0 <= ny < h and mask[j] and comp[j] < 0:
                    comp[j] = len(out)
                    stack.append(j)
        out.append(cells)
    return comp, out


def _dilate(mask, w, h, left, right, up, down):
    """Cells that have a mask cell within the box [x-left..x+right] x
    [y-up..y+down] around them."""
    out = [False] * (w * h)
    for i, m in enumerate(mask):
        if not m:
            continue
        x, y = divmod(i, h)
        for nx in range(max(0, x - right), min(w, x + left + 1)):
            for ny in range(max(0, y - down), min(h, y + up + 1)):
                out[nx * h + ny] = True
    return out


class _Pool:
    """Unique tile graphics (flags kept, editor high mark stripped)."""

    def __init__(self):
        self.index, self.tiles = {}, []

    def add(self, tile):
        key = _hash(tile) + '%04x' % (_flags(tile) & ~HIGH)
        if key not in self.index:
            raw = bytearray(tile)
            raw[0] &= ~HIGH & 0xFF
            self.index[key] = len(self.tiles)
            self.tiles.append(bytes(raw))
        return self.index[key]


def _sector(dx, dy):
    """Direction 0..7 (0 north, clockwise; y grows south) of a map vector."""
    import math
    a = math.degrees(math.atan2(dx, -dy)) % 360
    return int((a + 22.5) // 45) % 8


def learn(docs):
    """docs: [(name, MapDocument)] of one theme -> JSON-able style tables."""
    pool = _Pool()
    hills, per_map = [], []
    for m, (name, doc) in enumerate(docs):
        w, h = doc.width, doc.height
        top, ramp = top_mask(doc)
        comp, tops = _components(top, w, h)
        flags = [_flags(doc.tiles[t]) for t in doc.grid]
        # blocked cells next to (mostly below) a top belong to that hill
        owner = [-1] * (w * h)
        for c, cells in enumerate(tops):
            for i in cells:
                x, y = divmod(i, h)
                for dx in range(-2, 3):
                    for dy in range(-1, 4):
                        nx, ny = x + dx, y + dy
                        j = nx * h + ny
                        if 0 <= nx < w and 0 <= ny < h and flags[j] & BLOCKED and owner[j] < 0:
                            owner[j] = c
        cliffs = defaultdict(Counter)
        for i, c in enumerate(owner):
            if c >= 0:
                cliffs[c][_hash(doc.tiles[doc.grid[i]])] += 1
        for c, cells in enumerate(tops):
            if sum(cliffs[c].values()) >= 6:
                hills.append({'map': m, 'top': c, 'cells': cells, 'cliff': cliffs[c]})
        per_map.append((doc, top, ramp, comp, hill_phases(doc, top)))
    # hills whose cliff graphics overlap share a style
    parent = list(range(len(hills)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    sets = [set(k for k, _ in hl['cliff'].most_common(STYLE_KEYS)) for hl in hills]
    for a in range(len(hills)):
        for b in range(a + 1, len(hills)):
            if len(sets[a] & sets[b]) >= SAME_STYLE * min(len(sets[a]), len(sets[b])):
                parent[find(a)] = find(b)
    # curated hills first, each a style of its own
    names = [n for n, _ in docs]
    curated = []
    for name, cx, cy, label in CURATED:
        if name not in names:
            continue
        m = names.index(name)
        h = docs[m][1].height
        near_hills = [a for a, hl in enumerate(hills) if hl['map'] == m
                      and min(abs(i // h - cx) + abs(i % h - cy) for i in hl['cells']) <= 3]
        if near_hills and near_hills[0] not in [a for a, _ in curated]:
            curated.append((near_hills[0], label))
    taken = {a for a, _ in curated}
    groups = defaultdict(list)
    for a in range(len(hills)):
        if a not in taken:
            groups[find(a)].append(a)
    styles = []
    order = []
    for a, label in curated:
        group = next((g for g in order if g[1] == label), None)
        if group:
            group[0].append(a)
        else:
            order.append(([a], label))
    order += [(g, None) for g in sorted(groups.values(), key=lambda g: -sum(len(hills[a]['cells']) for a in g))]
    for members, label in order:
        votes = [defaultdict(Counter) for _ in WINDOWS]
        fill = defaultdict(Counter)  # inner top cells: the plain top block
        maps, cliff = set(), set()
        # Tables come from the style's largest hill alone: hills of one look
        # still differ in block phase and top graphics, and mixing them
        # breaks the blocks.  The other members only add map names.
        best = max(members, key=lambda a: len(hills[a]['cells']))
        for a in members:
            maps.add(docs[hills[a]['map']][0])
            cliff |= sets[a]
        for a in (members if label else [best]):
            hl = hills[a]
            doc, top, ramp, comp, phases = per_map[hl['map']]
            phase = phases[hl['cells'][0]]
            if phase == 2:
                phase = 0
            w, h = doc.width, doc.height
            mine = [comp[i] == hl['top'] for i in range(w * h)]
            near = _dilate(mine, w, h, NEAR, NEAR, NEAR, NEAR - 1)
            for i in range(w * h):
                if not near[i] or ramp[i]:
                    continue
                x, y = divmod(i, h)
                tile = pool.add(doc.tiles[doc.grid[i]])
                half = _half(phase, x)
                if _inner(mine, w, h, x, y):
                    fill[half][tile] += 1
                for k, window in enumerate(WINDOWS):
                    votes[k][_key(mine, w, h, x, y, window) + half][tile] += 1
        tables = [{key: c.most_common(1)[0][0] for key, c in v.items()} for v in votes]
        fill = {half: c.most_common(1)[0][0] for half, c in fill.items()}
        style = {'id': 's%d' % len(styles), 'hills': len(members), 'maps': sorted(maps),
                 'cells': sum(len(hills[a]['cells']) for a in members),
                 'tables': tables, 'fill': fill, 'cliff': cliff, 'curated': bool(label)}
        if label:
            style['label'] = label
        # gallery picture: the hill a click makes, on the source map's ground
        src = per_map[hills[best]['map']][0]
        plain = [t for t, _ in Counter(src.grid).most_common()
                 if _flags(src.tiles[t]) & GROUND and not _flags(src.tiles[t]) & (BLOCKED | HIGH)]
        ground = pool.add(src.tiles[plain[0] if plain else src.grid[0]])
        pw, ph = 4 * PREVIEW_R + 6, 2 * PREVIEW_R + 7
        top = diamond(pw, ph, pw // 2, PREVIEW_R + 2, PREVIEW_R)
        ids = _lookup_all(style, pool.tiles, pw, ph, top, 0)
        style['sample'] = {'w': pw, 'h': ph, 'ids': [ground if t is None else t for t in ids]}
        styles.append(style)
    styles = [s for s in styles if s['curated'] or s['cells'] >= MIN_STYLE_CELLS][:MAX_STYLES]
    ramps = _ramps(docs, per_map, styles, pool)
    for n, s in enumerate(styles):
        del s['cliff']
        s['id'] = 's%d' % n
    for r in ramps:
        r['style'] = r['style']['id']
    for n, s in enumerate(styles):
        s['name'] = '%d. %s' % (n + 1, s['maps'][0].rsplit('.', 1)[0]) + (' 외 %d' % (len(s['maps']) - 1) if len(s['maps']) > 1 else '')
    # keep only the tile graphics the tables, samples and ramps refer to
    used = sorted({t for s in styles for table in s['tables'] for t in table.values()}
                  | {t for s in styles for t in s['fill'].values()}
                  | {t for s in styles for t in s['sample']['ids']} | {t for r in ramps for t in r['ids']})
    remap = {old: new for new, old in enumerate(used)}
    for s in styles:
        s['tables'] = [{k: remap[t] for k, t in table.items()} for table in s['tables']]
        s['fill'] = {k: remap[t] for k, t in s['fill'].items()}
        s['sample']['ids'] = [remap[t] for t in s['sample']['ids']]
    for r in ramps:
        r['ids'] = [remap[t] for t in r['ids']]
    return {'windows': [[list(a), list(b)] for a, b in WINDOWS], 'near': NEAR, 'styles': styles, 'ramps': ramps,
            'tiles': base64.b64encode(b''.join(pool.tiles[t] for t in used)).decode(), 'tileCount': len(used)}


def _sample(entry, hill, pool, limit=24):
    """The style's largest hill cut out (at most limit x limit) for the gallery."""
    doc = entry[0]
    h = doc.height
    xs = [i // h for i in hill['cells']]
    ys = [i % h for i in hill['cells']]
    x0 = max(0, min(xs) - 2)
    y0 = max(0, min(ys) - 2)
    x1 = min(doc.width, x0 + limit, max(xs) + 3)
    y1 = min(h, y0 + limit, max(ys) + 5)
    ids = [pool.add(doc.tiles[doc.grid[x * h + y]]) for x in range(x0, x1) for y in range(y0, y1)]
    return {'w': x1 - x0, 'h': y1 - y0, 'ids': ids}


def _ramps(docs, per_map, styles, pool):
    out = []
    for m, (name, doc) in enumerate(docs):
        _, top, ramp, comp, _ = per_map[m]
        w, h = doc.width, doc.height
        for cells in _components(ramp, w, h)[1]:
            if not 2 <= len(cells) <= 40:
                continue
            xs, ys = [i // h for i in cells], [i % h for i in cells]
            x0, x1 = max(0, min(xs) - 2), min(w, max(xs) + 3)
            y0, y1 = max(0, min(ys) - 2), min(h, max(ys) + 3)
            if x1 - x0 > 16 or y1 - y0 > 16:
                continue
            tops = [(x, y) for x in range(x0, x1) for y in range(y0, y1) if top[x * h + y]]
            if not tops:
                continue
            rx, ry = sum(xs) / len(xs), sum(ys) / len(ys)
            tx, ty = sum(p[0] for p in tops) / len(tops), sum(p[1] for p in tops) / len(tops)
            hashes = {_hash(doc.tiles[doc.grid[x * h + y]]) for x in range(x0, x1) for y in range(y0, y1)
                      if _flags(doc.tiles[doc.grid[x * h + y]]) & BLOCKED}
            style = max(styles, key=lambda s: len(hashes & s['cliff']), default=None)
            if style is None or len(hashes & style['cliff']) < 2:
                continue
            cells_xy = [(x, y) for x in range(x0, x1) for y in range(y0, y1)]
            out.append({'style': style, 'dir': _sector(tx - rx, ty - ry), 'map': name,
                        'x': x0, 'y': y0, 'w': x1 - x0, 'h': y1 - y0,
                        'ids': [pool.add(doc.tiles[doc.grid[x * h + y]]) for x, y in cells_xy],
                        'top': [int(top[x * h + y]) for x, y in cells_xy],
                        'ramp': [int(ramp[x * h + y]) for x, y in cells_xy]})
    return out


class HillLibrary:
    """learn() per theme over map/multi, cached until a map file changes."""

    def __init__(self, maps_folder):
        self.maps_folder = maps_folder
        self._cache = {}

    def get(self, theme):
        docs, stamp = [], []
        for path in sorted(Path(self.maps_folder()).glob('*.map'), key=lambda p: p.name.lower()):
            try:
                st = path.stat()
                doc = MapDocument.load(path)
            except (OSError, ValueError):
                continue
            if doc.theme == theme:
                docs.append((path.name, doc))
                stamp.append((path.name, st.st_mtime_ns, st.st_size))
        cached = self._cache.get(theme)
        if cached and cached[0] == stamp:
            return cached[1]
        result = learn(docs)
        result['theme'] = theme
        self._cache[theme] = (stamp, result)
        return result


def generate(result, style_id, w, h, top, phase=0):
    """Tile index (into result['tiles']) for every cell near the top mask,
    None for cells left as they are.  Mirrors the editor's lookup."""
    style = next(s for s in result['styles'] if s['id'] == style_id)
    raw = base64.b64decode(result['tiles'])
    tiles = [raw[i:i + 1026] for i in range(0, len(raw), 1026)]
    return _lookup_all(style, tiles, w, h, top, phase)


def _lookup_all(style, tiles, w, h, top, phase):
    near = _dilate(top, w, h, NEAR, NEAR, NEAR, NEAR - 1)
    out = [None] * (w * h)
    small = style['tables'][-1]
    blocked = lambda t: tiles[t][0] & BLOCKED
    for i in range(w * h):
        if not near[i]:
            continue
        x, y = divmod(i, h)
        half = _half(phase, x)
        if top[i] and _inner(top, w, h, x, y) and half in style.get('fill', {}):
            out[i] = style['fill'][half]
            continue
        for k, window in enumerate(WINDOWS):
            tile = style['tables'][k].get(_key(top, w, h, x, y, window) + half)
            if tile is not None:
                break
        else:
            key = _key(top, w, h, x, y, WINDOWS[-1]) + half
            best = min(small, key=lambda k: (k[-1] != half, sum(a != b for a, b in zip(k, key)), k[4] != key[4]))
            tile = small[best]
        # low ground: cliff tiles only in the band a face reaches, else untouched
        if not top[i] and not (blocked(tile) and '1' in _key(top, w, h, x, y, WINDOWS[1])):
            continue
        out[i] = tile
    return out
