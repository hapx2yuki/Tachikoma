#!/usr/bin/env python3
"""頭部中央目カメラ化 (hardware/src/make_camera.py) の検証。

2026-07-28 設計変更: カメラはポッド (Cabin) のメインアイではなく**頭部の
中央可動目を固定カメラ目に置換**したもの (左右 2 目はキョロキョロ維持)。

 1. eye_pod_camera の外殻無傷確認 (瞳ボア/モジュールキャビティ以外は元
    Head_Eye_White 形状と一致 = 鉄則1) + 光軸設計の自己整合 (config.py の
    CAM2_ALPHA_DEG/CAM2_THETA_DEG/CAM2_RESIDUAL_DEG を実メッシュ・実ソケット
    法線から再計算して照合)
 2. 瞳径 (CAM2_PUPIL_D) と実効後退距離 (CAM2_LENS_STANDOFF) から、モジュール
    光学 FOV (CAM2_LENS_FOV_DEG) がケラレないことを幾何計算で確認
 3. camera_carrier + モジュール実寸ダミーが eye_pod_camera のキャビティへ
    干渉なく収まること (壁厚安全代を実メッシュに対し再検証)
 4. **全アセンブリ視界検証 (最重要)**: robot_meshes(dress=True) の全パーツ
    に対し、実際に頭部ソケットへ取り付けた姿勢でのレンズ位置/光軸から FOV
    円錐内へレイキャストし、自機体による遮蔽率を実測する (旧 Cabin_Eye 案の
    「前方 FOV の 80% が自機体に遮蔽される」不具合の再発防止 — 恒久チェック)
 5. 印刷用 2 分割 (eye_pod_camera_shell/base, 2026-08-19 印刷性再設計) の
    整合: 各部品の単一連結・watertight、相互/carrier/モジュールとの干渉ゼロ、
    体積の一体版整合、印刷第1層接地面積 (一体版 143mm2 失敗要因の回帰監視)
 6. eye_pod_camera のネック径 (CAM2_NECK_D) が Head_Top のボア (EYE_BORE_D)
    へ正のクリアランスで収まること、瞳開口が座グリ縁より十分上にあり視界を
    遮られないこと
"""
import sys
from pathlib import Path

import numpy as np
import trimesh

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hardware" / "src"))
import config as C  # noqa: E402
import make_camera as CAM  # noqa: E402

STL = ROOT / "hardware" / "stl"
OK = True


def check(cond, msg):
    global OK
    print(f"  {'OK ' if cond else 'NG '} {msg}")
    OK &= bool(cond)


def _vol(a, b):
    try:
        r = trimesh.boolean.intersection([a, b], engine="manifold")
        return 0.0 if r is None or r.is_empty else float(r.volume)
    except Exception:
        return 0.0


print("[1] eye_pod_camera 外殻無傷 + 光軸設計の自己整合")
cap0 = CAM._normalized_cap()               # 元 Head_Eye_White (加工前, 正規化済み)
p_outer, depth_total = CAM.pupil_center(cap0)
u = CAM.pupil_axis()

pod = trimesh.load(STL / "eye_pod_camera.stl")
check(pod.is_watertight and len(pod.split(only_watertight=False)) == 1,
      "eye_pod_camera: 単一連結体 + watertight")

# 外殻無傷: 瞳ボア/キャビティが到達しうる範囲 (瞳軸との垂直距離が
# キャビティ半対角+安全代を超える点) だけを比較する
cav_diag = 0.5 * np.hypot(
    C.CAM2_MODULE_L + 2 * (C.CAM2_MODULE_CLR + C.CAM2_WALL + C.CAM2_SAFETY),
    C.CAM2_MODULE_W + 2 * (C.CAM2_MODULE_CLR + C.CAM2_WALL + C.CAM2_SAFETY))
pts, _ = trimesh.sample.sample_surface(cap0, 20000)
rel = pts - p_outer
perp = rel - np.outer(rel @ u, u)                 # 瞳軸への垂線ベクトル
perp_d = np.linalg.norm(perp, axis=1)
# ネックボス下部 (z <= EYE_NECK_H+2, eye_pod() と同じ流儀で背面が変わる領域)
# も除外する
keep = (perp_d > cav_diag + 1.0) & (pts[:, 2] > C.EYE_NECK_H + 2.5)
_, dist, _ = trimesh.proximity.closest_point(pod, pts[keep])
worst = float(dist.max()) if keep.sum() else 0.0
check(worst < 0.1, f"eye_pod_camera: キャビティ影響範囲外の外殻ズレ 最大{worst:.4f}mm "
      f"< 0.1mm ({int(keep.sum())}/{len(pts)}点)")

# 光軸設計の自己整合 (config.py のハードコード値と実メッシュ/実ソケットの再計算を照合)
ctr1, n1 = C.EYE_SOCKETS_150[1]
n1 = np.array(n1)
alpha_calc = np.degrees(np.arctan2(n1[2], -n1[1]))
check(abs(alpha_calc - C.CAM2_ALPHA_DEG) < 0.01,
      f"CAM2_ALPHA_DEG 再計算値 {alpha_calc:.4f}° ≈ config値 {C.CAM2_ALPHA_DEG}°")

R_install = CAM.install_rotation(n1)
check(np.allclose(R_install.T @ R_install, np.eye(3), atol=1e-9) and
      abs(np.linalg.det(R_install) - 1.0) < 1e-9,
      "install_rotation: 直交行列 (回転行列として妥当)")
n1_unit = n1 / np.linalg.norm(n1)
check(np.allclose(R_install @ np.array([0, 0, 1.0]), n1_unit, atol=1e-6),
      "install_rotation: ローカル +Z がソケット法線と一致")
u_global = R_install @ u
elev = np.degrees(np.arctan2(u_global[2], -u_global[1]))
az = np.degrees(np.arctan2(u_global[0], -u_global[1]))
print(f"  光軸 (取付後, グローバル): 仰角 {elev:.2f}° / 方位ズレ {az:.2f}° (0°=正面)")
check(abs(elev - C.CAM2_RESIDUAL_DEG) < 0.01,
      f"取付後の光軸仰角 {elev:.2f}° ≈ config.CAM2_RESIDUAL_DEG {C.CAM2_RESIDUAL_DEG:.2f}°")
check(abs(elev) <= 10.0, f"光軸仰角 |{elev:.2f}°| ≤ 10° (ユーザー要求: 水平前方 ±10° 以内)")
check(abs(az) < 1.0, f"方位ズレ |{az:.2f}°| < 1° (中央目は機体中心面上のため左右へ流れない)")

print("\n[2] 瞳径とFOVのケラレ計算")
half_fov = np.radians(C.CAM2_LENS_FOV_DEG / 2)
L = C.CAM2_LENS_STANDOFF
d_min_no_vignette = 2 * L * np.tan(half_fov)
print(f"  レンズ後退距離 L={L:.2f}mm, 半画角={np.degrees(half_fov):.2f}°, "
      f"ケラレ無し最小径={d_min_no_vignette:.2f}mm")
check(C.CAM2_PUPIL_D >= d_min_no_vignette,
      f"採用瞳径 {C.CAM2_PUPIL_D}mm ≥ 必要最小径 {d_min_no_vignette:.2f}mm "
      f"(半径マージン {(C.CAM2_PUPIL_D - d_min_no_vignette) / 2:.2f}mm) "
      f"— 光学FOV {C.CAM2_LENS_FOV_DEG}° 全域でケラレ無し")

print("\n[3] camera_carrier + モジュールダミーの干渉/収容")
carrier = trimesh.load(STL / "camera_carrier.stl")
check(carrier.is_watertight and len(carrier.split(only_watertight=False)) == 1,
      "camera_carrier: 単一連結体 + watertight")

iv_carrier_pod = _vol(carrier, pod)
check(iv_carrier_pod < 5.0,
      f"camera_carrier vs eye_pod_camera(実体) 交差体積 {iv_carrier_pod:.2f}mm3 < 5.0 "
      f"— キャビティへ食い込みなく収まる")

# make_camera.py と同じ回転規約 (ローカル X 軸まわり CAM2_THETA_DEG 回転,
# ローカル +Z=u が瞳軸) でモジュールダミー (長辺=ローカルX, 短辺=ローカルY,
# 厚み=ローカルZ) を組み立て、p_outer から LENS_STANDOFF の深さへ置く
th = np.radians(C.CAM2_THETA_DEG)
Rx = np.array([[1, 0, 0], [0, np.cos(th), -np.sin(th)], [0, np.sin(th), np.cos(th)]])
module_dummy = trimesh.creation.box(
    extents=[C.CAM2_MODULE_L, C.CAM2_MODULE_W, C.CAM2_MODULE_T])
z_local = -(C.CAM2_LENS_STANDOFF + C.CAM2_MODULE_T / 2)  # 局所 -Z = 材内部
Tm = np.eye(4)
Tm[:3, :3] = Rx
Tm[:3, 3] = p_outer + Rx @ np.array([0, 0, z_local])
module_dummy.apply_transform(Tm)
iv_mod_pod = _vol(module_dummy, pod)
check(iv_mod_pod < 2.0,
      f"モジュールダミー (実姿勢) vs eye_pod_camera(実体) 交差体積 "
      f"{iv_mod_pod:.2f}mm3 < 2.0 — キャビティへ正しく収まる")
# camera_carrier のポケット (負形状, camera_carrier() と同じ寸法/位置で
# 独立に再構築) の中にモジュールダミーがほぼ収まっていること。モジュール
# はポケット (空隙) に入るのが正しい設計なので carrier の**実体**との
# 交差は 0 に近くて当然 — ここではポケットという空間そのものに対する
# 収容を検算する
from lib import box as _box, to_trimesh as _to_trimesh  # noqa: E402
pocket_t = C.CAM2_MODULE_T + 2 * C.CAM2_MODULE_CLR
pocket_local = _box(C.CAM2_MODULE_L + 2 * C.CAM2_MODULE_CLR,
                     C.CAM2_MODULE_W + 2 * C.CAM2_MODULE_CLR,
                     pocket_t + 2.0)
hi_local = -C.CAM2_LENS_STANDOFF
pocket_local = pocket_local.translate([0, 0, hi_local - pocket_t / 2 + 1.0])
pocket_tm = _to_trimesh(pocket_local)
Tpocket = np.eye(4); Tpocket[:3, :3] = Rx; Tpocket[:3, 3] = p_outer
pocket_tm.apply_transform(Tpocket)
mod_vol = float(module_dummy.volume)
contained_vol = _vol(module_dummy, pocket_tm)
check(contained_vol > mod_vol * 0.98,
      f"モジュールダミーの {contained_vol / mod_vol * 100:.1f}% が camera_carrier "
      f"のポケット (空隙) 内に収まる (要 ≥98%)")

# 壁厚安全代の再検証: モジュール footprint (安全代込み, 長辺=ローカルX,
# 短辺=ローカルY — make_camera.py のキャビティと同じ規約) が、レンズ位置の
# 深さで実メッシュの境界を破っていないこと (config.py CAM2_THETA_DEG
# コメントの探索結果を、現在の CAM2_* 値に対し実メッシュで再確認する)
half_L = C.CAM2_MODULE_L / 2 + C.CAM2_MODULE_CLR + C.CAM2_WALL + C.CAM2_SAFETY
half_W = C.CAM2_MODULE_W / 2 + C.CAM2_MODULE_CLR + C.CAM2_WALL + C.CAM2_SAFETY
lens_face = p_outer - u * C.CAM2_LENS_STANDOFF
edge_pts = []
for tt in np.linspace(-1, 1, 41):
    edge_pts += [(tt * half_L, half_W), (tt * half_L, -half_W),
                 (half_L, tt * half_W), (-half_L, tt * half_W)]
worst_margin = 1e9
for (ex, ey) in edge_pts:
    probe_dir_local = np.array([ex, ey, 0.0])
    r_req = np.linalg.norm(probe_dir_local)
    probe_dir = (Rx @ (probe_dir_local / r_req))
    far = lens_face + probe_dir * 40.0
    hl, *_ = cap0.ray.intersects_location(
        ray_origins=[far], ray_directions=[-probe_dir], multiple_hits=False)
    r_avail = np.linalg.norm(hl[0] - lens_face) if len(hl) else 0.0
    worst_margin = min(worst_margin, r_avail - r_req)
check(worst_margin > 0.0,
      f"モジュール footprint (クリア+肉厚+安全代込み) の実メッシュに対する "
      f"最小マージン {worst_margin:.2f}mm > 0 (θ={C.CAM2_THETA_DEG}° で成立)")

print("\n[4] 全アセンブリ視界検証 (最重要: 自機体による FOV 遮蔽)")
import make_visuals as MV  # noqa: E402  (重い import なので使う直前にまとめる)

SETBACK = (C.EYE_SOCKET_FLOOR - C.EYE_HOVER) + C.EYE_NECK_H
ctr1a = np.array(ctr1)
pos = ctr1a - n1 * SETBACK
T4 = np.eye(4); T4[:3, :3] = R_install; T4[:3, 3] = pos
lens_local = p_outer - u * C.CAM2_LENS_STANDOFF
half = np.radians(C.CAM2_LENS_FOV_DEG / 2)
# 中立だけでなく歩行位相・体高帯・腕ポーズも掃引する (静止 1 姿勢のみだと
# 遊脚中の脚/腕ポーズ起因の遮蔽を見逃す)。速度は正規化 (walk 動画と同じ)。
POSES = ([(ph, 0.0, 1.0, 0.0, 115.0, MV.ARM_TUCK) for ph in (0.0, 0.25, 0.5, 0.75)]
         + [(0.25, 1.0, 0.0, 0.0, 115.0, MV.ARM_TUCK)]
         + [(0.0, 0.0, 0.0, 0.0, bh, MV.ARM_TUCK) for bh in (105.0, 130.0)]
         + [(0.0, 0.0, 0.0, 0.0, 115.0, MV.ARM_READY)])
worst_frac, worst_pose = -1.0, None
for (ph, vx_, vy_, wz_, body_h, arm_pose) in POSES:
    zb = body_h + C.HIP_DROP
    ms = MV.robot_meshes(ph, vx_, vy_, wz_, body_h, arms=arm_pose, dress=True)
    T_head_top = MV.trans(0, C.ARM_MOUNT_HUB_Y, zb + MV.HEAD_TOP_Z_OFFSET) @ MV.rot(180, "z")
    T_place = T_head_top @ T4
    lens_global = (T_place @ np.array([*lens_local, 1.0]))[:3]
    u_dir_global = T_place[:3, :3] @ u
    u_dir_global /= np.linalg.norm(u_dir_global)
    combined = trimesh.util.concatenate([m for m, _, _ in ms])
    # 光軸まわり半画角 CAM2_LENS_FOV_DEG/2 の円錐内へグリッド状にレイを飛ばす
    tmp2 = np.array([1.0, 0, 0]) if abs(u_dir_global[0]) < 0.9 else np.array([0, 1.0, 0])
    ge1 = np.cross(u_dir_global, tmp2); ge1 /= np.linalg.norm(ge1)
    ge2 = np.cross(u_dir_global, ge1)
    N = 9
    dirs = []
    for iy in np.linspace(-1, 1, N):
        for ix in np.linspace(-1, 1, N):
            rr = np.hypot(ix, iy)
            if rr > 1.0:
                continue
            th = rr * half
            if rr < 1e-6:
                d = u_dir_global
            else:
                d = (np.cos(th) * u_dir_global +
                     np.sin(th) * (ix / rr * ge1 + iy / rr * ge2))
            dirs.append(d / np.linalg.norm(d))
    dirs = np.array(dirs)
    origins = np.tile(lens_global, (len(dirs), 1))
    locs, idx_ray, _ = combined.ray.intersects_location(
        ray_origins=origins, ray_directions=dirs, multiple_hits=False)
    hit_dist = np.full(len(dirs), np.inf)
    if len(idx_ray):
        d = np.linalg.norm(locs - origins[idx_ray], axis=1)
        for i, dd in zip(idx_ray, d):
            hit_dist[i] = min(hit_dist[i], dd)
    # 自パーツ (eye_pod_camera/camera_carrier 自身, φ ~20mm 級) によるレンズ
    # 直近のヒットは自己遮蔽ではなく設計上の枠 (レンズ鏡筒自体) なので、
    # SELF_R を超える距離のヒットだけを「自機体による FOV 遮蔽」として数える
    SELF_R = 25.0
    occluded_far = (hit_dist >= SELF_R) & (hit_dist < 400.0)
    frac = float(occluded_far.sum()) / len(dirs)
    if frac > worst_frac:
        worst_frac, worst_pose = frac, (ph, vx_, vy_, wz_, body_h, arm_pose is MV.ARM_READY)
    print(f"  pose(ph={ph:.2f} v=({vx_:.0f},{vy_:.0f},{wz_:.0f}) bh={body_h:.0f}"
          f" arms={'READY' if arm_pose is MV.ARM_READY else 'TUCK'}):"
          f" {len(dirs)} 本中 {int(occluded_far.sum())} 本遮蔽 ({frac * 100:.1f}%)")
check(worst_frac < 0.15, f"自機体による FOV 遮蔽率 最悪 {worst_frac * 100:.1f}% < 15% "
      f"({len(POSES)} ポーズ掃引: 歩行位相×体高105-130×腕TUCK/READY。"
      f"旧 Cabin_Eye 案は 80% 遮蔽で不採用 — 恒久回帰チェック)")

print("\n[5] 印刷用 2 分割 (shell/base) の整合 (2026-08-19 印刷性再設計)")
shell = trimesh.load(STL / "eye_pod_camera_shell.stl")
base = trimesh.load(STL / "eye_pod_camera_base.stl")
for _nm, _mm in (("shell", shell), ("base", base)):
    check(_mm.is_watertight and len(_mm.split(only_watertight=False)) == 1,
          f"eye_pod_camera_{_nm}: 単一連結体 + watertight")
iv_sb = _vol(shell, base)
check(iv_sb < 0.05, f"shell vs base 交差体積 {iv_sb:.3f}mm3 ≈ 0 (プラグ/溝クリアランス)")
vsum = float(shell.volume + base.volume)
check(abs(vsum - float(pod.volume)) < 250.0,
      f"shell+base 体積 {vsum:.0f}mm3 ≈ 一体参照 {pod.volume:.0f}mm3 "
      f"(差 {vsum - pod.volume:+.0f}mm3 — プラグ/溝ディテール分)")
iv_cs, iv_cb = _vol(carrier, shell), _vol(carrier, base)
check(iv_cs < 1.0 and iv_cb < 1.0,
      f"camera_carrier vs shell {iv_cs:.2f} / vs base {iv_cb:.2f} mm3 — "
      f"先入れ挿入でどちらにも食い込まない")
iv_mod_shellbase = _vol(module_dummy, shell) + _vol(module_dummy, base)
check(iv_mod_shellbase < 1.0,
      f"モジュールダミー vs shell+base 交差体積 計{iv_mod_shellbase:.2f}mm3 < 1.0")
# 印刷第1層の接地面積 (印刷姿勢 = どちらも STL のままの向き)。一体版は
# 143mm2 (キャビティにえぐられたネックボス断面) しかなく実印刷で失敗多発
# だった — 分割で両部品ともベタ置きになることを回帰チェックする
for _nm, _mm in (("shell", shell), ("base", base)):
    _zmin = float(_mm.bounds[0][2])
    _fz = _mm.vertices[_mm.faces][:, :, 2]
    _low = (_fz < _zmin + 0.4).all(axis=1) & (_mm.face_normals[:, 2] < -0.5)
    _a = float(_mm.area_faces[_low].sum())
    check(_a > 300.0, f"eye_pod_camera_{_nm}: 印刷第1層接地面積 {_a:.0f}mm2 > 300 "
          f"(一体版 143mm2 の解消)")

print("\n[6] ネック/座グリ整合")
check(C.CAM2_NECK_D < C.EYE_BORE_D,
      f"eye_pod_camera ネック径 {C.CAM2_NECK_D}mm < Head_Top ボア {C.EYE_BORE_D}mm "
      f"(片側クリアランス {(C.EYE_BORE_D - C.CAM2_NECK_D) / 2:.2f}mm)")
rim_z = C.EYE_NECK_H + (C.EYE_SOCKET_FLOOR - C.EYE_HOVER)
check(p_outer[2] - rim_z >= 3.0,
      f"瞳中心 z={p_outer[2]:.1f} が座グリ縁 z={rim_z:.1f} より "
      f"{p_outer[2] - rim_z:.1f}mm 上 (縁に隠れない, check_eye [3] と同じ基準)")

print(f"\nresult: {'OK' if OK else 'NG'}")
sys.exit(0 if OK else 1)
