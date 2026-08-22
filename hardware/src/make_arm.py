"""腕 1 本分の骨格パーツ生成 (肩ヨー/肩ピッチ/肘 = MG90S)。

構成 (右腕。左腕は build_all がミラー出力) — v4 (2026-07-29, キット準拠
固定爪化): 可動グリッパ (palm_base/grip_slider/grip_finger + サブマイクロ)
を廃止し、肘から爪までキット原型パーツを直結する:
  chassis 前縁の MICRO ヨーサーボ (上から挿入・軸下向き, make_chassis.py)
    └ shoulder_bracket : プレート下面のヨーホーンから吊り下がる箱枠
        └ upper_arm    : ピッチホーン片持ち円板 + ビーム + 肘サーボ箱枠
          (意匠: 元 Arm ポッドのクラムシェルを被せる — arm_shell.py)
            └ forearm  : 肘ホーン円板 + 手首面。キット実測比率に合わせ
              再短縮 (24→16mm, 下記 claw_mount docstring の実測根拠)
                └ claw_mount : 前腕手首面 → 元キット Claw_Grey (爪ハブ,
                  Arm_Left 版を両腕で鏡映使用) への接着アダプタ。
                  Finger_Black×3 (差込) + FingerTip_Grey×3 (接着) は
                  無加工のキット STL をそのまま 150% 印刷して組む
                  (印刷指示は docs/printing.md — 本ファイルでは加工しない)

腕は前面下部からノーズ両脇に垂れ下がる (実機のシルエット)。

座標系 (腕ローカル): +X = 腕が伸びる方向 (ヨー0°=中立で機体放射外向き
(正面から40°, C.ARM_MOUNT_YAW_DEG。2026-07-28 実ソケット移設)), +Z = 上。
腕パーツ自体のローカル形状は移設の影響を受けない (組付け回転は
make_chassis.py の取付 XY と firmware の kinematics 側で吸収)。
関節はすべてホーン片持ち結合 (+Y 側)。脚と同じ設計文法。
"""
from pathlib import Path

from manifold3d import Manifold

import config as C
from lib import (box, cyl_x, cyl_y, rbox, servo_pocket, servo_tab_holes,
                 horn_pocket, export)

MODEL = Path(__file__).resolve().parent.parent.parent / "model"

PA = C.ARM_SERVO      # MICRO (MG90S)

# MICRO 用の導出寸法 (脚の STD と同じ導出式)
WALL = 2.5
FRAME_Y = PA["W"] / 2 + WALL                       # 8.7
FRAME_TOP = FRAME_Y
HORN_TOP = PA["ABOVE_TAB"] + PA["HORN_HUB_H"]      # 10.8
PLATE_IN = HORN_TOP - PA["HORN_T"] + C.CLEAR       # 9.0
PLATE_T = 4.0
PLATE_OUT = PLATE_IN + PLATE_T                     # 13.0
_cx = PA["L"] / 2 - PA["SHAFT_OFF"]                # 5.6
_hole_hi = -_cx + PA["HOLE_PITCH"] / 2             # 8.3
_hole_lo = -_cx - PA["HOLE_PITCH"] / 2             # -19.5
FRAME_X1 = _hole_hi + 3.4                          # 11.7
FRAME_X0 = _hole_lo - 4.0                          # -23.5
DISC_R = 11.0
SWING_R = 26.5     # 上腕可動部が肩箱枠 (max r=sqrt(23.5^2+8.7^2)=25.1) を躱す


def micro_frame() -> Manifold:
    """MICRO サーボ箱枠 (軸=Y, 原点=軸中心, タブ面 y=0)。"""
    solid = box(FRAME_X1 - FRAME_X0, 2 * FRAME_Y, 2 * FRAME_TOP).translate(
        [(FRAME_X0 + FRAME_X1) / 2, 0, 0])
    return (solid - servo_pocket(PA).rotate([-90, 0, 0])
            - servo_tab_holes(PA).rotate([-90, 0, 0]))


def _horn_negative_micro(axis_arm_dir: str) -> Manifold:
    hp = horn_pocket(PA)
    if axis_arm_dir == "+x":
        hp = hp.rotate([90, 0, 0])
    else:  # '-x' (前腕は肘から -X 側に折り返さず +X。使用は +x のみだが将来用)
        hp = hp.rotate([90, 0, 0]).rotate([0, 180, 0])
    hp = hp.translate([0, PLATE_IN, 0])
    hub_clear = cyl_y(PLATE_IN - PA["ABOVE_TAB"] + 2.0,
                      PA["HORN_HUB_D"] + 2.4).translate(
        [0, (PA["ABOVE_TAB"] + PLATE_IN) / 2, 0])
    return hp + hub_clear


def _shoulder_bracket_up() -> Manifold:
    """ヨーホーンの上に載る形の L ブラケット (旧タワー版)。

    原点 = ヨー軸。肩ピッチ軸 = (20, *, +16.4)。現行設計はこれを z 反転して
    「シャーシ下面のホーンから吊り下がる」形で使う (shoulder_bracket())。
    """
    OFF = 14.0
    plate = rbox(36, 24, 5, r=4).translate([5, 0, 2.5 + PA["HORN_HUB_H"] - 2.0])
    pz = 2.5 + PA["HORN_HUB_H"] - 2.0  # プレート中心 z
    m = plate
    # ピッチ箱枠 (軸=Y) をプレート上に
    frame = micro_frame().translate([OFF + 6, 0, pz + 2.5 + FRAME_TOP - 0.1])
    m += frame
    # ヨーホーンポケット (プレート下面から)
    m -= horn_pocket(PA).rotate([180, 0, 0]).translate([0, 0, pz - 2.5])
    # 箱枠のポケット再貫通
    m -= servo_pocket(PA).rotate([-90, 0, 0]).translate(
        [OFF + 6, 0, pz + 2.5 + FRAME_TOP - 0.1])
    m -= servo_tab_holes(PA).rotate([-90, 0, 0]).translate(
        [OFF + 6, 0, pz + 2.5 + FRAME_TOP - 0.1])
    return m


def shoulder_bracket() -> Manifold:
    """シャーシ下面のヨーホーンから吊り下がるブラケット + 肩ピッチ箱枠。

    原点 = ヨー軸。ホーンポケットは上面に開口し、肩ピッチ軸 = (20, *, -16.4)。
    ポケット・箱枠とも z 対称形状なので、上載せ版の z 反転で成立する
    (manifold の mirror は法線を正しく処理する)。
    """
    return _shoulder_bracket_up().mirror([0, 0, 1])


def upper_arm() -> Manifold:
    """肩ピッチホーン円板 + ビーム + 肘サーボ箱枠。原点=肩ピッチ軸、腕は +X。"""
    m = cyl_y(PLATE_T, 2 * DISC_R).translate([0, PLATE_IN + PLATE_T / 2, 0])
    web_x1 = C.UPPER_ARM_LEN - (DISC_R + 2.0)
    # ウェブ (+Y 帯, 肩箱枠外面 8.7 と 1.3mm クリア → 内面 10.0)
    m += box(web_x1 + 8, PLATE_OUT - 10.0, 2 * FRAME_TOP).translate(
        [(web_x1 - 8) / 2, (10.0 + PLATE_OUT) / 2, 0])
    # ジョグ + ブリッジ (肩掃引 SWING_R より外で全幅結合)
    m += box(web_x1 - SWING_R, FRAME_Y + PLATE_OUT, 2 * FRAME_TOP).translate(
        [(SWING_R + web_x1) / 2, (PLATE_OUT - FRAME_Y) / 2, 0])
    for sz in (1, -1):
        m += box(C.UPPER_ARM_LEN - SWING_R + 4, 2 * FRAME_Y - 0.4, 3).translate(
            [(SWING_R + C.UPPER_ARM_LEN + 4) / 2, 0, sz * (FRAME_TOP - 1.5)])
    # 肘箱枠
    m += micro_frame().translate([C.UPPER_ARM_LEN, 0, 0])
    # 負形状
    m -= _horn_negative_micro("+x")
    m -= servo_pocket(PA).rotate([-90, 0, 0]).translate([C.UPPER_ARM_LEN, 0, 0])
    m -= servo_tab_holes(PA).rotate([-90, 0, 0]).translate([C.UPPER_ARM_LEN, 0, 0])
    return m


def forearm() -> Manifold:
    """肘ホーン円板 + 手首の平坦端 (claw_mount を接着)。原点=肘軸。

    v4 (2026-07-29, キット準拠固定爪化): FOREARM_LEN=16 に再短縮 (旧 v3 の
    24mm はキット比率の粗い目視推定だった)。16 の根拠はメカ的下限 (肘ホーン
    円板+ネックのクリアランスで ~13-14mm 必要) であり、キットのポッド全長
    71.4mm@150% − UPPER_ARM_LEN 55mm (UPPER_ARM_LEN はキット実寸ではなく
    本設計で独立に選んだパラメータ) ≈ 16.4mm という全体プロポーション上の
    目安とも定性的に近い。QA (2026-07-29) 指摘: これは 3MF 実測の「肘ボール
    中心→爪ハブ近位面」raw55.4mm=83.1mm@150% (claw_mount() docstring 参照)
    とは別物の計算であり、両者を突き合わせて整合したものではない (肘ボール・
    爪ハブはいずれもポッド自身の全長の外側に位置するため、raw55.4mm と
    16.4mm を直接比較する意味はない — 単純な桁違いの誤読を招く記述だった)。
    ユーザーの「手が長い」への定量回答としては、この16.4mm目安と下限13-14mm
    の一致で十分であり、raw55.4mm との照合は不要かつ不成立
    中央ブロックは x≥9 に置き、肘 95° 折りでも上腕箱枠上面 (z=8.7) を
    躱す (check_arm [1] のブーリアンで検証)。手首端は M3 フランジでなく
    平坦な接着面へ変更 (claw_mount 側と同じ理由 — この長さでは M3 ボスの
    肉厚を確保できない。詳細は claw_mount() docstring)。
    """
    m = cyl_y(PLATE_T, 2 * DISC_R).translate([0, PLATE_IN + PLATE_T / 2, 0])
    # ネック (+Y プレート帯。箱枠幅 ±8.7 の外側なので折り畳みで干渉しない)
    m += box(11, PLATE_T, 20).translate([5.5, PLATE_IN + PLATE_T / 2, 0])
    # 中央ブロック → 手首端
    block_len = C.FOREARM_LEN - 9
    m += rbox(block_len, 2 * PLATE_OUT, 20, r=2.0).translate(
        [9 + block_len / 2, 0, 0])
    m += cyl_x(1.5, 20.0).translate([C.FOREARM_LEN - 0.75, 0, 0])  # 手首の平坦端
    m -= _horn_negative_micro("+x")
    return m


def claw_mount() -> Manifold:
    """前腕手首面 → 元キット Arm_Claw_Grey (爪ハブ) への接着アダプタ。

    可動グリッパ (palm_base/grip_slider/grip_finger) 廃止 (2026-07-29,
    ユーザー方針「爪は可動不要, キット元デザイン準拠の固定爪へ」) に伴う
    新設パーツ。原点 = forearm 手首端の面、+X = 指方向。

    実測根拠 [3MF source_offset フォレンジクス, tools/kit_assembly.py と
    同じ手法。2026-07-29 実施]:
      - キットには "Claw_Grey" と名の付く STL が Left/Right 両方あるが、
        **形状が全くの別物**: Arm_Right_Claw_Grey (raw extents 24.7×32.0×
        21.4mm, 体積/凸包比 0.205 = 開放骨組) は爪ハブと無関係 (用途不明,
        恐らく展示台を掴む固定ポーズ用の一体成形パーツ)。
        **Arm_Left_Claw_Grey (raw extents 12.8×8.9×13.1mm, 体積/凸包比
        0.70) が実際の爪ハブ** — 小球ハブから 3 本のペグ (先端 raw r=1.14mm,
        120°でなく実測非等間隔) が突き出す形状で、Arm_Left_Finger_Black_x3
        の根元穴 (raw r=1.14mm, ブラインドホール) と直径が厳密一致し、3MF
        の kit 座標系上のギャップは 0.0012mm (実質ゼロ = 設計上の嵌合ペア)。
        Finger/FingerTip 同様、**両腕とも Arm_Left_Claw_Grey.stl を鏡映
        使用**する (Right 版は不使用)。
      - 「近位面の大きなリング (raw r=6.04mm, fuzzy)」は当初ソケット (差込
        穴) と誤認したが、2 段階の実測で訂正した: (1) trimesh.contains() の
        中心軸プローブでハブ中心が raw x=2.5〜12mm の全域にわたり中実と判明
        (深い差込穴は無い — ハブは中実成形。小型キットパーツでは一般的)。
        (2) 続いてレイキャストで近位面の形状を r=0/2/4mm オフセットで実測
        したところ、いずれも claw_mount ローカル x が同一値 (誤差<0.01mm)
        = **ドーム状ではなく平坦面**と判明 (「大きなリング」はこの平坦面と
        外側の丸みとの境界線だった)。よって claw_mount は「深い差込ペグ」
        ではなく、**平坦な円盤面を爪ハブの平坦近位面へ突き合わせ接着**する
        方式とした (キット自体の他の小物 joint — TailJoint スリーブや
        Insert 類 — と同じ「現物合わせ + 接着」の流儀。可視ジオメトリ保護の
        鉄則により Claw_Grey 自体への穴あけ加工はしない — Bored 系は
        Mouth/Neck/Ball のみ許可されている)。
      - 実測チェーン (@150%): 肘ボール中心→爪ハブ近位面 raw55.4mm=83.1mm
        (この値は forearm() の FOREARM_LEN 導出 [ポッド全長71.4mm−
        UPPER_ARM_LEN55mm=16.4mm] とは独立の計測値であり、両者は一致しない
        — 肘ボール・爪ハブはいずれもポッド自身の全長の外側にあるため、
        突き合わせて比較する意味はない。混同しないこと [QA 2026-07-29]）。
        ハブ内部 (近位面→指ペグ平面) raw5.605mm=8.407mm。指 (ペグ→タロン
        先端) raw11.598mm=17.397mm。3本のタロン先端のうち claw_mount
        ローカル x が最大 (=worst-case, finger index 1) の値は実メッシュ
        (FINGER_TO_MOUNT[1] 変換後) で 31.689mm — config.py
        ARM_HAND_REACH_MM (=FOREARM_LEN+31.70, 実測に+0.01mm の安全マージン)
        が firmware ARM_REACH_MM と一致する前提 (旧 31.55mm は丸め誤りで
        0.14mm 過小評価だった。tools/check_arm.py [3b] が実メッシュで直接
        検証する)。CLAW_TO_MOUNT/FINGER_TO_MOUNT/FINGERTIP_TO_MOUNT
        (config.py) はこの平坦面が claw_mount 前面 (x=CLAW_MOUNT_THICKNESS)
        にちょうど乗るよう選定済みの決定的変換 (tools/check_arm.py [3] の
        レイキャスト再検証つき)。

    構造: forearm 手首端と同径 (φ20) の単純な円盤 (厚み
    CLAW_MOUNT_THICKNESS)。前面が爪ハブの平坦近位面と突き合わせ接着になる。
    接着面は現物合わせ (双方を軽く均してから瞬間接着またはエポキシ —
    常時荷重は指3本+チップの自重のみで小さいため接着で足りる)。
    """
    T = C.CLAW_MOUNT_THICKNESS
    return cyl_x(T, 20.0).translate([T / 2, 0, 0])


def build_all() -> dict:
    print("[arm parts] (右腕。*_L は build_all.py がミラー出力)")
    parts = {
        "shoulder_bracket": shoulder_bracket(),
        "upper_arm": upper_arm(),
        "forearm": forearm(),
        "claw_mount": claw_mount(),
    }
    return {name: export(m, name) for name, m in parts.items()}


if __name__ == "__main__":
    build_all()
