#!/usr/bin/env python3
"""頭部中央寄せタスク (2026-07-31) の上面図 before/after を生成する。

旧 ARM_MOUNT_HUB_Y=12.0 (前寄り) と新 ARM_MOUNT_HUB_Y=0.0 (シャーシ中心)
それぞれについて、chassis / 頭部クラスタ (Head_Bottom/Head_Top の合成
bbox 円で簡略表示) / pod_neck / 頭部逃がしカット後の pod_neck 断面を
上面図 (X-Y, +Z を見下ろす) で比較する。頭中心=シャーシ中心のオフセット
を数値注記する。

実行: .venv/bin/python tools/render_head_center.py
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


def draw_panel(ax, hub_y, zb, pod_neck_tm, title):
    # chassis 外形 (円, タブ突起は簡略化して省略)
    ax.add_patch(Circle((0, 0), C.CHASSIS_D / 2, fill=False, ec="#888888",
                         lw=1.2, ls="--", label="chassis 外形 (プレート本体)"))
    ax.plot(0, 0, "+", color="black", ms=14, mew=2, zorder=5)
    ax.annotate("シャーシ中心 (0,0)", (0, 0), textcoords="offset points",
                xytext=(6, -14), fontsize=8, color="black")

    # 脚ヨー軸位置
    for name, (x, y) in C.HIPS.items():
        ax.plot(x, y, "o", color="#5588cc", ms=5)
        ax.annotate(name, (x, y), textcoords="offset points", xytext=(4, 4),
                    fontsize=7, color="#5588cc")

    # 頭部クラスタ (Head_Bottom/Head_Top) の合成 XY 範囲 (bbox から円で簡略表示)
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

    # シャーシ中心からのオフセット注記 (縦線)
    if abs(hub_y) > 0.01:
        ax.annotate("", xy=(0, hub_y), xytext=(0, 0),
                     arrowprops=dict(arrowstyle="<->", color="red", lw=1.2))
        ax.annotate(f"オフセット {hub_y:+.1f}mm", (2, hub_y / 2), fontsize=8, color="red")
    else:
        ax.annotate("オフセット 0mm (中央)", (2, 0), fontsize=8, color="green")

    # pod_neck (上面図, X-Y 投影の外形のみ簡略表示: convex hull 2D)
    pts = pod_neck_tm.vertices[:, :2]
    from scipy.spatial import ConvexHull
    hull = ConvexHull(pts)
    hull_pts = pts[hull.vertices]
    ax.fill(hull_pts[:, 0], hull_pts[:, 1], color="#ffaa55", alpha=0.6,
            ec="#cc6600", lw=1.0, label="pod_neck (上面投影)")

    ax.set_xlim(-90, 90)
    ax.set_ylim(-120, 90)
    ax.set_aspect("equal")
    ax.set_title(title, fontsize=11)
    ax.set_xlabel("X [mm]")
    ax.set_ylabel("Y [mm] (+Y=前方)")
    ax.grid(alpha=0.2)


def main():
    import japanize_matplotlib  # noqa: F401 (日本語タイトル文字化け対策)

    body_h = 105.0
    zb = body_h + C.HIP_DROP

    fig, axes = plt.subplots(1, 2, figsize=(13, 7))

    # --- before: 旧 ARM_MOUNT_HUB_Y=12.0, 旧 pod_neck (頭部逃がしカット無し) ---
    old_hub_y = 12.0
    # 旧 pod_neck 形状 (頭部逃がしカット導入前) を再現するため、
    # _head_relief_cutter() を空 (体積0) の Manifold に差し替えて呼ぶ
    orig_cutter = MC._head_relief_cutter
    from manifold3d import Manifold
    MC._head_relief_cutter = lambda: Manifold()
    old_nk = to_tm(MC.pod_neck())
    MC._head_relief_cutter = orig_cutter
    draw_panel(axes[0], old_hub_y, zb, old_nk,
               f"BEFORE: ARM_MOUNT_HUB_Y = {old_hub_y:+.1f}mm (旧, 根拠なしの前寄り)\n"
               f"pod_neck 頭部逃がしカット無し")

    # --- after: 新 ARM_MOUNT_HUB_Y=0.0, 新 pod_neck (頭部逃がしカット込み) ---
    new_hub_y = C.ARM_MOUNT_HUB_Y
    new_nk = to_tm(MC.pod_neck())
    draw_panel(axes[1], new_hub_y, zb, new_nk,
               f"AFTER: ARM_MOUNT_HUB_Y = {new_hub_y:+.1f}mm (現在値)\n"
               f"pod_neck 頭部逃がしカット込み (HEAD_RELIEF_PROTECT_H="
               f"{MC.HEAD_RELIEF_PROTECT_H:.1f}mm)")

    axes[0].legend(loc="lower left", fontsize=7)
    axes[1].legend(loc="lower left", fontsize=7)
    fig.suptitle(f"頭部位置比較 (ARM_MOUNT_HUB_Y {old_hub_y:g}→{new_hub_y:g}) 上面図", fontsize=13)
    fig.tight_layout()
    out = DOCS / "vis_head_center_before_after.png"
    fig.savefig(out, dpi=150)
    print(f"saved {out}")


if __name__ == "__main__":
    sys.exit(main())
