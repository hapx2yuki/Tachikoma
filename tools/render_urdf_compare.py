#!/usr/bin/env python3
"""URDF (実パース + FK) と robot_meshes(dress=True) (参照実装) の見た目比較
レンダを生成する (docs/urdf.md 「Isaac Sim への取込手順」上部で参照する
比較画像)。

hardware/urdf/tachikoma.urdf を独自の軽量 URDF パーサ + FK で読み、
tools/export_urdf.collect_all_parts() のパーツをそのチェーンで配置した
ものを render_urdf_stand.png、tools/make_visuals.robot_meshes(dress=True)
の直接出力を render_ref_stand.png として、同一姿勢・同一視点で書き出す
(見比べれば FK 一致を目視確認できる — 数値照合は check_urdf.py [3] 参照)。

日本語タイトルの文字化け (tofu) 対策として japanize_matplotlib は
__main__ 内でのみ import する (CLAUDE.md の規律に合わせる — import 副作用
をモジュールレベルに持ち込まない)。

実行: .venv/bin/python tools/render_urdf_compare.py
"""
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from mpl_toolkits.mplot3d.art3d import Poly3DCollection  # noqa: E402
from scipy.spatial.transform import Rotation  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hardware" / "src"))
sys.path.insert(0, str(ROOT / "tools"))
import config as C  # noqa: E402
import export_urdf as E  # noqa: E402
from make_visuals import robot_meshes, trans  # noqa: E402
from sim_gait import foot_target, leg_ik, BODY_H  # noqa: E402

URDF_PATH = ROOT / "hardware" / "urdf" / "tachikoma.urdf"
OUT_DIR = ROOT / "hardware" / "urdf"
MM = 0.001

STAND_BODY_H = 115.0
ARM_READY = (10.0, 30.0, 40.0)  # (yaw, pitch, elbow) deg — docs/urdf.md 標準立ち姿勢と同一


# ============================================================ URDF パーサ + FK
# tools/check_urdf.py の parse_urdf/joint_transform/fk_all と同一ロジック
# (独立コピー — check_urdf.py はトップレベルで検証を実行するモジュールの
# ため import 共有せず、preview_robot.py 等の既存スクリプトと同じ流儀で
# 自己完結させる)
def parse_urdf(path: Path):
    tree = ET.parse(path)
    root = tree.getroot()
    joints = {}
    for je in root.findall("joint"):
        name = je.get("name")
        jtype = je.get("type")
        parent = je.find("parent").get("link")
        child = je.find("child").get("link")
        o = je.find("origin")
        xyz = np.array([float(v) for v in o.get("xyz").split()])
        rpy = np.array([float(v) for v in o.get("rpy").split()])
        axis = None
        if jtype != "fixed":
            axis = np.array([float(v) for v in je.find("axis").get("xyz").split()])
        joints[name] = dict(type=jtype, parent=parent, child=child, xyz=xyz, rpy=rpy, axis=axis)
    return joints


def joint_transform(j, value):
    T = np.eye(4)
    T[:3, :3] = Rotation.from_euler("xyz", j["rpy"]).as_matrix()
    T[:3, 3] = j["xyz"]
    if j["type"] == "fixed":
        return T
    R = np.eye(4)
    R[:3, :3] = Rotation.from_rotvec(j["axis"] * value).as_matrix()
    return T @ R


def fk_all(joints, q_by_joint_name: dict):
    world = {"base_link": np.eye(4)}
    by_parent = {}
    for name, j in joints.items():
        by_parent.setdefault(j["parent"], []).append((name, j))
    frontier = ["base_link"]
    while frontier:
        p = frontier.pop()
        for name, j in by_parent.get(p, []):
            val = q_by_joint_name.get(name, 0.0)
            world[j["child"]] = world[p] @ joint_transform(j, val)
            frontier.append(j["child"])
    return world


def _m_to_mm(T_m: np.ndarray) -> np.ndarray:
    T = T_m.copy()
    T[:3, 3] = T[:3, 3] / MM
    return T


# ============================================================ 標準立ち姿勢の関節値
def gait_case(phase, vx, vy, wz, body_h):
    out = {}
    for li, leg in enumerate(E.LEGS):
        lx, ly, lz = foot_target(li, phase, vx, vy, wz)
        lz = lz + (BODY_H - body_h)
        a = leg_ik(lx, ly, lz)
        if a is None:
            return None
        out[leg] = a
    return out


def standing_q():
    leg_angles = gait_case(0.0, 0.0, 0.0, 0.0, STAND_BODY_H)
    assert leg_angles is not None, "standing pose IK 失敗"
    q = {}
    for leg, (yaw_d, pitch_d, knee_d) in leg_angles.items():
        lo = leg.lower()
        q[f"leg_{lo}_yaw"] = np.radians(yaw_d)
        q[f"leg_{lo}_pitch"] = np.radians(pitch_d)
        q[f"leg_{lo}_knee"] = np.radians(knee_d)
    for tag in ("r", "l"):
        q[f"arm_{tag}_yaw"] = np.radians(ARM_READY[0])
        q[f"arm_{tag}_pitch"] = np.radians(ARM_READY[1])
        q[f"arm_{tag}_elbow"] = np.radians(ARM_READY[2])
    q["eye_r_roll"] = q["eye_l_roll"] = 0.0
    return q


def urdf_fk_meshes():
    """URDF 実パース + FK チェーンで配置したパーツ一覧 [(color_hex, mesh_mm), ...]。"""
    joints = parse_urdf(URDF_PATH)
    q = standing_q()
    world = fk_all(joints, q)
    shiftz = trans(0, 0, STAND_BODY_H)
    parts = E.collect_all_parts()
    out = []
    for link, items in parts.items():
        if link not in world or not items:
            continue
        Fw = shiftz @ _m_to_mm(world[link])
        for m, c, n in items:
            m2 = m.copy()
            m2.apply_transform(Fw)
            out.append((c, m2))
    return out


def reference_meshes():
    """robot_meshes(dress=True) の直接出力 [(color_hex, mesh_mm), ...]。"""
    real = robot_meshes(0.0, 0.0, 0.0, 0.0, STAND_BODY_H,
                        arms=(*ARM_READY, 0.0), dress=True)
    return [(c, m) for (m, c, a) in real]


# ============================================================ レンダ
def render(meshes, title, out_path, elev=18, azim=-55):
    fig = plt.figure(figsize=(7.0, 7.0))
    ax = fig.add_subplot(111, projection="3d")
    light = np.array([0.4, -0.6, 0.7])
    light /= np.linalg.norm(light)
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
    c = (pts.min(0) + pts.max(0)) / 2
    r = float((pts.max(0) - pts.min(0)).max()) / 2 * 1.05
    ax.set_xlim(c[0] - r, c[0] + r)
    ax.set_ylim(c[1] - r, c[1] + r)
    ax.set_zlim(0, 2 * r)
    ax.set_box_aspect([1, 1, 1])
    ax.axis("off")
    ax.view_init(elev=elev, azim=azim)
    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(out_path, dpi=110, facecolor="white")
    plt.close(fig)
    print(f"saved {out_path}")


def main():
    urdf_title = ("URDF FK 描画 (trimesh, tachikoma.urdf 実パース)\n"
                  f"体高 body_h={STAND_BODY_H:.0f}, 腕READY({ARM_READY[0]:.0f},"
                  f"{ARM_READY[1]:.0f},{ARM_READY[2]:.0f})")
    ref_title = ("robot_meshes(dress=True) 参照 (make_visuals.py)\n"
                 f"体高 body_h={STAND_BODY_H:.0f}, 腕READY({ARM_READY[0]:.0f},"
                 f"{ARM_READY[1]:.0f},{ARM_READY[2]:.0f})")
    render(urdf_fk_meshes(), urdf_title, OUT_DIR / "render_urdf_stand.png")
    render(reference_meshes(), ref_title, OUT_DIR / "render_ref_stand.png")


if __name__ == "__main__":
    import japanize_matplotlib  # noqa: F401 (日本語タイトル文字化け対策)
    main()
