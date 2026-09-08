"""公開前プレースホルダー停止の正負試験。"""

from pathlib import Path
import copy
import hashlib
import json
import sys
import unittest
import importlib.util
import unicodedata

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/issues"))
import publication_gate as gate  # noqa: E402


ALLOWLIST_PATH = ROOT / "tools/make_print_first_publication_allowlist.py"


class PublicationGateTests(unittest.TestCase):
    def _load_allowlist_module(self):
        spec = importlib.util.spec_from_file_location(
            "allowlist_negative_test", ALLOWLIST_PATH
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_clean_payload_passes(self):
        gate.assert_no_placeholders({"status": "FINAL_FROZEN", "sha": "abc123"})

    def test_unresolved_template_is_rejected(self):
        for value in ("<FINAL_COMMIT_SHA>", "<DRAFT_PR_URL>", "PENDING_FINAL_FREEZE2"):
            with self.assertRaises(ValueError):
                gate.assert_no_placeholders({"value": value})

    def test_legacy_branch_or_issue_count_is_rejected(self):
        with self.assertRaises(ValueError):
            gate.assert_current_issue_publication({"branch": "codex/audit-20260905"})
        with self.assertRaises(ValueError):
            gate.assert_current_issue_publication({"text": "全96課題を同期する"})

    def test_allowlist_source_group_contains_issue_tools_and_excludes_private_tree(self):
        spec = importlib.util.spec_from_file_location("allowlist_for_test", ALLOWLIST_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        paths = set(module.SOURCE_GROUPS["issue_publication_plan"])
        for expected in (
            "tools/issues/plan.py",
            "tools/issues/issue_map.json",
            "tools/issues/fetch_public_snapshot.py",
            "tools/issues/sync_github_issues.py",
            "tools/issues/sync_project.py",
            "tools/issues/update_github_issues.py",
        ):
            self.assertIn(expected, paths)
        private_prefix = "outputs" + "/" + "private" + "/"
        self.assertFalse(any(path.startswith(private_prefix) for path in paths))

    def test_allowlist_declares_repository_context_and_contract_tests(self):
        module = self._load_allowlist_module()
        for expected in (".gitignore", "AGENTS.md", "README.md", "docs/HANDOFF.md", "docs/assembly.md"):
            self.assertIn(expected, module.SOURCE_GROUPS["repository_context"])
        contract_paths = set(module.SOURCE_GROUPS["publication_contract_tests"])
        self.assertIn("tools/tests/test_issue_append_update.py", contract_paths)
        self.assertIn("tools/tests/test_safe_snapshot_contracts.py", contract_paths)

    def test_stage_gate_requires_exact_allowlist_path_and_recorded_blob_sha(self):
        module = self._load_allowlist_module()
        content = b"stable staged fixture\n"
        digest = hashlib.sha256(content).hexdigest()
        data = {"candidate_documents": [{"path": "README.md", "sha256": digest}]}
        reader = lambda path: content
        result = module.audit_staged_paths(
            data, staged_paths=["README.md"], blob_reader=reader, require_final=False
        )
        self.assertEqual(result["status"], "PASS")
        with self.assertRaisesRegex(ValueError, "SHA"):
            module.audit_staged_paths(
                data, staged_paths=["README.md"], blob_reader=lambda path: b"changed", require_final=False
            )
        with self.assertRaisesRegex(ValueError, "unregistered"):
            module.audit_staged_paths(
                data, staged_paths=["docs/not-declared.md"], blob_reader=reader, require_final=False
            )
        excluded = "outputs" + "/" + "generated.bin"
        with self.assertRaisesRegex(ValueError, "explicit_excluded"):
            module.audit_staged_paths(
                data, staged_paths=[excluded], blob_reader=reader, require_final=False
            )

    def test_final_stage_gate_requires_explicit_selected_set(self):
        module = self._load_allowlist_module()
        readme = (ROOT / "README.md").read_bytes()
        digest = hashlib.sha256(readme).hexdigest()
        selected = [
            "README.md",
            *sorted(module.ALLOWLIST_ARTIFACT_PATHS),
        ]
        data = {
            "status": "FINAL_FROZEN",
            "candidate_documents": [{"path": "README.md", "sha256": digest}],
            "publication_selected_paths": selected,
        }
        with self.assertRaisesRegex(ValueError, "exact final publication stage mismatch"):
            module.audit_staged_paths(
                data,
                staged_paths=["README.md"],
                blob_reader=lambda path: readme,
                require_final=True,
                exact_selected=True,
            )

    def test_selected_path_cannot_bypass_reviewed_records(self):
        module = self._load_allowlist_module()
        data = {
            "status": "FINAL_FROZEN",
            "candidate_documents": [{"path": "README.md"}],
            "publication_selected_paths": ["docs/not-reviewed.md"],
        }
        with self.assertRaisesRegex(ValueError, "no reviewed allowlist record"):
            module.audit_staged_paths(
                data, staged_paths=[], blob_reader=lambda path: b"", require_final=True
            )

    def test_self_allowlist_blob_is_checked_as_content_not_only_sha_exempt(self):
        module = self._load_allowlist_module()
        path = ROOT / "docs/audits/20260905-round2/print-first-publication-allowlist-20260906.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        result = module.audit_staged_paths(
            data,
            staged_paths=[path.relative_to(ROOT).as_posix()],
            blob_reader=lambda _: path.read_bytes(),
            require_final=False,
        )
        self.assertEqual(result["self_structure_checked_paths"], [path.relative_to(ROOT).as_posix()])
        with self.assertRaisesRegex(ValueError, "differs from worktree input"):
            module.audit_staged_paths(
                data,
                staged_paths=[path.relative_to(ROOT).as_posix()],
                blob_reader=lambda _: b"{}",
                require_final=False,
            )

    def test_worktree_coverage_classifies_public_excluded_and_private_once(self):
        module = self._load_allowlist_module()
        data = {"candidate_documents": [{"path": "README.md"}]}
        private = "outputs" + "/" + "private" + "/raw.json"
        report = module.audit_worktree_coverage(
            data, paths=["README.md", "outputs/generated.json", private]
        )
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["classification_counts"], {
            "public": 1,
            "explicit_excluded": 1,
            "private": 1,
            "unregistered": 0,
        })

    def test_current_worktree_has_no_unregistered_paths_for_candidate_build(self):
        module = self._load_allowlist_module()
        data = module.build()
        self.assertEqual(data["content_scan"]["status"], "PASS_NO_VALUE_LEAK_FINDINGS")
        self.assertEqual(data["worktree_coverage"]["classification_counts"]["unregistered"], 0)

    def test_source_records_reject_duplicate_paths_and_print_first_gait_is_unique(self):
        module = self._load_allowlist_module()
        records, _ = module._source_records()
        gait = "firmware/src/print_first_gait.h"
        self.assertEqual(sum(row["path"] == gait for row in records), 1)
        original = module.SOURCE_GROUPS
        module.SOURCE_GROUPS = {
            **original,
            "duplicate_fixture": (gait,),
        }
        try:
            with self.assertRaisesRegex(ValueError, "duplicate source record path"):
                module._source_records()
        finally:
            module.SOURCE_GROUPS = original

    def test_candidate_records_reject_arbitrary_document_added_after_generation(self):
        module = self._load_allowlist_module()
        path = ROOT / "docs/audits/20260905-round2/print-first-publication-allowlist-20260906.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        mutated = copy.deepcopy(data)
        mutated["candidate_documents"].append({
            **module._record("README.md", role="candidate_document"),
            "role": "arbitrary_candidate_fixture",
        })
        with self.assertRaisesRegex(ValueError, "candidate_documents"):
            module._validate_current_allowlist_records(mutated, require_final=False)

    def test_absolute_path_gate_rejects_real_roots_and_accepts_url_or_repo_path(self):
        module = self._load_allowlist_module()
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
            with self.assertRaises(ValueError, msg=value):
                module._assert_self_blob_safe(value, None, source="negative-path")
        for value in (
            "https://example.com/" + "etc/passwd",
            "https://example.com/Users/秘密.txt",
            "docs/" + "etc/passwd",
            "docs/Users/秘密.txt",
            "outputs/" + "private/" + "raw.json",
        ):
            module._assert_self_blob_safe(value, None, source="positive-path")

    def test_self_json_rejects_unknown_raw_and_order_fields(self):
        module = self._load_allowlist_module()
        path = ROOT / "docs/audits/20260905-round2/print-first-publication-allowlist-20260906.json"
        data = json.loads(path.read_text(encoding="utf-8"))

        unknown = json.loads(json.dumps(data))
        unknown["unexpected_secret_field"] = "should fail closed"
        with self.assertRaisesRegex(ValueError, "(?:unknown field|dangerous field)"):
            module._validate_allowlist_json_structure(unknown, require_final=False)

        raw = json.loads(json.dumps(data))
        raw_key = "_".join(("raw", "issue", "body"))
        raw[raw_key] = "raw body must never enter the allowlist"
        with self.assertRaisesRegex(ValueError, "(?:unknown field|dangerous field)"):
            module._validate_allowlist_json_structure(raw, require_final=False)

        order = json.loads(json.dumps(data))
        lots_key = "_".join(("purchase", "lots"))
        order_key = "_".join(("order", "number"))
        order_value = "-".join(("123", "1234567", "1234567"))
        order[lots_key] = [{order_key: order_value}]
        with self.assertRaises(ValueError):
            module._assert_self_blob_safe(
                json.dumps(order, ensure_ascii=False), order, source="negative-json"
            )

    def test_self_markdown_rejects_sensitive_append_and_requires_byte_render(self):
        module = self._load_allowlist_module()
        path = ROOT / "docs/audits/20260905-round2/print-first-publication-allowlist-20260906.md"
        text = path.read_text(encoding="utf-8")
        data = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
        with self.assertRaises(ValueError):
            module._assert_self_blob_safe(
                text + "\nToken: " + "gh" + "p_1234567890abcdef\n",
                None,
                source="negative-markdown",
            )
        arbitrary_append = text + "\n任意の追記\n"
        self.assertNotEqual(arbitrary_append, module.render_markdown(data))

    def test_publication_workflow_requires_a_reference_and_rejects_b_self_reference(self):
        module = self._load_allowlist_module()
        with self.assertRaisesRegex(ValueError, "evidence commit A"):
            module._publication_workflow_contract(module.FINAL_ALLOWLIST_STATUS)
        workflow = module._publication_workflow_contract(
            module.FINAL_ALLOWLIST_STATUS,
            evidence_commit_sha="a" * 40,
            draft_pr_url="https://github.com/hapx2yuki/Tachikoma/pull/123",
        )
        data = json.loads(
            (ROOT / "docs/audits/20260905-round2/print-first-publication-allowlist-20260906.json").read_text(
                encoding="utf-8"
            )
        )
        data["status"] = module.FINAL_ALLOWLIST_STATUS
        data["publication_workflow"] = workflow
        data["publication_selection_status"] = "FINAL_EXACT_REQUIRED"
        data["publication_workflow"]["plan_allowlist_commit"]["sha"] = "b" * 40
        with self.assertRaisesRegex(ValueError, "self-reference"):
            module._validate_allowlist_json_structure(data, require_final=False)


if __name__ == "__main__":
    unittest.main()
