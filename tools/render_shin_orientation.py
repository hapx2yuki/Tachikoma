#!/usr/bin/env python3
"""脛シェル (shin_shell) の意匠面 (3ドットモールド/パネルライン) が放射外向き
になっていることを再現可能な形で検証・可視化する (2026-07-31 QA follow-up)。

背景: shell_mod.py の OFFSETS["shin_rotz"]=0.0 の採用根拠として「4脚とも
標準立位で cos_sim=1.000 (ドットモールドの水平面投影が完全に放射外向き)」
という主張が report に記載されていたが、その根拠画像
(docs/vis_shin_orientation_check.png, docs/vis_shin_shell_detail.png) が
使い捨てスクリプトで作られていて再現できず、しかも両画像の mtime が
shell_mod.py の最終保存より約45秒古い (= shell_mod.py の最終版が保存される
"前" に生成されていた) ことが 2026-07-31 レビューで発覚した (本プロジェクト
既知の失敗パターン「修正確認レンダが実は再生成前の画像だった」の再発)。

本スクリプトはその再現可能版。実行のたびに shell_mod.py の現在の内容から
fresh に cos_sim を計算・レンダする (make_visuals.py の _MESH_CACHE を
経由しない独立ロードなので、このスクリプト自体は STL 再生成直後でも常に
最新の shin_shell() を反映する — ただし証拠としての鮮度を保証するのは
「STL 再生成後に新規プロセスでこのスクリプトを実行すること」自体であり、
スクリプトの存在そのものではない点に注意)。

数値根拠 (shell_mod.py OFFSETS["shin_rotz"] のコメント参照):
  raw Leg_Shin_Blue_x4.stl 上の 3 ドットモールド面法線 (sharp-edge抽出+
  面積加重平均で実測) = (0.852, -0.001, 0.523) -- シェル構築チェーン
  (scale→translate→LOOP_RELIEF切除→shin_rotz回転) のうち法線の向きに
  効くのは等方 scale (無効) と shin_rotz 回転のみ。

出力:
  docs/vis_shin_orientation_check.png -- 4脚を上から見て、股ヨー放射方向
    (灰色破線) と実測ドット法線の水平面投影 (脚色の実線矢印) を重ねた図。
    矢印が破線とほぼ重なっていれば「外向き」が視覚的に確認できる。
  docs/vis_shin_shell_detail.png -- FR脚 (ミラー) の shin_shell を標準立位
    姿勢で描画し、ドットモールド推定位置にマーカーを打ったクローズアップ
    (2アングル)。
"""
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
sys.path.insert(0, str(ROOT / "tools"))
import config as C  # noqa: E402
import shell_mod as SM  # noqa: E402
from sim_gait import (leg_ik, ORIGIN, MOUNT, STANCE, STANCE_R,  # noqa: E402
                      BODY_H, foot_target, _LEGS as LEG_NAMES)

MODEL = ROOT / "model"
MIRROR_LEGS = {"FR", "RL"}
LEG_COLORS = {"FR": "#d24", "FL": "#28c", "RL": "#2a2", "RR": "#b6b"}

# raw Leg_Shin_Blue_x4.stl 上の 3 ドットモールド面法線・代表点
# (shell_mod.py OFFSETS["shin_rotz"] コメント参照, raw/未スケール座標)
RAW_DOT_NORMAL = np.array([0.852, -0.001, 0.523])
RAW_DOT_NORMAL /= np.linalg.norm(RAW_DOT_NORMAL)
RAW_DOT_CENTER = np.array([11.87, 0.0, 40.19])


def rot_y(deg):
    t = np.radians(deg)
    m = np.eye(3)
    m[0, 0], m[0, 2] = np.cos(t), np.sin(t)
    m[2, 0], m[2, 2] = -np.sin(t), np.cos(t)
    return m


def rot_z(deg):
    t = np.radians(deg)
    m = np.eye(3)
    m[0, 0], m[0, 1], m[1, 0], m[1, 1] = np.cos(t), -np.sin(t), np.sin(t), np.cos(t)
    return m


def shin_build_translate():
    """shin_shell() 冒頭の平行移動ベクトル (scale後raw bbox中心→原点, 上端→SHIN_TOP_Z)。"""
    tm = trimesh.load(MODEL / "Leg_Shin_Blue_x4.stl")
    tm.apply_scale(C.SCALE)
    lo, hi = tm.bounds
    ox, oy = SM.OFFSETS["shin_xy"]
    cx, cy = (lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2
    return np.array([-cx + ox, -cy + oy, -hi[2] + SM.SHIN_TOP_Z])


def local_frame_point(raw_pt):
    """raw (未スケール) 座標点を shin_shell() 自身の構築フレーム
    (=shin_rotz 適用後, ミラー前) へ変換する。"""
    scaled = raw_pt * C.SCALE
    pre_rot = scaled + shin_build_translate()
    return rot_z(SM.OFFSETS["shin_rotz"]) @ pre_rot


def local_frame_normal(raw_n):
    """raw 座標の法線ベクトルを shin_shell() 構築フレーム (shin_rotz 適用後,
    ミラー前) へ変換する (並進は無効, scale は等方なので無効, 回転のみ)。"""
    return rot_z(SM.OFFSETS["shin_rotz"]) @ raw_n


def stance_pose(leg_idx, body_h=BODY_H):
    """firmwareの保持姿勢。重心用のSTANCE_OFFを含める。"""
    x, y, z = foot_target(leg_idx, 0.0, 0.0, 0.0, 0.0, holding=True)
    a = leg_ik(x, y, z + BODY_H - body_h)
    assert a is not None, f"leg {leg_idx}: 保持姿勢IK失敗"
    return a


def dot_normal_world_xy(leg_idx, yaw_d, pitch_d, knee_d):
    """そのリーグの立位姿勢での、ドットモールド法線の world 水平面投影 (単位ベクトル)。"""
    leg_name = LEG_NAMES[leg_idx]
    mirror = leg_name in MIRROR_LEGS
    n_local = local_frame_normal(RAW_DOT_NORMAL)
    if mirror:
        n_local = n_local * np.array([1.0, -1.0, 1.0])  # Y ミラー (shin_shell_m 相当)
    # T_knee の回転成分 = rot_y(pitch) . rot_y(knee) (並進は法線に無効)
    n_after_knee = rot_y(pitch_d) @ (rot_y(knee_d) @ n_local)
    mnt_deg = np.degrees(MOUNT[leg_idx])
    n_world = rot_z(mnt_deg + yaw_d) @ n_after_knee
    xy = n_world[:2]
    return xy / np.linalg.norm(xy)


def radial_dir(leg_idx, yaw_d):
    ang = np.radians(np.degrees(MOUNT[leg_idx]) + yaw_d)
    return np.array([np.cos(ang), np.sin(ang)])


def main(output_dir=None):
    output_dir = Path(output_dir) if output_dir else ROOT / "docs"
    output_dir.mkdir(parents=True, exist_ok=True)
    print("[render_shin_orientation] shin_rotz =", SM.OFFSETS["shin_rotz"],
          " (shell_mod.py 現在値, fresh import)")
    results = {}
    for i, leg_name in enumerate(LEG_NAMES):
        yaw_d, pitch_d, knee_d = stance_pose(i)
        n_xy = dot_normal_world_xy(i, yaw_d, pitch_d, knee_d)
        r_xy = radial_dir(i, yaw_d)
        cos_sim = float(np.dot(n_xy, r_xy))
        results[leg_name] = (yaw_d, pitch_d, knee_d, n_xy, r_xy, cos_sim)
        print(f"  {leg_name}: stance(yaw={yaw_d:.2f},pitch={pitch_d:.2f},"
              f"knee={knee_d:.2f})  cos_sim(dot-normal, radial-outward) = {cos_sim:.4f}")
    worst = min(v[5] for v in results.values())
    print(f"  worst cos_sim = {worst:.4f}  ({'OK (放射外向き)' if worst > 0.999 else 'NG'})")

    # ---------------------------------------------------------- 上面図
    fig, ax = plt.subplots(figsize=(6.5, 6.5))
    R = STANCE_R + 40
    ax.add_patch(plt.Circle((0, 0), 45, facecolor="#dde3ea", edgecolor="none", zorder=0))
    for i, leg_name in enumerate(LEG_NAMES):
        yaw_d, pitch_d, knee_d, n_xy, r_xy, cos_sim = results[leg_name]
        hx, hy = ORIGIN[i]
        col = LEG_COLORS[leg_name]
        ax.plot(hx, hy, "o", color=col, ms=8, zorder=3)
        ax.annotate(f"{leg_name}\ncos={cos_sim:.3f}", (hx, hy),
                    textcoords="offset points", xytext=(10, 6), fontsize=9, color=col)
        L = 34
        # 放射外向き (灰色破線)
        ax.annotate("", xy=(hx + r_xy[0] * L, hy + r_xy[1] * L), xytext=(hx, hy),
                    arrowprops=dict(arrowstyle="-|>", color="#888", lw=1.6, ls=(0, (4, 2))),
                    zorder=2)
        # ドット法線の水平面投影 (実線, 脚色)
        ax.annotate("", xy=(hx + n_xy[0] * L, hy + n_xy[1] * L), xytext=(hx, hy),
                    arrowprops=dict(arrowstyle="-|>", color=col, lw=2.4), zorder=4)
    ax.plot(0, 0, "k+", ms=10)
    ax.set_xlim(-R, R); ax.set_ylim(-R, R)
    ax.set_aspect("equal"); ax.axis("off")
    ax.set_title("shin_shell 意匠面 (ドットモールド) の向き確認 -- 上面図\n"
                  "実線=ドット法線の水平投影, 破線=放射外向き方位 (両者が重なれば OK)",
                  fontsize=10)
    fig.tight_layout()
    out1 = output_dir / "vis_shin_orientation_check.png"
    fig.savefig(out1, dpi=130, facecolor="white")
    plt.close(fig)
    print(f"  saved {out1}")

    # ---------------------------------------------------------- detail 図
    leg_name = "FR"
    i = LEG_NAMES.index(leg_name)
    yaw_d, pitch_d, knee_d, n_xy, r_xy, cos_sim = results[leg_name]
    mirror = leg_name in MIRROR_LEGS
    tm = SM.to_trimesh(SM.shin_shell()) if hasattr(SM, "to_trimesh") else None
    if tm is None:
        mesh = SM.shin_shell().to_mesh()
        tm = trimesh.Trimesh(vertices=np.array(mesh.vert_properties[:, :3]),
                             faces=np.array(mesh.tri_verts), process=False)
    if mirror:
        tm = tm.copy()
        tm.vertices[:, 1] *= -1.0
        tm.faces = tm.faces[:, ::-1]

    def trans(x, y, z):
        m = np.eye(4)
        m[:3, 3] = [x, y, z]
        return m

    def rot4(deg, axis):
        m = np.eye(4)
        m[:3, :3] = rot_y(deg) if axis == "y" else rot_z(deg)
        return m

    T_hip = trans(C.COXA_LEN, 0, 0) @ rot4(pitch_d, "y")
    T_knee = T_hip @ trans(C.FEMUR_LEN, 0, 0) @ rot4(knee_d, "y")
    tm_world = tm.copy()
    tm_world.apply_transform(T_knee)

    dot_local = local_frame_point(RAW_DOT_CENTER)
    if mirror:
        dot_local = dot_local * np.array([1.0, -1.0, 1.0])
    dot_world = (T_knee @ np.array([*dot_local, 1.0]))[:3]
    normal_world3 = rot_y(pitch_d) @ (rot_y(knee_d) @ (
        local_frame_normal(RAW_DOT_NORMAL) * (np.array([1.0, -1.0, 1.0]) if mirror else 1.0)))

    LIGHT = np.array([0.4, -0.6, 0.7]) / np.linalg.norm([0.4, -0.6, 0.7])

    fig = plt.figure(figsize=(11, 5.5))
    for k, (elev, azim, ttl) in enumerate([
            (18, -60, "斜め (外側面が見える角度)"),
            (75, -90, "ほぼ真上 (放射外向きを確認)")]):
        ax = fig.add_subplot(1, 2, k + 1, projection="3d")
        tri = tm_world.vertices[tm_world.faces]
        lum = 0.45 + 0.55 * np.clip(tm_world.face_normals @ LIGHT, 0, 1)
        base = np.array(matplotlib.colors.to_rgb("#3b62c4"))
        fc = np.c_[lum[:, None] * base[None, :], np.full(len(lum), 1.0)]
        ax.add_collection3d(Poly3DCollection(tri, facecolor=fc, edgecolor="none"))
        ax.scatter(*dot_world, color="#ff2222", s=60, depthshade=False, zorder=5)
        arrow_end = dot_world + normal_world3 * 28
        ax.plot(*zip(dot_world, arrow_end), color="#ff2222", lw=2.5)
        pts = tm_world.vertices
        lo, hi = pts.min(0), pts.max(0)
        c = (lo + hi) / 2
        r = float((hi - lo).max()) / 2 * 1.15
        ax.set_xlim(c[0] - r, c[0] + r); ax.set_ylim(c[1] - r, c[1] + r)
        ax.set_zlim(c[2] - r, c[2] + r)
        ax.set_box_aspect([1, 1, 1])
        ax.view_init(elev=elev, azim=azim)
        ax.axis("off")
        ax.set_title(ttl, fontsize=10)
    fig.suptitle(f"shin_shell ({leg_name}, shin_rotz={SM.OFFSETS['shin_rotz']:.0f}deg) "
                 f"ドットモールド (赤点+矢印=推定法線) の向き確認\n"
                 f"標準立位 (yaw={yaw_d:.1f}, pitch={pitch_d:.1f}, knee={knee_d:.1f})  "
                 f"cos_sim={cos_sim:.4f}", fontsize=10)
    fig.tight_layout()
    out2 = output_dir / "vis_shin_shell_detail.png"
    fig.savefig(out2, dpi=130, facecolor="white")
    plt.close(fig)
    print(f"  saved {out2}")

    return 0 if worst > 0.999 else 1


if __name__ == "__main__":
    import japanize_matplotlib  # noqa: F401 (日本語タイトル文字化け対策, tools/render_urdf_compare.py と同じ流儀)
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    sys.exit(main(parser.parse_args().output_dir))
