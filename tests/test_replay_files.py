import os
import struct
import tempfile
import unittest
from pathlib import Path

import sys
import types
try:
    import tkinter  # noqa: F401
except ImportError:  # headless CI: the panel's pure helpers do not need Tk
    sys.modules['tkinter'] = types.ModuleType('tkinter')

import iop_replay as rp
import iop_replay_patch as patch
from iop_replay_control import ReplayState, vision_options

ORIGINAL = Path('game-assets/v0.019/iop.exe').read_bytes()


def make_block(map_path='map\\multi\\test.map'):
    block = bytearray(rp.BLOCK_LEN)
    block[rp.B_COUNT] = 2
    block[rp.B_TYPES:rp.B_TYPES + 3] = bytes([4, 1, 0])
    block[rp.B_RACES:rp.B_RACES + 2] = bytes([1, 3])
    block[rp.B_NAMES:rp.B_NAMES + 6] = '홍길동'.encode('cp949')
    struct.pack_into('<II', block, rp.B_TEAMIDX, 0, 1)
    encoded = map_path.encode('cp949')
    block[rp.B_MAP:rp.B_MAP + len(encoded)] = encoded
    return bytes(block)


def make_raw(ticks=(0, 1, 2), complete=True, map_path='map\\multi\\test.map'):
    header = bytearray(rp.HEADER_SIZE)
    struct.pack_into('<4s8I', header, 0, b'IOPR', 1, 1, 11, 22, 0, 15, 0, ticks[0])
    header[rp.BLOCK_OFFSET:rp.BLOCK_OFFSET + rp.BLOCK_LEN] = make_block(map_path)
    body = bytearray()
    for tick in ticks:
        body += b'\x01' + struct.pack('<III', tick, tick * 3, 0)
        body += b'\x02\x00' + bytes(rp.PACKET_LEN)
    if complete:
        body += b'\x04' + struct.pack('<I', ticks[-1])
    return bytes(header + body)


class ReplayFiles(unittest.TestCase):
    def test_parse(self):
        raw = rp.parse_raw(make_raw(ticks=(0, 1, 2, 30)))
        self.assertEqual((raw.mode, raw.tps, raw.start_tick, raw.end_tick, raw.commands), (1, 15, 0, 30, 4))
        self.assertTrue(raw.complete)
        self.assertEqual(raw.duration_seconds, 2.0)
        self.assertEqual([p['name'] for p in raw.players], ['홍길동', '컴퓨터 2'])
        self.assertEqual(raw.map_path, 'map\\multi\\test.map')

    def test_truncated_recording_stops_at_last_whole_record(self):
        data = make_raw(complete=False)
        raw = rp.parse_raw(data + b'\x02\x00\x01')
        self.assertFalse(raw.complete)
        self.assertEqual(raw.valid_length, len(data))
        self.assertEqual(raw.end_tick, 2)

    def test_pack_roundtrip_and_tamper(self):
        data = make_raw()
        blob = rp.pack({'raw_sha256': rp._sha(data), 'x': '한글'}, data, b'MAP')
        meta, raw, map_bytes = rp.unpack(blob)
        self.assertEqual((meta['x'], raw, map_bytes), ('한글', data, b'MAP'))
        with self.assertRaises(rp.ReplayError):
            rp.unpack(rp.pack({'raw_sha256': '0' * 64}, data))

    def test_map_path_rewrite(self):
        raw = rp.parse_raw(rp.with_map_path(make_raw(), 'map\\multi\\_replay_x.map'))
        self.assertEqual(raw.map_path, 'map\\multi\\_replay_x.map')

    def test_finalize_prepare_and_prune(self):
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp)
            (game / 'map' / 'multi').mkdir(parents=True)
            (game / 'map' / 'multi' / 'test.map').write_bytes(b'ORIGINAL MAP')
            (game / 'data').mkdir()
            (game / 'data' / 'Gweapon.res').write_bytes(b'weapons')
            (game / 'iop.exe').write_bytes(ORIGINAL)
            folder = rp.replay_dir(game)
            (folder / '_rec_00000001.iopraw').write_bytes(make_raw(complete=False))
            (folder / '_rec_00000002.iopraw').write_bytes(make_raw(ticks=(0,)) [:rp.HEADER_SIZE])  # empty battle
            produced = rp.finalize_pending(game, '0.041', keep_auto=5)
            self.assertEqual({p.parent.name for p in produced}, {'Replays', 'Auto'})
            self.assertEqual(list(folder.glob('*.iopraw')), [])
            meta = rp.read_meta(folder / rp.LAST_REPLAY)
            self.assertFalse(meta['complete'])
            self.assertIn('gweapon.res', meta['environment']['data'])
            listed = rp.list_replays(game)
            self.assertEqual(len(listed), 2)
            # balance changed + map removed -> warnings, embedded map is written
            (game / 'data' / 'Gweapon.res').write_bytes(b'changed')
            (game / 'map' / 'multi' / 'test.map').unlink()
            playback = rp.prepare_playback(game, folder / rp.LAST_REPLAY)
            self.assertTrue(any('gweapon.res' in w for w in playback.warnings))
            self.assertTrue(playback.temp_map.read_bytes() == b'ORIGINAL MAP')
            play = rp.parse_raw((folder / rp.PLAY_RAW).read_bytes())
            self.assertTrue(play.map_path.startswith('map\\multi\\_replay_'))
            self.assertEqual(patch.installed_mode((game / patch.PLAY_EXE).read_bytes()), 1)
            rp.cleanup_playback(game, playback)
            self.assertFalse((folder / rp.PLAY_RAW).exists())
            self.assertFalse(playback.temp_map.exists())
            for i in range(4):
                p = folder / rp.AUTO_DIR / f'x{i}.ioprep'
                p.write_bytes(b'x')
                os.utime(p, (i, i))
            self.assertEqual(rp.prune_auto(game, 2), 3)

    def test_controls(self):
        state = ReplayState(15, 100, 400)
        self.assertEqual(state.requested_tps(), 15)
        state.faster(); state.faster()
        self.assertEqual(state.factor, 2.0)
        self.assertEqual(state.requested_tps(), 30)
        for _ in range(10): state.faster()
        self.assertEqual(state.factor, 16.0)
        self.assertEqual(state.clock(250), '00:10 / 00:20')
        self.assertAlmostEqual(state.progress(250), 0.5)
        options = vision_options(rp.parse_players(make_block()), 0)
        self.assertEqual(options[0], (1, '전체 시야 (안개 없음)'))
        self.assertEqual(options[1], (2, '홍길동 (기록자) 시야'))

    def test_normalization_chains_strip_replay_section(self):
        from iop_assets import iop_is_canonical, normalized_iop, CANONICAL_IOP_SHA256
        import hashlib
        patched = patch.patched_bytes(ORIGINAL)
        self.assertEqual(hashlib.sha256(normalized_iop(patched)).hexdigest(), CANONICAL_IOP_SHA256)
        from iop_network import patched_bytes as network_bytes
        self.assertEqual(len(network_bytes(patched, '10.0.0.1')), len(patched))
        import iop_hotkeys
        out = iop_hotkeys.patch_exe(patched, {})
        self.assertEqual(patch.installed_mode(out), 0)
        import iop_hill_vision as hill
        both = hill.patched_bytes(patched)
        self.assertEqual(patch.normalize(both), hill.patched_bytes(ORIGINAL))
        self.assertEqual(hill.normalize(patch.normalize(both)), ORIGINAL)


if __name__ == '__main__':
    unittest.main()
