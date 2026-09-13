"""IOP MAP v1/v2, decoded against loaders 0x412960 and 0x427370."""
from pathlib import Path
import struct
import copy
import os
import tempfile
import hashlib
import shutil

MAX_BYTES = 32 * 1024 * 1024


class MapDocument:
    @classmethod
    def load(cls, path):
        path = Path(path)
        with path.open('rb') as f:
            data = f.read(MAX_BYTES + 1)
        obj = cls.decode(data)
        obj.path = path
        obj.disk_hash = hashlib.sha256(data).hexdigest()
        return obj

    @classmethod
    def decode(cls, data):
        if not 68 <= len(data) <= MAX_BYTES:
            raise ValueError('맵 파일 크기가 올바르지 않습니다.')
        self = cls()
        self.path = None
        self.version = struct.unpack_from('<H', data, 2)[0]
        self.theme, self.players = data[4:6]
        if self.version not in (1, 2) or self.theme > 2 or not 2 <= self.players <= 8:
            raise ValueError('멀티 MAP v1/v2 파일만 편집할 수 있습니다.')
        self.fixed = data[:38]
        self.starts = [struct.unpack_from('<hh', data, 6+4*i) for i in range(self.players)]
        count = struct.unpack_from('<H', data, 38)[0]
        tree_count = struct.unpack_from('<H', data, 40)[0] if self.version == 2 else 0
        if count > 1000 or tree_count > 1500:
            raise ValueError('자원/장식 개수가 게임의 저장 공간을 초과합니다.')
        offset = 40 if self.version == 1 else 50
        header = offset + 4*count + 8*tree_count
        if header+12 > len(data) or struct.unpack_from('<H', data)[0] != header:
            raise ValueError('맵 헤더 길이가 일치하지 않습니다.')
        self.extra = data[42:50] if self.version == 2 else b''
        self.resources = [struct.unpack_from('<HH', data, offset+4*i) for i in range(count)]
        self.objects = data[offset+4*count:header]
        self.width, self.height, tiles = struct.unpack_from('<III', data, header)
        if not (1 <= self.width <= 256 and 1 <= self.height <= 256 and 1 <= tiles <= 30000):
            raise ValueError('지원 범위 밖의 맵/타일 크기입니다.')
        cells = self.width*self.height
        if header+12+cells*5+tiles*1026+16 != len(data):
            raise ValueError('타일·격자·미니맵 블록 길이가 일치하지 않습니다.')
        offset = header+12
        self.grid = list(struct.unpack_from(f'<{cells}I', data, offset))
        if max(self.grid) >= tiles:
            raise ValueError('존재하지 않는 타일 번호입니다.')
        offset += cells*4
        self.tiles = [data[offset+i*1026:offset+(i+1)*1026] for i in range(tiles)]
        offset += tiles*1026
        self.minimap = bytearray(data[offset:offset+cells])
        self.bounds = data[-16:]
        self.validate_positions(self.starts)
        self.validate_positions(self.resources)
        return self

    def validate_positions(self, points):
        if any(not (0 <= x < self.width and 0 <= y < self.height) for x,y in points):
            raise ValueError('좌표가 맵 범위를 벗어났습니다.')

    def encode(self):
        self.validate_positions(self.starts)
        self.validate_positions(self.resources)
        if len(set(self.starts)) != len(self.starts):
            raise ValueError('시작 위치가 중복됩니다.')
        if len(self.resources) > 1000 or len(set(self.resources)) != len(self.resources):
            raise ValueError('자원은 중복 없이 최대 1,000개까지 배치할 수 있습니다.')
        header = bytearray(self.fixed)
        for i,(x,y) in enumerate(self.starts): struct.pack_into('<hh', header, 6+4*i, x,y)
        header += struct.pack('<H', len(self.resources))
        if self.version == 2:
            header += struct.pack('<H', len(self.objects)//8) + self.extra
        for point in self.resources: header += struct.pack('<HH', *point)
        header += self.objects
        if len(header) > 65535: raise ValueError('맵 헤더가 너무 큽니다.')
        struct.pack_into('<H', header, 0, len(header))
        return (bytes(header) + struct.pack('<III', self.width,self.height,len(self.tiles))
                + struct.pack(f'<{len(self.grid)}I', *self.grid) + b''.join(self.tiles)
                + bytes(self.minimap) + self.bounds)

    def save(self, path, overwrite=False):
        path = Path(path)
        if path.suffix.lower() != '.map': raise ValueError('.map 파일로 저장하세요.')
        data = self.encode()
        type(self).decode(data)
        if overwrite and path.exists():
            previous=path.read_bytes()
            if self.path and path.resolve()==self.path.resolve() and getattr(self,'disk_hash',None)!=hashlib.sha256(previous).hexdigest():
                raise ValueError('다른 프로그램에서 맵을 변경했습니다. 다시 열어 변경 내용을 확인하세요.')
            backup=path.with_name(path.name+'.bak')
            shutil.copy2(path,backup)
            fd,temp=tempfile.mkstemp(prefix=path.name+'.',suffix='.tmp',dir=path.parent)
            try:
                with os.fdopen(fd,'wb') as f:
                    f.write(data);f.flush();os.fsync(f.fileno())
                os.replace(temp,path)
            finally:
                if os.path.exists(temp):os.unlink(temp)
        else:
            with path.open('xb') as f: f.write(data)
        self.path = path
        self.disk_hash = hashlib.sha256(data).hexdigest()

    def tile_id(self, x, y):
        return self.grid[x*self.height+y]

    def paint(self, x, y, tile):
        self.validate_positions([(x,y)])
        if not 0 <= tile < len(self.tiles): raise ValueError('잘못된 타일 번호입니다.')
        self.grid[x*self.height+y] = tile
        # Preserve the game's palette index for reused tiles when possible.
        self.minimap[y*self.width+x] = self.tiles[tile][2+16*32+16]

    def stamp(self, x, y, patch):
        w,h,ids,colors = patch
        self.validate_positions([(x,y)])
        if x+w>self.width or y+h>self.height: raise ValueError('복사 영역이 맵 밖으로 나갑니다.')
        for dx in range(w):
            for dy in range(h):
                self.grid[(x+dx)*self.height+y+dy] = ids[dx*h+dy]
                self.minimap[(y+dy)*self.width+x+dx] = colors[dy*w+dx]

    def copy_region(self, x1,y1,x2,y2):
        x1,x2=sorted((x1,x2)); y1,y2=sorted((y1,y2))
        self.validate_positions([(x1,y1),(x2,y2)])
        w,h=x2-x1+1,y2-y1+1
        return (w,h,[self.tile_id(x,y) for x in range(x1,x2+1) for y in range(y1,y2+1)],
                [self.minimap[y*self.width+x] for y in range(y1,y2+1) for x in range(x1,x2+1)])

    def blank(self, tile):
        if not self.is_ground(tile):
            tile = self.ground_tile()
        self.resources=[]; self.objects=b''
        for x in range(self.width):
            for y in range(self.height): self.paint(x,y,tile)

    def is_ground(self, tile):
        flags = int.from_bytes(self.tiles[tile][:2], 'little')
        return bool(flags & 0xf003) and not bool(flags & 0x81c)

    def ground_tile(self):
        from collections import Counter
        for tile,_ in Counter(self.grid).most_common():
            if self.is_ground(tile): return tile
        for tile in range(len(self.tiles)):
            if self.is_ground(tile): return tile
        raise ValueError('이 맵에 평지 타일이 없습니다.')

    def spawn_issues(self):
        issues=[]
        for i,(x,y) in enumerate(self.starts,1):
            if x<5 or y<5 or x>=self.width-5 or y>=self.height-5:
                issues.append(f'P{i}: 맵 가장자리에서 5칸 이상 안쪽에 배치하세요.')
            elif any(not self.is_ground(self.tile_id(xx,yy)) for xx in range(x-4,x+5) for yy in range(y-4,y+5)):
                issues.append(f'P{i}: 시작 주변에 장애물이 있습니다. 시작 주변 평지 버튼을 사용하세요.')
            if any(abs(x-rx)<=4 and abs(y-ry)<=4 for rx,ry in self.resources):
                issues.append(f'P{i}: 시작 주변에 자원이 겹칩니다.')
        return issues

    def clear_spawns(self):
        if any(x<5 or y<5 or x>=self.width-5 or y>=self.height-5 for x,y in self.starts):
            raise ValueError('먼저 시작 위치를 맵 가장자리에서 5칸 이상 안쪽으로 옮기세요.')
        tile=self.ground_tile()
        for x,y in self.starts:
            for xx in range(x-4,x+5):
                for yy in range(y-4,y+5): self.paint(xx,yy,tile)
        occupied=lambda x,y:any(abs(x-sx)<=4 and abs(y-sy)<=4 for sx,sy in self.starts)
        self.resources=[(x,y) for x,y in self.resources if not occupied(x,y)]
        self.objects=b''.join(self.objects[i:i+8] for i in range(0,len(self.objects),8)
                              if not occupied(*struct.unpack_from('<HH',self.objects,i)))

    def snapshot(self):
        return (self.grid[:], self.minimap[:], self.starts[:], self.resources[:], self.objects)

    def restore(self, state):
        self.grid,self.minimap,self.starts,self.resources,self.objects=copy.deepcopy(state)

    def preview(self, palette, scale=4, region=None):
        """Render actual 32x32 tile pixels, not the embedded minimap."""
        rgb = [bytes(c) for c in palette]
        x0,y0,x1,y1=region or (0,0,self.width,self.height)
        samples = [min(31, int((i+.5)*32/scale)) for i in range(scale)]
        cached = {}
        for ident in {self.tile_id(x,y) for x in range(x0,x1) for y in range(y0,y1)}:
            pixels=self.tiles[ident][2:]
            cached[ident]=[b''.join(rgb[pixels[sy*32+sx]] for sx in samples) for sy in samples]
        rows=[]
        for y in range(y0,y1):
            tiles=[cached[self.tile_id(x,y)] for x in range(x0,x1)]
            rows.extend(b''.join(t[sy] for t in tiles) for sy in range(scale))
        return f'P6\n{(x1-x0)*scale} {(y1-y0)*scale}\n255\n'.encode()+b''.join(rows)

    def tile_ppm(self, ident, palette):
        return b'P6\n32 32\n255\n'+b''.join(bytes(palette[p]) for p in self.tiles[ident][2:])
