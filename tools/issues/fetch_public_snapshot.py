#!/usr/bin/env python3
"""GitHub Issue/Projectを本文なしの安全な再現スナップショットへ変換する。

GitHubから読み取ったIssue本文・コメント本文はハッシュ計算後に破棄する。
保存するのはIssueの公開メタデータ、コメントのID/時刻/本文SHA、Projectの
項目IDとStatus/レーンのIDだけである。書込みAPIは呼び出さない。
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
REPO = "hapx2yuki/Tachikoma"
DEFAULT_OUTPUT = ROOT / "docs/audits/20260905-round2/github-safe-snapshot-20260906.json"
PROJECT_LANE_NAME = "レーン"

SNAPSHOT_KEYS = frozenset({
    "schema_version", "generated_at", "repo", "issue_number_range",
    "issues", "comments", "project",
})
ISSUE_KEYS = frozenset({
    "number", "state", "title", "url", "labels", "updatedAt", "body_sha256",
})
COMMENT_KEYS = frozenset({"id", "created_at", "updated_at", "body_sha256"})
PROJECT_KEYS = frozenset({
    "number", "id", "title", "url", "status_field", "lane_field", "items",
})
PROJECT_FIELD_KEYS = frozenset({"id", "options"})
PROJECT_OPTION_KEYS = frozenset({"id", "name"})
PROJECT_ITEM_KEYS = frozenset({
    "id", "issue_number", "title", "url", "status", "lane",
})
PROJECT_VALUE_KEYS = frozenset({"field_id", "option_id", "name"})
SHA256_RE = re.compile(r"[0-9a-f]{64}")


def _exact_keys(value: Mapping[str, Any], expected: set[str] | frozenset[str], label: str) -> None:
    actual = set(value)
    unknown = sorted(actual - set(expected))
    missing = sorted(set(expected) - actual)
    if unknown:
        raise ValueError(f"{label} contains unknown field(s): {', '.join(unknown)}")
    if missing:
        raise ValueError(f"{label} is missing field(s): {', '.join(missing)}")


def _require_nonempty_string(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")


def _validate_issue_row(row: Any, *, repo: str, label: str) -> None:
    if not isinstance(row, Mapping):
        raise ValueError(f"{label} must be an object")
    _exact_keys(row, ISSUE_KEYS, label)
    number = row.get("number")
    if isinstance(number, bool) or not isinstance(number, int) or number < 1:
        raise ValueError(f"{label}.number must be a positive integer")
    _require_nonempty_string(row.get("state"), f"{label}.state")
    _require_nonempty_string(row.get("title"), f"{label}.title")
    expected_url = f"https://github.com/{repo}/issues/{number}"
    if row.get("url") != expected_url:
        raise ValueError(f"{label}.url is not the canonical Issue URL")
    labels = row.get("labels")
    if not isinstance(labels, list) or not all(isinstance(value, str) and value for value in labels):
        raise ValueError(f"{label}.labels must be a string array")
    if labels != sorted(set(labels)):
        raise ValueError(f"{label}.labels must be unique and sorted")
    _require_nonempty_string(row.get("updatedAt"), f"{label}.updatedAt")
    digest = row.get("body_sha256")
    if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
        raise ValueError(f"{label}.body_sha256 must be a lowercase SHA-256")


def _validate_comment_row(row: Any, *, label: str) -> None:
    if not isinstance(row, Mapping):
        raise ValueError(f"{label} must be an object")
    _exact_keys(row, COMMENT_KEYS, label)
    identifier = row.get("id")
    if isinstance(identifier, bool) or not isinstance(identifier, int) or identifier < 1:
        raise ValueError(f"{label}.id must be a positive integer")
    for key in ("created_at", "updated_at"):
        _require_nonempty_string(row.get(key), f"{label}.{key}")
    digest = row.get("body_sha256")
    if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
        raise ValueError(f"{label}.body_sha256 must be a lowercase SHA-256")


def _validate_project_field(field: Any, *, label: str) -> None:
    if not isinstance(field, Mapping):
        raise ValueError(f"{label} must be an object")
    _exact_keys(field, PROJECT_FIELD_KEYS, label)
    _require_nonempty_string(field.get("id"), f"{label}.id")
    options = field.get("options")
    if not isinstance(options, list) or not options:
        raise ValueError(f"{label}.options must be a non-empty array")
    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    for index, option in enumerate(options):
        if not isinstance(option, Mapping):
            raise ValueError(f"{label}.options[{index}] must be an object")
        _exact_keys(option, PROJECT_OPTION_KEYS, f"{label}.options[{index}]")
        option_id = option.get("id")
        option_name = option.get("name")
        _require_nonempty_string(option_id, f"{label}.options[{index}].id")
        _require_nonempty_string(option_name, f"{label}.options[{index}].name")
        if option_id in seen_ids or option_name in seen_names:
            raise ValueError(f"{label}.options contains duplicate id/name")
        seen_ids.add(option_id)
        seen_names.add(option_name)


def _validate_project_value(
    value: Any,
    *,
    label: str,
    field: Mapping[str, Any],
) -> None:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    _exact_keys(value, PROJECT_VALUE_KEYS, label)
    if value.get("field_id") != field.get("id"):
        raise ValueError(f"{label}.field_id does not match its Project field")
    option_id = value.get("option_id")
    option_name = value.get("name")
    _require_nonempty_string(option_id, f"{label}.option_id")
    _require_nonempty_string(option_name, f"{label}.name")
    options = {option.get("id"): option.get("name") for option in field.get("options", [])}
    if option_id not in options or options[option_id] != option_name:
        raise ValueError(f"{label} option id/name does not match its Project field")


def _validate_project(project: Any, *, repo: str, project_number: int = 2) -> None:
    if not isinstance(project, Mapping):
        raise ValueError("safe snapshot project must be an object")
    _exact_keys(project, PROJECT_KEYS, "project")
    number = project.get("number")
    if isinstance(number, bool) or not isinstance(number, int) or number != project_number:
        raise ValueError("safe snapshot project.number is invalid")
    for key in ("id", "title", "url"):
        _require_nonempty_string(project.get(key), f"project.{key}")
    if not project["url"].startswith("https://github.com/"):
        raise ValueError("project.url must be an HTTPS GitHub URL")
    status_field = project.get("status_field")
    lane_field = project.get("lane_field")
    _validate_project_field(status_field, label="project.status_field")
    _validate_project_field(lane_field, label="project.lane_field")
    items = project.get("items")
    if not isinstance(items, list):
        raise ValueError("project.items must be an array")
    seen_numbers: set[int] = set()
    for index, item in enumerate(items):
        label = f"project.items[{index}]"
        if not isinstance(item, Mapping):
            raise ValueError(f"{label} must be an object")
        _exact_keys(item, PROJECT_ITEM_KEYS, label)
        _require_nonempty_string(item.get("id"), f"{label}.id")
        issue_number = item.get("issue_number")
        if isinstance(issue_number, bool) or not isinstance(issue_number, int) or not 3 <= issue_number <= 109:
            raise ValueError(f"{label}.issue_number is outside #3--#109")
        if issue_number in seen_numbers:
            raise ValueError(f"project.items contains duplicate Issue #{issue_number}")
        seen_numbers.add(issue_number)
        _require_nonempty_string(item.get("title"), f"{label}.title")
        expected_url = f"https://github.com/{repo}/issues/{issue_number}"
        if item.get("url") != expected_url:
            raise ValueError(f"{label}.url is not the canonical Issue URL")
        _validate_project_value(item.get("status"), label=f"{label}.status", field=status_field)
        _validate_project_value(item.get("lane"), label=f"{label}.lane", field=lane_field)


def validate_safe_snapshot(
    snapshot: Mapping[str, Any],
    *,
    repo: str = REPO,
    project_number: int = 2,
    require_full_issue_range: bool = True,
) -> None:
    """Validate the exact public snapshot schema before any live use."""
    if not isinstance(snapshot, Mapping):
        raise ValueError("safe snapshot must be an object")
    _exact_keys(snapshot, SNAPSHOT_KEYS, "safe snapshot")
    if snapshot.get("schema_version") != "2026-09-06-safe-v1":
        raise ValueError("safe snapshot schema_version is unsupported")
    _require_nonempty_string(snapshot.get("generated_at"), "safe snapshot.generated_at")
    if snapshot.get("repo") != repo:
        raise ValueError("safe snapshot.repo does not match the requested repository")
    if snapshot.get("issue_number_range") != [3, 109]:
        raise ValueError("safe snapshot.issue_number_range must be [3, 109]")
    issues = snapshot.get("issues")
    if not isinstance(issues, list):
        raise ValueError("safe snapshot.issues must be an array")
    issue_numbers: list[int] = []
    for index, row in enumerate(issues):
        _validate_issue_row(row, repo=repo, label=f"issues[{index}]")
        issue_numbers.append(row["number"])
    if require_full_issue_range and issue_numbers != list(range(3, 110)):
        raise ValueError("safe snapshot issues must cover ordered #3--#109")
    if len(issue_numbers) != len(set(issue_numbers)):
        raise ValueError("safe snapshot issues contain duplicate numbers")
    comments = snapshot.get("comments")
    if not isinstance(comments, Mapping):
        raise ValueError("safe snapshot.comments must be an object")
    expected_comment_keys = {str(number) for number in range(3, 110)}
    if set(comments) != expected_comment_keys:
        raise ValueError("safe snapshot.comments keys must cover #3--#109 exactly")
    for key, rows in comments.items():
        if not isinstance(rows, list):
            raise ValueError(f"safe snapshot.comments[{key}] must be an array")
        previous_sort_key: tuple[str, int] | None = None
        seen_ids: set[int] = set()
        for index, row in enumerate(rows):
            _validate_comment_row(row, label=f"comments[{key}][{index}]")
            if row["id"] in seen_ids:
                raise ValueError(f"safe snapshot.comments[{key}] contains duplicate comment IDs")
            seen_ids.add(row["id"])
            sort_key = (row["created_at"], row["id"])
            if previous_sort_key is not None and sort_key < previous_sort_key:
                raise ValueError(f"safe snapshot.comments[{key}] must be sorted")
            previous_sort_key = sort_key
    _validate_project(snapshot.get("project"), repo=repo, project_number=project_number)
    assert_no_raw_body(dict(snapshot))


def run_gh(*args: str) -> str:
    result = subprocess.run(["gh", *args], cwd=ROOT, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(f"gh {' '.join(args[:3])} failed: {result.stderr.strip()[:500]}")
    return result.stdout


def run_gh_json(*args: str) -> Any:
    output = run_gh(*args)
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"gh JSON output is invalid: {exc}") from exc


def fetch_issue_metadata(repo: str = REPO) -> list[dict[str, Any]]:
    """Issue本文を取得せず、許可されたメタデータだけを返す。"""
    rows = run_gh_json(
        "issue",
        "list",
        "--repo",
        repo,
        "--state",
        "all",
        "--limit",
        "1000",
        "--json",
        # ``body`` is read only long enough to calculate its digest below;
        # assert_no_raw_body() guarantees that it never reaches the snapshot.
        "number,state,title,url,labels,updatedAt,body",
    )
    if not isinstance(rows, list):
        raise RuntimeError("gh issue list returned a non-list")
    result: list[dict[str, Any]] = []
    for row in rows:
        number = row.get("number")
        if not isinstance(number, int):
            raise RuntimeError(f"Issue number missing: {row!r}")
        body = row.get("body") if isinstance(row.get("body"), str) else ""
        result.append(
            {
                "number": number,
                "state": row.get("state"),
                "title": row.get("title"),
                "url": row.get("url"),
                "labels": sorted(
                    label.get("name")
                    for label in row.get("labels", [])
                    if label.get("name")
                ),
                "updatedAt": row.get("updatedAt"),
                "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            }
        )
    result.sort(key=lambda row: row["number"])
    return result


def _decode_paginated_json(output: str) -> list[Any]:
    decoder = json.JSONDecoder()
    result: list[Any] = []
    position = 0
    text = output.strip()
    while position < len(text):
        value, end = decoder.raw_decode(text, position)
        if isinstance(value, list):
            result.extend(value)
        else:
            result.append(value)
        position = end
        while position < len(text) and text[position].isspace():
            position += 1
    return result


def fetch_comments(number: int, repo: str = REPO) -> list[dict[str, Any]]:
    """コメント本文はSHA計算後に捨て、IDと時刻だけを返す。"""
    output = run_gh(
        "api",
        f"repos/{repo}/issues/{number}/comments?per_page=100",
        "--paginate",
    )
    raw_comments = _decode_paginated_json(output) if output.strip() else []
    comments: list[dict[str, Any]] = []
    for comment in raw_comments:
        body = comment.get("body")
        if not isinstance(body, str):
            body = ""
        comments.append(
            {
                "id": comment.get("id"),
                "created_at": comment.get("created_at"),
                "updated_at": comment.get("updated_at"),
                "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            }
        )
    comments.sort(key=lambda row: (row.get("created_at") or "", int(row.get("id") or 0)))
    return comments


def _field_key(item: dict[str, Any], name: str) -> str | None:
    if name in item:
        return name
    # gh on some macOS locales emits replacement characters for the Japanese
    # key.  The value itself remains the lane name, so match by suffix/value.
    for key, value in item.items():
        if str(key).endswith("ーン") and isinstance(value, str):
            return str(key)
    return None


def fetch_project(project_number: int = 2, owner: str = "hapx2yuki") -> dict[str, Any]:
    project = run_gh_json("project", "view", str(project_number), "--owner", owner, "--format", "json")
    fields_doc = run_gh_json(
        "project", "field-list", str(project_number), "--owner", owner, "--limit", "100", "--format", "json"
    )
    raw_items = run_gh_json(
        "project", "item-list", str(project_number), "--owner", owner, "--limit", "1000", "--format", "json"
    )
    fields = fields_doc.get("fields") or []
    status_field = next((field for field in fields if field.get("name") == "Status"), None)
    lane_field = next((field for field in fields if field.get("name") == PROJECT_LANE_NAME), None)
    if status_field is None or lane_field is None:
        raise RuntimeError("Project Status/レーン field is missing")
    status_options = {option.get("name"): option.get("id") for option in status_field.get("options", [])}
    lane_options = {option.get("name"): option.get("id") for option in lane_field.get("options", [])}

    safe_items: list[dict[str, Any]] = []
    for item in raw_items.get("items", []):
        content = item.get("content") or {}
        number = content.get("number")
        if not isinstance(number, int):
            raise RuntimeError(
                "Project item lacks a numbered Issue content; refusing to omit it from the safe snapshot"
            )
        expected_url = f"https://github.com/{REPO}/issues/{number}"
        if content.get("url") != expected_url:
            raise RuntimeError(
                f"Project item #{number} has a non-canonical content.url: {content.get('url')!r}"
            )
        status_name = item.get("status") if isinstance(item.get("status"), str) else None
        lane_key = _field_key(item, PROJECT_LANE_NAME)
        lane_name = item.get(lane_key) if lane_key else None
        safe_items.append(
            {
                "id": item.get("id"),
                "issue_number": number,
                "title": content.get("title"),
                "url": content.get("url"),
                "status": {
                    "field_id": status_field.get("id"),
                    "option_id": status_options.get(status_name),
                    "name": status_name,
                },
                "lane": {
                    "field_id": lane_field.get("id"),
                    "option_id": lane_options.get(lane_name),
                    "name": lane_name,
                },
            }
        )
    safe_items.sort(key=lambda row: row["issue_number"])
    if len({row["issue_number"] for row in safe_items}) != len(safe_items):
        raise RuntimeError("Project contains duplicate numbered Issue items")
    return {
        "number": project.get("number", project_number),
        "id": project.get("id"),
        "title": project.get("title"),
        "url": project.get("url"),
        "status_field": {
            "id": status_field.get("id"),
            "options": [{"id": option.get("id"), "name": option.get("name")} for option in status_field.get("options", [])],
        },
        "lane_field": {
            "id": lane_field.get("id"),
            "options": [{"id": option.get("id"), "name": option.get("name")} for option in lane_field.get("options", [])],
        },
        "items": safe_items,
    }


def build_snapshot(
    issues: list[dict[str, Any]] | None = None,
    *,
    project_number: int = 2,
    repo: str = REPO,
) -> dict[str, Any]:
    if issues is None:
        issues = fetch_issue_metadata(repo)
    issues = sorted(issues, key=lambda row: row["number"])
    if [row["number"] for row in issues] != list(range(3, 110)):
        raise RuntimeError("safe snapshot must cover contiguous Issue range #3--#109")
    comments = {str(row["number"]): fetch_comments(row["number"], repo) for row in issues}
    owner = repo.split("/", 1)[0]
    project = fetch_project(project_number, owner)
    snapshot = {
        "schema_version": "2026-09-06-safe-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "repo": repo,
        "issue_number_range": [3, 109],
        "issues": issues,
        "comments": comments,
        "project": project,
    }
    validate_safe_snapshot(snapshot, repo=repo, project_number=project_number)
    return snapshot


def _contains_key(value: Any, key: str = "body") -> bool:
    if isinstance(value, dict):
        return any(k == key or _contains_key(v, key) for k, v in value.items())
    if isinstance(value, list):
        return any(_contains_key(v, key) for v in value)
    return False


def assert_no_raw_body(snapshot: dict[str, Any]) -> None:
    if _contains_key(snapshot, "body"):
        raise ValueError("safe snapshot contains a raw body field")
    # Also reject raw comment-like content even if an accidental field gets a
    # different name in a future API response.
    serialized = json.dumps(snapshot, ensure_ascii=False)
    if "<!-- tachikoma" in serialized or "## 現在地" in serialized:
        raise ValueError("safe snapshot appears to contain raw Issue content")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--project", type=int, default=2)
    args = parser.parse_args()
    output = args.output if args.output.is_absolute() else ROOT / args.output
    snapshot = build_snapshot(project_number=args.project)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "PASS",
        "output": output.relative_to(ROOT).as_posix() if output.is_relative_to(ROOT) else str(output),
        "issues": len(snapshot["issues"]),
        "comments": sum(len(rows) for rows in snapshot["comments"].values()),
        "project_items": len(snapshot["project"]["items"]),
        "raw_bodies": False,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
