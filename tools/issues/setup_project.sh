#!/usr/bin/env bash
# GitHub Projects v2 ボードを作成し、plan.py 由来の全イシューを追加する。
#
# 前提: gh の token に project スコープが必要 (repo スコープだけでは 403)。
#   gh auth refresh -h github.com -s project,read:project
# 使い方 (リポジトリルートで):
#   tools/issues/setup_project.sh            # 作成 + 全イシュー追加
#   PROJECT_NUMBER=3 tools/issues/setup_project.sh   # 既存プロジェクトへ追加のみ
#
# 作るもの:
#   - Project "Tachikoma 物理製作" (owner: hapx2yuki)
#   - Status に Ready / Blocked を追加 (既定: Todo / In Progress / Done)
#   - 単一選択フィールド「レーン」(印刷/電装/脚・歩行/腕/頭部/意匠/準備/統合)
#   - 全イシューを追加し、レーンを親エピックから設定
# ビュー (Board by Status / Table / Roadmap by Milestone) と Visibility=Public は
# Web UI で設定する (gh CLI にビュー作成コマンドが無いため)。
set -euo pipefail
OWNER=hapx2yuki
REPO=hapx2yuki/Tachikoma
TITLE="Tachikoma 物理製作"
HERE="$(cd "$(dirname "$0")" && pwd)"
MAP="$HERE/issue_map.json"

if ! gh auth status 2>&1 | grep -q "project"; then
  echo "!! token に project スコープがありません: gh auth refresh -h github.com -s project,read:project" >&2
  exit 1
fi
[ -f "$MAP" ] || { echo "!! $MAP がありません。先に sync_github_issues.py --apply を実行" >&2; exit 1; }

if [ -z "${PROJECT_NUMBER:-}" ]; then
  echo "== project create: $TITLE"
  PROJECT_NUMBER=$(gh project create --owner "$OWNER" --title "$TITLE" --format json --jq .number)
  echo "   number=$PROJECT_NUMBER"
  gh project link "$PROJECT_NUMBER" --owner "$OWNER" --repo "$REPO" || true
fi
PID=$(gh project view "$PROJECT_NUMBER" --owner "$OWNER" --format json --jq .id)

echo "== fields"
# Status の選択肢に Ready / Blocked を追加 (既存 Todo/In Progress/Done は維持)
STATUS_ID=$(gh project field-list "$PROJECT_NUMBER" --owner "$OWNER" --format json --jq '.fields[] | select(.name=="Status") | .id')
gh api graphql -f query='
  mutation($f:ID!){ updateProjectV2Field(input:{fieldId:$f, singleSelectOptions:[
    {name:"Todo",        color:GRAY,   description:"未着手 (前提あり)"},
    {name:"Ready",       color:GREEN,  description:"前提が全て Close。今すぐ取れる"},
    {name:"In Progress", color:BLUE,   description:"担当者が着手中"},
    {name:"Blocked",     color:RED,    description:"要判断 / 部品待ち / 不具合で止まっている"},
    {name:"Done",        color:PURPLE, description:"DoD 達成・証拠付きで Close"}
  ]}){ projectV2Field{ ... on ProjectV2SingleSelectField { id } } } }' -f f="$STATUS_ID" >/dev/null
# レーン (親エピック)
if ! gh project field-list "$PROJECT_NUMBER" --owner "$OWNER" --format json --jq '.fields[].name' | grep -qx "レーン"; then
  gh project field-create "$PROJECT_NUMBER" --owner "$OWNER" --name "レーン" --data-type SINGLE_SELECT \
    --single-select-options "E1 準備,E2 印刷キュー,E3 電装,E4 脚・歩行,E5 腕,E6 頭部,E7 意匠シェル,E8 統合"
fi

echo "== add items"
python3 - "$MAP" "$OWNER" "$PROJECT_NUMBER" <<'PY'
import json, subprocess, sys, re
mp = json.load(open(sys.argv[1])); owner = sys.argv[2]; num = sys.argv[3]
sys.path.insert(0, "tools/issues"); import plan
lane_of = {"E1":"E1 準備","E2":"E2 印刷キュー","E3":"E3 電装","E4":"E4 脚・歩行","E5":"E5 腕","E6":"E6 頭部","E7":"E7 意匠シェル","E8":"E8 統合"}
fields = json.loads(subprocess.check_output(["gh","project","field-list",num,"--owner",owner,"--format","json"]))["fields"]
lane = next(f for f in fields if f["name"]=="レーン"); status = next(f for f in fields if f["name"]=="Status")
pid = json.loads(subprocess.check_output(["gh","project","view",num,"--owner",owner,"--format","json"]))["id"]
existing = {it["content"].get("number"): it["id"] for it in json.loads(subprocess.check_output(
    ["gh","project","item-list",num,"--owner",owner,"--format","json","--limit","500"]))["items"] if it.get("content")}
for spec in plan.ISSUES:
    n = mp.get(spec["key"]);
    if not n: continue
    url = f"https://github.com/{plan.REPO}/issues/{n}"
    item_id = existing.get(n)
    if not item_id:
        item_id = json.loads(subprocess.check_output(["gh","project","item-add",num,"--owner",owner,"--url",url,"--format","json"]))["id"]
        print("  +", spec["key"], f"#{n}")
    key = spec["parent"] or spec["key"]
    opt = next((o["id"] for o in lane["options"] if o["name"]==lane_of.get(key, "")), None)
    if opt:
        subprocess.run(["gh","project","item-edit","--project-id",pid,"--id",item_id,"--field-id",lane["id"],"--single-select-option-id",opt],check=True,capture_output=True)
    st = "Ready" if not spec["blocked_by"] and "type/エピック" not in spec["labels"] else "Todo"
    sopt = next(o["id"] for o in status["options"] if o["name"]==st)
    subprocess.run(["gh","project","item-edit","--project-id",pid,"--id",item_id,"--field-id",status["id"],"--single-select-option-id",sopt],check=True,capture_output=True)
print("done")
PY

echo
echo "== 次に Web UI で:"
echo "   https://github.com/users/$OWNER/projects/$PROJECT_NUMBER"
echo "   1) Settings → Visibility → Public"
echo "   2) ビュー: Board (group by Status, レーンで swimlane) / Table / Roadmap (date = Milestone)"
echo "   3) Workflows: 'Item closed → Done', 'Item reopened → Todo' を ON"
