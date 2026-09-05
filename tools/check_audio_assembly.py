#!/usr/bin/env python3
"""砲身の挿入経路と球面嵌合を実メッシュ検査する。生成物は上書きしない。"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

import numpy as np
import trimesh
from manifold3d import Manifold, Mesh

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hardware/src"))
import config as C
import make_audio as MA
from lib import to_trimesh


def overlap(a, b):
    from mesh_checks import intersection_volume_mm3
    return intersection_volume_mm3(a, b) / 1.0


def translated(mesh, xyz):
    m = mesh.copy()
    m.apply_translation(xyz)
    return m


def insertion(mesh, stationary, center, direction, travel, step=0.25):
    """組立位置から外部へ逆向きに動かす。各位置の交差体積を測る。"""
    rows = []
    for d in np.linspace(0, travel, int(np.ceil(travel / step)) + 1):
        m = translated(mesh, np.array(center) + d * np.array(direction))
        rows.append({"displacement_mm": float(d), "intersection_mm3": overlap(m, stationary)})
    return {"worst_mm3": max(r["intersection_mm3"] for r in rows), "samples": rows}


def run(source=False):
    def load(name, fn):
        return to_trimesh(fn()) if source else trimesh.load(ROOT / "hardware/stl" / f"{name}.stl", force="mesh")
    cannon = load("Mouth_Cannon_Bored", MA.mouth_cannon_bored)
    neck = load("Mouth_Neck_Bored", MA.mouth_neck_bored)
    ball = load("Mouth_Ball_Bored", MA.mouth_ball_bored)
    mic = load("audio_cradle_mic", MA.audio_cradle_mic)
    yc = (C.AUDIO_MIC_Y0 + C.AUDIO_MIC_Y1) / 2
    b_in_neck = translated(ball, [0, C.MOUTH_BALL_LOCAL_Y - C.MOUTH_NECK_LOCAL_Y, 0])
    cap = to_trimesh(MA._load("Mouth_Cap_Grey"))
    cap_in_neck = translated(cap, [0, C.MOUTH_CAP_LOCAL_Y - C.MOUTH_NECK_LOCAL_Y, 0])
    obj = json.loads((ROOT / "tools/data/kit_assembly_front.json").read_text())
    placements = {p["name"]: p for p in obj["parts"]}
    offset_ok = (placements["Mouth_Neck_Blue"]["t"][1] == C.MOUTH_NECK_LOCAL_Y and
                 placements["Mouth_Ball_Grey"]["t"][1] == C.MOUTH_BALL_LOCAL_Y and
                 placements["Mouth_Cap_Grey"]["t"][1] == C.MOUTH_CAP_LOCAL_Y)
    # 内部座の追加加工域が球面座に含まれることを差集合で確認する。
    raw = trimesh.load(ROOT / "model/Mouth_Neck_Blue.stl", force="mesh")
    raw.apply_scale(C.SCALE)
    original_bored = MA._load("Mouth_Neck_Blue") - MA.cyl_y(60, C.AUDIO_WIRE_BORE_D)
    neck_m = Manifold(Mesh(np.asarray(neck.vertices, np.float32), np.asarray(neck.faces, np.uint32)))
    # 等しいSTL同士の差が退化面だけになる場合も、Manifold の空集合として
    # 扱う。非体積メッシュを二次ブーリアンへ渡して誤って計算不能にしない。
    removed = original_bored - neck_m
    exterior_loss = float((removed - MA.mouth_ball_seat() - MA.mouth_cap_seat()).volume())
    result = {"mode": "source" if source else "STL", "offsets_match_JSON": offset_ok,
              "mic_front_insertion": insertion(mic, cannon, [0, yc, 0], [0, 1, 0], 24),
              "ball_neck_intersection_mm3": overlap(b_in_neck, neck),
              "cap_neck_intersection_mm3": overlap(cap_in_neck, neck),
              "cap_neck_insertion": insertion(cap_in_neck, neck, [0, 0, 0], [0, 1, 0], 30),
              "ball_neck_insertion": insertion(neck, b_in_neck, [0, 0, 0], [0, 1, 0], 30),
              "neck_material_removed_mm3": float(removed.volume()),
              "neck_removed_outside_hidden_seat_mm3": exterior_loss,
              "neck_watertight": bool(neck.is_watertight),
              "neck_components": len(neck.split(only_watertight=False)),
              "neck_volume_mm3": float(neck.volume)}
    result["pass"] = bool(offset_ok and result["mic_front_insertion"]["worst_mm3"] < 0.05 and
                          result["ball_neck_insertion"]["worst_mm3"] < 0.05 and
                          result["ball_neck_intersection_mm3"] < 0.05 and exterior_loss < 0.5 and
                          result["cap_neck_intersection_mm3"] < 0.05 and
                          result["cap_neck_insertion"]["worst_mm3"] < 0.05 and
                          neck.is_watertight and result["neck_components"] == 1)
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", action="store_true", help="STL を上書きせず CAD から検証")
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()
    result = run(args.source)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    summary = {k: ({"worst_mm3": v["worst_mm3"], "samples": len(v["samples"])}
                   if isinstance(v, dict) and "samples" in v else v) for k, v in result.items()}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
