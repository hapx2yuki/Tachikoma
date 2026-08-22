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


def build():
    # ヨー軸原点は股ピッチ軸より 27.6mm 上 (STD ヨーサーボのホーンスタック分)
    HIP_DROP = C.HIP_DROP
    meshes = []
    chassis = trimesh.load(STL / "chassis.stl")
    chassis.apply_transform(trans(0, 0, BODY_H + HIP_DROP))
    meshes.append(("#8899aa", chassis))

    # 腕 (READY 姿勢)
    from make_visuals import arm_meshes, ARM_READY
    for side in (1, -1):
        for m, c, a in arm_meshes(side, ARM_READY, BODY_H + HIP_DROP,
                                  body_h=BODY_H):
            meshes.append((c, m))

    ang = leg_ik(STANCE_R, 0.0, -BODY_H)
    assert ang, "stance unreachable"
    yaw_d, pitch_d, knee_d = ang
    for leg in range(4):
        mnt = np.degrees(MOUNT[leg])
        base = trans(ORIGIN[leg][0], ORIGIN[leg][1], BODY_H) @ rot(mnt + yaw_d, "z")
        cox = trimesh.load(STL / "coxa_bracket.stl")
        cox.apply_transform(base)
        meshes.append(("#5577cc", cox))
        fem = trimesh.load(STL / "femur_link.stl")
        T_hip = base @ trans(C.COXA_LEN, 0, 0) @ rot(pitch_d, "y")
        fem.apply_transform(T_hip)
        meshes.append(("#cc7755", fem))
        tib = trimesh.load(STL / "tibia_link.stl")
        T_knee = T_hip @ trans(C.FEMUR_LEN, 0, 0) @ rot(knee_d, "y")
        tib.apply_transform(T_knee)
        meshes.append(("#55aa77", tib))

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
        ax.set_zlim(0, 2 * r)
        ax.set_box_aspect([1, 1, 1]); ax.axis("off")
        ax.view_init(elev=elev, azim=azim)
    fig.suptitle("Tachikoma walker — skeleton stance (body height 115 mm)")
    fig.tight_layout()
    out = ROOT / "docs" / "preview_robot.png"
    fig.savefig(out, dpi=110, facecolor="white")
    print(f"saved {out}")
    print(f"stance angles: yaw={yaw_d:.1f} pitch={pitch_d:.1f} knee={knee_d:.1f}")


if __name__ == "__main__":
    build()
