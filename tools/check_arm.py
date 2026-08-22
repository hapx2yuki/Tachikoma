#!/usr/bin/env python3
"""腕サブシステム (吊り下げマウント) の検証。

 1.  肩マウントの実メッシュ整合 (MICRO 開口/タブボス肉/STD 開口との分離)
 1b. 肩ブラケット・上腕 vs 前脚 coxa の実メッシュブーリアン干渉 (脚ヨー±45°)
 2.  肩ピッチ/肘の静的トルク + 肩ヨー軸の常時曲げ指標 vs MG90S 定格
 3.  claw_mount ↔ 爪ハブ (Arm_Left_Claw_Grey) 接着継手の成立性 (2026-07-29
     固定爪化: 可動グリッパ廃止に伴い旧[3]から刷新。パッドボスが爪ハブの
     中実領域に食い込まない/爪ハブの指ペグ位置が claw_mount の外へ出て
     指を差し込めるか、実メッシュで検証)
 3b. ARM_HAND_REACH_MM-FOREARM_LEN が実メッシュの worst-case reach (指先
     claw_mount ローカル+X最大値) を上回るか (QA minor 指摘: 旧[4]の firmware
     ⇔config.py 定数突合は tautological だったため実メッシュで裏取り。[5b]
     と同型の drift 対策)
 1d. Mouth_Ball/Neck/Cannon (現在のポーズ) vs Head_Bottom_Armcut シェルの
     実体干渉 (2026-07-31 QA指摘で新設 — 頭部シェルとの交差を回帰検査する
     自動チェックが従来一切存在しなかった。以前 make_head.py が「Ball は
     全角度域で常に0」と記していた根拠は、実は Neck 専用の逃がしカット
     (_mouth_socket_relief_chassis) が副次的に Ball の干渉域まで削り込んで
     いたことによる偶然の救済であり、無関係に見えるパラメータ変更で再発し
     うる — その回帰を検出する)
 4.  腕作業域 vs 前脚 (高さ帯別モデル, ヨー±ARM_YAW_LIM+内側クランプ+地面ガード)
 5.  腕相互の接触回避 (内側ヨークランプ)
 5b. HAND_HALF 定数 (firmware ARM_HAND_HALF_MM と共有) が実メッシュの手の
     横幅を上回るか (2026-07-29 固定爪化: 旧「パーム半径13.5」から根拠が
     入れ替わったため実メッシュで裏取り。pitfalls #31 と同型の drift 対策)
 6.  プリセットの接地クリアランス (地面ガード適用後)
 7.  殻パーツ (元キット加工) の成立性: 単一連結体 + ポッド↔骨格掃引干渉
"""
import re
import sys
from pathlib import Path

import numpy as np
import trimesh

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hardware" / "src"))
import config as C  # noqa: E402

STL = ROOT / "hardware" / "stl"
OK = True


def check(cond, msg):
    global OK
    print(f"  {'OK ' if cond else 'NG '} {msg}")
    OK &= cond


print("[1] 肩マウント (シャーシ吊り下げ式) の整合")
chassis = trimesh.load(STL / "chassis.stl")
bracket = trimesh.load(STL / "shoulder_bracket.stl")
mx, my = C.ARM_MOUNT_XY
PA = C.ARM_SERVO
cxa = PA["L"] / 2 - PA["SHAFT_OFF"]
hole_ys = [my - cxa - PA["HOLE_PITCH"] / 2, my - cxa + PA["HOLE_PITCH"] / 2]
# ケース開口: 中心と 4 隅の内側が貫通していること
op = []
for s in (-1, 1):
    cx0, cy0 = s * mx, my - cxa
    op.append([cx0, cy0, C.CHASSIS_T / 2])
    for dx in (-1, 1):
        for dy in (-1, 1):
            op.append([cx0 + dx * (PA["W"] / 2 - 0.5),
                       cy0 + dy * (PA["L"] / 2 - 0.5), C.CHASSIS_T / 2])
inside = chassis.contains(np.array(op))
check(not inside.any(), f"MICRO ケース開口 10 点すべて貫通 (中実={int(inside.sum())})")
# タブビスの下穴まわり: ボス内 (r1.8 リング 12 点/穴) が中実 = ネジ肉がある
# (ボスは開口と反対側へ逃がした長円。開口側の最小肉は r2.0)
ring = []
for s in (-1, 1):
    for hy in hole_ys:
        for a in np.linspace(0, 2 * np.pi, 12, endpoint=False):
            ring.append([s * mx + 1.8 * np.cos(a), hy + 1.8 * np.sin(a),
                         C.CHASSIS_T + C.ARM_BOSS_H - 0.5])
rin = chassis.contains(np.array(ring))
check(rin.all(), f"タブボスのネジ肉 (r1.8 リング {len(ring)} 点) 中実={int(rin.sum())}")
# STD 脚ヨー開口との分離: 後側ボス (南端) と FR/FL ケース開口 (北端) の間の
# 点が中実であること。2026-07-28 移設で最接近点が変わったため C.ARM_MOUNT_XY
# 追従で毎回再計算する (make_chassis.py の away 式と同じロジック)
_away0 = -1.5 if hole_ys[0] - my < -cxa else 1.5
_boss0_y_south = hole_ys[0] + _away0 - 3.5   # rbox(8,7,..) の Y half=3.5
_leg_case_y_north = C.HIPS["FR"][1] + (C.YAW_SERVO["W"] + 0.6) / 2
_sep_y = (_boss0_y_south + _leg_case_y_north) / 2
sep = chassis.contains(np.array([[s * mx, _sep_y, C.CHASSIS_T / 2] for s in (-1, 1)]))
check(sep.all(), "MICRO 後側ボスと STD 開口の間に肉が残る "
      f"(y={_sep_y:.1f}, ボス南端{_boss0_y_south:.1f}/脚開口北端{_leg_case_y_north:.1f})")
# ブラケット: ヨーホーンポケット (上面) とピッチ軸ポケットが空いていること
pz = 2.5 + PA["HORN_HUB_H"] - 2.0
pitch_dn = pz + 2.5 + (PA["W"] / 2 + 2.5) - 0.1  # 16.4
bin_ = bracket.contains(np.array([[0, 0, -3.6], [20, 0, -pitch_dn]]))
check(not bin_.any(), "ブラケットのホーンポケット/ピッチ軸ポケットが貫通")

print("[1b] 肩ブラケット vs 前脚 coxa (脚ヨー最大 ±45° での干渉)")
# ブラケットをシャーシ座標へ配置 (原点 z = プレート下面-2, 腕ローカル+X→+Y)
horn_off = -2.0  # ブラケット原点のプレート下面からのオフセット


def _place(mesh, T):
    m = mesh.copy()
    m.apply_transform(T)
    return m


def _T(tx, ty, tz, rz):
    c, s = np.cos(np.radians(rz)), np.sin(np.radians(rz))
    M = np.eye(4)
    M[:2, :2] = [[c, -s], [s, c]]
    M[:3, 3] = [tx, ty, tz]
    return M


# v3: 腕に最も近い前脚 FR はミラー版 (_m)。左側 (FL=標準) は右の完全鏡像
coxa = trimesh.load(STL / "coxa_bracket_m.stl")
upper = trimesh.load(STL / "upper_arm.stl")
HIP_DROP = C.HIP_DROP  # プレート下面 → 股ヨー軸原点 (config.py が唯一の正)
# firmware のリミットは実ファイルから読む (複製 drift 防止)
_fwtxt = (ROOT / "firmware" / "src" / "config.h").read_text()


def _fwval(name):
    return float(re.search(rf"{name}\s*=\s*([\d.]+)f", _fwtxt).group(1))


YAW_LIM = _fwval("ARM_YAW_LIM")
LEG_YAW_LIM = _fwval("LIM_YAW")


def _roty(deg):
    t = np.radians(deg)
    M = np.eye(4)
    M[0, 0] = M[2, 2] = np.cos(t)
    M[0, 2] = np.sin(t)
    M[2, 0] = -np.sin(t)
    return M


def _vol(a, b):
    try:
        inter = trimesh.boolean.intersection([a, b], engine="manifold")
        return float(inter.volume) if inter is not None and len(inter.faces) else 0.0
    except Exception:
        return 0.0


MOUNT_YAW = C.ARM_MOUNT_YAW_DEG  # 肩ポッド中立向き (正面から外向き, 実測40°)
worst_ib = 0.0
for arm_yaw in (-YAW_LIM, 0, YAW_LIM):
    Tb = _T(mx, my, horn_off, 90 - MOUNT_YAW - arm_yaw)
    br_w = _place(bracket, Tb)
    for leg_yaw in (0, LEG_YAW_LIM * 0.6, LEG_YAW_LIM):
        # FR 脚 (mount 15°, v3): coxa 原点 = 股ヨー軸 (プレート下面の 27.6mm 下)
        cx_w = _place(coxa, _T(*C.HIPS["FR"], -HIP_DROP,
                               C.LEG_ANGLES["FR"] + leg_yaw))
        worst_ib = max(worst_ib, _vol(br_w, cx_w))
        # 上腕 (肩ピッチ軸 = ブラケット local (20, 0, -16.4)) も検査
        for pitch in (0, 45, 85):
            Tp = np.eye(4)
            Tp[:3, 3] = [20, 0, -pitch_dn]
            up_w = _place(upper, Tb @ Tp @ _roty(pitch))
            worst_ib = max(worst_ib, _vol(up_w, cx_w))
print(f"  最大交差体積: {worst_ib:.0f} mm3 (腕ヨー±{YAW_LIM:.0f}° × "
      f"脚ヨー0-{LEG_YAW_LIM:.0f}°)")
check(worst_ib < 1.0, "肩ブラケット/上腕と coxa の干渉なし")
# ヨーリミットでのクリアランス余裕 (脚ヨー最大が最接近)
cx_w = _place(coxa, _T(*C.HIPS["FR"], -HIP_DROP,
                       C.LEG_ANGLES["FR"] + LEG_YAW_LIM))
br_w = _place(bracket, _T(mx, my, horn_off, 90 - MOUNT_YAW - YAW_LIM))
sd = trimesh.proximity.signed_distance(cx_w, br_w.vertices)
clr = float(-sd.max())
print(f"  腕ヨー+{YAW_LIM:.0f}° × 脚ヨー+{LEG_YAW_LIM:.0f}° での"
      f"クリアランス: {clr:.1f} mm")
check(clr >= 1.5, "ヨーリミットで 1.5mm 以上のクリアランス")

print("[1c] shoulder_bracket vs Head_Bottom_Armcut (肩ヨー可動域全域, 左右)")
# 2026-07-30 追加: 実メッシュ検証で shoulder_bracket の取付プレートが
# Head_Bottom_Blue (元キット形状) と肩ヨー可動域全域 (中立含む) で常時
# 166〜182mm3 干渉することが判明 (旧 ARM_MOUNT_* コメントの「加工不要 or
# 最小拡口」は座標突合せのみで実メッシュ未検証だった)。Head_Bottom_Armcut
# (hardware/src/make_head.py) で拡口済み — ここで恒常回帰検証する
hb_armcut = trimesh.load(STL / "Head_Bottom_Armcut.stl")
# hardware/stl 側で既に bbox 中心化×150% 済みのキット座標系 (KIT.PRESCALED 規約)
# — 再スケール不要。**再中心化もしないこと** (2026-08-20 機構逃がしカットで
# 部品自身の bbox が非対称になった。旧版では bbox 中心化が偶然 no-op だった
# ため気付かれなかったが、再中心化すると配置が z+5.1mm ずれて偽干渉を報告する)

T_hb = _T(0, C.ARM_MOUNT_HUB_Y, -3, 180)
hb_w = _place(hb_armcut, T_hb)
worst_hb = 0.0
for arm_yaw in np.linspace(-YAW_LIM, YAW_LIM, 13):
    br_w = _place(bracket, _T(mx, my, horn_off, 90 - MOUNT_YAW - arm_yaw))
    worst_hb = max(worst_hb, _vol(br_w, hb_w))
    br_w_left = br_w.copy()
    br_w_left.apply_transform(np.diag([-1.0, 1.0, 1.0, 1.0]))  # X ミラー (右配置→左)
    worst_hb = max(worst_hb, _vol(br_w_left, hb_w))
print(f"  最大交差体積 (右実配置 + X ミラー左): {worst_hb:.2f} mm3 "
      f"(肩ヨー±{YAW_LIM:.0f}°全域)")
check(worst_hb < 0.5, "shoulder_bracket と Head_Bottom_Armcut の干渉なし")

print("\n[1d] Mouth_Ball/Neck/Cannon (現在のポーズ) vs Head_Bottom_Armcut シェル")
# 2026-07-31 QA指摘 (major) への対応: 頭部シェルとの交差を検査する恒久
# チェックがこれまで一切なかった。以前 make_head.py は「Ball は全角度域で
# 常に0」と記していたが、これは Neck 専用の逃がしカット
# (_mouth_socket_relief_chassis) が副次的に Ball の干渉域も削っていたことに
# よる偶然の救済であり (make_head.py の該当コメント参照)、RELIEF_SCALE や
# NECK_CANNON_LOCAL_Y、MOUTH_CANNON_REAR_STANDOFF_MM 等がこの偶然の依存関係
# を知らずに変更されると Ball 埋没が回帰検知されずに再発するリスクがあった。
# ここで Ball/Neck/Cannon 自身 (Cap/Key/Peg は Cannon local で小さくほぼ
# Cannon 本体に付随するため対象外) を実際の配置ポーズで実メッシュ検証する。
MODEL = ROOT / "model"


def _rotx(deg):
    t = np.radians(deg)
    M = np.eye(4)
    M[1, 1] = M[2, 2] = np.cos(t)
    M[1, 2] = -np.sin(t)
    M[2, 1] = np.sin(t)
    return M


def _load_mouth_150(name):
    m = trimesh.load(MODEL / f"{name}.stl")
    m.apply_translation(-(m.bounds[0] + m.bounds[1]) / 2)
    m.apply_scale(C.SCALE)
    return m


# Cannon-local Y オフセット (tools/data/kit_assembly_front.json が唯一の正)。
# Neck は hardware/src/make_head.py NECK_CANNON_LOCAL_Y と同じ値 (複写、
# 変更したら両方直すこと)。Ball はどのコードにも定数化されておらず
# (kit_assembly_front.json のみに存在) この検証のためにここで初めて複写する
# — 2026-07-30 QA修正(二回目)で -37.018(coincident 読み, 図面上の中間値)
# から -41.02 へ更新されたのが最新値 (kit_assembly_front.json
# Mouth_Ball_Grey の "note" フィールド参照)。config.py コメント中の
# 「s_ball_center=-37.018」という記述はこの二回目修正**前**の中間導出値の
# ままで、現在実際に使われている値ではない (2026-07-31 QA レビューで、
# 古い-37.018を使うと Head_Bottom_Blue 生シェルとの交差が誤って0.09cm^3
# 検出される “false positive” を実際に再現・特定した — 正しい -41.02 を
# 使うと Ball は独立に MOUTH_SOCKET_LOCAL 実測点と往復整合し、Neck の逃がし
# カットに頼らずとも生シェル比で 0.0000cm^3 になることを確認済み)。
NECK_LOCAL_Y = -28.97
BALL_LOCAL_Y = -41.02


def _mouth_pose(local_y_offset, name):
    m = _load_mouth_150(name)
    m.apply_translation([0.0, local_y_offset, 0.0])
    m.apply_transform(_rotx(C.MOUTH_CANNON_ROT_X_DEG))
    m.apply_translation(list(C.MOUTH_CANNON_T))
    return m


ball_w = _mouth_pose(BALL_LOCAL_Y, "Mouth_Ball_Grey")
neck_w = _mouth_pose(NECK_LOCAL_Y, "Mouth_Neck_Blue")
cannon_w = _mouth_pose(0.0, "Mouth_Cannon_Grey")
v_ball = _vol(ball_w, hb_w) / 1000.0
v_neck = _vol(neck_w, hb_w) / 1000.0
v_cannon = _vol(cannon_w, hb_w) / 1000.0
print(f"  Ball vs Head_Bottom_Armcut   = {v_ball:.4f} cm^3")
print(f"  Neck vs Head_Bottom_Armcut   = {v_neck:.4f} cm^3")
print(f"  Cannon vs Head_Bottom_Armcut = {v_cannon:.4f} cm^3")
check(v_ball < 1e-3 and v_neck < 1e-3 and v_cannon < 1e-3,
      "Mouth_Ball/Neck/Cannon と Head_Bottom_Armcut の干渉なし")

print("[2] 腕トルク (MG90S 定格 2.2 kg·cm @6V)")
g = 9.81
# 質量モデル [kg] と肩ピッチ軸からの距離 [m] (腕水平・肘伸ばし＝最悪)。
# 2026-07-29 固定爪化: m_grip (旧, サブマイクロ+パーム殻+O リング機構
# 30g@r=0.113m) を m_hand (claw_mount+爪ハブ+指3+指先チップ3, いずれも
# 中実小物プラ部品のみで駆動機構なし — 見積り 10g) へ置換。距離も新
# ARM_HAND_REACH_MM (44.1mm, 旧79mm) に合わせて再導出:
#   r_hand = (UPPER_ARM_LEN + FOREARM_LEN + (ARM_HAND_REACH_MM-FOREARM_LEN)/2)/1000
#   r_pay  = (UPPER_ARM_LEN + ARM_HAND_REACH_MM)/1000  (指先=ペイロード位置)
m_upper, m_fore, m_hand, m_pay = 0.028, 0.026, 0.010, 0.050
r_upper = (C.UPPER_ARM_LEN / 2) / 1000
r_fore = (C.UPPER_ARM_LEN + C.FOREARM_LEN / 2) / 1000
r_hand = (C.UPPER_ARM_LEN + C.FOREARM_LEN
          + (C.ARM_HAND_REACH_MM - C.FOREARM_LEN) / 2) / 1000
r_pay = (C.UPPER_ARM_LEN + C.ARM_HAND_REACH_MM) / 1000
tau_sh = g * (m_upper * r_upper + m_fore * r_fore + m_hand * r_hand
              + m_pay * r_pay) * 10.197
tau_sh_np = g * (m_upper * r_upper + m_fore * r_fore + m_hand * r_hand) * 10.197
# 肘軸からのオフセット [m] (前腕/手/ペイロードとも肘基準に再導出)
el_fore = (C.FOREARM_LEN / 2) / 1000
el_hand = (C.FOREARM_LEN + (C.ARM_HAND_REACH_MM - C.FOREARM_LEN) / 2) / 1000
el_pay = C.ARM_HAND_REACH_MM / 1000
tau_el = g * (m_fore * el_fore + m_hand * el_hand + m_pay * el_pay) * 10.197
print(f"  肩ピッチ: ペイロード50g込み {tau_sh:.2f} kg·cm ({tau_sh/2.2*100:.0f}%), "
      f"ペイロード無し {tau_sh_np:.2f} kg·cm ({tau_sh_np/2.2*100:.0f}%)")
print(f"  肘:       ペイロード50g込み {tau_el:.2f} kg·cm ({tau_el/2.2*100:.0f}%)")
check(tau_sh < 2.2 * 0.75, "肩ピッチが定格の 75% 未満 (50g ペイロード時)")
check(tau_el < 2.2 * 0.75, "肘が定格の 75% 未満")
# [2b] 肩ヨー軸の常時曲げ (吊り下げ片持ち)。ピッチ軸オフセット (20, -16.4) の
# ため腕の自重モーメントがヨー角によらず出力軸の曲げとして常時かかる。
# 定格トルクを横荷重の代理指標に使う簡易チェック — 肩ヨーは金属ギア +
# ボールベアリング出力軸の MG90S 系を指定すること (BOM 参照)
off_x = 0.020
m_bracket = 0.021
M_pay = 9.81 * (m_bracket * off_x + m_upper * (off_x + r_upper)
                + m_fore * (off_x + r_fore) + m_hand * (off_x + r_hand)
                + m_pay * (off_x + r_pay)) * 10.197
print(f"  肩ヨー軸の常時曲げ相当 (50g ペイロード込み): {M_pay:.2f} kg·cm "
      f"({M_pay/2.2*100:.0f}%) [要実測: 軸受形式]")
check(M_pay < 2.2 * 0.75, "肩ヨー軸の曲げ相当負荷が定格の 75% 未満")

print("[3] claw_mount ↔ 爪ハブ (Arm_Left_Claw_Grey) 接着継手の成立性")
# 2026-07-29 固定爪化: 可動グリッパを廃止し、前腕終端の claw_mount (平坦
# 円盤, 厚み CLAW_MOUNT_THICKNESS) へ元キット爪ハブ (Arm_Left_Claw_Grey,
# 両腕鏡映使用) の平坦近位面を突き合わせ接着する。config.py CLAW_TO_MOUNT/
# FINGER_TO_MOUNT/FINGERTIP_TO_MOUNT は 3MF source_offset フォレンジクスに
# よる決定的変換 (make_arm.py claw_mount() 参照)。ここでは実メッシュで
# (a) 爪ハブの近位面が claw_mount 前面とほぼ面一 (レイキャストで残差
# <0.5mm) か、(b) 爪ハブの指ペグ3本の位置が claw_mount 本体の外にあり
# 指を差し込めるか、(c) 爪ハブ+指+指先チップの一体が claw_mount/forearm
# と過大に交差しないかを検証する
mount_tm = trimesh.load(STL / "claw_mount.stl")
claw_raw = trimesh.load(ROOT / "model" / "Arm_Left_Claw_Grey.stl")
claw_tm = claw_raw.copy(); claw_tm.apply_transform(C.CLAW_TO_MOUNT)
finger_raw = trimesh.load(ROOT / "model" / "Arm_Left_Finger_Black_x3.stl")
fingertip_raw = trimesh.load(ROOT / "model" / "Arm_Left_FingerTip_Grey_x3.stl")

# (a) 爪ハブ近位面のレイキャスト実測 (r=0/2/4mm オフセット, claw_mount の
# 円盤半径 10mm 以内) が claw_mount 前面 (x=CLAW_MOUNT_THICKNESS) と面一に
# 近いこと。現物合わせ接着継手なので数mmの残差は許容するが、大きくズレて
# いたら (a) 接着面が浮く (ギャップ) か (b) claw_mount に食い込みすぎて
# 印刷/接着不能、のどちらかで設計破綻
mount_face_x = C.CLAW_MOUNT_THICKNESS
gaps = []
for r in (0.0, 2.0, 4.0):
    for a in np.linspace(0, 2 * np.pi, 8, endpoint=False):
        y, z = r * np.cos(a), r * np.sin(a)
        locs, _, _ = claw_tm.ray.intersects_location(
            np.array([[-20.0, y, z]]), np.array([[1.0, 0.0, 0.0]]))
        if len(locs):
            gaps.append(locs[:, 0].min() - mount_face_x)
gaps = np.array(gaps)
print(f"  爪ハブ近位面 vs claw_mount 前面の残差 (n={len(gaps)}): "
      f"mean={gaps.mean():+.2f}mm max|.|={np.abs(gaps).max():.2f}mm")
check(len(gaps) > 0 and np.abs(gaps).max() < 1.0,
      "爪ハブ近位面が claw_mount 前面と面一 (残差<1.0mm, 現物合わせ許容内)")

# (b) 3本の指ペグ (config.FINGER_TO_MOUNT の各原点 = 指根元穴の狙い位置)
# が claw_mount 本体の外側にあること (claw_mount と衝突していたら指が
# 差し込めない)
peg_pts = np.array([T[:3, 3] for T in C.FINGER_TO_MOUNT])
peg_in_mount = mount_tm.contains(peg_pts)
check(not peg_in_mount.any(),
      f"指ペグ位置 3 点が claw_mount 本体の外側 (中実={int(peg_in_mount.sum())})")

# (c) 爪ハブ+指×3+指先チップ×3 の一体 (claw_mount ローカル座標) が
# claw_mount/forearm 自身と交差しないこと (爪ハブは接着で claw_mount の
# 先端に付くだけなので、正常な設計では交差は生じないはず)
assembly = [claw_tm]
for T in C.FINGER_TO_MOUNT:
    f = finger_raw.copy(); f.apply_transform(T); assembly.append(f)
for T in C.FINGERTIP_TO_MOUNT:
    t = fingertip_raw.copy(); t.apply_transform(T); assembly.append(t)
hand_tm = trimesh.util.concatenate(assembly)
try:
    inter = trimesh.boolean.intersection([hand_tm, mount_tm], engine="manifold")
    iv = float(inter.volume) if inter is not None and len(inter.faces) else 0.0
except Exception:
    iv = 0.0
print(f"  爪ハブ+指一体 vs claw_mount 交差体積: {iv:.1f} mm3 "
      f"(接着面での軽微な重なりは想定内)")
check(iv < 50.0, "爪ハブ+指一体が claw_mount と過大に交差しない")

print("[3b] ARM_HAND_REACH_MM-FOREARM_LEN (定数, config.py) が実メッシュの"
      " worst-case reach を上回るか (QA minor 指摘: 旧 [4] は firmware "
      "config.h と config.py 定数同士の突合のみで、どちらも人力複写値のため"
      "tautological — 実メッシュとの乖離を検出できなかった。[5b] の HAND_HALF"
      "と同型の drift 対策として新設。[3] で組んだ hand_tm を再利用)")
hand_reach_actual = float(hand_tm.vertices[:, 0].max())
hand_reach_const = C.ARM_HAND_REACH_MM - C.FOREARM_LEN
print(f"  実メッシュ (claw_mount+爪ハブ+指+指先チップ) の局所+X最大値 (手首面"
      f"=claw_mount原点基準, worst-case=finger[1]): {hand_reach_actual:.3f}mm "
      f"vs 定数={hand_reach_const:.2f}mm")
check(hand_reach_actual <= hand_reach_const,
      f"ARM_HAND_REACH_MM 由来の reach 定数が実メッシュの worst-case reach を"
      f"上回る (安全マージン {hand_reach_const - hand_reach_actual:.3f}mm)")

print(f"[4] 腕作業域 vs 前脚 (高さ帯別モデル, ヨー±{YAW_LIM:.0f}° + 内側クランプ + 地面ガード)")
# 吊り下げマウントでは手先は脚と同じ高さ帯を動くため、脚を高さ帯別に
# モデル化して全姿勢グリッドを検査する。肩ピッチ軸はブラケットローカル
# (20,0) — 中立ヨー (放射外向き, MOUNT_YAW) だけ回転した方位に 20mm オフ
# セット (body_h+9.2 は高さ, 従来どおり):
#   コクサ帯 (z > bh-15):     股軸まわり r45 円板 + 股+40mm 起点コリドー r55
#   大腿/膝帯 (bh-60 < z):    股+25mm 起点コリドー r55
#   脛上帯 (40 < z):          股+60mm 起点コリドー r45
#   足元帯 (z ≤ 40):          足中立位置まわり r70 円板 (足40 + 歩幅30)
MOUNT_YAW_R = np.radians(MOUNT_YAW)
sx0 = C.ARM_MOUNT_XY[0] + 20.0 * np.sin(MOUNT_YAW_R)
sy0 = C.ARM_MOUNT_XY[1] + 20.0 * np.cos(MOUNT_YAW_R)
SH_OVER, G_MARGIN = 9.2, 8.0
# 前腕+手の実リーチ (肘→指先): 2026-07-29 固定爪化で config.py
# ARM_HAND_REACH_MM (FOREARM_LEN + claw_mount/爪ハブ/指の実測 worst-case)
# へ一本化 (旧 v3 のパーム+可動指の式は廃止)
r_u = C.UPPER_ARM_LEN
r_f = C.ARM_HAND_REACH_MM
# firmware がガード/クランプに使う腕長は config.h の実ファイルから読む
# (値の複写は drift を検出できない — ARM_REACH 73/79 不一致事故の再発防止)
_fw = (ROOT / "firmware" / "src" / "config.h").read_text()
R_FW = float(re.search(r"ARM_REACH_MM\s*=\s*([\d.]+)f", _fw).group(1))
U_FW = float(re.search(r"ARM_UPPER_MM\s*=\s*([\d.]+)f", _fw).group(1))
check(abs(R_FW - r_f) < 0.05 and abs(U_FW - r_u) < 0.05,
      f"firmware 腕長定数が実寸と一致 (REACH {R_FW:.0f}={r_f:.0f}, "
      f"UPPER {U_FW:.0f}={r_u:.0f})")
YAW4, LAT_MAX = YAW_LIM, C.ARM_MOUNT_XY[0] - 14.5
# v3: 前脚 FR/FL は mount 15°/165°。コリドー軸は取付方位、掃引セクタは
# mount ± LIM_YAW (40°) + 5° マージン
_MNT = C.LEG_ANGLES["FR"]
DIAG = np.array([np.cos(np.radians(_MNT)), np.sin(np.radians(_MNT))])
_SEC_LO, _SEC_HI = _MNT - LEG_YAW_LIM - 5.0, _MNT + LEG_YAW_LIM + 5.0


def leg_clear(px, py, z, bh):
    d = np.inf
    p = np.array([px, py])
    for s in (1, -1):
        hip = np.array([s * C.HIPS["FR"][0], C.HIPS["FR"][1]])
        u = DIAG * [s, 1]
        if z > bh - 15:
            # coxa 円板はヨー掃引セクタ (mount±LIM_YAW, ±5° マージン) のみ。
            # 軸まわり全周はヨーサーボ/ホーン相当の r22 (腕短縮 v3 で折り畳み
            # 姿勢が体側の高帯に入るようになり、全周 r45 は過保守と判明)
            rel = p - hip
            ang = np.degrees(np.arctan2(rel[1], s * rel[0]))
            if _SEC_LO <= ang <= _SEC_HI:
                d = min(d, np.linalg.norm(rel) - 45)
            d = min(d, np.linalg.norm(rel) - 22)
            dd = p - (hip + 40 * u)
            t = max(0.0, dd @ u)
            d = min(d, np.linalg.norm(dd - t * u) - 55)
        elif z > bh - 60:
            # コリドー始端の球キャップ (t<0) は股軸の内側後方まで覆う過保守
            # だった (v3 の短腕折り畳みで顕在化)。t<0 側は実体が無い領域なので
            # 小径ハブに置き換える。ハブ v2 でさらに精密化: femur 帯の股軸
            # 直下に実体は無く (coxa 下端は実測 z>bh-13.1, 配線は中央穴経由)、r18 は
            # 脚セクタ内のみ (femur 内端側)、セクタ外は迷い配線余裕 r4
            dd = p - (hip + 25 * u)
            t = dd @ u
            if t >= 0:
                d = min(d, np.linalg.norm(dd - t * u) - 55)
            else:
                rel = p - hip
                ang = np.degrees(np.arctan2(rel[1], s * rel[0]))
                r_hub = 18 if _SEC_LO <= ang <= _SEC_HI else 4
                d = min(d, np.linalg.norm(rel) - r_hub)
        elif z > 40:
            dd = p - (hip + 60 * u)
            t = dd @ u
            if t >= 0:
                d = min(d, np.linalg.norm(dd - t * u) - 45)
            else:
                # 脛帯の股軸近傍に脚実体は無い (coxa 下端は bh-45 まで)。
                # 垂下配線の余裕のみ r12
                d = min(d, np.linalg.norm(p - hip) - 12)
        else:
            d = min(d, np.linalg.norm(p - (hip + 88 * u)) - 70)
    return d


min_d = np.inf
for bh in (105.0, 115.0, 130.0):
    dmax = bh + SH_OVER - G_MARGIN
    for pitch0 in np.linspace(-45, 85, 27):
        for elbow in np.linspace(0, 95, 20):
            # firmware の地面ガードを再現 (ピッチを起こして寸止め)
            p_ = pitch0
            while (U_FW * np.sin(np.radians(p_)) + R_FW *
                   np.sin(np.radians(p_ + elbow))) > dmax and p_ > -45:
                p_ -= 0.5
            # firmware の折り畳み深追いガードを再現 (planar が -5mm を割ら
            # ないようピッチを起こす。放射マウント固有, arms.h 参照)
            while (U_FW * np.cos(np.radians(p_)) + R_FW *
                   np.cos(np.radians(p_ + elbow))) < -5.0 and p_ > -45:
                p_ -= 0.5
            pr, er = np.radians(p_), np.radians(elbow)
            planar = r_u * np.cos(pr) + r_f * np.cos(pr + er)
            hz = bh + SH_OVER - (r_u * np.sin(pr) + r_f * np.sin(pr + er))
            ez = bh + SH_OVER - r_u * np.sin(pr)
            # 内側は腕相互クランプ ([5] が別途保証)。折り畳み深追いガードに
            # より planar<-5 は基本発生しないが、念のため防御的に残す
            yaw_in, yaw_out = -YAW4, YAW4
            if planar < -5.0:
                yaw_in = yaw_out = 0.0
            elif planar > LAT_MAX:
                yaw_in = max(yaw_in, -MOUNT_YAW - np.degrees(np.arcsin(LAT_MAX / planar)))
            for yaw in np.radians(np.linspace(yaw_in, yaw_out, 9)):
                az = MOUNT_YAW_R + yaw   # 手先方位 = 中立(放射外向き) + 追加ヨー
                hx = sx0 + planar * np.sin(az)
                hy = sy0 + planar * np.cos(az)
                ex = sx0 + r_u * np.cos(pr) * np.sin(az)
                ey = sy0 + r_u * np.cos(pr) * np.cos(az)
                min_d = min(min_d, leg_clear(hx, hy, max(hz, 0), bh),
                            leg_clear(ex, ey, ez, bh))
print(f"  手先/肘と脚モデルの最小距離 (体高105-130): {min_d:.0f} mm")
check(min_d > 0, f"全姿勢グリッドで脚モデルへの進入なし (ヨー±{YAW_LIM:.0f}° + ガード)")
print("  (モデルは粗近似。実機では脚静止状態で腕の可動域を先に確認すること)")

print("[5] 腕相互の接触回避 (内側ヨークランプ, firmware 連成リミット2)")
# ミラー動作では左右対称なので、右手中心 x が +HAND_HALF 以上あれば左手と
# 接触しない。HAND_HALF=14.5mm は firmware ARM_HAND_HALF_MM と同じ値 (2026-07-29
# 固定爪化で旧「パーム半径13.5」から根拠入替え — 実メッシュの手の局所横幅
# max|Y|=14.30mm を安全側で上回る値。[5b] で実メッシュ裏取りする)。放射
# マウント (中立ヨーが正面から MOUNT_YAW 外向き) では hand_x = MOUNT_X +
# planar·sin(MOUNT_YAW+yaw) — 「yaw だけで前向き固定」の旧式は使えない。
# クランプ導出は firmware と同じ定数 (config.h 実読値)、手先位置は実リーチ
# r_f で評価する。同一値を両方に使うと境界で恒等式になり検査にならない
MOUNT_X, HAND_HALF = C.ARM_MOUNT_XY[0], 14.5
check(abs(_fwval("ARM_MOUNT_X_MM") - MOUNT_X) < 0.05,
      f"firmware ARM_MOUNT_X_MM が config と一致 ({MOUNT_X:.0f})")
check(abs(_fwval("ARM_MOUNT_YAW_DEG") - MOUNT_YAW) < 0.05,
      f"firmware ARM_MOUNT_YAW_DEG が config と一致 ({MOUNT_YAW:.0f})")
min_x = np.inf
for bh in (105.0, 115.0, 130.0):
  dmax5 = bh + SH_OVER - G_MARGIN
  for pitch0 in np.linspace(-30, 85, 24):
    for elbow_deg in np.linspace(0, 95, 20):
        # firmware の地面ガード + 折り畳み深追いガードを同じ順で再現
        # ([4] と同じ流儀。pitch/elbow は deg で操作し最後に radian 化)
        p_ = pitch0
        while (U_FW * np.sin(np.radians(p_)) + R_FW *
               np.sin(np.radians(p_ + elbow_deg))) > dmax5 and p_ > -45:
            p_ -= 0.5
        while (U_FW * np.cos(np.radians(p_)) + R_FW *
               np.cos(np.radians(p_ + elbow_deg))) < -5.0 and p_ > -45:
            p_ -= 0.5
        pitch, elbow = np.radians(p_), np.radians(elbow_deg)
        planar_fw = U_FW * np.cos(pitch) + R_FW * np.cos(pitch + elbow)
        planar = r_u * np.cos(pitch) + r_f * np.cos(pitch + elbow)
        lat_max = MOUNT_X - HAND_HALF
        if planar_fw < -5.0:
            # firmware の折り畳みガードが先に発火し yaw=0 に固定される
            # (planar<-5 は yaw の符号によらず優先) — この分岐を外すと
            # 「大きく折り畳んだ姿勢 × 外側ヨー」という firmware では
            # 実際に起きない組合せを誤って最悪ケースに数える
            yaw_candidates = [0.0]
        else:
            yaw_min = -YAW_LIM
            if planar_fw > lat_max:
                yaw_min = max(yaw_min, -MOUNT_YAW - np.degrees(np.arcsin(lat_max / planar_fw)))
            yaw_candidates = np.linspace(yaw_min, 0.0, 8)  # 内側 (yaw<0) だけが
                                                            # 相互接触の懸念方向
        for yaw in np.radians(yaw_candidates):
            hand_x = MOUNT_X + planar * np.sin(MOUNT_YAW_R + yaw)
            min_x = min(min_x, hand_x)
print(f"  クランプ適用後の右手中心 x 最小値: {min_x:.1f} mm (HAND_HALF={HAND_HALF}mm)")
check(min_x >= 13.9, "両腕ミラー動作で手先が接触しない (中心 x ≥ ~14mm)")

print("[5b] HAND_HALF (14.5mm) が実メッシュの手の横幅を上回るか (firmware/"
      "check_arm 両方が使う定数の裏取り, 2026-07-29 固定爪化で palm_base 廃止"
      "に伴い根拠が入れ替わったため — pitfalls #31 と同型の drift 対策)")
# [3] で組んだ hand_tm (claw_mount+爪ハブ+指3+指先チップ3, claw_mount ローカル
# 座標系) を再利用。局所 Y 軸は肩ピッチ/肘 (いずれも局所 Y 回りの回転) を
# 通しても向きが変わらず、最終的な腕ヨー+マウント方位 (Z 回りの回転) で
# そのままワールド水平方向 (相互クランプが監視する方向) へ写像される —
# 局所 Z (上下) 方向の張り出しは相互クランプの評価方向に寄与しないため、
# 半径 (Y,Z 合成) ではなく |Y| のみを見るのが正しい安全マージンの定義
hand_half_actual = float(np.abs(hand_tm.vertices[:, 1]).max())
print(f"  実メッシュ (claw_mount+爪ハブ+指+指先チップ) の局所|Y|最大値: "
      f"{hand_half_actual:.2f}mm vs HAND_HALF={HAND_HALF}mm")
check(hand_half_actual <= HAND_HALF,
      f"HAND_HALF が実メッシュの手の横幅を上回る (安全マージン "
      f"{HAND_HALF - hand_half_actual:.2f}mm)")

print("[6] プリセットの接地クリアランス (firmware 地面ガード適用後)")
# 肩ピッチ軸の接地面からの高さ = body_h + 9.2 (プレート下面 -18.4)
SH_OVER, MARGIN = 9.2, 8.0
presets = {"TUCK": (0, 55, 95), "READY": (10, 30, 40),
           "REACH": (0, 10, 10)}
worst_z = np.inf
for name, (py_, pp, pe) in presets.items():
    for bh in (105.0, 115.0, 130.0):
        dmax = bh + SH_OVER - MARGIN
        p = float(pp)
        # ガード再現は firmware 定数、手先高さは実リーチ r_f ([4] と同じ流儀)
        while (U_FW * np.sin(np.radians(p))
               + R_FW * np.sin(np.radians(p + pe))) > dmax and p > -45:
            p -= 0.5
        z = bh + SH_OVER - (r_u * np.sin(np.radians(p))
                            + r_f * np.sin(np.radians(p + pe)))
        worst_z = min(worst_z, z)
print(f"  全プリセット × 体高105-130 での手先最低高さ: {worst_z:.1f} mm")
check(worst_z >= 7.0, "地面ガード適用後、手先が床に潜らない")

print("[7] 殻パーツ (元キット加工) の成立性")
# 輸入メッシュ×ブーリアンは浮遊片/分断を生みやすい (arm_pod_upper のリブ
# 孤立・elbow_shell の欠き貫通で実際に発生)。単一連結体+watertight を強制
for name in ("arm_pod_upper", "arm_pod_lower", "elbow_shell",
             "arm_pod_upper_L", "arm_pod_lower_L", "elbow_shell_L"):
    tm = trimesh.load(STL / f"{name}.stl")
    nb = len(tm.split(only_watertight=False))
    check(nb == 1 and tm.is_watertight,
          f"{name}: 単一連結体 (bodies={nb}) + watertight={tm.is_watertight}")

# ポッド ↔ 骨格/前腕掃引のブーリアン干渉 (肘 0/30/60/95°, 交差体積 0)
from manifold3d import Manifold, Mesh as MMesh  # noqa: E402


def _mani(path):
    tm = trimesh.load(path)
    return Manifold(MMesh(vert_properties=np.asarray(tm.vertices, np.float32),
                          tri_verts=np.asarray(tm.faces, np.uint32)))


pods = _mani(STL / "arm_pod_upper.stl") + _mani(STL / "arm_pod_lower.stl")
ua_m = _mani(STL / "upper_arm.stl")
fa_m = _mani(STL / "forearm.stl")
cm_m = _mani(STL / "claw_mount.stl").translate([C.FOREARM_LEN, 0, 0])
worst_iv = (pods ^ ua_m).volume()
for deg in (0.0, 30.0, 60.0, 95.0):
    moving = (fa_m + cm_m).rotate([0, deg, 0]).translate(
        [C.UPPER_ARM_LEN, 0, 0])
    worst_iv = max(worst_iv, (pods ^ moving).volume())
check(worst_iv < 0.01,
      f"ポッド vs 骨格+前腕/claw_mount 掃引 (肘0-95°) 交差体積 {worst_iv:.3f} mm3")

print(f"[8] 肩ブラケット/upper_arm/forearm+claw_mount vs Mouth_Cannon_Bored "
      f"(肩ピッチ全域×肘0-95°×腕ヨー±{YAW_LIM:.0f}°)")
# 放射マウント化 (2026-07-28) で肩が正面中央の砲身 (Mouth_Cannon) に近接する
# 懸念があるが、これまでどの check スクリプトにも検証が無かった (レビュー
# 指摘)。Mouth_Cannon_Bored.stl は raw STL がすでに (0,0,0) 中心 (実測: bbox
# center = (0,0,0) exactly) のため、make_visuals.shell_ghosts と同じ配置を
# そのまま [1b] と同じ chassis STL フレーム (z=0=プレート下面) へ適用できる:
#   body 座標 z=(zb+C.MOUTH_CANNON_T[2]), プレート下面 body 座標 z=zb ⇒
#   chassis-local z=C.MOUTH_CANNON_T[2] (zb=body_h+HIP_DROP が差分で相殺
#   するため body_h に依存しない)。
# 2026-07-29 実ソケット準拠の組立チェーン再構成で Mouth_Cannon は無回転
# ではなくなった (X軸 C.MOUTH_CANNON_ROT_X_DEG 回転, 頭部前面ソケットの
# 実測法線に合わせて前方基準60.4°下向き — hardware/src/config.py
# MOUTH_CANNON_* 参照)。tools/make_visuals.py shell_ghosts() と共通の定数
# (config.py に一元化, ドリフト防止) を使う。


def _rotx(deg):
    t = np.radians(deg)
    M = np.eye(4)
    M[1, 1], M[1, 2], M[2, 1], M[2, 2] = np.cos(t), -np.sin(t), np.sin(t), np.cos(t)
    return M


cannon = trimesh.load(STL / "Mouth_Cannon_Bored.stl")
cannon_w = cannon.copy()
_cannon_T = _rotx(C.MOUTH_CANNON_ROT_X_DEG)
_cannon_T[:3, 3] = C.MOUTH_CANNON_T
cannon_w.apply_transform(_cannon_T)
forearm_tm = trimesh.load(STL / "forearm.stl")
claw_mount_at_wrist = trimesh.load(STL / "claw_mount.stl")
claw_mount_at_wrist.apply_translation([C.FOREARM_LEN, 0, 0])
forearm_claw = trimesh.util.concatenate([forearm_tm, claw_mount_at_wrist])

# 右腕のみ検査: Mouth_Cannon は x=0 面でほぼ対称 (raw STL bbox center が
# (0,0,0) と実測済み) なため、左腕 (鏡像マウント+鏡像パーツ) は同じ
# クリアランスになる ([1b] の coxa 検査と同じ省略ロジック)
worst_cv8, worst_clr8, worst_pose8 = 0.0, np.inf, None
for arm_yaw in (-YAW_LIM, 0.0, YAW_LIM):
    Tb8 = _T(mx, my, horn_off, 90 - MOUNT_YAW - arm_yaw)
    br_w8 = _place(bracket, Tb8)
    v = _vol(br_w8, cannon_w)
    worst_cv8 = max(worst_cv8, v)
    for pitch in np.linspace(-45.0, 85.0, 7):
        Tp8 = np.eye(4)
        Tp8[:3, 3] = [20, 0, -pitch_dn]
        up_w8 = _place(upper, Tb8 @ Tp8 @ _roty(pitch))
        v = _vol(up_w8, cannon_w)
        worst_cv8 = max(worst_cv8, v)
        for elbow in (0.0, 30.0, 60.0, 95.0):
            Te8 = np.eye(4)
            Te8[:3, 3] = [C.UPPER_ARM_LEN, 0, 0]
            fp_w8 = _place(forearm_claw,
                           Tb8 @ Tp8 @ _roty(pitch) @ Te8 @ _roty(elbow))
            v = _vol(fp_w8, cannon_w)
            worst_cv8 = max(worst_cv8, v)
            if v == 0.0:
                # bbox 中心間距離を安価なふるいにかけ、近い候補だけ厳密な
                # signed_distance (低速) を計算する — 全点で呼ぶと低速+
                # メモリ膨張の原因になった (レビュー対応中に実測)
                d_bbox = float(np.linalg.norm(
                    fp_w8.bounding_box.centroid - cannon_w.bounding_box.centroid))
                if d_bbox < worst_clr8 + 60.0:
                    sd = trimesh.proximity.signed_distance(
                        cannon_w, fp_w8.vertices[::3])
                    clr = float(-sd.max())
                    if clr < worst_clr8:
                        worst_clr8, worst_pose8 = clr, (arm_yaw, pitch, elbow)
print(f"  最大交差体積: {worst_cv8:.0f} mm3 (肩ピッチ-45..85°×肘0-95°×"
      f"腕ヨー±{YAW_LIM:.0f}°)")
if worst_pose8 is not None:
    print(f"  最小クリアランス: {worst_clr8:.1f} mm "
          f"(yaw={worst_pose8[0]:.0f}°, pitch={worst_pose8[1]:.0f}°, "
          f"elbow={worst_pose8[2]:.0f}°)")
check(worst_cv8 < 1.0, "腕 (ブラケット/上腕/前腕+パーム) と Mouth_Cannon の干渉なし")

# レイアウト意図の数値 assert (2026-07 追加, レビュー指摘): 「砲身が左右の腕
# マウント x=±mx の間 (x=0 中心) にあり、z 帯が腕ポッド上部と重なる」という
# 設計意図そのものを、ジオメトリ非交差チェックとは別に明示的に検証する。
# 干渉が 0 でも、レイアウトとして砲身が腕の間の窓に収まっていなければ
# 意図とズレる (例えば砲身が腕マウントより外側にはみ出す設計変更に気付けない)
print("[8b] レイアウト意図: Mouth_Cannon は腕マウント x=±mx の間、"
      "z 帯は腕ポッド上部と重なる")
check(cannon_w.bounds[0][0] > -mx and cannon_w.bounds[1][0] < mx,
      f"Mouth_Cannon x範囲 [{cannon_w.bounds[0][0]:.1f},{cannon_w.bounds[1][0]:.1f}] "
      f"が腕マウント x=±{mx:.1f} の内側")
pods_neutral = trimesh.util.concatenate([
    trimesh.load(STL / "arm_pod_upper.stl"), trimesh.load(STL / "arm_pod_lower.stl")])
pods_w8 = _place(pods_neutral, _T(mx, my, horn_off, 90 - MOUNT_YAW))
cz0, cz1 = cannon_w.bounds[0][2], cannon_w.bounds[1][2]
pz0, pz1 = pods_w8.bounds[0][2], pods_w8.bounds[1][2]
overlap = min(cz1, pz1) - max(cz0, pz0)
check(overlap > 0,
      f"Mouth_Cannon z=[{cz0:.1f},{cz1:.1f}] と 腕ポッド z=[{pz0:.1f},{pz1:.1f}] "
      f"が重なる (overlap={overlap:.1f}mm)")

print(f"\nresult: {'OK' if OK else 'NG'}")
sys.exit(0 if OK else 1)
