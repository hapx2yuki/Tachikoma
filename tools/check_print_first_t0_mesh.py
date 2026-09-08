#!/usr/bin/env python3
"""印刷優先構成のC++初期姿勢を、元STL同士で検査する。

``sim_stress`` の ``native_output_trace`` が返す最初の行は、MuJoCo の
``mj_step`` 前にコントローラが出した関節角である。この検査はその行を
各リンクへ適用し、凸包/VHACDを使わず、部品単位のManifold交差体積を
記録する。検査のPASSは実機の適合、強度、電源、配線を保証しない。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np
import trimesh
from manifold3d import Manifold, Mesh

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "hardware" / "src")]

import export_urdf as E
import print_first_assembly as A
import sim_collision as SC
import sim_print_first as P


VOLUME_TOL_MM3 = 1.0e-6
INTERSECTION_THRESHOLD_MM3 = float(E.C.BOOLEAN_INTERSECTION_THRESHOLD_MM3)


def _rel(path: Path) -> str:
    """結果JSON用の再現可能なパスを返す。

    ケースや診断をリポジトリ外から渡しても、公開台帳へユーザーの絶対
    パスを残さない。外部ファイルは名前とその時点のSHA先頭だけで表す。
    """
    path = Path(path).resolve()
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        digest = _sha256(path)[:12] if path.is_file() else "missing"
        return f"$EXTERNAL/{path.name}#{digest}"


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


EXPECTED_COMPILE_FLAG = "-DTACHIKOMA_PRINT_FIRST_PROFILE=1"
EXPECTED_PROFILE_MODE = "frozen_print_first"


def _joint_order() -> tuple[str, ...]:
    """診断JSONで要求する、native C++の20軸名を一元的に組み立てる。"""
    return tuple(
        [f"leg_{leg.lower()}_{joint}"
         for leg in E.LEGS for joint in ("yaw", "pitch", "knee")]
        + [
            "arm_r_yaw", "arm_r_pitch", "arm_r_elbow",
            "arm_l_yaw", "arm_l_pitch", "arm_l_elbow",
            "eye_r_roll", "eye_l_roll",
        ]
    )


def _diagnostic_output_root(path: Path) -> Path:
    """診断JSON内の '$OUTPUT' をその生成束へ戻す。"""
    path = Path(path).resolve()
    if path.parent.name in ("diagnostics", "native-trace"):
        return path.parent.parent
    return path.parent


def _resolve_diagnostic_ref(value: object, owner: Path) -> Path | None:
    """診断JSONの repo-relative/'$OUTPUT' 参照を実行用パスへ戻す。"""
    if not isinstance(value, str) or not value:
        return None
    if value.startswith("$EXTERNAL/"):
        # An external key is intentionally irreversible. The caller records a
        # contract error rather than guessing a local path with the same name.
        return None
    if value.startswith("$OUTPUT/"):
        return (_diagnostic_output_root(owner) / value[len("$OUTPUT/"):]).resolve()
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate.resolve()
    # Generator outputs use repository-relative keys; accepting a path next to
    # the diagnostic would make an old copied bundle appear current.
    return (ROOT / candidate).resolve()


def _resolve_build_child_ref(value: object, build_json: Path) -> Path | None:
    """build.json内の相対参照を、同じfirmware出力束へ解決する。"""
    if not isinstance(value, str) or not value or value.startswith("$EXTERNAL/"):
        return None
    if value.startswith("$OUTPUT/"):
        return (_diagnostic_output_root(build_json) / value[len("$OUTPUT/"):]).resolve()
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate.resolve()
    return (build_json.parent / candidate).resolve()


def _case_first_body_h(case: dict) -> float | None:
    """ケース先頭segmentのbody_hをnative診断の単一基準として返す。"""
    segments = case.get("segments") if isinstance(case, dict) else None
    profile = case.get("profile") if isinstance(case, dict) else None
    if not isinstance(segments, list) or not segments or not isinstance(segments[0], dict):
        return None
    if not isinstance(profile, dict):
        profile = {}
    value = segments[0].get("body_h", profile.get("body_h"))
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _validate_native_build_sources(build_data: object,
                                   build_json: Path) -> list[dict]:
    """build.jsonの実ビルド対象ヘッダー集合とSHAを厳密に突合する。"""
    if not isinstance(build_data, dict):
        return [{"kind": "native_build_source_sha256",
                 "actual": "missing_or_not_object"}]
    source_sha = build_data.get("source_sha256")
    if not isinstance(source_sha, dict) or not source_sha:
        return [{"kind": "native_build_source_sha256", "actual": "missing_or_empty"}]
    expected_headers = set(P.NATIVE_PROFILE_HEADERS)
    actual_headers = set(source_sha)
    errors: list[dict] = []
    if actual_headers != expected_headers:
        errors.append({"kind": "native_build_source_sha256_keys",
                       "actual": sorted(actual_headers),
                       "expected": sorted(expected_headers)})
    for raw_name, expected_sha in source_sha.items():
        child = _resolve_build_child_ref(raw_name, build_json)
        actual_sha = _sha256(child) if child is not None and child.is_file() else None
        if actual_sha != expected_sha:
            errors.append({"kind": "native_build_source_sha256_mismatch",
                           "path": raw_name, "actual": expected_sha,
                           "expected": actual_sha})
    return errors


def _case_assembly_entries(case: dict) -> list[tuple[str, Path]]:
    """実メッシュ監査が採用するbody/legs/feet台帳を列挙する。"""
    options = case.get("model", {}) if isinstance(case, dict) else {}
    defaults = (
        ("body", ROOT / "outputs/print-first-20260905/body/assembly.json"),
        ("legs", ROOT / "outputs/print-first-20260905/legs/assembly.json"),
        ("feet", ROOT / "outputs/print-first-20260905/feet/assembly.json"),
    )
    keys = {"body": ("assembly_manifest", "body_manifest", "geometry_manifest"),
            "legs": ("leg_manifest",),
            "feet": ("feet_manifest", "foot_assembly", "foot_contact_reference")}
    entries = []
    for role, default in defaults:
        raw = next((options.get(key) or case.get(key)
                    for key in keys[role]
                    if options.get(key) or case.get(key)), default)
        path = Path(raw)
        if not path.is_absolute():
            path = ROOT / path
        entries.append((role, path.resolve()))
    return entries


def _manifest_stl_paths(path: Path) -> list[Path]:
    """assembly台帳からSTL参照を再帰的に抽出する（局所実装）。"""
    path = Path(path).resolve()
    if not path.is_file() or path.suffix.lower() != ".json":
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    result: list[Path] = []

    def walk(item):
        if isinstance(item, dict):
            for key, value in item.items():
                if isinstance(value, str) and str(key).lower() in {
                    "path", "stl", "mesh", "filename", "file"
                } and Path(value).suffix.lower() == ".stl":
                    candidate = Path(value)
                    if not candidate.is_absolute():
                        candidate = (
                            ROOT / candidate
                            if candidate.parts and candidate.parts[0] in {
                                "hardware", "outputs", "model", "tools",
                                "docs", "firmware"
                            }
                            else path.parent / candidate
                        )
                    result.append(candidate.resolve())
                if isinstance(key, str) and Path(key).suffix.lower() == ".stl":
                    candidate = Path(key)
                    result.append((
                        ROOT / candidate if not candidate.is_absolute() else candidate
                    ).resolve())
                walk(value)
        elif isinstance(item, list):
            for value in item:
                walk(value)

    walk(data)
    return result


def _adopted_serialized_stl_entries(case: dict) -> list[tuple[str, Path]]:
    """採用台帳が直接参照する全STLを一度だけ列挙する。

    body/legs/feet assembly.jsonのpartsだけでなく、feet台帳の retained
    source STLも含める。台帳が途中で差し替えられた場合は、開始時に得た
    集合と台帳自身のSHA差分の両方で検出する。
    """
    entries: list[tuple[str, Path]] = []
    seen: set[Path] = set()
    for role, assembly in _case_assembly_entries(case):
        for value in _manifest_stl_paths(assembly):
            path = Path(value).resolve()
            if path in seen:
                continue
            seen.add(path)
            entries.append((f"{role}_adopted_stl", path))
    return entries


def _resolve_manifest_file(value: object, *, base: Path) -> Path | None:
    if not isinstance(value, str) or not value or value.startswith("$"):
        return None
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate.resolve()
    if candidate.parts and candidate.parts[0] in {
        "hardware", "firmware", "outputs", "docs", "tools", "model"
    }:
        return (ROOT / candidate).resolve()
    return (base / candidate).resolve()


def _urdf_mesh_entries(urdf: Path) -> list[Path]:
    if not urdf.is_file():
        return []
    try:
        import xml.etree.ElementTree as ET
        root = ET.parse(urdf).getroot()
    except Exception:
        return []
    result: list[Path] = []
    seen: set[Path] = set()
    for item in root.findall(".//mesh"):
        filename = item.get("filename")
        if not filename:
            continue
        candidate = Path(filename)
        if not candidate.is_absolute():
            candidate = urdf.parent / candidate
        candidate = candidate.resolve()
        if candidate.suffix.lower() == ".stl" and candidate not in seen:
            seen.add(candidate)
            result.append(candidate)
    return result


def _required_input_entries(case: dict, case_path: Path,
                            diagnostic_path: Path,
                            diagnostic: dict | None = None) -> list[tuple[str, Path]]:
    """t0判定の開始/終了SHAを取る全入力集合。"""
    entries: list[tuple[str, Path]] = [
        ("case", Path(case_path).resolve()),
        ("diagnostic", Path(diagnostic_path).resolve()),
        ("config_py", ROOT / "hardware/src/config.py"),
    ]
    entries.extend((f"{role}_assembly", path)
                   for role, path in _case_assembly_entries(case))
    entries.extend(_adopted_serialized_stl_entries(case))
    options = case.get("model", {}) if isinstance(case, dict) else {}
    source_urdf = _path_from_case_value(
        options.get("model_path") or options.get("model_urdf")
        or options.get("urdf_path"), owner=case_path
    ) or (ROOT / "hardware/urdf-print-first/tachikoma.urdf")
    entries.append(("case_source_urdf", source_urdf))
    entries.extend(("case_source_urdf_stl", path)
                   for path in _urdf_mesh_entries(source_urdf))

    # The t0 source rows are built from the derived URDF context used by the
    # diagnostic. Include that URDF and its serialized meshes as well.
    diagnostic_model = None
    if isinstance(diagnostic, dict):
        model = diagnostic.get("model")
        if isinstance(model, dict):
            diagnostic_model = _resolve_diagnostic_ref(model.get("path"),
                                                       diagnostic_path)
    if diagnostic_model is not None:
        entries.append(("diagnostic_derived_urdf", diagnostic_model))
        entries.extend(("diagnostic_derived_urdf_stl", path)
                       for path in _urdf_mesh_entries(diagnostic_model))

    # Native executable evidence is part of the pose source.  Keep the
    # executable, build contract, copied profile header, trace source, and all
    # header sources named by build.json in the start/end hash closure.
    if isinstance(diagnostic, dict):
        controller = diagnostic.get("controller")
        if isinstance(controller, dict):
            native_binary = _resolve_diagnostic_ref(controller.get("binary"),
                                                     diagnostic_path)
            native_build = _resolve_diagnostic_ref(controller.get("build_json"),
                                                    diagnostic_path)
            header_info = controller.get("header")
            native_header = (_resolve_diagnostic_ref(header_info.get("path"),
                                                      diagnostic_path)
                             if isinstance(header_info, dict) else None)
            trace_info = controller.get("trace")
            native_trace = (_resolve_diagnostic_ref(trace_info.get("path"),
                                                     diagnostic_path)
                            if isinstance(trace_info, dict) else None)
            for role, path in (("native_binary", native_binary),
                               ("native_build_json", native_build),
                               ("native_header", native_header),
                               ("native_trace", native_trace)):
                if path is not None:
                    entries.append((role, path))
            if native_build is not None and native_build.is_file():
                try:
                    build_data = json.loads(native_build.read_text(encoding="utf-8"))
                except Exception:
                    build_data = {}
                source_sha = (build_data.get("source_sha256", {})
                              if isinstance(build_data, dict) else {})
                if isinstance(source_sha, dict):
                    for raw in source_sha:
                        path = _resolve_build_child_ref(raw, native_build)
                        if path is not None:
                            entries.append(("native_build_source", path))
                if native_trace is None and isinstance(build_data, dict):
                    # prepare_native always emits trace.cpp beside build.json.
                    trace_path = native_build.parent / "trace.cpp"
                    if trace_path.is_file():
                        entries.append(("native_trace", trace_path))

    # Freeze2 is itself a required input, and every file it names is captured
    # so a source/STL change after the geometry check cannot be overlooked.
    freeze_value = options.get("freeze_manifest") or options.get("geometry_freeze_manifest")
    freeze = _path_from_case_value(freeze_value, owner=case_path)
    if freeze is not None:
        entries.append(("freeze_manifest", freeze))
        if freeze.is_file():
            try:
                freeze_data = json.loads(freeze.read_text(encoding="utf-8"))
            except Exception:
                freeze_data = {}
            rows = freeze_data.get("files", []) if isinstance(freeze_data, dict) else []
            if isinstance(rows, list):
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    path = _resolve_manifest_file(row.get("path"), base=freeze.parent)
                    if path is not None:
                        entries.append(("freeze_dependency", path))
    unique: list[tuple[str, Path]] = []
    seen: set[Path] = set()
    for role, path in entries:
        path = Path(path).resolve()
        if path in seen:
            continue
        seen.add(path)
        unique.append((role, path))
    return unique


def _hash_snapshot(entries: list[tuple[str, Path]]) -> dict[str, dict]:
    """絶対パスを内部キーにした開始/終了スナップショット。"""
    snapshot: dict[str, dict] = {}
    for role, raw_path in entries:
        path = Path(raw_path).resolve()
        exists = path.is_file()
        snapshot[str(path)] = {
            "role": role,
            "path": path,
            "exists": exists,
            "sha256": _sha256(path) if exists else None,
        }
    return snapshot


def _public_hash_snapshot(snapshot: dict[str, dict]) -> list[dict]:
    return [
        {"role": item["role"], "path": _rel(item["path"]),
         "exists": bool(item["exists"]), "sha256": item["sha256"]}
        for _key, item in sorted(snapshot.items())
    ]


def _compare_hash_snapshots(before: dict[str, dict],
                            after: dict[str, dict]) -> tuple[bool, list[dict]]:
    """全入力の存在/SHAを比較し、差分を構造化する。"""
    changes: list[dict] = []
    for key in sorted(set(before) | set(after)):
        old = before.get(key)
        new = after.get(key)
        if old is None or new is None:
            changes.append({"path": _rel(Path(key)), "kind": "required_set_changed",
                            "before": None if old is None else old["sha256"],
                            "after": None if new is None else new["sha256"]})
            continue
        if old["exists"] != new["exists"] or old["sha256"] != new["sha256"]:
            changes.append({"path": _rel(Path(key)), "kind": "file_changed",
                            "before_exists": old["exists"], "after_exists": new["exists"],
                            "before_sha256": old["sha256"], "after_sha256": new["sha256"]})
    complete = all(item["exists"] for item in before.values())
    return bool(complete and not changes), changes


def _path_from_case_value(value: object, *, owner: Path | None = None) -> Path | None:
    if not isinstance(value, (str, Path)) or not value:
        return None
    if isinstance(value, str) and value.startswith("$EXTERNAL/"):
        return None
    if isinstance(value, str) and value.startswith("$OUTPUT/"):
        if owner is None:
            return None
        return (_diagnostic_output_root(owner) / value[len("$OUTPUT/"):]).resolve()
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    return path.resolve()


def _validate_native_pose_source(case: dict, case_path: Path,
                                 diagnostic: dict, diagnostic_path: Path) -> tuple[
                                     dict[str, float] | None, list[dict], dict]:
    """診断内の姿勢が、現在ケースの native C++ t0 であることを検査する。"""
    errors: list[dict] = []
    derived_model_path: Path | None = None
    controller = diagnostic.get("controller")
    if not isinstance(controller, dict):
        return None, [{"kind": "controller_metadata", "actual": "missing"}], {}

    profile_mode = controller.get("profile_mode")
    if profile_mode != EXPECTED_PROFILE_MODE:
        errors.append({"kind": "profile_mode", "actual": profile_mode,
                       "expected": EXPECTED_PROFILE_MODE})
    compile_flags = [controller.get("compile_flag"),
                     controller.get("compile_flag_required")]
    if any(value != EXPECTED_COMPILE_FLAG for value in compile_flags):
        errors.append({"kind": "compile_flag", "actual": compile_flags,
                       "expected": EXPECTED_COMPILE_FLAG})

    binary = _resolve_diagnostic_ref(controller.get("binary"), diagnostic_path)
    if binary is None or not binary.is_file():
        errors.append({"kind": "native_binary_missing",
                       "actual": controller.get("binary")})
    elif controller.get("binary_sha256") != _sha256(binary):
        errors.append({"kind": "native_binary_sha256",
                       "actual": controller.get("binary_sha256"),
                       "expected": _sha256(binary)})
    build_data = None
    build_json = _resolve_diagnostic_ref(controller.get("build_json"), diagnostic_path)
    if build_json is None or not build_json.is_file():
        errors.append({"kind": "native_build_json_missing",
                       "actual": controller.get("build_json")})
    else:
        actual_build_sha = _sha256(build_json)
        if controller.get("build_json_sha256") != actual_build_sha:
            errors.append({"kind": "native_build_json_sha256",
                           "actual": controller.get("build_json_sha256"),
                           "expected": actual_build_sha})
        try:
            build_data = json.loads(build_json.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - preserve build evidence cause
            build_data = None
            errors.append({"kind": "native_build_json_parse", "error": str(exc)})
        if not isinstance(build_data, dict):
            errors.append({"kind": "native_build_contract", "actual": "missing_or_not_object"})
        else:
            if build_data.get("profile_mode") != EXPECTED_PROFILE_MODE:
                errors.append({"kind": "native_build_profile_mode",
                               "actual": build_data.get("profile_mode"),
                               "expected": EXPECTED_PROFILE_MODE})
            if build_data.get("print_first_compile_flag") != EXPECTED_COMPILE_FLAG:
                errors.append({"kind": "native_build_compile_flag",
                               "actual": build_data.get("print_first_compile_flag"),
                               "expected": EXPECTED_COMPILE_FLAG})
            command = build_data.get("command")
            command_values = command if isinstance(command, list) else [command]
            if EXPECTED_COMPILE_FLAG not in command_values:
                errors.append({"kind": "native_build_command_compile_flag",
                               "actual": command,
                               "expected": EXPECTED_COMPILE_FLAG})
            contract = build_data.get("header_contract")
            if not isinstance(contract, dict):
                errors.append({"kind": "native_build_header_contract", "actual": "missing"})
            else:
                if contract.get("mode") != EXPECTED_PROFILE_MODE:
                    errors.append({"kind": "native_build_header_mode",
                                   "actual": contract.get("mode"),
                                   "expected": EXPECTED_PROFILE_MODE})
                config_sha = _sha256(ROOT / "hardware/src/config.py")
                if contract.get("source_config_sha256") != config_sha:
                    errors.append({"kind": "native_build_header_config_sha256",
                                   "actual": contract.get("source_config_sha256"),
                                   "expected": config_sha})
            errors.extend(_validate_native_build_sources(build_data, build_json))
            trace = _resolve_build_child_ref("trace.cpp", build_json)
            expected_trace_sha = build_data.get("trace_sha256")
            actual_trace_sha = _sha256(trace) if trace is not None and trace.is_file() else None
            if expected_trace_sha != actual_trace_sha:
                errors.append({"kind": "native_build_trace_sha256",
                               "actual": expected_trace_sha, "expected": actual_trace_sha})
    header_info = controller.get("header")
    if not isinstance(header_info, dict):
        errors.append({"kind": "native_header_metadata", "actual": "missing"})
    else:
        header = _resolve_diagnostic_ref(header_info.get("path"), diagnostic_path)
        if header is None or not header.is_file():
            errors.append({"kind": "native_header_missing",
                           "actual": header_info.get("path")})
        else:
            actual_header_sha = _sha256(header)
            if header_info.get("sha256") != actual_header_sha:
                errors.append({"kind": "native_header_sha256",
                               "actual": header_info.get("sha256"),
                               "expected": actual_header_sha})
            config_sha = _sha256(ROOT / "hardware/src/config.py")
            if header_info.get("source_config_sha256") != config_sha:
                errors.append({"kind": "native_header_config_sha256",
                               "actual": header_info.get("source_config_sha256"),
                               "expected": config_sha})
            if isinstance(build_data, dict):
                bundle_sources = build_data.get("source_sha256")
                expected_bundle_header = (
                    bundle_sources.get("print_first_gait.h")
                    if isinstance(bundle_sources, dict) else None
                )
                if expected_bundle_header != actual_header_sha:
                    errors.append({
                        "kind": "native_header_build_sha256",
                        "actual": actual_header_sha,
                        "expected": expected_bundle_header,
                    })

    trace_info = controller.get("trace")
    if not isinstance(trace_info, dict):
        errors.append({"kind": "native_trace_metadata", "actual": "missing"})
    else:
        trace = _resolve_diagnostic_ref(trace_info.get("path"), diagnostic_path)
        if trace is None or not trace.is_file():
            errors.append({"kind": "native_trace_missing",
                           "actual": trace_info.get("path")})
        else:
            actual_trace_sha = _sha256(trace)
            if trace_info.get("sha256") != actual_trace_sha:
                errors.append({"kind": "native_trace_sha256",
                               "actual": trace_info.get("sha256"),
                               "expected": actual_trace_sha})
            if isinstance(build_data, dict):
                expected_build_trace = build_data.get("trace_sha256")
                if expected_build_trace != actual_trace_sha:
                    errors.append({
                        "kind": "native_trace_build_sha256",
                        "actual": actual_trace_sha,
                        "expected": expected_build_trace,
                    })

    joint_order = tuple(controller.get("joint_order")
                       or diagnostic.get("joint_order") or ())
    expected_joints = _joint_order()
    # The diagnostic generator historically omitted joint_order and relied on
    # the dict insertion order. Requiring it now makes a hand-edited/Python
    # pose fail closed instead of being interpreted as native output.
    if joint_order != expected_joints:
        errors.append({"kind": "joint_order", "actual": list(joint_order),
                       "expected": list(expected_joints)})

    angles = controller.get("native_initial_joint_angles_deg")
    if not isinstance(angles, dict):
        errors.append({"kind": "native_initial_angles", "actual": "missing_or_not_object"})
        pose = None
    else:
        missing = sorted(set(expected_joints) - set(angles))
        extra = sorted(set(angles) - set(expected_joints))
        if missing or extra or len(angles) != len(expected_joints):
            errors.append({"kind": "native_initial_joint_keys",
                           "missing": missing, "extra": extra,
                           "count": len(angles), "expected_count": len(expected_joints)})
        pose = {}
        for name in expected_joints:
            if name not in angles:
                continue
            if isinstance(angles[name], bool):
                errors.append({"kind": "native_initial_joint_bool", "joint": name})
                continue
            try:
                value = float(angles[name])
            except (TypeError, ValueError):
                errors.append({"kind": "native_initial_joint_value",
                               "joint": name, "actual": angles[name]})
                continue
            if not math.isfinite(value):
                errors.append({"kind": "native_initial_joint_nonfinite", "joint": name})
                continue
            pose[name] = value
        if len(pose) != len(expected_joints):
            pose = None

    enabled = controller.get("native_initial_enabled")
    if not isinstance(enabled, dict) or set(enabled) != set(expected_joints):
        errors.append({"kind": "native_initial_enabled_keys"})
    elif any(not isinstance(value, bool) for value in enabled.values()):
        errors.append({"kind": "native_initial_enabled_values"})
    elif any(value is not True for value in enabled.values()):
        errors.append({"kind": "native_initial_enabled_not_all_true"})

    enabled_raw = controller.get("native_initial_enabled_raw")
    if not isinstance(enabled_raw, dict) or set(enabled_raw) != set(expected_joints):
        errors.append({"kind": "native_initial_enabled_raw_keys"})
    else:
        for name in expected_joints:
            value = enabled_raw[name]
            if isinstance(value, bool):
                errors.append({"kind": "native_initial_enabled_raw_bool", "joint": name})
                continue
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                errors.append({"kind": "native_initial_enabled_raw_value",
                               "joint": name, "actual": value})
                continue
            if not math.isfinite(numeric) or not math.isclose(
                    numeric, 1.0, rel_tol=0.0, abs_tol=1.0e-9):
                errors.append({"kind": "native_initial_enabled_raw_not_one",
                               "joint": name, "actual": value})

    columns = controller.get("native_row_columns")
    if not isinstance(columns, dict):
        errors.append({"kind": "native_row_columns", "actual": "missing"})
    else:
        if isinstance(columns.get("phase"), bool):
            errors.append({"kind": "native_row_phase_bool"})
        else:
            try:
                phase = float(columns.get("phase"))
                if not math.isfinite(phase):
                    raise ValueError
                if not math.isclose(phase, 0.0, rel_tol=0.0, abs_tol=1.0e-9):
                    errors.append({"kind": "native_row_phase_value",
                                   "actual": phase, "expected": 0.0})
            except (TypeError, ValueError):
                errors.append({"kind": "native_row_phase", "actual": columns.get("phase")})
        for name in ("moving", "ready"):
            if not isinstance(columns.get(name), bool):
                errors.append({"kind": "native_row_flag", "name": name,
                               "actual": columns.get(name)})
        if columns.get("moving") is True or columns.get("ready") is not True:
            errors.append({"kind": "native_row_not_ready", "columns": columns})

    raw_columns = controller.get("native_row_columns_raw")
    if not isinstance(raw_columns, dict):
        errors.append({"kind": "native_row_columns_raw", "actual": "missing"})
    else:
        for name, expected in (("phase", 0.0), ("moving", 0.0), ("ready", 1.0)):
            value = raw_columns.get(name)
            if isinstance(value, bool):
                errors.append({"kind": "native_row_raw_bool", "name": name})
                continue
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                errors.append({"kind": "native_row_raw_value", "name": name,
                               "actual": value})
                continue
            if not math.isfinite(numeric):
                errors.append({"kind": "native_row_raw_nonfinite", "name": name})
            elif not math.isclose(numeric, expected, rel_tol=0.0, abs_tol=1.0e-9):
                errors.append({"kind": "native_row_raw_value", "name": name,
                               "actual": numeric, "expected": expected})

    expected_body_h = _case_first_body_h(case)
    recorded_body_h = controller.get("initial_body_h_mm")
    if expected_body_h is None:
        errors.append({"kind": "case_first_body_h", "actual": "missing_or_nonfinite"})
    elif isinstance(recorded_body_h, bool):
        errors.append({"kind": "native_body_h_bool", "actual": recorded_body_h})
    else:
        try:
            numeric_body_h = float(recorded_body_h)
        except (TypeError, ValueError):
            numeric_body_h = None
        if numeric_body_h is None or not math.isfinite(numeric_body_h):
            errors.append({"kind": "native_body_h", "actual": recorded_body_h,
                           "expected": expected_body_h})
        elif not math.isclose(numeric_body_h, expected_body_h,
                              rel_tol=0.0, abs_tol=1.0e-9):
            errors.append({"kind": "native_body_h", "actual": numeric_body_h,
                           "expected": expected_body_h})

    initialization = diagnostic.get("initialization")
    if not isinstance(initialization, dict):
        errors.append({"kind": "initialization_metadata", "actual": "missing"})
    else:
        if initialization.get("mj_step_called") is not False:
            errors.append({"kind": "native_t0_requires_no_mj_step",
                           "actual": initialization.get("mj_step_called")})
        states = initialization.get("states")
        if not isinstance(states, list) or not states:
            errors.append({"kind": "native_t0_states", "actual": "missing_or_empty"})
        else:
            if not any(
                isinstance(state, dict)
                and state.get("mj_step_called") is False
                and "before_first_mj_step" in str(state.get("label", ""))
                for state in states
            ):
                errors.append({"kind": "native_t0_state_label"})

    if diagnostic.get("status") != "DIAGNOSTIC_ONLY":
        errors.append({"kind": "diagnostic_status",
                       "actual": diagnostic.get("status"),
                       "expected": "DIAGNOSTIC_ONLY"})
    if diagnostic.get("case_name") != case.get("name"):
        errors.append({"kind": "case_name",
                       "actual": diagnostic.get("case_name"),
                       "expected": case.get("name")})
    diagnostic_case = _resolve_diagnostic_ref(diagnostic.get("case_input"),
                                               diagnostic_path)
    if diagnostic_case is None or diagnostic_case.resolve() != case_path.resolve():
        errors.append({"kind": "case_reference",
                       "actual": diagnostic.get("case_input"),
                       "expected": _rel(case_path)})
    recorded_case_sha = diagnostic.get("case_sha256")
    actual_case_sha = _sha256(case_path) if case_path.is_file() else None
    if recorded_case_sha != actual_case_sha:
        errors.append({"kind": "case_sha256", "actual": recorded_case_sha,
                       "expected": actual_case_sha})

    options = case.get("model", {}) if isinstance(case, dict) else {}
    expected_source_urdf = _path_from_case_value(
        options.get("model_path") or options.get("model_urdf")
        or options.get("urdf_path"), owner=case_path
    ) or (ROOT / "hardware/urdf-print-first/tachikoma.urdf")
    model = diagnostic.get("model")
    if not isinstance(model, dict):
        errors.append({"kind": "model_metadata", "actual": "missing"})
    else:
        diagnostic_model = _resolve_diagnostic_ref(model.get("path"), diagnostic_path)
        derived_model_path = diagnostic_model
        # diagnose_print_first_initial builds a voltage-limited derived URDF
        # for MuJoCo. Its own path/SHA must match, while source_urdf is the
        # final generated URDF named by the case.
        if diagnostic_model is None:
            errors.append({"kind": "model_reference", "actual": model.get("path"),
                           "expected": "resolvable derived MuJoCo URDF"})
        if diagnostic_model is None or not diagnostic_model.is_file():
            errors.append({"kind": "model_reference",
                           "actual": model.get("path"),
                           "expected": "existing derived MuJoCo URDF"})
        derived_model_sha = (
            _sha256(diagnostic_model)
            if diagnostic_model is not None and diagnostic_model.is_file() else None
        )
        if model.get("sha256") != derived_model_sha:
            errors.append({"kind": "model_sha256", "actual": model.get("sha256"),
                           "expected": derived_model_sha})
        source_urdf = _resolve_diagnostic_ref(model.get("source_urdf"), diagnostic_path)
        if source_urdf is None or source_urdf.resolve() != expected_source_urdf.resolve():
            errors.append({"kind": "source_urdf_reference",
                           "actual": model.get("source_urdf"),
                           "expected": _rel(expected_source_urdf)})
        expected_source_sha = (
            _sha256(expected_source_urdf)
            if expected_source_urdf.is_file() else None
        )
        if model.get("source_urdf_sha256") != expected_source_sha:
            errors.append({"kind": "source_urdf_sha256",
                           "actual": model.get("source_urdf_sha256"),
                           "expected": expected_source_sha})
        config_sha = _sha256(ROOT / "hardware/src/config.py")
        if diagnostic.get("config_sha256") != config_sha:
            errors.append({"kind": "config_sha256",
                           "actual": diagnostic.get("config_sha256"),
                           "expected": config_sha})
        if model.get("config_sha256") != config_sha:
            errors.append({"kind": "model_config_sha256",
                           "actual": model.get("config_sha256"),
                           "expected": config_sha})
        freeze_value = options.get("freeze_manifest") or options.get("geometry_freeze_manifest")
        expected_freeze = _path_from_case_value(freeze_value, owner=case_path)
        diagnostic_freeze = _resolve_diagnostic_ref(model.get("freeze_manifest"),
                                                    diagnostic_path)
        if expected_freeze is None or diagnostic_freeze is None \
                or diagnostic_freeze.resolve() != expected_freeze.resolve():
            errors.append({"kind": "freeze_reference",
                           "actual": model.get("freeze_manifest"),
                           "expected": None if expected_freeze is None else _rel(expected_freeze)})
        actual_freeze_sha = (
            _sha256(expected_freeze) if expected_freeze is not None
            and expected_freeze.is_file() else None
        )
        if model.get("freeze_manifest_sha256") != actual_freeze_sha:
            errors.append({"kind": "freeze_manifest_sha256",
                           "actual": model.get("freeze_manifest_sha256"),
                           "expected": actual_freeze_sha})
        expected_freeze_hash = (
            options.get("geometry_freeze_hash") or options.get("freeze_hash")
        )
        if expected_freeze_hash != actual_freeze_sha:
            errors.append({"kind": "case_freeze_hash",
                           "actual": expected_freeze_hash,
                           "expected": actual_freeze_sha})
        freeze_validation = model.get("freeze_validation")
        if not isinstance(freeze_validation, dict) \
                or freeze_validation.get("status") != "PASS":
            errors.append({"kind": "freeze_validation",
                           "actual": None if not isinstance(freeze_validation, dict)
                           else freeze_validation.get("status"),
                           "expected": "PASS"})
        build_options = model.get("build_options")
        if isinstance(build_options, dict):
            for key in ("self_collision", "include_servo_collision"):
                if build_options.get(key) is not True:
                    errors.append({"kind": f"model_{key}",
                                   "actual": build_options.get(key), "expected": True})
        else:
            errors.append({"kind": "model_build_options"})

    return pose, errors, {
        "profile_mode": profile_mode,
        "compile_flag": controller.get("compile_flag"),
        "joint_order": list(expected_joints),
        "case_path": _rel(case_path),
        "source_urdf": _rel(expected_source_urdf),
        "derived_model_path": (None if derived_model_path is None
                                else _rel(derived_model_path)),
        "initial_body_h_mm": _case_first_body_h(case),
    }


def _strict_native(mesh: trimesh.Trimesh, label: str) -> Manifold:
    """元メッシュを検査してManifoldへ変換する。

    負体積を ``abs`` や ``max(0, ...)`` で正常化しない。交差結果の
    ごく小さい負値だけは、数値誤差の閾値を明記して呼び出し側で扱う。
    """
    if not isinstance(mesh, trimesh.Trimesh):
        raise ValueError(f"{label}: trimesh.Trimeshではない")
    if len(mesh.vertices) < 4 or len(mesh.faces) < 4:
        raise ValueError(f"{label}: 頂点/面が不足")
    if not np.isfinite(mesh.vertices).all():
        raise ValueError(f"{label}: 頂点に非有限値")
    if not np.isfinite(mesh.faces).all():
        raise ValueError(f"{label}: 面に非有限値")
    if not mesh.is_watertight or not mesh.is_winding_consistent:
        raise ValueError(f"{label}: 非閉体または面向き不整合")
    volume = float(mesh.volume)
    if not math.isfinite(volume) or volume <= 0.0:
        raise ValueError(f"{label}: 非有限/非正の体積 {volume!r}")
    out = Manifold(
        Mesh(np.asarray(mesh.vertices, dtype=np.float32),
             np.asarray(mesh.faces, dtype=np.uint32))
    )
    status = out.status()
    if status.name != "NoError":
        raise ValueError(f"{label}: Manifold status={status}")
    native_volume = float(out.volume())
    if not math.isfinite(native_volume) or native_volume <= 0.0:
        raise ValueError(f"{label}: Manifold体積が非有限/非正 {native_volume!r}")
    return out


def _intersection(a: Manifold, b: Manifold, label: str) -> tuple[float, bool]:
    """交差体積と、微小負値を閾値内として扱ったかを返す。"""
    out = a ^ b
    status = out.status()
    if status.name != "NoError":
        raise ValueError(f"{label}: 交差Boolean status={status}")
    raw = float(out.volume())
    if not math.isfinite(raw):
        raise ValueError(f"{label}: 交差体積が非有限 {raw!r}")
    if raw < -VOLUME_TOL_MM3:
        raise ValueError(f"{label}: 交差体積が負 {raw!r}")
    return (0.0 if raw < 0.0 else raw), raw < 0.0


def _aabb_disjoint(a: trimesh.Trimesh, b: trimesh.Trimesh) -> bool:
    # 境界が接するだけの組はBooleanを呼ばず、接触を食込みと判定しない。
    return bool(np.any(a.bounds[1] <= b.bounds[0] + 1.0e-5)
                or np.any(b.bounds[1] <= a.bounds[0] + 1.0e-5))


def _pose_dict(row: np.ndarray) -> dict[str, float]:
    if len(row) < 23:
        raise ValueError(f"C++出力列が不足: {len(row)}")
    angles = np.asarray(row[3:23], dtype=float)
    if not np.isfinite(angles).all():
        raise ValueError("C++初期姿勢の関節角に非有限値")
    out: dict[str, float] = {}
    for offset, leg in enumerate(E.LEGS):
        out.update({
            f"leg_{leg.lower()}_yaw": float(angles[offset * 3]),
            f"leg_{leg.lower()}_pitch": float(angles[offset * 3 + 1]),
            f"leg_{leg.lower()}_knee": float(angles[offset * 3 + 2]),
        })
    for name, index in (
        ("arm_r_yaw", 12), ("arm_r_pitch", 13), ("arm_r_elbow", 14),
        ("arm_l_yaw", 15), ("arm_l_pitch", 16), ("arm_l_elbow", 17),
        ("eye_r_roll", 18), ("eye_l_roll", 19),
    ):
        out[name] = float(angles[index])
    return out


def _rows_at_t0(pose: dict[str, float], *, include_components: bool) -> list[tuple[trimesh.Trimesh, str, str]]:
    expected = {
        *(f"leg_{leg.lower()}_{joint}" for leg in E.LEGS for joint in ("yaw", "pitch", "knee")),
        "arm_r_yaw", "arm_r_pitch", "arm_r_elbow",
        "arm_l_yaw", "arm_l_pitch", "arm_l_elbow",
        "eye_r_roll", "eye_l_roll",
    }
    missing = sorted(expected - set(pose))
    if missing:
        raise ValueError(f"診断JSONの初期姿勢に軸が不足: {missing}")
    if any(not math.isfinite(float(pose[name])) for name in expected):
        raise ValueError("診断JSONの初期姿勢に非有限値")
    with A.context():
        parts = SC.parts_with_pad(True, include_components=include_components)
        rows: list[tuple[trimesh.Trimesh, str, str]] = []
        for link, items in parts.items():
            frame_fn = E.LINK_PARENT_FRAME.get(link)
            if frame_fn is None:
                raise ValueError(f"親フレームが未定義: {link}")
            frame = frame_fn(pose)
            for mesh, _color, name in items:
                if not isinstance(mesh, trimesh.Trimesh):
                    raise ValueError(f"{link}/{name}: mesh型が不正")
                moved = mesh.copy()
                moved.apply_transform(frame)
                _strict_native(moved, f"{link}/{name}")
                rows.append((moved, name, link))
    return rows


def _check_pairs(rows: list[tuple[trimesh.Trimesh, str, str]], *, same_link: bool):
    hits = []
    errors = []
    aabb_pairs = 0
    native_cache: dict[int, Manifold] = {}
    for i, (a, aname, alink) in enumerate(rows):
        for j in range(i):
            b, bname, blink = rows[j]
            if (alink == blink) != same_link:
                continue
            if _aabb_disjoint(a, b):
                continue
            aabb_pairs += 1
            try:
                if i not in native_cache:
                    native_cache[i] = _strict_native(a, f"{alink}/{aname}")
                if j not in native_cache:
                    native_cache[j] = _strict_native(b, f"{blink}/{bname}")
                value, small_negative = _intersection(
                    native_cache[i], native_cache[j], f"{alink}/{aname} x {blink}/{bname}"
                )
                if value > INTERSECTION_THRESHOLD_MM3:
                    hits.append({
                        "parts": [bname, aname],
                        "links": [blink, alink],
                        "volume_mm3": value,
                        "raw_negative_within_tolerance": small_negative,
                        "bounds_a_mm": a.bounds.tolist(),
                        "bounds_b_mm": b.bounds.tolist(),
                    })
            except Exception as exc:  # fail closed; retain exact pair
                errors.append({
                    "parts": [bname, aname], "links": [blink, alink],
                    "error": str(exc),
                })
    hits.sort(key=lambda item: -item["volume_mm3"])
    return {"aabb_pairs": aabb_pairs, "intersections": hits, "errors": errors}


def _inventory_report(rows: list[tuple[trimesh.Trimesh, str, str]]) -> dict:
    """t0で実際に列挙した部品名と置換契約を保存する。"""
    inventory = []
    names = []
    for mesh, name, link in rows:
        names.append(name)
        legacy_token = SC.print_first_legacy_name(name)
        try:
            material = SC.collision_material(name)
        except ValueError:
            # Keep a forbidden legacy row in the audit report so the shared
            # inventory contract returns FAIL rather than aborting before the
            # offending name is recorded.  All non-legacy rows remain strict.
            if legacy_token is None:
                raise
            material = "LEGACY_FORBIDDEN"
        inventory.append({
            "name": name,
            "link": link,
            "material": material,
            "volume_mm3": float(mesh.volume),
            "bounds_mm": mesh.bounds.astype(float).tolist(),
        })
    required = set(SC.PRINT_FIRST_REQUIRED_PARTS)
    legacy_names = set(SC.PRINT_FIRST_LEGACY_PART_NAMES)
    required_counts = {name: names.count(name) for name in sorted(required)}
    required_present = sorted(name for name, count in required_counts.items() if count == 1)
    missing_required = sorted(name for name, count in required_counts.items() if count == 0)
    duplicated_required = sorted(name for name, count in required_counts.items() if count > 1)

    # The clearanced replacements intentionally contain old semantic names as
    # traceability fragments.  Use the shared rule to allow only the exact
    # adopted replacement names and reject every other exact/prefix/fragment,
    # including old toe and head variants.
    legacy_present = sorted({
        name for name in names
        if SC.print_first_legacy_name(name) is not None
    })
    tpu_rows = [
        {
            "name": name,
            "link": link,
            "material": SC.collision_material(name),
        }
        for _mesh, name, link in rows
        if name == "tpu_shoe"
    ]
    expected_tpu_links = {f"leg_{leg.lower()}_tibia" for leg in E.LEGS}
    actual_tpu_links = [row["link"] for row in tpu_rows]
    tpu_link_counts = {link: actual_tpu_links.count(link)
                       for link in sorted(expected_tpu_links)}
    checks = {
        "required_new_parts_present": not missing_required,
        "required_new_parts_exactly_once": not missing_required and not duplicated_required,
        "legacy_replacement_names_absent": not legacy_present,
        "four_tpu_leg_candidates_present": (
            len(tpu_rows) == 4
            and set(actual_tpu_links) == expected_tpu_links
            and all(count == 1 for count in tpu_link_counts.values())
            and all(row["material"] == "TPU" for row in tpu_rows)
        ),
    }
    return {
        "part_count": len(inventory),
        "parts": inventory,
        "required_new_parts": sorted(required),
        "required_new_parts_present": required_present,
        "missing_required_new_parts": missing_required,
        "duplicated_required_new_parts": duplicated_required,
        "legacy_replacement_names_checked": sorted(legacy_names),
        "legacy_replacement_names_present": legacy_present,
        "tpu_candidate_rows": tpu_rows,
        "tpu_candidate_legs": sorted(set(actual_tpu_links)),
        "tpu_candidate_link_counts": tpu_link_counts,
        "tpu_candidate_expected_material": "TPU",
        "checks": checks,
        "status": "PASS" if all(checks.values()) else "FAIL",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--case", type=Path,
        default=ROOT / "outputs/print-first-20260905/final-simulation/cases/final_stand_pf1.json",
        help="sim_stress case JSON（入力ハッシュの記録用）",
    )
    parser.add_argument(
        "--initial-diagnostic", type=Path,
        default=ROOT / "outputs/print-first-20260905/final-simulation-initial-diagnostic/diagnostics/final_stand_pf1-initial-contact.json",
        help="simulation が保存した profile=1 の time0 診断JSON",
    )
    parser.add_argument(
        "--output", type=Path,
        default=ROOT / "outputs/print-first-20260905/mechanical-diagnostics/t0-real-mesh.json",
    )
    parser.add_argument(
        "--without-components", action="store_true",
        help="印刷優先の電装占有箱を除外する（比較用）",
    )
    args = parser.parse_args()
    case_path = args.case if args.case.is_absolute() else ROOT / args.case
    diagnostic_path = (args.initial_diagnostic if args.initial_diagnostic.is_absolute()
                       else ROOT / args.initial_diagnostic)
    output_path = args.output if args.output.is_absolute() else ROOT / args.output
    case_path = case_path.resolve()
    diagnostic_path = diagnostic_path.resolve()
    fixed = {"aabb_pairs": 0, "intersections": [], "errors": []}
    dynamic = {"aabb_pairs": 0, "intersections": [], "errors": []}
    input_errors: list[dict] = []
    pose_errors: list[dict] = []
    pose_meta: dict = {}
    inventory: dict = {"status": "UNRUN", "parts": [], "checks": {}}
    before_hashes: dict[str, dict] = {}
    after_hashes: dict[str, dict] = {}
    required_entries: list[tuple[str, Path]] = []
    diagnostic: dict = {}
    case: dict = {}
    pose: dict[str, float] | None = None
    try:
        raw_case = json.loads(case_path.read_text(encoding="utf-8"))
        if not isinstance(raw_case, dict):
            raise ValueError("t0 checkerのcase JSONは一件のobjectでなければならない")
        case = raw_case
        diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))
        if not isinstance(diagnostic, dict):
            raise ValueError("t0 checkerのdiagnostic JSONはobjectでなければならない")
        required_entries = _required_input_entries(
            case, case_path, diagnostic_path, diagnostic
        )
        before_hashes = _hash_snapshot(required_entries)
        missing = [
            {"kind": "required_input_missing", "role": item["role"],
             "path": _rel(item["path"])}
            for item in before_hashes.values() if not item["exists"]
        ]
        input_errors.extend(missing)
        pose, pose_errors, pose_meta = _validate_native_pose_source(
            case, case_path, diagnostic, diagnostic_path
        )
        input_errors.extend(
            {"kind": "native_pose_contract", "details": error}
            for error in pose_errors
        )
        if pose is not None and not input_errors:
            rows = _rows_at_t0(pose, include_components=not args.without_components)
            inventory = _inventory_report(rows)
            if inventory.get("status") != "PASS":
                input_errors.append({
                    "kind": "part_inventory_contract",
                    "details": inventory,
                })
            fixed = _check_pairs(rows, same_link=True)
            dynamic = _check_pairs(rows, same_link=False)
    except Exception as exc:  # fail closed, but always emit a reviewable report
        input_errors.append({"kind": "input_or_geometry_exception",
                             "error": str(exc)})
    if required_entries:
        try:
            after_hashes = _hash_snapshot(required_entries)
        except Exception as exc:  # pragma: no cover - filesystem failure
            input_errors.append({"kind": "end_hash_snapshot_exception",
                                 "error": str(exc)})
    inputs_unchanged, hash_changes = _compare_hash_snapshots(
        before_hashes, after_hashes
    ) if before_hashes and after_hashes else (False, [])
    if before_hashes and not inputs_unchanged:
        input_errors.append({
            "kind": "inputs_unchanged",
            "details": "required input SHA/existence changed or a required input was missing",
            "changes": hash_changes,
        })
    geometry_bad = bool(
        fixed.get("intersections") or fixed.get("errors")
        or dynamic.get("intersections") or dynamic.get("errors")
    )
    result = {
        "status": "PASS" if not geometry_bad and not input_errors
                   and inputs_unchanged else "FAIL",
        "physical_readiness": "UNVERIFIED",
        "configuration": "print_first",
        "method": "serialized STL parts + Manifold exact Boolean at native C++ t0",
        "intersection_threshold_mm3": INTERSECTION_THRESHOLD_MM3,
        "negative_volume_tolerance_mm3": VOLUME_TOL_MM3,
        "components_included": not args.without_components,
        "case": _rel(case_path),
        "case_sha256": _sha256(case_path) if case_path.is_file() else None,
        "pose_source": {
            "path": _rel(diagnostic_path),
            "sha256": _sha256(diagnostic_path) if diagnostic_path.is_file() else None,
            "field": "controller.native_initial_joint_angles_deg",
            "time0": bool(not pose_errors and pose is not None),
            **pose_meta,
        },
        "angles_deg": pose or {},
        "fixed_same_link": fixed,
        "dynamic_cross_link": dynamic,
        "part_inventory": inventory,
        "input_hashes": {
            "required_file_count": len(required_entries),
            "required_files": [
                {"role": role, "path": _rel(path)}
                for role, path in required_entries
            ],
            "before": _public_hash_snapshot(before_hashes),
            "after": _public_hash_snapshot(after_hashes),
            "inputs_unchanged": inputs_unchanged,
            "changes": hash_changes,
        },
        "checks": {
            "pose_contract": not pose_errors,
            "input_contract": not input_errors,
            "inputs_unchanged": inputs_unchanged,
            "fixed_geometry": not bool(fixed.get("intersections")
                                       or fixed.get("errors")),
            "dynamic_geometry": not bool(dynamic.get("intersections")
                                         or dynamic.get("errors")),
            "input_errors": input_errors,
            "pose_errors": pose_errors,
        },
        "source_sha256": {
            _rel(Path(__file__)): _sha256(Path(__file__)),
            _rel(ROOT / "tools" / "sim_stress.py"): _sha256(ROOT / "tools" / "sim_stress.py"),
            _rel(ROOT / "tools" / "sim_collision.py"): _sha256(ROOT / "tools" / "sim_collision.py"),
            _rel(ROOT / "tools" / "print_first_assembly.py"): _sha256(ROOT / "tools" / "print_first_assembly.py"),
            _rel(ROOT / "hardware/src/config.py"): _sha256(ROOT / "hardware/src/config.py"),
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(json.dumps({
        "status": result["status"],
        "output": _rel(output_path),
        "fixed_intersections": len(fixed["intersections"]),
        "dynamic_intersections": len(dynamic["intersections"]),
        "errors": len(input_errors) + len(fixed["errors"]) + len(dynamic["errors"]),
        "inputs_unchanged": inputs_unchanged,
    }, ensure_ascii=False))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
