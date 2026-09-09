"""実体交差の共通判定。計算不能を非干渉として扱わない。"""
import math
import numpy as np
import trimesh

BOOLEAN_NEGATIVE_TOLERANCE_MM3 = 1e-6


def checked_volume(value, *, label='Boolean', allow_tiny_negative=False,
                   negative_tolerance_mm3=BOOLEAN_NEGATIVE_TOLERANCE_MM3):
    """符号を保持してBoolean体積を検査する。負値をabs()/無条件clampしない。"""
    value = float(value)
    tolerance = float(negative_tolerance_mm3)
    if not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError(f'{label}: invalid negative-volume tolerance')
    if not math.isfinite(value):
        raise ValueError(f'{label}: Boolean volume is non-finite: {value}')
    if value < 0:
        if not allow_tiny_negative or value < -tolerance:
            raise ValueError(f'{label}: Boolean volume is negative: {value}')
        # Explicitly record the policy in the call site; this is an empty
        # numerical remainder, never an absolute-value repair.
        return 0.0
    return value


def _is_empty(result):
    value = getattr(result, 'is_empty', False)
    return bool(value() if callable(value) else value)


def _checked_boolean_result(result, *, label='Boolean'):
    """Manifold backend resultの状態・有限性・閉体を検査して体積を返す。"""
    if result is None:
        raise RuntimeError(f'{label}: Boolean operation returned no mesh')
    if _is_empty(result):
        return 0.0
    vertices = np.asarray(getattr(result, 'vertices', []), dtype=float)
    faces = np.asarray(getattr(result, 'faces', []))
    if (vertices.ndim != 2 or vertices.shape[1] != 3 or len(vertices) < 4 or
            faces.ndim != 2 or faces.shape[1] != 3 or len(faces) < 4 or
            not np.isfinite(vertices).all() or not np.isfinite(faces.astype(float)).all() or
            faces.min(initial=0) < 0 or faces.max(initial=0) >= len(vertices)):
        raise ValueError(f'{label}: Boolean output topology is invalid')
    if not result.is_watertight or not result.is_winding_consistent:
        raise ValueError(f'{label}: Boolean output is not watertight/winding-consistent')
    # trimesh's manifold engine returns a Trimesh and does not expose the
    # native status. Reconstruct it so the tiny signed tolerance is usable
    # only with a native NoError result.
    try:
        from manifold3d import Manifold, Mesh64
        # The trimesh result can contain coordinates around 1e2 mm while the
        # remaining intersection volume is below 1e-5 mm3.  Reconstructing
        # through Mesh (float32) quantizes those coordinates enough to change
        # both topology and the signed volume.  Mesh64 preserves the actual
        # Boolean result for the native cross-check.
        native = Manifold(Mesh64(np.asarray(vertices, np.float64),
                                 np.asarray(faces, np.uint32)))
    except Exception as exc:
        raise ValueError(f'{label}: native Manifold conversion failed') from exc
    status = native.status()
    if getattr(status, 'name', None) != 'NoError':
        raise ValueError(f'{label}: native Manifold status is not NoError: {status}')
    native_volume = float(native.volume())
    if not math.isfinite(native_volume):
        raise ValueError(f'{label}: native Manifold volume is non-finite')
    volume = float(result.volume)
    if not math.isfinite(volume):
        raise ValueError(f'{label}: Boolean volume is non-finite: {volume}')
    # Preserve orientation evidence. A sign mismatch is a conversion error,
    # except when both representations are within the existing tiny-negative
    # tolerance. In that case they are two zero-near numerical remainders
    # whose signs differ during float conversion; topology and native status
    # have already been checked above.
    signs_mismatch = ((volume < 0) != (native_volume < 0))
    tiny_signed_mismatch = (
        signs_mismatch and
        abs(volume) <= BOOLEAN_NEGATIVE_TOLERANCE_MM3 and
        abs(native_volume) <= BOOLEAN_NEGATIVE_TOLERANCE_MM3
    )
    if signs_mismatch and not tiny_signed_mismatch:
        raise ValueError(f'{label}: native/serialized signed volume mismatch')
    if tiny_signed_mismatch:
        if isinstance(getattr(result, 'metadata', None), dict):
            result.metadata.setdefault('signed_negative_validation', []).append({
                'serialized_volume_mm3': volume,
                'native_volume_mm3': native_volume,
                'native_status': getattr(status, 'name', str(status)),
                'tolerance_mm3': BOOLEAN_NEGATIVE_TOLERANCE_MM3,
                'sign_mismatch_within_tolerance': True,
            })
        return 0.0
    if native_volume < 0:
        volume = checked_volume(volume, label=label, allow_tiny_negative=True)
        if isinstance(getattr(result, 'metadata', None), dict):
            result.metadata.setdefault('signed_negative_validation', []).append({
                'serialized_volume_mm3': float(result.volume),
                'native_volume_mm3': native_volume,
                'native_status': getattr(status, 'name', str(status)),
                'tolerance_mm3': BOOLEAN_NEGATIVE_TOLERANCE_MM3,
            })
    else:
        volume = checked_volume(volume, label=label, allow_tiny_negative=False)
    return volume


def intersection_volume_mm3(a, b):
    result = trimesh.boolean.intersection([a, b], engine='manifold')
    return _checked_boolean_result(result, label='intersection')
