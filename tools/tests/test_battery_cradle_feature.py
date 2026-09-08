#!/usr/bin/env python3
"""battery_cradle のCAD由来凸分割を小さく検証する。"""
import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'hardware' / 'src'))
sys.path.insert(0, str(ROOT / 'tools'))

import export_urdf
import sim_collision


# Reviewed output for the canonical STL and the contract above.  Keeping this
# value in the test makes a cache hit prove a known decomposition, instead of
# comparing two values emitted by the same invocation.
EXPECTED_FEATURE_DECOMPOSITION_SHA256 = (
    'a6f237a8654703ecaf3d3ac47299680fdfcbb5cb5c3115e5032071981e3dd835')


class BatteryCradleFeatureTests(unittest.TestCase):
    def setUp(self):
        self.source = export_urdf.load('battery_cradle')

    def test_feature_split_covers_source_and_preserves_voids(self):
        hulls, contract = sim_collision._battery_cradle_feature_decomposition(
            self.source)
        self.assertGreater(len(hulls), 0)
        self.assertEqual(contract['status'], 'PASS')
        self.assertEqual(contract['source_coverage']['status'], 'PASS')
        self.assertEqual(contract['source_coverage']['covered_point_count'],
                         contract['source_coverage']['point_count'])
        self.assertEqual(contract['source_coverage']['tolerance_mm'], 0.011)
        geometry = contract['geometry_equivalence']
        self.assertEqual(geometry['status'], 'PASS')
        self.assertEqual(geometry['surface_sample_tolerance_mm'], 0.011)
        self.assertIsNone(geometry['outside_distance_measured_mm'])
        self.assertEqual(len(geometry['bands']), len(
            sim_collision.BATTERY_CRADLE_FEATURE_BANDS))
        for band in geometry['bands']:
            boolean = band['cross_section_boolean']
            self.assertEqual(boolean['status'], 'PASS')
            self.assertLessEqual(boolean['source_minus_candidate_area_mm2'],
                                 sim_collision.BATTERY_CRADLE_FEATURE_AREA_TOLERANCE_MM2)
            self.assertLessEqual(boolean['candidate_minus_source_area_mm2'],
                                 sim_collision.BATTERY_CRADLE_FEATURE_AREA_TOLERANCE_MM2)
            self.assertLessEqual(boolean['symmetric_difference_area_mm2'],
                                 sim_collision.BATTERY_CRADLE_FEATURE_AREA_TOLERANCE_MM2)
            self.assertEqual(band['constant_section_proof']['status'], 'PASS')
            self.assertEqual(band['constant_section_proof']['source_face_check']['sloped_face_count'], 0)
            self.assertEqual(band['constant_section_proof']['source_face_check']['internal_horizontal_face_count'], 0)
            self.assertTrue(all(check['status'] == 'PASS' for check in
                                band['constant_section_proof']['interval_checks']))
        self.assertLessEqual(
            abs(contract['volume_difference_mm3']),
            contract['volume_tolerance_mm3'])
        self.assertTrue(all(not row['occupied_hull_indices']
                            for row in contract['void_points']))
        self.assertEqual(
            [row['feature'] for row in contract['bands']],
            [band[0] for band in sim_collision.BATTERY_CRADLE_FEATURE_BANDS])
        self.assertEqual(contract['cell_count'], len(contract['hull_sha256']))

    def test_feature_route_is_bound_to_exact_name_and_geometry(self):
        with self.assertRaisesRegex(ValueError, 'exact.*binding'):
            sim_collision._battery_cradle_feature_decomposition(
                self.source, link='base_link', part='renamed_battery')
        tampered = self.source.copy()
        tampered.vertices[0, 0] += 0.1
        with self.assertRaisesRegex(ValueError, 'geometry differs'):
            sim_collision._battery_cradle_feature_decomposition(tampered)
        with patch.object(sim_collision, 'BATTERY_CRADLE_SOURCE_STL_SHA256',
                          '0' * 64):
            with self.assertRaisesRegex(ValueError, 'canonical STL SHA'):
                sim_collision._battery_cradle_feature_decomposition(self.source)

    def test_feature_cache_round_trip_keeps_split_hash(self):
        temp_root = Path(tempfile.mkdtemp(prefix='tachikoma-battery-feature-'))
        try:
            source_parts = {
                'base_link': [(self.source, '#286bb1', 'battery_cradle')],
            }
            with patch.object(sim_collision, 'parts_with_pad',
                              return_value=source_parts):
                first = sim_collision.convex_parts('vhacd', cache=temp_root)
                second = sim_collision.convex_parts('vhacd', cache=temp_root)
                cache_hashes, current_ledger = (
                    sim_collision.collision_cache_input_fingerprints(
                        sim_collision.convex_parts.last_cache_ledger,
                        output_root=temp_root,
                        source_parts=source_parts,
                        reject_extra_cache=True))
            self.assertEqual(len(first), 1)
            self.assertEqual(len(first[0][3]), 1832)
            self.assertEqual(len(second[0][3]), 1832)
            self.assertEqual(current_ledger['entry_count'], 1)
            self.assertEqual(len(cache_hashes), 2 + 1832)
            row = sim_collision.convex_parts.last_manifest[0]
            feature = row['feature_decomposition']
            self.assertEqual(feature['status'], 'PASS')
            self.assertEqual(feature['contract_version'],
                             sim_collision.BATTERY_CRADLE_FEATURE_CONTRACT_VERSION)
            self.assertEqual(feature['source_stl_sha256'],
                             sim_collision.BATTERY_CRADLE_SOURCE_STL_SHA256)
            self.assertEqual(feature['cell_count'], 1832)
            self.assertEqual(feature['feature_decomposition_sha256'],
                             EXPECTED_FEATURE_DECOMPOSITION_SHA256)
            self.assertEqual(feature['source_coverage']['point_count'], 23026)
            self.assertEqual(feature['source_coverage']['covered_point_count'], 23026)
            self.assertEqual(feature['geometry_equivalence']['status'], 'PASS')
            archives = list(temp_root.glob('*.npz'))
            self.assertEqual(len(archives), 1)
            with np.load(archives[0], allow_pickle=False) as saved:
                self.assertEqual(
                    int(np.asarray(
                        saved['battery_cradle_feature_contract_version']).item()),
                    sim_collision.BATTERY_CRADLE_FEATURE_CONTRACT_VERSION)
                self.assertEqual(
                    np.asarray(saved['battery_cradle_feature_sha256']).item(),
                    EXPECTED_FEATURE_DECOMPOSITION_SHA256)
                self.assertEqual(
                    np.asarray(saved['battery_cradle_source_stl_sha256']).item(),
                    sim_collision.BATTERY_CRADLE_SOURCE_STL_SHA256)
            manifest = temp_root / 'manifest-vhacd.json'
            payload = json.loads(manifest.read_text(encoding='utf-8'))
            self.assertEqual(payload[0]['feature_decomposition']['status'], 'PASS')
            self.assertEqual(payload[0]['feature_decomposition']['cell_count'], 1832)
            self.assertEqual(
                payload[0]['feature_decomposition']['feature_decomposition_sha256'],
                EXPECTED_FEATURE_DECOMPOSITION_SHA256)

            # A cache hit must reject a changed feature binding, even when the
            # NPZ remains structurally readable and the source STL is intact.
            with np.load(archives[0], allow_pickle=False) as saved:
                altered = {key: saved[key] for key in saved.files}
            altered['battery_cradle_feature_sha256'] = np.array('0' * 64)
            np.savez_compressed(archives[0], **altered)
            with patch.object(sim_collision, 'parts_with_pad',
                              return_value=source_parts):
                with self.assertRaisesRegex(ValueError,
                                            'feature decomposition SHA differs'):
                    sim_collision.convex_parts('vhacd', cache=temp_root)
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    def test_public_ledger_rebuild_rejects_tampered_hull_without_source_parts(self):
        temp_root = Path(tempfile.mkdtemp(prefix='tachikoma-battery-tamper-'))
        try:
            placed = self.source.copy()
            placed.apply_translation([0.0, 0.0, export_urdf.ZB])
            source_parts = {
                'base_link': [(placed, '#286bb1', 'battery_cradle')],
            }
            with patch.object(sim_collision, 'parts_with_pad',
                              return_value=source_parts):
                sim_collision.convex_parts('vhacd', cache=temp_root)
            ledger = sim_collision.convex_parts.last_cache_ledger
            # The independent replay path must accept an untouched ledger even
            # when the caller has no in-memory source_parts map.
            sim_collision.collision_cache_input_fingerprints(
                ledger, output_root=temp_root, reject_extra_cache=True)

            entry = ledger['entries'][0]
            cache_path = Path(entry['_cache_path'])
            with np.load(cache_path, allow_pickle=False) as saved:
                altered = {key: saved[key] for key in saved.files}
            fake = sim_collision.trimesh.creation.box((0.5, 0.5, 0.5))
            fake.apply_translation(placed.bounds.mean(axis=0) - fake.bounds.mean(axis=0))
            fake_sha = sim_collision._hull_digest(fake)
            altered['v0'] = np.asarray(fake.vertices)
            altered['f0'] = np.asarray(fake.faces, dtype=np.int64)
            hull_shas = list(np.asarray(altered['hull_sha256']).astype(str))
            hull_shas[0] = fake_sha
            altered['hull_sha256'] = np.asarray(hull_shas)
            np.savez_compressed(cache_path, **altered)

            entry['sha256'] = hashlib.sha256(cache_path.read_bytes()).hexdigest()
            entry['hull_sha256'][0] = fake_sha
            entry['feature_decomposition']['hull_sha256'][0] = fake_sha
            manifest_path = Path(ledger['manifest']['_manifest_path'])
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
            row = manifest[0]
            row['cache_sha256'] = entry['sha256']
            row['hull_sha256'][0] = fake_sha
            row['feature_decomposition']['hull_sha256'][0] = fake_sha
            manifest_path.write_text(
                json.dumps(manifest, indent=2, ensure_ascii=False) + '\n',
                encoding='utf-8')
            ledger['manifest']['sha256'] = hashlib.sha256(
                manifest_path.read_bytes()).hexdigest()

            with self.assertRaisesRegex(ValueError,
                                        'battery-cradle feature split differs'):
                sim_collision.collision_cache_input_fingerprints(
                    ledger, output_root=temp_root, reject_extra_cache=True)
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
