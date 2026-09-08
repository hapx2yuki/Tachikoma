#!/usr/bin/env python3
"""firmware の IK / 歩容ロジックの数値検証 (C++ と同一の式を Python で再現)。

検証項目:
 1. IK→FK 往復誤差 (作業空間グリッド)
 2. 歩容の全脚軌道が IK 可到達か (速度指令 × 体高 110-130 の全域スイープ)
 3. 静的トルク概算 (総重量 3.0kg の設計想定。旧DS3218由来の数値は履歴比較)
 4. 静的安定マージン (重心シフト込みの支持多角形と CG の距離)
出力: ``--output`` で指定した画像（省略時は一意な一時ファイル）
"""
import sys
import argparse
import os
import tempfile

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hardware" / "src"))
import config as _C  # noqa: E402
from config_contract import (  # noqa: E402
    assert_firmware_matches_config,
    canonical_values,
)

# ---- config.py を正本にした firmware 契約
# firmware/src/config.h から値を読んで Python 側の既定値にすることはしない。
# 先に config.py の正本と一方向に突合し、差分があればシミュレーションを
# 開始しない。これにより、firmware と Python の同じ誤値を自己比較する穴を塞ぐ。
_FW_TEXT = (ROOT / "firmware" / "src" / "config.h").read_text(encoding="utf-8")
assert_firmware_matches_config(_FW_TEXT)
_CANONICAL = canonical_values()
_CS = _CANONICAL["scalars"]
_CA = _CANONICAL["arrays"]

# TIBIA は IK/歩容専用の実効長 (TIBIA_LEN_GAIT = 物理 TIBIA_LEN 135 +
# FOOT_GROUND_OFFSET 20.98)。物理ジオメトリ生成 (make_leg.py 等) は引き続き
# C.TIBIA_LEN=135 を使うため、実体寸法と接地点の等価長を混同しない。
COXA, FEMUR, TIBIA = _C.COXA_LEN, _C.FEMUR_LEN, _C.TIBIA_LEN_GAIT
_LEGS = ["FR", "FL", "RL", "RR"]
MOUNT = np.radians([_C.LEG_ANGLES[k] for k in _LEGS])
STANCE = np.radians([_C.STANCE_ANGLES[k] for k in _LEGS])
ORIGIN = np.array([_C.HIPS[k] for k in _LEGS])
LIM_YAW = _CS["LIM_YAW"]
LIM_PITCH = (_CS["LIM_PITCH_UP"], _CS["LIM_PITCH_DN"])
LIM_KNEE = _CS["LIM_KNEE"]
LIM_YAW_IN = _CS["LIM_YAW_IN"]
LIM_YAW_IN_SUM = _CS["LIM_YAW_IN_SUM"]
LIM_YAW_POD = _CS["LIM_YAW_POD"]
YAW_POD_SIGN = np.array(_CA["YAW_POD_SIGN"], dtype=int)
YAW_IN_SIGN = np.array(_CA["YAW_IN_SIGN"], dtype=int)
BODY_H = _CS["BODY_H_DEF"]
STANCE_R = _CS["STANCE_R"]
STEP_H = _CS["STEP_H"]
BODY_H_RANGE = (_CS["BODY_H_MIN"], _CS["BODY_H_MAX"])
MAX_STEP = _CS["MAX_STEP"]
MAX_TURN = np.radians(_CS["MAX_TURN_DEG"])
PHASE_OFF = list(_CA["PHASE_OFF"])
DUTY = _CS["DUTY"]  # 遊脚順 RL→FL→FR→RR (回転順)
SWAY_MM = np.array(_CA["SWAY_MM"], dtype=float)  # 脚ごと {FR,FL,RL,RR}
SWAY_LEAD = _CS["SWAY_LEAD"]
D_KNEE_MAX = _CS["D_KNEE_MAX"]
D_KNEE_MIN = _CS["D_KNEE_MIN"]
# 中立足先パターンのオフセットと全機体重心 (config.py が正)
STANCE_OFF = np.array(_C.STANCE_OFF_XY, float)
CG_XY = np.array(_C.CG_XY, float)
LIFT_EPS = 1.0   # 足上げ高さがこれ未満なら接地扱い (TPU の潰れ相当)
TOTAL_KG = 3.0   # 実測前の設計想定。旧DS3218由来の定格比較は履歴であり採用値ではない。


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
    # 中立足先は STANCE 方位 (取付方位 + 中立ヨー) + パターン全体のオフセット
    # STANCE_OFF (重心側へ寄せる, gait.h と同一)
    return (ORIGIN[leg, 0] + STANCE_R * np.cos(STANCE[leg]) + STANCE_OFF[0],
            ORIGIN[leg, 1] + STANCE_R * np.sin(STANCE[leg]) + STANCE_OFF[1])


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
        k = SWAY_MM[leg] * np.sin(np.pi * u / win)
        sx += -nx / nn * k
        sy += -ny / nn * k
    return sx, sy


def foot_target(leg, phase, vx, vy, wz, body_h=BODY_H, *, holding=False):
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
    swx, swy = (0.0, 0.0) if holding else sway_of(phase)
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


def stance_points(phase, vx, vy, wz):
    """接地脚 index と、そのボディ座標足先 XY (足上げ高さ < LIFT_EPS は接地扱い)。"""
    stance = []
    for leg in range(4):
        _, _, lz = foot_target(leg, phase, vx, vy, wz)
        if lz > -BODY_H + LIFT_EPS:
            continue  # 空中
        stance.append(leg)
    return stance, np.array([foot_body_xy(i, phase, vx, vy, wz) for i in stance])


def leg_loads(pts):
    """静的 3/4 点支持の鉛直脚荷重 (N): ΣF=W, ΣF·x=W·CGx, ΣF·y=W·CGy を解く
    (3 点は一意、4 点は最小ノルム解)。重心オフセット CG_XY 込み。"""
    W = TOTAL_KG * 9.81
    A = np.vstack([np.ones(len(pts)), pts[:, 0], pts[:, 1]])
    b = np.array([W, W * CG_XY[0], W * CG_XY[1]])
    return np.linalg.lstsq(A, b, rcond=None)[0]


def polygon_margin(phase, vx, vy, wz):
    """向きを正規化した静的安定マージン (正=全機体重心 CG_XY が支持多角形内)。

    足上げ高さ < LIFT_EPS の脚は接地扱い (境界瞬間の 4 点支持を正しく評価)。
    2026-09-04: 旧実装は原点 (0,0) からの距離を返しており「重心=原点」を暗黙に
    仮定していた (S-01)。実重心 (config.py CG_XY, y=-39mm) 基準へ修正。
    """
    _, pts = stance_points(phase, vx, vy, wz)
    cen = pts.mean(axis=0)
    ang = np.arctan2(pts[:, 1] - cen[1], pts[:, 0] - cen[0])
    pts = pts[np.argsort(ang)]  # CCW
    dists = []
    for i in range(len(pts)):
        a, b = pts[i], pts[(i + 1) % len(pts)]
        e = b - a
        cross = e[0] * (CG_XY[1] - a[1]) - e[1] * (CG_XY[0] - a[0])
        dists.append(cross / np.linalg.norm(e))
    return min(dists)


# 安定マージン/トルク評価に使う指令セット (前後左右・斜め・旋回・複合・静止)
EVAL_CMDS = [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0),
             (0.7, 0.7, 0), (0.7, -0.7, 0), (-0.7, 0.7, 0), (-0.7, -0.7, 0),
             (0, 0, 1), (0, 0, -1), (0.5, 0.5, 0.5), (0, 0, 0)]
# 既存の静止点支持設計用閾値。連続定格ではない。メーカー端点は
# config.POWER_COMPONENTSに保存、6V最大トルク内挿は約19.94kgf·cm。
# 足トゥの実接点・動的荷重・実サーボ発熱はこの計算では評価できない。
T_HIP_WARN, T_HIP_NG = 18.0, 20.0


def main(output=None):
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
    print(f"[2] 歩容全域スイープ (体高{BODY_H_RANGE[0]:.0f}-{BODY_H_RANGE[1]:.0f}含む): {total} 姿勢中 IK 失敗 {fails} "
          f"({'OK' if fails == 0 else 'NG — 歩幅/体高/SWAYの見直しが必要'})")
    ok2b = (max_in < LIM_YAW_IN - 0.5 and max_sum < LIM_YAW_IN_SUM - 2
            and max_pod < LIM_YAW_POD - 1)
    print(f"[2b] 内側ヨー使用 単側 {max_in:.1f}°/{LIM_YAW_IN}° "
          f"ペア和 {max_sum:.1f}°/{LIM_YAW_IN_SUM}° "
          f"後脚ポッド側 {max_pod:.1f}°/{LIM_YAW_POD}° "
          f"({'OK' if ok2b else 'NG — クランプが歩容に干渉'})")

    # ---- 3. 静的トルク (総重量 TOTAL_KG, 重心 CG_XY): 全指令 × 全位相で
    #      3/4 点支持の静力学から脚荷重を解き、股ピッチ = F·|foot_r-COXA|,
    #      膝 = F·|foot_r-knee_r| の最悪値を取る。2026-09-04 (S-02): 旧実装は
    #      1 点 (FR, phase 0.3, vx=1)・「最悪脚 40%」仮定で 8.92 kgf·cm と報告して
    #      いたが、全域では 18 kgf·cm 級 (重心が支持三角形の辺に寄る瞬間は 1 脚に
    #      1.6kgf 以上が乗る)。旧DS3218の6V比較値とほぼ同水準だが、現行候補へ
    #      定格を流用しない — L-02ベンチ試験の荷重条件はこのホスト計算値で決める
    w_hip, w_knee, w_load, w_at = 0.0, 0.0, 0.0, None
    for cmd in EVAL_CMDS:
        for phase in np.linspace(0, 1, 200, endpoint=False):
            st, pts = stance_points(phase, *cmd)
            F = leg_loads(pts)
            for i, leg in enumerate(st):
                x, y, z = foot_target(leg, phase, *cmd)
                a = leg_ik(x, y, z)
                if a is None:
                    continue
                foot_r = np.hypot(x, y)
                knee_r = COXA + FEMUR * np.cos(np.radians(a[1]))
                t_hip = F[i] * abs(foot_r - COXA) / 1000 * 10.197
                t_knee = F[i] * abs(foot_r - knee_r) / 1000 * 10.197
                w_load = max(w_load, F[i] / 9.81)
                w_knee = max(w_knee, t_knee)
                if t_hip > w_hip:
                    w_hip, w_at = t_hip, (_LEGS[leg], cmd, round(phase, 3),
                                          round(F[i] / 9.81, 2), round(foot_r, 1))
    verdict = ("NG — サーボ定格超過 (歩幅/STANCE_R/重量の見直し or 高トルク品)"
               if w_hip > T_HIP_NG else
               ("要注意 — 連続トルク未確認。L-02 で実測" if w_hip > T_HIP_WARN
                else "OK"))
    print(f"[3] 静的トルク最悪 (総重量{TOTAL_KG}kg, 重心 y={CG_XY[1]:+.0f}mm, 3/4点支持静力学): "
          f"股ピッチ {w_hip:.2f} kgf·cm at {w_at[0]} cmd={w_at[1]} phase={w_at[2]} "
          f"(脚荷重 {w_at[3]} kgf, foot_r {w_at[4]}mm), 膝 {w_knee:.2f} kgf·cm, "
          f"最大脚荷重 {w_load:.2f} kgf → {verdict}")

    # ---- 4. 静的安定マージン (重心シフト込み, 実重心 CG_XY 基準)
    worst_m, worst_at = np.inf, None
    for vx, vy, wz in EVAL_CMDS:
        for phase in np.linspace(0, 1, 200, endpoint=False):
            m = polygon_margin(phase, vx, vy, wz)
            if m < worst_m:
                worst_m, worst_at = m, (vx, vy, wz, phase)
    print(f"[4] 静的安定マージン最小 {worst_m:.1f} mm (重心 ({CG_XY[0]:+.0f},{CG_XY[1]:+.0f})mm, "
          f"足先オフセット ({STANCE_OFF[0]:+.0f},{STANCE_OFF[1]:+.0f})mm, SWAY {SWAY_MM.tolist()}) "
          f"at cmd={worst_at[:3]} phase={worst_at[3]:.2f} "
          f"({'OK' if worst_m >= 8 else 'NG — STANCE_OFF/SWAY_MM を見直す'})")

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
    if output is None:
        fd, temp_name = tempfile.mkstemp(prefix="tachikoma-sim-gait-", suffix=".png")
        os.close(fd)
        out = Path(temp_name)
    else:
        out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=110)
    plt.close(fig)
    print(f"saved {out}")
    checks = {
        "IK/FK": ok > 0 and np.isfinite(worst) and worst < 1e-3,
        "IK到達": total > 0 and fails == 0,
        "ヨー余裕": ok2b,
        "静的トルク上限": np.isfinite(w_hip) and w_hip <= T_HIP_NG,
        "静的安定": np.isfinite(worst_m) and worst_m >= 8,
    }
    failed = [name for name, passed in checks.items() if not passed]
    print("RESULT:", "FAIL: " + ", ".join(failed) if failed else "PASS")
    print("注: PASSは計算上の既存閾値への適合。6V実機の連続トルク・発熱・接地は未検証。")
    return 1 if failed else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="検証画像の保存先")
    sys.exit(main(parser.parse_args().output))
