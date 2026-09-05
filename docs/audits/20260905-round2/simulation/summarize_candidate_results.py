from pathlib import Path
import sys,json
from datetime import datetime,timezone
ROOT=Path.cwd();sys.path.insert(0,str(ROOT/'tools'))
import sim_stress as T
B=ROOT/'docs/audits/20260905-round2/simulation';planned=json.loads((B/'foot-candidate-cases.json').read_text());rows=[]
for case in planned:
 p=B/'foot-candidate-final'/(case['name']+'.json');j=json.loads(p.read_text())
 old=json.loads((B/'foot-candidate-before-bvh-fix'/p.name).read_text())
 rows.append({'name':case['name'],'status':j['status'],'all_checks':j['checks'],'input_hashes_match_current':j['input_sha256']==T.input_fingerprints(case),'case_matches_plan':j['case']==case,
              'mass_kg':j['mass_kg'],'tpu_support':j['tpu_support'],'max_roll_deg':j['max_abs_roll_deg'],'max_pitch_deg':j['max_abs_pitch_deg'],'positive_mechanical_power_W':j['positive_mechanical_power_W'],
              'path_m':{s['name']:s['commanded_path_distance_m'] for s in j['segments']},
              'old_generator_reported_status':old['status'],'old_generator_validity':'INVALID_MODEL: inertia frame modified after compile; collision tree retained old inertial coordinates; these are not physical failures',
              'file':str(p.relative_to(ROOT))})
out={'created_utc':datetime.now(timezone.utc).isoformat(),'case_count':len(rows),'all_input_hashes_match_current':all(x['input_hashes_match_current'] for x in rows),'all_cases_match_plan':all(x['case_matches_plan'] for x in rows),'physical_readiness':'UNVERIFIED: conditional support comparison only; fixed head/servo intersections remain, material friction/compression and attachment strength unmeasured; candidate not adopted','cases':rows}
(B/'foot-candidate-summary.json').write_text(json.dumps(out,indent=2,ensure_ascii=False));print(json.dumps({k:v for k,v in out.items() if k!='cases'},indent=2))
