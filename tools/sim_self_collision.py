#!/usr/bin/env python3
"""リンク凸包の接触を実部品のブーリアン交差と照合する。接触の除外はしない。"""
import json,sys,itertools
from pathlib import Path
import numpy as np
import trimesh,mujoco
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
import sim_physics as S
import export_urdf as E
from sim_stress import self_contacts, native_output_trace


def audit(out, include_servos=False):
    hashes=S.input_fingerprints()
    from sim_collision import parts_with_pad
    parts=parts_with_pad(include_servos)
    fixed_intersections=[];fixed_errors=[]
    if include_servos:
        for (a,_,n1),(b,_,n2) in itertools.combinations(parts['base_link'],2):
            if not any(n.endswith('_servo_case') or n.startswith('eye_carrier#') for n in (n1,n2)):continue
            if np.any(a.bounds[1]<=b.bounds[0]) or np.any(b.bounds[1]<=a.bounds[0]):continue
            try:
                cut=trimesh.boolean.intersection([E._ensure_outward(a),E._ensure_outward(b)],engine='manifold',check_volume=False)
                volume=abs(float(cut.volume))
                if not np.isfinite(volume):raise ValueError('Boolean volume is non-finite')
                if volume>.01:fixed_intersections.append({'part1':n1,'part2':n2,'intersection_mm3':volume,'bounds_mm':cut.bounds.tolist()})
            except Exception as ex:fixed_errors.append({'parts':[n1,n2],'error':str(ex)})
    m,idx=S.build_model(1.,{'leg':24,'arm':.8,'eye':.05},{'leg':.4,'arm':.03,'eye':.005},self_collision=True,include_parent_collision=True,contact_model='parts' if include_servos else 'linked-hulls',include_servo_collision=include_servos)
    rows=[]
    native=native_output_trace({'segments':[{'name':'holding','duration':2.},
        {'name':'walk','duration':4.8,'vy':1.}]})
    for name,phase,holding,zero in [('holding',0,True,False),('zero',0,True,True)]+[(f'walk_{i:02d}',i/16,False,False) for i in range(16)]:
        if holding:sample=99
        else:
            candidates=np.arange(180,len(native))
            delta=abs((native[candidates,0]-phase+.5)%1-.5)
            sample=int(candidates[np.argmin(delta)])
        q=dict(zip(S.ALL_JOINTS,np.radians(native[sample,3:23])))
        d=mujoco.MjData(m)
        if zero:
            q.update({n:0 for n in S.ALL_LEG_JOINTS})
            for side in ('r','l'):q.update({f'arm_{side}_yaw':0,f'arm_{side}_pitch':0,f'arm_{side}_elbow':np.pi/4})
        for n,v in q.items():d.qpos[m.jnt_qposadr[idx['jid'][n]]]=v
        d.qpos[2]=.3;mujoco.mj_forward(m,d);contacts=self_contacts(m,d)
        qdeg={n:float(np.degrees(v)) for n,v in q.items()};world={}
        for link,items in parts.items():
            frame=E.LINK_PARENT_FRAME[link](qdeg)
            world[link]=[]
            for mesh,_,part in items:
                mm=E._ensure_outward(mesh.copy());mm.apply_transform(frame);world[link].append((part,mm))
        pose={'name':name,'angles_deg':qdeg,'native_phase':float(native[sample,0]),
              'pose_source':'ideal mechanical neutral before calibrated output' if zero else 'actual C++ Gait+LegOutput+Arms+Servos with PWM quantization',
              'pairs':[]}
        candidates=set(contacts)
        bounds={link:np.array([np.min([m.bounds[0] for _,m in items],axis=0),np.max([m.bounds[1] for _,m in items],axis=0)]) for link,items in world.items() if items}
        for l1,l2 in itertools.combinations(bounds,2):
            if np.all(bounds[l1][1]>bounds[l2][0]) and np.all(bounds[l2][1]>bounds[l1][0]):candidates.add('|'.join(sorted([l1,l2])))
        for pair in sorted(candidates):
            depth=contacts.get(pair)
            l1,l2=pair.split('|');collisions=[];errors=[];tested=0
            for (n1,a),(n2,b) in itertools.product(world[l1],world[l2]):
                if np.any(a.bounds[1]<=b.bounds[0]) or np.any(b.bounds[1]<=a.bounds[0]):continue
                tested+=1
                try:
                    mm=trimesh.boolean.intersection([a,b],engine='manifold',check_volume=False)
                    vol=abs(float(mm.volume))
                    if not np.isfinite(vol):raise ValueError('Boolean volume is non-finite')
                    if vol>.01:collisions.append({'part1':n1,'part2':n2,'intersection_mm3':vol,'intersection_bounds_mm':mm.bounds.tolist()})
                except Exception as ex:errors.append({'parts':[n1,n2],'error':str(ex)})
            classification='ACTUAL_MESH_INTERSECTION' if collisions else 'BOOLEAN_UNRESOLVED' if errors else 'CONVEX_HULL_FALSE_CONTACT' if depth is not None else 'NO_INTERSECTION'
            pose['pairs'].append({'links':[l1,l2],'hull_penetration_mm':depth*1000 if depth is not None else None,'bbox_candidate_pairs':tested,'classification':classification,'actual_intersections':collisions,'errors':errors})
        rows.append(pose);print(name,len(contacts),'hull pairs',sum(bool(r['actual_intersections']) for r in pose['pairs']),'real intersecting pairs',flush=True)
        Path(out).write_text(json.dumps({'input_sha256':hashes,'inputs_unchanged':hashes==S.input_fingerprints(),'method':'All link pairs with intersecting world AABBs plus MuJoCo hull contacts including parent pairs, exact source part Boolean manifold; volumes <=0.01mm3 suppressed as numerical tolerance. Fixed-base servo cases and eye carriers are also checked against every same-base part; these contacts cannot exert forces on one rigid MuJoCo body. Fixed shell-shell assembly is covered by mechanical audit. Intersections require fit-vs-collision interpretation, not automatic removal.','fixed_base_intersections':fixed_intersections,'fixed_base_boolean_errors':fixed_errors,'poses':rows},ensure_ascii=False,indent=2))
    return rows

if __name__=='__main__':
    import argparse
    a=argparse.ArgumentParser();a.add_argument('--out',type=Path,required=True);a.add_argument('--include-servos',action='store_true');args=a.parse_args();audit(args.out,args.include_servos)
