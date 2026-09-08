# 印刷向き候補台帳（最終freeze2前）

この台帳は`assembly.json`とSTLを変更せず、組立座標の部品をスライサーへ渡す候補回転とベッド移動を記録する。状態は`ORIENTATION_CANDIDATE_FREEZE2_PENDING`で、印刷・適合・強度・実機歩行の合格を示さない。freeze2後に同じ生成器を一度実行し、source SHAと数量を更新する。

- body設計個数: **16**（16行）
- legs設計個数: **20**（10行）
- 合計: **36**（body + legs。足・既存再使用品・試作/残数は最終印刷manifestで分離）
- 既知平面の候補: 18個。幾何候補（実印刷未確認）: 18個。機械推奨待ち: 0個。
- STL SHA不一致: 0件。
- source_sha256 未記録: 0件、assembly生成状態/SHA鮮度: 記録済み / 一致。
- 材料/密度の単一情報源: `hardware/src/config.py` SHA-256 `c8655229c754b5d57f22ae43393bf35d4fa8d3d8578d5b3599743182c3af6e86`。各行は`config.material_density_g_cm3(material)`を通じて記録。
- 機械推奨待ち: 0件。最終台帳では候補向き未確定として致命扱いにする。
- 幾何/支持の致命的失敗: **0件。**（支持面の局所確認は実印刷合格を意味しない）
- 脚強度の荷重候補: **1.9kgf × 動的係数2.0 = 3.8kgf（3.8kgfは適用済みで二重適用しない）**。印刷材料・積層方向・試験強度は未確認。

## 向きと支持面

- `pf_chassis`: シャーシ下面を下、回転なし。
- `pf_electronics_shelf_*`: 棚下面を下、回転なし。
- `pf_ld220_yaw_cap_*`: X180度。皿頭側を上、平面側を下。
- `pf_ld220_coxa_cap` / `pf_ld220_femur_cap`: X+90度。実STLの+Y側（皿頭側）を上、-Y側（平面側）を下。
- `pf_ld220_coxa_cap_m` / `pf_ld220_femur_cap_m`: X-90度。実STLの-Y側（皿頭側）を上、+Y側（平面側）を下。
- `pf_cabin_rail_*`: Y90度。広い側面を下。
- coxa/femur/tibia本体、mouth/camera/claw: 実STLの荷重軸・取付面・支持面積から幾何候補を記録。反り・層間強度・実機適合は未確認。

## 部品一覧

| 区分 | 部品 | 設計個数 | 材料 | 壁/充填 | 向き候補 | ベッド移動 | 状態 |
|---|---|---:|---|---|---|---|---|
| body | `pf_chassis` | 1 | PLA | 2.4 mm / 25% | (0.0, 0.0, 0.0)° / chassis_underside | (-0.0, 13.1, -0.0) | KNOWN_PLANE_CANDIDATE |
| body | `pf_head_top_clearanced` | 1 | PLA | 2.4 mm / 8% | (0.0, 0.0, 0.0)° / head_shell_original_underside_and_clearance_rim | (-0.0, -11.0, -9.7) | MECHANICAL_GEOMETRIC_CANDIDATE_PHYSICAL_PRINT_UNVERIFIED |
| body | `pf_ld220_yaw_cap_fr` | 1 | PLA | 2.4 mm / 50% | (180.0, 0.0, 0.0)° / yaw_cap_mount_face | (-110.9, 22.0, 1.6) | KNOWN_PLANE_CANDIDATE |
| body | `pf_ld220_yaw_cap_fl` | 1 | PLA | 2.4 mm / 50% | (180.0, 0.0, 0.0)° / yaw_cap_mount_face | (110.9, 22.0, 1.6) | KNOWN_PLANE_CANDIDATE |
| body | `pf_ld220_yaw_cap_rl` | 1 | PLA | 2.4 mm / 50% | (180.0, 0.0, 0.0)° / yaw_cap_mount_face | (102.4, -42.5, 1.6) | KNOWN_PLANE_CANDIDATE |
| body | `pf_ld220_yaw_cap_rr` | 1 | PLA | 2.4 mm / 50% | (180.0, 0.0, 0.0)° / yaw_cap_mount_face | (-102.4, -42.5, 1.6) | KNOWN_PLANE_CANDIDATE |
| body | `pf_cabin_rail_l` | 1 | PLA | 2.4 mm / 40% | (0.0, 90.0, 0.0)° / rail_side_face | (-35.0, 157.5, -26.0) | KNOWN_PLANE_CANDIDATE |
| body | `pf_cabin_rail_r` | 1 | PLA | 2.4 mm / 40% | (0.0, 90.0, 0.0)° / rail_side_face | (-35.0, 157.5, 69.0) | KNOWN_PLANE_CANDIDATE |
| body | `pf_electronics_shelf_0` | 1 | PLA | 2.4 mm / 40% | (0.0, 0.0, 0.0)° / shelf_underside | (-0.0, 80.0, -48.0) | KNOWN_PLANE_CANDIDATE |
| body | `pf_electronics_shelf_1` | 1 | PLA | 2.4 mm / 40% | (0.0, 0.0, 0.0)° / shelf_underside | (-0.0, 80.0, -82.0) | KNOWN_PLANE_CANDIDATE |
| body | `pf_electronics_shelf_2` | 1 | PLA | 2.4 mm / 40% | (0.0, 0.0, 0.0)° / shelf_underside | (-0.0, 80.0, -114.0) | KNOWN_PLANE_CANDIDATE |
| body | `pf_mouth_key` | 1 | PLA | 2.4 mm / 50% | (0.0, 90.0, 0.0)° / mouth_key_broad_side_support_and_insertion_axis | (-10.6, -92.9, 19.7) | MECHANICAL_GEOMETRIC_CANDIDATE_PHYSICAL_PRINT_UNVERIFIED |
| body | `pf_camera_carrier` | 1 | PLA | 2.4 mm / 40% | (0.0, 90.0, 0.0)° / camera_carrier_broad_side_and_tray_service_direction | (-0.8, 3.5, 13.0) | MECHANICAL_GEOMETRIC_CANDIDATE_PHYSICAL_PRINT_UNVERIFIED |
| body | `pf_eye_pod_camera_clearanced` | 1 | PLA | 2.4 mm / 8% | (0.0, 0.0, 0.0)° / camera_pod_mount_base_and_optical_opening | (-0.0, 0.0, -0.0) | MECHANICAL_GEOMETRIC_CANDIDATE_PHYSICAL_PRINT_UNVERIFIED |
| body | `pf_fixed_claw_r` | 1 | PLA | 2.4 mm / 50% | (0.0, 270.0, 0.0)° / fixed_claw_broad_root_support_and_adhesive_load_path | (3.7, -0.1, -16.0) | MECHANICAL_GEOMETRIC_CANDIDATE_PHYSICAL_PRINT_UNVERIFIED |
| body | `pf_fixed_claw_l` | 1 | PLA | 2.4 mm / 50% | (0.0, 90.0, 0.0)° / fixed_claw_broad_root_support_and_adhesive_load_path | (-3.7, -0.1, -16.0) | MECHANICAL_GEOMETRIC_CANDIDATE_PHYSICAL_PRINT_UNVERIFIED |
| legs | `pf_coxa_bracket` | 2 | PLA | 2.4 mm / 50% | (0.0, 0.0, 0.0)° / coxa_mount_plate_and_yaw_riser_underside | (-14.5, 7.2, 13.5) | MECHANICAL_GEOMETRIC_CANDIDATE_PHYSICAL_PRINT_UNVERIFIED |
| legs | `pf_femur_link` | 2 | PLA | 2.4 mm / 50% | (90.0, 0.0, 0.0)° / femur_horn_face_up_and_bending_axis_in_bed_plane | (-35.0, -0.0, 32.4) | MECHANICAL_GEOMETRIC_CANDIDATE_PHYSICAL_PRINT_UNVERIFIED |
| legs | `pf_tibia_link` | 2 | PLA | 2.4 mm / 50% | (90.0, 0.0, 0.0)° / tibia_horn_face_up_and_foot_load_axis_in_bed_plane | (-0.0, -58.5, 11.0) | MECHANICAL_GEOMETRIC_CANDIDATE_PHYSICAL_PRINT_UNVERIFIED |
| legs | `pf_ld220_coxa_cap` | 2 | PLA | 2.4 mm / 50% | (90.0, 0.0, 0.0)° / pitch_knee_cap_flat_face | (2.8, -0.0, -11.4) | KNOWN_PLANE_CANDIDATE |
| legs | `pf_ld220_femur_cap` | 2 | PLA | 2.4 mm / 50% | (90.0, 0.0, 0.0)° / pitch_knee_cap_flat_face | (-41.2, -0.0, -11.4) | KNOWN_PLANE_CANDIDATE |
| legs | `pf_coxa_bracket_m` | 2 | PLA | 2.4 mm / 50% | (0.0, 0.0, 0.0)° / coxa_mount_plate_and_yaw_riser_underside | (-14.5, -7.2, 13.5) | MECHANICAL_GEOMETRIC_CANDIDATE_PHYSICAL_PRINT_UNVERIFIED |
| legs | `pf_femur_link_m` | 2 | PLA | 2.4 mm / 50% | (-90.0, 0.0, 0.0)° / femur_horn_face_up_and_bending_axis_in_bed_plane | (-35.0, -0.0, 32.4) | MECHANICAL_GEOMETRIC_CANDIDATE_PHYSICAL_PRINT_UNVERIFIED |
| legs | `pf_tibia_link_m` | 2 | PLA | 2.4 mm / 50% | (-90.0, 0.0, 0.0)° / tibia_horn_face_up_and_foot_load_axis_in_bed_plane | (-0.0, 58.5, 11.0) | MECHANICAL_GEOMETRIC_CANDIDATE_PHYSICAL_PRINT_UNVERIFIED |
| legs | `pf_ld220_coxa_cap_m` | 2 | PLA | 2.4 mm / 50% | (-90.0, 0.0, 0.0)° / pitch_knee_cap_flat_face | (2.8, 0.0, -11.4) | KNOWN_PLANE_CANDIDATE |
| legs | `pf_ld220_femur_cap_m` | 2 | PLA | 2.4 mm / 50% | (-90.0, 0.0, 0.0)° / pitch_knee_cap_flat_face | (-41.2, 0.0, -11.4) | KNOWN_PLANE_CANDIDATE |

各行の完全なSTL相対path、期待/実測SHA、回転行列、ベッド境界、支持面面積、置換旧部品はJSONに保存する。設計個数は`assembly.parts`から集計した実装個数で、適合用1個と合格後の残数を意味しない。

## freeze2後の更新条件

1. 機械担当のsource/config完了宣言を受ける。
2. body/legsの最終`assembly.json`とSTLを読み、SHA不一致があればそのまま不一致として止める。SHAだけを付け替えない。
3. 複雑なリンクの推奨姿勢を反映し、印刷前に1靴・1脚の適合と全体CAD/物理試験のゲートを確認する。
4. 最終印刷manifestでは設計必要数、適合試作の内数、合格後の残数、既存再使用品を二重計上しない。
