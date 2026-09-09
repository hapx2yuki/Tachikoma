"""Issue追記markerとdry-runの冪等契約試験。"""

import json
import copy
import hashlib
import fcntl
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/issues"))
import update_github_issues as updater  # noqa: E402


class IssueAppendUpdateTests(unittest.TestCase):
    def test_marker_is_deterministic_and_binds_three_inputs(self):
        first = updater.marker_for("run-1", "bundle-sha", "abcdef0")
        self.assertEqual(first, updater.marker_for("run-1", "bundle-sha", "abcdef0"))
        self.assertNotEqual(first, updater.marker_for("run-2", "bundle-sha", "abcdef0"))
        self.assertIn("id=", first)
        self.assertIn("evidence=", first)
        self.assertIn("commit=", first)

    def test_placeholder_marker_input_is_rejected(self):
        with self.assertRaises(ValueError):
            updater.marker_for("<RUN_ID>", "bundle", "commit")

    def test_dry_run_has_107_rows_and_performs_no_write(self):
        rows = updater.load_rows(ROOT / "docs/audits/20260905-round2/github-issues-refresh-20260906.json")
        snapshot = updater.load_safe_snapshot(ROOT / "docs/audits/20260905-round2/github-safe-snapshot-20260906.json")
        result = updater.dry_run(rows, snapshot, updater.marker_for("run", "bundle", "commit"))
        self.assertEqual(result["issue_count"], 107)
        self.assertEqual(result["baseline_comments"], 16)
        self.assertFalse(result["github_write_performed"])

    def test_after_apply_baseline_allows_one_appended_marker(self):
        expected = {number: [] for number in updater.ISSUE_RANGE}
        expected[81] = [{"id": 1, "created_at": "t", "updated_at": "t", "body_sha256": "h"}]
        actual = {number: list(rows) for number, rows in expected.items()}
        actual[81].append({"id": 2, "created_at": "t2", "updated_at": "t2", "body_sha256": "m"})
        self.assertEqual(updater.verify_comment_baseline(expected, actual, allow_appended=True), 1)

    def test_resume_allows_only_this_marker_and_rejects_unknown_comment(self):
        marker = updater.marker_for("run", "bundle", "commit")
        expected = {number: [] for number in updater.ISSUE_RANGE}
        expected[81] = [{"id": 1, "created_at": "t", "updated_at": "t", "body_sha256": "h" * 64}]
        marker_comment = {
            "id": 2,
            "created_at": "t2",
            "updated_at": "t2",
            "body": marker + "\\n\\nissue-specific append",
            "body_sha256": "m" * 64,
        }
        actual = {number: [] for number in updater.ISSUE_RANGE}
        actual[81] = [*expected[81], marker_comment]
        self.assertEqual(updater.verify_append_baseline(expected, actual, marker)[81], 1)

        unknown = {number: list(rows) for number, rows in actual.items()}
        unknown[81] = [*unknown[81], {
            "id": 3,
            "created_at": "t3",
            "updated_at": "t3",
            "body": "unrelated concurrent comment",
            "body_sha256": "u" * 64,
        }]
        with self.assertRaises(RuntimeError):
            updater.verify_append_baseline(expected, unknown, marker)

    def test_existing_marker_requires_issue_specific_body_and_sha_exact(self):
        marker = updater.marker_for("run", "bundle", "commit")
        expected = {number: [] for number in updater.ISSUE_RANGE}
        expected_body = marker + "\n\nIssue #81 final text"
        actual = {number: [] for number in updater.ISSUE_RANGE}
        actual[81] = [{
            "id": 2,
            "created_at": "t",
            "updated_at": "t",
            "body": marker + "\n\nwrong Issue text",
            "body_sha256": hashlib.sha256((marker + "\n\nwrong Issue text").encode()).hexdigest(),
        }]
        with self.assertRaisesRegex(RuntimeError, "Issue-specific"):
            updater.verify_append_baseline(
                expected, actual, marker,
                expected_marker_bodies={81: expected_body},
            )
        actual[81][0]["body"] = expected_body
        actual[81][0]["body_sha256"] = "0" * 64
        with self.assertRaisesRegex(RuntimeError, "body SHA"):
            updater.verify_append_baseline(
                expected, actual, marker,
                expected_marker_bodies={81: expected_body},
            )

    def test_existing_marker_multiple_comments_fails_closed_with_exact_body_mode(self):
        marker = updater.marker_for("run", "bundle", "commit")
        expected = {number: [] for number in updater.ISSUE_RANGE}
        body = marker + "\n\nIssue #81 final text"
        row = {
            "created_at": "t",
            "updated_at": "t",
            "body": body,
            "body_sha256": hashlib.sha256(body.encode()).hexdigest(),
        }
        actual = {number: [] for number in updater.ISSUE_RANGE}
        actual[81] = [{"id": 2, **row}, {"id": 3, **row}]
        with self.assertRaisesRegex(RuntimeError, "multiple comments"):
            updater.verify_append_baseline(
                expected, actual, marker,
                expected_marker_bodies={81: body},
            )

    def test_snapshot_updated_at_is_strict_before_marker_and_relaxed_on_resume(self):
        expected = {
            "number": 99,
            "state": "OPEN",
            "title": "title",
            "url": "https://github.com/hapx2yuki/Tachikoma/issues/99",
            "labels": [],
            "updatedAt": "before",
            "body_sha256": "b" * 64,
        }
        changed = {**expected, "updatedAt": "after-self-comment"}
        with self.assertRaisesRegex(RuntimeError, "updatedAt"):
            updater.verify_issue_metadata_matches_snapshot(expected, changed)
        updater.verify_issue_metadata_matches_snapshot(
            expected, changed, allow_updated_at_drift=True
        )
        with self.assertRaisesRegex(RuntimeError, "updatedAt"):
            updater.verify_issue_metadata_unchanged(expected, changed)
        updater.verify_issue_metadata_unchanged(
            expected, changed, allow_updated_at_drift=True
        )

    def test_partial_failure_resumes_after_existing_markers(self):
        marker = updater.marker_for("resume-run", "resume-evidence", "a" * 40)
        baseline = {number: [] for number in updater.ISSUE_RANGE}
        baseline[81] = [{
            "id": 81,
            "created_at": "2026-09-05T00:00:00Z",
            "updated_at": "2026-09-05T00:00:00Z",
            "body_sha256": "c" * 64,
        }]
        issues = []
        state = {}
        for number in updater.ISSUE_RANGE:
            metadata = {
                "number": number,
                "state": "OPEN",
                "title": f"Issue {number}",
                "url": f"https://github.com/hapx2yuki/Tachikoma/issues/{number}",
                "labels": [],
                "updatedAt": "snapshot-time",
                "body_sha256": "d" * 64,
            }
            issues.append(dict(metadata))
            state[number] = {"metadata": metadata, "comments": copy.deepcopy(baseline[number])}
        snapshot = {"issues": issues, "comments": {str(n): rows for n, rows in baseline.items()}}
        rows = [{"number": n, "append_only_update_candidate": "final issue text"}
                for n in updater.ISSUE_RANGE]
        calls = []
        fail_once = {"enabled": True}

        def fake_metadata(number, repo=updater.REPO):
            return dict(state[number]["metadata"])

        def fake_comments(number, repo=updater.REPO):
            return copy.deepcopy(state[number]["comments"])

        def fake_runner(*args):
            number = int(args[2])
            payload = args[-1]
            calls.append(number)
            if fail_once["enabled"] and number == 5:
                fail_once["enabled"] = False
                raise RuntimeError("injected partial failure")
            comment = {
                "id": 1000 + number,
                "created_at": f"2026-09-06T00:00:{number:02d}Z",
                "updated_at": f"2026-09-06T00:00:{number:02d}Z",
                "body": payload,
                "body_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
            }
            state[number]["comments"].append(comment)
            state[number]["metadata"]["updatedAt"] = f"self-comment-{number}"

        with mock.patch.object(updater, "_issue_metadata", side_effect=fake_metadata), \
                mock.patch.object(updater, "_issue_comments_with_body", side_effect=fake_comments):
            with self.assertRaisesRegex(RuntimeError, "injected partial failure"):
                updater.apply_updates(rows, snapshot, marker, gh_runner=fake_runner, allow_test_inputs=True)
            resumed = updater.apply_updates(rows, snapshot, marker, gh_runner=fake_runner, allow_test_inputs=True)

        self.assertEqual(resumed["comments_added"], 105)
        self.assertEqual(resumed["already_marked"], 2)
        self.assertEqual(calls[:3], [3, 4, 5])
        self.assertEqual(len(calls), 108)  # 107 successful attempts plus the injected failure
        for number in updater.ISSUE_RANGE:
            marker_rows = [row for row in state[number]["comments"]
                           if marker in row.get("body", "")]
            self.assertEqual(len(marker_rows), 1, number)

    def test_local_publisher_lock_and_durable_state_fail_closed(self):
        marker = updater.marker_for("lock-run", "lock-evidence", "a" * 40)
        with tempfile.TemporaryDirectory() as directory:
            private = Path(directory)
            lock_path = private / "publisher.lock"
            state_path = private / "publisher-state.json"
            with mock.patch.object(updater, "PUBLISHER_PRIVATE_DIR", private), \
                    mock.patch.object(updater, "PUBLISHER_LOCK_PATH", lock_path), \
                    mock.patch.object(updater, "PUBLISHER_STATE_PATH", state_path):
                with updater.publisher_run_lock("lock-run", marker) as state:
                    self.assertEqual(state["status"], "RUNNING")
                    running = json.loads(state_path.read_text(encoding="utf-8"))
                    self.assertEqual(running["status"], "RUNNING")
                    self.assertNotIn(marker, state_path.read_text(encoding="utf-8"))
                    with self.assertRaisesRegex(RuntimeError, "another local Issue publisher"):
                        with updater.publisher_run_lock("other-run", marker + "-other"):
                            pass
                completed = json.loads(state_path.read_text(encoding="utf-8"))
                self.assertEqual(completed["status"], "PASS")
                self.assertEqual(set(completed), set(updater.PUBLISHER_STATE_KEYS))

                held = os.open(lock_path, os.O_RDWR)
                try:
                    fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    with self.assertRaisesRegex(RuntimeError, "another local Issue publisher"):
                        with updater.publisher_run_lock("blocked-run", "blocked-marker"):
                            pass
                finally:
                    fcntl.flock(held, fcntl.LOCK_UN)
                    os.close(held)

                with self.assertRaisesRegex(RuntimeError, "injected"):
                    with updater.publisher_run_lock("failed-run", "failed-marker"):
                        raise RuntimeError("injected")
                failed = json.loads(state_path.read_text(encoding="utf-8"))
                self.assertEqual(failed["status"], "FAILED")
                self.assertEqual(failed["error_type"], "RuntimeError")

    def test_cli_help_states_single_publisher_concurrency_boundary(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools/issues/update_github_issues.py"), "--help"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("単一publisherのみ", result.stdout)
        self.assertIn("別host競合", result.stdout)
        self.assertIn("重複が外部に残り得る", result.stdout)

    def test_normal_apply_requires_final_plan_and_verified_publication_context(self):
        rows = updater.load_rows(ROOT / "docs/audits/20260905-round2/github-issues-refresh-20260906.json")
        snapshot = updater.load_safe_snapshot(ROOT / "docs/audits/20260905-round2/github-safe-snapshot-20260906.json")
        marker = updater.marker_for("run", "bundle", "a" * 40)
        with self.assertRaisesRegex(RuntimeError, "FINAL_FROZEN"):
            updater.apply_updates(rows, snapshot, marker, gh_runner=lambda *_: None)

    def test_publication_context_checks_A_pr_B_lineage_and_exact_plan_readback(self):
        plan_path = ROOT / "docs/audits/20260905-round2/github-issues-refresh-20260906.json"
        plan_sha = hashlib.sha256(plan_path.read_bytes()).hexdigest()
        branch = "codex/print-first-20260905"
        commit_a = "a" * 40
        commit_b = "b" * 40
        pr_url = "https://github.com/hapx2yuki/Tachikoma/pull/123"
        publication_bundle = {
            "allowlist_json": {
                "path": updater.ALLOWLIST_JSON_PATH,
                "sha256": "c" * 64,
            },
            "allowlist_md": {
                "path": updater.ALLOWLIST_MD_PATH,
                "sha256": "d" * 64,
            },
            "publication_selected_paths": ["README.md", updater.ALLOWLIST_JSON_PATH, updater.ALLOWLIST_MD_PATH],
        }
        plan = {
            "finalization": {"status": "FINAL_FROZEN", "commit_sha": commit_a, "pull_request_url": pr_url,
                              "publication_bundle": publication_bundle},
            "final_update_template": {"status": "FINAL_FROZEN", "commit_sha": commit_a, "pull_request_url": pr_url,
                                       "publication_bundle": publication_bundle},
            "publication_bundle": publication_bundle,
            "publication_gate": {
                "publication_ready": True,
                "external_issue_update_performed": False,
                "external_project_update_performed": False,
            },
        }

        def git_reader(args):
            if args == ["rev-parse", "HEAD"]:
                return subprocess.CompletedProcess(args, 0, stdout=commit_b + "\n", stderr="")
            if args == ["branch", "--show-current"]:
                return subprocess.CompletedProcess(args, 0, stdout=branch + "\n", stderr="")
            if args == ["ls-remote", "origin", f"refs/heads/{branch}"]:
                return subprocess.CompletedProcess(args, 0, stdout=f"{commit_b}\trefs/heads/{branch}\n", stderr="")
            if args == ["cat-file", "-e", f"{commit_b}^{{commit}}"]:
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
            if args == ["merge-base", "--is-ancestor", commit_a, commit_b]:
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
            if args == ["merge-base", "--is-ancestor", commit_b, "HEAD"]:
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
            if args == ["show", f"{commit_b}:{plan_path.relative_to(ROOT).as_posix()}"]:
                return subprocess.CompletedProcess(args, 0, stdout=plan_path.read_text(encoding="utf-8"), stderr="")
            raise AssertionError(args)

        pr_reader = lambda url, repo: {
            "url": pr_url,
            "isDraft": True,
            "headRefName": branch,
            "headRefOid": commit_b,
            "baseRefName": "main",
            "headRepository": {"fullName": repo},
            "baseRepository": {"fullName": repo},
        }
        with mock.patch.object(
            updater,
            "_validate_publication_bundle_at_commit",
            return_value={"status": "PASS", "proof": "commit_b_tree_exact_selected_set",
                          "selected_paths": publication_bundle["publication_selected_paths"]},
        ):
            result = updater.verify_publication_context(
                plan,
                plan_path=plan_path.relative_to(ROOT).as_posix(),
                plan_sha256=plan_sha,
                plan_commit_b=commit_b,
                branch=branch,
                git_reader=git_reader,
                pr_reader=pr_reader,
                default_branch_reader=lambda _repo: "main",
            )
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["evidence_commit_A"], commit_a)
        self.assertEqual(result["plan_commit_B"], commit_b)

        bad_pr = dict(result)
        with self.assertRaisesRegex(ValueError, "draft PR"):
            with mock.patch.object(
                updater,
                "_validate_publication_bundle_at_commit",
                return_value={"status": "PASS", "proof": "commit_b_tree_exact_selected_set",
                              "selected_paths": publication_bundle["publication_selected_paths"]},
            ):
                updater.verify_publication_context(
                    plan,
                    plan_path=plan_path.relative_to(ROOT).as_posix(),
                    plan_sha256=plan_sha,
                    plan_commit_b=commit_b,
                    branch=branch,
                    git_reader=git_reader,
                    pr_reader=lambda url, repo: {**pr_reader(url, repo), "isDraft": False},
                    default_branch_reader=lambda _repo: "main",
                )

        with self.assertRaisesRegex(ValueError, "base branch"):
            with mock.patch.object(
                updater,
                "_validate_publication_bundle_at_commit",
                return_value={"status": "PASS", "proof": "commit_b_tree_exact_selected_set",
                              "selected_paths": publication_bundle["publication_selected_paths"]},
            ):
                updater.verify_publication_context(
                    plan,
                    plan_path=plan_path.relative_to(ROOT).as_posix(),
                    plan_sha256=plan_sha,
                    plan_commit_b=commit_b,
                    branch=branch,
                    git_reader=git_reader,
                    pr_reader=lambda url, repo: {**pr_reader(url, repo), "baseRefName": "master"},
                    default_branch_reader=lambda _repo: "main",
                )

    def test_finalize_plan_replaces_all_rows_with_visible_final_evidence(self):
        snapshot_path = ROOT / "docs/audits/20260905-round2/github-safe-snapshot-20260906.json"
        digest = hashlib.sha256(snapshot_path.read_bytes()).hexdigest()
        plan = {
            "all_issue_append_only_update_plan": [
                {
                    "number": n,
                    "candidate_evidence_status": "FINAL_FROZEN",
                    "acceptance_condition": f"condition {n}",
                    "acceptance_next_step": f"next {n}",
                    "issue_specific_focus": f"focus {n}",
                    "append_only_update_candidate": "old text",
                }
                for n in updater.ISSUE_RANGE
            ]
        }
        with self.assertRaisesRegex(ValueError, "prescribed final root"):
            updater.finalize_plan_payload(
                plan,
                commit_sha="a" * 40,
                pull_request_url="https://github.com/hapx2yuki/Tachikoma/pull/123",
                freeze2_manifest="docs/audits/20260905-round2/github-safe-snapshot-20260906.json",
                freeze2_sha256=digest,
                evidence_index="docs/audits/20260905-round2/github-safe-snapshot-20260906.json",
                evidence_index_sha256=digest,
            )

    def test_finalize_candidate_report_replaces_pending_fields(self):
        """実際の候補計画をfreeze2後の確定入力として消費できる。"""
        plan_path = ROOT / "docs/audits/20260905-round2/github-issues-refresh-20260906.json"
        snapshot_path = ROOT / "docs/audits/20260905-round2/github-safe-snapshot-20260906.json"
        digest = hashlib.sha256(snapshot_path.read_bytes()).hexdigest()
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        with self.assertRaisesRegex(ValueError, "prescribed final root"):
            updater.finalize_plan_payload(
                plan,
                commit_sha="b" * 40,
                pull_request_url="https://github.com/hapx2yuki/Tachikoma/pull/124",
                freeze2_manifest="docs/audits/20260905-round2/github-safe-snapshot-20260906.json",
                freeze2_sha256=digest,
                evidence_index="docs/audits/20260905-round2/github-safe-snapshot-20260906.json",
                evidence_index_sha256=digest,
            )

    def test_finalize_plan_rejects_unresolved_candidate_state(self):
        plan = {
            "candidate_evidence_bundle": {"status": "CANDIDATE_EVIDENCE_PENDING_FINAL_FREEZE2"},
            "all_issue_append_only_update_plan": [],
        }
        with self.assertRaises(ValueError):
            updater.finalize_plan_payload(
                plan,
                commit_sha="a" * 40,
                pull_request_url="https://github.com/hapx2yuki/Tachikoma/pull/123",
                freeze2_manifest="README.md",
                freeze2_sha256=hashlib.sha256((ROOT / "README.md").read_bytes()).hexdigest(),
                evidence_index="README.md",
                evidence_index_sha256=hashlib.sha256((ROOT / "README.md").read_bytes()).hexdigest(),
            )

    def test_final_freeze_reference_rejects_arbitrary_readme_and_stale_manifest_blob(self):
        readme_sha = hashlib.sha256((ROOT / "README.md").read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, "prescribed final root"):
            updater._validate_frozen_manifest_reference("README.md", readme_sha)
        stale = ROOT / "outputs/print-first-20260905/final-freeze2-rerun/freeze-manifest.json"
        stale_sha = hashlib.sha256(stale.read_bytes()).hexdigest()
        # The checked-in freeze manifest references a changed XIAO plan.  A
        # real SHA check must stop it instead of trusting only the manifest's
        # own status field.
        with self.assertRaisesRegex(ValueError, "blob SHA mismatch"):
            updater._validate_frozen_manifest_reference(
                stale.relative_to(ROOT).as_posix(), stale_sha
            )

    def test_canonical_release_layers_reject_pending_manifest_and_forged_numbers(self):
        with self.assertRaisesRegex(ValueError, "FINAL_FROZEN"):
            updater._canonical_release_layers()
        canonical = {"A": 31, "B": 25, "C": 4, "maximum": 60}
        forged = {
            "candidate_evidence_bundle": {
                "print_release_layers": {
                    "A_minimum_fit_prototype": {"quantity": 999},
                    "B_remaining_after_prototype": {"quantity": 25},
                    "C_conditional_new_shin_shell": {"quantity": 4, "maximum_machine_total": 60},
                }
            }
        }
        with self.assertRaisesRegex(ValueError, "disagrees with canonical"):
            updater._validate_candidate_release_layers(forged, canonical)

    def test_final_plan_requires_allowlist_json_md_and_selected_set(self):
        plan = {
            "finalization": {
                "status": "FINAL_FROZEN",
                "commit_sha": "a" * 40,
                "pull_request_url": "https://github.com/hapx2yuki/Tachikoma/pull/1",
            },
            "final_update_template": {
                "status": "FINAL_FROZEN",
                "commit_sha": "a" * 40,
                "pull_request_url": "https://github.com/hapx2yuki/Tachikoma/pull/1",
            },
            "publication_gate": {
                "publication_ready": True,
                "external_issue_update_performed": False,
                "external_project_update_performed": False,
            },
        }
        with self.assertRaisesRegex(ValueError, "publication_bundle is required"):
            updater._final_plan_publication_refs(plan)

    def test_commit_b_allowlist_selection_and_non_self_sha_are_bound(self):
        # Build a compact final allowlist fixture from the current generated
        # candidate, then exercise the B-tree content contract without any
        # GitHub or index mutation.
        allowlist_path = ROOT / "docs/audits/20260905-round2/print-first-publication-allowlist-20260906.json"
        allowlist_md_path = allowlist_path.with_suffix(".md")
        sys.path.insert(0, str(ROOT / "tools"))
        import make_print_first_publication_allowlist as allowlist
        payload = json.loads(allowlist_path.read_text(encoding="utf-8"))
        payload["status"] = "FINAL_FROZEN"
        payload["publication_selection_status"] = "FINAL_EXACT_REQUIRED"
        payload["publication_selected_paths"] = sorted([
            "README.md",
            *{
                "docs/audits/20260905-round2/print-first-publication-allowlist-20260906.json",
                "docs/audits/20260905-round2/print-first-publication-allowlist-20260906.md",
            },
        ])
        freeze_path = ROOT / "outputs/print-first-20260905/final-freeze2-rerun/freeze-manifest.json"
        payload["final_freeze2_selected_files"] = [{
            **allowlist._record(
                freeze_path.relative_to(ROOT).as_posix(),
                role="final_freeze2_manifest",
            ),
            "status": "FINAL_FROZEN",
            "selected_explicitly": True,
        }]
        payload["staging"]["read_only_stage_gate"]["publication_selected_paths"] = payload["publication_selected_paths"]
        workflow = payload["publication_workflow"]
        workflow["status"] = "FINAL_FROZEN"
        workflow["evidence_commit"]["sha"] = "a" * 40
        workflow["draft_pr"]["url"] = "https://github.com/hapx2yuki/Tachikoma/pull/1"
        workflow["draft_pr"]["created_from_commit_sha"] = "a" * 40
        json_blob = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode()
        md_blob = allowlist.render_markdown(payload).encode()
        records = updater._validate_b_allowlist_payload(
            json_blob,
            md_blob,
            selected=set(payload["publication_selected_paths"]),
        )
        self.assertEqual(records["README.md"], hashlib.sha256((ROOT / "README.md").read_bytes()).hexdigest())
        blobs = {
            updater.ALLOWLIST_JSON_PATH: json_blob,
            updater.ALLOWLIST_MD_PATH: md_blob,
            "README.md": (ROOT / "README.md").read_bytes(),
        }
        selected = set(payload["publication_selected_paths"])
        fake_git = lambda args: (
            "\n".join(sorted(selected)) + "\n"
            if args[:3] == ["ls-tree", "-r", "--name-only"]
            else ""
        )
        bundle_plan = {
            "publication_bundle": {
                "allowlist_json": {
                    "path": updater.ALLOWLIST_JSON_PATH,
                    "sha256": hashlib.sha256(json_blob).hexdigest(),
                },
                "allowlist_md": {
                    "path": updater.ALLOWLIST_MD_PATH,
                    "sha256": hashlib.sha256(md_blob).hexdigest(),
                },
                "publication_selected_paths": sorted(selected),
            }
        }
        audit = updater._validate_publication_bundle_at_commit(
            bundle_plan,
            "b" * 40,
            git_reader=fake_git,
            blob_reader=lambda _commit, path: blobs.get(path),
        )
        self.assertEqual(audit["proof"], "commit_b_tree_exact_selected_set")
        self.assertIn("README.md", audit["checked_non_self_blob_shas"])
        with self.assertRaisesRegex(ValueError, "selected blob SHA"):
            updater._validate_publication_bundle_at_commit(
                bundle_plan,
                "b" * 40,
                git_reader=fake_git,
                blob_reader=lambda _commit, path: b"tampered" if path == "README.md" else blobs.get(path),
            )
        with self.assertRaisesRegex(ValueError, "differs from final plan"):
            updater._validate_b_allowlist_payload(
                json_blob,
                md_blob,
                selected=set(payload["publication_selected_paths"]) | {"docs/print-first.md"},
            )


if __name__ == "__main__":
    unittest.main()
