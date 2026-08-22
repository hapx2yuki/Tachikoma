# 印刷ガイド (Bambu Lab X2D)

X2D のビルドボリュームはシングルノズル 256×256×260mm / デュアルノズル
235.5×256×256mm (確認日 2026-07-27, 下記出典)。150% 最大パーツ
Cabin_Front ≈ 170×195×110mm はどちらのモードでも印刷可能。

- 出典: [goodprints3d: X2D Build Plate Size and Build Volume](https://www.goodprints3d.com/blogs/3d/bambu-lab-x2d-build-plate-size-and-build-volume-what-you-actually-get), [MatterHackers: Bambu Lab X2D Combo](https://www.matterhackers.com/store/l/bambu-lab-x2d-3d-printer/sk/MZX8MQA2)
- デュアルノズル活用例: 骨格 (PETG) と意匠 (PLA) の同時プレート、
  Cabin の色分け (青+グレー) をパージ少なく印刷

## 新規設計パーツ (hardware/stl/)

v3 (2026-07-28): 脚は放射配置の 45° ペア干渉対策で **標準×2 (FL,RR) +
ミラー版 `_m` ×2 (FR,RL)** に分かれた。取り違えると組めないので刻印か
マーカーで区別すること。

| パーツ | 数 | 材料 | 設定 | 向き |
|---|---|---|---|---|
| chassis | 1 | **PETG** | 壁4, インフィル 25% グリッド | そのまま (平置き) |
| pod_neck (ポッド接続梁) | 1 | **PETG** | 壁4, 40% | 平置き (梁を寝かせる) |
| battery_cradle | 1 | PETG | 壁3, 20% | 開口を上 (Z 反転) |
| coxa_bracket / coxa_bracket_m | 各2 | PETG | 壁4, 40% | 天板を下 (Z 反転) |
| femur_link / femur_link_m | 各2 | PETG | 壁4, 40% | STL のまま。ビルドプレートのみサポート |
| tibia_link / tibia_link_m | 各2 | PETG | 壁4, 40% | 立てて印刷 |
| leg_foot_bored (元 Leg_Foot_Grey_x4_Repaired 加工) | 4 | **PLA グレー** | 壁3, 20% | プラグ側 (tibia 差込面) を下 (対称・共通) |
| foot_pad (隠し接地パッド, 完全内蔵) | 4 | **TPU 95A** | 壁3, 30% | フランジ面を下 |
| shin_shell / shin_shell_m | 各2 | PLA (青) | 壁2, 6% | STL のまま (上端平面が下) |
| thigh_cap | 4 | PLA (グレー) | 壁2, 6% | カット平面が下 (対称・共通) |
| shoulder_bracket (+_L) | 各1 | PETG | 壁4, 40% | 上面 (ホーンポケット側) を下 |
| arm_pod_upper/lower (+_L) | 各1 | **PLA 青** (元 Arm ポッド加工) | 壁2, 8% | カット面を下 |
| elbow_shell (+_L) | 各1 | PLA グレー (元 Elbow 球加工) | 壁2, 15% | カット面を下 |
| upper_arm (+_L) | 各1 | PETG | 壁4, 40% | STL のまま |
| forearm (+_L) | 各1 | PETG | 壁4, 40% | STL のまま (2026-07-29 固定爪化で 24→16mm に短縮) |
| claw_mount (+_L) | 各1 | PETG | 壁4, 40% | 円盤面を下 (爪ハブへの接着面が上) |
| eye_pod (眼球, **元キット Head_Eye_White 形状**, 左右キョロキョロ) | 2 | **PLA 白** | 壁2, 8% (LED 透過) | 背面を下 |
| eye_carrier | 2 | PETG | 壁4, 40% | 上面 (ポケット側) を上 |
| eye_pod_camera_shell (中央目ドーム部。一体版 eye_pod_camera は印刷性のため 2026-08-19 に 2 分割 — 一体版 STL は検証/可視化用参照で**印刷しない**) | 1 | **PLA 白** | 壁2, 8% | STL のまま (底面ベタ置き・ドーム上向き。サポート不要) |
| eye_pod_camera_base (中央目ネックボス部 — 上記の分割相方。リングプラグ嵌合+接着で一体化) | 1 | **PLA 白** | 壁2, 8% | STL のまま (背面ベタ置き) |
| camera_carrier (カメラモジュール保持, 完全内蔵。2026-08-19 短縮 — base ポケットへ先入れする方式になり後方貫通尾とシェル接着ウィングを廃止) | 1 | PETG | 壁4, 40% | レンズポケット側を上 |
| Mouth_Cannon_Bored (元 Mouth_Cannon_Grey 加工) | 1 | **PLA グレー** | 壁2, 8% | 砲口を上 (印刷面より先端が出ないよう向き現物合わせ) |
| Mouth_Neck_Bored (元 Mouth_Neck_Blue 加工) | 1 | **PLA 青** | 壁2, 8% | STL のまま |
| Mouth_Ball_Bored (元 Mouth_Ball_Grey 加工) | 1 | **PLA グレー** | 壁2, 8% | STL のまま |
| audio_cradle_mic (マイク基板保持, 完全内蔵) | 1 | PETG | 壁4, 40%, レイヤー 0.12 | 軸を水平に寝かせて印刷 |
| audio_cradle_spk (スピーカー抜け止めワッシャ, 完全内蔵) | 1 | PETG | 壁4, 40% | リング面を下 |
| Head_Bottom_Armcut (元 Head_Bottom_Blue 加工, 2026-07-30 追加 / 2026-08-20 機構逃がしカット追加) | 1 | **PLA 青** | 壁2, 8% | **切断リング面を下** (X軸180°反転。ドーム外観面が上=サポート痕は内側のみ) |
| Head_Top_Eyecut (元 Head_Top_Blue 加工, `tools/make_head_eyecut.py` が単体生成 — **`build_all.py` の対象外**。**2026-08-22 v2: 目ボアに加え内部機構逃がし = 内殻ホロー化 306→52cm³ + 下部スカートのサーボケースノッチ ×6** — 旧 v1 の STL は中実の床が脚/腕サーボ・PCA と干渉し被せられないので必ず v2 以降を印刷する) | 1 | **PLA 青** | 壁2, 8% (~64g。`tools/filament_calc.py` と一致) | STL のまま (外観面は元 Head_Top と同一)。**内部空洞の天井は 45° コーンで彫り止めしてあり内部サポート原則不要**だが、スカートノッチ天面 (z≈20 の平坦ブリッジ ~24×46mm ×4) はブリッジ印刷になる — 垂れが出ても不可視の内部なので許容、気になるなら tree サポート (内部のみ) を許可 |

- 骨格はレイヤー 0.2mm / ノズル 0.4mm。**PETG 指定** (PLA は夏場の車内・
  直射日光でクリープする)
- **最初に coxa_bracket + femur_link + tibia_link + leg_foot_bored + foot_pad
  を各 1 だけ印刷**し、サーボ実測 → `hardware/src/config.py` 更新 → 再生成 →
  はめあい合格後に残りを印刷する (assembly.md の Go/No-Go 手順)
- **claw_mount も片手分だけ先行印刷**して爪ハブ (Arm_Left_Claw_Grey) との
  接着面の現物合わせ (フラット度確認・軽くヤスリ調整) を先に済ませておくと
  安心 (2026-07-29 固定爪化: 旧グリッパの試作反復手順から置換。可動部が
  無いため調整項目は接着面のみ)

## 意匠シェル (元 3MF のパーツ、150% で印刷)

Bambu Studio でプレートごと 150% にスケールして印刷する。

- **印刷するもの**: Cabin 一式 (2026-07-28 設計変更: カメラをポッドの
  メインアイから頭部中央目へ移設したため、**Front/Eye とも無加工の元
  パーツをそのまま印刷** — Back/Peg×2/Turret 左右+Peg×2/RedLight 大小×各4/
  Spinnarette×4/Insert 全種を含む) / Head 一式 (**Head_Top と
  Head_Eye_White に加え、2026-07-30 追加で Head_Bottom も除く** —
  いずれも下記の加工版/可動版で置換。Plate, Peg 上下, Bottom_Cap, Dome,
  Plug, Screw×2, Insert_Black×4 を含む) /
  Mouth 一式 (**Cannon/Neck/Ball を除く** — 3点とも下記の音声クレードル
  加工版で置換。Peg/Key/Cap はそのまま元パーツを印刷) /
  **Head_TailJoint 一式 (Blue/Ball/Peg — v3 でポッドネックの化粧に使用。
  後述)** / ガード類 (Leg_Thigh_Guard, Leg_Shin_Guard) / **Leg_Toe ×12**
  (無加工, `leg_foot_bored` の甲へ接着 — 下記「足 (Leg_Foot 化)」参照) /
  **Arm_Left/Right_Guard_Grey 各1 + Arm_Left_Claw_Grey ×2 + Arm_Left_Finger_Black_x3
  ×6 + Arm_Left_FingerTip_Grey_x3 ×6** (固定爪一式, 詳細は下記「腕の元パーツ」)
  → 元 3MF にあるパーツは骨格で機能置換されるもの以外**全て印刷して使う**
  (タチコマとしての体裁を保つ。接着位置はキット完成写真/ビルドガイド準拠)
- **Head_Top は `hardware/stl/Head_Top_Eyecut.stl` を単体印刷する** (150%
  スケール済み・目ソケット底へ φ30 貫通ボア加工済み。元の Head_Top は目
  ソケットの底が塞がっていて可動眼球を嵌められない)。プレートの Head_Top
  は印刷しない。元プレートで印刷してしまった場合はソケット底へ φ30 を
  ドリル+リーマ/ホールソーで手加工してもよい
- **Head_Bottom は `hardware/stl/Head_Bottom_Armcut.stl` を単体印刷する**
  (150% スケール済み・肩ヨー可動域全域で shoulder_bracket と干渉しないよう
  左右の腕ソケットを拡口加工済み。上記「Head 下部シェルの腕開口」参照。
  2026-07-30 追加でマウスソケット奥の配線受け穴 φ7mm も同時に焼き込み済み
  — 下記「音声内蔵」節参照)。プレートの Head_Bottom (加工前) は印刷しない
- **Head_Bottom の機構逃がしカット (2026-08-20 焼き込み確定)**: キット彫刻は
  ほぼ中実 (181.8cm³) で、シャーシプレート帯 26.9cm³・バッテリーパック実体
  22.6cm³ (+cradle 6.5cm³, -Y 挿入経路も閉塞)・前脚 coxa ヨー掃引 0.33cm³×2・
  pod_neck 1.0cm³ と物理的に共存不能だった (「シェル vs 内部メカ」の実メッシュ
  検証がそれまで存在しなかった)。make_head.py がプレート下面より上のクラウン
  全カット+バッテリー窓/挿入経路+coxa ノッチを焼き込み、残体積 26.8cm³ の
  浅いボウルになる (全干渉 0.000cm³、make_head.py `__main__` が恒常検算)。
  外観影響: 上下シェル合わせ目の下側 ~10mm の青帯が無くなりプレート縁が
  見える (プレート φ144 は元々頭球 φ124.4 より広く張り出す設計で、完全な
  外観維持には「プレート上面に載る化粧リング」の別パーツ化が必要 — 未実装、
  make_head.py docstring 参照)。取付は §3: ボウル上端リング面→プレート下面へ
  ホットボンド。残体積は 2026-08-20b のリムカスプ除去後 23.7cm³
- **Head_Bottom のリムカスプ除去 (2026-08-20b, ユーザー指摘)**: C1 平面カットが
  腕/マウスソケットの円形開口と交差する場所に残っていた羽根状のツノ (平面視で
  先端が尖り折れやすい) を、実測ベースの局所カッター箱で除去済み (腕ソケットは
  U字肩に、マウス脇の <2.4mm スリバーも撤去)。窓縁の「下で支えられたクサビ」や
  表面モールド線は折れないため意図的に残す。`make_head.py __main__` が
  突起級薄片ゼロ+マウスソケット周囲材の実在を恒常検算する
- **Mouth_Cannon/Neck/Ball は `hardware/stl/Mouth_{Cannon,Neck,Ball}_Bored.stl`
  を単体印刷する** (150% スケール済み・INMP441 マイク+φ20 スピーカーの内蔵
  クレードル用に内部ボーリング+配線ボア加工済み。外側の意匠 — 側面グリップ
  スリット/砲口フレアの外形を含む — は元パーツと同一, `tools/check_audio.py`
  [2] で無加工域のズレ<0.05mm を保証)。プレートの Mouth_Cannon/Neck/Ball
  (加工前) は印刷しない。組立は「音声内蔵」節を参照
- **Cabin_Eye_White/Cabin_Front_Blue は元 3MF プレートのまま無加工で印刷する**
  (2026-07-28 設計変更でカメラは頭部中央目へ移設されたため、ポッド側の
  メインアイ/前面には加工不要。上記「印刷するもの」の Cabin 一式に含む)
- **腕の元パーツ**: Arm_Left / Arm_Right (ポッド) と Elbow は**加工版**
  (arm_pod_upper/lower, elbow_shell — arm_shell.py が生成) を使うため
  プレートからは印刷しない。**Arm_Left/Right_Guard_Grey も各 1 印刷**
  (ポッドへ接着)。
  **固定爪一式 (2026-07-29, キット準拠固定爪化) は全て無加工で印刷する**:
  **Arm_Left_Claw_Grey を ×2** (爪ハブ, 両腕とも Left 版を鏡映使用 —
  Arm_Right_Claw_Grey は爪ハブと無関係の別形状 [開放骨組] なので**印刷不要**),
  **Arm_Left_Finger_Black_x3 を ×6** (3本×2腕, 爪ハブのペグへ差込),
  **Arm_Left_FingerTip_Grey_x3 を ×6** (指の根元へ接着, キット標準組立)。
  claw_mount (hardware/stl, PETG 印刷) の平坦面へ爪ハブを接着し、爪ハブの
  3本のペグへ指を差込+接着、指根元へ指先チップを接着する。可動グリッパ
  (旧 palm_base/grip_slider/grip_finger, サブマイクロ×2) は廃止済み
- **Head 下部シェルの腕開口 (2026-07-30 焼き込み確定, 従来の現物合わせを置換)**:
  腕は前面の顔球体 (Head 一式) の側面・マウス砲の両脇、**Head_Bottom に
  元から成形されている左右のソケット穴 (正面から ±40°, STL実測)** から
  吊り下がる。2026-07-28 時点の見立て (「加工不要 or 現物合わせで最小拡口」)
  は座標・半径の突合せのみで、shoulder_bracket の実メッシュと Head_Bottom
  シェルの実ブーリアン干渉を検証していなかった。2026-07-30 に実メッシュで
  検証したところ、**肩ヨー可動域全域 (中立姿勢を含む) で shoulder_bracket
  の取付プレートがシェル材と実体干渉する**ことが判明 (交差体積166-182mm³,
  hardware/src/make_head.py 参照)。よって **`hardware/stl/Head_Bottom_Armcut.stl`
  を Head_Bottom_Blue の代わりに印刷する** (肩ヨー可動域全域+安全マージンで
  shoulder_bracket と干渉しない拡口を焼き込み済み、左右対称。
  `tools/check_arm.py` [1c] が恒常回帰検証する)。**元プレートの
  Head_Bottom_Blue (加工前) は印刷しない**。現物合わせでの追加拡げは不要
  — 干渉しない前提で組める。※Cabin は背中に載る箱型ポッドで腕とは無関係
  (完成図参照)
- **Head_TailJoint の使い方 (v3)**: TailJoint_Blue コーン + TailJoint_Ball を
  **無加工のまま** 150% で印刷し、**pod_neck 梁の化粧スリーブ**として被せ
  接着する (公式フィギュアの「青コーン+球リングのネック」再現)。
  **2026-07-30 焼き込み確定 (従来の「梁先端4隅を現物合わせで面取り」を置換)**:
  TailJoint 側の内部ボアを実メッシュで精密実測したところ、単純な円形ボア
  ではなく、キット本来のテール可動関節用の **"Optional_Cross" 十字キー溝**
  で、半径が角度により 6.9〜11.2mm 相当で変動することが判明した (旧「φ17mm
  /半径8.4-8.7mm」という記載は粗いラジアルサンプリングによる平均値で、
  実際は非円形だった)。位置決め回転を保証する機構の無い接着継手でこの十字
  形状を当てにするのは不安定なため、**TailJoint 側は無加工のまま**とし、
  代わりに完全に自制御下で非可視の **pod_neck 側の梁先端 (被せ代 20mm) を
  丸ポスト (`NECK_POST_D`=12mm, 実測ワースト半径7.0mmに対し片側1.0mm安全代)
  へ絞り込み**、円錐カッターで対角から絞ることで角を残しつつ徐々に丸める
  (対角逃げ) 形状を `hardware/src/make_chassis.py pod_neck()` に焼き込んだ。
  梁の対角がどの回転位相でも必ず収まるため、組付け時の位相合わせは不要
  (現物合わせ不要)。TailJoint_Peg は元の用途 (可動テール) 用なので予備として
  保管
- **印刷しないもの (骨格/可動部で機能置換 — 見えない内部関節)**:
  Leg_HipJoint, Leg_HipJoint_Socket, Leg_Thigh (→thigh_cap),
  Leg_KneeJoint, Leg_Shin (→shin_shell), Leg_AnkleJoint (旧キットの球関節。
  本設計は tibia 先端に足を直結するため不要 — 下記「足 (Leg_Foot 化)」参照),
  Leg_Foot (→leg_foot_bored, 元形状の加工版。プレートの Leg_Foot 生データは
  印刷しない),
  **Head_Top_Blue (→Head_Top_Eyecut, 目ソケット底 φ30 貫通ボア加工版。
  2026-07-30 印刷マニフェスト照合でこの一覧への記載漏れを是正 — 実際の
  「印刷しない」判断自体は上記「意匠シェル」節の記述どおり従来から有効)**,
  **Head_Bottom_Blue (→Head_Bottom_Armcut, 腕ソケット拡口+マウス配線受け穴
  焼き込み版。上記参照)**,
  **Head_Eye_White (→左右 2 目は eye_pod 可動目 / 中央 1 目は eye_pod_camera
  固定カメラ目, いずれも元形状のまま)**,
  **Arm_Left/Arm_Right (→arm_pod_upper/lower, 元形状の加工版)**,
  **Arm_Left/Right_Elbow_Grey (→elbow_shell, 同)**,
  Arm_Right_Claw_Grey (爪ハブと無関係の別形状 [開放骨組] — 不使用。爪ハブは
  Arm_Left_Claw_Grey のみ, 上記「腕の元パーツ」節で両腕分 ×2 印刷する),
  **Mouth_Cannon_Grey/Mouth_Neck_Blue/Mouth_Ball_Grey (→各 _Bored, 元形状の
  加工版。音声内蔵のため内部のみボーリング)**
  - 任意: Leg_HipJoint_Grey を縦割りして coxa ブラケット上面へ被せ接着
    すると股関節の見た目が原作の「グレーのコーン」に近づく (現物合わせ)
- Stand_mount: ベンチ吊りに使う場合は **chassis 後端のポッドブラケット
  (M3×4) に共締め**する (ポッドを外した状態で)。印刷は任意
- シェルは **壁2 / インフィル 4-6%** で軽量に (歩行成立の生命線)。
  2026-07-28 設計変更でカメラは頭部中央目へ移設されたため、ポッド前面の
  メインアイ (Cabin_Eye_White) はキット標準どおり無加工で印刷する

## 音声内蔵 (マウス砲クレードル, 2026-07 追加)

INMP441 (I2S マイク) + φ20mm 8Ω1W 薄型スピーカーを Mouth_Cannon 内部に完全
内蔵する。生成は `hardware/src/make_audio.py` (`build_all.py` に組込済み)、
検証は `tools/check_audio.py`。実測に基づく設計根拠は `hardware/src/config.py`
の `CANNON_Y_*` / `AUDIO_*` コメントを参照。

- **Mouth_Cannon_Bored**: 奥 (Neck 側, 側面グリップスリットより前方) に
  マイクポケット (φ17, INMP441 基板を保持)、砲口側 (フレア部) にスピーカー
  ポケット (φ20, 砲口面で開放 = 音の出口)。マイクポートは砲身下面 (スリット
  の無い安全面) へ φ1.8 で開口 — 目立たない位置という設計要求を満たす。
  マイクポケットにはポート反対側 (局所 +Z) に回転キー溝があり、
  audio_cradle_mic は正しい向き (キー同士が噛み合う向き) でしか挿入できない
- **組立順**: ① audio_cradle_mic の中央トレイへ INMP441 基板を差し込み瞬着で
  軽く固定 ② 配線 (マイク6芯) を Cannon 中心ボア方向へ引き出しつつ
  audio_cradle_mic のキー突起をマイクポケットのキー溝に合わせてマイクポケット
  へ圧入 (キーが噛み合わない向きでは物理的に入らない = 回転位置決め不要)
  ③ φ20 スピーカーを砲口側から挿入し配線 (2芯) を後方へ引き出す
  ④ audio_cradle_spk (抜け止めワッシャ) をその奥へ押し込んでスピーカーの
  脱落を防止 (現物合わせでホットボンド併用可)
  ⑤ 配線束を Mouth_Neck_Bored → Mouth_Ball_Bored (各 φ6 貫通ボア) 経由で
  Head_Bottom 内部へ通す
- **Head_Bottom 側の受け穴 (2026-07-30 焼き込み確定, 従来の「現物合わせ・
  担当範囲外」を置換)**: `hardware/stl/Head_Bottom_Armcut.stl` (腕ソケット
  拡口版, 上記「Head 下部シェルの腕開口」参照) に、マウスソケット
  (`MOUTH_SOCKET_LOCAL`) 奥へ φ7mm の配線ボアを追加焼き込み済み。実メッシュを
  ソケット軸に沿って0.25mm刻みでスキャンしたところ、ソケット奥の殻材は
  深さ13.25〜18.25mm (厚み約5mm, Ball半径R_BALL=12.557mmの直後) の薄い一層
  のみで、そこから先 (深さ20mm以降) は Head_Top_Eyecut のソリッドとも重なら
  ない共有の頭部内部キャビティであることを確認済み (`hardware/src/config.py`
  MOUTH_HEAD_BORE_* / `hardware/src/make_head.py` 参照, 同ファイル実行時に
  連通を検算)。**現物合わせは不要** — Mouth_Ball_Bored の配線 (計8芯) は
  Head_Bottom_Armcut のこの穴を素通りして頭部内部キャビティへ達する
- スピーカーポケット径 (φ20) はスピーカー外径ちょうどのため、外周を囲う
  別体スリーブは作れない (壁厚ゼロになる) — スピーカーはポケットへ直接
  圧入+接着し、audio_cradle_spk は奥の抜け止めのみを担う設計とした
  (`tools/check_audio.py` [3] 参照)

## 頭部中央目カメラ (固定, 2026-07-28 設計変更)

**2026-07-28 設計変更**: カメラはポッドのメインアイではなく、**頭部の
中央可動目を固定カメラ目に置換したもの**へ内蔵する (左右 2 目はキョロキョロ
のまま)。撤去理由は前方視界の自機体遮蔽 (§「新規設計パーツ」冒頭参照)。
ポッド前面のメインアイ (Cabin_Eye_White) は無加工の元パーツのまま。
生成は `hardware/src/make_camera.py` (`build_all.py` に組込済み)、検証は
`tools/check_camera.py`。実測に基づく設計根拠 (光軸の偏心角探索・FOV計算
含む) は `hardware/src/config.py` の `CAM2_*` コメントを参照。

- **eye_pod_camera (→ 印刷は shell + base の 2 分割, 2026-08-19)**: 元キット
  の目パーツ (Head_Eye_White) 形状のまま、キャップ軸から 38.0° 偏心した
  位置に定径 (φ10mm) の瞳ボア + モジュール収容キャビティを追加加工。
  中央目ソケットの法線は仰角 ~46.6° (ほぼ真上向き) だが、瞳を軸から偏心
  させ**正しい取付位相でグルーする**ことで光軸をほぼ水平前方 (残差 ~8.6°)
  へ向けている (完全な水平相殺 46.6° はカップの材が薄くモジュールが収まら
  ないため断念 — 探索の詳細は `config.py` CAM2_THETA_DEG コメント参照)。
  ネックボスは回転しない固定パーツのため φ28 (左右目の φ24 より太い) —
  Head_Top のボア (φ30) とのクリアランスは片側 1mm。
  **一体版は印刷失敗が多発した** (印刷姿勢でキャビティにえぐられたネック
  ボス断面 ~120mm² が唯一の接地で、その上にソリッドドームが載る不安定
  形状) ため、キャップ底面 (z=8) で `eye_pod_camera_shell` (ドーム部) と
  `eye_pod_camera_base` (ネックボス部) に分割した。両者ともベタ置き印刷
  (接地 525/570mm²)。位置決めはリングプラグ (φ27/φ23 呼び, 片側 0.2mm
  クリア) + 溝。base 側ポケットは **carrier 実形状+片側 0.6mm の最小掘り
  込み** (同日 v2 — 初版は一体版の汎用キャビティ負形状 [軸方向 20mm 張り
  出し込み] を流用しており +Y 側リム壁が根元 ~1.4mm の薄足になる脆さが
  あった。v2 で断面積 z=2: 117→531mm² / z=4: 107→451mm² に充填) を底肉
  1.5mm を残す止まり穴として掘る。FPC は**前側リム壁を貫通する開放縦溝**
  で底面へ抜く (v3 — v2 の閉じた縦穴は前壁を厚さ ~1.3mm×幅 10mm の孤立
  した刃として残し実印刷でちぎれた。前壁は接着周長のためだけの材で、
  ソケット装着後は Head_Top のボア壁 φ30 が溝の外側を閉じる)
  (`config.py` CAM2_SPLIT_*/CAM2_BASE_POCKET_FLOOR_T/CAM2_BASE_POCKET_CLR/
  CAM2_FPC_SLOT, 検証 `check_camera.py` [5])。一体版 STL は外殻無傷検証と
  可視化/URDF の参照形状として引き続き生成される
- **camera_carrier**: モジュール実寸 (20.5x12.5x5.54mm, Union
  Image/Seeed純正データシート実測, BOM #34) を保持する隠しパーツ
  (`tools/check_camera.py` [3] で収容/干渉を検算)。2026-08-19 短縮:
  分割接着では接着前に base のポケットへ先入れするため、後方への貫通尾と
  頭部シェル接着ウィングは廃止 (base ポケット内に完全内蔵され物理的に
  届かない。固定は shell/base 接着による閉じ込め)
- **組立順 (2026-08-19 分割版)**: ① camera_carrier のポケットへカメラ
  モジュール (レンズ側の子基板) を差し込み瞬着で軽く固定 ② FPC/配線を
  camera_carrier 側方の切り欠きから引き出す ③ carrier+モジュールを
  eye_pod_camera_base のポケットへ落とし込み、FPC を base 底面のスロット
  から引き出す ④ eye_pod_camera_shell を上から被せてリングプラグを溝へ
  嵌合し接着 (斜めチャネルが carrier を貫くため回転位相は機械的に一意)
  ⑤ 組み上がったポッドを Head_Top の中央目ソケットへ、瞳が水平前方を向く
  取付位相 (assembly.md §2.9) でグルーする
- **モジュール本体基板 (ESP32S3 チップ/WiFi アンテナ/USB-C) の設置場所は
  本設計の担当範囲外**: camera_carrier が保持するのはレンズ側の子基板のみ
  (FPC接続)。本体基板は頭部内部の空いたスペースへ現物合わせで固定する
  こと (音声ユニットの Head_Bottom 側受け穴が「別タスク」扱いなのと同様の
  切り分け)
- 配線 (電源 AWG30 2芯) は頭部内からシャーシ 7 タブ間隙間を通って胴へ下ろす
  (頭部は完全固定・可動部なしのため回転部を避ける配慮は不要。
  `docs/wiring.md` 「カメラ電源配線」参照)。データ線は無し (独立 WiFi
  モジュールのため PCA9685/I2C バスとは無関係)

## 足 (Leg_Foot 化, 2026-07-28)

非キットの TPU カスタム足先 `foot_tip` (球ドーム+差込プラグ) を廃止し、
キット部品 `Leg_Foot_Grey_x4_Repaired` (甲) + `Leg_Toe_Black_x12` (トゥ
×3/脚) を使う構成へ変更した (README 鉄則1: 見えるジオメトリは元キット
形状)。生成は `hardware/src/make_leg.py` (`leg_foot_bored`/`foot_pad`,
`build_all.py` に組込済み)、検証は `tools/check_leg_assembly.py`。

- **leg_foot_bored**: 元 Leg_Foot の外観をそのまま 150% 化し、(a) 甲背側
  (tibia 差込面) へ旧 foot_tip と同径の差込プラグを追加、(b) 甲コラム底面
  (3トゥ取付スタブの重心付近, 実測 `config.FOOT_PAD_XY`) へ隠しポケットを
  追加加工した版。**tibia 側の差込ソケットは無変更**
  (`hardware/src/make_leg.py` tibia_link() の `FOOT_SOCKET_D/H` 参照) なので
  旧 foot_tip と互換に差し替えられる。組付けは差し込み+接着剤 (瞬間接着剤
  またはエポキシ) で保持する — 抜け止めスナップ機構は無い (下記2点参照)
  - **2026-07-28 レビュー修正 (critical)**: 当初版はプラグの上に抜け止め
    リップ (D=11.2mm) を追加していたが、tibia_link() 側のソケットボアが
    段付きのない定径穴 (D=10.4mm) のため実体が 21.68mm³ 食い込み、PLA/PETG
    の剛体同士では物理的に挿入不能だった (旧 TPU foot_tip は弾性変形で
    スナップできていたが、新設計はその前提が成立しない)。リップは廃止し、
    接着剤保持のみに単純化した — 組立時は必ず接着剤を使うこと
  - **2026-07-28 レビュー修正 (major)**: 輸入キットメッシュへのブーリアン
    加工後に `simplify(0.01)` を呼んでいなかったため、生成した
    `leg_foot_bored.stl` を (書き出し後に) 再ロードすると非 watertight
    (穴あき) だった。`simplify(0.01)` を追加し、`lib.export()` 自体も
    書き出し後の実ファイルを再ロードして watertight 判定するよう修正した
  - **2026-07-28 の誤短縮とその復元 (2026-07-29)**: `Leg_AnkleJoint` (旧
    キットの球関節) を使わないため、本来その内部に隠れるはずの甲の取付
    タブ (3本のトゥ取付スタブ) が露出する。2026-07-28 時点は「3本のうち
    幅の広い2本 (約+40°/+140°) はトゥの接着代、細い1本 (約-90°) はどの
    トゥにも使われない」という粗い最近接点チェックに基づき、この1本を
    `_trim_unused_ankle_tab()` で短縮していた。しかし
    `tools/data/kit_assembly_front.json` の Leg_Toe_Black_x12 エントリが
    その後 (同日中に) trimesh.section による3本個別の精密実測を行った
    結果、**この-90°スタブも実際にはトゥ (id `_0`) の取付スタブとして
    使われている**ことが判明 (3本とも根元φ5→先端φ2.4のテーパー円錐で、
    どれも独立したトゥ取付スタブ)。この訂正が make_leg.py 側へ反映されて
    いなかったため、-90°スタブの大半 (半径9-13mm、切除セクタと重なる
    範囲) が誤って削られたまま残っていた — README 鉄則3 (元パーツを
    勝手に削らない) 違反。2026-07-29 に `_trim_unused_ankle_tab()` を
    廃止しキット原型の3スタブを完全復元した。3本とも見た目上の切除痕は
    無くなり、実機写真の丸い灰色カラー相当の外観に近づいた
- **foot_pad**: leg_foot_bored の隠しポケットへ圧入接着する TPU 接地パッド。
  甲コラム中心付近から突き出し、スタンス時に実際の耐荷重接地を担う設計
  (パッド先端は甲全体の最下点=トゥスタブ先端より
  `config.FOOT_PAD_PROTRUDE`(1.5mm) だけ下へ突き出す)。3本のトゥ (下記)
  はスタブから外側へ長く伸びるため、パッドよりさらに深く (ローカル z で
  6.5-7.1mm) 突き出す — 装飾側のトゥが先に着地点に見える姿勢もあるが、
  接地力は主に foot_pad が受ける想定は変えていない (下記「接地の連鎖」参照)
- **トゥの実測・配置 (kit forensics, 2026-07-28 発見・2026-07-29 精密化)**:
  `Leg_Toe_Black_x12` の 3MF source_offset は Leg_Foot ではなく
  Leg_AnkleJoint (旧キットの球関節) に最も近い (表面間距離 3.9mm) — 一方
  Leg_Foot 単体では底面近くに trimesh.section の水平スライス走査で3本の
  小さな一体成形スタブ (根元φ5mm→先端φ2.4mmのテーパー円錐、raw
  z=-1.3〜-3.29、XY 角度は概ね-90°/+40°/+140°の非等間隔トライポッド) が
  実在することを直接確認した。これは元キットが「小さな位置決めスタブ+
  接着」方式だったことを示唆し、トゥの実際の接着位置としてはこの
  Leg_Foot 自身のスタブ実測値を採用する方が (offset-diff 経由で 65.9mm も
  離れる Leg_Foot 単体の 3MF 対応より) 直接的で信頼できる。2026-07-29 に
  3スタブ復元後の実ビルド STL からスタブ軸・先端を 0.1mm 精度で再実測し、
  トゥの取付方向 (根元→爪先軸をスタブ軸に整合) とロール (爪の腹側=湾曲の
  凹み側を可能な限り接地方向=world -Z へ向ける — 完成写真で爪がアンクル
  カフから垂れ下がり先端が地面側に向く様子と整合) を確定。トゥ根元には
  深いソケット穴は無い (y軸断面走査で根元〜y=6.0まで内部空洞なしを確認)
  ため平面的な接着継手とみなし、根元の真の縁をスタブ base 点へ 0mm
  standoff (押し出しなし) で直接登録 — トゥ-トゥ相互重なり 0.0cm³、
  トゥ-足本体重なり 4.8-4.9% (5%未満は接着代として許容, 既存基準と同じ)。
  詳細な座標・導出過程は `tools/data/kit_assembly_front.json` の
  Leg_Toe_Black_x12 エントリの `method`/`instances_note`/`finding` を参照
  (このドキュメントの数値ではなく、そちらと `tools/check_leg_assembly.py`
  の実行結果が正)
- **接地の連鎖 (2026-07-29)**: leg_foot_bored+foot_pad は tibia の物理
  取付点 (`TIBIA_LEN`=135mm, 無変更) よりさらに下に実体があり、
  firmware/sim の IK がそのまま `TIBIA_LEN`=135mm を「足先接地点」として
  扱うと、実運用スタンス全域 (体高105-130, SWAY 込み) で foot_pad 底が
  world z を最大 3.5mm 下回る (めり込む) ことが判明した。firmware
  ik.h / `tools/sim_gait.py` の IK 計算だけが使う「実効 tibia 長」
  `TIBIA_LEN_GAIT` (= 135 + `FOOT_GROUND_OFFSET`=18.6 = 153.6mm,
  `hardware/src/config.py` 参照) へ校正し、スタンス全域で foot_pad 底が
  world z を下回らない (worst +0.05mm) ことを `tools/check_leg_assembly.py`
  が実ビルド STL + 実際の歩容ロジックで毎回再検証する。物理ジオメトリ
  (make_leg.py の `TIBIA_LEN`=135mm, tibia 差込ソケット) は無変更 — この
  校正は firmware/sim の IK 内部だけの数値 (見た目・組付けに影響しない)
- **組立**: leg_foot_bored を tibia_link 先端ソケットへ差し込み接着 → 隠し
  ポケットへ foot_pad を圧入接着 → Leg_Toe_Black_x12 (元キット STL 150%,
  無加工) ×3 を甲底面の一体成形スタブ (3箇所) へ位置合わせして瞬間接着
  (`docs/assembly.md` 参照)

## 重量バジェット (2026-07 設計値, filament_calc.py による見積り)

| 項目 | 目安 |
|---|---|
| 意匠シェル PLA 全色 (壁2/インフィル8% 計算値, 2026-07-31 shin_shell リリーフカット再評価タスク後の再計算 -- KNEE_RELIEF/TIP_RELIEF/ADJ_RELIEF_BANDS 撤去でキット形状復元、shin_shell +17g) | ~1,173g |
| 脚骨格+シャーシ PETG (chassis/coxa/femur/tibia) | ~460g |
| 腕骨格 PETG (左右, claw_mount 込み。旧 palm_base/grip_slider/grip_finger 廃止で 115g→軽量化) | ~55g |
| サーボ 12×DS3218 (60g) + SG90×1 | ~730g |
| 腕サーボ 6×MG90S (14g)。旧グリップ用サブマイクロ×2 は 2026-07-29 固定爪化で廃止 | ~85g |
| 電装 (PCA9685×2) + 2S 2200mAh + 配線 + ネジ | ~350g |
| **合計 (設計想定)** | **~3.0kg** |

- 歩容・トルク検証は総重量 **3.0kg** 前提で全合格 (v3: 股ピッチ 8.9kg·cm vs
  定格 20 = 45%、支持多角形マージン +8.8mm)。トルクの絶対余裕は大きいが、
  シェルのインフィルを上げない・塗装は薄く。
- v3 追加分の内訳: **PETG +37g** (pod_neck 20g + battery_cradle 17g,
  filament_calc 実測。2026-07-31 QA 再検証で pod_neck 22g、battery_cradle
  13g という旧記載を訂正 -- pod_neck は同日の頭部逃がしカット+増厚/
  テーパー追加後の最終値、battery_cradle は filament_calc の実測値
  [`docs/print_manifest.md` の 17g と一致] に合わせた) は上表
  「脚骨格+シャーシ」に未計上。TailJoint 一式は
  **PLA 青 ~16g** で意匠シェル側に計上済み。いずれも合計 ~3.0kg の
  想定誤差内 (検証は 3.0kg 前提)。
- **足 (Leg_Foot 化, 2026-07-28) の質量影響**: 旧 foot_tip (TPU) を
  leg_foot_bored (PLA灰, x4 実測 12g) + foot_pad (TPU, x4 実測 2g) へ
  差し替え。filament_calc.py 実測で合計 14g (x4) — 3.0kg 想定に対し
  <0.5% でトルク/歩容の前提 (3.0kg) に影響しないため再計算不要。上表・
  歩容検証の数値は据え置き。
- 軽量化したい場合: シェルをインフィル 4-6% へ (−100g 前後) → Cabin_Back の
  内側リブ除去 → ボディのみ 140% への縮小、の順で削る。
