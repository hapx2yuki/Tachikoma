"""3MF誤材質・形状同一性・既存ファイル保護の回帰検査。実機操作なし。"""
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile

import trimesh

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import make_plates as P
from check_print_artifacts import audit, shape_distance


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class PrintArtifactsTests(unittest.TestCase):
    def test_same_volume_and_height_are_not_shape_identity(self):
        a = trimesh.creation.box(extents=[10, 10, 10])
        b = a.copy()
        b.apply_scale([2, 0.5, 1])
        self.assertAlmostEqual(a.volume, b.volume)
        self.assertAlmostEqual(a.extents[2], b.extents[2])
        self.assertGreater(shape_distance(a, b), 0.02)

    def test_vertex_order_translation_and_rotation_are_allowed(self):
        a = trimesh.creation.box(extents=[10, 12, 14])
        b = a.copy()
        b.apply_transform(trimesh.transformations.rotation_matrix(1.5707963267948966, [0, 0, 1]))
        b.apply_translation([20, 70, 5])
        self.assertLess(shape_distance(a, b), 0.02)

    def test_tpu_regeneration_preserves_source_and_rejects_overwrite(self):
        source = P.STL / "foot_pad.3mf"
        initial = digest(source)
        with tempfile.TemporaryDirectory() as directory:
            out = P.build_plate("foot_pad", Path(directory))
            report = audit(out)
            self.assertFalse(report["errors"], report)
            self.assertEqual(report["actual_quantities"], {"foot_pad.stl": 4})
            self.assertEqual(report["objects"][0]["material"], "TPU")
            self.assertEqual(report["objects"][0]["filament_slot"], 6)
            generated = digest(out)
            with self.assertRaises(FileExistsError):
                P.build_plate("foot_pad", Path(directory))
            self.assertEqual(generated, digest(out))
            # 実際に起きていたslot 1への退行を人工的に再現する。
            with zipfile.ZipFile(out) as archive:
                members = {n: archive.read(n) for n in archive.namelist()}
            root = ET.fromstring(members["Metadata/model_settings.config"])
            for m in root.findall("object/metadata"):
                if m.get("key") == "extruder":
                    m.set("value", "1")
            members["Metadata/model_settings.config"] = ET.tostring(root)
            bad = Path(directory) / "wrong-material.3mf"
            with zipfile.ZipFile(bad, "w") as archive:
                for name, content in members.items():
                    archive.writestr(name, content)
            self.assertTrue(any(e.startswith("MATERIAL:") for e in audit(bad)["errors"]))
        self.assertEqual(initial, digest(source))


if __name__ == "__main__":
    unittest.main()
