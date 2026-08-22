"""manifold3d ベースの小さな CAD ヘルパー。

座標系: 右手系, Z 上。各パーツはそれぞれの原点まわりでモデリングし、
build_all.py が印刷向き・配置を決めて STL 出力する。
サーボ関連の関数はプロファイル辞書 (config.MICRO / config.STD) を受け取る。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import trimesh
from manifold3d import Manifold, set_circular_segments

import config as C

set_circular_segments(64)

STL_DIR = Path(__file__).resolve().parent.parent / "stl"


# ---------------------------------------------------------------- 基本形状
def box(x: float, y: float, z: float, center=True) -> Manifold:
    return Manifold.cube([x, y, z], center)


def cyl(h: float, d: float, center=True) -> Manifold:
    """Z 軸方向の円柱。"""
    return Manifold.cylinder(h, d / 2, d / 2, 0, center)


def tube(h: float, d_out: float, d_in: float) -> Manifold:
    return cyl(h, d_out) - cyl(h + 2, d_in)


def cyl_x(h: float, d: float) -> Manifold:
    return cyl(h, d).rotate([0, 90, 0])


def cyl_y(h: float, d: float) -> Manifold:
    return cyl(h, d).rotate([90, 0, 0])


def rbox(x: float, y: float, z: float, r: float = 2.0) -> Manifold:
    """XY 断面の角を丸めた箱 (Z 中心)。"""
    core = box(x - 2 * r, y, z) + box(x, y - 2 * r, z)
    for sx in (-1, 1):
        for sy in (-1, 1):
            core += cyl(z, 2 * r).translate([sx * (x / 2 - r), sy * (y / 2 - r), 0])
    return core


# ---------------------------------------------------------------- サーボ関連
def servo_pocket(p: dict, clear: float = C.CLEAR) -> Manifold:
    """サーボ本体の抜き形状 (負形状)。

    原点 = 出力軸中心・タブ下面高さ。ケースは -Z 方向に沈む。
    ケース長手 = X。軸はケースの +X 寄り (SHAFT_OFF)。
    """
    L = p["L"] + 2 * clear
    W = p["W"] + 2 * clear
    cx = p["L"] / 2 - p["SHAFT_OFF"]  # ケース中心の X (軸原点基準で -)
    body = box(L, W, p["TAB_BELOW"] + 2 * clear).translate(
        [-cx, 0, -(p["TAB_BELOW"] + 2 * clear) / 2]
    )
    top = box(L, W, p["ABOVE_TAB"] + 8).translate([-cx, 0, (p["ABOVE_TAB"] + 8) / 2])
    tabs = box(p["TAB_SPAN"] + 2 * clear, W, p["TAB_T"] + 6).translate(
        [-cx, 0, (p["TAB_T"] + 6) / 2]
    )
    wire = box(8, p["WIRE_W"], p["TAB_BELOW"]).translate(
        [-cx - L / 2 - 2, 0, -p["TAB_BELOW"] / 2]
    )
    return body + top + tabs + wire


def servo_tab_holes(p: dict) -> Manifold:
    """タブ固定ビスの下穴 (負形状)。servo_pocket と同じ原点。

    HOLE_SPREAD > 0 (標準サーボ) はタブごとに 2 穴。
    """
    cx = p["L"] / 2 - p["SHAFT_OFF"]
    h = Manifold()
    ys = (0.0,) if p["HOLE_SPREAD"] == 0 else (-p["HOLE_SPREAD"] / 2, p["HOLE_SPREAD"] / 2)
    for s in (-1, 1):
        for y in ys:
            h += cyl(30, p["TAB_HOLE_D"]).translate(
                [-cx + s * p["HOLE_PITCH"] / 2, y, 0])
    return h


def horn_pocket(p: dict, depth_extra: float = 0.0) -> Manifold:
    """付属シングルアームホーンの埋込みポケット (負形状)。

    原点 = 軸中心、ポケットはアーム面が Z=0〜-HORN_T に沈む。アームは +X。
    中心にホーン固定ビスの通し穴、アーム上に共締めビス下穴 2 個。
    """
    t = p["HORN_T"] + C.CLEAR + depth_extra
    arm = rbox(p["HORN_ARM_L"] + C.CLEAR, p["HORN_ARM_W"] + C.CLEAR, t, r=3.0).translate(
        [p["HORN_ARM_L"] / 2 - p["HORN_ARM_W"] / 2, 0, -t / 2]
    )
    hub = cyl(p["HORN_HUB_H"] + C.CLEAR, p["HORN_HUB_D"] + 2 * C.CLEAR).translate(
        [0, 0, -(p["HORN_HUB_H"] + C.CLEAR) / 2]
    )
    screw = cyl(40, p["HORN_SCREW_D"])
    pilot = Manifold()
    for x in (p["HORN_ARM_L"] * 0.45, p["HORN_ARM_L"] * 0.62):
        pilot += cyl(40, p["HORN_PILOT_D"]).translate([x, 0, 0])
    return arm + hub + screw + pilot


# ---------------------------------------------------------------- 出力
def to_trimesh(m: Manifold) -> trimesh.Trimesh:
    mesh = m.to_mesh()
    v = np.asarray(mesh.vert_properties)[:, :3]
    f = np.asarray(mesh.tri_verts)
    tm = trimesh.Trimesh(vertices=v, faces=f)
    tm.process(validate=True)
    if not tm.is_watertight:
        # validate が細かい輸入メッシュ (元キット形状の流用など) の縮退面を
        # 落として穴を開けることがある。Manifold の出力は常に閉じているので
        # 無加工で再構築する
        tm = trimesh.Trimesh(vertices=v, faces=f, process=False)
    return tm


def export(m: Manifold, name: str) -> trimesh.Trimesh:
    STL_DIR.mkdir(exist_ok=True)
    tm = to_trimesh(m)
    path = STL_DIR / f"{name}.stl"
    tm.export(path)
    # watertight 表示は書き出し前の in-memory メッシュではなく、実際に
    # 書き出した STL を他の全ツール (check_*.py 等) と同じ既定の
    # trimesh.load() で再ロードして判定する (2026-07-28 レビュー finding,
    # major: leg_foot_bored.stl が書き出し前は watertight=True と表示され
    # つつ、実際の STL 再ロードでは is_watertight=False/euler=-1 だった —
    # to_trimesh() の process=False フォールバックが「閉じている」ことを
    # 保証するのは Manifold 由来の面情報についてのみで、STL のテキスト/
    # バイナリ往復 (浮動小数点の再量子化) で縮退面や非多様体面が新たに
    # 生じる場合があるため、書き出し前チェックだけでは見逃す)
    reloaded = trimesh.load(path)
    ext = tm.extents
    print(f"  {name:28s} {ext[0]:6.1f} x {ext[1]:6.1f} x {ext[2]:6.1f} mm  "
          f"watertight={reloaded.is_watertight}  vol={tm.volume/1000:.1f}cm3")
    return tm
