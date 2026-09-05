#!/usr/bin/env python3
"""Cabin収納室の69局所検査に含まれない首・Eye・Ballの実体関係を確認。"""
import hashlib,json,sys
from pathlib import Path
import trimesh
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'tools'),str(ROOT/'hardware/src')]
import export_urdf as E
from design_cabin_electronics import source,native

def main():
    out=ROOT/'docs/audits/20260905-round2/cabin-electronics'
    path=out/'candidate_cabin_front_with_wire_route.stl'
    after=native(trimesh.load(path,force='mesh'));before=native(source('Cabin_Front_Blue'))
    records=[]
    names=['pod_neck','Cabin_Eye_White#single','Head_TailJoint_Blue_Optional_Cross#single','Head_TailJoint_Ball_Grey_Optional_Cross#single']
    for mesh,color,name in E.base_link_parts():
        if name not in names:continue
        mesh=mesh.copy();mesh.apply_translation([0,0,-E.ZB]);part=native(mesh)
        records.append({'part':name,'original_front_overlap_mm3':max(0.,(before^part).volume()),'candidate_front_overlap_mm3':max(0.,(after^part).volume())})
    sources=[Path(__file__),path,ROOT/'tools/export_urdf.py',ROOT/'tools/kit_assembly.py',ROOT/'hardware/src/config.py',ROOT/'tools/data/kit_assembly_rear.json']
    passed=len(records)==len(names) and all(r['candidate_front_overlap_mm3']<.01 for r in records)
    result={'status':'ADDITIONAL_FULL_ASSEMBLY_RELATIONS_PASS' if passed else 'ADDITIONAL_FULL_ASSEMBLY_RELATIONS_FAIL','method':'independent Boolean against current KIT static poses; these pairs are outside compartment checks','source_sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},'candidate_front_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'pairs':records,'passed':passed}
    (out/'external-interface-check.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False,indent=2));return 0 if passed else 1
if __name__=='__main__':raise SystemExit(main())
