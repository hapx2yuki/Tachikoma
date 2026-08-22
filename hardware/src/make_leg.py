"""脚 1 本分の骨格パーツ生成 (3DOF, 全軸 20kg 級 STD サーボ)。

2026-07 批判的レビュー後の構成:
  - 関節は全てホーン片持ち結合 (+Y 側 6mm プレート)。アイドラーは廃止 —
    STD サーボはケース自体が箱枠の -Y 側へ突き出るため両持ちは成立しない。
    デュアルベアリング出力軸のサーボを指定することで片持ちを成立させる
    (SpotMicro 等の同クラス実績に準拠)
  - 箱枠はタブビス 4 本 (M3 セルフタップ) が全て肉に噛むよう FRAME_X1 を
    タブ穴位置から導出
  - tibia 上部は 2 方向の 45° ウェッジで膝 ±45° の全掃引を保証

座標系 (脚ローカル): +X = 脚が伸びる radial 方向, +Z = 上。
股ピッチ軸 = (COXA_LEN, *, 0) の Y 軸平行線。膝軸 = femur ローカル X=FEMUR_LEN。
サーボはいずれも +Y 側から挿入し、タブ面 = y=0、ホーンは +Y 側。
ケース底は y=-TAB_BELOW まで沈む (箱枠 -Y 面から突き出る。装飾ガードで隠す)。

足先 (2026-07-28 改訂): 非キットの TPU カスタム foot_tip を廃止し、キット
部品 Leg_Foot_Grey_x4_Repaired (甲) を土台にした leg_foot_bored + 隠し TPU
パッド foot_pad の構成へ変更 (README 鉄則1: 見えるジオメトリは元キット形状)。
Leg_Toe_Black_x12 ×3/脚は無加工のまま印刷し、leg_foot_bored の甲へ瞬間接着
する (キットどおりの組立)。tibia 側の差込ソケットは無変更 — 互換性維持。

同日 レビュー追補 (major/critical 3件対応, leg_foot_bored() 内コメント参照):
  - tibia 差込プラグの抜け止めリップを廃止 (tibia_link() 側の単純穴と
    21.68mm^3 食い込んでいた critical finding への対応)
  - leg_foot_bored() に simplify(0.01) を追加 (STL 往復で非 watertight に
    なっていた major finding への対応)
  - [2026-07-28 当時] AnkleJoint 非使用で露出する3本のタブのうち、トゥ取付に
    使われない1本 (-90°側の細いポスト) だけを短縮していたが、これは誤り
    だったため 2026-07-29 に復元した (下記追補参照)。

2026-07-29 追補 (誤短縮の復元 — 足トゥ嵌合精密化タスク):
  上記の「-90°側ポストはどのトゥにも使われない」という判定は、当時
  tools/data/kit_assembly_front.json の Leg_Toe_Black_x12 配置がまだ
  存在しない段階での目視+粗い最近接点チェックによる誤判定だった。その後
  (同日中に) 同ファイルが trimesh.section() による3本のスタブの直接実測
  (STEP 2, 各スタブを z 平面で切り離して個別に最近接点計算) を行っており、
  それによれば FRONT (-90.4°)/LEFT (+144.5°)/RIGHT (+40.4°) の3本**全て**が
  実際にトゥ取付スタブとして使われている (instances FR_0/FR_1/FR_2 が
  それぞれ対応)。本セッションで独自に trimesh.slice_plane() 由来の再測定
  (leg_foot_bored ローカル座標系, 0.1mm 分解能) を行い、この判定を再現・
  確認した — WING_TRIM cutter (r 9-30mm, 角度 -135°..-45°) は FRONT スタブの
  ほぼ全域 (base r≈9.2mm, tip r≈13.1mm, 両方とも切除範囲内) を削り取って
  おり、これは README 鉄則3 (元パーツを勝手に削らない) 違反だった。
  _trim_unused_ankle_tab() とその WING_TRIM_* 定数は削除し、キット原型の
  3スタブを完全復元した (以後、再発防止のため空ジオメトリを流用元の
  比較検証なしに削る変更は行わない)。
"""
from pathlib import Path

import numpy as np
import trimesh
from manifold3d import Manifold

import config as C
from lib import (box, cyl, cyl_y, rbox, servo_pocket, servo_tab_holes,
                 horn_pocket, export)

P = C.LEG_SERVO
MODEL = Path(__file__).resolve().parent.parent.parent / "model"

# 導出寸法 --------------------------------------------------------------
FRAME_Y = P["W"] / 2 + C.WALL                    # 箱枠半幅 (13.1)
FRAME_TOP = FRAME_Y                              # 箱枠の上下半高 (13.1)
# ホーンアーム上面 = タブ面(y=0) + ABOVE_TAB + HUB_H
HORN_TOP = P["ABOVE_TAB"] + P["HORN_HUB_H"]      # 17.5
# ホーン結合プレート内面: アームがポケット深さぶん沈む
PLATE_IN = HORN_TOP - P["HORN_T"] + C.CLEAR      # 14.9
PLATE_T = C.PLATE_T                              # 6.0
PLATE_OUT = PLATE_IN + PLATE_T                   # 20.9
# 箱枠 X 範囲: タブ穴 4 本が全て肉に噛むことを保証
_cx = P["L"] / 2 - P["SHAFT_OFF"]                # ケース中心オフセット (10.35)
_hole_hi = -_cx + P["HOLE_PITCH"] / 2            # 外側タブ穴 (14.4)
_hole_lo = -_cx - P["HOLE_PITCH"] / 2            # 内側タブ穴 (-35.1)
FRAME_X1 = _hole_hi + 3.6                        # 18.0
FRAME_X0 = _hole_lo - 4.6                        # -39.7
COXA_TOP = FRAME_TOP + 3.8                       # coxa 天板の上面 Z (16.9)
SWING_R = 43.0        # femur 中央部が coxa 箱枠 (max r=41.8) を躱す半径
DISC_R = 15.0         # ホーン結合円板の半径


def servo_frame() -> Manifold:
    """STD サーボ箱枠 (軸=Y, 原点=軸中心, タブ面 y=0)。

    ケースは -Y へ TAB_BELOW 沈み、箱枠 -Y 面 (y=-FRAME_Y) を貫通して
    突き出る。タブビスは y=0 の座面から -Y 方向へ M3 セルフタップ。
    """
    solid = box(FRAME_X1 - FRAME_X0, 2 * FRAME_Y, 2 * FRAME_TOP).translate(
        [(FRAME_X0 + FRAME_X1) / 2, 0, 0]
    )
    pocket = servo_pocket(P).rotate([-90, 0, 0])
    holes = servo_tab_holes(P).rotate([-90, 0, 0])
    return solid - pocket - holes


def coxa_bracket() -> Manifold:
    """ヨーホーン吊り下げ天板 + 股ピッチサーボ箱枠。"""
    frame = servo_frame().translate([C.COXA_LEN, 0, 0])
    # 天板: ヨー軸(原点)から箱枠まで。幅 26 (femur ウェブ |y|>=14.9 と 1.9 clear)
    plate_h = COXA_TOP - FRAME_TOP + 6
    plate = rbox(C.COXA_LEN + 42, 26.0, plate_h, r=4).translate(
        [(C.COXA_LEN + 14) / 2 - 7, 0, COXA_TOP - plate_h / 2]
    )
    m = frame + plate
    # ヨーホーン (STD) のポケット (天板上面から沈める)。アームは +X (脚方向)
    m -= horn_pocket(C.YAW_SERVO).translate([0, 0, COXA_TOP])
    return m


def _horn_negative(axis_arm_dir: str) -> Manifold:
    """関節軸原点のホーン用負形状 (ポケット + ハブ通過クリアランス)。

    axis_arm_dir: ホーンアームの向き '+x' (femur) or '-z' (tibia)
    ※必ず全ての正形状を合成した後に減算すること (ポケット埋め戻し防止)
    """
    hp = horn_pocket(P)
    if axis_arm_dir == "+x":
        hp = hp.rotate([90, 0, 0])                     # 沈み込み -Z → +Y
    else:  # '-z'
        hp = hp.rotate([90, 0, 0]).rotate([0, 90, 0])  # アーム +X → -Z
    hp = hp.translate([0, PLATE_IN, 0])
    # ハブ+ギヤヘッド上部の通過帯 (y=タブ面基準 ABOVE_TAB..PLATE_IN)
    hub_clear = cyl_y(PLATE_IN - P["ABOVE_TAB"] + 2.0,
                      P["HORN_HUB_D"] + 3.0).translate(
        [0, (P["ABOVE_TAB"] + PLATE_IN) / 2 - 1.0 + 1.0, 0])
    return hp + hub_clear


def femur_link() -> Manifold:
    """股ピッチホーン ←→ 膝サーボ箱枠。原点=股ピッチ軸。"""
    # tibia 側の膝まわり掃引を躱すウェブ終端。旧値 DISC_R+2.0 (=円板 r15 のみ
    # を想定) は不十分だった: tibia ネックプレートの角 (x±9, z-21.1, r22.9) が
    # 膝 ±45° 掃引で x'=-21.3 まで届き、旧終端 -17 のウェブと交差する。この
    # 衝突を旧設計は tibia 側 45° ウェッジで角を切って回避していたが、その
    # ウェッジがネックを z-16.5..-21 で完全切断し膝ディスクが分離していた
    # (2026-08-21 ユーザー発見)。ネックを残す代わりにウェブを 5.5mm 後退:
    # 掃引最遠 21.3mm + 1.2mm 余裕 = 22.5。ジョグブロック (x>=SWING_R=43) とは
    # web_x1 = FEMUR_LEN-22.5 = 47.5 で 4.5mm 重なり結合を維持
    web_x1 = C.FEMUR_LEN - (DISC_R + 7.5)
    # --- 正形状を全て合成
    m = cyl_y(PLATE_T, 2 * DISC_R).translate([0, PLATE_IN + PLATE_T / 2, 0])
    m += servo_frame().translate([C.FEMUR_LEN, 0, 0])  # 枠 (ポケットは最後に再減算)
    # メインウェブ: +Y 帯 (内面 14.6 = coxa 箱枠外面 13.1 と 1.5mm クリア)。
    # 箱枠への結合はジョグブロック (x>=SWING_R) が担う
    m += box(web_x1 + 10, PLATE_OUT - 14.6, 2 * FRAME_TOP).translate(
        [(web_x1 - 10) / 2, (14.6 + PLATE_OUT) / 2, 0]
    )
    # ジョグブロック: ウェブ → 箱枠の全幅結合 (coxa 掃引 SWING_R より外)
    m += box(web_x1 - SWING_R, FRAME_Y + PLATE_OUT, 2 * FRAME_TOP).translate(
        [(SWING_R + web_x1) / 2, (PLATE_OUT - FRAME_Y) / 2, 0]
    )
    # 上下ブリッジ (箱枠幅、tibia 円板の内側)
    for sz in (1, -1):
        m += box(C.FEMUR_LEN - SWING_R + 4, 2 * FRAME_Y - 0.4, 4).translate(
            [(SWING_R + C.FEMUR_LEN + 4) / 2, 0, sz * (FRAME_TOP - 2)]
        )
    # --- 負形状 (最後にまとめて)
    m -= _horn_negative("+x")
    m -= servo_pocket(P).rotate([-90, 0, 0]).translate([C.FEMUR_LEN, 0, 0])
    m -= servo_tab_holes(P).rotate([-90, 0, 0]).translate([C.FEMUR_LEN, 0, 0])
    return m


def tibia_link() -> Manifold:
    """膝ホーン ←→ 足先。原点=膝軸、脚は -Z へ。"""
    neck_z = -26.0
    beam_w, beam_t = C.LINK_W, C.LINK_T
    beam_len = C.TIBIA_LEN + neck_z
    # --- 正形状を全て合成
    m = cyl_y(PLATE_T, 2 * DISC_R).translate([0, PLATE_IN + PLATE_T / 2, 0])
    # ネックプレート: 円板と同じ +Y 帯で下へ (z -13..-49)
    m += box(beam_w, PLATE_T, 36).translate([0, PLATE_IN + PLATE_T / 2, -31])
    # 集約ブロック: +Y 帯 → 中央ビームへの遷移 (z -34..-49)
    m += rbox(beam_w, PLATE_OUT + 11, 15, r=3).translate(
        [0, (PLATE_OUT - 11) / 2, -41.5]
    )
    # メインビーム + リブ (中央 y=0)
    m += box(beam_w, beam_t, beam_len).translate([0, 0, (neck_z - C.TIBIA_LEN) / 2])
    m += box(5, beam_t + 6, beam_len).translate([0, 0, (neck_z - C.TIBIA_LEN) / 2])
    # 足先フランジ
    m += cyl(8, C.FOOT_TIP_D + 4).translate([0, 0, -C.TIBIA_LEN + 4])
    # --- 負形状 (最後にまとめて)
    m -= _horn_negative("-z")
    # 45° ウェッジ面取り ×2: 膝 ±45° 回転後の全上部構造が femur の底面
    # (z=-13.1) を躱す。関節まわりはガード円筒で保護。
    # ガード半径は 23.0 (2026-08-21 修正): 旧 16.5 はウェッジ底 (x=0 で z=-21)
    # より浅く、ネックプレートを z-16.5..-21 の帯で完全切断して膝ディスク
    # (3.36cm3) が分離した部品を黙って出力していた (ユーザーがスライサ上で
    # 発見)。r23 はネック角 (r22.9) まで保護する。ウェッジが本来除去すべき
    # だった「掃引で femur ウェブに届く角」は femur 側のウェブ後退
    # (web_x1 = FEMUR_LEN-22.5, femur_link() 参照) で回避する設計に変更。
    # 検算: check_leg_assembly.py [10] が実メッシュで膝 ±45° 密掃引の
    # femur∩tibia = 0 と tibia 単一ボディを回帰検査する
    guard = cyl_y(60, 46.0)
    wedge1 = box(300, 60, 300).rotate([0, -45, 0]).translate([-95.5, 0, 95.5])
    wedge2 = box(300, 60, 300).rotate([0, 45, 0]).translate([95.5, 0, 95.5])
    m -= (wedge1 - guard)
    m -= (wedge2 - guard)
    # leg_foot_bored 差込ソケット (旧 TPU foot_tip と同一寸法。tibia 側は
    # 無変更のため互換維持 — leg_foot_bored() の差込プラグがこの寸法へ
    # 合わせにいく)。単純な定径貫通穴 (段付きカウンターボアなし) —
    # leg_foot_bored() 側はこれに合わせて抜け止めリップを持たない設計
    # (2026-07-28 レビュー finding: リップ [D=FOOT_SOCKET_D+1.2=11.2mm] は
    # この穴 [D=FOOT_SOCKET_D+2*CLEAR=10.4mm] より大きく、PLA/PETG の
    # 剛体同士では 21.68mm^3 食い込んで物理的に挿入不能だった。旧 TPU
    # foot_tip は弾性変形でスナップできたが新設計はその前提が成立しない
    # ため、リップを廃止し接着代のみで保持する設計に単純化した。
    # check_leg_assembly.py [8] がこの穴とプラグの非干渉を回帰検査する)
    m -= cyl(C.FOOT_SOCKET_H + 1, C.FOOT_SOCKET_D + 2 * C.CLEAR).translate(
        [0, 0, -C.TIBIA_LEN + (C.FOOT_SOCKET_H + 1) / 2 - 1]
    )
    # 脛シェル固定用: ビーム途中に M3 貫通穴 ×2
    for z in (-60, -100):
        m -= cyl_y(60, 3.4).translate([0, 0, z])
    return m


def _to_manifold_mesh(tm: trimesh.Trimesh):
    from manifold3d import Mesh
    return Mesh(vert_properties=np.asarray(tm.vertices, dtype=np.float32),
                tri_verts=np.asarray(tm.faces, dtype=np.uint32))


def _load_kit_foot() -> trimesh.Trimesh:
    """model/Leg_Foot_Grey_x4_Repaired.stl を 150% 化し、leg_foot_bored の
    ローカル座標系 (原点 = tibia 差込面, +Z = tibia 内部/上, -Z = 接地側)
    へ正規化して返す。

    実測 (raw STL, trimesh.section による水平スライス走査): 甲の背側頂点
    (raw z=+3.286, bbox 上端そのもの) がなだらかなドーム状の平坦域 (半径
    ~6.5-6.9mm) をなしており、tibia 差込プラグの土台として使える。XY は
    raw で既に bbox 中心 (0,0) 付近にある (3トゥ取付スタブの根元重心は
    raw≈(0.05,2.56) とわずかに +Y へ寄るが、150% でも 3.8mm に留まり φ8-10mm
    のプラグ/パッド設計には影響しない範囲 — config.FOOT_PAD_XY 参照)。
    """
    tm = trimesh.load(MODEL / "Leg_Foot_Grey_x4_Repaired.stl")
    tm.apply_scale(C.SCALE)
    lo, hi = tm.bounds
    tm.apply_translation([-(lo[0] + hi[0]) / 2, -(lo[1] + hi[1]) / 2, -hi[2]])
    return tm


def _foot_floor_z(tm: trimesh.Trimesh, xy=None) -> float:
    """甲コラム (xy 位置) の自然底面 z (leg_foot_bored ローカル) をレイキャスト実測。"""
    if xy is None:
        xy = C.FOOT_PAD_XY
    origin = np.array([[xy[0], xy[1], 5.0]])
    direction = np.array([[0.0, 0.0, -1.0]])
    locs, *_ = tm.ray.intersects_location(origin, direction, multiple_hits=True)
    if len(locs) == 0:
        raise RuntimeError(f"leg_foot_bored: xy={xy} でレイキャストが甲メッシュに命中しない")
    return float(locs[:, 2].min())


# 2026-07-28 finding (誤り, 2026-07-29 撤回): 当時 AnkleJoint 非使用で露出する
# 3本の取付スタブのうち、-90° 側の1本だけを「どのトゥにも使われない」と
# 誤判定して短縮していた。tools/data/kit_assembly_front.json の Leg_Toe_
# Black_x12 エントリが trimesh.section() によるスタブ個別分離実測 (STEP 2)
# で3本 (FRONT -90.4°/LEFT +144.5°/RIGHT +40.4°) 全てが実際にトゥ取付スタブ
# であることを直接測定で確定しており (instances FR_0/FR_1/FR_2 が対応)、
# 本セッションでの独自再測定 (trimesh.slice_plane, leg_foot_bored ローカル
# 座標系, 0.1mm 分解能) でも同じ3本・同じ位置を再現した。当時の「どの
# トゥの最近接点にもならない」という判定は、その時点でまだ存在しなかった
# 精密なトゥ配置データより前の粗い目視ベースの誤判定だった。よって
# _trim_unused_ankle_tab() は廃止し、キット原型の3スタブを完全復元する
# (README 鉄則3: 元パーツを勝手に削らない)。


def leg_foot_bored() -> Manifold:
    """キット形状 Leg_Foot (甲) + tibia 差込プラグ + 隠し TPU パッド用ポケット。

    原点 = tibia_link() の足先 (0,0,-C.TIBIA_LEN) にそのまま配置できる
    ローカル座標系 (z=0 が甲背側頂点=tibia 差込面、-Z が接地側)。外観は
    キット原型のまま無加工 (README 鉄則1・鉄則3) — プラグ/ポケットとも
    tibia 差込面より上 (tibia 内部に隠れる) /甲コラム内部 (パッドで隠れる)
    にのみ追加し、可視ジオメトリ (3本のトゥ取付スタブ含む) は一切削らない
    (2026-07-28 に AnkleJoint 非使用の -90°スタブを誤って短縮していたが
    2026-07-29 に復元済み — 上記コメント参照)。
    """
    tm = _load_kit_foot()
    m = Manifold(mesh=_to_manifold_mesh(tm))

    # tibia 差込プラグ (旧 foot_tip と同径 — tibia_link() のソケット無変更の
    # ため互換維持): z=0 (甲背側頂点) から +Z (tibia 内部) へ。旧版はここに
    # 抜け止めリップ (D=FOOT_SOCKET_D+1.2=11.2mm) を追加していたが、
    # tibia_link() 側のソケットボアが段付きのない定径穴 (D=FOOT_SOCKET_D+
    # 2*CLEAR=10.4mm) のため実体が 21.68mm^3 食い込み挿入不能だった
    # (2026-07-28 レビュー finding, critical — 詳細は tibia_link() 内コメント
    # 参照)。剛体同士のスナップ機構として成立しないためリップは廃止し、
    # 接着代のみで保持する設計に単純化した
    plug = cyl(C.FOOT_SOCKET_H, C.FOOT_SOCKET_D).translate([0, 0, C.FOOT_SOCKET_H / 2])
    m += plug

    # 隠しパッド用ポケット (甲コラム自然底面から上へ FOOT_PAD_POCKET_H だけ掘る)
    px, py = C.FOOT_PAD_XY
    floor_z = _foot_floor_z(tm, (px, py))
    pocket_h = C.FOOT_PAD_POCKET_H + 0.5   # わずかに掘り増し (パッド圧入代)
    pocket = cyl(pocket_h, C.FOOT_PAD_D + 2 * C.CLEAR).translate(
        [px, py, floor_z + pocket_h / 2 - 0.3])
    m -= pocket

    # simplify() 必須 (make_camera.py cabin_eye_bored/cabin_front_bored と
    # 同じ理由): これが無いと輸入キットメッシュへのブーリアン加工がスリバー
    # 面を残し、STL 往復 (エクスポート→再ロード) で非 watertight になる
    # ことを実測で確認した (2026-07-28 レビュー finding, major — export()
    # 時点の watertight=True 表示は書き出し前メッシュのみを見ており、実際に
    # 書き出した hardware/stl/leg_foot_bored.stl を再ロードすると
    # is_watertight=False だった。lib.export() 側もこの再ロード判定へ
    # 修正済み — 詳細は lib.py 参照)
    return m.simplify(0.01)


def foot_pad() -> Manifold:
    """leg_foot_bored() の隠しポケットへ圧入接着する TPU 接地パッド。

    軸 (ポケット嵌合, 圧入) + フランジ (ポケット口での位置決め) + ドーム
    (接地面。甲全体の最下点=トゥ取付スタブ先端より config.FOOT_PAD_PROTRUDE
    だけ下へ突き出し、実際の耐荷重接地を担う — 3本のトゥ取付スタブは薄く
    脆いため主たる接地はここに集約する)。leg_foot_bored() と同じローカル
    座標系 (原点 = tibia 差込面)。

    2026-07-29 追記: このドーム底の局所 z 深さ (=config.FOOT_GROUND_OFFSET,
    実測18.6mm) が firmware/tools の IK が使う TIBIA_LEN_GAIT の校正元
    (config.py 参照)。トゥ (Leg_Toe_Black_x12) は取付スタブ軸に沿って
    このドームよりさらに 6.5-7.1mm 深く突き出すが (tibia 軸から20mm前後
    横に離れるため 1 次元オフセットで一緒には拾えない)、装飾扱いのまま
    据え置いている — tools/check_leg_assembly.py の接地クリアランス節参照。
    """
    tm = _load_kit_foot()
    px, py = C.FOOT_PAD_XY
    floor_z = _foot_floor_z(tm, (px, py))
    lowest_z = float(tm.bounds[0][2])   # 甲全体の最下点 (トゥ取付スタブ先端)

    # xy=(0,0) のローカル軸上で組み立て、最後に (px,py) へ平行移動する
    shaft_h = C.FOOT_PAD_POCKET_H + 0.3   # ポケットよりわずかに長く (圧入代)
    shaft = cyl(shaft_h, C.FOOT_PAD_D).translate([0, 0, floor_z + shaft_h / 2 - 0.3])
    flange = cyl(1.0, C.FOOT_PAD_D + 1.5).translate([0, 0, floor_z - 0.5])
    dome_r = C.FOOT_PAD_D / 2
    dome_bottom_z = lowest_z - C.FOOT_PAD_PROTRUDE
    dome = Manifold.sphere(dome_r).translate([0, 0, dome_bottom_z + dome_r])
    m = shaft + flange + dome
    return m.translate([px, py, 0])


def build_all() -> dict:
    print("[leg parts]")
    parts = {
        "coxa_bracket": coxa_bracket(),
        "femur_link": femur_link(),
        "tibia_link": tibia_link(),
        "leg_foot_bored": leg_foot_bored(),
        "foot_pad": foot_pad(),
    }
    return {name: export(m, name) for name, m in parts.items()}


if __name__ == "__main__":
    build_all()
