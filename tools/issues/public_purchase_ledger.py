#!/usr/bin/env python3
"""購入台帳から公開可能な設計・確認情報だけを生成する。

公開版は品目の名前、仕様、必要数、現行4区分、判断理由、次の作業、
現物確認境界を扱う。外部の購入記録、商品識別子、受領状態、注文情報は
入力の一時処理でも公開出力へ持ち込まない。元データは呼出し側で
``outputs/private`` へ退避してから、このモジュールで公開版を再生成する。
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
PUBLIC_JSON = ROOT / "docs/additional-purchases.json"
PUBLIC_MD = ROOT / "docs/additional-purchases.md"

# These are deliberately narrow.  ``購入`` remains allowed because the public
# ledger describes the zero-additional-purchase policy and required quantities.
FORBIDDEN_PUBLIC_KEYS = {
    "amazon_history_status",
    "purchase_lots",
    "recorded_purchased",
    "delivery_status",
    "inventory_lot_ids",
    "order_recommended_now",
}
FORBIDDEN_PUBLIC_PATTERNS = (
    re.compile(r"\bamazon\b", re.IGNORECASE),
    # Do not match identifiers such as ``math.asin`` in reviewed source code;
    # a product identifier is written as the standalone token ``ASIN``.
    re.compile(r"(?<![A-Za-z.])asin(?![A-Za-z])", re.IGNORECASE),
    re.compile(r"\bpurchase_lots?\b", re.IGNORECASE),
    re.compile(r"\bdelivery_status\b", re.IGNORECASE),
    re.compile(r"\brecorded_purchased\b", re.IGNORECASE),
    re.compile(r"\binventory_lot_ids\b", re.IGNORECASE),
    re.compile(r"\bamazon_history_status\b", re.IGNORECASE),
    re.compile(r"\bB0[A-Z0-9]{8}\b", re.IGNORECASE),
    re.compile(
        r"購入履歴|購入記録|購入量|注文ページ|注文番号|注文件数|注文日|注文情報|"
        r"配送状態|配達(?:記録|数量|品|表示|済)|注文済|受領状態|到着|"
        r"全\s*7\s*ページ|64\s*注文"
    ),
    re.compile(r"\b\d{3}-\d{7}-\d{7}\b"),
)

PUBLIC_PLAN_STATUSES = (
    "print_first_adopted",
    "borrow_or_verify",
    "deferred",
    "not_required",
)

PUBLIC_TOP_LEVEL_KEYS = frozenset({
    "schema_version", "as_of", "title", "scope", "current_plan",
    "privacy", "rules", "sources", "items",
})
PUBLIC_PLAN_KEYS = frozenset({
    "name", "as_of", "plan_schema_version", "immediate_purchase_required",
    "immediate_purchase_item_ids", "print_first_adopted", "borrow_or_verify",
    "deferred", "not_required", "coverage", "status_definitions", "rules",
    "source_of_truth", "category_details", "cross_cutting_sources",
    "item_reason_action_audit", "allocation_update_as_of", "plan_status_by_item",
})
PUBLIC_COVERAGE_KEYS = frozenset({
    "item_count", "classified_item_count", "classified_once",
    "unclassified_item_ids", "category_counts",
})
PUBLIC_CATEGORY_DETAIL_KEYS = frozenset({"count", "reason", "source", "next_action"})
PUBLIC_CROSS_SOURCE_KEYS = frozenset({"reference", "purpose"})
PUBLIC_AUDIT_KEYS = frozenset({
    "status", "audited_item_count", "required_fields", "all_required_fields_nonempty",
    "plan_status_aggregation_matches_top_lists", "cross_item_copy_screen", "ap_027_guard",
})
PUBLIC_COPY_SCREEN_KEYS = frozenset({"status", "suspicious_item_ids", "method", "exact_copy_guard"})
PUBLIC_COPY_GUARD_KEYS = frozenset({
    "reference_item_id", "reference_pair_matches_only",
    "forbidden_servo_specific_terms_for_other_items", "status",
})
PUBLIC_AP027_GUARD_KEYS = frozenset({
    "status", "item_id", "status_required", "reason_or_action_must_mention_any",
    "reason_and_action_must_not_mention", "current_usable_quantity_must_remain",
})
PUBLIC_ITEM_KEYS = frozenset({
    "id", "anchor", "group", "name", "specification", "required_total",
    "current_usable_quantity", "current_shortage", "current_verification_boundary",
    "classification", "issue_keys", "bom_ids", "source_refs", "optional",
    "candidate_only", "issue_urls", "required_if_unavailable", "current_plan",
})
PUBLIC_REQUIRED_TOTAL_KEYS = frozenset({"quantity", "unit", "basis"})
PUBLIC_ITEM_PLAN_KEYS = frozenset({
    "status", "reason", "next_action", "immediate_purchase_required", "inventory_assertion",
})
PUBLIC_ISSUE_URL_KEYS = frozenset({
    "A-01", "A-02", "A-03", "A-04", "EL-01", "EL-02", "EL-03", "EL-04", "EL-05", "EL-06", "EL-07", "EL-08", "EL-09",
    "H-01", "H-02", "H-03", "H-04", "H-06", "H-07", "I-01", "I-04", "L-01", "L-02", "L-06", "L-07", "L-08", "L-09", "L-10", "L-11",
    "P-01", "P-02", "P-03", "P-05", "P-06", "P-07", "PR-01", "PR-02", "PR-03", "PR-04", "PR-05", "PR-06", "PR-07", "PR-08", "PR-09", "PR-11",
    "RV-06", "RV-07", "RV-09", "RV-11", "RV-13", "RV-15", "RV-16", "RV-17", "S-01", "S-02a", "S-02b", "S-02c", "S-02d", "S-03", "S-04", "S-05", "S-06", "S-07",
})
PUBLIC_SENSITIVE_VALUE_PATTERNS = {
    "absolute_posix_path": re.compile(
        # A slash after a URL scheme, another slash, or a word/drive prefix
        # is URL/repository syntax.  The remaining boundary accepts Unicode
        # path components and catches standard system roots and arbitrary
        # absolute roots.
        r"(?<![\w:/.])/(?!/)[^\s\"'<>]+"  # source-policy-literal: purchase-path-patterns
    ),
    "home_path": re.compile(r"(?<![\w])~(?:/|[^\d\s/][^\s/]*/)", re.UNICODE),  # source-policy-literal: purchase-path-patterns
    "windows_absolute_path": re.compile(r"(?<![\w])[A-Za-z]:[\\/][^\s\"'<>]+"),  # source-policy-literal: purchase-path-patterns
    "file_uri": re.compile(r"(?i)file://"),  # source-policy-literal: purchase-path-patterns
    "github_token": re.compile(r"(?:gh[pousr]_|github_pat_|sk-[A-Za-z0-9])"),
    "amazon_order_id": re.compile(r"\b\d{3}-\d{7}-\d{7}\b"),
}


def normalized_text_variants(value: str) -> tuple[str, ...]:
    """Return NFC/NFD forms so path checks cover decomposed macOS text."""
    if not isinstance(value, str):
        return ()
    variants: list[str] = []
    for form in (value, unicodedata.normalize("NFC", value), unicodedata.normalize("NFD", value)):
        if form not in variants:
            variants.append(form)
    return tuple(variants)


def find_sensitive_value_names(value: str) -> list[str]:
    """Return sensitive-value pattern names, preserving no source content."""
    findings: list[str] = []
    for name, pattern in PUBLIC_SENSITIVE_VALUE_PATTERNS.items():
        variants = normalized_text_variants(value)
        # NFD decomposes Japanese dakuten into a combining mark.  A generic
        # ``\w`` boundary in Python's regular-expression engine does not
        # consume that mark, so scanning the raw NFD form would mistake
        # ordinary prose such as ``サーボ/UBEC`` for a path.  NFC is sufficient
        # for detection because it also reconstitutes an NFD path component;
        # the non-path patterns keep the original forms below for completeness.
        if name in {"absolute_posix_path", "home_path", "windows_absolute_path", "file_uri"}:
            variants = (unicodedata.normalize("NFC", value),)
        if name == "absolute_posix_path":
            if any(
                _is_absolute_posix_match(variant, match)
                for variant in variants
                for match in pattern.finditer(variant)
            ):
                findings.append(name)
            continue
        if any(pattern.search(variant) for variant in variants):
            findings.append(name)
    return findings


def _is_absolute_posix_match(text: str, match: re.Match[str]) -> bool:
    """Distinguish a filesystem root from slash-separated prose/HTML.

    The detector intentionally errs on the side of rejecting a path-looking
    value.  A single slash before Japanese prose (``理由 / 次``) and an HTML
    closing tag are common in the public Markdown and are not absolute paths.
    Known system roots and multi-segment ASCII paths remain covered, including
    paths whose final component has no extension.
    """
    if match.start() > 0 and text[match.start() - 1] == "<":
        return False
    value = match.group(0)
    stripped = value.lstrip("/")
    if not stripped:
        return False
    first = stripped.split("/", 1)[0].rstrip(".,:;)]}>")
    if not first or first in {".", ".."}:
        return False
    common_roots = {
        "etc", "Volumes", "opt", "Library", "Users", "System",
        "Applications", "private", "tmp", "var", "home", "root",
        "usr", "bin", "sbin", "dev", "proc", "run", "workspace",
        "secret", "secrets", "password", "passwords",
    }
    if first in common_roots:
        return True
    if "/" not in stripped:
        return False
    # ASCII multi-segment roots are path-shaped even without a conventional
    # file suffix (for example /sensitive/data).  Unicode slash prose is not.
    if first.isascii() and first.replace("_", "").isalnum():
        return True
    clean_value = stripped.rstrip(".,;:)]}`'\"。、")
    return bool(re.search(r"\.[A-Za-z0-9][A-Za-z0-9_-]{0,15}$", clean_value))


def _exact_keys(value: Mapping[str, Any], allowed: set[str] | frozenset[str], label: str) -> None:
    unknown = sorted(set(value) - set(allowed))
    missing = sorted(set(allowed) - set(value))
    if unknown:
        raise ValueError(f"{label} contains unknown field(s): {', '.join(unknown)}")
    if missing:
        raise ValueError(f"{label} is missing field(s): {', '.join(missing)}")


def _require_string(value: Any, label: str) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")


def _require_string_list(value: Any, label: str) -> None:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{label} must be a string array")


def _validate_public_schema(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if not isinstance(payload, Mapping):
        raise ValueError("public purchase ledger must be an object")
    _exact_keys(payload, PUBLIC_TOP_LEVEL_KEYS, "public purchase ledger")
    for key in ("schema_version", "as_of", "title", "scope", "privacy"):
        _require_string(payload.get(key), f"$.{key}")
    _require_string_list(payload.get("rules"), "$.rules")
    _require_string_list(payload.get("sources"), "$.sources")
    items = payload.get("items")
    if not isinstance(items, list) or len(items) != 134:
        raise ValueError("public purchase ledger must contain exactly 134 items")
    plan = payload.get("current_plan")
    if not isinstance(plan, Mapping):
        raise ValueError("$.current_plan must be an object")
    _exact_keys(plan, PUBLIC_PLAN_KEYS, "$.current_plan")
    for key in ("name", "as_of", "plan_schema_version", "source_of_truth", "allocation_update_as_of"):
        _require_string(plan.get(key), f"$.current_plan.{key}")
    _require_string_list(plan.get("rules"), "$.current_plan.rules")
    for key in PUBLIC_PLAN_STATUSES:
        _require_string_list(plan.get(key), f"$.current_plan.{key}")
    if plan.get("immediate_purchase_required") != 0:
        raise ValueError("public plan immediate_purchase_required must be zero")
    if plan.get("immediate_purchase_item_ids") != []:
        raise ValueError("public plan immediate_purchase_item_ids must be empty")
    coverage = plan.get("coverage")
    if not isinstance(coverage, Mapping):
        raise ValueError("$.current_plan.coverage must be an object")
    _exact_keys(coverage, PUBLIC_COVERAGE_KEYS, "$.current_plan.coverage")
    for key in ("item_count", "classified_item_count"):
        if not isinstance(coverage.get(key), int) or isinstance(coverage.get(key), bool):
            raise ValueError(f"$.current_plan.coverage.{key} must be an integer")
    if coverage.get("item_count") != 134 or coverage.get("classified_item_count") != 134:
        raise ValueError("public plan coverage counts must equal the 134 input items")
    if coverage.get("classified_once") is not True:
        raise ValueError("public plan classified_once must be true")
    _require_string_list(coverage.get("unclassified_item_ids"), "$.current_plan.coverage.unclassified_item_ids")
    if coverage.get("unclassified_item_ids") != []:
        raise ValueError("public plan contains unclassified item IDs")
    counts = coverage.get("category_counts")
    if not isinstance(counts, Mapping):
        raise ValueError("$.current_plan.coverage.category_counts must be an object")
    _exact_keys(counts, PUBLIC_PLAN_STATUSES, "$.current_plan.coverage.category_counts")
    for key, value in counts.items():
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"$.current_plan.coverage.category_counts.{key} must be a non-negative integer")
    definitions = plan.get("status_definitions")
    if not isinstance(definitions, Mapping):
        raise ValueError("$.current_plan.status_definitions must be an object")
    _exact_keys(definitions, PUBLIC_PLAN_STATUSES, "$.current_plan.status_definitions")
    for key, value in definitions.items():
        _require_string(value, f"$.current_plan.status_definitions.{key}")
    details = plan.get("category_details")
    if not isinstance(details, Mapping):
        raise ValueError("$.current_plan.category_details must be an object")
    _exact_keys(details, PUBLIC_PLAN_STATUSES, "$.current_plan.category_details")
    for key, detail in details.items():
        if not isinstance(detail, Mapping):
            raise ValueError(f"$.current_plan.category_details.{key} must be an object")
        _exact_keys(detail, PUBLIC_CATEGORY_DETAIL_KEYS, f"$.current_plan.category_details.{key}")
        if not isinstance(detail.get("count"), int) or isinstance(detail.get("count"), bool):
            raise ValueError(f"$.current_plan.category_details.{key}.count must be an integer")
        _require_string(detail.get("reason"), f"$.current_plan.category_details.{key}.reason")
        _require_string_list(detail.get("source"), f"$.current_plan.category_details.{key}.source")
        _require_string(detail.get("next_action"), f"$.current_plan.category_details.{key}.next_action")
    cross_sources = plan.get("cross_cutting_sources")
    if not isinstance(cross_sources, list):
        raise ValueError("$.current_plan.cross_cutting_sources must be an array")
    for index, row in enumerate(cross_sources):
        if not isinstance(row, Mapping):
            raise ValueError(f"$.current_plan.cross_cutting_sources[{index}] must be an object")
        _exact_keys(row, PUBLIC_CROSS_SOURCE_KEYS, f"$.current_plan.cross_cutting_sources[{index}]")
        _require_string(row.get("reference"), f"$.current_plan.cross_cutting_sources[{index}].reference")
        _require_string(row.get("purpose"), f"$.current_plan.cross_cutting_sources[{index}].purpose")
    audit = plan.get("item_reason_action_audit")
    if not isinstance(audit, Mapping):
        raise ValueError("$.current_plan.item_reason_action_audit must be an object")
    _exact_keys(audit, PUBLIC_AUDIT_KEYS, "$.current_plan.item_reason_action_audit")
    if audit.get("status") != "PASS" or audit.get("audited_item_count") != 134:
        raise ValueError("public item audit is incomplete")
    _require_string_list(audit.get("required_fields"), "$.current_plan.item_reason_action_audit.required_fields")
    for key in ("all_required_fields_nonempty", "plan_status_aggregation_matches_top_lists"):
        if audit.get(key) is not True:
            raise ValueError(f"public item audit flag is false: {key}")
    copy_screen = audit.get("cross_item_copy_screen")
    if not isinstance(copy_screen, Mapping):
        raise ValueError("public item copy screen must be an object")
    _exact_keys(copy_screen, PUBLIC_COPY_SCREEN_KEYS, "public item copy screen")
    _require_string_list(copy_screen.get("suspicious_item_ids"), "public item copy screen.suspicious_item_ids")
    _require_string(copy_screen.get("method"), "public item copy screen.method")
    if copy_screen.get("status") != "PASS":
        raise ValueError("public item copy screen is not PASS")
    copy_guard = copy_screen.get("exact_copy_guard")
    if not isinstance(copy_guard, Mapping):
        raise ValueError("public item exact copy guard must be an object")
    _exact_keys(copy_guard, PUBLIC_COPY_GUARD_KEYS, "public item exact copy guard")
    _require_string(copy_guard.get("reference_item_id"), "public item exact copy guard.reference_item_id")
    _require_string_list(copy_guard.get("reference_pair_matches_only"), "public item exact copy guard.reference_pair_matches_only")
    _require_string_list(copy_guard.get("forbidden_servo_specific_terms_for_other_items"), "public item exact copy guard.forbidden_servo_specific_terms_for_other_items")
    if copy_guard.get("status") != "PASS":
        raise ValueError("public item exact copy guard is not PASS")
    ap_guard = audit.get("ap_027_guard")
    if not isinstance(ap_guard, Mapping):
        raise ValueError("public AP-027 guard must be an object")
    _exact_keys(ap_guard, PUBLIC_AP027_GUARD_KEYS, "public AP-027 guard")
    _require_string(ap_guard.get("item_id"), "public AP-027 guard.item_id")
    _require_string(ap_guard.get("status_required"), "public AP-027 guard.status_required")
    _require_string_list(ap_guard.get("reason_or_action_must_mention_any"), "public AP-027 guard.reason_or_action_must_mention_any")
    _require_string_list(ap_guard.get("reason_and_action_must_not_mention"), "public AP-027 guard.reason_and_action_must_not_mention")
    if ap_guard.get("status") != "PASS":
        raise ValueError("public AP-027 guard is not PASS")
    status_map = plan.get("plan_status_by_item")
    if not isinstance(status_map, Mapping):
        raise ValueError("$.current_plan.plan_status_by_item must be an object")

    item_ids: list[str] = []
    item_status_by_id: dict[str, str] = {}
    item_immediate_purchase_ids: list[str] = []
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            raise ValueError(f"$.items[{index}] must be an object")
        _exact_keys(item, PUBLIC_ITEM_KEYS, f"$.items[{index}]")
        for key in ("id", "anchor", "group", "name", "specification", "current_verification_boundary", "classification"):
            _require_string(item.get(key), f"$.items[{index}].{key}")
        item_id = item["id"]
        if item_id in item_ids:
            raise ValueError(f"duplicate public item id: {item_id}")
        item_ids.append(item_id)
        required = item.get("required_total")
        if not isinstance(required, Mapping):
            raise ValueError(f"$.items[{index}].required_total must be an object")
        _exact_keys(required, PUBLIC_REQUIRED_TOTAL_KEYS, f"$.items[{index}].required_total")
        if required.get("quantity") is not None and (
            not isinstance(required.get("quantity"), (int, float))
            or isinstance(required.get("quantity"), bool)
        ):
            raise ValueError(f"$.items[{index}].required_total.quantity must be numeric")
        _require_string(required.get("unit"), f"$.items[{index}].required_total.unit")
        _require_string(required.get("basis"), f"$.items[{index}].required_total.basis")
        for key in ("current_usable_quantity", "current_shortage", "required_if_unavailable"):
            value = item.get(key)
            if value is not None and (not isinstance(value, (int, float)) or isinstance(value, bool)):
                raise ValueError(f"$.items[{index}].{key} must be numeric or null")
        _require_string_list(item.get("issue_keys"), f"$.items[{index}].issue_keys")
        _require_string_list(item.get("bom_ids"), f"$.items[{index}].bom_ids")
        _require_string_list(item.get("source_refs"), f"$.items[{index}].source_refs")
        if not isinstance(item.get("optional"), bool) or not isinstance(item.get("candidate_only"), bool):
            raise ValueError(f"$.items[{index}] optional/candidate_only must be boolean")
        issue_urls = item.get("issue_urls")
        if not isinstance(issue_urls, Mapping):
            raise ValueError(f"$.items[{index}].issue_urls must be an object")
        if set(issue_urls) - PUBLIC_ISSUE_URL_KEYS:
            raise ValueError(f"$.items[{index}].issue_urls contains unknown field(s)")
        for issue_key, url in issue_urls.items():
            _require_string(url, f"$.items[{index}].issue_urls.{issue_key}")
            if not re.fullmatch(r"https://github\.com/hapx2yuki/Tachikoma/issues/[0-9]+", url):
                raise ValueError(f"$.items[{index}].issue_urls.{issue_key} is not a repository Issue URL")
        current = item.get("current_plan")
        if not isinstance(current, Mapping):
            raise ValueError(f"$.items[{index}].current_plan must be an object")
        _exact_keys(current, PUBLIC_ITEM_PLAN_KEYS, f"$.items[{index}].current_plan")
        if current.get("status") not in PUBLIC_PLAN_STATUSES:
            raise ValueError(f"$.items[{index}].current_plan.status is invalid")
        for key in ("reason", "next_action", "inventory_assertion"):
            _require_string(current.get(key), f"$.items[{index}].current_plan.{key}")
        if current.get("immediate_purchase_required") != 0:
            raise ValueError(f"$.items[{index}] immediate purchase must be zero")
        item_status_by_id[item_id] = current["status"]
        if current.get("immediate_purchase_required"):
            item_immediate_purchase_ids.append(item_id)
    expected_ids = set(item_ids)
    if set(status_map) != expected_ids:
        raise ValueError("plan_status_by_item keys do not match the 134 item IDs")
    if any(status_map[item_id] not in PUBLIC_PLAN_STATUSES for item_id in item_ids):
        raise ValueError("plan_status_by_item contains an invalid status")
    if dict(status_map) != item_status_by_id:
        raise ValueError("plan_status_by_item does not match every item current_plan.status")
    if plan.get("immediate_purchase_item_ids") != sorted(item_immediate_purchase_ids):
        raise ValueError(
            "immediate_purchase_item_ids does not match item current_plan values"
        )
    derived_counts = {
        status: sum(value == status for value in item_status_by_id.values())
        for status in PUBLIC_PLAN_STATUSES
    }
    if dict(counts) != derived_counts:
        raise ValueError("category_counts do not match the 134 item statuses")
    for status in PUBLIC_PLAN_STATUSES:
        listed = plan.get(status)
        if listed != sorted(set(listed)):
            raise ValueError(f"$.current_plan.{status} must be unique and sorted")
        expected_list = sorted(item_id for item_id, value in item_status_by_id.items() if value == status)
        if listed != expected_list:
            raise ValueError(f"$.current_plan.{status} does not match item statuses")
        detail = details[status]
        if detail.get("count") != derived_counts[status]:
            raise ValueError(f"$.current_plan.category_details.{status}.count does not match item statuses")
    return items


def _find_sensitive_values(value: Any, path: str = "$") -> list[str]:
    findings: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            findings.extend(_find_sensitive_values(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            findings.extend(_find_sensitive_values(child, f"{path}[{index}]"))
    elif isinstance(value, str):
        findings.extend(f"{path}:{name}" for name in find_sensitive_value_names(value))
    return findings


def _sanitize_text(value: str) -> str:
    """Remove historical purchase wording while retaining design meaning."""

    text = value
    replacements = (
        # Remove logistics/history wording before the broader substitutions
        # below.  The public ledger keeps the design decision and the
        # verification boundary, while a reader cannot infer a personal
        # purchase or delivery event from the wording.
        ("灰PLA(外部記録購入記録)", "灰PLA(現物未確認)"),
        ("外部記録配達品", "記録上の候補品"),
        ("配達を確認。", "現物条件は未確認。"),
        ("配達を確認", "現物条件は未確認"),
        ("配達数量", "記録上の数量"),
        ("配達品", "記録上の候補品"),
        ("配達表示", "外部記録"),
        ("配達記録", "外部記録"),
        ("購入記録", "記録上の"),
        ("購入量", "記録上の量"),
        ("受領状態", "現物確認状態"),
        ("受領未確認", "現物未確認"),
        ("受領", "現物"),
        ("到着", "現物確認"),
        ("Amazonの認証済み購入履歴", "外部記録"),
        ("Amazon認証済み購入履歴", "外部記録"),
        ("Amazon購入履歴", "外部記録"),
        ("認証済み購入履歴", "外部記録"),
        ("購入履歴", "外部記録"),
        ("購入14個", "対象個体"),
        ("14購入単位", "対象個体"),
        ("1購入単位", "既存候補"),
        ("購入単位", "既存候補"),
        ("購入済みセット", "既存候補"),
        ("購入済セット", "既存候補"),
        ("購入済み", "現物確認待ち"),
        ("購入済", "現物確認待ち"),
        ("配達済み", "現物確認待ち"),
        ("配達済", "現物確認待ち"),
        ("注文済み", "現物確認待ち"),
        ("注文済", "現物確認待ち"),
        ("注文ページ", "外部記録"),
        ("注文番号", "外部記録"),
        ("注文件数", "外部記録"),
        ("注文日", "外部記録"),
        ("注文情報", "外部記録"),
        ("注文履歴", "外部記録"),
        ("注文", "外部記録"),
        ("商品識別子（ASIN）", "商品識別子"),
        ("ASIN", "商品識別子"),
        ("amazon", "外部記録"),
        ("Amazon", "外部記録"),
        ("purchase_lots", "items"),
        ("inventory_lot_ids", "共有在庫の識別"),
        ("recorded_purchased", "既存候補"),
        ("delivery_status", "確認状態"),
        ("amazon_history_status", "外部記録状態"),
    )
    for old, new in replacements:
        text = text.replace(old, new)
    # Remove a bare product identifier even when it was embedded in a name.
    text = re.sub(r"\bB0[A-Z0-9]{8}\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"[（(]\s*[）)]", "", text)
    return text.strip()


def _safe_node(value: Any) -> Any:
    if isinstance(value, str):
        return _sanitize_text(value)
    if isinstance(value, Mapping):
        return {str(k): _safe_node(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_safe_node(v) for v in value]
    if isinstance(value, tuple):
        return [_safe_node(v) for v in value]
    return value


def _public_item(item: Mapping[str, Any]) -> dict[str, Any]:
    required = copy.deepcopy(item.get("required_total"))
    current_plan = _safe_node(copy.deepcopy(item.get("current_plan") or {}))
    status = current_plan.get("status")
    if status not in PUBLIC_PLAN_STATUSES:
        raise ValueError(f"unknown current_plan status for {item.get('id')}: {status!r}")
    boundary = _sanitize_text(
        " ".join(
            str(part)
            for part in (
                item.get("purchase_prerequisites") or "",
                item.get("current_shortage_reason") or "",
            )
            if str(part).strip()
        )
    )
    # Keep only fields that are useful for a public design decision.  The
    # original item, including removed history fields, remains in the private
    # source backup created before rewriting the public file.
    result: dict[str, Any] = {
        "id": item.get("id"),
        "anchor": item.get("anchor"),
        "group": item.get("group"),
        "name": item.get("name"),
        "specification": item.get("specification"),
        "required_total": required,
        "current_usable_quantity": item.get("current_usable_quantity"),
        "current_shortage": item.get("current_shortage"),
        "current_verification_boundary": boundary,
        "classification": item.get("classification"),
        "issue_keys": copy.deepcopy(item.get("issue_keys") or []),
        "bom_ids": copy.deepcopy(item.get("bom_ids") or []),
        "source_refs": [ref for ref in (item.get("source_refs") or []) if str(ref).lower() != "amazon"],
        "optional": bool(item.get("optional", False)),
        "candidate_only": bool(item.get("candidate_only", False)),
        "issue_urls": copy.deepcopy(item.get("issue_urls") or {}),
        "required_if_unavailable": item.get("purchase_quantity_if_none"),
        "current_plan": current_plan,
    }
    return _safe_node(result)


def make_public_payload(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Return the public payload without mutating ``raw``."""

    items = raw.get("items")
    if not isinstance(items, list) or len(items) != 134:
        raise ValueError("purchase ledger must contain exactly 134 items")
    public_plan = _safe_node(copy.deepcopy(raw.get("current_plan") or {}))
    public_plan.pop("plan_status_by_item", None)
    # The per-item status is retained on every row.  This map is useful for
    # mechanical readers and contains no inventory history.
    public_plan["plan_status_by_item"] = {
        str(item["id"]): item["current_plan"]["status"]
        for item in (_public_item(row) for row in items)
    }
    public_plan["coverage"] = _safe_node(copy.deepcopy(public_plan.get("coverage") or {}))
    public_plan["coverage"]["item_count"] = 134
    public_plan["coverage"]["classified_item_count"] = 134
    public_plan["coverage"]["classified_once"] = True
    public_plan["coverage"]["category_counts"] = {
        status: sum(1 for row in items if (row.get("current_plan") or {}).get("status") == status)
        for status in PUBLIC_PLAN_STATUSES
    }
    public_plan["immediate_purchase_required"] = 0
    public_plan["immediate_purchase_item_ids"] = []

    result: dict[str, Any] = {
        "schema_version": "2026-09-06-public-v1",
        "as_of": raw.get("as_of", "2026-09-06"),
        "title": "追加調達台帳（公開版）",
        "scope": _sanitize_text(
            "現行print-first構成の設計判断と、134項目の必要数・現物確認境界を公開する。"
        ),
        "current_plan": public_plan,
        "privacy": "個人情報や外部記録は保存しない。公開版は品目の設計判断と現物確認境界だけを扱う。",
        "rules": [
            "必要総数は追加購入数ではない。共有在庫と予備を二重加算しない。",
            "current_shortage=nullは不足0ではなく未確認。外部記録の有無は在庫0の根拠にしない。",
            "current_usable_quantityとcurrent_shortageは現物確認後に更新し、未確認値を確定しない。",
            "optionalは手持ち・借用・同等手段で代替可。candidate_onlyは未採用で先行しない。",
            "現行4区分を追加調達判断の正とし、各項目のreason・next_action・現物確認境界を併記する。",
        ],
        "sources": [
            source for source in (raw.get("sources") or []) if str(source).lower() != "amazon"
        ],
        "items": [_public_item(row) for row in items],
    }
    return _safe_node(result)


def _required_label(value: Any) -> str:
    if not isinstance(value, Mapping):
        return "未確定"
    quantity = value.get("quantity")
    unit = value.get("unit") or ""
    basis = value.get("basis")
    text = f"{quantity if quantity is not None else '未確定'} {unit}".strip()
    if basis:
        text += f"（{basis}）"
    return text


def _md(value: Any) -> str:
    text = "未確認" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ").strip()


def render_markdown(payload: Mapping[str, Any]) -> str:
    plan = payload["current_plan"]
    counts = plan["coverage"]["category_counts"]
    labels = {
        "print_first_adopted": "新print-first採用品",
        "borrow_or_verify": "借用・在庫確認",
        "deferred": "後回し",
        "not_required": "対象外・不採用",
    }
    lines = [
        "# 追加調達台帳（公開版、2026-09-06）",
        "",
        "この公開版は、現行設計の必要数と現物確認の境界を134項目について示す。",
        "外部記録、商品識別子、現物確認状態などは公開版へ含めない。",
        "",
        "## 現行の追加調達計画",
        "",
        "今回の着手に必要な追加購入数は **0**。これは実物確認後の不足がゼロだと確定する意味ではない。",
        "各項目の `current_plan` が現行4区分、理由、次の作業を示し、`current_verification_boundary` が現物確認待ちの条件を示す。",
        f"`borrow_or_verify` の**30項目**は、在庫・型番・寸法・適合を先に確認する実測対象である。30項目の未確認を購入済み・適合済み・不足0へ読み替えない。",
        "実測ゲートは、対象個体の型番/改版、数量と良品状態、寸法・ねじ・端子、許容電圧/電流、温度・保持・配線経路を記録し、設計・台帳・必要数へ反映してから不足を判定する。",
        "",
        "| 区分 | 件数 | 今回の扱い | 次の確認 |",
        "|---|---:|---|---|",
        f"| 新print-first採用品 (`print_first_adopted`) | {counts['print_first_adopted']} | 現行試作・電装ベンチの使用候補 | 適合、型番、数量、残量、材料、実測を記録 |",
        f"| 借用・在庫確認 (`borrow_or_verify`) | {counts['borrow_or_verify']} | 手持ち・借用・設計整合・実測を先に確認 | 不足確定後だけ必要数を突合 |",
        f"| 後回し (`deferred`) | {counts['deferred']} | 初期構成から外す候補を後段へ回す | 基礎試作・組立経路・物理試験後に再評価 |",
        f"| 対象外・不採用 (`not_required`) | {counts['not_required']} | 現行構成の対象外 | 再採用時だけ必要数と適合を再評価 |",
        f"| 台帳全体 | {len(payload['items'])} | 4区分で重複なく分類 | JSON `coverage.classified_once=true` を確認 |",
        "",
        "## 全134項目の必要数・判断・現物確認境界",
        "",
        "| ID | 品名・仕様 | 必要数 | 区分 | 理由 / 次の作業 | 現物確認境界 |",
        "|---|---|---:|---|---|---|",
    ]
    for item in payload["items"]:
        current = item.get("current_plan") or {}
        usable = "未確認" if item.get("current_usable_quantity") is None else item["current_usable_quantity"]
        shortage = "未確認" if item.get("current_shortage") is None else item["current_shortage"]
        reason_action = f"理由: {current.get('reason', '未記載')} / 次: {current.get('next_action', '未記載')}"
        boundary = f"使用可能数={usable}、不足={shortage}。{item.get('current_verification_boundary') or '現物確認後に更新'}"
        lines.append(
            "| "
            + " | ".join(
                (
                    f"<a id=\"{_md(item.get('anchor') or str(item.get('id', '')).lower())}\"></a>{_md(item.get('id'))}",
                    f"**{_md(item.get('name'))}**<br>{_md(item.get('specification'))}",
                    _md(_required_label(item.get("required_total"))),
                    _md(labels.get(current.get("status"), current.get("status"))),
                    _md(reason_action),
                    _md(boundary),
                )
            )
            + " |"
        )
    lines += [
        "",
        "## 機械確認用の境界",
        "",
        "`current_shortage=null` は不足0ではなく未確認を表す。必要総数、使用可能数、不足数、材料、形状適合は別々に実物で確認する。",
        "公開版の詳細項目は [additional-purchases.json](additional-purchases.json) にあり、関連課題は各JSON行の `issue_keys` で参照する。",
        "",
    ]
    return "\n".join(lines)


def find_forbidden(value: Any, path: str = "$") -> list[str]:
    findings: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key)
            if key_text in FORBIDDEN_PUBLIC_KEYS:
                findings.append(f"{path}.{key_text}")
            findings.extend(find_forbidden(child, f"{path}.{key_text}"))
        return findings
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            findings.extend(find_forbidden(child, f"{path}[{index}]"))
        return findings
    if isinstance(value, str):
        for pattern in FORBIDDEN_PUBLIC_PATTERNS:
            if pattern.search(value):
                findings.append(f"{path}: {pattern.pattern}")
    return findings


def assert_public_safe(payload: Mapping[str, Any], markdown: str | None = None) -> None:
    items = _validate_public_schema(payload)
    sensitive = _find_sensitive_values(payload)
    if sensitive:
        raise ValueError("sensitive value in public purchase ledger: " + "; ".join(sensitive[:12]))
    plan = payload.get("current_plan") or {}
    coverage = plan.get("coverage") or {}
    if coverage.get("item_count") != 134 or coverage.get("classified_once") is not True:
        raise ValueError("public purchase coverage is incomplete")
    counts = coverage.get("category_counts") or {}
    expected = {"print_first_adopted": 67, "borrow_or_verify": 30, "deferred": 31, "not_required": 6}
    if {key: counts.get(key) for key in expected} != expected:
        raise ValueError(f"public purchase category counts drifted: {counts}")
    if plan.get("immediate_purchase_required") != 0 or plan.get("immediate_purchase_item_ids") != []:
        raise ValueError("public purchase plan must keep immediate purchase at zero")
    findings = find_forbidden(payload)
    if markdown is not None:
        if markdown != render_markdown(payload):
            raise ValueError("public purchase Markdown is not the deterministic rendering of JSON")
        sensitive_markdown = _find_sensitive_values(markdown, "$markdown")
        if sensitive_markdown:
            raise ValueError("sensitive value in public purchase Markdown: " + "; ".join(sensitive_markdown[:12]))
        findings.extend(find_forbidden(markdown, "$markdown"))
    if findings:
        raise ValueError("forbidden private purchase data in public ledger: " + "; ".join(findings[:12]))


def rewrite_public_files(raw_path: Path = PUBLIC_JSON) -> dict[str, Any]:
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    payload = make_public_payload(raw)
    markdown = render_markdown(payload)
    assert_public_safe(payload, markdown)
    PUBLIC_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    PUBLIC_MD.write_text(markdown, encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validate", action="store_true", help="validate the existing public files")
    parser.add_argument("--rewrite", action="store_true", help="rewrite public files from the current JSON")
    args = parser.parse_args()
    if args.rewrite:
        payload = rewrite_public_files()
    else:
        payload = json.loads(PUBLIC_JSON.read_text(encoding="utf-8"))
        markdown = PUBLIC_MD.read_text(encoding="utf-8")
        assert_public_safe(payload, markdown)
    print(json.dumps({
        "status": "PASS",
        "items": len(payload["items"]),
        "category_counts": payload["current_plan"]["coverage"]["category_counts"],
        "immediate_purchase_required": payload["current_plan"]["immediate_purchase_required"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
