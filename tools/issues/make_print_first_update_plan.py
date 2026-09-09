#!/usr/bin/env python3
"""GitHub Issue 追記案を、各課題の受入条件から再生成する。

このツールは GitHub へ書き込まない。既存の読み取り候補と、ローカルの
``audit_plan_data.REVIEW`` を照合し、#3--#109 の各行に固有の受入条件・根拠・
候補証拠を付けた append-only 更新案を保存する。Issue 本文は入力としても出力
としても保存しない。
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import argparse
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / "docs/audits/20260905-round2/github-issues-refresh-20260906.json"
MARKDOWN_PATH = ROOT / "docs/audits/20260905-round2/github-issues-refresh-20260906.md"
MANIFEST_PATH = ROOT / "docs/print-first-manifest.json"
ORIENTATION_PATH = ROOT / "docs/print-first-orientation-manifest.json"
XIAO_PATH = ROOT / "docs/audits/20260905-round2/xiao-retention-plan.json"
CONFIG_PATH = ROOT / "hardware/src/config.py"
SAFE_SNAPSHOT_PATH = ROOT / "docs/audits/20260905-round2/github-safe-snapshot-20260906.json"
RENDER_META_PATH = ROOT / "outputs/print-first-20260905/render-freeze2-candidate/assembly-preview.json"
TRACE_PATH = ROOT / (
    "outputs/print-first-20260905/final-simulation-native-trace-freeze2-candidate/"
    "native-trace/final_stand_pf1-native-trace.json"
)
sys.path.insert(0, str(ROOT / "tools/issues"))
from issue_publication_data import EXTRA_ISSUES  # type: ignore  # noqa: E402
from fetch_public_snapshot import validate_safe_snapshot  # type: ignore  # noqa: E402

EXTRA_BY_NUMBER = {int(row["issue_number"]): row for row in EXTRA_ISSUES}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def load_review() -> dict[str, dict[str, Any]]:
    sys.path.insert(0, str(ROOT / "tools/issues"))
    from audit_plan_data import REVIEW  # type: ignore

    return REVIEW


def live_snapshots(snapshot_path: Path = SAFE_SNAPSHOT_PATH) -> dict[str, Any]:
    """安全スナップショットからメタデータだけを集計する。

    スナップショットは ``fetch_public_snapshot.py`` が生成し、Issue本文と
    コメント本文を保存しない。Projectは現在96項目でも、追加後の107項目でも
    読めるようにする。
    """
    if not snapshot_path.is_absolute():
        snapshot_path = ROOT / snapshot_path
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    # Validate the complete safe snapshot before inspecting any derived field.
    # This prevents a caller from supplying a partial/unknown-field object that
    # happens to contain plausible Issue counts.
    validate_safe_snapshot(snapshot)
    rows = snapshot.get("issues")
    if not isinstance(rows, list) or len(rows) != 107:
        raise ValueError("Issue metadata snapshot must contain 107 rows")
    if any("body" in row for row in rows):
        raise ValueError("Issue snapshot unexpectedly contains raw body")
    numbers = [int(row["number"]) for row in rows]
    if numbers != list(range(3, 110)):
        raise ValueError("Issue metadata snapshot must cover #3--#109")
    state_counts: dict[str, int] = {}
    for row in rows:
        state = str(row.get("state", "UNKNOWN"))
        state_counts[state] = state_counts.get(state, 0) + 1

    project = snapshot.get("project") or {}
    items = project.get("items") or []
    if not isinstance(items, list):
        raise ValueError("Project item snapshot must contain an items array")
    status_counts: dict[str, int] = {}
    lane_counts: dict[str, int] = {}
    number_status: dict[int, str] = {}
    number_lane: dict[int, str] = {}
    item_ids: dict[int, str] = {}
    for item in items:
        number = item.get("issue_number")
        if not isinstance(number, int):
            continue
        if number in item_ids:
            raise ValueError(f"Project item snapshot duplicates #{number}")
        item_ids[number] = str(item.get("id"))
        status = (item.get("status") or {}).get("name")
        lane = (item.get("lane") or {}).get("name")
        if isinstance(status, str):
            number_status[number] = status
            status_counts[status] = status_counts.get(status, 0) + 1
        if isinstance(lane, str):
            number_lane[number] = lane
            lane_counts[lane] = lane_counts.get(lane, 0) + 1

    return {
        "metadata_only": True,
        "snapshot_schema_version": snapshot.get("schema_version"),
        "issue_metadata": rows,
        "issue_count": len(rows),
        "issue_number_range": [3, 109],
        "issue_state_counts": dict(sorted(state_counts.items())),
        "closed_issue_numbers": sorted(int(row["number"]) for row in rows if row.get("state") == "CLOSED"),
        "issues_sha256": sha256(snapshot_path),
        "project_number": project.get("number", 2),
        "project_item_count": len(items),
        "project_status_counts": dict(sorted(status_counts.items())),
        "project_lane_counts": dict(sorted(lane_counts.items())),
        "project_items_sha256": sha256(snapshot_path),
        "project_sha256": sha256(snapshot_path),
        "source_files": {"safe_snapshot": snapshot_path.name},
        "project_number_status": number_status,
        "project_number_lane": number_lane,
        "project_item_ids": item_ids,
        "comment_count": sum(len(value) for value in (snapshot.get("comments") or {}).values()),
    }


def evidence_bundle(manifest: dict[str, Any], orientation: dict[str, Any], xiao: dict[str, Any]) -> dict[str, Any]:
    layer = manifest["production_quantity_layers"]["machine_design_required"]
    conditional = manifest["production_quantity_layers"]["conditional_new_shin_shell"]
    release_layers = manifest["production_quantity_layers"].get("release_layers")
    if not isinstance(release_layers, dict):
        raise ValueError("print-first manifest is missing derived A/B/C release layers")
    bundle: dict[str, Any] = {
        "status": "CANDIDATE_EVIDENCE_PENDING_FINAL_FREEZE2",
        "config": {"path": rel(CONFIG_PATH), "sha256": sha256(CONFIG_PATH)},
        "print_first_manifest": {
            "path": rel(MANIFEST_PATH),
            "sha256": sha256(MANIFEST_PATH),
            "status": manifest.get("status"),
            "design_required_quantity": layer["total"],
            "initial_prototype_quantity": layer["prototype_quantity"]["total"],
            "remaining_quantity": layer["remaining_after_prototype"]["total"],
            "conditional_new_shin_shell_quantity": conditional["quantity"],
            "conditional_machine_maximum_quantity": conditional["maximum_machine_total"],
        },
        "print_release_layers": release_layers,
        "orientation_manifest": {
            "path": rel(ORIENTATION_PATH),
            "sha256": sha256(ORIENTATION_PATH),
            "status": orientation.get("status"),
            "source_hash_mismatch_count": orientation.get("validation", {}).get("source_hash_mismatch_count"),
            "fatal_validation_failure_count": orientation.get("validation", {}).get("fatal_validation_failure_count"),
            "counterbore_actual_mesh_status": "ACTUAL_MESH_COUNTERSINK_SIDE_CONFIRMED_LOCAL_ONLY",
            "standard_cap_rotation": "X+90",
            "mirror_cap_rotation": "X-90",
        },
        "xiao_retention_plan": {
            "path": rel(XIAO_PATH),
            "sha256": sha256(XIAO_PATH),
            "status": xiao.get("status"),
            "holder_roundtrip": "watertight/1-solid/board-occupancy intersection 0 in candidate plan",
            "camera_child_lens_fixed": True,
            "board_occupancy_floor_ribs_holder_union_plus_z_mm": 4.0,
            "fpc_camera_end_fixed_board_connector_plus_z_mm": 4.0,
            "fpc_free_length_mm": 9.2,
        },
        "renderer_contract": {
            "renderer": "tools/render_print_first.py",
            "trace_path": rel(TRACE_PATH) if TRACE_PATH.exists() else None,
            "trace_sha256": sha256(TRACE_PATH) if TRACE_PATH.exists() else None,
            "render_metadata_path": rel(RENDER_META_PATH) if RENDER_META_PATH.exists() else None,
            "render_metadata_sha256": sha256(RENDER_META_PATH) if RENDER_META_PATH.exists() else None,
            "stale_trace_negative_test": "EXPECTED_REJECTION_SOURCE_CONFIG_MISMATCH",
        },
        "feet_gate": {
            "status": "CANDIDATE_CONFIG_FRESHNESS_PENDING_SOURCE_REGENERATION",
            "reason": "現候補の入力・設定鮮度と最終simulationが未確定。唯一入口後の足検査実結果へ置き換えるまで印刷可数を解放しない",
            "new_printable_quantity": 0,
        },
    }
    return bundle


def acceptance_for_row(row: dict[str, Any], review: dict[str, dict[str, Any]]) -> dict[str, Any]:
    number = int(row["number"])
    if number in EXTRA_BY_NUMBER:
        item = EXTRA_BY_NUMBER[number]
        return {
            "condition": item["acceptance_condition"],
            "next_step": item["next_step"],
            "evidence": item["evidence"],
            "source": "tools/issues/issue_publication_data.py:EXTRA_ISSUES",
        }
    key = row.get("key")
    if key and key in review:
        item = review[key]
        return {
            "condition": item["completion"],
            "next_step": item["next_step"],
            "evidence": [f"docs/audits/20260905-round2/{item['evidence']}" if not str(item["evidence"]).startswith("docs/") else item["evidence"]],
            "source": "tools/issues/audit_plan_data.py:REVIEW",
        }
    raise ValueError(f"no acceptance condition for issue #{number}")


def sentence(value: Any) -> str:
    """末尾の句点を一度取り、生成文で句点を二重にしない。"""
    cleaned = str(value).strip().rstrip("。.")
    cleaned = re.sub(r"。{2,}", "。", cleaned)
    cleaned = re.sub(r"\.{2,}", ".", cleaned)
    legacy_privacy = "無関係な" + "履歴/注文番号/住所" + "を公開しない"
    cleaned = cleaned.replace(legacy_privacy, "無関係な外部記録や個人情報を公開しない")
    return cleaned


def candidate_text(value: Any) -> str:
    """候補の局所判定を、完成品の合格と読めない表現へ正規化する。"""
    cleaned = re.sub(r"局所(?i:pass)", "局所判定", sentence(value))
    return cleaned


def currentize_issue_text(value: Any) -> Any:
    """旧96件の定型文を、今回の#3--#109公開範囲へ更新する。"""
    if isinstance(value, str):
        replacements = (
            ("購入履歴", "外部記録"),
            ("購入記録", "外部記録"),
            ("注文/配達", "現物確認"),
            ("購入・配達確認", "現物確認"),
            ("配達日表示が無く現物未確認", "現物確認前"),
            ("配達", "現物確認"),
            ("注文情報", "外部記録"),
            ("注文", "外部記録"),
            ("購入個体", "対象個体"),
            ("購入脚", "対象脚"),
            ("96件", "96項目"),
            ("全96本文", "#3〜#109の107件"),
            ("全96課題", "#3〜#109の107課題"),
            ("既存96件", "既存Issue"),
            ("監査時96課題", "監査時の既存Issue"),
            ("96キー", "107キー"),
            ("96課題", "107課題"),
        )
        for old, new in replacements:
            value = value.replace(old, new)
        return value
    if isinstance(value, dict):
        return {key: currentize_issue_text(child) for key, child in value.items()}
    if isinstance(value, list):
        return [currentize_issue_text(child) for child in value]
    return value


ISSUE_CANDIDATE_RESULTS = {
    26: (
        "今回の候補では `pf_head_top_clearanced.stl` を `Head_Top_Eyecut#single` の置換として採用し、"
        "body設計必要数へ1個を含めた。最終採用はfreeze2と頭内の実物開閉・支持確認で確定する"
    ),
    90: (
        "今回の候補ではHeadTop・ポッド梁・ケースのモデル上の干渉を解消する候補形状を再生成し、"
        "XIAOのboard occupancy/floor/rib/holder unionだけへ+Z4を適用しcamera child/lensは固定した。"
        "LD-220MGの現物寸法、カメラrevision、FPC曲げ、実取付は未確認であり、モデル合格を実機合格へ読み替えない"
    ),
}


def update_report(report: dict[str, Any], live: dict[str, Any], review: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rows = report["all_issue_append_only_update_plan"]
    if len(rows) != 107 or [r["number"] for r in rows] != list(range(3, 110)):
        raise ValueError("report must contain ordered issue rows #3-#109")

    # Regenerate stale wording from the legacy 96-row plan without touching
    # Issue state/body/title/labels on GitHub.
    for key in tuple(report):
        if key != "all_issue_append_only_update_plan":
            report[key] = currentize_issue_text(report[key])
    for index, row in enumerate(rows):
        rows[index] = currentize_issue_text(row)

    number_status = live.pop("project_number_status")
    number_lane = live.pop("project_number_lane", {})
    project_item_ids = live.pop("project_item_ids", {})
    project_numbers = set(project_item_ids)
    bundle = evidence_bundle(
        json.loads(MANIFEST_PATH.read_text(encoding="utf-8")),
        json.loads(ORIENTATION_PATH.read_text(encoding="utf-8")),
        json.loads(XIAO_PATH.read_text(encoding="utf-8")),
    )

    conditions: list[str] = []
    for row in rows:
        number = int(row["number"])
        if number in EXTRA_BY_NUMBER:
            row["key"] = EXTRA_BY_NUMBER[number]["key"]
        acceptance = acceptance_for_row(row, review)
        condition = currentize_issue_text(sentence(acceptance["condition"]))
        next_step = currentize_issue_text(sentence(acceptance["next_step"]))
        focus = currentize_issue_text(candidate_text(row["issue_specific_focus"]))
        conditions.append(condition)
        row["issue_specific_focus"] = focus
        row["project_status_readback"] = number_status.get(number)
        row["project_lane_readback"] = number_lane.get(number)
        row["project_item_id_readback"] = project_item_ids.get(number)
        row["project_item_present_in_snapshot"] = number in number_status
        row["acceptance_condition"] = condition
        row["acceptance_next_step"] = next_step
        row["acceptance_evidence_refs"] = acceptance["evidence"]
        row["acceptance_source"] = acceptance["source"]
        row["candidate_evidence_status"] = bundle["status"]
        row["candidate_current_result"] = (
            candidate_text(ISSUE_CANDIDATE_RESULTS[number])
            if number in ISSUE_CANDIDATE_RESULTS else None
        )
        # The per-Issue focus and acceptance condition are deliberately repeated
        # in the proposed text so that an external append remains issue-specific.
        row["append_only_update_candidate"] = (
            f"受入条件: {condition}。"
            f"次作業: {next_step}。"
            f"この課題の今回の焦点は「{focus}」。"
            f"根拠候補: {' / '.join(acceptance['evidence'])}。"
            + (f"候補結果: {candidate_text(ISSUE_CANDIDATE_RESULTS[number])}。" if number in ISSUE_CANDIDATE_RESULTS else "")
        )

    if len(set(conditions)) != 107:
        raise ValueError("acceptance conditions must be unique for all 107 issues")

    # Current metadata is refreshed from the latest body-free snapshot.
    issue_snapshot = live["issue_metadata"]
    issue_by_number = {int(row["number"]): row for row in issue_snapshot}
    if set(issue_by_number) != set(range(3, 110)):
        raise ValueError("live Issue metadata does not cover #3-#109")
    for row in rows:
        live_row = issue_by_number[int(row["number"])]
        for dest, source in (("state", "state"), ("title", "title"), ("updated_at", "updatedAt"), ("url", "url")):
            row[dest] = live_row[source]
        row["labels"] = [
            label if isinstance(label, str) else label.get("name")
            for label in live_row.get("labels", [])
            if (isinstance(label, str) and label) or (isinstance(label, dict) and label.get("name"))
        ]
        row["raw_body_included"] = False

    report["issues"].update(
        {
            "count": live["issue_count"],
            "number_range": live["issue_number_range"],
            "state_counts": live["issue_state_counts"],
            "closed_issue_numbers": live["closed_issue_numbers"],
        }
    )
    report["project"].update(
        {
            "item_count": live["project_item_count"],
            "status_counts": live["project_status_counts"],
            "lane_counts": live.get("project_lane_counts", {}),
            "all_issue_snapshot_coverage": live["project_item_count"],
            "new_issue_numbers_missing_from_project_snapshot": sorted(
                number for number in range(3, 110) if number not in project_numbers
            ),
        }
    )

    report["checked_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    report["read_only_refetch"] = live
    report["candidate_evidence_bundle"] = bundle
    report["final_update_template"] = {
        "status": "PLACEHOLDER_UNFILLED_UNTIL_FINAL_FREEZE2",
        "required_commit_sha": "<FINAL_COMMIT_SHA>",
        "required_pull_request_url": "<DRAFT_PR_URL>",
        "required_freeze2_manifest": {
            "path": "<FINAL_FREEZE2_MANIFEST_PATH>",
            "sha256": "<FINAL_FREEZE2_MANIFEST_SHA256>",
        },
        "required_evidence_index": {
            "path": "<FINAL_EVIDENCE_INDEX_PATH>",
            "sha256": "<FINAL_EVIDENCE_INDEX_SHA256>",
        },
        "required_publication_bundle": {
            "allowlist_json": {
                "path": "docs/audits/20260905-round2/print-first-publication-allowlist-20260906.json",
                "sha256": "<FINAL_ALLOWLIST_JSON_SHA256>",
            },
            "allowlist_md": {
                "path": "docs/audits/20260905-round2/print-first-publication-allowlist-20260906.md",
                "sha256": "<FINAL_ALLOWLIST_MD_SHA256>",
            },
            "publication_selected_paths": "<FINAL_PUBLICATION_SELECTED_PATHS>",
        },
        "rule": "各Issueへの外部追記は、上記の最終commit・draft PR・freeze2台帳・根拠索引を実値へ置換し、該当Issue固有の受入条件と残る実機条件を記載してから行う。",
    }
    report["acceptance_plan_audit"] = {
        "issue_rows": len(rows),
        "acceptance_condition_count": len(conditions),
        "unique_acceptance_condition_count": len(set(conditions)),
        "issue_specific_acceptance_conditions": True,
        "common_template_suffix_removed": True,
        "raw_issue_bodies_output": False,
    }
    report["append_only_update_plan"][-1] = (
        "全107件にIssueごとの受入条件・次作業・根拠参照・候補証拠SHAを付けた追記案を保存した。"
        "候補証拠は最終freeze2後に同一SHA集合で確定し、最終commit・draft PR・根拠索引を各Issueへ記載してから外部更新する。"
    )
    report["all_issue_coverage"].update(
        {
            "acceptance_condition_count": len(conditions),
            "unique_acceptance_condition_count": len(set(conditions)),
            "issue_specific_acceptance_conditions": True,
            "common_template_suffix_removed": True,
            "latest_read_only_refetch": report["checked_at"],
        }
    )
    report["publication_gate"]["external_issue_update_performed"] = False
    report["publication_gate"]["external_project_update_performed"] = False
    report["publication_gate"]["root_review_required"] = True
    report["publication_gate"]["mechanical_freeze2_required"] = True
    report["publication_gate"]["publication_ready"] = False
    report["public_safety"].update(
        {
            "raw_issue_bodies_included": False,
            "read_only_metadata_only_refetch": True,
            "candidate_evidence_is_not_final": True,
        }
    )
    serialized = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    findings: dict[str, list[str]] = {}
    for token, flags in (
        (r"/Users/", 0),  # source-policy-literal: update-plan-path-pattern; keep GitHub users URLs out
        (r"file://", re.IGNORECASE),  # source-policy-literal: update-plan-path-pattern
        ("native_binary", re.IGNORECASE),
        ("Amazon注文番号", 0),
        ("郵便番号", 0),
    ):
        if re.search(token, serialized, flags):
            findings.setdefault("github-issues-refresh-20260906.json", []).append(token)
    report["content_scan"] = {
        "findings_by_path": findings,
        "status": "PASS_NO_VALUE_LEAK_FINDINGS" if not findings else "FAIL_VALUE_LEAK_FINDINGS",
        "raw_bodies_included": False,
    }
    if findings:
        raise ValueError(f"public safety scan failed: {findings}")
    return report


def markdown(report: dict[str, Any]) -> str:
    issues = report["issues"]
    project = report["project"]
    bundle = report["candidate_evidence_bundle"]
    audit = report["acceptance_plan_audit"]
    layer = bundle["print_first_manifest"]
    release = bundle["print_release_layers"]
    release_a = release["A_minimum_fit_prototype"]
    release_b = release["B_remaining_after_prototype"]
    release_c = release["C_conditional_new_shin_shell"]
    release_invariant = release["invariant"]
    return f"""# GitHub Issue / Project 最新確認と追記案（2026-09-06）

**状態: `REVIEW_REQUIRED_APPEND_ONLY_NO_EXTERNAL_UPDATE`。** `gh` の読み取り再取得とローカル計画生成だけを行い、commit/push/Issue/Project外部更新は行っていない。各行は候補案で、最終freeze2根拠と最終commit/PRの代用ではない。

## 最新読み取り

- Issueは **{issues['count']}件（#{issues['number_range'][0]}〜#{issues['number_range'][1]}）**、OPEN **{issues['state_counts']['OPEN']}件**、CLOSED **{issues['state_counts']['CLOSED']}件（#81/#82/#83/#84/#93）**。Closedは再openしない。
- Project #{project['number']}「{project['title']}」は現在 **{project['item_count']}項目**。Status読戻しは Blocked {project['status_counts']['Blocked']} / Done {project['status_counts']['Done']} / In Progress {project['status_counts']['In Progress']} / Ready {project['status_counts']['Ready']} / Todo {project['status_counts']['Todo']}。#99〜#109の11件は未掲載。
- 現在のProject掲載項目の正式依存173辺（公開後履歴156辺）を保持。新規本文に含まれる関連番号14件は候補として記録し、正式依存辺へは追加していない。
- 今回の読み取りはメタデータだけを再取得した。Issue本文・トークン・外部記録は候補記録へ保存していない。

## 全107件の個別追記案

JSONの `all_issue_append_only_update_plan` に #3〜#109 の107行を保存し、各行へ固有の `acceptance_condition`、`acceptance_next_step`、`acceptance_evidence_refs`、候補証拠束の状態を付けた。受入条件は **{audit['unique_acceptance_condition_count']}件すべて固有**で、共通定型文だけの追記案ではない。

外部反映時は、既存Issueの本文・タイトル・状態・担当・コメント・ラベルを保持し、各行の受入条件に沿った一度の追記だけを行う。#99〜#109は個別に追加し、Project #{project['number']}へ全107件を掲載して各Status・分類・依存をreadbackする。

外部追記の共通前提はJSONの `final_update_template` に未記入欄として保存した。最終commit SHA、draft PR URL、最終freeze2台帳SHA、根拠索引SHAが揃うまで、107行の候補文をそのままコメントへ使用しない。

## 印刷優先設計の候補証拠

- 現行assemblyからの設計必要数は **{layer['design_required_quantity']}**、初回試作内数 **{layer['initial_prototype_quantity']}**、残り **{layer['remaining_quantity']}**。条件付き新規脛殻4を含む最大は **{layer['conditional_machine_maximum_quantity']}**。既存脛殻4の局所加工と新規4は択一で、LD治具3種は本番へ加算しない。
- 印刷解放はA/B/Cに分ける。Aは初回全体仮組み{release_a['quantity']}個（{release_a['purpose']}）で、その内側の実物適合の最小対象が1靴・1脚。BはAの合格後に進める残り{release_b['quantity']}個、Cは既存脛殻{release_c['quantity']}個の局所加工・再使用が不成立の場合だけ選ぶ新規{release_c['quantity']}個（完成機最大{release_c['maximum_machine_total']}個）です。A＋B={release_invariant['A_plus_B']}個で設計必要数{release_invariant['machine_design_required']}個に一致し、CはA/Bへ加えません。`currently_printable_quantity=0` は現時点の解放未確定を示し、追加印刷全体を不要とする値ではありません。
- `print-first-manifest.json` は `{layer['status']}`、orientationは標準cap **X+90**・鏡像cap **X-90**、実形状の皿頭側上の局所確認を記録している。XIAO候補は基板占有/床/リブ/holder unionだけ+Z4、camera child/lensは固定、FPCのカメラ端固定・基板端+Z4・余長9.2mmを記録している。
- 候補証拠束は `candidate_evidence_bundle` にファイルSHA付きで保存した。最終freeze2根拠が揃うまで、印刷可数0・実在庫・実印刷・実機合格とは区別する。外部追記に必要な最終commit/PR/根拠索引はJSONの `final_update_template` に置いた未記入欄へ記録する。

## 公開境界と実行順

- `outputs/` は再帰追加せず、最終freeze2後にallowlistが個別選択した小さい根拠だけを対象にする。衝突計算キャッシュ、実行形式・ビルド生成物、一時再現束、重複URDF mesh、製造元の原本STEP/STL、絶対パス、外部記録の個人情報は候補から除外する。
- 公開前に最終freeze2成果からallowlist・台帳・説明文を再生成し、`gh auth status`、最新Issue全107件、Project全107件、依存辺、staged file list、秘密/絶対パスをreadbackする。
- root最終レビュー後の順序は normal push → draft PR → 各Issueの個別追記 → Project #2への不足11件追加 → 全107件readback。現時点の外部更新は0件。

詳細JSON: [`github-issues-refresh-20260906.json`](github-issues-refresh-20260906.json)。
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, default=SAFE_SNAPSHOT_PATH,
                        help="fetch_public_snapshot.pyが生成した本文なしスナップショット")
    args = parser.parse_args()
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    updated = update_report(report, live_snapshots(args.snapshot), load_review())
    REPORT_PATH.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    MARKDOWN_PATH.write_text(markdown(updated), encoding="utf-8")
    print(json.dumps({
        "status": updated["publication_gate"]["publication_ready"],
        "issue_rows": updated["acceptance_plan_audit"]["issue_rows"],
        "unique_acceptance_conditions": updated["acceptance_plan_audit"]["unique_acceptance_condition_count"],
        "raw_bodies_output": updated["public_safety"]["raw_issue_bodies_included"],
        "external_updates": updated["publication_gate"]["external_issue_update_performed"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
