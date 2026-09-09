"""本文なしGitHubスナップショットの契約試験。"""

import json
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT / "docs/audits/20260905-round2/github-safe-snapshot-20260906.json"
sys.path.insert(0, str(ROOT / "tools/issues"))
import fetch_public_snapshot as fetch  # noqa: E402


class SafeSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))

    def test_issue_range_comments_and_project_contract(self):
        fetch.validate_safe_snapshot(self.snapshot)
        self.assertEqual([row["number"] for row in self.snapshot["issues"]], list(range(3, 110)))
        self.assertEqual(sum(len(value) for value in self.snapshot["comments"].values()), 16)
        self.assertEqual(len(self.snapshot["project"]["items"]), 96)
        fetch.assert_no_raw_body(self.snapshot)
        for row in self.snapshot["issues"]:
            self.assertEqual(
                set(row),
                {"number", "state", "title", "url", "labels", "updatedAt", "body_sha256"},
            )
            self.assertRegex(row["body_sha256"], r"^[0-9a-f]{64}$")
        for comment in self.snapshot["comments"]["81"]:
            self.assertEqual(set(comment), {"id", "created_at", "updated_at", "body_sha256"})

    def test_raw_body_field_is_rejected(self):
        bad = {"issues": [{"body": "secret"}]}
        with self.assertRaises(ValueError):
            fetch.assert_no_raw_body(bad)

    def test_unknown_top_and_nested_fields_are_rejected(self):
        bad = copy.deepcopy(self.snapshot)
        bad["unexpected"] = "secret"
        with self.assertRaisesRegex(ValueError, "unknown field"):
            fetch.validate_safe_snapshot(bad)

        for location in ("issue", "comment", "project", "project_item"):
            bad = copy.deepcopy(self.snapshot)
            if location == "issue":
                bad["issues"][0]["body"] = "raw body"
            elif location == "comment":
                bad["comments"]["81"][0]["body"] = "raw comment"
            elif location == "project":
                bad["project"]["private_token"] = "secret"
            else:
                bad["project"]["items"][0]["body"] = "raw body"
            with self.subTest(location=location):
                with self.assertRaisesRegex(ValueError, "unknown field|raw body"):
                    fetch.validate_safe_snapshot(bad)

    def test_project_issue_url_is_exact_repository_issue(self):
        bad = copy.deepcopy(self.snapshot)
        bad["project"]["items"][0]["url"] = "https://github.com/hapx2yuki/Tachikoma/pull/3"
        with self.assertRaisesRegex(ValueError, "canonical Issue URL"):
            fetch.validate_safe_snapshot(bad)

    def test_live_refetch_drops_raw_and_unknown_api_fields(self):
        issue_row = {
            "number": 3,
            "state": "OPEN",
            "title": "Issue 3",
            "url": "https://github.com/hapx2yuki/Tachikoma/issues/3",
            "labels": [{"name": "prio/P0"}],
            "updatedAt": "2026-09-06T00:00:00Z",
            "body": "secret raw body",
            "private_token": "secret token",
        }
        with patch.object(fetch, "run_gh_json", return_value=[issue_row]):
            rows = fetch.fetch_issue_metadata()
        self.assertEqual(set(rows[0]), {
            "number", "state", "title", "url", "labels", "updatedAt", "body_sha256",
        })
        self.assertNotIn("secret", json.dumps(rows, ensure_ascii=False))

        comment_payload = json.dumps([{
            "id": 123,
            "created_at": "2026-09-06T00:00:00Z",
            "updated_at": "2026-09-06T00:00:00Z",
            "body": "secret comment",
            "user": {"login": "private-user"},
        }])
        with patch.object(fetch, "run_gh", return_value=comment_payload):
            comments = fetch.fetch_comments(3)
        self.assertEqual(set(comments[0]), {"id", "created_at", "updated_at", "body_sha256"})
        self.assertNotIn("secret", json.dumps(comments, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
