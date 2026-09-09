#!/usr/bin/env python3
"""印刷優先構成の固定部品と有限C++姿勢を実メッシュで検査する。

既定の ``final`` では、完全な production native trace を ``--pose-file`` で
明示し、その全行を固定部品と動的交差へ渡す。固定部品だけ、または短縮traceの
比較は ``--mode development`` を明示した診断として保存する。
旧 ``self-collision-with-servos.json`` は既定入力に使わない。

この検査の合格は実機の適合・強度・電源・歩行成立を意味しない。実メッシュの
Boolean交差、生成台帳、ヘッダー/設定の鮮度を別々に記録し、エラーまたは
未確認条件があれば終了コード1を返す。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

import numpy as np
import trimesh

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "hardware" / "src")]

import export_urdf as E  # noqa: E402
import print_first_assembly as A  # noqa: E402
import sim_collision as SC  # noqa: E402
import sim_physics as SP  # noqa: E402
import sim_print_first as P  # noqa: E402
import check_print_first_t0_mesh as T0  # noqa: E402
import check_print_first_native_trace as NT  # noqa: E402


EXPECTED_FLAG = "-DTACHIKOMA_PRINT_FIRST_PROFILE=1"
JOINT_ORDER = tuple(SP.ALL_JOINTS)
DEFAULT_CASE = ROOT / "outputs/print-first-20260905/final-simulation/cases/initial-finite-cases.json"
DEFAULT_OUTPUT = ROOT / "outputs/print-first-20260905/check_print_first_body.json"
DEFAULT_ASSEMBLIES = (
    ROOT / "outputs/print-first-20260905/body/assembly.json",
    ROOT / "outputs/print-first-20260905/legs/assembly.json",
    ROOT / "outputs/print-first-20260905/feet/assembly.json",
)


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _public(path: Path, output_root: Path | None = None) -> str:
    return P.public_path(Path(path), output_root)


def _publicize(value, output_root: Path):
    """Convert nested execution values to reproducible report values.

    ``sim_print_first`` reports sometimes embed a path inside explanatory text
    (for example ``freeze manifest row: /...``), which a Path-only conversion
    cannot sanitize.  Strip the repository prefix there as well.
    """
    if isinstance(value, dict):
        return {key: _publicize(item, output_root) for key, item in value.items()}
    if isinstance(value, list):
        return [_publicize(item, output_root) for item in value]
    if isinstance(value, tuple):
        return [_publicize(item, output_root) for item in value]
    if isinstance(value, Path):
        return _public(value, output_root)
    if isinstance(value, str):
        prefix = str(ROOT.resolve()) + "/"
        if value.startswith("/"):
            try:
                return _public(Path(value), output_root)
            except (OSError, ValueError):
                pass
        return value.replace(prefix, "")
    return value


def _error_text(error: object) -> str:
    """エラーへ個人の絶対作業パスを残さない。"""
    text = str(error)
    root = str(ROOT.resolve())
    return text.replace(root + "/", "").replace(root, "<ROOT>")


def _file_record(path: Path, role: str, output_root: Path | None = None) -> dict:
    path = Path(path).resolve()
    row = {"role": role, "path": _public(path, output_root), "exists": path.is_file()}
    row["sha256"] = _sha(path) if path.is_file() else None
    return row


def _case_from_file(path: Path, case_name: str | None) -> tuple[dict, Path]:
    path = Path(path).resolve()
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        if case_name:
            choices = [item for item in data
                       if isinstance(item, dict) and item.get("name") == case_name]
            if not choices:
                raise ValueError(f"case not found: {case_name}")
            return choices[0], path
        if not data or not isinstance(data[0], dict):
            raise ValueError("case JSON has no object case")
        return data[0], path
    if not isinstance(data, dict):
        raise ValueError("case JSON must be an object or an array of objects")
    if case_name and data.get("name") != case_name:
        raise ValueError(f"case name mismatch: {case_name}")
    return data, path


def _normalise_profile(profile: dict | None) -> dict:
    if not isinstance(profile, dict):
        return {}
    off = profile.get("stance_off_xy")
    if off is not None:
        if len(off) != 2:
            return dict(profile)
        off_x, off_y = off
    else:
        off_x, off_y = profile.get("stance_off_x", 0.0), profile.get("stance_off_y")
    result = dict(profile)
    result["stance_off_x"] = off_x
    result["stance_off_y"] = off_y
    result.pop("stance_off_xy", None)
    return result


def _config_profile() -> dict:
    cfg = E.C.PRINT_FIRST_GAIT
    off = cfg["stance_off_xy"]
    return {
        "body_h": float(cfg["body_h"]),
        "stance_r": float(cfg["stance_r"]),
        "stance_off_x": float(off[0]),
        "stance_off_y": float(off[1]),
        "step_h": float(cfg["step_h"]),
        "max_step": float(cfg["max_step"]),
        "max_turn_deg": float(cfg["max_turn_deg"]),
        "cycle_t": float(cfg["cycle_t"]),
        "duty": float(cfg["duty"]),
        "sway_mm": [float(v) for v in cfg["sway_mm"]],
        "sway_lead": float(cfg["sway_lead"]),
        "phase_off": [float(v) for v in cfg["phase_off"]],
        "arm_swing_deg": float(cfg["arm_swing_deg"]),
        "hip_r": float(cfg["hip_r"]),
        "path_shape": str(cfg.get("path_shape", "linear")),
        "profile_status": str(cfg["status"]),
        "profile_adopted": bool(cfg.get("adopted", False)),
    }


def _same_value(actual, expected) -> bool:
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        return math.isclose(float(actual), float(expected), rel_tol=1e-7, abs_tol=1e-7)
    if isinstance(actual, (list, tuple)) and isinstance(expected, (list, tuple)):
        return len(actual) == len(expected) and all(
            _same_value(a, e) for a, e in zip(actual, expected))
    return actual == expected


def _profile_mismatches(actual: dict | None, expected: dict | None) -> list[dict]:
    actual = _normalise_profile(actual)
    expected = _normalise_profile(expected)
    keys = (
        "body_h", "stance_r", "stance_off_x", "stance_off_y", "step_h",
        "max_step", "max_turn_deg", "cycle_t", "duty", "sway_mm",
        "sway_lead", "phase_off", "arm_swing_deg", "hip_r", "path_shape",
        "profile_status", "profile_adopted",
    )
    result = []
    for key in keys:
        if key not in actual:
            result.append({"key": key, "expected": expected.get(key), "actual": None})
        elif key not in expected or not _same_value(actual[key], expected[key]):
            result.append({"key": key, "expected": expected.get(key), "actual": actual[key]})
    return result


def _resolve_trace_ref(value: object, trace_path: Path, trace_root: Path) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    if value.startswith("$OUTPUT/"):
        return (trace_root / value[len("$OUTPUT/"):]).resolve()
    if value.startswith("$EXTERNAL/"):
        # The public key is intentionally not reversible; report it missing.
        return None
    return P.resolve_manifest_path(value, base=trace_path.parent)


def _case_output_root(case_path: Path) -> Path:
    """publicize時の ``$OUTPUT`` をケース位置から再現する。"""
    case_path = Path(case_path).resolve()
    return case_path.parent.parent if case_path.parent.name == "cases" else case_path.parent


def _resolve_case_output_refs(value: object, output_root: Path) -> object:
    """ケースJSON内の公開用出力キーだけを内部絶対パスへ戻す。"""
    if isinstance(value, dict):
        return {
            key: _resolve_case_output_refs(item, output_root)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_resolve_case_output_refs(item, output_root) for item in value]
    if isinstance(value, str) and value.startswith("$OUTPUT/"):
        return str((output_root / value[len("$OUTPUT/"):]).resolve())
    return value


def _trace_root(path: Path) -> Path:
    path = Path(path).resolve()
    return path.parent.parent if path.parent.name == "native-trace" else path.parent


def _pose_angles(row: dict, joint_order: tuple[str, ...]) -> dict[str, float]:
    values = row.get("angles_deg")
    if isinstance(values, dict):
        if set(values) != set(joint_order):
            missing = sorted(set(joint_order) - set(values))
            extra = sorted(set(values) - set(joint_order))
            raise ValueError(f"pose row joint keys mismatch; missing={missing}, extra={extra}")
        raw = [values[name] for name in joint_order]
    elif isinstance(values, (list, tuple)):
        if len(values) != len(joint_order):
            raise ValueError(f"pose row has {len(values)} angles; expected {len(joint_order)}")
        raw = values
    else:
        raise ValueError("pose row angles_deg is missing or not an object/list")
    angles = []
    for value in raw:
        try:
            value = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"pose angle is not numeric: {value!r}") from exc
        if not math.isfinite(value):
            raise ValueError("pose angle is not finite")
        angles.append(value)
    return dict(zip(joint_order, angles))


def _load_pose_file(path: Path, case: dict, mode: str) -> tuple[dict, list[tuple[int, dict, dict]]]:
    path = Path(path).resolve()
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("pose-file must be the native trace JSON object")
    errors = []
    native_trace_contract = None
    if mode == "final":
        # The body checker consumes every row for dynamic geometry, so its
        # final input must also satisfy the native-trace checker’s exact
        # production sequence.  This closes the former gap where a frozen
        # profile with a shortened/manual row list could pass as final.
        native_trace_contract = NT._trace_contract(raw, path, mode="production")
        if native_trace_contract.get("status") != "PASS":
            errors.append({
                "kind": "final_requires_complete_native_trace",
                "status": native_trace_contract.get("status"),
                "expected": "complete production STANDARD_SEGMENT_ORDER and provenance",
                "errors": native_trace_contract.get("errors", []),
            })
    trace_mode = raw.get("profile_mode")
    if trace_mode not in ("candidate_print_first", "frozen_print_first"):
        errors.append({"kind": "profile_mode", "actual": trace_mode,
                       "expected": ["candidate_print_first", "frozen_print_first"]})
    flag = raw.get("compile_flag") or raw.get("compile_flag_required")
    if flag != EXPECTED_FLAG:
        errors.append({"kind": "compile_flag", "actual": flag, "expected": EXPECTED_FLAG})
    if mode == "final" and trace_mode != "frozen_print_first":
        errors.append({"kind": "final_requires_frozen_profile_mode", "actual": trace_mode})
    if mode == "development" and trace_mode != "candidate_print_first":
        errors.append({"kind": "development_requires_explicit_candidate_mode", "actual": trace_mode})

    order = raw.get("joint_order")
    if tuple(order or ()) != JOINT_ORDER:
        errors.append({"kind": "joint_order", "actual": order, "expected": list(JOINT_ORDER)})

    profile = raw.get("profile")
    case_mismatch = []
    config_mismatch = []
    if not isinstance(profile, dict):
        errors.append({"kind": "profile", "actual": "missing"})
    else:
        case_mismatch = _profile_mismatches(profile, case.get("profile"))
        config_mismatch = _profile_mismatches(profile, _config_profile())
        if case_mismatch:
            errors.append({"kind": "trace_profile_vs_case", "mismatches": case_mismatch})
        if mode == "final" and config_mismatch:
            errors.append({"kind": "trace_profile_vs_config_py", "mismatches": config_mismatch})

    trace_root = _trace_root(path)
    companion = {}
    for key in ("header", "build", "csv"):
        info = raw.get(key)
        if not isinstance(info, dict):
            errors.append({"kind": f"{key}_metadata", "actual": "missing"})
            continue
        ref = _resolve_trace_ref(info.get("path"), path, trace_root)
        companion[key] = (_file_record(ref, f"trace_{key}", trace_root) if ref else {
            "role": f"trace_{key}", "path": info.get("path"), "exists": False,
            "sha256": None})
        if ref is None or not ref.is_file():
            errors.append({"kind": f"{key}_missing", "path": info.get("path")})
            continue
        recorded = info.get("sha256")
        actual = _sha(ref)
        if recorded != actual:
            errors.append({"kind": f"{key}_sha256", "path": info.get("path"),
                           "expected": recorded, "actual": actual})
        if key == "build":
            try:
                build = json.loads(ref.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                errors.append({"kind": "build_json", "error": _error_text(exc)})
            else:
                build_flag = build.get("print_first_compile_flag")
                command = " ".join(str(value) for value in build.get("command", []))
                if build_flag != EXPECTED_FLAG or EXPECTED_FLAG not in command:
                    errors.append({"kind": "build_compile_flag", "actual": build_flag,
                                   "command_contains_flag": EXPECTED_FLAG in command})
                if build.get("profile_mode") != trace_mode:
                    errors.append({"kind": "build_profile_mode",
                                   "actual": build.get("profile_mode"), "expected": trace_mode})
        if key == "header" and isinstance(profile, dict):
            try:
                P._check_profile_header(profile, ref, mode=str(trace_mode))
            except Exception as exc:  # noqa: BLE001
                errors.append({"kind": "header_contract", "error": _error_text(exc)})
            header_source = info.get("source_config_sha256")
            config_sha = _sha(ROOT / "hardware/src/config.py")
            if header_source != config_sha:
                errors.append({"kind": "header_source_config_sha256",
                               "expected": config_sha, "actual": header_source})

    rows = raw.get("rows")
    if not isinstance(rows, list) or not rows:
        errors.append({"kind": "rows", "actual": "missing_or_empty"})
        rows = []
    expected_count = raw.get("row_count")
    if expected_count != len(rows):
        errors.append({"kind": "row_count", "expected": expected_count, "actual": len(rows)})
    parsed = []
    previous_time = None
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            errors.append({"kind": "row_object", "index": index})
            continue
        try:
            angles = _pose_angles(row, JOINT_ORDER)
        except ValueError as exc:
            errors.append({"kind": "row_angles", "index": index, "error": _error_text(exc)})
            continue
        numeric = [row.get("time_s"), row.get("phase")]
        command = row.get("command")
        if not isinstance(command, dict):
            errors.append({"kind": "row_command", "index": index})
        else:
            numeric.extend(command.get(key) for key in ("vx", "vy", "wz", "body_h_mm"))
        if any(not isinstance(value, (int, float)) or not math.isfinite(float(value))
               for value in numeric):
            errors.append({"kind": "row_nonfinite", "index": index})
        else:
            time_s = float(row["time_s"])
            phase = float(row["phase"])
            if previous_time is not None and time_s < previous_time - 1e-9:
                errors.append({"kind": "row_time_not_monotonic", "index": index})
            previous_time = time_s
            if phase < -1e-6 or phase >= 1.0 + 1e-6:
                errors.append({"kind": "row_phase_out_of_range", "index": index,
                               "phase": phase})
        enabled = row.get("enabled")
        if enabled is not None and (not isinstance(enabled, dict) or
                                    set(enabled) != set(JOINT_ORDER)):
            errors.append({"kind": "row_enabled_joint_keys", "index": index})
        parsed.append((index, row, angles))

    input_report = {
        "path": _public(path),
        "sha256": _sha(path),
        "profile_mode": trace_mode,
        "compile_flag": flag,
        "joint_order": list(JOINT_ORDER),
        "row_count": len(rows),
        "companion_files": companion,
        "profile": profile,
        "native_trace_contract": (
            None if native_trace_contract is None else {
                key: value for key, value in native_trace_contract.items()
                if not key.startswith("_")
            }
        ),
        "errors": errors,
        "valid": not errors,
    }
    return input_report, parsed


def _assembly_inputs(case: dict, output_root: Path | None) -> dict:
    options = case.get("model", {}) if isinstance(case, dict) else {}
    role_by_key = {
        "assembly_manifest": "body", "body_manifest": "body",
        "feet_manifest": "feet", "foot_assembly": "feet",
        "foot_contact_reference": "feet", "leg_manifest": "legs",
        "geometry_manifest": "body",
    }
    entries = []
    for key in ("assembly_manifest", "body_manifest", "feet_manifest", "leg_manifest",
                "foot_assembly", "foot_contact_reference", "geometry_manifest"):
        value = options.get(key) or case.get(key)
        if value:
            entries.append({"role": role_by_key[key], "path": value})
    if not entries:
        entries = [{"role": role, "path": path} for role, path in zip(
            ("body", "legs", "feet"), DEFAULT_ASSEMBLIES)]
    files = []
    seen = set()
    manifests = []
    entry_seen = set()
    for entry in entries:
        role = entry["role"]
        path = P.resolve_path(entry["path"])
        marker = (role, str(path))
        if marker not in entry_seen:
            entry_seen.add(marker)
            manifests.append({"role": role, "path": path})
        if path not in seen:
            seen.add(path)
            files.append(_file_record(path, f"{role}_manifest", output_root))
        for referenced in P._manifest_path_values(path):
            referenced = Path(referenced).resolve()
            if referenced in seen or not referenced.is_file():
                continue
            seen.add(referenced)
            file_role = f"{role}_manifest" if referenced.suffix.lower() == ".json" else f"{role}_stl"
            files.append(_file_record(referenced, file_role, output_root))
    generation = P.print_first_assembly_generation_contract(manifests)
    return {"files": files, "manifests": manifests, "generation_contract": generation}


def _model_inputs(case: dict, output_root: Path | None) -> dict:
    options = case.get("model", {}) if isinstance(case, dict) else {}
    raw = options.get("model_path") or options.get("model_urdf") or options.get("urdf_path")
    model = P.resolve_path(raw or ROOT / "hardware/urdf-print-first/tachikoma.urdf")
    result = _file_record(model, "final_urdf", output_root)
    result["valid_urdf"] = False
    result["geometry_errors"] = []
    meshes = []
    if model.is_file():
        try:
            root = ET.parse(model).getroot()
        except (ET.ParseError, OSError) as exc:
            result["parse_error"] = _error_text(exc)
        else:
            if root.tag != "robot":
                result["parse_error"] = f"URDF root must be robot, got {root.tag!r}"
            elif not root.findall(".//link"):
                result["parse_error"] = "URDF has no link elements"
            elif not root.findall(".//mesh"):
                result["parse_error"] = "URDF has no mesh elements"
            else:
                result["valid_urdf"] = True
            seen = set()
            mesh_refs = []
            for link in root.findall(".//link"):
                for section_name in ("collision", "visual"):
                    for item in link.findall(f"./{section_name}//mesh"):
                        mesh_refs.append((item, section_name))
            # Keep unusual but valid URDF mesh placements visible in the
            # manifest; they cannot silently disappear from the SHA record.
            known_ids = {id(item) for item, _section in mesh_refs}
            mesh_refs.extend((item, "unknown") for item in root.findall(".//mesh")
                             if id(item) not in known_ids)
            mesh_uses = {}
            mesh_records = {}
            for item, section_name in mesh_refs:
                filename = item.get("filename")
                if not filename:
                    result["geometry_errors"].append({
                        "kind": "mesh_filename_missing",
                    })
                    continue
                path = P.resolve_manifest_path(filename, base=model.parent)
                mesh_uses.setdefault(path, set()).add(section_name)
                if path in seen:
                    continue
                seen.add(path)
                record = _file_record(path, "final_urdf_mesh", output_root)
                meshes.append(record)
                mesh_records[path] = record
            for path, uses in mesh_uses.items():
                if path in mesh_records:
                    mesh_records[path]["uses"] = sorted(uses)
                if "collision" not in uses or not path.is_file():
                    continue
                try:
                    mesh = trimesh.load(path, force="mesh")
                    T0._strict_native(mesh, f"final_urdf_mesh/{path.name}")
                except Exception as exc:  # noqa: BLE001 - fail closed on bad mesh
                    result["geometry_errors"].append({
                        "kind": "invalid_mesh", "path": mesh_records[path]["path"],
                        "error": _error_text(exc),
                    })
    result["referenced_meshes"] = meshes
    result["all_referenced_meshes_exist"] = all(item["exists"] for item in meshes)
    result["all_referenced_meshes_valid"] = not result["geometry_errors"]
    return result


def _fixed_rows(include_components: bool) -> list[tuple[object, str, str]]:
    with A.context():
        parts = SC.parts_with_pad(True, include_components=include_components)
        rows = []
        for link, items in parts.items():
            for mesh, _color, name in items:
                # Validate every source mesh, including non-overlapping ones;
                # otherwise an invalid part could escape the AABB broad phase.
                T0._strict_native(mesh, f"{link}/{name}")
                rows.append((mesh.copy(), name, link))
        return rows


def _dynamic_rows(local_rows, pose: dict[str, float]):
    rows = []
    with A.context():
        for mesh, name, link in local_rows:
            frame_fn = E.LINK_PARENT_FRAME.get(link)
            if frame_fn is None:
                raise ValueError(f"parent frame missing: {link}")
            moved = mesh.copy()
            moved.apply_transform(frame_fn(pose))
            rows.append((moved, name, link))
    return rows


def _native_rows(local_rows):
    """Validate each local source mesh once and retain its Manifold form."""
    rows = []
    for mesh, name, link in local_rows:
        native = T0._strict_native(mesh, f"{link}/{name}")
        rows.append((mesh, name, link, native))
    return rows


def _native_transform(native, matrix):
    """Apply a 4x4 rigid matrix using manifold3d's Double3x4 input."""
    matrix = np.asarray(matrix, dtype=float)
    if matrix.shape != (4, 4) or not np.isfinite(matrix).all():
        raise ValueError(f"invalid rigid transform shape/values: {matrix.shape}")
    return native.transform(matrix[:3, :4].tolist())


def _dynamic_native_rows(local_rows, pose: dict[str, float]):
    """Transform both AABB meshes and cached native meshes for one pose."""
    rows = []
    with A.context():
        for mesh, name, link, native in local_rows:
            frame_fn = E.LINK_PARENT_FRAME.get(link)
            if frame_fn is None:
                raise ValueError(f"parent frame missing: {link}")
            frame = frame_fn(pose)
            moved = mesh.copy()
            moved.apply_transform(frame)
            rows.append((moved, name, link, _native_transform(native, frame)))
    return rows


def _check_native_pairs(rows, *, same_link: bool):
    """Run the strict Boolean pair check using cached local Manifolds."""
    hits = []
    errors = []
    aabb_pairs = 0
    for i, (a, aname, alink, anative) in enumerate(rows):
        for j in range(i):
            b, bname, blink, bnative = rows[j]
            if (alink == blink) != same_link:
                continue
            if T0._aabb_disjoint(a, b):
                continue
            aabb_pairs += 1
            try:
                value, small_negative = T0._intersection(
                    anative, bnative, f"{alink}/{aname} x {blink}/{bname}"
                )
                if value > T0.INTERSECTION_THRESHOLD_MM3:
                    hits.append({
                        "parts": [bname, aname],
                        "links": [blink, alink],
                        "volume_mm3": value,
                        "raw_negative_within_tolerance": small_negative,
                        "bounds_a_mm": a.bounds.tolist(),
                        "bounds_b_mm": b.bounds.tolist(),
                    })
            except Exception as exc:  # noqa: BLE001 - fail closed per pair
                errors.append({
                    "parts": [bname, aname], "links": [blink, alink],
                    "error": str(exc),
                })
    hits.sort(key=lambda item: -item["volume_mm3"])
    return {"aabb_pairs": aabb_pairs, "intersections": hits, "errors": errors}


def _check(case: dict, case_path: Path, *, pose_path: Path | None,
           output_root: Path, mode: str, include_components: bool,
           max_poses: int | None) -> dict:
    options = case.get("model", {}) if isinstance(case, dict) else {}
    input_errors = []
    input_origin = str(case.get("input_origin", "REPOSITORY_INPUT"))
    config_path = ROOT / "hardware/src/config.py"
    config_record = _file_record(config_path, "config_py", output_root)
    case_record = _file_record(case_path, "case", output_root)
    model_record = _model_inputs(case, output_root)
    assembly_record = _assembly_inputs(case, output_root)
    final_requirements = P.final_case_requirements(case, raise_on_missing=False)

    if assembly_record["generation_contract"]["status"] != "PASS":
        input_errors.append({"kind": "assembly_generation_contract",
                             "details": assembly_record["generation_contract"]})
    if not model_record["exists"]:
        input_errors.append({"kind": "model_geometry_missing"})
    if model_record.get("parse_error"):
        input_errors.append({"kind": "urdf_parse_error",
                             "error": model_record["parse_error"]})
    if not model_record.get("all_referenced_meshes_exist", True):
        input_errors.append({"kind": "model_mesh_missing"})
    if not model_record.get("all_referenced_meshes_valid", True):
        input_errors.append({"kind": "model_mesh_invalid",
                             "details": model_record.get("geometry_errors", [])})

    profile = case.get("profile") if isinstance(case.get("profile"), dict) else None
    profile_report = {
        "case_profile_present": profile is not None,
        "case_vs_config_py": _profile_mismatches(profile, _config_profile()),
        "config_sha256": _sha(config_path),
    }
    if mode == "final":
        if pose_path is None:
            input_errors.append({
                "kind": "final_requires_complete_pose_trace",
                "expected": "--pose-file with the complete production native trace",
            })
        if max_poses is not None:
            input_errors.append({
                "kind": "final_rejects_max_poses",
                "actual": max_poses,
                "expected": "omitted; final checks must consume every trace row",
            })
        if final_requirements.get("required") and final_requirements.get("status") != "PASS":
            input_errors.append({"kind": "final_case_requirements",
                                 "details": final_requirements})
        if input_origin != "REPOSITORY_INPUT":
            input_errors.append({"kind": "final_rejects_nonproduction_input_origin",
                                 "actual": input_origin})
        if profile_report["case_vs_config_py"]:
            input_errors.append({"kind": "case_profile_vs_config_py",
                                 "mismatches": profile_report["case_vs_config_py"]})
        if options.get("model_kind") not in P.FINAL_MODEL_KINDS:
            input_errors.append({"kind": "final_requires_final_model_kind",
                                 "actual": options.get("model_kind")})
        if options.get("self_collision") is not True:
            input_errors.append({"kind": "final_requires_self_collision"})
        if options.get("include_servo_collision") is not True:
            input_errors.append({"kind": "final_requires_servo_collision"})
        if not include_components:
            input_errors.append({"kind": "final_requires_component_occupancy"})
        freeze = P.validate_freeze_manifest(case, require_generation_roles=True)
        if freeze.get("status") != "PASS":
            input_errors.append({"kind": "freeze_manifest", "details": freeze})
    else:
        freeze = P.validate_freeze_manifest(case)

    pose_report = None
    parsed_poses = []
    header = ROOT / "firmware/src/print_first_gait.h"
    header_record = _file_record(header, "print_first_header", output_root)
    if mode == "final":
        # A final trace's local companion header is not enough: the frozen
        # source header must also match the current config before a fixed or
        # dynamic result can be called final.
        if not header.is_file():
            input_errors.append({"kind": "print_first_header_missing"})
        elif profile is not None:
            try:
                P._check_profile_header(profile, header, mode="frozen_print_first")
            except Exception as exc:  # noqa: BLE001
                input_errors.append({"kind": "print_first_header_contract",
                                     "error": _error_text(exc)})
    if pose_path is not None:
        try:
            pose_report, parsed_poses = _load_pose_file(pose_path, case, mode)
            if not pose_report.get("valid"):
                input_errors.append({"kind": "pose_file_contract",
                                     "details": pose_report.get("errors", []),
                                     "path": pose_report.get("path")})
        except Exception as exc:  # noqa: BLE001
            input_errors.append({"kind": "pose_file_contract", "error": _error_text(exc),
                                 "path": _public(pose_path)})

    fixed = {"status": "UNRUN", "aabb_pairs": 0, "intersections": [], "errors": []}
    dynamic = []
    try:
        # Do not spend time on a bundle that has already failed its generation
        # contract.  The failure is recorded above and exits 1.
        if not input_errors:
            local_rows = _fixed_rows(include_components)
            native_rows = _native_rows(local_rows)
            fixed = _check_native_pairs(native_rows, same_link=True)
            fixed["status"] = "FAIL" if fixed.get("intersections") or fixed.get("errors") else "PASS"
            if parsed_poses:
                selected = parsed_poses if max_poses is None else parsed_poses[:max_poses]
                for index, row, pose in selected:
                    moved = _dynamic_native_rows(native_rows, pose)
                    checked = _check_native_pairs(moved, same_link=False)
                    checked["pose_index"] = index
                    checked["time_s"] = row.get("time_s")
                    checked["segment"] = row.get("segment")
                    # Explicit evidence that the broad phase omitted rigid
                    # same-link pairs before any Boolean calculation.
                    checked["same_link_boolean_skipped"] = True
                    checked["status"] = "FAIL" if (
                        checked.get("intersections") or checked.get("errors")) else "PASS"
                    dynamic.append(checked)
    except Exception as exc:  # noqa: BLE001
        input_errors.append({"kind": "geometry_check_exception", "error": _error_text(exc)})

    fixed_bad = bool(fixed.get("intersections") or fixed.get("errors"))
    dynamic_bad = any(item.get("intersections") or item.get("errors") for item in dynamic)
    geometry_ok = fixed.get("status", "") != "UNRUN" and not fixed_bad and not dynamic_bad
    contract_ok = not input_errors
    if pose_path is None:
        status = "FIXED_ONLY_PASS" if geometry_ok and contract_ok else "FAIL"
    else:
        if not (geometry_ok and contract_ok):
            status = "FAIL"
        elif max_poses is not None:
            status = "DYNAMIC_SUBSET_PASS"
        else:
            status = "DYNAMIC_PASS"

    return {
        "schema_version": 2,
        "status": status,
        "mode": mode,
        "input_origin": input_origin,
        "physical_readiness": "UNVERIFIED",
        "configuration": "print_first",
        "case": {
            "name": case.get("name"),
            "path": case_record["path"],
            "sha256": case_record["sha256"],
        },
        "model": model_record,
        "config": config_record,
        "firmware_header": header_record,
        "assemblies": assembly_record,
        "final_case_requirements": final_requirements,
        "trace": pose_report,
        "checks": {
            "input_contract": contract_ok,
            "input_errors": input_errors,
            "profile": profile_report,
            "fixed_only": pose_path is None,
            "components_included": include_components,
            "pose_rows_read": 0 if pose_report is None else pose_report["row_count"],
            "pose_rows_checked": len(dynamic),
            "pose_check_limited": bool(pose_report is not None and max_poses is not None),
            "comparison_mode_explicit": mode == "development",
            "fixed_geometry": geometry_ok if pose_path is None or not dynamic else not fixed_bad,
            "dynamic_geometry": (None if pose_path is None else not dynamic_bad),
        },
        "fixed_same_link": fixed,
        "dynamic_cross_link": dynamic,
        "method": "source STL meshes + strict Manifold Boolean (local solids cached once, rigidly transformed per pose); dynamic poses are native C++ JSON rows and same-link pairs are skipped before cross-link Boolean",
        "limits": [
            "電装箱は設計用包絡であり、購入個体の寸法・保持は未確認。",
            "有限traceの検査は連続した全到達姿勢の証明ではない。",
            "PASSは実機の適合、印刷強度、電源容量、実歩行を意味しない。",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, default=DEFAULT_CASE,
                        help="検査ケースJSON（配列の場合は先頭、または--case-name）")
    parser.add_argument("--case-name", default=None)
    parser.add_argument("--pose-file", type=Path, default=None,
                        help="export_print_first_native_trace.py の完全native trace JSON")
    parser.add_argument("--mode", choices=("final", "development"), default="final",
                        help="finalは完全production traceを要求。候補/部分比較は明示的なdevelopment診断モード")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--without-components", action="store_true",
                        help="比較用に7個の電装占有箱を除外する")
    parser.add_argument("--max-poses", type=int, default=None,
                        help="動的比較を先頭N行へ制限（制限結果は合格証明ではない）")
    args = parser.parse_args()
    output = (args.output if args.output.is_absolute() else ROOT / args.output).resolve()
    try:
        if args.max_poses is not None and args.max_poses <= 0:
            raise ValueError("--max-poses must be positive")
        if args.max_poses is not None and args.pose_file is None:
            raise ValueError("--max-poses requires --pose-file")
        if args.without_components and args.mode == "final":
            raise ValueError("--without-components is allowed only in --mode development")
        case, case_path = _case_from_file(args.case, args.case_name)
        case = _resolve_case_output_refs(case, _case_output_root(case_path))
        pose_path = None if args.pose_file is None else (
            args.pose_file if args.pose_file.is_absolute() else ROOT / args.pose_file)
        result = _check(case, case_path, pose_path=pose_path, output_root=output.parent,
                        mode=args.mode, include_components=not args.without_components,
                        max_poses=args.max_poses)
    except Exception as exc:  # noqa: BLE001 - always save a reviewable FAIL result
        result = {
            "schema_version": 2,
            "status": "FAIL",
            "mode": args.mode,
            "physical_readiness": "UNVERIFIED",
            "configuration": "print_first",
            "checks": {"input_contract": False,
                       "input_errors": [{"kind": "cli_or_input_exception",
                                         "error": _error_text(exc)}]},
            "method": "source STL meshes + strict Manifold Boolean",
        }
    # Internal checks retain absolute Paths for execution, while the saved
    # artifact uses reproducible repo-relative/$OUTPUT/$EXTERNAL keys.
    result = _publicize(result, output.parent)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                      encoding="utf-8")
    print(json.dumps({
        "status": result["status"],
        "output": _public(output),
        "fixed_intersections": len(result.get("fixed_same_link", {}).get("intersections", [])),
        "dynamic_poses_checked": len(result.get("dynamic_cross_link", [])),
        "dynamic_intersections": sum(len(item.get("intersections", []))
                                     for item in result.get("dynamic_cross_link", [])),
        "errors": len(result.get("checks", {}).get("input_errors", [])) +
                  len(result.get("fixed_same_link", {}).get("errors", [])) +
                  sum(len(item.get("errors", [])) for item in result.get("dynamic_cross_link", [])),
    }, ensure_ascii=False))
    return 0 if result["status"] in (
        "FIXED_ONLY_PASS", "DYNAMIC_PASS", "DYNAMIC_SUBSET_PASS") else 1


if __name__ == "__main__":
    raise SystemExit(main())
