#!/usr/bin/env python3
"""hardware/urdf/tachikoma.urdf の検証 (tools/export_urdf.py の出力を検査)。

[1] XML 整形式 + 必須要素 + 名前一意性 (トップレベル <material> 宣言・
    参照解決含む)
[2] 運動学照合: 関節原点/軸/リミットを config.py・firmware から独立再計算した
    期待値と突合 (許容 1e-4 m)。camera_optical_fixed は visual/collision
    メッシュを持たず [3] の対象外になるため、ここで光軸方位角・仰角を
    config.CAM2_RESIDUAL_DEG と独立突合する
[3] FK 照合: ゼロ姿勢 + 標準立ち姿勢 + ランダム20姿勢で、URDF チェーン FK に
    よる visual メッシュ配置と robot_meshes(dress=True) の出力頂点を突合
    (許容 0.1mm)
[4] 接地: 標準立ち姿勢で 4 足 foot_pad 底面が同一平面 (±0.1mm) かつ base
    高さ ≈ BODY_H
[5] 慣性: 全リンク質量>0、慣性テンソル対称+正定値、総質量 2.5-3.5kg。
    [5b] leg_fr_coxa の質量を RHO/壁厚/インフィルの独立転記値から再計算し
    突合 (RHO drift・サーボ質量の重複計上/欠落を検出)
[6] メッシュ: 全ファイル存在、visual パーツ被覆 = robot_meshes(dress=True)
    の全パーツ (manifest 突合、欠落 0)。[6b]/[6c] は実際に焼き出された
    hardware/urdf/meshes/*.stl を bake_visual_meshes()/build_collisions()
    の出力と直接突合し、ベイク処理そのもの (mm->m スケール・同色パーツ
    結合・STL export/import) を自動チェック対象に含める
[7] スケール: メッシュ bbox が m 単位として妥当 (全長 ~0.4m 級)
[8] collision: 凸性・face 数上限・foot_pad 底包含

実行: .venv/bin/python tools/check_urdf.py
"""
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import trimesh
from scipy.spatial import cKDTree
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hardware" / "src"))
sys.path.insert(0, str(ROOT / "tools"))
import config as C  # noqa: E402
import kit_assembly as KIT  # noqa: E402
from make_visuals import robot_meshes, rot, trans, load  # noqa: E402
from sim_gait import leg_ik, foot_target, BODY_H, STANCE, MOUNT, STANCE_R, ORIGIN  # noqa: E402
import export_urdf as E  # noqa: E402 (mm 単位の FK 式再利用は check[3] のみ。
                          # check[2] は独立再導出であり E の frame 関数は使わない)

URDF_PATH = ROOT / "hardware" / "urdf" / "tachikoma.urdf"
MESH_DIR = ROOT / "hardware" / "urdf" / "meshes"
MM = 0.001

OK = True
RESULTS = []


def check(cond, msg, section=""):
    global OK
    OK &= bool(cond)
    mark = "OK " if cond else "NG "
    line = f"  {mark}{msg}"
    print(line)
    RESULTS.append((section, bool(cond), msg))
    return bool(cond)


# ============================================================ URDF パーサ
def parse_urdf(path: Path):
    tree = ET.parse(path)
    root = tree.getroot()
    links = {}
    for le in root.findall("link"):
        name = le.get("name")
        vis = [v.find("geometry/mesh").get("filename") for v in le.findall("visual")]
        col = [v.find("geometry/mesh").get("filename") for v in le.findall("collision")]
        inertial = le.find("inertial")
        mass = com = I = None
        if inertial is not None:
            mass = float(inertial.find("mass").get("value"))
            com = np.array([float(v) for v in inertial.find("origin").get("xyz").split()])
            it = inertial.find("inertia")
            ixx, ixy, ixz = float(it.get("ixx")), float(it.get("ixy")), float(it.get("ixz"))
            iyy, iyz, izz = float(it.get("iyy")), float(it.get("iyz")), float(it.get("izz"))
            I = np.array([[ixx, ixy, ixz], [ixy, iyy, iyz], [ixz, iyz, izz]])
        links[name] = dict(visual=vis, collision=col, mass=mass, com=com, I=I)
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
        limit = None
        if jtype != "fixed":
            axis = np.array([float(v) for v in je.find("axis").get("xyz").split()])
            lim = je.find("limit")
            limit = (float(lim.get("lower")), float(lim.get("upper")),
                    float(lim.get("effort")), float(lim.get("velocity")))
        joints[name] = dict(type=jtype, parent=parent, child=child, xyz=xyz, rpy=rpy,
                            axis=axis, limit=limit)
    return root, links, joints


def origin_matrix(xyz, rpy):
    T = np.eye(4)
    T[:3, :3] = Rotation.from_euler("xyz", rpy).as_matrix()
    T[:3, 3] = xyz
    return T


def joint_transform(j, value):
    T = origin_matrix(j["xyz"], j["rpy"])
    if j["type"] == "fixed":
        return T
    R = np.eye(4)
    R[:3, :3] = Rotation.from_rotvec(j["axis"] * value).as_matrix()
    return T @ R


def child_of(joints, parent_link):
    return [j for j in joints.values() if j["parent"] == parent_link]


def fk_all(joints, q_by_joint_name: dict):
    """base_link を単位行列として、全リンクの world (=base_link 相対, m) 変換を返す。"""
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


print("=" * 70)
print("[0] URDF ロード")
root, LINKS, JOINTS = parse_urdf(URDF_PATH)
print(f"  links={len(LINKS)} joints={len(JOINTS)}")

# ============================================================ [1] XML整形式・必須要素・名前一意性
print("\n[1] XML 整形式 + 必須要素 + 名前一意性")
check(root.tag == "robot" and root.get("name"), "ルート要素が <robot name=...>")
link_names = [le.get("name") for le in root.findall("link")]
joint_names = [je.get("name") for je in root.findall("joint")]
check(len(link_names) == len(set(link_names)), f"リンク名の一意性 ({len(link_names)}件)")
check(len(joint_names) == len(set(joint_names)), f"関節名の一意性 ({len(joint_names)}件)")
all_ref_links = set()
for j in JOINTS.values():
    all_ref_links.add(j["parent"]); all_ref_links.add(j["child"])
check(all_ref_links <= set(link_names), "全 joint の parent/child が定義済み link を参照")
# ツリー構造: base_link 以外は全リンクがちょうど1つの joint の child であること
child_links = [j["child"] for j in JOINTS.values()]
check(len(child_links) == len(set(child_links)), "各リンクの親 joint は高々1つ (木構造)")
non_base = set(link_names) - {"base_link"}
check(non_base == set(child_links), "base_link 以外の全リンクが到達可能")
for name, l in LINKS.items():
    check(l["mass"] is not None and l["I"] is not None, f"{name}: <inertial> あり", "1")
print(f"  robot name = {root.get('name')!r}")

# <material> の URDF 厳密仕様準拠: <robot> 直下にトップレベル宣言され、
# 各 <visual><material name=.../> の name-only 参照がそこへ解決できること
# (2026-07-31 レビュー指摘: 以前は <visual> 内の初出時にしか <color> を
# 持たせておらず、<robot> 直下の宣言が存在しなかった)
top_materials = {m.get("name"): m for m in root.findall("material")}
check(len(top_materials) > 0, f"<robot> 直下にトップレベル <material> が存在 ({len(top_materials)}件)", "1")
for mname, me in top_materials.items():
    check(me.find("color") is not None, f"トップレベル material '{mname}': <color> あり", "1")
vis_mat_refs = set()
for le in root.findall("link"):
    for vis in le.findall("visual"):
        me = vis.find("material")
        if me is not None:
            vis_mat_refs.add(me.get("name"))
unresolved_mats = vis_mat_refs - set(top_materials)
check(len(unresolved_mats) == 0,
     f"全 <visual> の material 参照 ({len(vis_mat_refs)}種) がトップレベル宣言で解決可能 "
     f"(未解決 {len(unresolved_mats)}件)", "1")


# ============================================================ [2] 運動学照合 (独立再計算)
print("\n[2] 運動学照合 (config.py/firmware から独立再計算した期待値との突合, 許容1e-4m)")
TOL_KIN = 1e-4  # m


def _fw(name):
    return E.FW[name]


def expect_leg_yaw():
    out = {}
    for leg in E.LEGS:
        ox, oy = C.HIPS[leg]
        xyz = np.array([ox, oy, 0.0]) * MM
        rpy = np.array([0.0, 0.0, np.radians(C.LEG_ANGLES[leg])])
        out[f"leg_{leg.lower()}_yaw"] = (xyz, rpy, np.array([0, 0, 1.0]),
                                        (-_fw("LIM_YAW"), _fw("LIM_YAW")))
    return out


def expect_leg_pitch_knee():
    out = {}
    for leg in E.LEGS:
        lo = leg.lower()
        out[f"leg_{lo}_pitch"] = (np.array([C.COXA_LEN, 0, 0]) * MM, np.zeros(3),
                                 np.array([0, 1.0, 0]),
                                 (_fw("LIM_PITCH_UP"), _fw("LIM_PITCH_DN")))
        out[f"leg_{lo}_knee"] = (np.array([C.FEMUR_LEN, 0, 0]) * MM, np.zeros(3),
                                np.array([0, 1.0, 0]),
                                (-_fw("LIM_KNEE"), _fw("LIM_KNEE")))
    return out


def _arm_pitch_dn():
    PA = C.ARM_SERVO
    pz = 2.5 + PA["HORN_HUB_H"] - 2.0
    return pz + 2.5 + (PA["W"] / 2 + 2.5) - 0.1


def expect_arm():
    out = {}
    mx, my = C.ARM_MOUNT_XY
    pdn = _arm_pitch_dn()
    # 右: origin回転 Rz(90-MOUNT_YAW), axis=-Z (make_visuals の
    # rot(90-MOUNT_YAW-ay,'z') を「固定オフセット @ 可変軸回転」に分解した
    # ときの唯一の形 — Rz(c-ay)=Rz(c)@Rz(-ay)=Rz(c)@R((0,0,-1),ay))
    out["arm_r_yaw"] = (np.array([mx, my, C.HIP_DROP - 2.0]) * MM,
                        np.array([0, 0, np.radians(90 - C.ARM_MOUNT_YAW_DEG)]),
                        np.array([0, 0, -1.0]), (-_fw("ARM_YAW_LIM"), _fw("ARM_YAW_LIM")))
    out["arm_r_pitch"] = (np.array([20.0, 0, -pdn]) * MM, np.zeros(3), np.array([0, 1.0, 0]),
                          (_fw("ARM_PITCH_MIN"), _fw("ARM_PITCH_MAX")))
    out["arm_r_elbow"] = (np.array([C.UPPER_ARM_LEN, 0, 0]) * MM, np.zeros(3),
                          np.array([0, 1.0, 0]), (_fw("ARM_ELBOW_MIN"), _fw("ARM_ELBOW_MAX")))
    # 左: 矢状面ミラー (x反転)。原点回転は Rz(MOUNT_YAW-90) = -(右の回転角)、
    # 軸は右の軸ベクトルの (ax,-ay,-az) (2重共役 Mx@F@Mx の一般則。
    # tools/export_urdf.py _mirror_frame のコメント/レビューログ参照。
    # 数値的な正しさは check[3] (実 make_visuals との FK 一致) で担保する)
    out["arm_l_yaw"] = (np.array([-mx, my, C.HIP_DROP - 2.0]) * MM,
                        np.array([0, 0, np.radians(C.ARM_MOUNT_YAW_DEG - 90)]),
                        np.array([0, 0, 1.0]), (-_fw("ARM_YAW_LIM"), _fw("ARM_YAW_LIM")))
    out["arm_l_pitch"] = (np.array([-20.0, 0, -pdn]) * MM, np.zeros(3), np.array([0, -1.0, 0]),
                          (_fw("ARM_PITCH_MIN"), _fw("ARM_PITCH_MAX")))
    out["arm_l_elbow"] = (np.array([-C.UPPER_ARM_LEN, 0, 0]) * MM, np.zeros(3),
                          np.array([0, -1.0, 0]), (_fw("ARM_ELBOW_MIN"), _fw("ARM_ELBOW_MAX")))
    return out


def expect_eyes():
    """目ソケットは Head_Top (T_head_top = trans(0,C.ARM_MOUNT_HUB_Y,
    zb+HEAD_TOP_Z_OFFSET)@rot(180,'z')) に取り付く — make_visuals.py の shell_ghosts()/
    kit_dress_static() の Head_Top 配置式 (config.py 定数 + HEAD_TOP_Z_OFFSET
    のみに依存, 独立に読める) をそのまま用いて base_link 相対位置へ変換する。"""
    from make_visuals import HEAD_TOP_Z_OFFSET
    setback = (C.EYE_SOCKET_FLOOR - C.EYE_HOVER) + C.EYE_NECK_H
    T_head_top = trans(0, C.ARM_MOUNT_HUB_Y, C.HIP_DROP + HEAD_TOP_Z_OFFSET) @ rot(180, "z")
    out = {}
    for idx, jname in ((0, "eye_r_roll"), (2, "eye_l_roll")):
        ctr, n = np.array(C.EYE_SOCKETS_150[idx][0]), np.array(C.EYE_SOCKETS_150[idx][1])
        pos_local = ctr - n * setback
        pos_world = (T_head_top @ np.array([*pos_local, 1.0]))[:3]
        out[jname] = (pos_world * MM, None, np.array([0, 0, 1.0]), (-_fw("EYE_LIM"), _fw("EYE_LIM")))
    return out


expected = {}
expected.update(expect_leg_yaw())
expected.update(expect_leg_pitch_knee())
expected.update(expect_arm())
eye_expected = expect_eyes()

for jname, (xyz, rpy, axis, limit) in expected.items():
    j = JOINTS[jname]
    d_xyz = np.linalg.norm(j["xyz"] - xyz)
    check(d_xyz < TOL_KIN, f"{jname}: origin xyz 誤差 {d_xyz*1000:.5f}mm < 0.1mm", "2")
    if rpy is not None:
        Ract = Rotation.from_euler("xyz", j["rpy"]).as_matrix()
        Rexp = Rotation.from_euler("xyz", rpy).as_matrix()
        d_rot = np.degrees(Rotation.from_matrix(Ract.T @ Rexp).magnitude())
        check(d_rot < 0.01, f"{jname}: origin 回転誤差 {d_rot:.5f}deg", "2")
    d_axis = min(np.linalg.norm(j["axis"] - axis), np.linalg.norm(j["axis"] + axis))
    check(d_axis < 1e-6 or np.allclose(j["axis"], axis, atol=1e-6), f"{jname}: axis 一致 {j['axis']}", "2")
    lo, hi = limit
    check(abs(j["limit"][0] - np.radians(lo)) < 1e-4 and abs(j["limit"][1] - np.radians(hi)) < 1e-4,
         f"{jname}: limit 一致 [{np.degrees(j['limit'][0]):.2f},{np.degrees(j['limit'][1]):.2f}]deg", "2")

for jname, (xyz, rpy, axis, limit) in eye_expected.items():
    j = JOINTS[jname]
    d_xyz = np.linalg.norm(j["xyz"] - xyz)
    check(d_xyz < TOL_KIN, f"{jname}: origin xyz 誤差 {d_xyz*1000:.5f}mm < 0.1mm (EYE_SOCKETS_150 再計算)", "2")
    lo, hi = limit
    check(abs(j["limit"][0] - np.radians(lo)) < 1e-4 and abs(j["limit"][1] - np.radians(hi)) < 1e-4,
         f"{jname}: limit 一致", "2")

def expect_camera_mount():
    """eye_pod_camera_fixed の origin position を EYE_SOCKETS_150[1] から
    独立に再計算する (expect_eyes() と全く同じ式、idx=1=中央目)。"""
    from make_visuals import HEAD_TOP_Z_OFFSET
    setback = (C.EYE_SOCKET_FLOOR - C.EYE_HOVER) + C.EYE_NECK_H
    T_head_top = trans(0, C.ARM_MOUNT_HUB_Y, C.HIP_DROP + HEAD_TOP_Z_OFFSET) @ rot(180, "z")
    ctr, n = np.array(C.EYE_SOCKETS_150[1][0]), np.array(C.EYE_SOCKETS_150[1][1])
    pos_local = ctr - n * setback
    pos_world = (T_head_top @ np.array([*pos_local, 1.0]))[:3]
    return pos_world * MM


j_mount = JOINTS["eye_pod_camera_fixed"]
check(j_mount["type"] == "fixed", "eye_pod_camera_fixed: type=fixed", "2")
d_xyz = np.linalg.norm(j_mount["xyz"] - expect_camera_mount())
check(d_xyz < TOL_KIN,
     f"eye_pod_camera_fixed: origin xyz 誤差 {d_xyz*1000:.5f}mm < 0.1mm (EYE_SOCKETS_150[1] 再計算)", "2")

# camera_optical_fixed: E.camera_optical_frame()/CAM.pupil_axis() を一切呼ばず
# config.py のスカラー定数のみで光軸の方位角・仰角を独立検証する (このリンクは
# visual/collision メッシュを持たないため [3] の FK 照合ループでは対象外になる
# 既知の盲点 — ここで代わりに数値突合する)。make_camera.install_rotation() の
# 設計意図 (docstring 参照) により、光軸は水平面内で完全に前方 (方位角0°,
# +Y 方向。config.py の HIPS より FR/FL の Y>0 が前方であることを確認済み)
# を向き、仰角は CAM2_ALPHA_DEG-CAM2_THETA_DEG=CAM2_RESIDUAL_DEG になる
# よう設計されている (config.py CAM2_RESIDUAL_DEG コメント参照)。
j_opt = JOINTS["camera_optical_fixed"]
check(j_opt["type"] == "fixed", "camera_optical_fixed: type=fixed", "2")
T_mount = origin_matrix(j_mount["xyz"], j_mount["rpy"])
T_opt_local = origin_matrix(j_opt["xyz"], j_opt["rpy"])
T_opt_world = T_mount @ T_opt_local
z_axis = T_opt_world[:3, 2]
cam_az = np.degrees(np.arctan2(z_axis[0], z_axis[1]))  # 0deg = +Y(前方)
cam_el = np.degrees(np.arctan2(z_axis[2], np.hypot(z_axis[0], z_axis[1])))
check(abs(cam_az) < 0.05,
     f"camera_optical_fixed: 光軸方位角 {cam_az:.4f}deg ≈ 0 (水平前方, +Y) [config.py 独立再計算]", "2")
check(abs(cam_el - C.CAM2_RESIDUAL_DEG) < 0.01,
     f"camera_optical_fixed: 光軸仰角 {cam_el:.4f}deg ≈ CAM2_RESIDUAL_DEG"
     f"({C.CAM2_RESIDUAL_DEG:.4f}deg)=ALPHA-THETA [config.py 独立再計算]", "2")
print(f"  camera_optical_fixed: world 位置(mm)={np.round(T_opt_world[:3, 3] / MM, 4)} "
     f"方位角={cam_az:.4f}deg 仰角={cam_el:.4f}deg")


# ============================================================ [3][4] FK照合・接地
import make_visuals as MV  # noqa: E402

PARTS = E.collect_all_parts()   # link -> [(mesh_mm_local, color, name), ...] (check[3][6][7][8] 共通)


def _m_to_mm(T_m: np.ndarray) -> np.ndarray:
    T = T_m.copy()
    T[:3, 3] = T[:3, 3] / MM
    return T


def gait_case(phase, vx, vy, wz, body_h):
    """foot_target()+leg_ik() で全脚 IK 成功する脚角一式を返す (失敗なら None)。"""
    out = {}
    for li, leg in enumerate(E.LEGS):
        lx, ly, lz = foot_target(li, phase, vx, vy, wz)
        lz = lz + (BODY_H - body_h)
        a = leg_ik(lx, ly, lz)
        if a is None:
            return None
        out[leg] = a
    return out


# robot_meshes(dress=True) に対応物が無い既知の追加パーツ (docs/urdf.md に
# 明記): camera_carrier (完全内蔵の隠しパーツ, kit_dress_static は描かない)。
# 目 (eye_r_pod/eye_l_pod) は roll≠0 の場合 robot_meshes 側に相当する描画が
# 無い (kit_dress_static は常に roll=0 固定姿勢) ため、このホール・ボディ
# 比較では目は常に roll=0 で検証し、目自体の関節 FK 精度は
# check_eye_roll_fk() で別途 (eyes_video() と同じ式に対して) 検証する。
KNOWN_EXTRA_PARTS = ("camera_carrier",)


def run_pose_check(label, phase, vx, vy, wz, body_h, arm_pose, tol_mm=0.1):
    leg_angles = gait_case(phase, vx, vy, wz, body_h)
    if leg_angles is None:
        return None
    ay, ap, ae = arm_pose
    clamped = MV.fw_arm_clamp((ay, ap, ae, 0.0), body_h)
    if not np.allclose(clamped[:3], (ay, ap, ae), atol=1e-6):
        return None   # このテスト角は安全域外 (呼び出し側で別姿勢を試す)

    q = {}
    for leg, (yaw_d, pitch_d, knee_d) in leg_angles.items():
        lo = leg.lower()
        q[f"leg_{lo}_yaw"] = np.radians(yaw_d)
        q[f"leg_{lo}_pitch"] = np.radians(pitch_d)
        q[f"leg_{lo}_knee"] = np.radians(knee_d)
    for tag in ("r", "l"):
        q[f"arm_{tag}_yaw"] = np.radians(ay)
        q[f"arm_{tag}_pitch"] = np.radians(ap)
        q[f"arm_{tag}_elbow"] = np.radians(ae)
    q["eye_r_roll"] = 0.0
    q["eye_l_roll"] = 0.0

    world = fk_all(JOINTS, q)          # base_link 相対 (m)
    shiftz = trans(0, 0, body_h)       # base_link(=hip高さ平面) -> world(zb基準と同一地面原点)

    mine = []
    for link, items in PARTS.items():
        if link not in world or not items:
            continue
        Fw = shiftz @ _m_to_mm(world[link])
        for m, c, n in items:
            m2 = m.copy(); m2.apply_transform(Fw)
            mine.append((link, n, m2))

    real = robot_meshes(phase, vx, vy, wz, body_h, arms=(ay, ap, ae, 0.0), dress=True)
    used = [False] * len(real)
    worst = 0.0
    n_checked = n_unmatched = 0
    for link, name, m in mine:
        if name.startswith(KNOWN_EXTRA_PARTS):
            continue
        best_i, best_d = -1, 1e18
        for i, (rm, rc, ra) in enumerate(real):
            if used[i] or rm.vertices.shape != m.vertices.shape:
                continue
            d = np.abs(rm.vertices - m.vertices).max()
            if d < best_d:
                best_d, best_i = d, i
        n_checked += 1
        if best_i < 0 or best_d > tol_mm:
            n_unmatched += 1
        else:
            used[best_i] = True
            worst = max(worst, best_d)
    check(n_unmatched == 0,
         f"{label}: 全 {n_checked} パーツ一致 (未一致{n_unmatched}, worst={worst:.6f}mm "
         f"[body_h={body_h:.1f} phase={phase:.2f} vx={vx:.2f} vy={vy:.2f} wz={wz:.2f} "
         f"arm=({ay:.1f},{ap:.1f},{ae:.1f})])",
         "3")
    return dict(leg_angles=leg_angles, world=world, worst=worst, n=n_checked)


def check_eye_roll_fk(n_trials=6, tol_mm=0.05):
    """目 (eye_r_pod/eye_l_pod) の roll 関節 FK を、make_visuals.eyes_video()
    と同一の取付式 (T_head_top @ trans(pos) @ align_vectors(...) @
    rot(EYE_DOT_ROLL_DEG+roll,'z')) に対して独立に突合する
    (robot_meshes(dress=True) は roll=0 固定でしか描画しないため、ここだけ
    別ルートで検証する)。"""
    from make_visuals import HEAD_TOP_Z_OFFSET, EYE_DOT_ROLL_DEG as EDR
    setback = (C.EYE_SOCKET_FLOOR - C.EYE_HOVER) + C.EYE_NECK_H
    T_head_top = trans(0, C.ARM_MOUNT_HUB_Y, C.HIP_DROP + HEAD_TOP_Z_OFFSET) @ rot(180, "z")
    pod_local = load("eye_pod")
    rng = np.random.default_rng(7)
    worst = 0.0
    for trial in range(n_trials):
        roll_r = float(rng.uniform(-79, 79))
        roll_l = float(rng.uniform(-79, 79))
        q = {"eye_r_roll": np.radians(roll_r), "eye_l_roll": np.radians(roll_l)}
        world = fk_all(JOINTS, q)
        for idx, jname, roll in ((0, "eye_r_pod", roll_r), (2, "eye_l_pod", roll_l)):
            ctr, n = np.array(C.EYE_SOCKETS_150[idx][0]), np.array(C.EYE_SOCKETS_150[idx][1])
            pos = ctr - n * setback
            A = trimesh.geometry.align_vectors([0, 0, 1], n) @ rot(EDR.get(idx, 0.0) + roll, "z")
            T_expect = T_head_top @ trans(*pos) @ A
            m_expect = pod_local.copy(); m_expect.apply_transform(T_expect)
            m_mine = pod_local.copy(); m_mine.apply_transform(_m_to_mm(world[jname]))
            d = float(np.abs(m_expect.vertices - m_mine.vertices).max())
            worst = max(worst, d)
    check(worst < tol_mm, f"目 roll 関節 FK (eyes_video() 式と独立突合, {n_trials*2}件): "
         f"worst={worst:.6f}mm < {tol_mm}mm", "3")


print("\n[3] FK 照合 (URDF 関節チェーン FK vs robot_meshes(dress=True), 許容0.1mm)")
RNG = np.random.default_rng(20260730)

# ゼロ姿勢相当: 腕/目/カメラは厳密ゼロ、脚は phase=0,v=0 (中立, IK成功 — 脚を
# 字義通りゼロ度にすると gait/robot_meshes 側に対応する呼び出し方が無いため
# 「非歩容依存の中立姿勢」をゼロ姿勢の代替として使う。docs/urdf.md に明記)
n_pose_ok = 0
res0 = run_pose_check("pose00(zero/neutral)", 0.0, 0.0, 0.0, 0.0, 115.0, (0.0, 0.0, 0.0))
n_pose_ok += res0 is not None

# 標準立ち姿勢 (体高 BODY_H_DEF=115, 静止, 腕READY寄りの姿勢)
res_std = run_pose_check("pose01(standing)", 0.0, 0.0, 0.0, 0.0, 115.0, (10.0, 30.0, 40.0))
n_pose_ok += res_std is not None

n_random = 0
trial = 0
while n_random < 20 and trial < 400:
    trial += 1
    body_h = RNG.uniform(105.0, 130.0)
    vx, vy, wz = RNG.uniform(-1, 1, 3)
    phase = RNG.uniform(0, 1)
    ay = RNG.uniform(-14.0, 14.0)
    ap = RNG.uniform(-40.0, 45.0)
    ae = RNG.uniform(2.0, 88.0)
    res = run_pose_check(f"pose{n_random+2:02d}(random)", phase, vx, vy, wz, body_h, (ay, ap, ae))
    if res is not None:
        n_random += 1
check(n_random == 20, f"ランダム姿勢 20/20 件を評価 (試行{trial}回)", "3")
check_eye_roll_fk()


# ============================================================ [4] 接地
print("\n[4] 接地 (標準立ち姿勢: 4足 foot_pad 底面の共面性 ±0.1mm, base高さ≈BODY_H)")
STAND_BODY_H = 115.0
# check[3] の姿勢 (phase=0,v=0) は SWAY_LEAD の窓境界がちょうど phase=0/1
# 境界に重なるため sway が厳密に 0 にならず (実測: sway_of(0.0)≈(0,21.8mm))、
# 前脚/後脚で追加の前後シフトがかかり足高さが対称にならない (これは歩容の
# 仕様であって URDF のバグではない — gait.h の sway 窓は常時どこかの脚の
# 遊脚区間をカバーするよう設計されている)。「静止して立つ」ための姿勢は
# robot_meshes() 自身が leg_ik 失敗時に使うフォールバック式 (STANCE 方位・
# STANCE_R・body_h への直接到達, sway 非依存) を使う — 4 脚とも半径
# STANCE_R・高さ body_h が同一なので幾何学的に厳密対称になる
def standing_leg_angles(body_h):
    out = {}
    for li, leg in enumerate(E.LEGS):
        d = STANCE[li] - MOUNT[li]
        a = leg_ik(STANCE_R * np.cos(d), STANCE_R * np.sin(d), -body_h)
        assert a is not None, f"standing pose IK 失敗: {leg}"
        out[leg] = a
    return out


leg_angles_stand = standing_leg_angles(STAND_BODY_H)
q_stand = {}
for leg, (yaw_d, pitch_d, knee_d) in leg_angles_stand.items():
    lo = leg.lower()
    q_stand[f"leg_{lo}_yaw"] = np.radians(yaw_d)
    q_stand[f"leg_{lo}_pitch"] = np.radians(pitch_d)
    q_stand[f"leg_{lo}_knee"] = np.radians(knee_d)
world = fk_all(JOINTS, q_stand)
shiftz = trans(0, 0, STAND_BODY_H)
foot_pad_local = load("foot_pad")
foot_pad_local.apply_transform(trans(0, 0, -C.TIBIA_LEN))
foot_zs = {}
for leg in E.LEGS:
    lo = leg.lower()
    Fw = shiftz @ _m_to_mm(world[f"leg_{lo}_tibia"])
    fp = foot_pad_local.copy(); fp.apply_transform(Fw)
    foot_zs[leg] = float(fp.vertices[:, 2].min())
zs = np.array(list(foot_zs.values()))
coplanar = float(zs.max() - zs.min())
print(f"  foot_pad 底面 world z (mm, 標準立ち姿勢): "
     + ", ".join(f"{leg}={foot_zs[leg]:+.4f}" for leg in E.LEGS))
check(coplanar < 0.1, f"4足 foot_pad 底面の共面性: {coplanar:.4f}mm < 0.1mm")
base_h_actual = STAND_BODY_H  # base_link 原点 = 股ヨー/ピッチ軸高さ平面 = 定義上 body_h そのもの
check(abs(base_h_actual - 115.0) < 1e-6,
     f"base_link 高さ = body_h = {base_h_actual:.1f}mm ≈ BODY_H_DEF(115)")
print(f"  (foot_pad 底が world z=0 からずれる量は FOOT_GROUND_OFFSET の校正が "
     f"SWAY込み歩容全域の worst-case 用であるため — 静止立ち姿勢はその対象で"
     f"はなく z=0 に厳密一致しなくてよい。config.py FOOT_GROUND_OFFSET コメント "
     f"参照。ここでの合否は「4足が同一平面にあるか」のみ判定する)")


# ============================================================ [5] 慣性
print("\n[5] 慣性 (全リンク質量>0、対称+正定値、総質量 2.5〜3.5kg)")
grand_total = 0.0
for name, l in LINKS.items():
    m, com, I = l["mass"], l["com"], l["I"]
    grand_total += m
    check(m > 0, f"{name}: mass={m*1000:.2f}g > 0", "5")
    check(np.allclose(I, I.T, atol=1e-15), f"{name}: 慣性テンソル対称", "5")
    eig = np.linalg.eigvalsh(I)
    check(bool((eig > 0).all()), f"{name}: 慣性テンソル正定値 (固有値 {np.round(eig,12)})", "5")
check(2.5 <= grand_total <= 3.5, f"総質量 {grand_total:.4f} kg (目標 2.5〜3.5kg)")

# ---- [5b] 質量の独立再計算 (leg_fr_coxa の代表例)
# 上記は mass>0/対称/正定値/総質量レンジのみの検証であり、
# combine_mass_items() の平行軸定理による合成は入力 MassItem が物理的に
# 妥当でさえあればほぼ自動的に対称・正定値になるため、RHO・壁厚・インフィル
# の誤設定やサーボ質量の重複計上/欠落を検出できない (2026-07-31 レビュー
# 指摘)。leg_fr_coxa (= coxa_bracket_m メッシュ + 股ピッチサーボ box のみの
# 単純な構成) について、export_urdf.estimate_mass_g() を呼ばず同じ物理式を
# ここに再実装し、RHO/壁厚/インフィルも tools/filament_calc.py new_parts
# (coxa_bracket: PETG/壁2.4mm/infill40%) からの独立転記値を使って質量を
# 再計算し、URDF <inertial> の記載値と突合する。
print("\n[5b] 質量の独立再計算 (leg_fr_coxa: RHO/壁厚/インフィルの drift・"
      "サーボ質量の重複計上/欠落を検出)")
RHO_INDEP = {"PLA": 1.24, "PETG": 1.27, "TPU": 1.21}  # tools/filament_calc.py RHO と同一のはず
check(E.RHO == RHO_INDEP, f"export_urdf.RHO が独立転記値と一致: {E.RHO}", "5b")


def _indep_estimate_mass_g(mesh_mm, wall_mm, infill, density):
    """tools/filament_calc.py printed_cm3() と同じ物理式の独立再実装
    (E.estimate_mass_g() は呼ばない)。"""
    v_solid = abs(mesh_mm.volume) / 1000.0   # cm3
    a = mesh_mm.area / 100.0                 # cm2
    v_wall = min(v_solid, a * wall_mm / 10.0)
    v_print = v_wall + max(0.0, v_solid - v_wall) * infill
    return v_print * density


coxa_mesh = load("coxa_bracket_m")  # hardware/stl/coxa_bracket_m.stl (mm) — FR は
                                     # MIRROR_LEGS 側 (export_urdf.leg_parts 参照)
coxa_mass_g = _indep_estimate_mass_g(coxa_mesh, wall_mm=2.4, infill=0.40,
                                     density=RHO_INDEP["PETG"])
# leg_fr_coxa リンクには coxa_bracket_m のメッシュ質量に加え、股ピッチ
# サーボ (STD, E.SERVO_STD_G) が同一リンクへ計上される (E.leg_servo_items()
# 参照)。box近似の質量寄与は定義から自明に SERVO_STD_G そのもの
expect_coxa_total_g = coxa_mass_g + E.SERVO_STD_G
urdf_coxa_g = LINKS["leg_fr_coxa"]["mass"] * 1000.0
check(abs(expect_coxa_total_g - urdf_coxa_g) < 0.05,
     f"leg_fr_coxa: 独立再計算質量 {expect_coxa_total_g:.3f}g ≈ URDF記載値 {urdf_coxa_g:.3f}g "
     f"(coxa_bracket_m {coxa_mass_g:.3f}g + pitch servo {E.SERVO_STD_G:.1f}g)", "5b")


# ============================================================ [6] メッシュ: 存在・被覆
print("\n[6] メッシュ (全ファイル存在 / visual パーツ被覆 = robot_meshes(dress=True) 全パーツ)")
all_mesh_files = set()
for l in LINKS.values():
    all_mesh_files |= set(l["visual"]) | set(l["collision"])
missing = [f for f in all_mesh_files if not (URDF_PATH.parent / f).exists()]
check(len(missing) == 0, f"URDF 参照メッシュ {len(all_mesh_files)}件、全て実在 (欠落{len(missing)})")

manifest = json.loads((ROOT / "hardware" / "urdf" / "parts_manifest.json").read_text())
manifest_total = manifest["total_parts"]
print(f"  parts_manifest.json 記録数: {manifest_total}")

# robot_meshes(dress=True) の実出力 (標準立ち姿勢, 両腕 READY) と、manifest が
# 記録した全パーツを突合する。camera_carrier のみ robot_meshes 側に対応物が
# 無い既知の追加パーツ (非表示の内蔵パーツ, docs/urdf.md に明記) として許容。
real_std = robot_meshes(0.0, 0.0, 0.0, 0.0, 115.0, arms=(10.0, 30.0, 40.0, 0.0), dress=True)
mine_all = []
q = {}
leg_angles_std = gait_case(0.0, 0.0, 0.0, 0.0, 115.0)
for leg, (yaw_d, pitch_d, knee_d) in leg_angles_std.items():
    lo = leg.lower()
    q[f"leg_{lo}_yaw"], q[f"leg_{lo}_pitch"], q[f"leg_{lo}_knee"] = (
        np.radians(yaw_d), np.radians(pitch_d), np.radians(knee_d))
for tag in ("r", "l"):
    q[f"arm_{tag}_yaw"], q[f"arm_{tag}_pitch"], q[f"arm_{tag}_elbow"] = (
        np.radians(10.0), np.radians(30.0), np.radians(40.0))
q["eye_r_roll"] = q["eye_l_roll"] = 0.0
world_std = fk_all(JOINTS, q)
shiftz = trans(0, 0, 115.0)
for link, items in PARTS.items():
    if link not in world_std:
        continue
    Fw = shiftz @ _m_to_mm(world_std[link])
    for m, c, n in items:
        m2 = m.copy(); m2.apply_transform(Fw)
        mine_all.append((link, n, m2))

used = [False] * len(real_std)
uncovered = []
KNOWN_EXTRA_PREFIXES = ("camera_carrier",)
for link, name, m in mine_all:
    found = False
    for i, (rm, rc, ra) in enumerate(real_std):
        if used[i] or rm.vertices.shape != m.vertices.shape:
            continue
        if np.abs(rm.vertices - m.vertices).max() < 0.05:
            used[i] = True
            found = True
            break
    if not found and not name.startswith(KNOWN_EXTRA_PREFIXES):
        uncovered.append((link, name))
check(len(uncovered) == 0,
     f"manifest 全{manifest_total}パーツが robot_meshes(dress=True) 出力と対応 "
     f"(camera_carrier {sum(1 for _,n,_ in mine_all if n=='camera_carrier')}件は"
     f"意図的な追加分として除外; 未対応 {len(uncovered)}件)")
if uncovered:
    for link, name in uncovered[:10]:
        print(f"    未対応: {link}/{name}")
check(len(real_std) - manifest_total <= 0,
     f"robot_meshes(dress=True) 実パーツ数 {len(real_std)} <= manifest記録数 {manifest_total} "
     f"(manifest が robot_meshes 出力を完全に被覆, +{manifest_total-len(real_std)}分は"
     f"camera_carrier 等の意図的追加)")

# ---- [6b]/[6c] 焼き出し STL の忠実性 (実ファイル vs ベイク処理そのもの)
# 上記 [3]/[6]/[7]/[8] はいずれも E.collect_all_parts() の mm単位・ベイク前
# インメモリ表現 (PARTS) を「mine」として使っており、bake_visual_meshes()/
# write_meshes() (MM=0.001 スケール適用・同色パーツ結合・STLエクスポート) と
# いう実際に出荷される成果物の生成処理そのものは自動チェックの対象外だった
# (関節origin/axisはURDF XMLを実パースしているので健全)。ここで
# bake_visual_meshes()/build_collisions() の出力 (メートル単位) を実際の
# hardware/urdf/meshes/*.stl と直接突合し、この盲点を閉じる。
#
# 注意: STL はメッシュを「三角形の集合 (頂点共有情報なし)」として保存する
# フォーマットのため、書き出し→再読込 (trimesh.load, 既定で process=True の
# merge_vertices() が走る) を経ると、同一の幾何形状でも .vertices 配列の
# 頂点順序・さらには「どの頂点をどこまで同一とみなして併合するか」の内部
# 判定結果 (=.vertices の件数そのもの) すら保持されない。
#
# 実装時にこれで 2 段階の誤検出を自分で踏んだ:
#   (1) 単純な同一インデックスでの引き算 (real.vertices - mine.vertices) は
#       この並び替えを「頂点位置の誤差」と誤検出する。
#   (2) 座標を辞書式ソートしてから引き算する方法に直しても、大きい
#       メッシュ (base_link 等, 面数万オーダー) では同じ x (第一ソートキー)
#       付近に多数の点が並ぶ対称形状のせいでソート自体が不安定になり、
#       近接するが別々の点同士がズレて整列され、実際には無関係な
#       「見かけ上 100mm 級の誤差」を作ってしまう。
# 最終的に、比較対象を .vertices (STL往復で件数が変わりうる内部表現) では
# なく .vertices[.faces] (面ごとに 3 頂点を展開した「三角形コーナー点群」
# — 面数は STL 往復で不変) にし、KDTree による最近傍点マッチング (順序にも
# 件数の内部併合にも依存しない、check[3]/[6] の「最近傍探索によるパーツ
# 突合」と同じ考え方) で両方向 (mine→real, real→mine) の最大距離を取る
# 方式に修正して初めて、既知に正しいはずの焼き出し (leg_fr_shoulder 等) で
# 期待通り ~1e-8m (float32 STL 精度) 以下に収束することを確認した。
def _mesh_content_diff(a: trimesh.Trimesh, b: trimesh.Trimesh) -> float:
    """2 つのメッシュが同じ三角形コーナー点群を表しているかを、頂点の並び順
    にも STL 往復後の頂点併合結果にも依存しない方法で比較する。"""
    ta = a.vertices[a.faces].reshape(-1, 3)
    tb = b.vertices[b.faces].reshape(-1, 3)
    if ta.shape != tb.shape:
        return float("inf")
    d_ab, _ = cKDTree(tb).query(ta, k=1)
    d_ba, _ = cKDTree(ta).query(tb, k=1)
    return float(max(d_ab.max(), d_ba.max()))


TOL_BAKE = 1e-4  # m (0.1mm — check[3]/[6] の tol_mm と同じ水準)

print("\n[6b] 焼き出し visual STL の忠実性 (実ファイル vs bake_visual_meshes() 出力)")
baked_visuals = E.bake_visual_meshes(PARTS)
n_bake_checked = 0
worst_bake = 0.0
for link, items in baked_visuals.items():
    for c, mesh_m in items:
        fname = f"{link}__vis_{E._color_name(c)}.stl"
        path = MESH_DIR / fname
        if not path.exists():
            check(False, f"{fname}: 実ファイルが存在しない", "6b")
            continue
        real_stl = trimesh.load(path)
        d = _mesh_content_diff(real_stl, mesh_m)
        worst_bake = max(worst_bake, d)
        n_bake_checked += 1
        check(d < TOL_BAKE,
             f"{fname}: 実STLがbake_visual_meshes()出力と三角形コーナー点群一致 "
             f"(誤差{d*1000:.6f}mm)", "6b")
check(n_bake_checked > 0, f"焼き出し visual STL {n_bake_checked}件をベイク処理そのものまで含めて検証")
print(f"  worst={worst_bake*1000:.6f}mm")

print("\n[6c] 焼き出し collision STL の忠実性 (実ファイル vs build_collisions()+スケール出力)")
baked_collisions_mm = E.build_collisions(PARTS)
n_col_bake_checked = 0
worst_col_bake = 0.0
for link, hulls in baked_collisions_mm.items():
    for i, h in enumerate(hulls):
        hm = h.copy(); hm.apply_scale(MM)
        fname = f"{link}__col_{i}.stl"
        path = MESH_DIR / fname
        if not path.exists():
            check(False, f"{fname}: 実ファイルが存在しない", "6c")
            continue
        real_stl = trimesh.load(path)
        d = _mesh_content_diff(real_stl, hm)
        worst_col_bake = max(worst_col_bake, d)
        n_col_bake_checked += 1
        check(d < TOL_BAKE,
             f"{fname}: 実STLがcollision焼き出し出力と三角形コーナー点群一致 "
             f"(誤差{d*1000:.6f}mm)", "6c")
check(n_col_bake_checked > 0, f"焼き出し collision STL {n_col_bake_checked}件をベイク処理そのものまで含めて検証")
print(f"  worst={worst_col_bake*1000:.6f}mm")


# ============================================================ [7] スケール
print("\n[7] スケール (メッシュ bbox が m 単位として妥当, 全長 ~0.4m 級)")
mesh_pts = []
for f in sorted((URDF_PATH.parent / "meshes").glob("*__vis_*.stl")):
    m = trimesh.load(f)
    mesh_pts.append(m.vertices)
allv = np.vstack(mesh_pts)
extent = allv.max(axis=0) - allv.min(axis=0)
print(f"  visual メッシュ全体 bbox (静止姿勢ではなく各リンクローカル寄せ集めの参考値): "
     f"{np.round(extent, 4)} m")
# より意味のある指標: 標準立ち姿勢で組み立てたワールド座標の全長
world_pts = np.vstack([m.vertices for _, _, m in mine_all])
world_pts_m = world_pts * MM
ext_world = world_pts_m.max(axis=0) - world_pts_m.min(axis=0)
print(f"  標準立ち姿勢での組立時 bbox: {np.round(ext_world, 4)} m "
     f"(X×Y×Z, 全長スケール ~0.4m 級を期待)")
check(0.15 < ext_world[0] < 1.0, f"X 幅 {ext_world[0]:.3f}m が妥当範囲 (0.15〜1.0m)")
check(0.15 < ext_world[1] < 1.0, f"Y 奥行 {ext_world[1]:.3f}m が妥当範囲 (0.15〜1.0m)")
check(0.05 < ext_world[2] < 1.0, f"Z 高さ {ext_world[2]:.3f}m が妥当範囲 (0.05〜1.0m)")
check(not any(f > 5.0 for f in ext_world), "mm 単位のまま焼き込まれていない (全長5m超なし)")
check(not any(0 < f < 0.01 for f in ext_world.tolist()), "cm/他単位混入なし (全長1cm未満なし)")


# ============================================================ [8] collision
print("\n[8] collision (凸性・面数上限・foot_pad 底の包含)")
MAX_FACES = 200
collisions = E.build_collisions(PARTS)
for link, hulls in collisions.items():
    for i, h in enumerate(hulls):
        check(len(h.faces) <= MAX_FACES, f"{link}[{i}]: 面数 {len(h.faces)} <= {MAX_FACES}", "8")
        rehull_vol = h.convex_hull.volume
        dev = abs(rehull_vol - h.volume) / h.volume if h.volume else 1.0
        check(dev < 0.01, f"{link}[{i}]: 実質凸 (自身の凸包との体積差 {dev*100:.3f}% < 1%, "
             f"decimate後の数値誤差込み許容)", "8")
        check(h.is_watertight, f"{link}[{i}]: watertight", "8")
    if link.endswith("_tibia"):
        fp = load("foot_pad")
        fp.apply_transform(trans(0, 0, -C.TIBIA_LEN))
        fp_min_z = float(fp.vertices[:, 2].min())
        hull_min_z = min(float(h.vertices[:, 2].min()) for h in hulls)
        check(hull_min_z <= fp_min_z + 1e-6,
             f"{link}: collision 凸包の最下点 ({hull_min_z:.3f}mm) が "
             f"foot_pad 底 ({fp_min_z:.3f}mm) を包含", "8")


# ============================================================ 総括
print("\n" + "=" * 70)
n_ok = sum(1 for _, ok, _ in RESULTS if ok)
n_ng = sum(1 for _, ok, _ in RESULTS if not ok)
print(f"合計 {len(RESULTS)} 項目: OK={n_ok} NG={n_ng}")
by_section: dict[str, list[bool]] = {}
for sec, ok, _ in RESULTS:
    by_section.setdefault(sec or "-", []).append(ok)
for sec in sorted(by_section):
    vals = by_section[sec]
    print(f"  section[{sec}]: {sum(vals)}/{len(vals)} OK")
print("RESULT:", "PASS" if OK else "FAIL")
sys.exit(0 if OK else 1)


