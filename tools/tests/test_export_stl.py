"""STL再読込失敗・書込失敗が既存ファイルを壊さないことを独立に検査。"""
import contextlib
import io
from pathlib import Path
import sys
import tempfile
import types
import unittest
import warnings
from unittest.mock import patch

import numpy as np
import trimesh

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hardware/src"))
import lib


class ExportBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.directory = Path(self.folder.name)
        self.path = self.directory / "part.stl"
        self.previous = trimesh.creation.box((3, 4, 5)).export(file_type="stl")
        self.path.write_bytes(self.previous)
        self.directory_patch = patch.object(lib, "STL_DIR", self.directory)
        self.directory_patch.start()

    def tearDown(self):
        self.directory_patch.stop()
        self.folder.cleanup()

    def export(self, solid):
        with contextlib.redirect_stdout(io.StringIO()), warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return lib.export(solid, "part")

    def assert_previous_intact(self):
        self.assertEqual(self.path.read_bytes(), self.previous)
        self.assertEqual(sorted(p.name for p in self.directory.iterdir()), ["part.stl"])

    def test_empty_nonfinite_and_inward_meshes_preserve_previous_output(self):
        empty = trimesh.Trimesh(vertices=np.empty((0, 3)), faces=np.empty((0, 3), dtype=int), process=False)
        inward = trimesh.creation.box()
        inward.invert()
        nan_mesh, inf_mesh = trimesh.creation.box(), trimesh.creation.box()
        nan_mesh.vertices[0, 0], inf_mesh.vertices[0, 0] = np.nan, np.inf
        for label, mesh in (("empty", empty), ("inward", inward), ("NaN", nan_mesh), ("infinity", inf_mesh)):
            with self.subTest(label=label), patch.object(lib, "to_trimesh", return_value=mesh):
                with self.assertRaises(RuntimeError):
                    self.export(lib.box(1, 1, 1))
                self.assert_previous_intact()

    def test_closed_source_rejected_when_float32_stl_collapses(self):
        # 元のfloat64箱は正常。STLのfloat32では大きな座標の1mm差が消える。
        mesh = trimesh.creation.box()
        mesh.apply_translation([1e8, 0, 0])
        self.assertTrue(mesh.is_watertight)
        self.assertTrue(mesh.is_winding_consistent)
        self.assertGreater(mesh.volume, 0)
        with patch.object(lib, "to_trimesh", return_value=mesh):
            with self.assertRaises(RuntimeError):
                self.export(lib.box(1, 1, 1))
        self.assert_previous_intact()

    def test_mirrored_manifold_exports_outward_normals(self):
        solid = lib.box(2, 3, 4).translate([7, 3, -2]).mirror([0, 1, 0])
        self.export(solid)
        mesh = trimesh.load(self.path, force="mesh")
        self.assertTrue(mesh.is_watertight)
        self.assertTrue(mesh.is_winding_consistent)
        self.assertAlmostEqual(mesh.volume, 24)
        np.testing.assert_allclose(mesh.bounds.mean(axis=0), [7, -3, -2])

    def test_disjoint_closed_solids_remain_allowed_by_generic_export(self):
        # 共通出力関数は複数部品を禁止しない。用途側の1体制約と区別する。
        self.export(lib.box(2, 2, 2) + lib.box(2, 2, 2).translate([5, 0, 0]))
        mesh = trimesh.load(self.path, force="mesh")
        self.assertEqual(len(mesh.split(only_watertight=False)), 2)
        self.assertAlmostEqual(mesh.volume, 16)

    def test_closed_internal_void_remains_valid(self):
        # 閉じた内壁は外壁と面で接続しない。split数だけで拒否すると正常な中空体も壊す。
        self.export(lib.box(10, 10, 10) - lib.box(6, 6, 6))
        mesh = trimesh.load(self.path, force="mesh")
        self.assertTrue(mesh.is_watertight)
        self.assertAlmostEqual(mesh.volume, 784)
        self.assertFalse(mesh.contains([[0, 0, 0]])[0])

    def test_closed_positive_mesh_with_wrong_cavity_volume_is_rejected(self):
        # 内壁まで外向きにしたメッシュもwatertight/winding/正体積だけなら通る。
        wrong = trimesh.util.concatenate([trimesh.creation.box((10, 10, 10)),
                                          trimesh.creation.box((6, 6, 6))])
        self.assertTrue(wrong.is_watertight)
        self.assertTrue(wrong.is_winding_consistent)
        self.assertAlmostEqual(wrong.volume, 1216)
        original = lib.box(10, 10, 10) - lib.box(6, 6, 6)
        with patch.object(lib, "to_trimesh", return_value=wrong):
            with self.assertRaisesRegex(RuntimeError, "Manifold体積"):
                self.export(original)
        self.assert_previous_intact()

    def test_duplicate_and_degenerate_faces_are_removed_without_flipping_void(self):
        solid = lib.box(10, 10, 10) - lib.box(6, 6, 6)
        raw = solid.to_mesh()
        faces = np.asarray(raw.tri_verts)
        # Manifold→Trimesh境界で生じた重複面/ゼロ面を再現する。
        raw_with_fragments = types.SimpleNamespace(vert_properties=np.asarray(raw.vert_properties),
            tri_verts=np.vstack([faces, faces[0], [faces[0, 0]] * 3]))
        source = types.SimpleNamespace(to_mesh=lambda: raw_with_fragments, volume=lambda: 784.)
        self.export(source)
        mesh = trimesh.load(self.path, force="mesh")
        self.assertTrue(mesh.is_watertight)
        self.assertAlmostEqual(mesh.volume, 784)

    def test_partial_write_failure_preserves_previous_and_cleans_temporary_file(self):
        original = Path.write_bytes
        def fail_after_partial_write(path, data):
            original(path, data[:64])
            raise OSError("simulated disk full after partial write")
        with patch.object(Path, "write_bytes", fail_after_partial_write):
            with self.assertRaisesRegex(OSError, "simulated disk full"):
                self.export(lib.box(2, 3, 4))
        self.assert_previous_intact()

    def test_replace_failure_preserves_previous_and_cleans_temporary_file(self):
        with patch("os.replace", side_effect=OSError("simulated replace failure")):
            with self.assertRaisesRegex(OSError, "simulated replace failure"):
                self.export(lib.box(2, 3, 4))
        self.assert_previous_intact()


if __name__ == "__main__":
    unittest.main()
