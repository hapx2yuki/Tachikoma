#!/usr/bin/env python3
"""新規動作品質判定の境界を軌跡fixtureで確認する。"""
import json
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import check_print_first_motion_quality as Q


def trajectory(segment, commands, positions, yaws, holding=None):
    rows = []
    for index, (command, position, yaw) in enumerate(zip(commands, positions, yaws)):
        row = {
            "time": float(index),
            "segment": segment,
            "command": list(command),
            "base_pos": [float(value) for value in position],
            "rpy_deg": [0.0, 0.0, float(yaw)],
        }
        if holding is not None:
            row["holding"] = bool(holding[index])
        rows.append(row)
    return rows


def marked_result(name, kind, rows, segments=None, criteria=None, **extra):
    criteria = dict(criteria or {})
    if kind in {"long_forward", "turn"}:
        criteria.setdefault("expected_command_vector", list(rows[0]["command"][:3]))
    elif kind == "stop_restart":
        restart_rows = [row for row in rows if row["segment"] == "restart"]
        restart_criteria = dict(criteria.get("restart", {}))
        if restart_rows:
            restart_criteria.setdefault(
                "expected_command_vector", list(restart_rows[0]["command"][:3])
            )
        criteria["restart"] = restart_criteria
    case = {
        "name": name,
        "motion_quality_case": True,
        "motion_quality_kind": kind,
        "motion_quality_segments": segments,
        "motion_quality_criteria": criteria,
    }
    case.update(extra)
    return {
        "case": case,
        "status": "PASS",
        "checks": {
            "completed": True,
            "numeric_stability": True,
            "no_fall": True,
            "initial_self_penetration_le_0p1mm": True,
            "inputs_unchanged": True,
        },
        "timeseries": rows,
    }


class PrintFirstMotionQualityTests(unittest.TestCase):
    def _formal_quality_results(self):
        forward = marked_result(
            "formal-forward", "long_forward",
            trajectory("forward", [[1.0, 0.0, 0.0, 115.0]] * 3,
                        [[0.0, 0.0, 0.115], [0.1, 0.0, 0.115], [0.2, 0.01, 0.115]],
                        [0.0, 0.0, 0.0]),
            segments=["forward"],
            criteria={"max_yaw_cumulative_deg": 10, "max_lateral_mm": 30,
                      "lateral_fraction": 0.25, "min_progress_m": 0.05},
        )
        turn_positive = marked_result(
            "formal-turn-positive", "turn",
            trajectory("turn_positive", [[0.0, 0.0, 1.0, 115.0]] * 3,
                        [[0.0, 0.0, 0.115]] * 3, [0.0, 10.0, 20.0]),
            segments=["turn_positive"],
            criteria={"expected_turn_sign": 1, "min_turn_deg": 10},
        )
        turn_negative = marked_result(
            "formal-turn-negative", "turn",
            trajectory("turn_negative", [[0.0, 0.0, -1.0, 115.0]] * 3,
                        [[0.0, 0.0, 0.115]] * 3, [0.0, -10.0, -20.0]),
            segments=["turn_negative"],
            criteria={"expected_turn_sign": -1, "min_turn_deg": 10},
        )
        stop_rows = trajectory(
            "stop", [[0.0, 0.0, 0.0, 115.0]] * 3,
            [[0.0, 0.0, 0.115], [0.001, 0.0, 0.115], [0.0, 0.0, 0.115]],
            [0.0, 0.0, 0.0], holding=[False, True, True],
        )
        restart_rows = trajectory(
            "restart", [[0.0, 1.0, 0.0, 115.0]] * 3,
            [[0.0, 0.0, 0.115], [0.0, 0.02, 0.115], [0.0, 0.04, 0.115]],
            [0.0, 0.0, 0.0],
        )
        restart_rows = [{**row, "time": row["time"] + 3.0} for row in restart_rows]
        stop_restart = marked_result(
            "formal-stop-restart", "stop_restart", stop_rows + restart_rows,
            segments=["stop"], criteria={"max_stop_drift_mm": 30,
                                           "max_stop_yaw_deg": 2,
                                           "restart": {"min_progress_m": 0.01}},
        )
        stop_restart["case"]["motion_quality_restart_segments"] = ["restart"]
        return [forward, turn_positive, turn_negative, stop_restart]

    def test_long_forward_passes_explicit_drift_and_progress_limits(self):
        rows = trajectory(
            "forward",
            [[1.0, 0.0, 0.0, 115.0]] * 21,
            [[index * 0.1, 0.0, 0.115] for index in range(21)],
            [0.0] * 21,
        )
        result = marked_result(
            "forward-good",
            "long_forward",
            rows,
            segments=["forward"],
            criteria={"max_yaw_cumulative_deg": 10, "max_lateral_mm": 30, "lateral_fraction": 0.25},
        )
        judgement = Q.evaluate_result_document(result)["judgement"]
        self.assertEqual(judgement["status"], "PASS")
        evaluation = judgement["evaluations"][0]
        self.assertTrue(evaluation["checks"]["positive_progress"])
        self.assertEqual(evaluation["metrics"]["progress_m"], 2.0)
        self.assertEqual(evaluation["metrics"]["max_abs_yaw_cumulative_deg"], 0.0)

    def test_long_forward_that_keeps_turning_fails_even_with_positive_progress(self):
        rows = []
        for index in range(21):
            angle = math.radians(index * 10.0)
            # A circular path keeps moving but accumulates the same yaw drift
            # that the old loose translation-only check could miss.
            rows.append(
                {
                    "time": float(index),
                    "segment": "forward",
                    "command": [1.0, 0.0, 0.0, 115.0],
                    "base_pos": [
                        index * 0.1 + 0.5 * math.sin(angle),
                        0.5 * (1.0 - math.cos(angle)),
                        0.115,
                    ],
                    "rpy_deg": [0.0, 0.0, index * 10.0],
                }
            )
        result = marked_result(
            "forward-looping",
            "long_forward",
            rows,
            segments=["forward"],
            criteria={"max_yaw_cumulative_deg": 10, "max_lateral_mm": 30, "lateral_fraction": 0.25},
        )
        judgement = Q.evaluate_result_document(result)["judgement"]
        self.assertEqual(judgement["status"], "FAIL")
        checks = judgement["evaluations"][0]["checks"]
        self.assertFalse(checks["yaw_cumulative_within_limit"])
        self.assertFalse(checks["lateral_within_limit"])
        self.assertTrue(checks["positive_progress"])

    def test_positive_and_negative_turns_require_the_commanded_sign(self):
        def turn(sign):
            return trajectory(
                "turn",
                [[0.0, 0.0, float(sign), 115.0]] * 11,
                [[0.0, 0.0, 0.115]] * 11,
                [sign * index * 8.0 for index in range(11)],
            )

        for sign in (1, -1):
            result = marked_result(
                f"turn-{sign}",
                "turn",
                turn(sign),
                segments=["turn"],
                criteria={"expected_turn_sign": sign, "min_turn_deg": 30},
            )
            judgement = Q.evaluate_result_document(result)["judgement"]
            self.assertEqual(judgement["status"], "PASS")
            self.assertTrue(judgement["evaluations"][0]["checks"]["turn_sign"])

        reversed_result = marked_result(
            "turn-wrong-sign",
            "turn",
            turn(-1),
            segments=["turn"],
            criteria={"expected_turn_sign": 1, "min_turn_deg": 30},
        )
        reversed_judgement = Q.evaluate_result_document(reversed_result)["judgement"]
        self.assertEqual(reversed_judgement["status"], "FAIL")
        self.assertFalse(reversed_judgement["evaluations"][0]["checks"]["turn_sign"])

        wrong_command = marked_result(
            "turn-wrong-command-sign",
            "turn",
            turn(-1),
            segments=["turn"],
            criteria={"expected_turn_sign": 1, "min_turn_deg": 10},
        )
        wrong_command["case"]["motion_quality_criteria"]["expected_command_vector"] = [
            0.0, 0.0, 1.0
        ]
        wrong_command_judgement = Q.evaluate_result_document(wrong_command)["judgement"]
        self.assertEqual(wrong_command_judgement["status"], "FAIL")
        self.assertFalse(
            wrong_command_judgement["evaluations"][0]["checks"]["command_turn_sign"]
        )

        impure = marked_result(
            "turn-with-translation",
            "turn",
            trajectory(
                "turn",
                [[0.1, 0.0, 1.0, 115.0]] * 3,
                [[0.0, 0.0, 0.115]] * 3,
                [0.0, 10.0, 20.0],
            ),
            segments=["turn"],
            criteria={"expected_turn_sign": 1, "min_turn_deg": 10,
                      "expected_command_vector": [0.0, 0.0, 1.0]},
        )
        impure_judgement = Q.evaluate_result_document(impure)["judgement"]
        self.assertEqual(impure_judgement["status"], "FAIL")
        self.assertFalse(
            impure_judgement["evaluations"][0]["checks"]["command_pure_rotation"]
        )

    def test_long_forward_requires_case_duration_and_constant_command(self):
        rows = trajectory(
            "forward",
            [[1.0, 0.0, 0.0, 115.0]] * 11,
            [[index * 0.1, 0.0, 0.115] for index in range(11)],
            [0.0] * 11,
        )
        result = marked_result("forward-truncated", "long_forward", rows, segments=["forward"])
        result["case"]["segments"] = [{"name": "forward", "duration": 60.0}]
        judgement = Q.evaluate_result_document(result)["judgement"]
        self.assertEqual(judgement["status"], "FAIL")
        self.assertFalse(judgement["evaluations"][0]["checks"]["complete"])

        changing = trajectory(
            "forward",
            [[1.0, 0.0, 0.0, 115.0]] * 5 + [[0.0, 1.0, 0.0, 115.0]] * 5,
            [[index * 0.1, 0.0, 0.115] for index in range(10)],
            [0.0] * 10,
        )
        changing_result = marked_result(
            "forward-command-change", "long_forward", changing, segments=["forward"]
        )
        changing_judgement = Q.evaluate_result_document(changing_result)["judgement"]
        self.assertFalse(
            changing_judgement["evaluations"][0]["checks"]["command_constant_forward"]
        )

        lateral_forward = marked_result(
            "forward-lateral-command",
            "long_forward",
            trajectory(
                "forward",
                [[1.0, 0.0, 0.0, 115.0]] * 3,
                [[0.0, 0.0, 0.115], [0.1, 0.0, 0.115], [0.2, 0.0, 0.115]],
                [0.0, 0.0, 0.0],
            ),
            segments=["forward"],
            criteria={"expected_command_vector": [0.0, 1.0, 0.0]},
        )
        lateral_judgement = Q.evaluate_result_document(lateral_forward)["judgement"]
        self.assertEqual(lateral_judgement["status"], "FAIL")
        self.assertFalse(
            lateral_judgement["evaluations"][0]["checks"]["command_matches_expected"]
        )

        missing_expected = marked_result(
            "forward-missing-command-vector",
            "long_forward",
            trajectory(
                "forward",
                [[0.0, 1.0, 0.0, 115.0]] * 3,
                [[0.0, 0.0, 0.115], [0.0, 0.1, 0.115], [0.0, 0.2, 0.115]],
                [0.0, 0.0, 0.0],
            ),
            segments=["forward"],
        )
        del missing_expected["case"]["motion_quality_criteria"]["expected_command_vector"]
        missing_judgement = Q.evaluate_result_document(missing_expected)["judgement"]
        self.assertEqual(missing_judgement["status"], "INPUT_ERROR")
        self.assertIn("expected_command_vector", missing_judgement["errors"][0])

    def test_multiple_stop_segments_are_judged_separately_and_restart_is_required(self):
        rows = (
            trajectory(
                "stop-a",
                [[0.0, 0.0, 0.0, 115.0]] * 3,
                [[0.0, 0.0, 0.115], [0.01, 0.0, 0.115], [0.0, 0.0, 0.115]],
                [0.0, 0.0, 0.0],
                holding=[False, True, True],
            )
            + [
                {**row, "time": row["time"] + 3.0}
                for row in trajectory(
                "stop-b",
                [[0.0, 0.0, 0.0, 115.0]] * 3,
                [[0.0, 0.0, 0.115], [0.01, 0.0, 0.115], [0.0, 0.0, 0.115]],
                [0.0, 0.0, 0.0],
                holding=[False, True, True],
                )
            ]
        )
        result = marked_result(
            "two-stops",
            "stop",
            rows,
            segments=["stop-a", "stop-b"],
            criteria={"max_stop_drift_mm": 30, "max_stop_yaw_deg": 2},
        )
        judgement = Q.evaluate_result_document(result)["judgement"]
        self.assertEqual(judgement["status"], "PASS")
        self.assertEqual(len(judgement["evaluations"]), 2)

        restart_case = marked_result(
            "restart-without-restart-segment",
            "stop_restart",
            rows,
            segments=["stop-a"],
        )
        restart_judgement = Q.evaluate_result_document(restart_case)["judgement"]
        self.assertEqual(restart_judgement["status"], "INPUT_ERROR")
        self.assertIn("restart segment", restart_judgement["errors"][0])

    def test_stop_with_small_drift_but_without_gait_holding_fails(self):
        rows = trajectory(
            "stop",
            [[0.0, 0.0, 0.0, 115.0]] * 3,
            [[0.0, 0.0, 0.115], [0.001, 0.0, 0.115], [0.0, 0.0, 0.115]],
            [0.0, 0.0, 0.0],
            holding=[False, False, False],
        )
        result = marked_result(
            "stop-without-holding", "stop", rows, segments=["stop"],
            criteria={"max_stop_drift_mm": 30, "max_stop_yaw_deg": 2},
        )
        result["case"]["segments"] = [{"name": "stop", "duration": 1.0}]
        judgement = Q.evaluate_result_document(result)["judgement"]
        self.assertEqual(judgement["status"], "FAIL")
        self.assertFalse(judgement["evaluations"][0]["checks"]["gait_holding_reached"])

    def test_stop_rejects_string_holding_flag_instead_of_truthiness(self):
        """文字列の ``"false"`` を真偽値として受け入れない。"""
        rows = trajectory(
            "stop",
            [[0.0, 0.0, 0.0, 115.0]] * 3,
            [[0.0, 0.0, 0.115]] * 3,
            [0.0, 0.0, 0.0],
            holding=[False, True, True],
        )
        rows[1]["holding"] = "false"
        result = marked_result(
            "stop-string-holding", "stop", rows, segments=["stop"],
            criteria={"max_stop_drift_mm": 30, "max_stop_yaw_deg": 2},
        )
        judgement = Q.evaluate_result_document(result)["judgement"]
        self.assertEqual(judgement["status"], "INPUT_ERROR")
        self.assertIn("holding", judgement["errors"][0])

    def test_formal_quality_aggregate_requires_all_four_directions_and_restart(self):
        records = [Q.evaluate_result_document(result)
                   for result in self._formal_quality_results()]
        coverage = Q.formal_quality_coverage(records)
        self.assertTrue(coverage["complete_and_passed"], coverage)
        self.assertEqual(Q.aggregate_status(records), "PASS")

        missing_negative = records[:2] + records[3:]
        coverage = Q.formal_quality_coverage(missing_negative)
        self.assertIn("turn_negative", coverage["missing_cases"])
        self.assertEqual(Q.aggregate_status(missing_negative), "UNVERIFIED")

        diagnostic = marked_result(
            "diagnostic-only", "initial_stand",
            trajectory("stand", [[0.0, 0.0, 0.0, 115.0]] * 2,
                        [[0.0, 0.0, 0.115]] * 2, [0.0, 0.0], holding=[True, True]),
            segments=["stand"], quality_eligible=False,
        )
        diagnostic_records = [Q.evaluate_result_document(diagnostic)]
        self.assertFalse(Q.formal_quality_coverage(diagnostic_records)["complete_and_passed"])
        self.assertEqual(Q.aggregate_status(diagnostic_records), "MODEL_VALIDITY_ONLY")

        partial = self._formal_quality_results()
        partial[0]["status"] = "FAIL"
        partial_records = [Q.evaluate_result_document(result) for result in partial]
        self.assertEqual(Q.aggregate_status(partial_records), "FAIL")
        self.assertIn("forward", Q.formal_quality_coverage(partial_records)["failed_cases"])

        mixed_records = [Q.evaluate_result_document(result) for result in self._formal_quality_results()]
        mixed_records[0]["judgement"]["status"] = "FAIL"
        mixed_records[1]["judgement"]["status"] = "UNVERIFIED"
        self.assertEqual(Q.aggregate_status(mixed_records), "FAIL")

    def test_stand_and_shortforward_are_model_diagnostics_only(self):
        rows = trajectory(
            "stand",
            [[0.0, 0.0, 0.0, 115.0]] * 3,
            [[0.0, 0.0, 0.115]] * 3,
            [0.0, 0.0, 1.0],
            holding=[True, True, True],
        )
        result = marked_result(
            "initial-stand",
            "initial_stand",
            rows,
            segments=["stand"],
            quality_eligible=False,
        )
        judgement = Q.evaluate_result_document(result)["judgement"]
        self.assertEqual(judgement["status"], "MODEL_VALIDITY_ONLY")
        self.assertFalse(judgement["quality_eligible"])
        self.assertIsNone(judgement["quality_pass"])

    def test_legacy_result_is_rejected_and_source_is_not_rewritten(self):
        rows = trajectory(
            "walk",
            [[1.0, 0.0, 0.0, 115.0]] * 2,
            [[0.0, 0.0, 0.115], [0.1, 0.0, 0.115]],
            [0.0, 0.0],
        )
        old = {"case": {"name": "old-result"}, "status": "PASS", "timeseries": rows}
        with tempfile.TemporaryDirectory(prefix="tachikoma-motion-quality-") as temp:
            folder = Path(temp)
            source = folder / "old.json"
            output = folder / "report.json"
            source.write_text(json.dumps(old))
            before = source.read_bytes()
            command = [
                sys.executable,
                str(ROOT / "tools/check_print_first_motion_quality.py"),
                "--input",
                str(source),
                "--output",
                str(output),
            ]
            completed = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 1)
            report = json.loads(output.read_text())
            self.assertEqual(report["status"], "INPUT_ERROR")
            self.assertEqual(report["records"][0]["judgement"]["status"], "INPUT_ERROR")
            self.assertEqual(source.read_bytes(), before)

    def test_public_report_does_not_expose_external_case_paths(self):
        rows = trajectory(
            "forward",
            [[1.0, 0.0, 0.0, 115.0]] * 3,
            [[0.0, 0.0, 0.115], [0.1, 0.0, 0.115], [0.2, 0.0, 0.115]],
            [0.0, 0.0, 0.0],
        )
        result = marked_result("path-scrub", "long_forward", rows, segments=["forward"])
        with tempfile.TemporaryDirectory(prefix="tachikoma-motion-quality-path-") as temp:
            source = Path(temp) / "source.json"
            output = Path(temp) / "report.json"
            result["case"]["model_path"] = str(source)
            source.write_text(json.dumps(result))
            report = Q.build_report([source], output)
            rendered = json.dumps(report)
            self.assertNotIn(str(source), rendered)

    def test_invalid_initial_physics_cannot_become_quality_pass(self):
        rows = trajectory(
            "forward",
            [[1.0, 0.0, 0.0, 115.0]] * 3,
            [[0.0, 0.0, 0.115], [0.1, 0.0, 0.115], [0.2, 0.0, 0.115]],
            [0.0, 0.0, 0.0],
        )
        result = marked_result("invalid-model", "long_forward", rows, segments=["forward"])
        result["status"] = "INVALID_INITIAL_CONTACT_MODEL"
        judgement = Q.evaluate_result_document(result)["judgement"]
        self.assertEqual(judgement["status"], "UNVERIFIED")
        self.assertTrue(judgement["quality_pass"])
        self.assertFalse(judgement["combined_pass"])

    def test_unverified_or_missing_source_status_stays_out_of_combined_pass(self):
        rows = trajectory(
            "forward",
            [[1.0, 0.0, 0.0, 115.0]] * 3,
            [[0.0, 0.0, 0.115], [0.1, 0.0, 0.115], [0.2, 0.0, 0.115]],
            [0.0, 0.0, 0.0],
        )
        for source_status in ("FAIL", "UNVERIFIED", "PENDING_EXTERNAL_PROXY_PROOF", None):
            result = marked_result(
                f"source-{source_status}", "long_forward", rows, segments=["forward"]
            )
            if source_status is None:
                del result["status"]
            else:
                result["status"] = source_status
            judgement = Q.evaluate_result_document(result)["judgement"]
            self.assertEqual(judgement["quality_pass"], True)
            expected_combined = "FAIL" if source_status == "FAIL" else "UNVERIFIED"
            self.assertEqual(judgement["combined_status"], expected_combined)
            self.assertFalse(judgement["combined_pass"])

        pending = marked_result(
            "pending-source-status", "long_forward", rows, segments=["forward"]
        )
        pending["status"] = "PENDING_EXTERNAL_PROXY_PROOF"
        pending_judgement = Q.evaluate_result_document(pending)["judgement"]
        self.assertEqual(
            pending_judgement["upstream"]["status"],
            "EXTERNAL_PROXY_PROOF_PENDING",
        )

        pending_bad = marked_result(
            "pending-with-failed-check", "long_forward", rows, segments=["forward"]
        )
        pending_bad["status"] = "PENDING_EXTERNAL_PROXY_PROOF"
        pending_bad["checks"]["no_fall"] = False
        pending_bad_judgement = Q.evaluate_result_document(pending_bad)["judgement"]
        self.assertEqual(pending_bad_judgement["combined_status"], "FAIL")
        self.assertEqual(
            pending_bad_judgement["upstream"]["status"], "UPSTREAM_CHECK_FAILED"
        )

    def test_final_motion_quality_requires_parent_link_collision_checks(self):
        rows = trajectory(
            "forward",
            [[1.0, 0.0, 0.0, 115.0]] * 3,
            [[0.0, 0.0, 0.115], [0.1, 0.0, 0.115], [0.2, 0.0, 0.115]],
            [0.0, 0.0, 0.0],
        )
        result = marked_result("final-parent-filter", "long_forward", rows, segments=["forward"])
        result["case"]["model"] = {
            "model_kind": "final_integrated",
            "include_parent_collision": False,
        }
        judgement = Q.evaluate_result_document(result)["judgement"]
        self.assertEqual(judgement["quality_pass"], True)
        self.assertEqual(judgement["combined_status"], "UNVERIFIED")
        self.assertEqual(
            judgement["upstream"]["status"], "FINAL_MODEL_PARENT_COLLISION_DISABLED"
        )


if __name__ == "__main__":
    unittest.main()
