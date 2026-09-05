#!/usr/bin/env python3
"""中実断面の強度判定が印刷内部の空隙にどれほど敏感かを調べる。

実スライサの経路解析・FEA・実測の代用ではない。断面を外周壁+40%密度の
均質コアに分けた感度計算であり、数値から現物破断を断定しない。
pod_neckは層上下面と側壁の厚さが違うため、一定壁厚仮定も近似。
比較は既存チェッカーと同一荷重。強度値はtibia 55MPa、pod 68MPa
(Bambu PETG Translucent公表XY曲げ強度の平均値、2026-09-05確認)。
資料値自体を許容応力とすること、実印刷との差は未検証。
"""
import argparse
import json
from pathlib import Path
import sys

import numpy as np
from shapely.geometry.polygon import orient
import trimesh

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hardware/src"))
import config as C
from check_leg_link_strength import _ring_props


def properties(polygons):
    total = np.zeros(5)
    for polygon in polygons:
        polygon = orient(polygon, 1)
        for ring in (polygon.exterior, *polygon.interiors):
            total += _ring_props(np.asarray(ring.coords))
    return total


def section_moduli(mesh, position, axis, wall, density):
    origin, normal = np.zeros(3), np.zeros(3)
    origin[axis], normal[axis] = position, 1
    section = mesh.section(plane_origin=origin, plane_normal=normal)
    if section is None:
        return None
    coords = [i for i in range(3) if i != axis]
    planar = trimesh.path.Path2D(entities=section.entities, vertices=section.vertices[:, coords])
    polygons = list(planar.polygons_full)
    if not polygons:
        return None
    cores = []
    for polygon in polygons:
        core = polygon.buffer(-wall)
        if not core.is_empty:
            cores.extend([core] if core.geom_type == "Polygon" else list(core.geoms))
    A, Sx, Sy, Ixx, Iyy = properties(polygons) - (1 - density) * properties(cores)
    cx, cy = Sy / A, Sx / A
    xy = np.concatenate([np.asarray(p.exterior.coords) for p in polygons])
    return ((Ixx - A * cy**2) / np.abs(xy[:, 1] - cy).max(),
            (Iyy - A * cx**2) / np.abs(xy[:, 0] - cx).max())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    results = []
    for name, axis, lo, hi, strength, requirement in (
        ("tibia_link", 2, -C.TIBIA_LEN + 12, -8, 55, 2),
        ("pod_neck", 1, -108, -26, 68, 3),
    ):
        mesh = trimesh.load(ROOT / f"hardware/stl/{name}.stl", force="mesh")
        for wall, density in ((1.6, 1.0), (1.6, 0.4), (1.8, 0.4)):
            worst, where = float("inf"), None
            for s in np.arange(lo, hi, 0.2) + 0.0137:
                moduli = section_moduli(mesh, s, axis, wall, density)
                if moduli is None:
                    continue
                if name == "tibia_link":
                    stress = 0.6 * 3.8 * 9.81 * (s + C.TIBIA_LEN) / min(moduli)
                else:
                    stress = 0.6 * 9.81 * 2 * abs(s + 187.1) / moduli[0] * 2.5
                sf = strength / stress
                if sf < worst:
                    worst, where = sf, s
            row = dict(part=name, wall_mm=wall, core_density=density, assumed_strength_mpa=strength,
                       safety_factor=float(worst), position_mm=float(where), required=requirement)
            results.append(row)
            print(f"{name}: wall={wall}mm core={density:.0%} SF={worst:.3f} "
                  f"at {where:.3f}mm / required={requirement} [近似・現物UNVERIFIED]")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps({"status": "SENSITIVITY_ONLY_UNVERIFIED_PRINT_PATH", "results": results},
                                       ensure_ascii=False, indent=2) + "\n")
    return 1 if any(r["safety_factor"] < r["required"] for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
