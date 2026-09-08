# GitHub 107件 WIP追記案（2026-09-09）

この文書と同名JSONは、Issue #3〜#109の107件へ将来追記するコメントの作業案です。JSONの`issue_rows`には107件すべての個別`comment_draft`を収録しています。外部投稿は行っていません。

GitHub内のURLは暫定的に [`codex/print-first-20260905`](https://github.com/hapx2yuki/Tachikoma/tree/codex/print-first-20260905) を指します。公開直前に実際の公開commitへ置き換えます。

## 現在地

- 監査対象はIssue #3〜#109の107件。ライブ状態はOPEN 102件、CLOSED 5件、Project #2は96件、正式な依存辺は156本です。これは投稿準備の読み取り結果で、外部への書き込みはしていません。
- 生成・validate-onlyは終了コード0ですが、実機完成を示しません。source 236件と履歴3MF 26件のSHA一致、86部品／23リンク／視覚STL 56件／衝突STL 24件の記録があります。
- 最新Cabin formalは事前検査296件PASS、source 304件不変の後、battery通過後にMouth_Cannon_Greyの表面被覆0.815934…で`mj_step`前停止しています。
- 現行証跡には全1141 trace／20軸12区間とbody fullmesh `DYNAMIC_PASS`（固定動的交差0・error0）があります。別schemaの全linkpair／厳密な動的物理は未完了です。
- 近似診断は自己衝突OFFで26秒実行し転倒なしでしたが、後退4.557 mm/sは既存5 mm/s基準未満です。正式な動的合格には使いません。
- 追加購入方針0件、印刷候補56件（A31+B25）、C条件付き4件を維持します。現物在庫・不足は未確認です。
- 初期歩行候補ではCabin殻・装飾・peg・turretを保管し、`pf_cabin_rail_l/r`（電装支柱一体）・棚・`battery_cradle`・`pf_chassis`を維持します。ねじ・配線の露出は実物確認事項です。

## 共通コメント形式

107件の本文を、共通短文に続く箇条書きへ整形しました。従来計画に由来する個別受入条件は旧仕様として原文を残し、現行候補の採否条件と分けています。近似診断の細数値は、該当する#55/#78/#80/#96だけに追記しています。

```text
WIP監査記録（2026-09-09）。印刷優先構成と追加購入方針0件（現物在庫・不足は未確認）を確認中。実機・最終print-firstは未完了。以下はWIP整理であり、機構認定・量産許可・実機完成を示さない。公開監査README: https://github.com/hapx2yuki/Tachikoma/blob/codex/print-first-20260905/docs/audits/20260909-resume/README.md
- 現在確認: {issue_specific_focus}。
- 受入条件（従来計画。現構成への適用は以下参照）: {acceptance_condition}。
- 次作業: {acceptance_next_step}。
- 依存（ライブ）: 親 #x / blocked by #y。
- 計画との差分: 計画との差分なし。
- Project #2: In Progress / E7。
- 根拠: 指定された公開ファイルURL。
```

## 整形例

### #55

```text
WIP監査記録（2026-09-09）。印刷優先構成と追加購入方針0件（現物在庫・不足は未確認）を確認中。実機・最終print-firstは未完了。以下はWIP整理であり、機構認定・量産許可・実機完成を示さない。公開監査README: https://github.com/hapx2yuki/Tachikoma/blob/codex/print-first-20260905/docs/audits/20260909-resume/README.md
- 現在確認: 設計課題「H-06 [CAD] Cabin内部の電装室・基板保持・挿入経路・配線を成立させる」: 実メッシュ/実測による成立条件を確認し、局所判定を全体完成へ読み替えない。
- 受入条件（従来計画。現構成への適用は以下参照）: 全体干渉・基板挿入/脱着・保持/熱/通線の検査と候補採否。変更質量/重心をI-08、印刷影響をP-05へ渡す。
- 次作業: 購入基板・USB/電源端子を実測し、RV-15と境界寸法を共有して部品配置/棚/保守経路を修正する。ESP32/PCA/電源を収め、短FPCのXIAOはH-02の頭内保持へ残す。
- 固有監査: 最新Cabin formalは事前検査296件PASS、source304件不変、battery通過後にMouth_Cannon_Greyが16007/19618＝0.815934...でmj_step前停止した。
- 現行診断補足: 全1141 trace／20軸12区間と body fullmesh `DYNAMIC_PASS`（固定動的交差0・error0）を保存済み。別schemaの全linkpair／厳密な動的物理は未完了。26秒の自己衝突OFF近似は転倒なしだが、後退4.557 mm/s < 既存5 mm/sで移動量ゲート不合格。
- 依存（ライブ）: 親 #8 / blocked by なし。
- 計画との差分: 計画との差分なし。
- Project #2: In Progress / E6 頭部。
- 根拠: https://github.com/hapx2yuki/Tachikoma/blob/codex/print-first-20260905/docs/audits/20260905-round2/cabin.md
```
### #90

```text
WIP監査記録（2026-09-09）。印刷優先構成と追加購入方針0件（現物在庫・不足は未確認）を確認中。実機・最終print-firstは未完了。以下はWIP整理であり、機構認定・量産許可・実機完成を示さない。公開監査README: https://github.com/hapx2yuki/Tachikoma/blob/codex/print-first-20260905/docs/audits/20260909-resume/README.md
- 現在確認: 設計課題「RV-09 [機構不具合] 頭部内の実サーボケースとポッド梁の干渉を解消する」: 実メッシュ/実測による成立条件を確認し、局所判定を全体完成へ読み替えない。
- 受入条件（従来計画。現構成への適用は以下参照）: 全ケース/眼/基板/梁の同時成立、壁厚/挿入/工具/配線の根拠と採否。固定軸をRV-13/14/10へ渡し有限失敗を不可能の証明と呼ばない。
- 現行受入確認: 実購入寸法を反映したケース・眼・基板・梁の実メッシュ干渉、印刷支持、工具／挿入、電装配線、取付強度を同一候補で確認する。
- 次作業: P-03/P-07の実購入寸法を照合し、印刷支持・目／カメラの電装配線・実メッシュ干渉・取付強度を同時に確認する。必要なら外観変更を候補に含める。
- 旧仕様の扱い: 「外観を保つ」「内部削りのみ」は旧完全意匠の履歴条件として保留し、現行採否へ使わない。
- 固有監査: 旧Head_Top_Eyecut対pod_neckの89.23001 mm3は旧構成の履歴値で、現行集合は旧pod_neckを収集しない。
- 依存（ライブ）: 親 #9 / blocked by なし。
- 計画との差分: 計画との差分なし。
- Project #2: In Progress / E7 意匠シェル。
- 根拠: https://github.com/hapx2yuki/Tachikoma/blob/codex/print-first-20260905/docs/audits/20260905-round2/simulation.md
```
### #96

```text
WIP監査記録（2026-09-09）。印刷優先構成と追加購入方針0件（現物在庫・不足は未確認）を確認中。実機・最終print-firstは未完了。以下はWIP整理であり、機構認定・量産許可・実機完成を示さない。公開監査README: https://github.com/hapx2yuki/Tachikoma/blob/codex/print-first-20260905/docs/audits/20260909-resume/README.md

- 現在確認: 設計課題「RV-15 [機構不具合] 首・Cabin・装飾を全長で組み合わせ、支持と挿入を成立させる」: 実メッシュ/実測による成立条件を確認し、局所判定を全体完成へ読み替えない。
- 受入条件（従来計画。現構成への適用は以下参照）: 首/Cabin/Eye/装飾/電装の全体交差と挿入・通線・保守・段階荷重の記録。既存11候補を採用済みとしない。
- 現行受入確認: Cabin殻・装飾・peg・turret保管後の pf_cabin_rail_l/r（電装支柱一体）・棚・battery_cradle・pf_chassisについて、支持・電装配線・実測着脱・全mesh干渉・段階荷重／印刷強度を確認する。
- 次作業: H-06について、現行頭支持座・シャーシ・棚の固定、配線経路、取り出しを実測し、保管後の支持部品・配線・全mesh・強度を同一候補で検証する。
- 旧仕様の扱い: 「外形を保つ内側加工」「首/Cabin/Eye/装飾/電装を全て組む」は旧完全意匠の履歴条件として保留し、現行採否へ使わない。
- 固有監査: 部品別診断は144件で51 PASS／87 FAIL／6 TIMEOUT。FAILは主にVHACD表面被覆・検証変換で、機体欠陥87件とは読まない。Cabin殻・装飾・peg・turretを保管し、pf_cabin_rail_l/rの一体電装支柱・棚・battery_cradle・pf_chassisを維持する方針である。
- 現行診断補足: 全1141 trace／20軸12区間と body fullmesh `DYNAMIC_PASS`（固定動的交差0・error0）を保存済み。別schemaの全linkpair／厳密な動的物理は未完了。26秒の自己衝突OFF近似は転倒なしだが、後退4.557 mm/s < 既存5 mm/sで移動量ゲート不合格。
- 依存（ライブ）: 親 #9 / blocked by なし。
- 計画との差分: 計画との差分なし。
- Project #2: In Progress / E7 意匠シェル。
- 根拠: https://github.com/hapx2yuki/Tachikoma/blob/codex/print-first-20260905/docs/audits/20260905-round2/neck-reinforcement.md
```

## #90/#96の旧前提の扱い

- #90の「外観を保つ」「内部削りのみ」は、旧完全意匠の履歴条件としてコメントに残し、現行採否から外しました。現行は印刷支持、電装配線、実メッシュ干渉、実測、取付強度を確認します。
- #96の「外形を保つ内側加工」「首/Cabin/Eye/装飾/電装を全て組む」も旧完全意匠の履歴条件として保留しました。現行はCabin殻・装飾・peg・turretを保管し、電装支柱一体の`pf_cabin_rail_l/r`、棚、`battery_cradle`、`pf_chassis`の支持・配線・着脱・全mesh・強度を確認します。
- `source_plan`の受入条件と、現行印刷支持・電装配線・実測・全mesh・強度による判定は、JSONの`acceptance_semantics`でも区別しています。

## 個別の監査値

| Issue | 追記する固有情報 |
|---:|---|
| #12 / #15 / #27 | 追加購入方針0件、印刷候補56件（A31+B25）、C条件付き4件。必要数と現物不足は未確認。 |
| #55 | Cabin formalの停止値と、全trace/body fullmeshの現行診断補足。 |
| #78 / #80 | 部品別診断144件の集計、全trace/body fullmesh、自己衝突OFF近似の位置づけ。 |
| #96 | 部品別診断、Cabin保管後の現行支持構成、全trace/body fullmesh、旧仕様の履歴保留。 |
| #90 | 旧Head_Top_Eyecut対pod_neckの値を履歴として保持し、現行の支持・配線・実測・全mesh・強度へ切り替え。 |
| #62 / #82 | 現行頭候補の根拠と履歴3MFのSHA記録。 |

## 依存と公開順序

JSONの各`formal_dependency`にあるライブ親・ライブblocked byを本文へ転記しました。計画にだけ残る依存は「計画との差分」に明記し、ライブの依存関係へ先回りして追加していません。Project #2の未掲載行は公開順序で追加予定と表示します。

投稿順序は、まず監査READMEを実commitへ置いた後、JSONの`publication_order`に従います。URLのブランチ置換と外部投稿は、この作業案の範囲外です。
