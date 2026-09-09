#!/usr/bin/env python3
"""リンク凸包の接触を実部品のブーリアン交差と照合する。

親子衝突は全体では無効化しない。印刷優先の生成済みモデルだけが、
``sim_collision`` の固定5組プロキシ例外を使える。例外の実STL根拠は
別の有限監査で記録し、未確認の組をケース入力から追加する経路は持たない。
"""
import hashlib
import json
import math
import sys
import itertools
from collections.abc import Mapping
from pathlib import Path
import numpy as np
import trimesh,mujoco
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
import sim_physics as S
import export_urdf as E
import sim_collision as COLLISION
from sim_stress import self_contacts, native_output_trace

# This is only the signed numerical remainder allowed from a native Boolean
# result.  The physical intersection gate below remains config-owned; keeping the
# two values separate prevents a real 0.001 mm3 overlap from being discarded.
BOOLEAN_VOLUME_EPS_MM3 = float(E.C.BOOLEAN_NEGATIVE_TOLERANCE_MM3)
INTERSECTION_THRESHOLD_MM3 = float(E.C.BOOLEAN_INTERSECTION_THRESHOLD_MM3)
EXPECTED_TRACE_COMPILE_FLAG = '-DTACHIKOMA_PRINT_FIRST_PROFILE=1'
FRAME_TRANSLATION_TOL_MM = 0.1
FRAME_ROTATION_TOL_RAD = 1e-6


def _trace_sha(value, label):
    if (not isinstance(value, str) or len(value) != 64
            or value != value.lower()
            or any(char not in '0123456789abcdef' for char in value)):
        raise ValueError(f'{label} must be a lowercase hexadecimal SHA-256')
    return value


def _trace_header_contract(payload):
    """外部native traceを、現行print-first入力へ厳密に束縛する。"""
    if not isinstance(payload, dict):
        raise ValueError('native trace must be a metadata object')
    if payload.get('status') != 'GENERATED_NATIVE_TRACE':
        raise ValueError('native trace status must be GENERATED_NATIVE_TRACE')
    if payload.get('compile_flag') != EXPECTED_TRACE_COMPILE_FLAG:
        raise ValueError(
            f'native trace compile_flag must be {EXPECTED_TRACE_COMPILE_FLAG!r}')
    if payload.get('profile_mode') not in ('candidate_print_first', 'frozen_print_first'):
        raise ValueError('native trace profile_mode is invalid')
    if tuple(payload.get('joint_order') or ()) != tuple(S.ALL_JOINTS):
        raise ValueError('native trace joint_order does not match the 20-axis order')
    header = payload.get('header')
    if not isinstance(header, dict):
        raise ValueError('native trace header is required for provenance validation')
    source_config_sha = _trace_sha(
        header.get('source_config_sha256'), 'native trace source_config_sha256')
    current_config_sha = hashlib.sha256(
        (ROOT / 'hardware/src/config.py').read_bytes()).hexdigest()
    if source_config_sha != current_config_sha:
        raise ValueError(
            'stale native trace: header.source_config_sha256 does not match '
            'current hardware/src/config.py')
    _trace_sha(header.get('sha256'), 'native trace header.sha256')
    if header.get('freeze_manifest_sha256') is not None:
        _trace_sha(header['freeze_manifest_sha256'],
                   'native trace header.freeze_manifest_sha256')
    return {
        'source_config_sha256': source_config_sha,
        'header_sha256': header['sha256'],
    }


def _strict_number(value, label):
    """JSONの数値を厳密に検査し、bool/文字列の暗黙変換を許さない。"""
    if isinstance(value, bool) or not isinstance(value, (int, float, np.number)):
        raise ValueError(f'{label} must be a number')
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f'{label} must be finite')
    return number


def _strict_bool(value, label):
    if not isinstance(value, bool):
        raise ValueError(f'{label} must be a boolean')
    return value


def _trace_input_descriptor(path):
    """trace JSONの公開キーとSHAを返す。絶対パスは台帳へ残さない。"""
    raw_path = Path(path)
    symlink = _repo_symlink_component(raw_path)
    if symlink is not None:
        raise ValueError(f'native trace must not use a repository symlink: {symlink}')
    path = raw_path.resolve()
    if not path.is_file():
        raise ValueError(f'native trace file is missing: {path}')
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    try:
        key = path.relative_to(ROOT).as_posix()
    except ValueError:
        key = f'$EXTERNAL/{path.name}#{digest[:16]}'
    return key, digest


def _intersection_volume(mesh, label):
    """Boolean結果を閉体として検査し、符号付き微小負値だけを許容する。

    ``trimesh`` は空の交差を空 ``Trimesh`` として返す。一方、体積属性
    だけを持つダミー値や、開いた/向きの壊れたメッシュを空交差として
    扱うと、Booleanエラーを非交差へ変換してしまう。空の結果だけを0とし、
    非空の結果は有限なトポロジーと、必要なら native Manifold の
    ``NoError`` を確認する。
    """
    if mesh is None:
        # A missing object has neither a native status nor topology.  Treating
        # it as empty would make a failed Boolean operation look clean; only
        # an explicit empty Trimesh below is allowed to become zero.
        raise ValueError(
            f'{label}: Boolean result is missing; only an explicit empty '
            'Trimesh result may have zero volume')
    if not isinstance(mesh, trimesh.Trimesh):
        raise ValueError(
            f'{label}: Boolean result must be a trimesh.Trimesh')
    try:
        vertices = np.asarray(mesh.vertices, dtype=float)
        faces = np.asarray(mesh.faces)
    except Exception as exc:  # noqa: BLE001 - preserve validation cause
        raise ValueError(f'{label}: Boolean result topology is unreadable') from exc
    if (vertices.ndim != 2 or vertices.shape[1] != 3 or
            faces.ndim != 2 or faces.shape[1] != 3):
        raise ValueError(f'{label}: Boolean result topology is not Nx3')
    if not np.isfinite(vertices).all():
        raise ValueError(f'{label}: Boolean result vertices are non-finite')
    if not np.issubdtype(faces.dtype, np.integer):
        raise ValueError(f'{label}: Boolean result faces must be integer indices')
    if not np.isfinite(faces.astype(float)).all():
        raise ValueError(f'{label}: Boolean result faces are non-finite')
    if len(faces) and (faces.min() < 0 or faces.max() >= len(vertices)):
        raise ValueError(f'{label}: Boolean result face index is out of bounds')
    try:
        volume = float(mesh.volume)
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f'{label}: Boolean volume is not numeric') from exc
    if not math.isfinite(volume):
        raise ValueError(f'{label}: Boolean volume is non-finite')

    # An empty Trimesh is the sole topology-free result accepted as zero.  A
    # vertices-only or faces-only object is malformed topology, even when its
    # convenience ``volume`` property happens to be zero; accepting either as
    # an empty Boolean would turn a truncated/error result into a clean scan.
    if len(vertices) == 0 and len(faces) == 0:
        if volume != 0.0:
            raise ValueError(
                f'{label}: empty Boolean result must have zero volume '
                f'(got {volume} mm3)')
        return 0.0
    if len(vertices) == 0 or len(faces) == 0:
        raise ValueError(
            f'{label}: Boolean result has one-sided empty topology '
            f'(vertices={len(vertices)}, faces={len(faces)})')

    try:
        watertight = bool(mesh.is_watertight)
        winding = bool(mesh.is_winding_consistent)
    except Exception as exc:  # noqa: BLE001 - preserve validation cause
        raise ValueError(f'{label}: Boolean result topology could not be checked') from exc
    if not watertight or not winding:
        raise ValueError(
            f'{label}: Boolean result must be watertight and winding-consistent')

    if volume < -BOOLEAN_VOLUME_EPS_MM3:
        raise ValueError(
            f'{label}: Boolean volume is negative beyond tolerance '
            f'({volume} mm3 < -{BOOLEAN_VOLUME_EPS_MM3} mm3)'
        )
    if volume < 0:
        # A negative value is admissible only when the actual topology can be
        # reconstructed by Manifold and it reports NoError with the same sign.
        # This closes the old ``class TinyNegative: volume=...`` loophole.
        try:
            from manifold3d import Manifold, Mesh
            native = Manifold(Mesh(
                np.asarray(vertices, dtype=np.float32),
                np.asarray(faces, dtype=np.uint32)))
            status = native.status()
            native_volume = float(native.volume())
        except Exception as exc:  # noqa: BLE001 - fail closed
            raise ValueError(
                f'{label}: negative Boolean result lacks native topology/status') from exc
        if getattr(status, 'name', None) != 'NoError':
            raise ValueError(
                f'{label}: negative Boolean result status is not NoError: {status}')
        if not math.isfinite(native_volume) or native_volume >= 0:
            raise ValueError(
                f'{label}: negative Boolean result has invalid native volume '
                f'{native_volume!r}')
        if not math.isclose(native_volume, volume, rel_tol=1e-5,
                            abs_tol=BOOLEAN_VOLUME_EPS_MM3):
            raise ValueError(
                f'{label}: native/serialized Boolean volume mismatch '
                f'({native_volume} vs {volume} mm3)')
        return 0.0
    try:
        if not bool(mesh.is_volume):
            raise ValueError(f'{label}: non-empty Boolean result is not a volume')
    except AttributeError as exc:
        raise ValueError(f'{label}: Boolean result has no is_volume topology flag') from exc
    # Positive volume remains signed and is never normalized with abs().
    return volume


def _audit_status(payload):
    """監査結果のCLI状態を返す。衝突/エラーは不完全より優先してFAIL。"""
    inventory = payload.get('print_first_inventory')
    if isinstance(inventory, dict) and inventory.get('status') != 'PASS':
        return 'FAIL'
    compiled_proxy = payload.get('proxy_exclusion_compiled')
    if isinstance(compiled_proxy, dict) and compiled_proxy.get('status') == 'FAIL':
        return 'FAIL'
    fixed_intersections = payload.get('fixed_base_intersections')
    fixed_errors = payload.get('fixed_base_boolean_errors')
    if (isinstance(fixed_intersections, list) and fixed_intersections) or (
            isinstance(fixed_errors, list) and fixed_errors):
        return 'FAIL'
    frame_contract = payload.get('frame_transform_contract')
    if isinstance(frame_contract, dict) and frame_contract.get('status') == 'FAIL':
        return 'FAIL'
    for pose in payload.get('poses', []):
        if not isinstance(pose, dict):
            return 'INCOMPLETE'
        for pair in pose.get('pairs', []):
            if not isinstance(pair, dict):
                return 'INCOMPLETE'
            if pair.get('actual_intersections'):
                return 'FAIL'
            if pair.get('errors'):
                return 'FAIL'
    if payload.get('finite_pose_sweep_complete') is not True:
        return 'INCOMPLETE'
    # The sweep covers the stored finite native rows only.  Keep its state
    # name distinct from a continuous reachable-set proof so a downstream
    # aggregate cannot mistake a complete sample sweep for coverage of all
    # intermediate poses.
    return 'PASS_FINITE_TRACE'


def _write_incomplete(out, error, *, trace_path=None, full_reachable=False,
                      expected_pose_count=None):
    """監査開始前/途中の例外でも既存結果をPASSとして残さない。"""
    trace_key = None
    trace_digest = None
    if trace_path is not None:
        try:
            trace_key, trace_digest = _trace_input_descriptor(trace_path)
        except Exception:
            trace_key = str(Path(trace_path).name)
    payload = {
        'status': 'INCOMPLETE',
        'coverage': 'finite_native_trace' if full_reachable else 'unknown',
        'finite_pose_sweep_requested': bool(full_reachable),
        'finite_pose_sweep_complete': False,
        'finite_pose_sweep_clean': False,
        'processed_pose_count': 0,
        'native_pose_count': expected_pose_count,
        'expected_pose_count': expected_pose_count,
        'expected_pose_count_matches': False,
        'pose_count_matches': False,
        'inputs_unchanged': False,
        'source_trace_key': trace_key,
        'source_trace_path': trace_key,
        'source_trace_sha256': trace_digest,
        'source_trace_sha256_current': None,
        'fixed_base_intersections': [],
        'fixed_base_boolean_errors': [],
        'poses': [],
        'errors': [str(error)],
        'continuous_reachable_set_proven': False,
        'continuous_proof': {
            'status': 'UNVERIFIED',
            'required_metrics': [
                'adjacent_pose_max_mesh_displacement_upper_bound_mm',
                'minimum_separation_mm',
            ],
            'reason': 'finite pose Boolean sweep does not prove unsampled continuous intervals',
        },
    }
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    return payload


def _load_native_trace(path, *, payload=None):
    """export_print_first_native_trace のJSONを監査用配列へ変換する。"""
    if payload is None:
        payload=json.loads(Path(path).read_bytes().decode('utf-8'))
    if not isinstance(payload, dict):
        raise ValueError('native trace JSON must be an object')
    _trace_header_contract(payload)
    rows=payload['rows']
    if not isinstance(rows,list) or not rows:
        raise ValueError('native trace has no rows')
    if 'row_count' not in payload:
        raise ValueError('native trace row_count is required')
    declared = payload['row_count']
    if isinstance(declared, bool) or not isinstance(declared, (int, float)):
        raise ValueError('native trace row_count must be an integer')
    if int(declared) != declared or int(declared) != len(rows):
        raise ValueError(
            f'native trace row_count {declared!r} != rows {len(rows)}'
        )
    values=np.zeros((len(rows),43),dtype=float)
    for index,row in enumerate(rows):
        if not isinstance(row,dict):
            raise ValueError(f'native trace row {index} is not an object')
        values[index,0]=_strict_number(row.get('phase'), f'native trace row {index}.phase')
        values[index,1]=float(_strict_bool(row.get('moving'), f'native trace row {index}.moving'))
        values[index,2]=float(_strict_bool(row.get('ready'), f'native trace row {index}.ready'))
        angles=row.get('angles_deg')
        if not isinstance(angles,dict):
            raise ValueError(f'native trace row {index} has no angles_deg')
        missing = [name for name in S.ALL_JOINTS if name not in angles]
        unknown = sorted(set(angles) - set(S.ALL_JOINTS))
        if missing:
            raise ValueError(f'native trace row {index} missing angles: {missing}')
        if unknown:
            raise ValueError(f'native trace row {index} has unknown angles: {unknown}')
        values[index,3:23]=[
            _strict_number(angles[name], f'native trace row {index}.angles_deg.{name}')
            for name in S.ALL_JOINTS
        ]
        enabled=row.get('enabled')
        if not isinstance(enabled, dict):
            raise ValueError(f'native trace row {index} has no enabled flags')
        missing_enabled=[name for name in S.ALL_JOINTS if name not in enabled]
        unknown_enabled=sorted(set(enabled) - set(S.ALL_JOINTS))
        if missing_enabled:
            raise ValueError(f'native trace row {index} missing enabled flags: {missing_enabled}')
        if unknown_enabled:
            raise ValueError(f'native trace row {index} has unknown enabled flags: {unknown_enabled}')
        values[index,23:43]=[
            float(_strict_bool(enabled[name], f'native trace row {index}.enabled.{name}'))
            for name in S.ALL_JOINTS
        ]
    if not np.isfinite(values).all():
        raise ValueError('native trace contains non-finite values')
    return values


def _generated_trace_digest(native):
    values = np.ascontiguousarray(native, dtype=np.float64)
    return hashlib.sha256(values.tobytes()).hexdigest()


def _compiled_frame_report(model, qdeg_rows, link_names):
    """compiled MuJoCo body frameをE.LINK_PARENT_FRAMEへ突合する。

    STLをワールドへ置く監査とURDFの関節木が別の座標規約へずれると、
    native/dense/物理qposのBoolean結果は同じ名前でも別の場所を検査する。
    各保存qposについてコンパイル済みbodyのxpos/xmatを再計算し、並進は
    0.1 mm、回転は1e-6 rad以内だけを受け入れる。これは指定サンプルの
    座標一致であり、連続区間の証明ではない。
    """
    errors = []
    rows = list(qdeg_rows) if isinstance(qdeg_rows, (list, tuple)) else []
    links = list(link_names) if isinstance(link_names, (list, tuple)) else []
    if not rows:
        errors.append('frame report has no sampled qpos rows')
    if len(set(links)) != len(links) or any(not isinstance(name, str) for name in links):
        errors.append('frame report link_names are malformed or duplicated')
    data = mujoco.MjData(model)
    base_id = int(mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, 'base_link'))
    if base_id < 0:
        errors.append('compiled model has no base_link for frame report')
    else:
        base_joint = int(model.body_jntadr[base_id])
        if base_joint >= 0 and int(model.jnt_type[base_joint]) == int(
                mujoco.mjtJoint.mjJNT_FREE):
            base_qadr = int(model.jnt_qposadr[base_joint])
            if base_qadr + 7 <= len(data.qpos):
                data.qpos[base_qadr:base_qadr + 7] = [0., 0., 0., 1., 0., 0., 0.]
    max_translation = 0.0
    max_rotation = 0.0
    evidence_rows = []
    for sample_index, qdeg in enumerate(rows):
        if not isinstance(qdeg, Mapping):
            errors.append(f'frame sample {sample_index} qdeg is malformed')
            continue
        data.qpos[:] = 0.0
        if base_id >= 0:
            base_joint = int(model.body_jntadr[base_id])
            if base_joint >= 0 and int(model.jnt_type[base_joint]) == int(
                    mujoco.mjtJoint.mjJNT_FREE):
                base_qadr = int(model.jnt_qposadr[base_joint])
                data.qpos[base_qadr:base_qadr + 7] = [0., 0., 0., 1., 0., 0., 0.]
        for name in S.ALL_JOINTS:
            if name not in qdeg:
                errors.append(f'frame sample {sample_index} is missing joint {name}')
                continue
            try:
                value = float(qdeg[name])
            except (TypeError, ValueError):
                value = math.nan
            if not math.isfinite(value):
                errors.append(f'frame sample {sample_index} joint {name} is non-finite')
                continue
            jid = int(mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name))
            if jid < 0:
                errors.append(f'frame report compiled joint is missing: {name}')
                continue
            qadr = int(model.jnt_qposadr[jid])
            if qadr < 0 or qadr >= len(data.qpos):
                errors.append(f'frame report qpos address is invalid: {name}')
                continue
            data.qpos[qadr] = math.radians(value)
        mujoco.mj_forward(model, data)
        for link in links:
            body_id = int(mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, link))
            expected_fn = E.LINK_PARENT_FRAME.get(link)
            if body_id < 0 or expected_fn is None:
                errors.append(f'frame report link is absent from compiled/source map: {link}')
                continue
            try:
                expected = np.asarray(expected_fn(dict(qdeg)), dtype=float)
                actual_position = np.asarray(data.xpos[body_id], dtype=float) * 1000.0
                actual_rotation = np.asarray(data.xmat[body_id], dtype=float).reshape(3, 3)
                expected_position = expected[:3, 3]
                expected_rotation = expected[:3, :3]
            except Exception as exc:  # noqa: BLE001 - source/model mismatch is a hard error
                errors.append(f'frame report cannot evaluate {link}: {exc}')
                continue
            if (expected.shape != (4, 4)
                    or not np.isfinite(expected).all()
                    or not np.isfinite(actual_position).all()
                    or not np.isfinite(actual_rotation).all()):
                errors.append(f'frame report has non-finite transform for {link}')
                continue
            translation_error = float(np.linalg.norm(actual_position - expected_position))
            relative = actual_rotation.T @ expected_rotation
            cosine = float(np.clip((np.trace(relative) - 1.0) / 2.0, -1.0, 1.0))
            rotation_error = float(math.acos(cosine))
            max_translation = max(max_translation, translation_error)
            max_rotation = max(max_rotation, rotation_error)
            evidence_rows.append({
                'sample_index': int(sample_index),
                'link': link,
                'actual_position_mm': actual_position.astype(float).tolist(),
                'expected_position_mm': expected_position.astype(float).tolist(),
                'actual_rotation': actual_rotation.astype(float).ravel().tolist(),
                'expected_rotation': expected_rotation.astype(float).ravel().tolist(),
                'translation_error_mm': translation_error,
                'rotation_error_rad': rotation_error,
            })
            if translation_error > FRAME_TRANSLATION_TOL_MM:
                errors.append(
                    f'frame translation mismatch sample {sample_index} {link}: '
                    f'{translation_error:.9g} mm')
            if rotation_error > FRAME_ROTATION_TOL_RAD:
                errors.append(
                    f'frame rotation mismatch sample {sample_index} {link}: '
                    f'{rotation_error:.9g} rad')
    evidence_bytes = json.dumps(
        evidence_rows, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False).encode('utf-8')
    return {
        'status': 'PASS' if not errors else 'FAIL',
        'sample_count': len(rows),
        'link_names': sorted(links),
        'translation_tolerance_mm': FRAME_TRANSLATION_TOL_MM,
        'rotation_tolerance_rad': FRAME_ROTATION_TOL_RAD,
        'max_translation_error_mm': float(max_translation),
        'max_rotation_error_rad': float(max_rotation),
        'evidence_sha256': hashlib.sha256(evidence_bytes).hexdigest(),
        'errors': errors[:100],
        'errors_truncated': max(0, len(errors) - 100),
        'interpretation': '指定した保存qposのcompiled frame一致。連続到達集合の証明ではない。',
    }


# Keep these aliases for callers that import the self-collision checker, while
# deriving the actual contract from sim_collision so t0 and finite-sweep
# inventories cannot drift apart.
PRINT_FIRST_REQUIRED_PARTS = COLLISION.PRINT_FIRST_REQUIRED_PARTS
PRINT_FIRST_LEGACY_PART_NAMES = COLLISION.PRINT_FIRST_LEGACY_PART_NAMES
PRINT_FIRST_MANIFEST_PATHS = (
    ROOT / 'outputs/print-first-20260905/body/assembly.json',
    ROOT / 'outputs/print-first-20260905/legs/assembly.json',
    ROOT / 'outputs/print-first-20260905/feet/assembly.json',
)
PRINT_FIRST_GENERATED_SOURCE_PATHS = {
    'component': (
        ROOT / 'hardware/src/config.py',
        ROOT / 'tools/sim_collision.py',
        ROOT / 'tools/print_first_assembly.py',
        ROOT / 'tools/print_first_components.py',
    ),
    'xiao': (
        ROOT / 'hardware/src/config.py',
        ROOT / 'tools/sim_collision.py',
        ROOT / 'tools/print_first_assembly.py',
        ROOT / 'tools/xiao_retention_plan.py',
    ),
    'ld220': (
        ROOT / 'hardware/src/config.py',
        ROOT / 'tools/export_urdf.py',
        ROOT / 'tools/sim_collision.py',
        ROOT / 'tools/print_first_assembly.py',
        ROOT / 'hardware/src/make_print_first_leg.py',
    ),
    'servo': (
        ROOT / 'hardware/src/config.py',
        ROOT / 'tools/export_urdf.py',
        ROOT / 'tools/sim_collision.py',
    ),
}


def _public_root_path(path, digest=None):
    """監査台帳用の再現可能な相対パスを返す。"""
    raw_path = Path(path)
    symlink = _repo_symlink_component(raw_path)
    if symlink is not None:
        raise ValueError(f'path must not use a repository symlink: {symlink}')
    path = raw_path.resolve()
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        if digest is None and path.is_file():
            digest = _file_sha256(path)
        suffix = f'#{digest[:16]}' if isinstance(digest, str) and len(digest) >= 16 else '#missing'
        return f'$EXTERNAL/{path.name}{suffix}'


def _file_sha256(path):
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f'print-first inventory file is missing: {path}')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _repo_symlink_component(path):
    """Return a lexical repository path component that is a symlink.

    ``Path.resolve()`` intentionally hides the distinction between a real
    repository file and an alias.  Collision/trace provenance must retain
    that distinction: an in-repository symlink can otherwise point at a
    different checkout or an external file while its resolved path still
    looks like a valid input.  Only paths whose *lexical* spelling starts in
    this repository are rejected; ordinary external output bundles remain
    usable.
    """
    raw = Path(path)
    lexical = raw if raw.is_absolute() else ROOT / raw
    try:
        lexical.relative_to(ROOT)
    except ValueError:
        return None
    parts = lexical.parts
    current = Path(lexical.anchor) if lexical.anchor else Path('.')
    start = 1 if lexical.anchor else 0
    for part in parts[start:]:
        current /= part
        if current.is_symlink():
            return current
    return None


def _resolve_repo_stl_path(value, label='STL path'):
    """Resolve a public STL path and prove it stays inside the repository."""
    if not isinstance(value, str) or not value or value.startswith('$EXTERNAL/'):
        raise ValueError(f'{label} must be a repository-relative path')
    raw = Path(value)
    if '..' in raw.parts:
        raise ValueError(f'{label} must not contain ..')
    symlink = _repo_symlink_component(raw)
    if symlink is not None:
        raise ValueError(f'{label} must not use a repository symlink: {symlink}')
    candidate = (raw if raw.is_absolute() else ROOT / raw).resolve()
    try:
        candidate.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise ValueError(f'{label} escapes the repository') from exc
    if not candidate.is_file():
        raise FileNotFoundError(f'{label} is missing: {candidate}')
    return candidate


def _load_verified_stl_mesh(path):
    """Reload the exact referenced STL for runtime geometry binding."""
    mesh = trimesh.load(Path(path), force='mesh')
    if not isinstance(mesh, trimesh.Trimesh):
        raise ValueError(f'STL did not load as a mesh: {path}')
    if (len(mesh.vertices) < 4 or len(mesh.faces) < 4
            or not np.isfinite(mesh.vertices).all()
            or not np.isfinite(mesh.faces).all()
            or not mesh.is_watertight or not mesh.is_winding_consistent
            or not np.isfinite(mesh.volume) or mesh.volume <= 0):
        raise ValueError(f'STL runtime geometry is not a finite closed solid: {path}')
    return mesh


def _mesh_invariant(mesh):
    """Return a transform/order independent geometry fingerprint for a mesh."""
    vertices = np.asarray(mesh.vertices, dtype=float)
    faces = np.asarray(mesh.faces, dtype=np.int64)
    if (vertices.ndim != 2 or vertices.shape[1] != 3
            or faces.ndim != 2 or faces.shape[1] != 3
            or not np.isfinite(vertices).all()):
        raise ValueError('mesh invariant input is malformed')
    edge_lengths = np.sort(np.round(np.asarray(mesh.edges_unique_length, dtype=float), 7))
    face_areas = np.sort(np.round(np.asarray(mesh.area_faces, dtype=float), 7))
    payload = {
        'vertex_count': int(len(vertices)),
        'face_count': int(len(faces)),
        'edge_lengths': edge_lengths.tolist(),
        'face_areas': face_areas.tolist(),
        'volume': round(abs(float(mesh.volume)), 7),
        'area': round(float(mesh.area), 7),
    }
    return hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _generated_geometry_provenance(name):
    """STLを持たない解析用占有の生成元を明示し、SHAを返す。"""
    if name.startswith('component_'):
        kind = 'component'
    elif name in ('xiao_all_boards_occupancy', 'camera_child_lens_occupancy'):
        kind = 'xiao'
    elif (name.startswith('leg_') and
          (name.endswith('_ld220_case')
           or name.endswith('_servo_horn_assistant')
           or name.endswith('_servo_horn_main'))):
        kind = 'ld220'
    elif ((name.startswith('arm_') or name.startswith('eye_'))
          and name.endswith('_servo_case')):
        kind = 'servo'
    else:
        return None
    sources = []
    for source_path in PRINT_FIRST_GENERATED_SOURCE_PATHS[kind]:
        source_path = Path(source_path).resolve()
        digest = _file_sha256(source_path)
        sources.append({
            'path': _public_root_path(source_path, digest),
            'sha256': digest,
        })
    return {'kind': kind, 'sources': sources}


def _index_legacy_stls(index, directories):
    """manifest外のSTLを曖昧性検査付きで索引へ追加する。"""
    candidates = {}
    for directory in directories:
        directory = Path(directory)
        if not directory.is_dir():
            continue
        for source_path in directory.glob('*.stl'):
            # Keep the lexical path until the symlink gate below.  Resolving
            # here would erase the very alias distinction this provenance
            # check is required to reject.
            candidates.setdefault(source_path.stem, []).append(source_path)
    for base, paths in sorted(candidates.items()):
        if base in index:
            continue
        for path in paths:
            symlink = _repo_symlink_component(path)
            if symlink is not None:
                raise ValueError(
                    f'legacy STL source must not use a repository symlink: {symlink}')
        digests = {path: _file_sha256(path) for path in sorted(paths)}
        unique_digests = set(digests.values())
        if len(unique_digests) > 1:
            rendered = ', '.join(
                f'{_public_root_path(path, digest)}={digest}'
                for path, digest in digests.items())
            raise ValueError(f'ambiguous STL source for {base}: {rendered}')
        source_path = sorted(paths)[0]
        digest = digests[source_path]
        index[base] = {
            'source_kind': 'stl',
            'stl_path': _public_root_path(source_path, digest),
            'stl_sha256': digest,
            'manifest_path': None,
            '_source_path': source_path,
            '_transforms': [np.eye(4).tolist()],
        }
    return index


def _print_first_stl_index():
    """採用候補manifestと標準STLから part→実体SHA の索引を作る。"""
    index = {}
    manifest_inputs = {}
    for manifest_path in PRINT_FIRST_MANIFEST_PATHS:
        manifest_path = Path(manifest_path).resolve()
        manifest_inputs[_public_root_path(manifest_path)] = _file_sha256(manifest_path)
        raw = json.loads(manifest_path.read_text(encoding='utf-8'))
        rows = []
        outputs = raw.get('outputs')
        if outputs is not None:
            if not isinstance(outputs, dict):
                raise ValueError(
                    f'print-first inventory manifest outputs is not a mapping: {manifest_path}')
            for name, output in outputs.items():
                if not isinstance(name, str) or not name:
                    raise ValueError(
                        f'print-first inventory output name is invalid: {manifest_path}')
                if not isinstance(output, dict):
                    raise ValueError(
                        f'print-first inventory output row is not an object: {manifest_path}')
                row = dict(output)
                row['name'] = name
                row['_schema'] = 'outputs'
                rows.append(row)
        parts_rows = raw.get('parts')
        if parts_rows is not None:
            if not isinstance(parts_rows, list):
                raise ValueError(
                    f'print-first inventory manifest parts is not a list: {manifest_path}')
            rows.extend(dict(row, _schema='parts') if isinstance(row, dict) else row
                        for row in parts_rows)
        if not rows:
            raise ValueError(f'print-first inventory manifest has no parts/outputs: {manifest_path}')
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError(f'print-first inventory manifest row is not an object: {manifest_path}')
            name = row.get('name')
            schema = row.get('_schema')
            if schema == 'outputs':
                raw_path = row.get('path')
            elif schema == 'parts':
                # Body manifests historically call this field ``stl`` while
                # the leg manifest uses ``path``.  Both are the same logical
                # schema; accepting either is explicit, and two conflicting
                # values are rejected below instead of guessed.
                stl_path = row.get('stl')
                output_path = row.get('path')
                if stl_path is not None and output_path is not None:
                    if stl_path != output_path:
                        raise ValueError(
                            f'print-first inventory manifest has conflicting STL paths '
                            f'for {name}: {manifest_path}')
                    raw_path = stl_path
                else:
                    raw_path = stl_path if stl_path is not None else output_path
            else:
                raise ValueError(
                    f'print-first inventory manifest row schema is invalid: {manifest_path}')
            if not isinstance(name, str) or not name:
                raise ValueError(
                    f'print-first inventory manifest row name is invalid: {manifest_path}')
            if not isinstance(raw_path, str) or not raw_path:
                raise ValueError(
                    f'print-first inventory manifest STL path is invalid for {name}: '
                    f'{manifest_path}')
            source_path = _resolve_repo_stl_path(
                raw_path, f'print-first inventory STL {name}')
            actual = _file_sha256(source_path)
            expected = row.get('sha256')
            if (not isinstance(expected, str) or len(expected) != 64
                    or expected != actual):
                raise ValueError(
                    f'print-first inventory manifest SHA is missing or mismatched '
                    f'for {name}: {source_path}')
            transform = row.get('transform', np.eye(4).tolist())
            transform_array = np.asarray(transform, dtype=float)
            if (transform_array.shape != (4, 4)
                    or not np.isfinite(transform_array).all()):
                raise ValueError(
                    f'print-first inventory transform is malformed for {name}: {manifest_path}')
            old = index.get(name)
            record = {
                'source_kind': 'stl',
                'stl_path': _public_root_path(source_path),
                'stl_sha256': actual,
                'manifest_path': _public_root_path(manifest_path),
                '_source_path': source_path,
                '_transforms': [transform_array.tolist()],
            }
            if old is not None:
                if (old['stl_path'] != record['stl_path']
                        or old['stl_sha256'] != record['stl_sha256']):
                    raise ValueError(f'print-first inventory has conflicting STL rows for {name}')
                old.setdefault('_transforms', []).extend(record['_transforms'])
                # Keep the first canonical manifest path while accepting an
                # outputs/parts duplicate that points at identical bytes.
            else:
                index[name] = record
            manifest_inputs[_public_root_path(source_path)] = actual

    # Parts retained from the kit are not present in the print-first manifests.
    # Enumerate both source roots before choosing a path. If two files share a
    # logical name, a different digest is ambiguous and must fail closed;
    # identical bytes have one deterministic canonical path.
    _index_legacy_stls(index, (ROOT / 'hardware/stl', ROOT / 'model'))
    return index, dict(sorted(manifest_inputs.items()))


def _mesh_geometry_sha256(mesh):
    """Hash normalized triangle corner geometry independent of STL indexing.

    STL reloads are allowed to reorder vertices, faces, and winding.  Hashing
    the raw arrays would therefore reject a byte-equivalent solid after a
    normal reload, while the old edge/area summary could accept a different
    topology with the same coarse statistics.  Canonical sorted triangle
    corners retain the complete surface geometry and remain stable across
    those representation changes.
    """
    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(mesh.faces, dtype=np.int64)
    if (vertices.ndim != 2 or vertices.shape[1] != 3
            or faces.ndim != 2 or faces.shape[1] != 3
            or len(vertices) == 0 or len(faces) == 0
            or not np.isfinite(vertices).all()
            or np.any(faces < 0) or np.any(faces >= len(vertices))):
        raise ValueError('mesh geometry hash input is malformed')
    corners = np.round(vertices[faces], decimals=7)
    # Sorting the three corners removes winding/vertex-index dependence;
    # sorting the resulting triangles removes face-order dependence.
    triangles = np.asarray(
        sorted(tuple(sorted(tuple(point) for point in triangle))
               for triangle in corners),
        dtype=np.float64,
    )
    triangles = np.ascontiguousarray(triangles)
    digest = hashlib.sha256()
    digest.update(str(triangles.dtype).encode())
    digest.update(repr(tuple(triangles.shape)).encode())
    digest.update(triangles.tobytes())
    return digest.hexdigest()


def _legacy_print_first_name(name):
    """共通の旧部品名規則を自己干渉台帳へ適用する。"""
    return COLLISION.print_first_legacy_name(name)


def _print_first_inventory_contract(parts, *, context_generated=False,
                                    require_stl_sha=True):
    """生成済みprint-first部品集合と実STL SHAの監査台帳を作る。

    ``context_generated`` は呼び出し側が ``context(generated=True)`` に
    入った事実を明示するための引数であり、active flagだけでは旧集合を
    誤って許可しない。helper占有箱のようにSTLを持たない候補は
    ``generated_geometry`` として記録する。
    """
    from sim_collision import _mesh_validation, collision_material

    if require_stl_sha is not True:
        raise ValueError(
            'require_stl_sha=False is forbidden: print-first inventory always '
            'requires source STL or allowlisted generator provenance')
    if not isinstance(parts, dict):
        raise ValueError('print-first inventory parts must be a mapping')
    source_index, manifest_inputs = _print_first_stl_index()
    rows = []
    missing_stl = []
    stl_geometry_failures = []
    unknown_generated_geometry = []
    stl_mesh_cache = {}
    for link in sorted(parts):
        items = parts[link]
        if not isinstance(items, (list, tuple)):
            raise ValueError(f'print-first inventory rows for {link} are not a sequence')
        for occurrence, item in enumerate(items):
            if not isinstance(item, (list, tuple)) or len(item) != 3:
                raise ValueError(f'print-first inventory row {link}/{occurrence} is malformed')
            mesh, _color, name = item
            if not isinstance(name, str) or not name:
                raise ValueError(f'print-first inventory row {link}/{occurrence} has no part name')
            mesh, validation = _mesh_validation(mesh, f'inventory {link}/{name}')
            base = name.split('#', 1)[0]
            source = source_index.get(name) or source_index.get(base)
            if source is None:
                provenance = _generated_geometry_provenance(name)
                if provenance is None:
                    unknown_generated_geometry.append(f'{link}/{name}')
                    source = {
                        'source_kind': 'unknown_generated_geometry',
                        'stl_path': None,
                        'stl_sha256': None,
                        'manifest_path': None,
                        'generator': None,
                    }
                else:
                    source = {
                        'source_kind': 'generated_geometry',
                        'stl_path': None,
                        'stl_sha256': None,
                        'manifest_path': None,
                        'generator': provenance,
                    }
                    # The generator source SHA is the reproducibility proof for
                    # analytic collision envelopes that have no STL file.
                    if not provenance['sources']:
                        missing_stl.append(f'{link}/{name}:generator')
            # A forbidden legacy name may have no current material rule (for
            # example ``pf_head_top``).  Keep it in the structured FAIL
            # inventory instead of raising before the shared legacy contract
            # can report the bad row.  Valid adopted/generated names still
            # require the normal strict material lookup.
            legacy_token = _legacy_print_first_name(name)
            try:
                material = collision_material(name)
            except ValueError:
                if legacy_token is None:
                    raise
                material = 'LEGACY_FORBIDDEN'
            stl_loaded_sha = None
            stl_runtime_sha = None
            stl_geometry_match = None
            stl_transform_count = None
            if source.get('source_kind') == 'stl':
                try:
                    source_path = source.get('_source_path')
                    if source_path is None:
                        source_path = _resolve_repo_stl_path(
                            source.get('stl_path'), f'{link}/{name} STL')
                    else:
                        source_path = _resolve_repo_stl_path(
                            str(source_path), f'{link}/{name} STL')
                    actual_sha = _file_sha256(source_path)
                    if actual_sha != source.get('stl_sha256'):
                        raise ValueError('referenced STL SHA differs from source index')
                    loaded = stl_mesh_cache.get(actual_sha)
                    if loaded is None:
                        loaded = _load_verified_stl_mesh(source_path)
                        stl_mesh_cache[actual_sha] = loaded
                    stl_loaded_sha = _mesh_geometry_sha256(loaded)
                    runtime_mesh_sha = _mesh_geometry_sha256(mesh)
                    stl_runtime_sha = runtime_mesh_sha
                    transforms = source.get('_transforms') or [np.eye(4).tolist()]
                    candidates = []
                    for transform in transforms:
                        matrix = np.asarray(transform, dtype=float)
                        if matrix.shape != (4, 4) or not np.isfinite(matrix).all():
                            raise ValueError('source transform is malformed')
                        transformed = loaded.copy()
                        transformed.apply_transform(matrix)
                        candidates.append(_mesh_geometry_sha256(transformed))
                    stl_geometry_match = bool(
                        stl_loaded_sha == runtime_mesh_sha
                        or runtime_mesh_sha in candidates)
                    stl_transform_count = len(candidates)
                    if not stl_geometry_match:
                        raise ValueError(
                            'reloaded STL geometry does not match runtime mesh')
                except (OSError, TypeError, ValueError) as exc:
                    stl_geometry_failures.append(f'{link}/{name}: {exc}')
            rows.append({
                'link': link,
                'part': name,
                'occurrence': occurrence,
                'material': material,
                'source_kind': source['source_kind'],
                'stl_path': source['stl_path'],
                'stl_sha256': source['stl_sha256'],
                'manifest_path': source['manifest_path'],
                'generator': source.get('generator'),
                'mesh_sha256': _mesh_geometry_sha256(mesh),
                'mesh_validation': validation,
                'stl_loaded_mesh_sha256': stl_loaded_sha,
                'stl_runtime_mesh_sha256': stl_runtime_sha,
                'stl_geometry_match': stl_geometry_match,
                'stl_transform_count': stl_transform_count,
            })
    rows.sort(key=lambda row: (row['link'], row['part'], row['occurrence']))
    names = [row['part'] for row in rows]
    duplicate_part_keys = sorted({
        (row['link'], row['part'])
        for row in rows
        if sum(1 for candidate in rows
               if candidate['link'] == row['link']
               and candidate['part'] == row['part']) > 1
    })
    required_counts = {name: names.count(name) for name in PRINT_FIRST_REQUIRED_PARTS}
    shoe_rows = [row for row in rows if row['part'] == 'tpu_shoe']
    shoe_name_anomalies = [
        row['part'] for row in rows
        if row['part'].startswith('tpu_shoe#') or row['part'].startswith('tpu_shoe_')
    ]
    shoe_links = [row['link'] for row in shoe_rows]
    expected_shoe_links = {
        f'leg_{leg.lower()}_tibia' for leg in ('FR', 'FL', 'RL', 'RR')}
    legacy_hits = [
        {'link': row['link'], 'part': row['part'], 'token': _legacy_print_first_name(row['part'])}
        for row in rows if _legacy_print_first_name(row['part']) is not None
    ]
    stl_rows = [row for row in rows if row['source_kind'] == 'stl']
    stl_sha_complete = bool(
        not missing_stl and stl_rows
        and all(isinstance(row['stl_sha256'], str)
                and len(row['stl_sha256']) == 64
                and row.get('stl_geometry_match') is True
                for row in stl_rows)
        and not stl_geometry_failures)
    checks = {
        'generated_context_entered': bool(context_generated),
        'print_first_active': bool(getattr(E.C, 'PRINT_FIRST_ACTIVE', False)),
        'required_new_parts_exactly_once': all(count == 1 for count in required_counts.values()),
        # A source part is the unit bound to one cache entry and one compiled
        # geometry set.  Repeating the same (link, part) silently doubles
        # occupancy, so the inventory must fail before any collision proof.
        'part_names_unique_per_link': not duplicate_part_keys,
        'tpu_shoe_exactly_four': len(shoe_rows) == 4,
        'tpu_shoe_name_exact': not shoe_name_anomalies,
        'tpu_shoe_material_tpu': len(shoe_rows) == 4 and all(
            row['material'] == 'TPU' for row in shoe_rows),
        'tpu_shoe_links_exactly_four_legs': (
            len(shoe_links) == 4 and len(set(shoe_links)) == 4
            and set(shoe_links) == expected_shoe_links),
        'legacy_replaced_parts_absent': not legacy_hits,
        'stl_sha_inventory_complete': stl_sha_complete,
        'stl_geometry_matches_runtime': bool(
            stl_rows and all(row.get('stl_geometry_match') is True for row in stl_rows)
            and not stl_geometry_failures),
        'generated_geometry_allowlist_complete': not unknown_generated_geometry,
    }
    inventory = {
        'status': 'PASS' if all(checks.values()) else 'FAIL',
        'context': {
            'generated': bool(context_generated),
            'print_first_active': bool(getattr(E.C, 'PRINT_FIRST_ACTIVE', False)),
            'model_output_root': _public_root_path(getattr(E, 'OUT', ROOT)),
        },
        'required_new_parts': list(PRINT_FIRST_REQUIRED_PARTS),
        'required_new_part_counts': required_counts,
        'forbidden_legacy_part_names': list(PRINT_FIRST_LEGACY_PART_NAMES),
        'legacy_hits': legacy_hits,
        'duplicate_part_keys': [list(key) for key in duplicate_part_keys],
        'tpu_shoe_name_anomalies': shoe_name_anomalies,
        'checks': checks,
        'missing_stl_sources': missing_stl,
        'stl_geometry_failures': stl_geometry_failures,
        'unknown_generated_geometry': unknown_generated_geometry,
        'parts': rows,
        'stl_sha256_by_part': {
            f"{row['link']}/{row['part']}#{row['occurrence']}": row['stl_sha256']
            for row in rows if row['stl_sha256'] is not None
        },
        'input_sha256': manifest_inputs,
    }
    inventory_without_hash = dict(inventory)
    inventory_without_hash.pop('input_sha256', None)
    inventory['inventory_sha256'] = hashlib.sha256(
        json.dumps(inventory_without_hash, sort_keys=True,
                   ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    # Keep the full inventory available to callers for diagnostics; the final
    # audit fails closed based on this status.
    return inventory


def _inventory_input_hashes(inventory):
    values = inventory.get('input_sha256', {})
    if not isinstance(values, dict):
        raise ValueError('print-first inventory input_sha256 is malformed')
    result = dict(values)
    for row in inventory.get('parts', []):
        path = row.get('stl_path')
        digest = row.get('stl_sha256')
        if path is not None and digest is not None:
            result[path] = digest
        for source in (row.get('generator') or {}).get('sources', []):
            path = source.get('path')
            digest = source.get('sha256')
            if path is not None and digest is not None:
                result[path] = digest
    inventory_digest = inventory.get('inventory_sha256')
    if inventory_digest is not None:
        result['$PRINT_FIRST/inventory_sha256'] = inventory_digest
    return dict(sorted(result.items()))


def _input_hashes(trace_path, native, extra_hashes=None):
    """形状入力に加えて、実行したnative traceも入力契約へ含める。"""
    hashes = S.input_fingerprints()
    if extra_hashes:
        for key, digest in extra_hashes.items():
            _trace_sha(digest, f'input SHA {key}')
            hashes[str(key)] = digest
    if trace_path is not None:
        key, digest = _trace_input_descriptor(trace_path)
    else:
        key, digest = '$GENERATED/native_output_trace', _generated_trace_digest(native)
    hashes[f'$TRACE/{key}' if not key.startswith('$') else key] = digest
    return dict(sorted(hashes.items())), key, digest


def _audit_generated_context(out, include_servos, trace_path, native,
                             full_reachable, expected_pose_count):
    """生成済みprint-first context内で実メッシュ監査を実行する。"""
    from sim_collision import _mesh_validation, parts_with_pad

    if not bool(getattr(E.C, 'PRINT_FIRST_ACTIVE', False)):
        raise ValueError('self-collision audit requires PRINT_FIRST_ACTIVE context')
    parts = parts_with_pad(include_servos, include_components=True)
    # Keep the audit source bundle identical to the print-first collision
    # builder/checker.  Electronics occupancy is an explicit base-link input;
    # omitting it here would let an all-link artifact prove a different model
    # from the one consumed by MuJoCo.
    inventory = _print_first_inventory_contract(
        parts, context_generated=True, require_stl_sha=True)
    if inventory['status'] != 'PASS':
        failed = [name for name, value in inventory['checks'].items() if not value]
        raise ValueError('print-first inventory contract failed: ' + ', '.join(failed))
    inventory_inputs = _inventory_input_hashes(inventory)
    # The five-pair exclusion table is executable policy.  Include its
    # canonical digest in the audit input ledger so a changed allowlist cannot
    # be paired with an old full-link sweep artifact.  The collision cache is
    # added immediately after build_model selects the files it consumed.
    proxy_exclusion_contract = COLLISION.print_first_proxy_exclusion_contract()
    inventory_inputs['$PROXY_EXCLUSION_CONTRACT'] = proxy_exclusion_contract['sha256']
    fixed_intersections=[];fixed_errors=[]
    if include_servos:
        for (a,_,n1),(b,_,n2) in itertools.combinations(parts['base_link'],2):
            if not any(
                    n.endswith('_servo_case') or n.endswith('_ld220_case')
                    or n.startswith('eye_carrier#')
                    for n in (n1,n2)):
                continue
            a,_ = _mesh_validation(a, f'fixed source {n1}')
            b,_ = _mesh_validation(b, f'fixed source {n2}')
            if np.any(a.bounds[1]<=b.bounds[0]) or np.any(b.bounds[1]<=a.bounds[0]):continue
            try:
                cut=trimesh.boolean.intersection([a,b],engine='manifold',check_volume=False)
                volume=_intersection_volume(cut, f'fixed Boolean {n1}/{n2}')
                if volume>INTERSECTION_THRESHOLD_MM3:
                    fixed_intersections.append({
                        'part1':n1,'part2':n2,'intersection_mm3':volume,
                        'bounds_mm':cut.bounds.tolist()})
            except Exception as ex:
                fixed_errors.append({'parts':[n1,n2],'error':str(ex)})
    m,idx=S.build_model(
        1., {'leg':24,'arm':.8,'eye':.05},
        {'leg':.4,'arm':.03,'eye':.005}, self_collision=True,
        include_parent_collision=True,
        contact_model='parts' if include_servos else 'linked-hulls',
        include_servo_collision=include_servos)
    proxy_exclusion_contract = idx.get('proxy_exclusion_contract',
                                      proxy_exclusion_contract)
    proxy_exclusion_compiled = idx.get('proxy_exclusion_compiled', {
        'status': 'NOT_APPLICABLE', 'expected_count': 0,
        'compiled_count': 0, 'expected_signatures': [],
        'compiled_excludes': [],
    })
    collision_cache_ledger = idx.get('collision_cache_ledger')
    cache_input_hash, collision_cache_ledger_public = (
        COLLISION.collision_cache_input_fingerprints(
            collision_cache_ledger, output_root=Path(out).resolve().parent)
        if collision_cache_ledger is not None else ({}, None))
    start_inputs = {**inventory_inputs, **cache_input_hash}
    hashes, trace_key, source_trace_sha256 = _input_hashes(
        trace_path, native, extra_hashes=start_inputs)
    rows=[]
    frame_qdeg_rows=[]
    if full_reachable:
        if expected_pose_count is not None and len(native) != expected_pose_count:
            raise ValueError(
                f'full reachable trace pose count {len(native)} != expected {expected_pose_count}')
        # Full coverage uses every native row, including holding rows. Reusing
        # a historical holding sample would silently omit trace endpoints.
        pose_specs=[{
            'name': f'native_{i:04d}', 'sample': i,
            'holding': not bool(row[1]), 'zero': False,
            'target_phase': None,
        } for i,row in enumerate(native)]
    else:
        pose_specs=[
            {'name':'holding','sample':99,'holding':True,'zero':False,'target_phase':None},
            {'name':'zero','sample':99,'holding':True,'zero':True,'target_phase':None},
        ] + [
            {'name':f'walk_{i:02d}','sample':None,'holding':False,
             'zero':False,'target_phase':i/16.0}
            for i in range(16)
        ]
    for spec in pose_specs:
        name=spec['name'];zero=spec['zero'];sample=spec['sample']
        if sample is None:
            candidates=np.arange(180,len(native))
            target_phase=spec['target_phase']
            delta=abs((native[candidates,0]-target_phase+.5)%1-.5)
            sample=int(candidates[np.argmin(delta)])
        q=dict(zip(S.ALL_JOINTS,np.radians(native[sample,3:23])))
        d=mujoco.MjData(m)
        if zero:
            q.update({n:0 for n in S.ALL_LEG_JOINTS})
            for side in ('r','l'):
                q.update({f'arm_{side}_yaw':0,
                          f'arm_{side}_pitch':0,
                          f'arm_{side}_elbow':np.pi/4})
        for n,v in q.items():
            d.qpos[m.jnt_qposadr[idx['jid'][n]]]=v
        d.qpos[2]=.3
        mujoco.mj_forward(m,d)
        contacts=self_contacts(m,d)
        qdeg={n:float(np.degrees(v)) for n,v in q.items()};world={}
        for link,items in parts.items():
            frame=E.LINK_PARENT_FRAME[link](qdeg)
            world[link]=[]
            for mesh,_,part in items:
                mesh,_ = _mesh_validation(mesh, f'source {link}/{part}')
                mm=mesh.copy();mm.apply_transform(frame)
                _mesh_validation(mm, f'world {link}/{part}')
                world[link].append((part,mm))
        frame_qdeg_rows.append(dict(qdeg))
        pose={
            'name':name,'pose_index': int(sample), 'angles_deg':qdeg,
            'native_phase':float(native[sample,0]),
            'pose_source':(
                'ideal mechanical neutral before calibrated output' if zero else
                'actual C++ Gait+LegOutput+Arms+Servos with PWM quantization'),
            'pairs':[],
        }
        bounds={
            link:np.array([
                np.min([mesh.bounds[0] for _,mesh in items],axis=0),
                np.max([mesh.bounds[1] for _,mesh in items],axis=0)])
            for link,items in world.items() if items}
        # Every link pair is represented at every finite trace row.  Pairs
        # whose world AABBs are disjoint retain a zero-cost NO_INTERSECTION
        # row, so a missing candidate cannot be mistaken for a clean audit.
        # This makes the saved artifact an auditable all-pair matrix rather
        # than a candidate-only list that could omit a later overlap.
        for l1, l2 in itertools.combinations(sorted(bounds), 2):
            pair = '|'.join((l1, l2))
            depth=contacts.get(pair)
            collisions=[];errors=[];tested=0;positive_below_threshold_count=0
            max_intersection_mm3=0.0
            bbox_overlap = (np.all(bounds[l1][1] > bounds[l2][0])
                            and np.all(bounds[l2][1] > bounds[l1][0]))
            if not bbox_overlap:
                pose['pairs'].append({
                    'links': [l1, l2],
                    'sample_count': 1,
                    'hull_penetration_mm': depth * 1000 if depth is not None else None,
                    'bbox_candidate_pairs': 0,
                    'boolean_evaluations': 0,
                    'positive_below_threshold_count': 0,
                    'max_intersection_mm3': 0.0,
                    'classification': 'NO_INTERSECTION',
                    'actual_intersections': [],
                    'errors': [],
                    'clean': True,
                })
                continue
            for (n1,a),(n2,b) in itertools.product(world[l1],world[l2]):
                if np.any(a.bounds[1]<=b.bounds[0]) or np.any(b.bounds[1]<=a.bounds[0]):continue
                tested+=1
                try:
                    mm=trimesh.boolean.intersection(
                        [a,b],engine='manifold',check_volume=False)
                    vol=_intersection_volume(mm, f'Boolean {l1}/{l2}/{n1}/{n2}')
                    max_intersection_mm3=max(max_intersection_mm3, float(vol))
                    if vol>INTERSECTION_THRESHOLD_MM3:
                        collisions.append({
                            'sample_index': int(sample),
                            'sample': name,
                            'part1':n1,'part2':n2,'intersection_mm3':vol,
                            'intersection_bounds_mm':mm.bounds.tolist()})
                    elif vol > 0.0:
                        positive_below_threshold_count += 1
                except Exception as ex:
                    errors.append({'parts':[n1,n2],'error':str(ex)})
            classification=(
                'ACTUAL_MESH_INTERSECTION' if collisions else
                'BOOLEAN_UNRESOLVED' if errors else
                'CONVEX_HULL_FALSE_CONTACT' if depth is not None else
                'NO_INTERSECTION')
            pose['pairs'].append({
                'links':[l1,l2],
                'sample_count': 1,
                'hull_penetration_mm':depth*1000 if depth is not None else None,
                'bbox_candidate_pairs':tested,
                'boolean_evaluations':tested,
                'positive_below_threshold_count':positive_below_threshold_count,
                'max_intersection_mm3':max_intersection_mm3,
                'classification':classification,
                'actual_intersections':collisions,
                'errors':errors,
                'clean': not collisions and not errors})
        rows.append(pose)
        print(name,len(contacts),'hull pairs',
              sum(bool(r['actual_intersections']) for r in pose['pairs']),
              'real intersecting pairs',flush=True)

    # Hash and serialize once at the end of the sweep. This preserves the
    # start/end input contract without O(N^2) rehashing or JSON rewrites.
    end_inventory = _print_first_inventory_contract(
        parts, context_generated=True, require_stl_sha=True)
    end_inventory_inputs = _inventory_input_hashes(end_inventory)
    # Keep the end-of-sweep ledger byte-for-byte equivalent to the start
    # ledger.  The exclusion table is executable policy, so a changed
    # allowlist must invalidate this artifact just like the start snapshot.
    end_inventory_inputs['$PROXY_EXCLUSION_CONTRACT'] = (
        proxy_exclusion_contract['sha256'])
    end_cache_hash, collision_cache_ledger_current = (
        COLLISION.collision_cache_input_fingerprints(
            collision_cache_ledger, output_root=Path(out).resolve().parent)
        if collision_cache_ledger is not None else ({}, None))
    end_inventory_inputs.update(end_cache_hash)
    current_hashes, _, current_trace_sha256 = _input_hashes(
        trace_path, native, extra_hashes=end_inventory_inputs)
    pose_rows_clean=all(
        not pair.get('actual_intersections') and not pair.get('errors')
        for pose in rows for pair in pose.get('pairs', []))
    fixed_rows_clean=not fixed_intersections and not fixed_errors
    frame_transform_contract = _compiled_frame_report(
        m, frame_qdeg_rows,
        sorted(link for link, items in parts.items() if items))
    payload={
        'input_sha256': hashes,
        'input_sha256_current': current_hashes,
        'inputs_unchanged': hashes == current_hashes,
        'source_trace_key': trace_key,
        'source_trace_sha256': source_trace_sha256,
        'source_trace_sha256_current': current_trace_sha256,
        'source_trace_path': trace_key if trace_path is not None else None,
        'model_path': _public_root_path(S.URDF_PATH),
        'print_first_inventory': inventory,
        'print_first_inventory_current': end_inventory,
        'proxy_exclusion_contract': proxy_exclusion_contract,
        'proxy_exclusion_compiled': proxy_exclusion_compiled,
        'collision_cache_ledger': collision_cache_ledger_public,
        'collision_cache_ledger_current': collision_cache_ledger_current,
        'proxy_exclusion_interpretation': {
            'parent_collision_filtering': 'globally_disabled',
            'fixed_pair_exclusions_only': True,
            'runtime_case_additions': 'forbidden',
            'exact_source_proof_required': True,
            'continuous_reachable_set_proven': False,
        },
        'method': 'All link pairs with intersecting world AABBs plus MuJoCo hull contacts including parent pairs, exact source part Boolean manifold; only an empty result or a signed negative remainder within config.BOOLEAN_NEGATIVE_TOLERANCE_MM3 is treated as zero, while positive intersections above config.BOOLEAN_INTERSECTION_THRESHOLD_MM3 remain collisions. Fixed-base servo cases and eye carriers are also checked against every same-base part; these contacts cannot exert forces on one rigid MuJoCo body. Fixed shell-shell assembly is covered by mechanical audit. Intersections require fit-vs-collision interpretation, not automatic removal.',
        'coverage': 'finite_native_trace' if full_reachable else 'sampled_phase_subset',
        'link_names': sorted(link for link, items in parts.items() if items),
        'expected_link_pair_count': (
            len(list(itertools.combinations(
                sorted(link for link, items in parts.items() if items), 2)))),
        'link_pair_coverage': 'all_link_pairs',
        'finite_pose_sweep_requested': bool(full_reachable),
        'processed_pose_count': len(rows),
        'native_pose_count': len(native),
        'expected_pose_count': expected_pose_count,
        'expected_pose_count_matches': (
            expected_pose_count is None or len(native) == expected_pose_count),
        'pose_count_matches': len(rows) == len(native),
        'finite_pose_sweep_complete': False,
        'finite_pose_count': len(rows),
        'continuous_reachable_set_proven': False,
        'continuous_proof': {
            'status':'UNVERIFIED',
            'required_metrics':[
                'adjacent_pose_max_mesh_displacement_upper_bound_mm',
                'minimum_separation_mm'],
            'reason':'finite pose Boolean sweep does not prove unsampled continuous intervals',
        },
        'fixed_base_intersections':fixed_intersections,
        'fixed_base_boolean_errors':fixed_errors,
        'frame_transform_contract': frame_transform_contract,
        'poses':rows,
    }
    payload['finite_pose_sweep_complete'] = bool(
        full_reachable and payload['inputs_unchanged']
        and payload['pose_count_matches']
        and payload['expected_pose_count_matches']
        and inventory['status'] == 'PASS'
        and end_inventory['status'] == 'PASS')
    payload['finite_pose_sweep_clean'] = bool(
        payload['finite_pose_sweep_complete']
        and pose_rows_clean and fixed_rows_clean)
    payload['status'] = _audit_status(payload)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload,ensure_ascii=False,indent=2))
    return rows


def audit(out, include_servos=False, trace_path=None, full_reachable=False,
          expected_pose_count=None):
    """生成済みprint-first contextへ束縛してから実メッシュ監査を行う。"""
    from print_first_assembly import context as print_first_context

    assembly_context = print_first_context(generated=True)
    assembly_context.__enter__()
    old_urdf = S.URDF_PATH
    old_convex = COLLISION.convex_parts
    old_collision_output = COLLISION.OUTPUT_ROOT
    # Standalone audits must be reproducible without rewriting the tracked
    # collision cache.  ``sim_collision.convex_parts`` has a historical
    # default argument bound to its repository cache, so pass the run-local
    # cache explicitly through a small wrapper and restore it below.
    collision_cache = Path(out).resolve().parent / 'collision-cache'
    collision_cache.mkdir(parents=True, exist_ok=True)

    def convex_with_output(*args, **kwargs):
        if 'cache' in kwargs:
            raise ValueError('standalone self-collision audit controls its collision cache')
        # ``sim_collision.convex_parts`` publishes its manifest attributes via
        # the module-level function name.  Because this wrapper temporarily
        # occupies that name, the attributes are written directly onto this
        # wrapper; copying from ``old_convex`` would reintroduce stale values.
        return old_convex(*args, cache=collision_cache, **kwargs)

    COLLISION.convex_parts = convex_with_output
    COLLISION.OUTPUT_ROOT = collision_cache
    try:
        generated_urdf = (ROOT / 'hardware/urdf-print-first/tachikoma.urdf').resolve()
        if not generated_urdf.is_file():
            raise FileNotFoundError(f'print-first generated URDF is missing: {generated_urdf}')
        # Keep the MuJoCo model and collected source parts in the same generated
        # context. The ordinary hardware/urdf model would silently reintroduce
        # legacy geometry even when the parts list had been replaced.
        S.URDF_PATH = generated_urdf
        native=(_load_native_trace(trace_path) if trace_path else
                native_output_trace({'segments':[{'name':'holding','duration':2.},
                    {'name':'walk','duration':4.8,'vy':1.}]}))
        result = _audit_generated_context(
            out, include_servos, trace_path, native,
            full_reachable, expected_pose_count)
    except BaseException as exc:
        S.URDF_PATH = old_urdf
        COLLISION.convex_parts = old_convex
        COLLISION.OUTPUT_ROOT = old_collision_output
        assembly_context.__exit__(type(exc), exc, exc.__traceback__)
        raise
    else:
        S.URDF_PATH = old_urdf
        COLLISION.convex_parts = old_convex
        COLLISION.OUTPUT_ROOT = old_collision_output
        assembly_context.__exit__(None, None, None)
    return result

if __name__=='__main__':
    import argparse
    a=argparse.ArgumentParser()
    a.add_argument('--out',type=Path,required=True)
    a.add_argument('--include-servos',action='store_true')
    a.add_argument('--trace-json',type=Path,help='export_print_first_native_traceのJSONを全行掃引へ使う')
    a.add_argument('--full-reachable',action='store_true',help='trace-jsonの全有限姿勢を監査する')
    a.add_argument('--expected-pose-count',type=int)
    args=a.parse_args()
    try:
        audit(args.out,args.include_servos,args.trace_json,args.full_reachable,args.expected_pose_count)
    except KeyboardInterrupt as exc:
        _write_incomplete(args.out, f'audit interrupted: {exc}',
                          trace_path=args.trace_json,
                          full_reachable=args.full_reachable,
                          expected_pose_count=args.expected_pose_count)
        raise SystemExit(130)
    except Exception as exc:  # noqa: BLE001 - CLI must leave an INCOMPLETE artifact
        _write_incomplete(args.out, exc, trace_path=args.trace_json,
                          full_reachable=args.full_reachable,
                          expected_pose_count=args.expected_pose_count)
        print(f'sim_self_collision: INCOMPLETE: {exc}', file=sys.stderr)
        raise SystemExit(1)
    payload=json.loads(args.out.read_text(encoding='utf-8'))
    status=_audit_status(payload)
    if payload.get('status') != status:
        payload['status']=status
        args.out.write_text(json.dumps(payload,ensure_ascii=False,indent=2))
    print(f'sim_self_collision: {status}')
    raise SystemExit(0 if status == 'PASS' else 1)
