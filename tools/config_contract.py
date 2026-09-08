#!/usr/bin/env python3
"""``hardware/src/config.py`` と firmware 設定の一方向契約。

Python 側の幾何・歩容・関節制限を設計入力の正本とし、組込み用の
``firmware/src/config.h`` はその値を C++ の型へ写したものとして検査する。
このモジュールは firmware の値を Python の既定値として読み込まないため、
両方に同じ誤値を書いて自己比較を通す経路を作らない。
"""
from __future__ import annotations

import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_H = ROOT / "firmware" / "src" / "config.h"
sys.path.insert(0, str(ROOT / "hardware" / "src"))
import config as C  # noqa: E402


_NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
_INTEGER = r"[+-]?\d+"
_TOLERANCE = 0.05
# D_KNEE_* は歩容の円形射影の境界値で、数値を丸めると境界付近の
# IK が別の姿勢を選ぶ。一般の設計値と同じ 0.05mm の許容を使わず、
# config.py から生成したリテラルを 1e-5mm 以内で厳格に比較する。
_STRICT_SCALAR_TOLERANCES = {
    "D_KNEE_MAX": 1e-5,
    "D_KNEE_MIN": 1e-5,
}


def _scalar(text: str, name: str) -> float:
    match = re.search(
        rf"(?:^\s*constexpr\s+(?:float|double|int|long)\s+|,\s*)"
        rf"{re.escape(name)}\s*=\s*({_NUMBER})f?\s*(?:,|;)",
        text,
        re.MULTILINE,
    )
    if not match:
        raise ValueError(f"firmware config.h に {name} のスカラー定義がない")
    return float(match.group(1))


def _array(text: str, name: str) -> list[float]:
    # C++ の一次元/二次元配列どちらも、セミコロンまでの初期化子を
    # まとめて拾って数値トークンだけを取り出す。
    match = re.search(
        rf"^\s*constexpr\s+(?:float|double|int|long)\s+{re.escape(name)}"
        rf"(?:\[[^\]]+\])+\s*=\s*(\{{[^;]*\}})\s*;",
        text,
        re.MULTILINE,
    )
    if not match:
        raise ValueError(f"firmware config.h に {name} の配列定義がない")
    return [float(value) for value in re.findall(_NUMBER, match.group(1))]


def _integer_array(text: str, name: str) -> list[int]:
    """C++ の配線/チャンネル配列を10進・16進の両方で読む。"""
    match = re.search(
        rf"^\s*constexpr\s+(?:float|double|int|long|uint8_t|uint16_t|uint32_t)\s+"
        rf"{re.escape(name)}(?:\[[^\]]+\])+\s*=\s*(\{{[^;]*\}})\s*;",
        text,
        re.MULTILINE,
    )
    if not match:
        raise ValueError(f"firmware config.h に {name} の配列定義がない")
    return [int(value, 0) for value in re.findall(r"0[xX][0-9a-fA-F]+|-?\d+", match.group(1))]


def _constant_number(text: str, name: str) -> float:
    """型名の追加で既存契約を壊さず、電装スカラーを読む。"""
    match = re.search(
        rf"(?:^\s*constexpr\s+[\w:<>, ]+\s+|,\s*){re.escape(name)}\s*=\s*"
        rf"({_NUMBER})f?\s*(?:,|;)",
        text,
        re.MULTILINE,
    )
    if not match:
        raise ValueError(f"firmware config.h に {name} の電装定義がない")
    return float(match.group(1))


def _vbat_div_parts(text: str) -> tuple[float, float, float]:
    match = re.search(
        r"constexpr\s+float\s+VBAT_DIV\s*=\s*\(\s*(\d+(?:\.\d+)?)f?\s*\+\s*"
        r"(\d+(?:\.\d+)?)f?\s*\)\s*/\s*(\d+(?:\.\d+)?)f?\s*;",
        text,
    )
    if not match:
        raise ValueError("firmware config.h に VBAT_DIV の分圧式がない")
    return tuple(float(match.group(index)) for index in (1, 2, 3))


def canonical_electrical_values() -> dict[str, dict[str, list[int] | float]]:
    """config.py の電装正本を firmware の比較形へ正規化する。"""
    source = getattr(C, "FIRMWARE_ELECTRICAL", None)
    if not isinstance(source, dict):
        raise ValueError("config.py に FIRMWARE_ELECTRICAL 正本がない")
    scalar_names = (
        "SERVO_FREQ", "N_CH", "CH_HEAD", "EYE_LIM", "EYE_SLEW_DPS",
        "US_MIN", "US_MAX", "DEG_RANGE", "DFPLAYER_TRACK_MAX",
        "CONTROL_LEASE_MS",
        "CAL_ENDPOINT_MARGIN_US", "PIN_SDA", "PIN_SCL", "PIN_LED",
        "PIN_DF_RX", "PIN_DF_TX", "PIN_VBAT", "VBAT_WARN", "VBAT_CUT",
        "VBAT_MAX_VALID", "N_LED", "PIN_I2S_BCLK", "PIN_I2S_WS",
        "PIN_I2S_DOUT", "PIN_I2S_DIN", "AUDIO_SAMPLE_RATE",
    )
    array_names = ("PCA_ADDR", "PCA_CH", "ARM_CH", "EYE_CH", "ARM_SIGN")
    missing = [name for name in scalar_names + array_names
               if name not in source]
    missing += [name for name in ("VBAT_DIV_NUMERATOR", "VBAT_DIV_DENOMINATOR")
                if name not in source]
    if missing:
        raise ValueError(
            "config.py の FIRMWARE_ELECTRICAL が不足: " + ", ".join(missing))
    return {
        "scalars": {name: float(source[name]) for name in scalar_names},
        "arrays": {name: [int(value) for value in source[name]]
                   for name in array_names},
        "expressions": {
            "VBAT_DIV": tuple(float(value) for value in source["VBAT_DIV_NUMERATOR"])
            + (float(source["VBAT_DIV_DENOMINATOR"]),),
        },
    }


def compare_electrical_text(text: str | None = None) -> list[str]:
    """固定配線・電源・I2S契約と config.h を突合する。"""
    source = CONFIG_H.read_text(encoding="utf-8") if text is None else text
    values = canonical_electrical_values()
    errors: list[str] = []
    for name, expected in values["scalars"].items():
        try:
            actual = _constant_number(source, name)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if not math.isclose(actual, float(expected), abs_tol=1e-6, rel_tol=0.0):
            errors.append(f"{name}: firmware={actual:g}, wiring contract={float(expected):g}")
    for name, expected_values in values["arrays"].items():
        try:
            actual_values = _integer_array(source, name)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        expected = [int(value) for value in expected_values]
        if actual_values != expected:
            errors.append(f"{name}: firmware={actual_values}, wiring contract={expected}")
    try:
        actual_div = _vbat_div_parts(source)
    except ValueError as exc:
        errors.append(str(exc))
    else:
        wanted_div = tuple(values["expressions"]["VBAT_DIV"])
        if any(not math.isclose(actual, expected, abs_tol=1e-6, rel_tol=0.0)
               for actual, expected in zip(actual_div, wanted_div)):
            errors.append(f"VBAT_DIV: firmware={actual_div!r}, config.py={wanted_div!r}")
    return errors


def canonical_values() -> dict[str, dict[str, list[float] | float]]:
    """正本 config.py から firmware へ渡す値を組み立てる。"""
    legs = ("FR", "FL", "RL", "RR")
    pitch_min, pitch_max = C.JOINT_LIMITS_DEG["pitch"]
    knee_limit = float(C.JOINT_LIMITS_DEG["knee"])
    tibia = float(C.TIBIA_LEN_GAIT)
    femur = float(C.FEMUR_LEN)
    knee_margin = float(C.KNEE_DISTANCE_MARGIN_MM)
    d_knee_max = math.sqrt(
        femur**2 + tibia**2
        + 2 * femur * tibia * math.cos(math.radians(90.0 - knee_limit))
    ) - knee_margin
    d_knee_min = math.sqrt(
        femur**2 + tibia**2
        + 2 * femur * tibia * math.cos(math.radians(90.0 + knee_limit))
    ) + knee_margin
    return {
        "scalars": {
            "COXA_LEN": float(C.COXA_LEN),
            "FEMUR_LEN": femur,
            "TIBIA_LEN": tibia,
            "HIP_R": float(C.HIP_R),
            "LIM_YAW": float(C.JOINT_LIMITS_DEG["yaw"]),
            "LIM_YAW_IN": float(C.JOINT_LIMITS_DEG["yaw_inner"]),
            "LIM_YAW_IN_SUM": float(C.JOINT_LIMITS_DEG["yaw_inner_sum"]),
            "LIM_YAW_POD": float(C.JOINT_LIMITS_DEG["yaw_pod"]),
            "LIM_PITCH_UP": float(pitch_min),
            "LIM_PITCH_DN": float(pitch_max),
            "LIM_KNEE": knee_limit,
            "BODY_H_DEF": float(C.BODY_H_DEFAULT),
            "BODY_H_MIN": float(C.BODY_H_MIN),
            "BODY_H_MAX": float(C.BODY_H_MAX),
            "STANCE_R": float(C.STANCE_R),
            "STANCE_OFF_X": float(C.STANCE_OFF_XY[0]),
            "STANCE_OFF_Y": float(C.STANCE_OFF_XY[1]),
            "STEP_H": float(C.STEP_H),
            "CYCLE_T": float(C.CYCLE_T),
            "MAX_STEP": float(C.MAX_STEP),
            "MAX_TURN_DEG": float(C.MAX_TURN_DEG),
            "DUTY": float(C.DUTY),
            "SWAY_LEAD": float(C.SWAY_LEAD),
            "D_KNEE_MAX": d_knee_max,
            "D_KNEE_MIN": d_knee_min,
            "ARM_MOUNT_YAW_DEG": float(C.ARM_MOUNT_YAW_DEG),
            "ARM_YAW_LIM": float(C.ARM_YAW_LIMIT_DEG),
            "ARM_PITCH_MIN": float(C.ARM_PITCH_LIMIT_DEG[0]),
            "ARM_PITCH_MAX": float(C.ARM_PITCH_LIMIT_DEG[1]),
            "ARM_ELBOW_MIN": float(C.ARM_ELBOW_LIMIT_DEG[0]),
            "ARM_ELBOW_MAX": float(C.ARM_ELBOW_LIMIT_DEG[1]),
            "ARM_SLEW_DPS": float(C.ARM_SLEW_DPS),
            "LEG_SLEW_DPS": float(C.LEG_SLEW_DPS),
            "ARM_MOUNT_X_MM": float(C.ARM_MOUNT_XY[0]),
            "ARM_MOUNT_Y_MM": float(C.ARM_MOUNT_XY[1]),
            "ARM_UPPER_MM": float(C.ARM_UPPER_MM),
            "ARM_HAND_HALF_MM": float(C.ARM_HAND_HALF_MM),
            "ARM_SHOULDER_OVER_HIP_MM": float(C.ARM_SHOULDER_OVER_HIP_MM),
            "ARM_REACH_MM": float(C.ARM_REACH_MM),
            "ARM_GROUND_MARGIN_MM": float(C.ARM_GROUND_MARGIN_MM),
            "ARM_LEG_YAW_GATE_DEG": float(C.ARM_LEG_YAW_GATE_DEG),
            "ARM_SWING_DEG": float(C.ARM_SWING_DEG),
            "EYE_LIM": float(C.EYE_LIMIT_DEG),
            "EYE_SLEW_DPS": float(C.EYE_SLEW_DPS),
        },
        "arrays": {
            "LEG_MOUNT_DEG": [float(C.LEG_ANGLES[name]) for name in legs],
            "STANCE_DEG": [float(C.STANCE_ANGLES[name]) for name in legs],
            "LEG_ORIGIN": [float(value) for name in legs for value in C.HIPS[name]],
            "YAW_IN_SIGN": [float(value) for value in C.YAW_IN_SIGN],
            "YAW_POD_SIGN": [float(value) for value in C.YAW_POD_SIGN],
            "JOINT_SIGN": [float(value) for row in C.JOINT_SIGN for value in row],
            "PHASE_OFF": [float(value) for value in C.PHASE_OFF],
            "SWAY_MM": [float(value) for value in C.SWAY_MM],
            "ARM_LEG_YAW_SIGN": [float(value) for value in C.ARM_LEG_YAW_SIGN],
            "ARM_POSE_TUCK": [float(value) for value in C.ARM_POSE_TUCK],
            "ARM_POSE_READY": [float(value) for value in C.ARM_POSE_READY],
            "ARM_POSE_REACH": [float(value) for value in C.ARM_POSE_REACH],
        },
    }


def compare_firmware_text(text: str | None = None) -> list[str]:
    """firmware headerを正本と比較し、差分を人間が読める形で返す。"""
    source = CONFIG_H.read_text(encoding="utf-8") if text is None else text
    values = canonical_values()
    errors: list[str] = []
    for name, expected in values["scalars"].items():
        try:
            actual = _scalar(source, name)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        tolerance = _STRICT_SCALAR_TOLERANCES.get(name, _TOLERANCE)
        if not math.isclose(actual, float(expected), abs_tol=tolerance, rel_tol=0.0):
            errors.append(
                f"{name}: firmware={actual:g}, config.py={float(expected):g}"
                f" (許容={tolerance:g})"
            )
    for name, expected_values in values["arrays"].items():
        try:
            actual_values = _array(source, name)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        expected = [float(value) for value in expected_values]
        if len(actual_values) != len(expected):
            errors.append(
                f"{name}: firmwareの要素数={len(actual_values)}, config.py={len(expected)}"
            )
            continue
        for index, (actual, wanted) in enumerate(zip(actual_values, expected)):
            if not math.isclose(actual, wanted, abs_tol=_TOLERANCE, rel_tol=0.0):
                errors.append(
                    f"{name}[{index}]: firmware={actual:g}, config.py={wanted:g}"
                )
    return errors + compare_electrical_text(source)


def assert_firmware_matches_config(text: str | None = None) -> None:
    """契約違反を例外にして、利用側が黙って続行しないようにする。"""
    errors = compare_firmware_text(text)
    if errors:
        raise ValueError("firmware/src/config.h と config.py の不一致:\n- " + "\n- ".join(errors))


__all__ = [
    "CONFIG_H",
    "ROOT",
    "assert_firmware_matches_config",
    "canonical_values",
    "canonical_electrical_values",
    "compare_electrical_text",
    "compare_firmware_text",
]
