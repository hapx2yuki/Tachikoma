#!/usr/bin/env python3
"""印刷する頭部派生 STL と pod_neck 全体の実体干渉を検証する。

内部の空間に置く部品でも、外殻の実材料へ重なってよい理由にはならない。
旧判定は基部パッドを除外し、Head_Top を古い原型で検証していた。
2026-09-05 監査では両方を廃止した。全体交差は0.01mm3以下を要求する。
非交差時の頂点→面距離は参考値で、厳密な全三角形間最短距離ではない。
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
    rendered = {"Head_Bottom_Blue": "Head_Bottom_Armcut", "Head_Top_Blue": "Head_Top_Eyecut"}[name]
    return trimesh.load(STL / f"{rendered}.stl", force="mesh")


def inter_vol(a, b):
    from mesh_checks import intersection_volume_mm3
    return intersection_volume_mm3(a, b) / 1000.0


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
    nk = trimesh.load(STL / "pod_neck.stl", force="mesh")
    nk.apply_translation((0, 0, zb + C.CHASSIS_T))
    return nk, nk


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
    print(f"  Head_Bottom_Armcut vs pod_neck: {v_hb_raw*1000:.5f} mm3")
    print(f"  Head_Top_Eyecut vs pod_neck: {v_ht_raw*1000:.5f} mm3")
    ok = max(v_hb_raw, v_ht_raw) <= .00001
    if not ok:
        print("  NG: 実材料が重なっている。内部部品という理由で除外しない。")
    else:
        d_hb = min_clearance_mm(nk_raw, hb)
        d_ht = min_clearance_mm(nk_raw, ht)
        ok = min(d_hb, d_ht) >= 2.0
        print(f"  頂点→面距離: Bottom={d_hb:.3f} / Top={d_ht:.3f}mm (目標2mm)")
    print(f"RESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
