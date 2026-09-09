"""未同定の関節補正慣性・粘性・ゲインへの依存を測る。実測値への校正ではない。"""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import sys
import mujoco

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT / 'tools'))
import sim_stress as T

B = ROOT / 'docs/audits/20260905-round2/simulation'
output = B / 'joint-dynamics-final'
output.mkdir(exist_ok=True)
if any(output.glob('*.json')):
    raise RuntimeError('既存結果の上書きを禁止。再実行前に前回資料を保存してください。')
cases = []
for name, armature, damping, step, gain in [
    ('armature_0.0001_dt0.002', .0001, .02, .002, 1.),
    ('armature_0.0001_dt0.0005', .0001, .02, .0005, 1.),
    ('armature_0_dt0.002', 0., .02, .002, 1.),
    ('armature_0_dt0.0005', 0., .02, .0005, 1.),
    ('damping_0', .001, 0., .002, 1.),
    ('damping_0.05', .001, .05, .002, 1.),
    ('gain_0.5', .001, .02, .002, .5),
    ('gain_2', .001, .02, .002, 2.),
]:
    cases.append({'name': name, 'controller': 'native',
                  'model': {'timestep': step},
                  'joint_dynamics_override': {'armature_kg_m2': armature,
                                              'damping_Nm_s_per_rad': damping},
                  'gains': {'kp': {'leg': 24. * gain, 'arm': .8 * gain, 'eye': .05 * gain},
                            'kv': {'leg': .4 * gain, 'arm': .03 * gain, 'eye': .005 * gain}},
                  'segments': [{'name': 'settle', 'duration': 2.},
                               {'name': 'walk', 'duration': 6.4, 'vy': 1.},
                               {'name': 'stop', 'duration': 1.6}]})
script_sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
(output / 'plan.json').write_text(json.dumps({
    'created_utc': datetime.now(timezone.utc).isoformat(),
    'script_sha256': script_sha, 'cases': cases,
    'criterion': 'sim_stress既定。2msで発散した低慣性の条件は0.5msとも比較し、数値計算破綻と物理転倒を区別する。',
    'limitations': '任意の感度幅。購入サーボのロータ・歯車慣性、粘性、内部制御ゲインを測った値ではない。',
}, indent=2, ensure_ascii=False))
original = T.S.build_model
active_parameters = {}
def modified_model(*args, **kwargs):
    model, indices = original(*args, **kwargs)
    for name in T.S.ALL_JOINTS:
        dof = model.jnt_dofadr[indices['jid'][name]]
        model.dof_armature[dof] = active_parameters['armature_kg_m2']
        model.dof_damping[dof] = active_parameters['damping_Nm_s_per_rad']
    # ここでは剛体の慣性座標/形状を変えない。軸慣性に依存する派生重みを更新。
    mujoco.mj_setConst(model, mujoco.MjData(model))
    return model, indices
T.S.build_model = modified_model
rows = []
for case in cases:
    active_parameters = case['joint_dynamics_override']
    result = T.execute(case, output)
    result['joint_dynamics_probe'] = {
        'script_sha256': script_sha,
        'override': active_parameters,
        'scope': '20個のサーボ軸だけに適用。自由ベース、部品の質量・COM・慣性・衝突形状は変更しない。',
    }
    path = output / (case['name'] + '.json')
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    rows.append({'name': case['name'], 'status': result['status'],
                 'fell_time_s': result['fell_time_s'],
                 'warning_counts': result['warning_counts'],
                 'failed_checks': [k for k, v in result['checks'].items() if not v],
                 'walk_distance_m': next(x['commanded_path_distance_m'] for x in result['segments'] if x['name'] == 'walk'),
                 'max_abs_roll_deg': result['max_abs_roll_deg'],
                 'max_abs_pitch_deg': result['max_abs_pitch_deg'],
                 'positive_mechanical_power_W': result['positive_mechanical_power_W'],
                 'input_hashes_match_current': result['input_sha256'] == T.input_fingerprints(case)})
(output / 'summary.json').write_text(json.dumps({
    'created_utc': datetime.now(timezone.utc).isoformat(),
    'script_sha256': script_sha, 'cases': rows,
    'all_input_hashes_match_current': all(r['input_hashes_match_current'] for r in rows),
}, indent=2, ensure_ascii=False))
print(json.dumps(rows, indent=2, ensure_ascii=False))
raise SystemExit(0 if all(r['status'] == 'PASS' for r in rows) else 1)
