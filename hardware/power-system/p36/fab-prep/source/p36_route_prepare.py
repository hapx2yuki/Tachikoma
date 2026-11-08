"""P36 route candidate. Execute exclusively inside an actual BlenderMCP call.
No CLI geometry execution. All dimensions in mm, P35 native XY datum.
"""
import bpy, json, math, os, itertools
from mathutils import Vector

OUT='source-root/P36_PCB_FAB_PREP'
os.makedirs(OUT,exist_ok=True)
SRC='source-root/P35_SOLDER_PAD_DISTRIBUTOR/CONNECTION_MODEL.json'
with open(SRC) as f: base=json.load(f)
pads=[]; tracks=[]; holes=[]
def pad(name,net,xy,diam=1.7,drill=1.0,layers=('F','B'),shape='C',size=None):
    p=dict(id=name,net=net,xy=list(xy),diam=diam,drill=drill,layers=list(layers),shape=shape,size=size)
    pads.append(p)
    if drill: holes.append(dict(id=name,xy=list(xy),diam=drill,plated=True,net=net))
    return p
for ch in range(12):
    side=-1 if ch<6 else 1; y=-15.24+6.35*(ch%6)
    for letter,x,net in [('S',3.81,'PWM_L%d'%ch),('V',6.35,'LEG_VBUS'),('G',8.89,'GND')]:
        pad('J%d.%s'%(ch,letter),net,(side*x,y),diam=2.2 if letter in ['V','G'] else 1.7)
for side in [-1,1]:
    for i in range(7):
        ch=i+(0 if side<0 else 6)
        label='PWM%d'%ch if i<6 else 'G%d'%side
        pad('J_IN.'+label,'PWM_L%d'%ch if i<6 else 'GND',(side*1.27,-7.62+2.54*i))
for sign,x,net in [('PLUS',-3.5,'POWER_IN'),('MINUS',3.5,'GND')]:
    pad('J_POWER.'+sign,net,(x,25),drill=0,shape='R',size=[5,8])
    # Parallel top/bottom power access, symmetric via array; manual solder wicking note applies.
    for dx in [-1.5,0,1.5]:
        for y in [22.5,24,25.5,27]:
            pad('VIA_%s_%s_%s'%(sign,dx,y),net,(x+dx,y),diam=1.05,drill=.5)
for x,net in [(-4.95,'POWER_IN'),(4.95,'LEG_VBUS')]:
    for y in [-23.905,-20.495]:pad('F1.%s.%s'%('IN' if x<0 else 'OUT',y),net,(x,y),diam=3.5,drill=1.7)
for x,y,d in base['mount_holes_mm']:holes.append(dict(id='H_%s_%s'%(x,y),xy=[x,y],diam=d,plated=False,net=None))

def route(name,net,layer,points,w):
    for i,(a,b) in enumerate(zip(points,points[1:])):
        if math.dist(a,b)>1e-8:tracks.append(dict(id=name+'.%d'%i,net=net,layer=layer,a=list(a),b=list(b),w=w))
for layer in ['F','B']:
    route('BAT_FEED_'+layer,'POWER_IN',layer,[(-3.5,25),(-5.5,23),(-11.3,23),(-11.3,-20.495),(-4.95,-20.495),(-4.95,-23.905)],1.8)
    route('FUSED_ROOT_'+layer,'LEG_VBUS',layer,[(4.95,-23.905),(4.95,-20.495),(6.35,-18.8),(6.35,-17.45),(-6.35,-17.45)],1.8)
    for x in [-6.35,6.35]:route('SERVO_V_'+str(x)+layer,'LEG_VBUS',layer,[(x,-17.45),(x,16.51)],1.8)
    route('GND_STAR_'+layer,'GND',layer,[(-8.89,19.2),(8.89,19.2)],1.8)
    route('GND_PAD_'+layer,'GND',layer,[(3.5,25),(3.5,19.2)],1.8)
    for x in [-8.89,8.89]:route('SERVO_G_'+str(x)+layer,'GND',layer,[(x,-15.24),(x,19.2)],1.8)
route('GND_REFERENCE','GND','F',[(-1.27,7.62),(0,9),(1.27,7.62)],.5)
route('GND_REFERENCE_STAR','GND','F',[(0,9),(0,19.2)],.5)

# Monotone escape by alternating copper faces. Routes remain inside the power rows.
signals={
0:[(1.27,-7.62),(2.4,-7.62),(2.4,-12),(3.81,-13.41),(3.81,-15.24)],
1:[(1.27,-5.08),(2.7,-5.08),(2.7,-8.89),(3.81,-8.89)],
2:[(1.27,-2.54),(3.81,-2.54)],
3:[(1.27,0),(2.7,0),(2.7,3.81),(3.81,3.81)],
4:[(1.27,2.54),(2.4,2.54),(2.4,3.81),(2.7,5.08),(2.7,8.7),(3.81,9.81),(3.81,10.16)],
5:[(1.27,5.08),(2.7,5.08),(2.7,8.6),(2.4,9.2),(2.4,12.5),(3.81,13.91),(3.81,16.51)]}
for side in [-1,1]:
    for i,pts in signals.items():
        ch=i+(0 if side<0 else 6)
        route('PWM_%02d'%ch,'PWM_L%d'%ch,'F' if i%2==0 else 'B',[(side*x,y) for x,y in pts],.35)

# Independent primitive separation calculation, with exact circle/segment/rectangle distances.
def point_seg(p,a,b):
    dx=b[0]-a[0];dy=b[1]-a[1];d=dx*dx+dy*dy
    t=max(0,min(1,((p[0]-a[0])*dx+(p[1]-a[1])*dy)/d)) if d else 0
    return math.hypot(p[0]-a[0]-t*dx,p[1]-a[1]-t*dy)
def cross(a,b,c):return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
def seg_dist(a,b,c,d):
    aa,bb,cc,dd=cross(a,b,c),cross(a,b,d),cross(c,d,a),cross(c,d,b)
    if aa*bb<=0 and cc*dd<=0 and max(min(a[0],b[0]),min(c[0],d[0]))<=min(max(a[0],b[0]),max(c[0],d[0]))+1e-10 and max(min(a[1],b[1]),min(c[1],d[1]))<=min(max(a[1],b[1]),max(c[1],d[1]))+1e-10:return 0
    return min(point_seg(a,c,d),point_seg(b,c,d),point_seg(c,a,b),point_seg(d,a,b))
def rect_corners(p):
    x,y=p['xy'];w,h=p['size'];return [(x-w/2,y-h/2),(x+w/2,y-h/2),(x+w/2,y+h/2),(x-w/2,y+h/2)]
def primitive(p):
    if 'a' in p:return ('seg',p['a'],p['b'],p['w']/2)
    if p['shape']=='C':return ('seg',p['xy'],p['xy'],p['diam']/2)
    return ('rect',rect_corners(p))
def gap(pa,pb):
    a,b=primitive(pa),primitive(pb)
    if a[0]=='seg' and b[0]=='seg':return seg_dist(a[1],a[2],b[1],b[2])-a[3]-b[3]
    if a[0]=='rect' and b[0]=='seg':a,b=b,a
    if a[0]=='seg':
        vs=b[1]
        for p in [a[1],a[2]]:
            if vs[0][0]<=p[0]<=vs[2][0] and vs[0][1]<=p[1]<=vs[2][1]:return -a[3]
        return min(seg_dist(a[1],a[2],v,w) for v,w in zip(vs,vs[1:]+vs[:1]))-a[3]
    av,bv=a[1],b[1]
    dx=max(av[0][0]-bv[2][0],bv[0][0]-av[2][0],0);dy=max(av[0][1]-bv[2][1],bv[0][1]-av[2][1],0)
    return math.hypot(dx,dy)
def features(layer):return [p for p in pads if layer in p['layers']]+[t for t in tracks if t['layer']==layer]
errors=[];minimum=[999,None]
for layer in ['F','B']:
    ff=features(layer)
    for a,b in itertools.combinations(ff,2):
        if a['net']==b['net']:continue
        g=gap(a,b)
        if g<minimum[0]:minimum=[g,[layer,a['id'],b['id']]]
        if g<.3-1e-6:errors.append(dict(kind='copper_clearance',layer=layer,a=a['id'],b=b['id'],gap=g))
    for a in ff:
        z=primitive(a)
        if z[0]=='seg':edge=min(12.7-max(abs(z[1][0]),abs(z[2][0])),31.25-max(abs(z[1][1]),abs(z[2][1])))-z[3]
        else:edge=min(12.7-max(abs(p[0]) for p in z[1]),31.25-max(abs(p[1]) for p in z[1]))
        if edge<.3-1e-6:errors.append(dict(kind='copper_edge',id=a['id'],gap=edge))
        for h in holes:
            if h['plated']:continue
            hp=dict(id=h['id'],xy=h['xy'],shape='C',diam=6.0)
            g=gap(a,hp)
            if g<.3-1e-6:errors.append(dict(kind='screwhead_keepout',id=a['id'],hole=h['id'],gap=g))

design=dict(revision='P36',base='P35',outline=[25.4,62.5],thickness=1.6,copper_finished_oz=4.5,pads=pads,tracks=tracks,holes=holes,requirements=dict(min_track=.3,min_clearance=.3,min_copper_edge=.3,screw_head_keepout_diameter=6),source_terminal_model=SRC)
report=dict(error_count=len(errors),errors=errors,min_copper_clearance=minimum,scope='custom analytic primitive DRC inside BlenderMCP; not KiCad/JLC DFM')
with open(OUT+'/route_candidate.json','w') as f:json.dump(design,f,indent=2)
with open(OUT+'/route_drc.json','w') as f:json.dump(report,f,indent=2)
print(json.dumps(report))
