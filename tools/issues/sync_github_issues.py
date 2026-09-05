#!/usr/bin/env python3
"""tools/issues/plan.py を GitHub Issues へ冪等に同期する。

usage (リポジトリルートで):
  .venv/bin/python tools/issues/sync_github_issues.py --dry-run        # 何をするか表示のみ
  .venv/bin/python tools/issues/sync_github_issues.py --apply          # ラベル/マイルストーン/イシュー作成 + 親子/依存
  .venv/bin/python tools/issues/sync_github_issues.py --apply --update-bodies   # 既存イシューの本文も plan.py で上書き
  .venv/bin/python tools/issues/sync_github_issues.py --plan-doc       # docs/build_plan.md の自動生成節を更新

前提: `gh auth login` 済み (scope: repo)。Projects v2 は別スクリプト (setup_project.sh, scope: project)。

冪等化の仕組み:
  各イシュー本文の先頭に `<!-- tachikoma-key: L-01 -->` を埋め込み、同期時は
  リポジトリの全イシュー (open+closed) からこのマーカーを拾って key→番号 を復元する。
  マーカーが無い key だけ新規作成する。親子 (sub-issue) と依存 (blocked by) は
  既存関係を GraphQL で読んでから不足分だけ追加する。
本文中の `{{KEY}}` は `#番号` に置換される (作成後に 2 パス目で本文を PATCH)。
"""
import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
import plan  # noqa: E402

OWNER, NAME = plan.REPO.split("/")
MAP_PATH = HERE / "issue_map.json"


# ---------------------------------------------------------------- gh helpers
def gh(*args, input_json=None, ok_codes=(0,)):
    cmd = ["gh", *args]
    r = subprocess.run(cmd, input=json.dumps(input_json) if input_json is not None else None,
                       text=True, capture_output=True)
    if r.returncode not in ok_codes:
        raise RuntimeError(f"gh {' '.join(args[:3])}... failed ({r.returncode}): {r.stderr.strip()[:500]}")
    return r.stdout


def api(method, path, body=None, paginate=False):
    args = ["api", "-X", method, path, "-H", "Accept: application/vnd.github+json"]
    if paginate:
        args.append("--paginate")
    if body is not None:
        args += ["--input", "-"]
    out = gh(*args, input_json=body)
    if not out.strip():
        return None
    if paginate:
        # --paginate は複数 JSON 配列を連結して返す
        items = []
        dec = json.JSONDecoder()
        s = out.strip()
        pos = 0
        while pos < len(s):
            obj, end = dec.raw_decode(s, pos)
            items.extend(obj if isinstance(obj, list) else [obj])
            pos = end
            while pos < len(s) and s[pos].isspace():
                pos += 1
        return items
    return json.loads(out)


def graphql(query, variables=None):
    body = {"query": query, "variables": variables or {}}
    out = gh("api", "graphql", "-H", "GraphQL-Features: sub_issues", "--input", "-", input_json=body)
    data = json.loads(out)
    if "errors" in data:
        raise RuntimeError(f"GraphQL errors: {data['errors']}")
    return data["data"]


# ---------------------------------------------------------------- labels / milestones
def sync_labels(dry):
    existing = {l["name"]: l for l in api("GET", f"repos/{plan.REPO}/labels?per_page=100", paginate=True)}
    for name, color, desc in plan.LABELS:
        cur = existing.get(name)
        if cur is None:
            print(f"  + label {name}")
            if not dry:
                api("POST", f"repos/{plan.REPO}/labels", {"name": name, "color": color, "description": desc})
        elif cur["color"].lower() != color.lower() or (cur.get("description") or "") != desc:
            print(f"  ~ label {name}")
            if not dry:
                api("PATCH", f"repos/{plan.REPO}/labels/{name}", {"new_name": name, "color": color, "description": desc})


def sync_milestones(dry):
    existing = {m["title"]: m for m in api("GET", f"repos/{plan.REPO}/milestones?state=all&per_page=100", paginate=True)}
    out = {}
    for title, desc in plan.MILESTONES:
        cur = existing.get(title)
        if cur is None:
            print(f"  + milestone {title}")
            if not dry:
                cur = api("POST", f"repos/{plan.REPO}/milestones", {"title": title, "description": desc})
        elif (cur.get("description") or "") != desc:
            print(f"  ~ milestone {title}")
            if not dry:
                cur = api("PATCH", f"repos/{plan.REPO}/milestones/{cur['number']}", {"description": desc})
        if cur:
            out[title] = cur["number"]
    return out


# ---------------------------------------------------------------- issues
MARK_RE = re.compile(rf"<!--\s*{plan.MARKER}:\s*([A-Za-z0-9\-]+)\s*-->")


def load_existing():
    """key -> {number, node_id, title, state} を全イシューから復元 (PR は除外)。"""
    items = api("GET", f"repos/{plan.REPO}/issues?state=all&per_page=100", paginate=True)
    found = {}
    for it in items:
        if "pull_request" in it:
            continue
        m = MARK_RE.search(it.get("body") or "")
        if m:
            if m.group(1) in found:
                raise RuntimeError(f"Issueキー重複: {m.group(1)} (#{found[m.group(1)]['number']}, #{it['number']})")
            found[m.group(1)] = {"number": it["number"], "node_id": it["node_id"],
                                 "title": it["title"], "state": it["state"], "_new": False}
    return found


def render_body(spec, numbers):
    """本文を組み立てる。numbers: key -> issue number (未作成は None)。"""
    def ref(k):
        n = numbers.get(k)
        return f"#{n}" if n else f"`{k}`"
    head = [f"<!-- {plan.MARKER}: {spec['key']} -->"]
    meta = []
    if spec["parent"]:
        meta.append(f"**ストリーム**: {ref(spec['parent'])}")
    if spec["milestone"]:
        meta.append(f"**マイルストーン**: {spec['milestone']}")
    if meta:
        head.append(" / ".join(meta))
    if spec["blocked_by"]:
        head.append("")
        head.append("## 完了待ちの条件（採用・組立・試験の前に満たす。独立した準備は本文参照）")
        for b in spec["blocked_by"]:
            title = next((i["title"] for i in plan.ISSUES if i["key"] == b), b)
            head.append(f"- {ref(b)} {title}")
    body = spec["body"]
    body = re.sub(r"\{\{([^}]+)\}\}", lambda m: ref(m.group(1)), body)
    return "\n".join(head) + "\n\n" + body + "\n" + plan.FOOTER


def sync_issues(dry, update_bodies, milestones, specs=None):
    specs = plan.ISSUES if specs is None else specs
    existing = load_existing()
    numbers = {k: v["number"] for k, v in existing.items()}
    # --- pass 1: 作成 (本文はプレースホルダ付きで仮) ---
    for spec in specs:
        if spec["key"] in existing:
            continue
        print(f"  + issue {spec['key']}: {spec['title']}")
        if dry:
            continue
        payload = {"title": spec["title"], "body": render_body(spec, numbers), "labels": spec["labels"]}
        if spec["milestone"]:
            payload["milestone"] = milestones[spec["milestone"]]
        created = api("POST", f"repos/{plan.REPO}/issues", payload)
        existing[spec["key"]] = {"number": created["number"], "node_id": created["node_id"],
                                 "title": created["title"], "state": created["state"], "_new": True}
        numbers[spec["key"]] = created["number"]
        time.sleep(0.3)   # secondary rate limit 対策
    # --- pass 2: 本文を実番号で確定 (新規は必ず、既存は --update-bodies 時) ---
    for spec in specs:
        cur = existing.get(spec["key"])
        if cur is None:
            continue  # dry-runの新規Issue。作成予定はpass 1で表示済み。
        want = render_body(spec, numbers)
        # 新規作成直後は本文に未解決参照 (`KEY`) が残り得るので必ず再 PATCH
        if update_bodies or cur["_new"]:
            it = api("GET", f"repos/{plan.REPO}/issues/{cur['number']}")
            patch = {}
            if (it.get("body") or "") != want:
                patch["body"] = want
            if it["title"] != spec["title"]:
                patch["title"] = spec["title"]
            have_labels = {l["name"] for l in it.get("labels", [])}
            managed_labels = {name for name, _, _ in plan.LABELS}
            want_labels = (have_labels - managed_labels) | set(spec["labels"])
            if want_labels != have_labels:
                patch["labels"] = sorted(want_labels)
            ms = milestones.get(spec["milestone"]) if spec["milestone"] else None
            if ms and (it.get("milestone") or {}).get("number") != ms:
                patch["milestone"] = ms
            if patch:
                print(f"  ~ body/meta {spec['key']} (#{cur['number']}): {sorted(patch)}")
                if not dry:
                    api("PATCH", f"repos/{plan.REPO}/issues/{cur['number']}", patch)
                    time.sleep(0.2)
    return existing


# ---------------------------------------------------------------- relations
def fetch_relations(numbers):
    """number -> {parent: number|None, blocked_by: set(numbers)} を GraphQL で一括取得。"""
    rel = {}
    nums = sorted(numbers)
    for i in range(0, len(nums), 25):
        chunk = nums[i:i + 25]
        q = "query($o:String!,$n:String!){ repository(owner:$o,name:$n){ " + " ".join(
            f"i{n}: issue(number:{n}){{ number parent{{number}} blockedBy(first:50){{nodes{{number}}}} }}"
            for n in chunk) + " } }"
        data = graphql(q, {"o": OWNER, "n": NAME})["repository"]
        for k, v in data.items():
            rel[v["number"]] = {"parent": (v["parent"] or {}).get("number"),
                                "blocked_by": {x["number"] for x in v["blockedBy"]["nodes"]}}
    return rel


def sync_relations(dry, existing, specs=None, reconcile=False):
    specs = plan.ISSUES if specs is None else specs
    numbers = {k: v["number"] for k, v in existing.items()}
    node = {v["number"]: v["node_id"] for v in existing.values()}
    rel = fetch_relations(set(numbers.values()))
    for spec in specs:
        me = numbers.get(spec["key"])
        if me is None:
            if dry:
                print(f"  planned {spec['key']}: parent={spec['parent']}, blocked_by={spec['blocked_by']}")
            continue
        # 親子
        if spec["parent"]:
            p = numbers.get(spec["parent"])
            if p and rel[me]["parent"] != p:
                print(f"  parent  #{me} ({spec['key']}) <- #{p} ({spec['parent']})")
                if not dry:
                    graphql("mutation($p:ID!,$c:ID!){ addSubIssue(input:{issueId:$p, subIssueId:$c, replaceParent:true}){ issue{number} } }",
                            {"p": node[p], "c": node[me]})
                    time.sleep(0.2)
        # 依存
        for b in spec["blocked_by"]:
            bn = numbers.get(b)
            if bn and bn not in rel[me]["blocked_by"]:
                print(f"  blocked #{me} ({spec['key']}) by #{bn} ({b})")
                if not dry:
                    graphql("mutation($i:ID!,$b:ID!){ addBlockedBy(input:{issueId:$i, blockingIssueId:$b}){ issue{number} } }",
                            {"i": node[me], "b": node[bn]})
                    time.sleep(0.2)
        if reconcile:
            # 管理外の関係は消さない。明示された対象の、plan管理キーに限る。
            want = {numbers[b] for b in spec['blocked_by'] if b in numbers}
            managed_numbers = {numbers[s['key']] for s in plan.ISSUES if s['key'] in numbers}
            stale = (rel[me]['blocked_by'] & managed_numbers) - want
            for bn in sorted(stale):
                print(f"  remove blocked #{me} ({spec['key']}) by #{bn}")
                if not dry:
                    graphql("mutation($i:ID!,$b:ID!){ removeBlockedBy(input:{issueId:$i, blockingIssueId:$b}){ issue{number} } }",
                            {'i': node[me], 'b': node[bn]})


# ---------------------------------------------------------------- plan doc (mermaid)
def _short(spec):
    t = spec["title"]
    t = re.sub(r"^[A-Z]+-?[0-9a-z]*\s+", "", t)         # key を落とす
    t = re.sub(r"^\[[^\]]+\]\s*", "", t)                 # [領域] タグを落とす
    t2 = re.sub(r"\s*[—(].*$", "", t)                      # 副題を落とす
    t = t2 if t2.strip() else t                            # 全部落ちたら元のまま
    return t[:28].replace('"', "'")


def render_plan_doc(numbers):
    """docs/build_plan.md の自動生成節 (mermaid + 表) を返す。"""
    def ref(k):
        n = numbers.get(k)
        return f"#{n}" if n else k
    lines = ["<!-- BEGIN GENERATED (tools/issues/sync_github_issues.py --plan-doc) -->",
             f"_生成: {time.strftime('%Y-%m-%d')} / イシュー数 {len(plan.ISSUES)} / 依存 {sum(len(i['blocked_by']) for i in plan.ISSUES)}_", "",
             "### 依存グラフ (矢印 = 先行 → 後続。エピックは省略)", "", "```mermaid", "flowchart LR"]
    epics = [i for i in plan.ISSUES if "type/エピック" in i["labels"]]
    tasks = [i for i in plan.ISSUES if "type/エピック" not in i["labels"]]
    for e in epics:
        kids = [t for t in tasks if t["parent"] == e["key"]]
        if not kids:
            continue
        lines.append(f"  subgraph {e['key']}[\"{e['key']} {_short(e)}\"]")
        for t in kids:
            shape = ("{{", "}}") if "type/ゲート" in t["labels"] else (("[/", "/]") if "type/要判断" in t["labels"] else ("[", "]"))
            lines.append(f"    {t['key'].replace('-', '_')}{shape[0]}\"{t['key']} {_short(t)}\"{shape[1]}")
        lines.append("  end")
    for t in tasks:
        for b in t["blocked_by"]:
            lines.append(f"  {b.replace('-', '_')} --> {t['key'].replace('-', '_')}")
    gates = [t["key"].replace('-', '_') for t in tasks if "type/ゲート" in t["labels"]]
    if gates:
        lines.append(f"  classDef gate fill:#ffd6d6,stroke:#b60205,stroke-width:2px;")
        lines.append(f"  class {','.join(gates)} gate;")
    decs = [t["key"].replace('-', '_') for t in tasks if "type/要判断" in t["labels"]]
    if decs:
        lines.append(f"  classDef dec fill:#fff3bf,stroke:#fbca04;")
        lines.append(f"  class {','.join(decs)} dec;")
    lines += ["```", "", "### 今すぐ着手できるイシュー (依存なし)", ""]
    for t in tasks:
        if not t["blocked_by"]:
            lines.append(f"- {ref(t['key'])} {t['title']}" + ("  ← 並行作業OK" if "並行作業OK" in t["labels"] else ""))
    lines += ["", "### 一覧 (マイルストーン順)", "", "| key | イシュー | マイルストーン | 前提 | 並行 |", "|---|---|---|---|---|"]
    order = {m[0]: i for i, m in enumerate(plan.MILESTONES)}
    for t in sorted(tasks, key=lambda x: (order.get(x["milestone"], 99), x["key"])):
        deps = ", ".join(ref(b) for b in t["blocked_by"]) or "—"
        par = "✅" if "並行作業OK" in t["labels"] else ("🖨" if "res/プリンタ" in t["labels"] else ("🤖" if "res/本体" in t["labels"] else ""))
        lines.append(f"| {t['key']} | {ref(t['key'])} {t['title']} | {t['milestone'] or ''} | {deps} | {par} |")
    lines.append("<!-- END GENERATED -->")
    return "\n".join(lines)


def update_plan_doc(numbers):
    doc = ROOT / "docs" / "build_plan.md"
    gen = render_plan_doc(numbers)
    if doc.exists():
        txt = doc.read_text()
        new, count = re.subn(r"<!-- BEGIN GENERATED.*?<!-- END GENERATED -->", lambda m: gen, txt, flags=re.S)
        if count == 0:
            new = txt.rstrip() + "\n\n" + gen + "\n"
        elif count > 1:
            raise RuntimeError("生成節が複数存在するため自動更新を停止。重複節を確認してください。")
    else:
        new = "# 物理製作 ビルドプラン\n\n" + gen + "\n"
    doc.write_text(new)
    print(f"  wrote {doc.relative_to(ROOT)}")


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    ap.add_argument("--update-bodies", action="store_true", help="既存イシューの本文/タイトル/ラベル/マイルストーンも plan.py で上書き")
    ap.add_argument("--plan-doc", action="store_true", help="docs/build_plan.md の自動生成節を更新")
    ap.add_argument("--keys", nargs="+", help="同期するIssueキーを限定（本文・親子・依存）。未指定は全件")
    ap.add_argument("--reconcile-dependencies", action="store_true", help="指定キーから不要になった管理内依存を削除（--keys必須）")
    a = ap.parse_args()
    if not (a.dry_run or a.apply or a.plan_doc):
        ap.error("--dry-run / --apply / --plan-doc のいずれかを指定")
    dry = not a.apply
    if a.reconcile_dependencies and not a.keys:
        ap.error("--reconcile-dependencies は --keys による対象限定が必要")
    specs = plan.ISSUES
    if a.keys:
        unknown = set(a.keys) - {s['key'] for s in specs}
        if unknown:
            ap.error(f"不明なキー: {sorted(unknown)}")
        specs = [s for s in specs if s['key'] in a.keys]

    if a.dry_run or a.apply:
        print("== labels ==")
        sync_labels(dry)
        print("== milestones ==")
        ms = sync_milestones(dry)
        print("== issues ==")
        existing = sync_issues(dry, a.update_bodies, ms, specs)
        print("== relations ==")
        sync_relations(dry, existing, specs, a.reconcile_dependencies)
        if not dry:
            MAP_PATH.write_text(json.dumps({k: v["number"] for k, v in sorted(existing.items())},
                                           ensure_ascii=False, indent=1) + "\n")
            print(f"  wrote {MAP_PATH.relative_to(ROOT)}")
    if a.plan_doc:
        numbers = json.loads(MAP_PATH.read_text()) if MAP_PATH.exists() else {}
        update_plan_doc(numbers)


if __name__ == "__main__":
    main()
