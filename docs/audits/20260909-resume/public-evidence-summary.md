# 2026-09-09 WIP公開根拠要約

確認日: **2026-09-09（JST）**。これは作業途中の公開用要約です。実機・最終print-firstは未完了であり、公開は機構認定、量産許可、実機完成を意味しません。

## 生成と保護

- `tools/generate_print_first.py` は終了コード0、続く `--validate-only` も終了コード0でした。
- 生成結果は86パーツ・23リンク、visual STL 56枚、collision STL 24枚、URDF参照メッシュ80枚です（`run_id=d93022334ee143b68ee0d06646e8f40e`）。Cabin保管後のruntime台帳は29源部品、合計587.3919603718443 g、追加保管568.7360089849115 gです。
- 生成前後の対象ソースは236件でSHA差分0、履歴3MFは26件すべて保持・SHA一致でした。共有原本の上書きはありません。
- 候補の状態は形状・契約検査の範囲です。印刷、組立、通電、歩行、材料強度は未確認です。

生成器と現行候補の入口は [generate_print_first.py](../../../tools/generate_print_first.py)、[現行候補URDF](../../../hardware/urdf-print-first/tachikoma.urdf)、[部品台帳](../../../hardware/urdf-print-first/parts_manifest.json) です。

## Cabin保管後のformal境界

最新のCabin保管後 formal stand/T0 は、事前検査296件がPASSし、入力ソース304件の前後差分もありませんでした。battery経路は通過しましたが、`Mouth_Cannon_Grey` の被覆検査で `16007/19618 = 0.8159343460087675`、許容距離0.011 mmとなり、`mj_step`前に停止しました。静止姿勢・歩行・実機の合格結果はありません。

同じ候補を部品別に分けた診断は144件で、51 PASS、87 FAIL、6 TIMEOUTでした。FAILの主因はVHACDの表面被覆・検証変換で、87件を機体の欠陥数へ読み替えません。未解決の部品別契約と、Cabinを保管した現行全機構成の物理検証は後続の再検査が必要です。

現行衝突メッシュで自己衝突を有効にした静止診断は、stand/T0が `INVALID_INITIAL_CONTACT_MODEL` となりました。4.4秒の数値積分自体は完了しましたが、初期自己貫入の最大は `28.1084 mm`（`leg_fr_coxa|leg_fr_femur`）、最大姿勢変位は roll `24.9095°`、pitch `11.5022°` でした。別の26秒歩行・自己衝突なし診断も `FAIL` で、有限積分は完了したものの、接触モデルの限界を示す診断です。いずれも厳密なVHACD契約、機構欠陥、歩行合格の根拠にはしません。

formal記録の `FINAL_FROZEN` は入力ファイルのSHA固定だけを表します。物理合格や採用決定ではありません。Cabin源部品は保管し、`pf_cabin_rail_l/r` の一体電装支柱・棚・battery・chassisは維持する方針です。保管による追加軽量化は約568.736 gで、既保管18.656 gを二重に差し引きません。追加購入方針は0件ですが、現物在庫・不足ゼロは未確認です。

CoACDの一部品調査は [公開要約JSON](mouth-cannon-coacd-public-summary.json) にコピーして記録しています。状態は `DIAGNOSTIC_ONLY`、`formal_gate_changed=false` です。既定実行は0.011 mmの標本被覆証明に到達した一方、source外Boolean体積 `2345.808177 mm3`、標本境界偏差 `2.254526 mm` を残し、実メートル条件の2候補は時間切れでした。この1部品調査から全体の衝突近似へ採用しません。

## Mesh64の数値修正

`tools/mesh_checks.py` はBoolean結果をfloat64とnative `Mesh64`で再構成し、符号・有限値・閉形状を確認します。speaker pocketの修正後値は `9.873275985228247e-06 mm3` で、修正前float32再構成は原点依存の値になりました。この記録はBoolean計算の再現性を示す数値検査であり、隙間、印刷適合、物理合格を示しません。実装と回帰は [mesh_checks.py](../../../tools/mesh_checks.py) と [test_mesh_checks.py](../../../tools/tests/test_mesh_checks.py) にあります。

## 頭候補と薄肉

現行の頭候補は `pf_head_top_clearanced` と `pf_eye_pod_camera_clearanced` を使います。旧 `Head_Top_Eyecut` と `pod_neck` の89.23001 mm3交差は旧構成の履歴値です。現行 `A.context(generated=True)` は旧 `pod_neck` を収集せず、頭候補と `pf_chassis` の実メッシュ交差0.0 mm3、目穴3/3開口を確認しました。

薄肉判定は1.25 mm内側プローブで10176/10879=93.538%。失敗703点は開口・切断端59、原本側浅部・端部61、現行の設計逃げ583、未分類0でした。開口・逃げを除いた実壁の最小は1.923876 mm、支持座の最小実断面は50.5659 mm2です。実サーボ、カメラ、積層強度、荷重、上下殻接合、実印刷面は未確認です。設計判断の背景は [第2次監査](../20260905-round2/README.md) と [XIAO保持候補](../20260905-round2/xiao-retention-plan.json) にあります。

## 個数と残課題

設計必要数は **56個（A31+B25）**、Cは既存脛殻加工が不成立の場合だけの条件付き4個です。LD-220適合治具3種は各1個の非本番確認具です。追加購入方針は0件ですが、手持ち材料と良品数は未確認です。設計必要数と現在印刷可能数を混同しません。台帳は [print-first説明](../../print-first.md)、[追加印刷](../../additional-printing.md)、[追加購入](../../additional-purchases.md) です。

現行候補のbody構成では、[collector回帰要約](collector-regression-summary-20260909.json)に記録した最新body検査は、native trace 20軸・12区間・全1141行を読み、`pose_rows_read=1141` と `pose_rows_checked=1141`、`fixed_geometry=true`、`dynamic_geometry=true`、交差・エラーなしで `status=DYNAMIC_PASS` となりました。元の`final-body-mesh.json`はローカル監査出力として保存しています。これはbody構成の1ケースについて全1141姿勢を同じ実メッシュへ束縛した結果です。材料別VHACD・購入サーボ外形・電圧群の適合、全構成の別ケース、実機は未確認であり、`physical_readiness=UNVERIFIED` のままです。最終freeze2はWIP公開の前提ではなく、同一SHA台帳による後続の全物理ゲートが必要です。

## 旧来検査の集計

現行候補とは別に、`legacy-verify-20260908T184106Z/verify-results.json` の旧来検査23件は **PASS 14件 / FAIL 8件 / UNVERIFIED 1件**でした。FAILは旧static、arm、leg、head-pod、URDF、print-artifacts、toe、strengthの各検査で、powerはUNVERIFIEDです。この集計は旧構成の回帰検査結果を示すもので、body全1141姿勢のprint-first `DYNAMIC_PASS`とは別の結果です。実機適合・印刷強度・電源能力を確定しません。

公開候補の選定・SHA・依存差分は [publication-allowlist.json](publication-allowlist.json) と [WIP Issue計画](github-issues-wip-comment-plan.md) に記録しています。GitHubへの外部書込み、stage、commit、pushはこの記録作成時点で行っていません。
