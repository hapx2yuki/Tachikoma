"""頭部中央目カメラ化 (2026-07-28 設計変更)。

頭部の中央可動目 (元 Head_Eye_White 形状の eye_pod) を固定カメラ目に置換
する。左右 2 目はキョロキョロ (SUBMICRO 駆動) を維持 — make_eye.py の
eye_pod/eye_carrier をそのまま ×2 使う (本ファイルは触らない)。

設計根拠の全詳細 (実測値・偏心角の探索・ケラレ計算・出典) は config.py の
CAM2_* コメント参照。要旨:
  - 中央目ソケット (config.EYE_SOCKETS_150[1]) の法線は仰角 ~46.6° (ほぼ
    真上向き) — 瞳をキャップ軸の真上に開けるとカメラは空を向く。
  - 既存のキョロキョロ機構 (黒ドット群がキャップ軸から ~45° 偏心) と同じ
    原理で、瞳をキャップ軸から CAM2_THETA_DEG=38.0° 偏心させ、正しい取付
    位相でグルーすることで光軸をほぼ水平前方へ向ける (残差 ~8.6°, 完全
    相殺の 46.6° は Head_Eye_White カップの材が薄くモジュールが収まらない
    ため断念 — config.py 参照)。
  - eye_pod_camera: 元 Head_Eye_White 形状のまま (外殻無傷, 鉄則1) 偏心
    位置に瞳ボア (定径 φ10) + モジュール収容キャビティを掘る。回転しない
    ため horn ポケットは持たず、ネックボスを太径にして接着代を稼ぐ。
  - camera_carrier: モジュール (子基板) を保持し、eye_pod_camera のキャビ
    ティへ差し込んで固定する隠しパーツ。頭部シェル内側へも接着ウィングで
    固定する (eye_carrier と同じ考え方)。XIAO 本体基板 (ESP32S3/WiFi/
    USB-C) は camera_carrier に載せず、頭部内の空きスペースへ FPC 経由で
    逃がす (配線は wiring.md 参照)。

生成物:
  eye_pod_camera — 中央目ソケットへ嵌める固定カメラ目 (Head_Eye_White 形状)。
                   組立後の参照形状 (検証/可視化用) — **印刷はしない**
  eye_pod_camera_shell / eye_pod_camera_base — 上記をキャップ底面で 2 分割した
                   印刷用パーツ (2026-08-19 印刷性再設計, config.py CAM2_SPLIT_*)。
                   一体版は印刷姿勢 (背面下) でキャビティにえぐられたネックボス
                   断面 ~120mm² の上にソリッドドームが載る不安定形状で失敗多発
                   だった。shell は底面ベタ置き・base は小物として安定印刷し、
                   camera_carrier を治具に挿した状態でリングプラグ嵌合+接着する
  camera_carrier — 子基板保持 + シェル内側固定の隠しパーツ

実行: cd hardware/src && ../../.venv/bin/python make_camera.py
"""
from pathlib import Path

import numpy as np
import trimesh
from manifold3d import Manifold, Mesh as MMesh

import config as C
from lib import box, cyl, rbox, export, to_trimesh
from make_eye import CAP_NORM

MODEL = Path(__file__).resolve().parent.parent.parent / "model"

# キャップ外面ドームの球フィット (check_eye.py の CAP_SPH_Z/CAP_SPH_R と
# 同一値 — eye_pod() の _cap_manifold() と同じ正規化フレーム: 底面が
# EYE_NECK_H, ドームが +Z へ突き出す)。偏心角 θ の瞳位置をこの球面上の
# 「軸から角度 θ」の点として定義する (回転不変性の根拠は config.py 参照)
CAP_SPH_Z = -3.42 + C.EYE_NECK_H
CAP_SPH_R = 19.11


def _to_manifold(tm: trimesh.Trimesh) -> Manifold:
    return Manifold(mesh=MMesh(vert_properties=np.asarray(tm.vertices, np.float32),
                                tri_verts=np.asarray(tm.faces, np.uint32)))


def _normalized_cap() -> trimesh.Trimesh:
    """eye_pod() の _cap_manifold() と同じ正規化 (make_eye.py 参照)。"""
    tm = trimesh.load(MODEL / "Head_Eye_White_x3.stl")
    tm.apply_transform(CAP_NORM)
    tm.apply_scale(1.5)
    tm.apply_translation([0.0, 0.0, C.EYE_NECK_H])
    return tm


def pupil_axis():
    """瞳の局所方向ベクトル u (外向き, 単位ベクトル)。

    ポッド座標: +Z = キャップ軸 (ソケット装着時に法線と一致)。u は
    このキャップ軸から CAM2_THETA_DEG だけ -Y 側 (既存のドット群と同じ
    偏心方向規約) へ傾いたベクトル。取付位相は assembly.md の指示どおり
    「グルー時にこの u が水平前方を向くように」選ぶ (config.py CAM2_THETA_DEG
    コメントの幾何導出参照)。
    """
    th = np.radians(C.CAM2_THETA_DEG)
    return np.array([0.0, -np.sin(th), np.cos(th)])


def pupil_center(tm: trimesh.Trimesh):
    """瞳ボアの外面開口中心 (ポッド座標) と、その位置での材厚を実メッシュに
    対しレイキャストして返す。"""
    u = pupil_axis()
    sph_c = np.array([0.0, 0.0, CAP_SPH_Z])
    origin_far = sph_c + CAP_SPH_R * u + u * 30.0
    locs, *_ = tm.ray.intersects_location(
        ray_origins=[origin_far], ray_directions=[-u], multiple_hits=True)
    assert len(locs) >= 2, (
        "eye_pod_camera: 瞳軸のレイが Head_Eye_White に当たらない "
        "(CAM2_THETA_DEG が形状の範囲外の可能性)")
    dists = np.sort(np.linalg.norm(locs - origin_far, axis=1))
    p_outer = origin_far - u * dists[0]
    depth_total = float(dists[-1] - dists[0])
    return p_outer, depth_total


def _rotated(m: Manifold, p_outer) -> Manifold:
    """ローカル -Z (=瞳軸に沿って材内部へ進む向き) が u (外向き) の逆に
    一致するよう X 軸まわりに CAM2_THETA_DEG 回転し、p_outer (瞳外面開口
    中心) へ平行移動する。manifold3d の .rotate([θ,0,0]) は局所 +Z を
    (0,-sinθ,cosθ)=u へ写す (make_camera.py 開発時に実測検証済み) ため、
    局所 -Z がちょうど「p_outer から材内部へ向かう向き」になる。"""
    return m.rotate([C.CAM2_THETA_DEG, 0, 0]).translate(list(p_outer))


def _pod_body_and_negative():
    """一体版 eye_pod_camera の実体 (加工前) と負形状 (瞳ボア+キャビティ)。

    印刷用 2 分割 (shell/base) と一体版が同一の実体・負形状を共有するための
    共通ビルダー。戻り値: (body Manifold, negative Manifold, p_outer)。"""
    tm = _normalized_cap()
    p_outer, depth_total = pupil_center(tm)
    need = C.CAM2_LENS_STANDOFF + C.CAM2_MODULE_T + 2 * C.CAM2_MODULE_CLR
    assert depth_total > need - 1.0, (
        f"eye_pod_camera: 実測材厚 {depth_total:.2f}mm がモジュール収容の "
        f"想定 {need:.2f}mm を大きく下回る (θ/標準距離の再検討が必要)")

    m = _to_manifold(tm)
    # ネックボス (太径。回転しない固定パーツのため座グリ回転クリアランス
    # 不要 — キャップへ 2mm 食い込ませて結合するのは eye_pod() と同じ流儀)
    boss = cyl(C.EYE_NECK_H + 2, C.CAM2_NECK_D).translate([0, 0, (C.EYE_NECK_H + 2) / 2])
    m = m + boss
    m -= box(80, 80, 30).translate([0, 0, -15])  # 背面 z=0 で平らに (eye_pod() と同じ)

    # 瞳ボア: 定径 (φCAM2_PUPIL_D) のまま外面からレンズ標準距離まで貫通
    # (局所 z は [-LENS_STANDOFF, +PAD] — PAD は開放境界を確実に貫通させる
    # 張り出し。_rotated() の規約で局所 -Z が材内部へ進む向き)
    pupil_h = C.CAM2_LENS_STANDOFF + C.CAM2_PUPIL_PAD
    pupil = cyl(pupil_h, C.CAM2_PUPIL_D).translate(
        [0, 0, (C.CAM2_PUPIL_PAD - C.CAM2_LENS_STANDOFF) / 2])
    pupil = _rotated(pupil, p_outer)

    # モジュール収容キャビティ: 瞳ボアが終わる深さ (LENS_STANDOFF) から
    # camera_carrier ぶんだけ奥へ、長辺=局所X (キャップ軸に垂直な水平方向)・
    # 短辺=局所Y (瞳偏心を作る鉛直面内方向) の箱で掘る。深さ方向に大きく
    # 張り出し (POCKET_PAD) てネックボス側面まで確実に貫通させ、
    # camera_carrier を差し込める開口にする
    ox = C.CAM2_MODULE_L + 2 * (C.CAM2_MODULE_CLR + C.CAM2_WALL + C.CAM2_SAFETY)
    oy = C.CAM2_MODULE_W + 2 * (C.CAM2_MODULE_CLR + C.CAM2_WALL + C.CAM2_SAFETY)
    cav_h = (C.CAM2_MODULE_T + 2 * C.CAM2_MODULE_CLR) + C.CAM2_POCKET_PAD
    cav_lo = -(C.CAM2_LENS_STANDOFF + cav_h)  # 局所 z 下端 (最も材内部)
    cav_hi = -C.CAM2_LENS_STANDOFF + 0.3      # 瞳ボアと 0.3mm 重ねて確実に繋げる
    cavity = box(ox, oy, cav_hi - cav_lo).translate([0, 0, (cav_lo + cav_hi) / 2])
    cavity = _rotated(cavity, p_outer)

    return m, pupil + cavity, p_outer


def _upper_halfspace(z_floor: float) -> Manifold:
    """z >= z_floor の半空間 (十分大きい箱)。"""
    return box(200, 200, 200).translate([0, 0, z_floor + 100])


def _fpc_slot() -> Manifold:
    """base の FPC 引き出し縦溝。carrier 配線切り欠きの出口 (y≈-11.8,
    z≈7.3) の直下から base 底面まで貫く。上端は分割面 (z=EYE_NECK_H)
    ちょうどで、shell 側の材には触れない (shell 側はチャネル開口がこの
    位置を覆っているため FPC は連続して通る)。

    v3 (2026-08-19): 前側リム壁を貫通する **開放溝**。v2 の閉じた縦穴は
    壁 (y=-12.75..-14 の円弧) を厚さ ~1.3mm×幅 10mm の孤立した刃として
    残し、両端の付け根だけで保持される形になって実印刷でちぎれた。前壁は
    接着周長のためだけの材 (shell もこの位置では支えない) で、ソケット
    装着後は Head_Top のボア壁 (φ30) が溝の外側を閉じる — FPC は装着後
    実質閉断面の溝に収まる。"""
    w, d, yc = C.CAM2_FPC_SLOT
    h = C.EYE_NECK_H + 4.0
    return box(w, d, h).translate([0.0, yc, C.EYE_NECK_H - h / 2])


def _carrier_dims(p_outer):
    """camera_carrier の本体寸法 (ox, oy, body_h, lo, hi, pocket_t)。
    carrier 本体と base ポケット負形状 (_base_pocket_negative) が同一の
    寸法定義を共有するための共通ヘルパー。"""
    ox = C.CAM2_MODULE_L + 2 * (C.CAM2_MODULE_CLR + C.CAM2_WALL)
    oy = C.CAM2_MODULE_W + 2 * (C.CAM2_MODULE_CLR + C.CAM2_WALL)
    pocket_t = C.CAM2_MODULE_T + 2 * C.CAM2_MODULE_CLR
    th = np.radians(C.CAM2_THETA_DEG)
    # 最深コーナー (局所 y=-oy/2, z=lo) が「ポケット底 +0.5mm」に着く最大長
    body_h = ((p_outer[2] - (C.CAM2_BASE_POCKET_FLOOR_T + 0.5) - (oy / 2) * np.sin(th))
              / np.cos(th)) - C.CAM2_LENS_STANDOFF
    lo = -(C.CAM2_LENS_STANDOFF + body_h)
    hi = -C.CAM2_LENS_STANDOFF
    return ox, oy, body_h, lo, hi, pocket_t


def _base_pocket_negative(p_outer) -> Manifold:
    """base 用の最小ポケット負形状 (2026-08-19 v2)。

    carrier 実形状 + 片側 CAM2_BASE_POCKET_CLR の斜めプリズム (深端は
    carrier 最深面の CLR 手前まで、上方は分割面より上へ大きく開放)。
    旧実装は一体版の汎用キャビティ負形状 (軸方向 POCKET_PAD=20mm 張り出し
    込み) を流用しており、carrier が存在しない領域まで斜めに掘れて +Y 側
    リム壁が根元 ~1.4mm の薄足で床板に立つ脆い形になっていた (断面図での
    ユーザー指摘)。挿入は carrier 自身の軸方向スイープ = このプリズムに
    一致するため、上から滑り込ませる組立はそのまま成立する。"""
    ox, oy, body_h, lo, hi, _ = _carrier_dims(p_outer)
    clr = C.CAM2_BASE_POCKET_CLR
    cz_lo = lo - clr
    cz_hi = hi + 30.0          # 上方開放 (base は z<=EYE_NECK_H で切られる)
    b = box(ox + 2 * clr, oy + 2 * clr, cz_hi - cz_lo).translate(
        [0, 0, (cz_lo + cz_hi) / 2])
    return _rotated(b, p_outer)


def eye_pod_camera() -> Manifold:
    """一体版 (組立後の参照形状)。check_camera.py / 可視化 / URDF が参照する。
    印刷には使わない — 印刷は eye_pod_camera_shell + eye_pod_camera_base
    (下記) を使う (2026-08-19 印刷性再設計, config.py CAM2_SPLIT_* 参照)。
    キャビティは base 底肉厚 (CAM2_BASE_POCKET_FLOOR_T) で止まる止まり穴で、
    背面へは FPC シュートだけが抜ける — 分割版 2 部品を接着した組立後の
    実形状と一致する (プラグ/溝の内部ディテールのみ簡略)。z>=EYE_NECK_H は
    shell と同じ汎用キャビティ、z<EYE_NECK_H は base と同じ最小ポケット。"""
    m, neg, p_outer = _pod_body_and_negative()
    m -= neg ^ _upper_halfspace(C.EYE_NECK_H)
    base_slab = (_upper_halfspace(C.CAM2_BASE_POCKET_FLOOR_T)
                 - _upper_halfspace(C.EYE_NECK_H))
    m -= _base_pocket_negative(p_outer) ^ base_slab
    m -= _fpc_slot()
    return m.simplify(0.01)


# ---- 印刷用 2 分割 (2026-08-19 印刷性再設計) ----
# 分割面はキャップ底面 (z=EYE_NECK_H)。位置決めは base 上面のリングプラグ
# (呼び φ27/φ23 × 1.8, 実寸は片側 CAM2_SPLIT_CLR 痩せ) が shell 底面の
# リング溝へ嵌まる。組立は「camera_carrier+モジュールを base のポケットへ
# 先入れ → shell を被せてプラグ嵌合+接着」— 斜めポケット/チャネルを carrier
# が貫くため回転位相も一意に決まる (assembly.md §2.9)。印刷姿勢:
#   shell — そのまま (底面ベタ置き, ドーム上向き)。サポート不要
#   base  — そのまま (背面 z=0 を下)。底面ベタ置きの小物で安定

def _split_ring(od: float, id_: float, h: float) -> Manifold:
    return cyl(h, od) - cyl(h + 2, id_)


def eye_pod_camera_shell() -> Manifold:
    """ドーム部 (z >= EYE_NECK_H)。底面にリング溝 (プラグ受け)。"""
    m, neg, _ = _pod_body_and_negative()
    zs = C.EYE_NECK_H
    shell = m - box(200, 200, 60).translate([0, 0, zs - 30])   # z < zs を除去
    groove_h = C.CAM2_SPLIT_PLUG_H + 0.2
    groove = _split_ring(C.CAM2_SPLIT_PLUG_OD, C.CAM2_SPLIT_PLUG_ID, groove_h)
    shell -= groove.translate([0, 0, zs + groove_h / 2])
    return (shell - neg).simplify(0.01)


def eye_pod_camera_base() -> Manifold:
    """ネックボス部 (z <= EYE_NECK_H) + 位置決めリングプラグ。

    ポケットは carrier 実形状+クリアランスの最小プリズム (2026-08-19 v2,
    _base_pocket_negative 参照) を底肉厚 CAM2_BASE_POCKET_FLOOR_T を残す
    止まり穴として適用する (貫通させるとボスが 4 本の柱に分断され単体で
    非連結になる — 分割実装時に実測で検出)。プラグリングは shell の溝と
    同じ汎用キャビティ負形状でトリムする (shell 側に溝が無い位置へプラグを
    残さないため。carrier とのクリアランスも旧来どおり確保される)。"""
    m, neg, p_outer = _pod_body_and_negative()
    zs = C.EYE_NECK_H
    base = m - box(200, 200, 60).translate([0, 0, zs + 30])    # z > zs を除去
    base = base ^ cyl(60, C.CAM2_NECK_D + 0.02)  # z=zs のキャップ縁共平面スライバー除去
    base -= _base_pocket_negative(p_outer) ^ _upper_halfspace(C.CAM2_BASE_POCKET_FLOOR_T)
    plug_h = C.CAM2_SPLIT_PLUG_H + 0.5                          # 0.5mm 食い込ませ結合
    plug = _split_ring(C.CAM2_SPLIT_PLUG_OD - 2 * C.CAM2_SPLIT_CLR,
                       C.CAM2_SPLIT_PLUG_ID + 2 * C.CAM2_SPLIT_CLR, plug_h)
    plug = plug.translate([0, 0, zs + C.CAM2_SPLIT_PLUG_H - plug_h / 2]) - neg
    base += plug
    base -= _fpc_slot()
    # ポケット空隙の直上に浮くプラグ円弧の微小片 (実測 ~4mm³) を除去 —
    # decompose して最大成分のみ残す (arm_shell.py の非連結片対策と同じ流儀)
    parts = base.decompose()
    if len(parts) > 1:
        vols = [to_trimesh(p).volume for p in parts]
        dropped = sum(vols) - max(vols)
        assert dropped < 20.0, f"eye_pod_camera_base: 想定外の非連結片 計{dropped:.1f}mm3"
        base = parts[int(np.argmax(vols))]
    return base.simplify(0.01)


def camera_carrier() -> Manifold:
    """eye_pod_camera のモジュール収容キャビティへ差し込む隠しパーツ。

    ローカル座標は eye_pod_camera と共通 (ポッド原点 = ソケット装着時の
    サーボ軸相当位置, 瞳軸は pupil_axis()/pupil_center() で得られる u と
    p_outer)。モジュール (子基板) をレンズ面が p_outer から CAM2_LENS_
    STANDOFF の深さに来る位置で保持する。

    2026-08-19 印刷性再設計に伴う変更: 分割接着 (shell/base) では carrier を
    接着前に base のポケットへ先入れするため、後方 (ネックボス) へ貫通する
    長い尾は不要になった — 本体長は「最深コーナーが base ポケット底
    (CAM2_BASE_POCKET_FLOOR_T) の 0.5mm 手前に届く」最大長を式で決める。
    頭部シェル内側への接着ウィングも廃止 (base ポケット内に完全内蔵される
    ため物理的に届かない。固定は shell/base 接着で閉じ込め + FPC は base の
    スロットから引き出す)。CAM2_CARRIER_WING/CAM2_POCKET_PAD の carrier 用途
    は失効 (POCKET_PAD は負形状の張り出しとして引き続き使用)。
    """
    tm = _normalized_cap()
    p_outer, _ = pupil_center(tm)

    ox, oy, body_h, lo, hi, pocket_t = _carrier_dims(p_outer)
    assert body_h >= pocket_t + 2.0, (
        f"camera_carrier: body_h {body_h:.2f}mm がモジュールポケット {pocket_t:.2f}mm"
        f"+底肉 2mm を下回る (CAM2_BASE_POCKET_FLOOR_T の再検討が必要)")
    # 本体トレイ: レンズ面 (局所 z = -LENS_STANDOFF) から材内部へ body_h
    body = rbox(ox, oy, body_h, r=min(ox, oy) * 0.15).translate([0, 0, (lo + hi) / 2])

    # モジュールポケット (負形状): レンズ面から開放
    pocket = box(C.CAM2_MODULE_L + 2 * C.CAM2_MODULE_CLR,
                 C.CAM2_MODULE_W + 2 * C.CAM2_MODULE_CLR,
                 pocket_t + 2.0).translate([0, 0, hi - pocket_t / 2 + 1.0])

    # 配線逃がし切り欠き (側方, 局所 -Y 側 = 瞳偏心と反対の開放空間側)
    notch_w, notch_d = C.CAM2_WIRE_NOTCH
    notch = box(notch_w, notch_d * 2, pocket_t + 1.0).translate(
        [0, -oy / 2, hi - pocket_t / 2 + 0.5])

    m = body - pocket - notch
    m = _rotated(m, p_outer)
    return m


def install_rotation(n: np.ndarray) -> np.ndarray:
    """中央目ソケットへの取付回転 R (3x3)。ローカル +Z (キャップ軸) を
    ソケット法線 n へ、ローカル -Y (瞳が偏心する側, pupil_axis() 参照) を
    「n を鉛直面内で水平側へ 90° 倒した」方向 e_v へ写す — これにより
    pupil_axis() の写り先の仰角がちょうど config.CAM2_RESIDUAL_DEG になる
    (make_camera.py 開発時に数値検証済み。tools/check_camera.py [1] で
    ビルドのたびに再検証する)。中央目ソケットは機体中心面上 (n の X 成分
    = 0) にあるため e_v はこの単純な式で求まる (他ソケットでは一般化が
    必要だが、固定カメラは中央目にしか使わないため未対応)。

    assembly.md の「取付位相」はこの R が implicit に定める向き — 現物では
    ネック軸に沿ってこの回転で挿してから接着する (キー等の機械的な位相
    決めは持たないため、水平が出ているか現物で確認しながら接着すること)。
    """
    n = np.asarray(n, dtype=float)
    n = n / np.linalg.norm(n)
    assert abs(n[0]) < 1e-6, "install_rotation: 中央目ソケット以外には未対応 (n_x != 0)"
    e_v = np.array([0.0, n[2], -n[1]])
    e_v /= np.linalg.norm(e_v)
    x_col = np.cross(e_v, n)
    return np.column_stack([x_col, e_v, n])


def build_all() -> dict:
    print("[camera] (中央目カメラ化: eye_pod_camera [参照] + 印刷用分割 shell/base + camera_carrier)")
    parts = {"eye_pod_camera": eye_pod_camera(),
             "eye_pod_camera_shell": eye_pod_camera_shell(),
             "eye_pod_camera_base": eye_pod_camera_base(),
             "camera_carrier": camera_carrier()}
    return {name: export(m, name) for name, m in parts.items()}


if __name__ == "__main__":
    build_all()
