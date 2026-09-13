import struct
import unittest
import tempfile
from pathlib import Path
from iop_map_format import MapDocument


def fixture(version=2):
    header=bytearray(38)
    struct.pack_into('<HBB',header,2,version,0,2)
    struct.pack_into('<4h',header,6,0,0,2,1)
    header+=struct.pack('<H',1)
    if version==2:header+=struct.pack('<H',1)+b'ABCDEFGH'
    header+=struct.pack('<HH',1,1)
    if version==2:header+=struct.pack('<HHI',1,0,0x120001)
    struct.pack_into('<H',header,0,len(header))
    return bytes(header)+struct.pack('<III6I',3,2,2,0,1,1,0,0,1)+b'\x01\0'+bytes([2])*1024+b'\x08\0'+bytes([3])*1024+bytes(range(6))+struct.pack('<4I',0,0,2,1)


class NativeMaps(unittest.TestCase):
    def test_overwrite_backup_and_external_change(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'existing.map';path.write_bytes(fixture())
            doc=MapDocument.load(path);doc.paint(0,0,1)
            doc.save(path,overwrite=True)
            self.assertEqual(path.read_bytes(),doc.encode())
            self.assertEqual(Path(str(path)+'.bak').read_bytes(),fixture())
            doc.save(path,overwrite=True)
            self.assertEqual(len(list(Path(folder).glob('*.map'))),1)
            path.write_bytes(fixture())
            with self.assertRaises(ValueError):doc.save(path,overwrite=True)
            self.assertEqual(path.read_bytes(),fixture())

    def test_save_as_preserves_existing(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'existing.map';path.write_bytes(fixture())
            doc=MapDocument.load(path)
            with self.assertRaises(FileExistsError):doc.save(path)
            target=Path(folder)/'copy.map';doc.save(target)
            self.assertEqual(doc.path,target)
            self.assertEqual(path.read_bytes(),fixture())

    def test_both_versions_roundtrip(self):
        for v in (1,2):
            raw=fixture(v);self.assertEqual(MapDocument.decode(raw).encode(),raw)

    def test_resource_change_relocates_header_preserving_terrain(self):
        d=MapDocument.decode(fixture());d.resources.append((2,0))
        edited=MapDocument.decode(d.encode())
        self.assertEqual(edited.resources,[(1,1),(2,0)])
        self.assertEqual(edited.grid,MapDocument.decode(fixture()).grid)
        self.assertEqual(edited.objects,d.objects)

    def test_tile_paint_is_x_major_and_undo_restores(self):
        d=MapDocument.decode(fixture());state=d.snapshot();d.paint(1,1,1)
        self.assertEqual(d.grid[3],1)
        self.assertEqual(MapDocument.decode(d.encode()).grid[3],1)
        d.restore(state);self.assertEqual(d.encode(),fixture())

    def test_stamp_keeps_tile_properties_and_preview(self):
        d=MapDocument.decode(fixture());patch=d.copy_region(0,0,0,1);d.stamp(2,0,patch)
        self.assertEqual(d.grid[4:6],d.grid[:2])
        self.assertEqual(d.minimap[2],d.minimap[0])
        self.assertEqual(d.minimap[5],d.minimap[3])
        with self.assertRaises(ValueError):d.stamp(-1,0,patch)

    def test_truncation_bad_indices_and_duplicates_rejected(self):
        with self.assertRaises(ValueError):MapDocument.decode(fixture()[:-1])
        d=MapDocument.decode(fixture());d.resources.append(d.resources[0])
        with self.assertRaises(ValueError):d.encode()
        data=bytearray(fixture());offset=struct.unpack_from('<H',data)[0]
        struct.pack_into('<I',data,offset+12,50)
        with self.assertRaises(ValueError):MapDocument.decode(data)

    def test_blank_clears_resources_and_objects(self):
        d=MapDocument.decode(fixture());d.blank(1);r=MapDocument.decode(d.encode())
        self.assertEqual(r.grid,[0]*6);self.assertEqual(r.resources,[]);self.assertEqual(r.objects,b'')

    def test_ground_classification(self):
        d=MapDocument.decode(fixture())
        self.assertTrue(d.is_ground(0));self.assertFalse(d.is_ground(1))
        self.assertEqual(d.ground_tile(),0)
