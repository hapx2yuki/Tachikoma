#!/usr/bin/env python3
"""物理シミュレーション用の材料別/部品別凸分解。CADのSTLは変更しない。

VHACDは近似。名前付きの元部品・凸片・体積比を保存し、実メッシュ交差監査と
併用する。穴を持つ単一部品も凸包1個へ戻して自己接触を無効化しない。
"""
import hashlib
import json
from importlib.metadata import version
import os
from pathlib import Path
import sys
import tempfile
import numpy as np
import trimesh

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import export_urdf as E

CACHE=ROOT/'docs/audits/20260905-round2/simulation/collision-cache'
VHACD_SETTINGS=dict(maxConvexHulls=32,resolution=200000,
    minimumVolumePercentErrorAllowed=.5,maxNumVerticesPerCH=64,asyncACD=False,shrinkWrap=True)


def _atomic_save_hulls(path,arrays):
    """並列生成中に途中のZIPを読ませない。同一キーの完成品だけを公開する。"""
    temp=None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent,prefix=path.name+'.',suffix='.tmp',delete=False) as stream:
            temp=Path(stream.name)
            np.savez_compressed(stream,**arrays)
        os.replace(temp,path)
    finally:
        if temp is not None and temp.exists():temp.unlink()


def parts_with_pad(include_servos=False,foot_candidate_dir=None):
    parts=E.collect_all_parts()
    for link,items in parts.items():
        if link.endswith('_tibia') and not any(n == 'foot_pad' for _,_,n in items):
            pad=E.load('foot_pad');pad.apply_transform(E.trans(0,0,-E.C.TIBIA_LEN))
            items.append((pad,'#333333','foot_pad'))
    if foot_candidate_dir:
        candidate=Path(foot_candidate_dir)
        if not candidate.is_absolute():candidate=ROOT/candidate
        placements={p.instance:p for p in E.KIT.by_link(E.KIT_PLACEMENTS,'leg_foot_bored')
                    if p.part=='Leg_Toe_Black_x12'}
        for leg in E.LEGS:
            items=parts[f'leg_{leg.lower()}_tibia']
            items[:]=[(m,c,n) for m,c,n in items if not n.startswith('Leg_Toe_Black_x12#')]
            for i in range(3):
                transform=E.trans(0,0,-E.C.TIBIA_LEN)@placements[f'{leg}_{i}'].matrix@np.linalg.inv(placements[f'FR_{i}'].matrix)
                for stem,material,name in (
                    ('shoe_fitted','#333333',f'foot_pad#shoe_{leg}_{i}'),
                    ('toe_hidden_seat','#222222',f'Leg_Toe_Black_x12#{leg}_{i}')):
                    mesh=trimesh.load(candidate/f'FR_{i}_{stem}_candidate.stl')
                    mesh.apply_transform(transform);items.append((mesh,material,name))
    if include_servos:
        mounts=[]
        for leg in E.LEGS:
            for kind,frame in E.leg_servo_frames(leg).items():
                link='base_link' if kind=='yaw' else f"leg_{leg.lower()}_{'coxa' if kind=='pitch' else 'femur'}"
                mounts.append((link,f'leg_{leg.lower()}_{kind}_servo_case',E.C.LEG_SERVO,frame))
        for tag in ('r','l'):
            for kind,frame in E.arm_servo_frames(tag).items():
                link='base_link' if kind=='yaw' else f"arm_{tag}_{'shoulder' if kind=='pitch' else 'upper'}"
                mounts.append((link,f'arm_{tag}_{kind}_servo_case',E.C.ARM_SERVO,frame))
        for idx, tag in ((0,'r'),(2,'l')):
            mounts.append(('base_link', f'eye_{tag}_servo_case', E.C.EYE_SERVO,
                           E.eye_servo_frame(idx)))
            parts['base_link'].append((E.eye_carrier_mesh(idx),'#444444',
                                       f'eye_carrier#{tag}'))
        for link,name,p,frame in mounts:
            cx=p['L']/2-p['SHAFT_OFF']
            m=trimesh.creation.box((p['L'],p['W'],p['TAB_BELOW']))
            m.apply_transform(frame@E.trans(-cx,0,-p['TAB_BELOW']/2))
            parts[link].append((m,'#444444',name))
    return parts


def convex_parts(mode='parts', cache=CACHE, include_servos=False,foot_candidate_dir=None):
    """(body,part_name,material,hulls_mm)を返す。mode=parts/vhacd。"""
    cache=Path(cache);cache.mkdir(parents=True,exist_ok=True)
    result=[];manifest=[]
    definition={'format':2,'mode':mode,'single_hull_ratio_threshold':1.05,
        'trimesh_version':trimesh.__version__,'vhacdx_version':version('vhacdx'),
        'vhacd_settings':VHACD_SETTINGS}
    definition_bytes=json.dumps(definition,sort_keys=True).encode()
    for link,items in parts_with_pad(include_servos,foot_candidate_dir).items():
        for mesh,_,name in items:
            mesh=E._ensure_outward(mesh)
            digest=hashlib.sha256(mesh.vertices.tobytes()+mesh.faces.tobytes()+definition_bytes).hexdigest()
            path=cache/(digest+'.npz')
            ratio=mesh.convex_hull.volume/max(abs(mesh.volume),1e-12)
            if path.exists():
                with np.load(path,allow_pickle=False) as saved:
                    hulls=[trimesh.Trimesh(vertices=saved[f'v{i}'],faces=saved[f'f{i}'],process=False)
                           for i in range(int(saved['count']))]
            else:
                if mode=='vhacd' and ratio>1.05:
                    # 原点・単位は元STL(mm)のまま。各片の精度は別の監査で検査する。
                    args=trimesh.decomposition.convex_decomposition(mesh,**VHACD_SETTINGS)
                    hulls=[trimesh.Trimesh(**a).convex_hull for a in args]
                else:
                    hulls=[mesh.convex_hull]
                arrays={'count':np.array(len(hulls))}
                for i,h in enumerate(hulls):arrays[f'v{i}']=h.vertices;arrays[f'f{i}']=h.faces
                _atomic_save_hulls(path,arrays)
            thin_replacements=[]
            for i,h in enumerate(hulls):
                center=h.vertices.mean(axis=0)
                _,_,axes=np.linalg.svd(h.vertices-center,full_matrices=False)
                local=(h.vertices-center)@axes.T
                low,high=local.min(axis=0),local.max(axis=0)
                extent=high-low
                if extent.min()<.02:
                    # VHACDが平面状の破片を返す場合がある。黙って削除せず、
                    # 元片全体を包含する厚み0.02mmの箱へ置換し、誤差を記録。
                    T=np.eye(4);T[:3,:3]=axes.T;T[:3,3]=center+axes.T@((low+high)/2)
                    replacement=trimesh.creation.box(np.maximum(extent,.02),transform=T)
                    hulls[i]=E._ensure_outward(replacement)
                    thin_replacements.append({'hull':i,'original_extents_mm':extent.tolist(),
                        'max_added_half_thickness_mm':float(np.maximum(.02-extent,0).max()/2)})
            material='TPU' if name=='foot_pad' else 'SERVO_CASE' if name.endswith('_servo_case') else E.part_material(name)[0]
            result.append((link,name,material,hulls))
            manifest.append({'link':link,'part':name,'material':material,'cache':str(path.relative_to(ROOT)),
                'cache_definition':definition,
                'input_mesh_sha256':digest,'hull_count':len(hulls),'source_volume_mm3':abs(mesh.volume),
                'single_hull_volume_ratio':ratio,'hulls_sum_volume_mm3':sum(abs(h.volume) for h in hulls),
                'thin_piece_replacements':thin_replacements,'source_watertight':bool(mesh.is_watertight)})
    destination=cache/f'manifest-{mode}{"-servos" if include_servos else ""}{"-foot-candidate" if foot_candidate_dir else ""}.json'
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=cache,prefix=destination.name+'.',suffix='.tmp',delete=False) as stream:
            temporary=Path(stream.name);json.dump(manifest,stream,indent=2,ensure_ascii=False)
        os.replace(temporary,destination)
    finally:
        if temporary is not None and temporary.exists():temporary.unlink()
    return result


if __name__=='__main__':
    import argparse
    ap=argparse.ArgumentParser();ap.add_argument('--mode',choices=['parts','vhacd'],default='vhacd')
    a=ap.parse_args();r=convex_parts(a.mode)
    print('parts',len(r),'convex pieces',sum(len(row[3]) for row in r))
