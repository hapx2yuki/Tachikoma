#!/usr/bin/env python3
"""候補STLを再読し、設計コードの集計から独立して嵌合/経路を検査する。

実物適合、印刷強度、ラッチ保持力は認定しない。既存STLは書換えない。
"""
import hashlib,json
import numpy as np
import trimesh
from pathlib import Path
from design_cabin_electronics import ROOT,OUT,source,native,bbox,box,to_trimesh
TOL=.1 # STL float32 roundtripの微小差を除く体積許容。外壁保証は別の3.2mm膨張検査。
def load(name):return trimesh.load(OUT/(name+'.stl'),force='mesh')
def vol(m):return max(0.,m.volume())
def main():
 reports={name:json.loads((OUT/(name+'-report.json')).read_text()) for name in ['candidate','latch']}
 evidence={'status':'COMPARTMENT_CHECKS_ONLY_FULL_ASSEMBLY_FAIL','full_assembly_pass':False,'scope':'pockets/carriers/reserved modules/latches only; see external-interface-check.json for remaining body/neck/Eye collisions','tolerance_volume_mm3':TOL,'checks':{},'files':{}}
 def check(name,value,limit=TOL):
  ok=value if isinstance(value,(bool,np.bool_)) else value<=limit
  evidence['checks'][name]={'value':value,'limit':limit,'pass':bool(ok)}
 for name,report in reports.items():
  for rel,digest in report['source_sha256'].items():check(name+':source:'+rel,hashlib.sha256((ROOT/rel).read_bytes()).hexdigest()==digest)
 rawf,rawb=native(source('Cabin_Front_Blue')),native(source('Cabin_Back_Blue_Repaired'))
 front=native(load('candidate_cabin_front_with_wire_route'));back=native(load('candidate_cabin_back_with_latches'))
 for name,original,cut in [('front',rawf,front),('back',rawb,back)]:
  evidence['checks'][name+':added_outside_original_mm3']={'value':vol(cut-original),'pass':True,'note':'surface simplification tolerance 0.015mm; geometric bound checked below'}
  expanded=original.minkowski_sum(box(.06,.06,.06))
  check(name+':outside_0p03mm_axis_expansion_mm3',vol(cut-expanded))
 check('closed_front_back_overlap_mm3',vol(front^back))
 # 開口から基板が出入りする連続70mm包絡。
 sweep=box(.001,70,.001).translate([0,-35,0]);trays=[]
 for i in range(2):
  tray=native(load('candidate_carrier_'+str(i)));trays.append(tray)
  check(f'tray{i}:static_overlap_mm3',vol(tray^(front+back)))
  check(f'tray{i}:rear_70mm_continuous_overlap_mm3',vol(tray.minkowski_sum(sweep)^front))
 check('trays_mutual_overlap_mm3',vol(trays[0]^trays[1]))
 for module in reports['candidate']['modules']:
  m=bbox(*module['bounds_mm']);name=module['name']
  check(name+':shell_overlap_mm3',vol(m^(front+back)))
  check(name+':carrier_overlap_mm3',sum(vol(m^t) for t in trays))
  check(name+':rear_70mm_continuous_overlap_mm3',vol(m.minkowski_sum(sweep)^front))
 for bay in reports['candidate']['bays']:
  for i,p in enumerate(bay['stepped_pockets']):check(f'bay{bay["center_x"]}:{i}:wall_3p2mm_expansion_mm3',p['expanded_outside_original_mm3'])
 pegs=[];pegs_deflected=[]
 for name in ['lower','upper']:
  p=native(load('candidate_latch_peg_'+name));d=native(load('candidate_latch_peg_'+name+'_deflected'));pegs.append(p);pegs_deflected.append(d)
  check(name+':closed_shell_overlap_mm3',vol(p^(front+back)))
  check(name+':front_carrier_overlap_mm3',sum(vol(p^t) for t in trays))
  check(name+':deflected_front_overlap_mm3',vol(d^front))
  # 蓋側だけ20mm後方へ連続移動する。ラッチ変形は最大0.85mmの場合。
  check(name+':deflected_cap_continuous_path_overlap_mm3',vol(d^back.minkowski_sum(box(.001,20,.001).translate([0,-10,0]))))
 check('pegs_mutual_overlap_mm3',vol(pegs[0]^pegs[1]))
 # 全STLを再読。原点は組立座標、印刷用自動整列はこの工程では行わない。
 final_names=['candidate_cabin_front_with_wire_route','candidate_cabin_back_with_latches','candidate_carrier_0','candidate_carrier_1','candidate_latch_peg_lower','candidate_latch_peg_upper']
 for name in final_names:
  p=OUT/(name+'.stl');m=load(name);bodies=m.split(only_watertight=False)
  check(name+':watertight',m.is_watertight);check(name+':one_body',len(bodies)==1)
  evidence['files'][str(p.relative_to(ROOT))]={'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bounds_mm':m.bounds.tolist(),'volume_mm3':float(m.volume),'body_count':len(bodies),'watertight':m.is_watertight,'frame':'chassis plate bottom z=0','transform_to_chassis':np.eye(4).tolist()}
 # 外側に出るポートはFront-neck既存接合面だけ。USBはBackを外す。
 usb=bbox([-39,-231,37],[-25,-202,49])
 check('usb_service_with_back_removed_overlap_mm3',vol(usb^front)+sum(vol(usb^t) for t in trays))
 evidence['physical_unverified']=['purchased HENGE and main-board/header/USB dimensions','latch retention force, creep and fatigue','neck reinforcement and harness bend radius','thermal rise and service-loop slack','Front-only adhesive bond and tap-screw holding strength']
 evidence['passed']=all(r['pass'] for r in evidence['checks'].values())
 (OUT/'independent-check.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n')
 failed={k:v for k,v in evidence['checks'].items() if not v['pass']}
 print(json.dumps({'passed':evidence['passed'],'checks':len(evidence['checks']),'failed':failed,'physical_unverified':evidence['physical_unverified']},ensure_ascii=False,indent=2))
 if not evidence['passed']:raise SystemExit(1)
if __name__=='__main__':main()
