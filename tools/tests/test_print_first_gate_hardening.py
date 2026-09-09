"""印刷優先の最終入口が短縮・混在・古い入力を通さないことを確認する。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import check_print_first_body as BODY
import make_print_first_freeze_manifest as FREEZE
import sim_print_first as SIM


class PrintFirstGateHardeningTests(unittest.TestCase):
    def test_final_body_requires_trace_and_rejects_subset(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "outputs") as temp:
            case_path = Path(temp) / "case.json"
            case_path.write_text(json.dumps({"name": "gate-case"}), encoding="utf-8")
            model = {
                "exists": True,
                "parse_error": None,
                "all_referenced_meshes_exist": True,
                "all_referenced_meshes_valid": True,
            }
            assemblies = {"generation_contract": {"status": "PASS"}}
            requirements = {"required": False, "status": "NOT_APPLICABLE"}
            with patch.object(BODY, "_model_inputs", return_value=model), \
                    patch.object(BODY, "_assembly_inputs", return_value=assemblies), \
                    patch.object(BODY.P, "final_case_requirements", return_value=requirements), \
                    patch.object(BODY.P, "validate_freeze_manifest", return_value={"status": "PASS"}):
                result = BODY._check(
                    {"name": "gate-case", "model": {}}, case_path,
                    pose_path=None, output_root=case_path.parent,
                    mode="final", include_components=True, max_poses=None,
                )
                kinds = {item["kind"] for item in result["checks"]["input_errors"]}
                self.assertIn("final_requires_complete_pose_trace", kinds)
                self.assertEqual(result["status"], "FAIL")

                result = BODY._check(
                    {"name": "gate-case", "model": {}}, case_path,
                    pose_path=None, output_root=case_path.parent,
                    mode="final", include_components=True, max_poses=1,
                )
                kinds = {item["kind"] for item in result["checks"]["input_errors"]}
                self.assertIn("final_rejects_max_poses", kinds)
                self.assertEqual(result["status"], "FAIL")

    def test_strict_final_freeze_rejects_legacy_alias_status(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "outputs") as temp:
            folder = Path(temp)
            urdf = folder / "model.urdf"
            urdf.write_text("<robot name='gate'/>", encoding="utf-8")
            freeze = folder / "freeze.json"
            freeze_data = {
                "status": "FROZEN",
                "geometry_freeze_time": "2026-09-08T00:00:00Z",
                "files": [{
                    "path": urdf.relative_to(ROOT).as_posix(),
                    "sha256": hashlib.sha256(urdf.read_bytes()).hexdigest(),
                }],
            }
            freeze.write_text(json.dumps(freeze_data), encoding="utf-8")
            case = {
                "model": {
                    "freeze_manifest": str(freeze),
                    "geometry_freeze_hash": hashlib.sha256(freeze.read_bytes()).hexdigest(),
                    "geometry_freeze_time": freeze_data["geometry_freeze_time"],
                    "urdf_path": str(urdf),
                }
            }
            with patch.object(SIM, "FREEZE_REQUIRED_SOURCES", ()), \
                    patch.object(SIM, "runtime_input_paths", return_value=([], None)), \
                    patch.object(SIM, "print_first_assembly_generation_contract",
                                  return_value={"status": "PASS"}):
                report = SIM.validate_freeze_manifest(case, require_generation_roles=True)
            self.assertNotEqual(report["status"], "PASS")
            self.assertTrue(any(
                row.get("kind") == "freeze_status"
                and row.get("expected") == (SIM.FINAL_FREEZE_STATUS,)
                for row in report["mismatches"]
            ))

    def test_freeze_output_path_is_checked_before_atomic_write(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "outputs") as temp:
            folder = Path(temp)
            source = folder / "source.json"
            source.write_text("source", encoding="utf-8")
            with self.assertRaises(ValueError):
                FREEZE._safe_output_path(Path("/tmp/tachikoma-outside.json"))
            with self.assertRaisesRegex(ValueError, "入力"):
                FREEZE._safe_output_path(source, input_paths=[source])
            redirected = folder / "redirected"
            redirected.mkdir()
            outside = folder / "outside-target"
            outside.mkdir()
            link = redirected / "link"
            link.symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symlink"):
                FREEZE._safe_output_path(link / "manifest.json")

    def test_summarize_binds_run_and_rejects_changed_input(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "outputs") as temp:
            out = Path(temp)
            (out / "results").mkdir()
            input_path = out / "input.dat"
            input_path.write_text("stable", encoding="utf-8")
            run_id = "a" * 32
            run_manifest = {
                "schema_version": SIM.RUN_MANIFEST_SCHEMA_VERSION,
                "status": "RUNNING",
                "run_id": run_id,
                "results": [{"case": "case", "path": "$OUTPUT/results/case.json"}],
                "freeze": {"required": False, "path": None, "sha256": None,
                            "geometry_freeze_time": None},
            }
            SIM.save(out / "run-manifest.json", run_manifest)
            input_sha = SIM.sha(input_path)
            result = {
                "case": {"name": "case"},
                "status": "PASS",
                "run_id": run_id,
                "run_manifest_path": "$OUTPUT/run-manifest.json",
                "checks": {"inputs_unchanged": True},
                "input_sha256": {"$OUTPUT/input.dat": input_sha},
                "input_sha256_current": {"$OUTPUT/input.dat": input_sha},
            }
            digest = SIM.physical_result_content_sha(result)
            result["physical_result_content_sha256"] = digest
            result["physical_result_file_sha256"] = digest
            SIM.save(out / "results/case.json", result)
            self.assertEqual(SIM.summarize(out)["status"], "PASS")

            input_path.write_text("changed", encoding="utf-8")
            report = SIM.summarize(out)
            self.assertEqual(report["status"], "FAIL")
            self.assertTrue(any(row["kind"] == "stale_input" for row in report["errors"]))

    def test_motion_quality_model_validity_only_requires_diagnostic_flag(self):
        source = {
            "case": {
                "name": "diagnostic",
                "motion_quality_case": True,
                "motion_quality_kind": "initial_stand",
                "quality_eligible": False,
                "motion_quality_segments": ["stand"],
                "segments": [{"name": "stand", "duration": 1.0}],
            },
            "status": "PASS",
            "checks": {"inputs_unchanged": True},
            "timeseries": [{
                "time": 0.0, "segment": "stand", "command": [0.0, 0.0, 0.0],
                "base_pos": [0.0, 0.0, 0.1], "rpy_deg": [0.0, 0.0, 0.0],
            }],
        }
        with tempfile.TemporaryDirectory(prefix="tachikoma-motion-gate-") as temp:
            folder = Path(temp)
            source_path = folder / "source.json"
            source_path.write_text(json.dumps(source), encoding="utf-8")
            command = [sys.executable, str(ROOT / "tools/check_print_first_motion_quality.py"),
                       "--input", str(source_path), "--output", str(folder / "report.json")]
            self.assertEqual(subprocess.run(command, capture_output=True).returncode, 1)
            command.append("--diagnostic")
            self.assertEqual(subprocess.run(command, capture_output=True).returncode, 0)


if __name__ == "__main__":
    unittest.main()
