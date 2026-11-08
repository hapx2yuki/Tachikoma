"""Executed only inside BlenderMCP. Standard Gerber/Excellon export + readback."""
import bpy,json,math,os,re,itertools,csv,hashlib,zipfile
ROOT='source-root/electronics-stack-study-20261108'
OUT=ROOT+'/P36_PCB_FAB_PREP'; FAB=OUT+'/gerber'; os.makedirs(FAB,exist_ok=True)
d=json.load(open(OUT+'/route_candidate.json')); pads=d['pads'];tracks=d['tracks'];holes=d['holes']
# Reuse analytic primitives, without running the prior design generator.
src=open(ROOT+'/p36_route_prepare.py').read();exec(src[src.index('def point_seg'):src.index('errors=[];minimum=')])
shift=(12.7,31.25)
def coord(p):return 'X%dY%d'%(round((p[0]+shift[0])*1e6),round((p[1]+shift[1])*1e6))
def aperture(p):
    if 'a' in p:return ('C',round(p['w'],6))
    if p['shape']=='C':return ('C',round(p['diam'],6))
    return ('R',*p['size'])
def export_gerber(name,ff,function):
    ap={}
    for p in ff:
        a=aperture(p)
        if a not in ap:ap[a]=10+len(ap)
    ls=['G04 P36 prototype - millimetres - native XY shifted by 12.7,31.25*','%FSLAX46Y46*%','%MOMM*%',f'%TF.FileFunction,{function}*%','%LPD*%']
    for a,n in ap.items():ls.append('%%ADD%d%s,%s*%%'%(n,a[0],'X'.join('%.6f'%v for v in a[1:])))
    ls.append('G01*')
    for p in ff:
        ls.append('D%d*'%ap[aperture(p)])
        if 'a' in p:ls += [coord(p['a'])+'D02*',coord(p['b'])+'D01*']
        else:ls.append(coord(p['xy'])+'D03*')
    ls.append('M02*');open(FAB+'/'+name,'w').write('\n'.join(ls)+'\n')
for layer,ext,func in [('F','GTL','Copper,L1,Top'),('B','GBL','Copper,L2,Bot')]:
    export_gerber('P36.'+ext,features(layer),func)
# 1:1 mask openings; vias tented on bottom. Power pads exposed only on top.
mask={}
for layer,ext,func in [('F','GTS','Soldermask,Top'),('B','GBS','Soldermask,Bot')]:
    mask[layer]=[p for p in pads if not p['id'].startswith('VIA_') and (layer=='F' or not p['id'].startswith('J_POWER.'))]
    export_gerber('P36.'+ext,mask[layer],func)
# Outline stroke is the nominal routing centreline; 0.05 mm is drawing width, not board allowance.
corner=[(-12.7,-31.25),(12.7,-31.25),(12.7,31.25),(-12.7,31.25),(-12.7,-31.25)]
edge=[dict(a=a,b=b,w=.05) for a,b in zip(corner,corner[1:])]
export_gerber('P36.GKO',edge,'Profile,NP')
# Minimal vector silkscreen font, every segment checked against mask and board edge.
font={
'0':[(0,0,0,1),(0,1,.6,1),(.6,1,.6,0),(.6,0,0,0)],
'1':[(.3,0,.3,1),(.1,.8,.3,1)],'2':[(0,1,.6,1),(.6,1,.6,.5),(.6,.5,0,.5),(0,.5,0,0),(0,0,.6,0)],
'3':[(0,1,.6,1),(.6,1,.6,0),(0,.5,.6,.5),(0,0,.6,0)],
'4':[(0,1,0,.5),(0,.5,.6,.5),(.6,1,.6,0)],'5':[(.6,1,0,1),(0,1,0,.5),(0,.5,.6,.5),(.6,.5,.6,0),(.6,0,0,0)],
'6':[(.6,1,0,1),(0,1,0,0),(0,0,.6,0),(.6,0,.6,.5),(.6,.5,0,.5)],
'7':[(0,1,.6,1),(.6,1,.2,0)],'8':[(0,0,0,1),(0,1,.6,1),(.6,1,.6,0),(.6,0,0,0),(0,.5,.6,.5)],
'9':[(.6,0,.6,1),(.6,1,0,1),(0,1,0,.5),(0,.5,.6,.5)],
'P':[(0,0,0,1),(0,1,.6,1),(.6,1,.6,.5),(.6,.5,0,.5)],'W':[(0,1,.1,0),(.1,0,.3,.5),(.3,.5,.5,0),(.5,0,.6,1)],
'M':[(0,0,0,1),(0,1,.3,.5),(.3,.5,.6,1),(.6,1,.6,0)],'I':[(.3,0,.3,1),(0,1,.6,1),(0,0,.6,0)],
'N':[(0,0,0,1),(0,1,.6,0),(.6,0,.6,1)],'G':[(.6,1,0,1),(0,1,0,0),(0,0,.6,0),(.6,0,.6,.5),(.6,.5,.3,.5)],
'V':[(0,1,.3,0),(.3,0,.6,1)],'S':[(.6,1,0,1),(0,1,0,.5),(0,.5,.6,.5),(.6,.5,.6,0),(.6,0,0,0)],
'A':[(0,0,.3,1),(.3,1,.6,0),(.15,.5,.45,.5)],'F':[(0,0,0,1),(0,1,.6,1),(0,.5,.5,.5)],
'+':[(.3,0,.3,1),(0,.5,.6,.5)],'-':[(0,.5,.6,.5)],' ':[]}
silk={'F':[],'B':[]}
def label(s,x,y,h=1,layer='F'):
    for i,ch in enumerate(s):
        for a,b,c,e in font[ch]:
            ax=x+(i*.85+a-(len(s)*.85-.25)/2)*h; bx=x+(i*.85+c-(len(s)*.85-.25)/2)*h
            if layer=='B':ax,bx=-ax,-bx
            silk[layer].append(dict(a=[ax,y+(b-.5)*h],b=[bx,y+(e-.5)*h],w=.15))
label('+',-3.5,30.2);label('-',3.5,30.2);label('P36',0,-30,1.1);label('15A',0,-26.7,1.2)
for ch in range(12):label(str(ch),-10.9 if ch<6 else 10.9,-15.24+6.35*(ch%6),.9)
for side in [-1,1]:
    for ch,x in [('G',8.89),('V',6.35),('S',3.81)]:label(ch,side*x,18.4,.9)
label('PWM IN',0,12.9,1,'B');label('P36',0,-30,1.1,'B')
for layer,ext,func in [('F','GTO','Legend,Top'),('B','GBO','Legend,Bot')]:
    for t in silk[layer]:
        assert min(gap(t,p) for p in mask[layer])>=.15-1e-6,('silk_mask',layer,t)
        assert min(12.7-max(abs(t['a'][0]),abs(t['b'][0])),31.25-max(abs(t['a'][1]),abs(t['b'][1])))-.075>=.15
    export_gerber('P36.'+ext,silk[layer],func)
for plated,name in [(True,'P36-PTH.drl'),(False,'P36-NPTH.drl')]:
    hh=[h for h in holes if h['plated']==plated];diam=sorted(set(h['diam'] for h in hh))
    ls=['M48',';P36 finished hole sizes in mm; separate plated/nonplated files','METRIC,LZ']
    ls += ['T%02dC%.6f'%(i+1,r) for i,r in enumerate(diam)];ls+=['%','G90','G05']
    for i,r in enumerate(diam):
        ls.append('T%02d'%(i+1))
        for h in hh:
            if h['diam']==r:ls.append('X%.6fY%.6f'%(h['xy'][0]+shift[0],h['xy'][1]+shift[1]))
    ls.append('M30');open(FAB+'/'+name,'w').write('\n'.join(ls)+'\n')

# Parse saved fabrication files independently: aperture modal state, decimal/implicit coords.
def read_gerber(path):
    aps={};cur=None;pos=None;result=[]
    for s in open(path):
        s=s.strip();m=re.fullmatch(r'%ADD(\d+)(C|R),([\d.X]+)\*%',s)
        if m:aps[int(m[1])]=(m[2],list(map(float,m[3].split('X'))));continue
        m=re.fullmatch(r'D(\d+)\*',s)
        if m:cur=int(m[1]);continue
        m=re.fullmatch(r'X(-?\d+)Y(-?\d+)D(0[123])\*',s)
        if not m:continue
        xy=[int(m[1])/1e6-shift[0],int(m[2])/1e6-shift[1]];typ,vals=aps[cur]
        if m[3]=='01':
            assert pos is not None and typ=='C';result.append(dict(a=pos,b=xy,w=vals[0]))
        if m[3]=='03':result.append(dict(xy=xy,shape=typ,diam=vals[0],size=vals if typ=='R' else None))
        pos=xy
    return result
readback={};checks={};all_features=[]
for layer,ext in [('F','GTL'),('B','GBL')]:
    rr=read_gerber(FAB+'/P36.'+ext);ff=features(layer);assert len(rr)==len(ff)
    for p,q in zip(rr,ff):
        assert aperture(p)==aperture(q)
        keys=['a','b'] if 'a' in p else ['xy']
        assert all(math.dist(p[k],q[k])<2e-6 for k in keys)
        p.update(id=q['id'],net=q['net'],layer=layer,drill=q.get('drill',0))
    readback[layer]=rr;all_features+=rr
    checks[layer+'_copper_primitive_count']=len(rr)
# Same-net continuity graph, and different-net separation on parsed copper.
parent=list(range(len(all_features)))
def root(i):
    while parent[i]!=i:parent[i]=parent[parent[i]];i=parent[i]
    return i
def union(i,j):parent[root(j)]=root(i)
mingap=99
for i,a in enumerate(all_features):
    for j in range(i):
        b=all_features[j]
        if a['layer']==b['layer']:
            g=gap(a,b)
            if a['net']!=b['net']:mingap=min(mingap,g);assert g>=.3-2e-6,('short/clearance',a,b,g)
            elif g<=1e-6:union(i,j)
        elif a['id']==b['id'] and a.get('drill',0)>0:union(i,j)
connectivity={}
for net in sorted(set(p['net'] for p in all_features)):
    idx=[i for i,p in enumerate(all_features) if p['net']==net];components=set(root(i) for i in idx)
    assert len(components)==1,(net,len(components));connectivity[net]={'components':1,'features':len(idx)}
checks['nets_connected']=len(connectivity);checks['different_nets_short']=False;checks['fuse_bypassed']=False
checks['minimum_copper_gap_mm']=mingap
# Drill readback and ring/hole separation.
rholes=[]
for plated,name in [(True,'P36-PTH.drl'),(False,'P36-NPTH.drl')]:
    ts={};cur=None
    for s in open(FAB+'/'+name):
        s=s.strip();m=re.fullmatch(r'T(\d+)C([\d.]+)',s)
        if m:ts[int(m[1])]=float(m[2]);continue
        m=re.fullmatch(r'T(\d+)',s)
        if m:cur=int(m[1]);continue
        m=re.fullmatch(r'X([\d.]+)Y([\d.]+)',s)
        if m:rholes.append(dict(xy=[float(m[1])-shift[0],float(m[2])-shift[1]],diam=ts[cur],plated=plated))
assert len(rholes)==len(holes)
for h in holes:assert sum(q['plated']==h['plated'] and abs(q['diam']-h['diam'])<1e-6 and math.dist(q['xy'],h['xy'])<2e-6 for q in rholes)==1
ring=min((p['diam']-p['drill'])/2 for p in pads if p['drill']);assert ring>=.25
holegap=min(math.dist(a['xy'],b['xy'])-(a['diam']+b['diam'])/2 for a,b in itertools.combinations(rholes,2));assert holegap>=.3
checks.update(holes=len(rholes),plated_holes=sum(h['plated'] for h in rholes),nonplated_holes=sum(not h['plated'] for h in rholes),min_annular_ring_mm=ring,min_hole_web_mm=holegap)
# Contract: existing terminal footprint centres, fuse centre and fixing holes exactly preserved.
base=json.load(open(d['source_terminal_model']));lookup={p['id']:p for p in pads};deltas=[]
for p in base['terminals']:
    if p['id'] in ['F1.IN','F1.OUT']:
        ps=[q for q in pads if q['id'].startswith(p['id']+'.')];xy=[sum(q['xy'][k] for q in ps)/len(ps) for k in range(2)]
    else:xy=lookup[p['id']]['xy']
    deltas.append(math.dist(xy,p['xyz'][:2]))
assert max(deltas)<1e-6
assert base['pcb_size_mm']==[25.4,62.5,1.6] if 'pcb_size_mm' in base else True
checks.update(terminal_xy_max_change_mm=max(deltas),terminal_count=len(deltas),terminal_Z_unchanged='P35 component meshes retained; fabrication file defines XY only')
for layer,ext in [('F','GTS'),('B','GBS')]:
    r=read_gerber(FAB+'/P36.'+ext);assert len(r)==len(mask[layer]);checks[layer+'_mask_openings']=len(r)
outline_read=read_gerber(FAB+'/P36.GKO');assert len(outline_read)==4
assert all(math.dist(p['b'],outline_read[(i+1)%4]['a'])<1e-6 for i,p in enumerate(outline_read))
checks['outline_mm']=[max(p['a'][k] for p in outline_read)-min(p['a'][k] for p in outline_read) for k in range(2)]
assert checks['outline_mm']==[25.4,62.5]
checks['independent_external_DFM']='NOT_RUN';checks['KiCad_DRC']='NOT_RUN - custom primitive/network checks only'
json.dump(dict(checks=checks,connectivity=connectivity),open(OUT+'/FAB_READBACK_CHECKS.json','w'),indent=2)
json.dump(dict(copper=readback,holes=rholes,outline=outline_read),open(OUT+'/FAB_READBACK_GEOMETRY.json','w'),indent=2)
with open(OUT+'/NETLIST.csv','w') as f:
    w=csv.writer(f);w.writerow(['terminal','net','x_native_mm','y_native_mm','drill_finished_mm','type'])
    for p in pads:w.writerow([p['id'],p['net'],*p['xy'],p['drill'],'via' if p['id'].startswith('VIA_') else 'terminal'])
with open(OUT+'/BOM.csv','w') as f:
    w=csv.writer(f);w.writerow(['Reference','Quantity','Manufacturer','MPN_or_spec','Mount_side','Status'])
    w.writerow(['PCB',1,'JLCPCB','P36 62.5x25.4x1.6mm 2-layer 4.5oz finished','NA','Prototype candidate; DFM and thermal qualification pending'])
    w.writerow(['F1 holder',1,'Keystone','3568','Front','Official footprint checked; physical sample fit untested'])
    w.writerow(['F1 insert',1,'Littelfuse','0297015.L 15A 32V MINI 297','Front','Provisional; replaceable; no final protection coordination'])
    w.writerow(['J0-J11',12,'Samtec','HTSW-103-07-L-S 1x3 2.54mm','Front','Footprint-compatible candidate; verify actual servo mating connector'])
    w.writerow(['J_IN',1,'Samtec','HTSW-107-07-L-D 2x7 2.54mm','Back','Footprint-compatible candidate; custom PCA harness required'])
    w.writerow(['J_POWER',2,'NA','Direct-wire 5x8mm exposed solder pad','Front','Wire gauge and strain relief to qualify with harness'])
print(json.dumps(checks,indent=2))
