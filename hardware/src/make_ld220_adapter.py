"""LD-220MG用の耳を使わない保持台・付属円盤ホーン変換板の有限候補。

通常STL/profileへ自動適用しない。軸=Z、ケース上端Z=0、出力軸XY=(0,0)。
公式寸法図のケース39.78×20.04×40.00、両ホーン込み51.40を区別する。
軸端距離/円盤径/穴径/ねじは実測未完。defaultsは試作候補で適合保証でない。
本体は+Zから挿入（ホーンを外す）、後部蓋は+Zから、ホーンは最後に付ける。
"""
from dataclasses import dataclass, asdict
from pathlib import Path
import argparse, hashlib, json
from lib import box, cyl, to_trimesh

ROOT = Path(__file__).resolve().parents[2]
DIMENSION_SOURCE = 'https://www.hiwonder.com/cdn/shop/products/8_b3bce00e-fee8-45b0-82c7-698ba062b0cf.jpg?v=1630310524+1200w'
PRODUCT_SOURCE = 'https://www.hiwonder.com/products/ld-220mg'

@dataclass(frozen=True)
class Candidate:
    case_l: float = 39.78
    case_w: float = 20.04
    case_h: float = 40.0
    total_with_horns: float = 51.4
    main_projection: float = 6.4  # 上下配分は未寸法の名目候補
    shaft_from_end: float = 10.0  # 図に数値なし。旧STDの値を読み込まない。
    gap: float = .30
    wall: float = 3.2
    horn_d: float = 25.806  # 本文1.016inch。図の見かけφ20との矛盾を検査する。
    horn_t: float = 2.0  # 図の2mmが主/従ホーンどちらかを実物で確認
    horn_clear: float = .20
    plate_t: float = 4.0
    shaft_access_d: float = 7.0
    radial_hole_d: float = 2.7  # M2.5を想定する候補。実ホーンねじ寸法未確認。
    pcd_min: float = 14.0
    pcd_max: float = 20.0

    @property
    def rear(self): return self.shaft_from_end - self.case_l
    @property
    def cap_screw_x(self): return self.rear - 4.72
    @property
    def outer_rear(self): return self.cap_screw_x - 4.0
    @property
    def cap_front(self): return -19.0
    @property
    def outer_y(self): return self.case_w / 2 + self.gap + self.wall
    @property
    def bottom(self): return -self.case_h - self.gap - self.wall
    @property
    def cap_z(self): return self.gap


def x_box(x0, x1, width, z0, z1):
    return box(x1-x0,width,z1-z0).translate([(x0+x1)/2,0,(z0+z1)/2])


def case_envelope(p=Candidate(), gap=0.0):
    return box(p.case_l+2*gap,p.case_w+2*gap,p.case_h+2*gap).translate([
        (p.rear+p.shaft_from_end)/2,0,-p.case_h/2])


def cable_reservation(p=Candidate()):
    # コネクター/曲率は要実測。+X側全高を抜き、挿入でケーブルを挟まない。
    return x_box(p.shaft_from_end-.1,p.shaft_from_end+18,10,-p.case_h-.1,p.gap+1)


def cap(p=Candidate()):
    m=x_box(p.outer_rear,p.cap_front,2*p.outer_y,p.cap_z,p.cap_z+p.plate_t)
    for y in (-7.,7.):
        m-=cyl(20,3.2).translate([p.cap_screw_x,y,p.cap_z])
        # M3×10皿タップ。3.2→6.4径、90度皿の候補。最終は現物頭部で合わせる。
        from manifold3d import Manifold
        sink=Manifold.cylinder(1.6,1.6,3.2,64,False)
        m-=sink.translate([p.cap_screw_x,y,p.cap_z+p.plate_t-1.6])
    return m


def cradle(p=Candidate()):
    m=x_box(p.outer_rear,p.shaft_from_end+p.gap+p.wall,2*p.outer_y,p.bottom,0)
    # Keep an additional 0.1 mm machining/serialization allowance inside the
    # nominal 0.3 mm design gap. This gives the final STL a measurable margin
    # when the occupied case envelope is expanded by 0.3 mm.
    fit_gap = p.gap + 0.1
    # 開口を上へ連続させる。上部ホーンは外してからケースだけ挿入。
    m-=x_box(p.rear-fit_gap,p.shaft_from_end+fit_gap,p.case_w+2*fit_gap,
             -p.case_h-fit_gap,10)
    # Leave the documented fit gap around the cable exit as well as around the
    # rectangular case.  A raw reservation subtraction is tangent at the
    # outlet and gives no allowance for a 0.3 mm print/connector error.
    m-=cable_reservation(p).minkowski_sum(box(2*fit_gap,2*fit_gap,2*fit_gap))
    # 従軸/ホーンを後から扱う底の開口。φ30は確定値でなく最大径候補+余裕。
    m-=cyl(12,30).translate([0,0,p.bottom+2])
    # 放熱/目視窓。四隅は反力を受ける平面として残す。
    for sy in (-1,1):
        m-=box(22,12,22).translate([-10,sy*p.outer_y,-20])
    # 蓋の2本のねじは既購入M3×10皿タップ。6mmねじ込みを狙う。
    for y in (-7.,7.):
        m-=cyl(9,2.5).translate([p.cap_screw_x,y,-4])
    return m


def radial_slot(p=Candidate()):
    lo,hi=p.pcd_min/2,p.pcd_max/2
    return (cyl(30,p.radial_hole_d).translate([lo,0,0])
            +cyl(30,p.radial_hole_d).translate([hi,0,0])
            +box(hi-lo,p.radial_hole_d,30).translate([(lo+hi)/2,0,0]))


def horn_adapter(p=Candidate()):
    t=p.horn_t+p.horn_clear+p.plate_t
    # 外周径36、+X方向の一体リンク用タブ。既存STDの単腕ホーン穴は使わない。
    m=cyl(t,36).translate([0,0,t/2]) + x_box(12,39,14,p.horn_t+p.horn_clear,t)
    pocket_h=p.horn_t+p.horn_clear
    m-=cyl(pocket_h+1,p.horn_d+2*p.horn_clear).translate([0,0,(pocket_h-1)/2])
    m-=cyl(30,p.shaft_access_d)
    for a in (0,90,180,270):m-=radial_slot(p).rotate([0,0,a])
    # 下流印刷リンクへの2本の候補穴。可能なら母材と一体化し追加ねじを減らす。
    for x in (25,34):m-=cyl(30,2.5).translate([x,0,0])
    return m


def horn_reference(p=Candidate()):
    # 穴は確定しないので保守的な円盤包絡。参考形状を製造数へ含めない。
    return cyl(p.horn_t,p.horn_d).translate([0,0,p.horn_t/2+p.horn_clear])


def build(p=Candidate()):
    return {'ld220_cradle':cradle(p),'ld220_cap':cap(p),
            'ld220_horn_adapter':horn_adapter(p)}


def save(p, output):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    import lib
    old=lib.STL_DIR
    try:
        lib.STL_DIR=output
        for name,shape in build(p).items():lib.export(shape,name)
    finally:lib.STL_DIR=old
    data={'status':'CANDIDATE_UNVERIFIED','parameters':asdict(p),
          'coordinate_system':'Z軸=サーボ出力軸、XY原点=軸、ケース上端Z0、ケースは-Zへ40mm',
          'nominal_main_projection_mm':p.main_projection,'nominal_assistant_projection_mm':p.total_with_horns-p.case_h-p.main_projection,'individual_projection_status':'UNVERIFIED: 図の51.4−40=11.4だけ確定。上下配分6.4/5は候補',
          'main_horn_outer_face_frame':{
              'canonical_main_outer_z_mm':0.0,
              'case_front_z_mm':-p.main_projection,
              'case_rear_z_mm':-p.case_h-p.main_projection,
              'case_translation':[0,0,-p.main_projection],
              'case_bounds_mm':[[p.rear,-p.case_w/2,-p.case_h-p.main_projection],[p.shaft_from_end,p.case_w/2,-p.main_projection]],
              'assistant_outer_z_mm':-p.total_with_horns,
              'assistant_inner_z_mm':-p.total_with_horns+p.horn_t,
              'contract_status':'DESIGN_CANDIDATE_PHYSICAL_DIMENSIONS_UNVERIFIED'},
          'documented_dimensions_mm':{'case':[39.78,20.04,40.0],'with_horns_axis_length':51.4},
          'sources':[DIMENSION_SOURCE,PRODUCT_SOURCE],
          'unverified':['出力軸のケース端からの距離10mm','主円盤の径:本文25.806mmと図の見かけ20mmの不一致',
                        'ホーン厚さ/軸方向段差/穴PCD/ねじ径とピッチ/付属ねじ数',
                        'ケース角/底突出/配線口とコネクター/曲げ半径の実包絡',
                        'PLAの温度/層間強度/ねじ引抜き/6V連続保持力','全脚への取付/動的干渉/全機支持'],
          'purchase_plan':{'new_hardware':False,'cap_fasteners_per_servo':2,'cap_fastener':'既購入M3×10皿タップ、候補24本/12台。旧枠から配分を更新',
                           'horn_fasteners':'付属の金属ホーン用ねじを優先。実物径/長/本数未確認なので追加ねじ数を確定しない'},
          'assembly_order':['金属主/従ホーンを外す（中心ねじを保管）','ケーブルを+X溝へ逃がしケースを+Zから挿入',
                            '後端蓋を上から載せM3×10皿タップ2本で固定','従ホーン/主ホーンを元の中心ねじで取付',
                            'PCDとねじを実測し変換板を金属ホーンへねじ止め','荷重前に保持/引抜き/回転干渉を台上で確認'],
          'interface':{'cap_screw_centers':[[p.cap_screw_x,y,p.cap_z+p.plate_t] for y in (-7.,7.)],
                       'cradle_merge_surface':'外側/後壁を新フレームに一体化。既存タブ穴とは互換でない',
                       'adapter_link_hole_centers':[[x,0,0] for x in (25,34)],'horn_pcd_adjustment_mm':[p.pcd_min,p.pcd_max]},
          'source_sha256':{str(f.relative_to(ROOT)):hashlib.sha256(f.read_bytes()).hexdigest() for f in [Path(__file__),ROOT/'hardware/src/lib.py']}}
    (output/'candidate.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    return data

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'outputs/print-first-20260905/ld220-adapter')
    parser.add_argument('--horn-diameter',type=float,default=25.806)
    args=parser.parse_args();save(Candidate(horn_d=args.horn_diameter),args.output)
