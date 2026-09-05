#!/usr/bin/env python3
"""砲身内オーディオクレードル (hardware/src/make_audio.py) の検証。

 1. Mouth_Cannon 実測の再検証: config.py の断面ブレークポイント (CANNON_Y_*)
    が実メッシュ (150%) と一致するか自動スキャンで再確認 (config 側の手打ち定数
    が元 STL からドリフトしていないことを保証する。check_arm.py の firmware
    実測値クロスチェックと同じ思想)
 2. 外殻無傷確認: Cannon/Neck/Ball の加工版が、意図したポケット/ボア以外では
    元メッシュの外側表面と一致すること (加工は内部のみ = 鉄則1)
 3. スピーカー実機ダミー (φ20 × AUDIO_SPK_REAL_H, 砲口側) が Cannon_Bored の
    意匠シルエット内に収まり、抜け止めワッシャ (audio_cradle_spk, 奥側) とも
    干渉しないこと
 4. マイク基板ダミー (L×W×T) が Cannon_Bored の殻内に収まり、
    audio_cradle_mic とも干渉しないこと
 5. 最小肉厚 (マイク/スピーカーポケット周り) が確保されていること
 6. 配線経路の連通: マイクポケット後端から Mouth_Cannon 後端まで、および
    Mouth_Neck_Bored / Mouth_Ball_Bored の貫通ボアが塞がっていないこと
 7. Mouth_Cap (元パーツ, 無加工) の前面開口がスピーカーポート (拡径した砲口)
    を塞がないこと (簡易確認, 現物合わせ前提)
"""
import sys
from pathlib import Path

import numpy as np
import trimesh

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hardware" / "src"))
import config as C  # noqa: E402

MODEL = ROOT / "model"
STL = ROOT / "hardware" / "stl"
OK = True


def check(cond, msg):
    global OK
    print(f"  {'OK ' if cond else 'NG '} {msg}")
    OK &= bool(cond)


def _scaled(name: str) -> trimesh.Trimesh:
    m = trimesh.load(MODEL / f"{name}.stl")
    m.apply_scale(C.SCALE)
    return m


def _radii_at_y(mesh: trimesh.Trimesh, y: float):
    """y 平面での断面ループごとの (外径半径, 内径半径|None) と本数。

    頂点ベースの半径 (中心=ロボット/STL 共通のY軸) を使う — bbox 近似より
    スロット区間の C 字断面で正確 (tools 実測時に確認済み)。
    """
    sec = mesh.section(plane_origin=[0, y, 0], plane_normal=[0, 1, 0])
    if sec is None:
        return []
    out = []
    for loop in sec.discrete:
        pts = np.asarray(loop)
        r = np.hypot(pts[:, 0], pts[:, 2])
        out.append((r.min(), r.max()))
    return out


def _is_slot_section(mesh: trimesh.Trimesh, y: float) -> bool:
    """側面グリップスリット区間の判定。

    普通の丸穴断面は「外周ループ (半径ほぼ一定)」+「内周ループ (半径ほぼ一定)」
    の2本 (=discrete loop 数だけではスロット区間と区別できない — どちらも2本)。
    スロット区間は外周から内周まで繋がった C字ループになるため、ループ内の
    半径レンジ (max-min) が大きくなる — これで判定する (実測: 通常 <0.1mm,
    スロット区間 ~4.5mm)。
    """
    sec = mesh.section(plane_origin=[0, y, 0], plane_normal=[0, 1, 0])
    if sec is None:
        return False
    for loop in sec.discrete:
        pts = np.asarray(loop)
        r = np.hypot(pts[:, 0], pts[:, 2])
        if r.max() - r.min() > 2.0:
            return True
    return False


print("[1] Mouth_Cannon 実測ブレークポイントの再検証 (config.py と実メッシュの一致)")
cannon0 = _scaled("Mouth_Cannon_Grey")
lo, hi = cannon0.bounds
check(abs(lo[1] - C.CANNON_Y_REAR) < 0.1, f"後端 {lo[1]:.2f} = CANNON_Y_REAR {C.CANNON_Y_REAR}")
check(abs(hi[1] - C.CANNON_Y_TIP) < 0.1, f"砲口面 {hi[1]:.2f} = CANNON_Y_TIP {C.CANNON_Y_TIP}")

ys = np.arange(lo[1] + 0.05, hi[1] - 0.05, 0.1)
is_slot = np.array([_is_slot_section(cannon0, y) for y in ys])
slot_idx = np.where(is_slot)[0]
check(len(slot_idx) > 0, "側面グリップスリット区間を検出")
if len(slot_idx):
    slot_lo, slot_hi = ys[slot_idx[0]], ys[slot_idx[-1]]
    check(abs(slot_lo - C.CANNON_Y_SLOT_LO) < 0.3,
          f"スリット始端 {slot_lo:.2f} ≈ CANNON_Y_SLOT_LO {C.CANNON_Y_SLOT_LO}")
    check(abs(slot_hi - C.CANNON_Y_SLOT_HI) < 0.3,
          f"スリット終端 {slot_hi:.2f} ≈ CANNON_Y_SLOT_HI {C.CANNON_Y_SLOT_HI}")

# 外径がフレア段差で急拡大する y (収縮前の外径 ~19.6-19.9mm → 25mm 超) を検出
outer_r = np.array([max(r[1] for r in _radii_at_y(cannon0, y)) for y in ys])
jump_idx = np.where(np.diff(outer_r) > 1.0)[0]   # 0.1mm ステップで 1mm 超ジャンプ
check(len(jump_idx) > 0, "フレア段差 (外径急拡大) を検出")
if len(jump_idx):
    collar_y = ys[jump_idx[0] + 1]
    check(abs(collar_y - C.CANNON_Y_COLLAR) < 0.3,
          f"フレア段差 {collar_y:.2f} ≈ CANNON_Y_COLLAR {C.CANNON_Y_COLLAR}")
    # マージン込みのスピーカー始端で外径が φ20+安全代を満たすこと
    r_at_spk0 = max(r[1] for r in _radii_at_y(cannon0, C.AUDIO_SPK_Y0))
    check(r_at_spk0 >= C.AUDIO_SPK_D / 2 + 1.0,
          f"AUDIO_SPK_Y0={C.AUDIO_SPK_Y0} での外径 {r_at_spk0*2:.1f} ≥ "
          f"スピーカー径+2mm ({C.AUDIO_SPK_D + 2})")

print("\n[2] 外殻無傷確認 (加工は内部のみ、ポケット/ボア以外は元メッシュの表面と一致)")


def _shell_unchanged(orig: trimesh.Trimesh, bored_name: str, exclude_fn, label: str,
                      n=15000, tol=0.05):
    bored = trimesh.load(STL / f"{bored_name}.stl")
    check(len(bored.split(only_watertight=False)) == 1 and bored.is_watertight,
          f"{label}: 単一連結体 + watertight")
    pts, _ = trimesh.sample.sample_surface(orig, n)
    keep = ~exclude_fn(pts)
    _, dist, _ = trimesh.proximity.closest_point(bored, pts[keep])
    worst = float(dist.max()) if len(dist) else 0.0
    check(worst < tol, f"{label}: 加工域外の外殻ズレ 最大{worst:.4f}mm < {tol}mm "
          f"({keep.sum()}/{n}点)")


_shell_unchanged(
    cannon0, "Mouth_Cannon_Bored",
    lambda p: ((p[:, 1] > C.AUDIO_MIC_Y0 - 0.5) & (p[:, 1] < C.AUDIO_MIC_Y1 + 0.5)) |
              (p[:, 1] > C.AUDIO_SPK_Y0 - 0.5),
    "Mouth_Cannon_Bored")

neck0 = _scaled("Mouth_Neck_Blue")
# 元 Ball の実頂点から隠れる座の範囲を独立に復元する。
_ball_vertices = _scaled("Mouth_Ball_Grey").vertices
_seat_center = np.linalg.lstsq(
    np.column_stack((2 * _ball_vertices, np.ones(len(_ball_vertices)))),
    np.square(_ball_vertices).sum(axis=1), rcond=None)[0][:3]
_seat_radius = np.linalg.norm(_ball_vertices - _seat_center, axis=1).max() + C.MOUTH_BALL_SEAT_CLEAR
_seat_center[1] += C.MOUTH_BALL_LOCAL_Y - C.MOUTH_NECK_LOCAL_Y
_cap_for_seat = _scaled("Mouth_Cap_Grey")
_cap_for_seat.apply_translation([0, C.MOUTH_CAP_LOCAL_Y - C.MOUTH_NECK_LOCAL_Y, 0])
_cap_outer_envelope = _cap_for_seat.convex_hull


def _hidden_neck_seat(p):
    # STL 簡略化の公差 0.01mm に対し 0.02mm の境界帯を持たせる。
    ball_hidden = np.linalg.norm(p - _seat_center, axis=1) <= _seat_radius + .02
    # 挿入掃引で削る先端は元Capの外周包絡の内側に限定する。生成した負形状
    # 自体を正解にせず、別の距離計算でCapの外形を超えないことを確認する。
    cap_hidden = trimesh.proximity.signed_distance(_cap_outer_envelope, p) >= -C.MOUTH_CAP_SEAT_CLEAR-.02
    return ball_hidden | cap_hidden


_shell_unchanged(neck0, "Mouth_Neck_Bored",
                 lambda p: (np.hypot(p[:, 0], p[:, 2]) < 3.5) | _hidden_neck_seat(p),
                 "Mouth_Neck_Bored")

ball0 = _scaled("Mouth_Ball_Grey")
_shell_unchanged(ball0, "Mouth_Ball_Bored",
                 lambda p: np.hypot(p[:, 0], p[:, 2]) < 3.5, "Mouth_Ball_Bored")


print("\n[2b] 配線ボアの開口が極キャップ (半径<3.5mm) の外に漏れていないこと")
# 上の _shell_unchanged は半径<3.5mm を無条件で除外しているため、ボアの突き
# 破りが本当にその範囲に収まっているか (=ボールジョイントの可動域である側面
# まで漏れていないか) はこれまで未検証だった。境界のすぐ外側 (半径
# 3.5-6.0mm, 除外域とほぼ同じ Y 範囲 = 極キャップ近傍) で外殻が本当に無傷で
# あることを明示的に確認する。将来 AUDIO_WIRE_BORE_D の拡大や配置ズレで
# ボアが極キャップより外側まで漏れる回帰が起きても、この帯で検出できる


def _pole_breach_confined(orig: trimesh.Trimesh, bored_name: str, label: str,
                           band=(3.5, 6.0), n=15000, tol=0.05, exclude_fn=None):
    bored = trimesh.load(STL / f"{bored_name}.stl")
    pts, _ = trimesh.sample.sample_surface(orig, n)
    r = np.hypot(pts[:, 0], pts[:, 2])
    keep = (r >= band[0]) & (r <= band[1])
    if exclude_fn is not None:
        keep &= ~exclude_fn(pts)
    check(keep.sum() > 50, f"{label}: 境界帯 (半径{band}) に十分なサンプル点"
          f" ({keep.sum()}点)")
    if keep.sum() == 0:
        return
    _, dist, _ = trimesh.proximity.closest_point(bored, pts[keep])
    worst = float(dist.max())
    check(worst < tol, f"{label}: 配線ボアの開口は半径{band[0]}mm以内 (極キャップ) "
          f"に収まる (境界帯 半径{band} 最大ズレ {worst:.4f}mm < {tol}mm, "
          f"{keep.sum()}点)")


_pole_breach_confined(neck0, "Mouth_Neck_Bored", "Mouth_Neck_Bored", exclude_fn=_hidden_neck_seat)
_pole_breach_confined(ball0, "Mouth_Ball_Bored", "Mouth_Ball_Bored")


def _vol(a, b):
    from mesh_checks import intersection_volume_mm3
    return intersection_volume_mm3(a, b) / 1.0


def _max_outer_radius(mesh: trimesh.Trimesh, y: float) -> float:
    """y断面での外周半径 (元メッシュの意匠シルエット, 頂点ベース)。"""
    rs = _radii_at_y(mesh, y)
    return max((r[1] for r in rs), default=0.0)


def _check_within_silhouette(label: str, y0: float, y1: float, radius: float,
                              margin: float = 0.3, n=12):
    """[y0,y1] の円柱側面が全域で元 Cannon の意匠シルエット内 (半径的に) に
    収まっているか。内部の空洞 (元々の丸穴) は無視して「外殻を突き破らないか」
    だけを見る — 元メッシュの体積ブーリアンで見ると内部の空洞を「外側」と
    誤判定するため (実際に踏んだ不具合)、半径プロファイルの直接比較にする。
    """
    worst = 1e9
    for y in np.linspace(y0 + 0.05, y1 - 0.05, n):
        worst = min(worst, _max_outer_radius(cannon0, y) - radius)
    check(worst >= margin, f"{label}: 意匠シルエットに対する最小クリアランス "
          f"{worst:.2f}mm ≥ {margin}mm")


print("\n[3] スピーカー: 実機ダミー (φ{:.0f}×{:.1f}mm, 砲口側) + "
      "抜け止めワッシャ (奥側)".format(C.AUDIO_SPK_D, C.AUDIO_SPK_REAL_H))
cannon_bored = trimesh.load(STL / "Mouth_Cannon_Bored.stl")

# 実機ダミーは砲口側 (AUDIO_SPK_REAL_H) に密着させる。ポケット径=スピーカー径
# ちょうどなので、意匠シルエット (元メッシュ) を突き破らないかで確認する
spk_y0, spk_y1 = C.AUDIO_SPK_Y1 - C.AUDIO_SPK_REAL_H, C.AUDIO_SPK_Y1
yc_spk = (spk_y0 + spk_y1) / 2
spk_dummy = trimesh.creation.cylinder(radius=C.AUDIO_SPK_D / 2, height=C.AUDIO_SPK_REAL_H,
                                       sections=64)
spk_dummy.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0]))
spk_dummy.apply_translation([0, yc_spk, 0])

iv = _vol(spk_dummy, cannon_bored)
check(iv < 1.0, f"スピーカーダミー vs Cannon_Bored 残存殻 交差体積 {iv:.2f}mm3 < 1.0")
_check_within_silhouette("スピーカーダミー", spk_y0, spk_y1, C.AUDIO_SPK_D / 2)

# 抜け止めワッシャ: スピーカーの奥 (AUDIO_SPK_BAFFLE_H 区間) に設置。
# スピーカーダミーとは Y 方向で分離しているので直接は干渉しないが、念のため確認
cradle_spk = trimesh.load(STL / "audio_cradle_spk.stl")
yc_baffle = C.AUDIO_SPK_Y0 + C.AUDIO_SPK_BAFFLE_H / 2
cradle_spk_w = cradle_spk.copy()
cradle_spk_w.apply_translation([0, yc_baffle, 0])
iv2 = _vol(spk_dummy, cradle_spk_w)
check(iv2 < 1.0, f"スピーカーダミー vs audio_cradle_spk(ワッシャ) 交差体積 "
      f"{iv2:.2f}mm3 < 1.0 (Y方向で分離)")
iv3 = _vol(cradle_spk_w, cannon_bored)
check(iv3 < 5.0, f"audio_cradle_spk vs Cannon_Bored 残存殻 交差体積 {iv3:.2f}mm3 < 5.0 "
      "(圧入クリアランス内)")

print("\n[4] マイク基板ダミー ({:.0f}×{:.0f}×{:.0f}mm) の収容".format(
    C.AUDIO_MIC_L, C.AUDIO_MIC_W, C.AUDIO_MIC_T))
yc_mic = (C.AUDIO_MIC_Y0 + C.AUDIO_MIC_Y1) / 2
mic_dummy = trimesh.creation.box(extents=[C.AUDIO_MIC_L, C.AUDIO_MIC_W, C.AUDIO_MIC_T])
mic_dummy.apply_translation([0, yc_mic, 0])

iv = _vol(mic_dummy, cannon_bored)
check(iv < 1.0, f"マイク基板ダミー vs Cannon_Bored 残存殻 交差体積 {iv:.2f}mm3 < 1.0")
# 基板の対角半径 (中心から角までの距離) が意匠シルエットに収まること
mic_diag_r = float(np.hypot(C.AUDIO_MIC_L / 2, C.AUDIO_MIC_T / 2))
_check_within_silhouette("マイク基板ダミー(対角)", C.AUDIO_MIC_Y0, C.AUDIO_MIC_Y1, mic_diag_r)

cradle_mic = trimesh.load(STL / "audio_cradle_mic.stl")
cradle_mic_w = cradle_mic.copy()
cradle_mic_w.apply_translation([0, yc_mic, 0])
iv2 = _vol(mic_dummy, cradle_mic_w)
check(iv2 < 1.0, f"マイク基板ダミー vs audio_cradle_mic 交差体積 {iv2:.2f}mm3 < 1.0 "
      "(トレイの溝内に収まる)")
iv3 = _vol(cradle_mic_w, cannon_bored)
check(iv3 < 5.0, f"audio_cradle_mic vs Cannon_Bored 残存殻 交差体積 {iv3:.2f}mm3 < 5.0 "
      "(圧入クリアランス内)")

print("\n[5] 最小肉厚 (元メッシュの外周半径プロファイル - ポケット半径)")
# [3]/[4] の意匠シルエットクリアランス確認と同じ一次データ (元メッシュの実測
# 外周半径) を使う。ポケット壁面の最近傍点探索 (closest_point) は「壁面上の
# 点から壁面への距離=0」を返すだけで肉厚にならない (実際に踏んだ不具合) ため、
# 半径プロファイルの直接差分で計算する
ys_mic = np.linspace(C.AUDIO_MIC_Y0 + 0.05, C.AUDIO_MIC_Y1 - 0.05, 20)
w_mic = min(_max_outer_radius(cannon0, y) for y in ys_mic) - C.AUDIO_MIC_D / 2
check(w_mic >= 1.2, f"マイクポケット周り最小肉厚 {w_mic:.2f}mm ≥ 1.2mm")

ys_spk = np.linspace(C.AUDIO_SPK_Y0 + 0.05, C.AUDIO_SPK_Y1 - 0.05, 20)
w_spk = min(_max_outer_radius(cannon0, y) for y in ys_spk) - C.AUDIO_SPK_D / 2
check(w_spk >= 1.2, f"スピーカーポケット周り最小肉厚 {w_spk:.2f}mm ≥ 1.2mm")

print("\n[5b] audio_cradle_mic/spk 自体の最小肉厚 (輸出済み STL への ray-casting,"
      " [5]とは独立の測定手段)")
# [5] は「Cannon の殻がポケットの外にどれだけ残るか」だけを見ており、クレードル
# 部品自身 (圧入される側) の肉厚は未検証だった。audio_cradle_mic は基板トレイの
# スロットが円筒本体を軸方向全長にわたって横断する形状のため、円筒の外周との
# 間に残る肉が両脇の細いリブだけになり、実際に 0.4mm ノズルの最小壁厚を割り
# 込む不具合を実際に発生させて発見した (2026-07-28)。表面点から法線方向内側へ
# レイを飛ばし最初にヒットする対面までの距離=局所肉厚とする、実装から独立の
# 検証手段 (mesh の三角形情報のみを使う。config の数式は一切使わない)


def _raycast_thickness(mesh: trimesh.Trimesh, mask=None, n=20000, eps=1e-3):
    """表面サンプル点から内側法線方向にレイを飛ばし、対面までの距離 (局所肉厚)
    を返す。mask(pts)->bool配列 で対象点を絞り込める。
    """
    pts, face_idx = trimesh.sample.sample_surface(mesh, n)
    if mask is not None:
        keep = mask(pts)
        pts, face_idx = pts[keep], face_idx[keep]
    if len(pts) == 0:
        return np.array([])
    normals = mesh.face_normals[face_idx]
    origins = pts - normals * eps
    locs, idx_ray, _ = mesh.ray.intersects_location(
        origins, -normals, multiple_hits=False)
    if len(locs) == 0:
        return np.array([])
    return np.linalg.norm(locs - origins[idx_ray], axis=1)


cradle_mic0 = trimesh.load(STL / "audio_cradle_mic.stl")
# 基板トレイ両脇のリブは局所 Z≈±tray_t/2 (中心付近) にできる。局所 Z が極
# (≈±od/2, マイクポート穴/回転キーの開口境界) に近い点は除外する — 穴の
# 開口縁は ray-cast 法だと必然的に肉厚→0 に漸近する測定アーチファクトが出る
# (どんな穴でも縁ギリギリの点を拾えば厚み0に近づくのは当然で、構造欠陥では
# ない。実際に確認済み: 除外なしだと極 (旧設計時点で既に存在, マイクポート
# 穴の開口縁) で 0.04mm 程度を拾ってしまい、本来見たいリブの薄さと区別が
# つかなくなる)
rib_mask = lambda p: np.abs(p[:, 2]) < 3.0  # noqa: E731
dist_rib = _raycast_thickness(cradle_mic0, rib_mask)
check(len(dist_rib) > 100, "audio_cradle_mic: リブ領域のレイヒット十分数を確保")
if len(dist_rib):
    check(dist_rib.min() >= 0.7,
          f"audio_cradle_mic: 基板トレイ両脇リブの実測最小肉厚(ray-cast) "
          f"{dist_rib.min():.3f}mm ≥ 0.7mm ({len(dist_rib)}点)")

cradle_spk0 = trimesh.load(STL / "audio_cradle_spk.stl")
dist_spk = _raycast_thickness(cradle_spk0, n=8000)
check(len(dist_spk) > 100 and dist_spk.min() >= 0.7,
      f"audio_cradle_spk: 実測最小肉厚(ray-cast) "
      f"{(dist_spk.min() if len(dist_spk) else float('nan')):.3f}mm ≥ 0.7mm "
      f"({len(dist_spk)}点)")

print("\n[5c] audio_cradle_mic 回転キーの実効性 (誤った向きでは挿入できないこと)")
# キー無しなら円形ポケットはどの回転角でも挿入できてしまう。180°回転させた
# クレードルが Cannon_Bored の残存殻と明確に干渉する (=挿入不能) ことを確認し、
# キーが実際に機能する (正しい向き以外を物理的に排除する) ことを検証する
cradle_mic_key_test = cradle_mic0.copy()   # ローカル原点 (円筒軸中心) で既に Y=0
cradle_mic_key_test.apply_transform(
    trimesh.transformations.rotation_matrix(np.pi, [0, 1, 0]))   # Y軸まわり180°
cradle_mic_key_test.apply_translation([0, yc_mic, 0])            # Cannon 座標へ配置
iv_wrong = _vol(cradle_mic_key_test, cannon_bored)
check(iv_wrong > 5.0, f"audio_cradle_mic を180°回転させた場合の Cannon_Bored 残存殻との"
      f"干渉体積 {iv_wrong:.1f}mm3 > 5.0 (誤った向きは物理的に挿入不能)")

print("\n[6] 配線経路の連通")
# Cannon: マイクポケット後端 (AUDIO_MIC_Y0) から後端 (CANNON_Y_REAR) まで、
# 中心軸沿いが Cannon_Bored の実体でないこと (=空隙が連続)
probe_ys = np.linspace(C.CANNON_Y_REAR + 0.3, C.AUDIO_MIC_Y0 - 0.3, 20)
probe_pts = np.stack([np.zeros_like(probe_ys), probe_ys, np.zeros_like(probe_ys)], axis=1)
inside = cannon_bored.contains(probe_pts)
check(not inside.any(), f"Cannon 中心軸 (後端→マイクポケット) が全区間で空隙 "
      f"(中実={int(inside.sum())}/{len(probe_ys)})")

neck_bored = trimesh.load(STL / "Mouth_Neck_Bored.stl")
ball_bored = trimesh.load(STL / "Mouth_Ball_Bored.stl")
for name, mesh in (("Mouth_Neck_Bored", neck_bored), ("Mouth_Ball_Bored", ball_bored)):
    lo_, hi_ = mesh.bounds
    ys_ = np.linspace(lo_[1] + 0.5, hi_[1] - 0.5, 12)
    pts_ = np.stack([np.zeros_like(ys_), ys_, np.zeros_like(ys_)], axis=1)
    ins = mesh.contains(pts_)
    check(not ins.any(), f"{name}: 中心軸ボアが全区間で空隙 (配線通過可, "
          f"中実={int(ins.sum())}/{len(ys_)})")
    # 配線束ダミー (φ5, ボア φ6 より細い) が干渉なく通ること
    wire = trimesh.creation.cylinder(radius=2.5, height=(hi_[1] - lo_[1]) - 1.0, sections=32)
    wire.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0]))
    iv = _vol(wire, mesh)
    check(iv < 0.5, f"{name}: 配線束ダミー(φ5) vs 実体 交差体積 {iv:.2f}mm3 < 0.5")

print("\n[7] Mouth_Cap (無加工) の前面開口とスピーカーポートの整合 (簡易, 現物合わせ前提)")
cap0 = _scaled("Mouth_Cap_Grey")
cap_lo, cap_hi = cap0.bounds
cap_front_y = cap_hi[1] - 0.3
cap_r = [r for r in _radii_at_y(cap0, cap_front_y)]
cap_inner_r = min(r[0] for r in cap_r) if cap_r and cap_r[0][0] else None
if cap_inner_r:
    check(cap_inner_r >= C.AUDIO_SPK_D / 2,
          f"Cap 前面内径 {cap_inner_r*2:.1f}mm ≥ スピーカーポート径 {C.AUDIO_SPK_D}mm "
          "(Cap が音の出口を塞がない, 前提: Cap前端≈Cannon砲口が同軸で揃う)")
else:
    print("  SKIP Cap 前端の内径を検出できず (現物合わせで確認すること)")

print("\n[8] マイク挿入経路・Neck/Ball 球面座の実体検査")
from check_audio_assembly import run as check_assembly
_assembly = check_assembly()
check(_assembly['pass'],
      f"マイク挿入 {_assembly['mic_front_insertion']['worst_mm3']:.3f}mm3 / "
      f"Neck-Ball {_assembly['ball_neck_intersection_mm3']:.3f}mm3 / "
      f"球面挿入 {_assembly['ball_neck_insertion']['worst_mm3']:.3f}mm3")

print(f"\nresult: {'OK' if OK else 'NG'}")
sys.exit(0 if OK else 1)
