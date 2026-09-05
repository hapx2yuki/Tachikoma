import json
from pathlib import Path
base=[{'name':'settle','duration':2},{'name':'walk','duration':6.4,'vy':1},{'name':'stop','duration':1.6}]
cases=[]
def add(name,model=None,segments=None,**extra):cases.append(dict(name=name,model=model or {},segments=segments or [dict(s) for s in base],**extra))
add('native_nominal')
for name,vx,vy,wz in [('back',0,-1,0),('right',1,0,0),('left',-1,0,0),('turn_ccw',0,0,1),('turn_cw',0,0,-1),('compound',.7,.7,.7)]:
 seg=[dict(s) for s in base];seg[1].update(vx=vx,vy=vy,wz=wz);add(name,segments=seg)
for h in [110,120,125,130]:add(f'height_{h}',segments=[dict(s,body_h=h) for s in base])
add('height_changes',segments=[{'name':f'height_{h}','duration':3.2,'body_h':h,'vy':1} for h in [115,130,110,125,115]])
add('abrupt_reverse',segments=[{'name':'settle','duration':2}]+[dict(name=n,duration=3.2,**v) for n,v in [('forward',{'vy':1}),('backward',{'vy':-1}),('ccw',{'wz':1}),('cw',{'wz':-1}),('right',{'vx':1}),('left',{'vx':-1})]])
add('zero_direct',initial='zero')
add('zero_sequential',initial='zero',startup_sequential=True,segments=[{'name':'startup','duration':3.2}]+base[1:])
for ph in range(16):
 seg=[{'name':'settle','duration':2},{'name':'phase_setup','duration':1.6+ph*.1,'vy':1},{'name':'stop','duration':.8},{'name':'restart','duration':3.2,'vy':1},{'name':'stop2','duration':.8}]
 add(f'stop_phase_{ph:02d}',segments=seg)
for slope in [-10,-5,5,10]:add(f'slope_{slope}',{'slope_deg':slope})
for step in [2,5,10]:add(f'step_{step}',{'step_height_mm':step,'step_front_y':.16})
for force in [2,5,10]:add(f'push_{force}N',pushes=[{'start':4,'duration':.2,'force_N':[force,0,0]}])
for key,values in [('mass_scale',[.7,1.3]),('effort_scale',[.5,.7,.9]),('velocity_scale',[.5,.75]),('friction',[0,.15,.3,.6])]:
 for value in values:add(f'{key}_{value}',{key:value})
add('combined_stress',{'mass_scale':1.3,'effort_scale':.7,'velocity_scale':.75,'friction':.3})
for secs in [60,180]:add(f'long_{secs}',segments=[{'name':'settle','duration':2},{'name':'walk','duration':secs,'vy':1},{'name':'stop','duration':2}])
add('timestep_half',{'timestep':.001})
for mode in ['linked-hulls','parts','vhacd']:
 for parent in [False,True]:add('self_'+mode+('_all' if parent else '_nonparent'),{'contact_model':mode,'self_collision':True,'include_parent_collision':parent},segments=[{'name':'hold','duration':.2}])
for mode in ['parts','vhacd']:
 add(f'contact_{mode}',{'contact_model':mode})
 add(f'contact_{mode}_uniform',{'contact_model':mode,'hard_friction':1.})
for volts in [5.,5.5,6.]:add(f'voltage_{volts}',{'voltage_V':volts})
for mode in ['parts','vhacd']:
 add(f'servos_self_{mode}',{'contact_model':mode,'self_collision':True,'include_parent_collision':True,'include_servo_collision':True},segments=[{'name':'hold','duration':.2}])
Path(__file__).with_name('stress-cases.json').write_text(json.dumps(cases,indent=2))
print(len(cases),'cases')
