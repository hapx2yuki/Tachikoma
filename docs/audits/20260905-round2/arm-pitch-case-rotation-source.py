"""2026-09-05有限比較の再現用。標準CADの生成/修正はしない。"""
import sys,json,pathlib,numpy as np,trimesh
R=pathlib.Path('/Users/uratayuuki/Documents/Tachikoma');sys.path[:0]=[str(R/'tools'),str(R/'hardware/src')]
import export_urdf as E
from sim_collision import parts_with_pad
from mesh_checks import intersection_volume_mm3
A=R/'docs/audits/20260905-round2'; old=json.loads((A/'simulation/all-pairs-native-20servos-before-head-sync.json').read_text())
parts=parts_with_pad(True); p=E.C.ARM_SERVO; frame=E.arm_servo_frames('r')['pitch']; rows=[]
for angle in range(0,360,30):
 shape=trimesh.creation.box([p['L'],p['W'],p['TAB_BELOW']]);shape.apply_transform(frame@E.rot(angle,'z')@E.trans(-(p['L']/2-p['SHAFT_OFF']),0,-p['TAB_BELOW']/2))
 maxima={}
 for pose in old['poses']:
  q=pose['angles_deg'];a=shape.copy();a.apply_transform(E.LINK_PARENT_FRAME['arm_r_shoulder'](q))
  for link,items in parts.items():
   for m,_,name in items:
    if name=='arm_r_pitch_servo_case':continue
    b=m.copy();b.apply_transform(E.LINK_PARENT_FRAME[link](q))
    if np.any(a.bounds[1]<=b.bounds[0]) or np.any(b.bounds[1]<=a.bounds[0]):continue
    v=intersection_volume_mm3(a,b)
    if v>.01 and v>maxima.get(name,{}).get('intersection_mm3',0):maxima[name]={'pose':pose['name'],'intersection_mm3':v,'link':link}
 row={'native_axis_case_rotation_deg':angle,'maximum_intersections':maxima};rows.append(row);print(angle,maxima,flush=True)
(A/'arm-pitch-case-rotation-candidates.json').write_text(json.dumps({'method':'肩ピッチ出力軸/軸間長を保持し主ケースを軸周り30度刻み12候補×18姿勢で検査。既存支持枠を含む全パーツ。ケース方向変更は新支持枠が必須。改修STLや成立証明ではない。','poses_source':'simulation/all-pairs-native-20servos-before-head-sync.json (angles only; meshes and placements are current)','rows':rows},ensure_ascii=False,indent=2))
