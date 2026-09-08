# GitHub Issue / Project 最新確認と追記案（2026-09-06）

**状態: `REVIEW_REQUIRED_APPEND_ONLY_NO_EXTERNAL_UPDATE`。** `gh` の読み取り再取得とローカル計画生成だけを行い、commit/push/Issue/Project外部更新は行っていない。各行は候補案で、最終freeze2根拠と最終commit/PRの代用ではない。

## 最新読み取り

- Issueは **107件（#3〜#109）**、OPEN **102件**、CLOSED **5件（#81/#82/#83/#84/#93）**。Closedは再openしない。
- Project #2「Tachikoma 物理製作」は現在 **96項目**。Status読戻しは Blocked 55 / Done 5 / In Progress 13 / Ready 15 / Todo 8。#99〜#109の11件は未掲載。
- 現在のProject掲載項目の正式依存173辺（公開後履歴156辺）を保持。新規本文に含まれる関連番号14件は候補として記録し、正式依存辺へは追加していない。
- 今回の読み取りはメタデータだけを再取得した。Issue本文・トークン・外部記録は候補記録へ保存していない。

## 全107件の個別追記案

JSONの `all_issue_append_only_update_plan` に #3〜#109 の107行を保存し、各行へ固有の `acceptance_condition`、`acceptance_next_step`、`acceptance_evidence_refs`、候補証拠束の状態を付けた。受入条件は **107件すべて固有**で、共通定型文だけの追記案ではない。

外部反映時は、既存Issueの本文・タイトル・状態・担当・コメント・ラベルを保持し、各行の受入条件に沿った一度の追記だけを行う。#99〜#109は個別に追加し、Project #2へ全107件を掲載して各Status・分類・依存をreadbackする。

外部追記の共通前提はJSONの `final_update_template` に未記入欄として保存した。最終commit SHA、draft PR URL、最終freeze2台帳SHA、根拠索引SHAが揃うまで、107行の候補文をそのままコメントへ使用しない。

## 印刷優先設計の候補証拠

- 現行assemblyからの設計必要数は **56**、初回試作内数 **31**、残り **25**。条件付き新規脛殻4を含む最大は **60**。既存脛殻4の局所加工と新規4は択一で、LD治具3種は本番へ加算しない。
- 印刷解放はA/B/Cに分ける。Aは初回全体仮組み31個（全体仮組み用body16＋左右/前後の固有脚部品10＋1脚分足裏5（TPU靴1＋スペーサー4））で、その内側の実物適合の最小対象が1靴・1脚。BはAの合格後に進める残り25個、Cは既存脛殻4個の局所加工・再使用が不成立の場合だけ選ぶ新規4個（完成機最大60個）です。A＋B=56個で設計必要数56個に一致し、CはA/Bへ加えません。`currently_printable_quantity=0` は現時点の解放未確定を示し、追加印刷全体を不要とする値ではありません。
- `print-first-manifest.json` は `FREEZE2_PENDING_CANDIDATE`、orientationは標準cap **X+90**・鏡像cap **X-90**、実形状の皿頭側上の局所確認を記録している。XIAO候補は基板占有/床/リブ/holder unionだけ+Z4、camera child/lensは固定、FPCのカメラ端固定・基板端+Z4・余長9.2mmを記録している。
- 候補証拠束は `candidate_evidence_bundle` にファイルSHA付きで保存した。最終freeze2根拠が揃うまで、印刷可数0・実在庫・実印刷・実機合格とは区別する。外部追記に必要な最終commit/PR/根拠索引はJSONの `final_update_template` に置いた未記入欄へ記録する。

## 公開境界と実行順

- `outputs/` は再帰追加せず、最終freeze2後にallowlistが個別選択した小さい根拠だけを対象にする。衝突計算キャッシュ、実行形式・ビルド生成物、一時再現束、重複URDF mesh、製造元の原本STEP/STL、絶対パス、外部記録の個人情報は候補から除外する。
- 公開前に最終freeze2成果からallowlist・台帳・説明文を再生成し、`gh auth status`、最新Issue全107件、Project全107件、依存辺、staged file list、秘密/絶対パスをreadbackする。
- root最終レビュー後の順序は normal push → draft PR → 各Issueの個別追記 → Project #2への不足11件追加 → 全107件readback。現時点の外部更新は0件。

詳細JSON: [`github-issues-refresh-20260906.json`](github-issues-refresh-20260906.json)。
