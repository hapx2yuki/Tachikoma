#!/usr/bin/env python3
"""LD-220保持候補の保存STL・組付け経路・干渉の有限検査。

公式図未寸法部は適合保証しない。候補内の幾何PASSと実機UNVERIFIEDを分離。
"""
import argparse, hashlib, json, sys
from dataclasses import replace
from pathlib import Path
import numpy as np
import trimesh
from manifold3d import Manifold, Mesh
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'hardware/src'))
import make_ld220_adapter as D
from lib import box, cyl, to_trimesh


def native(mesh):
    vertices=np.asarray(mesh.vertices,np.float32)
    faces=np.asarray(mesh.faces,np.uint32)
    if vertices.ndim != 2 or vertices.shape[1] != 3 or not np.isfinite(vertices).all():
        raise ValueError('mesh has non-finite or malformed vertices')
    if faces.ndim != 2 or faces.shape[1] != 3:
        raise ValueError('mesh has malformed faces')
    solid=Manifold(Mesh(vertices,faces))
    status=solid.status()
    volume=float(solid.volume())
    if status.name != 'NoError':
        raise ValueError(f'Manifold status is {status}')
    if not np.isfinite(volume) or volume <= 0:
        raise ValueError(f'Manifold volume is non-positive/non-finite: {volume}')
    return solid


def checked_shape(shape,label='shape'):
    """Reject invalid Manifold results instead of turning them into zero."""
    solid=shape if isinstance(shape,Manifold) else native(shape)
    status=solid.status()
    volume=float(solid.volume())
    if status.name != 'NoError':
        raise ValueError(f'{label}: Manifold status is {status}')
    if not np.isfinite(volume) or volume < -1e-6:
        raise ValueError(f'{label}: invalid signed volume {volume}')
    return solid


def intersection(a,b):
    result=checked_shape(a,'left operand') ^ checked_shape(b,'right operand')
    status=result.status()
    volume=float(result.volume())
    if status.name != 'NoError':
        raise ValueError(f'intersection: Manifold status is {status}')
    if not np.isfinite(volume) or volume < -1e-6:
        raise ValueError(f'intersection: invalid signed volume {volume}')
    # Only a documented sub-micron numerical roundoff may be represented as
    # zero. Negative/NaN volumes are never made harmless with max()/abs().
    return 0.0 if volume < 0 else volume


def run(output):
    output=Path(output).resolve();p=D.Candidate(**json.loads((output/'candidate.json').read_text())['parameters'])
    rows=[]
    def check(name,passed,**values):rows.append(dict(name=name,pass_geometry=bool(passed),**values))
    shapes={}
    for name,expected in D.build(p).items():
        path=output/(name+'.stl');mesh=trimesh.load(path,force='mesh')
        if not np.isfinite(mesh.vertices).all():
            raise ValueError(f'{path}: non-finite STL vertices')
        shapes[name]=native(mesh)
        n=len(mesh.split(only_watertight=False));diff=abs(mesh.volume-expected.volume())
        ok=mesh.is_watertight and mesh.is_winding_consistent and n==1 and mesh.volume>0 and np.isfinite(mesh.vertices).all() and diff<.01
        check(name+'保存後形状',ok,watertight=bool(mesh.is_watertight),body_count=n,
              volume_mm3=float(mesh.volume),volume_difference_mm3=float(diff),bounds_mm=mesh.bounds.tolist())
    cradle,cap,adapter=(shapes[n] for n in ['ld220_cradle','ld220_cap','ld220_horn_adapter'])
    case=D.case_envelope(p)
    for name,a,b in [('ケース/保持台',case,cradle),('ケース/蓋',case,cap),('保持台/蓋',cradle,cap),('ケーブル予約/保持台',D.cable_reservation(p),cradle)]:
        v=intersection(a,b);check(name,v<.001,intersection_mm3=v)
    # ホーンを外したケースの挿入。実コネクタ/曲率は未確認、予約直方体に限る。
    samples=[]
    for z in np.linspace(0,48,97):samples.append(intersection(case.translate([0,0,z]),cradle))
    check('ケース+Z挿入97位置',max(samples)<.001,max_intersection_mm3=max(samples),sample_step_mm=.5,
          continuous_argument='同一XYの上方開放ポケットなのでZを上げてもケース下端は底板へ入らない。丸い/突起の実ケースは別照合')
    samples=[intersection(cap.translate([0,0,z]),cradle+case) for z in np.linspace(0,12,25)]
    check('後端蓋+Z挿入25位置',max(samples)<.001,max_intersection_mm3=max(samples))
    # 6.4mmは上下突出の配分がない図からの名目仮置き。main外面を6.4とする。
    main_outer=p.main_projection
    adapter_at=adapter.translate([0,0,main_outer-p.horn_t-p.horn_clear])
    horn=D.horn_reference(p).translate([0,0,main_outer-p.horn_t-p.horn_clear])
    v=intersection(horn,adapter_at);check('金属円盤最大径と変換板',v<.001,intersection_mm3=v)
    for angle in (0,90,180,270):
        part=adapter_at.rotate([0,0,angle]);v=intersection(part,cradle+cap+case)
        check('変換板姿勢'+str(angle),v<.001,intersection_mm3=v)
    # 全角度の独立保守包絡。低い外周リムはr<=18、タブ全体は主ホーン外面以上。
    low_ring=cyl(p.horn_t+p.horn_clear,36/np.cos(np.pi/64)).translate([0,0,main_outer-(p.horn_t+p.horn_clear)/2])
    upper=cyl(p.plate_t,2*np.hypot(39,7)/np.cos(np.pi/64)).translate([0,0,main_outer+p.plate_t/2])
    v=intersection(low_ring+upper,cradle+cap)
    check('変換板360度保守包絡',v<.001,intersection_mm3=v,
          warning='保持台/蓋のみ。旧coxa/femur/全脚は別統合検査')
    # 六方向へ1mm移動する単純抜けの負例。実角丸/複合回転/強度は別。
    for axis in range(3):
        for sign in (-1,1):
            displacement=np.zeros(3);displacement[axis]=sign
            v=intersection(case.translate(displacement),cradle+cap)
            check('ケースの単純抜け拘束'+str(axis)+('/+' if sign>0 else '/-'),v>1,displaced_intersection_mm3=v)
    # 肉厚/ねじ長は体積差ではなく設計値と座の実体で計測。
    engagement=10-p.plate_t-p.gap
    check('蓋ねじの名目噛合長',engagement>=5,nominal_engagement_mm=engagement,
          fastener='既購入M3×10皿タップ×2、ケースねじ穴を利用しない',unverified='実皿頭形状、穴縮み、締付・引抜き強度')
    min_rear=p.rear-p.gap-(p.cap_screw_x+1.25)
    min_outer=p.cap_screw_x-1.25-p.outer_rear
    check('蓋ねじ下穴の後壁残肉',min(min_rear,min_outer)>=2.5,inner_wall_mm=min_rear,outer_wall_mm=min_outer)
    # The case opening is intentionally expanded by cradle() to
    # ``p.gap + 0.1``.  Probe the retained 3.1 mm bottom wall itself rather
    # than its just-outside face; the previous z=-case_h-gap-.05 probe sat in
    # the added clearance and reported a false missing-support failure.
    support=box(5,10,.1).translate([-24,0,p.bottom+3.0])
    lost=float((support-cradle).volume())
    check('ケース底の後部支持',lost<.001,required_support_patch_mm2=50,missing_support_mm3=max(0,lost))
    # 外周径について文章仕様と図の見かけの双方を独立比較する。
    diameter_sweep=[]
    for diameter in (20.,25.806):
        q=replace(p,horn_d=diameter);candidate=D.horn_adapter(q);tm=to_trimesh(candidate)
        v=intersection(D.horn_reference(q),candidate)
        diameter_sweep.append({'horn_diameter_mm':diameter,'intersection_mm3':v,'single_body':len(tm.split())==1,'volume_mm3':float(tm.volume),
                               'physical_fit':'UNVERIFIED: 実円盤とPCD/穴/ねじの照合が必要'})
    # パラメータを変えず狭い旧STD保持形状に押込むことをPASSにしない負例。
    bad_cradle=cradle+box(4,4,4).translate([-20,0,-20])
    bad=intersection(case,bad_cradle)
    check('ケース侵入負例を検出',bad>1,injected_intersection_mm3=bad)
    # Explicit malformed-input negative test. It must be rejected by the
    # same strict native() gate; accepting it as a zero-volume pass would hide
    # precisely the NaN/negative-volume failure mode this checker guards.
    try:
        malformed=trimesh.Trimesh(
            vertices=np.array([[0.,0.,0.],[1.,0.,0.],[0.,1.,np.nan]],dtype=float),
            faces=np.array([[0,1,2]],dtype=np.int64), process=False)
        native(malformed)
    except Exception as exc:
        check('壊れたSTL入力の拒否',True,negative_test=True,rejection=str(exc))
    else:
        check('壊れたSTL入力の拒否',False,negative_test=False,rejection='invalid mesh was accepted')
    paths=[Path(__file__).resolve(),ROOT/'hardware/src/make_ld220_adapter.py',ROOT/'hardware/src/lib.py',*output.glob('*.stl')]
    report={'status':'GEOMETRY_PASS_PHYSICAL_UNVERIFIED' if all(x['pass_geometry'] for x in rows) else 'GEOMETRY_FAIL',
            'geometry_pass':all(x['pass_geometry'] for x in rows),'checks':rows,'diameter_comparison':diameter_sweep,
            'units':'mm/mm3','conditions':'図のcase39.78×20.04×40、軸端10/主側6.4/従側5.0は候補。保持強度と適合は未測定',
            'strength_status':'UNVERIFIED: 形状の壁厚/経路だけ。引抜き/層間/温度/モータトルクによる実破断未検証',
            'fastener_inventory_status':'保持蓋24本/12台は既購入M3×10皿タップ100本から配分候補。ホーン付属ねじはサイズ/個数を要確認',
            'not_validated':['実サーボ/ホーンの3Dメッシュ','実ねじ締結/ケース熱/印刷誤差','既存coxa/femur/tibia互換','全脚/頭/ボディとの全姿勢干渉'],
            'source_sha256':{str(f.relative_to(ROOT)):hashlib.sha256(f.read_bytes()).hexdigest() for f in paths}}
    # 実候補メッシュの3投影。包絡の色を分け、未採用部品を実機写真に見せない。
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection
    fig=plt.figure(figsize=(13,4))
    assemblies=[(cradle,'#3887be',.65),(case,'#c83232',.18),(cap,'#607d8b',.7),(adapter_at,'#e9982c',.8)]
    for i,(elev,azim,title) in enumerate([(20,-55,'Assembled candidate'),(0,-90,'Side: case top z=0'),(90,-90,'Top: shaft origin')],1):
        ax=fig.add_subplot(1,3,i,projection='3d');ax.set_proj_type('ortho')
        for shape,color,alpha in assemblies:
            tm=to_trimesh(shape);patch=Poly3DCollection(tm.triangles,facecolor=color,alpha=alpha,edgecolor='none');ax.add_collection3d(patch)
        ax.set_xlim(-42,42);ax.set_ylim(-25,25);ax.set_zlim(-47,14);ax.set_box_aspect((84,50,61));ax.view_init(elev,azim)
        ax.set_xlabel('X mm');ax.set_ylabel('Y mm');ax.set_zlabel('Z mm');ax.set_title(title)
        if i==2:ax.set_yticks([]);ax.set_ylabel('')
        if i==3:ax.set_zticks([]);ax.set_zlabel('')
    fig.suptitle('LD-220MG / geometry candidate only / hardware fit and strength unverified')
    fig.tight_layout();fig.savefig(output/'candidate.png',dpi=170);plt.close(fig)
    (output/'checks.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(report['status'],len(rows),'checks')
    for x in rows:
        if not x['pass_geometry']:print('FAIL',x)
    return report

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,default=ROOT/'outputs/print-first-20260905/ld220-adapter');a=parser.parse_args()
    sys.exit(0 if run(a.output)['geometry_pass'] else 1)
