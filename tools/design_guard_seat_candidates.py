#!/usr/bin/env python3
"""装飾ガードを保存し、支持殻側の受け座と挿入口を有限比較する。候補専用。"""
from pathlib import Path
import sys,itertools,time,json,hashlib
import numpy as np,trimesh
from manifold3d import Manifold,Mesh
root=Path(__file__).resolve().parents[1];sys.path[:0]=[str(root/'tools'),str(root/'hardware/src')]
import export_urdf as E
from lib import box,to_trimesh
out=root/'docs/audits/20260905-round2/guard-seat-candidates';out.mkdir(exist_ok=True)
def N(m):return Manifold(Mesh(np.asarray(m.vertices,np.float32),np.asarray(m.faces,np.uint32)))
ball=trimesh.creation.icosphere(subdivisions=0,radius=1);rin=float(min(np.einsum('ij,ij->i',ball.face_normals,ball.triangles_center)))
directions=[np.array(a)/np.linalg.norm(a) for a in itertools.product((-1,0,1),repeat=3) if any(a)]
result={'offset_polyhedron':'外接二十面体。指定値は最小球半径、頂点の最大拡大量は1/inradius倍。','icosa_inradius':rin,'parts':[]}
for kind,gn,sn in [('femur','Leg_Thigh_Guard_Blue_x4#FL','thigh_cap'),('tibia','Leg_Shin_Guard_Grey_x4#FL','shin_shell')]:
 parts=E.leg_parts('FL')[kind];ps={n:m for m,c,n in parts};s,g=ps[sn],ps[gn];S,G=N(s),N(g)
 row={'kind':kind,'parent_link':'leg_fl_'+kind,'guard_name':gn,'shell_name':sn,'before_shell_volume_mm3':s.volume,'guard_volume_mm3':g.volume,'before_intersection_mm3':float((S^G).volume()),'guard_bone_intersections':{},'candidates':[]}
 fr={n:m for m,c,n in E.leg_parts('FR')[kind]};fr_shell=N(fr[sn+('_m' if kind=='tibia' else '')]);fr_guard=N(fr[gn[:-2]+'FR']);expected_s=S.mirror([0,1,0]) if kind=='tibia' else S;expected_g=G.mirror([0,1,0]) if kind=='tibia' else G
 row['FR_mirror_contract_symmetric_difference_mm3']={'shell':float((fr_shell-expected_s).volume()+(expected_s-fr_shell).volume()),'guard':float((fr_guard-expected_g).volume()+(expected_g-fr_guard).volume())}
 for n,m in fr.items():
  if n.startswith(('femur_link','tibia_link')):row['guard_bone_intersections']['FR_'+n]=float((fr_guard^N(m)).volume())
 for n,m in ps.items():
  if n.startswith(('femur_link','tibia_link')):row['guard_bone_intersections'][n]=float((G^N(m)).volume())
 if kind=='femur':protect=box(200,200,.8).translate([0,0,13.1+.4]);protect_note='femur上面へ接着する下面z=13.1..13.9の0.8mm帯'
 else:
  protect=box(12,120,18).translate([0,0,-60])+box(12,120,18).translate([0,0,-100]);protect_note='M3長穴2箇所のx±6,z中心±9,Y全長（ねじ支持保存用比較領域）'
 row['protected_material_note']=protect_note;row['protected_material_before_mm3']=float((S^protect).volume())
 for clear in (.1,.15):
  b=ball.copy();b.apply_scale(clear/rin);D=G.minkowski_sum(N(b));pocket=S-D
  cuts=[('pocket',D)]
  if clear==.1:
   # 元の装飾方向から真っ直ぐ入れる開口例。線分を厚さ0.0002mmの保守的箱で表現。
   axis=2 if kind=='femur' else 0
   dims=np.full(3,.0002);dims[axis]=64
   travel=np.zeros(3);travel[axis]=32
   sweep=D.minkowski_sum(box(*dims).translate(travel.tolist()))
   cuts.append(('open_positive_z' if kind=='femur' else 'open_positive_x',sweep))
  for mode,cutter in cuts:
   unsimplified=S-cutter;cand=unsimplified.simplify(.01);cm=to_trimesh(cand);tag=f'{kind}_{mode}_clear{clear:.2f}'.replace('.','_');path=out/(tag+'_simplified_link_candidate.stl');cm.export(path);loaded=trimesh.load(path,force='mesh');removed=S-cand
   # 原表面を0.01mm内側へ寄せて削除域を判別する。面中心の面積和はサンプル近似。
   points=s.triangles_center-s.face_normals*.01
   removed_mask=to_trimesh(cutter).contains(points)
   selected=points[removed_mask];sd=trimesh.proximity.signed_distance(g,selected) if len(selected) else np.array([])
   exposed=sd<-.2
   exposed_points=selected[exposed]
   entry={'name':tag,'minimum_clearance_mm':clear,'maximum_offset_mm':clear/rin,'mode':mode,'simplification_tolerance_mm':.01,'simplification_symmetric_difference_mm3':float((unsimplified-cand).volume()+(cand-unsimplified).volume()),'stl':str(path.relative_to(root)),'volume_mm3':cm.volume,'components':[float(a.volume()) for a in cand.decompose()],'stl_watertight':bool(loaded.is_watertight),'stl_bodies':len(loaded.split(only_watertight=False)),'guard_intersection_mm3':float((cand^G).volume()),'removed_mm3':float(removed.volume()),'protected_material_lost_mm3':float((removed^protect).volume()),'surface_sample_area_removed_outside_guard_plus_0_2_mm2':float(s.area_faces[removed_mask][exposed].sum()),'surface_sample_count_outside_guard_plus_0_2':int(exposed.sum()),'exposed_surface_sample_bounds':[exposed_points.min(axis=0).tolist(),exposed_points.max(axis=0).tolist()] if len(exposed_points) else None,'insertion_samples':[]}
   for direction in directions:
    # まず初動で閉じ込めを確認。初動を通れる向きだけ長い経路を調べる。
    vals=[float((cand^G.translate((direction*d).tolist())).volume()) for d in (.25,.5,1.)]
    if max(vals)<.01:vals += [float((cand^G.translate((direction*d).tolist())).volume()) for d in (2.,4.,8.,16.,32.,64.)]
    entry['insertion_samples'].append({'direction':direction.tolist(),'displacements_mm':[.25,.5,1.] if len(vals)==3 else [.25,.5,1.,2.,4.,8.,16.,32.,64.],'intersections_mm3':vals,'max_mm3':max(vals)})
   entry['best_sampled_direction']=min(entry['insertion_samples'],key=lambda e:e['max_mm3'])
   entry['all_directions_blocked_first_mm']=all(x['max_mm3']>.01 for x in entry['insertion_samples'])
   entry['full_assembly_pass']=False
   row['candidates'].append(entry)
   print(kind,mode,clear,'bodies',entry['stl_bodies'],'water',entry['stl_watertight'],'lost',entry['removed_mm3'],'protectloss',entry['protected_material_lost_mm3'],'exposedarea',entry['surface_sample_area_removed_outside_guard_plus_0_2_mm2'],'bestinsert',entry['best_sampled_direction']['max_mm3'],flush=True)
 result['parts'].append(row)
 (out/'comparison-final.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
result['input_sha256']={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path(__file__).resolve(),root/'hardware/src/config.py',root/'hardware/src/lib.py',root/'hardware/src/shell_mod.py',root/'tools/kit_assembly.py',root/'tools/export_urdf.py',root/'tools/data/kit_assembly_front.json',root/'hardware/stl/thigh_cap.stl',root/'hardware/stl/shin_shell.stl',root/'model/Leg_Thigh_Guard_Blue_x4.stl',root/'model/Leg_Shin_Guard_Grey_x4.stl']}
result['limitations']=['26方向・3〜9点の並進は有限反証。無干渉値は連続経路保証ではない。','削除された可視面の面積は原面中心サンプルでの0.2mmガード近傍外の集計。視点ごとの遮蔽判定ではない。','骨格は無加工。ガードと骨格の従来干渉は残る。','受け座全体の印刷壁厚・接着・実物公差・組付け荷重は未検証。']
(out/'comparison-final.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
