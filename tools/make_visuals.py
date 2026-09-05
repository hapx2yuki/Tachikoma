#!/usr/bin/env python3
"""組立イメージ (静止画) と動作イメージ (動画) の生成。

出力:
  docs/vis_exploded_leg.png   — 脚 1 本の分解図 (サーボ配置込み)
  docs/vis_exploded_arm.png   — 腕 1 本の分解図 (頭部ソケット直下・放射マウント吊り下げ式)
  docs/vis_elbow_detail.png   — 肘 (elbow_shell 化粧カバー) のクローズアップ2アングル
  docs/vis_hand_detail.png    — 手 (claw_mount+爪ハブ+指+指先チップ) のクローズアップ2アングル
                                 (2026-07-29 固定爪化: キットの三つ叉爪に見えるか目視確認用)
  docs/vis_chassis_layout.png — シャーシの電装レイアウト
  docs/vis_assembly_steps.png — 組立ステップ 4 コマ
  docs/vis_walk.mp4           — 前進 → 旋回 → 横歩き → 体高 → 腕動作
  docs/vis_eyes.mp4           — 目 (キョロキョロ機構) の動作イメージ
  docs/vis_wiring.mp4         — 配線イメージ (電源/脚/腕+目/ロジック)
  docs/vis_proportions.png    — 意匠シェル込みの比率確認 (全パーツ一律 150%)
"""
import sys
from pathlib import Path

import numpy as np
import trimesh
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
import imageio.v2 as imageio

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "docs"
sys.path.insert(0, str(ROOT / "hardware" / "src"))
sys.path.insert(0, str(ROOT / "tools"))
import config as C  # noqa: E402
from sim_gait import (leg_ik, foot_target, ORIGIN, MOUNT, STANCE_R, BODY_H,  # noqa: E402
                      PHASE_OFF, DUTY, MAX_STEP, BODY_H_RANGE, _LEGS as LEG_NAMES)
import kit_assembly as KIT  # noqa: E402
import make_camera as CAM  # noqa: E402

STL = ROOT / "hardware" / "stl"
MODEL = ROOT / "model"
# キット意匠パーツの配置 (tools/data/kit_assembly_{front,rear}.json)。
# dress=True 描画モード (フルドレス) で使う — kit_assembly.py 参照
KIT_PLACEMENTS = KIT.load_placements()
LIGHT = np.array([0.4, -0.6, 0.7]) / np.linalg.norm([0.4, -0.6, 0.7])

COL = {"coxa": "#5577cc", "femur": "#cc7755", "tibia": "#55aa77",
       "servo": "#333a45", "shell": "#3b62c4", "cap": "#9aa4b0",
       "chassis": "#8899aa", "tip": "#222222",
       # leg_foot_bored (2026-07-28 Leg_Foot 化): 元キット Leg_Foot と同じ
       # グレー (kit_assembly.COLOR_HEX["Grey"] と一致させる)
       "foot": "#9aa4b0",
       "bracket": "#93a3b5", "uarm": "#cc7755",
       "farm": "#55aa77", "palm": "#5a6472", "finger": "#333a45",
       # dress=True 用: femur_link/tibia_link/upper_arm/forearm はキットの
       # 意匠シェルに完全には覆われない内部骨格なので、分解図用のデバッグ配色
       # (オレンジ/グリーン) のままだと完成写真に無い色が透けて見える
       # (QA critical 指摘)。kit_assembly.py の Grey トークンと同じ中間グレー
       # に差し替えて意匠パーツと衝突しないようにする
       "skel_dress": "#9aa4b0"}

# dress=True 描画で「浮遊パーツ」に見える既知のジオメトリ不整合パーツ
# (kit_assembly_front.json 自身が finding として指摘済み — 根本対応は
# Mouth 系パーツの再測量など別途設計判断が必要)。
# 暫定対応として dress モードの描画からのみ除外する (QA major 指摘)。
#
# Leg_Shin_Guard_Grey_x4 は 2026-07 に解消済み (DRESS_SKIP_PARTS から除去):
# 元の 3MF source_offset 置換 (非touchingペア, gap 15.5mm) は shin_shell 実
# メッシュの実測半径 (最大 ~31mm) と矛盾する r=72mm を出す明らかな誤りだった
# — kit_assembly_front.json の method 欄に再導出の経緯を記録し、shin_shell
# 実面に沿う reasoned 配置 (z=-75, 実測面 embed 2mm) へ置き換えた。
# 2026-07-28 監査で上記「embed 2.04mm」の自己申告が実際の shin_shell()
# 出力に対して再現しない (実測 13.9% embed / 最大 15mm 浮き) と判明し、
# 6自由度の数値フィット (matrix フィールド, differential_evolution) へ
# 再置換済み (kit_assembly_front.json 参照。Leg_Thigh_Guard_Blue_x4 も同時に
# 同様の signed_distance 実測ベースで再フィット — thigh_cap() が
# Leg_Thigh_Grey_x4 の上55%だけを残す派生形状であることを見落とした旧 t.z が
# 原因で全頂点が浮いていた)。どちらも DRESS_SKIP_PARTS には追加していない
# (完全な密着ではないが、以前より大幅に浮きが減っているため描画に残す)。
# Head_Top_Blue の zb からの Z オフセット (shell_ghosts/kit_dress_static/
# wiring_video の 3箇所で同じ値を使う必要がある — 以前は生の数値 zb+57.7 が
# 3箇所へ複製されていて、どこか1箇所だけ更新されるとドリフトしても検出
# できなかった (QA minor 指摘)。ここへ一元化し、3箇所とも本定数を参照する。
# 根拠は shell_ghosts() 冒頭のコメント (Head_Bottom/Head_Top リムの面一
# 実測、kit_assembly_front.json Head_Top_Eyecut の "finding" 参照)。
#
# 頭部の Y オフセット (config.py ARM_MOUNT_HUB_Y) についても同種の穴が
# 2026-07-31 QA で発覚した: shell_ghosts()/kit_dress_static()/wiring_video()
# の頭部配置が生の literal 12 を複製しており、HEAD_TOP_Z_OFFSET と違って
# 一元化されていなかった (ARM_MOUNT_HUB_Y を将来変更しても可視化/ドキュメント
# 用レンダだけ追従せず古い位置のまま描画されるドリフトの恐れ)。上記と同じ
# 一元化パターンを適用し、該当箇所は全て C.ARM_MOUNT_HUB_Y を直接参照する
# よう修正済み (値そのものは 12.0 のまま不変)。
HEAD_TOP_Z_OFFSET = C.HEAD_TOP_Z_OFFSET

# 左右目 (eye_pod, index 0=右/2=左) の取付ロール補正 (ソケット法線まわり、
# align_vectors([0,0,1], n) の後に追加で乗せる角度)。eyes_video() と
# kit_dress_static() の両方が同じ eye_pod (make_eye.py — Head_Eye_White_x3
# 実測形状をそのまま流用しており、視線ドット3つの穴は CAP_NORM で局所 -Y
# 側に固定済み = 決定的、位置は EYE_DOTS_150 と同一) を配置するため、ここへ
# 一元化してドリフトを防ぐ (HEAD_TOP_Z_OFFSET と同じ理由)。
# 2026-07-30 反転監査で判明: align_vectors() が選ぶロール (ソケット法線まわ
# りの位相) は無補正だと両目とも視線ドットが「内側 (中心/相手目側)」に来る
# が、完成写真 (scratchpad/3mf_thumbs/thumbnail_middle.png 目元クロップ) で
# は両目とも「外側下寄り」— 左右対称の取り違え。ソケット法線が局所X軸まわ
# りに鏡像 (右目 n=(+.,0,.) / 左目 n=(-.,0,.)) なので align_vectors の選ぶ
# ロールの向きも鏡像になり、同符号の補正では揃わない (タレット固定
# 2026-07-29 と同じ現象)。右目 (index0) に +45°、左目 (index2) に -45° を
# 追加すると外側下寄りに一致することを実測・写真比較で確認済み (ソケット
# 法線がXZ平面内=Y成分ゼロのため「外側」と「上」が face-plane 上で同一直線
# 上になる特殊形状で、0°=純内側下寄り/90°=輪郭際で上下の手がかりが薄い、
# の中間である45°が最も写真と整合)。
EYE_DOT_ROLL_DEG = {0: 45.0, 2: -45.0}

# dress=True 描画で除外するパーツ名の集合 (既知のジオメトリ不整合/浮遊
# パーツの暫定回避用)。2026-07-30 Mouth 物理チェーン復元タスクで、それまで
# ここに入っていた Mouth_Ball_Grey/Mouth_Neck_Blue の両方を除去し空集合に
# なった (kit_assembly_front.json の Mouth_Ball_Grey/Mouth_Neck_Blue/
# Mouth_Cannon_Grey エントリの "method"/"finding"/"note" 参照)。経緯: Ball の
# 実測球半径 (最小二乗フィット, R_BALL_150=12.557mm) と Neck 自身の実測全長
# (NECK_AXIAL_LEN_150=16.092mm, Y bbox 全長) を実測し、Neck flange 面 (Ball
# 側) が Ball 表面に外接する「tangent」ではなく Ball 球心と一致する
# 「coincident」という読みで積み上げた (standoff=NECK_AXIAL_LEN_150=16.092mm)。
#   2026-07-30 QA修正 (同日, 二回目): coincident は Ball∩Neck の manifold3d
#   交差体積が Neck 自身の体積の 84.6% (ほぼ全体が Ball に埋没) になることが
#   判明し、完成写真の確定読解 (「青いコーン、可視!」) や docs/assembly.md
#   §2.8 の「Neck は意匠パーツとして塗装すること」という記述と矛盾していた。
#   非干渉な tangent (standoff=28.649mm) は tools/check_arm.py [8b] の
#   レイアウト意図 (両腕の間に見える) を -6.0mm で破るため採用できず、
#   standoff という1自由度だけでは両立しないトレードオフと判明した。妥協策
#   として [8b] マージン+1.5mm を保てる範囲で tangent 側へ寄せた
#   standoff=20.0mm を採用 (数値探索, scratchpad/mouth_fix/sweep_standoff.py)
#   — Ball∩Neck は Neck 体積の 49.4% まで低減 (84.6%から半減以下)、露出面積
#   ベース (face重心が球外の割合) は 71% (旧45%から改善)。**それでも体積の
#   約半分はなお Ball に埋没したままで、Neck の完全露出は達成できていない**
#   — 詳細・数値根拠は config.py MOUTH_CANNON_REAR_STANDOFF_MM のコメント
#   参照。Cannon 後端 (CANNON_Y_REAR) はソケット面から軸方向 +20.0mm
#   (採用した妥協 standoff) に、砲口は +61.8mm に出る — Cannon/Neck とも全長が
#   頭部外 (Head_Bottom シェルとの manifold3d boolean 交差体積は Cannon/Cap/
#   Neck/Ball いずれも 0.0mm3, standoff変更後も再実測)。
#   Mouth_Neck_Blue: 頭部外だが Ball との部分埋没 (49.4%, 上記) が残るため
#     「完全に露出」ではない → それでも描画対象には復帰させる (埋没していない
#     残り半分は完成写真の「両腕の間に大型のグレー円筒が青いコーンを介して
#     吊り下がる」の「青いコーン」として一部視認できる想定, UNVERIFIED
#     — ピクセル単位の写真照合はしていない)。
#   Mouth_Ball_Grey: 球心が Neck flange 面よりさらに Cannon から離れた位置に
#     あるため、ソケット面 (Head_Bottom 前面) から見て外向きの半球は開放
#     空間に露出する — 実レンダで確認 (scratchpad/mouth_check/check3.png,
#     check4_solid.png 相当) したところ、球がソケット直下にはっきり見える
#     「関節の球」として視認できた (完成写真の顎下の小さな灰色突起の付け根に
#     相当する見た目) ため、こちらも復帰。
# 将来また浮遊/不整合パーツが見つかった場合はこの集合へ追加すること。
DRESS_SKIP_PARTS = set()

# 腕プリセット (firmware/src/config.h と一致): (ヨー, ピッチ, 肘, グリップ)
# ヨー + = 外側へ開く (右腕基準)。中立 (ヨー0) の向きは前方 (+Y) ではなく
# 正面から C.ARM_MOUNT_YAW_DEG=40° の放射外向き (Head_Bottom 実ソケット準拠) —
# 手先方位 = ARM_MOUNT_YAW_DEG + yaw (arms.h / check_arm.py [4][5] と同式)
ARM_TUCK = (0.0, 55.0, 95.0, 0.0)
ARM_READY = (10.0, 30.0, 40.0, 0.0)
ARM_REACH = (0.0, 10.0, 10.0, 0.0)


def fw_arm_clamp(pose, body_h=115.0):
    """firmware arms.h と同じリミット (ヨー±15 / 地面ガード / 腕相互クランプ)。

    2026-07-29 固定爪化: pose の第4要素 (旧グリップ角) は firmware 側で
    廃止済みだが、呼び出し側 API 互換のため引数としては引き続き受け取り
    そのまま (未使用値として) 返す。腕長は C.UPPER_ARM_LEN/ARM_HAND_REACH_MM
    (旧ハードコード 55.0/79.0 から置換 — 固定爪化で 79→47.55 に短縮)。
    """
    ay, ap, ae, g = pose
    ay = np.clip(ay, -15.0, 15.0)
    ap = np.clip(ap, -45.0, 85.0)
    ae = np.clip(ae, 0.0, 95.0)
    U, R = C.UPPER_ARM_LEN, C.ARM_HAND_REACH_MM
    # 地面ガード (肩ピッチ軸 = body_h + 9.2, 余裕 8mm)
    dmax = body_h + 9.2 - 8.0
    while (U * np.sin(np.radians(ap))
           + R * np.sin(np.radians(ap + ae))) > dmax and ap > -45.0:
        ap -= 0.5
    # 折り畳み深追いガード + 折り畳みヨーガード + 腕相互クランプ (arms.h と一致)。
    # 放射マウントでは手先方位 = ARM_MOUNT_YAW_DEG + yaw なので
    # hand_x = MOUNT_X + planar·sin(MOUNT_YAW+yaw) ≥ HAND_HALF を解く
    # (「sin(yaw) だけ・前向き固定」の旧式は 2026-07-28 移設で撤去)
    while (U * np.cos(np.radians(ap))
           + R * np.cos(np.radians(ap + ae))) < -5.0 and ap > -45.0:
        ap -= 0.5
    planar = U * np.cos(np.radians(ap)) + R * np.cos(np.radians(ap + ae))
    if planar < -5.0:
        ay = 0.0
    elif ay < 0:
        lat_max = C.ARM_MOUNT_XY[0] - 14.5
        if planar > lat_max:
            ay = max(ay, -C.ARM_MOUNT_YAW_DEG
                     - np.degrees(np.arcsin(lat_max / planar)))
    return (float(ay), float(ap), float(ae), float(g))


def rot(deg, axis):
    t = np.radians(deg)
    c, s = np.cos(t), np.sin(t)
    m = np.eye(4)
    if axis == "x":
        m[1, 1], m[1, 2], m[2, 1], m[2, 2] = c, -s, s, c
    elif axis == "y":
        m[0, 0], m[0, 2], m[2, 0], m[2, 2] = c, s, -s, c
    else:
        m[0, 0], m[0, 1], m[1, 0], m[1, 1] = c, -s, s, c
    return m


def trans(x, y, z):
    m = np.eye(4)
    m[:3, 3] = [x, y, z]
    return m


def draw_mesh(ax, mesh, color, alpha=1.0):
    tri = mesh.vertices[mesh.faces]
    lum = 0.45 + 0.55 * np.clip(mesh.face_normals @ LIGHT, 0, 1)
    base = np.array(matplotlib.colors.to_rgb(color))
    fc = np.c_[lum[:, None] * base[None, :], np.full(len(lum), alpha)]
    ax.add_collection3d(Poly3DCollection(tri, facecolor=fc, edgecolor="none"))


def draw_meshes(ax, items):
    """複数メッシュを 1 つの Poly3DCollection に結合して描く。

    matplotlib 3D はコレクション同士を「コレクション平均深度」で前後合成する
    ため、面同士が近い別メッシュ (眼球と象嵌レンズ等) は丸ごと隠れることが
    ある。1 コレクションに結合すると面単位の zsort が効いて正しく重なる。
    items: [(mesh, color, alpha), ...]
    """
    tris, fcs = [], []
    for mesh, color, alpha in items:
        tris.append(mesh.vertices[mesh.faces])
        lum = 0.45 + 0.55 * np.clip(mesh.face_normals @ LIGHT, 0, 1)
        base = np.array(matplotlib.colors.to_rgb(color))
        fcs.append(np.c_[lum[:, None] * base[None, :],
                         np.full(len(lum), alpha)])
    ax.add_collection3d(Poly3DCollection(
        np.concatenate(tris), facecolor=np.concatenate(fcs), edgecolor="none"))


def frame_axes(ax, pts, zfloor=None, pad=1.05):
    lo, hi = pts.min(0), pts.max(0)
    c = (lo + hi) / 2
    r = float((hi - lo).max()) / 2 * pad
    ax.set_xlim(c[0] - r, c[0] + r)
    ax.set_ylim(c[1] - r, c[1] + r)
    # 床より下にある部品を切り捨てると、先行接地や貫通が図から消える。
    zmin = min(float(zfloor), c[2] - r) if zfloor is not None else c[2] - r
    ax.set_zlim(zmin, zmin + 2 * r)
    ax.set_box_aspect([1, 1, 1])
    ax.axis("off")


def servo_box():
    # DS3218 級 STD サーボの概形
    m = trimesh.creation.box((40.7, 20.2, 39.2))
    return m


def servo_box_micro():
    # MG90S 級 MICRO サーボの概形
    return trimesh.creation.box((23.0, 12.4, 26.0))


_MESH_CACHE = {}


def load(name, source=STL):
    path = (Path(source) / f"{name}.stl").resolve()
    stat = path.stat()
    stamp = (stat.st_mtime_ns, stat.st_size)
    cached = _MESH_CACHE.get(path)
    if cached is None or cached[0] != stamp:
        _MESH_CACHE[path] = (stamp, trimesh.load(path))
    return _MESH_CACHE[path][1].copy()


# ---------------------------------------------------------------- 分解図
def exploded_leg():
    fig = plt.figure(figsize=(13, 8))
    ax = fig.add_subplot(111, projection="3d")
    items = []

    coxa = load("coxa_bracket"); items.append((coxa, COL["coxa"], 1.0))
    sv_yaw = servo_box(); sv_yaw.apply_transform(rot(90, "x") @ trans(0, 0, 0))
    sv_yaw.apply_transform(trans(0, 0, 62)); items.append((sv_yaw, COL["servo"], 1.0))
    sv_pit = servo_box(); sv_pit.apply_transform(rot(90, "x"))
    sv_pit.apply_transform(trans(C.COXA_LEN - 6, 55, 0))
    items.append((sv_pit, COL["servo"], 1.0))

    fem = load("femur_link"); fem.apply_transform(trans(C.COXA_LEN + 55, 0, 0))
    items.append((fem, COL["femur"], 1.0))
    sv_knee = servo_box(); sv_knee.apply_transform(rot(90, "x"))
    sv_knee.apply_transform(trans(C.COXA_LEN + 55 + C.FEMUR_LEN - 6, 55, 0))
    items.append((sv_knee, COL["servo"], 1.0))

    tib = load("tibia_link")
    tib.apply_transform(trans(C.COXA_LEN + C.FEMUR_LEN + 130, 0, 40))
    items.append((tib, COL["tibia"], 1.0))
    tip = load("leg_foot_bored")
    tip.apply_transform(rot(180, "x") @ trans(0, 0, 0))
    tip.apply_transform(trans(C.COXA_LEN + C.FEMUR_LEN + 130, 0, -125))
    items.append((tip, COL["foot"], 1.0))

    shell = load("shin_shell")
    shell.apply_transform(rot(180, "x"))  # 印刷向き→機能向きに戻す
    shell.apply_transform(trans(C.COXA_LEN + C.FEMUR_LEN + 210, 0, 27))
    items.append((shell, COL["shell"], 0.55))
    cap = load("thigh_cap")
    cap.apply_transform(trans(C.COXA_LEN + 55 + C.FEMUR_LEN / 2 - 8, 0, 55))
    items.append((cap, COL["cap"], 0.8))

    pts = []
    for m, c, a in items:
        draw_mesh(ax, m, c, a)
        pts.append(m.vertices)
    frame_axes(ax, np.vstack(pts), pad=1.0)
    ax.view_init(elev=16, azim=-58)

    labels = [
        ("ヨーサーボ (シャーシに取付)", (0, 0, 92)),
        ("coxa_bracket\n(ヨーホーン結合)", (-10, 0, -35)),
        ("股ピッチサーボ", (C.COXA_LEN - 6, 78, 18)),
        ("femur_link (クレビス)", (C.COXA_LEN + 60, 0, -42)),
        ("膝サーボ", (C.COXA_LEN + 50 + C.FEMUR_LEN, 78, 18)),
        ("tibia_link", (C.COXA_LEN + C.FEMUR_LEN + 100, 0, 85)),
        ("leg_foot_bored (元Leg_Foot+隠しTPUパッド)", (C.COXA_LEN + C.FEMUR_LEN + 130, 0, -150)),
        ("shin_shell (意匠, 下からスライド)", (C.COXA_LEN + C.FEMUR_LEN + 215, 0, -125)),
        ("thigh_cap (接着)", (C.COXA_LEN + 40 + C.FEMUR_LEN / 2, 0, 88)),
    ]
    for txt, (x, y, z) in labels:
        ax.text(x, y, z, txt, fontsize=9, ha="center",
                bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="#888", alpha=0.9))
    ax.set_title("脚 1 本の分解図 (M3: サーボタブ / M2.6: ホーン共締め / ホーン片持ち結合)",
                 fontsize=12)
    fig.tight_layout()
    fig.savefig(OUTPUT_DIR / "vis_exploded_leg.png", dpi=120, facecolor="white")
    plt.close(fig)
    print("saved vis_exploded_leg.png")


# ---------------------------------------------------------------- 腕の分解図
def exploded_arm():
    """上段: 組立状態の腕 (READY, シャーシ前縁から吊り下げ) / 下段: パーツ個別。"""
    fig = plt.figure(figsize=(14, 9))
    gs = fig.add_gridspec(2, 7, height_ratios=[1.6, 1.0])

    # ---- 上段: 組立状態 (右腕, READY 姿勢, プレート下面 z=0 相当)
    ax = fig.add_subplot(gs[0, :], projection="3d")
    ms = arm_meshes(1, ARM_READY, 0.0, body_h=115.0)
    pts = []
    for m, c, a in ms:
        draw_mesh(ax, m, c, a)
        pts.append(m.vertices)
    mx, my = C.ARM_MOUNT_XY
    # マウント周辺のシャーシのイメージ (プレート片 + ヨーサーボのダミー。
    # ケース開口は放射方向へ回転させない軸平行配置 — make_chassis.py と一致)
    stub = trimesh.creation.box((70, 60, 4))
    stub.apply_transform(trans(mx, my - 8, 2))
    draw_mesh(ax, stub, COL["chassis"], 0.45); pts.append(stub.vertices)
    sv = servo_box_micro()
    sv.apply_transform(rot(90, "z") @ np.eye(4))
    sv.apply_transform(trans(mx, my - 5.6, 12))
    draw_mesh(ax, sv, COL["servo"], 0.9); pts.append(sv.vertices)
    frame_axes(ax, np.vstack(pts), pad=1.15)
    ax.view_init(elev=12, azim=-40)
    # 腕は中立で正面から ARM_MOUNT_YAW_DEG=40° の放射外向きに伸びる —
    # 肘/グリッパのラベルはその方位 (READY のヨー+10° 込み) に沿わせる
    azr = np.radians(C.ARM_MOUNT_YAW_DEG + ARM_READY[0])
    ux, uy = np.sin(azr), np.cos(azr)
    labels = [
        ("肩ヨー MG90S (シャーシへ上から挿入,\n軸下向き。ホーンがプレート下面側)",
         (mx, my - 40, 34)),
        ("shoulder_bracket\n(ホーンから吊り下げ)", (mx, my - 45, -22)),
        ("肘 (MG90S)\nupper_arm 55mm", (mx + 80 * ux, my + 80 * uy, 12)),
        ("固定爪 (可動なし)\nforearm 16mm + claw_mount\n元キット爪ハブ+指3+指先チップ3",
         (mx + 110 * ux, my + 110 * uy, -75)),
    ]
    for txt, (x, y, z) in labels:
        ax.text(x, y, z, txt, fontsize=9, ha="center",
                bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="#888", alpha=0.9))
    ax.set_title("腕の組立状態 (READY) — 頭部ソケット直下 (正面±40°) のシャーシ"
                 "下面から吊り下げ・中立は放射外向き、寸法はキット比率。"
                 "青ポッド = 元 Arm パーツのクラムシェル", fontsize=12)

    # ---- 下段: パーツ個別
    parts = [
        ("shoulder_bracket", COL["bracket"], "ヨーホーンから吊り下げ"),
        ("upper_arm", COL["uarm"], "肩→肘 55mm"),
        ("forearm", COL["farm"], "肘→手首 16mm (キット実測比率)"),
        ("claw_mount", COL["palm"], "平坦円盤, 爪ハブへ接着"),
        ("arm_pod_upper", COL["shell"], "元 Arm ポッド (クラムシェル上)"),
        ("arm_pod_lower", COL["shell"], "元 Arm ポッド (クラムシェル下)"),
        ("elbow_shell", COL["cap"], "元 Elbow 球の化粧半殻"),
    ]
    for i, (name, color, note) in enumerate(parts):
        axp = fig.add_subplot(gs[1, i], projection="3d")
        m = load(name)
        draw_mesh(axp, m, color, 1.0)
        frame_axes(axp, m.vertices, pad=1.2)
        axp.view_init(elev=20, azim=-55)
        axp.set_title(f"{name}\n{note}", fontsize=8)
    fig.suptitle("腕パーツ構成 (右腕。左腕 *_L はミラー出力, 爪ハブ/指/指先"
                 "チップは元キット無加工パーツを両腕共通で使用)",
                 fontsize=13)
    fig.tight_layout()
    fig.savefig(OUTPUT_DIR / "vis_exploded_arm.png", dpi=120, facecolor="white")
    plt.close(fig)
    print("saved vis_exploded_arm.png")


def elbow_detail_still():
    """肘 (upper_arm/elbow_shell/forearm 継ぎ目) だけを拡大したクローズアップ。

    vis_exploded_arm.png は分解図でデバッグ配色 (skel_dress) のため、化粧
    カバー (elbow_shell, 元 Arm_Right_Elbow_Grey の半殻) が normal 姿勢で
    実際どう見えるか標準アングルの静止画からは判別しづらい (QA minor 指摘:
    肘サーボ箱がそのまま露出して見えないかの確認ができない)。dress=True
    (フルドレス配色) の腕を組み、elbow_shell 自身の bbox を中心に専用ズーム
    した2アングルを保存し、単独で目視確認できるようにする。
    """
    fig = plt.figure(figsize=(11, 5.5))
    body_h = 115.0
    ms = arm_meshes(1, ARM_READY, 0.0, body_h=body_h, dress=True)
    # 肘 (elbow_shell) の中心 = Te (肩ピッチ軸から upper_arm 分オフセット)。
    # 色でメッシュを探すのは COL["skel_dress"] と KIT.kit_color(*_Grey) が
    # 同じ "#9aa4b0" になり得て upper_arm/forearm まで拾ってしまうため
    # (dress=True 時は意図的に同系グレーへ揃えている)、arm_meshes() 内部の
    # 変換式 (Te 定義, 428-431行目付近) をそのまま再現して直接その原点を使う
    ay, ap, _ae, _grip = fw_arm_clamp(ARM_READY, body_h)
    PA = C.ARM_SERVO
    pz = 2.5 + PA["HORN_HUB_H"] - 2.0
    pitch_dn = pz + 2.5 + (PA["W"] / 2 + 2.5) - 0.1
    mx, my = C.ARM_MOUNT_XY
    T0 = trans(mx, my, 0.0 - 2.0)  # plate_bottom_z=0.0 (exploded_arm と同じ単体基準)
    Ty = T0 @ rot(90 - C.ARM_MOUNT_YAW_DEG - ay, "z")
    Tu = Ty @ trans(20, 0, -pitch_dn) @ rot(ap, "y")
    Te = Tu @ trans(C.UPPER_ARM_LEN, 0, 0)
    center = Te[:3, 3]
    zoom = 30.0  # 継ぎ目周辺 ±30mm (肘サーボ箱+化粧カバー全体が入る余裕)
    for i, (elev, azim, ttl) in enumerate(
            [(15, -30, "肘クローズアップ (3/4)"), (8, -90, "肘クローズアップ (真横)")]):
        ax = fig.add_subplot(1, 2, i + 1, projection="3d")
        for m, c, a in ms:
            draw_mesh(ax, m, c, a)
        ax.set_xlim(center[0] - zoom, center[0] + zoom)
        ax.set_ylim(center[1] - zoom, center[1] + zoom)
        ax.set_zlim(center[2] - zoom, center[2] + zoom)
        ax.set_box_aspect((1, 1, 1))
        ax.view_init(elev=elev, azim=azim)
        ax.set_title(ttl, fontsize=11)
    fig.suptitle("肘の化粧カバー確認 (elbow_shell, 元 Arm_Right_Elbow_Grey 半殻) — "
                 "灰カバーで肘サーボ箱 (upper_arm 側に固定) が隠れているか確認用",
                 fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(OUTPUT_DIR / "vis_elbow_detail.png", dpi=130, facecolor="white")
    plt.close(fig)
    print("saved vis_elbow_detail.png")


def hand_detail_still():
    """手 (claw_mount + 爪ハブ + 指 + 指先チップ) だけを拡大したクローズアップ。

    2026-07-29 固定爪化 (可動グリッパ廃止) の目視 QA 用: キットの三つ叉爪
    (Arm_Left_Claw_Grey + Arm_Left_Finger_Black_x3 + Arm_Left_FingerTip_Grey_x3)
    に見えるか、3MF 完成図サムネイル (Auxiliaries/.thumbnails/thumbnail_middle.png)
    のシルエットと目視照合できるよう、フルドレス配色の手先だけを専用ズームで
    2 アングル保存する (elbow_detail_still() と同じ構成)。
    """
    fig = plt.figure(figsize=(11, 5.5))
    body_h = 115.0
    ms = arm_meshes(1, ARM_READY, 0.0, body_h=body_h, dress=True)
    # 手の中心 = claw_mount 原点 (Tp = 肩ピッチ軸 Ty から肩ピッチ/肘/前腕の
    # チェーンを通した先, arm_meshes() 内部の Tp 定義をそのまま再現)
    ay, ap, ae, _grip = fw_arm_clamp(ARM_READY, body_h)
    PA = C.ARM_SERVO
    pz = 2.5 + PA["HORN_HUB_H"] - 2.0
    pitch_dn = pz + 2.5 + (PA["W"] / 2 + 2.5) - 0.1
    mx, my = C.ARM_MOUNT_XY
    T0 = trans(mx, my, 0.0 - 2.0)
    Ty = T0 @ rot(90 - C.ARM_MOUNT_YAW_DEG - ay, "z")
    Tu = Ty @ trans(20, 0, -pitch_dn) @ rot(ap, "y")
    Te = Tu @ trans(C.UPPER_ARM_LEN, 0, 0)
    Tf = Te @ rot(ae, "y")
    Tp = Tf @ trans(C.FOREARM_LEN, 0, 0)
    # 指先まで含めた実測 worst-case リーチ (ARM_HAND_REACH_MM-FOREARM_LEN)
    # の半分だけ +X へ寄せた点を中心にすると爪+指全体が収まる
    center = (Tp @ np.array(
        [(C.ARM_HAND_REACH_MM - C.FOREARM_LEN) / 2, 0, 0, 1]))[:3]
    zoom = 26.0  # 爪ハブ+指3本+指先チップの全体 (worst-case 半径 ~20mm) が入る余裕
    for i, (elev, azim, ttl) in enumerate(
            [(15, -30, "手クローズアップ (3/4)"), (8, -90, "手クローズアップ (真横)")]):
        ax = fig.add_subplot(1, 2, i + 1, projection="3d")
        for m, c, a in ms:
            draw_mesh(ax, m, c, a)
        ax.set_xlim(center[0] - zoom, center[0] + zoom)
        ax.set_ylim(center[1] - zoom, center[1] + zoom)
        ax.set_zlim(center[2] - zoom, center[2] + zoom)
        ax.set_box_aspect((1, 1, 1))
        ax.view_init(elev=elev, azim=azim)
        ax.set_title(ttl, fontsize=11)
    fig.suptitle("手のキット準拠固定爪確認 (claw_mount+Arm_Left_Claw_Grey+"
                 "Finger_Black×3+FingerTip_Grey×3, 2026-07-29 可動グリッパ廃止) — "
                 "三つ叉爪のシルエットが完成図サムネイルと一致するか目視用",
                 fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(OUTPUT_DIR / "vis_hand_detail.png", dpi=130, facecolor="white")
    plt.close(fig)
    print("saved vis_hand_detail.png")


# ---------------------------------------------------------------- シャーシ配置
def chassis_layout():
    fig, ax = plt.subplots(figsize=(9, 10))
    ch = load("chassis")
    # 上面ポリゴン投影 (簡易): 頂点散布の外形 + 部品footprint
    from matplotlib.collections import PolyCollection
    tri2 = ch.vertices[ch.faces][:, :, :2]
    ax.add_collection(PolyCollection(tri2, facecolor="#b9c4d4", edgecolor="none"))

    def fp(x, y, w, h, name, color):
        ax.add_patch(mpatches.FancyBboxPatch((x - w / 2, y - h / 2), w, h,
                     boxstyle="round,pad=1.5", fc=color, ec="#333", alpha=0.9))
        ax.text(x, y, name, ha="center", va="center", fontsize=9, weight="bold")

    fp(0, 1, 25.4, 62.5, "PCA9685 ×2\n(縦・スタック)", "#ffd48a")
    # ESP32: 2026-07-28 腕マウント移設で旧位置 (y=40) が腕サーボケース開口と
    # 実体衝突するため C.ESP32_Y0 (=-12.5) へ再配置済み (make_chassis.py 参照)
    fp(0, C.ESP32_Y0, 55, 28, "ESP32 旧位置\n(頭内不成立・要移設)", "#a8d8ff")
    fp(0, -6, 34, 80, "2S LiPo 2200\n(プレート下面\nクレードル)", "#c9f0c9")
    fp(30, -58, 30, 18, "UBEC 6V", "#f0b9b9")
    fp(-30, -58, 30, 18, "DC-DC 5V", "#f0b9b9")
    fp(-38, 40, 26, 16, "DFPlayer", "#e0c9f0")
    fp(48, 28, 18, 14, "SW+F", "#d8d8d8")
    fp(0, -80, 22, 34, "ポッドネック\n(M3×4)", "#b9d4f0")
    # 音声会話ユニット (2026-07 追加): 座標は shell_ghosts の Mouth_Cannon
    # 位置 (C.MOUTH_CANNON_T, 2026-07-29 実ソケット準拠に再導出済み) 付近
    # (docs/wiring.md 音声ユニット(I2S)配線 参照)。これはシャーシ電装レイアウト
    # の模式図 (上から見た概略ボックス) であり Mouth_Cannon の厳密な xy では
    # ない。ESP32 box (y範囲 C.ESP32_Y0±14 ≈ -26.5..1.5) より前方の y=64 に置き
    # 重なりを避ける
    fp(0, 64, 26, 14, "MIC+SPK\n(砲身)", "#ffcc66")
    fp(0, 30, 22, 12, "AMP\n(頭部側)", "#ffb3d1")
    # カメラ内蔵 (2026-07-28 設計変更: ポッドのメインアイではなく**頭部の
    # 中央目**へ移設。make_camera.py CAM2_* 参照)。eye_pod_camera/
    # camera_carrier は頭部シェル内側に固定されるシャーシ非搭載パーツ。
    # ここでは MIC+SPK/AMP と同様、実体の親コンポーネント (シャーシプレート
    # ではなく頭部) に関わらずおおよその xy 位置をイメージとして描画する
    # (中央目ソケットの robot 座標 y≈58, MIC+SPK/64 のやや手前・頭頂寄り)
    fp(0, 84, 22, 12, "CAM\n(中央目内蔵)", "#66eecc")
    for name, (x, y) in C.HIPS.items():
        ax.text(x, y, f"ヨー\n{name}", ha="center", va="center", fontsize=8,
                color="white", weight="bold",
                bbox=dict(boxstyle="circle", fc="#445", alpha=0.85))
    for s in (-1, 1):
        ax.text(s * C.ARM_MOUNT_XY[0], C.ARM_MOUNT_XY[1], "腕", ha="center",
                va="center", fontsize=8, color="white", weight="bold",
                bbox=dict(boxstyle="circle", fc="#286", alpha=0.85))
    ax.set_xlim(-100, 100); ax.set_ylim(-140, 105)  # ポッドネック(y=-80)〜CAM(y=84) を含む
    ax.set_aspect("equal"); ax.axis("off")
    ax.set_title("円形ハブ v3 電装レイアウト (上面図, +Y=前 / 脚 15°/165°/210°/"
                 "330° + 後方ポッドネック / LiPo は下面吊り / SPK はポッド内)",
                 fontsize=11)
    fig.tight_layout()
    fig.savefig(OUTPUT_DIR / "vis_chassis_layout.png", dpi=120, facecolor="white")
    plt.close(fig)
    print("saved vis_chassis_layout.png")


# ---------------------------------------------------------------- 腕 (共通)
def arm_meshes(side, pose, plate_bottom_z, swing=0.0, body_h=115.0, dress=False):
    """腕 1 本分のメッシュ (body 座標)。side=+1 右 / -1 左。

    pose = (ヨー, ピッチ, 肘, グリップ) — 右腕基準 deg。左は x=0 面でミラー
    (中立向きの ±ARM_MOUNT_YAW_DEG ミラーも x 反転で自動的に成立する)。
    swing は歩行時の肩ピッチ重畳 (右 +swing / 左 -swing, firmware と同位相差)。
    吊り下げマウント: シャーシ内 MICRO ヨーサーボ (軸下向き) のホーンが
    プレート下面の 4.8mm 下、ブラケット原点はプレート下面 -2.0、肩ピッチ軸は
    さらに 16.4mm 下 (make_arm.py の導出と一致)。中立 (ヨー0) の腕の向きは
    正面から C.ARM_MOUNT_YAW_DEG=40° の放射外向き (check_arm.py [1b] と同じ
    rot(90-MOUNT_YAW-yaw) 配置)。
    dress=True: elbow_shell (肘化粧カバー) + Arm_Right_Guard_Grey (肩シールド,
    kit_assembly.py 経由。右腕形状を全身共通の「常に右形状を作って左は末尾で
    鏡映」という既存の流儀に合わせてそのまま使う) + 指先チップ
    (Arm_Left_FingerTip_Grey_x3, グリップ系は左右共通) を追加する。
    """
    ay, ap, ae, grip = fw_arm_clamp(
        (pose[0], pose[1] + (swing if side > 0 else -swing), pose[2], pose[3]),
        body_h)
    PA = C.ARM_SERVO
    pz = 2.5 + PA["HORN_HUB_H"] - 2.0
    pitch_dn = pz + 2.5 + (PA["W"] / 2 + 2.5) - 0.1  # 16.4 (ヨー軸基準, 下向き)
    mx, my = C.ARM_MOUNT_XY
    T0 = trans(mx, my, plate_bottom_z - 2.0)     # ブラケット原点
    out = []
    Ty = T0 @ rot(90 - C.ARM_MOUNT_YAW_DEG - ay, "z")  # ヨー + = 外側 (右腕は -z 回転,
    #                                            中立が既に放射外向き 40°)
    br = load("shoulder_bracket"); br.apply_transform(Ty)
    out.append((br, COL["bracket"], 1.0))
    Tu = Ty @ trans(20, 0, -pitch_dn) @ rot(ap, "y")
    ua = load("upper_arm"); ua.apply_transform(Tu)
    out.append((ua, COL["skel_dress"] if dress else COL["uarm"], 1.0))
    for shell_name in ("arm_pod_upper", "arm_pod_lower"):
        sh = load(shell_name); sh.apply_transform(Tu)
        out.append((sh, COL["shell"], 1.0))
    if dress:
        # 肩ガード (kit_assembly: link:arm_pod, Tu と同じ「原点=肩ピッチ軸」
        # フレーム。arm_pod_upper/lower と同じ Tu を使い回す)
        for p in KIT.by_link(KIT_PLACEMENTS, "arm_pod"):
            if p.part != "Arm_Right_Guard_Grey":
                continue  # 左腕分は末尾の全身ミラーで作る (既存の流儀と同じ)
            g = KIT.oriented_mesh(p); g.apply_transform(Tu)
            out.append((g, p.color, 1.0))
    # 肘の化粧カバー (元 Arm_Right_Elbow_Grey の半殻, arm_shell.py elbow_shell)。
    # 肘サーボは upper_arm に固定 (肘の折り角 ae では回らない) — 原点は
    # forearm と同じ肘軸だが ae 回転は掛けない
    Te = Tu @ trans(C.UPPER_ARM_LEN, 0, 0)
    if dress:
        esh = load("elbow_shell"); esh.apply_transform(Te)
        out.append((esh, KIT.kit_color("Arm_Right_Elbow_Grey"), 1.0))
    Tf = Te @ rot(ae, "y")
    fa = load("forearm"); fa.apply_transform(Tf)
    out.append((fa, COL["skel_dress"] if dress else COL["farm"], 1.0))
    # 手 (2026-07-29 固定爪化): claw_mount (平坦円盤アダプタ) + 元キット爪ハブ
    # (Arm_Left_Claw_Grey, 両腕鏡映使用) + 指 3 本 (Arm_Left_Finger_Black_x3)
    # + 指先チップ 3 個 (Arm_Left_FingerTip_Grey_x3)。可動グリッパは廃止済み
    # (grip 値は fw_arm_clamp の戻り値互換のため受け取るが未使用)。
    # 爪ハブ/指/指先チップの配置は config.py CLAW_TO_MOUNT/FINGER_TO_MOUNT/
    # FINGERTIP_TO_MOUNT (3MF source_offset フォレンジクスによる決定的変換,
    # make_arm.py claw_mount() 参照) をそのまま使う — claw_mount 自身の骨格
    # フレーム Tp (原点 = forearm 手首面) に対して直接適用する
    Tp = Tf @ trans(C.FOREARM_LEN, 0, 0)
    cm = load("claw_mount"); cm.apply_transform(Tp)
    out.append((cm, COL["palm"], 1.0))
    claw = load("Arm_Left_Claw_Grey", source=MODEL)
    claw.apply_transform(Tp @ C.CLAW_TO_MOUNT)
    out.append((claw, KIT.kit_color("Arm_Left_Claw_Grey"), 1.0))
    for i in range(3):
        fg = load("Arm_Left_Finger_Black_x3", source=MODEL)
        fg.apply_transform(Tp @ C.FINGER_TO_MOUNT[i])
        out.append((fg, KIT.kit_color("Arm_Left_Finger_Black_x3"), 1.0))
        if dress:
            ft = load("Arm_Left_FingerTip_Grey_x3", source=MODEL)
            ft.apply_transform(Tp @ C.FINGERTIP_TO_MOUNT[i])
            out.append((ft, KIT.kit_color("Arm_Left_FingerTip_Grey_x3"), 1.0))
    if side < 0:
        Mx = np.diag([-1.0, 1.0, 1.0, 1.0])
        for m, _, _ in out:
            m.apply_transform(Mx)
            # trimesh.apply_transform 自体が反射時の面順を補正する。
    return out


# ---------------------------------------------------------------- 姿勢構築 (共通)
def robot_meshes(phase, vx, vy, wz, body_h, body_xyz=(0, 0, 0), body_yaw=0.0,
                 arms=ARM_TUCK, arm_swing=0.0, dress=False, *, holding=None):
    """1 フレーム分のメッシュ (world 座標)。body_xyz は体の world 位置。

    arms = (ヨー, ピッチ, 肘, グリップ) or None (腕なし)。既定は TUCK。
    dress=True: 意匠シェルを不透明・キット配色で描画するフルドレスモード
    (kit_assembly.py 経由。脚はガード+トゥ、腕は肩ガード+肘カバー+指先チップ
    ([arm_meshes] 側)、頭/砲身/ポッドは kit_dress_static() が担当)。
    holding=None は無移動指令を保持姿勢として描く。停止途中を描く際は
    holding=False を明示する。これは運動学表示で、物理接触・実サーボ追従を解かない。
    """
    if holding is None:
        holding = abs(vx) + abs(vy) + abs(wz) <= 0.05
    if holding:
        phase = 0.0  # Gaitの保持状態は全脚を接地させ、phase=0を使う。
    HIP_DROP = C.HIP_DROP
    zb = body_h + HIP_DROP   # プレート下面 (kit_assembly の z0 基準はここ)
    meshes = []
    Tb = trans(*body_xyz) @ rot(body_yaw, "z")
    ch = load("chassis")
    ch.apply_transform(Tb @ trans(0, 0, zb))
    meshes.append((ch, COL["chassis"], 1.0))
    # ポッド接続ネック梁 (プレート上面) とバッテリークレードル (下面)
    nk = load("pod_neck")
    nk.apply_transform(Tb @ trans(0, 0, zb + C.CHASSIS_T))
    meshes.append((nk, COL["chassis"], 1.0))
    cr = load("battery_cradle")
    cr.apply_transform(Tb @ trans(0, 0, zb))
    meshes.append((cr, COL["chassis"], 0.9))
    if arms is not None:
        for side in (1, -1):
            for m, c, a in arm_meshes(side, arms, zb, arm_swing, body_h, dress):
                m.apply_transform(Tb)
                meshes.append((m, c, a))
    if dress:
        # 頭/砲身/ポッド一式 (脚・腕・目以外の静的な意匠パーツ)
        for m, c, a in kit_dress_static(zb):
            m.apply_transform(Tb)
            meshes.append((m, c, a))
    for leg in range(4):
        lx, ly, lz = foot_target(leg, phase, vx, vy, wz, body_h, holding=holding)
        a = leg_ik(lx, ly, lz)
        if a is None:
            raise ValueError(f"描画姿勢のIK不成立: leg={LEG_NAMES[leg]}, target={(lx, ly, lz)}")
        yaw_d, pitch_d, knee_d = a
        mnt = np.degrees(MOUNT[leg])
        leg_name = LEG_NAMES[leg]             # "FR"/"FL"/"RL"/"RR"
        sfx = "_m" if leg in (0, 2) else ""   # v3: FR/RL はミラー版
        base = Tb @ trans(ORIGIN[leg][0], ORIGIN[leg][1], body_h) @ rot(mnt + yaw_d, "z")
        cox = load(f"coxa_bracket{sfx}"); cox.apply_transform(base)
        meshes.append((cox, COL["coxa"], 1.0))
        T_hip = base @ trans(C.COXA_LEN, 0, 0) @ rot(pitch_d, "y")
        fem = load(f"femur_link{sfx}"); fem.apply_transform(T_hip)
        meshes.append((fem, COL["skel_dress"] if dress else COL["femur"], 1.0))
        T_knee = T_hip @ trans(C.FEMUR_LEN, 0, 0) @ rot(knee_d, "y")
        tib = load(f"tibia_link{sfx}"); tib.apply_transform(T_knee)
        meshes.append((tib, COL["skel_dress"] if dress else COL["tibia"], 1.0))
        # 足 (2026-07-28 Leg_Foot 化): leg_foot_bored は元キット Leg_Foot の
        # 意匠加工版そのもの (機能パーツと意匠パーツを兼ねる) なので dress の
        # 有無に関わらず常時描画する (旧 foot_tip は装飾要素扱いで dress時のみ
        # だったが、実キット外観を持つ本パーツは骨格表示でも見せる方が実態
        # に近い)。原点 = T_knee@trans(0,0,-C.TIBIA_LEN) (tibia 先端, make_leg.py
        # leg_foot_bored() 自身のローカル座標系原点と一致)
        T_foot = T_knee @ trans(0, 0, -C.TIBIA_LEN)
        ft = load("leg_foot_bored"); ft.apply_transform(T_foot)
        meshes.append((ft, COL["foot"], 1.0))
        pad = load("foot_pad"); pad.apply_transform(T_foot)
        meshes.append((pad, COL["tip"], 1.0))
        # 脛シェル: 印刷向き (上端平面が z=0) → 機能向き (上端 z=-16)
        T_shin = T_knee @ trans(0, 0, -16) @ rot(180, "x")
        sh = load(f"shin_shell{sfx}")
        sh.apply_transform(T_shin)
        meshes.append((sh, COL["shell"], 0.95 if dress else 0.35))
        if dress:
            # thigh_cap: femur_link 上への搭載点は未確定 (kit_assembly_front.json
            # の既知の未解決事項) — 現物合わせ前提の推定配置。X は
            # exploded_leg() の図解オフセット (femur 側の可視化用オフセットを
            # 差し引いた相対値 FEMUR_LEN/2-8 ≈ femur 中央よりやや股寄り) を
            # 流用できるが、Z=55 は exploded_leg() が「分解図として見やすく
            # 縦に離す」ためだけの演出値 (femur ローカル座標の実位置ではない)
            # だったため、femur 箱枠の天面 (make_leg.py FRAME_TOP≈13.1) の
            # すぐ上に載る高さへ置き換える (精密な組付け位置ではない)
            T_thigh = T_hip @ trans(C.FEMUR_LEN / 2 - 8, 0, 13.1)
            tc = load("thigh_cap"); tc.apply_transform(T_thigh)
            meshes.append((tc, COL["cap"], 0.95))
            # ガード/トゥ (kit_assembly 経由。thigh_cap は全脚共通の非ミラー
            # メッシュ (build_all.py に thigh_cap_m は無い — 脚ごとの向きは
            # T_thigh の親側で処理済み) なのでそのまま使えるが、shin_shell は
            # FR/RL のみ shin_shell_m (Y ミラー, build_all.py 参照) を描画に
            # 使うため、ガードもそちらに合わせてミラーする必要がある (2026-07-28
            # レビュー major 指摘で発覚: 旧コードは非ミラーのガード行列を
            # そのまま流用しており、FR/RL の実際の embed% が 82.8%→26.7% まで
            # 悪化していた。下の shin_shell 分岐で修正、詳細はその場のコメント参照)
            for p in KIT.by_link(KIT_PLACEMENTS, "thigh_cap"):
                if p.instance != leg_name:
                    continue
                m = KIT.oriented_mesh(p); m.apply_transform(T_thigh)
                meshes.append((m, p.color, 0.95))
            for p in KIT.by_link(KIT_PLACEMENTS, "shin_shell"):
                if p.instance != leg_name or p.part in DRESS_SKIP_PARTS:
                    continue
                # kit_assembly_front.json の "link:shin_shell" は
                # shell_mod.py shin_shell() 自身の構築フレーム (原点=膝軸,
                # OFFSETS['shin_rotz'] 込みで既にメッシュへ焼き込み済み。
                # 2026-07-31 にループ内側化のため 90→270° へ変更, guard の
                # matrix も同じ Rz(180) を追従済み — shell_mod.py 参照) —
                # 印刷向きフリップ (T_shin の rot(180,"x")@trans(0,0,-16)
                # 部分) は掛けない。キャリアは T_knee のみ (T_shin ではない)
                m = KIT.oriented_mesh(p)
                if sfx == "_m":
                    # 2026-07-28 レビュー major 指摘の修正: FR/RL は
                    # shin_shell{sfx}.stl = shin_shell_m (build_all.py の
                    # .mirror([0,1,0]), 印刷向きへの rotate/translate の"後"に
                    # 適用) を描画するが、mirror([0,1,0]) は shin_shell() 自身の
                    # 構築チェーン (translate → rot(180,'x')) と可換なので
                    # (両者とも Y 成分にしか作用しない/Y と可換な軸回転)、
                    # shin_shell_m を T_shin と同じ「印刷向き解除」で運んだ結果は
                    # 「shin_shell() をこの link:shin_shell フレームで直接 Y
                    # ミラーしたもの」に厳密に一致する (scratchpad/
                    # verify_shin_guard_mirror.py で shin_shell_m.stl 実ファイル
                    # に対して数値確認済み)。ガードの配置 matrix は非ミラーの
                    # shin_shell() に対してフィットされているので、同じ Y ミラー
                    # を「matrix 適用後」に掛ければよい — ミラーは等長変換
                    # (距離を保存する) なので、embed% はミラー後もフィット時と
                    # 完全に同一になる (実測: FR/RL とも FL/RR と同じ 82.8%
                    # embed / median 1.585mm / max float 6.552mm に一致。修正前は
                    # 26.7% embed / max float 15.97mm まで悪化していた)
                    m.apply_transform(np.diag([1.0, -1.0, 1.0, 1.0]))
                m.apply_transform(T_knee)
                meshes.append((m, p.color, 0.95))
            for p in KIT.by_link(KIT_PLACEMENTS, "leg_foot_bored"):
                # Leg_Toe_Black_x12 は 1 脚あたり 3 本 (instance id "FR_0"/
                # "FR_1"/"FR_2" 等, kit_assembly_front.json 2026-07 改訂) —
                # 他の leg_foot_bored 系パーツが将来 1 脚 1 個のままでも動く
                # よう前方一致で判定する
                if not (p.instance == leg_name
                        or p.instance.startswith(leg_name + "_")):
                    continue
                m = KIT.oriented_mesh(p)
                m.apply_transform(T_foot)
                meshes.append((m, p.color, 0.95))
    return meshes


# ---------------------------------------------------------------- 組立ステップ 4 コマ
def assembly_steps():
    fig = plt.figure(figsize=(15, 12))
    HIP_DROP = C.HIP_DROP

    # step1: シャーシ + ヨーサーボ
    ax = fig.add_subplot(2, 2, 1, projection="3d")
    ch = load("chassis"); ch.apply_transform(trans(0, 0, BODY_H + HIP_DROP))
    pts = [ch.vertices]; draw_mesh(ax, ch, COL["chassis"])
    for leg in range(4):
        sv = servo_box()
        # ケースは全脚ともX平行。脚の放射角はホーン以降に適用する。
        sv.apply_transform(trans(ORIGIN[leg][0], ORIGIN[leg][1], BODY_H + HIP_DROP + 18))
        draw_mesh(ax, sv, COL["servo"]); pts.append(sv.vertices)
    frame_axes(ax, np.vstack(pts)); ax.view_init(18, -55)
    ax.set_title("STEP 1: ヨーサーボ×4 を上から挿入し台座ボスへ M3 タブ固定", fontsize=11)

    # step2: 骨格脚 4 本 (腕なし)
    ax = fig.add_subplot(2, 2, 2, projection="3d")
    ms = robot_meshes(0.1, 0, 0, 0, 115, arms=None)
    pts = []
    for m, c, a in ms:
        if c == COL["shell"]:
            continue
        draw_mesh(ax, m, c, a); pts.append(m.vertices)
    frame_axes(ax, np.vstack(pts), zfloor=0); ax.view_init(15, -55)
    ax.set_title("STEP 2: 脚骨格 (coxa→femur→tibia) をホーン片持ち結合 (M2.6 共締め)", fontsize=11)

    # step3: シェル被せ + 腕ユニット取付
    ax = fig.add_subplot(2, 2, 3, projection="3d")
    ms = robot_meshes(0.1, 0, 0, 0, 115, arms=ARM_READY)
    pts = []
    for m, c, a in ms:
        draw_mesh(ax, m, c, max(a, 0.55) if c == COL["shell"] else a)
        pts.append(m.vertices)
    frame_axes(ax, np.vstack(pts), zfloor=0); ax.view_init(15, -55)
    ax.set_title("STEP 3: shin_shell 固定 + thigh_cap 接着 + 肩ヨーサーボを頭部"
                 "ソケット直下 (正面±40°) へ上から挿入し腕を吊り下げ", fontsize=11)

    # step4: ボディシェル (イメージ)
    ax = fig.add_subplot(2, 2, 4, projection="3d")
    ms = robot_meshes(0.1, 0, 0, 0, BODY_H, arms=ARM_TUCK, dress=True)
    pts = []
    for m, c, a in ms:
        draw_mesh(ax, m, c, 0.9 if c != COL["shell"] else 0.5)
        pts.append(m.vertices)
    frame_axes(ax, np.vstack(pts), zfloor=0); ax.view_init(12, -55)
    ax.set_title("STEP 4: 電装配線 → ボディ/頭部シェルをタブへ固定 (配置イメージ)",
                 fontsize=11)

    fig.suptitle("組立ステップ (現行STL・150%意匠配置。組立経路/接地は別検査)", fontsize=14)
    fig.tight_layout()
    fig.savefig(OUTPUT_DIR / "vis_assembly_steps.png", dpi=110, facecolor="white")
    plt.close(fig)
    print("saved vis_assembly_steps.png")


# ---------------------------------------------------------------- 目デモ動画
def eyes_video(out=None, fps=15, dur=8.0):
    """頭部ドーム (半透明, ボア加工済み) + 眼球 (左右2, キョロキョロ) +
    中央カメラ目 (固定) のデモ。

    firmware/src/eyes.h と同じ考え方のサッカード列を再生する (左右のみ)。
    目の位置は STL 実測のソケット中心/法線 (config.EYE_SOCKETS_150)。
    index 1 (中央) は 2026-07-28 以降固定カメラ目 (eye_pod_camera) に
    置換済み — 回転せず、視線ドットも描かない (make_camera.CAM2_* 参照)。
    """
    out = out or (OUTPUT_DIR / "vis_eyes.mp4")
    rng = np.random.default_rng(7)
    dome = load("Head_Top_Eyecut")   # ボア加工済み (150% スケール済み)

    # 目位置 = STL 実測のソケット中心/法線 (config.EYE_SOCKETS_150)。
    # ポッド背面 (z=0) はリム面から 床-浮き+ネック だけ奥 (check_eye [5] と同じ)
    mounts = []
    SETBACK = (C.EYE_SOCKET_FLOOR - C.EYE_HOVER) + C.EYE_NECK_H
    for i, (ctr, n) in enumerate(C.EYE_SOCKETS_150):
        ctr, n = np.array(ctr), np.array(n)
        pos = ctr - n * SETBACK
        # 中央目 (i==1) は固定カメラなので取付位相が意味を持つ
        # (CAM.install_rotation) — 左右目自体の取付位相 (ドーム意匠の向き)
        # は任意だが、視線ドット (EYE_DOTS_150, 後述) の位相は写真と一致さ
        # せる必要がある (2026-07-30 反転監査で判明、EYE_DOT_ROLL_DEG 参照)
        A = np.eye(4)
        A[:3, :3] = CAM.install_rotation(n) if i == 1 else \
            trimesh.geometry.align_vectors([0, 0, 1], n)[:3, :3]
        mounts.append((pos, A))

    # 黒ドット (視線マーク) の描画用マーカー位置 = 元キャップの穴位置 (左右目のみ)
    DOTS = np.array(C.EYE_DOTS_150)
    # ロール補正 (視線ドットの位相を写真に合わせる) はモジュール定数
    # EYE_DOT_ROLL_DEG に一元化済み (kit_dress_static() と共有、ドリフト防止)。
    # 経緯・根拠はその定義コメント参照。

    # サッカード列 (eyes.h と同様: ランダム目標 + 高速スルー + 保持)。
    # 中央 (i==1) は固定なので生成しない
    n_f = int(dur * fps)
    ang = np.zeros((3, n_f))
    for i in (0, 2):
        t = 0
        cur = 0.0
        while t < n_f:
            tgt = rng.uniform(-80, 80)
            hold = int(rng.uniform(0.3, 1.6) * fps)
            for k in range(hold):
                if t >= n_f:
                    break
                cur += np.clip(tgt - cur, -500 / fps, 500 / fps)
                ang[i, t] = cur
                t += 1
    # 途中 1 回は左右目そろって同じ方向を見る (共視)
    sync = ang[0, int(n_f * 0.55)]
    ang[2, int(n_f * 0.55):int(n_f * 0.75)] = sync

    frames = []
    for f in range(n_f):
        fig = plt.figure(figsize=(8, 6))
        ax = fig.add_subplot(111, projection="3d")
        draw_mesh(ax, dome, "#3b62c4", 0.30)
        pts = [dome.vertices]
        for i, (pos, A) in enumerate(mounts):
            if i == 1:
                T = trans(*pos) @ A
                pod = load("eye_pod_camera")
                pod.apply_transform(T)
                draw_meshes(ax, [(pod, "#f4f3f0", 1.0)])
                pts.append(pod.vertices)
                continue
            T = trans(*pos) @ A @ rot(EYE_DOT_ROLL_DEG.get(i, 0.0) + float(ang[i, f]), "z")
            pod = load("eye_pod")
            pod.apply_transform(T)
            items = [(pod, "#f4f3f0", 1.0)]
            for dpos in DOTS:
                dot = trimesh.creation.icosphere(2, 1.9)
                dot.apply_transform(T @ trans(*dpos))
                items.append((dot, "#15181e", 1.0))
            draw_meshes(ax, items)
            pts.append(pod.vertices)
        allp = np.vstack(pts)
        c0 = allp.mean(0)
        r = 85
        ax.set_xlim(c0[0] - r, c0[0] + r)
        ax.set_ylim(c0[1] - r, c0[1] + r)
        ax.set_zlim(c0[2] - r * 0.7, c0[2] + r * 0.7)
        ax.set_box_aspect([1, 1, 0.7]); ax.axis("off")
        # 正面ビュー: 中央目が -Y (実測) なのでカメラは -Y 側。ソケット法線は
        # 仰角 ~47° で上向きのため、目線が見えるようやや上から見下ろす
        ax.view_init(elev=38, azim=-90)
        ax.text2D(0.5, 0.94, "目: 左右キョロキョロ (ランダムサッカード) + 中央固定カメラ",
                  transform=ax.transAxes, ha="center", fontsize=13, weight="bold")
        ax.text2D(0.5, 0.02,
                  "左右目は元キットの目パーツをサーボで自軸回転 (黒ドット群が軸から ~45° 偏心)\n"
                  "中央目は同じ形状のまま瞳を偏心開口した固定カメラ (make_camera.py CAM2_*)",
                  transform=ax.transAxes, ha="center", fontsize=8, color="#666")
        fig.tight_layout(pad=0.1)
        fig.canvas.draw()
        frames.append(np.asarray(fig.canvas.buffer_rgba())[:, :, :3].copy())
        plt.close(fig)
    imageio.mimsave(out, frames, fps=fps, codec="libx264", quality=7,
                    pixelformat="yuv420p")
    print(f"saved {out} ({len(frames)} frames)")


# ---------------------------------------------------------------- 動画
def walk_video(out=None, fps=15):
    """運動学デモ。体の移動は表示用で、接触・トルク・転倒を解かない。"""
    out = out or (OUTPUT_DIR / "vis_walk.mp4")
    frames = []
    import re
    fw = (ROOT / "firmware/src/config.h").read_text()
    CYCLE_T = float(re.search(r"CYCLE_T\s*=\s*([0-9.]+)f", fw).group(1))

    # シーン定義: (説明, 秒数, vx, vy, wz, body_h の関数, 腕ポーズの関数)
    def h_const(t):
        return 115.0

    def h_wave(t):
        return np.mean(BODY_H_RANGE) + np.ptp(BODY_H_RANGE) / 2 * np.sin(2 * np.pi * t / 4.0)

    def a_tuck(t):
        return ARM_TUCK

    def lerp(p, q, u):
        u = min(max(u, 0.0), 1.0)
        return tuple(a + (b - a) * u for a, b in zip(p, q))

    def a_seq(t):
        """腕デモ: 構え → リーチ → バイバイ → 構え (固定爪)"""
        if t < 1.2:
            return lerp(ARM_TUCK, ARM_READY, t / 1.2)
        if t < 2.4:
            return lerp(ARM_READY, ARM_REACH, (t - 1.2) / 1.2)
        if t < 3.2:                      # REACH 保持
            return ARM_REACH
        if t < 6.2:                      # wave (arms.h と同じ振り)
            wt = t - 3.2
            blend = min(wt / 0.4, 1.0)
            wave = (12.0 * np.sin(wt * 6.0), -10.0,
                    45.0 + 30.0 * np.sin(wt * 12.0), 0.0)
            return lerp(ARM_REACH, wave, blend)
        if t < 7.0:                      # リーチへ戻す
            return lerp((12.0 * np.sin(3.0 * 6.0), -10.0, 45.0, 0.0),
                        ARM_REACH, (t - 6.2) / 0.8)
        return lerp(ARM_REACH, ARM_READY, (t - 7.0) / 2.6)

    scenes = [
        ("前進 (クロール歩容)", 6.4, 0.0, 1.0, 0.0, h_const, a_tuck),
        ("その場旋回", 4.8, 0.0, 0.0, 1.0, h_const, a_tuck),
        ("横歩き (全方向移動)", 4.8, 1.0, 0.0, 0.0, h_const, a_tuck),
        ("体高変更 (しゃがみ/伸び)", 4.0, 0.0, 0.0, 0.0, h_wave, a_tuck),
        ("腕: 構え→リーチ→バイバイ→構え (固定爪)", 9.6, 0.0, 0.0, 0.0,
         h_const, a_seq),
    ]

    body = np.array([0.0, 0.0])
    body_yaw = 0.0
    phase = 0.0
    for label, dur, vx, vy, wz, hf, af in scenes:
        n = int(dur * fps)
        for i in range(n):
            t = i / fps
            dt = 1.0 / fps
            moving = (abs(vx) + abs(vy) + abs(wz)) > 0.05
            swing = 0.0
            if moving:
                phase = (phase + dt / CYCLE_T) % 1.0
                # 表示用の移動軌跡。物理シムの測定値ではない
                mnt_step = MAX_STEP
                dxy = np.array([vx, vy]) * mnt_step * dt / CYCLE_T
                cy, sy = np.cos(body_yaw * np.pi / 180), np.sin(body_yaw * np.pi / 180)
                body += np.array([dxy[0] * cy - dxy[1] * sy,
                                  dxy[0] * sy + dxy[1] * cy])
                body_yaw += wz * 12.0 * dt / CYCLE_T
                swing = 8.0 * np.sin(2 * np.pi * phase)  # firmware の歩行スイング
            bh = hf(t)

            fig = plt.figure(figsize=(8, 6))
            ax = fig.add_subplot(111, projection="3d")
            ms = robot_meshes(phase, vx, vy, wz, bh,
                              body_xyz=(body[0], body[1], 0), body_yaw=body_yaw,
                              arms=af(t), arm_swing=swing, dress=True)
            # フルドレスは近接メッシュ (ガード/greeble 等) が多く、
            # Poly3DCollection ごとの平均深度 zsort だと隠れ違いが起きる
            # ため 1 コレクションへ結合して描く (draw_meshes, proportions_still
            # と同じ手当て。描画呼び出し数が減るぶん動画レンダも軽くなる)
            draw_meshes(ax, ms)
            pts = [m.vertices for m, c, a in ms]
            # 床グリッド
            g = np.arange(-400, 401, 50)
            for gx in g:
                ax.plot([gx, gx], [g[0], g[-1]], [0, 0], color="#ccc", lw=0.5)
                ax.plot([g[0], g[-1]], [gx, gx], [0, 0], color="#ccc", lw=0.5)
            allpts = np.vstack(pts)
            c0 = allpts.mean(0)
            arm_scene = label.startswith("腕")
            r = 170 if arm_scene else 240
            cy = c0[1] + (45 if arm_scene else 0)  # 腕シーンは前方寄りに寄せる
            ax.set_xlim(c0[0] - r, c0[0] + r)
            ax.set_ylim(cy - r, cy + r)
            ax.set_zlim(0, 2 * r * 0.75)
            ax.set_box_aspect([1, 1, 0.75]); ax.axis("off")
            # 腕シーンは正面右斜め上から (前=+Y → azim 65 が前方右)
            ax.view_init(elev=14 if arm_scene else 18,
                         azim=65 if arm_scene else -55)
            ax.text2D(0.5, 0.94, "運動学デモ: " + label, transform=ax.transAxes, ha="center",
                      fontsize=13, weight="bold")
            ax.text2D(0.5, 0.02,
                      "腕: 頭部ソケット直下 (正面±40°) から吊り下げ・中立は放射外向き"
                      "・キット比率 / 意匠は元キットパーツ "
                      "(青ポッド/肘球/タロン指) / 肩ヨー±15°+ピッチ+肘+3指" if arm_scene else
                      "フルドレス表示 (キット配色, kit_assembly.py) / 全パーツ一律 150%",
                      transform=ax.transAxes, ha="center", fontsize=8, color="#666")
            fig.tight_layout(pad=0.1)
            fig.canvas.draw()
            buf = np.asarray(fig.canvas.buffer_rgba())[:, :, :3]
            frames.append(buf.copy())
            plt.close(fig)
        print(f"  scene done: {label} ({n}f)")

    imageio.mimsave(out, frames, fps=fps, codec="libx264", quality=7,
                    pixelformat="yuv420p")
    print(f"saved {out} ({len(frames)} frames)")


# ---------------------------------------------------------------- シェルゴースト
def shell_ghosts(zb, alpha=0.10):
    """意匠シェルの組立イメージゴースト (完成図 = 3MF thumbnail 準拠)。

    解剖 (完成図で確認済み): **前面の球体 = Head 一式が本体の顔** (目玉 3 つ,
    下にマウス砲)。**Cabin は背中に載る箱型ポッド** (前面にメインアイ+赤ランプ)。
    全パーツ一律 C.SCALE (150%) — 部分スケールはしない。配置は現物合わせ前提の
    イメージ。zb = シャーシプレート下面 z。戻り順: CabinF, CabinB,
    Head_Bottom, Head_Top, Mouth_Cannon。
    """
    # v3 (2026-07-28): ポッドは背中高位置ではなく **シャーシ後端のネック梁
    # (足と同じ高さ, y=-104 フランジ) に前面下部で接続**し、頭球体の後ろに
    # 低く座る (公式フィギュア/キット完成写真準拠)。Cabin_Front は従来
    # rot(-90,"x") で上下逆 (メインアイが上) だったのを修正
    # Head_Top の +57 (旧値) は 2026-07 に 3MF source_offset 実測で裏取りした
    # (tools/data/kit_assembly_front.json Head_Top_Eyecut の "finding" 参照):
    # Head_Bottom_Blue の自身の上端リム (raw z=8.461, r=41.46) と Head_Top_Blue
    # の下端リム (raw z=-32.009, r=41.78 — 全メッシュ中の最大半径、= 赤道面の
    # 開口) はほぼ同径で、球殻を上下に割った本当の合わせ目そのもの (Head_Plate_Grey
    # という薄いガスケット円板がここに挟まることも source_offset で裏付け済み)。
    # 両リムがちょうど重なる (面一) ための Head_Top オフセットを厳密計算すると
    # +57.7 — 旧値 +57 との差はわずか 0.7mm で、**「実測は42.3mmで60mmは
    # 17.7mm過大」という前セッションの finding は誤りだった** (Head_Top_Blue/
    # Head_Bottom_Blue の raw offset_z 差42.31mmからリム半径ぶんを引く際の
    # 計算ミスと判明 — 詳細は JSON 参照)。+57.7 へ微修正のみ行う
    #
    # Mouth_Cannon (2026-07-30 Mouth 物理チェーン復元タスクで再訂正、詳細は
    # hardware/src/config.py MOUTH_CANNON_T のコメント参照): 2026-07-29 の
    # CANNON_Y_COLLAR 基準 (露出7.2mmのみ, Cannon 単体の目視合わせ) は Ball/
    # Neck 自身の実測を一切使わない「reasoned な再調整」に過ぎなかった。
    # 今回、Mouth_Ball_Grey の最小二乗球フィット (R_BALL_150=12.557mm) と
    # Mouth_Neck_Blue 自身の実測全長 (NECK_AXIAL_LEN_150=16.092mm, 精密な
    # 球受けカップやペグは見つからず、この部品の唯一曖昧さのない長さ量として
    # 採用) を Ball 表面→Cannon 後端面のスタンドオフとして積み上げ直した。
    # 当初 (coincident 読み) は Cannon 後端 (CANNON_Y_REAR) がソケット面から
    # 軸方向 +16.092mm に出る配置を採用したが、**同日の QA 再検証で Ball∩Neck
    # の manifold3d 交差体積が Neck 自身の体積の 84.6% に達すること (ほぼ
    # 全体が Ball に埋没) が発覚**。唯一の非干渉配置 (tangent, +28.649mm) は
    # tools/check_arm.py [8b] のレイアウト意図 (両腕の間に見える) を -6.0mm
    # で破るため、standoff 1自由度だけでは両立しないトレードオフと判明した。
    # 妥協策として [8b] マージン+1.5mmを保てる範囲で tangent 側へ寄せた
    # standoff=+20.0mm (Cannon 後端がソケット面から軸方向+20.0mm、砲口は
    # +61.8mm) を採用 — Ball∩Neck を Neck 体積の 49.4% まで低減 (完全解消では
    # ない、詳細は config.py MOUTH_CANNON_REAR_STANDOFF_MM のコメント参照)。
    # **Cannon 全長 (41.78mm) は REAR から丸ごと頭部外**という配置は変わらない。
    # Head_Bottom シェルとの manifold3d boolean 交差体積は Cannon/Cap/Neck/
    # Ball いずれも 0.0mm3 (物理的に非干渉、standoff変更後も再実測済み)。
    # 0°/20°/35° の複数カメラ仰角でレンダ確認: 0°では青い Neck (このコーン
    # 部分, 一部は依然 Ball に埋没) とグレーの Cannon が顎から明確な間隔を
    # 持って垂下し (旧 standoff=0 案のように頭部前面に直接貼り付いた円盤には
    # 見えない)、完成写真に近い仰角20°では60.4°の下向き傾斜による遠近圧縮で
    # Cannon がほぼ頭部シルエットの下に隠れ、顎下の小さなグレー突起として
    # 覗く程度になる — 定性的に thumbnail_middle.png と整合 (ピクセル単位の
    # 厳密照合はしていない、UNVERIFIED)。
    # Mouth_Neck_Blue はこの結果 DRESS_SKIP_PARTS から除去し青で描画 (完成写真の
    # 「青いコーン」に相当するが、上記の通り体積の約半分はなお Ball に埋没した
    # ままである点に留意)。Mouth_Ball_Grey は Neck の flange 面より Cannon から
    # 離れた位置にあり外向きの半球が開放空間に露出するため、こちらも
    # DRESS_SKIP_PARTS から除去済み (下記 DRESS_SKIP_PARTS 定義のコメント参照)。
    # C.MOUTH_CANNON_T/ROT_X_DEG は tools/check_arm.py [8] とも共有
    # (ドリフト防止のため config.py に一元化。kit_dress_static() が実際には
    # config.py を読まず kit_assembly_front.json の凍結値で Cannon 自身を
    # 描いていた別ドリフト不具合は 2026-07-29 に発覚・修正済み — 下記
    # kit_dress_static() 参照)
    #
    # Cabin_Back の上下反転修正 (2026-07-30, ユーザー指摘): 旧 rot(90,"x") は
    # 上下逆だった。証拠 (非対称フィーチャーで判定 — シーム一致はどちらの
    # 向きでも 0.08mm/0cm3 で成立するため反転を区別できない、
    # tools/data/kit_assembly_rear.json meta.methodology_caveats_2026_07_30
    # 参照): (1) 生メッシュの断面プロファイル (local Y に沿った X/Z 断面幅)
    # で Cabin_Front_Blue (向き確定済み) は local +Y 側が先細り (ドーム頂部,
    # 写真のノブ)・local -Y 側が幅広 (裾スカート) という非対称パターンを示す。
    # Cabin_Back_Blue_Repaired も同じ形の非対称 (local -Y 側が先細り [xspan
    # 52.1mm/zspan 14.3mm], local +Y 側が幅広 [xspan 79.7mm/zspan 26.9mm]) だが、
    # 旧回転は local +Y を world +Z (上) に写像していたため **幅広い側が上,
    # 先細り側が下になり Front と逆の上下パターン**になっていた。
    # (2) Back に取り付く RedLight_Large/Small・Spinnarette の裏取り済み
    # ローカル取付リング位置を旧回転で world 化すると、Front 側の対応ペア
    # (RedLight front Z≈+110, Spinnarette front Z≈-28) と大きく食い違う
    # (RedLight back 旧 Z≈-2, Spinnarette back 旧 Z≈+142 — 上下が入れ替わって
    # いる)。新回転 rot(180,"y")@rot(90,"x") (= 旧回転に world Y 軸まわり
    # 180°を追加 — 上下+左右反転, 前後 (outward_normal ≈ -Y, 後方向き) は
    # 保持) で world 化すると RedLight back Z≈+111〜+112, Spinnarette back
    # Z≈-32 となり Front 側と数 mm 差で一致する。座標中心 (bbox center) は
    # 元々 local 軸ごとに対称なため、この回転変更で t=(0,-235,zb+55) や
    # Front/Back の旧シーム説明。2026-09-05にBackの高さを-6.3075mm訂正した。
    # X 符号も反転するため、Back 側の RedLight/Spinnarette 各ペアは
    # left/right ラベルを入れ替えた (JSON 側で対応済み)。
    defs = [
        (name, KIT.cabin_transform(name), (0, 0, zb)) for name in C.CABIN_POSES
    ] + [
        ("Head_Bottom_Blue", rot(180, "z"), (0, C.ARM_MOUNT_HUB_Y, zb - 3)),
        ("Head_Top_Blue", rot(180, "z"), (0, C.ARM_MOUNT_HUB_Y, zb + HEAD_TOP_Z_OFFSET)),
        ("Mouth_Cannon_Grey", rot(C.MOUTH_CANNON_ROT_X_DEG, "x"),
         (C.MOUTH_CANNON_T[0], C.MOUTH_CANNON_T[1], zb + C.MOUTH_CANNON_T[2])),
    ]
    out = []
    for name, R, t in defs:
        # Head_Bottom_Blue → Head_Bottom_Armcut (2026-07-30, KIT.STL_RENDER_
        # OVERRIDE と同じ差し替え。hardware/stl/ の加工版は既に bbox 中心化
        # ×SCALE 済み [KIT.PRESCALED] のため、ここだけ元キット STL 用の
        # bbox中心化+スケールを飛ばして直接読む — 二重スケール防止)
        m = KIT.normalized_mesh("Head_Top_Eyecut" if name == "Head_Top_Blue" else name)
        m.apply_transform(R)
        m.apply_translation(t)
        out.append((m, COL["shell"], alpha))
    return out


# ------------------------------------------------------------- フルドレス (共通)
def pod_dress_shells(zb, alpha=0.95):
    """ポッド本体 (Cabin_Front/Back) をキット配色・不透明で。

    座標式は shell_ghosts() の Cabin 2 定義と同一 (shell_ghosts 自体は
    ghost/wiring 用の半透明・単色描画を維持するため変更しない — 値だけ
    ここに複製している。値を変えるときは両方直すこと)。
    Cabin_Back の回転は 2026-07-30 に上下反転を修正済み (詳細は
    shell_ghosts() のコメント参照)。
    """
    defs = [
        (name, KIT.cabin_transform(name), (0, 0, zb)) for name in C.CABIN_POSES
    ]
    out = []
    for name, R, t in defs:
        m = trimesh.load(MODEL / f"{name}.stl")
        m.apply_translation(-(m.bounds[0] + m.bounds[1]) / 2)
        m.apply_scale(C.SCALE)
        m.apply_transform(R)
        m.apply_translation(t)
        out.append((m, KIT.kit_color(name), alpha))
    return out


def kit_dress_static(zb, alpha=0.95):
    """脚/腕/目の可動部を除く、静的な意匠パーツ一式 (頭・砲身内部・ポッド外装
    +トリム, kit_assembly.py 経由)。body 座標 (Tb 適用前)、z はプレート下面
    zb 基準。脚 (thigh_cap/shin_shell/leg_foot_bored) と腕 (arm_pod/claw_mount) の
    付属パーツはポーズ依存のため robot_meshes()/arm_meshes() 側で個別処理する
    (ここでは扱わない)。
    """
    items = pod_dress_shells(zb, alpha)
    # Head_Bottom_Blue / Head_Top_Blue / Mouth_Cannon_Grey 自身の確立済み配置
    # (shell_ghosts() の R,t と同じ式)。frame="link:X" パーツはこれを後段
    # 適用してロボット座標へ運ぶ
    T_head_bottom = trans(0, C.ARM_MOUNT_HUB_Y, zb - 3) @ rot(180, "z")
    T_head_top = trans(0, C.ARM_MOUNT_HUB_Y, zb + HEAD_TOP_Z_OFFSET) @ rot(180, "z")   # shell_ghosts() と同一定数 HEAD_TOP_Z_OFFSET 参照
    T_mouth = (trans(C.MOUTH_CANNON_T[0], C.MOUTH_CANNON_T[1], zb + C.MOUTH_CANNON_T[2])
               @ rot(C.MOUTH_CANNON_ROT_X_DEG, "x"))   # shell_ghosts() と同一定数 (config.py MOUTH_CANNON_*) 参照
    LINK_T = {"Head_Bottom_Blue": T_head_bottom, "Head_Top_Blue": T_head_top,
             "Mouth_Cannon_Grey": T_mouth}
    # Mouth_Cannon_Grey 自身は KIT_PLACEMENTS (kit_assembly_front.json の凍結
    # スナップショット p.t/p.R, frame="robot") 経由で描くと、上で config.py から
    # 毎回計算している T_mouth が (Mouth_Cap/Key/Peg など子パーツの配置にしか
    # 使われず) 完全に無視される — config.py の MOUTH_CANNON_T/ROT_X_DEG を
    # 変更しても Cannon 本体の描画位置が一切動かないドリフト経路になっていた
    # (2026-07-29 QA major 指摘で発覚。「shell_ghosts()/kit_dress_static() が
    # config.py を共通で参照しドリフト防止」という従来コメントは kit_dress_static
    # に関して誤りだった)。ここで明示的に T_mouth で描き、下のループでは
    # JSON 側の値を使わないよう Mouth_Cannon_Grey を除外する
    _m = KIT.normalized_mesh("Mouth_Cannon_Grey")
    _m.apply_transform(T_mouth)
    items.append((_m, KIT.kit_color("Mouth_Cannon_Grey"), alpha))
    for p in KIT_PLACEMENTS:
        if p.unresolved:
            continue   # 位置情報なし (Cabin_Peg_x2 等) — 描画不可
        if p.part in DRESS_SKIP_PARTS:
            continue   # 既知のジオメトリ不整合 (浮遊パーツ) — QA major 指摘
        if p.part == "Mouth_Cannon_Grey":
            continue   # 上で T_mouth (config.py 直読み) から明示描画済み
        if p.frame == "robot":
            m = KIT.oriented_mesh(p)
            m.apply_translation([0, 0, zb])
            items.append((m, p.color, alpha))
        elif p.link in LINK_T:
            m = KIT.oriented_mesh(p)
            m.apply_transform(LINK_T[p.link])
            items.append((m, p.color, alpha))
        # link が thigh_cap/shin_shell/leg_foot_bored/arm_pod/claw_mount/eye_pod の
        # ものはここでは扱わない (呼び出し元を参照)
    # 目 (中立姿勢, サーボ角0): eye_pod は Head_Eye_White_x3 の実測形状その
    # ものを内蔵しているため (make_eye.py eye_pod())、kit_assembly が返す
    # Head_Eye_White_x3 の配置は使わない (二重描画を避ける)。
    # index 1 (中央) は 2026-07-28 以降固定カメラ目 (eye_pod_camera) —
    # 取付位相は CAM.install_rotation() (config.CAM2_THETA_DEG が水平前方へ
    # 相殺する向き。tools/check_camera.py [1] で検算)
    SETBACK = (C.EYE_SOCKET_FLOOR - C.EYE_HOVER) + C.EYE_NECK_H
    for i, (ctr, n) in enumerate(C.EYE_SOCKETS_150):
        ctr, n = np.array(ctr), np.array(n)
        pos = ctr - n * SETBACK
        if i == 1:
            A = np.eye(4)
            A[:3, :3] = CAM.install_rotation(n)
            pod = load("eye_pod_camera")
        else:
            # ロール補正 (EYE_DOT_ROLL_DEG): eye_pod は Head_Eye_White_x3 の
            # 実測形状 (視線ドット3穴を含む) をそのまま流用しているため
            # (make_eye.py CAP_NORM), align_vectors() 直後の無補正ロールは
            # eyes_video() と同じ理由でここでも誤り (2026-07-30 反転監査)
            A = trimesh.geometry.align_vectors([0, 0, 1], n) \
                @ rot(EYE_DOT_ROLL_DEG.get(i, 0.0), "z")
            pod = load("eye_pod")
        pod.apply_transform(T_head_top @ trans(*pos) @ A)
        items.append((pod, "#f4f3f0", 1.0))
    return items


def proportions_still():
    """意匠シェル (フルドレス, キット配色) を重ねた比率確認。"""
    body_h = 115.0
    zb = body_h + C.HIP_DROP
    ms = robot_meshes(0.0, 0.0, 0.0, 0.0, body_h, arms=ARM_READY, dress=True)
    meas = shell_ghosts(zb, alpha=0.0)   # 寸法計測専用 (描画には使わない)
    head_w = float((meas[3][0].bounds[1] - meas[3][0].bounds[0])[0])
    pod_w = float((meas[0][0].bounds[1] - meas[0][0].bounds[0])[0])
    fig = plt.figure(figsize=(15, 6))
    for i, (elev, azim, ttl) in enumerate(
            [(8, 0, "側面"), (16, 115, "前方 3/4"), (4, 90, "正面")]):
        ax = fig.add_subplot(1, 3, i + 1, projection="3d")
        draw_meshes(ax, ms)
        allp = np.vstack([m.vertices for m, _, _ in ms])
        frame_axes(ax, allp, zfloor=0)
        ax.view_init(elev=elev, azim=azim)
        ax.set_title(ttl, fontsize=10)
    fig.suptitle("比率確認 v3 (フルドレス): 実測放射配置 (前脚±75°/後脚±120°) + "
                 "ポッドは後方ネック (足と同じ高さ) に接続 — 全パーツ一律 150%",
                 fontsize=13)
    fig.text(0.5, 0.10,
             f"顔球体 φ{head_w:.0f}mm = 元 Head_Top ×1.5 / ポッド幅 "
             f"{pod_w:.0f}mm = 元 Cabin ×1.5 (比率はキットのまま・部分スケール"
             "なし) / 脚角度は公式フィギュア下面写真の実測値 / 意匠はキット"
             "配色フルドレス (kit_assembly.py, 3MF forensics 準拠)",
             ha="center", fontsize=9, color="#555")
    # ポッド (Cabin) に含まれる部位名の注記 (QA minor 指摘: タレット/
    # スピナレット等がラベル無しでは画像だけから判別できない -> 引き出し線
    # までは付けず、少なくとも「このレンダに何が含まれているか」を
    # kit_assembly_rear.json の実データから機械的に一覧できるようにする)
    import textwrap
    cabin_parts = sorted({p.part.removeprefix("Cabin_") for p in KIT_PLACEMENTS
                          if p.source == "rear" and p.part.startswith("Cabin_")
                          and not p.unresolved})
    if cabin_parts:
        wrapped = textwrap.wrap("ポッド収録パーツ: " + " / ".join(cabin_parts),
                                width=140)
        fig.text(0.5, 0.005, "\n".join(wrapped),
                 ha="center", va="bottom", fontsize=6.5, color="#888")
    fig.tight_layout(rect=(0, 0.16, 1, 0.94))
    fig.savefig(OUTPUT_DIR / "vis_proportions.png", dpi=110,
                facecolor="white")
    plt.close(fig)
    print("saved vis_proportions.png")


# ---------------------------------------------------------------- 配線イメージ動画
def _tp(T, v):
    return (T @ np.array([v[0], v[1], v[2], 1.0]))[:3]


def _leg_wire_route(leg, body_h, plate_top, board0):
    """脚 1 本の 3 線バンドル経路: 膝箱→femur ウェブ→coxa→配線穴→board0。"""
    lx, ly, lz = foot_target(leg, 0.0, 0.0, 0.0, 0.0, body_h, holding=True)
    a = leg_ik(lx, ly, lz)
    if a is None:
        raise ValueError(f"配線表示のIK不成立: leg={leg}, target={(lx, ly, lz)}")
    yaw_d, pitch_d, _ = a
    ox, oy = ORIGIN[leg][0], ORIGIN[leg][1]
    mnt = np.degrees(MOUNT[leg])
    base = trans(ox, oy, body_h) @ rot(mnt + yaw_d, "z")
    T_hip = base @ trans(C.COXA_LEN, 0, 0) @ rot(pitch_d, "y")
    pts = [
        _tp(T_hip, (C.FEMUR_LEN - 10, 8, 9)),   # 膝サーボ箱
        _tp(T_hip, (C.FEMUR_LEN / 2, 8, 9)),    # femur ウェブ沿い
        _tp(T_hip, (8, 8, 8)),                  # 股ピッチ
        _tp(base, (C.COXA_LEN * 0.4, 0, 20)),   # coxa 配線逃がし
        np.array([ox * 0.82, oy * 0.82, body_h + C.HIP_DROP]),  # シャーシ配線穴
        np.array([ox * 0.62, oy * 0.62, plate_top + 6]),
        board0,
    ]
    return np.array(pts)


def _arm_wire_route(side, pose, plate_bot, plate_top, board1, body_h):
    """腕 1 本のバンドル経路: 肘→上腕→肩ブラケット→MICRO 開口→board1。

    2026-07-29 固定爪化: 可動グリッパ (サブマイクロ+配線) を廃止したため、
    経路の起点は前腕先端 (palm) ではなく肘サーボ (最も末端の電装) になる —
    forearm/claw_mount/爪ハブ+指は電装を持たない受動パーツ。
    """
    ay, ap, ae, _ = fw_arm_clamp(pose, body_h)
    PA = C.ARM_SERVO
    pz = 2.5 + PA["HORN_HUB_H"] - 2.0
    pitch_dn = pz + 2.5 + (PA["W"] / 2 + 2.5) - 0.1
    mx, my = C.ARM_MOUNT_XY
    T0 = trans(mx, my, plate_bot - 2.0)
    Ty = T0 @ rot(90 - C.ARM_MOUNT_YAW_DEG - ay, "z")  # 中立=放射外向き (arm_meshes と同じ)
    Tu = Ty @ trans(20, 0, -pitch_dn) @ rot(ap, "y")
    pts = [
        _tp(Tu, (C.UPPER_ARM_LEN, 0, -9)),      # 肘サーボ (経路の末端電装)
        _tp(Tu, (12, 0, -8)),                   # フレーム内側
        _tp(Ty, (20, 0, -4)),                   # 肩ブラケット背面
        _tp(Ty, (4, 0, 2)),                     # MICRO 開口 (ヨーサーボ脇)
        np.array([mx, my, plate_top + 5]),
        np.array([mx * 0.5, my * 0.5, plate_top + 8]),
        board1,
    ]
    p = np.array(pts)
    if side < 0:
        p[:, 0] *= -1
    return p


def wiring_video(out=None, fps=15):
    """配線イメージ: 電源系統 → 脚 12ch → 腕+目 → ロジック/LED → 全系統。

    経路・チャンネルは docs/wiring.md と一致。電装ボックス位置と頭部/LED の
    取付点はイメージ (組立時に現物合わせ)。
    """
    out = out or (OUTPUT_DIR / "vis_wiring.mp4")
    body_h = 115.0
    zb = body_h + C.HIP_DROP                 # プレート下面
    zt = zb + C.CHASSIS_T                    # プレート上面
    board0 = np.array([0.0, 1.0, zt + 10])    # PCA9685 0x40 (脚+頭, 中央縦置き)
    board1 = np.array([0.0, 1.0, zt + 22])    # PCA9685 0x41 (腕+目, スタック)

    # --- 静的メッシュ (立位 + READY 腕 + シェルゴースト)
    # 頭 (前面の顔球体) は完成図準拠の組立位置 = shell_ghosts の Head_Top と
    # 同位置。外観シーンには Cabin (背中ポッド) 等のゴーストも重ねる
    meshes = robot_meshes(0.0, 0.0, 0.0, 0.0, body_h, arms=ARM_READY)
    head_c = np.array([0.0, C.ARM_MOUNT_HUB_Y, zb + HEAD_TOP_Z_OFFSET])   # shell_ghosts() と同一定数 HEAD_TOP_Z_OFFSET 参照
    Th = trans(*head_c) @ rot(180, "z")
    head = load("Head_Top_Eyecut")
    head.apply_transform(Th)
    bg = shell_ghosts(zb, alpha=0.055)
    body_ghosts = bg[:3] + bg[4:]   # CabinF/B + Head_Bottom + Mouth (Top は Eyecut で描画)
    sockets = [_tp(Th, s) for s in
               ((45.81, 0, -20.42), (0, -45.81, -20.42), (-45.81, 0, -20.42))]

    # カメラ内蔵 (2026-07-28 設計変更, hardware/src/make_camera.py): ポッドの
    # メインアイではなく**頭部の中央目**へ移設 (eye_pod_camera +
    # camera_carrier)。位置は中央目ソケットそのもの (sockets[1], 上記 eye
    # 描画と同じ Th 変換) を法線の逆方向へ少し (モジュール標準距離相当)
    # 押し込んだ近似値
    cam_ctr = tuple(sockets[1] - (Th[:3, :3] @ np.array(C.EYE_SOCKETS_150[1][1])) * 20)

    # --- 電装ボックス (name, size, center, color)
    boxes = [
        ("2S LiPo (下面)", (34, 105, 24), (0, -6, zb - 16), "#7ec87e"),
        # 2026-07-28: 腕マウント移設に伴い C.ESP32_Y0 (=-12.5) へ再配置済み
        ("ESP32 旧位置/要移設", (55, 28, 10), (0, C.ESP32_Y0, zt + 5), "#7db8e8"),
        ("PCA9685 0x40", (25.4, 62.5, 8), (0, 1, zt + 4), "#e8b860"),
        ("PCA9685 0x41", (25.4, 62.5, 8), (0, 1, zt + 16), "#e8b860"),
        ("UBEC 6V/10A", (30, 18, 12), (30, -58, zt + 6), "#e88888"),
        ("DC-DC 5V/3A", (30, 18, 12), (-30, -58, zt + 6), "#e88888"),
        ("SW+ヒューズ", (18, 14, 10), (48, 28, zt + 5), "#c0c0c0"),
        ("DFPlayer", (26, 16, 8), (-38, 40, zt + 4), "#c9a8e8"),
        ("SPK(ポッド内)", (24, 24, 12), (25, -150, zb + 20), "#c9a8e8"),
        # 音声会話ユニット (2026-07 追加, docs/voice.md): shell_ghosts の
        # Mouth_Cannon 位置 (C.MOUTH_CANNON_T, 2026-07-29 実ソケット準拠に
        # 再導出済み) 付近。MAX98357A は頭部/シャーシ側搭載のため Head_Bottom
        # 寄り (y を控えめに) に置く。以下のボックス配置はあくまで配線イメージ
        # (簡略な直方体マーカー) であり Mouth_Cannon の厳密な位置/傾きは
        # 反映していない
        ("MIC(砲身内)", (8, 10, 6), (0, 58, zb - 8), "#66ccff"),
        ("SPK(砲身)", (18, 18, 10), (0, 50, zb - 16), "#ffaa44"),
        ("AMP(頭部側)", (16, 12, 8), (0, 30, zb + 8), "#ff6699"),
        ("CAM(中央目内蔵)", (21, 13, 6), cam_ctr, "#66eecc"),
    ]
    box_meshes = []
    for name, size, ctr, col in boxes:
        b = trimesh.creation.box(size)
        b.apply_translation(ctr)
        box_meshes.append((name, b, col, np.array(ctr)))

    # --- 配線グループ {group: [(pts, color, lw), ...]}
    wires = {"power": [], "leg": [], "arm": [], "logic": []}
    W = wires["power"]
    # バッテリー (下面クレードル) → 配線穴 (16,-6) → SW+ヒューズ → UBEC/DC-DC
    W.append((np.array([(0, -6, zb - 8), (16, -6, zb - 2),
                        (16, -6, zt + 4), (48, 28, zt + 7)]), "#cc2222", 3.0))
    W.append((np.array([(48, 28, zt + 7), (58, -20, zt + 6),
                        (30, -58, zt + 9)]), "#cc2222", 3.0))
    W.append((np.array([(30, -58, zt + 9), (36, -45, zt + 3)]), "#cc2222", 3.0))
    W.append((np.array([(-55, -45, zt + 3), (55, -45, zt + 3)]), "#881111", 4.5))
    for leg in range(4):
        ox, oy = ORIGIN[leg][0], ORIGIN[leg][1]
        s = np.sign(ox)
        if oy < 0:
            W.append((np.array([(ox, -45, zt + 3), (ox, oy, zt + 2)]),
                      "#aa3333", 2.2))
        else:
            W.append((np.array([(s * 55, -45, zt + 3), (s * 60, 0, zt + 3),
                                (ox, oy, zt + 2)]), "#aa3333", 2.2))
    for s in (1, -1):
        mx, my = C.ARM_MOUNT_XY
        W.append((np.array([(s * 60, 0, zt + 3), (s * 45, 50, zt + 3),
                            (s * mx, my, zt + 2)]), "#aa3333", 2.2))
    W.append((np.array([(48, 28, zt + 7), (-16, -6, zt + 4),
                        (-30, -58, zt + 9)]), "#dd6622", 2.4))
    W.append((np.array([(-30, -58, zt + 9), (-40, -10, zt + 6),
                        (0, 40, zt + 8)]), "#dd6622", 2.4))
    # カメラ (2026-07-28 頭部の中央目へ移設): 独立WiFiモジュールなのでデータ
    # 線はなし (WiFi 経由で tools/voice_bridge.py --camera-url へ)、5V 電源
    # のみ DC-DC から首の回転部を避けて頭部内へ引き上げる
    W.append((np.array([(-30, -58, zt + 9), (-20, 0, zt + 20),
                        (0, 20, zb + 60), cam_ctr]), "#66eecc", 1.6))

    for leg in range(4):
        wires["leg"].append((_leg_wire_route(leg, body_h, zt, board0),
                             "#e0a020", 1.8))

    for s in (1, -1):
        wires["arm"].append((_arm_wire_route(s, ARM_READY, zb, zt, board1,
                                             body_h), "#00a0a8", 2.0))
    for sk in (sockets[0], sockets[2]):
        wires["arm"].append((np.array([sk, (sk[0] * 0.6, 20, zb + 20),
                                       (sk[0] * 0.25, -10, zt + 26),
                                       board1]), "#cc44aa", 1.5))

    L = wires["logic"]
    L.append((np.array([(0, 40, zt + 9), (6, 24, zt + 16),
                        board1 + (4, 8, 2)]), "#2255dd", 1.8))     # I2C
    L.append((np.array([(16, -6, zt + 4), (10, 20, zt + 7),
                        (0, 40, zt + 8)]), "#889090", 1.2))        # 電圧分圧
    L.append((np.array([(0, 40, zt + 8), (-20, 44, zt + 6),
                        (-38, 40, zt + 6)]), "#8844cc", 1.5))      # UART
    # SPK 線: ネック梁上面に沿わせてポッドへ
    L.append((np.array([(-38, 40, zt + 7), (-12, -40, zt + 10),
                        (0, -80, zt + 14), (10, -120, zb + 18),
                        (25, -150, zb + 20)]), "#8844cc", 2.0))
    # WS2812: メインアイ (ポッド前面下部, v3 低位置) → 頭部目×3 → 赤ランプ
    # (ポッド上部前後)
    lamps = [(30, -125, zb + 120), (-30, -125, zb + 120),
             (28, -215, zb + 125), (-28, -215, zb + 125)]
    chain = [np.array([0, 40, zt + 9]), np.array([0, -80, zt + 14]),
             np.array([0, -120, zb + 17])] + \
        [np.array(sockets[0]), np.array(sockets[1]), np.array(sockets[2])] + \
        [np.array(p) for p in lamps]
    L.append((np.vstack(chain), "#22aa33", 1.8))                   # WS2812

    scenes = [
        ("電源系統: 2S LiPo → SW+ヒューズ → UBEC 6V/10A → サーボバス直結",
         "power", 6.0, 55, -100, -30,
         "サーボ電源は PCA9685 の V+ を経由しない (AWG16 バスへ直結) / "
         "5V 3A は別系統で ESP32・LED・サウンドへ"),
        ("脚サーボ 12ch: 3線バンドル → シャーシ配線穴 → PCA9685 board0 (0x40)",
         "leg", 6.0, 24, -60, 20,
         "膝→股→coxa の順に沿わせ可動域全域で張らない長さを確保 / "
         "電源線はサーボ直近でバスへ分岐し信号+GND のみ PCA へ"),
        ("腕 6ch (固定爪化で ch19/23 未使用) + 目 2ch → PCA9685 board1 "
         "(0x41, A0 ジャンパ)",
         "arm", 6.0, 16, 115, 55,
         "肘→肩ブラケット背面→MICRO 開口から内部へ (forearm/claw_mount/爪+指"
         "は電装なしの受動パーツ) / 左右目の線はタブ間隙間から胴へ"
         " (頭部固定・中央目はカメラ)"),
        ("ロジック: I2C ×2枚 / WS2812 チェーン / DFPlayer+SPK / 電圧監視",
         "logic", 6.0, 38, -20, -95,
         "WS2812 順: メインアイ→頭部目×3→赤ランプ / GPIO4→74AHCT125→DIN / "
         "電池分圧→GPIO34 (6.4V×3秒で自動脱力)"),
        ("全系統 (配線経路まとめ)", "all", 5.0, 30, -55, 25,
         "経路とチャンネルは docs/wiring.md と一致 / 電装ボックス位置と LED "
         "取付点はイメージ (組立時に現物合わせ)"),
    ]

    frames = []
    for label, grp, dur, elev, az0, az1, note in scenes:
        n = int(dur * fps)
        for i in range(n):
            fig = plt.figure(figsize=(8, 6))
            ax = fig.add_subplot(111, projection="3d")
            items = [(m, c, 0.16) for m, c, _ in meshes]
            items.append((head, COL["shell"], 0.07))
            if grp in ("leg", "arm", "all"):
                items += body_ghosts
            draw_meshes(ax, items)
            box_a = 0.95 if grp in ("power", "logic", "all") else 0.35
            for name, b, col, ctr in box_meshes:
                draw_mesh(ax, b, col, box_a)
                if box_a > 0.5:
                    ax.text(ctr[0], ctr[1], ctr[2] + 14, name, fontsize=6.5,
                            ha="center", color="#222",
                            bbox=dict(boxstyle="round,pad=0.15", fc="white",
                                      ec="none", alpha=0.6))
            for g, entries in wires.items():
                on = (grp == "all") or (g == grp)
                for pts, col, lw in entries:
                    ax.plot(pts[:, 0], pts[:, 1], pts[:, 2], color=col,
                            lw=lw if on else lw * 0.6,
                            alpha=0.95 if on else 0.10)
            g_ = np.arange(-300, 301, 50)
            for gx in g_:
                ax.plot([gx, gx], [g_[0], g_[-1]], [0, 0], color="#ddd", lw=0.4)
                ax.plot([g_[0], g_[-1]], [gx, gx], [0, 0], color="#ddd", lw=0.4)
            r = 150 if grp in ("power", "logic") else 240
            cz = zt if grp in ("power", "logic") else 150
            ax.set_xlim(-r, r); ax.set_ylim(-r + 10, r + 10)
            ax.set_zlim(max(cz - r * 0.75, 0), cz + r * 0.75)
            ax.set_box_aspect([1, 1, 0.75]); ax.axis("off")
            ax.view_init(elev=elev, azim=az0 + (az1 - az0) * i / max(n - 1, 1))
            ax.text2D(0.5, 0.95, label, transform=ax.transAxes, ha="center",
                      fontsize=11.5, weight="bold")
            ax.text2D(0.5, 0.015, note, transform=ax.transAxes, ha="center",
                      fontsize=7.5, color="#666")
            fig.tight_layout(pad=0.1)
            fig.canvas.draw()
            frames.append(np.asarray(fig.canvas.buffer_rgba())[:, :, :3].copy())
            plt.close(fig)
        print(f"  scene done: {label} ({n}f)")

    imageio.mimsave(out, frames, fps=fps, codec="libx264", quality=7,
                    pixelformat="yuv420p")
    print(f"saved {out} ({len(frames)} frames)")


if __name__ == "__main__":
    import japanize_matplotlib  # noqa: F401  (日本語ラベル)
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("which", nargs="?", choices=("all", "stills", "video", "eyes", "wiring"), default="all")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--fps", type=int, default=15)
    opts = parser.parse_args()
    if opts.fps <= 0:
        parser.error("--fps は正の整数")
    OUTPUT_DIR = opts.output_dir
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    which = opts.which
    if which in ("all", "stills"):
        exploded_leg()
        exploded_arm()
        elbow_detail_still()
        hand_detail_still()
        chassis_layout()
        assembly_steps()
        proportions_still()
    if which in ("all", "video"):
        walk_video(fps=opts.fps)
    if which in ("all", "eyes"):
        eyes_video(fps=opts.fps)
    if which in ("all", "wiring"):
        wiring_video(fps=opts.fps)
