#!/usr/bin/env python3
"""タブビス下穴まわりの肉厚検査 (レビュー指摘の再発防止)。

各サーボのタブビス位置で、下穴を囲む環状領域 (r 1.6..3.8) にどれだけ
材料が残っているかをブーリアン交差で実測する。充填率 70% 未満は NG。
"""
import sys
from pathlib import Path

import numpy as np
import trimesh

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hardware" / "src"))
import config as C  # noqa: E402

STL = ROOT / "hardware" / "stl"
P = C.LEG_SERVO


def annulus(depth: float):
    outer = trimesh.creation.cylinder(radius=3.8, height=depth)
    inner = trimesh.creation.cylinder(radius=1.6, height=depth + 2)
    return trimesh.boolean.difference([outer, inner], engine="manifold")


def probe(mesh, positions, axis: str, depth: float, label: str):
    """positions: 下穴の (x,y,z) = ビス頭側の座面中心。axis: ねじ込み方向。"""
    ok = True
    theo = annulus(depth).volume
    for i, (x, y, z) in enumerate(positions):
        an = annulus(depth)
        if axis == "-y":
            an.apply_transform(trimesh.transformations.rotation_matrix(
                np.pi / 2, [1, 0, 0]))
            an.apply_translation([x, y - depth / 2, z])
        elif axis == "+y":   # ミラー部品: 材料が +y 側
            an.apply_transform(trimesh.transformations.rotation_matrix(
                np.pi / 2, [1, 0, 0]))
            an.apply_translation([x, y + depth / 2, z])
        elif axis == "-z":
            an.apply_translation([x, y, z - depth / 2])
        inter = trimesh.boolean.intersection([mesh, an], engine="manifold")
        fill = 0.0 if (inter is None or inter.is_empty) else inter.volume / theo
        flag = "OK" if fill >= 0.70 else "NG <<<"
        ok &= fill >= 0.70
        print(f"  {label}#{i}: fill {fill*100:5.1f}%  {flag}")
    return ok


def main():
    cx = P["L"] / 2 - P["SHAFT_OFF"]
    hx = (-cx - P["HOLE_PITCH"] / 2, -cx + P["HOLE_PITCH"] / 2)
    hy = (-P["HOLE_SPREAD"] / 2, P["HOLE_SPREAD"] / 2)
    ok = True

    # coxa / femur: 箱枠のタブ座面は y=0 (ローカル)、ねじ込み -Y、深さ 10。
    # v3: ミラー版 (_m, FR/RL 用) も検査 (穴位置は y 反転だがタブ座面は同じ)
    for sfx, ax in (("", "-y"), ("_m", "+y")):
        coxa = trimesh.load(STL / f"coxa_bracket{sfx}.stl")
        pos = [(C.COXA_LEN + x, 0, z) for x in hx for z in hy]
        ok &= probe(coxa, pos, ax, 10, f"coxa{sfx} tab")

        femur = trimesh.load(STL / f"femur_link{sfx}.stl")
        pos = [(C.FEMUR_LEN + x, 0, z) for x in hx for z in hy]
        ok &= probe(femur, pos, ax, 10, f"femur{sfx} tab")

    # chassis: ボス上面 z = CHASSIS_T + 3、ねじ込み -Z、深さ 6.5。
    # v3: ケース向きは CASE_ANG (脚方位と独立)。FR と RR (45°ペア側) を検査
    import make_chassis as MC
    chassis = trimesh.load(STL / "chassis.stl")
    for leg in ("FR", "RR"):
        a = np.radians(MC.CASE_ANG[leg])
        ca, sa = np.cos(a), np.sin(a)
        hx_, hy_ = C.HIPS[leg]
        pos = []
        for x in hx:
            for y in hy:
                bx = hx_ + x * ca - y * sa
                by = hy_ + x * sa + y * ca
                pos.append((bx, by, C.CHASSIS_T + 3))
        ok &= probe(chassis, pos, "-z", 6.5, f"chassis tab({leg})")

    # 腕ヨーサーボ (MICRO) のタブボス: 設計上最も肉厚マージンが薄い箇所
    # (開口との隙間 2.1mm → 長円ボスで逃がし。make_chassis.py 参照)。
    # ボス上面 z = CHASSIS_T + ARM_BOSS_H (脚の +3 とは高さが異なる)
    PA = C.ARM_SERVO
    cxa = PA["L"] / 2 - PA["SHAFT_OFF"]
    arm_hys = (-cxa - PA["HOLE_PITCH"] / 2, -cxa + PA["HOLE_PITCH"] / 2)
    pos = [(side * C.ARM_MOUNT_XY[0], C.ARM_MOUNT_XY[1] + hy_,
            C.CHASSIS_T + C.ARM_BOSS_H)
           for side in (-1, 1) for hy_ in arm_hys]
    ok &= probe(chassis, pos, "-z", 6.5, "chassis arm tab")

    # ---- ESP32 マウント検証は撤去 (2026-08-21)。
    # 旧検証は 2 つの盲点で「不成立のマウント」を OK にしていた:
    #   (1) プローブ点がボス内部の充填率のみ — 浮遊島でも 100% になる。
    #       取付機能ボスは「ボス直下のプレート実体 (z 1-3)」も見なければ
    #       浮きを検出できない
    #   (2) クリアランス検査が north 側ペア vs 前脚/腕開口だけで、south 側
    #       ペア vs 後脚開口 (実際に浮いていた側) を見ていなかった
    # マウント自体を make_chassis.py から撤去済み (基板はテープ留め運用、
    # 恒久マウントは別途設計 — 同ファイルの撤去コメント参照)。chassis の
    # 浮遊島は make_chassis.chassis() 末尾の単一ボディ assert が恒常検出する。

    print(f"\nresult: {'OK' if ok else 'NG'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
