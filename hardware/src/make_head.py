"""Head_Bottom の腕通し開口 + マウス配線受け穴 (2026-07-30 追加, 手加工の焼き込みタスク)。

背景 [実メッシュ検証, 2026-07-30]: config.py の ARM_MOUNT_XY/YAW は
「Head_Bottom_Blue の実ソケット (正面±40°) へ加工不要でそのまま使えるか、
最小限に拡口するだけでよい」という見立てのもとで導出されていたが、この
見立ては座標/半径の突合せのみで、shoulder_bracket の実メッシュと Head_Bottom
シェルの実ブーリアン干渉は一度も検証されていなかった。本タスクで実際に
検証したところ、**肩ヨー可動域の全域 (中立姿勢を含む) で shoulder_bracket
の取付プレートがシェル材と実体干渉する**ことが判明した (交差体積
166〜182mm³, 肩ヨー角によらずほぼ一定 — プレート縁がソケットリム付近の
殻材へ常時食い込んでいる。肘/肩ピッチは shoulder_bracket 自体を動かさない
= upper_arm/forearm 側はこの干渉に無関係、と実メッシュで確認済み)。

よって printing.md の「加工不要 or 現物合わせで最小拡口」という記述は
「拡口が必要」という結論に置き換える。拡口量は shoulder_bracket の実形状
から確定的に計算できる (現物合わせ不要) ため、ここで加工版
Head_Bottom_Armcut.stl を焼き込む。

手法: make_arm.shoulder_bracket() を肩ヨー可動域全域 (±ARM_YAW_LIM +
ARMCUT_YAW_MARGIN_DEG の安全マージン) でスイープし、Head_Bottom_Blue との
実ブーリアン交差を毎姿勢で計算 → 交差領域の外接直方体 (+ARMCUT_MARGIN_MM
マージン) を単純な直方体カッターとして焼き込む。単純直方体を選んだ理由:
交差領域そのもの (角度ごとに薄い異形スライバー) を厚み方向に和集合すると
manifold3d/trimesh 双方で退化面が生じ非 watertight になった (本ファイル
開発中に実際に発生・棄却した手法) — 外接直方体+マージンの方が数値的に
頑健で、Head_Top_Eyecut 等の既存 _Bored/_Eyecut 系パーツと同じ「シンプルな
一次カッター形状」の流儀にも合う。カット後に同じ角度スイープで交差体積が
0 になることを確認済み (下記 __main__ 実行時に検算。恒常検証は
tools/check_arm.py [1c] 参照)。

左右対称性: 右ソケットの干渉領域を実メッシュで計算し、左ソケットはこれを
X ミラーしたものを使う (Head_Bottom_Blue 自体は概ね X=0 面対称な頭部シェル
形状であり、check_arm.py が他の腕系検証で採用している「右のみ実メッシュ
検証し左は鏡像として扱う」省略ロジックと同じ考え方)。

マウス配線受け穴 (2026-07-30, 配線連通の悉皆確認タスクで追加): 音声ユニット
(make_audio.py) の配線は Mouth_Ball_Bored の中心ボアを抜けた先、Head_Bottom
のマウスソケット (MOUTH_SOCKET_LOCAL) の奥で行き止まりになっていた
(config.py MOUTH_HEAD_BORE_* のコメント参照 — 実メッシュ実測でソケット奥の
殻材は厚み約5mmの薄い一層のみと判明、現物合わせ不要と判断)。ここでは
ソケット軸 (MOUTH_SOCKET_OUTWARD_LOCAL) に沿った単純な円柱カッターを
Head_Bottom_Armcut に追加焼き込みする (腕ソケット拡口と同じ「実測+マージン
の一次カッター」流儀)。

マウスソケット内縁の逃がし (2026-07-31, 任務2 砲身ポーズ変更に伴う追加):
config.py MOUTH_CANNON_ROT_X_DEG を -60.38°→-18.0° へ変更した結果、
Mouth_Neck_Blue が Head_Bottom シェル (ソケット開口リム付近) と実体干渉する
ようになった (scratchpad/mouth_pose_sweep.py で実測: phi=18°で0.0562cm3。
Ball/Cannon 本体は全角度域で0のまま、Neck のみ)。ソケット開口自体は元キット
外観そのまま (φ26.8, 変更なし) を保ち、**内側 (頭部殻の内部, 外から見えない
側) だけ** を Neck の新ポーズ形状に合わせて逃がす。カッターは Neck 自身の
実メッシュ (小さくコンパクトな円錐台) を局所原点中心に RELIEF_SCALE 倍だけ
等方拡大したもの。

**定量的な注記 (2026-07-31 QA指摘, わずか=誤解を招くとの指摘で追記)**:
Neck 自身との交差体積は <0.06cm3 だが、これは「カッターがどれだけ Neck の
形状と重なるか」ではなく「Head_Bottom 自身から実際に何 mm3 の材料が削られる
か」の指標にはならない。実測 (head_bottom_armcut() から neck_relief 単独の
寄与を分離): カッター自体 (RELIEF_SCALE=1.15倍拡大した Neck) の体積は
6.025cm3、Head_Bottom シェルから実際に除去される材料量は 0.285cm3
(neck_relief 単独適用での体積差、他のカット [腕ソケット拡口/配線ボア] とは
独立に確認済み)。ソケット中心 (MOUTH_SOCKET_LOCAL) から放射状にレイキャスト
すると、方向によっては最大 ~2.5mm、カッター自身の中心から測ると最大 ~8.4mm
の局所後退が見られ、一部の視線 (ソケット軸に近い向き) はカット後に殻を
まったく貫通しなくなる (=その先まで開通した) ケースも複数観測される —
「Neck との交差 <0.06cm3」という数字だけから連想される「ごくわずかな
面取り」よりも実際の掘り込みは明確に大きい。後退面はいずれも頭部殻の
内部壁であり新規の外部開口を作るものではなく、実組立では Mouth_Ball/
Mouth_Neck が常時この位置を覆うため完成品としては不可視 (「見える形状の
無断改変禁止」ルールには抵触しない)。[UNVERIFIED: 掘り込み深さの正確な
最大値・分布はレイキャストの原点選びに依存し、本コメントの ~2.5mm/~8.4mm
はいずれも一つの代表的な原点からの実測値に過ぎない — 網羅的な最悪値
探索ではない]

機構逃がしカット (2026-08-20 追加, ユーザー指摘「この形状では脚が接続
できるように見えない」の検証タスクで発覚):
Head_Bottom はキット由来の**ほぼ中実な彫刻** (181.8cm3, bbox の 47%) で、
既存の検証は腕ソケット/マウス周りのみ — 「シェル vs シャーシ/バッテリー/
脚」の実メッシュ干渉は一度も検証されていなかった。確立済みゴースト配置
(rot180z + (0, ARM_MOUNT_HUB_Y, zb-3)) で全数計測した結果 (2026-08-20,
scratchpad/head_bottom_mech_analysis.py):
  - chassis プレート: 26.9cm3 (皿の上端リム z=+9.7 はプレート下面 z=0 より
    上にあり、キットで Head_Plate_Grey 薄ガスケットが挟まっていた合わせ目
    帯にプレート (t=4, φ144 > 皿 φ124.4) が丸ごと割り込む)
  - バッテリーパック実体 22.6cm3 + battery_cradle 6.5cm3 (パックは
    -Y 差し込み [docs/assembly.md] なので挿入経路も塞げない)
  - 前脚 coxa (ヨー±LIM_YAW スイープ): 各 0.33cm3、後脚 ~0.002cm3
  - pod_neck 1.0cm3 / Head_Top 下端リム 0.06cm3
対処 (このファイルで焼き込み):
  C1 プレート下面より上を全カット (z >= -HEADBOT_PLATE_CLR, chassis 座標)。
     クラウンは中実で 108.6cm3 がここで落ちる。外観への影響: 合わせ目下の
     青い帯 (高さ~10mm) が無くなりプレート縁が見える。ただしプレート
     (φ144+タブ~172) は元々皿 (φ124.4) より広く外へ張り出す設計
     (config.py CHASSIS_D コメント「意図的にプレート外周から張り出す耳」)
     であり、この帯は物理的にプレートと共存不能 — 完全な外観維持には
     「プレート上面に載る化粧リング」を別パーツ化する以外にない (未実装、
     必要になったら本コメントを起点に)。
  C2 バッテリークレードル外形 + パック実体 + -Y 挿入経路 (+MECH_CLR)。
  C3 各脚 coxa (bracket+股ピッチサーボ) をヨー±(LIM_YAW+margin) で
     スイープし、残交差の外接直方体+ARMCUT_MARGIN_MM を焼き込み
     (_armcut_box_right と同じ「実測+マージンの一次カッター」流儀)。
カット後の実測: 全相手 0.000cm3、残体積 26.8cm3 (=浅いボウル。腕/マウス
ソケットはプレート下面より下なので機能面は無傷、__main__ で恒常検算)。
取付は「ボウル上端リング面 (z=-0.5) をプレート下面へホットボンド」に変わる
(タブ r78-86 は皿 r62.2 の外なので皿には届かない — 従来 docs の「7 タブと
現物合わせ」は Head_Top 側のみに残る)。docs/assembly.md §3 参照。
"""
import re
import sys
from pathlib import Path

import numpy as np
import trimesh
from manifold3d import Manifold, Mesh as MMesh

import config as C
import make_arm
import make_chassis
import make_leg
from lib import export

ROOT = Path(__file__).resolve().parent.parent.parent
MODEL = ROOT / "model"

# 肩ヨー可動域 (firmware/src/config.h が唯一の正。複写値の drift を防ぐため
# 実ファイルから読む — check_arm.py と同じ流儀)
_FWTXT = (ROOT / "firmware" / "src" / "config.h").read_text()


def _fwval(name: str) -> float:
    return float(re.search(rf"{name}\s*=\s*([\d.]+)f", _FWTXT).group(1))


ARM_YAW_LIM = _fwval("ARM_YAW_LIM")
LEG_YAW_LIM = _fwval("LIM_YAW")      # 脚ヨー可動域 (check_leg_assembly と同じ実読)

# 安全マージン (check_arm.py 他の既存マージンと同オーダー: 脚ヨー検査の
# ±5°角度マージン、干渉クリアランス目標の 1.5mm と同じ考え方)
ARMCUT_YAW_MARGIN_DEG = 3.0
ARMCUT_MARGIN_MM = 1.5

# make_chassis.py の腕マウント原点オフセット (chassis-local, z=0=プレート下面)
ARM_HORN_OFF = -2.0

# Head_Bottom_Blue の確立済み配置 (tools/make_visuals.py shell_ghosts() /
# hardware/src/config.py ARM_MOUNT_* コメントと同一式。Y オフセットは
# C.ARM_MOUNT_HUB_Y を直接参照する (2026-07-31 QA指摘で修正: 以前はここも
# make_visuals.py 側も生の literal 12 を複製しており、将来 ARM_MOUNT_HUB_Y
# を変更しても追従しないドリフト穴だった — HEAD_TOP_Z_OFFSET で確立済みの
# 一元化パターンをここにも適用)。回転(rot180z)とZ(-3)は変更なし
HEAD_BOTTOM_T = ((0.0, C.ARM_MOUNT_HUB_Y, -3.0), 180.0)   # (translate, rotate-z-deg)


def _to_manifold(tm: trimesh.Trimesh) -> Manifold:
    return Manifold(mesh=MMesh(vert_properties=np.asarray(tm.vertices, np.float32),
                                tri_verts=np.asarray(tm.faces, np.uint32)))


def _load_kit_150(name: str) -> Manifold:
    """元キット STL を bbox 中心化→150% 化して Manifold へ (make_audio.py 等と同じ流儀)。"""
    tm = trimesh.load(MODEL / f"{name}.stl")
    tm.apply_translation(-(tm.bounds[0] + tm.bounds[1]) / 2)
    tm.apply_scale(C.SCALE)
    return _to_manifold(tm)


def _bracket_world(arm_yaw_deg: float, mx: float, my: float) -> Manifold:
    """shoulder_bracket (右腕) を指定の肩ヨー角・マウント XY でシャーシ座標へ配置。

    make_chassis.chassis() のブラケット取付式 (Tb = translate(mx,my,horn_off)
    @ rotate_z(90 - ARM_MOUNT_YAW_DEG - arm_yaw)) と同一 (check_arm.py [1b]/[8] と共通)。
    """
    ang = 90.0 - C.ARM_MOUNT_YAW_DEG - arm_yaw_deg
    return make_arm.shoulder_bracket().rotate([0, 0, ang]).translate([mx, my, ARM_HORN_OFF])


def _armcut_box_right(hb_chassis: Manifold, mx: float, my: float):
    """右ソケットについて、肩ヨー全域スイープでの Head_Bottom との交差を
    実メッシュで求め、外接直方体カッター (chassis-local, マージン込み) を返す。

    左ソケットはこの直方体を X=0 でミラーしたものを使う (head_bottom_armcut()
    参照 — 左腕ブラケットの取付角規約を別途導出する必要がなく、Head_Bottom
    自体の左右対称性のみに依拠する頑健な方法)。

    戻り値: (size(3,), center(3,)) — 交差が皆無なら None。
    """
    yaws = np.linspace(-ARM_YAW_LIM - ARMCUT_YAW_MARGIN_DEG,
                        ARM_YAW_LIM + ARMCUT_YAW_MARGIN_DEG, 13)
    lo = np.array([np.inf] * 3)
    hi = np.array([-np.inf] * 3)
    found = False
    for arm_yaw in yaws:
        br_w = _bracket_world(arm_yaw, mx, my)
        inter = br_w ^ hb_chassis
        v = inter.volume()
        if v > 1e-3:
            found = True
            bb = inter.bounding_box()
            lo = np.minimum(lo, np.array(bb[:3]))
            hi = np.maximum(hi, np.array(bb[3:]))
    if not found:
        return None
    lo -= ARMCUT_MARGIN_MM
    hi += ARMCUT_MARGIN_MM
    return (hi - lo), (hi + lo) / 2.0


# Mouth_Neck_Blue の Cannon-local Y オフセット (tools/data/kit_assembly_front.json
# の Mouth_Neck_Blue エントリ t.y=-28.97 が唯一の正。ここでの再定義は複写だが
# 値そのものは同ファイルを見て一致させること)
NECK_CANNON_LOCAL_Y = -28.97
RELIEF_SCALE = 1.15   # Neck を局所原点中心に等方拡大する係数 (中心から
                       # 概ね半径12mmの円錐台に対し、片側+1.8mm程度の逃がし
                       # マージン相当 — [8]/[8b] 等の他マージンと同オーダー)


def _mouth_socket_relief_chassis() -> Manifold:
    """Mouth_Neck_Blue の新ポーズ (config.MOUTH_CANNON_ROT_X_DEG/T) をわずかに
    等方拡大したものを、Head_Bottom シェル減算用のカッターとして chassis
    座標で返す。"""
    tm = trimesh.load(MODEL / "Mouth_Neck_Blue.stl")
    tm.apply_translation(-(tm.bounds[0] + tm.bounds[1]) / 2)
    tm.apply_scale(C.SCALE)
    tm.apply_scale(RELIEF_SCALE)   # 局所原点 (bbox中心) 基準にそのまま拡大
    tm.apply_translation([0.0, NECK_CANNON_LOCAL_Y, 0.0])
    neck = _to_manifold(tm)
    neck = neck.rotate([C.MOUTH_CANNON_ROT_X_DEG, 0, 0])
    neck = neck.translate(list(C.MOUTH_CANNON_T))
    return neck


def _mouth_wire_bore() -> Manifold:
    """マウスソケット軸に沿った配線ボア (Head_Bottom 自身のローカル座標)。

    config.MOUTH_SOCKET_LOCAL/OUTWARD_LOCAL の軸上、深さ
    [MOUTH_HEAD_BORE_START, MOUTH_HEAD_BORE_END] (mm, inward=正) を
    貫く単純な円柱カッター。値の根拠・実測手順は config.py のコメント参照。
    """
    axis = np.asarray(C.MOUTH_SOCKET_OUTWARD_LOCAL, dtype=float)
    axis /= np.linalg.norm(axis)
    c = np.asarray(C.MOUTH_SOCKET_LOCAL, dtype=float)
    length = C.MOUTH_HEAD_BORE_END - C.MOUTH_HEAD_BORE_START
    mid_depth = (C.MOUTH_HEAD_BORE_START + C.MOUTH_HEAD_BORE_END) / 2.0
    center_local = c - axis * mid_depth  # inward = -axis; depth d -> c + (-axis)*d

    tm = trimesh.creation.cylinder(radius=C.MOUTH_HEAD_BORE_D / 2, height=length,
                                    sections=64)
    R = trimesh.geometry.align_vectors([0.0, 0.0, 1.0], axis)
    T = np.eye(4)
    T[:3, 3] = center_local
    tm.apply_transform(T @ R)
    return _to_manifold(tm)


# ---- 機構逃がしカット (2026-08-20, モジュール docstring 末尾の節を参照) ----
HEADBOT_PLATE_CLR = 0.5    # プレート下面とボウル上端リングの接着ギャップ
MECH_CLR = 2.5             # バッテリー/クレードル周りの実体クリアランス
# 2S 2200mAh パック実体 (make_chassis.battery_cradle docstring / BOM #5)。
# クレードル内腔 z=[-29,-4] の床置きで z=[-29,-5]、中心 y=-6 はクレードルと同じ
BATT_PACK = (34.0, 105.0, 24.0)      # X × Y × Z (chassis 座標)
BATT_PACK_C = (0.0, -6.0, -17.0)
BATT_CORRIDOR_Y = -75.0    # -Y 挿入経路の終端 (皿後端 y=-51.2 の外まで開放)
MIRROR_LEGS = ("FR", "RL")  # ミラー版 _m を使う脚 (tools/check_leg_assembly.py
                            # MIRROR_LEGS が唯一の正 — 複写、変更時は要追従)


def _leg_servo_case(mirror: bool) -> Manifold:
    """STD サーボ実体の箱枠ローカル近似 (tools/check_leg_assembly.py
    servo_case_mesh と同式の Manifold 版 — 同ファイルが唯一の正、複写)。"""
    P = C.LEG_SERVO
    cx = P["L"] / 2 - P["SHAFT_OFF"]
    s = -1.0 if mirror else 1.0
    case = Manifold.cube([P["L"], P["TAB_BELOW"] + P["ABOVE_TAB"], P["W"]], True) \
        .translate([-cx, s * (P["ABOVE_TAB"] - P["TAB_BELOW"]) / 2, 0])
    hub = Manifold.cylinder(P["HORN_HUB_H"] + 1, P["HORN_HUB_D"] / 2, -1.0, 0, True) \
        .rotate([90, 0, 0]).translate([0, s * (P["ABOVE_TAB"] + P["HORN_HUB_H"] / 2), 0])
    return case + hub


def _coxa_chassis(name: str, leg_yaw_deg: float) -> Manifold:
    """coxa_bracket + 股ピッチサーボを指定脚・指定ヨーで chassis 座標へ配置
    (tools/check_leg_assembly.py leg_at()/leg_world() と同じ組成・配置式。
    脚ローカル z=0 = 股ヨー軸面 = chassis z -HIP_DROP)。"""
    mirror = name in MIRROR_LEGS
    br = make_leg.coxa_bracket()
    if mirror:
        br = br.mirror([0, 1, 0])
    coxa = br + _leg_servo_case(mirror).translate([C.COXA_LEN, 0, 0])
    hx, hy = C.HIPS[name]
    return coxa.rotate([0, 0, C.LEG_ANGLES[name] + leg_yaw_deg]) \
        .translate([hx, hy, -C.HIP_DROP])


def _mech_static_cutters_chassis() -> list:
    """姿勢に依らない機構逃がしカッター群 (chassis 座標): C1 プレート上帯 +
    C2 クレードル外形 / バッテリーパック+挿入経路。"""
    cutters = []
    # C1: プレート下面 (z=0) より上を全カット (クリアランス分だけ下げる)
    cutters.append(Manifold.cube([300, 300, 60], True)
                   .translate([0, 0, 30 - HEADBOT_PLATE_CLR]))
    # C2a: battery_cradle 実メッシュの外接直方体 + MECH_CLR (上方は C1 帯へ続ける)
    bb = make_chassis.battery_cradle().bounding_box()
    cutters.append(Manifold.cube(
        [bb[3] - bb[0] + 2 * MECH_CLR, bb[4] - bb[1] + 2 * MECH_CLR,
         (2 - (bb[2] - MECH_CLR))], True)
        .translate([(bb[0] + bb[3]) / 2, (bb[1] + bb[4]) / 2,
                    (2 + (bb[2] - MECH_CLR)) / 2]))
    # C2b: パック実体 + -Y 挿入経路 (docs/assembly.md「-Y 側から差し込み」)
    px, py, pz = BATT_PACK
    cx_, cy_, cz_ = BATT_PACK_C
    y_hi = cy_ + py / 2 + MECH_CLR
    z_lo, z_hi = cz_ - pz / 2 - MECH_CLR, cz_ + pz / 2 + MECH_CLR
    cutters.append(Manifold.cube([px + 2 * MECH_CLR, y_hi - BATT_CORRIDOR_Y,
                                  z_hi - z_lo], True)
                   .translate([cx_, (y_hi + BATT_CORRIDOR_Y) / 2, (z_hi + z_lo) / 2]))
    return cutters


# ---- リム薄片/カスプ処理 (2026-08-20b, ユーザー指摘「細かすぎて壊れやすい
# 造形は削る」タスク) ----
# C1 平面カットが腕/マウスソケットの円形開口と交差する場所に、平面視で
# 先細りになる羽根状カスプ (肉厚はあっても平面幅が先端で 0 に漸近 = 印刷/
# 取り扱いで折れる) が残る。対処は 2 系統:
# (1) 実測カスプ箱 RIM_CUSP_BOXES_LOCAL: カスプ 1 個ずつの外接直方体+マージン。
#     **棄却した 2 つの汎用案 (2026-08-20b 同日, 再発防止のため記録)**:
#     (a) 腕カッター箱を xy+2mm 拡張して全高 U 字ノッチ化 + マウスカスプ帯の
#         水平トリム箱 → 箱がマウスソケット側壁まで巻き込み受け座を全壊
#         (前壁域 7.3→0cm3)。「交差=0」系の検算は全て素通りした。
#     (b) リム帯の水平断面 (Manifold.slice) を 2D オフセット開演算にかけ
#         平面幅 2.4mm 未満の領域を柱状に剪定 → マウスソケットのカラー壁は
#         軸が前下がりに傾いており、水平断面では至る所で幅 2.4mm 未満に
#         見えるため層ごと剪定され受け座を再破壊 (リング 4/24 点)。さらに
#         レベル間の段差が新たな薄片 4 件 + 非 watertight を生んだ。
#     教訓: 「無いことの検査 (交差=0)」だけでは切りすぎを検出できない —
#     受け座リング検算 (__main__) が「あるべき材の実在」を必ず検査する。
# (2) 実測バリ取り箱 DEBURR_BOXES_LOCAL: ボクセル開演算スキャン
#     (scratchpad/fragility_scan.py, pitch0.35-0.5) で実測した薄片クラスタ
#     (<1.6mm 厚, いずれも内部/下面で不可視) の外接直方体+1mm。
# 座標は Head_Bottom ローカル。
# 導出 (2026-08-20b): 基準形状 (カスプ箱なし) のリム帯スライス −2.9..+2.4 を
# 2D オフセット開演算 (幅 2.4mm) にかけて平面視スリバーを抽出し、そのうち
# **自由突起 (先端が周囲より上に孤立して尖る) だけ** を外接 xy 矩形 +0.5mm、
# z=[最浅出現レベル−0.8, +3.0] の箱で切る。
# 1-4: 腕ソケットのツノ (ユーザー指摘の画像の正体。1/2 は再測で先端方向
#      x±48 へ拡張) / 5-6: マウスソケット脇のスリバー (5 は全高で <2.4mm 幅
#      — 受け座としては元々当てにできない側翼)。
# **含めないもの (2026-08-20b 実測で確定)**: バッテリー窓/クレードルカット
# 縁のレッジ類。2D レベル走査は「下で支えられているクサビ」も薄片と誤判定
# する (箱を広げるとスリバー判定が外周へ逃げ続け、最終的にリムブリッジの
# 両肩を切断して 897mm3 のブリッジ島を作った — 棄却)。支持を考慮できる
# 3D ボクセル開演算検査 (__main__ の薄片検算) はこれらを問題視していない。
RIM_CUSP_BOXES_LOCAL = [
    ((-48.0, -41.1, -0.9), (-39.8, -26.3, 3.0)),
    ((39.8, -41.1, -0.9), (48.0, -26.3, 3.0)),
    ((19.1, -51.5, -0.9), (33.0, -44.0, 3.0)),
    ((-33.1, -51.5, -0.9), (-19.1, -44.0, 3.0)),
    ((10.5, -57.1, -3.7), (15.2, -42.5, 3.0)),
    ((-14.7, -57.9, -0.2), (-10.2, -49.5, 3.0)),
]
DEBURR_BOXES_LOCAL = [
    # マウスソケット喉内の 2mm フィン×2 (配線ボアと Neck 逃がしの間の残材)
    ((-9.8, -40.6, -10.2), (0.6, -36.6, 1.4)),
    ((3.0, -40.6, -10.2), (10.9, -36.6, 1.4)),
    # マウスソケットリム角のチップ (x-13 側)
    ((-15.0, -52.5, -7.0), (-11.0, -43.4, 1.0)),
    # バッテリー窓前縁の 0.9mm ウェッジ×2 (窓カットとドーム斜面の鋭角交差)
    ((-25.3, -21.8, -13.7), (-17.7, -4.1, 1.5)),
    ((18.2, -21.8, -13.7), (25.8, -4.1, 1.5)),
    # ドーム内側の 1mm 刃 (コリドーカット残材)
    ((-34.9, 14.7, -12.1), (-22.8, 17.7, -5.0)),
]


def _coxa_notch_boxes(hb_chassis: Manifold) -> list:
    """各脚の coxa をヨー全域スイープし、hb_chassis との残交差の外接直方体
    (+ARMCUT_MARGIN_MM) を返す (_armcut_box_right と同じ流儀)。"""
    yaws = np.linspace(-LEG_YAW_LIM - ARMCUT_YAW_MARGIN_DEG,
                       LEG_YAW_LIM + ARMCUT_YAW_MARGIN_DEG, 13)
    boxes = []
    for name in C.LEG_ANGLES:
        lo = np.array([np.inf] * 3)
        hi = np.array([-np.inf] * 3)
        found = False
        for yw in yaws:
            inter = _coxa_chassis(name, yw) ^ hb_chassis
            if inter.volume() > 1e-3:
                found = True
                b = inter.bounding_box()
                lo = np.minimum(lo, np.array(b[:3]))
                hi = np.maximum(hi, np.array(b[3:]))
        if found:
            lo -= ARMCUT_MARGIN_MM
            hi += ARMCUT_MARGIN_MM
            boxes.append((name, hi - lo, (hi + lo) / 2.0))
    return boxes


def head_bottom_armcut() -> Manifold:
    """Head_Bottom_Blue の左右腕ソケット拡口 + マウス配線受け穴を焼き込んだ加工版。
    """
    hb = _load_kit_150("Head_Bottom_Blue")
    t, rz = HEAD_BOTTOM_T
    hb_chassis = hb.rotate([0, 0, rz]).translate(list(t))

    mx, my = C.ARM_MOUNT_XY
    cutters_chassis = []
    right = _armcut_box_right(hb_chassis, mx, my)
    if right is not None:
        size, center = right
        cutters_chassis.append(Manifold.cube(list(size), True).translate(list(center)))
        # 左ソケット: 右側カッターを X ミラー (Head_Bottom 自体が概ね X=0 面
        # 対称である前提。__main__ の検算では左腕ブラケットの実メッシュ
        # [mirror([0,1,0]) 版] を右配置の X ミラーとして置き、この前提ごと
        # 裏取りする)
        mcenter = [-center[0], center[1], center[2]]
        cutters_chassis.append(Manifold.cube(list(size), True).translate(mcenter))

    # chassis-local → Head_Bottom 自身のローカル座標系へ変換して減算
    # (180°回転は自己逆変換、並進は符号反転してから逆順に適用)
    hb_out = hb
    for cutter in cutters_chassis:
        cutter_local = cutter.translate([-t[0], -t[1], -t[2]]).rotate([0, 0, -rz])
        hb_out = hb_out - cutter_local

    # マウス配線受け穴 (Head_Bottom 自身のローカル座標系, 変換不要)
    hb_out = hb_out - _mouth_wire_bore()

    # マウスソケット内縁の逃がし (2026-07-31, 任務2: chassis frame → Head_Bottom
    # ローカルへ変換してから減算, 上記腕カッターと同じ変換式)
    relief_chassis = _mouth_socket_relief_chassis()
    relief_local = relief_chassis.translate([-t[0], -t[1], -t[2]]).rotate([0, 0, -rz])
    hb_out = hb_out - relief_local

    # 機構逃がしカット (2026-08-20): C1/C2 (静的) → C3 coxa notch の順。
    # C3 は C1/C2 適用後の残交差から計算する (最小の notch になる)
    def _to_local(m: Manifold) -> Manifold:
        return m.translate([-t[0], -t[1], -t[2]]).rotate([0, 0, -rz])

    for cutter in _mech_static_cutters_chassis():
        hb_out = hb_out - _to_local(cutter)
    hb_cur_chassis = hb_out.rotate([0, 0, rz]).translate(list(t))
    for _name, size, center in _coxa_notch_boxes(hb_cur_chassis):
        hb_out = hb_out - _to_local(
            Manifold.cube(list(size), True).translate(list(center)))

    # 実測薄片のバリ取り + 実測カスプ箱 (2026-08-20b, ローカル座標。定数定義の
    # 節コメント参照 — いずれも内部/下面/リム帯で不可視級)
    for lo, hi in DEBURR_BOXES_LOCAL + RIM_CUSP_BOXES_LOCAL:
        lo = np.asarray(lo, float)
        hi = np.asarray(hi, float)
        hb_out = hb_out - Manifold.cube(list(hi - lo), True).translate(
            list((lo + hi) / 2))
    hb_out = hb_out.simplify(0.01)   # STL 往復の watertight 保全 (#28 と同じ流儀)

    # カット残渣の浮きスライバー除去 (eye_pod_camera_base と同じ流儀):
    # 最大成分のみ残す。落とす体積が大きい場合は設計エラーとして止める。
    # 許容 250mm3 の根拠 (2026-08-20b 実測): バッテリー窓前縁ウェッジの
    # バリ取りで、0.9mm ウェッジだけで本体に繋がっていた底面ドームの島
    # (97mm3 ×2, 下面で不可視) が意図どおり一緒に分離する + カスプ先端の
    # 欠片 (<2mm3 数個)。これを超える分離は意図しない切断とみなして止める
    parts = hb_out.decompose()
    parts = sorted(parts, key=lambda p: p.volume(), reverse=True)
    dropped = sum(p.volume() for p in parts[1:])
    assert dropped < 250.0, f"機構逃がしカットで大きな分離片: {dropped:.1f}mm3"
    return parts[0]


if __name__ == "__main__":
    print("[head] Head_Bottom_Armcut (腕ソケット拡口 + マウス配線受け穴)")
    m = export(head_bottom_armcut(), "Head_Bottom_Armcut")

    # 検算: カット後、肩ヨー全域で shoulder_bracket と交差 0 か。
    # 右腕は実配置で直接検証。左腕は「Head_Bottom は X=0 面対称」という
    # head_bottom_armcut() 自体の前提に忠実に、右腕の配置済みメッシュを
    # そのまま X ミラーしたもので検証する (左腕独自の取付角規約を別途
    # 仮定しない — 前提が崩れていれば worst>0 として検出される)
    hb_check = _to_manifold(m)
    t, rz = HEAD_BOTTOM_T
    hb_chassis = hb_check.rotate([0, 0, rz]).translate(list(t))
    mx, my = C.ARM_MOUNT_XY
    worst = 0.0
    for arm_yaw in np.linspace(-ARM_YAW_LIM, ARM_YAW_LIM, 13):
        br_w = _bracket_world(arm_yaw, mx, my)
        worst = max(worst, (br_w ^ hb_chassis).volume())
        br_w_left = br_w.mirror([1, 0, 0])
        worst = max(worst, (br_w_left ^ hb_chassis).volume())
    print(f"  検算: 肩ヨー全域±{ARM_YAW_LIM:.0f}°(右+Xミラー左) での "
          f"shoulder_bracket 交差体積 worst={worst:.3f}mm3 "
          f"({'OK' if worst < 0.5 else 'NG'})")

    # 検算: マウス配線受け穴が Ball 裏面から頭部内部キャビティまで実際に
    # 連通しているか (カット前は深さ13.25-18.25mmが中実だった区間が、カット後は
    # 全域で開通しているはず)。Head_Bottom 自身のローカル座標系で直接検査
    # (回転・並進不要)
    hb_tm = _to_manifold(m)
    axis = np.asarray(C.MOUTH_SOCKET_OUTWARD_LOCAL, dtype=float)
    axis /= np.linalg.norm(axis)
    c = np.asarray(C.MOUTH_SOCKET_LOCAL, dtype=float)
    hb_local_tm = trimesh.Trimesh(
        vertices=np.asarray(hb_tm.to_mesh().vert_properties)[:, :3],
        faces=np.asarray(hb_tm.to_mesh().tri_verts), process=False)
    depths = np.linspace(C.MOUTH_HEAD_BORE_START + 0.5, C.MOUTH_HEAD_BORE_END - 0.5, 20)
    probe_pts = np.array([c - axis * d for d in depths])
    blocked = hb_local_tm.contains(probe_pts)
    print(f"  検算: マウス配線ボア軸上 (深さ{depths[0]:.1f}-{depths[-1]:.1f}mm, "
          f"{len(depths)}点) の中実点数={int(blocked.sum())} "
          f"({'OK' if not blocked.any() else 'NG'})")

    # 検算 (2026-08-20 機構逃がし): chassis 座標で全メカ相手との交差 0 か。
    # 配置は make_visuals kit_dress_static と同一 (HEAD_BOTTOM_T)。
    hb_chassis2 = _to_manifold(m).rotate([0, 0, rz]).translate(list(t))
    px, py, pz = BATT_PACK
    batt = Manifold.cube([px, py, pz], True).translate(list(BATT_PACK_C))
    head_top = _load_kit_150("Head_Top_Blue").rotate([0, 0, 180]).translate(
        [0, C.ARM_MOUNT_HUB_Y, 57.7])   # 57.7 = make_visuals HEAD_TOP_Z_OFFSET
                                        # (同ファイルが唯一の正 — 複写、要追従)
    ok_all = True
    for nm, other in [("chassis", make_chassis.chassis()),
                      ("battery_cradle", make_chassis.battery_cradle()),
                      ("battery_pack", batt),
                      ("pod_neck", make_chassis.pod_neck().translate([0, 0, C.CHASSIS_T])),
                      ("Head_Top(+57.7)", head_top)]:
        v = (hb_chassis2 ^ other).volume()
        ok = v < 50.0
        ok_all &= ok
        print(f"  検算: 皿 ∩ {nm:15s} = {v/1000:7.3f} cm3 ({'OK' if ok else 'NG'})")
    for name in C.LEG_ANGLES:
        worst2 = 0.0
        for yw in np.linspace(-LEG_YAW_LIM - ARMCUT_YAW_MARGIN_DEG,
                              LEG_YAW_LIM + ARMCUT_YAW_MARGIN_DEG, 13):
            worst2 = max(worst2, (_coxa_chassis(name, yw) ^ hb_chassis2).volume())
        ok = worst2 < 50.0
        ok_all &= ok
        print(f"  検算: 皿 ∩ coxa[{name}] ヨー±{LEG_YAW_LIM:.0f}°+{ARMCUT_YAW_MARGIN_DEG:.0f}° "
              f"worst = {worst2/1000:7.3f} cm3 ({'OK' if ok else 'NG'})")
    print(f"  検算: 機構逃がし総合 {'OK' if ok_all else 'NG'} "
          f"(残体積 {_to_manifold(m).volume()/1000:.1f} cm3)")

    # 検算 (2026-08-20b 薄片): ボクセル開演算で「突起級の薄片」が残っていないか。
    # eye_pod v3 の教訓 (穴掘り後の残りに孤立薄片ができていないか機械チェック
    # する) の恒常化。突起級 = 体積>=8mm3 かつ 最大寸法<28mm かつ 2軸以上が
    # 2.5mm 以上 (直角エッジの1ボクセル皮・リング面スキン・長尺モールド線は
    # 除外)。しきい値 <1.4mm 厚相当 (pitch0.35, r=2)
    from scipy import ndimage as _ndi
    _vg = m.voxelized(0.35).fill()
    _vol = _vg.matrix
    _st = _ndi.iterate_structure(_ndi.generate_binary_structure(3, 1), 2)
    _thin = _vol & ~_ndi.binary_opening(_vol, structure=_st)
    _lab, _n = _ndi.label(_thin)
    _bad = []
    if _n:
        _sizes = _ndi.sum_labels(np.ones_like(_lab), _lab,
                                 np.arange(1, _n + 1))
        for _i, _sz in zip(np.arange(1, _n + 1), _sizes):
            _v3 = _sz * 0.35 ** 3
            if _v3 < 8.0:
                continue
            _w = np.argwhere(_lab == _i)
            _d = (_w.max(0) - _w.min(0) + 1) * 0.35
            if _d.max() < 28.0 and sorted(_d)[1] >= 2.5:
                _c = (_w.min(0) * 0.35 + _vg.translation + _d / 2)
                _bad.append((_v3, _d, _c))
    for _v3, _d, _c in _bad:
        print(f"    薄片: {_v3:.1f}mm3 dims {np.round(_d,1)} local_c {np.round(_c,1)}")
    print(f"  検算: 突起級薄片 {len(_bad)} 件 ({'OK' if not _bad else 'NG'})")

    # 検算 (2026-08-20b): マウスソケット周囲材の実在。Mouth_Ball の保持は
    # ソケット中心 r16 球域にある壁材 (ボア円筒面+周囲壁+接着) が担う —
    # その総量をしきい値以上要求する。初版のU字ノッチ拡張がここを全壊
    # (前壁域 7.3→0cm3) させたのに「交差=0」系の検算が全て素通りした反省の
    # 恒久化 — 「無いことの検査」だけでなく「あるべき材の実在」を必ず検査
    # する。しきい値 0.27 の根拠: 2026-08-20b 確定形状での実測 0.37cm3 の
    # -25% (ソケット周囲は元々薄壁ボアで、球域の大半はボア空洞/逃がし
    # キャビティ — 0.37 が良品の実態。U字ノッチ事故版はほぼ 0 になる)。
    # ※「中心より下のカップ」案 (0.09cm3) とリング点プローブ案 (常時 4/24)
    # は、ソケット軸が前下がりで保持材が中心の上後方に分布するため測定不良
    # — 棄却済み
    _c0 = np.asarray(C.MOUTH_SOCKET_LOCAL, float)
    _cup_v = (_to_manifold(m) ^ Manifold.sphere(16.0, 64).translate(
        list(_c0))).volume() / 1000.0
    print(f"  検算: マウスソケット周囲材 (r16球域) {_cup_v:.2f} cm3 "
          f"({'OK' if _cup_v >= 0.27 else 'NG'}, 要 >=0.27)")
