"""#99--#109のProjectメタデータ境界と依存循環試験。"""

from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/issues"))
import issue_publication_data as data  # noqa: E402
import plan  # noqa: E402
import sync_github_issues as sync  # noqa: E402
import sync_project  # noqa: E402
import make_print_first_update_plan as update_plan  # noqa: E402


class IssuePublicationTests(unittest.TestCase):
    def test_plan_and_extra_metadata_are_complete(self):
        self.assertEqual(len(plan.ISSUES), 107)
        self.assertEqual([row["issue_number"] for row in data.EXTRA_ISSUES], list(range(99, 110)))
        self.assertEqual({row["issue_number"] for row in data.EXTRA_ISSUES}, set(range(99, 110)))
        self.assertTrue(all(row["project_status"] in data.PROJECT_STATUSES for row in data.EXTRA_ISSUES))
        self.assertTrue(all(row["project_lane"] in data.PROJECT_LANES for row in data.EXTRA_ISSUES))
        self.assertTrue(all(plan.ISSUES[-11 + index]["issue_body_managed"] is False for index in range(11)))
        data.validate_reference_cycles()

    def test_existing_extra_issue_body_is_not_synced(self):
        extra = next(row for row in plan.ISSUES if row["key"] == "GH-099")
        existing = {"GH-099": {"number": 99, "node_id": "node", "state": "open", "_new": False}}
        with patch.object(sync, "load_existing", return_value=existing), patch.object(sync, "api") as api:
            sync.sync_issues(True, True, {}, [extra])
        api.assert_not_called()

    def test_project_readback_preserves_canonical_item_and_checks_extra(self):
        canonical = {
            "id": "old",
            "content": {"number": 3, "url": "https://github.com/hapx2yuki/Tachikoma/issues/3"},
            "status": "Todo",
            "レーン": "E1 準備",
        }
        extra = {
            "id": "new",
            "content": {"number": 99, "url": "https://github.com/hapx2yuki/Tachikoma/issues/99"},
            "status": "Blocked",
            "レーン": "E8 統合",
        }
        result = sync_project.verify_project_readback(
            {"items": [canonical]}, {"items": [canonical, extra]},
            [next(row for row in plan.ISSUES if row["key"] == "GH-099")],
            require_full_range=False,
        )
        self.assertEqual(result["status"], "PASS")
        changed = {**canonical, "status": "Blocked"}
        with self.assertRaises(RuntimeError):
            sync_project.verify_project_readback(
                {"items": [canonical]}, {"items": [changed, extra]},
                [next(row for row in plan.ISSUES if row["key"] == "GH-099")],
                require_full_range=False,
            )

    def test_project_readback_rejects_non_issue_or_missing_content_url(self):
        base = {
            "id": "item",
            "content": {"number": 3, "url": "https://github.com/hapx2yuki/Tachikoma/issues/3"},
            "status": "Todo",
            "レーン": "E1 準備",
        }
        for url in (
            "https://github.com/other/repo/issues/3",
            "https://github.com/hapx2yuki/Tachikoma/pull/3",
            None,
        ):
            with self.subTest(url=url):
                item = {**base, "content": {"number": 3}}
                if url is not None:
                    item["content"]["url"] = url
                with self.assertRaisesRegex(RuntimeError, "canonical repository URL"):
                    sync_project.verify_project_readback(
                        {"items": [base]}, {"items": [item]}, require_full_range=False
                    )

    def test_update_plan_live_snapshot_rejects_unknown_fields_before_aggregation(self):
        snapshot_path = ROOT / "docs/audits/20260905-round2/github-safe-snapshot-20260906.json"
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        snapshot["unexpected_secret_field"] = "must fail closed"
        with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8") as handle:
            json.dump(snapshot, handle, ensure_ascii=False)
            handle.flush()
            with self.assertRaisesRegex(ValueError, "unknown field"):
                update_plan.live_snapshots(Path(handle.name))


if __name__ == "__main__":
    unittest.main()
