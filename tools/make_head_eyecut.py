#!/usr/bin/env python3
"""Head_Top の印刷用加工版 STL (Head_Top_Eyecut.stl) を生成する。

v1 (2026-07-28): 目ソケット底の貫通ボア。
    元の Head_Top は目ソケット (φ42.3 座グリ @150%) の底が塞がっている
    (ブラインドポケット, STL 実測)。可動眼球をシェル内側から嵌めるには
    底へ φEYE_BORE_D の貫通穴が必要。

v2 (2026-08-22): **内部機構逃がし (内殻ホロー + ケースノッチ) を追加**。
    経緯: Head_Top は元キットのほぼ中実彫刻 (306cm³) で、確立済み配置
    (rot180z + (0, ARM_MOUNT_HUB_Y, +HEAD_TOP_Z_OFFSET)) ではプレート上
    z≈10-14 に全断面の「床」があり、実メッシュブーリアンで
      - 脚ヨー STD ケース上部 ×4 (各 5.5-5.6 cm³) + タブビス頭
      - 腕 MICRO ケース ×2 (各 0.66 cm³) + タブビス頭
      - PCA9685 board1 (1.31 cm³, スタックなら board2/プラグも)
    と交差 = 頭が物理的に載らない (落とし穴 #55 の Head_Top 版。Head_Bottom
    は 2026-08-20 に機構逃がし済みだったが Head_Top は未検証だった)。
    対処:
      (a) 内殻ホロー: 外皮を CARVE_OFFSET_MM 残して内部を彫る。カッターは
          「外形の z 柱充填 (目ボア斜坑・内部空洞を閉じる) → 3D 侵食 →
          marching cubes」のボクセル由来メッシュ。外部開放面 (目ソケット
          座グリ・前面ポケット) は侵食が自動保護するので、座グリ床リングは
          ≥壁厚で残り eye_carrier の接着座になる。床 (z<14 帯) は不可視の
          内部面として撤去対象 (パディングで侵食の保護から外す)。
          クラウン (z>CROWN_Z) は 45° コーンで彫り止め — 内部天井を
          オーバーハング 45° 以下に保ち内部サポートを不要にする。
      (b) 下部スカートを貫通するケース角/ビス頭は個別ノッチ (実測残交差の
          外接直方体 + マージン。make_head._armcut_box_right と同じ流儀)。
    検算 (恒常): ボア貫通 / ケース+ビス頭+PCA スタック包絡+chassis との
    交差 0 / スタックと新内面の実距離 / 外皮の残存 (「あるべき材」#57) /
    目ソケット座材の実在 / watertight + 単一ボディ。

実行: .venv/bin/python tools/make_head_eyecut.py   (要 scikit-image)
"""
import sys
from pathlib import Path

import numpy as np
import trimesh
from manifold3d import Manifold, Mesh as MMesh
from scipy import ndimage

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hardware" / "src"))
import config as C  # noqa: E402

SCALE = 1.5
HEAD_TOP_Z_OFFSET = 57.7   # tools/make_visuals.py HEAD_TOP_Z_OFFSET と同一定数
                           # (同ファイルが唯一の正 — 複写、要追従)

# ---- 内殻ホローのパラメータ ----
PITCH = 0.7            # ボクセルピッチ。marching cubes の面誤差 ~±0.6mm
CARVE_OFFSET_MM = 3.2  # 外皮からの彫り込みオフセット。実残壁 = 3.2±0.6 →
                       # 最小 2.6mm ≥ 目標 2.5mm (check_eye [3] の「壁 ~2.5
                       # 想定」= 目モジュール設計時の前提値と整合させる)
WALL_MIN_MM = 2.5
FLOOR_Z = 4.5          # 彫り込み下限 (chassis z)。プレート上面 4.0 + 0.5
PAD_Z = 14.0           # この高さ以下の「床」は不可視内部面として撤去対象
CROWN_Z = 50.0         # ここから上は 45° コーンで彫り止め (内部天井の印刷性)
CROWN_R = 25.0         # コーン開始半径 (z=CROWN_Z, 頭軸 (0, hub_y) 基準)
NOTCH_CLR = 1.5        # ケースノッチのクリアランス (プロジェクト標準)
BORE_PROTECT_MM = 12.0  # 目ボアの保護深さ (座グリリム中心から軸沿い)。ネックの
                        # 回転域 (床+ホバー+ネック ~11mm) を覆い、それより深部は
                        # 空洞へ併合 (build() 内コメント参照)


def _rotz(deg):
    t = np.radians(deg)
    m = np.eye(4)
    m[0, 0], m[0, 1], m[1, 0], m[1, 1] = np.cos(t), -np.sin(t), np.sin(t), np.cos(t)
    return m


def _to_manifold(tm: trimesh.Trimesh) -> Manifold:
    return Manifold(mesh=MMesh(vert_properties=np.asarray(tm.vertices, np.float32),
                               tri_verts=np.asarray(tm.faces, np.uint32)))


def _to_trimesh(m: Manifold) -> trimesh.Trimesh:
    mm = m.to_mesh()
    return trimesh.Trimesh(vertices=np.asarray(mm.vert_properties)[:, :3],
                           faces=np.asarray(mm.tri_verts), process=False)


def _ball(r_mm):
    n = int(np.ceil(r_mm / PITCH))
    g = np.mgrid[-n:n+1, -n:n+1, -n:n+1].astype(float) * PITCH
    return (g ** 2).sum(0) <= r_mm * r_mm + 1e-9


def _box(lo, hi) -> Manifold:
    lo = np.asarray(lo, float)
    hi = np.asarray(hi, float)
    return Manifold.cube(list(hi - lo), True).translate(list((lo + hi) / 2))


# ---------------------------------------------------------------- 機構ダミー
# (chassis 座標。寸法は make_chassis.py / config.py が唯一の正 — 複写、要追従)
def mech_dummies():
    """頭と共存すべき機構の占有ダミー (名前, Manifold) のリスト。"""
    out = []
    P = C.YAW_SERVO
    cx = P["L"] / 2 - P["SHAFT_OFF"]
    CASE_ANG = {"FR": 0.0, "FL": 180.0, "RL": 180.0, "RR": 0.0}
    boss_top = C.CHASSIS_T + 3.0          # 脚タブボス h3 (make_chassis BOSS_H)
    for name, (x, y) in C.HIPS.items():
        a = np.radians(CASE_ANG[name])
        ca, sa = np.cos(a), np.sin(a)
        ctr = (x - cx * ca, y - cx * sa)
        # ケース (タブ面 = ボス上面。上方 ABOVE_TAB) + クリアランス
        case = Manifold.cube([P["L"] + 2 * NOTCH_CLR, P["W"] + 2 * NOTCH_CLR,
                              boss_top + P["ABOVE_TAB"] + NOTCH_CLR], False) \
            .translate([-(P["L"] / 2 + NOTCH_CLR), -(P["W"] / 2 + NOTCH_CLR), 0]) \
            .rotate([0, 0, CASE_ANG[name]]).translate([ctr[0], ctr[1], 0])
        out.append((f"case_{name}", case))
        # タブビス頭 (タブ 3.0 + 頭 2.5 + クリア)
        hx = (-cx - P["HOLE_PITCH"] / 2, -cx + P["HOLE_PITCH"] / 2)
        hy = (-P["HOLE_SPREAD"] / 2, P["HOLE_SPREAD"] / 2)
        for dx in hx:
            for dy in hy:
                bx, by = x + dx * ca - dy * sa, y + dx * sa + dy * ca
                scr = Manifold.cylinder(boss_top + 3.0 + 2.5 + NOTCH_CLR,
                                        5.0 + NOTCH_CLR, -1, 48, False) \
                    .translate([bx, by, 0])
                out.append((f"screw_{name}_{dx:+.0f}_{dy:+.0f}", scr))
    PA = C.ARM_SERVO
    cxa = PA["L"] / 2 - PA["SHAFT_OFF"]
    arm_boss_top = C.CHASSIS_T + C.ARM_BOSS_H
    for s in (-1, 1):
        ax, ay = s * C.ARM_MOUNT_XY[0], C.ARM_MOUNT_XY[1]
        case = _box((ax - PA["W"] / 2 - NOTCH_CLR, ay - cxa - PA["L"] / 2 - NOTCH_CLR, 0),
                    (ax + PA["W"] / 2 + NOTCH_CLR, ay - cxa + PA["L"] / 2 + NOTCH_CLR,
                     arm_boss_top + PA["ABOVE_TAB"] + NOTCH_CLR))
        out.append((f"case_ARM{s:+d}", case))
        for hy_ in (-cxa - PA["HOLE_PITCH"] / 2, -cxa + PA["HOLE_PITCH"] / 2):
            scr = Manifold.cylinder(arm_boss_top + PA["TAB_T"] + 2.0 + NOTCH_CLR,
                                    4.0 + NOTCH_CLR, -1, 48, False) \
                .translate([ax, ay + hy_, 0])
            out.append((f"screw_ARM{s:+d}_{hy_:+.0f}", scr))
    return out


def pca_stack_envelope() -> Manifold:
    """PCA9685 2 段スタックの恒久占有包絡 (chassis 座標, config が唯一の正)。

    基板 + 低背部品 + サーボプラグ列 (+x 側, board2 は北詰めチャネル帯のみ)
    + I2C ヘッダ (北端のみ実装の運用ルール)。クリアランスは含まない
    (呼び出し側で距離を検査する)。
    """
    W, L, T = C.PCA_BOARD
    ys = C.PCA_STACK_Y0
    env = Manifold()
    for bz in (C.PCA_B1_Z, C.PCA_B2_Z):
        env += _box((-W / 2, ys - L / 2, bz), (W / 2, ys + L / 2, bz + T + C.PCA_LOW_COMP_H))
        # I2C (北端, 直立ピン+ジャンパ想定)
        env += _box((-9, ys + L / 2 - 5, bz + T), (9, ys + L / 2, bz + T + 15.0))
    # プラグ列: board1 は全チャネル帯、board2 は北詰め帯のみ
    env += _box((C.PCA_PLUG_X[0], ys - 28.0, C.PCA_B1_Z + T),
                (C.PCA_PLUG_X[1], ys + 28.0, C.PCA_B1_Z + T + C.PCA_PLUG_ENV_H))
    env += _box((C.PCA_PLUG_X[0], C.PCA_B2_USED_Y[0], C.PCA_B2_Z + T),
                (C.PCA_PLUG_X[1], C.PCA_B2_USED_Y[1], C.PCA_B2_Z + T + C.PCA_PLUG_ENV_H))
    return env


# ---------------------------------------------------------------- 生成
def build():
    head = trimesh.load(ROOT / "model" / "Head_Top_Blue.stl")
    head.apply_scale(SCALE)

    cutters = []
    for (ctr, n) in C.EYE_SOCKETS_150:
        ctr, n = np.array(ctr), np.array(n)
        cyl = trimesh.creation.cylinder(radius=C.EYE_BORE_D / 2, height=50.0,
                                        sections=96)
        cyl.apply_transform(trimesh.geometry.align_vectors([0, 0, 1], n))
        cyl.apply_translation(ctr)   # 円柱中心 = リム中心 → ±25mm で底を貫通
        cutters.append(cyl)
    bored = trimesh.boolean.difference([head] + cutters, engine="manifold")
    bored.process(validate=True)

    # ---- chassis 座標へ (rot180z + (0, hub_y, +57.7)) ----
    hub_y = C.ARM_MOUNT_HUB_Y
    ch = bored.copy()
    ch.apply_transform(_rotz(180))
    ch.apply_translation([0, hub_y, HEAD_TOP_Z_OFFSET])

    # ---- ボクセル場: 外形 (フラッドフィル) → 侵食 → 彫り込みマスク ----
    # 「保護すべき外皮」= 外部から到達可能な表面のみ。目ソケット座グリ・
    # 前面ポケット等の外部開放くぼみは保護され、完全内包の空洞は内部扱い。
    # 例外 2 つ:
    #   - 床 (z<PAD_Z): 下面はプレートとの隙間帯に面するが不可視の内部面 —
    #     footprint パディングで外部到達を遮断し、撤去対象にする
    #   - 目ボア斜坑の深部 (座グリ床から BORE_PROTECT_MM 超): ボア自体は外部
    #     連通だが、深部まで保護すると φ36 のチューブ材が空洞内に残り前脚
    #     ケース域まで垂れる。ネック回転部 (座グリ近傍) だけ保護し、深部は
    #     内部扱いにして空洞へ併合する
    print("[hollow] voxelize ...", flush=True)
    vg = ch.voxelized(PITCH).fill()
    M = np.asarray(vg.matrix, bool)
    orig = np.asarray(vg.translation, float)   # index(0,0,0) ボクセル中心の world
    # グリッドを下方へ拡張してからパディングする。グリッド下端が床の直下だと
    # erosion の境界 (=外部扱い) が「床の下 3.2mm」を保護してしまい、床の
    # 中央部がレンズ状に残る (2026-08-22 実装時に実測で発覚したバグ)
    n_ext = int(np.ceil((CARVE_OFFSET_MM + 2.0) / PITCH))
    M = np.pad(M, ((0, 0), (0, 0), (n_ext, 0)))
    orig = orig - np.array([0, 0, n_ext * PITCH])
    nx, ny, nz = M.shape
    zw = orig[2] + np.arange(nz) * PITCH
    k_pad = int(np.clip(round((PAD_Z - orig[2]) / PITCH), 0, nz - 1))
    # footprint は「z < PAD_Z+2 の全材料」— 床は場所により z9.7..16 に分布する
    # ため、帯スライスだけだと前方帯の床がパディングから漏れて保護され、
    # 彫り込み後に離れ小島として残る (2026-08-22 実装時に実測で発覚)
    foot = M[:, :, :min(nz, k_pad + 3)].any(axis=2)
    pad = np.zeros_like(M)
    pad[:, :, :k_pad] = foot[:, :, None]
    # ボア深部マスク (chassis 座標): 軸沿い深さ d > BORE_PROTECT_MM の円柱域
    R180_ = np.array([[-1, 0, 0], [0, -1, 0], [0, 0, 1]])
    gx = orig[0] + np.arange(nx) * PITCH
    gy = orig[1] + np.arange(ny) * PITCH
    PX, PY, PZ = np.meshgrid(gx, gy, zw, indexing="ij")
    deep = np.zeros_like(M)
    for (ctr, n) in C.EYE_SOCKETS_150:
        c = R180_ @ np.array(ctr) + np.array([0, hub_y, HEAD_TOP_Z_OFFSET])
        u = -(R180_ @ np.array(n))            # 内向き単位ベクトル
        dx_, dy_, dz_ = PX - c[0], PY - c[1], PZ - c[2]
        d = dx_ * u[0] + dy_ * u[1] + dz_ * u[2]
        rho2 = (dx_ - d * u[0]) ** 2 + (dy_ - d * u[1]) ** 2 + (dz_ - d * u[2]) ** 2
        deep |= (d > BORE_PROTECT_MM) & (d < 30.0) & \
                (rho2 <= (C.EYE_BORE_D / 2 + 0.8) ** 2)
    solid = M | pad
    lbl, _ = ndimage.label(~(solid | deep))
    edge_labels = np.unique(np.concatenate([
        lbl[0, :, :].ravel(), lbl[-1, :, :].ravel(),
        lbl[:, 0, :].ravel(), lbl[:, -1, :].ravel(),
        lbl[:, :, 0].ravel(), lbl[:, :, -1].ravel()]))
    edge_labels = edge_labels[edge_labels != 0]
    OUTSIDE = np.isin(lbl, edge_labels)
    Opad = ~OUTSIDE                            # 外形 (内包空洞・床・ボア深部込み)
    print("[hollow] erosion ...", flush=True)
    CARVE = ndimage.binary_erosion(Opad, structure=_ball(CARVE_OFFSET_MM))
    # 下限 z / クラウン 45° コーン
    CARVE &= (zw >= FLOOR_Z)[None, None, :]
    gx = orig[0] + np.arange(nx) * PITCH
    gy = orig[1] + np.arange(ny) * PITCH
    rr = np.sqrt(gx[:, None] ** 2 + (gy[None, :] - hub_y) ** 2)
    cone_r = CROWN_R - np.maximum(zw - CROWN_Z, 0.0)
    CARVE &= (rr[:, :, None] <= cone_r[None, None, :]) | (zw <= CROWN_Z)[None, None, :]
    print(f"[hollow] carve mask {CARVE.sum() * PITCH**3 / 1000:.0f} cm3", flush=True)

    # marching cubes → カッター Manifold
    from trimesh.voxel import ops as vops
    carve_tm = vops.matrix_to_marching_cubes(CARVE, pitch=PITCH)
    carve_tm.apply_translation(orig)
    carve = _to_manifold(carve_tm).simplify(0.1)

    head_m = _to_manifold(ch)
    out = head_m - carve

    # ---- ケース/ビス頭ノッチ (残交差の外接直方体 + 1.0mm) ----
    notches = []
    for name, dm in mech_dummies():
        inter = out ^ dm
        v = inter.volume()
        if v > 8.0:   # [3] の合格閾値 (10mm3) より小さく取る
            bb = inter.bounding_box()
            lo = np.array(bb[:3]) - 1.0
            hi = np.array(bb[3:]) + 1.0
            out = out - _box(lo, hi)
            notches.append((name, v, lo, hi))
    for name, v, lo, hi in notches:
        print(f"[notch] {name}: 残交差 {v / 1000:.2f} cm3 → "
              f"box x{lo[0]:.0f}..{hi[0]:.0f} y{lo[1]:.0f}..{hi[1]:.0f} "
              f"z{lo[2]:.0f}..{hi[2]:.0f}")

    out = out.simplify(0.02)
    # 分離片: ホロー+ノッチの残渣スリバーのみ許容 (大きな分離は設計エラー)
    parts = sorted(out.decompose(), key=lambda p: p.volume(), reverse=True)
    dropped = sum(p.volume() for p in parts[1:])
    for p in parts[1:6]:
        bb = p.bounding_box()
        print(f"[debris] {p.volume():.0f} mm3  x{bb[0]:.0f}..{bb[3]:.0f} "
              f"y{bb[1]:.0f}..{bb[4]:.0f} z{bb[2]:.0f}..{bb[5]:.0f}")
    assert dropped < 400.0, f"内殻ホローで大きな分離片: {dropped:.0f} mm3"
    if len(parts) > 1:
        print(f"[hollow] 分離スリバー {len(parts)-1} 個 ({dropped:.0f} mm3) を除去")
    out = parts[0]

    # ---- chassis → ローカル座標へ戻して出力 ----
    out_local = out.translate([0, -hub_y, -HEAD_TOP_Z_OFFSET]).rotate([0, 0, -180])
    return bored, out_local, out


if __name__ == "__main__":
    bored, out_local, out_ch = build()
    tm_local = _to_trimesh(out_local)
    if not tm_local.is_watertight:
        tm_local = _to_trimesh(_to_manifold(tm_local))
    dst = ROOT / "hardware" / "stl" / "Head_Top_Eyecut.stl"
    tm_local.export(dst)
    # STL 往復後の再読込で最終検証 (lib.export と同じ発想)
    reread = trimesh.load(dst)
    e = reread.extents
    print(f"saved {dst}")
    print(f"  {e[0]:.1f} x {e[1]:.1f} x {e[2]:.1f} mm  "
          f"watertight={reread.is_watertight}  vol={reread.volume / 1000:.0f}cm3 "
          f"(ボア加工後の中実 {bored.volume / 1000:.0f}cm3)")
    ok = True

    # [1] ボア貫通 (v1 からの検算)
    probes = [np.array(ctr) - np.array(n) * (C.EYE_SOCKET_FLOOR + 1.0)
              for (ctr, n) in C.EYE_SOCKETS_150]
    inside = reread.contains(np.array(probes))
    ok &= not inside.any()
    print(f"  [1] ボア貫通プローブ: {'OK' if not inside.any() else 'NG'} "
          f"(中実={int(inside.sum())}/3)")

    # [2] chassis / 機構ダミー / PCA スタック包絡との交差 (chassis 座標)
    import make_chassis  # noqa: E402
    chassis_m = make_chassis.chassis()
    v = (out_ch ^ chassis_m).volume()
    ok &= v < 10.0
    print(f"  [2] ∩ chassis = {v / 1000:.3f} cm3 ({'OK' if v < 10 else 'NG'})")
    worst = 0.0
    for name, dm in mech_dummies():
        worst = max(worst, (out_ch ^ dm).volume())
    ok &= worst < 10.0
    print(f"  [3] ∩ サーボケース/ビス頭ダミー (クリア {NOTCH_CLR}mm 込) "
          f"worst = {worst / 1000:.3f} cm3 ({'OK' if worst < 10 else 'NG'})")
    env = pca_stack_envelope()
    inter4 = out_ch ^ env
    v = inter4.volume()
    ok &= v < 10.0
    print(f"  [4] ∩ PCA スタック包絡 = {v / 1000:.3f} cm3 ({'OK' if v < 10 else 'NG'})")
    if v >= 10.0:
        bb = inter4.bounding_box()
        print(f"      違反域 x{bb[0]:.0f}..{bb[3]:.0f} y{bb[1]:.0f}..{bb[4]:.0f} "
              f"z{bb[2]:.0f}..{bb[5]:.0f}")

    # [4b] スタック包絡と新内面の実距離 (>= 1.5mm)
    from scipy.spatial import cKDTree
    head_pts, _ = trimesh.sample.sample_surface(_to_trimesh(out_ch), 30000)
    env_pts, _ = trimesh.sample.sample_surface(_to_trimesh(env), 8000)
    dmin = float(cKDTree(head_pts).query(env_pts)[0].min())
    ok &= dmin >= 1.5
    print(f"  [4b] スタック包絡 → 頭内面 実距離 min = {dmin:.2f} mm "
          f"({'OK' if dmin >= 1.5 else 'NG'}, 要 >=1.5)")

    # [5] 外皮の残存 (「あるべき材」#57): ボア加工後メッシュの外表面直下に
    # 材が残っているか。意図的撤去域 (床 z<20 のノッチ/スカート帯, ボア斜坑の
    # 深部 = 保護解除域) のサンプルは除外する
    base_ch = bored.copy()
    base_ch.apply_transform(_rotz(180))
    base_ch.apply_translation([0, C.ARM_MOUNT_HUB_Y, HEAD_TOP_Z_OFFSET])
    pts, fidx = trimesh.sample.sample_surface(base_ch, 6000)
    nrm = base_ch.face_normals[fidx]
    keep = pts[:, 2] > 20.0
    R180_ = np.array([[-1, 0, 0], [0, -1, 0], [0, 0, 1]])
    for (ctr, n) in C.EYE_SOCKETS_150:
        c = R180_ @ np.array(ctr) + np.array([0, C.ARM_MOUNT_HUB_Y, HEAD_TOP_Z_OFFSET])
        u = -(R180_ @ np.array(n))
        rel = pts - c
        d = rel @ u
        rho = np.linalg.norm(rel - d[:, None] * u[None, :], axis=1)
        keep &= ~((d > BORE_PROTECT_MM - 2.0) & (rho < C.EYE_BORE_D / 2 + 2.5))
    p_in = pts[keep] - nrm[keep] * 1.25
    out_tm_ch = _to_trimesh(out_ch)
    frac = out_tm_ch.contains(p_in).mean()
    ok &= frac >= 0.985
    print(f"  [5] 外皮残存 (深さ1.25mm, z>20, 意図的撤去域除く): {frac * 100:.1f}% "
          f"({'OK' if frac >= 0.985 else 'NG'}, 要 >=98.5%)")
    p_in2 = pts[keep] - nrm[keep] * (WALL_MIN_MM - 0.3)
    frac2 = out_tm_ch.contains(p_in2).mean()
    print(f"      参考: 深さ{WALL_MIN_MM - 0.3:.1f}mm 残存 {frac2 * 100:.1f}%")

    # [6] 目ソケット座材の実在 (#57): ボア壁を囲むアニュラス (r15.5..20,
    # 軸沿い深さ FLOOR+0.5..FLOOR+5.5) の材料量。座グリ床リング+ボア保護壁が
    # 残っていれば ~2 cm3 (r14 球では球がボア空洞に内包され常に 0 になる —
    # 測定設計を較正してから使う, 落とし穴 #57)
    for i, (ctr, n) in enumerate(C.EYE_SOCKETS_150):
        ctr, n = np.array(ctr), np.array(n)
        ann = (Manifold.cylinder(5.0, 20.0, -1, 64, True) -
               Manifold.cylinder(7.0, C.EYE_BORE_D / 2 + 0.5, -1, 64, True))
        R = trimesh.geometry.align_vectors([0, 0, 1], -n)[:3, :3]
        # Manifold には任意回転が無いので trimesh 経由で配置
        ann_tm = _to_trimesh(ann)
        T = np.eye(4)
        T[:3, :3] = R
        T[:3, 3] = ctr - n * (C.EYE_SOCKET_FLOOR + 3.0)
        ann_tm.apply_transform(T)
        seat = (out_local ^ _to_manifold(ann_tm)).volume() / 1000
        ok &= seat >= 1.0
        print(f"  [6] 目ソケット{i} 座材 (ボア周アニュラス) {seat:.2f} cm3 "
              f"({'OK' if seat >= 1.0 else 'NG'}, 要 >=1.0)")

    # [7] SW ゾーン参考 (電装候補地の空き具合レポート, assert しない)
    sw = _box((38, 21, 4.5), (58, 35, 17))
    print(f"  [7] 参考: SW 想定ゾーン (48,28) ∩ 頭 = "
          f"{(out_ch ^ sw).volume() / 1000:.2f} cm3 (0 なら現配置のまま可)")

    print(f"\nresult: {'OK' if ok else 'NG'}")
    sys.exit(0 if ok else 1)
