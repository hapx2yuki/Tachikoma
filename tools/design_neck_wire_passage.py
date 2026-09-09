#!/usr/bin/env python3
"""Cabin 通線用ネック内孔の有限候補。印刷品は更新せず断面感度と干渉を保存。"""
import json
from pathlib import Path
import sys
import numpy as np
import trimesh
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'hardware/src'))
import config as C
import make_chassis as M
from lib import cyl_y,cyl,to_trimesh
from check_print_strength_sensitivity import section_moduli

def main():
    target=ROOT/'docs/audits/20260905-round2/neck-wire-candidates';target.mkdir(parents=True,exist_ok=True)
    baseline=M.pod_neck();rows=[]
    head=trimesh.load(ROOT/'hardware/stl/Head_Top_Eyecut.stl',force='mesh')
    head.apply_transform(trimesh.transformations.rotation_matrix(np.pi,[0,0,1]))
    head.apply_translation([0,C.ARM_MOUNT_HUB_Y,57.7])
    for diameter,y_exit in [(0,0), *[(d,y) for d in (6.,8.) for y in (-80.,-75.,-70.,-65.)]]:
        channel=cyl_y(y_exit+145,diameter).translate([0,(y_exit-145)/2,C.POD_NECK_BEAM[1]/2]) if diameter else None
        if diameter:
            channel+=cyl(30,diameter).translate([0,y_exit,15+C.POD_NECK_BEAM[1]/2])
        body=baseline-channel if channel is not None else baseline
        tm=to_trimesh(body); name=f'neck_d{diameter:g}_exit{y_exit:g}'
        if diameter:tm.export(target/(name+'.stl'))
        world=tm.copy();world.apply_translation([0,0,C.CHASSIS_T])
        intersection=trimesh.boolean.intersection([world,head],engine='manifold')
        cases=[]
        for wall,density in [(1.6,1.),(1.6,.4)]:
            samples=[]
            for y in np.arange(-107.7,-43.2,.2)+.0137:
                mod=section_moduli(tm,y,1,wall,density)
                if mod is not None and min(mod)>0:
                    # 既存チェックと同じ 600g ×2g, Kt2.5。材料68MPaも実測ではない。
                    sf=68/(.6*9.81*2*abs(y+187.1)/mod[0]*2.5)
                    samples.append({'y_mm':float(y),'Z_mm3':float(mod[0]),'sf':float(sf)})
            cases.append({'wall_mm':wall,'density':density,'worst':min(samples,key=lambda p:p['sf'])})
        rows.append({'name':name,'diameter_mm':diameter,'exit_y_mm':y_exit,'volume_mm3':float(tm.volume),
                     'components':len(tm.split()),'watertight':bool(tm.is_watertight),'strength':cases,
                     'head_top_intersection_mm3':0. if intersection.is_empty else float(intersection.volume)})
        print(name,[(c['density'],round(c['worst']['sf'],3),round(c['worst']['y_mm'],2)) for c in cases],flush=True)
    (target/'comparison.json').write_text(json.dumps({'status':'CANDIDATES_ONLY_UNVERIFIED',
      'limitations':'600g/68MPa/Kt2.5を仮定した梁断面感度。電装後質量、出口応力集中、線束曲率、実印刷強度は未確認。',
      'coordinate_frame':'pod_neck STL local; chassis z = local z + CHASSIS_T','candidates':rows},ensure_ascii=False,indent=2)+'\n')

if __name__=='__main__':main()
