#!/usr/bin/env python3
"""体高115mm保持時の実メッシュ足部を側面投影。CAD/STLは編集しない。"""
from pathlib import Path
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
from matplotlib.collections import PolyCollection
import numpy as np
import trimesh

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hardware/src"))
import config as C
import kit_assembly as K
import sim_gait as G


def main():
    font_path = Path("/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc")
    if font_path.exists():
        from matplotlib import font_manager
        font_manager.fontManager.addfont(str(font_path))
        plt.rcParams["font.family"] = FontProperties(fname=font_path).get_name()
    plt.rcParams.update({"font.size": 11, "axes.unicode_minus": False})
    fig, axes = plt.subplots(1, 2, figsize=(13, 7), sharey=True)
    colors = {"トゥ（PLA）": "#d8664b", "硬い甲（PLA）": "#9299a1", "隠し足裏（TPU）": "#177d9c"}
    placements = K.load_placements()
    for ax, li, title in zip(axes, (0, 3), ("前脚（FR）", "後脚（RR）")):
        leg = G._LEGS[li]
        _, pitch, knee = np.radians(G.leg_ik(*G.foot_target(li, 0, 0, 0, 0, body_h=115, holding=True)))
        tilt = pitch + knee
        base_z = 115 - C.FEMUR_LEN * np.sin(pitch) - C.TIBIA_LEN * np.cos(tilt)
        toe_meshes = [K.oriented_mesh(p) for p in K.by_link(placements, "leg_foot_bored")
                      if p.instance.startswith(leg + "_")]
        groups = {"トゥ（PLA）": trimesh.util.concatenate(toe_meshes),
                  "硬い甲（PLA）": trimesh.load(ROOT / "hardware/stl/leg_foot_bored.stl"),
                  "隠し足裏（TPU）": trimesh.load(ROOT / "hardware/stl/foot_pad.stl")}
        minima = {}
        for name, mesh in groups.items():
            v = mesh.vertices
            points = np.c_[v[:, 0] * np.cos(tilt) + v[:, 2] * np.sin(tilt),
                           -v[:, 0] * np.sin(tilt) + v[:, 2] * np.cos(tilt) + base_z]
            polygons = points[mesh.faces]
            ax.add_collection(PolyCollection(polygons, facecolor=colors[name], edgecolor="none", label=name))
            minima[name] = float(points[:, 1].min())
        tz, pz = minima["トゥ（PLA）"], minima["隠し足裏（TPU）"]
        delta = pz - tz
        ax.axhline(0, color="#282d32", lw=1.3, ls="--")
        ax.text(-31, -1.2, "計算上の床 z=0", va="top", fontsize=10, color="#444")
        ax.annotate("", xy=(27, pz), xytext=(27, tz),
                    arrowprops=dict(arrowstyle="<->", color="#a22d21", lw=1.5))
        ax.text(28.7, (tz + pz) / 2, f"{delta:.2f} mm", color="#a22d21", va="center", fontweight="bold")
        ax.plot([4, 28], [pz, pz], color=colors["隠し足裏（TPU）"], lw=0.8, ls=":")
        ax.plot([15, 28], [tz, tz], color=colors["トゥ（PLA）"], lw=0.8, ls=":")
        ax.text(-31, -11.2, f"最低高さ：トゥ {tz:+.2f} / 甲 {minima['硬い甲（PLA）']:+.2f} / TPU {pz:+.2f} mm",
                fontsize=10)
        ax.text(-31, -14.3, f"局所-Z方向に下げる場合の幾何上の下限：{delta / np.cos(tilt):.2f} mm",
                fontsize=10)
        ax.set_title(f"{title}　脛の傾き {np.degrees(tilt):.2f}°", loc="left", fontweight="bold", pad=14)
        ax.set_xlim(-34, 46); ax.set_ylim(-17, 30); ax.set_aspect("equal")
        ax.set_xlabel("脚の長手方向（足の原点基準、mm）")
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(alpha=0.15)
    axes[0].set_ylabel("計算上の床からの高さ z（mm）")
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", bbox_to_anchor=(0.5, 0.865), ncol=3, frameon=False)
    fig.suptitle("現行の足裏は、静止中もトゥより上にある", x=0.07, y=0.975, ha="left", fontsize=20, fontweight="bold")
    fig.text(0.07, 0.915, "体高115mm・無移動保持／現行STLと組立座標の側面投影（左右の同形脚は省略）", fontsize=12)
    fig.text(0.07, 0.08, "赤いトゥが先に床へ接触すると、青いTPU足裏には上記の隙間が残る。", fontsize=12, fontweight="bold")
    fig.text(0.07, 0.037, "図は剛体幾何の確認用。延長量には安全代・変形・取付構造を含まない。トゥ接着位置と実機荷重は未確認。", fontsize=10, color="#555")
    fig.subplots_adjust(top=0.77, bottom=0.17, left=0.075, right=0.98, wspace=0.14)
    out = ROOT / "docs/audits/20260905/foot_contact_115mm.png"
    fig.savefig(out, dpi=180, facecolor="white")
    print(out)


if __name__ == "__main__":
    main()
