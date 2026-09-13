import json
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, Mock

from iop_map_editor import MapEditor
from iop_map_format import MapDocument
from tests.test_map_format import fixture


class EditorUI(unittest.TestCase):
    def test_save_and_persistent_tile_labels(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'current.map';path.write_bytes(fixture())
            config=Path(folder)/'config.json'
            launcher=SimpleNamespace(cfg={},server=SimpleNamespace(alive=False),
                _launch_pending=False,_game_process=None,_refresh_maps=Mock(),
                gp=lambda *args:folder)
            launcher.save_network_config=lambda:config.write_text(json.dumps(launcher.cfg))
            root=tk.Tk();root.withdraw()
            try:
                editor=MapEditor(root,launcher)
                editor.doc=MapDocument.load(path)
                editor.palette=[(v,v,v) for v in range(256)]
                for i in range(2):editor.tiles.insert('end',editor.tile_label(i))
                editor.tiles.selection_set(0);editor.select_tile()
                editor.category.set('입구');editor.label_tile()
                launcher.cfg=json.loads(config.read_text())
                self.assertIn('입구',editor.tile_label(0))
                editor.doc.tiles.reverse()
                self.assertIn('입구',editor.tile_label(1))
                editor.doc.tiles.reverse()
                editor.doc.paint(0,0,1);editor.dirty=True
                editor.zoom.set(16)
                editor.erase(SimpleNamespace(x=0,y=0))
                self.assertEqual(editor.doc.tile_id(0,0),0)
                editor.undo()
                self.assertEqual(editor.doc.tile_id(0,0),1)
                with patch.object(editor.doc,'spawn_issues',return_value=[]), patch('iop_map_editor.filedialog.asksaveasfilename') as dialog, patch('iop_map_editor.messagebox.showerror') as error:
                    editor.save()
                    dialog.assert_not_called();error.assert_not_called()
                    self.assertFalse(editor.dirty)
                    self.assertEqual(MapDocument.load(path).grid[0],1)
                    target=Path(folder)/'new.map';dialog.return_value=str(target)
                    editor.save(True)
                    self.assertEqual(editor.doc.path,target)
                    editor.save()
                    self.assertEqual(dialog.call_count,1)
            finally:root.destroy()
