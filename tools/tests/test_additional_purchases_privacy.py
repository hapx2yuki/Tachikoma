"""公開購入台帳の情報境界と134行の保存契約。"""

import copy
import json
import unicodedata
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/issues"))
import public_purchase_ledger as ledger  # noqa: E402


class AdditionalPurchasesPrivacyTests(unittest.TestCase):
    def setUp(self):
        self.payload = json.loads((ROOT / "docs/additional-purchases.json").read_text(encoding="utf-8"))
        self.markdown = (ROOT / "docs/additional-purchases.md").read_text(encoding="utf-8")

    def test_public_contract_and_four_plan_counts(self):
        ledger.assert_public_safe(self.payload, self.markdown)
        self.assertEqual(len(self.payload["items"]), 134)
        self.assertEqual(
            self.payload["current_plan"]["coverage"]["category_counts"],
            {"print_first_adopted": 67, "borrow_or_verify": 30, "deferred": 31, "not_required": 6},
        )
        self.assertEqual(self.payload["current_plan"]["immediate_purchase_required"], 0)
        for item in self.payload["items"]:
            self.assertIn("current_verification_boundary", item)
            self.assertIn("reason", item["current_plan"])
            self.assertIn("next_action", item["current_plan"])

    def test_forbidden_key_and_phrase_are_negative_cases(self):
        bad = copy.deepcopy(self.payload)
        bad["purchase_" + "lots"] = [{"private": True}]
        with self.assertRaises(ValueError):
            ledger.assert_public_safe(bad, self.markdown)
        with self.assertRaises(ValueError):
            leaked = "\n" + "A" + "mazon " + "B0" + "ABC12345\n"
            ledger.assert_public_safe(self.payload, self.markdown + leaked)

    def test_required_public_fields_match_private_source(self):
        private = ROOT / Path("outputs").joinpath("private", "additional-purchases-private-source-20260906.json")
        self.assertTrue(private.is_file())
        raw = json.loads(private.read_text(encoding="utf-8"))
        public_by_id = {row["id"]: row for row in self.payload["items"]}
        self.assertEqual({row["id"] for row in raw["items"]}, set(public_by_id))
        for row in raw["items"]:
            out = public_by_id[row["id"]]
            for field in ("name", "specification", "required_total", "classification", "current_usable_quantity", "current_shortage"):
                expected = ledger._sanitize_text(row[field]) if isinstance(row[field], str) else row[field]
                self.assertEqual(out[field], expected, row["id"] + ":" + field)
            for field in ("status", "reason", "next_action", "immediate_purchase_required"):
                expected = row["current_plan"].get(field)
                if isinstance(expected, str):
                    expected = ledger._sanitize_text(expected)
                self.assertEqual(out["current_plan"].get(field), expected, row["id"] + ":current_plan." + field)

    def test_private_backup_is_ignored(self):
        result = subprocess.run(
            ["git", "check-ignore", "--quiet", str(Path("outputs").joinpath("private", "additional-purchases-private-source-20260906.json"))],
            cwd=ROOT,
        )
        self.assertEqual(result.returncode, 0)

    def test_unknown_nested_fields_and_nondeterministic_markdown_fail_closed(self):
        for location in ("top", "item", "plan", "coverage"):
            bad = copy.deepcopy(self.payload)
            if location == "top":
                bad["unexpected"] = "must fail"
            elif location == "item":
                bad["items"][0]["unexpected"] = "must fail"
            elif location == "plan":
                bad["current_plan"]["unexpected"] = "must fail"
            else:
                bad["current_plan"]["coverage"]["unexpected"] = "must fail"
            with self.assertRaises(ValueError, msg=location):
                ledger.assert_public_safe(bad, ledger.render_markdown(bad))
        with self.assertRaisesRegex(ValueError, "deterministic"):
            ledger.assert_public_safe(self.payload, self.markdown + "\n任意の追記\n")

    def test_status_maps_counts_lists_and_purchase_zero_are_recomputed(self):
        item_id = self.payload["items"][0]["id"]
        bad_map = copy.deepcopy(self.payload)
        bad_map["current_plan"]["plan_status_by_item"][item_id] = "deferred"
        with self.assertRaisesRegex(ValueError, "plan_status_by_item"):
            ledger.assert_public_safe(bad_map, ledger.render_markdown(bad_map))

        bad_item = copy.deepcopy(self.payload)
        bad_item["items"][0]["current_plan"]["status"] = "deferred"
        with self.assertRaisesRegex(ValueError, "plan_status_by_item|category_counts"):
            ledger.assert_public_safe(bad_item, ledger.render_markdown(bad_item))

        bad_count = copy.deepcopy(self.payload)
        bad_count["current_plan"]["coverage"]["category_counts"]["deferred"] += 1
        with self.assertRaisesRegex(ValueError, "category_counts"):
            ledger.assert_public_safe(bad_count, ledger.render_markdown(bad_count))

        bad_purchase = copy.deepcopy(self.payload)
        bad_purchase["current_plan"]["immediate_purchase_item_ids"] = [item_id]
        with self.assertRaisesRegex(ValueError, "immediate_purchase"):
            ledger.assert_public_safe(bad_purchase, ledger.render_markdown(bad_purchase))

    def test_general_absolute_path_values_fail_but_urls_and_repository_paths_pass(self):
        negative_values = [
            "/" + "etc/" + "passwd",
            "/" + "Volumes/" + "private/" + "secret",
            "/" + "opt/" + "private/" + "secret",
            "/" + "Library/" + "private/" + "secret",
            "~" + "/" + "private" + "/" + "file",
            "C:" + "/" + "Users" + "/" + "private.txt",
            "file:" + "/" + "/" + "Users/private.txt",
            "/" + "Users" + "/" + "é" + "/秘密.txt",
            "/" + "Users" + "/" + unicodedata.normalize("NFD", "é") + "/秘密.txt",
            "/" + "sensitive" + "/" + "data.txt",
            "C:" + chr(92) + "Users" + chr(92) + "秘密" + chr(92) + "private.txt",
        ]
        for value in negative_values:
            bad = copy.deepcopy(self.payload)
            bad["current_plan"]["scope_for_test"] = value
            # Keep the test focused on the value gate before the schema's
            # unknown-field rejection, so place the value in an allowed field.
            bad["current_plan"].pop("scope_for_test")
            bad["scope"] = value
            with self.assertRaises(ValueError, msg=value):
                ledger.assert_public_safe(bad, ledger.render_markdown(bad))
        for value in (
            "https://example.com/" + "etc/passwd",
            "https://example.com/Users/秘密.txt",
            "docs/" + "etc/passwd",
            "docs/Users/秘密.txt",
        ):
            bad = copy.deepcopy(self.payload)
            bad["scope"] = value
            ledger.assert_public_safe(bad, ledger.render_markdown(bad))


if __name__ == "__main__":
    unittest.main()
