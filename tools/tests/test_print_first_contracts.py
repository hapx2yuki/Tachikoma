#!/usr/bin/env python3
"""印刷優先シムの経路・荷重集計契約を軽量に確認する。"""
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import xml.etree.ElementTree as ET

import numpy as np
import trimesh

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'hardware/src'))
sys.path.insert(0,str(ROOT/'tools'))

import sim_physics as S
import sim_stress as T
import sim_print_first as P
import render_print_first as R
import sim_self_collision as SC
import xiao_retention_plan as X
import sim_collision
import check_print_first_proxy_exclusions as PX
from check_print_first_feet import (_simulation_force_summary,
                                    _checked_manifold_boolean_volume,
                                    _validated_difference_parts)
from sim_print_first import (contact_root_moment, final_case_requirements,
                             cases_for, PRINT_FIRST_PROFILE,
                             formal_motion_quality_cases_for,
                             apply_group_voltage_limits, voltage_limit_report,
                             collision_geometry_contract,
                             reachable_audit_assessment,
                             validate_freeze_manifest, FREEZE_REQUIRED_SOURCES,
                             prepare_native, _check_profile_header,
                             sha as print_first_sha, case_output_root,
                             resolve_case_output_refs, load_case_input)
from sim_collision import (collision_material,
                           print_first_proxy_exclusion_contract,
                           PRINT_FIRST_PROXY_EXCLUSION_PAIRS)
from sim_self_collision import (_audit_status, _intersection_volume,
                                _load_native_trace, _print_first_inventory_contract,
                                _index_legacy_stls, _public_root_path)
from check_leg_link_strength import (_print_first_rule, _print_first_paths,
                                     print_first_scan)


class PrintFirstContractTests(unittest.TestCase):
    def test_proxy_exclusion_contract_is_fixed_and_hash_bound(self):
        """ケース入力から隣接除外組を増減できず、同じSHAを記録する。"""
        contract = print_first_proxy_exclusion_contract()
        self.assertEqual(len(contract['pairs']), 5)
        self.assertEqual(
            [(row['body1'], row['body2']) for row in contract['pairs']],
            list(PRINT_FIRST_PROXY_EXCLUSION_PAIRS))
        self.assertEqual(contract['runtime_case_additions'], 'forbidden')
        self.assertTrue(contract['exact_proof_required'])
        canonical = {key: value for key, value in contract.items()
                     if key != 'sha256'}
        digest = hashlib.sha256(json.dumps(
            canonical, sort_keys=True, ensure_ascii=False,
            separators=(',', ':')).encode()).hexdigest()
        self.assertEqual(contract['sha256'], digest)

    def test_proxy_exclusion_keeps_non_exempt_self_collision_detection(self):
        """固定1組の除外後も、別の重なりはMuJoCoが検出する。"""
        import mujoco

        spec = mujoco.MjSpec()
        bodies = [spec.worldbody.add_body(name=name) for name in ('a', 'b', 'c')]
        for body in bodies:
            body.add_freejoint()
            body.add_geom(type=mujoco.mjtGeom.mjGEOM_BOX,
                          size=[0.1, 0.1, 0.1], contype=1, conaffinity=1)
        spec.add_exclude(name='fixed_pair', bodyname1='a', bodyname2='b')
        model = spec.compile()
        data = mujoco.MjData(model)
        mujoco.mj_forward(model, data)
        contacts = {
            frozenset((mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY,
                                         int(model.geom_bodyid[c.geom1])),
                       mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY,
                                         int(model.geom_bodyid[c.geom2]))))
            for c in data.contact[:data.ncon]
        }
        self.assertNotIn(frozenset(('a', 'b')), contacts)
        self.assertIn(frozenset(('a', 'c')), contacts)
        self.assertEqual(model.nexclude, 1)
        expected_signature = (1 << 16) | 2
        self.assertEqual(model.exclude_signature.tolist(), [expected_signature])

    def test_proxy_checker_binds_compiled_signatures_and_joint_qpos_addresses(self):
        """コンパイル済み除外署名と関節の実qpos番地を固定契約へ束縛する。"""
        import mujoco

        contract = print_first_proxy_exclusion_contract()
        body_names = sorted({name for row in contract['pairs']
                             for name in (row['body1'], row['body2'])})
        body_ids = {name: index + 1 for index, name in enumerate(body_names)}

        class FakeExclude:
            def __init__(self, signature):
                self.name = 'reviewed'
                self.signature = np.asarray([signature], dtype=np.uint64)

        class FakeModel:
            def __init__(self, signatures):
                self._excludes = [FakeExclude(value) for value in signatures]
                self.nexclude = len(self._excludes)
                self.opt = type('Opt', (), {
                    'disableflags': int(mujoco.mjtDisableBit.mjDSBL_FILTERPARENT),
                })()
                self.body_parentid = np.arange(max(body_ids.values()) + 1,
                                               dtype=np.int32)
                self.jnt_bodyid = np.zeros(len(S.ALL_JOINTS), dtype=np.int32)
                for row in contract['pairs']:
                    spec = next(item for item in __import__('export_urdf').JOINT_SPECS
                                if item['name'] == row['joint'])
                    parent = body_ids[spec['parent']]
                    child = body_ids[spec['child']]
                    self.body_parentid[child] = parent
                    self.jnt_bodyid[joint_ids[row['joint']]] = child

            def exclude(self, index):
                return self._excludes[index]

        expected = [((min(body_ids[row['body1']], body_ids[row['body2']]) << 16)
                     | max(body_ids[row['body1']], body_ids[row['body2']]))
                    for row in contract['pairs']]
        import export_urdf
        joint_ids = {name: index for index, name in enumerate(S.ALL_JOINTS)}
        def fake_name2id(_model, kind, name):
            if kind == mujoco.mjtObj.mjOBJ_BODY:
                return body_ids.get(name, -1)
            if kind == mujoco.mjtObj.mjOBJ_JOINT:
                return joint_ids.get(name, -1)
            return -1
        with patch.object(PX.mujoco, 'mj_name2id', side_effect=fake_name2id):
            accepted = PX._compiled_proxy_exclusion_report(
                FakeModel(expected), contract)
            self.assertEqual(accepted['status'], 'PASS')
            rejected = PX._compiled_proxy_exclusion_report(
                FakeModel(expected[:-1] + [expected[-1] + 1]), contract)
        self.assertEqual(rejected['status'], 'FAIL')

        class FakeJointModel:
            jnt_qposadr = np.arange(7, 27, dtype=np.int32)[::-1]
            jnt_range = np.tile(np.array([[-1., 1.]]), (20, 1))

        joint_model = FakeJointModel()
        with patch.object(PX.mujoco, 'mj_name2id',
                          side_effect=lambda _model, _kind, name: joint_ids.get(name, -1)):
            qpos = np.zeros(27)
            for axis, name in enumerate(S.ALL_JOINTS):
                qpos[joint_model.jnt_qposadr[axis]] = axis + 1.
            qdeg = PX._qdeg_from_qpos(joint_model, qpos)
            self.assertAlmostEqual(qdeg[S.ALL_JOINTS[0]],
                                   float(np.degrees(1.)))
            self.assertAlmostEqual(qdeg[S.ALL_JOINTS[-1]],
                                   float(np.degrees(20.)))
            accepted_qpos = PX._qpos_range_report(joint_model, np.zeros(27))
            bad_qpos = np.zeros(27)
            bad_qpos[joint_model.jnt_qposadr[0]] = 2.
            rejected_qpos = PX._qpos_range_report(joint_model, bad_qpos)
        self.assertEqual(accepted_qpos['status'], 'PASS')
        self.assertEqual(rejected_qpos['status'], 'FAIL')

    def test_proxy_checker_rejects_short_native_schedule_and_native_range(self):
        """traceの短縮とnative角度の範囲外を有限証拠として受け入れない。"""
        options = {
            '_case_segments': [{'name': 'forward', 'duration': .04,
                                'vy': 1., 'body_h': 115.}],
            '_profile_body_h': 115.,
        }
        with self.assertRaisesRegex(ValueError, 'expanded count'):
            PX._validate_trace_schedule({'rows': []}, options)
        limits = {
            'axes': [{'name': name, 'match': True, 'expected_deg': [-1., 1.]}
                     for name in S.ALL_JOINTS],
        }
        native = np.zeros((1, 43))
        accepted = PX._native_joint_range_report(native, limits)
        native[0, 3] = 2.
        rejected = PX._native_joint_range_report(native, limits)
        self.assertEqual(accepted['status'], 'PASS')
        self.assertEqual(rejected['status'], 'FAIL')

    def test_proxy_checker_rejects_status_no_fall_and_stale_runtime_ledger(self):
        """物理結果の合否・転倒・入力台帳を別の結果へ差し替えられない。"""
        contract = print_first_proxy_exclusion_contract()
        ledger = {'base': 'a' * 64,
                  '$PROXY_EXCLUSION_CONTRACT': contract['sha256']}
        clean_scan = {
            'bbox_candidate_pairs': 0, 'boolean_evaluations': 0,
            'positive_below_threshold_count': 0, 'max_intersection_mm3': 0.,
            'intersections': [], 'errors': [],
        }
        case = {
            'name': 'proxy-physical-contract',
            'model': {
                'model_kind': 'final_integrated', 'assembly_context': True,
                'self_collision': True, 'include_servo_collision': True,
                'include_parent_collision': True, 'contact_model': 'vhacd',
                'freeze_manifest_sha256': 'b' * 64,
            },
            'segments': [{'name': 'forward', 'duration': .02, 'vy': 1.}],
        }
        with tempfile.TemporaryDirectory(
                dir=ROOT, prefix='.tachikoma-proxy-physical-') as temp:
            folder = Path(temp)
            model_path = folder / 'model.urdf'
            model_path.write_text('<robot/>', encoding='utf-8')
            result_path = folder / 'physical.json'
            native_full = np.zeros((2, 43), dtype=float)
            native_full[:, 2] = 1.0
            native_full[1, 1] = 1.0
            native_full[1, 23:43] = 1.0
            def physical_row(step, time_s):
                return {
                    'step_index': step, 'time': time_s, 'segment': 'forward',
                    'phase': 0.0, 'holding': False,
                    'command': [0.0, 1.0, 0.0, 115.0],
                    'qpos': [0.0] * 27,
                    'torque_nm': [0.0] * 20,
                    'velocity_rad_s': [0.0] * 20,
                    'native_trace_index': 0,
                    'native_trace_source_index': 1,
                    'native_trace_time_s': 0.02,
                    'native_phase': 0.0,
                    'native_moving': True,
                    'native_ready': True,
                    'native_angles_deg': [0.0] * 20,
                    'native_enabled': [True] * 20,
                    'native_command': [0.0, 1.0, 0.0, 115.0],
                }
            result = {
                'case': case,
                # The simulation itself has not run the independent exact
                # source proof yet; final-case results remain explicitly
                # pending until this checker combines that evidence.
                'status': 'PENDING_EXTERNAL_PROXY_PROOF',
                'external_proxy_proof_required': True,
                'checks': {
                    'completed': True, 'numeric_stability': True,
                    'inputs_unchanged': True,
                    'fixed_proxy_exclusion_contract_applied': True,
                    'no_fall': True,
                    'timeseries_complete': True,
                },
                'collision_proxy_compiled': {
                    'status': 'PASS', 'compiled_count': 5,
                    'expected_signatures': [], 'compiled_excludes': [],
                },
                'input_sha256': ledger,
                'input_sha256_current': ledger,
                'collision_cache_ledger': {'manifest': {}, 'entries': []},
                'collision_cache_ledger_current': {'manifest': {}, 'entries': []},
                'model_manifest': {
                    'model_path': str(model_path),
                    'model_sha256': hashlib.sha256(model_path.read_bytes()).hexdigest(),
                },
                'joint_order': list(S.ALL_JOINTS),
                'controller_kind': 'native',
                'requested_time_s': .02,
                'total_sim_time_s': .02,
                'valid_integrated_time_s': .02,
                'timeseries_sample_stride_steps': 50,
                'timeseries_expected_row_count': 2,
                'timeseries_expected_step_indices': [0, 9],
                'timeseries_complete': True,
                'timeseries': [
                    physical_row(0, .002),
                    physical_row(9, .02),
                ],
                'initial_position_m': [0.0, 0.0, 0.115],
                'max_abs_roll_deg': 0.0,
                'max_abs_pitch_deg': 0.0,
                'min_base_z_m': 0.115,
                'max_abs_qvel': 0.0,
                'initial_self_penetration_m': {},
                'max_self_penetration_m': 0.0,
                'nonleg_contact_steps': 0,
                'ik_counts': {},
                'mass_kg': 1.0,
                'actuators': {},
                'positive_mechanical_power_W': {'mean': 0.0, 'max': 0.0, 'p95': 0.0},
                'axis_constraint_acceptance': {'axis_all': True},
                'enablement_contract': {'all_twenty_required_after_ready': True},
                'tpu_support': {'required': True, 'fraction': 1.0},
                'segments': [{'name': 'forward', 'holding_at_end': False}],
                'native_trace_mode': 'ready',
                'native_trace_initial_row': native_full[0].tolist(),
                'native_trace_row_count': 2,
            }
            replay = json.loads(json.dumps(result))
            replay['status'] = 'PASS'
            digest = PX._physical_result_content_sha(result)
            result['physical_result_content_sha256'] = digest
            result['physical_result_file_sha256'] = digest
            result_path.write_text(json.dumps(result), encoding='utf-8')
            output = folder / 'proof' / 'proof.json'
            with patch.object(T, '_validate_case_inputs', side_effect=lambda value: value), \
                 patch.object(PX, '_runtime_input_fingerprints', return_value=ledger), \
                 patch.object(PX, '_world_parts', return_value={}), \
                 patch.object(PX, '_scan_pair', return_value=clean_scan):
                accepted = PX._load_physical_result(
                    result_path, case, 'c' * 64, {}, output,
                    native=native_full,
                    replay=replay)
            self.assertEqual(accepted['status'], 'PASS_REALIZED_QPOS')

            forged_qpos = json.loads(json.dumps(result))
            forged_qpos['timeseries'][0]['qpos'][7] = 0.25
            forged_path = folder / 'forged-qpos.json'
            forged_path.write_text(json.dumps(forged_qpos), encoding='utf-8')
            with patch.object(T, '_validate_case_inputs',
                              side_effect=lambda value: value):
                with self.assertRaisesRegex(ValueError, 'timeseries contract'):
                    PX._load_physical_result(
                        forged_path, case, 'c' * 64, {}, output,
                        native=native_full,
                        replay=replay)

            for field, value in (('status', 'PASS'), ('status', 'FAIL')):
                bad = dict(result)
                bad['status'] = value
                bad_path = folder / 'bad-status.json'
                bad_path.write_text(json.dumps(bad), encoding='utf-8')
                with patch.object(T, '_validate_case_inputs',
                                  side_effect=lambda value: value):
                    with self.assertRaisesRegex(
                            ValueError, 'status must be PENDING_EXTERNAL_PROXY_PROOF'):
                        PX._load_physical_result(
                            bad_path, case, 'c' * 64, {}, output,
                            native=native_full,
                            replay=replay)

            bad = json.loads(result_path.read_text(encoding='utf-8'))
            bad['checks']['no_fall'] = False
            bad_path = folder / 'bad-fall.json'
            bad_path.write_text(json.dumps(bad), encoding='utf-8')
            with patch.object(T, '_validate_case_inputs',
                              side_effect=lambda value: value):
                with self.assertRaisesRegex(ValueError, 'timeseries contract'):
                    PX._load_physical_result(
                        bad_path, case, 'c' * 64, {}, output,
                        native=native_full,
                        replay=replay)

            with patch.object(PX, '_runtime_input_fingerprints',
                              return_value={'base': 'stale',
                                            '$PROXY_EXCLUSION_CONTRACT': contract['sha256']}), \
                 patch.object(PX, '_world_parts', return_value={}), \
                 patch.object(PX, '_scan_pair', return_value=clean_scan), \
                 patch.object(T, '_validate_case_inputs', side_effect=lambda value: value):
                with self.assertRaisesRegex(ValueError, 'input ledger is stale'):
                    PX._load_physical_result(
                        result_path, case, 'c' * 64, {}, output,
                        native=native_full,
                        replay=replay)

    def test_proxy_checker_binds_all_pair_audit_to_current_input_ledger(self):
        """古いall-pair監査や欠落監査は現在の入力台帳へ結合できない。"""
        contract = print_first_proxy_exclusion_contract()
        base = {
            'status': 'PASS_FINITE_TRACE', 'coverage': 'finite_native_trace',
            'finite_pose_sweep_requested': True,
            'finite_pose_sweep_complete': True,
            'finite_pose_sweep_clean': True, 'inputs_unchanged': True,
            'source_trace_sha256': 'd' * 64,
            'source_trace_sha256_current': 'd' * 64,
            'processed_pose_count': 1, 'native_pose_count': 1,
            'expected_pose_count_matches': True, 'pose_count_matches': True,
            'continuous_reachable_set_proven': False,
            'proxy_exclusion_contract': contract,
            'proxy_exclusion_compiled': {'status': 'PASS'},
            'input_sha256': {'old': '1' * 64},
            'input_sha256_current': {'old': '1' * 64},
            'print_first_inventory': {'status': 'PASS'},
            'print_first_inventory_current': {'status': 'PASS'},
        }
        with tempfile.TemporaryDirectory(prefix='tachikoma-proxy-audit-') as temp:
            folder = Path(temp)
            audit = folder / 'audit.json'
            audit.write_text(json.dumps(base), encoding='utf-8')
            report = PX._validate_self_collision_audit(
                audit, 'd' * 64, contract, folder / 'out.json',
                expected_input_hashes={'new': '2' * 64}, expected_pose_count=1)
            self.assertEqual(report['status'], 'FAIL')
            self.assertTrue(any('stale' in error for error in report['errors']))
            missing = PX._validate_self_collision_audit(
                folder / 'missing.json', 'd' * 64, contract, folder / 'out.json',
                expected_input_hashes={'new': '2' * 64}, expected_pose_count=1)
            self.assertEqual(missing['status'], 'FAIL')

            claimed = dict(base)
            claimed['input_sha256'] = {'new': '2' * 64}
            claimed['input_sha256_current'] = {'new': '2' * 64}
            claimed['continuous_reachable_set_proven'] = True
            claimed['continuous_status'] = 'PASS'
            claimed_path = folder / 'claimed-continuous.json'
            claimed_path.write_text(json.dumps(claimed), encoding='utf-8')
            self_declared = PX._validate_self_collision_audit(
                claimed_path, 'd' * 64, contract, folder / 'out.json',
                expected_input_hashes=claimed['input_sha256'], expected_pose_count=1)
            self.assertEqual(self_declared['status'], 'FAIL')
            self.assertTrue(any('continuous reachable-set' in error
                                for error in self_declared['errors']))

    def test_proxy_checker_deep_validates_each_audit_pose_and_pair(self):
        """自己干渉台帳の姿勢・20軸角度・全リンク組を実traceへ突合する。"""
        contract = print_first_proxy_exclusion_contract()
        native = np.zeros((1, 43), dtype=float)
        link_names = ['a', 'b']
        pair = {
            'links': ['a', 'b'], 'hull_penetration_mm': None,
            'bbox_candidate_pairs': 0, 'classification': 'NO_INTERSECTION',
            'actual_intersections': [], 'errors': [],
        }
        payload = {
            'status': 'PASS_FINITE_TRACE', 'coverage': 'finite_native_trace',
            'finite_pose_sweep_requested': True,
            'finite_pose_sweep_complete': True, 'finite_pose_sweep_clean': True,
            'inputs_unchanged': True, 'source_trace_sha256': 'd' * 64,
            'source_trace_sha256_current': 'd' * 64,
            'processed_pose_count': 1, 'native_pose_count': 1,
            'expected_pose_count_matches': True, 'pose_count_matches': True,
            'continuous_reachable_set_proven': False,
            'proxy_exclusion_contract': contract,
            'proxy_exclusion_compiled': {'status': 'PASS'},
            'input_sha256': {'new': '2' * 64},
            'input_sha256_current': {'new': '2' * 64},
            'print_first_inventory': {'status': 'PASS'},
            'print_first_inventory_current': {'status': 'PASS'},
            'collision_cache_ledger': {'manifest': {}, 'entries': []},
            'collision_cache_ledger_current': {'manifest': {}, 'entries': []},
            'link_names': link_names, 'expected_link_pair_count': 1,
            'link_pair_coverage': 'all_link_pairs',
            'poses': [{
                'pose_index': 0, 'native_phase': 0.0,
                'angles_deg': {name: 0.0 for name in S.ALL_JOINTS},
                'pairs': [pair],
            }],
        }
        with tempfile.TemporaryDirectory(prefix='tachikoma-proxy-deep-audit-') as temp:
            folder = Path(temp)
            audit = folder / 'audit.json'
            audit.write_text(json.dumps(payload), encoding='utf-8')
            accepted = PX._validate_self_collision_audit(
                audit, 'd' * 64, contract, folder / 'out.json',
                expected_input_hashes=payload['input_sha256'],
                expected_pose_count=1, expected_native=native,
                expected_link_names=link_names)
            self.assertEqual(accepted['status'], 'PASS_FINITE_TRACE')

            bad = json.loads(audit.read_text(encoding='utf-8'))
            bad['poses'][0]['angles_deg'][S.ALL_JOINTS[0]] = 1.0
            bad['poses'][0]['pairs'].append(dict(pair))
            bad_path = folder / 'bad-audit.json'
            bad_path.write_text(json.dumps(bad), encoding='utf-8')
            rejected = PX._validate_self_collision_audit(
                bad_path, 'd' * 64, contract, folder / 'out.json',
                expected_input_hashes=payload['input_sha256'],
                expected_pose_count=1, expected_native=native,
                expected_link_names=link_names)
            self.assertEqual(rejected['status'], 'FAIL')
            self.assertTrue(any('angle differs' in error or 'duplicate' in error
                                for error in rejected['errors']))

    def test_proxy_checker_recomputes_source_pairs_and_rejects_forged_empty_rows(self):
        """保存台帳が空でも、現行STLの実交差を独立再計算して落とす。"""
        native = np.zeros((1, 43), dtype=float)
        # The two link frames are separated by less than the deliberately large
        # source boxes below, so the current source Boolean must find a volume.
        source_box = trimesh.creation.box((1000., 1000., 1000.))
        parts = {
            'base_link': [(source_box.copy(), 'source_base')],
            'leg_fr_coxa': [(source_box.copy(), 'source_coxa')],
        }
        forged = {
            'poses': [{'pairs': [{
                'links': ['base_link', 'leg_fr_coxa'],
                'sample_count': 1,
                'bbox_candidate_pairs': 0,
                'boolean_evaluations': 0,
                'positive_below_threshold_count': 0,
                'max_intersection_mm3': 0.,
                'actual_intersections': [], 'errors': [],
            }]}],
        }
        report = PX._independent_all_pair_source_check(
            parts, native, payload=forged)
        self.assertEqual(report['status'], 'FAIL')
        self.assertTrue(report['pairs'][0]['intersections'])
        self.assertTrue(report['stored_evidence_mismatches'])

    def test_proxy_checker_rejects_forged_frame_pass_when_compiled_frame_fails(self):
        """stored frame PASSだけでは、現compiled frame不一致を隠せない。"""
        contract = print_first_proxy_exclusion_contract()
        native = np.zeros((1, 43), dtype=float)
        payload = {
            'status': 'PASS_FINITE_TRACE', 'coverage': 'finite_native_trace',
            'finite_pose_sweep_requested': True,
            'finite_pose_sweep_complete': True, 'finite_pose_sweep_clean': True,
            'inputs_unchanged': True, 'source_trace_sha256': 'd' * 64,
            'source_trace_sha256_current': 'd' * 64,
            'processed_pose_count': 1, 'native_pose_count': 1,
            'expected_pose_count_matches': True, 'pose_count_matches': True,
            'continuous_reachable_set_proven': False,
            'proxy_exclusion_contract': contract,
            'proxy_exclusion_compiled': {'status': 'PASS'},
            'input_sha256': {'new': '2' * 64},
            'input_sha256_current': {'new': '2' * 64},
            'print_first_inventory': {'status': 'PASS'},
            'print_first_inventory_current': {'status': 'PASS'},
            'collision_cache_ledger': {'manifest': {}, 'entries': []},
            'collision_cache_ledger_current': {'manifest': {}, 'entries': []},
            'link_names': ['a', 'b'], 'expected_link_pair_count': 1,
            'link_pair_coverage': 'all_link_pairs',
            'poses': [{
                'pose_index': 0, 'native_phase': 0.,
                'angles_deg': {name: 0. for name in S.ALL_JOINTS},
                'pairs': [{
                    'links': ['a', 'b'], 'sample_count': 1,
                    'bbox_candidate_pairs': 0, 'boolean_evaluations': 0,
                    'positive_below_threshold_count': 0,
                    'max_intersection_mm3': 0.,
                    'actual_intersections': [], 'errors': [],
                }],
            }],
            'frame_transform_contract': {
                'status': 'PASS', 'sample_count': 1,
                'link_names': ['a', 'b'], 'evidence_sha256': 'e' * 64,
                'max_translation_error_mm': 0.,
                'max_rotation_error_rad': 0.,
            },
        }
        with tempfile.TemporaryDirectory(prefix='tachikoma-proxy-frame-') as temp:
            folder = Path(temp)
            audit = folder / 'audit.json'
            audit.write_text(json.dumps(payload), encoding='utf-8')
            with patch.object(PX.SELF, '_compiled_frame_report', return_value={
                    'status': 'FAIL', 'sample_count': 1,
                    'link_names': ['a', 'b'],
                    'evidence_sha256': 'f' * 64,
                    'max_translation_error_mm': 1.,
                    'max_rotation_error_rad': 0.,
            }):
                report = PX._validate_self_collision_audit(
                    audit, 'd' * 64, contract, folder / 'out.json',
                    expected_input_hashes=payload['input_sha256'],
                    expected_pose_count=1, expected_native=native,
                    expected_link_names=['a', 'b'], compiled_model=object())
        self.assertEqual(report['status'], 'FAIL')
        self.assertTrue(any('expected frame' in error
                            for error in report['errors']))

    def test_proxy_runtime_fingerprint_uses_all_supported_freeze_spellings(self):
        """post-hoc台帳はwrapperと同じfreeze manifest探索規則を使う。"""
        with tempfile.TemporaryDirectory(prefix='tachikoma-proxy-fingerprint-') as temp:
            folder = Path(temp)
            model = folder / 'model.urdf'
            freeze = folder / 'freeze.json'
            result = folder / 'results' / 'physical.json'
            model.write_text('<robot/>', encoding='utf-8')
            freeze.write_text('{}', encoding='utf-8')
            case = {'model': {'geometry_freeze_manifest': str(freeze)}}
            result_payload = {
                'physical_result_content_sha256': '',
                'physical_result_file_sha256': '',
            }
            digest = PX._physical_result_content_sha(result_payload)
            result_payload['physical_result_content_sha256'] = digest
            result_payload['physical_result_file_sha256'] = digest
            with patch.object(T, 'input_fingerprints', return_value={'base': 'hash'}):
                hashes = PX._runtime_input_fingerprints(
                    case, result_payload, result, model)
            freeze_key = PX._public_path(freeze, result.parent.parent)
            self.assertIn(freeze_key, hashes)
            stale_file_digest = dict(result_payload)
            stale_file_digest['physical_result_file_sha256'] = '0' * 64
            with patch.object(T, 'input_fingerprints', return_value={'base': 'hash'}):
                with self.assertRaisesRegex(ValueError, 'canonical file SHA'):
                    PX._runtime_input_fingerprints(
                        case, stale_file_digest, result, model)

    def test_proxy_link_contract_rejects_missing_source_link(self):
        """URDF由来の期待リンクにsource meshが無い場合は合格させない。"""
        import export_urdf as E
        with tempfile.TemporaryDirectory(prefix='tachikoma-link-contract-') as temp:
            model = Path(temp) / 'model.urdf'
            model.write_text(
                '<robot name="test"><link name="base_link">'
                '<collision><geometry><box size="1 1 1"/></geometry></collision>'
                '</link></robot>', encoding='utf-8')
            with patch.object(E, 'LINK_PARENT_FRAME', {'base_link': lambda _q: np.eye(4)}):
                report = PX._independent_link_contract(model, None, {})
            self.assertEqual(report['status'], 'FAIL')
            self.assertTrue(any('source link set' in error
                                for error in report['errors']))

    def test_proxy_geometry_contract_binds_urdf_refs_and_rejects_escape(self):
        """各URDF mesh参照のSHA/原点/scaleを展開し、外部参照を拒否する。"""
        model = ROOT / 'hardware/urdf-print-first/tachikoma.urdf'
        root = ET.parse(model).getroot()
        expected = [row.get('name') for row in root.findall('link')
                    if row.get('name') not in PX.PRINT_FIRST_INTENTIONAL_EMPTY_LINKS]
        box = trimesh.creation.box([1., 1., 1.])
        parts = {name: [(box.copy(), '#333333', f'source_{name}')]
                 for name in expected}
        report = PX._urdf_geometry_contract(model, root, expected, None, parts)
        mesh_refs = root.findall('.//mesh')
        self.assertEqual(len(mesh_refs), 80)
        self.assertEqual(
            sum('__col_' in row.get('filename', '') for row in mesh_refs), 24)
        self.assertEqual(
            sum('__col_' not in row.get('filename', '') for row in mesh_refs), 56)
        self.assertEqual(report['row_count'], len(mesh_refs))
        self.assertEqual(len(report['rows']), len(mesh_refs))
        self.assertTrue(all(isinstance(row['sha256'], str)
                            and len(row['sha256']) == 64 for row in report['rows']))
        self.assertTrue(all(len(row['origin_xyz']) == 3
                            and len(row['origin_rpy']) == 3
                            and len(row['scale']) == 3
                            and len(row['transform']) == 4
                            for row in report['rows']))
        self.assertEqual(report['status'], 'FAIL')
        self.assertTrue(any('compiled model is missing' in error
                            for error in report['errors']))
        errors = []
        resolved = PX._resolve_urdf_mesh_path(model, '../outside.stl',
                                               'escape', errors)
        self.assertIsNone(resolved)
        self.assertTrue(any('must not contain ..' in error for error in errors))

    def test_proxy_geometry_contract_rejects_forged_rows_and_exact_cache_counts(self):
        """同一linkのmesh/origin改変とcompiled hull個数の過不足を拒否する。"""
        import mujoco

        with tempfile.TemporaryDirectory(dir=ROOT, prefix='tachikoma-geometry-binding-') as temp:
            folder = Path(temp)
            source_stl = folder / 'source.stl'
            other_stl = folder / 'other.stl'
            box = trimesh.creation.box([1., 1., 1.])
            box.export(source_stl)
            trimesh.creation.box([2., 1., 1.]).export(other_stl)
            model = folder / 'model.urdf'

            def write_urdf(filename='source.stl', origin=''):
                model.write_text(
                    '<robot name="test"><link name="base_link">'
                    f'<collision>{origin}<geometry><mesh filename="{filename}"/>'
                    '</geometry></collision></link></robot>', encoding='utf-8')

            def compiled(geom_names):
                geoms = ''.join(
                    f'<geom name="{name}" type="box" size="0.5 0.5 0.5"/>'
                    for name in geom_names)
                return mujoco.MjModel.from_xml_string(
                    '<mujoco><worldbody><body name="base_link">'
                    f'{geoms}</body></worldbody></mujoco>')

            source_sha = sim_collision._mesh_digest(box)
            hull_sha = 'a' * 64
            convex_sha = 'b' * 64
            cache_sha = 'c' * 64
            cache_path = '$COLLISION_CACHE/test.npz'
            metadata = {
                'link': 'base_link', 'part': 'source_part', 'material': 'PLA',
                'hull_index': 0, 'hull_count': 1, 'hull_sha256': hull_sha,
                'source_mesh_sha256': source_sha,
                'source_convex_hull_sha256': convex_sha,
                'cache_path': cache_path, 'cache_sha256': cache_sha,
            }
            index = {
                'part_metadata': {'part_0_0': metadata},
                'collision_cache_ledger': {'entries': [{
                    'link': 'base_link', 'part': 'source_part',
                    'source_mesh_sha256': source_sha,
                    'source_convex_hull_sha256': convex_sha,
                    'hull_sha256': [hull_sha], 'path': cache_path,
                    'sha256': cache_sha,
                }]},
            }
            parts = {'base_link': [(box, '#333333', 'source_part')]}
            write_urdf()
            root = ET.parse(model).getroot()
            accepted = PX._urdf_geometry_contract(
                model, root, ['base_link'], compiled(['part_0_0']), parts, index)
            self.assertEqual(accepted['status'], 'PASS')
            index['expected_urdf_geometry_rows'] = accepted['rows']

            write_urdf('other.stl')
            forged_path = PX._urdf_geometry_contract(
                model, ET.parse(model).getroot(), ['base_link'],
                compiled(['part_0_0']), parts, index)
            self.assertEqual(forged_path['status'], 'FAIL')
            self.assertTrue(any('path differs from expected source' in error
                                for error in forged_path['errors']))

            write_urdf('source.stl', '<origin xyz="0.1 0 0"/>')
            forged_origin = PX._urdf_geometry_contract(
                model, ET.parse(model).getroot(), ['base_link'],
                compiled(['part_0_0']), parts, index)
            self.assertEqual(forged_origin['status'], 'FAIL')
            self.assertTrue(any('origin_xyz differs from expected source' in error
                                for error in forged_origin['errors']))

            extra = PX._urdf_geometry_contract(
                model, ET.parse(model).getroot(), ['base_link'],
                compiled(['part_0_0', 'part_0_1']), parts, index)
            self.assertEqual(extra['status'], 'FAIL')
            self.assertTrue(any('no part metadata' in error or 'count is not exact' in error
                                for error in extra['errors']))

            missing = dict(index)
            missing['collision_cache_ledger'] = {'entries': [{
                **index['collision_cache_ledger']['entries'][0],
                'hull_sha256': [hull_sha, 'd' * 64],
            }]}
            missing['part_metadata'] = {
                'part_0_0': {**metadata, 'hull_count': 2,
                             'hull_sha256': hull_sha},
            }
            report = PX._urdf_geometry_contract(
                model, ET.parse(model).getroot(), ['base_link'],
                compiled(['part_0_0']), parts, missing)
            self.assertEqual(report['status'], 'FAIL')
            self.assertTrue(any('count is not exact' in error
                                for error in report['errors']))
    def test_renderer_accepts_trace_with_current_config_provenance(self):
        """現行configで生成されたflag-1 traceだけを姿勢入力に許可する。"""
        import json
        import render_print_first as R

        row = {
            'phase': 0.0,
            'moving': False,
            'ready': True,
            'enabled': {name: True for name in R.JOINT_ORDER},
            'angles_deg': {name: 0.0 for name in R.JOINT_ORDER},
        }
        payload = {
            'compile_flag': R.EXPECTED_FLAG,
            'profile_mode': 'candidate_print_first',
            'joint_order': list(R.JOINT_ORDER),
            'row_count': 1,
            'rows': [row],
            'header': {
                'sha256': 'a' * 64,
                'source_config_sha256': R._sha256(R.CONFIG_PATH),
            },
        }
        with tempfile.TemporaryDirectory(prefix='tachikoma-render-contract-') as temp:
            path = Path(temp) / 'trace.json'
            path.write_text(json.dumps(payload), encoding='utf-8')
            angles, metadata = R._load_pose(path, 0)
        self.assertEqual(set(angles), set(R.JOINT_ORDER))
        self.assertEqual(
            metadata['trace_contract']['header_source_config_sha256'],
            metadata['trace_contract']['current_config_sha256'],
        )

    def test_renderer_rejects_stale_trace_config_provenance(self):
        """古いconfigでコンパイルしたtraceを新形状へ流用しない。"""
        import json
        import render_print_first as R

        row = {
            'phase': 0.0,
            'moving': False,
            'ready': True,
            'enabled': {name: True for name in R.JOINT_ORDER},
            'angles_deg': {name: 0.0 for name in R.JOINT_ORDER},
        }
        payload = {
            'compile_flag': R.EXPECTED_FLAG,
            'profile_mode': 'candidate_print_first',
            'joint_order': list(R.JOINT_ORDER),
            'row_count': 1,
            'rows': [row],
            'header': {
                'sha256': 'a' * 64,
                'source_config_sha256': '0' * 64,
            },
        }
        with tempfile.TemporaryDirectory(prefix='tachikoma-render-stale-') as temp:
            path = Path(temp) / 'stale-trace.json'
            path.write_text(json.dumps(payload), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'stale pose trace|source_config_sha256'):
                R._load_pose(path, 0)

    def test_renderer_rejects_trace_with_mismatched_freeze_manifest(self):
        """凍結台帳を指定した描画はtraceと台帳のSHAを同じ束へ固定する。"""
        import hashlib
        import json
        import render_print_first as R

        row = {
            'phase': 0.0,
            'moving': False,
            'ready': True,
            'enabled': {name: True for name in R.JOINT_ORDER},
            'angles_deg': {name: 0.0 for name in R.JOINT_ORDER},
        }
        payload = {
            'compile_flag': R.EXPECTED_FLAG,
            'profile_mode': 'frozen_print_first',
            'joint_order': list(R.JOINT_ORDER),
            'row_count': 1,
            'rows': [row],
            'header': {
                'sha256': 'a' * 64,
                'source_config_sha256': R._sha256(R.CONFIG_PATH),
            },
        }
        with tempfile.TemporaryDirectory(prefix='tachikoma-render-freeze-') as temp:
            root = Path(temp)
            manifest = root / 'freeze-manifest.json'
            manifest.write_text('{"status":"FROZEN"}\n', encoding='utf-8')
            payload['header']['freeze_manifest_sha256'] = hashlib.sha256(
                manifest.read_bytes()
            ).hexdigest()
            trace = root / 'trace.json'
            trace.write_text(json.dumps(payload), encoding='utf-8')
            _angles, metadata = R._load_pose(trace, 0, freeze_manifest=manifest)
            self.assertEqual(
                metadata['trace_contract']['freeze_manifest']['sha256'],
                payload['header']['freeze_manifest_sha256'],
            )
            payload['header']['freeze_manifest_sha256'] = '0' * 64
            trace.write_text(json.dumps(payload), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'freeze_manifest_sha256'):
                R._load_pose(trace, 0, freeze_manifest=manifest)

    def test_foot_difference_rejects_negative_volume_fail_closed(self):
        """殻差分の負体積を数値残片として吸収しない。"""
        class FakeDifference:
            vertices = np.zeros((4, 3), dtype=float)
            faces = np.zeros((1, 3), dtype=np.int64)
            is_empty = False
            is_watertight = True
            is_winding_consistent = True
            is_volume = False

            def __init__(self, volume):
                self.volume = volume

            def split(self, only_watertight=False):
                return [self]

        for volume in (-1.0, -1e-7):
            with self.subTest(volume=volume):
                with self.assertRaisesRegex(ValueError, '有限かつ非負'):
                    _validated_difference_parts(FakeDifference(volume), label='regression')

    def test_foot_difference_empty_result_keeps_four_value_contract(self):
        """空差分も通常差分と同じ4値を返す。"""
        class EmptyDifference:
            vertices = np.empty((0, 3), dtype=float)
            faces = np.empty((0, 3), dtype=np.int64)
            is_empty = True

        result = _validated_difference_parts(EmptyDifference(), label='empty-regression')
        self.assertEqual(len(result), 4)
        self.assertEqual(result[0], None)
        self.assertEqual(result[1:], ([], [], 0.0))

    def test_foot_boolean_volume_checks_status_nan_and_signed_tolerance(self):
        """足裏の移動/耳閉じ経路はnative状態と符号を一括検査する。"""
        from manifold3d import Manifold, Mesh
        from lib import box, to_trimesh

        empty = box(1., 1., 1.) - box(2., 2., 2.)
        self.assertEqual(_checked_manifold_boolean_volume(empty, 'empty'), 0.0)

        class FakeMesh:
            vert_properties = np.zeros((4, 3), dtype=float)
            tri_verts = np.zeros((4, 3), dtype=np.int64)

        class FakeResult:
            def __init__(self, status_name='NoError', volume=float('nan')):
                self._status_name = status_name
                self._volume = volume

            def status(self):
                return type('Status', (), {'name': self._status_name})()

            def is_empty(self):
                return False

            def to_mesh(self):
                return FakeMesh()

            def volume(self):
                return self._volume

        with self.assertRaisesRegex(ValueError, 'status is not NoError'):
            _checked_manifold_boolean_volume(FakeResult('Error', 0.0), 'bad-status')
        with self.assertRaisesRegex(ValueError, 'non-finite'):
            _checked_manifold_boolean_volume(FakeResult('NoError', float('nan')), 'nan')

        mesh = to_trimesh(box(0.008, 0.008, 0.008))
        reversed_mesh = mesh.copy()
        reversed_mesh.faces = reversed_mesh.faces[:, ::-1]
        raw = Mesh(np.asarray(reversed_mesh.vertices, np.float32),
                   np.asarray(reversed_mesh.faces, np.uint32))
        tiny_negative = Manifold(raw)
        self.assertLess(tiny_negative.volume(), 0.0)
        audit = []
        self.assertEqual(
            _checked_manifold_boolean_volume(tiny_negative, 'tiny-negative', audit=audit),
            0.0)
        self.assertEqual(audit[0]['native_status'], 'NoError')

        large_mesh = to_trimesh(box(1., 1., 1.))
        large_mesh.faces = large_mesh.faces[:, ::-1]
        large_negative = Manifold(Mesh(np.asarray(large_mesh.vertices, np.float32),
                                        np.asarray(large_mesh.faces, np.uint32)))
        with self.assertRaisesRegex(ValueError, 'negative beyond tolerance'):
            _checked_manifold_boolean_volume(large_negative, 'large-negative')

    def test_foot_difference_records_only_native_thin_negative_slivers(self):
        """差分の負薄片は根拠を記録し、厚片・閾値超過・符号不一致を拒否する。"""
        import trimesh
        from manifold3d import Manifold, Mesh

        outer = trimesh.creation.box((2., 2., 2.))
        sliver = trimesh.creation.box((.008, .008, .008))
        sliver.apply_translation([5., 0., 0.])
        sliver.invert()
        combined = trimesh.util.concatenate([outer, sliver])
        removed, pieces, _significant, _positive_fragments = _validated_difference_parts(
            combined, label='observed-sliver')
        self.assertGreater(removed, 0.)
        negative = [(piece, volume) for piece, volume in pieces if volume < 0]
        self.assertEqual(len(negative), 1)
        self.assertLess(abs(negative[0][1]), 1e-6)
        audit = negative[0][0].metadata['signed_negative_validation']
        self.assertEqual(audit[0]['native_status'], 'NoError')
        self.assertEqual(audit[0]['strict_boolean_tolerance_mm3'], 1e-6)
        self.assertLess(audit[0]['serialized_volume_mm3'], 0.)

        thick = trimesh.creation.box((.12, .12, .12))
        thick.apply_translation([5., 0., 0.])
        thick.invert()
        with self.assertRaisesRegex(ValueError, 'negative fragment exceeds'):
            _validated_difference_parts(
                trimesh.util.concatenate([outer, thick]), label='thick-negative')

        class PositiveNative:
            def status(self):
                return type('Status', (), {'name': 'NoError'})()

            def volume(self):
                return 1e-7

            def num_vert(self):
                return 8

            def num_tri(self):
                return 12

        with patch('manifold3d.Manifold', return_value=PositiveNative()):
            with self.assertRaisesRegex(ValueError, 'signed volume mismatch'):
                _validated_difference_parts(combined, label='sign-mismatch')

    def test_print_first_leg_generation_requires_yaw_and_tibia_contracts(self):
        """脚生成は接続／脛キャップ契約がPASSでなければ保存しない。"""
        import make_print_first_leg as L

        L._require_geometry_contract_status(
            {'connection_status': 'PASS'}, 'connection_status', 'coxa')
        L._require_geometry_contract_status(
            {'status': 'PASS'}, 'status', 'tibia')
        for record, key in (({'connection_status': 'FAIL'}, 'connection_status'),
                            ({'status': 'FAIL'}, 'status'),
                            ({}, 'status'),
                            (None, 'status')):
            with self.subTest(record=record, key=key):
                with self.assertRaisesRegex(ValueError, 'must be PASS'):
                    L._require_geometry_contract_status(record, key, 'regression')

    def test_xiao_intersection_rejects_nonfinite_or_negative_volume(self):
        """XIAO保持台の非空交差は有限・非負だけを受け入れる。"""
        class Operand:
            def copy(self):
                return self

        class Intersection:
            def __init__(self, volume, *, empty=False):
                self.volume = volume
                self.is_empty = empty
                self.faces = np.zeros((0 if empty else 1, 3), dtype=np.int64)

        a = Operand()
        b = Operand()
        with patch(
            'trimesh.boolean.intersection',
            return_value=Intersection(float('nan')),
        ):
            with self.assertRaisesRegex(ValueError, 'finite and non-negative'):
                X._intersection_volume_mm3(a, b)
        with patch(
            'trimesh.boolean.intersection',
            return_value=Intersection(-1.0),
        ):
            with self.assertRaisesRegex(ValueError, 'finite and non-negative'):
                X._intersection_volume_mm3(a, b)

        # An explicitly empty boolean result is the only non-finite/absent
        # volume that is normalized to zero.
        with patch(
            'trimesh.boolean.intersection',
            return_value=Intersection(float('nan'), empty=True),
        ):
            self.assertEqual(X._intersection_volume_mm3(a, b), 0.0)

    def test_invalid_final_simulation_never_supplies_contact_force(self):
        """最終hashが一致しても初期干渉結果の荷重は足要求値に使わない。"""
        import json

        freeze_hash = 'c' * 64
        manifest_path = '$OUTPUT/models/test.manifest.json'
        model_manifest = {
            'model_kind': 'final_integrated',
            'model_sha256': 'a' * 64,
            'source_urdf_sha256': 'b' * 64,
            'geometry_freeze': {
                'geometry_freeze_hash': freeze_hash,
                'geometry_freeze_time': '2026-09-05T13:35:01Z',
            },
            'manifest_path': manifest_path,
            'assembly_inputs': [
                {'role': role, 'path': f'outputs/{role}.json'}
                for role in ('assembly_manifest', 'feet_manifest', 'leg_manifest')
            ],
            'pla_phi10_plug_roots': {
                leg: {'frame': 'tibia', 'xyz_mm': [0, 0, -135]}
                for leg in ('FR', 'FL', 'RL', 'RR')
            },
        }
        payload = {
            'status': 'INVALID_INITIAL_CONTACT_MODEL',
            'case': {'model': {
                'model_kind': 'final_integrated',
                'model_manifest': manifest_path,
                'geometry_freeze_hash': freeze_hash,
            }},
            'model_manifest': model_manifest,
            'input_sha256': {manifest_path: 'd' * 64},
            'checks': {
                'completed': True,
                'numeric_stability': True,
                'inputs_unchanged': True,
                'initial_self_penetration_le_0p1mm': False,
                'no_fall': True,
            },
            'foot_load_by_leg': {
                'FR': {'max_vertical_contact_force_N': 999,
                       'max_horizontal_contact_force_N': 999},
            },
        }
        with tempfile.TemporaryDirectory(prefix='tachikoma-invalid-sim-contract-') as temp:
            path = Path(temp) / 'invalid.json'
            path.write_text(json.dumps(payload))
            report = _simulation_force_summary(path)
        self.assertEqual(report['status'], 'UNVERIFIED_INVALID_INITIAL_CONTACT_MODEL')
        self.assertIsNone(report['max_normal_contact_force_N'])
        self.assertIsNone(report['max_horizontal_contact_force_N'])
        self.assertFalse(report['simulation_quality']['load_is_eligible'])

    def test_root_moment_sums_each_contact_force_and_torque(self):
        contacts=[
            {'pos':[.1,0.,0.],'force':[0.,0.,10.],'torque':[0.,0.,.2]},
            {'pos':[0.,.2,0.],'force':[0.,0.,5.],'torque':[.1,0.,0.]},
        ]
        moment,norm,components=contact_root_moment(contacts,[0.,0.,0.],[0.,0.,1.])
        expected=[1.1,-1.,.2]
        self.assertEqual(moment.tolist(),expected)
        self.assertAlmostEqual(norm,(1.1**2+1.**2+.2**2)**.5)
        self.assertAlmostEqual(components[0],(1.1**2+1.**2)**.5)
        self.assertAlmostEqual(components[1],.2)

    def test_candidate_hashes_cover_exact_six_stls_and_detect_one_change(self):
        source=ROOT/'docs/audits/20260905-round2/foot-support-candidates'
        names=sorted(p.name for p in source.glob('FR_*_candidate.stl'))
        self.assertEqual(names,[
            'FR_0_shoe_fitted_candidate.stl','FR_0_toe_hidden_seat_candidate.stl',
            'FR_1_shoe_fitted_candidate.stl','FR_1_toe_hidden_seat_candidate.stl',
            'FR_2_shoe_fitted_candidate.stl','FR_2_toe_hidden_seat_candidate.stl'])
        with tempfile.TemporaryDirectory(prefix='tachikoma-hash-contract-') as temp:
            out=Path(temp);folder=out/'candidates';folder.mkdir()
            for name in names:shutil.copy2(source/name,folder/name)
            old_output=T.FINGERPRINT_OUTPUT_ROOT
            try:
                T.FINGERPRINT_OUTPUT_ROOT=out
                case={'model':{'foot_candidate_dir':str(folder)}}
                before=T.input_fingerprints(case)
                keys={key for key in before if key.startswith('$OUTPUT/candidates/FR_')}
                self.assertEqual(len(keys),6)
                target=folder/'FR_1_shoe_fitted_candidate.stl'
                original=target.read_bytes();target.write_bytes(original+b'\n')
                after=T.input_fingerprints(case)
                key='$OUTPUT/candidates/FR_1_shoe_fitted_candidate.stl'
                self.assertNotEqual(before[key],after[key])
                self.assertEqual(set(before)-{key},set(after)-{key})
            finally:
                T.FINGERPRINT_OUTPUT_ROOT=old_output

    def test_input_fingerprints_include_print_first_occupancy_sources(self):
        """電装占有の生成元と保存計画を入力台帳へ含める。"""
        old_output = T.FINGERPRINT_OUTPUT_ROOT
        try:
            T.FINGERPRINT_OUTPUT_ROOT = None
            before = T.input_fingerprints({'model': {}})
            component_key = 'tools/print_first_components.py'
            plan_key = 'tools/xiao_retention_plan.py'
            saved_plan_key = 'docs/audits/20260905-round2/xiao-retention-plan.json'
            self.assertIn(component_key, before)
            self.assertIn(plan_key, before)
            self.assertIn(saved_plan_key, before)
            component_path = ROOT / component_key
            plan_path = ROOT / saved_plan_key
            component_before = component_path.read_bytes()
            plan_before = plan_path.read_bytes()
            try:
                component_path.write_bytes(component_before + b'\n')
                after_component = T.input_fingerprints({'model': {}})
                self.assertNotEqual(before[component_key], after_component[component_key])
                plan_path.write_bytes(plan_before + b'\n')
                after_plan = T.input_fingerprints({'model': {}})
                self.assertNotEqual(before[saved_plan_key], after_plan[saved_plan_key])
            finally:
                component_path.write_bytes(component_before)
                plan_path.write_bytes(plan_before)
        finally:
            T.FINGERPRINT_OUTPUT_ROOT = old_output

    def test_external_output_key_has_no_absolute_path(self):
        with tempfile.TemporaryDirectory(prefix='tachikoma-output-contract-') as temp:
            path=Path(temp)/'generated.urdf';path.write_text('test')
            key=S.fingerprint_key(path,Path(temp))
            self.assertEqual(key,'$OUTPUT/generated.urdf')
            external=S.fingerprint_key(path)
            self.assertTrue(external.startswith('$EXTERNAL/generated.urdf#'))
            self.assertNotIn(str(path),external)

    def test_publicized_case_output_refs_round_trip_from_case_location(self):
        """保存済みケースの$OUTPUT参照を元の出力束へ戻せる。"""
        import json
        with tempfile.TemporaryDirectory(prefix='tachikoma-case-path-contract-') as temp:
            root=Path(temp)/'run';(root/'cases').mkdir(parents=True)
            case_path=root/'cases'/'case.json'
            case_path.write_text(json.dumps({'name':'case','model':{
                'freeze_manifest':'$OUTPUT/freeze-manifest.json',
                'model_path':'hardware/urdf-print-first/tachikoma.urdf'}}))
            self.assertEqual(case_output_root(case_path),root.resolve())
            resolved=resolve_case_output_refs({'ref':'$OUTPUT/models/model.json'},root)
            self.assertEqual(resolved['ref'],str((root/'models/model.json').resolve()))
            loaded=load_case_input(case_path)
            self.assertEqual(loaded[0]['model']['freeze_manifest'],
                             str((root/'freeze-manifest.json').resolve()))

    def test_final_case_gate_rejects_inherited_legacy_collision_defaults(self):
        case={'name':'missing-final-gates','profile':dict(PRINT_FIRST_PROFILE),
              'model':{'model_kind':'final_integrated','assembly_context':True,
                       'contact_model':'vhacd','geometry_freeze_time':'2026-09-05T00:00:00Z',
                       'geometry_freeze_hash':'freeze-sha','full_reachable_mesh_audit':True,
                       'pla_phi10_plug_roots':{leg:{'xyz_mm':[0,0,-135]} for leg in ('FR','FL','RL','RR')}}}
        report=final_case_requirements(case,raise_on_missing=False)
        self.assertEqual(report['status'],'UNVERIFIED')
        self.assertTrue(report['checks']['finite_native_trace_mesh_audit_alias_used'])
        self.assertIn('model.self_collision=true',report['missing'])
        self.assertIn('model.include_servo_collision=true (include_servos)',report['missing'])
        with self.assertRaisesRegex(ValueError,'final model requirements unmet'):
            final_case_requirements(case)

    def test_final_case_factory_sets_explicit_collision_inputs(self):
        case=cases_for({'candidate':dict(PRINT_FIRST_PROFILE)},model_kind='final_integrated')[0]
        options=case['model']
        self.assertTrue(options['assembly_context'])
        self.assertTrue(options['self_collision'])
        self.assertTrue(options['include_servo_collision'])
        self.assertTrue(options['include_parent_collision'])
        self.assertTrue(options['finite_native_trace_mesh_audit'])
        self.assertEqual(options['group_voltage_V'],{'leg':6.0,'arm':5.0,'eye':5.0})
        self.assertEqual(options['torque_model'], 'linear-speed')

    def test_formal_motion_quality_factory_explicitly_covers_four_commands(self):
        cases = formal_motion_quality_cases_for(
            {'pf1': dict(PRINT_FIRST_PROFILE)},
            model_options={'geometry_freeze_time': 'pending',
                           'geometry_freeze_hash': 'pending',
                           'freeze_manifest': 'pending'},
        )
        self.assertEqual(len(cases), 4)
        self.assertEqual(
            {case['motion_quality_kind'] for case in cases},
            {'long_forward', 'turn', 'stop_restart'},
        )
        by_name = {case['name']: case for case in cases}
        forward = by_name['pf1_forward_quality']
        self.assertEqual(
            forward['motion_quality_criteria']['expected_command_vector'],
            [0.0, 1.0, 0.0],
        )
        positive = by_name['pf1_turn_positive_quality']
        negative = by_name['pf1_turn_negative_quality']
        self.assertEqual(
            positive['motion_quality_criteria']['expected_command_vector'],
            [0.0, 0.0, 0.75],
        )
        self.assertEqual(
            negative['motion_quality_criteria']['expected_command_vector'],
            [0.0, 0.0, -0.75],
        )
        restart = by_name['pf1_stop_restart_quality']
        self.assertEqual(
            restart['motion_quality_criteria']['restart']['expected_command_vector'],
            [0.0, 1.0, 0.0],
        )
        self.assertTrue(all(case['model']['include_parent_collision'] for case in cases))
        self.assertTrue(all(case['model']['torque_model'] == 'linear-speed' for case in cases))

    def test_hardware_occupancy_envelopes_are_not_classified_as_printed_pla(self):
        for name in ('xiao_all_boards_occupancy', 'camera_child_lens_occupancy',
                     'component_esp32_devkit'):
            self.assertEqual(collision_material(name), 'ELECTRONICS_ENVELOPE')
        self.assertEqual(collision_material('pf_camera_carrier'), 'PLA')

    def test_final_collision_contract_requires_valid_manifest_four_tpu_legs_and_support_error(self):
        import json
        valid_geometry = {
            'finite': True, 'watertight': True, 'winding_consistent': True,
            'is_volume': True, 'volume_mm3': 10.0,
        }
        rows = []
        for leg in S.sg._LEGS:
            rows.append({
                'link': f'leg_{leg.lower()}_tibia',
                'part': 'tpu_shoe',
                'material': 'TPU',
                'hull_count': 1,
                'source_volume_mm3': 10.0,
                'hulls_sum_volume_mm3': 10.1,
                'hulls_sum_minus_source_volume_mm3': .1,
                'hulls_sum_minus_source_volume_fraction': .01,
                'single_hull_volume_ratio': 1.01,
                'source_validation': dict(valid_geometry),
                'hull_validations': [dict(valid_geometry)],
                'support_z_min_error_mm': .05,
                'support_z_max_error_mm': -.04,
            })
        with tempfile.TemporaryDirectory(prefix='tachikoma-collision-contract-') as temp:
            manifest = Path(temp) / 'manifest.json'
            manifest.write_text(json.dumps(rows))
            report = collision_geometry_contract(rows, manifest, Path(temp))
            self.assertEqual(report['status'], 'PASS')
            self.assertTrue(report['checks']['convex_manifest_present'])
            self.assertTrue(report['checks']['convex_source_and_hulls_valid'])
            self.assertTrue(report['checks']['tpu_shoes_four_legs_material_tpu'])
            self.assertTrue(report['checks']['no_legacy_foot_pad'])
            self.assertTrue(report['checks']['no_legacy_replacement_parts'])
            self.assertTrue(report['checks']['tpu_support_z_error_le_0p1mm'])

            bad_rows = json.loads(json.dumps(rows))
            bad_rows[0]['hull_validations'][0]['volume_mm3'] = -1.0
            bad_rows[1]['part'] = 'foot_pad'
            bad_rows[2]['support_z_max_error_mm'] = .101
            manifest.write_text(json.dumps(bad_rows))
            bad = collision_geometry_contract(bad_rows, manifest, Path(temp))
            self.assertEqual(bad['status'], 'FAIL')
            self.assertFalse(bad['checks']['convex_source_and_hulls_valid'])
            self.assertFalse(bad['checks']['no_legacy_foot_pad'])
            self.assertFalse(bad['checks']['no_legacy_replacement_parts'])
            self.assertFalse(bad['checks']['tpu_support_z_error_le_0p1mm'])

            duplicate_rows = json.loads(json.dumps(rows))
            duplicate_rows.append(json.loads(json.dumps(rows[0])))
            manifest.write_text(json.dumps(duplicate_rows))
            duplicate = collision_geometry_contract(duplicate_rows, manifest, Path(temp))
            self.assertEqual(duplicate['status'], 'FAIL')
            self.assertFalse(duplicate['checks']['tpu_shoes_four_legs_material_tpu'])

            legacy_rows = json.loads(json.dumps(rows))
            legacy_rows[0]['part'] = 'foot_pad#shoe_FR_0'
            manifest.write_text(json.dumps(legacy_rows))
            legacy = collision_geometry_contract(legacy_rows, manifest, Path(temp))
            self.assertEqual(legacy['status'], 'FAIL')
            self.assertFalse(legacy['checks']['tpu_shoes_four_legs_material_tpu'])
            self.assertFalse(legacy['checks']['no_legacy_foot_pad'])

            suffix_rows = json.loads(json.dumps(rows))
            suffix_rows[0]['part'] = 'tpu_shoe#shoe_FR_0'
            manifest.write_text(json.dumps(suffix_rows))
            suffix = collision_geometry_contract(suffix_rows, manifest, Path(temp))
            self.assertEqual(suffix['status'], 'FAIL')
            self.assertFalse(suffix['checks']['tpu_shoes_four_legs_material_tpu'])

    def test_reachable_audit_keeps_finite_sweep_separate_from_continuous_proof(self):
        import json
        finite = {
            'status': 'PASS_FINITE_TRACE',
            'coverage': 'finite_native_trace',
            'finite_pose_sweep_requested': True,
            'inputs_unchanged': True,
            'finite_pose_sweep_complete': True,
            'finite_pose_sweep_clean': True,
            'processed_pose_count': 1,
            'native_pose_count': 1,
            'expected_pose_count_matches': True,
            'pose_count_matches': True,
            'fixed_base_intersections': [],
            'fixed_base_boolean_errors': [],
            'poses': [{'pairs': [{'actual_intersections': [], 'errors': []}]}],
        }
        with tempfile.TemporaryDirectory(prefix='tachikoma-reachable-contract-') as temp:
            path = Path(temp) / 'audit.json'
            path.write_text(json.dumps(finite))
            assessment = reachable_audit_assessment(path, Path(temp))
            self.assertTrue(assessment['finite_pose_sweep_clean'])
            self.assertEqual(assessment['finite_pose_count'], 1)
            self.assertFalse(assessment['continuous_reachable_set_proven'])
            self.assertEqual(assessment['continuous_status'], 'UNVERIFIED')

            # A finite clean audit cannot promote itself to a continuous
            # reachable-set proof by setting a flag or status.  The
            # independent interval-certificate verifier is not implemented.
            claimed = json.loads(json.dumps(finite))
            claimed['continuous_reachable_set_proven'] = True
            claimed['continuous_status'] = 'PASS'
            path.write_text(json.dumps(claimed))
            self_declared = reachable_audit_assessment(path, Path(temp))
            self.assertTrue(self_declared['finite_pose_sweep_clean'])
            self.assertFalse(self_declared['continuous_reachable_set_proven'])
            self.assertEqual(self_declared['continuous_status'], 'UNVERIFIED')
            self.assertTrue(any('self-declared continuous' in error
                                for error in self_declared['errors']))

            continuous_label = json.loads(json.dumps(finite))
            continuous_label['coverage'] = 'continuous_reachable_set'
            continuous_label['continuous_status'] = 'PASS'
            path.write_text(json.dumps(continuous_label))
            mislabeled = reachable_audit_assessment(path, Path(temp))
            self.assertFalse(mislabeled['continuous_reachable_set_proven'])
            self.assertEqual(mislabeled['continuous_status'], 'UNVERIFIED')
            self.assertTrue(any('self-declared continuous' in error
                                for error in mislabeled['errors']))

            finite['status'] = 'FAIL'
            path.write_text(json.dumps(finite))
            stale_status = reachable_audit_assessment(path, Path(temp))
            self.assertFalse(stale_status['finite_pose_sweep_clean'])
            self.assertTrue(any('status is not PASS_FINITE_TRACE' in error
                                for error in stale_status['errors']))

            finite['status'] = 'PASS_FINITE_TRACE'
            finite['coverage'] = 'sampled_phase_subset'
            path.write_text(json.dumps(finite))
            wrong_coverage = reachable_audit_assessment(path, Path(temp))
            self.assertFalse(wrong_coverage['finite_pose_sweep_clean'])

            finite['coverage'] = 'finite_native_trace'
            finite['finite_pose_sweep_requested'] = False
            path.write_text(json.dumps(finite))
            unrequested = reachable_audit_assessment(path, Path(temp))
            self.assertFalse(unrequested['finite_pose_sweep_clean'])

            finite['finite_pose_sweep_requested'] = True
            finite['expected_pose_count_matches'] = False
            path.write_text(json.dumps(finite))
            count_mismatch = reachable_audit_assessment(path, Path(temp))
            self.assertFalse(count_mismatch['finite_pose_sweep_clean'])

            finite['expected_pose_count_matches'] = True

            finite['poses'][0]['pairs'][0]['actual_intersections'] = [{'volume_mm3': 1.0}]
            path.write_text(json.dumps(finite))
            dirty = reachable_audit_assessment(path, Path(temp))
            self.assertFalse(dirty['finite_pose_sweep_clean'])
            self.assertFalse(dirty['continuous_reachable_set_proven'])

            incomplete = json.loads(json.dumps(finite))
            incomplete['poses'][0]['pairs'][0]['actual_intersections'] = []
            incomplete['finite_pose_sweep_complete'] = False
            incomplete['continuous_reachable_set_proven'] = False
            path.write_text(json.dumps(incomplete))
            incomplete_report = reachable_audit_assessment(path, Path(temp))
            self.assertFalse(incomplete_report['finite_pose_sweep_clean'])
            self.assertFalse(incomplete_report['finite_pose_sweep_complete'])

    def test_native_trace_loader_requires_finite_complete_joint_rows(self):
        import json
        row = {
            'phase': 0.25,
            'moving': False,
            'ready': True,
            'angles_deg': {name: 0.0 for name in S.ALL_JOINTS},
            'enabled': {name: True for name in S.ALL_JOINTS},
        }
        with tempfile.TemporaryDirectory(prefix='tachikoma-native-trace-contract-') as temp:
            path = Path(temp) / 'trace.json'
            def payload(rows, count=None):
                return {
                    'status': 'GENERATED_NATIVE_TRACE',
                    'compile_flag': '-DTACHIKOMA_PRINT_FIRST_PROFILE=1',
                    'profile_mode': 'candidate_print_first',
                    'joint_order': list(S.ALL_JOINTS),
                    'header': {
                        'path': '$OUTPUT/firmware/print_first_gait.h',
                        'sha256': 'a' * 64,
                        'source_config_sha256': print_first_sha(
                            ROOT / 'hardware/src/config.py'),
                    },
                    'row_count': len(rows) if count is None else count,
                    'rows': rows,
                }
            path.write_text(json.dumps(payload([row])))
            values = _load_native_trace(path)
            self.assertEqual(values.shape, (1, 43))
            self.assertTrue(np.isfinite(values).all())
            self.assertAlmostEqual(values[0, 0], .25)

            stale = payload([row])
            stale['header']['source_config_sha256'] = 'b' * 64
            path.write_text(json.dumps(stale))
            with self.assertRaisesRegex(ValueError, 'stale native trace'):
                _load_native_trace(path)

            malformed = dict(row, angles_deg=dict(row['angles_deg']))
            malformed['angles_deg'].pop(S.ALL_JOINTS[-1])
            path.write_text(json.dumps(payload([malformed])))
            with self.assertRaises((KeyError, ValueError, TypeError)):
                _load_native_trace(path)

            nonfinite = dict(row, phase=float('nan'))
            path.write_text(json.dumps(payload([nonfinite])))
            with self.assertRaisesRegex(ValueError, 'finite'):
                _load_native_trace(path)

            bool_string = dict(row, moving='false')
            path.write_text(json.dumps(payload([bool_string])))
            with self.assertRaisesRegex(ValueError, 'boolean'):
                _load_native_trace(path)

            malformed_enabled = dict(row, enabled=dict(row['enabled']))
            malformed_enabled['enabled'][S.ALL_JOINTS[0]] = 'false'
            path.write_text(json.dumps(payload([malformed_enabled])))
            with self.assertRaisesRegex(ValueError, 'boolean'):
                _load_native_trace(path)

            count_mismatch = payload([row], count=2)
            path.write_text(json.dumps(count_mismatch))
            with self.assertRaisesRegex(ValueError, 'row_count'):
                _load_native_trace(path)

    def test_native_numeric_trace_requires_exact_shape_finite_and_binary_flags(self):
        """実C++出力の43列と二値フラグを実行前に固定する。"""
        valid = np.zeros((2, 43), dtype=float)
        valid[:, 2] = 1.0
        valid[:, 23:] = 1.0
        checked = T.validate_native_trace(valid)
        self.assertEqual(checked.shape, (2, 43))
        for candidate, pattern in (
                (valid[:, :42], 'shape'),
                (valid.copy().astype(float), 'non-finite'),
                (valid.copy(), '0/1')):
            if pattern == 'non-finite':
                candidate[0, 7] = np.nan
            elif pattern == '0/1':
                candidate[0, 23] = .5
            with self.assertRaisesRegex(ValueError, pattern):
                T.validate_native_trace(candidate)

    def test_all_twenty_axis_constraints_reject_arm_or_eye_boundary_hold(self):
        """脚だけが健全でも腕/目の上限張り付きは最終ゲートを落とす。"""
        axes = {
            name: {
                'mean_absolute_torque_nm': .1,
                'rms_torque_nm': .2,
                'max_absolute_torque_nm': .3,
                'max_velocity_rad_s': 1.,
                'saturation_fraction': 0.,
                'bound_saturation_fraction': 0.,
                'stall_limit_nm': 1.,
                'no_load_velocity_rad_s': 2.,
            }
            for name in S.ALL_JOINTS
        }
        segment = {
            'axis_saturation_fraction': [0.] * len(S.ALL_JOINTS),
            'axis_bound_saturation_fraction': [0.] * len(S.ALL_JOINTS),
        }
        accepted = T.all_axis_constraint_checks(axes, [segment])
        self.assertTrue(accepted['axis_count_all_20'])
        self.assertTrue(accepted['axis_torque_within_limits_all_20'])
        self.assertTrue(accepted['axis_velocity_within_limits_all_20'])
        self.assertTrue(accepted['axis_saturation_per_segment_all_20_le_5pct'])

        arm_boundary = {name: dict(row) for name, row in axes.items()}
        arm_boundary['arm_r_yaw']['saturation_fraction'] = .051
        arm_boundary['arm_r_yaw']['bound_saturation_fraction'] = .051
        rejected = T.all_axis_constraint_checks(arm_boundary, [segment])
        self.assertFalse(rejected['axis_saturation_all_20_le_5pct'])
        self.assertFalse(rejected['axis_bound_saturation_all_20_le_5pct'])

        eye_over_speed = {name: dict(row) for name, row in axes.items()}
        eye_over_speed['eye_l_roll']['max_velocity_rad_s'] = 2.001
        eye_over_speed['eye_l_roll']['no_load_velocity_rad_s'] = 2.
        self.assertFalse(T.all_axis_constraint_checks(
            eye_over_speed, [segment])['axis_velocity_within_limits_all_20'])

        disabled_arm = np.ones(len(S.ALL_JOINTS), dtype=bool)
        disabled_arm[S.ALL_JOINTS.index('arm_l_elbow')] = False
        self.assertFalse(T.all_axis_constraint_checks(
            axes, [segment], enabled_axes=disabled_arm)['axis_enabled_all_20'])

    def test_voltage_report_source_describes_runtime_readback_only(self):
        metadata = T.voltage_model_metadata(
            {'group_voltage_V': {'leg': 6., 'arm': 5., 'eye': 5.}},
            {'leg': 6., 'arm': 5., 'eye': 5.}, 'linear-speed')
        self.assertIn('runtime', metadata['source'])
        self.assertIn('readback', metadata['source'])
        self.assertNotIn('compiled', metadata['source'])

    def test_renderer_rejects_trace_with_stale_source_config(self):
        """描画もtrace生成時のconfig SHAを現行入力へ束縛する。"""
        rows = [{
            'phase': 0., 'moving': False, 'ready': True,
            'angles_deg': {name: 0. for name in S.ALL_JOINTS},
            'enabled': {name: True for name in S.ALL_JOINTS},
        }]
        payload = {
            'compile_flag': R.EXPECTED_FLAG,
            'profile_mode': 'candidate_print_first',
            'joint_order': list(S.ALL_JOINTS),
            'header': {
                'path': '$OUTPUT/firmware/print_first_gait.h',
                'sha256': 'a' * 64,
                'source_config_sha256': print_first_sha(ROOT / 'hardware/src/config.py'),
            },
            'row_count': 1,
            'rows': rows,
        }
        accepted, _, _ = R._pose_rows(payload)
        self.assertEqual(len(accepted), 1)
        payload['header']['source_config_sha256'] = 'b' * 64
        with self.assertRaisesRegex(ValueError, 'source_config_sha256'):
            R._pose_rows(payload)

    def test_self_collision_boolean_volume_keeps_sign_and_cli_status_fails_closed(self):
        import trimesh
        import config
        self.assertEqual(SC.BOOLEAN_VOLUME_EPS_MM3,
                         float(config.BOOLEAN_NEGATIVE_TOLERANCE_MM3))
        positive = trimesh.creation.box((1., 1., 1.))
        self.assertGreater(_intersection_volume(positive, 'positive'), 0.)
        tiny_negative = positive.copy()
        tiny_negative.invert()
        # The unit cube is intentionally too large for the numerical tolerance.
        with self.assertRaisesRegex(ValueError, 'negative beyond tolerance'):
            _intersection_volume(tiny_negative, 'negative')
        class TinyNegative:
            volume = -0.001
        with self.assertRaisesRegex(ValueError, 'trimesh.Trimesh'):
            _intersection_volume(TinyNegative(), 'tiny')
        empty = trimesh.Trimesh(
            vertices=np.empty((0, 3), dtype=float),
            faces=np.empty((0, 3), dtype=np.int64), process=False)
        self.assertEqual(_intersection_volume(empty, 'empty'), 0.)
        vertices_only = trimesh.Trimesh(
            vertices=np.zeros((4, 3), dtype=float),
            faces=np.empty((0, 3), dtype=np.int64), process=False)
        with self.assertRaisesRegex(ValueError, 'one-sided empty topology'):
            _intersection_volume(vertices_only, 'vertices-only')
        # The opposite one-sided case cannot be a valid indexed mesh (faces
        # would necessarily refer to missing vertices), which is itself the
        # fail-closed condition we need to exercise.
        faces_only = trimesh.creation.box((1., 1., 1.))
        with patch.object(trimesh.Trimesh, 'vertices',
                          new=property(lambda _mesh: np.empty((0, 3), dtype=float))):
            with self.assertRaisesRegex(ValueError,
                                        'one-sided empty topology|face index is out of bounds'):
                _intersection_volume(faces_only, 'faces-only')
        fabricated_empty = trimesh.Trimesh(
            vertices=np.empty((0, 3), dtype=float),
            faces=np.empty((0, 3), dtype=np.int64), process=False)
        with patch.object(type(fabricated_empty), 'volume',
                          new=property(lambda _mesh: -0.001)):
            with self.assertRaisesRegex(ValueError, 'zero volume'):
                _intersection_volume(fabricated_empty, 'fabricated-empty')
        # A real closed topology with a signed remainder within the strict
        # config tolerance is accepted only after native NoError validation.
        tiny = trimesh.creation.box((.009, .009, .009))
        tiny.invert()
        self.assertLess(tiny.volume, 0.)
        self.assertGreaterEqual(tiny.volume,
                                -config.BOOLEAN_NEGATIVE_TOLERANCE_MM3)
        self.assertEqual(_intersection_volume(tiny, 'signed-tiny'), 0.)
        with self.assertRaisesRegex(ValueError, 'result is missing'):
            _intersection_volume(None, 'missing')

        complete = {
            'finite_pose_sweep_complete': True,
            'fixed_base_intersections': [],
            'fixed_base_boolean_errors': [],
            'poses': [{'pairs': [{'actual_intersections': [], 'errors': []}]}],
        }
        self.assertEqual(_audit_status(complete), 'PASS_FINITE_TRACE')
        incomplete = dict(complete, finite_pose_sweep_complete=False)
        self.assertEqual(_audit_status(incomplete), 'INCOMPLETE')
        collision = dict(complete, poses=[{'pairs': [
            {'actual_intersections': [{'intersection_mm3': 1.}], 'errors': []}
        ]}])
        self.assertEqual(_audit_status(collision), 'FAIL')
        # A complete finite sweep may still be a failed geometry audit.  The
        # producer records coverage and cleanliness independently.
        collision['finite_pose_sweep_clean'] = False
        self.assertTrue(collision['finite_pose_sweep_complete'])
        self.assertFalse(collision['finite_pose_sweep_clean'])

    def test_self_collision_cli_writes_incomplete_and_nonzero_on_bad_trace(self):
        """欠落/不正なnative行をCLIが成功扱いで残さない。"""
        import json
        row = {
            'phase': 0., 'moving': 'false', 'ready': True,
            'angles_deg': {name: 0. for name in S.ALL_JOINTS},
            'enabled': {name: True for name in S.ALL_JOINTS},
        }
        with tempfile.TemporaryDirectory(prefix='tachikoma-self-collision-cli-') as temp:
            folder = Path(temp)
            trace = folder / 'bad-trace.json'
            out = folder / 'audit.json'
            trace.write_text(json.dumps({
                'status': 'GENERATED_NATIVE_TRACE',
                'compile_flag': '-DTACHIKOMA_PRINT_FIRST_PROFILE=1',
                'profile_mode': 'candidate_print_first',
                'joint_order': list(S.ALL_JOINTS),
                'header': {
                    'path': '$OUTPUT/firmware/print_first_gait.h',
                    'sha256': 'a' * 64,
                    'source_config_sha256': print_first_sha(
                        ROOT / 'hardware/src/config.py'),
                },
                'row_count': 1,
                'rows': [row],
            }))
            completed = subprocess.run(
                [sys.executable, str(ROOT / 'tools/sim_self_collision.py'),
                 '--out', str(out), '--trace-json', str(trace),
                 '--full-reachable', '--expected-pose-count', '1'],
                capture_output=True, text=True)
            self.assertNotEqual(completed.returncode, 0)
            self.assertTrue(out.is_file())
            payload = json.loads(out.read_text())
            self.assertEqual(payload['status'], 'INCOMPLETE')
            self.assertFalse(payload['finite_pose_sweep_complete'])
            self.assertFalse(payload['finite_pose_sweep_clean'])
            self.assertEqual(payload['processed_pose_count'], 0)
            self.assertTrue(any('boolean' in error for error in payload['errors']))

    def test_self_collision_inventory_is_bound_to_generated_print_first_context(self):
        """自己干渉台帳は生成済み候補の部品名・実STL SHAを記録する。"""
        import print_first_assembly as A
        import sim_collision as C
        mesh = trimesh.creation.box([1., 1., 1.])
        required = list(SC.PRINT_FIRST_REQUIRED_PARTS)
        source = {
            'base_link': [(mesh.copy(), '#fff', name) for name in required],
            **{f'leg_{leg.lower()}_tibia': [(mesh.copy(), '#333', 'tpu_shoe')]
               for leg in ('FR', 'FL', 'RL', 'RR')},
        }
        stl_path = ROOT / 'outputs/print-first-20260905/feet/tpu_shoe_foot_frame.stl'
        stl_sha = hashlib.sha256(stl_path.read_bytes()).hexdigest()
        source_index = {
            name: {'source_kind': 'stl', 'stl_path': str(stl_path.relative_to(ROOT)),
                   'stl_sha256': stl_sha, 'manifest_path': 'test/manifest.json'}
            for name in required + ['tpu_shoe']}
        with A.context(generated=True), patch.object(
                SC, '_print_first_stl_index', return_value=(source_index, {})), \
                patch.object(SC, '_load_verified_stl_mesh', return_value=mesh.copy()):
            inventory = _print_first_inventory_contract(
                source, context_generated=True, require_stl_sha=True)
        self.assertEqual(inventory['status'], 'PASS')
        self.assertTrue(all(inventory['checks'].values()))
        self.assertEqual(inventory['required_new_part_counts'], {
            'pf_head_top_clearanced': 1,
            'pf_eye_pod_camera_clearanced': 1,
            'pf_camera_carrier': 1,
        })
        shoes = [row for row in inventory['parts'] if row['part'] == 'tpu_shoe']
        self.assertEqual(len(shoes), 4)
        self.assertEqual({row['material'] for row in shoes}, {'TPU'})
        self.assertTrue(all(isinstance(row['stl_sha256'], str) for row in shoes))
        self.assertGreater(len(inventory['parts']), 0)
        self.assertEqual(len(inventory['stl_sha256_by_part']),
                         sum(row['stl_sha256'] is not None for row in inventory['parts']))
        servo_source = {link: list(items) for link, items in source.items()}
        servo_source['base_link'].append((mesh.copy(), '#444', 'component_test'))
        with A.context(generated=True), patch.object(
                SC, '_print_first_stl_index', return_value=(source_index, {})), \
                patch.object(SC, '_load_verified_stl_mesh', return_value=mesh.copy()):
            servo_inventory = _print_first_inventory_contract(
                servo_source, context_generated=True,
                require_stl_sha=True)
        self.assertEqual(servo_inventory['status'], 'PASS')
        self.assertTrue(servo_inventory['checks']['generated_geometry_allowlist_complete'])
        self.assertTrue(any(row['source_kind'] == 'generated_geometry'
                            for row in servo_inventory['parts']))

        duplicate = {link: list(items) for link, items in servo_source.items()}
        duplicate['base_link'].append(duplicate['base_link'][-1])
        with A.context(generated=True), patch.object(
                SC, '_print_first_stl_index', return_value=(source_index, {})), \
             patch.object(SC, '_load_verified_stl_mesh', return_value=mesh.copy()):
            duplicate_inventory = _print_first_inventory_contract(
                duplicate, context_generated=True, require_stl_sha=True)
        self.assertFalse(duplicate_inventory['checks']['part_names_unique_per_link'])
        self.assertEqual(duplicate_inventory['duplicate_part_keys'],
                         [['base_link', 'component_test']])
        self.assertEqual(duplicate_inventory['status'], 'FAIL')

    def test_inventory_require_stl_sha_cannot_disable_provenance(self):
        """旧引数をfalseにしてSTL/生成元来歴を省略する経路を拒否する。"""
        with self.assertRaisesRegex(ValueError, 'require_stl_sha=False'):
            _print_first_inventory_contract({}, require_stl_sha=False)

    def test_self_collision_inventory_rejects_legacy_and_nonexact_shoe_names(self):
        """旧foot_padやtpu_shoeのsuffix複製を自己干渉台帳へ混ぜない。"""
        import print_first_assembly as A
        import sim_collision as C
        mesh = trimesh.creation.box([1., 1., 1.])
        required = list(SC.PRINT_FIRST_REQUIRED_PARTS)
        source = {
            'base_link': [(mesh.copy(), '#fff', name) for name in required],
            **{f'leg_{leg.lower()}_tibia': [(mesh.copy(), '#333', 'tpu_shoe')]
               for leg in ('FR', 'FL', 'RL', 'RR')},
        }
        stl_path = ROOT / 'outputs/print-first-20260905/feet/tpu_shoe_foot_frame.stl'
        stl_sha = hashlib.sha256(stl_path.read_bytes()).hexdigest()
        source_index = {
            name: {'source_kind': 'stl', 'stl_path': str(stl_path.relative_to(ROOT)),
                   'stl_sha256': stl_sha, 'manifest_path': 'test/manifest.json'}
            for name in required + ['tpu_shoe']}
        with A.context(generated=True), patch.object(
                SC, '_print_first_stl_index', return_value=(source_index, {})), \
                patch.object(SC, '_load_verified_stl_mesh', return_value=mesh.copy()):
            valid = _print_first_inventory_contract(
                source, context_generated=True, require_stl_sha=True)
            self.assertEqual(valid['status'], 'PASS')

            legacy = {link: list(items) for link, items in source.items()}
            mesh, color, _ = legacy['base_link'][0]
            legacy['base_link'].append((mesh.copy(), color, 'foot_pad#shoe_FR'))
            bad_legacy = _print_first_inventory_contract(
                legacy, context_generated=True, require_stl_sha=True)
            self.assertFalse(bad_legacy['checks']['legacy_replaced_parts_absent'])
            self.assertEqual(bad_legacy['status'], 'FAIL')

            suffix = {link: list(items) for link, items in source.items()}
            mesh, color, _ = suffix['leg_fr_tibia'][0]
            suffix['leg_fr_tibia'].append((mesh.copy(), color, 'tpu_shoe#shoe_FR_0'))
            bad_suffix = _print_first_inventory_contract(
                suffix, context_generated=True, require_stl_sha=True)
            self.assertFalse(bad_suffix['checks']['tpu_shoe_name_exact'])
            self.assertEqual(bad_suffix['status'], 'FAIL')

            unknown = {link: list(items) for link, items in source.items()}
            mesh, color, _ = unknown['base_link'][0]
            unknown['base_link'].append((mesh.copy(), color, 'mystery_occupancy'))
            bad_unknown = _print_first_inventory_contract(
                unknown, context_generated=True, require_stl_sha=True)
            self.assertFalse(bad_unknown['checks']['generated_geometry_allowlist_complete'])
            self.assertEqual(bad_unknown['unknown_generated_geometry'],
                             ['base_link/mystery_occupancy'])
            self.assertEqual(bad_unknown['status'], 'FAIL')

            inactive_context = _print_first_inventory_contract(
                source, context_generated=False, require_stl_sha=True)
            self.assertFalse(inactive_context['checks']['generated_context_entered'])
            self.assertEqual(inactive_context['status'], 'FAIL')

            # The same shared forbidden set also covers retained toe/head
            # names and fragments that are not represented by the old foot_pad
            # negative alone.
            for legacy_name in (
                    'Leg_Toe_Black_x12', 'retained_Leg_Toe_Black_x12#FR',
                    'Head_Top_Blue', 'old_Head_Top_Blue_shell', 'pf_head_top'):
                bad = {link: list(items) for link, items in source.items()}
                mesh, color, _ = bad['base_link'][0]
                bad['base_link'].append((mesh.copy(), color, legacy_name))
                report = _print_first_inventory_contract(
                    bad, context_generated=True, require_stl_sha=True)
                self.assertFalse(report['checks']['legacy_replaced_parts_absent'],
                                 legacy_name)
                self.assertEqual(report['status'], 'FAIL', legacy_name)

    def test_self_collision_inventory_rejects_ambiguous_stl_sources_and_hashes_external_keys(self):
        """同名異SHAの保持STLを推測で選ばず、外部キーにも短縮SHAを残す。"""
        with tempfile.TemporaryDirectory(prefix='tachikoma-stl-ambiguity-') as temp:
            folder = Path(temp)
            hardware = folder / 'hardware'
            model = folder / 'model'
            hardware.mkdir()
            model.mkdir()
            (hardware / 'retained.stl').write_bytes(b'hardware-version')
            (model / 'retained.stl').write_bytes(b'model-version')
            with self.assertRaisesRegex(ValueError, 'ambiguous STL source'):
                _index_legacy_stls({}, (hardware, model))
            external = folder / 'outside.stl'
            external.write_bytes(b'external-content')
            digest = hashlib.sha256(external.read_bytes()).hexdigest()
            self.assertEqual(_public_root_path(external),
                             f'$EXTERNAL/{external.name}#{digest[:16]}')

    def test_repo_inputs_reject_internal_symlink_aliases(self):
        """STL/trace/URDF参照を解決して別実体へすり替えるsymlinkを拒否する。"""
        with tempfile.TemporaryDirectory(dir=ROOT,
                                         prefix='tachikoma-symlink-contract-') as temp:
            folder = Path(temp)
            target = folder / 'real.stl'
            target.write_bytes(b'not-a-mesh-but-path-provenance-is-tested')
            link = folder / 'alias.stl'
            link.symlink_to(target)
            public = link.relative_to(ROOT).as_posix()

            with self.assertRaisesRegex(ValueError, 'repository symlink'):
                SC._resolve_repo_stl_path(public)
            with self.assertRaisesRegex(ValueError, 'repository symlink'):
                SC._trace_input_descriptor(link)
            with self.assertRaisesRegex(ValueError, 'repository symlink'):
                SC._index_legacy_stls({}, (folder,))

            errors = []
            resolved = PX._resolve_urdf_mesh_path(
                folder / 'model.urdf', 'alias.stl', 'symlink', errors)
            self.assertIsNone(resolved)
            self.assertTrue(any('repository symlink' in error for error in errors))

    def test_self_collision_stl_index_normalizes_feet_outputs_schema_strictly(self):
        """feet の outputs map と body/legs の parts listを混同せず読む。"""
        shoe = ROOT / 'outputs/print-first-20260905/feet/tpu_shoe_foot_frame.stl'
        shoe_sha = hashlib.sha256(shoe.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory(prefix='tachikoma-feet-schema-') as temp:
            root = Path(temp)
            manifest = root / 'feet-assembly.json'
            manifest.write_text(json.dumps({
                'outputs': {
                    'tpu_shoe': {'path': str(shoe), 'sha256': shoe_sha},
                },
                # The real feet manifest also carries this duplicate canonical
                # entry in its legacy parts list; identical bytes are allowed.
                'parts': [{'name': 'tpu_shoe', 'stl': str(shoe), 'sha256': shoe_sha}],
            }), encoding='utf-8')
            with patch.object(SC, 'PRINT_FIRST_MANIFEST_PATHS', (manifest,)):
                index, inputs = SC._print_first_stl_index()
            self.assertEqual(index['tpu_shoe']['stl_sha256'], shoe_sha)
            self.assertEqual(index['tpu_shoe']['stl_path'],
                             'outputs/print-first-20260905/feet/tpu_shoe_foot_frame.stl')
            self.assertIn(index['tpu_shoe']['manifest_path'], inputs)

            manifest.write_text(json.dumps({
                'outputs': {'tpu_shoe': {'sha256': shoe_sha}},
            }), encoding='utf-8')
            with patch.object(SC, 'PRINT_FIRST_MANIFEST_PATHS', (manifest,)):
                with self.assertRaisesRegex(ValueError, 'STL path is invalid'):
                    SC._print_first_stl_index()

            manifest.write_text(json.dumps({
                'parts': [{'name': 'tpu_shoe', 'sha256': shoe_sha}],
            }), encoding='utf-8')
            with patch.object(SC, 'PRINT_FIRST_MANIFEST_PATHS', (manifest,)):
                with self.assertRaisesRegex(ValueError, 'STL path is invalid'):
                    SC._print_first_stl_index()

            other = root / 'other.stl'
            other.write_bytes(shoe.read_bytes() + b'\nconflicting-source')
            manifest.write_text(json.dumps({
                'outputs': {'tpu_shoe': {'path': str(shoe), 'sha256': shoe_sha}},
                'parts': [{'name': 'tpu_shoe', 'stl': str(other),
                           'sha256': hashlib.sha256(other.read_bytes()).hexdigest()}],
            }), encoding='utf-8')
            with patch.object(SC, 'PRINT_FIRST_MANIFEST_PATHS', (manifest,)):
                with self.assertRaisesRegex(ValueError, 'escapes the repository'):
                    SC._print_first_stl_index()

    def test_self_collision_sweep_hashes_and_serializes_only_at_boundaries(self):
        """全掃引で入力全体の再ハッシュ/JSON再書込みを姿勢ごとに行わない。"""
        import inspect
        import re
        import sim_self_collision as C

        source = inspect.getsource(C._audit_generated_context)
        self.assertEqual(len(re.findall(r'(?<!inventory)_input_hashes\(', source)), 2)
        self.assertEqual(source.count('out.write_text(json.dumps(payload'), 1)
        self.assertGreaterEqual(source.count("$PROXY_EXCLUSION_CONTRACT"), 2)

    def test_convex_wrappers_do_not_copy_stale_function_attributes(self):
        """衝突台帳は呼出し先の直近属性を読み、旧関数属性で上書きしない。"""
        import inspect
        import diagnose_print_first_initial as D
        import sim_print_first as P
        import sim_self_collision as C

        for function in (
                D.diagnose, P.execute, C.audit):
            source = inspect.getsource(function)
            self.assertNotIn('getattr(old_convex', source)
            self.assertNotIn('old_convex.last_manifest', source)

    def test_print_first_strength_scope_covers_config_wall_mirror_and_three_links(self):
        """印刷優先強度は設定した壁/充填と標準・鏡像3リンクを全て読む。"""
        rule = _print_first_rule()
        self.assertEqual(rule['material'], 'PLA')
        self.assertAlmostEqual(rule['wall_mm'], 2.4)
        self.assertAlmostEqual(rule['infill_fraction'], .50)
        folder = ROOT / 'outputs/print-first-20260905/legs'
        paths = _print_first_paths(folder)
        self.assertEqual(len(paths), 6)
        self.assertEqual({kind for kind, _ in paths}, {'coxa', 'femur', 'tibia'})
        self.assertEqual({suffix for _, suffix in paths}, {'', '_m'})
        details = print_first_scan(folder,
                                   sigma_allow=rule['allowable_bending_mpa'],
                                   sf_req=rule['required_safety_factor'],
                                   load_kgf=rule['load_kgf'], emit=False,
                                   return_details=True)
        self.assertEqual(len(details), 6)
        self.assertTrue(all(row['volume_mm3'] > 0 for row in details))
        self.assertTrue(all(np.isfinite(row['safety_factor']) for row in details))
        self.assertTrue(min(row['safety_factor'] for row in details) >= rule['required_safety_factor'])

    def test_collision_mesh_validation_rejects_nonfinite_and_open_inputs(self):
        import trimesh
        import sim_collision

        valid, metadata = sim_collision._validated_outward_mesh(
            trimesh.creation.box([2., 3., 4.]), 'valid')
        self.assertTrue(metadata['finite'])
        self.assertTrue(metadata['watertight'])
        self.assertTrue(metadata['winding_consistent'])
        self.assertTrue(metadata['is_volume'])
        self.assertGreater(metadata['volume_mm3'], 0.)
        self.assertGreater(valid.volume, 0.)

        reversed_source = trimesh.creation.box([2., 3., 4.])
        reversed_source.invert()
        with self.assertRaisesRegex(ValueError, 'volume must be finite and > 0'):
            sim_collision._validated_outward_mesh(
                reversed_source, 'reversed-source', source=True)
        # Generated convex pieces may still be normalized explicitly; the
        # source-only gate above is the boundary that must remain fail-closed.
        normalized, normalized_metadata = sim_collision._validated_outward_mesh(
            reversed_source, 'generated-piece')
        self.assertGreater(normalized_metadata['volume_mm3'], 0.)
        self.assertTrue(normalized_metadata['is_volume'])

        open_mesh = trimesh.creation.box([2., 3., 4.])
        open_mesh.update_faces(np.arange(len(open_mesh.faces) - 1))
        with self.assertRaisesRegex(ValueError, 'watertight'):
            sim_collision._validated_outward_mesh(open_mesh, 'open')
        nonfinite = trimesh.creation.box([2., 3., 4.])
        nonfinite.vertices[0, 0] = np.nan
        with self.assertRaisesRegex(ValueError, 'non-finite'):
            sim_collision._validated_outward_mesh(nonfinite, 'nonfinite')

        with tempfile.TemporaryDirectory(prefix='tachikoma-hull-cache-contract-') as temp:
            path = Path(temp) / 'valid.npz'
            cache_source_sha = 'a' * 64
            cache_definition_sha = 'b' * 64
            cache_hull_sha = sim_collision._hull_digest(valid)
            cache_source_convex_sha = sim_collision._hull_digest(valid.convex_hull)
            np.savez_compressed(path, count=np.array(1),
                                cache_format=np.array(sim_collision.CACHE_FORMAT),
                                source_mesh_sha256=np.array(cache_source_sha),
                                source_convex_hull_sha256=np.array(cache_source_convex_sha),
                                cache_definition_sha256=np.array(cache_definition_sha),
                                source_bounds_mm=np.asarray(valid.bounds),
                                hull_sha256=np.array([cache_hull_sha]),
                                v0=valid.vertices, f0=valid.faces)
            hulls, validations = sim_collision._load_validated_hulls(path, 'cache')
            self.assertEqual(len(hulls), 1)
            self.assertTrue(validations[0]['is_volume'])
            broken = Path(temp) / 'open.npz'
            np.savez_compressed(broken, count=np.array(1),
                                cache_format=np.array(sim_collision.CACHE_FORMAT),
                                source_mesh_sha256=np.array(cache_source_sha),
                                source_convex_hull_sha256=np.array(cache_source_convex_sha),
                                cache_definition_sha256=np.array(cache_definition_sha),
                                source_bounds_mm=np.asarray(open_mesh.bounds),
                                hull_sha256=np.array([sim_collision._hull_digest(open_mesh)]),
                                v0=open_mesh.vertices, f0=open_mesh.faces)
            with self.assertRaisesRegex(ValueError, 'watertight'):
                sim_collision._load_validated_hulls(broken, 'cache')
            reversed_cache = Path(temp) / 'reversed.npz'
            reversed_vertices = valid.vertices.copy()
            reversed_faces = valid.faces.copy()
            reversed_faces[[0, 1]] = reversed_faces[[1, 0]]
            # Reverse every face to make the cache's signed volume negative.
            reversed_faces = reversed_faces[:, [0, 2, 1]]
            np.savez_compressed(reversed_cache, count=np.array(1),
                                cache_format=np.array(sim_collision.CACHE_FORMAT),
                                source_mesh_sha256=np.array(cache_source_sha),
                                source_convex_hull_sha256=np.array(cache_source_convex_sha),
                                cache_definition_sha256=np.array(cache_definition_sha),
                                source_bounds_mm=np.asarray(valid.bounds),
                                hull_sha256=np.array([sim_collision._mesh_digest(
                                    trimesh.Trimesh(vertices=reversed_vertices,
                                                    faces=reversed_faces,
                                                    process=False))]),
                                v0=reversed_vertices, f0=reversed_faces)
            with self.assertRaisesRegex(ValueError, 'volume must be finite and > 0'):
                sim_collision._load_validated_hulls(reversed_cache, 'cache')

            tampered_hull = Path(temp) / 'tampered-hull.npz'
            tampered_vertices = valid.vertices.copy()
            tampered_vertices[:, 0] *= 1.01
            # Keep the original hull SHA to prove that a valid solid with
            # altered geometry cannot be accepted by metadata alone.
            np.savez_compressed(tampered_hull, count=np.array(1),
                                cache_format=np.array(sim_collision.CACHE_FORMAT),
                                source_mesh_sha256=np.array(cache_source_sha),
                                source_convex_hull_sha256=np.array(cache_source_convex_sha),
                                cache_definition_sha256=np.array(cache_definition_sha),
                                source_bounds_mm=np.asarray(valid.bounds),
                                hull_sha256=np.array([cache_hull_sha]),
                                v0=tampered_vertices, f0=valid.faces)
            with self.assertRaisesRegex(ValueError, 'hull SHA metadata'):
                sim_collision._load_validated_hulls(tampered_hull, 'cache')

            tampered_metadata = Path(temp) / 'tampered-metadata.npz'
            np.savez_compressed(tampered_metadata, count=np.array(1),
                                cache_format=np.array(sim_collision.CACHE_FORMAT),
                                source_mesh_sha256=np.array('c' * 64),
                                source_convex_hull_sha256=np.array(cache_source_convex_sha),
                                cache_definition_sha256=np.array(cache_definition_sha),
                                source_bounds_mm=np.asarray(valid.bounds),
                                hull_sha256=np.array([cache_hull_sha]),
                                v0=valid.vertices, f0=valid.faces)
            with self.assertRaisesRegex(ValueError, 'source mesh SHA differs'):
                sim_collision._load_validated_hulls(
                    tampered_metadata, 'cache',
                    expected_source_mesh_sha256=cache_source_sha)

    def test_collision_cache_rejects_self_consistent_fake_parts_hull(self):
        """source bounds/SHAを写し替えた偽parts hullを独立再構築で拒否する。"""
        import sim_collision
        valid = trimesh.creation.box([2., 3., 4.])
        fake = trimesh.creation.box([1., 1., 1.])
        definition = sim_collision.collision_cache_definition('parts')
        definition_sha = sim_collision.collision_cache_definition_sha('parts')
        input_sha = sim_collision.collision_cache_input_digest(valid, definition)
        source_sha = sim_collision._mesh_digest(valid)
        source_convex_sha = sim_collision._hull_digest(valid.convex_hull)
        temp_root = Path(tempfile.mkdtemp(prefix='tachikoma-fake-cache-'))
        cache = temp_root / f'{input_sha}.npz'
        try:
            np.savez_compressed(
                cache, count=np.array(1), cache_format=np.array(sim_collision.CACHE_FORMAT),
                source_mesh_sha256=np.array(source_sha),
                source_convex_hull_sha256=np.array(source_convex_sha),
                cache_definition_sha256=np.array(definition_sha),
                source_bounds_mm=np.asarray(valid.bounds),
                hull_sha256=np.array([sim_collision._hull_digest(fake)]),
                v0=fake.vertices, f0=fake.faces)
            manifest = temp_root / 'manifest.json'
            row = {
                'link': 'base_link', 'part': 'sample',
                'input_mesh_sha256': input_sha, 'source_mesh_sha256': source_sha,
                'source_convex_hull_sha256': source_convex_sha,
                'cache_definition_sha256': definition_sha,
                'cache_sha256': sim_collision._file_sha256(cache),
                'hull_sha256': [sim_collision._hull_digest(fake)],
                'source_bounds_mm': valid.bounds.tolist(),
            }
            manifest.write_text(json.dumps([row]), encoding='utf-8')
            ledger = {
                'version': 1,
                'manifest': {'path': f'$COLLISION_CACHE/{manifest.name}',
                             'sha256': sim_collision._file_sha256(manifest),
                             '_manifest_path': manifest},
                'entries': [{**row, 'path': f'$COLLISION_CACHE/{cache.name}',
                            '_cache_path': cache, 'sha256': row['cache_sha256'],
                            'cache_definition': definition}],
                'entry_count': 1, 'definition': definition,
            }
            with self.assertRaisesRegex(ValueError, 'parts cache hull differs'):
                sim_collision.collision_cache_input_fingerprints(
                    ledger, source_parts={'base_link': [(valid, '#fff', 'sample')]})
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    def test_physics_input_boundaries_reject_nonfinite_and_zero_speed(self):
        valid = S.validate_model_parameters(
            1., {'leg': 24., 'arm': .8, 'eye': .05},
            {'leg': .4, 'arm': .03, 'eye': .005})
        self.assertEqual(valid['contact_model'], 'linked-hulls')
        with self.assertRaisesRegex(ValueError, 'finite'):
            S.validate_model_parameters(
                float('nan'), {'leg': 24., 'arm': .8, 'eye': .05},
                {'leg': .4, 'arm': .03, 'eye': .005})
        with self.assertRaisesRegex(ValueError, '> 0'):
            S.speed_torque_ranges(np.tile([-1., 1.], (1, 1)),
                                  np.array([0.]), np.array([0.]))
        with self.assertRaisesRegex(ValueError, 'finite'):
            S.speed_torque_ranges(np.tile([-1., 1.], (1, 1)),
                                  np.array([np.inf]), np.array([1.]))

    def test_thin_vhacd_replacement_preserves_projected_footprint(self):
        """大きな平面片を軸平行箱へ膨らませず、投影凸包だけを押し出す。"""
        thin = trimesh.creation.box((18.8, 1.82, .001))
        replacement, metadata = sim_collision._thin_piece_replacement(
            thin, 'thin-contract', 'test')
        self.assertTrue(replacement.is_volume)
        self.assertTrue(replacement.is_watertight)
        self.assertAlmostEqual(replacement.volume,
                               metadata['footprint_area_mm2'] * .02,
                               places=8)
        self.assertEqual(metadata['source'],
                         'generated_vhacd_piece_convex_footprint_prism')
        self.assertLess(metadata['relative_normal_thickness'], .1)
        self.assertGreater(metadata['footprint_area_ratio_to_svd_box'], .99)
        self.assertTrue(metadata['source_normal_spread_within_limit'])
        self.assertTrue(metadata['footprint_contains_source_points'])
        self.assertTrue(metadata['replacement_volume_matches_footprint_prism'])
        self.assertAlmostEqual(
            metadata['replacement_volume_mm3'],
            metadata['expected_prism_volume_mm3'], places=8)
        self.assertAlmostEqual(metadata['extruded_thickness_mm'], .02)
        self.assertEqual(
            sim_collision.audit_thin_piece_replacement(metadata, replacement)['status'],
            'PASS')
        forged = dict(metadata)
        forged['source_projected_points_2d'] = [
            [float(x) * 1.5, float(y) * 1.5]
            for x, y in metadata['source_projected_points_2d']]
        with self.assertRaisesRegex(ValueError, 'stored footprint'):
            sim_collision.audit_thin_piece_replacement(forged, replacement)
        forged_source = dict(metadata)
        forged_source['source_vertices_mm'] = [
            list(row) for row in metadata['source_vertices_mm']]
        forged_source['source_vertices_mm'][0][0] += 0.25
        with self.assertRaisesRegex(ValueError, 'source vertex evidence'):
            sim_collision.audit_thin_piece_replacement(forged_source, replacement)
        missing_cell_hash = dict(metadata)
        missing_cell_hash.pop('source_cell_mesh_sha256')
        with self.assertRaisesRegex(ValueError, 'source cell topology/hash'):
            sim_collision.audit_thin_piece_replacement(missing_cell_hash, replacement)
        forged_cell = dict(metadata)
        forged_cell['source_cell_faces'] = [list(face)
                                            for face in metadata['source_cell_faces']]
        forged_cell['source_cell_faces'][0][0] = (
            forged_cell['source_cell_faces'][0][0] + 1) % len(metadata['source_vertices_mm'])
        with self.assertRaisesRegex(ValueError, 'source cell mesh SHA'):
            sim_collision.audit_thin_piece_replacement(forged_cell, replacement)

        # A non-rectangular footprint exercises the explicit triangle
        # tessellation used by the cache replacement.  A list of polygon
        # faces would become a ragged ndarray here and could not be reloaded
        # as a valid convex volume.
        from shapely.geometry import Polygon
        pentagon = trimesh.creation.extrude_polygon(
            Polygon([(0., 0.), (5., 0.), (6., 2.), (3., 4.), (0., 2.)]),
            .001)
        prism, pentagon_meta = sim_collision._thin_piece_replacement(
            pentagon, 'thin-pentagon-contract', 'test')
        self.assertEqual(pentagon_meta['footprint_vertex_count'], 5)
        self.assertEqual(prism.faces.shape, (16, 3))
        self.assertTrue(prism.is_watertight)
        self.assertTrue(prism.is_volume)
        self.assertAlmostEqual(
            prism.volume,
            pentagon_meta['footprint_area_mm2'] * pentagon_meta['extruded_thickness_mm'],
            places=8)
        self.assertGreater(pentagon_meta['footprint_area_ratio_to_svd_box'], .5)
        self.assertTrue(pentagon_meta['footprint_contains_source_points'])
        self.assertTrue(pentagon_meta['replacement_volume_matches_footprint_prism'])
        self.assertEqual(
            sim_collision.audit_thin_piece_replacement(pentagon_meta, prism)['status'],
            'PASS')

    def test_vhacd_source_coverage_rejects_tiny_inner_fake_hull(self):
        """source convex hull内に収まるだけの極小偽hullは通さない。"""
        source = trimesh.creation.box([20., 10., 4.])
        tiny = trimesh.creation.box([0.1, 0.1, 0.1])
        tiny.apply_translation([9.9, 4.9, 1.9])
        equations = sim_collision._convex_hull_equations(
            source.vertices, 'coverage-test')
        self.assertEqual(equations.shape[1], 4)
        report = sim_collision._source_coverage_report(
            source, [tiny], 'coverage-test')
        self.assertEqual(report['status'], 'FAIL')
        self.assertLess(report['coverage_fraction'], 1.0)

    def test_stress_case_input_gate_covers_model_gains_pushes_and_parent_filter(self):
        base = {
            'name': 'input-gate',
            'model': {},
            'gains': {'kp': {'leg': 24., 'arm': .8, 'eye': .05},
                      'kv': {'leg': .4, 'arm': .03, 'eye': .005}},
            'segments': [{'name': 'hold', 'duration': .5, 'body_h': 115.}],
            'pushes': [{'start': 0., 'duration': .1, 'force_N': [0., 0., 0.]}],
        }
        normalized = T._validate_case_inputs(base)
        self.assertEqual(normalized['segments'][0]['duration'], .5)
        self.assertEqual(normalized['pushes'][0]['force_N'], [0., 0., 0.])
        self.assertEqual(normalized['model']['torque_model'], 'linear-speed')
        for mutate, pattern in (
                (lambda c: c['model'].__setitem__('mass_scale', float('nan')), 'finite'),
                (lambda c: c['gains']['kp'].__setitem__('leg', float('inf')), 'finite'),
                (lambda c: c['pushes'][0]['force_N'].__setitem__(0, float('nan')), 'finite')):
            candidate = {
                'name': base['name'], 'model': dict(base['model']),
                'gains': {'kp': dict(base['gains']['kp']),
                          'kv': dict(base['gains']['kv'])},
                'segments': [dict(base['segments'][0])],
                'pushes': [{'start': 0., 'duration': .1, 'force_N': [0., 0., 0.]}],
            }
            mutate(candidate)
            with self.assertRaisesRegex(ValueError, pattern):
                T._validate_case_inputs(candidate)

        final = {
            'name': 'final-input-gate',
            'model': {'model_kind': 'final_integrated', 'assembly_context': True,
                      'self_collision': True, 'include_servo_collision': True,
                      'include_parent_collision': False, 'contact_model': 'vhacd',
                      'group_voltage_V': {'leg': 6., 'arm': 5., 'eye': 5.}},
            'segments': [{'name': 'hold', 'duration': .5}],
        }
        with self.assertRaisesRegex(ValueError, 'include_parent_collision'):
            T._validate_case_inputs(final)
        invalid_torque = dict(final)
        invalid_torque['model'] = dict(final['model'], torque_model='unknown')
        with self.assertRaisesRegex(ValueError, 'torque_model'):
            T._validate_case_inputs(invalid_torque)
        stall_final = dict(final)
        stall_final['model'] = dict(final['model'], include_parent_collision=True,
                                    torque_model='stall')
        with self.assertRaisesRegex(ValueError, 'linear-speed'):
            T._validate_case_inputs(stall_final)

    def test_stress_case_ground_initialization_is_normalized_to_model(self):
        base = {
            'name': 'ground-normalization', 'model': {},
            'gains': {'kp': {'leg': 24., 'arm': .8, 'eye': .05},
                      'kv': {'leg': .4, 'arm': .03, 'eye': .005}},
            'segments': [{'name': 'hold', 'duration': .5}],
        }
        legacy = dict(base, ground_initialization=False)
        normalized = T._validate_case_inputs(legacy)
        self.assertFalse(normalized['model']['ground_initialization'])
        conflict = dict(base, ground_initialization=False,
                         model={'ground_initialization': True})
        with self.assertRaisesRegex(ValueError, 'must match'):
            T._validate_case_inputs(conflict)
        invalid = dict(base, ground_initialization='false')
        with self.assertRaisesRegex(ValueError, 'must be boolean'):
            T._validate_case_inputs(invalid)

    def test_stress_enablement_policy_requires_explicit_startup_exception(self):
        base = {
            'name': 'enablement-policy', 'model': {},
            'segments': [{'name': 'startup', 'duration': .5},
                         {'name': 'walk', 'duration': .5, 'vy': 1.}],
        }
        normalized = T._validate_case_inputs(base)
        self.assertEqual(normalized['startup_enablement_policy'], 'all_enabled')
        self.assertFalse(normalized['startup_sequential'])

        sequential = dict(base, startup_enablement_policy='sequential_startup')
        normalized = T._validate_case_inputs(sequential)
        self.assertEqual(normalized['startup_enablement_policy'], 'sequential_startup')
        self.assertTrue(normalized['startup_sequential'])
        conflict = dict(base, startup_sequential=True,
                        startup_enablement_policy='all_enabled')
        with self.assertRaisesRegex(ValueError, 'must match'):
            T._validate_case_inputs(conflict)

        explicit = dict(base, startup_enablement_policy='explicit_exempt_segments',
                        enablement_exempt_segments=['startup'])
        normalized = T._validate_case_inputs(explicit)
        self.assertEqual(normalized['enablement_exempt_segments'], ['startup'])
        with self.assertRaisesRegex(ValueError, 'requires enablement_exempt_segments'):
            T._validate_case_inputs(dict(base,
                startup_enablement_policy='explicit_exempt_segments'))
        with self.assertRaisesRegex(ValueError, 'requires explicit_exempt_segments'):
            T._validate_case_inputs(dict(base, enablement_exempt_segments=['startup']))

    def test_all_twenty_enablement_denominator_catches_short_or_segment_disable(self):
        axes = {
            name: {
                'mean_absolute_torque_nm': .1, 'rms_torque_nm': .2,
                'max_absolute_torque_nm': .3, 'max_velocity_rad_s': 1.,
                'saturation_fraction': 0., 'bound_saturation_fraction': 0.,
                'stall_limit_nm': 1., 'no_load_velocity_rad_s': 2.,
                'enabled_step_count': 100,
                'required_enabled_step_count': 100,
            }
            for name in S.ALL_JOINTS
        }
        segment = {
            'axis_saturation_fraction': [0.] * len(S.ALL_JOINTS),
            'axis_bound_saturation_fraction': [0.] * len(S.ALL_JOINTS),
        }
        kwargs = {
            'enabled_axes': np.ones(20, dtype=bool),
            'enabled_counts': np.ones(20) * 100,
            'required_enabled_counts': np.ones(20) * 100,
            'required_step_count': 100,
            'segment_enabled_counts': np.ones((1, 20)) * 100,
            'segment_required_enabled_counts': np.ones((1, 20)) * 100,
            'segment_required_steps': np.array([100]),
            'ready_gate': True,
        }
        accepted = T.all_axis_constraint_checks(axes, [segment], **kwargs)
        self.assertTrue(accepted['axis_enabled_count_all_20'])
        self.assertTrue(accepted['axis_enabled_after_ready_all_20'])
        self.assertTrue(accepted['axis_enabled_per_required_segment_all_20'])

        one_frame_missing = dict(kwargs,
            required_enabled_counts=np.r_[np.ones(1) * 99, np.ones(19) * 100],
            segment_required_enabled_counts=np.vstack(
                [np.r_[np.ones(1) * 99, np.ones(19) * 100]]))
        rejected = T.all_axis_constraint_checks(axes, [segment], **one_frame_missing)
        self.assertFalse(rejected['axis_enabled_after_ready_all_20'])
        self.assertFalse(rejected['axis_enabled_per_required_segment_all_20'])

        all_disabled = dict(kwargs, enabled_counts=np.zeros(20),
                            required_enabled_counts=np.zeros(20),
                            segment_enabled_counts=np.zeros((1, 20)),
                            segment_required_enabled_counts=np.zeros((1, 20)))
        rejected = T.all_axis_constraint_checks(axes, [segment], **all_disabled)
        self.assertFalse(rejected['axis_enabled_count_all_20'])
        self.assertFalse(rejected['axis_enabled_after_ready_all_20'])

    def test_all_twenty_segment_load_and_speed_metrics_are_gated(self):
        """最終区間の腕/目の荷重・速度も脚と同じ上限判定へ入れる。"""
        axes = {
            name: {
                'mean_absolute_torque_nm': .1, 'rms_torque_nm': .2,
                'max_absolute_torque_nm': .3, 'max_velocity_rad_s': 1.,
                'saturation_fraction': 0., 'bound_saturation_fraction': 0.,
                'stall_limit_nm': 1., 'no_load_velocity_rad_s': 2.,
                'enabled_step_count': 100, 'required_enabled_step_count': 100,
            }
            for name in S.ALL_JOINTS
        }
        segment = {
            'axis_saturation_fraction': [0.] * 20,
            'axis_bound_saturation_fraction': [0.] * 20,
            'axis_mean_absolute_torque_nm': [.1] * 20,
            'axis_rms_torque_nm': [.2] * 20,
            'axis_max_absolute_torque_nm': [.3] * 20,
            'axis_max_velocity_rad_s': [1.] * 20,
        }
        kwargs = {
            'enabled_axes': np.ones(20, dtype=bool),
            'enabled_counts': np.ones(20) * 100,
            'required_enabled_counts': np.ones(20) * 100,
            'required_step_count': 100,
            'segment_enabled_counts': np.ones((1, 20)) * 100,
            'segment_required_enabled_counts': np.ones((1, 20)) * 100,
            'segment_required_steps': np.array([100]),
            'ready_gate': True,
        }
        accepted = T.all_axis_constraint_checks(axes, [segment], **kwargs)
        self.assertTrue(accepted['axis_torque_within_limits_per_segment_all_20'])
        self.assertTrue(accepted['axis_rms_torque_within_limits_per_segment_all_20'])
        self.assertTrue(accepted['axis_velocity_within_limits_per_segment_all_20'])

        arm_load = dict(segment,
                        axis_max_absolute_torque_nm=[1.001 if name == 'arm_r_yaw' else .3
                                                     for name in S.ALL_JOINTS])
        rejected = T.all_axis_constraint_checks(axes, [arm_load], **kwargs)
        self.assertFalse(rejected['axis_torque_within_limits_per_segment_all_20'])
        eye_speed = dict(segment,
                         axis_max_velocity_rad_s=[2.001 if name == 'eye_l_roll' else 1.
                                                  for name in S.ALL_JOINTS])
        rejected = T.all_axis_constraint_checks(axes, [eye_speed], **kwargs)
        self.assertFalse(rejected['axis_velocity_within_limits_per_segment_all_20'])
        missing = dict(segment)
        del missing['axis_rms_torque_nm']
        rejected = T.all_axis_constraint_checks(axes, [missing], **kwargs)
        self.assertFalse(rejected['axis_segment_metrics_finite_all_20'])

    def test_final_material_support_is_required_before_status_can_pass(self):
        tpu = [{'normal_impulse_by_material': {'TPU': 95., 'PLA': 5.}}]
        metrics = T._tpu_support_metrics(tpu, 'vhacd')
        self.assertTrue(metrics['total_impulse_finite_positive'])
        self.assertAlmostEqual(metrics['fraction'], .95)
        self.assertEqual(metrics['status'], 'PASS')
        pla_only = T._tpu_support_metrics(
            [{'normal_impulse_by_material': {'PLA': 100.}}], 'vhacd')
        self.assertEqual(pla_only['status'], 'FAIL')
        self.assertEqual(pla_only['fraction'], 0.)
        zero = T._tpu_support_metrics(
            [{'normal_impulse_by_material': {}}], 'vhacd')
        self.assertFalse(zero['total_impulse_finite_positive'])
        self.assertEqual(zero['status'], 'FAIL')
        legacy = T._tpu_support_metrics(
            [{'normal_impulse_by_material': {'PLA': 100.}}], 'linked-hulls')
        self.assertEqual(legacy['status'], 'UNVERIFIED')

    def test_full_rate_torque_report_exposes_rms_and_contiguous_bound_hold(self):
        """全刻み荷重集計に関節別RMSと上限張り付き時間を残す。"""
        case = {'model': {'timestep': .002},
                'segments': [{'name': 'stand', 'duration': .1}]}
        metrics = P.FullRateMetrics(case)
        zero = np.zeros(len(S.ALL_JOINTS))
        velocity = np.zeros(len(S.ALL_JOINTS))
        for index, value in enumerate((1., 3., 5.)):
            torque = zero.copy(); torque[0] = value
            metrics.records.append((.002 * (index + 1), 'stand', torque, velocity))
        bound = np.zeros(len(S.ALL_JOINTS), dtype=bool)
        metrics.bound_records = [(.002, np.r_[np.ones(1), zero[1:]],
                                  np.r_[True, bound[1:]]),
                                (.004, np.r_[np.ones(1), zero[1:]],
                                  np.r_[True, bound[1:]]),
                                (.006, np.r_[zero[:1], zero[1:]],
                                  bound.copy())]
        report = metrics.summary()['stand']
        self.assertAlmostEqual(report['leg_axis_mean_torque_nm'][0], 3.)
        self.assertAlmostEqual(report['leg_axis_rms_torque_nm'][0],
                               np.sqrt((1. + 9. + 25.) / 3.))
        self.assertAlmostEqual(report['leg_axis_bound_saturation_fraction'][0], 2/3)
        self.assertAlmostEqual(report['leg_axis_bound_saturation_max_contiguous_s'][0], .004)
        self.assertTrue(report['torque_acceptance']['continuous_hold_saturation_is_not_pass'])

    def test_stress_bound_saturation_gate_rejects_persistent_holding(self):
        """最終区間は上限張り付き時間とholding到達を別々にゲートする。"""
        valid = {
            'name': 'stand', 'command': [0., 0., 0.], 'steps': 100,
            'axis_bound_saturation_max_contiguous_by_axis_s': [.004] * 20,
            'holding_step_count': 100,
            'holding_at_end': True,
            'axis_holding_bound_saturation_fraction': [.01] * 20,
            'axis_holding_bound_saturation_max_contiguous_by_axis_s': [.004] * 20,
        }
        accepted = T.bound_saturation_acceptance([valid])
        self.assertTrue(accepted['axis_bound_saturation_contiguous_all_20_le_0p1s'])
        self.assertTrue(
            accepted['axis_holding_bound_saturation_contiguous_all_20_le_0p02s'])
        self.assertTrue(
            accepted['axis_holding_bound_saturation_fraction_all_20_le_5pct'])
        self.assertTrue(
            accepted['axis_holding_required_for_zero_command_segments_all_reached'])

        persistent = dict(valid)
        persistent['axis_holding_bound_saturation_max_contiguous_by_axis_s'] = [
            .021 if index == 0 else .004 for index in range(20)
        ]
        rejected = T.bound_saturation_acceptance([persistent])
        self.assertFalse(
            rejected['axis_holding_bound_saturation_contiguous_all_20_le_0p02s'])

        broad_persistent = dict(valid)
        broad_persistent['axis_bound_saturation_max_contiguous_by_axis_s'] = [
            .101 if index == 7 else .004 for index in range(20)
        ]
        rejected = T.bound_saturation_acceptance([broad_persistent])
        self.assertFalse(
            rejected['axis_bound_saturation_contiguous_all_20_le_0p1s'])

        no_holding = dict(valid, holding_step_count=0)
        no_holding['axis_holding_bound_saturation_fraction'] = [0.] * 20
        no_holding['axis_holding_bound_saturation_max_contiguous_by_axis_s'] = [0.] * 20
        rejected = T.bound_saturation_acceptance([no_holding])
        self.assertFalse(
            rejected['axis_holding_required_for_zero_command_segments_all_reached'])

        end_released = dict(valid, holding_at_end=False)
        rejected = T.bound_saturation_acceptance([end_released])
        self.assertFalse(
            rejected['axis_holding_required_for_zero_command_segments_all_reached'])

        malformed = dict(valid)
        del malformed['axis_holding_bound_saturation_max_contiguous_by_axis_s']
        rejected = T.bound_saturation_acceptance([malformed])
        self.assertFalse(
            rejected['axis_holding_bound_saturation_metrics_finite_all_20'])

    def test_voltage_copy_checks_all_twenty_limits_without_mass_change(self):
        source=ROOT/'hardware/urdf/tachikoma.urdf'
        self.assertTrue(source.is_file())
        case={'name':'voltage-contract','model':{},
              'group_voltage_V':{'leg':6.0,'arm':5.0,'eye':5.0}}
        with tempfile.TemporaryDirectory(prefix='tachikoma-voltage-contract-') as temp:
            out=Path(temp)
            destination,meta=apply_group_voltage_limits(case,source,out)
            self.assertEqual(meta['status'],'APPLIED')
            report=voltage_limit_report(case,destination)
            self.assertEqual(report['status'],'PASS')
            self.assertTrue(report['all_20_limits_applied'])
            self.assertFalse(report['physical_verified'])
            self.assertIn('runtime', report['source'])
            self.assertIn('model limit elements', report['source'])
            self.assertNotIn('compiled', report['source'])
            self.assertEqual(len(report['joints']),20)
            import xml.etree.ElementTree as ET
            original=ET.parse(source).getroot()
            copied=ET.parse(destination).getroot()
            for name in ('base_link','leg_fr_tibia','arm_r_forearm'):
                a=original.find(f"link[@name='{name}']/inertial")
                b=copied.find(f"link[@name='{name}']/inertial")
                self.assertIsNotNone(a);self.assertIsNotNone(b)
                self.assertEqual(a.find('mass').get('value'),b.find('mass').get('value'))
                self.assertEqual(a.find('origin').get('xyz'),b.find('origin').get('xyz'))

    def test_stress_group_voltage_model_records_applied_limits_and_torque_mode(self):
        metadata = T.voltage_model_metadata(
            {'group_voltage_V': {'leg': 6.0, 'arm': 5.0, 'eye': 5.0}},
            {'leg': 6.0, 'arm': 5.0, 'eye': 5.0},
            'linear-speed',
        )
        self.assertEqual(metadata['mode'], 'group_voltage_V')
        self.assertEqual(metadata['adopted_torque_model'], 'linear-speed')
        self.assertEqual(set(metadata['requested_group_voltage_V']), {'leg', 'arm', 'eye'})
        self.assertFalse(metadata['physical_verified'])
        self.assertIn('3群', metadata['description'])
        self.assertIn('LD-220MG', metadata['description'])
        limits = T._group_servo_limits({'leg': 6.0, 'arm': 5.0, 'eye': 5.0})
        import export_urdf as E
        for group, voltage in {'leg': 6.0, 'arm': 5.0, 'eye': 5.0}.items():
            expected = E.servo_limits_at_voltage(voltage)[group]
            self.assertAlmostEqual(limits[group]['effort'], expected['effort'])
            self.assertAlmostEqual(limits[group]['velocity'], expected['velocity'])

    def test_native_profile_uses_header_flag_and_rejects_header_drift(self):
        """候補/最終のC++入力はheader経由で、config.h書換えへ戻らない。"""
        import json
        with tempfile.TemporaryDirectory(prefix='tachikoma-native-contract-') as temp:
            folder=Path(temp)
            config_sha=print_first_sha(ROOT/'firmware/src/config.h')
            profile=dict(PRINT_FIRST_PROFILE,stance_r=106.)
            prepare_native(profile,folder,profile_mode='candidate_print_first')
            build=json.loads((folder/'build.json').read_text())
            self.assertEqual(build['print_first_compile_flag'],
                             '-DTACHIKOMA_PRINT_FIRST_PROFILE=1')
            self.assertIn('-DTACHIKOMA_PRINT_FIRST_PROFILE=1',build['command'])
            self.assertEqual(print_first_sha(folder/'config.h'),config_sha)
            self.assertEqual(build['profile_mode'],'candidate_print_first')
            header=folder/'print_first_gait.h'
            header.write_text(header.read_text().replace('PRINT_FIRST_STANCE_R = 106.000000f',
                                                          'PRINT_FIRST_STANCE_R = 107.000000f'))
            with self.assertRaisesRegex(ValueError,'header/profile mismatch'):
                _check_profile_header(profile,header,mode='candidate_print_first')

    def test_native_build_provenance_rejects_flag_or_source_drift(self):
        """build.jsonのコンパイル条件・複製ヘッダー来歴を再検証する。"""
        with tempfile.TemporaryDirectory(prefix='tachikoma-native-build-proof-') as temp:
            folder = Path(temp) / 'firmware' / 'candidate'
            binary = prepare_native(dict(PRINT_FIRST_PROFILE), folder,
                                     profile_mode='candidate_print_first')
            build_path = folder / 'build.json'
            build = json.loads(build_path.read_text(encoding='utf-8'))
            payload = {'profile_mode': 'candidate_print_first', 'header': {}}
            PX._validate_build_provenance(
                build_path, build, payload, Path(temp),
                folder / 'print_first_gait.h', binary)
            bad = json.loads(json.dumps(build))
            bad['compile_flags'][-1] = '-DTACHIKOMA_PRINT_FIRST_PROFILE=0'
            with self.assertRaisesRegex(ValueError, 'compile_flags'):
                PX._validate_build_provenance(
                    build_path, bad, payload, Path(temp),
                    folder / 'print_first_gait.h', binary)
            bad_source = json.loads(json.dumps(build))
            bad_source['source_headers'][0]['source_sha256'] = '0' * 64
            with self.assertRaisesRegex(ValueError, 'source header SHA'):
                PX._validate_build_provenance(
                    build_path, bad_source, payload, Path(temp),
                    folder / 'print_first_gait.h', binary)

    def test_stop_boundary_near_phase_wrap_reaches_holding_in_cpp_and_python(self):
        """停止要求が1.0境界を飛び越えず、実C++とPython写しが停止する。"""
        profile=dict(PRINT_FIRST_PROFILE)
        commands=([[.02,0.,1.,0.,profile['body_h']]]*119 +
                  [[.02,0.,0.,0.,profile['body_h']]]*75)
        with tempfile.TemporaryDirectory(prefix='tachikoma-stop-boundary-') as temp:
            folder=Path(temp)
            binary=prepare_native(profile,folder,profile_mode='candidate_print_first')
            result=subprocess.run(
                [str(binary),'ready'],
                input=''.join(' '.join(map(str,row))+'\n' for row in commands),
                check=True,capture_output=True,text=True)
        native=np.loadtxt(result.stdout.splitlines(),ndmin=2)
        self.assertEqual(native.shape,(len(commands),43))
        self.assertLess(float(native[:,0].max()),1.0)
        # One frame may still be the final approach to q=1.0 because the
        # float32 next phase is just below the boundary.  The following
        # frame must latch holding and remain there.
        self.assertTrue(np.all(native[120:,1] == 0.0))
        with P.python_profile(profile):
            driver=S.PhaseDriver()
            for _ in range(119):
                driver.step(.02,0.,1.,0.)
            self.assertGreater(driver.phase,.9)
            for _ in range(75):
                driver.step(.02,0.,0.,0.)
            self.assertTrue(driver.holding)
            self.assertLess(driver.phase,1.0)

    def test_freeze_manifest_rejects_stl_changed_after_freeze(self):
        """凍結URDFと新STLを混在させたまま実行入口を通さない。"""
        import json
        import xml.etree.ElementTree as ET

        final_urdf=ROOT/'hardware/urdf/tachikoma.urdf'
        self.assertTrue(final_urdf.is_file())
        freeze_time='2026-09-05T00:00:00Z'
        with tempfile.TemporaryDirectory(prefix='tachikoma-freeze-contract-') as temp:
            folder=Path(temp)
            changed_stl=folder/'new_tpu_shoe.stl'
            changed_stl.write_bytes(b'freeze-test-stl-v1')
            assembly=folder/'feet-assembly.json'
            assembly.write_text(json.dumps({'parts':[{'name':'tpu_shoe','path':str(changed_stl)}]}))

            root=ET.parse(final_urdf).getroot()
            mesh_paths=[]
            for mesh in root.findall('.//mesh'):
                filename=mesh.get('filename','')
                if filename:
                    path=Path(filename)
                    mesh_paths.append(path if path.is_absolute() else (final_urdf.parent/path).resolve())
            required=[final_urdf,assembly,changed_stl]
            required.extend(ROOT/path for path in FREEZE_REQUIRED_SOURCES)
            required.extend(mesh_paths)
            rows=[];seen=set()
            for path in required:
                path=Path(path).resolve()
                if path in seen:continue
                seen.add(path)
                try:
                    label=str(path.relative_to(ROOT))
                except ValueError:
                    label=str(path)
                rows.append({'path':label,'sha256':print_first_sha(path)})
            manifest=folder/'freeze-manifest.json'
            manifest.write_text(json.dumps({'status':'FROZEN',
                'geometry_freeze_time':freeze_time,'files':rows},indent=2)+'\n')
            case={'name':'freeze-contract','model':{
                'model_kind':'final_integrated','model_path':str(final_urdf),
                'freeze_manifest':str(manifest),
                'geometry_freeze_time':freeze_time,
                'geometry_freeze_hash':print_first_sha(manifest),
                'feet_manifest':str(assembly)}}
            before=validate_freeze_manifest(case)
            self.assertEqual(before['status'],'PASS',before)
            changed_stl.write_bytes(b'freeze-test-stl-v2')
            after=validate_freeze_manifest(case)
            self.assertEqual(after['status'],'UNVERIFIED')
            self.assertTrue(any(row.get('kind')=='file_sha256' and
                                row.get('file')==str(changed_stl.resolve())
                                for row in after['mismatches']),after)

    def test_freeze_generator_requires_exact_urdf_mesh_closure(self):
        """凍結生成はURDF参照メッシュだけを台帳へ入れ、missing/orphanを拒否する。"""
        import make_print_first_freeze_manifest as F

        self.assertEqual(
            F.canonical_utc_freeze_time('2026-09-06T21:34:56+09:00'),
            '2026-09-06T12:34:56Z')
        with self.assertRaisesRegex(ValueError, 'offset'):
            F.canonical_utc_freeze_time('2026-09-06T12:34:56')

        current = F.build_manifest(
            F.DEFAULT_URDF, [], '2026-09-06T12:34:56Z')
        self.assertEqual(current['final_urdf_mesh_count'], 80)
        self.assertEqual(current['final_mesh_bundle']['referenced_count'], 80)
        self.assertEqual(current['final_mesh_bundle']['files_count'], 80)
        self.assertEqual(current['final_mesh_bundle']['orphan_count'], 0)
        self.assertEqual(current['final_mesh_bundle']['referenced_outside_bundle_count'], 0)

        with tempfile.TemporaryDirectory(dir=ROOT, prefix='.freeze-generator-') as temp:
            folder = Path(temp)
            mesh_dir = folder / 'meshes'
            mesh_dir.mkdir()
            referenced = mesh_dir / 'referenced.stl'
            orphan = mesh_dir / 'orphan.stl'
            referenced.write_bytes(b'referenced')
            orphan.write_bytes(b'orphan')
            urdf = folder / 'tachikoma.urdf'
            urdf.write_text(
                '<robot name="contract"><link name="base_link">'
                '<visual><geometry><mesh filename="meshes/referenced.stl"/>'
                '</geometry></visual></link></robot>', encoding='utf-8')
            freeze_time = '2026-09-06T12:34:56Z'
            with self.assertRaisesRegex(ValueError, '孤立メッシュ'):
                F.build_manifest(urdf, [], freeze_time)
            orphan.unlink()
            manifest = F.build_manifest(urdf, [], freeze_time)
            self.assertEqual(manifest['final_urdf_mesh_count'], 1)
            self.assertEqual(manifest['final_mesh_bundle']['referenced_count'], 1)
            self.assertEqual(manifest['final_mesh_bundle']['files_count'], 1)
            self.assertEqual(manifest['final_mesh_bundle']['orphan_count'], 0)
            self.assertEqual(manifest['final_mesh_bundle']['referenced_outside_bundle_count'], 0)
            self.assertEqual(manifest['geometry_freeze_time'], freeze_time)
            self.assertEqual(
                sum(row['path'].endswith('meshes/referenced.stl')
                    for row in manifest['files']), 1)
            referenced.unlink()
            outside = folder / 'outside.stl'
            outside.write_bytes(b'outside-bundle')
            urdf.write_text(
                '<robot name="contract"><link name="base_link">'
                '<visual><geometry><mesh filename="outside.stl"/>'
                '</geometry></visual></link></robot>', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'final_mesh_bundle外'):
                F.build_manifest(urdf, [], freeze_time)
            urdf.write_text(
                '<robot name="contract"><link name="base_link">'
                '<visual><geometry><mesh filename="meshes/referenced.stl"/>'
                '</geometry></visual></link></robot>', encoding='utf-8')
            with self.assertRaisesRegex(FileNotFoundError, 'final_urdf_mesh'):
                F.build_manifest(urdf, [], freeze_time)


if __name__=='__main__':
    unittest.main()
