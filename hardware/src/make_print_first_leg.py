#!/usr/bin/env python3
"""既存候補 記録上の候補 LD-220MG を使う印刷優先脚部品。

旧 ``make_leg`` のリンク長・軸線・可動部を残し、標準サーボの耳/タブだけを
使う部分を、耳のない LD-220MG を差し込める保持ケージへ置き換える。
元の ``hardware/stl`` は変更しない。ここで作る部品は
``outputs/print-first-20260905/legs`` へ出力し、
``tools/print_first_assembly.py`` の明示的な構成でだけ採用する。

座標系は旧リンクと同じ（単位 mm）。LD の公式寸法はケース 39.78 x 20.04 x
40.00、軸をケース上端から 10 mm とした試作値、主ホーン面を 6.4 mm とした
候補値を使う。軸位置、金属ホーンの穴径/PCD/ねじは現物未測定なので、
``candidate.json`` の状態を適合保証に使ってはならない。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import trimesh
from manifold3d import Manifold

import config as C
import make_leg as L
import make_ld220_adapter as D
from lib import to_trimesh

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "outputs" / "print-first-20260905" / "legs"

# 出力軸の主ホーン面。旧リンクの軸線は変えず、LD本体のケースだけを
# この面から配置する。いずれも主面/ケース端の実測前の候補値である。
PITCH_FACE_Y = 17.5
YAW_FACE_Z = 17.1 # legacy fallback; print-first uses config.PRINT_FIRST
CASE_CABLE_CLEAR = 0.5
CASE_CAP_CLEAR = 0.1


def yaw_face_z() -> float:
  """印刷優先構成のヨー主面高さを返す。

  ヨー軸は鉛直軸なので、主面を同じZ方向へ移すだけなら
  coxa/femur/tibia の軸線とリンク長は変わらない。通常構成では
  旧候補値を返し、印刷優先構成だけ config の比較候補を適用する。
  """
  if getattr(C, "PRINT_FIRST_ACTIVE", False):
    return float(C.PRINT_FIRST.get("yaw_face_z", YAW_FACE_Z)
           + C.PRINT_FIRST.get("yaw_face_z_lift", 0.0))
  return YAW_FACE_Z


def pitch_face_y() -> float:
  """印刷優先構成のpitch/knee主面を返す（configを唯一の正にする）。"""
  if getattr(C, "PRINT_FIRST_ACTIVE", False):
    return float(C.PRINT_FIRST.get("pitch_face_y", PITCH_FACE_Y))
  return PITCH_FACE_Y


def _xform(m: Manifold, matrix: np.ndarray) -> Manifold:
  """4x4行列をManifoldの3x4変換として適用する。"""
  matrix = np.asarray(matrix, dtype=float)
  if matrix.shape != (4, 4):
    raise ValueError(f"4x4 transform required, got {matrix.shape}")
  return m.transform(matrix[:3, :])


def _translation(x: float, y: float, z: float) -> np.ndarray:
  m = np.eye(4)
  m[:3, 3] = [x, y, z]
  return m


def _rotation(deg: float, axis: str) -> np.ndarray:
  from scipy.spatial.transform import Rotation
  m = np.eye(4)
  m[:3, :3] = Rotation.from_euler(axis, deg, degrees=True).as_matrix()
  return m


def _link_mirror() -> np.ndarray:
  """脚リンクの左右反転行列（形状変換専用、booleanへは渡さない）。"""
  return np.diag([1.0, -1.0, 1.0, 1.0])


def _case_mount_transform(kind: str) -> np.ndarray:
  """LDケースの基準座標を各リンクのサーボ軸へ置く。"""
  if kind == "yaw":
    # ケースは軸線の+Z側、主面は既存coxaの上面 z=17.1。
    return _translation(0.0, 0.0, yaw_face_z()) @ _rotation(180.0, "x")
  if kind in ("pitch", "knee"):
    x = C.COXA_LEN if kind == "pitch" else C.FEMUR_LEN
    return _translation(x, pitch_face_y(), 0.0) @ _rotation(-90.0, "x")
  raise ValueError(f"unknown case orientation: {kind}")


def _adapter_mount_transform(kind: str) -> np.ndarray:
  """印刷リンク側の円盤変換板を各関節軸へ置く。"""
  if kind == "yaw":
    return _translation(0.0, 0.0, yaw_face_z()) @ _rotation(180.0, "x")
  if kind == "pitch":
    return _translation(0.0, pitch_face_y(), 0.0) @ _rotation(-90.0, "x")
  if kind == "knee":
    # 主面は+Yのまま、タブを足の伸長方向(-Z)へ向ける。
    return (_translation(0.0, pitch_face_y(), 0.0)
        @ _rotation(-90.0, "x") @ _rotation(90.0, "z"))
  raise ValueError(f"unknown adapter orientation: {kind}")


def _case_frame(kind: str) -> Manifold:
  """LD保持ケージを対応するリンクローカル座標へ置く。"""
  p = D.Candidate()
  return _xform(D.cradle(p).translate([0, 0, -p.main_projection]),
         _case_mount_transform(kind))


def _yaw_riser() -> Manifold:
  """旧coxa天板と上げたyaw変換板をつなぐ環状立上がり。

  yaw主面を上げると、旧coxa天板上面 (L.COXA_TOP) と変換板下面の
  間に空隙が生じる。別購入スペーサーを使わず、変換板のホーン
  ポケットを塞がない薄い環をcoxaへ一体化する。上下の重なり量は
  configで管理し、出力時に最終STLが1つの正体積成分になることを
  検査する。寸法・PLAの層間強度は現物未確認である。
  """
  p = D.Candidate()
  f = C.PRINT_FIRST if getattr(C, "PRINT_FIRST_ACTIVE", False) else {}
  outer_d = float(f.get("yaw_riser_outer_d", 34.0))
  inner_d = float(f.get("yaw_riser_inner_d", 26.6))
  base_overlap = float(f.get("yaw_riser_base_overlap", 0.5))
  adapter_overlap = float(f.get("yaw_riser_adapter_overlap", 0.5))
  if outer_d <= inner_d or inner_d <= 0.0:
    raise ValueError("yaw riser diameter contract is invalid")
  z0 = float(L.COXA_TOP - base_overlap)
  # _horn_adapter('yaw') has its lower face at yaw_face_z()-plate_t after
  # the canonical main-face translation and 180-degree X rotation.
  adapter_bottom = float(yaw_face_z() - p.plate_t)
  z1 = adapter_bottom + adapter_overlap
  if z1 <= z0:
    raise ValueError(f"yaw riser has no bridge height: {z0}..{z1}")
  height = z1 - z0
  ring = D.cyl(height, outer_d) - D.cyl(height + 1.0, inner_d)
  return ring.translate([0.0, 0.0, (z0 + z1) / 2.0])


def _case_cap(kind: str) -> Manifold:
  p = D.Candidate()
  return _xform(D.cap(p).translate([0, 0, -p.main_projection]),
         _case_mount_transform(kind))


def _case_occupied(kind: str, clear: float = 0.0) -> Manifold:
  """実ケース+出口ケーブルを対応リンク座標へ置いた包絡。"""
  p = D.Candidate()
  shape = D.case_envelope(p) + D.cable_reservation(p)
  shape = shape.translate([0, 0, -p.main_projection])
  # _case_mount_transform() already places the pitch/knee case at the
  # +Y=17.5 mm face. Keep this occupied volume in the same canonical
  # (shaft-frame) coordinates as ld220_case_mesh(), otherwise applying the
  # mount transform would translate it a second time and cut the wrong
  # section out of the link.
  if clear:
    shape = shape.minkowski_sum(D.box(2 * clear, 2 * clear, 2 * clear))
  return _xform(shape, _case_mount_transform(kind))


def _horn_adapter(orientation: str) -> Manifold:
  """金属円盤ホーンとリンクをつなぐ印刷板。

  生 adapter の金属ホーン面は z=2.2。z=-2.2 として樹脂荷重板を
  canonical z=0..4 に移し、metal horn が主面の内側 z=-2..0 に収まる。
  ``orientation`` は ``pitch``（軸 +Y）、``knee``（軸 +Y・タブ -Z）または
  ``yaw``（軸 -Z）である。
  """
  p = D.Candidate()
  m = D.horn_adapter(p).translate([0, 0, -p.horn_t - p.horn_clear])
  return _xform(m, _adapter_mount_transform(orientation))


def _horn_adapter_voids(orientation: str) -> Manifold:
  """変換板の穴・ポケットを、母材とのunion後に再切削する負形状。"""
  p = D.Candidate()
  pocket_h = p.horn_t + p.horn_clear
  voids = D.cyl(pocket_h + 1, p.horn_d + 2 * p.horn_clear).translate(
    [0, 0, (pocket_h - 1) / 2])
  voids += D.cyl(30, p.shaft_access_d)
  for angle in (0, 90, 180, 270):
    voids += D.radial_slot(p).rotate([0, 0, angle])
  for x in (25, 34):
    voids += D.cyl(30, 2.5).translate([x, 0, 0])
  voids = voids.translate([0, 0, -p.horn_t - p.horn_clear])
  return _xform(voids, _adapter_mount_transform(orientation))


def ld220_case_mesh(kind: str = "pitch", mirror: bool = False) -> trimesh.Trimesh:
  """物理衝突検査へ渡す LD 本体+ケーブル予約包絡。

  ケースは中身を仮定せず、ケース実寸の箱とケーブル予約をManifoldで
  unionした単一閉体にする。これは質量形状ではなく衝突包絡であり、
  実ケーブルの曲率は現物確認が必要。戻り値はサーボ軸フレームへ適用
  する前の局所座標である。
  """
  p = D.Candidate()
  if kind not in ("yaw", "pitch", "knee"):
    raise ValueError(f"unknown case orientation: {kind}")
  # 連結したSTLをtrimesh.concatenateしない。重なりを二重計上せず、
  # Manifoldのunion状態とError.NoErrorを事前に検査できる形にする。
  shape = D.case_envelope(p) + D.cable_reservation(p)
  shape = shape.translate([0, 0, -p.main_projection])
  if kind in ("pitch", "knee"):
    # ケース前面 z=0 を主面から+17.5mmへ戻す。
    shape = shape.translate([0, 0, pitch_face_y()])
  if shape.status().name != "NoError":
    raise ValueError(f"LD case union failed: {shape.status()}")
  mesh = to_trimesh(shape).copy()
  if not np.isfinite(mesh.vertices).all() or not np.isfinite(mesh.volume) or mesh.volume <= 0:
    raise ValueError("LD case union produced non-finite/non-positive mesh")
  if mirror:
    # 負行列を boolean に入れないため、座標値を直接反転してから
    # 法線を再計算する。衝突用メッシュは trimesh で扱う。
    mesh.vertices[:, 1] *= -1
    mesh.invert()
  return mesh


def ld220_mass_item(frame: np.ndarray, label: str, kind: str = "pitch"):
  """LD 本体 66 g のリンクローカル質量項を作る。

  import 循環を避けるため MassItem の生成は呼び出し側の
  ``export_urdf.box_mass_item`` へ委譲する。ケースの COM は軸中心から
  (−9.89, 0, −20) mm。ケーブル/ホーン/樹脂は別部品の質量として計上する。
  """
  import export_urdf as E

  p = D.Candidate()
  z = -p.case_h / 2 - p.main_projection
  if kind in ("pitch", "knee"):
    z += pitch_face_y()
  center = np.array([-(p.case_l / 2 - p.shaft_from_end), 0.0, z])
  item = E.box_mass_item(66.0, (p.case_l, p.case_w, p.case_h),
              (frame @ np.r_[center, 1.0])[:3], label,
              verified=False)
  item.I_com = frame[:3, :3] @ item.I_com @ frame[:3, :3].T
  return item


def print_first_leg_frames(leg: str) -> dict[str, np.ndarray]:
  """旧関節軸を維持した LD ケースの軸フレーム。

  ケースの保持方式が変わっても出力軸の基準は同じため、
  ``export_urdf.leg_servo_frames`` の座標規約をそのまま採用する。
  """
  from make_chassis import CASE_ANG
  ox, oy = C.HIPS[leg]
  # 新ケージのLD主面はリンク側のworld/base z=17.1。旧STDの
  # プレート上ボスz=34.6を流用するとケースが17.5mm高くずれる。
  # print-first body/collision use their own explicitly recorded case angles.
  # Falling back to the legacy angles here silently rotates the four LD cases
  # relative to the cages generated by make_print_first_body.py.
  angles = (C.PRINT_FIRST.get("yaw_case_angles", CASE_ANG)
       if getattr(C, "PRINT_FIRST_ACTIVE", False) else CASE_ANG)
  yaw_frame = (_translation(ox, oy, yaw_face_z())
         @ _rotation(float(angles[leg]), "z") @ _rotation(180, "x"))
  mirror = np.diag([1.0, -1.0 if leg in ("FR", "RL") else 1.0, 1.0, 1.0])
  pitch_frame = mirror @ _translation(C.COXA_LEN, 0, 0) @ _rotation(-90, "x")
  knee_frame = mirror @ _translation(C.FEMUR_LEN, 0, 0) @ _rotation(-90, "x")
  return {"yaw": yaw_frame, "pitch": pitch_frame, "knee": knee_frame}


def _cable_channel(kind: str) -> Manifold:
  """LDケーブル予約に0.5mmの印刷逃げを加えた切削体。"""
  p = D.Candidate()
  cable = D.cable_reservation(p).translate([0, 0, -p.main_projection])
  cable = cable.minkowski_sum(D.box(2 * CASE_CABLE_CLEAR,
                   2 * CASE_CABLE_CLEAR,
                   2 * CASE_CABLE_CLEAR))
  return _xform(cable, _case_mount_transform(kind))


def _integrate_adapter(link: Manifold, orientation: str) -> Manifold:
  """旧リンクと変換板を一体化し、unionで埋まった穴を再切削する。"""
  joined = link + _horn_adapter(orientation)
  joined -= _horn_adapter_voids(orientation)
  return joined.simplify(.005)


def _tibia_cap_escape() -> Manifold:
  """膝蓋の可動包絡だけを脛背面から局所的に抜く。

  ``pf_ld220_femur_cap`` は femur 側の交換蓋なので、蓋や LD ケースを
  削らず、tibia の y=14.9 mm 内側縁だけを抜いて 0.2 mm の印刷逃げを
  残す。x/z 範囲は LIM_KNEE=44 deg の実メッシュ sweep を包含する候補
  で、左右鏡像は structural_parts() の最後に一度だけ適用する。
  """
  f = C.PRINT_FIRST
  x0, x1 = (float(v) for v in f['tibia_cap_escape_x'])
  y0, y1 = (float(v) for v in f['tibia_cap_escape_y'])
  z0, z1 = (float(v) for v in f['tibia_cap_escape_z'])
  if not (x1 > x0 and y1 > y0 and z1 > z0):
    raise ValueError('tibia cap escape bounds are invalid')
  return D.box(x1 - x0, y1 - y0, z1 - z0).translate(
    [(x0 + x1) / 2.0, (y0 + y1) / 2.0, (z0 + z1) / 2.0])


def _tibia_cap_escape_contract(tibia: Manifold, mirror: bool = False) -> dict:
  """最終脛STLと蓋を firmware 膝範囲で実メッシュ掃引する。

  この検査は ``structural_parts`` の最終形状に対して行う。蓋を削ったり
  衝突を除外したりせず、各角度の Manifold Boolean を記録するため、
  adapter のunionで逃げが埋め戻された場合も検出できる。
  """
  cap = _cap_for_link("knee", mirror)
  knee_limit = float(C.PRINT_FIRST.get("tibia_cap_escape_knee_limit_deg", 44.0))
  step = float(C.PRINT_FIRST.get("tibia_cap_escape_sweep_step_deg", 0.5))
  if not (np.isfinite(knee_limit) and knee_limit > 0.0
      and np.isfinite(step) and step > 0.0):
    raise ValueError("invalid tibia cap escape sweep range")
  relative_base = _translation(C.FEMUR_LEN, 0.0, 0.0)
  values = []
  negative_samples = []
  angles = np.arange(-knee_limit, knee_limit + step * 0.25, step)
  for angle in angles:
    moved = _xform(cap, np.linalg.inv(
      relative_base @ _rotation(float(angle), "y")))
    overlap = moved ^ tibia
    status = overlap.status()
    if status.name != "NoError":
      raise ValueError(f"tibia/cap sweep Boolean failed: {status}")
    volume = float(overlap.volume())
    if not np.isfinite(volume) or volume < -C.BOOLEAN_NEGATIVE_TOLERANCE_MM3:
      raise ValueError(f"tibia/cap sweep volume invalid: {volume!r}")
    if volume < 0.0:
      negative_samples.append({"angle_deg": float(angle),
                   "signed_volume_mm3": volume,
                   "tolerance_mm3": C.BOOLEAN_NEGATIVE_TOLERANCE_MM3,
                   "native_status": status.name})
      values.append(0.0)
    else:
      values.append(volume)
  max_volume = max(values, default=float("nan"))
  return {
    "status": "PASS" if np.isfinite(max_volume) and max_volume <= 0.01 else "FAIL",
    "angle_min_deg": -knee_limit,
    "angle_max_deg": knee_limit,
    "angle_step_deg": step,
    "sample_count": len(values),
    "max_intersection_mm3": max_volume,
    "negative_volume_samples": negative_samples,
    "negative_volume_tolerance_mm3": C.BOOLEAN_NEGATIVE_TOLERANCE_MM3,
    "hit_count_over_0_001mm3": sum(v > 0.001 for v in values),
    "escape_bounds_mm": {
      "x": list(C.PRINT_FIRST["tibia_cap_escape_x"]),
      "y": list(C.PRINT_FIRST["tibia_cap_escape_y"]),
      "z": list(C.PRINT_FIRST["tibia_cap_escape_z"]),
    },
    "serialized_components_required": 1,
    "final_tibia_components": len(tibia.decompose()),
    "physical_strength_status": "UNVERIFIED",
  }


def structural_parts(mirror: bool = False) -> dict[str, Manifold]:
  """旧リンクへLD保持ケージと円盤変換板を一体化した3リンクを返す。"""
  # 旧リンクの軸線/長さは維持する。ケーブル予約を先に切り、LDケースを
  # 入れたときに+X側の出口を潰さない。
  fit_clear = C.PRINT_FIRST.get("ld_case_fit_clear", D.Candidate().gap)
  coxa = L.coxa_bracket() - _cable_channel("pitch") - _case_occupied("pitch", fit_clear)
  femur = L.femur_link() - _cable_channel("knee") - _case_occupied("knee", fit_clear)
  tibia = L.tibia_link()
  # At knee angles 34..44 deg the LD femur cap reaches y=15.4 mm while the
  # retained tibia back web begins at y=14.9 mm. Keep the cap/case/bolts
  # intact and subtract only the config-driven local web strip. The final
  # integrated STL is swept again, so an adapter union cannot refill it.
  tibia -= _tibia_cap_escape()

  # ケース保持壁は既存リンクへunionする。ケージ自身はケース形状と
  # cable_reservationの差分を持つため、別の「箱」を重ねない。
  coxa += _case_frame("pitch")
  femur += _case_frame("knee")

  # 円盤変換板もリンクの構造体へ一体化する。元のSTDホーンポケットを
  # unionで埋めたままにせず、付属金属ホーンのポケット・中心逃げ・
  # 径方向長穴・リンク側2穴を最後に再切削する。
  # The raised yaw transform plate is connected to the old coxa top by an
  # integrated annular riser. Without this ring the boolean result has two
  # positive components and the serialized link cannot transmit torque.
  coxa += _yaw_riser()
  coxa = _integrate_adapter(coxa, "yaw")
  femur = _integrate_adapter(femur, "pitch")
  tibia = _integrate_adapter(tibia, "knee")

  # Manifold mirror は面の向きを処理するので、trimesh負行列は使わない。
  out = {"coxa_bracket": coxa.simplify(.005),
      "femur_link": femur.simplify(.005),
      "tibia_link": tibia.simplify(.005)}
  if mirror:
    out = {k: v.mirror([0, 1, 0]) for k, v in out.items()}
  return out


def _yaw_riser_connection_contract(mirror: bool = False) -> dict:
  """最終切削後に環と上下の母材が実体積で重なることを記録する。"""
  fit_clear = C.PRINT_FIRST.get("ld_case_fit_clear", D.Candidate().gap)
  base = (L.coxa_bracket() - _cable_channel("pitch")
      - _case_occupied("pitch", fit_clear) + _case_frame("pitch"))
  adapter = _horn_adapter("yaw")
  ring = _yaw_riser()
  if mirror:
    base = base.mirror([0, 1, 0])
    adapter = adapter.mirror([0, 1, 0])
    ring = ring.mirror([0, 1, 0])
  # The adapter holes/slots are cut after all positive geometry is joined.
  # Apply that same final negative shape before reporting the connection so
  # a raw ring overlap cannot hide a post-cut neck or a severed section.
  final_voids = _horn_adapter_voids("yaw")
  base = base - final_voids
  adapter = adapter - final_voids
  ring = ring - final_voids
  base_overlap = ring ^ base
  adapter_overlap = ring ^ adapter
  for label, shape in (("base", base_overlap), ("adapter", adapter_overlap)):
    if shape.status().name != "NoError" or not np.isfinite(shape.volume()):
      raise ValueError(f"yaw riser {label} overlap Boolean failed")
  ring_mesh = to_trimesh(ring)
  z0, z1 = ring_mesh.bounds[:, 2]
  section_areas = []
  for z in np.linspace(z0 + 0.01, z1 - 0.01, 9):
    section = ring_mesh.section(
      plane_origin=np.asarray([0.0, 0.0, float(z)]),
      plane_normal=np.asarray([0.0, 0.0, 1.0]),
    )
    if section is None:
      continue
    area = float(section.to_planar()[0].area)
    if np.isfinite(area) and area > 0.0:
      section_areas.append(area)
  if not section_areas:
    raise ValueError("yaw riser post-cut cross-section could not be measured")
  return {
    "status": "GEOMETRIC_CANDIDATE_UNVERIFIED_PHYSICAL_STRENGTH",
    "ring_bbox_mm": ring_mesh.bounds.tolist(),
    "post_cut_ring_volume_mm3": float(ring.volume()),
    "post_cut_ring_components": len(ring.decompose()),
    "base_overlap_volume_mm3": float(base_overlap.volume()),
    "adapter_overlap_volume_mm3": float(adapter_overlap.volume()),
    "base_overlap_z_mm": float(C.PRINT_FIRST.get("yaw_riser_base_overlap", 0.5)),
    "adapter_overlap_z_mm": float(C.PRINT_FIRST.get("yaw_riser_adapter_overlap", 0.5)),
    "post_cut_section_area_min_mm2": float(min(section_areas)),
    "post_cut_section_area_max_mm2": float(max(section_areas)),
    "post_cut_section_samples": len(section_areas),
    "serialized_positive_components_required": 1,
  }


def _save_mesh(shape: Manifold, path: Path) -> dict:
  path.parent.mkdir(parents=True, exist_ok=True)
  simplified = shape.simplify(.005)
  mesh = to_trimesh(simplified)
  raw_components = mesh.split(only_watertight=False)
  if any(not np.isfinite(part.volume) for part in raw_components):
    raise ValueError(f"{path.name}: non-finite component volume")
  negative_components = [part for part in raw_components if part.volume < -1e-8]
  if negative_components:
    volumes = [float(part.volume) for part in negative_components]
    raise ValueError(
      f"{path.name}: negative-volume component(s) would be discarded: {volumes}"
    )
  components = [part for part in raw_components if part.volume > 1e-8]
  zero_artifacts = [part for part in raw_components if abs(part.volume) <= 1e-8]
  # Booleanの同一平面処理で残る微小なゼロ体積片だけを明示的に捨てる。
  # 正の体積片や面積のある開殻を削って合格扱いにはしない。
  if any(part.area > 1.0 for part in zero_artifacts):
    raise ValueError(f"{path.name}: non-trivial zero-volume artifact")
  if len(components) != 1:
    raise ValueError(f"{path.name}: {len(components)} positive components")
  mesh = components[0].copy()
  if not np.isfinite(mesh.vertices).all() or not np.isfinite(mesh.volume):
    raise ValueError(f"{path.name}: non-finite serialized input")
  if not mesh.is_watertight or not mesh.is_winding_consistent or mesh.volume <= 0 or not mesh.is_volume:
    raise ValueError(f"{path.name}: STL前の閉形状検査失敗")
  data = mesh.export(file_type="stl")
  tmp = path.with_suffix(path.suffix + ".tmp")
  tmp.write_bytes(data)
  actual = trimesh.load(tmp, file_type="stl", force="mesh")
  if (not actual.is_watertight or not actual.is_winding_consistent
      or not np.isfinite(actual.volume) or actual.volume <= 0 or not actual.is_volume):
    tmp.unlink(missing_ok=True)
    raise ValueError(f"{path.name}: STL保存後の閉形状検査失敗")
  if not np.isclose(actual.volume, mesh.volume, atol=C.STL_VOLUME_ATOL_MM3,
           rtol=C.STL_VOLUME_RTOL):
    tmp.unlink(missing_ok=True)
    raise ValueError(f"{path.name}: STL体積往復不一致")
  tmp.replace(path)
  return {"path": str(path.relative_to(ROOT)),
      "exists": True,
      "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
      "bbox_mm": actual.extents.tolist(), "volume_mm3": float(actual.volume),
      "watertight": bool(actual.is_watertight),
      "winding_consistent": bool(actual.is_winding_consistent),
      "positive_components": 1, "components": len(components),
      "zero_volume_artifacts_removed": len(zero_artifacts),
      "solid_pla_g_upper_bound": float(actual.volume)
      * C.material_density_g_cm3("PLA") / 1000}


def _cap_for_link(kind: str, mirror: bool = False) -> Manifold:
  """リンクと重なる部分を抜いた、交換可能な保持蓋を作る。"""
  if kind not in ("pitch", "knee"):
    raise ValueError(f"unknown cap orientation: {kind}")
  cap = _case_cap(kind)
  # 旧リンクだけでなく、LDケージ・一体変換板・ケーブル逃げを含む
  # 最終リンクの面に突き抜けないよう、接触面より内側だけを抜く。
  # 蓋はケース壁のねじ締結で保持するため、リンクとの体積共有は残さない。
  if kind == "pitch":
    old = _integrate_adapter(
      L.coxa_bracket() - _cable_channel("pitch")
      - _case_occupied("pitch", C.PRINT_FIRST.get("ld_case_fit_clear", D.Candidate().gap))
      + _case_frame("pitch"), "yaw")
  else:
    old = _integrate_adapter(
      L.femur_link() - _cable_channel("knee")
      - _case_occupied("knee", C.PRINT_FIRST.get("ld_case_fit_clear", D.Candidate().gap))
      + _case_frame("knee"), "pitch")
  # STLの量子化で同一平面の微小片が残らないよう、0.1mmだけ余分に
  # 掘る。これは蓋のねじ締結には影響しない加工逃げである。
  old = old.minkowski_sum(D.box(2 * CASE_CAP_CLEAR,
                 2 * CASE_CAP_CLEAR,
                 2 * CASE_CAP_CLEAR))
  cap -= old
  if mirror:
    cap = cap.mirror([0, 1, 0])
  return cap.simplify(.005)


def _raw_metal_horn_mesh(side: str = "main") -> trimesh.Trimesh:
  """サーボ軸(+Z)基準の金属ホーン包絡。

  Canonical coordinates use the main horn outer face at ``z=0``. The
  companion disk's outer face is therefore at ``-total_with_horns`` and its
  material extends toward the main disk by ``horn_t``. Keeping this frame
  independent of pitch/knee's +Y face offset prevents a parent-link frame
  from silently moving only one of the two disks.
  """
  if side not in ("main", "assistant"):
    raise ValueError(f"unknown horn side: {side}")
  p = D.Candidate()
  z = -p.horn_t - p.horn_clear
  if side == "assistant":
    # horn_reference() occupies [0.2, 2.2] before translation. Place
    # its outside face at -51.4 and keep the 2 mm disk thickness inward:
    # the resulting bounds are [-51.4, -49.4], rather than the previous
    # [-53.4, -51.4] double-offset placement.
    z = -p.total_with_horns - p.horn_clear
  tm = to_trimesh(D.horn_reference(p).translate([0, 0, z]))
  if not tm.is_watertight or not tm.is_volume or not np.isfinite(tm.volume):
    raise ValueError(f"{side}: horn envelope invalid")
  return tm


def raw_metal_horn_mesh_for_kind(kind: str, side: str = "main") -> trimesh.Trimesh:
  """Return a disk in the parent-link shaft frame for ``kind``.

  ``print_first_leg_frames`` deliberately keeps pitch/knee's canonical
  face at Y=0. Their physical axis is placed at the old link's +Y=17.5 mm
  face by the same canonical-Z offset used by ``ld220_case_mesh``. Keeping
  this adjustment in one helper makes the case and both metal disks obey
  the same frame contract in URDF and collision checks.
  """
  if kind not in ("yaw", "pitch", "knee"):
    raise ValueError(f"unknown horn kind: {kind}")
  tm = _raw_metal_horn_mesh(side)
  if kind in ("pitch", "knee"):
    # This is a canonical-Z offset. The parent frame rotates canonical
    # +Z into the physical +Y face; applying it in world Y here would
    # place the companion disk 17.5 mm too far out of the link.
    tm.apply_translation([0.0, 0.0, pitch_face_y()])
  return tm


def interface_contract(kind: str) -> dict:
  """Numerical canonical interface contract used by downstream checks.

  These are design coordinates, not measurements of a physical LD-220MG.
  The case/cable mesh has a larger occupied bound than the rectangular
  case; the named faces below remain the placement references.
  """
  if kind not in ("yaw", "pitch", "knee"):
    raise ValueError(f"unknown interface kind: {kind}")
  p = D.Candidate()
  return {
    "canonical_main_horn_outer_z_mm": 0.0,
    "canonical_case_front_z_mm": -p.main_projection,
    "canonical_case_rear_z_mm": -p.case_h - p.main_projection,
    "canonical_assistant_horn_outer_z_mm": -p.total_with_horns,
    "canonical_assistant_horn_inner_z_mm": -p.total_with_horns + p.horn_t,
    "canonical_face_offset_y_mm": pitch_face_y() if kind in ("pitch", "knee") else 0.0,
    "source_status": "DESIGN_CANDIDATE_PHYSICAL_DIMENSIONS_UNVERIFIED",
  }


def metal_horn_mesh(kind: str = "pitch", side: str = "main") -> trimesh.Trimesh:
  """付属金属円盤ホーンの保守的な包絡を返す（質量は66gへ二重計上しない）。

  ``side=main`` はリンク側の主面、``side=assistant`` は LD の反対軸側。
  円盤径/厚み/軸方向51.4mmは候補値で、実物の円盤形状を意味しない。
  """
  if kind not in ("yaw", "pitch", "knee") or side not in ("main", "assistant"):
    raise ValueError(f"unknown horn placement: {kind}/{side}")
  # This function returns the child-link frame, whose adapter transform
  # below already contains the pitch/knee +Y face offset. Do not apply the
  # parent-link helper's offset a second time here.
  raw = _raw_metal_horn_mesh(side)
  tm = raw.copy()
  tm.apply_transform(_adapter_mount_transform(kind))
  return tm


def _material_rule(name: str) -> dict:
  """部品名に対応する明示的な印刷材料規則を返す。

  print-first の脚は汎用 ``pf_*`` 既定値へ落とさず、config の部品接頭辞
  を必ず一つ解決する。第二要素は密度ではなく壁厚なので、強度規則の
  材料密度とは別の項目として記録する。
  """
  matches = [
    (prefix, spec) for prefix, spec in C.PRINT_FIRST_MATERIALS.items()
    if name.startswith(prefix)
  ]
  if not matches:
    raise ValueError(f"{name}: print-first material rule is not explicit")
  prefix, (material, wall_mm, infill) = max(matches, key=lambda item: len(item[0]))
  if not (str(material) == "PLA" and float(wall_mm) > 0.0
      and 0.0 < float(infill) <= 1.0):
    raise ValueError(f"{name}: invalid print-first material rule {prefix}")
  strength = dict(C.PRINT_FIRST_STRENGTH_RULE)
  return {
    "config_key": f"PRINT_FIRST_MATERIALS[{prefix!r}]",
    "prefix": prefix,
    "material": str(material),
    "wall_mm": float(wall_mm),
    "infill_fraction": float(infill),
    "density_g_cm3": C.material_density_g_cm3(material),
    "strength_rule": strength if name.startswith(
      ("pf_coxa_bracket", "pf_femur_link", "pf_tibia_link")) else None,
    "status": "CONFIG_EXPLICIT_UNVERIFIED_PRINTED_STRENGTH",
  }


def _require_geometry_contract_status(record: dict, key: str, name: str) -> None:
  """生成物へ記録する機構契約を、書き出し前に失敗閉鎖する。"""
  if not isinstance(record, dict) or record.get(key) != "PASS":
    raise ValueError(f"{name}: {key} must be PASS before serialization")


def build() -> dict:
  """左右リンク・LD保持蓋を生成し、assembly.jsonを保存する。"""
  OUT.mkdir(parents=True, exist_ok=True)
  p = D.Candidate()
  rows = []
  for mirror, legs, suffix in ((False, ("FL", "RR"), ""),
                 (True, ("FR", "RL"), "_m")):
    shapes = structural_parts(mirror)
    for kind, shape in shapes.items():
      name = f"pf_{kind}{suffix}"
      rec = _save_mesh(shape, OUT / f"{name}.stl")
      link_kind = kind.removesuffix("_bracket") if kind.endswith("_bracket") else kind.removesuffix("_link")
      rec.update({"name": name, "role": "link_structure", "link_kind": link_kind,
            "link_kinds": [link_kind], "legs": list(legs),
            "replaces": [f"{kind}{suffix}"], "color": "#286bb1",
            "adapter_integrated": link_kind in ("coxa", "femur", "tibia"),
            "adapter_orientation": {"coxa": "yaw", "femur": "pitch",
                         "tibia": "knee"}.get(link_kind),
            "material_rule": _material_rule(name)})
      if link_kind == "coxa":
        # A single positive component is necessary but not sufficient
        # to describe the load path. Keep the exact generated ring
        # overlaps and the serialized component count in the manifest.
        rec["yaw_riser_connection"] = _yaw_riser_connection_contract(mirror)
        rec["yaw_riser_connection"]["serialized_positive_components"] = rec["components"]
        rec["yaw_riser_connection"]["connection_status"] = (
          "PASS" if rec["components"] == 1
          and rec["yaw_riser_connection"]["post_cut_ring_components"] == 1
          and rec["yaw_riser_connection"]["base_overlap_volume_mm3"] > 0.0
          and rec["yaw_riser_connection"]["adapter_overlap_volume_mm3"] > 0.0
          and rec["yaw_riser_connection"]["post_cut_section_area_min_mm2"] > 0.0
          else "FAIL")
        _require_geometry_contract_status(
          rec["yaw_riser_connection"], "connection_status", name)
      if link_kind == "tibia":
        rec["tibia_cap_escape_validation"] = _tibia_cap_escape_contract(
          shape, mirror)
        _require_geometry_contract_status(
          rec["tibia_cap_escape_validation"], "status", name)
      rows.append(rec)

    # 各脚の蓋は同一形状だが、リンクとの体積共有を抜いた交換部品。
    for cap_kind, link_kind in (("pitch", "coxa"), ("knee", "femur")):
      nm = f"pf_ld220_{link_kind}_cap{suffix}"
      rec = _save_mesh(_cap_for_link(cap_kind, mirror), OUT / f"{nm}.stl")
      rec.update({"name": nm, "role": "removable_case_cap", "link_kind": link_kind,
            "link_kinds": [link_kind], "legs": list(legs),
            "transform": np.eye(4).tolist(), "replaces": [],
            "color": "#607d8b", "fasteners_per_servo": 2,
            "fastener": "既購入M3x10皿タッピング候補",
            "intentional_overlap_mm3": 0.0,
            "material_rule": _material_rule(nm)})
      rows.append(rec)

  source_hashes = {str(f.relative_to(ROOT)): hashlib.sha256(f.read_bytes()).hexdigest()
           for f in (Path(__file__).resolve(), ROOT / "hardware/src/config.py",
                ROOT / "hardware/src/make_leg.py",
                ROOT / "hardware/src/make_ld220_adapter.py")}
  for row in rows:
    row["exists"] = True
    row["source"] = "hardware/src/make_print_first_leg.py"
    row["source_sha256"] = source_hashes["hardware/src/make_print_first_leg.py"]

  data = {
    "status": "GEOMETRY_CANDIDATE_PHYSICAL_FIT_UNVERIFIED",
    "dimensions_mm": {"case": [p.case_l, p.case_w, p.case_h],
             "axis_length_with_horns": p.total_with_horns,
             "main_projection_candidate": p.main_projection,
             "shaft_from_case_end_candidate": p.shaft_from_end,
             "pitch_axis_face": pitch_face_y(),
             "yaw_axis_face": yaw_face_z(),
             "yaw_face_z_lift": yaw_face_z() - YAW_FACE_Z},
    "tibia_cap_escape": {
      "x_mm": list(C.PRINT_FIRST['tibia_cap_escape_x']),
      "y_mm": list(C.PRINT_FIRST['tibia_cap_escape_y']),
      "z_mm": list(C.PRINT_FIRST['tibia_cap_escape_z']),
      "clearance_status": "GEOMETRIC_CANDIDATE_SWEEP_PLUS_0.2MM",
      "physical_strength_status": "UNVERIFIED",
    },
    "parts": rows,
    "material_policy": {
      "structural_parts": [
        "pf_coxa_bracket", "pf_coxa_bracket_m",
        "pf_femur_link", "pf_femur_link_m",
        "pf_tibia_link", "pf_tibia_link_m",
      ],
      "config_source": "hardware/src/config.py:PRINT_FIRST_MATERIALS",
      "strength_rule": dict(C.PRINT_FIRST_STRENGTH_RULE),
      "density_basis": "hardware/src/config.py:MATERIAL_DENSITY_G_CM3['PLA']; export_urdf.RHO derives from it",
      "status": "EXPLICIT_RULE_UNVERIFIED_PRINTED_MATERIAL_AND_LAYER_STRENGTH",
    },
    "legs": {"FL": {"suffix": "", "source": "pf_*"},
         "RR": {"suffix": "", "source": "pf_*"},
         "FR": {"suffix": "_m", "source": "pf_*_m"},
         "RL": {"suffix": "_m", "source": "pf_*_m"}},
    "mass_policy": "LDサーボアセンブリ公称66gを12台へ1回だけ計上。旧STD60gケースは置換し、付属ホーン/配線が66gへ含まれるかは未確認。adapterはリンクSTLへ一体化し、蓋/リンクの最終STL体積から印刷上限を別計上する。",
    "new_hardware": False,
    "unverified": [
      "全14台の実ケース角/軸端位置/ケーブル出口の個体差",
      "付属金属円盤ホーンの径・PCD・穴径・ねじ径/長さ/本数",
      "樹脂adapterと金属ホーンの実締結トルク/ねじ引抜き",
      "主面6.4mm・軸端10mmは公式図にない候補値",
      "従側円盤の外面は主面から51.4mm（pitchの候補位置Y=-33.9）だが、実径/厚み/ケース後壁との適合は未測定",
      "PLA層間強度・ケージ壁の動荷重・6V連続トルク",
      "1脚の適合試作・全脚の組立/動的干渉"],
    "assembly_order": [
      "付属の主/従金属ホーンを外し、中心ねじを保管",
      "ケースをケーブル溝へ逃がし、LDケージへ開口側から差し込む",
      "蓋を既購入M3x10皿タッピング2本で固定（実皿頭は要確認）",
      "金属ホーンを元の中心ねじで軸へ固定",
      "金属円盤のPCD/ねじを実測し、リンク一体adapterの長穴位置を1個だけ試す",
      "軸を手で全域回し、締結/ケーブル/ケース抜けを確認してから通電"],
    # config.py is part of the serialized geometry contract. A legs
    # manifest generated with the legacy yaw height must never be accepted
    # by print_first_assembly after PRINT_FIRST is changed.
    "source_sha256": source_hashes,
    "source_files": [
      {"path": path, "exists": True, "sha256": digest}
      for path, digest in source_hashes.items()
    ],
  }
  (OUT / "assembly.json").write_text(
    json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
  return data


if __name__ == "__main__":
  # This file is also the documented standalone generator. Running it
  # directly must use the same explicit print-first context as the assembly
  # and URDF generators; otherwise C.PRINT_FIRST_ACTIVE remains False and
  # the serialized links are produced at the legacy 17.1 mm yaw face while
  # the body/collision consumers use the raised face.
  sys.path.insert(0, str(ROOT / "tools"))
  import print_first_assembly as A
  with A.context(generated=False):
    result = build()
  print(json.dumps({"parts": len(result["parts"]), "output": str(OUT)}, ensure_ascii=False))
