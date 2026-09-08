#!/usr/bin/env python3
"""三葉TPU靴の幾何/取付/支持/材料量を検査。剛体検査を実機合格にしない。"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

import numpy as np
import trimesh

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hardware/src"))
import config as C
import make_print_first_feet as F
from lib import box, cyl_y, to_trimesh
from mesh_checks import intersection_volume_mm3 as intersection


# A Manifold Boolean may expose a signed sub-mesh with a tiny negative volume
# when two serialized surfaces share a quantized boundary.  This is a narrow
# numerical-tolerance path only: status=NoError, finite closed topology, and a
# thin fragment are required.  It is separate from STL round-trip tolerance
# and the 0.01 mm3 physical intersection gate.
BOOLEAN_NEGATIVE_TOLERANCE_MM3 = float(
    getattr(C, "BOOLEAN_NEGATIVE_TOLERANCE_MM3", 1e-6))
# The shell-difference audit may split a quantized boundary into a closed,
# sub-0.001 mm3 sliver.  This larger bound applies only to those explicitly
# identified thin components after native NoError validation; it never applies
# to the top-level Boolean result or to a physical intersection gate.
NEGATIVE_FRAGMENT_VOLUME_TOLERANCE_MM3 = 1e-3
NEGATIVE_FRAGMENT_THICKNESS_TOL_MM = 0.02

FINAL_SIM_MODEL_KINDS = {"final_integrated", "print_first_final", "frozen_integrated"}
INVALID_SIMULATION_STATUSES = {
    "INVALID_INITIAL_CONTACT_MODEL",
    "NUMERIC_FAILURE",
    "NUMERIC_INSTABILITY",
    "NUMERICALLY_UNSTABLE",
    "FELL",
    "FALL",
    "ABORTED",
    "INCOMPLETE",
}
# Only this geometry-pass state may be consumed by the print-first generator.
# The checker still records physical bench work as a separate obligation.
PERMITTED_VERIFICATION_STATUS_STATES = frozenset({"PASS_GEOMETRY_BENCH_REQUIRED"})


def _is_sha256(value):
    """完全なSHA-256文字列かを確認する（経路名や短縮hashは受けない）。"""
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def load(path):
    m = trimesh.load(path, force="mesh")
    if not isinstance(m, trimesh.Trimesh) or len(m.vertices) < 4 or len(m.faces) < 4:
        raise ValueError(f"空または不完全な必須STL: {path}")
    vertices = np.asarray(m.vertices, dtype=float)
    faces = np.asarray(m.faces)
    if not np.isfinite(vertices).all() or not np.isfinite(faces.astype(float)).all():
        raise ValueError(f"有限でない頂点/面インデックスを含む必須STL: {path}")
    if faces.ndim != 2 or faces.shape[1] != 3 or faces.min(initial=0) < 0 or faces.max(initial=0) >= len(vertices):
        raise ValueError(f"面インデックスが不正な必須STL: {path}")
    volume = float(m.volume)
    if not math.isfinite(volume) or volume <= 0:
        raise ValueError(f"正体積でない必須STL: {path}")
    if (not m.is_watertight or not m.is_winding_consistent
            or not m.is_volume):
        raise ValueError(f"不正な必須STL: {path}")
    return m


def _validated_difference_parts(mesh, *, label, fragment_threshold_mm3=1e-3):
    """ブール差分と全成分を、符号を捨てずに閉体として検査する。

    `abs(volume)` で選別すると、面の向きが反転した負体積成分を正常な
    切除として扱ってしまう。空メッシュだけは差分なしとして許容し、非空の
    差分本体と各分割片は、有限・watertight・一貫した winding・非負体積を
    満たすことを先に確認する。差分本体の負体積は失敗とする。分割された負の
    薄片だけは、native Manifold の NoError、有限トポロジー、符号一致、薄さ、
    signed tolerance をすべて満たす場合に限り、監査項目として残して許容する。
    """
    if mesh is None:
        raise ValueError(f"{label}: 殻の差分計算不能")

    def _native_negative_fragment_is_valid(candidate, candidate_label, volume):
        """負の薄片を許容できる native Manifold の根拠を確認する。"""
        if not isinstance(candidate, trimesh.Trimesh):
            raise ValueError(
                f"{candidate_label}: tiny negative fragment must be a Trimesh")
        vertices = np.asarray(candidate.vertices, dtype=float)
        faces = np.asarray(candidate.faces)
        extents = np.ptp(vertices, axis=0) if len(vertices) else np.zeros(3)
        if (not np.isfinite(extents).all() or
                float(np.min(extents, initial=float("inf"))) > NEGATIVE_FRAGMENT_THICKNESS_TOL_MM):
            raise ValueError(
                f"{candidate_label}: negative fragment is not a thin numerical sliver")
        try:
            from manifold3d import Manifold, Mesh
            native = Manifold(Mesh(np.asarray(vertices, np.float32),
                                   np.asarray(faces, np.uint32)))
        except Exception as exc:
            raise ValueError(
                f"{candidate_label}: native Manifold conversion failed") from exc
        status = native.status()
        if getattr(status, "name", None) != "NoError":
            raise ValueError(
                f"{candidate_label}: native Manifold status is not NoError: {status}")
        native_volume = float(native.volume())
        if (not math.isfinite(native_volume) or native.num_vert() == 0 or
                native.num_tri() == 0):
            raise ValueError(
                f"{candidate_label}: native negative fragment topology/volume is invalid")
        # Keep the sign: a native positive volume after a negative serialized
        # component would indicate an orientation conversion error, not a
        # numerical remainder.
        if native_volume >= 0 or volume * native_volume <= 0:
            raise ValueError(
                f"{candidate_label}: native/serialized signed volume mismatch")
        if native_volume < -NEGATIVE_FRAGMENT_VOLUME_TOLERANCE_MM3:
            raise ValueError(
                f"{candidate_label}: negative fragment exceeds signed tolerance: "
                f"{native_volume!r}")
        return {
            "serialized_volume_mm3": float(volume),
            "native_volume_mm3": native_volume,
            "native_status": getattr(status, "name", str(status)),
            "bounds_extents_mm": extents.tolist(),
            "tolerance_mm3": NEGATIVE_FRAGMENT_VOLUME_TOLERANCE_MM3,
            "strict_boolean_tolerance_mm3": BOOLEAN_NEGATIVE_TOLERANCE_MM3,
        }

    def check_one(candidate, candidate_label, *, allow_tiny_negative=False):
        vertices = np.asarray(candidate.vertices, dtype=float)
        if vertices.size and not np.isfinite(vertices).all():
            raise ValueError(f"{candidate_label}: 頂点座標が有限ではない")
        if bool(getattr(candidate, "is_empty", False)) or len(candidate.faces) == 0:
            return None
        if not candidate.is_watertight:
            raise ValueError(f"{candidate_label}: 非空差分がwatertightではない")
        if not candidate.is_winding_consistent:
            raise ValueError(f"{candidate_label}: 非空差分のwindingが不一致")
        volume = float(candidate.volume)
        if not math.isfinite(volume):
            raise ValueError(
                f"{candidate_label}: 差分体積は有限である必要がある: {volume!r}")
        if volume < 0:
            if not allow_tiny_negative:
                raise ValueError(
                    f"{candidate_label}: 差分体積は有限かつ非負である必要がある: {volume!r}")
            if volume < -NEGATIVE_FRAGMENT_VOLUME_TOLERANCE_MM3:
                raise ValueError(
                    f"{candidate_label}: negative fragment exceeds signed tolerance: "
                    f"{volume!r}")
            detail = _native_negative_fragment_is_valid(
                candidate, candidate_label, volume)
            # Keep the signed evidence available to the caller without
            # changing the established four-value return contract.
            if isinstance(getattr(candidate, "metadata", None), dict):
                candidate.metadata.setdefault("signed_negative_validation", []).append(detail)
            return volume
        if volume <= 0:
            raise ValueError(
                f"{candidate_label}: 非空差分の体積は正である必要がある: {volume!r}")
        if not candidate.is_volume:
            raise ValueError(f"{candidate_label}: 正体積差分がis_volumeではない")
        return volume

    mesh_volume = check_one(mesh, label)
    if mesh_volume is None:
        # Keep the public four-value contract identical to the non-empty path.
        return mesh_volume, [], [], 0.0

    checked = []
    for index, piece in enumerate(mesh.split(only_watertight=False)):
        piece_label = f"{label} component {index}"
        piece_volume = check_one(piece, piece_label, allow_tiny_negative=True)
        if piece_volume is None:
            continue
        checked.append((piece, piece_volume))
    significant = [piece for piece, volume in checked
                   if volume > fragment_threshold_mm3]
    # Numerical fragments are restricted to already-validated tiny positive
    # volumes.  Negative slivers remain signed in ``checked`` and in the row
    # audit; no signed value is normalized or accumulated with abs().
    numerical_fragments = sum(
        volume for _, volume in checked
        if 0 < volume <= fragment_threshold_mm3
    )
    return mesh_volume, checked, significant, float(numerical_fragments)


def minimum(vertices, normals):
    return (vertices @ normals.T).min(axis=0)


def _manifold_is_empty(result):
    value = getattr(result, "is_empty", False)
    return bool(value() if callable(value) else value)


def _checked_manifold_boolean_volume(result, label, *, audit=None):
    """Manifold Booleanの状態・トポロジー・符号を一箇所で検査する。"""
    status_method = getattr(result, "status", None)
    if not callable(status_method):
        raise ValueError(f"{label}: Boolean result has no native status")
    status = status_method()
    if getattr(status, "name", None) != "NoError":
        raise ValueError(f"{label}: Boolean status is not NoError: {status}")
    if _manifold_is_empty(result):
        return 0.0
    try:
        native_mesh = result.to_mesh()
        vertices = np.asarray(native_mesh.vert_properties, dtype=float)
        faces = np.asarray(native_mesh.tri_verts)
    except Exception as exc:
        raise ValueError(f"{label}: Boolean mesh extraction failed") from exc
    if (vertices.ndim != 2 or vertices.shape[1] < 3 or len(vertices) < 4 or
            faces.ndim != 2 or faces.shape[1] != 3 or len(faces) < 4 or
            not np.isfinite(vertices[:, :3]).all() or
            not np.isfinite(faces.astype(float)).all() or
            faces.min(initial=0) < 0 or faces.max(initial=0) >= len(vertices)):
        raise ValueError(f"{label}: Boolean output topology is invalid")
    volume = float(result.volume())
    if not math.isfinite(volume):
        raise ValueError(f"{label}: Boolean volume is non-finite: {volume!r}")
    if volume < -BOOLEAN_NEGATIVE_TOLERANCE_MM3:
        raise ValueError(f"{label}: Boolean volume is negative beyond tolerance: {volume!r}")
    if volume < 0:
        if audit is not None:
            audit.append({"label": label, "signed_volume_mm3": volume,
                          "native_status": getattr(status, "name", str(status)),
                          "tolerance_mm3": BOOLEAN_NEGATIVE_TOLERANCE_MM3})
        # This is an explicit signed tolerance, never abs()/max() repair.
        return 0.0
    return volume


def moving_intersection(mesh, fixed, direction, *, audit=None, label="moving intersection"):
    """純平行移動の連続包絡。零厚箱を避けるため0.0001mmだけ保守的に膨張。"""
    d = np.asarray(direction, float)
    sweep = F.as_manifold(mesh).minkowski_sum(box(*(abs(d) + .0001)).translate(d / 2))
    return _checked_manifold_boolean_volume(
        sweep ^ F.as_manifold(fixed), label, audit=audit)


def _finite_float(value):
    """JSON内の数値を、NaN/文字列を除いて有限値へ変換する。"""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _plane_basis(normal):
    n = np.asarray(normal, dtype=float)
    n /= np.linalg.norm(n)
    ref = np.array([0., 0., 1.]) if abs(n[2]) < .9 else np.array([1., 0., 0.])
    u = np.cross(ref, n)
    u /= np.linalg.norm(u)
    v = np.cross(n, u)
    return n, u, v


def _polygon_centroid(points):
    """2D凸包点列の面積重心。退化時は点の平均を返す。"""
    points = np.asarray(points, dtype=float)
    if len(points) < 3:
        return points.mean(axis=0), 0., list(range(len(points)))
    from scipy.spatial import ConvexHull, QhullError
    try:
        hull = ConvexHull(points)
    except QhullError:
        return points.mean(axis=0), 0., list(range(len(points)))
    poly = points[hull.vertices]
    nxt = np.roll(poly, -1, axis=0)
    cross = poly[:, 0] * nxt[:, 1] - nxt[:, 0] * poly[:, 1]
    twice_area = float(cross.sum())
    if abs(twice_area) < 1e-12:
        return points.mean(axis=0), 0., hull.vertices.tolist()
    centroid = ((poly + nxt) * cross[:, None]).sum(axis=0) / (3. * twice_area)
    return centroid, abs(twice_area) / 2., hull.vertices.tolist()


def _support_envelope(mesh, normal, tolerance_mm=.2):
    """実STLの面を平面近傍で切り、傾斜時の最低支持点を抽出する。

    凸包を入力形状の代用にはせず、元メッシュの頂点と三角形辺の交点だけを
    使う。支持面の圧力分布は測っていないため、重心は「最低層の凸包の面積
    重心」、端への移動量はその重心と最外点の両方を記録する。
    """
    if not (math.isfinite(tolerance_mm) and tolerance_mm > 0):
        raise ValueError("支持点の許容厚みは有限の正数")
    n, u, v = _plane_basis(normal)
    vertices = np.asarray(mesh.vertices, dtype=float)
    signed = vertices @ n
    minimum_signed = float(signed.min())
    cut = minimum_signed + tolerance_mm
    points = []
    for face in np.asarray(mesh.faces, dtype=np.int64):
        fv = vertices[face]
        fs = signed[face]
        if float(fs.min()) > cut:
            continue
        points.extend(fv[fs <= cut])
        if float(fs.max()) <= cut:
            points.extend(fv)
        for i, j in ((0, 1), (1, 2), (2, 0)):
            si, sj = float(fs[i]), float(fs[j])
            if (si - cut) * (sj - cut) < 0:
                t = (cut - si) / (sj - si)
                points.append(fv[i] + t * (fv[j] - fv[i]))
    if not points:
        points = [vertices[int(np.argmin(signed))]]
    points = np.asarray(points, dtype=float)
    # 同一頂点・交点をまとめ、三角形の構造は入力STLに残したままにする。
    points = np.unique(np.round(points, decimals=7), axis=0)
    projected = points - np.outer(points @ n - minimum_signed, n)
    plane_origin = n * minimum_signed
    points_2d = np.column_stack((projected @ u, projected @ v))
    center_2d, hull_area, hull_indices = _polygon_centroid(points_2d)
    center = plane_origin + center_2d[0] * u + center_2d[1] * v
    radial = np.linalg.norm(projected[:, :2], axis=1)
    center_radial = float(np.linalg.norm(center[:2]))
    edge_index = int(np.argmax(radial))
    return dict(
        normal=n.tolist(),
        minimum_signed_mm=minimum_signed,
        support_cut_mm=cut,
        support_tolerance_mm=float(tolerance_mm),
        contact_sample_count=int(len(projected)),
        convex_hull_area_mm2=float(hull_area),
        support_centroid_xyz_mm=center.tolist(),
        support_centroid_xy_mm=center[:2].tolist(),
        support_centroid_eccentricity_mm=center_radial,
        edge_support_xyz_mm=projected[edge_index].tolist(),
        edge_support_eccentricity_mm=float(radial[edge_index]),
        support_z_min_mm=float(projected[:, 2].min()),
        support_z_max_mm=float(projected[:, 2].max()),
        support_centroid_z_mm=float(center[2]),
        source="actual shoe STL vertices and triangle-edge intersections",
        hull_vertex_count=int(len(hull_indices)),
    )


def _ring_integrals(coords):
    """閉じた2D輪郭の面積一次/二次モーメント (符号付き)。"""
    points = np.asarray(coords, dtype=float)
    if len(points) < 4:
        return (0.,) * 6
    if not np.allclose(points[0], points[-1]):
        points = np.vstack((points, points[0]))
    x0, y0 = points[:-1].T
    x1, y1 = points[1:].T
    cross = x0 * y1 - x1 * y0
    area = float(cross.sum() / 2.)
    first_x = float(((x0 + x1) * cross).sum() / 6.)
    first_y = float(((y0 + y1) * cross).sum() / 6.)
    i_xx = float(((y0**2 + y0*y1 + y1**2) * cross).sum() / 12.)
    i_yy = float(((x0**2 + x0*x1 + x1**2) * cross).sum() / 12.)
    i_xy = float(((2*x0*y0 + x0*y1 + x1*y0 + 2*x1*y1) * cross).sum() / 24.)
    return area, first_x, first_y, i_xx, i_yy, i_xy


def _root_section_properties(foot, section_z_mm=0.):
    """φ10プラグ根元の実足STL断面を測る。印刷積層/充填率は含めない。"""
    section = foot.section(plane_origin=[0., 0., section_z_mm],
                           plane_normal=[0., 0., 1.])
    if section is None:
        return dict(status="UNVERIFIED_NO_SECTION", section_z_mm=float(section_z_mm),
                    source="leg_foot_bored.stl", limitation="断面を抽出できない")
    planar = section.to_planar()[0]
    polygons = list(planar.polygons_full)
    if not polygons:
        return dict(status="UNVERIFIED_NO_SECTION", section_z_mm=float(section_z_mm),
                    source="leg_foot_bored.stl", limitation="断面ポリゴンが空")
    totals = np.zeros(6, dtype=float)
    boundary_points = []
    for polygon in polygons:
        rings = [polygon.exterior, *polygon.interiors]
        for ring in rings:
            vals = np.asarray(ring.coords, dtype=float)
            totals += np.asarray(_ring_integrals(vals))
            boundary_points.append(vals)
    area, first_x, first_y, i_xx, i_yy, i_xy = totals.tolist()
    if area < 0:
        area, first_x, first_y, i_xx, i_yy, i_xy = (-totals).tolist()
    if area <= 0 or not math.isfinite(area):
        return dict(status="UNVERIFIED_INVALID_SECTION", section_z_mm=float(section_z_mm),
                    source="leg_foot_bored.stl", area_mm2=float(area))
    cx, cy = first_x / area, first_y / area
    i_xx_c = i_xx - area * cy**2
    i_yy_c = i_yy - area * cx**2
    i_xy_c = i_xy - area * cx * cy
    inertia = np.array([[i_xx_c, -i_xy_c], [-i_xy_c, i_yy_c]], dtype=float)
    principal = np.linalg.eigvalsh(inertia)
    points = np.vstack(boundary_points)
    radius = float(np.linalg.norm(points - np.array([cx, cy]), axis=1).max())
    z_min = float(principal.min() / radius) if radius > 0 else None
    return dict(
        status="CAD_SECTION_ONLY",
        section_z_mm=float(section_z_mm),
        source="actual leg_foot_bored.stl horizontal section at plug root",
        area_mm2=float(area),
        centroid_xy_mm=[float(cx), float(cy)],
        inertia_xx_mm4=float(i_xx_c),
        inertia_yy_mm4=float(i_yy_c),
        inertia_xy_mm4=float(i_xy_c),
        principal_inertia_min_mm4=float(principal.min()),
        principal_inertia_max_mm4=float(principal.max()),
        outer_radius_mm=radius,
        weakest_section_modulus_mm3=z_min,
        limitation=("STLの中実断面を計算しただけで、薄肉印刷の周壁/充填、"
                    "積層異方性、座面の局所応力、疲労/クリープは未確認"),
    )


def _iter_dicts(value, path="root"):
    yield path, value
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _iter_dicts(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _iter_dicts(child, f"{path}[{index}]")


def _simulation_model_identity(data):
    """最終モデルの識別情報を検査し、旧形状の荷重を拒否する。

    `selected_derate50.json` などの旧感度結果には model_kind とモデル台帳が
    無いため、接触力の数値だけを取り出して新足の根元要求へ流用しない。最終
    統合結果はモデル種別、モデル/元URDFの完全hash、凍結hash、body/feet/legs
    台帳、4脚の根元座標を同じ結果へ保存している必要がある。
    """
    case = data.get("case") if isinstance(data, dict) else None
    case = case if isinstance(case, dict) else {}
    options = case.get("model") if isinstance(case.get("model"), dict) else {}
    kind = options.get("model_kind") or data.get("model_kind")
    identity = {
        "status": "UNVERIFIED_FINAL_MODEL_IDENTITY",
        "model_kind": kind,
        "model_sha256": None,
        "source_urdf_sha256": None,
        "geometry_freeze_hash": None,
        "geometry_freeze_time": None,
        "assembly_input_roles": [],
        "root_legs": [],
        "mismatches": [],
    }
    if kind not in FINAL_SIM_MODEL_KINDS:
        identity["status"] = "UNVERIFIED_LEGACY_SIMULATION_MODEL"
        identity["mismatches"].append(
            "simulation.case.model.model_kind must identify a final integrated model")
        return identity

    manifest = data.get("model_manifest")
    if not isinstance(manifest, dict):
        identity["mismatches"].append("simulation.model_manifest is missing")
        return identity
    manifest_kind = manifest.get("model_kind")
    if manifest_kind != kind:
        identity["mismatches"].append(
            f"model_kind mismatch: case={kind!r}, model_manifest={manifest_kind!r}")
    for key in ("model_sha256", "source_urdf_sha256"):
        value = manifest.get(key)
        identity[key] = value
        if not _is_sha256(value):
            identity["mismatches"].append(f"model_manifest.{key} must be a full SHA-256")

    freeze = manifest.get("geometry_freeze")
    if not isinstance(freeze, dict):
        freeze = {}
        identity["mismatches"].append("model_manifest.geometry_freeze is missing")
    identity["geometry_freeze_hash"] = freeze.get("geometry_freeze_hash") or freeze.get("freeze_hash")
    identity["geometry_freeze_time"] = freeze.get("geometry_freeze_time") or freeze.get("freeze_time")
    if not _is_sha256(identity["geometry_freeze_hash"]):
        identity["mismatches"].append("model_manifest.geometry_freeze hash must be a full SHA-256")
    if not identity["geometry_freeze_time"]:
        identity["mismatches"].append("model_manifest.geometry_freeze time is missing")
    case_freeze_hash = options.get("geometry_freeze_hash") or options.get("freeze_hash")
    if case_freeze_hash and case_freeze_hash != identity["geometry_freeze_hash"]:
        identity["mismatches"].append("case/model_manifest geometry freeze hash mismatch")

    case_manifest_path = case.get("model_manifest") or options.get("model_manifest")
    manifest_path = manifest.get("manifest_path")
    if case_manifest_path and manifest_path and case_manifest_path != manifest_path:
        identity["mismatches"].append("case/model_manifest manifest path mismatch")
    input_hashes = data.get("input_sha256")
    if not isinstance(input_hashes, dict) or not manifest_path or manifest_path not in input_hashes:
        identity["mismatches"].append("input_sha256 does not cover model_manifest")
    elif not _is_sha256(input_hashes.get(manifest_path)):
        identity["mismatches"].append("input_sha256 model_manifest entry is not a full SHA-256")

    assembly_inputs = manifest.get("assembly_inputs")
    if not isinstance(assembly_inputs, list):
        assembly_inputs = []
        identity["mismatches"].append("model_manifest.assembly_inputs is missing")
    roles = sorted({row.get("role") for row in assembly_inputs
                    if isinstance(row, dict) and row.get("role")})
    identity["assembly_input_roles"] = roles
    for required_role in ("assembly_manifest", "feet_manifest", "leg_manifest"):
        if required_role not in roles:
            identity["mismatches"].append(f"model_manifest.assembly_inputs lacks {required_role}")

    roots = manifest.get("pla_phi10_plug_roots")
    legs = sorted(roots) if isinstance(roots, dict) else []
    identity["root_legs"] = legs
    for leg in ("FR", "FL", "RL", "RR"):
        spec = roots.get(leg) if isinstance(roots, dict) else None
        if not isinstance(spec, dict) or spec.get("frame") != "tibia":
            identity["mismatches"].append(
                f"model_manifest.pla_phi10_plug_roots.{leg} must be a tibia-frame record")
        elif not isinstance(spec.get("xyz_mm"), list) or len(spec["xyz_mm"]) != 3:
            identity["mismatches"].append(
                f"model_manifest.pla_phi10_plug_roots.{leg}.xyz_mm[3] is missing")
    if "foot_candidate_dir" in options:
        identity["mismatches"].append("final simulation still declares legacy foot_candidate_dir")
    if not identity["mismatches"]:
        identity["status"] = "PASS_METADATA_ONLY"
    return identity


def _simulation_result_quality(data):
    """接触力を要求値へ使える計算結果か確認する。

    モデルのhashが最終形状と一致していても、初期自己干渉や数値不安定、
    転倒後の荷重は足の要求値にならない。通常のトルク飽和による物理FAILは
    この入口では一律に捨てず、上記の無効条件と区別して返す。
    """
    checks = data.get("checks") if isinstance(data, dict) else None
    checks = checks if isinstance(checks, dict) else {}
    required = (
        "completed",
        "numeric_stability",
        "inputs_unchanged",
        "initial_self_penetration_le_0p1mm",
        "no_fall",
    )
    mismatches = []
    status = data.get("status") if isinstance(data, dict) else None
    if status in INVALID_SIMULATION_STATUSES:
        mismatches.append(f"simulation.status={status!r} is not a valid load source")
    for key in required:
        if checks.get(key) is not True:
            mismatches.append(f"simulation.checks.{key} must be true")
    if checks.get("initial_self_penetration_le_0p1mm") is not True:
        result_status = "UNVERIFIED_INVALID_INITIAL_CONTACT_MODEL"
    elif checks.get("numeric_stability") is not True or status in {
        "NUMERIC_FAILURE", "NUMERIC_INSTABILITY", "NUMERICALLY_UNSTABLE"
    }:
        result_status = "UNVERIFIED_NUMERICALLY_UNSTABLE_SIMULATION"
    elif checks.get("no_fall") is not True or status in {"FELL", "FALL"}:
        result_status = "UNVERIFIED_FALLEN_SIMULATION"
    elif not mismatches:
        result_status = "PASS_RESULT_QUALITY"
    else:
        result_status = "UNVERIFIED_INVALID_SIMULATION_RESULT"
    return {
        "status": result_status,
        "simulation_status": status,
        "checks": {key: checks.get(key) for key in required},
        "mismatches": mismatches,
        "load_is_eligible": not mismatches,
        "interpretation": (
            "初期干渉・数値不安定・転倒後の荷重を足の最終要求値へ使わない。"
            "status=FAILでもこの品質条件を満たす場合は、材料合格ではない参考比較値として扱う。"
        ),
    }


def _simulation_force_summary(path):
    """最終シムの接触力表記を読み、無い成分は推測せずUNVERIFIEDにする。"""
    if path is None:
        return dict(status="UNVERIFIED_FINAL_SIM_PENDING", source=None,
                    limitation="最終形状のシムJSONを--simulationで指定していない")
    path = Path(path)
    if not path.exists():
        return dict(status="UNVERIFIED_SIMULATION_MISSING", source=str(path),
                    limitation="指定されたシムJSONが存在しない")
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return dict(status="UNVERIFIED_SIMULATION_UNREADABLE", source=str(path),
                    limitation=str(exc))
    model_identity = _simulation_model_identity(data)
    if model_identity["status"] != "PASS_METADATA_ONLY":
        return dict(status=model_identity["status"], source=str(path),
                    model_identity=model_identity, max_normal_contact_force_N=None,
                    max_horizontal_contact_force_N=None, max_abs_roll_deg=None,
                    max_abs_pitch_deg=None, candidate_geometry=data.get("candidate_geometry"),
                    physical_readiness=data.get("physical_readiness"),
                    limitation=("旧形状または識別hash不足のシム結果を、最終足の接触荷重へ"
                                "流用しない。final model_kind・モデル台帳・凍結hash・"
                                "body/feet/legs台帳・4脚根元座標をそろえること。"))
    result_quality = _simulation_result_quality(data)
    if not result_quality["load_is_eligible"]:
        return dict(status=result_quality["status"], source=str(path),
                    model_identity=model_identity,
                    simulation_quality=result_quality,
                    max_normal_contact_force_N=None,
                    max_horizontal_contact_force_N=None,
                    max_abs_roll_deg=None, max_abs_pitch_deg=None,
                    candidate_geometry=data.get("candidate_geometry") if isinstance(data, dict) else None,
                    physical_readiness=data.get("physical_readiness") if isinstance(data, dict) else None,
                    limitation=("最終形状hashが一致していても、初期自己干渉・数値不安定・"
                                "転倒後の荷重を正式な足の要求値へ流用しない。"))
    normal = []
    horizontal = []
    roll = []
    pitch = []
    evidence = []
    normal_keys = ("max_normal_contact_force_N", "max_vertical_contact_force_N",
                   "max_normal_force_N", "max_normal_N")
    horizontal_keys = ("max_horizontal_contact_force_N", "max_tangential_contact_force_N",
                       "max_horizontal_force_N", "max_tangential_force_N",
                       "max_tangent_force_N")
    normal_row_keys = ("normal_force_N", "normal_N", "normal_contact_force_N")
    horizontal_row_keys = ("horizontal_force_N", "horizontal_N", "tangential_force_N",
                           "tangential_N", "tangent_force_N", "tangent_N")
    for location, node in _iter_dicts(data):
        if not isinstance(node, dict):
            continue
        for key in normal_keys:
            value = _finite_float(node.get(key))
            if value is not None:
                normal.append((max(value, 0.), f"{location}.{key}"))
        for key in horizontal_keys:
            value = _finite_float(node.get(key))
            if value is not None:
                horizontal.append((abs(value), f"{location}.{key}"))
        for key in normal_row_keys:
            value = _finite_float(node.get(key))
            if value is not None:
                normal.append((max(value, 0.), f"{location}.{key}"))
        for key in horizontal_row_keys:
            value = _finite_float(node.get(key))
            if value is not None:
                horizontal.append((abs(value), f"{location}.{key}"))
        for key in ("normal_force_by_material_N", "normal_force_by_part"):
            values = node.get(key)
            if isinstance(values, dict):
                total = sum(max(v, 0.) for v in (_finite_float(x) for x in values.values())
                            if v is not None)
                if total:
                    normal.append((total, f"{location}.{key} (合計)"))
        for key in ("horizontal_force_by_material_N", "horizontal_force_by_part",
                    "tangential_force_by_material_N", "tangential_force_by_part"):
            values = node.get(key)
            if isinstance(values, dict):
                total = sum(abs(v) for v in (_finite_float(x) for x in values.values())
                            if v is not None)
                if total:
                    horizontal.append((total, f"{location}.{key} (合計)"))
        if "max_abs_roll_deg" in node:
            value = _finite_float(node.get("max_abs_roll_deg"))
            if value is not None: roll.append(abs(value))
        if "max_abs_pitch_deg" in node:
            value = _finite_float(node.get("max_abs_pitch_deg"))
            if value is not None: pitch.append(abs(value))
        # 既に法線/接線へ分解された接触レコードだけを採用する。
        for key in ("contact_force", "contact_force_N", "force"):
            force = node.get(key)
            if isinstance(force, dict):
                nv = next((_finite_float(force.get(k)) for k in normal_row_keys
                           if _finite_float(force.get(k)) is not None), None)
                hv = next((_finite_float(force.get(k)) for k in horizontal_row_keys
                           if _finite_float(force.get(k)) is not None), None)
                if nv is not None: normal.append((max(nv, 0.), f"{location}.{key}"))
                if hv is not None: horizontal.append((abs(hv), f"{location}.{key}"))
    if normal:
        normal_max, normal_source = max(normal)
    else:
        normal_max = normal_source = None
    if horizontal:
        horizontal_max, horizontal_source = max(horizontal)
    else:
        horizontal_max = horizontal_source = None
    status = "AVAILABLE_BUT_NOT_MATERIAL_PASS" if normal_max is not None and horizontal_max is not None else (
        "PARTIAL_NORMAL_ONLY" if normal_max is not None else "UNVERIFIED_CONTACT_FORCE_FIELDS")
    geometry = data.get("candidate_geometry") if isinstance(data, dict) else None
    return dict(
        status=status,
        source=str(path),
        model_identity=model_identity,
        simulation_quality=result_quality,
        max_normal_contact_force_N=normal_max,
        max_normal_source=normal_source,
        max_horizontal_contact_force_N=horizontal_max,
        max_horizontal_source=horizontal_source,
        max_abs_roll_deg=max(roll) if roll else None,
        max_abs_pitch_deg=max(pitch) if pitch else None,
        candidate_geometry=geometry,
        physical_readiness=data.get("physical_readiness") if isinstance(data, dict) else None,
        limitation=("接触力の比較値であり、材料の許容応力・積層方向・実機の"
                    "足裏接触を合格にしない。水平力の記録が無い場合は推定しない。"),
    )


def _contact_load_demand(force_summary, support_cases, section, *, reference_load_n=22.4):
    """支持点偏心と接触力から、断面平均/名目曲げ要求を計算する。"""
    if support_cases:
        critical = max(support_cases, key=lambda row: row["edge_support_eccentricity_mm"])
        edge_e = max(float(s["edge_support_eccentricity_mm"]) for s in support_cases)
        centroid_e = max(float(s["support_centroid_eccentricity_mm"]) for s in support_cases)
        lever_h = max(abs(float(s["support_z_min_mm"])) for s in support_cases)
    else:
        critical = None; edge_e = centroid_e = lever_h = None
    area = _finite_float(section.get("area_mm2")) if section else None
    modulus = (_finite_float(section.get("weakest_section_modulus_mm3"))
               if section else None)
    fn = _finite_float(force_summary.get("max_normal_contact_force_N"))
    fh = _finite_float(force_summary.get("max_horizontal_contact_force_N"))
    row = dict(
        status="UNVERIFIED_FORCE_COMPARISON" if fn is None or fh is None else "CAD_DEMAND_ONLY",
        simulation=force_summary,
        critical_support_tilt=critical,
        support_centroid_eccentricity_max_mm=centroid_e,
        support_edge_eccentricity_max_mm=edge_e,
        horizontal_force_lever_arm_max_mm=lever_h,
        section=section,
        reference_22p4_N=float(reference_load_n),
        reference_22p4_is_not_a_pass=True,
        reference_limitation=("22.4 Nは従来の固定参照値。最終シムの接触力の代用にせず、"
                              "機械合格判定にも使わない。"),
    )
    if fn is None and fh is None:
        row["limitation"] = "最終シムの鉛直/水平接触力が未入力のため要求値を計算できない"
        return row
    # 最終シムの力が片方しかない場合も、ある成分だけを勝手に補完しない。
    row.update(
        max_normal_contact_force_N=fn,
        max_horizontal_contact_force_N=fh,
        normal_compression_demand_mpa=(fn / area if fn is not None and area else None),
        horizontal_shear_average_demand_mpa=(fh / area if fh is not None and area else None),
        vertical_eccentric_bending_moment_Nmm=(fn * edge_e if fn is not None and edge_e is not None else None),
        horizontal_root_bending_moment_Nmm=(fh * lever_h if fh is not None and lever_h is not None else None),
    )
    mv = row["vertical_eccentric_bending_moment_Nmm"]
    mh = row["horizontal_root_bending_moment_Nmm"]
    row["combined_bending_moment_Nmm"] = (math.hypot(mv, mh)
                                           if mv is not None and mh is not None else None)
    row["weakest_section_bending_demand_mpa"] = (
        row["combined_bending_moment_Nmm"] / modulus
        if row["combined_bending_moment_Nmm"] is not None and modulus else None)
    row["stress_interpretation"] = (
        "CAD中実断面の平均/名目要求値。許容応力、積層周壁、充填、根元の局所"
        "曲げ/せん断、座屈、接着/クリープの合否は未確認。"
    )
    return row


def run(directory, *, step_deg=.1, roll_deg=15., load_n=22.4, simulation=None,
        support_tolerance_mm=.2):
    if not (math.isfinite(step_deg) and .01 <= step_deg <= .5):
        raise ValueError("角度間隔は0.01〜0.5度")
    if not (math.isfinite(roll_deg) and 0 <= roll_deg <= 20 and math.isfinite(load_n) and load_n > 0):
        raise ValueError("横傾斜0〜20度、荷重は有限な正値")
    directory = Path(directory)
    data = json.loads((directory / "assembly.json").read_text())
    d = data["dimensions"]
    checks = []
    boolean_audit = []
    def check(name, ok, **evidence):
        checks.append(dict(name=name, pass_geometry=bool(ok), **evidence))
        print(("OK " if ok else "FAIL ")+name,flush=True)
    for src in data["sources"]:
        p = ROOT / src["path"]
        check("入力の鮮度:" + src["path"], hashlib.sha256(p.read_bytes()).hexdigest() == src["sha256"])
    meshes = {}
    for name, rec in data["outputs"].items():
        p = ROOT / rec["path"]
        mesh = load(p); meshes[name] = mesh
        check("STL:" + name, hashlib.sha256(p.read_bytes()).hexdigest() == rec["sha256"],
              volume_mm3=float(mesh.volume), components=rec["solid_components"], role=rec["role"])
    foot, tibia = F.source_parts()
    shoe = meshes["tpu_shoe"]
    sp = meshes["pla_spacer_positive_y"]
    sn = meshes["pla_spacer_negative_y"]
    assembled = {"foot": foot, "tibia": tibia, "shoe": shoe, "spacer_positive_y": sp, "spacer_negative_y": sn}
    for name in ("pla_spacer_upper_positive_y","pla_spacer_upper_negative_y"):
        assembled[name]=meshes[name]
    pairs = []
    for i, (an, a) in enumerate(assembled.items()):
        for bn, b in list(assembled.items())[i+1:]:
            v = intersection(a,b); pairs.append(dict(a=an,b=bn,volume_mm3=v))
            check("実体交差:" + an + "/" + bn, v < .01, intersection_mm3=v)
    # 工具を通す以前は3パーツとも保持耳の間へ上から入る。
    v = moving_intersection(foot, shoe, [0, 0, 60], audit=boolean_audit,
                            label="甲の上からの連続挿入")
    check("甲の上からの連続挿入", v < .02, swept_intersection_mm3=v, travel_mm=60)
    v = moving_intersection(tibia, shoe, [0, 0, 65], audit=boolean_audit,
                            label="脛の上からの連続挿入")
    check("脛の上からの連続挿入", v < .02, swept_intersection_mm3=v, travel_mm=65)
    v = moving_intersection(tibia, foot, [0, 0, 65], audit=boolean_audit,
                            label="φ10プラグの脛挿入")
    check("φ10プラグの脛挿入", v < .02, swept_intersection_mm3=v,
          numeric_dilation_mm=.0001, volume_tolerance_mm3=.02)
    # 実の下面接触があるためゼロ端面を微小膨張する検査は別に評価。
    for name, spacer, sign in (("positive",sp,1),("negative",sn,-1)):
        v = moving_intersection(spacer, shoe, [0,sign*25,0], audit=boolean_audit,
                                label="スペーサー外側から挿入:"+name)
        check("スペーサー外側から挿入:"+name, v < .02, swept_intersection_mm3=v)
    # M3×40軸、頭とナットの外接包絡。寸法は実物照合前の許容上限。
    grip = d["screw_grip_mm"]
    end = grip/2
    screw = to_trimesh(cyl_y(40,3).translate([0,end-20,d["bolt_z"]]))
    fasteners = {"shank": screw,
                 "head": to_trimesh(cyl_y(2.5,6).translate([0,end+1.25,d["bolt_z"]])),
                 "nut": to_trimesh(cyl_y(4,6.6).translate([0,-end-2,d["bolt_z"]]))}
    for name,m in list(fasteners.items()):
        m=m.copy();m.apply_translation([0,0,d["upper_bolt_z"]-d["bolt_z"]])
        fasteners[name+"_upper"]=m
    for fn,fm in fasteners.items():
        for an,am in assembled.items():
            v=intersection(fm,am)
            check("ねじ包絡:"+fn+"/"+an, v < .01, intersection_mm3=v)
    protrusion = 40-grip-4
    check("M3×40の突出2山以上",protrusion>=1.,grip_mm=grip,
          assumed_nut_height_mm=4, protrusion_mm=protrusion,
          maximum_nut_height_for_two_threads_mm=40-grip-1,
          limitation="実ねじ全ねじ長/頭形/ナイロン部高さ/在庫は未測定")
    shell_rows=[]
    for suffix in ("","_m"):
        sh = load(ROOT/f"hardware/stl/shin_shell{suffix}.stl")
        sh.apply_transform(trimesh.transformations.rotation_matrix(np.pi,[1,0,0]))
        sh.apply_translation([0,0,C.TIBIA_LEN-16])
        fitted=meshes[f"shin_shell_retained{suffix}"]
        rm=trimesh.boolean.difference([sh,fitted],engine="manifold")
        rm_volume, pieces, significant, numerical_fragments = _validated_difference_parts(
            rm, label=f"殻の差分:{suffix or 'standard'}")
        actual_bounds=trimesh.util.concatenate(significant).bounds if significant else np.zeros((2,3))
        row=dict(variant=suffix or "standard",original_volume_mm3=float(sh.volume),
                 removed_volume_mm3=rm_volume,retained_volume_fraction=float(fitted.volume/sh.volume),
                 removed_bounds_mm=actual_bounds.tolist(),
                 numerical_difference_fragment_volume_mm3=numerical_fragments,
                 negative_numerical_difference_fragment_volume_mm3=-sum(
                     volume for _, volume in pieces if volume < 0),
                 negative_numerical_difference_fragment_count=sum(
                     volume < 0 for _, volume in pieces),
                 negative_fragment_signed_tolerance_mm3=NEGATIVE_FRAGMENT_VOLUME_TOLERANCE_MM3,
                 negative_fragment_audit=[detail
                     for piece, _ in pieces
                     for detail in piece.metadata.get("signed_negative_validation", [])])
        for an,am in assembled.items():
            v=intersection(fitted,am);row["intersection_"+an+"_mm3"]=v
            check("保持した脛殻:"+(suffix or "standard")+"/"+an,v<.01,intersection_mm3=v)
        for fn,fm in fasteners.items():
            v=intersection(fitted,fm);check("脛殻とねじ:"+suffix+"/"+fn,v<.01,intersection_mm3=v)
        # 下側の耳逃げと上下ボルト座だけ。膝側25mm以上は変更しない。
        check("殻差分の数値残片:"+suffix,numerical_fragments<.02,
              absolute_fragment_volume_mm3=numerical_fragments,per_fragment_threshold_mm3=.001)
        significant_mesh = trimesh.util.concatenate(significant) if significant else None
        high_cut=(0. if significant_mesh is None or actual_bounds[1,2] <=85 else
                  intersection(significant_mesh,to_trimesh(box(200,200,200).translate([0,0,185]))))
        check("脛殻の膝側を保存:"+suffix,high_cut<.02,
              highest_cut_z_mm=float(actual_bounds[1,2]),cut_above_85mm_volume_mm3=high_cut)
        for name in ("pla_spacer_upper_positive_y","pla_spacer_upper_negative_y"):
            spacer=meshes[name]
            _,dist,_=trimesh.proximity.closest_point(fitted,spacer.triangles_center)
            area=float(spacer.area_faces[dist<.005].sum())
            row[name+"_case_contact_area_mm2"]=area
            check("上側スペーサーの殻支持:"+suffix+"/"+name,area>40,contact_area_mm2=area)
        # 柱を外へ曲げて、先に組んだ脛+殻を上から下ろす。一定曲率の仮想経路。
        sole=F.as_manifold(shoe)^box(200,200,100).translate([0,0,-50])
        open_webs=F.flex_webs(F.as_manifold(shoe),30)
        for wi,web in enumerate(open_webs):
            v=moving_intersection(fitted,to_trimesh(web),[0,0,65],audit=boolean_audit,
                                  label="殻を下ろす経路:"+suffix+f"/ear{wi}")
            check("殻を下ろす経路:"+suffix+f"/ear{wi}",v<.02,swept_intersection_mm3=v)
        max_closure=0.
        for angle in np.arange(0,30.001,.5):
            for web in F.flex_webs(F.as_manifold(shoe),float(angle)):
                closure_volume = _checked_manifold_boolean_volume(
                    web ^ F.as_manifold(fitted),
                    "耳を閉じる経路:"+suffix+f"/angle={angle:g}",
                    audit=boolean_audit)
                max_closure=max(max_closure,closure_volume)
        check("耳を閉じる経路:"+suffix,max_closure<.01,max_intersection_mm3=max_closure,
              angular_step_deg=.5,between_sample_displacement_bound_mm=50*np.radians(.25),
              relief_clearance_mm=.3,elastic_fibre_strain_demand=data["insertion_flex"]["maximum_nominal_fibre_strain"])
        shell_rows.append(row)
    # joint rectangle: p[-45,55], k[-44,44]。rr=C+Fcos(p)-Tsin(p+k)>=0。
    # T>=物理脛長なら正側p+kはarcsin((C+F)/Tmin)以下。もう一方のsin解は99°より大きい。
    # ワークスペース射影よりも広い必要条件を使い、離散ゲイトだけの判定を避ける。
    low=-89.; high=float(np.degrees(np.arcsin((C.COXA_LEN+C.FEMUR_LEN)/C.TIBIA_LEN)))
    theta=np.linspace(low,high,math.ceil((high-low)/step_deg)+1)
    rolls=np.linspace(-roll_deg,roll_deg,math.ceil(2*roll_deg/step_deg)+1) if roll_deg else np.array([0.])
    rigid_parts=[am for an,am in assembled.items() if an!="shoe"]+[meshes["shin_shell_retained"],meshes["shin_shell_retained_m"]]
    hv=trimesh.util.concatenate(rigid_parts).convex_hull.vertices
    sv=shoe.convex_hull.vertices
    best=dict(clearance_mm=float("inf"))
    th=np.radians(theta)
    for roll in rolls:
        r=np.radians(roll)
        normals=np.column_stack([-np.sin(th)*np.cos(r),np.full(len(th),np.sin(r)),np.cos(th)*np.cos(r)])
        gaps=minimum(hv,normals)-minimum(sv,normals)
        idx=int(gaps.argmin())
        if gaps[idx]<best["clearance_mm"]:best=dict(clearance_mm=float(gaps[idx]),tilt_deg=float(theta[idx]),roll_deg=float(roll))
    # 支持関数のLipschitz上界。全頂点の原点距離を用い、角度格子間も保守的に差引く。
    radius_bound=float(np.linalg.norm(hv,axis=1).max()+np.linalg.norm(sv,axis=1).max())
    interpolation_bound=radius_bound*np.radians(step_deg) # 2軸の各半ステップの和
    support=dict(tilt_range_deg=[low,high],roll_range_deg=[-roll_deg,roll_deg],
                 evaluated_normals=len(theta)*len(rolls),worst_grid=best,
                 interpolation_bound_mm=interpolation_bound,
                 conservative_clearance_mm=best["clearance_mm"]-interpolation_bound,
                 scope="足/脛全体/左右スペーサー/標準と鏡像の脛殻。床は平面。TPU変形は含めない。",
                 assumption="足実効長>=物理脛135mm、pitch[-45,55]、knee[-44,44]、IKのhypot半径>=0")
    check("連続角度域でTPUが先行接地",support["conservative_clearance_mm"]>1.,**support)
    # 傾斜時に実メッシュの最低支持点がφ10プラグ軸から移る量を記録する。
    # 最終シムが与えられたときは、その最大 roll/pitch を使う。未指定時は
    # 既存の設計横傾斜範囲だけを走査し、シム荷重を作り出さない。
    force_summary = _simulation_force_summary(simulation)
    sim_roll = _finite_float(force_summary.get("max_abs_roll_deg"))
    sim_pitch = _finite_float(force_summary.get("max_abs_pitch_deg"))
    if sim_roll is None:
        sim_roll = float(roll_deg)
        tilt_source = "design_roll_range (final simulation not supplied)"
    else:
        tilt_source = "simulation max_abs_roll_deg/max_abs_pitch_deg"
    if sim_pitch is None:
        sim_pitch = 0.
    # 床法線は既存の連続干渉検査と同じ符号規約を使う。
    support_cases=[]
    for pitch in sorted(set((-abs(sim_pitch), abs(sim_pitch)))):
        for roll in sorted(set((-abs(sim_roll), abs(sim_roll)))):
            th0, rr0 = np.radians([pitch, roll])
            normal=np.array([-np.sin(th0)*np.cos(rr0), np.sin(rr0),
                             np.cos(th0)*np.cos(rr0)])
            case=_support_envelope(shoe,normal,support_tolerance_mm)
            case.update(pitch_deg=float(pitch),roll_deg=float(roll))
            support_cases.append(case)
    support_eccentricity=dict(
        status="CAD_SUPPORT_ENVELOPE_ONLY" if force_summary.get("max_normal_contact_force_N") is None
        else "CAD_SUPPORT_ENVELOPE_WITH_SIM_TILT",
        tilt_source=tilt_source,
        simulation_source=force_summary.get("source"),
        requested_max_abs_pitch_deg=float(sim_pitch),
        requested_max_abs_roll_deg=float(sim_roll),
        plug_axis_xy_mm=[0., 0.],
        cases=support_cases,
        maximum_edge_eccentricity_mm=max((s["edge_support_eccentricity_mm"]
                                          for s in support_cases),default=None),
        maximum_centroid_eccentricity_mm=max((s["support_centroid_eccentricity_mm"]
                                              for s in support_cases),default=None),
        limitation=("床との実接触圧力分布、TPUの変形、歩行中の接触移動は未測定。"
                    "傾斜時の偏心は実STL最低支持点からの幾何量として扱う。")
    )
    # 引抜き方向へ動かしたとき、段差で受けることを実メッシュで示す。
    # これは破断/疲労/摩擦の証明ではない。
    retention=[]
    for vec,name in (([0,0,-1],"下向き抜け"),([1,0,0],"横ずれX"),([0,1,0],"横ずれY")):
        moved=shoe.copy();moved.apply_translation(vec)
        stop=intersection(moved,sp)+intersection(moved,sn)+intersection(moved,foot)
        retention.append(dict(direction=name,translation_mm=vec,stop_intersection_mm3=stop))
        check("形状による保持:"+name,stop>.1,stop_intersection_mm3=stop)
    # 設計荷重の必要強度のみ。TPUの異方性/層接着を仮の許容応力で隠さない。
    net_ear_area=2*(d["ear_width"]-d["bore_d"])*(d["counterbore_floor_y"]-d["ear_inner_y"])
    bearing_area=math.pi*(d["spacer_flange_d"]**2-d["bore_d"]**2)/4*2
    strength=dict(reference_load_n=load_n,proof_load_n=None,
                  proof_load_is_established=False,reference_22p4_is_not_a_pass=True,
                  plug_compression_area_mm2=math.pi*10**2/4,
                  plug_compression_demand_mpa=load_n/(math.pi*10**2/4),
                  two_ear_net_area_mm2=net_ear_area,ear_net_tension_demand_mpa=load_n/net_ear_area,
                  two_flange_bearing_area_mm2=bearing_area,flange_bearing_demand_mpa=load_n/bearing_area,
                  confidence="22.4N等の固定参照値による断面平均。機械合格ではなく、"
                             "応力集中/座屈/積層/クリープを実物試験で別に確認する")
    root_section = _root_section_properties(foot)
    root_strength = _contact_load_demand(force_summary,support_cases,root_section,
                                         reference_load_n=load_n)
    volume_shoe=float(shoe.volume);volume_spacer=float(sp.volume)
    tpu_density = C.material_density_g_cm3("TPU95A")
    pla_density = C.material_density_g_cm3("PLA")
    density_config_sha256 = hashlib.sha256(
        (ROOT / "hardware/src/config.py").read_bytes()).hexdigest()
    printing=dict(tpu_shoes=4,pla_spacers=16,shoe_volume_each_mm3=volume_shoe,
                  spacer_volume_each_mm3=volume_spacer,
                  tpu_solid_4_mass_g=4*volume_shoe*tpu_density*.001,
                  pla_solid_16_mass_g=16*volume_spacer*pla_density*.001,
                  density_assumption_g_cm3={
                      "TPU": tpu_density,
                      "TPU95A": tpu_density,
                      "PLA": pla_density},
                  density_source_config={
                      "path":"hardware/src/config.py",
                      "sha256":density_config_sha256},
                  old_blue_shells="旧標準2/鏡像2を保管。加工する場合は生成差分だけ。新たに印刷する場合は対応4個。",
                  printer_setting="TPU95A: 0.2mm、4周壁、100%充填、底面を下。PLAスペーサー: 0.2mm、4周壁、100%、フランジを下。既存3MFに入っていない。")
    result=dict(status="PASS_GEOMETRY_BENCH_REQUIRED" if all(c["pass_geometry"] for c in checks) else "FAIL_GEOMETRY",
                checks=checks,pairs=pairs,shell_changes=shell_rows,continuous_support=support,
                retention=retention,strength_demand=strength,
                support_eccentricity=support_eccentricity,
                root_section=root_section,contact_load_demand=root_strength,
                printing=printing,
                boolean_volume_audit=boolean_audit,
                actual_hardware_test="NOT_RUN: 印刷/実機へ書込/ねじ締結/床歩行をこの検査は実行しない")
    # Keep provenance in the verification artifact itself.  The orchestrator
    # checks all of these fields against the current assembly and its source
    # freeze, so an old or hand-edited PASS cannot be accepted silently.
    assembly_path = directory / "assembly.json"
    source_hashes = {
        str(row["path"]): hashlib.sha256((ROOT / row["path"]).read_bytes()).hexdigest()
        for row in data["sources"]
    }
    config_sha256 = source_hashes.get("hardware/src/config.py")
    result.update({
        "checker_path": "tools/check_print_first_feet.py",
        "checker_sha256": hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest(),
        "assembly_path": "outputs/print-first-20260905/feet/assembly.json",
        "assembly_sha256": hashlib.sha256(assembly_path.read_bytes()).hexdigest(),
        "config_path": "hardware/src/config.py",
        "config_sha256": config_sha256,
        "input_sha256": source_hashes,
        "expected_check_names": sorted(c["name"] for c in checks),
        "permitted_status_states": sorted(PERMITTED_VERIFICATION_STATUS_STATES),
    })
    (directory/"verification.json").write_text(
        json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+"\n")
    print(json.dumps({"status":result["status"],"failed":[c for c in checks if not c["pass_geometry"]],
                      "support":support,"shell":shell_rows,"printing":printing},ensure_ascii=False,indent=2))
    return result


def render(directory):
    """製作前に取付と局所変更を確認する実STLの図。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import PolyCollection
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection
    from matplotlib import font_manager
    from matplotlib.font_manager import FontProperties
    # Keep the platform font lookup free of a literal absolute path in the
    # public source record; the runtime path remains the same on macOS.
    font=Path("/", "System", "Library", "Fonts", "ヒラギノ角ゴシック W3.ttc")
    if font.exists():font_manager.fontManager.addfont(str(font));plt.rcParams["font.family"]=FontProperties(fname=font).get_name()
    plt.rcParams["axes.unicode_minus"]=False
    directory=Path(directory)
    data=json.loads((directory/"assembly.json").read_text())
    foot,tibia=F.source_parts()
    shoe=load(directory/"tpu_shoe_foot_frame.stl")
    shell=load(directory/"shin_shell_retained_foot_frame.stl")
    parts=[(shell,"#2198d0",.50),(tibia,"#adbec9",1),(foot,"#9b9ea3",1),
           (shoe,"#263c47",1)]
    for n in ("pla_spacer_positive_y","pla_spacer_negative_y","pla_spacer_upper_positive_y","pla_spacer_upper_negative_y"):
        parts.append((load(ROOT/data["outputs"][n]["path"]),"#71b59a",1))
    fig=plt.figure(figsize=(15,7))
    ax=fig.add_subplot(131,projection="3d")
    for m,col,alpha in parts:
        ax.add_collection3d(Poly3DCollection(m.triangles,facecolor=col,edgecolor="none",alpha=alpha))
    ax.set(xlim=(-33,33),ylim=(-33,28),zlim=(-15,90),xlabel="X [mm]",ylabel="Y [mm]",zlabel="Z [mm]")
    ax.set_box_aspect((66,61,105));ax.view_init(22,-65);ax.set_title("青い殻とPLAの甲を残す",loc="left")
    ax2=fig.add_subplot(132)
    slab=box(.6,180,180).translate([0,0,65])
    for m,col,alpha in parts:
        mm=F.as_manifold(m)^slab
        if mm.volume()>0:
            sm=to_trimesh(mm);ax2.add_collection(PolyCollection(sm.vertices[sm.faces][:,:,[1,2]],facecolor=col,edgecolor="none"))
    for z in (35,75):
        ax2.plot([-22.6,17.4],[z,z],color="#a86e31",lw=2)
        ax2.plot([17.4,19.9],[z,z],color="#a86e31",lw=5)
        ax2.plot([-21.4,-17.4],[z,z],color="#a86e31",lw=5)
    ax2.annotate("M3×40 ×2\nねじ座の幅34.8 mm",xy=(18,75),xytext=(25,83),fontsize=9,arrowprops={"arrowstyle":"->"})
    ax2.annotate("段付きPLA\n4個 / 脚",xy=(13,35),xytext=(26,48),fontsize=9,arrowprops={"arrowstyle":"->"})
    ax2.annotate("元のφ10プラグ",xy=(0,4),xytext=(-38,17),fontsize=9,arrowprops={"arrowstyle":"->"})
    ax2.set(xlim=(-42,49),ylim=(-16,97),xlabel="Y [mm]",ylabel="Z [mm]");ax2.set_aspect("equal")
    ax2.set_title("中央断面：接着剤なしの保持",loc="left");ax2.spines[["right","top"]].set_visible(False)
    ax3=fig.add_subplot(133,projection="3d")
    base=to_trimesh(F.as_manifold(shoe)^box(180,180,100).translate([0,0,-50]))
    opened=[to_trimesh(w) for w in F.flex_webs(F.as_manifold(shoe),30)]
    for m in [base,*opened]:ax3.add_collection3d(Poly3DCollection(m.triangles,facecolor="#263c47",edgecolor="none"))
    for m,col in ((shell,"#2198d0"),(tibia,"#adbec9")):
        mm=m.copy();mm.apply_translation([0,0,20]);ax3.add_collection3d(Poly3DCollection(mm.triangles,facecolor=col,edgecolor="none",alpha=.5))
    ax3.add_collection3d(Poly3DCollection(foot.triangles,facecolor="#9b9ea3",edgecolor="none"))
    ax3.set(xlim=(-33,33),ylim=(-40,40),zlim=(-15,90),xlabel="X [mm]",ylabel="Y [mm]",zlabel="Z [mm]")
    ax3.set_box_aspect((66,80,105));ax3.view_init(22,-65)
    ax3.set_title("装着経路：耳を30°開く\nひずみ5.24%は実物試験が必要",
                  loc="left",fontsize=10,pad=8)
    fig.suptitle("既存部材で留める三葉TPU靴 — 印刷前の形状確認",
                 fontsize=15,y=.975)
    fig.text(.04,.025,"硬いトゥ12本と旧TPUパッドは保管。黒=TPU、緑=追加PLA、青=保持殻。\n"
                    "CAD確認と実物合格を区別する。",fontsize=9)
    # 3D軸の長いタイトルを tight_layout に任せると表題下の注記と重なるため、
    # 上下の予約域を明示してから保存する。
    fig.subplots_adjust(left=.025,right=.985,top=.82,bottom=.16,wspace=.12)
    fig.savefig(directory/"assembly-preview.png",dpi=150);plt.close(fig)

    original=load(ROOT/"hardware/stl/shin_shell.stl")
    original.apply_transform(np.diag([1.,-1.,-1.,1.]));original.apply_translation([0,0,C.TIBIA_LEN-16])
    removed=F.as_manifold(original)-F.as_manifold(shell)
    fig,axes=plt.subplots(1,2,figsize=(12,7))
    for ax,y in zip(axes,[0,16.7]):
        cutter=box(150,.4,160).translate([0,y,50])
        for m,col in ((F.as_manifold(original),"#d9e1e5"),(F.as_manifold(shell),"#2198d0"),(removed,"#e46848")):
            sect=m^cutter
            if sect.volume()>0:
                sm=to_trimesh(sect);ax.add_collection(PolyCollection(sm.vertices[sm.faces][:,:,[0,2]],facecolor=col,edgecolor="none"))
        ax.set(xlim=(-24,34),ylim=(-4,118),xlabel="X [mm]",ylabel="足原点からZ [mm]")
        ax.set_aspect("equal");ax.set_title(f"Y={y:g} mm 断面：赤だけを変更",loc="left")
        ax.axhline(35,color="#77838b",ls=":",lw=.7);ax.axhline(75,color="#77838b",ls=":",lw=.7)
        ax.spines[["right","top"]].set_visible(False)
    fig.suptitle("青い殻の全長を保存 — 耳の通路と上下のねじ座を加工",
                 fontsize=15,y=.975)
    fig.subplots_adjust(left=.06,right=.985,top=.88,bottom=.08,wspace=.18)
    fig.savefig(directory/"shell-local-changes.png",dpi=150);plt.close(fig)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--directory",type=Path,default=F.DEFAULT_OUTPUT)
    p.add_argument("--step-deg",type=float,default=.1)
    p.add_argument("--simulation",type=Path,
                   help="最終形状を使ったシム結果JSON。鉛直/水平接触力が無い成分は推測しない")
    p.add_argument("--support-tolerance-mm",type=float,default=.2,
                   help="最低支持点を抽出する実メッシュ層の厚み")
    p.add_argument("--render-only",action="store_true")
    args=p.parse_args()
    if args.render_only:
        render(args.directory);return 0
    result=run(args.directory,step_deg=args.step_deg,simulation=args.simulation,
               support_tolerance_mm=args.support_tolerance_mm)
    render(args.directory)
    return 0 if result["status"].startswith("PASS_GEOMETRY") else 1


if __name__=="__main__":raise SystemExit(main())
