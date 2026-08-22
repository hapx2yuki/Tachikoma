"""目 (キョロキョロ機構) — SUBMICRO 1 個/目 × 3 目。

v3: 眼球は独自造形をやめ、**元キットの目パーツ Head_Eye_White の形状を
そのまま流用**する (150%)。実物の目 = 白い扁平ドームに黒い小ドット 3 つが
偏って配置されたもので、キットのキャップにはドット位置の小穴がモールド
されている (黒く塗って仕上げる)。

機構: ドット群はキャップ軸から **~45° 偏心**しているため、キャップを
サーボで自軸回転させるだけでドット群 (=視線) が眼球上を大きく泳ぐ
(掃引径 ~25mm)。傾け機構は不要。

構成 (原点 = サーボ軸, +Z = ドームが突き出る向き):
  eye_carrier : SUBMICRO を保持する板。頭部シェル内側へ接着 [現物合わせ]
  eye_pod     : 元キャップ (φ37.7 × 高さ15.7 @150%) + 背面ネックボス。
                Head_Top の目ソケット (φ42.3 座グリ) の底へ φ30 貫通ボアを
                開け (tools/make_head_eyecut.py)、シェル内側から嵌める。
                キャップ底は座グリ床の ~1.5mm 上に浮き、ネック (φ24) が
                ボアを通る。座グリ縁との回転ギャップ ~2.3mm

組立順 (assembly.md §2.7): ホーンを**先に**ポッド背面ポケットへ共締め
(アーム穴 ×2 経由、ポッドを手に持って裏から) → サーボ中立でドット群が
ほぼ真下を向くスプライン位相を選んで軸へ押し込む。中心ビスはポッドに
塞がれて締結できないため使わない (目は無負荷。摩擦保持+必要なら微量接着。
位相の残差は Web UI の目トリム ±200µs で吸収)。

組付けスタック (タブ面 z=0 基準): ケース上面 = ABOVE_TAB 4.5 / ホーン
アーム上面 = ABOVE_TAB+HORN_HUB_H 7.7 / ポッド背面 = 7.7−(HORN_T+CLEAR)
= 6.1 → 回転するポッド背面と静止ケースのクリアランス 1.6mm (check_eye [4])。
"""
from pathlib import Path

import numpy as np
import trimesh
from manifold3d import Manifold, Mesh as MMesh

import config as C
from lib import box, cyl, rbox, servo_pocket, servo_tab_holes, export

PG = C.EYE_SERVO
MODEL = Path(__file__).resolve().parent.parent.parent / "model"

# Head_Eye_White_x3.stl の正規化変換 (実測で確定した定数, 決定的):
# キャップ軸を +Z・ドーム上向き・底面 z=0・ドット群 (3 穴) を -Y へ。
# 導出: 慣性主軸 → 軸を z へ回転 → ドーム上向き反転 → 底面/中心合わせ →
# ドット群方位を -Y へ回転。詳細はプロジェクトログ参照
CAP_NORM = np.array([
    [9.999978e-01, 1.516494e-03, 1.442459e-03, 3.815264e-04],
    [-2.092949e-03, 7.252969e-01, 6.884331e-01, 1.002399e-01],
    [-2.206572e-06, -6.884346e-01, 7.252985e-01, 1.603734e+00],
    [0.0, 0.0, 0.0, 1.0],
])


def _cap_manifold() -> Manifold:
    """元キャップを正規化 → 150% → ネック上へ載せた Manifold を返す。"""
    tm = trimesh.load(MODEL / "Head_Eye_White_x3.stl")
    tm.apply_transform(CAP_NORM)
    tm.apply_scale(1.5)
    tm.apply_translation([0.0, 0.0, C.EYE_NECK_H])
    mesh = MMesh(vert_properties=np.asarray(tm.vertices, np.float32),
                 tri_verts=np.asarray(tm.faces, np.uint32))
    return Manifold(mesh)


def eye_pod() -> Manifold:
    # ネックボス (ホーンポケット壁)。キャップへ 2mm 食い込ませて結合
    boss = cyl(C.EYE_NECK_H + 2, 24).translate([0, 0, (C.EYE_NECK_H + 2) / 2])
    m = _cap_manifold() + boss
    m -= box(80, 80, 30).translate([0, 0, -15])  # 背面 z=0 で平らに
    # ---- 負形状 (ホーンポケット: 背面 z=0 から +Z 側の材へ埋め込み)
    t = PG["HORN_T"] + C.CLEAR
    arm = rbox(PG["HORN_ARM_L"] + C.CLEAR, PG["HORN_ARM_W"] + C.CLEAR, t,
               r=2.0).translate(
        [PG["HORN_ARM_L"] / 2 - PG["HORN_ARM_W"] / 2, 0, t / 2])
    # ハブ逃げ: ハブ上面はアーム上面と面一 (lib.horn_pocket と同じ想定) の
    # ため、アーム矩形からはみ出すハブ縁がポケット深さ t のぶんポッド材へ
    # 入り込む。中心ビスを先付けした場合のビス頭も含めて +Z 側へ深めに逃がす
    hub = cyl(PG["HORN_HUB_H"] + 4, PG["HORN_HUB_D"] + 2 * C.CLEAR).translate(
        [0, 0, (PG["HORN_HUB_H"] + 4) / 2 - 0.01])
    pilots = Manifold()
    for x in (PG["HORN_ARM_L"] * 0.45, PG["HORN_ARM_L"] * 0.62):
        pilots += cyl(10, PG["HORN_PILOT_D"]).translate([x, 0, 5])  # 深さ 8
    m -= arm + hub + pilots
    # 輸入キャップとのブーリアンはスリバー面を残し STL 往復で非 watertight に
    # なるため、微小許容で簡約して除去する (体積変化 <0.1%)
    return m.simplify(0.01)


def eye_carrier() -> Manifold:
    """サーボ保持板。上面 = タブ着座面 (z=0)。頭部シェル内側へ接着。"""
    m = rbox(34, 20, 5, r=3).translate([0, 0, -2.5])
    m += rbox(48, 10, 2.5, r=2).translate([0, 0, -1.25])   # 接着ウィング
    m -= servo_pocket(PG)
    m -= servo_tab_holes(PG)
    return m


def build_all() -> dict:
    print("[eye parts] (3 目共通。eye_pod=白 / carrier=PETG)")
    parts = {"eye_pod": eye_pod(), "eye_carrier": eye_carrier()}
    return {name: export(m, name) for name, m in parts.items()}


if __name__ == "__main__":
    build_all()
