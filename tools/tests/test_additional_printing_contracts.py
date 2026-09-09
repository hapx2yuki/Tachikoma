#!/usr/bin/env python3
"""追加印刷台帳と現行print-first正本の参照契約。"""
import hashlib
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / "docs/additional-printing.json"
MANIFEST = ROOT / "docs/print-first-manifest.json"
MARKDOWN = ROOT / "docs/additional-printing.md"


class AdditionalPrintingContractTests(unittest.TestCase):
    def test_historical_139_is_excluded_and_manifest_values_are_current(self):
        ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        summary = ledger["summary"]
        current = summary["current_print_first_required"]
        layers = manifest["production_quantity_layers"]
        required = layers["machine_design_required"]
        self.assertEqual(summary["assembly_pieces"], 139)
        self.assertFalse(summary["assembly_pieces_is_current_print_first_required"])
        self.assertEqual(current["source_path"], "docs/print-first-manifest.json")
        self.assertTrue(current["source_manifest_exists"])
        self.assertEqual(current["source_manifest_sha256"], hashlib.sha256(
            MANIFEST.read_bytes()).hexdigest())
        self.assertEqual(current["quantity"], required["total"])
        self.assertEqual(current["quantity_layers"], {
            "machine_design_required": required["total"],
            "prototype_quantity": required["prototype_quantity"]["total"],
            "remaining_after_prototype": required["remaining_after_prototype"]["total"],
            "conditional_new_shin_shell_inclusive_maximum": layers[
                "conditional_new_shin_shell"]["maximum_machine_total"],
        })
        self.assertEqual(current["currently_printable_quantity"], 0)
        release = current["release_layers"]
        self.assertEqual(release["A_minimum_fit_prototype"]["quantity"], 31)
        self.assertEqual(release["A_minimum_fit_prototype"]["breakdown"], {
            "body": 16,
            "unique_leg_parts": 10,
            "tpu_shoe": 1,
            "pla_spacers": 4,
            "underfoot": 5,
            "total": 31,
        })
        self.assertEqual(release["A_minimum_fit_prototype"]["fit_scope"], {"tpu_shoe": 1, "leg": 1})
        self.assertTrue(release["A_minimum_fit_prototype"]["included_in_initial_prototype_quantity"])
        self.assertEqual(release["B_remaining_after_prototype"]["quantity"], 25)
        self.assertEqual(release["C_conditional_new_shin_shell"]["quantity"], 4)
        self.assertEqual(release["C_conditional_new_shin_shell"]["maximum_machine_total"], 60)
        self.assertEqual(release["invariant"]["A_plus_B"], 56)
        self.assertTrue(release["invariant"]["C_is_exclusive_with_existing_shell_reuse"])
        self.assertTrue(release["invariant"]["C_is_not_added_to_A_plus_B"])
        self.assertTrue(release["C_conditional_new_shin_shell"]["excluded_from_A_and_B"])
        self.assertTrue(all(
            row["currently_printable_quantity"] == 0
            for name, row in release.items()
            if name != "invariant"
        ))

    def test_release_layers_reconcile_from_manifest_component_rows(self):
        """A/B are sums of the generated real rows; C stays exclusive."""
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        layers = manifest["production_quantity_layers"]
        machine = layers["machine_design_required"]
        mass = manifest["mass_summary"]
        quantity_keys = mass["quantity_layer_keys"]
        design_rows = mass["quantity_layer_mass"][quantity_keys["design"]]["components"]
        prototype_rows = mass["quantity_layer_mass"][quantity_keys["prototype"]]["components"]
        remaining_rows = mass["quantity_layer_mass"][quantity_keys["remaining"]]["components"]

        def by_scope(rows, scope):
            return sum(int(row["quantity"]) for row in rows if row["scope"] == scope)

        def by_name(rows, name):
            matches = [int(row["quantity"]) for row in rows if row["logical_name"] == name]
            self.assertEqual(len(matches), 1, name)
            return matches[0]

        design = {
            "body": by_scope(design_rows, "body"),
            "legs": by_scope(design_rows, "legs"),
            "tpu_shoes": by_name(design_rows, "tpu_shoe"),
            "pla_spacers": by_name(design_rows, "pla_spacer"),
        }
        prototype = {
            "body": by_scope(prototype_rows, "body"),
            "legs": by_scope(prototype_rows, "legs"),
            "tpu_shoes": by_name(prototype_rows, "tpu_shoe"),
            "pla_spacers": by_name(prototype_rows, "pla_spacer"),
        }
        remaining = {
            key: by_scope(remaining_rows, "body") if key == "body" else
            by_scope(remaining_rows, "legs") if key == "legs" else
            by_name(remaining_rows, "tpu_shoe") if key == "tpu_shoes" else
            by_name(remaining_rows, "pla_spacer")
            for key in design
        }
        release = layers["release_layers"]
        a = release["A_minimum_fit_prototype"]
        b = release["B_remaining_after_prototype"]
        self.assertEqual(design, {
            "body": machine["body"], "legs": machine["legs"],
            "tpu_shoes": machine["tpu_shoes"], "pla_spacers": machine["pla_spacers"],
        })
        self.assertEqual(a["breakdown"]["body"], prototype["body"])
        self.assertEqual(a["breakdown"]["unique_leg_parts"], prototype["legs"])
        self.assertEqual(a["breakdown"]["tpu_shoe"], prototype["tpu_shoes"])
        self.assertEqual(a["breakdown"]["pla_spacers"], prototype["pla_spacers"])
        self.assertEqual(a["quantity"], sum(prototype.values()))
        self.assertEqual(b["quantity"], sum(remaining.values()))
        self.assertEqual(a["quantity"] + b["quantity"], machine["total"])
        self.assertEqual(release["invariant"]["A_plus_B"], machine["total"])
        c = release["C_conditional_new_shin_shell"]
        shell = layers["conditional_new_shin_shell"]
        self.assertEqual(c["quantity"], shell["quantity"])
        self.assertEqual(c["maximum_machine_total"], machine["total"] + c["quantity"])
        self.assertTrue(c["excluded_from_A_and_B"])
        self.assertEqual(c["exclusive_with"]["quantity"], c["quantity"])

    def test_markdown_states_the_same_boundary(self):
        text = MARKDOWN.read_text(encoding="utf-8")
        self.assertIn("summary.assembly_pieces=139", text)
        self.assertIn("assembly_pieces_is_current_print_first_required=false", text)
        self.assertIn("56個、初回31個、残り25個、条件付き新規脛殻込み最大60個", text)
        self.assertIn("今印刷可数0", text)
        self.assertIn("Aは**初回全体仮組み31個**", text)
        self.assertIn("全体仮組み用body16＋左右/前後の固有脚部品10＋1脚分足裏5", text)
        self.assertIn("BはAの合格後に進める残り25個", text)
        self.assertIn("Cは既存脛殻4個を加工できない場合だけ選ぶ新規4個", text)


if __name__ == "__main__":
    unittest.main()
