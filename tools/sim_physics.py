#!/usr/bin/env python3
"""タチコマ歩行の MuJoCo 条件付き物理検証。

既定は +Y 前進8秒→旋回3秒、前後に静定/停止各1秒。50Hzで脚歩容・
スルー後の出力制約を計算し、500Hzで剛体の重力/地面接触を積分する。
脚目標は sim_gait の IK/歩容を再利用し、tools/tests/simulation_regression.py
で実際の firmware ヘッダーを C++ 実行した時系列と比較する。

各軸の実トルク/未制限要求/飽和時間率/速度/追従誤差、接触リンク、
符号付き変位、入力ハッシュを JSON に記録する。指定条件の不合格は exit 1。
過去の動画/metricsを既定で上書きしない。

トルク速度特性、質量/慣性、摩擦、PDゲイン、樹脂の撓み、ホーンのガタ、
電源/熱は実測前。自己衝突は無効、腕は READY + 前脚からの退避判定のみ。
初期状態は立位に配置済みで、通電/再有効化は対象外。したがってシミュレーション
PASS を実機製作・全数印刷の承認として使わない。詳細は JSON の
unverified_assumptions / physical_readiness および docs/audits/20260905/simulation.md。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import sim_gait as sg  # noqa: E402 (firmware 忠実歩容ロジック — 複製せず import)

import mujoco  # noqa: E402

URDF_PATH = ROOT / "hardware" / "urdf" / "tachikoma.urdf"
FW_CONFIG = (ROOT / "firmware" / "src" / "config.h").read_text()

# ---- firmware/src/config.h から直接読む (sim_gait.py と同じ規約: ハード
# コード複製ではなく単一の正から都度パース。drift 事故防止)
CYCLE_T = float(re.search(r"CYCLE_T\s*=\s*([\d.]+)f", FW_CONFIG).group(1))
_ready_m = re.search(r"ARM_POSE_READY\[3\]\s*=\s*\{([^}]+)\}", FW_CONFIG)
ARM_POSE_READY_DEG = [float(x) for x in re.findall(r"-?[\d.]+", _ready_m.group(1))]
# [2026-07-31 検証指摘 (1) 対応] 前脚(FR/FL)×腕 連成クランプ (arms.h
# L147/195) に必要な定数。ハードコード複製せず config.h から都度パース
# (CYCLE_T/ARM_POSE_READY_DEG と同じ規約)
ARM_YAW_LIM_DEG = float(re.search(r"ARM_YAW_LIM\s*=\s*([\d.]+)f", FW_CONFIG).group(1))
ARM_LEG_YAW_GATE_DEG = float(
    re.search(r"ARM_LEG_YAW_GATE_DEG\s*=\s*([\d.]+)f", FW_CONFIG).group(1))
_sign_m = re.search(r"ARM_LEG_YAW_SIGN\[2\]\s*=\s*\{([^}]+)\}", FW_CONFIG)
ARM_LEG_YAW_SIGN = [float(x) for x in re.findall(r"[+-]?\d+", _sign_m.group(1))]
# firmware gait.h: 「次の四半位相境界」は PHASE_OFF が 0.25 刻みであることに
# 由来する。sim_gait.DUTY (=0.75) から 1-DUTY として導出し、0.25 の再ハード
# コードを避ける (DUTY が変われば自動追従)
QUARTER = 1.0 - sg.DUTY

BODY_H_M = sg.BODY_H / 1000.0  # mm -> m
BODY_H_DEFAULT = float(re.search(r"BODY_H_DEF\s*=\s*([\d.]+)f", FW_CONFIG).group(1))
SERVO_HZ = int(re.search(r"SERVO_FREQ\s*=\s*(\d+)", FW_CONFIG).group(1))
LEG_SLEW_DPS = float(re.search(r"LEG_SLEW_DPS\s*=\s*([\d.]+)f", FW_CONFIG).group(1))

# [2026-07-31 検証指摘 (3)(4) 対応] 地面の視覚的な基準がなく、接地判定/並進量
# が動画から目視できなかった問題への対応で使う定数群。地面平面の半サイズ
# (build_model の geom size と共有 — 二重ハードコード回避) とグリッド1マスの
# 目標寸法 (歩行フェーズ実測並進量 ~0.11-0.15m が1マス強で読み取れる密度)。
PLANE_HALF_SIZE_M = 5.0
GROUND_GRID_SQUARE_M = 0.3


class PhaseDriver:
    """firmware/src/gait.h Gait::update() の位相ステートマシンの再現。

    歩容そのもの (foot_target/leg_ik) は sim_gait.py 側にあり、ここでは
    「動いている間は位相を進める」「停止指令が来たら次の全脚接地瞬間
    (1/4 位相境界) まで進めてから静止する」という gait.h 固有の呼び出し側
    状態遷移だけを再現する (gait.h L19-30 と 1:1 対応)。
    """

    def __init__(self, cycle_t: float = CYCLE_T, quarter: float = QUARTER):
        self.phase = 0.0
        self.holding = True  # 起動直後は静止 (gait.h と同じ初期値)
        self.cycle_t = cycle_t
        self.quarter = quarter

    def step(self, dt: float, vx: float, vy: float, wz: float) -> float:
        mag = min(1.0, math.hypot(vx, vy) + abs(wz))
        if mag > 0.05:
            self.phase = math.fmod(self.phase + dt / self.cycle_t, 1.0)
            self.holding = False
        elif not self.holding:
            q = math.ceil(self.phase / self.quarter + 1e-4) * self.quarter
            nxt = self.phase + dt / self.cycle_t
            if nxt >= q:
                self.phase = math.fmod(q, 1.0)
                self.holding = True
            else:
                self.phase = nxt
        return self.phase


def leg_joint_names(leg: str) -> tuple[str, str, str]:
    lo = leg.lower()
    return f"leg_{lo}_yaw", f"leg_{lo}_pitch", f"leg_{lo}_knee"


def compute_leg_targets(phase: float, vx: float, vy: float, wz: float,
                         last_good: dict, *, holding: bool = False,
                         body_h: float = BODY_H_DEFAULT) -> tuple[dict, dict]:
    """歩容/IKを再利用し、停止時のSWAY抑制とfirmwareの出力制約を反映。

    回復したIKフォールバックも記録する。二重失敗は直近値を保持するが、
    その実行は合格にならない。未初期化二重失敗時は仮の0角で続けるため、
    実機の起動経路を証明する用途には使わない。
    """
    targets = {}
    leg_angles_deg = {}
    for i, leg in enumerate(sg._LEGS):
        x, y, z = sg.foot_target(i, phase, vx, vy, wz, body_h, holding=holding)
        ang = sg.leg_ik(x, y, z)
        if ang is None:
            last_good['_ik_fallback_count'] = last_good.get('_ik_fallback_count', 0) + 1
            nx, ny = sg.neutral_xy(i)
            gx, gy = nx - sg.ORIGIN[i, 0], ny - sg.ORIGIN[i, 1]
            c, s = np.cos(-sg.MOUNT[i]), np.sin(-sg.MOUNT[i])
            ang = sg.leg_ik(gx * c - gy * s, gx * s + gy * c, -body_h)
        if ang is None:
            ang = last_good.get(leg, (0.0, 0.0, 0.0))
            last_good.setdefault("_ik_fail_count", 0)
            last_good["_ik_fail_count"] += 1
        else:
            last_good[leg] = ang
        yaw_d, pitch_d, knee_d = ang
        leg_angles_deg[leg] = (yaw_d, pitch_d, knee_d)
        jy, jp, jk = leg_joint_names(leg)
        targets[jy] = math.radians(yaw_d)
        targets[jp] = math.radians(pitch_d)
        targets[jk] = math.radians(knee_d)
    # gait.h の単脚 / ペア安全制約も適用する (通常歩容で非発火でも省略しない)。
    clamped = clamp_leg_angles(np.array([leg_angles_deg[n] for n in sg._LEGS]))
    for leg, angles in zip(sg._LEGS, clamped):
        leg_angles_deg[leg] = tuple(angles)
        if leg in last_good:
            last_good[leg] = tuple(angles)
        for jn, val in zip(leg_joint_names(leg), angles):
            targets[jn] = math.radians(val)
    return targets, leg_angles_deg


def clamp_leg_angles(angles: np.ndarray) -> np.ndarray:
    """gait.h の角度制約。native C++ との連続時系列比較で検証する。"""
    out = np.array(angles, dtype=float, copy=True)
    out[:, 0] = np.clip(out[:, 0], -sg.LIM_YAW, sg.LIM_YAW)
    out[:, 1] = np.clip(out[:, 1], *sg.LIM_PITCH)
    out[:, 2] = np.clip(out[:, 2], -sg.LIM_KNEE, sg.LIM_KNEE)
    for leg in range(4):
        if out[leg, 0] * sg.YAW_IN_SIGN[leg] > sg.LIM_YAW_IN:
            out[leg, 0] = sg.LIM_YAW_IN * sg.YAW_IN_SIGN[leg]
        if out[leg, 0] * sg.YAW_POD_SIGN[leg] > sg.LIM_YAW_POD:
            out[leg, 0] = sg.LIM_YAW_POD * sg.YAW_POD_SIGN[leg]
    for a, b in ((0, 3), (1, 2)):
        ia, ib = out[a, 0] * sg.YAW_IN_SIGN[a], out[b, 0] * sg.YAW_IN_SIGN[b]
        if ia > 0 and ib > 0 and ia + ib > sg.LIM_YAW_IN_SUM:
            k = sg.LIM_YAW_IN_SUM / (ia + ib)
            out[a, 0], out[b, 0] = ia * k * sg.YAW_IN_SIGN[a], ib * k * sg.YAW_IN_SIGN[b]
    return out


class LegOutputDriver:
    """firmware の脚出力スルー段 + 現在角側の安全制約。"""
    def __init__(self, initial=None):
        self.current = np.zeros((4, 3)) if initial is None else np.array(initial, dtype=float)

    def step(self, targets: dict, dt: float) -> tuple[dict, dict]:
        desired = np.array([[math.degrees(targets[n]) for n in leg_joint_names(leg)]
                            for leg in sg._LEGS])
        self.current += np.clip(desired - self.current, -LEG_SLEW_DPS * dt, LEG_SLEW_DPS * dt)
        self.current = clamp_leg_angles(self.current)
        return ({n: math.radians(v) for leg, row in zip(sg._LEGS, self.current)
                 for n, v in zip(leg_joint_names(leg), row)},
                {leg: tuple(row) for leg, row in zip(sg._LEGS, self.current)})


ARM_JOINTS = ["arm_r_yaw", "arm_r_pitch", "arm_r_elbow",
              "arm_l_yaw", "arm_l_pitch", "arm_l_elbow"]
EYE_JOINTS = ["eye_r_roll", "eye_l_roll"]
ALL_LEG_JOINTS = [n for leg in sg._LEGS for n in leg_joint_names(leg)]
ALL_JOINTS = ALL_LEG_JOINTS + ARM_JOINTS + EYE_JOINTS


def arm_targets_rad(leg_angles_deg: dict | None = None) -> tuple[dict, tuple[bool, bool]]:
    """READY固定に前脚×腕の退避判定を追加する近似。

    引数は目標角と脚のスルー後角のうち危険方向が大きい角。
    腕スイング/波動作/スルー/地面・折畳み・相互ガードは未再現。
    """
    yaw, pitch, elbow = (math.radians(v) for v in ARM_POSE_READY_DEG)
    yaw_retreat = math.radians(-ARM_YAW_LIM_DEG)
    r_yaw, l_yaw = yaw, yaw
    r_gated = l_gated = False
    if leg_angles_deg is not None:
        fr_yaw_deg = leg_angles_deg["FR"][0]
        fl_yaw_deg = leg_angles_deg["FL"][0]
        r_gated = fr_yaw_deg * ARM_LEG_YAW_SIGN[0] > ARM_LEG_YAW_GATE_DEG
        l_gated = fl_yaw_deg * ARM_LEG_YAW_SIGN[1] > ARM_LEG_YAW_GATE_DEG
        if r_gated:
            r_yaw = yaw_retreat
        if l_gated:
            l_yaw = yaw_retreat
    targets = {
        "arm_r_yaw": r_yaw, "arm_r_pitch": pitch, "arm_r_elbow": elbow,
        "arm_l_yaw": l_yaw, "arm_l_pitch": pitch, "arm_l_elbow": elbow,
    }
    return targets, (r_gated, l_gated)


def build_model(friction_lateral: float, kp: dict, kv: dict,
                 offwidth: int = 1280, offheight: int = 720, *,
                 timestep: float = 0.002, effort_scale: float = 1.0,
                 mass_scale: float = 1.0, self_collision: bool = False,
                 include_parent_collision: bool = False,
                 slope_deg: float = 0.0, step_height_mm: float = 0.0,
                 step_front_y: float = 0.25, contact_model: str = 'linked-hulls',
                 hard_friction: float = .3, include_servo_collision: bool = False,
                 foot_candidate_dir=None) -> tuple[mujoco.MjModel, dict]:
    """URDF を MjSpec で読み込み、(1) base_link への free joint 追加,
    (2) 地面プレート追加 (チェッカーテクスチャ + シャドウ用ライト。
    2026-07-31 検証指摘 (3) 対応 — 下記参照), (3) 自己衝突 OFF (既定通り —
    理由は下記), (4) 各関節へ PD 位置アクチュエータ (kp/kv, forcerange は
    URDF の <limit effort=...> をそのまま MuJoCo が actfrcrange として保持
    した値を使う) を追加してコンパイルする。

    自己衝突は既定 OFF のまま: この設定は実機成立の証明ではない。
    第2次監査では実停止立位にも複数の実メッシュ交差を確認した。
    URDFの簡略凸包には偽接触もあり、実形状Booleanと分けて評価する。
    既定の地面接触のみの比較ではcontype/conaffinityを分離する
    (ロボット内部 geom 同士: contype=2/conaffinity=1、地面:
    既定 contype=1/conaffinity=1 のまま → ロボット-地面のみ衝突)。

    MuJoCo の URDF インポータは既定で discardvisual=true (<visual> を捨てて
    <collision> の凸包25個だけを使う) — 動画の見た目を元の意匠 (色分けされた
    複数マテリアルの visual メッシュ) にするため、URDF に `<mujoco>` 拡張
    タグを注入して discardvisual=false を明示指定する (元ファイルは変更せず
    メモリ上の文字列だけ書き換えて MjSpec.from_string に渡す)。
    """
    urdf_text = URDF_PATH.read_text()
    inject = (f'<mujoco><compiler discardvisual="false" '
              f'meshdir="{URDF_PATH.parent}"/></mujoco>')
    urdf_text = urdf_text.replace('<robot name="tachikoma">',
                                   f'<robot name="tachikoma">\n  {inject}', 1)
    spec = mujoco.MjSpec.from_string(urdf_text)
    spec.option.timestep = timestep
    spec.option.gravity = [0, 0, -9.81]
    spec.option.integrator = mujoco.mjtIntegrator.mjINT_IMPLICITFAST
    if include_parent_collision:
        spec.option.disableflags |= int(mujoco.mjtDisableBit.mjDSBL_FILTERPARENT)
    # offscreen framebュファは既定 640x480 のため動画解像度に合わせて拡張
    spec.visual.global_.offwidth = max(offwidth, 640)
    spec.visual.global_.offheight = max(offheight, 480)

    base = spec.body("base_link")
    base.add_freejoint()

    # [2026-07-31 検証指摘 (3)(4) 対応] 地面をチェッカーテクスチャ付きに
    # する。従来は無圧縮PNGでも標準偏差0の完全単色で、接地判定/並進量が
    # 動画から一切目視できなかった (検証指摘、独立確認済み — model.ntex=0,
    # model.nmat=0, model.nlight=0 だった)。1マス = GROUND_GRID_SQUARE_M
    # (実測歩行フェーズ並進量 ~0.11-0.15m が1マス強で読み取れる密度)。
    # 色は従来の単色 rgba (0.55,0.58,0.62) を明暗2色に分割し、全体トーンは
    # 変えない。物理 (摩擦係数) には一切影響しない — 見た目のみの変更。
    n_sq = round(2 * PLANE_HALF_SIZE_M / GROUND_GRID_SQUARE_M)
    spec.add_texture(
        name="ground_tex", type=mujoco.mjtTexture.mjTEXTURE_2D,
        builtin=mujoco.mjtBuiltin.mjBUILTIN_CHECKER,
        rgb1=[0.66, 0.69, 0.73], rgb2=[0.42, 0.45, 0.50],
        width=300, height=300,
    )
    spec.add_material(
        # textures はロール別配列 (mjtTextureRole; index=1 が mjTEXROLE_RGB)
        name="ground_mat", textures=["", "ground_tex", "", "", "", "", "", "", "", ""],
        texrepeat=[n_sq, n_sq], texuniform=True, reflectance=0.05,
    )
    # シャドウ用の固定太陽光 (headlight はカメラ追従でシャドウを落とさない
    # ため、これが無いと接地判定用の影が一切出ない — 検証指摘 (3) の実測
    # 原因)。ロボットの並進量 (~0.15m) はシーン規模 (地面 10m四方) に比べ
    # 無視できるため、光源はワールド固定で問題ない
    spec.worldbody.add_light(
        name="sun", pos=[1.2, -1.2, 2.2], dir=[-0.45, 0.45, -1.0],
        type=mujoco.mjtLightType.mjLIGHT_DIRECTIONAL, castshadow=True,
        diffuse=[0.55, 0.55, 0.55], ambient=[0.15, 0.15, 0.15],
    )
    spec.visual.headlight.ambient = [0.15, 0.15, 0.15]
    spec.visual.headlight.diffuse = [0.25, 0.25, 0.25]

    # 地面 (平面, 摩擦は下記で明示設定。TPU 95A foot_pad の実測係数は
    # UNVERIFIED — lateralFriction ~1.0 を設計目安値として採用)
    spec.worldbody.add_geom(
        name="ground",
        type=mujoco.mjtGeom.mjGEOM_PLANE,
        size=[PLANE_HALF_SIZE_M, PLANE_HALF_SIZE_M, 0.1], pos=[0, 0, 0],
        friction=[friction_lateral, 0.005, 0.0001],
        material="ground_mat",
        group=2,  # ロボット衝突凸包 (group=0, レンダでは隠す) と区別する
        quat=[math.cos(math.radians(slope_deg) / 2), math.sin(math.radians(slope_deg) / 2), 0, 0],
    )
    if step_height_mm > 0:
        spec.worldbody.add_geom(name='step_obstacle', type=mujoco.mjtGeom.mjGEOM_BOX,
            size=[1.0, 1.0, step_height_mm / 2000.0],
            pos=[0, step_front_y + 1.0, step_height_mm / 2000.0],
            friction=[friction_lateral, .005, .0001], group=2, rgba=[.4, .4, .4, 1])

    part_metadata = {}
    if foot_candidate_dir and contact_model=='linked-hulls':
        raise ValueError('足候補は材料別接触で評価してください')
    if contact_model != 'linked-hulls':
        from sim_collision import convex_parts
        for body in spec.bodies:
            if body.name != 'world':
                for geom in list(body.geoms):
                    if geom.group != 1:
                        spec.delete(geom)
        for pi, (link, name, material, hulls) in enumerate(convex_parts(contact_model, include_servos=include_servo_collision,foot_candidate_dir=foot_candidate_dir)):
            body = spec.body(link)
            for hi, hull in enumerate(hulls):
                unique = f'part_{pi}_{hi}'
                spec.add_mesh(name=unique, uservert=(hull.vertices * .001).ravel(), userface=hull.faces.ravel())
                body.add_geom(name=unique, type=mujoco.mjtGeom.mjGEOM_MESH, meshname=unique,
                    group=0, contype=2, conaffinity=3 if self_collision else 1,
                    priority=1, mass=0, density=0,
                    friction=[friction_lateral if material=='TPU' else hard_friction, .005, .0001],
                    solref=[.02 if material=='TPU' else .004, 1])
                part_metadata[unique] = {'link': link, 'part': name, 'material': material}
    for body in spec.bodies:
        if body.name in ("world",):
            continue
        for g in body.geoms:
            if g.group == 1:
                continue  # 見た目のみの visual mesh (discardvisual=false で追加) — 非衝突のまま
            g.contype = 2
            g.conaffinity = 3 if self_collision else 1
            if g.name not in part_metadata:
                g.friction = [friction_lateral, 0.005, 0.0001]

    def add_pd(joint_name: str, kp_v: float, kv_v: float):
        j = spec.joint(joint_name)
        j.actfrcrange = np.array(j.actfrcrange) * effort_scale
        gainprm = [0.0] * 10
        gainprm[0] = kp_v
        biasprm = [0.0] * 10
        biasprm[1] = -kp_v
        biasprm[2] = -kv_v
        spec.add_actuator(
            name=f"act_{joint_name}", trntype=mujoco.mjtTrn.mjTRN_JOINT,
            target=joint_name,
            gaintype=mujoco.mjtGain.mjGAIN_FIXED, gainprm=gainprm,
            biastype=mujoco.mjtBias.mjBIAS_AFFINE, biasprm=biasprm,
            forcerange=j.actfrcrange, forcelimited=True,
            ctrlrange=j.range, ctrllimited=True,
        )

    for jn in ALL_LEG_JOINTS:
        add_pd(jn, kp["leg"], kv["leg"])
    for jn in ARM_JOINTS:
        add_pd(jn, kp["arm"], kv["arm"])
    for jn in EYE_JOINTS:
        add_pd(jn, kp["eye"], kv["eye"])

    if foot_candidate_dir:
        from sim_collision import parts_with_pad
        import export_urdf as E
        # 候補の追加質量を無視しない。既定URDFは保存したまま、足リンクの
        # 質量/COM/慣性を同じ印刷密度法で算出し直す。
        for link,items in parts_with_pad(False,foot_candidate_dir).items():
            if not link.endswith('_tibia'):continue
            mass,com,inertia=E.combine_mass_items([E.part_mass_item(mesh,name) for mesh,_,name in items])
            principal,axes=np.linalg.eigh(inertia)
            if np.linalg.det(axes)<0:axes[:,0]*=-1
            quat=np.empty(4);mujoco.mju_mat2Quat(quat,axes.ravel())
            body=spec.body(link)
            body.mass=mass;body.ipos=com;body.inertia=principal;body.iquat=quat
            body.fullinertia=[np.nan,0.,0.,0.,0.,0.]
            body.explicitinertial=True
    # 既存の未同定パラメータ。実サーボの計測値ではなく、感度試験を別に行う。
    # 自由ベースに架空の粘性や補正慣性を加えず、サーボ軸だけに適用する。
    for jn in ALL_JOINTS:
        joint = spec.joint(jn)
        joint.armature = 0.001
        joint.damping = [0.02, 0., 0.]
    for body in spec.bodies:
        if body.mass <= 0:
            continue
        body.mass *= mass_scale
        if np.isfinite(body.fullinertia).all():
            body.fullinertia = np.asarray(body.fullinertia) * mass_scale
        else:
            body.inertia = np.asarray(body.inertia) * mass_scale
    # 衝突探索木と慣性座標、body_subtreemassとdof_invweight0などの派生値を
    # 全て同じパラメータから構築する。コンパイル後には質量・慣性を変えない。
    model = spec.compile()

    jid = {n: mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, n) for n in ALL_JOINTS}
    aid = {n: mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, f"act_{n}") for n in ALL_JOINTS}
    limits = {j.attrib['name']: float(j.find('limit').attrib['velocity'])
              for j in ET.fromstring(urdf_text).findall('joint') if j.find('limit') is not None}
    return model, {"jid": jid, "aid": aid, "velocity_limits": limits, 'part_metadata': part_metadata}


def quat_to_rpy(q: np.ndarray) -> tuple[float, float, float]:
    """MuJoCo quat [w,x,y,z] -> roll,pitch,yaw (rad)."""
    w, x, y, z = q
    sinr_cosp = 2 * (w * x + y * z)
    cosr_cosp = 1 - 2 * (x * x + y * y)
    roll = math.atan2(sinr_cosp, cosr_cosp)
    sinp = 2 * (w * y - z * x)
    sinp = max(-1.0, min(1.0, sinp))
    pitch = math.asin(sinp)
    siny_cosp = 2 * (w * z + x * y)
    cosy_cosp = 1 - 2 * (y * y + z * z)
    yaw = math.atan2(siny_cosp, cosy_cosp)
    return roll, pitch, yaw


_HUD_FONT = None


def _hud_font() -> ImageFont.ImageFont:
    global _HUD_FONT
    if _HUD_FONT is None:
        try:
            _HUD_FONT = ImageFont.load_default(size=20)
        except TypeError:  # 古い Pillow フォールバック (size 引数非対応)
            _HUD_FONT = ImageFont.load_default()
    return _HUD_FONT


def draw_hud(img: np.ndarray, t: float, phase: str, vx: float, wz: float,
             dxy_m: float) -> np.ndarray:
    """[2026-07-31 検証指摘 (4) 対応] 「その場足踏みに見える」問題への追加
    緩和策。地面グリッド (build_model 側, 検証指摘 (3)(4) 共通原因) だけでは
    足りない場合の保険として、経過時間・フェーズ・並進量を毎フレーム数値で
    焼き込む。カメラは意図的にロボット追従のまま変更していない — 実測並進量
    (0.11-0.15m) はシーン規模 (地面 10m四方) に対して小さく、追従を止めて
    ワールド固定にすると歩容そのもの (脚の可動) が豆粒化して見えなくなり、
    検証性が今より悪化する。グリッド上を模様が流れる「トレッドミル効果」+
    このHUDの数値表示の組合せで、追従カメラを維持したまま並進を確認できる
    構成を採った (根拠ありで見送った代替案: ワールド固定カメラ)。

    純粋なレンダリング後処理であり、物理積分にもメトリクス JSON の数値にも
    一切影響しない (img はコピー後に描画するため呼び出し元の元データも不変)。
    """
    im = Image.fromarray(img).convert("RGBA")
    overlay = Image.new("RGBA", im.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = _hud_font()
    text = (f"t={t:5.2f}s  phase={phase:<6s} vy={vx:+.2f} wz={wz:+.2f}  "
            f"d(xy)={dxy_m * 1000:6.1f}mm")
    bbox = draw.textbbox((8, 6), text, font=font)
    draw.rectangle([(0, 0), (im.width, bbox[3] + 8)], fill=(0, 0, 0, 165))
    draw.text((8, 6), text, fill=(255, 255, 255, 255), font=font)
    im = Image.alpha_composite(im, overlay).convert("RGB")
    return np.array(im)


def input_fingerprints() -> dict:
    """実行した制御コード・URDF・参照メッシュを内容ハッシュで固定する。"""
    files = {Path(__file__), URDF_PATH, ROOT / 'tools/sim_gait.py',
             ROOT / 'tools/export_urdf.py', ROOT / 'hardware/src/config.py'}
    files.update(ROOT.glob('tools/sim_*.py'))
    files.update(ROOT.glob('tools/tests/simulation_*.cpp'))
    files.update((ROOT / 'tools/tests/firmware_stubs').rglob('*.h'))
    files.add(ROOT / 'tools/make_visuals.py')
    files.add(ROOT / 'tools/kit_assembly.py')
    files.update(ROOT.glob('tools/data/*.json'))
    files.update(ROOT.glob('hardware/stl/*.stl'))
    files.update(ROOT.glob('hardware/src/*.py'))
    files.update(ROOT.glob('model/*.stl'))
    files.update((ROOT / 'firmware/src').glob('*.h'))
    files.add(ROOT / 'firmware/src/main.cpp')
    for mesh in ET.parse(URDF_PATH).findall('.//mesh'):
        files.add((URDF_PATH.parent / mesh.attrib['filename']).resolve())
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(files)}


def speed_torque_ranges(stall_ranges: np.ndarray, velocity: np.ndarray,
                        no_load_speed: np.ndarray) -> np.ndarray:
    """モータの線形トルク速度近似。逆方向の制動は停止トルク以内に制限。

    URDF velocity は MuJoCo が物理上限として適用しないため明示的に使う。
    実サーボの制御器/実測曲線は UNVERIFIED。外力で速度上限を超えることは
    あり得るので、速度そのものを強制変更する処理は行わない。
    """
    ratio = velocity / no_load_speed
    return np.column_stack((stall_ranges[:, 0] * np.clip(1 + ratio, 0, 1),
                            stall_ranges[:, 1] * np.clip(1 - ratio, 0, 1)))


def walk_displacement(times, positions, walk_start, walk_end, vx, vy) -> dict:
    """歩行区間内の、重複しない1周期平均間の符号付き変位。

    ノルムを前進距離と呼ばず、+Y前 / +X右を分ける。平均区間の中心間時間も
    記録する。短い歩行や歩行なしを停止/旋回区間の変位で補完しない。
    """
    result = {'available': False, 'reason': '歩行区間が2周期未満、または指令がゼロ'}
    if walk_end - walk_start < 2 * CYCLE_T - 1e-9 or math.hypot(vx, vy) == 0:
        return result
    t, xyz = np.asarray(times), np.asarray(positions)
    a = (t >= walk_start - 1e-9) & (t < walk_start + CYCLE_T - 1e-9)
    b = (t >= walk_end - CYCLE_T - 1e-9) & (t < walk_end - 1e-9)
    if not a.any() or not b.any() or np.any(a & b):
        return result
    delta = xyz[b, :2].mean(axis=0) - xyz[a, :2].mean(axis=0)
    elapsed = float(t[b].mean() - t[a].mean())
    commanded = float(np.dot(delta, [vx, vy]) / math.hypot(vx, vy))
    return {'available': True, 'axis_frame': 'initial world +X right, +Y forward',
            'right_m': float(delta[0]), 'forward_m': float(delta[1]),
            'xy_norm_m': float(np.linalg.norm(delta)), 'commanded_direction_m': commanded,
            'averaging_window_s': CYCLE_T, 'effective_elapsed_s': elapsed,
            'commanded_direction_speed_m_s': commanded / elapsed}


def main() -> int:
    ap = argparse.ArgumentParser(description='MuJoCo 条件付き歩行検証。実機動作は UNVERIFIED。')
    for name, default in [('settle', 1.0), ('walk', 8.0), ('turn', 3.0), ('stop', 1.0)]:
        ap.add_argument('--' + name, type=float, default=default, help='各区間の秒数')
    ap.add_argument('--vx', type=float, default=0.0, help='右方向の指令 (-1..1)')
    ap.add_argument('--vy', type=float, default=1.0, help='前方向の指令 (-1..1)')
    ap.add_argument('--wz', type=float, default=1.0, help='旋回の指令 (-1..1)')
    ap.add_argument('--friction', type=float, default=1.0, help='全接触形状の摩擦係数 (実測前)')
    ap.add_argument('--effort-scale', type=float, default=1.0, help='URDFトルク上限の倍率')
    ap.add_argument('--mass-scale', type=float, default=1.0, help='URDF質量・慣性の倍率')
    ap.add_argument('--torque-model', choices=['linear-speed', 'constant'], default='linear-speed',
                    help='linear-speed=未実測の線形トルク速度近似、constant=従来の楽観上限')
    ap.add_argument('--timestep', type=float, default=.002, help='物理刻み秒。50Hz制御周期の整数分割')
    for group, kp, kv in [('leg', 24., .40), ('arm', .8, .03), ('eye', .05, .005)]:
        ap.add_argument('--kp-' + group, type=float, default=kp, help='実測前のPD位置ゲイン')
        ap.add_argument('--kv-' + group, type=float, default=kv, help='実測前のPD速度ゲイン')
    ap.add_argument('--min-walk-speed', type=float, default=.005,
                    help='計算上の合否基準: 指令方向の平均最低速度m/s (既定5mm/s)')
    ap.add_argument('--max-saturation-fraction', type=float, default=.05,
                    help='計算上の合否基準: 各脚軸で許容するトルク飽和時間率 (既定5%%)')
    ap.add_argument('--min-turn-deg', type=float, default=1.0,
                    help='計算上の合否基準: 指令方向への最低旋回角度')
    ap.add_argument('--fps', type=int, default=30)
    ap.add_argument('--width', type=int, default=1280)
    ap.add_argument('--height', type=int, default=720)
    ap.add_argument('--seed', type=int, default=0, help='互換性維持用。乱数不使用のため効果なし')
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    output_dir = ROOT / 'docs' / 'simulation_runs' / stamp
    ap.add_argument('--out', default=str(output_dir / 'walk.mp4'))
    ap.add_argument('--metrics', default=str(output_dir / 'metrics.json'))
    ap.add_argument('--novideo', action='store_true')
    args = ap.parse_args()
    numeric = [v for v in vars(args).values() if isinstance(v, (int, float))]
    if not all(math.isfinite(v) for v in numeric):
        ap.error('数値引数は有限値で指定してください')
    durations = [args.settle, args.walk, args.turn, args.stop]
    if min(durations) < 0 or sum(durations) <= 0:
        ap.error('区間秒数は0以上、合計は0より大きくしてください')
    if any(abs(v) > 1 for v in (args.vx, args.vy, args.wz)):
        ap.error('速度指令は -1..1 です')
    if args.timestep <= 0 or args.friction < 0 or min(args.mass_scale, args.effort_scale) <= 0:
        ap.error('刻み、質量倍率、トルク倍率は正、摩擦係数は0以上です')
    control_steps = 1 / (SERVO_HZ * args.timestep)
    if not math.isclose(control_steps, round(control_steps), abs_tol=1e-8) or control_steps < 1:
        ap.error('--timestep は50Hz制御周期の整数分割にしてください (例: .002, .001)')
    if round(sum(durations) / args.timestep) < 1:
        ap.error('合計秒数は少なくとも物理1ステップが必要です')
    if args.fps <= 0 or min(args.width, args.height) <= 0:
        ap.error('fps/画像寸法は正です')
    if (not 0 <= args.max_saturation_fraction <= 1 or args.min_walk_speed < 0
            or args.min_turn_deg < 0 or any(v < 0 for k, v in vars(args).items() if k.startswith(('kp_', 'kv_')))):
        ap.error('ゲイン/速度/旋回基準は0以上、飽和時間率は0..1です')

    fingerprints = input_fingerprints()
    kp = {g: getattr(args, 'kp_' + g) for g in ('leg', 'arm', 'eye')}
    kv = {g: getattr(args, 'kv_' + g) for g in ('leg', 'arm', 'eye')}
    model, idx = build_model(args.friction, kp, kv, args.width, args.height,
                            timestep=args.timestep, effort_scale=args.effort_scale,
                            mass_scale=args.mass_scale)
    data = mujoco.MjData(model)
    mujoco.mj_setConst(model, data)
    dt = model.opt.timestep
    control_stride = round(control_steps)
    last_good = {}
    stand, stand_deg = compute_leg_targets(0., 0., 0., 0., last_good, holding=True)
    stand.update(arm_targets_rad(stand_deg)[0])
    stand.update({n: 0. for n in EYE_JOINTS})
    base_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, 'base_link')
    root_adr = model.jnt_qposadr[model.body_jntadr[base_id]]
    data.qpos[root_adr:root_adr + 7] = [0, 0, BODY_H_M + .002, 1, 0, 0, 0]
    for name, value in stand.items():
        data.qpos[model.jnt_qposadr[idx['jid'][name]]] = value
        data.ctrl[idx['aid'][name]] = value
    mujoco.mj_forward(model, data)
    driver = PhaseDriver()
    output = LegOutputDriver([stand_deg[n] for n in sg._LEGS])
    plane_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, 'ground')
    aids = np.array([idx['aid'][n] for n in ALL_JOINTS])
    qadr = np.array([model.jnt_qposadr[idx['jid'][n]] for n in ALL_JOINTS])
    dadr = np.array([model.jnt_dofadr[idx['jid'][n]] for n in ALL_JOINTS])
    stall_ranges = model.actuator_forcerange[aids].copy()
    speeds = np.array([idx['velocity_limits'][n] for n in ALL_JOINTS])
    torque_peak = np.zeros(len(aids)); demand_peak = torque_peak.copy(); speed_peak = torque_peak.copy()
    error_peak = torque_peak.copy(); saturation_count = np.zeros(len(aids), dtype=int)
    saturation_run = np.zeros(len(aids), dtype=int); saturation_longest = saturation_run.copy()
    stall_saturation_count = saturation_run.copy()
    sample_stride = max(1, round(.1 / dt))
    metrics = {k: [] for k in ('time', 'phase', 'base_pos', 'roll_deg', 'pitch_deg', 'yaw_deg',
                               'ground_contact_points', 'foot_contact', 'contact_bodies',
                               'torque_nm', 'joint_velocity_rad_s', 'tracking_error_deg')}
    contact_steps_by_body = {}; nonfoot_contact_steps = 0
    arm_gate_count = {'r': 0, 'l': 0}; phase_steps = {k: 0 for k in ('SETTLE', 'WALK', 'TURN', 'STOP')}
    gate_by_phase = {k: {'r': 0, 'l': 0} for k in phase_steps}
    fell_time = None; max_roll = max_pitch = 0.; nonfinite = False
    turn_yaw_start = turn_yaw_end = None
    turn_yaw_total = 0.
    ts = np.cumsum(durations); total_t = float(ts[-1]); n_steps = round(total_t / dt)
    gate = (False, False)
    renderer = ffmpeg_proc = None; frames = 0; next_frame_t = 0.
    if not args.novideo:
        renderer = mujoco.Renderer(model, height=args.height, width=args.width)
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        ffmpeg_proc = subprocess.Popen(['ffmpeg', '-y', '-f', 'rawvideo', '-pixel_format', 'rgb24',
            '-video_size', f'{args.width}x{args.height}', '-framerate', str(args.fps), '-i', '-',
            '-an', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '18', '-preset', 'medium', args.out],
            stdin=subprocess.PIPE)
    scene_opt = mujoco.MjvOption(); scene_opt.geomgroup[0] = 0
    scene_opt.geomgroup[1] = scene_opt.geomgroup[2] = 1
    try:
        for step in range(n_steps):
            t = step * dt
            pi = min(int(np.searchsorted(ts, t + 1e-10, side='right')), 3)
            name = ('SETTLE', 'WALK', 'TURN', 'STOP')[pi]
            vx, vy, wz = ((args.vx, args.vy, 0.) if pi == 1 else
                          (0., 0., args.wz) if pi == 2 else (0., 0., 0.))
            if step % control_stride == 0:
                phase = driver.step(1 / SERVO_HZ, vx, vy, wz)
                target, target_deg = compute_leg_targets(phase, vx, vy, wz, last_good, holding=driver.holding)
                command, current_deg = output.step(target, 1 / SERVO_HZ)
                # 腕の退避判定は目標とスルー後の危険方向の大きい方を使う。
                # READY固定という腕シミュレーションの限界は残る。
                guarded = dict(current_deg)
                for leg, sign in zip(('FR', 'FL'), ARM_LEG_YAW_SIGN):
                    yaw = max(target_deg[leg][0] * sign, current_deg[leg][0] * sign) * sign
                    guarded[leg] = (yaw, *current_deg[leg][1:])
                arm, gate = arm_targets_rad(guarded)
                command.update(arm)
                for jn, val in command.items():
                    data.ctrl[idx['aid'][jn]] = val
            phase_steps[name] += 1
            for side, active in zip(('r', 'l'), gate):
                arm_gate_count[side] += int(active); gate_by_phase[name][side] += int(active)
            velocity_before = data.qvel[dadr].copy()
            force_ranges = (speed_torque_ranges(stall_ranges, velocity_before, speeds)
                            if args.torque_model == 'linear-speed' else stall_ranges)
            model.actuator_forcerange[aids] = force_ranges
            ctrl = np.clip(data.ctrl[aids], model.actuator_ctrlrange[aids, 0], model.actuator_ctrlrange[aids, 1])
            error = ctrl - data.qpos[qadr]
            demand = model.actuator_gainprm[aids, 0] * error + model.actuator_biasprm[aids, 2] * velocity_before
            saturated = (demand < force_ranges[:, 0] - 1e-8) | (demand > force_ranges[:, 1] + 1e-8)
            stall_sat = (demand < stall_ranges[:, 0] - 1e-8) | (demand > stall_ranges[:, 1] + 1e-8)
            saturation_count += saturated; stall_saturation_count += stall_sat
            saturation_run = np.where(saturated, saturation_run + 1, 0)
            saturation_longest = np.maximum(saturation_longest, saturation_run)
            mujoco.mj_step(model, data)
            actual_force = data.actuator_force[aids].copy()
            if not np.isfinite(data.qpos).all() or not np.isfinite(data.qvel).all():
                nonfinite = True
                break
            # MuJoCo は発散時に状態/時刻を自動リセットする場合がある。
            # その後の時系列を通常の13秒試験として続行しない。
            if any(data.warning[int(w)].number for w in (
                    mujoco.mjtWarning.mjWARN_BADQPOS, mujoco.mjtWarning.mjWARN_BADQVEL,
                    mujoco.mjtWarning.mjWARN_BADQACC, mujoco.mjtWarning.mjWARN_BADCTRL)):
                break
            torque_peak = np.maximum(torque_peak, abs(actual_force)); demand_peak = np.maximum(demand_peak, abs(demand))
            speed_peak = np.maximum(speed_peak, abs(data.qvel[dadr])); error_peak = np.maximum(error_peak, abs(error))
            mujoco.mj_forward(model, data)  # 積分後の姿勢と接触を同一時刻へそろえる
            pos = data.xpos[base_id].copy(); roll, pitch, yaw = quat_to_rpy(data.xquat[base_id])
            rd, pd, yd = map(math.degrees, (roll, pitch, yaw))
            max_roll = max(max_roll, abs(rd)); max_pitch = max(max_pitch, abs(pd))
            if fell_time is None and (abs(rd) > 30 or abs(pd) > 30 or pos[2] < BODY_H_M * .5):
                fell_time = float(data.time)
            if pi == 2:
                if turn_yaw_start is None:
                    turn_yaw_start = yd
                else:
                    turn_yaw_total += (yd - turn_yaw_end + 180) % 360 - 180
                turn_yaw_end = yd
            contacts = set(); n_contacts = 0
            for ci in range(data.ncon):
                c = data.contact[ci]
                if plane_id not in (c.geom1, c.geom2) or c.efc_address < 0:
                    continue
                g = c.geom2 if c.geom1 == plane_id else c.geom1
                contacts.add(mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, model.geom_bodyid[g]))
                n_contacts += 1
            for body in contacts: contact_steps_by_body[body] = contact_steps_by_body.get(body, 0) + 1
            feet = {b for b in contacts if b.startswith('leg_') and b.endswith('_tibia')}
            if contacts - feet: nonfoot_contact_steps += 1
            if step % sample_stride == 0 or step == n_steps - 1:
                row = [float(data.time), name, pos.tolist(), rd, pd, yd, n_contacts, len(feet),
                       sorted(contacts), actual_force.tolist(), data.qvel[dadr].tolist(), np.degrees(error).tolist()]
                for key, value in zip(metrics, row): metrics[key].append(value)
            if renderer is not None and t + 1e-9 >= next_frame_t:
                cam = mujoco.MjvCamera(); cam.lookat = [*pos[:2], pos[2] + .02]
                cam.distance = .78; cam.azimuth = 205.; cam.elevation = -18.
                renderer.update_scene(data, camera=cam, scene_option=scene_opt)
                img = draw_hud(renderer.render(), t, name, vy, wz, float(np.linalg.norm(pos[:2])))
                ffmpeg_proc.stdin.write(img.tobytes()); frames += 1; next_frame_t = frames / args.fps
    finally:
        if ffmpeg_proc is not None:
            ffmpeg_proc.stdin.close(); ffmpeg_proc.wait()
        if renderer is not None: renderer.close()

    completed = step + 1
    walk = walk_displacement(metrics['time'], metrics['base_pos'], ts[0], ts[1], args.vx, args.vy)
    turn_deg = None if turn_yaw_start is None else turn_yaw_total
    torque = {n: {'peak_applied_nm': float(torque_peak[i]), 'peak_unlimited_demand_nm': float(demand_peak[i]),
                 'saturation_fraction': float(saturation_count[i] / completed),
                 'stall_limit_saturation_fraction': float(stall_saturation_count[i] / completed),
                 'longest_saturation_s': float(saturation_longest[i] * dt),
                 'peak_velocity_rad_s': float(speed_peak[i]), 'urdf_no_load_velocity_rad_s': float(speeds[i]),
                 'peak_tracking_error_deg': float(np.degrees(error_peak[i]))}
              for i, n in enumerate(ALL_JOINTS)}
    max_leg_sat = max(torque[n]['saturation_fraction'] for n in ALL_LEG_JOINTS)
    checks = {'completed': completed == n_steps and not nonfinite and math.isclose(data.time, n_steps * dt),
              'finite_state': not nonfinite, 'no_fall': fell_time is None,
              'no_ik_fallback': not last_good.get('_ik_fallback_count', 0) and not last_good.get('_ik_fail_count', 0),
              'no_nonfoot_ground_contact': nonfoot_contact_steps == 0,
              'leg_torque_saturation': max_leg_sat <= args.max_saturation_fraction,
              'walk_progress': (walk.get('commanded_direction_speed_m_s', -math.inf) >= args.min_walk_speed
                                if args.walk > 0 and math.hypot(args.vx, args.vy) > 0 else None),
              'turn_progress': ((turn_deg or 0.) * math.copysign(1., args.wz) >= args.min_turn_deg
                                if args.turn > 0 and args.wz else None),
              'video_encoded': ffmpeg_proc.returncode == 0 if ffmpeg_proc is not None else None,
              'inputs_unchanged_during_run': fingerprints == input_fingerprints()}
    warning_counts = {mujoco.mjtWarning(i).name: int(w.number)
                      for i, w in enumerate(data.warning) if w.number}
    checks['no_physics_warning'] = not warning_counts
    result = {'schema_version': 2, 'created_at_utc': datetime.now(timezone.utc).isoformat(),
              'engine': 'mujoco', 'mujoco_version': mujoco.__version__, 'python_version': platform.python_version(),
              'platform': platform.platform(), 'input_sha256': fingerprints, 'arguments': vars(args),
              'timestep_s': dt, 'controller_hz': SERVO_HZ, 'total_sim_time_s': float(data.time),
              'requested_sim_time_s': total_t, 'schedule_s': dict(zip(('settle', 'walk', 'turn', 'stop'), durations)),
              'commands': {'vx_right': args.vx, 'vy_forward': args.vy, 'wz_turn': args.wz},
              'gains': {'kp': kp, 'kv': kv}, 'mass_kg': float(model.body_mass.sum()),
              'initialization': 'prepared standing; power-on/enable/unknown horn angle not simulated',
              'free_base_damping': model.dof_damping[:6].tolist(), 'free_base_armature': model.dof_armature[:6].tolist(),
              'friction_lateral': args.friction, 'torque_model': args.torque_model,
              'unverified_assumptions': ['サーボ実測トルク速度曲線、PDゲイン、関節粘性/ロータ慣性',
                  '全接触形状に同一摩擦。tibia凸包はPLAトゥとTPUパッドを一体化し材質別荷重を識別できない',
                  '現形状はトゥ/硬い足本体がTPUより先に接地する。TPU支持・トゥ強度・実摩擦はこのPASSで証明できない (RV06)',
                  '樹脂/ホーンを剛体と仮定。変形、バックラッシュ、電源電圧降下、温度上昇は未計算',
                  '均質密度の推定質量/慣性。実重量/重心未測定',
                  '自己衝突無効。腕はREADY+退避判定のみ。腕スイング/ガード/スルー、目動作、起動動作は未再現'],
              'walk_displacement': walk, 'forward_distance_during_walk_phase_m': walk.get('forward_m'),
              'net_xy_distance_start_to_end_m': float(np.linalg.norm(data.qpos[root_adr:root_adr + 2])),
              'turn_angle_deg': turn_deg, 'max_abs_roll_deg': max_roll, 'max_abs_pitch_deg': max_pitch,
              'fell': fell_time is not None, 'fell_time_s': fell_time,
              'ik_fallback_count': last_good.get('_ik_fallback_count', 0),
              'ik_double_failure_count': last_good.get('_ik_fail_count', 0),
              'joint_order': ALL_JOINTS, 'actuators': torque,
              'physics_warning_counts': warning_counts,
              'contacts': {'steps_by_body': contact_steps_by_body, 'nonfoot_ground_contact_steps': nonfoot_contact_steps,
                           'foot_contact_definition': '地面と接触する異なるtibiaリンク数。接触点数やTPUパッド数ではない'},
              'arm_leg_yaw_gate': {'fire_steps': arm_gate_count, 'phase_steps': phase_steps,
                                   'fire_steps_by_phase': gate_by_phase},
              'simulation_acceptance': {'status': 'PASS' if all(v is not False for v in checks.values()) else 'FAIL',
                                        'checks': checks, 'scope': '指定した仮定・閾値・シーケンスだけの数値判定'},
              'physical_readiness': 'UNVERIFIED',
              'video_frames': frames, 'video_path': None if args.novideo else args.out, 'timeseries': metrics}
    Path(args.metrics).parent.mkdir(parents=True, exist_ok=True)
    Path(args.metrics).write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
    print(f"simulation_acceptance = {result['simulation_acceptance']['status']}  physical_readiness = UNVERIFIED")
    print(f"walk = {walk}")
    print(f"turn_deg = {turn_deg}  max_roll = {max_roll:.3f}  max_pitch = {max_pitch:.3f}  fell = {fell_time is not None}")
    print(f"max_leg_saturation_fraction = {max_leg_sat:.5f}  nonfoot_ground_contact_steps = {nonfoot_contact_steps}")
    print('failed_checks = ' + ', '.join(k for k, v in checks.items() if v is False))
    print(f'metrics saved -> {args.metrics}')
    return 0 if result['simulation_acceptance']['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
