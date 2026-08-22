"""砲身内オーディオクレードル (2026-07 追加)。

元 3MF の Mouth 一式 (Ball/Neck/Cannon) を 150% 化したうえで内部だけを加工し、
INMP441 マイク + φ20mm スピーカーを Mouth_Cannon に完全内蔵する。見えるジオメトリ
(外側の意匠・側面グリップスリット・砲口フレアの外形) は元パーツのまま — 変更は
すべて「隠れる内部」に限定する (鉄則1)。

生成物:
  Mouth_Cannon_Bored — 内部ボーリング済み Mouth_Cannon_Grey。奥 (Neck 側) に
      マイクポケット、砲口側にスピーカーポケット。スピーカーポケットは砲口の
      既存の小穴+肉厚フレアをそのまま拡径したもの = 音の出口 (外観は「砲口」の
      まま、単に開口が大きくなる)。マイクポートは砲身下面 (局所 -Z, スリット
      と干渉しない安全面) に φ1.8 で開ける
  Mouth_Neck_Bored / Mouth_Ball_Bored — 中心軸 (Y) に φ6 配線ボアを貫通させた
      もの。両パーツとも元は無垢〜浅い盲穴のみで配線が通せなかった
  audio_cradle_mic — マイク基板の保持パーツ (完全内蔵の隠れ形状 = 鉄則1 の対象外)。
      φAUDIO_MIC_D ポケットへ圧入する円筒 + 中心を貫く基板トレイ + 回転キー突起
      (ポートの反対側。Cannon 側の溝と噛み合い、誤った向きでは挿入できない)
  audio_cradle_spk — スピーカーの抜け止めワッシャ。スピーカーポケット径 (φ20)
      はスピーカー外径ちょうどのため外周スリーブを巻く肉厚が無い — スピーカー
      本体は砲口側 (AUDIO_SPK_REAL_H) へ直接圧入/接着し、本品はその奥
      (AUDIO_SPK_BAFFLE_H) に押し込んでスピーカー背面のストッパーにする

配線: 各ポケットで露出させたワイヤは Mouth_Cannon の元々の中心ボア (実測で
既にほぼ全長 φ10.5-10.9mm 貫通済み, [1] 参照) をそのまま通り、Neck/Ball の
新規ボアを抜けて Head_Bottom 内部へ達する。Head_Bottom 側の受け穴は
2026-07-30 に `make_head.py` の `head_bottom_armcut()` へ焼き込み済み
(マウスソケット軸に沿った φ7mm の配線ボア。旧「本タスクの担当範囲外・
現物合わせ」から置換、根拠は `config.py` MOUTH_HEAD_BORE_* コメント参照)。

実行: cd hardware/src && ../../.venv/bin/python make_audio.py
"""
from pathlib import Path

import numpy as np
import trimesh
from manifold3d import Manifold, Mesh as MMesh

import config as C
from lib import box, cyl, cyl_y, export

MODEL = Path(__file__).resolve().parent.parent.parent / "model"


def _to_manifold(tm: trimesh.Trimesh) -> Manifold:
    return Manifold(mesh=MMesh(vert_properties=np.asarray(tm.vertices, np.float32),
                                tri_verts=np.asarray(tm.faces, np.uint32)))


def _load(name: str) -> Manifold:
    """元キット STL を 150% 化して Manifold へ (shell_mod.py と同じ流儀)。"""
    tm = trimesh.load(MODEL / f"{name}.stl")
    tm.apply_scale(C.SCALE)
    return _to_manifold(tm)


def _bore_y(y0: float, y1: float, d: float, pad_front: float = 0.0,
            pad_back: float = 0.0) -> Manifold:
    """Y=[y0,y1] の円柱ボア (負形状, ロボット/STL 共通の Y 軸)。

    pad_front/back は「メッシュの実端 (開放境界)」を確実に貫通させたいときだけ
    指定する。内部の段差境界 (例: フレアの立ち上がり) へ延長パディングしては
    いけない — その先で外径が細くなっていると外壁を突き破って本体が分断される
    (実際に発生させて発見した不具合。config.AUDIO_SPK_MARGIN 参照)。
    """
    h = (y1 - y0) + pad_front + pad_back
    yc = (y0 + y1) / 2 + (pad_front - pad_back) / 2
    return cyl_y(h, d).translate([0, yc, 0])


def mouth_cannon_bored() -> Manifold:
    """Mouth_Cannon_Grey の内部ボーリング。外側意匠は無加工。"""
    m = _load("Mouth_Cannon_Grey")

    # [奥] マイクポケット: INMP441 基板 (L14×W11×T3) を X=長辺/Z=厚み/Y=軸方向で
    # 収納。側面グリップスリット (CANNON_Y_SLOT_LO..HI) より前方、フレア段差
    # (CANNON_Y_COLLAR) より手前の丸穴区間 (外径ほぼ一定 ~19.6-19.9mm) 内に完全に
    # 収まる — 両端とも内部境界なのでパディングしない
    m -= _bore_y(C.AUDIO_MIC_Y0, C.AUDIO_MIC_Y1, C.AUDIO_MIC_D)

    # 回転キー溝: ポートの反対側 (局所 +Z) にクレードルのキー突起を受ける溝を
    # 掘る。マイクポケットの Y 範囲全長に渡す。キー無しで挿入すると干渉するので
    # 誤った向きでは物理的に挿入できない (config.AUDIO_MIC_KEY_* 参照)
    mic_len = C.AUDIO_MIC_Y1 - C.AUDIO_MIC_Y0
    key_r0 = C.AUDIO_MIC_D / 2 - 0.1   # ポケット壁と確実に重ねる (隙間防止)
    m -= box(C.AUDIO_MIC_KEY_W + 2 * C.CLEAR, mic_len,
             C.AUDIO_MIC_KEY_H + C.CLEAR + 0.1).translate(
        [0, (C.AUDIO_MIC_Y0 + C.AUDIO_MIC_Y1) / 2,
         key_r0 + (C.AUDIO_MIC_KEY_H + C.CLEAR) / 2])

    # マイクポート: 基板ポート (下面実装想定) から砲身下面 (局所 -Z, スリットの
    # 無い安全面) へ抜く。中心軸から外殻 (半径 ~9.9mm 以下) を十分越える長さ
    yc_mic = (C.AUDIO_MIC_Y0 + C.AUDIO_MIC_Y1) / 2
    m -= cyl(20.0, C.AUDIO_MIC_PORT_D).translate([0, yc_mic, -5.0])

    # [砲口側] スピーカーポケット: 既存の砲口フレア (COLLAR+マージン..TIP, 元は
    # φ7.4 の小穴+肉厚) をそのまま φ20 に拡径。TIP 側だけ実メッシュ前端を確実に
    # 貫通させるためわずかにパディング (フロント側 = 開放境界なので安全)。
    # 後端 (COLLAR側) は内部境界なのでパディングしない (AUDIO_SPK_MARGIN で
    # 既に安全側に寄せてある)
    m -= _bore_y(C.AUDIO_SPK_Y0, C.AUDIO_SPK_Y1, C.AUDIO_SPK_D, pad_front=0.6)

    return m


def mouth_neck_bored() -> Manifold:
    """Mouth_Neck_Blue に配線ボア (中心軸, Y) を貫通させる。"""
    m = _load("Mouth_Neck_Blue")
    m -= cyl_y(60.0, C.AUDIO_WIRE_BORE_D)
    return m


def mouth_ball_bored() -> Manifold:
    """Mouth_Ball_Grey に配線ボア (中心軸, Y) を貫通させる。"""
    m = _load("Mouth_Ball_Grey")
    m -= cyl_y(60.0, C.AUDIO_WIRE_BORE_D)
    return m


# ---------------------------------------------------------------- クレードル本体
def audio_cradle_mic() -> Manifold:
    """マイク基板保持パーツ。マイクポケット (φAUDIO_MIC_D) へ圧入する短い円筒 +
    中心を貫く基板トレイ。

    ローカル座標: 原点 = 円筒軸中心 (Cannon 座標の yc_mic に一致させて配置する)。
    基板は X=長辺(14)/Z=厚み(3)/Y=短辺(軸方向, 11) で、円筒の中心軸 (Z=0) を
    貫通するトレイに収める。

    肉厚に関する注意 (2026-07-28, 実物不具合の修正): トレイが円筒を軸方向全長に
    わたって横断するため、円筒の残り肉は上下キャップを繋ぐ両脇の細いリブだけに
    なる。旧版 (AUDIO_MIC_D=16, パディング+0.6/+0.4) はこのリブが実測
    0.1996〜0.38mm しかなく、0.4mmノズルの最小壁厚を割り込んで印刷不能/破断する
    不具合があった (tools/check_audio.py [5b] が検出)。AUDIO_MIC_D の拡径 +
    AUDIO_MIC_TRAY_PAD_* の詰めでリブ肉厚を ≥0.8mm まで確保している。
    """
    od = C.AUDIO_MIC_D - 2 * C.AUDIO_CRADLE_CLR
    length = C.AUDIO_MIC_LEN - 0.6   # ポケットよりわずかに短く (挿入代)
    body = cyl_y(length, od)

    # 基板トレイ: 中心軸 (Z=0) を貫くスロット (X×Y×Z = 長辺×軸方向×厚み)
    tray_w = C.AUDIO_MIC_L + C.AUDIO_MIC_TRAY_PAD_W   # 長辺方向のクリアランス込み開口
    tray_t = C.AUDIO_MIC_T + C.AUDIO_MIC_TRAY_PAD_T
    slot = box(tray_w, C.AUDIO_MIC_W + 0.6, tray_t)
    body -= slot
    # マイクポートの通し穴 (下面, Cannon 側ポートと同軸)。上端はトレイ中心
    # (Z=0, どうせトレイで空洞な高さ) に留め、+Z側 (キー突起) まで到達させない
    # — 旧版は高さ30/中心-5.0でトレイの反対側 (+Z, 上面キャップ) まで貫通して
    # おり、丸穴(XY断面)と円筒外殻(XZ断面)が浅い角度で交わる縁が上下2箇所とも
    # 局所的に極薄 (実測 <0.1mm) になる不具合があった
    port_top, port_bot = 0.0, -(od / 2 + 2.0)
    body -= cyl(port_top - port_bot, C.AUDIO_MIC_PORT_D + 0.4).translate(
        [0, 0, (port_top + port_bot) / 2])

    # 回転キー突起: ポートの反対側 (局所 +Z) に細いリブを設け、Cannon 側の溝
    # (mouth_cannon_bored 参照) に噛み合わせて正しい向きでしか挿入できなくする
    key_len = length
    body += box(C.AUDIO_MIC_KEY_W, key_len, C.AUDIO_MIC_KEY_H + 0.2).translate(
        [0, 0, od / 2 + C.AUDIO_MIC_KEY_H / 2 - 0.1])
    return body


def audio_cradle_spk() -> Manifold:
    """スピーカー抜け止めワッシャ。φ20 ポケットは径がスピーカーちょうどなので
    (=外側にスリーブを巻く肉厚が無い)、砲口側 AUDIO_SPK_REAL_H にスピーカーを
    直接ポケットへ圧入/接着し、その奥 (Neck 寄り, AUDIO_SPK_BAFFLE_H 分) へ本品
    を押し込んでスピーカー背面を受けるストッパーにする。内径は音/配線の抜けを
    確保しつつスピーカー外径より小さく絞る。
    """
    od = C.AUDIO_SPK_D - 2 * C.AUDIO_CRADLE_CLR
    h = C.AUDIO_SPK_BAFFLE_H - 0.2   # ポケットよりわずかに短く (挿入代)
    washer = cyl_y(h, od) - cyl_y(h + 2, C.AUDIO_SPK_STOP_ID)
    return washer


def build_all() -> dict:
    print("[audio cradle] (Mouth 一式の内部ボーリング + 保持パーツ)")
    parts = {
        "Mouth_Cannon_Bored": mouth_cannon_bored(),
        "Mouth_Neck_Bored": mouth_neck_bored(),
        "Mouth_Ball_Bored": mouth_ball_bored(),
        "audio_cradle_mic": audio_cradle_mic(),
        "audio_cradle_spk": audio_cradle_spk(),
    }
    return {name: export(m, name) for name, m in parts.items()}


if __name__ == "__main__":
    build_all()
