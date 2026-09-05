#!/usr/bin/env python3
"""口の固定部品対シャーシの実体、切削域、支持部の材料保持を検査する。"""
import argparse,json,sys
from pathlib import Path
import numpy as np
import trimesh
from manifold3d import Manifold,Mesh
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'hardware/src'),str(ROOT/'tools')]
import config as C
import make_chassis as CH
import make_audio as A
from lib import box,cyl,to_trimesh
from mesh_checks import intersection_volume_mm3

def native(m):return Manifold(Mesh(np.asarray(m.vertices,dtype=np.float32),np.asarray(m.faces,dtype=np.uint32)))
def run(source):
    original_cutter=CH.mouth_clearance
    try:
        CH.mouth_clearance=lambda:Manifold()
        before=CH.chassis()
    finally:CH.mouth_clearance=original_cutter
    after=CH.chassis() if source else native(trimesh.load(ROOT/'hardware/stl/chassis.stl',force='mesh'))
    mesh=to_trimesh(after);removed=before-after
    frames={}
    for stem,fn,offset in [('Mouth_Ball_Bored',A.mouth_ball_bored,C.MOUTH_BALL_LOCAL_Y),
                          ('Mouth_Neck_Bored',A.mouth_neck_bored,C.MOUTH_NECK_LOCAL_Y),
                          ('Mouth_Cap_Grey',lambda:A._load('Mouth_Cap_Grey'),C.MOUTH_CAP_LOCAL_Y)]:
        part=fn() if source or stem=='Mouth_Cap_Grey' else native(trimesh.load(ROOT/'hardware/stl'/f'{stem}.stl',force='mesh'))
        part=part.translate([0,offset,0]).rotate([C.MOUTH_CANNON_ROT_X_DEG,0,0]).translate(C.MOUTH_CANNON_T)
        frames[stem]=to_trimesh(part)
    zones={}
    for tag,points,d in [('pod',CH.POD_BOLTS,10),('battery',CH.CRADLE_BOLTS,10)]:
        for i,(x,y) in enumerate(points):zones[f'{tag}_{i}']=cyl(20,d).translate([x,y,4])
    p=C.YAW_SERVO;cx=p['L']/2-p['SHAFT_OFF']
    for leg,(x,y) in C.HIPS.items():
        a=np.radians(CH.CASE_ANG[leg]);ca,sa=np.cos(a),np.sin(a)
        for i,hx in enumerate((-cx-p['HOLE_PITCH']/2,-cx+p['HOLE_PITCH']/2)):
            for j,hy in enumerate((-p['HOLE_SPREAD']/2,p['HOLE_SPREAD']/2)):
                zones[f'{leg}_yaw_boss_{i}_{j}']=cyl(20,8).translate([x+hx*ca-hy*sa,y+hx*sa+hy*ca,4])
    p=C.ARM_SERVO;cx=p['L']/2-p['SHAFT_OFF']
    for s in (-1,1):
        for i,hy in enumerate((-cx-p['HOLE_PITCH']/2,-cx+p['HOLE_PITCH']/2)):
            x,y=s*C.ARM_MOUNT_XY[0],C.ARM_MOUNT_XY[1]+hy
            zones[f'arm_{s}_{i}']=box(12,12,20).translate([x,y,4])
    for s in (-1,1):
        for t in (-1,1):zones[f'PCA_{s}_{t}']=cyl(20,6).translate([s*C.PCA9685_HOLES[1]/2,C.PCA_STACK_Y0+t*C.PCA9685_HOLES[0]/2,4])
    for angle in (90,30,150,210,330,245,295):
        a=np.radians(angle);zones[f'head_tab_{angle}']=cyl(20,10).translate([78*np.cos(a),78*np.sin(a),4])
    losses={name:float((removed^zone).volume()) for name,zone in zones.items()}
    # 下面の損失を隠さず記録し、前タブには上面カラーを含む有効厚5mmの
    # 完全なφ10/φ3.2リングが残ることを直接検査する。
    collar_top=C.CHASSIS_T+C.MOUTH_FRONT_TAB_BOSS_H
    ring=cyl(collar_top-2,10).translate([0,78,(collar_top+2)/2])-cyl(20,3.2).translate([0,78,4])
    ring_missing=float((ring-after).volume())
    # 新カラーと頭/主要ケースの実体も照合。
    from sim_collision import parts_with_pad
    import export_urdf as E
    collar=cyl(C.MOUTH_FRONT_TAB_BOSS_H,10).translate([0,78,C.CHASSIS_T+C.MOUTH_FRONT_TAB_BOSS_H/2])
    collar_hits={}
    for m,_,name in parts_with_pad(True)['base_link']:
        if name.startswith('Head_Top') or name.endswith('_servo_case'):
            mm=m.copy();mm.apply_translation([0,0,-E.ZB]);collar_hits[name]=float((collar^native(mm)).volume())
    removed_mesh=to_trimesh(removed)
    # 包囲箱間距離は領域間距離の保守的な下限。柱の上下の頂点だけの
    # 最近接距離では、途中の面接触を落とすので使わない。
    distances={}
    if not removed.is_empty():
        for name,zone in zones.items():
            if name.startswith(('head_tab','pod','battery','arm')):
                bb=np.array(zone.bounding_box()).reshape(2,3);rb=removed_mesh.bounds
                distances[name]=float(np.linalg.norm(np.maximum(np.maximum(bb[0]-rb[1],rb[0]-bb[1]),0)))
    overlap={name:intersection_volume_mm3(mesh,part) for name,part in frames.items()}
    insertion=[]
    for distance in np.linspace(0,30,121):
        hits={}
        for name,part in frames.items():
            shifted=part.copy();shifted.apply_translation([0,0,-distance])
            hits[name]=intersection_volume_mm3(mesh,shifted)
        insertion.append({'below_final_mm':float(distance),'intersections_mm3':hits})
    result={'mode':'source' if source else 'STL','watertight':bool(mesh.is_watertight),'components':len(mesh.split()),
      'before_volume_mm3':float(before.volume()),'after_volume_mm3':float(after.volume()),'removed_mm3':float(removed.volume()),
      'removed_bounds_mm':None if removed.is_empty() else removed.bounding_box(),'mouth_intersection_mm3':overlap,
      'protected_support_material_loss_mm3':losses,'distance_lower_bound_removed_to_protected_zone_mm':distances,
      'head_tab_protected_radius_mm':5,'head_tab_hole_radius_mm':1.6,
      'front_tab_reinforcement':{'ring_z_mm':[2,collar_top],'ring_radial_wall_mm':3.4,'missing_ring_material_mm3':ring_missing,'new_collar_intersections_mm3':collar_hits,
        'fastener_note':'前タブの板厚4→7mm。既存ねじ長さに+3mmが必要。頭への固定構造そのものは別途設計する。'},
      'bottom_insertion':{'samples':len(insertion),'maximum_intersection_mm3':max(v for row in insertion for v in row['intersections_mm3'].values()),'rows':insertion},
      'removed_outside_mouth_clearance_mm3':float((removed-original_cutter()).volume()),
      'limitations':'保護円柱/箱の実材料不変と1閉体を確認。全板の曲げ/穴縁疲労/新頭支持柱は未検証。現在7タブは頭に届かない別の未解消問題がある。'}
    result['pass']=bool(mesh.is_watertight and len(mesh.split())==1 and all(v<.01 for v in overlap.values())
      and all(v<.01 for name,v in losses.items() if name!='head_tab_90') and ring_missing<.01
      and all(v<.01 for v in collar_hits.values()) and result['removed_outside_mouth_clearance_mm3']<.01
      and result['bottom_insertion']['maximum_intersection_mm3']<.01)
    return result
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',action='store_true');p.add_argument('--json',type=Path);args=p.parse_args()
    r=run(args.source)
    if args.json:args.json.parent.mkdir(parents=True,exist_ok=True);args.json.write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(r,ensure_ascii=False,indent=2));sys.exit(0 if r['pass'] else 1)
