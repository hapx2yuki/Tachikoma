#!/usr/bin/env python3
"""既存足を活かす支持案を比較する。候補 STL のみ監査フォルダに出力する。"""
import json
from pathlib import Path
import sys
import numpy as np
import trimesh
from manifold3d import Manifold, Mesh
from scipy import ndimage
from trimesh.voxel import ops
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'hardware/src'))
import config as C
import make_leg as L
from lib import box,cyl,to_trimesh
import check_toe_contact as T
import kit_assembly as K
from check_print_strength_sensitivity import section_moduli

def mani(tm):return Manifold(Mesh(np.asarray(tm.vertices,np.float32),np.asarray(tm.faces,np.uint32)))
def minimum(v,tilt):return (v@np.column_stack([-np.sin(tilt),np.zeros(len(tilt)),np.cos(tilt)]).T).min(axis=0)
def main():
    dest=ROOT/'docs/audits/20260905-round2/foot-support-candidates';dest.mkdir(parents=True,exist_ok=True)
    rendered_toe,pad,rigid=T.load_vertices();angles=[]
    placements=[p for p in K.load_placements() if p.part=='Leg_Toe_Black_x12']
    raw_original=trimesh.load(ROOT/'model/Leg_Toe_Black_x12.stl',force='mesh')
    root_raw=np.array([0.,raw_original.bounds[1,1],0.])
    production={}; transforms={}; input_scales={}
    for p in placements:
        scale=float(np.linalg.svd(p.matrix[:3,:3])[1].mean())
        rotation=p.matrix[:3,:3]/scale
        root=p.matrix[:3,:3]@root_raw+p.matrix[:3,3]
        tr=np.eye(4);tr[:3,:3]=rotation;tr[:3,3]=root-rotation@(root_raw*C.SCALE)
        tm=raw_original.copy();tm.apply_scale(C.SCALE);tm.apply_transform(tr)
        production[p.instance]=tm;transforms[p.instance]=tr;input_scales[p.instance]=scale
    toe={leg:trimesh.util.concatenate([m for n,m in production.items() if n.startswith(leg+'_')]).convex_hull.vertices for leg in T.G._LEGS}
    for height in (110,115,120,125,130):
        for command in [(0,0,0),*T.G.EVAL_CMDS]:
            for phase in np.arange(96)/96:
                for i in range(4):
                    a=T.G.leg_ik(*T.G.foot_target(i,phase,*command,body_h=height))
                    if a is not None: angles.append(a[1]+a[2])
    low,high=min(angles),max(angles); tilts=np.radians(np.linspace(low,high,1001))
    reference=toe['FR']; obstacle=np.vstack([reference,rigid]); rigid_tm=trimesh.load(ROOT/'hardware/stl/leg_foot_bored.stl',force='mesh')
    # 取付角だけ変える案。足/トゥ/パッド全部を同じ角度だけ回す。
    rotations=[]
    for delta in np.arange(-180,180.01,.25):
        r=tilts+np.radians(delta)
        gap=minimum(obstacle,r)-minimum(pad,r)
        rotations.append({'delta_deg':float(delta),'worst_clearance_mm':float(gap.min())})
    small=max((r for r in rotations if abs(r['delta_deg'])<=15),key=lambda r:r['worst_clearance_mm'])
    best=max(rotations,key=lambda r:r['worst_clearance_mm'])
    # 球面足裏は接地法線の変化を受け止めるが、従来足裏より大きく下へ出る。
    floor=L._foot_floor_z(L._load_kit_foot()); radius=15.
    center_z=float(np.min((minimum(obstacle,tilts)-2.+radius)/np.cos(tilts)))-.1
    sphere=Manifold.sphere(radius,96).translate([0,2.5,center_z])
    cone=Manifold.cylinder(floor-center_z,radius,4.,64,True).translate([0,2.5,(floor+center_z)/2])
    shaft=cyl(C.FOOT_PAD_POCKET_H+.3,C.FOOT_PAD_D).translate([0,2.5,floor+1.35])
    support=sphere+cone+shaft;sm=to_trimesh(support);sm.export(dest/'spherical_support_R15_candidate.stl')
    sphere_gap=minimum(obstacle,tilts)-minimum(sm.convex_hull.vertices,tilts)
    sphere_overlap=float((support^mani(rigid_tm)).volume())
    # toe の下半分だけを 1.5mm 包む TPU 靴。0.3mm ボクセルの近似外殻。
    raw=trimesh.load(ROOT/'model/Leg_Toe_Black_x12.stl',force='mesh');raw.apply_scale(C.SCALE)
    pitch=.3;vg=raw.voxelized(pitch).fill();a=np.pad(vg.matrix,7);origin=vg.translation-7*pitch
    outer=ndimage.distance_transform_edt(~a)*pitch<=1.5
    offset=ops.matrix_to_marching_cubes(outer,pitch);offset.apply_translation(origin)
    cover=(mani(offset)-mani(raw)) ^ box(100,100,40).translate([0,0,-20])
    fragments=sorted(cover.decompose(),key=lambda m:m.volume(),reverse=True)
    dropped=sum(m.volume() for m in fragments[1:])
    cover=fragments[0]
    ct=to_trimesh(cover);ct.export(dest/'toe_under_shoe_1p5_candidate.stl')
    parts=[p for p in K.by_link(K.load_placements(),'leg_foot_bored') if p.part=='Leg_Toe_Black_x12' and p.instance.startswith('FR_')]
    covers=[];toes=[];root_rows=[]
    for p in parts:
        transform=transforms[p.instance]
        m=ct.copy();m.apply_transform(transform)
        # 足の元意匠を保ち、追加靴側の根元だけを足に沿って逃がす。
        m=to_trimesh(mani(m)-mani(rigid_tm));covers.append(m)
        m.export(dest/(p.instance+'_shoe_fitted_candidate.stl'))
        t=production[p.instance];toes.append(t)
        iv=float((mani(t)^mani(rigid_tm)).volume())
        fitted_toe=to_trimesh(mani(t)-mani(rigid_tm))
        fitted_toe.export(dest/(p.instance+'_toe_hidden_seat_candidate.stl'))
        _, face_distance, _=trimesh.proximity.closest_point(rigid_tm,fitted_toe.triangles_center)
        contact_area=float(fitted_toe.area_faces[face_distance < .005].sum())
        # 接地力を最も低い toe 1本が受ける上限、実全身質量仮定3.8kg。
        sec=section_moduli(raw,10.,1,1.6,1.)
        worst=0.
        for angle in tilts[::10]:
            normal=np.array([-np.sin(angle),0,np.cos(angle)])
            v=t.vertices[np.argmin(t.vertices@normal)]
            r=transform[:3,:3].T@(v-transform[:3,3])-np.array([0,10.,0])
            force=transform[:3,:3].T@(normal*(.6*3.8*9.81))
            moment=np.cross(r,force)
            worst=max(worst,abs(moment[0])/sec[0]+abs(moment[2])/sec[1])
        root_rows.append({'toe':p.instance,'toe_foot_intersection_mm3':iv,
                          'hidden_seat_contact_area_mm2':contact_area,
                          'single_toe_average_contact_traction_mpa':float(.6*3.8*9.81/contact_area),
                          'root_y_mm':10.,'section_moduli_mm3':list(map(float,sec)),
                          'one_toe_bending_mpa_Kt1':float(worst),
                          'three_equal_toes_bending_mpa_Kt1':float(worst/3)})
    combined=trimesh.util.concatenate(covers)
    cover_gap=minimum(obstacle,tilts)-minimum(combined.convex_hull.vertices,tilts)
    overlap=float((mani(combined)^mani(rigid_tm)).volume())
    result={'status':'候補比較のみ。製作承認・全身IK再校正・接着/圧縮/疲労試験なし。',
      'frame':'leg_foot_bored local (sphere); toe raw STL scaled 1.5 (shoe)',
      'source_matrix_scale':input_scales,'evaluated_print_scale':C.SCALE,
      'scale_correction':'印刷仕様150%で評価。JSONが100%なら根元(原toe Y最大面中心)を固定して拡大。JSON自体は変更しない。',
      'reachable_tilt_deg':[low,high],'sampled_command_poses':len(angles),'continuous_tilt_samples':len(tilts),
      'mount_angle_change':{'best_within_15_deg':small,'best_all_360_deg':best,
                            'candidate_count':len(rotations),'limitation':'俯仰角のみ。全体が上下逆になる案も含めた比較。'},
      'spherical_support':{'radius_mm':radius,'center_local_mm':[0,2.5,center_z],
        'pad_depth_increase_mm':float(pad[:,2].min()-sm.bounds[0,2]),'worst_rigid_clearance_mm':float(sphere_gap.min()),
        'foot_intersection_mm3':sphere_overlap,'components':len(sm.split()),'watertight':bool(sm.is_watertight)},
      'toe_shoe':{'thickness_mm':1.5,'voxel_pitch_mm':pitch,'worst_rigid_clearance_mm':float(cover_gap.min()),
        'voxel_isolated_fragment_removed_mm3':float(dropped),
        'shoe_foot_intersection_mm3':overlap,'components_per_shoe':len(ct.split()),'watertight':bool(ct.is_watertight),
        'root_load_sensitivity':root_rows,
        'limitations':'toe下面の仮靴は装飾toeへ荷重を流す。原toe/足の隠れた座加工、接着強度、固定具、層方向は未検証。'} }
    (dest/'comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
