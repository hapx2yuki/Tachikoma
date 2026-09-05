# パーツリスト用 STL サムネイル生成。使い方: uv venv -p 3.12 .venv && uv pip install -p .venv/bin/python trimesh numpy matplotlib fast-simplification && .venv/bin/python site/tools/render_parts.py [部品名...]
import sys, os, numpy as np, trimesh
import matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
ROOT='/Volumes/AIWorkSSD/AIWorkSpace/3dprintMokumoku/tachikoma/Tachikoma'
OUT=ROOT+'/site/docs/public/img/parts'
COL={'blue':'#3b6fd6','grey':'#9aa3ad','white':'#f2f2f2','black':'#3a3d44','red':'#d8433b','petg':'#f0a05a','tpu':'#4a4a4a'}
custom={'Head_Bottom_Armcut':'blue','Head_Top_Eyecut':'blue','Mouth_Ball_Bored':'grey','Mouth_Cannon_Bored':'grey','Mouth_Neck_Bored':'blue','arm_pod_lower':'blue','arm_pod_lower_L':'blue','arm_pod_upper':'blue','arm_pod_upper_L':'blue','audio_cradle_mic':'petg','audio_cradle_spk':'petg','battery_cradle':'petg','camera_carrier':'petg','chassis':'petg','claw_mount':'petg','claw_mount_L':'petg','coxa_bracket':'petg','coxa_bracket_m':'petg','elbow_shell':'grey','elbow_shell_L':'grey','eye_carrier':'petg','eye_pod':'white','eye_pod_camera_base':'white','eye_pod_camera_shell':'white','femur_link':'petg','femur_link_m':'petg','foot_pad':'tpu','forearm':'petg','forearm_L':'petg','leg_foot_bored':'grey','pod_neck':'petg','shin_shell':'blue','shin_shell_m':'blue','shoulder_bracket':'petg','shoulder_bracket_L':'petg','thigh_cap':'grey','tibia_link':'petg','tibia_link_m':'petg','upper_arm':'petg','upper_arm_L':'petg'}
kit=['Arm_Left_Claw_Grey','Arm_Left_FingerTip_Grey_x3','Arm_Left_Finger_Black_x3','Arm_Left_Guard_Grey','Arm_Right_Guard_Grey','Cabin_Back_Blue_Repaired','Cabin_Eye_White','Cabin_Front_Blue','Cabin_Front_Insert_Back_Black_x2','Cabin_Front_Insert_Bottom_Long_Black_x2','Cabin_Front_Insert_Bottom_Wide_Black','Cabin_Front_Insert_Front_Black','Cabin_Front_Insert_Left_Black','Cabin_Front_Insert_Right_Black','Cabin_Peg_x2','Cabin_RedLight_Large_Red_x4','Cabin_RedLight_Small_Red_x4','Cabin_Spinnarette_Grey_x4','Cabin_Turrent_Left_Grey','Cabin_Turrent_Right_Grey','Cabin_Turret_Peg_x2','Head_Dome_Grey','Head_Insert_Black_x4','Head_Peg_Lower','Head_Peg_Upper','Head_Plug_Grey','Head_Screw_Grey_x2','Head_TailJoint_Ball_Grey_Optional_Cross','Head_TailJoint_Blue_Optional_Cross','Head_TailJoint_Peg','Head_TailJoint_Peg_Optional_Cross_Repaired','Leg_Shin_Guard_Grey_x4','Leg_Thigh_Guard_Blue_x4','Leg_Toe_Black_x12','Mouth_Cap_Grey','Mouth_Key_Grey','Mouth_Peg_Grey','Stand_mount_Optional']
def kitcol(n):
    for k in ['Blue','Grey','White','Black','Red']:
        if '_'+k in n: return k.lower()
    return 'grey'
jobs=[(ROOT+'/hardware/stl/'+n+'.stl',n,custom[n]) for n in custom]+[(ROOT+'/model/'+n+'.stl',n,kitcol(n)) for n in kit]
if len(sys.argv)>1: jobs=[j for j in jobs if j[1] in sys.argv[1:]]
light=np.array([0.4,-0.6,0.7]); light/=np.linalg.norm(light)
for path,name,c in jobs:
    out=f'{OUT}/{name}.png'
    if os.path.exists(out): continue
    m=trimesh.load(path,force='mesh')
    if len(m.faces)>12000:
        try: m=m.simplify_quadric_decimation(12000)
        except Exception as e: print('simplify fail',name,e)
    v=m.vertices-m.bounding_box.centroid; ext=m.extents.max()
    v=v/ext
    f=m.faces; n=m.face_normals
    shade=np.clip(n@light,0,1)*0.6+0.4
    base=np.array(matplotlib.colors.to_rgb(COL[c]))
    fc=np.clip(base*shade[:,None],0,1)
    fig=plt.figure(figsize=(4.8,4.0),dpi=100); ax=fig.add_subplot(111,projection='3d')
    ax.add_collection3d(Poly3DCollection(v[f],facecolors=fc,edgecolors='none'))
    ax.set_xlim(-0.55,0.55); ax.set_ylim(-0.55,0.55); ax.set_zlim(-0.55,0.55); ax.set_box_aspect((1,1,1))
    ax.view_init(elev=28,azim=-55); ax.set_axis_off(); fig.subplots_adjust(0,0,1,1)
    fig.savefig(out,facecolor='white'); plt.close(fig)
    print('ok',name,len(f))
