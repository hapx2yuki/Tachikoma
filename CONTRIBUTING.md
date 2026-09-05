# CONTRIBUTING — 物理製作フェーズの作業の取り方

このリポジトリは **GitHub Issues でイシュー駆動 (チケット駆動) に物理製作を進める**。
「チケットなしで作業しない」— 印刷・組立・配線・試験は全て 1 イシュー = 1 成果物 = 1 担当者で回す。
全体図は [docs/build_plan.md](docs/build_plan.md)、計画の正は
[tools/issues/plan.py](tools/issues/plan.py) (GitHub へは `tools/issues/sync_github_issues.py` で同期)。

## 1. 仕組みの全体像

| 軸 | GitHub 上の対応 | 見方 |
|---|---|---|
| **いつ** (Go/No-Go ゲート) | Milestone `M0 準備完了` → `M1 片脚 Go/No-Go` → `M2 頭無し歩行` → `M3 サブアセンブリ` → `M4 フルドレス` → `M5 統合・完成` | Issues → Milestones |
| **何の流れ** (作業ストリーム) | エピック `E1 準備` / `E2 印刷キュー` / `E3 電装` / `E4 脚・歩行` / `E5 腕` / `E6 頭部` / `E7 意匠シェル` / `E8 統合` — 子イシューを **サブイシュー** で束ねる | エピックを開くと進捗バー |
| **順番** (直列/並列) | **blocked by / blocking** (イシューの Relationships) | サイドバー。`is:open -is:blocked` 検索で「今できること」 |
| **誰が・何が要るか** | ラベル `area/*` `skill/*` `res/プリンタ` `res/本体` `並行作業OK` `prio/P0-2` `type/ゲート` `type/要判断` | Labels |
| **見える化** | [Projects v2 ボード「Tachikoma 物理製作」](https://github.com/users/hapx2yuki/projects/2) (Status: Todo / Ready / In Progress / Blocked / Done、レーン = エピック) — Public なのでアカウント無しでも閲覧可 | Projects |

キー体系 (タイトル先頭): `P`=準備 / `PR`=印刷 / `EL`=電装 / `L`=脚・歩行 / `A`=腕 / `H`=頭部 / `S`=意匠シェル / `I`=統合 / `RV`=独立監査。ソフトウェア監査はエピック `E9` にまとめる。
口頭やチャットでは「L-02 どう?」のようにキーで呼ぶ。

## 2. 作業を取る (5 ステップ)

1. **探す**: [Issues](https://github.com/hapx2yuki/Tachikoma/issues) を `is:open -is:blocked no:assignee` で絞る
   (または [Project ボード](https://github.com/users/hapx2yuki/projects/2) の **Ready** 列)。自分の得意に合わせて `label:skill/はんだ` `label:並行作業OK` `label:"good first issue"` で更に絞る。
   - `res/プリンタ` の付いたものは **同時に 1 件だけ** (X2D は 1 台)。順番は E2 印刷キューのエピック本文
   - `res/本体` の付いたものは本体を占有する。同時に取るときは先に着手している人と調整
2. **宣言する**: イシューにコメント「担当します (いつ頃)」→ 自分を **Assignee** に。宣言なしで着手しない (二重作業防止)。
   Assignee は **1 人**。手伝う人はコメントで参加を書く
3. **やる**: 手順は本文とリンク先 docs (`docs/assembly.md` / `docs/wiring.md` / `docs/printing.md` / `docs/print_manifest.md`)。
   迷ったこと・決めたことは **チャットではなくイシューにコメント** (チャットで決めたら要点を転記)
4. **証拠を残す**: 完了条件 (DoD) のチェックボックスを埋め、**写真 / 動画 / 実測値 / ログ** をコメントに添付。
   「できたはず」「確認済み」だけの Close は不可 (このプロジェクトの鉄則: 実測・実出力で示す)
5. **閉じる**: DoD が全部埋まり証拠が付いたら Close。物理タスクは PR を伴わないので直接 Close でよい。
   docs を更新する PR があるなら `Closes #N` を PR 本文に

## 3. イシューを新しく切るとき

- テンプレートを使う: **印刷ジョブ** / **組立・配線ステップ** / **不具合・現物合わせ NG** / **要判断**
- 粒度: **1 人が半日〜1 日で終えられ、pass/fail で判定できる** 単位。「脚を全部作る」は大きすぎる (脚 1 本 = 1 イシュー)
- タイトル: `[領域] 動詞+目的語` (例: `[印刷] PETG_Walk_3_Tibia (tibia×4) を印刷する`)。計画由来のものはキー付き
- 前提があれば **Mark as blocked by** で明示。親ストリームがあれば **サブイシューとして追加**
- 現物が設計と合わないときは **削る前に `不具合` テンプレで報告** (見える意匠パーツの無断改変は禁止。内部加工は可)

## 4. 要判断 (オーナー決定) の扱い

`type/要判断` はオーナー @hapx2yuki が決める。決定は **理由付きでイシューのコメントに残してから Close**。
待っている側は `blocked by` でその要判断イシューを指しておく (待ち時間に別の Ready を取る)。

## 5. やってはいけないこと

- ゲート (`type/ゲート`: 片脚 1.2kg リフト、頭無し歩行) を飛ばして下流へ進む
- サーボ実測前にサーボ寸法依存の骨格を印刷する (`P-04` 完了が条件)
- `AP_PASS` (Wi-Fi パスワード) や API キーをコミットする — firmware/src/config.h の実値はローカルのみ
- チャットだけで決めて記録を残さない / 証拠なしで Close する / 1 つのイシューに複数の成果物を詰める
- 標準 / ミラー (`_m`, `_L`) を取り違える (マーキング必須)

## 6. 計画そのものを直したいとき

`tools/issues/audit_plan_data.py` の個別本文・依存（共通の組立ては `plan.py`）を編集 → `.venv/bin/python tools/issues/sync_github_issues.py --dry-run` で差分を見て
`--apply` (新規作成・関係追加) / `--apply --update-bodies` (本文も上書き) / `--plan-doc` (docs/build_plan.md 再生成)。
変更するIssueだけ `--keys L-11 EL-09` のように限定する。管理内の不要依存を取り除くときは、
対象を限定した `--reconcile-dependencies` を付け、先に `--dry-run` を確認する。
既存Projectへの追加は `tools/issues/setup_project.sh`（差分表示）→ `--apply`。
同じProjectを再利用し、既存のStatus・担当・コメント・フィールド選択肢を保持する。
Ready/Blockedは完了済み前提の実状態で初期設定する。既存Statusは自動追従させず、作業担当が更新する。
2026-09-05 は全件更新の依頼に基づき、[96課題の見直し](docs/issues-audit-20260905.md) と実際の未完了依存を照合して Status を更新する。機構の候補検討中と実機完了を区別し、完了済み5件以外は閉じない。
GitHub 上で直接本文を書き換えても次の `--update-bodies` で戻るので、恒久的な変更は plan.py へ。
個別の追加イシュー (不具合・現物合わせ) は GitHub 上で直接切ってよい (plan.py の管理外)。

### 2026-09-05 監査後の順序

- 制御修正・印刷データ検査・物理シム・Issue管理は別ファイル担当で並行する（E9）。
- `PR-04` はFL用tibia標準1本 → `L-02` 片脚合格 → `PR-11` 残り3本 → `L-03/04/05` 組立。
- `L-11` トルク不足対策、`EL-09` 電源不足対策は、対象試験が不合格でも着手できる。ゲートのClose待ちにしない。
- コードのローカル検証、GitHubへのコード反映、実機試験の完了を別々に記録する。監査だけで実機IssueをCloseしない。

## 7. 参考: なぜこの運用か

- チケット駆動開発 (TiDD, 2007 年〜) の「No Ticket, No Commit」と GitHub Flow の「issue first」を物理作業に転用
- 1 台のプリンタ・1 台の本体という **占有リソース** をラベルで明示し、それ以外 (`並行作業OK`) を複数人で同時に進める
- 依存は本文の文章ではなく **blocked by** に持たせて機械的に「今できること」を出す (`is:open -is:blocked`)
- 証拠 (写真・実測) をイシューに残すことで、後から `docs/` へ知見を戻せる (`I-06`)
