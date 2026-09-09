#!/usr/bin/env python3
"""OV3660候補の局所開口と全機体遮蔽を区別して実レイで記録。"""
import json,sys
from pathlib import Path
import numpy as np
import trimesh
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'tools'),str(ROOT/'hardware/src')]
import export_urdf as E
import make_camera as M
from sim_collision import parts_with_pad

def main():
    dest=ROOT/'docs/audits/20260905-round2/camera-ov3660-candidate'
    camera=json.loads((dest/'comparison.json').read_text());TC=np.array(camera['camera_frame_chassis'])
    p=M.pupil_center(M._normalized_cap())[0];u=M.pupil_axis();lens=TC[:3,:3]@(p-u*3.2)+TC[:3,3]
    world_axis=TC[:3,:3]@u;side=np.array([1.,0,0]);up=np.cross(world_axis,side);directions=[]
    for radius in np.linspace(0,1,9):
        for az in np.linspace(0,2*np.pi,72,endpoint=False):
            theta=np.radians(51)*radius
            directions.append(world_axis*np.cos(theta)+(side*np.cos(az)+up*np.sin(az))*np.sin(theta))
    directions=np.array(directions)
    candidates=[]
    for name in ('eye_pod_camera_shell_ov3660_candidate','camera_carrier_ov3660_candidate','OV3660_sensor_envelope'):
        m=trimesh.load(dest/(name+'.stl'),force='mesh');m.apply_transform(TC);candidates.append((name,m))
    for name in ('camera_base_with_xiao_cradle_candidate','XIAO_envelope_head_candidate','OV3660_FPC_bent_candidate'):
        candidates.append((name,trimesh.load(dest/(name+'.stl'),force='mesh')))
    def trace(named):
        mesh=trimesh.util.concatenate([m for _,m in named]);names=np.concatenate([np.repeat(n,len(m.faces)) for n,m in named])
        locations,rays,faces=mesh.ray.intersects_location(np.tile(lens,(len(directions),1)),directions,multiple_hits=True)
        distances=np.linalg.norm(locations-lens,axis=1);mask=(distances>.1)&(distances<=400)
        first={}
        for ray,face,distance in zip(rays[mask],faces[mask],distances[mask]):
            if int(ray) not in first or distance<first[int(ray)]['distance_mm']:
                first[int(ray)]={'part':str(names[face]),'distance_mm':float(distance)}
        counts={n:sum(h['part']==n for h in first.values()) for n in sorted(set(h['part'] for h in first.values()))}
        return {'rays':len(directions),'occluded_rays':len(first),'nearest_hit_by_part':counts,'first_hits':first}
    local=trace(candidates);rows=[];parts=parts_with_pad(True)
    poses=json.loads((ROOT/'docs/audits/20260905-round2/simulation/self-collision-with-servos.json').read_text())['poses']
    for pose in poses:
        named=list(candidates)
        for link,items in parts.items():
            if link in ('eye_pod_camera','camera_optical_frame'):continue
            frame=E.LINK_PARENT_FRAME[link](pose['angles_deg'])
            for mesh,_,name in items:
                if name.startswith(('eye_pod_camera','camera_carrier')):continue
                m=mesh.copy();m.apply_transform(frame);m.apply_translation([0,0,-E.ZB]);named.append((name,m))
        result=trace(named);rows.append({'pose':pose['name'],**result});print(pose['name'],result['occluded_rays'],result['nearest_hit_by_part'],flush=True)
    result={'status':'LOCAL_APERTURE_PASS' if local['occluded_rays']==0 else 'LOCAL_APERTURE_FAIL',
      'lens_world_mm':lens.tolist(),'axis_world':world_axis.tolist(),'pupil_diameter_mm':10,'setback_mm':3.2,
      'test':'対角102度の円形円錐を9半径×72方位で検査。画像矩形より保守的な円錐。0.1〜400mmの全交点を調べる。',
      'local_camera_assembly':local,'full_robot':rows,
      'interpretation':'自部品による機体遮蔽と開口のケラレを分けて記録。648本は離散サンプルであり連続全画素の保証ではない。'}
    (dest/'full-fov.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
if __name__=='__main__':main()
