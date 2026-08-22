#!/usr/bin/env python3
"""生成 STL のプレビュー画像を matplotlib でレンダリングする。

usage: .venv/bin/python tools/preview.py out.png part1.stl [part2.stl ...]
       (複数指定時は 1 枚に並べる。--assembly で重ね合わせ表示)
"""
import sys
from pathlib import Path

import numpy as np
import trimesh
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection

VIEWS = [(30, -60), (15, 20), (75, -90)]


def draw(ax, mesh: trimesh.Trimesh, color="#5577cc", alpha=1.0):
    tri = mesh.vertices[mesh.faces]
    pc = Poly3DCollection(tri, alpha=alpha, facecolor=color, edgecolor="none")
    # 簡易シェーディング
    n = mesh.face_normals
    light = np.array([0.4, -0.6, 0.7])
    light = light / np.linalg.norm(light)
    lum = 0.45 + 0.55 * np.clip(n @ light, 0, 1)
    base = np.array(matplotlib.colors.to_rgb(color))
    pc.set_facecolor(np.c_[lum[:, None] * base[None, :], np.full(len(lum), alpha)])
    ax.add_collection3d(pc)


def setup_axes(ax, bounds):
    lo, hi = bounds
    c = (lo + hi) / 2
    r = float(max(hi - lo)) / 2 * 1.1
    ax.set_xlim(c[0] - r, c[0] + r)
    ax.set_ylim(c[1] - r, c[1] + r)
    ax.set_zlim(c[2] - r, c[2] + r)
    ax.set_box_aspect([1, 1, 1])
    ax.axis("off")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    assembly = "--assembly" in sys.argv
    out, stls = args[0], args[1:]
    colors = ["#5577cc", "#cc7755", "#55aa77", "#aa55aa", "#888888", "#ccaa33"]

    if assembly:
        meshes = [trimesh.load(p) for p in stls]
        allb = np.array([m.bounds for m in meshes])
        bounds = (allb[:, 0].min(axis=0), allb[:, 1].max(axis=0))
        fig = plt.figure(figsize=(5 * len(VIEWS), 5))
        for i, (elev, azim) in enumerate(VIEWS):
            ax = fig.add_subplot(1, len(VIEWS), i + 1, projection="3d")
            for j, m in enumerate(meshes):
                draw(ax, m, colors[j % len(colors)])
            setup_axes(ax, bounds)
            ax.view_init(elev=elev, azim=azim)
    else:
        fig = plt.figure(figsize=(5 * len(VIEWS), 5 * len(stls)))
        for r, p in enumerate(stls):
            m = trimesh.load(p)
            for i, (elev, azim) in enumerate(VIEWS):
                ax = fig.add_subplot(len(stls), len(VIEWS), r * len(VIEWS) + i + 1,
                                     projection="3d")
                draw(ax, m, colors[r % len(colors)])
                setup_axes(ax, (m.bounds[0], m.bounds[1]))
                ax.view_init(elev=elev, azim=azim)
                if i == 0:
                    ax.set_title(Path(p).stem, fontsize=10)
    fig.tight_layout()
    fig.savefig(out, dpi=110, facecolor="white")
    print(f"saved {out}")


if __name__ == "__main__":
    main()
