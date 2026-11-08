"""Scalar electrical screening. No hardware drive or CAD changes."""
import json, math, csv
from pathlib import Path
O=Path('source-root/P36_PCB_FAB_PREP')
d=json.loads((O/'route_candidate.json').read_text())
rho=1.724e-8*(1+.00393*40) # conductor at 60 C
T=.00014 # assumed conservative thickness, supplier minimum not yet guaranteed
W=.0018
rmm=rho/(2*T*W)*.001
Lfeed=sum(math.dist(t['a'],t['b']) for t in d['tracks'] if t['id'].startswith('BAT_FEED_F') and t['b']!=[-4.95,-23.905])
Lroot=math.dist((4.95,-20.495),(6.35,-18.8))+1.35
ys=[-15.24+6.35*i for i in range(6)]

def deltaT(current,width_mm,thick_mm=.14):
    area=width_mm*thick_mm/(.0254**2)
    return (current/(.048*area**.725))**(1/.44)

def calc(total):
    load=total/12;half=total/2
    common_drop=total*rmm*(Lfeed+Lroot+5.8)
    common_heat=total**2*rmm*(Lfeed+Lroot+5.8)
    sideheat=half**2*rmm*(12.7+12.39+5.39)
    # all four six-servo rails: 2.21 mm V and 2.69 mm G terminal-to-first/last pad lengths.
    railheat=2*rmm*(half**2*(2.21+2.69)+2*sum((n*load)**2*6.35 for n in range(1,6)))
    drops=[]
    for side in ['L','R']:
        across=half*rmm*((12.7 if side=='L' else 0)+(12.39 if side=='L' else 5.39))
        for j,y in enumerate(ys):
            v_drop=rmm*(half*2.21+sum((6-i)*load*6.35 for i in range(1,j+1)))
            g_drop=rmm*(half*2.69+sum(i*load*6.35 for i in range(j+1,6)))
            drops.append(common_drop+across+v_drop+g_drop)
    # Deliberately pessimistic additive neck envelope: 4 fuse pin zones each full current,
    # and all 12 V/G pad zones on a worst path each half total, not a thermal FEA result.
    neck_fuse=rho/(2*T)*.0017*(1/.0018-1/W)
    neck_pad=rho/(2*T)*.001*(1/.0012-1/W)
    extra_drop=4*total*neck_fuse+12*half*neck_pad
    extra_heat=4*total**2*neck_fuse+24*half**2*neck_pad
    return dict(total_A=total,each_servo_assumed_A=load,
        ideal_pcb_max_drop_V=max(drops),neck_envelope_pcb_max_drop_V=max(drops)+extra_drop,
        ideal_pcb_heat_W=common_heat+sideheat+railheat,
        neck_envelope_pcb_heat_W=common_heat+sideheat+railheat+extra_heat,
        fuse_cold_drop_V=total*.00458,fuse_cold_heat_W=total**2*.00458,
        screening_trunk_deltaT_C=deltaT(total*.7,1.8*.8),
        screening_servo_hole_deltaT_C=deltaT(half*.7,2.2*.8-1.0),
        screening_fuse_hole_deltaT_C=deltaT(total*.7,3.5*.8-1.7),
        screening_one_bank_only_hole_deltaT_C=deltaT(total*.7,2.2*.8-1.0))
rows=[calc(i) for i in [1.2,10,12,15,18,20.25,24,30,36]]
with open(O/'CURRENT_SCREENING.csv','w') as f:
    w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
r=dict(assumptions={'rho_at_60C_ohm_m':rho,'copper_um':140,'nominal_finished_oz':4.5,'parallel_faces':2,'routing_width_mm':1.8,'thermal_width_factor':.8,'thermal_high_face_share':.7,'thermal_formula':'historical IPC2221 external I=0.048*dT^0.44*A_mil2^0.725; screening, not IPC2152/FEA','voltage_drop_current_split':'equal 50/50; 60 C; equal per-servo current; contact/harness/via resistances excluded','fuse_cold_resistance_ohm':.00458},lengths={'unfused_feed_to_first_fuse_pin_mm':Lfeed,'fused_root_to_rail_mm':Lroot},cases=rows)
(O/'CURRENT_SCREENING.json').write_text(json.dumps(r,indent=2))
print(json.dumps({'lengths':r['lengths'],'case_12A':rows[2],'case_20_25A':rows[5]},indent=2))
