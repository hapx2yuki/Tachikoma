#!/usr/bin/env python3
"""config.pyを正本にした firmware 設定契約の回帰試験。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import config_contract as contract  # noqa: E402


class ConfigContractTests(unittest.TestCase):
    def test_current_header_matches_config_source(self):
        self.assertEqual(contract.compare_firmware_text(), [])

    def test_mutated_firmware_limit_is_rejected(self):
        source = contract.CONFIG_H.read_text(encoding="utf-8")
        mutated = source.replace(
            "constexpr float LIM_YAW = 40.0f;",
            "constexpr float LIM_YAW = 41.0f;",
            1,
        )
        errors = contract.compare_firmware_text(mutated)
        self.assertTrue(errors)
        self.assertTrue(any("LIM_YAW" in error for error in errors))

    def test_electrical_contract_matches_wiring(self):
        self.assertEqual(contract.compare_electrical_text(), [])

    def test_mutated_vbat_pin_is_rejected(self):
        source = contract.CONFIG_H.read_text(encoding="utf-8")
        mutated = source.replace(
            "constexpr int PIN_VBAT = 34;",
            "constexpr int PIN_VBAT = 35;",
            1,
        )
        errors = contract.compare_electrical_text(mutated)
        self.assertTrue(any("PIN_VBAT" in error for error in errors))

    def test_mutated_array_is_rejected(self):
        source = contract.CONFIG_H.read_text(encoding="utf-8")
        mutated = source.replace(
            "constexpr float SWAY_MM[4] = {34.0f, 34.0f, 40.0f, 40.0f};",
            "constexpr float SWAY_MM[4] = {34.0f, 34.0f, 41.0f, 40.0f};",
            1,
        )
        errors = contract.compare_firmware_text(mutated)
        self.assertTrue(any("SWAY_MM[2]" in error for error in errors))

    def test_derived_knee_limits_follow_config_source(self):
        values = contract.canonical_values()
        scalars = values["scalars"]
        self.assertGreater(scalars["D_KNEE_MAX"], scalars["D_KNEE_MIN"])
        self.assertAlmostEqual(
            scalars["D_KNEE_MAX"], 210.2110679755, places=6)
        self.assertAlmostEqual(
            scalars["D_KNEE_MIN"], 119.0764168484, places=6)

    def test_derived_knee_literals_are_kept_at_generated_precision(self):
        source = contract.CONFIG_H.read_text(encoding="utf-8")
        self.assertIn("D_KNEE_MAX = 210.2110679755f", source)
        self.assertIn("D_KNEE_MIN = 119.0764168484f", source)

    def test_mutated_derived_knee_limit_is_rejected_by_strict_tolerance(self):
        source = contract.CONFIG_H.read_text(encoding="utf-8")
        # 1.68e-5mm の丸めでも、一般の 0.05mm 契約では見逃す。
        for name, original, replacement in (
            ("D_KNEE_MAX", "210.2110679755f", "210.2110f"),
            ("D_KNEE_MIN", "119.0764168484f", "119.0764f"),
        ):
            mutated = source.replace(
                f"constexpr float {name} = {original};",
                f"constexpr float {name} = {replacement};",
                1,
            )
            errors = contract.compare_firmware_text(mutated)
            self.assertTrue(any(name in error for error in errors))


if __name__ == "__main__":
    unittest.main()
