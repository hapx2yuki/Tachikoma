import sys,json,collections,hashlib
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path.cwd();sys.path.insert(0,str(ROOT/'tools'))
import sim_physics as S
B=ROOT/'docs/audits/20260905-round2/simulation'
folder=B/'stress-final'
planned=json.loads((B/'stress-cases.json').read_text());now=S.input_fingerprints();rows=[]
for case in planned:
 p=folder/(case['name']+'.json')
 if not p.exists():raise RuntimeError(f'Missing planned result: {p}')
 j=json.loads(p.read_text());old=B/'stress-before-cad'/p.name
 earlier=json.loads(old.read_text()) if old.exists() else None
 row={'name':case['name'],'status':j['status'],'failed_checks':[k for k,v in j['checks'].items() if not v],
      'input_hashes_match_current':j['input_sha256']==now,'case_matches_plan':j['case']==case,
      'changed_input_files':[k for k,v in now.items() if j['input_sha256'].get(k)!=v],
      'mass_kg':j['mass_kg'],'requested_time_s':j['requested_time_s'],'integrated_time_s':j['valid_integrated_time_s'],
      'max_roll_deg':j['max_abs_roll_deg'],'max_pitch_deg':j['max_abs_pitch_deg'],'fell_time_s':j['fell_time_s'],
      'tpu_support':j['tpu_support'],'max_leg_saturation':max(j['actuators'][n]['saturation_fraction'] for n in S.ALL_LEG_JOINTS),
      'max_arm_saturation':max(j['actuators'][n]['saturation_fraction'] for n in S.ARM_JOINTS),
      'positive_mechanical_power_W':j['positive_mechanical_power_W'],
      'commanded_paths_m':{s['name']:s['commanded_path_distance_m'] for s in j['segments']},
      'before_cad_status':earlier['status'] if earlier else None,'file':str(p.relative_to(ROOT))}
 rows.append(row)
byname={r['name']:r for r in rows}
distance=byname['native_nominal']['commanded_paths_m']['walk'];half=byname['timestep_half']['commanded_paths_m']['walk'];relative=abs(half-distance)/abs(distance) if distance else None
out={'created_utc':datetime.now(timezone.utc).isoformat(),'case_count':len(rows),'all_planned_cases_executed':len(rows)==len(planned),
     'all_input_hashes_match_current':all(r['input_hashes_match_current'] for r in rows),
     'all_cases_match_plan':all(r['case_matches_plan'] for r in rows),
     'status_counts':dict(collections.Counter(r['status'] for r in rows)),
     'timestep_comparison':{'nominal_distance_m':distance,'half_step_distance_m':half,'relative_difference':relative,'threshold':.05,'status':'PASS' if relative is not None and relative<=.05 else 'FAIL'},
     'physical_readiness':'FAIL: known geometric intersections and unsupported TPU contact; remaining actuator/electrical properties unmeasured',
     'cases':rows}
(B/'scenario_summary.json').write_text(json.dumps(out,indent=2,ensure_ascii=False))
print(json.dumps({k:v for k,v in out.items() if k!='cases'},ensure_ascii=False,indent=2))
