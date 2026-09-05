#!/usr/bin/env python3
"""キット配置の全行列について印刷倍率・直交性・参照先を検証する。"""
import argparse
import json
from pathlib import Path
import sys
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'hardware/src'))
import config as C
import kit_assembly as K

def run():
    rows=[]
    for p in K.load_placements():
        row={'part':p.part,'instance':p.instance,'source':p.source,'unresolved':p.unresolved}
        if p.matrix is not None:
            sv=np.linalg.svd(p.matrix[:3,:3])[1]
            row.update({'singular_values':sv.tolist(),'expected_scale':C.SCALE,
                        'finite':bool(np.isfinite(p.matrix).all()),
                        'affine_last_row':bool(np.allclose(p.matrix[3],[0,0,0,1],atol=1e-9)),
                        'scale_ok':bool(np.allclose(sv,C.SCALE,atol=1e-4))})
            row['pass']=row['finite'] and row['affine_last_row'] and row['scale_ok']
        else:
            row['pass']=bool(p.unresolved or (np.isfinite(p.R).all() and np.isfinite(p.t).all() and
                             np.allclose(p.R[:3,:3].T@p.R[:3,:3],np.eye(3),atol=1e-4)))
        rows.append(row)
    return {'pass':all(r['pass'] for r in rows),'unresolved_placements':sum(r['unresolved'] for r in rows),
            'note':'位置不明は別件として残す。参照/行列の整合は組立可能性の証明ではない。','placements':rows}

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--json',type=Path);a=ap.parse_args();r=run()
    if a.json:a.json.parent.mkdir(parents=True,exist_ok=True);a.json.write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({**r,'placements':[p for p in r['placements'] if not p['pass']]},ensure_ascii=False,indent=2))
    return 0 if r['pass'] else 1
if __name__=='__main__':sys.exit(main())
