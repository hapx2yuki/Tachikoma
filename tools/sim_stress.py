#!/usr/bin/env python3
"""シミュレーション第2次監査。任意の指令列・高さ・接触・外力を再現する。

結果は条件付き。数値発散、初期形状不整合、物理的転倒を別の状態で保存する。
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
import subprocess,tempfile
import platform
from datetime import datetime,timezone
import numpy as np
import mujoco

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import sim_physics as S

# sim_print_first が出力先を明示したとき、生成 URDF/候補 STL の入力台帳を
# リポジトリ外でも安定した表示名へ変換する。
FINGERPRINT_OUTPUT_ROOT = None


def input_fingerprints(case):
    hashes=S.input_fingerprints(output_root=FINGERPRINT_OUTPUT_ROOT)
    # The five fixed print-first proxy exclusions are executable input policy,
    # not presentation metadata. Track their canonical contract digest beside
    # the URDF/config hashes so a later edit cannot reuse old results.
    from sim_collision import print_first_proxy_exclusion_contract
    contract = print_first_proxy_exclusion_contract()
    hashes['$PROXY_EXCLUSION_CONTRACT'] = contract['sha256']
    candidate=case.get('model',{}).get('foot_candidate_dir')
    if candidate:
        folder=Path(candidate)
        if not folder.is_absolute():folder=ROOT/folder
        for i in range(3):
            for stem in ('shoe_fitted','toe_hidden_seat'):
                path=folder/f'FR_{i}_{stem}_candidate.stl'
                hashes[S.fingerprint_key(path, FINGERPRINT_OUTPUT_ROOT)]=hashlib.sha256(path.read_bytes()).hexdigest()
    return dict(sorted(hashes.items()))


def _collision_cache_snapshot(ledger, *, reject_extra_cache=False):
    """build_modelが実際に消費したキャッシュだけを再ハッシュする。"""
    if ledger is None:
        return {}, None
    from sim_collision import (collision_cache_input_fingerprints,
                               public_collision_cache_ledger)
    hashes, current = collision_cache_input_fingerprints(
        ledger, output_root=FINGERPRINT_OUTPUT_ROOT,
        reject_extra_cache=reject_extra_cache)
    return hashes, current


def self_contacts(m,d):
    found={}
    for c in d.contact[:d.ncon]:
        b1,b2=int(m.geom_bodyid[c.geom1]),int(m.geom_bodyid[c.geom2])
        if not b1 or not b2 or c.dist>=-1e-6:continue
        names=[mujoco.mj_id2name(m,mujoco.mjtObj.mjOBJ_BODY,b) for b in (b1,b2)]
        key='|'.join(sorted(names))
        found[key]=max(found.get(key,0.),float(-c.dist))
    return found


def place_on_ground(m,d,base_qadr,slope_deg):
    """最下形状を床上0.5mmへ。低いトゥを無視した初期貫通を作らない。"""
    mujoco.mj_forward(m,d)
    normal=np.array([0.,-math.sin(math.radians(slope_deg)),math.cos(math.radians(slope_deg))])
    minimum=math.inf
    for gi in range(m.ngeom):
        if m.geom_bodyid[gi]==0 or not m.geom_contype[gi]:continue
        if m.geom_type[gi]!=mujoco.mjtGeom.mjGEOM_MESH:continue
        mi=m.geom_dataid[gi];start=m.mesh_vertadr[mi];n=m.mesh_vertnum[mi]
        pts=m.mesh_vert[start:start+n]@d.geom_xmat[gi].reshape(3,3).T+d.geom_xpos[gi]
        minimum=min(minimum,float((pts@normal).min()))
    if not math.isfinite(minimum):raise ValueError('接地用メッシュが無い')
    d.qpos[base_qadr+2]+=(.0005-minimum)/normal[2]
    mujoco.mj_forward(m,d)


def validate_native_trace(values, *, label='native output trace'):
    """C++ native出力を実行前に43列・有限・二値フラグへ固定する。"""
    try:
        trace=np.asarray(values, dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{label} is not numeric') from exc
    if trace.ndim != 2 or trace.shape[1] != 43 or trace.shape[0] <= 0:
        raise ValueError(f'{label} must have shape (N, 43), got {trace.shape}')
    if not np.isfinite(trace).all():
        raise ValueError(f'{label} contains non-finite values')
    for start, stop, name in ((1, 3, 'moving/ready'), (23, 43, 'enable flags')):
        if not np.isin(trace[:, start:stop], (0.0, 1.0)).all():
            raise ValueError(f'{label} {name} must contain only 0/1')
    return trace


def native_trace_mode(case):
    """Return the exact C++ startup mode bound to a case."""
    if case.get('startup_sequential'):
        return 'sequential'
    if case.get('initial') == 'zero':
        return 'direct'
    return 'ready'


def native_output_trace(case,include_initial=False):
    edges=np.cumsum([s['duration'] for s in case['segments']]);commands=[]
    if include_initial:commands.append([0.,0.,0.,0.,S.BODY_H_DEFAULT])
    for i in range(round(edges[-1]*S.SERVO_HZ)):
        segment=case['segments'][min(np.searchsorted(edges,i/S.SERVO_HZ+1e-10,side='right'),len(edges)-1)]
        commands.append([1/S.SERVO_HZ,*[segment.get(p,0.) for p in ('vx','vy','wz')],segment.get('body_h',S.BODY_H_DEFAULT)])
    with tempfile.TemporaryDirectory(prefix='tachikoma-native-sim-') as td:
        binary=Path(td)/'trace'
        subprocess.run(['c++','-std=c++17','-O2','-I',str(ROOT/'tools/tests/firmware_stubs'),'-I',str(ROOT/'firmware/src'),str(ROOT/'tools/tests/simulation_output_trace.cpp'),'-o',str(binary)],check=True,capture_output=True,text=True)
        result=subprocess.run([str(binary), native_trace_mode(case)],input='\n'.join(' '.join(map(str,c)) for c in commands)+'\n',check=True,capture_output=True,text=True)
    return validate_native_trace(
        np.loadtxt(result.stdout.splitlines(),ndmin=2),
        label='native output trace')


FINAL_MODEL_KINDS = ('final_integrated', 'print_first_final', 'frozen_integrated')

# A force output that remains at the speed-dependent actuator ceiling can look
# numerically stable while the servo is continuously loaded.  The fraction
# gate below catches broad saturation; these time gates catch a narrow but
# persistent upper-bound hold.  They are model acceptance criteria, not an
# LD-220MG continuous-torque or temperature guarantee.
BOUND_SATURATION_FRACTION_LIMIT = 0.05
BOUND_SATURATION_CONTIGUOUS_LIMIT_S = 0.10
HOLDING_BOUND_SATURATION_CONTIGUOUS_LIMIT_S = 0.02


def _validated_group_voltages(case, options):
    """ケース直下/モデル内の電圧指定を同じ規則で正規化する。"""
    nested = options.get('group_voltage_V')
    direct = case.get('group_voltage_V')
    if nested is not None and direct is not None and nested != direct:
        raise ValueError('model.group_voltage_V and case.group_voltage_V must match')
    raw = nested if nested is not None else direct
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError('group_voltage_V must be a leg/arm/eye mapping')
    unknown = sorted(set(raw) - {'leg', 'arm', 'eye'})
    if unknown:
        raise ValueError(f'unknown servo voltage groups: {unknown}')
    import export_urdf as E
    values = {}
    for group, value in raw.items():
        value = S._finite_float(value, f'group_voltage_V.{group}',
                                 strictly_positive=True)
        # Keep the same manufacturer endpoint check used by the final-case
        # gate; an out-of-range voltage must never become a partial report.
        E.servo_limits_at_voltage(value)
        values[group] = value
    return values


def _group_servo_limits(group_voltages):
    """3群の要求電圧を、同じメーカー端点内挿へ変換する。"""
    import export_urdf as E
    return {
        group: E.servo_limits_at_voltage(float(voltage))[group]
        for group, voltage in group_voltages.items()
    }


def voltage_model_metadata(options, group_voltages, torque_model):
    """結果JSONへ残す電圧モデルの実態と未確認境界を返す。"""
    if group_voltages:
        mode = 'group_voltage_V'
        description = (
            'group_voltage_V の leg/arm/eye 3群ごとに、旧DS3218/MG90Sメーカー'
            '端点（トルク・速度）を電圧内挿してMuJoCo上限へ適用。'
            'LD-220MG個体の6V実測、連続定格、目サーボの実電圧応答は未確認。'
        )
    elif 'voltage_V' in options:
        mode = 'voltage_V'
        description = (
            'voltage_V 1値について、旧DS3218/MG90Sメーカー端点（トルク・速度）'
            'を電圧内挿してMuJoCo上限へ適用。LD-220MG個体の実測・連続定格は未確認。'
        )
    else:
        mode = 'urdf_limits'
        description = 'URDF記載の上限へ、明示した感度倍率だけを適用。サーボ電圧内挿は未指定。'
    return {
        'mode': mode,
        'description': description,
        'requested_group_voltage_V': dict(group_voltages),
        'adopted_torque_model': torque_model,
        # This field describes the values actually consumed by ``execute``.
        # The manufacturer table is only a model reference and is recorded
        # separately below; it is not evidence of the fitted servo or of a
        # compiled firmware/URDF provenance.
        'source': (
            'runtime execute() stall/speed arrays; force range readback from '
            'MuJoCo after initial application, while the no-load speed cap '
            'remains the runtime array because MuJoCo has no speed-limit field'
        ),
        'physical_verified': False,
        'ld220mg_continuous_torque_temperature_verified': False,
        'eye_servo_voltage_response_verified': False,
    }


def _validate_case_inputs(case):
    """実行前にケース全入力を有限・型・範囲検査し、正規化したコピーを返す。

    以前は区間指令だけを検査し、ゲイン、外力、速度倍率、接触摩擦、電圧
    などが NaN/inf や不正型のまま MuJoCo へ届く余地があった。ここでは
    物理計算で参照する入力を一つの入口に集約し、失敗を結果JSONへ混ぜない。
    """
    if not isinstance(case, dict):
        raise ValueError('case must be an object')
    name = case.get('name')
    if not isinstance(name, str) or not name:
        raise ValueError('case.name must be a non-empty string')
    raw_segments = case.get('segments')
    if not isinstance(raw_segments, list) or not raw_segments:
        raise ValueError('segments must contain at least one object')
    normalized_segments = []
    for index, raw in enumerate(raw_segments):
        if not isinstance(raw, dict):
            raise ValueError(f'segments[{index}] must be an object')
        segment_name = raw.get('name', f'segment_{index}')
        if not isinstance(segment_name, str) or not segment_name:
            raise ValueError(f'segments[{index}].name must be a non-empty string')
        duration = S._finite_float(raw.get('duration', 0),
                                   f'segments[{index}].duration',
                                   strictly_positive=True)
        if not math.isclose(duration * S.SERVO_HZ,
                            round(duration * S.SERVO_HZ), abs_tol=1e-8):
            raise ValueError(f'segments[{index}].duration must be an integer servo period')
        normalized = dict(raw, name=segment_name, duration=duration)
        for key in ('vx', 'vy', 'wz'):
            value = S._finite_float(raw.get(key, 0),
                                    f'segments[{index}].{key}')
            if abs(value) > 1.0:
                raise ValueError(f'segments[{index}].{key} must be within -1..1')
            normalized[key] = value
        if 'body_h' in raw:
            normalized['body_h'] = S._finite_float(
                raw['body_h'], f'segments[{index}].body_h', strictly_positive=True)
        normalized_segments.append(normalized)

    raw_options = case.get('model', {})
    if not isinstance(raw_options, dict):
        raise ValueError('model must be an object')
    options = dict(raw_options)
    # Older case bundles placed this switch beside ``model``.  Normalize that
    # legacy spelling into the model options once, and reject an ambiguous
    # disagreement, so validation and execution read the same value.
    if 'ground_initialization' in case:
        root_ground = case['ground_initialization']
        if not isinstance(root_ground, bool):
            raise ValueError('case.ground_initialization must be boolean')
        if ('ground_initialization' in options
                and options['ground_initialization'] != root_ground):
            raise ValueError('case.ground_initialization and model.ground_initialization must match')
        options['ground_initialization'] = root_ground
    numeric_options = {
        'friction': (0.0, False), 'timestep': (0.0, True),
        'effort_scale': (0.0, True), 'mass_scale': (0.0, True),
        'velocity_scale': (0.0, True), 'step_height_mm': (0.0, False),
        'hard_friction': (0.0, False),
    }
    for key, (minimum, strictly_positive) in numeric_options.items():
        if key in options:
            options[key] = S._finite_float(
                options[key], f'model.{key}', minimum=minimum,
                strictly_positive=strictly_positive)
    if 'slope_deg' in options:
        options['slope_deg'] = S._finite_float(options['slope_deg'],
                                                'model.slope_deg')
        if abs(options['slope_deg']) >= 90.0:
            raise ValueError('model.slope_deg must be between -90 and 90 degrees')
    if 'step_front_y' in options:
        options['step_front_y'] = S._finite_float(options['step_front_y'],
                                                   'model.step_front_y')
    if 'voltage_V' in options:
        options['voltage_V'] = S._finite_float(options['voltage_V'],
                                                'model.voltage_V',
                                                strictly_positive=True)
        import export_urdf as E
        E.servo_limits_at_voltage(options['voltage_V'])
    for key in ('self_collision', 'include_parent_collision',
                'include_servo_collision', 'assembly_context',
                'ground_initialization'):
        if key in options and not isinstance(options[key], bool):
            raise ValueError(f'model.{key} must be boolean')
    contact_model = options.get('contact_model', 'linked-hulls')
    if contact_model not in ('linked-hulls', 'parts', 'vhacd'):
        raise ValueError('model.contact_model must be linked-hulls, parts, or vhacd')
    options['contact_model'] = contact_model
    torque_model = options.get('torque_model', 'linear-speed')
    if torque_model not in ('linear-speed', 'stall'):
        raise ValueError('model.torque_model must be linear-speed or stall')
    options['torque_model'] = torque_model
    group_voltages = _validated_group_voltages(case, options)
    if group_voltages and 'voltage_V' in options:
        raise ValueError('model.voltage_V and model.group_voltage_V are mutually exclusive')
    if group_voltages:
        # Use the normalized mapping for the actual comparison below. Keep it
        # nested so model and case input cannot diverge after validation.
        options['group_voltage_V'] = group_voltages

    raw_gains = case.get('gains', {
        'kp': {'leg': 24., 'arm': .8, 'eye': .05},
        'kv': {'leg': .4, 'arm': .03, 'eye': .005},
    })
    if not isinstance(raw_gains, dict):
        raise ValueError('gains must be an object')
    normalized_gains = {}
    for key in ('kp', 'kv'):
        if key not in raw_gains:
            raise ValueError(f'gains.{key} is required')
        normalized_gains[key] = S._gain_values(raw_gains[key], f'gains.{key}')

    raw_pushes = case.get('pushes', [])
    if not isinstance(raw_pushes, list):
        raise ValueError('pushes must be a list')
    normalized_pushes = []
    for index, raw in enumerate(raw_pushes):
        if not isinstance(raw, dict):
            raise ValueError(f'pushes[{index}] must be an object')
        start = S._finite_float(raw.get('start'), f'pushes[{index}].start',
                                minimum=0.0)
        duration = S._finite_float(raw.get('duration'),
                                   f'pushes[{index}].duration', minimum=0.0)
        force = raw.get('force_N')
        if not isinstance(force, (list, tuple)) or len(force) != 3:
            raise ValueError(f'pushes[{index}].force_N must be a 3-vector')
        force = [S._finite_float(value, f'pushes[{index}].force_N[{axis}]')
                 for axis, value in enumerate(force)]
        normalized_pushes.append(dict(raw, start=start, duration=duration,
                                      force_N=force))

    initial = case.get('initial', 'standing')
    if initial not in ('standing', 'zero'):
        raise ValueError('initial must be standing or zero')
    controller = case.get('controller', 'native')
    if controller not in ('native', 'python'):
        raise ValueError('controller must be native or python')
    if 'startup_sequential' in case and not isinstance(case['startup_sequential'], bool):
        raise ValueError('startup_sequential must be boolean')

    # Native traces may intentionally keep channels disabled during a
    # sequential boot or calibration, but that exception must be declared by
    # the case.  An implicit ``any(enabled)`` rule would let one active frame
    # dilute a disabled interval and make the model look healthy.
    legacy_sequential = case.get('startup_sequential', False)
    raw_enablement_policy = case.get('startup_enablement_policy')
    if raw_enablement_policy is None:
        enablement_policy = ('sequential_startup' if legacy_sequential
                             else 'all_enabled')
    else:
        if not isinstance(raw_enablement_policy, str):
            raise ValueError('startup_enablement_policy must be a string')
        policy_aliases = {
            'all_enabled': 'all_enabled',
            'sequential': 'sequential_startup',
            'sequential_startup': 'sequential_startup',
            'explicit_exemptions': 'explicit_exempt_segments',
            'explicit_exempt_segments': 'explicit_exempt_segments',
        }
        if raw_enablement_policy not in policy_aliases:
            raise ValueError(
                'startup_enablement_policy must be all_enabled, '
                'sequential_startup, or explicit_exempt_segments'
            )
        enablement_policy = policy_aliases[raw_enablement_policy]
        if ('startup_sequential' in case
                and legacy_sequential != (enablement_policy == 'sequential_startup')):
            raise ValueError(
                'startup_sequential and startup_enablement_policy must match'
            )
    raw_exempt_segments = case.get('enablement_exempt_segments', [])
    if not isinstance(raw_exempt_segments, list) or not all(
            isinstance(value, str) and value for value in raw_exempt_segments):
        raise ValueError('enablement_exempt_segments must be a list of names')
    segment_names = {segment['name'] for segment in normalized_segments}
    unknown_exempt_segments = sorted(set(raw_exempt_segments) - segment_names)
    if unknown_exempt_segments:
        raise ValueError(
            f'enablement_exempt_segments contain unknown names: {unknown_exempt_segments}'
        )
    if enablement_policy == 'explicit_exempt_segments' and not raw_exempt_segments:
        raise ValueError(
            'explicit_exempt_segments policy requires enablement_exempt_segments'
        )
    if raw_exempt_segments and enablement_policy != 'explicit_exempt_segments':
        raise ValueError(
            'enablement_exempt_segments requires explicit_exempt_segments policy'
        )

    normalized_case = dict(case)
    normalized_case['segments'] = normalized_segments
    normalized_case['model'] = options
    normalized_case['gains'] = normalized_gains
    normalized_case['pushes'] = normalized_pushes
    normalized_case['initial'] = initial
    normalized_case['controller'] = controller
    normalized_case['startup_enablement_policy'] = enablement_policy
    normalized_case['enablement_exempt_segments'] = list(raw_exempt_segments)
    normalized_case['startup_sequential'] = (
        enablement_policy == 'sequential_startup'
    )

    if options.get('model_kind') in FINAL_MODEL_KINDS:
        required_true = ('assembly_context', 'self_collision',
                         'include_servo_collision', 'include_parent_collision')
        missing = [key for key in required_true if options.get(key) is not True]
        if missing:
            raise ValueError('final model requires true flags: ' + ', '.join(missing))
        if contact_model == 'linked-hulls':
            raise ValueError('final model requires material-aware convex collision')
        if torque_model != 'linear-speed':
            raise ValueError('final model requires torque_model=linear-speed')
        if controller != 'native':
            raise ValueError('final model requires controller=native')
        if set(group_voltages) != {'leg', 'arm', 'eye'}:
            raise ValueError('final model requires leg/arm/eye group voltages')

    return normalized_case


def _tpu_support_metrics(segment_data, contact_model):
    """材料別の床反力インパルスを集計し、TPU支持の判定値を返す。"""
    material_impulse = {}
    finite = True
    for segment in segment_data:
        values = segment.get('normal_impulse_by_material', {})
        if not isinstance(values, dict):
            finite = False
            continue
        for material, value in values.items():
            try:
                number = float(value)
            except (TypeError, ValueError):
                finite = False
                continue
            if not math.isfinite(number) or number < 0.0:
                finite = False
                continue
            material_impulse[str(material)] = material_impulse.get(str(material), 0.0) + number
    impulse = sum(material_impulse.values())
    finite_positive = bool(finite and math.isfinite(impulse) and impulse > 0.0)
    material_aware = contact_model != 'linked-hulls'
    fraction = (material_impulse.get('TPU', 0.0) / impulse
                if finite_positive and material_aware else None)
    fraction_finite = fraction is not None and math.isfinite(float(fraction))
    accepted = bool(material_aware and finite_positive and fraction_finite
                    and float(fraction) >= 0.95)
    status = ('PASS' if accepted else 'FAIL') if material_aware else 'UNVERIFIED'
    return {
        'normal_impulse_Ns': material_impulse,
        'total_normal_impulse_Ns': float(impulse) if math.isfinite(impulse) else None,
        'total_impulse_finite_positive': finite_positive,
        'fraction': None if fraction is None else float(fraction),
        'fraction_finite': fraction_finite,
        'status': status,
        'threshold_fraction': 0.95,
        'material_aware_contact_required': material_aware,
    }


def all_axis_constraint_checks(axes, segment_data, *, saturation_limit=.05,
                               enabled_axes=None, enabled_counts=None,
                               required_enabled_counts=None,
                               required_step_count=None,
                               segment_enabled_counts=None,
                               segment_required_enabled_counts=None,
                               segment_required_steps=None,
                               startup_policy='all_enabled',
                               ready_gate=True):
    """Return the model-side load, speed, and saturation gates for all 20 axes.

    The old acceptance path inspected only the twelve leg axes.  Arms and eyes
    are also actuated and a boundary-hugging hold on one of them must make a
    final case fail.  Temperature and continuous rated torque are deliberately
    represented as unverified metadata by the caller: this function checks the
    finite model limits and measured-in-simulation load, but cannot turn that
    model result into a hardware guarantee.
    """
    names = tuple(S.ALL_JOINTS)

    def finite_number(value):
        try:
            return math.isfinite(float(value))
        except (TypeError, ValueError):
            return False

    rows = [axes.get(name) if isinstance(axes, dict) else None for name in names]
    axis_count_all_20 = (
        isinstance(axes, dict) and len(axes) == len(names)
        and set(axes) == set(names)
    )
    if enabled_axes is None:
        axis_enabled_all_20 = True
    else:
        try:
            raw_enabled_array = np.asarray(enabled_axes)
            if raw_enabled_array.dtype == np.bool_:
                enabled_array = raw_enabled_array
                binary_enabled = True
            else:
                enabled_array = np.asarray(enabled_axes, dtype=float)
                binary_enabled = bool(
                    np.isfinite(enabled_array).all()
                    and np.isin(enabled_array, (0.0, 1.0)).all()
                )
            axis_enabled_all_20 = bool(
                enabled_array.shape == (len(names),)
                and binary_enabled and np.equal(enabled_array, 1).all()
            )
        except (TypeError, ValueError):
            axis_enabled_all_20 = False
    if enabled_counts is None:
        enabled_counts_array = None
        axis_enabled_count_all_20 = True
    else:
        try:
            enabled_counts_array = np.asarray(enabled_counts, dtype=float)
            axis_enabled_count_all_20 = bool(
                enabled_counts_array.shape == (len(names),)
                and np.isfinite(enabled_counts_array).all()
                and np.equal(enabled_counts_array,
                             np.floor(enabled_counts_array)).all()
                and (enabled_counts_array > 0.0).all()
            )
        except (TypeError, ValueError):
            enabled_counts_array = None
            axis_enabled_count_all_20 = False
    if required_enabled_counts is None or required_step_count is None:
        required_enabled_array = None
        required_steps = None
        axis_enabled_after_ready_all_20 = axis_enabled_all_20 and ready_gate
        axis_enabled_fraction_after_ready_all_20 = axis_enabled_all_20 and ready_gate
    else:
        try:
            required_enabled_array = np.asarray(required_enabled_counts, dtype=float)
            required_steps = float(required_step_count)
            valid_required = bool(
                required_enabled_array.shape == (len(names),)
                and np.isfinite(required_enabled_array).all()
                and np.isfinite(required_steps)
                and required_steps > 0.0
                and required_steps == math.floor(required_steps)
                and (required_enabled_array >= 0.0).all()
                and (required_enabled_array <= required_steps).all()
                and np.equal(required_enabled_array,
                             np.floor(required_enabled_array)).all()
            )
        except (TypeError, ValueError):
            required_enabled_array = None
            required_steps = None
            valid_required = False
        axis_enabled_after_ready_all_20 = bool(
            valid_required and ready_gate
            and np.equal(required_enabled_array, required_steps).all()
        )
        axis_enabled_fraction_after_ready_all_20 = bool(
            valid_required and ready_gate
            and (required_enabled_array / required_steps >= .95).all()
        )
    finite_fields = (
        'mean_absolute_torque_nm', 'rms_torque_nm',
        'max_absolute_torque_nm', 'max_velocity_rad_s',
        'saturation_fraction', 'bound_saturation_fraction',
        'stall_limit_nm', 'no_load_velocity_rad_s',
    )
    finite = bool(axis_count_all_20 and all(
        isinstance(row, dict)
        and all(finite_number(row.get(field)) for field in finite_fields)
        and float(row.get('stall_limit_nm')) > 0.0
        and float(row.get('no_load_velocity_rad_s')) > 0.0
        and 0.0 <= float(row.get('saturation_fraction')) <= 1.0
        and 0.0 <= float(row.get('bound_saturation_fraction')) <= 1.0
        for row in rows
    ))
    if finite and enabled_counts is not None:
        finite &= all(
            finite_number(row.get('enabled_step_count'))
            and finite_number(row.get('required_enabled_step_count'))
            and float(row.get('enabled_step_count')) > 0.0
            and float(row.get('required_enabled_step_count')) >= 0.0
            for row in rows
        )
    if finite:
        torque_within = all(
            float(row['max_absolute_torque_nm'])
            <= float(row['stall_limit_nm']) * (1.0 + 1e-9)
            for row in rows
        )
        rms_within = all(
            float(row['rms_torque_nm'])
            <= float(row['stall_limit_nm']) * (1.0 + 1e-9)
            for row in rows
        )
        velocity_within = all(
            float(row['max_velocity_rad_s'])
            <= float(row['no_load_velocity_rad_s']) * (1.0 + 1e-9) + 1e-9
            for row in rows
        )
        saturation_ok = all(
            float(row['saturation_fraction']) <= saturation_limit
            for row in rows
        )
        bound_saturation_ok = all(
            float(row['bound_saturation_fraction']) <= saturation_limit
            for row in rows
        )
    else:
        torque_within = rms_within = velocity_within = False
        saturation_ok = bound_saturation_ok = False

    segment_count = len(segment_data) if isinstance(segment_data, list) else 0
    segment_enabled_array = None
    if segment_enabled_counts is None:
        segment_enabled_count_valid = True
    else:
        try:
            segment_enabled_array = np.asarray(segment_enabled_counts, dtype=float)
            segment_enabled_count_valid = bool(
                segment_enabled_array.shape == (segment_count, len(names))
                and np.isfinite(segment_enabled_array).all()
                and np.equal(segment_enabled_array,
                             np.floor(segment_enabled_array)).all()
                and (segment_enabled_array >= 0.0).all()
            )
            if segment_enabled_count_valid:
                segment_steps = []
                for segment in segment_data:
                    steps = segment.get('steps') if isinstance(segment, dict) else None
                    if steps is None:
                        segment_steps = []
                        break
                    try:
                        steps = float(steps)
                    except (TypeError, ValueError):
                        segment_steps = []
                        break
                    if (not math.isfinite(steps) or steps < 0.0
                            or steps != math.floor(steps)):
                        segment_steps = []
                        break
                    segment_steps.append(steps)
                if len(segment_steps) == segment_count:
                    segment_enabled_count_valid &= bool(
                        (segment_enabled_array <=
                         np.asarray(segment_steps, dtype=float)[:, None]).all())
        except (TypeError, ValueError):
            segment_enabled_count_valid = False
            segment_enabled_array = None
    if (enabled_counts_array is not None and segment_enabled_array is not None
            and segment_enabled_count_valid
            and segment_enabled_array.shape == (segment_count, len(names))):
        axis_enabled_count_all_20 &= bool(
            np.equal(enabled_counts_array,
                     segment_enabled_array.sum(axis=0)).all())
    segment_finite = bool(segment_count > 0 and segment_enabled_count_valid)
    segment_saturation_ok = segment_count > 0
    segment_bound_saturation_ok = segment_count > 0
    # Final executions also carry torque and velocity summaries for every
    # actuator in every segment.  Keep the older helper fixtures (which only
    # model saturation) compatible when no enablement counts were supplied,
    # but make the production path fail closed when those per-segment fields
    # are missing or malformed.
    segment_torque_ok = segment_count > 0
    segment_rms_torque_ok = segment_count > 0
    segment_velocity_ok = segment_count > 0
    require_segment_load_metrics = segment_enabled_counts is not None
    for segment in segment_data if isinstance(segment_data, list) else []:
        if not isinstance(segment, dict):
            segment_finite = False
            segment_saturation_ok = segment_bound_saturation_ok = False
            segment_torque_ok = segment_rms_torque_ok = segment_velocity_ok = False
            continue
        for field, target in (
                ('axis_saturation_fraction', 'saturation'),
                ('axis_bound_saturation_fraction', 'bound')):
            values = segment.get(field)
            try:
                values = np.asarray(values, dtype=float)
            except (TypeError, ValueError):
                values = np.empty(0, dtype=float)
            valid_values = (
                values.shape == (len(names),)
                and np.isfinite(values).all()
                and (values >= 0.0).all()
                and (values <= 1.0).all()
            )
            segment_finite &= bool(valid_values)
            if not valid_values:
                if target == 'saturation':
                    segment_saturation_ok = False
                else:
                    segment_bound_saturation_ok = False
                continue
            allowed = bool(float(values.max()) <= saturation_limit)
            if target == 'saturation':
                segment_saturation_ok &= allowed
            else:
                segment_bound_saturation_ok &= allowed

        if require_segment_load_metrics:
            metric_arrays = {}
            for field in (
                    'axis_mean_absolute_torque_nm',
                    'axis_rms_torque_nm',
                    'axis_max_absolute_torque_nm',
                    'axis_max_velocity_rad_s'):
                values = segment.get(field)
                try:
                    values = np.asarray(values, dtype=float)
                except (TypeError, ValueError):
                    values = np.empty(0, dtype=float)
                valid_values = (
                    values.shape == (len(names),)
                    and np.isfinite(values).all()
                    and (values >= 0.0).all()
                )
                segment_finite &= bool(valid_values)
                metric_arrays[field] = values if valid_values else None
            if finite and all(value is not None for value in metric_arrays.values()):
                limits_stall = np.asarray(
                    [float(row['stall_limit_nm']) for row in rows], dtype=float)
                limits_speed = np.asarray(
                    [float(row['no_load_velocity_rad_s']) for row in rows], dtype=float)
                segment_torque_ok &= bool(
                    np.less_equal(metric_arrays['axis_max_absolute_torque_nm'],
                                  limits_stall * (1.0 + 1e-9)).all())
                segment_rms_torque_ok &= bool(
                    np.less_equal(metric_arrays['axis_rms_torque_nm'],
                                  limits_stall * (1.0 + 1e-9)).all())
                segment_velocity_ok &= bool(
                    np.less_equal(metric_arrays['axis_max_velocity_rad_s'],
                                  limits_speed * (1.0 + 1e-9) + 1e-9).all())
            else:
                segment_torque_ok = segment_rms_torque_ok = segment_velocity_ok = False

    segment_enabled_all_required = segment_count > 0
    segment_enabled_fraction_sufficient = segment_count > 0
    if (segment_required_enabled_counts is None
            or segment_required_steps is None):
        segment_enabled_all_required = bool(segment_enabled_all_required and ready_gate)
        segment_enabled_fraction_sufficient = bool(
            segment_enabled_fraction_sufficient and ready_gate)
    else:
        try:
            required_counts = np.asarray(segment_required_enabled_counts, dtype=float)
            required_steps_by_segment = np.asarray(segment_required_steps, dtype=float)
            valid_segments = bool(
                required_counts.ndim == 2
                and required_counts.shape[1] == len(names)
                and required_counts.shape[0] == segment_count
                and required_steps_by_segment.shape == (segment_count,)
                and np.isfinite(required_counts).all()
                and np.isfinite(required_steps_by_segment).all()
                and (required_counts >= 0.0).all()
                and (required_steps_by_segment >= 0.0).all()
                and np.equal(required_counts,
                             np.floor(required_counts)).all()
                and np.equal(required_steps_by_segment,
                             np.floor(required_steps_by_segment)).all()
                and (required_counts <= required_steps_by_segment[:, None]).all()
            )
        except (TypeError, ValueError):
            valid_segments = False
            required_counts = np.empty((0, len(names)))
            required_steps_by_segment = np.empty(0)
        required_segment_mask = np.asarray(
            [float(value) > 0.0 for value in required_steps_by_segment],
            dtype=bool) if valid_segments else np.empty(0, dtype=bool)
        segment_enabled_all_required = bool(
            valid_segments and ready_gate and required_segment_mask.any()
            and all(
                np.equal(required_counts[index], required_steps_by_segment[index]).all()
                for index in range(segment_count)
                if required_segment_mask[index]
            )
        )
        segment_enabled_fraction_sufficient = bool(
            valid_segments and ready_gate and required_segment_mask.any()
            and all(
                (required_counts[index] / required_steps_by_segment[index] >= .95).all()
                for index in range(segment_count)
                if required_segment_mask[index]
            )
        )

    return {
        'axis_count_all_20': axis_count_all_20,
        'axis_enabled_all_20': axis_enabled_all_20,
        'axis_enabled_count_all_20': axis_enabled_count_all_20,
        'axis_enabled_after_ready_all_20': axis_enabled_after_ready_all_20,
        'axis_enabled_fraction_after_ready_all_20': axis_enabled_fraction_after_ready_all_20,
        'axis_ready_after_startup_all_20': axis_enabled_after_ready_all_20,
        'required_step_count': (None if required_steps is None else int(required_steps)),
        'axis_metrics_finite_all_20': finite,
        'axis_torque_within_limits_all_20': torque_within,
        'axis_rms_torque_within_limits_all_20': rms_within,
        'axis_velocity_within_limits_all_20': velocity_within,
        'axis_saturation_all_20_le_5pct': saturation_ok,
        'axis_bound_saturation_all_20_le_5pct': bound_saturation_ok,
        'axis_segment_metrics_finite_all_20': segment_finite,
        'axis_saturation_per_segment_all_20_le_5pct': segment_saturation_ok,
        'axis_bound_saturation_per_segment_all_20_le_5pct': segment_bound_saturation_ok,
        'axis_torque_within_limits_per_segment_all_20': segment_torque_ok,
        'axis_rms_torque_within_limits_per_segment_all_20': segment_rms_torque_ok,
        'axis_velocity_within_limits_per_segment_all_20': segment_velocity_ok,
        'axis_enabled_per_required_segment_all_20': segment_enabled_all_required,
        'axis_enabled_fraction_per_required_segment_all_20': segment_enabled_fraction_sufficient,
        'startup_enablement_policy_explicit': startup_policy in (
            'all_enabled', 'sequential_startup', 'explicit_exempt_segments'),
        'temperature_constraints_verified': False,
        'continuous_rated_torque_verified': False,
        'saturation_fraction_limit': float(saturation_limit),
    }


def bound_saturation_acceptance(
        segment_data, *,
        fraction_limit=BOUND_SATURATION_FRACTION_LIMIT,
        contiguous_limit_s=BOUND_SATURATION_CONTIGUOUS_LIMIT_S,
        holding_contiguous_limit_s=HOLDING_BOUND_SATURATION_CONTIGUOUS_LIMIT_S):
    """Check per-axis upper-bound persistence for final model cases.

    ``bound_saturation_fraction`` is averaged over each axis's enabled
    physics steps.  That denominator can still make a short but sustained
    hold look harmless during a long run, so final cases also carry the
    maximum contiguous duration for every axis, both overall and while the
    firmware gait reports ``holding``.  A zero-command segment is required to
    observe at least one holding step; otherwise a stop-like segment could be
    declared healthy without ever entering the firmware hold state.

    Missing or malformed per-axis metrics fail closed.  The helper is kept
    separate from ``all_axis_constraint_checks`` so legacy sensitivity
    fixtures that predate the full-rate persistence fields remain readable;
    final execution calls it only after those fields have been produced.
    """
    names = tuple(S.ALL_JOINTS)

    def axis_values(segment, key):
        values = segment.get(key)
        try:
            values = np.asarray(values, dtype=float)
        except (TypeError, ValueError):
            return None
        if (values.shape != (len(names),)
                or not np.isfinite(values).all()
                or (values < 0.0).any()):
            return None
        return values

    segment_count = len(segment_data) if isinstance(segment_data, list) else 0
    metrics_finite = segment_count > 0
    overall_ok = segment_count > 0
    holding_metrics_finite = segment_count > 0
    holding_ok = segment_count > 0
    holding_fraction_ok = segment_count > 0
    required_holding_ok = segment_count > 0
    observed_holding = False
    maximum_overall = 0.0
    maximum_holding = 0.0
    for segment in segment_data if isinstance(segment_data, list) else []:
        if not isinstance(segment, dict):
            metrics_finite = holding_metrics_finite = False
            overall_ok = holding_ok = holding_fraction_ok = required_holding_ok = False
            continue

        overall = axis_values(
            segment, 'axis_bound_saturation_max_contiguous_by_axis_s')
        if overall is None:
            metrics_finite = overall_ok = False
        else:
            maximum_overall = max(maximum_overall, float(overall.max()))
            overall_ok &= bool((overall <= float(contiguous_limit_s) + 1e-12).all())

        raw_holding_steps = segment.get('holding_step_count')
        try:
            holding_steps = float(raw_holding_steps)
            valid_holding_steps = bool(
                math.isfinite(holding_steps)
                and holding_steps >= 0.0
                and holding_steps == math.floor(holding_steps)
            )
        except (TypeError, ValueError):
            holding_steps = 0.0
            valid_holding_steps = False
        holding_at_end = segment.get('holding_at_end')
        valid_holding_at_end = isinstance(holding_at_end, bool)
        holding_max = axis_values(
            segment, 'axis_holding_bound_saturation_max_contiguous_by_axis_s')
        holding_fraction = axis_values(
            segment, 'axis_holding_bound_saturation_fraction')
        valid_holding_metrics = bool(
            valid_holding_steps and holding_max is not None
            and holding_fraction is not None
            and valid_holding_at_end
            and (holding_fraction <= 1.0).all()
        )
        if not valid_holding_metrics:
            holding_metrics_finite = holding_ok = holding_fraction_ok = False
        else:
            maximum_holding = max(maximum_holding, float(holding_max.max()))
            holding_ok &= bool(
                (holding_max <= float(holding_contiguous_limit_s) + 1e-12).all()
            )
            holding_fraction_ok &= bool((holding_fraction <= float(fraction_limit)).all())
            if holding_steps > 0.0:
                observed_holding = True
            command = segment.get('command', [0.0, 0.0, 0.0])
            try:
                zero_command = bool(
                    len(command) >= 3
                    and max(abs(float(value)) for value in command[:3]) <= 1e-9
                )
            except (TypeError, ValueError):
                zero_command = False
            try:
                segment_steps = float(segment.get('steps', 0.0))
                valid_segment_steps = bool(
                    math.isfinite(segment_steps)
                    and segment_steps >= 0.0
                    and segment_steps == math.floor(segment_steps)
                )
            except (TypeError, ValueError):
                segment_steps = 0.0
                valid_segment_steps = False
            if not valid_segment_steps:
                metrics_finite = False
                required_holding_ok = False
            elif zero_command and segment_steps > 0.0:
                required_holding_ok &= holding_steps > 0.0 and holding_at_end is True

    return {
        'axis_bound_saturation_contiguous_metrics_finite_all_20': bool(
            metrics_finite),
        'axis_bound_saturation_contiguous_all_20_le_0p1s': bool(
            metrics_finite and overall_ok),
        'axis_holding_bound_saturation_metrics_finite_all_20': bool(
            holding_metrics_finite),
        'axis_holding_bound_saturation_contiguous_all_20_le_0p02s': bool(
            holding_metrics_finite and holding_ok),
        'axis_holding_bound_saturation_fraction_all_20_le_5pct': bool(
            holding_metrics_finite and holding_fraction_ok),
        'axis_holding_required_for_zero_command_segments_all_reached': bool(
            holding_metrics_finite and required_holding_ok),
        'holding_at_end_required_for_zero_command_segments': True,
        # Informational only: a moving-only final case need not contain a
        # holding segment.  Zero-command segments are gated separately above.
        'holding_samples_observed': bool(observed_holding),
        'bound_saturation_fraction_limit': float(fraction_limit),
        'bound_saturation_contiguous_limit_s': float(contiguous_limit_s),
        'holding_bound_saturation_contiguous_limit_s': float(
            holding_contiguous_limit_s),
        'maximum_bound_saturation_contiguous_s': float(maximum_overall),
        'maximum_holding_bound_saturation_contiguous_s': float(maximum_holding),
    }


def execute(case,out_dir):
    case = _validate_case_inputs(case)
    out_dir=Path(out_dir);out_dir.mkdir(parents=True,exist_ok=True)
    base_input_hash=input_fingerprints(case)
    options=case.get('model',{})
    gains=case['gains']
    m,idx=S.build_model(options.get('friction',1.),gains['kp'],gains['kv'],
        timestep=options.get('timestep',.002),effort_scale=options.get('effort_scale',1.),
        mass_scale=options.get('mass_scale',1.),self_collision=options.get('self_collision',False),
        include_parent_collision=options.get('include_parent_collision',False),
        slope_deg=options.get('slope_deg',0.),step_height_mm=options.get('step_height_mm',0.),
        step_front_y=options.get('step_front_y',.25),contact_model=options.get('contact_model','linked-hulls'),
        hard_friction=options.get('hard_friction',.3),include_servo_collision=options.get('include_servo_collision',False),
        foot_candidate_dir=options.get('foot_candidate_dir'))
    collision_cache_ledger = idx.get('collision_cache_ledger')
    cache_input_hash, collision_cache_ledger_public = _collision_cache_snapshot(
        collision_cache_ledger,
        reject_extra_cache=options.get('model_kind') in FINAL_MODEL_KINDS)
    # The cache is known only after build_model selects the collision parts.
    # Freeze the complete input ledger at this point, before integration, so
    # unrelated cache files cannot influence the result.
    current_hash = dict(sorted({**base_input_hash, **cache_input_hash}.items()))
    proxy_exclusion_contract = idx.get('proxy_exclusion_contract',
                                      {'status': 'NOT_APPLICABLE', 'pairs': []})
    native_full=native_output_trace(case,include_initial=True) if case.get('controller','native')=='native' else None
    native=native_full[1:] if native_full is not None else None
    d=mujoco.MjData(m);mujoco.mj_setConst(m,d)
    dt=float(m.opt.timestep);ctrl_steps=round(1/S.SERVO_HZ/dt)
    if not math.isclose(ctrl_steps*dt,1/S.SERVO_HZ):raise ValueError('制御周期の整数分割が必要')
    segments=case['segments'];edges=np.cumsum([s['duration'] for s in segments]);total=float(edges[-1]);steps=round(total/dt)
    startup_policy = case.get('startup_enablement_policy', 'all_enabled')
    exempt_segments = set(case.get('enablement_exempt_segments', []))
    segment_names = [segment['name'] for segment in segments]
    # Build the per-control-step enablement contract before integrating.  A
    # sequential boot is exempt only up to the first row that is both ready
    # and has all 20 channels enabled.  Any later disable is a failure.
    if native is None:
        required_control_mask = np.ones(steps // ctrl_steps + 1, dtype=bool)
        ready_control_ok = True
    else:
        control_count = len(native)
        control_times = np.arange(control_count, dtype=float) / S.SERVO_HZ
        control_segment_index = np.minimum(
            np.searchsorted(edges, control_times + 1e-10, side='right'),
            len(segments) - 1)
        control_all_enabled = np.all(native[:, 23:43] == 1.0, axis=1)
        control_ready = native[:, 2] == 1.0
        if startup_policy == 'sequential_startup':
            ready_rows = np.flatnonzero(control_all_enabled & control_ready)
            if len(ready_rows):
                first_ready = int(ready_rows[0])
                required_control_mask = np.arange(control_count) >= first_ready
            else:
                required_control_mask = np.zeros(control_count, dtype=bool)
        elif startup_policy == 'explicit_exempt_segments':
            required_control_mask = np.array(
                [segment_names[index] not in exempt_segments
                 for index in control_segment_index], dtype=bool)
        else:
            required_control_mask = np.ones(control_count, dtype=bool)
        required_rows = required_control_mask
        ready_control_ok = bool(
            required_rows.any()
            and np.all(control_all_enabled[required_rows])
            and np.all(control_ready[required_rows])
        )
    required_control_mask = np.asarray(required_control_mask, dtype=bool)
    required_step_mask = np.array([
        bool(required_control_mask[min(k // ctrl_steps,
                                     len(required_control_mask) - 1)])
        for k in range(steps)
    ], dtype=bool)
    if startup_policy == 'explicit_exempt_segments':
        required_segment_mask = np.array(
            [name not in exempt_segments for name in segment_names], dtype=bool)
    elif startup_policy == 'sequential_startup' and native is not None:
        required_segment_mask = np.array([
            bool(required_control_mask[np.flatnonzero(control_segment_index == si)].any())
            if np.any(control_segment_index == si) else False
            for si in range(len(segments))
        ], dtype=bool)
    else:
        required_segment_mask = np.ones(len(segments), dtype=bool)
    base=mujoco.mj_name2id(m,mujoco.mjtObj.mjOBJ_BODY,'base_link');qbase=int(m.jnt_qposadr[m.body_jntadr[base]])
    names=S.ALL_JOINTS;aids=np.array([idx['aid'][n] for n in names]);jids=[idx['jid'][n] for n in names]
    qa=np.array([m.jnt_qposadr[j] for j in jids]);da=np.array([m.jnt_dofadr[j] for j in jids])
    stall=m.actuator_forcerange[aids].copy();speed=np.array([idx['velocity_limits'][n] for n in names])*options.get('velocity_scale',1.)
    # final URDF はケース別コピーへ参照電圧の上限を書き込む。ここでは
    # コンパイル後のMuJoCo値を読み直して、全20関節へ実際に入った値を
    # 後段の台帳へ記録する。旧ケースの ``voltage_V`` も互換維持する。
    group_voltages=options.get('group_voltage_V',{})
    expected_by_group={}
    if group_voltages:
        try:
            expected_by_group=_group_servo_limits(group_voltages)
        except (TypeError,ValueError,KeyError):
            expected_by_group={}
        # Apply the same 3-group force and no-load speed limits used by the
        # final copied URDF.  Merely recording the requested voltage while
        # integrating at the old URDF limits would make sensitivity results
        # describe a different model than the JSON claims.
        for i,n in enumerate(names):
            group='leg' if n.startswith('leg_') else 'arm' if n.startswith('arm_') else 'eye'
            limits=expected_by_group.get(group)
            if limits is None:
                continue
            scale_effort=options.get('effort_scale',1.)
            scale_velocity=options.get('velocity_scale',1.)
            stall[i]=np.array([-float(limits['effort'])*scale_effort,
                               float(limits['effort'])*scale_effort])
            speed[i]=float(limits['velocity'])*scale_velocity
    if 'voltage_V' in options:
        volts=options['voltage_V']
        import export_urdf as E
        limits=E.servo_limits_at_voltage(volts)
        for i,n in enumerate(names):
            group='leg' if n.startswith('leg_') else 'arm' if n.startswith('arm_') else 'eye'
            torque=limits[group]['effort']
            stall[i]=np.array([-torque,torque])*options.get('effort_scale',1.)
            speed[i]=limits[group]['velocity']*options.get('velocity_scale',1.)

    # Make the effective initial force range part of the compiled model before
    # reporting it, then read the range back.  This keeps the voltage ledger
    # tied to what this execution actually supplied to MuJoCo.  The velocity
    # limit remains the explicit runtime ``speed`` array because MuJoCo has no
    # equivalent actuator no-load-speed field.
    m.actuator_forcerange[aids] = stall
    applied_stall = np.asarray(m.actuator_forcerange[aids], dtype=float).copy()
    voltage_rows=[];voltage_complete=True
    if group_voltages:
        import export_urdf as E
        voltage_complete=bool(expected_by_group)
        for i,name in enumerate(names):
            group=('leg' if name.startswith('leg_') else
                   'arm' if name.startswith('arm_') else 'eye')
            expected=expected_by_group.get(group)
            actual_effort=float(applied_stall[i,1])
            actual_velocity=float(speed[i])
            expected_effort=(None if expected is None else
                             float(expected['effort'])*options.get('effort_scale',1.))
            expected_velocity=(None if expected is None else
                               float(expected['velocity'])*options.get('velocity_scale',1.))
            effort_match=expected_effort is not None and math.isclose(actual_effort,expected_effort,rel_tol=1e-9,abs_tol=1e-9)
            velocity_match=expected_velocity is not None and math.isclose(actual_velocity,expected_velocity,rel_tol=1e-9,abs_tol=1e-9)
            voltage_complete &= effort_match and velocity_match
            voltage_rows.append({'joint':name,'group':group,
                'requested_voltage_V':group_voltages.get(group),
                'expected_force_limit_Nm':expected_effort,
                'expected_no_load_velocity_rad_s':expected_velocity,
                'mujoco_force_limit_Nm':actual_effort,
                'mujoco_no_load_velocity_rad_s':actual_velocity,
                'force_match':effort_match,'velocity_match':velocity_match})
        voltage_complete &= len(voltage_rows)==20 and set(group_voltages)=={'leg','arm','eye'}
    else:
        voltage_complete=False
    voltage_details=voltage_model_metadata(
        options, group_voltages, options.get('torque_model','linear-speed'))
    voltage_limit_report={'status':'PASS' if voltage_complete else 'UNVERIFIED',
        'source':('runtime stall/speed arrays used by execute(); force range readback '
                  'from MuJoCo after initial group application, while the no-load '
                  'speed cap remains the runtime array because MuJoCo has no '
                  'speed-limit field'),
        'reference':'manufacturer endpoint interpolation is a model assumption; LD-220MG individual/6V continuous capability unmeasured',
        'requested_group_voltage_V':group_voltages,
        'all_20_limits_applied':voltage_complete,
        'physical_verified':False,
        'mode':voltage_details['mode'],
        'description':voltage_details['description'],
        'adopted_torque_model':voltage_details['adopted_torque_model'],
        'joints':voltage_rows}

    h=segments[0].get('body_h',S.BODY_H_DEFAULT)
    initial=case.get('initial','standing');last={}
    targets,angles=S.compute_leg_targets(0,0,0,0,last,holding=True,body_h=h)
    targets.update(S.arm_targets_rad(angles)[0]);targets.update({n:0 for n in S.EYE_JOINTS})
    if initial=='zero':
        targets.update({n:0 for n in S.ALL_LEG_JOINTS})
        for side in ('r','l'):targets.update({f'arm_{side}_yaw':0.,f'arm_{side}_pitch':0.,f'arm_{side}_elbow':math.pi/4})
        current=np.zeros((4,3))
    else:current=np.array([angles[n] for n in S.sg._LEGS])
    if initial!='zero' and native_full is not None:
        # 初期物理角も実出力へ一致させる。高さ変更条件を変更後の姿勢へ
        # 瞬間移動して始めない。nativeの初期保持はBODY_H_DEF。
        targets.update(dict(zip(names,np.radians(native_full[0,3:23]))))
    d.qpos[qbase:qbase+7]=[0,0,h*.001,1,0,0,0]
    for n,v in targets.items():d.qpos[m.jnt_qposadr[idx['jid'][n]]]=v;d.ctrl[idx['aid'][n]]=v
    mujoco.mj_forward(m,d)
    if options.get('ground_initialization',True):place_on_ground(m,d,qbase,options.get('slope_deg',0.))
    initial_pos=d.xpos[base].copy();initial_penetration=self_contacts(m,d)
    driver=S.PhaseDriver();output=S.LegOutputDriver(current)
    torque_abs_sum=np.zeros(len(aids));torque_sq_sum=np.zeros(len(aids));torque_max=torque_abs_sum.copy();vel_max=torque_abs_sum.copy();error_max=torque_abs_sum.copy()
    saturation_count=np.zeros(len(aids),int);bound_saturation_count=np.zeros(len(aids),int)
    bound_saturation_run=np.zeros(len(aids),int);bound_saturation_max_run=np.zeros(len(aids),int)
    segment_torque_abs_sum=[np.zeros(12) for _ in segments]
    segment_torque_sq_sum=[np.zeros(12) for _ in segments]
    segment_torque_max=[np.zeros(12) for _ in segments]
    segment_saturation_count=[np.zeros(12,int) for _ in segments]
    segment_bound_saturation_count=[np.zeros(12,int) for _ in segments]
    segment_bound_saturation_run=[np.zeros(12,int) for _ in segments]
    segment_bound_saturation_max_run=[np.zeros(12,int) for _ in segments]
    # Keep the same per-segment saturation accounting for all 20 actuators.
    # The 12-axis arrays above remain as a compact leg-specific compatibility
    # view, while final acceptance must also cover both arm and eye groups.
    segment_all_saturation_count=[np.zeros(len(aids),int) for _ in segments]
    segment_all_bound_saturation_count=[np.zeros(len(aids),int) for _ in segments]
    segment_all_bound_saturation_run=[np.zeros(len(aids),int) for _ in segments]
    segment_all_bound_saturation_max_run=[np.zeros(len(aids),int) for _ in segments]
    segment_all_torque_abs_sum=[np.zeros(len(aids)) for _ in segments]
    segment_all_torque_sq_sum=[np.zeros(len(aids)) for _ in segments]
    segment_all_torque_max=[np.zeros(len(aids)) for _ in segments]
    segment_all_vel_max=[np.zeros(len(aids)) for _ in segments]
    segment_holding_count=[0 for _ in segments]
    segment_holding_bound_saturation_count=[np.zeros(len(aids),int) for _ in segments]
    segment_holding_bound_saturation_run=[np.zeros(len(aids),int) for _ in segments]
    segment_holding_bound_saturation_max_run=[np.zeros(len(aids),int) for _ in segments]
    enabled_count=np.zeros(len(aids), int)
    required_enabled_count=np.zeros(len(aids), int)
    segment_enabled_count=[np.zeros(len(aids), int) for _ in segments]
    segment_required_enabled_count=[np.zeros(len(aids), int) for _ in segments]
    segment_required_steps=np.zeros(len(segments), int)
    required_step_count=0
    power_positive=[];power_signed=[];self_max=dict(initial_penetration)
    segment_data=[{'name':s['name'],'command':[float(s.get('vx',0.)),
                   float(s.get('vy',0.)),float(s.get('wz',0.))],
                   'steps':0,'start_pos':None,'end_pos':None,'positive_power_sum':0.,
                   'normal_impulse_by_material':{},'normal_impulse_by_part':{},'yaw_change_deg':0.,'commanded_path_distance_m':0.} for s in segments]
    timeseries=[];sample_stride=max(1,round(.1/dt));nonfoot=0;fell=None;warning={};max_roll=max_pitch=0.
    enabled=np.ones(len(aids),bool);previous_pos=initial_pos.copy()
    min_h=math.inf;max_qvel=0.;yaw_last=None;force_temp=np.zeros(6);numeric_failure=False
    previous_segment_index=None
    native_control_index = 0
    for k in range(steps):
        t=k*dt;si=min(int(np.searchsorted(edges,t+1e-10,side='right')),len(segments)-1);seg=segments[si];sd=segment_data[si]
        vx,vy,wz=[float(seg.get(p,0.)) for p in ('vx','vy','wz')];h=float(seg.get('body_h',S.BODY_H_DEFAULT))
        if k%ctrl_steps==0:
            phase=driver.step(1/S.SERVO_HZ,vx,vy,wz)
            target,deg=S.compute_leg_targets(phase,vx,vy,wz,last,holding=driver.holding,body_h=h)
            command,cur=output.step(target,1/S.SERVO_HZ)
            for leg,sign in zip(('FR','FL'),S.ARM_LEG_YAW_SIGN):
                cur[leg]=(max(deg[leg][0]*sign,cur[leg][0]*sign)*sign,*cur[leg][1:])
            command.update(S.arm_targets_rad(cur)[0])
            for n,v in command.items():d.ctrl[idx['aid'][n]]=v
            if native is not None:
                native_control_index = min(k // ctrl_steps, len(native) - 1)
                row=native[native_control_index];driver.phase=float(row[0]);driver.holding=not bool(row[1])
                d.ctrl[aids]=np.radians(row[3:23])
                # The trace contract admits only exact 0/1 flags; keep the
                # comparison explicit so invalid values cannot become truthy.
                enabled=np.equal(row[23:43], 1.0)
        enabled_count += enabled
        segment_enabled_count[si] += enabled
        step_required = bool(required_step_mask[k])
        if step_required:
            required_step_count += 1
            required_enabled_count += enabled
            segment_required_steps[si] += 1
            segment_required_enabled_count[si] += enabled
        d.xfrc_applied[:]=0
        for push in case.get('pushes',[]):
            if push['start']<=t<push['start']+push['duration']:d.xfrc_applied[base,:3]+=push['force_N']
        velocity=d.qvel[da].copy();ranges=S.speed_torque_ranges(stall,velocity,speed) if options.get('torque_model','linear-speed')=='linear-speed' else stall
        ranges[~enabled]=0
        m.actuator_forcerange[aids]=ranges
        error=np.clip(d.ctrl[aids],m.actuator_ctrlrange[aids,0],m.actuator_ctrlrange[aids,1])-d.qpos[qa]
        error[~enabled]=0
        demand=m.actuator_gainprm[aids,0]*error+m.actuator_biasprm[aids,2]*velocity
        sat=enabled & ((demand<ranges[:,0]-1e-8)|(demand>ranges[:,1]+1e-8))
        mujoco.mj_step(m,d);actual=d.actuator_force[aids].copy()
        warning={mujoco.mjtWarning(i).name:int(w.number) for i,w in enumerate(d.warning) if w.number}
        if warning or not np.isfinite(d.qpos).all() or not np.isfinite(d.qvel).all():numeric_failure=True;break
        # `sat` is the exact controller-demand clipping condition.  Keep a
        # second, independent metric for output force sitting on the current
        # speed-dependent upper limit; this catches a holding posture that is
        # numerically just at the actuator ceiling even when demand is already
        # clipped.  Disabled actuators and zero ranges are excluded.
        upper=np.abs(ranges[:,1])
        bound_sat=enabled & np.isfinite(actual) & np.isfinite(upper) & (upper>1e-12) & (np.abs(actual)>=upper*(1.0-5e-3))
        if previous_segment_index != si:
            bound_saturation_run[:]=0
            segment_bound_saturation_run[si][:]=0
            previous_segment_index=si
        bound_saturation_run[bound_sat]+=1
        bound_saturation_run[~bound_sat]=0
        bound_saturation_max_run=np.maximum(bound_saturation_max_run,bound_saturation_run)
        segment_all_saturation_count[si]+=sat
        segment_all_bound_saturation_count[si]+=bound_sat
        segment_all_bound_saturation_run[si][bound_sat]+=1
        segment_all_bound_saturation_run[si][~bound_sat]=0
        segment_all_bound_saturation_max_run[si]=np.maximum(
            segment_all_bound_saturation_max_run[si],
            segment_all_bound_saturation_run[si])
        segment_bound_saturation_run[si][bound_sat[:12]]+=1
        segment_bound_saturation_run[si][~bound_sat[:12]]=0
        segment_bound_saturation_max_run[si]=np.maximum(segment_bound_saturation_max_run[si],segment_bound_saturation_run[si])
        # Record the same upper-bound signal only during the firmware's
        # explicit holding state.  This denominator is per enabled axis and
        # cannot be diluted by moving samples elsewhere in the case.
        holding_now = bool(driver.holding)
        if holding_now:
            segment_holding_count[si] += 1
            segment_holding_bound_saturation_count[si] += bound_sat
            segment_holding_bound_saturation_run[si][bound_sat] += 1
            segment_holding_bound_saturation_run[si][~bound_sat] = 0
            segment_holding_bound_saturation_max_run[si] = np.maximum(
                segment_holding_bound_saturation_max_run[si],
                segment_holding_bound_saturation_run[si])
        else:
            segment_holding_bound_saturation_run[si][:] = 0
        sd['holding_last'] = holding_now
        torque_abs=np.abs(actual)
        active_torque_abs=np.where(enabled, torque_abs, 0.)
        active_velocity=np.where(enabled, np.abs(d.qvel[da]), 0.)
        torque_abs_sum+=active_torque_abs
        torque_sq_sum+=np.where(enabled, actual*actual, 0.)
        torque_max=np.maximum(torque_max, active_torque_abs)
        vel_max=np.maximum(vel_max, active_velocity)
        error_max=np.maximum(error_max, np.where(enabled, abs(error), 0.))
        saturation_count+=sat;bound_saturation_count+=bound_sat
        leg_torque_abs=active_torque_abs[:12]
        segment_torque_abs_sum[si]+=leg_torque_abs
        segment_torque_sq_sum[si]+=np.where(enabled[:12], actual[:12]*actual[:12], 0.)
        segment_torque_max[si]=np.maximum(segment_torque_max[si],leg_torque_abs)
        segment_saturation_count[si]+=sat[:12]
        segment_bound_saturation_count[si]+=bound_sat[:12]
        segment_all_torque_abs_sum[si]+=active_torque_abs
        segment_all_torque_sq_sum[si]+=np.where(enabled, actual*actual, 0.)
        segment_all_torque_max[si]=np.maximum(
            segment_all_torque_max[si], active_torque_abs)
        segment_all_vel_max[si]=np.maximum(
            segment_all_vel_max[si], active_velocity)
        power=actual*.5*(velocity+d.qvel[da]);positive=float(np.maximum(power,0).sum());power_positive.append(positive);power_signed.append(float(power.sum()))
        mujoco.mj_forward(m,d);pos=d.xpos[base].copy();r,p,y=np.degrees(S.quat_to_rpy(d.xquat[base]));max_roll=max(max_roll,abs(r));max_pitch=max(max_pitch,abs(p));min_h=min(min_h,pos[2]);max_qvel=max(max_qvel,float(abs(d.qvel).max()))
        ground_z=math.tan(math.radians(options.get('slope_deg',0.)))*pos[1]
        if fell is None and (abs(r)>30 or abs(p)>30 or pos[2]-ground_z<h*.0005):fell=t+dt
        for pair,depth in self_contacts(m,d).items():self_max[pair]=max(self_max.get(pair,0.),depth)
        cmd=np.array([vx,vy]);yaw=math.radians(y)
        if np.linalg.norm(cmd):
            direction=np.array([[math.cos(yaw),-math.sin(yaw)],[math.sin(yaw),math.cos(yaw)]])@cmd/np.linalg.norm(cmd)
            sd['commanded_path_distance_m']+=float((pos-previous_pos)[:2]@direction)
        previous_pos=pos.copy()
        if sd['start_pos'] is None:sd['start_pos']=pos.tolist();yaw_last=y
        sd['end_pos']=pos.tolist();sd['steps']+=1;sd['positive_power_sum']+=positive
        sd['yaw_change_deg']+=(y-yaw_last+180)%360-180;yaw_last=y
        contacts=set();normal_by_mat={};normal_by_part={}
        for ci,c in enumerate(d.contact[:d.ncon]):
            b1,b2=int(m.geom_bodyid[c.geom1]),int(m.geom_bodyid[c.geom2])
            if b1 and b2 or not b1 and not b2 or c.efc_address<0:continue
            gi=c.geom1 if b1 else c.geom2;bi=int(m.geom_bodyid[gi]);body=mujoco.mj_id2name(m,mujoco.mjtObj.mjOBJ_BODY,bi);contacts.add(body)
            gname=mujoco.mj_id2name(m,mujoco.mjtObj.mjOBJ_GEOM,gi)
            info=idx['part_metadata'].get(gname,{'material':'MIXED','part':body})
            mujoco.mj_contactForce(m,d,ci,force_temp);normal=max(float(force_temp[0]),0.)
            for dest,key in [(normal_by_mat,info['material']),(normal_by_part,body+'/'+info['part'])]:dest[key]=dest.get(key,0.)+normal
        if any(not (b.startswith('leg_') and b.endswith('_tibia')) for b in contacts):nonfoot+=1
        for field,values in [('normal_impulse_by_material',normal_by_mat),('normal_impulse_by_part',normal_by_part)]:
            for key,value in values.items():sd[field][key]=sd[field].get(key,0.)+value*dt
        if k%sample_stride==0 or k==steps-1:
            sampled = {
                'step_index':int(k),
                'time':float(d.time),
                'segment':seg['name'],
                'phase':driver.phase,
                'holding':driver.holding,
                'command':[vx,vy,wz,h],
                'base_pos':pos.tolist(),
                'rpy_deg':[float(r),float(p),float(y)],
                'qpos':d.qpos.tolist(),
                'torque_nm':actual.tolist(),
                'velocity_rad_s':d.qvel[da].tolist(),
                'positive_power_W':positive,
                'normal_force_by_material_N':normal_by_mat,
                'contacts':sorted(contacts),
            }
            if native is not None:
                native_row = native[native_control_index]
                native_segment_index = min(
                    int(np.searchsorted(edges,
                                        native_control_index / S.SERVO_HZ + 1e-10,
                                        side='right')),
                    len(segments) - 1)
                native_segment = segments[native_segment_index]
                sampled.update({
                    'native_trace_index': int(native_control_index),
                    # ``native`` is the operational part of the trace; the
                    # exporter keeps an explicit initial row at source index
                    # zero.  Preserve that source index so a post-hoc checker
                    # can bind every saved physics row to the exact JSON/CSV
                    # row instead of guessing from a local counter.
                    'native_trace_source_index': int(native_control_index + 1),
                    'native_trace_time_s': float((native_control_index + 1)
                                                 / S.SERVO_HZ),
                    'native_phase': float(native_row[0]),
                    'native_moving': bool(native_row[1] == 1.0),
                    'native_ready': bool(native_row[2] == 1.0),
                    'native_angles_deg': [float(value)
                                          for value in native_row[3:23]],
                    'native_enabled': [bool(value == 1.0)
                                       for value in native_row[23:43]],
                    'native_command': [
                        float(native_segment.get('vx', 0.)),
                        float(native_segment.get('vy', 0.)),
                        float(native_segment.get('wz', 0.)),
                        float(native_segment.get('body_h', S.BODY_H_DEFAULT)),
                    ],
                })
            timeseries.append(sampled)
    completed=k+1;valid=len(power_positive)
    axes={n:{
        'enabled_step_count':int(enabled_count[i]),
        'enabled_fraction':float(enabled_count[i]/max(1,steps)),
        'required_enabled_step_count':int(required_enabled_count[i]),
        'required_enabled_fraction':float(required_enabled_count[i]/max(1,required_step_count)),
        'mean_absolute_torque_nm':float(torque_abs_sum[i]/max(1,enabled_count[i])),
        'rms_torque_nm':float(np.sqrt(torque_sq_sum[i]/max(1,enabled_count[i]))),
        'max_absolute_torque_nm':float(torque_max[i]),
        'max_velocity_rad_s':float(vel_max[i]),
        'saturation_fraction':float(saturation_count[i]/max(1,enabled_count[i])),
        'bound_saturation_fraction':float(bound_saturation_count[i]/max(1,enabled_count[i])),
        'max_contiguous_bound_saturation_s':float(bound_saturation_max_run[i]*options.get('timestep',.002)),
        'max_tracking_error_deg':float(np.degrees(error_max[i])),
        'stall_limit_nm':float(stall[i,1]),
        'no_load_velocity_rad_s':float(speed[i])
    } for i,n in enumerate(names)}
    for si_segment,(seg,sd) in enumerate(zip(segments,segment_data)):
        sd['duration_s']=sd['steps']*dt
        sd['mean_positive_mechanical_power_W']=sd.pop('positive_power_sum')/max(1,sd['steps'])
        segment_enabled = np.maximum(segment_enabled_count[si_segment], 1)
        sd['enabled_count_by_axis']=segment_enabled_count[si_segment].tolist()
        sd['enabled_fraction_by_axis']=(
            segment_enabled_count[si_segment]/max(1,sd['steps'])).tolist()
        sd['required_enabled_step_count_by_axis']=(
            segment_required_enabled_count[si_segment]).tolist()
        sd['required_step_count']=int(segment_required_steps[si_segment])
        sd['required_enabled_fraction_by_axis']=(
            segment_required_enabled_count[si_segment]
            / max(1,segment_required_steps[si_segment])).tolist()
        sd['enablement_required_after_startup']=bool(
            required_segment_mask[si_segment])
        holding_steps = int(segment_holding_count[si_segment])
        sd['holding_step_count'] = holding_steps
        sd['holding_fraction'] = float(holding_steps / max(1, sd['steps']))
        sd['holding_at_end'] = bool(sd.get('holding_last', False))
        sd['axis_bound_saturation_max_contiguous_by_axis_s'] = (
            segment_all_bound_saturation_max_run[si_segment] * dt
        ).tolist()
        sd['axis_holding_bound_saturation_fraction'] = (
            segment_holding_bound_saturation_count[si_segment]
            / max(1, holding_steps)
        ).tolist()
        sd['axis_holding_bound_saturation_max_contiguous_by_axis_s'] = (
            segment_holding_bound_saturation_max_run[si_segment] * dt
        ).tolist()
        if sd['steps']:
            sd['leg_axis_mean_absolute_torque_nm']=(segment_torque_abs_sum[si_segment]/segment_enabled[:12]).tolist()
            sd['leg_axis_rms_torque_nm']=np.sqrt(segment_torque_sq_sum[si_segment]/segment_enabled[:12]).tolist()
            sd['leg_axis_max_absolute_torque_nm']=segment_torque_max[si_segment].tolist()
            sd['leg_axis_saturation_fraction']=(segment_saturation_count[si_segment]/segment_enabled[:12]).tolist()
            sd['leg_axis_bound_saturation_fraction']=(segment_bound_saturation_count[si_segment]/segment_enabled[:12]).tolist()
            sd['leg_saturation_fraction_max']=float(max(sd['leg_axis_saturation_fraction']))
            sd['leg_bound_saturation_fraction_max']=float(max(sd['leg_axis_bound_saturation_fraction']))
            sd['leg_bound_saturation_max_contiguous_s']=float(segment_bound_saturation_max_run[si_segment].max()*dt)
            sd['axis_saturation_fraction']=(
                segment_all_saturation_count[si_segment]/segment_enabled).tolist()
            sd['axis_bound_saturation_fraction']=(
                segment_all_bound_saturation_count[si_segment]/segment_enabled).tolist()
            sd['axis_mean_absolute_torque_nm']=(
                segment_all_torque_abs_sum[si_segment]/segment_enabled).tolist()
            sd['axis_rms_torque_nm']=np.sqrt(
                segment_all_torque_sq_sum[si_segment]/segment_enabled).tolist()
            sd['axis_max_absolute_torque_nm']=segment_all_torque_max[si_segment].tolist()
            sd['axis_max_velocity_rad_s']=segment_all_vel_max[si_segment].tolist()
            sd['axis_saturation_fraction_max']=float(max(sd['axis_saturation_fraction']))
            sd['axis_bound_saturation_fraction_max']=float(max(sd['axis_bound_saturation_fraction']))
            sd['axis_bound_saturation_max_contiguous_s']=float(
                segment_all_bound_saturation_max_run[si_segment].max()*dt)
            sd['axis_holding_bound_saturation_fraction_max'] = float(
                max(sd['axis_holding_bound_saturation_fraction']))
            sd['axis_holding_bound_saturation_max_contiguous_s'] = float(
                max(sd['axis_holding_bound_saturation_max_contiguous_by_axis_s']))
        else:
            sd['leg_axis_mean_absolute_torque_nm']=[0.]*12
            sd['leg_axis_rms_torque_nm']=[0.]*12
            sd['leg_axis_max_absolute_torque_nm']=[0.]*12
            sd['leg_axis_saturation_fraction']=[0.]*12
            sd['leg_axis_bound_saturation_fraction']=[0.]*12
            sd['leg_saturation_fraction_max']=0.
            sd['leg_bound_saturation_fraction_max']=0.
            sd['leg_bound_saturation_max_contiguous_s']=0.
            sd['axis_saturation_fraction']=[0.]*len(aids)
            sd['axis_bound_saturation_fraction']=[0.]*len(aids)
            sd['axis_mean_absolute_torque_nm']=[0.]*len(aids)
            sd['axis_rms_torque_nm']=[0.]*len(aids)
            sd['axis_max_absolute_torque_nm']=[0.]*len(aids)
            sd['axis_max_velocity_rad_s']=[0.]*len(aids)
            sd['axis_saturation_fraction_max']=0.
            sd['axis_bound_saturation_fraction_max']=0.
            sd['axis_bound_saturation_max_contiguous_s']=0.
            sd['holding_step_count'] = 0
            sd['holding_fraction'] = 0.
            sd['holding_at_end'] = False
            sd['axis_bound_saturation_max_contiguous_by_axis_s'] = [0.] * len(aids)
            sd['axis_holding_bound_saturation_fraction'] = [0.] * len(aids)
            sd['axis_holding_bound_saturation_max_contiguous_by_axis_s'] = [0.] * len(aids)
            sd['axis_holding_bound_saturation_fraction_max'] = 0.
            sd['axis_holding_bound_saturation_max_contiguous_s'] = 0.
        if sd['start_pos']:
            delta=np.array(sd['end_pos'])-sd['start_pos'];sd['delta_xyz_m']=delta.tolist()
            v=np.array([seg.get('vx',0),seg.get('vy',0)]);sd['commanded_direction_distance_m']=float(delta[:2]@v/np.linalg.norm(v)) if np.linalg.norm(v) else None
    tpu_support = _tpu_support_metrics(segment_data, options.get('contact_model','linked-hulls'))
    # Persist the post-run input ledger. The boolean comparison alone is not
    # auditable when the result is moved to another output bundle.
    base_current_input_hash = input_fingerprints(case)
    current_cache_hash, collision_cache_ledger_current = _collision_cache_snapshot(
        collision_cache_ledger,
        reject_extra_cache=options.get('model_kind') in FINAL_MODEL_KINDS)
    current_input_hash = dict(sorted({**base_current_input_hash,
                                      **current_cache_hash}.items()))
    all_axis_checks = all_axis_constraint_checks(
        axes, segment_data,
        enabled_axes=(
            np.any(native[:, 23:43] == 1.0, axis=0)
            if native is not None else np.ones(len(names), dtype=bool)
        ),
        enabled_counts=enabled_count,
        required_enabled_counts=required_enabled_count,
        required_step_count=required_step_count,
        segment_enabled_counts=segment_enabled_count,
        segment_required_enabled_counts=segment_required_enabled_count,
        segment_required_steps=segment_required_steps,
        startup_policy=startup_policy,
        ready_gate=ready_control_ok)
    bound_checks = bound_saturation_acceptance(segment_data)
    expected_timeseries_indices = list(range(0, steps, sample_stride))
    if expected_timeseries_indices[-1] != steps - 1:
        expected_timeseries_indices.append(steps - 1)
    checks={'completed':completed==steps and not numeric_failure,'numeric_stability':not numeric_failure,
        'no_fall':fell is None,'no_nonleg_contact':nonfoot==0,'no_ik_fallback':not last.get('_ik_fallback_count',0) and not last.get('_ik_fail_count',0),
        'initial_self_penetration_le_0p1mm':max(initial_penetration.values(),default=0)<=.0001,
        'leg_saturation_le_5pct':max(axes[n]['saturation_fraction'] for n in S.ALL_LEG_JOINTS)<=.05,
        # A short transient must not hide a saturated hold in a stand/stop
        # segment.  Apply the same 5% cap per segment and expose the force-at-
        # bound metric separately for review.
        'leg_saturation_per_segment_le_5pct':all(sd['leg_saturation_fraction_max']<=.05 for sd in segment_data),
        'leg_bound_saturation_per_segment_le_5pct':all(sd['leg_bound_saturation_fraction_max']<=.05 for sd in segment_data),
        'inputs_unchanged':current_hash==current_input_hash,
        'translation_progress':all(sd['commanded_path_distance_m']/max(sd['duration_s'],1e-10)>=.005 for seg,sd in zip(segments,segment_data) if math.hypot(seg.get('vx',0),seg.get('vy',0))>.5 and sd['duration_s']>=3),
        'rotation_progress':all(sd['yaw_change_deg']*seg.get('wz',0)>=1 for seg,sd in zip(segments,segment_data) if abs(seg.get('wz',0))>.5 and sd['duration_s']>=3),
        'timeseries_complete': bool(
            not numeric_failure and len(timeseries) == len(expected_timeseries_indices))}
    if options.get('model_kind') in ('final_integrated','print_first_final','frozen_integrated'):
        # 最終caseでは指定電圧の3群を全20関節へ適用できたこと自体を
        # 入力条件として判定する。物理的な定格・連続トルクの合格ではない。
        checks['voltage_limits_all_20_applied']=voltage_complete
        # This is a fixed geometry-policy contract, not a case-controlled
        # parent filter.  Build-time verification ensures the model applied
        # exactly the reviewed five adjacent-interface pairs while the global
        # parent filter remains disabled.
        try:
            from sim_collision import print_first_proxy_exclusion_contract
            expected_proxy_contract = print_first_proxy_exclusion_contract()
        except Exception:
            expected_proxy_contract = None
        checks['fixed_proxy_exclusion_contract_applied'] = bool(
            isinstance(expected_proxy_contract, dict)
            and proxy_exclusion_contract == expected_proxy_contract
            and options.get('include_parent_collision') is True
            and idx.get('proxy_exclusion_compiled', {}).get('status') == 'PASS'
            and idx.get('proxy_exclusion_compiled', {}).get('compiled_count') == 5)
        # Material-aware final cases must demonstrate that the support actually
        # came from TPU foot contacts.  A finite simulation with PLA,
        # electronics, or servo-case contact alone is not accepted.
        checks['material_aware_contact_model']=tpu_support['material_aware_contact_required']
        checks['tpu_support_total_impulse_finite_positive']=tpu_support['total_impulse_finite_positive']
        checks['tpu_support_fraction_finite']=tpu_support['fraction_finite']
        checks['tpu_support_fraction_ge_0p95']=(
            tpu_support['fraction_finite']
            and tpu_support['fraction'] is not None
            and tpu_support['fraction'] >= tpu_support['threshold_fraction'])
        # Final integrated acceptance covers arms and eyes as well as the legs.
        # The thermal/rated-continuous boundary remains explicitly unverified
        # in the result metadata because no LD-220MG temperature measurement is
        # available.
        # Only model-side boolean gates participate in ``status``.  Thermal
        # and continuous-rated limits are deliberately retained in
        # ``axis_constraint_acceptance`` as false/unverified metadata and are
        # never mistaken for a measured hardware result.
        checks.update({key: value for key, value in all_axis_checks.items()
                       if key.startswith('axis_')})
        # A fraction-only gate can hide a continuous upper-bound hold in a
        # long trajectory.  Require the per-axis contiguous and holding-state
        # metrics before allowing a final model result to be PASS.
        checks.update({key: value for key, value in bound_checks.items()
                       if key.startswith('axis_')})
    if numeric_failure:status='NUMERICAL_FAILURE'
    elif not checks['initial_self_penetration_le_0p1mm']:status='INVALID_INITIAL_CONTACT_MODEL'
    elif fell is not None:status='FALL'
    elif all(checks.values()):status='PASS'
    else:status='FAIL'
    result={'case':case,'created_utc':datetime.now(timezone.utc).isoformat(),'input_sha256':current_hash,
        'input_sha256_current':current_input_hash,
        'engine':'mujoco','version':mujoco.__version__,'runtime':{'python':sys.version,'numpy':np.__version__,'platform':platform.platform()},'checks':checks,'status':status,'physical_readiness':'UNVERIFIED',
        'initial_position_m':initial_pos.tolist(),'total_sim_time_s':float(d.time),'valid_integrated_time_s':valid*dt,'requested_time_s':total,'warning_counts':warning,'fell_time_s':fell,'max_abs_roll_deg':max_roll,'max_abs_pitch_deg':max_pitch,'min_base_z_m':min_h if math.isfinite(min_h) else None,'max_abs_qvel':max_qvel,
        'initial_self_penetration_m':initial_penetration,'max_self_penetration_m':self_max,'nonleg_contact_steps':nonfoot,'ik_counts':{k:v for k,v in last.items() if k.startswith('_')},'mass_kg':float(m.body_mass.sum()),'joint_order':names,'actuators':axes,
        'positive_mechanical_power_W':{'mean':float(np.mean(power_positive)) if valid else None,'max':float(max(power_positive)) if valid else None,'p95':float(np.percentile(power_positive,95)) if valid else None},
        'voltage_model': voltage_model_metadata(options, group_voltages,
                                                options.get('torque_model','linear-speed'))['description'],
        'voltage_model_details': voltage_model_metadata(
            options, group_voltages, options.get('torque_model','linear-speed')),
        'torque_model': options.get('torque_model','linear-speed'),
        'torque_acceptance': {'saturation_fraction_limit': 0.05,
            'bound_saturation_threshold': 0.995,
            'bound_saturation_fraction_limit': 0.05,
            'bound_saturation_contiguous_limit_s': (
                BOUND_SATURATION_CONTIGUOUS_LIMIT_S),
            'holding_bound_saturation_contiguous_limit_s': (
                HOLDING_BOUND_SATURATION_CONTIGUOUS_LIMIT_S),
            'holding_required_for_zero_command_segments': True,
            'continuous_rms_is_report_only': True,
            'persistent_upper_bound_saturation_is_not_pass': True,
            'interpretation': '脚関節の上限張り付き・保持中の持続飽和は物理合格にしない。LD-220MGの実連続トルク/温度は未実測で、モデル値は実機保証ではない。'},
        'axis_constraint_acceptance': {**all_axis_checks, **bound_checks},
        'enablement_contract': {
            'startup_enablement_policy': startup_policy,
            'enablement_exempt_segments': sorted(exempt_segments),
            'ready_gate': bool(ready_control_ok),
            'required_step_count': int(required_step_count),
            'all_twenty_required_after_ready': True,
            'one_frame_disable_is_rejected': True,
        },
        'controller': 'actual firmware Gait+LegOutput+Arms+Servos including PWM quantization; hardware/network/IMU unavailable' if native is not None else 'Python leg replica, READY arms approximate',
        # Keep the human explanation separate from the machine gate used by
        # the post-hoc proof checker.  A final case may only claim native
        # controller evidence.
        'controller_kind': 'native' if native is not None else 'python_replica',
        'native_trace_mode': (native_trace_mode(case) if native is not None else None),
        'native_trace_initial_row': (
            native_full[0].tolist() if native_full is not None else None),
        'native_trace_row_count': (
            int(len(native_full)) if native_full is not None else None),
        'timeseries_sample_stride_steps': int(sample_stride),
        'timeseries_sample_period_s': float(sample_stride * dt),
        'timeseries_expected_row_count': int(len(expected_timeseries_indices)),
        'timeseries_complete': bool(
            not numeric_failure and len(timeseries) == len(expected_timeseries_indices)),
        'timeseries_expected_step_indices': expected_timeseries_indices,
        'collision_proxy_contract': proxy_exclusion_contract,
        'collision_proxy_compiled': idx.get('proxy_exclusion_compiled', {
            'status': 'NOT_APPLICABLE', 'expected_count': 0,
            'compiled_count': 0, 'expected_signatures': [],
            'compiled_excludes': [],
        }),
        'collision_cache_ledger': collision_cache_ledger_public,
        'collision_cache_ledger_current': collision_cache_ledger_current,
        # Final print-first cases add the external exact-source proof metadata
        # below.  Legacy/sensitivity runs must not be labeled as if the
        # print-first proxy contract had been exercised.
        'candidate_geometry': 'TPU toe shoes and hidden toe seats; per-tibia printed mass/inertia recomputed, candidate only, not adopted into CAD' if options.get('foot_candidate_dir') else None,
        'power_interpretation':'軸出力の正機械仕事率のみ。停止保持電流、銅損、ドライバ損失、電源効率は別であり、電流上限の認定には使えない。',
        'segments':segment_data,'timeseries':timeseries,
        'tpu_support':tpu_support}
    result['voltage_limit_report']=voltage_limit_report
    if options.get('model_kind') in FINAL_MODEL_KINDS:
        result.update({
            'external_proxy_proof_required': True,
            'collision_proxy_exact_source_proof': {
                'status': 'REQUIRED_EXTERNAL_POSTHOC',
                'checker': 'tools/check_print_first_proxy_exclusions.py',
                'continuous_reachable_set_proven': False,
            },
            'collision_proxy_interpretation': {
                'parent_collision_filtering': 'globally_disabled',
                'fixed_pair_exclusions_only': True,
                'runtime_case_additions': 'forbidden',
                'exact_source_proof_required': True,
                'continuous_reachable_set_proven': False,
            },
        })
    path=out_dir/(case['name']+'.json');path.write_text(json.dumps(result,indent=2,ensure_ascii=False,allow_nan=False))
    print(case['name'],status,'roll',round(max_roll,2),'pitch',round(max_pitch,2),'power',result['positive_mechanical_power_W'],'self initial mm',1000*max(initial_penetration.values(),default=0),flush=True)
    return result


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--cases',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);ap.add_argument('--select')
    args=ap.parse_args();cases=json.loads(args.cases.read_text())
    if args.select:cases=[c for c in cases if c['name']==args.select]
    if not cases:ap.error('該当するケースが無い')
    results=[execute(case,args.out) for case in cases]
    print('SCENARIO RESULTS:',{status:sum(r['status']==status for r in results) for status in sorted({r['status'] for r in results})})
    sys.exit(0 if all(r['status']=='PASS' for r in results) else 1)
