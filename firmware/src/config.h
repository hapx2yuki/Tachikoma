#pragma once
// タチコマ歩行ファームウェア 設定
// 幾何値は hardware/src/config.py と一致させること

// ---------------- 幾何 (mm)
constexpr float COXA_LEN  = 26.0f;   // ヨー軸→股ピッチ軸 (水平)
constexpr float FEMUR_LEN = 70.0f;   // 股ピッチ軸→膝軸 (STD サーボ構成)
// TIBIA_LEN: ik.h の IK/FK 計算だけが使う「実効 (ground-equivalent) tibia
// 長」。物理リンク長 (hardware/src/make_leg.py の tibia_link()/
// leg_foot_bored() が使う実寸) は 135.0mm で無変更 — 足の実体
// (leg_foot_bored+foot_pad) は膝軸から見た tibia の物理原点 (=旧
// TIBIA_LEN=135 の到達点) よりさらに foot_pad 底まで実体があり、しかも
// tibia 軸は SWAY 込みの実スタンス姿勢によって鉛直から最大 20° 以上傾く
// ため、単純な一次元押し出し (foot_pad 底の深さ 11.358mm をそのまま加算)
// では体高105mm・SWAY 込みの最悪位相で world z 残差が最大 3.5mm 残ることが
// 判明した。tools/sim_gait.py の実際の foot_target()/leg_ik() (SWAY 込み)
// を使い、実運用スタンス全域 (体高105-130mm, 全位相) を物理配置チェーンで
// 数値評価し、最悪ケースがちょうど world z=0 に接する (めり込まない)
// FOOT_GROUND_OFFSET=18.6mm で校正した (2026-07-29 実測+校正, hardware/
// src/config.py FOOT_GROUND_OFFSET 参照)。135+18.6=153.6 (config.py
// TIBIA_LEN_GAIT と一致させること。tools/sim_gait.py が regex 突合で
// 検査する)。ik.h はこの定数を物理形状目的では使っていないため、値を
// 実効値にしても他の意味は壊れない。
constexpr float TIBIA_LEN = 155.98f;  // 膝軸→足先接地点 (実効値, IK専用)。2026-09-04 153.6→155.98
                                      // (重心対応スタンスで最悪位相の脛傾きが増えた分の再校正,
                                      // config.py FOOT_GROUND_OFFSET 20.98)
constexpr float HIP_R = 50.9f;       // ヨー軸の配置半径 (円形ハブ v3, 放射配置)

// 脚インデックスと取付方位 (脚が伸びる向き, ボディ座標 +X右 +Y前)
// v3 (2026-07-28): 公式フィギュア下面実測 — 前脚±75°/後脚±120° (正面基準)。
// 真後ろ 270° はポッド接続ネックのステーション (config.py と一致必須)
enum LegId { FR = 0, FL = 1, RL = 2, RR = 3 };
constexpr float LEG_MOUNT_DEG[4] = {15.0f, 165.0f, 210.0f, 330.0f};
// 中立スタンスの足先方位 (取付方位と別)。公式フィギュアのポーズ準拠で
// 前脚は前寄り (中立ヨー+18°)・後脚はやや外拡げ (-2°)。後脚を内へ寄せる
// と FR-RL 対角線が重心に接近し前脚遊脚の安定が崩壊する (sim_gait [4])
constexpr float STANCE_DEG[4] = {33.0f, 147.0f, 208.0f, 332.0f};
constexpr float LEG_ORIGIN[4][2] = {  // ヨー軸のボディ座標 {x, y} = HIP_R∠mount
    {+49.166f, +13.173f}, {-49.166f, +13.173f},
    {-44.081f, -25.450f}, {+44.081f, -25.450f}};

// ---------------- サーボ
// PCA9685 ×2: board0=0x40 (脚+頭 ch0-12), board1=0x41 (腕 ch16-23 + 目 ch24-26)
// グローバル ch = board*16 + ローカル ch
constexpr int SERVO_FREQ = 50;            // Hz
constexpr uint8_t PCA_ADDR[2] = {0x40, 0x41};
constexpr int N_CH = 32;
constexpr int PCA_CH[4][3] = {            // [leg][yaw, pitch, knee]
    {0, 1, 2}, {3, 4, 5}, {6, 7, 8}, {9, 10, 11}};
constexpr int CH_HEAD = 12;
// 腕: [arm][yaw, pitch, elbow]  arm0=右, arm1=左。ch 19/23 (旧グリップ)
// は未使用 (2026-07-29 固定爪化でグリッパ機構ごと廃止。配線/PCA9685
// 割当は変えず ch 番号のみ予約のまま残す)
constexpr int ARM_CH[2][3] = {{16, 17, 18}, {20, 21, 22}};
// 目 (頭部, SUBMICRO)。元キットの目パーツ (白ドーム) を自軸回転させる。
// 黒ドット群 (視線マーク) が軸から ~45° 偏心しており、回転で視線が泳ぐ。
// 2026-07-28 設計変更: 中央目 (ch25) は固定カメラ目 (hardware/src/
// make_camera.py) に置換済みでサーボを持たない — ch 番号はそのまま予約し
// (配線/PCA9685 割当を変えない)、eyes.h の Eyes::update() が未使用として
// スキップする。可動 (キョロキョロ) するのは右目 (ch24)・左目 (ch26) のみ
constexpr int EYE_CH[3] = {24, 25, 26};   // 右目, 中央(未使用/固定カメラ), 左目
constexpr float EYE_LIM = 80.0f;          // サーボ回転の使用範囲 ±
constexpr float EYE_SLEW_DPS = 500.0f;    // サッカードの速さ
// 左腕はヨーのみソフトでミラー (ピッチ/肘はパーツが左右ミラー印刷なので
// 符号そのまま。arms.h の出力部を参照。2026-07-29 固定爪化でグリップ軸は
// 廃止済み)
constexpr int ARM_SIGN[2] = {+1, -1};
constexpr int US_MIN = 500, US_MAX = 2500; // サーボパルス範囲
constexpr float DEG_RANGE = 180.0f;        // US_MIN..US_MAX に対応する角度
// 関節ソフトリミット (deg, 機構設計値。assembly.md 参照)
constexpr float LIM_YAW = 40.0f;   // v3: 前脚が腕マウントから 35° 離れたため
                                   // ±35→±40 に緩和 (中立ヨー +18° + 歩容 ~22°。
                                   // 2026-07-28 腕マウント移設後の角度差は 35°
                                   // (本ファイル下部 ARM_YAW_LIM 注記と同一根拠)。
                                   // check_leg_assembly / check_arm[1b] で実メッシュ検証)
// v3: 45° ペア (FR↔RR, FL↔RL) が互いへヨーすると脚が交差し得るため、
// 「ペア相手方向 (内側)」のヨーを 2 段で制限する (check_leg_assembly で
// 実メッシュ検証。FR/RL はミラー脚 _m 前提):
//  1) 単側 ≤ LIM_YAW_IN (単側 18° の深タック姿勢から交差が始まる実測)
//  2) ペア同時内側の和 ≤ LIM_YAW_IN_SUM (対称 (17.5,17.5)=和35 は交差)
// 歩容の使用量は単側 ~16.6° / 和 ~8.6° (sim_gait [2b])
constexpr float LIM_YAW_IN = 17.5f;
constexpr float LIM_YAW_IN_SUM = 26.0f;
// 内側ヨーの符号: FR は RR へ CW(-), FL は RL へ CCW(+), RL は FL へ CW(-),
// RR は FR へ CCW(+)
constexpr int YAW_IN_SIGN[4] = {-1, +1, -1, +1};
// v3: ポッド (Cabin) が後方の脚と同じ高さに接続されるため、後脚の
// ポッド側 (後方中央向き) ヨーを別途制限する。歩容の使用量は ~15.4°、
// check_leg_assembly でこの限界値 × IK 到達姿勢の実メッシュ干渉 0 を検証
constexpr float LIM_YAW_POD = 30.0f;  // 2026-09-04: 22→30。実メッシュ掃引 (IK 到達極値姿勢) で
                                       // ポッド/バッテリー実体との接触は 34° から (32° まで 0.000cm³)。
                                       // 重心オフセット吸収の後方スタンスに必要 (config.py CG_XY 参照)
constexpr int YAW_POD_SIGN[4] = {0, 0, +1, -1};  // RL:+ / RR:- がポッド向き
constexpr float LIM_PITCH_UP = -45.0f, LIM_PITCH_DN = 55.0f;
constexpr float LIM_KNEE = 44.0f;  // 機構限界 45° の手前で止める
// 回転方向反転フラグ [leg][joint]
// v3: FR/RL はミラー脚 (_m パーツ, 45°ペア干渉対策) でピッチ/膝サーボが
// 反対側に付くため初期値 -1。ヨーサーボはシャーシ側 (全脚共通) で +1。
// 組立後、逆に動く関節があれば該当エントリのみ実機で反転する。
constexpr int JOINT_SIGN[4][3] = {
    {+1, -1, -1}, {+1, +1, +1}, {+1, -1, -1}, {+1, +1, +1}};

// ---------------- 歩容デフォルト
constexpr float BODY_H_DEF = 115.0f;  // 股ピッチ軸から接地面までの高さ
constexpr float BODY_H_MIN = 110.0f, BODY_H_MAX = 130.0f;
// MIN の根拠: 2026-09-04 に 105→110。中立足先を重心側へ 30mm 寄せた (STANCE_OFF_Y)
// 結果、体高 105 では前脚の遊脚持ち上げ時に股ピッチが LIM_PITCH_UP (-45°) を
// 0.3° 超えて IK 失敗する姿勢が出るため (sim_gait.py [2] 全域スイープで 31/41600)。
// 110 以上では失敗 0。広げる場合は sim_gait の BODY_H_RANGE を先に更新すること
constexpr float STANCE_R = 129.0f;    // 中立時のヨー軸→足先 水平距離。ハブ v2 で
                                      // 股が内寄せされた分を拡大し足先位置=旧設計
                                      // 相当を維持 (旧 r92.2+88 ≈ 新 r50.9+129)
// 中立足先パターン全体のボディ座標オフセット (config.py STANCE_OFF_XY と一致)。
// 全機体重心が Cabin のため y=-39mm (config.py CG_XY) にあり、支持多角形の中心を
// 重心へ寄せるために足先を後方へ 30mm ずらす (2026-09-04 S-01 対応。sim_gait.py
// [4] 重心基準マージン: 旧 -23.7mm → +10.9mm)
constexpr float STANCE_OFF_X = 0.0f, STANCE_OFF_Y = -30.0f;
constexpr float STEP_H = 18.0f;       // 遊脚の持ち上げ高さ
constexpr float CYCLE_T = 1.6f;       // 歩容 1 周期 (s)
constexpr float MAX_STEP = 30.0f;     // 最大歩幅 (片振幅)
constexpr float MAX_TURN_DEG = 12.0f; // 1 周期あたりの最大旋回角
// クロール位相オフセット (FR, FL, RL, RR)
// 遊脚順 = RL → FL → FR → RR (回転順)。隣接脚が連続するため重心シフトの
// ベクトルが連続的に回転し、対角遷移でのシフト相殺が起きない
constexpr float PHASE_OFF[4] = {0.25f, 0.50f, 0.75f, 0.0f};
constexpr float DUTY = 0.75f;         // 接地時間率
// 重心シフト: 遊脚と反対側へボディを寄せ、支持三角形マージンを確保する。
// シフト窓は遊脚区間より SWAY_LEAD (位相) だけ前後に広げ、離地の瞬間に
// 既にシフトが乗っている状態を作る (静的クロールの定石)
// 脚ごとの振幅 {FR, FL, RL, RR}: 後脚遊脚時は重心が後寄りのため支持三角形の
// 後辺に近く、前脚より大きなシフトが要る (2026-09-04: 後脚 34→40, sim_gait [4])
constexpr float SWAY_MM[4] = {34.0f, 34.0f, 40.0f, 40.0f};
constexpr float SWAY_LEAD = 0.11f; // v3: 離地瞬間のシフト率を上げる (0.08→0.11 で
                                   // 窓の sin 立上りが 58%→68%。sim_gait スキャン)
// ワークスペース射影 (gait.h): 膝リミット 44° に対応する股ピッチ軸→足先の
// 最大距離。v3 では遊脚の反対側 66° 隣の脚が sway で外側へ押されるため、
// 足先目標をこの円内へ平面クランプして膝リミット超過を防ぐ
constexpr float D_KNEE_MAX = 210.2f;  // sqrt(F²+T²+2FT·cos46°) - 0.5 (2026-09-04 T=155.98 で再計算)
                                      // (T=TIBIA_LEN=153.6, 2026-07-29 接地
                                      // オフセット校正で 189.9→207.9)
// 同 折り畳み側 (膝 +44°) の最小距離。近すぎる足先目標を外側へ押し出す
// (対称性のため追加 — 歩容の最小使用 rr は ~80 で通常は発火しない)
constexpr float D_KNEE_MIN = 119.1f;  // sqrt(F²+T²+2FT·cos134°) + 0.5 (2026-09-04 T=155.98 で再計算)
                                      // (2026-07-29: 100.5→116.9)

// ---------------- 腕の可動域・動作 (deg)
// 腕は Head_Bottom 側面の実ソケット (2026-07-28 実機準拠へ移設) から吊り下がる。
// ソケットは正面から ±40° (Head_Bottom_Blue.stl 実測, hardware/src/config.py
// ARM_MOUNT_YAW_DEG 参照) — 肩ポッドの中立 (ヨー0) 向きは前向きでなく放射
// 外向き。旧マウント (16,74)=正面±12° の「前向き固定」前提はここで解消。
// 肩ピッチ軸の高さは従来どおり plate 下 18.4mm 相当 (ARM_SHOULDER_OVER_HIP_MM)。
// 手先は前脚と同じ高さ帯を動くため、ヨーは中立向きから常時 ±ARM_YAW_LIM に
// 制限する。前脚 (正面75°) との角度差 (75-40=35°) が新たな最接近 — 実メッシュ
// 掃引 (tools/check_arm.py [1b][4]) でクリアランスを検証済み (assembly.md
// 可動域表と一致)。
// ヨーの符号: + = 中立向きからさらに外側へ開く (右腕基準。左腕は ARM_SIGN で
// ミラー)。手先の実位置 (chassis xy) は ARM_MOUNT_YAW_DEG+yaw の方位で決まる
// (arms.h の相互接触クランプ参照 — 「y 無視・前向き固定」の旧前提を撤去)
constexpr float ARM_MOUNT_YAW_DEG = 40.0f; // 肩ポッド中立向き = 正面からの方位
                                           // (Head_Bottom ソケット実測, config.py 一致)
constexpr float ARM_YAW_LIM = 15.0f;      // 肩ヨー ± (中立向きからの追加, 常時)。
                                          // 新配置での実メッシュ掃引クリアランスは
                                          // tools/check_arm.py [1b] 参照 (要再検証)
constexpr float ARM_PITCH_MIN = -45.0f;   // 上げ限界 (前方斜め上まで)
constexpr float ARM_PITCH_MAX = 85.0f;    // 下げ限界 (垂れ下げ)
constexpr float ARM_ELBOW_MIN = 0.0f;     // 伸ばし
constexpr float ARM_ELBOW_MAX = 95.0f;    // 曲げ
constexpr float ARM_SLEW_DPS = 150.0f;    // 腕のスルーレート (deg/s)
// 脚の出力スルーレート (deg/s)。歩容の関節速度は最大 ~100 dps (遊脚 0.4s で膝 ~30°)
// なので通常歩行では効かず、(a) 通電/再有効化直後の「無信号→立位」の直撃と
// (b) dt が伸びた周期での位相飛びだけを抑える (2026-09-04 レビュー F-01/F-03)
constexpr float LEG_SLEW_DPS = 240.0f;
// 幾何 (hardware/src/config.py と一致): 上腕 55, 前腕+手 = 肘→指先 47.70
// (2026-07-29 固定爪化: 可動グリッパを廃止しキット原型の爪 (Arm_Claw_Grey
// 爪ハブ+Finger×3+FingerTip×3) を直結。前腕16 (手首面まで) + claw_mount
// (平坦円盤, 厚み2.5) + 爪ハブ/指の 3MF source_offset フォレンジクス実測
// (config.py ARM_HAND_REACH_MM 参照, worst-case 指先 31.689mm 実測 +0.01mm
// マージン=31.70) = 16+31.70≈47.70。旧可動グリッパ版 (79mm = 前腕24+
// フランジ5+パーム36+指先突出14) から 31.30mm 短縮 (-40%) — ユーザー指摘
// 「手が長い」の定量的な解消。旧 47.55 (worst-case 31.55mm) は QA で判明した
// 丸め誤りで 0.14mm 過小評価だった。check_arm.py [4] がこの値と config.py
// 実寸の一致を検査、[3b] が実メッシュとの一致を直接検査する
constexpr float ARM_UPPER_MM = 55.0f;
constexpr float ARM_REACH_MM = 47.70f;
// 肩ピッチ軸の高さ = body_h + 9.2mm (プレート下面 -18.4, 股ピッチ面 +27.6)
constexpr float ARM_SHOULDER_OVER_HIP_MM = 9.2f;
constexpr float ARM_GROUND_MARGIN_MM = 8.0f;  // 地面ガードの余裕
// 腕どうしの接触回避 (ミラー動作で両腕が内側へ振れたとき):
// 手先中心の横変位 planar·sin(ARM_MOUNT_YAW_DEG+yaw) を MOUNT_X - HAND_HALF
// までに制限し、両手が機体中心線 (x=0) 付近ですれ違わないようにする。
// 放射マウント (中立ヨーが±40°外向き) では中立姿勢の時点で手先が大きく
// 外側にあるため、旧マウント (前向き中立) よりこのクランプは発火しにくい
// はずだが、実姿勢グリッドで最小値を確認すること (check_arm.py [5])
constexpr float ARM_MOUNT_X_MM = 31.7f;   // 肩ヨー軸の x オフセット
                                          // (Head_Bottom ソケット実測, config.py 一致)
constexpr float ARM_MOUNT_Y_MM = 48.77f;  // 同 y オフセット (幾何ドキュメント用。
                                          // hand 位置式には ARM_MOUNT_YAW_DEG 経由で
                                          // 反映済みのため、この値自体は式に未使用)
                                          // 2026-07-31 境界スイープタスク: hub_y
                                          // 0.0→11.0 (実現可能な最中央値, config.py
                                          // ARM_MOUNT_HUB_Y コメント参照) に伴い
                                          // 37.8f→48.77f へ更新 (= config.py
                                          // ARM_MOUNT_XY[1] 実測値)
// 2026-07-29 固定爪化: 旧「パーム半径 13.5+余裕 1mm」は palm_base 廃止に
// 伴い無効化。現行の爪ハブ+指 3 本+指先チップ一体の実測 (claw_mount ローカル
// Y 軸方向, 手先の実横幅に対応する成分のみ — Z 方向の突き出しはこの軸には
// 寄与しない) は max|Y|=14.30mm (tools/check_arm.py [5b] が実メッシュで
// 継続検証)。14.5mm は依然としてこれを上回る安全側の値なので変更していない
// が、根拠は完全に入れ替わっている (「たまたま」ではなく [5b] で保証)
constexpr float ARM_HAND_HALF_MM = 14.5f;
// プリセット [yaw, pitch, elbow] (2026-07-29 固定爪化でグリップ軸は廃止済み)
constexpr float ARM_POSE_TUCK[3] = {0, 55, 95};    // 収納 (腹の下へ畳む)
constexpr float ARM_POSE_READY[3] = {10, 30, 40};  // 構え (前方下がり)
constexpr float ARM_POSE_REACH[3] = {0, 10, 10};   // 前方へ伸ばす
constexpr float ARM_SWING_DEG = 8.0f;     // 歩行時の腕スイング振幅

// ---------------- 脚(前脚)×腕 連成クランプ (2026-07-31 追加,
// 2026-07-31 「境界スイープ (実現可能な最中央値確定)」タスクで
// ARM_MOUNT_HUB_Y 0.0→11.0 (config.py 参照) に伴いゲート値を再々導出・更新)
// shin_shell (意匠シェル) は骨格 (femur/tibia) 検証には現れないが、前脚が
// 遊脚で体前方・高く振れる (脚 yaw が中立から大きく+側) と、同側の腕が
// READY 等の前方姿勢だと shin_shell と実体干渉することが
// check_shin_arm_leg.py [C] で判明。脚の IK を制限すると歩行そのものが
// 阻害される (股関節可動域を歩容が使い切っているため) ので、脚側は無制限の
// まま、危険域では同側の腕ヨーだけを強制的に最大内寄せ (-ARM_YAW_LIM) へ
// 退避させる設計は維持する。
//
// 【2026-07-31 境界スイープタスクでの再々導出】hub_y=0 (完全中央) は
// check_screw_bosses.py/check_arm.py の静的検査 4 件が NG で物理的に不可能と
// 判明 (config.py ARM_MOUNT_HUB_Y コメント参照)。実現可能な最中央値として
// hub_y=11.0 (旧12.0から1mmだけ中央寄せ) を採用したところ、肩ヨー軸が
// y≈37.8mm(hub_y=0時) → y≈48.77mm(hub_y=11.0) へ後退量が大幅に緩和され
// (旧12mm後退 → 実質1mm後退相当)、干渉onsetが hub_y=0実測の11.0°
// (旧コメントの「10-12.5°」と整合) から大きく改善した。scratchpad
// onset_scan.py (translate-trick で腕側配置のみ hub_y 分平行移動する高速化,
// leg reachable集合83点×腕yaw/pitch/elbow全域126点の全域探索) による実測:
//   - FR の中立 (静止立位) leg-local yaw は sim_gait.leg_ik() 実測で
//     約 +9.12° (hub_y に非依存の脚側パラメータのみで決まるため不変)。
//   - クランプ非発火 (腕 yaw/pitch/elbow 全域, 脚 pitch/knee 到達可能集合
//     全域) での shin_shell 干渉 (hub_y=11.0): FR yaw<=17.5° で常に
//     0.0000cm^3、yaw=20.0°で0.0023cm^3 (実質ゼロ)、yaw=25.0°で
//     1.3305cm^3 — onset は 20.0°直後 (旧 hub_y=12.0 の実測 20.0°とほぼ
//     同値、hub_y=11.0がほぼ旧配置に近い後退量であることと整合)。
//     ARM_LEG_YAW_GATE_DEG=20.0° (onset直下, 旧hub_y=12.0時代と同じ値) を
//     採用。中立姿勢 (9.12°) とのマージンは10.88°で要求3°を大きく上回る。
//   - 腕側の退避は従来どおり yaw のみで足りる (pitch/elbow は無関係)。
//     check_shin_arm_leg.py の実チェッカーで [C-duty]/[C]/[B] を再検証し
//     全て PASS・ceiling(0.25cm^3) 内に収まることを確認済み (docs/
//     assembly.md 参照)。
//   - FL(左脚)+左腕は FR+右腕の左右鏡像 (shin_shell/腕とも X ミラー構成) で、
//     数値が厳密に一致する (危険方向の符号だけが反転, ARM_LEG_YAW_SIGN 参照)。
//   - 発火頻度: ARM_LEG_YAW_GATE_DEG が10→20になったことで、通常歩行中の
//     クランプ発火時間比率は hub_y=0時代の約70-74%から大幅に低下した
//     (check_shin_arm_leg.py [C-duty] の実測値は docs/assembly.md 参照。
//     旧hub_y=12.0時代の42-44%相当に近い水準まで改善見込み — hub_y=11.0が
//     ほぼ同じ肩ヨー軸後退量になったことの直接的な帰結)。
constexpr float ARM_LEG_YAW_GATE_DEG = 20.0f;  // 同側前脚ヨーがこれを超えたら危険域
// 腕 index [0]=右(FR相当) [1]=左(FL相当)。危険側の脚ヨー符号
// (脚が体前方・頭部側へ振れる方向) — FR は +、FL は - (鏡像)
constexpr int ARM_LEG_YAW_SIGN[2] = {+1, -1};

// ---------------- ピン
constexpr int PIN_SDA = 21, PIN_SCL = 22;
constexpr int PIN_LED = 4;
constexpr int PIN_DF_RX = 16, PIN_DF_TX = 17;  // ESP32 RX2/TX2
constexpr int PIN_VBAT = 34;
constexpr float VBAT_DIV = (100.0f + 33.0f) / 33.0f;  // 分圧比
constexpr float VBAT_WARN = 6.8f, VBAT_CUT = 6.4f;     // 2S LiPo

// ---------------- LED (WS2812B 直列順は docs/wiring.md と一致)
constexpr int N_LED = 12;
constexpr int LED_MAIN_EYE = 0;   // メインアイ
constexpr int LED_HEAD_EYE0 = 1;  // 頭部目 x3 (1..3)
constexpr int LED_RED0 = 4;       // 赤ランプ x8 (4..11)

// ---------------- WiFi
constexpr const char* AP_SSID = "Tachikoma";      // AP は常時維持 (操作UIのフォールバック)
// !!! 書き込み前に必ず自分の値へ変更すること (8文字以上 / WPA2)。
// 公開リポジトリのためプレースホルダにしてある — 実運用値をコミットしない
constexpr const char* AP_PASS = "change-me-8chars";
constexpr const char* MDNS_HOST = "tachikoma";    // http://tachikoma.local/
// STA (iPhone テザリング) の SSID/パスワードは Web UI 設定タブから入力し
// NVS (Preferences) へ保存する。ソースへのハードコード禁止 (main.cpp 参照)
// 注: NVS 暗号化 (Flash Encryption) は未対応のためパスワードは平文で
// Flash に保存される。個人所有ホビーロボットの脅威モデルでは許容範囲だが、
// 気になる場合は Flash Encryption / Secure Boot の導入を検討する
// (要 eFuse 書き込みのため書き込み後は後戻り不可)
constexpr const char* STA_PREFS_NS = "sta";

// ---------------- 音声 (I2S0 全二重: INMP441 マイク + MAX98357A アンプ)
// BCLK/WS は録音・再生で共用。DOUT=アンプ(MAX98357A) DIN、DIN=マイク(INMP441) SD。
// 16kHz/16bit/mono固定 (audio.h)。DFPlayer (Serial2, PIN_DF_RX/TX) とはピン非衝突
constexpr int PIN_I2S_BCLK = 26;
constexpr int PIN_I2S_WS = 25;
constexpr int PIN_I2S_DOUT = 27;  // MAX98357A DIN (再生)
constexpr int PIN_I2S_DIN = 33;   // INMP441 SD (録音)
constexpr uint32_t AUDIO_SAMPLE_RATE = 16000;
