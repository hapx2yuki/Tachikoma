#!/usr/bin/env python3
"""印刷優先構成の電池・砲身音声部品の占有候補を出力する。

この補助は ``export_urdf.base_link_electronics_items`` や
``tools/print_first_assembly.py`` を呼び出さない。印刷優先用の context が
それらの関数を置き換える場合でも、置換後の関数を再帰的に呼ばないように
している。

マイクとスピーカーの局所位置は ``tools/check_audio.py`` のダミーと同じ式
から作り、Cannon ローカル座標から URDF の ``base_link`` 座標へ一度だけ
変換する。電池の寸法・質量・基準位置は呼び出し側から受け取る。既定値は
電池候補の寸法と質量だけで、基準位置を勝手に確定しない。

出力は収納成立や実物寸法の確定を示さない。対象個体の寸法・質量と、実機で
の配線経路・保持は別途確認する。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from itertools import product
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np
import trimesh

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hardware" / "src"))
import config as C  # noqa: E402


BATTERY_CANDIDATE_SIZE_MM = (34.0, 105.0, 24.0)
BATTERY_CANDIDATE_MASS_G = 180.0
WIRING_MISC_REFERENCE_MASS_G = 97.0


def _vector(values: Sequence[float] | None, *, name: str) -> np.ndarray | None:
    if values is None:
        return None
    result = np.asarray(tuple(values), dtype=float)
    if result.shape != (3,) or not np.isfinite(result).all():
        raise ValueError(f"{name} must contain three finite numbers")
    return result


def _size(values: Sequence[float], *, name: str) -> tuple[float, float, float]:
    result = _vector(values, name=name)
    assert result is not None
    if (result <= 0).any():
        raise ValueError(f"{name} must be positive")
    return tuple(float(v) for v in result)


def cannon_to_base_transform(*, cannon_translation_mm: Sequence[float] | None = None,
                             cannon_rotation_x_deg: float | None = None,
                             base_z_offset_mm: float | None = None) -> np.ndarray:
    """Return the single Cannon-local -> ``base_link`` transform.

    ``check_audio.py`` works in the Cannon-local frame.  ``export_urdf.py`` uses
    the same rotation and adds ``ZB`` (``config.HIP_DROP``) to the z translation.
    The defaults read those three values directly from ``config.py``; callers may
    override them for an explicitly named design variant.
    """
    translation = _vector(
        C.MOUTH_CANNON_T if cannon_translation_mm is None else cannon_translation_mm,
        name="cannon_translation_mm",
    )
    assert translation is not None
    rotation_deg = float(
        C.MOUTH_CANNON_ROT_X_DEG
        if cannon_rotation_x_deg is None else cannon_rotation_x_deg
    )
    z_offset = float(C.HIP_DROP if base_z_offset_mm is None else base_z_offset_mm)
    if not math.isfinite(rotation_deg) or not math.isfinite(z_offset):
        raise ValueError("cannon rotation and base z offset must be finite")
    matrix = trimesh.transformations.translation_matrix(
        [translation[0], translation[1], translation[2] + z_offset]
    )
    matrix = matrix @ trimesh.transformations.rotation_matrix(
        math.radians(rotation_deg), [1.0, 0.0, 0.0]
    )
    return np.asarray(matrix, dtype=float)


def _pocket_geometry() -> dict[str, dict[str, object]]:
    """Return the exact local dummy geometry used by ``check_audio.py``.

    The microphone occupies ``AUDIO_MIC_Y0..Y1``.  The real speaker dummy is
    placed at the muzzle side, ``AUDIO_SPK_Y1 - AUDIO_SPK_REAL_H..Y1``; the
    deeper baffle interval is not part of the speaker occupancy.
    """
    mic_y0 = float(C.AUDIO_MIC_Y0)
    mic_y1 = float(C.AUDIO_MIC_Y1)
    spk_y0 = float(C.AUDIO_SPK_Y1 - C.AUDIO_SPK_REAL_H)
    spk_y1 = float(C.AUDIO_SPK_Y1)
    if not mic_y1 > mic_y0:
        raise ValueError("invalid microphone pocket interval")
    if not spk_y1 > spk_y0:
        raise ValueError("invalid speaker pocket interval")

    mic_size = (float(C.AUDIO_MIC_L), float(C.AUDIO_MIC_W), float(C.AUDIO_MIC_T))
    # ``size_mm`` is the local XYZ bounding-box size.  The speaker cylinder is
    # rotated from trimesh's local Z axis to Cannon-local Y by Rx(+90 deg), so
    # its local envelope is [diameter, axis-height, diameter], not a box with
    # [diameter, diameter, height].  The actual cylinder mesh is returned by
    # ``get_print_first_component_meshes`` for integrations that need inertia.
    speaker_size = (float(C.AUDIO_SPK_D), float(C.AUDIO_SPK_REAL_H),
                    float(C.AUDIO_SPK_D))
    mic_center_y = (mic_y0 + mic_y1) / 2.0
    speaker_center_y = (spk_y0 + spk_y1) / 2.0
    return {
        "mic_cannon": {
            "kind": "box",
            "size_mm": list(mic_size),
            "local_center_mm": [0.0, mic_center_y, 0.0],
            "local_interval_y_mm": [mic_y0, mic_y1],
            "source": "tools/check_audio.py mic_dummy",
            "dimension_status": "UNVERIFIED_PURCHASE_DIMENSIONS",
        },
        "speaker_cannon": {
            "kind": "cylinder_axis_y",
            "diameter_mm": float(C.AUDIO_SPK_D),
            "height_mm": float(C.AUDIO_SPK_REAL_H),
            "size_mm": list(speaker_size),
            "local_center_mm": [0.0, speaker_center_y, 0.0],
            "local_interval_y_mm": [spk_y0, spk_y1],
            "source": "tools/check_audio.py spk_dummy",
            "dimension_status": "UNVERIFIED_PURCHASE_DIMENSIONS",
        },
    }


def _box_mesh(size_mm: Sequence[float], center_mm: Sequence[float]) -> trimesh.Trimesh:
    mesh = trimesh.creation.box(extents=_size(size_mm, name="box size"))
    mesh.apply_translation(_vector(center_mm, name="box center"))
    return mesh


def _speaker_mesh(diameter_mm: float, height_mm: float,
                  center_mm: Sequence[float]) -> trimesh.Trimesh:
    if not math.isfinite(diameter_mm) or not math.isfinite(height_mm):
        raise ValueError("speaker dimensions must be finite")
    if diameter_mm <= 0 or height_mm <= 0:
        raise ValueError("speaker dimensions must be positive")
    mesh = trimesh.creation.cylinder(
        radius=diameter_mm / 2.0, height=height_mm, sections=64
    )
    # check_audio.py creates a z-axis cylinder and applies Rx(+90 deg), making
    # its cylinder axis the Cannon-local Y axis.
    mesh.apply_transform(trimesh.transformations.rotation_matrix(
        math.pi / 2.0, [1.0, 0.0, 0.0]
    ))
    mesh.apply_translation(_vector(center_mm, name="speaker center"))
    return mesh


def _mesh_summary(mesh: trimesh.Trimesh, transform: np.ndarray) -> dict[str, object]:
    transformed = mesh.copy()
    transformed.apply_transform(transform)
    low, high = transformed.bounds
    center = transformed.centroid
    return {
        "center_base_mm": center.astype(float).tolist(),
        "bounds_base_mm": [low.astype(float).tolist(), high.astype(float).tolist()],
        "volume_mm3": float(transformed.volume),
        "finite_geometry": bool(np.isfinite(transformed.vertices).all()),
    }


def _component_mesh(name: str, geometry: Mapping[str, object]) -> trimesh.Trimesh:
    center = geometry["local_center_mm"]
    if name == "mic_cannon":
        return _box_mesh(geometry["size_mm"], center)
    if name == "speaker_cannon":
        return _speaker_mesh(float(geometry["diameter_mm"]),
                             float(geometry["height_mm"]), center)
    raise KeyError(name)


def get_print_first_components(
    *,
    battery_size_mm: Sequence[float] = BATTERY_CANDIDATE_SIZE_MM,
    battery_mass_g: float = BATTERY_CANDIDATE_MASS_G,
    battery_reference_center_mm: Sequence[float] | None = None,
    cannon_transform: np.ndarray | None = None,
) -> dict[str, object]:
    """Return candidate occupancy/mass records in one ``base_link`` frame.

    ``battery_reference_center_mm`` is deliberately an argument.  It must be a
    base-link coordinate selected by the mechanical integration after the shelf
    and battery insertion route are known.  When omitted, the battery remains
    explicitly unplaced while its candidate envelope and mass are retained.
    """
    battery_size = _size(battery_size_mm, name="battery_size_mm")
    if not math.isfinite(float(battery_mass_g)) or float(battery_mass_g) < 0:
        raise ValueError("battery_mass_g must be a finite non-negative number")
    battery_center = _vector(battery_reference_center_mm,
                             name="battery_reference_center_mm")
    transform = (cannon_to_base_transform() if cannon_transform is None
                 else np.asarray(cannon_transform, dtype=float))
    if transform.shape != (4, 4) or not np.isfinite(transform).all():
        raise ValueError("cannon_transform must be a finite 4x4 matrix")

    geometry = _pocket_geometry()
    components: list[dict[str, object]] = []

    battery: dict[str, object] = {
        "id": "battery_2s_2200mah_candidate",
        "kind": "box",
        "size_mm": list(battery_size),
        "mass_g": float(battery_mass_g),
        "mass_status": "CANDIDATE_UNVERIFIED",
        "dimension_status": "UNVERIFIED_PURCHASE_DIMENSIONS",
        "frame": "base_link",
        "source": "export_urdf.py base_link_electronics_items candidate dimensions/mass; copied without calling that function",
        "occupancy_status": "PLACED_CANDIDATE" if battery_center is not None else "UNPLACED_REFERENCE",
    }
    if battery_center is None:
        battery["reference_center_base_mm"] = None
        battery["bounds_base_mm"] = None
    else:
        battery["reference_center_base_mm"] = battery_center.astype(float).tolist()
        battery_mesh = _box_mesh(battery_size, battery_center)
        battery["bounds_base_mm"] = [
            battery_mesh.bounds[0].astype(float).tolist(),
            battery_mesh.bounds[1].astype(float).tolist(),
        ]
        battery["volume_mm3"] = float(battery_mesh.volume)
    components.append(battery)

    for name, local in geometry.items():
        mesh = _component_mesh(name, local)
        row = dict(local)
        row.update({
            "id": name + "_real_pocket_candidate",
            "mass_g": None,
            "mass_status": "UNVERIFIED_PURCHASE_MASS",
            "frame": "base_link",
            "cannon_local_center_mm": list(local["local_center_mm"]),
            "transform_cannon_to_base": transform.astype(float).tolist(),
        })
        row.update(_mesh_summary(mesh, transform))
        components.append(row)

    return {
        "schema_version": 1,
        "status": "CANDIDATE_OCCUPANCY_ONLY",
        "coordinate_frame": {
            "output": "base_link (URDF export convention)",
            "input": "Mouth_Cannon local; x/y/z in millimetres",
            "transform_application": "Cannon-local geometry is rotated Rx(MOUTH_CANNON_ROT_X_DEG), then translated by MOUTH_CANNON_T with HIP_DROP added to z; exactly once",
            "transform_cannon_to_base": transform.astype(float).tolist(),
        },
        "components": components,
        "mass_reference_candidates_not_applied": {
            "mic_cannon": {
                "mass_g": 1.0,
                "status": "LEGACY_URDF_ESTIMATE_UNVERIFIED_NOT_APPLIED",
                "source": "export_urdf.py legacy estimate; integration may pass it explicitly after review",
            },
            "speaker_cannon": {
                "mass_g": 3.0,
                "status": "LEGACY_URDF_ESTIMATE_UNVERIFIED_NOT_APPLIED",
                "source": "export_urdf.py legacy estimate; integration may pass it explicitly after review",
            },
        },
        "excluded_mass_reference": {
            "id": "wiring_misc",
            "mass_reference_g": WIRING_MISC_REFERENCE_MASS_G,
            "solid_box": False,
            "included_in_occupancy": False,
            "included_in_component_mass_total": False,
            "status": "UNMODELED_RESIDUAL_REFERENCE",
            "reason": "配線・ねじ・スイッチ等の残差であり、固体箱として干渉検査へ入れない",
        },
        "checks": {
            "config_audio_dimensions_read": True,
            "pocket_coordinates_match_check_audio_formulas": True,
            "legacy_mic_box_not_used": True,
            "legacy_speaker_box_not_used": True,
            "wiring_misc_not_solidified": True,
            "battery_position_explicit_or_unplaced": True,
        },
        "notes": [
            "マイク/スピーカーの寸法はconfig.pyの候補値で、対象個体の実測値ではない",
            "電池の基準位置は呼び出し側の引数で与え、未指定なら収納済みと扱わない",
            "この出力は頭部・棚・配線・固定の干渉合格や実機質量を意味しない",
        ],
    }


def _meshes_from_component_records(diagnostic: Mapping[str, object]) -> dict[str, trimesh.Trimesh]:
    """Materialize the same records as meshes already expressed in base_link."""
    transform = np.asarray(
        diagnostic["coordinate_frame"]["transform_cannon_to_base"], dtype=float
    )
    meshes: dict[str, trimesh.Trimesh] = {}
    for row in diagnostic["components"]:
        component_id = row["id"]
        if component_id == "battery_2s_2200mah_candidate":
            center = row.get("reference_center_base_mm")
            if center is None:
                continue
            mesh = _box_mesh(row["size_mm"], center)
        else:
            base_name = component_id.removesuffix("_real_pocket_candidate")
            mesh = _component_mesh(base_name, row)
            mesh.apply_transform(transform)
        if not isinstance(mesh, trimesh.Trimesh):
            raise TypeError(f"component mesh is not a trimesh.Trimesh: {component_id}")
        if not np.isfinite(mesh.vertices).all():
            raise ValueError(f"component mesh has non-finite vertices: {component_id}")
        meshes[component_id] = mesh
    return meshes


def get_print_first_component_bundle(
    *,
    battery_size_mm: Sequence[float] = BATTERY_CANDIDATE_SIZE_MM,
    battery_mass_g: float = BATTERY_CANDIDATE_MASS_G,
    battery_reference_center_mm: Sequence[float] | None = None,
    cannon_transform: np.ndarray | None = None,
) -> tuple[dict[str, object], dict[str, trimesh.Trimesh]]:
    """Return ``(record, meshes)`` using one coordinate/materialization path.

    The first value is JSON-safe diagnostic metadata.  The second contains the
    battery box (when a center was supplied), microphone box, and speaker
    cylinder, all in ``base_link`` millimetres.  Callers that need inertia or
    collision geometry can use these meshes directly rather than recreating the
    Cannon rotation and pocket offsets.
    """
    diagnostic = get_print_first_components(
        battery_size_mm=battery_size_mm,
        battery_mass_g=battery_mass_g,
        battery_reference_center_mm=battery_reference_center_mm,
        cannon_transform=cannon_transform,
    )
    return diagnostic, _meshes_from_component_records(diagnostic)


def get_print_first_component_meshes(
    *,
    battery_size_mm: Sequence[float] = BATTERY_CANDIDATE_SIZE_MM,
    battery_mass_g: float = BATTERY_CANDIDATE_MASS_G,
    battery_reference_center_mm: Sequence[float] | None = None,
    cannon_transform: np.ndarray | None = None,
) -> dict[str, trimesh.Trimesh]:
    """Return the base-link meshes from the same JSON-safe component records."""
    _, meshes = get_print_first_component_bundle(
        battery_size_mm=battery_size_mm,
        battery_mass_g=battery_mass_g,
        battery_reference_center_mm=battery_reference_center_mm,
        cannon_transform=cannon_transform,
    )
    return meshes


def _repo_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        return str(path)


def _draw_box(ax, bounds: Sequence[Sequence[float]], *, color: str, label: str,
              linestyle: str = "-") -> None:
    low = np.asarray(bounds[0], dtype=float)
    high = np.asarray(bounds[1], dtype=float)
    corners = np.asarray(list(product(*[(low[i], high[i]) for i in range(3)])))
    edges = []
    for i in range(8):
        for j in range(i + 1, 8):
            if np.sum(corners[i] != corners[j]) == 1:
                edges.append((corners[i], corners[j]))
    for a, b in edges:
        ax.plot([a[0], b[0]], [a[1], b[1]], [a[2], b[2]],
                color=color, linestyle=linestyle, linewidth=1.0)
    center = (low + high) / 2.0
    ax.text(center[0], center[1], center[2], label, color=color, fontsize=8)


def render_diagnostic_figure(diagnostic: Mapping[str, object], output_path: Path) -> None:
    """Render a compact base-frame occupancy figure without loading CAD meshes."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig = plt.figure(figsize=(8.2, 6.2), dpi=150)
    ax = fig.add_subplot(111, projection="3d")
    colors = {"battery_2s_2200mah_candidate": "#355cde",
              "mic_cannon_real_pocket_candidate": "#d97706",
              "speaker_cannon_real_pocket_candidate": "#15803d"}
    centers = []
    for row in diagnostic["components"]:
        name = row["id"]
        bounds = row.get("bounds_base_mm")
        if bounds is None:
            continue
        centers.append(np.mean(np.asarray(bounds, dtype=float), axis=0))
        _draw_box(ax, bounds, color=colors.get(name, "#333333"),
                  label=name.replace("_candidate", ""),
                  linestyle="--" if "speaker" in name else "-")
    if centers:
        pts = np.asarray(centers)
        ax.scatter(pts[:, 0], pts[:, 1], pts[:, 2], c="#111827", s=12)
    ax.set_xlabel("base x [mm]")
    ax.set_ylabel("base y [mm]")
    ax.set_zlabel("base z [mm]")
    ax.set_title("Print-first electronics occupancy candidates\n"
                 "candidate dimensions; physical fit UNVERIFIED")
    ax.view_init(elev=18, azim=-62)
    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)


def build_diagnostic(*, battery_center_mm: Sequence[float] | None = None,
                     battery_size_mm: Sequence[float] = BATTERY_CANDIDATE_SIZE_MM,
                     battery_mass_g: float = BATTERY_CANDIDATE_MASS_G) -> dict[str, object]:
    diagnostic = get_print_first_components(
        battery_size_mm=battery_size_mm,
        battery_mass_g=battery_mass_g,
        battery_reference_center_mm=battery_center_mm,
    )
    diagnostic["generated_by"] = _relative(Path(__file__))
    diagnostic["source_hashes"] = {
        "tools/print_first_components.py": _repo_sha256(Path(__file__)),
        "tools/check_audio.py": _repo_sha256(ROOT / "tools/check_audio.py"),
        "hardware/src/config.py": _repo_sha256(ROOT / "hardware/src/config.py"),
    }
    return diagnostic


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-json", type=Path,
                        default=ROOT / "outputs/print-first-20260905/components/diagnostic.json")
    parser.add_argument("--output-figure", type=Path,
                        default=ROOT / "outputs/print-first-20260905/components/diagnostic.png")
    parser.add_argument("--battery-center", type=float, nargs=3, metavar=("X", "Y", "Z"),
                        help="battery candidate center in base_link mm; omit to keep it unplaced")
    parser.add_argument("--battery-size", type=float, nargs=3, default=BATTERY_CANDIDATE_SIZE_MM,
                        metavar=("X", "Y", "Z"))
    parser.add_argument("--battery-mass-g", type=float, default=BATTERY_CANDIDATE_MASS_G)
    args = parser.parse_args()
    diagnostic = build_diagnostic(
        battery_center_mm=args.battery_center,
        battery_size_mm=args.battery_size,
        battery_mass_g=args.battery_mass_g,
    )
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(diagnostic, ensure_ascii=False, indent=2) + "\n",
                                encoding="utf-8")
    render_diagnostic_figure(diagnostic, args.output_figure)
    print(json.dumps({
        "json": _relative(args.output_json),
        "figure": _relative(args.output_figure),
        "component_count": len(diagnostic["components"]),
        "battery_placed": diagnostic["components"][0]["occupancy_status"] == "PLACED_CANDIDATE",
        "status": diagnostic["status"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
