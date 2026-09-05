#!/usr/bin/env python3
"""OV3660用の不可視内部キャリア候補。元の瞳径10mm/光軸/印刷品を変更しない。"""
import json
from pathlib import Path
import sys
import numpy as np
import trimesh
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'hardware/src'))
import config as C
import make_camera as M
from lib import box,to_trimesh
from mesh_checks import intersection_volume_mm3

def main():
    dest=ROOT/'docs/audits/20260905-round2/camera-ov3660-candidate';dest.mkdir(parents=True,exist_ok=True)
    before={n:getattr(C,n) for n in ['CAM2_MODULE_L','CAM2_MODULE_W','CAM2_MODULE_T','CAM2_LENS_STANDOFF','CAM2_WIRE_NOTCH']}
    # このプロセス内だけの候補値。恒久config.pyへは書き込まない。
    C.CAM2_MODULE_L=C.CAM2_MODULE_W=8.;C.CAM2_MODULE_T=5.3
    # 元の瞳中心には浅い凹みがあり、周辺の外面が中心の基準面より前に
    # 出る。平面式の上限4.05mmだけでなく実レイを通すため3.2mmを採る。
    C.CAM2_LENS_STANDOFF=3.2;C.CAM2_WIRE_NOTCH=(7.5,4.)
    shell_all=M.eye_pod_camera_shell();pieces=sorted(shell_all.decompose(),key=lambda m:m.volume(),reverse=True)
    # 分割リングの内部に孤立した残片が出る。候補では除去を明記して保存する。
    assert all(p.bounding_box()[5]<C.EYE_NECK_H+2 for p in pieces[1:])
    shell=pieces[0];base=M.eye_pod_camera_base();carrier=M.camera_carrier()
    p_outer=M.pupil_center(M._normalized_cap())[0];u=M.pupil_axis();L=C.CAM2_LENS_STANDOFF
    module=M._rotated(box(8.,8.,5.3).translate([0,0,-L-5.3/2]),p_outer)
    fpc=M._rotated(box(6.5,13.2,.3).translate([0,-10.6,-L-5.3+.15])+
                   box(12.5,4.,.3).translate([0,-15.2,-L-5.3+.15]),p_outer)
    named={'eye_pod_camera_shell_ov3660_candidate':shell,'eye_pod_camera_base_ov3660_candidate':base,
           'camera_carrier_ov3660_candidate':carrier,'OV3660_sensor_envelope':module,'OV3660_FPC_straight_envelope':fpc}
    meshes={n:to_trimesh(m) for n,m in named.items()}
    for n,m in meshes.items():m.export(dest/(n+'.stl'))
    stationary=trimesh.util.concatenate([meshes[n] for n in list(meshes)[:3]])
    lens=p_outer-u*L;dirs=[]
    e1=np.array([1.,0,0]);e2=np.cross(u,e1)
    for radius in np.linspace(0,1,9):
        for az in np.linspace(0,2*np.pi,72,endpoint=False):
            th=np.radians(51)*radius;dirs.append(u*np.cos(th)+(e1*np.cos(az)+e2*np.sin(az))*np.sin(th))
    dirs=np.array(dirs);locations,ir,_=stationary.ray.intersects_location(np.tile(lens,(len(dirs),1)),dirs,multiple_hits=True)
    distances=np.linalg.norm(locations-lens,axis=1);hit=np.unique(ir[distances>.1])
    # 外面の追加加工は瞳の既存φ10mm内だけか、元キャップの表面サンプルで確認。
    raw=M._normalized_cap();pts,fi=trimesh.sample.sample_surface(raw,25000,seed=44)
    rel=pts-p_outer;rad=np.linalg.norm(rel-np.outer(rel@u,u),axis=1)
    keep=(rad>C.CAM2_PUPIL_D/2+.1)&(pts[:,2]>C.EYE_NECK_H+2.5)
    _,distance,_=trimesh.proximity.closest_point(trimesh.util.concatenate([meshes[n] for n in list(meshes)[:2]]),pts[keep])
    # 中央目のシャーシ座標。以後、本体基板とコネクタ位置の候補に使う。
    ctr,n=C.EYE_SOCKETS_150[1];n=np.array(n);T=np.eye(4)
    T[:3,:3]=np.diag([-1,-1,1])@M.install_rotation(n)
    T[:3,3]=np.diag([-1,-1,1])@(np.array(ctr)-n*(C.EYE_SOCKET_FLOOR-C.EYE_HOVER+C.EYE_NECK_H))+[0,C.ARM_MOUNT_HUB_Y,57.7]
    result={'status':'CANDIDATE: センサー現物/メイン基板取付/曲げFPC/全身FOVは別途検証',
      'source_url':'https://files.seeedstudio.com/wiki/SeeedStudio-XIAO-ESP32S3/new-res/OV3660_Camera_Module_Specification.pdf',
      'confirmed_on':'2026-09-05','manufacturer_dimensions_mm':{'sensor':[8,8,5.3],'total_length_with_FPC':21.2,'contact_width':12.5,'flex_width':6.5},
      'optics':{'diagonal_fov_deg':102,'horizontal_fov_deg':85,'efl_mm':1.63,'unchanged_pupil_diameter_mm':C.CAM2_PUPIL_D,
                'candidate_setback_mm':L,'required_pupil_diameter_mm':float(2*L*np.tan(np.radians(51))),
                'mechanical_rays':len(dirs),'occluded_rays':len(hit),'visible_surface_max_change_mm':float(distance.max())},
      'baseline_config':before,'candidate_config':{n:getattr(C,n) for n in before},
      'camera_frame_chassis':T.tolist(),'removed_internal_ring_fragment_mm3':sum(p.volume() for p in pieces[1:]),
      'parts':{n:{'components':len(m.split()),'watertight':bool(m.is_watertight),'volume_mm3':float(m.volume)} for n,m in meshes.items()},
      'intersections_mm3':{f'{a}__{b}':intersection_volume_mm3(meshes[a],meshes[b])
                          for a,b in [(list(meshes)[0],list(meshes)[1]),(list(meshes)[0],list(meshes)[2]),(list(meshes)[1],list(meshes)[2])]},
      'sensor_material_intersection_mm3':intersection_volume_mm3(meshes['OV3660_sensor_envelope'],stationary),
      'straight_FPC_material_intersection_mm3':intersection_volume_mm3(meshes['OV3660_FPC_straight_envelope'],stationary)}
    result['geometry_pass']=bool(len(hit)==0 and distance.max()<.05 and all(v<.05 for v in result['intersections_mm3'].values())
                                and result['sensor_material_intersection_mm3']<.05 and result['straight_FPC_material_intersection_mm3']<.05)
    (dest/'comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result['geometry_pass'] else 1
if __name__=='__main__':sys.exit(main())
