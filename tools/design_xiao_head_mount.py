#!/usr/bin/env python3
"""公式 STEP 包絡と短い FPC を同時に扱う XIAO 頭内配置候補探索。"""
import json,sys
from pathlib import Path
import numpy as np
import trimesh
from manifold3d import Manifold,Mesh
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'tools'),str(ROOT/'hardware/src')]
import export_urdf as E
from sim_collision import parts_with_pad
from lib import box,to_trimesh

def native(mesh):
    return Manifold(Mesh(np.asarray(mesh.vertices,dtype=np.float32),np.asarray(mesh.faces,dtype=np.uint32)))

def main():
    dest=ROOT/'docs/audits/20260905-round2/camera-ov3660-candidate'
    step=ROOT/'docs/audits/20260905-round2/primary-sources/xiao-step-measured'
    measured=json.loads((step/'camera-removed-summary.json').read_text())
    data=measured['camera_removed_without_sd'];dims=np.array(data['centered_flat_dimensions_mm'])
    camera=json.loads((dest/'comparison.json').read_text());Tcamera=np.array(camera['camera_frame_chassis'])
    obstacles=[]
    for link,items in parts_with_pad(True).items():
        if link not in ('base_link','eye_r_pod','eye_l_pod'):continue
        for m,_,name in items:
            if name.startswith(('eye_pod_camera','camera_carrier')):continue
            m=m.copy();m.apply_transform(E.LINK_PARENT_FRAME[link]({}));m.apply_translation([0,0,-E.ZB])
            obstacles.append((name,native(m),m.bounds))
    for path in dest.glob('*candidate.stl'):
        if path.name.startswith(('eye_pod_camera_','camera_carrier_')):
            m=trimesh.load(path,force='mesh');m.apply_transform(Tcamera)
            obstacles.append((path.stem,native(m),m.bounds))
    for name in ('OV3660_sensor_envelope','OV3660_FPC_straight_envelope'):
        if name.endswith('straight_envelope'):continue # 曲げ配線を別途設計するため直線仮置きは障害物ではない
        m=trimesh.load(dest/(name+'.stl'),force='mesh');m.apply_transform(Tcamera)
        obstacles.append((name,native(m),m.bounds))
    # FPC はセンサーの -Y 端から出る。レンズ座標は design_ov3660_camera と同じ。
    import make_camera as MC
    pupil=MC.pupil_center(MC._normalized_cap())[0]
    marker=to_trimesh(MC._rotated(box(.01,.01,.01).translate([0,-4,-8.35]),pupil));marker.apply_transform(Tcamera)
    flex_start=marker.bounds.mean(axis=0)
    source_to_flat=np.array(data['source_to_centered_flat_matrix'])
    connector=np.array(measured['fpc_connector']['center_mm']+[1.])
    connector_flat=(source_to_flat@connector)[:3]
    # 入口の位置/符号は実物で未確認。コネクタ外接箱のカメラ側面を入口候補とし、
    # 入口が中心より3mm前後にずれる両方を保存する。
    rows=[];feasible=[]
    for pitch in (-45,-30,-15,0,15,30,45):
        rot=E.rot(pitch,'x')@E.rot(-90,'z')
        for y in np.arange(38,59.01,1.):
            for z in np.arange(10,32.01,1.):
                T=E.trans(0,y,z)@rot
                body=box(*dims).transform(T[:3,:]);b=np.array(body.bounding_box()).reshape(2,3)
                con=(T@np.r_[connector_flat,1])[:3]
                axis=T[:3,:3]@np.array([1.,0,0])
                openings=[con+s*3*axis for s in (-1,1)]
                lengths=[float(np.linalg.norm(p-flex_start)) for p in openings]
                if min(lengths)>9.2:continue
                hits={name:float((body^m).volume()) for name,m,bb in obstacles
                      if np.all(b[1]>bb[0]) and np.all(bb[1]>b[0])}
                hits={k:v for k,v in hits.items() if v>.01}
                row={'center_mm':[0,float(y),float(z)],'pitch_deg':pitch,
                     'frame_flat_to_chassis':T.tolist(),'connector_center_mm':con.tolist(),
                     'opening_candidates_mm':[p.tolist() for p in openings],
                     'straight_line_from_flex_start_mm':lengths,'intersections_mm3':hits,
                     'total_intersection_mm3':sum(hits.values())}
                rows.append(row)
                if not hits:feasible.append(row)
    rows.sort(key=lambda r:(r['total_intersection_mm3'],min(r['straight_line_from_flex_start_mm'])))
    base_only=[r for r in rows if r['intersections_mm3'] and all(n=='eye_pod_camera_base_ov3660_candidate' for n in r['intersections_mm3'])]
    base_only.sort(key=lambda r:min(r['straight_line_from_flex_start_mm']))
    result={'status':'CANDIDATE SEARCH ONLY: FPC入口の向き/曲率、固定具、全20サーボ同時収納は未確定',
      'source':'公式2023 STEP。USB含む/SD無し/カメラ無しの閉直方体包絡。追加コネクタ/アンテナ配線なし',
      'dimensions_mm':dims.tolist(),'free_flex_length_mm':9.2,'flex_start_chassis_mm':flex_start.tolist(),
      'envelope_clearance_mm':0,'grid':{'center_y_mm':[38,59,1],'center_z_mm':[10,32,1],'pitch_deg':[-45,-30,-15,0,15,30,45]},
      'positions_within_flex_straight_bound':len(rows),'no_material_intersections':len(feasible),
      'best':rows[:20], 'internal_base_relief_candidates':base_only[:20],
      'limitation':'直線距離は可否の必要条件。曲げ/接点4mm挿入/抜差し/保持具が未確認なので、これだけで合格にしない。'}
    if feasible:
        best=min(feasible,key=lambda r:min(r['straight_line_from_flex_start_mm']))
        T=np.array(best['frame_flat_to_chassis']);body=to_trimesh(box(*dims).transform(T[:3,:]));body.export(dest/'XIAO_envelope_head_candidate.stl')
        result['selected_for_further_design']=best
    (dest/'xiao-placement-search.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
