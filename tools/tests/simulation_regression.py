#!/usr/bin/env python3
"""シミュレーション検証器の回帰。機構形状の合否とは別。"""
import json
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import sim_physics as sp

class SimulationRegression(unittest.TestCase):
    def test_catalog_limits_use_operating_voltage(self):
        import export_urdf as E
        limits=E.servo_limits_at_voltage(6.)
        self.assertAlmostEqual(limits['leg']['effort'],(18+3.5/1.8)*.0980665)
        self.assertAlmostEqual(limits['arm']['effort'],(1.8+1.2/1.8*.4)*.0980665)
        self.assertLess(limits['arm']['effort'],.22)
        self.assertAlmostEqual(limits['arm']['velocity'],math.pi/3/.08)
        with self.assertRaises(ValueError):E.servo_limits_at_voltage(7.)

    def test_native_firmware_trace(self):
        # 起動静止→各方向/複合指令→境界外の停止→再開。浮動小数の誤差も観測する。
        commands = []
        for command, count in [((0,0,0),50), ((0,1,0),401), ((0,0,0),37),
                               ((1,0,0),47), ((0,0,1),153), ((0,0,0),57),
                               ((0,-1,0),133), ((.7,-.7,-.7),119), ((0,0,0),80)]:
            commands.extend([(.02,*command,115)]*count)
        for h in (110,120,125,130,110,115):
            commands.extend([(.02,.7,-.7,.7,h)]*81)
        with tempfile.TemporaryDirectory(prefix='tachikoma-gait-test-') as tmp:
            binary = Path(tmp) / 'trace'
            subprocess.run(['c++','-std=c++17','-O0',str(Path(__file__).with_name('simulation_firmware_trace.cpp')),
                            '-o',str(binary)],check=True,capture_output=True,text=True)
            result = subprocess.run([str(binary)],input='\n'.join(' '.join(map(str,c)) for c in commands)+'\n',
                                    check=True,capture_output=True,text=True)
        native = np.loadtxt(result.stdout.splitlines())
        driver = sp.PhaseDriver(); output = sp.LegOutputDriver(); last={}; worst=0
        for i,(dt,vx,vy,wz,h) in enumerate(commands):
            phase = driver.step(dt,vx,vy,wz)
            self.assertEqual(not driver.holding, bool(native[i,1]), f'holding frame {i}')
            self.assertLess(abs((phase-native[i,0]+.5)%1-.5),.0001, f'phase frame {i}')
            targets, angles = sp.compute_leg_targets(phase,vx,vy,wz,last,holding=driver.holding,body_h=h)
            _, current = output.step(targets,dt)
            both = np.array([angles[n] for n in sp.sg._LEGS]+[current[n] for n in sp.sg._LEGS]).reshape(-1)
            error = np.max(abs(both-native[i,2:])); worst=max(worst,error)
            self.assertLess(error,.02, f'angle frame {i} (deg)')
        print(f'native firmware trace: {len(commands)} frames, max angle error {worst:.6f} deg')

    def test_native_sequential_output_and_quantization(self):
        import sim_stress as stress
        trace=stress.native_output_trace({'initial':'zero','startup_sequential':True,
            'segments':[{'duration':2.4,'name':'startup'}]})
        counts=trace[:,23:].sum(axis=1)
        self.assertEqual(counts[3],0)
        self.assertEqual(counts[4],1)
        self.assertEqual(counts[98],19)
        self.assertEqual(counts[99],20)
        self.assertFalse(trace[98,2]);self.assertTrue(trace[99,2])
        # 50Hz PCA tickは4.88us。180deg/2000usの変換を通すため0.4392deg刻み。
        neutral=trace[4,3]
        self.assertLess(abs(neutral),.44)
        self.assertNotEqual(neutral,0.)

    def test_native_height_change_starts_from_real_ready_angles(self):
        import sim_stress as stress
        trace=stress.native_output_trace({'segments':[{'duration':.04,'name':'height130','body_h':130}]},include_initial=True)
        reference=stress.native_output_trace({'segments':[{'duration':.04,'name':'height115','body_h':115}]},include_initial=True)
        np.testing.assert_array_equal(trace[0],reference[0])
        self.assertGreater(np.max(abs(trace[1,3:15]-trace[0,3:15])),.1)

    def test_candidate_mass_update_keeps_ground_collision_valid(self):
        import sim_stress as stress
        case={'name':'candidate_regression','model':{'contact_model':'parts','friction':.6,
              'foot_candidate_dir':'docs/audits/20260905-round2/foot-support-candidates'},
              'segments':[{'name':'hold','duration':.5}]}
        with tempfile.TemporaryDirectory(prefix='tachikoma-contact-test-') as tmp:
            result=stress.execute(case,tmp)
        # コンパイル後の慣性座標変更ではBVH境界が古く、靴を抜けて脛へ落下した。
        self.assertTrue(result['checks']['no_fall'])
        self.assertGreater(result['min_base_z_m'],.11)
        self.assertGreater(result['tpu_support']['fraction'],.95)

    def test_inertial_mount_origins_and_pad_mass(self):
        import export_urdf as E
        C=E.C
        yaw,pitch,knee=E.leg_servo_items('FR')
        p=C.LEG_SERVO;cx=p['L']/2-p['SHAFT_OFF']
        np.testing.assert_allclose(yaw.com_m*1000,[C.HIPS['FR'][0]-cx,C.HIPS['FR'][1],
            C.HIP_DROP+C.CHASSIS_T+3+(p['TAB_BELOW']-p['ABOVE_TAB'])/2],atol=1e-9)
        self.assertGreater(pitch.com_m[1],0)  # FRはY鏡映、タブ下ケースが+Y。
        self.assertAlmostEqual(pitch.com_m[0]*1000,C.COXA_LEN-cx)
        self.assertGreater(pitch.I_com[2,2],pitch.I_com[1,1])
        electronics={x.label:x for x in E.base_link_electronics_items()}
        self.assertAlmostEqual(electronics['battery_2s_2200mah'].com_m[2]*1000,C.HIP_DROP-16)
        parts=E.collect_all_parts()
        pads=[E.part_mass_item(m,n) for items in parts.values() for m,_,n in items if n=='foot_pad']
        self.assertEqual(len(pads),4);self.assertTrue(all(p.mass_kg>0 for p in pads))
        # 目ケースはタブ原点に固定し、頭の180度回転と6.1mm積層を反映。
        p=C.EYE_SERVO
        offset=p['ABOVE_TAB']+p['HORN_HUB_H']-(p['HORN_T']+C.CLEAR)
        frame=E.eye_servo_frame(0)
        np.testing.assert_allclose(E._eye_mount(0)[:3,3]-frame[:3,3],
                                   E._eye_mount(0)[:3,2]*offset,atol=1e-12)
        center=np.array([-(p['L']/2-p['SHAFT_OFF']),0,
                         (p['ABOVE_TAB']-p['TAB_BELOW'])/2,1])
        np.testing.assert_allclose(electronics['eye_r_servo'].com_m*1000,
                                   (frame@center)[:3])
        from sim_collision import parts_with_pad
        cases=[n for items in parts_with_pad(True).values() for _,_,n in items
               if n.endswith('_servo_case')]
        self.assertEqual(len(cases),20)

    def test_holding_really_removes_sway(self):
        _, a = sp.compute_leg_targets(0.,0.,0.,0.,{},holding=True)
        for i,leg in enumerate(sp.sg._LEGS):
            local=np.array(sp.sg.leg_fk(*a[leg]))
            c,s=math.cos(sp.sg.MOUNT[i]),math.sin(sp.sg.MOUNT[i])
            world=[local[0]*c-local[1]*s+sp.sg.ORIGIN[i,0],local[0]*s+local[1]*c+sp.sg.ORIGIN[i,1]]
            self.assertTrue(np.allclose(world,sp.sg.neutral_xy(i),atol=1e-8))

    def test_fallback_count_includes_recovered_ik(self):
        original=sp.sg.leg_ik; calls=0
        def fail_first(*args):
            nonlocal calls
            calls += 1
            return None if calls == 1 else original(*args)
        state={}
        with patch.object(sp.sg,'leg_ik',side_effect=fail_first):
            _,a=sp.compute_leg_targets(.13,0.,1.,0.,state)
        self.assertEqual(state['_ik_fallback_count'],1)
        self.assertEqual(state.get('_ik_fail_count',0),0)
        _,neutral=sp.compute_leg_targets(0.,0.,0.,0.,{},holding=True)
        np.testing.assert_allclose(a['FR'],neutral['FR'])

    def test_no_phantom_base_damping_or_inertia(self):
        m,idx=sp.build_model(1.,dict(leg=24.,arm=.8,eye=.05),dict(leg=.4,arm=.03,eye=.005))
        np.testing.assert_equal(m.dof_damping[:6],np.zeros(6))
        np.testing.assert_equal(m.dof_armature[:6],np.zeros(6))
        for name in sp.ALL_JOINTS:
            self.assertGreater(m.dof_damping[m.jnt_dofadr[idx['jid'][name]]],0)
        self.assertEqual(set(idx['velocity_limits']),set(sp.ALL_JOINTS))

    def test_compiled_mass_and_joint_constants_are_consistent(self):
        import mujoco
        m,_=sp.build_model(1.,dict(leg=24.,arm=.8,eye=.05),dict(leg=.4,arm=.03,eye=.005),mass_scale=1.3)
        bid=mujoco.mj_name2id(m,mujoco.mjtObj.mjOBJ_BODY,'base_link')
        self.assertAlmostEqual(m.body_subtreemass[bid],m.body_mass.sum(),places=12)
        before={n:getattr(m,n).copy() for n in ('body_subtreemass','body_invweight0','dof_invweight0')}
        mujoco.mj_setConst(m,mujoco.MjData(m))
        for name,value in before.items():
            np.testing.assert_allclose(getattr(m,name),value,rtol=1e-12,atol=1e-12,err_msg=name)

    def test_collision_cache_never_publishes_partial_archive(self):
        import sim_collision
        from unittest.mock import patch
        with tempfile.TemporaryDirectory(prefix='tachikoma-cache-test-') as tmp:
            path=Path(tmp)/'hull.npz'
            sim_collision._atomic_save_hulls(path,{'count':np.array(1)})
            original=path.read_bytes()
            def broken_save(stream,**arrays):
                stream.write(b'partial archive')
                raise OSError('simulated write failure')
            with patch.object(sim_collision.np,'savez_compressed',side_effect=broken_save):
                with self.assertRaises(OSError):
                    sim_collision._atomic_save_hulls(path,{'count':np.array(2)})
            self.assertEqual(path.read_bytes(),original)
            self.assertEqual(list(Path(tmp).glob('*.tmp')),[])
            sim_collision._atomic_save_hulls(path,{'count':np.array(2)})
            with np.load(path,allow_pickle=False) as saved:self.assertEqual(int(saved['count']),2)

    def test_export_input_gate_preserves_cavities_and_existing_output(self):
        import export_urdf as E
        import trimesh
        outer=trimesh.creation.box([10,10,10]);inner=trimesh.creation.box([6,6,6]);inner.invert()
        cavity=trimesh.util.concatenate([outer,inner])
        E.validate_input_parts({'base_link':[(cavity,'#333333','closed_cavity')]})
        self.assertAlmostEqual(cavity.volume,784.)
        second=trimesh.creation.box([1,1,1]);second.apply_translation([20,0,0])
        E.validate_input_parts({'base_link':[(trimesh.util.concatenate([outer,second]),'#333333','multiple_shells')]})
        open_mesh=trimesh.Trimesh(vertices=outer.vertices.copy(),faces=outer.faces[:-1],process=False)
        with tempfile.TemporaryDirectory(prefix='tachikoma-export-test-') as tmp:
            out=Path(tmp)/'urdf';(out/'meshes').mkdir(parents=True)
            (out/'tachikoma.urdf').write_bytes(b'previous urdf')
            (out/'meshes'/'base_link__col_0.stl').write_bytes(b'previous mesh')
            prior={str(p.relative_to(out)):p.read_bytes() for p in out.rglob('*') if p.is_file()}
            with patch.object(E,'OUT',out),patch.object(E,'collect_all_parts',return_value={'base_link':[(open_mesh,'#333333','bad_open_mesh')]}):
                with self.assertRaisesRegex(ValueError,'bad_open_mesh'):E.main()
            after={str(p.relative_to(out)):p.read_bytes() for p in out.rglob('*') if p.is_file()}
            self.assertEqual(after,prior)

    def test_export_bundle_failure_keeps_previous_meshes_and_urdf(self):
        import export_urdf as E
        import trimesh
        with tempfile.TemporaryDirectory(prefix='tachikoma-export-test-') as tmp:
            out=Path(tmp)/'urdf';(out/'meshes').mkdir(parents=True)
            for name in ['tachikoma.urdf','render_ref_stand.png','meshes/base_link__col_0.stl']:
                (out/name).write_bytes(name.encode())
            prior={str(p.relative_to(out)):p.read_bytes() for p in out.rglob('*') if p.is_file()}
            with patch.object(E,'OUT',out),patch.object(E,'build_urdf',side_effect=ValueError('simulated XML failure')):
                with self.assertRaisesRegex(ValueError,'simulated XML failure'):
                    E.save_output_bundle({}, {}, {'base_link':[('#ffffff',trimesh.creation.box([.01,.01,.01]))]}, {})
            self.assertEqual({str(p.relative_to(out)):p.read_bytes() for p in out.rglob('*') if p.is_file()},prior)
            self.assertEqual(list(Path(tmp).glob('.urdf-export-*')),[])

    def test_speed_torque_envelope(self):
        ranges=np.tile([-2.,2.],(5,1)); speeds=np.array([0.,3.,6.,-6.,12.])
        actual=sp.speed_torque_ranges(ranges,speeds,np.full(5,6.))
        np.testing.assert_allclose(actual,[[-2,2],[-2,1],[-2,0],[0,2],[-2,0]])

    def test_signed_forward_and_short_walk(self):
        t=np.arange(0,14,.1); xyz=np.column_stack([t*.02,-t*.01,np.full(len(t),.115)])
        m=sp.walk_displacement(t,xyz,1,9,0,1)
        self.assertLess(m['forward_m'],0)  # 横移動・後退のノルムを前進合格に使わない。
        self.assertAlmostEqual(m['commanded_direction_speed_m_s'],-.01)
        self.assertAlmostEqual(m['effective_elapsed_s'],6.4)
        self.assertFalse(sp.walk_displacement(t,xyz,1,2,0,1)['available'])
        self.assertFalse(sp.walk_displacement(t,xyz,1,1,0,1)['available'])

    def test_failed_scenario_has_nonzero_exit(self):
        with tempfile.TemporaryDirectory(prefix='tachikoma-sim-test-') as tmp:
            metrics=Path(tmp)/'metrics.json'
            result=subprocess.run([sys.executable,str(ROOT/'tools/sim_physics.py'),'--novideo',
                                   '--settle','0.02','--walk','0','--turn','0','--stop','0',
                                   '--max-saturation-fraction','0','--kp-leg','10000',
                                   '--metrics',str(metrics)],capture_output=True,text=True)
            self.assertEqual(result.returncode,1,result.stdout+result.stderr)
            m=json.loads(metrics.read_text())
            self.assertEqual(m['simulation_acceptance']['status'],'FAIL')
            self.assertFalse(m['simulation_acceptance']['checks']['leg_torque_saturation'])
            self.assertIsNone(m['forward_distance_during_walk_phase_m'])
            self.assertEqual(m['physical_readiness'],'UNVERIFIED')
            self.assertIn('firmware/src/gait.h',m['input_sha256'])

if __name__ == '__main__':
    unittest.main(verbosity=2)
