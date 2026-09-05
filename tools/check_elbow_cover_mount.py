#!/usr/bin/env python3
"""肘カバーをケース底へ合わせる位置候補の全リンク占有比較。"""
import argparse,json,sys
from pathlib import Path
import numpy as np
import trimesh
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
import export_urdf as E
from sim_collision import parts_with_pad
from mesh_checks import intersection_volume_mm3

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source-current',action='store_true',help='現在のCAD前腕/肘殻を全実姿勢で検査')
    args=ap.parse_args()
    dest=ROOT/'docs/audits/20260905-round2/elbow-cover-candidates';dest.mkdir(parents=True,exist_ok=True)
    if args.source_current:
        import make_arm,arm_shell
        from lib import to_trimesh
        source={'elbow_shell':to_trimesh(arm_shell.elbow_shell()),'forearm':to_trimesh(make_arm.forearm())}
        original_load=E.load
        E.load=lambda name,*a,**kw: source[name].copy() if name in source and not a and not kw else original_load(name,*a,**kw)
    parts=parts_with_pad(True);poses=json.loads((ROOT/'docs/audits/20260905-round2/simulation/self-collision-with-servos.json').read_text())['poses']
    rows=[]
    for offset in ((0.,) if args.source_current else (0.,-13.5,-13.8,-14.)):
        contacts=[]
        for pose in poses:
            world={}
            for link,items in parts.items():
                frame=E.LINK_PARENT_FRAME[link](pose['angles_deg'])
                world[link]=[(name,mesh.copy().apply_transform(frame)) for mesh,_,name in items]
            for side in ('r','l'):
                link=f'arm_{side}_upper';local=next(m.copy() for m,_,n in parts[link] if n=='elbow_shell')
                local.apply_translation([0,offset,0]);local.apply_transform(E.LINK_PARENT_FRAME[link](pose['angles_deg']))
                for other,items in world.items():
                    for name,mesh in items:
                        if other==link and name=='elbow_shell':continue
                        if np.any(local.bounds[1]<=mesh.bounds[0]) or np.any(mesh.bounds[1]<=local.bounds[0]):continue
                        volume=intersection_volume_mm3(local,mesh)
                        if volume>.01:contacts.append({'pose':pose['name'],'side':side,'other_link':other,'part':name,'intersection_mm3':volume})
        rows.append({'offset_local_y_mm':offset,'actual_contacts':contacts});print(offset,len(contacts),flush=True)
    result={'status':'現CADのケース底配置を全リンク比較' if args.source_current else '読込STLに対する追加Y移動量の比較。旧未移設STLへ-13.8を与えた初回結果はcomparison.jsonに保存済み。',
      'mode':'source' if args.source_current else 'STL-relative-offsets',
      'configured_cover_center_y_mm':E.C.ARM_ELBOW_COVER_Y,
      'coordinate_frame':'elbow_shell原点（肘軸）局所。軸/サーボ/骨格/外形は変更せず取付Yだけを移す。',
      'poses':len(poses),'variants':rows,'limits':'18実姿勢と0姿勢のみ。全到達集合/接着/可視配置の採用判断は別途。'}
    filename='source-current.json' if args.source_current else 'stl-relative-offsets.json'
    (dest/filename).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    if args.source_current and any(r['actual_contacts'] for r in rows):sys.exit(1)
if __name__=='__main__':main()
