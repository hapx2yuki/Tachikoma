#!/usr/bin/env python3
"""初期接触診断のメッシュ検証契約を確認する。"""
import unittest
from unittest.mock import patch

import numpy as np
import trimesh

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import diagnose_print_first_initial as D


class PrintFirstInitialDiagnosticTests(unittest.TestCase):
    def test_freeze_validation_is_publicized_recursively(self):
        case = {}
        report = {
            'manifest_path': str(ROOT / 'outputs/freeze.json'),
            'required_files': [str(ROOT / 'hardware/src/config.py')],
            'source_files': [{
                'path': str(ROOT / 'tools/diagnose_print_first_initial.py'),
                'nested': {'path': str(ROOT / 'firmware/src/config.h')},
            }],
        }
        with patch.object(D.P, 'validate_freeze_manifest', return_value=report):
            public = D._public_freeze_validation(
                case, ROOT / 'outputs/print-first-20260905/final-simulation-initial-diagnostic')

        def absolute_strings(value):
            if isinstance(value, dict):
                return [item for child in value.values() for item in absolute_strings(child)]
            if isinstance(value, list):
                return [item for child in value for item in absolute_strings(child)]
            return [value] if isinstance(value, str) and value.startswith('/') else []

        self.assertEqual(absolute_strings(public), [])
        self.assertEqual(public['manifest_path'],
                         'outputs/freeze.json')

    def test_empty_boolean_intersection_is_zero(self):
        first = trimesh.creation.box(extents=[10., 10., 10.])
        second = trimesh.creation.box(extents=[10., 10., 10.])
        second.apply_translation([20., 0., 0.])
        volume, error = D._boolean_volume_mm3(first, second)
        self.assertEqual(volume, 0.0)
        self.assertIsNone(error)

    def test_negative_input_volume_is_not_normalized(self):
        first = trimesh.creation.box(extents=[10., 10., 10.])
        first.invert()
        second = trimesh.creation.box(extents=[10., 10., 10.])
        volume, error = D._boolean_volume_mm3(first, second)
        self.assertIsNone(volume)
        self.assertIn('negative', error)

    def test_open_input_is_rejected(self):
        first = trimesh.creation.box(extents=[10., 10., 10.])
        first.update_faces(range(len(first.faces) - 1))
        second = trimesh.creation.box(extents=[10., 10., 10.])
        volume, error = D._boolean_volume_mm3(first, second)
        self.assertIsNone(volume)
        self.assertIn('watertight', error)

    def test_nonfinite_input_is_rejected(self):
        first = trimesh.creation.box(extents=[10., 10., 10.])
        first.vertices[0, 0] = float('nan')
        second = trimesh.creation.box(extents=[10., 10., 10.])
        volume, error = D._boolean_volume_mm3(first, second)
        self.assertIsNone(volume)
        self.assertIn('non-finite', error)

    def test_negative_nonempty_boolean_result_is_rejected(self):
        first = trimesh.creation.box(extents=[10., 10., 10.])
        second = trimesh.creation.box(extents=[10., 10., 10.])
        negative = trimesh.creation.box(extents=[5., 5., 5.])
        negative.invert()
        with patch.object(D.trimesh.boolean, 'intersection', return_value=negative):
            volume, error = D._boolean_volume_mm3(first, second)
        self.assertIsNone(volume)
        self.assertIn('intersection[0]', error)
        self.assertIn('negative', error)

    def test_positive_intersection_is_validated_and_returned(self):
        first = trimesh.creation.box(extents=[10., 10., 10.])
        second = trimesh.creation.box(extents=[10., 10., 10.])
        second.apply_translation([5., 0., 0.])
        volume, error = D._boolean_volume_mm3(first, second)
        self.assertAlmostEqual(volume, 500.0)
        self.assertIsNone(error)

    def test_zero_volume_nonempty_input_is_rejected(self):
        first = trimesh.Trimesh(
            vertices=[[0., 0., 0.], [1., 0., 0.], [0., 1., 0.]],
            faces=[[0, 1, 2]], process=False,
        )
        second = trimesh.creation.box(extents=[10., 10., 10.])
        volume, error = D._boolean_volume_mm3(first, second)
        self.assertIsNone(volume)
        self.assertIn('valid volume', error)

    def test_one_sided_boolean_result_is_not_treated_as_empty(self):
        """頂点片側/面片側の壊れた交差結果を空交差へ変換しない。"""
        first = trimesh.creation.box(extents=[10., 10., 10.])
        second = trimesh.creation.box(extents=[10., 10., 10.])
        vertices_only = trimesh.Trimesh(
            vertices=np.zeros((4, 3), dtype=float),
            faces=np.empty((0, 3), dtype=np.int64), process=False)
        with patch.object(D.trimesh.boolean, 'intersection',
                          return_value=vertices_only):
            volume, error = D._boolean_volume_mm3(first, second)
        self.assertIsNone(volume)
        self.assertIn('mesh is empty', error)

        faces_only = trimesh.creation.box(extents=[1., 1., 1.])
        with patch.object(type(faces_only), 'vertices',
                          new=property(lambda _mesh: np.empty((0, 3), dtype=float))):
            with patch.object(D.trimesh.boolean, 'intersection',
                              return_value=faces_only):
                volume, error = D._boolean_volume_mm3(first, second)
        self.assertIsNone(volume)
        self.assertTrue('mesh is empty' in error or 'out of bounds' in error)

    def test_contact_frame_normal_is_first_row(self):
        frame = list(range(9))
        normal = D._contact_normal_world(frame)
        self.assertEqual(normal.tolist(), [0., 1., 2.])


if __name__ == '__main__':
    unittest.main()
