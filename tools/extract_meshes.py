#!/usr/bin/env python3
"""TACHIKOMA.3mf からユニークパーツのメッシュを STL として抽出する。

3MF (Bambu Studio) は 3D/Objects/*.model に個別オブジェクトを持つ。
実ジオメトリを含むファイルのみ対象とし、同名パーツの配置インスタンス
(参照のみの小さい .model) はスキップする。

usage: .venv/bin/python tools/extract_meshes.py [path/to/TACHIKOMA.3mf]
既定は outputs/extracted-model。model/原型を置換する場合は明示指定する。
"""
import argparse
import re
import xml.etree.ElementTree as ET
import sys
import zipfile
from pathlib import Path

import numpy as np
import trimesh

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_3MF = Path.home() / "Downloads" / "TACHIKOMA.3mf"
OUT_DIR = ROOT / "model"

NS = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
UNIT_MM = {"micron": 0.001, "millimeter": 1.0, "centimeter": 10.0,
           "inch": 25.4, "foot": 304.8, "meter": 1000.0}


def part_name(archive_path: str) -> str:
    """'3D/Objects/Leg_Shin_Blue_x4.stl_14.model' -> 'Leg_Shin_Blue_x4'"""
    stem = Path(archive_path).name
    stem = re.sub(r"\.model$", "", stem)
    stem = re.sub(r"\.stl(?:_[A-Za-z0-9]+)*$", "", stem)
    return stem  # 部品名そのものの末尾番号 (Part_1等) は保持する


def extract(src: Path, output: Path, overwrite: bool = False) -> dict[str, trimesh.Trimesh]:
    """原型の生座標を抽出。全項目を検証してから書く。配置transformは適用しない。"""
    parts = {}
    with zipfile.ZipFile(src) as zf:
        bad = zf.testzip()
        if bad:
            raise ValueError(f"ZIP CRC不一致: {bad}")
        for info in zf.infolist():
            if not (info.filename.startswith("3D/Objects/") and info.filename.endswith(".model")):
                continue
            tree = ET.fromstring(zf.read(info))
            unit = tree.get("unit", "millimeter")
            if unit not in UNIT_MM:
                raise ValueError(f"未知の3MF単位: {unit}")
            meshes = tree.findall("m:resources/m:object/m:mesh", NS)
            if not meshes:
                continue  # 配置参照だけのファイル
            if len(meshes) != 1:
                raise ValueError(f"1ファイルに複数原型。部品名を確定できない: {info.filename}")
            node = meshes[0]
            v = np.array([[float(e.attrib[k]) for k in ("x", "y", "z")]
                          for e in node.findall("m:vertices/m:vertex", NS)]) * UNIT_MM[unit]
            f = np.array([[int(e.attrib[k]) for k in ("v1", "v2", "v3")]
                          for e in node.findall("m:triangles/m:triangle", NS)], dtype=int)
            if v.ndim != 2 or v.shape[1] != 3 or not np.isfinite(v).all() or f.size == 0:
                raise ValueError(f"頂点/面が不正: {info.filename}")
            if f.min() < 0 or f.max() >= len(v):
                raise ValueError(f"面の頂点参照が範囲外: {info.filename}")
            name = part_name(info.filename)
            mesh = trimesh.Trimesh(vertices=v, faces=f, process=True)
            if name in parts:
                old = parts[name]
                # 頂点数が多い方を勝手に選ぶと原型がすり替わるため拒否する。
                if not (np.array_equal(mesh.vertices, old.vertices) and np.array_equal(mesh.faces, old.faces)):
                    raise ValueError(f"同名で形状の違う原型: {name}")
                continue
            parts[name] = mesh
    if not parts:
        raise ValueError("抽出可能な原型が0件。既存modelは変更しない")
    targets = {name: output / f"{name}.stl" for name in parts}
    existing = [str(path) for path in targets.values() if path.exists()]
    if existing and not overwrite:
        raise FileExistsError("既存ファイルを保存するため中止 (--overwriteで明示指定): " + ", ".join(existing))
    output.mkdir(parents=True, exist_ok=True)
    for name, mesh in parts.items():
        mesh.export(targets[name])
        ext = mesh.extents
        print(f"{name:45s} {ext[0]:7.1f} x {ext[1]:6.1f} x {ext[2]:6.1f} mm  "
              f"watertight={mesh.is_watertight}")
    return parts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", nargs="?", type=Path, default=DEFAULT_3MF)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs" / "extracted-model")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    parts = extract(args.source, args.output_dir, args.overwrite)
    print(f"\n{len(parts)} unique parts -> {args.output_dir}")


if __name__ == "__main__":
    main()
