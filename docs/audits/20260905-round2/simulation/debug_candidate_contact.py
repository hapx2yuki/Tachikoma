from pathlib import Path
import sys,json,numpy as np,mujoco
sys.path.insert(0,'tools')
import sim_stress as T,sim_physics as S,export_urdf as E
B=Path('docs/audits/20260905-round2/simulation')
rows=[]
for candidate in [None,'docs/audits/20260905-round2/foot-support-candidates']:
 m,idx=S.build_model(.6,{'leg':24,'arm':.8,'eye':.05},{'leg':.4,'arm':.03,'eye':.005},contact_model='vhacd',foot_candidate_dir=candidate)
 case={'segments':[{'name':'hold','duration':.5}]};native=T.native_output_trace(case,True)
 d=mujoco.MjData(m);mujoco.mj_setConst(m,d)
 aids=np.array([idx['aid'][n] for n in S.ALL_JOINTS]);jids=np.array([idx['jid'][n] for n in S.ALL_JOINTS]);qa=m.jnt_qposadr[jids];da=m.jnt_dofadr[jids]
 d.qpos[:7]=[0,0,.115,1,0,0,0];d.qpos[qa]=np.radians(native[0,3:23]);d.ctrl[aids]=d.qpos[qa];T.place_on_ground(m,d,0,0)
 mins={}
 for gi in range(m.ngeom):
  if not m.geom_bodyid[gi] or not m.geom_contype[gi] or m.geom_type[gi]!=mujoco.mjtGeom.mjGEOM_MESH:continue
  name=mujoco.mj_id2name(m,mujoco.mjtObj.mjOBJ_GEOM,gi);meta=idx['part_metadata'][name];key=meta['link']+'/'+meta['part'];mi=m.geom_dataid[gi];s=m.mesh_vertadr[mi];n=m.mesh_vertnum[mi]
  points=m.mesh_vert[s:s+n]@d.geom_xmat[gi].reshape(3,3).T+d.geom_xpos[gi];mins[key]=min(mins.get(key,1.),float(points[:,2].min()))
 stall=m.actuator_forcerange[aids].copy();speed=np.array([idx['velocity_limits'][n] for n in S.ALL_JOINTS]);trace=[]
 for k in range(250):
  if k%10==0:d.ctrl[aids]=np.radians(native[1+k//10,3:23])
  m.actuator_forcerange[aids]=S.speed_torque_ranges(stall,d.qvel[da],speed);mujoco.mj_step(m,d)
  if k%10==0:
   mujoco.mj_forward(m,d);cs=[]
   for ci,c in enumerate(d.contact[:d.ncon]):
    if not m.geom_bodyid[c.geom1]:gi=c.geom2
    elif not m.geom_bodyid[c.geom2]:gi=c.geom1
    else:continue
    name=mujoco.mj_id2name(m,mujoco.mjtObj.mjOBJ_GEOM,gi);f=np.zeros(6);mujoco.mj_contactForce(m,d,ci,f);meta=idx['part_metadata'][name]
    cs.append({'part':meta,'depth_mm':c.dist*1000,'normal_force_N':f[0]})
   trace.append({'time':float(d.time),'angles_deg':np.degrees(d.qpos[qa]).tolist(),'target_deg':np.degrees(d.ctrl[aids]).tolist(),'base':d.qpos[:7].tolist(),'contacts':cs})
 rows.append({'candidate':candidate,'initial_lowest_parts_mm':[(n,v*1000) for n,v in sorted(mins.items(),key=lambda x:x[1])[:18]],'trace':trace})
(B/'debug-candidate-contact.json').write_text(json.dumps(rows,indent=2))
print([(r['candidate'],r['initial_lowest_parts_mm'][:4],r['trace'][5]['angles_deg'][:3]) for r in rows])
