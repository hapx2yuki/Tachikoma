#!/usr/bin/env python3
"""firmware の IK / 歩容ロジックの数値検証 (C++ と同一の式を Python で再現)。

検証項目:
 1. IK→FK 往復誤差 (作業空間グリッド)
 2. 歩容の全脚軌道が IK 可到達か (速度指令 × 体高 105-130 の全域スイープ)
 3. 静的トルク概算 (総重量 3.0kg 想定 (腕込み), DS3218 20kg·cm 級)
 4. 静的安定マージン (重心シフト込みの支持多角形と CG の距離)
出力: docs/preview_gait.png (足先軌道の可視化)
"""
import re
import sys

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hardware" / "src"))
import config as _C  # noqa: E402

# ---- firmware/src/config.h と一致させる定数
# 寸法系は config.py を単一の正とし、firmware 側は下の突合チェックで検証する
# (複製定数の drift 事故防止 — ARM_REACH 73/79 の再発防止と同じ方式)
# TIBIA は IK/歩容専用の実効長 (TIBIA_LEN_GAIT = 物理 TIBIA_LEN 135 +
# FOOT_GROUND_OFFSET 18.6 = 153.6 — foot_pad 底を SWAY込みスタンス全域で
# world z=0 を下回らないよう校正した 2026-07-29 の接地連鎖修正。config.py
# 側のコメント参照。物理ジオメトリ生成 (make_leg.py 等) は引き続き
# C.TIBIA_LEN=135 を使う、別物)
COXA, FEMUR, TIBIA = _C.COXA_LEN, _C.FEMUR_LEN, _C.TIBIA_LEN_GAIT
_LEGS = ["FR", "FL", "RL", "RR"]
MOUNT = np.radians([_C.LEG_ANGLES[k] for k in _LEGS])
STANCE = np.radians([_C.STANCE_ANGLES[k] for k in _LEGS])
ORIGIN = np.array([_C.HIPS[k] for k in _LEGS])
LIM_YAW, LIM_PITCH, LIM_KNEE = 40.0, (-45.0, 55.0), 44.0  # LIM_YAW は下で突合
LIM_YAW_IN = 17.5      # 45°ペア内側ヨー 単側 (firmware LIM_YAW_IN と突合)
LIM_YAW_IN_SUM = 26.0  # 同 ペア同時内側の和 (firmware LIM_YAW_IN_SUM と突合)
LIM_YAW_POD = 22.0     # 後脚ポッド側ヨー (firmware LIM_YAW_POD と突合)
YAW_POD_SIGN = np.array([0, 0, +1, -1])
YAW_IN_SIGN = np.array([-1, +1, -1, +1])
BODY_H, STANCE_R, STEP_H = 115.0, _C.STANCE_R, 18.0
_fw = (ROOT / "firmware" / "src" / "config.h").read_text()
for _name, _py in (("HIP_R", _C.HIP_R), ("STANCE_R", STANCE_R),
                   ("LIM_YAW_IN", LIM_YAW_IN), ("LIM_YAW_IN_SUM", LIM_YAW_IN_SUM),
                   ("LIM_YAW_POD", LIM_YAW_POD), ("LIM_YAW", LIM_YAW)):
    _v = float(re.search(rf"{_name}\s*=\s*([\d.]+)f", _fw).group(1))
    assert abs(_v - _py) < 0.05, \
        f"firmware {_name}={_v} が config.py/sim の {_py} と不一致 (要同期)"
for _arr, _py in (("LEG_MOUNT_DEG", np.degrees(MOUNT)),
                  ("STANCE_DEG", np.degrees(STANCE))):
    _m = re.search(rf"{_arr}\[4\]\s*=\s*\{{([^}}]+)\}}", _fw).group(1)
    _v = [float(s) for s in re.findall(r"([\d.]+)f", _m)]
    assert np.allclose(_v, _py, atol=0.05), \
        f"firmware {_arr}={_v} が config.py の {list(_py)} と不一致 (要同期)"
_m = re.findall(r"\{([+-][\d.]+)f,\s*([+-][\d.]+)f\}", _fw)
_fw_origin = np.array([[float(a), float(b)] for a, b in _m[:4]])
assert np.allclose(_fw_origin, ORIGIN, atol=0.05), \
    f"firmware LEG_ORIGIN={_fw_origin.tolist()} が config.py の {ORIGIN.tolist()} と不一致"
BODY_H_RANGE = (105.0, 130.0)
MAX_STEP, MAX_TURN = 30.0, np.radians(12.0)
PHASE_OFF, DUTY = [0.25, 0.50, 0.75, 0.0], 0.75  # 遊脚順 RL→FL→FR→RR (回転順)
_fwtxt = (Path(__file__).resolve().parent.parent /
          "firmware" / "src" / "config.h").read_text()
SWAY_MM = float(re.search(r"SWAY_MM\s*=\s*([\d.]+)f", _fwtxt).group(1))
SWAY_LEAD = float(re.search(r"SWAY_LEAD\s*=\s*([\d.]+)f", _fwtxt).group(1))
D_KNEE_MAX = float(re.search(r"D_KNEE_MAX\s*=\s*([\d.]+)f", _fwtxt).group(1))
D_KNEE_MIN = float(re.search(r"D_KNEE_MIN\s*=\s*([\d.]+)f", _fwtxt).group(1))
# 突合: D_KNEE_MAX/MIN はリンク長と膝リミットから決まる導出値
_d_expect = np.sqrt(FEMUR**2 + TIBIA**2
                    + 2 * FEMUR * TIBIA * np.cos(np.radians(46.0))) - 0.5
assert abs(D_KNEE_MAX - _d_expect) < 0.1, \
    f"firmware D_KNEE_MAX={D_KNEE_MAX} が導出値 {_d_expect:.1f} と不一致"
_d_expect2 = np.sqrt(FEMUR**2 + TIBIA**2
                     + 2 * FEMUR * TIBIA * np.cos(np.radians(134.0))) + 0.5
assert abs(D_KNEE_MIN - _d_expect2) < 0.1, \
    f"firmware D_KNEE_MIN={D_KNEE_MIN} が導出値 {_d_expect2:.1f} と不一致"
# 突合: 複製している幾何/歩容定数一式 (レビュー指摘: COXA 等が bare literal
# だと firmware 側変更を検出できない — ARM_REACH 73/79 と同型の drift 穴)。
# TIBIA_LEN は 2026-07-29 以降 config.py の TIBIA_LEN_GAIT (=物理135+接地
# オフセット) と一致させる規約 — foot_pad 底の実測値が変わったら config.py
# 側 (FOOT_GROUND_OFFSET) を更新し、ここは自動的に追従する
# (tools/check_leg_assembly.py が実ビルド STL から実測して drift を検査)
for _name, _py in (("COXA_LEN", COXA), ("FEMUR_LEN", FEMUR), ("TIBIA_LEN", TIBIA),
                   ("LIM_PITCH_UP", -45.0), ("LIM_PITCH_DN", 55.0),
                   ("LIM_KNEE", 44.0), ("BODY_H_MIN", 105.0),
                   ("BODY_H_MAX", 130.0), ("STEP_H", 18.0),
                   ("MAX_STEP", 30.0), ("MAX_TURN_DEG", 12.0), ("DUTY", 0.75)):
    _v = float(re.search(rf"{_name}\s*=\s*(-?[\d.]+)f", _fwtxt).group(1))
    assert abs(_v - _py) < 0.05, \
        f"firmware {_name}={_v} が sim の {_py} と不一致 (要同期)"
# 突合: 腕マウント定数 (2026-07-28 Head_Bottom 実ソケット移設)。ARM_MOUNT_X_MM/
# ARM_MOUNT_YAW_DEG が config.py の ARM_MOUNT_XY[0]/ARM_MOUNT_YAW_DEG と
# drift していないか (ARM_REACH 73/79 事故と同型の穴を塞ぐ)
for _name, _py in (("ARM_MOUNT_X_MM", _C.ARM_MOUNT_XY[0]),
                   ("ARM_MOUNT_YAW_DEG", _C.ARM_MOUNT_YAW_DEG)):
    _v = float(re.search(rf"{_name}\s*=\s*(-?[\d.]+)f", _fwtxt).group(1))
    assert abs(_v - _py) < 0.05, \
        f"firmware {_name}={_v} が config.py の {_py:.1f} と不一致 (要同期)"
_m = re.search(r"PHASE_OFF\[4\]\s*=\s*\{([^}]+)\}", _fwtxt).group(1)
_v = [float(s) for s in re.findall(r"([\d.]+)f", _m)]
assert _v == [0.25, 0.50, 0.75, 0.0], f"firmware PHASE_OFF={_v} が sim と不一致"
LIFT_EPS = 1.0   # 足上げ高さがこれ未満なら接地扱い (TPU の潰れ相当)
TOTAL_KG = 3.0   # 実測前の設計想定 (STD サーボ+腕込み, filament_calc 実行値ベース・切上げ)


def leg_ik(x, y, z):
    yaw = np.arctan2(y, x)
    r = np.hypot(x, y) - COXA
    d = -z
    dist2 = r * r + d * d
    dist = np.sqrt(dist2)
    if dist >= (FEMUR + TIBIA) * 0.995 or dist <= abs(FEMUR - TIBIA) * 1.02:
        return None
    cb = np.clip((dist2 - FEMUR**2 - TIBIA**2) / (2 * FEMUR * TIBIA), -1, 1)
    beta = np.arccos(cb)
    alpha = np.arctan2(d, r) - np.arctan2(TIBIA * np.sin(beta), FEMUR + TIBIA * np.cos(beta))
    yaw_d, pitch_d, knee_d = np.degrees([yaw, alpha, beta - np.pi / 2])
    if abs(yaw_d) > LIM_YAW: return None
    if not (LIM_PITCH[0] <= pitch_d <= LIM_PITCH[1]): return None
    if abs(knee_d) > LIM_KNEE: return None
    return yaw_d, pitch_d, knee_d


def leg_fk(yaw_d, pitch_d, knee_d):
    yaw, pitch = np.radians(yaw_d), np.radians(pitch_d)
    beta = np.radians(knee_d) + np.pi / 2
    r = COXA + FEMUR * np.cos(pitch) + TIBIA * np.cos(pitch + beta)
    d = FEMUR * np.sin(pitch) + TIBIA * np.sin(pitch + beta)
    return r * np.cos(yaw), r * np.sin(yaw), -d


def neutral_xy(leg):
    # 中立足先は STANCE 方位 (取付方位 + 中立ヨー, gait.h と同一)
    return (ORIGIN[leg, 0] + STANCE_R * np.cos(STANCE[leg]),
            ORIGIN[leg, 1] + STANCE_R * np.sin(STANCE[leg]))


def swing_state(phase):
    """(遊脚 index or None, 遊脚進行度)"""
    for leg in range(4):
        p = (phase + PHASE_OFF[leg]) % 1.0
        if p >= DUTY:
            return leg, (p - DUTY) / (1 - DUTY)
    return None, 0.0


def sway_of(phase):
    """gait.h と同一: 遊脚窓を前後 SWAY_LEAD 拡張した sin 窓の合成。"""
    swing_len = 1.0 - DUTY
    win = swing_len + 2 * SWAY_LEAD
    sx = sy = 0.0
    for leg in range(4):
        p = (phase + PHASE_OFF[leg]) % 1.0
        u = p - (DUTY - SWAY_LEAD)
        if u < -0.5:
            u += 1.0
        if u < 0 or u > win:
            continue
        nx, ny = neutral_xy(leg)
        nn = np.hypot(nx, ny)
        k = SWAY_MM * np.sin(np.pi * u / win)
        sx += -nx / nn * k
        sy += -ny / nn * k
    return sx, sy


def foot_target(leg, phase, vx, vy, wz, body_h=BODY_H):
    """gait.h update() と同一 (重心シフト込み, 脚ローカル座標を返す)。"""
    nx, ny = neutral_xy(leg)
    turn = wz * MAX_TURN
    tx = nx * np.cos(turn) - ny * np.sin(turn) - nx
    ty = nx * np.sin(turn) + ny * np.cos(turn) - ny
    sx, sy = vx * MAX_STEP + tx, vy * MAX_STEP + ty
    sn = np.hypot(sx, sy)
    if sn > MAX_STEP:
        sx, sy = sx * MAX_STEP / sn, sy * MAX_STEP / sn
    p = (phase + PHASE_OFF[leg]) % 1.0
    if p < DUTY:
        t = p / DUTY
        dx, dy, dz = sx * (0.5 - t), sy * (0.5 - t), 0.0
    else:
        t = (p - DUTY) / (1 - DUTY)
        dx, dy, dz = sx * (t - 0.5), sy * (t - 0.5), STEP_H * np.sin(np.pi * t)
    swx, swy = sway_of(phase)
    fx = nx + dx - swx - ORIGIN[leg, 0]
    fy = ny + dy - swy - ORIGIN[leg, 1]
    c, s = np.cos(-MOUNT[leg]), np.sin(-MOUNT[leg])
    lx, ly, lz = fx * c - fy * s, fx * s + fy * c, -body_h + dz
    # ワークスペース射影 (gait.h と同一): 膝リミット円環内へ平面クランプ
    dd = -lz
    rr = np.hypot(lx, ly)
    if dd < D_KNEE_MAX:
        rmax = COXA + np.sqrt(D_KNEE_MAX**2 - dd * dd)
        if rr > rmax:
            lx, ly = lx * rmax / rr, ly * rmax / rr
    if dd < D_KNEE_MIN and rr > 0.1:
        rmin = COXA + np.sqrt(D_KNEE_MIN**2 - dd * dd)
        if rr < rmin:
            lx, ly = lx * rmin / rr, ly * rmin / rr
    return lx, ly, lz


def foot_body_xy(leg, phase, vx, vy, wz):
    """ボディ座標系での足先 XY (安定判定用)。"""
    lx, ly, _ = foot_target(leg, phase, vx, vy, wz)
    c, s = np.cos(MOUNT[leg]), np.sin(MOUNT[leg])
    return (lx * c - ly * s + ORIGIN[leg, 0], lx * s + ly * c + ORIGIN[leg, 1])


def polygon_margin(phase, vx, vy, wz):
    """向きを正規化した安定マージン (正=CG が支持多角形内)。

    足上げ高さ < LIFT_EPS の脚は接地扱い (境界瞬間の 4 点支持を正しく評価)。
    """
    stance = []
    for leg in range(4):
        _, _, lz = foot_target(leg, phase, vx, vy, wz)
        if lz > -BODY_H + LIFT_EPS:
            continue  # 空中
        stance.append(leg)
    pts = np.array([foot_body_xy(i, phase, vx, vy, wz) for i in stance])
    cen = pts.mean(axis=0)
    ang = np.arctan2(pts[:, 1] - cen[1], pts[:, 0] - cen[0])
    pts = pts[np.argsort(ang)]  # CCW
    dists = []
    for i in range(len(pts)):
        a, b = pts[i], pts[(i + 1) % len(pts)]
        e = b - a
        cross = e[0] * (-a[1]) - e[1] * (-a[0])
        dists.append(cross / np.linalg.norm(e))
    return min(dists)


def main():
    # ---- 1. IK/FK 往復
    n, worst, ok = 0, 0.0, 0
    for x in np.linspace(40, 180, 25):
        for y in np.linspace(-70, 70, 25):
            for z in np.linspace(-165, -60, 19):
                n += 1
                a = leg_ik(x, y, z)
                if a is None:
                    continue
                ok += 1
                xx, yy, zz = leg_fk(*a)
                worst = max(worst, np.hypot(np.hypot(xx - x, yy - y), zz - z))
    print(f"[1] IK/FK 往復: {ok}/{n} 点到達, 最大誤差 {worst:.4f} mm "
          f"({'OK' if worst < 1e-3 else 'NG'})")

    # ---- 2. 歩容スイープ (速度指令 × 体高)。あわせて 45° ペア内側ヨーの
    #      使用量を実測し、LIM_YAW_IN クランプが通常歩容で発火しないことを確認
    fails = total = 0
    max_in, max_sum, max_pod = -np.inf, -np.inf, -np.inf
    for body_h in np.linspace(*BODY_H_RANGE, 4):
        for vx in np.linspace(-1, 1, 5):
            for vy in np.linspace(-1, 1, 5):
                for wz in np.linspace(-1, 1, 5):
                    if np.hypot(vx, vy) > 1:
                        continue
                    for phase in np.linspace(0, 1, 40, endpoint=False):
                        inw = [0.0] * 4
                        for leg in range(4):
                            total += 1
                            a = leg_ik(*foot_target(leg, phase, vx, vy, wz, body_h))
                            if a is None:
                                fails += 1
                            else:
                                inw[leg] = a[0] * YAW_IN_SIGN[leg]
                                if YAW_POD_SIGN[leg]:
                                    max_pod = max(max_pod,
                                                  a[0] * YAW_POD_SIGN[leg])
                        max_in = max(max_in, *inw)
                        max_sum = max(max_sum, inw[0] + inw[3], inw[1] + inw[2])
    print(f"[2] 歩容全域スイープ (体高105-130含む): {total} 姿勢中 IK 失敗 {fails} "
          f"({'OK' if fails == 0 else 'NG — 歩幅/体高/SWAYの見直しが必要'})")
    ok2b = (max_in < LIM_YAW_IN - 0.5 and max_sum < LIM_YAW_IN_SUM - 2
            and max_pod < LIM_YAW_POD - 1)
    print(f"[2b] 内側ヨー使用 単側 {max_in:.1f}°/{LIM_YAW_IN}° "
          f"ペア和 {max_sum:.1f}°/{LIM_YAW_IN_SUM}° "
          f"後脚ポッド側 {max_pod:.1f}°/{LIM_YAW_POD}° "
          f"({'OK' if ok2b else 'NG — クランプが歩容に干渉'})")

    # ---- 3. 静的トルク概算 (総重量 TOTAL_KG, 3 脚接地, 最悪脚 40% 負担)
    load = TOTAL_KG * 9.81 * 0.40
    x, y, z = foot_target(0, 0.3, 1.0, 0, 0)
    a = leg_ik(x, y, z)
    if a:
        pitch = np.radians(a[1])
        knee_r = COXA + FEMUR * np.cos(pitch)
        foot_r = np.hypot(x, y)
        t_knee = load * abs(foot_r - knee_r) / 1000 * 10.197
        t_hip = load * abs(foot_r - COXA) / 1000 * 10.197
        print(f"[3] 静的トルク概算 (総重量{TOTAL_KG}kg): 膝 {t_knee:.2f} kg·cm, "
              f"股ピッチ {t_hip:.2f} kg·cm (DS3218 定格 ~20 kg·cm @6.8V)")

    # ---- 4. 静的安定マージン (重心シフト込み)
    worst_m, worst_at = np.inf, None
    for vx, vy, wz in [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0),
                       (0.7, 0.7, 0), (0.7, -0.7, 0), (-0.7, 0.7, 0), (-0.7, -0.7, 0),
                       (0, 0, 1), (0, 0, -1), (0.5, 0.5, 0.5), (0, 0, 0)]:
        for phase in np.linspace(0, 1, 200, endpoint=False):
            m = polygon_margin(phase, vx, vy, wz)
            if m < worst_m:
                worst_m, worst_at = m, (vx, vy, wz, phase)
    print(f"[4] 静的安定マージン最小 {worst_m:.1f} mm at cmd={worst_at[:3]} "
          f"phase={worst_at[3]:.2f} ({'OK' if worst_m >= 8 else 'NG — SWAY_MM を増やす'})")

    # ---- 軌道プロット
    fig = plt.figure(figsize=(12, 4.5))
    ax1 = fig.add_subplot(1, 2, 1, projection="3d")
    cols = ["#d24", "#28c", "#2a2", "#b6b"]
    for leg in range(4):
        pts = []
        for ph in np.linspace(0, 1, 120):
            bx, by = foot_body_xy(leg, ph, 1.0, 0, 0)
            _, _, lz = foot_target(leg, ph, 1.0, 0, 0)
            pts.append([bx, by, lz])
        pts = np.array(pts)
        ax1.plot(*pts.T, color=cols[leg], label=["FR", "FL", "RL", "RR"][leg])
    ax1.scatter(*ORIGIN.T, [0] * 4, c="k", marker="s")
    ax1.set_title("foot paths w/ CG sway (vx=1)"); ax1.legend(fontsize=8)
    ax1.set_box_aspect([1, 1, 0.5])

    ax2 = fig.add_subplot(1, 2, 2)
    phases = np.linspace(0, 1, 400, endpoint=False)
    for cmd, lab in [((1, 0, 0), "vx=1"), ((0, 0, 1), "wz=1"), ((0.7, 0.7, 0), "diag")]:
        ms = [polygon_margin(p, *cmd) for p in phases]
        ax2.plot(phases, ms, label=lab)
    ax2.axhline(0, color="k", lw=0.8); ax2.axhline(8, color="r", lw=0.8, ls="--")
    ax2.set_title("stability margin (mm) vs phase"); ax2.legend(fontsize=8)
    ax2.grid(alpha=0.3)
    fig.tight_layout()
    out = ROOT / "docs" / "preview_gait.png"
    fig.savefig(out, dpi=110)
    print(f"saved {out}")


if __name__ == "__main__":
    main()
