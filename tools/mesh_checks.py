"""実体交差の共通判定。計算不能を非干渉として扱わない。"""
import math
import trimesh


def checked_volume(value):
    value = float(value)
    if not math.isfinite(value) or value < -1e-6:
        raise ValueError(f'交差体積が有限な非負値ではない: {value}')
    return max(0.0, value)


def intersection_volume_mm3(a, b):
    result = trimesh.boolean.intersection([a, b], engine='manifold')
    if result is None:
        raise RuntimeError('ブーリアン演算がメッシュを返さなかった')
    return 0.0 if result.is_empty else checked_volume(result.volume)
