#!/usr/bin/env python3
"""印刷優先シムの新しい動作品質を判定する。

既存のシミュレーション結果へ判定を追記することはしない。入力結果の
``case`` に ``motion_quality_case: true`` が明示された新規ケースだけを
受け取り、生の軌跡と新しい判定を別のJSONへ保存する。

歩行品質の合否は、通常の物理シムの ``checks`` や過去の判定とは独立に
記録する。stand/shortforward はモデル妥当性診断として保存するが、歩行
品質のPASSへ昇格させない。
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = 1
MARKER = "motion_quality_case"
QUALITY_KINDS = {"long_forward", "turn", "stop", "stop_restart"}
DIAGNOSTIC_KINDS = {
    "model_validity",
    "initial_stand",
    "short_forward",
    "stand",
}


class MotionQualityInputError(ValueError):
    """入力が新しい動作品質ケースの契約を満たさない。"""


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise MotionQualityInputError(f"{label} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise MotionQualityInputError(f"{label} must be a finite number") from exc
    if not math.isfinite(number):
        raise MotionQualityInputError(f"{label} must be finite")
    return number


def _vector(value: Any, length: int, label: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != length:
        raise MotionQualityInputError(f"{label} must have {length} values")
    return [_finite(item, f"{label}[{index}]") for index, item in enumerate(value)]


def _optional_finite_sequence(value: Any, label: str) -> None:
    if value is None:
        return
    if not isinstance(value, (list, tuple)) or not value:
        raise MotionQualityInputError(f"{label} must be a non-empty finite list")
    for index, item in enumerate(value):
        _finite(item, f"{label}[{index}]")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _public_path(path: Path, output_path: Path | None = None) -> str:
    """監査JSONへ絶対ローカルパスを漏らさない表示名を作る。"""
    path = path.resolve()
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        pass
    if output_path is not None:
        output_root = output_path.resolve().parent
        try:
            return "$OUTPUT/" + path.relative_to(output_root).as_posix()
        except ValueError:
            pass
    try:
        digest = _sha256(path)[:16]
    except OSError:
        digest = "unreadable"
    return f"$EXTERNAL/{path.name}#{digest}"


def _publicize_value(value: Any, output_path: Path | None = None) -> Any:
    """source result 内の絶対パスも公開用の役割名へ変換する。"""
    if isinstance(value, dict):
        return {
            key: _publicize_value(item, output_path) for key, item in value.items()
        }
    if isinstance(value, list):
        return [_publicize_value(item, output_path) for item in value]
    if isinstance(value, str) and value.startswith("/"):
        return _public_path(Path(value), output_path)
    return value


def _first_dict(*values: Any) -> dict[str, Any]:
    for value in values:
        if isinstance(value, dict):
            return value
    return {}


def _normalise_segments(value: Any, label: str) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)) or not value:
        raise MotionQualityInputError(f"{label} must be a non-empty string list")
    result = []
    for index, segment in enumerate(value):
        if not isinstance(segment, str) or not segment:
            raise MotionQualityInputError(f"{label}[{index}] must be a non-empty string")
        result.append(segment)
    return result


def quality_case_spec(result: dict[str, Any]) -> dict[str, Any]:
    """入力結果から明示された新ケースの仕様を取り出す。

    ``sim_stress.execute`` のようにケースを ``result.case`` へ格納する
    形式と、fixtureで使うトップレベル形式の両方を受け付ける。マーカーが
    無い過去結果は、ファイル名やケース名から推測して通さない。
    """
    if not isinstance(result, dict):
        raise MotionQualityInputError("result JSON must be an object")
    case = result.get("case")
    if not isinstance(case, dict):
        case = {}
    nested = result.get("motion_quality")
    if not isinstance(nested, dict):
        nested = {}
    case_nested = case.get("motion_quality")
    if not isinstance(case_nested, dict):
        case_nested = {}

    marker = (
        result.get(MARKER) is True
        or case.get(MARKER) is True
        or nested.get(MARKER) is True
        or case_nested.get(MARKER) is True
    )
    if not marker:
        raise MotionQualityInputError(
            "motion_quality_case=true is required; legacy results are not eligible"
        )

    name = (
        result.get("motion_quality_name")
        or case.get("motion_quality_name")
        or nested.get("name")
        or case_nested.get("name")
        or case.get("name")
    )
    if not isinstance(name, str) or not name:
        raise MotionQualityInputError("a non-empty motion-quality case name is required")

    kind = (
        result.get("motion_quality_kind")
        or case.get("motion_quality_kind")
        or nested.get("kind")
        or case_nested.get("kind")
        or case.get("kind")
    )
    if not isinstance(kind, str) or kind not in QUALITY_KINDS | DIAGNOSTIC_KINDS:
        raise MotionQualityInputError(
            "motion_quality_kind must be one of "
            + ", ".join(sorted(QUALITY_KINDS | DIAGNOSTIC_KINDS))
        )

    criteria = _first_dict(
        result.get("motion_quality_criteria"),
        case.get("motion_quality_criteria"),
        nested.get("criteria"),
        case_nested.get("criteria"),
        case.get("quality_criteria"),
    )
    segment_names = _normalise_segments(
        result.get("motion_quality_segments")
        if result.get("motion_quality_segments") is not None
        else case.get("motion_quality_segments")
        if case.get("motion_quality_segments") is not None
        else nested.get("segments")
        if nested.get("segments") is not None
        else case_nested.get("segments"),
        "motion_quality_segments",
    )
    restart_names = _normalise_segments(
        result.get("motion_quality_restart_segments")
        if result.get("motion_quality_restart_segments") is not None
        else case.get("motion_quality_restart_segments")
        if case.get("motion_quality_restart_segments") is not None
        else nested.get("restart_segments")
        if nested.get("restart_segments") is not None
        else case_nested.get("restart_segments"),
        "motion_quality_restart_segments",
    )
    quality_eligible = result.get("quality_eligible")
    if quality_eligible is None:
        quality_eligible = case.get("quality_eligible")
    if quality_eligible is None:
        quality_eligible = kind in QUALITY_KINDS
    if not isinstance(quality_eligible, bool):
        raise MotionQualityInputError("quality_eligible must be boolean")
    if kind in DIAGNOSTIC_KINDS and quality_eligible:
        raise MotionQualityInputError(
            "stand/shortforward diagnostic cases must set quality_eligible=false"
        )
    if kind in QUALITY_KINDS and not quality_eligible:
        raise MotionQualityInputError(
            "long_forward/turn/stop cases must set quality_eligible=true"
        )

    expected_duration_by_segment: dict[str, float] = {}
    source_segments = case.get("segments")
    if isinstance(source_segments, list):
        for index, segment in enumerate(source_segments):
            if not isinstance(segment, dict):
                raise MotionQualityInputError(f"case.segments[{index}] must be an object")
            segment_name = segment.get("name")
            if not isinstance(segment_name, str) or not segment_name:
                raise MotionQualityInputError(
                    f"case.segments[{index}].name must be a non-empty string"
                )
            if "duration" in segment:
                duration = _finite(
                    segment["duration"], f"case.segments[{index}].duration"
                )
                if duration < 0:
                    raise MotionQualityInputError(
                        f"case.segments[{index}].duration must be non-negative"
                    )
                expected_duration_by_segment[segment_name] = duration
    explicit_duration = _first_dict(
        result.get("motion_quality_expected_duration_by_segment_s"),
        case.get("motion_quality_expected_duration_by_segment_s"),
        nested.get("expected_duration_by_segment_s"),
        case_nested.get("expected_duration_by_segment_s"),
    )
    for segment_name, duration in explicit_duration.items():
        if not isinstance(segment_name, str) or not segment_name:
            raise MotionQualityInputError(
                "motion_quality_expected_duration_by_segment_s keys must be strings"
            )
        duration = _finite(
            duration, f"expected duration for {segment_name}"
        )
        if duration < 0:
            raise MotionQualityInputError(
                f"expected duration for {segment_name} must be non-negative"
            )
        expected_duration_by_segment[segment_name] = duration
    if kind == "stop_restart" and (not segment_names or not restart_names):
        raise MotionQualityInputError(
            "stop_restart requires explicit stop and restart segment lists"
        )

    return {
        "name": name,
        "kind": kind,
        "quality_eligible": quality_eligible,
        "segment_names": segment_names,
        "restart_segment_names": restart_names,
        "expected_duration_by_segment_s": expected_duration_by_segment,
        "criteria": criteria,
        "marker": True,
    }


def _parse_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    rows = result.get("timeseries")
    if not isinstance(rows, list) or not rows:
        raise MotionQualityInputError("timeseries must be a non-empty list")
    parsed: list[dict[str, Any]] = []
    previous_time: float | None = None
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise MotionQualityInputError(f"timeseries[{index}] must be an object")
        time = _finite(row.get("time"), f"timeseries[{index}].time")
        if previous_time is not None and time < previous_time:
            raise MotionQualityInputError("timeseries.time must be monotonic")
        previous_time = time
        segment = row.get("segment", row.get("segment_name"))
        if not isinstance(segment, str) or not segment:
            raise MotionQualityInputError(
                f"timeseries[{index}].segment must be a non-empty string"
            )
        position = _vector(row.get("base_pos"), 3, f"timeseries[{index}].base_pos")
        rpy = row.get("rpy_deg", row.get("rpy"))
        rpy = _vector(rpy, 3, f"timeseries[{index}].rpy_deg")
        _optional_finite_sequence(row.get("qpos"), f"timeseries[{index}].qpos")
        _optional_finite_sequence(row.get("torque_nm"), f"timeseries[{index}].torque_nm")
        command = row.get("command")
        if isinstance(command, dict):
            command_values = [
                command.get("vx", 0.0),
                command.get("vy", 0.0),
                command.get("wz", 0.0),
            ]
        elif isinstance(command, (list, tuple)) and len(command) >= 3:
            command_values = list(command[:3])
        else:
            raise MotionQualityInputError(
                f"timeseries[{index}].command must contain vx/vy/wz"
            )
        command_values = _vector(command_values, 3, f"timeseries[{index}].command")
        holding = row.get("holding")
        if holding is not None and not isinstance(holding, bool):
            raise MotionQualityInputError(
                f"timeseries[{index}].holding must be boolean when present"
            )
        parsed.append(
            {
                "time": time,
                "segment": segment,
                "base_pos": position,
                "rpy_deg": rpy,
                "command": command_values,
                "holding": holding,
            }
        )
    return parsed


def _unwrap_degrees(values: list[float]) -> list[float]:
    if not values:
        return []
    result = [0.0]
    previous = values[0]
    accumulated = 0.0
    for value in values[1:]:
        # The native trace is sampled at 10Hz or slower, so a physical step
        # over 180 degrees is an invalidly undersampled input rather than a
        # useful turn. Keep the conventional shortest-step unwrap explicit.
        delta = (value - previous + 180.0) % 360.0 - 180.0
        accumulated += delta
        result.append(accumulated)
        previous = value
    return result


def _trajectory_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        raise MotionQualityInputError("selected motion-quality segment has no rows")
    positions = [row["base_pos"] for row in rows]
    yaws = [row["rpy_deg"][2] for row in rows]
    yaw_cumulative = _unwrap_degrees(yaws)
    start = positions[0]
    deltas = [[point[i] - start[i] for i in range(3)] for point in positions]
    positive_intervals = [
        rows[index]["time"] - rows[index - 1]["time"]
        for index in range(1, len(rows))
        if rows[index]["time"] > rows[index - 1]["time"]
    ]
    return {
        "sample_count": len(rows),
        "time_start_s": rows[0]["time"],
        "time_end_s": rows[-1]["time"],
        "duration_s": rows[-1]["time"] - rows[0]["time"],
        "sample_interval_s": (
            sorted(positive_intervals)[len(positive_intervals) // 2]
            if positive_intervals
            else None
        ),
        "start_base_pos_m": list(start),
        "end_base_pos_m": list(positions[-1]),
        "delta_base_pos_m": list(deltas[-1]),
        "yaw_start_deg": yaws[0],
        "yaw_end_deg": yaws[-1],
        "yaw_cumulative_deg": yaw_cumulative[-1],
        "max_abs_yaw_cumulative_deg": max(abs(value) for value in yaw_cumulative),
        "max_abs_roll_deg": max(abs(row["rpy_deg"][0]) for row in rows),
        "max_abs_pitch_deg": max(abs(row["rpy_deg"][1]) for row in rows),
        "yaw_cumulative_series_deg": yaw_cumulative,
        "base_xy_series_m": [list(point[:2]) for point in positions],
        "holding_fraction": (
            sum(row["holding"] is True for row in rows) / len(rows)
            if any(row["holding"] is not None for row in rows)
            else None
        ),
        "_deltas_xy": [list(delta[:2]) for delta in deltas],
    }


def _selected_rows(
    rows: list[dict[str, Any]],
    names: list[str] | None,
    kind: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    available = []
    for row in rows:
        if row["segment"] not in available:
            available.append(row["segment"])
    if names is None:
        if kind == "long_forward":
            names = [
                name
                for name in available
                if any(
                    math.hypot(row["command"][0], row["command"][1]) > 1e-9
                    and abs(row["command"][2]) <= 1e-9
                    for row in rows
                    if row["segment"] == name
                )
            ]
        elif kind == "turn":
            names = [
                name
                for name in available
                if any(abs(row["command"][2]) > 1e-9 for row in rows if row["segment"] == name)
            ]
        elif kind in {"stop", "stop_restart"}:
            names = [
                name
                for name in available
                if any(
                    max(abs(command) for command in row["command"]) <= 1e-9
                    for row in rows
                    if row["segment"] == name
                )
            ]
        else:
            names = available
    missing = [name for name in names if name not in available]
    if missing:
        raise MotionQualityInputError(
            f"selected segment(s) missing from timeseries: {', '.join(missing)}"
        )
    selected = [row for row in rows if row["segment"] in names]
    if not selected:
        raise MotionQualityInputError("selected motion-quality segment has no rows")
    return selected, names


def _criteria_number(criteria: dict[str, Any], name: str, default: float) -> float:
    value = criteria.get(name, default)
    number = _finite(value, f"criteria.{name}")
    if number < 0:
        raise MotionQualityInputError(f"criteria.{name} must be non-negative")
    return number


def _command_consistency(
    rows: list[dict[str, Any]], criteria: dict[str, Any]
) -> tuple[bool, float, float]:
    """指令区間が途中で別の方向や角速度へ変わっていないか確認する。"""
    tolerance = _criteria_number(criteria, "command_tolerance", 1e-9)
    reference = rows[0]["command"]
    maximum_delta = max(
        (
            max(abs(row["command"][index] - reference[index]) for index in range(3))
            for row in rows
        ),
        default=0.0,
    )
    return maximum_delta <= tolerance, maximum_delta, tolerance


def _expected_command(
    rows: list[dict[str, Any]], criteria: dict[str, Any], label: str
) -> tuple[list[float], float, float]:
    """ケースで明示した指令ベクトルと実指令を突合する。"""
    if "expected_command_vector" not in criteria:
        raise MotionQualityInputError(
            f"{label} requires criteria.expected_command_vector"
        )
    expected = _vector(
        criteria["expected_command_vector"], 3,
        f"criteria.expected_command_vector for {label}",
    )
    tolerance = _criteria_number(
        criteria,
        "expected_command_tolerance",
        _criteria_number(criteria, "command_tolerance", 1e-9),
    )
    maximum_delta = max(
        (
            max(abs(row["command"][index] - expected[index]) for index in range(3))
            for row in rows
        ),
        default=0.0,
    )
    return expected, maximum_delta, tolerance


def _minimum_duration(
    rows: list[dict[str, Any]],
    criteria: dict[str, Any],
    expected_duration_s: float | None,
) -> tuple[float | None, float | None]:
    """ケース指定時間をサンプルの欠損でPASSにしない下限を返す。"""
    if "min_duration_s" in criteria:
        minimum = _criteria_number(criteria, "min_duration_s", 0.0)
        return expected_duration_s, minimum
    if expected_duration_s is None:
        return None, None
    intervals = [
        rows[index]["time"] - rows[index - 1]["time"]
        for index in range(1, len(rows))
        if rows[index]["time"] > rows[index - 1]["time"]
    ]
    if not intervals:
        interval = 0.1
    else:
        interval = sorted(intervals)[len(intervals) // 2]
    # 10Hz sampled traces have a half-second boundary allowance. The case
    # plan records this as a concrete 59.5s minimum for a requested 60s run.
    tolerance = max(0.5, float(interval) * 2.0)
    return expected_duration_s, max(0.0, expected_duration_s - tolerance)


def _evaluate_forward(
    rows: list[dict[str, Any]],
    criteria: dict[str, Any],
    *,
    label: str,
    expected_duration_s: float | None = None,
) -> dict[str, Any]:
    metrics = _trajectory_metrics(rows)
    command = rows[0]["command"]
    planar_speed = math.hypot(command[0], command[1])
    if planar_speed <= 1e-9:
        raise MotionQualityInputError(f"{label} command has no planar direction")
    command_consistent, command_delta, command_tolerance = _command_consistency(rows, criteria)
    expected_command, expected_delta, expected_tolerance = _expected_command(
        rows, criteria, label
    )
    command_is_forward = all(
        math.hypot(row["command"][0], row["command"][1]) > 1e-9
        and abs(row["command"][2]) <= command_tolerance
        for row in rows
    )
    expected_is_forward = (
        math.hypot(expected_command[0], expected_command[1]) > expected_tolerance
        and abs(expected_command[2]) <= expected_tolerance
    )
    yaw_start = math.radians(rows[0]["rpy_deg"][2])
    body_command = [command[0] / planar_speed, command[1] / planar_speed]
    direction = [
        math.cos(yaw_start) * body_command[0] - math.sin(yaw_start) * body_command[1],
        math.sin(yaw_start) * body_command[0] + math.cos(yaw_start) * body_command[1],
    ]
    lateral = [-direction[1], direction[0]]
    delta_series = metrics["_deltas_xy"]
    progress_series = [
        delta[0] * direction[0] + delta[1] * direction[1]
        for delta in delta_series
    ]
    lateral_series = [
        delta[0] * lateral[0] + delta[1] * lateral[1]
        for delta in [
            [point[0] - rows[0]["base_pos"][0], point[1] - rows[0]["base_pos"][1]]
            for point in [row["base_pos"] for row in rows]
        ]
    ]
    max_yaw = _criteria_number(criteria, "max_yaw_cumulative_deg", 10.0)
    min_progress = _criteria_number(criteria, "min_progress_m", 0.0)
    lateral_floor = _criteria_number(criteria, "max_lateral_mm", 30.0) / 1000.0
    lateral_fraction = _criteria_number(criteria, "lateral_fraction", 0.25)
    expected_duration_s, min_duration_s = _minimum_duration(
        rows, criteria, expected_duration_s
    )
    progress = progress_series[-1]
    lateral_limit = max(lateral_floor, max(progress, 0.0) * lateral_fraction)
    metrics.update(
        {
            "command_body_vx": command[0],
            "command_body_vy": command[1],
            "command_direction_world_xy": direction,
            "progress_series_m": progress_series,
            "lateral_series_m": lateral_series,
            "progress_m": progress,
            "endpoint_lateral_m": lateral_series[-1],
            "max_abs_lateral_m": max(abs(value) for value in lateral_series),
            "max_yaw_cumulative_deg_limit": max_yaw,
            "min_progress_m": min_progress,
            "max_lateral_floor_m": lateral_floor,
            "lateral_fraction": lateral_fraction,
            "lateral_limit_m": lateral_limit,
            "expected_duration_s": expected_duration_s,
            "min_duration_s": min_duration_s,
            "command_max_delta": command_delta,
            "command_tolerance": command_tolerance,
            "expected_command_vector": expected_command,
            "expected_command_max_delta": expected_delta,
            "expected_command_tolerance": expected_tolerance,
        }
    )
    checks = {
        "positive_progress": progress >= min_progress,
        "yaw_cumulative_within_limit": metrics["max_abs_yaw_cumulative_deg"] <= max_yaw,
        "lateral_within_limit": metrics["max_abs_lateral_m"] <= lateral_limit,
        "command_matches_expected": expected_delta <= expected_tolerance,
        "expected_command_is_forward": expected_is_forward,
        "command_constant_forward": (
            command_consistent and command_is_forward and expected_is_forward
            and expected_delta <= expected_tolerance
        ),
        "complete": min_duration_s is None or metrics["duration_s"] >= min_duration_s,
    }
    return {
        "kind": label,
        "metrics": metrics,
        "criteria": {
            "max_yaw_cumulative_deg": max_yaw,
            "min_progress_m": min_progress,
            "max_lateral_mm": lateral_floor * 1000.0,
            "lateral_fraction": lateral_fraction,
            "lateral_limit_m": lateral_limit,
            "expected_command_vector": expected_command,
            "expected_command_tolerance": expected_tolerance,
            "expected_duration_s": expected_duration_s,
            "min_duration_s": min_duration_s,
        },
        "checks": checks,
        "passed": all(checks.values()),
    }


def _evaluate_turn(
    rows: list[dict[str, Any]],
    criteria: dict[str, Any],
    *,
    expected_duration_s: float | None = None,
) -> dict[str, Any]:
    metrics = _trajectory_metrics(rows)
    wz = rows[0]["command"][2]
    if abs(wz) <= 1e-9:
        raise MotionQualityInputError("turn command has no angular velocity")
    command_consistent, command_delta, command_tolerance = _command_consistency(rows, criteria)
    expected_command, expected_delta, expected_tolerance = _expected_command(
        rows, criteria, "turn"
    )
    command_is_turn = all(
        math.hypot(row["command"][0], row["command"][1]) <= command_tolerance
        and abs(row["command"][2]) > command_tolerance
        for row in rows
    )
    expected_sign = criteria.get("expected_turn_sign", 1 if wz > 0 else -1)
    expected_sign = _finite(expected_sign, "criteria.expected_turn_sign")
    if expected_sign not in (-1.0, 1.0):
        raise MotionQualityInputError("criteria.expected_turn_sign must be -1 or 1")
    expected_is_pure_turn = (
        math.hypot(expected_command[0], expected_command[1]) <= expected_tolerance
        and abs(expected_command[2]) > expected_tolerance
    )
    expected_sign_matches_command = expected_command[2] * expected_sign > expected_tolerance
    command_sign_matches = all(
        row["command"][2] * expected_sign > command_tolerance for row in rows
    )
    min_turn = _criteria_number(criteria, "min_turn_deg", 10.0)
    expected_duration_s, min_duration_s = _minimum_duration(
        rows, criteria, expected_duration_s
    )
    signed = metrics["yaw_cumulative_deg"]
    checks = {
        "turn_sign": signed * expected_sign > 0.0,
        "sufficient_turn": abs(signed) >= min_turn,
        "command_pure_rotation": command_is_turn and expected_is_pure_turn,
        "command_turn_sign": command_sign_matches,
        "expected_command_sign": expected_sign_matches_command,
        "command_matches_expected": expected_delta <= expected_tolerance,
        "command_constant_turn": (
            command_consistent and command_is_turn and expected_is_pure_turn
            and command_sign_matches and expected_sign_matches_command
            and expected_delta <= expected_tolerance
        ),
        "complete": min_duration_s is None or metrics["duration_s"] >= min_duration_s,
    }
    metrics.update(
        {
            "command_wz": wz,
            "command_vx": rows[0]["command"][0],
            "command_vy": rows[0]["command"][1],
            "expected_turn_sign": int(expected_sign),
            "signed_turn_deg": signed,
            "turn_magnitude_deg": abs(signed),
            "expected_duration_s": expected_duration_s,
            "min_duration_s": min_duration_s,
            "command_max_delta": command_delta,
            "command_tolerance": command_tolerance,
            "expected_command_vector": expected_command,
            "expected_command_max_delta": expected_delta,
            "expected_command_tolerance": expected_tolerance,
        }
    )
    return {
        "kind": "turn",
        "metrics": metrics,
        "criteria": {
            "expected_turn_sign": int(expected_sign),
            "expected_command_vector": expected_command,
            "expected_command_tolerance": expected_tolerance,
            "min_turn_deg": min_turn,
            "expected_duration_s": expected_duration_s,
            "min_duration_s": min_duration_s,
        },
        "checks": checks,
        "passed": all(checks.values()),
    }


def _evaluate_stop(
    rows: list[dict[str, Any]],
    criteria: dict[str, Any],
    *,
    expected_duration_s: float | None = None,
) -> dict[str, Any]:
    metrics = _trajectory_metrics(rows)
    xy_drift = []
    start = rows[0]["base_pos"]
    for row in rows:
        dx = row["base_pos"][0] - start[0]
        dy = row["base_pos"][1] - start[1]
        xy_drift.append(math.hypot(dx, dy))
    max_drift = max(xy_drift)
    final_drift = xy_drift[-1]
    command_consistent, command_delta, command_tolerance = _command_consistency(rows, criteria)
    all_zero = all(max(abs(value) for value in row["command"]) <= 1e-9 for row in rows)
    max_drift_mm = _criteria_number(criteria, "max_stop_drift_mm", 30.0)
    max_yaw = _criteria_number(criteria, "max_stop_yaw_deg", 2.0)
    expected_duration_s, min_duration_s = _minimum_duration(
        rows, criteria, expected_duration_s
    )
    holding_values = [row.get("holding") for row in rows]
    holding_observed = any(value is not None for value in holding_values)
    holding_reached = any(value is True for value in holding_values)
    # A stop that merely has small positional drift is not enough: the
    # firmware gait must have entered its explicit holding state.  Requiring
    # the final sampled row to remain in holding prevents a brief transient
    # flag from turning a still-active gait into a stop pass.
    holding_at_end = bool(holding_values and holding_values[-1] is True)
    holding_tail_count = 0
    for value in reversed(holding_values):
        if value is True:
            holding_tail_count += 1
        else:
            break
    metrics.update(
        {
            "base_xy_drift_series_m": xy_drift,
            "max_base_xy_drift_m": max_drift,
            "final_base_xy_drift_m": final_drift,
            "max_stop_drift_mm_limit": max_drift_mm,
            "max_stop_yaw_deg_limit": max_yaw,
            "all_commands_zero": all_zero,
            "holding_observed": holding_observed,
            "holding_reached": holding_reached,
            "holding_at_end": holding_at_end,
            "holding_tail_sample_count": holding_tail_count,
            "expected_duration_s": expected_duration_s,
            "min_duration_s": min_duration_s,
            "command_max_delta": command_delta,
            "command_tolerance": command_tolerance,
        }
    )
    checks = {
        "stop_commands_zero": all_zero,
        "base_drift_within_limit": max_drift <= max_drift_mm / 1000.0,
        "yaw_drift_within_limit": metrics["max_abs_yaw_cumulative_deg"] <= max_yaw,
        "command_constant_stop": command_consistent,
        "gait_holding_reached": holding_observed and holding_reached and holding_at_end,
        "complete": min_duration_s is None or metrics["duration_s"] >= min_duration_s,
    }
    return {
        "kind": "stop",
        "metrics": metrics,
        "criteria": {
            "max_stop_drift_mm": max_drift_mm,
            "max_stop_yaw_deg": max_yaw,
            "expected_duration_s": expected_duration_s,
            "min_duration_s": min_duration_s,
        },
        "checks": checks,
        "passed": all(checks.values()),
    }


def _public_metrics(value: Any) -> Any:
    """内部計算用の一時キーを出力から除く。"""
    if isinstance(value, dict):
        return {key: _public_metrics(item) for key, item in value.items() if not key.startswith("_")}
    if isinstance(value, list):
        return [_public_metrics(item) for item in value]
    return value


UPSTREAM_REQUIRED_CHECKS = (
    "completed",
    "numeric_stability",
    "no_fall",
    "initial_self_penetration_le_0p1mm",
    "inputs_unchanged",
)
UPSTREAM_UNSAFE_STATUSES = {
    "INVALID_INITIAL_CONTACT_MODEL",
    "NUMERICAL_FAILURE",
    "FALL",
    "UNVERIFIED",
    "INPUT_ERROR",
}
UPSTREAM_PENDING_STATUSES = {
    # A final simulation result is intentionally provisional until the
    # independent exact-source proxy proof is combined.  Treat that boundary
    # as UNVERIFIED in motion-quality aggregation, rather than calling the
    # trajectory a physical FAIL merely because its status is pending.
    "PENDING_EXTERNAL_PROXY_PROOF",
}


def _upstream_assessment(result: dict[str, Any]) -> dict[str, Any]:
    """新しい軌跡判定を上流の物理入力状態と結合する。"""
    source_status = result.get("status")
    checks = result.get("checks")
    case = result.get("case") if isinstance(result.get("case"), dict) else {}
    model = case.get("model") if isinstance(case.get("model"), dict) else {}
    final_model_kinds = {"final_integrated", "print_first_final", "frozen_integrated"}
    final_model = model.get("model_kind") in final_model_kinds
    parent_collision_ok = (not final_model) or model.get("include_parent_collision") is True
    if not isinstance(checks, dict):
        return {
            "status": "NOT_PROVIDED",
            "usable": False,
            "source_status": source_status,
            "required_checks": list(UPSTREAM_REQUIRED_CHECKS),
            "missing_checks": list(UPSTREAM_REQUIRED_CHECKS),
            "failed_checks": [],
            "all_boolean_checks": False,
            "final_model": final_model,
            "include_parent_collision": model.get("include_parent_collision"),
            "model_input_ok": parent_collision_ok,
        }
    missing = [name for name in UPSTREAM_REQUIRED_CHECKS if name not in checks]
    failed = [name for name in UPSTREAM_REQUIRED_CHECKS if checks.get(name) is not True]
    failed.extend(
        name
        for name, value in checks.items()
        if isinstance(value, bool) and value is not True and name not in failed
    )
    all_boolean_checks = all(
        value is True for value in checks.values() if isinstance(value, bool)
    )
    if source_status in UPSTREAM_UNSAFE_STATUSES:
        status = "UNSAFE_SOURCE_STATUS"
    elif source_status is None:
        status = "SOURCE_STATUS_MISSING"
    elif source_status in UPSTREAM_PENDING_STATUSES:
        # Pending is a provisional boundary only when the finite simulation
        # has still passed every required internal check.  Do not let a
        # forged pending label hide a failed/no-fall/changed-input check.
        if missing:
            status = "REQUIRED_CHECK_MISSING"
        elif failed or not all_boolean_checks:
            status = "UPSTREAM_CHECK_FAILED"
        else:
            status = "EXTERNAL_PROXY_PROOF_PENDING"
    elif source_status not in (None, "PASS"):
        status = "SOURCE_RESULT_NOT_PASS"
    elif missing:
        status = "REQUIRED_CHECK_MISSING"
    elif failed or not all_boolean_checks:
        status = "UPSTREAM_CHECK_FAILED"
    elif not parent_collision_ok:
        status = "FINAL_MODEL_PARENT_COLLISION_DISABLED"
    else:
        status = "PASS"
    return {
        "status": status,
        "usable": status == "PASS",
        "source_status": source_status,
        "required_checks": list(UPSTREAM_REQUIRED_CHECKS),
        "missing_checks": missing,
        "failed_checks": failed,
        "all_boolean_checks": all_boolean_checks,
        "final_model": final_model,
        "include_parent_collision": model.get("include_parent_collision"),
        "model_input_ok": parent_collision_ok,
    }


def _combine_status(trajectory_status: str, upstream: dict[str, Any]) -> str:
    if trajectory_status != "PASS":
        return trajectory_status
    if upstream["status"] == "PASS":
        return "PASS"
    if upstream["status"] in {"SOURCE_RESULT_NOT_PASS", "UPSTREAM_CHECK_FAILED"}:
        return "FAIL"
    return "UNVERIFIED"


def _groups(rows: list[dict[str, Any]], names: list[str]) -> list[tuple[str, list[dict[str, Any]]]]:
    result = []
    for name in names:
        group = [row for row in rows if row["segment"] == name]
        if not group:
            raise MotionQualityInputError(f"selected segment has no rows: {name}")
        result.append((name, group))
    return result


def judge_case(result: dict[str, Any]) -> dict[str, Any]:
    """一つの新規結果を入力検証し、動作品質の判定を返す。"""
    spec = quality_case_spec(result)
    rows = _parse_rows(result)
    selected, segment_names = _selected_rows(rows, spec["segment_names"], spec["kind"])
    criteria = spec["criteria"]
    upstream = _upstream_assessment(result)
    if spec["kind"] in DIAGNOSTIC_KINDS:
        metrics = _trajectory_metrics(selected)
        # 妥当性診断は軌跡を保存するための情報であり、歩行品質の合否に
        # 変換しない。ここで ``passed`` を作らないことが契約上重要。
        return {
            "case": spec,
            "status": "MODEL_VALIDITY_ONLY",
            "quality_eligible": False,
            "quality_pass": None,
            "selected_segments": segment_names,
            "metrics": _public_metrics(metrics),
            "checks": {},
            "upstream": upstream,
            "combined_status": "MODEL_VALIDITY_ONLY",
            "interpretation": "stand/shortforward is a model-validity diagnostic only",
        }

    evaluations: list[dict[str, Any]] = []
    expected_durations = spec["expected_duration_by_segment_s"]
    if spec["kind"] == "long_forward":
        for name, group in _groups(rows, segment_names):
            evaluation = _evaluate_forward(
                group,
                criteria,
                label="long_forward",
                expected_duration_s=expected_durations.get(name),
            )
            evaluation["segment"] = name
            evaluations.append(evaluation)
    elif spec["kind"] == "turn":
        for name, group in _groups(rows, segment_names):
            evaluation = _evaluate_turn(
                group,
                criteria,
                expected_duration_s=expected_durations.get(name),
            )
            evaluation["segment"] = name
            evaluations.append(evaluation)
    elif spec["kind"] == "stop":
        for name, group in _groups(rows, segment_names):
            evaluation = _evaluate_stop(
                group,
                criteria,
                expected_duration_s=expected_durations.get(name),
            )
            evaluation["segment"] = name
            evaluations.append(evaluation)
    elif spec["kind"] == "stop_restart":
        for name, group in _groups(rows, segment_names):
            evaluation = _evaluate_stop(
                group,
                criteria,
                expected_duration_s=expected_durations.get(name),
            )
            evaluation["segment"] = name
            evaluations.append(evaluation)
        restart_names = spec["restart_segment_names"]
        for name, restart_rows in _groups(rows, restart_names or []):
            restart_criteria = _first_dict(
                criteria.get("restart"), criteria.get("restart_criteria")
            )
            restart = _evaluate_forward(
                restart_rows,
                restart_criteria,
                label="restart",
                expected_duration_s=expected_durations.get(name),
            )
            restart["segment"] = name
            evaluations.append(restart)

    trajectory_passed = all(item["passed"] for item in evaluations)
    trajectory_status = "PASS" if trajectory_passed else "FAIL"
    combined_status = _combine_status(trajectory_status, upstream)
    return {
        "case": spec,
        "status": combined_status,
        "trajectory_status": trajectory_status,
        "combined_status": combined_status,
        "quality_eligible": True,
        "quality_pass": trajectory_passed,
        "combined_pass": combined_status == "PASS",
        "selected_segments": segment_names,
        "evaluations": _public_metrics(evaluations),
        "checks": {
            f"{index}:{item['kind']}": item["passed"]
            for index, item in enumerate(evaluations)
        },
        "upstream": upstream,
        "interpretation": (
            "new motion-quality criteria are evaluated from the raw base trajectory; "
            "the source result status is preserved separately"
        ),
    }


def _source_record(path: Path | None, result: dict[str, Any], output_path: Path | None) -> dict[str, Any]:
    record: dict[str, Any] = {
        "path": _public_path(path, output_path) if path else None,
        "sha256": _sha256(path) if path else None,
        "source_status": result.get("status"),
        "source_checks": _publicize_value(result.get("checks"), output_path),
        "case": _publicize_value(result.get("case"), output_path),
    }
    return record


def evaluate_result_document(
    result: dict[str, Any],
    *,
    source_path: Path | None = None,
    output_path: Path | None = None,
) -> dict[str, Any]:
    """生データと判定を別キーへ格納した一件分の報告を返す。"""
    raw = {
        "source_result": _source_record(source_path, result, output_path),
        "timeseries": result.get("timeseries"),
    }
    try:
        judgement = judge_case(result)
    except MotionQualityInputError as exc:
        judgement = {
            "status": "INPUT_ERROR",
            "combined_status": "INPUT_ERROR",
            "trajectory_status": None,
            "quality_eligible": False,
            "quality_pass": None,
            "checks": {},
            "errors": [str(exc)],
        }
    return {"raw": raw, "judgement": judgement}


def aggregate_status(records: list[dict[str, Any]]) -> str:
    """正式4品質ケースの網羅性を含めて、報告全体の状態を集約する。"""
    statuses = [record["judgement"].get("status") for record in records]
    if not statuses:
        return "INPUT_ERROR"
    if "INPUT_ERROR" in statuses:
        return "INPUT_ERROR"
    if "FAIL" in statuses:
        return "FAIL"
    if "UNVERIFIED" in statuses:
        return "UNVERIFIED"
    if any(status == "PASS" for status in statuses):
        coverage = formal_quality_coverage(records)
        if coverage["complete_and_passed"]:
            return "PASS"
        # A diagnostic-only report, a missing turn direction, or a partial
        # formal set must never look like an all-quality PASS.  Keep a
        # distinction from a measured failing case: the required evidence is
        # incomplete, so the aggregate is UNVERIFIED.
        return "UNVERIFIED"
    return "MODEL_VALIDITY_ONLY"


FORMAL_QUALITY_CASE_KEYS = ("forward", "turn_positive", "turn_negative", "stop_restart")


def _formal_quality_case_key(judgement: dict[str, Any]) -> str | None:
    """一件の判定を正式4ケースのどれかへ割り当てる。"""
    spec = judgement.get("case")
    if not isinstance(spec, dict) or spec.get("quality_eligible") is not True:
        return None
    kind = spec.get("kind")
    if kind == "long_forward":
        return "forward"
    if kind == "stop_restart":
        return "stop_restart"
    if kind == "turn":
        criteria = spec.get("criteria")
        if isinstance(criteria, dict):
            sign = criteria.get("expected_turn_sign")
            try:
                sign = float(sign)
            except (TypeError, ValueError):
                sign = 0.0
            if sign == 1.0:
                return "turn_positive"
            if sign == -1.0:
                return "turn_negative"
    return None


def formal_quality_coverage(records: list[dict[str, Any]]) -> dict[str, Any]:
    """forward/turn±/stop_restartの存在・適格・合格を個別に返す。"""
    entries = {key: [] for key in FORMAL_QUALITY_CASE_KEYS}
    for record in records:
        judgement = record.get("judgement", {})
        key = _formal_quality_case_key(judgement)
        if key is None:
            continue
        entries[key].append({
            "name": (judgement.get("case") or {}).get("name"),
            "status": judgement.get("status"),
            "quality_eligible": judgement.get("quality_eligible") is True,
            "quality_pass": judgement.get("quality_pass") is True,
            "combined_pass": judgement.get("combined_pass") is True,
        })
    cases = {}
    for key, values in entries.items():
        # Duplicate cases are not allowed to hide a missing/failed required
        # direction.  Every supplied instance must be eligible and PASS, and
        # at least one instance must exist.
        passed = bool(values) and all(
            item["status"] == "PASS"
            and item["quality_eligible"]
            and item["quality_pass"]
            and item["combined_pass"]
            for item in values
        )
        cases[key] = {"present": bool(values), "passed": passed, "records": values}
    missing = [key for key, value in cases.items() if not value["present"]]
    failed = [key for key, value in cases.items()
              if value["present"] and not value["passed"]]
    return {
        "required_cases": list(FORMAL_QUALITY_CASE_KEYS),
        "cases": cases,
        "missing_cases": missing,
        "failed_cases": failed,
        "complete_and_passed": not missing and not failed,
        "interpretation": "正式動作品質の全体PASSにはforward、turn正方向、turn負方向、stop_restartの4ケースが各々eligibleかつcombined PASSで必要。stand/short_forward診断は代用しない。",
    }


def build_report(input_paths: list[Path], output_path: Path | None = None) -> dict[str, Any]:
    records = []
    for path in input_paths:
        try:
            result = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            records.append(
                {
                    "raw": {
                        "source_result": {
                            "path": _public_path(path, output_path),
                            "sha256": _sha256(path) if path.is_file() else None,
                        },
                        "timeseries": None,
                    },
                    "judgement": {
                        "status": "INPUT_ERROR",
                        "quality_eligible": False,
                        "quality_pass": None,
                        "checks": {},
                        "errors": [f"cannot read JSON: {exc}"],
                    },
                }
            )
            continue
        records.append(
            evaluate_result_document(result, source_path=path, output_path=output_path)
        )
    coverage = formal_quality_coverage(records)
    return {
        "schema_version": SCHEMA_VERSION,
        "report_type": "print_first_motion_quality",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": aggregate_status(records),
        "formal_quality_coverage": coverage,
        "source_results_unchanged": True,
        "records": records,
        "interpretation": (
            "This report is additive. It never edits or backfills historical result JSON. "
            "MODEL_VALIDITY_ONLY is deliberately excluded from motion-quality PASS."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        "--result",
        dest="inputs",
        action="append",
        type=Path,
        required=True,
        help="explicitly marked new motion-quality result JSON (repeatable)",
    )
    parser.add_argument("--output", type=Path, required=True, help="new report JSON path")
    parser.add_argument(
        "--diagnostic",
        action="store_true",
        help="diagnostic-only run: allow MODEL_VALIDITY_ONLY to exit zero",
    )
    args = parser.parse_args(argv)
    output = args.output.resolve()
    input_paths = [path.resolve() for path in args.inputs]
    if output in input_paths:
        parser.error("--output must be a new file; source result JSON is never rewritten")
    report = build_report(input_paths, output)
    report["execution_mode"] = "diagnostic" if args.diagnostic else "quality"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(
        json.dumps(
            {
                "status": report["status"],
                "cases": len(report["records"]),
                "output": _public_path(output),
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["status"] == "PASS" or (
        args.diagnostic and report["status"] == "MODEL_VALIDITY_ONLY"
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
