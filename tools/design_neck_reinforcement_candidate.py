#!/usr/bin/env python3
"""ネック基部の局所補強を有限比較。標準部品は変更しない。

旧rampを実Head+隙間の差引きに替え、左右へ基部を広げる。
頭だけと強度だけの合格を、全外装との組立合格と混同しない。
"""
import hashlib,json,sys
from pathlib import Path
import numpy as np
import trimesh
from manifold3d import Manifold
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'tools'),str(ROOT/'hardware/src')]
import config as C,make_chassis as M,export_urdf as E
from lib import box,cyl,cyl_y,to_trimesh
from design_cabin_electronics import native,bbox
from check_print_strength_sensitivity import section_moduli
OUT=ROOT/'docs/audits/20260905-round2/neck-reinforcement-candidates'

def strength(mesh):
    cases=[]
    for density in [1.,.4]:
        samples=[]
        for y in np.arange(-107.7,-43.2,.2)+.0137:
            mod=section_moduli(mesh,y,1,1.6,density)
            if mod is not None and min(mod)>0:
                sf=68/(.6*9.81*2*abs(y+187.1)/mod[0]*2.5)
                samples.append({'y_mm':float(y),'section_modulus_mm3':float(mod[0]),'sf':float(sf)})
        cases.append({'wall_mm':1.6,'density':density,'worst':min(samples,key=lambda r:r['sf'])})
    return cases

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    head=trimesh.load(ROOT/'hardware/stl/Head_Top_Eyecut.stl',force='mesh')
    head.apply_transform(trimesh.transformations.rotation_matrix(np.pi,[0,0,1]))
    head.apply_translation([0,C.ARM_MOUNT_HUB_Y,57.7-C.CHASSIS_T]);head_m=native(head)
    baseline=M.pod_neck()
    old_relief=M._head_relief_cutter
    try:
        M._head_relief_cutter=lambda:Manifold()
        uncut=M.pod_neck()
    finally:M._head_relief_cutter=old_relief
    nearby=[]
    for mesh,color,name in E.base_link_parts():
        if name=='pod_neck' or name.startswith('Head_Top'):continue
        mesh=mesh.copy();mesh.apply_translation([0,0,-E.ZB-C.CHASSIS_T]);lo,hi=mesh.bounds
        if np.all(lo<[27,-42,13]) and np.all(hi>[-27,-109,-10]):nearby.append((name,native(mesh)))
    def other_contacts(body):return {name:max(0.,(body^other).volume()) for name,other in nearby}
    baseline_contact=other_contacts(baseline)
    rows=[]
    configs=[(w,-73.,.3,f'neck_width{w}') for w in [32,36,40,44,48]]
    configs += [(w,-59.,.3,f'neck_frontwidth{w}') for w in [40,44,48]]
    configs += [(w,-59.,2.,f'neck_gap2_width{w}') for w in [44,48,52]]
    for width,y_start,gap,name in configs:
        body=uncut+bbox([-width/2,y_start,0],[width/2,-43,12])
        for x,y in M.POD_BOLTS:
            body-=cyl(30,C.M3_FREE).translate([x,y,5])
            body-=cyl(20,7).translate([x,y,14]) # 既存基部高さ4mmの頭座と工具経路
        channel=cyl_y(70,8).translate([0,-110,6])+cyl(30,8).translate([0,-75,21])
        body-=head_m.minkowski_sum(box(2*gap,2*gap,2*gap))+channel
        path=OUT/(name+'.stl');to_trimesh(body.set_tolerance(.015)).export(path)
        mesh=trimesh.load(path,force='mesh');actual=native(mesh)
        overlap=max(0.,(actual^head_m).volume());s=strength(mesh)
        contacts=other_contacts(actual)
        row={'name':name,'width_mm':width,'extension_y_start_mm':y_start,'head_axis_gap_mm':gap,'wire_diameter_mm':8,'wire_exit_y_mm':-75,'stl':str(path.relative_to(ROOT)),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'watertight':mesh.is_watertight,'body_count':len(mesh.split(only_watertight=False)),'volume_mm3':float(mesh.volume),'head_top_overlap_mm3':overlap,'strength_sensitivity':s,'other_static_overlaps_mm3':contacts,'head_collision_and_strength_only_pass':overlap<.01 and all(c['worst']['sf']>=3 for c in s),'full_assembly_pass':False}
        rows.append(row);print(name,'SF',round(s[1]['worst']['sf'],3),'head',round(overlap,4),'other',contacts,flush=True)
    sources=[Path(__file__),ROOT/'hardware/src/config.py',ROOT/'hardware/src/make_chassis.py',ROOT/'hardware/stl/Head_Top_Eyecut.stl',ROOT/'tools/export_urdf.py',ROOT/'tools/kit_assembly.py',ROOT/'tools/data/kit_assembly_rear.json',ROOT/'tools/check_print_strength_sensitivity.py']
    result={'status':'CANDIDATES_NOT_READY_FOR_PRINT_OR_BASELINE_ADOPTION','frame':'pod_neck local; chassis z=local z+CHASSIS_T','source_sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},'baseline':{'head_top_overlap_mm3':max(0.,(baseline^head_m).volume()),'strength_sensitivity':strength(to_trimesh(baseline)),'other_static_overlaps_mm3':baseline_contact},'candidates':rows,'limitations':['full assembly still intersects TailJoint / Ball / Cabin Eye in existing KIT poses','base-width extension can become visible; appearance preservation not established','68MPa,600g,2g,Kt2.5 and homogeneous40% core are sensitivity assumptions, not physical proof','gap2mm candidates fail section strength; gap0.3mm is below existing2mm target','bolt/counterbore strength, cable bundle, print anisotropy and fatigue unverified']}
    (OUT/'comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print('Saved',OUT/'comparison.json')
if __name__=='__main__':main()
