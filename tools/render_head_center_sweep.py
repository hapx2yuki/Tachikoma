#!/usr/bin/env python3
"""境界スイープタスク (2026-07-31) の上面図 3態比較。

旧 ARM_MOUNT_HUB_Y=12.0 (根拠なしの前寄り) / 参考 hub_y=0.0 (完全中央,
物理的に不可能と判明) / 採用値 hub_y=11.0 (実現可能な最中央値) の3つを
並べ、chassis / 頭部クラスタ (bbox円で簡略表示) / pod_neck (逃がしカット
込み, 各 hub_y に応じて動的に再計算) を上面図 (X-Y) で比較する。

実行: .venv/bin/python tools/render_head_center_sweep.py
(新規プロセスで実行すること — make_visuals._MESH_CACHE に mtime チェック
無しのため、同一プロセス内で config を使い回すと古いメッシュを再利用する
おそれがある。本スクリプトは make_visuals を使わず make_chassis を直接
呼ぶため影響は限定的だが、規律として新規プロセス実行を踏襲する)
"""
import sys
from pathlib import Path

import numpy as np
import trimesh
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Circle  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hardware" / "src"))
sys.path.insert(0, str(ROOT / "tools"))
import config as C  # noqa: E402
import make_chassis as MC  # noqa: E402
import kit_assembly as KIT  # noqa: E402

MODEL = ROOT / "model"
STL = ROOT / "hardware" / "stl"
DOCS = ROOT / "docs"
HEAD_TOP_Z_OFFSET = 57.7


def rot_z(deg):
    t = np.radians(deg)
    m = np.eye(4)
    m[0, 0], m[0, 1], m[1, 0], m[1, 1] = np.cos(t), -np.sin(t), np.sin(t), np.cos(t)
    return m


def load_kit(name):
    render_name = KIT.STL_RENDER_OVERRIDE.get(name, name)
    if render_name in KIT.PRESCALED:
        return trimesh.load(STL / f"{render_name}.stl")
    m = trimesh.load(MODEL / f"{render_name}.stl")
    m.apply_translation(-(m.bounds[0] + m.bounds[1]) / 2)
    m.apply_scale(C.SCALE)
    return m


def to_tm(manifold):
    mesh = manifold.to_mesh()
    return trimesh.Trimesh(vertices=np.array(mesh.vert_properties[:, :3]),
                            faces=np.array(mesh.tri_verts), process=False)


def draw_panel(ax, hub_y, zb, pod_neck_tm, title, arm_mount_xy=None):
    ax.add_patch(Circle((0, 0), C.CHASSIS_D / 2, fill=False, ec="#888888",
                         lw=1.2, ls="--", label="chassis 外形 (プレート本体)"))
    ax.plot(0, 0, "+", color="black", ms=14, mew=2, zorder=5)
    ax.annotate("シャーシ中心 (0,0)", (0, 0), textcoords="offset points",
                xytext=(6, -14), fontsize=8, color="black")

    for name, (x, y) in C.HIPS.items():
        ax.plot(x, y, "o", color="#5588cc", ms=5)
        ax.annotate(name, (x, y), textcoords="offset points", xytext=(4, 4),
                    fontsize=7, color="#5588cc")

    # 腕肩ヨー軸位置 (ARM_MOUNT_HUB_Y に連動して動く)
    if arm_mount_xy is not None:
        mx, my = arm_mount_xy
        for s in (-1, 1):
            ax.plot(s * mx, my, "^", color="#cc3366", ms=8, zorder=5)
        ax.annotate(f"腕マウント y={my:.1f}", (mx, my), textcoords="offset points",
                    xytext=(6, 6), fontsize=7, color="#cc3366")

    hb = load_kit("Head_Bottom_Blue")
    hb.apply_transform(rot_z(180))
    hb.apply_translation((0, hub_y, zb - 3))
    ht = load_kit("Head_Top_Blue")
    ht.apply_transform(rot_z(180))
    ht.apply_translation((0, hub_y, zb + HEAD_TOP_Z_OFFSET))
    r_head = max(hb.bounds[1, 0] - hb.bounds[0, 0], hb.bounds[1, 1] - hb.bounds[0, 1],
                 ht.bounds[1, 0] - ht.bounds[0, 0], ht.bounds[1, 1] - ht.bounds[0, 1]) / 2
    ax.add_patch(Circle((0, hub_y), r_head, fill=True, fc="#cfe3ff", ec="#3366aa",
                         lw=1.5, alpha=0.5, label="頭部クラスタ外形 (概略, bbox円)"))
    ax.plot(0, hub_y, "x", color="#3366aa", ms=12, mew=2, zorder=5)
    ax.annotate(f"頭部中心 y={hub_y:+.1f}mm", (0, hub_y), textcoords="offset points",
                xytext=(8, 6), fontsize=9, color="#3366aa", weight="bold")

    if abs(hub_y) > 0.01:
        ax.annotate("", xy=(0, hub_y), xytext=(0, 0),
                     arrowprops=dict(arrowstyle="<->", color="red", lw=1.2))
        ax.annotate(f"オフセット {hub_y:+.1f}mm", (2, hub_y / 2), fontsize=8, color="red")
    else:
        ax.annotate("オフセット 0mm (中央)", (2, 0), fontsize=8, color="green")

    pts = pod_neck_tm.vertices[:, :2]
    from scipy.spatial import ConvexHull
    hull = ConvexHull(pts)
    hull_pts = pts[hull.vertices]
    ax.fill(hull_pts[:, 0], hull_pts[:, 1], color="#ffaa55", alpha=0.6,
            ec="#cc6600", lw=1.0, label="pod_neck (上面投影)")

    ax.set_xlim(-90, 90)
    ax.set_ylim(-120, 90)
    ax.set_aspect("equal")
    ax.set_title(title, fontsize=10)
    ax.set_xlabel("X [mm]")
    ax.set_ylabel("Y [mm] (+Y=前方)")
    ax.grid(alpha=0.2)


def arm_mount_xy_for(hub_y):
    return (C.ARM_MOUNT_R * np.sin(np.radians(C.ARM_MOUNT_YAW_DEG)),
            hub_y + C.ARM_MOUNT_R * np.cos(np.radians(C.ARM_MOUNT_YAW_DEG)))


def main():
    import japanize_matplotlib  # noqa: F401

    body_h = 105.0
    zb = body_h + C.HIP_DROP

    fig, axes = plt.subplots(1, 3, figsize=(18, 7))

    # --- 旧 hub_y=12.0 (根拠なしの前寄り, 逃がしカット導入前の kit 形状) ---
    orig_hub_y = C.ARM_MOUNT_HUB_Y  # 現在値 (=11.0) を保存
    orig_cutter = MC._head_relief_cutter
    from manifold3d import Manifold
    MC._head_relief_cutter = lambda: Manifold()
    old_nk = to_tm(MC.pod_neck())
    MC._head_relief_cutter = orig_cutter
    draw_panel(axes[0], 12.0, zb, old_nk,
               "旧 hub_y = +12.0mm (根拠なしの前寄り)\n逃がしカット無し",
               arm_mount_xy=arm_mount_xy_for(12.0))

    # --- 参考: hub_y=0.0 (完全中央, 物理的に不可能と判明) ---
    C.ARM_MOUNT_HUB_Y = 0.0
    ref_nk = to_tm(MC.pod_neck())
    draw_panel(axes[1], 0.0, zb, ref_nk,
               "参考 hub_y = 0.0mm (完全中央)\n"
               "ARM_MOUNT_XYが前脚と静的干渉 (NG, 本タスクで不採用)",
               arm_mount_xy=arm_mount_xy_for(0.0))

    # --- 採用: hub_y=11.0 (実現可能な最中央値) ---
    C.ARM_MOUNT_HUB_Y = orig_hub_y  # =11.0
    new_nk = to_tm(MC.pod_neck())
    draw_panel(axes[2], orig_hub_y, zb, new_nk,
               f"採用 hub_y = {orig_hub_y:+.1f}mm (実現可能な最中央値)\n"
               f"逃がしカット込み (PROTECT_H={MC.HEAD_RELIEF_PROTECT_H:.1f}mm) — 全チェッカーPASS",
               arm_mount_xy=arm_mount_xy_for(orig_hub_y))

    for ax in axes:
        ax.legend(loc="lower left", fontsize=6)
    fig.suptitle("ARM_MOUNT_HUB_Y 境界スイープ: 旧12.0 / 参考0.0(不可) / 採用11.0 の上面図比較",
                 fontsize=13)
    fig.tight_layout()
    out = DOCS / "vis_head_center_sweep3.png"
    fig.savefig(out, dpi=150)
    print(f"saved {out}")


if __name__ == "__main__":
    sys.exit(main())
