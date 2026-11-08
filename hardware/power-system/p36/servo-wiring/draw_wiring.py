# Electrical connection schematic only; no CAD geometry or manufacturing data changes.
from PIL import Image,ImageDraw,ImageFont
from pathlib import Path
import json,html,csv,math
O=Path(__file__).parent
D=json.loads((O.parent/'P36_PCB_FAB_PREP/route_candidate.json').read_text())
lookup={p['id']:p for p in D['pads']}
for ch in range(12):assert lookup[f'J_IN.PWM{ch}']['net']==lookup[f'J{ch}.S']['net']==f'PWM_L{ch}'
W,H=1800,1580
im=Image.new('RGB',(W,H),'#f6f8fb');dr=ImageDraw.Draw(im)
fontpath=next(p for p in Path('/System/Library/Fonts').glob('*W3.ttc') if '角' in p.name)
boldpath=next(p for p in Path('/System/Library/Fonts').glob('*W6.ttc') if '角' in p.name)
svg=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',f'<rect width="{W}" height="{H}" fill="#f6f8fb"/>']
ink='#182a40';muted='#526479';blue='#087dad';red='#d63d3d';yellow='#ba8700';gray='#63717e';green='#28744a'
def line(points,fill,width=3):
 dr.line(points,fill=fill,width=width,joint='curve');svg.append('<polyline points="'+' '.join(f'{x},{y}' for x,y in points)+f'" fill="none" stroke="{fill}" stroke-width="{width}" stroke-linejoin="round"/>')
def rect(box,fill,outline='#c3cfdd',width=2,r=12):
 dr.rounded_rectangle(box,radius=r,fill=fill,outline=outline,width=width);x,y,x2,y2=box;svg.append(f'<rect x="{x}" y="{y}" width="{x2-x}" height="{y2-y}" rx="{r}" fill="{fill}" stroke="{outline}" stroke-width="{width}"/>')
def text(x,y,s,size=22,color=ink,bold=False,anchor='left',bg=None):
 f=ImageFont.truetype(str(boldpath if bold else fontpath),size);b=dr.textbbox((0,0),s,font=f);tw=b[2]-b[0]
 if anchor=='center':x-=tw/2
 if anchor=='right':x-=tw
 if bg:rect((x-3,y-2,x+tw+3,y+size+4),bg,bg,0,0)
 dr.text((x,y),s,font=f,fill=color,anchor='lt')
 svg.append(f'<text x="{x}" y="{y}" dominant-baseline="text-before-edge" font-family="Hiragino Kaku Gothic ProN,Hiragino Sans,Noto Sans JP,sans-serif" font-size="{size}" font-weight="{600 if bold else 400}" fill="{color}">{html.escape(s)}</text>')
def dot(x,y,c,r=5):
 dr.ellipse((x-r,y-r,x+r,y+r),fill=c);svg.append(f'<circle cx="{x}" cy="{y}" r="{r}" fill="{c}"/>')
def bus(x,start,end,skips,c):
 cur=start
 for y in sorted(skips):
  if y-7>cur:line([(x,cur),(x,y-7)],c,6)
  cur=y+7
 if cur<end:line([(x,cur),(x,end)],c,6)
text(65,35,'脚サーボ12個の接続図',40,bold=True)
text(65,91,'PCA9685 → P36分配基板 → LD-220MG   ／   PWMは12本独立、電源とGNDを共通分配',23,muted)
# Board blocks and supply.
rect((650,145,1390,1370),'#ffffff','#87a49b',3)
text(680,318,'P36 電源・PWM分配基板',28,green,True)
text(680,360,'入力 J_IN',18,muted)
text(1330,360,'出力 J0～J11',18,muted,anchor='center')
rect((65,150,310,286),'#fff2e9','#e3af89',2)
text(85,169,'LiPo 2S / XT60',23,bold=True)
text(85,207,'7.4V（満充電8.4V）',19,muted)
text(282,178,'＋',22,red);text(282,252,'−',22,gray)
rect((380,166,530,213),'#fff4f1',red,2,7);text(455,178,'既存の主ヒューズ',17,red,anchor='center')
line([(310,190),(380,190)],red,6);line([(530,190),(820,190)],red,6)
dot(700,190,red);text(700,156,'＋パッド',16,red,anchor='center',bg='#ffffff')
rect((820,165,1005,215),'#fff6d9',yellow,2,7);text(912,178,'F1：15A 仮候補',20,yellow,bold=True,anchor='center')
line([(1005,190),(1190,190)],yellow,6);text(1073,153,'ヒューズ後の＋',18,yellow,anchor='center')
line([(310,270),(1250,270)],gray,6);dot(700,270,gray);text(700,238,'−パッド / GND',17,gray,anchor='center',bg='#ffffff')
text(1040,228,'電源電流はPCA9685を通らない',17,muted,anchor='center',bg='#ffffff')
# PCA block. The power pins are deliberately not connected to the servo supply.
rect((65,325,500,1370),'#edf5ff','#7f9fbf',3)
text(90,347,'脚用 PCA9685',29,bold=True)
text(90,390,'各CHの S（PWM）だけ取り出す',20,blue)
ys=[445+70*i for i in range(12)]
bus(1190,190,ys[-1]+18,ys,yellow)
bus(1250,270,1315,ys+[y+18 for y in ys],gray)
rows=[]
for ch,y in enumerate(ys):
 # One direct signal wire per channel; no shared PWM trunk.
 text(465,y-11,f'CH {ch:02d}  /  S',21,blue,anchor='right')
 dot(500,y,blue)
 line([(500,y),(1360,y)],blue,3)
 dot(730,y,blue);text(790,y-23,f'PWM {ch:02d}',16,blue,anchor='center',bg='#ffffff')
 text(1075,y-23,f'独立 → J{ch}.S',15,blue,anchor='center',bg='#ffffff')
 line([(1190,y+18),(1360,y+18)],yellow,5);dot(1190,y+18,yellow)
 line([(1250,y+36),(1360,y+36)],gray,5);dot(1250,y+36,gray)
 # Connector uses a 3-row schematic representation, not a physical footprint.
 rect((1344,y-8,1380,y+44),'#f8fafc','#c3cfdd',1,3)
 for off,c,label in [(0,blue,'S'),(18,yellow,'＋'),(36,gray,'G')]:
  dot(1360,y+off,c,4);line([(1364,y+off),(1510,y+off)],c,3 if off==0 else 4)
  text(1399,y+off-9,label,14,c,bg='#f6f8fb')
 rect((1510,y-12,1740,y+50),'#ffffff','#b4c1cf',2,8)
 for off,c,label in [(0,blue,'白 S'),(18,red,'赤 ＋'),(36,gray,'黒 G')]:
  dot(1510,y+off,c,3);text(1522,y+off-8,label,13,c)
 text(1629,y-2,f'サーボ {ch:02d}',20,ink,True,anchor='center');text(1629,y+26,'LD-220MG',16,muted,anchor='center')
 rows.extend([(f'PCA9685.CH{ch}.S',f'P36.J_IN.PWM{ch}',f'PWM_L{ch}'),(f'P36.J{ch}.S',f'SERVO{ch}.PWM','white'),(f'P36.J{ch}.V',f'SERVO{ch}.V+','red'),(f'P36.J{ch}.G',f'SERVO{ch}.GND','black')])
# Conventional wire-hop marks: overpass bus drawn after crossing conductors.
for xx,crossings,c in [(1190,ys,yellow),(1250,ys+[y+18 for y in ys],gray)]:
 for yy in crossings:
  points=[(xx+7*math.cos(-math.pi/2+k*math.pi/20),yy+7*math.sin(-math.pi/2+k*math.pi/20)) for k in range(21)]
  line(points,'#ffffff',12);line(points,c,6)
# Reference ground is separate from the 12 PWM signal lines.
text(465,1303,'GND',22,gray,True,anchor='right');dot(500,1315,gray)
line([(500,1315),(650,1315),(720,1300)],gray,3);line([(650,1315),(720,1330)],gray,3);dot(650,1315,gray)
for yy in [1300,1330]:dot(720,yy,gray);line([(720,yy),(770,1315)],gray,3)
line([(770,1315),(1250,1315)],gray,3);dot(770,1315,gray);dot(1250,1315,gray)
text(925,1282,'J_INのGND 2ピン → 共通GND',20,gray,anchor='center',bg='#ffffff')
text(87,1252,'V＋端子・各CHのV＋列は未接続',19,red)
text(87,1338,'VCC / SDA / SCL は既存接続を維持',17,muted)
# Notes essential to distinguish the electrical drawing from the PCB placement.
rect((65,1400,1740,1545),'#ffffff','#c3cfdd',1,8)
text(88,1420,'● は接続点。交差部の丸い橋形は飛び越しを示し、交差する別信号とは接続しない。',20,bold=True)
text(88,1455,'線色は機能を示す：青=PWM、赤=ヒューズ前、黄=ヒューズ後、灰=GND。サーボ線の実色は右端に表示。',19,muted)
text(88,1488,'模式図：端子の物理配置・線長ではない。J_INは実物では2列×7ピン。ヒューズ容量と実負荷電流は評価中。',18,muted)
svg.append('</svg>')
im.save(O/'P36_PCA9685_12_SERVO_WIRING.png')
(O/'P36_PCA9685_12_SERVO_WIRING.svg').write_text('\n'.join(svg))
rows.extend([('PCA9685.GND','P36.J_IN.G-1','GND'),('PCA9685.GND','P36.J_IN.G1','GND'),('BATTERY+ via existing main fuse','P36.J_POWER.PLUS','POWER_IN'),('P36.J_POWER.PLUS','P36.F1.IN','POWER_IN'),('P36.F1.OUT','P36.J0..J11.V','LEG_VBUS'),('BATTERY-','P36.J_POWER.MINUS','GND'),('P36.J_POWER.MINUS','P36.J0..J11.G','GND')])
with (O/'CONNECTIONS.csv').open('w') as f:
 w=csv.writer(f);w.writerow(['from','to','net_or_wire_colour']);w.writerows(rows)
(O/'README.md').write_text('''# P36 PCA9685と脚サーボ12個の接続図\n\nP36の保存済み端子割当から作成した論理接続図。基板、製造データ、Blenderの形状は変更していない。\n\n- PCA9685 CH0～11のS出力を、P36 J_INの同番号へ1対1に配線。\n- J_INの残り2端子は共通GND。\n- 電池＋は既存の主ヒューズを経てP36の＋パッドへ、基板F1を経て12個のV＋へ。\n- 電池−はP36の−パッドから12個のGNDへ。\n- PCA9685のサーボV＋系統をこの給電経路に接続しない。論理VCC/I²Cの既存配線は省略表示。\n- 各サーボへは3線を接続。図中の灰は共通GND、実ケーブル黒。黄はヒューズ後＋、実ケーブル赤。青はPWM、実ケーブル白。\n- 図は実装配置ではない。基板のJ_INは2列×7端子で裏面から見ると左右が反転するため、実装時はP36のCIRCUIT_AND_PINMAP.mdの座標と照合する。\n- 現物導通・電流容量・PWM入力閾値・最終ヒューズ容量を保証する図ではない。\n\nPNGは閲覧用、SVGは拡大用、CONNECTIONS.csvは接続一覧。\n''')
print(O/'P36_PCA9685_12_SERVO_WIRING.png')
