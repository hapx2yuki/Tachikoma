#!/usr/bin/env python3
"""Cabin保管方針と生成前後の部品境界を検査するfocused test。"""

from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "hardware" / "src")]

import config as C  # noqa: E402
import export_urdf as E  # noqa: E402
import print_first_assembly as A  # noqa: E402


class PrintFirstCabinStorageTest(unittest.TestCase):
    def test_direct_cli_serializes_active_context_runtime(self):
        """Direct ``__main__`` execution must not select the duplicate module."""
        root = ROOT
        script = textwrap.dedent(
            """
            import runpy
            import sys
            from pathlib import Path

            root = Path(sys.argv[1]).resolve()
            output = Path(sys.argv[2]).resolve()
            sys.path[:0] = [str(root / "tools"), str(root / "hardware" / "src")]
            import export_urdf as E

            original_save = E.save_output_bundle

            def save_in_temp(*args, **kwargs):
                old_out, old_mesh_dir = E.OUT, E.MESH_DIR
                E.OUT = output / "urdf"
                E.MESH_DIR = E.OUT / "meshes"
                try:
                    return original_save(*args, **kwargs)
                finally:
                    E.OUT, E.MESH_DIR = old_out, old_mesh_dir

            E.save_output_bundle = save_in_temp
            runpy.run_path(str(root / "tools" / "print_first_assembly.py"),
                           run_name="__main__")
            """
        )
        with tempfile.TemporaryDirectory(prefix="tachikoma-cabin-cli-") as temp:
            subprocess.run(
                [sys.executable, "-c", script, str(root), temp],
                cwd=root,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            manifest = Path(temp) / "urdf" / "parts_manifest.json"
            self.assertTrue(manifest.is_file())
            import json
            policy = json.loads(manifest.read_text(encoding="utf-8"))[
                "print_first_cabin_storage"
            ]
        runtime = policy["runtime"]
        self.assertEqual(runtime["collection_mode"], "generated_true_assembly")
        self.assertTrue(runtime["storage_filter_applied"])
        self.assertEqual(runtime["source_part_count"], len(runtime["mass_rows"]))
        self.assertNotEqual(runtime.get("status"), "NOT_COLLECTED")

    def test_generated_false_retains_required_cabin_source_meshes(self):
        with A.context(generated=False):
            parts = E.collect_all_parts()
            names = [name for rows in parts.values() for _mesh, _color, name in rows]
            policy = A.cabin_storage_policy_record()
        cabin_names = [name for name in names if name.startswith("Cabin_")]
        self.assertIn("Cabin_Front_Blue", cabin_names)
        self.assertIn("Cabin_Back_Blue_Repaired", cabin_names)
        self.assertNotIn("Cabin_Eye_White#single", cabin_names)
        self.assertEqual(
            set(policy["runtime"]["retained_after_filter_source_part_names"]),
            set(cabin_names),
        )
        self.assertTrue(policy["runtime"]["source_mesh_retained_for_body_generation"])
        self.assertFalse(policy["runtime"]["storage_filter_applied"])

    def test_generated_true_stores_cabin_and_keeps_supports(self):
        with A.context(generated=True):
            parts = E.collect_all_parts()
            names = [name for rows in parts.values() for _mesh, _color, name in rows]
            policy = A.cabin_storage_policy_record()
        self.assertFalse(any(name.startswith("Cabin_") for name in names))
        for required in (
            "pf_chassis", "pf_cabin_rail_l", "pf_cabin_rail_r",
            "pf_electronics_shelf_0", "pf_electronics_shelf_1",
            "pf_electronics_shelf_2", "battery_cradle",
        ):
            self.assertIn(required, names)
        self.assertFalse(any(name.startswith("pf_electronics_post_") for name in names))
        runtime = policy["runtime"]
        self.assertTrue(runtime["storage_filter_applied"])
        self.assertFalse(runtime["source_mesh_retained_for_body_generation"])
        self.assertTrue(runtime["additional_mass_accounted_once"])
        self.assertAlmostEqual(
            runtime["source_total_mass_g"],
            runtime["already_stored_mass_g"] + runtime["additional_stored_mass_g"],
            places=9,
        )
        self.assertGreater(runtime["additional_stored_mass_g"], 0.0)
        self.assertEqual(
            tuple(C.PRINT_FIRST_CABIN_STORAGE["stored_source_prefixes"]),
            ("Cabin_",),
        )


if __name__ == "__main__":
    unittest.main()
