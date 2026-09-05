#!/usr/bin/env python3
"""Cabin候補の実メッシュ断面とラッチ試験結果を描画。"""
import json
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
from matplotlib.patches import Rectangle
import trimesh
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/audits/20260905-round2/cabin-electronics'
font=next(Path('/System/Library/Fonts').glob('*角* W3.ttc'),None)
if font:plt.rcParams['font.family']=FontProperties(fname=str(font)).get_name()
plt.rcParams.update({'font.size':11,'axes.spines.top':False,'axes.spines.right':False,'figure.facecolor':'#f5f7fb','axes.facecolor':'#f5f7fb','savefig.facecolor':'#f5f7fb'})
report=json.loads((OUT/'candidate-report.json').read_text());latch=json.loads((OUT/'latch-report.json').read_text())
COLORS=['#39a9d8','#f7b74c','#77c59c','#b591d9','#e7869a']
def mesh(name):return trimesh.load(OUT/(name+'.stl'),force='mesh')
def section(ax,m,axis,value,color,lw=1):
 n=np.eye(3)[axis];o=np.zeros(3);o[axis]=value;s=m.section(plane_origin=o,plane_normal=n)
 if s:
  keep=[i for i in range(3) if i!=axis]
  for line in s.discrete:ax.plot(line[:,keep[0]],line[:,keep[1]],color=color,lw=lw)
def draw(ax,m,color,alpha=1):
 ax.add_collection3d(Poly3DCollection(m.triangles,facecolors=color,edgecolors=color,alpha=alpha,linewidths=0,rasterized=True,shade=True))
fig=plt.figure(figsize=(15,8));gs=fig.add_gridspec(2,3,height_ratios=[1,1],wspace=.32,hspace=.38)
ax=fig.add_subplot(gs[:,0]);ax.set_title('後方から見た断面  Y = −181 mm',loc='left',fontweight='bold')
section(ax,mesh('candidate_cabin_front_with_wire_route'),1,-181,'#33516d',1.1)
for i in range(2):section(ax,mesh(f'candidate_carrier_{i}'),1,-181,'#2f855a',1.2)
for item,color in zip(report['modules'],COLORS):
 lo,hi=np.array(item['bounds_mm'])
 if lo[1]<=-181<=hi[1]:ax.add_patch(Rectangle((lo[0],lo[2]),hi[0]-lo[0],hi[2]-lo[2],facecolor=color,alpha=.75));ax.text((lo[0]+hi[0])/2,(lo[2]+hi[2])/2,'ESP32' if 'ESP32' in item['name'] else 'UBEC' if 'UBEC' in item['name'] else 'DFPlayer' if 'DFPlayer' in item['name'] else '5 V',ha='center',va='center',fontsize=9)
ax.set(xlabel='X [mm]',ylabel='Z [mm]',xlim=(-90,90),ylim=(-48,156));ax.set_aspect('equal');ax.grid(alpha=.18)
ax.text(-88,-44,'外壁余裕：3.2 mm以上（接合面を除く）\n内部棚・基板は後方へ70 mm直線で抜去可能',fontsize=9,va='bottom')
ax=fig.add_subplot(gs[0,1:],projection='3d');ax.set_title('分解配置：元の外形を維持する内部加工案',loc='left',fontweight='bold')
draw(ax,mesh('candidate_cabin_front_with_wire_route'),'#76b8dd')
back=mesh('candidate_cabin_back_with_latches');back.apply_translation([0,-105,0]);draw(ax,back,'#90b2ce')
for i in range(2):
 tray=mesh(f'candidate_carrier_{i}');tray.apply_translation([0,-65,0]);draw(ax,tray,'#55ad8a')
for item,color in zip(report['modules'],COLORS):
 lo,hi=np.array(item['bounds_mm']);m=trimesh.creation.box(hi-lo);m.apply_translation((lo+hi)/2+[0,-65,0]);draw(ax,m,color)
for name in ['lower','upper']:draw(ax,mesh('candidate_latch_peg_'+name),'#edaf4d')
ax.set(xlim=(-100,100),ylim=(-375,-100),zlim=(-48,156));ax.set_box_aspect((200,275,204),zoom=1.55);ax.view_init(23,-40);ax.set_axis_off()
ax=fig.add_subplot(gs[1,1]);ax.set_title('交換ペグ：後半に2本の板ばね',loc='left',fontweight='bold')
# 片側爪中央でY-Z断面。表示はペグの中心からの座標へ戻す。
p=mesh('candidate_latch_peg_lower');p.apply_translation([0,211.0932502746582,-9.95]);d=mesh('candidate_latch_peg_lower_deflected');d.apply_translation([0,211.0932502746582,-9.95]);section(ax,p,0,11.5,'#bd7800',2);section(ax,d,0,11.5,'#c34a5d',1.3)
ax.set(xlabel='接合面からのY [mm]',ylabel='ペグ中心からのZ [mm]',xlim=(-10,2),ylim=(4.5,11.5));ax.set_aspect('equal');ax.grid(alpha=.18)
ax.text(-9.7,5,'金：無荷重 / 赤：0.85 mm押下\n前半は元の形状を維持しFrontだけ接着',fontsize=9)
ax=fig.add_subplot(gs[1,2]);ax.set_title('蓋の抜去とラッチの必要変形',loc='left',fontweight='bold')
for record,color,label in zip(latch['pegs'],['#337da6','#bd7800'],['下ペグ','上ペグ']):
 a=record['travel_samples'];ax.plot([r['cap_rear_shift_mm'] for r in a],[r['required_tip_deflection_mm'] for r in a],label=label,color=color,lw=2)
ax.set(xlabel='Backを後ろへ移動 [mm]',ylabel='自由端変形 [mm]',ylim=(-.03,.87));ax.grid(alpha=.18);ax.legend(frameon=False,loc='upper right')
fig.suptitle('Cabin 電装室・着脱する背面蓋の検討案',x=.065,y=.98,ha='left',fontsize=21,fontweight='bold')
fig.text(.065,.925,'実メッシュ検査済み / 部品の実測・保持力・耐久試験は未実施 / 現行の印刷STLは未置換',fontsize=11,color='#596779')
fig.savefig(OUT/'candidate-overview.png',dpi=160,bbox_inches='tight');plt.close(fig)
# 対話的な3D閲覧用（閉状態）。外殻は半透明で内部を確認できる。
scene=trimesh.Scene()
for name,color in [('candidate_cabin_front_with_wire_route',[70,153,201,65]),('candidate_cabin_back_with_latches',[100,160,205,55]),('candidate_carrier_0',[70,173,125,255]),('candidate_carrier_1',[70,173,125,255]),('candidate_latch_peg_lower',[240,174,65,255]),('candidate_latch_peg_upper',[240,174,65,255])]:
 m=mesh(name);mat=trimesh.visual.material.PBRMaterial(baseColorFactor=color,alphaMode='BLEND' if color[3]<255 else 'OPAQUE',doubleSided=True);m.visual=trimesh.visual.TextureVisuals(material=mat);scene.add_geometry(m,node_name=name)
for item,color in zip(report['modules'],COLORS):
 lo,hi=np.array(item['bounds_mm']);m=trimesh.creation.box(hi-lo);m.apply_translation((lo+hi)/2);m.visual.face_colors=matplotlib.colors.to_rgba(color);m.visual.face_colors=np.array(matplotlib.colors.to_rgba(color))*255;scene.add_geometry(m,node_name=item['name'])
scene.export(OUT/'candidate-assembly.glb')
print(OUT/'candidate-overview.png')
