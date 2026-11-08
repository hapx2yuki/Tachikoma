#!/usr/bin/env python3
"""External controller example. HTTP simulation only. No hardware connection."""
import argparse, csv, json, math, time, urllib.request
from pathlib import Path
parser=argparse.ArgumentParser()
parser.add_argument('--url',default='http://127.0.0.1:8796')
parser.add_argument('--seconds',type=float,default=8)
parser.add_argument('--fast',action='store_true')
parser.add_argument('--csv',default=str(Path(__file__).parent/'results'/'external-controller.csv'))
a=parser.parse_args()
def call(endpoint,data):
    req=urllib.request.Request(a.url+endpoint,json.dumps(data).encode(),{'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=10) as res:return json.load(res)
state=call('/api/reset',{'config':{'loadScale':.3}})
rows=[]
for n in range(round(a.seconds/.02)):
    started=time.monotonic(); t=n*.02
    commands=[]
    for ch in state['channel_map']:
        i=ch['index'];angle=(20 if i<12 else 15)*math.sin(2*math.pi*.5*t+(i%3)*.4)
        commands.append({'board':ch['board'],'channel':ch['channel'],'pulse_us':1500+angle*1000/90})
    call('/api/command',{'version':'p15.control.v1','sequence':n,'commands':commands})
    state=call('/api/step',{'step_s':.02});v=state['simulation'];s=v['servos'][2]
    rows.append([v['t'],v['bus'],v['ib'],s['target'],s['q'],s['current'],s['pulse']])
    if not a.fast:time.sleep(max(0,.02-(time.monotonic()-started)))
Path(a.csv).parent.mkdir(parents=True,exist_ok=True)
with open(a.csv,'w',newline='') as f:
    w=csv.writer(f);w.writerow(['t_s','battery_V','battery_A','knee_target_deg','knee_actual_deg','knee_current_A','knee_pwm_us']);w.writerows(rows)
print(json.dumps({'simulated_s':state['simulation']['t'],'last_sequence':state['simulation']['lastSequence'],'csv':a.csv}))
