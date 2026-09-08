#!/usr/bin/env python3
"""追加購入台帳の区分・品名・作業理由の回帰契約。"""
import json
from collections import Counter
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
LEDGER_PATH = ROOT / "docs/additional-purchases.json"
MARKDOWN_PATH = ROOT / "docs/additional-purchases.md"
STATUS_KEYS = ("print_first_adopted", "borrow_or_verify", "deferred", "not_required")


class AdditionalPurchasesContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ledger = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
        cls.items = cls.ledger["items"]
        cls.plan = cls.ledger["current_plan"]
        cls.markdown = MARKDOWN_PATH.read_text(encoding="utf-8")

    def test_top_plan_and_item_aggregation_are_identical(self):
        """トップ計画と134行のcurrent_plan.statusを同じ正本から再現する。"""
        self.assertEqual(len(self.items), 134)
        ids = [row["id"] for row in self.items]
        self.assertEqual(len(ids), len(set(ids)))
        item_status = {row["id"]: row["current_plan"]["status"] for row in self.items}
        self.assertEqual(Counter(item_status.values()), Counter({
            "print_first_adopted": 67,
            "borrow_or_verify": 30,
            "deferred": 31,
            "not_required": 6,
        }))
        for status in STATUS_KEYS:
            listed = self.plan[status]
            self.assertEqual(len(listed), self.plan["coverage"]["category_counts"][status])
            self.assertEqual(set(listed), {
                item_id for item_id, item_status_value in item_status.items()
                if item_status_value == status
            })
        self.assertEqual(
            self.plan["plan_status_by_item"], item_status,
        )
        self.assertTrue(self.plan["coverage"]["classified_once"])

    def test_every_item_has_reason_and_next_action(self):
        """134行すべての品名に、空欄でない現在判断と次作業がある。"""
        for row in self.items:
            with self.subTest(item=row["id"]):
                self.assertTrue(row.get("name"))
                current = row.get("current_plan") or {}
                self.assertTrue(current.get("reason"))
                self.assertTrue(current.get("next_action"))

    def test_ap_027_is_board_specific_and_inventory_is_unconfirmed(self):
        """AP-027へ脚サーボのホーン作業を誤コピーしない。"""
        row = next(item for item in self.items if item["id"] == "AP-027")
        current = row["current_plan"]
        text = f"{current['reason']} {current['next_action']}"
        self.assertEqual(current["status"], "print_first_adopted")
        self.assertIsNone(row["current_usable_quantity"])
        self.assertIsNone(row["current_shortage"])
        self.assertRegex(text, r"ESP32|本体|XIAO|GPIO|USB")
        self.assertNotRegex(text, r"ホーン|中心ねじ|スプライン|PCD|購入14個")
        self.assertIn("print_first_adopted", self.markdown)
        ap_line = next(line for line in self.markdown.splitlines() if "id=\"ap-027\"" in line)
        self.assertIn("ESP32-WROOM-32E型番", ap_line)
        self.assertNotRegex(ap_line, r"購入14個.*ホーン|スプライン.*取付寸法")

    def test_ld220_specific_reason_action_is_not_reused_by_other_items(self):
        """脚サーボ固有の文言が別品目へコピーされていないことを全行で確認する。"""
        source = next(item for item in self.items if item["id"] == "AP-020")["current_plan"]
        source_pair = (source["reason"], source["next_action"])
        exact_matches = []
        forbidden_terms = ("LD-220MG", "購入14個", "個体番号を記録する")
        for item in self.items:
            current = item["current_plan"]
            if (current["reason"], current["next_action"]) == source_pair:
                exact_matches.append(item["id"])
            if item["id"] != "AP-020":
                text = f"{current['reason']} {current['next_action']}"
                self.assertFalse(
                    any(term in text for term in forbidden_terms),
                    f"LD-220MG-specific wording leaked to {item['id']}",
                )
        self.assertEqual(exact_matches, ["AP-020"])

    def test_copy_screen_metadata_is_machine_readable(self):
        audit = self.plan["item_reason_action_audit"]
        self.assertEqual(audit["status"], "PASS")
        self.assertEqual(audit["audited_item_count"], len(self.items))
        self.assertTrue(audit["all_required_fields_nonempty"])
        self.assertTrue(audit["plan_status_aggregation_matches_top_lists"])
        self.assertEqual(audit["cross_item_copy_screen"]["suspicious_item_ids"], [])
        self.assertEqual(
            audit["cross_item_copy_screen"]["exact_copy_guard"]["reference_pair_matches_only"],
            ["AP-020"],
        )
        self.assertEqual(audit["cross_item_copy_screen"]["exact_copy_guard"]["status"], "PASS")
        self.assertEqual(audit["ap_027_guard"]["current_usable_quantity_must_remain"], None)


if __name__ == "__main__":
    unittest.main()
