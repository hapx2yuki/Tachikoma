#!/usr/bin/env python3
"""左右対称4yawケースの収納探索。STL/configは変更しない。凸包候補を実Booleanで追試。"""
import json,sys,math,itertools
from pathlib import Path
import numpy as np,trimesh
from scipy.optimize import differential_evolution
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
import export_urdf as E
import sim_physics as S
from sim_collision import parts_with_pad


def search(out,tab_z=7.,seed=52,wide=False,trials=4,near=None,tab_range=None):
    initial_hashes=S.input_fingerprints()
    parts=E.collect_all_parts();P=E.C.LEG_SERVO;cx=P['L']/2-P['SHAFT_OFF'];half=np.array([P['L'],P['W'],P['TAB_BELOW']])/2
    head=[];obstacles=[]
    for stem,T in [('Head_Top_Blue',E.HEAD_TOP_T),('Head_Bottom_Blue',E.HEAD_BOTTOM_T)]:
        m=E.KIT.normalized_mesh(stem);m.apply_transform(T);m.apply_translation([0,0,-E.ZB]);head.append(m)
    hull=trimesh.util.concatenate(head).convex_hull
    face_n=hull.face_normals;offset=-np.sum(face_n*hull.triangles_center,axis=1)
    keep=np.unique(np.round(np.column_stack([face_n,offset]),5),axis=0,return_index=True)[1];face_n=face_n[keep];offset=offset[keep]
    for link in ['eye_r_pod','eye_l_pod','eye_pod_camera']:
        for mesh,_,name in parts[link]:
            m=mesh.copy();m.apply_transform(E.LINK_PARENT_FRAME[link]({}));m.apply_translation([0,0,-E.ZB]);obstacles.append((link+'/'+name,m))
    ready={f'arm_{tag}_{axis}':angle for tag in ('r','l')
           for axis,angle in (('yaw',10.),('pitch',30.),('elbow',40.))}
    for link,items in parts_with_pad(True).items():
        for mesh,_,name in items:
            if name.startswith('arm_') and name.endswith('_servo_case'):
                for label,q in [('zero',{}),('ready',ready)]:
                    m=mesh.copy();m.apply_transform(E.LINK_PARENT_FRAME[link](q));m.apply_translation([0,0,-E.ZB]);obstacles.append((name+'/'+label,m))
            elif name.startswith('eye_') and (name.endswith('_servo_case') or name.startswith('eye_carrier#')):
                m=mesh.copy();m.apply_translation([0,0,-E.ZB]);obstacles.append((name,m))
    # 公式STEPのSD込み24.363×17.780×8.500mmに余裕を加え、長手をYへ。
    reserved=trimesh.creation.box([19.8,25.4,10.5]);reserved.apply_translation([0,42.5,17.75])
    obstacles.append(('XIAO_STEP_reserved_19.8x25.4x10.5_center_0_42.5_17.75',reserved))
    obstacle_data=[]
    for name,m in obstacles:
        hh=m.convex_hull;norm=np.unique(np.round(hh.face_normals,5),axis=0);v=hh.vertices;proj=v@norm.T
        obstacle_data.append((name,m,norm,proj.min(axis=0),proj.max(axis=0),v))
    signs=np.array(list(itertools.product([-1,1],repeat=3)));relative=signs*half
    original=np.array([*E.C.HIPS['FR'],0,*E.C.HIPS['RR'],0])
    def poses(x):
        ans=[]
        height=x[6] if tab_range is not None else tab_z
        for xx,yy,theta in [x[:3],x[3:6]]:
            a=math.radians(theta);R=np.array([[math.cos(a),-math.sin(a),0],[math.sin(a),math.cos(a),0],[0,0,1.]])
            center=np.array([xx,yy,height+half[2]])+R@np.array([-cx,0,0])
            for side in (1,-1):
                mirror=np.diag([side,1.,1.]);ans.append((mirror@center,mirror@R,(relative@R.T+center)@mirror))
        return ans
    def constraints(x,details=False):
        pp=poses(x);viol=[];gaps=[]
        for center,R,corners in pp:
            excess=float((corners@face_n.T+offset+1.5).max());viol.append(max(excess,0))
            for name,m,n,low,high,v in obstacle_data:
                cp=corners@n.T;separation=float(np.maximum(cp.min(axis=0)-high,low-cp.max(axis=0)).max())
                vp=v@R;pc=center@R
                separation=max(separation,float(np.maximum(pc-half-vp.max(axis=0),vp.min(axis=0)-(pc+half)).max()))
                viol.append(max(.6-separation,0))
        for a,b in itertools.combinations(pp,2):
            ca,Ra,va=a;cb,Rb,vb=b;axes=np.concatenate([Ra[:,:2],Rb[:,:2]],axis=1)
            pa,pb=va@axes,vb@axes;gap=float(np.maximum(pa.min(axis=0)-pb.max(axis=0),pb.min(axis=0)-pa.max(axis=0)).max());gaps.append(gap);viol.append(max(.6-gap,0))
        return np.array(viol),gaps,pp
    def decode(y):
        if near is None:return np.array(y)
        # 半径で上限を厳密に守る。重み付き罰則で10mmを超えて妥協しない。
        x=np.array(y)
        for i in (0,3):
            radius,angle=y[i],math.radians(y[i+1])
            x[i:i+2]=original[i:i+2]+radius*np.array([math.cos(angle),math.sin(angle)])
        return x
    def objective(x):
        x=decode(x)
        v,_,_=constraints(x);change=sum(np.linalg.norm(x[i:i+2]-original[i:i+2]) for i in [0,3])+.025*(abs(x[2])+abs(x[5]))
        return float(v@v*1000+change)
    bounds=[(25,52),(-2,29),(-180,180),(25,52),(-34,4),(-180,180)]
    candidates=[]
    if wide:bounds=[(5,55),(-15,50),(-180,180),(5,55),(-40,35),(-180,180)]
    if near is not None:
        bounds=[(0,near),(-180,180),(-180,180),(0,near),(-180,180),(-180,180)]
    if tab_range is not None:bounds.append(tuple(tab_range))
    for trial in range(trials):
        res=differential_evolution(objective,bounds,seed=seed+trial,popsize=12,maxiter=180,tol=.0001,polish=True,workers=1)
        x=decode(res.x)
        v,gaps,pp=constraints(x);booleans=[];case_meshes=[]
        for i,(center,R,corners) in enumerate(pp):
            T=np.eye(4);T[:3,:3]=R;T[:3,3]=center;m=trimesh.creation.box(2*half,transform=T);case_meshes.append(m)
            for name,obst in obstacles:
                cut=trimesh.boolean.intersection([m,obst],engine='manifold',check_volume=False);volume=abs(float(cut.volume))
                if volume>.01:booleans.append({'case':i,'obstacle':name,'intersection_mm3':volume})
            for link,items in parts.items():
                if link!='base_link':continue
                for mesh,_,name in items:
                    if name.startswith('Head_'):
                        ob=E._ensure_outward(mesh.copy());ob.apply_translation([0,0,-E.ZB]);cut=trimesh.boolean.intersection([m,ob],engine='manifold',check_volume=False);volume=abs(float(cut.volume))
                        if volume>.01:booleans.append({'case':i,'obstacle':name,'intersection_mm3':volume,'requires_internal_recut':True})
        height=float(x[6]) if tab_range is not None else tab_z
        row={'parameters_xf_yf_thetaf_xr_yr_thetar':x[:6].tolist(),'tab_z_mm':height,'tab_search_range_mm':tab_range,'max_constraint_violation_mm':float(v.max()),'all_constraint_violations_mm':v.tolist(),'max_axis_move_mm':near,'axis_move_mm':[float(np.linalg.norm(x[i:i+2]-original[i:i+2])) for i in (0,3)],'pair_separations_mm':gaps,'objective':float(res.fun),'case_centers_chassis_mm':[p[0].tolist() for p in pp],'case_z_range_chassis_mm':[height,height+P['TAB_BELOW']],'boolean_intersections':booleans}
        candidates.append(row);print('candidate',trial,row['parameters_xf_yf_thetaf_xr_yr_thetar'],'violation',v.max(),'boolean',booleans,flush=True)
        Path(out).write_text(json.dumps({'input_sha256':initial_hashes,'inputs_unchanged_during_search':S.input_fingerprints()==initial_hashes,'method':'yaw主ケース28.2mm高、外殻は元Head上下を包む凸包より1.5mm内側。これは収納の必要条件のみ。目固定ケース/保持板は名目ロール、腕ケースは0度/READYの凸包と0.6mm距離、ケース間0.6mm。XIAO予約は公式STEP由来の19.8x25.4x10.5mm(0,42.5,17.75)。近距離探索は半径による厳密な上限制約。固定障害物の凸包は空隙を埋めるため、不合格だけで収納不能と断定しない。最終実Booleanで既存内殻の追加切削量を記録。脚骨格/配線/タブ/ギヤ上部/ベアリング未含有。','obstacles':[name for name,_ in obstacles],'head_outer_z_bounds_mm':hull.bounds[:,2].tolist(),'necessary_tab_lower_bound_mm':float(hull.bounds[0,2]+1.5),'original_parameters':original.tolist(),'candidates':candidates},indent=2,ensure_ascii=False))
    return candidates

if __name__=='__main__':
 import argparse
 p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--tab-z',type=float,default=7.);p.add_argument('--tab-range',type=float,nargs=2);p.add_argument('--wide',action='store_true');p.add_argument('--trials',type=int,default=4);p.add_argument('--near',type=float);a=p.parse_args();search(a.out,a.tab_z,wide=a.wide,trials=a.trials,near=a.near,tab_range=a.tab_range)
