#!/usr/bin/env python3
"""実C++ trace入口の固定/動的/リンク対契約を検査する回帰試験。"""
from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import check_print_first_native_trace as checker  # noqa: E402
import sim_physics as S  # noqa: E402


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _trace_payload() -> dict:
    names = [
        "stand", "forward", "backward", "right", "left", "turn_positive",
        "turn_negative", "stop", "restart_forward", "restart_stop",
    ]
    commands = {
        "stand": (0.0, 0.0, 0.0),
        "forward": (0.0, 1.0, 0.0),
        "backward": (0.0, -1.0, 0.0),
        "right": (1.0, 0.0, 0.0),
        "left": (-1.0, 0.0, 0.0),
        "turn_positive": (0.0, 0.0, 1.0),
        "turn_negative": (0.0, 0.0, -1.0),
        "stop": (0.0, 0.0, 0.0),
        "restart_forward": (0.0, 1.0, 0.0),
        "restart_stop": (0.0, 0.0, 0.0),
    }
    rows = []
    for index, name in enumerate(names):
        rows.append({
            "index": index,
            "time_s": index * 0.02,
            "segment": name,
            "dt_s": 0.0 if index == 0 else 0.02,
            "command": {
                "vx": commands[name][0], "vy": commands[name][1],
                "wz": commands[name][2], "body_h_mm": 115.0,
            },
            "phase": 0.0,
            "moving": not _is_static(commands[name]),
            "ready": True,
            "angles_deg": {joint: 0.0 for joint in S.ALL_JOINTS},
            "enabled": {joint: True for joint in S.ALL_JOINTS},
        })
    initial = copy.deepcopy(rows[0])
    initial.update({
        "index": 0, "time_s": 0.0, "segment": "initial", "dt_s": 0.0,
        "command": {"vx": 0.0, "vy": 0.0, "wz": 0.0, "body_h_mm": 115.0},
        "moving": False,
    })
    for index, row in enumerate(rows, start=1):
        row["index"] = index
        row["time_s"] = index * 0.02
        row["dt_s"] = 0.02
    rows.insert(0, initial)
    fake = "0" * 64
    header_path = ROOT / "firmware/src/print_first_gait.h"
    return {
        "schema_version": 1,
        "status": "GENERATED_NATIVE_TRACE",
        "trace_mode": "test",
        "profile_mode": "candidate_print_first",
        "build_mode": "candidate_print_first",
        "compile_flag": "-DTACHIKOMA_PRINT_FIRST_PROFILE=1",
        "joint_order": list(S.ALL_JOINTS),
        "header": {
            "sha256": _sha(header_path),
            "source_config_sha256": _sha(ROOT / "hardware/src/config.py"),
            "build_json_sha256": fake,
        },
        "build": {"sha256": fake, "build_json_sha256": fake},
        "binary": {"sha256": fake},
        "segments": [
            {"name": name, "duration_s": 0.02, "body_h_mm": 115.0,
             "command": {"vx": commands[name][0], "vy": commands[name][1],
                          "wz": commands[name][2]}}
            for name in names
        ],
        "row_count": len(rows),
        "rows": rows,
    }


def _is_static(command: tuple[float, float, float]) -> bool:
    return command == (0.0, 0.0, 0.0)


class NativeTraceContractTests(unittest.TestCase):
    def test_static_and_dynamic_trace_contract(self):
        result = checker._trace_contract(_trace_payload(), mode="test")
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["coverage"]["status"], "PASS")
        self.assertEqual(result["axis_count"], 20)

    def test_missing_dynamic_direction_fails_closed(self):
        payload = _trace_payload()
        payload["segments"] = [segment for segment in payload["segments"]
                               if _is_static((segment["command"]["vx"],
                                              segment["command"]["vy"],
                                              segment["command"]["wz"]))]
        payload["rows"] = [row for row in payload["rows"]
                            if _is_static((row["command"]["vx"],
                                           row["command"]["vy"],
                                           row["command"]["wz"]))]
        payload["row_count"] = len(payload["rows"])
        result = checker._trace_contract(payload, mode="test")
        self.assertEqual(result["status"], "FAIL")
        self.assertFalse(result["coverage"]["dynamic"]["default_direction_set_complete"])

    def test_all_link_pair_audit_is_required_for_finite_pass(self):
        payload = _trace_payload()
        with tempfile.TemporaryDirectory() as directory:
            trace_path = Path(directory) / "trace.json"
            trace_path.write_text(json.dumps(payload), encoding="utf-8")
            pairs = [{"links": ["link_a", "link_b"], "clean": True,
                      "actual_intersections": [], "errors": []}]
            audit = {
                "status": "PASS_FINITE_TRACE",
                "finite_pose_sweep_requested": True,
                "finite_pose_sweep_complete": True,
                "finite_pose_sweep_clean": True,
                "inputs_unchanged": True,
                "pose_count_matches": True,
                "expected_pose_count_matches": True,
                "coverage": "finite_native_trace",
                "link_pair_coverage": "all_link_pairs",
                "expected_link_pair_count": 1,
                "processed_pose_count": len(payload["rows"]),
                "native_pose_count": len(payload["rows"]),
                "expected_pose_count": len(payload["rows"]),
                "finite_pose_count": len(payload["rows"]),
                "link_names": ["link_a", "link_b"],
                "source_trace_sha256": _sha(trace_path),
                "fixed_base_intersections": [],
                "fixed_base_boolean_errors": [],
                "poses": [{"pose_index": i, "pairs": copy.deepcopy(pairs)}
                          for i in range(len(payload["rows"]))],
            }
            audit_path = Path(directory) / "audit.json"
            audit_path.write_text(json.dumps(audit), encoding="utf-8")
            result = checker.check(trace_path, audit_path, mode="test")
            self.assertEqual(result["status"], "PASS_TEST_TRACE_CONTRACT")

            broken = copy.deepcopy(audit)
            broken["link_pair_coverage"] = "candidate_pairs"
            audit_path.write_text(json.dumps(broken), encoding="utf-8")
            self.assertEqual(checker.check(trace_path, audit_path, mode="test")["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
