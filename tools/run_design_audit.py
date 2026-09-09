#!/usr/bin/env python3
"""設計生成と独立検査を分けて実行し、失敗も全件保存する。実機は動かさない。"""
import argparse,concurrent.futures,datetime,hashlib,json,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
PYTHON=str(ROOT/'.venv/bin/python')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--phase',choices=['generate','verify'],required=True)
    p.add_argument('--output-dir',type=Path,required=True)
    args=p.parse_args();out=args.output_dir.resolve();out.mkdir(parents=True,exist_ok=True)
    record=out/(args.phase+'-results.json')
    if record.exists():p.error(f'既存記録を保存するため別の出力先を指定: {record}')
    def cmd(name,script,*extra):return name,[PYTHON,str(ROOT/script),*map(str,extra)]
    if args.phase=='generate':
        commands=[cmd('build-all','hardware/src/build_all.py'),cmd('head-eyecut','tools/make_head_eyecut.py'),cmd('export-urdf','tools/export_urdf.py')]
    else:
        commands=[cmd('check-leg-assembly','tools/check_leg_assembly.py','--output',out/'leg-assembly.png'),
                  cmd('coxa-sweep','tools/check_coxa_sweep.py','--json',out/'coxa-sweep.json'),
                  cmd('static-assembly','tools/check_static_assembly.py','--json',out/'static-all-pairs.json'),
                  cmd('sim-gait','tools/sim_gait.py','--output',out/'gait.png')]
        for stem in ['check_screw_bosses','check_leg_link_strength','check_arm','check_eye','check_audio','check_camera','check_shin_arm_leg','check_head_pod_clearance','check_pod_neck_strength','check_urdf']:
            commands.append(cmd(stem.replace('_','-'),'tools/'+stem+'.py'))
        for stem in ['check_print_artifacts','check_toe_contact','check_print_strength_sensitivity','check_mouth_chassis','check_kit_transforms']:
            commands.append(cmd(stem.replace('_','-'),'tools/'+stem+'.py','--json',out/(stem+'.json')))
        commands.extend([cmd('power-budget','tools/check_power_budget.py','--output',out/'power-budget.json'),
                         cmd('firmware-host-and-build','tools/tests/firmware_run.py','--build'),
                         ('regression',[PYTHON,'-m','unittest','tools.tests.test_audit_gates','tools.tests.test_issue_sync','tools.tests.test_print_artifacts','tools.tests.test_render_contracts','tools.tests.test_extract_meshes','tools.tests.test_mesh_checks','tools.tests.test_coxa_sweep','tools.tests.test_integration_audit','tools.tests.test_stl_export','tools.tests.test_export_stl','-v']),
                         cmd('simulation-regression','tools/tests/simulation_regression.py')])
    # 一つでも既存ログ/成果物があれば、生成や並列検査を開始する前に止める。
    conflicts = [path for path in out.iterdir() if path.name != record.name]
    if conflicts:
        p.error('空の出力先が必要: ' + ', '.join(str(path) for path in conflicts))
    def run(item):
        name,command=item;start=time.monotonic();log=out/(name+'.log')
        error = None
        try:
            with log.open('x') as f:
                r=subprocess.run(command,cwd=ROOT,stdout=f,stderr=subprocess.STDOUT)
            code = r.returncode
            status='PASS' if code==0 else 'UNVERIFIED' if name=='power-budget' and code==2 else 'FAIL'
        except Exception as ex:
            code, status, error = -1, 'ERROR', repr(ex)
        entry={'name':name,'command':command,'returncode':code,'status':status,'seconds':round(time.monotonic()-start,3),'log':str(log.relative_to(ROOT)) if log.is_relative_to(ROOT) else str(log),'log_sha256':hashlib.sha256(log.read_bytes()).hexdigest() if log.exists() else None}
        if error is not None: entry['error'] = error
        print(name,status,code,flush=True);return entry
    rows=[]
    def save():record.write_text(json.dumps({'date':datetime.datetime.now().astimezone().isoformat(),'phase':args.phase,'results':rows},ensure_ascii=False,indent=2)+'\n')
    if args.phase=='generate':
        for command in commands:
            result=run(command);rows.append(result);save()
            if result['returncode']:return 1  # 生成失敗後に古い出力を検査しない
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            pending=[pool.submit(run,c) for c in commands]
            for future in concurrent.futures.as_completed(pending):rows.append(future.result());save()
    return int(any(r['returncode'] for r in rows))


if __name__=='__main__':sys.exit(main())
