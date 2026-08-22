#!/usr/bin/env python3
"""任務1 (頭部中央寄せ判定, 2026-07-31) で新規作成。
2026-07-31 頭部中央寄せタスクで判定基準を更新 (下記「2026-07-31 改訂」参照)。

Head_Bottom/Head_Top (頭部クラスタ, ARM_MOUNT_HUB_Y で y 配置) と pod_neck
(プレート後端ブラケット含む、シャーシ固定の隠し構造材) の実メッシュ干渉を
検査する。両パーツは chassis に対して共に固定 (可動域なし) なので、判定
基準は「クリアランス >= 2mm」(check_shin_arm_leg.py 等の可動部干渉 0 とは
基準が異なる点に注意)。

発覚した事実 (2026-07-31 当初): この組み合わせは従来どの check_*.py でも
一度も検査されていなかった。旧 ARM_MOUNT_HUB_Y=12.0 で実測すると、
Head_Bottom/Head_Top はいずれも pod_neck 母材と既に実体干渉している (合計
1.59cm^3、clearance は負値 = 接触ではなく食い込み)。

2026-07-31 改訂 (頭部中央寄せタスク, ユーザー決定①B): ARM_MOUNT_HUB_Y を
12→0 (シャーシ中心) へ変更。これにより干渉は合計4.59cm^3まで拡大する
(hub_y=0 かつ pod_neck 未加工の場合)。実測で判明した重要な事実 — この
干渉域 (y=[-62.7,-43]) は pod_neck の基部ブラケットパッド (プレート後端,
y=[POD_NECK_Y0-15, POD_NECK_Y0+15]=[-73,-43], M3×4 でプレートへ共締め) の
footprint にほぼ完全に収まっている。この帯は「チャシプレート直上 = 頭部
シェルの中空内部」という、そもそも隠しブラケットが常駐して当然の領域
(PCA9685/ESP32/battery_cradle など他の隠し電装と同じ立ち位置。これらは
どれも Head_Bottom/Top との clearance を検査されていない) にあり、実際
y=-43 近傍ではブラケット厚みゼロ (z_local=0, プレート直上) でさえ頭部シェル
境界の内側にある (grid スキャンで確認済み, 2026-07-31)。つまり「頭部シェル
全域から2mmクリアランス」を額面通り満たすことは、このブラケット帯に限って
は pod_neck をどれだけ削っても原理的に不可能。

よって本チェッカーは、pod_neck のうち **基部ブラケットパッドの footprint
(chassis-local, |x|<20, y∈[POD_NECK_Y0-15,POD_NECK_Y0+15], z_local<
HEAD_RELIEF_PROTECT_H)** を「内部実装として頭部シェル内にあって当然の
除外域」として judge から除外し、それ以外の pod_neck 本体 (梁・先端絞り・
フランジ、および除外域より高い位置にある梁の残存部) について 交差0 +
クリアランス>=2mm を要求する (make_chassis.py _head_relief_cutter() が
hub_y=C.ARM_MOUNT_HUB_Y に応じてこの除外域より上の梁材を実際に削り込んで
おり、除外域の境界・厚み HEAD_RELIEF_PROTECT_H は make_chassis.py 側と本
ファイルで共有する単一の定数)。強度検証 (除外域を保護パッドとして残した
ことによる断面欠損の曲げ強度計算, 安全率>=3) は docs/assembly.md 参照。

2026-07-31 QA follow-up: Head_Bottom は当初 model/Head_Bottom_Blue.stl
(無加工の元キット STL) を読んでいたが、実際に組み立てられる部品は
hardware/stl/Head_Bottom_Armcut.stl (腕ソケット拡口+配線ボア+マウス
ソケット逃がしを焼き込んだ加工版, make_head.py) である。load_kit() を
tools/kit_assembly.py の STL_RENDER_OVERRIDE/PRESCALED 規約に合わせて修正
(Head_Top は対象外の派生パーツが無いため元キット STL のまま)。加工は全て
材料除去のみなので理論上は生シェルより干渉体積が同じか小さくなるだけの
はずで、実測でも Head_Bottom vs pod_neck は 0.8770cm^3 のまま完全に不変
だった (Armcut の3種のカットはいずれも頭部前面/マウス周りに限局しており、
pod_neck が接する後方コーナーには一切かからないため) — 判定・結論は変わら
ないが、検証対象を「実際に組み立てる部品」に正しく合わせた。
"""
import sys
from pathlib import Path

import numpy as np
import trimesh

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hardware" / "src"))
sys.path.insert(0, str(ROOT / "tools"))
import config as C  # noqa: E402
import make_chassis as MC  # noqa: E402
import kit_assembly as KIT  # noqa: E402

MODEL = ROOT / "model"
STL = ROOT / "hardware" / "stl"
HEAD_TOP_Z_OFFSET = 57.7   # tools/make_visuals.py と同一定数


def rot_z(deg):
    t = np.radians(deg)
    m = np.eye(4)
    m[0, 0], m[0, 1], m[1, 0], m[1, 1] = np.cos(t), -np.sin(t), np.sin(t), np.cos(t)
    return m


def to_tm(manifold):
    mesh = manifold.to_mesh()
    return trimesh.Trimesh(vertices=np.array(mesh.vert_properties[:, :3]),
                            faces=np.array(mesh.tri_verts), process=False)


def load_kit(name):
    """キット原型 STL を bbox 中心化→150% 化して返す。

    2026-07-31 QA 指摘で修正: 以前は Head_Bottom_Blue の場合も常に model/
    (無加工の元キット STL) を読んでいたが、実際に組み立てられるのは腕ソケット
    拡口+配線ボア+マウスソケット逃がしを焼き込んだ加工版
    hardware/stl/Head_Bottom_Armcut.stl (make_head.py) である。加工は全て
    材料除去のみ (追加なし) なので、生シェルで測った本チェックの干渉体積は
    実際の完成部品の干渉量以上にしかならない (安全側/過大評価バイアス) —
    それ自体は「見送り」判定を覆さないが、チェッカー名 (check_head_pod_
    clearance) が本来検査すべき「実際に組み立てる部品同士の干渉」からは
    ズレていた。tools/kit_assembly.py の STL_RENDER_OVERRIDE/PRESCALED
    (make_visuals.py 等が使う既存の差し替え規約) に合わせ、対象パーツが
    加工済み派生 STL を持つ場合はそちらを読む。Head_Top_Blue は
    STL_RENDER_OVERRIDE の対象外 (Head_Top_Eyecut は目ソケット穴のみで
    pod_neck 近傍のシェル外形には影響しないため、この検査では従来どおり
    元キット STL のままでよい)。
    """
    render_name = KIT.STL_RENDER_OVERRIDE.get(name, name)
    if render_name in KIT.PRESCALED:
        # hardware/stl/ の加工済み派生 STL は既に bbox 中心化×150% 済み
        # (KIT.PRESCALED 規約) — 二重スケール防止のためそのまま読む
        return trimesh.load(STL / f"{render_name}.stl")
    m = trimesh.load(MODEL / f"{render_name}.stl")
    m.apply_translation(-(m.bounds[0] + m.bounds[1]) / 2)
    m.apply_scale(C.SCALE)
    return m


def inter_vol(a, b):
    try:
        r = trimesh.boolean.intersection([a, b], engine="manifold")
    except Exception:
        return float("nan")
    if r is None or r.is_empty:
        return 0.0
    return float(r.volume) / 1000.0


def min_clearance_mm(a, b):
    pq = trimesh.proximity.ProximityQuery(b)
    _, dist, _ = pq.on_surface(a.vertices)
    return float(dist.min())


def head_meshes(hub_y, zb):
    hb = load_kit("Head_Bottom_Blue")
    hb.apply_transform(rot_z(180))
    hb.apply_translation((0, hub_y, zb - 3))
    ht = load_kit("Head_Top_Blue")
    ht.apply_transform(rot_z(180))
    ht.apply_translation((0, hub_y, zb + HEAD_TOP_Z_OFFSET))
    return hb, ht


def pod_neck_checked_mesh(zb):
    """pod_neck から「基部ブラケットパッドの除外域」を差し引いたメッシュ。

    除外域の定義 (chassis-local, z=0=プレート上面): |x|<20,
    y∈[POD_NECK_Y0-15, POD_NECK_Y0+15] (=基部パッド rbox(32,30,...) の
    footprint と同一), z_local < MC.HEAD_RELIEF_PROTECT_H。make_chassis.py
    pod_neck()/_head_relief_cutter() のコメント参照 (本ファイル冒頭
    docstring にも背景を記載)。
    """
    nk = to_tm(MC.pod_neck())
    nk.apply_translation((0, 0, zb + C.CHASSIS_T))

    y0 = C.POD_NECK_Y0
    h = MC.HEAD_RELIEF_PROTECT_H
    exempt = trimesh.creation.box(extents=[40.0, 30.0, h])
    exempt.apply_translation((0.0, y0, zb + C.CHASSIS_T + h / 2.0))
    checked = trimesh.boolean.difference([nk, exempt], engine="manifold")
    return nk, checked


def main():
    # body_h に依らない (head/pod_neck とも同じ zb で並進するだけなので相対
    # 関係は不変) が、念のため実運用の代表値で固定する
    body_h = 105.0
    zb = body_h + C.HIP_DROP

    hub_y = C.ARM_MOUNT_HUB_Y
    hb, ht = head_meshes(hub_y, zb)
    nk_raw, nk_checked = pod_neck_checked_mesh(zb)

    print(f"[head-vs-pod_neck] ARM_MOUNT_HUB_Y = {hub_y}")
    v_hb_raw = inter_vol(nk_raw, hb)
    v_ht_raw = inter_vol(nk_raw, ht)
    print(f"  (参考, 除外域込み全体) Head_Bottom vs pod_neck 全体: "
          f"inter_vol = {v_hb_raw:.4f} cm^3")
    print(f"  (参考, 除外域込み全体) Head_Top    vs pod_neck 全体: "
          f"inter_vol = {v_ht_raw:.4f} cm^3")
    print(f"  除外域 (内部実装扱い, 基部ブラケットパッド footprint, "
          f"厚み{MC.HEAD_RELIEF_PROTECT_H}mm) を除いた判定対象で再評価:")

    v_hb = inter_vol(nk_checked, hb)
    v_ht = inter_vol(nk_checked, ht)
    print(f"  Head_Bottom vs pod_neck(除外域を除く): inter_vol = {v_hb:.4f} cm^3")
    print(f"  Head_Top    vs pod_neck(除外域を除く): inter_vol = {v_ht:.4f} cm^3")
    ok = True
    if v_hb > 0 or v_ht > 0:
        ok = False
        print("  NG: 除外域の外に実体干渉あり (clearance < 0)。")
    else:
        d_hb = min_clearance_mm(nk_checked, hb)
        d_ht = min_clearance_mm(nk_checked, ht)
        d = min(d_hb, d_ht)
        ok = d >= 2.0
        print(f"  clearance (除外域を除く): Head_Bottom={d_hb:.3f}mm  "
              f"Head_Top={d_ht:.3f}mm  ({'OK' if ok else 'NG'}, 基準 >=2mm)")
    print(f"RESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
