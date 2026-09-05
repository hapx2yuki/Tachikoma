"""原型抽出で単位・XML表記・同名衝突によって元形状を壊さない検査。"""
import tempfile
import unittest
import zipfile
from pathlib import Path

from tools.extract_meshes import extract, part_name


def model(unit="millimeter", x=1):
    return f'''<model unit='{unit}' xmlns='http://schemas.microsoft.com/3dmanufacturing/core/2015/02'>
      <resources><object id='1' type='model'><mesh><vertices>
      <vertex z='0' x='0' y='0'/><vertex y='0' x='{x}' z='0'/>
      <vertex x='0' z='0' y='1'/><vertex x='0' y='0' z='1'/>
      </vertices><triangles><triangle v3='1' v1='0' v2='2'/>
      <triangle v1='0' v2='1' v3='3'/><triangle v1='0' v2='3' v3='2'/>
      <triangle v1='1' v2='2' v3='3'/></triangles></mesh></object></resources></model>'''


class ExtractMeshesTests(unittest.TestCase):
    def test_meaningful_part_number_is_preserved(self):
        self.assertEqual(part_name("3D/Objects/Part_1.stl_14.model"), "Part_1")
        self.assertEqual(part_name("3D/Objects/Part_2.model"), "Part_2")

    def test_xml_attribute_order_single_quotes_and_units(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            src = root / "source.3mf"
            with zipfile.ZipFile(src, "w") as z:
                z.writestr("3D/Objects/Peg.stl_1.model", model("centimeter"))
            meshes = extract(src, root / "out")
            self.assertAlmostEqual(meshes["Peg"].volume, 1000 / 6)
            self.assertTrue(meshes["Peg"].is_watertight)

    def test_existing_part_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            src = root / "source.3mf"
            with zipfile.ZipFile(src, "w") as z:
                z.writestr("3D/Objects/Peg.stl_1.model", model())
            output = root / "out"
            output.mkdir()
            (output / "Peg.stl").write_bytes(b"original")
            with self.assertRaises(FileExistsError):
                extract(src, output)
            self.assertEqual((output / "Peg.stl").read_bytes(), b"original")

    def test_conflicting_duplicate_refuses_entire_batch(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            src = root / "source.3mf"
            with zipfile.ZipFile(src, "w") as z:
                z.writestr("3D/Objects/Peg.stl_1.model", model())
                z.writestr("3D/Objects/Peg.stl_2.model", model(x=2))
            with self.assertRaisesRegex(ValueError, "同名"):
                extract(src, root / "out")
            self.assertFalse((root / "out").exists())


if __name__ == "__main__":
    unittest.main()
