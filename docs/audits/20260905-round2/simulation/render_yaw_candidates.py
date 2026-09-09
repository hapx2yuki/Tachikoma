from pathlib import Path
import sys,json,math
import numpy as np,trimesh
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
from matplotlib.patches import Patch
ROOT=Path.cwd();sys.path.insert(0,str(ROOT/'tools'))
import export_urdf as E
from sim_collision import parts_with_pad
BASE=ROOT/'docs/audits/20260905-round2/simulation'

def case_meshes(row):
    p=E.C.LEG_SERVO;cx=p['L']/2-p['SHAFT_OFF'];out=[]
    for x,y,a in np.reshape(row['parameters_xf_yf_thetaf_xr_yr_thetar'],(2,3)):
        T=E.trans(x,y,row['tab_z_mm'])@E.rot(a,'z')@E.rot(180,'x')@E.trans(-cx,0,-p['TAB_BELOW']/2)
        m=trimesh.creation.box([p['L'],p['W'],p['TAB_BELOW']],transform=T)
        out.append(m);l=m.copy();l.apply_transform(np.diag([-1,1,1,1]));out.append(l)
    return out

def best(name):
    j=json.loads((BASE/name).read_text());return min(j['candidates'],key=lambda r:r['max_constraint_violation_mm'])

from make_chassis import BOSS_H,CASE_ANG
original={'parameters_xf_yf_thetaf_xr_yr_thetar':[*E.C.HIPS['FR'],CASE_ANG['FR'],*E.C.HIPS['RR'],CASE_ANG['RR']], 'tab_z_mm':E.C.CHASSIS_T+BOSS_H}
near=best('yaw-pack-step-strict10-variable-z.json')
wide=best('yaw-pack-step-wide-variable-z.json')
common=[]
for name,T in [('Head_Top_Blue',E.HEAD_TOP_T),('Head_Bottom_Blue',E.HEAD_BOTTOM_T)]:
    m=E.KIT.normalized_mesh(name);m.apply_transform(T);m.apply_translation([0,0,-E.ZB]);common.append((m,'#69aeca',.08))
ready={f'arm_{tag}_{axis}':a for tag in ('r','l') for axis,a in [('yaw',10),('pitch',30),('elbow',40)]}
for link,parts in parts_with_pad(True).items():
    for mesh,_,name in parts:
        if link.startswith('eye_') or name.startswith('eye_') or name.startswith('arm_') and name.endswith('_servo_case'):
            m=mesh.copy();m.apply_transform(E.LINK_PARENT_FRAME[link](ready));m.apply_translation([0,0,-E.ZB]);common.append((m,'#8293a2',.22))
r=trimesh.creation.box([19.8,25.4,10.5]);r.apply_translation([0,42.5,17.75]);common.append((r,'#a06cd5',.30))
fig=plt.figure(figsize=(16,9),facecolor='white')
for col,(title,row) in enumerate([('Current mounts',original),('Move <= 10 mm (rejected)',near),('Wide search (rejected)',wide)]):
    for rr,(elev,azim,view) in enumerate([(90,-90,'TOP'),(0,0,'SIDE')]):
        ax=fig.add_subplot(2,3,rr*3+col+1,projection='3d');ax.set_proj_type('ortho')
        for mesh,color,alpha in common+[(m,'#e26732',.55) for m in case_meshes(row)]:
            if len(mesh.faces)>1800:mesh=mesh.simplify_quadric_decimation(face_count=1800)
            collection=Poly3DCollection(mesh.triangles,facecolor=color,edgecolor='none',alpha=alpha)
            ax.add_collection3d(collection)
        ax.set(xlim=(-85,85),ylim=(-60,85),zlim=(-25,110),xlabel='X / mm',ylabel='Y / mm',zlabel='Z / mm')
        ax.set_box_aspect([170,145,135]);ax.view_init(elev,azim)
        if rr==0:ax.set_zticks([]);ax.set_zlabel('')
        else:ax.set_xticks([]);ax.set_xlabel('')
        label=f'{view}: {title}\nTab z = {row["tab_z_mm"]:.2f} mm'
        if 'max_constraint_violation_mm' in row:label+=f', violation {row["max_constraint_violation_mm"]:.2f} mm'
        ax.set_title(label,fontsize=11)
fig.suptitle('Yaw servo packing: source STL head / eyes and full main-case envelopes\nBoth proposed layouts retain real intersections; no CAD geometry was changed.',fontsize=15,y=.98)
fig.legend(handles=[Patch(color='#69aeca',label='Original head (transparent)'),Patch(color='#8293a2',label='Eyes, carriers and arm cases'),Patch(color='#e26732',label='Four DS3218 main cases'),Patch(color='#a06cd5',label='XIAO envelope from official STEP')],loc='lower center',ncol=4)
fig.subplots_adjust(top=.85,bottom=.09,hspace=.18,wspace=.04)
fig.savefig(BASE/'yaw-packing-comparison.png',dpi=150)
(BASE/'yaw-packing-comparison.json').write_text(json.dumps({'original':original,'near':near,'wide':wide,'render_method':'Source STL triangles (display decimation <=1800 faces); metrics computed on original full mesh. Transparency and orthographic views are explanatory, not collision proof.'},indent=2))
