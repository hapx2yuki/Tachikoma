#!/usr/bin/env python3
"""既存サーボを使う低負荷歩容の有限比較。通常CAD/firmware/URDFを変更しない。

scan は足先点支持の予備選別、run は複製した実C++ヘッダーを50Hzで実行し
既存のMuJoCo検証器へ渡す。電流/連続定格/実収納の合格には使用しない。
print-first用の追加ヘッダーは、通常モードと分離した試験入力として扱う。
"""
import argparse
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
import hashlib
import itertools
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
import uuid

import numpy as np
import mujoco

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import sim_physics as S
import sim_stress as T
import sim_collision as COLLISION
from print_first_source_closure import (
    PRINT_FIRST_FREEZE_ADDITIONAL_INPUTS,
    PRINT_FIRST_SOURCE_CLOSURE,
)

OUT=ROOT/'outputs/print-first-20260905/simulation'
SHOES='docs/audits/20260905-round2/foot-support-candidates'
NATIVE_BINARY_RUNTIME=None
FINAL_MODEL_KINDS=('final_integrated','print_first_final','frozen_integrated')
# ``prepare_native`` copies exactly this header set into each native evidence
# bundle.  Keeping the set explicit makes build.json source provenance
# fail-closed when a header is silently omitted or an unrelated file is added.
NATIVE_PROFILE_HEADERS=(
    'arms.h', 'audio.h', 'config.h', 'control.h', 'eyes.h', 'gait.h',
    'ik.h', 'leg_output.h', 'peripherals.h', 'print_first_gait.h',
    'profile_config.h', 'servos.h', 'web_ui.h',
)
# leg_foot_bored の足フレーム原点 (z=0, φ10プラグ根元面) を、脛リンク
# の関節原点フレームへ一度だけ移す。export_urdf.py/print_first_assembly.py
# と同じ足→脛変換であり、物理長 C.TIBIA_LEN_GAIT ではなく生成形状の
# C.TIBIA_LEN を使う。各脚のリンク局所座標は同じで、実機の印刷材強度を
# 示すものではない。
PLA_PHI10_PLUG_ROOTS={
    leg:{'frame':'tibia','xyz_mm':[0.,0.,-float(S.sg._C.TIBIA_LEN)],
         'link':f'leg_{leg.lower()}_tibia','axis_local':[0.,0.,1.]}
    for leg in S.sg._LEGS
}
DEFAULT={'body_h':115.,'stance_r':129.,'stance_off_y':-30.,'step_h':18.,
         'max_step':30.,'max_turn_deg':12.,'cycle_t':1.6,'duty':.75,
         'sway_mm':[34.,34.,40.,40.],'sway_lead':.11,
         'phase_off':[.25,.5,.75,0.],'arm_swing_deg':8.,'hip_r':float(S.sg._C.HIP_R),'path_shape':'linear'}
def final_case_requirements(case, *, raise_on_missing=True):
    """最終統合モデルを旧感度モデルから分離する入力ゲート。

    最終ケースは、実形状を読み込めることに加えて、自己衝突を明示的に
    有効化し、サーボ外形を凸分解へ含める必要がある。ここで既定値を
    ``False`` から推測して通すと、床接触だけの予備結果を最終全機評価と
    誤認するため、条件不足は実行前に拒否する。
    """
    options=case.get('model',{}) if isinstance(case,dict) else {}
    kind=options.get('model_kind','legacy_sensitivity')
    if kind not in FINAL_MODEL_KINDS:
        return {'required':False,'status':'NOT_APPLICABLE','model_kind':kind,
                'missing':[],'conflicts':[],'checks':{}}

    missing=[];conflicts=[]
    checks={
        'assembly_context':options.get('assembly_context') is True,
        'self_collision':options.get('self_collision') is True,
        'include_servo_collision':options.get('include_servo_collision') is True,
        'contact_model':options.get('contact_model','linked-hulls'),
        'parent_collision_filter_disabled':options.get('include_parent_collision') is True,
    }
    if options.get('assembly_context') is not True:
        missing.append('model.assembly_context=true')
    if options.get('self_collision') is not True:
        missing.append('model.self_collision=true')
    if options.get('include_servo_collision') is not True:
        missing.append('model.include_servo_collision=true (include_servos)')
    if options.get('contact_model','linked-hulls')=='linked-hulls':
        missing.append('model.contact_model must use material-aware convex parts (for example vhacd)')
    try:
        requested=requested_group_voltages(case)
    except ValueError as exc:
        conflicts.append(str(exc));requested={}
    checks['group_voltage_V']=requested
    missing_voltage=sorted(set(SERVO_GROUPS)-set(requested))
    if missing_voltage:
        missing.append('group_voltage_V must explicitly provide leg/arm/eye reference voltages')
    if options.get('foot_candidate_dir'):
        conflicts.append('model.foot_candidate_dir is legacy-only and cannot be combined with a final model')
    if options.get('include_parent_collision') is not True:
        missing.append('model.include_parent_collision=true for formal freeze2 candidate')


    explicit=options.get('urdf_path') or options.get('model_urdf') or options.get('model_path')
    model_path=resolve_path(explicit or (ROOT/'hardware/urdf-print-first/tachikoma.urdf'))
    checks['final_urdf']=str(model_path)
    if not model_path.is_file():
        missing.append(f'final URDF exists: {model_path}')
    elif model_path == (ROOT/'hardware/urdf/tachikoma.urdf').resolve():
        conflicts.append('final model cannot use the legacy hardware/urdf/tachikoma.urdf')
    final_root=(ROOT/'hardware/urdf-print-first').resolve()
    checks['final_urdf_root']=str(final_root)
    try:
        model_path.relative_to(final_root)
    except ValueError:
        conflicts.append('final model URDF must reside under hardware/urdf-print-first')
    source_option=options.get('source_urdf')
    if source_option:
        source_path=resolve_path(source_option)
        checks['source_urdf']=str(source_path)
        if source_path!=model_path:
            conflicts.append('final model source_urdf must match the frozen final URDF')

    # A final model must carry all four root locations so contact forces can be
    # transferred to the measured PLA phi10 plug root. The executor may derive
    # this from C.TIBIA_LEN once, but cases_for() writes the derived structure
    # explicitly before this gate runs.
    roots=options.get('pla_phi10_plug_roots') or case.get('pla_phi10_plug_roots')
    root_legs=sorted(roots) if isinstance(roots,dict) else []
    checks['pla_phi10_plug_root_legs']=root_legs
    for leg in S.sg._LEGS:
        if not isinstance(roots,dict) or leg not in roots:
            missing.append(f'model.pla_phi10_plug_roots.{leg}')
            continue
        spec=roots[leg]
        xyz=(spec.get('xyz_mm') if isinstance(spec,dict) else spec)
        if not isinstance(xyz,(list,tuple)) or len(xyz)!=3:
            missing.append(f'model.pla_phi10_plug_roots.{leg}.xyz_mm[3]')
    # Geometry freeze data belongs to the final manifest. It prevents a stale
    # generated bundle from being silently used after config.py changes.
    freeze_time=options.get('geometry_freeze_time',options.get('freeze_time'))
    freeze_hash=options.get('geometry_freeze_hash',options.get('freeze_hash'))
    checks['geometry_freeze_time']=freeze_time
    checks['geometry_freeze_hash']=freeze_hash
    if not freeze_time:
        missing.append('model.geometry_freeze_time')
    if not freeze_hash:
        missing.append('model.geometry_freeze_hash')
    freeze_report=validate_freeze_manifest(case, require_generation_roles=True)
    checks['freeze_manifest']=freeze_report
    if freeze_report.get('status')!='PASS':
        missing.append('model.freeze_manifest must match the frozen URDF, STL, config, and firmware source hashes')

    # ``include_parent_collision`` controls MuJoCo's parent-link filtering and
    # is required for the formal candidate. The full mesh audit is still a
    # separate completeness item, but its incompleteness must not prevent an
    # offline physics run once the geometry/mass/input bundle is present.
    # ``finite_native_trace_mesh_audit`` is the canonical case key.  Read the
    # old name only as a deprecated alias so older case files remain
    # diagnosable without presenting it as a continuous-reachability claim.
    audit_alias_used = (
        'finite_native_trace_mesh_audit' not in options
        and options.get('full_reachable_mesh_audit') is True)
    audit_declared=(options.get('finite_native_trace_mesh_audit') is True or
                    audit_alias_used or bool(options.get('self_collision_audit_path')) or
                    bool(options.get('self_collision_audit')))
    checks['finite_native_trace_mesh_audit_declared']=audit_declared
    checks['finite_native_trace_mesh_audit_alias_used']=audit_alias_used

    status='PASS' if not missing and not conflicts else 'UNVERIFIED'
    result={'required':True,'status':status,'model_kind':kind,'missing':missing,
            'conflicts':conflicts,'checks':checks,
            'interpretation':'最終統合は材料別接触・サーボ外形・自己衝突を明示した場合だけ実行する。親子link衝突を有効にし、固定5組の例外を含む指定した有限native traceの全姿勢を実メッシュで別途検査する。床接触や足底近似だけの結果を全機PASSへ昇格しない。'}
    if raise_on_missing and status!='PASS':
        detail='; '.join(missing+conflicts)
        raise ValueError(f'final model requirements unmet: {detail}')
    return result


def source_print_first_profile():
    """hardware/src/config.py の候補辞書をそのまま試験profileへ変換する。"""
    cfg=getattr(S.sg._C,'PRINT_FIRST_GAIT',None)
    if not isinstance(cfg,dict):
        raise ValueError('config.py の PRINT_FIRST_GAIT が無い')
    required=('body_h','stance_r','stance_off_xy','step_h','max_step','max_turn_deg',
              'cycle_t','duty','sway_mm','sway_lead','phase_off','arm_swing_deg','hip_r','status')
    missing=[key for key in required if key not in cfg]
    if missing:raise ValueError(f'PRINT_FIRST_GAIT の値が不足: {missing}')
    off=cfg['stance_off_xy']
    return dict(DEFAULT,body_h=float(cfg['body_h']),stance_r=float(cfg['stance_r']),
        stance_off_y=float(off[1]),step_h=float(cfg['step_h']),max_step=float(cfg['max_step']),
        max_turn_deg=float(cfg['max_turn_deg']),cycle_t=float(cfg['cycle_t']),duty=float(cfg['duty']),
        sway_mm=[float(v) for v in cfg['sway_mm']],sway_lead=float(cfg['sway_lead']),
        phase_off=[float(v) for v in cfg['phase_off']],arm_swing_deg=float(cfg['arm_swing_deg']),
        hip_r=float(cfg['hip_r']),path_shape=str(cfg.get('path_shape','linear')),
        profile_status=str(cfg['status']),profile_adopted=bool(cfg.get('adopted',False)))


PRINT_FIRST_PROFILE=source_print_first_profile()


def config_phi10_plug_roots():
    """新足のφ10プラグ根元をconfig.pyの物理tibia長から生成する。"""
    tibia_len=float(S.sg._C.TIBIA_LEN)
    return {leg:{'xyz_mm':[0.,0.,-tibia_len],'frame':'tibia',
                    'link':f'leg_{leg.lower()}_tibia','axis_local':[0.,0.,1.]}
            for leg in S.sg._LEGS}


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False)+'\n')


def physical_result_content_sha(result):
    """Result content digest excluding the self-referential input ledger."""
    if not isinstance(result, dict):
        raise ValueError('physical result must be an object for canonical hashing')
    value = json.loads(json.dumps(
        result, sort_keys=True, ensure_ascii=False, allow_nan=False,
        separators=(',', ':')))
    for key in ('input_sha256', 'input_sha256_current',
                'physical_result_content_sha256',
                'physical_result_file_sha256'):
        value.pop(key, None)
    canonical = json.dumps(value, sort_keys=True, ensure_ascii=False,
                           allow_nan=False, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(canonical).hexdigest()


def contact_root_moment(contacts, root, axis=None):
    """接触ごとの力/接触トルクを根元へ移送する。単位は入力に従う。"""
    root=np.asarray(root,dtype=float);moment=np.zeros(3)
    for contact in contacts:
        pos=np.asarray(contact['pos'],dtype=float);force=np.asarray(contact['force'],dtype=float)
        torque=np.asarray(contact.get('torque',np.zeros(3)),dtype=float)
        moment+=np.cross(pos-root,force)+torque
    norm=float(np.linalg.norm(moment))
    if axis is None:return moment,norm,None
    axis=np.asarray(axis,dtype=float);length=float(np.linalg.norm(axis))
    if length<=1e-12:return moment,norm,None
    axis/=length;torsion=abs(float(moment@axis));bending=float(np.linalg.norm(moment-axis*(moment@axis)))
    return moment,norm,(bending,torsion)


def public_path(path, output_root=None):
    """結果JSONに保存するパス表現。実行用の絶対パスとは分離する。"""
    return S.fingerprint_key(Path(path), output_root)


def resolve_path(value, *, base=ROOT):
    """ケース入力のパスを内部実行用に解決する。"""
    path=Path(value)
    return path.resolve() if path.is_absolute() else (Path(base)/path).resolve()


def case_output_root(case_path):
    """ケースJSON内の ``$OUTPUT`` を生成元出力へ戻す基準ディレクトリ。"""
    path=Path(case_path).resolve()
    return path.parent.parent if path.parent.name=='cases' else path.parent


def resolve_case_output_refs(value, output_root):
    """保存済みケースの公開用 ``$OUTPUT`` 参照だけを内部パスへ戻す。

    結果JSONは絶対パスを漏らさないため、同じ出力束内の参照を
    ``$OUTPUT/...`` として保存する。再実行時はケースファイルの位置から
    出力束を復元する。``$EXTERNAL`` は内容ハッシュだけの非可逆表現なので
    そのまま残し、入力ゲートが未解決として扱えるようにする。
    """
    if isinstance(value,dict):
        return {key:resolve_case_output_refs(item,output_root)
                for key,item in value.items()}
    if isinstance(value,list):
        return [resolve_case_output_refs(item,output_root) for item in value]
    if isinstance(value,str) and value.startswith('$OUTPUT/'):
        return str((Path(output_root)/value[len('$OUTPUT/'):]).resolve())
    return value


def load_case_input(path):
    """ケース列を読み、公開パスを実行用パスへ解決する。"""
    path=Path(path).resolve()
    data=json.loads(path.read_text(encoding='utf-8'))
    data=resolve_case_output_refs(data,case_output_root(path))
    if isinstance(data,list):
        return data
    if isinstance(data,dict) and isinstance(data.get('cases'),list):
        return data['cases']
    if isinstance(data,dict):
        return [data]
    raise ValueError('ケースJSONはオブジェクトまたは配列である必要があります')


ROOT_RELATIVE_TOPS={'hardware','firmware','outputs','docs','tools','model'}


def resolve_manifest_path(value, *, base=ROOT):
    """台帳内のrepo相対pathと台帳隣接pathを区別して解決する。"""
    path=Path(value)
    if path.is_absolute():
        return path.resolve()
    if path.parts and path.parts[0] in ROOT_RELATIVE_TOPS:
        return (ROOT/path).resolve()
    return (Path(base)/path).resolve()


def print_first_assembly_generation_contract(paths, *, require_roles=True):
    """生成台帳の役割別スキーマ、入力SHA、寸法を検査する。

    ``paths`` は ``{"role": "body|legs|feet", "path": ...}`` の列を推奨する。
    旧呼出しとの互換性のため、単なるpath列も受け付け、その場合は親ディレクトリ
    名から役割を推定する。生成後に generator や入力が変わった台帳を、ファイル
    自身のSHA一致だけで最終入力へ戻さないため、台帳内の全 source_sha256/sources
    行も実ファイルと再計算して突合する。
    """
    import make_print_first_leg as PL
    cfg = S.sg._C.PRINT_FIRST
    config_path = ROOT / 'hardware/src/config.py'
    config_sha = sha(config_path)
    report = {'status': 'PASS', 'checked': [], 'roles': [], 'missing': [],
              'mismatches': [], 'source_files': []}

    role_aliases = {
        'body': 'body', 'body_manifest': 'body', 'assembly_manifest': 'body',
        'legs': 'legs', 'leg': 'legs', 'leg_manifest': 'legs',
        'feet': 'feet', 'foot': 'feet', 'feet_manifest': 'feet',
        'foot_assembly': 'feet', 'foot_contact_reference': 'feet',
    }
    required_top = {
        'body': ('config', 'parts', 'source_sha256'),
        'legs': ('dimensions_mm', 'parts', 'legs', 'source_sha256'),
        'feet': ('dimensions', 'outputs', 'print_to_foot_frame',
                 'install_to_tibia_link', 'sources', 'parts'),
    }
    required_part_keys = {
        'body': ('name', 'stl', 'link', 'transform', 'sha256'),
        'legs': ('name', 'path', 'role', 'link_kind', 'legs', 'sha256'),
        'feet': ('name', 'stl', 'material'),
    }

    def canonical(value):
        if isinstance(value, dict):
            return {str(k): canonical(v) for k, v in value.items()}
        if isinstance(value, (tuple, list)):
            return [canonical(v) for v in value]
        return value

    def equal(actual, expected):
        if isinstance(actual, dict) and isinstance(expected, dict):
            return set(actual) == set(expected) and all(
                equal(actual[key], expected[key]) for key in expected)
        if isinstance(actual, list) and isinstance(expected, list):
            return len(actual) == len(expected) and all(
                equal(a, e) for a, e in zip(actual, expected))
        if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
            return math.isclose(float(actual), float(expected), rel_tol=1e-9, abs_tol=1e-9)
        return actual == expected

    def finite_tree(value):
        if isinstance(value, dict):
            return all(finite_tree(child) for child in value.values())
        if isinstance(value, (list, tuple)):
            return all(finite_tree(child) for child in value)
        if isinstance(value, bool) or value is None or isinstance(value, str):
            return True
        if isinstance(value, (int, float)):
            return math.isfinite(float(value))
        return False

    def infer_role(path, explicit):
        if explicit:
            return role_aliases.get(str(explicit).lower(), str(explicit).lower())
        parts = {part.lower() for part in Path(path).parts}
        for candidate in ('body', 'legs', 'feet'):
            if candidate in parts:
                return candidate
        return None

    def entries():
        for raw in paths or []:
            explicit = None
            value = raw
            if isinstance(raw, dict) and 'path' in raw:
                explicit, value = raw.get('role'), raw.get('path')
            elif isinstance(raw, (tuple, list)) and len(raw) == 2:
                explicit, value = raw
            path = resolve_path(value)
            yield infer_role(path, explicit), path

    def source_rows(data):
        raw = data.get('source_sha256')
        if raw is None:
            raw = data.get('sources')
        if isinstance(raw, dict):
            return [{'path': key, 'sha256': value} for key, value in raw.items()]
        if isinstance(raw, list):
            return raw
        return None

    seen_roles = set()
    for role, path in entries():
        if role in seen_roles:
            report['mismatches'].append({'file': str(path), 'kind': 'duplicate_role',
                                         'role': role})
        seen_roles.add(role)
        report['roles'].append({'role': role, 'path': str(path)})
        if role not in required_top:
            report['missing'].append({'file': str(path), 'kind': 'unrecognized_role',
                                      'role': role})
            continue
        if not path.is_file():
            report['missing'].append({'file': str(path), 'kind': 'manifest_file'})
            continue
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except Exception as exc:  # noqa: BLE001 - retain cause in report
            report['mismatches'].append({'file': str(path), 'kind': 'invalid_json',
                                         'error': str(exc)})
            continue
        report['checked'].append(str(path))
        if not isinstance(data, dict):
            report['mismatches'].append({'file': str(path), 'kind': 'manifest_not_object'})
            continue

        for key in required_top[role]:
            if key not in data:
                report['missing'].append({'file': str(path), 'role': role,
                                          'kind': 'missing_schema_key', 'key': key})

        parts = data.get('parts')
        if not isinstance(parts, list) or not parts:
            report['missing'].append({'file': str(path), 'role': role,
                                      'kind': 'parts_missing_or_empty'})
        else:
            for index, item in enumerate(parts):
                if not isinstance(item, dict):
                    report['missing'].append({'file': str(path), 'role': role,
                                              'kind': 'part_not_object', 'index': index})
                    continue
                for key in required_part_keys[role]:
                    if key not in item:
                        report['missing'].append({'file': str(path), 'role': role,
                                                  'kind': 'part_missing_key',
                                                  'index': index, 'key': key})
                if not finite_tree(item):
                    report['mismatches'].append({'file': str(path), 'role': role,
                                                 'kind': 'part_nonfinite', 'index': index})

        if role == 'body':
            actual_config = data.get('config')
            if not isinstance(actual_config, dict):
                report['missing'].append({'file': str(path), 'role': role,
                                          'kind': 'config_missing_or_not_object'})
            else:
                for key in cfg:
                    if key not in actual_config:
                        report['missing'].append({'file': str(path), 'role': role,
                                                  'kind': 'config_missing_key', 'key': key})
                if not finite_tree(actual_config):
                    report['mismatches'].append({'file': str(path), 'role': role,
                                                 'kind': 'config_nonfinite'})
                expected = canonical(cfg)
                if not equal(canonical(actual_config), expected):
                    report['mismatches'].append({
                        'file': str(path), 'kind': 'body_config_vs_config_py',
                        'expected': expected, 'actual': canonical(actual_config)})

        elif role == 'legs':
            dims = data.get('dimensions_mm')
            required_dims = ('case', 'axis_length_with_horns',
                             'main_projection_candidate', 'shaft_from_case_end_candidate',
                             'pitch_axis_face', 'yaw_axis_face', 'yaw_face_z_lift')
            if not isinstance(dims, dict):
                report['missing'].append({'file': str(path), 'role': role,
                                          'kind': 'dimensions_mm_missing_or_not_object'})
            else:
                for key in required_dims:
                    if key not in dims:
                        report['missing'].append({'file': str(path), 'role': role,
                                                  'kind': 'dimensions_missing_key', 'key': key})
                if not finite_tree(dims):
                    report['mismatches'].append({'file': str(path), 'role': role,
                                                 'kind': 'dimensions_nonfinite'})
                expected_pitch = float(PL.PITCH_FACE_Y)
                expected_yaw_base = float(cfg.get('yaw_face_z', PL.YAW_FACE_Z))
                expected_lift = float(cfg.get('yaw_face_z_lift', 0.0))
                checks = {
                    'pitch_axis_face': (dims.get('pitch_axis_face'), expected_pitch),
                    'yaw_axis_face': (dims.get('yaw_axis_face'), expected_yaw_base + expected_lift),
                    'yaw_face_z_lift': (dims.get('yaw_face_z_lift'), expected_lift),
                }
                for key, (actual, expected) in checks.items():
                    try:
                        matches = actual is not None and math.isclose(
                            float(actual), expected, rel_tol=1e-9, abs_tol=1e-9)
                    except (TypeError, ValueError):
                        matches = False
                    if not matches:
                        report['mismatches'].append({
                            'file': str(path), 'kind': f'legs_{key}_vs_config_frame',
                            'expected': expected, 'actual': actual})
            legs = data.get('legs')
            if not isinstance(legs, dict):
                report['missing'].append({'file': str(path), 'role': role,
                                          'kind': 'legs_missing_or_not_object'})
            else:
                for leg in ('FL', 'FR', 'RL', 'RR'):
                    if leg not in legs:
                        report['missing'].append({'file': str(path), 'role': role,
                                                  'kind': 'legs_missing_key', 'key': leg})

        elif role == 'feet':
            dimensions = data.get('dimensions')
            if not isinstance(dimensions, dict) or not dimensions:
                report['missing'].append({'file': str(path), 'role': role,
                                          'kind': 'dimensions_missing_or_empty'})
            elif not finite_tree(dimensions):
                report['mismatches'].append({'file': str(path), 'role': role,
                                             'kind': 'dimensions_nonfinite'})
            outputs = data.get('outputs')
            if not isinstance(outputs, dict) or not outputs:
                report['missing'].append({'file': str(path), 'role': role,
                                          'kind': 'outputs_missing_or_empty'})
            elif not finite_tree(outputs):
                report['mismatches'].append({'file': str(path), 'role': role,
                                             'kind': 'outputs_nonfinite'})
            foot_frame = data.get('print_to_foot_frame')
            if not isinstance(foot_frame, dict) or not foot_frame or not finite_tree(foot_frame):
                report['missing'].append({'file': str(path), 'role': role,
                                          'kind': 'print_to_foot_frame_missing_or_invalid'})
            tibia_frame = data.get('install_to_tibia_link')
            if (not isinstance(tibia_frame, (list, tuple)) or
                    not tibia_frame or not finite_tree(tibia_frame)):
                report['missing'].append({'file': str(path), 'role': role,
                                          'kind': 'install_to_tibia_link_missing_or_invalid'})

        # A generator must record every source it used.  The hash is checked
        # even for the feet list, whose key is ``sources`` rather than
        # ``source_sha256``.  Generated part hashes are checked separately by
        # the assembly file collector and the final freeze manifest.
        source = source_rows(data)
        if not source:
            report['missing'].append({'file': str(path), 'role': role,
                                      'kind': 'source_hashes_missing_or_empty'})
        else:
            for index, item in enumerate(source):
                if not isinstance(item, dict):
                    report['missing'].append({'file': str(path), 'role': role,
                                              'kind': 'source_row_not_object', 'index': index})
                    continue
                raw_source = item.get('path') or item.get('file')
                expected_hash = item.get('sha256') or item.get('hash') or item.get('digest')
                if not isinstance(raw_source, str) or not raw_source:
                    report['missing'].append({'file': str(path), 'role': role,
                                              'kind': 'source_path_missing', 'index': index})
                    continue
                source_path = resolve_manifest_path(raw_source, base=path.parent)
                source_record = {'role': role, 'manifest': str(path),
                                 'path': str(source_path), 'expected_sha256': expected_hash,
                                 'exists': source_path.is_file()}
                if source_path.is_file():
                    source_record['actual_sha256'] = sha(source_path)
                else:
                    source_record['actual_sha256'] = None
                report['source_files'].append(source_record)
                if not source_path.is_file():
                    report['missing'].append({'file': str(path), 'role': role,
                                              'kind': 'source_file_missing',
                                              'source': raw_source})
                elif not isinstance(expected_hash, str) or not re.fullmatch(
                        r'[0-9a-fA-F]{64}', expected_hash):
                    report['mismatches'].append({'file': str(path), 'role': role,
                                                 'kind': 'source_hash_invalid',
                                                 'source': raw_source,
                                                 'actual': source_record['actual_sha256']})
                elif expected_hash.lower() != source_record['actual_sha256']:
                    report['mismatches'].append({'file': str(path), 'role': role,
                                                 'kind': 'source_sha256', 'source': raw_source,
                                                 'expected': expected_hash,
                                                 'actual': source_record['actual_sha256']})

    if require_roles:
        for role in ('body', 'legs', 'feet'):
            if role not in seen_roles:
                report['missing'].append({'role': role, 'kind': 'manifest_role_missing'})
    if report['missing'] or report['mismatches']:
        report['status'] = 'UNVERIFIED'
    return report


SERVO_GROUPS=('leg','arm','eye')


def requested_group_voltages(case):
    """ケースで指定された参照電圧を検証し、3群の値を返す。

    ``group_voltage_V`` は旧探索ケースとの互換性のためケース直下と
    ``model`` のどちらからも読める。最終構成では3群すべてを明示する。
    ここで使う ``servo_limits_at_voltage`` はメーカー端点からの参照内挿で、
    LD-220MG 個体の6 V定格や連続保持能力を意味しない。
    """
    options=case.get('model',{}) if isinstance(case,dict) else {}
    nested=options.get('group_voltage_V') if isinstance(options,dict) else None
    direct=case.get('group_voltage_V') if isinstance(case,dict) else None
    if nested is not None and direct is not None and nested != direct:
        raise ValueError('model.group_voltage_V と case.group_voltage_V が一致しない')
    raw=nested if nested is not None else direct
    if raw is None:
        return {}
    if not isinstance(raw,dict):
        raise ValueError('group_voltage_V は leg/arm/eye の辞書で指定する')
    import export_urdf as E
    unknown=sorted(set(raw)-set(SERVO_GROUPS))
    if unknown:
        raise ValueError(f'未知のサーボ群電圧: {unknown}')
    values={}
    for group,value in raw.items():
        try:
            voltage=float(value)
        except (TypeError,ValueError) as exc:
            raise ValueError(f'{group} の電圧が数値ではない: {value!r}') from exc
        if not math.isfinite(voltage):
            raise ValueError(f'{group} の電圧が有限ではない: {value!r}')
        # 端点範囲外は export_urdf 側で明示的に拒否する。
        E.servo_limits_at_voltage(voltage)
        values[group]=voltage
    return values


def _servo_group(joint_name):
    if joint_name.startswith('leg_'):
        return 'leg'
    if joint_name.startswith('arm_'):
        return 'arm'
    if joint_name.startswith('eye_'):
        return 'eye'
    return None


def voltage_limit_report(case, model_path):
    """URDFへ記録された全20関節の上限を参照電圧と突合する。"""
    requested=requested_group_voltages(case)
    base={'requested_group_voltage_V':requested,
          'source':'runtime case model limit elements read from model_path; expected group limit array read from voltage interpolation function',
          'reference':'export_urdf.servo_limits_at_voltage; manufacturer endpoint interpolation only',
          'ld220mg_6V_measured':False,
          'physical_verified':False,
          'ld220mg_continuous_torque_temperature_verified':False,
          'eye_servo_voltage_response_verified':False}
    if not requested:
        return dict(base,status='NOT_REQUESTED',joints=[],all_20_limits_applied=False)
    import export_urdf as E
    expected_by_group={group:E.servo_limits_at_voltage(voltage)[group]
                       for group,voltage in requested.items()}
    root=ET.parse(model_path).getroot()
    rows=[];seen=set()
    for joint in root.findall('joint'):
        name=joint.get('name','');group=_servo_group(name)
        limit=joint.find('limit')
        if group is None or name not in S.ALL_JOINTS or limit is None:
            continue
        seen.add(name)
        try:
            actual_effort=float(limit.get('effort'))
            actual_velocity=float(limit.get('velocity'))
        except (TypeError,ValueError):
            actual_effort=actual_velocity=float('nan')
        expected=expected_by_group.get(group)
        rows.append({'joint':name,'group':group,
                     'requested_voltage_V':requested.get(group),
                     'expected_effort_Nm':None if expected is None else expected['effort'],
                     'expected_velocity_rad_s':None if expected is None else expected['velocity'],
                     'urdf_effort_Nm':actual_effort if math.isfinite(actual_effort) else None,
                     'urdf_velocity_rad_s':actual_velocity if math.isfinite(actual_velocity) else None,
                     'effort_match':bool(expected is not None and math.isclose(actual_effort,expected['effort'],rel_tol=1e-9,abs_tol=1e-9)),
                     'velocity_match':bool(expected is not None and math.isclose(actual_velocity,expected['velocity'],rel_tol=1e-9,abs_tol=1e-9))})
    missing=sorted(set(S.ALL_JOINTS)-seen)
    complete=(set(requested)==set(SERVO_GROUPS) and len(rows)==len(S.ALL_JOINTS)
             and not missing and all(row['effort_match'] and row['velocity_match'] for row in rows))
    return dict(base,status='PASS' if complete else 'UNVERIFIED',joints=rows,
                missing_joints=missing,all_20_limits_applied=complete,
                interpretation='全20関節のURDF上限と参照電圧内挿値を突合。実LD-220MGの個体差・6 V定格・連続トルクは未測定。')


def apply_group_voltage_limits(case, source_path, out):
    """最終URDFをケース別に複製し、上限だけを参照電圧へ差し替える。

    形状、質量、慣性、関節原点には触れない。元の凍結URDFは保持し、
    複製側のメッシュ参照だけを絶対化して出力ディレクトリからも読めるようにする。
    """
    requested=requested_group_voltages(case)
    if not requested:
        return Path(source_path), {'status':'NOT_REQUESTED','path':None}
    if set(requested)!=set(SERVO_GROUPS):
        missing=sorted(set(SERVO_GROUPS)-set(requested))
        raise ValueError(f'最終モデルのgroup_voltage_Vが不足: {missing}')
    import export_urdf as E
    source_path=Path(source_path).resolve();root=ET.parse(source_path).getroot()
    expected={group:E.servo_limits_at_voltage(voltage)[group]
              for group,voltage in requested.items()}
    seen=set()
    for joint in root.findall('joint'):
        name=joint.get('name','');group=_servo_group(name)
        if group is None or name not in S.ALL_JOINTS:
            continue
        limit=joint.find('limit')
        if limit is None:
            raise ValueError(f'最終URDFの関節limitが無い: {name}')
        seen.add(name)
        limit.set('effort',f"{expected[group]['effort']:.12g}")
        limit.set('velocity',f"{expected[group]['velocity']:.12g}")
    missing=sorted(set(S.ALL_JOINTS)-seen)
    if missing:
        raise ValueError(f'最終URDFの関節limitが不足: {missing}')
    for mesh in root.findall('.//mesh'):
        filename=mesh.get('filename')
        if filename and not Path(filename).is_absolute():
            mesh.set('filename',str((source_path.parent/filename).resolve()))
    name=case.get('name','case')
    destination=Path(out)/'models'/f'{name}-voltage-limited.urdf'
    destination.parent.mkdir(parents=True,exist_ok=True)
    temporary=destination.with_suffix(destination.suffix+'.tmp')
    ET.indent(root)
    ET.ElementTree(root).write(temporary,encoding='unicode',xml_declaration=True)
    temporary.replace(destination)
    return destination, {'status':'APPLIED','path':destination,
                         'source_path':source_path,'requested_group_voltage_V':requested}


FREEZE_OK_STATUSES=('FROZEN','FINAL_FROZEN','PASS','OK','COMPLETE')
# The freeze ledger must cover every firmware source consumed by the normal
# and print-first builds.  Keep the canonical closure in one module so the
# generator, native trace checker, simulation, and publication gate cannot
# silently drift apart.
FREEZE_REQUIRED_SOURCES=tuple(dict.fromkeys(
    (*PRINT_FIRST_SOURCE_CLOSURE, *PRINT_FIRST_FREEZE_ADDITIONAL_INPUTS)
))
FINAL_FREEZE_STATUS='FINAL_FROZEN'


def _freeze_manifest_path(case):
    options=case.get('model',{}) if isinstance(case,dict) else {}
    raw=(options.get('freeze_manifest') or options.get('geometry_freeze_manifest')
         or options.get('freeze_manifest_path') or case.get('freeze_manifest'))
    return None if not raw else resolve_path(raw)


def _freeze_rows(data):
    """凍結台帳の汎用 files/inputs/artifacts 列を正規化する。"""
    raw=None
    for key in ('files','inputs','artifacts','frozen_files','file_hashes'):
        if isinstance(data,dict) and data.get(key) is not None:
            raw=data[key];break
    if raw is None and isinstance(data,dict) and isinstance(data.get('source_sha256'),dict):
        raw=data['source_sha256']
    rows=[]
    if isinstance(raw,dict):
        raw=[{'path':path,'sha256':digest} for path,digest in raw.items()]
    if not isinstance(raw,list):
        return rows
    for item in raw:
        if isinstance(item,str):
            rows.append({'path':item,'sha256':None,'role':None})
            continue
        if not isinstance(item,dict):
            continue
        path=item.get('path') or item.get('file') or item.get('filename')
        digest=(item.get('sha256') or item.get('sha') or item.get('hash')
                or item.get('digest'))
        if path:
            rows.append({'path':str(path),'sha256':None if digest is None else str(digest).lower(),
                         'role':item.get('role')})
    return rows


def _manifest_path_values(value):
    """生成台帳内の STL/path 参照を再帰的に拾う。"""
    if not value:
        return []
    path=resolve_path(value)
    if not path.is_file() or path.suffix.lower()!='.json':
        return [path] if path.is_file() else []
    values=[path]
    try:
        data=json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return values
    def walk(item):
        if isinstance(item,dict):
            for key,val in item.items():
                if key.lower() in ('path','stl','mesh','filename') and isinstance(val,str):
                    candidate=Path(val)
                    if candidate.suffix.lower() in ('.stl','.obj','.3mf','.urdf'):
                        values.append(resolve_manifest_path(candidate,base=path.parent))
                # Assembly manifests record generator provenance as a
                # ``source_sha256`` mapping whose keys are paths rather than
                # values.  Include those source files in the freeze closure.
                if isinstance(key,str) and Path(key).suffix.lower() in (
                        '.py','.h','.hpp','.cpp','.cc','.ini','.json','.stl',
                        '.obj','.3mf','.urdf'):
                    candidate=Path(key)
                    values.append(resolve_manifest_path(candidate,base=path.parent))
                walk(val)
        elif isinstance(item,list):
            for val in item:walk(val)
    walk(data)
    return values


def runtime_input_paths(urdf_path):
    """``sim_physics.input_fingerprints`` と同じローカル入力集合を返す。

    凍結台帳の実行時入力を別の手書き一覧へ複製しないため、S側の実際の
    fingerprint collectorを一時的に最終URDFへ向けてキーだけ取り出す。
    ``validate_freeze_manifest`` は返されたエラーを入力不整合として扱う。
    """
    old_urdf = S.URDF_PATH
    old_output = S.FINGERPRINT_OUTPUT_ROOT
    try:
        S.URDF_PATH = Path(urdf_path).resolve()
        S.FINGERPRINT_OUTPUT_ROOT = None
        keys = S.input_fingerprints(output_root=None)
    except Exception as exc:  # noqa: BLE001
        return [], str(exc)
    finally:
        S.URDF_PATH = old_urdf
        S.FINGERPRINT_OUTPUT_ROOT = old_output
    paths = []
    external = []
    for key in keys:
        if str(key).startswith('$'):
            external.append(str(key))
            continue
        paths.append(resolve_manifest_path(key, base=ROOT))
    if external:
        return paths, f'non-reproducible runtime fingerprint keys: {external}'
    return paths, None


def validate_freeze_manifest(case, *, require_generation_roles=False):
    """凍結台帳の実体SHAと、最終URDF/全参照STL/制御入力を突合する。

    ``geometry_freeze_hash`` は台帳ファイル自身のSHA-256とする。台帳の
    行に記録された各ファイルも現在値を再計算するため、URDFを古い質量で
    残したまま新しいSTLだけ差し替える組合せは最終入口で拒否される。
    """
    options=case.get('model',{}) if isinstance(case,dict) else {}
    path=_freeze_manifest_path(case)
    expected_manifest_sha=(options.get('geometry_freeze_hash',options.get('freeze_hash'))
                          or case.get('geometry_freeze_hash') or case.get('freeze_hash'))
    expected_time=(options.get('geometry_freeze_time',options.get('freeze_time'))
                   or case.get('geometry_freeze_time') or case.get('freeze_time'))
    report={'status':'UNVERIFIED','manifest_path':None if path is None else str(path),
            'manifest_sha256':None,'expected_manifest_sha256':expected_manifest_sha,
            'geometry_freeze_time':expected_time,'missing':[],'mismatches':[],
            'checked_files':0,'required_files':[]}
    if path is None:
        report['missing'].append('freeze_manifest path')
        return report
    if not path.is_file():
        report['missing'].append(f'freeze_manifest exists: {path}')
        return report
    report['manifest_sha256']=sha(path)
    if not isinstance(expected_manifest_sha,str) or expected_manifest_sha.lower()!=report['manifest_sha256']:
        report['mismatches'].append({'file':str(path),'expected':expected_manifest_sha,
                                     'actual':report['manifest_sha256'],'kind':'manifest_sha256'})
    try:
        data=json.loads(path.read_text(encoding='utf-8'))
    except Exception as exc:
        report['mismatches'].append({'file':str(path),'kind':'invalid_json','error':str(exc)})
        return report
    status=data.get('status') or data.get('freeze_status')
    allowed_statuses = ((FINAL_FREEZE_STATUS,) if require_generation_roles
                        else FREEZE_OK_STATUSES)
    if status not in allowed_statuses:
        report['mismatches'].append({'kind':'freeze_status','expected':allowed_statuses,'actual':status})
    manifest_time=data.get('geometry_freeze_time',data.get('freeze_time',data.get('frozen_at')))
    if not expected_time:
        report['missing'].append('geometry_freeze_time')
    if not manifest_time:
        report['missing'].append('freeze manifest timestamp')
    elif expected_time and str(manifest_time)!=str(expected_time):
        report['mismatches'].append({'kind':'freeze_time','expected':expected_time,'actual':manifest_time})
    rows=_freeze_rows(data)
    by_path={}
    for row in rows:
        raw=row['path']
        if raw.startswith('$'):
            report['mismatches'].append({'file':raw,'kind':'non_reproducible_path'})
            continue
        file_path=resolve_manifest_path(raw,base=path.parent)
        key=str(file_path)
        old=by_path.get(key)
        if old is not None and old.get('sha256')!=row.get('sha256'):
            report['mismatches'].append({'file':raw,'kind':'duplicate_hash_conflict'})
        by_path[key]=dict(row,path=file_path)
    for key,row in by_path.items():
        file_path=Path(key);expected=row.get('sha256')
        if not file_path.is_file():
            report['mismatches'].append({'file':str(file_path),'expected':expected,'actual':None,'kind':'missing_file'})
            continue
        actual=sha(file_path);report['checked_files']+=1
        if not isinstance(expected,str) or expected.lower()!=actual:
            report['mismatches'].append({'file':str(file_path),'expected':expected,'actual':actual,'kind':'file_sha256'})

    explicit=options.get('urdf_path') or options.get('model_urdf') or options.get('model_path')
    final_path=resolve_path(explicit or (ROOT/'hardware/urdf-print-first/tachikoma.urdf'))
    required=[final_path]
    required.extend(ROOT/p for p in FREEZE_REQUIRED_SOURCES)
    if require_generation_roles:
        runtime_paths, runtime_error = runtime_input_paths(final_path)
        report['runtime_input_files'] = [str(p) for p in runtime_paths]
        if runtime_error:
            report['mismatches'].append({
                'kind': 'runtime_input_fingerprints',
                'error': runtime_error,
            })
        required.extend(runtime_paths)
    # assembly_context の入力台帳自身と、その行が参照する各STLを含める。
    # 役割を失うと body の台帳を feet として通す余地が生じるため、
    # generation contract へ明示 role も渡す。
    assembly_paths=[]
    assembly_entries=[]
    assembly_entry_seen=set()
    assembly_role_by_key={
        'assembly_manifest':'body', 'body_manifest':'body',
        'feet_manifest':'feet', 'foot_assembly':'feet',
        'foot_contact_reference':'feet', 'leg_manifest':'legs',
        'geometry_manifest':'body',
    }
    for key in ('assembly_manifest','body_manifest','feet_manifest','foot_assembly',
                'foot_contact_reference','leg_manifest','geometry_manifest'):
        value=options.get(key) or case.get(key)
        values=_manifest_path_values(value)
        required.extend(values)
        assembly_paths.extend(values)
        role=assembly_role_by_key.get(key)
        # Only the explicitly supplied JSON is an assembly manifest for role
        # validation. Recursive path values include provenance JSON (for
        # example the XIAO plan); treating those as a second body manifest
        # creates a false duplicate-role failure while still retaining them in
        # the required SHA closure above.
        if role is not None and value:
            candidate=resolve_path(value)
            if candidate.suffix.lower() == '.json':
                marker=(role,str(candidate.resolve()))
                if marker not in assembly_entry_seen:
                    assembly_entry_seen.add(marker)
                    assembly_entries.append({'role':role,'path':candidate})
    final_root=None
    if final_path.is_file():
        try:
            final_root=ET.parse(final_path).getroot()
        except (ET.ParseError,OSError) as exc:
            report['mismatches'].append({'file':str(final_path),'kind':'invalid_final_urdf',
                                         'error':str(exc)})
    # A strict final freeze records the exact mesh bundle closure.  Checking
    # only the SHA rows below would allow a hand-edited manifest to omit an
    # orphaned STL or to claim a different count while all listed files still
    # hash correctly.  Keep this gate behind ``require_generation_roles`` so
    # legacy sensitivity fixtures can continue to use their lightweight
    # freeze schema.
    if require_generation_roles:
        bundle=data.get('final_mesh_bundle') if isinstance(data,dict) else None
        if not isinstance(bundle,dict):
            report['mismatches'].append({
                'kind':'final_mesh_bundle_metadata',
                'expected':'object with exact URDF reference closure',
                'actual':None if bundle is None else type(bundle).__name__,
            })
        else:
            mesh_dir=(final_path.parent/'meshes').resolve()
            referenced_meshes=[]
            for mesh in (final_root.findall('.//mesh')
                         if final_root is not None else []):
                filename=mesh.get('filename','')
                if not filename:
                    continue
                referenced_meshes.append(resolve_path(filename,base=final_path.parent))
            referenced_set={Path(item).resolve() for item in referenced_meshes}
            bundle_files={path.resolve() for path in mesh_dir.rglob('*')
                          if path.is_file() and path.suffix.lower() in
                          {'.stl','.obj','.3mf'}} if mesh_dir.is_dir() else set()
            outside=sorted(path for path in referenced_set
                           if not path.is_relative_to(mesh_dir))
            orphan=sorted(bundle_files-referenced_set)
            expected_counts={
                'referenced_count':len(referenced_set),
                'files_count':len(bundle_files),
                'orphan_count':len(orphan),
                'referenced_outside_bundle_count':len(outside),
            }
            report['final_mesh_bundle']={
                'directory':str(mesh_dir),
                'referenced_paths':[str(path) for path in sorted(referenced_set)],
                'orphan_paths':[str(path) for path in orphan],
                'outside_paths':[str(path) for path in outside],
                'expected_counts':expected_counts,
                'declared_counts':{
                    key:bundle.get(key) for key in expected_counts},
            }
            declared_directory=bundle.get('directory')
            if declared_directory not in (None, str(mesh_dir),
                                           public_path(mesh_dir)):
                report['mismatches'].append({
                    'kind':'final_mesh_bundle_directory',
                    'expected':str(mesh_dir),
                    'actual':declared_directory,
                })
            for key,expected in expected_counts.items():
                actual=bundle.get(key)
                if (isinstance(actual,bool) or not isinstance(actual,int)
                        or actual != expected):
                    report['mismatches'].append({
                        'kind':'final_mesh_bundle_count',
                        'field':key,
                        'expected':expected,
                        'actual':actual,
                    })
            if orphan:
                report['mismatches'].append({
                    'kind':'final_mesh_bundle_orphan',
                    'paths':[str(path) for path in orphan],
                })
            if outside:
                report['mismatches'].append({
                    'kind':'final_mesh_bundle_outside_reference',
                    'paths':[str(path) for path in outside],
                })
            if referenced_set != bundle_files:
                report['mismatches'].append({
                    'kind':'final_mesh_bundle_reference_set',
                    'missing_from_directory':[str(path) for path in sorted(referenced_set-bundle_files)],
                    'unreferenced_files':[str(path) for path in orphan],
                })
    for mesh in (final_root.findall('.//mesh') if final_root is not None else []):
        filename=mesh.get('filename','')
        if filename:
            required.append(resolve_path(filename,base=final_path.parent))
    dedup=[];seen=set()
    for file_path in required:
        file_path=Path(file_path).resolve()
        if str(file_path) not in seen:
            seen.add(str(file_path));dedup.append(file_path)
    report['required_files']=[str(p) for p in dedup]
    for file_path in dedup:
        row=by_path.get(str(file_path))
        if row is None:
            report['missing'].append(f'freeze manifest row: {file_path}')
    # Legacy freeze fixtures may contain a single lightweight feet reference.
    # The strict body/legs/feet schema is required by the print-first checker
    # and final_case_requirements, while the generic freeze helper remains
    # backward-compatible for older non-final tests.
    entry_roles={entry.get('role') for entry in assembly_entries}
    if require_generation_roles or {'body', 'legs', 'feet'} <= entry_roles:
        generation_contract=print_first_assembly_generation_contract(
            assembly_entries, require_roles=require_generation_roles)
    else:
        generation_contract={'status':'NOT_APPLICABLE', 'checked':[], 'roles':[],
                             'missing':[], 'mismatches':[], 'source_files':[]}
    report['assembly_generation_contract']=generation_contract
    if generation_contract.get('status') not in ('PASS', 'NOT_APPLICABLE'):
        report['mismatches'].append({
            'kind': 'assembly_generation_contract',
            'details': generation_contract})
    if not report['missing'] and not report['mismatches']:
        report['status']='PASS'
    return report


def _manifest_files(value):
    """台帳/生成束のファイル列を返す（入力値が無い場合は空列）。"""
    if not value:
        return []
    path=resolve_path(value)
    if path.is_file():
        return [path]
    if path.is_dir():
        return sorted(p for p in path.rglob('*') if p.is_file())
    return []


def publicize(value, output_root):
    """結果へ保存する値から、実行用の絶対パスだけを公開キーへ置き換える。"""
    if isinstance(value, dict):
        return {key: publicize(item, output_root) for key, item in value.items()}
    if isinstance(value, list):
        return [publicize(item, output_root) for item in value]
    if isinstance(value, tuple):
        return [publicize(item, output_root) for item in value]
    if isinstance(value, Path):
        return public_path(value, output_root)
    if isinstance(value, str) and value.startswith('/'):
        return public_path(Path(value), output_root)
    return value


def model_manifest(case, model_path, source_urdf, out):
    """モデル、参照メッシュ、凍結情報を絶対パスなしで記録する。"""
    options=case.get('model',{})
    model_path=Path(model_path).resolve();source_urdf=Path(source_urdf).resolve();out=Path(out).resolve()
    mesh_rows=[]
    root=ET.parse(model_path).getroot()
    seen=set()
    for mesh in root.findall('.//mesh'):
        filename=mesh.get('filename','')
        path=resolve_path(filename,base=model_path.parent)
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        mesh_rows.append({'path':public_path(path,out),'sha256':sha(path)})
    assembly_rows=[]
    for key in ('assembly_manifest','body_manifest','feet_manifest','foot_assembly',
                'foot_contact_reference','leg_manifest','geometry_manifest',
                'freeze_manifest'):
        value=options.get(key) or case.get(key)
        for path in _manifest_files(value):
            assembly_rows.append({'role':key,'path':public_path(path,out),'sha256':sha(path)})
    # 明示しない場合でも、既知の新構成束を推測して混ぜない。呼び出し側が
    # path を指定したものだけを入力台帳へ入れ、旧モデルとの混同を防ぐ。
    freeze={k:options[k] for k in ('geometry_freeze_time','geometry_freeze_hash',
                                   'freeze_time','freeze_hash') if k in options}
    for key in ('geometry_freeze_time','geometry_freeze_hash','freeze_time','freeze_hash'):
        if key not in freeze and key in case:
            freeze[key]=case[key]
    root_specs=options.get('pla_phi10_plug_roots')
    final_requirements=final_case_requirements(case,raise_on_missing=False)
    freeze_validation=validate_freeze_manifest(case)
    voltage_limits=voltage_limit_report(case,model_path)
    parent_filtering=options.get('include_parent_collision') is not True
    audit_path=options.get('self_collision_audit_path')
    return {
        'model_kind':options.get('model_kind','legacy_sensitivity'),
        'model_path':public_path(model_path,out),
        'model_sha256':sha(model_path),
        'source_urdf':public_path(source_urdf,out),
        'source_urdf_sha256':sha(source_urdf),
        'referenced_meshes':mesh_rows,
        'assembly_inputs':assembly_rows,
        'geometry_freeze':freeze,
        'freeze_validation':publicize(freeze_validation,out),
        'contact_model':options.get('contact_model','linked-hulls'),
        'voltage_limits':voltage_limits,
        'self_collision':bool(options.get('self_collision',False)),
        'include_servo_collision':bool(options.get('include_servo_collision',False)),
        'include_parent_collision':bool(options.get('include_parent_collision',False)),
        'self_collision_audit_required':bool(options.get('self_collision',False)),
        'final_case_requirements':publicize(final_requirements,out),
        'self_collision_interpretation':{
            'mujoco_self_collision_enabled':bool(options.get('self_collision',False)),
            'servo_geometry_included':bool(options.get('include_servo_collision',False)),
            'parent_link_filtering_may_apply':parent_filtering,
            'finite_native_trace_mesh_audit_required':bool(options.get('self_collision',False)),
            'audit_declared':bool(options.get('finite_native_trace_mesh_audit') is True or audit_path or options.get('self_collision_audit')),
            'finite_native_trace_mesh_audit_alias_used':bool(
                'finite_native_trace_mesh_audit' not in options
                and options.get('full_reachable_mesh_audit') is True),
            'audit_evidence_path':None if not audit_path else public_path(resolve_path(audit_path),out),
            'audit_status':'UNVERIFIED_AUDIT_REQUIRED',
            'method':'tools/sim_self_collision.py plus the complete specified finite native-trace real-mesh check; MuJoCo convex contacts and floor-only contact are not sufficient for a final whole-robot PASS.'
        },
        'pla_phi10_plug_roots':publicize(root_specs,out),
        'foot_contact_reference':publicize(options.get('foot_contact_reference'),out),
        'interpretation':'モデルの形状・参照メッシュ・生成束の内容ハッシュを記録。凍結台帳は実ファイルSHAを再計算し、URDF/STL/config/firmware入力の混在を拒否する。物理実証や印刷適合を意味しない。'
    }


@contextmanager
def python_profile(profile):
    """予備選別/Python補助計算もC++複製の定数へ合わせる。正規ファイルは不変。"""
    profile=dict(DEFAULT,**profile)
    sg=S.sg
    attrs={'ORIGIN':np.c_[np.cos(S.sg.MOUNT),np.sin(S.sg.MOUNT)]*profile['hip_r'],'STANCE_R':profile['stance_r'],'STANCE_OFF':np.array([0.,profile['stance_off_y']]),
      'STEP_H':profile['step_h'],'MAX_STEP':profile['max_step'],
      'MAX_TURN':math.radians(profile['max_turn_deg']),'PHASE_OFF':profile['phase_off'],
      'DUTY':profile['duty'],'SWAY_MM':np.array(profile['sway_mm']),'SWAY_LEAD':profile['sway_lead']}
    old={k:getattr(sg,k) for k in attrs};old_driver=S.PhaseDriver;old_target=sg.foot_target
    for k,v in attrs.items():setattr(sg,k,v)
    class FloatPhaseDriver:
        def __init__(self):self.phase=0.;self.holding=True
        def step(self,dt,vx,vy,wz):
            f=np.float32;phase=f(self.phase);delta=f(f(dt)/f(profile['cycle_t']))
            if min(1.,math.hypot(vx,vy)+abs(wz))>.05:
                self.phase=float(f(math.fmod(float(f(phase+delta)),1.)));self.holding=False
            elif not self.holding:
                # Match gait.h exactly: adding epsilon before ceil can skip
                # the 1.0 boundary and leave the stop state moving forever.
                q=f(math.ceil(float(f(phase/f(.25))))*f(.25));nxt=f(phase+delta)
                if nxt>=q:self.phase=float(f(math.fmod(float(q),1.)));self.holding=True
                else:self.phase=float(nxt)
            return self.phase
    S.PhaseDriver=FloatPhaseDriver
    if profile.get('path_shape')=='smooth':
        def smooth_target(leg,phase,vx,vy,wz,body_h=S.BODY_H_DEFAULT,*,holding=False):
            nx,ny=sg.neutral_xy(leg);turn=wz*sg.MAX_TURN
            sx=vx*sg.MAX_STEP+nx*math.cos(turn)-ny*math.sin(turn)-nx
            sy=vy*sg.MAX_STEP+nx*math.sin(turn)+ny*math.cos(turn)-ny
            sn=math.hypot(sx,sy)
            if sn>sg.MAX_STEP:sx*=sg.MAX_STEP/sn;sy*=sg.MAX_STEP/sn
            p=(phase+sg.PHASE_OFF[leg])%1
            if p<sg.DUTY:t=p/sg.DUTY;factor=.5-t;dz=0.
            else:
                t=(p-sg.DUTY)/(1-sg.DUTY);a=(1-sg.DUTY)/sg.DUTY
                factor=-.5-a*t+(3+3*a)*t*t-(2+2*a)*t*t*t
                dz=sg.STEP_H*16*t*t*(1-t)*(1-t)
            swx,swy=(0.,0.) if holding else sg.sway_of(phase)
            x=nx+sx*factor-swx-sg.ORIGIN[leg,0];y=ny+sy*factor-swy-sg.ORIGIN[leg,1]
            c,s=math.cos(-sg.MOUNT[leg]),math.sin(-sg.MOUNT[leg]);lx,ly=x*c-y*s,x*s+y*c;lz=-body_h+dz
            dd=-lz;rr=math.hypot(lx,ly)
            if dd<sg.D_KNEE_MAX:
                maximum=sg.COXA+math.sqrt(sg.D_KNEE_MAX**2-dd**2)
                if rr>maximum:lx*=maximum/rr;ly*=maximum/rr
            if dd<sg.D_KNEE_MIN and rr>.1:
                minimum=sg.COXA+math.sqrt(sg.D_KNEE_MIN**2-dd**2)
                if rr<minimum:lx*=minimum/rr;ly*=minimum/rr
            return lx,ly,lz
        sg.foot_target=smooth_target
    try:yield
    finally:
        for k,v in old.items():setattr(sg,k,v)
        S.PhaseDriver=old_driver;sg.foot_target=old_target


def static_evaluate(profile,mass_kg=2.726350248,cg_xy=(0.,-44.),phases=64):
    """実出力のヨー制約後のFK、鉛直点荷重のみ。樹脂/接触/動力はrunで別評価。"""
    profile=dict(DEFAULT,**profile)
    peak_hip=peak_knee=0.;min_margin=math.inf;min_load=math.inf;fail=clamps=0
    commands=((0.,1.,0.),(0.,-1.,0.),(1.,0.,0.),(-1.,0.,0.),(0.,0.,1.),(0.,0.,-1.))
    cg=np.array(cg_xy);angles_min=np.full(3,np.inf);angles_max=-angles_min
    with python_profile(profile):
        for cmd in commands:
            for phase in np.arange(phases)/phases:
                feet=[];angles=[];stance=[]
                for leg in range(4):
                    foot=S.sg.foot_target(leg,phase,*cmd,profile['body_h'])
                    a=S.sg.leg_ik(*foot)
                    if a is None:fail+=1;a=(0.,0.,0.)
                    angles.append(a)
                    p=(phase+profile['phase_off'][leg])%1
                    if p<profile['duty'] or (profile['step_h']*(16*((p-profile['duty'])/(1-profile['duty']))**2*(1-(p-profile['duty'])/(1-profile['duty']))**2 if profile.get('path_shape')=='smooth' else math.sin(math.pi*(p-profile['duty'])/(1-profile['duty']))))<1.:
                        stance.append(leg)
                actual=S.clamp_leg_angles(np.array(angles))
                clamps+=int(np.max(abs(actual-np.array(angles)))>.05)
                angles_min=np.minimum(angles_min,actual.min(axis=0));angles_max=np.maximum(angles_max,actual.max(axis=0))
                for leg,a in enumerate(actual):
                    x,y,z=S.sg.leg_fk(*a);angle=S.sg.MOUNT[leg];c,s=math.cos(angle),math.sin(angle)
                    feet.append([S.sg.ORIGIN[leg,0]+c*x-s*y,S.sg.ORIGIN[leg,1]+s*x+c*y])
                points=np.array(feet)[stance];center=points.mean(axis=0)
                polygon=points[np.argsort(np.arctan2(points[:,1]-center[1],points[:,0]-center[0]))]
                margin=min(((b-a)[0]*(cg-a)[1]-(b-a)[1]*(cg-a)[0])/np.linalg.norm(b-a) for a,b in zip(polygon,np.roll(polygon,-1,axis=0)))
                min_margin=min(min_margin,float(margin))
                A=np.vstack([np.ones(len(points)),points.T]);b=mass_kg*9.81*np.array([1.,*cg]);loads=np.linalg.lstsq(A,b,rcond=None)[0]
                min_load=min(min_load,float(loads.min()))
                for leg,load in zip(stance,loads):
                    a=actual[leg];x,y,_=S.sg.leg_fk(*a);rad=math.hypot(x,y)
                    peak_hip=max(peak_hip,float(max(load,0)*abs(rad-S.sg.COXA)/1000))
                    knee_r=S.sg.COXA+S.sg.FEMUR*math.cos(math.radians(a[1]))
                    peak_knee=max(peak_knee,float(max(load,0)*abs(rad-knee_r)/1000))
    return {'profile':profile,'mass_kg':mass_kg,'cg_xy_mm':list(cg_xy),
      'commands':len(commands),'phases':phases,'ik_failures':fail,'yaw_clamped_frames':clamps,
      'min_static_margin_mm':min_margin,'minimum_unilateral_load_N':min_load,
      'max_pitch_torque_nm':peak_hip,'max_knee_torque_nm':peak_knee,
      'angle_min_deg':angles_min.tolist(),'angle_max_deg':angles_max.tolist(),
      'screen_pass':fail==0 and min_margin>=8. and min_load>=-1e-6,
      'interpretation':'quasi-static equivalent foot points, minimum-norm vertical loads; excludes foot shape, limb inertia, motor self-mass moment and material strength'}


def scan(out):
    out.mkdir(parents=True,exist_ok=True)
    profiles=[]
    for h,r,off,sway in itertools.product((115.,125.,135.,145.,155.),(85.,95.,105.,115.,129.),(-30.,-20.,-10.),(.65,.85,1.)):
        p=dict(DEFAULT,body_h=h,stance_r=r,stance_off_y=off,step_h=8.,max_step=18.,max_turn_deg=6.,cycle_t=2.4,duty=.82,arm_swing_deg=0.,sway_mm=[34*sway,34*sway,40*sway,40*sway])
        profiles.append(p)
    save(out/'scan-plan.json',{'created_utc':datetime.now(timezone.utc).isoformat(),
        'source_sha256':T.input_fingerprints({}),'tool_sha256':sha(__file__),
        'criteria':{'static_margin_min_mm':8,'ik_failures':0,'ranking':'max hip/knee torque after feasibility','phases':64},
        'cg_xy_mm':[0,-44],'profiles':profiles})
    rows=[static_evaluate(DEFAULT)]
    for i,p in enumerate(profiles):
        rows.append(static_evaluate(p))
        if i%30==0:print('scan',i+1,'/',len(profiles),flush=True)
    rows.sort(key=lambda r:(not r['screen_pass'],max(r['max_pitch_torque_nm'],r['max_knee_torque_nm'])))
    save(out/'static-scan.json',rows)
    passed=[r for r in rows if r['screen_pass']]
    selected=[]
    for row in passed:
        p=row['profile']
        if any(abs(p['body_h']-q['body_h'])<10 and abs(p['stance_r']-q['stance_r'])<10 for q in selected):continue
        selected.append(p)
        if len(selected)>=4:break
    profiles={'baseline':DEFAULT,'slow_only':dict(DEFAULT,max_step=18.,step_h=8.,cycle_t=2.4,duty=.82,arm_swing_deg=0.),
              # canonical 候補は config.py から読む。adopted=False のまま
              # なので量産選定や通常profileの上書きには使わない。
              'print_first_candidate':PRINT_FIRST_PROFILE}
    profiles.update({f'low_load_{i+1}':p for i,p in enumerate(selected)})
    save(out/'profiles.json',profiles)
    print('feasible',len(passed),'of',len(rows),'selected',profiles,flush=True)


def _render_profile_header(profile):
    """ケースprofileから局所候補ヘッダーを生成する。

    候補探索はこの関数で出力ディレクトリ内のヘッダーだけを作る。凍結
    最終ケースは下の ``prepare_native(..., profile_mode='frozen_print_first')``
    が config.py から生成済みの正規ヘッダーをそのまま検査して使うため、
    firmware/src/config.h を書き換える旧経路とは分離される。
    """
    p=dict(DEFAULT,**profile)
    source=ROOT/'hardware/src/config.py'
    source_sha=sha(source)
    status=str(p.get('profile_status','CANDIDATE_LOCAL'))
    adopted='true' if bool(p.get('profile_adopted',False)) else 'false'
    off_x=float(p.get('stance_off_x',0.))
    off_y=float(p['stance_off_y'])
    origins=np.c_[np.cos(S.sg.MOUNT),np.sin(S.sg.MOUNT)]*float(p['hip_r'])
    f=lambda value:f'{float(value):.6f}f'
    lines=[
        '#pragma once',
        '// GENERATED for a local print-first candidate; do not edit.',
        f'// source: hardware/src/config.py#PRINT_FIRST_GAIT sha256={source_sha}',
        '// This header is used only when TACHIKOMA_PRINT_FIRST_PROFILE=1.',
        f'constexpr bool PRINT_FIRST_PROFILE_ADOPTED = {adopted};',
        f'constexpr const char* PRINT_FIRST_PROFILE_STATUS = {json.dumps(status)};',
        f'constexpr float PRINT_FIRST_BODY_H = {f(p["body_h"])};',
        f'constexpr float PRINT_FIRST_STANCE_R = {f(p["stance_r"])};',
        f'constexpr float PRINT_FIRST_STANCE_OFF_X = {f(off_x)};',
        f'constexpr float PRINT_FIRST_STANCE_OFF_Y = {f(off_y)};',
        f'constexpr float PRINT_FIRST_STEP_H = {f(p["step_h"])};',
        f'constexpr float PRINT_FIRST_MAX_STEP = {f(p["max_step"])};',
        f'constexpr float PRINT_FIRST_MAX_TURN_DEG = {f(p["max_turn_deg"])};',
        f'constexpr float PRINT_FIRST_CYCLE_T = {f(p["cycle_t"])};',
        f'constexpr float PRINT_FIRST_DUTY = {f(p["duty"])};',
        f'constexpr float PRINT_FIRST_SWAY_MM[4] = {{{", ".join(f(v) for v in p["sway_mm"])}}};',
        f'constexpr float PRINT_FIRST_SWAY_LEAD = {f(p["sway_lead"])};',
        f'constexpr float PRINT_FIRST_PHASE_OFF[4] = {{{", ".join(f(v) for v in p["phase_off"])}}};',
        f'constexpr float PRINT_FIRST_ARM_SWING_DEG = {f(p["arm_swing_deg"])};',
        f'constexpr float PRINT_FIRST_HIP_R = {f(p["hip_r"])};',
        'constexpr float PRINT_FIRST_LEG_ORIGIN[4][2] = {',
        *[f'    {{{f(x)}, {f(y)}}},' for x,y in origins],
        '};',
        '',
    ]
    return '\n'.join(lines),source_sha


def _check_profile_header(profile, header, *, mode):
    """コンパイル前にヘッダー数値とケースprofileの一致を検査する。"""
    text=Path(header).read_text(encoding='utf-8')
    expected=dict(DEFAULT,**profile)
    scalar={
        'PRINT_FIRST_BODY_H':expected['body_h'],
        'PRINT_FIRST_STANCE_R':expected['stance_r'],
        'PRINT_FIRST_STANCE_OFF_X':expected.get('stance_off_x',0.),
        'PRINT_FIRST_STANCE_OFF_Y':expected['stance_off_y'],
        'PRINT_FIRST_STEP_H':expected['step_h'],
        'PRINT_FIRST_MAX_STEP':expected['max_step'],
        'PRINT_FIRST_MAX_TURN_DEG':expected['max_turn_deg'],
        'PRINT_FIRST_CYCLE_T':expected['cycle_t'],
        'PRINT_FIRST_DUTY':expected['duty'],
        'PRINT_FIRST_SWAY_LEAD':expected['sway_lead'],
        'PRINT_FIRST_ARM_SWING_DEG':expected['arm_swing_deg'],
        'PRINT_FIRST_HIP_R':expected['hip_r'],
    }
    errors=[]
    for name,value in scalar.items():
        match=re.search(rf'constexpr\s+float\s+{name}\s*=\s*([-+0-9.eE]+)f',text)
        if match is None or not math.isclose(float(match.group(1)),float(value),rel_tol=1e-7,abs_tol=1e-7):
            errors.append(f'{name} expected {value}')
    for name,values in (('PRINT_FIRST_SWAY_MM',expected['sway_mm']),('PRINT_FIRST_PHASE_OFF',expected['phase_off'])):
        match=re.search(rf'{name}\[4\]\s*=\s*\{{([^}}]+)\}}',text)
        actual=[] if match is None else [float(v) for v in re.findall(r'[-+0-9.eE]+',match.group(1))]
        if len(actual)!=4 or any(not math.isclose(a,float(v),rel_tol=1e-7,abs_tol=1e-7) for a,v in zip(actual,values)):
            errors.append(f'{name} expected {values}')
    status=str(expected.get('profile_status','CANDIDATE_LOCAL'))
    status_match=re.search(r'PRINT_FIRST_PROFILE_STATUS\s*=\s*"([^"]*)"',text)
    if status_match is None or status_match.group(1)!=status:
        errors.append(f'PRINT_FIRST_PROFILE_STATUS expected {status}')
    adopted='true' if bool(expected.get('profile_adopted',False)) else 'false'
    adopted_match=re.search(r'PRINT_FIRST_PROFILE_ADOPTED\s*=\s*(true|false)',text)
    if adopted_match is None or adopted_match.group(1)!=adopted:
        errors.append(f'PRINT_FIRST_PROFILE_ADOPTED expected {adopted}')
    source_sha=sha(ROOT/'hardware/src/config.py')
    if f'hardware/src/config.py#PRINT_FIRST_GAIT sha256={source_sha}' not in text:
        errors.append('print-first header source config SHA is stale')
    if errors:
        raise ValueError(f'print-first header/profile mismatch ({mode}): {"; ".join(errors)}')
    return {'mode':mode,'header':public_path(header),'source_config_sha256':source_sha,
            'status':status,'adopted':bool(expected.get('profile_adopted',False)),
            'numeric_values_match':True}


def prepare_native(profile,folder,*,profile_mode='candidate_print_first',
                   freeze_manifest=None):
    """実firmware複製を print-first header + compile flag で構築する。

    最終統合では正規 ``firmware/src/print_first_gait.h`` と凍結 config.py を
    読む。探索候補では同じ出力ディレクトリに局所ヘッダーを生成する。
    どちらも ``config.h`` を変更せず、``-DTACHIKOMA_PRINT_FIRST_PROFILE=1``
    の脱落を build.json に残す。
    """
    folder.mkdir(parents=True,exist_ok=True);source=ROOT/'firmware/src'
    (folder/'bin').mkdir(exist_ok=True)
    if (folder/'compare').exists():
        (folder/'compare').rename(folder/'bin'/'legacy_compare')
    for name in NATIVE_PROFILE_HEADERS:
        p=source/name
        if not p.is_file():
            raise FileNotFoundError(f'native profile header missing: {p}')
        shutil.copy2(p,folder/name)
    if profile_mode=='frozen_print_first':
        header_info=_check_profile_header(profile,folder/'print_first_gait.h',mode=profile_mode)
    elif profile_mode=='candidate_print_first':
        header_text,source_sha=_render_profile_header(profile)
        (folder/'print_first_gait.h').write_text(header_text,encoding='utf-8')
        header_info=_check_profile_header(profile,folder/'print_first_gait.h',mode=profile_mode)
        header_info['generated_from_config_sha256']=source_sha
    else:
        raise ValueError(f'unknown native profile mode: {profile_mode}')
    src=ROOT/'tools/tests/simulation_output_trace.cpp';shutil.copy2(src,folder/'trace.cpp')
    binary=folder/'bin'/'output_trace'
    command=['c++','-std=c++17','-O2','-DTACHIKOMA_PRINT_FIRST_PROFILE=1',
             '-I',str(ROOT/'tools/tests/firmware_stubs'),'-I',str(folder),
             str(folder/'trace.cpp'),'-o',str(binary)]
    proc=subprocess.run(command,check=True,capture_output=True,text=True)
    display_command=[public_path(arg,folder.parents[1]) if isinstance(arg,str) and arg.startswith('/') else arg
                     for arg in command]
    output_root = folder.parents[1]
    compile_flag = '-DTACHIKOMA_PRINT_FIRST_PROFILE=1'
    source_headers = []
    for name in NATIVE_PROFILE_HEADERS:
        source_path = source / name
        bundle_path = folder / name
        source_headers.append({
            'name': name,
            'source_path': public_path(source_path, output_root),
            'source_sha256': sha(source_path),
            'bundle_path': public_path(bundle_path, output_root),
            'bundle_sha256': sha(bundle_path),
        })
    build = {
        'schema_version': 2,
        'command': display_command,
        'compiler_command': display_command,
        'compile_flags': ['-std=c++17', '-O2', compile_flag],
        'include_dirs': [
            public_path(ROOT / 'tools/tests/firmware_stubs', output_root),
            public_path(folder, output_root),
        ],
        'profile_mode': profile_mode,
        'build_mode': profile_mode,
        'print_first_compile_flag': compile_flag,
        'header_contract': header_info,
        # Preserve the old flat bundle map for consumers that already read it,
        # while the per-file records below bind both the source and copied
        # header bytes independently.
        'source_sha256': {p.name: sha(p) for p in folder.glob('*.h')},
        'source_headers': source_headers,
        'source_config_path': public_path(ROOT / 'hardware/src/config.py', output_root),
        'source_config_sha256': sha(ROOT / 'hardware/src/config.py'),
        'trace_path': public_path(folder / 'trace.cpp', output_root),
        'trace_sha256': sha(folder / 'trace.cpp'),
        'trace_source_path': public_path(ROOT / 'tools/tests/simulation_output_trace.cpp', output_root),
        'trace_source_sha256': sha(ROOT / 'tools/tests/simulation_output_trace.cpp'),
        'binary_path': public_path(binary, output_root),
        'binary_sha256': sha(binary),
        'stdout': proc.stdout,
        'stderr': proc.stderr,
    }
    if freeze_manifest is not None:
        freeze_path = Path(freeze_manifest).resolve()
        if not freeze_path.is_file():
            raise FileNotFoundError(f'freeze manifest does not exist: {freeze_path}')
        build['freeze_manifest_path'] = public_path(freeze_path, output_root)
        build['freeze_manifest_sha256'] = sha(freeze_path)
    save(folder/'build.json', build)
    return binary


def verify_profile(profile,folder,*,profile_mode='candidate_print_first'):
    header_info=_check_profile_header(profile,Path(folder)/'print_first_gait.h',mode=profile_mode)
    source=(ROOT/'tools/tests/simulation_firmware_trace.cpp').read_text().replace('"../../firmware/src/leg_output.h"','"leg_output.h"')
    (folder/'compare.cpp').write_text(source);binary=folder/'bin'/'control_comparison'
    command=['c++','-std=c++17','-O2','-DTACHIKOMA_PRINT_FIRST_PROFILE=1',
             '-I',str(folder),str(folder/'compare.cpp'),'-o',str(binary)]
    subprocess.run(command,check=True,capture_output=True,text=True)
    commands=[]
    for vx,vy,wz in ((0.,0.,0.),(0.,1.,0.),(1.,0.,0.),(0.,0.,1.),(0.,0.,0.),(0.,-1.,0.),(0.,0.,-1.),(0.,0.,0.)):
        commands.extend([[.02,vx,vy,wz,profile['body_h']]]*round(profile['cycle_t']*50))
    result=subprocess.run([str(binary)],input='\n'.join(' '.join(map(str,c)) for c in commands)+'\n',capture_output=True,text=True,check=True)
    native=np.loadtxt(result.stdout.splitlines());worst=0.;phase_error=0.;last={}
    with python_profile(profile):
        driver=S.PhaseDriver();output=S.LegOutputDriver()
        for i,(dt,vx,vy,wz,h) in enumerate(commands):
            phase=driver.step(dt,vx,vy,wz);target,deg=S.compute_leg_targets(phase,vx,vy,wz,last,holding=driver.holding,body_h=h)
            _,cur=output.step(target,dt)
            both=np.array([deg[n] for n in S.sg._LEGS]+[cur[n] for n in S.sg._LEGS]).ravel()
            worst=max(worst,float(abs(both-native[i,2:]).max()))
            phase_error=max(phase_error,abs((phase-native[i,0]+.5)%1-.5))
            if bool(native[i,1])==driver.holding:raise ValueError('native/Python holding mismatch')
    report={'frames':len(commands),'max_angle_error_deg':worst,'max_phase_error':phase_error,
      'ik_counts':{k:v for k,v in last.items() if k.startswith('_')},'pass':worst<.01 and phase_error<.0001 and not any(v for k,v in last.items() if k.startswith('_'))}
    report['profile_mode']=profile_mode
    report['print_first_compile_flag']='-DTACHIKOMA_PRINT_FIRST_PROFILE=1'
    report['header_contract']=header_info
    report['command']=[public_path(arg,Path(folder).parents[1]) if isinstance(arg,str) and arg.startswith('/') else arg
                       for arg in command]
    save(folder/'native-comparison.json',report)
    if not report['pass']:raise ValueError(f'native profile verification failed: {report}')
    return report


def native_trace(case,include_initial=False):
    p=case['profile'];edges=np.cumsum([s['duration'] for s in case['segments']]);commands=[]
    if include_initial:commands.append([0.,0.,0.,0.,p['body_h']])
    for i in range(round(edges[-1]*S.SERVO_HZ)):
        seg=case['segments'][min(np.searchsorted(edges,i/S.SERVO_HZ+1e-10,side='right'),len(edges)-1)]
        commands.append([1/S.SERVO_HZ,*[seg.get(k,0.) for k in ('vx','vy','wz')],seg.get('body_h',p['body_h'])])
    binary=NATIVE_BINARY_RUNTIME or case['native_binary']
    proc=subprocess.run([str(binary),'ready'],input='\n'.join(' '.join(map(str,c)) for c in commands)+'\n',capture_output=True,text=True,check=True)
    trace=T.validate_native_trace(
        np.loadtxt(proc.stdout.splitlines(),ndmin=2),
        label='print-first native trace')
    if trace.shape!=(len(commands),43):
        raise ValueError(f'print-first native trace row count mismatch: {trace.shape[0]} != {len(commands)}')
    return trace


def mass_model(case,out):
    """支持感度用にbaseの重心を平行移動。形状移動や収納成立を主張しない。"""
    root=ET.parse(S.URDF_PATH).getroot();base=root.find("link[@name='base_link']/inertial")
    xyz=np.array([float(x) for x in base.find('origin').attrib['xyz'].split()])
    mass=float(base.find('mass').get('value'));origin0=xyz.copy()
    inertia=base.find('inertia');keys=('ixx','ixy','ixz','iyy','iyz','izz')
    vals=[float(inertia.get(k)) for k in keys];I=np.array([[vals[0],vals[1],vals[2]],[vals[1],vals[3],vals[4]],[vals[2],vals[4],vals[5]]])
    def parallel(v):return np.dot(v,v)*np.eye(3)-np.outer(v,v)
    Iorigin=I+mass*parallel(xyz);first=mass*xyz
    import export_urdf as E
    new_origins=np.c_[np.cos(S.sg.MOUNT),np.sin(S.sg.MOUNT)]*case['profile']['hip_r']
    for n,leg in enumerate(E.LEGS):
        joint=root.find(f"joint[@name='leg_{leg.lower()}_yaw']/origin")
        oldjoint=np.array([float(v) for v in joint.get('xyz').split()]);delta=new_origins[n]*.001-oldjoint[:2]
        oldjoint[:2]=new_origins[n]*.001;joint.set('xyz',' '.join(map(str,oldjoint)))
        old=E.leg_servo_items(leg)[0]
        frame=E.leg_servo_frames(leg)['yaw'].copy();frame[:2,3]+=delta*1000
        if case.get('yaw_case_outward'):frame=frame@E.rot(180,'z')
        new=E.servo_mass_item(E.C.LEG_SERVO,E.SERVO_STD_G,frame,'candidate_yaw')
        first+=new.mass_kg*new.com_m-old.mass_kg*old.com_m
        Iorigin+=new.I_com+new.mass_kg*parallel(new.com_m)-old.I_com-old.mass_kg*parallel(old.com_m)
    extra=case.get('extra_mass_kg',0.)
    if extra:
        point=np.array(case.get('extra_mass_center_mm',[0,0,40]))*.001
        first+=extra*point;mass+=extra;Iorigin+=extra*parallel(point)+np.eye(3)*extra*.05**2/6
    xyz=first/mass;I=Iorigin-mass*parallel(xyz)
    xyz+=np.array(case.get('base_mass_shift_mm',[0,0,0]))*.001
    if np.linalg.eigvalsh(I).min()<=0:raise ValueError('candidate inertia is not positive')
    base.find('origin').set('xyz',' '.join(map(str,xyz)));base.find('mass').set('value',str(mass))
    for k,v in zip(keys,[I[0,0],I[0,1],I[0,2],I[1,1],I[1,2],I[2,2]]):inertia.set(k,str(v))
    group_volts=case.get('group_voltage_V',{})
    for joint in root.findall('joint'):
        name=joint.get('name');group='leg' if name.startswith('leg_') else 'arm' if name.startswith('arm_') else 'eye'
        if group in group_volts and joint.find('limit') is not None:
            limit=E.servo_limits_at_voltage(group_volts[group])[group]
            joint.find('limit').set('effort',str(limit['effort']));joint.find('limit').set('velocity',str(limit['velocity']))
    # 相対メッシュ参照を元の絶対パスへ固定し、派生URDFを別名保存する。
    for mesh in root.findall('.//mesh'):
        filename=mesh.get('filename')
        if not Path(filename).is_absolute():mesh.set('filename',str((S.URDF_PATH.parent/filename).resolve()))
    path=out/'models'/(case['name']+'.urdf');path.parent.mkdir(parents=True,exist_ok=True)
    ET.indent(root);ET.ElementTree(root).write(path,encoding='unicode',xml_declaration=True)
    return path


def select_model(case, out):
    """旧感度モデルと凍結済み統合モデルを明示的に分けて選ぶ。"""
    options=case.get('model',{})
    kind=options.get('model_kind','legacy_sensitivity')
    final=kind in FINAL_MODEL_KINDS
    explicit=options.get('urdf_path') or options.get('model_urdf') or options.get('model_path')
    source=resolve_path(options.get('source_urdf',S.URDF_PATH))
    if final:
        path=resolve_path(explicit or (ROOT/'hardware/urdf-print-first/tachikoma.urdf'))
        if not path.is_file():
            raise FileNotFoundError(f'凍結済み統合URDFが無い: {path}')
        source=path if not options.get('source_urdf') else source
        # 電圧感度は質量/形状へ旧mass_modelを通さず、ケース別URDFの
        # effort/velocityだけへ適用する。元の凍結URDFは変更しない。
        limited,_=apply_group_voltage_limits(case,path,out)
        return limited,source,True
    if explicit:
        path=resolve_path(explicit)
        if not path.is_file():raise FileNotFoundError(f'指定URDFが無い: {path}')
        return path,source,bool(options.get('assembly_context',False))
    old=S.URDF_PATH
    S.URDF_PATH=source
    try:
        path=mass_model(case,out)
    finally:
        S.URDF_PATH=old
    return path,source,False


def geometry_context(case, model_path):
    """新構成の凸分解を、生成URDFと同じ部品集合で実行する。"""
    options=case.get('model',{})
    requested=options.get('assembly_context',False) or options.get('model_kind') in FINAL_MODEL_KINDS
    if not requested:
        return nullcontext()
    # import を遅延し、旧感度探索時には生成側の設定を一切触らない。
    from print_first_assembly import context
    return context(generated=True)


class FullRateMetrics:
    """500 Hz の軸負荷と足材料別接触を収集する（印刷強度の実証ではない）。"""
    def __init__(self,case):
        self.case=case;self.records=[];self.bound_records=[];self.support=[];self.foot_trace=[];self.last_time=-1
        self.edges=np.cumsum([s['duration'] for s in case['segments']])
        self.step_fn=mujoco.mj_step;self.forward_fn=mujoco.mj_forward;self.latest=None
        self.model=None;self.idx=None;self.root_specs=self._load_root_specs(case)
        self._foot={};self._proxy_roots={};self._contact_names={}

    @staticmethod
    def _leg_from_text(value):
        match=re.search(r'(?<![A-Z])(FR|FL|RL|RR)(?![A-Z])',str(value).upper())
        return match.group(1) if match else None

    @classmethod
    def _load_root_specs(cls,case):
        options=case.get('model',{});raw=(options.get('pla_phi10_plug_roots')
             or options.get('pla_phi10_plug_roots_mm') or case.get('pla_phi10_plug_roots'))
        defaulted=False
        if raw is None and options.get('model_kind') in FINAL_MODEL_KINDS:
            # 足原点は物理 tibia 長の末端。config.py の一元値から都度作り、
            # 最終caseへ明示した入力と同じ構造で保持する。
            tibia_len=float(S.sg._C.TIBIA_LEN)
            raw={leg:{'xyz_mm':[0.,0.,-tibia_len],'frame':'tibia',
                         'link':f'leg_{leg.lower()}_tibia','axis_local':[0.,0.,1.]}
                 for leg in S.sg._LEGS};defaulted=True
        if isinstance(raw,(str,Path)):
            path=resolve_path(raw)
            if path.is_file():
                loaded=json.loads(path.read_text());raw=(loaded.get('pla_phi10_plug_roots')
                    or loaded.get('pla_phi10_plug_roots_mm') or loaded.get('roots') or loaded)
        if not isinstance(raw,dict):return {}
        specs={}
        for key,value in raw.items():
            leg=cls._leg_from_text(key)
            if leg is None:continue
            if isinstance(value,dict):
                xyz=(value.get('xyz_mm') or value.get('position_mm') or value.get('local_mm')
                     or value.get('world_mm'))
                frame='world' if value.get('world_mm') is not None else value.get('frame','tibia')
                link=value.get('link') or value.get('body')
                axis=(value.get('axis') or value.get('axis_local') or value.get('axis_world'))
            else:
                xyz=value;frame='tibia';link=None;axis=None
            if xyz is None or len(xyz)!=3:continue
            specs[leg]={'xyz_m':(np.asarray(xyz,dtype=float)*.001).tolist(),
                        'frame':str(frame).lower(),'link':link,
                        'axis':None if axis is None else np.asarray(axis,dtype=float).tolist(),
                        'axis_frame':'world' if isinstance(value,dict) and value.get('axis_world') is not None else 'tibia',
                        'source':'config.C.TIBIA_LEN' if defaulted else 'case.model.pla_phi10_plug_roots'}
        return specs

    def bind(self,m,idx):
        self.model=m;self.idx=idx

    def step(self,m,d):
        self.step_fn(m,d)
        time=float(d.time)
        self.latest=(time,d.actuator_force.copy(),d.qvel.copy())
        # sim_stress computes demand clipping before stepping, but this
        # collector is intentionally independent of that loop.  Record the
        # post-step force against the actual speed-dependent MuJoCo range so
        # the full-rate print-first report can expose continuous upper-bound
        # holding saturation.  A force within 0.5% of the bound is treated as
        # sitting on the bound; disabled/zero-range actuators are excluded.
        if self.idx and isinstance(self.idx.get('aid'),dict):
            aids=np.asarray([self.idx['aid'][n] for n in S.ALL_JOINTS],dtype=int)
            actual=np.asarray(d.actuator_force[aids],dtype=float)
            upper=np.abs(np.asarray(m.actuator_forcerange[aids,1],dtype=float))
            bound=(np.isfinite(actual) & np.isfinite(upper) & (upper>1e-12)
                   & (np.abs(actual)>=upper*(1.0-5e-3)))
            self.bound_records.append((time,actual.copy(),bound.astype(bool)))

    def _contact_info(self,m,geom):
        gname=mujoco.mj_id2name(m,mujoco.mjtObj.mjOBJ_GEOM,int(geom)) or ''
        info=(self.idx or {}).get('part_metadata',{}).get(gname,{})
        body=mujoco.mj_id2name(m,mujoco.mjtObj.mjOBJ_BODY,int(m.geom_bodyid[geom])) or ''
        link=str(info.get('link') or body);part=str(info.get('part') or gname)
        material=str(info.get('material') or '')
        if not material:
            lower=(part+' '+gname).lower()
            material=('TPU' if 'tpu' in lower or 'shoe' in lower or 'foot_pad' in lower
                      else 'PLA' if any(x in lower for x in ('plug','phi10','φ10','spacer','toe','foot'))
                      else 'MIXED')
        leg=self._leg_from_text(link) or self._leg_from_text(part) or self._leg_from_text(gname)
        # 足の荷重は tibia 上の足部および明示した足部名だけを対象にする。
        is_foot=link.endswith('_tibia') or any(x in (part+' '+gname).lower()
                                                for x in ('shoe','foot_pad','toe','plug','phi10','φ10','spacer'))
        if leg is None or not is_foot:return None
        return {'body':body,'link':link,'part':part,'material':material,'leg':leg,'geom':gname}

    def _root_position(self,leg,m,d):
        spec=self.root_specs.get(leg)
        if spec:
            xyz=np.asarray(spec['xyz_m'],float);frame=spec['frame']
            if frame in ('world','world_m'):
                return xyz,'EXPLICIT_WORLD'
            body_name=spec.get('link') or f'leg_{leg.lower()}_tibia'
            body=mujoco.mj_name2id(m,mujoco.mjtObj.mjOBJ_BODY,body_name)
            if body>=0:
                return d.xpos[body]+d.xmat[body].reshape(3,3)@xyz,'EXPLICIT_LINK'
        # 生成側が根元座標をまだ出していない場合は、プラグ/スペーサー
        # geom原点を代理にして数値を残す。これは強度判定へ使えない。
        for geom,info in self._proxy_roots.get(leg,[]):
            return d.geom_xpos[geom].copy(),'PROXY_GEOM_ORIGIN_UNVERIFIED'
        return None,'UNVERIFIED_NO_PLA_PHI10_ROOT'

    def _root_axis(self,leg,m,d):
        spec=self.root_specs.get(leg)
        if not spec or spec.get('axis') is None:return None
        axis=np.asarray(spec['axis'],dtype=float)
        if spec.get('axis_frame')=='world':
            n=np.linalg.norm(axis);return axis/n if n>1e-12 else None
        body_name=spec.get('link') or f'leg_{leg.lower()}_tibia'
        body=mujoco.mj_name2id(m,mujoco.mjtObj.mjOBJ_BODY,body_name)
        if body<0:return None
        axis=d.xmat[body].reshape(3,3)@axis
        n=np.linalg.norm(axis)
        return axis/n if n>1e-12 else None

    def _empty_foot(self):
        return {'contact_samples':0,'max_vertical_N':0.,'max_vertical_time_s':None,
                'max_horizontal_N':0.,'max_horizontal_resultant_N':0.,'max_horizontal_time_s':None,
                'tpu_contact_weight_N':0.,'_tpu_center_sum':np.zeros(3),
                'max_eccentric_moment_Nm':None,'max_eccentric_time_s':None,
                'max_eccentric_root_world_m':None,'max_eccentric_center_world_m':None,
                'max_eccentric_force_world_N':None,'max_eccentric_status':None,
                'max_root_moment_world_Nm':None,'max_root_bending_moment_Nm':None,
                'max_root_torsional_moment_Nm':None,
                '_proxy_eccentric_moment_Nm':None,'_proxy_eccentric_time_s':None,
                '_last_root':None,'_last_root_status':'UNVERIFIED_NO_PLA_PHI10_ROOT',
                '_contact_materials':{},'_contact_parts':{}}

    def _collect_foot_load(self,m,d,time,segment):
        by_leg={};force=np.zeros(6)
        ground_ids={g for g in range(m.ngeom) if int(m.geom_bodyid[g])==0}
        for ci,c in enumerate(d.contact[:d.ncon]):
            b1=int(m.geom_bodyid[c.geom1]);b2=int(m.geom_bodyid[c.geom2])
            if (c.efc_address<0 or ((c.geom1 in ground_ids)==(c.geom2 in ground_ids))):continue
            geom=c.geom2 if c.geom1 in ground_ids else c.geom1
            info=self._contact_info(m,geom)
            if info is None:continue
            mujoco.mj_contactForce(m,d,ci,force)
            local=force[:3].copy();normal=abs(float(local[0]))
            if normal<=1e-9:continue
            frame=np.asarray(c.frame).reshape(3,3)
            world=frame.T@local
            # mj_contactForce の向きを、脚へ作用する反力へ統一する。
            if c.geom2 in ground_ids:world=-world
            leg=info['leg'];row=by_leg.setdefault(leg,{'force':np.zeros(3),'vertical':0.,
                'horizontal_sum':0.,'horizontal_vec':np.zeros(2),'tpu_n':0.,
                'tpu_center_sum':np.zeros(3),'tpu_force':np.zeros(3),'tpu_contacts':[],
                'root_status':'UNVERIFIED_NO_PLA_PHI10_ROOT','contacts':0})
            row['force']+=world;row['vertical']+=abs(float(world[2]))
            h=float(np.linalg.norm(world[:2]));row['horizontal_sum']+=h
            row['horizontal_vec']+=world[:2];row['contacts']+=1
            self._contact_names[info['geom']]=self._contact_names.get(info['geom'],0)+1
            summary=self._foot.setdefault(leg,self._empty_foot())
            summary['contact_samples']+=1
            summary['_contact_materials'][info['material']]=summary['_contact_materials'].get(info['material'],0)+1
            summary['_contact_parts'][info['part']]=summary['_contact_parts'].get(info['part'],0)+1
            if row['vertical']>summary['max_vertical_N']:
                summary['max_vertical_N']=row['vertical'];summary['max_vertical_time_s']=time
            if row['horizontal_sum']>summary['max_horizontal_N']:
                summary['max_horizontal_N']=row['horizontal_sum'];summary['max_horizontal_time_s']=time
            resultant=float(np.linalg.norm(row['horizontal_vec']))
            summary['max_horizontal_resultant_N']=max(summary['max_horizontal_resultant_N'],resultant)
            if info['material'].upper()=='TPU':
                world_torque=frame.T@force[3:6].copy()
                if c.geom2 in ground_ids:world_torque=-world_torque
                row['tpu_n']+=normal;row['tpu_center_sum']+=c.pos*normal;row['tpu_force']+=world
                row['tpu_contacts'].append({'pos':c.pos.copy(),'force':world.copy(),
                                            'torque':world_torque})
                summary['tpu_contact_weight_N']+=normal;summary['_tpu_center_sum']+=c.pos*normal
            # 明示座標が無ければ、荷重中のPLAスペーサー/プラグを代理根元候補へ。
            lower=(info['part']+' '+info['geom']).lower()
            if any(x in lower for x in ('plug','phi10','φ10','spacer')):
                self._proxy_roots.setdefault(leg,[]).append((int(geom),info))
        roots={}
        for leg,row in by_leg.items():
            summary=self._foot[leg]
            center=(row['tpu_center_sum']/row['tpu_n'] if row['tpu_n']>0 else None)
            root,status=self._root_position(leg,m,d)
            roots[leg]=(root,status)
            summary['_last_root']=None if root is None else root.copy()
            summary['_last_root_status']=status
            if center is not None and root is not None:
                moment_vec,moment,_=contact_root_moment(row['tpu_contacts'],root)
                axis=self._root_axis(leg,m,d)
                _,_,components=contact_root_moment(row['tpu_contacts'],root,axis)
                if status.startswith('EXPLICIT'):
                    if (summary['max_eccentric_moment_Nm'] is None or
                            moment>summary['max_eccentric_moment_Nm']):
                        summary['max_eccentric_moment_Nm']=moment;summary['max_eccentric_time_s']=time
                        summary['max_eccentric_root_world_m']=root.tolist();summary['max_eccentric_center_world_m']=center.tolist()
                        summary['max_eccentric_force_world_N']=row['tpu_force'].tolist();summary['max_eccentric_status']=status
                        summary['max_root_moment_world_Nm']=moment_vec.tolist()
                        if components is not None:
                            summary['max_root_bending_moment_Nm']=components[0]
                            summary['max_root_torsional_moment_Nm']=components[1]
                elif (summary['_proxy_eccentric_moment_Nm'] is None or
                      moment>summary['_proxy_eccentric_moment_Nm']):
                    summary['_proxy_eccentric_moment_Nm']=moment;summary['_proxy_eccentric_time_s']=time
        if len(self.records)%max(1,round(.01/m.opt.timestep))==0:
            trace_legs={}
            # 接地していない脚もゼロ行として残す。脚ごとの時刻列を揃え、
            # 接触した脚だけを append することで生じる欠測を荷重ゼロと
            # 誤認しないよう、contact_count を明示する。
            for name in S.sg._LEGS:
                value=by_leg.get(name)
                if value is None:
                    root,status=self._root_position(name,m,d)
                    trace_legs[name]={'contact_count':0,'total_force_world_N':[0.,0.,0.],
                         'vertical_N':0.,'horizontal_N':0.,'horizontal_resultant_N':0.,
                         'tpu_contact_center_world_m':None,'tpu_normal_N':0.,
                         'pla_phi10_plug_root_world_m':None if root is None else root.tolist(),
                         'pla_phi10_plug_root_status':status}
                    continue
                root,status=roots[name]
                trace_legs[name]={'contact_count':int(value['contacts']),'total_force_world_N':value['force'].tolist(),
                     'vertical_N':float(value['vertical']),'horizontal_N':float(value['horizontal_sum']),
                     'horizontal_resultant_N':float(np.linalg.norm(value['horizontal_vec'])),
                     'tpu_contact_center_world_m':None if value['tpu_n']<=0 else (value['tpu_center_sum']/value['tpu_n']).tolist(),
                     'tpu_normal_N':float(value['tpu_n']),
                     'pla_phi10_plug_root_world_m':None if root is None else root.tolist(),
                     'pla_phi10_plug_root_status':status}
            self.foot_trace.append({'time_s':time,'segment':segment,'legs':trace_legs})

    def forward(self,m,d):
        self.forward_fn(m,d)
        if self.latest is None or self.latest[0]!=d.time or self.last_time==d.time:return
        self.last_time=float(d.time);time,torque,velocity=self.latest
        names=S.ALL_JOINTS
        aid=[mujoco.mj_name2id(m,mujoco.mjtObj.mjOBJ_ACTUATOR,'act_'+n) for n in names]
        da=[m.jnt_dofadr[mujoco.mj_name2id(m,mujoco.mjtObj.mjOBJ_JOINT,n)] for n in names]
        segment=self.case['segments'][min(np.searchsorted(self.edges,time-1e-8,side='right'),len(self.edges)-1)]['name']
        self.records.append((time,segment,torque[aid],velocity[da]))
        self._collect_foot_load(m,d,time,segment)
        # 正の床反力を持つ全接触点。摩擦/慣性のある動的安定そのものとは別の指標。
        points=[];force=np.zeros(6)
        ground_ids={g for g in range(m.ngeom) if int(m.geom_bodyid[g])==0}
        for i,c in enumerate(d.contact[:d.ncon]):
            if c.efc_address<0 or ((c.geom1 in ground_ids)==(c.geom2 in ground_ids)):continue
            mujoco.mj_contactForce(m,d,i,force)
            if abs(float(force[0]))>.05:points.append(c.pos[:2].copy())
        base=mujoco.mj_name2id(m,mujoco.mjtObj.mjOBJ_BODY,'base_link');cg=d.subtree_com[base].copy()
        margin=None
        if len(points)>=3:
            from scipy.spatial import ConvexHull,QhullError
            try:
                pts=np.array(points);hull=ConvexHull(pts);polygon=pts[hull.vertices];q=cg[:2]
                margin=min(((b-a)[0]*(q-a)[1]-(b-a)[1]*(q-a)[0])/np.linalg.norm(b-a) for a,b in zip(polygon,np.roll(polygon,-1,axis=0)))
            except QhullError:pass
        if len(self.records)%max(1,round(.01/m.opt.timestep))==0:
            self.support.append({'time_s':time,'segment':segment,'cg_world_m':cg.tolist(),'margin_mm':None if margin is None else float(margin*1000),'contact_points':len(points)})

    def foot_summary(self):
        output={}
        for leg in S.sg._LEGS:
            summary=self._foot.get(leg,self._empty_foot())
            center=(summary['_tpu_center_sum']/summary['tpu_contact_weight_N']
                    if summary['tpu_contact_weight_N']>0 else None)
            output[leg]={'contact_samples':summary['contact_samples'],
                'max_vertical_contact_force_N':summary['max_vertical_N'],
                'max_vertical_contact_force_time_s':summary['max_vertical_time_s'],
                'max_horizontal_contact_force_N':summary['max_horizontal_N'],
                'max_horizontal_resultant_force_N':summary['max_horizontal_resultant_N'],
                'max_horizontal_contact_force_time_s':summary['max_horizontal_time_s'],
                'tpu_contact_center_world_m':None if center is None else center.tolist(),
                'tpu_contact_weight_N':summary['tpu_contact_weight_N'],
                'pla_phi10_plug_root_world_m':(None if summary['_last_root'] is None
                                               else summary['_last_root'].tolist()),
                'pla_phi10_plug_root_status':summary['_last_root_status'],
                'pla_phi10_plug_root_source':(self.root_specs.get(leg,{}).get('source')
                                              if leg in self.root_specs else None),
                'max_eccentric_moment_Nm':summary['max_eccentric_moment_Nm'],
                'max_eccentric_moment_time_s':summary['max_eccentric_time_s'],
                'max_eccentric_moment_root_world_m':summary['max_eccentric_root_world_m'],
                'max_eccentric_moment_tpu_center_world_m':summary['max_eccentric_center_world_m'],
                'max_eccentric_moment_tpu_force_world_N':summary['max_eccentric_force_world_N'],
                'max_eccentric_moment_status':summary['max_eccentric_status'] or 'UNVERIFIED_NO_EXPLICIT_ROOT',
                'max_root_moment_world_Nm':summary['max_root_moment_world_Nm'],
                'max_root_bending_moment_Nm':summary['max_root_bending_moment_Nm'],
                'max_root_torsional_moment_Nm':summary['max_root_torsional_moment_Nm'],
                'proxy_eccentric_moment_Nm':summary['_proxy_eccentric_moment_Nm'],
                'proxy_eccentric_moment_time_s':summary['_proxy_eccentric_time_s'],
                'contact_material_counts':summary['_contact_materials'],
                'contact_part_counts':summary['_contact_parts'],
                'interpretation':'接触反力はMuJoCoの凸分解接触から算出。TPU接触中心は法線力重み付き。PLA φ10 プラグ根元の明示座標が無い場合、偏心モーメントはUNVERIFIEDで値を出さない。'
            }
        return output

    def summary(self):
        result={}
        def segment_name_at(time):
            index=min(np.searchsorted(self.edges,time-1e-8,side='right'),len(self.case['segments'])-1)
            return self.case['segments'][index]['name']

        def max_contiguous_seconds(mask, dt):
            if mask.size == 0:return 0.0
            best=current=0
            for value in mask:
                current=current+1 if bool(value) else 0
                best=max(best,current)
            return float(best*dt)

        for name in dict.fromkeys(s['name'] for s in self.case['segments']):
            rows=[r for r in self.records if r[1]==name]
            if not rows:continue
            torque=np.abs(np.array([r[2] for r in rows]));power=np.maximum(np.array([r[2]*r[3] for r in rows]),0).sum(axis=1)
            control_rows=[r for r in self.bound_records if segment_name_at(r[0])==name]
            if control_rows:
                control_signed=np.array([r[1] for r in control_rows],dtype=float)
                control_torque=np.abs(control_signed)
                control_bound=np.array([r[2] for r in control_rows],dtype=bool)
                leg_control=control_signed[:,:12]
                leg_bound=control_bound[:,:12]
                leg_bound_fraction=leg_bound.mean(axis=0)
                leg_bound_max_runs=[max_contiguous_seconds(leg_bound[:,i],self.case['model'].get('timestep',.002))
                                    for i in range(leg_bound.shape[1])]
            else:
                control_signed=np.empty((0,len(S.ALL_JOINTS)));control_torque=np.empty_like(control_signed)
                leg_bound=np.empty((0,12),dtype=bool);leg_control=np.empty((0,12))
                leg_bound_fraction=np.zeros(12);leg_bound_max_runs=[0.0]*12
            margins=[r['margin_mm'] for r in self.support if r['segment']==name and r['margin_mm'] is not None]
            result[name]={'samples':len(rows),'sampling_hz':1/self.case['model'].get('timestep',.002),
              'leg_axis_mean_absolute_torque_nm':torque[:,:12].mean(axis=0).tolist(),
              'leg_axis_mean_torque_nm':np.array([r[2][:12] for r in rows]).mean(axis=0).tolist(),
              'leg_axis_rms_torque_nm':np.sqrt(np.mean(np.array([r[2][:12] for r in rows])**2,axis=0)).tolist() if rows else [0.]*12,
              'leg_axis_p95_absolute_torque_nm':np.percentile(torque[:,:12],95,axis=0).tolist(),
              'leg_axis_p99_absolute_torque_nm':np.percentile(torque[:,:12],99,axis=0).tolist(),
              'leg_axis_max_absolute_torque_nm':torque[:,:12].max(axis=0).tolist(),
              'leg_mean_absolute_torque_sum_nm':float(torque[:,:12].mean(axis=0).sum()),
              'leg_p95_max_nm':float(np.percentile(torque[:,:12],95,axis=0).max()),
              'leg_p99_max_nm':float(np.percentile(torque[:,:12],99,axis=0).max()),
              'leg_axis_bound_saturation_fraction':leg_bound_fraction.tolist(),
              'leg_axis_bound_saturation_max_contiguous_s':leg_bound_max_runs,
              'leg_bound_saturation_fraction_max':float(max(leg_bound_fraction,default=0.0)),
              'leg_bound_saturation_max_contiguous_s':float(max(leg_bound_max_runs,default=0.0)),
              'bound_saturation_threshold':0.995,
              'bound_saturation_sample_count':int(len(control_rows)),
              'positive_mechanical_power_mean_W':float(power.mean()),
              'positive_mechanical_power_p95_W':float(np.percentile(power,95)),
              'support_margin_min_mm':float(min(margins)) if margins else None,
              'support_margin_p05_mm':float(np.percentile(margins,5)) if margins else None,
              'support_margin_negative_fraction':float(np.mean(np.array(margins)<0)) if margins else None,
              'support_sample_count':len(margins),
              'torque_acceptance':{'bound_saturation_fraction_limit':0.05,
                  'continuous_hold_saturation_is_not_pass':True,
                  'physical_status':'UNVERIFIED_LD220MG_CONTINUOUS_TORQUE_AND_TEMPERATURE'} }
        # 軸の最大値と時刻を脚ケース単位で残す。印刷強度の許容値は別途要実測。
        for leg_index,leg in enumerate(S.sg._LEGS):
            rows=self.records
            if not rows:continue
            indexes=slice(leg_index*3,leg_index*3+3)
            matrix=np.abs(np.array([r[2][indexes] for r in rows]))
            flat=int(np.argmax(matrix));ri,ai=np.unravel_index(flat,matrix.shape)
            summary=self._foot.setdefault(leg,self._empty_foot())
            summary['_leg_case_max_torque_nm']=float(matrix[ri,ai])
            summary['_leg_case_max_torque_time_s']=float(rows[ri][0])
            summary['_leg_case_max_torque_segment']=rows[ri][1]
            summary['_leg_case_max_torque_axis']=S.ALL_LEG_JOINTS[leg_index*3+ai]
        result['foot_load_by_leg']=self.foot_summary()
        for leg,summary in result['foot_load_by_leg'].items():
            source=self._foot.get(leg,self._empty_foot())
            summary.update({
                'leg_case_max_torque_nm':source.get('_leg_case_max_torque_nm'),
                'leg_case_max_torque_time_s':source.get('_leg_case_max_torque_time_s'),
                'leg_case_max_torque_segment':source.get('_leg_case_max_torque_segment'),
                'leg_case_max_torque_axis':source.get('_leg_case_max_torque_axis'),
            })
        return result


def collision_summary(rows, manifest_path, out):
    """凸分解の余剰/欠損と材料分類を結果へ転記する。"""
    rows=rows or []
    source=sum(float(r.get('source_volume_mm3',0.)) for r in rows)
    hull=sum(float(r.get('hulls_sum_volume_mm3',0.)) for r in rows)
    signed_delta=sum(float(r.get('hulls_sum_minus_source_volume_mm3',0.)) for r in rows)
    abs_delta=sum(abs(float(r.get('hulls_sum_minus_source_volume_mm3',0.))) for r in rows)
    worst=max(rows,key=lambda r:abs(float(r.get('hulls_sum_minus_source_volume_fraction',0.))),default=None)
    tpu_rows=[r for r in rows if r.get('material')=='TPU']
    return {'status':'UNVERIFIED' if rows else 'UNVERIFIED_NO_MANIFEST',
        'manifest':None if manifest_path is None else public_path(manifest_path,out),
        'part_count':len(rows),'hull_count':sum(int(r.get('hull_count',0)) for r in rows),
        'source_volume_total_mm3':source,'hulls_volume_total_mm3':hull,
        'hulls_sum_minus_source_volume_total_mm3':signed_delta,
        'absolute_hulls_sum_minus_source_volume_total_mm3':abs_delta,
        'hulls_sum_minus_source_volume_fraction_total':(hull-source)/max(source,1e-12),
        'worst_abs_hulls_sum_minus_source_volume_fraction':None if worst is None else abs(float(worst.get('hulls_sum_minus_source_volume_fraction',0.))),
        'worst_part':None if worst is None else {'link':worst.get('link'),'part':worst.get('part'),
            'hulls_sum_minus_source_volume_fraction':worst.get('hulls_sum_minus_source_volume_fraction'),
            'single_hull_volume_ratio':worst.get('single_hull_volume_ratio')},
        'material_part_counts':{mat:sum(1 for r in rows if r.get('material')==mat)
                                for mat in sorted({r.get('material') for r in rows})},
        'tpu_parts':[{'link':r.get('link'),'part':r.get('part'),'hull_count':r.get('hull_count')}
                     for r in rows if r.get('material')=='TPU'],
        'tpu_support_z_errors_mm':[{'link':r.get('link'),'part':r.get('part'),
            'source_range_mm':r.get('support_z_source_range_mm'),
            'hulls_range_mm':r.get('support_z_hulls_range_mm'),
            'min_error_mm':r.get('support_z_min_error_mm'),
            'max_error_mm':r.get('support_z_max_error_mm')}
            for r in tpu_rows],
        'tpu_max_abs_support_z_error_mm':max((abs(float(x)) for r in tpu_rows
            for x in (r.get('support_z_min_error_mm'),r.get('support_z_max_error_mm'))
            if x is not None),default=None),
        'old_foot_pad_parts':[{'link':r.get('link'),'part':r.get('part')}
                              for r in rows if r.get('part')=='foot_pad'],
        'interpretation':'凸片体積の和と元体積の差は、凸片間の重複/欠損を分離した実幾何誤差ではない。TPUは局所z支持関数の差も記録する。凹部を単一凸包で塞いだ架空接触をPASS扱いせず、自己衝突の無効化もこの数値からは導かない。'}


def collision_geometry_contract(rows, manifest_path, out):
    """最終モデルの凸分解台帳とTPU足支持の必須条件を判定する。

    ``collision_summary`` は近似量を記録するだけなので、ここで最終結果へ
    結合する真偽値を別に作る。manifestの存在、元メッシュと全凸片の検査
    状態、4脚のTPU靴、共有された旧部品名の不在、支持z誤差を一つでも欠けば、
    最終ケースは物理PASSへ進めない。
    """
    live_rows = rows if isinstance(rows, list) else []
    errors = []
    manifest = None
    manifest_file = None
    if manifest_path is not None:
        manifest_file = Path(manifest_path)
        if manifest_file.is_file():
            try:
                manifest = json.loads(manifest_file.read_text(encoding='utf-8'))
            except (OSError, json.JSONDecodeError) as exc:
                errors.append(f'convex manifest unreadable: {exc}')
        else:
            errors.append('convex manifest file is missing')
    else:
        errors.append('convex manifest path is missing')
    manifest_present = isinstance(manifest, list) and manifest_file is not None and manifest_file.is_file()
    if not isinstance(manifest, list):
        errors.append('convex manifest must contain a list of parts')
        manifest = []

    def valid_volume_record(record, label):
        if not isinstance(record, dict):
            errors.append(f'{label} validation is missing')
            return False
        required = ('finite', 'watertight', 'winding_consistent', 'is_volume')
        ok = all(record.get(key) is True for key in required)
        if not ok:
            errors.append(f'{label} finite/watertight/winding/is_volume check failed')
        try:
            volume = float(record.get('volume_mm3'))
        except (TypeError, ValueError):
            volume = float('nan')
        if not math.isfinite(volume) or volume <= 0.0:
            errors.append(f'{label} volume is not finite and positive')
            ok = False
        return ok

    def validate_row(row, label):
        if not isinstance(row, dict):
            errors.append(f'{label} is not an object')
            return False
        ok = valid_volume_record(row.get('source_validation'), f'{label} source')
        try:
            source_volume = float(row.get('source_volume_mm3'))
            hull_volume = float(row.get('hulls_sum_volume_mm3'))
        except (TypeError, ValueError):
            source_volume = hull_volume = float('nan')
        if not math.isfinite(source_volume) or source_volume <= 0.0:
            errors.append(f'{label} source_volume_mm3 is not finite and positive')
            ok = False
        if not math.isfinite(hull_volume) or hull_volume <= 0.0:
            errors.append(f'{label} hulls_sum_volume_mm3 is not finite and positive')
            ok = False
        hulls = row.get('hull_validations')
        try:
            hull_count = int(row.get('hull_count'))
        except (TypeError, ValueError):
            hull_count = 0
        if hull_count <= 0 or not isinstance(hulls, list) or len(hulls) != hull_count:
            errors.append(f'{label} hull validation count does not match hull_count')
            ok = False
        else:
            for index, hull in enumerate(hulls):
                ok = valid_volume_record(hull, f'{label} hull[{index}]') and ok
        for key in ('hulls_sum_minus_source_volume_mm3',
                    'hulls_sum_minus_source_volume_fraction',
                    'single_hull_volume_ratio'):
            try:
                value = float(row.get(key))
            except (TypeError, ValueError):
                value = float('nan')
            if not math.isfinite(value):
                errors.append(f'{label} {key} is not finite')
                ok = False
        return ok

    for index, row in enumerate(manifest):
        validate_row(row, f'convex manifest row {index}')
    live_ids = {(row.get('link'), row.get('part')) for row in live_rows
                if isinstance(row, dict)}
    manifest_ids = {(row.get('link'), row.get('part')) for row in manifest
                    if isinstance(row, dict)}
    if not live_rows:
        errors.append('live convex manifest rows are missing')
    if live_ids != manifest_ids or len(live_ids) != len(live_rows):
        errors.append('live and saved convex manifests do not contain the same unique parts')
    geometry_error_count = len(errors)
    all_source_and_hulls_valid = bool(manifest) and geometry_error_count == 0

    def row_leg(row):
        link = str(row.get('link', ''))
        match = re.fullmatch(r'leg_(fr|fl|rl|rr)_tibia', link.lower())
        return None if match is None else match.group(1).upper()

    # The final assembly collector emits the one adopted logical part name
    # once on each tibia.  Do not infer a shoe from a substring: an old
    # ``foot_pad#shoe_FR_0`` row, or an arbitrary ``tpu_*`` row, must never
    # satisfy the four-leg material contract by naming coincidence.
    tpu_shoe_rows = [
        row for row in manifest
        if isinstance(row, dict)
        and row.get('part') == 'tpu_shoe'
        and str(row.get('material', '')).upper() == 'TPU'
    ]
    tpu_shoe_legs = [row_leg(row) for row in tpu_shoe_rows]
    tpu_shoe_links = [str(row.get('link', '')).lower() for row in tpu_shoe_rows]
    tpu_shoes_four_legs = (
        len(tpu_shoe_rows) == 4
        and len(set(tpu_shoe_legs)) == 4
        and set(tpu_shoe_legs) == set(S.sg._LEGS)
        and len(set(tpu_shoe_links)) == 4
        and all(leg is not None for leg in tpu_shoe_legs)
    )
    if not tpu_shoes_four_legs:
        errors.append(
            'final TPU shoe contract requires exactly one material=TPU '
            f'part=tpu_shoe row on each tibia; got legs={tpu_shoe_legs!r} '
            f'rows={len(tpu_shoe_rows)}'
        )
    # Use the same exact/prefix/fragment replacement contract as the t0 and
    # self-collision inventories.  The adopted pf_* replacement names are
    # explicitly exempted by sim_collision.print_first_legacy_name().
    old_foot_pad_rows = [
        row for row in manifest
        if isinstance(row, dict)
        and 'foot_pad' in str(row.get('part', '')).lower()
    ]
    no_legacy_foot_pad = not old_foot_pad_rows
    if not no_legacy_foot_pad:
        errors.append('legacy foot_pad is present in the final convex manifest')
    old_replacement_rows = [
        row for row in manifest
        if isinstance(row, dict)
        and COLLISION.print_first_legacy_name(row.get('part')) is not None
    ]
    no_legacy_replacement_parts = not old_replacement_rows
    if not no_legacy_replacement_parts:
        errors.append('legacy print-first replacement part is present in the final convex manifest')
    z_errors = []
    for row in [row for row in manifest
                if isinstance(row, dict) and str(row.get('material', '')).upper() == 'TPU']:
        for key in ('support_z_min_error_mm', 'support_z_max_error_mm'):
            try:
                value = float(row.get(key))
            except (TypeError, ValueError):
                value = float('nan')
            z_errors.append(value)
    tpu_support_z_error_finite = bool(z_errors) and all(math.isfinite(value) for value in z_errors)
    tpu_max_abs_support_z_error_mm = (
        max((abs(value) for value in z_errors if math.isfinite(value)), default=None)
    )
    tpu_support_z_error_le_0p1mm = (
        tpu_support_z_error_finite and tpu_max_abs_support_z_error_mm <= 0.1
    )
    if not tpu_support_z_error_le_0p1mm:
        errors.append('TPU support_z min/max error exceeds 0.1 mm or is unavailable')
    checks = {
        'convex_manifest_present': manifest_present,
        'convex_source_and_hulls_valid': all_source_and_hulls_valid,
        'tpu_shoes_four_legs_material_tpu': tpu_shoes_four_legs,
        'no_legacy_foot_pad': no_legacy_foot_pad,
        'no_legacy_replacement_parts': no_legacy_replacement_parts,
        'tpu_support_z_error_finite': tpu_support_z_error_finite,
        'tpu_support_z_error_le_0p1mm': tpu_support_z_error_le_0p1mm,
    }
    return {
        'status': 'PASS' if all(checks.values()) else 'FAIL',
        'checks': checks,
        'manifest': None if manifest_file is None else public_path(manifest_file, out),
        'manifest_part_count': len(manifest),
        'tpu_shoe_legs': tpu_shoe_legs,
        'legacy_foot_pad_parts': [
            {'link': row.get('link'), 'part': row.get('part')}
            for row in old_foot_pad_rows
        ],
        'legacy_replacement_parts': [
            {'link': row.get('link'), 'part': row.get('part'),
             'token': COLLISION.print_first_legacy_name(row.get('part'))}
            for row in old_replacement_rows
        ],
        'forbidden_legacy_part_names': list(COLLISION.PRINT_FIRST_LEGACY_PART_NAMES),
        'tpu_max_abs_support_z_error_mm': tpu_max_abs_support_z_error_mm,
        'errors': errors,
        'interpretation': '最終凸分解の入力/全凸片の有限正体積検査、4脚TPU靴、旧foot_pad不在、支持z誤差0.1mm以下を全て満たす場合だけ物理結果へ結合する。これは実材料のたわみ・接着・連続定格を証明しない。',
    }


def reachable_audit_assessment(audit_path, out):
    """有限姿勢掃引と連続到達集合の根拠を別々に評価する。

    有限のfirmware姿勢に対する実メッシュ掃引が存在しても、隣接姿勢間
    の変位上界・最小分離距離による連続区間証明にはならない。後者の明示
    根拠が無い限り、最終統合の ``continuous`` 判定をPASSへ昇格しない。
    """
    report = {
        'finite_pose_sweep_available': False,
        'finite_pose_sweep_clean': False,
        'finite_pose_sweep_complete': False,
        'finite_native_trace_status': None,
        'finite_native_trace_coverage': None,
        'finite_pose_count': 0,
        'finite_pose_inputs_unchanged': False,
        'continuous_reachable_set_proven': False,
        'continuous_status': 'UNVERIFIED',
        'audit_path': None,
        'errors': [],
    }
    if not audit_path:
        report['errors'].append('reachable-pose audit path is missing')
        return report
    path = resolve_path(audit_path)
    report['audit_path'] = public_path(path, out)
    if not path.is_file():
        report['errors'].append(
            f'reachable-pose audit evidence is missing: {report["audit_path"]}'
        )
        return report
    try:
        audit = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        report['errors'].append(f'could not read reachable-pose audit: {exc}')
        return report
    poses = audit.get('poses')
    report['finite_pose_count'] = len(poses) if isinstance(poses, list) else 0
    report['finite_pose_sweep_available'] = report['finite_pose_count'] > 0
    report['finite_pose_inputs_unchanged'] = audit.get('inputs_unchanged') is True
    report['finite_pose_sweep_complete'] = audit.get('finite_pose_sweep_complete') is True
    report['finite_native_trace_status'] = audit.get('status')
    report['finite_native_trace_coverage'] = audit.get('coverage')
    # The stored rows alone are insufficient evidence: a failed/partial audit
    # can still contain clean-looking pose objects.  Require the producer's
    # finite-trace status and all coverage/count declarations before any
    # downstream physics result may treat this as a clean finite audit.
    audit_contract_ok = True
    if audit.get('status') != 'PASS_FINITE_TRACE':
        audit_contract_ok = False
        report['errors'].append(
            'reachable-pose audit status is not PASS_FINITE_TRACE')
    if audit.get('coverage') != 'finite_native_trace':
        audit_contract_ok = False
        report['errors'].append(
            'reachable-pose audit coverage is not finite_native_trace')
    if audit.get('finite_pose_sweep_requested') is not True:
        audit_contract_ok = False
        report['errors'].append(
            'reachable-pose audit did not request a finite native-trace sweep')
    if audit.get('expected_pose_count_matches') is not True:
        audit_contract_ok = False
        report['errors'].append(
            'reachable-pose audit expected pose count does not match')
    if audit.get('pose_count_matches') is not True:
        audit_contract_ok = False
        report['errors'].append(
            'reachable-pose audit processed pose count does not match')
    if audit.get('finite_pose_sweep_clean') is not True:
        audit_contract_ok = False
        report['errors'].append(
            'reachable-pose audit did not declare finite_pose_sweep_clean')
    processed_count = audit.get('processed_pose_count')
    native_count = audit.get('native_pose_count')
    if (isinstance(processed_count, bool) or not isinstance(processed_count, int)
            or isinstance(native_count, bool) or not isinstance(native_count, int)
            or processed_count != report['finite_pose_count']
            or native_count != report['finite_pose_count']):
        audit_contract_ok = False
        report['errors'].append(
            'reachable-pose audit processed/native counts do not match pose rows')
    if not report['finite_pose_sweep_complete']:
        report['errors'].append(
            'reachable-pose audit did not declare a complete finite native-trace sweep'
        )
    clean = True
    if not isinstance(poses, list) or not poses:
        clean = False
        report['errors'].append('reachable-pose audit has no finite pose rows')
    else:
        for index, pose in enumerate(poses):
            if not isinstance(pose, dict):
                clean = False
                report['errors'].append(f'pose {index} is not an object')
                continue
            pairs = pose.get('pairs')
            if not isinstance(pairs, list):
                clean = False
                report['errors'].append(f'pose {index} has no pair results')
                continue
            for pair in pairs:
                if not isinstance(pair, dict):
                    clean = False
                    continue
                if pair.get('actual_intersections'):
                    clean = False
                if pair.get('errors'):
                    clean = False
        if not clean:
            report['errors'].append('finite pose sweep contains intersections or unresolved Boolean pairs')
    fixed_intersections = audit.get('fixed_base_intersections', [])
    fixed_errors = audit.get('fixed_base_boolean_errors', [])
    if not isinstance(fixed_intersections, list) or fixed_intersections:
        clean = False
        report['errors'].append('fixed-base audit contains intersections or has invalid metadata')
    if not isinstance(fixed_errors, list) or fixed_errors:
        clean = False
        report['errors'].append('fixed-base audit contains unresolved Boolean errors or has invalid metadata')
    report['finite_pose_sweep_clean'] = bool(
        report['finite_pose_sweep_available']
        and report['finite_pose_inputs_unchanged']
        and report['finite_pose_sweep_complete']
        and audit_contract_ok
        and clean
    )
    # A finite native-trace audit has no authority to establish a continuous
    # reachable-set result.  In particular, do not accept a producer's
    # self-declared ``continuous_reachable_set_proven`` flag or a broad
    # coverage label as an interval certificate.  A future independent
    # verifier may populate this boundary explicitly, but until that verifier
    # exists the result is deliberately fixed at UNVERIFIED.
    continuous_claimed = (
        audit.get('continuous_reachable_set_proven') is True
        or audit.get('coverage') in ('continuous_reachable_set', 'all_reachable_continuous')
        or audit.get('continuous_status') == 'PASS'
    )
    report['continuous_reachable_set_proven'] = False
    report['continuous_status'] = 'UNVERIFIED'
    if continuous_claimed:
        report['errors'].append(
            'self-declared continuous reachable-set evidence is not accepted without an independent interval certificate verifier'
        )
    if report['finite_pose_sweep_clean'] and not report['continuous_reachable_set_proven']:
        report['errors'].append(
            'finite pose sweep is trajectory/finite evidence only; continuous reachability proof is absent'
        )
    report['interpretation'] = (
        'finite firmware-output pose sweep and continuous reachable-set proof are separate. '
        'Adjacent-pose displacement upper bounds and minimum separation certificates are required for continuous PASS.'
    )
    return report


def _rotation_from_rpy_deg(rpy):
    """roll/pitch/yaw を MuJoCo の body 座標変換へ変換する。"""
    roll,pitch,yaw=np.radians(np.asarray(rpy,dtype=float))
    cr,sr=np.cos(roll),np.sin(roll);cp,sp=np.cos(pitch),np.sin(pitch);cy,sy=np.cos(yaw),np.sin(yaw)
    return np.array([[cy*cp,cy*sp*sr-sy*cr,cy*sp*cr+sy*sr],
                     [sy*cp,sy*sp*sr+cy*cr,sy*sp*cr-cy*sr],
                     [-sp,cp*sr,cp*cr]])


def commanded_fk_reference(profile, body_h):
    """初期保持姿勢の指令FK位置を profile から導出する。"""
    refs={}
    with python_profile(profile):
        for index,leg in enumerate(S.sg._LEGS):
            target=np.asarray(S.sg.foot_target(index,0.,0.,0.,0.,body_h,holding=True),dtype=float)
            angles=S.sg.leg_ik(*target)
            if angles is None:
                refs[leg]={'status':'UNVERIFIED_IK_FAILURE','target_leg_mm':target.tolist()}
                continue
            fk=np.asarray(S.sg.leg_fk(*angles),dtype=float)
            c,s=np.cos(S.sg.MOUNT[index]),np.sin(S.sg.MOUNT[index])
            body=np.array([S.sg.ORIGIN[index,0]+c*fk[0]-s*fk[1],
                           S.sg.ORIGIN[index,1]+s*fk[0]+c*fk[1],fk[2]])
            refs[leg]={'status':'DERIVED_FROM_PROFILE_FK','target_leg_mm':target.tolist(),
                       'ik_deg':list(map(float,angles)),'fk_leg_mm':fk.tolist(),
                       'fk_body_mm':body.tolist()}
    return refs


def effective_foot_geometry(case, result, model_info):
    """新靴の初期接地観測とFKを並べる（単一実効長への置換はしない）。"""
    final=model_info.get('model_kind') in FINAL_MODEL_KINDS
    cfg=S.sg._C
    base={'physical_tibia_len_mm':float(cfg.TIBIA_LEN),
          'gait_tibia_len_mm':float(cfg.TIBIA_LEN_GAIT),
          'foot_frame_to_tibia_root':'[0, 0, -config.TIBIA_LEN] mm, frame=tibia',
          'status':'NOT_APPLICABLE_LEGACY_MODEL' if not final else 'UNVERIFIED_NEW_SHOE_MEASUREMENT_PENDING',
          'measurement_policy':'最初の静止区間で指令FK、TPU接触中心、股高を同時に記録する。姿勢依存の差を残し、床位置を姿勢ごとに合わせない。'}
    if not final:
        return base
    segments=case.get('segments',[])
    body_h=float(segments[0].get('body_h',S.BODY_H_DEFAULT)) if segments else float(S.BODY_H_DEFAULT)
    refs=commanded_fk_reference(case['profile'],body_h)
    base['commanded_body_h_mm']=body_h
    base['commanded_fk_body_mm']=refs
    base_pos=np.asarray(result.get('initial_position_m',[0.,0.,0.]),dtype=float)
    trace=next(iter(result.get('foot_load_trace_100hz',[])),None)
    first_ts=(result.get('timeseries') or [{}])[0]
    rotation=_rotation_from_rpy_deg(first_ts.get('rpy_deg',[0.,0.,0.]))
    base['initial_base_position_m']=base_pos.tolist()
    base['initial_base_rpy_deg']=first_ts.get('rpy_deg',[0.,0.,0.])
    rows={}
    for leg in S.sg._LEGS:
        ref=refs.get(leg,{})
        expected=None
        if ref.get('fk_body_mm') is not None:
            expected=(base_pos+rotation@np.asarray(ref['fk_body_mm'])*.001).tolist()
        observed=(trace or {}).get('legs',{}).get(leg,{})
        center=observed.get('tpu_contact_center_world_m')
        root=observed.get('pla_phi10_plug_root_world_m')
        row={'commanded_fk_world_m':expected,
             'tpu_contact_center_world_m':center,
             'pla_phi10_plug_root_world_m':root,
             'pla_phi10_plug_root_status':observed.get('pla_phi10_plug_root_status'),
             'initial_trace_time_s':None if trace is None else trace.get('time_s'),
             'contact_count':observed.get('contact_count',0)}
        if center is not None and expected is not None:
            residual=(np.asarray(center)-np.asarray(expected))*1000.
            row['tpu_center_minus_commanded_fk_mm']=residual.tolist()
            row['vertical_residual_mm']=float(residual[2])
        if center is not None and root is not None:
            vector=np.asarray(center)-np.asarray(root)
            row['root_to_tpu_center_distance_mm']=float(np.linalg.norm(vector)*1000.)
            row['root_to_tpu_center_vector_mm']=(vector*1000.).tolist()
        rows[leg]=row
    base['initial_static_comparison']=rows
    distances=[r['root_to_tpu_center_distance_mm'] for r in rows.values()
               if r.get('root_to_tpu_center_distance_mm') is not None]
    residuals=[r['vertical_residual_mm'] for r in rows.values()
               if r.get('vertical_residual_mm') is not None]
    base['observed_root_to_tpu_center_distance_range_mm']=(
        [min(distances),max(distances)] if distances else None)
    base['observed_vertical_residual_range_mm']=(
        [min(residuals),max(residuals)] if residuals else None)
    if not distances:
        base['status']='UNVERIFIED_NO_INITIAL_TPU_CONTACT'
    else:
        base['status']='OBSERVATION_ONLY_POSTURE_DEPENDENT_TPU_CENTER'
    base['interpretation']='TPU接触中心は靴底全体の最低点やたわみを表さない。単一のTIBIA_LEN_EFFへ丸めず、姿勢別の残差を最終実形状・実物測定と照合する。'
    return base


def plot_simulation_overview(result, path):
    """姿勢・脚トルク・平面軌跡を別名の1枚へ出力する。"""
    rows=result.get('timeseries') or []
    path=Path(path)
    if not rows:
        return {'status':'UNVERIFIED_NO_TIMESERIES','path':public_path(path,path.parent)}
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        t=np.asarray([r.get('time',0.) for r in rows],dtype=float)
        rpy=np.asarray([r.get('rpy_deg',[np.nan]*3) for r in rows],dtype=float)
        pos=np.asarray([r.get('base_pos',[np.nan]*3) for r in rows],dtype=float)
        torque=np.asarray([r.get('torque_nm',[])[:12] for r in rows],dtype=float)
        fig,axes=plt.subplots(2,2,figsize=(12,8),constrained_layout=True)
        for i,label in enumerate(('roll','pitch','yaw')):
            axes[0,0].plot(t,rpy[:,i],label=label)
        axes[0,0].set(xlabel='time [s]',ylabel='angle [deg]',title='Body attitude')
        axes[0,0].legend(loc='best',fontsize=8)
        if torque.ndim==2 and torque.shape[1]>=12:
            for i,leg in enumerate(S.sg._LEGS):
                axes[0,1].plot(t,np.max(np.abs(torque[:,i*3:(i+1)*3]),axis=1),label=leg)
        axes[0,1].set(xlabel='time [s]',ylabel='max leg torque [Nm]',title='Leg case torque')
        axes[0,1].legend(loc='best',fontsize=8)
        axes[1,0].plot(pos[:,0]*1000.,pos[:,1]*1000.,color='#245b9c')
        axes[1,0].scatter(pos[0,0]*1000.,pos[0,1]*1000.,label='start',s=30)
        axes[1,0].scatter(pos[-1,0]*1000.,pos[-1,1]*1000.,label='end',s=30)
        axes[1,0].set(xlabel='base X [mm]',ylabel='base Y [mm]',title='Planar trajectory')
        axes[1,0].axis('equal');axes[1,0].legend(loc='best',fontsize=8)
        walk=[s for s in result.get('segments',[]) if s.get('name')=='walk']
        walk=walk[0] if walk else None
        if walk and walk.get('delta_xyz_m'):
            delta=np.asarray(walk['delta_xyz_m'])*1000.
            text_lines=[f"walk ΔX {delta[0]:.1f} mm",f"walk ΔY {delta[1]:.1f} mm",
                        f"walk ΔZ {delta[2]:.1f} mm",f"walk yaw {walk.get('yaw_change_deg',0.):.2f} deg"]
        else:
            delta=(pos[-1]-pos[0])*1000.
            text_lines=[f"total ΔX {delta[0]:.1f} mm",f"total ΔY {delta[1]:.1f} mm",
                        f"total ΔZ {delta[2]:.1f} mm",f"total yaw {rpy[-1,2]-rpy[0,2]:.2f} deg"]
        axes[1,1].axis('off');axes[1,1].text(.05,.9,'; '.join(text_lines),va='top',wrap=True)
        axes[1,1].set_title('Run displacement')
        fig.suptitle(str(result.get('case',{}).get('name','simulation')))
        path.parent.mkdir(parents=True,exist_ok=True);fig.savefig(path,dpi=150);plt.close(fig)
        return {'status':'GENERATED','path':public_path(path,path.parent),'sha256':sha(path),
                'interpretation':'時系列の10Hz保存値による確認図。全500Hz負荷の代替ではない。'}
    except Exception as exc:
        return {'status':'UNVERIFIED_PLOT_ERROR','path':public_path(path,path.parent),'error':str(exc)}


RUN_MANIFEST_SCHEMA_VERSION = 1


def _freeze_binding_for_case(case, out):
    """ケースの凍結台帳参照を実体・時刻つきで正規化する。"""
    internal = resolve_case_output_refs(case, out)
    path = _freeze_manifest_path(internal)
    options = internal.get('model', {}) if isinstance(internal, dict) else {}
    expected_hash = (
        options.get('geometry_freeze_hash', options.get('freeze_hash'))
        or internal.get('geometry_freeze_hash') or internal.get('freeze_hash')
    )
    expected_time = (
        options.get('geometry_freeze_time', options.get('freeze_time'))
        or internal.get('geometry_freeze_time') or internal.get('freeze_time')
    )
    if path is None:
        return {
            'required': False,
            'path': None,
            'sha256': None,
            'geometry_freeze_time': None,
        }
    return {
        'required': True,
        'path': public_path(path, out),
        'sha256': expected_hash,
        'geometry_freeze_time': expected_time,
    }


def build_run_manifest(cases, out, run_id):
    """一回のsimulationと結果ファイル集合を固定する。"""
    out = Path(out).resolve()
    if not isinstance(run_id, str) or not run_id:
        raise ValueError('run_id must be a non-empty string')
    seen = set()
    expected = []
    freeze_bindings = []
    for case in cases:
        name = case.get('name') if isinstance(case, dict) else None
        if not isinstance(name, str) or not name:
            raise ValueError('run case.name must be a non-empty string')
        if name in seen:
            raise ValueError(f'duplicate run case name: {name}')
        seen.add(name)
        expected.append({
            'case': name,
            'path': public_path(out / 'results' / f'{name}.json', out),
        })
        freeze_bindings.append(_freeze_binding_for_case(case, out))
    unique_freeze = {
        tuple(sorted(binding.items())) for binding in freeze_bindings
    }
    if len(unique_freeze) > 1:
        raise ValueError('one run cannot mix frozen and non-frozen case inputs')
    freeze = freeze_bindings[0] if freeze_bindings else {
        'required': False, 'path': None, 'sha256': None,
        'geometry_freeze_time': None,
    }
    return {
        'schema_version': RUN_MANIFEST_SCHEMA_VERSION,
        'status': 'RUNNING',
        'run_id': run_id,
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'result_directory': public_path(out / 'results', out),
        'results': expected,
        'freeze': freeze,
        'interpretation': '一回の実行ID、結果集合、凍結台帳を束ねるsimulation入力台帳。',
    }


def _resolve_run_fingerprint_key(key, out):
    """入力台帳キーを再検査できるパスへ戻す。"""
    if key.startswith('$PHYSICAL_RESULT_CONTENT/') or key.startswith('$PHYSICAL_RESULT_FILE/'):
        return 'self', None
    if key == '$PROXY_EXCLUSION_CONTRACT':
        return 'proxy', None
    if key.startswith('$OUTPUT/'):
        path = (Path(out) / key[len('$OUTPUT/'):]).resolve()
        try:
            path.relative_to(Path(out).resolve())
        except ValueError as exc:
            raise ValueError(f'入力台帳の$OUTPUT pathが出力束外: {key}') from exc
        return 'file', path
    if key.startswith('$EXTERNAL/'):
        return 'external', None
    if key.startswith('$'):
        return 'unknown', None
    return 'file', resolve_manifest_path(key, base=ROOT)


def _verify_result_input_binding(result, result_path, out, errors):
    """結果の実行時SHA台帳、自己内容SHA、現在ファイルを再照合する。"""
    checks = result.get('checks')
    result_case = result.get('case') if isinstance(result.get('case'), dict) else {}
    case_name = result_case.get('name')
    if not isinstance(checks, dict) or checks.get('inputs_unchanged') is not True:
        errors.append({'kind': 'stale_input_ledger', 'case': case_name})
    ledger = result.get('input_sha256')
    current_ledger = result.get('input_sha256_current')
    if not isinstance(ledger, dict) or not isinstance(current_ledger, dict):
        errors.append({'kind': 'input_sha256_missing', 'path': public_path(result_path, out)})
        return
    if ledger != current_ledger:
        errors.append({'kind': 'input_sha256_current_differs', 'path': public_path(result_path, out)})

    content_sha = result.get('physical_result_content_sha256')
    try:
        computed = physical_result_content_sha(result)
    except (TypeError, ValueError) as exc:
        errors.append({'kind': 'physical_result_content_invalid', 'error': str(exc)})
        computed = None
    if content_sha != computed:
        errors.append({'kind': 'physical_result_content_sha256',
                       'expected': content_sha, 'actual': computed})
    if result.get('physical_result_file_sha256') != content_sha:
        errors.append({'kind': 'physical_result_file_sha256',
                       'expected': content_sha,
                       'actual': result.get('physical_result_file_sha256')})

    for key, expected in ledger.items():
        try:
            kind, path = _resolve_run_fingerprint_key(str(key), out)
        except (OSError, ValueError) as exc:
            errors.append({'kind': 'input_path_invalid', 'key': key, 'error': str(exc)})
            continue
        if kind == 'self':
            if expected != content_sha:
                errors.append({'kind': 'self_input_sha256', 'key': key,
                               'expected': content_sha, 'actual': expected})
            continue
        if kind == 'proxy':
            actual = COLLISION.print_first_proxy_exclusion_contract()['sha256']
        elif kind == 'external':
            errors.append({'kind': 'external_input_unreproducible', 'key': key})
            continue
        elif kind == 'unknown':
            errors.append({'kind': 'unknown_input_key', 'key': key})
            continue
        else:
            if path is None or not path.is_file():
                errors.append({'kind': 'input_missing', 'key': key})
                continue
            actual = sha(path)
        if expected != actual:
            errors.append({'kind': 'stale_input', 'key': key,
                           'expected': expected, 'actual': actual})


def _freeze_rows_current(data, manifest_path, errors):
    """凍結台帳の各行を現在の実体SHAへ再照合する。"""
    rows = _freeze_rows(data)
    if not rows:
        errors.append({'kind': 'freeze_files_missing', 'path': public_path(manifest_path)})
        return
    seen = set()
    for row in rows:
        raw = row.get('path')
        if not isinstance(raw, str) or raw.startswith('$'):
            errors.append({'kind': 'freeze_non_reproducible_path', 'path': raw})
            continue
        path = resolve_manifest_path(raw, base=manifest_path.parent)
        key = str(path)
        if key in seen:
            errors.append({'kind': 'freeze_duplicate_path', 'path': raw})
        seen.add(key)
        if not path.is_file():
            errors.append({'kind': 'freeze_input_missing', 'path': raw})
            continue
        actual = sha(path)
        if row.get('sha256') != actual:
            errors.append({'kind': 'freeze_input_stale', 'path': raw,
                           'expected': row.get('sha256'), 'actual': actual})


def _verify_result_freeze_binding(result, manifest_freeze, out, errors):
    """各結果をrun-manifestの同一FINAL_FROZEN台帳へ束縛する。"""
    result_case = result.get('case')
    if not isinstance(result_case, dict):
        errors.append({'kind': 'result_case_missing'})
        return
    binding = _freeze_binding_for_case(result_case, out)
    if binding != manifest_freeze:
        errors.append({'kind': 'mixed_freeze_binding',
                       'expected': manifest_freeze, 'actual': binding,
                       'case': result_case.get('name')})
        return
    if not manifest_freeze.get('required'):
        return
    path = resolve_case_output_refs(manifest_freeze['path'], out)
    # resolve_case_output_refs returns a string for a scalar; resolve_path then
    # handles repository-relative keys as well as the absolute case path.
    path = resolve_path(path)
    if not path.is_file():
        errors.append({'kind': 'freeze_manifest_missing', 'path': manifest_freeze['path']})
        return
    actual_hash = sha(path)
    if manifest_freeze.get('sha256') != actual_hash:
        errors.append({'kind': 'freeze_manifest_stale',
                       'expected': manifest_freeze.get('sha256'), 'actual': actual_hash})
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append({'kind': 'freeze_manifest_invalid_json', 'error': str(exc)})
        return
    if not isinstance(data, dict) or data.get('status') != FINAL_FREEZE_STATUS:
        errors.append({'kind': 'freeze_manifest_status',
                       'expected': FINAL_FREEZE_STATUS,
                       'actual': data.get('status') if isinstance(data, dict) else None})
    manifest_time = data.get('geometry_freeze_time', data.get('freeze_time', data.get('frozen_at')))
    if manifest_time != manifest_freeze.get('geometry_freeze_time'):
        errors.append({'kind': 'freeze_manifest_time',
                       'expected': manifest_freeze.get('geometry_freeze_time'),
                       'actual': manifest_time})
    if isinstance(data, dict):
        _freeze_rows_current(data, path, errors)


def _result_summary_row(result, result_path, out):
    """既存要約項目を欠損入力でも落とさず保存する。"""
    actuators = result.get('actuators') if isinstance(result.get('actuators'), dict) else {}
    joint_order = result.get('joint_order') if isinstance(result.get('joint_order'), list) else []
    leg = [actuators[name] for name in joint_order[:12] if name in actuators]
    walk = next((s for s in result.get('segments', [])
                 if isinstance(s, dict) and s.get('name') == 'walk'), None)
    values = [v.get('max_absolute_torque_nm') for v in leg
              if isinstance(v, dict) and isinstance(v.get('max_absolute_torque_nm'), (int, float))]
    saturation = [v.get('saturation_fraction') for v in leg
                  if isinstance(v, dict) and isinstance(v.get('saturation_fraction'), (int, float))]
    return {
        'case': (result.get('case') or {}).get('name'),
        'status': result.get('status'),
        'mass_kg': result.get('mass_kg'),
        'max_all_time_leg_torque_nm': max(values) if values else None,
        'max_leg_saturation_fraction': max(saturation) if saturation else None,
        'sampled_walking': result.get('walking_load_sampled_10hz'),
        'full_rate_walking': result.get('full_rate_load', {}).get('walk')
            if isinstance(result.get('full_rate_load'), dict) else None,
        'walk_speed_m_s': (
            walk.get('commanded_path_distance_m') / walk.get('duration_s')
            if isinstance(walk, dict) and isinstance(walk.get('commanded_path_distance_m'), (int, float))
            and isinstance(walk.get('duration_s'), (int, float)) and walk.get('duration_s')
            else None
        ),
        'roll_deg': result.get('max_abs_roll_deg'),
        'pitch_deg': result.get('max_abs_pitch_deg'),
        'tpu_fraction': result.get('tpu_support', {}).get('fraction')
            if isinstance(result.get('tpu_support'), dict) else None,
        'warnings': result.get('warning_counts'),
        'result_sha256': sha(result_path),
    }


def execute(case,out, *, run_id=None, run_manifest_path=None):
    global NATIVE_BINARY_RUNTIME
    out=Path(out).resolve();out.mkdir(parents=True,exist_ok=True)
    profile=dict(DEFAULT,**case['profile']);case['profile']=profile;folder=out/'firmware'/case['profile_name']
    # 最終統合モデルでは、足フレーム→脛リンクの変換済み根元を入力へ明示する。
    # 旧感度モデルへ自動注入すると、旧形状の結果を新構成の根元荷重と誤認し得るため、
    # final_integrated 系だけを対象にする。ユーザー指定値がある場合は保持する。
    model_options=case.setdefault('model',{})
    if (model_options.get('model_kind') in FINAL_MODEL_KINDS
            and 'pla_phi10_plug_roots' not in model_options):
        model_options['pla_phi10_plug_roots']={k:dict(v) for k,v in PLA_PHI10_PLUG_ROOTS.items()}
    final_requirements=final_case_requirements(case, raise_on_missing=True)
    profile_mode=('frozen_print_first' if model_options.get('model_kind') in FINAL_MODEL_KINDS
                  else 'candidate_print_first')
    binary=prepare_native(
        profile, folder, profile_mode=profile_mode,
        freeze_manifest=_freeze_manifest_path(case))
    comparison=verify_profile(profile,folder,profile_mode=profile_mode)
    case['native_binary']=public_path(binary,out)
    old_path=S.URDF_PATH;old_native=T.native_output_trace;old_fingerprint=T.input_fingerprints
    old_convex=COLLISION.convex_parts;old_build=S.build_model
    old_s_output=S.FINGERPRINT_OUTPUT_ROOT;old_t_output=T.FINGERPRINT_OUTPUT_ROOT
    old_c_output=COLLISION.OUTPUT_ROOT;old_native_runtime=NATIVE_BINARY_RUNTIME
    source_urdf=resolve_path(case.get('model',{}).get('source_urdf',old_path))
    model_path,source_urdf,assembly_requested=select_model(case,out)
    manifest_path=out/'models'/(case['name']+'.manifest.json')
    model_info=model_manifest(case,model_path,source_urdf,out)
    model_info['manifest_path']=public_path(manifest_path,out)
    save(manifest_path,model_info)
    case['model_manifest']=public_path(manifest_path,out)
    save(out/'cases'/(case['name']+'.json'),publicize(case,out))
    native_hash={public_path(p,out):sha(p) for p in folder.glob('*.h')}
    for native_input in (folder / 'trace.cpp', folder / 'build.json'):
        if native_input.is_file():
            native_hash[public_path(native_input, out)] = sha(native_input)
    def fingerprints(c):
        h=old_fingerprint(c);h.update(native_hash)
        h[public_path(Path(__file__),out)]=sha(__file__)
        h[public_path(model_path,out)]=sha(model_path)
        h[public_path(manifest_path,out)]=sha(manifest_path)
        freeze_path=_freeze_manifest_path(c)
        if freeze_path is not None and freeze_path.is_file():
            h[public_path(freeze_path,out)]=sha(freeze_path)
        return dict(sorted(h.items()))
    # Final integrated runs deliberately start with an empty, isolated cache.
    # Seeding it from the repository would make an orphan or stale NPZ look as
    # if it had been generated by this case, and would also defeat the
    # extra-cache gate in the post-hoc checker.  Legacy sensitivity runs keep
    # the historical read-only seed behavior, but archives from an older
    # format are skipped so they are regenerated under the current contract.
    cache=out/'collision-cache';cache.mkdir(exist_ok=True)
    final_cache = bool(final_requirements.get('required'))
    existing_cache = sorted(cache.glob('*.npz'))
    if final_cache and existing_cache:
        raise ValueError(
            'final integrated collision cache must start empty; remove the '
            'uncommitted case output and rerun from a fresh output directory')
    cache_seed_policy = 'empty_final_case_cache' if final_cache else 'legacy_read_only_seed'
    if not final_cache:
        for src in COLLISION.CACHE.glob('*.npz'):
            try:
                with np.load(src, allow_pickle=False) as saved:
                    archive_format = int(np.asarray(saved.get('cache_format')).item()) \
                        if 'cache_format' in saved else None
            except Exception:
                archive_format = None
            if archive_format != getattr(COLLISION, 'CACHE_FORMAT', None):
                continue
            dest=cache/src.name
            if not dest.exists():shutil.copy2(src,dest)
    def convex_with_output(*args,**kw):
        return old_convex(*args,cache=cache,**kw)
    COLLISION.convex_parts=convex_with_output
    T.native_output_trace=native_trace;T.input_fingerprints=fingerprints
    S.URDF_PATH=model_path;S.FINGERPRINT_OUTPUT_ROOT=out;T.FINGERPRINT_OUTPUT_ROOT=out;COLLISION.OUTPUT_ROOT=out
    collector=FullRateMetrics(case)
    def build_with_capture(*args,**kw):
        model,index=old_build(*args,**kw);collector.bind(model,index);return model,index
    S.build_model=build_with_capture
    mujoco.mj_step=collector.step;mujoco.mj_forward=collector.forward
    NATIVE_BINARY_RUNTIME=str(binary)
    try:
        with geometry_context(case,model_path):
            with python_profile(profile):result=T.execute(case,out/'results')
        # convex_parts の関数属性は、実行中の出力ラッパーへ付く。
        collision_rows=getattr(COLLISION.convex_parts,'last_manifest',None)
        collision_manifest=getattr(COLLISION.convex_parts,'last_manifest_path',None)
    finally:
        mujoco.mj_step=collector.step_fn;mujoco.mj_forward=collector.forward_fn
        S.URDF_PATH=old_path;S.FINGERPRINT_OUTPUT_ROOT=old_s_output;S.build_model=old_build
        T.native_output_trace=old_native;T.input_fingerprints=old_fingerprint;T.FINGERPRINT_OUTPUT_ROOT=old_t_output
        COLLISION.convex_parts=old_convex;COLLISION.OUTPUT_ROOT=old_c_output;NATIVE_BINARY_RUNTIME=old_native_runtime
    result['native_comparison']=comparison
    result['collision_cache_seed_policy'] = cache_seed_policy
    result['group_voltage_V']=requested_group_voltages(case)
    result['group_voltage_interpretation']='旧DS3218/MG90Sメーカー値の電圧内挿を使った参照感度。現物LD-220MGの6Vトルクは未公表で、本計算値への適合は未確認。目の電圧応答は未知。'
    result['model_manifest']=model_info
    result['final_case_requirements']=publicize(final_requirements,out)
    if final_requirements.get('required'):
        # self_collision=True still uses convex collision geometry in MuJoCo,
        # and parent-link filtering may remain enabled. A separate audit over
        # every pose in the specified finite native trace and the source meshes
        # is therefore required;
        # floor/foot contacts alone cannot become a whole-robot PASS.
        options=case.get('model',{})
        audit_path=options.get('self_collision_audit_path')
        audit_assessment=reachable_audit_assessment(audit_path,out)
        # A finite native-trace sweep is a valid model-side gate for this
        # execution.  It does not become a continuous reachable-set proof;
        # that boundary is retained as metadata below and is deliberately
        # kept out of ``checks``.
        audit_complete=audit_assessment['finite_pose_sweep_complete']
        audit_clean=audit_assessment['finite_pose_sweep_clean']
        audit_reason=(
            'referenced finite native-trace audit reports PASS with unchanged inputs'
            if audit_complete and audit_clean else '; '.join(audit_assessment.get('errors', []))
            or 'finite native-trace evidence is pending'
        )
        result['self_collision_interpretation']={
            'mujoco_self_collision_enabled':options.get('self_collision') is True,
            'servo_geometry_included':options.get('include_servo_collision') is True,
            'parent_link_filtering_may_apply':options.get('include_parent_collision') is not True,
            'finite_native_trace_mesh_audit_required':True,
            'finite_native_trace_mesh_audit_complete':audit_complete,
            'finite_pose_sweep_available':audit_assessment['finite_pose_sweep_available'],
            'finite_pose_sweep_clean':audit_assessment['finite_pose_sweep_clean'],
            'finite_pose_count':audit_assessment['finite_pose_count'],
            'continuous_reachable_set_proven':audit_assessment['continuous_reachable_set_proven'],
            'audit_evidence_path':None if not audit_path else public_path(resolve_path(audit_path),out),
            'status':'PASS_FINITE_TRACE' if audit_complete and audit_clean
                    else 'FAIL' if audit_complete else 'UNVERIFIED',
            'reason':audit_reason,
            'method':'tools/sim_self_collision.py plus every pose in the specified finite native trace using exact source-part Boolean checks; convex-hull contacts and floor-only contacts cannot establish a whole-robot PASS.'
        }
        result['checks']['finite_native_trace_mesh_audit_complete']=audit_complete
        result['checks']['finite_native_trace_mesh_sweep_clean']=audit_clean
        # Continuous reachability remains an explicit limitation rather than
        # a boolean acceptance check.  A complete but dirty finite audit is a
        # concrete model failure; an absent/incomplete audit remains pending.
        if result.get('status')=='PASS':
            if audit_complete and not audit_clean:
                result['status']='FAIL'
            elif not audit_complete:
                result['status']='UNVERIFIED'
    result['collision_geometry_approximation']=collision_summary(collision_rows,collision_manifest,out)
    if final_requirements.get('required'):
        collision_contract=collision_geometry_contract(collision_rows,collision_manifest,out)
        result['collision_geometry_contract']=collision_contract
        result['external_proxy_proof_required']=True
        result['external_proxy_proof_checker']={
            'path':'tools/check_print_first_proxy_exclusions.py',
            'status':'REQUIRED_POSTHOC',
            'continuous_reachable_set_proven':False,
            'interpretation':'物理結果のPASSだけでは固定5組の除外を確定せず、同一caseのnative全行・密掃引・今回サンプリングしたqposの外部exact監査を別途結合する。',
        }
        for name,value in collision_contract['checks'].items():
            result['checks'][name]=bool(value)
        # A concrete manifest/material/support violation is a model failure.
        # Keep an already more specific numerical/initial-contact/fall status,
        # while preventing PASS or pending-only UNVERIFIED from surviving.
        if collision_contract['status'] != 'PASS':
            result['status_before_collision_contract']=result.get('status')
            if result.get('status') in ('PASS','UNVERIFIED'):
                result['status']='FAIL'
        elif result.get('status') == 'PASS':
            # The physical run has only established the internal finite-case
            # checks.  The fixed five-pair proxy exception still needs the
            # independent post-hoc source-mesh proof, so a raw simulation
            # result must never be mistaken for the final integrated PASS.
            result['status_before_external_proxy_proof'] = 'PASS'
            result['status'] = 'PENDING_EXTERNAL_PROXY_PROOF'
            result['external_proxy_proof_status'] = 'REQUIRED_POSTHOC'
    result['full_rate_load']=collector.summary()
    result['foot_load_by_leg']=result['full_rate_load'].get('foot_load_by_leg',collector.foot_summary())
    result['support_trace_100hz']=collector.support
    result['foot_load_trace_100hz']=collector.foot_trace
    result['foot_effective_geometry']=effective_foot_geometry(case,result,model_info)
    result['profile_changes']={k:v for k,v in profile.items() if k not in DEFAULT or v!=DEFAULT[k]}
    result['profile_source']={'path':'hardware/src/config.py','key':'PRINT_FIRST_GAIT',
        'status':profile.get('profile_status'),'adopted':profile.get('profile_adopted',False)}
    result['mass_placement_interpretation']=('凍結済み統合URDFをそのまま使用。生成時の部品集合/参照メッシュを台帳へ記録したが、実組立の適合・樹脂強度・電源を証明しない。'
        if model_info['model_kind'] in FINAL_MODEL_KINDS
        else 'base_linkの質量分布を平行移動した旧感度条件。部品形状は移動しておらず実組立の再現ではない。')
    result['controller']='正規firmware/srcの通常設定は維持し、print-first用に複製/生成したヘッダー・定数差分・PWM量子化を適用。追加ヘッダーを含む試作モードであり、通常版と同一ではない。'
    # 段階間の始動ピークと定常歩行の負荷を分ける。時系列は10Hz標本なので最大値は別の全刻み指標を参照。
    walk=[r for r in result['timeseries'] if r['segment']=='walk']
    if walk:
        torque=np.abs(np.array([r['torque_nm'][:12] for r in walk]))
        result['walking_load_sampled_10hz']={'peak_absolute_torque_nm':float(torque.max()),'max_axis_p95_absolute_torque_nm':float(np.percentile(torque,95,axis=0).max()),'sum_mean_absolute_torque_nm':float(torque.mean(axis=0).sum()),'positive_power_mean_W':float(np.mean([r['positive_power_W'] for r in walk]))}
    figure_path=out/f'final-simulation-overview-{case["name"]}.png'
    result['overview_figure']=plot_simulation_overview(result,figure_path)
    # 実行時の絶対パスは台帳の公開キーへ変換し、ローカル環境の個人パスを
    # ケース内へ残さない。内部で使う case 自体はこの後も変更しない。
    result['case']=publicize(result.get('case',case),out)
    if run_id is not None:
        result['run_id'] = run_id
        result['run_manifest_path'] = (
            public_path(Path(run_manifest_path).resolve(), out)
            if run_manifest_path is not None else None
        )
    result_path = out/'results'/(case['name']+'.json')
    build_path = folder / 'build.json'
    if not build_path.is_file():
        raise FileNotFoundError(f'native build provenance is missing: {build_path}')
    result['native_build'] = {
        'path': public_path(build_path, out),
        'sha256': sha(build_path),
        'profile_mode': profile_mode,
        'build_mode': profile_mode,
    }
    # Bind the result content to the input ledger without an impossible
    # self-hash recursion: the canonical digest excludes only the ledger
    # fields and this field, then is stored in both ledgers.  The post-hoc
    # checker recomputes the same digest from the bytes it reads.
    result_content_sha = physical_result_content_sha(result)
    result['physical_result_content_sha256'] = result_content_sha
    # This is the canonical file-payload digest (the self-referential ledger
    # fields are excluded), rather than an impossible hash of a file that
    # contains its own final hash.  Keep a distinct key for consumers that
    # require an explicit result-file binding.
    result['physical_result_file_sha256'] = result_content_sha
    result_key = '$PHYSICAL_RESULT_CONTENT/' + public_path(result_path, out)
    file_key = '$PHYSICAL_RESULT_FILE/' + public_path(result_path, out)
    for ledger_key in ('input_sha256', 'input_sha256_current'):
        if not isinstance(result.get(ledger_key), dict):
            result[ledger_key] = {}
        result[ledger_key][result_key] = result_content_sha
        result[ledger_key][file_key] = result_content_sha
        result[ledger_key] = dict(sorted(result[ledger_key].items()))
    save(result_path,result)
    return result


def formal_motion_quality_cases_for(
    profiles, *, model_kind='final_integrated', model_options=None
):
    """同一最終モデル用の正式4動作品質ケースをメモリ上で生成する。

    機械側の凍結台帳をまだ持たない段階では、ここからファイルを書かず
    ``final_case_requirements`` の入力テンプレートだけを返す。実行側は
    source/config stable 通知後に凍結情報を ``model_options`` へ渡す。
    """
    if model_kind not in FINAL_MODEL_KINDS:
        raise ValueError('formal motion-quality cases require a final model kind')
    if not isinstance(profiles, dict) or not profiles:
        raise ValueError('profiles must contain at least one named profile')
    # cases_for centralizes the final model paths, parent collision switch,
    # material-aware collision model, and the three reference voltages.
    base_cases = cases_for(
        profiles, model_kind=model_kind, model_options=model_options
    )
    cases = []
    for base in base_cases:
        profile_name = base['profile_name']
        profile = base['profile']
        common = dict(base['model'])
        common['torque_model'] = 'linear-speed'
        body_h = profile['body_h']

        def make_case(name, kind, selected, segments, criteria, **markers):
            case = {
                'name': f'{profile_name}_{name}',
                'profile_name': profile_name,
                'profile': dict(profile),
                'model': dict(common),
                'motion_quality_case': True,
                'motion_quality_kind': kind,
                'quality_eligible': True,
                'motion_quality_segments': list(selected),
                'motion_quality_criteria': criteria,
                'segments': segments,
            }
            case.update(markers)
            cases.append(case)

        make_case(
            'forward_quality',
            'long_forward',
            ['forward'],
            [
                {'name': 'settle', 'duration': 2.0, 'body_h': body_h},
                {'name': 'forward', 'duration': 60.0, 'vy': 1.0, 'body_h': body_h},
            ],
            {
                'expected_command_vector': [0.0, 1.0, 0.0],
                'expected_command_tolerance': 1e-6,
                'max_yaw_cumulative_deg': 10.0,
                'max_lateral_mm': 30.0,
                'lateral_fraction': 0.25,
                'min_progress_m': 0.05,
                'min_duration_s': 59.5,
                'command_tolerance': 1e-6,
            },
        )
        for sign, label in ((1, 'positive'), (-1, 'negative')):
            make_case(
                f'turn_{label}_quality',
                'turn',
                [f'turn_{label}'],
                [
                    {'name': 'settle', 'duration': 2.0, 'body_h': body_h},
                    {
                        'name': f'turn_{label}',
                        'duration': 8.0,
                        'wz': 0.75 * sign,
                        'body_h': body_h,
                    },
                ],
                {
                    'expected_turn_sign': sign,
                    'expected_command_vector': [0.0, 0.0, 0.75 * sign],
                    'expected_command_tolerance': 1e-6,
                    'min_turn_deg': 10.0,
                    'min_duration_s': 7.5,
                    'command_tolerance': 1e-6,
                },
            )
        make_case(
            'stop_restart_quality',
            'stop_restart',
            ['stop'],
            [
                {'name': 'settle', 'duration': 2.0, 'body_h': body_h},
                {'name': 'approach', 'duration': 8.0, 'vy': 1.0, 'body_h': body_h},
                {'name': 'stop', 'duration': 4.0, 'body_h': body_h},
                {'name': 'restart', 'duration': 8.0, 'vy': 1.0, 'body_h': body_h},
            ],
            {
                'max_stop_drift_mm': 30.0,
                'max_stop_yaw_deg': 2.0,
                'min_duration_s': 3.5,
                'command_tolerance': 1e-6,
                'restart': {
                    'expected_command_vector': [0.0, 1.0, 0.0],
                    'expected_command_tolerance': 1e-6,
                    'max_yaw_cumulative_deg': 10.0,
                    'max_lateral_mm': 30.0,
                    'lateral_fraction': 0.25,
                    'min_progress_m': 0.01,
                    'min_duration_s': 7.5,
                    'command_tolerance': 1e-6,
                },
            },
            motion_quality_restart_segments=['restart'],
        )
    return cases


# Keep a concise discoverable name for callers preparing the freeze2 bundle.
motion_quality_cases_for = formal_motion_quality_cases_for


def cases_for(profiles, *, model_kind='legacy_sensitivity', model_options=None):
    cases=[]
    common={'contact_model':'vhacd','friction':.6,'hard_friction':.3}
    if model_options:common.update(model_options)
    if model_kind in FINAL_MODEL_KINDS:
        common['model_kind']=model_kind
        common.setdefault('model_path',ROOT/'hardware/urdf-print-first/tachikoma.urdf')
        common.setdefault('assembly_context',True)
        common.setdefault('self_collision',True)
        common.setdefault('include_servo_collision',True)
        # The formal freeze2 candidate includes parent-link contacts so a
        # servo tracking transient cannot hide cap/femur or case/coxa contact.
        # A caller may explicitly set false for a comparison diagnostic, but
        # that input is not the formal final candidate.
        common.setdefault('include_parent_collision',True)
        common.setdefault('torque_model','linear-speed')
        common.setdefault('finite_native_trace_mesh_audit',True)
        # LD-220MGの6 V実測値は未取得のため、ここはメーカー端点からの
        # 参照内挿感度であることをケースへ明示し、通常定格と混同しない。
        common.setdefault('group_voltage_V',{'leg':6.0,'arm':5.0,'eye':5.0})
        common.setdefault('assembly_manifest',ROOT/'outputs/print-first-20260905/body/assembly.json')
        common.setdefault('feet_manifest',ROOT/'outputs/print-first-20260905/feet/assembly.json')
        common.setdefault('leg_manifest',ROOT/'outputs/print-first-20260905/legs/assembly.json')
        common.setdefault('foot_contact_reference',ROOT/'outputs/print-first-20260905/feet/assembly.json')
        common.setdefault('pla_phi10_plug_roots',config_phi10_plug_roots())
    else:
        common.setdefault('foot_candidate_dir',SHOES)
        common.setdefault('model_kind','legacy_sensitivity')
    for name,p in profiles.items():
        cases.append({'name':name,'profile_name':name,'profile':p,
          'model':dict(common),
          'segments':[{'name':'settle','duration':2.,'body_h':p['body_h']},
            {'name':'walk','duration':round(4*p['cycle_t'],2),'vy':1.,'body_h':p['body_h']},
            {'name':'stop','duration':p['cycle_t'],'body_h':p['body_h']}]})
    return cases


def summarize(out):
    out = Path(out).resolve()
    errors = []
    run_manifest_path = out / 'run-manifest.json'
    run_manifest = None
    if not run_manifest_path.is_file():
        errors.append({'kind': 'run_manifest_missing',
                       'path': public_path(run_manifest_path, out)})
    else:
        try:
            run_manifest = json.loads(run_manifest_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append({'kind': 'run_manifest_invalid_json', 'error': str(exc)})
    expected = []
    run_id = None
    manifest_freeze = {
        'required': False, 'path': None, 'sha256': None,
        'geometry_freeze_time': None,
    }
    if isinstance(run_manifest, dict):
        if run_manifest.get('schema_version') != RUN_MANIFEST_SCHEMA_VERSION:
            errors.append({'kind': 'run_manifest_schema',
                           'expected': RUN_MANIFEST_SCHEMA_VERSION,
                           'actual': run_manifest.get('schema_version')})
        run_id = run_manifest.get('run_id')
        if not isinstance(run_id, str) or not run_id:
            errors.append({'kind': 'run_manifest_run_id_missing'})
        raw_expected = run_manifest.get('results')
        if not isinstance(raw_expected, list) or not raw_expected:
            errors.append({'kind': 'run_manifest_results_missing'})
        else:
            for item in raw_expected:
                if not isinstance(item, dict) or not isinstance(item.get('case'), str) \
                        or not isinstance(item.get('path'), str):
                    errors.append({'kind': 'run_manifest_result_entry_invalid', 'entry': item})
                    continue
                expected.append(item)
        raw_freeze = run_manifest.get('freeze')
        if isinstance(raw_freeze, dict):
            manifest_freeze = {
                'required': raw_freeze.get('required') is True,
                'path': raw_freeze.get('path'),
                'sha256': raw_freeze.get('sha256'),
                'geometry_freeze_time': raw_freeze.get('geometry_freeze_time'),
            }
        else:
            errors.append({'kind': 'run_manifest_freeze_missing'})

    expected_names = [item.get('case') for item in expected]
    if len(expected_names) != len(set(expected_names)):
        errors.append({'kind': 'run_manifest_duplicate_cases'})
    expected_paths = []
    for item in expected:
        raw_path = item.get('path')
        try:
            if not raw_path.startswith('$OUTPUT/'):
                raise ValueError('result path must be under $OUTPUT')
            result_path = (out / raw_path[len('$OUTPUT/'):]).resolve()
            result_path.relative_to(out)
        except (AttributeError, OSError, ValueError) as exc:
            errors.append({'kind': 'run_manifest_result_path_invalid',
                           'path': raw_path, 'error': str(exc)})
            continue
        expected_paths.append((item.get('case'), result_path))

    result_files = sorted((out / 'results').glob('*.json')) if (out / 'results').is_dir() else []
    expected_file_set = {path for _, path in expected_paths}
    for extra in sorted(set(result_files) - expected_file_set):
        errors.append({'kind': 'mixed_result_file', 'path': public_path(extra, out)})
    for _, missing_path in expected_paths:
        if not missing_path.is_file():
            errors.append({'kind': 'result_missing', 'path': public_path(missing_path, out)})

    rows = []
    for case_name, result_path in expected_paths:
        if not result_path.is_file():
            continue
        try:
            result = json.loads(result_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append({'kind': 'result_invalid_json',
                           'path': public_path(result_path, out), 'error': str(exc)})
            continue
        if not isinstance(result, dict):
            errors.append({'kind': 'result_not_object', 'path': public_path(result_path, out)})
            continue
        result_case = result.get('case') if isinstance(result.get('case'), dict) else {}
        if result_case.get('name') != case_name:
            errors.append({'kind': 'result_case_mismatch', 'expected': case_name,
                           'actual': result_case.get('name'),
                           'path': public_path(result_path, out)})
        if result.get('run_id') != run_id:
            errors.append({'kind': 'result_run_id_mismatch', 'expected': run_id,
                           'actual': result.get('run_id'),
                           'path': public_path(result_path, out)})
        expected_manifest_ref = public_path(run_manifest_path, out)
        if result.get('run_manifest_path') != expected_manifest_ref:
            errors.append({'kind': 'result_run_manifest_mismatch',
                           'expected': expected_manifest_ref,
                           'actual': result.get('run_manifest_path'),
                           'path': public_path(result_path, out)})
        if result.get('status') != 'PASS':
            errors.append({'kind': 'result_status_not_pass',
                           'expected': 'PASS', 'actual': result.get('status'),
                           'case': case_name})
        _verify_result_input_binding(result, result_path, out, errors)
        _verify_result_freeze_binding(result, manifest_freeze, out, errors)
        rows.append(_result_summary_row(result, result_path, out))

    report = {
        'schema_version': 2,
        'report_type': 'print_first_simulation_summary',
        'status': 'PASS' if not errors and bool(expected_paths) else 'FAIL',
        'run_id': run_id,
        'run_manifest': {
            'path': public_path(run_manifest_path, out),
            'sha256': sha(run_manifest_path) if run_manifest_path.is_file() else None,
        },
        'freeze': manifest_freeze,
        'cases': rows,
        'errors': errors,
        'interpretation': '同一run-manifest、同一凍結台帳、現在の入力SHA、全結果PASSを束ねた要約。',
    }
    save(out / 'summary.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('action',choices=['scan','run','summarize']);ap.add_argument('--out',type=Path,default=OUT);ap.add_argument('--cases',type=Path);ap.add_argument('--select')
    args=ap.parse_args();out=args.out.resolve()
    if args.action=='scan':scan(out)
    elif args.action=='summarize':
        report = summarize(out)
        return 0 if report.get('status') == 'PASS' else 1
    else:
        cases=load_case_input(args.cases) if args.cases else cases_for(json.loads((out/'profiles.json').read_text()))
        if args.select:cases=[c for c in cases if c['name'] in args.select.split(',')]
        if not cases:ap.error('該当条件なし')
        run_id = uuid.uuid4().hex
        run_manifest_path = out / 'run-manifest.json'
        run_manifest = build_run_manifest(cases, out, run_id)
        save(run_manifest_path, run_manifest)
        save(out/('dynamic-plan-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.json'),{'cases':publicize(cases,out),'tool_sha256':sha(__file__),'comparison_criteria':'同一幾何/質量で低トルク化。既存5%飽和/30度姿勢/5mm/sの条件を維持、電流は未測定。'})
        results=[execute(c,out,run_id=run_id,run_manifest_path=run_manifest_path) for c in cases]
        report = summarize(out)
        return 0 if report.get('status') == 'PASS' else 1
    return 0

if __name__=='__main__':sys.exit(main())
