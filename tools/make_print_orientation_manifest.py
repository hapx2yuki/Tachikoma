#!/usr/bin/env python3
"""body/legsの印刷向きを独立台帳へ書き出す。

組立用STLは組立座標のまま保存されているため、スライサーへ渡す向きと
ベッド移動をこの台帳で明示する。``assembly.json`` やSTL自体は変更しない。
平面が確定している部品だけを候補向きへ置き、複雑部品は実STLから
幾何上の支持面・荷重方向候補を記録する。反り、層間強度、実機適合は
別の物理確認事項として扱い、未確認の支持条件を合格扱いにしない。
"""
from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import trimesh

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hardware/src"), str(ROOT / "tools")]
import config as C

CONFIG_PATH = ROOT / "hardware/src/config.py"


DEFAULT_BODY = ROOT / "outputs/print-first-20260905/body/assembly.json"
DEFAULT_LEGS = ROOT / "outputs/print-first-20260905/legs/assembly.json"
DEFAULT_JSON = ROOT / "docs/print-first-orientation-manifest.json"
DEFAULT_MD = ROOT / "docs/print-first-orientation-manifest.md"


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _public_path(path: Path) -> str:
    path = Path(path).resolve()
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        digest = _sha(path)[:16] if path.is_file() else hashlib.sha256(
            str(path).encode("utf-8")
        ).hexdigest()[:16]
        return f"$EXTERNAL/{path.name}#{digest}"


def _rotation_matrix(rotation_deg_xyz: tuple[float, float, float]) -> np.ndarray:
    """外側XYZ順で回転する4x4行列を返す（mm座標）。"""
    rx, ry, rz = np.radians(rotation_deg_xyz)
    cx, sx = np.cos(rx), np.sin(rx)
    cy, sy = np.cos(ry), np.sin(ry)
    cz, sz = np.cos(rz), np.sin(rz)
    rx_m = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]], dtype=float)
    ry_m = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]], dtype=float)
    rz_m = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]], dtype=float)
    matrix = np.eye(4)
    matrix[:3, :3] = rz_m @ ry_m @ rx_m
    return matrix


def _bed_placement(mesh: trimesh.Trimesh, rotation_deg_xyz: tuple[float, float, float]) -> dict:
    """回転後のXYを中央へ、最低Zを0へ移す候補変換を計算する。"""
    if not isinstance(mesh, trimesh.Trimesh) or len(mesh.vertices) < 4 or len(mesh.faces) < 4:
        raise ValueError("orientation source STL is empty")
    if (not np.isfinite(mesh.vertices).all() or not np.isfinite(mesh.faces).all()
            or not mesh.is_watertight or not mesh.is_winding_consistent
            or not np.isfinite(mesh.volume) or mesh.volume <= 0 or not mesh.is_volume):
        raise ValueError("orientation source STL is not a finite positive solid")
    if len(mesh.split(only_watertight=False)) != 1:
        raise ValueError("orientation source STL is not a single solid")
    rotation = _rotation_matrix(rotation_deg_xyz)
    rotated = mesh.copy()
    rotated.apply_transform(rotation)
    low, high = rotated.bounds
    translation = np.array(
        [-float((low[0] + high[0]) / 2),
         -float((low[1] + high[1]) / 2),
         -float(low[2])],
        dtype=float,
    )
    transform = np.eye(4)
    transform[:3, :3] = rotation[:3, :3]
    transform[:3, 3] = translation
    placed = mesh.copy()
    placed.apply_transform(transform)
    placed_low, placed_high = placed.bounds
    z_min = float(placed_low[2])
    # STLの量子化を吸収する局所診断。支持面の存在を記録するだけで、
    # 印刷成功・反り・層間強度を判定しない。
    face_z = placed.triangles[:, :, 2]
    support = np.max(np.abs(face_z - z_min), axis=1) <= 0.05
    support_area = float(placed.area_faces[support].sum())
    return {
        "rotation_deg_xyz": [float(v) for v in rotation_deg_xyz],
        "rotation_matrix": rotation.tolist(),
        "bed_translation_mm": translation.tolist(),
        "bed_bounds_mm": [placed_low.tolist(), placed_high.tolist()],
        "support_face_count": int(support.sum()),
        "support_face_area_mm2": support_area,
        "support_plane_status": (
            "CANDIDATE_GEOMETRIC_PLANE_DETECTED" if support_area > 0.0
            else "CANDIDATE_GEOMETRIC_PLANE_NOT_DETECTED"
        ),
    }


def _cap_expected_countersink_side(name: str) -> str:
    """部品名から、実STLで確認すべき皿頭側の軸向きを返す。"""
    if name.startswith("pf_ld220_yaw_cap_"):
        # yaw cap is made with the canonical +Z countersink and mounted with
        # Rx(180), so its actual countersink face is local -Z.
        return "-Z"
    if name.endswith("_m"):
        # The source mirror flips the pitch/knee cap's +Y face to -Y.
        return "-Y"
    return "+Y"


def _axis_label(axis: int, sign: int) -> str:
    return ("+" if sign > 0 else "-") + "XYZ"[axis]


def _cap_side_metrics(
    mesh: trimesh.Trimesh,
    *,
    thickness_axis: int,
    long_axis: int,
    transverse_axis: int,
    long_center: float,
) -> dict[str, float]:
    """実STLの皿穴周辺の開口半径を、厚み両側で測る。

    ケース蓋は長手軸の端から約4 mmの位置に2本のねじ穴を持つ。
    各側の表面頂点を実メッシュから拾い、穴中心の最小半径を測る。
    皿頭側は平面側より大きな開口になるため、鏡像を含めて面の向きを
    ファイルの形状そのものから判定できる。
    """
    bounds = np.asarray(mesh.bounds, dtype=float)
    extents = np.asarray(mesh.extents, dtype=float)
    if bounds.shape != (2, 3) or not np.all(np.isfinite(bounds)):
        raise ValueError("cap bounds are not finite")
    side_tolerance = max(1.0e-4, float(extents[thickness_axis]) * 1.0e-5)
    # Both possible ends are tried because yaw caps can be rotated 180 degrees
    # around Z; pitch/knee caps use the first candidate.
    long_candidates = (
        float(bounds[0, long_axis] + 4.0),
        float(bounds[1, long_axis] - 4.0),
    )
    transverse_center = float(np.mean(bounds[:, transverse_axis]))
    points = np.asarray(mesh.vertices, dtype=float)
    if not np.all(np.isfinite(points)):
        raise ValueError("cap vertices are not finite")
    side_metrics: dict[str, float] | None = None
    selected_long_center: float | None = None
    for long_center_candidate in long_candidates:
        candidate: dict[str, float] = {}
        for side_index, side_sign in ((0, -1), (1, 1)):
            side = float(bounds[side_index, thickness_axis])
            side_points = points[
                np.abs(points[:, thickness_axis] - side) <= side_tolerance
            ]
            if len(side_points) == 0:
                continue
            radii: list[float] = []
            for transverse_center_offset in (-7.0, 7.0):
                center = np.zeros(2, dtype=float)
                center[0] = long_center_candidate
                center[1] = transverse_center + transverse_center_offset
                planar = side_points[:, [long_axis, transverse_axis]]
                distance = np.linalg.norm(planar - center, axis=1)
                near = distance[(distance >= 0.5) & (distance <= 6.0)]
                if len(near):
                    # The low percentile is stable for a triangulated circle,
                    # while still handling the femur cap's larger relief cut.
                    radii.append(float(np.percentile(near, 5.0)))
            if radii:
                candidate[_axis_label(thickness_axis, side_sign)] = float(np.mean(radii))
        if len(candidate) == 2:
            side_metrics = candidate
            selected_long_center = long_center_candidate
            break
    if side_metrics is None or selected_long_center is None:
        raise ValueError("cap countersink probe could not find both face sides")
    return {
        **side_metrics,
        "probe_long_center_mm": selected_long_center,
        "probe_transverse_center_mm": transverse_center,
    }


def _cap_geometry_check(
    mesh: trimesh.Trimesh,
    *,
    expected_side: str,
    rotation_deg_xyz: tuple[float, float, float],
) -> dict:
    """皿穴の実開口と、印刷回転後の上向きを確認する。"""
    extents = np.asarray(mesh.extents, dtype=float)
    if extents.shape != (3,) or not np.all(np.isfinite(extents)):
        raise ValueError("cap extents are not finite")
    thickness_axis = int(np.argmin(extents))
    planar_axes = [axis for axis in range(3) if axis != thickness_axis]
    # The cap's 19.5 mm direction contains the two screw centers; the other
    # planar direction is the 27.04 mm face width.
    long_axis = int(min(planar_axes, key=lambda axis: extents[axis]))
    transverse_axis = int(max(planar_axes, key=lambda axis: extents[axis]))
    metrics = _cap_side_metrics(
        mesh,
        thickness_axis=thickness_axis,
        long_axis=long_axis,
        transverse_axis=transverse_axis,
        long_center=0.0,
    )
    side_values = {
        key: value for key, value in metrics.items() if key in {
            _axis_label(thickness_axis, -1), _axis_label(thickness_axis, 1)
        }
    }
    detected_side = max(side_values, key=side_values.get)
    opening_difference = float(
        side_values[detected_side] - side_values[
            _axis_label(thickness_axis, -1)
            if detected_side == _axis_label(thickness_axis, 1)
            else _axis_label(thickness_axis, 1)
        ]
    )
    if detected_side != expected_side:
        orientation_status = "FAIL_COUNTERSINK_SIDE_MISMATCH"
    elif not np.isfinite(opening_difference) or opening_difference <= 0.30:
        orientation_status = "FAIL_COUNTERSINK_FEATURE_NOT_DISTINGUISHABLE"
    else:
        orientation_status = "PASS_COUNTERSINK_SIDE_FROM_ACTUAL_MESH"

    labels = {"+X": np.array([1.0, 0.0, 0.0]), "-X": np.array([-1.0, 0.0, 0.0]),
              "+Y": np.array([0.0, 1.0, 0.0]), "-Y": np.array([0.0, -1.0, 0.0]),
              "+Z": np.array([0.0, 0.0, 1.0]), "-Z": np.array([0.0, 0.0, -1.0])}
    rotation = _rotation_matrix(rotation_deg_xyz)
    up_vector = rotation[:3, :3] @ labels[expected_side]
    counterbore_points_up = bool(np.allclose(up_vector, [0.0, 0.0, 1.0], atol=1.0e-6))
    if not counterbore_points_up:
        orientation_status = "FAIL_COUNTERSINK_NOT_UP"
    return {
        "status": orientation_status if counterbore_points_up else "FAIL_COUNTERSINK_NOT_UP",
        "expected_counterbore_side": expected_side,
        "detected_counterbore_side": detected_side,
        "thickness_axis": _axis_label(thickness_axis, 1)[1:],
        "opening_metric_mm": side_values,
        "opening_metric_difference_mm": opening_difference,
        "rotation_counterbore_vector_after": up_vector.tolist(),
        "counterbore_points_up": counterbore_points_up,
        "probe": {
            "long_axis": _axis_label(long_axis, 1)[1:],
            "transverse_axis": _axis_label(transverse_axis, 1)[1:],
            "long_center_mm": metrics["probe_long_center_mm"],
            "transverse_center_mm": metrics["probe_transverse_center_mm"],
        },
    }


def _material_settings(name: str) -> dict:
    """configの材料条件を読み、部品ごとの値を台帳へ写す。"""
    match = None
    config_key = None
    for prefix, spec in sorted(C.PRINT_FIRST_MATERIALS.items(),
                               key=lambda item: len(item[0]), reverse=True):
        if name.startswith(prefix):
            match = spec
            config_key = prefix
            break
    if match is None and name.startswith("pf_"):
        raise ValueError(
            f"{name}: print-first material rule is missing from config.PRINT_FIRST_MATERIALS"
        )
    if match is None:
        return {
            "config_key": None,
            "config_source": "hardware/src/config.py",
            "config_sha256": _sha(CONFIG_PATH),
            "material": "UNVERIFIED",
            "wall_mm": None,
            "infill_fraction": None,
            "infill_percent": None,
            "density_g_cm3": None,
            "strength_rule": None,
            "status": "CONFIG_RULE_NOT_FOUND",
        }
    material, wall, infill = match
    try:
        density = C.material_density_g_cm3(material)
    except ValueError:
        density = None
    strength = None
    if name.startswith(("pf_coxa_bracket", "pf_femur_link", "pf_tibia_link")):
        strength = dict(C.PRINT_FIRST_STRENGTH_RULE)
    return {
        "config_key": f"PRINT_FIRST_MATERIALS[{config_key!r}]",
        "config_source": "hardware/src/config.py",
        "config_sha256": _sha(CONFIG_PATH),
        "material": str(material),
        "wall_mm": float(wall),
        "infill_fraction": float(infill),
        "infill_percent": float(infill) * 100.0,
        "density_g_cm3": density,
        "strength_rule": strength,
        "status": "CONFIG_DERIVED_RECHECK_AFTER_FREEZE2",
    }


def _orientation_rule(name: str, *, section: str, role: str | None) -> dict:
    """実STLの荷重面/取付面から印刷向き候補を選ぶ。

    複雑部品の候補は、支持面・荷重軸・穴やサービス面の向きを実メッシュ
    から読んだ幾何上の提案であり、反り・層間強度・実機適合を検証した
    合格判定ではない。
    """
    if name == "pf_chassis":
        return {
            "status": "KNOWN_PLANE_CANDIDATE",
            "basis": "chassis_underside",
            "description": "シャーシ下面をベッドへ。",
            "rotation_deg_xyz": (0.0, 0.0, 0.0),
        }
    if name.startswith("pf_electronics_shelf_"):
        return {
            "status": "KNOWN_PLANE_CANDIDATE",
            "basis": "shelf_underside",
            "description": "棚下面をベッドへ。",
            "rotation_deg_xyz": (0.0, 0.0, 0.0),
        }
    if name.startswith("pf_ld220_yaw_cap_"):
        return {
            "status": "KNOWN_PLANE_CANDIDATE",
            "basis": "yaw_cap_mount_face",
            "description": "yaw蓋の皿頭側を上、平面側を下。X180度で上下を戻す。",
            "rotation_deg_xyz": (180.0, 0.0, 0.0),
            "expected_counterbore_side": "-Z",
        }
    if name.startswith("pf_cabin_rail_"):
        return {
            "status": "KNOWN_PLANE_CANDIDATE",
            "basis": "rail_side_face",
            "description": "railの広い側面（元x最大側）をベッドへ。",
            "rotation_deg_xyz": (0.0, 90.0, 0.0),
        }
    if role == "removable_case_cap" and name.startswith("pf_ld220_"):
        mirror = name.endswith("_m")
        return {
            "status": "KNOWN_PLANE_CANDIDATE",
            "basis": "pitch_knee_cap_flat_face",
            "description": (
                "pitch/knee蓋は実STLの皿頭側を上、平面側を下。"
                + ("鏡像はX-90度。" if mirror else "標準はX+90度。")
            ),
            "rotation_deg_xyz": (-90.0 if mirror else 90.0, 0.0, 0.0),
            "expected_counterbore_side": "-Y" if mirror else "+Y",
        }
    complex_status = "MECHANICAL_GEOMETRIC_CANDIDATE_PHYSICAL_PRINT_UNVERIFIED"
    if name == "pf_mouth_key":
        return {
            "status": complex_status,
            "basis": "mouth_key_broad_side_support_and_insertion_axis",
            "description": "実STLの広い側面を下。キーの挿入長手軸をベッド面内に保つ幾何候補。",
            "rotation_deg_xyz": (0.0, 90.0, 0.0),
            "mechanical_basis": "actual_STL_support_face_scan_broad_side_candidate",
            "load_direction": "mouth_key_insertion_and_retention_axis_in_bed_plane",
            "service_face": "cannon_keyway_face_up_candidate",
        }
    if name == "pf_camera_carrier":
        return {
            "status": complex_status,
            "basis": "camera_carrier_broad_side_and_tray_service_direction",
            "description": "実STLの広い側面を下。光学窓/トレーサービス側を上側へ向ける幾何候補。",
            "rotation_deg_xyz": (0.0, 90.0, 0.0),
            "mechanical_basis": "actual_STL_support_face_scan_broad_side_candidate",
            "load_direction": "XIAO_tray_to_carrier_beam_load_in_bed_plane",
            "service_face": "camera_window_and_XIAO_port_side_up_candidate",
        }
    if name == "pf_head_top_clearanced":
        return {
            "status": complex_status,
            "basis": "head_shell_original_underside_and_clearance_rim",
            "description": "頭殻の元の下面をベッドへ。目開口/外装側を上へ置く幾何候補。",
            "rotation_deg_xyz": (0.0, 0.0, 0.0),
            "mechanical_basis": "actual_STL_head_shell_underside_and_clearance_obstacle_scan",
            "load_direction": "head_shell_support_normal_to_print_bed",
            "service_face": "eye_camera_opening_up_candidate",
        }
    if name == "pf_eye_pod_camera_clearanced":
        return {
            "status": complex_status,
            "basis": "camera_pod_mount_base_and_optical_opening",
            "description": "中央podの取付基部をベッドへ。レンズ/基板サービス側を上へ置く幾何候補。",
            "rotation_deg_xyz": (0.0, 0.0, 0.0),
            "mechanical_basis": "actual_STL_camera_pod_mount_base_and_clearance_scan",
            "load_direction": "camera_pod_mount_load_normal_to_print_bed",
            "service_face": "lens_and_fpc_service_side_up_candidate",
        }
    if name.startswith("pf_fixed_claw_"):
        mirror = name.endswith("_l")
        return {
            "status": complex_status,
            "basis": "fixed_claw_broad_root_support_and_adhesive_load_path",
            "description": (
                "固定爪の広い根元側を下。接着/保持荷重を積層面内へ置く幾何候補。"
                + ("左鏡像。" if mirror else "右標準。")
            ),
            "rotation_deg_xyz": (0.0, 90.0 if mirror else 270.0, 0.0),
            "mechanical_basis": "actual_STL_support_face_scan_broad_root_candidate",
            "load_direction": "forearm_claw_retention_load_in_bed_plane",
            "service_face": "claw_opening_up_candidate",
        }
    if name.startswith("pf_coxa_bracket"):
        return {
            "status": complex_status,
            "basis": "coxa_mount_plate_and_yaw_riser_underside",
            "description": "coxa取付板/ヨー立上がりの下面を下。ヨー軸とホーン座を水平に保つ幾何候補。",
            "rotation_deg_xyz": (0.0, 0.0, 0.0),
            "mechanical_basis": "actual_STL_plate_underside_and_yaw_load_axis_scan_candidate",
            "load_direction": "yaw_riser_load_normal_to_coxa_plate",
            "service_face": "LD_yaw_case_and_fastener_face_up_candidate",
        }
    if name.startswith("pf_femur_link"):
        mirror = name.endswith("_m")
        return {
            "status": complex_status,
            "basis": "femur_horn_face_up_and_bending_axis_in_bed_plane",
            "description": (
                "femurのホーン面を上、長手荷重軸をベッド面内へ置く幾何候補。"
                + ("鏡像はX-90度。" if mirror else "標準はX+90度。")
            ),
            "rotation_deg_xyz": (-90.0 if mirror else 90.0, 0.0, 0.0),
            "mechanical_basis": "actual_STL_link_load_axis_and_horn_face_scan_candidate",
            "load_direction": "knee_bending_load_in_layer_plane",
            "service_face": "LD_pitch_horn_and_cap_face_up_candidate",
        }
    if name.startswith("pf_tibia_link"):
        mirror = name.endswith("_m")
        return {
            "status": complex_status,
            "basis": "tibia_horn_face_up_and_foot_load_axis_in_bed_plane",
            "description": (
                "tibiaのホーン面を上、足先荷重の長手軸をベッド面内へ置く幾何候補。"
                + ("鏡像はX-90度。" if mirror else "標準はX+90度。")
            ),
            "rotation_deg_xyz": (-90.0 if mirror else 90.0, 0.0, 0.0),
            "mechanical_basis": "actual_STL_link_load_axis_and_horn_face_scan_candidate",
            "load_direction": "foot_contact_bending_load_in_bed_plane",
            "service_face": "LD_knee_horn_and_shoe_socket_face_up_candidate",
        }
    return {
        "status": "MECHANICAL_RECOMMENDATION_REQUIRED",
        "basis": None,
        "description": (
            "複雑なリンク/爪の支持面と向きは機械担当の推奨待ち。"
            "未確認の支持条件で向きを合格扱いにしない。"
        ),
        "rotation_deg_xyz": None,
        "expected_counterbore_side": None,
    }


def _load_assembly(path: Path, section: str) -> tuple[dict, list[dict]]:
    path = Path(path).resolve()
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("parts")
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"{section} assembly has no parts: {path}")
    return data, rows


def _assembly_source_validation(data: dict, *, section: str, assembly_path: Path) -> dict:
    """assembly.json の生成状態と source_sha256 の鮮度を検査する。"""
    failures = []
    status = data.get("status")
    if not isinstance(status, str) or not status.strip():
        failures.append(f"{section}: missing generation status")
    elif any(token in status.upper() for token in ("FAIL", "ERROR", "INVALID")):
        failures.append(f"{section}: assembly status is {status!r}")
    hashes = data.get("source_sha256")
    if not isinstance(hashes, dict) or not hashes:
        failures.append(f"{section}: source_sha256 is missing or empty")
        hashes = {}
    checked = []
    for raw_path, expected in hashes.items():
        if not isinstance(raw_path, str) or not raw_path:
            failures.append(f"{section}: invalid source_sha256 path {raw_path!r}")
            continue
        source_path = Path(raw_path)
        if not source_path.is_absolute():
            source_path = ROOT / source_path
        if not source_path.is_file():
            failures.append(f"{section}: source missing {raw_path}")
            continue
        actual = _sha(source_path)
        if not isinstance(expected, str) or expected != actual:
            failures.append(
                f"{section}: source_sha256 mismatch {raw_path} "
                f"expected={expected!r} actual={actual}"
            )
        checked.append({
            "path": _public_path(source_path),
            "expected_sha256": expected,
            "actual_sha256": actual,
            "status": "MATCH" if expected == actual else "MISMATCH",
        })
    return {
        "assembly_path": _public_path(assembly_path),
        "assembly_sha256": _sha(assembly_path),
        "generation_status": status,
        "source_sha256_count": len(checked),
        "source_sha256_checked": checked,
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
    }


def _make_row(row: dict, *, section: str, assembly_path: Path) -> dict:
    name = row.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError(f"{section} assembly has an unnamed part")
    raw_path = row.get("stl") or row.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        raise ValueError(f"{section}/{name} has no STL path")
    stl_path = Path(raw_path)
    if not stl_path.is_absolute():
        stl_path = ROOT / stl_path
    if not stl_path.is_file():
        raise FileNotFoundError(f"missing STL for {section}/{name}: {stl_path}")
    mesh = trimesh.load(stl_path, force="mesh")
    if not isinstance(mesh, trimesh.Trimesh) or len(mesh.vertices) < 4 or len(mesh.faces) < 4:
        raise ValueError(f"invalid STL for {section}/{name}: {stl_path}")
    if not np.isfinite(mesh.vertices).all() or not np.isfinite(mesh.faces).all():
        raise ValueError(f"non-finite STL for {section}/{name}: {stl_path}")
    if np.any(mesh.faces < 0) or np.any(mesh.faces >= len(mesh.vertices)):
        raise ValueError(f"out-of-range STL face index for {section}/{name}: {stl_path}")
    actual_sha = _sha(stl_path)
    expected_sha = row.get("sha256")
    hash_status = "UNRECORDED"
    if expected_sha:
        hash_status = "MATCH" if expected_sha == actual_sha else "MISMATCH"
    role = row.get("role")
    rule = _orientation_rule(name, section=section, role=role)
    quantity = row.get("quantity")
    if quantity is None:
        legs = row.get("legs")
        quantity = len(legs) if section == "legs" and isinstance(legs, list) and legs else 1
    try:
        quantity = int(quantity)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid quantity for {section}/{name}: {quantity!r}") from exc
    if quantity <= 0:
        raise ValueError(f"quantity must be positive for {section}/{name}")
    placement = None
    geometry_check = None
    if rule["rotation_deg_xyz"] is not None:
        placement = _bed_placement(mesh, rule["rotation_deg_xyz"])
        if rule.get("expected_counterbore_side") is not None:
            geometry_check = _cap_geometry_check(
                mesh,
                expected_side=rule["expected_counterbore_side"],
                rotation_deg_xyz=rule["rotation_deg_xyz"],
            )
    material = _material_settings(name)
    orientation_status = rule["status"]
    support_failure = None
    if placement is not None:
        support_area = float(placement["support_face_area_mm2"])
        if (not np.isfinite(support_area) or support_area <= 0.0
                or placement.get("support_plane_status") != "CANDIDATE_GEOMETRIC_PLANE_DETECTED"):
            support_failure = {
                "support_face_area_mm2": support_area,
                "support_plane_status": placement.get("support_plane_status"),
            }
            orientation_status = "GEOMETRY_SUPPORT_CHECK_FAIL"
    if geometry_check is not None and not geometry_check["status"].startswith("PASS_"):
        orientation_status = "GEOMETRY_CHECK_FAIL"
    return {
        "section": section,
        "name": name,
        "source_path": _public_path(stl_path),
        "source_sha256_expected": expected_sha,
        "source_sha256_actual": actual_sha,
        "source_hash_status": hash_status,
        "assembly_source": _public_path(assembly_path),
        "quantity": quantity,
        "quantity_source": "assembly.parts row; legs length when present",
        "instance_legs": list(row.get("legs", [])) if isinstance(row.get("legs"), list) else [],
        "quantity_semantics": "設計上の組立個数。適合試作/残数の分割は最終印刷manifestで管理する。",
        "role": role,
        "link_kind": row.get("link_kind"),
        "replaces": list(row.get("replaces", [])),
        "bbox_mm": row.get("bbox_mm"),
        "solid_pla_g_upper_bound": row.get("solid_pla_g_upper_bound"),
        "material": material,
        "orientation": {
            "status": orientation_status,
            "basis": rule["basis"],
            "description": rule["description"],
            "mechanical_basis": rule.get("mechanical_basis"),
            "load_direction": rule.get("load_direction"),
            "service_face": rule.get("service_face"),
            "physical_verification_status": (
                "UNVERIFIED_PRINT_ORIENTATION_WARP_LAYER_STRENGTH_AND_FIT"
                if rule["status"] == "MECHANICAL_GEOMETRIC_CANDIDATE_PHYSICAL_PRINT_UNVERIFIED"
                else "UNVERIFIED_UNLESS_EXPLICIT_GEOMETRY_CHECK"
            ),
            "placement": placement,
            "geometry_check": geometry_check,
            "support_validation": {
                "status": "FAIL" if support_failure else "PASS",
                "source": "orientation.placement.support_face_area_mm2",
                "failure": support_failure,
            },
            "support_interpretation": (
                "幾何上の候補面を置いただけ。印刷反り、層間強度、実物適合、"
                "スライサーの支持材を検証していない。"
            ),
        },
    }


def build_manifest(body_path: Path, legs_path: Path) -> dict:
    body_path = Path(body_path).resolve()
    legs_path = Path(legs_path).resolve()
    body, body_rows = _load_assembly(body_path, "body")
    legs, legs_rows = _load_assembly(legs_path, "legs")
    body_source_validation = _assembly_source_validation(
        body, section="body", assembly_path=body_path)
    legs_source_validation = _assembly_source_validation(
        legs, section="legs", assembly_path=legs_path)
    parts = [
        *[_make_row(row, section="body", assembly_path=body_path) for row in body_rows],
        *[_make_row(row, section="legs", assembly_path=legs_path) for row in legs_rows],
    ]
    mismatches = [
        f"{row['section']}/{row['name']}"
        for row in parts if row["source_hash_status"] == "MISMATCH"
    ]
    body_count = sum(row["quantity"] for row in parts if row["section"] == "body")
    legs_count = sum(row["quantity"] for row in parts if row["section"] == "legs")
    known = sum(
        row["quantity"] for row in parts
        if row["orientation"]["status"] == "KNOWN_PLANE_CANDIDATE"
    )
    review = sum(
        row["quantity"] for row in parts
        if row["orientation"]["status"] == "MECHANICAL_RECOMMENDATION_REQUIRED"
    )
    geometric_candidates = sum(
        row["quantity"] for row in parts
        if row["orientation"]["status"] == "MECHANICAL_GEOMETRIC_CANDIDATE_PHYSICAL_PRINT_UNVERIFIED"
    )
    unrecorded_hashes = [
        f"{row['section']}/{row['name']}"
        for row in parts if row["source_hash_status"] == "UNRECORDED"
    ]
    recommendation_required = [
        f"{row['section']}/{row['name']}"
        for row in parts
        if row["orientation"]["status"] == "MECHANICAL_RECOMMENDATION_REQUIRED"
    ]
    partition_is_disjoint = known + geometric_candidates + review == body_count + legs_count
    geometry_failures = [
        f"{row['section']}/{row['name']}:{row['orientation']['status']}"
        for row in parts
        if isinstance(row["orientation"].get("status"), str)
        and row["orientation"]["status"].startswith("GEOMETRY_")
        and "FAIL" in row["orientation"]["status"]
    ]
    support_failures = [
        f"{row['section']}/{row['name']}"
        for row in parts
        if row["orientation"].get("support_validation", {}).get("status") == "FAIL"
    ]
    fatal_failures = list(dict.fromkeys([
        *[f"source_hash:{item}" for item in mismatches],
        *[f"unrecorded_hash:{item}" for item in unrecorded_hashes],
        *body_source_validation["failures"],
        *legs_source_validation["failures"],
        *[f"recommendation_required:{item}" for item in recommendation_required],
        *(["status_partition_is_disjoint:false"] if not partition_is_disjoint else []),
        *[f"geometry:{item}" for item in geometry_failures],
        *[f"support:{item}" for item in support_failures],
    ]))
    return {
        "schema_version": 1,
        "status": "ORIENTATION_CANDIDATE_FREEZE2_PENDING",
        "as_of": date.today().isoformat(),
        "source_policy": {
            "assembly_json_unchanged": True,
            "stl_unchanged": True,
            "source_hashes_rechecked": True,
            "final_freeze2_required": True,
            "stale_hashes_are_not_relabelled": True,
        },
        "transform_convention": {
            "rotation": "extrinsic XYZ degrees, applied to source STL vertices about its origin",
            "bed_translation": "after rotation, XY center is 0 and minimum Z is 0",
            "bed_coordinate_units": "mm",
        },
        "orientation_rules": {
            "chassis": "chassis underside down; identity rotation",
            "shelf": "shelf underside down; identity rotation",
            "yaw_cap": "X180; countersink side up, flat side down",
            "pitch_knee_cap": "standard X+90 / mirror X-90; actual countersink side up, flat side down",
            "cabin_rail": "Y90; broad side face down",
            "complex_links": "actual-STL load/support candidate rotations are recorded separately from physical print verification",
        },
        "source_assemblies": {
            "body": {
                "path": _public_path(body_path),
                "sha256": body_source_validation["assembly_sha256"],
                "status": body.get("status"),
                "source_sha256": body.get("source_sha256", {}),
                "integrity": body_source_validation,
            },
            "legs": {
                "path": _public_path(legs_path),
                "sha256": legs_source_validation["assembly_sha256"],
                "status": legs.get("status"),
                "source_sha256": legs.get("source_sha256", {}),
                "integrity": legs_source_validation,
            },
        },
        "strength_rule": legs.get("material_policy", {}).get("strength_rule"),
        "strength_rule_basis": (
            "3.8kgf is already 1.9kgf base load x dynamic_factor 2.0; "
            "orientation ledger does not apply the dynamic factor again; "
            "printed material/layer strength remains UNVERIFIED"
        ),
        "material_rule_source": {
            "path": "hardware/src/config.py",
            "sha256": _sha(CONFIG_PATH),
            "mapping": "PRINT_FIRST_MATERIALS; per-part longest prefix",
            "density_lookup": "config.material_density_g_cm3(material)",
        },
        "counts": {
            "body_rows": len(body_rows),
            "body_design_instances": body_count,
            "legs_rows": len(legs_rows),
            "legs_design_instances": legs_count,
            "design_instances_total": body_count + legs_count,
            "known_plane_candidate_instances": known,
            "mechanical_geometric_candidate_instances": geometric_candidates,
            "mechanical_recommendation_required_instances": review,
            "status_partition_is_disjoint": (
                partition_is_disjoint
            ),
            "trial_and_remaining_split": "not represented here; do not infer production approval from quantity",
        },
        "parts": parts,
        "validation": {
            "source_hash_mismatches": mismatches,
            "source_hash_mismatch_count": len(mismatches),
            "all_hashes_match": not mismatches,
            "unrecorded_hashes": unrecorded_hashes,
            "unrecorded_hash_count": len(unrecorded_hashes),
            "recommendation_required": recommendation_required,
            "recommendation_required_count": len(recommendation_required),
            "body_source_validation": body_source_validation,
            "legs_source_validation": legs_source_validation,
            "geometry_failures": geometry_failures,
            "geometry_failure_count": len(geometry_failures),
            "support_failures": support_failures,
            "support_failure_count": len(support_failures),
            "fatal_validation_failures": fatal_failures,
            "fatal_validation_failure_count": len(fatal_failures),
            "fatal_validation_status": "PASS" if not fatal_failures else "FAIL",
            "candidate_support_geometry_is_not_physical_print_pass": True,
            "unknown_support_not_promoted": True,
            "status_is_review_candidate": True,
        },
    }


def _fmt(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.1f}"
    return str(value)


def render_markdown(data: dict) -> str:
    lines = [
        "# 印刷向き候補台帳（freeze2前）",
        "",
        "この台帳は`assembly.json`とSTLを変更せず、組立座標の部品をスライサーへ渡す候補回転とベッド移動を記録する。`ORIENTATION_CANDIDATE_FREEZE2_PENDING`であり、印刷・適合・強度・実機歩行の合格を示さない。freeze2後に同じ生成器を一度実行し、source SHAと数量を更新する。",
        "",
        f"- body設計個数: **{data['counts']['body_design_instances']}**（{data['counts']['body_rows']}行）",
        f"- legs設計個数: **{data['counts']['legs_design_instances']}**（{data['counts']['legs_rows']}行）",
        f"- 合計: **{data['counts']['design_instances_total']}**（body + legs。足・既存再使用品・試作/残数は最終印刷manifestで分離）",
        f"- 既知平面の候補: {data['counts']['known_plane_candidate_instances']}個。幾何候補（実印刷未確認）: {data['counts'].get('mechanical_geometric_candidate_instances', 0)}個。機械推奨待ち: {data['counts']['mechanical_recommendation_required_instances']}個。",
        f"- STL SHA不一致: {data['validation'].get('source_hash_mismatch_count', 0)}件。",
        f"- source_sha256 未記録: {data['validation'].get('unrecorded_hash_count', 0)}件、assembly生成状態/SHA鮮度: {data['validation'].get('body_source_validation', {}).get('status')} / {data['validation'].get('legs_source_validation', {}).get('status')}。",
        f"- 材料/密度の単一情報源: `hardware/src/config.py` SHA-256 `{data['material_rule_source']['sha256']}`。各行は`config.material_density_g_cm3(material)`を通じて記録。",
        f"- 機械推奨待ち: {data['validation'].get('recommendation_required_count', 0)}件。最終台帳では候補向き未確定として致命扱いにする。",
        f"- 幾何/支持の致命的失敗: **{data['validation'].get('fatal_validation_failure_count', 0)}件。**（支持面候補PASSは実印刷合格を意味しない）",
        "- 脚強度の荷重候補: **1.9kgf × 動的係数2.0 = 3.8kgf（3.8kgfは適用済みで二重適用しない）**。印刷材料・積層方向・試験強度は未確認。",
        "",
        "## 向きと支持面",
        "",
        "- `pf_chassis`: シャーシ下面を下、回転なし。",
        "- `pf_electronics_shelf_*`: 棚下面を下、回転なし。",
        "- `pf_ld220_yaw_cap_*`: X180度。皿頭側を上、平面側を下。",
        "- `pf_ld220_coxa_cap` / `pf_ld220_femur_cap`: X+90度。実STLの+Y側（皿頭側）を上、-Y側（平面側）を下。",
        "- `pf_ld220_coxa_cap_m` / `pf_ld220_femur_cap_m`: X-90度。実STLの-Y側（皿頭側）を上、+Y側（平面側）を下。",
        "- `pf_cabin_rail_*`: Y90度。広い側面を下。",
        "- coxa/femur/tibia本体、mouth/camera/claw: 実STLの荷重軸・取付面・支持面積から幾何候補を記録。反り・層間強度・実機適合は未確認。",
        "",
        "## 部品一覧",
        "",
        "| 区分 | 部品 | 設計個数 | 材料 | 壁/充填 | 向き候補 | ベッド移動 | 状態 |",
        "|---|---|---:|---|---|---|---|---|",
    ]
    for row in data["parts"]:
        material = row["material"]
        if material["wall_mm"] is None:
            setting = "未定"
        else:
            setting = f"{material['wall_mm']:.1f} mm / {material['infill_percent']:.0f}%"
        orientation = row["orientation"]
        placement = orientation["placement"]
        rotation = "—" if placement is None else "(" + ", ".join(
            _fmt(v) for v in placement["rotation_deg_xyz"]
        ) + ")°"
        translation = "—" if placement is None else "(" + ", ".join(
            _fmt(v) for v in placement["bed_translation_mm"]
        ) + ")"
        lines.append(
            f"| {row['section']} | `{row['name']}` | {row['quantity']} | {material['material']} | {setting} | {rotation} / {orientation['basis'] or '機械確認'} | {translation} | {orientation['status']} |"
        )
    lines += [
        "",
        "各行の完全なSTL相対path、期待/実測SHA、回転行列、ベッド境界、支持面面積、置換旧部品はJSONに保存する。設計個数は`assembly.parts`から集計した実装個数で、適合用1個と合格後の残数を意味しない。",
        "",
        "## freeze2後の更新条件",
        "",
        "1. 機械担当のsource/config完了宣言を受ける。",
        "2. body/legsの最終`assembly.json`とSTLを読み、SHA不一致があればそのまま不一致として止める。SHAだけを付け替えない。",
        "3. 複雑なリンクの推奨姿勢を反映し、印刷前に1靴・1脚の適合と全体CAD/物理試験のゲートを確認する。",
        "4. 最終印刷manifestでは設計必要数、適合試作の内数、合格後の残数、既存再使用品を二重計上しない。",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--body-assembly", type=Path, default=DEFAULT_BODY)
    parser.add_argument("--legs-assembly", type=Path, default=DEFAULT_LEGS)
    parser.add_argument("--output", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MD)
    args = parser.parse_args()
    data = build_manifest(args.body_assembly, args.legs_assembly)
    output = args.output if args.output.is_absolute() else ROOT / args.output
    markdown = args.markdown if args.markdown.is_absolute() else ROOT / args.markdown
    output.parent.mkdir(parents=True, exist_ok=True)
    markdown.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown.write_text(render_markdown(data), encoding="utf-8")
    print(json.dumps({
        "status": data["status"],
        "json": _public_path(output),
        "markdown": _public_path(markdown),
        "counts": data["counts"],
        "source_hash_mismatch_count": data["validation"]["source_hash_mismatch_count"],
        "geometry_failure_count": data["validation"]["geometry_failure_count"],
        "support_failure_count": data["validation"]["support_failure_count"],
        "fatal_validation_failure_count": data["validation"]["fatal_validation_failure_count"],
    }, ensure_ascii=False))
    return 0 if data["validation"]["fatal_validation_failure_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
