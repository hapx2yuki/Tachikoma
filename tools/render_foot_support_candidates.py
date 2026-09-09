#!/usr/bin/env python3
"""150%の実印刷仕様に揃えた足支持候補を同一縮尺で側面比較する。"""
from pathlib import Path
import sys,json
import numpy as np,trimesh
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.collections import PolyCollection
from matplotlib.font_manager import FontProperties
from matplotlib import font_manager
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
import kit_assembly as K

def main():
    folder=ROOT/'docs/audits/20260905-round2/foot-support-candidates'
    p=Path('/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc')
    if p.exists():font_manager.fontManager.addfont(str(p));plt.rcParams['font.family']=FontProperties(fname=p).get_name()
    plt.rcParams['axes.unicode_minus']=False
    foot=trimesh.load(ROOT/'hardware/stl/leg_foot_bored.stl');pad=trimesh.load(ROOT/'hardware/stl/foot_pad.stl')
    toes=trimesh.util.concatenate([K.oriented_mesh(p) for p in K.load_placements() if p.part=='Leg_Toe_Black_x12' and p.instance.startswith('FR_')])
    sphere=trimesh.load(folder/'spherical_support_R15_candidate.stl')
    shoes=trimesh.util.concatenate([trimesh.load(folder/f'FR_{i}_shoe_fitted_candidate.stl') for i in range(3)])
    fig,axes=plt.subplots(1,3,figsize=(14,6.4),sharex=True,sharey=True)
    tilt=np.radians(-21.222)
    for ax,title,support in zip(axes,['現行の足裏','中央の球面足裏案','トゥ下面の靴案'],[pad,sphere,shoes]):
        for m,color,label in [(toes,'#383e45','印刷150%トゥ'),(foot,'#b6bbc2','既存の甲'),(support,'#1889a1','TPU候補')]:
            v=m.vertices;v=np.c_[v[:,0]*np.cos(tilt)+v[:,2]*np.sin(tilt),-v[:,0]*np.sin(tilt)+v[:,2]*np.cos(tilt)]
            ax.add_collection(PolyCollection(v[m.faces],facecolor=color,edgecolor='none',label=label))
        ax.set_title(title,loc='left',fontsize=13,fontweight='bold');ax.axhline(0,color='#a0a4aa',ls=':',lw=.7)
        ax.set_aspect('equal');ax.set_xlim(-39,49);ax.set_ylim(-59,16);ax.spines[['top','right']].set_visible(False);ax.set_xlabel('脚の長手方向 [mm]')
    axes[0].set_ylabel('足原点からの高さ [mm]')
    axes[0].text(-36,-50,'トゥが先に接地\n現足裏によるTPU支持は不成立',color='#a42e23',fontsize=11)
    axes[1].text(-36,-55,'従来足裏より36.59 mm深い\n体高・外観・圧縮の再設計が必要',fontsize=10)
    axes[2].text(-36,-50,'下面1.5 mm、剛体余裕1.64 mm\n根元の座・接着・疲労を別途検証',fontsize=10)
    fig.suptitle('足支持案の比較 — 後脚保持姿勢相当、同一縮尺、全案は比較用',fontsize=15)
    axes[2].legend(loc='upper right',fontsize=9,frameon=False)
    fig.tight_layout(rect=(0,0,1,.94));fig.savefig(folder/'comparison.png',dpi=150);plt.close(fig)
if __name__=='__main__':main()
