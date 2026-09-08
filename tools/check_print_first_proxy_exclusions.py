#!/usr/bin/env python3
"""印刷優先モデルの固定5組プロキシ除外を実メッシュで検証する。

MuJoCo の凸片が作る隣接リンクの疑似接触について、除外を追加する前に
実STLのBoolean交差を (1) native C++ の全有限姿勢、(2) 各除外組の関節
可動範囲の密掃引、(3) 物理計算で得た実現 qpos の時系列で確認する。
この検査は有限サンプルの根拠であり、未サンプルの連続到達集合を証明しない。
ケースJSONから除外組を増やす経路は持たない。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import math
from pathlib import Path
import subprocess
import sys
from typing import Iterable, Mapping, Sequence
import xml.etree.ElementTree as ET

import numpy as np
import trimesh
import mujoco

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hardware/src"))
sys.path.insert(0, str(ROOT / "tools"))

import export_urdf as E
import sim_collision as COLLISION
import sim_self_collision as SELF


INTERSECTION_THRESHOLD_MM3 = float(E.C.BOOLEAN_INTERSECTION_THRESHOLD_MM3)
DEFAULT_DENSE_SAMPLES = 1001
REQUIRED_CASE_KINDS = ("final_integrated", "print_first_final", "frozen_integrated")


def _sha256(path: Path) -> str:
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"input file is missing: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _public_path(path: Path, output_root: Path | None = None) -> str:
    """絶対ローカルパスを監査JSON用の再現可能なキーへ変換する。"""
    path = Path(path).resolve()
    if output_root is not None:
        try:
            return "$OUTPUT/" + path.relative_to(
                Path(output_root).resolve()).as_posix()
        except ValueError:
            pass
    digest = _sha256(path) if path.is_file() else None
    return SELF._public_root_path(path, digest)


def _resolve(value: str | Path, base: Path = ROOT, *, label: str = "path",
             allow_external: bool = False) -> Path:
    """入力パスを検査してから解決する。

    先に ``resolve()`` すると ``..`` とリポジトリ外参照の痕跡が消える
    ため、字面を検査してから解決する。外部入力は明示モードでのみ許可し、
    結果台帳には内容SHAを残す。
    """
    if isinstance(value, Path):
        raw = value
    elif isinstance(value, str) and value:
        raw = Path(value)
    else:
        raise ValueError(f"{label} must be a non-empty path")
    if ".." in raw.parts:
        raise ValueError(f"{label} must not contain '..'")
    lexical = raw if raw.is_absolute() else Path(base) / raw
    symlink = SELF._repo_symlink_component(lexical)
    if symlink is not None:
        raise ValueError(f"{label} must not use a repository symlink: {symlink}")
    resolved = raw.resolve() if raw.is_absolute() else (Path(base) / raw).resolve()
    try:
        resolved.relative_to(ROOT.resolve())
    except ValueError:
        if not allow_external:
            raise ValueError(
                f"{label} is outside the repository; use --allow-external-input "
                "and retain its content SHA")
    return resolved


def _strict_sha(value: object, label: str) -> str:
    if (not isinstance(value, str) or len(value) != 64
            or value != value.lower()
            or any(ch not in "0123456789abcdef" for ch in value)):
        raise ValueError(f"{label} must be a lowercase hexadecimal SHA-256")
    return value


def _normalized_case_sha(case: Mapping[str, object]) -> str:
    canonical = json.dumps(case, sort_keys=True, ensure_ascii=False,
                           separators=(',', ':'), default=str).encode('utf-8')
    return hashlib.sha256(canonical).hexdigest()


def _strict_csv_bool(value: object, label: str) -> bool:
    if value == 'True':
        return True
    if value == 'False':
        return False
    raise ValueError(f'{label} must be the literal True or False')


def _validate_trace_csv(csv_path: Path, payload: Mapping[str, object],
                        native: np.ndarray):
    """CSV全行をJSON行へ突合し、CSVだけの自己申告を受け入れない。"""
    rows = payload.get('rows')
    if not isinstance(rows, list) or len(rows) != len(native):
        raise ValueError('native trace JSON rows are missing for CSV comparison')
    try:
        with csv_path.open('r', encoding='utf-8', newline='') as stream:
            csv_rows = list(csv.DictReader(stream))
    except (OSError, csv.Error) as exc:
        raise ValueError(f'native trace CSV cannot be read: {csv_path}') from exc
    if len(csv_rows) != len(rows):
        raise ValueError('native trace CSV row count does not match JSON rows')

    def number(value, label):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f'{label} is missing')
        try:
            parsed = float(value)
        except ValueError as exc:
            raise ValueError(f'{label} is not numeric') from exc
        if not math.isfinite(parsed):
            raise ValueError(f'{label} is non-finite')
        return parsed

    for index, (json_row, csv_row) in enumerate(zip(rows, csv_rows)):
        if not isinstance(json_row, dict):
            raise ValueError(f'native trace JSON row {index} is malformed')
        csv_index = number(csv_row.get('index'), f'CSV row {index}.index')
        if not csv_index.is_integer() or int(csv_index) != index:
            raise ValueError(f'CSV row {index}.index does not match JSON')
        for key in ('time_s', 'dt_s'):
            if not math.isclose(number(csv_row.get(key), f'CSV row {index}.{key}'),
                                float(json_row.get(key)), abs_tol=1e-9):
                raise ValueError(f'CSV row {index}.{key} does not match JSON')
        if csv_row.get('segment') != json_row.get('segment'):
            raise ValueError(f'CSV row {index}.segment does not match JSON')
        command = json_row.get('command')
        if not isinstance(command, dict):
            raise ValueError(f'JSON row {index}.command is malformed')
        for csv_key, json_key in (('vx', 'vx'), ('vy', 'vy'), ('wz', 'wz'),
                                  ('body_h_mm', 'body_h_mm')):
            if not math.isclose(number(csv_row.get(csv_key),
                                       f'CSV row {index}.{csv_key}'),
                                float(command[json_key]), abs_tol=1e-9):
                raise ValueError(f'CSV row {index}.{csv_key} does not match JSON')
        for key in ('phase',):
            if not math.isclose(number(csv_row.get(key), f'CSV row {index}.{key}'),
                                float(json_row[key]), abs_tol=1e-9):
                raise ValueError(f'CSV row {index}.{key} does not match JSON')
        for key in ('moving', 'ready'):
            if _strict_csv_bool(csv_row.get(key), f'CSV row {index}.{key}') != json_row[key]:
                raise ValueError(f'CSV row {index}.{key} does not match JSON')
        angles = json_row.get('angles_deg')
        enabled = json_row.get('enabled')
        if not isinstance(angles, dict) or not isinstance(enabled, dict):
            raise ValueError(f'JSON row {index} angle/enable data is malformed')
        for name in SELF.S.ALL_JOINTS:
            if not math.isclose(number(csv_row.get(name),
                                       f'CSV row {index}.{name}'),
                                float(angles[name]), abs_tol=1e-9):
                raise ValueError(f'CSV row {index}.{name} does not match JSON')
            enabled_key = f'{name}_enabled'
            if (_strict_csv_bool(csv_row.get(enabled_key),
                                 f'CSV row {index}.{enabled_key}')
                    != enabled[name]):
                raise ValueError(f'CSV row {index}.{enabled_key} does not match JSON')


def _replay_trace_binary(binary_path: Path, options: Mapping[str, object],
                         expected: np.ndarray):
    """記録済みbinaryを同じcase区間で再実行し、全43列を比較する。"""
    segments = options.get('_case_segments')
    if not isinstance(segments, list) or not segments:
        raise ValueError('case segments are required for binary replay')
    body_h_default = float(options.get('_profile_body_h', SELF.S.BODY_H_DEFAULT))
    commands = [[0., 0., 0., 0., body_h_default]]
    for segment in segments:
        duration = float(segment['duration'])
        steps = round(duration * SELF.S.SERVO_HZ)
        body_h = float(segment.get('body_h', body_h_default))
        for _ in range(steps):
            commands.append([1. / SELF.S.SERVO_HZ,
                             float(segment.get('vx', 0.)),
                             float(segment.get('vy', 0.)),
                             float(segment.get('wz', 0.)), body_h])
    mode = options.get('_native_trace_mode')
    if mode not in ('ready', 'direct', 'sequential'):
        raise ValueError('native trace mode is missing or invalid for replay')
    try:
        replay = subprocess.run(
            [str(binary_path), mode],
            input=''.join(' '.join(map(str, row)) + '\n' for row in commands),
            capture_output=True, text=True, check=True)
        values = np.loadtxt(replay.stdout.splitlines(), ndmin=2)
        values = SELF.validate_native_trace(values, label='replayed native trace')
    except Exception as exc:  # noqa: BLE001 - replay is a hard provenance gate
        raise ValueError(f'recorded native binary replay failed: {exc}') from exc
    if values.shape != expected.shape or not np.allclose(values, expected,
                                                         rtol=0.0, atol=1e-9):
        raise ValueError('recorded native binary output differs from trace JSON')


def _resolve_trace_ref(value: object, trace_output_root: Path, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"trace {label} path is required")
    if value.startswith("$OUTPUT/"):
        raw = Path(value[len("$OUTPUT/"):])
        if '..' in raw.parts:
            raise ValueError(f"trace {label} output path must not contain '..'")
        candidate = (Path(trace_output_root).resolve() / raw).resolve()
        try:
            candidate.relative_to(Path(trace_output_root).resolve())
        except ValueError as exc:
            raise ValueError(f"trace {label} output path escaped output root") from exc
        return candidate
    if value.startswith("$EXTERNAL/"):
        raise ValueError(f"trace {label} external path cannot be resolved")
    return _resolve(value, ROOT, label=label)


def _validate_build_provenance(build_path: Path, build: Mapping[str, object],
                               payload: Mapping[str, object],
                               trace_output_root: Path, header_path: Path,
                               binary_path: Path):
    """Revalidate the native build manifest against current source bytes.

    A compiler command copied into a trace is evidence only when its include
    set, copied headers, source template and output binary all still match the
    files being audited.  Keep this check independent from the producer's
    flat input ledger so a self-consistent stale ``build.json`` cannot pass.
    """
    import sim_print_first as PRINT_FIRST

    errors = []
    if build.get('schema_version') != 2:
        errors.append('native build schema_version must be 2')
    mode = payload.get('profile_mode')
    if build.get('profile_mode') != mode or build.get('build_mode') != mode:
        errors.append('native build profile/build mode differs from trace')
    compile_flag = '-DTACHIKOMA_PRINT_FIRST_PROFILE=1'
    if build.get('print_first_compile_flag') != compile_flag:
        errors.append('native build print-first compile flag is missing')
    if build.get('compile_flags') != ['-std=c++17', '-O2', compile_flag]:
        errors.append('native build compile_flags differ from the fixed command')
    command = build.get('compiler_command')
    if not isinstance(command, list) or command != build.get('command'):
        errors.append('native build compiler_command/command are not identical lists')
    elif (not command or not isinstance(command[0], str)
          or command[0] not in ('c++', 'g++', 'clang++')
          or compile_flag not in command or '-std=c++17' not in command
          or '-O2' not in command):
        errors.append('native build compiler command is not the fixed C++ profile build')
    output_root = Path(trace_output_root).resolve()
    expected_includes = [
        PRINT_FIRST.public_path(ROOT / 'tools/tests/firmware_stubs', output_root),
        PRINT_FIRST.public_path(build_path.parent, output_root),
    ]
    if build.get('include_dirs') != expected_includes:
        errors.append('native build include_dirs differ from the generated bundle')

    def path_ref(value, label):
        return _resolve_trace_ref(value, output_root, label)

    flat = build.get('source_sha256')
    if not isinstance(flat, dict) or set(flat) != set(PRINT_FIRST.NATIVE_PROFILE_HEADERS):
        errors.append('native build source_sha256 header set is incomplete or has extras')
    else:
        for name in PRINT_FIRST.NATIVE_PROFILE_HEADERS:
            actual = _sha256(build_path.parent / name)
            if flat.get(name) != actual:
                errors.append(f'native build copied header SHA differs: {name}')
    source_headers = build.get('source_headers')
    if not isinstance(source_headers, list) \
            or [row.get('name') for row in source_headers
                if isinstance(row, Mapping)] != list(PRINT_FIRST.NATIVE_PROFILE_HEADERS):
        errors.append('native build source_headers order/set is invalid')
    else:
        for row in source_headers:
            if not isinstance(row, Mapping):
                errors.append('native build source_headers row is malformed')
                continue
            name = row.get('name')
            source_ref = row.get('source_path')
            bundle_ref = row.get('bundle_path')
            try:
                source_file = path_ref(source_ref, f'source header {name}')
                bundle_file = path_ref(bundle_ref, f'bundle header {name}')
                if source_file != (ROOT / 'firmware/src' / str(name)).resolve():
                    errors.append(f'native build source header path is unexpected: {name}')
                if bundle_file != (build_path.parent / str(name)).resolve():
                    errors.append(f'native build bundle header path is unexpected: {name}')
                if row.get('source_sha256') != _sha256(source_file):
                    errors.append(f'native build source header SHA is stale: {name}')
                if row.get('bundle_sha256') != _sha256(bundle_file):
                    errors.append(f'native build bundle header SHA is stale: {name}')
            except (TypeError, ValueError, FileNotFoundError) as exc:
                errors.append(f'native build source header cannot be checked: {name}: {exc}')
    source_config = ROOT / 'hardware/src/config.py'
    try:
        config_file = path_ref(build.get('source_config_path'), 'source config')
        if config_file != source_config.resolve() or build.get('source_config_sha256') != _sha256(config_file):
            errors.append('native build source config provenance is stale')
    except (TypeError, ValueError, FileNotFoundError) as exc:
        errors.append(f'native build source config is malformed: {exc}')
    try:
        trace_file = path_ref(build.get('trace_path'), 'build trace')
        expected_source = ROOT / 'tools/tests/simulation_output_trace.cpp'
        if trace_file != (build_path.parent / 'trace.cpp').resolve() \
                or build.get('trace_sha256') != _sha256(trace_file) \
                or trace_file != (Path(header_path).parent / 'trace.cpp').resolve():
            errors.append('native build trace provenance is stale')
        trace_source = path_ref(build.get('trace_source_path'), 'trace source')
        if trace_source != expected_source.resolve() \
                or build.get('trace_source_sha256') != _sha256(trace_source):
            errors.append('native build trace source provenance is stale')
        if trace_file.read_bytes() != expected_source.read_bytes():
            errors.append('native build copied trace bytes differ from canonical source')
        expected_command = [
            'c++', '-std=c++17', '-O2', compile_flag,
            '-I', PRINT_FIRST.public_path(ROOT / 'tools/tests/firmware_stubs', output_root),
            '-I', PRINT_FIRST.public_path(build_path.parent, output_root),
            PRINT_FIRST.public_path(trace_file, output_root),
            '-o', PRINT_FIRST.public_path(Path(binary_path), output_root),
        ]
        if command != expected_command:
            errors.append('native build compiler command inputs differ from canonical source bundle')
    except (TypeError, ValueError, FileNotFoundError) as exc:
        errors.append(f'native build trace provenance is malformed: {exc}')
    try:
        declared_binary = path_ref(build.get('binary_path'), 'build binary')
        if declared_binary != Path(binary_path).resolve() \
                or build.get('binary_sha256') != _sha256(declared_binary):
            errors.append('native build binary provenance is stale')
    except (TypeError, ValueError, FileNotFoundError) as exc:
        errors.append(f'native build binary provenance is malformed: {exc}')
    expected_freeze = payload.get('header', {}).get('freeze_manifest_sha256') \
        if isinstance(payload.get('header'), Mapping) else None
    if expected_freeze is not None:
        if build.get('freeze_manifest_sha256') != expected_freeze:
            errors.append('native build freeze manifest SHA differs from trace')
        try:
            freeze_file = path_ref(build.get('freeze_manifest_path'), 'build freeze manifest')
            if build.get('freeze_manifest_sha256') != _sha256(freeze_file):
                errors.append('native build freeze manifest SHA is stale')
        except (TypeError, ValueError, FileNotFoundError) as exc:
            errors.append(f'native build freeze manifest is malformed: {exc}')
    if errors:
        raise ValueError('; '.join(errors))


def _load_case(case_path: Path) -> tuple[dict, str]:
    raw_bytes = case_path.read_bytes()
    case_sha = hashlib.sha256(raw_bytes).hexdigest()
    try:
        raw = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"case JSON is invalid: {case_path}") from exc
    if isinstance(raw, dict) and isinstance(raw.get("cases"), list):
        if len(raw["cases"]) != 1:
            raise ValueError("proxy proof case input must contain exactly one case")
        case = raw["cases"][0]
    elif isinstance(raw, dict):
        case = raw
    else:
        raise ValueError("proxy proof case input must be an object")
    if not isinstance(case, dict):
        raise ValueError("proxy proof case must be an object")
    # Reuse the public $OUTPUT resolver without importing the executor's
    # simulation path. This leaves the original case bytes available for the
    # exact case SHA recorded in the evidence.
    try:
        import sim_print_first as PRINT_FIRST
        case = PRINT_FIRST.resolve_case_output_refs(
            case, PRINT_FIRST.case_output_root(case_path))
    except Exception as exc:  # noqa: BLE001 - report malformed references
        raise ValueError(f"case path references are invalid: {exc}") from exc
    return case, case_sha


def _validate_case(case: Mapping[str, object]) -> dict:
    options = case.get("model")
    if not isinstance(options, dict):
        raise ValueError("case.model must be an object")
    if options.get("model_kind") not in REQUIRED_CASE_KINDS:
        raise ValueError("proxy proof requires a final integrated model kind")
    for key in ("assembly_context", "self_collision", "include_servo_collision",
                "include_parent_collision"):
        if options.get(key) is not True:
            raise ValueError(f"case.model.{key}=true is required")
    if options.get("contact_model") not in ("parts", "vhacd"):
        raise ValueError("proxy proof requires material-aware parts/vhacd contact")
    freeze_sha = (options.get("freeze_manifest_sha256")
                  or options.get("geometry_freeze_hash"))
    if not isinstance(freeze_sha, str):
        raise ValueError("case.model freeze manifest SHA is required")
    _strict_sha(freeze_sha, "case.model freeze manifest SHA")
    return options


def _validate_case_path_literals(case: Mapping[str, object], *, allow_external: bool):
    """ケース内の実行入力パスも同じ外部参照契約へ通す。"""
    options = case.get("model", {})
    if not isinstance(options, Mapping):
        return
    path_keys = {
        "model_path", "model_urdf", "urdf_path", "source_urdf",
        "assembly_manifest", "body_manifest", "feet_manifest",
        "foot_assembly", "foot_contact_reference", "leg_manifest",
        "geometry_manifest", "freeze_manifest", "self_collision_audit_path",
        "proxy_exclusion_proof_path",
    }
    for key in path_keys:
        value = options.get(key)
        if value is None or not isinstance(value, (str, Path)):
            continue
        if isinstance(value, str) and value.startswith("$OUTPUT/"):
            # The public case loader resolves this before this function is
            # called. Leave an unresolved token as a malformed input.
            raise ValueError(f"case.model.{key} has unresolved $OUTPUT reference")
        _resolve(value, label=f"case.model.{key}", allow_external=allow_external)


def _load_trace(trace_path: Path, options: Mapping[str, object]) -> tuple[np.ndarray, dict, str]:
    trace_path = Path(trace_path).resolve()
    try:
        raw_bytes = trace_path.read_bytes()
        payload = json.loads(raw_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"native trace JSON is invalid: {trace_path}") from exc
    trace_contract = SELF._trace_header_contract(payload)
    # Keep one byte snapshot for the JSON parse, row conversion, and file
    # digest.  Re-reading a mutable file between these steps could otherwise
    # pair metadata from one trace with rows from another.
    native = SELF._load_native_trace(trace_path, payload=payload)
    if payload.get("row_count") != len(native):
        raise ValueError("native trace row_count does not match validated rows")
    if payload.get("case_name") != options.get("_case_name"):
        raise ValueError(
            f"native trace case_name {payload.get('case_name')!r} does not match "
            f"case {options.get('_case_name')!r}")
    _validate_trace_schedule(payload, options)
    header = payload.get("header", {})
    if not isinstance(header, dict):
        raise ValueError("native trace header is required")
    expected_case_sha = options.get("_case_sha256")
    expected_normalized_case_sha = options.get("_case_normalized_sha256")
    for location, declared, expected in (
            ("trace case_sha256", payload.get("case_sha256"), expected_case_sha),
            ("header case_sha256", header.get("case_sha256"), expected_case_sha),
            ("trace case_normalized_sha256", payload.get("case_normalized_sha256"),
             expected_normalized_case_sha),
            ("header case_normalized_sha256", header.get("case_normalized_sha256"),
             expected_normalized_case_sha)):
        if expected is not None:
            if declared != expected:
                raise ValueError(f"{location} does not match the bound case")
            _strict_sha(declared, location)
    expected_mode = options.get("_expected_trace_mode")
    if expected_mode is not None and payload.get("profile_mode") != expected_mode:
        raise ValueError("native trace profile_mode does not match final case")
    if payload.get("build_mode") != payload.get("profile_mode"):
        raise ValueError("native trace build_mode/profile_mode differ")
    if header.get("profile_mode") != payload.get("profile_mode"):
        raise ValueError("native trace header profile_mode differs")
    if header.get("build_mode") != payload.get("build_mode"):
        raise ValueError("native trace header build_mode differs")
    trace_output_root = (trace_path.parent.parent
                         if trace_path.parent.name == "native-trace"
                         else trace_path.parent)
    header_path = _resolve_trace_ref(header.get("path"), trace_output_root,
                                     "header")
    if _sha256(header_path) != header.get("sha256"):
        raise ValueError("native trace header SHA does not match the generated header")
    build = payload.get("build")
    if not isinstance(build, dict):
        raise ValueError("native trace build metadata is required")
    build_path = _resolve_trace_ref(build.get("path"), trace_output_root, "build")
    if _sha256(build_path) != build.get("sha256"):
        raise ValueError("native trace build SHA does not match the generated build")
    if (payload.get('build_json_sha256') is not None
            and payload.get('build_json_sha256') != _sha256(build_path)):
        raise ValueError("native trace build_json_sha256 is stale")
    if (header.get('build_json_sha256') is not None
            and header.get('build_json_sha256') != _sha256(build_path)):
        raise ValueError("native trace header build_json_sha256 is stale")
    csv = payload.get("csv")
    if not isinstance(csv, dict):
        raise ValueError("native trace CSV metadata is required")
    csv_path = _resolve_trace_ref(csv.get("path"), trace_output_root, "csv")
    if _sha256(csv_path) != csv.get("sha256"):
        raise ValueError("native trace CSV SHA does not match the generated CSV")
    binary = payload.get("binary")
    if not isinstance(binary, dict):
        raise ValueError("native trace binary metadata is required")
    binary_path = _resolve_trace_ref(binary.get("path"), trace_output_root, "binary")
    binary_sha = _sha256(binary_path)
    if binary_sha != binary.get("sha256"):
        raise ValueError("native trace binary SHA does not match the generated binary")
    _validate_build_provenance(
        build_path, build, payload, trace_output_root, header_path, binary_path)
    _validate_trace_csv(csv_path, payload, native)
    replay_options = dict(options)
    replay_options["_case_segments"] = options.get("_case_segments")
    replay_options["_native_trace_mode"] = options.get("_native_trace_mode")
    _replay_trace_binary(binary_path, replay_options, native)
    expected_freeze = (options.get("freeze_manifest_sha256")
                       or options.get("geometry_freeze_hash"))
    actual_freeze = payload.get("header", {}).get("freeze_manifest_sha256")
    if actual_freeze != expected_freeze:
        raise ValueError("native trace freeze manifest SHA does not match case")
    trace_sha = hashlib.sha256(raw_bytes).hexdigest()
    return native, {**trace_contract,
                    "header_path": _public_path(header_path),
                    "header_sha256": _sha256(header_path),
                    "build_path": _public_path(build_path),
                    "build_sha256": _sha256(build_path),
                    "build_schema_version": build.get('schema_version'),
                    "build_profile_mode": build.get('profile_mode'),
                    "build_compile_flags": build.get('compile_flags'),
                    "csv_path": _public_path(csv_path),
                    "csv_sha256": _sha256(csv_path),
                    "binary_path": _public_path(binary_path),
                    "binary_sha256": binary_sha,
                    "trace_file_sha256": trace_sha,
                    "row_count": int(len(native)),
                    "case_name": payload.get("case_name"),
                    "freeze_manifest_sha256": actual_freeze,
                    "case_sha256": payload.get("case_sha256"),
                    "case_normalized_sha256": payload.get("case_normalized_sha256"),
                    "profile_mode": payload.get("profile_mode"),
                    "build_mode": payload.get("build_mode")}, trace_sha


def _validate_trace_schedule(payload: Mapping[str, object], options: Mapping[str, object]):
    """case区間を50Hzへ展開し、traceの行時刻・指令・体高へ束縛する。"""
    segments = options.get("_case_segments")
    if not isinstance(segments, list) or not segments:
        raise ValueError("case segments are required for native trace binding")
    expected = [("initial", 0.0, 0.0, 0.0, 0.0,
                 float(segments[0].get("body_h", options.get("_profile_body_h", 115.0))))]
    elapsed = 0.0
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            raise ValueError(f"case.segments[{index}] is not an object")
        duration = segment.get("duration")
        if isinstance(duration, bool) or not isinstance(duration, (int, float)):
            raise ValueError(f"case.segments[{index}].duration is not numeric")
        duration = float(duration)
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError(f"case.segments[{index}].duration is invalid")
        steps = round(duration * SELF.S.SERVO_HZ)
        if not math.isclose(duration * SELF.S.SERVO_HZ, steps, abs_tol=1e-8):
            raise ValueError(f"case.segments[{index}].duration is not a servo-period multiple")
        name = segment.get("name", f"segment_{index}")
        if not isinstance(name, str) or not name:
            raise ValueError(f"case.segments[{index}].name is invalid")
        command = [float(segment.get(axis, 0.0)) for axis in ("vx", "vy", "wz")]
        body_h = float(segment.get("body_h", options.get("_profile_body_h", 115.0)))
        if any(not math.isfinite(value) for value in command + [body_h]):
            raise ValueError(f"case.segments[{index}] command/height is non-finite")
        for _ in range(steps):
            elapsed += 1.0 / SELF.S.SERVO_HZ
            expected.append((name, elapsed, *command, body_h))
    rows = payload.get("rows")
    expected_count = len(expected)
    if not isinstance(rows, list) or len(rows) != expected_count:
        raise ValueError(
            f"native trace row_count {len(rows) if isinstance(rows, list) else None} "
            f"does not match case expanded count {expected_count}")
    for index, (row, expected_row) in enumerate(zip(rows, expected)):
        if not isinstance(row, dict):
            raise ValueError(f"native trace row {index} is not an object")
        name, time_s, vx, vy, wz, body_h = expected_row
        if row.get("index") != index:
            raise ValueError(f"native trace row {index}.index does not match")
        def check_float(value, expected_value, label):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"native trace row {index}.{label} is not numeric")
            if not math.isfinite(float(value)) or not math.isclose(
                    float(value), expected_value, abs_tol=1e-7):
                raise ValueError(
                    f"native trace row {index}.{label} does not match case schedule")
        check_float(row.get("time_s"), time_s, "time_s")
        check_float(row.get("dt_s"), 0.0 if index == 0 else 1.0 / SELF.S.SERVO_HZ, "dt_s")
        if row.get("segment") != name:
            raise ValueError(f"native trace row {index}.segment does not match case")
        command = row.get("command")
        if not isinstance(command, dict):
            raise ValueError(f"native trace row {index}.command is required")
        for axis, value in (("vx", vx), ("vy", vy), ("wz", wz), ("body_h_mm", body_h)):
            check_float(command.get(axis), value, f"command.{axis}")


PRINT_FIRST_INTENTIONAL_EMPTY_LINKS = frozenset({'camera_optical_frame'})


def _xml_vector(element, attribute: str, default: Sequence[float], label: str,
                errors: list[str]) -> list[float]:
    raw = element.get(attribute) if element is not None else None
    values = list(default) if raw is None else raw.split()
    if len(values) != 3:
        errors.append(f'{label}.{attribute} must contain three values')
        return list(default)
    try:
        values = [float(value) for value in values]
    except (TypeError, ValueError):
        errors.append(f'{label}.{attribute} must be numeric')
        return list(default)
    if not np.isfinite(values).all():
        errors.append(f'{label}.{attribute} must be finite')
    return values


def _rpy_matrix(rpy: Sequence[float]) -> np.ndarray:
    roll, pitch, yaw = [float(value) for value in rpy]
    cr, sr = math.cos(roll), math.sin(roll)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    return np.array([
        [cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
        [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
        [-sp, cp * sr, cp * cr],
    ], dtype=float)


def _resolve_urdf_mesh_path(model_path: Path, filename: object,
                             label: str, errors: list[str]) -> Path | None:
    if not isinstance(filename, str) or not filename:
        errors.append(f'{label} mesh filename is missing')
        return None
    raw = Path(filename)
    if '..' in raw.parts:
        errors.append(f'{label} mesh filename must not contain ..')
        return None
    lexical = raw if raw.is_absolute() else Path(model_path).parent / raw
    symlink = SELF._repo_symlink_component(lexical)
    if symlink is not None:
        errors.append(f'{label} mesh path must not use a repository symlink: {symlink}')
        return None
    candidate = lexical.resolve()
    try:
        candidate.relative_to(ROOT.resolve())
    except ValueError:
        errors.append(f'{label} mesh path is outside the repository')
        return None
    if not candidate.is_file():
        errors.append(f'{label} mesh file is missing: {candidate}')
        return None
    return candidate


def _urdf_geometry_contract(model_path: Path, root: ET.Element,
                            expected_links: Sequence[str], compiled_model,
                            parts: Mapping,
                            compiled_index: Mapping | None = None) -> dict:
    """Bind every URDF mesh reference to bytes, transforms and compiled geoms.

    Link names alone are insufficient evidence: a forged URDF can retain every
    expected name while swapping a mesh, dropping a collision element, or
    changing an origin/scale.  Keep this contract independent of the source
    part-name list and record the complete XML-derived reference set.
    """
    errors: list[str] = []
    rows: list[dict] = []
    collision_counts: dict[str, int] = {}
    visual_counts: dict[str, int] = {}
    for link in root.findall('link'):
        name = link.get('name')
        if not isinstance(name, str) or not name:
            continue
        for kind in ('collision', 'visual'):
            elements = link.findall(kind)
            counts = collision_counts if kind == 'collision' else visual_counts
            counts[name] = len(elements)
            for occurrence, element in enumerate(elements):
                label = f'URDF {name}/{kind}[{occurrence}]'
                geometry = element.find('geometry')
                mesh = geometry.find('mesh') if geometry is not None else None
                if mesh is None:
                    errors.append(f'{label} must reference a mesh geometry')
                    continue
                path = _resolve_urdf_mesh_path(
                    Path(model_path), mesh.get('filename'), label, errors)
                origin = element.find('origin')
                xyz = _xml_vector(origin, 'xyz', (0., 0., 0.), label, errors)
                rpy = _xml_vector(origin, 'rpy', (0., 0., 0.), label, errors)
                scale = _xml_vector(mesh, 'scale', (1., 1., 1.), label, errors)
                if any(abs(value) <= 0.0 for value in scale):
                    errors.append(f'{label}.scale must not contain zero')
                transform = np.eye(4, dtype=float)
                transform[:3, :3] = _rpy_matrix(rpy)
                transform[:3, :3] = transform[:3, :3] @ np.diag(scale)
                transform[:3, 3] = xyz
                row = {
                    'link': name,
                    'kind': kind,
                    'occurrence': occurrence,
                    'filename': mesh.get('filename'),
                    'path': _public_path(path) if path is not None else None,
                    'sha256': _sha256(path) if path is not None else None,
                    'origin_xyz': xyz,
                    'origin_rpy': rpy,
                    'scale': scale,
                    'transform': transform.tolist(),
                }
                rows.append(row)
    by_link = {name: [] for name in expected_links}
    for row in rows:
        by_link.setdefault(row['link'], []).append(row)
    row_keys = [(row['link'], row['kind'], row['occurrence']) for row in rows]
    if len(set(row_keys)) != len(row_keys):
        errors.append('URDF geometry rows contain duplicate link/kind/occurrence keys')

    # A generated model is normally checked against the freeze manifest before
    # this function runs.  When the independent source row set is supplied by
    # the caller, compare every path/hash/transform field exactly as well; a
    # same-link mesh or origin edit must not be hidden by the link name.
    expected_geometry_rows = None
    if isinstance(compiled_index, Mapping):
        for key in ('expected_urdf_geometry_rows', 'urdf_geometry_manifest',
                    'urdf_geometry_rows'):
            candidate = compiled_index.get(key)
            if candidate is not None:
                expected_geometry_rows = candidate
                break
    if expected_geometry_rows is not None:
        if (not isinstance(expected_geometry_rows, Sequence)
                or isinstance(expected_geometry_rows, (str, bytes))):
            errors.append('expected URDF geometry rows are malformed')
        elif len(expected_geometry_rows) != len(rows):
            errors.append(
                f'URDF geometry row count differs from expected source: '
                f'{len(rows)} != {len(expected_geometry_rows)}')
        else:
            current_by_key = {key: row for key, row in zip(row_keys, rows)}
            expected_keys = []
            for expected in expected_geometry_rows:
                if not isinstance(expected, Mapping):
                    errors.append('expected URDF geometry row is malformed')
                    continue
                key = (expected.get('link'), expected.get('kind'),
                       expected.get('occurrence'))
                expected_keys.append(key)
                actual = current_by_key.get(key)
                if actual is None:
                    errors.append(f'URDF geometry row is missing: {key!r}')
                    continue
                for field in ('path', 'sha256', 'filename'):
                    if actual.get(field) != expected.get(field):
                        errors.append(
                            f'URDF geometry {key!r} {field} differs from expected source')
                for field in ('origin_xyz', 'origin_rpy', 'scale', 'transform'):
                    try:
                        left = np.asarray(actual.get(field), dtype=float)
                        right = np.asarray(expected.get(field), dtype=float)
                    except (TypeError, ValueError):
                        errors.append(f'URDF geometry {key!r} {field} is malformed')
                        continue
                    if (left.shape != right.shape or not np.isfinite(right).all()
                            or not np.allclose(left, right, rtol=0.0, atol=1e-12)):
                        errors.append(
                            f'URDF geometry {key!r} {field} differs from expected source')
            if sorted(expected_keys, key=str) != sorted(row_keys, key=str):
                errors.append('URDF geometry key set differs from expected source')
    # Every source link with a physical mesh must be represented in the XML;
    # intentional camera_optical_frame remains the only empty allowlist entry.
    for name in expected_links:
        collision_rows = [row for row in by_link.get(name, [])
                          if row['kind'] == 'collision']
        if not collision_rows:
            errors.append(f'URDF link {name} has no collision mesh reference')
        source_items = parts.get(name, []) if isinstance(parts, Mapping) else []
        if not isinstance(source_items, Sequence) or not source_items:
            errors.append(f'source link {name} has no mesh for geometry binding')

    compiled_collision_counts: dict[str, int] = {}
    compiled_visual_counts: dict[str, int] = {}
    compiled_mesh_rows: list[dict] = []
    compiled_mesh_names = set()
    compiled_part_rows: dict[tuple[str, str], list[dict]] = {}
    part_metadata = (
        compiled_index.get('part_metadata')
        if isinstance(compiled_index, Mapping) else None)
    if compiled_model is not None and (
            not isinstance(part_metadata, Mapping)):
        errors.append('compiled collision part metadata is missing')
    if compiled_model is None:
        errors.append('compiled model is missing for geometry contract')
    else:
        for geom_index in range(int(compiled_model.ngeom)):
            body_id = int(compiled_model.geom_bodyid[geom_index])
            if body_id <= 0:
                continue
            link = mujoco.mj_id2name(
                compiled_model, mujoco.mjtObj.mjOBJ_BODY, body_id)
            if not isinstance(link, str) or not link:
                errors.append(f'compiled geom {geom_index} has no link name')
                continue
            group = int(compiled_model.geom_group[geom_index])
            target = compiled_collision_counts if group == 0 else compiled_visual_counts
            target[link] = target.get(link, 0) + 1
            geom_name = mujoco.mj_id2name(
                compiled_model, mujoco.mjtObj.mjOBJ_GEOM, geom_index)
            mesh_id = int(compiled_model.geom_dataid[geom_index])
            mesh_name = None
            mesh_sha = None
            if mesh_id >= 0 and mesh_id < int(compiled_model.nmesh):
                mesh_name = mujoco.mj_id2name(
                    compiled_model, mujoco.mjtObj.mjOBJ_MESH, mesh_id)
                if isinstance(mesh_name, str):
                    compiled_mesh_names.add(mesh_name)
            compiled_mesh_rows.append({
                'geom_index': geom_index,
                'geom_name': geom_name,
                'link': link,
                'group': group,
                'mesh_id': mesh_id,
                'mesh_name': mesh_name,
                'mesh_sha256': mesh_sha,
            })
            if group == 0:
                metadata = (part_metadata.get(geom_name)
                            if isinstance(part_metadata, Mapping) else None)
                if not isinstance(metadata, Mapping):
                    errors.append(
                        f'compiled collision geom {geom_index} has no part metadata')
                    continue
                if metadata.get('link') != link:
                    errors.append(
                        f'compiled geom {geom_index} metadata link differs from body')
                part = metadata.get('part')
                if not isinstance(part, str) or not part:
                    errors.append(f'compiled geom {geom_index} metadata part is missing')
                    continue
                row = {
                    'geom_index': geom_index,
                    'geom_name': geom_name,
                    'metadata': dict(metadata),
                }
                compiled_part_rows.setdefault((link, part), []).append(row)
    # Collision geometry is replaced by the reviewed convex-cell bundle, so
    # its compiled representation is bound through the cache source ledger.
    # Visual geometry remains the URDF mesh itself and must have a compiled
    # mesh name corresponding to the referenced file.
    cache_entries = []
    if isinstance(compiled_index, Mapping):
        ledger = compiled_index.get('collision_cache_ledger')
        if isinstance(ledger, Mapping):
            cache_entries = ledger.get('entries', [])
    cache_by_part = {}
    for row in cache_entries:
        if not isinstance(row, Mapping):
            errors.append('collision cache ledger entry is malformed for geometry contract')
            continue
        key = (row.get('link'), row.get('part'))
        if key in cache_by_part:
            errors.append(f'collision cache has duplicate source part: {key!r}')
        else:
            cache_by_part[key] = row
    source_part_bindings = []
    for link in expected_links:
        items = parts.get(link, []) if isinstance(parts, Mapping) else []
        for occurrence, item in enumerate(items):
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                errors.append(f'source geometry row {link}/{occurrence} is malformed')
                continue
            source_mesh, source_name = item[0], item[-1]
            if not isinstance(source_mesh, trimesh.Trimesh) or not isinstance(source_name, str):
                errors.append(f'source geometry row {link}/{occurrence} is malformed')
                continue
            source_sha = COLLISION._mesh_digest(source_mesh)
            cached = cache_by_part.get((link, source_name))
            compiled_names_for_part = []
            compiled_rows_for_part = compiled_part_rows.get((link, source_name), [])
            compiled_names_for_part = [str(row.get('geom_name'))
                                       for row in compiled_rows_for_part]
            hull_shas = (cached.get('hull_sha256')
                          if isinstance(cached, Mapping) else None)
            expected_hull_count = (len(hull_shas)
                                   if isinstance(hull_shas, list) else 0)
            source_part_bindings.append({
                'link': link,
                'part': source_name,
                'occurrence': occurrence,
                'source_mesh_sha256': source_sha,
                'compiled_cache_source_mesh_sha256': (
                    cached.get('source_mesh_sha256') if isinstance(cached, Mapping) else None),
                'compiled_geom_names': sorted(compiled_names_for_part),
                'compiled_geom_count': len(compiled_rows_for_part),
                'expected_hull_count': expected_hull_count,
            })
            if not isinstance(cached, Mapping):
                errors.append(f'collision cache entry is missing for {link}/{source_name}')
            if expected_hull_count <= 0:
                errors.append(f'collision cache hull count is missing for {link}/{source_name}')
            if len(compiled_rows_for_part) != expected_hull_count:
                errors.append(
                    f'compiled geometry count is not exact for {link}/{source_name}: '
                    f'{len(compiled_rows_for_part)} != {expected_hull_count}')
            if not compiled_names_for_part:
                errors.append(
                    f'compiled geometry is not bound to source part {link}/{source_name}')
            if not isinstance(cached, Mapping) \
                    or cached.get('source_mesh_sha256') != source_sha:
                errors.append(f'compiled source mesh SHA is stale for {link}/{source_name}')
            for compiled_row in compiled_rows_for_part:
                metadata = compiled_row['metadata']
                geom_label = f'{link}/{source_name}/{compiled_row["geom_name"]}'
                hull_index = metadata.get('hull_index')
                if (isinstance(hull_index, bool) or not isinstance(hull_index, int)
                        or hull_index < 0 or hull_index >= expected_hull_count):
                    errors.append(f'compiled hull index is invalid for {geom_label}')
                    continue
                if metadata.get('hull_count') != expected_hull_count:
                    errors.append(f'compiled hull count is stale for {geom_label}')
                if (isinstance(cached, Mapping)
                        and metadata.get('hull_sha256') != hull_shas[hull_index]):
                    errors.append(f'compiled hull SHA is stale for {geom_label}')
                for field in ('source_mesh_sha256', 'source_convex_hull_sha256',
                              'cache_path', 'cache_sha256'):
                    cache_field = {
                        'cache_path': 'path',
                        'cache_sha256': 'sha256',
                    }.get(field, field)
                    if isinstance(cached, Mapping) and metadata.get(field) != cached.get(
                            cache_field):
                        errors.append(f'compiled {field} is stale for {geom_label}')
            indices = sorted(
                row['metadata'].get('hull_index') for row in compiled_rows_for_part
                if isinstance(row['metadata'].get('hull_index'), int)
                and not isinstance(row['metadata'].get('hull_index'), bool))
            if indices != list(range(expected_hull_count)):
                errors.append(f'compiled hull index set is not exact for {link}/{source_name}')
    for key, compiled_rows in compiled_part_rows.items():
        if key not in cache_by_part:
            errors.append(f'compiled geometry has no cache source part: {key!r}')
    for row in rows:
        if row['kind'] == 'visual' and row['filename']:
            stem = Path(str(row['filename'])).stem
            if compiled_mesh_names and not any(
                    name == stem or name.endswith('/' + stem)
                    for name in compiled_mesh_names):
                errors.append(
                    f'compiled visual mesh is missing for URDF {row["link"]}/'
                    f'{row["occurrence"]}: {stem}')
    expected_compiled_collision_counts = {}
    for (link, _part), cached in cache_by_part.items():
        hull_shas = cached.get('hull_sha256') if isinstance(cached, Mapping) else None
        if isinstance(hull_shas, list):
            expected_compiled_collision_counts[link] = (
                expected_compiled_collision_counts.get(link, 0) + len(hull_shas))
    for name in expected_links:
        expected_collision = expected_compiled_collision_counts.get(name, 0)
        actual_collision = compiled_collision_counts.get(name, 0)
        if actual_collision != expected_collision:
            errors.append(
                f'compiled collision geometry count for {name} is not exact: '
                f'{actual_collision} != cache {expected_collision}')
        if actual_collision <= 0:
            errors.append(f'compiled link {name} has no collision geometry')
    canonical = json.dumps(rows, sort_keys=True, ensure_ascii=False,
                           separators=(',', ':')).encode('utf-8')
    return {
        'status': 'PASS' if not errors else 'FAIL',
        'rows': rows,
        'row_count': len(rows),
        'collision_counts': dict(sorted(collision_counts.items())),
        'visual_counts': dict(sorted(visual_counts.items())),
        'compiled_collision_counts': dict(sorted(compiled_collision_counts.items())),
        'compiled_visual_counts': dict(sorted(compiled_visual_counts.items())),
        'compiled_mesh_rows': compiled_mesh_rows,
        'compiled_mesh_names': sorted(compiled_mesh_names),
        'source_part_bindings': source_part_bindings,
        'sha256': hashlib.sha256(canonical).hexdigest(),
        'errors': errors,
    }


def _independent_link_contract(model_path: Path, compiled_model, parts: Mapping,
                               compiled_index: Mapping | None = None) -> dict:
    """Derive the audited link set from URDF/XML and compiled geoms.

    ``parts`` is used only to bind each independently derived link to at least
    one current source mesh.  It is never used to decide which links should be
    audited; that set comes from the generated URDF and ``LINK_PARENT_FRAME``.
    """
    errors = []
    try:
        root = ET.parse(Path(model_path)).getroot()
    except (OSError, ET.ParseError) as exc:
        return {'status': 'FAIL', 'errors': [f'generated URDF cannot be parsed: {exc}'],
                'expected_links': [], 'intentional_empty_links': []}
    urdf_links = []
    urdf_geom_links = set()
    for element in root.findall('link'):
        name = element.get('name')
        if not isinstance(name, str) or not name:
            errors.append('generated URDF contains a link without a name')
            continue
        if name in urdf_links:
            errors.append(f'generated URDF contains duplicate link: {name}')
        urdf_links.append(name)
        if element.findall('collision') or element.findall('visual'):
            urdf_geom_links.add(name)
    urdf_set = set(urdf_links)
    frame_set = set(getattr(E, 'LINK_PARENT_FRAME', {}))
    if urdf_set != frame_set:
        errors.append(
            f'URDF/LINK_PARENT_FRAME link sets differ: '
            f'urdf={sorted(urdf_set)!r} frames={sorted(frame_set)!r}')
    unknown_empty = PRINT_FIRST_INTENTIONAL_EMPTY_LINKS - urdf_set
    if unknown_empty:
        errors.append(f'intentional empty link allowlist is not in URDF: {sorted(unknown_empty)!r}')
    expected_links = sorted(urdf_set - PRINT_FIRST_INTENTIONAL_EMPTY_LINKS)
    empty_links = sorted(urdf_set & PRINT_FIRST_INTENTIONAL_EMPTY_LINKS)
    source_nonempty = {
        str(link) for link, items in parts.items()
        if isinstance(items, Sequence) and not isinstance(items, (str, bytes)) and len(items)
    }
    source_keys = {str(link) for link in parts}
    source_empty = source_keys - source_nonempty
    if source_nonempty != set(expected_links):
        errors.append(
            f'source link set differs from independently expected links: '
            f'source={sorted(source_nonempty)!r} expected={expected_links!r}')
    if source_empty != set(empty_links):
        errors.append(
            f'source empty-link set is not the explicit allowlist: '
            f'source={sorted(source_empty)!r} allowed={empty_links!r}')
    if source_keys - urdf_set:
        errors.append(f'source contains links absent from URDF: {sorted(source_keys - urdf_set)!r}')
    compiled_geom_links = set()
    if compiled_model is None:
        errors.append('compiled model is missing for independent link contract')
    else:
        for geom_index in range(int(compiled_model.ngeom)):
            body_id = int(compiled_model.geom_bodyid[geom_index])
            if body_id <= 0:
                continue
            name = mujoco.mj_id2name(
                compiled_model, mujoco.mjtObj.mjOBJ_BODY, body_id)
            if isinstance(name, str) and name:
                compiled_geom_links.add(name)
        if compiled_geom_links != set(expected_links):
            errors.append(
                f'compiled geom link set differs from expected: '
                f'compiled={sorted(compiled_geom_links)!r} expected={expected_links!r}')
    if urdf_geom_links != set(expected_links):
        errors.append(
            f'URDF visual/collision link set differs from expected: '
            f'urdf_geom={sorted(urdf_geom_links)!r} expected={expected_links!r}')
    geometry = _urdf_geometry_contract(
        Path(model_path), root, expected_links, compiled_model, parts, compiled_index)
    if geometry.get('status') != 'PASS':
        errors.extend(geometry.get('errors', []))
    return {
        'status': 'PASS' if not errors else 'FAIL',
        'urdf_links': sorted(urdf_set),
        'frame_links': sorted(frame_set),
        'expected_links': expected_links,
        'intentional_empty_links': empty_links,
        'urdf_geom_links': sorted(urdf_geom_links),
        'source_links': sorted(source_nonempty),
        'compiled_geom_links': sorted(compiled_geom_links),
        'geometry_contract': geometry,
        'errors': errors,
    }


def _source_bundle() -> tuple[dict[str, list[tuple]], dict]:
    """生成済みprint-first contextの部品と完全な在庫台帳を得る。"""
    if not bool(getattr(E.C, "PRINT_FIRST_ACTIVE", False)):
        raise ValueError("proxy proof must run inside generated print-first context")
    parts = COLLISION.parts_with_pad(include_servos=True)
    # sim_collision.parts_with_pad() already includes the serialized
    # electronics reservation envelopes in the generated context. Keep the
    # exact same source bundle for inventory and compiled-model binding.
    inventory = SELF._print_first_inventory_contract(
        parts, context_generated=True, require_stl_sha=True)
    if inventory.get("status") != "PASS":
        failed = [name for name, value in inventory.get("checks", {}).items()
                  if value is not True]
        raise ValueError("print-first inventory contract failed: " + ", ".join(failed))
    # Return the complete generated source bundle.  The proxy proof used to
    # select only the five adjacent-link bodies, which left the all-link audit
    # vulnerable to a forged/empty result for a non-exempt pair.  Inventory
    # validation above has already checked every raw mesh before any source is
    # transformed; retain that exact complete set for the independent Boolean
    # recomputation below.
    for link in {
        name for pair in COLLISION.PRINT_FIRST_PROXY_EXCLUSION_PAIRS
        for name in pair
    }:
        if link not in parts or not parts[link]:
            raise ValueError(f"proxy proof link has no source parts: {link}")
    return parts, inventory


def _world_parts(parts: Mapping[str, Sequence[tuple]],
                 links: Iterable[str], qdeg: Mapping[str, float]) -> dict[str, list[tuple[str, trimesh.Trimesh]]]:
    result: dict[str, list[tuple[str, trimesh.Trimesh]]] = {}
    for link in links:
        frame = np.asarray(E.LINK_PARENT_FRAME[link](dict(qdeg)), dtype=float)
        if frame.shape != (4, 4) or not np.isfinite(frame).all():
            raise ValueError(f"{link}: parent frame is not finite 4x4")
        result[link] = []
        for occurrence, item in enumerate(parts[link]):
            if not isinstance(item, (list, tuple)):
                raise ValueError(f'{link}/{occurrence}: source part is not a tuple')
            if len(item) == 3:
                mesh, _color, name = item
            elif len(item) == 2:
                mesh, name = item
            else:
                raise ValueError(f'{link}/{occurrence}: source part tuple has invalid length')
            if not isinstance(mesh, trimesh.Trimesh) or not isinstance(name, str):
                raise ValueError(f'{link}/{occurrence}: source part is malformed')
            world = mesh.copy()
            world.apply_transform(frame)
            # Transforming a valid closed mesh is not a repair step; verify the
            # resulting world geometry before passing it to Boolean.
            COLLISION._validated_outward_mesh(
                world, f"proxy world {link}/{name}", source=True)
            result[link].append((name, world))
    return result


def _aabb_disjoint(a: trimesh.Trimesh, b: trimesh.Trimesh) -> bool:
    return bool(np.any(a.bounds[1] <= b.bounds[0])
                or np.any(b.bounds[1] <= a.bounds[0]))


def _scan_pair(world: Mapping[str, Sequence[tuple[str, trimesh.Trimesh]]],
               pair: tuple[str, str], sample_index: int,
               sample_label: str) -> dict:
    left, right = pair
    stats = {
        "pair": [left, right],
        "sample_count": 1,
        "bbox_candidate_pairs": 0,
        "boolean_evaluations": 0,
        "positive_below_threshold_count": 0,
        "max_intersection_mm3": 0.0,
        "intersections": [],
        "errors": [],
    }
    for name1, mesh1 in world[left]:
        for name2, mesh2 in world[right]:
            if _aabb_disjoint(mesh1, mesh2):
                continue
            stats["bbox_candidate_pairs"] += 1
            stats["boolean_evaluations"] += 1
            try:
                intersection = trimesh.boolean.intersection(
                    [mesh1, mesh2], engine="manifold", check_volume=False)
                volume = float(SELF._intersection_volume(
                    intersection, f"proxy {sample_label}/{left}/{name1}/{right}/{name2}"))
            except Exception as exc:  # noqa: BLE001 - unresolved exact proof fails closed
                stats["errors"].append({
                    "sample_index": int(sample_index),
                    "sample": sample_label,
                    "part1": name1,
                    "part2": name2,
                    "error": str(exc),
                })
                continue
            if not math.isfinite(volume) or volume < 0.0:
                stats["errors"].append({
                    "sample_index": int(sample_index),
                    "sample": sample_label,
                    "part1": name1,
                    "part2": name2,
                    "error": f"invalid exact Boolean volume {volume!r}",
                })
                continue
            stats["max_intersection_mm3"] = max(
                float(stats["max_intersection_mm3"]), volume)
            if volume > INTERSECTION_THRESHOLD_MM3:
                stats["intersections"].append({
                    "sample_index": int(sample_index),
                    "sample": sample_label,
                    "part1": name1,
                    "part2": name2,
                    "intersection_mm3": volume,
                })
            elif volume > 0.0:
                stats["positive_below_threshold_count"] += 1
    return stats


def _merge_pair_stats(rows: Iterable[dict], pair: tuple[str, str], *, samples: int) -> dict:
    merged = {
        "pair": list(pair),
        "sample_count": int(samples),
        "bbox_candidate_pairs": 0,
        "boolean_evaluations": 0,
        "positive_below_threshold_count": 0,
        "max_intersection_mm3": 0.0,
        "intersections": [],
        "errors": [],
    }
    for row in rows:
        merged["bbox_candidate_pairs"] += int(row["bbox_candidate_pairs"])
        merged["boolean_evaluations"] += int(row["boolean_evaluations"])
        merged["positive_below_threshold_count"] += int(
            row["positive_below_threshold_count"])
        merged["max_intersection_mm3"] = max(
            float(merged["max_intersection_mm3"]),
            float(row["max_intersection_mm3"]))
        merged["intersections"].extend(row["intersections"])
        merged["errors"].extend(row["errors"])
    merged["clean"] = not merged["intersections"] and not merged["errors"]
    return merged


def _qdeg_from_native(row: np.ndarray) -> dict[str, float]:
    if row.shape != (43,) or not np.isfinite(row).all():
        raise ValueError("native trace row must be finite shape (43,)")
    return {name: float(value)
            for name, value in zip(SELF.S.ALL_JOINTS, row[3:23])}


def _scan_native(parts, native: np.ndarray) -> dict:
    per_pair: dict[tuple[str, str], list[dict]] = {
        pair: [] for pair in COLLISION.PRINT_FIRST_PROXY_EXCLUSION_PAIRS}
    links = sorted({name for pair in per_pair for name in pair})
    for index, row in enumerate(native):
        qdeg = _qdeg_from_native(row)
        world = _world_parts(parts, links, qdeg)
        for pair in per_pair:
            per_pair[pair].append(_scan_pair(
                world, pair, index, f"native_{index:04d}"))
    merged_pairs = [_merge_pair_stats(rows, pair, samples=len(native))
                    for pair, rows in per_pair.items()]
    return {
        "status": "PASS_FINITE_TRACE" if all(
            row["clean"] for row in merged_pairs) else "FAIL",
        "pose_count": int(len(native)),
        "pairs": merged_pairs,
        "interpretation": (
            "native C++ traceの全有限行を実STL Booleanで調べた有限証拠。"
            "連続到達集合の証明ではない。"),
    }


def _dense_samples_for_pair(pair: tuple[str, str], native: np.ndarray,
                            sample_count: int) -> tuple[np.ndarray, str]:
    joint = COLLISION.PRINT_FIRST_PROXY_EXCLUSION_JOINTS.get(pair)
    if not isinstance(joint, str):
        raise ValueError(f"proxy pair has no reviewed joint: {pair}")
    specs = {row["name"]: row for row in E.JOINT_SPECS
             if row.get("kind") == "revolute"}
    if joint not in specs:
        raise ValueError(f"reviewed exclusion joint is absent: {joint}")
    limit = specs[joint].get("limit")
    if not isinstance(limit, (tuple, list)) or len(limit) != 2:
        raise ValueError(f"joint {joint} has no finite limit")
    lo, hi = (float(limit[0]), float(limit[1]))
    if not math.isfinite(lo) or not math.isfinite(hi) or not hi >= lo:
        raise ValueError(f"joint {joint} limits are invalid")
    return np.linspace(lo, hi, sample_count, dtype=float), joint


def _scan_dense(parts, native: np.ndarray, sample_count: int) -> dict:
    base = _qdeg_from_native(native[0])
    links = sorted({name for pair in COLLISION.PRINT_FIRST_PROXY_EXCLUSION_PAIRS
                    for name in pair})
    pair_reports = []
    for pair in COLLISION.PRINT_FIRST_PROXY_EXCLUSION_PAIRS:
        values, joint = _dense_samples_for_pair(pair, native, sample_count)
        rows = []
        for index, value in enumerate(values):
            qdeg = dict(base)
            qdeg[joint] = float(value)
            world = _world_parts(parts, links, qdeg)
            rows.append(_scan_pair(
                world, pair, index, f"dense_{joint}_{index:04d}"))
        merged = _merge_pair_stats(rows, pair, samples=len(values))
        merged["joint"] = joint
        merged["joint_limit_deg"] = [float(values[0]), float(values[-1])]
        pair_reports.append(merged)
    return {
        "status": "PASS_DENSE_JOINT_LIMITS" if all(
            row["clean"] for row in pair_reports) else "FAIL",
        "sample_count_per_pair": int(sample_count),
        "pairs": pair_reports,
        "interpretation": (
            "各固定除外組に対応する一軸を関節限界内で密掃引した有限証拠。"
            "他軸との同時可動や連続集合を証明しない。"),
    }


def _canonical_source_pair(row: Mapping[str, object]) -> dict:
    """一姿勢一リンク組のBoolean証拠を比較可能な形へ正規化する。"""
    if not isinstance(row, Mapping):
        raise ValueError('source pair evidence is not a mapping')
    links = row.get('links', row.get('pair'))
    if (not isinstance(links, (list, tuple)) or len(links) != 2
            or any(not isinstance(value, str) for value in links)):
        raise ValueError('source pair evidence links are malformed')

    def integer(name, default=None):
        value = row.get(name, default)
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or int(value) != value or int(value) < 0):
            raise ValueError(f'source pair evidence {name} is invalid')
        return int(value)

    def number(name, default=0.0):
        value = row.get(name, default)
        try:
            value = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f'source pair evidence {name} is invalid') from exc
        if not math.isfinite(value) or value < 0.0:
            raise ValueError(f'source pair evidence {name} is invalid')
        return value

    intersections = row.get('actual_intersections', row.get('intersections'))
    errors = row.get('errors')
    if not isinstance(intersections, list) or not isinstance(errors, list):
        raise ValueError('source pair evidence result lists are missing')
    canonical_intersections = []
    for item in intersections:
        if not isinstance(item, Mapping):
            raise ValueError('source pair intersection is malformed')
        part1, part2 = item.get('part1'), item.get('part2')
        try:
            volume = float(item.get('intersection_mm3'))
        except (TypeError, ValueError) as exc:
            raise ValueError('source pair intersection volume is invalid') from exc
        if (not isinstance(part1, str) or not isinstance(part2, str)
                or not math.isfinite(volume) or volume <= INTERSECTION_THRESHOLD_MM3):
            raise ValueError('source pair intersection is invalid')
        canonical_intersections.append((part1, part2, volume))
    canonical_errors = []
    for item in errors:
        if not isinstance(item, Mapping) or not isinstance(item.get('error'), str):
            raise ValueError('source pair Boolean error is malformed')
        canonical_errors.append((
            item.get('part1'), item.get('part2'), item.get('error')))
    canonical_intersections.sort(key=lambda item: (item[0], item[1], item[2]))
    canonical_errors.sort(key=lambda item: tuple('' if value is None else str(value)
                                                 for value in item))
    return {
        'links': [str(links[0]), str(links[1])],
        'sample_count': integer('sample_count'),
        'bbox_candidate_pairs': integer('bbox_candidate_pairs'),
        'boolean_evaluations': integer('boolean_evaluations'),
        'positive_below_threshold_count': integer('positive_below_threshold_count'),
        'max_intersection_mm3': number('max_intersection_mm3'),
        'intersections': [list(item) for item in canonical_intersections],
        'errors': [list(item) for item in canonical_errors],
        'clean': not canonical_intersections and not canonical_errors,
    }


def _independent_all_pair_source_check(parts, native: np.ndarray, *, payload=None,
                                       expected_links: Sequence[str] | None = None) -> dict:
    """現行source部品から全リンク組を独立に再計算する。

    保存監査のstatus/countを信頼せず、指定native行ごとに現在の生成メッシュを
    worldへ配置してBooleanを再実行する。保存行がある場合は候補数とBooleanの
    正規化結果も比較し、emptyな偽造行や別形状由来の行を結合段階で拒否する。
    """
    errors = []
    if not isinstance(parts, Mapping):
        return {'status': 'FAIL', 'errors': ['source parts are not a mapping']}
    values = np.asarray(native, dtype=float)
    if values.ndim != 2 or values.shape[1] != 43 or not np.isfinite(values).all():
        return {'status': 'FAIL', 'errors': ['native trace for source recomputation is invalid']}
    source_links = sorted(str(link) for link, items in parts.items() if items)
    links = (sorted(str(link) for link in expected_links)
             if expected_links is not None else source_links)
    if expected_links is not None and source_links != links:
        errors.append(
            f'source link set differs from independently derived expected set: '
            f'{source_links!r} != {links!r}')
    if len(set(links)) != len(links) or len(links) < 2:
        return {'status': 'FAIL', 'errors': ['source link inventory has fewer than two unique links']}
    expected_pairs = list(itertools.combinations(links, 2))
    stored_poses = payload.get('poses') if isinstance(payload, Mapping) else None
    if payload is not None and (not isinstance(stored_poses, list)
                                or len(stored_poses) != len(values)):
        errors.append('stored audit poses do not match independent native count')
        stored_poses = stored_poses if isinstance(stored_poses, list) else []
    pair_rows = {pair: [] for pair in expected_pairs}
    evidence_rows = []
    for pose_index, row in enumerate(values):
        qdeg = _qdeg_from_native(row)
        try:
            world = _world_parts(parts, links, qdeg)
        except Exception as exc:  # noqa: BLE001 - source geometry failure is hard
            errors.append(f'independent world source validation failed at pose {pose_index}: {exc}')
            continue
        stored_by_pair = {}
        if pose_index < len(stored_poses) and isinstance(stored_poses[pose_index], Mapping):
            stored_rows = stored_poses[pose_index].get('pairs')
            if isinstance(stored_rows, list):
                for stored_row in stored_rows:
                    if isinstance(stored_row, Mapping):
                        links_value = stored_row.get('links')
                        if isinstance(links_value, list) and len(links_value) == 2:
                            key = (links_value[0], links_value[1])
                            if key in stored_by_pair:
                                errors.append(
                                    f'stored audit pose {pose_index} duplicates pair {key!r}')
                            stored_by_pair[key] = stored_row
        for pair in expected_pairs:
            recomputed = _scan_pair(
                world, pair, pose_index, f'independent_native_{pose_index:04d}')
            pair_rows[pair].append(recomputed)
            canonical = _canonical_source_pair(recomputed)
            evidence_rows.append({'pose_index': int(pose_index), **canonical})
            stored_row = stored_by_pair.get(pair)
            if payload is None:
                continue
            if stored_row is None:
                errors.append(f'stored audit pose {pose_index} is missing pair {pair!r}')
                continue
            try:
                stored_canonical = _canonical_source_pair(stored_row)
            except ValueError as exc:
                errors.append(f'stored audit pose {pose_index} pair {pair!r}: {exc}')
                continue
            # Do not compare hull penetration here; that is MuJoCo proxy evidence.
            # Compare the source Boolean result itself, including the candidate
            # count and all numerical/list fields needed to reproduce it.
            if stored_canonical != canonical:
                errors.append(
                    f'stored audit source Boolean differs at pose {pose_index} pair {pair!r}')
        if payload is not None and set(stored_by_pair) != set(expected_pairs):
            errors.append(f'stored audit pose {pose_index} pair set differs from current links')
    merged = [_merge_pair_stats(pair_rows[pair], pair, samples=len(values))
              for pair in expected_pairs]
    independent_clean = all(row['clean'] for row in merged)
    canonical_bytes = json.dumps(
        evidence_rows, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False).encode('utf-8')
    return {
        'status': 'PASS' if independent_clean and not errors else 'FAIL',
        'pose_count': int(len(values)),
        'link_names': links,
        'expected_link_pair_count': len(expected_pairs),
        'pairs': merged,
        'independent_clean': independent_clean,
        'stored_rows_compared': payload is not None,
        'stored_evidence_mismatches': len(errors),
        'evidence_sha256': hashlib.sha256(canonical_bytes).hexdigest(),
        'errors': errors[:100],
        'errors_truncated': max(0, len(errors) - 100),
        'interpretation': (
            '現行生成済みsource部品を指定native traceの各姿勢へ配置し、全リンク組を'
            '独立に実STL Boolean再計算した有限証拠。連続到達集合の証明ではない。'),
    }


def _compiled_proxy_exclusion_report(model, contract: Mapping[str, object]) -> dict:
    """コンパイル後の除外5組・親子関係・MuJoCo設定を再計算する。"""
    expected_pairs = contract.get("pairs")
    if not isinstance(expected_pairs, list):
        raise ValueError("proxy exclusion contract pairs are malformed")
    expected_signatures = []
    errors = []
    expected_parent_rows = []
    disableflags = getattr(getattr(model, "opt", None), "disableflags", None)
    filter_parent_bit = int(mujoco.mjtDisableBit.mjDSBL_FILTERPARENT)
    parent_filter_disabled = bool(
        disableflags is not None and int(disableflags) & filter_parent_bit)
    if disableflags is None:
        errors.append("compiled model has no opt.disableflags")
    elif not parent_filter_disabled:
        errors.append("compiled model does not disable global parent filtering")
    for row in expected_pairs:
        if not isinstance(row, dict):
            errors.append("proxy exclusion contract row is not an object")
            continue
        names = (row.get("body1"), row.get("body2"))
        if any(not isinstance(name, str) or not name for name in names):
            errors.append(f"proxy exclusion body names are invalid: {names!r}")
            continue
        ids = [int(mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, name))
               for name in names]
        if any(body_id < 0 for body_id in ids):
            errors.append(f"proxy exclusion body is absent: {names!r}")
            continue
        expected_signatures.append((min(ids) << 16) | max(ids))
        joint_name = row.get("joint")
        joint_spec = next((spec for spec in E.JOINT_SPECS
                           if spec.get("name") == joint_name), None)
        if not isinstance(joint_spec, dict):
            errors.append(f"proxy exclusion joint is absent from JOINT_SPECS: {joint_name!r}")
            continue
        parent_name, child_name = joint_spec.get("parent"), joint_spec.get("child")
        if {parent_name, child_name} != set(names):
            errors.append(
                f"proxy exclusion {names!r} is not the body pair for joint {joint_name!r}")
            continue
        parent_id = int(mujoco.mj_name2id(
            model, mujoco.mjtObj.mjOBJ_BODY, parent_name))
        child_id = int(mujoco.mj_name2id(
            model, mujoco.mjtObj.mjOBJ_BODY, child_name))
        if parent_id < 0 or child_id < 0:
            errors.append(f"proxy exclusion joint bodies are absent: {joint_name!r}")
            continue
        body_parentid = getattr(model, "body_parentid", None)
        if body_parentid is None or child_id >= len(body_parentid):
            errors.append(f"compiled model has no parent relation for {joint_name!r}")
        elif int(body_parentid[child_id]) != parent_id:
            errors.append(
                f"compiled body relation for {joint_name!r} is not parent-child")
        joint_id = int(mujoco.mj_name2id(
            model, mujoco.mjtObj.mjOBJ_JOINT, joint_name))
        joint_bodyid = getattr(model, "jnt_bodyid", None)
        if joint_id < 0 or joint_bodyid is None or joint_id >= len(joint_bodyid):
            errors.append(f"compiled joint body relation is missing: {joint_name!r}")
        elif int(joint_bodyid[joint_id]) != child_id:
            errors.append(
                f"compiled joint {joint_name!r} does not drive the child body")
        expected_parent_rows.append({
            "joint": joint_name,
            "parent": parent_name,
            "child": child_name,
            "parent_id": parent_id,
            "child_id": child_id,
        })

    compiled = []
    for index in range(int(model.nexclude)):
        exclude = model.exclude(index)
        signatures = np.asarray(exclude.signature, dtype=np.uint64).reshape(-1)
        if signatures.size != 1:
            errors.append(f"compiled exclude {index} signature is not scalar")
            continue
        value = int(signatures[0])
        compiled.append({"name": str(exclude.name), "signature": value})
    actual_signatures = [row["signature"] for row in compiled]
    if int(model.nexclude) != len(expected_signatures):
        errors.append(
            f"compiled model nexclude={model.nexclude} expected {len(expected_signatures)}")
    if sorted(actual_signatures) != sorted(expected_signatures):
        errors.append(
            f"compiled exclude signatures {sorted(actual_signatures)!r} do not match "
            f"expected {sorted(expected_signatures)!r}")
    return {
        "status": "PASS" if not errors else "FAIL",
        "expected_count": int(len(expected_signatures)),
        "compiled_count": int(model.nexclude),
        "expected_signatures": sorted(expected_signatures),
        "compiled_excludes": compiled,
        "parent_filter_disabled": parent_filter_disabled,
        "parent_filter_disable_bit": filter_parent_bit,
        "expected_parent_relations": expected_parent_rows,
        "errors": errors,
    }


def _compile_current_proxy_model(case: Mapping[str, object], model_path: Path,
                                 contract: Mapping[str, object]):
    """現URDFを同一の物理ビルダでコンパイルし、除外/関節範囲を得る。"""
    import sim_stress as STRESS
    normalized = STRESS._validate_case_inputs(dict(case))
    options = normalized["model"]
    gains = normalized["gains"]
    old_urdf = SELF.S.URDF_PATH
    SELF.S.URDF_PATH = Path(model_path).resolve()
    try:
        model, index = SELF.S.build_model(
            options.get("friction", 1.0), gains["kp"], gains["kv"],
            timestep=options.get("timestep", 0.002),
            effort_scale=options.get("effort_scale", 1.0),
            mass_scale=options.get("mass_scale", 1.0),
            self_collision=options.get("self_collision", False),
            include_parent_collision=options.get("include_parent_collision", False),
            slope_deg=options.get("slope_deg", 0.0),
            step_height_mm=options.get("step_height_mm", 0.0),
            step_front_y=options.get("step_front_y", 0.25),
            contact_model=options.get("contact_model", "linked-hulls"),
            hard_friction=options.get("hard_friction", 0.3),
            include_servo_collision=options.get("include_servo_collision", False),
            foot_candidate_dir=options.get("foot_candidate_dir"),
        )
    finally:
        SELF.S.URDF_PATH = old_urdf
    compiled = _compiled_proxy_exclusion_report(model, contract)
    # ``build_model`` records a second copy of this evidence.  Compare it too,
    # so a future wrapper cannot report a Python contract that differs from the
    # compiled MjModel object consumed by the checker.
    index_compiled = index.get("proxy_exclusion_compiled")
    if not isinstance(index_compiled, dict):
        compiled["errors"].append("build_model did not return compiled proxy evidence")
    elif (index_compiled.get("status") != compiled.get("status")
          or index_compiled.get("compiled_count") != compiled.get("compiled_count")
          or sorted(index_compiled.get("expected_signatures", []))
             != sorted(compiled.get("expected_signatures", []))
          or sorted(row.get("signature") for row in index_compiled.get(
              "compiled_excludes", [])) != sorted(compiled.get("expected_signatures", []))):
        compiled["errors"].append(
            "build_model proxy evidence differs from compiled MjModel evidence")
    if compiled["errors"]:
        compiled["status"] = "FAIL"
    source_urdf_value = options.get("source_urdf")
    if source_urdf_value is not None:
        source_urdf = _resolve(source_urdf_value, label="source URDF")
        try:
            source_root = ET.parse(source_urdf).getroot()
        except (OSError, ET.ParseError) as exc:
            raise ValueError(f"source URDF cannot be parsed: {source_urdf}") from exc
        # Reuse the XML row parser to carry an independent path/hash/transform
        # expectation into the link contract.  The source rows are intentionally
        # collected without a compiled model or source parts; only their exact
        # geometry identity is needed here.
        source_rows = _urdf_geometry_contract(
            source_urdf, source_root, [], None, {}).get("rows")
        if not isinstance(source_rows, list):
            raise ValueError("source URDF geometry rows are malformed")
        index["expected_urdf_geometry_rows"] = source_rows
    cache_ledger = index.get("collision_cache_ledger")
    if not isinstance(cache_ledger, dict):
        raise ValueError("build_model did not return collision cache ledger")
    return model, compiled, normalized, cache_ledger, index


def _joint_limit_report(model) -> dict:
    """E.JOINT_SPECS とコンパイル済みMuJoCo joint rangeを突合する。"""
    specs = {
        row.get("name"): row for row in E.JOINT_SPECS
        if isinstance(row, dict) and row.get("kind") == "revolute"
    }
    errors = []
    rows = []
    if tuple(specs) != tuple(SELF.S.ALL_JOINTS):
        errors.append("E.JOINT_SPECS revolute order does not match all 20 axes")
    for name in SELF.S.ALL_JOINTS:
        spec = specs.get(name)
        if not isinstance(spec, dict):
            errors.append(f"joint spec is missing: {name}")
            continue
        limit = spec.get("limit")
        if not isinstance(limit, (list, tuple)) or len(limit) != 2:
            errors.append(f"joint spec limit is malformed: {name}")
            continue
        expected = np.radians(np.asarray(limit, dtype=float))
        jid = int(mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name))
        if jid < 0:
            errors.append(f"compiled joint is missing: {name}")
            continue
        actual = np.asarray(model.jnt_range[jid], dtype=float)
        finite = bool(actual.shape == (2,) and np.isfinite(actual).all())
        match = bool(finite and np.allclose(actual, expected, rtol=1e-9,
                                            atol=1e-9))
        if not match:
            errors.append(
                f"joint range mismatch {name}: compiled={actual.tolist()} "
                f"expected={expected.tolist()}")
        rows.append({
            "name": name,
            "jid": jid,
            "qpos_adr": int(model.jnt_qposadr[jid]),
            "expected_deg": [float(limit[0]), float(limit[1])],
            "expected_rad": expected.tolist(),
            "compiled_rad": actual.tolist() if finite else None,
            "match": match,
        })
    return {
        "status": "PASS" if not errors and len(rows) == len(SELF.S.ALL_JOINTS)
                  else "FAIL",
        "axis_count": len(rows),
        "axes": rows,
        "errors": errors,
    }


def _native_joint_range_report(native: np.ndarray, limits: Mapping[str, object]) -> dict:
    """native角度が上流の20軸関節範囲を越えていないことを検査する。"""
    errors = []
    rows = limits.get("axes", [])
    by_name = {row.get("name"): row for row in rows if isinstance(row, dict)}
    for pose_index, values in enumerate(native[:, 3:23]):
        for axis, name in enumerate(SELF.S.ALL_JOINTS):
            row = by_name.get(name)
            if not row or row.get("match") is not True:
                errors.append(f"native range lacks compiled limit for {name}")
                continue
            lo, hi = row["expected_deg"]
            value = float(values[axis])
            if value < lo - 1e-7 or value > hi + 1e-7:
                errors.append(
                    f"native pose {pose_index} {name}={value} deg outside [{lo}, {hi}]")
    return {
        "status": "PASS" if not errors else "FAIL",
        "pose_count": int(len(native)),
        "axis_count": int(len(SELF.S.ALL_JOINTS)),
        "errors": errors,
    }


def _qpos_range_report(model, qpos: Sequence[float]) -> dict:
    """保存済みqposの全20軸をコンパイル済み範囲へ照合する。"""
    values = np.asarray(qpos, dtype=float)
    errors = []
    if values.ndim != 1 or not np.isfinite(values).all():
        return {"status": "FAIL", "errors": ["qpos must be finite and one-dimensional"]}
    for name in SELF.S.ALL_JOINTS:
        jid = int(mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name))
        if jid < 0:
            errors.append(f"compiled joint is missing: {name}")
            continue
        qadr = int(model.jnt_qposadr[jid])
        if qadr < 0 or qadr >= len(values):
            errors.append(f"qpos address is out of range for {name}: {qadr}")
            continue
        limits = np.asarray(model.jnt_range[jid], dtype=float)
        value = float(values[qadr])
        if (limits.shape != (2,) or not np.isfinite(limits).all()
                or value < float(limits[0]) - 1e-9
                or value > float(limits[1]) + 1e-9):
            errors.append(
                f"{name}={value!r} outside compiled range {limits.tolist()}")
    return {
        "status": "PASS" if not errors else "FAIL",
        "axis_count": len(SELF.S.ALL_JOINTS),
        "errors": errors,
    }


def _qdeg_from_qpos(model, qpos: Sequence[float]) -> dict[str, float]:
    """今回サンプリングしたqposを関節名へ戻す（固定添字を仮定しない）。"""
    values = np.asarray(qpos, dtype=float)
    if values.ndim != 1 or not np.isfinite(values).all():
        raise ValueError("qpos must be finite and one-dimensional")
    result = {}
    for axis, name in enumerate(SELF.S.ALL_JOINTS):
        if model is None:
            qadr = 7 + axis
        else:
            jid = int(mujoco.mj_name2id(
                model, mujoco.mjtObj.mjOBJ_JOINT, name))
            if jid < 0:
                raise ValueError(f"compiled joint is missing: {name}")
            qadr = int(model.jnt_qposadr[jid])
        if qadr < 0 or qadr >= len(values):
            raise ValueError(f"qpos address is out of range for {name}: {qadr}")
        result[name] = float(np.degrees(values[qadr]))
    return result


def _native_control_index(step_index: int, timestep: float,
                          native_count: int) -> int:
    control_steps = round(1.0 / SELF.S.SERVO_HZ / timestep)
    if control_steps <= 0 or not math.isclose(
            control_steps * timestep, 1.0 / SELF.S.SERVO_HZ,
            rel_tol=0.0, abs_tol=1e-12):
        raise ValueError('physical timestep must divide the native control period')
    if native_count <= 0:
        raise ValueError('native trace has no control rows')
    return min(step_index // control_steps, native_count - 1)


def _operational_native_trace(native: np.ndarray, case: Mapping[str, object]) -> tuple[np.ndarray, int]:
    """Return the 50 Hz control rows and their source-row offset.

    Exported traces contain one explicit initial row.  ``sim_stress.execute``
    integrates the following operational rows, so the checker must not bind a
    saved physics row to the initial row merely because both arrays happen to
    have a valid shape.  Accept the two representations used by the producer
    (full trace or already-sliced operational trace), but require the case
    expanded count to disambiguate them.
    """
    values = np.asarray(native, dtype=float)
    if values.ndim != 2 or values.shape[1] != 43 or values.shape[0] <= 0:
        raise ValueError('case-bound native trace is malformed')
    segments = case.get('segments')
    if not isinstance(segments, list) or not segments:
        raise ValueError('physical case segments are missing')
    count = 0
    for index, segment in enumerate(segments):
        if not isinstance(segment, Mapping):
            raise ValueError(f'physical case segment {index} is malformed')
        duration = float(segment.get('duration'))
        steps = round(duration * SELF.S.SERVO_HZ)
        if not math.isfinite(duration) or duration <= 0 or steps <= 0 \
                or not math.isclose(duration * SELF.S.SERVO_HZ, steps,
                                    rel_tol=0.0, abs_tol=1e-8):
            raise ValueError('physical case segment duration is not a 50 Hz multiple')
        count += steps
    if values.shape[0] == count + 1:
        return values[1:], 1
    if values.shape[0] == count:
        return values, 0
    raise ValueError(
        f'case-bound native trace row count {values.shape[0]} does not match '
        f'operational count {count} or full count {count + 1}')


def _native_segment_command(case: Mapping[str, object], control_index: int) -> list[float]:
    segments = case.get('segments')
    if not isinstance(segments, list) or not segments:
        raise ValueError('physical case segments are missing')
    edges = np.cumsum([float(segment['duration']) for segment in segments])
    segment_index = min(int(np.searchsorted(
        edges, control_index / SELF.S.SERVO_HZ + 1e-10, side='right')),
        len(segments) - 1)
    segment = segments[segment_index]
    return [float(segment.get('vx', 0.)), float(segment.get('vy', 0.)),
            float(segment.get('wz', 0.)),
            float(segment.get('body_h', SELF.S.BODY_H_DEFAULT))]


def _deterministic_native_physical_replay(case: Mapping[str, object],
                                          native: np.ndarray,
                                          result_path: Path) -> Mapping[str, object]:
    """Re-run the saved native controls using the current compiled model.

    The checker supplies the already validated native trace, so this replay
    does not trust a result's self-reported angles or qpos.  It is kept as a
    small function to make the expensive post-hoc operation directly
    replaceable in focused tests without weakening the production path.
    """
    import copy
    import sim_stress as STRESS
    full_values = np.asarray(native, dtype=float).copy()
    if full_values.ndim != 2 or full_values.shape[1] != 43:
        raise ValueError('deterministic replay native trace is malformed')
    operational_values, initial_offset = _operational_native_trace(full_values, case)
    if initial_offset != 1:
        raise ValueError(
            'deterministic replay requires the original native initial row; '
            'an operational-only trace is not accepted')
    expected_mode = STRESS.native_trace_mode(case)
    old_trace = STRESS.native_output_trace
    old_output_root = STRESS.FINGERPRINT_OUTPUT_ROOT
    old_cache = COLLISION.CACHE
    old_collision_output_root = COLLISION.OUTPUT_ROOT
    replay_root = Path(result_path).resolve().parent.parent / '.deterministic-replay'
    replay_root.mkdir(parents=True, exist_ok=True)
    replay_cache = replay_root / 'collision-cache'
    replay_cache.mkdir(parents=True, exist_ok=True)

    def supplied_trace(_case, include_initial=False):
        if include_initial:
            return full_values.copy()
        return operational_values.copy()

    try:
        STRESS.native_output_trace = supplied_trace
        STRESS.FINGERPRINT_OUTPUT_ROOT = replay_root
        COLLISION.CACHE = replay_cache
        COLLISION.OUTPUT_ROOT = replay_root
        replay = STRESS.execute(copy.deepcopy(dict(case)), replay_root / 'results')
    finally:
        STRESS.native_output_trace = old_trace
        STRESS.FINGERPRINT_OUTPUT_ROOT = old_output_root
        COLLISION.CACHE = old_cache
        COLLISION.OUTPUT_ROOT = old_collision_output_root
    if not isinstance(replay, Mapping):
        raise ValueError('deterministic replay did not return a result object')
    if replay.get('native_trace_mode') != expected_mode:
        raise ValueError(
            'deterministic replay native trace mode does not match the case: '
            f'{replay.get("native_trace_mode")!r} != {expected_mode!r}')
    replay_initial = replay.get('native_trace_initial_row')
    if (not isinstance(replay_initial, list)
            or len(replay_initial) != 43
            or not np.allclose(np.asarray(replay_initial, dtype=float),
                               full_values[0], rtol=0.0, atol=1e-9)):
        raise ValueError('deterministic replay initial row differs from source trace')
    return replay


_REPLAY_SUMMARY_FIELDS = (
    # Integrator completion and posture/contact evidence.
    'initial_position_m', 'total_sim_time_s', 'valid_integrated_time_s',
    'requested_time_s', 'max_abs_roll_deg', 'max_abs_pitch_deg',
    'min_base_z_m', 'max_abs_qvel', 'initial_self_penetration_m',
    'max_self_penetration_m', 'nonleg_contact_steps', 'ik_counts',
    # Complete actuator and model summaries, including all twenty axes.
    'mass_kg', 'joint_order', 'actuators', 'positive_mechanical_power_W',
    'axis_constraint_acceptance', 'enablement_contract', 'tpu_support',
    'segments',
    # Save schedule and native startup provenance.
    'timeseries_expected_step_indices', 'timeseries_expected_row_count',
    'timeseries_sample_stride_steps', 'timeseries_complete',
    'native_trace_mode', 'native_trace_initial_row', 'native_trace_row_count',
)


def _compare_replay_value(expected, actual, path: str, errors: list[str]):
    """Compare replay evidence structurally, with only numeric tolerance."""
    if isinstance(expected, bool) or isinstance(actual, bool):
        if type(expected) is not type(actual) or expected != actual:
            errors.append(f'{path} differs from deterministic replay')
        return
    if isinstance(expected, Mapping) or isinstance(actual, Mapping):
        if not isinstance(expected, Mapping) or not isinstance(actual, Mapping):
            errors.append(f'{path} type differs from deterministic replay')
            return
        if set(expected) != set(actual):
            errors.append(f'{path} keys differ from deterministic replay')
            return
        for key in sorted(expected, key=str):
            _compare_replay_value(expected[key], actual[key],
                                  f'{path}.{key}', errors)
        return
    if isinstance(expected, (list, tuple)) or isinstance(actual, (list, tuple)):
        if not isinstance(expected, (list, tuple)) or not isinstance(actual, (list, tuple)):
            errors.append(f'{path} type differs from deterministic replay')
            return
        if len(expected) != len(actual):
            errors.append(f'{path} length differs from deterministic replay')
            return
        for index, (left, right) in enumerate(zip(expected, actual)):
            _compare_replay_value(left, right, f'{path}[{index}]', errors)
        return
    if isinstance(expected, (int, float)) or isinstance(actual, (int, float)):
        try:
            left = float(expected)
            right = float(actual)
        except (TypeError, ValueError):
            errors.append(f'{path} is not numeric in deterministic replay')
            return
        if not math.isfinite(left) or not math.isfinite(right) \
                or not math.isclose(left, right, rel_tol=0.0, abs_tol=1e-8):
            errors.append(f'{path} differs from deterministic replay')
        return
    if expected != actual:
        errors.append(f'{path} differs from deterministic replay')


def _validate_replay_summary(result: Mapping[str, object],
                             replay: Mapping[str, object] | None,
                             case: Mapping[str, object],
                             native: np.ndarray | None,
                             errors: list[str]):
    """Require replay to recompute the complete physical acceptance summary."""
    if not isinstance(replay, Mapping):
        errors.append('deterministic replay object is missing')
        return
    if replay.get('status') != 'PASS':
        errors.append('deterministic replay status is not PASS')
    result_checks = result.get('checks')
    replay_checks = replay.get('checks')
    if not isinstance(result_checks, Mapping):
        errors.append('physical result checks are missing for replay comparison')
    if not isinstance(replay_checks, Mapping):
        errors.append('deterministic replay checks are missing')
    else:
        # Every boolean gate emitted by the physical executor participates in
        # the replay contract.  This includes no-fall, non-leg-contact,
        # posture/progress and every one of the twenty-axis gates.
        for key, value in replay_checks.items():
            if type(value) is not bool:
                errors.append(f'deterministic replay checks.{key} is not boolean')
            elif value is not True:
                errors.append(f'deterministic replay checks.{key} is not true')
        if isinstance(result_checks, Mapping):
            _compare_replay_value(dict(result_checks), dict(replay_checks),
                                  'checks', errors)
    if isinstance(result_checks, Mapping):
        for key, value in result_checks.items():
            if type(value) is bool and value is not True:
                errors.append(f'physical result checks.{key} is not true')
    for key in _REPLAY_SUMMARY_FIELDS:
        if key not in result:
            errors.append(f'physical result replay summary is missing: {key}')
        if key not in replay:
            errors.append(f'deterministic replay summary is missing: {key}')
        if key in result and key in replay:
            _compare_replay_value(result[key], replay[key], key, errors)
    if native is not None:
        expected_mode = None
        try:
            import sim_stress as STRESS
            expected_mode = STRESS.native_trace_mode(case)
        except Exception as exc:  # noqa: BLE001 - a missing mode is a hard failure
            errors.append(f'native trace mode could not be derived: {exc}')
        if expected_mode is not None:
            if result.get('native_trace_mode') != expected_mode:
                errors.append('physical result native_trace_mode differs from case')
            if replay.get('native_trace_mode') != expected_mode:
                errors.append('deterministic replay native_trace_mode differs from case')
        native_array = np.asarray(native, dtype=float)
        for source, label in ((result, 'physical result'),
                              (replay, 'deterministic replay')):
            initial = source.get('native_trace_initial_row')
            if (not isinstance(initial, list) or len(initial) != 43
                    or not np.allclose(np.asarray(initial, dtype=float),
                                       native_array[0], rtol=0.0, atol=1e-9)):
                errors.append(f'{label} native initial row is not bound to trace')
            if source.get('native_trace_row_count') != int(len(native_array)):
                errors.append(f'{label} native trace row count is not bound to trace')


def _validate_physical_timeseries(result: Mapping[str, object],
                                  case: Mapping[str, object], *,
                                  native: np.ndarray | None = None,
                                  replay: Mapping[str, object] | None = None) -> dict:
    """物理結果をケース区間・保存刻み・時刻列へ結び付ける。

    qposを1行だけ添付した偽のPASSを防ぐため、シミュレーションの刻み数
    と保存規則から期待するstep indexを再計算する。これは保存された
    sampled qposの完全性であり、未保存の積分中軌跡を証明するものではない。
    """
    errors = []
    if result.get('controller_kind') != 'native':
        errors.append('physical result controller_kind must be native')
    checks = result.get('checks')
    if not isinstance(checks, dict):
        errors.append('physical result checks are missing')
    else:
        for name, value in checks.items():
            if type(value) is not bool:
                errors.append(f'physical result checks.{name} is not boolean')
            elif value is not True:
                errors.append(f'physical result checks.{name} is not true')
    options = case.get('model')
    if not isinstance(options, dict):
        errors.append('physical case model is missing')
        options = {}
    try:
        dt = float(options.get('timestep', 0.002))
        durations = [float(segment['duration'])
                     for segment in case.get('segments', [])]
    except (KeyError, TypeError, ValueError):
        dt = math.nan
        durations = []
    if not math.isfinite(dt) or dt <= 0:
        errors.append('physical result timestep is invalid')
    if not durations or any(not math.isfinite(value) or value <= 0 for value in durations):
        errors.append('physical case segment durations are invalid')
    total = float(sum(durations)) if durations else math.nan
    if not math.isfinite(total):
        errors.append('physical case total duration is invalid')
    steps = int(round(total / dt)) if math.isfinite(dt) and dt > 0 else 0
    if steps <= 0 or not math.isclose(steps * dt, total, abs_tol=1e-9):
        errors.append('physical case duration is not an integral simulation timestep')
    stride = result.get('timeseries_sample_stride_steps')
    if isinstance(stride, bool) or not isinstance(stride, (int, float)):
        errors.append('timeseries_sample_stride_steps is missing or non-integer')
        stride_int = 0
    else:
        stride_int = int(stride)
        if stride_int != stride or stride_int <= 0:
            errors.append('timeseries_sample_stride_steps is invalid')
    expected_indices = []
    if steps > 0 and stride_int > 0:
        expected_indices = list(range(0, steps, stride_int))
        if expected_indices[-1] != steps - 1:
            expected_indices.append(steps - 1)
    declared_indices = result.get('timeseries_expected_step_indices')
    if declared_indices != expected_indices:
        errors.append('timeseries expected step index list does not match case')
    if result.get('timeseries_expected_row_count') != len(expected_indices):
        errors.append('timeseries expected row count does not match case')
    timeseries = result.get('timeseries')
    if not isinstance(timeseries, list) or len(timeseries) != len(expected_indices):
        errors.append('timeseries row count does not match case/save stride')
        timeseries = timeseries if isinstance(timeseries, list) else []
    edges = np.cumsum(durations) if durations else np.asarray([], dtype=float)
    previous_time = -math.inf
    native_values = None
    native_source_offset = 0
    if native is None:
        errors.append('physical result has no case-bound native trace')
    else:
        try:
            raw_native = np.asarray(native, dtype=float)
            if (raw_native.ndim != 2 or raw_native.shape[1] != 43
                    or raw_native.shape[0] <= 0
                    or not np.isfinite(raw_native).all()
                    or not np.isin(raw_native[:, 1:3], (0.0, 1.0)).all()
                    or not np.isin(raw_native[:, 23:43], (0.0, 1.0)).all()):
                raise ValueError('case-bound native trace is malformed')
            native_values, native_source_offset = _operational_native_trace(
                raw_native, case)
            if native_source_offset != 1:
                raise ValueError(
                    'native trace must retain the explicit original initial row')
        except (TypeError, ValueError) as exc:
            errors.append(str(exc))
    replay_rows = None
    if replay is None:
        errors.append('deterministic native physical replay is missing')
    elif not isinstance(replay.get('timeseries'), list):
        errors.append('deterministic replay has no timeseries')
    else:
        replay_rows = replay.get('timeseries')
        if len(replay_rows) != len(expected_indices):
            errors.append('deterministic replay row count does not match save schedule')
    _validate_replay_summary(result, replay, case, native, errors)
    zero_segment_names = set()
    for segment in case.get('segments', []):
        try:
            if max(abs(float(segment.get(name, 0.))) for name in ('vx', 'vy', 'wz')) <= 1e-9:
                zero_segment_names.add(str(segment.get('name')))
        except (TypeError, ValueError):
            errors.append('physical case contains a malformed command')
    for index, (row, expected_step) in enumerate(zip(timeseries, expected_indices)):
        if not isinstance(row, dict):
            errors.append(f'timeseries[{index}] is not an object')
            continue
        step_index = row.get('step_index')
        if (isinstance(step_index, bool) or not isinstance(step_index, (int, float))
                or int(step_index) != step_index or int(step_index) != expected_step):
            errors.append(f'timeseries[{index}].step_index does not match save schedule')
        time_value = row.get('time')
        try:
            time_value = float(time_value)
        except (TypeError, ValueError):
            time_value = math.nan
        expected_time = (expected_step + 1) * dt
        if not math.isfinite(time_value) or not math.isclose(
                time_value, expected_time, rel_tol=0.0, abs_tol=1e-8):
            errors.append(f'timeseries[{index}].time does not match save schedule')
        if not math.isfinite(time_value) or time_value <= previous_time:
            errors.append(f'timeseries[{index}].time is not strictly increasing')
        previous_time = time_value
        segment_name = row.get('segment')
        segment_index = int(np.searchsorted(edges, expected_step * dt + 1e-10,
                                             side='right')) if len(edges) else -1
        segment_index = min(segment_index, len(durations) - 1) if durations else -1
        expected_name = (case['segments'][segment_index].get('name',
                        f'segment_{segment_index}')
                         if segment_index >= 0 else None)
        if segment_name != expected_name:
            errors.append(f'timeseries[{index}].segment does not match case')
        if not isinstance(row.get('holding'), bool):
            errors.append(f'timeseries[{index}].holding must be boolean')
        command = row.get('command')
        if (not isinstance(command, list) or len(command) != 4
                or any(isinstance(value, bool) or not isinstance(value, (int, float))
                       or not math.isfinite(float(value)) for value in command)):
            errors.append(f'timeseries[{index}].command is malformed')
        for field, width in (('qpos', 7 + len(SELF.S.ALL_JOINTS)),
                             ('torque_nm', len(SELF.S.ALL_JOINTS)),
                             ('velocity_rad_s', len(SELF.S.ALL_JOINTS))):
            values = row.get(field)
            try:
                values_array = np.asarray(values, dtype=float)
            except (TypeError, ValueError):
                values_array = np.asarray([], dtype=float)
            if (values_array.shape != (width,) or not np.isfinite(values_array).all()):
                errors.append(f'timeseries[{index}].{field} is not finite shape ({width},)')
        if native_values is not None:
            try:
                native_index = _native_control_index(
                    expected_step, dt, len(native_values))
                native_row = native_values[native_index]
                if (isinstance(row.get('native_trace_index'), bool)
                        or not isinstance(row.get('native_trace_index'), (int, float))
                        or int(row.get('native_trace_index')) != native_index):
                    errors.append(f'timeseries[{index}].native_trace_index is not bound')
                expected_source_index = native_index + native_source_offset
                source_index = row.get('native_trace_source_index')
                if (isinstance(source_index, bool)
                        or not isinstance(source_index, (int, float))
                        or int(source_index) != source_index
                        or int(source_index) != expected_source_index):
                    errors.append(
                        f'timeseries[{index}].native_trace_source_index is not bound')
                expected_native_fields = {
                    'native_phase': float(native_row[0]),
                    'native_trace_time_s': float(
                        expected_source_index / SELF.S.SERVO_HZ),
                }
                for field, expected_value in expected_native_fields.items():
                    try:
                        actual_value = float(row.get(field))
                    except (TypeError, ValueError):
                        actual_value = math.nan
                    if (not math.isfinite(actual_value)
                            or not math.isclose(actual_value, expected_value,
                                                rel_tol=0.0, abs_tol=1e-9)):
                        errors.append(f'timeseries[{index}].{field} differs from native trace')
                for field, expected_value in (
                        ('native_moving', bool(native_row[1] == 1.0)),
                        ('native_ready', bool(native_row[2] == 1.0))):
                    if type(row.get(field)) is not bool or row.get(field) != expected_value:
                        errors.append(f'timeseries[{index}].{field} differs from native trace')
                angles = row.get('native_angles_deg')
                expected_angles = native_row[3:23].tolist()
                if (not isinstance(angles, list) or len(angles) != 20
                        or not np.allclose(np.asarray(angles, dtype=float), expected_angles,
                                           rtol=0.0, atol=1e-7)):
                    errors.append(f'timeseries[{index}].native_angles_deg differs from native trace')
                enabled = row.get('native_enabled')
                expected_enabled = [bool(value == 1.0) for value in native_row[23:43]]
                if enabled != expected_enabled:
                    errors.append(f'timeseries[{index}].native_enabled differs from native trace')
                expected_command = _native_segment_command(case, native_index)
                for field, actual, expected_value in (
                        ('command', command, expected_command),
                        ('native_command', row.get('native_command'), expected_command)):
                    try:
                        actual_array = np.asarray(actual, dtype=float)
                    except (TypeError, ValueError):
                        actual_array = np.asarray([], dtype=float)
                    if (actual_array.shape != (4,)
                            or not np.isfinite(actual_array).all()
                            or not np.allclose(actual_array, expected_value,
                                                rtol=0.0, atol=1e-9)):
                        errors.append(f'timeseries[{index}].{field} differs from case/native schedule')
                if type(row.get('holding')) is bool and row.get('holding') != (not bool(native_row[1] == 1.0)):
                    errors.append(f'timeseries[{index}].holding differs from native trace')
                if row.get('phase') is None or not math.isclose(
                        float(row.get('phase')), float(native_row[0]),
                        rel_tol=0.0, abs_tol=1e-9):
                    errors.append(f'timeseries[{index}].phase differs from native trace')
            except (TypeError, ValueError, IndexError) as exc:
                errors.append(f'timeseries[{index}] native binding failed: {exc}')
        if replay_rows is not None and index < len(replay_rows):
            replay_row = replay_rows[index]
            if not isinstance(replay_row, Mapping):
                errors.append(f'deterministic replay row {index} is malformed')
                continue
            for field in ('step_index', 'segment'):
                if row.get(field) != replay_row.get(field):
                    errors.append(f'timeseries[{index}].{field} differs from deterministic replay')
            try:
                if not math.isclose(float(row.get('time')), float(replay_row.get('time')),
                                    rel_tol=0.0, abs_tol=1e-10):
                    errors.append(f'timeseries[{index}].time differs from deterministic replay')
            except (TypeError, ValueError):
                errors.append(f'timeseries[{index}].time cannot be compared to replay')
            for field, tolerance in (('qpos', 1e-9), ('torque_nm', 1e-8),
                                     ('velocity_rad_s', 1e-8)):
                try:
                    actual = np.asarray(row.get(field), dtype=float)
                    expected = np.asarray(replay_row.get(field), dtype=float)
                except (TypeError, ValueError):
                    actual = expected = np.asarray([], dtype=float)
                if (actual.shape != expected.shape or not np.isfinite(expected).all()
                        or not np.allclose(actual, expected, rtol=0.0,
                                            atol=tolerance)):
                    errors.append(
                        f'timeseries[{index}].{field} differs from deterministic replay')
            for field in ('phase', 'holding', 'command', 'native_trace_index',
                          'native_trace_source_index', 'native_trace_time_s',
                          'native_phase', 'native_moving', 'native_ready',
                          'native_angles_deg', 'native_enabled', 'native_command'):
                if field in replay_row and row.get(field) != replay_row.get(field):
                    # Numeric native fields are compared with the same strict
                    # tolerances as the source schedule rather than JSON text.
                    try:
                        if not np.allclose(np.asarray(row.get(field), dtype=float),
                                           np.asarray(replay_row.get(field), dtype=float),
                                           rtol=0.0, atol=1e-9):
                            errors.append(f'timeseries[{index}].{field} differs from replay')
                    except (TypeError, ValueError):
                        errors.append(f'timeseries[{index}].{field} differs from replay')
    if timeseries and zero_segment_names:
        for segment_name in sorted(zero_segment_names):
            rows_for_segment = [row for row in timeseries
                                if isinstance(row, Mapping)
                                and row.get('segment') == segment_name]
            if not rows_for_segment:
                errors.append(f'zero-command segment {segment_name} has no saved row')
            elif rows_for_segment[-1].get('holding') is not True:
                errors.append(
                    f'zero-command segment {segment_name} does not end in holding')
            segment_meta = next(
                (item for item in result.get('segments', [])
                 if isinstance(item, Mapping) and item.get('name') == segment_name),
                None)
            if not isinstance(segment_meta, Mapping) \
                    or segment_meta.get('holding_at_end') is not True:
                errors.append(
                    f'zero-command segment {segment_name} lacks holding_at_end=true')
    for key, expected in (
            ('requested_time_s', total),
            ('total_sim_time_s', total),
            ('valid_integrated_time_s', total)):
        try:
            value = float(result.get(key))
        except (TypeError, ValueError):
            value = math.nan
        if not math.isfinite(value) or not math.isclose(value, expected,
                                                        rel_tol=0.0, abs_tol=1e-8):
            errors.append(f'physical result {key} does not match case duration')
    return {
        'status': 'PASS' if not errors else 'FAIL',
        'controller_kind': result.get('controller_kind'),
        'timestep_s': dt,
        'requested_time_s': total,
        'simulation_step_count': steps,
        'sample_stride_steps': stride_int,
        'expected_step_indices': expected_indices,
        'stored_row_count': len(timeseries),
        'native_trace_bound': native_values is not None,
        'deterministic_replay_bound': replay_rows is not None,
        'errors': errors,
    }


def _physical_result_content_sha(result: Mapping[str, object]) -> str:
    """Hash the result content while excluding its self-referential ledger.

    A file cannot contain the SHA of its own final bytes without an impossible
    recursive update.  The canonical content digest excludes only the ledger
    fields and this digest field itself; the resulting value is stable and is
    bound back into both input ledgers by the producer and checker.
    """
    if not isinstance(result, Mapping):
        raise ValueError('physical result must be a mapping for canonical hashing')
    value = json.loads(json.dumps(result, sort_keys=True,
                                  ensure_ascii=False, allow_nan=False,
                                  separators=(',', ':')))
    for key in ('input_sha256', 'input_sha256_current',
                'physical_result_content_sha256',
                'physical_result_file_sha256'):
        value.pop(key, None)
    canonical = json.dumps(value, sort_keys=True, ensure_ascii=False,
                           allow_nan=False, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(canonical).hexdigest()


def _physical_result_content_key(result_path: Path, bundle_root: Path) -> str:
    name = _public_path(result_path, bundle_root)
    return '$PHYSICAL_RESULT_CONTENT/' + name


def _physical_result_file_key(result_path: Path, bundle_root: Path) -> str:
    name = _public_path(result_path, bundle_root)
    return '$PHYSICAL_RESULT_FILE/' + name


def _runtime_input_fingerprints(result_case: Mapping[str, object], result: Mapping[str, object],
                                result_path: Path, model_path: Path, *,
                                source_parts=None) -> dict:
    """sim_print_first wrapperと同じ実行入力台帳を現在のファイルから再構成する。"""
    import sim_print_first as PRINT_FIRST
    import sim_stress as STRESS
    bundle_root = Path(result_path).resolve().parent.parent
    old_urdf = STRESS.S.URDF_PATH
    old_output_root = STRESS.FINGERPRINT_OUTPUT_ROOT
    STRESS.S.URDF_PATH = Path(model_path).resolve()
    STRESS.FINGERPRINT_OUTPUT_ROOT = bundle_root
    try:
        hashes = STRESS.input_fingerprints(dict(result_case))
    finally:
        STRESS.S.URDF_PATH = old_urdf
        STRESS.FINGERPRINT_OUTPUT_ROOT = old_output_root

    def add(path: Path):
        path = Path(path).resolve()
        if not path.is_file():
            raise ValueError(f"runtime fingerprint input is missing: {path}")
        hashes[PRINT_FIRST.public_path(path, bundle_root)] = _sha256(path)

    # sim_print_first adds all generated profile headers, the wrapper source,
    # the selected model/manifest and the freeze manifest on top of the
    # sim_stress baseline ledger.
    native_binary = result_case.get("native_binary")
    if isinstance(native_binary, (str, Path)):
        native_path = Path(native_binary).resolve()
        if native_path.is_file():
            for header in sorted(native_path.parent.glob("*.h")):
                add(header)
            for native_artifact in (native_path.parent / 'trace.cpp',
                                    native_path.parent / 'build.json'):
                if native_artifact.is_file():
                    add(native_artifact)
    native_build = result.get('native_build')
    build_path = None
    if isinstance(native_build, Mapping):
        raw_build_path = native_build.get('path')
        if isinstance(raw_build_path, str) and raw_build_path.startswith('$OUTPUT/'):
            build_path = (bundle_root / raw_build_path[len('$OUTPUT/'):]).resolve()
        elif isinstance(raw_build_path, (str, Path)):
            build_path = Path(raw_build_path).resolve()
        if build_path is None or not build_path.is_file():
            raise ValueError('physical result native build provenance is missing')
        if native_build.get('sha256') != _sha256(build_path):
            raise ValueError('physical result native build SHA is stale')
        add(build_path)
    add(ROOT / "tools/sim_print_first.py")
    add(Path(model_path))
    manifest_value = result_case.get("model_manifest")
    if not isinstance(manifest_value, (str, Path)):
        manifest = result.get("model_manifest")
        if isinstance(manifest, dict):
            manifest_value = manifest.get("manifest_path")
    if isinstance(manifest_value, (str, Path)):
        manifest_path = Path(manifest_value).resolve()
        add(manifest_path)
    # Reuse the wrapper's complete lookup, including
    # model.geometry_freeze_manifest and the root case.freeze_manifest.  A
    # hand-copied subset here would make post-hoc provenance depend on which
    # supported spelling the case used.
    freeze_path = PRINT_FIRST._freeze_manifest_path(dict(result_case))
    if freeze_path is not None and freeze_path.is_file():
        add(freeze_path)
    cache_ledger = result.get("collision_cache_ledger")
    if cache_ledger is not None:
        from sim_collision import collision_cache_input_fingerprints
        cache_hashes, current_ledger = collision_cache_input_fingerprints(
            cache_ledger, output_root=bundle_root,
            source_parts=source_parts,
            reject_extra_cache=(result_case.get('model', {}).get('model_kind')
                                in getattr(STRESS, 'FINAL_MODEL_KINDS', ())))
        hashes.update(cache_hashes)
        declared_current = result.get("collision_cache_ledger_current")
        if declared_current is not None and declared_current != current_ledger:
            raise ValueError("physical result collision cache ledger is stale")
    content_sha = _physical_result_content_sha(result)
    declared_content_sha = result.get('physical_result_content_sha256')
    if declared_content_sha != content_sha:
        raise ValueError('physical result canonical content SHA is stale')
    declared_file_sha = result.get('physical_result_file_sha256')
    if declared_file_sha != content_sha:
        raise ValueError('physical result canonical file SHA is stale')
    hashes[_physical_result_content_key(Path(result_path), bundle_root)] = content_sha
    hashes[_physical_result_file_key(Path(result_path), bundle_root)] = content_sha
    return dict(sorted(hashes.items()))


def _load_physical_result(path: Path, case: Mapping[str, object],
                          case_sha: str, parts, output: Path, *, model=None,
                          compiled_proxy=None,
                          expected_cache_ledger: Mapping[str, object] | None = None,
                          native: np.ndarray | None = None,
                          replay: Mapping[str, object] | None = None) -> dict:
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"physical result JSON is invalid: {path}") from exc
    if not isinstance(result, dict):
        raise ValueError(f"physical result is not an object: {path}")
    result_case = result.get("case")
    if not isinstance(result_case, dict):
        raise ValueError(f"physical result has no embedded case: {path}")
    result_options = result_case.get("model")
    if not isinstance(result_options, dict):
        raise ValueError(f"physical result case has no model: {path}")
    # Compare the complete normalized input case. Results may have been
    # publicized with ``$OUTPUT`` paths, so resolve that token relative to the
    # result bundle before comparing. Name/freeze equality alone would permit
    # a result from a different command sequence to be attached here.
    import sim_print_first as PRINT_FIRST
    import sim_stress as STRESS
    result_case_resolved = PRINT_FIRST.resolve_case_output_refs(
        result_case, path.parent.parent)
    try:
        expected_normalized = STRESS._validate_case_inputs(dict(case))
        result_normalized = STRESS._validate_case_inputs(
            dict(result_case_resolved))
    except Exception as exc:  # noqa: BLE001 - malformed provenance is a hard error
        raise ValueError(f"physical result embedded case cannot be validated: {path}") from exc
    canonical_expected = json.dumps(expected_normalized, sort_keys=True,
                                    ensure_ascii=False, separators=(",", ":"),
                                    default=str)
    canonical_result = json.dumps(result_normalized, sort_keys=True,
                                  ensure_ascii=False, separators=(",", ":"),
                                  default=str)
    if canonical_result != canonical_expected:
        raise ValueError(f"physical result embedded case does not exactly match input: {path}")
    if result.get("status") != "PENDING_EXTERNAL_PROXY_PROOF":
        raise ValueError(
            "physical result status must be PENDING_EXTERNAL_PROXY_PROOF "
            f"until this checker completes: {path}")
    if native is None:
        raise ValueError(
            f"physical result validation requires the case-bound native trace: {path}")
    try:
        operational_native, _ = _operational_native_trace(native, result_case_resolved)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"physical result native trace binding failed: {path}: {exc}") from exc
    if replay is None:
        replay = _deterministic_native_physical_replay(
            result_case_resolved, native, path)
    timeseries_contract = _validate_physical_timeseries(
        result, case, native=native, replay=replay)
    if timeseries_contract.get('status') != 'PASS':
        raise ValueError(
            f"physical result timeseries contract failed: "
            f"{timeseries_contract.get('errors')}: {path}")
    if result.get("external_proxy_proof_required") is not True:
        raise ValueError(
            f"physical result must declare external_proxy_proof_required=true: {path}")
    required_result_checks = (
        "completed", "numeric_stability", "inputs_unchanged",
        "fixed_proxy_exclusion_contract_applied", "no_fall",
    )
    result_checks = result.get("checks")
    if not isinstance(result_checks, dict) or any(
            result_checks.get(key) is not True for key in required_result_checks):
        raise ValueError(
            f"physical result required checks are not all true: {path}")
    compiled = result.get("collision_proxy_compiled")
    if not isinstance(compiled, dict) or compiled.get("status") != "PASS" \
            or compiled.get("compiled_count") != 5:
        raise ValueError(
            f"physical result lacks the compiled five-pair exclusion evidence: {path}")
    if compiled_proxy is not None and (
            compiled.get("compiled_count") != compiled_proxy.get("compiled_count")
            or sorted(compiled.get("expected_signatures", []))
            != sorted(compiled_proxy.get("expected_signatures", []))
            or sorted(row.get("signature") for row in compiled.get(
                "compiled_excludes", []))
            != sorted(row.get("signature") for row in compiled_proxy.get(
                "compiled_excludes", []))):
        raise ValueError(
            f"physical result compiled exclusion evidence differs from current model: {path}")
    input_hashes = result.get("input_sha256")
    current_hashes = result.get("input_sha256_current")
    if not isinstance(input_hashes, dict) or not isinstance(current_hashes, dict):
        raise ValueError(f"physical result must store input_sha256 and current ledger: {path}")
    if input_hashes != current_hashes:
        raise ValueError(f"physical result input ledger changed during run: {path}")
    expected_contract = COLLISION.print_first_proxy_exclusion_contract()
    if input_hashes.get("$PROXY_EXCLUSION_CONTRACT") != expected_contract["sha256"]:
        raise ValueError(f"physical result proxy contract SHA is stale: {path}")
    cache_ledger = result.get("collision_cache_ledger")
    if not isinstance(cache_ledger, dict):
        raise ValueError(
            f"physical result must store the consumed collision cache ledger: {path}")
    cache_ledger_current = result.get("collision_cache_ledger_current")
    if not isinstance(cache_ledger_current, dict):
        raise ValueError(
            f"physical result must store the current collision cache ledger: {path}")
    if expected_cache_ledger is not None:
        if cache_ledger != expected_cache_ledger or cache_ledger_current != expected_cache_ledger:
            raise ValueError(
                f"physical result collision cache ledger differs from current compiled model: {path}")
    result_manifest = result.get("model_manifest")
    model_value = (result_manifest.get("model_path")
                   if isinstance(result_manifest, dict) else None)
    if not isinstance(model_value, (str, Path)):
        model_value = (result_options.get("model_path")
                       or result_options.get("urdf_path")
                       or result_options.get("model_urdf"))
    if not isinstance(model_value, (str, Path)):
        raise ValueError(f"physical result has no runtime model path for input rehash: {path}")
    # result_case_resolved already expanded its $OUTPUT references relative
    # to the result bundle.  Avoid resolving a public token against ROOT.
    model_path = Path(model_value).resolve()
    if not model_path.is_file():
        raise ValueError(f"physical result runtime model is missing: {path}")
    if isinstance(result_manifest, dict):
        declared_model_sha = result_manifest.get("model_sha256")
        if declared_model_sha != _sha256(model_path):
            raise ValueError(f"physical result runtime model SHA is stale: {path}")
    current_runtime_hashes = _runtime_input_fingerprints(
        result_case_resolved, result, path, model_path,
        source_parts=parts)
    if current_runtime_hashes != input_hashes:
        raise ValueError(
            f"physical result input ledger is stale against current files: {path}")
    expected_freeze = (case.get("model", {}).get("freeze_manifest_sha256")
                       or case.get("model", {}).get("geometry_freeze_hash"))
    result_freeze = (result_options.get("freeze_manifest_sha256")
                     or result_options.get("geometry_freeze_hash"))
    if result_case.get("name") != case.get("name"):
        raise ValueError(
            f"physical result case name {result_case.get('name')!r} does not match "
            f"{case.get('name')!r}: {path}")
    if result_freeze != expected_freeze:
        raise ValueError(f"physical result freeze SHA does not match case: {path}")
    order = result.get("joint_order")
    if tuple(order or ()) != tuple(SELF.S.ALL_JOINTS):
        raise ValueError(f"physical result joint_order is not all 20 axes: {path}")
    timeseries = result.get("timeseries")
    if not isinstance(timeseries, list) or not timeseries:
        raise ValueError(f"physical result has no qpos timeseries: {path}")
    rows = []
    sampled_qdeg_rows = []
    per_pair: dict[tuple[str, str], list[dict]] = {
        pair: [] for pair in COLLISION.PRINT_FIRST_PROXY_EXCLUSION_PAIRS}
    links = sorted({name for pair in per_pair for name in pair})
    for index, item in enumerate(timeseries):
        if not isinstance(item, dict):
            raise ValueError(f"physical result timeseries[{index}] is not an object")
        qpos = np.asarray(item.get("qpos"), dtype=float)
        if qpos.shape != (7 + len(SELF.S.ALL_JOINTS),) or not np.isfinite(qpos).all():
            raise ValueError(
                f"physical result timeseries[{index}].qpos must be finite shape (27,)")
        if model is not None:
            qpos_range = _qpos_range_report(model, qpos)
            if qpos_range["status"] != "PASS":
                raise ValueError(
                    f"physical result sampled_realized_qpos[{index}] is out of "
                    f"compiled joint range: {qpos_range['errors']}: {path}")
        qdeg = _qdeg_from_qpos(model, qpos)
        sampled_qdeg_rows.append(qdeg)
        world = _world_parts(parts, links, qdeg)
        for pair in per_pair:
            per_pair[pair].append(_scan_pair(
                world, pair, index, f"realized_{index:04d}"))
    merged = [_merge_pair_stats(rows, pair, samples=len(timeseries))
              for pair, rows in per_pair.items()]
    frame_report = ({
        'status': 'NOT_APPLICABLE',
        'sample_count': len(sampled_qdeg_rows),
        'link_names': links,
        'interpretation': 'compiled model is unavailable in this isolated fixture',
    } if model is None else SELF._compiled_frame_report(
        model, sampled_qdeg_rows, links))
    if model is not None and frame_report.get('status') != 'PASS':
        raise ValueError(
            f"physical result sampled qpos frame contract failed: "
            f"{frame_report.get('errors')}: {path}")
    return {
        "path": _public_path(path, _resolve(output).parent),
        "sha256": _sha256(path),
        "case_name": result_case.get("name"),
        "case_sha256": case_sha,
        "result_status": result.get("status"),
        "qpos_row_count": int(len(timeseries)),
        "sampled_realized_qpos": True,
        "timeseries_contract": timeseries_contract,
        "frame_transform_contract": frame_report,
        "pairs": merged,
        "status": "PASS_REALIZED_QPOS" if all(row["clean"] for row in merged)
        else "FAIL",
        "interpretation": (
            "物理ケースが保存した有限のsampled_realized_qposを元STLへ戻した証拠。"
            "記録された時系列の外側の連続軌跡は証明しない。"),
    }


def _validate_self_collision_audit(path: Path, trace_sha: str,
                                   contract: Mapping[str, object],
                                   output: Path, *, expected_input_hashes=None,
                                   compiled_proxy=None,
                                   expected_pose_count: int | None = None,
                                   expected_native: np.ndarray | None = None,
                                   expected_link_names: Sequence[str] | None = None,
                                   expected_cache_ledger: Mapping[str, object] | None = None,
                                   compiled_model=None,
                                   source_parts: Mapping[str, Sequence[tuple]] | None = None) -> dict:
    """全リンク有限監査をこのtrace/固定契約へ束縛する。"""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"path": _public_path(path, output.parent), "status": "FAIL",
                "sha256": _sha256(path) if path.is_file() else None,
                "errors": [f"audit JSON is invalid: {exc}"]}
    errors = []
    if not isinstance(payload, dict):
        return {"path": _public_path(path, output.parent), "status": "FAIL",
                "sha256": _sha256(path), "errors": ["audit is not an object"]}
    if payload.get("status") not in ("PASS", "PASS_FINITE_TRACE"):
        errors.append("audit status is not a clean finite-trace PASS")
    if payload.get("coverage") != "finite_native_trace":
        errors.append("audit coverage is not finite_native_trace")
    if payload.get("finite_pose_sweep_requested") is not True:
        errors.append("audit did not request the full finite native trace")
    if payload.get("finite_pose_sweep_complete") is not True:
        errors.append("audit finite_pose_sweep_complete is false")
    if payload.get("finite_pose_sweep_clean") is not True:
        errors.append("audit finite_pose_sweep_clean is false")
    if payload.get("inputs_unchanged") is not True:
        errors.append("audit inputs_unchanged is false")
    if payload.get("source_trace_sha256") != trace_sha:
        errors.append("audit source trace SHA does not match proof trace")
    if payload.get("source_trace_sha256_current") != trace_sha:
        errors.append("audit current source trace SHA does not match proof trace")
    if payload.get("processed_pose_count") != payload.get("native_pose_count"):
        errors.append("audit processed/native pose counts differ")
    if payload.get("expected_pose_count_matches") is not True:
        errors.append("audit expected pose count does not match")
    if payload.get("pose_count_matches") is not True:
        errors.append("audit pose count does not match native rows")
    if expected_pose_count is not None and payload.get("native_pose_count") != expected_pose_count:
        errors.append("audit native pose count does not match case-bound trace")
    if (payload.get("continuous_reachable_set_proven") is True
            or payload.get("coverage") in (
                "continuous_reachable_set", "all_reachable_continuous")
            or payload.get("continuous_status") == "PASS"):
        errors.append(
            "audit cannot claim continuous reachable-set proof without an independent interval certificate verifier")
    if payload.get("proxy_exclusion_contract") != contract:
        errors.append("audit proxy exclusion contract differs")
    compiled = payload.get("proxy_exclusion_compiled")
    if not isinstance(compiled, dict) or compiled.get("status") != "PASS":
        errors.append("audit compiled proxy exclusion evidence is not PASS")
    if compiled_proxy is not None and (
            not isinstance(compiled, dict)
            or compiled.get("compiled_count") != compiled_proxy.get("compiled_count")
            or sorted(compiled.get("expected_signatures", []))
               != sorted(compiled_proxy.get("expected_signatures", []))
            or sorted(row.get("signature") for row in compiled.get(
                "compiled_excludes", []))
               != sorted(row.get("signature") for row in compiled_proxy.get(
                   "compiled_excludes", []))):
        errors.append("audit compiled exclusion evidence differs from current model")
    audit_input = payload.get("input_sha256")
    audit_current = payload.get("input_sha256_current")
    if not isinstance(audit_input, dict) or not isinstance(audit_current, dict):
        errors.append("audit input SHA ledgers are missing or malformed")
    elif audit_input != audit_current:
        errors.append("audit input SHA changed during the sweep")
    if expected_input_hashes is not None and audit_input != expected_input_hashes:
        errors.append("audit input SHA ledger is stale against current source inputs")
    inventory = payload.get("print_first_inventory")
    inventory_current = payload.get("print_first_inventory_current")
    if not isinstance(inventory, dict) or inventory.get("status") != "PASS":
        errors.append("audit print-first inventory is not PASS")
    if not isinstance(inventory_current, dict) or inventory_current.get("status") != "PASS":
        errors.append("audit current print-first inventory is not PASS")
    cache_ledger = payload.get("collision_cache_ledger")
    cache_ledger_current = payload.get("collision_cache_ledger_current")
    if not isinstance(cache_ledger, dict) or not isinstance(cache_ledger_current, dict):
        errors.append("audit collision cache ledger is missing")
    elif cache_ledger != cache_ledger_current:
        errors.append("audit collision cache ledger changed during the sweep")
    if expected_cache_ledger is not None:
        if cache_ledger_current != expected_cache_ledger:
            errors.append("audit collision cache ledger is stale against current build")

    independent_source_report = {
        'status': 'NOT_APPLICABLE',
        'stored_rows_compared': False,
        'errors': [],
        'interpretation': 'source_partsが渡されていないため独立再計算なし',
    }

    # Metadata alone is insufficient for a full finite all-link artifact.  An
    # artifact with copied counts and status but empty/tampered poses must be
    # rejected before it can be combined with the proxy proof.
    if expected_native is not None:
        native_values = np.asarray(expected_native, dtype=float)
        poses = payload.get('poses')
        if (native_values.ndim != 2 or native_values.shape[1] != 43
                or not np.isfinite(native_values).all()):
            errors.append('expected native trace for audit pose comparison is invalid')
        if not isinstance(poses, list) or len(poses) != len(native_values):
            errors.append('audit poses do not cover every expected native row')
            poses = poses if isinstance(poses, list) else []
        link_names = payload.get('link_names')
        if not isinstance(link_names, list) or any(
                not isinstance(name, str) or not name for name in link_names):
            errors.append('audit link_names are missing or malformed')
            link_names = []
        if len(set(link_names)) != len(link_names):
            errors.append('audit link_names contain duplicates')
        if expected_link_names is not None:
            expected_links = sorted(str(name) for name in expected_link_names)
            if sorted(link_names) != expected_links:
                errors.append('audit link_names differ from current all-link inventory')
        expected_pairs = {
            tuple(pair) for pair in itertools.combinations(sorted(link_names), 2)
        }
        if payload.get('link_pair_coverage') != 'all_link_pairs':
            errors.append('audit link pair coverage is not all_link_pairs')
        if payload.get('expected_link_pair_count') != len(expected_pairs):
            errors.append('audit expected link-pair count is inconsistent')
        for index, (pose, expected_row) in enumerate(zip(poses, native_values)):
            if not isinstance(pose, dict):
                errors.append(f'audit pose {index} is not an object')
                continue
            pose_index = pose.get('pose_index')
            if (isinstance(pose_index, bool)
                    or not isinstance(pose_index, (int, float))
                    or int(pose_index) != pose_index or int(pose_index) != index):
                errors.append(f'audit pose {index} index is discontinuous')
            try:
                phase = float(pose.get('native_phase'))
            except (TypeError, ValueError):
                phase = math.nan
            if (not math.isfinite(phase)
                    or not math.isclose(phase, float(expected_row[0]),
                                        rel_tol=0.0, abs_tol=1e-9)):
                errors.append(f'audit pose {index} phase differs from native trace')
            angles = pose.get('angles_deg')
            if not isinstance(angles, dict) or set(angles) != set(SELF.S.ALL_JOINTS):
                errors.append(f'audit pose {index} angles do not contain exactly 20 axes')
            else:
                for axis, name in enumerate(SELF.S.ALL_JOINTS):
                    try:
                        angle = float(angles[name])
                    except (TypeError, ValueError):
                        angle = math.nan
                    expected_angle = float(expected_row[3 + axis])
                    if (not math.isfinite(angle)
                            or not math.isclose(angle, expected_angle,
                                                rel_tol=0.0, abs_tol=1e-7)):
                        errors.append(
                            f'audit pose {index} angle differs from native trace: {name}')
            pairs = pose.get('pairs')
            if not isinstance(pairs, list) or len(pairs) != len(expected_pairs):
                errors.append(f'audit pose {index} does not contain every link pair')
                pairs = pairs if isinstance(pairs, list) else []
            seen_pairs = set()
            for pair_index, pair in enumerate(pairs):
                if not isinstance(pair, dict):
                    errors.append(f'audit pose {index} pair {pair_index} is malformed')
                    continue
                links = pair.get('links')
                if (not isinstance(links, list) or len(links) != 2
                        or any(not isinstance(name, str) for name in links)
                        or links[0] >= links[1]):
                    errors.append(f'audit pose {index} pair {pair_index} links are malformed')
                    continue
                key = (links[0], links[1])
                if key in seen_pairs:
                    errors.append(f'audit pose {index} has duplicate link pair {key!r}')
                seen_pairs.add(key)
                if key not in expected_pairs:
                    errors.append(f'audit pose {index} has unknown link pair {key!r}')
                candidate_count = pair.get('bbox_candidate_pairs')
                if (isinstance(candidate_count, bool)
                        or not isinstance(candidate_count, (int, float))
                        or int(candidate_count) != candidate_count
                        or int(candidate_count) < 0):
                    errors.append(f'audit pose {index} pair {key!r} candidate count is invalid')
                depth = pair.get('hull_penetration_mm')
                if depth is not None:
                    try:
                        depth = float(depth)
                    except (TypeError, ValueError):
                        depth = math.nan
                    if not math.isfinite(depth) or depth < 0.0:
                        errors.append(f'audit pose {index} pair {key!r} penetration is invalid')
                intersections = pair.get('actual_intersections')
                boolean_errors = pair.get('errors')
                if not isinstance(intersections, list) or not isinstance(boolean_errors, list):
                    errors.append(f'audit pose {index} pair {key!r} result lists are missing')
                    intersections = intersections if isinstance(intersections, list) else []
                    boolean_errors = boolean_errors if isinstance(boolean_errors, list) else []
                for collision_index, collision in enumerate(intersections):
                    if not isinstance(collision, dict):
                        errors.append(
                            f'audit pose {index} pair {key!r} intersection {collision_index} malformed')
                        continue
                    try:
                        volume = float(collision.get('intersection_mm3'))
                    except (TypeError, ValueError):
                        volume = math.nan
                    if not math.isfinite(volume) or volume <= INTERSECTION_THRESHOLD_MM3:
                        errors.append(
                            f'audit pose {index} pair {key!r} intersection volume is invalid')
                    if not all(isinstance(collision.get(name), str)
                               for name in ('part1', 'part2')):
                        errors.append(
                            f'audit pose {index} pair {key!r} intersection parts are missing')
                for error_index, detail in enumerate(boolean_errors):
                    if not isinstance(detail, dict) or not isinstance(detail.get('error'), str):
                        errors.append(
                            f'audit pose {index} pair {key!r} Boolean error {error_index} malformed')
            if seen_pairs != expected_pairs:
                errors.append(f'audit pose {index} link-pair set is incomplete')
        if compiled_model is not None and native_values.ndim == 2:
            expected_frame = SELF._compiled_frame_report(
                compiled_model,
                [{name: float(value)
                  for name, value in zip(SELF.S.ALL_JOINTS, row[3:23])}
                 for row in native_values],
                sorted(link_names))
            stored_frame = payload.get('frame_transform_contract')
            if (not isinstance(stored_frame, dict)
                    or stored_frame.get('status') != 'PASS'):
                errors.append('audit frame transform contract is not PASS')
            elif (stored_frame.get('sample_count') != expected_frame.get('sample_count')
                  or sorted(stored_frame.get('link_names', []))
                     != sorted(expected_frame.get('link_names', []))
                  or expected_frame.get('status') != 'PASS'
                  or stored_frame.get('evidence_sha256')
                     != expected_frame.get('evidence_sha256')
                  or float(stored_frame.get('max_translation_error_mm', math.inf))
                     > SELF.FRAME_TRANSLATION_TOL_MM
                  or float(stored_frame.get('max_rotation_error_rad', math.inf))
                     > SELF.FRAME_ROTATION_TOL_RAD):
                errors.append('audit frame transform contract differs from compiled frames')
            if expected_frame.get('status') != 'PASS':
                errors.append('compiled expected frame transform contract is not PASS')

        if source_parts is not None and native_values.ndim == 2:
            try:
                independent_source_report = _independent_all_pair_source_check(
                    source_parts, native_values, payload=payload,
                    expected_links=expected_link_names)
            except Exception as exc:  # noqa: BLE001 - independent proof fails closed
                independent_source_report = {
                    'status': 'FAIL',
                    'stored_rows_compared': True,
                    'errors': [f'independent source Boolean recomputation failed: {exc}'],
                }
            if independent_source_report.get('status') != 'PASS':
                errors.append('independent current-source all-pair proof is not PASS')
    return {
        "path": _public_path(path, output.parent),
        "status": "PASS_FINITE_TRACE" if not errors else "FAIL",
        "sha256": _sha256(path),
        "processed_pose_count": payload.get("processed_pose_count"),
        "native_pose_count": payload.get("native_pose_count"),
        "source_trace_sha256": payload.get("source_trace_sha256"),
        "input_sha256": audit_input,
        "continuous_reachable_set_proven": False,
        "independent_source_all_pair_proof": independent_source_report,
        "errors": errors,
    }


def _contract_digest(contract: Mapping[str, object]) -> str:
    payload = {key: value for key, value in contract.items() if key != "sha256"}
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                           separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def run(case_path: Path, trace_path: Path, output: Path,
        physical_results: Sequence[Path] = (),
        dense_samples: int = DEFAULT_DENSE_SAMPLES,
        *, allow_external_input: bool = False) -> dict:
    if (isinstance(dense_samples, bool) or not isinstance(dense_samples, int)
            or dense_samples < 2):
        raise ValueError("dense_samples must be an integer >= 2")
    case_path = _resolve(case_path, label="case", allow_external=allow_external_input)
    trace_path = _resolve(trace_path, label="native trace", allow_external=allow_external_input)
    output = _resolve(output, label="output", allow_external=allow_external_input)
    physical_results = [
        _resolve(path, label="physical result", allow_external=allow_external_input)
        for path in physical_results
    ]
    case, case_sha = _load_case(case_path)
    _validate_case_path_literals(case, allow_external=allow_external_input)
    options = _validate_case(case)
    options = dict(options)
    options["_case_name"] = case.get("name")
    options["_case_segments"] = case.get("segments")
    options["_profile_body_h"] = case.get("profile", {}).get(
        "body_h", SELF.S.BODY_H_DEFAULT)
    options["_case_sha256"] = case_sha
    options["_case_normalized_sha256"] = _normalized_case_sha(case)
    options["_expected_trace_mode"] = "frozen_print_first"
    import sim_stress as STRESS
    options["_native_trace_mode"] = STRESS.native_trace_mode(case)
    trace_sha = _sha256(trace_path)
    # The freeze verifier is intentionally run before geometry is materialized;
    # stale source/config changes must stop proof generation.
    import sim_print_first as PRINT_FIRST
    freeze_report = PRINT_FIRST.validate_freeze_manifest(
        case, require_generation_roles=True)
    if freeze_report.get("status") != "PASS":
        raise ValueError(
            "case freeze manifest is not current: "
            + "; ".join(str(row) for row in freeze_report.get("mismatches", [])))
    native, trace_metadata, trace_sha = _load_trace(trace_path, options)
    expected_trace_sha = trace_metadata["trace_file_sha256"]
    if expected_trace_sha != trace_sha:
        raise ValueError("native trace SHA changed while loading")
    contract = COLLISION.print_first_proxy_exclusion_contract()
    if contract.get("sha256") != _contract_digest(contract):
        raise ValueError("proxy exclusion contract SHA is inconsistent")
    model_value = (options.get("model_path") or options.get("urdf_path")
                   or options.get("model_urdf"))
    if not isinstance(model_value, (str, Path)):
        raise ValueError("case.model must identify the current generated URDF")
    model_path = _resolve(model_value, label="generated URDF",
                          allow_external=allow_external_input)
    if not model_path.is_file():
        raise ValueError(f"generated URDF is missing: {model_path}")
    with __import__("print_first_assembly").context(generated=True):
        parts, inventory = _source_bundle()
        inventory_inputs = SELF._inventory_input_hashes(inventory)
        old_urdf = SELF.S.URDF_PATH
        SELF.S.URDF_PATH = model_path
        try:
            compiled_model, compiled_report, normalized_case, collision_cache_ledger, compiled_index = (
                _compile_current_proxy_model(case, model_path, contract))
            cache_input_hash, collision_cache_ledger_current = (
                COLLISION.collision_cache_input_fingerprints(
                    collision_cache_ledger, output_root=output.parent,
                    source_parts=parts, reject_extra_cache=True))
            link_contract = _independent_link_contract(
                model_path, compiled_model, parts, compiled_index)
            if link_contract.get('status') != 'PASS':
                raise ValueError(
                    'independent all-link contract failed: '
                    + '; '.join(link_contract.get('errors', [])))
            joint_limits = _joint_limit_report(compiled_model)
            native_limits = _native_joint_range_report(native, joint_limits)
            proof_link_names = list(link_contract['expected_links'])
            native_frame = SELF._compiled_frame_report(
                compiled_model,
                [_qdeg_from_native(row) for row in native],
                proof_link_names)
            dense_qdeg_rows = []
            dense_base = _qdeg_from_native(native[0])
            for dense_pair in COLLISION.PRINT_FIRST_PROXY_EXCLUSION_PAIRS:
                dense_values, dense_joint = _dense_samples_for_pair(
                    dense_pair, native, dense_samples)
                for dense_value in dense_values:
                    dense_qdeg = dict(dense_base)
                    dense_qdeg[dense_joint] = float(dense_value)
                    dense_qdeg_rows.append(dense_qdeg)
            dense_frame = SELF._compiled_frame_report(
                compiled_model, dense_qdeg_rows, proof_link_names)
            input_hashes, _trace_key, _ = SELF._input_hashes(
                trace_path, native,
                extra_hashes={
                    **inventory_inputs,
                    **cache_input_hash,
                    "$PROXY_EXCLUSION_CONTRACT": contract["sha256"],
                })
            audit_value = options.get("self_collision_audit_path")
            if not isinstance(audit_value, (str, Path)):
                audit_report = {
                    "status": "FAIL", "path": None, "sha256": None,
                    "errors": ["case.model.self_collision_audit_path is required"],
                }
            else:
                audit_path = _resolve(audit_value, label="self-collision audit",
                                      allow_external=allow_external_input)
                audit_report = _validate_self_collision_audit(
                    audit_path, trace_sha, contract, output,
                    expected_input_hashes=input_hashes,
                    compiled_proxy=compiled_report,
                    expected_pose_count=len(native),
                    expected_native=native,
                    expected_link_names=proof_link_names,
                    expected_cache_ledger=COLLISION.public_collision_cache_ledger(
                        collision_cache_ledger_current),
                    compiled_model=compiled_model,
                    source_parts=parts,
                )
            native_report = _scan_native(parts, native)
            dense_report = _scan_dense(parts, native, dense_samples)
            realized = []
            for result_path in physical_results:
                realized.append(_load_physical_result(
                        Path(result_path).resolve(), case, case_sha,
                        parts, output, model=compiled_model,
                    compiled_proxy=compiled_report,
                    expected_cache_ledger=COLLISION.public_collision_cache_ledger(
                        collision_cache_ledger_current),
                    native=native))
            end_parts, end_inventory = _source_bundle()
            end_inputs = SELF._inventory_input_hashes(end_inventory)
            current_hashes, _key, _digest = SELF._input_hashes(
                trace_path, native,
                extra_hashes={
                    **end_inputs,
                    **COLLISION.collision_cache_input_fingerprints(
                        collision_cache_ledger, output_root=output.parent,
                        source_parts=end_parts, reject_extra_cache=True)[0],
                    "$PROXY_EXCLUSION_CONTRACT": contract["sha256"],
                })
        finally:
            SELF.S.URDF_PATH = old_urdf
    realized_status = (
        "PASS_REALIZED_QPOS" if realized and all(
            row.get("status") == "PASS_REALIZED_QPOS" for row in realized)
        else "INCOMPLETE" if not realized else "FAIL")
    input_clean = bool(input_hashes == current_hashes
                       and end_inventory.get("status") == "PASS"
                       and audit_report.get("status") == "PASS_FINITE_TRACE")
    source_clean = (native_report.get("status") == "PASS_FINITE_TRACE"
                    and dense_report.get("status") == "PASS_DENSE_JOINT_LIMITS"
                    and native_frame.get("status") == "PASS"
                    and dense_frame.get("status") == "PASS"
                    and compiled_report.get("status") == "PASS"
                    and joint_limits.get("status") == "PASS"
                    and native_limits.get("status") == "PASS"
                    and audit_report.get("status") == "PASS_FINITE_TRACE"
                    and audit_report.get("independent_source_all_pair_proof", {}).get(
                        "status") == "PASS")
    finite_status = "PASS_FINITE_SOURCE_PROOF" if source_clean and input_clean else "FAIL"
    if not input_clean or not source_clean or realized_status == "FAIL":
        status = "FAIL"
    elif realized_status != "PASS_REALIZED_QPOS":
        status = "INCOMPLETE"
    else:
        status = "PASS_FINITE_SOURCE_AND_REALIZED_QPOS"
    payload = {
        "status": status,
        "case": {
        "path": _public_path(case_path, output.parent),
            "sha256": case_sha,
            "name": case.get("name"),
            "freeze_manifest_sha256": options.get("freeze_manifest_sha256")
            or options.get("geometry_freeze_hash"),
        },
        "trace": {
            "path": _public_path(trace_path, output.parent),
            **trace_metadata,
        },
        "proxy_exclusion_contract": contract,
        "proxy_exclusion_contract_sha256": contract["sha256"],
        "print_first_inventory": inventory,
        "print_first_inventory_current": end_inventory,
        "inventory_part_count": len(inventory.get("parts", [])),
        "native_trace_proof": native_report,
        "dense_joint_limit_proof": dense_report,
        "compiled_proxy_exclusion_proof": compiled_report,
        "independent_link_contract": link_contract,
        "compiled_joint_limit_proof": joint_limits,
        "native_frame_transform_proof": native_frame,
        "dense_frame_transform_proof": dense_frame,
        "native_joint_limit_proof": native_limits,
        "collision_cache_ledger": COLLISION.public_collision_cache_ledger(
            collision_cache_ledger),
        "collision_cache_ledger_current": collision_cache_ledger_current,
        "all_link_self_collision_audit": audit_report,
        "independent_source_all_pair_proof": audit_report.get(
            "independent_source_all_pair_proof"),
        "realized_qpos_proof": {
            "status": realized_status,
            "results": realized,
            "required": True,
            "reason": ("このケースの有限qpos時系列を指定する必要がある"
                       if not realized else None),
        },
        "finite_source_proof_status": finite_status,
        "input_sha256": input_hashes,
        "input_sha256_current": current_hashes,
        "inputs_unchanged": input_clean,
        "continuous_reachable_set_proven": False,
        "continuous_status": "UNVERIFIED",
        "interpretation": (
            "固定5組だけを除外するための有限証拠。指定した有限native traceの全"
            f"{len(native)}行、各関節限界の"
            "密掃引、指定物理ケースで今回サンプリングしたqposを元STL Booleanで検査した。"
            "連続到達集合の証明ではなく、隣接区間の変位上界・最小分離証明が無い"
            "ため continuous_reachable_set はUNVERIFIEDのまま。"),
    }
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2,
                                 allow_nan=False) + "\n", encoding="utf-8")
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--trace-json", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--physical-result", type=Path, action="append", default=[])
    parser.add_argument("--dense-samples", type=int, default=DEFAULT_DENSE_SAMPLES)
    parser.add_argument(
        "--allow-external-input", action="store_true",
        help="allow case/trace/result paths outside the repository; their SHA is recorded")
    args = parser.parse_args(argv)
    try:
        payload = run(args.case, args.trace_json, args.out, args.physical_result,
                      args.dense_samples,
                      allow_external_input=args.allow_external_input)
    except KeyboardInterrupt:
        payload = {
            "status": "INCOMPLETE",
            "continuous_reachable_set_proven": False,
            "errors": ["proxy proof interrupted"],
        }
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
        return 130
    except Exception as exc:  # noqa: BLE001 - CLI must fail closed
        payload = {
            "status": "FAIL",
            "continuous_reachable_set_proven": False,
            "errors": [str(exc)],
        }
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
        print(f"proxy exclusion proof FAIL: {exc}", file=sys.stderr)
        return 2
    print(f"proxy exclusion proof {payload['status']} -> {args.out}")
    return 0 if payload["status"] == "PASS_FINITE_SOURCE_AND_REALIZED_QPOS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
