"""LD-220MG (タブ無し 40×20 ケース) を既存の STD 箱枠 / シャーシ開口に固定する
カップ (2026-09-05, userInput/ の写真と STL を根拠に追加)。

背景
  - 届いた脚サーボは Hiwonder LD-220MG。DS3218 と違い側面タブが無く、上下面の
    ねじ穴と底面の補助軸ボスで固定する構造。ケース外形 40.0×20.0 は実測済み。
    配線は出力軸側 (+x) の端面の、底面寄りから出る (写真 IMG_1903/1909)。
    → 出口は箱枠の外 (カップの端壁の位置) なので、箱枠は無加工でよい。
  - 既存の servo_frame() は 41.1×20.6 の貫通ポケットなので、タブ無しケースは
    そのまま通る。よって「ケース底面側から被せるカップ」で
      (a) ケースの Y 位置 (= ホーンの高さ) を底板で決め、
      (b) 横方向を側壁 (はめあい 0.2) で決め、
      (c) フランジを既存のタブ穴 (φ2.8 貫通, M3 タッピング) に 4 本で固定する。
  - 箱枠の +Y 面側は関節相手の円板内面まで 1.8mm しか無い (PLATE_IN 14.9 −
    FRAME_Y 13.1) ので、上面側には何も置けない。底面側に被せるのはそのため。
  - 断面確認で判明した既存設計の事実: タブ用スロット (y 0..9) の上に 4.1mm の
    天井が残っており、DS3218 のタブは物理的に挿入できない。LD-220MG + 本カップ
    ではスロットを使わないので影響しない。

座標 (カップ・ローカル = 「取付面」基準)
  z=0 が取付面 (脚: 箱枠の -Y 面 / シャーシ: タブボス上面)。ケース中心が原点、
  ケース長手 = X、幅 = Y。ケースは z<0 側 (取付面の向こう = ポケット内) から
  z=ZB (ケース底面) まで伸び、カップの底板は z∈[ZB, ZB+FLOOR]。
  脚用は箱枠ローカル (make_leg 規約: 軸=Y) へ回転して検証する。

出力 (hardware/stl/)
  ld220_cup_leg.stl   ×8 (股ピッチ 4 + 膝 4。ミラー脚も同一部品、裏返して使う。
                      両端のスカートが箱枠の端面にはまり、被せた時点で位置が決まる)
  ld220_cup_yaw.stl   ×4 (シャーシのヨーサーボ。上から被せて外側 2 ボスへ固定)
  既存の印刷物 (coxa_bracket / femur_link / chassis) は無加工で使う。
"""
from pathlib import Path

import numpy as np
from manifold3d import Manifold

import config as C
from lib import box, cyl, cyl_y, export
import make_leg as ML

LD = C.LD220
K = C.LD220_CUP
P = C.LEG_SERVO
FRAME_Y = ML.FRAME_Y                       # 13.1 箱枠半深さ (Y) = 半高さ (Z)
_cx = P["L"] / 2 - P["SHAFT_OFF"]          # 10.35 軸→ケース中心
HOLE_X = P["HOLE_PITCH"] / 2               # 24.75 (ケース中心基準)
HOLE_Y = P["HOLE_SPREAD"] / 2              # 5.0

# ケース上面 (出力軸側) の Y (箱枠ローカル): ホーンアーム上面 HORN_TOP を
# DS3218 と同じ高さに合わせる → 上面 = HORN_TOP - LD.HORN_TOP
CASE_TOP_Y = ML.HORN_TOP - LD["HORN_TOP"]  # 11.0 (LD.HORN_TOP=6.5 のとき)
CASE_BOT_Y = CASE_TOP_Y - LD["H"]          # -29.5
ZB_LEG = -CASE_BOT_Y - FRAME_Y             # 16.4 取付面 (箱枠 -Y 面) からケース底面まで
# シャーシ: タブ面 = ボス上面。DS3218 は上面 (出力軸側) がタブ面の 11mm 下
ZB_YAW = LD["H"] - P["ABOVE_TAB"]          # 29.5

CAV_L = LD["L"] + 2 * K["CLEAR"]           # 40.4
CAV_W = LD["W"] + 2 * K["CLEAR"]           # 20.4
OUT_L = CAV_L + 2 * K["END_WALL"]          # 44.0
OUT_W = CAV_W + 2 * K["WALL"]              # 25.4 (< 26.2 箱枠幅)
FLANGE_X = (-(-_cx - ML.FRAME_X0), ML.FRAME_X1 + _cx)   # (-29.35, 28.35) 箱枠フットプリント
FLANGE_W = 2 * FRAME_Y                     # 26.2


def _cup(zb: float, flange_x=FLANGE_X, flange_w=FLANGE_W) -> Manifold:
    """カップ本体 (取付面 z=0, ケース底面 z=zb)。"""
    fl = K["FLANGE"]
    h = zb + K["FLOOR"]
    # フランジ (箱枠フットプリント) + 壁+底板の外形
    m = box(flange_x[1] - flange_x[0], flange_w, fl).translate(
        [(flange_x[0] + flange_x[1]) / 2, 0, fl / 2])
    m += box(OUT_L, OUT_W, h).translate([0, 0, h / 2])
    # ケースの空洞 (取付面より下 = ポケット側へ貫通)
    m -= box(CAV_L, CAV_W, zb + 5).translate([0, 0, (zb - 5) / 2])
    # 補助軸ボスの逃がし (底板貫通)
    m -= cyl(K["FLOOR"] + 2, LD["REAR_BOSS_D"] + 1.0).translate([0, 0, zb + K["FLOOR"] / 2])
    # 底面ねじ穴 (実測が入っているときだけ)
    if LD.get("BOT_HOLES"):
        px, py, d = LD["BOT_HOLES"]
        for sx in (-1, 1):
            for sy in (-1, 1):
                m -= cyl(K["FLOOR"] + 2, d).translate([sx * px / 2, sy * py / 2, zb + K["FLOOR"] / 2])
    # タブ穴位置の通し穴 + ビス頭の逃がし (頭はフランジ上面 z=fl に座る)
    for sx in (-1, 1):
        for sy in (-1, 1):
            x, y = sx * HOLE_X, sy * HOLE_Y
            m -= cyl(fl + 2, K["SCREW_D"]).translate([x, y, fl / 2])
            m -= cyl(6.0, K["HEAD_D"]).translate([x, y, fl + 3.0])
    # 配線穴 (+x 端壁, 底面寄り)。コネクタごと通せる大きさ
    m -= _cable_hole(zb)
    # 箱枠の両端面にはまるスカート (取付面より箱枠側 z<0)。幅方向は ±SKIRT_HALF_W
    # に限定して裏返し (ミラー脚) でも同じ部品が使え、coxa 天板 (箱枠 z>7.1) を避ける
    for sx, xe in ((-1, flange_x[0]), (1, flange_x[1])):
        m += box(K["SKIRT_T"], 2 * K["SKIRT_HALF_W"], K["SKIRT_H"] + fl).translate(
            [xe + sx * (K["CLEAR"] + K["SKIRT_T"] / 2), 0, (fl - K["SKIRT_H"]) / 2])
    # 出力軸から遠い側 (-x) の底板×端壁の角を面取り (底板側 6mm × 端壁側 3mm)。
    # 後脚の股ピッチカップがポッド側ヨー (LIM_YAW_POD) でバッテリーパック側面に
    # 2.4mm 食い込むのを避ける (tools/check_ld220_cup.py / check_leg_assembly.py
    # クレードル検査)。面取りは空洞 (ケース角) を露出させない範囲に収める
    m -= _corner_chamfer(-OUT_L / 2, h, run_x=K["CHAMFER_X"], run_z=K["CHAMFER_Z"])
    return m


def _cable_hole(zb: float) -> Manifold:
    """+x 端壁の配線穴 (負形状)。中心は底面から WIRE_ABOVE_BOT、幅 WIRE_HOLE_W、高さ WIRE_HOLE_H。"""
    zc = zb - LD["WIRE_ABOVE_BOT"]
    return box(K["END_WALL"] + 6, K["WIRE_HOLE_W"], K["WIRE_HOLE_H"]).translate(
        [CAV_L / 2 + K["END_WALL"] / 2, 0, zc])


def _corner_chamfer(x_edge: float, z_top: float, run_x: float, run_z: float) -> Manifold:
    """垂直エッジ (x=x_edge, z=z_top, y 全幅) の外側角を落とす楔 (負形状)。

    面取り面は (x_edge, z_top - run_z) と (x_edge + run_x, z_top) を通る。
    """
    ang = np.degrees(np.arctan2(run_z, run_x))
    big = 80.0
    # 面取り面より「上」(角側) を覆う箱: 底面を原点に置いてから傾け、面の中点へ
    w = box(big, OUT_W + 4, big).translate([0, 0, big / 2]).rotate([0, -ang, 0])
    mx, mz = x_edge + run_x / 2, z_top - run_z / 2
    w = w.translate([mx, 0, mz])
    # x > x_edge + run_x 側は残す
    return w - box(big, OUT_W + 6, big).translate([x_edge + run_x + big / 2, 0, 0])


def cup_leg() -> Manifold:
    return _cup(ZB_LEG)


def cup_yaw() -> Manifold:
    """シャーシのヨーサーボ用 (上から被せる)。

    シャーシ実測で判明した制約: ヨー開口の内側 (シャーシ中央寄り) 端は
    PCA9685 の下段基板 (下面 z=9, x±12.7) と基板ボス (φ6, 上面 z=9) の真下に
    あり、DS3218 のタブ座面 (z 7..10) でさえ当たる配置になっている。そのため
    ヨー用カップは
      - 内側端の壁とフランジ (耳) を持たない U 字 (側壁 2 + 外側端壁 1)
      - 外側端の 2 本のビス (既存タブボス, M3 タッピング) で固定
      - 底板をサーボ底面のねじ穴 (LD220.BOT_HOLES [要実測]) に 4 本で固定 —
        ヨーサーボは脚を吊るので、この底板ねじが無いとサーボが開口から
        下へ抜ける。BOT_HOLES を実測してから印刷すること
    """
    zb = ZB_YAW
    fl = K["FLANGE"]
    h = zb + K["FLOOR"]
    inner_x = -CAV_L / 2                      # 内側端 (壁なし)
    outer_x = CAV_L / 2 + K["END_WALL"]
    m = box(outer_x - inner_x, OUT_W, h).translate([(inner_x + outer_x) / 2, 0, h / 2])
    # 外側端の耳 (フランジ): x 20.2..28.35, 幅 26.2
    m += box(FLANGE_X[1] - CAV_L / 2 + 2, FLANGE_W, fl).translate(
        [(CAV_L / 2 - 2 + FLANGE_X[1]) / 2, 0, fl / 2])
    m -= box(CAV_L + 10, CAV_W, zb + 5).translate([-5, 0, (zb - 5) / 2])   # 内側端は開放
    m -= cyl(K["FLOOR"] + 2, LD["REAR_BOSS_D"] + 1.0).translate([0, 0, zb + K["FLOOR"] / 2])
    if LD.get("BOT_HOLES"):
        px, py, d = LD["BOT_HOLES"]
        for sx in (-1, 1):
            for sy in (-1, 1):
                m -= cyl(K["FLOOR"] + 2, d).translate([sx * px / 2, sy * py / 2, zb + K["FLOOR"] / 2])
    for sy in (-1, 1):
        x, y = HOLE_X, sy * HOLE_Y
        m -= cyl(fl + 2, K["SCREW_D"]).translate([x, y, fl / 2])
        m -= cyl(6.0, K["HEAD_D"]).translate([x, y, fl + 3.0])
    m -= _cable_hole(zb)
    return m


def cup_leg_in_frame() -> Manifold:
    """脚用カップを箱枠ローカル (軸=Y, タブ面 y=0) へ配置したもの。"""
    # カップ z → 箱枠 -Y, カップ y → 箱枠 z。z=0 面を y=-FRAME_Y に置く
    return cup_leg().rotate([90, 0, 0]).translate([-_cx, -FRAME_Y, 0])


def ld220_case_in_frame(clear: float = 0.0) -> Manifold:
    """LD-220MG ケース (+補助軸ボス) の実体を箱枠ローカルに置く (検証用)。"""
    h = LD["H"]
    m = box(LD["L"] + 2 * clear, h, LD["W"] + 2 * clear).translate(
        [-_cx, (CASE_TOP_Y + CASE_BOT_Y) / 2, 0])
    m += cyl_y(LD["REAR_BOSS_H"], LD["REAR_BOSS_D"]).translate(
        [-_cx, CASE_BOT_Y - LD["REAR_BOSS_H"] / 2, 0])
    return m


def build_all() -> dict:
    out = {
        "ld220_cup_leg": export(cup_leg(), "ld220_cup_leg"),
        "ld220_cup_yaw": export(cup_yaw(), "ld220_cup_yaw"),
    }
    return out


if __name__ == "__main__":
    for k, v in build_all().items():
        print(f"{k:22s} bounds={np.round(v.bounds, 2).tolist()} watertight={v.is_watertight}")
    print(f"CASE_TOP_Y={CASE_TOP_Y} CASE_BOT_Y={CASE_BOT_Y} ZB_LEG={ZB_LEG} ZB_YAW={ZB_YAW}")
