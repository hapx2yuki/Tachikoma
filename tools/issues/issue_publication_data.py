"""#99--#109の公開Issue計画とProject初期値。

既存#3--#98の計画は ``audit_plan_data.py`` に保存されている。ここでは、
後から作成された11件を別表として定義し、Issue番号・Projectの初期Status・
レーン・正式な依存方針を一つの公開可能な入力にまとめる。

このファイルにはIssue本文、コメント本文、外部記録を保存しない。既存Issueの
本文を更新する用途にも使わず、未掲載Issueの識別と新規項目の初期値に限定する。
"""

from __future__ import annotations

from copy import deepcopy
import re


PROJECT_LANES = (
  "E1 準備",
  "E2 印刷キュー",
  "E3 電装",
  "E4 脚・歩行",
  "E5 腕",
  "E6 頭部",
  "E7 意匠シェル",
  "E8 統合",
  "E9 独立監査",
)
PROJECT_STATUSES = ("Todo", "Ready", "In Progress", "Blocked", "Done")


# ``blocked_by`` is the formal dependency policy. References in an Issue body
# may provide context, but are not silently promoted to formal dependencies.
EXTRA_ISSUES = (
  {
    "key": "GH-099",
    "issue_number": 99,
    "title": "[不具合] 設計変更必要",
    "parent": None,
    "milestone": "M0 準備完了",
    "labels": ["type/不具合"],
    "blocked_by": ["GH-100", "GH-101", "GH-104"],
    "project_status": "Blocked",
    "project_lane": "E8 統合",
    "project_status_reason": "前段の実測・保持確認・1個試作が揃うまで設計変更の要否を保留する。",
    "acceptance_condition": "LD-220MGの設計判断を、#104の実測・#101の保持/出口確認・#100の1個試作の根拠で確定し、それらが揃うまで設計変更と量産を完了扱いにしない",
    "next_step": "#104の寸法表、#101の保持/出口確認、#100の試作適合をこの順に照合する",
    "evidence": ["docs/print-first.md", "docs/print-first-manifest.json"],
    "progress": "前段の実測・保持・試作根拠待ち",
  },
  {
    "key": "GH-100",
    "issue_number": 100,
    "title": "[印刷] coxa_bracket",
    "parent": None,
    "milestone": "M1 片脚 Go/No-Go",
    "labels": ["area/印刷", "res/プリンタ", "skill/プリンタ操作", "type/タスク"],
    "blocked_by": ["GH-104", "GH-101", "GH-103"],
    "project_status": "Blocked",
    "project_lane": "E4 脚・歩行",
    "project_status_reason": "実測・保持・配線経路を確認するまで、coxaの印刷版を固定しない。",
    "acceptance_condition": "LD-220MGの実測・保持方式・配線出口・ホーン接続を反映したSTLを1個試し、嵌合・配線・締結を寸法と写真で確認してから残数を解放する",
    "next_step": "#104/#101/#103の実物根拠を反映した1個を印刷候補として識別し、適合結果を記録する",
    "evidence": ["docs/print-first-manifest.json", "docs/print-first-orientation-manifest.json"],
    "progress": "設計変更と実物適合待ち",
  },
  {
    "key": "GH-101",
    "issue_number": 101,
    "title": "[印刷] クレビス：LD-220MGの保持・ケーブル出口・接続部の確認と軽量化の相談",
    "parent": None,
    "milestone": "M1 片脚 Go/No-Go",
    "labels": ["area/印刷", "res/プリンタ", "skill/プリンタ操作", "type/タスク"],
    "blocked_by": ["GH-104", "GH-103"],
    "project_status": "Blocked",
    "project_lane": "E4 脚・歩行",
    "project_status_reason": "#104の実測と#103の出口確認だけを待つ1個候補検証で、最終採用前の形状を固定しない。",
    "acceptance_condition": "耳なし保持、配線出口、ヨー接続、軽量化後の残肉を実物で確認し、保持試験・写真・形状SHAを記録する",
    "next_step": "#104の寸法と#103の出口条件を照合し、保持候補を1個で仮合わせしてから形状を凍結する",
    "evidence": ["docs/print-first.md", "hardware/src/make_ld220_adapter.py"],
    "progress": "保持形状と実物寸法の確認待ち",
  },
  {
    "key": "GH-102",
    "issue_number": 102,
    "title": "[不具合] tibia_link：膝サーボの出力軸との固定方法を確認したい",
    "parent": None,
    "milestone": "M0 準備完了",
    "labels": ["type/不具合"],
    "blocked_by": [],
    "project_status": "Ready",
    "project_lane": "E4 脚・歩行",
    "project_status_reason": "対象個体のホーン・中心ねじ・軸端を測れば着手できる。",
    "acceptance_condition": "付属ホーン・中心ねじ・軸端を対象サーボの現物で確認してtibia固定を決め、未測定のホーン候補を確定値にしない",
    "next_step": "現物1個のホーンとねじを測り、tibia/femur/coxaの各ポケットと適合を比較する",
    "evidence": ["docs/print-first.md", "docs/print-first-manifest.json"],
    "progress": "現物の軸端・ホーン測定待ち",
  },
  {
    "key": "GH-103",
    "issue_number": 103,
    "title": "[組立] 全体：サーボケーブルの取り出し口・配線経路を確認したい",
    "parent": None,
    "milestone": "M0 準備完了",
    "labels": ["area/組立", "type/タスク"],
    "blocked_by": [],
    "project_status": "Ready",
    "project_lane": "E8 統合",
    "project_status_reason": "無通電の配線追跡から着手でき、他の設計変更とは独立して確認できる。",
    "acceptance_condition": "対象サーボを含む全出口で全可動域の挟み込み・引張り・擦れがなく、個体識別付きの実物記録がある",
    "next_step": "無通電の全可動域で配線を追跡し、固定前後の余長・擦れ・出口写真を残す",
    "evidence": ["docs/wiring.md", "docs/print-first.md", "tools/check_static_assembly.py"],
    "progress": "無通電配線の経路確認待ち",
  },
  {
    "key": "GH-104",
    "issue_number": 104,
    "title": "[測定] LD-220MG の実寸 (ケース高さ・ホーン上面・補助軸ボス・配線高さ・底面ねじ穴) を測って config.py LD220 を確定する",
    "parent": None,
    "milestone": "M0 準備完了",
    "labels": ["area/測定", "prio/P0", "type/タスク", "並行作業OK"],
    "blocked_by": [],
    "project_status": "Ready",
    "project_lane": "E4 脚・歩行",
    "project_status_reason": "測定器と対象個体があれば、設計変更に先行して着手できる。",
    "acceptance_condition": "公式値と現物ノギス値を分離し、ケース・ホーン上面・補助軸・配線・底面穴の値、写真、config更新、検査結果を揃える",
    "next_step": "公式資料で確定できる値だけを出典付きで残し、軸位置・PCD・ねじ等は現物実測までUNVERIFIEDとする",
    "evidence": ["docs/print-first-manifest.json", "hardware/src/config.py"],
    "progress": "対象個体の寸法表作成待ち",
  },
  {
    "key": "GH-105",
    "issue_number": 105,
    "title": "[印刷] ld220_cup_leg ×8 (LD-220MG 固定カップ, 股ピッチ 4 + 膝 4) を印刷する",
    "parent": None,
    "milestone": "M1 片脚 Go/No-Go",
    "labels": ["area/印刷", "prio/P0", "res/プリンタ", "skill/プリンタ操作", "type/タスク"],
    "blocked_by": ["GH-099", "GH-100", "GH-101", "GH-103"],
    "project_status": "Blocked",
    "project_lane": "E4 脚・歩行",
    "project_status_reason": "設計変更・保持・配線経路を確認してから、8個の印刷へ進む。",
    "acceptance_condition": "前提課題の成立後、ld220_cup_leg 8個の版・材料・向き・配線穴・固定・反りを実物写真と寸法で確認する",
    "next_step": "8個を完成機の設計必要数へ混ぜず、LD適合の個別工程として1個の試作から確認する",
    "evidence": ["docs/print-first-manifest.json", "docs/print-first-orientation-manifest.json", "docs/additional-printing.json"],
    "progress": "前提課題の根拠待ち",
  },
  {
    "key": "GH-106",
    "issue_number": 106,
    "title": "[印刷] ld220_cup_yaw ×4 (シャーシ ヨーサーボ用 LD-220MG カップ) を印刷する",
    "parent": None,
    "milestone": "M1 片脚 Go/No-Go",
    "labels": ["area/印刷", "prio/P1", "res/プリンタ", "skill/プリンタ操作", "type/タスク"],
    "blocked_by": ["GH-104", "RV-09"],
    "project_status": "Blocked",
    "project_lane": "E4 脚・歩行",
    "project_status_reason": "実寸と頭内干渉の両方が成立するまで、ヨーカップの版を固定しない。",
    "acceptance_condition": "#104と#90の成立後、ld220_cup_yaw 4個の底面固定・PCAクリアランス・頭内干渉を実物と実形状で確認する",
    "next_step": "底面穴を実測し、#90の頭内干渉とPCA下段逃げを分けて1個の実形状で検査する",
    "evidence": ["docs/print-first-manifest.json", "docs/print-first-orientation-manifest.json", "docs/audits/20260905-round2/xiao-retention-plan.json"],
    "progress": "実寸と頭内干渉の確認待ち",
  },
  {
    "key": "GH-107",
    "issue_number": 107,
    "title": "[不具合] servo_frame のタブ用スロットに天井 4.1mm が残り、DS3218 のタブが物理的に入らない",
    "parent": None,
    "milestone": "M0 準備完了",
    "labels": ["area/CAD", "prio/P1", "type/不具合", "並行作業OK"],
    "blocked_by": [],
    "project_status": "Ready",
    "project_lane": "E3 電装",
    "project_status_reason": "DS3218用スロットの修正はLD経路から独立して着手でき、同じframeを使う場合だけ境界を記録する。",
    "acceptance_condition": "DS3218タブが入るservo_frameスロットの修正結果をLD-220MGカップ経路と別判定にし、各STL・断面・実挿入を確認する",
    "next_step": "DS3218指定経路を先に1個で実挿入し、LD経路との共有面だけを別記録して不要な依存を作らない",
    "evidence": ["docs/print-first.md", "hardware/src/make_ld220_adapter.py", "tools/check_leg_assembly.py"],
    "progress": "スロット修正と実挿入確認待ち",
  },
  {
    "key": "GH-108",
    "issue_number": 108,
    "title": "[不具合] シャーシ後脚ヨーサーボのタブ座の内側端が PCA9685 下段基板・ボスの真下にあり干渉する",
    "parent": None,
    "milestone": "M0 準備完了",
    "labels": ["area/CAD", "area/電装", "prio/P1", "type/不具合", "並行作業OK"],
    "blocked_by": ["RV-09", "RV-11"],
    "project_status": "Blocked",
    "project_lane": "E3 電装",
    "project_status_reason": "頭部ケースと電装境界の根拠を揃えるまで、後脚ヨーの逃げを固定しない。",
    "acceptance_condition": "後脚ヨーとPCAの接触が実形状で0となり、U字逃げ・電装境界・配線を組立状態で記録する",
    "next_step": "#90/#92のケースと電装境界を確認し、ld220_cup_yawのU字逃げを組立で測る",
    "evidence": ["docs/audits/20260905-round2/xiao-retention-plan.json", "docs/print-first-manifest.json", "tools/check_static_assembly.py"],
    "progress": "ケース・電装境界の確認待ち",
  },
  {
    "key": "GH-109",
    "issue_number": 109,
    "title": "[要判断] LD-220MG のホーン (金属円盤?) と tibia/femur/coxa のホーンポケット (シングルアーム 32×9.5 前提) の整合",
    "parent": None,
    "milestone": "M0 準備完了",
    "labels": ["area/CAD", "prio/P0", "type/要判断"],
    "blocked_by": ["GH-102", "GH-104"],
    "project_status": "Blocked",
    "project_lane": "E4 脚・歩行",
    "project_status_reason": "設計変更の要否と対象個体のホーン寸法を同じ測定根拠で判断する。",
    "acceptance_condition": "公式に明示されない中心ねじ・PCD・候補寸法はUNVERIFIEDのまま、#102の固定方法と#104の寸法を基準に対象サーボ現物1個と任意3種治具で適合を測る",
    "next_step": "#102/#104の公式値と現物計測値を分け、ld220_cradle/cap/horn_adapterは完成機へ組み込まない寸法確認具として扱う",
    "evidence": ["docs/print-first-manifest.json", "docs/additional-printing.md"],
    "progress": "ホーンとポケットの適合判断待ち",
  },
)


def extra_issue_map() -> dict[str, dict]:
  """Return a defensive copy for callers that add generated fields."""
  return {row["key"]: deepcopy(row) for row in EXTRA_ISSUES}


def reference_dependency_graph() -> dict[str, set[str]]:
  """formal blocked_by と受入/次作業の #番号参照を同じ向きで集約する。

  参照は「この課題が先行課題を待つ」向きに扱う。単なる説明文の
  既知Issue参照も、循環を見落とさないため検査上は依存候補へ含める。
  """
  by_number = {row["issue_number"]: row["key"] for row in EXTRA_ISSUES}
  graph: dict[str, set[str]] = {row["key"]: set(row["blocked_by"]) for row in EXTRA_ISSUES}
  # Existing #3--#98 keys are terminals for this focused extra-issue cycle
  # check; their own graph is validated by plan.py.
  for dependency in {key for values in graph.values() for key in values}:
    graph.setdefault(dependency, set())
  for row in EXTRA_ISSUES:
    text = f"{row['acceptance_condition']} {row['next_step']}"
    for number_text in re.findall(r"#(\d+)", text):
      key = by_number.get(int(number_text))
      if key and key != row["key"]:
        graph[row["key"]].add(key)
  return graph


def validate_reference_cycles() -> None:
  """正式依存だけでなく受入条件/次作業内参照を含めて循環を拒否する。"""
  graph = reference_dependency_graph()
  visiting: set[str] = set()
  visited: set[str] = set()

  def visit(key: str, path: list[str]) -> None:
    if key in visiting:
      cycle_start = path.index(key) if key in path else 0
      raise AssertionError("acceptance/reference cycle: " + " -> ".join(path[cycle_start:] + [key]))
    if key in visited:
      return
    visiting.add(key)
    for dependency in graph[key]:
      visit(dependency, path + [dependency])
    visiting.remove(key)
    visited.add(key)

  for key in graph:
    visit(key, [key])
