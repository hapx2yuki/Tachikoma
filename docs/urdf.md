# URDF (フル見た目版) — hardware/urdf/

`tools/export_urdf.py` が `hardware/src/config.py` (寸法の唯一の正) と
`tools/make_visuals.py` の `robot_meshes(dress=True)` (FK・パーツ→リンク
対応の「正解データ」) から自動生成する。**手編集しないこと** — 変更は
config.py / make_visuals.py 側を直し、`export_urdf.py` を再実行する。

生成:

```
.venv/bin/python tools/export_urdf.py            # hardware/urdf/ 一式を生成
.venv/bin/python tools/check_urdf.py             # 生成物の検証 (下記 [1]〜[8])
.venv/bin/python tools/render_urdf_compare.py    # 比較レンダ2枚を再生成
```

出力:

- `hardware/urdf/tachikoma.urdf` — SI 単位 (m, kg, rad)
- `hardware/urdf/meshes/*.stl` — visual/collision メッシュ (メートル単位で
  焼き込み export 済み。`<mesh>` に `scale` 属性は使わない — importer 側の
  scale 解釈依存を避けるため)
- `hardware/urdf/parts_manifest.json` — 取り込んだ全パーツの一覧
  (link → パーツ名リスト)
- `hardware/urdf/render_urdf_stand.png` / `render_ref_stand.png` —
  同一視点の比較レンダ (URDF 実パース FK vs `robot_meshes(dress=True)`)

## Isaac Sim への取込手順

**バージョン注記 (2026-07-31 確認)**: 以下は Isaac Sim 5.x 系の URDF
Importer UI を前提にしている。監査タスクが当初想定していた
「Isaac Sim 2024.x/2025.x系」は、2026-07 時点で既に Isaac Sim 6.0
(2026年6月 GA) へ更新されており一世代前の版を指す。挙動の意図
(フローティングベースにする/固定ジョイントを維持する) 自体はバージョンに
依らず正しいが、UI 上の項目名は版によって異なりうるため、実際に使う
バージョンで一度は動作確認すること。

1. `hardware/urdf/` ディレクトリ全体 (urdf ファイル + `meshes/`) をコピーする
   (メッシュパスは `meshes/xxx.stl` の相対パスなので、フォルダ構成を保つ
   こと)。
2. Isaac Sim の **URDF Importer** で `tachikoma.urdf` を開く。
   - Joint drive: 12 脚関節 + 6 腕関節 + 2 目関節が `revolute` として
     インポートされる (`effort`/`velocity` は下記アクチュエータ節の値、
     `<limit>` の rad 値も併せてインポートされる)。
   - Base: 本ロボットはフローティングベース (脚で接地する四脚機) のため、
     ベースを固定しない設定を選ぶこと。UI 上の項目名はバージョンにより
     異なる — Isaac Sim 5.0/5.1 系では Links 設定に **"Moveable base"
     (選ぶべき方) / "Static base"** の二択、より古い版では **"Fixed Base"**
     チェックボックス (オフにする) として現れることを WebSearch で確認した
     (2026-07-31 確認, NVIDIA Isaac Sim 公式ドキュメント)。狙いは常に同じ
     — base_link を固定しない設定にすること。
   - Self Collision: 既定 (オフ) を推奨。collision メッシュは簡略凸包
     (下記) であり、密着する意匠パーツどうしの自己衝突誤検出が起きやすい。
   - **Merge Fixed Joints**: 既定値は `true` (`mergeFixedJoints`, WebSearch で
     2026-07-31 確認, NVIDIA Isaac Sim 公式 ImportConfig 定義)。本 URDF には
     `eye_pod_camera_fixed` → `camera_optical_fixed` という 2 段の fixed
     joint があり、既定のマージ挙動だと `camera_optical_frame` が
     `base_link` 側へ吸収されて個別 prim として残らない可能性がある
     (`eye_pod_camera`/`camera_optical_frame` はどちらも非ゼロの質量を
     持つため、質量を持つ fixed joint 配下リンクをマージしない例外がある
     バージョンでは実害が出ない可能性が高いが、バージョンによって挙動が
     異なりうる)。カメラセンサを `camera_optical_frame` にアタッチする
     予定がある場合は、インポート後にこの prim が個別に存在するか
     Stage で目視確認し、無ければ Merge Fixed Joints をオフにして
     再インポートすること。
3. インポート後、Isaac のジョイントドライブの**能動的な位置制御ゲイン**
   (stiffness/Kp, ArticulationController の PD ゲイン) は URDF 標準タグには
   存在しないため URDF に含まれていない — Isaac 側の ArticulationController
   や joint drive API で別途設定すること (**UNVERIFIED**: 具体的なゲイン値は
   未検討、まず適当な初期値で位置制御ループを組んでから調整する想定)。
   これとは別に、URDF 標準には**受動的な**関節減衰/摩擦を表す
   `<joint><dynamics damping="" friction=""/></joint>` タグが存在するが、
   `tachikoma.urdf` は全 22 関節でこのタグを使用していない (サーボの
   ギアボックス摩擦等は未モデル化, **UNVERIFIED**)。

## 座標規約

- **base_link 原点**: 股ヨー/股ピッチ軸が乗る水平面 (make_visuals.py の
  `robot_meshes()` でいう world z = `body_h` の高さ)。xy はシャーシ中心。
  4 本の脚のヨー軸は全てこの平面上 (z=0, base_link ローカル) にある。
- **地面との関係**: 標準立ち姿勢 (体高 `BODY_H_DEF=115mm`) では base_link
  原点は地面から 115mm の高さになる (`check_urdf.py [4]` で検証)。
- **各関節の zero 姿勢**は make_visuals.py の関節式 (`rot(角度,軸)`) が
  そのまま 0 になる姿勢であり、**firmware の角度規約 (サーボ中立 = 0°)
  と 1:1 対応する** — `URDF関節値[rad] = firmware指令角[deg] × π/180`。
  例外: `leg_*_yaw` は台座の取付方位 (`LEG_ANGLES`) を `<joint><origin>`
  の固定回転として吸収済みなので、URDF の関節値そのものが firmware の
  yaw 指令角 (`yaw_d`, 中立=0) と一致する。
- **左右ミラー (腕)**: `arm_l_*` は `arm_r_*` と**同じ符号の関節値**を
  与えると鏡像動作になるよう、関節origin/axisを「矢状面 (X=0) ミラーの
  2 重共役」で定義している (`tools/export_urdf.py` の `_mirror_frame`
  参照)。firmware 側もヨーのみ物理サーボ出力を `ARM_SIGN` で反転して同じ
  規約を実現している (`arms.h`)。**関節値そのものに追加の符号反転は
  不要** — 数値検証は `check_urdf.py [3]` (`make_visuals.arm_meshes(side=-1,
  ...)` との厳密一致) 済み。

## 標準立ち姿勢の関節値ベクトル (体高 115mm, `check_urdf.py [4]` で使用)

```
leg_fr_yaw=+18.00°  leg_fr_pitch=-28.09°  leg_fr_knee=+12.51°
leg_fl_yaw=-18.00°  leg_fl_pitch=-28.09°  leg_fl_knee=+12.51°
leg_rl_yaw= -2.00°  leg_rl_pitch=-28.09°  leg_rl_knee=+12.51°
leg_rr_yaw= +2.00°  leg_rr_pitch=-28.09°  leg_rr_knee=+12.51°
arm_r_yaw=0  arm_r_pitch=0  arm_r_elbow=0   (arm_l_* も同様に 0)
eye_r_roll=0  eye_l_roll=0
```

(yaw は `STANCE_ANGLES - LEG_ANGLES` = 前脚±18° / 後脚∓2° に一致 — 取付
方位からの追加ヨーであり、pitch/knee は 4 脚とも同一値になる。これは
`leg_ik` への到達目標が全脚で同一半径 `STANCE_R=129mm`・同一高さ
`-body_h` であるという幾何学的な対称性から自明に導かれる — `yaw` の
回転 (Z軸) は脚の高さに寄与しないため)

(STANCE 方位・`STANCE_R=129mm`・体高 115mm への直接到達点。歩容の重心
シフト (SWAY) には依存しない静的な「気をつけ」姿勢 — `gait.h` の実際の
歩容中はここから常時 SWAY ぶんだけ揺れる。SWAY を含む「立ち姿勢」を
phase=0 で代表させようとすると、`SWAY_LEAD` の窓境界がちょうど位相
0/1 に重なり前脚/後脚が非対称になる — `check_urdf.py [4]` 実装時に
判明。docs/urdf.md はこの静的姿勢を「標準立ち姿勢」と定義する。)

## 関節名 ⇔ PWM チャンネル対応表 (firmware/src/config.h 準拠)

| URDF 関節名 | PCA9685 | ch (グローバル= board×16+ローカル) | 備考 |
|---|---|---|---|
| leg_fr_yaw / pitch / knee | board0 (0x40) | 0 / 1 / 2 | `PCA_CH[FR]` |
| leg_fl_yaw / pitch / knee | board0 (0x40) | 3 / 4 / 5 | `PCA_CH[FL]` |
| leg_rl_yaw / pitch / knee | board0 (0x40) | 6 / 7 / 8 | `PCA_CH[RL]` |
| leg_rr_yaw / pitch / knee | board0 (0x40) | 9 / 10 / 11 | `PCA_CH[RR]` |
| (頭部ヨー, URDF関節なし) | board0 (0x40) | 12 | `CH_HEAD`。駆動対象は
  2026-07-30 実測で未確定 (docs/BOM.md #2 参照) — 本 URDF には頭部ヨー
  自体を関節として含めない (プロジェクトの確定方針) |
| arm_r_yaw / pitch / elbow | board1 (0x41) | 16 / 17 / 18 | `ARM_CH[0]`。
  ch19 (旧グリップ) は未使用のまま予約 |
| arm_l_yaw / pitch / elbow | board1 (0x41) | 20 / 21 / 22 | `ARM_CH[1]`。
  ch23 (旧グリップ) は未使用のまま予約 |
| eye_r_roll | board1 (0x41) | 24 | `EYE_CH[0]` |
| (中央目, 固定カメラ・サーボなし) | board1 (0x41) | 25 | `EYE_CH[1]`
  — 未使用 (eyes.h がスキップ)。URDF では `eye_pod_camera_fixed` (fixed
  joint) として存在するが可動関節ではない |
| eye_l_roll | board1 (0x41) | 26 | `EYE_CH[2]` |

## リンク構成表

| リンク | 種別 | 内容 (visual) |
|---|---|---|
| base_link | フローティングベース | chassis / pod_neck / battery_cradle
  + 頭部・砲身・ポッド外装一式 (Cabin_*/Head_*/Mouth_* の全 45 キット
  パーツ, kit_dress_static() 準拠) |
| leg_{fr,fl,rl,rr}_coxa | revolute (yaw) 子 | coxa_bracket(_m) |
| leg_{fr,fl,rl,rr}_femur | revolute (pitch) 子 | femur_link(_m) + thigh_cap
  + Leg_Thigh_Guard_Blue_x4 |
| leg_{fr,fl,rl,rr}_tibia | revolute (knee) 子 | tibia_link(_m) +
  leg_foot_bored + shin_shell(_m) + Leg_Shin_Guard_Grey_x4 +
  Leg_Toe_Black_x12 (×3) |
| arm_{r,l}_shoulder | revolute (yaw) 子 | shoulder_bracket |
| arm_{r,l}_upper | revolute (pitch) 子 | upper_arm + arm_pod_upper/lower +
  Arm_*_Guard_Grey + elbow_shell |
| arm_{r,l}_forearm | revolute (elbow) 子 | forearm + claw_mount +
  Arm_Left_Claw_Grey (鏡映共用) + Finger_Black×3 + FingerTip_Grey×3 (爪は
  固定, 可動 DOF なし) |
| eye_r_pod / eye_l_pod | revolute (roll) 子 | eye_pod (キョロキョロ) |
| eye_pod_camera | fixed 子 | eye_pod_camera + camera_carrier |
| camera_optical_frame | fixed 子 (visual なし) | ROS optical 規約の
  カメラ光学フレーム (+Z=光軸前方) |

## アクチュエータ effort/velocity (docs/urdf.md 出典表)

| サーボ | effort (N·m) | velocity (rad/s) | 出典 |
|---|---|---|---|
| DS3218 (脚 12軸) | 1.96 | 6.5 | 20 kgf·cm 級カタログ値を N·m 換算
  (1 kgf·cm=0.0980665N·m → 20×0.098≈1.96)。速度は 0.16s/60°級の一般的な
  20kg級デジタルサーボ値からの概算 [**UNVERIFIED**: 実個体のデータシート
  未確認、config.py 冒頭の「個体差・クローン差が大きい」注記のとおり] |
| MG90S (腕 6軸) | 0.22 | 13.0 | 2.2 kgf·cm 級カタログ値 (0.22N·m)。速度は
  MG90S 一般カタログ値 (0.1s/60°級) からの概算 [**UNVERIFIED**] |
| ES9251II 級 (目 2軸) | 0.03 | 8.0 | サブマイクロサーボの一般値からの概算
  [**UNVERIFIED**: config.py SUBMICRO 自体が「[要実測]」注記付き] |

## 慣性・質量モデルの前提 (簡略化の内訳を正直に記載)

- **visual/collision メッシュ由来の質量**: パーツごとに `trimesh` の均質
  密度 (density=1) 慣性/COM を求め、`tools/filament_calc.py` と同じ物理
  モデル (表面積×壁厚+インフィル×体積、材料密度) による質量見積りへ
  スケールする。**COM は均質密度のままの幾何重心**であり、実際の
  中空+インフィル構造 (特に壁2/インフィル8%の意匠シェル) は表面寄りに
  実 COM があるはず — 簡略化として残る誤差 [**UNVERIFIED**: 実測なし]。
- **サーボ本体**: 質量は DS3218=60g / MG90S=14g (docs/printing.md 重量
  バジェット表, filament_calc.py 実行値ベース) / ES9251II級=3.7g
  (config.py SUBMICRO docstring [要実測]) を使用。**搭載リンク側**
  (yaw サーボの本体はケースが回らないので base_link/coxa/shoulder のうち
  ケースを保持する側のリンク) に box 近似で配置 — 位置は関節軸まわり
  ±10-20mm 程度の**概算**であり、`hardware/src/make_leg.py`/`make_arm.py`
  の実 CAD 位置とは厳密には一致しない [**UNVERIFIED**]。
- **バッテリー/電装** (LiPo・ESP32・PCA9685×2・UBEC・DC-DC・DFPlayer・
  マイク/スピーカー/アンプ等): `tools/make_visuals.py wiring_video()` の
  配線イメージ用ボックス配置 (zb 基準) を base_link ローカルへ焼き直して
  再利用。質量は多くが **datasheet 未参照の概算**
  (バッテリー180g のみプロジェクトメモの参照値、他は形状からの類推)
  [**UNVERIFIED 多数**] — docs/BOM.md に実際の型番が決まり次第、
  `tools/export_urdf.py` の `base_link_electronics_items()` を実測値へ
  差し替えること。
- **頭部ヨーサーボ (SG90/MG90S, CH_HEAD)**: 駆動対象・搭載位置が
  2026-07-30 時点で未確定 (docs/BOM.md #2) なので、URDF には関節を
  設けず、質量のみ (9g, docs/printing.md 重量バジェットとの整合用) を
  base_link に計上した [**UNVERIFIED, 位置は完全な仮置き**]。
- **合計**: 上記全て込みで総質量 ≈ 2.78kg (2026-07-31 の ~2.86kg から
  2026-08-22 の Head_Top_Eyecut 内殻ホロー化 [印刷 95→64g 相当] で微減。
  `check_urdf.py [5]` が 2.5〜3.5kg の範囲内であることを検証。
  docs/printing.md の設計想定 ~3.0kg とおおむね整合)。

## collision (簡略凸包) の作り方

- 可動リンク (脚 3種・腕 3種・目・カメラ): そのリンクの visual メッシュ
  全部を合成した凸包を、面数上限 (200) まで `fast_simplification`
  (quadric decimation) で簡略化。脚の tibia リンクだけは非表示の
  `foot_pad.stl` (TPU 接地パッド) も合成対象に加え、接地点を確実に
  含める。
- base_link: 「シャーシ (chassis+pod_neck+battery_cradle)」「頭部
  (Head_*)」「ポッド (Cabin_*)」「砲身 (Mouth_*)」の 4 ブロックへ分け、
  ブロックごとに凸包化 (task 指定の「主要ブロック数個の凸包合成」)。
- 簡略化後の凸包は QEM 由来の数値誤差で厳密な数学的凸性を僅かに失う
  ことがある (実測: 自身を再凸包した体積との差はいずれも < 0.1%) —
  `check_urdf.py [8]` はこの体積差を「実質凸」の判定基準 (<1%) として
  使う。

## 既知の限界 (UNVERIFIED 項目まとめ)

1. 上記アクチュエータ effort/velocity・多くの電装質量は datasheet 未参照
   の概算。
2. 頭部ヨーサーボの搭載位置・駆動対象が未確定 (URDF は関節を持たない)。
3. サーボ本体・電装の box 近似位置は概算 (関節軸/配線イメージ由来の
   目安)。
4. 慣性は均質密度仮定であり、実際の中空+インフィル構造の COM ズレは
   未反映。`check_urdf.py [5b]` は leg_fr_coxa 1 リンクについてのみ、
   RHO/壁厚/インフィルを `tools/filament_calc.py` 側の値から独立転記して
   質量を再計算し drift を検出する — 他リンクの質量・COM・慣性テンソルの
   数値そのものは独立突合の対象外のまま [**UNVERIFIED**]。
5. Isaac 側の**能動的な**位置制御ゲイン (stiffness/Kp, ArticulationController
   の PD ゲイン) は URDF 標準タグに存在しないため含めていない — Isaac 側で
   別途設定が必要。これとは別に URDF 標準の**受動的な**
   `<dynamics damping="" friction=""/>` タグ自体は存在するが、本 URDF では
   未使用 (サーボのギアボックス摩擦等は未モデル化) [**UNVERIFIED**]。
6. `foot_pad` (TPU接地パッド) は隠しパーツのため **visual には含めない**
   (robot_meshes(dress=True) が描かないのに合わせた) が、**collision には
   含める** (実際の接地点のため)。実機の見た目とは非表示分だけ差がある
   ことに注意。
7. `camera_carrier` (カメラ子基板の隠し保持パーツ) は robot_meshes 側に
   対応する描画が無い独自追加 — `check_urdf.py [6]` はこれを既知の例外
   として扱う。
8. `kit_assembly.py` のキットパーツ配置 DB (`tools/data/kit_assembly_front.json`)
   で `Head_Insert_Black_x4` のうち instance 3/4 (4個中2個) が
   `unresolved` のまま現物合わせ計測が未完了 — `robot_meshes()` 側・本
   URDF 側の両方からこの 2 個が欠落している。上流の `kit_assembly.py`
   データ欠損であり `export_urdf.py` 固有のバグではないため、現物合わせ
   計測が完了次第 `kit_assembly_front.json` 側を埋めて再エクスポートする
   想定 [**UNVERIFIED**]。
9. visual STL 46 枚中 2 枚 (`arm_l_upper__vis_shell_blue.stl` /
   `arm_r_upper__vis_shell_blue.stl` = `arm_pod_upper`+`arm_pod_lower` の
   色マージ結果) が非 watertight (元の 2 パーツはそれぞれ単体では
   watertight — クラムシェル状に接する 2 つの閉じたソリッドを同色で
   結合・STL 往復させる過程で継ぎ目が非多様体になると推定, 2026-07-31
   確認)。レンダリング自体への実害はほぼ無い (法線/シェーディングが
   向きによって破綻しうる程度) — collision 側は 25/25 全て watertight
   (`check_urdf.py [8]`) なので物理シミュレーションには影響しない見込み
   [**UNVERIFIED**: 開放シェルの具体的な悪影響は未検証]。
10. **シミュレータ上での自己接触について (2026-07-31 リリーフカット
    再評価タスクで追記)**: 実機の脚は firmware `gait.h` の Gait::update()
    が `D_KNEE_MIN`/`D_KNEE_MAX` によるワークスペース射影を必ず適用する
    ため、crouch(pitch45°,knee30°) 級の極端な深屈み姿勢は歩容コマンドと
    しては構造的に出力され得ない (`tools/check_shin_arm_leg.py`
    `pk_reachable()` 参照)。`hardware/src/shell_mod.py` の shin_shell
    リリーフカット群も、この「firmware 到達可能集合」を基準に再評価し
    (2026-07-31)、到達可能集合内で干渉が起きないカットは撤去してキット
    形状へ復元した。**この保証は firmware のソフトウェアクランプに
    依存しており、URDF/Isaac Sim 側で関節角を直接駆動する場合はこの
    クランプを経由しない** — したがって Isaac Sim 等のシミュレータ上で
    ArticulationController 等により crouch(45,30) 級の極端姿勢を直接
    指令した場合、隣接脚同士の shin_shell が実際に接触し得る (実測:
    無対策で最大 8.49cm³、実機ではこの姿勢自体が到達不能なため発生し
    ない)。シミュレータでの学習・制御則設計時に極端姿勢が生成され得る
    場合は、Isaac 側でも同等のワークスペース射影 (またはジョイントリミット)
    をアプリケーション側で再現することを推奨する
    [**UNVERIFIED**: Isaac Sim 上での実際の接触挙動そのものは未実機検証、
    上記は shin_shell 実メッシュのブーリアン交差からの理論的帰結]。

## キット由来メッシュの出自について

`hardware/urdf/meshes/` に焼き込まれる visual メッシュの大部分は、
タチコマ 3D プリントキットの元 STL (`model/*.stl`) を 150% スケール・
現物合わせ変換で配置したもの。**元モデルはプロジェクトオーナー
(浦田氏) 自身の著作物である** (2026-07-31 本人確認済み) ため、
メッシュの取り扱いに第三者モデル提供元のライセンス上の制約はない。
骨格パーツ (chassis/coxa_bracket/femur_link/tibia_link/shoulder_bracket/
upper_arm/forearm/claw_mount/pod_neck/battery_cradle/eye_pod/
eye_pod_camera/camera_carrier 等) は `hardware/src/*.py` の独自設計。

**配布用 ZIP の再生成について (2026-07-31 QA 再検証で追記)**: リポジトリ
直下の `tachikoma_urdf_20260822.zip` (Isaac Sim 等での利用者向け配布物,
`hardware/urdf/` 一式 + 本ファイルを同梱) は `hardware/src/config.py` の
寸法変更 (特に `ARM_MOUNT_HUB_Y` のようなジオメトリに直結する定数) が
入るたびに手動で作り直す必要がある — 自動連動しない。配布・共有の
直前には、必ず ZIP のタイムスタンプが `config.py` の最終更新より新しい
ことを確認し、古ければ `build_all.py`→`tools/export_urdf.py`→
`tools/render_urdf_compare.py` を再実行してから
`zip -r -X tachikoma_urdf_20260822.zip hardware/urdf docs/urdf.md`
(または現在の日付を反映した新しいファイル名) で作り直すこと
(2026-07-31 QA 再検証で、`ARM_MOUNT_HUB_Y` 変更が反映されないまま
配布直前状態になっていたドリフトを実際に検出・修正した実例あり)。

## 検証ログの読み方 (check_urdf.py)

`check_urdf.py` は 8 節 (+ 節内の一部をさらに細分化する `5b`/`6b`/`6c` の
サブラベル) ・約 380 個の個別アサーションを出力する (2026-07-31 時点。
件数は check_urdf.py 自体の改修で変動するので、正確な値は末尾のサマリ
`合計 N 項目` を見ること)。各行頭が `OK`/`NG` で、末尾のサマリに
`RESULT: PASS`/`FAIL` が出る。CI 等で使う場合は終了コード (0=PASS, 1=FAIL)
を見ること。
