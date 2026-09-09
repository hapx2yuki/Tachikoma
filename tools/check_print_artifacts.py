#!/usr/bin/env python3
"""3MFの実体・変換・材料を独立に検査する (読み取り専用)。

既定: リポジトリ直下と hardware/stl の全3MF。名前を指定すれば確認用出力も検査できる。
例: .venv/bin/python tools/check_print_artifacts.py --json report.json

元STLと埋込頂点の双方向最近傍距離を0.02mmで照合する。STLの頂点順序、
平行移動、軸の直交回転には依存しない。別の三角形分割や任意角の回転は
誤差として報告され得るので、SHAPE_REVIEW は旧形状との断定ではない。
購入・実印刷履歴はここでは分からないため、生成定義との数量差は警告とする。
この検査は印刷成功・組立・強度を証明しない。
"""
from __future__ import annotations

import argparse
from collections import Counter
import itertools
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

import numpy as np
from scipy.spatial import cKDTree
import trimesh

import make_plates as P

NS = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"
PN = "{http://schemas.microsoft.com/3dmanufacturing/production/2015/06}"
TOL = 0.02


def metadata(element):
    return {m.get("key"): m.get("value") for m in element.findall("metadata")
            if m.get("key")}


def transform(value):
    if value is None:
        return np.eye(4)
    vals = np.asarray(value.split(), float)
    if vals.size != 12:
        raise ValueError("3MF変換は12要素でなければならない")
    out = np.eye(4)
    out[:3, :] = vals.reshape(4, 3).T
    return out


def resource_mesh(zf, document, oid, ancestors=()):
    key = (document, oid)
    if key in ancestors:
        raise ValueError(f"循環component参照: {key}")
    root = ET.fromstring(zf.read(document))
    if root.get("unit", "millimeter") != "millimeter":
        raise ValueError(f"非mmモデル: {document}")
    obj = root.find(f"{NS}resources/{NS}object[@id='{oid}']")
    if obj is None:
        raise ValueError(f"object参照先なし: {key}")
    mesh = obj.find(f"{NS}mesh")
    if mesh is not None:
        vertices = [[float(v.get(a)) for a in "xyz"]
                    for v in mesh.findall(f"{NS}vertices/{NS}vertex")]
        faces = [[int(f.get(a)) for a in ("v1", "v2", "v3")]
                 for f in mesh.findall(f"{NS}triangles/{NS}triangle")]
        return trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    parts = []
    for component in obj.findall(f"{NS}components/{NS}component"):
        child = component.get(f"{PN}path", document).lstrip("/")
        item = resource_mesh(zf, child, component.get("objectid"), ancestors + (key,))
        item.apply_transform(transform(component.get("transform")))
        parts.append(item)
    if not parts:
        raise ValueError(f"空のobject: {key}")
    return trimesh.util.concatenate(parts)


def centered(mesh):
    v = np.unique(np.asarray(mesh.vertices), axis=0)
    return v - (v.min(axis=0) + v.max(axis=0)) / 2


def shape_distance(source, embedded):
    """右手系の24直交回転での最小距離。鏡映は別部品なので許容しない。"""
    a, b = centered(source), centered(embedded)
    atree, best = cKDTree(a), float("inf")
    extent = np.ptp(a, axis=0)
    for perm in itertools.permutations(range(3)):
        if np.max(np.abs(extent - np.ptp(b[:, perm], axis=0))) > TOL:
            continue
        for signs in itertools.product((-1, 1), repeat=3):
            mat = np.eye(3)[:, perm] @ np.diag(signs)
            if np.linalg.det(mat) < 0:
                continue
            q = b @ mat
            d1 = float(atree.query(q, workers=1)[0].max())
            if d1 >= best:
                continue
            d2 = float(cKDTree(q).query(a, workers=1)[0].max())
            best = min(best, max(d1, d2))
            if best < TOL:
                return best
    return best


def audit(path):
    report = {"path": str(path.resolve()), "objects": [], "errors": [], "warnings": []}
    counts = Counter()
    with zipfile.ZipFile(path) as zf:
        main = ET.fromstring(zf.read("3D/3dmodel.model"))
        settings = ET.fromstring(zf.read("Metadata/model_settings.config"))
        project = json.loads(zf.read("Metadata/project_settings.config"))
        settings_by_id = {o.get("id"): metadata(o) for o in settings.findall("object")}
        items = main.findall(f"{NS}build/{NS}item")
        if not items:
            report["errors"].append("EMPTY_BUILD")
        # 使われるobjectを起点にする。設定表から消えた/未登録の部品を黙って飛ばさない。
        for oid in sorted({item.get("objectid") for item in items}):
            opts = settings_by_id.get(oid, {})
            name = opts.get("name", f"unnamed:{oid}")
            instances = [i for i in items if i.get("objectid") == oid and i.get("printable", "1") != "0"]
            counts[name] += len(instances)
            row = {"id": oid, "name": name, "instances": len(instances)}
            mesh = resource_mesh(zf, "3D/3dmodel.model", oid)
            if name not in P.PARTS:
                report["errors"].append(f"UNKNOWN_SOURCE: {name}")
                report["objects"].append(row)
                continue
            rule, qty, walls, infill, color, kit = P.PARTS[name]
            # Studioは元キットを100%の頂点+build側150%で保存する場合がある。
            # 生成器は頂点自体が150%。両方とも最終的な寸法で比較する。
            build_scale = 1.0
            if kit and instances:
                sv = np.linalg.svd(transform(instances[0].get("transform"))[:3, :3], compute_uv=False)
                if np.allclose(sv, P.SCALE, atol=1e-5):
                    build_scale = P.SCALE
                    mesh.apply_scale(build_scale)
            source = P.load_oriented(name)
            distance = shape_distance(source, mesh)
            # Studio保存の生座標にも対応 (平面自動選択の回転を取り消す必要をなくす)。
            if distance >= TOL:
                raw = trimesh.load((P.MODEL if kit else P.STL) / name, force="mesh")
                if kit:
                    raw.apply_scale(P.SCALE)
                distance = min(distance, shape_distance(raw, mesh))
            dv = abs(abs(mesh.volume) - abs(source.volume)) / max(abs(source.volume), 1e-9)
            row.update(vertex_distance_mm=distance if np.isfinite(distance) else None,
                       relative_volume_difference=float(dv))
            if distance >= TOL or dv >= 0.005:
                report["errors"].append(f"SHAPE_REVIEW: {name} distance={distance:.4g}mm volume={dv:.3%}")
            slot = int(opts.get("extruder", "0"))
            types = project.get("filament_type", [])
            material = types[slot - 1] if 1 <= slot <= len(types) else "UNRESOLVED"
            expected = "PETG" if color == "petg" else "TPU" if color == "tpu" else "PLA"
            row.update(filament_slot=slot, material=material,
                       wall_loops=opts.get("wall_loops", project.get("wall_loops")),
                       infill=opts.get("sparse_infill_density", project.get("sparse_infill_density")))
            if material != expected:
                report["errors"].append(f"MATERIAL: {name} {material} != {expected}")
            if int(row["wall_loops"]) < walls or float(row["infill"].rstrip("%")) < float(infill.rstrip("%")):
                report["warnings"].append(f"PRINT_SETTINGS_REVIEW: {name} 壁/充填が生成定義未満")
            for index, item in enumerate(instances):
                matrix = transform(item.get("transform"))
                singular = np.linalg.svd(matrix[:3, :3], compute_uv=False)
                if not np.allclose(singular, build_scale, atol=1e-5):
                    report["errors"].append(f"BUILD_SCALE: {name}#{index} {singular.tolist()}")
                instance = mesh.copy()
                instance.apply_scale(1.0 / build_scale)
                instance.apply_transform(matrix)
                if abs(instance.bounds[0, 2]) > 0.1:
                    report["warnings"].append(f"BED_Z_REVIEW: {name}#{index} z={instance.bounds[0, 2]:.3f}mm")
            report["objects"].append(row)
    if path.stem in P.PLATES:
        spec = P.PLATES[path.stem]
        expected_counts = Counter({f: P.PARTS[f][1] for f in spec.get("items", [])})
        if counts != expected_counts:
            report["warnings"].append("QUANTITY_REVIEW: 現物は全数生成定義と異なる。印刷済み除外/個別配置か履歴確認")
            report["defined_quantities"] = dict(expected_counts)
    else:
        report["warnings"].append("UNMANAGED_PLATE: 単発3MF。全数計画の数量とは照合しない")
    report["actual_quantities"] = dict(counts)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    results = []
    paths = args.paths or sorted(set(P.STL.glob("*.3mf")) | set(P.ROOT.glob("*.3mf")))
    if not paths:
        results.append({"path": None, "errors": ["NO_INPUT: 検査対象の3MFがない"], "warnings": []})
        print("NG: 検査対象の3MFがない")
    for path in paths:
        try:
            result = audit(path)
        except Exception as exc:
            result = {"path": str(path), "errors": [f"PARSE: {type(exc).__name__}: {exc}"], "warnings": []}
        results.append(result)
        print(f"{path.name}: {'NG' if result['errors'] else 'OK'} / warnings={len(result['warnings'])}")
        for line in result["errors"] + result["warnings"]:
            print(f"  {line}")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n")
    return 1 if any(r["errors"] for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
