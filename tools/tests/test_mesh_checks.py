import sys
from pathlib import Path
import types
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import mesh_checks as M

class MeshChecks(unittest.TestCase):
    def test_boolean_failure_is_not_clearance(self):
        with patch.object(M.trimesh.boolean,'intersection',side_effect=ValueError('not a volume')):
            with self.assertRaises(ValueError):M.intersection_volume_mm3(None,None)
    def test_missing_and_invalid_volume_fail(self):
        for obj,error in [(None,RuntimeError),
                          (types.SimpleNamespace(is_empty=False,volume=float('nan')),ValueError),
                          (types.SimpleNamespace(is_empty=False,volume=-1),ValueError)]:
            with patch.object(M.trimesh.boolean,'intersection',return_value=obj):
                with self.assertRaises(error):M.intersection_volume_mm3(None,None)
    def test_empty_mesh_is_zero(self):
        with patch.object(M.trimesh.boolean,'intersection',return_value=types.SimpleNamespace(is_empty=True)):
            self.assertEqual(M.intersection_volume_mm3(None,None),0)
if __name__=='__main__':unittest.main()
