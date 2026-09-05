# 統合検査・課題分割の独立レビュー

2026-09-05。対象は `check_static_assembly.py`、`run_design_audit.py`、`kit_assembly.py` の頭部配置修正、`tools/issues/plan.py` の RV-13/14/15 と関連依存、`build_audit_coverage.py`。本レビューでは本流の CAD・制御・物理計算を編集していない。修正は親担当が実施し、独立担当が負例を再実行した。

| 判定 | 内容・再現・根拠 |
|---|---|
| 修正後の負例合格 | 旧 static 検査は base_link のみを対象とし、arm_r_upper 内の完全重複2箱も PASS。新版は全リンク内の組合せを走査し、欠落リンク・空の実体リンクを失敗とする。空の camera_optical_frame は意味のある例外。|
| 修正後の負例合格 | 旧 runner は既存 coxa-sweep.log で FileExistsError が出ても他20コマンドを実行し、結果JSONは1件だけ残った。新版は実行前に空出力先を要求し、起動例外も ERROR にして全件保存。生成失敗後は後続生成を行わない。|
| 調整を依頼 | static の結果ハッシュに判定関数の `mesh_checks.py` が未収載。runner に今回追加した audio_assembly / arm_joints / mouth_chassis / kit_transforms と test_coxa_sweep / test_integration_audit を追加するよう親担当へ報告。|
| 合格・現行配置形式に限定 | 頭Top/Bottomの y を config.ARM_MOUNT_HUB_Y とする修正は、11と7.3の変更で子座標系との一致が実回帰で成立。現JSONの単一・t形式を壊す挙動は認めない。将来頭をinstances形式へ移す場合はインスタンス個別t/matrixにも同じ規約が必要。|
| 依存循環なし | 全 blocked_by に加え、親課題が子の完了を待つ辺も加えたDFSで循環0。RV-13/14はRV-09の軸確定後、RV-15とH-06/RV-07は測定を並行できる。共通make_chassis.pyの同時編集禁止が本文にある。|
| 印刷依存の不足を報告 | PR-06はpod_neckとeye_carrierを含むがP-04/RV-10だけを前提とする。RV-15/RV-09で未確定の部品をプレートから明示除外するか依存を追加するよう親担当へ報告。既存印刷済み部品を削除する操作は行っていない。|
| 網羅記録の範囲を確認 | build_audit_coverage は開始時515ファイルを対象に現hashを照合する。実行時は17ファイルの更新待ちで非0を返した。追加ファイルは名前一覧のみで現hashの照合対象外であり、これを現在のrepo全ファイルに対する合格と読んではいけない。記録存在は欠陥不存在の証明でもない。|

## 実形状への影響

base以外の97 AABB候補を独立にBoolean検査し、0.01mm³を超える60組を記録した。各coxaケース1563.87mm³、thigh_capと大腿装飾1211.94mm³、shin_shellと脛装飾1146.76mm³、upper_armとelbow_shell370.60mm³、elbow_shellと肘ケース402.79mm³が含まれる。これは「すべて不可能な嵌合」と断定した表ではなく、固定リンクの体積共有を検査から隠していた事実を示す。部品ごとの干渉原因は本流担当へ引き継いだ。ペグ・足の差込みなどを根拠なく無視していない。

## 実行記録

- `integration-peer-reproductions.json`: 修正前の偽PASSと例外による結果欠落。
- `integration-peer-nonbase.json`: 現行STLのbase以外全組合せの結果。後続のcoxa等修正前の履歴。
- `integration-peer-tests.log`: 新規 `tools/tests/test_integration_audit.py` の7試験合格。
- 頭位置回帰: `python -m unittest tools.tests.test_render_contracts.RenderContracts.test_head_shell_follows_config_and_matches_child_frame -v`、1試験合格。

実機は駆動していない。新規回帰のrunnerコマンドは模擬実行であり、全CAD生成や全物理条件を実行したという意味ではない。今回の7合格は検査器の負例検出と結果保全に対するもの。

## 最終再照合

親担当がstaticのmesh_checks.pyハッシュ追加、PR-06のP-07/RV-09/RV-15前提追加、runnerのmouth_chassis/kit_transformsと負例2群収載を実施。audio_assemblyとarm_jointsは既存check_audio/check_armから呼ばれており統合対象に含まれることを確認。網羅記録は追加ファイルの現hashも照合するよう改訂され、保存ZIP内部を通常パスとして扱う不具合はcurrent_digestの実ZIP読取りへ修正された。ZIP内容変更・欠落member・壊れたZIPを含めた最新8負例はPASS。親の子完了待ちを加えた現課題依存も循環0。
