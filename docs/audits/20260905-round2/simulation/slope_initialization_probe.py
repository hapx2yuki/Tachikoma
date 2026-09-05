"""水平投入と斜面整列を区別する追加比較。斜面整列は実機の自動機能ではない。"""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import math
import sys

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT / 'tools'))
import sim_stress as T

B = ROOT / 'docs/audits/20260905-round2/simulation'
output = B / 'slope-initialization-final'
output.mkdir(exist_ok=True)
if any(output.glob('*.json')):
    raise RuntimeError('既存結果の上書きを禁止。再実行前に前回資料を保存してください。')
shoe = 'docs/audits/20260905-round2/foot-support-candidates'
cases = []
for name, slope, extra in [
    ('aligned_up10_linked', 10, {}),
    ('aligned_down10_linked', -10, {}),
    ('aligned_up10_vhacd', 10, {'contact_model': 'vhacd', 'hard_friction': .3}),
    ('aligned_up10_shoe', 10, {'contact_model': 'vhacd', 'hard_friction': .3,
                             'friction': .6, 'foot_candidate_dir': shoe}),
]:
    cases.append({'name': name, 'controller': 'native',
                  'model': {'slope_deg': slope, **extra},
                  'initial_orientation': 'X-axis rotation equals floor slope; externally pre-positioned',
                  'segments': [{'name': 'settle', 'duration': 2.},
                               {'name': 'walk', 'duration': 6.4, 'vy': 1.},
                               {'name': 'stop', 'duration': 1.6}]})
script_sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
(output / 'plan.json').write_text(json.dumps({
    'created_utc': datetime.now(timezone.utc).isoformat(),
    'script_sha256': script_sha, 'cases': cases,
    'criterion': 'sim_stressの既定基準を共用。転倒時刻が整定/歩行のどちらかも比較。',
    'limitations': '本体を床角に揃える外部初期配置。自動姿勢適応、実IMU応答、登坂性能の保証ではない。',
}, indent=2, ensure_ascii=False))
original = T.place_on_ground
def aligned_initialization(model, data, base_qadr, slope_deg):
    angle = math.radians(slope_deg) / 2
    data.qpos[base_qadr + 3:base_qadr + 7] = [math.cos(angle), math.sin(angle), 0., 0.]
    original(model, data, base_qadr, slope_deg)
T.place_on_ground = aligned_initialization
rows = []
for case in cases:
    result = T.execute(case, output)
    result['initial_orientation_probe'] = {
        'script_sha256': script_sha,
        'base_quaternion_wxyz': [math.cos(math.radians(case['model']['slope_deg']) / 2),
                                math.sin(math.radians(case['model']['slope_deg']) / 2), 0., 0.],
        'interpretation': '水平投入による初期整定の影響を分離するための外部初期配置。実機の姿勢機能ではない。',
    }
    path = output / (case['name'] + '.json')
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    rows.append({'name': case['name'], 'status': result['status'],
                 'fell_time_s': result['fell_time_s'],
                 'failed_checks': [k for k, v in result['checks'].items() if not v],
                 'tpu_support': result['tpu_support'],
                 'walk_distance_m': next(x['commanded_path_distance_m'] for x in result['segments'] if x['name'] == 'walk'),
                 'input_hashes_match_current': result['input_sha256'] == T.input_fingerprints(case)})
(output / 'summary.json').write_text(json.dumps({
    'created_utc': datetime.now(timezone.utc).isoformat(),
    'script_sha256': script_sha, 'cases': rows,
    'all_input_hashes_match_current': all(r['input_hashes_match_current'] for r in rows),
}, indent=2, ensure_ascii=False))
print(json.dumps(rows, indent=2, ensure_ascii=False))
raise SystemExit(0 if all(r['status'] == 'PASS' for r in rows) else 1)
