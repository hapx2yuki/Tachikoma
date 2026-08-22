#!/usr/bin/env python3
"""目 (キョロキョロ機構) の検証。v3: 元キット Head_Eye_White 形状を流用。

2026-07-28 設計変更: 中央目 (EYE_SOCKETS_150[1]) は固定カメラ目
(eye_pod_camera/camera_carrier) に置換済み — カメラ固有の検証 (FOV/瞳径/
取付角度/全アセンブリ視界) は tools/check_camera.py が担当する。本ファイルは
**左右 2 目 (キョロキョロ, SUBMICRO 駆動)** の機構検証 + 中央カメラとの
共存 (シェル内の相互クリアランス) を扱う。

 1. eye_pod の実メッシュ整合 (ホーンアームポケット/ハブ逃げ/下穴/寸法/
    ドット穴 3 つの存在)。キャップ φ37.7 は座グリ φ42.3 内で回転、
    ネック φ24 がボア φ30 を通る
 2. eye_carrier のポケット整合
 3. 幾何: ドット群 (視線マーク) の掃引径・座グリ縁上の可視性・モジュール
    奥行き vs 頭部ドーム内径・実測ソケット間隔 (3 ソケットとも対象 —
    中央目も外殻シルエットは Head_Eye_White のまま)
 4. 組付けスタック (左右目のみ): ポッド背面はホーンにぶら下がってタブ面+
    6.1mm に浮く。回転するポッド背面と静止サーボケース上面のクリアランスを
    config から検算
 5. 2 サーボ + 1 カメラの収容: 実測ソケット位置 (EYE_SOCKETS_150) で左右
    サーボ尾と中央カメラ (camera_carrier, 実メッシュ) が頭部中心付近で
    収束する。指定ロール (ケース長辺 = 接線方向) でのサーボ尾部間クリア
    ランスを実箱サンプリングで検算し、camera_carrier との干渉も実メッシュで
    確認する
"""
import sys
from pathlib import Path

import numpy as np
import trimesh

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hardware" / "src"))
import config as C  # noqa: E402

STL = ROOT / "hardware" / "stl"
OK = True
BORE = C.EYE_BORE_D      # ソケット底へ貫通させるボア (make_head_eyecut.py)
# キャップ外面ドームの球フィット (実測): 中心 z (ポッド座標) と半径 @150%
CAP_SPH_Z = -3.42 + C.EYE_NECK_H
CAP_SPH_R = 19.11


def check(cond, msg):
    global OK
    print(f"  {'OK ' if cond else 'NG '} {msg}")
    OK &= cond


print("[1] eye_pod (元キット形状) の実メッシュ整合")
pod = trimesh.load(STL / "eye_pod.stl")
check(pod.is_watertight and len(pod.split(only_watertight=False)) == 1,
      "watertight かつ単一連結体")
PG_ = C.EYE_SERVO
hub_r = PG_["HORN_HUB_D"] / 2 + C.CLEAR      # ハブ逃げの半径
void_pts = [
    (5.0, 0.0, 0.7),                    # ホーンアームポケット
    (0.0, 0.0, 3.0),                    # ハブ逃げ中心 (ビス頭も入る深さ)
    (-hub_r + 0.2, 0.0, 0.8),           # ハブ縁 (アーム矩形の外側)
]
inside = pod.contains(np.array(void_pts))
check(not inside.any(),
      f"アームポケット/ハブ逃げが空隙 (中実={int(inside.sum())})")
solid_pts = [(0.0, 0.0, PG_["HORN_HUB_H"] + 4 + 1.5)]
inside = pod.contains(np.array(solid_pts))
check(inside.all(), "ハブ逃げの底が中実 (貫通しない)")
ext = pod.extents
check(abs(max(ext[0], ext[1]) - C.EYE_CAP_D) < 0.3,
      f"キャップ径 {max(ext[0], ext[1]):.1f} = 実測 {C.EYE_CAP_D} (元パーツ流用)")
check(abs(ext[2] - (C.EYE_NECK_H + C.EYE_CAP_H)) < 0.3,
      f"全高 {ext[2]:.1f} = ネック {C.EYE_NECK_H} + キャップ {C.EYE_CAP_H}")
check(C.EYE_CAP_D <= C.EYE_SOCKET_D - 2 * 1.5,
      f"キャップ {C.EYE_CAP_D} ≤ 座グリ {C.EYE_SOCKET_D} - 3 (回転ギャップ "
      f"{(C.EYE_SOCKET_D - C.EYE_CAP_D) / 2:.1f}mm)")
check(24.0 <= BORE - 4.0,
      f"ネック φ24 ≤ ボア {BORE} - 4 (回転クリアランス片側 "
      f"{(BORE - 24) / 2:.0f}mm)")
# ドット穴 3 つ (黒く塗る視線マーク) が元メッシュから引き継がれていること
ctr = np.array([0.0, 0.0, CAP_SPH_Z])
hole_probes, dot_rxy = [], []
for d in C.EYE_DOTS_150:
    rel = np.array(d) - ctr
    u = rel / np.linalg.norm(rel)
    hole_probes.append(ctr + u * (CAP_SPH_R - 0.5))   # 表面下 0.5mm
    dot_rxy.append(np.hypot(d[0], d[1]))
inside = pod.contains(np.array(hole_probes))
check(not inside.any(), f"ドット穴 ×3 が存在 (表面下0.5mm が空隙, "
      f"中実={int(inside.sum())})")
u_ref = np.array([0.0, np.sin(np.radians(45)), np.cos(np.radians(45))])
inside = pod.contains(np.array([ctr + u_ref * (CAP_SPH_R - 0.5)]))
check(inside.all(), "ドット穴以外のドーム面は中実 (対照プローブ)")

print("[2] eye_carrier の整合")
car = trimesh.load(STL / "eye_carrier.stl")
PG = C.EYE_SERVO
cxa = PG["L"] / 2 - PG["SHAFT_OFF"]
cin = car.contains(np.array([(-cxa, 0.0, -2.0), (14.0, 7.0, -2.0)]))
check(not cin[0], "サーボケースポケットが貫通")
check(cin[1], "プレート肉が中実")

print("[3] 幾何")
sweep = 2 * float(np.mean(dot_rxy))
print(f"  ドット群の軸からの横距離 {np.round(dot_rxy, 1)} mm → "
      f"掃引径 ~{sweep:.0f} mm")
check(sweep >= 12.0, f"視線マークの掃引径 {sweep:.0f} ≥ 12 mm (キョロキョロが目立つ)")
# 可視性: ドットが座グリ縁 (リム面) より十分上にあること
rim_z = C.EYE_NECK_H + (C.EYE_SOCKET_FLOOR - C.EYE_HOVER)
dot_zmin = min(d[2] for d in C.EYE_DOTS_150)
check(dot_zmin - rim_z >= 3.0,
      f"最下ドット z={dot_zmin:.1f} がリム面 z={rim_z:.1f} より "
      f"{dot_zmin - rim_z:.1f}mm 上 (縁に隠れない)")
# モジュール奥行き: キャップ+ネック + ホーン/ハブ + サーボ + キャリア板
depth = float(pod.bounds[1][2]) + (PG["ABOVE_TAB"] + PG["HORN_HUB_H"]) \
    + PG["TAB_BELOW"] + 5.0
head_r = 62.7 - 2.5   # Head_Top 150% 内半径 (壁 ~2.5 想定, footprint 実測 62.7)
print(f"  モジュール奥行き ~{depth:.0f} mm vs 頭部ドーム内半径 ~{head_r:.0f} mm")
check(depth < head_r, "目モジュールがドーム内に収まる (中心干渉は [5] で検算)")
ctrs = np.array([c for c, _ in C.EYE_SOCKETS_150])
neigh = min(np.linalg.norm(ctrs[i] - ctrs[j])
            for i in range(3) for j in range(i + 1, 3))
check(neigh > C.EYE_CAP_D + 4,
      f"隣接ソケット中心間 (実測) {neigh:.0f} mm > キャップ径+4 (相互非接触)")

print("[4] 組付けスタック (タブ面 z=0 基準, config から検算)")
horn_top = PG["ABOVE_TAB"] + PG["HORN_HUB_H"]
pod_back = horn_top - (PG["HORN_T"] + C.CLEAR)
case_clr = pod_back - PG["ABOVE_TAB"]
print(f"  アーム上面 +{horn_top:.1f} / ポッド背面 +{pod_back:.1f} / "
      f"ケース上面 +{PG['ABOVE_TAB']:.1f}")
check(case_clr >= 1.0,
      f"回転ポッド背面とケース上面のクリアランス {case_clr:.1f} ≥ 1.0 mm")
screw_top = PG["TAB_T"] + 1.5
check(pod_back - screw_top >= 1.0,
      f"タブビス頭上面 +{screw_top:.1f} とのクリアランス "
      f"{pod_back - screw_top:.1f} ≥ 1.0 mm")

print("[5] 2 サーボ + 1 カメラの収容 (実測ソケット位置 + 指定ロールでの相互クリアランス)")
# ソケット法線は仰角 ~47° で 3 本とも頭部中心下方へ収束するため、サーボ尾
# (タブ下 TAB_BELOW=15) の間隔が最も厳しい。ロールは「ケース長辺 = 水平接線
# 方向」と規定する (assembly.md §2.7)。中央ソケットは 2026-07-28 以降
# eye_pod_camera/camera_carrier (固定, サーボなし) — 実メッシュ (hardware/
# stl/) を実際の取付姿勢 (CAM.install_rotation) で置き、左右サーボの実体
# 点群との最短距離を検算する
D_tab = (C.EYE_SOCKET_FLOOR - C.EYE_HOVER) + C.EYE_NECK_H + pod_back
print(f"  リム面→タブ面の奥行き {D_tab:.1f} mm (床 {C.EYE_SOCKET_FLOOR}"
      f" - 浮き {C.EYE_HOVER} + ネック {C.EYE_NECK_H} + 背面浮き {pod_back:.1f})")
cxa_ = PG["L"] / 2 - PG["SHAFT_OFF"]
xs = np.linspace(-cxa_ - PG["L"] / 2, -cxa_ + PG["L"] / 2, 9)
ys = np.linspace(-PG["W"] / 2, PG["W"] / 2, 5)
zs = np.linspace(-PG["TAB_BELOW"], 0.0, 7)
grid = np.array([(x, y, z) for x in xs for y in ys for z in zs])
clouds = {}
for i in (0, 2):   # 右目, 左目 (中央=1 はサーボ無し, 別途カメラとして扱う)
    sctr, n = np.array(C.EYE_SOCKETS_150[i][0]), np.array(C.EYE_SOCKETS_150[i][1])
    xhat = np.array([-n[1], n[0], 0.0])
    xhat /= np.linalg.norm(xhat)          # 水平接線 = ケース長辺の向き
    yhat = np.cross(n, xhat)
    tab = sctr - n * D_tab
    clouds[i] = tab + grid[:, :1] * xhat + grid[:, 1:2] * yhat + grid[:, 2:3] * n
names = {0: "右", 2: "左"}
worst = float(np.min(np.linalg.norm(
    clouds[0][:, None, :] - clouds[2][None, :, :], axis=2)))
print(f"  {names[0]}-{names[2]} サーボ実体間の最短距離 {worst:.1f} mm")
check(worst >= 3.0, f"左右サーボ尾部間クリアランス {worst:.1f} ≥ 3.0 mm (指定ロールで成立)")

# 中央カメラ (実メッシュ, 実際の取付回転) vs 左右サーボ実体点群
sys.path.insert(0, str(ROOT / "hardware" / "src"))
import make_camera as CAM  # noqa: E402
SETBACK = (C.EYE_SOCKET_FLOOR - C.EYE_HOVER) + C.EYE_NECK_H
ctr1, n1 = np.array(C.EYE_SOCKETS_150[1][0]), np.array(C.EYE_SOCKETS_150[1][1])
pos1 = ctr1 - n1 * SETBACK
T_cam = np.eye(4)
T_cam[:3, :3] = CAM.install_rotation(n1)
T_cam[:3, 3] = pos1
cam_pts = []
for name in ("eye_pod_camera", "camera_carrier"):
    m = trimesh.load(STL / f"{name}.stl")
    m.apply_transform(T_cam)
    pts, _ = trimesh.sample.sample_surface(m, 3000)
    cam_pts.append(pts)
cam_pts = np.vstack(cam_pts)
worst_cam = 1e9
for i in (0, 2):
    dmin = float(np.min(np.linalg.norm(
        clouds[i][:, None, :] - cam_pts[None, :, :], axis=2)))
    worst_cam = min(worst_cam, dmin)
    print(f"  中央カメラ-{names[i]} サーボ実体間の最短距離 {dmin:.1f} mm")
check(worst_cam >= 3.0,
      f"カメラ-サーボ間クリアランス {worst_cam:.1f} ≥ 3.0 mm (指定ロールで成立)")

print(f"\nresult: {'OK' if OK else 'NG'}")
sys.exit(0 if OK else 1)
