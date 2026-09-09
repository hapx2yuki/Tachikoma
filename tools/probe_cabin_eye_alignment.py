#!/usr/bin/env python3
"""Cabin Eye中心をリング面に置いた誤差の有限切り分け。標準姿勢は変更しない。"""
import hashlib,json,sys
from pathlib import Path
import numpy as np
import trimesh
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'tools'),str(ROOT/'hardware/src')]
import export_urdf as E
from design_cabin_electronics import native,source
OUT=ROOT/'docs/audits/20260905-round2/cabin-electronics'

def main():
    data=json.loads((ROOT/'tools/data/kit_assembly_rear.json').read_text())
    # JSON structureは元データを使い、軸の向きの転記を避ける。
    def find(obj):
        if isinstance(obj,dict):
            if 'Cabin_Eye_White' in obj:return obj['Cabin_Eye_White']
            for v in obj.values():
                result=find(v)
                if result:return result
        return None
    entry=find(data);normal=np.array(entry['outward_normal'],float);normal/=np.linalg.norm(normal)
    parts={}
    for m,color,name in E.base_link_parts():
        if name in ['Cabin_Eye_White#single','pod_neck','Head_TailJoint_Ball_Grey_Optional_Cross#single','Head_TailJoint_Blue_Optional_Cross#single']:
            m=m.copy();m.apply_translation([0,0,-E.ZB]);parts[name]=m
    eye=parts.pop('Cabin_Eye_White#single');eye_native=native(eye)
    blockers={k:native(m) for k,m in parts.items()}
    blockers['Cabin_Front_original']=native(source('Cabin_Front_Blue'))
    frontpath=OUT/'candidate_cabin_front_with_wire_route.stl'
    blockers['Cabin_Front_candidate']=native(trimesh.load(frontpath,force='mesh'))
    rows=[]
    for shift in list(np.arange(0,15.001,.5))+[7.39]:
        moved=eye_native.translate(normal*shift)
        rows.append({'normal_shift_mm':float(shift),'translation_mm':(normal*shift).tolist(),'overlap_mm3':{name:max(0.,(moved^block).volume()) for name,block in blockers.items()}})
    rows.sort(key=lambda r:r['normal_shift_mm'])
    # 7.39mmは元JSON記載の半厚み。最初の無干渉も比較用に保存する。
    clear=[r for r in rows if all(v<.01 for v in r['overlap_mm3'].values())]
    for label,shift in [('half_depth7p39',7.39)]+([('first_clear',clear[0]['normal_shift_mm'])] if clear else []):
        m=eye.copy();m.apply_translation(normal*shift);m.export(OUT/('candidate_cabin_eye_'+label+'.stl'))
    result={'status':'POSITION_CANDIDATE_ONLY_NO_STANDARD_CHANGE','frame':'chassis bottom z0','outward_normal':normal.tolist(),'current_position_mm':entry['pos_mm'],'source_sha256':{'tools/data/kit_assembly_rear.json':hashlib.sha256((ROOT/'tools/data/kit_assembly_rear.json').read_bytes()).hexdigest(),'model/Cabin_Eye_White.stl':hashlib.sha256((ROOT/'model/Cabin_Eye_White.stl').read_bytes()).hexdigest(),str(frontpath.relative_to(ROOT)):hashlib.sha256(frontpath.read_bytes()).hexdigest()},'samples':rows,'first_all_clear_sample':clear[0] if clear else None,'limitations':['clearance is not adhesive rim contact proof','moving the visible Eye changes silhouette; original assembly photo and exact rim datum unavailable','whole neck placement/attachment still unresolved','0.5mm grid is cause isolation only; no continuous-position certification']}
    (OUT/'eye-position-probe.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    for row in rows:print(row,flush=True)
    print('first_all_clear',result['first_all_clear_sample'])
if __name__=='__main__':main()
