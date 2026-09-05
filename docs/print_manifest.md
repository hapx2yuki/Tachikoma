# 印刷マニフェスト — 全パーツ悉皆リスト・照合レポート

**2026-09-05 第2次監査:** この手順の旧合格記録は製作許可を意味しない。現在は足の嵌合/支持、頭内収納と固定、ポッド梁に未解決項目がある。
先に[最新監査](audits/20260905-round2/README.md)と[部品別の変更](audits/20260905-round2/manufacturing.md)を確認する。既存3MFは保存してあり、修正済みSTLとの不一致を含む。

`docs/printing.md` (印刷設定の正) / `docs/assembly.md` (組立手順の正) と
1:1 照合し、「印刷すべき全パーツ」を一覧化する担当ドキュメント。齟齬は
発見の都度 `printing.md`/`assembly.md` 側を修正済み (本書末尾「発見した
齟齬と修正」参照)。

**検証方法**: 目視突合せに加え、以下を実行し機械的に確認した。

**⚠ 2026-07-31 QA 再検証 (2回目) で更新**: 下記チェック結果は同日、新規
プロセスで全チェッカーを再実行した結果に更新。**旧版の本書は
`check_screw_bosses.py`/`check_arm.py` を `result: NG` (「頭部中央寄せ」
タスクで `ARM_MOUNT_HUB_Y` を一時的に `0.0` (完全中央) にした中間状態での
腕肩マウント×前脚coxaブラケット静的干渉) のまま記載していたが、これは
その後の「境界スイープ」タスクで `ARM_MOUNT_HUB_Y=11.0` (完全中央
`0.0` は物理的に不可能と判明した上での実現可能な最中央値) へ確定して以降
解消済みであり、本書がその解消を反映せず更新漏れのまま放置されていた。
現在は両方とも `result: OK` (chassis arm tab fill 72.7%, `check_arm.py`
[1b]クリアランス3.3mm含め全項目OK) — 詳細は `docs/assembly.md`
「頭部中央寄せ」§7「境界スイープによる解決」参照。

```
ls hardware/stl/*.stl          # 41 件 (本書 §1 と 1:1。2026-08-19 eye_pod_camera の印刷用 2 分割 shell/base を追加)
ls model/*.stl                 # 58 件 (本書 §2 と 1:1)
cd hardware/src && ../../.venv/bin/python build_all.py   # 全 watertight=True
.venv/bin/python tools/make_head_eyecut.py                # Head_Top_Eyecut (build_all.py 対象外, 下記参照)
.venv/bin/python tools/check_leg_assembly.py  # OK
.venv/bin/python tools/check_screw_bosses.py  # result: OK (chassis arm tab fill 72.7%>=70%,
                                               #  ARM_MOUNT_HUB_Y=11.0 確定で解消 -- docs/assembly.md
                                               #  頭部中央寄せ§7参照)
.venv/bin/python tools/check_arm.py           # result: OK ([1b]clr 3.3mm・[4]min_d 2mm 含め全項目OK)
.venv/bin/python tools/check_pod_neck_strength.py  # result: OK (真の最弱点 y≈-43.5mm で
                                               #  SF_nominal=8.07, Kt=2.5込み実効安全率3.23
                                               #  -- 2026-07-31 QA 再検証で新規作成。
                                               #  docs/assembly.md 強度検証節参照)
.venv/bin/python tools/check_eye.py           # result: OK
.venv/bin/python tools/check_audio.py         # result: OK
.venv/bin/python tools/check_camera.py        # result: OK
.venv/bin/python tools/check_head_pod_clearance.py  # PASS (clearance 3.779/2.706mm,
                                               #  HEAD_RELIEF_PROTECT_H 8.0mm+テーパー)
.venv/bin/python tools/check_shin_arm_leg.py  # overall = PASS ([C-duty]発火率42.5-44.2%,
                                               #  ARM_LEG_YAW_GATE_DEG=20.0°)
.venv/bin/python tools/check_urdf.py          # 380/380 OK
.venv/bin/python tools/filament_calc.py       # 色別合計が docs/filament.md と完全一致
                                               # (2026-09-04 実行値: 青833/灰189/黒40/白30/赤4/PETG573/TPU2 g
                                               #  — 旧記載 青911/PETG570 は Head_Top_Eyecut ホロー化 (2026-08-22)
                                               #  前の値だった。以下の経緯メモは履歴として残す)
                                               # (2026-07-31 shin_shell 装飾面放射外向き化
                                               #  タスクで ADJ_RELIEF_BANDS 追加により
                                               #  shin_shell 実体積が減り、青が 913→895g へ
                                               #  再度微減。他色は無変化。
                                               #  さらに同日、リリーフカット再評価タスクで
                                               #  KNEE_RELIEF/TIP_RELIEF/ADJ_RELIEF_BANDS を
                                               #  firmware到達可能集合基準で撤去しキット形状へ
                                               #  復元した結果、shin_shell 実体積が増え青が
                                               #  895→911g へ再度増加。他色は無変化。
                                               #  同日 QA 再検証で pod_neck の応力集中対策
                                               #  [HEAD_RELIEF_PROTECT_H 6→8mm+テーパー追加] に
                                               #  より PETG が 569→570g へ微増、あわせて
                                               #  battery_cradle の旧誤記載 13g を実測17gへ訂正)
```

重量は `tools/filament_calc.py` の物理体積モデル (表面積×壁厚+インフィル)
による見積りで、**±30% 目安**(スライサー実測ではない)。本書の
hardware/stl 側重量合計 (§1) は独立に積み上げても **≈1009g**
(2026-07-31 リリーフカット再評価タスクで shin_shell が +17g、旧≈991gから
増加。さらに同日 QA 再検証で pod_neck +1g) となり、`docs/printing.md`
「重量バジェット」節の内訳 (脚骨格+シャーシ PETG ~460g + 腕骨格 PETG ~55g
+ 意匠シェル側 PLA 分) と整合する。

---

## 1. カスタム STL 全数 (`hardware/stl/*.stl`, 41 件, `ls` 順)

凡例: 数量=そのファイルを印刷する枚数。重量は 1 枚あたり / 合計
(qty×1枚)。サポートは `printing.md` に明記があるもののみ引用し、
他は「明記なし」と正直に記載する (スライサーの自動判定に委ねる)。

| ファイル | 数量 | 材料/色 | 壁/インフィル(/レイヤー) | 向き | サポート | 重量目安(1枚/合計) | 組立節 |
|---|---|---|---|---|---|---|---|
| Head_Bottom_Armcut.stl | 1 | PLA 青 | 壁2, 8% | 切断リング面を下 (X軸180°反転, 2026-08-20 機構逃がしカット後) | 明記なし | 26g / 26g | §2.5-1, §2.8-5, §3 |
| Head_Top_Eyecut.stl | 1 | PLA 青 | 壁2, 8%※ | STL のまま (元 Head_Top と同一外観。**2026-08-22 v2: 内殻ホロー化 306→52cm³ + スカートノッチ ×6** — v1 の中実版は電装/サーボと干渉し使用不可) | 明記なし | 64g / 64g | §2.7-5, §2.9-4, §3 |
| Mouth_Ball_Bored.stl | 1 | PLA グレー | 壁2, 8% | STL のまま | 明記なし | 4g / 4g | §2.8, §3 |
| Mouth_Cannon_Bored.stl | 1 | PLA グレー | 壁2, 8% | 砲口を上 (印刷面より先端が出ないよう現物合わせ) | 明記なし (向き自体が現物合わせ対象) | 9g / 9g | §2.8 |
| Mouth_Neck_Bored.stl | 1 | PLA 青 | 壁2, 8% | STL のまま | 明記なし | 3g / 3g | §2.8 |
| arm_pod_lower.stl | 1 | PLA 青 | 壁2, 8% | カット面を下 | 明記なし | 4g / 4g | §2.5-6 |
| arm_pod_lower_L.stl | 1 | PLA 青 | 壁2, 8% | カット面を下 (X ミラー) | 明記なし | 4g / 4g | §2.5-6,7 |
| arm_pod_upper.stl | 1 | PLA 青 | 壁2, 8% | カット面を下 | 明記なし | 5.5g / 5.5g | §2.5-6 |
| arm_pod_upper_L.stl | 1 | PLA 青 | 壁2, 8% | カット面を下 (X ミラー) | 明記なし | 5.5g / 5.5g | §2.5-6,7 |
| audio_cradle_mic.stl | 1 | PETG | 壁4, 40%, レイヤー0.12 | 軸を水平に寝かせて印刷 | 明記なし | 2g / 2g | §2.8-1,2 |
| audio_cradle_spk.stl | 1 | PETG | 壁4, 40% | リング面を下 | 明記なし | <1g / <1g | §2.8-4 |
| battery_cradle.stl | 1 | PETG | 壁3, 20% | 開口を上 (Z 反転) | 明記なし | 17g / 17g | §2-4 |
| camera_carrier.stl | 1 | PETG | 壁4, 40% | レンズポケット側を上 | 明記なし | 6g / 6g | §2.9-1,2,3 |
| chassis.stl | 1 | PETG | 壁4, インフィル25%グリッド | そのまま (平置き) | 明記なし | 72g / 72g | §2 全体 |
| claw_mount.stl | 1 | PETG | 壁4, 40% | 円盤面を下 | 明記なし | 1g / 1g | §2.5-5 |
| claw_mount_L.stl | 1 | PETG | 壁4, 40% | 円盤面を下 (X ミラー) | 明記なし | 1g / 1g | §2.5-5,7 |
| coxa_bracket.stl | 2 (FL,RR) | PETG | 壁4, 40% | 天板を下 (Z 反転) | 明記なし | 31g / 62g | §1-3, §2-2 |
| coxa_bracket_m.stl | 2 (FR,RL) | PETG | 壁4, 40% | 天板を下 (Z 反転, Y ミラー) | 明記なし | 31g / 62g | §1-3, §2-2 |
| elbow_shell.stl | 1 | PLA グレー | 壁2, 15% | カット面を下 | 明記なし | 2g / 2g | §2.5-6 |
| elbow_shell_L.stl | 1 | PLA グレー | 壁2, 15% | カット面を下 (X ミラー) | 明記なし | 2g / 2g | §2.5-6,7 |
| eye_carrier.stl | 2 (左右) | PETG | 壁4, 40% | 上面 (ポケット側) を上 | 明記なし | 3.5g / 7g | §2.7-2,6 |
| eye_pod.stl | 2 (左右) | PLA 白 | 壁2, 8% (LED 透過) | 背面を下 | 明記なし | 7.5g / 15g | §2.7-1,3,4,5 |
| eye_pod_camera.stl | **0 (印刷しない)** | — | — | — | — | — (検証/可視化用の一体参照形状。印刷は下記分割版 — 2026-08-19 印刷性再設計, printing.md 参照) | §2.9-4 |
| eye_pod_camera_base.stl | 1 | PLA 白 | 壁2, 8% | STL のまま (背面ベタ置き) | 明記なし | 2g / 2g | §2.9-4 |
| eye_pod_camera_shell.stl | 1 | PLA 白 | 壁2, 8% | STL のまま (底面ベタ置き, ドーム上) | 不要 (ベタ置きで自立) | 6g / 6g | §2.9-4 |
| femur_link.stl | 2 (FL,RR) | PETG | 壁4, 40% | STL のまま | **ビルドプレートのみ** (printing.md 明記) | 28.5g / 57g | §1-2,5 |
| femur_link_m.stl | 2 (FR,RL) | PETG | 壁4, 40% | STL のまま (Y ミラー) | **ビルドプレートのみ** (femur_link と同一形状) | 28.5g / 57g | §1-2,5 |
| foot_pad.stl | 4 | **TPU 95A** | 壁3, 30% | フランジ面を下 | 明記なし (TPU の AMS/バイパス注意は §3 参照) | 0.5g / 2g | §1-6 |
| forearm.stl | 1 | PETG | 壁4, 40% | STL のまま | 明記なし | 6g / 6g | §2.5-4 |
| forearm_L.stl | 1 | PETG | 壁4, 40% | STL のまま (X ミラー) | 明記なし | 6g / 6g | §2.5-4,7 |
| leg_foot_bored.stl | 4 | PLA グレー | 壁3, 20% | プラグ側 (tibia 差込面) を下 | 明記なし | 3g / 12g | §1-6 |
| pod_neck.stl | 1 | PETG | 壁4, 40% | 平置き (梁を寝かせる) | 明記なし | 20g / 20g | §2-3 (2026-07-31 頭部逃がしカット追加で22g→19g、同日QA再検証で応力集中対策の増厚/テーパー追加により19g→20gへ再訂正, docs/assembly.md 参照) |
| shin_shell.stl | 2 (FL,RR) | PLA 青 | 壁2, 6% | STL のまま (上端平面が下) | 明記なし | 40.9g / 81.8g (2026-07-31 リリーフカット再評価でキット形状復元、旧36.8g/73.7gから増) | §3 |
| shin_shell_m.stl | 2 (FR,RL) | PLA 青 | 壁2, 6% | STL のまま (Y ミラー) | 明記なし | 40.9g / 81.8g (同上) | §3 |
| shoulder_bracket.stl | 1 | PETG | 壁4, 40% | 上面 (ホーンポケット側) を下 | 明記なし | 10g / 10g | §2.5-2 |
| shoulder_bracket_L.stl | 1 | PETG | 壁4, 40% | 上面を下 (Y ミラー) | 明記なし | 10g / 10g | §2.5-2,7 |
| thigh_cap.stl | 4 | PLA グレー | 壁2, 6% | カット平面が下 | 明記なし | 6g / 24g | §3 |
| tibia_link.stl | 2 (FL,RR) | PETG | 壁4, 40% | 立てて印刷 | 明記なし | 38g / 76g | §1-4 |
| tibia_link_m.stl | 2 (FR,RL) | PETG | 壁4, 40% | 立てて印刷 (Y ミラー) | 明記なし | 38g / 76g | §1-4 |
| upper_arm.stl | 1 | PETG | 壁4, 40% | STL のまま | 明記なし | 10.5g / 10.5g | §2.5-3 |
| upper_arm_L.stl | 1 | PETG | 壁4, 40% | STL のまま (X ミラー) | 明記なし | 10.5g / 10.5g | §2.5-3,7 |

**合計: 41 ファイル / 実質 59 枚 (ミラー・複数個体込み, eye_pod_camera 一体版は印刷対象外) / ≈998g**
(PETG ≈571g, PLA青 ≈368g, PLA灰 ≈53g, PLA白 ≈23g, TPU ≈2g — 内訳は
`tools/filament_calc.py` 出力の「新規設計 (hardware/stl)」節と一致)

※ Head_Top_Eyecut の壁/インフィルは printing.md 本文に数値の明記が
無かったため、`tools/filament_calc.py` の `new_parts["Head_Top_Eyecut"]`
定義 (wall=1.4mm≈壁2, infill=0.08) を典拠とした。本マニフェスト作成時に
printing.md の表へも同条件で追記済み (末尾「発見した齟齬と修正」参照)。

**生成元コマンドの注意 (重要)**: 上記 41 ファイルのうち **40 ファイルは
`cd hardware/src && ../../.venv/bin/python build_all.py` で一括再生成**
されるが、**`Head_Top_Eyecut.stl` だけは独立スクリプト
`tools/make_head_eyecut.py` で生成**する (`build_all.py` の対象外 —
`hardware/src/build_all.py` の import 一覧に `make_head_eyecut` が
含まれない)。目ソケット関連の値 (`config.EYE_BORE_D`/`EYE_SOCKETS_150`)
を変更した場合、`build_all.py` だけを再実行しても Head_Top_Eyecut.stl は
古いまま残る点に注意 (`docs/assembly.md` §0 手順2 に注記を追加済み)。

---

## 2. キット元パーツ全数分類 (`model/*.stl`, 58 件, `ls` 順)

分類は 3 区分 (印刷する / 加工版で置換 / 不使用) + 印刷するの中の
「条件付き(任意)」1 件。**58 件全てに分類がつくことを確認済み
(未分類ゼロ)**。

集計: **印刷する 37 件 + 条件付き印刷 1 件 (Stand_mount_Optional) +
加工版で置換 11 件 + 不使用 7 件 + 印刷しない 2 件 (Head_Plate_Grey /
Head_Bottom_Cap_Grey, 2026-08-20/21 に不使用化) = 58 件** (2026-09-04 集計訂正:
旧「39 件」は印刷しない 2 件を印刷する側に数えたままだった)。

| # | ファイル | kit数量 | 分類 | 対応・理由 |
|---|---|---|---|---|
| 1 | Arm_Left.stl | 1 | **不使用** | 腕ポッドは arm_pod_upper/lower(+_L) に機能置換。加工ソースは `hardware/src/arm_shell.py` が読む Arm_Right.stl のみで、左腕用 (_L) はその鏡映生成 — 本ファイル自体はソースとしても使われない |
| 2 | Arm_Left_Claw_Grey.stl | 1 | 印刷する | 爪ハブ。両腕とも本パーツを鏡映使用 (×2 印刷, DOUBLE_SIDED)。§2.5-5 |
| 3 | Arm_Left_Elbow_Grey.stl | 1 | **不使用** | elbow_shell(+_L) に機能置換。加工ソースは Arm_Right_Elbow_Grey.stl のみ (`arm_shell.py`) — 本ファイルは未使用 |
| 4 | Arm_Left_FingerTip_Grey_x3.stl | 3 | 印刷する | 指先チップ。kit内3本×両腕鏡映=×6 印刷 (DOUBLE_SIDED)。§2.5-5 |
| 5 | Arm_Left_Finger_Black_x3.stl | 3 | 印刷する | 指。kit内3本×両腕鏡映=×6 印刷 (DOUBLE_SIDED)。§2.5-5 |
| 6 | Arm_Left_Guard_Grey.stl | 1 | 印刷する | 左腕ポッドガード ×1。§2.5-6 |
| 7 | Arm_Right.stl | 1 | **加工版で置換** | `arm_shell.py` が読み込み、arm_pod_upper/lower (+ 左腕分 _L の鏡映元) をブーリアン加工するソース |
| 8 | Arm_Right_Claw_Grey.stl | 1 | **不使用** | 爪ハブと無関係の別形状 (開放骨組, 体積/凸包比0.205)。爪ハブは Arm_Left_Claw_Grey のみ採用 (§2.5-5 に明記) |
| 9 | Arm_Right_Elbow_Grey.stl | 1 | **加工版で置換** | `arm_shell.py` が読み込み、elbow_shell (+_L 鏡映元) をブーリアン加工するソース |
| 10 | Arm_Right_Guard_Grey.stl | 1 | 印刷する | 右腕ポッドガード ×1。§2.5-6 |
| 11 | Cabin_Back_Blue_Repaired.stl | 1 | 印刷する | Cabin 一式。§3 |
| 12 | Cabin_Eye_White.stl | 1 | 印刷する | ポッド前面メインアイ、無加工 (2026-07-28 カメラ移設によりそのまま使用)。§2.9 前提, §3 |
| 13 | Cabin_Front_Blue.stl | 1 | 印刷する | ポッド前面、pod_neck フランジと M3×4 共締め。§3 |
| 14 | Cabin_Front_Insert_Back_Black_x2.stl | 2 | 印刷する | Cabin Insert 一式。§3 |
| 15 | Cabin_Front_Insert_Bottom_Long_Black_x2.stl | 2 | 印刷する | 同上 |
| 16 | Cabin_Front_Insert_Bottom_Wide_Black.stl | 1 | 印刷する | 同上 |
| 17 | Cabin_Front_Insert_Front_Black.stl | 1 | 印刷する | 同上 |
| 18 | Cabin_Front_Insert_Left_Black.stl | 1 | 印刷する | 同上 |
| 19 | Cabin_Front_Insert_Right_Black.stl | 1 | 印刷する | 同上 |
| 20 | Cabin_Peg_x2.stl | 2 | 印刷する | 前後シェル嵌合ペグ。§3 |
| 21 | Cabin_RedLight_Large_Red_x4.stl | 4 | 印刷する | 赤ランプ大。§3 |
| 22 | Cabin_RedLight_Small_Red_x4.stl | 4 | 印刷する | 赤ランプ小。§3 |
| 23 | Cabin_Spinnarette_Grey_x4.stl | 4 | 印刷する | §3 |
| 24 | Cabin_Turrent_Left_Grey.stl | 1 | 印刷する | §3 |
| 25 | Cabin_Turrent_Right_Grey.stl | 1 | 印刷する | §3 |
| 26 | Cabin_Turret_Peg_x2.stl | 2 | 印刷する | §3 |
| 27 | Head_Bottom_Blue.stl | 1 | **加工版で置換** | `hardware/src/make_head.py` が読み込み、Head_Bottom_Armcut (腕ソケット拡口+マウス配線受け穴) をブーリアン加工するソース |
| 28 | Head_Bottom_Cap_Grey.stl | 1 | **印刷しない** (2026-08-20 変更) | 旧: Head 積層順最下段の底蓋。機構逃がしカット後の Head_Bottom_Armcut は底中央がバッテリー窓+挿入経路 (旧キャップ座 r7.5..20 は窓に消滅) で、装着するとバッテリー交換を塞ぐため不使用。§3 |
| 29 | Head_Dome_Grey.stl | 1 | 印刷する | Head 一式。§3 |
| 30 | Head_Eye_White_x3.stl | 3 | **加工版で置換** | `make_eye.py`/`make_camera.py` が読み込み、eye_pod (×2, 左右) + eye_pod_camera (×1, 中央) の共通ブーリアン加工ソース。kit内3個を使い切る対応 |
| 31 | Head_Insert_Black_x4.stl | 4 | 印刷する | Head 一式。§3 |
| 32 | Head_Peg_Lower.stl | 1 | 印刷する | Head 一式。§3 |
| 33 | Head_Peg_Upper.stl | 1 | 印刷する | Head 一式。§3 |
| 34 | Head_Plate_Grey.stl | 1 | **印刷しない** (2026-08-21 変更) | 旧: Top/Bottom 間に挟むガスケット円板。2026-08-20 の機構逃がしカット設計でこの層は**シャーシプレート自体が置き換える**ことが確定 (assembly.md §3「Head_Plate は印刷・使用しない」) — 表の更新が漏れていたのを是正。§3 |
| 35 | Head_Plug_Grey.stl | 1 | 印刷する | Head 一式。§3 |
| 36 | Head_Screw_Grey_x2.stl | 2 | 印刷する | Head 一式。§3 |
| 37 | Head_TailJoint_Ball_Grey_Optional_Cross.stl | 1 | 印刷する | pod_neck 化粧スリーブ (無加工のまま被せ接着)。printing.md「Head_TailJoint の使い方」, §2-3 |
| 38 | Head_TailJoint_Blue_Optional_Cross.stl | 1 | 印刷する | 同上 (コーン)。§2-3 |
| 39 | Head_TailJoint_Peg.stl | 1 | 印刷する | 旧可動テール用ペグ。今回の組立には不使用だが、元キット完成度維持のため印刷し予備保管 (printing.md) |
| 40 | Head_TailJoint_Peg_Optional_Cross_Repaired.stl | 1 | 印刷する | 同上、予備保管 |
| 41 | Head_Top_Blue.stl | 1 | **加工版で置換** | `tools/make_head_eyecut.py` が読み込み、Head_Top_Eyecut (目ソケット底φ30貫通ボア) をブーリアン加工するソース |
| 42 | Leg_AnkleJoint_Grey_x4_Repaired.stl | 4 | **不使用** | 旧キットの球関節。本設計は tibia 先端に足 (leg_foot_bored) を直結するため不要 |
| 43 | Leg_Foot_Grey_x4_Repaired.stl | 4 | **加工版で置換** | `make_leg.py` が読み込み、leg_foot_bored (差込プラグ+隠しパッドポケット追加) をブーリアン加工するソース |
| 44 | Leg_HipJoint_Grey_x4.stl | 4 | **不使用** | 股ヨー関節は coxa_bracket 骨格に機能置換 (任意でコーン外皮として縦割り被せ可、必須ではない) |
| 45 | Leg_HipJoint_Socket_Grey_x4.stl | 4 | **不使用** | 同上 |
| 46 | Leg_KneeJoint_Grey_x4.stl | 4 | **不使用 (2026-07-31 ユーザー決定で確定)** | 膝関節は femur_link/tibia_link 骨格に機能置換。膝露出カバーとしての流用も実測で不成立と判明 (露出48mm > パーツ最大辺30.2mm, docs/assembly.md §3 参照) — 膝はメカ剥き出しの見た目で確定、新規カバー形状も作らない |
| 47 | Leg_Shin_Blue_x4.stl | 4 | **加工版で置換** | `shell_mod.py` が読み込み、shin_shell/_m をブーリアン加工するソース |
| 48 | Leg_Shin_Guard_Grey_x4.stl | 4 | 印刷する | §3 (現物合わせでの追い込みがほぼ必須と明記あり) |
| 49 | Leg_Thigh_Grey_x4.stl | 4 | **加工版で置換** | `shell_mod.py` が読み込み、thigh_cap をブーリアン加工するソース |
| 50 | Leg_Thigh_Guard_Blue_x4.stl | 4 | 印刷する | §3 |
| 51 | Leg_Toe_Black_x12.stl | 12 | 印刷する | 3本/脚×4脚=12。leg_foot_bored の甲底面スタブへ瞬間接着。§3 |
| 52 | Mouth_Ball_Grey.stl | 1 | **加工版で置換** | `make_audio.py` が読み込み、Mouth_Ball_Bored をブーリアン加工するソース |
| 53 | Mouth_Cannon_Grey.stl | 1 | **加工版で置換** | 同上、Mouth_Cannon_Bored のソース |
| 54 | Mouth_Cap_Grey.stl | 1 | 印刷する | Mouth 一式、無加工。§2.8-6 |
| 55 | Mouth_Key_Grey.stl | 1 | 印刷する | Mouth 一式、無加工。§2.8-6 |
| 56 | Mouth_Neck_Blue.stl | 1 | **加工版で置換** | `make_audio.py` が読み込み、Mouth_Neck_Bored をブーリアン加工するソース |
| 57 | Mouth_Peg_Grey.stl | 1 | 印刷する | Mouth 一式、無加工。§2.8-6 |
| 58 | Stand_mount_Optional.stl | 1 | 印刷する **(条件付き・任意)** | ベンチ吊り治具用途のみ。使わないなら印刷不要。printing.md |

**分類の精度についての注記**: `printing.md`「印刷しないもの」節は
Arm_Left/Arm_Right、Arm_Left/Right_Elbow_Grey を左右まとめて
「→加工版」と記述しているが、実装 (`hardware/src/arm_shell.py`) を
確認すると加工のブーリアン演算に使うソースメッシュは Arm_Right 系
(Arm_Right.stl / Arm_Right_Elbow_Grey.stl) のみで、左 (_L) 側は
その鏡映で生成される。**最終的に「印刷しない」という結論は同じ**だが、
本書では「どのファイルが実際に加工ソースとして使われるか」まで一段
精密に区別した (Arm_Left / Arm_Left_Elbow_Grey は上表で「不使用」)。
printing.md 側の文言は結論として誤りではないため未修正 — 齟齬ではなく
精度差として記録する。

---

## 3. 印刷順序 (Go/No-Go) とプレート構成の提案

`docs/assembly.md` の Go/No-Go ゲート構造に沿った印刷フェーズ分け。
**フェーズが進む条件は printed パーツの組立検証結果であり、印刷順序を
勝手に前倒ししない** (特に Phase 0→1 のベンチ試験合格は必須ゲート)。

### Phase 0 — 事前準備・テスト印刷 (assembly.md §0)

1. サーボ・ホーン実測 → `config.py` 更新 → `build_all.py` **+
   `tools/make_head_eyecut.py`** 再生成 → 検証7点セット green を確認
2. テスト印刷 (各1個、PETG骨格+PLA灰): `coxa_bracket` / `femur_link` /
   `tibia_link` / `leg_foot_bored` / `foot_pad`
3. 並行して `claw_mount` を片手分 (1個, PETG) 先行印刷し、爪ハブ
   (`Arm_Left_Claw_Grey`, 150%キット部品を別途1個印刷) との接着面を
   現物合わせ (printing.md)

### Phase 1 — 脚 1 本の Go/No-Go (assembly.md §1)

Phase 0 の印刷物で脚 1 本を組み、コキシをバイス固定してスイープ確認 +
**1.2kg 錘での持ち上げ試験** (6V給電) + 保持電流 <1.5A/サーボ を確認。
**持ち上がらなければ Phase 2 (全数印刷) へ進まない** — 設計値見直し→
`build_all.py` 再生成→再テスト印刷のループに戻る。

### Phase 2 — 残り 3 脚 + シャーシ (Go 後, assembly.md §2)

PETG骨格は色替え不要の単色プレートにまとめる (filament.md 推奨):

- `coxa_bracket` ×1 追加 (計2) + `coxa_bracket_m` ×2
- `femur_link` ×1 追加 (計2) + `femur_link_m` ×2
- `tibia_link` ×1 追加 (計2) + `tibia_link_m` ×2
- `leg_foot_bored` ×3 追加 (計4, PLA灰) / `foot_pad` ×3 追加 (計4, **TPU**)
- `chassis` / `pod_neck` / `battery_cradle` 各1 (PETG)
- 脚意匠: `shin_shell` ×2 + `shin_shell_m` ×2 (PLA青) / `thigh_cap` ×4
  (PLA灰) — 骨格寸法確定後ならこのフェーズと並行印刷可
- キット (150%): `Leg_Toe_Black_x12` (黒) / `Leg_Thigh_Guard_Blue_x4`
  (青) / `Leg_Shin_Guard_Grey_x4` (灰)

### Phase 3 — 腕 (脚ベンチ試験合格後 or 並行可, assembly.md §2.5)

腕は歩行に影響しないため脚と並行、または脚が Go した後の空き時間で。

- PETG (同一プレート可): `shoulder_bracket`+`_L`, `upper_arm`+`_L`,
  `forearm`+`_L`, `claw_mount_L` (Phase0で片手分は印刷済み)
- PLA青: `arm_pod_upper`+`_L`, `arm_pod_lower`+`_L`
- PLA灰: `elbow_shell`+`_L`
- キット (150%): `Arm_Left_Guard_Grey`×1, `Arm_Right_Guard_Grey`×1,
  `Arm_Left_Claw_Grey`×1追加 (計2), `Arm_Left_Finger_Black_x3`×6,
  `Arm_Left_FingerTip_Grey_x3`×6

### Phase 4 — 頭部・目・音声・カメラ (assembly.md §2.7-2.9)

- `Head_Top_Eyecut` (`tools/make_head_eyecut.py`, build_all.py 対象外
  — 上記§1の注意参照) / `Head_Bottom_Armcut` (PLA青)
- `eye_pod`×2 (PLA白) / `eye_carrier`×2 (PETG)
- `eye_pod_camera_shell`×1 + `eye_pod_camera_base`×1 (PLA白, 接着で一体化) / `camera_carrier`×1 (PETG)
- **印刷前に INMP441 基板寸法・スピーカー厚を実測**し `config.py` の
  `AUDIO_MIC_*`/`AUDIO_SPK_REAL_H` と差異があれば更新→再生成
  (assembly.md §2.8 前提)。その後 `Mouth_Cannon_Bored`/`Mouth_Neck_Bored`/
  `Mouth_Ball_Bored` (PLA灰/青/灰) + `audio_cradle_mic`/`audio_cradle_spk`
  (PETG)
- キット (150%): Head一式 (`Head_Peg_Lower`, `Head_Peg_Upper`, `Head_Dome_Grey`,
  `Head_Plug_Grey`, `Head_Screw_Grey_x2`, `Head_Insert_Black_x4` —
  **`Head_Plate_Grey` / `Head_Bottom_Cap_Grey` は印刷しない** (§2 #34/#28)) /
  Mouth一式 (`Mouth_Cap_Grey`, `Mouth_Key_Grey`, `Mouth_Peg_Grey`) /
  TailJoint一式 (`Head_TailJoint_Blue_Optional_Cross`,
  `Head_TailJoint_Ball_Grey_Optional_Cross`, `Head_TailJoint_Peg`,
  `Head_TailJoint_Peg_Optional_Cross_Repaired`)

### Phase 5 — 意匠シェル最終 (Cabin, assembly.md §3)

キット (150%, デュアルノズルで青+灰同時印刷が efficient):
`Cabin_Front_Blue`, `Cabin_Back_Blue_Repaired`, `Cabin_Eye_White`,
`Cabin_Peg_x2`, `Cabin_Turrent_Left_Grey`, `Cabin_Turrent_Right_Grey`,
`Cabin_Turret_Peg_x2`, `Cabin_RedLight_Large_Red_x4`,
`Cabin_RedLight_Small_Red_x4`, `Cabin_Spinnarette_Grey_x4`,
`Cabin_Front_Insert_*` (6種)。任意: `Stand_mount_Optional` (ベンチ吊り時のみ)。

### プレート構成 / X2D 運用上の注意

- **ビルドボリューム**: シングルノズル256×256×260mm / デュアルノズル
  235.5×256×256mm (確認日2026-07-27, printing.md 出典)。150%最大パーツ
  Cabin_Front ≈170×195×110mm はどちらでも印刷可能 — 単独プレートで足りる
- **デュアルノズル活用**: 骨格(PETG)と意匠(PLA)の同時プレート、Cabin/Head
  周りの青+灰2色プレートでパージ削減 (printing.md/filament.md)
- **PLA白は専用プレート** (`eye_pod`×2 + `eye_pod_camera_shell`/`_base`, インフィル
  設定が他と異なるため他パーツと混在させない)
- **TPU (`foot_pad`×4) は要注意**: X2D の AMS へ直接給紙可能なのは
  「AMS用TPU」専用グレードのみで、**硬度95A以下の一般TPU (95A HF/90A/85A)
  はAMS直接給紙非推奨** — 外部スプールホルダー(バイパス給紙)または
  トップマウントホルダーを使うこと。さらに右補助ノズル(デュアルノズルの
  サブノズル)はTPU非対応のため**必ずメインノズル側で印刷**する
  [出典: Bambu Lab公式Wiki「X2D用TPU印刷ガイド」
  wiki.bambulab.com/ja/x2d/manual/tpu-printing-guide, 確認日2026-07-30,
  `docs/shopping.md` B-3 に既出]
- **黒/白/赤の消費は僅少** (filament.md): AMS常設スプールで足りる想定

---

## 4. 相互照合レポート — assembly.md 登場パーツ名の解決

`docs/assembly.md` に登場する物理パーツ名 (config/firmware のコード定数
— `ARM_MOUNT_XY`, `ARM_SIGN`, `JOINT_SIGN`, `CALIBRATION_MODE`,
`FOREARM_LEN`, `HAND_HALF`, `EYE_SOCKETS_150`, `ESP32_Y0`,
`CAM2_THETA_DEG`, `MOUTH_CANNON_T`/`_TIP_T`/`_REAR_STANDOFF_MM`,
`POD_FLANGE`, `AUDIO_SPK_REAL_H`, `ARM_MOUNT_YAW_DEG` 等は物理パーツでは
ないため対象外) を全て抽出し、解決先 (hardware/stl / kit プレート /
BOM.md 購入品) を突き合わせた。**未解決ゼロ**。

| assembly.md 上の表記 | 解決先 |
|---|---|
| coxa_bracket / femur_link / tibia_link / leg_foot_bored / foot_pad | hardware/stl (§1) |
| chassis (「シャーシ」表記) / pod_neck / battery_cradle | hardware/stl (§1) |
| shin_shell / shin_shell_m / thigh_cap | hardware/stl (§1) |
| shoulder_bracket / upper_arm / forearm / claw_mount (+_L) | hardware/stl (§1) |
| arm_pod_upper / arm_pod_lower / elbow_shell (+_L) | hardware/stl (§1) |
| eye_pod / eye_carrier / eye_pod_camera / camera_carrier | hardware/stl (§1) |
| audio_cradle_mic / audio_cradle_spk | hardware/stl (§1) |
| Mouth_Cannon_Bored / Mouth_Neck_Bored / Mouth_Ball_Bored | hardware/stl (§1) |
| Head_Top_Eyecut / Head_Bottom_Armcut | hardware/stl (§1) |
| Head_Top / Head_Bottom / Head_Eye_White (加工前の言及) | kit → 上記加工版が代替 (§2 #27,30,41) |
| Arm_Left_Claw_Grey / Arm_Right_Claw_Grey | kit (§2 #2, #8 — Right は不使用) |
| Arm_Left_Finger_Black_x3 / Arm_Left_FingerTip_Grey_x3 | kit (§2 #4, #5) |
| Arm_Guard_Grey (assembly.md 旧表記) | kit `Arm_Left_Guard_Grey`/`Arm_Right_Guard_Grey` (§2 #6, #10)。**assembly.md §2.5-6 を実ファイル名2点へ修正済み** (下記「発見した齟齬」) |
| Leg_Toe / Leg_Toe_Black_x12 | kit (§2 #51) |
| Leg_Shin_Guard / Leg_Shin_Guard_Grey_x4 | kit (§2 #48) |
| Leg_Foot (加工前の言及) | kit → leg_foot_bored が代替 (§2 #43) |
| Head_Plate (グレー円板) | kit `Head_Plate_Grey` (§2 #34) |
| Bottom_Cap (「Top→Plate→Bottom→Bottom_Cap」の積層順表記) | kit `Head_Bottom_Cap_Grey` (§2 #28) + Head_Bottom_Armcut (hardware/stl) |
| Cabin_Eye / Cabin_Eye_White | kit (§2 #12) |
| Cabin_Peg | kit `Cabin_Peg_x2` (§2 #20) |
| Cabin_Front (「Cabin(ポッド)v3」節) | kit `Cabin_Front_Blue` (§2 #13) |
| TailJoint_Blue | kit `Head_TailJoint_Blue_Optional_Cross` (§2 #38) |
| DS3218(MG) | BOM.md #1 |
| MG90S | BOM.md #2, #2a |
| サブマイクロ (assembly.md §2.7) | BOM.md #2b |
| PCA9685 | BOM.md #4 |
| UBEC | BOM.md #6 |
| WS2812(B) | BOM.md #9 |
| INMP441 | BOM.md #30 |
| MAX98357A | BOM.md #31 |
| ESP32 (DevKitC) | BOM.md #3 |
| ESP32S3 (XIAO ESP32S3 Sense, カメラ) | BOM.md #34 |

ネジ・接着剤等の消費材 (M3×10 等) は `docs/BOM.md`/`docs/shopping.md` 側で
既に番号 (#18-29, #35) 単位の相互照合が完了しているため (`shopping.md`
「検証: BOM.mdとの相互照合」節, 2026-07-30実施)、本書では対象外とした。

---

## 発見した齟齬と修正 (本タスクで実施)

以下は本マニフェスト作成の過程で機械照合により発見し、`printing.md`/
`assembly.md` 側を修正したもの。いずれも「印刷しない/しない」といった
最終結論を変えるものではなく、記載漏れ・記述精度の是正。

1. **printing.md「新規設計パーツ」表に `Head_Top_Eyecut` の行が無かった**
   (Head_Bottom_Armcut など他の `_Bored`/`_Armcut` 系加工版は全て表に
   行があるのに Head_Top_Eyecut だけ prose 中の言及のみで壁厚/インフィル
   の数値が無かった) → 表に行を追加 (壁2/8%, `tools/filament_calc.py`
   の前提と一致させた)
2. **printing.md「印刷しないもの」の集約リストに `Head_Top_Blue` が
   欠けていた** (Head_Bottom_Blue 等は載っているのに Head_Top_Blue だけ
   別節の prose にしか書かれていなかった) → リストへ追加
3. **assembly.md 冒頭の概要文が「腕は MG90S + サブマイクロ」のまま
   古かった** — 2026-07-29 の固定爪化でグリップ用サブマイクロは腕から
   廃止済み (同じ assembly.md 内の §2.5 / 行363 の記述とは矛盾していた)
   → 「腕は MG90S ×3/腕 (肩ヨー/肩ピッチ/肘)、目はサブマイクロ×2」に
   修正
4. **assembly.md §0 手順2 に `tools/make_head_eyecut.py` の再生成手順が
   無かった** — `build_all.py` だけを実行すると Head_Top_Eyecut.stl は
   古いまま残る (import 一覧に無いため) → 手順2に注記を追加
5. **assembly.md §2.5-6 の `Arm_Guard_Grey` という表記が実ファイル名と
   不一致** (実際のキットファイルは `Arm_Left_Guard_Grey.stl`/
   `Arm_Right_Guard_Grey.stl` の左右別形状2ファイルで、`Arm_Guard_Grey`
   という単一ファイルは存在しない) → 実ファイル名2点の表記に修正

**2026-07-30 追記 (解消済み)**: 上記で記録していた `docs/shopping.md` の
B-2表 (「現物合わせ・加工が必要な箇所」) の stale 3行 (TailJoint の
面取り、Head_Bottom 腕ソケット開口、Mouth ソケット内側の配線穴) は、
`docs/wiring.md`/`docs/printing.md` の焼き込み更新に追随する形で
shopping.md 側を別タスクで修正済み (shopping.md「B-2」節の解消済み注記
参照)。本節はその時点での既知課題の記録として履歴保持のため残す。

## 単色プレート 3mf 一覧 (hardware/stl/, `tools/make_plates.py` が生成・検証)

全て単一フィラメント。生成後に「3mf 埋め込みメッシュ vs ソース STL」の体積/bbox 照合
(verify_3mf) と BambuStudio CLI スライス Success を確認済み (2026-08-21 時点)。
黒/赤は AMS 非搭載のためスロット1へ出力 — **印刷前に Studio でフィラメント割当を差し替えること**。
Stand_mount_Optional (任意治具) と Head_Plate_Grey / Head_Bottom_Cap_Grey (不使用) は非収載。

| ファイル | 内容 | スロット | 実測 (スライス) |
|---|---|---|---|
| PLA_Matte_Blue_1 | Head_Top_Eyecut + shin_shell×2 + _m×2 | 1 青 | 195g / 9.5h |
| PLA_Matte_Blue_2 | Head_Bottom_Armcut + arm_pod×4 + Mouth_Neck_Bored | 1 青 | 31.5g / 1.75h |
| PLA_Matte_Blue_3 | Cabin_Front_Blue | 1 青 | 305g / 11.3h |
| PLA_Matte_Blue_4 | Cabin_Back + TailJoint_Blue + Thigh_Guard×4 (ブリム付) | 1 青 | 160g / 6.7h |
| PLA_Matte_Gray | Mouth Ball/Cannon_Bored + thigh_cap×4 + leg_foot_bored×4 | 3 灰 | 47g / 2.5h |
| PLA_Matte_Gray_2 | キット灰意匠+ペグ類 21種36個 (Turret×2, Mouth_Cap, TailJoint_Ball, shin_guard×4, 爪ハブ×2, 指先×6 ほか) | 3 灰 | 70g / 3.7h |
| PLA_Matte_White | eye_pod×2 + eye_pod_camera_shell/base | 2 白 | 22g / 1.3h |
| PLA_Matte_White_2 | Cabin_Eye_White | 2 白 | 4.2g / 0.14h |
| PLA_Black_1 | Leg_Toe×12 + 指×6 + Cabin/Head Insert 12個 | 1→**黒に差替** | 34g / 2.2h |
| PLA_Red_1 | RedLight 大×4 + 小×4 | 1→**赤に差替** | 2.5g / 0.16h |
| PETG_1_Chassis | chassis + eye_carrier×2 + claw_mount×2 + audio_spk | 4 PETG | 54g / 2.9h |
| PETG_2_Tibia | tibia_link×2 + _m×2 (立て) + pod_neck | 4 PETG | ~177g / ~11h (2026-09-04 ネック増厚後の概算, 要再スライス) |
| PETG_3_Femur | femur_link×2 + _m×2 + battery_cradle | 4 PETG | 129g / 8.7h |
| PETG_4_CoxaArm | coxa×4 + shoulder×2 + upper_arm×2 + forearm×2 + camera_carrier | 4 PETG | 153g / 9.9h |
| PETG_5_Mic | audio_cradle_mic (**レイヤー 0.12**) | 4 PETG | 1.9g / 0.26h |
| PETG_Walk_1_Chassis | 歩行最小① chassis + battery_cradle + audio_cradle_mic | 4 PETG | 66g / 3.7h |
| PETG_Walk_2_CoxaFemur | 歩行最小② coxa_bracket×4 + femur_link×4 | 4 PETG | 210g / 13.8h |
| PETG_Walk_3_Tibia | 歩行最小③ tibia_link×4 (立て) | 4 PETG | ~160g / ~9.8h (2026-09-04 ネック増厚後の概算, 要再スライス) |
| PETG_Walk_4_Rest | 残りPETG一括: pod_neck + 肩×2 + 上腕×2 + 前腕×2 + eye_carrier×2 + camera_carrier + claw_mount×2 + spk (mic は Walk_1 で印刷済) | 4 PETG | 84g / 5.2h |
| elbow_shells_PLA_Matte | elbow_shell + _L | 3 灰 | (生成済み) |
| eye_pod_camera_base_x2 | eye_pod_camera_base ×2 (予備) | 2 白 | (生成済み) |
| foot_pad | foot_pad ×4 (TPU, 外部スプール) | TPU | (既存) |

- PETG 骨格のうち**印刷済みの部品** (Phase0 の femur/tibia, 片脚試作, claw_mount 先行印刷分)
  は該当プレートを Studio で開いて不要オブジェクトを削除してから印刷する
- 接地面積 <150mm² のオブジェクトには外周ブリム 5mm を自動付与済み (make_plates.py)。
  Head_TailJoint_Peg_..._Repaired は接地 0mm² (全面丸み) — サポート上印刷になる予備部品
- **2026-08-21b 形状修正 (要再ダウンロード/開き直し)**: ① chassis から ESP32
  ネジ止めボスを撤去 (後脚サーボ開口内に浮遊+基板がサーボと干渉する不成立
  設計だった — 基板はテープ留め運用へ)。② tibia_link/_m の膝ディスク分離を
  修正 (45°ウェッジがネックを切断していた → ガード r23 + femur ウェブ後退
  web_x1=FEMUR_LEN-22.5)。chassis/femur/tibia を含む 6 プレート
  (PETG_1/2/3, PETG_Walk_1/2/3) は新メッシュで再生成・再スライス済み。
  **修正前の 3mf や印刷済みの旧 femur/tibia は使用不可**
- **2026-09-04 tibia 膝ネック強度修正 (要再ダウンロード)**: 機構レビュー M-01 —
  上記 45° ウェッジ×2 は femur が存在しない +X 側まで削り、ネックプレートが z=-23 で
  3.7×3.0mm (11mm², σ≈240MPa, 破断確実) しか残っていなかった。femur 側障害物の膝
  ±47° 掃引領域を数値減算する方式 (`_femur_knee_sweep`) + 外側 3mm 増厚に変更
  (断面 115mm², `check_leg_link_strength.py` SF 2.1)。tibia 体積 32.4→34.3cm³。
  `PETG_Walk_3_Tibia` / `PETG_2_Tibia` を再生成。あわせて全ホーン共締め下穴
  `HORN_PILOT_D` 2.0→2.2 (coxa/femur も STL 変更、印刷済み品はドリル追加工可)。
  **2026-09-04 より前の tibia 3mf/印刷物は使用不可**
- **PETG_Walk_1〜3 (2026-08-21)**: 歩行チェーン (chassis→coxa→femur→tibia) +
  battery_cradle だけの歩行実験最小セット (番号 = 印刷推奨順)。腕・目・カメラ・
  pod_neck・spk は含まない。PETG_1〜4 と部品が重複するので**どちらか一方の系列
  だけを印刷する**こと。mic は ① に「ついで」同乗 — 本来のレイヤー 0.12 指定に
  対し 0.2 で印刷される。圧入がきつい/粗い場合は PETG_5_Mic (0.12, 16分) で
  刷り直す。歩行には別途 leg_foot_bored×4 (PLA_Matte_Gray 収載) と foot_pad×4
  (TPU) が必要
