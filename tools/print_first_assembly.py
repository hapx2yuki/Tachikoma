#!/usr/bin/env python3
"""既存候補を使う印刷改修構成。旧設計を保持した明示的な別構成。

寸法は config.PRINT_FIRST。context() は新しいPythonプロセスで使う。
設計候補の占有と慣性を同じ配置で出力し、存在しない収納を仮定しない。
"""
from contextlib import contextmanager
from pathlib import Path
from pathlib import PurePosixPath
import hashlib
import json
import sys
import numpy as np
import trimesh
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'hardware/src'),str(ROOT/'tools')]
import config as C
import export_urdf as E
import make_chassis as MC
import make_print_first_leg as PL
import print_first_components as PC
import xiao_retention_plan as XR
OUT=ROOT/'outputs/print-first-20260905/body'
LEGS_OUT=ROOT/'outputs/print-first-20260905/legs'
FEET_OUT=ROOT/'outputs/print-first-20260905/feet'
# 接着のみの装飾で干渉があるものは初期歩行で保管する。機能部はここに含めない。
STORED_PREFIXES=(
  'Head_TailJoint_', 'Head_Insert_', 'Head_Screw_', 'Cabin_Eye_', 'Cabin_Turret_Peg',
  'Cabin_RedLight_', 'Cabin_Spinnarette_', 'Cabin_Front_Insert_',
  'Leg_Thigh_Guard_', 'Leg_Shin_Guard_', 'thigh_cap', 'Arm_Right_Guard_Grey',
  # Decorative mouth peg is retained as a stored kit part. It is not a
  # structural fastener and must not be used to hide the speaker collision.
  'Mouth_Peg_Grey',
)
# ``Cabin_`` is an explicit initial-walking storage rule.  Keep this separate
# from STORED_PREFIXES: the latter is the legacy decorative filter, while this
# prefix records the complete source-part boundary adopted for print-first.
CABIN_STORAGE_RUNTIME={}
COMPONENTS=C.PRINT_FIRST_COMPONENTS

XIAO_PLAN=ROOT/'docs/audits/20260905-round2/xiao-retention-plan.json'


def _cabin_storage_prefixes():
  """Return the configured Cabin source boundary as a validated tuple."""
  raw=C.PRINT_FIRST_CABIN_STORAGE.get('stored_source_prefixes',())
  if not isinstance(raw,(tuple,list)) or not raw or any(
      not isinstance(item,str) or not item for item in raw):
    raise ValueError('PRINT_FIRST_CABIN_STORAGE.stored_source_prefixes is invalid')
  return tuple(raw)


def _matches_prefixes(name, prefixes):
  return isinstance(name,str) and name.startswith(tuple(prefixes))


def _mass_rows(rows):
  """Compute the same estimated mass used by the active URDF context."""
  result=[]
  for link,mesh,color,name in rows:
    item=E.part_mass_item(mesh,name)
    result.append({
      'link':link,
      'name':name,
      'mass_g':float(item.mass_kg*1000.0),
    })
  return result


def cabin_storage_policy_record():
  """Return config plus the current collected-part accounting.

  The runtime section is populated inside ``context`` before its filter is
  applied.  This makes the manifest state which Cabin rows were actually
  removed and avoids hard-coding the old 18.655951 g subtotal.
  """
  policy=dict(C.PRINT_FIRST_CABIN_STORAGE)
  for key,value in tuple(policy.items()):
    if isinstance(value,tuple):
      policy[key]=list(value)
  policy['runtime']=dict(CABIN_STORAGE_RUNTIME) if CABIN_STORAGE_RUNTIME else {
    'status':'NOT_COLLECTED',
  }
  return policy


def _xiao_z_lifted_spec(spec, dz: float):
  """基板トレーだけを camera-local +Z へ移した仕様へ更新する。

  `xiao_retention_plan.py` は保存済み測定値の基準資料として保持する。
  印刷優先構成で必要な再配置はここでだけ適用し、固定カメラ子基板/レンズは
  元の camera-local 座標に留める。FPC はカメラ端を固定し、基板側の
  connector 端だけを同じ移動量で更新する。
  """
  dz=float(dz)
  if not np.isfinite(dz):
    raise ValueError('invalid XIAO holder z offset')
  inputs=spec.get('inputs',{})
  relative=spec.get('relative_frames',{})
  occupancy=spec.get('occupancy',{})
  board_to_camera=np.asarray(relative.get('board_to_camera'),dtype=float)
  if board_to_camera.shape != (4,4) or not np.isfinite(board_to_camera).all():
    raise ValueError('XIAO board_to_camera must be a finite 4x4 matrix')
  start=np.asarray(relative.get('fpc_start_camera_mm'),dtype=float)
  connector=np.asarray(relative.get('connector_center_camera_mm'),dtype=float)
  if start.shape != (3,) or connector.shape != (3,):
    raise ValueError('XIAO FPC endpoints must be three-vectors')
  if not np.isfinite(start).all() or not np.isfinite(connector).all():
    raise ValueError('XIAO FPC endpoints must be finite')

  lift=np.eye(4)
  lift[2,3]=dz
  board_to_camera_lifted=lift @ board_to_camera
  connector_lifted=(lift @ np.r_[connector,1.0])[:3]
  relative['board_to_camera']=board_to_camera_lifted.astype(float).tolist()
  relative['connector_center_camera_mm']=connector_lifted.astype(float).tolist()

  base_frame=inputs.get('base_camera_frame')
  if base_frame is not None:
    base=np.asarray(base_frame,dtype=float)
    if base.shape != (4,4) or not np.isfinite(base).all():
      raise ValueError('XIAO base_camera_frame must be a finite 4x4 matrix')
    relative['board_to_base']=(base @ board_to_camera_lifted).astype(float).tolist()
    relative['fpc_start_base_mm']=(base @ np.r_[start,1.0])[:3].astype(float).tolist()
    relative['connector_center_base_mm']=(base @ np.r_[connector_lifted,1.0])[:3].astype(float).tolist()
    occupancy['xiao_all_boards']['transform_base']=(base @ board_to_camera_lifted).astype(float).tolist()
  occupancy['xiao_all_boards']['transform_camera']=board_to_camera_lifted.astype(float).tolist()

  free_length=float(inputs.get('fpc_free_length_mm'))
  if not np.isfinite(free_length) or free_length <= 0.0:
    raise ValueError('XIAO FPC free length must be positive')
  before_distance=float(np.linalg.norm(connector-start))
  after_distance=float(np.linalg.norm(connector_lifted-start))
  spec['fpc_integration']={
    'camera_endpoint_fixed':True,
    'board_connector_endpoint_lift_camera_z_mm':dz,
    'fpc_start_camera_mm':start.astype(float).tolist(),
    'connector_center_camera_mm':connector_lifted.astype(float).tolist(),
    'straight_line_distance_before_lift_mm':before_distance,
    'straight_line_distance_after_lift_mm':after_distance,
    'free_length_mm':free_length,
    'straight_line_reserve_after_lift_mm':free_length-after_distance,
    'straight_line_geometric_fit_after_lift':bool(free_length >= after_distance),
    'physical_fit_status':'UNVERIFIED_FPC_BEND_RADIUS_CONNECTOR_AND_STRAIN_RELIEF',
  }
  spec['holder_z_offset_applied_in_camera_local_mm']=dz
  return spec


def _xiao_holder_spec():
  """保存済み計測値を現在の camera_mount_frame へ一度だけ再投影する。

  ``xiao-retention-plan.json`` に保存された base frame は通常構成の参照値
  なので、そのまま使わない。最終 print-first context のフレームを毎回
  渡して、基板・FPC・保持台の座標を同じ変換経路から生成する。
  """
  if not XIAO_PLAN.is_file():
    raise FileNotFoundError(f'XIAO retention plan is missing: {XIAO_PLAN}')
  plan=json.loads(XIAO_PLAN.read_text(encoding='utf-8'))
  xcfg=C.PRINT_FIRST_XIAO
  variant=xcfg['variant']
  try:
    source=plan['retained_hardware'][variant]['holder_spec']
    inputs=source['inputs']
  except (KeyError,TypeError) as exc:
    raise ValueError(f'XIAO retention plan has no holder variant {variant!r}') from exc
  spec=XR.build_holder_spec(
    board_size_mm=inputs['board_size_mm'],
    board_frame_flat_to_chassis=inputs['board_frame_flat_to_chassis'],
    camera_frame_chassis=inputs['camera_frame_chassis'],
    fpc_start_mm=inputs['fpc_start_mm'],
    connector_center_mm=inputs['connector_center_mm'],
    fpc_free_length_mm=inputs['fpc_free_length_mm'],
    base_camera_frame=E.camera_mount_frame({}),
    camera_child_envelope_size_mm=inputs['camera_child_envelope_size_mm'],
    camera_child_center_camera_mm=inputs['camera_child_center_camera_mm'],
    clearance_each_side_mm=xcfg['clearance_each_side_mm'],
    wall_mm=xcfg['wall_mm'],
    short_rib_height_mm=xcfg['short_rib_height_mm'],
    board_design_mass_g=xcfg['board_design_mass_g'],
    camera_design_mass_g=xcfg['camera_design_mass_g'],
  )
  # The saved helper describes the measured baseline. Lift only the board
  # tray/occupancy and the board-side FPC endpoint for this print-first
  # candidate; the camera child/lens remains on the existing carrier.
  return _xiao_z_lifted_spec(
    spec, float(xcfg.get('holder_z_offset_mm',0.0)))


def _xiao_strap_slot_cutters(spec):
  """基板局所座標の結束バンド貫通スロットをcamera座標へ置く。"""
  inputs=spec['inputs']; xcfg=C.PRINT_FIRST_XIAO
  size=np.asarray(inputs['board_size_mm'],dtype=float)
  board_to_camera=np.asarray(spec['relative_frames']['board_to_camera'],dtype=float)
  wall=float(inputs['wall_mm'])
  width=float(xcfg['strap_slot_width_mm'])
  length=float(xcfg['strap_slot_length_mm'])
  ys=tuple(float(v) for v in xcfg['strap_slot_y_mm'])
  if (len(ys)!=int(xcfg['strap_slot_count']) or width<=0 or length<=0
      or width>=size[0] or any(abs(y)+length/2.0>=size[1]/2.0 for y in ys)):
    raise ValueError('invalid XIAO strap slot dimensions/locations')
  floor_center_z=-size[2]/2.0-wall/2.0
  cutters=[]
  for y in ys:
    cutter=trimesh.creation.box((width,length,wall+2.0))
    cutter.apply_translation([0.0,y,floor_center_z])
    cutter.apply_transform(board_to_camera)
    cutters.append(cutter)
  return cutters


def _xiao_add_strap_slots(meshes,spec):
  """床と一体化候補へ、実体の貫通スロットを追加する。"""
  cutters=_xiao_strap_slot_cutters(spec)
  for key in ('xiao_tray_floor_candidate','xiao_tray_holder3_union_candidate'):
    current=meshes[key]
    for cutter in cutters:
      result=trimesh.boolean.difference([current,cutter],engine='manifold')
      if result is None or not isinstance(result,trimesh.Trimesh):
        raise ValueError(f'XIAO strap slot boolean failed: {key}')
      current=result
    current.remove_unreferenced_vertices()
    if (not current.is_watertight or not current.is_winding_consistent
        or not np.isfinite(current.volume) or current.volume<=0
        or len(current.split(only_watertight=False))!=1):
      raise ValueError(f'XIAO strap slot made invalid solid: {key}')
    meshes[key]=current
  return meshes


def xiao_holder_meshes():
  """XIAO基板包絡と保持台候補を eye_pod_camera 座標で返す。"""
  # `_xiao_holder_spec()` has already applied the camera-local lift to the
  # board frame. The child camera/lens mesh is deliberately untouched.
  spec=_xiao_holder_spec()
  return _xiao_add_strap_slots(XR.make_holder_meshes(spec),spec)


def xiao_retention_summary(meshes=None):
  """スロット位置・基板非交差・サービス側の候補判定を台帳用に返す。"""
  spec=_xiao_holder_spec(); meshes=xiao_holder_meshes() if meshes is None else meshes
  cutters=_xiao_strap_slot_cutters(spec)
  board=meshes['xiao_all_boards_occupancy']
  floor=meshes['xiao_tray_floor_candidate']
  union=meshes['xiao_tray_holder3_union_candidate']
  intersection=XR._intersection_volume_mm3(board,union)
  floor_intersection=XR._intersection_volume_mm3(board,floor)
  xcfg=C.PRINT_FIRST_XIAO
  slot_rows=[]
  for index,(y,cutter) in enumerate(zip(xcfg['strap_slot_y_mm'],cutters)):
    slot_rows.append({
      'index':index+1,'board_local_center_y_mm':float(y),
      'width_mm':float(xcfg['strap_slot_width_mm']),
      'length_mm':float(xcfg['strap_slot_length_mm']),
      'camera_bounds_mm':cutter.bounds.astype(float).tolist(),
    })
  holder_ok=(floor.is_watertight and floor.is_winding_consistent
        and union.is_watertight and union.is_winding_consistent
        and np.isfinite(floor.volume) and floor.volume>0
        and np.isfinite(union.volume) and union.volume>0
        and len(union.split(only_watertight=False))==1)
  board_ok=(np.isfinite(intersection) and np.isfinite(floor_intersection)
       and intersection>=0.0 and floor_intersection>=0.0
       and intersection<=1e-3 and floor_intersection<=1e-3)
  return {
    'retention_method':'one_existing_nonconductive_tie_through_two_floor_slots',
    'standalone_helper_mesh_status':'UNSLOTTED_BASELINE_HELPER_OUTPUT',
    'final_slot_integration_owner':'tools/print_first_assembly.py::_xiao_add_strap_slots',
    'final_slot_application_count':1,
    'slot_application_rule':'helper floor/rib geometry is consumed as a baseline; final carrier floor/holder union is cut once here, and the helper STL/union is not counted alongside it',
    'strap_count':int(xcfg['strap_count']),
    'slot_count':len(cutters),
    'strap_width_candidate_mm':float(xcfg['strap_width_candidate_mm']),
    'strap_width_status':'UNVERIFIED_EXISTING_TIE_DIMENSION',
    'slots':slot_rows,
    'floor_watertight':bool(floor.is_watertight),
    'holder_union_watertight':bool(union.is_watertight),
    'holder_union_components':len(union.split(only_watertight=False)),
    'board_intersection_with_holder_union_mm3':float(intersection),
    'board_intersection_with_slotted_floor_mm3':float(floor_intersection),
    'board_nonintersection_status':'PASS' if holder_ok and board_ok else 'FAIL',
    'usb_sd_service_side_status':'UNVERIFIED_PURCHASED_PORT_SIDE_AND_PLUG_ENVELOPE',
    'fpc_service_clearance_status':'UNVERIFIED_FPC_LOCK_AND_BEND_ROUTE',
    'candidate_service_rule':'slots remain inside board footprint; both board-local Y sides remain open',
    'physical_retention_status':'UNVERIFIED_ONE_UNIT_TIE_AND_PULL_TEST_REQUIRED',
  }


def xiao_fpc_summary():
  """最終候補のFPC直線長と未確認境界を返す。"""
  return dict(_xiao_holder_spec().get('fpc_integration',{}))


def xiao_occupancy_meshes():
  """実基板/カメラの占有候補だけを同じ camera ローカル座標で返す。"""
  meshes=xiao_holder_meshes()
  return [
    (meshes['xiao_all_boards_occupancy'], '#d97706',
     'xiao_all_boards_occupancy'),
    (meshes['camera_child_lens_occupancy'], '#16a34a',
     'camera_child_lens_occupancy'),
  ]


def xiao_connection_beams():
  """保持台を既存 camera carrier へつなぐ印刷梁を返す。

  梁は候補の床と carrier の両方へ重ね、後段で単一 STL へ結合する。
  座標・大きさは config.PRINT_FIRST_XIAO にあり、買い増し部品を要求しない。
  """
  xcfg=C.PRINT_FIRST_XIAO
  width=float(xcfg['connection_beam_width_mm'])
  depth=float(xcfg['connection_beam_depth_mm'])
  z0=float(xcfg['connection_beam_bottom_z_mm'])
  z1=float(xcfg['connection_beam_top_z_mm'])
  if not z1>z0 or width<=0 or depth<=0:
    raise ValueError('invalid XIAO connection beam dimensions')
  y=float(xcfg['connection_beam_y_mm'])
  return [
    trimesh.creation.box((width, depth, z1-z0)).apply_translation(
      [float(x), y, (z0+z1)/2.0])
    for x in xcfg['connection_beam_x_mm']
  ]


def _mass_item_from_mesh(mesh, mass_g, label):
  """既知の設計質量を実メッシュの COM/慣性へ割り当てる (未実測)。"""
  mesh=E._ensure_outward(mesh)
  scaled=mesh.copy();scaled.apply_scale(E.MM)
  mp=scaled.mass_properties
  if not np.isfinite(mp['mass']) or mp['mass']<=0:
    raise ValueError(f'{label}: mass geometry has non-positive volume')
  mass_kg=float(mass_g)/1000.0
  scale=mass_kg/float(mp['mass'])
  return E.MassItem(mass_kg,np.asarray(mp['center_mass'],dtype=float),
           np.asarray(mp['inertia'],dtype=float)*scale,label,False)

def component_meshes():
  """棚上電装と音声/電池候補の占有を mass と同じ座標で返す。"""
  rows=[]
  for row in COMPONENTS:
    name=row['name'];size=row['size_mm'];ctr=row['center_zb_mm']
    m=trimesh.creation.box(size)
    m.apply_translation([ctr[0],ctr[1],ctr[2]+E.ZB])
    rows.append((m,'#2b7958','component_'+name))
  battery=C.PRINT_FIRST_BATTERY
  _diag,meshes=PC.get_print_first_component_bundle(
    battery_size_mm=battery['size_mm'],
    battery_mass_g=battery['mass_g'],
    battery_reference_center_mm=battery['center_base_mm'])
  colors={
    'battery_2s_2200mah_candidate':'#355cde',
    'mic_cannon_real_pocket_candidate':'#d97706',
    'speaker_cannon_real_pocket_candidate':'#15803d',
  }
  for name,m in meshes.items():
    rows.append((m,colors.get(name,'#2b7958'),'component_'+name))
  return rows


def foot_parts():
  """靴生成器が用意する assembly.json を、足原点から脛へ一度だけ変換。"""
  path=ROOT/'outputs/print-first-20260905/feet/assembly.json'
  spec=_load_verified_manifest(path, required_names={
    'tpu_shoe', 'pla_spacer_positive_y', 'pla_spacer_negative_y',
    'pla_spacer_upper_positive_y', 'pla_spacer_upper_negative_y',
    'shin_shell_retained', 'shin_shell_retained_m'})
  rows=[]
  for p in spec['parts']:
    m=_load_manifest_mesh(p)
    m.apply_transform(np.asarray(p.get('transform',np.eye(4))))
    m.apply_translation([0,0,-C.TIBIA_LEN])
    rows.append((m,p.get('color','#333333'),p['name'],p.get('legs',E.LEGS)))
  return rows,spec.get('replace_prefixes',['foot_pad','Leg_Toe_Black_x12'])


def _load_manifest_mesh(row):
  """台帳のハッシュと閉体を確認してから STL を読む。

  print-first の最終入口では欠損・改変・壊れた STL を旧部品へ戻して
  続行しない。候補生成の途中だけは生成器自身を ``generated=False`` で
  実行できるため、試作と採用構成の境界も明示できる。
  """
  raw = row.get('stl', row.get('path'))
  if not isinstance(raw, str) or not raw:
    raise ValueError(f"manifest row {row.get('name')!r} has no STL path")
  public_path = PurePosixPath(raw)
  if ("\\" in raw or raw.startswith("~") or raw.startswith("$OUTPUT")
      or public_path.is_absolute() or ".." in public_path.parts
      or "." in public_path.parts or public_path.as_posix() != raw):
    raise ValueError(f"manifest row {row.get('name')!r} has an invalid repository path: {raw!r}")
  path=ROOT / raw
  if path.is_symlink() or not path.resolve(strict=False).is_relative_to(ROOT):
    raise ValueError(f"manifest row {row.get('name')!r} escapes repository: {raw!r}")
  if not path.is_file():
    raise FileNotFoundError(f"print-first required STL is missing: {path}")
  expected=row.get('sha256')
  if (not isinstance(expected, str) or len(expected) != 64
      or any(c not in '0123456789abcdef' for c in expected)):
    raise ValueError(f"print-first STL hash is missing: {path}")
  if row.get('exists') is not True:
    raise ValueError(f"print-first STL exists marker is missing: {path}")
  actual_hash=hashlib.sha256(path.read_bytes()).hexdigest()
  if expected != actual_hash:
    raise ValueError(f"print-first STL hash mismatch: {path}")
  mesh=trimesh.load(path,force='mesh')
  if not isinstance(mesh,trimesh.Trimesh) or len(mesh.vertices)<4 or len(mesh.faces)<4:
    raise ValueError(f"print-first STL is empty: {path}")
  if not np.isfinite(mesh.vertices).all() or not np.isfinite(mesh.faces).all():
    raise ValueError(f"print-first STL has non-finite data: {path}")
  if not mesh.is_watertight or not mesh.is_winding_consistent:
    raise ValueError(f"print-first STL is not a closed consistently wound solid: {path}")
  if not np.isfinite(mesh.volume) or mesh.volume <= 0:
    raise ValueError(f"print-first STL has non-positive volume: {path}")
  return mesh


def _load_verified_manifest(path, *, required_names):
  """読み込み対象と数量を固定し、別構成への黙った fallback を防ぐ。"""
  path=Path(path)
  if not path.is_file():
    raise FileNotFoundError(f"print-first required manifest is missing: {path}")
  try:
    spec=json.loads(path.read_text(encoding='utf-8'))
  except Exception as exc:
    raise ValueError(f"print-first manifest is not valid JSON: {path}: {exc}") from exc
  rows=spec.get('parts')
  if not isinstance(rows,list) or not rows:
    raise ValueError(f"print-first manifest has no parts: {path}")
  names=[row.get('name') for row in rows]
  if any(not isinstance(name,str) or not name for name in names):
    raise ValueError(f"print-first manifest has unnamed part: {path}")
  if len(set(names)) != len(names):
    raise ValueError(f"print-first manifest has duplicate part names: {path}")
  missing=set(required_names)-set(names)
  if missing:
    raise ValueError(f"print-first manifest missing required parts {sorted(missing)}: {path}")
  for row in rows:
    _load_manifest_mesh(row)
  return spec


def _print_first_manifests():
  """全ての採用候補を検査し、脚の左右/関節数を固定する。"""
  body=_load_verified_manifest(OUT/'assembly.json', required_names={
    'pf_chassis', 'pf_ld220_yaw_cap_fr', 'pf_ld220_yaw_cap_fl',
    'pf_ld220_yaw_cap_rl', 'pf_ld220_yaw_cap_rr',
    'pf_head_top_clearanced', 'pf_camera_carrier',
    'pf_eye_pod_camera_clearanced'})
  legs=_load_verified_manifest(LEGS_OUT/'assembly.json', required_names={
    'pf_coxa_bracket', 'pf_femur_link', 'pf_tibia_link',
    'pf_coxa_bracket_m', 'pf_femur_link_m', 'pf_tibia_link_m',
    'pf_ld220_coxa_cap', 'pf_ld220_femur_cap',
    'pf_ld220_coxa_cap_m', 'pf_ld220_femur_cap_m'})
  feet=_load_verified_manifest(FEET_OUT/'assembly.json', required_names={
    'tpu_shoe', 'pla_spacer_positive_y', 'pla_spacer_negative_y',
    'pla_spacer_upper_positive_y', 'pla_spacer_upper_negative_y',
    'shin_shell_retained', 'shin_shell_retained_m'})
  expected_legs={'FL','FR','RL','RR'}
  seen_links={(row.get('link_kind'),leg)
        for row in legs['parts'] if row.get('role')=='link_structure'
        for leg in row.get('legs',())}
  for leg in expected_legs:
    for kind in ('coxa','femur','tibia'):
      if (kind,leg) not in seen_links:
        raise ValueError(f"print-first legs manifest lacks {leg}/{kind}")
  cap_seen={(row.get('link_kind'),leg)
       for row in legs['parts'] if row.get('role')=='removable_case_cap'
       for leg in row.get('legs',())}
  for leg in expected_legs:
    for kind in ('coxa','femur'):
      if (kind,leg) not in cap_seen:
        raise ValueError(f"print-first cap manifest lacks {leg}/{kind}")
  # These are the old visual names that must disappear when this explicit
  # context is active. The guard is checked again after collection below.
  return body,legs,feet


@contextmanager
def context(generated=True):
  """URDF/物理/検査の全入口が同じ座標・質量・部品集合を使う。"""
  global CABIN_STORAGE_RUNTIME
  cfg=C.PRINT_FIRST
  names=['HIPS','HIP_R','PRINT_FIRST_ACTIVE'];oldC={k:getattr(C,k) for k in names}
  oldM={k:getattr(MC,k) for k in ('HIPS','CASE_ANG')}
  oldE={k:getattr(E,k) for k in ('head_top_frame','collect_all_parts','base_link_electronics_items',
                 'build_link_mass_items','part_material','leg_servo_frames',
                 'leg_servo_items','OUT','MESH_DIR')}
  old_static=E._LINK_T_STATIC.copy()
  old_recorder=E._ACTIVE_PRINT_FIRST_CABIN_STORAGE_RECORDER
  old_runtime=dict(CABIN_STORAGE_RUNTIME)
  # A new context must never inherit a previous collection's mass accounting.
  # The active recorder is therefore invalid until this context's collector
  # has populated it.
  CABIN_STORAGE_RUNTIME.clear()
  C.PRINT_FIRST_ACTIVE=True
  C.HIP_R=cfg['hip_r'];C.HIPS={k:(C.HIP_R*np.cos(np.radians(a)),C.HIP_R*np.sin(np.radians(a))) for k,a in C.LEG_ANGLES.items()}
  MC.HIPS=dict(C.HIPS);MC.CASE_ANG=dict(cfg['yaw_case_angles'])
  # The generated leg CAD and every downstream collision/mass consumer must
  # share the same LD frame. The old functions remain available in oldE only
  # for restoring the legacy context on exit.
  E.leg_servo_frames=PL.print_first_leg_frames
  def print_first_leg_servo_items(leg):
    frames=E.leg_servo_frames(leg)
    return tuple(PL.ld220_mass_item(frame,
           f'leg_{leg.lower()}_{kind}_servo', kind)
           for kind,frame in frames.items())
  E.leg_servo_items=print_first_leg_servo_items
  E.head_top_frame=lambda:E.trans(0,0,cfg['head_top_lift'])@oldE['head_top_frame']()
  E._LINK_T_STATIC['Head_Top_Blue']=E.head_top_frame()
  def material(name):
    if name=='tpu_shoe':return ('TPU',2.4,1.0)
    if name.startswith('pla_spacer'):return ('PLA',2.4,1.0)
    if name.startswith('shin_shell_retained'):return ('PLA',1.4,.08)
    for prefix,spec in sorted(C.PRINT_FIRST_MATERIALS.items(),
                 key=lambda item:len(item[0]),reverse=True):
      if name.startswith(prefix):return spec
    if name.startswith('pf_'):
      raise ValueError(
        f'{name}: print-first part has no explicit PRINT_FIRST_MATERIALS rule')
    return oldE['part_material'](name)
  E.part_material=material
  def collect():
    parts=oldE['collect_all_parts']()
    cabin_prefixes=_cabin_storage_prefixes()
    cabin_rows=[(link,m,c,n) for link,items in parts.items()
                for m,c,n in items if _matches_prefixes(n,cabin_prefixes)]
    mass_rows=_mass_rows(cabin_rows)
    legacy_rows=[row for row in mass_rows
                 if _matches_prefixes(row['name'],STORED_PREFIXES)]
    newly_stored_rows=[row for row in mass_rows
                       if not _matches_prefixes(row['name'],STORED_PREFIXES)]
    total_mass_g=sum(row['mass_g'] for row in mass_rows)
    already_stored_mass_g=sum(row['mass_g'] for row in legacy_rows)
    additional_mass_g=sum(row['mass_g'] for row in newly_stored_rows)
    if not np.isclose(total_mass_g,already_stored_mass_g+additional_mass_g,
                      rtol=0.0,atol=1e-9):
      raise ValueError('Cabin storage mass accounting does not close')
    CABIN_STORAGE_RUNTIME.clear()
    CABIN_STORAGE_RUNTIME.update({
      'collection_mode':'generated_true_assembly' if generated
          else 'generated_false_source_for_body_generator',
      'storage_filter_applied':bool(generated),
      'source_mesh_retained_for_body_generation':not generated,
      'source_part_count':len(mass_rows),
      'source_part_names':[row['name'] for row in mass_rows],
      'already_stored_source_part_names':[row['name'] for row in legacy_rows],
      'newly_stored_source_part_names':[row['name'] for row in newly_stored_rows],
      'source_total_mass_g':float(total_mass_g),
      'already_stored_mass_g':float(already_stored_mass_g),
      'additional_stored_mass_g':float(additional_mass_g),
      'additional_mass_accounted_once':True,
      'mass_rows':mass_rows,
    })
    for m,c,n in parts['base_link']:
      # Head_Top is already transformed by the print-first
      # ``_LINK_T_STATIC['Head_Top_Blue']`` frame above. Translating
      # the mesh again here would raise the shell twice while the
      # camera_mount_frame is raised once, creating a false XIAO/head
      # collision and a wrong neck/head relation.
      if n.startswith('Cabin_Turrent_'):
        m.apply_translation([0.3 if 'Right' in n else -0.3,0,0])
    # In generated=False the existing decorative filter is intentionally
    # unchanged; it leaves Cabin_Front/Back available to the body generator's
    # rail lower-support cut.  Only generated=True adds the complete Cabin
    # boundary.  No pf_* prefix is used here, so integrated
    # rail/electronics supports remain addressable by their names.
    source_store_prefixes=(
      tuple(dict.fromkeys((*STORED_PREFIXES,*cabin_prefixes)))
      if generated else STORED_PREFIXES
    )
    for link,rows in parts.items():
      rows[:]=[(m,c,n) for m,c,n in rows
               if not n.startswith(source_store_prefixes) and n!='pod_neck']
    CABIN_STORAGE_RUNTIME['retained_after_filter_source_part_names']=[
      n for items in parts.values() for _m,_c,n in items
      if _matches_prefixes(n,cabin_prefixes)
    ]
    if generated:
      body_data,leg_data,_feet_data=_print_first_manifests()
      # Remove all legacy structural names first. This is intentionally
      # broader than each manifest's ``replaces`` field: a missing old
      # suffix must never leave a standard link alongside the candidate.
      for leg in E.LEGS:
        for link,suffixes in (
          (f'leg_{leg.lower()}_coxa',('coxa_bracket',)),
          (f'leg_{leg.lower()}_femur',('femur_link',)),
          (f'leg_{leg.lower()}_tibia',('tibia_link',))):
          parts[link][:]=[(m,c,n) for m,c,n in parts[link]
                  if not any(n==s or n.startswith(s+'#') or n.startswith(s+'_')
                        for s in suffixes)]
      # A body row is already expressed in its declared link frame. The
      # two explicit transforms are applied once, and their hashes were
      # checked by _print_first_manifests above.
      for p in body_data['parts']:
        link=p.get('link')
        if link not in parts:
          raise ValueError(f"print-first body row targets unknown link: {link}")
        for old in p.get('replaces',[]):
          parts[link][:]=[(m,c,n) for m,c,n in parts[link] if n != old]
        m=_load_manifest_mesh(p)
        m.apply_transform(np.asarray(p.get('transform',np.eye(4))))
        parts[link].append((m,p.get('color','#286bb1'),p['name']))
      # Each link row declares the legs it serves. No row is copied to a
      # different side by inference; the mirror STL is a separate row.
      for p in leg_data['parts']:
        role=p.get('role')
        if role not in ('link_structure','removable_case_cap'):
          raise ValueError(f"unknown print-first leg row role: {p.get('name')}")
        m=_load_manifest_mesh(p)
        m.apply_transform(np.asarray(p.get('transform',np.eye(4))))
        if role=='link_structure':
          for leg in p.get('legs',[]):
            link=f"leg_{leg.lower()}_{p['link_kind']}"
            if link not in parts:
              raise ValueError(f"print-first leg row targets unknown link: {link}")
            # A structural candidate replaces the old link mesh;
            # decorative thigh/shin pieces remain independently
            # available for the fit check.
            parts[link][:]=[(om,oc,on) for om,oc,on in parts[link]
                    if not (on.startswith(p['link_kind']+'_link')
                        or on.startswith(p['link_kind']+'_bracket'))]
            parts[link].append((m.copy(),p.get('color','#286bb1'),p['name']))
        else:
          # Caps are removable hardware and therefore an additional
          # named part on the same rigid link. They are not hidden in
          # the structural STL or counted as a second case.
          for leg in p.get('legs',[]):
            link=f"leg_{leg.lower()}_{p['link_kind']}"
            if link not in parts:
              raise ValueError(f"print-first cap row targets unknown link: {link}")
            parts[link].append((m.copy(),p.get('color','#607d8b'),p['name']))
      feet_rows,prefixes=foot_parts()
      prefix_tuple=tuple(prefixes)
      for leg in E.LEGS:
        link=f'leg_{leg.lower()}_tibia'
        # Retain the rigid foot socket from the original design, while
        # replacing old toe/pad/shell parts with the explicit candidate.
        parts[link][:]=[(m,c,n) for m,c,n in parts[link]
                if not n.startswith(prefix_tuple)]
        parts[link].extend((m.copy(),c,n) for m,c,n,legs in feet_rows if leg in legs)
      # Final fail-closed name guard. An old STD servo case/horn is never
      # allowed to reappear through a future legacy collector change.
      forbidden=('servo_case','servo_horn','STD_HORN','standard_servo')
      leftovers=[f'{link}/{n}' for link,items in parts.items()
            for _,_,n in items if any(token.lower() in n.lower() for token in forbidden)]
      if leftovers:
        raise ValueError('print-first legacy servo geometry remained: '+', '.join(leftovers))
    return parts
  E.collect_all_parts=collect
  def electronics():
    old=oldE['base_link_electronics_items']()
    # 旧 mic/speaker/battery の仮位置は取り除き、同じ helper が返す
    # 実包絡の中心へだけ質量を割り当てる。eye と wiring_misc は従来の
    # 固定質量項として残すが、wiring_misc は固体占有へ変換しない。
    keep=[it for it in old if it.label.startswith(('eye_','wiring_misc'))]
    battery=C.PRINT_FIRST_BATTERY
    diag,meshes=PC.get_print_first_component_bundle(
      battery_size_mm=battery['size_mm'],
      battery_mass_g=battery['mass_g'],
      battery_reference_center_mm=battery['center_base_mm'])
    for row in COMPONENTS:
      keep.append(E.box_mass_item(row['mass_g'],row['size_mm'],
                    (row['center_zb_mm'][0],row['center_zb_mm'][1],
                     row['center_zb_mm'][2]+E.ZB),
                    row['name'],verified=False))
    # All three rows are generated from PC's mesh/transform path, so the
    # candidate mass COM and the collision envelope cannot silently drift.
    audio_mass={
      'battery_2s_2200mah_candidate':float(battery['mass_g']),
      'mic_cannon_real_pocket_candidate':float(C.PRINT_FIRST_AUDIO['mic_mass_g']),
      'speaker_cannon_real_pocket_candidate':float(C.PRINT_FIRST_AUDIO['speaker_mass_g']),
    }
    for name,mesh in meshes.items():
      keep.append(_mass_item_from_mesh(mesh,audio_mass[name],name))
    return keep
  E.base_link_electronics_items=electronics

  def build_mass_items(parts):
    per_link=oldE['build_link_mass_items'](parts)
    # Every printed mesh mass in this context comes from the area×wall+
    # infill estimate in E.part_mass_item, not a weighed part. Keep the
    # regular profile's historical flag unchanged, but fail closed for
    # this candidate so downstream reports cannot call an estimate
    # measured. Servo/electronics box items are already unverified.
    for items in per_link.values():
      for item in items:
        item.verified = False
    # XIAO/camera hardware belongs to the fixed eye-pod link. The tray
    # itself is already in pf_camera_carrier and is counted as printed
    # material; these two items are design-mass allowances for the boards.
    if 'eye_pod_camera' in per_link:
      # Use the same +Z adjusted meshes as collision and body
      # integration, so mass COM and serialized occupancy cannot drift.
      holder=xiao_holder_meshes()
      per_link['eye_pod_camera'].append(
        _mass_item_from_mesh(holder['xiao_all_boards_occupancy'],
                   C.PRINT_FIRST_XIAO['board_design_mass_g'],
                   'xiao_all_boards_design_allowance'))
      per_link['eye_pod_camera'].append(
        _mass_item_from_mesh(holder['camera_child_lens_occupancy'],
                   C.PRINT_FIRST_XIAO['camera_design_mass_g'],
                   'camera_child_lens_design_allowance'))
    return per_link
  E.build_link_mass_items=build_mass_items
  # Bind the recorder belonging to this exact context.  This is necessary
  # when this file is executed as ``__main__``: make_print_first_leg imports a
  # second module instance under ``print_first_assembly`` during startup.
  E._ACTIVE_PRINT_FIRST_CABIN_STORAGE_RECORDER=cabin_storage_policy_record
  E.OUT=ROOT/'hardware/urdf-print-first';E.MESH_DIR=E.OUT/'meshes'
  try:yield E
  finally:
    E._ACTIVE_PRINT_FIRST_CABIN_STORAGE_RECORDER=old_recorder
    CABIN_STORAGE_RUNTIME.clear()
    CABIN_STORAGE_RUNTIME.update(old_runtime)
    for k,v in oldC.items():setattr(C,k,v)
    for k,v in oldM.items():setattr(MC,k,v)
    for k,v in oldE.items():setattr(E,k,v)
    E._LINK_T_STATIC.clear();E._LINK_T_STATIC.update(old_static)


if __name__=='__main__':
  with context():E.main()
