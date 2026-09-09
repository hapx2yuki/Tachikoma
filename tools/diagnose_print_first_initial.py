#!/usr/bin/env python3
"""最終印刷優先モデルの ``mj_forward`` 直後を記録する診断器。

通常の物理実行は最初の ``mj_step`` 後の時系列を保存するため、初期化直後の
凸形状接触を特定できない。この小ツールは同じケース、凍結URDF、実C++出力、
assembly context、材料別凸分解を使い、接地合わせの前後を分けて保存する。
``mj_step`` は呼び出さない。診断結果は原因特定用であり、合否を変更しない。
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys

import numpy as np
import mujoco
import trimesh

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import sim_print_first as P
import sim_physics as S
import sim_stress as T
import sim_collision as COLLISION


def _sha(path: Path) -> str:
    return P.sha(path)


def _public_freeze_validation(case: dict, output: Path) -> dict:
    """凍結検証の絶対パスを診断JSON用の公開キーへ変換する。"""
    options = case.get('model', {}) if isinstance(case, dict) else {}
    strict = options.get('model_kind') in P.FINAL_MODEL_KINDS
    return P.publicize(
        P.validate_freeze_manifest(case, require_generation_roles=strict), output)


def _public(path: Path, output: Path) -> str:
    return P.public_path(path, output)


def _copy_collision_cache(source: Path, destination: Path) -> dict:
    """検証済みVHACDキャッシュだけを診断出力へ複製する。

    以前の生成束に残った壊れたNPZをそのまま再利用すると、現在の厳格な
    ``sim_collision`` 入力検査で診断が途中停止する。壊れたキャッシュは
    入力として採用せず、同じキーを現在の生成器で再構築する。スキップは
    結果へ記録して、無言の再利用にしない。
    """
    destination.mkdir(parents=True, exist_ok=True)
    result = {'source': str(source), 'copied': 0, 'skipped_invalid': []}
    if not source.is_dir():
        return result
    for item in source.glob('*.npz'):
        try:
            COLLISION._load_validated_hulls(item, f'cached hull {item.name}')
        except Exception as exc:  # noqa: BLE001 - retain cache provenance
            result['skipped_invalid'].append({
                'file': item.name,
                'reason': str(exc),
            })
            continue
        target = destination / item.name
        if not target.exists():
            shutil.copy2(item, target)
            result['copied'] += 1
    return result


def _joint_angles(m, d, names, idx):
    rows = []
    for name in names:
        jid = int(idx['jid'][name])
        qadr = int(m.jnt_qposadr[jid])
        dadr = int(m.jnt_dofadr[jid])
        rows.append({
            'joint': name,
            'qpos_rad': float(d.qpos[qadr]),
            'angle_deg': float(np.degrees(d.qpos[qadr])),
            'qvel_rad_s': float(d.qvel[dadr]),
            'qpos_address': qadr,
            'dof_address': dadr,
        })
    return rows


def _geom_info(m, geom, idx):
    geom = int(geom)
    body_id = int(m.geom_bodyid[geom])
    geom_name = mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_GEOM, geom) or f'geom_{geom}'
    body_name = (mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_BODY, body_id)
                 if body_id else 'world') or f'body_{body_id}'
    metadata = (idx.get('part_metadata') or {}).get(geom_name)
    if metadata is None:
        metadata = {}
    return {
        'geom_id': geom,
        'geom_name': geom_name,
        'body_id': body_id,
        'body_name': body_name,
        'link': metadata.get('link', body_name),
        'part_name': metadata.get('part', geom_name),
        'material': metadata.get('material', 'UNMAPPED'),
        'part_metadata': dict(metadata),
        'geom_type': int(m.geom_type[geom]),
        'geom_group': int(m.geom_group[geom]),
    }


def _contact_normal_world(frame):
    """Return the MuJoCo contact normal from a row-major contact frame.

    MuJoCo stores the three contact-frame basis vectors in rows of ``frame``;
    the normal is therefore row 0.  The transpose below is still the correct
    row-basis to world-basis conversion for forces and torques.
    """
    matrix = np.asarray(frame, dtype=float).reshape(3, 3)
    return matrix[0]


def _contact_row(m, d, ci, idx):
    c = d.contact[ci]
    left = _geom_info(m, c.geom1, idx)
    right = _geom_info(m, c.geom2, idx)
    frame = np.asarray(c.frame, dtype=float).reshape(3, 3)
    contact_force = np.zeros(6, dtype=float)
    force_status = 'UNAVAILABLE_EFC'
    if int(c.efc_address) >= 0:
        mujoco.mj_contactForce(m, d, ci, contact_force)
        force_status = 'AVAILABLE'
    # MuJoCo contact frame stores its three basis vectors row-wise. Keeping the
    # local values and frame makes the world transform auditable; transpose is
    # the row-basis -> world-basis conversion.
    world_force = frame.T @ contact_force[:3]
    world_torque = frame.T @ contact_force[3:]
    b1, b2 = int(m.geom_bodyid[c.geom1]), int(m.geom_bodyid[c.geom2])
    self_contact = bool(b1 and b2 and float(c.dist) < 0.)
    return {
        'contact_index': int(ci),
        'geom1': left,
        'geom2': right,
        'self_collision': self_contact,
        'position_world_m': np.asarray(c.pos, dtype=float).tolist(),
        'distance_m': float(c.dist),
        'distance_mm': float(c.dist) * 1000.,
        'normal_world': _contact_normal_world(frame).tolist(),
        'frame_world_matrix': frame.tolist(),
        'dim': int(c.dim),
        'efc_address': int(c.efc_address),
        'force_status': force_status,
        'force_contact_frame': contact_force[:3].tolist(),
        'torque_contact_frame': contact_force[3:].tolist(),
        'force_world_N': world_force.tolist(),
        'torque_world_Nm': world_torque.tolist(),
    }


def _state(m, d, idx, names, label):
    base = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, 'base_link')
    contacts = [_contact_row(m, d, ci, idx) for ci in range(int(d.ncon))]
    self_contacts = [row for row in contacts if row['self_collision']]
    pairs = Counter()
    for row in self_contacts:
        pair = '|'.join(sorted((row['geom1']['body_name'], row['geom2']['body_name'])))
        pairs[pair] += 1
    return {
        'label': label,
        'time_s': float(d.time),
        'mj_step_called': False,
        'qpos': np.asarray(d.qpos, dtype=float).tolist(),
        'qvel': np.asarray(d.qvel, dtype=float).tolist(),
        'base_qpos': np.asarray(d.qpos[:7], dtype=float).tolist(),
        'base_qvel': np.asarray(d.qvel[:6], dtype=float).tolist(),
        'base_position_world_m': np.asarray(d.xpos[base], dtype=float).tolist(),
        'base_rpy_deg': [float(v) for v in np.degrees(S.quat_to_rpy(d.xquat[base]))],
        'joint_angles': _joint_angles(m, d, names, idx),
        'ncon': int(d.ncon),
        'contacts': contacts,
        'self_contact_pair_counts': dict(sorted(pairs.items())),
        'self_contact_count': len(self_contacts),
        'max_self_penetration_mm': max((row['distance_mm'] * -1. for row in self_contacts), default=0.),
    }


def _world_transform_mm(m, d, link):
    """MuJoCoのリンク姿勢を、元STL(mm)へ適用する4x4行列にする。"""
    body_id = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, link)
    if body_id < 0:
        raise ValueError(f'link body not found for source geometry audit: {link}')
    transform = np.eye(4)
    transform[:3, :3] = np.asarray(d.xmat[body_id], dtype=float).reshape(3, 3)
    transform[:3, 3] = np.asarray(d.xpos[body_id], dtype=float) * 1000.0
    return transform


def _mesh_validation(mesh, label, *, kind='input'):
    """Boolean入力/結果の有限性、閉性、向き、体積を検査する。

    ``kind`` は入力と Boolean 結果の規則を台帳上で分けるためのもの。
    source/VHACD入力および非空の交差結果は有限な正体積を必須とし、
    空交差の体積0は ``_mesh_is_empty`` で明示的に処理してからここへ
    渡さない。
    """
    validation = {
        'label': str(label),
        'kind': str(kind),
        'vertex_count': None,
        'face_count': None,
        'finite_vertices': False,
        'finite_faces': False,
        'finite': False,
        'watertight': False,
        'winding_consistent': False,
        'is_volume': False,
        'volume_mm3': None,
        'positive_volume': False,
        'nonnegative_volume': False,
        'status': 'ERROR',
        'errors': [],
    }
    if mesh is None:
        validation['errors'].append('mesh is None')
        return validation
    try:
        vertices = np.asarray(mesh.vertices, dtype=float)
        faces = np.asarray(mesh.faces)
        validation['vertex_count'] = int(len(vertices))
        validation['face_count'] = int(len(faces))
        if len(vertices) == 0 or len(faces) == 0:
            validation['errors'].append('mesh is empty')
        elif vertices.ndim != 2 or vertices.shape[1] != 3:
            validation['errors'].append('vertices are not an Nx3 array')
        elif not np.isfinite(vertices).all():
            validation['errors'].append('vertices contain non-finite values')
        else:
            validation['finite_vertices'] = True
        if len(faces) > 0:
            try:
                faces_numeric = np.asarray(faces, dtype=float)
            except (TypeError, ValueError):
                faces_numeric = None
            if faces_numeric is None:
                validation['errors'].append('faces are not numeric')
            elif faces_numeric.ndim != 2 or faces_numeric.shape[1] != 3:
                validation['errors'].append('faces are not an Mx3 array')
            elif not np.isfinite(faces_numeric).all():
                validation['errors'].append('faces contain non-finite values')
            elif not np.equal(faces_numeric, np.floor(faces_numeric)).all():
                validation['errors'].append('faces contain non-integer indices')
            elif len(vertices) == 0 or np.any(faces_numeric < 0) \
                    or np.any(faces_numeric >= len(vertices)):
                validation['errors'].append('face index is out of bounds')
            else:
                validation['finite_faces'] = True
        if validation['finite_vertices'] and validation['finite_faces']:
            validation['finite'] = True
        try:
            validation['watertight'] = bool(mesh.is_watertight)
        except Exception as exc:  # noqa: BLE001 - preserve validation cause
            validation['errors'].append(f'watertight check failed: {type(exc).__name__}: {exc}')
        if not validation['watertight']:
            validation['errors'].append('mesh is not watertight')
        try:
            validation['winding_consistent'] = bool(mesh.is_winding_consistent)
        except Exception as exc:  # noqa: BLE001 - preserve validation cause
            validation['errors'].append(
                f'winding check failed: {type(exc).__name__}: {exc}')
        if not validation['winding_consistent']:
            validation['errors'].append('mesh winding is inconsistent')
        try:
            validation['is_volume'] = bool(mesh.is_volume)
        except Exception as exc:  # noqa: BLE001 - preserve validation cause
            validation['errors'].append(
                f'is_volume check failed: {type(exc).__name__}: {exc}')
        if not validation['is_volume']:
            validation['errors'].append('mesh is not a valid volume')
        try:
            volume = float(mesh.volume)
            validation['volume_mm3'] = volume
            if not np.isfinite(volume):
                validation['errors'].append('mesh volume is non-finite')
            elif volume < 0.0:
                validation['errors'].append('mesh volume is negative')
            else:
                validation['nonnegative_volume'] = True
                if volume <= 0.0:
                    validation['errors'].append(
                        f'{kind} mesh volume must be > 0 (got {volume!r})')
                else:
                    validation['positive_volume'] = True
        except Exception as exc:  # noqa: BLE001 - preserve validation cause
            validation['errors'].append(f'volume check failed: {type(exc).__name__}: {exc}')
    except Exception as exc:  # noqa: BLE001 - preserve validation cause
        validation['errors'].append(f'mesh inspection failed: {type(exc).__name__}: {exc}')
    validation['status'] = 'PASS' if not validation['errors'] else 'ERROR'
    return validation


def _mesh_is_empty(mesh):
    """Booleanが返す完全な空交差だけを体積0として扱う。

    頂点だけ、または面だけが残った途中結果は空交差ではない。これを
    ``True`` にすると、Booleanの切断・直列化エラーが非交差へ変換される
    ため、両方の配列が空で有限体積0の場合だけ許可する。
    """
    if mesh is None:
        return True
    try:
        vertices = np.asarray(mesh.vertices)
        faces = np.asarray(mesh.faces)
        if len(vertices) != 0 or len(faces) != 0:
            return False
        volume = float(mesh.volume)
        return bool(np.isfinite(volume) and volume == 0.0)
    except Exception:
        return False


def _validation_error(validation, context):
    errors = validation.get('errors') or ['unknown mesh validation error']
    return f'{context}: ' + '; '.join(str(error) for error in errors)


def _boolean_volume_mm3(first, second):
    """2つの閉体のBoolean交差体積をmm³で返す。

    空交差は0として返す。入力または非空の交差結果が有限・watertight・
    winding-consistent・正体積を満たさない場合は、絶対値で正常化せず
    ``(None, error)`` として呼び出し側へ戻す。
    """
    for mesh, label in ((first, 'first input'), (second, 'second input')):
        validation = _mesh_validation(mesh, label, kind='input')
        if validation['status'] != 'PASS':
            return None, _validation_error(validation, label)
    try:
        intersection = trimesh.boolean.intersection(
            [first, second], engine='manifold', check_volume=False)
        if intersection is None:
            return 0.0, None
        pieces = intersection if isinstance(intersection, (list, tuple)) else [intersection]
        nonempty = [piece for piece in pieces if not _mesh_is_empty(piece)]
        if not nonempty:
            return 0.0, None
        volumes = []
        for index, mesh in enumerate(nonempty):
            validation = _mesh_validation(mesh, f'intersection[{index}]',
                                          kind='intersection')
            if validation['status'] != 'PASS':
                return None, _validation_error(validation, f'intersection[{index}]')
            volumes.append(validation['volume_mm3'])
        volume = float(sum(volumes))
        if not np.isfinite(volume):
            return None, 'intersection volume is non-finite'
        if volume < 0.0:
            return None, 'intersection volume is negative'
        return volume, None
    except Exception as exc:  # noqa: BLE001 - keep diagnostic running and record cause
        return None, f'{type(exc).__name__}: {exc}'


def _source_geometry_audit(m, d, state, convex_rows, convex_manifest, source_parts):
    """pf_chassis対各pf_femurの実メッシュ/凸片を同じ姿勢で突合する。

    MuJoCoは凸片で接触を計算するため、実メッシュBooleanが空でも凸片が
    接触する場合がある。その場合は部品を削らず、近似由来の接触として記録し、
    VHACD条件の再検討対象にする。
    """
    source_by_key = {}
    for link, items in source_parts.items():
        for mesh, _color, name in items:
            if name == 'pf_chassis' or name in ('pf_femur_link', 'pf_femur_link_m'):
                source_by_key[(link, name)] = mesh.copy()

    row_by_key = {}
    for part_index, (link, name, material, hulls) in enumerate(convex_rows):
        if name == 'pf_chassis' or name in ('pf_femur_link', 'pf_femur_link_m'):
            row_by_key[(link, name)] = {
                'part_index': part_index,
                'material': material,
                'hulls': hulls,
                'manifest': convex_manifest[part_index]
                if part_index < len(convex_manifest) else {},
            }

    contact_rows = state.get('contacts', [])
    report = {
        'units': {'length': 'mm', 'volume': 'mm3'},
        'method': 'source STL Boolean intersection (Manifold) versus each VHACD convex-piece Boolean intersection at exact mj_forward pose; MuJoCo contacts are listed separately.',
        'source_boolean_engine': 'manifold',
        'classification_rule': 'CONVEX_APPROXIMATION_FALSE_POSITIVE when source STL volume is zero and any convex-piece intersection is positive; this does not alter contact filtering.',
        'state_label': state.get('label'),
        'time_s': state.get('time_s'),
        'mujoco_step_called': False,
        'pairs': [],
    }
    chassis_key = ('base_link', 'pf_chassis')
    if chassis_key not in source_by_key or chassis_key not in row_by_key:
        report['status'] = 'UNVERIFIED_MISSING_SOURCE_OR_CONVEX_PART'
        report['missing'] = [str(key) for key in (chassis_key,)
                             if key not in source_by_key or key not in row_by_key]
        return report

    chassis_source = source_by_key[chassis_key]
    chassis_hulls = row_by_key[chassis_key]['hulls']
    chassis_transform = _world_transform_mm(m, d, 'base_link')
    chassis_source.apply_transform(chassis_transform)
    chassis_validation = _mesh_validation(chassis_source, 'source base_link/pf_chassis')
    chassis_hulls_world = []
    for hull in chassis_hulls:
        transformed = hull.copy()
        transformed.apply_transform(chassis_transform)
        chassis_hulls_world.append(transformed)
    chassis_hull_validations = [
        _mesh_validation(hull, f'convex base_link/pf_chassis[{index}]')
        for index, hull in enumerate(chassis_hulls_world)
    ]
    report['input_mesh_validation'] = {
        'chassis_source': chassis_validation,
        'chassis_hulls': chassis_hull_validations,
    }

    for leg in ('FR', 'FL', 'RL', 'RR'):
        link = f'leg_{leg.lower()}_femur'
        name = 'pf_femur_link_m' if leg in ('FR', 'RL') else 'pf_femur_link'
        key = (link, name)
        row = {'leg': leg, 'link': link, 'part': name}
        if key not in source_by_key or key not in row_by_key:
            row['status'] = 'UNVERIFIED_MISSING_SOURCE_OR_CONVEX_PART'
            report['pairs'].append(row)
            continue

        femur_source = source_by_key[key]
        femur_transform = _world_transform_mm(m, d, link)
        femur_source.apply_transform(femur_transform)
        femur_validation = _mesh_validation(femur_source, f'source {link}/{name}')
        femur_hulls_world = []
        for hull in row_by_key[key]['hulls']:
            transformed = hull.copy()
            transformed.apply_transform(femur_transform)
            femur_hulls_world.append(transformed)
        femur_hull_validations = [
            _mesh_validation(hull, f'convex {link}/{name}[{index}]')
            for index, hull in enumerate(femur_hulls_world)
        ]
        row['source_mesh_validation'] = {
            'chassis': chassis_validation,
            'femur': femur_validation,
        }
        row['convex_input_mesh_validation'] = {
            'chassis': chassis_hull_validations,
            'femur': femur_hull_validations,
        }
        row['source'] = {
            'chassis': {
                'link': 'base_link', 'part': 'pf_chassis',
                'source_volume_mm3': chassis_validation['volume_mm3'],
                'hull_count': len(chassis_hulls),
                'single_hull_volume_ratio': row_by_key[chassis_key]['manifest'].get('single_hull_volume_ratio'),
            },
            'femur': {
                'link': link, 'part': name,
                'source_volume_mm3': femur_validation['volume_mm3'],
                'hull_count': len(row_by_key[key]['hulls']),
                'single_hull_volume_ratio': row_by_key[key]['manifest'].get('single_hull_volume_ratio'),
            },
            'boolean_intersection_volume_mm3': None,
            'boolean_error': None,
        }
        invalid_inputs = []
        if chassis_validation['status'] != 'PASS':
            invalid_inputs.append(_validation_error(chassis_validation, 'chassis source'))
        invalid_inputs.extend(
            _validation_error(validation, validation['label'])
            for validation in chassis_hull_validations
            if validation['status'] != 'PASS'
        )
        if femur_validation['status'] != 'PASS':
            invalid_inputs.append(_validation_error(femur_validation, 'femur source'))
        invalid_inputs.extend(
            _validation_error(validation, validation['label'])
            for validation in femur_hull_validations
            if validation['status'] != 'PASS'
        )
        if not chassis_hulls:
            invalid_inputs.append('chassis convex hull list is empty')
        if not femur_hulls_world:
            invalid_inputs.append('femur convex hull list is empty')
        if invalid_inputs:
            row['classification'] = 'UNVERIFIED_SOURCE_GEOMETRY'
            row['errors'] = invalid_inputs
            row['convex_piece_intersections'] = []
            row['mujoco_contacts'] = [contact for contact in contact_rows
                                      if {contact['geom1']['part_name'], contact['geom2']['part_name']} ==
                                      {'pf_chassis', name}]
            row['status'] = 'UNVERIFIED_SOURCE_GEOMETRY'
            report['pairs'].append(row)
            continue

        source_volume, source_error = _boolean_volume_mm3(chassis_source, femur_source)
        row['source'].update({
            'boolean_intersection_volume_mm3': source_volume,
            'boolean_error': source_error,
        })
        convex_intersections = []
        for chassis_index, chassis_hull in enumerate(chassis_hulls_world):
            for femur_index, femur_hull in enumerate(femur_hulls_world):
                # VHACD pieces are numerous; avoid a Boolean call for disjoint
                # AABBs while retaining every positive-volume candidate.
                if (np.any(chassis_hull.bounds[1] < femur_hull.bounds[0]) or
                        np.any(femur_hull.bounds[1] < chassis_hull.bounds[0])):
                    continue
                volume, error = _boolean_volume_mm3(chassis_hull, femur_hull)
                if error is not None or (volume is not None and volume > 1e-9):
                    convex_intersections.append({
                        'chassis_hull_index': chassis_index,
                        'femur_hull_index': femur_index,
                        'volume_mm3': volume,
                        'boolean_error': error,
                        'chassis_geom_name': f"part_{row_by_key[chassis_key]['part_index']}_{chassis_index}",
                        'femur_geom_name': f"part_{row_by_key[key]['part_index']}_{femur_index}",
                    })
        row['convex_piece_intersections'] = convex_intersections
        row['mujoco_contacts'] = [contact for contact in contact_rows
                                  if {contact['geom1']['part_name'], contact['geom2']['part_name']} ==
                                  {'pf_chassis', name}]
        source_zero = source_volume is not None and source_volume <= 1e-9
        convex_positive = any(item.get('volume_mm3') is not None and
                              item['volume_mm3'] > 1e-9
                              for item in convex_intersections)
        if source_zero and convex_positive:
            row['classification'] = 'CONVEX_APPROXIMATION_FALSE_POSITIVE'
        elif source_volume is None:
            row['classification'] = 'UNVERIFIED_SOURCE_BOOLEAN'
        elif convex_positive:
            row['classification'] = 'SOURCE_AND_CONVEX_INTERSECTION'
        else:
            row['classification'] = 'NO_POSITIVE_CONVEX_INTERSECTION'
        if source_error is not None:
            row['status'] = 'UNVERIFIED_SOURCE_BOOLEAN'
            row.setdefault('errors', []).append(source_error)
        elif any(item.get('boolean_error') for item in convex_intersections):
            row['status'] = 'UNVERIFIED_CONVEX_BOOLEAN'
            row.setdefault('errors', []).extend(
                item['boolean_error'] for item in convex_intersections
                if item.get('boolean_error'))
        else:
            row['status'] = 'RECORDED'
        report['pairs'].append(row)

    unverified = [row['leg'] for row in report['pairs']
                  if str(row.get('status', '')).startswith('UNVERIFIED')]
    report['status'] = ('UNVERIFIED_SOURCE_GEOMETRY' if unverified
                        else 'PASS_SOURCE_AUDIT_RECORDED')
    report['summary'] = {
        'source_boolean_zero_legs': [row['leg'] for row in report['pairs']
                                     if row.get('source', {}).get('boolean_intersection_volume_mm3') is not None
                                     and row['source']['boolean_intersection_volume_mm3'] <= 1e-9],
        'convex_false_positive_legs': [row['leg'] for row in report['pairs']
                                       if row.get('classification') == 'CONVEX_APPROXIMATION_FALSE_POSITIVE'],
        'mujoco_contact_legs': [row['leg'] for row in report['pairs'] if row.get('mujoco_contacts')],
        'unverified_legs': unverified,
    }
    if unverified:
        report['errors'] = [
            {'leg': row['leg'], 'status': row.get('status'),
             'errors': row.get('errors', [])}
            for row in report['pairs'] if row['leg'] in unverified
        ]
    return report


def _native_initial(binary: Path, body_h: float):
    command = [0., 0., 0., 0., float(body_h)]
    result = subprocess.run(
        [str(binary), 'ready'],
        input=' '.join(map(str, command)) + '\n',
        capture_output=True, text=True, check=True,
    )
    values = np.loadtxt(result.stdout.splitlines(), ndmin=2)
    if values.shape != (1, 43) or not np.isfinite(values).all():
        raise ValueError(f'native initial trace has unexpected shape: {values.shape}')
    row = values[0]
    # Do not convert arbitrary numeric flags through bool(): a compiler or
    # wrapper regression returning 0.5 would otherwise become valid evidence.
    if not np.isclose(row[0], 0.0, atol=1e-9, rtol=0.0):
        raise ValueError(f'native initial phase must be 0, got {row[0]!r}')
    if not np.isclose(row[1], 0.0, atol=1e-9, rtol=0.0):
        raise ValueError(f'native initial moving flag must be 0, got {row[1]!r}')
    if not np.isclose(row[2], 1.0, atol=1e-9, rtol=0.0):
        raise ValueError(f'native initial ready flag must be 1, got {row[2]!r}')
    if not np.isclose(row[23:43], 1.0, atol=1e-9, rtol=0.0).all():
        raise ValueError('native initial enabled flags must all be 1')
    return row


def diagnose(case: dict, case_path: Path, output: Path) -> Path:
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    options = case.get('model', {})
    model_path, source_urdf, _ = P.select_model(case, output)
    profile = dict(P.DEFAULT, **case['profile'])
    profile_name = case.get('profile_name', case.get('name', 'case'))
    firmware_dir = output / 'firmware' / profile_name
    binary = P.prepare_native(profile, firmware_dir,
                              profile_mode=('frozen_print_first'
                                            if options.get('model_kind') in P.FINAL_MODEL_KINDS
                                            else 'candidate_print_first'))
    body_h = float(case['segments'][0].get('body_h', profile['body_h']))
    if not np.isfinite(body_h):
        raise ValueError(f'case t0 body_h must be finite, got {body_h!r}')
    native = _native_initial(binary, body_h)

    cache = output / 'collision-cache'
    # Reuse already generated hulls when available, then add only any cache
    # entries needed by this run. No old manifest is treated as a geometry input.
    cache_reuse = _copy_collision_cache(
        ROOT / 'outputs/print-first-20260905/final-simulation/collision-cache', cache)
    old_urdf = S.URDF_PATH
    old_s_output = S.FINGERPRINT_OUTPUT_ROOT
    old_collision_output = COLLISION.OUTPUT_ROOT
    old_convex = COLLISION.convex_parts

    def convex_with_output(*args, **kwargs):
        # ``sim_collision.convex_parts`` writes its transient manifest through
        # the module-level function name.  Since that name now points at this
        # wrapper, the attributes are set directly on the wrapper by the
        # callee; copying attributes from ``old_convex`` would overwrite them
        # with stale values.
        return old_convex(*args, cache=cache, **kwargs)

    S.URDF_PATH = model_path
    S.FINGERPRINT_OUTPUT_ROOT = output
    COLLISION.OUTPUT_ROOT = output
    COLLISION.convex_parts = convex_with_output
    try:
        gains = case.get('gains', {'kp': {'leg': 24., 'arm': .8, 'eye': .05},
                                   'kv': {'leg': .4, 'arm': .03, 'eye': .005}})
        with P.geometry_context(case, model_path):
            with P.python_profile(profile):
                model, idx = S.build_model(
                    options.get('friction', 1.), gains['kp'], gains['kv'],
                    timestep=options.get('timestep', .002),
                    effort_scale=options.get('effort_scale', 1.),
                    mass_scale=options.get('mass_scale', 1.),
                    self_collision=options.get('self_collision', False),
                    include_parent_collision=options.get('include_parent_collision', False),
                    slope_deg=options.get('slope_deg', 0.),
                    step_height_mm=options.get('step_height_mm', 0.),
                    step_front_y=options.get('step_front_y', .25),
                    contact_model=options.get('contact_model', 'linked-hulls'),
                    hard_friction=options.get('hard_friction', .3),
                    include_servo_collision=options.get('include_servo_collision', False),
                    foot_candidate_dir=options.get('foot_candidate_dir'),
                )
                data = mujoco.MjData(model)
                mujoco.mj_setConst(model, data)
                base_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, 'base_link')
                qbase = int(model.jnt_qposadr[model.body_jntadr[base_id]])
                names = S.ALL_JOINTS
                last = {}
                targets, angles = S.compute_leg_targets(0., 0., 0., 0., last,
                                                        holding=True, body_h=body_h)
                targets.update(S.arm_targets_rad(angles)[0])
                targets.update({name: 0. for name in S.EYE_JOINTS})
                if case.get('initial', 'standing') == 'zero':
                    targets.update({name: 0. for name in S.ALL_LEG_JOINTS})
                    for side in ('r', 'l'):
                        targets.update({f'arm_{side}_yaw': 0.,
                                        f'arm_{side}_pitch': 0.,
                                        f'arm_{side}_elbow': math.pi / 4.})
                else:
                    targets.update(dict(zip(names, np.radians(native[3:23]))))
                data.qpos[qbase:qbase + 7] = [0., 0., body_h * .001, 1., 0., 0., 0.]
                for name, value in targets.items():
                    data.qpos[model.jnt_qposadr[idx['jid'][name]]] = value
                    data.ctrl[idx['aid'][name]] = value
                mujoco.mj_forward(model, data)
                before_ground = _state(model, data, idx, names,
                                       'after_initial_mj_forward_before_ground_alignment')
                if case.get('ground_initialization', True):
                    T.place_on_ground(model, data, qbase, options.get('slope_deg', 0.))
                else:
                    # Keep the state label explicit even when the case requests
                    # no z alignment; this forward is still before mj_step.
                    mujoco.mj_forward(model, data)
                after_ground = _state(model, data, idx, names,
                                      'after_ground_alignment_mj_forward_before_first_mj_step')
                # Re-read the source part meshes and cached VHACD pieces at
                # this exact pre-step pose.  This identifies contacts that
                # exist only because a concavity was filled by decomposition.
                source_parts = COLLISION.parts_with_pad(include_servos=True)
                convex_rows = convex_with_output('vhacd', include_servos=True)
                # sim_collision.convex_parts writes its transient manifest
                # attributes through the module-level function name. Since
                # this diagnostic wraps that name to force an output-local
                # cache, read the attributes from the wrapper itself.
                manifest = getattr(convex_with_output, 'last_manifest', []) or []
                manifest_path = getattr(convex_with_output, 'last_manifest_path', None)
                source_geometry_audit = _source_geometry_audit(
                    model, data, after_ground, convex_rows, manifest, source_parts)
    finally:
        S.URDF_PATH = old_urdf
        S.FINGERPRINT_OUTPUT_ROOT = old_s_output
        COLLISION.OUTPUT_ROOT = old_collision_output
        COLLISION.convex_parts = old_convex

    component_rows = [row for row in manifest if str(row.get('part', '')).startswith('component_')]
    material_counts = Counter(str(row.get('material', 'UNKNOWN')) for row in manifest)
    all_states = [before_ground, after_ground]
    result = {
        'schema_version': 1,
        'status': 'DIAGNOSTIC_ONLY',
        'case_name': case.get('name'),
        'case_input': _public(case_path, output),
        # The t0 real-mesh checker treats the native pose as an executable
        # artifact.  Keep the exact case/config identity beside the pose so an
        # old diagnostic cannot be reused after a geometry or profile change.
        'case_sha256': _sha(case_path),
        'config_sha256': _sha(ROOT / 'hardware/src/config.py'),
        'joint_order': list(S.ALL_JOINTS),
        'model': {
            'path': _public(model_path, output),
            'sha256': _sha(model_path),
            'source_urdf': _public(source_urdf, output),
            'source_urdf_sha256': _sha(source_urdf),
            'config_sha256': _sha(ROOT / 'hardware/src/config.py'),
            'freeze_manifest': (_public(P._freeze_manifest_path(case), output)
                                if P._freeze_manifest_path(case) else None),
            'freeze_manifest_sha256': (_sha(P._freeze_manifest_path(case))
                                       if P._freeze_manifest_path(case) else None),
            'freeze_validation': _public_freeze_validation(case, output),
            'build_options': {
                key: options.get(key) for key in (
                    'model_kind', 'assembly_context', 'self_collision',
                    'include_servo_collision', 'include_parent_collision',
                    'contact_model', 'group_voltage_V')
            },
        },
        'controller': {
            'binary': _public(binary, output),
            'binary_sha256': _sha(binary),
            'build_json': _public(firmware_dir / 'build.json', output),
            'build_json_sha256': _sha(firmware_dir / 'build.json'),
            'header': {
                'path': _public(firmware_dir / 'print_first_gait.h', output),
                'sha256': _sha(firmware_dir / 'print_first_gait.h'),
                'source_config_sha256': _sha(ROOT / 'hardware/src/config.py'),
            },
            'trace': {
                'path': _public(firmware_dir / 'trace.cpp', output),
                'sha256': _sha(firmware_dir / 'trace.cpp'),
            },
            'profile_mode': 'frozen_print_first' if options.get('model_kind') in P.FINAL_MODEL_KINDS else 'candidate_print_first',
            'compile_flag': '-DTACHIKOMA_PRINT_FIRST_PROFILE=1',
            'compile_flag_required': '-DTACHIKOMA_PRINT_FIRST_PROFILE=1',
            # Preserve the raw numeric columns alongside the human-readable
            # bool fields.  The checker rejects this evidence if a wrapper
            # turns values such as 0.5 into True before recording them.
            'native_row_columns_raw': {
                'phase': float(native[0]),
                'moving': float(native[1]),
                'ready': float(native[2]),
            },
            'native_row_columns': {
                'phase': float(native[0]), 'moving': bool(native[1]),
                'ready': bool(native[2]),
            },
            'initial_body_h_mm': body_h,
            'native_initial_enabled_raw': {
                name: float(value) for name, value in zip(S.ALL_JOINTS, native[23:43])
            },
            'native_initial_joint_angles_deg': {
                name: float(value) for name, value in zip(S.ALL_JOINTS, native[3:23])
            },
            'native_initial_enabled': {
                name: bool(value) for name, value in zip(S.ALL_JOINTS, native[23:43])
            },
        },
        'collision_manifest': {
            'path': None if manifest_path is None else _public(manifest_path, output),
            'sha256': None if manifest_path is None else _sha(manifest_path),
            'part_count': len(manifest),
            'material_counts': dict(sorted(material_counts.items())),
            'component_part_count': len(component_rows),
            'component_parts': [
                {'part': row.get('part'), 'link': row.get('link'),
                 'material': row.get('material'), 'hull_count': row.get('hull_count'),
                 'source_volume_mm3': row.get('source_volume_mm3')}
                for row in component_rows
            ],
        },
        'collision_cache_reuse': {
            'source': _public(Path(cache_reuse['source']), output),
            'copied': int(cache_reuse['copied']),
            'skipped_invalid_count': len(cache_reuse['skipped_invalid']),
            'skipped_invalid': cache_reuse['skipped_invalid'],
        },
        'initialization': {
            'mj_step_called': False,
            'states': all_states,
            'comparison': {
                'qpos_same_before_after_ground_alignment': bool(
                    np.allclose(before_ground['qpos'], after_ground['qpos'], atol=1e-12)
                    or np.allclose(before_ground['qpos'][7:], after_ground['qpos'][7:], atol=1e-12)),
                'qvel_zero_after_initial_forward': bool(
                    np.allclose(after_ground['qvel'], 0., atol=1e-12)),
                'self_contact_pairs_after_ground_alignment': after_ground['self_contact_pair_counts'],
            },
            'source_geometry_audit': source_geometry_audit,
        },
        'interpretation': '初期化直後の凸分解接触を切り分ける診断。mj_forward後でmj_step前のため、物理合否や実機強度のPASSを意味しない。接地合わせ前後を別状態として保存し、0.01秒後をt0とは呼ばない。',
    }
    path = output / 'diagnostics' / f'{case.get("name", "case")}-initial-contact.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    P.save(path, result)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', type=Path, required=True,
                        help='一件のcase JSON、またはケース配列JSON')
    parser.add_argument('--case-name', default=None)
    parser.add_argument('--out', type=Path,
                        default=ROOT / 'outputs/print-first-20260905/final-simulation-initial-diagnostic')
    args = parser.parse_args()
    raw = json.loads(args.case.read_text(encoding='utf-8'))
    if isinstance(raw, list):
        if args.case_name:
            cases = [case for case in raw if case.get('name') == args.case_name]
            if not cases:
                raise SystemExit(f'case not found: {args.case_name}')
            case = cases[0]
        else:
            case = raw[0]
    else:
        case = raw
    path = diagnose(case, args.case.resolve(), args.out)
    print(json.dumps({'status': 'DIAGNOSTIC_ONLY', 'path': _public(path, args.out.resolve())}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
