#!/usr/bin/env python3
"""色別フィラメント必要量の見積り。

印刷対象 (printing.md の方針):
  - 意匠シェル: 元パーツを 150% (脚の置換対象を除く) … PLA, 壁2/インフィル8%
  - 骨格: hardware/stl (等倍) … PETG, 壁4/インフィル25-40%
  - leg_foot_bored (元 Leg_Foot 加工版) … PLA灰 / foot_pad (隠し接地パッド) … TPU

推定モデル (物理ベース):
  印刷体積 ≈ 表面積 × 実効壁厚 + (ソリッド体積 - 表面積 × 実効壁厚) × インフィル率
  実効壁厚: 壁2 ≈ 1.4mm / 壁4 ≈ 2.4mm (上下面・微小部の寄与込みの経験値)
  (スライサー実測に対する概算。±30% 程度の誤差を見込むこと)
"""
import re
from pathlib import Path

import trimesh

ROOT = Path(__file__).resolve().parent.parent
MODEL, STL = ROOT / "model", ROOT / "hardware" / "stl"
SCALE = 1.5

RHO = {"PLA": 1.24, "PETG": 1.27, "TPU": 1.21}  # g/cm3

# 印刷しない (骨格/加工版で置換 / ロボットに不要) パーツ。stem 完全一致
SKIP = ["Leg_HipJoint_Grey_x4", "Leg_HipJoint_Socket_Grey_x4",
        "Leg_Thigh_Grey_x4", "Leg_KneeJoint_Grey_x4", "Leg_Shin_Blue_x4",
        "Leg_AnkleJoint_Grey_x4_Repaired",  # 旧キットの球関節。本設計は
        # tibia 先端に足を直結するため不使用 (printing.md 参照)
        "Leg_Foot_Grey_x4_Repaired",  # → leg_foot_bored (加工版, new_parts
        # で別途計上) に差し替え。生データは印刷しない
        "Stand_mount_Optional",
        "Head_Eye_White_x3",        # → eye_pod (左右2, 可動眼球) +
        # eye_pod_camera (中央1, 固定カメラ目。2026-07-28 設計変更) に取込
        "Head_Top_Blue",            # → Head_Top_Eyecut (ボア加工版)
        "Head_Bottom_Blue",         # → Head_Bottom_Armcut (腕ソケット拡口版,
        # 2026-07-30 追加。hardware/src/make_head.py 参照)
        "Arm_Left", "Arm_Right",    # → arm_pod_upper/lower (加工版)
        "Arm_Left_Elbow_Grey", "Arm_Right_Elbow_Grey",  # → elbow_shell
        "Arm_Right_Claw_Grey",      # 爪ハブと無関係の別形状 (体積/凸包比0.205
        # = 開放骨組) — 不使用。爪ハブは Arm_Left_Claw_Grey (2026-07-29
        # 固定爪化, 両腕鏡映使用。DOUBLE_SIDED 参照) のみ (make_arm.py
        # claw_mount() docstring 参照)
        "Mouth_Cannon_Grey", "Mouth_Neck_Blue", "Mouth_Ball_Grey"]
        # → Mouth_Cannon_Bored/Mouth_Neck_Bored/Mouth_Ball_Bored (音声クレードル
        #   内蔵のボア加工版, hardware/src/make_audio.py) に差し替え
        # 注: Cabin_Eye_White/Cabin_Front_Blue は 2026-07-28 設計変更 (カメラを
        # ポッドのメインアイから頭部中央目へ移設) で無加工の元パーツへ戻った
        # ため SKIP から除外 — 通常の意匠シェルとして下の model/*.stl 走査で
        # 自動的に計上される

COLOR_PAT = [("Blue", "青"), ("Grey", "グレー"), ("Black", "黒"),
             ("White", "白"), ("Red", "赤")]


def color_of(name: str) -> str:
    for pat, _ in COLOR_PAT:
        if f"_{pat}" in name:
            return pat
    return "Grey"  # 色名なし (ペグ類) はグレー扱い


# 元キット片側パーツを両腕鏡映で共通使用するもの (count_of の x2 相当を
# 別途乗算する。2026-07-29 固定爪化: 爪ハブ+指+指先チップは無加工のキット
# STL を両腕で共通使用。旧コードは FingerTip をここに入れておらず ×3 の
# まま (実際に必要な ×6 の半分) で過小計上していた — ここで併せて是正)
DOUBLE_SIDED = {"Arm_Left_Claw_Grey", "Arm_Left_Finger_Black_x3",
                "Arm_Left_FingerTip_Grey_x3"}


def count_of(name: str) -> int:
    m = re.search(r"_x(\d+)$", name)
    n = int(m.group(1)) if m else 1
    return n * 2 if name in DOUBLE_SIDED else n


def printed_cm3(mesh, scale: float, wall_mm: float, infill: float) -> float:
    """表面積×壁厚 + 内部×インフィルの物理モデル (cm3)。"""
    v_solid = abs(mesh.volume) * scale**3 / 1000.0
    a = mesh.area * scale**2 / 100.0                     # cm2
    v_wall = min(v_solid, a * wall_mm / 10.0)
    return v_wall + max(0.0, v_solid - v_wall) * infill


def main():
    shell_g = {c: 0.0 for c, _ in COLOR_PAT}
    rows = []
    for p in sorted(MODEL.glob("*.stl")):
        name = p.stem
        if name in SKIP:
            continue
        mesh = trimesh.load(p)
        n = count_of(name)
        col = color_of(name)
        vol = printed_cm3(mesh, SCALE, wall_mm=1.4, infill=0.08) * n
        g = vol * RHO["PLA"]
        shell_g[col] += g
        rows.append((name, n, col, vol, g))

    print("== 意匠シェル (PLA 150%, 壁2/インフィル8%) ==")
    for name, n, col, vol, g in sorted(rows, key=lambda r: -r[4])[:12]:
        print(f"  {name:42s} x{n:<2d} {col:5s} {vol:7.1f}cm3 -> {g:6.0f}g")
    print("  ... (他省略)")

    # 新規設計パーツ: (材料, 個数, 壁厚mm, インフィル)
    new_parts = {
        # 脚 4 本分 (v3: FR/RL はミラー版 _m だが体積は同一なので std 名で計上)
        "chassis": ("PETG", 1, 2.4, 0.25), "coxa_bracket": ("PETG", 4, 2.4, 0.40),
        "femur_link": ("PETG", 4, 2.4, 0.40), "tibia_link": ("PETG", 4, 2.4, 0.40),
        "shin_shell": ("PLA-Blue", 4, 1.4, 0.08), "thigh_cap": ("PLA-Grey", 4, 1.4, 0.08),
        # 足 (2026-07-28 Leg_Foot 化): leg_foot_bored は元キット Leg_Foot の
        # 意匠加工版 (壁厚は骨格寄りに壁3相当=2.0mm相当を意図し 20% infill、
        # tibia 差込プラグ/隠しパッドポケットの強度確保)。foot_pad は隠し
        # TPU 接地パッド (完全内蔵)
        "leg_foot_bored": ("PLA-Grey", 4, 1.4, 0.20),
        "foot_pad": ("TPU", 4, 1.8, 0.30),
        # v3 追加: ポッド接続梁 + バッテリークレードル
        "pod_neck": ("PETG", 1, 2.4, 0.40),
        "battery_cradle": ("PETG", 1, 2.4, 0.20),
        # 腕 (右+左ミラーで各2。grip 系は左右共通)
        "shoulder_bracket": ("PETG", 2, 2.4, 0.40),
        # 腕シェル (元 Arm ポッド/Elbow 球の加工版, arm_shell.py)
        "arm_pod_upper": ("PLA-Blue", 2, 1.4, 0.08),
        "arm_pod_lower": ("PLA-Blue", 2, 1.4, 0.08),
        "elbow_shell": ("PLA-Grey", 2, 1.4, 0.15),
        "Head_Top_Eyecut": ("PLA-Blue", 1, 1.4, 0.08),
        "Head_Bottom_Armcut": ("PLA-Blue", 1, 1.4, 0.08),
        # マウス砲 音声クレードル (make_audio.py): Cannon/Neck/Ball は元パーツの
        # 意匠加工版 (壁2/インフィル8%は元シェル同様)。audio_cradle は完全内蔵の
        # 隠しパーツなので骨格と同じ PETG/壁4/40%
        "Mouth_Cannon_Bored": ("PLA-Grey", 1, 1.4, 0.08),
        "Mouth_Neck_Bored": ("PLA-Blue", 1, 1.4, 0.08),
        "Mouth_Ball_Bored": ("PLA-Grey", 1, 1.4, 0.08),
        "audio_cradle_mic": ("PETG", 1, 2.4, 0.40),
        "audio_cradle_spk": ("PETG", 1, 2.4, 0.40),
        # 頭部中央目カメラ化 (2026-07-28, make_camera.py): eye_pod_camera は
        # 元パーツの意匠加工版 (瞳ボア以外は Head_Eye_White のまま) — 白は
        # LED バックライト用途を引き継がないが同じ壁厚/インフィルで計上。
        # camera_carrier は完全内蔵の隠しパーツなので骨格と同じ PETG/壁4/40%
        "eye_pod_camera": ("PLA-White", 1, 1.4, 0.08),
        "camera_carrier": ("PETG", 1, 2.4, 0.40),
        "upper_arm": ("PETG", 2, 2.4, 0.40), "forearm": ("PETG", 2, 2.4, 0.40),
        # 2026-07-29 固定爪化: palm_base/grip_slider/grip_finger 廃止 →
        # claw_mount (平坦円盤アダプタ, forearm と同じ PETG 骨格材)。
        # 爪ハブ/指/指先チップは無加工のキット STL (model/*.stl の自動走査
        # + DOUBLE_SIDED 乗算) で計上済み — ここには含めない
        "claw_mount": ("PETG", 2, 2.4, 0.40),
        # 目 (可動眼球 ×2 = 左右, 元キット Head_Eye_White 形状。白は LED
        # バックライトが透けるよう低インフィル。中央目は上記 eye_pod_camera)
        "eye_pod": ("PLA-White", 2, 1.4, 0.08),
        "eye_carrier": ("PETG", 2, 2.4, 0.40),
    }
    petg = tpu = 0.0
    print("\n== 新規設計 (hardware/stl) ==")
    for stem, (mat, n, wall, fill) in new_parts.items():
        mesh = trimesh.load(STL / f"{stem}.stl")
        rho = RHO["TPU" if mat == "TPU" else ("PETG" if mat == "PETG" else "PLA")]
        vol = printed_cm3(mesh, 1.0, wall, fill) * n
        g = vol * rho
        print(f"  {stem:20s} x{n} {mat:9s} {vol:7.1f}cm3 -> {g:6.0f}g")
        if mat == "PETG":
            petg += g
        elif mat == "TPU":
            tpu += g
        elif mat == "PLA-Blue":
            shell_g["Blue"] += g
        elif mat == "PLA-White":
            shell_g["White"] += g
        else:
            shell_g["Grey"] += g

    print("\n== 色別合計 (印刷失敗・パージ・試作分は含まず) ==")
    for col, jp in COLOR_PAT:
        print(f"  PLA {jp:4s}: {shell_g[col]:6.0f} g")
    print(f"  PETG    : {petg:6.0f} g")
    print(f"  TPU     : {tpu:6.0f} g")


if __name__ == "__main__":
    main()
