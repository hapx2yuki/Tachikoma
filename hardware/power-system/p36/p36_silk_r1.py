# Execute inside BlenderMCP. Change legend layers only.
import bpy,json,math,re,os,shutil,hashlib,zipfile
ROOT='source-root/electronics-stack-study-20261108'
BASE=ROOT+'/P36_PCB_FAB_PREP';OUT=ROOT+'/P36_SILK_R1';FAB=OUT+'/gerber';os.makedirs(FAB,exist_ok=True)
for name in os.listdir(BASE+'/gerber'):shutil.copy2(BASE+'/gerber/'+name,FAB+'/'+name)
s=open(ROOT+'/p36_route_prepare.py').read();exec(s[s.index('def point_seg'):s.index('def features')])
s=open(ROOT+'/p36_fabricate.py').read();exec(s[s.index('def coord'):s.index("for layer,ext,func in [('F','GTL'")]);exec(s[s.index('font={'):s.index("label('+',-3.5")]);exec(s[s.index('def read_gerber'):s.index('readback={};')])
shift=(12.7,31.25)
font.update({'B':[(0,0,0,1),(0,1,.5,1),(.5,1,.6,.75),(.6,.75,.4,.5),(.4,.5,0,.5),(.4,.5,.6,.25),(.6,.25,.5,0),(.5,0,0,0)],'D':[(0,0,0,1),(0,1,.4,1),(.4,1,.6,.75),(.6,.75,.6,.25),(.6,.25,.4,0),(.4,0,0,0)],'U':[(0,1,0,.1),(0,.1,.6,.1),(.6,.1,.6,1)],'E':[(0,0,0,1),(0,1,.6,1),(0,.5,.5,.5),(0,0,.6,0)],'J':[(0,1,.6,1),(.45,1,.45,0),(.45,0,0,0),(0,0,0,.2)]})
sp=[(.6,.9),(.45,1),(.15,1),(0,.85),(0,.65),(.15,.5),(.45,.5),(.6,.35),(.6,.15),(.45,0),(.15,0),(0,.1)]
font['S']=[(*a,*b) for a,b in zip(sp,sp[1:])]
# Front labels: visible outside header bodies and away from exposed solder pads.
label('B+',-3.5,30.2,1);label('B-',3.5,30.2,1)
label('P36 S1',0,-30,1);label('F1',0,-26.7,1.1);label('FUSE',0,-28.3,1)
for ch in range(12):label(str(ch),-11.1 if ch<6 else 11.1,-15.24+6.35*(ch%6),1)
for side in [-1,1]:
 for txt,x in [('G',8.89),('V+',6.35),('S',3.81)]:label(txt,side*x,18.6,1)
label('S PWM',0,20,1)
# Back labels are constructed in back-view coordinates; label() mirrors into fabrication XY.
label('PWM IN',0,12.9,1,'B');label('GND',0,9.7,1,'B');label('P36 S1',0,-30,1,'B')
label('J IN MAP',0,-11.3,1,'B')
# Printed pin map uses actual back-view left/right order, power end kept upward.
maprows=[('G','G'),('11','5'),('10','4'),('9','3'),('8','2'),('7','1'),('6','0')]
for i,(left,right) in enumerate(maprows):
 label(left,-1.25,-15-2*i,1,'B');label(right,1.25,-15-2*i,1,'B')
# A frame distinguishes the legend table from a row of physical connector holes.
for a,b in [((-2.65,-13.5),(2.65,-13.5)),((2.65,-13.5),(2.65,-28)),((2.65,-28),(-2.65,-28)),((-2.65,-28),(-2.65,-13.5))]:silk['B'].append(dict(a=a,b=b,w=.15))
mask={'F':read_gerber(BASE+'/gerber/P36.GTS'),'B':read_gerber(BASE+'/gerber/P36.GBS')}
# Mechanical clearance for the four NPTH openings as well.
d=json.load(open(BASE+'/route_candidate.json'))
for h in d['holes']:
 if not h['plated']:
  for layer in mask:mask[layer].append(dict(xy=h['xy'],shape='C',diam=h['diam']))
checks={};errs=[]
for layer in ['F','B']:
 mingap=min(gap(t,p) for t in silk[layer] for p in mask[layer]);checks[layer+'_minimum_legend_to_opening_mm']=mingap
 for t in silk[layer]:
  for p in mask[layer]:
   if gap(t,p)<.15-1e-6:errs.append([layer,t,p,gap(t,p)])
  edge=min(12.7-max(abs(t['a'][0]),abs(t['b'][0])),31.25-max(abs(t['a'][1]),abs(t['b'][1])))-.075
  if edge<.15:errs.append(['edge',layer,t,edge])
assert not errs,errs[:5]
for layer,ext,func in [('F','GTO','Legend,Top'),('B','GBO','Legend,Bot')]:
 export_gerber('P36.'+ext,silk[layer],func)
 parsed=read_gerber(FAB+'/P36.'+ext);assert len(parsed)==len(silk[layer])
 assert all(math.dist(a['a'],b['a'])<1e-6 and math.dist(a['b'],b['b'])<1e-6 and a['w']==b['w'] for a,b in zip(parsed,silk[layer]))
 checks[layer+'_stroke_count']=len(parsed)
checks['unchanged_manufacturing_layers']=[n for n in os.listdir(FAB) if n not in ['P36.GTO','P36.GBO'] and open(FAB+'/'+n,'rb').read()==open(BASE+'/gerber/'+n,'rb').read()]
assert len(checks['unchanged_manufacturing_layers'])==7
checks.update(font_height_mm=1.0,stroke_width_mm=.15,back_map_rows_top_to_bottom=maprows,coordinate_datum='Back view: power-pad end up; back print mirrored in Gerber',silk_only_revision=True)
json.dump(checks,open(OUT+'/SILK_CHECKS.json','w'),indent=2)
print(json.dumps(checks))
