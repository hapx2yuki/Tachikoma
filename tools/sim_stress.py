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


def input_fingerprints(case):
    hashes=S.input_fingerprints()
    candidate=case.get('model',{}).get('foot_candidate_dir')
    if candidate:
        folder=Path(candidate)
        if not folder.is_absolute():folder=ROOT/folder
        for i in range(3):
            for stem in ('shoe_fitted','toe_hidden_seat'):
                path=folder/f'FR_{i}_{stem}_candidate.stl'
                hashes[str(path.relative_to(ROOT))]=hashlib.sha256(path.read_bytes()).hexdigest()
    return dict(sorted(hashes.items()))


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


def native_output_trace(case,include_initial=False):
    edges=np.cumsum([s['duration'] for s in case['segments']]);commands=[]
    if include_initial:commands.append([0.,0.,0.,0.,S.BODY_H_DEFAULT])
    for i in range(round(edges[-1]*S.SERVO_HZ)):
        segment=case['segments'][min(np.searchsorted(edges,i/S.SERVO_HZ+1e-10,side='right'),len(edges)-1)]
        commands.append([1/S.SERVO_HZ,*[segment.get(p,0.) for p in ('vx','vy','wz')],segment.get('body_h',S.BODY_H_DEFAULT)])
    with tempfile.TemporaryDirectory(prefix='tachikoma-native-sim-') as td:
        binary=Path(td)/'trace'
        subprocess.run(['c++','-std=c++17','-O2','-I',str(ROOT/'tools/tests/firmware_stubs'),'-I',str(ROOT/'firmware/src'),str(ROOT/'tools/tests/simulation_output_trace.cpp'),'-o',str(binary)],check=True,capture_output=True,text=True)
        result=subprocess.run([str(binary),'sequential' if case.get('startup_sequential') else 'direct' if case.get('initial')=='zero' else 'ready'],input='\n'.join(' '.join(map(str,c)) for c in commands)+'\n',check=True,capture_output=True,text=True)
    return np.loadtxt(result.stdout.splitlines(),ndmin=2)


def execute(case,out_dir):
    if not case.get('segments'):raise ValueError('1個以上の指令区間が必要')
    for segment in case['segments']:
        duration=segment.get('duration',0)
        if not math.isfinite(duration) or duration<=0:raise ValueError('区間長は有限の正数が必要')
        if not math.isclose(duration*S.SERVO_HZ,round(duration*S.SERVO_HZ),abs_tol=1e-8):
            raise ValueError('区間長は50Hz制御周期の整数倍が必要')
        if any(not math.isfinite(segment.get(k,0)) or abs(segment.get(k,0))>1 for k in ('vx','vy','wz')):
            raise ValueError('指令は有限な-1..1が必要')
    out_dir=Path(out_dir);out_dir.mkdir(parents=True,exist_ok=True)
    current_hash=input_fingerprints(case)
    options=case.get('model',{})
    gains=case.get('gains',{'kp':{'leg':24.,'arm':.8,'eye':.05},'kv':{'leg':.4,'arm':.03,'eye':.005}})
    m,idx=S.build_model(options.get('friction',1.),gains['kp'],gains['kv'],
        timestep=options.get('timestep',.002),effort_scale=options.get('effort_scale',1.),
        mass_scale=options.get('mass_scale',1.),self_collision=options.get('self_collision',False),
        include_parent_collision=options.get('include_parent_collision',False),
        slope_deg=options.get('slope_deg',0.),step_height_mm=options.get('step_height_mm',0.),
        step_front_y=options.get('step_front_y',.25),contact_model=options.get('contact_model','linked-hulls'),
        hard_friction=options.get('hard_friction',.3),include_servo_collision=options.get('include_servo_collision',False),
        foot_candidate_dir=options.get('foot_candidate_dir'))
    native_full=native_output_trace(case,include_initial=True) if case.get('controller','native')=='native' else None
    native=native_full[1:] if native_full is not None else None
    d=mujoco.MjData(m);mujoco.mj_setConst(m,d)
    dt=float(m.opt.timestep);ctrl_steps=round(1/S.SERVO_HZ/dt)
    if not math.isclose(ctrl_steps*dt,1/S.SERVO_HZ):raise ValueError('制御周期の整数分割が必要')
    segments=case['segments'];edges=np.cumsum([s['duration'] for s in segments]);total=float(edges[-1]);steps=round(total/dt)
    base=mujoco.mj_name2id(m,mujoco.mjtObj.mjOBJ_BODY,'base_link');qbase=int(m.jnt_qposadr[m.body_jntadr[base]])
    names=S.ALL_JOINTS;aids=np.array([idx['aid'][n] for n in names]);jids=[idx['jid'][n] for n in names]
    qa=np.array([m.jnt_qposadr[j] for j in jids]);da=np.array([m.jnt_dofadr[j] for j in jids])
    stall=m.actuator_forcerange[aids].copy();speed=np.array([idx['velocity_limits'][n] for n in names])*options.get('velocity_scale',1.)
    if 'voltage_V' in options:
        volts=options['voltage_V']
        import export_urdf as E
        limits=E.servo_limits_at_voltage(volts)
        for i,n in enumerate(names):
            group='leg' if n.startswith('leg_') else 'arm' if n.startswith('arm_') else 'eye'
            torque=limits[group]['effort']
            stall[i]=np.array([-torque,torque])*options.get('effort_scale',1.)
            speed[i]=limits[group]['velocity']*options.get('velocity_scale',1.)

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
    if case.get('ground_initialization',True):place_on_ground(m,d,qbase,options.get('slope_deg',0.))
    initial_pos=d.xpos[base].copy();initial_penetration=self_contacts(m,d)
    driver=S.PhaseDriver();output=S.LegOutputDriver(current)
    torque_abs_sum=np.zeros(len(aids));torque_max=torque_abs_sum.copy();vel_max=torque_abs_sum.copy();error_max=torque_abs_sum.copy()
    saturation_count=np.zeros(len(aids),int);power_positive=[];power_signed=[];self_max=dict(initial_penetration)
    segment_data=[{'name':s['name'],'steps':0,'start_pos':None,'end_pos':None,'positive_power_sum':0.,
                   'normal_impulse_by_material':{},'normal_impulse_by_part':{},'yaw_change_deg':0.,'commanded_path_distance_m':0.} for s in segments]
    timeseries=[];sample_stride=max(1,round(.1/dt));nonfoot=0;fell=None;warning={};max_roll=max_pitch=0.
    enabled=np.ones(len(aids),bool);previous_pos=initial_pos.copy()
    min_h=math.inf;max_qvel=0.;yaw_last=None;force_temp=np.zeros(6);numeric_failure=False
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
                row=native[k//ctrl_steps];driver.phase=float(row[0]);driver.holding=not bool(row[1])
                d.ctrl[aids]=np.radians(row[3:23]);enabled=row[23:43].astype(bool)
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
        torque_abs_sum+=abs(actual);torque_max=np.maximum(torque_max,abs(actual));vel_max=np.maximum(vel_max,abs(d.qvel[da]));error_max=np.maximum(error_max,abs(error));saturation_count+=sat
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
            timeseries.append({'time':float(d.time),'segment':seg['name'],'phase':driver.phase,'holding':driver.holding,'command':[vx,vy,wz,h],'base_pos':pos.tolist(),'rpy_deg':[float(r),float(p),float(y)],'qpos':d.qpos.tolist(),'torque_nm':actual.tolist(),'velocity_rad_s':d.qvel[da].tolist(),'positive_power_W':positive,'normal_force_by_material_N':normal_by_mat,'contacts':sorted(contacts)})
    completed=k+1;valid=len(power_positive)
    axes={n:{'mean_absolute_torque_nm':float(torque_abs_sum[i]/max(1,valid)),'max_absolute_torque_nm':float(torque_max[i]),'max_velocity_rad_s':float(vel_max[i]),'saturation_fraction':float(saturation_count[i]/max(1,valid)),'max_tracking_error_deg':float(np.degrees(error_max[i])),'stall_limit_nm':float(stall[i,1]),'no_load_velocity_rad_s':float(speed[i])} for i,n in enumerate(names)}
    for seg,sd in zip(segments,segment_data):
        sd['duration_s']=sd['steps']*dt
        sd['mean_positive_mechanical_power_W']=sd.pop('positive_power_sum')/max(1,sd['steps'])
        if sd['start_pos']:
            delta=np.array(sd['end_pos'])-sd['start_pos'];sd['delta_xyz_m']=delta.tolist()
            v=np.array([seg.get('vx',0),seg.get('vy',0)]);sd['commanded_direction_distance_m']=float(delta[:2]@v/np.linalg.norm(v)) if np.linalg.norm(v) else None
    checks={'completed':completed==steps and not numeric_failure,'numeric_stability':not numeric_failure,
        'no_fall':fell is None,'no_nonleg_contact':nonfoot==0,'no_ik_fallback':not last.get('_ik_fallback_count',0) and not last.get('_ik_fail_count',0),
        'initial_self_penetration_le_0p1mm':max(initial_penetration.values(),default=0)<=.0001,
        'leg_saturation_le_5pct':max(axes[n]['saturation_fraction'] for n in S.ALL_LEG_JOINTS)<=.05,
        'inputs_unchanged':current_hash==input_fingerprints(case),
        'translation_progress':all(sd['commanded_path_distance_m']/max(sd['duration_s'],1e-10)>=.005 for seg,sd in zip(segments,segment_data) if math.hypot(seg.get('vx',0),seg.get('vy',0))>.5 and sd['duration_s']>=3),
        'rotation_progress':all(sd['yaw_change_deg']*seg.get('wz',0)>=1 for seg,sd in zip(segments,segment_data) if abs(seg.get('wz',0))>.5 and sd['duration_s']>=3)}
    if numeric_failure:status='NUMERICAL_FAILURE'
    elif not checks['initial_self_penetration_le_0p1mm']:status='INVALID_INITIAL_CONTACT_MODEL'
    elif fell is not None:status='FALL'
    elif all(checks.values()):status='PASS'
    else:status='FAIL'
    result={'case':case,'created_utc':datetime.now(timezone.utc).isoformat(),'input_sha256':current_hash,'engine':'mujoco','version':mujoco.__version__,'runtime':{'python':sys.version,'numpy':np.__version__,'platform':platform.platform()},'checks':checks,'status':status,'physical_readiness':'UNVERIFIED',
        'initial_position_m':initial_pos.tolist(),'total_sim_time_s':float(d.time),'valid_integrated_time_s':valid*dt,'requested_time_s':total,'warning_counts':warning,'fell_time_s':fell,'max_abs_roll_deg':max_roll,'max_abs_pitch_deg':max_pitch,'min_base_z_m':min_h if math.isfinite(min_h) else None,'max_abs_qvel':max_qvel,
        'initial_self_penetration_m':initial_penetration,'max_self_penetration_m':self_max,'nonleg_contact_steps':nonfoot,'ik_counts':{k:v for k,v in last.items() if k.startswith('_')},'mass_kg':float(m.body_mass.sum()),'joint_order':names,'actuators':axes,
        'positive_mechanical_power_W':{'mean':float(np.mean(power_positive)) if valid else None,'max':float(max(power_positive)) if valid else None,'p95':float(np.percentile(power_positive,95)) if valid else None},
        'voltage_model': 'DS3218 manufacturer 5/6.8V and MG90S TowerPro 4.8/6.6V torque, 4.8/6V speed endpoints linearly interpolated; eye voltage response unknown' if 'voltage_V' in options else 'URDF limits with explicit global sensitivity scales',
        'controller': 'actual firmware Gait+LegOutput+Arms+Servos including PWM quantization; hardware/network/IMU unavailable' if native is not None else 'Python leg replica, READY arms approximate',
        'candidate_geometry': 'TPU toe shoes and hidden toe seats; per-tibia printed mass/inertia recomputed, candidate only, not adopted into CAD' if options.get('foot_candidate_dir') else None,
        'power_interpretation':'軸出力の正機械仕事率のみ。停止保持電流、銅損、ドライバ損失、電源効率は別であり、電流上限の認定には使えない。',
        'segments':segment_data,'timeseries':timeseries}
    material_impulse={}
    for segment in segment_data:
        for material,value in segment['normal_impulse_by_material'].items():material_impulse[material]=material_impulse.get(material,0.)+value
    impulse=sum(material_impulse.values())
    fraction=material_impulse.get('TPU',0.)/impulse if impulse and options.get('contact_model','linked-hulls')!='linked-hulls' else None
    result['tpu_support']={'normal_impulse_Ns':material_impulse,'fraction':fraction,'status':'UNVERIFIED' if fraction is None else 'PASS' if fraction>=.95 else 'FAIL','threshold_fraction':.95}
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
