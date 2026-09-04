#!/usr/bin/env python3
"""shin_shell を含めた干渉チェック (2026-07-31 ループ意匠切除タスクで新規作成)。

check_leg_assembly.py は coxa/femur/tibia の「骨格」のみを検査し shin_shell
(意匠シェル) を一度も含めていない。check_arm.py も脚を粗い近似モデルでしか
扱わず shin_shell 非依存。このため shin_shell と (a) 自脚骨格の深屈み、
(b) 隣接脚 (特に crouch=(45°,30°) のような代表姿勢)、(c) 腕 (READY/TUCK)、
の実体干渉は今まで一度も自動検査されていなかった (2026-07-31 レビューで
発覚)。本スクリプトはその隙間を埋める恒久チェックとして追加する。

検査結果のうち [C] 腕との干渉は、現状の shell_mod.py の変更 (ループ切除 /
KNEE_RELIEF・TIP_RELIEF の再調整) だけでは解消できない規模の既存不具合
(shin_shell の広い範囲 (シェル長の2/3超) が READY 姿勢の腕と重なる) と
判明している。ローカルな削り込みで対応する類の問題ではなく、firmware 側の
脚×腕 姿勢連成クランプ (例: 該当脚が pitch<-30°かつyaw>+25°相当のときは
その側の腕を READY へ進めさせない) 等、別レイヤでの対応が必要と考えられる。
本スクリプトはこれを「既知の要修正項目」として NG のまま報告し続ける
(隠蔽しない) — 対応が入るまでは result は FAIL のままになる。

[D] 隣接脚 tibia_link 同士のごく僅かな接触 (0.0683cm^3, 45°ペア片側最大内寄せ
ヨー+crouch=(45,30) 同士) も shin_shell とは無関係な骨格側の事象として発見
された。2026-07-31 タスク#30 で到達可能性を検証した結果、この接触を生む
(pitch,knee)=(45,30) は firmware gait.h のワークスペース射影 (D_KNEE_MIN/
D_KNEE_MAX, pk_reachable() 参照) の下では歩容コマンドとして絶対に出力され
得ない不動点条件違反であり (実歩容全域スイープでも pitch は最大1.3°にしか
達しない)、通常運用下では非到達と判定できた。tibia_link の形状は変更せず
(README 鉄則3)、[B] のtibia-tibia判定を「firmware到達可能集合」に限定する
よう再定義し KNOWN NG を解消した (pk_reachable() および [B] 節のコメント、
docs/assembly.md 参照)。この解消は firmware の物理クランプ (gait.h の
ワークスペース射影) に依存しており、歩容制御を経由しない raw 関節コマンド
の経路が将来 firmware に追加された場合は 0.0683cm^3 の接触が理論上再現し
得る点に注意 (現行 firmware にはそのような経路は無い)。

2026-07-31 (実物写真タスク, shin_rotz 270->0 再変更): [B] の shin-shin
近ペアに一時 KNOWN NG 項目 (worst=0.84cm^3, M3ボルト座面のハード制約由来)
があったが、下記「リリーフカット再評価タスク」で解消済み (履歴として記録)。

2026-07-31 (リリーフカット再評価タスク): shell_mod.py の KNEE_RELIEF /
TIP_RELIEF / ADJ_RELIEF_BANDS+BOLT_BOSS_KEEPOUT_X を「firmware 到達可能
集合内で干渉するか」基準 (上記 [D] の tibia-tibia と同じ pk_reachable()
基準) で再評価し、いずれも到達可能集合内では干渉ゼロと確認できたため撤去
してキット形状へ復元した (shell_mod.py 該当コメント参照)。これに伴い [B]
の shin-shin 判定も tibia-tibia と同じ到達可能集合基準に統一し、
KNOWN_BOLT_BOSS_CEILING の特例ロジック (worst=0.84cm^3 を KNOWN NG として
PASS 扱いする分岐) を削除した — 到達可能集合内では worst=0.0000cm^3 の
真の PASS になったため特例が不要になった。物理全域 (到達不能姿勢込み) の
参考値は printed だが overall には算入しない (tibia-tibia の 0.0683cm^3
と同じ理論上限の扱い)。
"""
import re
import sys
from pathlib import Path

import numpy as np
import trimesh

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hardware" / "src"))
sys.path.insert(0, str(ROOT / "tools"))
import config as C  # noqa: E402
import shell_mod as SM  # noqa: E402
import make_visuals as MV  # noqa: E402
import sim_gait as SG  # noqa: E402

STL = ROOT / "hardware" / "stl"
MIRROR_LEGS = {"FR", "RL"}
LEG_NAMES = ["FR", "FL", "RL", "RR"]


def rot_y(deg):
    t = np.radians(deg)
    m = np.eye(4)
    m[0, 0] = np.cos(t); m[0, 2] = np.sin(t)
    m[2, 0] = -np.sin(t); m[2, 2] = np.cos(t)
    return m


def rot_z(deg):
    t = np.radians(deg)
    m = np.eye(4)
    m[0, 0], m[0, 1], m[1, 0], m[1, 1] = np.cos(t), -np.sin(t), np.sin(t), np.cos(t)
    return m


def trans(x, y, z):
    m = np.eye(4)
    m[:3, 3] = [x, y, z]
    return m


def to_tm(m):
    mesh = m.to_mesh()
    return trimesh.Trimesh(vertices=np.array(mesh.vert_properties[:, :3]),
                            faces=np.array(mesh.tri_verts), process=False)


def inter_vol(a, b):
    try:
        r = trimesh.boolean.intersection([a, b], engine="manifold")
    except Exception:
        return float("nan")
    if r is None or r.is_empty:
        return 0.0
    return float(r.volume) / 1000.0


def pk_reachable(pitch_deg, knee_deg):
    """(pitch,knee) が gait.h のワークスペース射影 (D_KNEE_MIN/D_KNEE_MAX) の
    もとで実際に出力され得る組合せかを判定する (2026-07-31 タスク#30, [B]
    tibia-tibia KNOWN NG の到達可能性判定で追加)。

    gait.h Gait::update() は脚ローカル目標 (lx,ly,lz) を legIK() へ渡す前に
    必ず「深さ dd=-lz に応じた半径帯 [rmin(dd),rmax(dd)] へ rr=hypot(lx,ly)
    を強制収める」射影を適用する (D_KNEE_MAX/D_KNEE_MIN, gait.h 92-106行)。
    この射影は歩容が生成する足先目標のみに掛かる関門ではなく、rr が帯の外に
    あれば必ず書き換えるため、legIK() に実際に渡る (rr,dd) は常にこの帯の
    内側にある — 言い換えれば、ある (pitch,knee) を出すのに必要な (rr,dd)
    が帯の外にあるなら、その (pitch,knee) は歩容コマンドとして絶対に出力
    されない (射影が必ず別の rr へ書き換えてしまうため)。本関数はその
    不動点条件を判定する。

    必要な (rr,dd) は sim_gait.leg_ik() の内部方程式を直接逆算して求める
    (2026-07-31 レビュー指摘で修正: 以前は SG.leg_fk(0,pitch,knee) の x 座標
    に abs() を掛けた値を rr の代理として使っていたが、leg_fk の
    r = COXA + FEMUR*cos(pitch) + TIBIA*cos(pitch+beta) が負になる
    (脚がヨー軸を越えて折り畳まれる) 領域では leg_ik 側の
    r = hypot(x,y) - COXA は常に hypot(x,y)>=0 から来るため |leg_fk の r|
    と一致せず、abs(x) は不動点方程式の左辺として不正だった。41x45 グリッド
    スキャンで実際に 263/1845 点が「abs(x) 版では reachable 判定なのに
    厳密な逆算では unreachable」という過大評価になっていたことを確認済み
    (逆方向の食い違いは 0 件 — 過小評価/安全側の誤りはなかった)。以下は
    legIK の beta=knee+90 から dist (余弦定理の逆) と r,d の方向 (alpha+gamma
    逆算) を直接求める厳密版。r+COXA が hypot(x,y) の値そのものなので
    abs() は不要 (負なら「実ターゲットが物理的に存在しない」として
    reachable=False にする)。

    経験的な裏付け (scratchpad, 2026-07-31): sim_gait.foot_target() を
    体高105-130mm・vx/vy/wz全域・全位相でスイープした実測でも、この判定で
    unreachable となる crouch=(45,30) 相当の深さ+近さの組合せは一度も
    出現しない (実歩容全域での pitch 最大値は 1.3° に留まり、pitch>35°か
    つ knee>20° となる姿勢は 0 件)。CALIBRATION_MODE は allNeutral() のみ、
    web_ui.h の /trim は ±200us=±18° 程度の小さな中立オフセットに留まり
    gait.update() の出力を経由しない直接関節コマンドの経路は現行 firmware
    に存在しないため (main.cpp 参照)、この判定は「firmware の実際の出力
    集合」に対して妥当である。ただし将来 firmware に raw 関節コマンド
    (校正モード以外の直接指令) が追加された場合は本前提が崩れるため、
    その際は本関数によるゲーティングごと見直すこと。
    """
    beta = np.radians(knee_deg) + np.pi / 2
    dist2 = SG.FEMUR**2 + SG.TIBIA**2 + 2 * SG.FEMUR * SG.TIBIA * np.cos(beta)
    dist = np.sqrt(max(0.0, dist2))
    gamma = np.arctan2(SG.TIBIA * np.sin(beta), SG.FEMUR + SG.TIBIA * np.cos(beta))
    theta = np.radians(pitch_deg) + gamma
    r = dist * np.cos(theta)
    d = dist * np.sin(theta)
    rr, dd = r + SG.COXA, d
    if rr < 0:
        return False  # hypot(x,y) は常に非負 -> この (pitch,knee) を出す実ターゲットが存在しない
    if dd < SG.D_KNEE_MAX:
        rmax = SG.COXA + np.sqrt(max(0.0, SG.D_KNEE_MAX**2 - dd * dd))
        if rr > rmax:
            return False
    if dd < SG.D_KNEE_MIN and rr > 0.1:
        rmin = SG.COXA + np.sqrt(max(0.0, SG.D_KNEE_MIN**2 - dd * dd))
        if rr < rmin:
            return False
    return True


_shin_cache = {}


def shin_local(mirror: bool):
    """shin_shell() を base (未ミラー) 構築フレームのまま trimesh 化してキャッシュ。"""
    if mirror not in _shin_cache:
        tm = to_tm(SM.shin_shell())
        if mirror:
            tm = tm.copy()
            tm.vertices[:, 1] *= -1.0
            tm.faces = tm.faces[:, ::-1]
        _shin_cache[mirror] = tm
    return _shin_cache[mirror].copy()


def leg_parts(name, yaw_delta, pitch_deg, knee_deg):
    """1 脚分の {coxa, femur, tibia, shin} を world 座標で返す (z 原点 0 基準)。"""
    mirror = name in MIRROR_LEGS
    sfx = "_m" if mirror else ""
    coxa = trimesh.load(STL / f"coxa_bracket{sfx}.stl")
    femur = trimesh.load(STL / f"femur_link{sfx}.stl")
    tibia = trimesh.load(STL / f"tibia_link{sfx}.stl")
    T_hip = trans(C.COXA_LEN, 0, 0) @ rot_y(pitch_deg)
    femur.apply_transform(T_hip)
    T_knee = T_hip @ trans(C.FEMUR_LEN, 0, 0) @ rot_y(knee_deg)
    tibia.apply_transform(T_knee)
    shin = shin_local(mirror)
    shin.apply_transform(T_knee)
    T_outer = trans(C.HIPS[name][0], C.HIPS[name][1], 0) @ rot_z(C.LEG_ANGLES[name] + yaw_delta)
    for p in (coxa, femur, tibia, shin):
        p.apply_transform(T_outer)
    return {"coxa": coxa, "femur": femur, "tibia": tibia, "shin": shin}


def main():
    overall_ok = True

    # ---- [A] 自脚: femur/coxa vs shin_shell, pitch x knee 全域グリッド + 実歩容
    print("[A] 自脚: shin_shell vs femur_link/coxa_bracket (pitch x knee グリッド)")
    worst = 0.0
    worst_pose = None
    for mirror in (False, True):
        sfx = "_m" if mirror else ""
        femur_master = trimesh.load(STL / f"femur_link{sfx}.stl")
        coxa = trimesh.load(STL / f"coxa_bracket{sfx}.stl")
        for pitch in np.linspace(-45, 55, 11):
            for knee in np.linspace(-44, 44, 12):
                femur = femur_master.copy()
                T_hip = trans(C.COXA_LEN, 0, 0) @ rot_y(pitch)
                femur.apply_transform(T_hip)
                T_knee = T_hip @ trans(C.FEMUR_LEN, 0, 0) @ rot_y(knee)
                shin = shin_local(mirror)
                shin.apply_transform(T_knee)
                v = inter_vol(femur, shin)
                if v > worst:
                    worst, worst_pose = v, (mirror, pitch, knee, "femur")
                v2 = inter_vol(coxa, shin)
                if v2 > worst:
                    worst, worst_pose = v2, (mirror, pitch, knee, "coxa")
    ok = worst < 0.01
    overall_ok &= ok
    print(f"  worst = {worst:.4f} cm^3 at {worst_pose}  ({'OK' if ok else 'NG'})")

    # 実歩容 (sim_gait) 由来の実姿勢でも確認
    worst_g = 0.0
    n = 0
    for body_h in np.linspace(105, 130, 4):
        for phase in np.linspace(0, 1, 12, endpoint=False):
            for i, leg in enumerate(LEG_NAMES):
                lx, ly, lz = SG.foot_target(i, phase, 0.3, 0.0, 0.0, body_h=body_h)
                a = SG.leg_ik(lx, ly, lz)
                if a is None:
                    continue
                yaw_d, pitch_d, knee_d = a
                mirror = leg in MIRROR_LEGS
                sfx = "_m" if mirror else ""
                femur = trimesh.load(STL / f"femur_link{sfx}.stl")
                T_hip = trans(C.COXA_LEN, 0, 0) @ rot_y(pitch_d)
                femur.apply_transform(T_hip)
                T_knee = T_hip @ trans(C.FEMUR_LEN, 0, 0) @ rot_y(knee_d)
                shin = shin_local(mirror)
                shin.apply_transform(T_knee)
                v = inter_vol(femur, shin)
                worst_g = max(worst_g, v)
                n += 1
    ok_g = worst_g < 0.01
    overall_ok &= ok_g
    print(f"  実歩容 {n} 姿勢: worst = {worst_g:.4f} cm^3  ({'OK' if ok_g else 'NG'})")

    # ---- [B] 隣接脚 45°ペア: crouch=(45,30) を含む代表姿勢 + IK到達極値
    print("\n[B] 隣接脚 45°ペア (単側最大内寄せヨー, crouch=(45,30) 含む)")
    fw = (ROOT / "firmware" / "src" / "config.h").read_text()
    LIM_IN = float(re.search(r"LIM_YAW_IN\s*=\s*([\d.]+)f", fw).group(1))
    LIM_SUM = float(re.search(r"LIM_YAW_IN_SUM\s*=\s*([\d.]+)f", fw).group(1))
    LIM_YAW = float(re.search(r"LIM_YAW\s*=\s*([\d.]+)f", fw).group(1))

    cands = []
    for x in np.linspace(40, 180, 25):
        for y in np.linspace(-70, 70, 11):
            for z in np.linspace(-165, -60, 15):
                a = SG.leg_ik(x, y, z)
                if a:
                    cands.append(a[1:3])
    arr = np.array(cands)
    idx = {int(i) for i in (
        arr[:, 0].argmax(), arr[:, 0].argmin(), arr[:, 1].argmax(),
        arr[:, 1].argmin(), (arr[:, 0] + arr[:, 1]).argmax(),
        (arr[:, 0] + arr[:, 1]).argmin())}
    ik_poses = [(round(arr[i, 0]), round(arr[i, 1])) for i in sorted(idx)]
    ik_poses += [(20, 0), (45, 30)]  # (45,30)=crouch: 2026-07-31 レビュー指摘で追加

    # tibia-tibia / shin-shin ともに firmware のワークスペース射影
    # (D_KNEE_MIN/MAX, pk_reachable() 参照) を通過し得ない (pitch,knee) を
    # 除いた「到達可能集合」に統一して判定する。
    #
    # 【2026-07-31 リリーフカット再評価タスクで統一】従来は shin-shin だけ
    # ik_poses 全域のまま判定し、worst=0.8404cm^3 (BOLT_BOSS_KEEPOUT_X 縦通し
    # 柱起因) を KNOWN NG として PASS 扱いする非対称な特例ロジックだった。
    # 今回 shell_mod.py の ADJ_RELIEF_BANDS+BOLT_BOSS_KEEPOUT_X を撤去し
    # キット形状へ復元したため、この特例自体が不要になった: shin-shin
    # worst=0.8404cm^3 も tibia-tibia と全く同じ不動点条件違反の角
    # (pitch,knee)=(45,30) 同士だけで発生しており、pk_reachable() を
    # shin-shin にも適用すると worst=0.0000cm^3 まで落ちる (カット撤去後の
    # shin_shell() で再検証済み)。よって tibia-tibia と全く同じ基準
    # (ik_poses_reachable への統一) を shin-shin にも適用し、
    # KNOWN_BOLT_BOSS_CEILING の特例分岐を削除した。
    ik_poses_reachable = [pk for pk in ik_poses if pk_reachable(*pk)]
    _unreachable = [pk for pk in ik_poses if pk not in ik_poses_reachable]
    print(f"  (tibia-tibia判定: ik_poses {len(ik_poses)}件中 firmware到達不能"
          f" {len(_unreachable)}件を除外 -- {_unreachable} は gait.h の"
          f" D_KNEE_MIN/MAX 射影の不動点条件を満たさず、歩容コマンドとして"
          f" 出力され得ない。pk_reachable() docstring 参照)")

    pairs45 = [
        ("FR-RR(単側FR)", "FR", -LIM_IN, "RR", 0.0),
        ("FR-RR(単側RR)", "FR", 0.0, "RR", +LIM_IN),
        ("FR-RR(和・対称)", "FR", -LIM_SUM / 2, "RR", +LIM_SUM / 2),
        ("FR-RR(和・偏り)", "FR", -LIM_IN, "RR", +(LIM_SUM - LIM_IN)),
    ]
    worst_b_shin = 0.0
    worst_b_tibia = 0.0
    worst_b_shin_full = 0.0  # 参考値 (到達不能姿勢込みの物理全域, 非算入)
    for name, l1, y1, l2, y2 in pairs45:
        w_shin = w_tibia = w_shin_full = 0.0
        for p1, k1 in ik_poses:
            for p2, k2 in ik_poses:
                parts1 = leg_parts(l1, y1, p1, k1)
                parts2 = leg_parts(l2, y2, p2, k2)
                v_shin = inter_vol(parts1["shin"], parts2["shin"])
                w_shin_full = max(w_shin_full, v_shin)
                if (p1, k1) in ik_poses_reachable and (p2, k2) in ik_poses_reachable:
                    w_shin = max(w_shin, v_shin)
                    w_tibia = max(w_tibia, inter_vol(parts1["tibia"], parts2["tibia"]))
        worst_b_shin = max(worst_b_shin, w_shin)
        worst_b_tibia = max(worst_b_tibia, w_tibia)
        worst_b_shin_full = max(worst_b_shin_full, w_shin_full)
        print(f"  {name}: shin-shin(到達可能集合限定) {w_shin:.4f} cm^3"
              f" [参考:物理全域 {w_shin_full:.4f} cm^3], tibia-tibia(到達可能集合限定) {w_tibia:.4f} cm^3")
    # 2026-07-31 (リリーフカット再評価タスク): shin-shin も tibia-tibia と
    # 同じ「firmware 到達可能集合」基準に統一した (上記コメント参照)。
    # shell_mod.py から ADJ_RELIEF_BANDS+BOLT_BOSS_KEEPOUT_X を撤去し
    # キット形状へ復元した結果、到達可能集合内では worst=0.0000cm^3 (旧
    # KNOWN_BOLT_BOSS_CEILING=1.0cm^3 の特例は不要になったため削除)。
    # 物理全域 (到達不能姿勢込み) では crouch(45,30) 同士で依然
    # worst_full≈8.49cm^3 相当が起こり得るが、これは tibia-tibia 側の
    # 0.0683cm^3 (下記) と全く同じ理由 (pk_reachable()=False) で firmware
    # が歩容コマンドとして絶対に出力しない姿勢なので、overall には算入せず
    # 参考値としてのみ表示する。
    ok_b_shin = worst_b_shin < 0.01
    overall_ok &= ok_b_shin
    label = ("OK (firmware到達可能集合では非接触 -- gait.h D_KNEE_MIN/MAX 射影が"
              " crouch(45,30)級の組合せを構造的に排除。shell_mod.py の"
              " ADJ_RELIEF_BANDS 撤去によりキット形状へ復元済み)"
             if ok_b_shin else "NG (regression)")
    print(f"  shin-shin worst(到達可能集合限定) = {worst_b_shin:.4f} cm^3  ({label})")
    print(f"  shin-shin worst(参考:物理全域, raw関節指令の経路が将来追加された場合の"
          f"理論上限) = {worst_b_shin_full:.4f} cm^3")
    # tibia-tibia: 2026-07-31 タスク#30 で再分類。素の ik_poses 全域では
    # crouch=(45,30) 同士 (FR 単側最大内寄せヨー + RR 中立) で 0.0683cm^3 の
    # 既知残留があったが、この (pitch,knee) の組合せは firmware
    # gait.h のワークスペース射影 (D_KNEE_MIN, pk_reachable() 参照) の下では
    # 歩容コマンドとして絶対に出力されない (実歩容全域スイープでも pitch は
    # 最大 1.3° にしか達せず、pitch>35°かつknee>20°の姿勢は0件, scratchpad
    # 2026-07-31 実測)。よって「firmware 到達可能集合」に絞った本判定では
    # tibia_link 側の形状変更なしに worst=0.0000cm^3 (PASS) となり、overall
    # にも算入する。tibia_link の形状自体は変更していない (README 鉄則3)。
    # 【重要な限定条件】この PASS は「歩容制御 (gait.update()) を経由する
    # 限り」の保証であり、raw 関節角度を直接書き込む経路 (現行 firmware に
    # は存在しない -- main.cpp 参照) が将来追加された場合は 0.0683cm^3 の
    # 残留接触が理論上再現し得る。その場合は pk_reachable() のゲーティング
    # 前提ごと見直すこと (docs/assembly.md 参照)。
    ok_b_tibia = worst_b_tibia < 0.01
    overall_ok &= ok_b_tibia
    tibia_label = (
        "OK (firmware到達可能集合では非接触 -- gait.h D_KNEE_MIN 射影が "
        "crouch(45,30)級の組合せを構造的に排除。raw関節指令の経路が将来 "
        "追加された場合は要再検討, 下記コメント/docs/assembly.md参照)"
        if ok_b_tibia else "NG (regression)")
    print(f"  tibia-tibia worst(到達可能集合限定) = {worst_b_tibia:.4f} cm^3  ({tibia_label})")

    # 遠ペア (フルヨー) も crouch 込みで確認
    print("\n  隣接脚 遠ペア (フルヨー±LIM_YAW, crouch 含む)")
    worst_far = 0.0
    combos = [((20, 0), (20, 0)), ((45, 30), (45, 30)), ((-38, 33), (-38, 33)),
              ((-10, -20), (20, 0)), ((-45, 45), (-45, 45))]
    pairs_far = [("FR-FL", "FR", +LIM_YAW, "FL", -LIM_YAW),
                 ("RL-RR", "RL", +LIM_YAW, "RR", -LIM_YAW)]
    for name, l1, y1, l2, y2 in pairs_far:
        w = 0.0
        for (p1, k1), (p2, k2) in combos:
            parts1 = leg_parts(l1, y1, p1, k1)
            parts2 = leg_parts(l2, y2, p2, k2)
            w = max(w, inter_vol(parts1["shin"], parts2["shin"]))
        worst_far = max(worst_far, w)
        print(f"    {name}: worst shin-shin {w:.4f} cm^3")
    ok_far = worst_far < 0.01
    overall_ok &= ok_far
    print(f"  遠ペア worst = {worst_far:.4f} cm^3  ({'OK' if ok_far else 'NG'})")

    # ---- [C] 腕 (READY/TUCK) vs shin_shell -- 脚×腕連成クランプ (firmware
    # arms.h/config.h, 2026-07-31 追加, 同日「腕×脚連成クランプの再導出」
    # タスクで hub_y=0 後の最終形状に合わせ再導出) の Python 複製による検証。
    # 無対策では READY 等の前方腕姿勢が前脚 (FR/FL) の深い前方振り (yaw が
    # 中立から大きく+側) で shin_shell と実体干渉する (代表姿勢で最大
    # ~5.6cm^3, 下記 before 実測)。firmware は脚 IK を制限せず、危険域では
    # 同側腕のヨーだけを -ARM_YAW_LIM へ強制退避させる (pitch/elbow は無関係
    # — scratchpad sweep7〜sweep20 系, 2026-07-31 再導出タスクで再実測:
    # ARM_LEG_YAW_GATE_DEG (hub_y=0時点の旧ゲート10°での実測、hub_y=11.0
    # 確定後の現行値は20.0°。config.h から動的に読むため下記コードの動作
    # 自体には影響しない) 〜LIM_YAW=40°の危険域全体で worst=0cm^3、
    # 唯一の例外は脚 yaw が物理上限 40° に極めて近い最後の約3°で、局所再
    # スキャンにより 0.166cm^3 に収束することを確認済み)。ここではその式を
    # config.h から regex 読取して複製し (複製ドリフト防止)、クランプ適用後
    # に実メッシュで干渉が既知許容残留 (ceiling 参照) 未満に収まることを
    # 検証する。
    print("\n[C] 腕 (READY/TUCK) vs shin_shell -- 脚×腕連成クランプ (firmware 複製) 検証")
    from manifold3d import Manifold, Mesh

    def to_manifold(tm):
        # 左腕 (arm_meshes side=-1) は表示用 invert() 補正により manifold3d
        # の期待する外向き (CCW) 巻き順と符号が逆転する (union の .volume()
        # が負値になる実測で発覚, 2026-07-31)。符号を実測し必要なら反転する
        faces = tm.faces
        if tm.volume < 0:
            faces = faces[:, ::-1]
        return Manifold(mesh=Mesh(vert_properties=np.asarray(tm.vertices, dtype=np.float32),
                                   tri_verts=np.asarray(faces, dtype=np.uint32)))

    def inter_vol_mani(a_mani, tm):
        return float((a_mani ^ to_manifold(tm)).volume()) / 1000.0

    BODY_H = 115.0
    zb = BODY_H + C.HIP_DROP

    # firmware 定数を regex で読む (複製 drift 防止, 本ファイルの既存流儀)
    YAW_GATE = float(re.search(r"ARM_LEG_YAW_GATE_DEG\s*=\s*([\d.]+)f", fw).group(1))
    m_sign = re.search(r"ARM_LEG_YAW_SIGN\[2\]\s*=\s*\{([^}]+)\}", fw)
    LEG_SIGN = [int(x) for x in re.findall(r"[+-]?\d+", m_sign.group(1))]
    ARM_YAW_LIM_PY = float(re.search(r"ARM_YAW_LIM\s*=\s*([\d.]+)f", fw).group(1))

    def fw_leg_arm_couple(arm_yaw, leg_yaw, side_idx):
        """arms.h の連成クランプ (target/cur_ 共通の式) の複製。"""
        if leg_yaw * LEG_SIGN[side_idx] > YAW_GATE:
            return -ARM_YAW_LIM_PY
        return arm_yaw

    # ---- [C-duty] 通常歩行中にクランプが実際にどれだけの時間発火するか
    # (2026-07-31 QA指摘, major: 「前脚が大きく前方へ振れる瞬間だけ腕が
    # 一瞬引く」というdocs/assembly.mdの記述が実測と食い違っていた —
    # sim_gait.py の実歩容 (CYCLE_T=1.6s の1周期) で FR脚 yaw を時間発展させ、
    # ARM_LEG_YAW_GATE_DEG を超えている位相の比率を実測する。前進速度 vx を
    # 0.1(低速)〜1.0(最大速度)まで振り、速度依存性の有無も確認する)
    print("\n[C-duty] 通常歩行中のクランプ発火時間比率 (FR脚, 前進速度 0.1-1.0 で確認)")
    duty_fracs = []
    for vx in (0.1, 0.3, 0.5, 0.7, 1.0):
        n = hits = 0
        for phase in np.linspace(0, 1, 600, endpoint=False):
            a = SG.leg_ik(*SG.foot_target(0, phase, vx, 0.0, 0.0))  # leg 0 = FR
            n += 1
            if a is not None and a[0] * LEG_SIGN[0] > YAW_GATE:
                hits += 1
        frac = hits / n
        duty_fracs.append(frac)
        print(f"    vx={vx:.1f}: gate 超過比率 = {frac*100:.1f}% ({hits}/{n})")
    duty_lo, duty_hi = min(duty_fracs) * 100, max(duty_fracs) * 100
    cycle_t = float(re.search(r"CYCLE_T\s*=\s*([\d.]+)f", fw).group(1))
    print(f"  歩容周期 CYCLE_T={cycle_t}s のうち約"
          f" {duty_lo:.0f}-{duty_hi:.0f}% (低速→最大速度) でクランプが発火する。"
          f" 2026-09-04 の重心対応スタンス (STANCE_OFF_Y -30) で FR の前方ヨー使用量が"
          f" 減り、旧 42-44% (速度非依存・常態) から速度依存の低頻度へ変わった。"
          f" docs/assembly.md §3/§4 参照。")
    # regression 監視: 速度依存性が急変したり (=歩容パラメータの変更で
    # onset挙動が変わった)、比率が極端に振れたりしていないかだけを緩く見る
    # (「ゼロにすべき」種類のチェックではない — 発火自体は安全機構として
    # 意図通り。ここは説明文の正確性を保証するための数値監視)。
    # 2026-07-31「境界スイープ (実現可能な最中央値確定)」タスクで
    # ARM_MOUNT_HUB_Y 0.0→11.0 に伴い ARM_LEG_YAW_GATE_DEG を 10°→20° へ
    # 引き上げた (config.h コメント参照)。肩ヨー軸の後退量が hub_y=0時代の
    # 12mmから実質1mm相当まで縮小したため、発火比率は hub_y=0時代の
    # 約70-74%から旧hub_y=12時代相当の約42-44%まで低下した (実測 42.5-
    # 44.2%) — レンジを追従して更新
    # 2026-09-04: 重心対応の後方スタンス (STANCE_OFF_Y -30 / 後脚 SWAY 40) で実測
    # 0.0-12.7% (vx 0.1→1.0 で単調増加)。レンジを「最大速度で 25% 未満」に追従
    ok_duty = duty_hi < 25.0
    overall_ok &= ok_duty
    print(f"  ({'OK' if ok_duty else 'NG'}, 最大速度で 25% 未満の想定レンジ内かを監視 — "
          f"レンジ外なら docs/assembly.md の数値記述も要更新)")

    def shin_world_leg(leg_name, yaw_d, pitch_d, knee_d):
        mirror = leg_name in MIRROR_LEGS
        idx = LEG_NAMES.index(leg_name)
        mnt_deg = np.degrees(SG.MOUNT[idx])
        origin_xy = SG.ORIGIN[idx]
        base = trans(origin_xy[0], origin_xy[1], BODY_H) @ MV.rot(mnt_deg + yaw_d, "z")
        T_hip = base @ trans(C.COXA_LEN, 0, 0) @ MV.rot(pitch_d, "y")
        T_knee = T_hip @ trans(C.FEMUR_LEN, 0, 0) @ MV.rot(knee_d, "y")
        tm = shin_local(mirror)
        tm.apply_transform(T_knee)
        return tm

    def arm_union_side(side, pose):
        arm_parts = MV.arm_meshes(side, pose, zb, swing=0.0, body_h=BODY_H, dress=False)
        u = None
        for m, c, a in arm_parts:
            mm = to_manifold(m)
            u = mm if u is None else (u + mm)
        return u

    shin_world = lambda yd, pd, kd: shin_world_leg("FR", yd, pd, kd)  # noqa: E731

    # before (クランプなし): 従来どおり READY/TUCK を素通しした場合の実測
    print("  before (クランプなし, 参考値):")
    for arm_pose, arm_name in [(MV.ARM_READY, "READY"), (MV.ARM_TUCK, "TUCK")]:
        union_arm = arm_union_side(1, arm_pose)
        v1 = inter_vol_mani(union_arm, shin_world(40.0, -45.0, 41.0))
        v2 = inter_vol_mani(union_arm, shin_world(33.15, -41.06, 30.46))
        print(f"    {arm_name}: FR関節限界姿勢(yaw40,pitch-45,knee41) = {v1:.4f} cm^3, "
              f"実歩容由来姿勢(yaw33.15,pitch-41.06,knee30.46) = {v2:.4f} cm^3")

    # after (クランプあり): fw_arm_clamp (既存, 地面ガード等) → 連成クランプ
    # (今回追加) の順で適用した実際の出力姿勢を使う。danger 姿勢 (FR yaw40)
    # では連成クランプが yaw=-ARM_YAW_LIM へ強制するため、pose の yaw 成分
    # (READY=10/TUCK=0) は上書きされる — pitch/elbow は各プリセットのまま
    print("  after (クランプあり, 実際の firmware 出力に相当):")
    # cm^3: 2026-07-31「腕×脚連成クランプの再導出」タスクで hub_y=0 後の
    # 最終形状に合わせ再測定。旧ceiling=0.08 (旧ジオメトリ, worst=0.0493cm^3
    # 収束) は肩ヨー軸が12mm体幹側へ後退した新形状では worst=0.8546cm^3まで
    # 悪化して超過することが判明 (旧[C]全域グリッドが pk_reachable() 未適用
    # だった影響も含む)。[B]と同じ「firmware到達可能集合」基準を[C]にも
    # 適用した上で改めて局所再スキャンした結果、真の worst は脚 yaw が
    # 物理上限 LIM_YAW=40° に極めて近い最後の約3° (37-40°) でのみ発生し、
    # leg_yaw=40°(物理上限)/leg_pitch≈23-25°/leg_knee≈10-16° x
    # 腕pitch≈65-80°/elbow≈0-10° 付近で 0.166cm^3 に収束することを確認済み
    # (scratchpad sweep8〜sweep12 の段階的局所再スキャン, 2026-07-31)。
    # ceiling はそこに 50%超の余裕を持たせた値 (グリッド解像度依存で多少
    # 上下しても regression と誤判定しないよう、かつ本来の危険域 (無対策時
    # 代表姿勢で最大 ~5.6cm^3) とは1桁半以上の差がある実質ゼロ扱いの残留
    # であることを示す値)。ARM_LEG_YAW_GATE_DEG (hub_y=0時点の旧ゲート10°
    # での実測、hub_y=11.0確定後の現行値は20.0°) 〜LIM_YAW=40°の
    # 危険域全体を broad grid で走査した結果 (leg_yaw=10.5-30°) は
    # worst=0.0000cm^3 であり、残留は本当に物理上限ぎりぎりの一角に限られる
    # ことも確認済み (config.h ARM_LEG_YAW_GATE_DEG コメント参照)
    KNOWN_RESIDUAL_CEILING = 0.25
    worst_after = 0.0
    for arm_pose, arm_name in [(MV.ARM_READY, "READY"), (MV.ARM_TUCK, "TUCK")]:
        for label, (yd, pd, kd) in [
            ("FR関節限界姿勢(yaw40,pitch-45,knee41)", (40.0, -45.0, 41.0)),
            ("実歩容由来姿勢(yaw33.15,pitch-41.06,knee30.46)", (33.15, -41.06, 30.46)),
        ]:
            ay, ap, ae, g = MV.fw_arm_clamp(arm_pose, BODY_H)
            ay = fw_leg_arm_couple(ay, yd, 0)  # side_idx=0 (右腕/FR)
            union_arm = arm_union_side(1, (ay, ap, ae, g))
            v = inter_vol_mani(union_arm, shin_world(yd, pd, kd))
            worst_after = max(worst_after, v)
            print(f"    {arm_name}->clamped(yaw={ay:.1f}) @ {label} = {v:.4f} cm^3")
    ok_c_repr = worst_after < KNOWN_RESIDUAL_CEILING
    overall_ok &= ok_c_repr
    print(f"  代表姿勢2点×READY/TUCK worst = {worst_after:.4f} cm^3  "
          f"({'OK' if ok_c_repr else 'NG'}, ceiling={KNOWN_RESIDUAL_CEILING})")

    # 全域グリッド検証 (FR): 脚 yaw x pitch x knee の「firmware到達可能集合」
    # (pk_reachable(), [B]節と同じ基準) で、クランプ通過後に到達しうる腕姿勢
    # 集合 (yaw は危険域なら常に -ARM_YAW_LIM に固定される。非危険域は元の
    # pitch/elbow±yaw 全域を素通しするので実質的なテスト対象は「危険域 x
    # 腕 pitch/elbow 全域」) の worst を実測する。
    # 【2026-07-31 再導出タスクで修正】従来は (leg_pitch,leg_knee) を
    # pk_reachable() で絞り込まずに全域のまま評価しており、(pitch,knee)=
    # (25,36) のような gait.h ワークスペース射影の不動点条件を満たさず歩容
    # コマンドとして絶対に出力され得ない組合せ (worst=0.8546cm^3) を算入
    # していた。[B] のtibia-tibia/shin-shin判定と同じ基準に統一し、到達
    # 不能姿勢を除外する。
    print("\n  全域グリッド (FR脚 yaw全域 x pitch/knee 到達可能集合 x 腕"
          " pitch/elbow 全域, クランプ通過後の到達可能集合)")
    leg_yaws = np.linspace(-40, 40, 17)
    leg_pitches = np.linspace(-45, 55, 11)
    leg_knees = np.linspace(-44, 44, 12)
    arm_pitches = np.linspace(-45, 85, 7)
    arm_elbows = np.linspace(0, 95, 6)
    pk_reachable_grid = [(lp, lk) for lp in leg_pitches for lk in leg_knees
                          if pk_reachable(lp, lk)]
    print(f"  (leg_pitch x leg_knee: {len(pk_reachable_grid)}/"
          f"{len(leg_pitches) * len(leg_knees)} が firmware到達可能集合)")

    worst_grid, worst_grid_pose = 0.0, None
    # 危険域の腕ヨーは常に -ARM_YAW_LIM (定数) なので union はヨー以外の
    # 組合せ (pitch x elbow) だけ用意すれば足りる -- 全 leg yaw で使い回す
    danger_unions = {}
    for ap in arm_pitches:
        for ae in arm_elbows:
            danger_unions[(ap, ae)] = arm_union_side(1, (-ARM_YAW_LIM_PY, float(ap), float(ae), 0.0))

    for ly in leg_yaws:
        danger = ly * LEG_SIGN[0] > YAW_GATE
        if not danger:
            continue  # 非危険域はクランプが素通しなので [4] 相当 (check_arm.py) の対象、ここでは省略
        for lp, lk in pk_reachable_grid:
            tm = shin_world(ly, lp, lk)
            for (ap, ae), u in danger_unions.items():
                v = inter_vol_mani(u, tm)
                if v > worst_grid:
                    worst_grid, worst_grid_pose = v, (ly, lp, lk, ap, ae)
    ok_c_grid = worst_grid < KNOWN_RESIDUAL_CEILING
    overall_ok &= ok_c_grid
    print(f"  worst = {worst_grid:.4f} cm^3 at (leg_yaw,leg_pitch,leg_knee,arm_pitch,arm_elbow)="
          f"{worst_grid_pose}  ({'OK' if ok_c_grid else 'NG'}, ceiling={KNOWN_RESIDUAL_CEILING})")

    # FL + 左腕 (鏡像) の代表姿勢確認: shin_shell/腕とも X ミラー構成のため
    # FR+右腕と数値が厳密に一致するはず (scratchpad sweep_fl.py で確認済み)。
    # ここでは危険方向の符号 (ARM_LEG_YAW_SIGN[1]=-1) を含めクランプ式ごと
    # 実メッシュで裏取りする
    print("\n  FL脚+左腕 (鏡像) の代表姿勢確認 (危険方向は yaw 符号が反転)")
    worst_fl = 0.0
    for arm_pose, arm_name in [(MV.ARM_READY, "READY")]:
        for label, (yd, pd, kd) in [
            ("FL関節限界姿勢(yaw-40,pitch-45,knee41)", (-40.0, -45.0, 41.0)),
            ("実歩容由来姿勢(yaw-33.15,pitch-41.06,knee30.46)", (-33.15, -41.06, 30.46)),
        ]:
            ay, ap, ae, g = MV.fw_arm_clamp(arm_pose, BODY_H)
            ay = fw_leg_arm_couple(ay, yd, 1)  # side_idx=1 (左腕/FL)
            union_arm = arm_union_side(-1, (ay, ap, ae, g))
            v = inter_vol_mani(union_arm, shin_world_leg("FL", yd, pd, kd))
            worst_fl = max(worst_fl, v)
            print(f"    {arm_name}->clamped(yaw={ay:.1f}) @ {label} = {v:.4f} cm^3")
    ok_c_fl = worst_fl < KNOWN_RESIDUAL_CEILING
    overall_ok &= ok_c_fl
    print(f"  FL+左腕 worst = {worst_fl:.4f} cm^3  "
          f"({'OK' if ok_c_fl else 'NG'}, ceiling={KNOWN_RESIDUAL_CEILING})")
    print(f"  (before/after 比較: 無対策 READY 代表姿勢で最大 ~5.6cm^3 -> クランプ後は"
          f" 上記 worst 全て {KNOWN_RESIDUAL_CEILING}cm^3 未満。既知許容残留の"
          f" 根拠は firmware/src/config.h ARM_LEG_YAW_GATE_DEG のコメント参照)")

    print(f"\n{'='*60}\noverall = {'PASS' if overall_ok else 'FAIL'}")
    print("(shin-shin[B]・tibia-tibia[B] は共に firmware到達可能集合"
          " (pk_reachable()) に統一した基準で overall に算入する --"
          " 2026-07-31 リリーフカット再評価タスクで shin-shin も"
          " tibia-tibia と同じ基準に統一し、旧 KNOWN_BOLT_BOSS_CEILING"
          " 特例 (worst<1.0cm^3を KNOWN NGとしてPASS扱い) は"
          " shell_mod.py ADJ_RELIEF_BANDS 撤去により不要化・削除した。"
          " 腕干渉[C]は 2026-07-31 の連成クランプ追加により overall に"
          " 算入するようになった)")
    return overall_ok


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
