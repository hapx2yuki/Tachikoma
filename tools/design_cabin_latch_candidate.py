#!/usr/bin/env python3
"""既存Cabinペグの背面半分を弾性ラッチへ置き換える検討用モデル。

現行STLは変更しない。Frontにだけペグを接着し、Backは両斜面の爪で着脱する。
梁理論は材料定数の感度計算。実印刷の保持力/繰返し寿命を認定しない。
"""
import hashlib,json,sys
from pathlib import Path
import numpy as np
import trimesh
from manifold3d import Manifold
from design_cabin_electronics import ROOT,OUT,source,native,bbox,box,to_trimesh,rear_crossover,export_part,MESH_TOLERANCE_MM
LENGTH=15.0
THICKNESS=1.6
WIDTH=7.0
ENGAGEMENT=0.6
MAX_DEFLECTION=0.85
TOP=9.305259704589844

def prism_x(x0,x1,yz):
    return Manifold.hull_points([[x,y,z] for x in [x0,x1] for y,z in yz])

def deflection(x,delta):
    s=np.clip(26-abs(x),0,LENGTH)
    return delta*s*s*(3*LENGTH-s)/(2*LENGTH**3)

def flex_beam(sign,delta):
    # 0.5mm分割の断面をつなぐ。固定端～自由端のEuler-Bernoulli曲線。
    result=Manifold()
    xs=np.linspace(11,27,33)
    for a,b in zip(xs,xs[1:]):
        pts=[]
        for x in [a,b]:
            dz=deflection(x,delta)
            for y in [-8,-1]:
                for z in [TOP-THICKNESS,TOP]:pts.append([sign*x,y,z-dz])
        result+=Manifold.hull_points(pts)
    return result

def setup_peg(name,entry,back):
    trans=np.eye(4)
    if name=='upper':trans=trimesh.transformations.rotation_matrix(np.pi/2,[0,1,0])
    trans[:3,3]=entry['position_normalized_scaled_mm']
    shell=back.copy();shell.apply_transform(np.linalg.inv(trans))
    local=source('Cabin_Peg_x2',axes=[('x',90)])
    original=native(local);base=original;teeth=[];roof_values=[]
    for sign in [-1,1]:
        lo,hi=sorted([sign*10.6,sign*26])
        base-=bbox([lo,-8.6,6.2],[hi,-.4,11.5])
        # 爪全幅/両端で実ソケット面を測り、最も低い面から0.6mm噛ませる。
        origins=np.array([[sign*x,y,0] for x in [11,11.7,12.4] for y in [-8,-6,-4,-2]])
        hit,idx,_=shell.ray.intersects_location(origins,np.tile([0,0,1],(len(origins),1)),multiple_hits=True)
        roof=[float(min(hit[idx==i,2])) for i in range(len(origins))]
        roof_values.append(roof);peak=min(roof)+ENGAGEMENT;rise=peak-TOP
        y0=-7.8;y1=y0+rise/np.tan(np.radians(30));y2=-4.5;y3=y2+rise/np.tan(np.radians(60))
        assert y1<y2 and y3<-1, (y1,y2,y3)
        tooth=prism_x(*sorted([sign*11,sign*12.4]),[(y0,TOP-.05),(y0,TOP),(y1,peak),(y2,peak),(y3,TOP),(y3,TOP-.05)])
        teeth.append(tooth)
    def shape(delta):
        part=base
        for sign,tooth in zip([-1,1],teeth):
            moved=tooth.warp(lambda p: (p[0],p[1],p[2]-deflection(p[0],delta)))
            part+=flex_beam(sign,delta)+moved
        return part
    return {'name':name,'transform':trans,'original':original,'base':base,'teeth':teeth,'shape':shape,'roof':roof_values}

def placed(m,trans):return m.transform(trans[:3,:])
def main():
    OUT.mkdir(parents=True,exist_ok=True)
    entries=json.loads((ROOT/'tools/data/cabin_peg_alignment_candidate.json').read_text())['pegs']
    front=trimesh.load(OUT/'candidate_cabin_front_with_wire_route.stl',force='mesh')
    back=source('Cabin_Back_Blue_Repaired');original_back=native(back)
    back_before=original_back-rear_crossover();cap=back_before
    pegs=[setup_peg(e['name'],e,back) for e in entries]
    recess=Manifold()
    for peg in pegs:
        for tooth in peg['teeth']:recess+=placed(tooth.minkowski_sum(Manifold.sphere(.2,24)),peg['transform'])
    cap-=recess
    export_part(cap,'candidate_cabin_back_with_latches')
    result={'status':'DESIGN_CANDIDATE_NOT_PRINT_RELEASE','frame':'chassis plate bottom z=0','mesh_simplification_tolerance_mm':MESH_TOLERANCE_MM,'source_sha256':{},'design':{'beam_active_length_mm':LENGTH,'beam_width_mm':WIDTH,'beam_thickness_mm':THICKNESS,'nominal_engagement_mm':ENGAGEMENT,'insertion_cam_deg':30,'release_cam_deg':60,'recess_clearance_mm':.2,'maximum_geometry_deflection_mm':MAX_DEFLECTION,'peg_front_half':'original unchanged; bond only into Front','rear_cap_removal':'straight rearward pull; no outside holes or tools'},'checks':{},'pegs':[]}
    for path in [Path(__file__),ROOT/'tools/design_cabin_electronics.py',ROOT/'hardware/src/config.py',ROOT/'hardware/src/lib.py',ROOT/'tools/kit_assembly.py',ROOT/'tools/data/cabin_peg_alignment_candidate.json',ROOT/'model/Cabin_Peg_x2.stl',ROOT/'model/Cabin_Back_Blue_Repaired.stl',OUT/'candidate_cabin_front_with_wire_route.stl',OUT/'candidate_cabin_back_crossover.stl']:
        result['source_sha256'][str(path.relative_to(ROOT))]=hashlib.sha256(path.read_bytes()).hexdigest()
    result['checks']['back_added_outside_original_mm3']=max(0.,(cap-original_back).volume())
    result['checks']['detent_removed_mm3']=back_before.volume()-cap.volume()
    result['checks']['back_watertight']=to_trimesh(cap).is_watertight
    result['checks']['back_body_count']=len(to_trimesh(cap).split(only_watertight=False))
    result['checks']['back_remaining_volume_mm3']=cap.volume()
    # 切削面の0.2mm余裕を含めても既存外表面まで3.2mmを維持。
    # 既存ペグ穴は元々空なので、この検査では外表面のみを独立レイで確認する。
    result['checks']['cap_front_collision_mm3']=max(0.,(cap^native(front)).volume())
    max_required=0
    for peg in pegs:
        shape=peg['shape'];trans=peg['transform'];relaxed=shape(0);compressed=shape(MAX_DEFLECTION)
        world=placed(relaxed,trans);world_compressed=placed(compressed,trans)
        name=peg['name'];tm=to_trimesh(world)
        export_part(world,f'candidate_latch_peg_{name}')
        export_part(world_compressed,f'candidate_latch_peg_{name}_deflected')
        # 動作中は蓋だけ後方へ動く。全押下形状を連続20mm移動する蓋と交差判定。
        cap_sweep=cap.minkowski_sum(box(.001,20,.001).translate([0,-10,0]))
        continuous=max(0.,(world_compressed^cap_sweep).volume())
        closed=max(0.,(world^cap).volume())
        fixed_front=bbox([-50,0,-30],[50,20,30])
        front_change=((relaxed-peg['original'])^fixed_front).volume()+((peg['original']-relaxed)^fixed_front).volume()
        record={'name':name,'transform':trans.tolist(),'socket_roof_measurements_mm':peg['roof'],'watertight':tm.is_watertight,'body_count':len(tm.split(only_watertight=False)),'volume_mm3':world.volume(),'front_half_changed_mm3':max(0.,front_change),'closed_cap_overlap_mm3':closed,'front_overlap_mm3':max(0.,(world^native(front)).volume()),'fully_deflected_continuous_travel_overlap_mm3':continuous,'deflected_front_overlap_mm3':max(0.,(world_compressed^native(front)).volume()),'travel_samples':[]}
        # 0.25mm刻みの途中姿勢で、交差が解消される最小変形を二分探索。
        for distance in np.arange(0,12.001,.25):
            moving_cap=cap.translate([0,-float(distance),0]);lo,hi=0.,MAX_DEFLECTION
            uncompressed=max(0.,(world^moving_cap).volume())
            if uncompressed<=1e-5:hi=0
            else:
                for _ in range(9):
                    mid=(lo+hi)/2
                    if (placed(shape(mid),trans)^moving_cap).volume()>1e-5:lo=mid
                    else:hi=mid
            max_required=max(max_required,hi)
            record['travel_samples'].append({'cap_rear_shift_mm':float(distance),'required_tip_deflection_mm':hi,'relaxed_overlap_mm3':uncompressed})
        record['maximum_required_tip_deflection_mm']=max(x['required_tip_deflection_mm'] for x in record['travel_samples'])
        result['pegs'].append(record)
        print(name, {k:v for k,v in record.items() if k not in ['travel_samples','socket_roof_measurements_mm','transform']},flush=True)
    # 小変形の片持ち梁。Eは印刷材の認定値でなく800〜2000 MPaの感度範囲。
    result['analytical_estimate']={'model':'Euler-Bernoulli cantilever, point load at free end, friction omitted, all four beams engaged; not a force measurement','max_required_deflection_mm':max_required,'max_surface_strain_fraction':3*THICKNESS*max_required/(2*LENGTH**2),'modulus_sensitivity_MPa':[800,1200,1600,2000],'estimates':[],'UNVERIFIED':['actual printed modulus and layer direction','notch stress concentration','friction and dimensional error','fatigue and creep','minimum retention force under walking shock','adhesive bond on Front half']}
    for elastic_modulus in [800,1200,1600,2000]:
        force=elastic_modulus*WIDTH*THICKNESS**3*max_required/(4*LENGTH**3)
        result['analytical_estimate']['estimates'].append({'E_MPa':elastic_modulus,'radial_N_each':force,'insertion_N_four':4*force*np.tan(np.radians(30)),'release_N_four':4*force*np.tan(np.radians(60))})
    (OUT/'latch-report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ['pegs','source_sha256']},indent=2,ensure_ascii=False),flush=True)
if __name__=='__main__':main()
