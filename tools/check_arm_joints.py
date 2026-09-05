#!/usr/bin/env python3
"""隣接する腕部品の可動干渉を生 STL または CAD で検証する。URDF は用いない。"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import re
import sys
import numpy as np
import trimesh
from manifold3d import Manifold, Mesh
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'hardware/src'))
import config as C
import make_arm as A
import arm_shell as S
from lib import box, cyl_y, to_trimesh


def load(name, fn, source):
    if source:
        return fn()
    m = trimesh.load(ROOT / 'hardware/stl' / (name + '.stl'), force='mesh')
    return Manifold(Mesh(np.asarray(m.vertices, np.float32), np.asarray(m.faces, np.uint32)))


def volume(m):
    value = float(m.volume())
    if not np.isfinite(value) or value < -1e-6:
        raise ValueError('交差計算が有限な非負体積を返さなかった')
    return max(0., value)


def run(source=False, step=.25):
    b, u, f, s = [load(n, fn, source) for n, fn in (
        ('shoulder_bracket', A.shoulder_bracket), ('upper_arm', A.upper_arm),
        ('forearm', A.forearm), ('elbow_shell', S.elbow_shell))]
    pitch_z = 2.5 + A.PA['HORN_HUB_H'] - 2 + 2.5 + A.FRAME_TOP - .1
    fw = (ROOT / 'firmware/src/config.h').read_text()
    limits = {}
    for name, value in [('ARM_PITCH_MIN', C.ARM_PITCH_LIMIT_DEG[0]),
                        ('ARM_PITCH_MAX', C.ARM_PITCH_LIMIT_DEG[1]),
                        ('ARM_ELBOW_MIN', C.ARM_ELBOW_LIMIT_DEG[0]),
                        ('ARM_ELBOW_MAX', C.ARM_ELBOW_LIMIT_DEG[1])]:
        match = re.search(r'\b' + name + r'\s*=\s*([-\d.]+)f?', fw)
        limits[name] = bool(match and abs(float(match[1]) - value) < 1e-6)
    def sweep(a, moving, low, high, offset):
        rows = []
        for angle in np.linspace(low, high, int(np.ceil((high-low)/step))+1):
            rows.append({'angle_deg': float(angle), 'intersection_mm3': volume(
                a ^ moving.rotate([0, float(angle), 0]).translate(offset))})
        return {'worst_mm3': max(r['intersection_mm3'] for r in rows), 'samples': rows}
    pairs = {
        'shoulder_upper': sweep(b, u, *C.ARM_PITCH_LIMIT_DEG, [20, 0, -pitch_z]),
        'upper_forearm': sweep(u, f, *C.ARM_ELBOW_LIMIT_DEG, [C.UPPER_ARM_LEN, 0, 0]),
        'elbow_shell_forearm': sweep(s, f, *C.ARM_ELBOW_LIMIT_DEG, [0, 0, 0]),
    }
    # 剛体内の交差は物理エンジンの自己接触では検出されない。
    # タブ下面を原点とする実ケース主箱を、STL配置と独立に置く。
    case = box(A.PA['L'], A.PA['TAB_BELOW'], A.PA['W']).translate(
        [-A._cx, -A.PA['TAB_BELOW']/2, 0])
    shell_at_upper = s.translate([C.UPPER_ARM_LEN, 0, 0])
    fixed_pairs = {
        'upper_elbow_shell_mm3': volume(u ^ shell_at_upper),
        'elbow_case_shell_mm3': volume(case ^ s),
        'elbow_case_upper_mm3': volume(case.translate([C.UPPER_ARM_LEN,0,0]) ^ u),
        'shell_outside_rotational_clearance_mm3': volume(s - S.elbow_shell_swept_clearance()),
    }
    pairs['elbow_case_forearm'] = sweep(case, f, *C.ARM_ELBOW_LIMIT_DEG, [0,0,0])
    # ホーン円板の y>=PLATE_IN は加工前から残すべき荷重伝達面。
    # 上腕と前腕は同じ円板/同じホーン穴なので、この範囲を直接突き合わせる。
    disc_region = cyl_y(A.PLATE_T-.02, 2*A.DISC_R).translate(
        [0, A.PLATE_IN+A.PLATE_T/2, 0])
    horn_lost = volume((u ^ disc_region) - f)
    # 肩箱枠の材を残す。ヨー取付板の +Y 張出しだけが加工対象。
    baseline = A._shoulder_bracket_up().mirror([0, 0, 1])
    frame_region = box(100, 2*A.FRAME_Y, 100).translate([20, 0, -pitch_z])
    frame_lost = volume((baseline-b) ^ frame_region)
    sections = []
    for x in (10., 12., 14., 15., C.FOREARM_LEN-.1):
        sections.append({'x_mm': x, 'area_mm2': volume(f ^ box(.02, 100, 100).translate([x,0,0]))/.02})
    result = {'mode': 'source' if source else 'STL', 'step_deg': step,
              'firmware_limits_match': limits, 'pairs': pairs, 'fixed_pairs': fixed_pairs,
              'elbow_cover_center_y_mm': C.ARM_ELBOW_COVER_Y,
              'forearm_horn_disc_lost_mm3': horn_lost,
              'shoulder_servo_frame_lost_mm3': frame_lost,
              'forearm_sections': sections,
              'parts': {n: {'volume_mm3': volume(m), 'components': len(m.decompose()),
                            'watertight': bool(to_trimesh(m).is_watertight)}
                        for n,m in [('shoulder_bracket', b), ('upper_arm', u), ('forearm', f), ('elbow_shell', s)]},
              'strength_status': 'UNVERIFIED: 断面積は幾何記録。接着・層間強度と疲労の実証ではない'}
    result['pass'] = bool(all(limits.values()) and all(p['worst_mm3'] < .01 for p in pairs.values())
                          and all(v < .01 for v in fixed_pairs.values())
                          and horn_lost < .01 and frame_lost < .01
                          and all(p['components']==1 and p['watertight'] for p in result['parts'].values()))
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source', action='store_true')
    ap.add_argument('--step', type=float, default=.25)
    ap.add_argument('--json', type=Path)
    args = ap.parse_args()
    if not np.isfinite(args.step) or args.step <= 0 or args.step > 1:
        ap.error('--step は 0 より大きく 1 度以下')
    r = run(args.source, args.step)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(r, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({**r, 'pairs': {k: {'worst_mm3': v['worst_mm3'], 'samples':len(v['samples'])}
                                   for k,v in r['pairs'].items()}}, ensure_ascii=False, indent=2))
    return 0 if r['pass'] else 1


if __name__ == '__main__':
    sys.exit(main())
