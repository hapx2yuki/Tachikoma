#!/usr/bin/env python3
"""シャーシ7穴の実接続先と内寄せ候補の占有を調べる。製造データを変更しない。"""
import json,sys
from pathlib import Path
import numpy as np
import trimesh
from manifold3d import Manifold,Mesh
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'tools'),str(ROOT/'hardware/src')]
import export_urdf as E
from sim_collision import parts_with_pad
from lib import rbox,cyl,to_trimesh

def native(m):return Manifold(Mesh(np.asarray(m.vertices,dtype=np.float32),np.asarray(m.faces,dtype=np.uint32)))

def main():
    out=ROOT/'docs/audits/20260905-round2/head-attachment-candidates';out.mkdir(parents=True,exist_ok=True)
    parts=parts_with_pad(True);head=next(m.copy() for m,_,n in parts['base_link'] if n.startswith('Head_Top_Eyecut'))
    head.apply_translation([0,0,-E.ZB]);hn=native(head)
    case=[(n,native(m.copy().apply_translation([0,0,-E.ZB]))) for m,_,n in parts['base_link'] if n.endswith('_servo_case')]
    poses=json.loads((ROOT/'docs/audits/20260905-round2/simulation/self-collision-with-servos.json').read_text())['poses']
    moving=[]
    for pose in poses:
        for leg in E.LEGS:
            for m,_,name in parts[f'leg_{leg.lower()}_femur']:
                if name=='thigh_cap' or 'Thigh_Guard' in name:
                    mm=m.copy();mm.apply_transform(E.leg_pitch_frame(leg,pose['angles_deg']));mm.apply_translation([0,0,-E.ZB])
                    moving.append((pose['name'],leg,name,native(mm)))
    rows=[]
    for radius,center_y in [(r,0.) for r in (78,70,62,54)]+[(r,E.C.ARM_MOUNT_HUB_Y) for r in (62,54,46)]:
        for angle in (90,30,150,210,330,245,295):
            xy=radius*np.array([np.cos(np.radians(angle)),np.sin(np.radians(angle))])
            xy[1]+=center_y
            tab=rbox(16,16,E.C.CHASSIS_T,r=5).translate([*xy,E.C.CHASSIS_T/2])-cyl(E.C.CHASSIS_T+2,3.2).translate([*xy,E.C.CHASSIS_T/2])
            if radius==78 and center_y==0 and angle==90:
                h=E.C.MOUTH_FRONT_TAB_BOSS_H
                tab+= (cyl(h,10)-cyl(h+2,3.2)).translate([*xy,E.C.CHASSIS_T+h/2])
            tm=to_trimesh(tab);loc,_,_=head.ray.intersects_location([[*xy,E.C.CHASSIS_T]],[[0,0,1]],multiple_hits=True)
            distances=sorted(float(z-E.C.CHASSIS_T) for z in loc[:,2] if z>E.C.CHASSIS_T)
            pillar=None if not distances else cyl(distances[0]+.5,6).translate([*xy,E.C.CHASSIS_T+(distances[0]+.5)/2])
            pts,_=trimesh.sample.sample_surface(tm,1500,seed=31);_,nearest,_=trimesh.proximity.closest_point(head,pts)
            hits=[{'pose':pose,'leg':leg,'part':name,'intersection_mm3':float((tab^mesh).volume())} for pose,leg,name,mesh in moving]
            hits=[r for r in hits if r['intersection_mm3']>.01]
            rows.append({'radius_mm':radius,'circle_center_y_mm':center_y,'angle_deg':angle,'hole_xy_mm':xy.tolist(),
                'vertical_hole_axis_head_crossings_above_plate_mm':distances,
                'tab_head_intersection_mm3':float((tab^hn).volume()),
                'sampled_tab_surface_to_head_min_mm':float(nearest.min()),
                'servo_case_intersections_mm3':{n:float((tab^m).volume()) for n,m in case if (tab^m).volume()>.01},
                'vertical_support_d6_case_intersections_mm3':None if pillar is None else {n:float((pillar^m).volume()) for n,m in case if (pillar^m).volume()>.01},
                'moving_part_intersections':hits})
    current=[r for r in rows if r['radius_mm']==78]
    # cap外面そのものが現在タブ内にあるかを独立に調べる。
    raw=E.load('thigh_cap');pts,fi=trimesh.sample.sample_surface(raw,20000,seed=41)
    chassis=next(m for m,_,n in parts['base_link'] if n=='chassis');surface=[]
    for leg in E.LEGS:
        F=E.leg_pitch_frame(leg,poses[0]['angles_deg'])@E.trans(E.C.FEMUR_LEN/2-8,0,13.1)
        ob=chassis.copy();ob.apply_transform(np.linalg.inv(F));inside=ob.contains(pts)
        cap=raw.copy();cap.apply_transform(F);inter=trimesh.boolean.intersection([cap,chassis],engine='manifold')
        radius=np.linalg.norm(inter.vertices[:,:2],axis=1)
        surface.append({'leg':leg,'surface_samples_inside_chassis':int(inside.sum()),
            'upward_surface_samples_inside_chassis':int(sum(raw.face_normals[fi[inside],2]>.1)),
            'intersection_mm3':float(inter.volume),'intersection_radial_range_mm':[float(radius.min()),float(radius.max())]})
    result={'status':'FAIL: 既存の頭固定穴は7本とも頭殻へ向かう軸線外。移設/支持構造の再設計が必要',
      'method':'全原材Boolean/垂直軸レイ/面距離サンプル/18実出力姿勢。現90度タブは口逃げ後の3mm増しカラーを含む。候補半径は穴位置だけを比較し、本体円板やケース穴を再設計したとは扱わない。',
      'current_holes_with_vertical_head_path':sum(bool(r['vertical_hole_axis_head_crossings_above_plate_mm']) for r in current),
      'cap_visible_surface_collision':surface,'candidates':rows,
      'limitations':['r内寄せだけでも本体円板r72の前脚交差が残る。','垂直軸が殻を通る候補でも、支持柱/ねじ座/ドライバー/外面非貫通保持は未設計。','入力18姿勢外の全到達集合は別検査。','表面サンプル最小距離は厳密な全三角形最短距離ではない。']}
    (out/'comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    for r in current:print('r78',r['angle_deg'],'head_axis',r['vertical_hole_axis_head_crossings_above_plate_mm'],'near',r['sampled_tab_surface_to_head_min_mm'])
    print('cap',surface)
if __name__=='__main__':main()
