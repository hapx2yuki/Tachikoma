#!/usr/bin/env python3
"""TACHIKOMA.3mf からユニークパーツのメッシュを STL として抽出する。

3MF (Bambu Studio) は 3D/Objects/*.model に個別オブジェクトを持つ。
実ジオメトリを含むファイルのみ対象とし、同名パーツの配置インスタンス
(参照のみの小さい .model) はスキップする。

usage: .venv/bin/python tools/extract_meshes.py [path/to/TACHIKOMA.3mf]
"""
import re
import sys
import zipfile
from pathlib import Path

import numpy as np
import trimesh

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_3MF = Path.home() / "Downloads" / "TACHIKOMA.3mf"
OUT_DIR = ROOT / "model"

VERT_RE = re.compile(rb'<vertex x="([^"]+)" y="([^"]+)" z="([^"]+)"')
TRI_RE = re.compile(rb'<triangle v1="(\d+)" v2="(\d+)" v3="(\d+)"')


def part_name(archive_path: str) -> str:
    """'3D/Objects/Leg_Shin_Blue_x4.stl_14.model' -> 'Leg_Shin_Blue_x4'"""
    stem = Path(archive_path).name
    stem = re.sub(r"\.model$", "", stem)
    stem = re.sub(r"\.stl(_[A-Za-z0-9]+)*$", "", stem)
    stem = re.sub(r"\.stl_\d+$", "", stem)
    return re.sub(r"_\d+$", "", stem)


def main() -> None:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_3MF
    OUT_DIR.mkdir(exist_ok=True)
    seen: dict[str, int] = {}
    with zipfile.ZipFile(src) as zf:
        for info in zf.infolist():
            if not info.filename.startswith("3D/Objects/"):
                continue
            data = zf.read(info)
            verts = VERT_RE.findall(data)
            tris = TRI_RE.findall(data)
            if not verts or not tris:
                continue  # 参照のみのインスタンス
            name = part_name(info.filename)
            if name in seen and seen[name] >= len(verts):
                continue  # 既により大きいジオメトリを保存済み
            v = np.array(verts, dtype=float)
            f = np.array(tris, dtype=int)
            mesh = trimesh.Trimesh(vertices=v, faces=f, process=True)
            out = OUT_DIR / f"{name}.stl"
            mesh.export(out)
            seen[name] = len(verts)
            ext = mesh.extents
            print(f"{name:45s} {ext[0]:7.1f} x {ext[1]:6.1f} x {ext[2]:6.1f} mm  "
                  f"watertight={mesh.is_watertight}")
    print(f"\n{len(seen)} unique parts -> {OUT_DIR}")


if __name__ == "__main__":
    main()
