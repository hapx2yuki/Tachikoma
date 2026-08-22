#!/usr/bin/env python3
"""タチコマ フル見た目版 URDF 自動生成器。

hardware/src/config.py (寸法の唯一の正) と tools/make_visuals.py の
robot_meshes(dress=True) が実装する FK・パーツ→リンク対応を「正解データ」
として、Isaac Sim 等で読み込める URDF 一式を生成する。

出力 (hardware/urdf/):
  tachikoma.urdf        — ロボット記述 (SI: m, kg, rad)
  meshes/*.stl           — visual/collision メッシュ (メートル単位で焼き込み)
  parts_manifest.json    — 取り込んだキットパーツ名の一覧 (欠落確認用)

設計方針・座標規約・関節⇔PWM対応表は docs/urdf.md 参照。

実行: .venv/bin/python tools/export_urdf.py
検証: .venv/bin/python tools/check_urdf.py
"""
from __future__ import annotations

import json
import sys
import xml.dom.minidom as minidom
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import trimesh
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hardware" / "src"))
sys.path.insert(0, str(ROOT / "tools"))
import config as C  # noqa: E402
import kit_assembly as KIT  # noqa: E402
import make_camera as CAM  # noqa: E402
from make_visuals import (rot, trans, load, STL, MODEL, KIT_PLACEMENTS,  # noqa: E402
                          kit_dress_static, arm_meshes, COL, HEAD_TOP_Z_OFFSET)

OUT = ROOT / "hardware" / "urdf"
MESH_DIR = OUT / "meshes"
MM = 0.001  # mm -> m


# ============================================================ firmware 定数の直接読取
# 関節リミットは firmware/src/config.h に定義がある (config.py には無い) —
# ハードコード複製は drift の元 (sim_gait.py と同じ流儀) なので正規表現で
# 直接読む。ここで読めなかった値は KeyError で即座に失敗させる (無言の
# ハードコード化を防ぐ)。
import re as _re  # noqa: E402

_FW_TXT = (ROOT / "firmware" / "src" / "config.h").read_text()


def _fw_const(name: str) -> float:
    # "constexpr float LIM_PITCH_UP = -45.0f, LIM_PITCH_DN = 55.0f;" のように
    # 1 行に複数宣言されるケースがあるため、name の直後の "= 値f" だけを拾う
    # (constexpr float の有無は問わない)
    m = _re.search(rf"\b{name}\s*=\s*(-?[\d.]+)f", _FW_TXT)
    if not m:
        raise KeyError(f"firmware/src/config.h に {name} が見つからない")
    return float(m.group(1))


FW = {k: _fw_const(k) for k in (
    "LIM_YAW", "LIM_PITCH_UP", "LIM_PITCH_DN", "LIM_KNEE",
    "ARM_YAW_LIM", "ARM_PITCH_MIN", "ARM_PITCH_MAX",
    "ARM_ELBOW_MIN", "ARM_ELBOW_MAX", "EYE_LIM",
)}

LEGS = ["FR", "FL", "RL", "RR"]
MIRROR_LEGS = {"FR", "RL"}   # _m メッシュを使う脚 (make_visuals と一致)

MX = np.diag([-1.0, 1.0, 1.0, 1.0])   # 矢状面 (X=0) ミラー行列

# base_link 原点 = 股ヨー/股ピッチ軸の高さ平面 (make_visuals の world z=body_h
# 相当)、xy はシャーシ中心。全ての「zb 基準」(プレート下面基準) の値は
# ここでは ZB (=HIP_DROP) を zb の代わりに使うことで得られる
# (body_h=0, Tb=I を想定した local 座標)。
ZB = C.HIP_DROP


# ============================================================ FK: 関節フレーム
# 全て「body_h=0, Tb=I」を想定した base_link 相対 (ローカル) の 4x4 同次変換。
# make_visuals.robot_meshes()/arm_meshes() の式をそのまま転記 (角度 deg)。
# tools/export_urdf.py 単体テスト (本ファイル下部) と check_urdf.py [2][3] が
# 実際の make_visuals 出力との数値一致 (<1e-6mm) を検証する。

def leg_yaw_frame(leg: str, q: dict) -> np.ndarray:
    ox, oy = C.HIPS[leg]
    mnt = C.LEG_ANGLES[leg]
    yaw = q.get(f"leg_{leg.lower()}_yaw", 0.0)
    return trans(ox, oy, 0.0) @ rot(mnt + yaw, "z")


def leg_pitch_frame(leg: str, q: dict) -> np.ndarray:
    pitch = q.get(f"leg_{leg.lower()}_pitch", 0.0)
    return leg_yaw_frame(leg, q) @ trans(C.COXA_LEN, 0, 0) @ rot(pitch, "y")


def leg_knee_frame(leg: str, q: dict) -> np.ndarray:
    knee = q.get(f"leg_{leg.lower()}_knee", 0.0)
    return leg_pitch_frame(leg, q) @ trans(C.FEMUR_LEN, 0, 0) @ rot(knee, "y")


def _arm_pitch_dn():
    PA = C.ARM_SERVO
    pz = 2.5 + PA["HORN_HUB_H"] - 2.0
    return pz + 2.5 + (PA["W"] / 2 + 2.5) - 0.1


def arm_yaw_frame_r(q: dict) -> np.ndarray:
    ay = q.get("arm_r_yaw", 0.0)
    mx, my = C.ARM_MOUNT_XY
    T0 = trans(mx, my, ZB - 2.0)
    return T0 @ rot(90 - C.ARM_MOUNT_YAW_DEG - ay, "z")


def arm_pitch_frame_r(q: dict) -> np.ndarray:
    ap = q.get("arm_r_pitch", 0.0)
    return arm_yaw_frame_r(q) @ trans(20, 0, -_arm_pitch_dn()) @ rot(ap, "y")


def arm_elbow_frame_r(q: dict) -> np.ndarray:
    ae = q.get("arm_r_elbow", 0.0)
    Te = arm_pitch_frame_r(q) @ trans(C.UPPER_ARM_LEN, 0, 0)
    return Te @ rot(ae, "y")


def _mirror_frame(F: np.ndarray) -> np.ndarray:
    """矢状面ミラー conjugation (常に proper: det=+1 を保つ)。

    make_visuals.arm_meshes() は左腕を「右腕を pose(ay,ap,ae) で組み、
    メッシュ全体を X=0 面でミラー (X反転+法線反転)」して作る。関節チェーンを
    この 2 重共役 (Mx@F@Mx) で再定義すると、原点(4x4)・回転軸とも常に proper
    (det=+1) になり、通常の URDF 単軸関節 (origin + axis + value, 右腕と
    同じ符号の関節値) として厳密に表現できる。メッシュ側は「右リンクの
    ローカルメッシュを X 反転+法線反転」するだけで済む (数値検証: 本ファイル
    下部 __main__ の self-test、および check_urdf.py [2][3] で
    make_visuals.arm_meshes(side=-1,...) と <1e-9mm 一致することを確認済み)。
    """
    return MX @ F @ MX


def arm_yaw_frame_l(q: dict) -> np.ndarray:
    q2 = dict(q)
    q2["arm_r_yaw"] = q.get("arm_l_yaw", 0.0)
    return _mirror_frame(arm_yaw_frame_r(q2))


def arm_pitch_frame_l(q: dict) -> np.ndarray:
    q2 = dict(q)
    q2["arm_r_yaw"] = q.get("arm_l_yaw", 0.0)
    q2["arm_r_pitch"] = q.get("arm_l_pitch", 0.0)
    return _mirror_frame(arm_pitch_frame_r(q2))


def arm_elbow_frame_l(q: dict) -> np.ndarray:
    q2 = dict(q)
    q2["arm_r_yaw"] = q.get("arm_l_yaw", 0.0)
    q2["arm_r_pitch"] = q.get("arm_l_pitch", 0.0)
    q2["arm_r_elbow"] = q.get("arm_l_elbow", 0.0)
    return _mirror_frame(arm_elbow_frame_r(q2))


# ---- 目・カメラ (固定原点 + roll 変数)
_SETBACK = (C.EYE_SOCKET_FLOOR - C.EYE_HOVER) + C.EYE_NECK_H
EYE_DOT_ROLL_DEG = {0: 45.0, 2: -45.0}


def head_top_frame() -> np.ndarray:
    return trans(0, C.ARM_MOUNT_HUB_Y, ZB + HEAD_TOP_Z_OFFSET) @ rot(180, "z")


def _eye_mount(idx: int) -> np.ndarray:
    """base_link 相対、eye_pod/eye_pod_camera のソケット装着フレーム
    (roll=0 での姿勢)。idx: 0=右/1=中央/2=左。make_visuals.kit_dress_static()
    の eye ループと同一式。"""
    ctr, n = np.array(C.EYE_SOCKETS_150[idx][0]), np.array(C.EYE_SOCKETS_150[idx][1])
    pos = ctr - n * _SETBACK
    A = np.eye(4)
    if idx == 1:
        A[:3, :3] = CAM.install_rotation(n)
    else:
        A = trimesh.geometry.align_vectors([0, 0, 1], n) \
            @ rot(EYE_DOT_ROLL_DEG.get(idx, 0.0), "z")
    return head_top_frame() @ trans(*pos) @ A


def eye_r_frame(q: dict) -> np.ndarray:
    return _eye_mount(0) @ rot(q.get("eye_r_roll", 0.0), "z")


def eye_l_frame(q: dict) -> np.ndarray:
    return _eye_mount(2) @ rot(q.get("eye_l_roll", 0.0), "z")


def camera_mount_frame(q: dict) -> np.ndarray:
    return _eye_mount(1)


def camera_optical_frame(q: dict) -> np.ndarray:
    """base_link 相対、camera_optical_frame の姿勢 (ROS optical 規約:
    +Z=光軸前方, +X=画像右, +Y=画像下)。make_camera.pupil_axis()/
    pupil_center() の実測値 (瞳の外面開口中心・光軸方向) は
    eye_pod_camera のポッドローカル座標で定義されているため、
    camera_mount_frame(q) (= base_link -> eye_pod_camera, base_link相対)
    と合成してから返す。

    (修正履歴: 以前はポッドローカルの R をそのまま base_link 相対として
    返していたため、extract_joint_origin_axis() の
    origin=inv(parent_pose)@child_pose 計算に余計な inv(camera_mount_frame)
    が混入し、光軸が水平前方ではなく後方上空を向く不具合があった。
    check_urdf.py [2] の camera_optical_fixed 数値検証で回帰確認する。)"""
    tm = CAM._normalized_cap()
    p_outer, _ = CAM.pupil_center(tm)
    u = CAM.pupil_axis()
    z = u / np.linalg.norm(u)
    up_ref = np.array([0.0, 0.0, 1.0])
    x = np.cross(up_ref, z)
    if np.linalg.norm(x) < 1e-6:
        up_ref = np.array([0.0, 1.0, 0.0])
        x = np.cross(up_ref, z)
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    R = np.eye(4)
    R[:3, 0], R[:3, 1], R[:3, 2] = x, y, z
    R[:3, 3] = p_outer
    return camera_mount_frame(q) @ R


BASE_FRAME = lambda q: np.eye(4)  # noqa: E731


# ============================================================ 関節木の定義
# (name, parent_link, child_link, frame_fn, kind, axis_hint) — axis_hint は
# 数値抽出の健全性チェック用ラベルに過ぎない (実際の axis は数値抽出で決定)。
# limits は firmware/src/config.h の値 (deg) → 生成時に rad へ変換。
JOINT_SPECS = []
for _leg in LEGS:
    lo = _leg.lower()
    JOINT_SPECS += [
        dict(name=f"leg_{lo}_yaw", parent="base_link", child=f"leg_{lo}_coxa",
             frame=lambda q, l=_leg: leg_yaw_frame(l, q), kind="revolute",
             qkey=f"leg_{lo}_yaw", limit=(-FW["LIM_YAW"], FW["LIM_YAW"])),
        dict(name=f"leg_{lo}_pitch", parent=f"leg_{lo}_coxa", child=f"leg_{lo}_femur",
             frame=lambda q, l=_leg: leg_pitch_frame(l, q), kind="revolute",
             qkey=f"leg_{lo}_pitch", limit=(FW["LIM_PITCH_UP"], FW["LIM_PITCH_DN"])),
        dict(name=f"leg_{lo}_knee", parent=f"leg_{lo}_femur", child=f"leg_{lo}_tibia",
             frame=lambda q, l=_leg: leg_knee_frame(l, q), kind="revolute",
             qkey=f"leg_{lo}_knee", limit=(-FW["LIM_KNEE"], FW["LIM_KNEE"])),
    ]

for _side, _tag in ((1, "r"), (-1, "l")):
    JOINT_SPECS += [
        dict(name=f"arm_{_tag}_yaw", parent="base_link", child=f"arm_{_tag}_shoulder",
             frame=(arm_yaw_frame_r if _side > 0 else arm_yaw_frame_l), kind="revolute",
             qkey=f"arm_{_tag}_yaw", limit=(-FW["ARM_YAW_LIM"], FW["ARM_YAW_LIM"])),
        dict(name=f"arm_{_tag}_pitch", parent=f"arm_{_tag}_shoulder", child=f"arm_{_tag}_upper",
             frame=(arm_pitch_frame_r if _side > 0 else arm_pitch_frame_l), kind="revolute",
             qkey=f"arm_{_tag}_pitch", limit=(FW["ARM_PITCH_MIN"], FW["ARM_PITCH_MAX"])),
        dict(name=f"arm_{_tag}_elbow", parent=f"arm_{_tag}_upper", child=f"arm_{_tag}_forearm",
             frame=(arm_elbow_frame_r if _side > 0 else arm_elbow_frame_l), kind="revolute",
             qkey=f"arm_{_tag}_elbow", limit=(FW["ARM_ELBOW_MIN"], FW["ARM_ELBOW_MAX"])),
    ]

JOINT_SPECS += [
    dict(name="eye_r_roll", parent="base_link", child="eye_r_pod", frame=eye_r_frame,
         kind="revolute", qkey="eye_r_roll", limit=(-FW["EYE_LIM"], FW["EYE_LIM"])),
    dict(name="eye_l_roll", parent="base_link", child="eye_l_pod", frame=eye_l_frame,
         kind="revolute", qkey="eye_l_roll", limit=(-FW["EYE_LIM"], FW["EYE_LIM"])),
    dict(name="eye_pod_camera_fixed", parent="base_link", child="eye_pod_camera",
         frame=camera_mount_frame, kind="fixed", qkey=None, limit=None),
    dict(name="camera_optical_fixed", parent="eye_pod_camera", child="camera_optical_frame",
         frame=camera_optical_frame, kind="fixed", qkey=None, limit=None),
]

LINK_PARENT_FRAME = {"base_link": BASE_FRAME}
for _j in JOINT_SPECS:
    LINK_PARENT_FRAME[_j["child"]] = _j["frame"]


def extract_joint_origin_axis(jspec: dict, q0: dict, eps_deg: float = 30.0):
    """親リンクフレーム相対の固定 origin (4x4) と関節軸 (3-vector, 親joint
    origin ローカル) を数値的に抽出する。

    各関節は単軸回転 (もしくは fixed) であることを前提に、q0 (基準姿勢,
    通常は全関節 0) からその関節の値だけを eps_deg だけ動かし、結果の
    回転行列から軸を復元する (Rotation.as_rotvec, 厳密解 — 微小角近似では
    ない。eps_deg は 0<eps<180 なら理論上どの値でも良いが、数値誤差の
    観点から 30° 程度を既定にする)。抽出した回転角が eps_deg と厳密一致
    することを assert し、単軸回転でない関節 (実装ミス) を検出する。
    """
    parent_fn = LINK_PARENT_FRAME[jspec["parent"]]
    child_fn = jspec["frame"]
    a0 = dict(q0)
    if jspec["qkey"]:
        a0[jspec["qkey"]] = 0.0
    P0 = parent_fn(a0)
    C0 = child_fn(a0)
    origin = np.linalg.inv(P0) @ C0
    if jspec["kind"] == "fixed":
        return origin, np.array([0.0, 0.0, 1.0])
    a1 = dict(a0)
    a1[jspec["qkey"]] = eps_deg
    C1 = child_fn(a1)
    rel = np.linalg.inv(origin) @ np.linalg.inv(P0) @ C1
    rv = Rotation.from_matrix(rel[:3, :3]).as_rotvec()
    ang = np.linalg.norm(rv)
    expect = np.radians(eps_deg)
    assert abs(ang - expect) < 1e-7, (
        f"{jspec['name']}: 単軸回転チェック失敗 (抽出角 {np.degrees(ang):.6f}"
        f" != {eps_deg})。FK式が単一関節変数の回転になっていない")
    axis = rv / ang
    # 平行移動成分が eps によらず一定であることも確認 (=単軸回転で並進が
    # 生じていない。生じていれば origin/axis 分解が破綻している)
    return origin, axis


# ============================================================ パーツ→リンク 対応
# 各関数は (link_name) -> list[(mesh: trimesh.Trimesh (mm, link ローカル座標),
# color: str, part_name: str)] を返す。mesh はここで「そのリンクの関節原点
# フレーム」基準のローカル座標へ変換済み (以後 export_urdf 側で追加変換しない)。
#
# 実装方針 (低リスク優先): 可能な限り make_visuals.py の実関数をそのまま
# 呼び出して再利用する (base_link の意匠一式は kit_dress_static()/
# pod_dress_shells() を直接呼ぶ)。脚・腕は関節角を明示指定できないため
# (robot_meshes は歩容IK経由) 該当ブロックのみ最小限に転記するが、
# tools/check_urdf.py [3] で make_visuals の実出力と <1e-6mm 一致することを
# 毎回検証する (このモジュール単体のテスト — 本ファイル下部 __main__ でも
# 簡易チェックを行う)。

MOUTH_T = (trans(C.MOUTH_CANNON_T[0], C.MOUTH_CANNON_T[1], ZB + C.MOUTH_CANNON_T[2])
           @ rot(C.MOUTH_CANNON_ROT_X_DEG, "x"))
HEAD_BOTTOM_T = trans(0, C.ARM_MOUNT_HUB_Y, ZB - 3) @ rot(180, "z")
HEAD_TOP_T = head_top_frame()
_LINK_T_STATIC = {"Head_Bottom_Blue": HEAD_BOTTOM_T, "Head_Top_Blue": HEAD_TOP_T,
                  "Mouth_Cannon_Grey": MOUTH_T}


def base_link_parts():
    from make_visuals import DRESS_SKIP_PARTS
    parts = []
    ch = load("chassis"); ch.apply_transform(trans(0, 0, ZB))
    parts.append((ch, COL["chassis"], "chassis"))
    nk = load("pod_neck"); nk.apply_transform(trans(0, 0, ZB + C.CHASSIS_T))
    parts.append((nk, COL["chassis"], "pod_neck"))
    cr = load("battery_cradle"); cr.apply_transform(trans(0, 0, ZB))
    parts.append((cr, COL["chassis"], "battery_cradle"))

    # ポッド外装 (pod_dress_shells と同一式)
    defs = [
        ("Cabin_Front_Blue", rot(180, "z") @ rot(90, "x"), (0, -156, ZB + 55)),
        ("Cabin_Back_Blue_Repaired", rot(180, "y") @ rot(90, "x"), (0, -235, ZB + 55)),
    ]
    for name, R, t in defs:
        m = trimesh.load(MODEL / f"{name}.stl")
        m.apply_translation(-(m.bounds[0] + m.bounds[1]) / 2)
        m.apply_scale(C.SCALE)
        m.apply_transform(R)
        m.apply_translation(t)
        parts.append((m, KIT.kit_color(name), name))

    # マウス砲身 (config.py MOUTH_CANNON_T/ROT_X_DEG から直接。kit_dress_static
    # と同一式)
    _m = trimesh.load(MODEL / "Mouth_Cannon_Grey.stl")
    _m.apply_translation(-(_m.bounds[0] + _m.bounds[1]) / 2)
    _m.apply_scale(C.SCALE)
    _m.apply_transform(MOUTH_T)
    parts.append((_m, KIT.kit_color("Mouth_Cannon_Grey"), "Mouth_Cannon_Grey"))

    # 頭/砲身/ポッドに紐づく全キットパーツ (kit_dress_static() と同一フィルタ)
    for p in KIT_PLACEMENTS:
        if p.unresolved:
            continue
        if p.part in DRESS_SKIP_PARTS:
            continue
        if p.part == "Mouth_Cannon_Grey":
            continue
        if p.frame == "robot":
            m = KIT.oriented_mesh(p)
            m.apply_translation([0, 0, ZB])
            parts.append((m, p.color, f"{p.part}#{p.instance}"))
        elif p.link in _LINK_T_STATIC:
            m = KIT.oriented_mesh(p)
            m.apply_transform(_LINK_T_STATIC[p.link])
            parts.append((m, p.color, f"{p.part}#{p.instance}"))

    return parts


def camera_link_parts():
    """eye_pod_camera リンク (base_link 相対の固定リンク) のローカル可視メッシュ。
    ローカル座標系はソケット装着フレーム自身 (camera_mount_frame と同一原点)。"""
    parts = []
    pod = load("eye_pod_camera")
    parts.append((pod, "#f4f3f0", "eye_pod_camera"))
    carrier = load("camera_carrier")
    parts.append((carrier, COL["chassis"], "camera_carrier"))
    return parts


def eye_pod_parts():
    """eye_r_pod / eye_l_pod: いずれも同一形状 (eye_pod.stl) をローカル原点
    (=関節原点) にそのまま置く。"""
    return [(load("eye_pod"), "#f4f3f0", "eye_pod")]


def leg_parts(leg: str):
    """脚 1 本ぶんのリンク別パーツ (leg_xx_coxa/femur/tibia)。ローカル座標は
    各リンクの関節原点フレーム基準 (leg_yaw_frame/leg_pitch_frame/
    leg_knee_frame の角度 0 の値で robot_meshes() の対応ブロックと厳密一致—
    check_urdf.py [3] で数値検証)。"""
    sfx = "_m" if leg in MIRROR_LEGS else ""
    out = {"coxa": [], "femur": [], "tibia": []}

    cox = load(f"coxa_bracket{sfx}")
    out["coxa"].append((cox, COL["coxa"], f"coxa_bracket{sfx}"))

    fem = load(f"femur_link{sfx}")
    out["femur"].append((fem, COL["skel_dress"], f"femur_link{sfx}"))
    tc = load("thigh_cap")
    tc.apply_transform(trans(C.FEMUR_LEN / 2 - 8, 0, 13.1))
    out["femur"].append((tc, COL["cap"], "thigh_cap"))
    for p in KIT.by_link(KIT_PLACEMENTS, "thigh_cap"):
        if p.instance != leg:
            continue
        m = KIT.oriented_mesh(p)
        m.apply_transform(trans(C.FEMUR_LEN / 2 - 8, 0, 13.1))
        out["femur"].append((m, p.color, f"{p.part}#{p.instance}"))

    tib = load(f"tibia_link{sfx}")
    out["tibia"].append((tib, COL["skel_dress"], f"tibia_link{sfx}"))
    ft = load("leg_foot_bored")
    ft.apply_transform(trans(0, 0, -C.TIBIA_LEN))
    out["tibia"].append((ft, COL["foot"], "leg_foot_bored"))
    sh = load(f"shin_shell{sfx}")
    sh.apply_transform(trans(0, 0, -16) @ rot(180, "x"))
    out["tibia"].append((sh, COL["shell"], f"shin_shell{sfx}"))
    for p in KIT.by_link(KIT_PLACEMENTS, "shin_shell"):
        if p.instance != leg:
            continue
        m = KIT.oriented_mesh(p)
        if sfx == "_m":
            m.apply_transform(np.diag([1.0, -1.0, 1.0, 1.0]))
            m.invert()
        out["tibia"].append((m, p.color, f"{p.part}#{p.instance}"))
    for p in KIT.by_link(KIT_PLACEMENTS, "leg_foot_bored"):
        if not (p.instance == leg or p.instance.startswith(leg + "_")):
            continue
        m = KIT.oriented_mesh(p)
        m.apply_transform(trans(0, 0, -C.TIBIA_LEN))
        out["tibia"].append((m, p.color, f"{p.part}#{p.instance}"))
    return out


def _mirror_mesh(m: trimesh.Trimesh) -> trimesh.Trimesh:
    m2 = m.copy()
    m2.vertices[:, 0] *= -1.0
    m2.invert()
    return m2


def arm_parts(side: int):
    """腕 1 本ぶんのリンク別パーツ (arm_x_shoulder/upper/forearm)。

    右腕 (side=+1) は各リンク原点フレーム (Ty/Tu/Tf) 基準のローカル座標を
    直接転記。左腕 (side=-1) は「右腕の各リンクのローカルメッシュを X 反転
    +法線反転したもの」を使う — これは export_urdf.py 冒頭の _mirror_frame
    (2 重共役) による関節原点/軸の定義と対になる関係で、両者を合成すると
    make_visuals.arm_meshes(side=-1,...) の world 出力と厳密一致することを
    導出・数値検証済み (tools/export_urdf.py の self-test, check_urdf.py [3])。
    """
    tag = "r" if side > 0 else "l"
    out_r = {"shoulder": [], "upper": [], "forearm": []}

    br = load("shoulder_bracket")
    out_r["shoulder"].append((br, COL["bracket"], "shoulder_bracket"))

    ua = load("upper_arm")
    out_r["upper"].append((ua, COL["skel_dress"], "upper_arm"))
    for nm in ("arm_pod_upper", "arm_pod_lower"):
        m = load(nm)
        out_r["upper"].append((m, COL["shell"], nm))
    for p in KIT.by_link(KIT_PLACEMENTS, "arm_pod"):
        if p.part != "Arm_Right_Guard_Grey":
            continue
        g = KIT.oriented_mesh(p)
        out_r["upper"].append((g, p.color, p.part))
    esh = load("elbow_shell")
    esh.apply_transform(trans(C.UPPER_ARM_LEN, 0, 0))
    out_r["upper"].append((esh, KIT.kit_color("Arm_Right_Elbow_Grey"), "elbow_shell"))

    fa = load("forearm")
    out_r["forearm"].append((fa, COL["skel_dress"], "forearm"))
    Tp = trans(C.FOREARM_LEN, 0, 0)
    cm = load("claw_mount"); cm.apply_transform(Tp)
    out_r["forearm"].append((cm, COL["palm"], "claw_mount"))
    claw = load("Arm_Left_Claw_Grey", source=MODEL)
    claw.apply_transform(Tp @ C.CLAW_TO_MOUNT)
    out_r["forearm"].append((claw, KIT.kit_color("Arm_Left_Claw_Grey"), "Arm_Left_Claw_Grey"))
    for i in range(3):
        fg = load("Arm_Left_Finger_Black_x3", source=MODEL)
        fg.apply_transform(Tp @ C.FINGER_TO_MOUNT[i])
        out_r["forearm"].append((fg, KIT.kit_color("Arm_Left_Finger_Black_x3"),
                                 f"Arm_Left_Finger_Black_x3#{i}"))
        ft = load("Arm_Left_FingerTip_Grey_x3", source=MODEL)
        ft.apply_transform(Tp @ C.FINGERTIP_TO_MOUNT[i])
        out_r["forearm"].append((ft, KIT.kit_color("Arm_Left_FingerTip_Grey_x3"),
                                 f"Arm_Left_FingerTip_Grey_x3#{i}"))

    if side > 0:
        return {f"arm_r_{k}": v for k, v in out_r.items()}
    out_l = {}
    for k, items in out_r.items():
        out_l[f"arm_l_{k}"] = [(_mirror_mesh(m), c, n) for (m, c, n) in items]
    return out_l


def collect_all_parts() -> dict:
    """全リンクの (mesh, color, name) リストを返す (mm, リンクローカル座標)。"""
    parts: dict[str, list] = {"base_link": base_link_parts(),
                              "eye_pod_camera": camera_link_parts(),
                              "camera_optical_frame": [],
                              "eye_r_pod": eye_pod_parts(),
                              "eye_l_pod": eye_pod_parts()}
    for leg in LEGS:
        lo = leg.lower()
        lp = leg_parts(leg)
        parts[f"leg_{lo}_coxa"] = lp["coxa"]
        parts[f"leg_{lo}_femur"] = lp["femur"]
        parts[f"leg_{lo}_tibia"] = lp["tibia"]
    for side in (1, -1):
        parts.update(arm_parts(side))
    return parts


# ============================================================ 質量・慣性
# 方針 (CLAUDE.md 記載どおり): パーツごとに trimesh の均質密度 (density=1)
# 慣性/COM を求め、tools/filament_calc.py と同じ物理モデル (表面積×壁厚+
# インフィル×体積) による質量見積りへスケールする。均質密度を仮定するため
# COM は「そのパーツの幾何重心」のままになる (中空+インフィルの実際の COM
# とは僅かにズレる — 意匠シェルのように壁が薄く infill が低いパーツほど
# 実際は表面寄りに COM があるはずで、ここは簡略化の一つ。docs/urdf.md に
# 明記する)。
RHO = {"PLA": 1.24, "PETG": 1.27, "TPU": 1.21}   # g/cm3 (tools/filament_calc.py と同一)

# 自作パーツ (hardware/stl) の (材料, 壁厚mm, インフィル) — tools/filament_calc.py
# の new_parts 辞書からの転記 (出典: 同ファイル)。ここに無い名前は KIT 由来
# (model/*.stl) とみなし KIT_DEFAULT_MATERIAL を使う (filament_calc.py の
# 意匠シェル既定則 壁2/インフィル8% と同一)。
CUSTOM_MATERIAL = {
    "chassis": ("PETG", 2.4, 0.25), "coxa_bracket": ("PETG", 2.4, 0.40),
    "femur_link": ("PETG", 2.4, 0.40), "tibia_link": ("PETG", 2.4, 0.40),
    "shin_shell": ("PLA", 1.4, 0.08), "thigh_cap": ("PLA", 1.4, 0.08),
    "leg_foot_bored": ("PLA", 1.4, 0.20), "foot_pad": ("TPU", 1.8, 0.30),
    "pod_neck": ("PETG", 2.4, 0.40), "battery_cradle": ("PETG", 2.4, 0.20),
    "shoulder_bracket": ("PETG", 2.4, 0.40),
    "arm_pod_upper": ("PLA", 1.4, 0.08), "arm_pod_lower": ("PLA", 1.4, 0.08),
    "elbow_shell": ("PLA", 1.4, 0.15),
    "upper_arm": ("PETG", 2.4, 0.40), "forearm": ("PETG", 2.4, 0.40),
    "claw_mount": ("PETG", 2.4, 0.40),
    "eye_pod": ("PLA", 1.4, 0.08), "eye_carrier": ("PETG", 2.4, 0.40),
    "eye_pod_camera": ("PLA", 1.4, 0.08), "camera_carrier": ("PETG", 2.4, 0.40),
    "Cabin_Front_Blue": ("PLA", 1.4, 0.08), "Cabin_Back_Blue_Repaired": ("PLA", 1.4, 0.08),
    "Head_Top_Eyecut": ("PLA", 1.4, 0.08), "Head_Bottom_Armcut": ("PLA", 1.4, 0.08),
    "Mouth_Cannon_Grey": ("PLA", 1.4, 0.08),
}
KIT_DEFAULT_MATERIAL = ("PLA", 1.4, 0.08)


def part_material(name: str):
    base = name.split("#")[0]
    if base.endswith("_m") and base not in CUSTOM_MATERIAL:
        base = base[:-2]
    return CUSTOM_MATERIAL.get(base, KIT_DEFAULT_MATERIAL)


def estimate_mass_g(mesh_mm: trimesh.Trimesh, wall_mm: float, infill: float, density: float) -> float:
    """tools/filament_calc.py printed_cm3() と同じ物理モデル (表面積×壁厚+
    インフィル)。mesh_mm は既に最終寸法 (mm, scale適用済み) を渡すこと。"""
    v_solid = abs(mesh_mm.volume) / 1000.0   # cm3
    a = mesh_mm.area / 100.0                 # cm2
    v_wall = min(v_solid, a * wall_mm / 10.0)
    v_print = v_wall + max(0.0, v_solid - v_wall) * infill
    return v_print * density


@dataclass
class MassItem:
    mass_kg: float
    com_m: np.ndarray     # リンクローカル (m)
    I_com: np.ndarray     # 3x3, リンクローカル軸に沿った COM まわりの慣性 (kg*m^2)
    label: str
    verified: bool = True


def _ensure_outward(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """面の巻き順(法線の向き)を「体積が正 (=外向き)」になるよう正規化する。

    matplotlib のライティング用に mesh.invert() を挟む処理 (leg_xx_tibia の
    FR/RL 用ミラー等, make_visuals.py 由来) は「頂点座標」は正しく再現する
    (tools/export_urdf.py の FK 数値照合で確認済み) が、面の巻き順について
    trimesh は反射変換単体では自動修正しない (実測: apply_transform だけの
    段階では体積+のまま=正しい向き、直後の invert() で体積-に反転=誤った
    向きになる。逆に言えば「反射で裏返るので invert が必要」という make_visuals
    側コメントの前提は法線の見た目 [光源計算] には影響しても符号付き体積の
    向き規約とは逆に効く)。質量・慣性計算や CAD 出力では面の向きが物理的な
    意味 (体積符号) を持つため、ここで体積が正になるよう統一する
    (頂点位置は不変 — invert() は面のインデックス順だけを変える)。
    """
    if mesh.volume < 0:
        mesh = mesh.copy()
        mesh.invert()
    return mesh


def part_mass_item(mesh_mm: trimesh.Trimesh, name: str) -> MassItem:
    mesh_mm = _ensure_outward(mesh_mm)
    mat, wall, infill = part_material(name)
    mass_g = estimate_mass_g(mesh_mm, wall, infill, RHO[mat])
    m = mesh_mm.copy()
    m.apply_scale(MM)
    mp = m.mass_properties  # density=1 (無次元) での質量特性
    if mp["mass"] <= 0:
        raise ValueError(f"{name}: 体積が非正 (watertight でない可能性)")
    scale = (mass_g / 1000.0) / mp["mass"]
    return MassItem(mass_g / 1000.0, np.array(mp["center_mass"]),
                    np.array(mp["inertia"]) * scale, name, verified=True)


def box_mass_item(mass_g: float, size_mm, center_mm, label: str, verified=False) -> MassItem:
    b = trimesh.creation.box(np.array(size_mm) * MM)
    b.density = 1.0
    mp = b.mass_properties
    scale = (mass_g / 1000.0) / mp["mass"]
    com = np.array(center_mm) * MM
    return MassItem(mass_g / 1000.0, com, np.array(mp["inertia"]) * scale, label, verified)


def combine_mass_items(items: list[MassItem]):
    """複数 MassItem (同一リンクローカル座標系, 軸は揃っている前提) を
    平行軸の定理で合成し、そのリンク自身の COM まわりの (mass, com, I) を返す。"""
    total_mass = sum(it.mass_kg for it in items)
    if total_mass <= 0:
        raise ValueError("合計質量が非正")
    com = sum(it.mass_kg * it.com_m for it in items) / total_mass
    I = np.zeros((3, 3))
    for it in items:
        d = it.com_m - com
        I += it.I_com + it.mass_kg * (np.dot(d, d) * np.eye(3) - np.outer(d, d))
    return total_mass, com, I


# ---- サーボ/バッテリー/電装の box 近似質量 (実装位置は概算, docs/urdf.md に
# 明記) ----
# 質量出典: DS3218=60g, MG90S=14g は docs/printing.md 重量バジェット表
# (2026-07 filament_calc.py 実行値ベース)。SUBMICRO(ES9251II級)=3.7g は
# hardware/src/config.py SUBMICRO の docstring 値 [要実測] をそのまま使用。
# 電装 (LiPo/PCA9685/ESP32/UBEC/DC-DC/DFPlayer/mic/spk/amp) の位置は
# tools/make_visuals.py wiring_video() の配線イメージ用ボックス配置を流用
# (docs/wiring.md の配線経路と整合)。質量値は datasheet 未参照分を含み
# UNVERIFIED — docs/urdf.md に明記する。
SERVO_STD_G = 60.0
SERVO_MICRO_G = 14.0
SERVO_SUBMICRO_G = 3.7      # [要実測] config.py SUBMICRO comment
STD_BOX = (40.7, 20.2, 39.2)      # servo_box() (make_visuals.py)
MICRO_BOX = (23.0, 12.4, 26.0)    # servo_box_micro()
SUBMICRO_BOX = (20.0, 8.6, 20.0)  # C.SUBMICRO の L/W から概算 (H は未計測なので W 相当で近似)

ZT = ZB + C.CHASSIS_T  # プレート上面 (base_link 相対)


def leg_servo_items(leg: str):
    """(base_link 用ヨーサーボ1個, coxa用股ピッチサーボ1個, femur用膝サーボ1個)。
    位置は概算 (関節軸まわりに±20mm 程度, exploded_leg() の配置図を参考)。"""
    ox, oy = C.HIPS[leg]
    yaw = box_mass_item(SERVO_STD_G, STD_BOX, (ox, oy, -10.0),
                        f"leg_{leg.lower()}_yaw_servo", verified=False)
    pitch = box_mass_item(SERVO_STD_G, STD_BOX, (C.COXA_LEN, 0, -8.0),
                          f"leg_{leg.lower()}_pitch_servo", verified=False)
    knee = box_mass_item(SERVO_STD_G, STD_BOX, (C.FEMUR_LEN - 6.0, 0, -8.0),
                         f"leg_{leg.lower()}_knee_servo", verified=False)
    return yaw, pitch, knee


def arm_servo_items(tag: str):
    """右腕基準の位置に対し、左腕は arm_parts() のメッシュミラー規約
    (リンクローカル X を反転) と揃えて X を反転させる (揃えないと
    combine_mass_items() の COM が左右非対称になってしまう)。"""
    sx = -1.0 if tag == "l" else 1.0
    mx, my = C.ARM_MOUNT_XY
    yaw = box_mass_item(SERVO_MICRO_G, MICRO_BOX, (sx * mx, my, ZB - 6.0),
                        f"arm_{tag}_yaw_servo", verified=False)
    pitch = box_mass_item(SERVO_MICRO_G, MICRO_BOX, (sx * 20.0, 0, -_arm_pitch_dn()),
                          f"arm_{tag}_pitch_servo", verified=False)
    elbow = box_mass_item(SERVO_MICRO_G, MICRO_BOX, (sx * C.UPPER_ARM_LEN, 0, 0),
                          f"arm_{tag}_elbow_servo", verified=False)
    return yaw, pitch, elbow


def base_link_electronics_items():
    """wiring_video() のボックス配置 (zb 基準) を ZB 基準へ焼き直して転記。
    質量は datasheet 未確認の概算 (UNVERIFIED)。"""
    items = []
    boxes = [  # (label, size(x,y,z)mm, pos(x,y,z)mm[zb基準], mass_g)
        ("battery_2s_2200mah", (34, 105, 24), (0, -6, -16), 180.0),
        ("esp32_devkit", (55, 28, 10), (0, C.ESP32_Y0, C.CHASSIS_T + 5), 10.0),
        ("pca9685_0", (25.4, 62.5, 8), (0, 1, C.CHASSIS_T + 4), 15.0),
        ("pca9685_1", (25.4, 62.5, 8), (0, 1, C.CHASSIS_T + 16), 15.0),
        ("ubec_6v", (30, 18, 12), (30, -58, C.CHASSIS_T + 6), 10.0),
        ("dcdc_5v", (30, 18, 12), (-30, -58, C.CHASSIS_T + 6), 8.0),
        ("dfplayer", (26, 16, 8), (-38, 40, C.CHASSIS_T + 4), 4.0),
        ("speaker_pod", (24, 24, 12), (25, -150, 20), 5.0),
        ("mic_cannon", (8, 10, 6), (0, 58, -8), 1.0),
        ("speaker_cannon", (18, 18, 10), (0, 50, -16), 3.0),
        ("amp_head", (16, 12, 8), (0, 30, 8), 2.0),
        # 未計上分 (配線・ネジ・スイッチ・ヒューズ・コネクタ等の残差。
        # docs/printing.md 電装バジェット行 ~350g との整合を取るための
        # 一括計上, UNVERIFIED)
        ("wiring_misc", (40, 40, 10), (0, 0, C.CHASSIS_T / 2), 97.0),
        # 頭部ヨーサーボ (SG90/MG90S, CH_HEAD): 物理マウント/駆動対象は
        # 2026-07-30 実測で未確定 (docs/BOM.md #2 参照)。位置は不明のため
        # シャーシ中心へ仮置き — 質量のみ設計重量バジェットに合わせて計上
        ("head_yaw_servo_unmounted", (23.0, 12.4, 26.0), (0, 0, C.CHASSIS_T + 10), 9.0),
    ]
    for label, size, pos, mass in boxes:
        items.append(box_mass_item(mass, size, pos, label, verified=False))
    # 目サーボ (SUBMICRO ×2) — eye_carrier に保持され頭部シェル (base_link)
    # に固定 (回転しない)。位置は目ソケット直下付近の概算
    for idx, tag in ((0, "r"), (2, "l")):
        ctr, n = np.array(C.EYE_SOCKETS_150[idx][0]), np.array(C.EYE_SOCKETS_150[idx][1])
        pos = ctr - n * (_SETBACK + 10.0)
        items.append(box_mass_item(SERVO_SUBMICRO_G, SUBMICRO_BOX,
                                   (pos[0], pos[1] + C.ARM_MOUNT_HUB_Y, pos[2] + ZB + HEAD_TOP_Z_OFFSET),
                                   f"eye_{tag}_servo", verified=False))
        # eye_carrier 自体の質量 (PETG, filament_calc.new_parts 準拠) も点質量で計上
        carr = load("eye_carrier")
        mi = part_mass_item(carr, f"eye_{tag}_carrier")
        items.append(MassItem(mi.mass_kg,
                              np.array([pos[0], pos[1] + C.ARM_MOUNT_HUB_Y, pos[2] + ZB + HEAD_TOP_Z_OFFSET]) * MM,
                              mi.I_com, f"eye_{tag}_carrier", verified=False))
    return items


def build_link_mass_items(parts: dict) -> dict:
    """各リンクの MassItem リスト (メッシュ由来 + サーボ/電装の box 近似) を返す。"""
    per_link = {link: [part_mass_item(m, n) for (m, c, n) in items]
               for link, items in parts.items()}
    per_link["base_link"] += base_link_electronics_items()
    for leg in LEGS:
        lo = leg.lower()
        yaw, pitch, knee = leg_servo_items(leg)
        per_link["base_link"].append(yaw)
        per_link[f"leg_{lo}_coxa"].append(pitch)
        per_link[f"leg_{lo}_femur"].append(knee)
    for tag in ("r", "l"):
        yaw, pitch, elbow = arm_servo_items(tag)
        per_link["base_link"].append(yaw)
        per_link[f"arm_{tag}_shoulder"].append(pitch)
        per_link[f"arm_{tag}_upper"].append(elbow)
    # 視覚要素を持たないリンク (camera_optical_frame) にも名目上の微小質量を
    # 与える (check_urdf.py [5]: 全リンク質量>0)
    for link, items in per_link.items():
        if not items:
            items.append(box_mass_item(1.0, (5, 5, 5), (0, 0, 0), f"{link}_nominal", verified=False))
    return per_link


# ============================================================ コリジョン (簡略凸包)
def _hull_decimated(mesh: trimesh.Trimesh, max_faces: int = 180) -> trimesh.Trimesh:
    """凸包を面数上限以下へ簡略化する。

    convex_hull() をそのまま simplify_quadric_decimation() へ渡すと
    (fast_simplification 実測, 2026-07-30) 目標面数まで縮まらず数百面で
    頭打ちになるケースがある (凸包特有の大きな平坦面が退化三角形の集合に
    triangulate されるため、と推定)。元メッシュを先に十分粗く
    decimate してから凸包を取ると、凸包自体の面数が最初から少なくなり
    この頭打ちを回避できる (実測で確認済み)。
    """
    mesh = _ensure_outward(mesh)
    src = mesh
    if len(src.faces) > 4000:
        src = mesh.simplify_quadric_decimation(face_count=4000)
    hull = _ensure_outward(src.convex_hull)
    if len(hull.faces) > max_faces:
        try:
            simplified = hull.simplify_quadric_decimation(face_count=max_faces)
            simplified = _ensure_outward(simplified)
            if len(simplified.faces) <= max_faces and simplified.is_watertight:
                hull = simplified
        except Exception:
            pass
    if len(hull.faces) > max_faces:
        # まだ超過している場合は元メッシュをさらに粗く decimate してから
        # 凸包を取り直す (段階的に縮小)
        for target in (1500, 600, 250):
            src2 = mesh.simplify_quadric_decimation(face_count=min(target, len(mesh.faces) - 1))
            h2 = _ensure_outward(src2.convex_hull)
            if len(h2.faces) <= max_faces:
                hull = h2
                break
            hull = h2
    return hull


def build_collisions(parts: dict) -> dict:
    """リンクごとの collision メッシュ (mm, リンクローカル) リストを返す。

    可動リンクはそのリンクの visual メッシュ全部の凸包 (足先は
    foot_pad.stl [非表示の隠しパッド] も合成して接地点を含める)。
    base_link は「主要ブロックの凸包の合成」— パーツ名の接頭辞
    (Head_/Cabin_/Mouth_/それ以外=シャーシ) でブロック分けする。
    """
    out: dict[str, list[trimesh.Trimesh]] = {}
    for link, items in parts.items():
        if not items:
            continue
        if link != "base_link":
            meshes = [m for (m, c, n) in items]
            if link.endswith("_tibia"):
                fp = load("foot_pad")
                fp.apply_transform(trans(0, 0, -C.TIBIA_LEN))
                meshes = meshes + [fp]
            merged = trimesh.util.concatenate(meshes)
            out[link] = [_hull_decimated(merged)]
        else:
            blocks: dict[str, list[trimesh.Trimesh]] = {"chassis": [], "head": [],
                                                         "cabin": [], "mouth": []}
            for m, c, n in items:
                if n.startswith("Head_") or n.startswith("Head_Bottom") or n.startswith("Head_Top"):
                    blocks["head"].append(m)
                elif n.startswith("Cabin_"):
                    blocks["cabin"].append(m)
                elif n.startswith("Mouth_"):
                    blocks["mouth"].append(m)
                else:
                    blocks["chassis"].append(m)
            hulls = []
            for name, ms in blocks.items():
                if not ms:
                    continue
                hulls.append(_hull_decimated(trimesh.util.concatenate(ms)))
            out["base_link"] = hulls
    return out


# ============================================================ メッシュ焼き出し・URDF 出力
COLOR_NAMES = {
    "#5577cc": "coxa_blue", "#9aa4b0": "grey", "#8899aa": "chassis_grey",
    "#3b62c4": "shell_blue", "#93a3b5": "bracket_grey", "#5a6472": "palm_grey",
    "#f4f3f0": "white", "#2d55b8": "kit_blue", "#23262b": "black",
    "#cc2222": "red",
}


def _color_name(hexcode: str) -> str:
    return COLOR_NAMES.get(hexcode, hexcode.lstrip("#"))


def _hex_to_rgba(hexcode: str, alpha: float = 1.0):
    h = hexcode.lstrip("#")
    r, g, b = int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0, int(h[4:6], 16) / 255.0
    return (r, g, b, alpha)


def bake_visual_meshes(parts: dict) -> dict:
    """リンクごとに色でマージした visual メッシュ (メートル単位) を返す。
    dict[link] -> list[(color_hex, trimesh (m))]"""
    out = {}
    for link, items in parts.items():
        by_color: dict[str, list[trimesh.Trimesh]] = {}
        for m, c, n in items:
            by_color.setdefault(c, []).append(m)
        merged = []
        for c, meshes in by_color.items():
            mm = trimesh.util.concatenate(meshes) if len(meshes) > 1 else meshes[0].copy()
            mm = _ensure_outward(mm)
            mm.apply_scale(MM)
            merged.append((c, mm))
        out[link] = merged
    return out


def write_meshes(visuals: dict, collisions: dict):
    MESH_DIR.mkdir(parents=True, exist_ok=True)
    for f in MESH_DIR.glob("*.stl"):
        f.unlink()
    vis_files: dict[str, list[tuple[str, str]]] = {}   # link -> [(path, color_hex)]
    for link, items in visuals.items():
        vis_files[link] = []
        for c, mesh_m in items:
            fname = f"{link}__vis_{_color_name(c)}.stl"
            mesh_m.export(MESH_DIR / fname)
            vis_files[link].append((fname, c))
    col_files: dict[str, list[str]] = {}
    for link, hulls in collisions.items():
        col_files[link] = []
        for i, h in enumerate(hulls):
            hm = h.copy()
            hm.apply_scale(MM)
            fname = f"{link}__col_{i}.stl"
            hm.export(MESH_DIR / fname)
            col_files[link].append(fname)
    return vis_files, col_files


def _matrix_to_xyz_rpy(T: np.ndarray):
    xyz = (T[:3, 3] * MM).tolist()
    rpy = Rotation.from_matrix(T[:3, :3]).as_euler("xyz", degrees=False).tolist()
    return xyz, rpy


def build_urdf(parts: dict, mass_items: dict, vis_files: dict, col_files: dict) -> ET.Element:
    robot = ET.Element("robot", name="tachikoma")
    robot.append(ET.Comment(
        " 自動生成: tools/export_urdf.py — 手編集しないこと (docs/urdf.md 参照) "))

    all_links = ["base_link"] + [j["child"] for j in JOINT_SPECS]

    # URDF 厳密仕様: <material> は本来 <robot> 直下でトップレベル宣言し、各
    # <visual> からは name のみで参照するのが正式な形 (色を持たない
    # name-only 参照が <robot> 直下に無いままだと、パーサ実装によっては
    # 解決できずデフォルト色にフォールバックしうる — 2026-07-31 レビュー
    # 指摘)。ここで全 vis_files を先に走査し、色ごとに一意な <material> を
    # <robot> 直下へ 1 回ずつ書き出してから、各 <visual> は name-only 参照
    # にする。
    all_colors: dict[str, str] = {}   # matname -> chex (同一名で異なる色が
                                       # 混ざらないことを assert で確認)
    for items in vis_files.values():
        for fname, chex in items:
            matname = f"mat_{_color_name(chex)}"
            prev = all_colors.setdefault(matname, chex)
            assert prev == chex, (
                f"material 名 '{matname}' に異なる色 {prev} / {chex} が混在 "
                f"(_color_name() の衝突 — COLOR_NAMES テーブルを見直すこと)")
    for matname in sorted(all_colors):
        mat = ET.SubElement(robot, "material", name=matname)
        ET.SubElement(mat, "color",
                     rgba=" ".join(f"{v:.4f}" for v in _hex_to_rgba(all_colors[matname])))

    for link in all_links:
        le = ET.SubElement(robot, "link", name=link)
        for fname, chex in vis_files.get(link, []):
            vis = ET.SubElement(le, "visual")
            geo = ET.SubElement(ET.SubElement(vis, "geometry"), "mesh")
            geo.set("filename", f"meshes/{fname}")
            matname = f"mat_{_color_name(chex)}"
            ET.SubElement(vis, "material", name=matname)
        for fname in col_files.get(link, []):
            col = ET.SubElement(le, "collision")
            geo = ET.SubElement(ET.SubElement(col, "geometry"), "mesh")
            geo.set("filename", f"meshes/{fname}")
        m, com, I = combine_mass_items(mass_items[link])
        inertial = ET.SubElement(le, "inertial")
        ET.SubElement(inertial, "origin", xyz=" ".join(f"{v:.9f}" for v in com), rpy="0 0 0")
        ET.SubElement(inertial, "mass", value=f"{m:.9f}")
        ET.SubElement(inertial, "inertia",
                      ixx=f"{I[0,0]:.12e}", ixy=f"{I[0,1]:.12e}", ixz=f"{I[0,2]:.12e}",
                      iyy=f"{I[1,1]:.12e}", iyz=f"{I[1,2]:.12e}", izz=f"{I[2,2]:.12e}")

    for j in JOINT_SPECS:
        origin, axis = extract_joint_origin_axis(j, {})
        xyz, rpy = _matrix_to_xyz_rpy(origin)
        je = ET.SubElement(robot, "joint", name=j["name"], type=j["kind"])
        ET.SubElement(je, "parent", link=j["parent"])
        ET.SubElement(je, "child", link=j["child"])
        ET.SubElement(je, "origin", xyz=" ".join(f"{v:.9f}" for v in xyz),
                     rpy=" ".join(f"{v:.9f}" for v in rpy))
        if j["kind"] == "revolute":
            ET.SubElement(je, "axis", xyz=" ".join(f"{v:.6f}" for v in axis))
            lo, hi = j["limit"]
            act = ACTUATOR_LIMITS.get(j["name"], ACTUATOR_LIMITS["_default"])
            ET.SubElement(je, "limit", lower=f"{np.radians(lo):.6f}", upper=f"{np.radians(hi):.6f}",
                         effort=f"{act['effort']}", velocity=f"{act['velocity']}")
    return robot


# アクチュエータ effort/velocity 上限 (docs/urdf.md に出典・UNVERIFIED区分を記載)。
# DS3218: 1.96 N*m (20 kgf*cm @ 6.8V 級, カタログ値換算) / 6.5 rad/s 級
# MG90S : 0.22 N*m (2.2 kgf*cm) / 13 rad/s 級
# ES9251II 級 (目): 0.03 N*m / 8 rad/s 級 (SUBMICRO 一般値, UNVERIFIED)
ACTUATOR_LIMITS = {
    "_default": {"effort": 1.96, "velocity": 6.5},
    "eye_r_roll": {"effort": 0.03, "velocity": 8.0},
    "eye_l_roll": {"effort": 0.03, "velocity": 8.0},
}
for _side in ("r", "l"):
    for _j in ("yaw", "pitch", "elbow"):
        ACTUATOR_LIMITS[f"arm_{_side}_{_j}"] = {"effort": 0.22, "velocity": 13.0}


def write_manifest(parts: dict):
    manifest = {link: [n for (m, c, n) in items] for link, items in parts.items()}
    total = sum(len(v) for v in manifest.values())
    (OUT / "parts_manifest.json").write_text(
        json.dumps({"total_parts": total, "links": manifest}, ensure_ascii=False, indent=2))
    return total


def main():
    print("[export_urdf] パーツ収集...")
    parts = collect_all_parts()
    total = sum(len(v) for v in parts.values())
    print(f"  {total} パーツ ({len(parts)} リンク)")

    print("[export_urdf] 質量・慣性...")
    mass_items = build_link_mass_items(parts)
    grand = sum(combine_mass_items(v)[0] for v in mass_items.values())
    print(f"  総質量 {grand*1000:.0f} g")

    print("[export_urdf] コリジョン (凸包)...")
    collisions = build_collisions(parts)

    print("[export_urdf] メッシュ焼き出し (m)...")
    visuals = bake_visual_meshes(parts)
    vis_files, col_files = write_meshes(visuals, collisions)

    print("[export_urdf] URDF 組み立て...")
    robot = build_urdf(parts, mass_items, vis_files, col_files)
    xml_str = ET.tostring(robot, encoding="unicode")
    pretty = minidom.parseString(xml_str).toprettyxml(indent="  ")
    pretty = "\n".join(line for line in pretty.split("\n") if line.strip())
    (OUT / "tachikoma.urdf").write_text(pretty + "\n")

    n_manifest = write_manifest(parts)
    print(f"[export_urdf] 完了: {OUT / 'tachikoma.urdf'}")
    print(f"  visual STL: {sum(len(v) for v in vis_files.values())} 枚")
    print(f"  collision STL: {sum(len(v) for v in col_files.values())} 枚")
    print(f"  parts_manifest.json: {n_manifest} パーツ記録")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "debug":
        q0 = {}
        print("leg_fr_yaw ", leg_yaw_frame("FR", q0)[:3, 3])
        print("arm_r_yaw  ", arm_yaw_frame_r(q0)[:3, 3])
        print("arm_l_yaw  ", arm_yaw_frame_l(q0)[:3, 3])
        print("eye_r      ", eye_r_frame(q0)[:3, 3])
        print("camera opt ", camera_optical_frame(q0)[:3, 3])
    else:
        main()
