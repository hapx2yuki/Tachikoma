#!/usr/bin/env python3
"""既存候補甲/脛/M3×40を使う三葉 TPU 靴。既存 STL は変更しない。

靴へ甲を上から入れ、脛へ差し込んだ後に左右の段付き PLA スペーサーと
既存の上下 M3×40 を通す。硬質トゥ/旧パッド/脛カバーは取り外し保管。
座標は leg_foot_bored と同じ。数値は本流採用前の関数引数で受ける。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile

import numpy as np
import trimesh
from manifold3d import Manifold, Mesh

import config as C
from lib import box, cyl, cyl_y, to_trimesh

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "outputs/print-first-20260905/feet"


def as_manifold(mesh):
  solid = Manifold(Mesh(np.asarray(mesh.vertices, np.float32),
             np.asarray(mesh.faces, np.uint32)))
  if solid.num_vert() == 0 or not math.isfinite(solid.volume()) or solid.volume() <= 0:
    raise ValueError("有効な非空の閉体が必要")
  return solid


def source_parts():
  foot = trimesh.load(ROOT / "hardware/stl/leg_foot_bored.stl", force="mesh")
  tibia = trimesh.load(ROOT / "hardware/stl/tibia_link.stl", force="mesh")
  tibia.apply_translation([0, 0, C.TIBIA_LEN])
  for name, mesh in (("甲", foot), ("脛", tibia)):
    if not isinstance(mesh, trimesh.Trimesh):
      raise ValueError(f"{name}の入力 STL がTrimeshではない")
    vertices = np.asarray(mesh.vertices)
    faces = np.asarray(mesh.faces)
    if (vertices.ndim != 2 or vertices.shape[1] != 3 or len(vertices) < 4
        or faces.ndim != 2 or faces.shape[1] != 3 or len(faces) < 4):
      raise ValueError(f"{name}の入力 STL が空または面数不足")
    if not np.isfinite(vertices).all() or not np.isfinite(faces).all():
      raise ValueError(f"{name}の入力 STL に有限でない頂点/面がある")
    if np.any(faces < 0) or np.any(faces >= len(vertices)):
      raise ValueError(f"{name}の入力 STL の面添字が範囲外")
    volume = float(mesh.volume)
    if (not np.isfinite(volume) or volume <= 0.0 or not mesh.is_watertight
        or not mesh.is_winding_consistent or not mesh.is_volume):
      raise ValueError(f"{name}の入力 STL が閉体/向き/正体積検査に失敗")
  return foot, tibia


def teardrop_y(length, diameter):
  """+Z側の天井を45°にする横穴。円形部の必要クリアランスを狭めない。"""
  r = diameter / 2
  profile = [(-r / math.sqrt(2), r / math.sqrt(2)),
        (r / math.sqrt(2), r / math.sqrt(2)), (0, r * math.sqrt(2))]
  roof = Manifold.hull_points([[x, y, z] for x, z in profile
                for y in (-length / 2, length / 2)])
  return cyl_y(length, diameter) + roof


def flex_webs(shoe, angle_deg, *, bend_length=15.0, ear_y=16.7):
  """根元15mmを一定曲率で外へ開く取付経路モデル（材料特性の保証ではない）。"""
  angle = math.radians(angle_deg)
  result = []
  for sign in (-1, 1):
    web = shoe ^ box(160, 100, 160).translate([0, sign * 50, 80])
    if angle > 0:
      mesh = to_trimesh(web)
      v = mesh.vertices.copy()
      zz = v[:,2]
      normal_offset = sign*v[:,1] - ear_y
      arc_angle = np.minimum(zz/bend_length, 1)*angle
      radius = bend_length/angle
      tail = np.maximum(zz-bend_length, 0)
      out_y = ear_y + radius*(1-np.cos(arc_angle)) + tail*np.sin(angle)
      out_z = radius*np.sin(arc_angle) + tail*np.cos(angle)
      v[:,1] = sign*(out_y + normal_offset*np.cos(arc_angle))
      v[:,2] = out_z - normal_offset*np.sin(arc_angle)
      mesh.vertices = v
      web = as_manifold(mesh)
    result.append(web)
  return result


def make_feet(*, clearance=0.3, seat_gap=0.1, sole_radius=19.0, sole_depth=14.0,
       ear_inner_y=15.2, ear_thickness=3.0, ear_width=22.0,
       bolt_z=35.0, ear_top_z=46.0, bore_d=8.4, counterbore_d=12.4,
       counterbore_floor_y=16.2, spacer_inner_y=8.05,
       spacer_barrel_d=8.0, spacer_flange_d=12.0,
       spacer_flange_t=1.2, screw_clearance_d=3.4,
       sweep_height=65.0):
  """戻り値 (固体辞書, 寸法, 入力mesh)。靴は一閉体、スペーサーは左右同形。"""
  values = locals().copy()
  if not all(math.isfinite(float(v)) and v > 0 for v in values.values()):
    raise ValueError("全寸法は有限な正数で指定")
  if not (spacer_inner_y < ear_inner_y < counterbore_floor_y <
      counterbore_floor_y + spacer_flange_t < ear_inner_y + ear_thickness):
    raise ValueError("スペーサーと耳の段差順序が不正")
  if not (screw_clearance_d < spacer_barrel_d < bore_d <
      spacer_flange_d < counterbore_d < ear_width):
    raise ValueError("ボア/段付きスペーサーの径順序が不正")
  if abs(ear_top_z - bolt_z - ear_width/2) > 1e-6:
    raise ValueError("耳上端はボルト高さ+耳半径に一致させる")
  foot, tibia = source_parts()
  fm = as_manifold(foot)
  # 三葉の外形。旧トゥの細い差込根元を荷重部材として再使用しない。
  body = cyl(sole_depth, sole_radius * 2).translate([0, 0, -sole_depth / 2])
  for angle in (-90.0, 30.0, 150.0):
    a = math.radians(angle)
    # 底を平面に揃える。楕円体の下端は急な張り出しとなりTPU支持材が必要。
    toe = cyl(sole_depth, 2).scale([12.0, 8.0, 1.0])
    toe = toe.rotate([0, 0, angle]).translate(
      [18.0 * math.cos(a), 18.0 * math.sin(a), -sole_depth / 2])
    body += toe
  # 柱を青い脛殻の後方へ寄せる。殻と重なる領域はボルト付近の耳へ限定。
  for sign in (-1, 1):
    y = sign * (ear_inner_y + ear_thickness / 2)
    body += box(12, ear_thickness, bolt_z + sole_depth).translate(
      [-12, y, (bolt_z - sole_depth) / 2])
    body += cyl_y(ear_thickness, ear_width).translate([0, y, bolt_z])
    # 柱から耳へ45°で広げ、耳下面の浮いた開始層をなくす。
    profile = [(-18, 10), (-6, 10), (11, 27), (11, bolt_z), (-18, bolt_z)]
    body += Manifold.hull_points([[x, yy, z] for x, z in profile
                   for yy in (y - ear_thickness / 2, y + ear_thickness / 2)])
  # 正確な上方向の移動包絡。甲の下面に沿う受け座と上開きの挿入路を同時に作る。
  # 離散姿勢の和集合ではなく連続の Minkowski 和なので格子間の衝突を残さない。
  insertion = fm.minkowski_sum(box(2 * clearance, 2 * clearance,
                   sweep_height + seat_gap).translate(
                     [0, 0, (sweep_height - seat_gap) / 2]))
  body -= insertion
  body -= teardrop_y(60, bore_d).translate([0, 0, bolt_z])
  for sign in (-1, 1):
    body -= teardrop_y(20, counterbore_d).translate(
      [0, sign * (counterbore_floor_y + 10), bolt_z])
  # 頭とナットの力は段付き PLA の端面→脛の中央リブへ渡す。
  length = counterbore_floor_y - spacer_inner_y
  spacer = cyl_y(length, spacer_barrel_d).translate(
    [0, (spacer_inner_y + counterbore_floor_y) / 2, bolt_z])
  spacer += cyl_y(spacer_flange_t, spacer_flange_d).translate(
    [0, counterbore_floor_y + spacer_flange_t / 2, bolt_z])
  spacer -= cyl_y(50, screw_clearance_d).translate([0, 0, bolt_z])
  dims = dict(values)
  dims.update(frame="leg_foot_bored local, mm, +Z=tibia",
        screw_grip_mm=2 * (counterbore_floor_y + spacer_flange_t),
        hardware_per_leg={"M3x40_pan_machine_screw": 2, "M3_nylon_lock_nut": 2},
        removed_and_saved=["Leg_Toe_Black_x12: 3/leg", "foot_pad: 1/leg"],
        shin_shell_policy="既存殻を保持。上下ボルト座面周囲を局所的に逃がした別名STL/切除工具を参照し、全長・外形を保持。",
        leg_count=4, shoe_count=4, spacer_count=16, upper_bolt_z=75.0)
  return {"tpu_shoe": body.simplify(0.001), "pla_spacer_positive_y": spacer}, dims, (foot, tibia)


def atomic_export(solid, path, *, printable=True):
  path = Path(path)
  mesh = to_trimesh(solid)
  payload = mesh.export(file_type="stl")
  import io
  saved = trimesh.load(io.BytesIO(payload), file_type="stl", force="mesh")
  if (not saved.is_watertight or not saved.is_winding_consistent or
      not np.isfinite(saved.vertices).all() or saved.volume <= 0 or
      not np.isclose(saved.volume, solid.volume(), atol=0.02, rtol=2e-5)):
    raise ValueError(f"STL往復検査に失敗: {path}")
  components = len(solid.decompose())
  if printable and components != 1:
    raise ValueError(f"1つの実体へ繋がっていない: {path}")
  path.parent.mkdir(parents=True, exist_ok=True)
  fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
  try:
    with os.fdopen(fd, "wb") as f:
      f.write(payload)
    os.replace(tmp, path)
  finally:
    if os.path.exists(tmp):
      os.unlink(tmp)
  return {"path": str(path.relative_to(ROOT)), "sha256": hashlib.sha256(payload).hexdigest(),
      "exists": True,
      "volume_mm3": float(saved.volume), "bounds_mm": saved.bounds.tolist(),
      "watertight": True, "solid_components": components,
      "role": "printable_or_assembly" if printable else "DO_NOT_PRINT_boolean_tool"}


def generate(output=DEFAULT_OUTPUT, **kwargs):
  candidate = Path(output)
  if ".." in candidate.parts or "\x00" in str(candidate) or "\\" in str(candidate):
    raise ValueError("print-first feet output must be a canonical repository path")
  if candidate.is_symlink():
    raise ValueError("print-first feet output symlink is not permitted")
  if not candidate.is_absolute():
    candidate = ROOT / candidate
  output = candidate.resolve(strict=False)
  if not output.is_relative_to(ROOT) or not output.is_relative_to(DEFAULT_OUTPUT.resolve()):
    raise ValueError(f"出力は保護された専用フォルダ内に限定: {DEFAULT_OUTPUT}")
  solids, dims, _ = make_feet(**kwargs)
  outputs = {}
  for name, solid in solids.items():
    outputs[name] = atomic_export(solid, output / (name + "_foot_frame.stl"))
  # 負Y側は proper rotation (Z180°)、左右の反射や裏返しは不要。
  outputs["pla_spacer_negative_y"] = atomic_export(
    solids["pla_spacer_positive_y"].rotate([0, 0, 180]),
    output / "pla_spacer_negative_y_foot_frame.stl")
  for name, sign in (("pla_spacer_upper_positive_y",1),("pla_spacer_upper_negative_y",-1)):
    upper = solids["pla_spacer_positive_y"].translate([0,0,dims["upper_bolt_z"]-dims["bolt_z"]])
    if sign < 0: upper = upper.rotate([0,0,180])
    outputs[name] = atomic_export(upper, output/(name+"_foot_frame.stl"))
  # 青い殻の107mm全長を外さず、ボルト耳/スペーサーの周りだけを逃がす。
  # 既存の印刷済み殻と本流STLは保存。鏡像も実STLから個別に作る。
  adapter = solids["tpu_shoe"] + solids["pla_spacer_positive_y"]
  adapter += solids["pla_spacer_positive_y"].rotate([0, 0, 180])
  # 先に脛と青殻を組み、TPUの耳を開いて装着する。耳を閉じる連続経路も殻から逃がす。
  # 上部の最大回転半径<50mm、0.5°格子の半間隔移動<0.219mmを0.3mm膨張で包絡。
  swept_vertices = [[],[]]
  for angle in np.arange(0, 30.01, .5):
    for i, web in enumerate(flex_webs(solids["tpu_shoe"], float(angle))):
      swept_vertices[i].append(np.asarray(web.to_mesh().vert_properties)[:,:3])
  for vertices in swept_vertices:
    # 切削工具には凸包による余裕を許す。靴/接触判定は元の非凸実体を維持する。
    adapter += Manifold.hull_points(np.vstack(vertices))
  tool = adapter.minkowski_sum(box(.6, .6, .6))
  # 頭/ナットと工具がスペーサーの端面へ到達するY方向の通路。
  for sign in (-1, 1):
    tool += cyl_y(24, 8).translate([0, sign * (17.4 + 12), dims["bolt_z"]])
  # 元の上側座面幅は約51mmでM3×40が届かない。同じ段付きスペーサーで座面を34.8mmへ。
  tool += cyl_y(100,dims["bore_d"]).translate([0,0,dims["upper_bolt_z"]])
  for sign in (-1,1):
    tool += cyl_y(30,dims["counterbore_d"]).translate(
      [0,sign*(dims["counterbore_floor_y"]+15),dims["upper_bolt_z"]])
  # 負工具は印刷部品ではないためSTLを配布しない。再現は本コードのtoolそのもの。
  for suffix in ("", "_m"):
    sh = trimesh.load(ROOT / f"hardware/stl/shin_shell{suffix}.stl", force="mesh")
    sh.apply_transform(trimesh.transformations.rotation_matrix(np.pi, [1, 0, 0]))
    sh.apply_translation([0, 0, C.TIBIA_LEN - 16])
    fitted = (as_manifold(sh) - tool).simplify(.001)
    outputs[f"shin_shell_retained{suffix}"] = atomic_export(fitted, output / f"shin_shell_retained{suffix}_foot_frame.stl")
    # 元の印刷姿勢へ戻す。新たな向き推定はしない。
    pr = fitted.translate([0, 0, -(C.TIBIA_LEN - 16)]).rotate([180, 0, 0])
    outputs[f"shin_shell_retained{suffix}_print"] = atomic_export(pr, output / f"shin_shell_retained{suffix}_print.stl")
  # 実際にスライサへ渡す配置。座標を戻す行列も明記する。
  shoe_print = solids["tpu_shoe"].translate([0, 0, dims["sole_depth"]])
  outputs["tpu_shoe_print"] = atomic_export(shoe_print, output / "tpu_shoe_print.stl")
  # 正Yスペーサーは外側のフランジ面を床に、筒穴を縦にする。
  end = dims["counterbore_floor_y"] + dims["spacer_flange_t"]
  spacer_print = solids["pla_spacer_positive_y"].translate([0, -end, -dims["bolt_z"]]).rotate([-90, 0, 0])
  outputs["pla_spacer_print"] = atomic_export(spacer_print, output / "pla_spacer_print.stl")
  t = np.eye(4); t[2, 3] = -dims["sole_depth"]
  # print = Rx(-90) @ (foot - [0,end,bolt_z]); foot = Rx(+90) @ print + offset。
  s = trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0])
  s[:3, 3] = [0, end, dims["bolt_z"]]
  source_paths = [Path(__file__).resolve(), ROOT / "hardware/src/config.py",
          ROOT / "hardware/stl/leg_foot_bored.stl", ROOT / "hardware/stl/tibia_link.stl",
          ROOT / "hardware/stl/shin_shell.stl", ROOT / "hardware/stl/shin_shell_m.stl"]
  source_rows = [{"path": str(p.relative_to(ROOT)),
          "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
          "exists": True} for p in source_paths]
  source_hashes = {row["path"]: row["sha256"] for row in source_rows}
  generator_source = str(Path(__file__).resolve().relative_to(ROOT))
  generator_sha = source_hashes[generator_source]
  data = {"status": "BENCH_CANDIDATE: CAD検査と実物試験を区別する", "dimensions": dims,
      "outputs": outputs, "print_to_foot_frame": {"tpu_shoe": t.tolist(), "pla_spacer_positive_y": s.tolist()},
      "install_to_tibia_link": trimesh.transformations.translation_matrix([0, 0, -C.TIBIA_LEN]).tolist(),
      "sources": source_rows, "source_sha256": source_hashes}
  data["parts"] = [
    {"name": "tpu_shoe", "stl": outputs["tpu_shoe"]["path"],
     "exists": True, "sha256": outputs["tpu_shoe"]["sha256"],
     "source": generator_source, "source_sha256": generator_sha,
     "transform": np.eye(4).tolist(), "color": "#20252a", "material": "TPU"},
    *[{"name": name, "stl": outputs[name]["path"], "exists": True,
      "sha256": outputs[name]["sha256"], "source": generator_source,
      "source_sha256": generator_sha,
      "transform": np.eye(4).tolist(), "color": "#888888", "material": "PLA"}
     for name in ("pla_spacer_positive_y", "pla_spacer_negative_y",
            "pla_spacer_upper_positive_y", "pla_spacer_upper_negative_y")],
    *[{"name": name, "stl": outputs[name]["path"], "exists": True,
      "sha256": outputs[name]["sha256"], "source": generator_source,
      "source_sha256": generator_sha,
      "transform": np.eye(4).tolist(), "color": "#248aca", "material": "PLA", "legs": legs}
     for name, legs in (("shin_shell_retained", ["FL", "RR"]), ("shin_shell_retained_m", ["FR", "RL"]))]]
  data["replace_prefixes"] = ["foot_pad", "Leg_Toe_Black_x12", "shin_shell"]
  data["insertion_flex"] = {"open_angle_deg":30,"bend_length_mm":15,"ear_center_abs_y_mm":16.7,
               "maximum_nominal_fibre_strain":3*math.radians(30)/(2*15),
               "case_relief_angular_step_deg":.5,"case_relief_clearance_mm":.3,
               "limitation":"一定曲率による経路計算。実TPU/積層で5.24%の曲げひずみを許容できるか試験する。"}
  (output / "assembly.json").write_text(
    json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
  return data


def main():
  p = argparse.ArgumentParser(description=__doc__)
  p.add_argument("--output", type=Path, default=None)
  p.add_argument("--sole-radius", type=float, default=19.0)
  p.add_argument("--sole-depth", type=float, default=14.0)
  args = p.parse_args()
  output = DEFAULT_OUTPUT
  if args.output is not None:
    if args.output.is_absolute() or ".." in args.output.parts:
      p.error("--output must be a repository-relative path")
    output = ROOT / args.output
  data = generate(output, sole_radius=args.sole_radius, sole_depth=args.sole_depth)
  print(json.dumps({k: {"volume_mm3": v["volume_mm3"], "bounds_mm": v["bounds_mm"]}
           for k, v in data["outputs"].items()}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
  main()
