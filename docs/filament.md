# フィラメント購入リスト (Bambu Lab X2D 用)

必要量は `tools/filament_calc.py` による実メッシュ体積ベースの見積り
(表面積×壁厚+インフィルの物理モデル、誤差 ±30% 想定)。
購入量は **試作・印刷失敗・色替えパージ・将来の補修** を織り込んだ推奨値。

## 必要量 (正味) と購入推奨

| 材料 / 色 | 正味必要量 | 購入推奨 | 用途 | 備考 |
|---|---|---|---|---|
| **PLA 青** | ~833g | **1kg × 2** | Cabin/Head/脚シェルの本体色 | 最大消費色。失敗許容+補修用に 2 巻。タチコマらしい青は Bambu PLA Basic の Blue 系かマットブルー。2026-07-31 shin_shell のリリーフカット再評価で +17g/セット。**2026-08-22 Head_Top_Eyecut の内殻ホロー化 (電装との共存に必須の機構逃がし, `tools/make_head_eyecut.py`) で 95→64g** — 正味合計は `tools/filament_calc.py` 2026-08-22 実行値 833g へ更新 |
| **PLA グレー** | ~189g | **1kg × 1** | 関節・砲・ディテール・足の甲 | thigh_cap / leg_foot_bored ×4 (元 Leg_Foot 加工版, ~12g) 含む |
| **PLA 黒** | ~40g | 1kg × 1 (他用途兼用) | つま先・インサート類・**指 (Arm_Left_Finger_Black_x3 ×6, 2026-07-29 固定爪化で無加工キット部品として印刷)** | 手持ちがあれば購入不要の量 |
| **PLA 白 (またはナチュラル)** | ~29g | 1kg × 1 (他用途兼用) | 目パーツ (可動眼球 eye_pod ×2 = 左右, 元キット Head_Eye_White 形状。中央目 eye_pod_camera ×1 = 固定カメラ加工版, 同形状) | **LED 透光のため低インフィルで印刷** (壁2/8%)。半透明のナチュラルが最も光る。ドット穴は黒仕上げ |
| PLA 赤 | ~4g | 購入不要でも可 | 赤ランプ ×8 | 白で印刷して赤塗装/赤マーカーでも代替可。買うなら最小量 |
| **PETG (黒 or グレー)** | ~570g | **1kg × 1** | 脚・腕骨格 (claw_mount 込み)・シャーシ・ポッドネック・バッテリークレードル・目キャリア・カメラキャリア (構造材) | 放射配置 v3 (pod_neck 20g + battery_cradle 17g) 込み。pod_neck は 2026-07-31 頭部中央寄せタスクで頭部逃がしカット (`hardware/src/make_chassis.py _head_relief_cutter()`) を追加し 22g→20g (同日 QA 再検証で `HEAD_RELIEF_PROTECT_H` を6→8mmへ再増厚+テーパー追加した後の最終値、旧報告の19gから訂正。docs/assembly.md 参照)。battery_cradle は旧報告の13gが `tools/filament_calc.py` の実測 (17g, `docs/print_manifest.md` と一致) と食い違っていたため17gへ訂正。2026-07-29 固定爪化で腕骨格が可動グリッパ (旧 palm_base/grip_slider/grip_finger) 廃止により ~115g→~55g へ軽量化 (printing.md 参照, 全体 PETG では旧 621g から -49g)。片脚試作+claw_mount 先行印刷 (接着面の現物合わせ用, printing.md 参照) のやり直し分を含めて 1kg で足りる想定。色分け不要の単色プレート (指は PLA 黒側で計上済みなので PETG 側に黒指定の例外パーツは無い) |
| **TPU 95A** | ~2g | 最小巻 (500g 等) × 1 | foot_pad ×4 (leg_foot_bored 底面の隠し接地パッド。2026-07-28 Leg_Foot 化で旧 足先チップ を置換) | ほぼ余るので他プロジェクト兼用前提 |

購入目安合計: **PLA 4-5 巻 + PETG 1 巻 + TPU 1 巻 ≈ ¥12,000〜18,000**
(ブランドにより変動。UNVERIFIED: 発注時に要確認)

## X2D での運用メモ

- **デュアルノズルの活用**: 青+グレーの 2 色プレート (Cabin まわり) を
  ノズル分担すると AMS 色替えパージを大幅節約できる
- 骨格 (PETG) は色替え不要の単色プレートにまとめる (2026-07-29 固定爪化で
  黒指定の grip_finger [PETG] は廃止済み。指 Finger_Black は PLA の
  意匠シェル側で計上されるため、PETG プレートに色替え例外は無くなった)
- 白目パーツは単独プレートで (インフィル設定が他と違うため)
- 黒/白/赤の消費は僅少なので、AMS に常設のスプールがあればそれで足りる

## 消費内訳 (上位、`tools/filament_calc.py` 出力)

| パーツ | 印刷重量目安 |
|---|---|
| Cabin_Front_Blue (青, 2026-07-28 以降無加工) | ~363g |
| Cabin_Back (青) | ~171g |
| shin_shell ×4 (青) | ~164g (2026-07-31 リリーフカット再評価でキット形状復元、旧~147gから+17g) |
| Head_Top (青, 2026-08-22 内殻ホロー化 306→52cm³) | ~64g |
| Head_Bottom (青) | ~67g |
| tibia_link ×4 (PETG) | ~152g |
| chassis (PETG, 円形ハブ v3) | ~76g |
| pod_neck + battery_cradle (PETG, v3 追加) | ~37g (pod_neck 20g + battery_cradle 17g。pod_neck は2026-07-31 頭部逃がしカット+同日QA再検証の増厚/テーパー追加で22g→20gへ。battery_cradleは旧13g表記の誤りを17gへ訂正) |
| coxa_bracket ×4 (PETG, 標準2+ミラー2) | ~124g |
| femur_link ×4 (PETG, 標準2+ミラー2) | ~114g |
| 腕骨格一式 ×2腕 (PETG, shoulder_bracket+upper_arm+forearm+claw_mount) | ~55g |

(数値は壁2/インフィル6-8% (シェル)、壁4/25-40% (骨格) 前提)
