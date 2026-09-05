"""キット意匠パーツの配置ローダ (make_visuals.py のフルドレス描画モード用)。

tools/data/kit_assembly_front.json (頭/砲身/脚ガード/腕ガード) と
kit_assembly_rear.json (ポッド外装+トリム) を読み、両ファイル共通の規約
(front JSON meta.frame_robot_z0 / meta.frame_link, rear JSON
meta.frame_definition) に従って正規化した Placement のフラットなリストを
返す。座標系・単位は両 JSON と同じ:

  frame="robot": ロボット座標 (chassis STL と同じ x,y。z はプレート下面
      z0=0 基準の相対値 — 実際の world/body z は呼び出し側で
      + (body_h + config.HIP_DROP) すること)。
  frame="link:X": X 自身のローカル座標系での配置。X が意匠シェルの土台
      (Head_Bottom_Blue / Head_Top_Blue / Mouth_Cannon_Grey) の場合は
      「X の bbox 中心化×1.5 済みローカル座標系」(tools/make_visuals.py
      shell_ghosts() の R,t を適用する直前のフレーム)。X が機構パーツ
      (thigh_cap / shin_shell / arm_pod / claw_mount / leg_foot_bored) の場合は
      hardware/src/*.py の対応する関数が定義するローカル座標系 —
      いずれも呼び出し側 (make_visuals.py) が既に確立している「X 自身を
      ロボット座標へ置く変換」をこの Placement のメッシュに追加で適用
      すればよい (X の回転を二重適用しないこと)。

各 Placement のメッシュそのものは持たない (I/O 遅延) — oriented_mesh() が
都度 STL をロードし、上記ローカル座標系まで変換したメッシュを返す。
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import trimesh

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hardware/src"))
import config as C
DATA = ROOT / "tools" / "data"
MODEL = ROOT / "model"
STL = ROOT / "hardware" / "stl"

SCALE = C.SCALE

# model/*.stl ではなく hardware/stl/*.stl (加工済み出力) から読む必要がある
# パーツ。tools/make_head_eyecut.py が示す通り、これらは既に
# 「bbox 中心化 (raw が既に原点付近) → ×SCALE」まで適用済みの状態で
# エクスポートされている (二重スケールを避けるため oriented_mesh() は
# ここに載る名前だけ正規化ステップを飛ばす)
PRESCALED = {"Head_Top_Eyecut", "Head_Bottom_Armcut"}

# 描画時にキット原型 STL でなく加工済み派生 STL (hardware/stl/) へ差し替える
# パーツ (JSON のフォレンジクス上の部品名はキット原型のまま維持し、描画層
# だけ差し替える)。2026-07-28 設計変更でカメラはポッドのメインアイから
# 頭部の中央目へ移設され、Cabin_Eye_White は元パーツのまま (無加工) に
# 戻ったため、しばらく override が必要なパーツは無かった。中央目
# (Head_Eye_White の 3 インスタンス中の 1 つ) は tools/make_visuals.py 側が
# 個別に eye_pod/eye_pod_camera を出し分けて描画するため、ここでの一括
# 差し替えは不要 — 二重描画を避ける仕組みは make_visuals.py のコメント
# (「kit_assembly が返す Head_Eye_White_x3 の配置は使わない」) を参照。
#
# Head_Bottom_Blue → Head_Bottom_Armcut (2026-07-30 追加): 肩ヨー可動域
# 全域で shoulder_bracket と実体干渉することが実メッシュ検証で判明し
# (hardware/src/make_head.py 参照)、腕ソケットを拡口した加工版へ差し替え。
# JSON 側の子パーツ (Insert 等, frame="link:Head_Bottom_Blue") はキット
# 原型の座標系のまま参照でき、ここでの override はベースシェル本体の描画に
# のみ影響する。**注意 (2026-08-20)**: 機構逃がしカット (make_head.py) で
# Armcut 自身の bbox は非対称になった (座標系=キット中心化フレームは不変)。
# 以後この STL を bbox 再中心化して置くと z+5.1mm ずれる — PRESCALED の
# 「正規化スキップ」を必ず経由すること (check_arm.py で実際に起きかけた)。
STL_RENDER_OVERRIDE: dict[str, str] = {"Head_Bottom_Blue": "Head_Bottom_Armcut"}

# make_audio は原型座標を保ってSCALEだけ掛ける。加工後のbboxで中心を
# 取り直すと、非対称な球面座や開口によって組立位置がずれるため原型の中心を使う。
AUDIO_RENDER_OVERRIDE = {
    "Mouth_Cannon_Grey": "Mouth_Cannon_Bored",
    "Mouth_Neck_Blue": "Mouth_Neck_Bored",
    "Mouth_Ball_Grey": "Mouth_Ball_Bored",
}

# rear JSON の "outward_normal" 整列は既定でパーツの局所 +Z を法線へ向ける
# (_iter_rear 参照)。これは長さ方向が Z のパーツにのみ正しい。2026-07-29
# 監査で判明: RedLight_Large/Small・Spinnarette は raw STL の断面が X=Z
# (円形キャップ面) で軸方向は Y (RedLight は等断面の円柱、Spinnarette は
# -Y 側が太い根元・+Y 側が細い先端のテーパー形状 — tools/section() 実測、
# 2026-07-29 rear turret/decor audit)。既定の (+Z→法線) をそのまま適用す
# ると、この 2 種は実軸 (Y) が法線に対してほぼ直交したまま配置され「面に
# 寝そべる」向きになる (ユーザー指摘のタレット 90°誤りと同種のバグ、ただし
# 症状は逆: タレットは軸Zで正しいはずが実は軸が斜めでほぼ垂直に誤刺さり、
# こちらは軸Zでは誤りで実軸Yを使うべきところ縦のまま=面に寝る)。
# LOCAL_AXIS_OVERRIDE[part] にその軸 (raw STL ローカル, 単位ベクトル) を
# 登録すると、_iter_rear は既定の [0,0,±1] の代わりにこれを src_axis に使う
# (align_vectors(src_axis, outward_normal) — 符号は「outward_normal へ直接
# 写像すべき局所方向」= パーツ自身の可視/突出側)。
#
# Cabin_Eye_White: raw STL 自身の Z 規約が逆で「外向きの先端 (可視面) が
# Z 負側、Cabin_Front 台座へ接着する広い縁が Z 正側」(2026-07-28 監査)。
# 既定の (+Z→法線) だと広い接着縁が外向きになり「白いドーム」ではなく扁平な
# 縁だけが見える不具合の原因だった。局所 -Z を法線へ向ける。
#
# Cabin_RedLight_Large / Cabin_RedLight_Small: 等断面の円柱 (前後対称 —
# 実測で全長にわたり断面積・外形が完全に一定, sign は見た目に影響しない)。
# 軸 = ローカル Y。
#
# Cabin_Spinnarette_Grey: -Y (根元, 断面積 11.4mm²) → +Y (先端, 断面積
# 4.6mm²) のテーパー実測 (2026-07-29)。先端 (+Y) を外向きにするため軸は
# +Y のまま (align_vectors の直接写像規約 = 「この局所方向が外を向く」)。
LOCAL_AXIS_OVERRIDE: dict[str, tuple[float, float, float]] = {
    "Cabin_Eye_White": (0.0, 0.0, -1.0),
    "Cabin_RedLight_Large": (0.0, 1.0, 0.0),
    "Cabin_RedLight_Small": (0.0, 1.0, 0.0),
    "Cabin_Spinnarette_Grey": (0.0, 1.0, 0.0),
}
# 旧名の別名 (後方互換; 挙動は同じ)
NORMAL_FLIP_PARTS = {"Cabin_Eye_White"}

# JSON の source_stl / name が model/*.stl の実ファイル名と食い違うケース
# (rear JSON は一部、印刷数サフィックス _x2/_x3/_x4 を省いた名前で
# source_stl を記録している)
STL_ALIAS = {
    "Cabin_Turret_Peg": "Cabin_Turret_Peg_x2",
    "Cabin_RedLight_Large": "Cabin_RedLight_Large_Red_x4",
    "Cabin_RedLight_Small": "Cabin_RedLight_Small_Red_x4",
    "Cabin_Spinnarette_Grey": "Cabin_Spinnarette_Grey_x4",
    "Cabin_Front_Insert_Back_Black": "Cabin_Front_Insert_Back_Black_x2",
    "Cabin_Front_Insert_Bottom_Long_Black": "Cabin_Front_Insert_Bottom_Long_Black_x2",
}

# キット配色 (thumbnail_middle.png の実色に合わせる。パーツ名のカラー
# トークンから自動判定 — 青 Cabin/Head_Top/Head_Bottom/Neck/Thigh_Guard/
# TailJoint、グレー Dome/Plate/Cannon/Cap/Guard類/Spinnarette/Turret、
# 白 Eye、黒 Toe/Insert/Finger、赤 RedLight)
COLOR_HEX = {
    "Blue": "#2d55b8", "Grey": "#9aa4b0", "Gray": "#9aa4b0",
    "White": "#f4f3f0", "Black": "#23262b", "Red": "#cc2222",
}
_DEFAULT_COLOR = COLOR_HEX["Grey"]

# 加工済み派生パーツ (ファイル名自体にカラートークンが無い) の配色は
# 由来元キットパーツの配色を継承する
COLOR_NAME_ALIAS = {"Head_Top_Eyecut": "Head_Top_Blue",
                     "Head_Bottom_Armcut": "Head_Bottom_Blue"}


def kit_color(stl_stem: str) -> str:
    """ファイル名のカラートークン (_Blue/_Grey/_White/_Black/_Red) から
    キット配色を返す。トークンが無ければグレー既定。"""
    stl_stem = COLOR_NAME_ALIAS.get(stl_stem, stl_stem)
    for tok in stl_stem.split("_"):
        if tok in COLOR_HEX:
            return COLOR_HEX[tok]
    return _DEFAULT_COLOR


def _resolve_stl(stem: str) -> tuple[str, Path]:
    stem = STL_RENDER_OVERRIDE.get(stem, stem)
    if stem in PRESCALED:
        path = STL / f"{stem}.stl"
    else:
        stem = STL_ALIAS.get(stem, stem)
        path = MODEL / f"{stem}.stl"
    if not path.exists():
        raise FileNotFoundError(f"kit_assembly: STL が見つからない '{stem}' -> {path}")
    return stem, path


def rot(deg: float, axis: str) -> np.ndarray:
    t = np.radians(deg)
    c, s = np.cos(t), np.sin(t)
    m = np.eye(4)
    if axis == "x":
        m[1, 1], m[1, 2], m[2, 1], m[2, 2] = c, -s, s, c
    elif axis == "y":
        m[0, 0], m[0, 2], m[2, 0], m[2, 2] = c, s, -s, c
    else:
        m[0, 0], m[0, 1], m[1, 0], m[1, 1] = c, -s, s, c
    return m


def trans(x: float, y: float, z: float) -> np.ndarray:
    m = np.eye(4)
    m[:3, 3] = [x, y, z]
    return m


def _compose_R(pairs) -> np.ndarray:
    """[["z",180], ...] 形式のリストから回転行列を合成 (先頭が内側/先に適用)。"""
    R = np.eye(4)
    for axis, deg in (pairs or []):
        R = R @ rot(deg, axis)
    return R


def _matrix4(rows) -> np.ndarray:
    m = np.array(rows, dtype=float)
    if m.shape == (3, 4):
        m = np.vstack([m, [0.0, 0.0, 0.0, 1.0]])
    if m.shape != (4, 4):
        raise ValueError(f"kit_assembly: matrix の形状が不正 {m.shape}")
    return m


@dataclass
class Placement:
    part: str                    # パーツ名 (JSON の name / dict key。#side は除去)
    instance: str                 # インスタンス識別子 ("single" / "FR" / "eye_1_..." 等)
    stl_stem: str                  # model/*.stl の実ファイル名 (拡張子抜き)
    frame: str                      # "robot" | "link"
    link: Optional[str]              # frame=="link" のときの親リンク名
    R: Optional[np.ndarray]           # 4x4 (frame内 / link内 ローカル回転)。matrix指定時は未使用
    t: Optional[np.ndarray]            # 3-vector 平行移動 (mm)。matrix指定時は未使用
    matrix: Optional[np.ndarray]        # RAW頂点に直接適用する 4x4 (SCALE込み)
    color: str
    confidence: str
    unresolved: bool                      # True: 位置情報なし (描画不可)
    source: str                            # "front" | "rear"


def _iter_front(path: Path):
    data = json.loads(path.read_text())
    for entry in data["parts"]:
        if entry.get("assembly_used") is False:
            continue
        name = entry["name"]
        stl_stem, _ = _resolve_stl(name)
        color = kit_color(stl_stem)
        confidence0 = entry.get("confidence", "")
        frame_raw = entry["frame"]
        if frame_raw == "robot":
            frame, link = "robot", None
        else:
            frame, link = "link", frame_raw.split(":", 1)[1]
        matrix0 = _matrix4(entry["matrix"]) if "matrix" in entry else None
        R0 = _compose_R(entry.get("R_axis_deg", []))
        t0 = np.array(entry["t"], dtype=float) if entry.get("t") is not None else None
        # JSONは原型の配置記録。頭の前後位置は機構設計のconfigを正とする。
        # 旧t.y=12のままでは、眼・肩・検査のy=11と外殻だけが1mmずれていた。
        if name in ("Head_Top_Eyecut", "Head_Bottom_Blue") and frame == "robot":
            if t0 is None or matrix0 is not None:
                raise ValueError(f"頭部基準の配置形式が不正: {name}")
            t0[1] = C.ARM_MOUNT_HUB_Y
            if name == "Head_Top_Eyecut":
                t0[2] = C.HEAD_TOP_Z_OFFSET
        # entry 自身が明示的に "unresolved": true を持つ場合 (例:
        # Leg_Toe_Black_x12 — t は入っているが "reasoned design estimate
        # only" と明記) は、t が存在していても unresolved 扱いにする。
        # 従来は t の有無しか見ておらず、この明示フラグが無視されていた
        # (QA major 指摘の「浮遊パーツ」原因の一つ)
        unresolved0 = bool(entry.get("unresolved", False))

        instances = entry.get("instances")
        if not instances:
            yield Placement(
                part=name, instance="single", stl_stem=stl_stem, frame=frame,
                link=link, R=None if matrix0 is not None else R0,
                t=None if matrix0 is not None else t0, matrix=matrix0,
                color=color, confidence=confidence0,
                unresolved=(unresolved0 or (t0 is None and matrix0 is None)),
                source="front")
            continue
        for inst in instances:
            inst_id = inst.get("id", "inst")
            inst_conf = inst.get("confidence", confidence0)
            # インスタンスごとに異なる "matrix" を持つ場合 (例:
            # Leg_Toe_Black_x12 — 3本のトゥは各々スタブの実測軸が異なる
            # ため、Head_Eye_White_x3 のような単一共有 matrix では表せない)
            # はそちらを優先。無ければエントリ共通の matrix0 (全インスタンス
            # 共有, 従来どおり) にフォールバック
            inst_matrix = (_matrix4(inst["matrix"]) if "matrix" in inst
                           else matrix0)
            if inst_matrix is not None:
                yield Placement(
                    part=name, instance=inst_id, stl_stem=stl_stem, frame=frame,
                    link=link, R=None, t=None, matrix=inst_matrix,
                    color=color, confidence=inst_conf, unresolved=unresolved0,
                    source="front")
                continue
            if "t" in inst:
                it = inst["t"]
                if it is None:
                    yield Placement(
                        part=name, instance=inst_id, stl_stem=stl_stem,
                        frame=frame, link=link, R=None, t=None, matrix=None,
                        color=color, confidence=inst_conf, unresolved=True,
                        source="front")
                    continue
                t_use = np.array(it, dtype=float)
                R_use = (_compose_R(inst["R_axis_deg"])
                         if inst.get("R_axis_deg") else R0)
            else:
                t_use, R_use = t0, R0
            yield Placement(
                part=name, instance=inst_id, stl_stem=stl_stem, frame=frame,
                link=link, R=R_use, t=t_use, matrix=None, color=color,
                confidence=inst_conf, unresolved=(unresolved0 or (t_use is None)),
                source="front")


def _iter_rear(path: Path):
    data = json.loads(path.read_text())
    references = data["meta"]["shell_translation_reference_20260905"]
    for key, entry in data["parts"].items():
        if entry.get("assembly_used") is False:
            continue
        part = key.split("#", 1)[0]
        stl_stem, _ = _resolve_stl(Path(entry["source_stl"]).stem)
        color = kit_color(stl_stem)
        instance = key.split("#", 1)[1] if "#" in key else "single"
        if entry.get("config_peg_pose"):
            pose = C.CABIN_PEG_POSES[entry["config_peg_pose"]]
            R = np.eye(4)
            for axis, deg in pose["rotations"]:
                R = rot(deg, axis) @ R
            yield Placement(part=part, instance=instance, stl_stem=stl_stem,
                            frame="robot", link=None, R=R,
                            t=np.asarray(pose["translation"]), matrix=None,
                            color=color, confidence=entry["confidence"],
                            unresolved=False, source="rear")
            continue
        mount = entry.get("shell_mount")
        shift = np.zeros(3)
        if mount in C.CABIN_POSES:
            shift = np.asarray(C.CABIN_POSES[mount]["translation"]) - np.asarray(references[mount])
        # "matrix" がある場合 (2026-07-29 追加、Cabin_Turrent_Left/Right_Grey
        # 用): raw STL 頂点に直接適用する 4x4 (front JSON の Head_Eye_White_x3
        # と同じ規約 — SCALE は 3x3 ブロックに焼き込み済み)。bbox 中心化を
        # 経由しない = パーツ自身の bbox 中心と実際の取付面 (フランジ/リム)
        # 中心がズレているケース (タレットは 8.6mm ズレていた実測済み、
        # 2026-07-29) でも 0.1mm 精度で置ける。pos_mm/outward_normal は
        # そのケースでは「フランジ中心の参考値」として JSON に残すのみで
        # 描画には使わない。
        if "matrix" in entry:
            matrix = _matrix4(entry["matrix"])
            matrix[:3, 3] += shift
            yield Placement(
                part=part, instance=instance, stl_stem=stl_stem, frame="robot",
                link=None, R=None, t=None, matrix=matrix,
                color=color, confidence=entry.get("confidence", ""),
                unresolved=False, source="rear")
            continue
        pos = entry.get("pos_mm")
        t = np.array(pos, dtype=float) + shift if pos is not None else None
        # rear JSON は基本的に回転情報を持たない (ring 抽出はメッシュ自体の
        # bbox 中心化×1.5 済みローカル座標系での位置のみを与える)。
        # "outward_normal" がある場合のみ、ローカル +Z (LOCAL_AXIS_OVERRIDE
        # に載るパーツはそこで指定した軸) をその法線へ向ける (eyes_video() の
        # align_vectors([0,0,1], n) と同じ手法) — 無いものは無回転のまま
        # (見た目が浮いて見えることがある既知の限界。位置自体はシェル表面
        # から数 mm 以内であることを別途確認済み)
        n = entry.get("outward_normal")
        src_axis = LOCAL_AXIS_OVERRIDE.get(part, (0.0, 0.0, 1.0))
        R_normal = trimesh.geometry.align_vectors(src_axis, n) if n else np.eye(4)
        # 2026-07-30 反転監査: outward_normal も matrix も持たない (= 既定で
        # 無回転だった) パーツは、raw STL のローカル軸がそのままロボット
        # 座標系の向きに一致する保証が無い。Head_TailJoint_Blue/Ball が実際
        # 180°反転していた (ソリッド同士が突き合い、意図したホロー勘合面が
        # 露出側に来ていた — 反転監査 flip_findings 参照) ため、front JSON
        # と同じ "R_axis_deg" (list of ["axis",deg]) 形式を rear JSON でも
        # 受け付ける。outward_normal 由来の回転より先 (ローカル側) に合成
        # するので、両方が指定された場合はローカル反転→法線整列の順で効く。
        R_flip = _compose_R(entry.get("R_axis_deg", []))
        R = R_normal @ R_flip
        yield Placement(
            part=part, instance=instance,
            stl_stem=stl_stem, frame="robot", link=None, R=R, t=t,
            matrix=None, color=color, confidence=entry.get("confidence", ""),
            unresolved=(t is None), source="rear")


_CACHE: list[Placement] | None = None


def load_placements(force: bool = False) -> list[Placement]:
    """front/rear 両 JSON を読み、Placement のフラットなリストを返す (キャッシュ済み)。"""
    global _CACHE
    if _CACHE is None or force:
        _CACHE = (list(_iter_front(DATA / "kit_assembly_front.json"))
                  + list(_iter_rear(DATA / "kit_assembly_rear.json")))
    return _CACHE


def by_link(placements: list[Placement], link: str) -> list[Placement]:
    return [p for p in placements if p.frame == "link" and p.link == link
            and not p.unresolved]


def by_part(placements: list[Placement], part: str) -> list[Placement]:
    return [p for p in placements if p.part == part and not p.unresolved]


def robot_only(placements: list[Placement]) -> list[Placement]:
    return [p for p in placements if p.frame == "robot" and not p.unresolved]


def cabin_transform(stem: str) -> np.ndarray:
    """配置定数からCabinの原型中心化座標→プレート下面座標を作る。"""
    pose = C.CABIN_POSES[stem]
    rotation = np.eye(4)
    for axis, deg in pose["rotations"]:
        rotation = rotation @ rot(deg, axis)
    return trans(*pose["translation"]) @ rotation


def normalized_mesh(stem: str) -> trimesh.Trimesh:
    """配置前の150%ローカル形状。加工部品も原型の基準座標を維持する。"""
    resolved, path = _resolve_stl(stem)
    if stem in AUDIO_RENDER_OVERRIDE:
        original = trimesh.load(path, force="mesh")
        mesh = trimesh.load(STL / f"{AUDIO_RENDER_OVERRIDE[stem]}.stl", force="mesh")
        mesh.apply_translation(-original.bounds.mean(axis=0) * SCALE)
        return mesh
    mesh = trimesh.load(path, force="mesh")
    if resolved not in PRESCALED:
        mesh.apply_translation(-mesh.bounds.mean(axis=0))
        mesh.apply_scale(SCALE)
    return mesh


def oriented_mesh(p: Placement) -> trimesh.Trimesh:
    """p のローカル座標系でのメッシュを返す。

    frame=="robot" ならロボット座標 (z はプレート下面 z0=0 基準、+zb は
    呼び出し側で加える)。frame=="link" なら p.link のローカル座標系
    (呼び出し側がさらにキャリア変換を 1 回適用してロボット座標へ運ぶ)。
    """
    if p.matrix is not None:
        # matrixは原型RAW頂点向け。音声加工品のSCALEを一度戻してから適用する。
        if p.stl_stem in AUDIO_RENDER_OVERRIDE:
            tm = trimesh.load(STL / f"{AUDIO_RENDER_OVERRIDE[p.stl_stem]}.stl", force="mesh")
            tm.apply_scale(1 / SCALE)
        else:
            _, path = _resolve_stl(p.stl_stem)
            tm = trimesh.load(path, force="mesh")
        tm.apply_transform(p.matrix)
    else:
        tm = normalized_mesh(p.stl_stem)
        tm.apply_transform(trans(*p.t) @ p.R)
    return tm


if __name__ == "__main__":
    placements = load_placements()
    by_conf: dict[str, int] = {}
    for p in placements:
        by_conf[p.confidence] = by_conf.get(p.confidence, 0) + 1
    print(f"loaded {len(placements)} placements "
          f"({sum(1 for p in placements if p.unresolved)} unresolved)")
    for conf, n in sorted(by_conf.items()):
        print(f"  confidence={conf!r}: {n}")
    for p in placements:
        print(f"  [{p.source}] {p.part}#{p.instance:<20s} "
              f"frame={p.frame}{(':' + p.link) if p.link else '':<22s} "
              f"stl={p.stl_stem:<38s} color={p.color} "
              f"{'UNRESOLVED' if p.unresolved else ''}")
