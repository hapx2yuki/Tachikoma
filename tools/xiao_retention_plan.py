#!/usr/bin/env python3
"""XIAO 本体基板・拡張基板・カメラ/FPC の再利用保持案を組み立てる。

このファイルは共有の ``config.py``、body 生成、assembly 生成を変更せず、
監査で保存した測定結果から「どこへ置き、何を印刷するか」の候補を作る
独立した補助である。寸法や FPC の曲げは現物確認前なので、出力は量産用
manifest へ自動採用しない。

API 利用例（機械側で最終座標を渡す場合）::

  spec = build_holder_spec(
    board_size_mm=(22.782, 17.780, 8.030),
    board_frame_flat_to_chassis=..., camera_frame_chassis=...,
    fpc_start_mm=..., connector_center_mm=...,
    fpc_free_length_mm=9.2,
    base_camera_frame=..., # 最終実行時の A.context(...).camera_mount_frame
    holder_z_offset_mm=4.0, # 基板/保持台だけを camera ローカル +Z へ移動
  )

``board_frame_flat_to_chassis`` は基板包絡の中心から chassis への変換、
``camera_frame_chassis`` は保存済みの中央カメラ取付フレームである。
基板とFPCを古い chassis 座標へ直接足し込まず、まず
``inverse(camera_frame_chassis) @ board_frame_flat_to_chassis`` と同じ逆変換で
camera ローカルへ移し、必要なら ``base_camera_frame`` で最終 base 座標へ戻す。
最終設計が凍結したら、機械側が同じ API に最終メッシュ由来の値を渡して統合する。
このスクリプトの公開経路は保存済み要約JSONと数値引数だけで動き、メーカー原本
STEP/STLを読み込まない。
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "docs" / "audits" / "20260905-round2"
STEP_DIR = AUDIT / "primary-sources" / "xiao-step-measured"
CAMERA_DIR = AUDIT / "camera-ov3660-candidate"
DEFAULT_OUTPUT = AUDIT / "xiao-retention-plan.json"
DEFAULT_MESH_OUTPUT = AUDIT / "xiao-retention-candidate"

# ``make_holder_meshes`` returns only mesh values so existing callers can iterate
# over the result without accidentally translating metadata. The policy is
# kept separately and is intentionally explicit: the carrier/board holder is
# raised in camera-local +Z, while the existing camera child/lens stays at its
# camera-local measured position.
HOLDER_MESH_ROLES = {
  "xiao_all_boards_occupancy": "board_occupancy",
  "camera_child_lens_occupancy": "camera_child_lens_occupancy",
  "xiao_tray_floor_candidate": "holder_floor",
  "xiao_tray_rib_left_candidate": "holder_rib",
  "xiao_tray_rib_right_candidate": "holder_rib",
  "xiao_tray_holder3_union_candidate": "holder_union",
}
HOLDER_Z_OFFSET_ROLES = frozenset({
  "board_occupancy",
  "holder_floor",
  "holder_rib",
  "holder_union",
})


def holder_mesh_role(name: str) -> str:
  """返却メッシュの論理役割を返す。

  役割は配置更新の選別に使う。特に ``camera_child_lens_occupancy`` は
  既存カメラ側の固定占有であり、保持台の ``+Z`` 移動対象ではない。
  """
  try:
    return HOLDER_MESH_ROLES[name]
  except KeyError as exc:
    raise ValueError(f"unknown XIAO holder mesh: {name}") from exc


def holder_mesh_offset_policy() -> dict[str, dict[str, Any]]:
  """各保持候補へ ``holder_z_offset_mm`` を適用するか返す。"""
  return {
    name: {
      "role": role,
      "apply_holder_z_offset": role in HOLDER_Z_OFFSET_ROLES,
      "frame": "eye_pod_camera",
    }
    for name, role in HOLDER_MESH_ROLES.items()
  }


def _read_json(path: Path) -> dict[str, Any]:
  return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
  return hashlib.sha256(path.read_bytes()).hexdigest()


def _rel(path: Path) -> str:
  path = Path(path)
  if ".." in path.parts or "\x00" in str(path) or "\\" in str(path):
    raise ValueError(f"public XIAO path must be canonical and traversal-free: {path}")
  try:
    resolved = path.resolve(strict=False)
    if not resolved.is_relative_to(ROOT):
      raise ValueError(f"public XIAO path must stay inside repository: {path}")
    return resolved.relative_to(ROOT).as_posix()
  except ValueError as exc:
    raise ValueError(f"public XIAO path must stay inside repository: {path}") from exc


def _checked_repo_output(path: Path, label: str) -> Path:
  """Resolve an output only after rejecting traversal and outside paths."""
  candidate = Path(path)
  if ".." in candidate.parts or "\x00" in str(candidate) or "\\" in str(candidate):
    raise ValueError(f"{label} must be a repository-relative path")
  if candidate.is_symlink():
    raise ValueError(f"{label} symlink is not permitted")
  if not candidate.is_absolute():
    candidate = ROOT / candidate
  resolved = candidate.resolve(strict=False)
  if not resolved.is_relative_to(ROOT):
    raise ValueError(f"{label} must stay inside repository")
  return resolved


def _vec(values: Iterable[float]) -> list[float]:
  return [float(v) for v in values]


def _matrix(values: Sequence[Sequence[float]]) -> list[list[float]]:
  return [[float(v) for v in row] for row in values]


def _mat4(values: Sequence[Sequence[float]]) -> np.ndarray:
  matrix = np.asarray(values, dtype=float)
  if matrix.shape != (4, 4):
    raise ValueError("a homogeneous transform must be 4x4")
  if not np.all(np.isfinite(matrix)):
    raise ValueError("a homogeneous transform must be finite")
  if not np.allclose(matrix[3], [0.0, 0.0, 0.0, 1.0]):
    raise ValueError("a homogeneous transform must have [0,0,0,1] as its last row")
  return matrix


def _inverse(values: Sequence[Sequence[float]]) -> np.ndarray:
  matrix = _mat4(values)
  try:
    inverse = np.linalg.inv(matrix)
  except np.linalg.LinAlgError as exc:
    raise ValueError("a frame transform must be invertible") from exc
  if not np.all(np.isfinite(inverse)):
    raise ValueError("a frame transform inverse must be finite")
  return inverse


def _compose(*values: Sequence[Sequence[float]]) -> np.ndarray:
  result = np.eye(4)
  for value in values:
    result = result @ _mat4(value)
  return result


def _box_corners(size_mm: Sequence[float]) -> list[list[float]]:
  half = [float(v) / 2.0 for v in size_mm]
  return [
    [sx * half[0], sy * half[1], sz * half[2], 1.0]
    for sx in (-1.0, 1.0)
    for sy in (-1.0, 1.0)
    for sz in (-1.0, 1.0)
  ]


def _apply(matrix: Sequence[Sequence[float]], point: Sequence[float]) -> list[float]:
  return [
    sum(float(matrix[i][j]) * float(point[j]) for j in range(4))
    for i in range(3)
  ]


def _world_bounds(
  size_mm: Sequence[float], frame: Sequence[Sequence[float]]
) -> list[list[float]]:
  points = [_apply(frame, p) for p in _box_corners(size_mm)]
  return [
    [min(p[i] for p in points) for i in range(3)],
    [max(p[i] for p in points) for i in range(3)],
  ]


def build_holder_spec(
  *,
  board_size_mm: Sequence[float],
  board_frame_flat_to_chassis: Sequence[Sequence[float]],
  camera_frame_chassis: Sequence[Sequence[float]],
  fpc_start_mm: Sequence[float],
  connector_center_mm: Sequence[float],
  fpc_free_length_mm: float,
  base_camera_frame: Sequence[Sequence[float]] | None = None,
  camera_child_envelope_size_mm: Sequence[float] = (8.0, 9.56709, 9.10175),
  camera_child_center_camera_mm: Sequence[float] = (0.0, -7.32913, 13.96086),
  clearance_each_side_mm: float = 0.30,
  wall_mm: float = 1.20,
  short_rib_height_mm: float = 3.0,
  rib_floor_overlap_mm: float | None = None,
  floor_board_clearance_mm: float = 0.01,
  holder_z_offset_mm: float = 0.0,
  board_design_mass_g: float = 10.0,
  camera_design_mass_g: float = 5.0,
) -> dict[str, Any]:
  """機械側へ渡す、再利用前提の保持台仕様を返す。

  ``camera_frame_chassis`` と ``board_frame_flat_to_chassis`` から一度だけ
  ``board_to_camera`` を計算し、FPC点も同じ相対フレームへ変換する。
  ``base_camera_frame`` を渡した場合は、同じ相対変換を最終実行時の
  ``A.context(...).camera_mount_frame`` などへ適用した base 座標も返す。
  ``make_plan`` が保存する base 座標は、A.context 外で読んだ通常設定の
  参照値に過ぎず、print-first最終フレームの確定値ではない。
  ここでは閉じた部品 STL は生成しない。未測定の購入品を確定寸法として
  焼き込まないため、保持台の包絡・公差・検査条件だけを返す。
  """

  size = [float(v) for v in board_size_mm]
  if len(size) != 3 or any(v <= 0.0 for v in size):
    raise ValueError("board_size_mm must contain three positive values")
  child_size = [float(v) for v in camera_child_envelope_size_mm]
  child_center = [float(v) for v in camera_child_center_camera_mm]
  if len(child_size) != 3 or any(v <= 0.0 for v in child_size):
    raise ValueError("camera_child_envelope_size_mm must contain three positive values")
  if len(child_center) != 3 or not np.all(np.isfinite(child_center)):
    raise ValueError("camera_child_center_camera_mm must contain three finite values")
  clearance = float(clearance_each_side_mm)
  wall = float(wall_mm)
  free_flex = float(fpc_free_length_mm)
  rib_height = float(short_rib_height_mm)
  overlap = wall / 3.0 if rib_floor_overlap_mm is None else float(rib_floor_overlap_mm)
  floor_board_clearance = float(floor_board_clearance_mm)
  holder_z_offset = float(holder_z_offset_mm)
  board_mass = float(board_design_mass_g)
  camera_mass = float(camera_design_mass_g)
  if clearance < 0.0 or wall <= 0.0 or free_flex <= 0.0 or rib_height <= 0.0:
    raise ValueError("clearance, wall, flex length, and rib height must be positive")
  if not np.isfinite(overlap) or overlap <= 0.0 or overlap >= wall:
    raise ValueError("rib_floor_overlap_mm must be finite, > 0, and < wall_mm")
  if not np.isfinite(floor_board_clearance) or floor_board_clearance < 0.0:
    raise ValueError("floor_board_clearance_mm must be finite and non-negative")
  if not np.isfinite(holder_z_offset):
    raise ValueError("holder_z_offset_mm must be finite")
  if board_mass < 0.0 or camera_mass < 0.0:
    raise ValueError("design masses must be non-negative")
  fpc_start = np.asarray(fpc_start_mm, dtype=float)
  connector = np.asarray(connector_center_mm, dtype=float)
  if fpc_start.shape != (3,) or connector.shape != (3,):
    raise ValueError("FPC points must contain three values")
  if not np.all(np.isfinite(fpc_start)) or not np.all(np.isfinite(connector)):
    raise ValueError("FPC points must be finite")
  board_chassis = _mat4(board_frame_flat_to_chassis)
  camera_chassis = _mat4(camera_frame_chassis)
  camera_to_chassis = _inverse(camera_chassis)
  board_to_camera = camera_to_chassis @ board_chassis
  # The carrier-side +Z move is expressed in the camera-local frame. Keep
  # the nominal transform for traceability, then derive the moved board and
  # holder transform explicitly. The camera child/lens never uses this
  # transform.
  holder_offset_camera = np.eye(4)
  holder_offset_camera[2, 3] = holder_z_offset
  board_to_camera_after_holder_offset = holder_offset_camera @ board_to_camera
  fpc_start_camera = camera_to_chassis @ np.r_[fpc_start, 1.0]
  connector_camera = camera_to_chassis @ np.r_[connector, 1.0]
  if not np.all(np.isfinite(fpc_start_camera)) or not np.all(np.isfinite(connector_camera)):
    raise ValueError("FPC points must be finite three-vectors")
  fpc_camera = np.asarray(fpc_start_camera[:3], dtype=float)
  connector_camera_nominal = np.asarray(connector_camera[:3], dtype=float)
  connector_camera_holder = connector_camera_nominal + np.array(
    [0.0, 0.0, holder_z_offset], dtype=float
  )
  board_base = None
  board_base_after_holder_offset = None
  fpc_start_base = None
  connector_base = None
  connector_base_after_holder_offset = None
  if base_camera_frame is not None:
    base_camera = _mat4(base_camera_frame)
    board_base = base_camera @ board_to_camera
    board_base_after_holder_offset = base_camera @ board_to_camera_after_holder_offset
    fpc_start_base = base_camera @ fpc_start_camera
    connector_base = base_camera @ connector_camera
    connector_base_after_holder_offset = base_camera @ np.r_[connector_camera_holder, 1.0]
  fpc_endpoint_distance = float(
    np.linalg.norm(connector_camera_holder - fpc_camera)
  )
  fpc_remaining_length = free_flex - fpc_endpoint_distance
  if not np.isfinite(fpc_endpoint_distance) or not np.isfinite(fpc_remaining_length):
    raise ValueError("FPC endpoint distance must be finite")
  # Extend the floor beneath the complete rib footprint. Merely bringing
  # the floor edge to the rib edge leaves a non-manifold edge after STL
  # export; the overlap below is deliberately recorded in the spec.
  floor_size = [
    size[0] + 2.0 * clearance,
    size[1] + 2.0 * clearance + 2.0 * wall,
    wall,
  ]
  return {
    "id": "xiao_internal_tray_retention_candidate",
    "status": "CANDIDATE_ONLY_UNVERIFIED_HARDWARE",
    "coordinate_frame": "eye_pod_camera local for holder/occupancy; board envelope is centered in its flat frame",
    "reuse_intent": {
      "xiao_main_board": "retain_existing_board; revision and soldered headers require inspection",
      "sense_expansion_board": "retain_existing_board; MicroSD is optional and must remain serviceable",
      "camera_module": "retain_existing_camera; exact part and lens/FPC interface require measurement",
      "fpc": "retain_existing_fpc; free length, bend radius, locking direction, and contacts require measurement",
      "new_purchase_required": False,
    },
    "inputs": {
      "board_size_mm": size,
      "board_frame_flat_to_chassis": _matrix(board_frame_flat_to_chassis),
      "camera_frame_chassis": _matrix(camera_frame_chassis),
      "base_camera_frame": None if base_camera_frame is None else _matrix(base_camera_frame),
      "fpc_start_mm": _vec(fpc_start_mm),
      "connector_center_mm": _vec(connector_center_mm),
      "fpc_free_length_mm": free_flex,
      "camera_child_envelope_size_mm": child_size,
      "camera_child_center_camera_mm": child_center,
      "clearance_each_side_mm": clearance,
      "wall_mm": wall,
      "short_rib_height_mm": rib_height,
      "rib_floor_overlap_mm": overlap,
      "floor_board_clearance_mm": floor_board_clearance,
      "holder_z_offset_mm": holder_z_offset,
      "holder_z_offset_frame": "eye_pod_camera local +Z",
      "rib_floor_overlap_source": (
        "wall_mm/3 (derived)" if rib_floor_overlap_mm is None
        else "explicit rib_floor_overlap_mm API argument"
      ),
    },
    "relative_frames": {
      "board_to_camera": _matrix(board_to_camera),
      "holder_offset_camera": _matrix(holder_offset_camera),
      "board_to_camera_after_holder_offset": _matrix(board_to_camera_after_holder_offset),
      "fpc_start_camera_mm": _vec(fpc_start_camera[:3]),
      "connector_center_camera_mm": _vec(connector_camera[:3]),
      "connector_center_camera_after_holder_offset_mm": _vec(connector_camera_holder),
      "board_to_base": None if board_base is None else _matrix(board_base),
      "board_to_base_after_holder_offset": (
        None if board_base_after_holder_offset is None
        else _matrix(board_base_after_holder_offset)
      ),
      "fpc_start_base_mm": None if fpc_start_base is None else _vec(fpc_start_base[:3]),
      "connector_center_base_mm": None if connector_base is None else _vec(connector_base[:3]),
      "connector_center_base_after_holder_offset_mm": (
        None if connector_base_after_holder_offset is None
        else _vec(connector_base_after_holder_offset[:3])
      ),
      "fpc_endpoints": {
        "camera_end": {
          "point_camera_mm": _vec(fpc_camera),
          "movement_camera_mm": [0.0, 0.0, 0.0],
          "movement_rule": "fixed existing camera/carrier endpoint",
        },
        "board_connector_end": {
          "point_camera_nominal_mm": _vec(connector_camera_nominal),
          "holder_offset_camera_mm": [0.0, 0.0, holder_z_offset],
          "point_camera_after_holder_offset_mm": _vec(connector_camera_holder),
          "movement_rule": "move with board occupancy and holder only",
        },
        "straight_endpoint_distance_after_holder_offset_mm": fpc_endpoint_distance,
        "free_length_mm": free_flex,
        "remaining_free_length_mm": fpc_remaining_length,
        "nominal_length_status": (
          "PASS_DISTANCE_WITHIN_FREE_LENGTH"
          if fpc_remaining_length >= 0.0
          else "FAIL_DISTANCE_EXCEEDS_FREE_LENGTH"
        ),
        "hardware_status": "UNVERIFIED_PURCHASED_FPC_AND_BEND_RADIUS",
      },
      "transform_rule": "inverse(camera_frame_chassis) @ board_frame_flat_to_chassis; for board/holder use holder_offset_camera @ board_to_camera; then base_camera_frame",
    },
    "occupancy": {
      "xiao_all_boards": {
        "frame": "eye_pod_camera",
        "size_mm": size,
        "transform_camera_nominal": _matrix(board_to_camera),
        "transform_camera": _matrix(board_to_camera_after_holder_offset),
        "transform_base_nominal": None if board_base is None else _matrix(board_base),
        "transform_base": (
          None if board_base_after_holder_offset is None
          else _matrix(board_base_after_holder_offset)
        ),
        "holder_offset_applied": True,
        "design_mass_g": board_mass,
        "mass_status": "UNVERIFIED_DESIGN_ALLOWANCE",
        "contents": "XIAO main board + Sense Expansion Board + USB/shield/header/SD allowance",
      },
      "camera_child_lens": {
        "frame": "eye_pod_camera",
        "size_mm": child_size,
        "center_camera_mm": child_center,
        "holder_offset_applied": False,
        "source": "OV3660 candidate child envelope from saved measurement summary; raw source mesh is not a runtime dependency",
        "design_mass_g": camera_mass,
        "mass_status": "UNVERIFIED_DESIGN_ALLOWANCE",
        "geometry_status": "OV3660 candidate envelope; purchased camera identity and lens center unverified",
      },
    },
    "printed_holder_concept": {
      "part_role": "detachable tray plus two short ribs in eye_pod_camera local coordinates; no camera shell/body rewrite",
      "print_orientation": "tray floor down, board flat, USB and MicroSD service sides open",
      "floor_envelope_mm": floor_size,
      "side_clearance_each_mm": clearance,
      "rail_wall_mm": wall,
      "short_rib_height_mm": rib_height,
      "rib_floor_overlap_mm": overlap,
      "rib_floor_overlap_rule": "rib bottom sinks by overlap; rib top remains at the original height",
      "floor_board_clearance_mm": floor_board_clearance,
      "floor_board_clearance_rule": "keep the holder floor below the board occupancy by this candidate clearance so STL roundtrip intersection remains zero",
      "holder_z_offset_mm": holder_z_offset,
      "holder_z_offset_rule": "apply only to board occupancy, floor, ribs and holder union; camera child/lens remains fixed",
      "floor_support_rule": "floor Y span includes both rib wall widths so each rib has a volumetric footprint",
      "retention": "nonconductive strap through serviceable slots; adhesive is not the primary torque path",
      "strap_slot_concept_mm": {"width": 3.0, "height": 1.0},
      "anchor_status": "UNCONNECTED_CANDIDATE_RIBS_NEED_MECHANICAL_INTEGRATION",
      "anchor": "mechanical integration must choose a verified head/chassis face after freeze2; the two ribs are not connected to the carrier by this helper",
    },
    "mass_candidates": {
      "xiao_main_plus_expansion_g": board_mass,
      "camera_child_lens_g": camera_mass,
      "status": "UNVERIFIED_DESIGN_ALLOWANCE",
      "use": "pass as arguments to mechanical mesh mass/COM/inertia calculation; do not add to measured mass automatically",
      "decision": "accepted_as_design_allowance_only; replace or retain after measurement",
    },
    "required_checks_before_adoption": [
      "measure the purchased XIAO and Sense Expansion Board including USB, shield, headers, SD and solder joints",
      "identify the purchased camera board and verify lens center against camera_frame_chassis",
      "measure FPC free length, connector insertion face/sign, bend radius and strain relief",
      "run actual mesh self/intersection and insertion checks with the frozen head, coxa/yaw and all electronics",
      "print one tray and one board/camera assembly for fit before production quantity",
    ],
  }


def _box_mesh(
  size_mm: Sequence[float],
  frame: Sequence[Sequence[float]],
  center_mm: Sequence[float] = (0.0, 0.0, 0.0),
):
  """中心付き直方体を指定 frame へ置く（候補可視化用）。"""

  import trimesh

  size = np.asarray(size_mm, dtype=float)
  center = np.asarray(center_mm, dtype=float)
  if size.shape != (3,) or np.any(size <= 0.0):
    raise ValueError("mesh size must contain three positive values")
  if center.shape != (3,) or not np.all(np.isfinite(center)):
    raise ValueError("mesh center must contain three finite values")
  transform = _mat4(frame).copy()
  local = np.eye(4)
  local[:3, 3] = center
  mesh = trimesh.creation.box(extents=size)
  mesh.apply_transform(transform @ local)
  return mesh


def _holder_dimensions(
  size: np.ndarray,
  clearance: float,
  wall: float,
  rib_height: float,
  overlap: float,
  floor_board_clearance: float,
) -> dict[str, Any]:
  """床・リブの寸法と中心を一つの規則から計算する。"""

  floor_size = np.asarray(
    [
      size[0] + 2.0 * clearance,
      size[1] + 2.0 * clearance + 2.0 * wall,
      wall,
    ],
    dtype=float,
  )
  floor_top_z = -size[2] / 2.0 - floor_board_clearance
  floor_center = np.asarray([0.0, 0.0, floor_top_z - wall / 2.0])
  rib_y = size[1] / 2.0 + clearance + wall / 2.0
  # ``rib_height`` denotes the original top height. Extending the solid
  # downward by overlap while moving its center keeps that top unchanged.
  rib_bottom_z = floor_top_z - overlap
  rib_top_z = -size[2] / 2.0 + rib_height
  rib_size = np.asarray(
    [size[0] + 2.0 * clearance, wall, rib_top_z - rib_bottom_z],
    dtype=float,
  )
  rib_z = (rib_bottom_z + rib_top_z) / 2.0
  return {
    "floor_size_mm": floor_size,
    "floor_center_mm": floor_center,
    "rib_y_mm": float(rib_y),
    "rib_size_mm": rib_size,
    "rib_center_z_mm": float(rib_z),
    "floor_top_z_mm": float(floor_top_z),
    "floor_board_clearance_mm": float(floor_board_clearance),
    "rib_bottom_z_mm": float(rib_bottom_z),
    "rib_top_z_mm": float(rib_top_z),
    "rib_floor_overlap_mm": float(overlap),
  }


def _union_mesh(meshes: Sequence[Any], *, label: str):
  """実体積が重なる候補部品を Manifold で一体化する。"""

  import trimesh

  if len(meshes) < 2:
    raise ValueError(f"{label}: at least two meshes are required")
  try:
    union = trimesh.boolean.union(
      [mesh.copy() for mesh in meshes],
      engine="manifold",
      check_volume=True,
    )
  except Exception as exc: # pragma: no cover - backend-specific failure
    raise ValueError(f"{label}: boolean union failed: {exc}") from exc
  if union is None or not isinstance(union, trimesh.Trimesh):
    raise ValueError(f"{label}: boolean union returned no single mesh")
  union.remove_unreferenced_vertices()
  bodies = union.split(only_watertight=False)
  if not union.is_watertight or not union.is_winding_consistent or len(bodies) != 1:
    raise ValueError(
      f"{label}: union must be one watertight consistently wound solid "
      f"(watertight={union.is_watertight}, winding={union.is_winding_consistent}, "
      f"bodies={len(bodies)})"
    )
  return union


def _intersection_volume_mm3(a: Any, b: Any) -> float:
  """二つの閉体の交差体積を Manifold で求める。"""

  import trimesh
  import warnings

  # Manifold represents an empty intersection as an empty Trimesh and
  # trimesh computes its mass properties while constructing that object,
  # which emits divide-by-zero warnings. Empty is an expected result for
  # this clearance check, so keep the diagnostic output clean while still
  # handling the returned mesh explicitly below.
  with warnings.catch_warnings():
    warnings.simplefilter("ignore", RuntimeWarning)
    try:
      intersection = trimesh.boolean.intersection(
        [a.copy(), b.copy()],
        engine="manifold",
        check_volume=True,
      )
    except Exception as exc: # pragma: no cover - backend-specific failure
      raise ValueError(f"boolean intersection failed: {exc}") from exc
    if intersection is None:
      raise RuntimeError("boolean intersection returned no mesh")

    def checked_volume(item: Any) -> float:
      """交差結果を有限・非負として検査し、異常値は失敗させる。"""
      try:
        value = float(item.volume)
      except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("intersection volume is unavailable") from exc
      if not np.isfinite(value) or value < 0.0:
        raise ValueError(
          f"intersection volume must be finite and non-negative: {value!r}"
        )
      return value

    def is_empty(item: Any) -> bool:
      if bool(getattr(item, "is_empty", False)):
        return True
      faces = getattr(item, "faces", None)
      return faces is not None and len(faces) == 0

    if isinstance(intersection, (list, tuple)):
      total = 0.0
      for item in intersection:
        if item is None:
          raise RuntimeError("boolean intersection returned a null mesh item")
        if is_empty(item):
          continue
        total += checked_volume(item)
        if not np.isfinite(total):
          raise ValueError("intersection volume sum is not finite")
      return total
    if is_empty(intersection):
      return 0.0
    return checked_volume(intersection)


def make_holder_meshes(spec: Mapping[str, Any]) -> dict[str, Any]:
  """保持台と二つの占有包絡を eye_pod_camera 座標の mesh で返す。

  返却する mesh は全て候補であり、実物適合・頭部統合・量産 manifest の
  合格を表さない。原本 STEP/STL は読み込まず、spec の寸法だけで作る。
  """

  inputs = spec["inputs"]
  relative = spec["relative_frames"]
  size = np.asarray(inputs["board_size_mm"], dtype=float)
  clearance = float(inputs["clearance_each_side_mm"])
  wall = float(inputs["wall_mm"])
  rib_height = float(inputs["short_rib_height_mm"])
  overlap = float(inputs.get("rib_floor_overlap_mm", wall / 3.0))
  floor_board_clearance = float(inputs.get("floor_board_clearance_mm", 0.01))
  board_to_camera = _mat4(relative["board_to_camera"])
  holder_z_offset = float(inputs.get("holder_z_offset_mm", 0.0))
  child_size = inputs["camera_child_envelope_size_mm"]
  child_center = inputs["camera_child_center_camera_mm"]

  if clearance < 0.0 or wall <= 0.0 or rib_height <= 0.0:
    raise ValueError("invalid holder dimensions")
  if not np.isfinite(overlap) or overlap <= 0.0 or overlap >= wall:
    raise ValueError("rib floor overlap must be finite, > 0, and < wall")
  if not np.isfinite(floor_board_clearance) or floor_board_clearance < 0.0:
    raise ValueError("floor board clearance must be finite and non-negative")
  if not np.isfinite(holder_z_offset):
    raise ValueError("holder z offset must be finite")
  holder_offset_camera = np.eye(4)
  holder_offset_camera[2, 3] = holder_z_offset
  holder_frame_camera = holder_offset_camera @ board_to_camera
  dimensions = _holder_dimensions(
    size, clearance, wall, rib_height, overlap, floor_board_clearance
  )
  floor = _box_mesh(
    dimensions["floor_size_mm"],
    holder_frame_camera,
    dimensions["floor_center_mm"],
  )
  rib_left = _box_mesh(
    dimensions["rib_size_mm"],
    holder_frame_camera,
    (0.0, -dimensions["rib_y_mm"], dimensions["rib_center_z_mm"]),
  )
  rib_right = _box_mesh(
    dimensions["rib_size_mm"],
    holder_frame_camera,
    (0.0, dimensions["rib_y_mm"], dimensions["rib_center_z_mm"]),
  )
  # Boolean the three boxes in their common board frame first. Applying a
  # non-axis-aligned camera transform to each box before the boolean can
  # leave duplicate float32 corner vertices; those become non-manifold when
  # the resulting STL is reloaded. A single transform after the local
  # union preserves shared topology through the serialization roundtrip.
  floor_local = _box_mesh(
    dimensions["floor_size_mm"],
    np.eye(4),
    dimensions["floor_center_mm"],
  )
  rib_left_local = _box_mesh(
    dimensions["rib_size_mm"],
    np.eye(4),
    (0.0, -dimensions["rib_y_mm"], dimensions["rib_center_z_mm"]),
  )
  rib_right_local = _box_mesh(
    dimensions["rib_size_mm"],
    np.eye(4),
    (0.0, dimensions["rib_y_mm"], dimensions["rib_center_z_mm"]),
  )
  holder_union = _union_mesh(
    [floor_local, rib_left_local, rib_right_local],
    label="xiao_tray_holder3_candidate_local",
  )
  holder_union.apply_transform(holder_frame_camera)
  if not holder_union.is_watertight or len(holder_union.split(only_watertight=False)) != 1:
    raise ValueError("xiao_tray_holder3_candidate: transformed union lost solid topology")
  return {
    # Board occupancy follows the tray +Z move; the camera child/lens below
    # intentionally stays in its existing camera-local position.
    "xiao_all_boards_occupancy": _box_mesh(size, holder_frame_camera),
    "camera_child_lens_occupancy": _box_mesh(child_size, np.eye(4), child_center),
    "xiao_tray_floor_candidate": floor,
    "xiao_tray_rib_left_candidate": rib_left,
    "xiao_tray_rib_right_candidate": rib_right,
    # This is a serialized one-piece alternative/inspection artifact; it
    # must never be counted in addition to the three constituent parts.
    "xiao_tray_holder3_union_candidate": holder_union,
  }


def export_holder_meshes(
  spec: Mapping[str, Any],
  output_dir: Path = DEFAULT_MESH_OUTPUT,
  *,
  name_prefix: str = "",
) -> dict[str, Any]:
  """候補 mesh を書き出し、相対 path とハッシュを返す。"""

  output_dir = _checked_repo_output(Path(output_dir), "XIAO mesh output")
  output_dir.mkdir(parents=True, exist_ok=True)
  meshes = make_holder_meshes(spec)
  rows = {}
  for name, mesh in meshes.items():
    path = output_dir / f"{name_prefix}{name}.stl"
    mesh.export(path)
    role = holder_mesh_role(name)
    rows[name] = {
      "path": _rel(path),
      "exists": True,
      "sha256": _sha256(path),
      "role": role,
      "frame": "eye_pod_camera",
      "holder_z_offset_applied": role in HOLDER_Z_OFFSET_ROLES,
      "bounds_mm": [[float(v) for v in row] for row in mesh.bounds],
      "volume_mm3": float(mesh.volume),
      "status": "CANDIDATE_ONLY_NOT_FOR_PRODUCTION",
    }
  return rows


def inspect_holder_meshes(meshes: Mapping[str, Any]) -> dict[str, Any]:
  """保持台候補の一体性と基板占有との体積交差を検査する。

  ``pass`` は候補メッシュの幾何検査結果だけを表し、実機適合や
  carrier への固定を合格扱いしない。
  """

  required = (
    "xiao_all_boards_occupancy",
    "xiao_tray_floor_candidate",
    "xiao_tray_rib_left_candidate",
    "xiao_tray_rib_right_candidate",
    "xiao_tray_holder3_union_candidate",
  )
  missing = [name for name in required if name not in meshes]
  if missing:
    raise ValueError(f"holder geometry is missing {missing}")
  holder = meshes["xiao_tray_holder3_union_candidate"]
  board = meshes["xiao_all_boards_occupancy"]
  components = len(holder.split(only_watertight=False))
  intersection = _intersection_volume_mm3(board, holder)
  tol = 1.0e-3
  holder_ok = bool(
    holder.is_watertight
    and holder.is_winding_consistent
    and components == 1
    and np.isfinite(holder.volume)
    and holder.volume > 0.0
  )
  board_ok = bool(np.isfinite(intersection) and intersection <= tol)
  return {
    "status": (
      "GEOMETRY_CHECK_PASS_CANDIDATE_ONLY"
      if holder_ok and board_ok
      else "GEOMETRY_CHECK_FAIL_CANDIDATE"
    ),
    "holder3_union": {
      "watertight": bool(holder.is_watertight),
      "winding_consistent": bool(holder.is_winding_consistent),
      "solid_count": int(components),
      "volume_mm3": float(holder.volume),
      "pass": holder_ok,
    },
    "board_occupancy_x_holder3": {
      "intersection_volume_mm3": float(intersection),
      "tolerance_mm3": tol,
      "pass": board_ok,
    },
    "interpretation": (
      "幾何候補の一体性・基板との体積非交差のみを確認。"
      "carrier接続、実機適合、保持力、量産採用は未確認。"
    ),
  }


def inspect_serialized_holder_meshes(rows: Mapping[str, Any]) -> dict[str, Any]:
  """出力STLを再読し、holder3の閉体性と基板交差を確認する。"""

  import trimesh

  required = ("xiao_all_boards_occupancy", "xiao_tray_holder3_union_candidate")
  missing = [name for name in required if name not in rows]
  if missing:
    raise ValueError(f"serialized holder rows are missing {missing}")

  def load_row(name: str):
    raw = rows[name]["path"]
    path = ROOT / _rel(Path(raw))
    mesh = trimesh.load(path, force="mesh")
    if not isinstance(mesh, trimesh.Trimesh):
      raise ValueError(f"serialized holder row is not a mesh: {path}")
    return mesh, path

  board, board_path = load_row("xiao_all_boards_occupancy")
  holder, holder_path = load_row("xiao_tray_holder3_union_candidate")
  components = len(holder.split(only_watertight=False))
  intersection = _intersection_volume_mm3(board, holder)
  tol = 1.0e-3
  holder_ok = bool(
    holder.is_watertight
    and holder.is_winding_consistent
    and components == 1
    and np.isfinite(holder.volume)
    and holder.volume > 0.0
  )
  board_ok = bool(np.isfinite(intersection) and intersection <= tol)
  return {
    "status": (
      "SERIALIZE_ROUNDTRIP_PASS_CANDIDATE_ONLY"
      if holder_ok and board_ok
      else "SERIALIZE_ROUNDTRIP_FAIL_CANDIDATE"
    ),
    "holder3_union": {
      "path": rows["xiao_tray_holder3_union_candidate"]["path"],
      "sha256": rows["xiao_tray_holder3_union_candidate"]["sha256"],
      "watertight": bool(holder.is_watertight),
      "winding_consistent": bool(holder.is_winding_consistent),
      "solid_count": int(components),
      "volume_mm3": float(holder.volume),
      "pass": holder_ok,
    },
    "board_occupancy_x_holder3": {
      "board_path": rows["xiao_all_boards_occupancy"]["path"],
      "holder_path": rows["xiao_tray_holder3_union_candidate"]["path"],
      "reload_paths_are_same_as_recorded_paths": True,
      "intersection_volume_mm3": float(intersection),
      "tolerance_mm3": tol,
      "pass": board_ok,
    },
    "interpretation": (
      "STL再読結果。幾何候補の検査であり、carrier統合・実機適合・"
      "保持力・量産採用を示さない。"
    ),
  }


def _record_for(
  bounds: Mapping[str, Any],
  suffix: str,
  source_sha256: str | None = None,
) -> dict[str, Any]:
  return {
    "source_path": f"docs/audits/20260905-round2/primary-sources/xiao-step-measured/{suffix}",
    "source_required_at_runtime": False,
    "source_sha256": source_sha256,
    "bounds_mm": bounds["bounds_mm"],
    "dimensions_mm": bounds["dimensions_mm"],
    "mesh": bounds.get("mesh"),
    "identity": "official 2023 STEP reference; purchased revision unverified",
  }


def _current_base_camera_frame() -> tuple[list[list[float]] | None, dict[str, Any]]:
  """現行 ``export_urdf.camera_mount_frame({})`` を読み取り専用で得る。"""

  try:
    sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "hardware" / "src")]
    import export_urdf as export # noqa: PLC0415

    frame = export.camera_mount_frame({})
    return _matrix(frame), {
      "status": "READ_ONLY_NORMAL_PROFILE_REFERENCE",
      "frame_context": "export_urdf.camera_mount_frame({}) outside A.context; normal-profile reference only",
      "not_print_first_final": True,
      "final_frame_source_required": "A.context(generated=False) or final print-first integration context",
      "source_path": "tools/export_urdf.py",
      "source_sha256": _sha256(ROOT / "tools" / "export_urdf.py"),
      "config_path": "hardware/src/config.py",
      "config_sha256": _sha256(ROOT / "hardware" / "src" / "config.py"),
    }
  except Exception as exc: # pragma: no cover - a public summary remains usable
    return None, {
      "status": "UNVERIFIED_CURRENT_EXPORT_URDF_UNAVAILABLE",
      "reason": str(exc),
    }


def _current_holder_z_offset() -> tuple[float, dict[str, Any]]:
  """印刷優先構成の保持台 +Z 移動量を config から読み取る。"""
  config_path = ROOT / "hardware" / "src" / "config.py"
  try:
    sys.path[:0] = [str(ROOT / "hardware" / "src")]
    import config as C # noqa: PLC0415

    value = float(C.PRINT_FIRST_XIAO["holder_z_offset_mm"])
    if not np.isfinite(value):
      raise ValueError("PRINT_FIRST_XIAO holder_z_offset_mm is not finite")
    return value, {
      "status": "READ_ONLY_PRINT_FIRST_CONFIG_REFERENCE",
      "source_path": "hardware/src/config.py",
      "source_sha256": _sha256(config_path),
      "value_mm": value,
      "frame": "eye_pod_camera local +Z",
    }
  except Exception as exc: # pragma: no cover - missing config is a hard stop
    raise ValueError(f"print-first holder z offset unavailable: {exc}") from exc


def make_plan(root: Path = ROOT) -> dict[str, Any]:
  """監査保存物から公開可能な相対パスだけを使う計画を作る。"""

  summary_path = root / STEP_DIR.relative_to(ROOT) / "camera-removed-summary.json"
  bounds_path = root / STEP_DIR.relative_to(ROOT) / "assembly-bounds.json"
  comparison_path = root / CAMERA_DIR.relative_to(ROOT) / "comparison.json"
  placement_path = root / CAMERA_DIR.relative_to(ROOT) / "xiao-placement-search.json"
  cradle_path = root / CAMERA_DIR.relative_to(ROOT) / "xiao-cradle-comparison.json"
  source_register_path = root / AUDIT.relative_to(ROOT) / "primary-sources" / "source-register.json"
  summary = _read_json(summary_path)
  bounds = _read_json(bounds_path)
  comparison = _read_json(comparison_path)
  placement = _read_json(placement_path)
  cradle = _read_json(cradle_path)
  source_register = _read_json(source_register_path)
  source_hashes = {
    str(row["path"]).split("/")[-1]: row.get("sha256")
    for row in source_register.get("files", [])
    if row.get("path")
  }

  records = bounds["records"]
  by_name = {row["path"][-1]: row for row in records if row.get("path")}
  selected = cradle["selected"]
  board_frame = selected["frame_flat_to_chassis"]
  camera_frame = comparison["camera_frame_chassis"]
  base_camera_frame, base_frame_meta = _current_base_camera_frame()
  holder_z_offset, holder_offset_meta = _current_holder_z_offset()
  without_sd = summary["camera_removed_without_sd"]
  with_sd = summary["camera_removed_with_sd"]
  fpc = summary["fpc_connector"]

  # The public plan depends on measurement JSON, not on redistribution-restricted
  # STEP/STL files. Mesh names and their historical hashes remain in the source
  # register for local review, but absence of the raw files must not block this
  # envelope/holder generation.
  source_paths = [
    summary_path,
    bounds_path,
    comparison_path,
    placement_path,
    cradle_path,
    source_register_path,
    root / "tools" / "xiao_retention_plan.py",
    root / "hardware" / "src" / "config.py",
  ]
  if base_camera_frame is not None:
    source_paths.extend([
      root / "tools" / "export_urdf.py",
    ])

  no_sd_spec = build_holder_spec(
    board_size_mm=without_sd["centered_flat_dimensions_mm"],
    board_frame_flat_to_chassis=board_frame,
    camera_frame_chassis=camera_frame,
    fpc_start_mm=placement["flex_start_chassis_mm"],
    connector_center_mm=selected["connector_center_mm"],
    fpc_free_length_mm=9.2,
    base_camera_frame=base_camera_frame,
    holder_z_offset_mm=holder_z_offset,
  )
  with_sd_spec = build_holder_spec(
    board_size_mm=with_sd["centered_flat_dimensions_mm"],
    board_frame_flat_to_chassis=board_frame,
    camera_frame_chassis=camera_frame,
    fpc_start_mm=placement["flex_start_chassis_mm"],
    connector_center_mm=selected["connector_center_mm"],
    fpc_free_length_mm=9.2,
    base_camera_frame=base_camera_frame,
    holder_z_offset_mm=holder_z_offset,
  )

  historical_camera = by_name.get("Camer Module", {})
  historical_connector = by_name.get("JUSHUO AFC01-S24FCA-00", {})
  plan = {
    "schema": "tachikoma.xiao-retention-plan.v1",
    "as_of": date.today().isoformat(),
    "status": "CANDIDATE_ONLY_UNVERIFIED_HARDWARE_AND_FREEZE2_GEOMETRY",
    "purpose": "既存XIAO本体基板・Sense拡張基板・カメラ/FPCを買い替えず保持するための独立候補",
    "scope_boundary": [
      "この計画は shared config/body/assembly の変更を含まない",
      "既存カメラを寸法確定品とは扱わず、STEP/仕様書は候補の包絡だけに使う",
      "候補STLは量産印刷数へ入れず、現物適合試作を通過するまで採用しない",
      "公開時の再生成は保存済み測定要約JSONと数値引数だけで実行でき、メーカー原本STEP/STLを要求しない",
    ],
    "holder_mesh_policy": {
      "frame": "eye_pod_camera",
      "holder_z_offset_mm": holder_z_offset,
      "holder_z_offset_source": holder_offset_meta,
      "roles": holder_mesh_offset_policy(),
      "offset_applies_to": sorted(HOLDER_Z_OFFSET_ROLES),
      "camera_child_lens_rule": "fixed at camera-local measured proxy position; no holder +Z translation",
      "board_fpc_rule": "board connector endpoint follows board/holder +Z; camera endpoint stays fixed",
    },
    "retained_hardware": {
      "xiao_assembly_without_sd": {
        "source_path": "docs/audits/20260905-round2/primary-sources/xiao-step-measured/camera_removed_without_sd_envelope.stl",
        "source_required_at_runtime": False,
        "source_sha256": source_hashes.get("camera_removed_without_sd_envelope.stl"),
        "source_summary": "XIAO主基板・Sense拡張基板・USB・shield等の閉じた全厚包絡。カメラとMicroSDを除外",
        "size_flat_mm": without_sd["centered_flat_dimensions_mm"],
        "placement_center_chassis_mm": selected["center_mm"],
        "world_bounds_chassis_mm": _world_bounds(without_sd["centered_flat_dimensions_mm"], board_frame),
        "holder_spec": no_sd_spec,
      },
      "xiao_assembly_with_sd": {
        "source_path": "docs/audits/20260905-round2/primary-sources/xiao-step-measured/camera_removed_with_sd_envelope.stl",
        "source_required_at_runtime": False,
        "source_sha256": source_hashes.get("camera_removed_with_sd_envelope.stl"),
        "source_summary": "上記にMicroSD包絡を含めた閉じた全厚包絡。MicroSDは後回しにできるが装着時の逃げを残す",
        "size_flat_mm": with_sd["centered_flat_dimensions_mm"],
        "placement_center_chassis_mm": selected["center_mm"],
        "world_bounds_chassis_mm": _world_bounds(with_sd["centered_flat_dimensions_mm"], board_frame),
        "holder_spec": with_sd_spec,
      },
      "main_board_reference": _record_for(
        by_name["Seeed Studio XIAO-ESP32-S3 (Sense)"],
        "Seeed_Studio_XIAO-ESP32-S3_Sense.stl",
        source_hashes.get("Seeed_Studio_XIAO-ESP32-S3_Sense.stl"),
      ),
      "sense_expansion_board_reference": _record_for(
        by_name["Sense Expansion Board"],
        "Sense_Expansion_Board.stl",
        source_hashes.get("Sense_Expansion_Board.stl"),
      ),
      "historical_camera_reference": _record_for(
        historical_camera,
        "Camer_Module.stl",
        source_hashes.get("Camer_Module.stl"),
      ),
      "historical_fpc_connector_reference": _record_for(
        historical_connector,
        "JUSHUO_AFC01-S24FCA-00.stl",
        source_hashes.get("JUSHUO_AFC01-S24FCA-00.stl"),
      ),
    },
    "existing_camera_frame": {
      "frame_name": "eye_pod_camera / camera_carrier",
      "frame_chassis": _matrix(camera_frame),
      "basis": "保存済みOV3660候補の camera_frame_chassis。購入カメラの現物フレームではない",
      "current_base_camera_frame": base_camera_frame,
      "current_base_frame_source": base_frame_meta,
      "current_base_frame_status": "NORMAL_PROFILE_REFERENCE_ONLY_FINAL_CONTEXT_REQUIRED",
      "conversion_rule": "board_to_camera = inverse(frame_chassis) @ board_frame_flat_to_chassis; board_to_base = current_base_camera_frame @ board_to_camera",
      "old_frame_direct_reuse_forbidden": True,
      "holder_z_offset_mm": holder_z_offset,
      "camera_child_lens_fixed_under_holder_offset": True,
      "current_camera_proxy_dimensions_mm": [
        comparison["baseline_config"]["CAM2_MODULE_L"],
        comparison["baseline_config"]["CAM2_MODULE_W"],
        comparison["baseline_config"]["CAM2_MODULE_T"],
      ],
      "camera_proxy_is_not_hardware_confirmation": True,
    },
    "board_and_fpc_position": {
      "board_frame_flat_to_chassis": _matrix(board_frame),
      "board_center_chassis_mm": _vec(selected["center_mm"]),
      "connector_center_chassis_mm": _vec(selected["connector_center_mm"]),
      "opening_candidates_chassis_mm": selected["opening_candidates_mm"],
      "fpc_start_chassis_mm": _vec(placement["flex_start_chassis_mm"]),
      "board_to_camera": no_sd_spec["relative_frames"]["board_to_camera"],
      "board_to_camera_after_holder_offset": no_sd_spec["relative_frames"]["board_to_camera_after_holder_offset"],
      "fpc_start_camera_mm": no_sd_spec["relative_frames"]["fpc_start_camera_mm"],
      "connector_center_camera_mm": no_sd_spec["relative_frames"]["connector_center_camera_mm"],
      "connector_center_camera_after_holder_offset_mm": no_sd_spec["relative_frames"]["connector_center_camera_after_holder_offset_mm"],
      "board_to_base": no_sd_spec["relative_frames"]["board_to_base"],
      "board_to_base_after_holder_offset": no_sd_spec["relative_frames"]["board_to_base_after_holder_offset"],
      "fpc_start_base_mm": no_sd_spec["relative_frames"]["fpc_start_base_mm"],
      "connector_center_base_mm": no_sd_spec["relative_frames"]["connector_center_base_mm"],
      "connector_center_base_after_holder_offset_mm": no_sd_spec["relative_frames"]["connector_center_base_after_holder_offset_mm"],
      "fpc_endpoints": no_sd_spec["relative_frames"]["fpc_endpoints"],
      "holder_z_offset_mm": holder_z_offset,
      "relative_transform_status": "DERIVED_FROM_SAVED_CANDIDATE_FRAMES_NORMAL_BASE_REFERENCE_ONLY_FINAL_CONTEXT_REQUIRED",
      "candidate_straight_lengths_mm": selected["straight_line_from_flex_start_mm"],
      "fpc_reference": {
        "total_length_mm": comparison["manufacturer_dimensions_mm"]["total_length_with_FPC"],
        "sensor_mm": comparison["manufacturer_dimensions_mm"]["sensor"],
        "contact_width_mm": comparison["manufacturer_dimensions_mm"]["contact_width"],
        "flex_width_mm": comparison["manufacturer_dimensions_mm"]["flex_width"],
        "derived_free_curve_length_mm": 9.2,
        "actual_purchased_fpc_confirmed": False,
        "connector_axis": fpc.get("insertion_axis"),
      },
      "candidate_route": {
        "length_mm": cradle["flex_curve_length_mm"],
        "minimum_radius_mm": cradle["minimum_curve_radius_mm"],
        "source_status": cradle["status"],
        "do_not_promote_to_requirement": True,
      },
    },
    "holder_concept": {
      "name": "xiao_internal_tray_retention_candidate",
      "print_quantity": 1,
      "production_manifest_status": "EXCLUDED_UNTIL_MEASURED_AND_ONE_UNIT_FIT_PASSES",
      "nominal_rule": "board envelope + 0.30mm each side; floor Y span adds both 1.20mm rib walls; rib bottoms overlap floor by wall/3 while rib tops stay unchanged",
      "retention_rule": "nonconductive strap or mechanical clip; adhesive alone is not the primary load path",
      "camera_side": "reuse existing central camera shell/carrier and route the retained FPC to the tray; do not print a replacement camera module",
      "occupancy_records": ["xiao_all_boards", "camera_child_lens"],
      "mesh_role_policy": holder_mesh_offset_policy(),
      "occupancy_quantity": "one record each; without-SD and with-SD are mutually exclusive comparison candidates",
      "printed_subparts": [
        "xiao_tray_floor_candidate",
        "xiao_tray_rib_left_candidate",
        "xiao_tray_rib_right_candidate",
      ],
      "serialized_holder3_union_candidate": "xiao_tray_holder3_union_candidate",
      "union_counting_rule": "holder3 union is a one-piece alternative/inspection artifact; never print or count it together with the three constituent parts",
      "tie_slot_parameters_mm": {"width": 3.0, "height": 1.0},
      "design_mass_candidates_g": {
        "xiao_main_plus_expansion": 10.0,
        "camera_child_lens": 5.0,
      },
      "design_mass_status": "未実測の設計余裕値。UNVERIFIED_DESIGN_ALLOWANCEとして扱い、COM/慣性へ候補値として渡す",
      "anchor_status": "UNCONNECTED_CANDIDATE_RIBS_NEED_MECHANICAL_INTEGRATION",
      "anchor_rule": "mechanical owner chooses verified head/chassis attachment after yaw/coxa and freeze2 geometry are frozen; helper ribs are not connected to carrier",
      "candidate_reference_assets": [
        "docs/audits/20260905-round2/camera-ov3660-candidate/camera_base_with_xiao_cradle_candidate.stl",
        "docs/audits/20260905-round2/camera-ov3660-candidate/OV3660_FPC_bent_candidate.stl",
      ],
    },
    "minimal_integration_api": {
      "module": "tools/xiao_retention_plan.py",
      "functions": [
        "build_holder_spec",
        "make_holder_meshes",
        "holder_mesh_role",
        "holder_mesh_offset_policy",
        "inspect_holder_meshes",
        "export_holder_meshes",
        "inspect_serialized_holder_meshes",
      ],
      "required_arguments": [
        "board_size_mm",
        "board_frame_flat_to_chassis",
        "camera_frame_chassis",
        "fpc_start_mm",
        "connector_center_mm",
        "fpc_free_length_mm",
      ],
      "optional_arguments": {
        "base_camera_frame": "A.context(generated=False) の最終 camera_mount_frame。make_planの通常設定参照値は代用不可",
        "camera_child_envelope_size_mm": [8.0, 9.56709, 9.10175],
        "camera_child_center_camera_mm": [0.0, -7.32913, 13.96086],
        "clearance_each_side_mm": 0.3,
        "wall_mm": 1.2,
        "short_rib_height_mm": 3.0,
        "rib_floor_overlap_mm": "wall_mm/3 by default; explicit positive value may be supplied",
        "floor_board_clearance_mm": 0.01,
        "holder_z_offset_mm": 0.0,
        "board_design_mass_g": 10.0,
        "camera_design_mass_g": 5.0,
      },
      "integration_contract": [
        "機械側は最終凍結後の実メッシュ包絡とフレームを渡し、返却された床/レール包絡を新holder候補へ変換する",
        "camera_frame_chassis と board_frame_flat_to_chassis は別入力として保持し、cameraローカルへ逆変換してから最終 base_camera_frame へ戻す。カメラを基板中心へ推定移動しない",
        "FPC点は基板と同じ inverse(camera_frame_chassis) で変換し、旧 chassis の絶対点を head+33 mm へ直接流用しない",
        "FPC入口の符号・曲率・実長が未確認なら status を UNVERIFIED のまま保持する",
        "xiao_all_boards と camera_child_lens は占有を各1式返し、without-SD/with-SD候補は比較用で本番数量へ二重計上しない",
        "床のY幅は基板の逃げに左右リブ壁厚を加え、リブ下端はwall_mm/3（既定）だけ床へ沈めて実体積を重ねる。リブ上端は変更しない",
        "holder_z_offset_mmはcameraローカル+Zへ適用するが、camera_child_lensは固定し、基板側FPC端だけを同じ量だけ移動する",
        "holder3 unionは3部品の一体化候補であり、STL再読時にwatertight/1solidと基板占有との交差体積0を検査する。3部品と同時に数量計上しない",
        "make_holder_meshes は spec の数値だけを使い、メーカー原本 STEP/STL の配布や読み込みを要求しない",
        "共有 config.py/body/assembly の変更と本APIの候補計算を混ぜない",
      ],
    },
    "source_files": [
      {"path": _rel(path), "exists": True, "sha256": _sha256(path)}
      for path in source_paths
    ],
    "source_policy": {
      "runtime_inputs": "summary JSON, candidate JSON and numeric API arguments only",
      "manufacturer_raw_files_required": False,
      "manufacturer_source_register": {
        "path": _rel(source_register_path),
        "sha256": _sha256(source_register_path),
        "use": "attribution and provenance only; raw STEP/STL remains excluded from public runtime",
      },
    },
    "limitations": [
      "公式STEPは2023年の旧カメラ世代の参考であり、既存候補XIAO/カメラ/FPCの型番・改版を証明しない",
      "camera_removed_* はカメラを除く包絡で、カメラは既存中央カメラフレームへ別配置する。全体のFPC経路は候補である",
      "MicroSD、USBプラグ、アンテナ、外部ヘッダ、はんだ、実FPC長の常設包絡は未確定",
      "今回の局所候補の geometry_pass は新しい頭・脚統合や実機適合の合格を意味しない",
    ],
  }
  return plan


def main(argv: Sequence[str] | None = None) -> int:
  args = list(argv if argv is not None else sys.argv[1:])
  def option_value(name: str) -> str | None:
    prefix = f"{name}="
    for index, argument in enumerate(args):
      if argument == name:
        if index + 1 >= len(args):
          raise ValueError(f"{name} requires a repository-relative path")
        return args[index + 1]
      if argument.startswith(prefix):
        return argument[len(prefix):]
    return None

  output_arg = option_value("--output")
  if output_arg is not None:
    output_candidate = Path(output_arg)
    if output_candidate.is_absolute() or ".." in output_candidate.parts:
      raise ValueError("--output must be a repository-relative path")
  output = _checked_repo_output(
    Path(output_arg) if output_arg is not None else DEFAULT_OUTPUT,
    "--output")
  output.parent.mkdir(parents=True, exist_ok=True)
  plan = make_plan()
  mesh_output_arg = option_value("--mesh-output")
  if mesh_output_arg is not None:
    mesh_candidate = Path(mesh_output_arg)
    if mesh_candidate.is_absolute() or ".." in mesh_candidate.parts:
      raise ValueError("--mesh-output must be a repository-relative path")
  mesh_output = _checked_repo_output(
    Path(mesh_output_arg) if mesh_output_arg is not None else DEFAULT_MESH_OUTPUT,
    "--mesh-output")
  if "--no-meshes" not in args:
    without_sd_spec = plan["retained_hardware"]["xiao_assembly_without_sd"]["holder_spec"]
    with_sd_spec = plan["retained_hardware"]["xiao_assembly_with_sd"]["holder_spec"]
    without_sd_meshes = make_holder_meshes(without_sd_spec)
    with_sd_meshes = make_holder_meshes(with_sd_spec)
    without_sd_rows = export_holder_meshes(
      without_sd_spec, mesh_output, name_prefix="without_sd_"
    )
    with_sd_rows = export_holder_meshes(
      with_sd_spec, mesh_output, name_prefix="with_sd_"
    )
    plan["holder_meshes"] = {
      "without_sd_candidate": without_sd_rows,
      "with_sd_candidate": with_sd_rows,
      "geometry_checks": {
        "without_sd_pre_serialization": inspect_holder_meshes(without_sd_meshes),
        "without_sd_serialized_roundtrip": inspect_serialized_holder_meshes(without_sd_rows),
        "with_sd_pre_serialization": inspect_holder_meshes(with_sd_meshes),
        "with_sd_serialized_roundtrip": inspect_serialized_holder_meshes(with_sd_rows),
      },
      "selection_rule": "comparison_only; choose one after measurement; never count both as production quantity",
    }
  else:
    plan["holder_meshes"] = {
      "status": "NOT_EMITTED",
      "selection_rule": "run without --no-meshes to emit candidate tray/rib/occupancy meshes",
    }
  output.write_text(
    json.dumps(plan, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
    encoding="utf-8")
  print(json.dumps({
    "status": plan["status"],
    "output": output.relative_to(ROOT).as_posix() if output.is_relative_to(ROOT) else str(output),
    "source_count": len(plan["source_files"]),
    "board_center_chassis_mm": plan["board_and_fpc_position"]["board_center_chassis_mm"],
    "fpc_reference_free_curve_length_mm": plan["board_and_fpc_position"]["fpc_reference"]["derived_free_curve_length_mm"],
    "mesh_output": mesh_output.relative_to(ROOT).as_posix() if mesh_output.is_relative_to(ROOT) else str(mesh_output),
    "mesh_sets": [k for k in plan["holder_meshes"] if k.endswith("_candidate")],
  }, ensure_ascii=False, indent=2))
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
