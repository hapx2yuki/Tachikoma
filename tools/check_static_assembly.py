#!/usr/bin/env python3
"""同じ固定リンク内の全パーツの体積共有を検出。意図した嵌合も要解釈とする。"""
import argparse
import hashlib
import itertools
import json
from pathlib import Path
import sys
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tools'), str(ROOT / 'hardware/src')]
from sim_collision import parts_with_pad
from sim_physics import input_fingerprints
from mesh_checks import intersection_volume_mm3
from export_urdf import LINK_PARENT_FRAME


def run():
    inputs = input_fingerprints()
    inputs[str(Path(__file__).relative_to(ROOT))] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    inputs['tools/mesh_checks.py'] = hashlib.sha256((ROOT / 'tools/mesh_checks.py').read_bytes()).hexdigest()
    links = parts_with_pad(True)
    intersections, errors = [], []
    missing = set(LINK_PARENT_FRAME) - set(links)
    empty = [name for name, items in links.items() if not items and name != 'camera_optical_frame']
    if missing or empty:
        errors.append({'error': '検査対象リンクの欠落/空形状', 'missing_links': sorted(missing), 'empty_links': empty})
    tested, pairs = 0, 0
    for link, parts in links.items():
        pairs += len(parts) * (len(parts) - 1) // 2
        for (a, _, n1), (b, _, n2) in itertools.combinations(parts, 2):
            if np.any(a.bounds[1] <= b.bounds[0]) or np.any(b.bounds[1] <= a.bounds[0]):
                continue
            tested += 1
            try:
                volume = intersection_volume_mm3(a, b)
                if volume > .01:
                    intersections.append({'link': link, 'parts': [n1, n2], 'intersection_mm3': volume})
            except Exception as error:
                errors.append({'link': link, 'parts': [n1, n2], 'error': str(error)})
    return {'input_sha256': inputs, 'parts': sum(len(p) for p in links.values()), 'parts_per_link': {k: len(v) for k, v in links.items()}, 'all_pairs': pairs,
            'tested_aabb_pairs': tested, 'intersection_tolerance_mm3': .01,
            'intersections': sorted(intersections, key=lambda x: -x['intersection_mm3']),
            'errors': errors, 'pass': not intersections and not errors,
            'interpretation': '全リンクそれぞれの同一剛体内部を検査する。動的接触では検出できない装飾と支持梁を含む。'
            '購入電装の仮位置箱は含めない。嵌合・数値近似・物理的に不可能な体積共有を個別解釈し、'
            '交差を自動的に除去しない。終了1は未解釈を含む交差あり。'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', type=Path, required=True)
    args = parser.parse_args()
    result = run()
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'input_sha256'}, ensure_ascii=False, indent=2))
    sys.exit(0 if result['pass'] else 1)
