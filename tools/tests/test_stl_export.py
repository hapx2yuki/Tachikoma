import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import trimesh

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'hardware/src'))
import lib


class STLExportTests(unittest.TestCase):
    def test_roundtrip_valid_volume_is_written(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(lib,'STL_DIR',Path(tmp)):
            lib.export(lib.box(2,3,4),'part')
            mesh=trimesh.load(Path(tmp)/'part.stl',force='mesh')
            self.assertTrue(mesh.is_volume)
            self.assertAlmostEqual(mesh.volume,24.)

    def test_bad_roundtrip_cannot_replace_existing_output(self):
        # 1面欠落はSTL化自体が成功するので、保存後検証がなければ見逃す。
        broken=trimesh.creation.box();broken.update_faces([True]*11+[False])
        with tempfile.TemporaryDirectory() as tmp, patch.object(lib,'STL_DIR',Path(tmp)):
            target=Path(tmp)/'part.stl';target.write_bytes(b'previous verified file')
            with patch.object(lib,'to_trimesh',return_value=broken):
                with self.assertRaises(RuntimeError):lib.export(lib.box(2,3,4),'part')
            self.assertEqual(target.read_bytes(),b'previous verified file')

    def test_real_tibia_removes_zero_volume_faces_without_flipping_material(self):
        import make_leg
        solid=make_leg.tibia_link()
        with tempfile.TemporaryDirectory() as tmp, patch.object(lib,'STL_DIR',Path(tmp)):
            lib.export(solid,'tibia')
            mesh=trimesh.load(Path(tmp)/'tibia.stl',force='mesh')
            self.assertTrue(mesh.is_volume)
            self.assertEqual(len(mesh.split(only_watertight=False)),1)
            self.assertAlmostEqual(mesh.volume,solid.volume(),places=3)


if __name__=='__main__':unittest.main()
