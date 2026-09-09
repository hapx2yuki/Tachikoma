#!/usr/bin/env python3
"""print-first候補を実メッシュから描画する。

動的な姿勢は古い self-collision の固定姿勢から読み込まない。既定値は
``export_print_first_native_trace.py`` が生成した flag 1 の有限traceであり、
``--pose-file`` と ``--pose-index`` で入力行を明示的に選べる。描画は
``print_first_assembly.context`` と ``sim_collision.parts_with_pad`` が返す
実形状だけを使うため、見た目を合わせるための補助形状は作らない。
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
import numpy as np
import trimesh

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "hardware/src")]
CONFIG_PATH = ROOT / "hardware/src/config.py"

import export_urdf as E
import print_first_assembly as A
from sim_collision import parts_with_pad
import sim_physics as S


EXPECTED_FLAG = "-DTACHIKOMA_PRINT_FIRST_PROFILE=1"
JOINT_ORDER = tuple(S.ALL_JOINTS)
DEFAULT_POSE_FILE = (
    ROOT
    / "outputs/print-first-20260905/final-simulation-native-trace-checker-current"
    / "native-trace/final_stand_pf1-native-trace.json"
)
DEFAULT_OUT = ROOT / "outputs/print-first-20260905/render"


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _validated_sha256(value: object, *, label: str) -> str:
    """Return a normalized SHA-256 string or reject an incomplete contract."""
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError(f"trace header {label} must be a 64-character SHA-256")
    try:
        int(value, 16)
    except ValueError as exc:
        raise ValueError(f"trace header {label} must be a hexadecimal SHA-256") from exc
    return value.lower()


def _public_path(path: Path) -> str:
    """絶対パスを成果物へ保存せず、リポジトリ相対名を返す。"""
    path = Path(path).resolve()
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        digest = _sha256(path)[:16] if path.is_file() else hashlib.sha256(
            str(path).encode("utf-8")
        ).hexdigest()[:16]
        return f"$EXTERNAL/{path.name}#{digest}"


def _trace_header_contract(raw: object, *, freeze_manifest: Path | None = None) -> dict:
    """Validate trace provenance against the current source configuration.

    A native trace contains angles emitted by a compiled header.  Reusing a
    trace generated before ``config.py`` changed can otherwise produce a
    plausible-looking image with the wrong geometry/profile.  The current
    config hash is therefore mandatory.  A final freeze manifest can be
    supplied by the caller; in that case the trace must carry the manifest
    hash as well.
    """
    if not isinstance(raw, dict):
        raise ValueError("--pose-file must be a native flag-1 trace JSON object")
    header = raw.get("header")
    if not isinstance(header, dict):
        raise ValueError("--pose-file header is required for provenance validation")

    trace_config_sha = _validated_sha256(
        header.get("source_config_sha256"), label="source_config_sha256"
    )
    current_config_sha = _sha256(CONFIG_PATH)
    if trace_config_sha != current_config_sha:
        raise ValueError(
            "stale pose trace: header.source_config_sha256 does not match the "
            f"current hardware/src/config.py ({trace_config_sha} != {current_config_sha})"
        )

    trace_freeze_sha = header.get("freeze_manifest_sha256")
    if trace_freeze_sha is not None:
        trace_freeze_sha = _validated_sha256(
            trace_freeze_sha, label="freeze_manifest_sha256"
        )

    freeze_meta = None
    if freeze_manifest is not None:
        freeze_path = Path(freeze_manifest).resolve()
        if not freeze_path.is_file():
            raise FileNotFoundError(f"freeze manifest does not exist: {freeze_path}")
        expected_freeze_sha = _sha256(freeze_path)
        if trace_freeze_sha is None:
            raise ValueError(
                "pose trace is missing header.freeze_manifest_sha256 for the "
                "requested freeze manifest"
            )
        if trace_freeze_sha != expected_freeze_sha:
            raise ValueError(
                "stale pose trace: header.freeze_manifest_sha256 does not match "
                f"the requested freeze manifest ({trace_freeze_sha} != {expected_freeze_sha})"
            )
        freeze_meta = {
            "path": _public_path(freeze_path),
            "sha256": expected_freeze_sha,
        }

    return {
        "header_source_config_sha256": trace_config_sha,
        "current_config_sha256": current_config_sha,
        "freeze_manifest": freeze_meta,
    }


def _pose_rows(raw: object, *, freeze_manifest: Path | None = None) -> tuple[list[dict], str, dict]:
    """flag 1 traceの行を取得する。

    ``rows`` を正規形とし、旧監査の ``poses`` は入力として受けない。
    これにより、古い self-collision 姿勢を暗黙に描画へ流用しない。
    """
    trace_contract = _trace_header_contract(raw, freeze_manifest=freeze_manifest)
    if raw.get("compile_flag") != EXPECTED_FLAG:
        raise ValueError(
            "--pose-file must contain compile_flag "
            f"{EXPECTED_FLAG!r}; old or unmarked pose files are rejected"
        )
    if raw.get("profile_mode") not in ("candidate_print_first", "frozen_print_first"):
        raise ValueError(
            "--pose-file profile_mode must be candidate_print_first or "
            "frozen_print_first"
        )
    if tuple(raw.get("joint_order") or ()) != JOINT_ORDER:
        raise ValueError("--pose-file joint_order does not match the 20-axis flag-1 order")
    header = raw["header"]
    header_sha = header.get("sha256")
    if (not isinstance(header_sha, str) or len(header_sha) != 64
            or header_sha != header_sha.lower()
            or any(char not in "0123456789abcdef" for char in header_sha)):
        raise ValueError("--pose-file header.sha256 must be a lowercase hexadecimal SHA-256")
    rows = raw.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ValueError("--pose-file has no native trace rows")
    declared_count = raw.get("row_count")
    if declared_count != len(rows):
        raise ValueError(
            f"--pose-file row_count {declared_count!r} does not match rows {len(rows)}"
        )
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError("--pose-file rows must be objects")
    for index, row in enumerate(rows):
        for field in ("moving", "ready"):
            if type(row.get(field)) is not bool:
                raise ValueError(f"--pose-file row {index}.{field} must be boolean")
        phase = row.get("phase")
        if isinstance(phase, bool):
            raise ValueError(f"--pose-file row {index}.phase must be finite numeric")
        try:
            phase = float(phase)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"--pose-file row {index}.phase must be finite numeric") from exc
        if not np.isfinite(phase):
            raise ValueError(f"--pose-file row {index}.phase must be finite numeric")
        enabled = row.get("enabled")
        if not isinstance(enabled, dict) or set(enabled) != set(JOINT_ORDER):
            raise ValueError(f"--pose-file row {index}.enabled keys do not match the 20-axis order")
        if any(type(enabled[name]) is not bool for name in JOINT_ORDER):
            raise ValueError(f"--pose-file row {index}.enabled values must be boolean")
    return rows, "native_trace_rows", trace_contract


def _load_pose(
    path: Path, index: int, *, freeze_manifest: Path | None = None
) -> tuple[dict[str, float], dict]:
    """traceから指定行を検査して角度辞書と出典情報を返す。"""
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"pose-file does not exist: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    rows, row_kind, trace_contract = _pose_rows(
        raw, freeze_manifest=freeze_manifest
    )
    if index < 0 or index >= len(rows):
        raise IndexError(f"pose-index {index} is outside 0..{len(rows) - 1}")
    row = rows[index]
    values = row.get("angles_deg")
    if not isinstance(values, dict) or set(values) != set(JOINT_ORDER):
        missing = sorted(set(JOINT_ORDER) - set(values or {}))
        extra = sorted(set(values or {}) - set(JOINT_ORDER))
        raise ValueError(
            f"pose row angles_deg keys mismatch; missing={missing}, extra={extra}"
        )
    angles = {}
    for name in JOINT_ORDER:
        try:
            value = float(values[name])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"pose angle {name} is not numeric") from exc
        if not np.isfinite(value):
            raise ValueError(f"pose angle {name} is not finite")
        angles[name] = value
    return angles, {
        "path": _public_path(path),
        "sha256": _sha256(path),
        "row_kind": row_kind,
        "row_count": len(rows),
        "pose_index": index,
        "trace_case_name": raw.get("case_name"),
        "profile_mode": raw.get("profile_mode"),
        "compile_flag": raw.get("compile_flag"),
        "trace_contract": trace_contract,
        "source_config_sha256": raw["header"]["source_config_sha256"],
        "header_sha256": raw["header"]["sha256"],
        "row": {
            "time_s": row.get("time_s"),
            "segment": row.get("segment"),
            "ready": row.get("ready"),
            "moving": row.get("moving"),
        },
    }


def _rgba(value: object) -> np.ndarray:
    if isinstance(value, str):
        return np.asarray(trimesh.visual.color.hex_to_rgba(value), dtype=np.uint8)
    raw = np.asarray(value, dtype=np.uint8).reshape(-1)
    if raw.size == 3:
        return np.r_[raw, 255].astype(np.uint8)
    if raw.size == 4:
        return raw
    raise ValueError(f"unsupported mesh color: {value!r}")


def _display_mesh(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """描画用に面数だけを抑える。形状を補う処理は行わない。"""
    if len(mesh.faces) <= 1700:
        return mesh
    try:
        reduced = mesh.simplify_quadric_decimation(face_count=1700)
        if isinstance(reduced, trimesh.Trimesh) and len(reduced.faces) > 0:
            return reduced
    except Exception:
        # Rendering must remain possible when an optional decimator is absent;
        # the original mesh is still the only geometry used.
        pass
    return mesh


def _collect_meshes(angles: dict[str, float], *, include_components: bool = True):
    """print-first contextから、base座標へ移した描画対象を作る。"""
    rendered = []
    with A.context():
        parts = parts_with_pad(True, include_components=include_components)
        for link, items in parts.items():
            if link not in E.LINK_PARENT_FRAME:
                raise ValueError(f"unknown link in print-first geometry: {link}")
            frame = E.LINK_PARENT_FRAME[link](angles)
            for mesh, color, name in items:
                if not isinstance(mesh, trimesh.Trimesh):
                    raise TypeError(f"{link}/{name} is not a Trimesh")
                moved = _display_mesh(mesh.copy())
                moved.apply_transform(frame)
                moved.visual.face_colors = _rgba(color)
                rendered.append((moved, color, f"{link}/{name}"))
    if not rendered:
        raise ValueError("print-first geometry collection is empty")
    return rendered


def _limits(bounds: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    low = bounds[:, 0, :].min(axis=0)
    high = bounds[:, 1, :].max(axis=0)
    extent = high - low
    pad = np.maximum(extent * 0.035, 2.0)
    return low - pad, high + pad, extent + 2.0 * pad


def _draw_panel(ax, rendered, low, high, extent, *, elev, azim, title):
    ax.set_facecolor("#f3f5f7")
    for mesh, _color, _name in rendered:
        coll = Poly3DCollection(
            mesh.triangles,
            facecolors=_color,
            edgecolors="none",
            linewidth=0,
            alpha=1.0,
        )
        ax.add_collection3d(coll)
    ax.set_xlim(float(low[0]), float(high[0]))
    ax.set_ylim(float(low[1]), float(high[1]))
    ax.set_zlim(float(low[2]), float(high[2]))
    ax.set_box_aspect(tuple(float(x) for x in extent))
    ax.view_init(elev=elev, azim=azim)
    ax.set_proj_type("ortho")
    ax.set_axis_off()
    ax.set_title(title, pad=2)


def render(
    out: Path,
    pose_file: Path,
    pose_index: int,
    *,
    include_components: bool = True,
    freeze_manifest: Path | None = None,
):
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    angles, pose_meta = _load_pose(
        pose_file, pose_index, freeze_manifest=freeze_manifest
    )
    rendered = _collect_meshes(angles, include_components=include_components)
    bounds = np.asarray([mesh.bounds for mesh, _color, _name in rendered], dtype=float)
    low, high, extent = _limits(bounds)

    fig = plt.figure(figsize=(13.2, 6.8), facecolor="#f3f5f7")
    _draw_panel(
        fig.add_subplot(1, 2, 1, projection="3d"),
        rendered,
        low,
        high,
        extent,
        elev=24,
        azim=60,
        title="Full assembly | flag-1 trace",
    )
    _draw_panel(
        fig.add_subplot(1, 2, 2, projection="3d"),
        rendered,
        low,
        high,
        extent,
        elev=7,
        azim=0,
        title="Side elevation | flag-1 trace",
    )
    fig.subplots_adjust(left=0.0, right=1.0, bottom=0.0, top=0.96, wspace=0.0)
    preview = out / "assembly-preview.png"
    fig.savefig(preview, dpi=150, bbox_inches="tight", pad_inches=0.04)
    plt.close(fig)

    # Keep a separately addressable side view for docs and review comments.
    side_fig = plt.figure(figsize=(7.0, 6.8), facecolor="#f3f5f7")
    _draw_panel(
        side_fig.add_subplot(1, 1, 1, projection="3d"),
        rendered,
        low,
        high,
        extent,
        elev=7,
        azim=0,
        title="Side elevation | flag-1 trace",
    )
    side_fig.subplots_adjust(left=0.0, right=1.0, bottom=0.0, top=0.96)
    side_preview = out / "assembly-preview-side.png"
    side_fig.savefig(side_preview, dpi=150, bbox_inches="tight", pad_inches=0.04)
    plt.close(side_fig)

    scene = trimesh.Scene()
    for mesh, _color, name in rendered:
        scene.add_geometry(mesh, node_name=name)
    glb = out / "assembly-preview.glb"
    scene.export(glb)

    stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )
    metadata = {
        "schema_version": 1,
        "status": "RENDERED_PRINT_FIRST_TRACE_GEOMETRY",
        "created_utc": stamp,
        "pose": pose_meta,
        "pose_angles_deg": angles,
        "components_included": bool(include_components),
        "mesh_count": len(rendered),
        "mesh_names": [name for _mesh, _color, name in rendered],
        "bounds_mm": {"min": low.tolist(), "max": high.tolist()},
        "source": {
            "context": "print_first_assembly.context(generated=True)",
            "parts": "sim_collision.parts_with_pad(include_servos=True)",
            "config_sha256": _sha256(CONFIG_PATH),
            "native_trace_sha256": pose_meta["sha256"],
            "native_trace_source_config_sha256": pose_meta["source_config_sha256"],
            "native_trace_header_sha256": pose_meta["header_sha256"],
            "assembly_helper_sha256": _sha256(ROOT / "tools/print_first_assembly.py"),
            "renderer_sha256": _sha256(Path(__file__).resolve()),
            "geometry_policy": "source meshes only; no fabricated silhouette or filler geometry",
        },
        "outputs": {
            "full_side_png": _public_path(preview),
            "side_png": _public_path(side_preview),
            "scene_glb": _public_path(glb),
        },
        "interpretation": (
            "候補traceの1行を使った現在メッシュの図。候補profileの採用、"
            "実機適合、実歩行、印刷強度、freeze2完了を意味しない。"
        ),
    }
    metadata_path = out / "assembly-preview.json"
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return preview, side_preview, glb, metadata_path, pose_meta, len(rendered)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pose-file",
        type=Path,
        default=DEFAULT_POSE_FILE,
        help="flag-1 native trace JSON（既定: final_stand_pf1）",
    )
    parser.add_argument(
        "--pose-index",
        type=int,
        default=0,
        help="trace rows の姿勢番号（既定: 0）",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help="画像・GLB・メタデータの出力先",
    )
    parser.add_argument(
        "--freeze-manifest",
        type=Path,
        default=None,
        help="最終凍結台帳（指定時はtrace headerのSHAも突合する）",
    )
    parser.add_argument(
        "--without-components",
        action="store_true",
        help="電池・マイク・スピーカー等の占有候補を除外する比較用指定",
    )
    args = parser.parse_args()
    try:
        result = render(
            args.out,
            args.pose_file,
            args.pose_index,
            include_components=not args.without_components,
            freeze_manifest=args.freeze_manifest,
        )
    except Exception as exc:  # noqa: BLE001 - command should report the cause
        parser.error(str(exc))
        return 2
    preview, side_preview, glb, meta, pose_meta, count = result
    print(
        json.dumps(
            {
                "status": "RENDERED_PRINT_FIRST_TRACE_GEOMETRY",
                "pose": pose_meta,
                "mesh_count": count,
                "full_side_png": _public_path(preview),
                "side_png": _public_path(side_preview),
                "scene_glb": _public_path(glb),
                "metadata": _public_path(meta),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
