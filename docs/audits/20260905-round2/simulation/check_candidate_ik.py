from pathlib import Path
import sys,json,math
import numpy as np
ROOT=Path.cwd();sys.path[:0]=[str(ROOT/'tools'),str(ROOT/'hardware/src')]
import sim_physics as S
import sim_gait as G
rows=[]
files=list(Path('docs/audits/20260905-round2/simulation').glob('yaw-pack-*.json'))
for file in files+[None]:
 if file is None:
  j={'candidates':[{'parameters_xf_yf_thetaf_xr_yr_thetar':[*G.ORIGIN[0],0,*G.ORIGIN[3],0]}]}
 else:
  j=json.loads(file.read_text())
 for ci,candidate in enumerate(j['candidates']):
  xf,yf,tf,xr,yr,tr=candidate['parameters_xf_yf_thetaf_xr_yr_thetar']
  hips=np.array([[xf,yf],[-xf,yf],[-xr,yr],[xr,yr]])
  for h in [105,110,115,120,125,130]:
   fail=[];angles=[];maxerr=0;samples=0
   for cmd in [(0,0,0),(0,1,0),(0,-1,0),(1,0,0),(-1,0,0),(0,0,1),(0,0,-1)]:
    for ph in ([0.] if cmd==(0,0,0) else np.arange(0,1,.05)):
     for leg in range(4):
      old=np.array(G.foot_target(leg,ph,*cmd,h,holding=cmd==(0,0,0)));mnt=G.MOUNT[leg];R=np.array([[math.cos(mnt),-math.sin(mnt)],[math.sin(mnt),math.cos(mnt)]])
      world=R@old[:2]+G.ORIGIN[leg];new=R.T@(world-hips[leg]);a=G.leg_ik(*new,old[2]);samples+=1
      if a is None:fail.append([cmd,float(ph),G._LEGS[leg]])
      elif cmd==(0,0,0) and ph==0:angles.append([G._LEGS[leg],list(a)])
   rows.append({'source':file.name if file else 'ORIGINAL_BASELINE','candidate':ci,'hip_height_mm':h,'required_foot_world':'original firmware foot targets in body XY; no projection of new target; original link lengths and joint limits; holding phase fixed zero; yaw pair guard not yet applied','samples':samples,'failed':len(fail),'first_failures':fail[:8],'standing_angles_deg':angles})
Path('docs/audits/20260905-round2/simulation/yaw-candidate-ik.json').write_text(json.dumps(rows,indent=2))
for r in rows:
 if r['hip_height_mm']==115:print(r['source'],r['candidate'],r['failed'],'/',r['samples'],'fail; standing',len(r['standing_angles_deg']),'/4')
