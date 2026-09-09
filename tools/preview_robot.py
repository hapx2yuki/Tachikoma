#!/usr/bin/env python3
"""シャーシ + 4 脚のフルアセンブリプレビュー (立位姿勢, 体高115mm 相当)。"""
import sys
from pathlib import Path

import numpy as np
import trimesh
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hardware" / "src"))
import config as C  # noqa: E402
sys.path.insert(0, str(ROOT / "tools"))
from sim_gait import leg_ik, ORIGIN, MOUNT, STANCE_R, BODY_H  # noqa: E402

STL = ROOT / "hardware" / "stl"


def rot(deg, axis):
    t = np.radians(deg)
    c, s = np.cos(t), np.sin(t)
    m = np.eye(4)
    i = {"x": (1, 2), "y": (0, 2), "z": (0, 1)}[axis]
    m[i[0], i[0]] = c; m[i[1], i[1]] = c
    if axis == "y":
        m[i[0], i[1]] = s; m[i[1], i[0]] = -s
    else:
        m[i[0], i[1]] = -s; m[i[1], i[0]] = s
    return m


def trans(x, y, z):
    m = np.eye(4)
    m[:3, 3] = [x, y, z]
    return m


def stance_meshes():
    """ミラー脚と重心補正を含む現行の保持姿勢を使う。"""
    from make_visuals import robot_meshes, ARM_READY
    return [(c, m) for m, c, _ in robot_meshes(
        0.0, 0.0, 0.0, 0.0, BODY_H, arms=ARM_READY, holding=True)]


def build(out=None):
    meshes = stance_meshes()
    fig = plt.figure(figsize=(15, 5.5))
    light = np.array([0.4, -0.6, 0.7]); light /= np.linalg.norm(light)
    for i, (elev, azim) in enumerate([(18, -50), (5, -90), (35, -20)]):
        ax = fig.add_subplot(1, 3, i + 1, projection="3d")
        pts_all = []
        for color, m in meshes:
            tri = m.vertices[m.faces]
            lum = 0.45 + 0.55 * np.clip(m.face_normals @ light, 0, 1)
            base_c = np.array(matplotlib.colors.to_rgb(color))
            pc = Poly3DCollection(tri, facecolor=np.c_[lum[:, None] * base_c[None, :],
                                                       np.ones(len(lum))])
            ax.add_collection3d(pc)
            pts_all.append(m.vertices)
        pts = np.vstack(pts_all)
        cmid = (pts.min(0) + pts.max(0)) / 2
        r = float((pts.max(0) - pts.min(0)).max()) / 2 * 1.02
        ax.set_xlim(cmid[0] - r, cmid[0] + r)
        ax.set_ylim(cmid[1] - r, cmid[1] + r)
        zmin = min(0.0, float(pts[:, 2].min()) - 2.0)
        ax.set_zlim(zmin, zmin + 2 * r)
        ax.set_box_aspect([1, 1, 1]); ax.axis("off")
        ax.view_init(elev=elev, azim=azim)
    fig.suptitle("Tachikoma walker — skeleton stance (body height 115 mm)")
    fig.tight_layout()
    out = Path(out) if out else ROOT / "docs" / "preview_robot.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=110, facecolor="white")
    print(f"saved {out}")
    plt.close(fig)
    print("現行の保持姿勢。接触・トルクは物理シミュレーションで別途検証する。")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    build(parser.parse_args().output)
