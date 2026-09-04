"""脚リンク (tibia_link / femur_link) の曲げ応力スキャン — 断面係数の全区間走査。

2026-09-04 機構レビュー M-01 の恒久チェッカ: 既存の check_leg_assembly.py
(干渉・クリアランス) と check_screw_bosses.py (ビス肉厚) はどちらも「梁として
の曲げ強度」を見ておらず、tibia_link の膝ネックが 45° ウェッジで 11mm² まで
痩せていた欠陥 (σ≈240MPa, 破断確実) を見逃していた。ここでは実 STL を
リンク軸に沿って 0.5mm 刻みで切断し、各断面の断面二次モーメント (多角形の
Green 公式, 穴込み) から 2 軸の断面係数 Z を求め、足先荷重による曲げ
モーメント M(s) と比較して安全率 SF = σ_allow / (M/Z) の最小値を報告する。

荷重ケース (保守的):
  F_foot  = LOAD_KGF × 9.81 N。LOAD_KGF は sim_gait.py [3] の 3 脚支持静力学
            (重心オフセット込み) の最悪脚荷重 (≈1.9kgf) に動的係数 2.0
            (check_pod_neck_strength.py と同じ) を掛けた 3.8kgf を既定とする
  tibia   : 足先 (z=-TIBIA_LEN) に横荷重 T = 0.6·F (脛傾き ≤25° の面内成分
            sin25°≈0.42 と摩擦横力 μ≈0.5 の合成上限) を x / y 両方向で個別に
            作用させ、各断面 z で M = T·(z - z_foot)
  femur   : 膝 (x=FEMUR_LEN) に鉛直 F (y 軸まわり曲げ) と横 0.5·F (z 軸まわり)
            を作用させ、各断面 x で M = F·(FEMUR_LEN - x)
許容応力 σ_allow = 55MPa (PETG 曲げ強度の文献値 50-70MPa の下寄り, UNVERIFIED —
Phase 0 で試験片実測を推奨 (docs/build_plan.md P-06))。要求 SF ≥ 2.0。

使い方: .venv/bin/python tools/check_leg_link_strength.py  → 各リンクの最弱断面と SF
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import trimesh

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hardware" / "src"))
import config as C  # noqa: E402

STL = ROOT / "hardware" / "stl"
LOAD_KGF = 3.8          # 1.9kgf (3 脚支持・重心オフセット込み最悪脚) × 動的係数 2.0
SIGMA_ALLOW = 55.0      # MPa (PETG 曲げ, UNVERIFIED 文献値の下寄り)
SF_REQ = 2.0
STEP = 0.5
PHASE = 0.0137   # 断面位置を格子からずらす (面と一致する平面は退化リングを返す)


def _ring_props(xy: np.ndarray):
    """閉多角形 (N,2) の符号付き面積・一次/二次モーメント (原点まわり)。"""
    x, y = xy[:, 0], xy[:, 1]
    x1, y1 = np.roll(x, -1), np.roll(y, -1)
    cross = x * y1 - x1 * y
    A = 0.5 * cross.sum()
    Sx = cross @ (y + y1) / 6.0          # ∫y dA
    Sy = cross @ (x + x1) / 6.0          # ∫x dA
    Ixx = cross @ (y * y + y * y1 + y1 * y1) / 12.0
    Iyy = cross @ (x * x + x * x1 + x1 * x1) / 12.0
    return A, Sx, Sy, Ixx, Iyy


def section_props(mesh: trimesh.Trimesh, origin, normal, axes):
    """断面の (A, Zu, Zv): axes=(u,v) は断面内の 2 直交軸 (3D 単位ベクトル)。
    穴は自動的に負の面積として効く (リング向きを包含数で決める)。"""
    s = mesh.section(plane_origin=origin, plane_normal=normal)
    if s is None:
        return None
    rings = []
    for path in s.discrete:
        p = np.asarray(path)
        if len(p) < 3:
            continue
        uv = np.stack([p @ axes[0], p @ axes[1]], axis=1)
        rings.append(uv)
    if not rings:
        return None
    # 向き正規化: 外周 CCW (+), 穴 CW (-)。穴判定 = 他リングに奇数回含まれる
    from matplotlib.path import Path as MPath
    paths = [MPath(r) for r in rings]
    props = []
    for i, r in enumerate(rings):
        inside = sum(1 for j, pp in enumerate(paths) if j != i and pp.contains_point(r[0]))
        A, Sx, Sy, Ixx, Iyy = _ring_props(r)
        sign = -1.0 if inside % 2 == 1 else 1.0
        if (A > 0) != (sign > 0):
            A, Sx, Sy, Ixx, Iyy = -A, -Sx, -Sy, -Ixx, -Iyy
        props.append((A, Sx, Sy, Ixx, Iyy, r))
    A = sum(p[0] for p in props)
    if A <= 1e-6:
        return None
    cu = sum(p[2] for p in props) / A
    cv = sum(p[1] for p in props) / A
    Iuu = sum(p[3] for p in props) - A * cv * cv   # v 方向分布 → u 軸まわり
    Ivv = sum(p[4] for p in props) - A * cu * cu
    allv = np.concatenate([p[5] for p in props])
    c_v = np.abs(allv[:, 1] - cv).max()
    c_u = np.abs(allv[:, 0] - cu).max()
    return A, Iuu / c_v, Ivv / c_u   # Z_u (u 軸まわり曲げ), Z_v


def scan(name, mesh, s_range, origin_fn, normal, axes, moment_fns, labels):
    worst = (np.inf, None)
    rows = []
    lo, hi = sorted(s_range)
    for s in np.arange(lo, hi, STEP) + PHASE:
        pr = section_props(mesh, origin_fn(s), normal, axes)
        if pr is None:
            continue
        A, Zu, Zv = pr
        for (mfn, Z, lab) in ((moment_fns[0], Zu, labels[0]), (moment_fns[1], Zv, labels[1])):
            M = mfn(s)
            if M <= 0 or Z <= 1e-9:
                continue
            sigma = M / Z
            sf = SIGMA_ALLOW / sigma
            rows.append((s, A, Zu, Zv))
            if sf < worst[0]:
                worst = (sf, (s, A, Z, M, sigma, lab))
    sf, (s, A, Z, M, sigma, lab) = worst
    ok = sf >= SF_REQ
    print(f"[{name}] 最弱断面 s={s:+.1f}mm ({lab}): A={A:.1f}mm² Z={Z:.1f}mm³ "
          f"M={M/1000:.2f}N·m σ={sigma:.1f}MPa → SF={sf:.2f} "
          f"({'OK' if ok else 'NG — SF<' + str(SF_REQ)})")
    return ok


def main() -> int:
    F = LOAD_KGF * 9.81
    ok = True
    # ---- tibia: 原点=膝軸, 足先 z=-TIBIA_LEN, 断面は z=const (u=x, v=y)
    tib = trimesh.load(STL / "tibia_link.stl", force="mesh")
    T = 0.6 * F
    zf = -C.TIBIA_LEN
    ok &= scan("tibia_link", tib, (-8.0, zf + 12.0),
               lambda z: [0, 0, z], [0, 0, 1],
               (np.array([1.0, 0, 0]), np.array([0, 1.0, 0])),
               (lambda z: T * (z - zf), lambda z: T * (z - zf)),
               ("横荷重 y (x 軸まわり)", "面内荷重 x (y 軸まわり)"))
    # ---- femur: 原点=股ピッチ軸, 膝 x=FEMUR_LEN, 断面は x=const (u=y, v=z)
    fem = trimesh.load(STL / "femur_link.stl", force="mesh")
    xk = C.FEMUR_LEN
    ok &= scan("femur_link", fem, (8.0, xk - 12.0),
               lambda x: [x, 0, 0], [1, 0, 0],
               (np.array([0, 1.0, 0]), np.array([0, 0, 1.0])),
               (lambda x: F * (xk - x), lambda x: 0.5 * F * (xk - x)),
               ("鉛直荷重 z (y 軸まわり)", "横荷重 y (z 軸まわり)"))
    print(f"荷重 F={LOAD_KGF}kgf (動的係数込み), σ_allow={SIGMA_ALLOW}MPa (UNVERIFIED 文献値), "
          f"要求 SF≥{SF_REQ} → {'ALL OK' if ok else 'NG'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
