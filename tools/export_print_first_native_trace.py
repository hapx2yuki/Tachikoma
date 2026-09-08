#!/usr/bin/env python3
"""print-first flag/headerで実行した有限C++指令列を保存する。

形状掃引や物理シムが通常版の ``config.h`` へ戻らないよう、
``sim_print_first.prepare_native`` の同じコンパイル入口を使う。出力は
立位、前後左右、正逆旋回、停止、再始動を含み、各行に時刻・区間・20軸角度・
有効状態を持つ。実機のサーボ出力や歩行成立を意味しない。
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import sim_print_first as P
import sim_physics as S


def _segments(profile):
    """有限の制御指令を、幾何掃引で再利用できる区間名付きで返す。"""
    cycle = float(profile['cycle_t'])
    return [
        ('stand', (0., 0., 0.), .48),
        ('forward', (0., 1., 0.), cycle),
        ('backward', (0., -1., 0.), cycle),
        ('right', (1., 0., 0.), cycle),
        ('left', (-1., 0., 0.), cycle),
        ('turn_positive', (0., 0., 1.), cycle),
        ('turn_negative', (0., 0., -1.), cycle),
        # This duration covers the next all-stance boundary from any incoming
        # phase, including a stop request immediately before phase 1.0.
        ('stop', (0., 0., 0.), 1.32),
        ('restart_forward', (0., 1., 0.), cycle),
        ('restart_stop', (0., 0., 0.), 1.32),
        ('settle', (0., 0., 0.), .48),
        ('hold', (0., 0., 0.), 2.40),
    ]


def _sha256(path: Path) -> str:
    return P.sha(Path(path))


def _normalized_case_sha(case: dict) -> str:
    canonical = json.dumps(case, sort_keys=True, ensure_ascii=False,
                           separators=(',', ':'), default=str).encode('utf-8')
    return hashlib.sha256(canonical).hexdigest()


def export(case, case_path, output, *, profile_mode=None, freeze_manifest=None,
           trace_mode='production'):
    if trace_mode not in ('production', 'test'):
        raise ValueError('trace_mode must be production or test')
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    case_path = Path(case_path).resolve()
    # The checker resolves only the public $OUTPUT references before it hashes
    # the normalized case.  Hash the same representation here so a trace
    # produced from a public case bundle cannot be attached to a different
    # bundle merely because its path spelling differs.
    normalized_case = P.resolve_case_output_refs(
        case, P.case_output_root(case_path))
    case_sha256 = _sha256(case_path)
    normalized_case_sha256 = _normalized_case_sha(normalized_case)
    profile = dict(P.DEFAULT, **case['profile'])
    profile_name = case.get('profile_name', case.get('name', 'print_first_trace'))
    firmware_dir = output / 'firmware' / profile_name
    mode = profile_mode or (
        'frozen_print_first'
        if case.get('model', {}).get('model_kind') in P.FINAL_MODEL_KINDS
        else 'candidate_print_first')
    binary = P.prepare_native(
        profile, firmware_dir, profile_mode=mode,
        freeze_manifest=freeze_manifest)

    rows = []
    # A trace is evidence for the case that produced it. Earlier versions
    # always expanded the profile's demonstration sequence here, so a trace
    # named after a short stand case could actually contain the unrelated
    # forward/turn/restart sequence. Prefer the explicit case segments and
    # retain the profile sequence only for legacy callers without segments.
    case_segments = case.get('segments')
    # The production evidence gate always exercises the complete named
    # sequence.  A case's short settle/walk sample is useful for exploratory
    # simulation, but must be explicitly marked as test mode before it can
    # become a reduced native trace.
    if trace_mode == 'production':
        case_segments = None
    if case_segments is not None:
        if not isinstance(case_segments, list) or not case_segments:
            raise ValueError('case.segments must be a non-empty list')
        segment_specs = []
        for index, segment in enumerate(case_segments):
            if not isinstance(segment, dict):
                raise ValueError(f'case.segments[{index}] must be an object')
            name = segment.get('name', f'segment_{index}')
            if not isinstance(name, str) or not name:
                raise ValueError(f'case.segments[{index}].name is invalid')
            duration = float(segment.get('duration'))
            if not np.isfinite(duration) or duration <= 0:
                raise ValueError(f'case.segments[{index}].duration must be positive finite')
            steps = round(duration * S.SERVO_HZ)
            if not np.isclose(duration * S.SERVO_HZ, steps, atol=1e-8):
                raise ValueError(f'case.segments[{index}].duration is not a servo-period multiple')
            command = tuple(float(segment.get(axis, 0.0))
                            for axis in ('vx', 'vy', 'wz'))
            if any(not np.isfinite(value) for value in command):
                raise ValueError(f'case.segments[{index}] command is non-finite')
            body_h = float(segment.get('body_h', profile['body_h']))
            if not np.isfinite(body_h) or body_h <= 0:
                raise ValueError(f'case.segments[{index}].body_h must be positive finite')
            segment_specs.append((name, command, duration, body_h))
    else:
        segment_specs = [
            (name, command, duration, float(profile['body_h']))
            for name, command, duration in _segments(profile)
        ]
    initial_body_h = float(segment_specs[0][3])
    commands = [[0., 0., 0., 0., initial_body_h]]
    command_labels = [('initial', 0.)]
    elapsed = 0.
    for name, (vx, vy, wz), duration, body_h in segment_specs:
        steps = round(duration * S.SERVO_HZ)
        for _ in range(steps):
            dt = 1. / S.SERVO_HZ
            commands.append([dt, vx, vy, wz, body_h])
            elapsed += dt
            command_labels.append((name, elapsed))
    proc = subprocess.run(
        [str(binary), 'ready'],
        input='\n'.join(' '.join(map(str, row)) for row in commands) + '\n',
        capture_output=True, text=True, check=True,
    )
    native_stdout_sha256 = hashlib.sha256(proc.stdout.encode('utf-8')).hexdigest()
    values = P.T.validate_native_trace(
        np.loadtxt(proc.stdout.splitlines(), ndmin=2),
        label='exported print-first native trace')
    if values.shape != (len(commands), 43):
        raise ValueError(
            f'native trace row count mismatch: {values.shape[0]} != {len(commands)}'
        )
    for index, (value, (segment, time_s), command) in enumerate(
            zip(values, command_labels, commands)):
        rows.append({
            'index': index,
            'time_s': float(time_s),
            'segment': segment,
            'dt_s': float(command[0]),
            'command': {'vx': float(command[1]), 'vy': float(command[2]),
                        'wz': float(command[3]), 'body_h_mm': float(command[4])},
            'phase': float(value[0]),
            'moving': bool(value[1]),
            'ready': bool(value[2]),
            'angles_deg': {name: float(angle)
                           for name, angle in zip(S.ALL_JOINTS, value[3:23])},
            'enabled': {name: bool(enabled)
                        for name, enabled in zip(S.ALL_JOINTS, value[23:43])},
        })

    stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
    trace_dir = output / 'native-trace'
    trace_dir.mkdir(parents=True, exist_ok=True)
    csv_path = trace_dir / f'{case.get("name", "print_first")}-native-trace.csv'
    fieldnames = ['index', 'time_s', 'segment', 'dt_s', 'vx', 'vy', 'wz',
                  'body_h_mm', 'phase', 'moving', 'ready'] + S.ALL_JOINTS + [
                      f'{name}_enabled' for name in S.ALL_JOINTS]
    with csv_path.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            command = row['command']
            flat = {key: row[key] for key in ('index', 'time_s', 'segment', 'dt_s',
                                               'phase', 'moving', 'ready')}
            flat.update({'vx': command['vx'], 'vy': command['vy'],
                         'wz': command['wz'], 'body_h_mm': command['body_h_mm']})
            flat.update(row['angles_deg'])
            flat.update({f'{name}_enabled': value
                         for name, value in row['enabled'].items()})
            writer.writerow(flat)

    json_path = trace_dir / f'{case.get("name", "print_first")}-native-trace.json'
    header_path = firmware_dir / 'print_first_gait.h'
    build_path = firmware_dir / 'build.json'
    header = {
        'path': P.public_path(header_path, output),
        'sha256': P.sha(header_path),
        'source_config_sha256': P.sha(ROOT / 'hardware/src/config.py'),
        'case_sha256': case_sha256,
        'case_normalized_sha256': normalized_case_sha256,
        'profile_mode': mode,
        'build_mode': mode,
        'build_json_sha256': P.sha(firmware_dir / 'build.json'),
    }
    if freeze_manifest is not None:
        freeze_path = Path(freeze_manifest).resolve()
        if not freeze_path.is_file():
            raise FileNotFoundError(f'freeze manifest does not exist: {freeze_path}')
        header['freeze_manifest_path'] = P.public_path(freeze_path, output)
        header['freeze_manifest_sha256'] = _sha256(freeze_path)
    payload = {
        'schema_version': 1,
        'status': 'GENERATED_NATIVE_TRACE',
        'trace_mode': trace_mode,
        'created_utc': stamp,
        'case_name': case.get('name'),
        'case_input': P.public_path(Path(case_path), output),
        'case_sha256': case_sha256,
        'case_normalized_sha256': normalized_case_sha256,
        'profile': profile,
        'profile_mode': mode,
        'build_mode': mode,
        'compile_flag': '-DTACHIKOMA_PRINT_FIRST_PROFILE=1',
        'joint_order': list(S.ALL_JOINTS),
        'header': header,
        'build': {'path': P.public_path(build_path, output),
                  'sha256': P.sha(build_path),
                  'schema_version': json.loads(build_path.read_text(encoding='utf-8')).get('schema_version'),
                  'profile_mode': mode,
                  'build_mode': mode},
        'binary': {'path': P.public_path(binary, output),
                   'sha256': P.sha(binary),
                   'build_json_sha256': P.sha(build_path)},
        'native_argv': ['ready'],
        'native_stdout_sha256': native_stdout_sha256,
        'build_json_sha256': P.sha(build_path),
        'csv': {'path': P.public_path(csv_path, output),
                'sha256': P.sha(csv_path)},
        'row_count': len(rows),
        'segments': [{'name': name, 'duration_s': duration,
                      'command': {'vx': command[0], 'vy': command[1], 'wz': command[2]},
                      'body_h_mm': body_h}
                     for name, command, duration, body_h in segment_specs],
        'rows': rows,
        'interpretation': '同一のprint-first header/compile flagで形状掃引へ渡す有限実C++出力。実サーボの角度校正、連続定格、実機歩行の証明ではない。',
    }
    P.save(json_path, payload)
    return json_path, csv_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', type=Path,
                        default=ROOT / 'outputs/print-first-20260905/final-simulation/cases/initial-finite-cases.json')
    parser.add_argument('--case-name', default='final_stand_pf1')
    parser.add_argument('--out', type=Path,
                        default=ROOT / 'outputs/print-first-20260905/final-simulation-native-trace')
    parser.add_argument('--profile-mode', choices=('candidate_print_first', 'frozen_print_first'),
                        default=None,
                        help='ヘッダー経路を明示する。候補設定の保存時は candidate_print_first を使う。')
    parser.add_argument('--trace-mode', choices=('production', 'test'), default='production',
                        help='production は標準全区間、test は明示fixture用の出力印')
    parser.add_argument('--freeze-manifest', type=Path, default=None,
                        help='最終凍結台帳。指定時はtrace headerへ台帳のSHAを記録する。')
    args = parser.parse_args()
    data = json.loads(args.case.read_text(encoding='utf-8'))
    cases = data if isinstance(data, list) else [data]
    selected = [case for case in cases if case.get('name') == args.case_name]
    if not selected:
        raise SystemExit(f'case not found: {args.case_name}')
    json_path, csv_path = export(selected[0], args.case.resolve(), args.out,
                                 profile_mode=args.profile_mode,
                                 freeze_manifest=args.freeze_manifest,
                                 trace_mode=args.trace_mode)
    print(json.dumps({'status': 'GENERATED_NATIVE_TRACE',
                      'json': P.public_path(json_path, args.out.resolve()),
                      'csv': P.public_path(csv_path, args.out.resolve())}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
