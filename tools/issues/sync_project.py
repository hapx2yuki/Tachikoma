#!/usr/bin/env python3
"""Project #2 の不足Issueを冪等に追加し、Status/レーンを読戻し確認する。

既存 #3--#98 のProject項目は識別子・Status・レーンをそのまま保持する。
#99--#109 は ``issue_publication_data.py`` の明示値だけを初期値として使い、
``--apply`` 後に全項目を再取得して重複・欠落・値のずれを拒否する。
既定は読取りだけで、GitHubへの書込みは ``--apply`` のときだけ行う。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import plan  # noqa: E402
from sync_github_issues import gh, load_existing  # noqa: E402


PROJECT_LANE_FIELD = "レーン"
PROJECT_STATUS_FIELD = "Status"
ISSUE_RANGE = range(3, 110)


def initial_status(spec, states):
    """既存Project項目との互換用。手動のStatusは呼出し側で保持する。"""
    if states.get(spec["key"]) == "closed":
        return "Done"
    if "type/エピック" in spec["labels"]:
        return "Todo"
    if any(states.get(key) != "closed" for key in spec["blocked_by"]):
        return "Blocked"
    return "Ready"


def status_change(current, spec, states):
    # 手動のBlocked/Todoも保持する。
    return None if current else initial_status(spec, states)


def _field(fields: Iterable[dict[str, Any]], name: str) -> dict[str, Any]:
    for field in fields:
        if field.get("name") == name:
            return field
    raise RuntimeError(f"Projectに{ name }フィールドが無い")


def _options(field: dict[str, Any]) -> dict[str, str]:
    return {str(option["name"]): option["id"] for option in field.get("options", [])}


def _lane_key(item: dict[str, Any]) -> str | None:
    """ghの端末文字コードで壊れたキーも含めてレーン列を見つける。"""
    for key in item:
        if key == PROJECT_LANE_FIELD or str(key).endswith("ーン"):
            return str(key)
    return None


def item_number(item: dict[str, Any]) -> int | None:
    content = item.get("content") or {}
    number = content.get("number")
    return int(number) if isinstance(number, int) or (isinstance(number, str) and number.isdigit()) else None


def item_url(item: dict[str, Any]) -> str | None:
    content = item.get("content") or {}
    return content.get("url") if isinstance(content.get("url"), str) else None


def _assert_canonical_issue_url(number: int, item: dict[str, Any]) -> None:
    """Reject a numbered Project card whose content is not this Issue."""
    expected = _issue_url(number)
    actual = item_url(item)
    if actual != expected:
        raise RuntimeError(
            f"Project Issue #{number} content.url is not the canonical repository URL: "
            f"{actual!r} != {expected!r}"
        )


def item_status(item: dict[str, Any]) -> str | None:
    value = item.get("status")
    return value if isinstance(value, str) else None


def item_lane(item: dict[str, Any]) -> str | None:
    key = _lane_key(item)
    value = item.get(key) if key else None
    return value if isinstance(value, str) else None


def items_by_number(snapshot: dict[str, Any]) -> dict[int, dict[str, Any]]:
    items = snapshot.get("items")
    if not isinstance(items, list):
        raise RuntimeError("Project item-listのitemsが配列でない")
    result: dict[int, dict[str, Any]] = {}
    for item in items:
        number = item_number(item)
        if number is None:
            raise RuntimeError(
                "Project item lacks a numbered Issue content; refusing to ignore a draft/PR/missing URL"
            )
        _assert_canonical_issue_url(number, item)
        if number in result:
            raise RuntimeError(f"Project内のIssue番号が重複: #{number}")
        result[number] = item
    return result


def _validate_snapshot_count(snapshot: dict[str, Any]) -> None:
    total = snapshot.get("totalCount")
    items = snapshot.get("items") or []
    if isinstance(total, int) and total > len(items):
        raise RuntimeError("Projectが1000件を超えるため、取得漏れを防いで停止")


def fetch_project(project_number: int) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    owner = plan.REPO.split("/")[0]
    project = json.loads(gh("project", "view", str(project_number), "--owner", owner, "--format", "json"))
    fields_doc = json.loads(
        gh("project", "field-list", str(project_number), "--owner", owner, "--limit", "100", "--format", "json")
    )
    snapshot = json.loads(
        gh("project", "item-list", str(project_number), "--owner", owner, "--limit", "1000", "--format", "json")
    )
    _validate_snapshot_count(snapshot)
    return project, fields_doc, snapshot


def desired_status(spec: dict[str, Any], states: dict[str, str]) -> str:
    explicit = spec.get("project_status")
    return explicit if explicit else initial_status(spec, states)


def desired_lane(spec: dict[str, Any]) -> str | None:
    value = spec.get("project_lane")
    return value if isinstance(value, str) and value else None


def _issue_url(number: int) -> str:
    return f"https://github.com/{plan.REPO}/issues/{number}"


def _edit_item(
    project_id: str,
    item: dict[str, Any],
    status_field: dict[str, Any],
    lane_field: dict[str, Any],
    status: str,
    lane: str | None,
    status_options: dict[str, str],
    lane_options: dict[str, str],
) -> None:
    item_id = item.get("id")
    if not item_id:
        raise RuntimeError("Project item IDが無い")
    status_id = status_options.get(status)
    if status_id is None:
        raise RuntimeError(f"Statusの選択肢が無い: {status}")
    gh(
        "project",
        "item-edit",
        "--project-id",
        project_id,
        "--id",
        item_id,
        "--field-id",
        status_field["id"],
        "--single-select-option-id",
        status_id,
    )
    if lane is not None:
        lane_id = lane_options.get(lane)
        if lane_id is None:
            raise RuntimeError(f"レーンの選択肢が無い: {lane}")
        gh(
            "project",
            "item-edit",
            "--project-id",
            project_id,
            "--id",
            item_id,
            "--field-id",
            lane_field["id"],
            "--single-select-option-id",
            lane_id,
        )


def verify_project_readback(
    before_snapshot: dict[str, Any],
    after_snapshot: dict[str, Any],
    specs: list[dict[str, Any]] | None = None,
    *,
    require_full_range: bool = True,
) -> dict[str, Any]:
    """Projectの完全性と、既存項目の不変条件を検証する。"""
    before = items_by_number(before_snapshot)
    after = items_by_number(after_snapshot)
    target_specs = plan.ISSUES if specs is None else specs
    target_by_number = {int(spec.get("issue_number", -1)): spec for spec in target_specs if spec.get("issue_number")}
    if require_full_range and set(after) != set(ISSUE_RANGE):
        missing = sorted(set(ISSUE_RANGE) - set(after))
        extra = sorted(set(after) - set(ISSUE_RANGE))
        raise RuntimeError(f"Project readback範囲不一致: missing={missing}, extra={extra}")
    numbered_count = sum(1 for item in (after_snapshot.get("items") or []) if item_number(item) is not None)
    duplicates = len(after) != numbered_count
    # Draft/non-Issue entries are allowed; duplicates among numbered Issues are
    # rejected by items_by_number before reaching here.
    if duplicates:
        raise RuntimeError("Project readbackに同じIssueの重複項目がある")

    preserved = 0
    changed_existing: list[int] = []
    for number, old in before.items():
        new = after.get(number)
        if new is None:
            raise RuntimeError(f"既存Project項目が消えた: #{number}")
        old_identity = (old.get("id"), item_status(old), item_lane(old))
        new_identity = (new.get("id"), item_status(new), item_lane(new))
        # Only the original #3--#98 entries are immutable.  #99--#109 may be
        # converged to their explicit metadata values on a rerun.
        if number < 99 and old_identity != new_identity:
            changed_existing.append(number)
        else:
            preserved += 1
    if changed_existing:
        raise RuntimeError(f"既存Project項目を変更: {changed_existing}")

    checked_new: list[int] = []
    for number, spec in target_by_number.items():
        if number < 99 or number not in after:
            continue
        expected_status = desired_status(spec, {})
        expected_lane = desired_lane(spec)
        row = after[number]
        if expected_status and item_status(row) != expected_status:
            raise RuntimeError(
                f"Project Status不一致 #{number}: {item_status(row)!r} != {expected_status!r}"
            )
        if expected_lane and item_lane(row) != expected_lane:
            raise RuntimeError(
                f"Project レーン不一致 #{number}: {item_lane(row)!r} != {expected_lane!r}"
            )
        checked_new.append(number)
    return {
        "status": "PASS",
        "before_issue_count": len(before),
        "after_issue_count": len(after),
        "preserved_existing_items": preserved,
        "checked_new_items": sorted(checked_new),
        "issue_number_range": [min(after), max(after)] if after else [],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="不足Issueの追加と#99--#109の指定値設定を実行")
    mode.add_argument("--dry-run", action="store_true", help="読取りと差分表示だけ（既定）")
    parser.add_argument("--project", type=int, default=2, help="既存Project番号。新規作成は行わない")
    parser.add_argument("--keys", nargs="+", help="対象Issueキー。省略時は全107件")
    args = parser.parse_args()
    specs = list(plan.ISSUES)
    if args.keys:
        unknown = set(args.keys) - {spec["key"] for spec in specs}
        if unknown:
            parser.error(f"不明なキー: {sorted(unknown)}")
        specs = [spec for spec in specs if spec["key"] in args.keys]

    owner = plan.REPO.split("/")[0]
    project, fields_doc, before_snapshot = fetch_project(args.project)
    fields = fields_doc.get("fields") or []
    status_field = _field(fields, PROJECT_STATUS_FIELD)
    lane_field = _field(fields, PROJECT_LANE_FIELD)
    status_options = _options(status_field)
    lane_options = _options(lane_field)
    project_items = items_by_number(before_snapshot)
    existing = load_existing()
    states = {key: value["state"] for key, value in existing.items()}

    changes = 0
    planned_new: list[int] = []
    for spec in specs:
        key = spec["key"]
        issue = existing.get(key)
        if issue is None:
            print(f"SKIP {key}: GitHub Issue未作成")
            continue
        number = int(issue["number"])
        item = project_items.get(number)
        desired_s = desired_status(spec, states)
        desired_l = desired_lane(spec)
        if item is None:
            if number < 99:
                raise RuntimeError(f"既存#3--#98のProject項目が欠落: {key} #{number}; 自動追加せず停止")
            if desired_s not in status_options:
                raise RuntimeError(f"Statusの選択肢が無い: {desired_s}")
            if desired_l not in lane_options:
                raise RuntimeError(f"レーンの選択肢が無い: {desired_l}")
            print(f"ADD {key} #{number} Status={desired_s} レーン={desired_l}")
            changes += 1
            planned_new.append(number)
            if args.apply:
                gh("project", "item-add", str(args.project), "--owner", owner, "--url", _issue_url(number))
                # item-add may not return a stable shape across gh versions;
                # identify the new item from a fresh readback.
                _, _, refreshed = fetch_project(args.project)
                project_items = items_by_number(refreshed)
                item = project_items.get(number)
                if item is None:
                    raise RuntimeError(f"追加後のProject項目を読めない: #{number}")
                _edit_item(project["id"], item, status_field, lane_field, desired_s, desired_l,
                           status_options, lane_options)
            continue

        # Existing canonical items are immutable.  Extra items may be repaired
        # to their explicit initial Status/lane so reruns converge.
        current_s, current_l = item_status(item), item_lane(item)
        if number < 99:
            print(f"KEEP {key} #{number} Status={current_s or '未設定'} レーン={current_l or '未設定'}")
            continue
        if current_s == desired_s and (desired_l is None or current_l == desired_l):
            print(f"KEEP {key} #{number} Status={current_s} レーン={current_l or '未設定'}")
            continue
        print(f"UPDATE {key} #{number} Status={current_s!r}->{desired_s} レーン={current_l!r}->{desired_l}")
        changes += 1
        if args.apply:
            _edit_item(project["id"], item, status_field, lane_field, desired_s, desired_l,
                       status_options, lane_options)

    if args.apply:
        _, _, after_snapshot = fetch_project(args.project)
        result = verify_project_readback(
            before_snapshot,
            after_snapshot,
            specs,
            require_full_range=not bool(args.keys),
        )
        print(json.dumps({"mode": "APPLIED", "changes": changes, "readback": result}, ensure_ascii=False))
    else:
        print(json.dumps({"mode": "DRY RUN", "changes": changes, "would_add": planned_new,
                          "existing_issue_count": len(project_items)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
