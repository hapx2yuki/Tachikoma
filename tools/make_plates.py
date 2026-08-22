"""単色プレート 3mf の生成・検証パイプライン (2026-08-20 恒久化)。

usage:
  cd <repo> && .venv/bin/python tools/make_plates.py [プレート名 ... | all | verify]
    プレート名省略/all: PLATES 全部を再生成
    verify: 生成せず、既存 3mf の埋め込みメッシュ照合だけ実行

経緯 (2026-08-20): 従来この工程はセッション scratchpad の使い捨てスクリプト
(orient_parts.py / build_3mf.py) + 中間 pickle で行っていたが、
  (a) scratchpad が tmp クリーナーで消失し再現不能になった
  (b) 中間 pickle が stale なまま rebuild が走り、**STL は更新済みなのに
      3mf には旧メッシュが埋め込まれる事故が実際に起きた** (Head_Bottom_Armcut
      のカスプ除去が Blue_2 に反映されず、ユーザーがスライサ上で発見。
      パイプの終了コードを grep が握りつぶし上流失敗が見えなかった)
ためリポジトリへ恒久化した。対策としてこのツールは:
  1. 中間キャッシュを持たない (毎回 STL / model から直接読む)
  2. **生成した 3mf から埋め込みメッシュを抽出し直し、ソースメッシュと
     体積・bbox を照合する検証 (verify_3mf) を必須で通す** — 「ログに正しい
     寸法が出た」ではなく「成果物の中身」を検査する
  3. プレート構成 (PLATES) を固定し、再パッキングによる構成ドリフトを防ぐ

3mf の器 (project_settings 等) は本ツール系で過去に生成した
elbow_shells_PLA_Matte.3mf をテンプレートとして使う (元は claw_mount_L.3mf
[Studio 生成, stock 設定+サポート有効] から派生)。プリンタ/ノズル設定は
テンプレート由来 — 変えたくなったら Studio で開いて保存し直すのが正。

X2D ベッド 256x256。左ノズル専用帯を避けて X>=45 に配置 (既存プレート群と
同じマージン)。スロット: 1=青 (Panchroma Sapphire Blue / Generic PLA),
2=白 PLA Matte, 3=グレー PLA Matte (AMS 実装に一致 — docs/print_manifest.md)。
"""
import re
import shutil
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path

import numpy as np
import trimesh

ROOT = Path(__file__).resolve().parent.parent
STL = ROOT / "hardware" / "stl"
MODEL = ROOT / "model"
TEMPLATE_3MF = STL / "elbow_shells_PLA_Matte.3mf"
SCALE = 1.5

# AMS 実装: 1=青 (Panchroma Sapphire Blue), 2=白 PLA Matte, 3=グレー PLA Matte,
# 4=PETG Translucent。black/red は AMS 非搭載 — スロット1へ出力するので
# **印刷前に Studio でフィラメント割当を黒/赤へ差し替えること** (ファイル名で
# 色を明示して事故防止)
COLOR_SLOT = {"blue": "1", "gray": "3", "white": "2", "petg": "4",
              "black": "1", "red": "1"}

# name -> (rule, qty, wall_loops, sparse_infill, color, kit)
# kit=True は model/ の元キット STL (読み込み時に bbox 中心化 + x1.5)
PARTS = {
    "Head_Bottom_Armcut.stl":  ("flip_x180", 1, 2, "8%", "blue", False),
    "Mouth_Neck_Bored.stl":    ("identity",  1, 2, "8%", "blue", False),
    "arm_pod_upper.stl":       ("flat_down", 1, 2, "8%", "blue", False),
    "arm_pod_upper_L.stl":     ("flat_down", 1, 2, "8%", "blue", False),
    "arm_pod_lower.stl":       ("flat_min",  1, 2, "8%", "blue", False),
    "arm_pod_lower_L.stl":     ("flat_min",  1, 2, "8%", "blue", False),
    "Cabin_Front_Blue.stl":                   ("flat_down", 1, 2, "8%", "blue", True),
    "Cabin_Back_Blue_Repaired.stl":           ("flat_down", 1, 2, "8%", "blue", True),
    "Head_TailJoint_Blue_Optional_Cross.stl": ("flat_down", 1, 2, "8%", "blue", True),
    "Leg_Thigh_Guard_Blue_x4.stl":            ("flat_min",  4, 2, "8%", "blue", True),
    # ---- 残パーツ一括プレート化 (2026-08-21) ----
    # キット グレー意匠 (_x4 等のファイルは 1 個入り — qty で複数印刷)
    "Arm_Left_Claw_Grey.stl":            ("flat_min",  2, 2, "8%",  "gray", True),
    "Arm_Left_FingerTip_Grey_x3.stl":    ("flat_down", 6, 2, "8%",  "gray", True),
    "Arm_Left_Guard_Grey.stl":           ("flat_min",  1, 2, "8%",  "gray", True),
    "Arm_Right_Guard_Grey.stl":          ("flat_min",  1, 2, "8%",  "gray", True),
    "Cabin_Spinnarette_Grey_x4.stl":     ("flat_down", 4, 2, "8%",  "gray", True),
    "Cabin_Turrent_Left_Grey.stl":       ("flat_min",  1, 2, "8%",  "gray", True),
    "Cabin_Turrent_Right_Grey.stl":      ("flat_min",  1, 2, "8%",  "gray", True),
    "Head_Dome_Grey.stl":                ("flat_down", 1, 2, "8%",  "gray", True),
    "Head_Plug_Grey.stl":                ("flat_down", 1, 2, "8%",  "gray", True),
    "Head_Screw_Grey_x2.stl":            ("flat_min",  2, 2, "8%",  "gray", True),
    "Head_TailJoint_Ball_Grey_Optional_Cross.stl": ("flat_down", 1, 2, "8%", "gray", True),
    "Leg_Shin_Guard_Grey_x4.stl":        ("flat_min",  4, 2, "8%",  "gray", True),
    "Mouth_Cap_Grey.stl":                ("flat_down", 1, 2, "8%",  "gray", True),
    "Mouth_Key_Grey.stl":                ("flat_min",  1, 2, "8%",  "gray", True),
    "Mouth_Peg_Grey.stl":                ("flat_down", 1, 2, "8%",  "gray", True),
    # 嵌合ペグ類 (完成後不可視 — グレーで印刷)
    "Cabin_Peg_x2.stl":                  ("flat_min",  2, 2, "15%", "gray", True),
    "Cabin_Turret_Peg_x2.stl":           ("flat_min",  2, 2, "15%", "gray", True),
    "Head_Peg_Lower.stl":                ("flat_down", 1, 2, "15%", "gray", True),
    "Head_Peg_Upper.stl":                ("flat_down", 1, 2, "15%", "gray", True),
    "Head_TailJoint_Peg.stl":            ("flat_down", 1, 2, "15%", "gray", True),
    "Head_TailJoint_Peg_Optional_Cross_Repaired.stl": ("flat_down", 1, 2, "15%", "gray", True),
    # 黒 (トゥ/指は接着・接地部品なので壁3/15%)
    "Leg_Toe_Black_x12.stl":             ("flat_down", 12, 3, "15%", "black", True),
    "Arm_Left_Finger_Black_x3.stl":      ("flat_min",  6, 3, "15%", "black", True),
    "Cabin_Front_Insert_Back_Black_x2.stl":        ("flat_min", 2, 2, "8%", "black", True),
    "Cabin_Front_Insert_Bottom_Long_Black_x2.stl": ("flat_min", 2, 2, "8%", "black", True),
    "Cabin_Front_Insert_Bottom_Wide_Black.stl":    ("flat_min", 1, 2, "8%", "black", True),
    "Cabin_Front_Insert_Front_Black.stl":          ("flat_min", 1, 2, "8%", "black", True),
    "Cabin_Front_Insert_Left_Black.stl":           ("flat_min", 1, 2, "8%", "black", True),
    "Cabin_Front_Insert_Right_Black.stl":          ("flat_min", 1, 2, "8%", "black", True),
    "Head_Insert_Black_x4.stl":          ("flat_min",  4, 2, "8%",  "black", True),
    # 赤 / 白
    "Cabin_RedLight_Large_Red_x4.stl":   ("flat_down", 4, 2, "8%",  "red",   True),
    "Cabin_RedLight_Small_Red_x4.stl":   ("flat_down", 4, 2, "8%",  "red",   True),
    "Cabin_Eye_White.stl":               ("flat_down", 1, 2, "8%",  "white", True),
    # PETG 骨格 (向き・壁/インフィルは docs/print_manifest.md §1 表が正:
    # 「STLのまま」=identity, 「Z反転」「上面を下」=flip_x180, tibia は立てて印刷)
    "chassis.stl":          ("identity",  1, 4, "25%", "petg", False),
    "pod_neck.stl":         ("identity",  1, 4, "40%", "petg", False),
    "battery_cradle.stl":   ("flip_x180", 1, 3, "20%", "petg", False),
    "coxa_bracket.stl":     ("flip_x180", 2, 4, "40%", "petg", False),
    "coxa_bracket_m.stl":   ("flip_x180", 2, 4, "40%", "petg", False),
    "femur_link.stl":       ("identity",  2, 4, "40%", "petg", False),
    "femur_link_m.stl":     ("identity",  2, 4, "40%", "petg", False),
    "tibia_link.stl":       ("identity",  2, 4, "40%", "petg", False),
    "tibia_link_m.stl":     ("identity",  2, 4, "40%", "petg", False),
    "shoulder_bracket.stl": ("flip_x180", 1, 4, "40%", "petg", False),
    "shoulder_bracket_L.stl": ("flip_x180", 1, 4, "40%", "petg", False),
    "upper_arm.stl":        ("identity",  1, 4, "40%", "petg", False),
    "upper_arm_L.stl":      ("identity",  1, 4, "40%", "petg", False),
    "forearm.stl":          ("identity",  1, 4, "40%", "petg", False),
    "forearm_L.stl":        ("identity",  1, 4, "40%", "petg", False),
    "claw_mount.stl":       ("flat_down", 1, 4, "40%", "petg", False),
    "claw_mount_L.stl":     ("flat_down", 1, 4, "40%", "petg", False),
    "eye_carrier.stl":      ("identity",  2, 4, "40%", "petg", False),
    # camera_carrier: raw フレームは接地 0mm² (完全サポート上) になる。flat_down
    # なら大平面を下にしてポケット開口が上 (中実率 下0.57/上0.11 で実測確認)
    # — manifest「レンズポケット側を上」を満たし接地 352mm²
    "camera_carrier.stl":   ("flat_down", 1, 4, "40%", "petg", False),
    "audio_cradle_mic.stl": ("flat_min",  1, 4, "40%", "petg", False),
    "audio_cradle_spk.stl": ("flat_down", 1, 4, "40%", "petg", False),
}

# プレート構成は固定 (再パッキングで Blue_1 等と構成が混ざるのを防ぐ)。
# rot90: 平面 90° 回転して詰めるパーツ / gap: そのプレートの部品間隔
# 注意: ディスク上の Blue_3/4 (2026-08-20, 旧 scratchpad パイプライン産) は
# 構成が CF+ガード / CB+TailJoint と本定義 (CF 単独 / CB+TJ+ガード) と異なるが
# 埋め込みメッシュは verify で全 OK — メッシュが変わらない限り再生成不要。
# 再生成した場合は本定義の構成に変わる (どちらも全部品を網羅しており等価)
PLATES = {
    "PLA_Matte_Blue_2": dict(
        items=["Head_Bottom_Armcut.stl", "arm_pod_upper.stl", "arm_pod_upper_L.stl",
               "arm_pod_lower.stl", "arm_pod_lower_L.stl", "Mouth_Neck_Bored.stl"],
        color="blue", gap=14.0, rot90=()),
    # Cabin_Front (170x195 接地) は単独でベッドをほぼ専有
    "PLA_Matte_Blue_3": dict(
        items=["Cabin_Front_Blue.stl"], color="blue", gap=14.0, rot90=()),
    # Cabin_Back は 90° 回して (183x142) 下段へ、上段に TailJoint+ガード x4
    # (gap 14 では 4 個目のガードが X1=240 を 3mm 超えるため gap 10)
    "PLA_Matte_Blue_4": dict(
        items=["Cabin_Back_Blue_Repaired.stl", "Head_TailJoint_Blue_Optional_Cross.stl",
               "Leg_Thigh_Guard_Blue_x4.stl"],
        color="blue", gap=10.0, rot90=("Cabin_Back_Blue_Repaired.stl",)),
    # ---- 残パーツ一括 (2026-08-21)。Stand_mount_Optional (任意) と
    # Head_Plate_Grey (2026-08-20 不使用化 — シャーシが置換) は含めない ----
    "PLA_Matte_Gray_2": dict(
        items=["Cabin_Turrent_Left_Grey.stl", "Cabin_Turrent_Right_Grey.stl",
               "Mouth_Cap_Grey.stl", "Head_TailJoint_Ball_Grey_Optional_Cross.stl",
               "Leg_Shin_Guard_Grey_x4.stl", "Arm_Left_Claw_Grey.stl",
               "Arm_Left_Guard_Grey.stl", "Arm_Right_Guard_Grey.stl",
               "Cabin_Spinnarette_Grey_x4.stl", "Arm_Left_FingerTip_Grey_x3.stl",
               "Mouth_Key_Grey.stl", "Mouth_Peg_Grey.stl", "Head_Dome_Grey.stl",
               "Head_Plug_Grey.stl", "Head_Screw_Grey_x2.stl", "Cabin_Peg_x2.stl",
               "Cabin_Turret_Peg_x2.stl", "Head_Peg_Lower.stl", "Head_Peg_Upper.stl",
               "Head_TailJoint_Peg.stl",
               "Head_TailJoint_Peg_Optional_Cross_Repaired.stl"],
        color="gray", gap=10.0, rot90=()),
    "PLA_Black_1": dict(
        items=["Leg_Toe_Black_x12.stl", "Arm_Left_Finger_Black_x3.stl",
               "Cabin_Front_Insert_Back_Black_x2.stl",
               "Cabin_Front_Insert_Bottom_Long_Black_x2.stl",
               "Cabin_Front_Insert_Bottom_Wide_Black.stl",
               "Cabin_Front_Insert_Front_Black.stl",
               "Cabin_Front_Insert_Left_Black.stl",
               "Cabin_Front_Insert_Right_Black.stl", "Head_Insert_Black_x4.stl"],
        color="black", gap=10.0, rot90=()),
    "PLA_Red_1": dict(
        items=["Cabin_RedLight_Large_Red_x4.stl", "Cabin_RedLight_Small_Red_x4.stl"],
        color="red", gap=10.0, rot90=()),
    "PLA_Matte_White_2": dict(
        items=["Cabin_Eye_White.stl"], color="white", gap=14.0, rot90=()),
    "PETG_1_Chassis": dict(
        items=["chassis.stl", "eye_carrier.stl", "claw_mount.stl", "claw_mount_L.stl",
               "audio_cradle_spk.stl"],
        color="petg", gap=12.0, rot90=()),
    "PETG_2_Tibia": dict(
        items=["tibia_link.stl", "tibia_link_m.stl", "pod_neck.stl"],
        color="petg", gap=12.0, rot90=("pod_neck.stl",)),
    "PETG_3_Femur": dict(
        items=["femur_link.stl", "femur_link_m.stl", "battery_cradle.stl"],
        color="petg", gap=12.0, rot90=()),
    "PETG_4_CoxaArm": dict(
        items=["coxa_bracket.stl", "coxa_bracket_m.stl", "shoulder_bracket.stl",
               "shoulder_bracket_L.stl", "upper_arm.stl", "upper_arm_L.stl",
               "forearm.stl", "forearm_L.stl", "camera_carrier.stl"],
        color="petg", gap=12.0, rot90=()),
    # audio_cradle_mic はレイヤー 0.12 指定 (docs/print_manifest.md) のため単独
    "PETG_5_Mic": dict(
        items=["audio_cradle_mic.stl"], color="petg", gap=14.0, rot90=(),
        layer_height="0.12"),
    # ---- 歩行実験 最小セット (2026-08-21, 3 ファイル構成) ----
    # 歩行チェーン chassis→coxa→femur→tibia + battery_cradle のみ。腕・目・
    # カメラ・pod_neck・spk は含めない。全体で footprint 121% なので 1 ベッド
    # には収まらない。当初 1 ファイル 3 プレートのマルチプレート構成にしたが
    # 「分かりづらい」とのユーザー要望で 1 プレート = 1 ファイルに分割した
    # (マルチプレート機能自体は plates=[...] キーで引き続き利用可)。
    # mic は「ついで」同乗 (注意: 単独プレートの 0.12 指定に対しここでは
    # 0.2 — 圧入面が粗ければ PETG_5_Mic で刷り直す)。
    # femur は 103mm 幅で 2 本並ばないため 90° 回転で 4 本 1 段に。
    "PETG_Walk_1_Chassis": dict(
        items=["chassis.stl", "battery_cradle.stl", "audio_cradle_mic.stl"],
        color="petg", gap=12.0, rot90=()),
    "PETG_Walk_2_CoxaFemur": dict(
        items=["coxa_bracket.stl", "coxa_bracket_m.stl",
               "femur_link.stl", "femur_link_m.stl"],
        color="petg", gap=12.0,
        rot90=("femur_link.stl", "femur_link_m.stl")),
    "PETG_Walk_3_Tibia": dict(
        items=["tibia_link.stl", "tibia_link_m.stl"],
        color="petg", gap=12.0, rot90=()),
}

# オブジェクト単位の追加印刷設定 (model_settings.config の object metadata へ
# そのまま出力)。Leg_Thigh_Guard: 湾曲シェルで第1層接地がリム線 40mm² しかなく
# 実印刷で失敗 (2026-08-21 ユーザー報告) — プレート既定の auto_brim は小接地
# パーツでブリムを省くことがあるため、明示的に外周ブリムを強制する
EXTRA_OBJECT_SETTINGS = {
    "Leg_Thigh_Guard_Blue_x4.stl": {"brim_type": "outer_only", "brim_width": "5"},
    # chassis はマニフェスト指定「インフィル25%グリッド」
    "chassis.stl": {"sparse_infill_pattern": "grid"},
}

X0, X1, Y0, Y1 = 45.0, 240.0, 15.0, 235.0

# ---- マルチプレート (1 ファイル複数ベッド) ----
# BambuStudio 本体のプレート配置定数に一致させる必要がある
# (src/slic3r/GUI/PartPlate.cpp: LOGICAL_PART_PLATE_GAP = 1/5,
#  stride = plate_size * 1.2, col = i % cols, row = i / cols, y は -row 方向,
#  cols = round(sqrt(n)) を超えたら +1 — PartPlate.hpp compute_colum_count)
PLATE_STRIDE = 256.0 * 1.2  # 307.2


def _plate_cols(n: int) -> int:
    v = n ** 0.5
    r = round(v)
    return int(r + 1) if v > r else int(r)


def _plate_origin(i: int, n: int):
    cols = _plate_cols(n)
    return ((i % cols) * PLATE_STRIDE, -(i // cols) * PLATE_STRIDE)


# ---------------- 向き ----------------
def _rot_flat_face_down(mesh, min_height=False):
    """凸包の大きい平面ファセットを -Z へ (min_height=True は高さ最小を選ぶ)。"""
    hull = mesh.convex_hull
    areas, normals = hull.facets_area, hull.facets_normal
    if not len(areas):
        normals, areas = hull.face_normals, hull.area_faces
    if not min_height:
        return trimesh.geometry.align_vectors(normals[int(np.argmax(areas))], [0, 0, -1])
    best = (np.eye(4), np.inf)
    for n, a in zip(normals, areas):
        if a < 0.15 * areas.max():
            continue
        R = trimesh.geometry.align_vectors(n, [0, 0, -1])
        m = mesh.copy()
        m.apply_transform(R)
        if m.extents[2] < best[1] - 1e-6:
            best = (R, m.extents[2])
    return best[0]


def load_oriented(fname: str, rot90: bool = False) -> trimesh.Trimesh:
    rule, _q, _w, _i, _c, kit = PARTS[fname]
    if kit:
        m = trimesh.load(MODEL / fname, force="mesh")
        m.apply_translation(-(m.bounds[0] + m.bounds[1]) / 2)
        m.apply_scale(SCALE)
    else:
        m = trimesh.load(STL / fname, force="mesh")
    if rule == "flip_x180":
        m.apply_transform(trimesh.transformations.rotation_matrix(np.pi, [1, 0, 0]))
    elif rule == "flat_down":
        m.apply_transform(_rot_flat_face_down(m))
    elif rule == "flat_min":
        m.apply_transform(_rot_flat_face_down(m, min_height=True))
    elif rule != "identity":
        raise ValueError(rule)
    if rot90:
        m.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [0, 0, 1]))
    b = m.bounds
    m.apply_translation([-(b[0][0] + b[1][0]) / 2, -(b[0][1] + b[1][1]) / 2, -b[0][2]])
    return m


# ---------------- パッキング (シェルフ法) ----------------
def pack(entries, gap):
    """entries: [(fname, w, d)] (数量展開済み)。1 プレートに収まらなければ例外。
    戻り値: [(fname, cx, cy)]"""
    items = sorted(entries, key=lambda t: t[1] * t[2], reverse=True)
    shelves, placed = [], []
    for fname, w, d in items:
        pos = None
        for sh in shelves:
            if sh["x"] + w <= X1 and sh["y"] + d <= Y1:
                pos = (sh["x"], sh["y"])
                sh["x"] += w + gap
                sh["h"] = max(sh["h"], d)
                break
        if pos is None:
            ny = (shelves[-1]["y"] + shelves[-1]["h"] + gap) if shelves else Y0
            if ny + d > Y1 or X0 + w > X1:
                raise RuntimeError(f"{fname} がプレートに収まらない (y={ny}, d={d})")
            shelves.append({"x": X0 + w + gap, "y": ny, "h": d})
            pos = (X0, ny)
        placed.append((fname, pos[0] + w / 2, pos[1] + d / 2))
    return placed


# ---------------- 3mf 書き出し ----------------
def _mesh_xml(obj_id, verts, faces):
    L = ['<?xml version="1.0" encoding="UTF-8"?>',
         '<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" xmlns:BambuStudio="http://schemas.bambulab.com/package/2021" xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06" requiredextensions="p">',
         ' <metadata name="BambuStudio:3mfVersion">1</metadata>',
         ' <resources>',
         f'  <object id="{obj_id}" p:UUID="{uuid.uuid4()}" type="model">',
         '   <mesh>',
         '    <vertices>']
    L += [f'     <vertex x="{v[0]:.8g}" y="{v[1]:.8g}" z="{v[2]:.8g}"/>' for v in verts]
    L += ['    </vertices>', '    <triangles>']
    L += [f'     <triangle v1="{f[0]}" v2="{f[1]}" v3="{f[2]}"/>' for f in faces]
    L += ['    </triangles>', '   </mesh>', '  </object>', ' </resources>', ' <build/>', '</model>']
    return "\n".join(L)


def _preview_png(out_path, plate_name, items, meshes, color):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import PolyCollection
    fig, ax = plt.subplots(figsize=(5.12, 5.12), dpi=100)
    ax.add_patch(plt.Rectangle((0, 0), 256, 256, fc="#3a3d44", ec="none"))
    cols = {"blue": [0.28, 0.42, 0.85], "gray": [0.62, 0.63, 0.64],
            "white": [0.92, 0.92, 0.9], "black": [0.25, 0.25, 0.28],
            "red": [0.85, 0.2, 0.18], "petg": [0.55, 0.75, 0.72]}[color]
    for (fname, cx, cy) in items:
        m = meshes[fname]
        tri = m.vertices[m.faces]
        pts = tri[:, :, :2] + np.array([cx, cy])
        zd = tri[:, :, 2].mean(axis=1)
        o = np.argsort(zd)
        sh = 0.35 + 0.55 * (zd - zd.min()) / max(float(zd.max() - zd.min()), 1e-9)
        ax.add_collection(PolyCollection(pts[o], facecolors=np.outer(sh[o], cols),
                                         edgecolors="none"))
        ax.text(cx, cy, fname.replace(".stl", ""), fontsize=5, color="#ffdd55",
                ha="center")
    ax.set_xlim(0, 256); ax.set_ylim(0, 256); ax.set_aspect("equal")
    ax.set_title(plate_name, fontsize=9)
    ax.tick_params(labelsize=6)
    fig.savefig(out_path, dpi=100, bbox_inches="tight")
    plt.close(fig)


def build_plate(plate_name: str) -> Path:
    spec = PLATES[plate_name]
    color = spec["color"]
    slot = COLOR_SLOT[color]
    # "plates" キーがあれば 1 ファイル複数ベッド。無ければ従来の単一プレート
    subplates = spec.get("plates") or [dict(items=spec["items"],
                                            rot90=spec.get("rot90", ()))]
    n_sub = len(subplates)
    meshes, sub_items = {}, []
    for sp in subplates:
        gap = sp.get("gap", spec.get("gap", 12.0))
        for f in sp["items"]:
            meshes[f] = load_oriented(f, rot90=(f in sp.get("rot90", ())))
        entries = []
        for f in sp["items"]:
            w, d = meshes[f].extents[0], meshes[f].extents[1]
            entries += [(f, w, d)] * PARTS[f][1]
        sub_items.append(pack(entries, gap))

    work = Path(tempfile.mkdtemp(prefix=f"plate_{plate_name}_"))
    with zipfile.ZipFile(TEMPLATE_3MF) as z:
        z.extractall(work)
    for f in (work / "3D/Objects").iterdir():
        f.unlink()

    # プレート別レイヤー高 (例: audio_cradle_mic の 0.12 指定)
    lh = spec.get("layer_height")
    if lh:
        ps = work / "Metadata/project_settings.config"
        t = ps.read_text()
        t2 = re.sub(r'"layer_height": "[^"]+"', f'"layer_height": "{lh}"', t)
        assert t2 != t, "layer_height キーが見つからない"
        ps.write_text(t2)

    plate_of, order = {}, []
    for si, its in enumerate(sub_items):
        for f, _, _ in its:
            if f not in plate_of:
                plate_of[f] = si
                order.append(f)
    placements = {f: [(x, y) for g, x, y in sub_items[plate_of[f]] if g == f]
                  for f in order}
    res_objects, build_items, rels = [], [], []
    ms_objects, ms_assemble = [], []
    ms_instances = [[] for _ in range(n_sub)]   # プレート別
    plate_json_objs = [[] for _ in range(n_sub)]
    ident = 100
    for k, fname in enumerate(order):
        m = meshes[fname]
        oid, comp_id = 2 * k + 2, 2 * k + 1
        model_file = f"object_{k+1}.model"
        (work / "3D/Objects" / model_file).write_text(
            _mesh_xml(comp_id, m.vertices, m.faces))
        res_objects.append(
            f'  <object id="{oid}" p:UUID="{uuid.uuid4()}" type="model">\n'
            f'   <components>\n'
            f'    <component p:path="/3D/Objects/{model_file}" objectid="{comp_id}" p:UUID="{uuid.uuid4()}" transform="1 0 0 0 1 0 0 0 1 0 0 0"/>\n'
            f'   </components>\n'
            f'  </object>')
        rels.append(f' <Relationship Target="/3D/Objects/{model_file}" Id="rel-{k+1}" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>')
        nf = len(m.faces)
        _r, _q, walls, infill, _c, _k = PARTS[fname]
        extras = dict(EXTRA_OBJECT_SETTINGS.get(fname, {}))
        # 第1層接地が小さいパーツは自動でブリム強制 (Thigh_Guard 失敗の教訓 —
        # プレート既定 auto_brim は小接地でブリムを省くことがある)
        fz = m.vertices[m.faces][:, :, 2]
        low = (fz < 0.4).all(axis=1) & (m.face_normals[:, 2] < -0.5)
        contact = float(m.area_faces[low].sum())
        if contact < 150.0 and "brim_type" not in extras:
            extras["brim_type"] = "outer_only"
            extras["brim_width"] = "5"
        extra = "".join(f'    <metadata key="{k}" value="{v}"/>\n'
                        for k, v in extras.items())
        if extras.get("brim_type"):
            print(f"    brim: {fname} (contact {contact:.0f}mm2)")
        ms_objects.append(
            f'  <object id="{oid}">\n'
            f'    <metadata key="name" value="{fname}"/>\n'
            f'    <metadata key="extruder" value="{slot}"/>\n'
            f'    <metadata key="wall_loops" value="{walls}"/>\n'
            f'    <metadata key="sparse_infill_density" value="{infill}"/>\n'
            + extra +
            f'    <metadata face_count="{nf}"/>\n'
            f'    <part id="{comp_id}" subtype="normal_part">\n'
            f'      <metadata key="name" value="{fname}"/>\n'
            f'      <metadata key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"/>\n'
            f'      <metadata key="source_file" value="{fname}"/>\n'
            f'      <metadata key="source_object_id" value="0"/>\n'
            f'      <metadata key="source_volume_id" value="0"/>\n'
            f'      <mesh_stat face_count="{nf}" edges_fixed="0" degenerate_facets="0" facets_removed="0" facets_reversed="0" backwards_edges="0"/>\n'
            f'    </part>\n'
            f'  </object>')
        w, d = m.extents[0], m.extents[1]
        si = plate_of[fname]
        ox, oy = _plate_origin(si, n_sub)   # プレート k の world 原点オフセット
        for inst, (cx, cy) in enumerate(placements[fname]):
            build_items.append(f'  <item objectid="{oid}" p:UUID="{uuid.uuid4()}" transform="1 0 0 0 1 0 0 0 1 {cx + ox:.5f} {cy + oy:.5f} 0" printable="1"/>')
            ms_instances[si].append(
                '    <model_instance>\n'
                f'      <metadata key="object_id" value="{oid}"/>\n'
                f'      <metadata key="instance_id" value="{inst}"/>\n'
                f'      <metadata key="identify_id" value="{ident}"/>\n'
                '    </model_instance>')
            ms_assemble.append(f'   <assemble_item object_id="{oid}" instance_id="{inst}" transform="1 0 0 0 1 0 0 0 1 {80*k + inst*45:.1f} 0 0" offset="0 0 0" />')
            plate_json_objs[si].append({"area": round(w * d, 2),
                                        "bbox": [round(cx - w/2, 3), round(cy - d/2, 3),
                                                 round(cx + w/2, 3), round(cy + d/2, 3)],
                                        "id": ident, "layer_height": 0.2, "name": fname})
            ident += 11

    p = work / "3D/3dmodel.model"
    t = p.read_text()
    t = re.sub(r' <resources>.*?</resources>',
               ' <resources>\n' + "\n".join(res_objects) + '\n </resources>', t, flags=re.S)
    t = re.sub(r' <build p:UUID="[^"]*">.*?</build>',
               f' <build p:UUID="{uuid.uuid4()}">\n' + "\n".join(build_items) + '\n </build>',
               t, flags=re.S)
    p.write_text(t)
    (work / "3D/_rels/3dmodel.model.rels").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
        + "\n".join(rels) + '\n</Relationships>\n')
    p = work / "Metadata/model_settings.config"
    t = p.read_text()
    t = re.sub(r'  <object id=.*?</object>\n', '', t, flags=re.S)
    # テンプレートの単一 <plate> ブロックをプレート数分に複製し、plater_id・
    # サムネイルファイル名を差し替え、各プレートの model_instance を挿入する
    mblk = re.search(r'[ \t]*<plate>.*?</plate>\n', t, re.S)
    tpl_block = re.sub(r'    <model_instance>.*?</model_instance>\n', '',
                       mblk.group(0), flags=re.S)
    blocks = []
    for si in range(n_sub):
        b = re.sub(r'<metadata key="plater_id" value="\d+"/>',
                   f'<metadata key="plater_id" value="{si + 1}"/>', tpl_block)
        for base in ("plate", "plate_no_light", "top", "pick"):
            b = b.replace(f'Metadata/{base}_1.png', f'Metadata/{base}_{si + 1}.png')
        b = b.replace('</plate>', "\n".join(ms_instances[si]) + '\n  </plate>')
        blocks.append(b)
    t = t[:mblk.start()] + "".join(blocks) + t[mblk.end():]
    t = re.sub(r'   <assemble_item [^\n]*\n', '', t)
    t = t.replace('<config>\n', '<config>\n' + "\n".join(ms_objects) + '\n')
    t = t.replace('  <assemble>\n', '  <assemble>\n' + "\n".join(ms_assemble) + '\n')
    p.write_text(t)
    import json
    for si in range(n_sub):
        objs = plate_json_objs[si]
        allb = [min(o["bbox"][0] for o in objs), min(o["bbox"][1] for o in objs),
                max(o["bbox"][2] for o in objs), max(o["bbox"][3] for o in objs)]
        (work / f"Metadata/plate_{si + 1}.json").write_text(
            json.dumps({"bbox_all": allb, "bbox_objects": objs}))
        png = work / f"Metadata/plate_{si + 1}.png"
        title = plate_name if n_sub == 1 else f"{plate_name}  [plate {si + 1}/{n_sub}]"
        _preview_png(png, title, sub_items[si], meshes, color)
        for base in ["plate_no_light", "top", "pick"]:
            shutil.copy(png, work / f"Metadata/{base}_{si + 1}.png")
        try:
            from PIL import Image
            Image.open(png).resize((128, 128)).save(
                work / f"Metadata/plate_{si + 1}_small.png")
        except Exception:
            shutil.copy(png, work / f"Metadata/plate_{si + 1}_small.png")
    fs = work / "Metadata/filament_sequence.json"
    if fs.exists():
        fs.write_text(json.dumps(
            {f"plate_{i + 1}": {"nozzle_sequence": [], "optimal_assignment": [],
                                "sequence": []} for i in range(n_sub)}))

    out = STL / f"{plate_name}.3mf"
    if out.exists():
        out.unlink()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(work / "[Content_Types].xml", "[Content_Types].xml")
        for f in sorted(work.rglob("*")):
            if f.is_file() and f.name != "[Content_Types].xml":
                z.write(f, str(f.relative_to(work)))
    shutil.rmtree(work)
    n_inst = sum(len(its) for its in sub_items)
    print(f"  built {out.name}: {n_inst} instances / {len(order)} objects / "
          f"{n_sub} plate(s) / {out.stat().st_size // 1024}KB")
    return out


# ---------------- 成果物検証 ----------------
def verify_3mf(path: Path) -> bool:
    """3mf に実際に埋め込まれたメッシュを抽出し、ソースメッシュと照合する。
    体積差 >0.5% か bbox 差 >0.3mm で NG (stale 埋め込みの検出が目的)。"""
    zf = zipfile.ZipFile(path)
    ms = zf.read("Metadata/model_settings.config").decode()
    pairs = re.findall(r'<object id="(\d+)">\s*<metadata key="name" value="([^"]+)"', ms)
    main = zf.read("3D/3dmodel.model").decode()
    ok_all = True
    for oid, name in pairs:
        m2 = re.search(rf'<object id="{oid}"[^>]*>.*?p:path="([^"]+)"', main, re.S)
        xml = zf.read(m2.group(1).lstrip("/")).decode()
        verts = np.array(re.findall(r'<vertex x="([^"]+)" y="([^"]+)" z="([^"]+)"', xml), float)
        faces = np.array(re.findall(r'<triangle v1="(\d+)" v2="(\d+)" v3="(\d+)"', xml), int)
        emb = trimesh.Trimesh(vertices=verts, faces=faces, process=False)
        if name in PARTS:
            src = load_oriented(name)          # rot90 は体積/高さに影響しない
        else:
            print(f"    {name}: PARTS 未登録 — スキップ (照合対象外)")
            continue
        dv = abs(emb.volume - src.volume) / max(src.volume, 1e-9)
        dz = abs(emb.extents[2] - src.extents[2])
        ok = dv < 0.005 and dz < 0.3
        ok_all &= ok
        print(f"    {name}: embedded {emb.volume/1000:7.1f}cm3 h{emb.extents[2]:5.1f} "
              f"/ source {src.volume/1000:7.1f}cm3 h{src.extents[2]:5.1f} "
              f"({'OK' if ok else '** STALE/NG **'})")
    return ok_all


if __name__ == "__main__":
    args = sys.argv[1:] or ["all"]
    if args == ["verify"]:
        ok = True
        for name in PLATES:
            p = STL / f"{name}.3mf"
            if p.exists():
                print(f"[verify] {p.name}")
                ok &= verify_3mf(p)
        sys.exit(0 if ok else 1)
    names = list(PLATES) if args == ["all"] else args
    ok = True
    for name in names:
        print(f"[build] {name}")
        out = build_plate(name)
        ok &= verify_3mf(out)
    print(f"verify: {'ALL OK' if ok else 'NG あり — 出荷しないこと'}")
    sys.exit(0 if ok else 1)
