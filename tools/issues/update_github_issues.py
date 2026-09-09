#!/usr/bin/env python3
"""Issue #3--#109へ追記案をappend-onlyで反映する。

既定は計画表示だけで、GitHubへの書込みは ``--apply`` の明示指定時に
``gh issue comment`` だけを使う。Issue本文・題名・状態・ラベルは
書き換えず、Project操作もこのツールでは行わない。反映前に安全
スナップショットの16コメント（ID/時刻/本文SHA）を照合し、反映後に
全107件のmarkerが一度だけ存在することを確認する。
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import copy
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
REPO = "hapx2yuki/Tachikoma"
DEFAULT_PLAN = ROOT / "docs/audits/20260905-round2/github-issues-refresh-20260906.json"
DEFAULT_SNAPSHOT = ROOT / "docs/audits/20260905-round2/github-safe-snapshot-20260906.json"
ISSUE_RANGE = range(3, 110)
MARKER_PREFIX = "<!-- tachikoma-append:v1"
PUBLISHER_PRIVATE_DIR = ROOT / "outputs/private"
PUBLISHER_LOCK_PATH = PUBLISHER_PRIVATE_DIR / "github-issue-publisher.lock"
PUBLISHER_STATE_PATH = PUBLISHER_PRIVATE_DIR / "github-issue-publisher-state.json"
PUBLISHER_STATE_KEYS = frozenset({
    "schema_version", "status", "run_token_sha256", "marker_sha256",
    "started_at", "updated_at", "completed_at", "error_type",
})
PUBLISHER_CONCURRENCY_BOUNDARY = (
    "単一publisherのみ。同一hostはfcntl+durable stateで直列化する。"
    "別host競合は投稿後のmarker/body readbackで検出停止するが、"
    "競合投稿が先行した場合は既に重複が外部へ残り得る。"
    "分散同時実行の原子的保証は提供しない。"
)

# These are the immutable publication inputs.  The finalizer deliberately
# does not infer a freeze/evidence file from an arbitrary path supplied by a
# caller: the path class, JSON contract, role and every referenced blob are
# checked before any final Issue text is made.
CANONICAL_PRINT_MANIFEST = ROOT / "docs/print-first-manifest.json"
CANONICAL_CONFIG = ROOT / "hardware/src/config.py"
CANONICAL_XIAO_PLAN = ROOT / "docs/audits/20260905-round2/xiao-retention-plan.json"
ALLOWLIST_JSON_PATH = "docs/audits/20260905-round2/print-first-publication-allowlist-20260906.json"
ALLOWLIST_MD_PATH = "docs/audits/20260905-round2/print-first-publication-allowlist-20260906.md"
FINAL_FREEZE_ROOT = "outputs/print-first-20260905/"
FINAL_EVIDENCE_ROOT = "docs/audits/20260905-round2/"
FINAL_FREEZE_FILE_KEYS = frozenset({
    "schema_version", "status", "geometry_freeze_time", "final_urdf",
    "final_urdf_mesh_count", "final_mesh_bundle", "files", "file_count",
    "interpretation",
})
FINAL_FREEZE_BUNDLE_KEYS = frozenset({
    "directory", "referenced_count", "files_count", "orphan_count",
    "referenced_outside_bundle_count", "orphan_policy",
})
FINAL_FREEZE_ROLES = frozenset({
    "final_urdf", "final_urdf_mesh", "final_parts_manifest",
    "firmware_or_generator_input", "runtime_input_fingerprint", "assembly_input",
})
FINAL_EVIDENCE_INDEX_KEYS = frozenset({
    "schema_version", "status", "entries", "entry_count",
})
FINAL_EVIDENCE_ENTRY_KEYS = frozenset({"path", "sha256", "role"})
PUBLICATION_BUNDLE_KEYS = frozenset({
    "allowlist_json", "allowlist_md", "publication_selected_paths",
})
PUBLICATION_BLOB_RECORD_KEYS = (
    "candidate_documents", "candidate_source_files", "candidate_evidence_files",
    "final_freeze2_selected_files", "print_first_stl_candidate_records",
    "holder_candidate_stl_records",
)

sys.path.insert(0, str(HERE))
from publication_gate import assert_current_issue_publication, assert_no_placeholders  # noqa: E402
from fetch_public_snapshot import (  # noqa: E402
    _decode_paginated_json,
    fetch_comments,
    fetch_issue_metadata,
    run_gh,
    validate_safe_snapshot,
)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _token(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} is required")
    value = value.strip()
    assert_no_placeholders(value, source=label)
    return value


def marker_components(run_id: str, evidence_bundle: str, commit: str) -> dict[str, str]:
    run_id = _token(run_id, "run_id")
    evidence_bundle = _token(evidence_bundle, "evidence_bundle")
    commit = _token(commit, "commit")
    if "\x00" in run_id + evidence_bundle + commit:
        raise ValueError("marker inputs may not contain NUL")
    run_sha = sha256_bytes(run_id.encode("utf-8"))
    evidence_sha = sha256_bytes(evidence_bundle.encode("utf-8"))
    commit_sha = sha256_bytes(commit.encode("utf-8"))
    bundle_sha = sha256_bytes((run_id + "\0" + evidence_bundle + "\0" + commit).encode("utf-8"))
    return {
        "run_id_sha256": run_sha,
        "evidence_bundle_sha256": evidence_sha,
        "commit_sha256": commit_sha,
        "marker_id": bundle_sha,
    }


def marker_for(run_id: str, evidence_bundle: str, commit: str) -> str:
    parts = marker_components(run_id, evidence_bundle, commit)
    return (
        f"{MARKER_PREFIX} id={parts['marker_id']} run={parts['run_id_sha256']} "
        f"evidence={parts['evidence_bundle_sha256']} commit={parts['commit_sha256']} -->"
    )


def marker_count(text: str, marker: str) -> int:
    return text.count(marker)


def load_plan_payload(path: Path = DEFAULT_PLAN) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("append plan must be a JSON object")
    return payload


def load_rows(path: Path = DEFAULT_PLAN) -> list[dict[str, Any]]:
    payload = load_plan_payload(path)
    rows = payload.get("all_issue_append_only_update_plan")
    if not isinstance(rows, list) or [int(row.get("number", -1)) for row in rows] != list(ISSUE_RANGE):
        raise ValueError("append plan must contain ordered #3--#109 rows")
    for row in rows:
        candidate = row.get("append_only_update_candidate")
        if not isinstance(candidate, str) or not candidate.strip():
            raise ValueError(f"Issue #{row.get('number')} has no append candidate")
    return rows


def load_safe_snapshot(path: Path = DEFAULT_SNAPSHOT) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    validate_safe_snapshot(payload, repo=REPO, project_number=2)
    return payload


def _walk_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _safe_comment_tuple(comment: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        comment.get("id"),
        comment.get("created_at") or comment.get("createdAt"),
        comment.get("updated_at") or comment.get("updatedAt"),
        comment.get("body_sha256"),
    )


def baseline_comments(snapshot: Mapping[str, Any]) -> dict[int, list[dict[str, Any]]]:
    comments = snapshot.get("comments") or {}
    result: dict[int, list[dict[str, Any]]] = {}
    for number in ISSUE_RANGE:
        rows = comments.get(str(number), [])
        if not isinstance(rows, list):
            raise ValueError(f"comments for #{number} must be an array")
        checked: list[dict[str, Any]] = []
        for row in rows:
            value = dict(row)
            if not isinstance(value.get("body_sha256"), str) or not re.fullmatch(
                r"[0-9a-f]{64}", value["body_sha256"]
            ):
                raise ValueError(f"comment body_sha256 is invalid for #{number}")
            checked.append(value)
        result[number] = checked
    return result


def verify_comment_baseline(
    expected: Mapping[int, list[Mapping[str, Any]]],
    actual: Mapping[int, list[Mapping[str, Any]]],
    *,
    allow_appended: bool = False,
) -> int:
    """既存コメントのID/時刻/本文SHAを完全一致させる。"""
    total = 0
    for number in ISSUE_RANGE:
        expected_rows = [_safe_comment_tuple(row) for row in expected.get(number, [])]
        actual_rows = [_safe_comment_tuple(row) for row in actual.get(number, [])]
        if allow_appended:
            actual_by_id = {row[0]: row for row in actual_rows}
            if any(row[0] not in actual_by_id or actual_by_id[row[0]] != row for row in expected_rows):
                raise RuntimeError(f"既存コメントが変化または不足: #{number}")
        elif expected_rows != actual_rows:
            raise RuntimeError(f"既存コメントが変化または不足: #{number}")
        total += len(expected_rows)
    return total


def _issue_metadata(number: int, repo: str = REPO) -> dict[str, Any]:
    """Issue本文を保存せず、変更検知用の一時メタデータだけ返す。"""
    value = json.loads(run_gh("api", f"repos/{repo}/issues/{number}"))
    return {
        "number": value.get("number"),
        "state": value.get("state"),
        "title": value.get("title"),
        "url": value.get("html_url") or value.get("url"),
        "labels": sorted(label.get("name") for label in value.get("labels", []) if label.get("name")),
        "updatedAt": value.get("updated_at"),
        "body_sha256": sha256_bytes((value.get("body") or "").encode("utf-8")),
    }


def _issue_comments_with_body(number: int, repo: str = REPO) -> list[dict[str, Any]]:
    raw = _decode_paginated_json(
        run_gh("api", f"repos/{repo}/issues/{number}/comments?per_page=100", "--paginate")
    )
    result = []
    for row in raw:
        body = row.get("body") if isinstance(row.get("body"), str) else ""
        result.append({
            "id": row.get("id"),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
            "body": body,
            "body_sha256": sha256_bytes(body.encode("utf-8")),
        })
    result.sort(key=lambda row: (row.get("created_at") or "", int(row.get("id") or 0)))
    return result


def _metadata_from_snapshot(snapshot: Mapping[str, Any]) -> dict[int, dict[str, Any]]:
    return {int(row["number"]): dict(row) for row in snapshot.get("issues", [])}


ISSUE_METADATA_FIELDS = ("number", "state", "title", "url", "labels", "updatedAt", "body_sha256")
ISSUE_METADATA_FIELDS_EXCLUDING_UPDATED_AT = (
    "number", "state", "title", "url", "labels", "body_sha256"
)


def _validate_issue_metadata_shape(value: Mapping[str, Any], label: str) -> None:
    """Keep live refetch values on the same safe, exact schema as the snapshot."""
    expected = set(ISSUE_METADATA_FIELDS)
    actual = set(value)
    if actual != expected:
        unknown = sorted(actual - expected)
        missing = sorted(expected - actual)
        detail = []
        if unknown:
            detail.append("unknown=" + ",".join(unknown))
        if missing:
            detail.append("missing=" + ",".join(missing))
        raise ValueError(f"{label} metadata schema mismatch: {'; '.join(detail)}")
    number = value.get("number")
    if isinstance(number, bool) or not isinstance(number, int) or number not in ISSUE_RANGE:
        raise ValueError(f"{label}.number is outside #3--#109")
    for key in ("state", "title", "url", "updatedAt"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            raise ValueError(f"{label}.{key} must be a non-empty string")
    if value.get("url") != f"https://github.com/{REPO}/issues/{number}":
        raise ValueError(f"{label}.url is not the canonical Issue URL")
    labels = value.get("labels")
    if not isinstance(labels, list) or not all(isinstance(item, str) and item for item in labels):
        raise ValueError(f"{label}.labels must be a string array")
    if labels != sorted(set(labels)):
        raise ValueError(f"{label}.labels must be unique and sorted")
    if not isinstance(value.get("body_sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", value["body_sha256"]):
        raise ValueError(f"{label}.body_sha256 must be a lowercase SHA-256")


def verify_issue_metadata_unchanged(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    allow_updated_at_drift: bool = False,
) -> None:
    """Issue本文・題名等を固定し、自己コメント由来の時刻だけ任意に許容する。"""
    _validate_issue_metadata_shape(before, f"before Issue #{before.get('number')}")
    _validate_issue_metadata_shape(after, f"after Issue #{after.get('number')}")
    fields = (
        ISSUE_METADATA_FIELDS_EXCLUDING_UPDATED_AT
        if allow_updated_at_drift else ISSUE_METADATA_FIELDS
    )
    for field in fields:
        if before.get(field) != after.get(field):
            raise RuntimeError(f"Issue metadata changed for #{before.get('number')}: {field}")


def verify_issue_metadata_matches_snapshot(
    expected: Mapping[str, Any],
    actual: Mapping[str, Any],
    *,
    allow_updated_at_drift: bool = False,
) -> None:
    """Snapshot照合。既存markerの再開時だけ自己追記のupdatedAt変化を許容する。"""
    _validate_issue_metadata_shape(expected, f"snapshot Issue #{expected.get('number')}")
    _validate_issue_metadata_shape(actual, f"live Issue #{actual.get('number')}")
    fields = (
        ISSUE_METADATA_FIELDS_EXCLUDING_UPDATED_AT
        if allow_updated_at_drift else ISSUE_METADATA_FIELDS
    )
    for field in fields:
        if expected.get(field) != actual.get(field):
            raise RuntimeError(
                f"safe snapshot does not match live Issue #{expected.get('number')}: {field}"
            )


def verify_append_baseline(
    expected: Mapping[int, list[Mapping[str, Any]]],
    actual: Mapping[int, list[Mapping[str, Any]]],
    marker: str,
    *,
    expected_marker_bodies: Mapping[int, str] | None = None,
) -> dict[int, int]:
    """Validate a resumable append state while rejecting unknown comments.

    The only permitted comment outside the 16-comment snapshot is one
    comment containing this exact marker.  That permits a rerun after a
    partial failure to skip already appended Issues and continue with the
    remainder, while a concurrent/unrelated comment stops the operation
    before another write.
    """
    marker_counts: dict[int, int] = {}
    for number in ISSUE_RANGE:
        expected_rows = [_safe_comment_tuple(row) for row in expected.get(number, [])]
        actual_rows = list(actual.get(number, []))
        expected_by_id = {row[0]: row for row in expected_rows}
        if len(expected_by_id) != len(expected_rows):
            raise RuntimeError(f"baseline has duplicate comment IDs: #{number}")
        actual_by_id = {_safe_comment_tuple(row)[0]: _safe_comment_tuple(row) for row in actual_rows}
        if len(actual_by_id) != len(actual_rows):
            raise RuntimeError(f"live comments have duplicate IDs: #{number}")
        for comment_id, expected_row in expected_by_id.items():
            if actual_by_id.get(comment_id) != expected_row:
                raise RuntimeError(f"既存コメントが変化または不足: #{number}")

        marker_rows = []
        for row in actual_rows:
            body = row.get("body") if isinstance(row.get("body"), str) else ""
            count = marker_count(body, marker)
            if count > 1:
                raise RuntimeError(f"marker appears more than once in a comment: #{number}")
            if count == 1:
                marker_rows.append(row)
        if len(marker_rows) > 1:
            raise RuntimeError(f"marker appears in multiple comments: #{number}")

        if marker_rows and expected_marker_bodies is not None:
            expected_body = expected_marker_bodies.get(number)
            if not isinstance(expected_body, str) or not expected_body:
                raise RuntimeError(f"missing expected marker body: #{number}")
            expected_body_sha = sha256_bytes(expected_body.encode("utf-8"))
            for row in marker_rows:
                if row.get("body") != expected_body:
                    raise RuntimeError(f"existing marker body differs from Issue-specific expected text: #{number}")
                if row.get("body_sha256") != expected_body_sha:
                    raise RuntimeError(f"existing marker body SHA differs from Issue-specific expected text: #{number}")

        unknown = [row for row in actual_rows if _safe_comment_tuple(row)[0] not in expected_by_id]
        unknown_ids = {_safe_comment_tuple(row)[0] for row in unknown}
        marker_ids = {_safe_comment_tuple(row)[0] for row in marker_rows}
        if unknown_ids - marker_ids:
            raise RuntimeError(f"未知の既存コメントを検出: #{number}")
        marker_counts[number] = len(marker_rows)
    return marker_counts


def _evidence_token(raw: str) -> str:
    """実ファイル指定なら相対pathと内容SHAをmarker入力へ結合する。"""
    raw = _token(raw, "evidence_bundle")
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = ROOT / candidate
    if candidate.is_file():
        relative = candidate.resolve().relative_to(ROOT.resolve()).as_posix()
        content = candidate.read_bytes()
        if candidate.suffix.lower() == ".json":
            assert_no_placeholders(json.loads(content.decode("utf-8")), source=relative)
        else:
            assert_no_placeholders(content.decode("utf-8", errors="replace"), source=relative)
        return f"{relative}:sha256={sha256_bytes(content)}"
    return raw


def _validate_apply_inputs(rows: list[dict[str, Any]], run_id: str, evidence_bundle: str, commit: str) -> str:
    assert_no_placeholders(rows, source="append_plan")
    assert_current_issue_publication(rows, source="append_plan")
    token = _evidence_token(evidence_bundle)
    marker = marker_for(run_id, token, commit)
    # A marker must itself remain a single HTML comment and carry all three
    # hashed inputs; this guards accidental hand-written marker variants.
    if marker.count("<!--") != 1 or marker.count("-->") != 1:
        raise ValueError("invalid append marker")
    return marker


FINAL_SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
FINAL_PR_RE = re.compile(r"^https://github\.com/hapx2yuki/Tachikoma/pull/[1-9][0-9]*$")
UNFINALIZED_RE = re.compile(
    r"(?:PLACEHOLDER_UNFILLED|PENDING_FINAL_FREEZE2|CANDIDATE_(?:EVIDENCE|ALLOWLIST|CONFIG)|"
    r"REVIEW_REQUIRED|<FINAL_|<DRAFT_)",
    re.IGNORECASE,
)


def _final_repository_path(value: str, label: str) -> str:
    """Validate a final evidence reference without accepting absolute paths."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} path is required")
    value = value.strip()
    raw = Path(value)
    if raw.is_absolute() or ".." in raw.parts or "\\" in value or value.startswith("~"):
        raise ValueError(f"{label} must be a repository-relative path: {value!r}")
    unresolved = ROOT / raw
    root = ROOT.absolute()
    if root.is_symlink():
        raise ValueError(f"{label} repository root is a symlink")
    current = root
    for component in raw.parts:
        current = current / component
        if current.is_symlink():
            raise ValueError(f"{label} contains a symlink component: {current}")
    candidate = unresolved.resolve()
    try:
        relative = candidate.relative_to(ROOT.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError(f"{label} resolves outside repository: {value!r}") from exc
    if relative != value:
        raise ValueError(f"{label} is not a canonical regular repository path: {value!r}")
    # Rewalk the resolved spelling as a second boundary check.  This catches a
    # parent that changed between the lexical and resolved checks and prevents
    # a future caller from reusing this helper without the exclusion check.
    current = root
    for component in Path(relative).parts:
        current = current / component
        if current.is_symlink():
            raise ValueError(f"{label} resolves through a symlink component: {current}")
    if any(part.lower() in {"private", "collision-cache", "public-reproduction"}
           for part in candidate.relative_to(ROOT).parts):
        raise ValueError(f"{label} is in an excluded tree: {value!r}")
    if not candidate.is_file():
        raise ValueError(f"{label} does not exist: {value!r}")
    return relative


def _final_reference(path: str, digest: str, label: str) -> dict[str, str]:
    relative = _final_repository_path(path, label)
    if not isinstance(digest, str) or not FINAL_SHA_RE.fullmatch(digest.lower()):
        raise ValueError(f"{label} SHA must be a 40- or 64-character lowercase commit/file SHA")
    digest = digest.lower()
    actual = sha256_bytes((ROOT / relative).read_bytes())
    if actual != digest:
        raise ValueError(f"{label} SHA does not match {relative}: {actual} != {digest}")
    return {"path": relative, "sha256": digest}


def _require_exact_keys(value: Mapping[str, Any], expected: Iterable[str], label: str) -> None:
    """Reject both omitted and attacker-added fields in final JSON inputs."""
    expected_set = set(expected)
    actual = set(value)
    unknown = sorted(actual - expected_set)
    missing = sorted(expected_set - actual)
    if unknown:
        raise ValueError(f"{label} contains unknown field(s): {', '.join(unknown)}")
    if missing:
        raise ValueError(f"{label} is missing field(s): {', '.join(missing)}")


def _require_sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError(f"{label} must be a lowercase SHA-256")
    return value


def _read_final_json_ref(
    path: str,
    digest: str,
    *,
    label: str,
    root_prefix: str,
    basename_pattern: re.Pattern[str],
) -> tuple[str, bytes, Any]:
    """Read a final JSON reference only after path and content SHA checks."""
    relative = _final_repository_path(path, label)
    if not relative.startswith(root_prefix):
        raise ValueError(f"{label} is outside its prescribed final root: {relative}")
    if not basename_pattern.fullmatch(Path(relative).name):
        raise ValueError(f"{label} has an unapproved filename: {relative}")
    if any("candidate" in part.lower() for part in Path(relative).parts):
        raise ValueError(f"{label} is a candidate artifact, not a final input: {relative}")
    expected = _require_sha256(digest, f"{label} SHA")
    content = (ROOT / relative).read_bytes()
    actual = sha256_bytes(content)
    if actual != expected:
        raise ValueError(f"{label} SHA does not match {relative}: {actual} != {expected}")
    try:
        parsed = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not UTF-8 JSON: {relative}: {exc}") from exc
    if not isinstance(parsed, Mapping):
        raise ValueError(f"{label} must be a JSON object: {relative}")
    return relative, content, parsed


def _validate_frozen_manifest_reference(path: str, digest: str) -> dict[str, str]:
    """Validate the freeze2 generator's exact manifest contract and blobs."""
    relative, _content, manifest = _read_final_json_ref(
        path,
        digest,
        label="freeze2 manifest",
        root_prefix=FINAL_FREEZE_ROOT,
        basename_pattern=re.compile(r"freeze-manifest\.json"),
    )
    _require_exact_keys(manifest, FINAL_FREEZE_FILE_KEYS, "freeze2 manifest")
    if manifest.get("schema_version") != 2 or manifest.get("status") != "FINAL_FROZEN":
        raise ValueError("freeze2 manifest must have schema_version=2 and status=FINAL_FROZEN")
    if not isinstance(manifest.get("geometry_freeze_time"), str) or not manifest["geometry_freeze_time"].strip():
        raise ValueError("freeze2 manifest geometry_freeze_time is required")
    final_urdf = manifest.get("final_urdf")
    if not isinstance(final_urdf, str):
        raise ValueError("freeze2 manifest final_urdf must be a path")
    final_urdf = _final_repository_path(final_urdf, "freeze2 manifest final_urdf")
    bundle = manifest.get("final_mesh_bundle")
    if not isinstance(bundle, Mapping):
        raise ValueError("freeze2 manifest final_mesh_bundle must be an object")
    _require_exact_keys(bundle, FINAL_FREEZE_BUNDLE_KEYS, "freeze2 manifest final_mesh_bundle")
    for key in ("final_urdf_mesh_count", "file_count"):
        value = manifest.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"freeze2 manifest {key} must be a non-negative integer")
    for key in ("referenced_count", "files_count", "orphan_count"):
        value = bundle.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"freeze2 manifest final_mesh_bundle.{key} must be a non-negative integer")
    if bundle.get("orphan_count") != 0 or bundle.get("orphan_policy") != "error":
        raise ValueError("freeze2 manifest has an unsafe orphan policy")
    if bundle.get("referenced_count") != manifest.get("final_urdf_mesh_count"):
        raise ValueError("freeze2 manifest mesh counts disagree")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("freeze2 manifest files must be a non-empty array")
    if manifest.get("file_count") != len(files):
        raise ValueError("freeze2 manifest file_count does not match files")
    seen: set[str] = set()
    urdf_row = None
    for index, row in enumerate(files):
        if not isinstance(row, Mapping):
            raise ValueError(f"freeze2 manifest files[{index}] must be an object")
        _require_exact_keys(row, {"path", "sha256", "role"}, f"freeze2 manifest files[{index}]")
        file_path = row.get("path")
        if not isinstance(file_path, str):
            raise ValueError(f"freeze2 manifest files[{index}].path must be a string")
        file_relative = _final_repository_path(file_path, f"freeze2 manifest files[{index}]")
        if file_relative in seen:
            raise ValueError(f"freeze2 manifest contains duplicate file path: {file_relative}")
        seen.add(file_relative)
        role = row.get("role")
        if role not in FINAL_FREEZE_ROLES:
            raise ValueError(f"freeze2 manifest files[{index}] has an unapproved role: {role!r}")
        expected_sha = _require_sha256(row.get("sha256"), f"freeze2 manifest files[{index}].sha256")
        actual_sha = sha256_bytes((ROOT / file_relative).read_bytes())
        if actual_sha != expected_sha:
            raise ValueError(
                f"freeze2 manifest blob SHA mismatch for {file_relative}: {actual_sha} != {expected_sha}"
            )
        if file_relative == final_urdf:
            urdf_row = row
            if role != "final_urdf":
                raise ValueError("freeze2 manifest final_urdf row must have role final_urdf")
    if urdf_row is None:
        raise ValueError("freeze2 manifest files does not contain final_urdf")
    # The final URDF itself is the only permitted root-level geometry anchor.
    if not final_urdf.endswith(".urdf"):
        raise ValueError("freeze2 manifest final_urdf must be an URDF")
    return {"path": relative, "sha256": digest.lower()}


def _validate_evidence_index_reference(path: str, digest: str) -> dict[str, str]:
    """Validate the small, public final evidence index used by Issue text."""
    relative, content, index = _read_final_json_ref(
        path,
        digest,
        label="evidence index",
        root_prefix=FINAL_EVIDENCE_ROOT,
        basename_pattern=re.compile(r"(?:final-)?evidence-index(?:-[A-Za-z0-9_.-]+)?\.json"),
    )
    _require_exact_keys(index, FINAL_EVIDENCE_INDEX_KEYS, "evidence index")
    if index.get("schema_version") != 1 or index.get("status") != "FINAL_FROZEN":
        raise ValueError("evidence index must have schema_version=1 and status=FINAL_FROZEN")
    entries = index.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("evidence index entries must be a non-empty array")
    if index.get("entry_count") != len(entries):
        raise ValueError("evidence index entry_count does not match entries")
    seen: set[str] = set()
    for number, row in enumerate(entries):
        if not isinstance(row, Mapping):
            raise ValueError(f"evidence index entries[{number}] must be an object")
        _require_exact_keys(row, FINAL_EVIDENCE_ENTRY_KEYS, f"evidence index entries[{number}]")
        entry_path = row.get("path")
        if not isinstance(entry_path, str):
            raise ValueError(f"evidence index entries[{number}].path must be a string")
        entry_relative = _final_repository_path(entry_path, f"evidence index entries[{number}]")
        if entry_relative in seen:
            raise ValueError(f"evidence index contains duplicate path: {entry_relative}")
        seen.add(entry_relative)
        role = row.get("role")
        if not isinstance(role, str) or role not in {
            "final_freeze2_manifest", "final_freeze2_evidence", "final_urdf",
            "final_urdf_mesh", "firmware_or_generator_input", "runtime_input_fingerprint",
        }:
            raise ValueError(f"evidence index entries[{number}] has an unapproved role: {role!r}")
        expected_sha = _require_sha256(row.get("sha256"), f"evidence index entries[{number}].sha256")
        actual_sha = sha256_bytes((ROOT / entry_relative).read_bytes())
        if actual_sha != expected_sha:
            raise ValueError(
                f"evidence index blob SHA mismatch for {entry_relative}: {actual_sha} != {expected_sha}"
            )
    # Keep this explicit so future changes cannot silently turn the index into
    # an opaque binary/object reference.
    if not content.endswith(b"\n"):
        raise ValueError("evidence index must use the canonical newline-terminated JSON form")
    return {"path": relative, "sha256": digest.lower()}


def _canonical_release_layers() -> dict[str, Any]:
    """Recompute A/B/C/max from the canonical print-first manifest.

    Candidate plan fields are never the source of truth.  The finalizer may
    only consume a manifest that has itself reached FINAL_FROZEN; this keeps a
    pending or hand-edited candidate from manufacturing a release quantity.
    """
    for source, label in (
        (CANONICAL_PRINT_MANIFEST, "canonical print-first manifest"),
        (CANONICAL_CONFIG, "canonical config"),
        (CANONICAL_XIAO_PLAN, "canonical XIAO retention plan"),
    ):
        if not source.is_file() or source.is_symlink():
            raise ValueError(f"{label} is missing or is a symlink: {source}")
    try:
        manifest = json.loads(CANONICAL_PRINT_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"canonical print-first manifest is invalid: {exc}") from exc
    if not isinstance(manifest, Mapping) or manifest.get("status") != "FINAL_FROZEN":
        raise ValueError("canonical print-first manifest must be FINAL_FROZEN before Issue finalization")
    layers = manifest.get("production_quantity_layers")
    if not isinstance(layers, Mapping):
        raise ValueError("canonical print-first manifest lacks production_quantity_layers")
    design = layers.get("machine_design_required")
    conditional = layers.get("conditional_new_shin_shell")
    if not isinstance(design, Mapping) or not isinstance(conditional, Mapping):
        raise ValueError("canonical print-first manifest lacks quantity layers")

    def quantities(value: Mapping[str, Any], keys: tuple[str, ...], label: str) -> dict[str, int]:
        result: dict[str, int] = {}
        for key in keys:
            item = value.get(key)
            if isinstance(item, bool) or not isinstance(item, int) or item < 0:
                raise ValueError(f"{label}.{key} must be a non-negative integer")
            result[key] = item
        return result

    design_parts = quantities(design, ("body", "legs", "tpu_shoes", "pla_spacers", "total"), "design")
    prototype = design.get("prototype_quantity")
    remaining = design.get("remaining_after_prototype")
    if not isinstance(prototype, Mapping) or not isinstance(remaining, Mapping):
        raise ValueError("canonical print-first manifest lacks prototype/remaining layers")
    prototype_parts = quantities(prototype, ("body", "legs", "tpu_shoes", "pla_spacers", "total"), "prototype")
    remaining_parts = quantities(remaining, ("body", "legs", "tpu_shoes", "pla_spacers", "total"), "remaining")
    c_quantity = conditional.get("quantity")
    c_maximum = conditional.get("maximum_machine_total")
    if isinstance(c_quantity, bool) or not isinstance(c_quantity, int) or c_quantity < 0:
        raise ValueError("conditional_new_shin_shell.quantity must be a non-negative integer")
    if isinstance(c_maximum, bool) or not isinstance(c_maximum, int) or c_maximum < 0:
        raise ValueError("conditional_new_shin_shell.maximum_machine_total must be a non-negative integer")
    if sum(design_parts[key] for key in ("body", "legs", "tpu_shoes", "pla_spacers")) != design_parts["total"]:
        raise ValueError("canonical design quantity total is inconsistent")
    if sum(prototype_parts[key] for key in ("body", "legs", "tpu_shoes", "pla_spacers")) != prototype_parts["total"]:
        raise ValueError("canonical prototype quantity total is inconsistent")
    if sum(remaining_parts[key] for key in ("body", "legs", "tpu_shoes", "pla_spacers")) != remaining_parts["total"]:
        raise ValueError("canonical remaining quantity total is inconsistent")
    if prototype_parts["total"] + remaining_parts["total"] != design_parts["total"]:
        raise ValueError("canonical A+B quantity is inconsistent with design total")
    if c_maximum != design_parts["total"] + c_quantity:
        raise ValueError("canonical maximum machine quantity is inconsistent with C")
    for value, label in ((design, "design"), (prototype, "prototype"), (remaining, "remaining"), (conditional, "conditional")):
        if value.get("currently_printable_quantity") != 0:
            raise ValueError(f"canonical {label} currently_printable_quantity must remain zero before release")
    return {
        "design_required": design_parts["total"],
        "A": prototype_parts["total"],
        "B": remaining_parts["total"],
        "C": c_quantity,
        "maximum": c_maximum,
        "currently_printable": 0,
        "A_breakdown": {
            "body": prototype_parts["body"],
            "unique_leg_parts": prototype_parts["legs"],
            "tpu_shoe": prototype_parts["tpu_shoes"],
            "pla_spacers": prototype_parts["pla_spacers"],
            "underfoot": prototype_parts["tpu_shoes"] + prototype_parts["pla_spacers"],
            "total": prototype_parts["total"],
        },
        "B_breakdown": {
            "body": remaining_parts["body"],
            "unique_leg_parts": remaining_parts["legs"],
            "tpu_shoe": remaining_parts["tpu_shoes"],
            "pla_spacers": remaining_parts["pla_spacers"],
            "underfoot": remaining_parts["tpu_shoes"] + remaining_parts["pla_spacers"],
            "total": remaining_parts["total"],
        },
        "manifest_sha256": sha256_bytes(CANONICAL_PRINT_MANIFEST.read_bytes()),
        "config_sha256": sha256_bytes(CANONICAL_CONFIG.read_bytes()),
        "xiao_plan_sha256": sha256_bytes(CANONICAL_XIAO_PLAN.read_bytes()),
    }


def _validate_candidate_release_layers(plan: Mapping[str, Any], canonical: Mapping[str, Any]) -> None:
    """Check candidate numbers when present; never use them as a fallback."""
    bundle = plan.get("candidate_evidence_bundle")
    if not isinstance(bundle, Mapping):
        return
    manifest_ref = bundle.get("print_first_manifest")
    if isinstance(manifest_ref, Mapping):
        expected_path = "docs/print-first-manifest.json"
        if manifest_ref.get("path") != expected_path:
            raise ValueError("candidate print-first manifest path is not canonical")
        if manifest_ref.get("status") != "FINAL_FROZEN":
            raise ValueError("candidate print-first manifest status is not FINAL_FROZEN")
        if manifest_ref.get("sha256") != canonical["manifest_sha256"]:
            raise ValueError("candidate print-first manifest SHA differs from canonical manifest")
        expected_fields = {
            "design_required_quantity": canonical["design_required"],
            "initial_prototype_quantity": canonical["A"],
            "remaining_quantity": canonical["B"],
            "conditional_new_shin_shell_quantity": canonical["C"],
            "conditional_machine_maximum_quantity": canonical["maximum"],
        }
        for key, expected in expected_fields.items():
            if manifest_ref.get(key) != expected:
                raise ValueError(f"candidate print-first manifest {key} disagrees with canonical manifest")
    release = bundle.get("print_release_layers")
    if not isinstance(release, Mapping):
        return
    expected_names = {
        "A_minimum_fit_prototype": canonical["A"],
        "B_remaining_after_prototype": canonical["B"],
        "C_conditional_new_shin_shell": canonical["C"],
    }
    for name, expected in expected_names.items():
        entry = release.get(name)
        if not isinstance(entry, Mapping):
            raise ValueError(f"candidate release layer {name} is missing")
        actual = entry.get("quantity")
        if isinstance(actual, Mapping):
            actual = actual.get("total")
        if isinstance(actual, bool) or not isinstance(actual, int) or actual != expected:
            raise ValueError(f"candidate release layer {name} disagrees with canonical manifest")
        expected_breakdown = canonical.get("A_breakdown" if name.startswith("A_") else "B_breakdown")
        if isinstance(entry.get("breakdown"), Mapping) and entry.get("breakdown") != expected_breakdown:
            raise ValueError(f"candidate release layer {name} breakdown disagrees with canonical manifest")
    c_entry = release.get("C_conditional_new_shin_shell")
    if isinstance(c_entry, Mapping) and c_entry.get("maximum_machine_total") != canonical["maximum"]:
        raise ValueError("candidate C maximum disagrees with canonical manifest")


def _walk_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for child in value.values():
            yield from _walk_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_strings(child)


def _assert_final_input(value: Any, *, source: str) -> None:
    """Reject a candidate/placeholder plan before constructing final comments."""
    assert_no_placeholders(value, source=source)
    assert_current_issue_publication(value, source=source)
    for text in _walk_strings(value):
        match = UNFINALIZED_RE.search(text)
        if match:
            raise ValueError(
                f"{source} contains unresolved candidate/freeze state: {match.group(0)}"
            )


def _assert_candidate_plan_for_finalization(value: Mapping[str, Any]) -> None:
    """Validate the useful part of a candidate plan before replacing it.

    A candidate report intentionally contains ``PENDING`` markers and a
    placeholder final-update template.  Those fields are exactly what the
    finalizer must replace, so running the final-output gate over the whole
    candidate would make the documented freeze2 workflow impossible.  The
    issue-specific inputs are still checked strictly; an unresolved marker in
    a condition, next step, focus, or visible candidate text is never carried
    into a final row.
    """
    assert_current_issue_publication(value, source="finalization candidate")
    rows = value.get("all_issue_append_only_update_plan")
    if not isinstance(rows, list):
        raise ValueError("finalization candidate must contain issue rows")
    checked_fields = (
        "acceptance_condition",
        "acceptance_next_step",
        "issue_specific_focus",
        "append_only_update_candidate",
    )
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("finalization candidate issue row must be an object")
        for field in checked_fields:
            field_value = row.get(field)
            if not isinstance(field_value, str) or not field_value.strip():
                continue
            assert_no_placeholders(field_value, source=f"candidate row {row.get('number')}.{field}")
            for text in _walk_strings(field_value):
                match = UNFINALIZED_RE.search(text)
                if match:
                    raise ValueError(
                        "candidate issue row contains unresolved state: "
                        f"#{row.get('number')} {field}: {match.group(0)}"
                    )


def _publication_bundle_descriptor(value: Any, *, label: str = "publication_bundle") -> dict[str, Any]:
    """Validate the final plan's allowlist paths/SHA and selected path set."""
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} is required")
    _require_exact_keys(value, PUBLICATION_BUNDLE_KEYS, label)
    result: dict[str, Any] = {}
    for key, expected_path in (("allowlist_json", ALLOWLIST_JSON_PATH), ("allowlist_md", ALLOWLIST_MD_PATH)):
        ref = value.get(key)
        if not isinstance(ref, Mapping):
            raise ValueError(f"{label}.{key} must be an object")
        _require_exact_keys(ref, {"path", "sha256"}, f"{label}.{key}")
        if ref.get("path") != expected_path:
            raise ValueError(f"{label}.{key}.path must be {expected_path}")
        digest = _require_sha256(ref.get("sha256"), f"{label}.{key}.sha256")
        result[key] = {"path": expected_path, "sha256": digest}
    selected = value.get("publication_selected_paths")
    if not isinstance(selected, list) or not selected or any(not isinstance(path, str) or not path for path in selected):
        raise ValueError(f"{label}.publication_selected_paths must be a non-empty string array")
    if len(selected) != len(set(selected)):
        raise ValueError(f"{label}.publication_selected_paths contains duplicates")
    if set(selected) != {str(path) for path in selected}:
        raise ValueError(f"{label}.publication_selected_paths contains invalid values")
    if ALLOWLIST_JSON_PATH not in selected or ALLOWLIST_MD_PATH not in selected:
        raise ValueError(f"{label}.publication_selected_paths must include both allowlist artifacts")
    # Canonical selected paths are repository-relative POSIX paths.  Importing
    # the allowlist module here would make the plan verifier depend on the
    # mutable worktree, so enforce the path grammar locally as well.
    for path in selected:
        if path.startswith(("/", "~")) or "\\" in path or ".." in Path(path).parts:
            raise ValueError(f"{label}.publication_selected_paths contains a non-relative path: {path!r}")
    result["publication_selected_paths"] = list(selected)
    return result


def _allowlist_record_shas(payload: Mapping[str, Any]) -> dict[str, str]:
    """Collect SHA-bearing records from a final allowlist, rejecting conflicts."""
    records: dict[str, str] = {}
    for key in PUBLICATION_BLOB_RECORD_KEYS:
        rows = payload.get(key)
        if not isinstance(rows, list):
            raise ValueError(f"final allowlist {key} must be an array")
        for index, row in enumerate(rows):
            if not isinstance(row, Mapping):
                raise ValueError(f"final allowlist {key}[{index}] must be an object")
            path = row.get("path")
            if not isinstance(path, str) or not path:
                raise ValueError(f"final allowlist {key}[{index}].path is required")
            exists = row.get("exists")
            if exists is not True:
                raise ValueError(f"final allowlist {key}[{index}] is not an existing public blob")
            digest = _require_sha256(row.get("sha256"), f"final allowlist {key}[{index}].sha256")
            previous = records.get(path)
            if previous is not None and previous != digest:
                raise ValueError(f"final allowlist contains conflicting blob SHAs for {path}")
            records[path] = digest
    return records


def _validate_b_allowlist_payload(
    json_content: bytes,
    md_content: bytes,
    *,
    selected: set[str],
) -> dict[str, str]:
    """Validate allowlist bytes read from Commit B, including its self pair."""
    try:
        text = json_content.decode("utf-8")
        payload = json.loads(text)
        markdown = md_content.decode("utf-8")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Commit B allowlist is not valid UTF-8 JSON/Markdown: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("Commit B allowlist JSON must be an object")
    if payload.get("schema_version") != 2 or payload.get("status") != "FINAL_FROZEN":
        raise ValueError("Commit B allowlist must be schema_version=2 FINAL_FROZEN")
    declared = payload.get("publication_selected_paths")
    if not isinstance(declared, list) or len(declared) != len(set(declared)):
        raise ValueError("Commit B allowlist publication_selected_paths is invalid")
    if set(declared) != selected:
        raise ValueError("Commit B allowlist publication_selected_paths differs from final plan")
    if ALLOWLIST_JSON_PATH not in selected or ALLOWLIST_MD_PATH not in selected:
        raise ValueError("Commit B selected set must include both allowlist artifacts")
    # This calls the same self-content checks used by the read-only stage
    # gate.  It deliberately does not trust content_scan or self-SHA fields.
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        import make_print_first_publication_allowlist as allowlist  # type: ignore
    except ImportError as exc:
        raise ValueError(f"allowlist validator is unavailable: {exc}") from exc
    # The final artifact is a generated schema, rather than an arbitrary JSON
    # envelope carrying a selected path.  Validate its complete top-level and
    # nested shape before trusting the selected set or any recorded SHA.
    allowlist._validate_allowlist_json_structure(payload, require_final=True)
    allowlist._assert_self_blob_safe(text, payload, source="commit B allowlist JSON")
    allowlist._assert_self_blob_safe(markdown, None, source="commit B allowlist Markdown")
    expected_md = allowlist.render_markdown(dict(payload))
    if markdown != expected_md:
        raise ValueError("Commit B allowlist Markdown is not the deterministic JSON rendering")
    return _allowlist_record_shas(payload)


def _default_commit_blob_reader(commit: str, path: str) -> bytes | None:
    result = subprocess.run(
        ["git", "show", f"{commit}:{path}"],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        return None
    return result.stdout


def _commit_tree_paths(git_reader, commit: str) -> set[str]:
    output = _git_reader_output(
        git_reader,
        ["ls-tree", "-r", "--name-only", commit],
        label="Commit B selected publication tree",
    )
    return {line.strip() for line in output.splitlines() if line.strip()}


def _validate_publication_bundle_at_commit(
    plan: Mapping[str, Any],
    commit_b: str,
    *,
    git_reader,
    blob_reader=None,
) -> dict[str, Any]:
    """Bind final Issue input to the exact allowlist bytes in Commit B.

    The normal apply command runs after Commit B, when the working index is
    normally empty.  Therefore the immutable proof is the B tree itself:
    selected paths must be present there, both self artifacts are checked as
    content, and every selected non-self record's SHA is recomputed from B.
    """
    descriptor = _publication_bundle_descriptor(plan.get("publication_bundle"))
    read_blob = blob_reader or _default_commit_blob_reader
    json_path = descriptor["allowlist_json"]["path"]
    md_path = descriptor["allowlist_md"]["path"]
    json_blob = read_blob(commit_b, json_path)
    md_blob = read_blob(commit_b, md_path)
    if not isinstance(json_blob, (bytes, bytearray)) or not isinstance(md_blob, (bytes, bytearray)):
        raise ValueError("Commit B does not contain both final allowlist blobs")
    json_blob = bytes(json_blob)
    md_blob = bytes(md_blob)
    if sha256_bytes(json_blob) != descriptor["allowlist_json"]["sha256"]:
        raise ValueError("Commit B allowlist JSON SHA differs from final plan")
    if sha256_bytes(md_blob) != descriptor["allowlist_md"]["sha256"]:
        raise ValueError("Commit B allowlist Markdown SHA differs from final plan")
    selected = set(descriptor["publication_selected_paths"])
    tree_paths = _commit_tree_paths(git_reader, commit_b)
    if not selected.issubset(tree_paths):
        missing = sorted(selected - tree_paths)
        raise ValueError("Commit B tree is missing selected publication paths: " + ", ".join(missing[:20]))
    records = _validate_b_allowlist_payload(json_blob, md_blob, selected=selected)
    checked: dict[str, str] = {}
    for path in sorted(selected - {json_path, md_path}):
        expected = records.get(path)
        if expected is None:
            raise ValueError(f"selected non-self path has no SHA-bearing allowlist record: {path}")
        blob = read_blob(commit_b, path)
        if not isinstance(blob, (bytes, bytearray)):
            raise ValueError(f"Commit B selected path has no blob: {path}")
        actual = sha256_bytes(bytes(blob))
        if actual != expected:
            raise ValueError(f"Commit B selected blob SHA differs from allowlist for {path}")
        checked[path] = actual
    return {
        "status": "PASS",
        "commit": commit_b,
        "proof": "commit_b_tree_exact_selected_set",
        "selected_paths": sorted(selected),
        "checked_non_self_blob_shas": checked,
        "allowlist_json_sha256": descriptor["allowlist_json"]["sha256"],
        "allowlist_md_sha256": descriptor["allowlist_md"]["sha256"],
    }


def _validate_publication_bundle_worktree(bundle: Mapping[str, Any]) -> dict[str, Any]:
    """Validate final allowlist bytes before a plan is finalized."""
    descriptor = _publication_bundle_descriptor(bundle)
    json_blob = (ROOT / descriptor["allowlist_json"]["path"]).read_bytes()
    md_blob = (ROOT / descriptor["allowlist_md"]["path"]).read_bytes()
    if sha256_bytes(json_blob) != descriptor["allowlist_json"]["sha256"]:
        raise ValueError("final allowlist JSON SHA does not match the worktree")
    if sha256_bytes(md_blob) != descriptor["allowlist_md"]["sha256"]:
        raise ValueError("final allowlist Markdown SHA does not match the worktree")
    _validate_b_allowlist_payload(
        json_blob,
        md_blob,
        selected=set(descriptor["publication_selected_paths"]),
    )
    return descriptor


def _final_plan_publication_refs(plan: Mapping[str, Any]) -> tuple[str, str]:
    finalization = plan.get("finalization")
    template = plan.get("final_update_template")
    if not isinstance(finalization, Mapping) or finalization.get("status") != "FINAL_FROZEN":
        raise ValueError("normal Issue apply requires a FINAL_FROZEN finalization block")
    if not isinstance(template, Mapping) or template.get("status") != "FINAL_FROZEN":
        raise ValueError("normal Issue apply requires a FINAL_FROZEN final update template")
    commit = finalization.get("commit_sha")
    pr_url = finalization.get("pull_request_url")
    if not isinstance(commit, str) or not FINAL_SHA_RE.fullmatch(commit.lower()):
        raise ValueError("final Issue plan lacks a valid evidence commit A SHA")
    if template.get("commit_sha") != commit:
        raise ValueError("final Issue plan commit references disagree")
    if not isinstance(pr_url, str) or not FINAL_PR_RE.fullmatch(pr_url):
        raise ValueError("final Issue plan lacks a canonical draft PR URL")
    if template.get("pull_request_url") != pr_url:
        raise ValueError("final Issue plan draft PR references disagree")
    gate = plan.get("publication_gate")
    if not isinstance(gate, Mapping):
        raise ValueError("final Issue plan lacks publication_gate")
    if gate.get("publication_ready") is not True:
        raise ValueError("final Issue plan is not publication_ready")
    if gate.get("external_issue_update_performed") is not False:
        raise ValueError("final Issue plan records an earlier external Issue update")
    if gate.get("external_project_update_performed") is not False:
        raise ValueError("final Issue plan records an earlier external Project update")
    bundle = _publication_bundle_descriptor(plan.get("publication_bundle"))
    final_bundle = _publication_bundle_descriptor(finalization.get("publication_bundle"))
    template_bundle = _publication_bundle_descriptor(template.get("publication_bundle"))
    if bundle != final_bundle or bundle != template_bundle:
        raise ValueError("final Issue plan publication bundle references disagree")
    return commit.lower(), pr_url


def _default_git_reader(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=ROOT, text=True, capture_output=True, check=False
    )


def _git_reader_output(
    git_reader, args: list[str], *, label: str, allow_failure: bool = False
) -> str:
    result = git_reader(args)
    if isinstance(result, str):
        return result
    if not hasattr(result, "returncode"):
        raise ValueError(f"git reader returned an invalid result for {label}")
    if result.returncode and not allow_failure:
        raise ValueError(f"git {label} failed: {(result.stderr or '').strip()[:300]}")
    return result.stdout or ""


def _default_pr_reader(url: str, repo: str) -> Mapping[str, Any]:
    raw = run_gh(
        "pr", "view", url, "--repo", repo,
        "--json", "isDraft,headRefName,headRefOid,headRepository,baseRepository,baseRefName,url",
    )
    value = json.loads(raw)
    if not isinstance(value, Mapping):
        raise ValueError("gh pr view returned a non-object")
    return value


def _default_branch_reader(repo: str) -> str:
    """Read the repository's live default branch without trusting local config."""
    raw = run_gh("repo", "view", repo, "--json", "defaultBranchRef")
    value = json.loads(raw)
    if not isinstance(value, Mapping):
        raise ValueError("gh repo view returned a non-object")
    ref = value.get("defaultBranchRef")
    if not isinstance(ref, Mapping) or not isinstance(ref.get("name"), str) or not ref["name"].strip():
        raise ValueError("live repository defaultBranchRef.name is missing")
    return ref["name"].strip()


def verify_publication_context(
    plan: Mapping[str, Any],
    *,
    plan_path: str,
    plan_sha256: str,
    plan_commit_b: str,
    repo: str = REPO,
    branch: str = "codex/print-first-20260905",
    git_reader=None,
    pr_reader=None,
    default_branch_reader=None,
    blob_reader=None,
) -> dict[str, Any]:
    """Verify the A -> draft PR -> B lineage before a normal Issue apply."""
    commit_a, pr_url = _final_plan_publication_refs(plan)
    if not isinstance(plan_commit_b, str) or not FINAL_SHA_RE.fullmatch(plan_commit_b.lower()):
        raise ValueError("plan commit B must be a lowercase commit SHA")
    plan_commit_b = plan_commit_b.lower()
    if not isinstance(plan_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", plan_sha256.lower()):
        raise ValueError("final Issue plan SHA-256 is required")
    relative_path = _final_repository_path(plan_path, "final Issue plan")
    plan_bytes = (ROOT / relative_path).read_bytes()
    actual_plan_sha = sha256_bytes(plan_bytes)
    if actual_plan_sha != plan_sha256.lower():
        raise ValueError("final Issue plan SHA-256 does not match the worktree file")
    reader = git_reader or _default_git_reader
    head = _git_reader_output(reader, ["rev-parse", "HEAD"], label="HEAD").strip().lower()
    current_branch = _git_reader_output(reader, ["branch", "--show-current"], label="branch").strip()
    if current_branch != branch:
        raise ValueError(f"local branch is not the publication branch: {current_branch!r}")
    remote_lines = _git_reader_output(
        reader, ["ls-remote", "origin", f"refs/heads/{branch}"], label="remote branch"
    ).splitlines()
    remote_sha = remote_lines[0].split()[0].lower() if remote_lines else ""
    if remote_sha != plan_commit_b:
        raise ValueError("origin publication branch does not point at plan commit B")
    _git_reader_output(reader, ["cat-file", "-e", f"{plan_commit_b}^{{commit}}"], label="plan commit B")
    ancestor = reader(["merge-base", "--is-ancestor", commit_a, plan_commit_b])
    if not isinstance(ancestor, str) and getattr(ancestor, "returncode", 1) != 0:
        raise ValueError("evidence commit A is not an ancestor of plan commit B")
    if isinstance(ancestor, str):
        # Injected readers may return text only; requiring a non-empty marker
        # keeps the test seam explicit without weakening production checks.
        if ancestor.strip().lower() not in {"1", "true", "yes", "pass", "ok"}:
            raise ValueError("injected ancestry check did not pass")
    local_ancestor = reader(["merge-base", "--is-ancestor", plan_commit_b, "HEAD"])
    if not isinstance(local_ancestor, str) and getattr(local_ancestor, "returncode", 1) != 0:
        raise ValueError("local publication branch does not contain plan commit B")
    if isinstance(local_ancestor, str) and local_ancestor.strip().lower() not in {"1", "true", "yes", "pass", "ok"}:
        raise ValueError("injected local ancestry check did not pass")
    committed_plan = _git_reader_output(
        reader, ["show", f"{plan_commit_b}:{relative_path}"], label="plan path at commit B"
    ).encode("utf-8")
    if sha256_bytes(committed_plan) != actual_plan_sha:
        raise ValueError("plan path/SHA readback at commit B does not match the final input")
    publication_bundle = _validate_publication_bundle_at_commit(
        plan,
        plan_commit_b,
        git_reader=reader,
        blob_reader=blob_reader,
    )
    pull = (pr_reader or _default_pr_reader)(pr_url, repo)
    if pull.get("url") != pr_url or pull.get("isDraft") is not True:
        raise ValueError("draft PR URL/readback is not a draft PR")
    head_repo = pull.get("headRepository") or {}
    base_repo = pull.get("baseRepository") or {}
    head_full_name = head_repo.get("fullName") if isinstance(head_repo, Mapping) else head_repo
    base_full_name = base_repo.get("fullName") if isinstance(base_repo, Mapping) else base_repo
    if head_full_name != repo or base_full_name != repo:
        raise ValueError("draft PR repositories do not match the target repository")
    live_default_branch = (default_branch_reader or _default_branch_reader)(repo)
    if pull.get("baseRefName") != live_default_branch:
        raise ValueError(
            "draft PR base branch does not match the live repository default branch: "
            f"{pull.get('baseRefName')!r} != {live_default_branch!r}"
        )
    if pull.get("headRefName") != branch or str(pull.get("headRefOid", "")).lower() != plan_commit_b:
        raise ValueError("draft PR head does not match publication branch/plan commit B")
    return {
        "status": "PASS",
        "evidence_commit_A": commit_a,
        "draft_pr_url": pr_url,
        "plan_commit_B": plan_commit_b,
        "plan_path": relative_path,
        "plan_sha256": actual_plan_sha,
        "branch": branch,
        "base_branch": live_default_branch,
        "remote_branch_sha": remote_sha,
        "head": head,
        "publication_bundle": publication_bundle,
    }


def finalize_plan_payload(
    plan: Mapping[str, Any],
    *,
    commit_sha: str,
    pull_request_url: str,
    freeze2_manifest: str,
    freeze2_sha256: str,
    evidence_index: str,
    evidence_index_sha256: str,
    allowlist_json: str | None = None,
    allowlist_json_sha256: str | None = None,
    allowlist_md: str | None = None,
    allowlist_md_sha256: str | None = None,
) -> dict[str, Any]:
    """Freeze 107 issue append rows into deterministic, visible final text.

    The operation is pure with respect to ``plan``.  It requires the caller to
    pass real commit/PR/evidence references whose files and SHA-256 values are
    checked in the current repository; unresolved candidate plans fail closed.
    No GitHub or git index operation occurs here.
    """
    if not isinstance(plan, Mapping):
        raise ValueError("finalization input must be an object")
    if not isinstance(commit_sha, str) or not FINAL_SHA_RE.fullmatch(commit_sha.lower()):
        raise ValueError("commit_sha must be a 40- or 64-character lowercase SHA")
    commit_sha = commit_sha.lower()
    if not isinstance(pull_request_url, str) or not FINAL_PR_RE.fullmatch(pull_request_url):
        raise ValueError("pull_request_url must be the canonical repository draft PR URL")
    freeze_ref = _validate_frozen_manifest_reference(freeze2_manifest, freeze2_sha256)
    evidence_ref = _validate_evidence_index_reference(evidence_index, evidence_index_sha256)
    if not all((allowlist_json, allowlist_json_sha256, allowlist_md, allowlist_md_sha256)):
        raise ValueError(
            "finalization requires final allowlist JSON/Markdown paths and SHA-256 values"
        )
    allowlist_json_path = ROOT / str(allowlist_json)
    try:
        allowlist_json_payload = json.loads(allowlist_json_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"final allowlist JSON is not readable: {allowlist_json}: {exc}") from exc
    if not isinstance(allowlist_json_payload, Mapping):
        raise ValueError("final allowlist JSON must be an object")
    selected_paths = allowlist_json_payload.get("publication_selected_paths")
    if not isinstance(selected_paths, list):
        raise ValueError("final allowlist must declare publication_selected_paths")
    publication_bundle = _validate_publication_bundle_worktree({
        "allowlist_json": {"path": allowlist_json, "sha256": allowlist_json_sha256},
        "allowlist_md": {"path": allowlist_md, "sha256": allowlist_md_sha256},
        # The final allowlist is the sole authority for this set.  Read it
        # once here so a caller cannot supply a second, divergent selection.
        "publication_selected_paths": selected_paths,
    })

    result = copy.deepcopy(dict(plan))
    rows = result.get("all_issue_append_only_update_plan")
    if not isinstance(rows, list) or [int(row.get("number", -1)) for row in rows] != list(ISSUE_RANGE):
        raise ValueError("finalization input must contain ordered #3--#109 rows")
    # A candidate report has deliberately unresolved top-level fields.  Check
    # the issue-specific inputs strictly, then replace those fields below;
    # applying the final-output gate to the candidate itself would prevent the
    # freeze2 finalization command from consuming its documented input.
    _assert_candidate_plan_for_finalization(result)

    # The final allowlist carries the selected set.  Re-read it after the
    # descriptor check and bind that exact set into the output plan.
    if not isinstance(selected_paths, list) or not selected_paths:
        raise ValueError("final allowlist must declare a non-empty publication_selected_paths set")
    publication_bundle["publication_selected_paths"] = list(selected_paths)

    common = (
        f"共通根拠: commit={commit_sha}; draft PR={pull_request_url}; "
        f"freeze2台帳={freeze_ref['path']} (SHA-256={freeze_ref['sha256']}); "
        f"根拠索引={evidence_ref['path']} (SHA-256={evidence_ref['sha256']}); "
        f"公開allowlist JSON={publication_bundle['allowlist_json']['path']} "
        f"(SHA-256={publication_bundle['allowlist_json']['sha256']}); "
        f"公開allowlist MD={publication_bundle['allowlist_md']['path']} "
        f"(SHA-256={publication_bundle['allowlist_md']['sha256']})。"
    )
    canonical = _canonical_release_layers()
    _validate_candidate_release_layers(result, canonical)
    a_quantity = canonical["A"]
    b_quantity = canonical["B"]
    c_quantity = canonical["C"]
    c_maximum = canonical["maximum"]
    candidate_release = (result.get("candidate_evidence_bundle") or {}).get(
        "print_release_layers", {}
    )
    c_entry = candidate_release.get("C_conditional_new_shin_shell", {}) if isinstance(candidate_release, dict) else {}
    a_breakdown = canonical["A_breakdown"]
    a_purpose = (
        f"全体仮組み用body{a_breakdown['body']}＋左右/前後の固有脚部品"
        f"{a_breakdown['unique_leg_parts']}＋1脚分足裏{a_breakdown['underfoot']}"
    )
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("finalization issue row must be an object")
        number = int(row["number"])
        condition = str(row.get("acceptance_condition") or "").strip()
        next_step = str(row.get("acceptance_next_step") or "").strip()
        focus = str(row.get("issue_specific_focus") or "").strip()
        if not condition or not next_step or not focus:
            raise ValueError(f"Issue #{number} lacks issue-specific acceptance/focus fields")
        issue_result = row.get("final_issue_result")
        if not isinstance(issue_result, str) or not issue_result.strip():
            # Candidate results are evidence hints, not final findings.  Do
            # not promote ``candidate_current_result`` into a public Issue
            # comment when freeze2 has not supplied a row-specific result.
            issue_result = f"最終判定は未完了。{focus}について、確定根拠と実機確認を照合する。"
        issue_result = issue_result.strip().rstrip("。.")
        assert_no_placeholders(issue_result, source=f"final Issue #{number} result")
        assert_current_issue_publication(issue_result, source=f"final Issue #{number} result")
        match = UNFINALIZED_RE.search(issue_result)
        if match:
            raise ValueError(
                f"final Issue #{number} result contains unresolved state: {match.group(0)}"
            )
        unfinished = f"{next_step}（受入条件: {condition}）"
        row["final_issue_result"] = issue_result
        row["final_uncompleted_condition"] = unfinished
        row["final_common_evidence"] = common
        row["candidate_evidence_status"] = "FINAL_FROZEN"
        row["finalized"] = True
        row.pop("candidate_current_result", None)
        context = row.get("print_first_context")
        if isinstance(context, dict):
            context["status"] = "FINAL_FROZEN"
            context["release_layers_status"] = (
                f"A: 初回全体仮組み{a_quantity}個（{a_purpose}。実物適合の最小対象は1靴・1脚）。"
                f"B: A合格後に残り{b_quantity}個を判定。"
                f"C: 既存脛殻を加工できない場合だけ新規{c_quantity}個、完成機最大{c_maximum}個。"
                "各数量の印刷解放は確定根拠と実機ゲートの結果に従う。"
            )
        row["append_only_update_candidate"] = (
            f"Issue #{number}固有結果: {issue_result}。"
            f"未完了条件/次作業: {unfinished}。{common}"
        )

    # Candidate-only evidence summaries and templates must not survive as
    # apparently final metadata.  Keep only the immutable references supplied
    # to this finalization call.
    result.pop("candidate_evidence_bundle", None)
    result["final_evidence_bundle"] = {
        "status": "FINAL_FROZEN",
        "freeze2_manifest": freeze_ref,
        "evidence_index": evidence_ref,
        "publication_bundle": publication_bundle,
        "release_layers": {
            "A_quantity": a_quantity,
            "B_quantity": b_quantity,
            "C_quantity": c_quantity,
            "C_maximum_machine_total": c_maximum,
            "A_plus_B": a_quantity + b_quantity,
            "C_exclusive_with_existing_shell_reuse": True,
            "design_required_quantity": canonical["design_required"],
            "currently_printable_quantity": canonical["currently_printable"],
            "canonical_manifest_sha256": canonical["manifest_sha256"],
            "canonical_config_sha256": canonical["config_sha256"],
            "canonical_xiao_plan_sha256": canonical["xiao_plan_sha256"],
        },
        "issue_count": len(rows),
    }
    if isinstance(result.get("dependency_audit"), dict):
        result["dependency_audit"]["status"] = "FINAL_FROZEN"
    if isinstance(result.get("append_only_update_plan"), list) and result["append_only_update_plan"]:
        result["append_only_update_plan"][-1] = (
            "全107件のIssue行を、各課題固有の結果・未完了条件と共通根拠付きの"
            "FINAL_FROZEN追記文へ確定した。"
        )
    if isinstance(result.get("public_safety"), dict):
        result["public_safety"].update({
            "candidate_evidence_is_not_final": False,
            "final_evidence_references_only": True,
        })
    result["finalization"] = {
        "status": "FINAL_FROZEN",
        "commit_sha": commit_sha,
        "pull_request_url": pull_request_url,
        "freeze2_manifest": freeze_ref,
        "evidence_index": evidence_ref,
        "publication_bundle": publication_bundle,
        "issue_count": len(rows),
        "candidate_rows_replaced": True,
        "visible_common_evidence_in_each_row": True,
    }
    result["publication_bundle"] = publication_bundle
    result["final_update_template"] = {
        "status": "FINAL_FROZEN",
        "commit_sha": commit_sha,
        "pull_request_url": pull_request_url,
        "freeze2_manifest": freeze_ref,
        "evidence_index": evidence_ref,
        "publication_bundle": publication_bundle,
        "rule": "各Issueの固有結果・未完了条件と共通根拠を含むappend_only_update_candidateだけを外部追記へ渡す。",
    }
    if isinstance(result.get("publication_gate"), dict):
        result["publication_gate"].update({
            "publication_ready": True,
            "finalization_status": "FINAL_FROZEN",
            "external_issue_update_performed": False,
            "external_project_update_performed": False,
        })
    _assert_final_input(result, source="finalization output")
    return result


def finalize_plan_file(
    input_path: Path,
    output_path: Path,
    **kwargs: str,
) -> dict[str, Any]:
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    result = finalize_plan_payload(payload, **kwargs)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def _read_live_baseline(repo: str) -> tuple[dict[int, dict[str, Any]], dict[int, list[dict[str, Any]]]]:
    metadata_rows = fetch_issue_metadata(repo)
    if [row["number"] for row in metadata_rows] != list(ISSUE_RANGE):
        raise RuntimeError("live Issue metadata does not cover #3--#109")
    metadata: dict[int, dict[str, Any]] = {}
    comments: dict[int, list[dict[str, Any]]] = {}
    for row in metadata_rows:
        number = int(row["number"])
        metadata[number] = {
            **row,
            "labels": sorted(row.get("labels", [])),
            "body_sha256": _issue_metadata(number, repo)["body_sha256"],
        }
        comments[number] = fetch_comments(number, repo)
    return metadata, comments


def _publisher_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_publisher_state(state: Mapping[str, Any]) -> None:
    """Durably replace the private publisher run state without raw content."""
    if set(state) != set(PUBLISHER_STATE_KEYS):
        raise ValueError("publisher state schema mismatch")
    PUBLISHER_PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=".github-issue-publisher-state-",
        suffix=".tmp",
        dir=PUBLISHER_PRIVATE_DIR,
    )
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            fd = -1
            json.dump(dict(state), stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, PUBLISHER_STATE_PATH)
        directory_fd = os.open(PUBLISHER_PRIVATE_DIR, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _read_publisher_state() -> dict[str, Any] | None:
    if not PUBLISHER_STATE_PATH.exists():
        return None
    try:
        value = json.loads(PUBLISHER_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"publisher durable state is unreadable: {exc}") from exc
    if not isinstance(value, dict) or set(value) != set(PUBLISHER_STATE_KEYS):
        raise RuntimeError("publisher durable state has an unknown or incomplete schema")
    if value.get("schema_version") != 1:
        raise RuntimeError("publisher durable state schema_version is unsupported")
    if value.get("status") not in {"RUNNING", "PASS", "FAILED"}:
        raise RuntimeError("publisher durable state status is invalid")
    for key in ("run_token_sha256", "marker_sha256"):
        if not isinstance(value.get(key), str) or not re.fullmatch(r"[0-9a-f]{64}", value[key]):
            raise RuntimeError(f"publisher durable state {key} is invalid")
    for key in ("started_at", "updated_at"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            raise RuntimeError(f"publisher durable state {key} is invalid")
    if value.get("status") == "RUNNING":
        if value.get("completed_at") is not None or value.get("error_type") is not None:
            raise RuntimeError("RUNNING publisher state has completion/error fields")
    else:
        if not isinstance(value.get("completed_at"), str) or not value["completed_at"].strip():
            raise RuntimeError("completed publisher state lacks completed_at")
        if value.get("status") == "FAILED" and not isinstance(value.get("error_type"), str):
            raise RuntimeError("FAILED publisher state lacks error_type")
        if value.get("status") == "PASS" and value.get("error_type") is not None:
            raise RuntimeError("PASS publisher state contains an error_type")
    return value


@contextmanager
def publisher_run_lock(run_token: str, marker: str):
    """Serialize one local publisher and persist a crash-resumable run state.

    The lock is host-local.  GitHub cannot be atomically claimed here without
    adding an external ref side effect, so distributed writers are detected
    by the exact marker/body readback and the operation stops on duplicates or
    unknown comments.  A stale RUNNING state can resume only with the same
    run token and marker hashes.
    """
    run_token = _token(run_token, "publisher_run_id")
    marker = _token(marker, "marker")
    run_digest = sha256_bytes(run_token.encode("utf-8"))
    marker_digest = sha256_bytes(marker.encode("utf-8"))
    PUBLISHER_PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(PUBLISHER_LOCK_PATH, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(
                "another local Issue publisher is active; distributed writers are not claimable and must stop"
            ) from exc
        previous = _read_publisher_state()
        if previous and previous["status"] == "RUNNING":
            if previous["run_token_sha256"] != run_digest or previous["marker_sha256"] != marker_digest:
                raise RuntimeError(
                    "durable publisher state records a different unfinished run; refuse concurrent resume"
                )
        now = _publisher_now()
        state: dict[str, Any] = {
            "schema_version": 1,
            "status": "RUNNING",
            "run_token_sha256": run_digest,
            "marker_sha256": marker_digest,
            "started_at": previous.get("started_at", now) if previous and previous["status"] == "RUNNING" else now,
            "updated_at": now,
            "completed_at": None,
            "error_type": None,
        }
        _write_publisher_state(state)
        try:
            yield state
        except BaseException as exc:
            failed = {
                **state,
                "status": "FAILED",
                "updated_at": _publisher_now(),
                "completed_at": _publisher_now(),
                "error_type": type(exc).__name__,
            }
            _write_publisher_state(failed)
            raise
        else:
            passed = {
                **state,
                "status": "PASS",
                "updated_at": _publisher_now(),
                "completed_at": _publisher_now(),
                "error_type": None,
            }
            _write_publisher_state(passed)
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _apply_updates_unlocked(
    rows: list[dict[str, Any]],
    snapshot: Mapping[str, Any],
    marker: str,
    *,
    repo: str = REPO,
    gh_runner=run_gh,
    final_plan: Mapping[str, Any] | None = None,
    publication_context: Mapping[str, Any] | None = None,
    allow_test_inputs: bool = False,
) -> dict[str, Any]:
    """全件事前検査→追記→全件読戻し。失敗時は後続書込みを止める。"""
    if not allow_test_inputs:
        if final_plan is None:
            raise RuntimeError("normal Issue apply requires a FINAL_FROZEN plan input")
        _final_plan_publication_refs(final_plan)
        if not isinstance(publication_context, Mapping) or publication_context.get("status") != "PASS":
            raise RuntimeError("normal Issue apply requires a verified A/draft PR/B publication context")
        context_bundle = publication_context.get("publication_bundle")
        if not isinstance(context_bundle, Mapping) \
                or context_bundle.get("status") != "PASS" \
                or context_bundle.get("proof") != "commit_b_tree_exact_selected_set":
            raise RuntimeError(
                "normal Issue apply requires the Commit B tree publication bundle proof"
            )
        plan_bundle = _publication_bundle_descriptor(final_plan.get("publication_bundle"))
        context_selected = context_bundle.get("selected_paths")
        if not isinstance(context_selected, list) or set(context_selected) != set(plan_bundle["publication_selected_paths"]):
            raise RuntimeError(
                "normal Issue apply publication selection differs from the verified Commit B tree"
            )
        if len(rows) != len(ISSUE_RANGE):
            raise RuntimeError("normal Issue apply requires all 107 final Issue rows")
        for row in rows:
            if row.get("candidate_evidence_status") != "FINAL_FROZEN" or row.get("finalized") is not True:
                raise RuntimeError(f"normal Issue apply received a non-final row: #{row.get('number')}")
    expected_comments = baseline_comments(snapshot)
    expected_metadata = _metadata_from_snapshot(snapshot)
    before_metadata: dict[int, dict[str, Any]] = {}
    before_comments: dict[int, list[dict[str, Any]]] = {}
    before_marker_counts: dict[int, int] = {}
    rows_by_number = {int(row["number"]): row for row in rows}
    expected_marker_bodies = {
        number: marker + "\n\n" + str(rows_by_number[number]["append_only_update_candidate"])
        for number in ISSUE_RANGE
    }

    # Fetch all preconditions before the first write so a stale snapshot cannot
    # cause a partial update.
    for number in ISSUE_RANGE:
        expected_row = expected_metadata.get(number)
        if expected_row is None:
            raise RuntimeError(f"safe snapshot has no Issue metadata for #{number}")
        # Read comments before metadata.  A resumed run may already contain
        # this marker, and GitHub can advance updated_at when that comment was
        # created.  The marker state determines which snapshot fields remain
        # strict; unknown comments are always rejected by the same check.
        live_comments = _issue_comments_with_body(number, repo)
        marker_counts = verify_append_baseline(
            {number: expected_comments[number]}, {number: live_comments}, marker,
            expected_marker_bodies=expected_marker_bodies,
        )
        before_marker_counts[number] = marker_counts[number]
        before_metadata[number] = _issue_metadata(number, repo)
        verify_issue_metadata_matches_snapshot(
            expected_row,
            before_metadata[number],
            allow_updated_at_drift=marker_counts[number] == 1,
        )
        safe_comments = [
            {key: value for key, value in comment.items() if key != "body"}
            for comment in live_comments
        ]
        before_comments[number] = safe_comments

    added = 0
    skipped_existing = 0
    for number in ISSUE_RANGE:
        live_comments = _issue_comments_with_body(number, repo)
        count = verify_append_baseline(
            {number: expected_comments[number]}, {number: live_comments}, marker,
            expected_marker_bodies=expected_marker_bodies,
        )[number]
        if count == 1:
            skipped_existing += 1
            continue
        candidate = rows_by_number[number]["append_only_update_candidate"]
        payload = marker + "\n\n" + candidate
        assert_no_placeholders(payload, source=f"Issue #{number} append")
        # The only mutating command in this module.
        gh_runner("issue", "comment", str(number), "--repo", repo, "--body", payload)
        added += 1

    after_metadata: dict[int, dict[str, Any]] = {}
    after_comments: dict[int, list[dict[str, Any]]] = {}
    for number in ISSUE_RANGE:
        live_comments = _issue_comments_with_body(number, repo)
        safe_comments = [{key: value for key, value in comment.items() if key != "body"} for comment in live_comments]
        after_comments[number] = safe_comments
        count = verify_append_baseline(
            {number: expected_comments[number]}, {number: live_comments}, marker,
            expected_marker_bodies=expected_marker_bodies,
        )[number]
        if count != 1:
            raise RuntimeError(f"marker readback count is {count}, expected exactly one: #{number}")
        after_metadata[number] = _issue_metadata(number, repo)
        verify_issue_metadata_unchanged(
            before_metadata[number],
            after_metadata[number],
            allow_updated_at_drift=(before_marker_counts[number] == 0 and count == 1),
        )

    return {
        "status": "PASS",
        "marker": marker,
        "issue_count": len(rows),
        "comments_added": added,
        "already_marked": skipped_existing,
        "existing_comment_count_preserved": sum(len(value) for value in expected_comments.values()),
        "raw_issue_bodies_output": False,
        "raw_comment_bodies_output": False,
        "issue_body_or_metadata_changed": False,
        "project_updated": False,
        "readback_marker_exactly_once": True,
    }


def apply_updates(
    rows: list[dict[str, Any]],
    snapshot: Mapping[str, Any],
    marker: str,
    *,
    repo: str = REPO,
    gh_runner=run_gh,
    final_plan: Mapping[str, Any] | None = None,
    publication_context: Mapping[str, Any] | None = None,
    allow_test_inputs: bool = False,
    publisher_run_id: str | None = None,
) -> dict[str, Any]:
    """Run the append operation under the local single-publisher boundary."""
    if allow_test_inputs:
        return _apply_updates_unlocked(
            rows,
            snapshot,
            marker,
            repo=repo,
            gh_runner=gh_runner,
            final_plan=final_plan,
            publication_context=publication_context,
            allow_test_inputs=True,
        )
    # The CLI supplies the explicit run id.  Falling back to the marker keeps
    # the programmatic API safe for callers that predate that argument while
    # still binding the durable state to this exact append attempt.
    run_id = publisher_run_id or marker
    with publisher_run_lock(run_id, marker):
        result = _apply_updates_unlocked(
            rows,
            snapshot,
            marker,
            repo=repo,
            gh_runner=gh_runner,
            final_plan=final_plan,
            publication_context=publication_context,
            allow_test_inputs=False,
        )
    result["publisher_concurrency_boundary"] = (
        PUBLISHER_CONCURRENCY_BOUNDARY
    )
    return result


def dry_run(rows: list[dict[str, Any]], snapshot: Mapping[str, Any], marker: str) -> dict[str, Any]:
    expected = baseline_comments(snapshot)
    preserved = sum(len(value) for value in expected.values())
    return {
        "status": "DRY_RUN",
        "marker": marker,
        "issue_count": len(rows),
        "planned_comments": len(rows),
        "baseline_comments": preserved,
        "raw_issue_bodies_output": False,
        "raw_comment_bodies_output": False,
        "github_write_performed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--repo", default=REPO)
    parser.add_argument("--run-id")
    parser.add_argument("--evidence-bundle", help="最終根拠束の相対pathまたは固定識別子")
    parser.add_argument("--commit")
    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Issueコメントを追記する（単一publisherのみ。同一hostはfcntl+durable state、"
            "別host競合は投稿後検出。競合時は重複が外部に残り得る）"
        ),
    )
    parser.add_argument("--readback", type=Path, help="安全な読戻し結果（本文なし）を書き出す")
    parser.add_argument(
        "--finalize-plan",
        type=Path,
        help="候補計画を実値のcommit/PR/freeze2根拠付き107行へ確定して保存する（外部更新なし）",
    )
    parser.add_argument("--pull-request-url", help="最終draft PRの正規URL")
    parser.add_argument("--freeze2-manifest", help="最終freeze2台帳のリポジトリ相対path")
    parser.add_argument("--freeze2-sha256", help="最終freeze2台帳のSHA-256")
    parser.add_argument("--evidence-index", help="最終根拠索引のリポジトリ相対path")
    parser.add_argument("--evidence-index-sha256", help="最終根拠索引のSHA-256")
    parser.add_argument("--allowlist-json", help="FINAL_FROZEN公開allowlist JSONの正規path")
    parser.add_argument("--allowlist-json-sha256", help="FINAL_FROZEN公開allowlist JSONのSHA-256")
    parser.add_argument("--allowlist-md", help="FINAL_FROZEN公開allowlist Markdownの正規path")
    parser.add_argument("--allowlist-md-sha256", help="FINAL_FROZEN公開allowlist MarkdownのSHA-256")
    parser.add_argument("--plan-commit-b", help="最終計画/allowlist commit BのSHA（通常apply必須）")
    parser.add_argument("--plan-sha256", help="最終計画pathのSHA-256（通常apply必須）")
    parser.add_argument(
        "--publication-branch",
        default="codex/print-first-20260905",
        help="A/Bとdraft PRを検証する公開branch",
    )
    args = parser.parse_args()
    plan_path = args.plan if args.plan.is_absolute() else ROOT / args.plan
    snapshot_path = args.snapshot if args.snapshot.is_absolute() else ROOT / args.snapshot
    if args.finalize_plan:
        required = {
            "--commit": args.commit,
            "--pull-request-url": args.pull_request_url,
            "--freeze2-manifest": args.freeze2_manifest,
            "--freeze2-sha256": args.freeze2_sha256,
            "--evidence-index": args.evidence_index,
            "--evidence-index-sha256": args.evidence_index_sha256,
            "--allowlist-json": args.allowlist_json,
            "--allowlist-json-sha256": args.allowlist_json_sha256,
            "--allowlist-md": args.allowlist_md,
            "--allowlist-md-sha256": args.allowlist_md_sha256,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise SystemExit("finalization requires " + ", ".join(missing))
        output = args.finalize_plan if args.finalize_plan.is_absolute() else ROOT / args.finalize_plan
        finalized = finalize_plan_file(
            plan_path,
            output,
            commit_sha=args.commit,
            pull_request_url=args.pull_request_url,
            freeze2_manifest=args.freeze2_manifest,
            freeze2_sha256=args.freeze2_sha256,
            evidence_index=args.evidence_index,
            evidence_index_sha256=args.evidence_index_sha256,
            allowlist_json=args.allowlist_json,
            allowlist_json_sha256=args.allowlist_json_sha256,
            allowlist_md=args.allowlist_md,
            allowlist_md_sha256=args.allowlist_md_sha256,
        )
        print(json.dumps({
            "status": finalized["finalization"]["status"],
            "issue_count": finalized["finalization"]["issue_count"],
            "output": output.relative_to(ROOT).as_posix() if output.is_relative_to(ROOT) else str(output),
            "github_write_performed": False,
        }, ensure_ascii=False, indent=2))
        return 0
    plan_payload = load_plan_payload(plan_path)
    rows = load_rows(plan_path)
    snapshot = load_safe_snapshot(snapshot_path)
    if not (args.run_id and args.evidence_bundle and args.commit):
        raise SystemExit("--run-id/--evidence-bundle/--commit are required for marker creation")
    marker = (
        _validate_apply_inputs(rows, args.run_id, args.evidence_bundle, args.commit)
        if args.apply
        else marker_for(args.run_id, _evidence_token(args.evidence_bundle), args.commit)
    )
    publication_context = None
    if args.apply:
        required = {
            "--pull-request-url": args.pull_request_url,
            "--plan-commit-b": args.plan_commit_b,
            "--plan-sha256": args.plan_sha256,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise SystemExit("normal --apply requires " + ", ".join(missing))
        commit_a, plan_pr = _final_plan_publication_refs(plan_payload)
        if args.commit.lower() != commit_a or args.pull_request_url != plan_pr:
            raise SystemExit("--commit/--pull-request-url must exactly match FINAL_FROZEN plan references")
        publication_context = verify_publication_context(
            plan_payload,
            plan_path=plan_path.relative_to(ROOT).as_posix() if plan_path.is_relative_to(ROOT) else str(plan_path),
            plan_sha256=args.plan_sha256,
            plan_commit_b=args.plan_commit_b,
            repo=args.repo,
            branch=args.publication_branch,
        )
    result = (
        apply_updates(
            rows,
            snapshot,
            marker,
            repo=args.repo,
            final_plan=plan_payload,
            publication_context=publication_context,
            publisher_run_id=args.run_id,
        )
        if args.apply
        else dry_run(rows, snapshot, marker)
    )
    if args.readback:
        output = args.readback if args.readback.is_absolute() else ROOT / args.readback
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
