#!/usr/bin/env python3
"""タチコマ歩行の物理シミュレーション (重力・接触・摩擦・トルク上限あり)。

物理エンジン: MuJoCo (mujoco>=3, pip wheel あり)。
[UNVERIFIED/フォールバック理由] 当初計画は PyBullet だったが、このマシンの
Xcode/clang (Apple clang 17.0.0, macOS SDK 26) では pybullet の pip ソース
ビルドが失敗する: Bullet3 に同梱された thirdparty zlib (examples/ThirdPartyLibs/
zlib/zutil.c) の古い K&R スタイル関数定義 `int ZEXPORT uncompress(dest,
destLen, source, sourceLen)` を、新しい macOS SDK の <stdio.h> と一緒に
clang がパースできず `error: expected identifier or '('` で停止する
(CFLAGS=-std=gnu99 を渡しても同じ箇所で失敗 — pybullet の setup.py が
独自の compile flags を使っておりでも通らない、SDK ヘッダ側の非互換)。
cp311-macosx arm64 向けの pip wheel も PyPI に存在しない (ソースビルドのみ)。
→ 指示どおり mujoco (公式 arm64 wheel あり, pip install 一発で成功) へ
フォールバック。歩容ロジック自体は変わらず tools/sim_gait.py を単純 import
して使うので、エンジン差し替えの影響はない。

歩容軌道 (足先ワールド/脚ローカル座標, IK) は一切ここで再実装しない —
tools/sim_gait.py の foot_target()/leg_ik() を毎ステップ呼ぶだけ (import 済み
firmware 忠実ロジック)。本ファイルが持つのは:
  (1) firmware/src/gait.h の「停止指令時は次の四半位相境界まで進めてから
      静止する」という位相駆動ステートマシンの薄い再現 (PhaseDriver) —
      これは sim_gait.py にはない、gait.h 固有の「呼び出し側の状態管理」
      なので、コアの IK/歩容数式とは別物として持つ (下記 PhaseDriver 参照)。
  (2) MuJoCo モデル構築・PD 位置制御・カメラ・動画書き出しの配線コード。
  (3) [2026-07-31 検証指摘対応で追加] firmware/src/arms.h の 前脚(FR/FL)×腕
      連成クランプ (config.h ARM_LEG_YAW_GATE_DEG) だけを薄く再現する
      (arm_targets_rad 参照)。腕は基本 READY 固定という既存の単純化方針は
      維持し、wave / 歩行スイング / スルーレート / 地面ガード / 折り畳み
      ガード / 相互接触クランプは対象外のまま (未実装, arms.h 参照)。この
      連成クランプだけは「腕がしばしば READY のままでよいか」という映像上
      の忠実性に直結するため追加した。

既知の限界 (2026-07-31 検証指摘, 意図的に未対応):
  - 上記 (3) 以外の arms.h ロジック (wave, 歩行スイング, ARM_SLEW_DPS スルー
    レート, 地面/折り畳み/相互接触ガード) は再現していない。腕は
    ARM_LEG_YAW_GATE_DEG クランプの発火有無以外は常に ARM_POSE_READY 固定。
  - --seed 引数は本シミュレーション (PD 位置制御 + MuJoCo 決定論的積分のみ、
    乱数不使用) では no-op (下記 CLI ヘルプ参照)。
"""
from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
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
                         last_good: dict) -> tuple[dict, dict]:
    """sim_gait.foot_target()/leg_ik() を呼ぶだけ。IK 到達不能時のフォール
    バックも firmware/src/gait.h L108-141 と同じ「中立姿勢へフォールバック
    →それも失敗なら直近成功角を保持」を踏襲 (sim_gait 自体は歩容全域で
    IK 失敗 0 と検証済みなので通常経路では発火しない — 安全網)。

    戻り値は (targets[joint_name]=rad, leg_angles_deg[leg]=(yaw,pitch,knee))
    の2つ。leg_angles_deg は前脚(FR/FL)×腕 連成クランプ (arm_targets_rad)
    の入力として再利用する — leg_ik を呼び直さず、この関数が既に計算した
    角度をそのまま渡す (二重計算を避ける)。
    """
    targets = {}
    leg_angles_deg = {}
    for i, leg in enumerate(sg._LEGS):
        x, y, z = sg.foot_target(i, phase, vx, vy, wz, sg.BODY_H)
        ang = sg.leg_ik(x, y, z)
        if ang is None:
            nx, ny, nz = sg.foot_target(i, 0.0, 0.0, 0.0, 0.0, sg.BODY_H)
            ang = sg.leg_ik(nx, ny, nz)
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
    return targets, leg_angles_deg


ARM_JOINTS = ["arm_r_yaw", "arm_r_pitch", "arm_r_elbow",
              "arm_l_yaw", "arm_l_pitch", "arm_l_elbow"]
EYE_JOINTS = ["eye_r_roll", "eye_l_roll"]
ALL_LEG_JOINTS = [n for leg in sg._LEGS for n in leg_joint_names(leg)]
ALL_JOINTS = ALL_LEG_JOINTS + ARM_JOINTS + EYE_JOINTS


def arm_targets_rad(leg_angles_deg: dict | None = None) -> tuple[dict, tuple[bool, bool]]:
    """firmware arms.h の READY 固定姿勢 + 前脚(FR/FL)×腕 連成クランプ
    (arms.h L147/195, config.h ARM_LEG_YAW_GATE_DEG) を再現する。

    [2026-07-31 検証指摘 (1) 対応] 従来は腕を無条件に ARM_POSE_READY へ固定
    していたが、実測 (独立再現) で通常歩行フェーズの88%・旋回フェーズの
    74%のステップで同側前脚ヨーがゲート (20°) を超過しており (FR yaw 最大
    36.8°)、firmware であれば腕がその間ずっと -ARM_YAW_LIM へ退避している
    はずだった。pitch/elbow はこのクランプでは操作しない (arms.h コメント
    「pitch/elbowは無関係」通り、ヨーのみ)。

    leg_angles_deg が None のとき (起動時の中立姿勢初期化など、ゲート非該当
    が自明な場合) は無条件 READY を返す — 中立立位の FR/FL yaw は
    約 +9.12°/-9.12° でゲート(20°)を大きく下回るため、この省略は結果に
    影響しない。

    このシミュレーションが再現しないもの (docstring 冒頭の既知の限界参照):
    wave / 歩行スイング / スルーレート / 地面ガード / 折り畳みガード /
    相互接触クランプ。
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
                 offwidth: int = 1280, offheight: int = 720) -> tuple[mujoco.MjModel, dict]:
    """URDF を MjSpec で読み込み、(1) base_link への free joint 追加,
    (2) 地面プレート追加 (チェッカーテクスチャ + シャドウ用ライト。
    2026-07-31 検証指摘 (3) 対応 — 下記参照), (3) 自己衝突 OFF (既定通り —
    理由は下記), (4) 各関節へ PD 位置アクチュエータ (kp/kv, forcerange は
    URDF の <limit effort=...> をそのまま MuJoCo が actfrcrange として保持
    した値を使う) を追加してコンパイルする。

    自己衝突は既定 OFF のまま: URDF の collision は 25 個の凸包 (project
    context 記載) で隣接リンク間の意図しない自己接触/めり込みロックを避ける
    ための調整をしていない (もともと関節可動域そのものが機構的に自己干渉
    しない設計だが未検証)。地面との接触だけを有効にする contype/conaffinity
    分離で対応する (ロボット内部 geom 同士: contype=2/conaffinity=1、地面:
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
    spec.option.timestep = 1.0 / 240.0
    spec.option.gravity = [0, 0, -9.81]
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
        type=mujoco.mjtGeom.mjGEOM_PLANE,
        size=[PLANE_HALF_SIZE_M, PLANE_HALF_SIZE_M, 0.1], pos=[0, 0, 0],
        friction=[friction_lateral, 0.005, 0.0001],
        material="ground_mat",
        group=2,  # ロボット衝突凸包 (group=0, レンダでは隠す) と区別する
    )

    for body in spec.bodies:
        if body.name in ("world",):
            continue
        for g in body.geoms:
            if g.group == 1:
                continue  # 見た目のみの visual mesh (discardvisual=false で追加) — 非衝突のまま
            g.contype = 2
            g.conaffinity = 1
            g.friction = [friction_lateral, 0.005, 0.0001]

    def add_pd(joint_name: str, kp_v: float, kv_v: float):
        j = spec.joint(joint_name)
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

    model = spec.compile()

    # ---- チューニング (2026-07-31, 実測での安定化):
    # 既定の Euler 陽解法 + damping=0 では、この軽量ロボット (2.87kg, 樹脂/
    # 小型サーボ) の関節PD制御と接触力の組合せで roll/pitch が ±15-20° 級で
    # 発振し 5s 未満で転倒した (最初のチューニング試行の実測)。
    # (1) implicitfast 積分器 (関節ダンピング/PDゲインを陰的に積分し数値減衰
    #     を稼ぐ, MuJoCo 公式推奨: 剛性/減衰が大きい系向け) に変更、
    # (2) 全関節に微小 armature (数値安定化, 実サーボのロータ慣性の近似でも
    #     ある) と damping (受動的な関節粘性— サーボのギア/軸受摩擦の近似,
    #     [UNVERIFIED 実測なし・安定化のための設計目安値]) を追加。
    # この2点だけで静定 (settle 8s 単独テスト) が roll発散→転倒 から
    # roll収束 4.4° 前後の静止安定へ改善したため採用 (試行1回で収束、追加の
    # ゲイン再探索は不要だった)。
    model.opt.integrator = mujoco.mjtIntegrator.mjINT_IMPLICITFAST
    model.dof_armature[:] = 0.001
    model.dof_damping[:] = 0.02

    jid = {n: mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, n) for n in ALL_JOINTS}
    aid = {n: mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, f"act_{n}") for n in ALL_JOINTS}
    return model, {"jid": jid, "aid": aid}


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
    text = (f"t={t:5.2f}s  phase={phase:<6s} vx={vx:+.2f} wz={wz:+.2f}  "
            f"d(xy)={dxy_m * 1000:6.1f}mm")
    bbox = draw.textbbox((8, 6), text, font=font)
    draw.rectangle([(0, 0), (im.width, bbox[3] + 8)], fill=(0, 0, 0, 165))
    draw.text((8, 6), text, fill=(255, 255, 255, 255), font=font)
    im = Image.alpha_composite(im, overlay).convert("RGB")
    return np.array(im)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--settle", type=float, default=1.0)
    ap.add_argument("--walk", type=float, default=8.0, help="前進フェーズ秒数")
    ap.add_argument("--turn", type=float, default=3.0, help="その場旋回フェーズ秒数")
    ap.add_argument("--stop", type=float, default=1.0)
    ap.add_argument("--vx", type=float, default=1.0, help="前進コマンド (-1..1)")
    ap.add_argument("--wz", type=float, default=1.0, help="旋回コマンド (-1..1)")
    ap.add_argument("--friction", type=float, default=1.0,
                     help="地面/足 側方摩擦係数 [UNVERIFIED 設計目安値]")
    ap.add_argument("--kp-leg", type=float, default=24.0,
                     help="脚 PD 位置ゲイン [N*m/rad]。2026-07-31 実測スイープ "
                          "(4/6/10/14/18/24/30 で前進距離比較, novideo) : "
                          "kp=6 で 0.03m -> kp=24 で 0.138m (理論上限 ~0.150m "
                          "の92%) に収束、kp=30 では悪化せず横ばい。転倒なし・"
                          "roll最大6.5°で安定した24を既定値に採用")
    ap.add_argument("--kv-leg", type=float, default=0.40)
    ap.add_argument("--kp-arm", type=float, default=0.8)
    ap.add_argument("--kv-arm", type=float, default=0.03)
    ap.add_argument("--kp-eye", type=float, default=0.05)
    ap.add_argument("--kv-eye", type=float, default=0.005)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--seed", type=int, default=0,
                     help="[2026-07-31 検証指摘 (2) 対応: 現状 no-op] "
                          "本シミュレーションは PD 位置制御 + MuJoCo 決定論的"
                          "積分のみで乱数を一切使用しないため、値を変えても"
                          "結果は変化しない (同一 seed で再実行しビット完全"
                          "一致を確認済み)。将来センサノイズ等を導入する場合"
                          "に備えた予約引数として残している")
    ap.add_argument("--out", type=str, default=str(ROOT / "docs" / "vis_physics_walk.mp4"))
    ap.add_argument("--metrics", type=str, default=str(ROOT / "docs" / "physics_walk_metrics.json"))
    ap.add_argument("--novideo", action="store_true", help="動画書き出しをスキップ (チューニング用)")
    args = ap.parse_args()

    np.random.seed(args.seed)

    kp = {"leg": args.kp_leg, "arm": args.kp_arm, "eye": args.kp_eye}
    kv = {"leg": args.kv_leg, "arm": args.kv_arm, "eye": args.kv_eye}
    model, idx = build_model(args.friction, kp, kv, offwidth=args.width, offheight=args.height)
    data = mujoco.MjData(model)
    dt = model.opt.timestep

    plane_geom_id = 0  # worldbody の最初の geom = 追加した地面プレート

    # ---- 初期姿勢: 静定フェーズと同じ「位相0, 指令0」の中立立ち姿勢
    last_good: dict = {}
    stand, stand_leg_deg = compute_leg_targets(0.0, 0.0, 0.0, 0.0, last_good)
    arm_stand, _ = arm_targets_rad(stand_leg_deg)
    stand.update(arm_stand)
    for jn in EYE_JOINTS:
        stand[jn] = 0.0

    freejoint_qpos_adr = model.jnt_qposadr[model.body_jntadr[
        mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "base_link")]]
    data.qpos[freejoint_qpos_adr:freejoint_qpos_adr + 3] = [0.0, 0.0, BODY_H_M + 0.002]
    data.qpos[freejoint_qpos_adr + 3:freejoint_qpos_adr + 7] = [1.0, 0.0, 0.0, 0.0]
    for jn, val in stand.items():
        data.qpos[model.jnt_qposadr[idx["jid"][jn]]] = val
        data.ctrl[idx["aid"][jn]] = val
    mujoco.mj_forward(model, data)

    # ---- スケジュール
    t_settle_end = args.settle
    t_walk_end = t_settle_end + args.walk
    t_turn_end = t_walk_end + args.turn
    t_stop_end = t_turn_end + args.stop
    total_t = t_stop_end

    def command_at(t: float) -> tuple[float, float, float]:
        if t < t_settle_end:
            return 0.0, 0.0, 0.0
        elif t < t_walk_end:
            return args.vx, 0.0, 0.0
        elif t < t_turn_end:
            return 0.0, 0.0, args.wz
        else:
            return 0.0, 0.0, 0.0

    def phase_name_at(t: float) -> str:
        if t < t_settle_end:
            return "SETTLE"
        elif t < t_walk_end:
            return "WALK"
        elif t < t_turn_end:
            return "TURN"
        else:
            return "STOP"

    driver = PhaseDriver()
    base_body_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "base_link")

    metrics = {
        "time": [], "base_pos": [], "roll_deg": [], "pitch_deg": [], "yaw_deg": [],
        "foot_contact": [], "arm_r_gated": [], "arm_l_gated": [],
    }
    fell = False
    fell_time = None
    max_abs_roll = 0.0
    max_abs_pitch = 0.0
    init_height = BODY_H_M
    init_xy = np.array([0.0, 0.0])  # 初期姿勢設定 (qpos) と一致
    # [2026-07-31 検証指摘 (1) 対応] 連成クランプの実発火回数を集計し、
    # メトリクス JSON に実測値として残す (report での「88%/74%」等の主張を
    # 動画生成そのものの実行結果で裏付ける)
    arm_gate_count = {"r": 0, "l": 0}
    arm_gate_count_by_phase = {"WALK": {"r": 0, "l": 0}, "TURN": {"r": 0, "l": 0}}
    phase_step_count = {"WALK": 0, "TURN": 0}

    # レンダは意匠 (visual, group=1) だけを表示し、衝突凸包 (collision,
    # group=0) は隠す — 両方表示すると2枚のほぼ同一形状メッシュが重なって
    # z-fighting (実測: 初回レンダで赤/青がちらつく縞模様として現れた) する
    scene_opt = mujoco.MjvOption()
    scene_opt.geomgroup[0] = 0
    scene_opt.geomgroup[1] = 1
    scene_opt.geomgroup[2] = 1  # 地面プレート

    # 動画書き出し (ffmpeg へ raw rgb24 フレームを stdin パイプ)
    ffmpeg_proc = None
    renderer = None
    frame_stride = max(1, round(1.0 / (args.fps * dt)))
    if not args.novideo:
        renderer = mujoco.Renderer(model, height=args.height, width=args.width)
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        ffmpeg_cmd = [
            "ffmpeg", "-y", "-f", "rawvideo", "-pixel_format", "rgb24",
            "-video_size", f"{args.width}x{args.height}", "-framerate", str(args.fps),
            "-i", "-", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-crf", "18", "-preset", "medium", str(out_path),
        ]
        ffmpeg_proc = subprocess.Popen(ffmpeg_cmd, stdin=subprocess.PIPE)

    n_steps = int(round(total_t / dt))
    frame_count = 0

    for step in range(n_steps):
        t = step * dt
        vx, vy, wz = command_at(t)
        phase_name = phase_name_at(t)
        phase = driver.step(dt, vx, vy, wz)
        targets, leg_deg = compute_leg_targets(phase, vx, vy, wz, last_good)
        for jn, val in targets.items():
            data.ctrl[idx["aid"][jn]] = val
        # [2026-07-31 検証指摘 (1) 対応] 腕は基本 READY 固定のまま、前脚
        # (FR/FL)×腕 連成クランプ (arms.h L147/195) だけを毎ステップ反映する
        # (目は引き続き常時0固定, 初期値のまま変更しない)
        arm_t, (r_gated, l_gated) = arm_targets_rad(leg_deg)
        for jn, val in arm_t.items():
            data.ctrl[idx["aid"][jn]] = val
        if r_gated:
            arm_gate_count["r"] += 1
        if l_gated:
            arm_gate_count["l"] += 1
        if phase_name in phase_step_count:
            phase_step_count[phase_name] += 1
            if r_gated:
                arm_gate_count_by_phase[phase_name]["r"] += 1
            if l_gated:
                arm_gate_count_by_phase[phase_name]["l"] += 1

        mujoco.mj_step(model, data)

        base_pos = data.xpos[base_body_id].copy()
        base_quat = data.xquat[base_body_id].copy()
        roll, pitch, yaw = quat_to_rpy(base_quat)
        roll_d, pitch_d, yaw_d = math.degrees(roll), math.degrees(pitch), math.degrees(yaw)
        max_abs_roll = max(max_abs_roll, abs(roll_d))
        max_abs_pitch = max(max_abs_pitch, abs(pitch_d))

        if not fell and (abs(roll_d) > 30.0 or abs(pitch_d) > 30.0 or base_pos[2] < init_height * 0.5):
            fell = True
            fell_time = t

        # サンプリング (10Hz 相当で記録、ファイルサイズ抑制)
        if step % max(1, round(1.0 / (10 * dt))) == 0:
            n_contact = 0
            for ci in range(data.ncon):
                c = data.contact[ci]
                if c.geom1 == plane_geom_id or c.geom2 == plane_geom_id:
                    n_contact += 1
            metrics["time"].append(round(t, 4))
            metrics["base_pos"].append([round(float(v), 5) for v in base_pos])
            metrics["roll_deg"].append(round(roll_d, 3))
            metrics["pitch_deg"].append(round(pitch_d, 3))
            metrics["yaw_deg"].append(round(yaw_d, 3))
            metrics["foot_contact"].append(n_contact)
            metrics["arm_r_gated"].append(bool(r_gated))
            metrics["arm_l_gated"].append(bool(l_gated))

        if renderer is not None and step % frame_stride == 0:
            cam = mujoco.MjvCamera()
            cam.type = mujoco.mjtCamera.mjCAMERA_FREE
            cam.lookat = [base_pos[0], base_pos[1], base_pos[2] + 0.02]
            cam.distance = 0.78
            cam.azimuth = 205.0 + 12.0 * math.sin(2 * math.pi * t / total_t)
            cam.elevation = -18.0
            renderer.update_scene(data, camera=cam, scene_option=scene_opt)
            img = renderer.render()
            # [2026-07-31 検証指摘 (4) 対応] 地面グリッド (build_model) に加え、
            # 経過時間・フェーズ・累積並進量を毎フレーム焼き込む。追従カメラは
            # そのまま (歩容自体が見えなくなるほど引くとむしろ改悪 — 下記
            # draw_hud docstring 参照) だが、これで動画単体・数値表示の両方
            # から並進を確認できる。レンダリングの後処理のみ、物理/メトリク
            # スJSONの値には一切影響しない
            dxy_m = float(np.linalg.norm(base_pos[:2] - init_xy))
            img = draw_hud(img, t, phase_name, vx, wz, dxy_m)
            ffmpeg_proc.stdin.write(img.tobytes())
            frame_count += 1

    # 前進距離: 単一サンプルだと歩容周期内スウェイ (CG シフト, 振幅 3-5cm) の
    # 位相次第でぶれるため、walk フェーズ最初/最後の 1 CYCLE_T 分を平均して
    # ノイズを均す (クロール歩容の周期内振動と周期をまたぐ正味の並進を分離)
    ts_t = np.array(metrics["time"])
    ts_xy = np.array([[p[0], p[1]] for p in metrics["base_pos"]])
    win = CYCLE_T
    m_start = (ts_t >= t_settle_end) & (ts_t < t_settle_end + win)
    m_end = (ts_t >= t_walk_end - win) & (ts_t < t_walk_end)
    if m_start.sum() > 0 and m_end.sum() > 0:
        start_xy = ts_xy[m_start].mean(axis=0)
        end_xy = ts_xy[m_end].mean(axis=0)
    else:
        start_xy = ts_xy[0]
        end_xy = ts_xy[-1]
    forward_distance_m = float(np.linalg.norm(end_xy - start_xy))
    final_pos = data.xpos[base_body_id].copy()
    net_distance_m = float(np.linalg.norm(final_pos[:2]))

    if ffmpeg_proc is not None:
        ffmpeg_proc.stdin.close()
        ffmpeg_proc.wait()
    if renderer is not None:
        renderer.close()

    result = {
        "engine": "mujoco",
        "mujoco_version": mujoco.__version__,
        "fallback_reason": "pybullet source build failed on this macOS/clang combo "
                            "(bundled Bullet3 zlib K&R decl vs macOS SDK 26 stdio.h); "
                            "no arm64 pip wheel available either. mujoco arm64 wheel "
                            "installs cleanly.",
        "timestep_s": dt,
        "total_sim_time_s": total_t,
        "schedule_s": {"settle": args.settle, "walk": args.walk, "turn": args.turn, "stop": args.stop},
        "commands": {"vx": args.vx, "wz": args.wz},
        "gains": {"kp": kp, "kv": kv},
        "friction_lateral": args.friction,
        "forward_distance_during_walk_phase_m": round(forward_distance_m, 5),
        "net_xy_distance_start_to_end_m": round(net_distance_m, 5),
        "max_abs_roll_deg": round(max_abs_roll, 3),
        "max_abs_pitch_deg": round(max_abs_pitch, 3),
        "fell": fell,
        "fell_time_s": fell_time,
        "ik_fallback_count": last_good.get("_ik_fail_count", 0),
        "success_forward_ge_0p3m": forward_distance_m >= 0.3 and not fell,
        "video_frames": frame_count,
        "video_path": None if args.novideo else str(args.out),
        # [2026-07-31 検証指摘 (1) 対応] 前脚(FR/FL)×腕 連成クランプの実発火
        # 実績 (このシミュレーション実行そのものの実測値。事前の独立試算
        # ではなく、実際に動画生成に使ったのと同じ n_steps ループでの集計)
        "arm_leg_yaw_gate": {
            "gate_deg": ARM_LEG_YAW_GATE_DEG,
            "arm_yaw_lim_deg": ARM_YAW_LIM_DEG,
            "total_steps": n_steps,
            "fire_steps": arm_gate_count,
            "fire_fraction_overall": {
                k: round(v / n_steps, 4) for k, v in arm_gate_count.items()
            },
            "fire_fraction_by_phase": {
                ph: {k: round(v / phase_step_count[ph], 4) if phase_step_count[ph] else None
                     for k, v in cnt.items()}
                for ph, cnt in arm_gate_count_by_phase.items()
            },
        },
        "timeseries": metrics,
    }
    Path(args.metrics).parent.mkdir(parents=True, exist_ok=True)
    Path(args.metrics).write_text(json.dumps(result, indent=2, ensure_ascii=False))

    print(f"forward_distance_during_walk_phase_m = {forward_distance_m:.4f}")
    print(f"net_xy_distance_start_to_end_m = {net_distance_m:.4f}")
    print(f"max_abs_roll_deg = {max_abs_roll:.2f}  max_abs_pitch_deg = {max_abs_pitch:.2f}")
    print(f"fell = {fell}  fell_time_s = {fell_time}")
    print(f"ik_fallback_count = {last_good.get('_ik_fail_count', 0)}")
    gate_r = result["arm_leg_yaw_gate"]["fire_fraction_overall"]["r"]
    gate_l = result["arm_leg_yaw_gate"]["fire_fraction_overall"]["l"]
    print(f"arm_leg_yaw_gate fire_fraction_overall: r={gate_r:.3f} l={gate_l:.3f}")
    print(f"success (>=0.3m, not fell) = {result['success_forward_ge_0p3m']}")
    if not args.novideo:
        print(f"video_frames = {frame_count} -> {args.out}")
    print(f"metrics saved -> {args.metrics}")


if __name__ == "__main__":
    main()
