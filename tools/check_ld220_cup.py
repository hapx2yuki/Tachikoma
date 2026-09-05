#!/usr/bin/env python3
"""LD-220MG カップ (hardware/src/make_ld220_cup.py) の検証。

[1] カップ単体: 箱枠との干渉ゼロ / ケース (はめあい込み) との干渉ゼロ /
    フランジの 4 穴が箱枠のタブ穴 (φ2.8 貫通) と同軸 (穴柱がカップの肉に触れない)
[2] 脚 1 本の掃引 (check_leg_assembly.py と同じ姿勢セット + ミラー脚):
    coxa+股カップ / femur+膝カップ / tibia / shin_shell の相互干渉
[3] 隣接脚 (45° ペア・遠ペア, firmware の LIM_YAW を実読) にカップを含めて再検査
[4] ヨー用カップ ×4 を chassis.stl 上に置き、シャーシとの干渉 (ボス上面の接触を除く)
    と隣接カップ同士の距離を確認
"""
import re
import sys
from pathlib import Path

import numpy as np
import trimesh

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hardware" / "src"))
import config as C  # noqa: E402
import make_ld220_cup as MC  # noqa: E402
import make_leg as ML  # noqa: E402
import shell_mod as SM  # noqa: E402

STL = ROOT / "hardware" / "stl"
OK = True


def to_tm(m):
    mm = m.to_mesh()
    return trimesh.Trimesh(np.asarray(mm.vert_properties)[:, :3], np.asarray(mm.tri_verts), process=False)


def vol(a, b):
    try:
        i = trimesh.boolean.intersection([a, b], engine="manifold")
    except Exception:
        return float("nan")
    return 0.0 if (i is None or i.is_empty) else float(i.volume) / 1000.0


def rot_y(deg):
    return trimesh.transformations.rotation_matrix(np.radians(deg), [0, 1, 0])


def rot_z(deg):
    return trimesh.transformations.rotation_matrix(np.radians(deg), [0, 0, 1])


def trans(x, y, z):
    return trimesh.transformations.translation_matrix([x, y, z])


def flag(v, lim=0.01):
    global OK
    if not (v < lim):
        OK = False
        return "  << NG"
    return ""


# ---------------------------------------------------------------- [1]
print("[1] カップ単体 (箱枠ローカル)")
frame = to_tm(ML.servo_frame())
cup = to_tm(MC.cup_leg_in_frame())
case = to_tm(MC.ld220_case_in_frame(clear=0.0))
case_fit = to_tm(MC.ld220_case_in_frame(clear=MC.K["CLEAR"] - 0.05))
v1 = vol(cup, frame); v2 = vol(cup, case); v3 = vol(cup, case_fit); v4 = vol(case, frame)
print(f"  cup∩frame = {v1:.4f} cm3{flag(v1)}   cup∩case = {v2:.4f}{flag(v2)}   "
      f"cup∩case(+0.15clear) = {v3:.4f}{flag(v3)}   case∩frame = {v4:.4f}{flag(v4)}")
# タブ穴の同軸性: 箱枠のタブ穴柱 (φ2.8, y -15..15) をカップに通す → カップの肉に触れないこと
holes = to_tm(ML.servo_tab_holes(ML.P).rotate([-90, 0, 0]))
v5 = vol(cup, holes)
print(f"  タブ穴柱(φ2.8)∩cup = {v5:.4f} cm3{flag(v5)}  (0 なら 4 穴が同軸)")
# 実際の coxa_bracket / femur_link (天板・ウェブ・ブリッジ込み) に対する干渉 (スカート・フランジ)
for part, off in (("coxa_bracket", C.COXA_LEN), ("femur_link", C.FEMUR_LEN)):
    for sfx in ("", "_m"):
        host = trimesh.load(STL / f"{part}{sfx}.stl")
        cm = MC.cup_leg_in_frame().mirror([0, 1, 0]) if sfx else MC.cup_leg_in_frame()
        c2 = to_tm(cm.translate([off, 0, 0]))
        v = vol(c2, host)
        print(f"  cup∩{part}{sfx} = {v:.4f} cm3{flag(v)}")
# 寸法サマリ
cb = cup.bounds
print(f"  cup bounds (箱枠ローカル) y: {cb[0][1]:.1f}..{cb[1][1]:.1f}  (箱枠 -Y 面 -13.1, ケース底 {MC.CASE_BOT_Y:.1f})")
print(f"  ケース上面 y={MC.CASE_TOP_Y:.1f} (DS3218 の上面 {ML.P['ABOVE_TAB']:.1f} と一致) / "
      f"ホーンアーム上面 {ML.HORN_TOP:.1f}")

# ---------------------------------------------------------------- [2]
print("\n[2] 脚 1 本掃引 (カップ込み)")


def leg_at(pitch, knee, mirror):
    sfx = "_m" if mirror else ""
    s = -1.0 if mirror else 1.0
    coxa = trimesh.load(STL / f"coxa_bracket{sfx}.stl")
    femur = trimesh.load(STL / f"femur_link{sfx}.stl")
    tibia = trimesh.load(STL / f"tibia_link{sfx}.stl")
    shin = to_tm(SM.shin_shell().mirror([0, 1, 0]) if mirror else SM.shin_shell())
    cupc = to_tm(MC.cup_leg_in_frame().mirror([0, 1, 0]) if mirror else MC.cup_leg_in_frame())
    casec = to_tm(MC.ld220_case_in_frame().mirror([0, 1, 0]) if mirror else MC.ld220_case_in_frame())
    hip = trimesh.util.concatenate([cupc.copy(), casec.copy()]); hip.apply_transform(trans(C.COXA_LEN, 0, 0))
    coxa = trimesh.util.concatenate([coxa, hip])
    T_hip = trans(C.COXA_LEN, 0, 0) @ rot_y(pitch)
    knee_m = trimesh.util.concatenate([cupc.copy(), casec.copy()]); knee_m.apply_transform(trans(C.FEMUR_LEN, 0, 0))
    femur = trimesh.util.concatenate([femur, knee_m]); femur.apply_transform(T_hip)
    T_knee = T_hip @ trans(C.FEMUR_LEN, 0, 0) @ rot_y(knee)
    tibia.apply_transform(T_knee); shin.apply_transform(T_knee)
    return coxa, femur, tibia, shin


poses = [("neutral", 20, 0), ("crouch", 45, 30), ("high", -10, -20), ("reach", 0, -35),
         ("tuck", 55, 45), ("sprawl", -38, 33), ("sprawl+", -45, 45), ("sprawl-", -45, -20)]
print(f"  {'pose':10s} {'pitch':>5s} {'knee':>5s}  coxa-fem  fem-tib  coxa-tib  coxa-shin  fem-shin")
for mirror in (False, True):
    for name, p, k in poses:
        coxa, femur, tibia, shin = leg_at(p, k, mirror)
        r = [vol(coxa, femur), vol(femur, tibia), vol(coxa, tibia), vol(coxa, shin), vol(femur, shin)]
        print(f"  {name + ('_m' if mirror else ''):10s} {p:5.0f} {k:5.0f}  " + "  ".join(f"{x:7.3f}" for x in r) + flag(max(r)))

# ---------------------------------------------------------------- [3]
print("\n[3] check_leg_assembly.py をサーボ実体 = LD-220MG + ld220_cup_leg に差し替えて全項目実行")
sys.path.insert(0, str(ROOT / "tools"))
import check_leg_assembly as CLA  # noqa: E402


def _ld_case(mirror=False):
    m = MC.cup_leg_in_frame() + MC.ld220_case_in_frame()
    if mirror:
        m = m.mirror([0, 1, 0])
    return to_tm(m)


CLA.servo_case_mesh = _ld_case
import io, contextlib  # noqa: E402
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    CLA.main()
txt = buf.getvalue()
ngs = [ln for ln in txt.splitlines() if "NG" in ln and "OK" not in ln]
for ln in txt.splitlines():
    if "worst" in ln or "OK" in ln or "NG" in ln:
        print("   " + ln.strip())
if ngs:
    OK = False
    print("  << check_leg_assembly で NG:", ngs)
else:
    print("  check_leg_assembly 全項目 OK (カップ込み)")
import subprocess  # noqa: E402
subprocess.run(["git", "checkout", "--", "docs/preview_leg_assembly.png"], cwd=ROOT)  # チェッカーが上書きする画像を戻す

# ---------------------------------------------------------------- [4]
print("\n[4] ヨー用カップ ×4 vs chassis.stl")
chassis = trimesh.load(STL / "chassis.stl")
cy = to_tm(MC.cup_yaw())
import make_head_eyecut as MH  # noqa: E402
pca = to_tm(MH.pca_stack_envelope())
P = C.YAW_SERVO
cx = P["L"] / 2 - P["SHAFT_OFF"]
CASE_ANG = {"FR": 0.0, "FL": 180.0, "RL": 180.0, "RR": 0.0}
boss_top = C.CHASSIS_T + 3.0
placed = {}
for name, (x, y) in C.HIPS.items():
    a = np.radians(CASE_ANG[name]); ca, sa = np.cos(a), np.sin(a)
    ctr = (x - cx * ca, y - cx * sa)
    m = cy.copy(); m.apply_transform(trans(ctr[0], ctr[1], boss_top) @ rot_z(CASE_ANG[name]))
    placed[name] = m
    # 接触面 (ボス上面) を除くため 0.05mm 持ち上げて判定
    m2 = m.copy(); m2.apply_transform(trans(0, 0, 0.05))
    v = vol(m2, chassis); vp = vol(m, pca)
    tab = trimesh.creation.box((P["TAB_SPAN"], P["W"], P["TAB_T"]))
    tab.apply_transform(trans(ctr[0], ctr[1], boss_top + 1.5) @ rot_z(CASE_ANG[name]))
    print(f"  {name}: cup∩chassis = {v:.4f} cm3{flag(v)}  cup∩PCA包絡 = {vp:.4f} cm3"
          f"  [参考: DS3218 タブ∩PCA包絡 {vol(tab, pca):.4f}]  top z = {m.bounds[1][2]:.1f} (DS3218 ケース上端 {boss_top + P['TAB_BELOW']:.1f})")
names = list(placed)
for i in range(4):
    for j in range(i + 1, 4):
        A, B = placed[names[i]], placed[names[j]]
        d = trimesh.proximity.closest_point(A, B.vertices)[1].min()
        print(f"  {names[i]}-{names[j]} 最短距離 {d:.1f} mm")

print("  注: RR の cup∩PCA包絡 は既存設計でも DS3218 ケース/タブが同じ包絡と重なる箇所 (プラグ列 +x 側)。カップ固有の問題ではない")
print("\n" + ("ALL OK" if OK else "NG あり"))
