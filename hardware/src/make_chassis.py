"""ボディシャーシプレート (全軸 STD サーボ構成)。

役割: 4 個のヨーサーボ (STD, 軸下向き) と電装一式を載せる背骨。
意匠シェル (Cabin/Head) はこの上に被せて周囲タブへ固定する。

ヨーサーボの取付 (2026-07 レビュー反映):
  - ケースは上から差し込み、プレートにはケース断面の通し穴のみ開ける
  - タブはプレート上面の 4 つの台座ボス (h3) に着座し、M3 セルフタップで固定
  - ギヤヘッド/ホーンはプレート下面側へ突き出し、coxa 天板と結合する

v3 (2026-07-28) 公式フィギュア下面実測の放射配置:
  - 股ヨー軸 r=HIP_R, 方位 15/165/210/330° (前脚±75°/後脚±120°, 正面基準)
  - ケースは軸まわり回転自由 (結合はホーンのみ) → 4 個とも長手 X 軸平行・
    ボディを中央向きに寝かせる。45° ペア (FR-RR/FL-RL) 間クリア 17.9mm
  - 真後ろ 270° (=フィギュアの 180° 後方) にポッド接続ブラケット
    (M3×4 + φ8)。吊りスタンドと共用。ネック梁 pod_neck は別パーツ
  - バッテリーはプレート下面中央の吊りクレードル (別パーツ) — 低重心化。
    coxa 内側リーチ (r≈39 @ヨー±35°) の内側 r≤36 に収める

座標系: 原点=プレート中心, +Y=前, +Z=上。プレートは z=[0, CHASSIS_T]。
"""
from pathlib import Path

import numpy as np
import trimesh
from manifold3d import Manifold

import config as C
from lib import box, cyl, cyl_y, rbox, export

MODEL_DIR = Path(__file__).resolve().parent.parent.parent / "model"

HIPS = dict(C.HIPS)
P = C.YAW_SERVO
BOSS_H = 3.0
# ケース長手の向き (軸→ケース中心の方位角)。全ケース X 軸平行・中央向き
CASE_ANG = {"FR": 0.0, "FL": 180.0, "RL": 180.0, "RR": 0.0}
# ポッドブラケット (プレート後端, スタンド共用)。x±12 はネック梁 (幅16) の
# 外側にビス頭座面を確保するため
POD_BOLTS = [(sx * 12.0, C.POD_NECK_Y0 + sy * 8.0)
             for sx in (-1, 1) for sy in (-1, 1)]
# バッテリークレードル取付 (プレート下面へ M3 セルフタップ)。前後端フランジ
# 位置 = ケース通し穴 (x≥13.1) を避けた中央ストリップ
CRADLE_BOLTS = [(sx * 10.0, 8.0) for sx in (-1, 1)] + \
               [(sx * 10.0, -20.0) for sx in (-1, 1)]


def chassis() -> Manifold:
    t = C.CHASSIS_T
    plate = cyl(t, C.CHASSIS_D).translate([0, 0, t / 2])

    # ---- ヨーサーボ (STD): タブ台座ボス → ケース通し穴 → タブビス下穴
    cx = P["L"] / 2 - P["SHAFT_OFF"]  # 軸→ケース中心 (10.35)
    hole_xs = (-cx - P["HOLE_PITCH"] / 2, -cx + P["HOLE_PITCH"] / 2)
    hole_ys = (-P["HOLE_SPREAD"] / 2, P["HOLE_SPREAD"] / 2)
    for name, (x, y) in HIPS.items():
        a = np.radians(CASE_ANG[name])
        ca, sa = np.cos(a), np.sin(a)
        for hx in hole_xs:
            for hy in hole_ys:
                bx = x + hx * ca - hy * sa
                by = y + hx * sa + hy * ca
                # φ8: タブ穴中心⇔ケース開口エッジの隙間 4.1mm に収める (φ9 は
                # 開口へ 0.4mm 食い込んでいた — レビュー指摘)
                plate += cyl(BOSS_H + 2, 8).translate([bx, by, t + BOSS_H / 2 - 1])
        hole = box(P["L"] + 0.6, P["W"] + 0.6, 40).rotate(
            [0, 0, CASE_ANG[name]]).translate([x + (-cx) * ca, y + (-cx) * sa, t / 2])
        plate -= hole
        for hx in hole_xs:
            for hy in hole_ys:
                bx = x + hx * ca - hy * sa
                by = y + hx * sa + hy * ca
                plate -= cyl(30, P["TAB_HOLE_D"]).translate([bx, by, t / 2])

    # ---- 電装マウント
    # PCA9685 ×2 縦積み (中央ストリップ, 基板長手を Y に 90° 回転)。
    # スタック中心 y / ボス高は config.PCA_STACK_Y0 / PCA_BOSS_H が唯一の正
    # (Head_Top 内部逃がし tools/make_head_eyecut.py の検算と共有 — 2026-08-22)
    for sx in (-1, 1):
        for sy in (-1, 1):
            bx = sx * C.PCA9685_HOLES[1] / 2                  # ±9.5 (短辺)
            by = C.PCA_STACK_Y0 + sy * C.PCA9685_HOLES[0] / 2  # 1±27.95 (長辺)
            plate += cyl(C.PCA_BOSS_H, 6).translate([bx, by, t + C.PCA_BOSS_H / 2])
            plate -= cyl(14, 2.2).translate([bx, by, t + 2])
    # ESP32 DevKit のネジ止めマウントは撤去 (2026-08-21)。経緯:
    # 2026-07-28 に y=40 → C.ESP32_Y0=-12.5 帯へ移設したが、これは不成立
    # だった (ユーザーがスライサ上で「浮いた部品」として発見):
    #   - 後側スタンドオフ (±25.5, -24.5) が後脚ケース開口 (x 13.1-54.4,
    #     y -35.9..-15.1) の内側 → プレートと繋がらない浮遊島 (印刷不良)
    #   - 仮に繋いでも基板本体 (x±29, y≈-41..+16) が前脚・後脚どちらの
    #     開口上空も横切り、タブ面より上へ突き出るサーボケース上部と干渉
    #   - 51×24 の穴パターンを同一面に置ける帯は存在しない: x=±25.5 で
    #     使える y 帯は後脚開口と前脚開口の間 [-11.1, -1.5] の 9.6mm のみで
    #     24mm スパンが入らない (前開口帯 2.8-23.6 も 20.8mm < 24mm)
    # 当面は基板を両面テープ/マジックテープで固定する運用 (9g、歩行実験に
    # 支障なし)。恒久マウント (PCA スタック上段化や中央ストリップ再配置) は
    # 頭部ドーム内クリアランス検証込みで別途設計する。
    # 当時の検証 (check_screw_bosses.py) がこれを見逃した理由: プローブ点が
    # ボス「内部」の充填率だけを見ており (浮遊島でも 100%)、ボス直下の
    # プレート実体 (z 1-3) と south 側ペア vs 後脚開口を検査していなかった。
    # バッテリークレードル (プレート下面吊り) の M3 下穴
    for bx, by in CRADLE_BOLTS:
        plate -= cyl(t + 2, C.M3_TAP).translate([bx, by, t / 2])

    # ---- 軽量化穴 (FR-RR / FL-RL ケース間の帯)
    for sx in (-1, 1):
        plate -= cyl(t + 2, 12).translate([sx * 48, -6, t / 2])

    # ---- ポッド接続ブラケット (後端中央, 吊りスタンド共用): 補強パッド +
    #      M3×4 タップ (ポッド配線はネック梁上面に沿わせる)
    plate += rbox(34, 26, t, r=5).translate([0, C.POD_NECK_Y0, t / 2])
    for bx, by in POD_BOLTS:
        plate -= cyl(t + 2, C.M3_TAP).translate([bx, by, t / 2])

    # ---- シェル取付タブ (周囲 7 箇所, 放射状 r=78, φ3.2)
    #      270° は 245/295 に分割 (ポッドネック梁が 270° を通るため)
    for ang in (90, 30, 150, 210, 330, 245, 295):
        a = np.radians(ang)
        x, y = 78 * np.cos(a), 78 * np.sin(a)
        plate += rbox(16, 16, t, r=5).translate([x, y, t / 2])
        extra = C.MOUTH_FRONT_TAB_BOSS_H if ang == 90 else 0.0
        if extra:
            # 口の逃げは前タブ下面をかすめる。穴位置は保ち、上面側の
            # 一体カラーでz2以上に5mm厚の無欠損リングを確保する。
            plate += cyl(extra, 10).translate([x, y, t + extra/2])
        plate -= cyl(t + extra + 2, 3.2).translate([x, y, (t + extra)/2])

    # ---- 配線通し穴 (ケース間の空き φ9 ×2)
    for sx in (-1, 1):
        plate -= cyl(t + 2, 9).translate([sx * 16, -6, t / 2])

    # ---- 腕ヨーサーボ (MICRO, 軸下向き): 脚ヨーと同じ「上から挿入」パターン。
    #      肩ブラケットはプレート下面側のホーンから吊り下がる。取付 XY は
    #      Head_Bottom 実ソケット直下 (2026-07-28 移設, C.ARM_MOUNT_XY 参照,
    #      正面±40°相当・旧 (16,74) より前脚寄り)。ケース長手は前後方向のまま
    #      (放射方向へは回転させない — 実メッシュ確認で干渉なし, check_arm.py [1])。
    PA = C.ARM_SERVO
    cxa = PA["L"] / 2 - PA["SHAFT_OFF"]          # 軸→ケース中心 (5.6)
    arm_hole_ys = (-cxa - PA["HOLE_PITCH"] / 2, -cxa + PA["HOLE_PITCH"] / 2)
    for side in (-1, 1):
        ax, ay = side * C.ARM_MOUNT_XY[0], C.ARM_MOUNT_XY[1]
        plate += rbox(26, 28, t, r=4).translate([ax, ay + 2, t / 2])
        # タブ台座ボス (M2): ケース開口との隙間が 2.1mm しかないため、円柱では
        # なく開口と反対側へ 1.5mm 逃がした長円ボスにする (開口への食込み防止)
        for hy in arm_hole_ys:
            away = -1.5 if hy < -cxa else 1.5   # 開口 (ケース中心側) の反対へ
            plate += rbox(8, 7, C.ARM_BOSS_H + 2, r=2).translate(
                [ax, ay + hy + away, t + C.ARM_BOSS_H / 2 - 1])
        plate -= box(PA["W"] + 0.6, PA["L"] + 0.6, 40).translate([ax, ay - cxa, t / 2])
        for hy in arm_hole_ys:
            plate -= cyl(30, PA["TAB_HOLE_D"]).translate([ax, ay + hy, t / 2])

    # ---- 単一ボディ検算 (2026-08-21 ESP32 浮遊島事故の再発防止)。
    # ボス追加より前に開口カットが走る構成なので、開口内へボスを置くと
    # 「浮遊した島」が黙って出力される。分離片は不良の兆候なので即 assert
    # (make_head の decompose-keep-largest と違い、ここは削らず失敗させる —
    # チャシのボスは全て取付機能部品であり、勝手に消してよい island は無い)
    parts = plate.decompose()
    assert len(parts) == 1, (
        f"chassis が {len(parts)} 個の分離ボディ: "
        f"{sorted((round(p.volume()) for p in parts), reverse=True)} mm3 — "
        "ボスが開口上に浮いていないか確認せよ")
    # 頭下部の球とNeck/Capはプレート高さを横切る。頭皿の座グリとは別に、
    # シャーシにも固定組立包絡を設ける。visibleなキット部品は削らない。
    plate -= mouth_clearance()
    # 複雑な実口形状との差で生じた重複頂点/ゼロ厚面をSTL往復前に整理する。
    # 0.001mmで体積差0.004mm3未満。外形・支持材の保持は別検査する。
    plate = plate.simplify(C.MOUTH_CHASSIS_SIMPLIFY_MM)
    return plate


def mouth_clearance() -> Manifold:
    """口の固定部品+0.3mmの逃げ。原型Cannon座標→chassis座標を共用する。"""
    import make_audio as audio
    # Ballの配線ボア内へ薄いプレート片を残すと、下方からの挿入で引掛かる。
    # 球のみ凸包に戻してボア内を含める（0.13mm³の経路干渉を独立検査で再現）。
    shapes = [audio.mouth_ball_bored().hull().translate([0, C.MOUTH_BALL_LOCAL_Y, 0]),
              audio.mouth_neck_bored().translate([0, C.MOUTH_NECK_LOCAL_Y, 0]),
              audio._load("Mouth_Cap_Grey").translate([0, C.MOUTH_CAP_LOCAL_Y, 0])]
    # 凸包ではCapの空洞まで埋めて前タブを余分に削るため、実体を膨張する。
    # 1閉体/ボス保持と下側からの組立経路はcheck_mouth_chassis.pyで確認する。
    negative = Manifold()
    for shape in shapes:
        negative += shape.minkowski_sum(Manifold.sphere(C.MOUTH_CHASSIS_CLEAR, 16))
    return negative.rotate([C.MOUTH_CANNON_ROT_X_DEG, 0, 0]).translate(C.MOUTH_CANNON_T)


# ---- 頭部逃がしカット (2026-07-31, 任務: 頭部中央寄せ ①B) ----------------
# ARM_MOUNT_HUB_Y を 12→0 (シャーシ中心) へ寄せると、Head_Bottom/Head_Top
# (頭部クラスタ, 元キット形状=可視ジオメトリにつき無加工で保護) の後面が
# pod_neck の基部ブラケット (プレート後端, POD_NECK_Y0 付近) と実体干渉する
# (hub_y=0 で合計 4.59cm^3, tools/check_head_pod_clearance.py 参照)。
#
# 実測で判明した制約 (2026-07-31): 干渉域は y=[-62.7,-43] に収まり、これは
# 基部ブラケットパッド自体の footprint (y=[POD_NECK_Y0-15, POD_NECK_Y0+15]
# = [-73,-43], M3×4 でプレートへ共締め) の内側にほぼ完全に含まれる。この帯は
# 「チャシプレート直上 = 頭部シェルの中空内部」という、そもそも隠しブラケット
# が常駐して当然の領域 (電装基板やバッテリークレードルと同じ立ち位置) にあり、
# 実際 y=-43 近傍ではブラケット厚みゼロ (z_local=0, プレート直上) でさえ頭部
# シェル境界の内側にある — 「頭部シェル全域から2mmクリアランス」を額面通り
# 満たすことは、このブラケット帯に限っては原理的に不可能 (どれだけ削っても
# 達成できない)。よって同じ y 帯にある梁の "パッド厚みを超える余剰高さ"
# だけを削り、パッド自体 (0〜HEAD_RELIEF_PROTECT_H) は「シャーシ直上の隠し
# 実装」として意図的に残す設計とした。tools/check_head_pod_clearance.py は
# この設計方針に合わせて基準を更新済み (パッド帯を除いた干渉/クリアランスを
# 判定)。
#
# HEAD_RELIEF_PROTECT_H (パッド保護厚み) は元のパッド厚み 4.0mm から
# 8.0mm へ増厚 (非可視リブ補強 — カット後の断面のみでの強度検証で安全率
# 不足だったための対応。数値根拠は docs/assembly.md 強度計算節参照。パッドは
# 頭部シェル内部にあり外から見えないため増厚しても意匠上の制約はない)。
# 2026-07-31 QA major 指摘 (応力集中係数 Kt 未考慮) を受け、6.0→8.0mm へ
# 再増厚し (Kt 込みの実効安全率マージンを確保)、併せて下記 CHAMFER_RUN_MM の
# 傾斜遷移も追加した (docs/assembly.md 強度計算節・本節末尾コメント参照)。
HEAD_RELIEF_MARGIN_MM = 2.0    # check_head_pod_clearance.py の要求クリアランス
HEAD_RELIEF_PROTECT_H = 8.0    # パッド保護厚み (chassis-local z, 0=プレート
                                # 上面)。元のパッド厚み4.0mmより厚い分は
                                # 非可視リブ補強 (梁側の増厚)
CHAMFER_RUN_MM = 12.0  # 2026-07-31 QA major 指摘 (応力集中): 逃がしカットの
                        # 境界 (y 方向, カッター footprint の始端 lo[1]) で
                        # 梁高さが HEAD_RELIEF_PROTECT_H まで垂直に段差状に
                        # 落ちると、段付き梁の曲げに典型的な応力集中 (Kt) が
                        # 生じる。境界から CHAMFER_RUN_MM だけ**外側**
                        # (y がさらに負, 頭部クリアランス判定の除外域
                        # footprint の外・頭部から遠ざかる方向) の帯を、
                        # 段差 (垂直壁) ではなく傾斜面 (ウェッジ) で遷移させ、
                        # 実応力集中を緩和する (_head_relief_cutter() 参照。
                        # 内側に置くと除外域の外側で頭部シェルと新規干渉に
                        # なるバグを実装時に確認・修正した)。真の丸フィレット
                        # ではなく直線テーパーだが、90°の鋭い切欠きより Kt を
                        # 大きく下げる。数値的な Kt 見積り (文献値, UNVERIFIED)
                        # は docs/assembly.md 強度計算節を参照


def _load_head_kit(name: str) -> trimesh.Trimesh:
    m = trimesh.load(MODEL_DIR / f"{name}.stl")
    m.apply_translation(-(m.bounds[0] + m.bounds[1]) / 2)
    m.apply_scale(C.SCALE)
    return m


def _rot_z(deg: float) -> np.ndarray:
    t = np.radians(deg)
    mtx = np.eye(4)
    mtx[0, 0], mtx[0, 1], mtx[1, 0], mtx[1, 1] = np.cos(t), -np.sin(t), np.sin(t), np.cos(t)
    return mtx


def _head_relief_cutter() -> Manifold:
    """Head_Bottom/Head_Top (C.ARM_MOUNT_HUB_Y 配置) との干渉域 + マージンを
    pod_neck から差し引くカッター (chassis-local, z=0 がプレート上面)。

    tools/check_head_pod_clearance.py の head_meshes() と同じ配置式 (body_h
    に依らず相対関係は不変なので任意の代表値でよい) で実メッシュを読み、外接
    直方体 + HEAD_RELIEF_MARGIN_MM を求める (make_head.py _armcut_box_right()
    と同じ「実測+マージンの外接直方体」流儀)。z 下限は HEAD_RELIEF_PROTECT_H
    でクランプし、保護パッド厚みより下は削らない (上記コメント参照)。

    2026-07-31 QA major 指摘 (Kt 未考慮) 対応: この直方体カッターの y 方向
    始端 (lo[1]) で梁高さが PROTECT_H まで垂直に落ちる段差ができ、段付き梁の
    曲げの典型的な応力集中源になる。CHAMFER_RUN_MM ぶんの帯を垂直壁ではなく
    直線テーパー (ウェッジ) に置き換えて遷移を緩和する (_y_chamfer_wedge()
    参照)。**傾斜帯は必ず lo[1] より外側 (y がさらに負, 頭部クリアランス
    判定の対象になる干渉域からより遠い側) に置く**こと — 内側 (lo[1] より
    y が大きい側, tools/check_head_pod_clearance.py の除外域 footprint内)
    に置くと、除外域の z 上限 (=PROTECT_H) を超えて張り出す「傾斜のぶん
    余分に残した material」が除外域の外側で頭部シェルと新規に干渉する
    (2026-07-31 実装時に発覚したバグ — 最初 lo[1] の内側に傾斜帯を置いて
    check_head_pod_clearance.py が新規 NG になることを確認して外側へ修正)。
    """
    body_h = 105.0
    zb = body_h + C.HIP_DROP
    head_top_z_offset = 57.7  # tools/make_visuals.py HEAD_TOP_Z_OFFSET と同一定数

    hub_y = C.ARM_MOUNT_HUB_Y
    hb = _load_head_kit("Head_Bottom_Blue")
    hb.apply_transform(_rot_z(180))
    hb.apply_translation((0, hub_y, zb - 3))
    ht = _load_head_kit("Head_Top_Blue")
    ht.apply_transform(_rot_z(180))
    ht.apply_translation((0, hub_y, zb + head_top_z_offset))

    m = HEAD_RELIEF_MARGIN_MM
    lo = np.minimum(hb.bounds[0], ht.bounds[0]) - m
    hi = np.maximum(hb.bounds[1], ht.bounds[1]) + m
    z0 = zb + C.CHASSIS_T   # world z における pod_neck 自身の z_local=0 面
    lo[2] -= z0
    hi[2] -= z0
    lo[2] = max(lo[2], HEAD_RELIEF_PROTECT_H)

    run = min(CHAMFER_RUN_MM, (hi[1] - lo[1]) / 2)  # 帯が y 範囲の半分を
    # 超えないようクランプ (pod_neck 実寸に対し十分小さいため通常は無効)
    # ランプの上限 (z_ramp_ceil) は hi[2] (頭部メッシュ由来の遠い上限,
    # pod_neck の実高さよりずっと上) ではなく梁の実高さ (C.POD_NECK_BEAM[1])
    # + 余裕にクランプする。hi[2] のまま直線ランプすると、実材 (高々梁高さ
    # 程度) に到達する前に run 区間をほぼ使い切ってしまい、ランプが実質
    # 無効化される (2026-07-31 実装時に発覚したバグ — 実測でランプ域全体が
    # 無カットになっていることを確認して修正)。z_ramp_ceil より上は
    # どの y でも保護すべき実材が無いため常時フルカットでよい
    z_ramp_ceil = min(hi[2], C.POD_NECK_BEAM[1] + 4.0)

    # always_cut は本来のカッター y 範囲 [lo[1],hi[1]] 全域 (傾斜帯を
    # y<lo[1] 側の外部へ追加するため、ここは変更しない)
    always_cut = Manifold.cube(
        [hi[0] - lo[0], hi[1] - lo[1], hi[2] - z_ramp_ceil], True).translate(
        [(hi[0] + lo[0]) / 2, (hi[1] + lo[1]) / 2, (hi[2] + z_ramp_ceil) / 2])

    # コア (フラットカット) は従来どおり lo[1] から開始 (縮めない —
    # 除外域 footprint 内は元の垂直カットのまま、干渉判定に影響しない)
    core = Manifold.cube(
        [hi[0] - lo[0], hi[1] - lo[1], z_ramp_ceil - lo[2]], True).translate(
        [(hi[0] + lo[0]) / 2, (hi[1] + lo[1]) / 2, (z_ramp_ceil + lo[2]) / 2])

    # 傾斜帯は lo[1] から run だけ「外側」(y がより負, 除外域 footprint の
    # 外・頭部から遠ざかる方向) へ張り出す — 除外域境界の内側には踏み込まない
    wedge = _y_chamfer_wedge(
        x_lo=lo[0], x_hi=hi[0], y_edge=lo[1] - run, run=run,
        z_top=z_ramp_ceil, z_cut=lo[2])
    return always_cut + core + wedge


def _y_chamfer_wedge(x_lo: float, x_hi: float, y_edge: float, run: float,
                      z_top: float, z_cut: float) -> Manifold:
    """y=y_edge (カット深さ0, 周囲の非カット面に一致) から y=y_edge+run
    (カット深さ full=z_top-z_cut, メインカッターに一致) へ直線的にランプする
    くさび形カッター。y-z 断面が直角三角形の三角柱 (x 方向に押し出し)。
    run<=0 の場合は空 (段差なしのカット領域では遷移不要) を返す。
    """
    if run <= 1e-6:
        return Manifold()
    pts = np.array([
        [x_lo, y_edge, z_top], [x_hi, y_edge, z_top],
        [x_lo, y_edge + run, z_top], [x_hi, y_edge + run, z_top],
        [x_lo, y_edge + run, z_cut], [x_hi, y_edge + run, z_cut],
    ])
    return Manifold.hull_points(pts)


def pod_neck() -> Manifold:
    """ポッド接続ネック梁 (隠し構造材)。

    プレート後端ブラケットへ M3×4 で共締めし、-Y へ POD_NECK_LEN 張り出す。
    先端の縦フランジをポッド (Cabin_Front 前面下部) の内側当て板と M3×4 で
    共締め。外観はキット Head_TailJoint_Blue コーン (150%) を梁に被せて接着
    (公式フィギュアの青コーンネック再現。docs/assembly.md 参照)。

    先端の丸ポスト絞り (2026-07-30 追加): 梁断面 16×12 (対角20.0mm) は
    TailJoint_Blue/Ball の内部ボアにそのままでは通らない (旧記述は「梁先端
    4隅を現物合わせで面取り」だった)。TailJoint 側の内部形状を実メッシュで
    調べたところ、単純な丸ボアではなくキット本来のテール関節用
    "Optional_Cross" 十字キー溝で、半径が角度により 6.9〜11.2mm 相当で
    変動すると判明 (回転位相を保証する機構が無い接着継手にこの十字形状を
    当てにするのは不安定)。よって TailJoint 側は無加工のまま (印刷そのまま
    使用)、代わりに完全に自制御下にあり非可視の pod_neck 側だけで解決する:
    梁先端の NECK_TAPER_LEN 区間を、対角がどの回転位相でも必ず収まる円形
    ポスト (NECK_POST_D, 実測ワースト半径7.0mmに対し片側1.0mm安全代) へ
    「対角から絞る」円錐カッターで絞り込む (角を残しつつ絞る中間区間は
    対角逃げそのもの、絞り切った先端は回転位相を問わない丸ポストになる)。
    """
    bw, bh = C.POD_NECK_BEAM
    y0 = C.POD_NECK_Y0
    y1 = y0 - C.POD_NECK_LEN
    # 基部プレート (z=0 が底面 = シャーシプレート上面に着座)
    part = rbox(32, 30, 4, r=4).translate([0, y0, 2])
    # 梁 (基部と同じ底面高さで -Y へ連続)
    beam = box(bw, (y0 + 15) - y1, bh).translate([0, ((y0 + 15) + y1) / 2, bh / 2])
    # 先端 NECK_TAPER_LEN 区間だけを丸ポストへ絞るカッター: 円錐 (先端側
    # NECK_POST_D/2 → 絞り開始側 diag/2+マージン) + それより手前は梁全体を
    # 包含する太い円柱 (絞りに寄与しない)。梁とのブーリアン積で、絞り開始
    # 側は無加工のまま (円柱半径 > 半対角)、先端に向かって角から徐々に
    # 絞られ、先端では完全な円形断面 (半径 NECK_POST_D/2) になる
    taper_len = C.NECK_TAPER_LEN
    r_wide = (bw ** 2 + bh ** 2) ** 0.5 / 2 + 1.5  # 半対角+マージン (梁を包含)
    cone = Manifold.cylinder(taper_len, r_wide, C.NECK_POST_D / 2, 0, True) \
        .rotate([90, 0, 0]).translate([0, y1 + taper_len / 2, bh / 2])
    collar = cyl_y((y0 + 15) - (y1 + taper_len) + 2, 2 * r_wide).translate(
        [0, ((y0 + 15) + (y1 + taper_len)) / 2, bh / 2])
    beam = beam ^ (cone + collar)
    part += beam
    # 先端フランジ (縦板, 梁中心高さ基準。ポッド内側当て板と M3×4 共締め)
    fw, fh = C.POD_FLANGE
    part += box(fw, 4, fh).translate([0, y1 - 2, bh / 2])
    for sx in (-1, 1):
        for dz in (-10, 10):
            part -= cyl(10, C.M3_FREE).rotate([90, 0, 0]).translate(
                [sx * 12, y1 - 2, bh / 2 + dz])
    # 基部の取付穴 (M3 フリー ×4, ビス頭は梁の外側 x±12) — 最後に開ける
    for bx, by in [(sx * 12.0, y0 + sy * 8.0) for sx in (-1, 1) for sy in (-1, 1)]:
        part -= cyl(20, C.M3_FREE).translate([bx, by, 2])
    # 頭部逃がしカット (2026-07-31, 任務: 頭部中央寄せ ①B。上記
    # _head_relief_cutter() コメント参照)。ボルト穴カットの後に適用しても
    # 順序依存はない (減算は可換)
    part -= _head_relief_cutter()
    return part


def battery_cradle() -> Manifold:
    """バッテリー吊りクレードル (プレート下面, 隠し内部パーツ)。

    2S 2200mAh パック (~105×34×24, BOM #5) の中央部を長手 Y (前後) 向きに
    抱える短トンネル形。パックは前後へオーバーハング (パック本体 x±17 は
    coxa 掃引と干渉しない — 干渉するのは壁だけなので、壁は後脚掃引の届か
    ない前方帯 y∈[-26,14] に限定する。check_leg_assembly で実メッシュ検証)。
    バッテリーは -Y 端から差し込み、ベルクロベルト×2 を底面スリットに通す。
    z=0 がプレート下面。前後端の天面フランジをプレートへ M3×4。
    """
    W, D, H = 44.0, 40.0, 32.0   # 外形 (X × Y × 深さ)。中心 y = -6
    yc = -6.0
    wall = 3.0
    flange = 4.0                 # 天面フランジ厚 (M3 頭 2mm 座ぐり込み)
    body = rbox(W, D, H, r=4).translate([0, yc, -H / 2])
    # トンネル内腔 (底 wall + 側壁 wall + 天面フランジを残し、±Y 開放)。
    # 内腔 z = [-(H-wall), -flange] = [-29, -4] → 高さ 25 (パック 24 対応)。
    # 内腔幅 38 (パック 34-36 対応)
    body -= box(W - 2 * wall, D + 4, H - wall - flange).translate(
        [0, yc, -(H - wall + flange) / 2])
    # 天面はフランジ帯 (前後端 12mm) 以外を開放 (配線/放熱と軽量化)
    body -= box(W - 2 * wall, D - 24, flange + 2).translate([0, yc, -2])
    # ベルトスリット (底板, X 方向に貫通する帯 ×2)
    for sy in (-18.0, 6.0):
        body -= box(W + 2, 5, wall + 2).translate([0, sy, -H + wall / 2])
    # 取付ボルト (天面フランジを貫通, 頭は内腔側に沈める)
    for bx, by in CRADLE_BOLTS:
        body -= cyl(12, C.M3_FREE).translate([bx, by, -2])
        body -= cyl(4, 6.4).translate([bx, by, -4])  # 頭座ぐり (フランジへ 2mm)
    return body


if __name__ == "__main__":
    print("[chassis]")
    export(chassis(), "chassis")
    print("[pod_neck]")
    export(pod_neck(), "pod_neck")
    print("[battery_cradle]")
    export(battery_cradle(), "battery_cradle")
