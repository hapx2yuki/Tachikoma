import sys
from pathlib import Path
import types
import unittest
from unittest.mock import patch
import numpy as np
import trimesh
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import mesh_checks as M

class MeshChecks(unittest.TestCase):
    def test_positive_intersection_is_returned(self):
        box = trimesh.creation.box((1., 1., 1.))
        self.assertAlmostEqual(M.intersection_volume_mm3(box, box), 1.0, places=5)

    def test_mouth_speaker_intersection_survives_native_float64_reconstruction(self):
        from print_first_assembly import context
        from sim_collision import parts_with_pad
        with context(generated=True):
            items = parts_with_pad(True)['base_link']
            mouth = next(mesh for mesh, _, name in items
                         if name == 'Mouth_Cannon_Grey')
            speaker = next(mesh for mesh, _, name in items
                           if name == 'component_speaker_cannon_real_pocket_candidate')
            volume = M.intersection_volume_mm3(mouth, speaker)
        self.assertGreater(volume, M.BOOLEAN_NEGATIVE_TOLERANCE_MM3)
        self.assertAlmostEqual(volume, 9.873275985e-06, delta=1e-09)

    def test_boolean_failure_is_not_clearance(self):
        with patch.object(M.trimesh.boolean,'intersection',side_effect=ValueError('not a volume')):
            with self.assertRaises(ValueError):M.intersection_volume_mm3(None,None)
    def test_missing_and_invalid_volume_fail(self):
        for obj,error in [(None,RuntimeError),
                          (types.SimpleNamespace(is_empty=False,volume=float('nan')),ValueError),
                          (types.SimpleNamespace(is_empty=False,volume=-1),ValueError)]:
            with patch.object(M.trimesh.boolean,'intersection',return_value=obj):
                with self.assertRaises(error):M.intersection_volume_mm3(None,None)

    def test_non_watertight_boolean_result_fails_closed(self):
        mesh = trimesh.creation.box((1., 1., 1.))
        mesh.update_faces(np.arange(len(mesh.faces) - 1))
        mesh.remove_unreferenced_vertices()
        with patch.object(M.trimesh.boolean, 'intersection', return_value=mesh):
            with self.assertRaisesRegex(ValueError, 'watertight'):
                M.intersection_volume_mm3(mesh, mesh)

    def test_tiny_negative_requires_native_noerror_and_is_recorded(self):
        mesh = trimesh.creation.box((.008, .008, .008))
        mesh.faces = mesh.faces[:, ::-1]
        self.assertLess(mesh.volume, 0.)
        with patch.object(M.trimesh.boolean, 'intersection', return_value=mesh):
            self.assertEqual(M.intersection_volume_mm3(mesh, mesh), 0.0)
        self.assertEqual(mesh.metadata['signed_negative_validation'][0]['native_status'], 'NoError')
        self.assertLess(mesh.metadata['signed_negative_validation'][0]['serialized_volume_mm3'], 0.)

    def test_negative_threshold_and_native_sign_mismatch_fail(self):
        mesh = trimesh.creation.box((1., 1., 1.))
        mesh.faces = mesh.faces[:, ::-1]
        with patch.object(M.trimesh.boolean, 'intersection', return_value=mesh):
            with self.assertRaisesRegex(ValueError, 'negative'):
                M.intersection_volume_mm3(mesh, mesh)

        tiny = trimesh.creation.box((.008, .008, .008))
        tiny.faces = tiny.faces[:, ::-1]
        class PositiveNative:
            def status(self):
                return types.SimpleNamespace(name='NoError')
            def volume(self):
                return 1e-7
        with patch.object(M.trimesh.boolean, 'intersection', return_value=tiny), \
             patch('manifold3d.Manifold', return_value=PositiveNative()):
            self.assertEqual(M.intersection_volume_mm3(tiny, tiny), 0.0)
        validation = tiny.metadata['signed_negative_validation'][0]
        self.assertTrue(validation['sign_mismatch_within_tolerance'])
        self.assertEqual(validation['tolerance_mm3'], M.BOOLEAN_NEGATIVE_TOLERANCE_MM3)
        mismatch = tiny.copy()
        mismatch.metadata = {}
        class LargePositiveNative:
            def status(self):
                return types.SimpleNamespace(name='NoError')
            def volume(self):
                return 2e-6
        with patch.object(M.trimesh.boolean, 'intersection', return_value=mismatch), \
             patch('manifold3d.Manifold', return_value=LargePositiveNative()):
            with self.assertRaisesRegex(ValueError, 'signed volume mismatch'):
                M.intersection_volume_mm3(mismatch, mismatch)

    def test_positive_intersection_is_never_clamped_for_tiny_native_mismatch(self):
        mesh = trimesh.creation.box((1., 1., 1.))
        class TinyNegativeNative:
            def status(self):
                return types.SimpleNamespace(name='NoError')
            def volume(self):
                return -1e-7
        with patch.object(M.trimesh.boolean, 'intersection', return_value=mesh), \
             patch('manifold3d.Manifold', return_value=TinyNegativeNative()):
            with self.assertRaisesRegex(ValueError, 'signed volume mismatch'):
                M.intersection_volume_mm3(mesh, mesh)
    def test_empty_mesh_is_zero(self):
        with patch.object(M.trimesh.boolean,'intersection',return_value=types.SimpleNamespace(is_empty=True)):
            self.assertEqual(M.intersection_volume_mm3(None,None),0)
if __name__=='__main__':unittest.main()
