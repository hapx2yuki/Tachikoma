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
from check_leg_link_strength import (_ring_props, _print_first_rule,
                                     print_first_scan)


# 55 MPa is a candidate material allowance, not a PLA print guarantee.  These
# factors intentionally include substantially lower values so the report
# exposes how much the model depends on that single literature assumption.
PRINT_FIRST_ALLOWABLE_FACTORS = (0.25, 0.50, 0.75, 1.00)
PRINT_FIRST_LAYER_FACTORS = (1.00, 0.75, 0.50)


def print_first_sensitivity(*, output_json=None, allowable_mpa=None):
    """印刷優先6リンクの許容応力/積層方向の感度を走査する。

    形状断面の beam model を使うため、スライサ経路、層間接着、疲労、
    実PLA試験を代替しない。``allowable_mpa`` は主に回帰試験用の明示値で、
    未指定なら config の候補値から低い倍率を展開する。
    """
    rule = _print_first_rule()
    base = float(rule["allowable_bending_mpa"])
    print("print-first strength rule: "
          f"base_load={rule['base_load_kgf']:.2f}kgf × dynamic_factor={rule['dynamic_factor']:.2f} "
          f"= dynamic_load={rule['load_kgf']:.2f}kgf; wall={rule['wall_mm']:.1f}mm "
          f"infill={rule['infill_fraction']:.0%}; physical strength UNVERIFIED")
    values = ([float(value) for value in allowable_mpa]
              if allowable_mpa else [base * factor for factor in PRINT_FIRST_ALLOWABLE_FACTORS])
    if not values or any(not np.isfinite(value) or value <= 0.0 for value in values):
        raise ValueError("print-first allowable stress sensitivity must be finite and positive")
    rows = []
    # First dimension: direct allowable stress sensitivity.  Second dimension:
    # a conservative orientation factor representing unmeasured layer quality.
    # The output keeps both dimensions explicit instead of silently selecting
    # one orientation as a pass condition.
    for stress in values:
        for orientation_factor in PRINT_FIRST_LAYER_FACTORS:
            effective = stress * orientation_factor
            details = print_first_scan(
                Path(__file__).resolve().parents[1] / "outputs/print-first-20260905/legs",
                sigma_allow=effective,
                sf_req=rule["required_safety_factor"],
                load_kgf=rule["load_kgf"], emit=False, return_details=True)
            minimum = min(float(row["safety_factor"]) for row in details)
            rows.append({
                "allowable_stress_mpa": float(stress),
                "layer_orientation_factor": float(orientation_factor),
                "effective_allowable_stress_mpa": float(effective),
                "minimum_safety_factor": minimum,
                "required_safety_factor": float(rule["required_safety_factor"]),
                "model_meets_requirement": bool(minimum >= rule["required_safety_factor"]),
                "link_results": details,
            })
    result = {
        "status": "SENSITIVITY_ONLY_UNVERIFIED_PRINT_PATH",
        "rule_source": "hardware/src/config.py:PRINT_FIRST_STRENGTH_RULE",
        "material": rule["material"],
        "wall_mm": rule["wall_mm"],
        "infill_fraction": rule["infill_fraction"],
        "base_load_kgf": rule["base_load_kgf"],
        "load_kgf": rule["load_kgf"],
        "dynamic_factor": rule["dynamic_factor"],
        "load_interpretation": "base 1.9kgf × dynamic factor 2.0 = 3.8kgf; load_kgf is already the dynamic envelope and is not multiplied again",
        "layer_orientation_status": rule["layer_orientation_status"],
        "physical_status": rule["physical_status"],
        "allowable_factors": list(PRINT_FIRST_ALLOWABLE_FACTORS) if not allowable_mpa else None,
        "rows": rows,
        "all_model_cases_meet_requirement": all(row["model_meets_requirement"] for row in rows),
        "interpretation": "最終判定は低許容応力・積層方向の全感度を確認してから行う。単一の55MPaモデル合格は実PLA強度保証にしない。",
    }
    if output_json:
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    for row in rows:
        print("print-first sensitivity: "
              f"allowable={row['allowable_stress_mpa']:.2f}MPa "
              f"layer_factor={row['layer_orientation_factor']:.2f} "
              f"effective={row['effective_allowable_stress_mpa']:.2f}MPa "
              f"min_SF={row['minimum_safety_factor']:.3f} "
              f"{'OK' if row['model_meets_requirement'] else 'NG'}")
    print("print-first sensitivity completed: model-only / physical printed strength UNVERIFIED")
    # Low-sensitivity rows are expected to fail for a candidate geometry.  A
    # nonzero exit is deliberate: callers must not collapse this analysis
    # into an unconditional PASS.
    return 0 if result["all_model_cases_meet_requirement"] else 1


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
    parser.add_argument("--print-first", action="store_true",
                        help="print-first PLA脚の標準/鏡像6 STLを感度走査する")
    parser.add_argument("--allowable-mpa", type=float, nargs="*",
                        help="感度用の許容応力を明示（省略時はconfig値の25/50/75/100%%）")
    args = parser.parse_args()
    if args.print_first:
        return print_first_sensitivity(output_json=args.json,
                                       allowable_mpa=args.allowable_mpa)
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
