#!/usr/bin/env python3
"""公開・外部反映前の未確定プレースホルダー検査。

候補台帳は未確定欄を持てるが、外部公開またはIssue追記へ進む入力は
実値へ置換済みでなければならない。検査は文字列を再帰的に走査し、
プレースホルダーを見つけた時点で停止する。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Any


PLACEHOLDER_PATTERNS = (
    re.compile(r"<FINAL_[A-Z0-9_]+>"),
    re.compile(r"<DRAFT_PR_URL>"),
    re.compile(r"<[A-Z][A-Z0-9_]{2,}>"),
    re.compile(r"<[^>]*(?:PLACEHOLDER|PENDING|UNFILLED|FREEZE2)[^>]*>", re.IGNORECASE),
    re.compile(r"(?:PLACEHOLDER_UNFILLED|PENDING_FINAL_FREEZE2|FREEZE2_PENDING_CANDIDATE)", re.IGNORECASE),
)

LEGACY_ISSUE_PUBLICATION_PATTERNS = (
    re.compile(r"codex/audit-20260905"),
    # The historical plan used several prefixes, so keep the numeric part in
    # the gate as the invariant.  A current Project count can still be 96
    # while #99--#109 are waiting to be added; only the old issue-range
    # wording is rejected here.
    re.compile(r"(?<!\d)96(?:課題|件|本文)"),
)


def _walk(value: Any, path: str = "$") -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            findings.extend(_walk(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            findings.extend(_walk(child, f"{path}[{index}]"))
    elif isinstance(value, str):
        for pattern in PLACEHOLDER_PATTERNS:
            match = pattern.search(value)
            if match:
                findings.append({"path": path, "match": match.group(0)})
    return findings


def find_placeholders(value: Any) -> list[dict[str, str]]:
    return _walk(value)


def assert_no_placeholders(value: Any, *, source: str = "input") -> None:
    findings = find_placeholders(value)
    if findings:
        detail = "; ".join(f"{source}{row['path']}: {row['match']}" for row in findings[:12])
        raise ValueError("publish blocked by unresolved placeholder(s): " + detail)


def assert_current_issue_publication(value: Any, *, source: str = "input") -> None:
    """Issue公開入力に古い固定branch/96件定型文が残っていないか検査する。"""
    findings: list[str] = []

    def walk(node: Any, path: str = "$") -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                walk(child, f"{path}.{key}")
        elif isinstance(node, list):
            for index, child in enumerate(node):
                walk(child, f"{path}[{index}]")
        elif isinstance(node, str):
            for pattern in LEGACY_ISSUE_PUBLICATION_PATTERNS:
                if pattern.search(node):
                    findings.append(f"{source}{path}: {pattern.pattern}")

    walk(value)
    if findings:
        raise ValueError("publish blocked by stale Issue publication reference(s): " + "; ".join(findings[:12]))


def load_path(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        return json.loads(text)
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", action="append", type=Path, required=True,
                        help="検査するJSON/Markdown等（複数指定可）")
    args = parser.parse_args()
    checked = []
    for raw_path in args.path:
        path = raw_path.resolve()
        if not path.is_file():
            parser.error(f"file not found: {path}")
        value = load_path(path)
        assert_no_placeholders(value, source=str(path))
        assert_current_issue_publication(value, source=str(path))
        checked.append(str(path))
    print(json.dumps({"status": "PASS", "checked": checked}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
