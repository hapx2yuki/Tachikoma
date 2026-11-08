import bpy,json,csv,hashlib
from pathlib import Path
from mathutils import Vector
O=Path('source-root/P37_COLOR_HARNESS')
s=bpy.data.scenes['P37_COLOR_HARNESS'];D=json.loads((O/'P37_HARNESS_NETLIST.json').read_text());E=D['endpoints']
# Move bank labels away from adjacent servo connector.
for ch in range(12):
 ob=s.objects[f'P37_P14_TX_L{ch}'];ob.location=((-330 if ch<6 else 330),145+55*(ch%6),0);ob.data.size=5.5
# Persist all referenced object identities in this scene.
for k,e in E.items():
 if not s.objects.get(e['object']):
  n=e['object'].replace('P21_','P37_',1);o=s.objects.get(n) or next((o for o in s.objects if o.name.startswith(n+'.')),None)
  if o:e['object']=o.name
assert all(s.objects.get(e['object']) for e in E.values()),[k for k,e in E.items() if not s.objects.get(e['object'])]
errors=[]
for h in D['harnesses']:
 o=s.objects[h['curve_object']];p=o.data.splines[0].points
 for point,k in [(p[0],h['source']),(p[-1],h['destination'])]:
  err=(o.matrix_world@Vector(point.co[:3])-Vector(E[k]['point'])).length
  if err>1e-4:errors.append((h['id'],k,err))
assert not errors,errors
# Confirm every leg wire meets the moved connector and distributor pin tip.
contact=[]
for ch in range(12):
 for k in [f'L{ch}_POWER',f'L{ch}_GND',f'PWM_L{ch}_SERVO']:
  o=s.objects[E[k]['object']];err=(o.matrix_world.translation-Vector(E[k]['point'])).length
  assert err<1e-4,(k,err);contact.append(k)
 for role in ['S','V','G']:
  k=f'D_{ch}_{role}';o=s.objects[E[k]['object']];pt=o.matrix_world.translation+Vector((0,0,o.dimensions.z/2));assert (pt-Vector(E[k]['point'])).length<1e-4
# Graph of explicit conductors plus switchable fuse contacts.
def graph(exclude=()):
 g={k:set() for k in E}
 def edge(a,b):g[a].add(b);g[b].add(a)
 for h in D['harnesses']:
  if h.get('condition') not in exclude:edge(h['source'],h['destination'])
 for a,b,*_ in D['internal_links']:edge(a,b)
 for h in D['conditional_internal_links']:
  if h['condition'] not in exclude:edge(h['source'],h['destination'])
 return g
def reach(g,start):
 found={start};todo=[start]
 while todo:
  for k in g[todo.pop()]-found:found.add(k);todo.append(k)
 return found
allg=graph();pwm=[]
for ch in range(12):
 r=reach(allg,f'PWM_L{ch}_SOURCE');assert f'PWM_L{ch}_SERVO' in r
 assert not any(f'PWM_L{other}_SERVO' in r for other in range(12) if other!=ch)
 assert 'COMMON_GND' not in r and 'BAT+' not in r
 pwm.append({'channel':ch,'pins':sorted(r)})
for state in ['FUSE_INTACT','D_F1_INTACT','XT60_MATED']:
 r=reach(graph([state]),'BAT+');assert all(f'L{i}_POWER' not in r for i in range(12))
r=reach(allg,'BAT+');assert all(f'L{i}_POWER' in r for i in range(12));assert 'UBEC_OUT+' not in r and 'DC_OUT+' not in r
r=reach(allg,'COMMON_GND');assert all(f'L{i}_GND' in r for i in range(12))
checks={'curve_count':len(D['harnesses']),'curve_endpoint_mismatches':errors,'leg_connector_contacts':len(contact),'PWM_channels_independent':pwm,'XT60_main_fuse_and_F1_break_leg_power':True,'UBEC_output_not_shorted_to_battery':True,'common_GND_present':True,'physical_tests':'NOT_RUN','board_fuse':'15A candidate, not validated','body_assembly_changed':False}
D['checks']=checks
(O/'P37_HARNESS_NETLIST.json').write_text(json.dumps(D,ensure_ascii=False,indent=2))
with (O/'P37_HARNESS_TABLE.csv').open('w') as f:
 w=csv.writer(f);w.writerow(['id','source','destination','net','condition'])
 for h in D['harnesses']:w.writerow([h['id'],h['source'],h['destination'],h['net'],h.get('condition','')])
s['configuration']=D['configuration'];s['evidence']='Wiring graph and endpoints verified; not a powered test'
s['scope']='Existing detailed flat harness updated with P36 S1; original body CAD unchanged'
# Save a leg/PWM close-up, then restore the full-color overall view.
cam=s.camera;cam.location=(0,262,1500);cam.data.ortho_scale=790
s.render.resolution_x=2400;s.render.resolution_y=1300;s.render.filepath=str(O/'P37_LEG_DISTRIBUTOR.png')
bpy.ops.render.render(write_still=True,scene=s.name)
cam.location=(0,55,1500);cam.data.ortho_scale=1060;s.render.resolution_x=2200;s.render.resolution_y=2600;s.render.filepath=str(O/'P37_COLOR_HARNESS.png')
bpy.ops.render.render(write_still=True,scene=s.name)
# Show the saved full diagram with colors in the live user viewport.
for screen in bpy.data.screens:
 for a in screen.areas:
  if a.type=='VIEW_3D':
   a.spaces.active.shading.type='SOLID';a.spaces.active.shading.color_type='OBJECT';a.spaces.active.shading.light='FLAT';a.spaces.active.region_3d.view_perspective='CAMERA'
bpy.context.window.scene=s
text=bpy.data.texts.new('P37_NETLIST');text.write(json.dumps(D,ensure_ascii=False,indent=2))
p=str(O/'P37_COLOR_HARNESS.blend');bpy.data.libraries.write(p,{s,text},fake_user=True,compress=True)
# Fresh read-back of saved scene and curve metadata, without replacing the user session.
with bpy.data.libraries.load(p,link=False) as (a,b):b.scenes=['P37_COLOR_HARNESS']
r=b.scenes[0];savedw=[o for o in r.objects if o.type=='CURVE' and o.get('source')]
assert len(savedw)==len(D['harnesses']);assert r['configuration']==D['configuration']
checks['saved_scene_readback_wire_count']=len(savedw);checks['saved_blend_sha256']=hashlib.sha256(Path(p).read_bytes()).hexdigest()
bpy.data.scenes.remove(r)
(O/'CHECKS.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2))
(O/'README.md').write_text('''# P37 カラーハーネス図\n\n依頼：Blenderで作成したハーネス図の更新・カラー表示。\nP21の詳細電子部品と接続を引き継ぎ、P36 S1を追加した独立平面図。本体内の電池・基板・機構配置は変更していない。\n\n- 脚用PCA9685のPWM0–11 → 分配基板裏面 → 表面の各サーボS。V+は接続しない。\n- 電池 → XT60 → 既存主ヒューズ → 分配基板F1 → 脚12軸。専用GND電力帰路と基準GNDを表示。\n- 腕UBEC、MINI560、ESP32、腕PCA9685の既存経路は維持。腕4軸割当は提案のまま。\n- 基板F1の15Aは候補。主ヒューズ・線径・負荷適合・UBEC設定・実機確認は未確定。\n- 電池監視・停止時扱いは保留。\n- 赤=分配基板F1前、黄色=F1後の脚電源、緑=個別PWM、灰=GND。図の色は実物線色ではない。\n- 98配線の端点、PWM12系統独立、XT60/主ヒューズ/F1遮断時の脚給電切断を明示グラフで検査。実物導通・電流容量は検証していない。\n- 原資料のPCA/サーボコネクタ等には参考形状・未確認向きが含まれる。実物ピン配列は組立前に照合する。\n\n作成・描画・保存・読み戻しはライブBlenderMCP。画像とネイティブモデルを同フォルダへ保存。\n''')
print(json.dumps({'saved':p,'wires':len(savedw),'independent_PWM':len(pwm),'endpoint_errors':len(errors),'live_scene':bpy.context.scene.name},ensure_ascii=False))
