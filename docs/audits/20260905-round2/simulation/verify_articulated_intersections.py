import sys,json
from pathlib import Path
import numpy as np,trimesh
ROOT=Path.cwd();sys.path[:0]=[str(ROOT/'tools'),str(ROOT/'hardware/src')]
import config as C
from make_visuals import trans,rot,load
from sim_stress import native_output_trace
# URDFやexportのフレームを使わず、make_armの軸定義から直接組み立てる。
pa=C.ARM_SERVO;pz=2.5+pa['HORN_HUB_H']-2.;pitch_dn=pz+2.5+(pa['W']/2+2.5)-.1
upper=load('upper_arm');fore=load('forearm');bracket=load('shoulder_bracket');elbow=load('elbow_shell');elbow.apply_transform(trans(C.UPPER_ARM_LEN,0,0))
def vol(a,b,T):
 b=b.copy();b.apply_transform(T);m=trimesh.boolean.intersection([a,b],engine='manifold',check_volume=False);return abs(float(m.volume))
rows=[]
for angle in range(0,96):
 rows.append({'angle_deg':angle,'shoulder_upper_mm3':vol(bracket,upper,trans(20,0,-pitch_dn)@rot(angle,'y')),'upper_forearm_mm3':vol(upper,fore,trans(C.UPPER_ARM_LEN,0,0)@rot(angle,'y')),'elbow_shell_forearm_mm3':vol(elbow,fore,trans(C.UPPER_ARM_LEN,0,0)@rot(angle,'y'))})
trace=native_output_trace({'segments':[{'duration':2,'name':'hold'}]})
result={'method':'raw hardware/stl STL + make_arm axis definition, no URDF/export frame reuse','rows':rows,'native_steady_output_deg':trace[-1,3:23].tolist(),'native_enabled':trace[-1,23:].tolist()}
Path('docs/audits/20260905-round2/simulation/arm-independent-intersections.json').write_text(json.dumps(result,indent=2))
print('native arms',trace[-1,15:21]);print([r for r in rows if r['angle_deg'] in [0,30,40,45,85,95]])
