"""Local web map editor for Impact of Power multiplayer maps.

The launcher serves map_editor_web/IOPMapEditor.html on 127.0.0.1 and opens it
in the browser. The page talks to a small JSON API:

    GET  /api/ping
    GET  /api/maps                       map/multi/*.map list
    GET  /api/map?name=X.map             decoded map (tiles, grid, starts, ...)
    PUT  /api/map?name=X.map[&overwrite=1]   save edited map (.bak backup)
    POST /api/new                        empty map of a terrain (theme)
    PUT  /api/category                   remember a tile's 일반/언덕/입구 label
    GET  /api/stamps?theme=N             hill/ramp stamps for a theme
    PUT  /api/stamp                      save a user stamp
    DELETE /api/stamp?id=X               delete a user stamp

Every API request must carry the per-session token the server writes into the
page (header X-IOP-Token), so other web pages cannot read or write maps
through this port. Only .map files directly inside map/multi are reachable.
Encoding and validation reuse iop_map_format.MapDocument, the same code the
Tk editor uses; a save is re-decoded before it replaces the file.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import struct
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import iop_hills
import iop_maps
import iop_sprites
import iop_stamps
from iop_map_format import MapDocument, HIGH_GROUND_FLAG

PORT = 47232
PAGE = 'IOPMapEditor.html'
TOKEN_MARK = '__IOP_TOKEN__'
MAX_BODY = 64 * 1024 * 1024
MIN_SIZE, MAX_SIZE = 32, 128
CATEGORIES = ('일반', '언덕', '입구', '미분류')


def resource_dir():
    return Path(getattr(sys, '_MEIPASS', Path(__file__).parent)) / 'map_editor_web'


def maps_folder(game_dir):
    return Path(game_dir) / 'map' / 'multi'


def safe_map_path(folder, name):
    """A plain file name ending in .map inside map/multi, or None."""
    if not name or not isinstance(name, str):
        return None
    name = name.strip()
    if (not name.lower().endswith('.map') or len(name) > 120 or any(c in name for c in '/\\:*?"<>|')
            or name.startswith('.') or name != Path(name).name):
        return None
    path = (Path(folder) / name).resolve()
    try:
        path.relative_to(Path(folder).resolve())
    except ValueError:
        return None
    return path


def tile_key(theme, raw):
    return hashlib.sha256(bytes([theme]) + bytes(raw)).hexdigest()


def b64(data):
    return base64.b64encode(bytes(data)).decode('ascii')


def unb64(text, limit=MAX_BODY):
    if not isinstance(text, str) or len(text) > limit * 2:
        raise ValueError('잘못된 데이터 형식입니다.')
    return base64.b64decode(text.encode('ascii'), validate=True)


def doc_to_json(doc, name, palette, categories):
    """Everything the page needs to draw and edit, plus what save must keep."""
    raw_tiles = b''.join(doc.tiles)
    return {
        'name': name,
        'hash': getattr(doc, 'disk_hash', None),
        'version': doc.version,
        'theme': doc.theme,
        'themeName': iop_maps.THEMES.get(doc.theme, ('?',))[0],
        'players': doc.players,
        'width': doc.width,
        'height': doc.height,
        'starts': [list(p) for p in doc.starts],
        'resources': [list(p) for p in doc.resources],
        'objects': b64(doc.objects),
        'fixed': b64(doc.fixed),
        'extra': b64(doc.extra),
        'grid': b64(struct.pack(f'<{len(doc.grid)}H', *doc.grid)),
        'tiles': b64(raw_tiles),
        'tileCount': len(doc.tiles),
        'minimap': b64(doc.minimap),
        'palette': b64(b''.join(bytes(c) for c in palette)),
        'categories': [categories.get(tile_key(doc.theme, t), '미분류') for t in doc.tiles],
        'highFlag': HIGH_GROUND_FLAG,
    }


def json_to_doc(data):
    """Rebuild a MapDocument from the page's JSON and validate it."""
    if not isinstance(data, dict):
        raise ValueError('맵 데이터 형식이 올바르지 않습니다.')
    doc = MapDocument()
    doc.path = None
    doc.version = int(data['version'])
    width, height = int(data['width']), int(data['height'])
    players = int(data['players'])
    if doc.version not in (1, 2) or not 2 <= players <= 8:
        raise ValueError('지원하지 않는 맵 버전 또는 인원입니다.')
    if not (1 <= width <= 256 and 1 <= height <= 256):
        raise ValueError('맵 크기가 지원 범위를 벗어났습니다.')
    fixed = bytearray(unb64(data['fixed']))
    if len(fixed) != 38:
        raise ValueError('맵 헤더가 손상되었습니다.')
    starts = [tuple(int(v) for v in p) for p in data['starts']]
    if len(starts) != players:
        raise ValueError('시작 위치 개수가 인원 수와 다릅니다.')
    # Unused slots keep their bytes; slots freed by fewer players become the
    # game's empty value (-1, -1), as in the installed maps.
    for i in range(players, fixed[5]):
        struct.pack_into('<hh', fixed, 6 + 4 * i, -1, -1)
    fixed[5] = players
    doc.fixed = bytes(fixed)
    doc.theme, doc.players = fixed[4], players
    doc.starts = starts
    doc.resources = [tuple(int(v) for v in p) for p in data['resources']]
    doc.objects = unb64(data['objects'])
    if len(doc.objects) % 8:
        raise ValueError('장식 데이터 길이가 올바르지 않습니다.')
    doc.extra = unb64(data['extra']) if doc.version == 2 else b''
    if doc.version == 2 and len(doc.extra) != 8:
        raise ValueError('맵 v2 보존 필드가 손상되었습니다.')
    doc.width, doc.height = width, height
    grid = unb64(data['grid'])
    if len(grid) != 2 * width * height:
        raise ValueError('지형 격자 크기가 맵 크기와 다릅니다.')
    doc.grid = list(struct.unpack(f'<{width * height}H', grid))
    tiles = unb64(data['tiles'])
    if not tiles or len(tiles) % 1026 or len(tiles) // 1026 > 30000:
        raise ValueError('타일 데이터가 손상되었습니다.')
    doc.tiles = [tiles[i:i + 1026] for i in range(0, len(tiles), 1026)]
    if max(doc.grid) >= len(doc.tiles):
        raise ValueError('존재하지 않는 타일 번호가 있습니다.')
    doc.minimap = bytearray(unb64(data['minimap']))
    if len(doc.minimap) != width * height:
        raise ValueError('미니맵 크기가 맵 크기와 다릅니다.')
    doc.bounds = struct.pack('<4i', 0, 0, width - 1, height - 1)
    doc.validate_positions(doc.starts)
    doc.validate_positions(doc.resources)
    for i in range(0, len(doc.objects), 8):
        x, y = struct.unpack_from('<HH', doc.objects, i)
        if not (x < width and y < height):
            raise ValueError('장식 좌표가 맵 범위를 벗어났습니다.')
    MapDocument.decode(doc.encode())  # same check the game loader layout needs
    return doc


# Base ground looks of the installed multi maps (new-map dialog).
TERRAINS = {'purple': '보랏빛 지형', 'snow': '스노우 지형', 'dirt': '흙색 지형', 'desert': '사막 지형'}


def terrain_of(doc, palette):
    """Classify a map by its most used ground tile: theme 1 is purple, theme 2
    desert; theme 0 holds both grey snow and dark brown dirt ground."""
    if doc.theme == 1:
        return 'purple'
    if doc.theme == 2:
        return 'desert'
    if not palette:
        return 'dirt'
    pixels = doc.tiles[doc.ground_tile()][2:]
    r, g, b = (sum(palette[i][c] for i in pixels) / len(pixels) for c in range(3))
    return 'snow' if min(r, g, b) > 110 and max(r, g, b) - min(r, g, b) < 40 else 'dirt'


# Tiles a new map starts with: the installed maps reach 2,261, keep below that.
MAX_NEW_TILES = 2048


def theme_tiles(docs, limit=MAX_NEW_TILES):
    """Tile set for an empty map of one terrain, gathered from all installed
    maps of that terrain: graphics ordered by how often the maps use them,
    and the most used plain ground first."""
    from collections import Counter
    use, ground = Counter(), Counter()
    for doc in docs:
        for tile, n in Counter(doc.grid).items():
            raw = doc.tiles[tile]
            use[raw] += n
            if doc.is_ground(tile) and not int.from_bytes(raw[:2], 'little') & HIGH_GROUND_FLAG:
                ground[raw] += n
    if not ground:
        raise ValueError('이 지형의 맵에 평지 타일이 없습니다.')
    first = ground.most_common(1)[0][0]
    tiles = [first] + [raw for raw, _ in use.most_common() if raw != first]
    return tiles[:limit]


def new_map(base, width, height, players, tiles=None):
    """Flat map with the base map's theme and header. The tile set is the
    base map's, or `tiles` (ground first) when given."""
    if not (MIN_SIZE <= width <= MAX_SIZE and MIN_SIZE <= height <= MAX_SIZE):
        raise ValueError(f'맵 크기는 {MIN_SIZE}~{MAX_SIZE} 사이로 지정하세요.')
    if not 2 <= players <= 8:
        raise ValueError('인원은 2~8명으로 지정하세요.')
    doc = MapDocument()
    doc.path = None
    doc.version, doc.theme = base.version, base.theme
    doc.extra = base.extra
    fixed = bytearray(base.fixed)
    fixed[5] = players
    doc.fixed = bytes(fixed)
    doc.players = players
    doc.width, doc.height = width, height
    doc.tiles = list(tiles) if tiles else list(base.tiles)
    ground = 0 if tiles else base.ground_tile()
    doc.grid = [ground] * (width * height)
    color = doc.tiles[ground][2 + 16 * 32 + 16]
    doc.minimap = bytearray([color]) * (width * height)
    doc.bounds = struct.pack('<4i', 0, 0, width - 1, height - 1)
    doc.resources, doc.objects = [], b''
    # Starts evenly spaced on a ring, 12 tiles in from the edges.
    import math
    cx, cy = (width - 1) / 2, (height - 1) / 2
    rx, ry = max(6, cx - 12), max(6, cy - 12)
    doc.starts = []
    for i in range(players):
        a = 2 * math.pi * i / players + math.pi / 4
        doc.starts.append((int(round(cx + rx * math.cos(a))), int(round(cy + ry * math.sin(a)))))
    for i in range(8):
        x, y = doc.starts[i] if i < players else (-1, -1)
        struct.pack_into('<hh', fixed, 6 + 4 * i, x, y)
    doc.fixed = bytes(fixed)
    MapDocument.decode(doc.encode())
    return doc


class Api:
    """Request logic, kept apart from HTTP so it can be tested directly."""

    def __init__(self, game_dir, categories=None, save_categories=None, stamp_dir=None):
        self.game_dir = game_dir
        self.categories = categories if categories is not None else {}
        self.save_categories = save_categories or (lambda: None)
        self.lock = threading.Lock()
        self._sprites = None
        self.stamps = iop_stamps.StampStore(stamp_dir, self.folder)
        self.hills = iop_hills.HillLibrary(self.folder)
        self._hill_lock = threading.Lock()
        self.warm_hills = False  # the editor server learns a theme's hills as soon as a map opens

    def hill_styles(self, theme):
        with self._hill_lock:  # one learning pass per theme even with parallel requests
            return self.hills.get(theme)

    def _warm_hills(self, theme):
        if not self.warm_hills:
            return
        threading.Thread(target=lambda: self._safe(self.hill_styles, theme), daemon=True).start()

    @staticmethod
    def _safe(fn, *args):
        try:
            fn(*args)
        except Exception:  # warming is best effort; the real request reports errors
            pass

    def sprites(self, doc):
        """Resource and decoration images from data/Rsi.res ({} if unavailable)."""
        root = self.game_dir()
        if self._sprites is None or self._sprites[0] != root:
            self._sprites = (root, iop_sprites.SpriteLibrary(root))
        ids = [iop_sprites.RESOURCE_OBJECT]
        ids += [struct.unpack_from('<I', doc.objects, i)[0] for i in range(4, len(doc.objects) - 3, 8)]
        with self.lock:
            return self._sprites[1].as_json(ids)

    def folder(self):
        if not self.game_dir():
            raise ValueError('런처에서 게임 폴더를 먼저 지정하세요.')
        folder = maps_folder(self.game_dir())
        if not folder.is_dir():
            raise ValueError(f'맵 폴더가 없습니다: {folder}')
        return folder

    def list_maps(self):
        items, palettes = [], {}
        for path in sorted(self.folder().glob('*.map'), key=lambda p: p.name.lower()):
            try:
                doc = MapDocument.load(path)
                if doc.theme not in palettes:
                    try:
                        palettes[doc.theme] = iop_maps.read_palette(self.game_dir(), doc.theme)
                    except (OSError, ValueError):
                        palettes[doc.theme] = None
                items.append({'name': path.name, 'width': doc.width, 'height': doc.height,
                              'players': doc.players, 'theme': doc.theme,
                              'themeName': iop_maps.THEMES.get(doc.theme, ('?',))[0],
                              'terrain': terrain_of(doc, palettes[doc.theme]), 'tiles': len(doc.tiles),
                              'mtime': int(path.stat().st_mtime * 1000)})
            except Exception as exc:
                items.append({'name': path.name, 'error': str(exc)})
        return {'folder': str(self.folder()), 'maps': items}

    def _path(self, name):
        path = safe_map_path(self.folder(), name)
        if path is None:
            raise ValueError('맵 이름은 map/multi 안의 .map 파일 이름만 사용할 수 있습니다.')
        return path

    def get_map(self, name):
        path = self._path(name)
        if not path.is_file():
            raise FileNotFoundError(name)
        doc = MapDocument.load(path)
        palette = iop_maps.read_palette(self.game_dir(), doc.theme)
        result = doc_to_json(doc, path.name, palette, self.categories)
        result['terrain'] = terrain_of(doc, palette)
        result['sprites'] = self.sprites(doc)
        top, _ = iop_hills.top_mask(doc)
        result['hillTop'] = b64(bytes(top))
        self._warm_hills(doc.theme)
        return result

    def put_map(self, name, data, overwrite=False):
        path = self._path(name)
        doc = json_to_doc(data)
        with self.lock:
            if path.exists():
                if not overwrite:
                    raise FileExistsError(path.name)
                current = hashlib.sha256(path.read_bytes()).hexdigest()
                loaded = data.get('hash')
                if loaded and loaded != current and data.get('name') == path.name:
                    raise ValueError('다른 프로그램에서 이 맵이 바뀌었습니다. 다시 열어 확인한 뒤 저장하세요.')
                doc.path, doc.disk_hash = path, current
                doc.save(path, overwrite=True)
            else:
                doc.save(path)
        return {'ok': True, 'name': path.name, 'hash': doc.disk_hash,
                'warnings': doc.spawn_issues(), 'backup': path.name + '.bak' if overwrite else None}

    def new(self, data):
        size = int(data['width']), int(data['height']), int(data['players'])
        terrain = data.get('terrain')
        if terrain:
            # every installed map of that terrain gives tiles; the one with the
            # most tiles gives the header
            if terrain not in TERRAINS:
                raise ValueError('지형은 ' + ', '.join(TERRAINS.values()) + ' 중 하나입니다.')
            docs = []
            for item in self.list_maps()['maps']:
                if item.get('terrain') == terrain:
                    docs.append(MapDocument.load(self._path(item['name'])))
            if not docs:
                raise ValueError(f'{TERRAINS[terrain]} 맵이 게임 폴더에 없어 타일을 가져올 수 없습니다.')
            base = max(docs, key=lambda d: len(d.tiles))
            doc = new_map(base, *size, tiles=theme_tiles(docs))
        else:
            base = MapDocument.load(self._path(data.get('base')))
            doc = new_map(base, *size)
            terrain = None
        palette = iop_maps.read_palette(self.game_dir(), doc.theme)
        result = doc_to_json(doc, '', palette, self.categories)
        result['terrain'] = terrain or terrain_of(doc, palette)
        result['hash'] = None
        result['sprites'] = self.sprites(doc)
        result['hillTop'] = b64(bytes(doc.width * doc.height))
        self._warm_hills(doc.theme)
        return result

    def set_category(self, data):
        category = data.get('category')
        if category not in CATEGORIES:
            raise ValueError('분류는 일반/언덕/입구/미분류 중 하나입니다.')
        raw = unb64(data.get('tile', ''), 2048)
        if len(raw) != 1026:
            raise ValueError('타일 데이터가 올바르지 않습니다.')
        key = tile_key(int(data['theme']), raw)
        with self.lock:
            if category == '미분류':
                self.categories.pop(key, None)
            else:
                self.categories[key] = category
            self.save_categories()
        return {'ok': True}


def make_handler(api, token):
    page_dir = resource_dir()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # windowed launcher: no stderr
            pass

        def _send(self, code, body=b'', ctype='text/plain; charset=utf-8'):
            if isinstance(body, str):
                body = body.encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            if body and self.command != 'HEAD':
                self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj, ensure_ascii=False), 'application/json; charset=utf-8')

        def _authorized(self):
            host = (self.headers.get('Host') or '').split(':')[0]
            return (host in ('127.0.0.1', 'localhost')
                    and secrets.compare_digest(self.headers.get('X-IOP-Token', ''), token))

        def _body(self):
            length = int(self.headers.get('Content-Length') or 0)
            if not 0 < length <= MAX_BODY:
                raise ValueError('요청 크기가 올바르지 않습니다.')
            return json.loads(self.rfile.read(length).decode('utf-8'))

        def _api(self, fn):
            if not self._authorized():
                return self._json({'error': '권한이 없습니다. 런처에서 에디터를 다시 여세요.'}, 403)
            try:
                return self._json(fn())
            except FileExistsError as exc:
                return self._json({'error': f'같은 이름의 맵이 있습니다: {exc}', 'exists': True}, 409)
            except FileNotFoundError as exc:
                return self._json({'error': f'맵을 찾을 수 없습니다: {exc}'}, 404)
            except (ValueError, KeyError, TypeError, struct.error, json.JSONDecodeError) as exc:
                return self._json({'error': str(exc) or type(exc).__name__}, 400)
            except Exception as exc:  # keep the server alive; report to the page
                return self._json({'error': f'{type(exc).__name__}: {exc}'}, 500)

        def do_GET(self):
            url = urlparse(self.path)
            query = parse_qs(url.query)
            if url.path == '/api/ping':
                return self._api(lambda: {'ok': True})
            if url.path == '/api/maps':
                return self._api(api.list_maps)
            if url.path == '/api/map':
                return self._api(lambda: api.get_map(query.get('name', [''])[0]))
            if url.path == '/api/hills':
                return self._api(lambda: api.hill_styles(int(query.get('theme', ['-1'])[0])))
            if url.path == '/api/stamps':
                return self._api(lambda: api.stamps.list(int(query.get('theme', ['-1'])[0])))
            if url.path in ('/', '/' + PAGE):
                host = (self.headers.get('Host') or '').split(':')[0]
                if host not in ('127.0.0.1', 'localhost'):
                    return self._send(403, 'forbidden')
                html = (page_dir / PAGE).read_text(encoding='utf-8').replace(TOKEN_MARK, token)
                return self._send(200, html, 'text/html; charset=utf-8')
            return self._send(404, 'not found')

        do_HEAD = do_GET

        def do_PUT(self):
            url = urlparse(self.path)
            query = parse_qs(url.query)
            if url.path == '/api/map':
                return self._api(lambda: api.put_map(query.get('name', [''])[0], self._body(),
                                                     query.get('overwrite', ['0'])[0] == '1'))
            if url.path == '/api/category':
                return self._api(lambda: api.set_category(self._body()))
            if url.path == '/api/stamp':
                return self._api(lambda: api.stamps.save(self._body()))
            return self._send(404, 'not found')

        def do_DELETE(self):
            url = urlparse(self.path)
            if url.path == '/api/stamp':
                return self._api(lambda: api.stamps.delete(parse_qs(url.query).get('id', [''])[0]))
            return self._send(404, 'not found')

        def do_POST(self):
            if urlparse(self.path).path == '/api/new':
                return self._api(lambda: api.new(self._body()))
            return self._send(404, 'not found')

    return Handler


class EditorServer:
    """Daemon-thread HTTP server owned by the launcher process."""

    def __init__(self, api, port=PORT):
        self.api = api
        api.warm_hills = True
        self.port = port
        self.token = secrets.token_urlsafe(24)
        self.httpd = None

    @property
    def url(self):
        return f'http://127.0.0.1:{self.port}/{PAGE}'

    def start(self):
        if self.httpd:
            return self.url
        last = None
        for port in range(self.port, self.port + 10):  # skip a port already in use
            try:
                self.httpd = ThreadingHTTPServer(('127.0.0.1', port), make_handler(self.api, self.token))
                self.port = port
                break
            except OSError as exc:
                last = exc
        if not self.httpd:
            raise OSError(f'맵 에디터 서버를 시작하지 못했습니다: {last}')
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self.url

    def open(self):
        webbrowser.open(self.start())

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
