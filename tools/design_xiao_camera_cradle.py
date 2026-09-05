#!/usr/bin/env python3
"""OV3660 の短FPCとXIAO保持台を備えた内部候補。既定STLは変更しない。"""
import json,sys
from pathlib import Path
import numpy as np
import trimesh
from manifold3d import Manifold,Mesh
from scipy.optimize import brentq
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'tools'),str(ROOT/'hardware/src')]
import export_urdf as E
from sim_collision import parts_with_pad
from lib import box,to_trimesh

def native(m):return Manifold(Mesh(np.asarray(m.vertices,dtype=np.float32),np.asarray(m.faces,dtype=np.uint32)))
def ribbon(points,width,thickness):
    tangent=np.gradient(points,axis=0);tangent/=np.linalg.norm(tangent,axis=1)[:,None]
    side=np.tile([1.,0,0],(len(points),1));normal=np.cross(side,tangent)
    vertices=np.vstack([p+s*width/2*x+t*thickness/2*n for p,x,n in zip(points,side,normal) for s,t in [(-1,-1),(1,-1),(1,1),(-1,1)]])
    faces=[]
    for i in range(len(points)-1):
        for j in range(4):
            a=4*i+j;b=4*i+(j+1)%4;c=b+4;d=a+4;faces.extend([[a,b,c],[a,c,d]])
    faces.extend([[0,2,1],[0,3,2]]);k=4*(len(points)-1);faces.extend([[k,k+1,k+2],[k,k+2,k+3]])
    mesh=trimesh.Trimesh(vertices,faces,process=True);mesh.fix_normals();return mesh

def main():
    dest=ROOT/'docs/audits/20260905-round2/camera-ov3660-candidate'
    data=json.loads((dest/'xiao-placement-search.json').read_text());camera=json.loads((dest/'comparison.json').read_text())
    selected=next(r for r in data['internal_base_relief_candidates'] if r['center_mm']==[0,45.,23.] and r['pitch_deg']==0)
    T=np.array(selected['frame_flat_to_chassis']);TC=np.array(camera['camera_frame_chassis']);dims=np.array(data['dimensions_mm'])
    start=np.array(data['flex_start_chassis_mm']);end=np.array(selected['opening_candidates_mm'][0]);end[0]=0
    # 2023コネクタの外側面を仮の入口にする。現品の入口/挿入方向は未確定。
    d0=np.array([0.,.14933585,-.98878653]);d1=np.array([0.,-1,0]);t=np.linspace(0,1,301)[:,None]
    def curve(length):
        return (1-t)**3*start+3*(1-t)**2*t*(start+d0*length)+3*(1-t)*t**2*(end-d1*length)+t**3*end
    def length(points):return float(np.linalg.norm(np.diff(points,axis=0),axis=1).sum())
    control=brentq(lambda a:length(curve(a))-9.2,.001,20);points=curve(control)
    derivative=np.gradient(points,axis=0);second=np.gradient(derivative,axis=0)
    k=np.linalg.norm(np.cross(derivative,second),axis=1)/np.linalg.norm(derivative,axis=1)**3
    cable=ribbon(points,6.5,.3);cable_clear=native(ribbon(points,7.1,.9))
    body=box(*dims).transform(T[:3,:]);body_clear=box(*(dims+.6)).transform(T[:3,:])
    base_mesh=trimesh.load(dest/'eye_pod_camera_base_ov3660_candidate.stl',force='mesh');base_mesh.apply_transform(TC)
    base=native(base_mesh);cleared=base-body_clear-cable_clear
    # 絶縁トレー + 両側レール。部品を包む包絡に片側0.3mmの余裕を取る。
    # USB側/SD側は開け、別の非導電ストラップで上方向の抜けを止める候補。
    L,W,H=dims;floor=box(L+.6,W+3.,1.2).translate([0,0,-H/2-.3-.6])
    rails=Manifold()
    for sy in (-1,1):
        rail=box(L+.6,1.2,H+1.5).translate([0,sy*(W/2+.3+.6),-.75])
        # 固定用ストラップの通し穴(幅3mm×厚1mm)。
        for x in (-L/3,L/3):rail-=box(3,4,1).translate([x,sy*(W/2+.9),-H/2-.15])
        rails+=rail
    # 頭内壁に沿う前側角を逃がす。基板端の約3mmはトレーから張り出すが、
    # 残り約20mmを底で支え、前側コネクタの操作空間も確保する。
    tray=(floor+rails)-box(40,40,40).translate([-L/2-20+3.5,0,0])
    tray=tray.transform(T[:3,:]);candidate=(cleared+tray)-body_clear-cable_clear
    named={'camera_base_with_xiao_cradle_candidate':candidate,'XIAO_envelope_head_candidate':body,'OV3660_FPC_bent_candidate':native(cable)}
    meshes={name:to_trimesh(m) for name,m in named.items()}
    for name,m in meshes.items():m.export(dest/(name+'.stl'))
    obstacles=[]
    for link,items in parts_with_pad(True).items():
        if link not in ('base_link','eye_r_pod','eye_l_pod'):continue
        for mesh,_,name in items:
            if name.startswith(('eye_pod_camera','camera_carrier')):continue
            m=mesh.copy();m.apply_transform(E.LINK_PARENT_FRAME[link]({}));m.apply_translation([0,0,-E.ZB]);obstacles.append((name,native(m)))
    for name in ('eye_pod_camera_shell_ov3660_candidate','camera_carrier_ov3660_candidate','OV3660_sensor_envelope'):
        m=trimesh.load(dest/(name+'.stl'),force='mesh');m.apply_transform(TC);obstacles.append((name,native(m)))
    hits={}
    for name,m in named.items():
        hits[name]={other:float((m^ob).volume()) for other,ob in obstacles if (m^ob).volume()>.01}
    insertion=[]
    for distance in np.linspace(0,35,141):
        shifted=body.translate([0,-distance,0])
        insertion.append({'withdraw_back_mm':float(distance),'cradle_intersection_mm3':float((shifted^candidate).volume())})
    # base背面の削り込みが見えるcap部分に達するかを、元camera座標で範囲確認。
    removed=to_trimesh(base-cleared);removed.apply_transform(np.linalg.inv(TC))
    result={'status':'CANDIDATE ONLY: 現物FPC入口/曲率/基板revision/ストラップ/装着順未検証',
      'coordinate_frame':'全てchassis座標。従来camera候補5STLはcamera局所座標なので混同しない。',
      'selected':selected,'body_clearance_each_side_mm':.3,'flex_curve_length_mm':length(points),
      'minimum_curve_radius_mm':float(1/k[2:-2].max()),'flex_curve_control_length_mm':float(control),
      'flex_start_mm':start.tolist(),'assumed_connector_opening_mm':end.tolist(),
      'base_removed_mm3':float((base-cleared).volume()),'base_removed_bounds_camera_local_mm':removed.bounds.tolist(),
      'parts':{name:{'watertight':bool(m.is_watertight),'components':len(m.split()),'volume_mm3':float(m.volume)} for name,m in meshes.items()},
      'intersections_mm3':hits,'candidate_board_intersection_mm3':float((candidate^body).volume()),
      'candidate_cable_intersection_mm3':float((candidate^native(cable)).volume()),
      'board_insertion':{'method':'FPCを外した基板包絡をchassis -Yへ35mm引き出す連続逆挿入。0.25mm刻み。頭の蓋を外した整備工程を想定。',
        'samples':len(insertion),'maximum_cradle_intersection_mm3':max(r['cradle_intersection_mm3'] for r in insertion),
        'rows':insertion},
      'limitations':['21.2mm全長のうちセンサー8mm/接点4mmを除く9.2mmで曲線を構成。許容曲率はメーカー未確認。',
        '全厚包絡トレーは下面を支持するが、基板端/はんだ部の荷重許容と非導電ストラップ装着は現物確認が必要。',
        'Wi-Fiアンテナ/IPXケーブル/USBプラグの常設包絡は未追加。USBを使う整備時は頭部を開ける。',
        '本体基板の収納ができても、同じ頭内の20サーボ干渉は別問題として残る。']}
    result['geometry_pass']=bool(all(not h for h in hits.values()) and len(meshes['camera_base_with_xiao_cradle_candidate'].split())==1
      and result['candidate_board_intersection_mm3']<.01 and result['candidate_cable_intersection_mm3']<.01)
    (dest/'xiao-cradle-comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
