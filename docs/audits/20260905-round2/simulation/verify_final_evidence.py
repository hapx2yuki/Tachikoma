"""最終試験の件数、設定、入力世代をまとめて確かめる。結果のPASSは要求しない。"""
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import hashlib
import json
import sys

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT / 'tools'))
import sim_physics as S
from sim_stress import input_fingerprints

B = ROOT / 'docs/audits/20260905-round2/simulation'
now = S.input_fingerprints()
rows = []
for plan, folder in [('stress-cases.json', 'stress-final'),
                     ('foot-candidate-cases.json', 'foot-candidate-final')]:
    for case in json.loads((B / plan).read_text()):
        path = B / folder / (case['name'] + '.json')
        result = json.loads(path.read_text())
        expected = input_fingerprints(case)
        rows.append({
            'path': str(path.relative_to(ROOT)),
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'name': case['name'],
            'status': result['status'],
            'case_matches_plan': result['case'] == case,
            'input_hashes_match_current': result['input_sha256'] == expected,
            'inputs_unchanged_during_run': result['checks']['inputs_unchanged'],
            'changed_inputs': sorted(k for k in set(expected) | set(result['input_sha256'])
                                     if expected.get(k) != result['input_sha256'].get(k)),
        })
supplemental = []
for folder, script, metadata in [
    ('slope-initialization-final', 'slope_initialization_probe.py', 'initial_orientation_probe'),
    ('joint-dynamics-final', 'joint_dynamics_probe.py', 'joint_dynamics_probe'),
    ('joint-convergence-final', 'joint_dynamics_convergence.py', 'joint_convergence_probe'),
]:
    plan_path = B / folder / 'plan.json'
    plan = json.loads(plan_path.read_text())
    script_sha = hashlib.sha256((B / script).read_bytes()).hexdigest()
    for case in plan['cases']:
        path = B / folder / (case['name'] + '.json')
        result = json.loads(path.read_text())
        supplemental.append({
            'path': str(path.relative_to(ROOT)), 'name': case['name'],
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'status': result['status'], 'case_matches_plan': result['case'] == case,
            'input_hashes_match_current': result['input_sha256'] == input_fingerprints(case),
            'inputs_unchanged_during_run': result['checks']['inputs_unchanged'],
            'helper_hash_matches': result[metadata]['script_sha256'] == script_sha == plan['script_sha256'],
        })
self_path = B / 'all-pairs-native-20servos-final.json'
self_result = json.loads(self_path.read_text())
verification = json.loads((B / 'verification-final.json').read_text())
replay = json.loads((B / 'native-nominal-replay.json').read_text())
replay_matches = (
    replay['input_hashes_match_current']
    and replay['source_result_sha256'] == hashlib.sha256((ROOT / replay['source_result']).read_bytes()).hexdigest()
    and replay['video_sha256'] == hashlib.sha256((ROOT / replay['video']).read_bytes()).hexdigest()
    and replay['render_script_sha256'] == hashlib.sha256((B / 'render_native_replay.py').read_bytes()).hexdigest()
)
pose_rows = [{
    'name': pose['name'],
    'actual_intersecting_link_pairs': sum(bool(pair['actual_intersections']) for pair in pose['pairs']),
    'boolean_errors': sum(len(pair['errors']) for pair in pose['pairs']),
} for pose in self_result['poses']]
artifact_names = ['scenario_summary.json', 'foot-candidate-summary.json',
                  'all-pairs-native-20servos-final.json', 'check-urdf-final.log',
                  'regression-final.log', 'simulation-results.png', 'old-distributions.json',
                  'native-nominal-replay.mp4', 'native-nominal-replay.json',
                  'native-nominal-replay-frame.png', 'verification-final.json',
                  'warning-cleanup.json', 'mujoco-numerical-warnings.log']
artifacts = [{'path': str((B / name).relative_to(ROOT)),
              'sha256': hashlib.sha256((B / name).read_bytes()).hexdigest()}
             for name in artifact_names]
result = {
    'completed_utc': datetime.now(timezone.utc).isoformat(),
    'completed_jst': datetime.now(ZoneInfo('Asia/Tokyo')).isoformat(),
    'dynamic_case_count': len(rows),
    'all_dynamic_cases_match_plan': all(r['case_matches_plan'] for r in rows),
    'all_dynamic_input_hashes_match_current': all(r['input_hashes_match_current'] for r in rows),
    'all_dynamic_inputs_unchanged_during_run': all(r['inputs_unchanged_during_run'] for r in rows),
    'supplemental_case_count': len(supplemental),
    'all_supplemental_inputs_and_plan_match': all(
        r['case_matches_plan'] and r['input_hashes_match_current'] and r['helper_hash_matches']
        and r['inputs_unchanged_during_run']
        for r in supplemental),
    'self_contact_pose_count': len(pose_rows),
    'self_contact_input_hashes_match_current': self_result['input_sha256'] == now,
    'self_contact_inputs_unchanged_during_run': self_result['inputs_unchanged'],
    'verification_input_hashes_match_current': verification['input_sha256'] == now and verification['inputs_unchanged'],
    'regression_command_succeeded': any(r['log'].endswith('/regression-final.log') and r['exit_code'] == 0 for r in verification['checks']),
    'native_replay_matches_result_and_current_helper': replay_matches,
    'self_contact_boolean_error_count': sum(r['boolean_errors'] for r in pose_rows)
                                        + len(self_result['fixed_base_boolean_errors']),
    'fixed_base_case_intersections': len(self_result['fixed_base_intersections']),
    'input_sha256': now,
    'poses': pose_rows,
    'dynamic_cases': rows,
    'supplemental_cases': supplemental,
    'artifacts': artifacts,
    'interpretation': '完走と入力一致の証明。実部品干渉・支持不良が残るため、実機組立や歩行の合格証明ではない。',
}
result['evidence_complete_and_current'] = (
    result['dynamic_case_count'] == 75
    and result['all_dynamic_cases_match_plan']
    and result['all_dynamic_input_hashes_match_current']
    and result['all_dynamic_inputs_unchanged_during_run']
    and result['supplemental_case_count'] == 14
    and result['all_supplemental_inputs_and_plan_match']
    and result['self_contact_pose_count'] == 18
    and result['self_contact_input_hashes_match_current']
    and result['self_contact_inputs_unchanged_during_run']
    and result['verification_input_hashes_match_current']
    and result['regression_command_succeeded']
    and result['native_replay_matches_result_and_current_helper']
    and result['self_contact_boolean_error_count'] == 0
)
(B / 'final_refresh_evidence.json').write_text(json.dumps(result, indent=2, ensure_ascii=False))
print(json.dumps({k: v for k, v in result.items()
                  if k not in ('input_sha256', 'poses', 'dynamic_cases', 'supplemental_cases', 'artifacts')},
                 indent=2, ensure_ascii=False))
raise SystemExit(0 if result['evidence_complete_and_current'] else 1)
