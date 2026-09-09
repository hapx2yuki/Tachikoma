#!/usr/bin/env python3
"""股ピッチ空間を再貫通したブラケットの直接上板・穴・曲げ感度を比較する。

既存STLを読むだけで生成/上書きしない。合格は幾何に限定し、印刷強度を保証しない。
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import trimesh
from manifold3d import Manifold, Mesh

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "hardware/src")]
import config as C
import make_leg as L
from lib import box, horn_pocket, servo_pocket, servo_tab_holes, to_trimesh
from check_leg_link_strength import section_props
from check_print_strength_sensitivity import section_moduli


def native(mesh):
    return Manifold(Mesh(np.asarray(mesh.vertices, np.float32), np.asarray(mesh.faces, np.uint32)))


def inspect(before, after):
    meshes = {name: trimesh.load(path, force="mesh") for name, path in (("before", before), ("after", after))}
    solids = {name: native(mesh) for name, mesh in meshes.items()}
    pocket = servo_pocket(L.P).rotate([-90, 0, 0]).translate([C.COXA_LEN, 0, 0])
    tabs = servo_tab_holes(L.P).rotate([-90, 0, 0]).translate([C.COXA_LEN, 0, 0])
    horn = horn_pocket(C.YAW_SERVO).translate([0, 0, L.COXA_TOP])
    floor_z = L.P["W"] / 2 + C.CLEAR
    top_region = box(200, 200, 100).translate([0, 0, floor_z + 50])
    tops = {name: solid ^ top_region for name, solid in solids.items()}
    result = {"pocket_intersection_mm3": float((solids["after"] ^ pocket).volume()),
              "tab_holes_intersection_mm3": float((solids["after"] ^ tabs).volume()),
              "horn_intersection_mm3": float((solids["after"] ^ horn).volume()),
              "added_outside_before_mm3": float((solids["after"] - solids["before"]).volume()),
              "top_bridge_floor_z_mm": floor_z,
              "top_bridge_symmetric_difference_mm3": float((tops["after"] - tops["before"]).volume() + (tops["before"] - tops["after"]).volume()),
              "mesh": {name: {"volume_mm3": float(mesh.volume), "watertight": bool(mesh.is_watertight),
                               "bodies": len(mesh.split(only_watertight=False))} for name, mesh in meshes.items()},
              "sections": {}, "pilot_bore_support": []}
    for name, mesh in meshes.items():
        result["sections"][name] = {}
        for region, shape in (("entire_frame", mesh), ("direct_top_bridge", to_trimesh(tops[name]))):
            rows = []
            for x in np.arange(.0137, C.COXA_LEN, .2):
                area, zy, zz = section_props(shape, [x, 0, 0], [1, 0, 0],
                    (np.array([0, 1., 0]), np.array([0, 0, 1.])))
                z40 = section_moduli(shape, x, 0, 1.6, .4)[0]
                rows.append({"x_mm": float(x), "area_mm2": float(area), "Zy_solid_mm3": float(zy),
                             "Zz_solid_mm3": float(zz), "Zy_wall1_6_core40_mm3": float(z40)})
            sensitivity = []
            for force in (22.37, 37.278, 44.73):
                for torque in (1.77, 1.956):
                    for mode, key in (("solid", "Zy_solid_mm3"), ("wall1_6_core40", "Zy_wall1_6_core40_mm3")):
                        values = [(55 * row[key] / (force * (C.COXA_LEN-row["x_mm"]) + 1000*torque), row["x_mm"]) for row in rows]
                        sf, x = min(values)
                        sensitivity.append({"force_N": force, "pitch_couple_Nm": torque, "material_model": mode,
                                            "minimum_sf": sf, "x_mm": x})
            result["sections"][name][region] = {"rows": rows, "sensitivity": sensitivity}
    for x in (C.YAW_SERVO["HORN_ARM_L"] * .45, C.YAW_SERVO["HORN_ARM_L"] * .62):
        samples = []
        for radius in (1.2, 1.5, 2., 3.):
            for angle in np.linspace(0, 2*np.pi, 16, endpoint=False):
                origin = [x + radius*np.cos(angle), radius*np.sin(angle), 50]
                hits, _, _ = meshes["after"].ray.intersects_location([origin], [[0, 0, -1]], multiple_hits=True)
                z = sorted(hits[:, 2], reverse=True)
                intervals = [[z[i+1], z[i]] for i in range(0, len(z)-1, 2)]
                samples.append({"radius_mm": radius, "angle_rad": float(angle), "material_intervals_mm": intervals})
        result["pilot_bore_support"].append({"x_mm": x, "samples": samples,
            "upper_material_min_depth_mm": min(s["material_intervals_mm"][0][1]-s["material_intervals_mm"][0][0] for s in samples)})
    paths = [Path(__file__), before, after, ROOT/"hardware/src/config.py", ROOT/"hardware/src/lib.py",
             ROOT/"hardware/src/make_leg.py", ROOT/"tools/check_leg_link_strength.py", ROOT/"tools/check_print_strength_sensitivity.py"]
    result["input_sha256"] = {str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    result["assumptions"] = {"sigma_MPa": 55, "section_step_mm": .2, "pitch_load_position_x_mm": C.COXA_LEN,
        "meaning": "足先荷重相当Fとピッチ反力偶力Mを別に置いた比較感度。力学モデルから同定した同時反力ではない。",
        "limitations": "全断面は下枠の合成梁効果を仮定する。直接上板も併記し全断面SFを安全保証に使わない。応力集中・ねじ抜け・積層異方性・実スライサ経路・実荷重は未検証。"}
    result["geometry_pass"] = bool(all(result[k] < .001 for k in (
        "pocket_intersection_mm3", "tab_holes_intersection_mm3", "horn_intersection_mm3",
        "added_outside_before_mm3", "top_bridge_symmetric_difference_mm3")) and
        result["mesh"]["after"]["watertight"] and result["mesh"]["after"]["bodies"] == 1)
    result["print_strength_status"] = "UNVERIFIED"
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--json", type=Path, required=True)
    args = parser.parse_args()
    result = inspect(args.before.resolve(), args.after.resolve())
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k not in ("sections", "pilot_bore_support", "input_sha256")}, ensure_ascii=False, indent=2))
    sys.exit(0 if result["geometry_pass"] else 1)
