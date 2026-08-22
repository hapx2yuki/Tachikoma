#!/usr/bin/env python3
"""脚アセンブリの干渉チェック + 組立プレビュー生成。

coxa 原点に固定し、femur を股ピッチ角、tibia を膝角で回転配置して
各姿勢でのペア交差体積を計算する。交差 > 0.01 cm^3 なら NG。
併せて docs/preview_leg_assembly.png を出力する。
"""
import sys
from pathlib import Path

import numpy as np
import trimesh

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hardware" / "src"))
import config as C  # noqa: E402

STL = ROOT / "hardware" / "stl"


def rot_y(deg):
    t = np.radians(deg)
    m = np.eye(4)
    m[0, 0] = np.cos(t); m[0, 2] = np.sin(t)
    m[2, 0] = -np.sin(t); m[2, 2] = np.cos(t)
    return m


def trans(x, y, z):
    m = np.eye(4)
    m[:3, 3] = [x, y, z]
    return m


def servo_case_mesh(mirror: bool = False):
    """STD サーボの実体 (ケース+ギヤヘッド) を箱枠ローカル座標で近似。

    タブ面 y=0、ケースは -Y へ TAB_BELOW、ギヤヘッドは +Y へ ABOVE_TAB。
    ホーンハブ円柱も含める。mirror=True は y 座標を符号反転して直接生成する
    (trimesh の鏡映+invert は boolean エンジンが拒否するため)。
    """
    P = C.LEG_SERVO
    cx = P["L"] / 2 - P["SHAFT_OFF"]
    s = -1.0 if mirror else 1.0
    case = trimesh.creation.box((P["L"], P["TAB_BELOW"] + P["ABOVE_TAB"], P["W"]))
    case.apply_translation([-cx, s * (P["ABOVE_TAB"] - P["TAB_BELOW"]) / 2, 0])
    hub = trimesh.creation.cylinder(radius=P["HORN_HUB_D"] / 2,
                                    height=P["HORN_HUB_H"] + 1)
    hub.apply_transform(trimesh.transformations.rotation_matrix(
        np.pi / 2, [1, 0, 0]))
    hub.apply_translation([0, s * (P["ABOVE_TAB"] + P["HORN_HUB_H"] / 2), 0])
    return trimesh.util.concatenate([case, hub])


MIRROR_LEGS = {"FR", "RL"}   # v3: 45°ペア対策でミラー版 (_m) を使う脚


def leg_at(pitch_deg: float, knee_deg: float, mirror: bool = False):
    """関節角を与えて (coxa+股サーボ, femur+膝サーボ, tibia) を返す。

    firmware (ik.h) と同じサーボ角規約:
    pitch: 股ピッチ (0=femur 水平, +で脚先が下がる)
    knee:  膝サーボ角 (femur 相対。0=tibia が femur に垂直)
    サーボの実体もそれぞれの親パーツに合体させて検査する
    (印刷部品同士だけ見てケース干渉を見落とした反省から)。
    mirror=True で FR/RL 用ミラー版 (_m STL + サーボも y 反転) を使う。
    """
    sfx = "_m" if mirror else ""
    coxa = trimesh.load(STL / f"coxa_bracket{sfx}.stl")
    femur = trimesh.load(STL / f"femur_link{sfx}.stl")
    tibia = trimesh.load(STL / f"tibia_link{sfx}.stl")

    pitch_servo = servo_case_mesh(mirror)
    pitch_servo.apply_transform(trans(C.COXA_LEN, 0, 0))
    coxa = trimesh.util.concatenate([coxa, pitch_servo])

    T_hip = trans(C.COXA_LEN, 0, 0) @ rot_y(pitch_deg)
    knee_servo = servo_case_mesh(mirror)
    knee_servo.apply_transform(trans(C.FEMUR_LEN, 0, 0))
    femur = trimesh.util.concatenate([femur, knee_servo])
    femur.apply_transform(T_hip)

    T_knee = T_hip @ trans(C.FEMUR_LEN, 0, 0) @ rot_y(knee_deg)
    tibia.apply_transform(T_knee)
    return coxa, femur, tibia


def pair_intersection(a: trimesh.Trimesh, b: trimesh.Trimesh) -> float:
    try:
        inter = trimesh.boolean.intersection([a, b], engine="manifold")
    except Exception:
        return float("nan")
    if inter is None or inter.is_empty:
        return 0.0
    return float(inter.volume) / 1000.0  # cm^3


def main():
    poses = [
        ("neutral", 20, 0), ("crouch", 45, 30), ("high", -10, -20),
        ("reach", 0, -35), ("tuck", 55, 45),
        ("sprawl", -38, 33), ("sprawl+", -45, 45), ("sprawl-", -45, -20),
    ]
    print(f"{'pose':12s} {'pitch':>6s} {'knee':>6s}   coxa-fem  fem-tib  coxa-tib")
    worst = 0.0
    for mirror in (False, True):
        tag = "_m" if mirror else ""
        for name, p, k in poses:
            coxa, femur, tibia = leg_at(p, k, mirror=mirror)
            cf = pair_intersection(coxa, femur)
            ft = pair_intersection(femur, tibia)
            ct = pair_intersection(coxa, tibia)
            worst = max(worst, cf, ft, ct)
            flag = "" if max(cf, ft, ct) < 0.01 else "  << 干渉!"
            print(f"{name + tag:12s} {p:6.0f} {k:6.0f}   "
                  f"{cf:8.3f} {ft:8.3f} {ct:8.3f}{flag}")
    print(f"\nworst = {worst:.3f} cm^3  ({'OK' if worst < 0.01 else 'NG'})")

    # ---- 隣接脚どうしの干渉 (v3 放射配置: 45° ペアは内側ヨーを LIM_YAW_IN、
    # 遠いペアは LIM_YAW まで互いへ寄せた最悪組合せ)。制限値は firmware から実読
    import re
    fw = (STL.parent.parent / "firmware" / "src" / "config.h").read_text()
    LIM = float(re.search(r"LIM_YAW\s*=\s*([\d.]+)f", fw).group(1))
    LIM_IN = float(re.search(r"LIM_YAW_IN\s*=\s*([\d.]+)f", fw).group(1))
    LIM_SUM = float(re.search(r"LIM_YAW_IN_SUM\s*=\s*([\d.]+)f", fw).group(1))

    def rot_z(deg):
        t = np.radians(deg)
        m = np.eye(4)
        m[0, 0], m[0, 1], m[1, 0], m[1, 1] = np.cos(t), -np.sin(t), np.sin(t), np.cos(t)
        return m

    def leg_world(name, yaw, p, k):
        m = trimesh.util.concatenate(list(leg_at(p, k, mirror=name in MIRROR_LEGS)))
        m.apply_transform(rot_z(C.LEG_ANGLES[name] + yaw))
        m.apply_translation([C.HIPS[name][0], C.HIPS[name][1], 0])
        return m

    # 45° ペア (FR-RR/FL-RL) は firmware の 2 段クランプ (単側 LIM_IN +
    # 和 LIM_SUM) が許す最悪ヨー配分 × **IK 到達可能な関節極値姿勢** で検査。
    # サーボ空間の全極値 (深タック等) は IK が原理的に出力しない (足先 r<0)
    # ため運用エンベロープ外 — 手ポーズ時の注意として assembly.md に明記。
    # 前後の遠いペアはフルヨー + サーボ空間極値のまま (全て 0 を確認済)
    sys.path.insert(0, str(ROOT / "tools"))
    import sim_gait as SG
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
    ik_poses.append((20, 0))
    print(f"\nIK 到達可能極値姿勢 (pitch,knee): {ik_poses}")

    pairs45 = [
        ("FR-RR(単側FR)", "FR", -LIM_IN, "RR", 0.0),
        ("FR-RR(単側RR)", "FR", 0.0, "RR", +LIM_IN),
        ("FR-RR(和・対称)", "FR", -LIM_SUM / 2, "RR", +LIM_SUM / 2),
        ("FR-RR(和・偏り)", "FR", -LIM_IN, "RR", +(LIM_SUM - LIM_IN)),
    ]
    print(f"隣接脚の干渉 45°ペア (単側 ±{LIM_IN:.1f}°, 和 ≤{LIM_SUM:.0f}°, "
          f"IK 到達姿勢のみ):")
    worst2 = 0.0
    for name, l1, y1, l2, y2 in pairs45:
        w = 0.0
        for p1, k1 in ik_poses:
            for p2, k2 in ik_poses:
                v = pair_intersection(leg_world(l1, y1, p1, k1),
                                      leg_world(l2, y2, p2, k2))
                w = max(w, v)
        worst2 = max(worst2, w)
        print(f"  {name}: worst {w:.3f} cm^3")

    combos = [((20, 0), (20, 0)), ((45, 30), (45, 30)), ((-38, 33), (-38, 33)),
              ((-10, -20), (20, 0)), ((-45, 45), (-45, 45))]
    pairs_far = [("FR-FL(前寄せ)", "FR", +LIM, "FL", -LIM),
                 ("RL-RR(後寄せ)", "RL", +LIM, "RR", -LIM)]
    print(f"隣接脚の干渉 遠ペア (フルヨー ±{LIM:.0f}°, サーボ空間極値):")
    for name, l1, y1, l2, y2 in pairs_far:
        for (p1, k1), (p2, k2) in combos:
            v = pair_intersection(leg_world(l1, y1, p1, k1),
                                  leg_world(l2, y2, p2, k2))
            worst2 = max(worst2, v)
            print(f"  {name} pose ({p1:3.0f},{k1:3.0f})/({p2:3.0f},{k2:3.0f}): "
                  f"{v:.3f} cm^3")
    print(f"隣接脚 worst = {worst2:.3f} cm^3  ({'OK' if worst2 < 0.01 else 'NG'})")

    # ---- ポッド (Cabin) と後脚: v3 でポッドが脚と同じ高さの後方ネックに
    # 接続されるため、後脚のポッド側ヨーは firmware LIM_YAW_POD でクランプ。
    # その限界値 × IK 到達極値姿勢での実メッシュ干渉を検査する。
    # シェル配置は make_visuals.shell_ghosts と共通 (複製 drift 防止)
    LIM_POD = float(re.search(r"LIM_YAW_POD\s*=\s*([\d.]+)f", fw).group(1))
    from make_visuals import shell_ghosts
    g = shell_ghosts(C.HIP_DROP, alpha=1.0)  # プレート下面 (股ピッチ面基準)
    pod = trimesh.util.concatenate([g[0][0], g[1][0]])
    print(f"\nポッド (Cabin) と後脚 (ポッド側ヨー +{LIM_POD:.0f}°, IK 到達姿勢):")
    worst_pod = 0.0
    for leg in ("RL", "RR"):
        ypod = +LIM_POD if leg == "RL" else -LIM_POD
        w = 0.0
        for p, k in ik_poses:
            v = pair_intersection(leg_world(leg, ypod, p, k), pod)
            w = max(w, v)
        worst_pod = max(worst_pod, w)
        print(f"  {leg} yaw={ypod:+.0f}: worst {w:.3f} cm^3")
    print(f"ポッド worst = {worst_pod:.3f} cm^3  "
          f"({'OK' if worst_pod < 0.01 else 'NG'})")

    # ---- バッテリークレードル (プレート下面吊り) と脚の干渉:
    # クレードルはプレート下面 z=0 から下がる。脚座標系はヨー軸原点 z=0 =
    # プレート下面から HIP_DROP 下がった股ピッチ面なので +HIP_DROP に置く
    HIP_DROP = C.HIP_DROP  # プレート下面 → 股ヨー軸原点 (config.py が唯一の正)
    cradle = trimesh.load(STL / "battery_cradle.stl")
    cradle.apply_translation([0, 0, HIP_DROP])
    # クレードル本体 + バッテリー実体ダミー (2S 2200: 105×34×24, BOM #5。
    # パックはクレードル前後へオーバーハングするため本体も検査対象)
    batt = trimesh.creation.box((34.0, 105.0, 24.0))
    batt.apply_translation([0, -6.0, HIP_DROP - 16.0])
    obstacle = trimesh.util.concatenate([cradle, batt])
    print("\nバッテリークレードル+パック実体と脚 (内側/ポッド側ヨー最悪):")
    worst3 = 0.0
    for leg in ("FR", "FL", "RL", "RR"):
        yaws = [-LIM_IN if leg in ("FR", "RL") else +LIM_IN]
        if leg in ("RL", "RR"):   # 後脚はポッド側 (外側) ヨーも検査
            yaws.append(+LIM_POD if leg == "RL" else -LIM_POD)
        w_leg = 0.0   # 脚ごとにリセット (累積 max の誤表示を修正)
        for yw in yaws:
            for p, k in ((20, 0), (55, 45), (-45, 45)):
                v = pair_intersection(leg_world(leg, yw, p, k), obstacle)
                w_leg = max(w_leg, v)
        worst3 = max(worst3, w_leg)
        print(f"  {leg} yaw={'/'.join(f'{y:+.0f}' for y in yaws)}: "
              f"worst {w_leg:.3f} cm^3")
    print(f"クレードル worst = {worst3:.3f} cm^3  ({'OK' if worst3 < 0.01 else 'NG'})")

    # ---- 足の地面クリアランス (2026-07-29 接地連鎖修正, 'Leg_Foot 化' 改訂の
    # 継続)。leg 変換チェーンは make_visuals.robot_meshes() の
    # base/T_hip/T_knee/T_foot と同一式。
    #
    # 2026-07-28 時点は「TIBIA_LEN/STEP_H を変更しない限り world z=0 を常に
    # 下回らないことは不可能」として worst z<0 を無条件に受け入れる妥協判定
    # (トゥが足本体より深く潜っていないか、だけを見る) だった。2026-07-29 に
    # 以下で解消: firmware ik.h / tools/sim_gait.py の IK だけが使う「実効
    # (ground-equivalent) tibia 長」TIBIA_LEN_GAIT を、実運用スタンス全域
    # (体高105-130, SWAY 込み全位相) で foot_pad 底 (leg_foot_bored+
    # foot_pad の実メッシュ) が world z を下回らない最小値に校正した
    # (hardware/src/config.py FOOT_GROUND_OFFSET のコメント参照。物理
    # ジオメトリ側の TIBIA_LEN=135mm は無変更 — make_leg.py/tibia 差込
    # ソケットへの影響なし)。この節はその校正が実際に効いているかを
    # 実ビルド STL + 実際の sim_gait ロジックで再検証する。
    #
    # トゥ (Leg_Toe_Black_x12) はスタブ軸に沿って tibia 軸から 20mm 前後
    # 横へ離れるため、上記のような「tibia 軸に沿った一次元オフセット」では
    # 良く表現できない (脚姿勢ごとに横変位の意味が変わる) — 接地基準は
    # 意図どおり foot_pad 底に取り、トゥ先端との実際の relationship は
    # 事実として報告するに留める (tools/data/kit_assembly_front.json の
    # Leg_Toe_Black_x12 エントリ finding も参照)。
    import kit_assembly as KIT
    placements = KIT.load_placements()
    toe_by_leg = {name: [p for p in KIT.by_link(placements, "leg_foot_bored")
                          if p.instance == name or p.instance.startswith(name + "_")]
                  for name in SG._LEGS}
    n_toe = sum(len(v) for v in toe_by_leg.values())

    foot_solid = trimesh.util.concatenate([
        trimesh.load(STL / "leg_foot_bored.stl"),
        trimesh.load(STL / "foot_pad.stl"),
    ])

    # ---- ドリフト検査: config.py の FOOT_GROUND_OFFSET/TIBIA_LEN_GAIT が
    # 実ビルド STL + 実際の SWAY 込みスタンス全域評価に対して依然 0.1mm
    # 精度で校正されているかを、その場で数値探索し直して突合する
    # (ARM_REACH 73/79 事故と同型の drift 穴を塞ぐ — pitfalls #31)。
    def worst_stance_z(tibia_gait, body_hs, phases):
        orig_tibia = SG.TIBIA
        SG.TIBIA = tibia_gait
        try:
            worst = float("inf")
            for body_h in body_hs:
                for phase in phases:
                    for li, leg in enumerate(SG._LEGS):
                        p_frac = (phase + SG.PHASE_OFF[li]) % 1.0
                        if p_frac >= SG.DUTY - 1e-9:
                            continue  # 遊脚 (地面より高いはずなので支配的でない)
                        lx, ly, lz = SG.foot_target(li, phase, 0.0, 0.0, 0.0, body_h=body_h)
                        a = SG.leg_ik(lx, ly, lz)
                        if a is None:
                            continue
                        yaw_d, pitch_d, knee_d = a
                        mnt = np.degrees(SG.MOUNT[li])
                        base = (trans(SG.ORIGIN[li][0], SG.ORIGIN[li][1], body_h)
                                @ rot_z(mnt + yaw_d))
                        T_hip = base @ trans(C.COXA_LEN, 0, 0) @ rot_y(pitch_d)
                        T_knee = T_hip @ trans(C.FEMUR_LEN, 0, 0) @ rot_y(knee_d)
                        T_foot = T_knee @ trans(0, 0, -C.TIBIA_LEN)
                        fm = foot_solid.copy()
                        fm.apply_transform(T_foot)
                        worst = min(worst, float(fm.vertices[:, 2].min()))
            return worst
        finally:
            SG.TIBIA = orig_tibia

    _cal_body_hs = np.linspace(*SG.BODY_H_RANGE, 6)
    _cal_phases = np.linspace(0.0, 1.0, 72, endpoint=False)
    _w_current = worst_stance_z(SG.TIBIA, _cal_body_hs, _cal_phases)
    # 現在の校正値が「めり込まない最小値」から 0.1mm 精度で離れていないかを
    # 二分探索で追認する (config.py の FOOT_GROUND_OFFSET が stale でないか)
    _lo, _hi = C.TIBIA_LEN, C.TIBIA_LEN + 40.0
    for _ in range(24):
        _mid = (_lo + _hi) / 2
        if worst_stance_z(_mid, _cal_body_hs, _cal_phases) < 0:
            _lo = _mid
        else:
            _hi = _mid
    _tibia_gait_optimal = _hi
    _drift = C.TIBIA_LEN_GAIT - _tibia_gait_optimal
    print(f"\n足の地面クリアランス (leg_foot_bored+foot_pad 実メッシュ, "
          f"体高{SG.BODY_H_RANGE[0]:.0f}-{SG.BODY_H_RANGE[1]:.0f}, "
          f"SWAY込み全位相スタンス相のみ):")
    print(f"  config.py TIBIA_LEN_GAIT = {C.TIBIA_LEN_GAIT:.2f}mm での worst world z "
          f"= {_w_current:+.3f}mm ({'OK' if -0.15 <= _w_current <= 1.5 else 'NG'} — "
          f"埋め込み側 -0.15mm 未満は NG, 浮き側は body_h の現物合わせで吸収できる"
          f"範囲として 1.5mm まで許容)")
    print(f"  再校正 (二分探索) による最適値 = {_tibia_gait_optimal:.2f}mm "
          f"(config.py との差 {_drift:+.2f}mm, "
          f"{'OK' if abs(_drift) < 0.15 else 'NG — config.py の FOOT_GROUND_OFFSET が drift — 要更新'})")

    # ---- トゥ (装飾, Leg_Toe_Black_x12) の world z 報告。foot_pad と違い
    # tibia 軸から大きく (20mm前後) 横へ離れるため、この一次元オフセットの
    # 対象にはしない (上記コメント参照) — 実際の位置関係を歩容全域で
    # 事実として報告するのみ (合否判定はしない: 装飾パーツであり許容は
    # tools/data/kit_assembly_front.json の finding に記載済み)
    worst_toe_z = float("inf")
    worst_toe_at = None
    n_pose = 0
    for body_h in np.linspace(*SG.BODY_H_RANGE, 6):
        for phase in np.linspace(0.0, 1.0, 24, endpoint=False):
            for li, leg in enumerate(SG._LEGS):
                lx, ly, lz = SG.foot_target(li, phase, 0.0, 0.0, 0.0, body_h=body_h)
                a = SG.leg_ik(lx, ly, lz)
                if a is None:
                    continue
                n_pose += 1
                yaw_d, pitch_d, knee_d = a
                mnt = np.degrees(SG.MOUNT[li])
                base = (trans(SG.ORIGIN[li][0], SG.ORIGIN[li][1], body_h)
                        @ rot_z(mnt + yaw_d))
                T_hip = base @ trans(C.COXA_LEN, 0, 0) @ rot_y(pitch_d)
                T_knee = T_hip @ trans(C.FEMUR_LEN, 0, 0) @ rot_y(knee_d)
                T_foot = T_knee @ trans(0, 0, -C.TIBIA_LEN)
                for p in toe_by_leg[leg]:
                    m = KIT.oriented_mesh(p)
                    m.apply_transform(T_foot)
                    zmin = float(m.vertices[:, 2].min())
                    if zmin < worst_toe_z:
                        worst_toe_z, worst_toe_at = zmin, (leg, body_h, phase)
    print(f"  トゥ (Leg_Toe_Black_x12 x{n_toe}, 装飾) 最小 world z = "
          f"{worst_toe_z:+.2f}mm at {worst_toe_at} [参考値 — 甲コラムの tibia 軸から"
          f"横へ大きく (最大22mm) 離れるため上記の一次元校正では拾いきれない。"
          f"foot_pad が実接地を担う設計 (装飾のトゥがこれより深く見えても、"
          f"接地力は主に foot_pad が受ける想定)]")

    # ---- [8] leg_foot_bored の tibia 差込プラグ ↔ tibia_link ソケットボアの
    # 実体干渉 (2026-07-28 レビュー finding, critical への回帰チェック):
    # 旧版はプラグに追加した抜け止めリップ (D=FOOT_SOCKET_D+1.2=11.2mm) が
    # ソケットボア (段付きのない定径穴, D=FOOT_SOCKET_D+2*CLEAR=10.4mm) の
    # 壁へ 21.68mm^3 食い込んでおり、PLA/PETG の剛体同士では物理的に挿入
    # 不能だった (旧 TPU foot_tip は弾性変形で吸収できていたが新設計では
    # 前提が崩れていた)。リップは廃止済みだが、以後の寸法変更で再発しない
    # よう組付け位置での実体ブーリアン交差を恒常検査する
    tibia0 = trimesh.load(STL / "tibia_link.stl")
    foot0 = trimesh.load(STL / "leg_foot_bored.stl")
    foot0.apply_translation([0, 0, -C.TIBIA_LEN])   # tibia_link ローカル原点へ
    plug_overlap = pair_intersection(tibia0, foot0)
    print(f"\n[8] tibia ソケット ↔ leg_foot_bored プラグ 干渉体積: "
          f"{plug_overlap:.4f} cm^3 ({'OK' if plug_overlap < 0.001 else 'NG'})")

    # ---- [9] Leg_Toe_Black_x12 (3本/脚) の相互重なり + leg_foot_bored との
    # 重なり (2026-07-28 レビュー finding, minor: 過去は scratchpad の
    # 一回限りのスクリプトでのみ検証されており、恒常回帰チェックが無かった
    # ため組み込む)。トゥ↔足本体は数%程度の接着代としての軽い埋め込みを
    # 許容する (docs/printing.md 記載の embed 1-3% 目安) — 完全な相互貫通
    # (無関係な大体積の重なり) だけを NG とする
    fr_toes = [p for p in KIT.by_link(placements, "leg_foot_bored")
               if p.instance.startswith("FR")]
    toe_meshes = [KIT.oriented_mesh(p) for p in fr_toes]
    foot_only = trimesh.load(STL / "leg_foot_bored.stl")
    toe_vol = float(toe_meshes[0].volume) / 1000.0 if toe_meshes else 1.0
    worst_toe_toe = 0.0
    for i in range(len(toe_meshes)):
        for j in range(i + 1, len(toe_meshes)):
            worst_toe_toe = max(worst_toe_toe, pair_intersection(toe_meshes[i], toe_meshes[j]))
    worst_toe_foot = 0.0
    for tm in toe_meshes:
        worst_toe_foot = max(worst_toe_foot, pair_intersection(foot_only, tm))
    toe_foot_pct = 100 * worst_toe_foot / toe_vol if toe_vol else float("nan")
    print(f"[9] トゥ相互重なり (FR脚 {len(toe_meshes)}本): worst "
          f"{worst_toe_toe:.4f} cm^3 ({'OK' if worst_toe_toe < 0.001 else 'NG'})")
    print(f"    トゥ↔足本体 重なり (FR脚): worst {worst_toe_foot:.4f} cm^3 "
          f"= トゥ体積の{toe_foot_pct:.2f}% "
          f"({'OK' if toe_foot_pct < 5.0 else 'NG'} — 5%未満は接着代として許容)")

    # ---- [10] 膝 ±45° 密掃引 (femur∩tibia) + 脚リンク単一ボディ検査
    # (2026-08-21 ユーザー発見の回帰: tibia の 45° ウェッジがガード円筒
    # (旧 r16.5) の外でネックプレートを z-16.5..-21 で切断し、膝ディスク
    # 3.36cm^3 が分離した部品を出力していた。既存検査は姿勢クリアランスと
    # 体積照合のみで「1 部品が複数ボディに割れている」ことを見なかった。
    # 修正は tibia ガード r23 + femur ウェブ後退 web_x1=FEMUR_LEN-22.5 —
    # make_leg.py の両コメント参照。ここでは実 STL で膝可動域全体の
    # femur∩tibia = 0 と、脚 3 リンクの単一ボディ性を恒常検査する)
    for _n in ("coxa_bracket", "femur_link", "tibia_link"):
        _m = trimesh.load(STL / f"{_n}.stl")
        _nb = len(_m.split(only_watertight=False))
        print(f"[10] {_n}: {_nb} body ({'OK' if _nb == 1 else 'NG <<< 分離ボディ'})")
    fem_k = trimesh.load(STL / "femur_link.stl")
    fem_k.apply_translation([-C.FEMUR_LEN, 0, 0])   # 膝軸原点系へ
    tib_k0 = trimesh.load(STL / "tibia_link.stl")
    worst_knee = (0.0, 0.0)
    for _ang in np.arange(-45.0, 45.01, 5.0):
        _t = tib_k0.copy()
        _t.apply_transform(trimesh.transformations.rotation_matrix(
            np.radians(_ang), [0, 1, 0]))
        _v = pair_intersection(fem_k, _t)
        if _v > worst_knee[0]:
            worst_knee = (_v, _ang)
    print(f"[10] 膝 ±45° 掃引 femur∩tibia: worst {worst_knee[0]:.4f} cm^3 "
          f"@ {worst_knee[1]:+.0f}deg ({'OK' if worst_knee[0] < 0.001 else 'NG'})")

    # foot_pad 接地面積 (球ドーム, 半径 FOOT_PAD_D/2 の球冠モデル。TPU の
    # 実圧縮量は現物合わせだが、目安として 1.0mm 圧縮時の接地パッチ面積を
    # 報告する: a = pi*(2*R*h - h^2))
    pad_r = C.FOOT_PAD_D / 2
    compress_h = 1.0
    contact_area = np.pi * (2 * pad_r * compress_h - compress_h ** 2)
    print(f"\nfoot_pad 接地面積 (半径{pad_r:.1f}mm 球冠, 圧縮代{compress_h:.1f}mm"
          f" 想定): {contact_area:.1f}mm^2 (x4 脚)")

    # 組立プレビュー (neutral)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    fig = plt.figure(figsize=(15, 5))
    for i, (elev, azim) in enumerate([(20, -70), (5, 0), (10, -90)]):
        ax = fig.add_subplot(1, 3, i + 1, projection="3d")
        meshes = leg_at(20, 0)
        colors = ["#5577cc", "#cc7755", "#55aa77"]
        allpts = []
        light = np.array([0.4, -0.6, 0.7]); light /= np.linalg.norm(light)
        for m, c in zip(meshes, colors):
            tri = m.vertices[m.faces]
            lum = 0.45 + 0.55 * np.clip(m.face_normals @ light, 0, 1)
            base = np.array(matplotlib.colors.to_rgb(c))
            pc = Poly3DCollection(tri, facecolor=np.c_[lum[:, None] * base[None, :],
                                                       np.ones(len(lum))])
            ax.add_collection3d(pc)
            allpts.append(m.vertices)
        pts = np.vstack(allpts)
        cmid = (pts.min(0) + pts.max(0)) / 2
        r = float((pts.max(0) - pts.min(0)).max()) / 2 * 1.05
        ax.set_xlim(cmid[0] - r, cmid[0] + r)
        ax.set_ylim(cmid[1] - r, cmid[1] + r)
        ax.set_zlim(cmid[2] - r, cmid[2] + r)
        ax.set_box_aspect([1, 1, 1]); ax.axis("off")
        ax.view_init(elev=elev, azim=azim)
    fig.tight_layout()
    out = ROOT / "docs" / "preview_leg_assembly.png"
    fig.savefig(out, dpi=110, facecolor="white")
    print(f"saved {out}")


if __name__ == "__main__":
    main()
