#!/usr/bin/env python3
"""実トゥとTPU足裏の最低高さを姿勢ごとに比較する。設計変更・実機操作なし。

足の姿勢はsim_gaitのIK/ワークスペース射影を使用。トゥはkit_assemblyの
実メッシュ、パッドは現行STLの凸包頂点を使用する。平面までの最小距離は
頂点で厳密に求まる。位相・体高・指令は離散走査のため連続全域の証明ではない。
toe_minus_pad_z<0 は同一脚でトゥがTPUより先に接触することを意味する。
軟体変形・床・がたは未モデル化。負値を「装飾だから安全」とは扱わない。
"""
import argparse
import json
from pathlib import Path
import sys

import numpy as np
import trimesh

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hardware/src"))
import config as C
import sim_gait as G
import kit_assembly as K


def load_vertices():
    placements = K.load_placements()
    toes = {}
    for leg in G._LEGS:
        parts = [K.oriented_mesh(p) for p in K.by_link(placements, "leg_foot_bored")
                 if p.part == "Leg_Toe_Black_x12" and (p.instance == leg or p.instance.startswith(leg + "_"))]
        if len(parts) != 3:
            raise ValueError(f"{leg}: トゥ3個が必要、実際は{len(parts)}")
        toes[leg] = trimesh.util.concatenate(parts).convex_hull.vertices
    pad = trimesh.load(ROOT / "hardware/stl/foot_pad.stl", force="mesh").convex_hull.vertices
    rigid = trimesh.load(ROOT / "hardware/stl/leg_foot_bored.stl", force="mesh").convex_hull.vertices
    return toes, pad, rigid


def evaluate(toes, pad, rigid, height, commands, phases, holding=False):
    out = {"min_toe_world_z_mm": float("inf"), "min_pad_world_z_mm": float("inf"),
           "min_toe_minus_pad_z_mm": float("inf"), "samples": 0, "stance_samples": 0,
           "min_rigid_foot_minus_pad_z_mm": float("inf"),
           "pad_local_axis_extension_lower_bound_mm": 0.0,
           "stance_toe_lower_count": 0, "stance_rigid_foot_lower_count": 0, "ik_failures": 0}
    for command in commands:
        for phase in phases:
            for li, leg in enumerate(G._LEGS):
                angles = G.leg_ik(*G.foot_target(li, phase, *command, body_h=height, holding=holding))
                if angles is None:
                    out["ik_failures"] += 1
                    continue
                _, pitch, knee = np.radians(angles)
                tilt = pitch + knee
                base_z = height - C.FEMUR_LEN * np.sin(pitch) - C.TIBIA_LEN * np.cos(tilt)
                def minimum(vertices):
                    return float((-vertices[:, 0] * np.sin(tilt) + vertices[:, 2] * np.cos(tilt)).min() + base_z)
                tz, pz, rz = minimum(toes[leg]), minimum(pad), minimum(rigid)
                phase_fraction = (phase + G.PHASE_OFF[li]) % 1.0
                stance = holding or phase_fraction < G.DUTY
                where = {"leg": leg, "phase": float(phase), "command": list(command),
                         "pitch_deg": float(np.degrees(pitch)), "knee_deg": float(np.degrees(knee)),
                         "stance": bool(stance), "toe_z_mm": tz, "pad_z_mm": pz, "rigid_foot_z_mm": rz}
                out["samples"] += 1
                if stance:
                    out["stance_samples"] += 1
                    out["stance_toe_lower_count"] += int(tz < pz - 0.1)
                    out["stance_rigid_foot_lower_count"] += int(rz < pz - 0.1)
                    out["min_rigid_foot_minus_pad_z_mm"] = min(out["min_rigid_foot_minus_pad_z_mm"], rz - pz)
                    # パッド全体を局所-Zへ下げるときの幾何上の下限。
                    # 嵌合を維持する実部品の設計長・安全代を意味しない。
                    if np.cos(tilt) > 0:
                        out["pad_local_axis_extension_lower_bound_mm"] = max(
                            out["pad_local_axis_extension_lower_bound_mm"], (pz - min(tz, rz)) / np.cos(tilt))
                    if tz - pz < out["min_toe_minus_pad_z_mm"]:
                        out["min_toe_minus_pad_z_mm"] = tz - pz
                        out["worst_toe_vs_pad_pose"] = where
                if tz < out["min_toe_world_z_mm"]:
                    out["min_toe_world_z_mm"], out["lowest_toe_pose"] = tz, where
                out["min_pad_world_z_mm"] = min(out["min_pad_world_z_mm"], pz)
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path)
    parser.add_argument("--phases", type=int, default=360)
    args = parser.parse_args()
    if args.phases <= 0:
        parser.error("--phases は1以上で指定してください")
    toes, pad, rigid = load_vertices()
    results = []
    for height in (110, 115, 120, 125, 130):
        cases = [("holding", [(0, 0, 0)], [0], True),
                 ("active_sway_zero_stride", [(0, 0, 0)], np.arange(args.phases) / args.phases, False)]
        for gain in (1 / 6, 1 / 3, 2 / 3, 1):
            commands = [tuple(gain * v for v in c) for c in G.EVAL_CMDS if any(c)]
            cases.append((f"command_gain_{gain:.3f}", commands, np.arange(args.phases) / args.phases, False))
        for name, commands, phases, holding in cases:
            row = {"height_mm": height, "case": name,
                   **evaluate(toes, pad, rigid, height, commands, phases, holding)}
            results.append(row)
            print(f"h={height} {name}: toe_z={row['min_toe_world_z_mm']:+.3f}mm "
                  f"toe-pad={row['min_toe_minus_pad_z_mm']:+.3f}mm "
                  f"rigid-pad={row['min_rigid_foot_minus_pad_z_mm']:+.3f}mm "
                  f"stance_first={row['stance_toe_lower_count']}/{row['stance_samples']} "
                  f"ik_failures={row['ik_failures']}", flush=True)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps({"phase_samples": args.phases,
            "interpretation": "負のtoe-padは同一脚のトゥ先行接地。平面剛体近似、実機UNVERIFIED。",
            "results": results}, ensure_ascii=False, indent=2) + "\n")
    # 診断専用。既知の負値を安全とする合格コードは返さない。
    return 1 if any(r["min_toe_minus_pad_z_mm"] < -0.1 or r["ik_failures"] for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
