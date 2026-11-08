# Run only through live BlenderMCP. Flat diagram, not mechanical assembly.
import bpy,json,math,csv,copy
from pathlib import Path
from mathutils import Vector,Matrix
ROOT=Path('source-root/electronics-stack-study-20261108')
OUT=ROOT/'P37_COLOR_HARNESS'
old=bpy.data.scenes['P21_CURRENT_WIRING']
assert 'P37_COLOR_HARNESS' not in bpy.data.scenes
s=bpy.data.scenes.new('P37_COLOR_HARNESS');s.unit_settings.system='METRIC';s.unit_settings.scale_length=.001
bpy.context.window.scene=s
mapping={}
for o in old.objects:
 n=o.copy()
 if o.type in ['FONT','CURVE','CAMERA']: n.data=o.data.copy()
 n.name=o.name.replace('P21_','P37_',1);s.collection.objects.link(n);mapping[o]=n
for a,b in mapping.items():
 b.parent=mapping.get(a.parent);b.matrix_world=a.matrix_world.copy()
 if a.data and hasattr(a.data,'materials') and a.data.materials and a.data.materials[0]: b.color=a.data.materials[0].diffuse_color
s.camera=mapping[old.camera]
def find(name):
 return next((o for a,o in mapping.items() if a.name==name or a.name.startswith(name+'.')),None)
def drop(o):
 if o: bpy.data.objects.remove(o,do_unlink=True)
def txtset(name,body,pos=None,size=None):
 o=find(name)
 if o:
  o.data.body=body;o.color=(.035,.05,.075,1)
  if pos:o.location=pos
  if size:o.data.size=size
 return o
font=find('P21_P14_TX_TITLE').data.font
D=json.loads((ROOT/'P21_CURRENT_WIRING/P21_HARNESS_NETLIST.json').read_text())
# Remove old leg splices and their harnesses; retain all other module identities.
removeids={'leg_supply_direct','leg_return_trunk'}
for h in D['harnesses']:
 if any(h['id'].startswith(p) for p in ['power_trunk_L','ground_trunk_L','power_branch_L','ground_branch_L','signal_L']):removeids.add(h['id'])
for h in D['harnesses']:drop(find(h['curve_object']))
D['harnesses']=[h for h in D['harnesses'] if h['id'] not in removeids]
removeeps=[k for k in D['endpoints'] if k in ['LEG_FEED','LEG_RETURN'] or k.startswith('SPLICE_L')]
for k in removeeps:drop(find(D['endpoints'].pop(k)['object']))
# Arrange only flat-diagram leg components into two banks; no robot positions changed.
for ch in range(12):
 root=find('P21_P14_L'+str(ch));oldpos=root.matrix_world.translation.copy()
 desired=Vector((-255 if ch<6 else 255,145+55*(ch%6),oldpos.z));delta=desired-oldpos
 root.location+=delta
 for k in [f'PWM_L{ch}_SERVO',f'L{ch}_POWER',f'L{ch}_GND']:
  D['endpoints'][k]['point']=list(Vector(D['endpoints'][k]['point'])+delta)
 lab=find('P21_P14_TX_L'+str(ch));lab.location=(desired.x,desired.y+26,0);lab.data.size=5
# Copy the registered distributor native geometry without modifying it.
T=Matrix.Translation((0,278,22));dist={}
for a in bpy.data.scenes['P36_DISTRIBUTOR_NATIVE'].objects:
 b=a.copy();b.name='P37_DIST_'+a.name;b.parent=None;b.matrix_world=T@a.matrix_world;s.collection.objects.link(b);dist[a.name]=b
# Add exact S1 front/back legend geometry from verified Gerber preview.
for face in ['F','B']:
 a=bpy.data.objects['S1_'+face+'_PRINT'];b=a.copy();b.data=a.data.copy();b.name='P37_DIST_S1_'+face;s.collection.objects.link(b)
 for v in b.data.vertices:
  if face=='F':v.co=(v.co.x+21,v.co.y,.85)
  else:v.co=(-(v.co.x-21),v.co.y,-.85)
 b.matrix_world=T;b.color=(.95,.97,1,1)
# Explicit electrical endpoints at pin tips and exposed power pads.
E=D['endpoints']
def ep(k,p,net,obj):E[k]={'point':list(p),'net':net,'object':obj.name if hasattr(obj,'name') else obj,'kind':'component_terminal'}
def pin(k,name,net,top):
 o=dist[name];p=o.matrix_world.translation.copy();p.z+=o.dimensions.z/2*(1 if top else -1);ep(k,p,net,o)
for ch in range(12):
 pin(f'D_IN_{ch}',f'P36_IN_PWM{ch}',f'PWM_L{ch}',False)
 for role,net in [('S',f'PWM_L{ch}'),('V','LEG_VBUS'),('G','GND')]:pin(f'D_{ch}_{role}',f'P36_OUT_{ch:02}_{role}_PIN',net,True)
 D['internal_links'].append([f'D_IN_{ch}',f'D_{ch}_S',f'PWM_L{ch}'])
 D['internal_links'] += [['D_FUSED',f'D_{ch}_V','LEG_VBUS'],['D_MINUS',f'D_{ch}_G','GND']]
 E[f'L{ch}_POWER']['net']='LEG_VBUS'
for i,name in enumerate(['P36_IN_G-1','P36_IN_G1']):
 pin('D_REF'+str(i),name,'GND',False);D['internal_links'].append(['D_MINUS','D_REF'+str(i),'GND'])
ep('D_PLUS',(-3.5,303,22.85),'BAT_FUSED',dist['P36_PCB'])
ep('D_MINUS',(3.5,303,22.85),'GND',dist['P36_PCB'])
ep('D_FUSED',(4.95,255.8,22.85),'LEG_VBUS',dist['P36_PCB'])
D['conditional_internal_links'].append({'source':'D_PLUS','destination':'D_FUSED','condition':'D_F1_INTACT','kind':'onboard_fuse'})
def wire(id,a,b,net,mid):
 D['harnesses'].append(dict(id=id,source=a,destination=b,net=net,status='DESIGN_CONNECTION',curve_object='P37_W_'+id,path_mm=[E[a]['point']]+mid+[E[b]['point']]))
# Power travels on dedicated conductors; signal input is below the PCB.
wire('distributor_power','MAIN_DIST','D_PLUS','BAT_FUSED',[[-190,35,5],[-190,445,5],[-3.5,445,5],[-3.5,303,36]])
wire('distributor_return','COMMON_GND','D_MINUS','GND',[[-200,-30,5],[-200,455,5],[3.5,455,5],[3.5,303,36]])
for ch in range(12):
 a=E[f'PWM_L{ch}_SOURCE']['point'];b=E[f'D_IN_{ch}']['point'];lane=-34+ch*3
 wire(f'pwm_input_{ch}',f'PWM_L{ch}_SOURCE',f'D_IN_{ch}',f'PWM_L{ch}',[[a[0],120+ch*.8,5],[lane,155+ch*.8,5],[lane,b[1],5],[b[0],b[1],5]])
 side=-1 if ch<6 else 1
 for j,(role,target,net) in enumerate([('G',f'L{ch}_GND','GND'),('V',f'L{ch}_POWER','LEG_VBUS'),('S',f'PWM_L{ch}_SERVO',f'PWM_L{ch}')]):
  a=E[f'D_{ch}_{role}']['point'];b=E[target]['point'];z=35+j*2
  wire(f'servo_{ch}_{role}',f'D_{ch}_{role}',target,net,[[a[0],a[1],z],[side*(32+j*3),a[1],z],[side*(195+j*3),b[1]-9+j*3,z],[b[0],b[1]-9+j*3,z]])
for i in range(2):wire(f'distributor_reference_{i}','LEGS_GND','D_REF'+str(i),'GND',[[45+i*3,112,5],[45+i*3,295,5],[E['D_REF'+str(i)]['point'][0],295,5]])
# Semantic coloring; do not trust obsolete source material assignments.
C={'BAT_RAW':(.9,.025,.025,1),'BAT_FUSED':(.9,.055,.025,1),'LEG_VBUS':(1,.65,.015,1),'GND':(.16,.20,.25,1),'ARM_SUPPLY':(.75,.35,.02,1),'LOGIC5V':(.6,.08,.8,1),'5V_LOGIC':(.6,.08,.8,1),'3V3_LOGIC':(.95,.35,.04,1),'SDA':(.01,.65,.8,1),'SCL':(.06,.2,.85,1)}
def color(net):return (.015,.58,.43,1) if net.startswith('PWM') else C.get(net,(.5,.06,.7,1))
for h in D['harnesses']:
 h['curve_object']='P37_W_'+h['id'];cu=bpy.data.curves.new(h['curve_object'],'CURVE');cu.dimensions='3D';cu.resolution_u=1;cu.bevel_depth=.40 if h['net'].startswith('PWM') else .65;cu.bevel_resolution=2
 p=cu.splines.new('POLY');p.points.add(len(h['path_mm'])-1)
 for v,pt in zip(p.points,h['path_mm']):v.co=(*pt,1)
 o=bpy.data.objects.new(h['curve_object'],cu);s.collection.objects.link(o);o.color=color(h['net']);o['net']=h['net'];o['source']=h['source'];o['destination']=h['destination']
for k,v in E.items():
 o=dist.get(v['object']) or s.objects.get(v['object']) or find(v['object'])
 if o and o.type=='MESH' and k not in ['D_PLUS','D_MINUS','D_FUSED']:o.color=color(v['net']);v['object']=o.name
# Matching terminal swatches on distributor.
for name,o in dist.items():
 if '_COLOR' in name:
  o.color=color('GND' if name.endswith('_G_COLOR') or '_COLOR_G' in name else 'LEG_VBUS' if name.endswith('_V_COLOR') else 'PWM')
# Readable hierarchy and exact status labels.
for o in s.objects:
 if o.type=='FONT':o.color=(.025,.035,.055,1)
for name in ['P21_P14_TX_LEG_FEED','P21_P14_TX_SERVO_BOUNDARY']:drop(find(name))
txtset('P21_P14_TX_TITLE','タチコマ  カラーハーネス図  P37',(0,505,0),16)
txtset('P21_P14_TX_SUB','P36 S1 分配基板 / 脚12軸 + 腕4軸 / 平面配線図・実装位置ではない',(0,483,0),6)
txtset('P21_P14_TX_NET_LEG','赤：分配基板の手前  黄：基板F1後の脚電源  緑：各軸PWM',(0,-343,0),6)
txtset('P21_P14_TX_NET_CONTROL','灰：共通GND  橙：腕電源  紫：5V  水色：SDA  青：SCL',(0,-356,0),6)
txtset('P21_P14_TX_NOTES','点のない交差は非接続。経路は図示用で製作線長ではない。\n基板F1は15A候補。定格・実物端子・通電は未検証。',(0,-378,0),5)
txtset('P21_DECISIONS','XT60 → 主ヒューズ → P36のF1 → 脚12個\n                               → UBEC → 腕4個\n                               → MINI560 → ESP32',(-285,-160,0),5)
txtset('P21_DEFERRED','電池監視 / 停止時の扱い：保留',(-270,-230,0),5)
def text(name,body,pos,size):
 c=bpy.data.curves.new(name,'FONT');c.body=body;c.font=font;c.size=size;c.align_x='CENTER';o=bpy.data.objects.new(name,c);s.collection.objects.link(o);o.location=pos;o.color=(.025,.04,.065,1);return o
text('P37_DIST_LABEL','P36 S1 / 脚用電源・PWM分配',(0,341,0),7)
text('P37_DIST_NOTE','表：サーボ3ピン ×12\n裏：PWM12本 + GND2本',(0,320,0),5)
text('P37_INPUT_LABEL','PWM 0–11 + 基準GND\n脚用PCA9685のV+は未接続',(0,185,0),5)
# Render colors from object data for consistent source and new components.
s.render.engine='BLENDER_WORKBENCH';sh=s.display.shading;sh.light='STUDIO';sh.color_type='OBJECT';sh.show_shadows=False;sh.show_cavity=True;sh.cavity_type='WORLD';sh.background_type='WORLD'
s.world=bpy.data.worlds.new('P37_WORLD');s.world.color=(.82,.84,.88)
s.view_settings.view_transform='Standard';s.render.resolution_x=2200;s.render.resolution_y=2600;s.render.resolution_percentage=100;s.render.image_settings.file_format='PNG'
s.camera.location=(0,55,1500);s.camera.rotation_euler=(0,0,0);s.camera.data.type='ORTHO';s.camera.data.ortho_scale=1060
s.render.filepath=str(OUT/'P37_COLOR_HARNESS.png')
for screen in bpy.data.screens:
 for area in screen.areas:
  if area.type=='VIEW_3D':
   sp=area.spaces.active;sp.shading.type='SOLID';sp.shading.color_type='OBJECT';sp.shading.light='STUDIO';sp.shading.background_type='WORLD';sp.overlay.show_overlays=False;sp.region_3d.view_perspective='CAMERA'
D['configuration']='P37_COLOR_HARNESS_P36_S1';D['source_model']='P21_CURRENT_WIRING + P36_DISTRIBUTOR_NATIVE + S1 legend';D['manufacturing_release']=False
D['components'].append({'id':'P36_S1','bom_config':'TACHIKOMA-LEG-DISTRIBUTOR-P36-S1-20261108-R1','quantity':1})
D['checks']={};D['representation']='Flat wiring topology, paths not manufacturing lengths'
(OUT/'P37_HARNESS_NETLIST.json').write_text(json.dumps(D,ensure_ascii=False,indent=2))
print(json.dumps({'scene':s.name,'objects':len(s.objects),'wires':len(D['harnesses']),'render':s.render.filepath},ensure_ascii=False))
