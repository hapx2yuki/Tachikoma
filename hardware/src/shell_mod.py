"""意匠シェルの加工: 元モデルのメッシュを 150% に拡大し、骨格に被せられる形へ。

生成物:
  shin_shell.stl  — 脛シェル。tibia に下からスライドし M3x40 ボルト 2 本で固定
  thigh_cap.stl   — 大腿シェルの上面キャップ (femur 上面ブリッジに接着)
  foot_ring.stl   — 元の足リングに φ18.6 の座グリを開け、足先フランジへ接着

⚠ 彫刻的な曲面と骨格の位置関係は最後は現物合わせ。OFFSETS で XY 位置と
   回転を微調整し、再生成 → テスト印刷で追い込むこと。
"""
from pathlib import Path

import numpy as np
import trimesh
from manifold3d import Manifold

import config as C
from lib import box, cyl, cyl_y, rbox, export, to_trimesh

MODEL = Path(__file__).resolve().parent.parent.parent / "model"

# 現物合わせ調整用 (mm / deg)
OFFSETS = {
    "shin_xy": (0.0, 0.0),     # シェル中心 vs tibia ビーム中心
    # シェルの向き。
    #
    # 2026-07-31 (実物写真タスク, 最終確定): ユーザーが実物 (塗装済み完成品)
    # の写真2枚を提示し、「脚の側面パーツは全て外側 (放射軸から見て外側) を
    # 向いている」ことを明示。脚の意匠面 (脛シェル上部の 3 点ドットモールド +
    # 主パネルライン) がキット本来の向きで放射外向きになる角度を、raw
    # Leg_Shin_Blue_x4.stl 上で実際に幾何学的に特定した上で数値的に導出した:
    #   - 3 ドットモールド: raw 座標クラスタ 3 点 (中心 (11.87,0,40.19) と
    #     左右対称ペア (14.17,±2.14,36.48)、面法線は sharp-edge 抽出+周辺
    #     面積加重平均で実測: raw 平均法線 (0.852,-0.001,0.523) = 局所 +X
    #     方向が支配的)
    #   - 主パネルライン: raw sharp-edge 連結成分 (X∈[12.26,17.13],
    #     Y∈[-10.61,10.61], Z∈[-46.45,33.55], シェルほぼ全長にわたる) も
    #     同じ +X 面
    #   - ループ/ハンドル (下記 LOOP_RELIEF_* で切除済み) は正反対の -X 面
    #     (raw 中心 (-9.47,-0.02,34.1)、法線 (-0.782,0.002,-0.623) ≈ ドット
    #     法線の符号反転) — キット原型で "外側=ドット面/内側=ループ面" が
    #     明確に分離した設計だったことを裏付ける
    #   4脚それぞれの標準立位 IK 姿勢 (STANCE 方位直接到達式) で T_knee の
    #   実回転を合成し、raw +X 法線を局所 Z 回転 shin_rotz だけ振って
    #   world 水平面へ投影した方向と、その脚の放射方位 (mnt+yaw_d) との
    #   cos 類似度を 0-355° で 5° 刻み走査した結果、shin_rotz=0.0° で
    #   4脚 (FR/FL/RL/RR, ミラー込み) 全て cos_sim=1.000 (完全に放射外向き)
    #   になることを確認 (それ以外の角度は単調に外れる — 90°/270° 近辺は
    #   cos_sim≈-0.16、180° は cos_sim=-1.000=真逆の最悪角)。これは以前の
    #   270° 選定時に近似的に見積もっていた「ループが隠れるのは 0° 近辺」
    #   という所見とも整合する (ドットとループは正反対の面なので、
    #   ドット外向き=ループ内向きは同じ shin_rotz で同時に成立する)。
    #
    # 90°/270° は過去 (下記 2026-07-31 旧経緯参照) に「45°ペア隣接脚との
    # crouch 干渉がほぼゼロになる角度帯」として選ばれていたが、実物写真で
    # 判明した意匠面の向きとは一致しない (270° ではドット面が隣接脚側/横向き
    # になり、写真の見た目と食い違う)。ユーザー指示「装飾面は外側」を
    # 優先し、shin_rotz=0.0° へ再変更。この角度で新たに生じた 45°ペア
    # 隣接脚 crouch 干渉 (無対策で最大 8.49cm^3) は、下記 ADJ_RELIEF_BANDS
    # (放射内向き=外から見えない側だけを削る内側リリーフ) で解消した
    # (M3 ボルト座面のハード制約により 0.84cm^3 の既知残留あり、
    # BOLT_BOSS_KEEPOUT_X コメント参照)。腕 (READY 姿勢) との干渉は
    # rotz=270 のときの 10.65cm^3 から rotz=0 で 7.91cm^3 へむしろ改善した
    # (どちらの角度でも局所リリーフでは対応不可能な規模の既存不具合のまま
    # であることに変わりはなく、firmware 側の連成クランプを別途推奨する
    # 点は変更なし)。
    #
    # ---- 2026-07-31 旧経緯 (270° 選定時, 参考として保持) ----
    # 当時のユーザー指摘 (URDF ビューアのスクショ、写真提示より前) は
    # 「ループ状の突起が外側を向いてしまっている、内側へ向けてほしい」
    # だった。90→270 (+180°) で直るかと思ったが誤りで (0°/90°/180°/270°
    # 総当たりの結果、ループが真に内側を向くのは 0° 近辺のみと判明)、
    # 0° は 45°ペア隣接脚 crouch 干渉が 7-9cm^3 出るため断念し、ループ
    # 自体を直接切除 (LOOP_RELIEF_*) する方針に転換、干渉の少ない 270° を
    # 維持していた。今回の実物写真タスクでドット/パネルラインという別の
    # (かつユーザー指示に直接合致する) 意匠面が特定できたため、270° の
    # 「干渉を避けるための妥協」より「意匠面を実際に正しい向きにする」を
    # 優先し 0° へ確定した。
    "shin_rotz": 0.0,
    "thigh_cut_frac": 0.45,    # 大腿キャップ: 下から何割で水平カットするか
}

# 2026-07-31 (リリーフカット再評価タスク): ループ復元を試みたが不可能と判明
# したため LOOP_RELIEF は維持する。ユーザー指示の優先順位「(1) キット形状の
# まま残す > (2) 動作に本当に邪魔なら削る」に従い、まず「ループを削らず
# LOOP_RELIEF を外したらどうなるか」を decompose() のパーツ数で検証した:
# 現行 shin_rotz=0 の構築チェーンでは LOOP_RELIEF を外すと shin_shell() が
# 常に 2 パーツに分解される (メイン本体 ~84cm³ + ループ片 1.12cm³)。原因は
# 干渉/回転ではなく UPPER_CAVITY (クレビス+集約ブロックを飲み込む機能上
# 必須の空洞) がループの付け根をちょうど切断してしまうこと — LOOP_RELIEF・
# 回転量・他のリリーフカットの有無に関係なく発生する (UPPER_CAVITY だけを
# 単独で適用した最小構成でも再現、scratchpad 2026-07-31 実測)。分離した
# ループ片は shin_shell() 末尾の「最大連結成分のみ残す」処理でどのみち
# 捨てられるため、LOOP_RELIEF を外しても印刷物にループは現れず (無意味な
# 変更)、仮に多パーツ STL として残しても宙に浮いた別体になり接着以外の
# 固定手段がない。UPPER_CAVITY 自体はクレビス円板+集約ブロックを収める
# 機能上必須の空洞で削れないため、ループを物理的に接続したまま復元するには
# UPPER_CAVITY の形状再設計 (ブリッジ追加等) が必要になり、単純なカットの
# 撤去では対応できない。よって本タスクでは LOOP_RELIEF を変更せず維持する
# (「動作の邪魔」ではなく「そもそも印刷可能な形で復元できない」ため)。
#
# 元のループ/ハンドル意匠の直接切除 (回転非依存)。raw
# Leg_Shin_Blue_x4.stl 上でループ特徴を幾何学的に単離 (X<-5 かつ
# Z∈[25,45] の頂点クラスタ、中心 (-9.47,-0.02,34.09)mm, raw/未スケール)
# し、「shin_xy 平行移動後・shin_rotz 回転前」のローカル座標系 (=raw 座標を
# 1.5倍しシェル中心を原点へ、上端を SHIN_TOP_Z へ移動しただけの系) で
# 切除ボックスを構成する。この段階でカットすることで shin_rotz を何度に
# 変えてもループ位置への追従が保証される。マージン込みでボックス化した際に
# UPPER_CAVITY と同じ帯 (Z 上部) の一部と重なるが独立カットとして扱っても
# 無害 (どちらも除去方向)。切除後 shin_shell() は decompose() で最大の
# 連結成分のみを残す (ループが完全に分離した破片になる回転角でも安全)。
LOOP_RELIEF_XY = ((-28.44 - 7.5) / 2, 0.0)         # 中心 X, Y
LOOP_RELIEF_HALF = ((28.44 - 7.5) / 2 + 1.0, 21.65 + 1.0)  # 半幅 X, Y (+1mm マージン)
LOOP_RELIEF_Z = (-48.15 - 18.87) / 2               # 中心 Z
LOOP_RELIEF_HALF_Z = (48.15 - 18.87) / 2 + 1.0     # 半高さ Z (+1mm マージン)

# tibia ローカル座標系 (膝軸=原点, 下向き -Z) でのシェル配置
# STD サーボ化 (2026-07) でクレビス幅 44mm → 空洞を拡大済み
SHIN_TOP_Z = -16.0             # シェル上端 (クレビス円板 r15 の直下)
SHIN_BOTTOM_Z = -132.0         # シェル下端トリム (足先フランジを囲む位置)
UPPER_CAVITY = (36.0, 46.0)    # クレビス+集約ブロックを飲み込む空洞 (X, Y)
UPPER_CAVITY_Z = -51.0         # 空洞の下端 (集約ブロック下端 -49 + 余裕)
LOWER_CHANNEL = (21.0, 19.0)   # ビーム+リブ+フランジ通過チャネル
BOLT_ZS = (-60.0, -100.0)      # tibia 側 M3 穴と一致 (make_leg.py)

# 2026-07-31 (リリーフカット再評価タスク): KNEE_RELIEF / TIP_RELIEF /
# ADJ_RELIEF_BANDS+BOLT_BOSS_KEEPOUT_X を撤去し、キット形状 (機能上必須の
# UPPER_CAVITY/LOWER_CHANNEL/BOLT_ZS 以外は無加工) へ復元した。
#
# 判定基準は「firmware 到達可能集合内で干渉するか」(タスク#30 で確立した
# pk_reachable() の不動点条件と同じ基準を、tibia-tibia だけでなく shin-shin/
# femur-shin にも統一適用)。各カットを実際に外した shin_shell() 変種を
# 生成し、対応する干渉テストを到達可能集合に限定して再実測した結果:
#
#   - KNEE_RELIEF (旧: pitch>=35°,knee>=35°で femur_link と干渉, 0.22-0.26cm^3
#     @ rotz=270 時に発見): 現行 shin_rotz=0 の形状で pitch∈[-45,55]°×
#     knee∈[-44,44]° の全域 (到達可否を問わず) を 41×45 の細密グリッドで
#     femur_link/coxa_bracket 双方・両ミラーを実ブーリアンで走査した結果、
#     worst=0.0000cm^3 (完全に非干渉)。旧干渉は shin_rotz=270 時の形状に
#     固有のものであり、270→0 の再変更 (本ファイル OFFSETS 参照) で幾何が
#     動いた結果、干渉自体が現行形状には存在しない (到達可能性以前の話)。
#   - TIP_RELIEF (旧: FR-FL/RL-RR 遠ペア フルヨー±40°+crouch(45,30) で
#     shin_shell 下端外縁同士が 0.0103cm^3 接触): 同条件を現行形状で
#     再実測すると worst=0.0000cm^3。pitch∈[20,55]°×knee∈[10,44]° の
#     細密グリッドまで拡げても残留は最大 0.000031cm^3 (0.031mm^3、メッシュ
#     の数値誤差レベル) で、しかもその極値点 (pitch=55,knee=40) は
#     pk_reachable()=False (到達不能)。
#   - ADJ_RELIEF_BANDS+BOLT_BOSS_KEEPOUT_X (旧: 45°ペア crouch(45,30) で
#     無対策 8.49cm^3、BOLT_BOSS_KEEPOUT_X 込みでも既知残留 0.84cm^3):
#     tools/check_shin_arm_leg.py の ik_poses を pk_reachable() で到達可能
#     集合へ絞った上で pairs45 の4通り全組合せを再実測すると、このカットを
#     入れても外しても worst=0.0000cm^3 (到達可能集合内では最初から無干渉
#     — 8.49cm^3/0.84cm^3 はいずれも (pitch,knee)=(45,30) という
#     gait.h D_KNEE_MIN/MAX 射影の不動点条件違反 = firmware が歩容コマンド
#     として絶対に出力しない姿勢の組合せでのみ発生していた、タスク#30の
#     tibia-tibia の結論と完全に同型)。BOLT_BOSS_KEEPOUT_X はこのカットの
#     副作用回避策だったため、カット自体の撤去で不要化し M3 ボルト座面は
#     元キット形状のフル厚みに復元される (check_screw_bosses.py で再検証)。
#
# 撤去後の [A]自脚全域細密グリッド・実歩容384姿勢、[B]隣接45°ペア到達可能
# 集合・遠ペア到達可能集合、[C]腕クランプ全域グリッド、いずれも worst
# 0.0000cm^3 (腕クランプのみ既知残留 0.0440cm^3 <ceiling 0.08cm^3、これは
# 今回のカット撤去と無関係な既存の [C] 既知残留で変化なし)。シャーシ/
# pod_neck は股ヨー軸→膝軸→シェルの運動学的位置関係上 (chassis/pod_neck は
# hip 取付点より上、shin は knee より下) 到達可能域はおろか物理可動域全体
# でも接触し得ないため個別のブーリアン走査は行っていない (hip 取付
# z=BODY_H に対し chassis は z=BODY_H+HIP_DROP 以上、shin は同一姿勢で
# z<hip が常に成立、詳細は check_shin_arm_leg.py 由来の再評価スクリプト
# 実行ログ参照)。
#
# 詳細な数値・グリッド設定・検証スクリプトは本タスクの作業ログ (scratchpad
# reeval_relief.py 系) 参照。tools/check_shin_arm_leg.py [B] は本撤去に
# 合わせて shin-shin 判定も tibia-tibia と同じ到達可能集合基準へ統一し、
# KNOWN_BOLT_BOSS_CEILING の特例ロジックを削除した (同ファイル参照)。


def _to_manifold_mesh(tm: trimesh.Trimesh):
    from manifold3d import Mesh
    return Mesh(vert_properties=np.asarray(tm.vertices, dtype=np.float32),
                tri_verts=np.asarray(tm.faces, dtype=np.uint32))


def shin_shell() -> Manifold:
    tm = trimesh.load(MODEL / "Leg_Shin_Blue_x4.stl")
    tm.apply_scale(C.SCALE)
    lo, hi = tm.bounds
    cx, cy = (lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2
    m = Manifold(mesh=_to_manifold_mesh(tm))
    # tibia ローカルへ: XY 中心を軸に、上端を SHIN_TOP_Z へ
    ox, oy = OFFSETS["shin_xy"]
    m = m.translate([-cx + ox, -cy + oy, -hi[2] + SHIN_TOP_Z])
    # ループ/ハンドル意匠の切除 (shin_rotz より前 = raw メッシュに対して
    # 固定した位置で切る。回転を何度にしてもループが追従して消える)
    m -= box(
        2 * LOOP_RELIEF_HALF[0], 2 * LOOP_RELIEF_HALF[1], 2 * LOOP_RELIEF_HALF_Z
    ).translate([LOOP_RELIEF_XY[0], LOOP_RELIEF_XY[1], LOOP_RELIEF_Z])
    if OFFSETS["shin_rotz"]:
        m = m.rotate([0, 0, OFFSETS["shin_rotz"]])

    # 上段空洞 (シェル上端の上から UPPER_CAVITY_Z まで)
    h_up = (SHIN_TOP_Z + 10) - UPPER_CAVITY_Z
    m -= rbox(UPPER_CAVITY[0], UPPER_CAVITY[1], h_up, r=6).translate(
        [0, 0, UPPER_CAVITY_Z + h_up / 2]
    )
    # 下段チャネル (全長貫通)
    h_lo = UPPER_CAVITY_Z - (SHIN_BOTTOM_Z - 10) + 4
    m -= rbox(LOWER_CHANNEL[0], LOWER_CHANNEL[1], h_lo, r=5).translate(
        [0, 0, (UPPER_CAVITY_Z + 2 + SHIN_BOTTOM_Z - 10) / 2]
    )
    # 2026-07-31 (リリーフカット再評価タスク): 膝逃がし/下端逃がし/隣接脚
    # 45°ペア内側逃がしの3カットはここに撤去済み (上記 KNEE_RELIEF/
    # TIP_RELIEF/ADJ_RELIEF_BANDS 系コメント参照 — 到達可能集合内で干渉
    # ゼロと実測確認済みのためキット形状のまま残す)。
    # 下端トリム
    m -= box(300, 300, 60).translate([0, 0, SHIN_BOTTOM_Z - 30])
    # 上端も念のため水平化 (クレビス回転域に入らないように)
    m -= box(300, 300, 60).translate([0, 0, SHIN_TOP_Z + 30])
    # M3 ボルト用の縦長穴 (Y 貫通, 高さ ±4 の調整代)
    for z in BOLT_ZS:
        slot = cyl_y(120, 4.0)
        slot = slot + slot.translate([0, 0, 4]) + slot.translate([0, 0, -4])
        slot += box(4.0, 120, 8).translate([0, 0, 0])
        m -= slot.translate([0, 0, z])
    # 安全策: 万一どこかのカットでシェルが分離してしまっても (例: 将来
    # shin_rotz を変更した際に LOOP_RELIEF が UPPER_CAVITY と結託して意匠を
    # 完全に切り離すケースが起こり得る、2026-07-31 rotz=0 での検証時に実際に
    # 発生を確認済み)、印刷用の最終出力は最大の連結成分のみを残す
    parts = m.decompose()
    if len(parts) > 1:
        m = max(parts, key=lambda p: p.volume())
    return m


def thigh_cap() -> Manifold:
    tm = trimesh.load(MODEL / "Leg_Thigh_Grey_x4.stl")
    tm.apply_scale(C.SCALE)
    lo, hi = tm.bounds
    m = Manifold(mesh=_to_manifold_mesh(tm))
    cut_z = lo[2] + (hi[2] - lo[2]) * OFFSETS["thigh_cut_frac"]
    m -= box(300, 300, 300).translate([0, 0, cut_z - 150])
    # 中心を原点へ、カット面を z=0 に
    m = m.translate([-(lo[0] + hi[0]) / 2, -(lo[1] + hi[1]) / 2, -cut_z])
    return m


# NOTE: foot_ring (元 Leg_Foot への座グリ) は試作の結果、元パーツが小さすぎて
# 崩壊するため廃止。つま先 (Leg_Toe_Black) は 150% で印刷し、TPU チップの
# フランジ側面へ直接接着する (docs/assembly.md 参照)。


if __name__ == "__main__":
    print("[shells]")
    s = shin_shell()
    # 印刷向き: 上端(平面)を下にして z=0 から立てて出力
    export(s.rotate([180, 0, 0]).translate([0, 0, SHIN_TOP_Z]), "shin_shell")
    export(thigh_cap(), "thigh_cap")
