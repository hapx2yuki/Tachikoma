from pathlib import Path
import json,numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
plt.rcParams['font.family']='Hiragino Sans'
B=Path('docs/audits/20260905-round2/simulation')
base=json.loads((B/'stress-final/contact_vhacd.json').read_text())
shoe=json.loads((B/'foot-candidate-final/toe_shoe_forward_mu0.6.json').read_text())
long=json.loads((B/'stress-final/long_180.json').read_text())
fig,axes=plt.subplots(2,2,figsize=(13,8))
for j,color,label in [(base,'#55778b','現仕様の硬いトゥ'),(shoe,'#008476','TPU靴候補（摩擦係数0.6）')]:
 ts=j['timeseries'];t=np.array([r['time'] for r in ts]);rpy=np.array([r['rpy_deg'] for r in ts])
 axes[0,0].plot(t,rpy[:,0],color=color,label=label)
 fraction=j['tpu_support']['fraction'];axes[0,1].bar(label,fraction*100,color=color)
 axes[1,0].plot(t,[r['positive_power_W'] for r in ts],color=color,alpha=.8,label=label)
axes[0,0].set(title='X軸周りの傾き（前後方向）',xlabel='経過時間 / 秒',ylabel='ロール角 / °');axes[0,0].legend(fontsize=8)
axes[0,1].set(title='TPUが受けた床荷重の割合（時間積分）',ylabel='TPUの垂直力積 / %',ylim=(0,105));axes[0,1].axhline(95,color='#a94235',linestyle='--',label='比較基準95%');axes[0,1].legend(fontsize=8)
axes[1,0].set(title='正の機械仕事率（10Hz抽出）',xlabel='経過時間 / 秒',ylabel='W');axes[1,0].legend(fontsize=8)
t=np.array([r['time'] for r in long['timeseries']]);p=np.array([r['base_pos'] for r in long['timeseries']]);axes[1,1].plot(p[:,0],p[:,1],color='#55778b');axes[1,1].set(title='180秒の前進指令：本体の軌跡',xlabel='世界座標X / m',ylabel='世界座標Y / m');axes[1,1].axis('equal')
for ax in axes.ravel():ax.grid(alpha=.2)
fig.suptitle('条件付きの剛体比較：実C++の20軸出力、自己衝突は無効\n組立干渉は残存。TPUの摩擦・圧縮特性、サーボ制御特性は未測定。',fontsize=12)
fig.tight_layout(rect=(0,0,1,.92));fig.savefig(B/'simulation-results.png',dpi=160)
