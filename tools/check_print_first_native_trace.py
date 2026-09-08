#!/usr/bin/env python3
"""実C++ print-first trace と有限実メッシュ監査の入口契約。

生成器が保存した実C++の有限入力を、生成元ヘッダー・ビルド記録・実行
バイナリ、20軸の出力行、固定/動的指令、そして有限メッシュ監査へ束縛する。
既定の ``production`` モードは短縮したfixture、手作業で作ったJSON、古い
trace、候補だけのリンク対を受け付けない。縮小した小fixtureは、明示的な
``--mode test`` でのみ検査する。
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import stat
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import config_contract as CONTRACT  # noqa: E402
import sim_physics as S  # noqa: E402
import sim_self_collision as SELF  # noqa: E402


EXPECTED_TRACE_COMPILE_FLAG = "-DTACHIKOMA_PRINT_FIRST_PROFILE=1"
REQUIRED_DIRECTION_LABELS = {
    "forward", "backward", "right", "left",
    "turn_positive", "turn_negative", "restart_forward",
}
REQUIRED_STATIC_LABELS = {"stand", "stop", "restart_stop", "settle", "hold"}
STANDARD_SEGMENT_ORDER = (
    "stand", "forward", "backward", "right", "left",
    "turn_positive", "turn_negative", "stop", "restart_forward",
    "restart_stop", "settle", "hold",
)
PRODUCTION_LINKS = {
    "base_link", "eye_pod_camera", "eye_r_pod", "eye_l_pod",
    *(f"leg_{leg}_{part}" for leg in ("fr", "fl", "rl", "rr")
      for part in ("coxa", "femur", "tibia")),
    *(f"arm_{side}_{part}" for side in ("r", "l")
      for part in ("shoulder", "upper", "forearm")),
}
SERVO_PERIOD = 1.0 / float(S.SERVO_HZ)
_FLOAT_TOLERANCE = 1e-6
_ANGLE_TOLERANCE_DEG = 0.25


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha(value: Any, label: str) -> str:
    if (not isinstance(value, str) or len(value) != 64
            or value.lower() != value
            or any(char not in "0123456789abcdef" for char in value)):
        raise ValueError(f"{label} must be a lowercase SHA-256")
    return value


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


def _close(actual: Any, expected: Any, *, tolerance: float = _FLOAT_TOLERANCE) -> bool:
    try:
        return math.isclose(float(actual), float(expected), rel_tol=0.0,
                           abs_tol=tolerance)
    except (TypeError, ValueError):
        return False


def _command(command: Any, label: str) -> tuple[float, float, float]:
    if not isinstance(command, dict):
        raise ValueError(f"{label}.command must be an object")
    values = tuple(_number(command.get(axis), f"{label}.command.{axis}")
                   for axis in ("vx", "vy", "wz"))
    if any(abs(value) > 1.0 + _FLOAT_TOLERANCE for value in values):
        raise ValueError(f"{label}.command must stay within [-1, 1]")
    return values


def _command_is_static(command: Any, label: str) -> bool:
    return all(abs(value) <= _FLOAT_TOLERANCE
               for value in _command(command, label))


def _regular_without_symlink(path: Path, anchor: Path, label: str) -> Path:
    """Resolve one canonical regular file, rejecting symlink components."""
    anchor = Path(anchor).absolute()
    raw = Path(path)
    if raw.is_absolute():
        candidate = raw
    else:
        candidate = anchor / raw
    try:
        relative = candidate.relative_to(anchor)
    except ValueError as exc:
        raise ValueError(f"{label} escapes its allowed root") from exc
    current = anchor
    if current.is_symlink():
        raise ValueError(f"{label} root is a symlink")
    for part in relative.parts:
        if part in ("", "."):
            continue
        if part == "..":
            raise ValueError(f"{label} contains '..'")
        current = current / part
        if current.is_symlink():
            raise ValueError(f"{label} contains a symlink: {current}")
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(anchor.resolve())
    except ValueError as exc:
        raise ValueError(f"{label} resolves outside its allowed root") from exc
    try:
        mode = resolved.stat().st_mode
    except FileNotFoundError as exc:
        raise ValueError(f"{label} does not exist: {candidate}") from exc
    if not stat.S_ISREG(mode):
        raise ValueError(f"{label} is not a regular file: {candidate}")
    return resolved


def _artifact_path(value: Any, label: str, trace_path: Path, *, required: bool,
                   allow_absolute: bool = False) -> Path | None:
    if value is None:
        if required:
            raise ValueError(f"{label} path is required")
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} path must be a non-empty string")
    run_root = (trace_path.parent.parent
                if trace_path.parent.name == "native-trace"
                else trace_path.parent)
    if value.startswith("$OUTPUT/"):
        return _regular_without_symlink(
            run_root / value[len("$OUTPUT/"):], run_root, label)
    if value.startswith("$EXTERNAL/"):
        raise ValueError(f"{label} external path is not allowed")
    raw = Path(value)
    if raw.is_absolute() and not allow_absolute:
        raise ValueError(f"{label} must use a repository or $OUTPUT path")
    if raw.is_absolute():
        return _regular_without_symlink(raw, raw.anchor, label)
    return _regular_without_symlink(ROOT / raw, ROOT, label)


def _input_trace_path(path: Path) -> Path:
    raw = Path(path)
    if not raw.is_absolute():
        raw = ROOT / raw
    raw = raw.absolute()
    run_root = raw.parent.parent if raw.parent.name == "native-trace" else raw.parent
    # The operating system may spell its temporary directory through an
    # alias.  The project/run-root portion itself must remain canonical.
    anchor = run_root
    current = anchor
    for part in raw.relative_to(anchor).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"native trace path contains a symlink: {current}")
    resolved = raw.resolve(strict=False)
    if not resolved.is_file() or not stat.S_ISREG(resolved.stat().st_mode):
        raise FileNotFoundError(f"native trace does not exist as a regular file: {raw}")
    return resolved


def _profile_expected() -> dict[str, Any]:
    cfg = getattr(CONTRACT.C, "PRINT_FIRST_GAIT", None)
    if not isinstance(cfg, dict):
        raise ValueError("config.py PRINT_FIRST_GAIT is missing")
    return {
        "body_h": cfg["body_h"],
        "stance_r": cfg["stance_r"],
        "stance_off_y": cfg["stance_off_xy"][1],
        "step_h": cfg["step_h"],
        "max_step": cfg["max_step"],
        "max_turn_deg": cfg["max_turn_deg"],
        "cycle_t": cfg["cycle_t"],
        "duty": cfg["duty"],
        "sway_mm": list(cfg["sway_mm"]),
        "sway_lead": cfg["sway_lead"],
        "phase_off": list(cfg["phase_off"]),
        "arm_swing_deg": cfg["arm_swing_deg"],
        "hip_r": cfg["hip_r"],
        "path_shape": cfg.get("path_shape", "linear"),
    }


def _standard_segments() -> list[tuple[str, tuple[float, float, float], float, float]]:
    profile = _profile_expected()
    cycle = float(profile["cycle_t"])
    body_h = float(profile["body_h"])
    return [
        ("stand", (0.0, 0.0, 0.0), 0.48, body_h),
        ("forward", (0.0, 1.0, 0.0), cycle, body_h),
        ("backward", (0.0, -1.0, 0.0), cycle, body_h),
        ("right", (1.0, 0.0, 0.0), cycle, body_h),
        ("left", (-1.0, 0.0, 0.0), cycle, body_h),
        ("turn_positive", (0.0, 0.0, 1.0), cycle, body_h),
        ("turn_negative", (0.0, 0.0, -1.0), cycle, body_h),
        ("stop", (0.0, 0.0, 0.0), 1.32, body_h),
        ("restart_forward", (0.0, 1.0, 0.0), cycle, body_h),
        ("restart_stop", (0.0, 0.0, 0.0), 1.32, body_h),
        ("settle", (0.0, 0.0, 0.0), 0.48, body_h),
        ("hold", (0.0, 0.0, 0.0), 2.40, body_h),
    ]


def _joint_limits() -> dict[str, tuple[float, float]]:
    values = CONTRACT.canonical_values()["scalars"]
    limits: dict[str, tuple[float, float]] = {}
    for leg in ("fr", "fl", "rl", "rr"):
        limits[f"leg_{leg}_yaw"] = (-values["LIM_YAW"], values["LIM_YAW"])
        limits[f"leg_{leg}_pitch"] = (values["LIM_PITCH_UP"], values["LIM_PITCH_DN"])
        limits[f"leg_{leg}_knee"] = (-values["LIM_KNEE"], values["LIM_KNEE"])
    limits.update({
        "arm_r_yaw": (-values["ARM_YAW_LIM"], values["ARM_YAW_LIM"]),
        "arm_l_yaw": (-values["ARM_YAW_LIM"], values["ARM_YAW_LIM"]),
        "arm_r_pitch": (values["ARM_PITCH_MIN"], values["ARM_PITCH_MAX"]),
        "arm_l_pitch": (values["ARM_PITCH_MIN"], values["ARM_PITCH_MAX"]),
        "arm_r_elbow": (values["ARM_ELBOW_MIN"], values["ARM_ELBOW_MAX"]),
        "arm_l_elbow": (values["ARM_ELBOW_MIN"], values["ARM_ELBOW_MAX"]),
        "eye_r_roll": (-values["EYE_LIM"], values["EYE_LIM"]),
        "eye_l_roll": (-values["EYE_LIM"], values["EYE_LIM"]),
    })
    return limits


def _verify_provenance(payload: dict[str, Any], trace_path: Path, *, mode: str) -> tuple[list[str], dict[str, Path]]:
    """実体SHA、ビルド設定、ソース閉包を照合する。"""
    errors: list[str] = []
    artifacts: dict[str, Path] = {}
    production = mode == "production"
    header = payload.get("header")
    build = payload.get("build")
    binary = payload.get("binary")
    if not isinstance(header, dict) or not isinstance(build, dict) or not isinstance(binary, dict):
        return ["header/build/binary provenance objects are required"], artifacts

    def verify_file(value: Any, label: str, expected_sha: Any, *, required: bool = True) -> Path | None:
        try:
            path = _artifact_path(value, label, trace_path, required=required)
            if path is None:
                return None
            digest = _sha256(path)
            _sha(expected_sha, f"{label}.sha256")
            if digest != expected_sha:
                errors.append(f"{label}.sha256 does not match the canonical file")
            return path
        except (OSError, ValueError) as exc:
            errors.append(str(exc))
            return None

    header_path = verify_file(header.get("path"), "header", header.get("sha256"), required=production)
    build_path = verify_file(build.get("path"), "build", build.get("sha256"), required=production)
    binary_path = verify_file(binary.get("path"), "binary", binary.get("sha256"), required=production)
    csv = payload.get("csv")
    csv_path = None
    if isinstance(csv, dict):
        csv_path = verify_file(csv.get("path"), "csv", csv.get("sha256"), required=production)
    elif production:
        errors.append("csv provenance is required")
    for key, path in (("header", header_path), ("build", build_path),
                      ("binary", binary_path), ("csv", csv_path)):
        if path is not None:
            artifacts[key] = path

    current_config_sha = _sha256(ROOT / "hardware/src/config.py")
    try:
        source_sha = _sha(header.get("source_config_sha256"), "header.source_config_sha256")
        if source_sha != current_config_sha:
            errors.append("header.source_config_sha256 is stale")
    except ValueError as exc:
        errors.append(str(exc))
    try:
        build_json_sha = _sha(header.get("build_json_sha256"), "header.build_json_sha256")
        if build_path is not None and build_json_sha != _sha256(build_path):
            errors.append("header.build_json_sha256 does not match build.json")
    except ValueError as exc:
        if production:
            errors.append(str(exc))

    if not production:
        return errors, artifacts
    if binary_path is not None and not os.access(binary_path, os.X_OK):
        errors.append("binary is not executable")

    case_path = None
    try:
        case_path = _artifact_path(payload.get("case_input"), "case_input", trace_path, required=True)
        case_sha = _sha(payload.get("case_sha256"), "case_sha256")
        if case_path is not None and _sha256(case_path) != case_sha:
            errors.append("case_sha256 does not match case_input")
    except (OSError, ValueError) as exc:
        errors.append(str(exc))
    if case_path is not None:
        artifacts["case"] = case_path

    try:
        build_data = json.loads(build_path.read_text(encoding="utf-8")) if build_path else None
    except (OSError, json.JSONDecodeError) as exc:
        build_data = None
        errors.append(f"build.json cannot be read: {exc}")
    if not isinstance(build_data, dict):
        errors.append("build.json is not an object")
        return errors, artifacts

    profile_mode = payload.get("profile_mode")
    if build_data.get("profile_mode") != profile_mode or build_data.get("build_mode") != payload.get("build_mode"):
        errors.append("build profile/build mode does not match trace")
    if build_data.get("print_first_compile_flag") != EXPECTED_TRACE_COMPILE_FLAG:
        errors.append("build.json print-first compile flag is missing")
    flags = build_data.get("compile_flags")
    if not isinstance(flags, list) or EXPECTED_TRACE_COMPILE_FLAG not in flags:
        errors.append("build.json compile_flags do not contain the print-first flag")
    command = build_data.get("command", build_data.get("compiler_command"))
    if not isinstance(command, list) or EXPECTED_TRACE_COMPILE_FLAG not in command:
        errors.append("build.json compiler command does not contain the print-first flag")
    if build_data.get("source_config_sha256") != current_config_sha:
        errors.append("build.json source_config_sha256 is stale or missing")
    source_config_path = build_data.get("source_config_path")
    try:
        resolved_source = _artifact_path(source_config_path, "build.source_config_path", trace_path, required=True)
        if resolved_source != (ROOT / "hardware/src/config.py").resolve():
            errors.append("build source_config_path is not hardware/src/config.py")
    except (OSError, ValueError) as exc:
        errors.append(str(exc))

    headers = build_data.get("source_headers")
    expected_header_names = {
        "arms.h", "audio.h", "config.h", "control.h", "eyes.h", "gait.h",
        "ik.h", "leg_output.h", "peripherals.h", "print_first_gait.h",
        "profile_config.h", "servos.h", "web_ui.h",
    }
    if not isinstance(headers, list):
        errors.append("build.json source_headers closure is missing")
    else:
        seen: set[str] = set()
        for index, record in enumerate(headers):
            if not isinstance(record, dict):
                errors.append(f"build.source_headers[{index}] is not an object")
                continue
            name = record.get("name")
            seen.add(str(name))
            try:
                source_path = _artifact_path(record.get("source_path"), f"source_headers[{index}].source_path", trace_path, required=True)
                bundle_path = _artifact_path(record.get("bundle_path"), f"source_headers[{index}].bundle_path", trace_path, required=True)
                source_digest = _sha(record.get("source_sha256"), f"source_headers[{index}].source_sha256")
                bundle_digest = _sha(record.get("bundle_sha256"), f"source_headers[{index}].bundle_sha256")
                if source_path is not None and _sha256(source_path) != source_digest:
                    errors.append(f"source_headers[{index}] source SHA mismatch")
                if bundle_path is not None and _sha256(bundle_path) != bundle_digest:
                    errors.append(f"source_headers[{index}] bundle SHA mismatch")
                if source_digest != bundle_digest:
                    errors.append(f"source_headers[{index}] source/bundle differ")
                if source_path is not None and source_path.parent != (ROOT / "firmware/src").resolve():
                    errors.append(f"source_headers[{index}] source is outside firmware/src")
            except (OSError, ValueError) as exc:
                errors.append(str(exc))
        if seen != expected_header_names:
            errors.append(f"source header closure mismatch: {sorted(seen)}")

    source_sha_map = build_data.get("source_sha256")
    if not isinstance(source_sha_map, dict) or set(source_sha_map) != expected_header_names:
        errors.append("build.json source_sha256 header map is incomplete")
    else:
        for name, digest in source_sha_map.items():
            try:
                _sha(digest, f"build.source_sha256.{name}")
                if build_path is not None:
                    bundle = build_path.parent / name
                    if not bundle.is_file() or _sha256(bundle) != digest:
                        errors.append(f"build.source_sha256.{name} does not match bundle")
            except (OSError, ValueError) as exc:
                errors.append(str(exc))

    for field in ("trace_path", "trace_source_path", "binary_path"):
        if field not in build_data:
            errors.append(f"build.json {field} is missing")
    try:
        trace_bundle = _artifact_path(build_data.get("trace_path"), "build.trace_path", trace_path, required=True)
        trace_digest = _sha(build_data.get("trace_sha256"), "build.trace_sha256")
        if trace_bundle is not None and _sha256(trace_bundle) != trace_digest:
            errors.append("build.trace_sha256 does not match trace.cpp")
        trace_source = _artifact_path(build_data.get("trace_source_path"), "build.trace_source_path", trace_path, required=True)
        source_digest = _sha(build_data.get("trace_source_sha256"), "build.trace_source_sha256")
        if trace_source is not None and _sha256(trace_source) != source_digest:
            errors.append("build.trace_source_sha256 does not match source")
        binary_build = _artifact_path(build_data.get("binary_path"), "build.binary_path", trace_path, required=True)
        binary_digest = _sha(build_data.get("binary_sha256"), "build.binary_sha256")
        if binary_build is not None and _sha256(binary_build) != binary_digest:
            errors.append("build.binary_sha256 does not match binary")
        if binary_path is not None and binary_build is not None and binary_path != binary_build:
            errors.append("trace binary path differs from build.json binary_path")
        if binary_path is not None and binary_digest != _sha256(binary_path):
            errors.append("trace binary SHA differs from build.json")
    except (OSError, ValueError) as exc:
        errors.append(str(exc))

    if build_path is None or payload.get("build_json_sha256") != _sha256(build_path):
        errors.append("payload build_json_sha256 does not match build.json")
    if build_path is None or header.get("build_json_sha256") != _sha256(build_path):
        errors.append("header build_json_sha256 does not match build.json")
    if binary_path is not None and binary.get("build_json_sha256") not in (None, header.get("build_json_sha256")):
        errors.append("binary build_json_sha256 does not match header")
    if csv_path is None:
        errors.append("csv file is required in production mode")
    return errors, artifacts


def _row_contract(payload: dict[str, Any], segments: list[dict[str, Any]], rows: list[dict[str, Any]]) -> tuple[list[str], list[tuple[str, tuple[float, float, float], float, float]]]:
    errors: list[str] = []
    specs: list[tuple[str, tuple[float, float, float], float, float]] = []
    default_body_h = None
    if rows and isinstance(rows[0], dict) and isinstance(rows[0].get("command"), dict):
        try:
            default_body_h = _number(rows[0]["command"].get("body_h_mm"), "rows[0].command.body_h_mm")
        except ValueError:
            pass
    names: list[str] = []
    for index, segment in enumerate(segments):
        label = f"segments[{index}]"
        if not isinstance(segment, dict):
            errors.append(f"{label} must be an object")
            continue
        name = segment.get("name")
        if not isinstance(name, str) or not name:
            errors.append(f"{label}.name is invalid")
            continue
        if name in names:
            errors.append(f"duplicate native trace segment: {name}")
            continue
        names.append(name)
        try:
            command = _command(segment.get("command"), label)
            duration = _number(segment.get("duration_s"), f"{label}.duration_s")
            body_h = _number(segment.get("body_h_mm", default_body_h), f"{label}.body_h_mm")
            steps = round(duration * float(S.SERVO_HZ))
            if duration <= 0 or steps <= 0:
                raise ValueError(f"{label}.duration_s must be positive")
            if not math.isclose(duration * float(S.SERVO_HZ), steps, rel_tol=0.0, abs_tol=1e-8):
                raise ValueError(f"{label}.duration_s is not a servo-period multiple")
            specs.append((name, command, duration, body_h))
        except ValueError as exc:
            errors.append(str(exc))
    if not isinstance(rows, list) or not rows:
        errors.append("native trace rows must be a non-empty list")
        return errors, specs
    expected_count = 1 + sum(round(duration * float(S.SERVO_HZ)) for _, _, duration, _ in specs)
    if len(rows) != expected_count:
        errors.append(f"row_count {len(rows)} != expected command rows {expected_count}")
    if payload.get("row_count") != len(rows):
        errors.append("payload row_count does not match rows")
    expected_rows: list[tuple[str, float, float, tuple[float, float, float], float, bool]] = [
        ("initial", 0.0, 0.0, (0.0, 0.0, 0.0), default_body_h or 0.0, False)
    ]
    elapsed = 0.0
    for name, command, duration, body_h in specs:
        for _ in range(round(duration * float(S.SERVO_HZ))):
            elapsed += SERVO_PERIOD
            expected_rows.append((name, elapsed, SERVO_PERIOD, command, body_h,
                                  any(abs(value) > _FLOAT_TOLERANCE for value in command)))
    previous_time = -math.inf
    for index, (row, expected) in enumerate(zip(rows, expected_rows)):
        label = f"rows[{index}]"
        if not isinstance(row, dict):
            errors.append(f"{label} must be an object")
            continue
        name, wanted_time, wanted_dt, wanted_command, wanted_body_h, wanted_moving = expected
        if row.get("index") != index:
            errors.append(f"{label}.index mismatch")
        if row.get("segment") != name:
            errors.append(f"{label}.segment mismatch: {row.get('segment')!r} != {name!r}")
        try:
            time_s = _number(row.get("time_s"), f"{label}.time_s")
            dt_s = _number(row.get("dt_s"), f"{label}.dt_s")
            if time_s + _FLOAT_TOLERANCE < previous_time:
                errors.append(f"{label}.time_s is not monotonic")
            previous_time = time_s
            if not _close(time_s, wanted_time, tolerance=2e-6):
                errors.append(f"{label}.time_s does not match command schedule")
            if not _close(dt_s, wanted_dt, tolerance=2e-6):
                errors.append(f"{label}.dt_s does not match command schedule")
        except ValueError as exc:
            errors.append(str(exc))
        try:
            command = row.get("command")
            actual_command = _command(command, label)
            for axis, actual, wanted in zip(("vx", "vy", "wz"), actual_command, wanted_command):
                if not _close(actual, wanted):
                    errors.append(f"{label}.command.{axis} does not match segment")
            actual_body = _number(command.get("body_h_mm"), f"{label}.command.body_h_mm")
            if not _close(actual_body, wanted_body_h):
                errors.append(f"{label}.command.body_h_mm does not match segment")
        except (AttributeError, ValueError) as exc:
            errors.append(str(exc))
        if row.get("moving") is not wanted_moving:
            errors.append(f"{label}.moving does not match segment command")
    if len(rows) > len(expected_rows):
        errors.append("rows contain extra entries beyond the command schedule")
    row_names = {row.get("segment") for row in rows if isinstance(row, dict)}
    if row_names - set(names) - {"initial"}:
        errors.append("rows contain unknown segment names")
    return errors, specs


def _profile_and_sequence_contract(payload: dict[str, Any], specs: list[tuple[str, tuple[float, float, float], float, float]], *, mode: str) -> tuple[list[str], dict[str, Any]]:
    errors: list[str] = []
    names = [spec[0] for spec in specs]
    static = {name for name, command, _, _ in specs if not any(abs(value) > _FLOAT_TOLERANCE for value in command)}
    dynamic = set(names) - static
    coverage = {
        "static": {
            "status": "PASS" if static else "FAIL",
            "segments": sorted(static),
            "required_labels_present": sorted(REQUIRED_STATIC_LABELS & static),
            "default_label_set_complete": REQUIRED_STATIC_LABELS <= static,
        },
        "dynamic": {
            "status": "PASS" if dynamic else "FAIL",
            "segments": sorted(dynamic),
            "required_direction_labels_present": sorted(REQUIRED_DIRECTION_LABELS & dynamic),
            "default_direction_set_complete": REQUIRED_DIRECTION_LABELS <= dynamic,
        },
    }
    coverage["status"] = "PASS" if static and dynamic else "FAIL"
    coverage["named_default_sequence"] = (
        "PASS" if names == list(STANDARD_SEGMENT_ORDER) else "CUSTOM_OR_INCOMPLETE"
    )
    if not static or not dynamic:
        errors.append("trace must contain both static and moving segments")
    if mode == "production":
        if names != list(STANDARD_SEGMENT_ORDER):
            errors.append("production trace must use the complete named default sequence")
        expected = _standard_segments()
        if len(specs) != len(expected):
            errors.append("production trace has a reduced segment list")
        else:
            for actual, wanted in zip(specs, expected):
                if actual[0] != wanted[0] or actual[1] != wanted[1] or not _close(actual[2], wanted[2]) or not _close(actual[3], wanted[3]):
                    errors.append(f"production segment mismatch: {actual[0]}")
                    break
        profile = payload.get("profile")
        expected_profile = _profile_expected()
        if not isinstance(profile, dict):
            errors.append("production trace profile is required")
        else:
            for key, wanted in expected_profile.items():
                actual = profile.get(key)
                if isinstance(wanted, list):
                    if not isinstance(actual, (list, tuple)) or len(actual) != len(wanted) or any(not _close(a, b) for a, b in zip(actual, wanted)):
                        errors.append(f"profile.{key} does not match config.py")
                elif isinstance(wanted, str):
                    if actual != wanted:
                        errors.append(f"profile.{key} does not match config.py")
                elif not _close(actual, wanted):
                    errors.append(f"profile.{key} does not match config.py")
    return errors, {"coverage": coverage, "segment_names": names}


def _trace_contract(payload: dict[str, Any], trace_path: Path | None = None, *, mode: str = "production") -> dict[str, Any]:
    """trace JSONの構造、出所、20軸範囲、指令区間を検査する。"""
    if mode not in ("production", "test"):
        raise ValueError("mode must be production or test")
    values = SELF._load_native_trace(None, payload=payload)
    errors: list[str] = []
    if payload.get("schema_version") != 1:
        errors.append("native trace schema_version must be 1")
    if payload.get("status") != "GENERATED_NATIVE_TRACE":
        errors.append("native trace status must be GENERATED_NATIVE_TRACE")
    if mode == "production":
        if payload.get("trace_mode") != "production":
            errors.append("production trace_mode is required")
        if payload.get("profile_mode") not in ("candidate_print_first", "frozen_print_first"):
            errors.append("production profile_mode is invalid")
    elif payload.get("trace_mode") not in ("test", "fixture"):
        errors.append("test mode requires trace_mode=test or fixture")
    if payload.get("compile_flag") != EXPECTED_TRACE_COMPILE_FLAG:
        errors.append("native trace must use the print-first C++ compile flag")
    if tuple(payload.get("joint_order") or ()) != tuple(S.ALL_JOINTS):
        errors.append("native trace joint_order is not the 20-axis order")
    try:
        CONTRACT.assert_firmware_matches_config()
    except ValueError as exc:
        errors.append(str(exc))

    segments = payload.get("segments")
    rows = payload.get("rows")
    if not isinstance(segments, list) or not segments:
        errors.append("native trace segments must be a non-empty list")
        segments = []
    if not isinstance(rows, list) or not rows:
        errors.append("native trace rows must be a non-empty list")
        rows = []
    row_errors, specs = _row_contract(payload, segments, rows)
    errors.extend(row_errors)
    sequence_errors, sequence = _profile_and_sequence_contract(payload, specs, mode=mode)
    errors.extend(sequence_errors)

    limits = _joint_limits()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        angles = row.get("angles_deg")
        if not isinstance(angles, dict):
            continue
        for name, (low, high) in limits.items():
            if name not in angles:
                continue
            try:
                value = _number(angles[name], f"rows[{index}].angles_deg.{name}")
            except ValueError as exc:
                errors.append(str(exc))
                continue
            if value < low - _ANGLE_TOLERANCE_DEG or value > high + _ANGLE_TOLERANCE_DEG:
                errors.append(f"rows[{index}].angles_deg.{name} exceeds firmware/config joint limit")

    provenance = {"status": "SKIPPED_TEST_MODE", "errors": []}
    if trace_path is not None:
        provenance_errors, artifacts = _verify_provenance(payload, trace_path, mode=mode)
        provenance = {"status": "PASS" if not provenance_errors else "FAIL",
                      "errors": provenance_errors,
                      "artifacts": {name: str(path) for name, path in artifacts.items()}}
        errors.extend(provenance_errors)
    elif mode == "production":
        errors.append("production provenance requires a trace path")
    return {
        "status": "PASS" if not errors else "FAIL",
        "mode": mode,
        "row_count": len(values),
        "processed_pose_count": len(values),
        "native_pose_count": len(values),
        "axis_count": len(S.ALL_JOINTS),
        "segment_count": len(segments),
        "segment_names": sequence.get("segment_names", []),
        "coverage": sequence.get("coverage", {}),
        "source_config_sha256": payload.get("header", {}).get("source_config_sha256"),
        "header_sha256": payload.get("header", {}).get("sha256"),
        "binary_sha256": payload.get("binary", {}).get("sha256"),
        "provenance": provenance,
        "errors": errors,
        "_segment_specs": specs,
    }


def _audit_contract(audit: dict[str, Any], trace_path: Path, trace_row_count: int, *, mode: str = "production") -> dict[str, Any]:
    """self-collision結果の全姿勢・全リンク対行列を照合する。"""
    errors: list[str] = []
    expected_pairs: int | None = None
    if audit.get("status") != "PASS_FINITE_TRACE":
        errors.append(f"status={audit.get('status')!r}")
    required_true = (
        "finite_pose_sweep_requested", "finite_pose_sweep_complete",
        "finite_pose_sweep_clean", "inputs_unchanged", "pose_count_matches",
        "expected_pose_count_matches",
    )
    for field in required_true:
        if audit.get(field) is not True:
            errors.append(f"{field} is not true")
    if audit.get("coverage") != "finite_native_trace":
        errors.append("coverage is not finite_native_trace")
    if audit.get("link_pair_coverage") != "all_link_pairs":
        errors.append("link_pair_coverage is not all_link_pairs")
    for field in ("processed_pose_count", "native_pose_count", "expected_pose_count", "finite_pose_count"):
        value = audit.get(field)
        if isinstance(value, bool) or not isinstance(value, int):
            errors.append(f"{field} is not an integer")
        elif value != trace_row_count:
            errors.append(f"{field} {value} != trace row count {trace_row_count}")
    link_names = audit.get("link_names")
    if not isinstance(link_names, list) or not link_names or any(not isinstance(name, str) or not name for name in link_names):
        errors.append("link_names is missing or malformed")
        link_names = []
    if len(set(link_names)) != len(link_names):
        errors.append("link_names contains duplicates")
    if mode == "production" and not PRODUCTION_LINKS.issubset(set(link_names)):
        errors.append("production link_names are reduced or incomplete")
    if isinstance(link_names, list) and len(link_names) >= 2:
        expected_pairs = len(list(itertools.combinations(sorted(link_names), 2)))
    value = audit.get("expected_link_pair_count")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        errors.append("expected_link_pair_count is invalid")
    else:
        if expected_pairs is not None and value != expected_pairs:
            errors.append(f"expected_link_pair_count {value} != recomputed {expected_pairs}")
        expected_pairs = value
    for field in ("fixed_base_intersections", "fixed_base_boolean_errors"):
        if audit.get(field):
            errors.append(f"{field} is non-empty")
    for field in ("frame_transform_contract", "proxy_exclusion_compiled"):
        record = audit.get(field)
        if isinstance(record, dict) and record.get("status") == "FAIL":
            errors.append(f"{field} is FAIL")
    inventory = audit.get("print_first_inventory")
    if mode == "production" and (not isinstance(inventory, dict) or inventory.get("status") != "PASS"):
        errors.append("print_first_inventory is not PASS")

    poses = audit.get("poses")
    if not isinstance(poses, list) or len(poses) != trace_row_count:
        errors.append("poses count does not match trace row count")
        poses = []
    wanted_links = sorted(link_names)
    wanted_pair_set = set(itertools.combinations(wanted_links, 2))
    seen_pose_indices: set[int] = set()
    for index, pose in enumerate(poses):
        if not isinstance(pose, dict):
            errors.append(f"pose[{index}] is not an object")
            continue
        pose_index = pose.get("pose_index")
        if isinstance(pose_index, bool) or not isinstance(pose_index, int):
            errors.append(f"pose[{index}].pose_index is invalid")
        else:
            seen_pose_indices.add(pose_index)
            if pose_index != index:
                errors.append(f"pose[{index}].pose_index is not ordered")
        pairs = pose.get("pairs")
        if not isinstance(pairs, list) or expected_pairs is None or len(pairs) != expected_pairs:
            errors.append(f"pose[{index}] does not contain the recomputed link pair count")
            continue
        actual_set: set[tuple[str, str]] = set()
        for pair_index, pair in enumerate(pairs):
            if not isinstance(pair, dict):
                errors.append(f"pose[{index}].pairs[{pair_index}] is not an object")
                continue
            links = pair.get("links")
            if (not isinstance(links, list) or len(links) != 2
                    or any(not isinstance(name, str) for name in links)):
                errors.append(f"pose[{index}].pairs[{pair_index}].links is malformed")
                continue
            key = tuple(links)
            if key in actual_set:
                errors.append(f"pose[{index}] has duplicate link pair {key}")
            actual_set.add(key)
            if key not in wanted_pair_set:
                errors.append(f"pose[{index}] has an omitted/unknown link pair {key}")
            if list(key) != sorted(key):
                errors.append(f"pose[{index}] link pair is not canonicalized: {key}")
            if pair.get("clean") is not True:
                errors.append(f"pose[{index}] link pair {key} is not clean")
            if pair.get("actual_intersections") or pair.get("errors"):
                errors.append(f"pose[{index}] contains an intersection or Boolean error")
        if actual_set != wanted_pair_set:
            errors.append(f"pose[{index}] link pair set has omissions or extras")
    if seen_pose_indices != set(range(trace_row_count)):
        errors.append("pose_index set is truncated, duplicated, or out of order")
    source_digest = audit.get("source_trace_sha256")
    try:
        _sha(source_digest, "audit.source_trace_sha256")
        if source_digest != _sha256(trace_path):
            errors.append("audit source_trace_sha256 does not match trace file")
    except ValueError as exc:
        errors.append(str(exc))
    return {
        "status": "PASS" if not errors else "FAIL",
        "expected_link_pair_count": expected_pairs,
        "processed_pose_count": audit.get("processed_pose_count"),
        "native_pose_count": audit.get("native_pose_count"),
        "errors": errors,
    }


def _verify_deterministic_binary(payload: dict[str, Any],
                                 specs: list[tuple[str, tuple[float, float, float], float, float]],
                                 artifacts: dict[str, Path], *, mode: str) -> list[str]:
    if mode != "production":
        return []
    errors: list[str] = []
    stored = payload.get("native_stdout_sha256")
    try:
        _sha(stored, "native_stdout_sha256")
    except ValueError as exc:
        return [str(exc)]
    binary = artifacts.get("binary")
    if binary is None:
        return ["deterministic binary rerun has no binary artifact"]
    body_h = specs[0][3] if specs else 0.0
    commands = [f"0.0 0.0 0.0 0.0 {body_h}"]
    for _, command, duration, segment_body_h in specs:
        for _ in range(round(duration * float(S.SERVO_HZ))):
            commands.append(f"{SERVO_PERIOD} {command[0]} {command[1]} {command[2]} {segment_body_h}")
    try:
        proc = subprocess.run([str(binary), "ready"], input="\n".join(commands) + "\n",
                              capture_output=True, text=True, check=False)
    except OSError as exc:
        return [f"deterministic binary rerun failed: {exc}"]
    if proc.returncode != 0:
        errors.append(f"deterministic binary rerun exited {proc.returncode}")
    digest = hashlib.sha256(proc.stdout.encode("utf-8")).hexdigest()
    if digest != stored:
        errors.append("same-binary rerun stdout is not deterministic")
    return errors


def check(trace_path: Path, audit_path: Path | None = None, *, mode: str = "production") -> dict[str, Any]:
    if mode not in ("production", "test"):
        raise ValueError("mode must be production or test")
    trace_path = _input_trace_path(Path(trace_path))
    payload = json.loads(trace_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("native trace JSON must be an object")
    trace = _trace_contract(payload, trace_path, mode=mode)
    result: dict[str, Any] = {
        "schema_version": 1,
        "mode": mode,
        "trace_path": str(trace_path),
        "trace_sha256": _sha256(trace_path),
        "trace": {key: value for key, value in trace.items() if not key.startswith("_")},
        "link_pairs": {"status": "UNVERIFIED_NO_AUDIT", "audit_path": None, "audit": None},
        "interpretation": (
            "traceは実C++出力の有限入力を示す。リンク間Boolean、連続姿勢、"
            "印刷材・実機の成立は別監査/現物確認が必要。"
        ),
    }
    deterministic_errors = _verify_deterministic_binary(
        payload, trace.get("_segment_specs", []),
        {key: Path(value) for key, value in trace.get("provenance", {}).get("artifacts", {}).items()},
        mode=mode,
    )
    result["trace"]["deterministic_binary"] = {
        "status": "PASS" if not deterministic_errors else "FAIL",
        "errors": deterministic_errors,
    }
    if deterministic_errors:
        trace["errors"].extend(deterministic_errors)
        result["trace"]["errors"] = trace["errors"]
        trace["status"] = "FAIL"
        result["trace"]["status"] = "FAIL"
    if audit_path is not None:
        audit_path = _input_trace_path(Path(audit_path))
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        if not isinstance(audit, dict):
            raise ValueError("self-collision audit JSON must be an object")
        audit_result = _audit_contract(audit, trace_path, trace["row_count"], mode=mode)
        result["link_pairs"] = {
            "status": audit_result["status"],
            "audit_path": str(audit_path),
            "audit": audit_result,
        }
    elif mode == "production":
        result["link_pairs"] = {
            "status": "FAIL",
            "audit_path": None,
            "audit": {"status": "FAIL", "errors": ["production finite mesh audit is required"]},
        }
    if trace["status"] != "PASS":
        result["status"] = "FAIL"
    elif result["link_pairs"]["status"] != "PASS":
        result["status"] = "FAIL"
    elif mode == "test":
        result["status"] = "PASS_TEST_TRACE_CONTRACT"
    else:
        result["status"] = "PASS_FINITE_TRACE"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace-json", type=Path, required=True)
    parser.add_argument("--audit-json", type=Path)
    parser.add_argument("--mode", choices=("production", "test"), default="production",
                        help="production is the complete default sequence; test permits only explicit fixtures")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    try:
        result = check(args.trace_json, args.audit_json, mode=args.mode)
    except Exception as exc:  # noqa: BLE001 - CLI reports a failed contract
        result = {"schema_version": 1, "mode": args.mode, "status": "FAIL", "error": str(exc)}
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if result["status"] in ("PASS_FINITE_TRACE", "PASS_TEST_TRACE_CONTRACT") else 1


if __name__ == "__main__":
    raise SystemExit(main())
