"""統合検査の対象欠落と実行失敗が合格扱いされないことを確認する。"""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
import zipfile
from unittest.mock import patch

import trimesh

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "hardware/src")]
import check_static_assembly as static
import run_design_audit as runner
import build_audit_coverage as coverage


class CoverageArchiveGuards(unittest.TestCase):
    def test_member_digest_follows_archive_content_and_missing_member(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(coverage, 'ROOT', Path(folder)):
            archive = Path(folder) / 'old.zip'
            for content in (b'original', b'changed'):
                with zipfile.ZipFile(archive, 'w') as bundle:
                    bundle.writestr('hardware/mesh.stl', content)
                self.assertEqual(coverage.current_digest('old.zip/hardware/mesh.stl'),
                                 hashlib.sha256(content).hexdigest())
                self.assertIsNone(coverage.current_digest('old.zip/missing.stl'))
            archive.write_bytes(b'broken')
            self.assertIsNone(coverage.current_digest('old.zip/hardware/mesh.stl'))


class StaticAssemblyGuards(unittest.TestCase):
    def parts(self):
        box = trimesh.creation.box((1, 1, 1))
        return {key: [] if key == "camera_optical_frame" else [(box.copy(), "grey", key)]
                for key in static.LINK_PARENT_FRAME}

    def check(self, parts):
        with patch.object(static, "parts_with_pad", return_value=parts), \
             patch.object(static, "input_fingerprints", return_value={}):
            return static.run()

    def test_nonbase_same_body_collision_is_rejected(self):
        parts = self.parts()
        parts["arm_r_upper"].append((parts["arm_r_upper"][0][0].copy(), "grey", "overlap"))
        result = self.check(parts)
        self.assertFalse(result["pass"])
        self.assertEqual(result["intersections"][0]["link"], "arm_r_upper")
        self.assertAlmostEqual(result["intersections"][0]["intersection_mm3"], 1)

    def test_empty_base_and_missing_link_are_rejected(self):
        for changed in ("empty", "missing"):
            with self.subTest(changed=changed):
                parts = self.parts()
                if changed == "empty":
                    parts["base_link"] = []
                else:
                    del parts["arm_l_upper"]
                result = self.check(parts)
                self.assertFalse(result["pass"])
                self.assertTrue(result["errors"])

    def test_valid_empty_optical_frame_is_allowed(self):
        self.assertTrue(self.check(self.parts())["pass"])

    def test_boolean_failure_is_recorded(self):
        parts = self.parts()
        parts["arm_r_upper"] *= 2
        with patch.object(static, "intersection_volume_mm3", side_effect=RuntimeError("test backend failure")):
            result = self.check(parts)
        self.assertFalse(result["pass"])
        self.assertIn("test backend failure", result["errors"][0]["error"])


class AuditRunnerGuards(unittest.TestCase):
    def test_preexisting_evidence_prevents_any_command(self):
        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder) / "coxa-sweep.log"
            log.write_text("existing proof\n")
            with patch.object(sys, "argv", ["runner", "--phase", "verify", "--output-dir", folder]), \
                 patch.object(runner.subprocess, "run") as execute, \
                 contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    runner.main()
                execute.assert_not_called()
            self.assertEqual(log.read_text(), "existing proof\n")

    def test_subprocess_error_keeps_all_other_results(self):
        with tempfile.TemporaryDirectory() as folder:
            def execute(command, **kwargs):
                if any(str(arg).endswith("check_coxa_sweep.py") for arg in command):
                    raise OSError("test launch failure")
                kwargs["stdout"].write("mock result\n")
                return types.SimpleNamespace(returncode=0)
            with patch.object(sys, "argv", ["runner", "--phase", "verify", "--output-dir", folder]), \
                 patch.object(runner.subprocess, "run", side_effect=execute) as commands, \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(runner.main(), 1)
            results = json.loads((Path(folder) / "verify-results.json").read_text())["results"]
            self.assertEqual(len(results), commands.call_count)
            failure = next(row for row in results if row["name"] == "coxa-sweep")
            self.assertEqual(failure["status"], "ERROR")
            self.assertIn("test launch failure", failure["error"])
            self.assertEqual(sum(row["status"] == "ERROR" for row in results), 1)
            self.assertTrue(all(Path(row["log"]).exists() for row in results))

    def test_generate_stops_on_first_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(sys, "argv", ["runner", "--phase", "generate", "--output-dir", folder]), \
                 patch.object(runner.subprocess, "run", return_value=types.SimpleNamespace(returncode=1)) as commands, \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(runner.main(), 1)
            self.assertEqual(commands.call_count, 1)
            self.assertEqual(len(json.loads((Path(folder) / "generate-results.json").read_text())["results"]), 1)


if __name__ == "__main__":
    unittest.main()
