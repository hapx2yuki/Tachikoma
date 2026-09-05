#!/usr/bin/env python3
"""ガード受け座候補と入口の外装変化を実断面で可視化する。"""
from pathlib import Path
import sys
import numpy as np,trimesh
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'tools'),str(ROOT/'hardware/src')]
import export_urdf as E
D=ROOT/'docs/audits/20260905-round2/guard-seat-candidates'
f=next(Path('/System/Library/Fonts').glob('*角* W3.ttc'),None)
if f:plt.rcParams['font.family']=FontProperties(fname=str(f)).get_name()
plt.rcParams.update({'font.size':10,'figure.facecolor':'#f7f8fa','axes.facecolor':'#f7f8fa'})
def section(ax,m,color,label):
 s=m.section([0,1,0],[0,0,0])
 if s:
  for i,line in enumerate(s.discrete):ax.plot(line[:,0],line[:,2],color=color,lw=1.2,label=label if i==0 else None)
fig,axes=plt.subplots(2,3,figsize=(14,9))
for row,(kind,guard,shell,bone,opened) in enumerate([('femur','Leg_Thigh_Guard_Blue_x4#FL','thigh_cap','femur_link','open_positive_z'),('tibia','Leg_Shin_Guard_Grey_x4#FL','shin_shell','tibia_link','open_positive_x')]):
 ps={n:m for m,c,n in E.leg_parts('FL')[kind]};g=ps[guard];s=ps[shell]
 for col,(title,m) in enumerate([('変更前',s),('局所受け座：挿入不可',trimesh.load(D/(kind+'_pocket_clear0_10_simplified_link_candidate.stl'))),('挿入口を開放：外面を切除',trimesh.load(D/(kind+'_'+opened+'_clear0_10_simplified_link_candidate.stl')))]):
  ax=axes[row,col];section(ax,ps[bone],'#9ba4ad','骨格');section(ax,m,'#976846','支持殻');section(ax,g,'#168baa','原形ガード')
  ax.set_title(('大腿 / ' if row==0 else '脛 / ')+title,fontweight='bold');ax.set_xlabel('リンク X [mm]');ax.set_ylabel('リンク Z [mm]');ax.set_aspect('equal');ax.grid(alpha=.15)
  if row==0:ax.set(xlim=(3,49),ylim=(6,35))
  else:ax.set(xlim=(-17,32),ylim=(-111,-67))
  if col==0:ax.legend(loc='lower right',frameon=False,fontsize=9)
fig.suptitle('装飾ガードを保存した受け座の有限比較（Y = 0 mm 実断面）',x=.055,ha='left',fontsize=18,fontweight='bold')
fig.text(.055,.025,'局所受け座は26方向すべてで初動の干渉を確認。開放案では可視面の切除／脛の分離が生じる。全案未採用、実機・接着・印刷強度は未確認。',fontsize=10)
fig.tight_layout(rect=[.025,.05,.995,.95]);fig.savefig(D/'guard-seat-sections.png',dpi=160);plt.close(fig)
