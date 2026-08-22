"""腕の意匠シェル: 元キット Arm パーツを加工して骨格に被せる (v3)。

生成物 (右腕。左腕 *_L は build_all.py がミラー出力):
  arm_pod_upper / arm_pod_lower — 元 Arm_Right.stl (青ポッド) を腕座標へ配置し、
      骨格チャネルを中抜き → 上下クラムシェル 2 分割。upper_arm を挟んで接着
  elbow_shell — 元 Arm_Right_Elbow_Grey.stl の大球を半殻化し、肘サーボの
      突出ケース底に被せる化粧カバー (接着 [現物合わせ])

ポッドの向き: 平円盤キャップ端 (STL Y-) = 肩側 / 薄板テール端 (STL Y+) =
肘側。両端とも閉じた中実スカルプトなので、カット (ARM_POD_X0/X1) で開口し
クラムシェルで挟む。左右ポッドは元キットでは非対称 (右のみ肩シールド) だが
実機写真は両腕対称のため、右ポッドのミラーを左腕に使う (config 参照)。

腕座標系 (make_arm.py と同じ): 原点 = 肩ピッチ軸, +X = 腕が伸びる方向,
+Y = ホーン側, +Z 上。肘軸 = x = UPPER_ARM_LEN。
"""
from pathlib import Path

import numpy as np
import trimesh
from manifold3d import Manifold, Mesh as MMesh

import config as C
from lib import box, cyl_y, rbox, export

MODEL = Path(__file__).resolve().parent.parent.parent / "model"

# ポッド STL → 腕座標の配置: STL の長軸 Y → 腕 X。x = styY×1.5 + POD_OFF
# (ポッド全長 71.4@150% が x ≈ -10 .. 61 を覆い、カットで 11.5..59 を残す)
POD_OFF = 25.7


def _pod_manifold() -> Manifold:
    tm = trimesh.load(MODEL / "Arm_Right.stl")
    tm.apply_scale(C.SCALE)
    # STL Y → 腕 X (Z は上のまま): (x,y,z) = (styY, -styX, styZ)
    R = np.array([[0.0, 1.0, 0.0, 0.0],
                  [-1.0, 0.0, 0.0, 0.0],
                  [0.0, 0.0, 1.0, 0.0],
                  [0.0, 0.0, 0.0, 1.0]])
    tm.apply_transform(R)
    tm.apply_translation([POD_OFF, 0.0, 0.0])
    mesh = MMesh(vert_properties=np.asarray(tm.vertices, np.float32),
                 tri_verts=np.asarray(tm.faces, np.uint32))
    return Manifold(mesh)


def _skeleton_channel() -> Manifold:
    """骨格+可動掃引を飲み込む中抜きチャネル (負形状)。

    - 基本チャネル 19.5×19.5 (箱枠 17.4×17.4 + クリア) を全長
    - ジョグ/ブリッジ帯 (y -8.7..13) は +Y 側へ拡幅
    - 肘サーボの突出ケース底 (-Y 側, タブ下 19.5) の通し欠き
    - 前腕ホーン円板 (r11) の回転域を r13 円筒で逃がす
    - 肘 0..95° 折り (前腕ネック/ブロック/パーム, -Z 側へ折れる) の掃引を
      下側セクタ r27 で逃がす = 肘窩の開口
    - パーム (φ27) が肘 0° で通る出口 (x 49.. の拡幅)
    """
    ch = box(120, 19.5, 19.5).translate([35, 0, 0])
    # +Y 側拡幅 (web/ジョグ帯 y≤13.0 の逃げ)。骨格がこの幅を使うのは
    # x -8..42 (web 終端) のみなので x 8..44 に限定する — 全長に広げると
    # 肘寄りの外皮まで余計に消える。x≥49 は下のパーム出口が全断面を覆う
    ch += box(36, 4.0, 19.5).translate([26, 11.75, 0])
    # 肘サーボケース底の通し欠き (-Y 側へ ~11mm 突出する)
    ch += box(26, 24, 15).translate([49.5, -14.0, 0])
    # 前腕ホーン円板の回転域
    ch += cyl_y(60, 26.0).translate([C.UPPER_ARM_LEN, 0, 0])
    # 肘折り掃引 (下側セクタ): ネック/ブロック外接 r26 + クリア → r27
    rel = cyl_y(60, 54.0).translate([C.UPPER_ARM_LEN, 0, 0])
    rel = rel ^ box(90, 80, 40).translate([C.UPPER_ARM_LEN, 0, -17.0])  # z ≤ 3
    ch += rel
    # パーム出口 (肘 0° で φ27 が x=49.. を通過)
    ch += box(24, 29.0, 29.0).translate([61, 0, 0])
    return ch


def _pod_hollow() -> Manifold:
    m = _pod_manifold()
    m -= _skeleton_channel()
    # 前後カット (開口)
    m -= box(60, 120, 120).translate([C.ARM_POD_X0 - 30, 0, 0])
    m -= box(60, 120, 120).translate([C.ARM_POD_X1 + 30, 0, 0])
    return m


def _largest_body(m: Manifold, name: str) -> Manifold:
    """最大連結成分のみ残す。ポッドの +Y フランクのリブ (x 21..39) は真下を
    骨格ウェブが通るため接続材が残せず、チャネル中抜きで必ず孤立する
    (~108mm³)。浮遊片を STL に残すとスライサ上バラバラの 2 部品になるので
    ここで落とす (フランクに ~18×5mm の加工開口が残る — ホーン側面)。
    想定外の大きな分離 (>5%) は設計退行なので例外にする。"""
    parts = m.decompose()
    if len(parts) == 1:
        return m
    parts = sorted(parts, key=lambda p: p.volume(), reverse=True)
    dropped = sum(p.volume() for p in parts[1:])
    if dropped > 0.05 * parts[0].volume():
        raise RuntimeError(f"{name}: 浮遊片が想定外に大きい ({dropped:.0f}mm3)")
    print(f"  {name}: 孤立片 {len(parts)-1} 個 ({dropped:.0f}mm3) を除去")
    return parts[0]


def arm_pod_upper() -> Manifold:
    """クラムシェル上半分 (z ≥ 0)。"""
    m = _pod_hollow() - box(200, 200, 200).translate([35, 0, -100])
    return _largest_body(m, "arm_pod_upper").simplify(0.01)


def arm_pod_lower() -> Manifold:
    """クラムシェル下半分 (z < 0)。"""
    m = _pod_hollow() - box(200, 200, 200).translate([35, 0, 100])
    return _largest_body(m, "arm_pod_lower").simplify(0.01)


def elbow_shell() -> Manifold:
    """肘の化粧カバー: 元 Elbow_Grey の大球 (φ25.1) を半殻化。

    肘サーボの突出ケース底 (-Y 側) に被せて接着する。球のどちら側を
    使うかは現物合わせ (キットの球+コブのうち大球のみ流用)。
    """
    tm = trimesh.load(MODEL / "Arm_Right_Elbow_Grey.stl")
    parts = tm.split(only_watertight=False)
    ball = max(parts, key=lambda p: abs(p.volume))     # 大球ボディ
    ball.apply_translation(-ball.center_mass)
    ball.apply_scale(C.SCALE)
    mesh = MMesh(vert_properties=np.asarray(ball.vertices, np.float32),
                 tri_verts=np.asarray(ball.faces, np.uint32))
    m = Manifold(mesh)
    # 球半径 12.56 @150% (実測)。壁 ARM_POD_WALL を残して中抜き
    m -= Manifold.sphere(12.56 - C.ARM_POD_WALL, 96)
    m -= box(60, 60, 60).translate([0, 34.0, 0])       # 半殻カット (y>4 を除去)
    # サーボケース通し欠き。y ≥ -6 に限定 (原点貫通させると -Y キャップまで
    # 分断され半殻が 2 ピースに割れる)。ケース底は欠き底 y=-6 に当たる
    m -= box(26, 60, 14).translate([0, 24.0, 0])
    return _largest_body(m, "elbow_shell").simplify(0.01)


def build_all() -> dict:
    print("[arm shells] (元 Arm パーツ加工。右腕。*_L は build_all.py がミラー)")
    parts = {"arm_pod_upper": arm_pod_upper(),
             "arm_pod_lower": arm_pod_lower(),
             "elbow_shell": elbow_shell()}
    return {name: export(m, name) for name, m in parts.items()}


if __name__ == "__main__":
    build_all()
