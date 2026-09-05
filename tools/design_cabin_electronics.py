#!/usr/bin/env python3
"""Cabin 電装室の候補を生成・検査する。既存model/STLを上書きしない。

寸法は候補設計用であり、実購入基板/プラグ/ケーブルの実測前は印刷リリース不可。
実機座標はシャーシ底面z=0。外観面は接合面開口と隠れたneck配線口以外を維持。
"""
from pathlib import Path
import json,hashlib,sys
import numpy as np
import trimesh
from manifold3d import Manifold,Mesh
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'hardware/src'))
import config as C
import kit_assembly as KIT
from lib import box,cyl_y,to_trimesh
MESH_TOLERANCE_MM=.015
OUT=ROOT/'docs/audits/20260905-round2/cabin-electronics'

def export_part(part,name):
    # Boolean後の微小な接触稜線はSTLの頂点統合で非多様体になるため、
    # 0.015mm以内の簡略化後にファイルを実際に再読して閉体を確認する。
    mesh=to_trimesh(part.set_tolerance(MESH_TOLERANCE_MM))
    path=OUT/(name+'.stl');mesh.export(path)
    reread=trimesh.load(path,force='mesh')
    if not reread.is_watertight:
        raise ValueError(f'{name}: exported STL is not watertight')
    return path

def native(m):
    return Manifold(Mesh(vert_properties=np.array(m.vertices,dtype=np.float32),tri_verts=np.array(m.faces,dtype=np.uint32)))
def bbox(lo,hi):
    lo,hi=np.array(lo,float),np.array(hi,float)
    return box(*(hi-lo)).translate((lo+hi)/2)
def source(name,axes=(),loc=(0,0,0)):
    m=trimesh.load(ROOT/'model'/f'{name}.stl',force='mesh')
    m.apply_translation(-m.bounds.mean(0));m.apply_scale(C.SCALE)
    if name in C.CABIN_POSES:
        m.apply_transform(KIT.cabin_transform(name))
        return m
    transform=np.eye(4)
    for axis,degree in axes:
        transform=transform@trimesh.transformations.rotation_matrix(np.radians(degree),np.eye(3)['xyz'.index(axis)])
    m.apply_transform(transform);m.apply_translation(loc)
    return m

def capsule(points,r):
    result=Manifold()
    for a,b in zip(points,points[1:]):
        result+=(Manifold.sphere(r,32).translate(a)+Manifold.sphere(r,32).translate(b)).hull()
    return result

def rear_crossover():
    return bbox([-37,-222,48],[37,-210,60])

def carrier(x):
    # ベルト固定用2段棚。USB/配線を後方へ出す開口付き。
    part=bbox([x-17,-209,30],[x+17,-206,99])
    for z in [30,66]:
        shelf=bbox([x-17,-209,z],[x+17,-144 if z==30 else -156,z+2.4])
        for dx in [-13,13]:
            for y in [-185,-160]:shelf-=bbox([x+dx-1.3,y-3,z-1],[x+dx+1.3,y+3,z+3.4])
        part+=shelf
    for dx in [-16.25,16.25]:
        part+=bbox([x+dx-.75,-209,30],[x+dx+.75,-144,64.5])
        part+=bbox([x+dx-.75,-209,64],[x+dx+.75,-156,94])
    part-=bbox([x-8,-210,34],[x+8,-205,60]) # USB plug / cable service opening
    for dx in [-10,10]:part-=cyl_y(5,C.M3_FREE).translate([x+dx,-207.5,94])
    return part

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    front=source('Cabin_Front_Blue');back=source('Cabin_Back_Blue_Repaired')
    fm,bm=native(front),native(back)
    alignment=json.loads((ROOT/'tools/data/cabin_peg_alignment_candidate.json').read_text())
    shells=fm+bm
    result={'status':'DESIGN_CANDIDATE_NOT_PRINT_RELEASE','units':'mm','frame':'chassis plate bottom z=0','mesh_simplification_tolerance_mm':MESH_TOLERANCE_MM,'source_sha256':{},'checks':{},'bays':[],'modules':[]}
    for p in [ROOT/'model/Cabin_Front_Blue.stl',ROOT/'model/Cabin_Back_Blue_Repaired.stl',ROOT/'model/Cabin_Peg_x2.stl',ROOT/'hardware/src/config.py',ROOT/'hardware/src/lib.py',ROOT/'tools/kit_assembly.py',ROOT/'tools/data/cabin_peg_alignment_candidate.json',Path(__file__)]:result['source_sha256'][str(p.relative_to(ROOT))]=hashlib.sha256(p.read_bytes()).hexdigest()
    result['original_solid_volume_mm3']={'front':fm.volume(),'back':bm.volume()}
    # R25で曲がる通線候補: 上段前端の空き→中心材→neck中心。
    arc_xz=[[25-25*np.sin(t),-156,52+25*np.cos(t)] for t in np.linspace(0,np.pi/2,25)]
    arc_yz=[[0,-131-25*np.cos(t),35-25*np.sin(t)] for t in np.linspace(0,np.pi/2,25)]
    points=[[32,-156,77]]+arc_xz+[[0,-156,35]]+arc_yz+[[0,-101,10]]
    wire=capsule(points,4)
    # 左右の配線を接合面の内側で渡す。既存Peg間の帯だけを使う。
    back_crossover=rear_crossover()
    carved_back=bm-back_crossover
    result['checks']['back_crossover_removed_mm3']=bm.volume()-carved_back.volume()
    for side_x in [-32,32]:
        crossing=cyl_y(6,8).translate([side_x,-211,54])
        wire+=crossing

    pocket=Manifold();supports=Manifold();trays=[]
    for x in [-32,32]:
        bounds=[([x-18,-214,27],[x+18,-143,65]),
                ([x-17.5,-214,64],[x+17.5,-155,95]),
                ([x-17.5,-214,27],[x+17.5,-192,99.5])]
        checks=[]
        for lo,hi in bounds:
            pocket+=bbox(lo,hi)
            safe=bbox([lo[0]-3.3,front.bounds[0,1]+.01,lo[2]-3.3],[hi[0]+3.3,hi[1]+3.3,hi[2]+3.3])
            checks.append({'bounds_mm':[lo,hi],'expanded_outside_original_mm3':max(0.,(safe-fm).volume())})
        result['bays'].append({'center_x':x,'stepped_pockets':checks,'wall_expansion_mm':3.3})
        # 上側の2本のM3は、天井へ一体化する固定座へ後方からねじ込む。
        for dx in [-10,10]:
            support=bbox([x+dx-4,-205,90.5],[x+dx+4,-194,101])
            support-=cyl_y(10,C.M3_TAP).translate([x+dx,-200.5,94])
            supports+=support
        trays.append(carrier(x)-wire)
    carved=(fm-pocket)+supports
    result['checks']['support_union_added_outside_original_mm3']=(carved-fm).volume()
    result['checks']['carved_volume_mm3']=carved.volume()
    # 既存の下横Peg穴と上縦Peg穴の壁を保護。検査箱自体は切らない。
    protect=bbox([-34,-214,-3],[34,-198,23])+bbox([-13,-214,60],[13,-198,128])
    result['checks']['peg_protection_removed_mm3']=((fm-carved)^protect).volume()
    result['checks']['back_peg_protection_removed_mm3']=((bm-carved_back)^protect).volume()
    neck_face=bbox([-18,-108,-5],[18,-104,25])
    result['checks']['neck_flange_region_removed_without_wire_mm3']=((fm-carved)^neck_face).volume()
    # 既存2 Pegとソケットの独立検査済み配置を使用 (Back高さ補正を含む)。
    result['peg_alignment']=alignment
    for item in alignment['pegs']:
        peg_mesh=trimesh.load(ROOT/'model'/item['source_stl'],force='mesh')
        peg_mesh.apply_transform(item['raw_to_chassis_matrix'])
        candidate=native(peg_mesh)
        result['checks'][f"peg_{item['name']}"]={'overlap_mm3':max(0.,(candidate^shells).volume()),'carved_shell_overlap_mm3':max(0.,(candidate^(carved+bm)).volume())}
        export_part(candidate,f"candidate_peg_{item['name']}")
    # 棚そのものと後方直線挿入全経路の交差を実メッシュで測る。
    for idx,tray in enumerate(trays):
        static=(tray^carved).volume();worst=(static,0)
        sweep=tray.minkowski_sum(box(.001,70,.001).translate([0,-35,0]))
        swept_overlap=max(0.,(sweep^carved).volume())
        for d in np.arange(0,71,1):
            collision=(tray.translate([0,-float(d),0])^carved).volume()
            if collision>worst[0]:worst=(collision,float(d))
        result['checks'][f'tray_{idx}']={'static_overlap_mm3':static,'continuous_insertion_overlap_mm3':swept_overlap,'insertion_max_overlap_mm3':worst[0],'at_rear_shift_mm':worst[1],'body_count':len(to_trimesh(tray).split(only_watertight=False)),'volume_mm3':tray.volume()}
        export_part(tray,f'candidate_carrier_{idx}')
    # 物理基板本体と配線/端子の予約体積。HENGEサイズは未確認のため適合保証しない。
    envelopes=[('ESP32_with_vertical_headers',[-47,-203,34],[-17,-145,62],'58x28 PCB plus provision, exact plugs UNVERIFIED'),
               ('UBEC_reserved',[17,-203,34],[47,-144,62],'HOBBYWING45x20x16.2 fits; purchased HENGE UNVERIFIED'),
               ('DFPlayer_reserved',[-45,-205,70],[-19,-179,90],'purchased header dimensions UNVERIFIED'),
               ('MAX98357A_reserved',[-44,-178,70],[-20,-156,90],'Adafruit board19.4x17.8 plus terminals provision'),
               ('5V_regulator_reserved',[18,-199,70],[46,-161,90],'mini560 exact version/heatsink UNVERIFIED')]
    parts=[]
    sweep_kernel=box(.001,70,.001).translate([0,-35,0])
    for name,lo,hi,note in envelopes:
        shape=bbox(lo,hi);parts.append(shape)
        result['modules'].append({'name':name,'bounds_mm':[lo,hi],'note':note,'shell_overlap_mm3':(shape^carved).volume(),'carrier_overlap_mm3':sum((shape^t).volume() for t in trays),'continuous_insertion_overlap_mm3':max(0.,(shape.minkowski_sum(sweep_kernel)^carved).volume())})
    result['checks']['wire_module_overlap_mm3']={name: max(0.,(wire^shape).volume()) for (name,*_),shape in zip(envelopes,parts)}
    result['checks']['wire_carrier_overlap_mm3']=sum(max(0.,(wire^tray).volume()) for tray in trays)
    usb=bbox([-39,-231,37],[-25,-202,49])
    result['checks']['usb_service_cap_removed_overlap_mm3']=(usb^carved).volume()+sum((usb^t).volume() for t in trays)
    result['checks']['usb_with_rear_cap_overlap_mm3']=(usb^bm).volume()
    wire_cabin=carved-wire
    result['wire_candidate']={'diameter_mm':8,'bend_radius_mm':25,'centerline':points,'removed_mm3':carved.volume()-wire_cabin.volume(),'peg_region_removed_mm3':((carved-wire_cabin)^protect).volume(),'neck_interface_removed_mm3':((carved-wire_cabin)^neck_face).volume(),'status':'REQUIRES_NECK_REINFORCEMENT_AND_BUNDLE_MEASUREMENT','neck_post_I_ratio_12mm':1-(8/12)**4,'neck_post_area_ratio_12mm':1-(8/12)**2}
    export_part(carved_back,'candidate_cabin_back_crossover');export_part(carved,'candidate_cabin_front_pockets');export_part(wire_cabin,'candidate_cabin_front_with_wire_route');export_part(wire,'candidate_wire_route')
    # 全STLは検討用。印刷/現行置換の処理には接続しない。
    result['checks']['candidate_front_watertight']=to_trimesh(carved).is_watertight
    result['checks']['candidate_front_body_count']=len(to_trimesh(carved).split(only_watertight=False))
    (OUT/'candidate-report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ['source_sha256','wire_candidate']},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
