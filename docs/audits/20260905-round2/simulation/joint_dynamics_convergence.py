"""低補正慣性の追加刻み試験。0.5msと0.25msの移動距離差5%以内を確認する。"""
from pathlib import Path
from datetime import datetime, timezone
import copy
import hashlib
import json
import sys
import mujoco

ROOT=Path.cwd();sys.path.insert(0,str(ROOT/'tools'))
import sim_stress as T
B=ROOT/'docs/audits/20260905-round2/simulation'
output=B/'joint-convergence-final';output.mkdir(exist_ok=True)
if any(output.glob('*.json')):raise RuntimeError('既存結果を保存してから再実行してください')
previous=json.loads((B/'joint-dynamics-final/plan.json').read_text())
cases=[];references={}
for case in previous['cases']:
    if not case['name'].endswith('dt0.0005'):continue
    updated=copy.deepcopy(case);updated['name']=case['name'].replace('dt0.0005','dt0.00025');updated['model']['timestep']=.00025
    cases.append(updated);references[updated['name']]=B/'joint-dynamics-final'/(case['name']+'.json')
script_sha=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
(output/'plan.json').write_text(json.dumps({'created_utc':datetime.now(timezone.utc).isoformat(),
    'script_sha256':script_sha,'cases':cases,'criterion':'0.5msから0.25msへの歩行区間移動距離差/0.5msの距離が5%以内。個々の物理条件も満たすこと。'},indent=2,ensure_ascii=False))
original=T.S.build_model;active={}
def modified(*args,**kwargs):
    model,indices=original(*args,**kwargs)
    for name in T.S.ALL_JOINTS:
        dof=model.jnt_dofadr[indices['jid'][name]]
        model.dof_armature[dof]=active['armature_kg_m2'];model.dof_damping[dof]=active['damping_Nm_s_per_rad']
    mujoco.mj_setConst(model,mujoco.MjData(model))
    return model,indices
T.S.build_model=modified;rows=[]
for case in cases:
    active=case['joint_dynamics_override'];r=T.execute(case,output)
    reference=json.loads(references[case['name']].read_text())
    def distance(result):return next(x['commanded_path_distance_m'] for x in result['segments'] if x['name']=='walk')
    before,after=distance(reference),distance(r);difference=abs(after-before)/abs(before)
    comparison={'coarser_timestep_s':.0005,'finer_timestep_s':.00025,'coarser_distance_m':before,
        'finer_distance_m':after,'relative_difference':difference,'threshold':.05,
        'status':'PASS' if difference<=.05 and r['status']=='PASS' and reference['status']=='PASS' else 'FAIL',
        'reference_sha256':hashlib.sha256(references[case['name']].read_bytes()).hexdigest()}
    r['joint_convergence_probe']={'script_sha256':script_sha,'override':active,'comparison':comparison}
    (output/(case['name']+'.json')).write_text(json.dumps(r,indent=2,ensure_ascii=False))
    rows.append({'name':case['name'],'status':r['status'],'comparison':comparison,
        'positive_mechanical_power_W':r['positive_mechanical_power_W'],
        'input_hashes_match_current':r['input_sha256']==T.input_fingerprints(case)})
(output/'summary.json').write_text(json.dumps({'created_utc':datetime.now(timezone.utc).isoformat(),
    'script_sha256':script_sha,'cases':rows,'all_input_hashes_match_current':all(r['input_hashes_match_current'] for r in rows)},indent=2,ensure_ascii=False))
print(json.dumps(rows,indent=2,ensure_ascii=False))
raise SystemExit(0 if all(r['comparison']['status']=='PASS' for r in rows) else 1)
