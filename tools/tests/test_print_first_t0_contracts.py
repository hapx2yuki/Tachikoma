#!/usr/bin/env python3
"""t0実メッシュ監査の入力鮮度・姿勢契約を確認する軽量回帰試験。"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import check_print_first_t0_mesh as T0  # noqa: E402


class PrintFirstT0ContractTests(unittest.TestCase):
    def test_active_collector_emits_components_once_and_exact_false_filter(self):
        """実collectorの電装行は一度だけで、比較除外はその名前だけにする。"""
        import export_urdf as E
        import print_first_assembly as A
        import sim_collision as SC

        with A.context():
            with_components = SC.parts_with_pad(True, include_components=True)
            without_components = SC.parts_with_pad(True, include_components=False)

        all_rows = [(link, name)
                    for link, items in with_components.items()
                    for _mesh, _color, name in items]
        component_rows = [key for key in all_rows if key[1].startswith("component_")]
        self.assertGreater(len(component_rows), 0)
        self.assertEqual(len(component_rows), len(set(component_rows)))
        self.assertEqual(
            {key for key in all_rows if key[1].startswith("component_")},
            {("base_link", name) for _link, name in component_rows},
        )

        without_rows = {(link, name)
                        for link, items in without_components.items()
                        for _mesh, _color, name in items}
        self.assertTrue(
            {key for key in without_rows if key[1].startswith("component_")} == set()
        )
        self.assertIn(("base_link", "pf_chassis"), without_rows)
        # XIAO occupancy is a separate eye-pod collector and must remain.
        self.assertIn(("eye_pod_camera", "xiao_all_boards_occupancy"), without_rows)

    def test_active_collector_rejects_duplicate_link_name_rows(self):
        """重複をsetで潰さず、collectorの契約違反として停止する。"""
        import print_first_assembly as A
        import sim_collision as SC

        with A.context():
            component_rows = A.component_meshes()
            duplicate_rows = [component_rows[0], component_rows[0]]
            with patch.object(SC, "_print_first_component_rows",
                              return_value=duplicate_rows):
                with self.assertRaisesRegex(ValueError, "duplicate"):
                    SC.parts_with_pad(True, include_components=True)

    def test_external_paths_are_redacted_with_sha_prefix(self):
        with tempfile.TemporaryDirectory(prefix="tachikoma-t0-external-") as temp:
            path = Path(temp) / "outside-diagnostic.json"
            path.write_text("{}", encoding="utf-8")
            public = T0._rel(path)
            self.assertTrue(public.startswith("$EXTERNAL/outside-diagnostic.json#"))
            self.assertNotIn(str(path), public)
            self.assertIsNone(T0._resolve_diagnostic_ref(public, path))

    def test_hash_snapshot_detects_one_serialized_stl_change(self):
        with tempfile.TemporaryDirectory(prefix="tachikoma-t0-hash-") as temp:
            path = Path(temp) / "adopted.stl"
            path.write_bytes(b"solid candidate\n")
            entries = [("body_adopted_stl", path)]
            before = T0._hash_snapshot(entries)
            path.write_bytes(path.read_bytes() + b"changed\n")
            after = T0._hash_snapshot(entries)
            unchanged, changes = T0._compare_hash_snapshots(before, after)
            self.assertFalse(unchanged)
            self.assertEqual(len(changes), 1)
            self.assertEqual(changes[0]["kind"], "file_changed")

    def test_old_diagnostic_without_case_config_and_native_contract_is_rejected(self):
        case_path = ROOT / "outputs/print-first-20260905/final-simulation/cases/final_stand_pf1.json"
        diagnostic_path = (
            ROOT / "outputs/print-first-20260905/final-simulation-initial-diagnostic"
            / "diagnostics/final_stand_pf1-initial-contact.json"
        )
        if not case_path.is_file() or not diagnostic_path.is_file():
            self.skipTest("保存済みの旧t0診断束が無い")
        case = json.loads(case_path.read_text(encoding="utf-8"))
        diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))
        _pose, errors, _meta = T0._validate_native_pose_source(
            case, case_path, diagnostic, diagnostic_path
        )
        kinds = {item["kind"] for item in errors}
        self.assertIn("case_sha256", kinds)
        self.assertIn("config_sha256", kinds)
        self.assertIn("joint_order", kinds)

    def test_external_case_or_diagnostic_cannot_become_a_local_reference(self):
        case = {
            "name": "external-case",
            "model": {
                "model_path": str(ROOT / "hardware/urdf-print-first/tachikoma.urdf"),
            },
        }
        diagnostic = {
            "status": "DIAGNOSTIC_ONLY",
            "case_name": "external-case",
            "case_input": "$EXTERNAL/case.json#deadbeef",
            "case_sha256": "deadbeef",
            "joint_order": list(T0._joint_order()),
            "controller": {
                "profile_mode": T0.EXPECTED_PROFILE_MODE,
                "compile_flag_required": T0.EXPECTED_COMPILE_FLAG,
                "joint_order": list(T0._joint_order()),
                "native_initial_joint_angles_deg": {
                    name: 0.0 for name in T0._joint_order()
                },
                "native_initial_enabled": {
                    name: True for name in T0._joint_order()
                },
                "native_row_columns": {"phase": 0.0, "moving": False, "ready": True},
            },
            "initialization": {
                "mj_step_called": False,
                "states": [{"label": "before_first_mj_step", "mj_step_called": False}],
            },
        }
        with tempfile.TemporaryDirectory(prefix="tachikoma-t0-case-") as temp:
            case_path = Path(temp) / "case.json"
            diagnostic_path = Path(temp) / "diagnostic.json"
            case_path.write_text(json.dumps(case), encoding="utf-8")
            diagnostic_path.write_text(json.dumps(diagnostic), encoding="utf-8")
            _pose, errors, _meta = T0._validate_native_pose_source(
                case, case_path, diagnostic, diagnostic_path
            )
        self.assertIn("case_reference", {item["kind"] for item in errors})

    def test_native_raw_phase_must_be_exactly_zero(self):
        case = {
            "name": "raw-phase-case",
            "profile": {"body_h": 115.0},
            "segments": [{"body_h": 115.0}],
            "model": {},
        }
        diagnostic = {
            "controller": {
                "profile_mode": T0.EXPECTED_PROFILE_MODE,
                "compile_flag": T0.EXPECTED_COMPILE_FLAG,
                "compile_flag_required": T0.EXPECTED_COMPILE_FLAG,
                "native_row_columns": {"phase": 0.0, "moving": False, "ready": True},
                "native_row_columns_raw": {"phase": 0.5, "moving": 0.0, "ready": 1.0},
                "native_initial_enabled": {
                    name: True for name in T0._joint_order()
                },
                "native_initial_enabled_raw": {
                    name: 1.0 for name in T0._joint_order()
                },
                "native_initial_joint_angles_deg": {
                    name: 0.0 for name in T0._joint_order()
                },
                "joint_order": list(T0._joint_order()),
                "initial_body_h_mm": 115.0,
            },
            "initialization": {
                "mj_step_called": False,
                "states": [{"label": "before_first_mj_step", "mj_step_called": False}],
            },
            "status": "DIAGNOSTIC_ONLY",
            "case_name": "raw-phase-case",
            "case_input": "$EXTERNAL/case.json#deadbeef",
            "case_sha256": "deadbeef",
        }
        with tempfile.TemporaryDirectory(prefix="tachikoma-t0-phase-") as temp:
            case_path = Path(temp) / "case.json"
            diagnostic_path = Path(temp) / "diagnostic.json"
            case_path.write_text(json.dumps(case), encoding="utf-8")
            diagnostic_path.write_text(json.dumps(diagnostic), encoding="utf-8")
            _pose, errors, _meta = T0._validate_native_pose_source(
                case, case_path, diagnostic, diagnostic_path
            )
        kinds = {item["kind"] for item in errors}
        self.assertIn("native_row_raw_value", kinds)

        diagnostic["controller"]["native_row_columns"]["phase"] = 0.5
        with tempfile.TemporaryDirectory(prefix="tachikoma-t0-phase-column-") as temp:
            case_path = Path(temp) / "case.json"
            diagnostic_path = Path(temp) / "diagnostic.json"
            case_path.write_text(json.dumps(case), encoding="utf-8")
            diagnostic_path.write_text(json.dumps(diagnostic), encoding="utf-8")
            _pose, phase_errors, _meta = T0._validate_native_pose_source(
                case, case_path, diagnostic, diagnostic_path
            )
        self.assertIn("native_row_phase_value",
                      {item["kind"] for item in phase_errors})

    def test_native_build_source_keys_must_match_prepare_native_bundle(self):
        with tempfile.TemporaryDirectory(prefix="tachikoma-t0-build-") as temp:
            build_path = Path(temp) / "build.json"
            build_path.write_text("{}", encoding="utf-8")
            errors = T0._validate_native_build_sources(
                {"source_sha256": {"print_first_gait.h": "deadbeef"}},
                build_path,
            )
        kinds = {item["kind"] for item in errors}
        self.assertIn("native_build_source_sha256_keys", kinds)

    def test_inventory_requires_exactly_one_tpu_shoe_per_tibia(self):
        import trimesh
        box = trimesh.creation.box(extents=[1., 1., 1.])
        rows = [
            (box.copy(), "pf_head_top_clearanced", "base_link"),
            (box.copy(), "pf_camera_carrier", "eye_pod_camera"),
            (box.copy(), "pf_eye_pod_camera_clearanced", "eye_pod_camera"),
        ]
        rows.extend(
            (box.copy(), "tpu_shoe", f"leg_{leg.lower()}_tibia")
            for leg in ("FR", "FL", "RL", "RR")
        )
        rows.append((box.copy(), "tpu_shoe", "leg_fr_tibia"))
        report = T0._inventory_report(rows)
        self.assertEqual(report["status"], "FAIL")
        self.assertFalse(report["checks"]["four_tpu_leg_candidates_present"])

    def test_inventory_rejects_legacy_toe_instance(self):
        """旧Leg_Toe_Black_x12の同居を、除外扱いではなく失敗にする。"""
        import trimesh
        box = trimesh.creation.box(extents=[1., 1., 1.])
        rows = [
            (box.copy(), "pf_head_top_clearanced", "base_link"),
            (box.copy(), "pf_camera_carrier", "base_link"),
            (box.copy(), "pf_eye_pod_camera_clearanced", "base_link"),
        ]
        rows.extend(
            (box.copy(), "tpu_shoe", f"leg_{leg.lower()}_tibia")
            for leg in ("FR", "FL", "RL", "RR")
        )
        rows.append((box.copy(), "Leg_Toe_Black_x12#shoe_FR", "base_link"))
        report = T0._inventory_report(rows)
        self.assertEqual(report["status"], "FAIL")
        self.assertFalse(report["checks"]["legacy_replacement_names_absent"])

    def test_inventory_uses_shared_legacy_forbidden_names(self):
        import trimesh
        box = trimesh.creation.box(extents=[1., 1., 1.])
        rows = [
            (box.copy(), "pf_head_top_clearanced", "base_link"),
            (box.copy(), "pf_camera_carrier", "eye_pod_camera"),
            (box.copy(), "pf_eye_pod_camera_clearanced", "eye_pod_camera"),
        ]
        rows.extend(
            (box.copy(), "tpu_shoe", f"leg_{leg.lower()}_tibia")
            for leg in ("FR", "FL", "RL", "RR")
        )
        for legacy_name in (
                "Leg_Toe_Black_x12", "retained_Leg_Toe_Black_x12#FR",
                "Head_Top_Blue", "old_Head_Top_Blue_shell", "pf_head_top"):
            report = T0._inventory_report(
                rows + [(box.copy(), legacy_name, "base_link")])
            self.assertEqual(report["status"], "FAIL", legacy_name)
            self.assertFalse(
                report["checks"]["legacy_replacement_names_absent"], legacy_name)
            self.assertIn(legacy_name, report["legacy_replacement_names_present"])


if __name__ == "__main__":
    unittest.main()
