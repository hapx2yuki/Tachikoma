#!/usr/bin/env python3
"""根元ブラケット全ヨー範囲の保守的な平面包絡検査。単位mm。"""
import argparse,hashlib,json,re,sys
from pathlib import Path
import numpy as np
import shapely
from shapely.geometry import MultiPoint
from shapely.affinity import affine_transform
import trimesh
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'hardware/src'),str(ROOT/'tools')]
import config as C,make_leg as L
from lib import to_trimesh,horn_pocket,cyl,servo_pocket,servo_tab_holes


def run(source=False, step=.1):
    fw=(ROOT/'firmware/src/config.h').read_text()
    def value(n):return float(re.search(r'constexpr float\s+'+n+r'\s*=\s*([-\d.]+)f',fw).group(1))
    lim,inner,total=value('LIM_YAW'),value('LIM_YAW_IN'),value('LIM_YAW_IN_SUM')
    mesh=to_trimesh(L.coxa_bracket()) if source else trimesh.load(ROOT/'hardware/stl/coxa_bracket.stl',force='mesh')
    hull=MultiPoint(mesh.vertices[:,:2]).convex_hull
    # 全3D実体を含むXY凸包の距離が正なら、Zの違いによらず非干渉。
    radius=float(np.linalg.norm(mesh.vertices[:,:2],axis=1).max())
    angles=np.linspace(-lim,lim,int(np.ceil(2*lim/step))+1)
    actual_step=float(angles[1]-angles[0])
    sweeps={}
    for name in ('FR','FL','RL','RR'):
        flip=-1 if name in ('FR','RL') else 1
        polys=[]
        for angle in angles:
            theta=np.radians(C.LEG_ANGLES[name]+angle);cs,sn=np.cos(theta),np.sin(theta)
            polys.append(affine_transform(hull,[cs,-sn*flip,sn,cs*flip,*C.HIPS[name]]))
        sweeps[name]=np.array(polys,dtype=object)
    match=re.search(r'constexpr int YAW_IN_SIGN\[4\]\s*=\s*\{([^}]+)\}',fw)
    signs=[int(v.strip()) for v in match.group(1).split(',')]
    if len(signs)!=4 or any(abs(v)!=1 for v in signs):raise ValueError('YAW_IN_SIGNが不正')
    inner_sign=dict(zip(('FR','FL','RL','RR'),signs))
    rows=[]
    for a,b in (('FR','RR'),('FL','RL'),('FR','FL'),('RL','RR'),('FR','RL'),('FL','RR')):
        aa,bb=np.meshgrid(angles,angles,indexing='ij')
        valid=(aa*inner_sign[a]<=inner+actual_step)&(bb*inner_sign[b]<=inner+actual_step)
        if (a,b) in [('FR','RR'),('FL','RL')]:
            valid&=(np.maximum(aa*inner_sign[a],0)+np.maximum(bb*inner_sign[b],0)<=total+2*actual_step)
        # 最寄り格子が制限境界の外側でも候補に含めるためmaskをstep分広げる。
        # 個別角は全LIMまで広く取り、後脚pod側制限は緩めた包絡で検査する。
        distances=shapely.distance(sweeps[a][:,None],sweeps[b][None,:])
        distances=np.where(valid,distances,np.inf)
        where=np.unravel_index(np.argmin(distances),distances.shape)
        distance=float(distances[where])
        # 格子の中間姿勢へ両物体が最大半stepずつ動く時の距離減少上界。
        rounding_bound=4*radius*np.sin(np.radians(actual_step)/4)
        continuous=distance-rounding_bound
        rows.append({'pair':[a,b],'tested_angle_pairs':int(valid.sum()),'worst_angles_deg':[float(aa[where]),float(bb[where])],
                     'sampled_convex_hull_gap_mm':distance,'between_samples_motion_bound_mm':float(rounding_bound),
                     'continuous_gap_lower_bound_mm':float(continuous),'pass':bool(continuous>=.1)})
    # 元の箱枠とホーン周囲r=10mmを失っていないことを実体差で確認する。
    from manifold3d import Manifold,Mesh
    current=Manifold(Mesh(np.asarray(mesh.vertices,np.float32),np.asarray(mesh.faces,np.uint32)))
    frame=L.servo_frame().translate([C.COXA_LEN,0,0])
    frame-=horn_pocket(C.YAW_SERVO).translate([0,0,L.COXA_TOP])
    frame_lost=float((frame-current).volume())
    plate_h=L.COXA_TOP-L.FRAME_TOP+6
    # 旧天板に確実に含まれていた軸周囲r10の荷重伝達面。ホーン負形状は除く。
    horn_seat=cyl(plate_h,20).translate([0,0,L.COXA_TOP-plate_h/2])
    horn_seat-=horn_pocket(C.YAW_SERVO).translate([0,0,L.COXA_TOP])
    # ケースと天板が体積を共有していた旧「保持座」は物理的に存在できない。
    # 必要なサーボ負形状を除いた座の材料保持を検査。除去量も明示する。
    pocket=(servo_pocket(C.LEG_SERVO)+servo_tab_holes(C.LEG_SERVO)).rotate([-90,0,0]).translate([C.COXA_LEN,0,0])
    old_seat_volume = horn_seat.volume()
    horn_seat -= pocket
    pocket_seat_removed = old_seat_volume - horn_seat.volume()
    horn_lost=float((horn_seat-current).volume())
    mirror=to_trimesh(L.coxa_bracket().mirror([0,1,0])) if source else trimesh.load(ROOT/'hardware/stl/coxa_bracket_m.stl',force='mesh')
    mirror_native=Manifold(Mesh(np.asarray(mirror.vertices,np.float32),np.asarray(mirror.faces,np.uint32)))
    expected_mirror=current.mirror([0,1,0])
    mirror_solid_difference=float((mirror_native-expected_mirror).volume()+(expected_mirror-mirror_native).volume())
    shape_ok=mesh.is_watertight and len(mesh.split(only_watertight=False))==1
    from scipy.spatial import cKDTree
    expected=mesh.vertices*np.array([1,-1,1])
    mirror_error=max(float(cKDTree(mirror.vertices).query(expected)[0].max()),float(cKDTree(expected).query(mirror.vertices)[0].max()))
    mirror_ok=mirror_error<.01 and mirror.is_watertight and mirror.volume>0 and mirror_solid_difference<.001
    inputs=[Path(__file__),ROOT/'hardware/src/config.py',ROOT/'hardware/src/lib.py',ROOT/'hardware/src/make_leg.py',ROOT/'firmware/src/config.h']
    if not source:inputs.extend([ROOT/'hardware/stl/coxa_bracket.stl',ROOT/'hardware/stl/coxa_bracket_m.stl'])
    hashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
    return {'input_sha256':hashes,'mirror_max_vertex_error_mm':mirror_error,'mirror_pass':bool(mirror_ok),'mode':'source' if source else 'STL','method':'XY凸包の全角度組合せと格子間最大回転量による保守的距離下界。ケース/他の部品は別検査。',
            'step_deg':actual_step,'radius_mm':radius,'pairs':rows,'frame_material_lost_mm3':frame_lost,'horn_seat_lost_mm3':horn_lost,'required_servo_pocket_removed_from_old_seat_mm3':float(pocket_seat_removed),'mirror_symmetric_difference_mm3':mirror_solid_difference,
            'watertight_single_body':bool(shape_ok),'pass':bool(all(x['pass'] for x in rows) and frame_lost<.001 and horn_lost<.001 and shape_ok and mirror_ok)}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',action='store_true');p.add_argument('--json',type=Path);a=p.parse_args()
    result=run(a.source)
    if a.json:a.json.parent.mkdir(parents=True,exist_ok=True);a.json.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False,indent=2));return 0 if result['pass'] else 1

if __name__=='__main__':sys.exit(main())
