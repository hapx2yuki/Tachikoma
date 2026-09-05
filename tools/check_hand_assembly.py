#!/usr/bin/env python3
"""固定爪の全9部品を個別検査し、未確認の圧入・接着代を合格にしない。"""
from __future__ import annotations
import argparse
import itertools
import json
from pathlib import Path
import sys
import numpy as np
import trimesh

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'hardware/src'))
import config as C
from mesh_checks import intersection_volume_mm3


def run():
    # 手首面（claw_mount原点）を共通基準とし、URDFに依存しない。
    forearm = trimesh.load(ROOT/'hardware/stl/forearm.stl', force='mesh')
    forearm.apply_translation([-C.FOREARM_LEN, 0, 0])
    parts = {'forearm': forearm,
             'claw_mount': trimesh.load(ROOT/'hardware/stl/claw_mount.stl', force='mesh')}
    for name, stem, transform in [('claw', 'Arm_Left_Claw_Grey', C.CLAW_TO_MOUNT)] + [
            (f'finger_{i}', 'Arm_Left_Finger_Black_x3', T) for i,T in enumerate(C.FINGER_TO_MOUNT)] + [
            (f'tip_{i}', 'Arm_Left_FingerTip_Grey_x3', T) for i,T in enumerate(C.FINGERTIP_TO_MOUNT)]:
        parts[name] = trimesh.load(ROOT/'model'/f'{stem}.stl', force='mesh').apply_transform(transform)
    pairs = [{'a': a, 'b': b, 'intersection_mm3': intersection_volume_mm3(parts[a], parts[b])}
             for a,b in itertools.combinations(parts, 2)]
    gaps = []
    for radius in (0.,2.,4.):
        for angle in np.linspace(0,2*np.pi,8,endpoint=False):
            y,z=radius*np.cos(angle),radius*np.sin(angle)
            loc,_,_=parts['claw'].ray.intersects_location([[-20.,y,z]], [[1.,0,0]])
            gaps.append({'y_mm':float(y),'z_mm':float(z),
                         'gap_mm': None if len(loc)==0 else float(loc[:,0].min()-C.CLAW_MOUNT_THICKNESS)})
    collisions = [row for row in pairs if row['intersection_mm3'] > .01]
    return {'status':'FAIL' if collisions else 'UNVERIFIED', 'pass':False,
            'coordinate_frame':'claw_mount原点。左腕はこの全体の鏡映で同じ交差体積。',
            'parts':list(parts), 'pairs':pairs, 'collisions':collisions, 'mount_face_rays':gaps,
            'joint_allowance_status':'UNVERIFIED: 元キットの圧入量、接着座、公差の実証がない。1mm残差や50mm3食い込みを許容とみなさない。',
            'numerical_volume_tolerance_mm3':.01,
            'limitations':'元STLの意図した圧入だったとしても、剛体交差そのものは残る。原型側の仕様/実測に基づく受け座と挿入検証が必要。接着・強度も別途確認。'}


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--json',type=Path)
    args=ap.parse_args();result=run()
    text=json.dumps(result,ensure_ascii=False,indent=2)+'\n'
    if args.json:
        args.json.parent.mkdir(parents=True,exist_ok=True)
        args.json.write_text(text)
    print(text,end='')
    return 0 if result['pass'] else 1


if __name__=='__main__':sys.exit(main())
