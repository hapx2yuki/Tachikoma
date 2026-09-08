# 印刷優先マニフェスト（2026-09-06）

**状態: `FREEZE2_PENDING_CANDIDATE`。** 印刷優先候補。CAD検査と実物/量産許可を分け、追加購入を発生させないための台帳。

初回の全体仮組みは設計必要数の内数で、その内側に1靴・1脚の実物適合確認を含める。合格後の残数だけを追加計数する。青殻は既存局所加工4個と新規印刷4個を択一にし、LDケージ単体候補・工具・同じ論理部品の向き違いを本番数へ重ねない。構造PLAは手持ち色を自由に配分し、色を追加購入の条件にしない。候補形状はfreeze2前のため、以下の数量は設計必要数と今印刷可数を分離して記録する。

## 個数の三層（設計必要数と今印刷可数）

今印刷可数は、freeze2・足の厳密検査・実物適合・材料残量のゲートをすべて通過するまで **0** とする。設計必要数は生成済みassemblyの部品対応から算出し、pending行の数量0へ隠さない。

| 層 | 設計必要数 | 初回内数 | 適合後残数 | 今印刷可数 | 扱い |
|---|---:|---:|---:|---:|---|
| 設計必要数 body（量産未解放） | 16 | 16 | 0 | 0 | body/assembly.jsonの全parts（yaw蓋を含む） |
| 設計必要数 legs（量産未解放） | 20 | 10 | 10 | 0 | legs/assembly.jsonの脚対応数の合計。各ユニークSTLを1個ずつ先行 |
| 設計必要数 TPU靴（量産未解放） | 4 | 1 | 3 | 0 | 脚IDのユニーク数。1個を先行 |
| 設計必要数 PLAスペーサー（量産未解放） | 16 | 4 | 12 | 0 | feetのspacer variants×脚ID数。1脚分を先行 |
| 設計必要数（量産未解放） | **56** | **31** | **25** | **0** | freeze2後に再生成・再検査 |
| 条件付き新規脛殻 | 4 | — | — | 0 | 既存脛殻4個を加工できない場合だけ。設計上限60個。二案を合計しない |
| 非本番LD適合治具 | 3種×各1 | — | — | 0 | `ld220_cradle` / `ld220_cap` / `ld220_horn_adapter`。量産へ組み込まず、56/60へ加算しない |
現行の比較基準は設計必要数56（量産未解放）、初回31、残り25、条件付き殻を含む設計上限60。頭上部と中央カメラ逃がし部品をassemblyへ意図的に追加したため、旧レビュー値（設計必要数54、初回29、残り25、設計上限58）は履歴比較として保持する。現行assemblyが変わる場合は、意図を確認したうえで基準・初回/残数・質量層を同時に更新する。

## 印刷解放の三つの単位

Aは**31個**の初回全体仮組みです。全体仮組み用body16＋左右/前後の固有脚部品10＋1脚分足裏5（TPU靴1＋スペーサー4）。このAの中で実物適合を判定する最小対象は1靴・1脚です。BはAの合格後に進める**25個**です。Cは既存脛殻4個の局所加工・再使用が不成立の場合だけ選ぶ新規4個で、設計上限は60個です。A＋B=56個で設計必要数56個に一致し、CはA/Bへ加えません。`currently_printable_quantity=0` はA・B・Cの解放未確定を示す値で、追加印刷全体を不要とする判定ではありません。

| 解放単位 | 数量 | 内訳/用途 | 解放条件 | 設計必要数との関係 |
|---|---:|---|---|---|
| A 初回全体仮組み | **31** | 全体仮組み用body16＋左右/前後の固有脚部品10＋1脚分足裏5（TPU靴1＋スペーサー4）。実物適合の最小対象は1靴・1脚 | freeze2・足の厳密検査・材料/現物確認を確認してから先行判定する | 初回内数の全量 |
| B A合格後の残数 | **25** | body0＋固有脚部品10＋足裏15 | Aの実物適合・電装・最終CAD/組立・実C++物理試験の合格後に解放する | A＋B=56 |
| C 条件付き新規脛殻 | **4** | 既存脛殻再使用との択一 | 既存脛殻4個の局所加工・再使用が不成立の場合だけ選ぶ | A/B外。最大60 |


LD治具は実物寸法の確認に使う任意試験具で、手持ちPLA・基準面を下に置く候補向き（capは皿穴側、horn_adapterはポケット側を上）です。0.20 mmは候補値、壁数/充填率はA.massとスライサーで確定します。寸法確認後も廃棄せず保管し、一体化済みケージや蓋を二重計上しません。

## 少量試作の順序と合否

`currently_printable_quantity=0` のまま、実測に必要な最小試作だけをこの順で判定する。LD保持が不要と確認できた場合は治具を省略し、印刷済み品を増やさない。

1. **LD適合治具（必要時のみ）**: `ld220_cradle`、`ld220_cap`、`ld220_horn_adapter`を各1個。ケース外形・取付軸・ホーン径/PCD・ねじ・工具進入を実物で照合し、保持でき、干渉せず、無理な押し込みなしに取り外せること。3種とも非本番。候補値の実測前採用はNo-Go。
2. **TPU靴1個**: 1脚へ取り付け、四隅の接地、保持/引抜き、足裏の滑り、低速可動、配線/工具の進入を確認する。静荷重を段階的に加えて白化・裂け・永久変形・接着剥離がなく、硬いトゥが先行接地しないこと。接触力・材料強度・連続歩行は別ゲートで、合格を全4個の解放へ拡張しない。
3. **隠し補強1個（候補形状を確定できた場合のみ）**: 既存部品の内側へ仮合わせし、外観面を変えず、支持面の全周接触、ねじ/接着面、工具・配線経路、静荷重時のたわみと脱落を確認する。候補を確定できない間は数量0・量産未解放とし、首補強候補11種を同時印刷しない。
各段階の合否は、使用STLのSHA、材料/積層、対象個体、寸法、荷重、変形、写真/動画を記録してから次段へ進める。ホスト検査やnative traceのPASSだけで実機完成とは判定しない。

| ID | 採用候補STL（相対path） | SHA-256 | 材料 | 材料条件 | 体積 / 表面積 | 概算質量 / 完全充填上限 | 設計合計 / 試作 / 残数 | 今印刷可 | 向き | 置換旧部品 | 量産前ゲート |
|---|---|---|---|---|---:|---:|---:|---:|---|---|---|
| PF-FOOT-TPU | `outputs/print-first-20260905/feet/tpu_shoe_print.stl` | `a47d91a5d082…` | TPU95A | TPU95A / 壁2.4mm / 充填率100%（EXPLICIT_PRINT_FIRST_NON_PF['tpu_shoe']） | V 24236.5 / A 10868.9 | 29.33 g / 29.33 g | 4 / 1 / 3 | 0 | 印刷面を床、耳を上。穴軸を傾けずこの印刷向きを使用 | foot_pad, Leg_Toe_Black_x12 | 幾何候補を確認済み。1靴の嵌合・接地・荷重・TPU耳5.24%ひずみを実物確認し、最終sim接触力を比較 |
| PF-FOOT-SPACER-PRINT | `outputs/print-first-20260905/feet/pla_spacer_print.stl` | `c24de9e2659a…` | PLA | PLA / 壁2.4mm / 充填率100%（EXPLICIT_PRINT_FIRST_NON_PF['pla_spacer']） | V 459.8 / A 557.5 | 0.57 g / 0.57 g | 16 / 4 / 12 | 0 | フランジ面を床、穴を縦。単一部品を4脚×上下左右へ配置 | — | 1脚分4個の実ねじ・ナット・殻との適合を確認してから残り12個 |
| PF-SHELL-NEW-STD | `outputs/print-first-20260905/feet/shin_shell_retained_print.stl` | `fa2237b8b20f…` | PLA | PLA / 壁1.4mm / 充填率8%（EXPLICIT_PRINT_FIRST_NON_PF['shin_shell_retained']） | V 69806.6 / A 18869.7 | 37.06 g / 86.56 g | 2 / 1 / 1 | 0 | 元STLの印刷向きを維持（局所加工版） | shin_shell | 1脚の殻・耳・ねじ座の適合と印刷状態を確認してから残数 |
| PF-SHELL-NEW-MIRROR | `outputs/print-first-20260905/feet/shin_shell_retained_m_print.stl` | `c15cc32f52ef…` | PLA | PLA / 壁1.4mm / 充填率8%（EXPLICIT_PRINT_FIRST_NON_PF['shin_shell_retained']） | V 69806.3 / A 18869.6 | 37.06 g / 86.56 g | 2 / 0 / 2 | 0 | 元STLの印刷向きを維持（局所加工版） | shin_shell | 1脚の殻・耳・ねじ座の適合と印刷状態を確認してから残数 |
| PF-SHELL-EXISTING-STD | `outputs/print-first-20260905/feet/shin_shell_retained_foot_frame.stl` | `7e662fb53a21…` | PLA | PLA / 壁1.4mm / 充填率8%（EXPLICIT_PRINT_FIRST_NON_PF['shin_shell_retained']） | V 69806.6 / A 18869.7 | 37.06 g / 86.56 g | 2 / 1 / 1 | 0 | 既存印刷物を加工するため新規印刷しない | shin_shell | 現物4個の版・寸法・加工後の嵌合を確認してから使用 |
| PF-SHELL-EXISTING-MIRROR | `outputs/print-first-20260905/feet/shin_shell_retained_m_foot_frame.stl` | `2297ae9a4e66…` | PLA | PLA / 壁1.4mm / 充填率8%（EXPLICIT_PRINT_FIRST_NON_PF['shin_shell_retained']） | V 69806.3 / A 18869.6 | 37.06 g / 86.56 g | 2 / 0 / 2 | 0 | 既存印刷物を加工するため新規印刷しない | shin_shell | 現物4個の版・寸法・加工後の嵌合を確認してから使用 |
| PF-BODY-CHASSIS | `outputs/print-first-20260905/body/pf_chassis.stl` | `2a90ad5f5953…` | PLA | PLA / 壁2.4mm / 充填率25%（PRINT_FIRST_MATERIALS['pf_chassis']） | V 142899.7 / A 86271.5 | 177.20 g / 177.20 g | 1 / 1 / 0 | 0 | STL原点を保持。実部品を置いたスライサー画面で最終確認 | chassis | A.massの材料/壁数/充填率と一致させ、全体固定部品・頭/Cabin・配線・干渉を再検査 |
| PF-BODY-HEAD_TOP_CLEARANCED | `outputs/print-first-20260905/body/pf_head_top_clearanced.stl` | `b6024dda92ec…` | PLA | PLA / 壁2.4mm / 充填率8%（PRINT_FIRST_MATERIALS['pf_head_top_clearanced']） | V 49246.1 / A 43973.0 | 61.07 g / 61.07 g | 1 / 1 / 0 | 0 | STL原点を保持。実部品を置いたスライサー画面で最終確認 | Head_Top_Eyecut#single | A.massの材料/壁数/充填率と一致させ、全体固定部品・頭/Cabin・配線・干渉を再検査 |
| PF-BODY-CABIN_RAIL_L | `outputs/print-first-20260905/body/pf_cabin_rail_l.stl` | `fe8716efba02…` | PLA | PLA / 壁2.4mm / 充填率40%（PRINT_FIRST_MATERIALS['pf_cabin_rail']） | V 51150.7 / A 25068.9 | 63.43 g / 63.43 g | 1 / 1 / 0 | 0 | STL原点を保持。実部品を置いたスライサー画面で最終確認 | — | A.massの材料/壁数/充填率と一致させ、全体固定部品・頭/Cabin・配線・干渉を再検査 |
| PF-BODY-CABIN_RAIL_R | `outputs/print-first-20260905/body/pf_cabin_rail_r.stl` | `5f29a29d12cd…` | PLA | PLA / 壁2.4mm / 充填率40%（PRINT_FIRST_MATERIALS['pf_cabin_rail']） | V 51160.4 / A 25069.7 | 63.44 g / 63.44 g | 1 / 1 / 0 | 0 | STL原点を保持。実部品を置いたスライサー画面で最終確認 | — | A.massの材料/壁数/充填率と一致させ、全体固定部品・頭/Cabin・配線・干渉を再検査 |
| PF-BODY-ELECTRONICS_SHELF_0 | `outputs/print-first-20260905/body/pf_electronics_shelf_0.stl` | `d2a76e6cf2e8…` | PLA | PLA / 壁2.4mm / 充填率40%（PRINT_FIRST_MATERIALS['pf_electronics_shelf']） | V 8438.7 / A 8165.1 | 10.46 g / 10.46 g | 1 / 1 / 0 | 0 | STL原点を保持。実部品を置いたスライサー画面で最終確認 | — | A.massの材料/壁数/充填率と一致させ、全体固定部品・頭/Cabin・配線・干渉を再検査 |
| PF-BODY-ELECTRONICS_SHELF_1 | `outputs/print-first-20260905/body/pf_electronics_shelf_1.stl` | `48c6ed88a6d6…` | PLA | PLA / 壁2.4mm / 充填率40%（PRINT_FIRST_MATERIALS['pf_electronics_shelf']） | V 8437.1 / A 8148.3 | 10.46 g / 10.46 g | 1 / 1 / 0 | 0 | STL原点を保持。実部品を置いたスライサー画面で最終確認 | — | A.massの材料/壁数/充填率と一致させ、全体固定部品・頭/Cabin・配線・干渉を再検査 |
| PF-BODY-ELECTRONICS_SHELF_2 | `outputs/print-first-20260905/body/pf_electronics_shelf_2.stl` | `607899e17f3d…` | PLA | PLA / 壁2.4mm / 充填率40%（PRINT_FIRST_MATERIALS['pf_electronics_shelf']） | V 8662.7 / A 8692.6 | 10.74 g / 10.74 g | 1 / 1 / 0 | 0 | STL原点を保持。実部品を置いたスライサー画面で最終確認 | — | A.massの材料/壁数/充填率と一致させ、全体固定部品・頭/Cabin・配線・干渉を再検査 |
| PF-BODY-MOUTH_KEY | `outputs/print-first-20260905/body/pf_mouth_key.stl` | `83991c140422…` | PLA | PLA / 壁2.4mm / 充填率50%（PRINT_FIRST_MATERIALS['pf_mouth_key']） | V 868.8 / A 1003.2 | 1.08 g / 1.08 g | 1 / 1 / 0 | 0 | STL原点を保持。実部品を置いたスライサー画面で最終確認 | Mouth_Key_Grey#single | A.massの材料/壁数/充填率と一致させ、全体固定部品・頭/Cabin・配線・干渉を再検査 |
| PF-BODY-CAMERA_CARRIER | `outputs/print-first-20260905/body/pf_camera_carrier.stl` | `9b2ff67a5a55…` | PLA | PLA / 壁2.4mm / 充填率40%（PRINT_FIRST_MATERIALS['pf_camera_carrier']） | V 2321.5 / A 3517.8 | 2.88 g / 2.88 g | 1 / 1 / 0 | 0 | STL原点を保持。実部品を置いたスライサー画面で最終確認 | camera_carrier | A.massの材料/壁数/充填率と一致させ、全体固定部品・頭/Cabin・配線・干渉を再検査 |
| PF-BODY-EYE_POD_CAMERA_CLEARANCED | `outputs/print-first-20260905/body/pf_eye_pod_camera_clearanced.stl` | `1688de600f2c…` | PLA | PLA / 壁2.4mm / 充填率8%（PRINT_FIRST_MATERIALS['pf_eye_pod_camera_clearanced']） | V 10022.5 / A 4772.7 | 12.43 g / 12.43 g | 1 / 1 / 0 | 0 | STL原点を保持。実部品を置いたスライサー画面で最終確認 | eye_pod_camera | A.massの材料/壁数/充填率と一致させ、全体固定部品・頭/Cabin・配線・干渉を再検査 |
| PF-BODY-FIXED_CLAW_R | `outputs/print-first-20260905/body/pf_fixed_claw_r.stl` | `2d578b453824…` | PLA | PLA / 壁2.4mm / 充填率50%（PRINT_FIRST_MATERIALS['pf_fixed_claw']） | V 4281.1 / A 2818.1 | 5.31 g / 5.31 g | 1 / 1 / 0 | 0 | STL原点を保持。実部品を置いたスライサー画面で最終確認 | claw_mount, Arm_Left_Claw_Grey, Arm_Left_Finger_Black_x3#0, Arm_Left_FingerTip_Grey_x3#0, Arm_Left_Finger_Black_x3#1, Arm_Left_FingerTip_Grey_x3#1, Arm_Left_Finger_Black_x3#2, Arm_Left_FingerTip_Grey_x3#2 | A.massの材料/壁数/充填率と一致させ、全体固定部品・頭/Cabin・配線・干渉を再検査 |
| PF-BODY-FIXED_CLAW_L | `outputs/print-first-20260905/body/pf_fixed_claw_l.stl` | `4130138afb72…` | PLA | PLA / 壁2.4mm / 充填率50%（PRINT_FIRST_MATERIALS['pf_fixed_claw']） | V 4281.1 / A 2818.1 | 5.31 g / 5.31 g | 1 / 1 / 0 | 0 | STL原点を保持。実部品を置いたスライサー画面で最終確認 | claw_mount, Arm_Left_Claw_Grey, Arm_Left_Finger_Black_x3#0, Arm_Left_FingerTip_Grey_x3#0, Arm_Left_Finger_Black_x3#1, Arm_Left_FingerTip_Grey_x3#1, Arm_Left_Finger_Black_x3#2, Arm_Left_FingerTip_Grey_x3#2 | A.massの材料/壁数/充填率と一致させ、全体固定部品・頭/Cabin・配線・干渉を再検査 |

## 質量と材料の参考集計

各STLの表面積・実体積を読み、configの最長prefix規則（材料・壁厚・充填率）を適用した。『概算』は `tools/export_urdf.py:estimate_mass_g` と同じ表面積×壁厚＋残体積×充填率、『完全充填上限』は実体積×密度である。支持材・失敗分・実スライサー値・手持ち残量・実測重量は未確認であり、印刷可数は別表のとおり0のまま。

| 層 | 個数 | 概算 PLA | 概算 TPU95A | 完全充填 PLA | 完全充填 TPU95A | 状態 |
|---|---:|---:|---:|---:|---:|---|
| 設計必要数（量産未解放） | 56 | 973.7889 g | 117.3048 g | 979.1898 g | 117.3048 g | body16 + legs20 + 靴4 + spacer16 |
| 初回内数 | 31 | 701.4980 g | 29.3262 g | 704.1984 g | 29.3262 g | body16 + ユニーク脚STL10 + 靴1 + spacer4 |
| 適合後残数 | 25 | 272.2909 g | 87.9786 g | 274.9913 g | 87.9786 g | legs10 + 靴3 + spacer12 |
| 条件付き新規脛殻 | 4 | 148.2478 g | — | 346.2401 g | — | 既存4個の局所加工不可時だけ。量産へ加えず設計上限60 |

完全充填上限の旧互換集計は body **433.7678 g**、legs **536.3005 g**、feet（靴＋スペーサー） **126.4263 g**、合計 **1096.4946 g**です。既存殻再使用は新規印刷材料へ足さず、新規殻4個とは択一です。

| 設計必要数56（量産未解放）の内訳 | 概算 | 完全充填上限 | 個数 |
|---|---:|---:|---:|
| body | 433.7678 g | 433.7678 g | 16 |
| legs | 530.8997 g | 536.3005 g | 20 |
| TPU靴 | 117.3048 g | 117.3048 g | 4 |
| PLAスペーサー | 9.1215 g | 9.1215 g | 16 |

設計必要数は body 16、legs 20、TPU靴 4、PLAスペーサー 16 の **56個**です。これは量産未解放の設計値です。既存脛殻4個の局所加工・再使用と新規脛殻4個は択一で、設計上限は60個。LD治具3種各1は非本番です。

## 組立参照（数量0）

foot-frame版は組立座標の参照であり、印刷向き候補と同じ論理部品を二重計上しない。

| ID | 相対path | SHA-256 | 材料 | 数量 | 役割 | 理由 |
|---|---|---|---|---:|---|---|
| REF-FOOT-TPU-FRAME | `outputs/print-first-20260905/feet/tpu_shoe_foot_frame.stl` | `d4d23df508f2…` | TPU95A | 0 | 組立参照 | 印刷向きSTLとの同一論理部品の重複を避け、組立座標だけを確認 |
| REF-FOOT-SPACER-POSITIVE_Y | `outputs/print-first-20260905/feet/pla_spacer_positive_y_foot_frame.stl` | `0583fa0905eb…` | PLA | 0 | 組立参照 | 同一solidの配置/回転を確認する参照。本番数量は印刷向き単一部品16個だけ |
| REF-FOOT-SPACER-NEGATIVE_Y | `outputs/print-first-20260905/feet/pla_spacer_negative_y_foot_frame.stl` | `e08791bff267…` | PLA | 0 | 組立参照 | 同一solidの配置/回転を確認する参照。本番数量は印刷向き単一部品16個だけ |
| REF-FOOT-SPACER-UPPER_POSITIVE_Y | `outputs/print-first-20260905/feet/pla_spacer_upper_positive_y_foot_frame.stl` | `37f0b6e83f49…` | PLA | 0 | 組立参照 | 同一solidの配置/回転を確認する参照。本番数量は印刷向き単一部品16個だけ |
| REF-FOOT-SPACER-UPPER_NEGATIVE_Y | `outputs/print-first-20260905/feet/pla_spacer_upper_negative_y_foot_frame.stl` | `94322de634b3…` | PLA | 0 | 組立参照 | 同一solidの配置/回転を確認する参照。本番数量は印刷向き単一部品16個だけ |

## 最終凍結後に印刷可否を確定する候補

下表の数量は設計必要数として表示し、今印刷可数は上の層表で0と分離する。最終freeze2後にCAD・材料・実LD適合・全脚干渉を再確認する。

| ID | 相対path | SHA-256 | 材料/設定 | 設計必要数 | 今印刷可 | 理由 |
|---|---|---|---|---:|---:|---|
| PENDING-BODY-LD220_YAW_CAP_FR | `outputs/print-first-20260905/body/pf_ld220_yaw_cap_fr.stl` | `00953418fbc7…` | PLA / PLA / 壁2.4mm / 充填率50%（PRINT_FIRST_MATERIALS['pf_ld220_yaw_cap']）。実スライサーの積層・支持材・失敗分は未確認 | 1 | 0 | 新yaw蓋。configの材料・壁厚・充填率は反映済み。最終body/legs統合、A.mass、干渉を確認してから本番数を決める |
| PENDING-BODY-LD220_YAW_CAP_FL | `outputs/print-first-20260905/body/pf_ld220_yaw_cap_fl.stl` | `21d01e4f2e56…` | PLA / PLA / 壁2.4mm / 充填率50%（PRINT_FIRST_MATERIALS['pf_ld220_yaw_cap']）。実スライサーの積層・支持材・失敗分は未確認 | 1 | 0 | 新yaw蓋。configの材料・壁厚・充填率は反映済み。最終body/legs統合、A.mass、干渉を確認してから本番数を決める |
| PENDING-BODY-LD220_YAW_CAP_RL | `outputs/print-first-20260905/body/pf_ld220_yaw_cap_rl.stl` | `f62d58443bf8…` | PLA / PLA / 壁2.4mm / 充填率50%（PRINT_FIRST_MATERIALS['pf_ld220_yaw_cap']）。実スライサーの積層・支持材・失敗分は未確認 | 1 | 0 | 新yaw蓋。configの材料・壁厚・充填率は反映済み。最終body/legs統合、A.mass、干渉を確認してから本番数を決める |
| PENDING-BODY-LD220_YAW_CAP_RR | `outputs/print-first-20260905/body/pf_ld220_yaw_cap_rr.stl` | `043b2d70ad56…` | PLA / PLA / 壁2.4mm / 充填率50%（PRINT_FIRST_MATERIALS['pf_ld220_yaw_cap']）。実スライサーの積層・支持材・失敗分は未確認 | 1 | 0 | 新yaw蓋。configの材料・壁厚・充填率は反映済み。最終body/legs統合、A.mass、干渉を確認してから本番数を決める |
| PENDING-LEGS-PF_COXA_BRACKET | `outputs/print-first-20260905/legs/pf_coxa_bracket.stl` | `a474dfab4c3f…` | PLA / PLA / 壁2.4mm / 充填率50%（PRINT_FIRST_MATERIALS['pf_coxa_bracket']）。実スライサーの積層・支持材・失敗分は未確認 | 2 | 0 | 新LD統合脚部品。configの材料・壁厚・充填率は反映済み。最終CAD、実LD個体適合、A.mass、全脚干渉を確認してから本番数を決める |
| PENDING-LEGS-PF_FEMUR_LINK | `outputs/print-first-20260905/legs/pf_femur_link.stl` | `3f7294322321…` | PLA / PLA / 壁2.4mm / 充填率50%（PRINT_FIRST_MATERIALS['pf_femur_link']）。実スライサーの積層・支持材・失敗分は未確認 | 2 | 0 | 新LD統合脚部品。configの材料・壁厚・充填率は反映済み。最終CAD、実LD個体適合、A.mass、全脚干渉を確認してから本番数を決める |
| PENDING-LEGS-PF_TIBIA_LINK | `outputs/print-first-20260905/legs/pf_tibia_link.stl` | `8cde6a079275…` | PLA / PLA / 壁2.4mm / 充填率50%（PRINT_FIRST_MATERIALS['pf_tibia_link']）。実スライサーの積層・支持材・失敗分は未確認 | 2 | 0 | 新LD統合脚部品。configの材料・壁厚・充填率は反映済み。最終CAD、実LD個体適合、A.mass、全脚干渉を確認してから本番数を決める |
| PENDING-LEGS-PF_LD220_COXA_CAP | `outputs/print-first-20260905/legs/pf_ld220_coxa_cap.stl` | `f846205a3fea…` | PLA / PLA / 壁2.4mm / 充填率50%（PRINT_FIRST_MATERIALS['pf_ld220_coxa_cap']）。実スライサーの積層・支持材・失敗分は未確認 | 2 | 0 | 新LD統合脚部品。configの材料・壁厚・充填率は反映済み。最終CAD、実LD個体適合、A.mass、全脚干渉を確認してから本番数を決める |
| PENDING-LEGS-PF_LD220_FEMUR_CAP | `outputs/print-first-20260905/legs/pf_ld220_femur_cap.stl` | `129177deabd8…` | PLA / PLA / 壁2.4mm / 充填率50%（PRINT_FIRST_MATERIALS['pf_ld220_femur_cap']）。実スライサーの積層・支持材・失敗分は未確認 | 2 | 0 | 新LD統合脚部品。configの材料・壁厚・充填率は反映済み。最終CAD、実LD個体適合、A.mass、全脚干渉を確認してから本番数を決める |
| PENDING-LEGS-PF_COXA_BRACKET_M | `outputs/print-first-20260905/legs/pf_coxa_bracket_m.stl` | `66959b69c422…` | PLA / PLA / 壁2.4mm / 充填率50%（PRINT_FIRST_MATERIALS['pf_coxa_bracket']）。実スライサーの積層・支持材・失敗分は未確認 | 2 | 0 | 新LD統合脚部品。configの材料・壁厚・充填率は反映済み。最終CAD、実LD個体適合、A.mass、全脚干渉を確認してから本番数を決める |
| PENDING-LEGS-PF_FEMUR_LINK_M | `outputs/print-first-20260905/legs/pf_femur_link_m.stl` | `805d0ad692ce…` | PLA / PLA / 壁2.4mm / 充填率50%（PRINT_FIRST_MATERIALS['pf_femur_link']）。実スライサーの積層・支持材・失敗分は未確認 | 2 | 0 | 新LD統合脚部品。configの材料・壁厚・充填率は反映済み。最終CAD、実LD個体適合、A.mass、全脚干渉を確認してから本番数を決める |
| PENDING-LEGS-PF_TIBIA_LINK_M | `outputs/print-first-20260905/legs/pf_tibia_link_m.stl` | `8e7d4664c39f…` | PLA / PLA / 壁2.4mm / 充填率50%（PRINT_FIRST_MATERIALS['pf_tibia_link']）。実スライサーの積層・支持材・失敗分は未確認 | 2 | 0 | 新LD統合脚部品。configの材料・壁厚・充填率は反映済み。最終CAD、実LD個体適合、A.mass、全脚干渉を確認してから本番数を決める |
| PENDING-LEGS-PF_LD220_COXA_CAP_M | `outputs/print-first-20260905/legs/pf_ld220_coxa_cap_m.stl` | `a0ed92195ddf…` | PLA / PLA / 壁2.4mm / 充填率50%（PRINT_FIRST_MATERIALS['pf_ld220_coxa_cap']）。実スライサーの積層・支持材・失敗分は未確認 | 2 | 0 | 新LD統合脚部品。configの材料・壁厚・充填率は反映済み。最終CAD、実LD個体適合、A.mass、全脚干渉を確認してから本番数を決める |
| PENDING-LEGS-PF_LD220_FEMUR_CAP_M | `outputs/print-first-20260905/legs/pf_ld220_femur_cap_m.stl` | `b91ecfbae3be…` | PLA / PLA / 壁2.4mm / 充填率50%（PRINT_FIRST_MATERIALS['pf_ld220_femur_cap']）。実スライサーの積層・支持材・失敗分は未確認 | 2 | 0 | 新LD統合脚部品。configの材料・壁厚・充填率は反映済み。最終CAD、実LD個体適合、A.mass、全脚干渉を確認してから本番数を決める |

## 生産数へ含めない候補

| ID | 相対path | SHA-256 | 理由 |
|---|---|---|---|
| EX-FOOT-DO_NOT_PRINT_SHELL_RELIEF_TOOL_FOOT_FRAME.STL | `outputs/print-first-20260905/feet/DO_NOT_PRINT_shell_relief_tool_foot_frame.stl` | `dcfa3e456a4e…` | 殻切削用の工具STL。印刷部品ではない |
| EX-FOOT-SHELL_RELIEF_TOOL_ENVELOPE_FOOT_FRAME.STL | `outputs/print-first-20260905/feet/shell_relief_tool_envelope_foot_frame.stl` | `13a1d7bef8f3…` | 殻切削包絡の工具STL。印刷部品ではない |
| EX-BODY-PF_ELECTRONICS_POST_L.STL | `outputs/print-first-20260905/body/pf_electronics_post_l.stl` | `16bdbf34c642…` | pf_cabin_rail_l/rへ一体化済み。単体印刷すると棚柱を二重計上する |
| EX-BODY-PF_ELECTRONICS_POST_R.STL | `outputs/print-first-20260905/body/pf_electronics_post_r.stl` | `de8c10951473…` | pf_cabin_rail_l/rへ一体化済み。単体印刷すると棚柱を二重計上する |
| EX-LD220-LD220_CRADLE.STL | `outputs/print-first-20260905/ld220-adapter/ld220_cradle.stl` | `6581a046130b…` | LDケージ/ホーン候補。実LDの軸端・主面・ホーン径PCDを実測する適合試験用として任意1組まで候補にできるが、寸法確定後に一体化案が不要なら印刷しない。本番数量0 |
| EX-LD220-LD220_HORN_ADAPTER.STL | `outputs/print-first-20260905/ld220-adapter/ld220_horn_adapter.stl` | `5ef1d4a7b5c7…` | LDケージ/ホーン候補。実LDの軸端・主面・ホーン径PCDを実測する適合試験用として任意1組まで候補にできるが、寸法確定後に一体化案が不要なら印刷しない。本番数量0 |
| EX-LD220-LD220_CAP.STL | `outputs/print-first-20260905/ld220-adapter/ld220_cap.stl` | `5c072c77e963…` | LDケージ/ホーン候補。実LDの軸端・主面・ホーン径PCDを実測する適合試験用として任意1組まで候補にできるが、寸法確定後に一体化案が不要なら印刷しない。本番数量0 |
| EX-LEGS-PF_LD220_KNEE_CAP.STL | `outputs/print-first-20260905/legs/pf_ld220_knee_cap.stl` | `cd17977ae809…` | legs/assembly.jsonに未収録の旧/未採用出力。本番数量0で隔離し、最終CAD凍結後に必要性を再評価 |
| EX-LEGS-PF_LD220_KNEE_CAP_M.STL | `outputs/print-first-20260905/legs/pf_ld220_knee_cap_m.stl` | `d1260a1a2d2f…` | legs/assembly.jsonに未収録の旧/未採用出力。本番数量0で隔離し、最終CAD凍結後に必要性を再評価 |
| EX-LEGS-PF_LD220_PITCH_CAP.STL | `outputs/print-first-20260905/legs/pf_ld220_pitch_cap.stl` | `48a82b0b5669…` | legs/assembly.jsonに未収録の旧/未採用出力。本番数量0で隔離し、最終CAD凍結後に必要性を再評価 |
| EX-LEGS-PF_LD220_PITCH_CAP_M.STL | `outputs/print-first-20260905/legs/pf_ld220_pitch_cap_m.stl` | `c95602381cc2…` | legs/assembly.jsonに未収録の旧/未採用出力。本番数量0で隔離し、最終CAD凍結後に必要性を再評価 |

## 非本番LD適合治具の個別仕様

治具は量産・本番数に含めない。実物寸法の確認に使い、確認後も廃棄せず保管する。

| ID | 相対path | 設計必要数 | 今印刷可 | 材料/向き | 用途 |
|---|---|---:|---:|---|---|
| `ld220_cradle` | `outputs/print-first-20260905/ld220-adapter/ld220_cradle.stl` | 1 | 0 | 手持ちPLA（残量未確認） / 基準面をベッドへ、ケース挿入口（+Z側）を上 | 記録上の候補14個のうち現物未確認の対象でケース外形・軸端・ホーン径/PCD・ねじを確認する任意試験具。本番構成へ組み込まず、寸法・軸端・ホーン適合を確認した後も廃棄せず治具として保管 |
| `ld220_cap` | `outputs/print-first-20260905/ld220-adapter/ld220_cap.stl` | 1 | 0 | 手持ちPLA（残量未確認） / 皿穴側（+Z側）を上 | 記録上の候補14個のうち現物未確認の対象でケース外形・軸端・ホーン径/PCD・ねじを確認する任意試験具。本番構成へ組み込まず、寸法・軸端・ホーン適合を確認した後も廃棄せず治具として保管 |
| `ld220_horn_adapter` | `outputs/print-first-20260905/ld220-adapter/ld220_horn_adapter.stl` | 1 | 0 | 手持ちPLA（残量未確認） / ホーン受け面/ポケット側（+Z側）を上 | 記録上の候補14個のうち現物未確認の対象でケース外形・軸端・ホーン径/PCD・ねじを確認する任意試験具。本番構成へ組み込まず、寸法・軸端・ホーン適合を確認した後も廃棄せず治具として保管 |

## 固定の順序

1. 新STLのhash・material・版・向きを固定
2. 印刷向きTPU靴1個/段付きスペーサー4個の単体嵌合・ねじ締結を確認
3. 脚1本の支持・荷重・電源・停止を確認
4. body/legsのA.mass部品別material/wall/infillと一致させ、最終freeze2で生成assemblyの設計必要数56を突合
5. 全機の固定部品・電装・自己干渉を再生成して検査
6. 歩行シムを最終STL・質量・接触材料で再実行し、実機歩行は別に確認

## 根拠

- `outputs/print-first-20260905/feet/assembly.json` — `300ffa10eb31c16843b6e2f2452e20dd978184756311afb88708b2c7b371d75c`
- `outputs/print-first-20260905/body/assembly.json` — `91f8f9c2969e5a81ea9d880d64a700edd15804d0c13cef249dc4a76c887aa36b`
- `tools/check_print_first_feet.py` — `ddeb7758bbced16ec65bf0b88409f97b3d5592ff71322e0eedc8ca3859d1fce9`
- `tools/print_first_assembly.py` — `c3646df829e461d6332a1ae143eceaf89e9067ab1b4142fb55ec4bfacaae0f11`
- `hardware/src/make_print_first_feet.py` — `753e102f5051b03ff21463e6d3333b708222eae2c1804e3287c47cc9b60eea0a`
- `hardware/src/make_print_first_body.py` — `8907cca777be74329056e5413d3c8fc8b6fe193eda71a0029ee63ce86a67de4f`
- `hardware/src/make_print_first_leg.py` — `9977608c616d5257dfe4121ba0161371a06acfee9fc689451d1acd01900f897c`
- `hardware/src/config.py` — `f261e04ea2b615dafa861477cc89d0e43f8f32eca8976a93d1cdac3f6fd3dda1`
- `tools/print_first_components.py` — `878227ee9bc0c6cf6bee3446418f17223f9b8322b291d2ea60f0435db89bc950`
- `tools/make_print_first_manifest.py` — `23d460a1f98bac31a3a9ae6a58da1d8a2fc7eb24350048dc4740cafb42b3e31e`
