#!/usr/bin/env python3
"""物理シミュレーション用の材料別/部品別凸分解。CADのSTLは変更しない。

VHACDは近似。名前付きの元部品・凸片・体積比を保存し、実メッシュ交差監査と
併用する。穴を持つ単一部品も凸包1個へ戻して自己接触を無効化しない。
"""
import hashlib
import json
import math
from collections.abc import Mapping
from importlib.metadata import version
import os
from pathlib import Path
import sys
import tempfile
import warnings
import numpy as np
import trimesh
from scipy.spatial import ConvexHull

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import export_urdf as E

CACHE=ROOT/'docs/audits/20260905-round2/simulation/collision-cache'
VHACD_SETTINGS=dict(maxConvexHulls=32,resolution=200000,
    minimumVolumePercentErrorAllowed=.5,maxNumVerticesPerCH=64,asyncACD=False,shrinkWrap=True)
# The on-disk archive format is part of the collision input contract.  A
# format bump is required whenever the decomposition or thin-cell recovery
# semantics change; old archives must fail closed instead of being silently
# reused.
CACHE_FORMAT = 5
THIN_PRISM_CONTRACT_VERSION = 2
# ``battery_cradle`` is the only current source part for which the generic
# VHACD cap left a 0.916485... surface certificate.  Its CAD construction is
# an outer rounded box followed by a cavity, a top opening, two belt slots,
# and four counterbored bolt holes.  The feature route below reconstructs
# those boolean boundaries from the canonical STL's section topology and
# makes a convex triangular prism for each exact 2D triangle.  The STL is
# never rewritten.  These immutable fingerprints make the route fail closed
# when a different file is placed under the same semantic name.
BATTERY_CRADLE_SOURCE_STL = ROOT / 'hardware' / 'stl' / 'battery_cradle.stl'
BATTERY_CRADLE_SOURCE_STL_SHA256 = (
    'c5362681caa42cd76275e2db9db5318b2759d2edf7eaed23aecb9a64de6965ab')
BATTERY_CRADLE_SOURCE_MESH_SHA256 = (
    'f11f9ec7993dc21fd816da668233eebf027f508c131f3f8711587016e5d87039')
# The contract includes the independent planar Boolean and constant-section
# proof below.  Bump it whenever that proof or its inputs change so an older
# archive cannot be treated as equivalent evidence.
BATTERY_CRADLE_FEATURE_CONTRACT_VERSION = 2
BATTERY_CRADLE_FEATURE_TOLERANCE_MM = 0.011
BATTERY_CRADLE_FEATURE_AREA_TOLERANCE_MM2 = 1e-7
BATTERY_CRADLE_FEATURE_FACE_Z_TOLERANCE_MM = 1e-7
BATTERY_CRADLE_FEATURE_VERTICAL_NORMAL_Z_TOLERANCE = 1e-6
BATTERY_CRADLE_FEATURE_BANDS = (
    # The levels are the direct z boundaries of H/wall, the belt-slot
    # cutter, the flange, and the counterbore cutter in
    # hardware/src/make_chassis.py::battery_cradle().
    ('bottom_plate_with_belt_slots', -32.0, -29.0, -30.5),
    ('lower_side_walls_with_belt_relief', -29.0, -28.0, -28.5),
    ('side_walls', -28.0, -4.0, -16.0),
    ('flange_counterbore_layer', -4.0, -2.0, -3.0),
    ('flange_through_hole_layer', -2.0, 0.0, -1.0),
)
# 実行結果をリポジトリ外へ保存する場合でも、台帳に絶対パスを出さない。
# sim_print_first が実行単位の出力根を設定する。
OUTPUT_ROOT = None
ELECTRONICS_ENVELOPE_PARTS = frozenset({
    'xiao_all_boards_occupancy',
    'camera_child_lens_occupancy',
})

# One replacement contract is shared by the t0 exact-mesh checker and the
# finite self-collision sweep.  Keeping the forbidden names here prevents one
# checker from accepting an old kit part that the other checker rejects.
PRINT_FIRST_REQUIRED_PARTS = (
    'pf_head_top_clearanced',
    'pf_eye_pod_camera_clearanced',
    'pf_camera_carrier',
)
PRINT_FIRST_LEGACY_PART_NAMES = (
    'Head_Top_Eyecut',
    'Head_Top_Blue',
    'eye_pod_camera',
    'camera_carrier',
    'foot_pad',
    'Leg_Toe_Black_x12',
    'pf_head_top',
)

# The final MuJoCo model keeps parent-link collision enabled globally.  A few
# adjacent link interfaces are represented by many convex pieces, and the
# convex approximation can overlap across an otherwise clean exact STL
# interface.  These five pairs are the only fixed, reviewed exception.  The
# list is deliberately a module constant: a case JSON cannot add or remove a
# pair at runtime.  Exact source-mesh checks over the native trace and a dense
# one-joint limit sweep are separate evidence required before this exception
# can be interpreted as a fit classification.
PRINT_FIRST_PROXY_EXCLUSION_PAIRS = (
    ('arm_l_forearm', 'arm_l_upper'),
    ('arm_l_shoulder', 'arm_l_upper'),
    ('arm_r_forearm', 'arm_r_upper'),
    ('arm_r_shoulder', 'arm_r_upper'),
    ('leg_fl_femur', 'leg_fl_tibia'),
)
PRINT_FIRST_PROXY_EXCLUSION_JOINTS = {
    ('arm_l_forearm', 'arm_l_upper'): 'arm_l_elbow',
    ('arm_l_shoulder', 'arm_l_upper'): 'arm_l_pitch',
    ('arm_r_forearm', 'arm_r_upper'): 'arm_r_elbow',
    ('arm_r_shoulder', 'arm_r_upper'): 'arm_r_pitch',
    ('leg_fl_femur', 'leg_fl_tibia'): 'leg_fl_knee',
}
PRINT_FIRST_PROXY_EXCLUSION_VERSION = 1


def collision_cache_definition(mode):
    """Return the canonical definition used for a collision archive."""
    if not isinstance(mode, str) or not mode:
        raise ValueError('collision cache mode is required')
    return {
        'format': CACHE_FORMAT,
        'mode': mode,
        'single_hull_ratio_threshold': 1.05,
        'trimesh_version': trimesh.__version__,
        'vhacdx_version': version('vhacdx'),
        'vhacd_settings': VHACD_SETTINGS,
        'thin_prism_contract_version': THIN_PRISM_CONTRACT_VERSION,
        'battery_cradle_feature_contract_version': (
            BATTERY_CRADLE_FEATURE_CONTRACT_VERSION),
    }


def collision_cache_definition_sha(mode):
    """Return the content SHA of the current canonical archive definition."""
    payload = json.dumps(collision_cache_definition(mode), sort_keys=True,
                         separators=(',', ':')).encode()
    return hashlib.sha256(payload).hexdigest()


def collision_cache_input_digest(mesh, definition):
    """Return the deterministic filename digest for one source mesh."""
    if not isinstance(definition, dict):
        raise ValueError('collision cache definition is malformed')
    definition_bytes = json.dumps(definition, sort_keys=True,
                                  separators=(',', ':')).encode()
    vertices = np.ascontiguousarray(np.asarray(mesh.vertices))
    faces = np.ascontiguousarray(np.asarray(mesh.faces))
    return hashlib.sha256(vertices.tobytes() + faces.tobytes()
                          + definition_bytes).hexdigest()


def print_first_proxy_exclusion_contract():
    """Return the immutable adjacent-interface exception contract.

    ``sim_physics`` consumes this function when building a generated
    print-first model, while diagnostics/results record the same canonical
    rows and SHA.  Keeping the digest over the pair/joint mapping makes an
    accidental edit visible in the model evidence and does not permit a case
    file to broaden the exception.
    """
    pairs = []
    for pair in PRINT_FIRST_PROXY_EXCLUSION_PAIRS:
        if len(pair) != 2 or tuple(sorted(pair)) != tuple(pair):
            raise ValueError(f'invalid proxy exclusion pair ordering: {pair!r}')
        joint = PRINT_FIRST_PROXY_EXCLUSION_JOINTS.get(pair)
        if not isinstance(joint, str) or not joint:
            raise ValueError(f'proxy exclusion pair has no reviewed joint: {pair!r}')
        pairs.append({
            'body1': pair[0],
            'body2': pair[1],
            'joint': joint,
            'classification': 'INTENDED_JOINT_INTERFACE_EXACT_SOURCE_PROOF_REQUIRED',
        })
    payload = {
        'version': PRINT_FIRST_PROXY_EXCLUSION_VERSION,
        'pairs': pairs,
        'parent_collision_filtering': 'globally_disabled',
        'runtime_case_additions': 'forbidden',
        'exact_proof_required': True,
    }
    canonical = json.dumps(payload, sort_keys=True,
                            ensure_ascii=False, separators=(',', ':')).encode()
    return {**payload, 'sha256': hashlib.sha256(canonical).hexdigest()}


def print_first_legacy_name(name):
    """旧部品名のexact/prefix/fragmentを共通規則で検出する。

    採用済みの3置換名だけは説明上旧名を含むため明示的に許可する。
    それ以外の綴りは大文字小文字を区別せず断片まで拒否し、旧部品の
    ``#instance``や別suffixが新しい靴/頭の行数契約をすり抜けないようにする。
    """
    if not isinstance(name, str) or not name:
        return '<invalid_name>'
    if name in PRINT_FIRST_REQUIRED_PARTS:
        return None
    lowered = name.casefold()
    for token in PRINT_FIRST_LEGACY_PART_NAMES:
        if token.casefold() in lowered:
            return token
    return None


def collision_material(name):
    """衝突包絡の材料分類を一元化する。占有箱は印刷PLAへ落とさない。"""
    name = str(name)
    if (name == 'tpu_shoe' or name == 'foot_pad' or
            name.startswith('foot_pad#') or name.startswith('tpu_shoe#')):
        return 'TPU'
    if '_servo_horn_' in name:
        return 'SERVO_HORN'
    if name.endswith('_servo_case') or '_ld220_case' in name:
        return 'SERVO_CASE'
    if name.startswith('component_') or name in ELECTRONICS_ENVELOPE_PARTS:
        return 'ELECTRONICS_ENVELOPE'
    return E.part_material(name)[0]


def _path_key(path, output_root=None):
    """凸分解キャッシュの公開用キーを返す。絶対実行パスは保存しない。"""
    p = Path(path).resolve()
    output = output_root if output_root is not None else OUTPUT_ROOT
    if output is not None:
        try:
            return '$OUTPUT/' + p.relative_to(Path(output).resolve()).as_posix()
        except ValueError:
            pass
    try:
        return p.relative_to(ROOT).as_posix()
    except ValueError:
        digest = (hashlib.sha256(p.read_bytes()).hexdigest()[:16]
                  if p.is_file() else hashlib.sha256(str(p).encode()).hexdigest()[:16])
        return f'$EXTERNAL/{p.name}#{digest}'


def _file_sha256(path):
    """ファイル内容のSHA-256を返す（キャッシュ来歴用）。"""
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f'collision cache input is missing: {path}')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_sha256(value):
    return (isinstance(value, str) and len(value) == 64
            and value == value.lower()
            and all(char in '0123456789abcdef' for char in value))


def _mesh_digest(mesh):
    """メッシュ頂点/面の実体を内容アドレス化する。"""
    vertices = np.ascontiguousarray(np.asarray(mesh.vertices))
    faces = np.ascontiguousarray(np.asarray(mesh.faces))
    digest = hashlib.sha256()
    for array in (vertices, faces):
        digest.update(str(array.dtype).encode())
        digest.update(repr(tuple(array.shape)).encode())
        digest.update(array.tobytes())
    return digest.hexdigest()


def _hull_digest(mesh):
    """最終的にMuJoCoへ渡す凸片の内容SHAを返す。"""
    return _mesh_digest(mesh)


def _array_digest(array):
    """Hash a numeric array including dtype/shape for thin-cell evidence."""
    values = np.ascontiguousarray(np.asarray(array))
    digest = hashlib.sha256()
    digest.update(str(values.dtype).encode())
    digest.update(repr(tuple(values.shape)).encode())
    digest.update(values.tobytes())
    return digest.hexdigest()


def _cache_public_key(path):
    """出力場所に依存しない凸分解キャッシュの公開キー。"""
    return f'$COLLISION_CACHE/{Path(path).name}'


def public_collision_cache_ledger(ledger):
    """内部Pathを除いた、結果JSONへ埋め込めるキャッシュ台帳を返す。"""
    if not isinstance(ledger, dict):
        raise ValueError('collision cache ledger is malformed')
    manifest = ledger.get('manifest')
    if not isinstance(manifest, dict):
        raise ValueError('collision cache ledger manifest is malformed')
    entries = ledger.get('entries')
    if not isinstance(entries, list):
        raise ValueError('collision cache ledger entries are malformed')
    public_entries = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError('collision cache ledger entry is malformed')
        row = {key: value for key, value in entry.items()
               if not key.startswith('_')}
        public_entries.append(row)
    return {
        'version': ledger.get('version'),
        'manifest': {key: value for key, value in manifest.items()
                     if not key.startswith('_')},
        'entries': public_entries,
        'entry_count': len(public_entries),
        'definition': ledger.get('definition'),
    }


def _resolve_public_cache_path(path, output_root=None):
    """公開キャッシュキーを現在の実ファイルへ束縛する。"""
    if not isinstance(path, str) or not path:
        raise ValueError('collision cache path is missing')
    if path.startswith('$COLLISION_CACHE/'):
        name = path[len('$COLLISION_CACHE/'):]
        if not name or Path(name).name != name or '..' in Path(name).parts:
            raise ValueError(f'invalid collision cache path: {path}')
        candidates = []
        if output_root is not None:
            candidates.append(Path(output_root).resolve() / 'collision-cache' / name)
        candidates.append(CACHE / name)
        for candidate in candidates:
            if candidate.is_file():
                return candidate.resolve()
        raise FileNotFoundError(f'collision cache input is missing: {path}')
    if path.startswith('$OUTPUT/'):
        if output_root is None:
            raise ValueError(f'cannot resolve output cache path without output root: {path}')
        relative = Path(path[len('$OUTPUT/'):])
        if '..' in relative.parts:
            raise ValueError(f'invalid collision cache output path: {path}')
        candidate = (Path(output_root).resolve() / relative).resolve()
        try:
            candidate.relative_to(Path(output_root).resolve())
        except ValueError as exc:
            raise ValueError(f'collision cache output path escaped output root: {path}') from exc
        return candidate
    candidate = (ROOT / path).resolve()
    try:
        candidate.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise ValueError(f'collision cache path is outside repository: {path}') from exc
    return candidate


def collision_cache_input_fingerprints(ledger, output_root=None, *,
                                       source_parts=None,
                                       reject_extra_cache=False):
    """消費したNPZ/manifestと各最終凸片を再ハッシュする。

    ``ledger`` は build_model が返す内部台帳でも、結果JSONから読んだ公開
    台帳でもよい。ファイル位置ではなく ``$COLLISION_CACHE/<name>`` を入力
    キーへ使うため、同じ内容の出力キャッシュを別のbundleへ移しても来歴を
    比較できる。キャッシュが壊れている場合は例外でfail-closedにする。
    """
    if not isinstance(ledger, dict):
        raise ValueError('collision cache ledger is missing')
    manifest = ledger.get('manifest')
    entries = ledger.get('entries')
    if not isinstance(manifest, dict) or not isinstance(entries, list):
        raise ValueError('collision cache ledger schema is invalid')

    def actual_path(item, *, manifest_item=False):
        private_key = '_manifest_path' if manifest_item else '_cache_path'
        value = item.get(private_key)
        if isinstance(value, Path):
            return value.resolve()
        return _resolve_public_cache_path(item.get('path'), output_root)

    manifest_path = actual_path(manifest, manifest_item=True)
    manifest_sha = _file_sha256(manifest_path)
    declared_manifest_sha = manifest.get('sha256')
    if (not _is_sha256(declared_manifest_sha)
            or declared_manifest_sha != manifest_sha):
        raise ValueError(
            'collision cache manifest content SHA differs from ledger')
    try:
        manifest_payload = json.loads(manifest_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError('collision cache manifest is not valid JSON') from exc
    if not isinstance(manifest_payload, list):
        raise ValueError('collision cache manifest must be a list')
    manifest_rows = {}
    for manifest_row in manifest_payload:
        if not isinstance(manifest_row, dict):
            raise ValueError('collision cache manifest row is malformed')
        key = (manifest_row.get('link'), manifest_row.get('part'))
        if key in manifest_rows:
            raise ValueError(f'collision cache manifest has duplicate row: {key!r}')
        manifest_rows[key] = manifest_row
    hashes = {_cache_public_key(manifest_path): manifest_sha}
    current_manifest = {
        'path': _cache_public_key(manifest_path),
        'location': _path_key(manifest_path),
        'sha256': hashes[_cache_public_key(manifest_path)],
    }
    current_entries = []
    seen = {}
    expected_cache_paths = set()
    expected_definition_by_mode = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError('collision cache ledger entry is malformed')
        cache_path = actual_path(entry)
        expected_cache_paths.add(cache_path.resolve())
        cache_key = _cache_public_key(cache_path)
        cache_sha = _file_sha256(cache_path)
        declared_cache_sha = entry.get('sha256')
        if (not _is_sha256(declared_cache_sha)
                or declared_cache_sha != cache_sha):
            raise ValueError(
                f'collision cache content SHA differs from ledger for {cache_key}')
        declared_input_sha = entry.get('input_mesh_sha256')
        if (not _is_sha256(declared_input_sha)
                or cache_path.stem != declared_input_sha):
            raise ValueError(
                f'collision cache path does not match deterministic input digest: '
                f'{cache_key}')
        source_sha = entry.get('source_mesh_sha256')
        definition_sha = entry.get('cache_definition_sha256')
        source_convex_sha = entry.get('source_convex_hull_sha256')
        if (not _is_sha256(source_sha) or not _is_sha256(definition_sha)
                or not _is_sha256(source_convex_sha)):
            raise ValueError(
                f'collision cache ledger metadata is incomplete for {cache_key}')
        definition = entry.get('cache_definition')
        if not isinstance(definition, dict):
            raise ValueError(
                f'collision cache definition is missing for {cache_key}')
        mode = definition.get('mode')
        if not isinstance(mode, str) or not mode:
            raise ValueError(f'collision cache definition mode is missing for {cache_key}')
        canonical_definition = collision_cache_definition(mode)
        if definition != canonical_definition:
            raise ValueError(
                f'collision cache definition differs from current code for {cache_key}')
        expected_definition_sha = collision_cache_definition_sha(mode)
        if definition_sha != expected_definition_sha:
            raise ValueError(
                f'collision cache definition SHA is stale for {cache_key}')
        expected_definition_by_mode[mode] = expected_definition_sha
        if cache_key in seen and seen[cache_key] != cache_sha:
            raise ValueError(f'collision cache basename is ambiguous: {cache_key}')
        seen[cache_key] = cache_sha
        hashes[cache_key] = cache_sha
        hulls, _ = _load_validated_hulls(
            cache_path, f'collision cache ledger {entry.get("link")}/{entry.get("part")}',
            expected_source_mesh_sha256=source_sha,
            expected_definition_sha256=definition_sha,
            expected_source_convex_hull_sha256=source_convex_sha)
        hull_shas = [_hull_digest(hull) for hull in hulls]
        declared_hull_shas = entry.get('hull_sha256')
        if (not isinstance(declared_hull_shas, list)
                or len(declared_hull_shas) != len(hull_shas)
                or declared_hull_shas != hull_shas):
            raise ValueError(
                f'collision cache hull SHA differs from ledger for {cache_key}')
        source_bounds = entry.get('source_bounds_mm')
        if source_bounds is None:
            # The source bounds are deliberately stored in the archive and
            # ledger.  Without them a valid-but-wrong convex archive could be
            # self-consistent while having no independent relation to its STL.
            raise ValueError(f'collision cache source bounds are missing for {cache_key}')
        source_bounds = np.asarray(source_bounds, dtype=float)
        if source_bounds.shape != (2, 3) or not np.isfinite(source_bounds).all() \
                or np.any(source_bounds[1] < source_bounds[0]):
            raise ValueError(f'collision cache source bounds are invalid for {cache_key}')
        manifest_row = manifest_rows.get((entry.get('link'), entry.get('part')))
        if manifest_row is None:
            raise ValueError(
                f'collision cache manifest has no row for {entry.get("link")}/'
                f'{entry.get("part")}')
        feature_contract = manifest_row.get('feature_decomposition')
        for key in ('input_mesh_sha256', 'source_mesh_sha256',
                    'cache_definition_sha256', 'cache_sha256', 'hull_sha256',
                    'source_convex_hull_sha256', 'source_bounds_mm',
                    'source_coverage', 'feature_decomposition'):
            # The public ledger has historically called the archive content
            # digest ``sha256`` while the manifest calls it
            # ``cache_sha256``.  Compare the same value explicitly instead
            # of treating a valid cache as a manifest mismatch.
            ledger_value = (entry.get('sha256') if key == 'cache_sha256'
                            else entry.get(key))
            if manifest_row.get(key) != ledger_value:
                raise ValueError(
                    f'collision cache manifest/ledger mismatch for {cache_key}: {key}')
        if entry.get('part') == 'battery_cradle':
            if (not isinstance(feature_contract, dict)
                    or feature_contract.get('status') != 'PASS'
                    or feature_contract.get('contract_version')
                    != BATTERY_CRADLE_FEATURE_CONTRACT_VERSION
                    or feature_contract.get('source_stl_sha256')
                    != BATTERY_CRADLE_SOURCE_STL_SHA256
                    or feature_contract.get('hull_sha256') != hull_shas):
                raise ValueError(
                    f'battery-cradle feature contract is incomplete for {cache_key}')
            _validate_battery_cradle_feature_cache(cache_path, feature_contract)
            if source_parts is None:
                # A replayed public ledger has no caller-owned source mesh to
                # compare against.  Rebuild the one canonical base-link
                # placement independently; accepting the ledger's own source
                # SHA/bounds here would allow a self-consistent fake hull.
                independent_source = _battery_cradle_independent_source()
                independent_source_sha = _mesh_digest(independent_source)
                independent_convex, _ = _validated_outward_mesh(
                    independent_source.convex_hull,
                    'independent battery-cradle source convex hull')
                independent_convex_sha = _hull_digest(independent_convex)
                independent_input_sha = collision_cache_input_digest(
                    independent_source, canonical_definition)
                if source_sha != independent_source_sha:
                    raise ValueError(
                        f'collision cache battery-cradle source SHA differs from '
                        f'current source for {entry.get("link")}/{entry.get("part")}')
                if source_convex_sha != independent_convex_sha:
                    raise ValueError(
                        f'collision cache battery-cradle convex hull SHA differs '
                        f'from current source for {entry.get("link")}/{entry.get("part")}')
                if entry.get('input_mesh_sha256') != independent_input_sha:
                    raise ValueError(
                        f'collision cache battery-cradle input digest differs from '
                        f'current source for {entry.get("link")}/{entry.get("part")}')
                if not np.allclose(independent_source.bounds, source_bounds,
                                   rtol=0.0, atol=1e-7):
                    raise ValueError(
                        f'collision cache battery-cradle placement differs from '
                        f'current source for {entry.get("link")}/{entry.get("part")}')
                independent_hulls, independent_feature = (
                    _battery_cradle_feature_decomposition(
                        independent_source, link=entry.get('link'),
                        part=entry.get('part')))
                independent_hull_shas = [
                    _hull_digest(hull) for hull in independent_hulls]
                if (feature_contract != independent_feature
                        or hull_shas != independent_hull_shas):
                    raise ValueError(
                        f'collision cache battery-cradle feature split differs from '
                        f'current source for {entry.get("link")}/{entry.get("part")}')
        for index, hull in enumerate(hulls):
            if (np.asarray(hull.bounds).shape != (2, 3)
                    or not np.isfinite(hull.bounds).all()
                    or np.any(hull.bounds[0] < source_bounds[0] - 1e-7)
                    or np.any(hull.bounds[1] > source_bounds[1] + 1e-7)):
                raise ValueError(
                    f'collision cache hull {cache_key}[{index}] escapes source bounds')
        thin_records = manifest_row.get('thin_piece_replacements', [])
        if not isinstance(thin_records, list):
            raise ValueError(
                f'collision cache thin-prism records are malformed for {cache_key}')
        for record in thin_records:
            if not isinstance(record, dict):
                raise ValueError(
                    f'collision cache thin-prism record is malformed for {cache_key}')
            hull_index = record.get('hull')
            if (isinstance(hull_index, bool) or not isinstance(hull_index, int)
                    or hull_index < 0 or hull_index >= len(hulls)):
                raise ValueError(
                    f'collision cache thin-prism hull index is invalid for {cache_key}')
            try:
                independent_thin = audit_thin_piece_replacement(
                    record, hulls[hull_index], label=f'{cache_key}[{hull_index}]')
            except Exception as exc:  # noqa: BLE001 - fail closed for cache geometry
                raise ValueError(
                    f'collision cache thin-prism audit failed for {cache_key}: {exc}') from exc
            if independent_thin.get('status') != 'PASS':
                raise ValueError(f'collision cache thin-prism audit failed for {cache_key}')
        if source_parts is not None:
            link = entry.get('link')
            part = entry.get('part')
            candidates = []
            for item in (source_parts.get(link, [])
                         if isinstance(source_parts, Mapping) else []):
                if not isinstance(item, (list, tuple)) or len(item) < 2:
                    continue
                name = item[-1]
                if name == part:
                    candidates.append(item[0])
            if len(candidates) != 1 or not isinstance(candidates[0], trimesh.Trimesh):
                raise ValueError(
                    f'collision cache source part is not uniquely bound: {link}/{part}')
            source_mesh = candidates[0]
            _, source_validation = _validated_outward_mesh(
                source_mesh, f'cache source {link}/{part}', source=True)
            source_convex_hull, _ = _validated_outward_mesh(
                source_mesh.convex_hull, f'cache source convex hull {link}/{part}')
            actual_source_convex_sha = _hull_digest(source_convex_hull)
            if actual_source_convex_sha != source_convex_sha:
                raise ValueError(
                    f'collision cache source convex hull SHA differs from current source '
                    f'for {link}/{part}')
            actual_source_sha = _mesh_digest(source_mesh)
            if actual_source_sha != source_sha:
                raise ValueError(
                    f'collision cache source mesh SHA differs from current source for '
                    f'{link}/{part}')
            if not np.allclose(source_mesh.bounds, source_bounds,
                               rtol=0.0, atol=1e-7):
                raise ValueError(
                    f'collision cache source bounds differ from current source for '
                    f'{link}/{part}')
            expected_input = collision_cache_input_digest(
                source_mesh, canonical_definition)
            if entry.get('input_mesh_sha256') != expected_input:
                raise ValueError(
                    f'collision cache input digest differs from current source for '
                    f'{link}/{part}')
            if part == 'battery_cradle':
                _, expected_feature = _battery_cradle_feature_decomposition(
                    source_mesh, link=link, part=part)
                if feature_contract != expected_feature or hull_shas != expected_feature.get(
                        'hull_sha256'):
                    raise ValueError(
                        f'collision cache battery-cradle feature split differs from '
                        f'current source for {link}/{part}')
            # A ``parts`` cache is exactly the source convex hull.  This is a
            # cheap independent reconstruction that rejects a fabricated hull
            # even when an attacker edits the NPZ and all self-reported SHAs.
            if mode == 'parts':
                if hull_shas != [actual_source_convex_sha]:
                    raise ValueError(
                        f'parts cache hull differs from fresh source convex hull '
                        f'for {link}/{part}')
            else:
                equations = _convex_hull_equations(
                    np.asarray(source_mesh.vertices),
                    f'cache source convex hull {link}/{part}')
                for hull_index, hull in enumerate(hulls):
                    distance = (np.asarray(hull.vertices, dtype=float)
                                @ equations[:, :3].T + equations[:, 3])
                    if (not np.isfinite(distance).all()
                            or float(distance.max()) > 0.011):
                        raise ValueError(
                            f'cached hull {link}/{part}[{hull_index}] escapes '
                            'fresh source convex hull')
                coverage = _source_coverage_report(
                    source_mesh, hulls, f'cache source coverage {link}/{part}')
                if coverage.get('status') != 'PASS':
                    raise ValueError(
                        f'cached VHACD hulls do not cover fresh source surface '
                        f'for {link}/{part}: {coverage}')
                declared_coverage = entry.get('source_coverage')
                if not isinstance(declared_coverage, dict):
                    raise ValueError(
                        f'collision cache source coverage is missing for '
                        f'{link}/{part}')
                if declared_coverage != coverage:
                    raise ValueError(
                        f'collision cache source coverage differs from current source '
                        f'for {link}/{part}')
        for index, digest in enumerate(hull_shas):
            hashes[f'$COLLISION_HULL/{cache_path.name}/{index}'] = digest
        current_entries.append({
            'link': entry.get('link'),
            'part': entry.get('part'),
            'path': cache_key,
            'location': _path_key(cache_path),
            'sha256': cache_sha,
            'source_mesh_sha256': source_sha,
            'source_convex_hull_sha256': source_convex_sha,
            'input_mesh_sha256': entry.get('input_mesh_sha256'),
            'cache_definition_sha256': definition_sha,
            'hull_sha256': hull_shas,
            'cache_definition': definition,
            'source_bounds_mm': source_bounds.tolist(),
            'source_coverage': entry.get('source_coverage'),
            'feature_decomposition': feature_contract,
        })
    if reject_extra_cache:
        cache_dirs = {path.parent for path in expected_cache_paths}
        for cache_dir in cache_dirs:
            actual = {path.resolve() for path in cache_dir.glob('*.npz')}
            extra = sorted(str(path) for path in actual - expected_cache_paths)
            missing = sorted(str(path) for path in expected_cache_paths - actual)
            if extra:
                raise ValueError(
                    f'collision cache contains unconsumed extra NPZ files: {extra}')
            if missing:
                raise ValueError(
                    f'collision cache ledger references missing NPZ files: {missing}')
    current = {
        'version': ledger.get('version'),
        'manifest': current_manifest,
        'entries': current_entries,
        'entry_count': len(current_entries),
        'definition': ledger.get('definition'),
    }
    return dict(sorted(hashes.items())), current


def _atomic_save_hulls(path,arrays):
    """並列生成中に途中のZIPを読ませない。同一キーの完成品だけを公開する。"""
    temp=None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent,prefix=path.name+'.',suffix='.tmp',delete=False) as stream:
            temp=Path(stream.name)
            np.savez_compressed(stream,**arrays)
        os.replace(temp,path)
    finally:
        if temp is not None and temp.exists():temp.unlink()


def _mesh_validation(mesh, label):
    """正規化を行わず、元の面向きを含むメッシュ状態を検査する。"""
    vertices = np.asarray(mesh.vertices)
    faces = np.asarray(mesh.faces)
    if vertices.ndim != 2 or vertices.shape[1] != 3 or len(vertices) == 0:
        raise ValueError(f'{label}: vertices must be a non-empty Nx3 array')
    if faces.ndim != 2 or faces.shape[1] != 3 or len(faces) == 0:
        raise ValueError(f'{label}: faces must be a non-empty Mx3 array')
    try:
        vertices_finite = bool(np.isfinite(vertices).all())
        faces_finite = bool(np.isfinite(faces).all())
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{label}: vertices/faces are not numeric') from exc
    if not vertices_finite:
        raise ValueError(f'{label}: vertices contain non-finite values')
    if not faces_finite or not np.issubdtype(faces.dtype, np.integer):
        raise ValueError(f'{label}: faces contain non-finite or non-integer indices')
    if int(faces.min()) < 0 or int(faces.max()) >= len(vertices):
        raise ValueError(f'{label}: face index is out of bounds')
    try:
        watertight = bool(mesh.is_watertight)
        winding = bool(mesh.is_winding_consistent)
        is_volume = bool(mesh.is_volume)
        volume = float(mesh.volume)
    except Exception as exc:  # noqa: BLE001 - retain the geometry cause
        raise ValueError(f'{label}: mesh property inspection failed: {exc}') from exc
    if not watertight:
        raise ValueError(f'{label}: mesh is not watertight')
    if not winding:
        raise ValueError(f'{label}: mesh winding is inconsistent')
    if not np.isfinite(volume) or volume <= 0.0:
        raise ValueError(f'{label}: mesh volume must be finite and > 0 (got {volume!r})')
    if not is_volume:
        raise ValueError(f'{label}: mesh is not a valid volume')
    return mesh, {
        'finite': True,
        'watertight': watertight,
        'winding_consistent': winding,
        'is_volume': is_volume,
        'volume_mm3': volume,
        'vertex_count': int(len(vertices)),
        'face_count': int(len(faces)),
    }


def _validated_outward_mesh(mesh, label, *, source=False):
    """衝突元/凸片を厳密検査する。

    元STLは ``E._ensure_outward`` より前に検査する。負体積や向き異常を
    自動反転して入力の異常として見えなくすると、source/continuous の
    幾何監査が別の形状を証明してしまうためである。凸包・生成済み凸片は
    ``source=False`` の既定値で外向きへそろえた後に検査する。
    """
    if source:
        return _mesh_validation(mesh, label)
    try:
        mesh = E._ensure_outward(mesh)
    except Exception as exc:  # noqa: BLE001 - retain the geometry cause
        raise ValueError(f'{label}: orientation normalization failed: {exc}') from exc
    return _mesh_validation(mesh, label)


def _load_validated_hulls(path, label, *, expected_source_mesh_sha256=None,
                          expected_definition_sha256=None,
                          expected_source_convex_hull_sha256=None):
    """NPZキャッシュを読み、全凸片を再検査してから公開する。"""
    try:
        with np.load(path, allow_pickle=False) as saved:
            count = int(saved['count'])
            if count <= 0:
                raise ValueError('empty hull archive')
            if 'cache_format' not in saved:
                raise ValueError('missing cache metadata: cache_format')
            cache_format = np.asarray(saved['cache_format'])
            if cache_format.ndim != 0:
                raise ValueError('cache metadata is not scalar: cache_format')
            try:
                cache_format_value = int(cache_format.item())
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError('cache metadata is not an integer: cache_format') from exc
            if cache_format_value != CACHE_FORMAT:
                raise ValueError(
                    f'unsupported collision cache format {cache_format_value}; '
                    f'expected {CACHE_FORMAT}')
            def scalar_text(key):
                if key not in saved:
                    raise ValueError(f'missing cache metadata: {key}')
                value = np.asarray(saved[key])
                if value.ndim != 0:
                    raise ValueError(f'cache metadata is not scalar: {key}')
                value = value.item()
                if (not isinstance(value, str) or len(value) != 64
                        or value != value.lower()
                        or any(char not in '0123456789abcdef' for char in value)):
                    raise ValueError(f'cache metadata is not text: {key}')
                return value
            stored_source = scalar_text('source_mesh_sha256')
            stored_definition = scalar_text('cache_definition_sha256')
            stored_source_convex = scalar_text('source_convex_hull_sha256')
            if 'source_bounds_mm' not in saved:
                raise ValueError('missing cache metadata: source_bounds_mm')
            stored_source_bounds = np.asarray(saved['source_bounds_mm'], dtype=float)
            if (stored_source_bounds.shape != (2, 3)
                    or not np.isfinite(stored_source_bounds).all()
                    or np.any(stored_source_bounds[1] < stored_source_bounds[0])):
                raise ValueError('cache source_bounds_mm metadata is malformed')
            stored_hull_sha256 = np.asarray(saved['hull_sha256'])
            if (stored_hull_sha256.ndim != 1
                    or stored_hull_sha256.shape[0] != count
                    or any(not isinstance(value, str) or len(value) != 64
                           or value != value.lower()
                           or any(char not in '0123456789abcdef' for char in value)
                           for value in stored_hull_sha256.tolist())):
                raise ValueError('cache hull_sha256 metadata is malformed')
            if (expected_source_mesh_sha256 is not None
                    and stored_source != expected_source_mesh_sha256):
                raise ValueError(
                    f'cache source mesh SHA differs from current source: '
                    f'{stored_source} != {expected_source_mesh_sha256}')
            if (expected_definition_sha256 is not None
                    and stored_definition != expected_definition_sha256):
                raise ValueError(
                    f'cache definition SHA differs from current definition: '
                    f'{stored_definition} != {expected_definition_sha256}')
            if (expected_source_convex_hull_sha256 is not None
                    and stored_source_convex != expected_source_convex_hull_sha256):
                raise ValueError(
                    f'cache source convex hull SHA differs from current source: '
                    f'{stored_source_convex} != {expected_source_convex_hull_sha256}')
            hulls = [trimesh.Trimesh(vertices=saved[f'v{i}'],
                                     faces=saved[f'f{i}'], process=False)
                     for i in range(count)]
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001 - fail closed on malformed archives
        raise ValueError(f'{label}: invalid hull archive: {exc}') from exc
    checked = []
    validations = []
    for index, hull in enumerate(hulls):
        # A cache is an input artifact, so do not repair a reversed/invalid
        # hull while re-reading it.  Only hulls produced in the current
        # generation path may use the explicit orientation normalization.
        hull, validation = _validated_outward_mesh(
            hull, f'{label}[{index}]', source=True)
        checked.append(hull)
        validations.append(validation)
    actual_hull_sha256 = [_hull_digest(hull) for hull in checked]
    if actual_hull_sha256 != stored_hull_sha256.tolist():
        raise ValueError(
            f'{label}: cache hull SHA metadata does not match hull contents')
    for index, hull in enumerate(checked):
        if (np.any(hull.bounds[0] < stored_source_bounds[0] - 1e-7)
                or np.any(hull.bounds[1] > stored_source_bounds[1] + 1e-7)):
            raise ValueError(
                f'{label}[{index}]: hull bounds escape source bounds')
    return checked, validations


def _thin_piece_replacement(mesh, label, error):
    """VHACDの平面/極薄片を、投影輪郭の薄い凸柱へ明示置換する。

    大きな平面片を軸平行箱へ置き換えると、元の輪郭の外側を埋めて
    衝突を作る。ここではSVDの第一/第二軸へ投影した2D凸包をそのまま
    ±0.01 mmだけ押し出し、元の面積を保つ。入力STLの修復には使わない。
    """
    try:
        vertices = np.asarray(mesh.vertices, dtype=np.float64)
    except Exception as exc:  # noqa: BLE001 - preserve generated geometry cause
        raise ValueError(f'{label}: thin-piece vertices cannot be read ({error})') from exc
    if (vertices.ndim != 2 or vertices.shape[1] != 3 or len(vertices) < 4
            or not np.isfinite(vertices).all()):
        raise ValueError(f'{label}: invalid generated thin-piece vertices ({error})')
    center = vertices.mean(axis=0)
    try:
        _, _, axes = np.linalg.svd(vertices - center, full_matrices=False)
        # SVD may return a reflected basis.  Make the local frame right-handed
        # before constructing face winding, then recompute local coordinates.
        if np.linalg.det(axes) < 0:
            axes[2] *= -1.0
        local = (vertices - center) @ axes.T
        low, high = local.min(axis=0), local.max(axis=0)
        extent = high - low
    except Exception as exc:  # noqa: BLE001 - preserve generated geometry cause
        raise ValueError(f'{label}: thin-piece bounds cannot be computed ({error})') from exc
    thin_prism_thickness = 0.02
    thickness_tolerance = 1e-9
    if (not np.isfinite(extent).all()
            or float(extent[2]) > thin_prism_thickness + thickness_tolerance):
        raise ValueError(f'{label}: generated hull is invalid and is not a thin piece ({error})')
    planar_area_box = float(extent[0] * extent[1])
    relative_normal_thickness = (
        float(extent[2] / max(max(extent[0], extent[1]), 1e-12)))
    if (not math.isfinite(planar_area_box) or planar_area_box <= 0.0
            or not math.isfinite(relative_normal_thickness)
            or relative_normal_thickness > 0.1):
        raise ValueError(
            f'{label}: generated hull is not relatively thin enough ({error})')
    try:
        footprint = ConvexHull(local[:, :2])
        polygon = local[footprint.vertices, :2]
        footprint_area = float(footprint.volume)
    except Exception as exc:  # noqa: BLE001 - collinear/invalid cells fail closed
        raise ValueError(f'{label}: thin-piece footprint is not a 2D polygon ({error})') from exc
    if (not np.isfinite(footprint_area) or footprint_area <= 0.0
            or not np.isfinite(polygon).all()):
        raise ValueError(f'{label}: thin-piece footprint area is invalid ({error})')
    # ConvexHull supplies the boundary, but keep an explicit containment
    # check so a future projection/footprint change cannot silently enlarge
    # or otherwise alter the source cell's support region.
    polygon_next = np.roll(polygon, -1, axis=0)
    edge = polygon_next - polygon
    cross_values = (
        edge[None, :, 0] * (local[:, None, 1] - polygon[None, :, 1])
        - edge[None, :, 1] * (local[:, None, 0] - polygon[None, :, 0]))
    polygon_orientation = float(
        np.sum(polygon[:, 0] * polygon_next[:, 1]
               - polygon_next[:, 0] * polygon[:, 1]))
    containment_tolerance = 1e-8 * max(float(max(extent[0], extent[1])), 1.0)
    if polygon_orientation >= 0.0:
        footprint_contains_source_points = bool(
            np.all(cross_values >= -containment_tolerance))
    else:
        footprint_contains_source_points = bool(
            np.all(cross_values <= containment_tolerance))
    if not footprint_contains_source_points:
        raise ValueError(
            f'{label}: projected footprint does not contain source points ({error})')
    footprint_area_ratio = footprint_area / planar_area_box
    if (not math.isfinite(footprint_area_ratio)
            or footprint_area_ratio <= 0.0
            or footprint_area_ratio > 1.0 + 1e-9):
        raise ValueError(f'{label}: thin-piece footprint area ratio is invalid ({error})')

    # The replacement is an exact convex footprint prism with a fixed total
    # thickness of 0.02 mm.  Center it on the generated cell's measured normal
    # coordinate and retain the measured normal spread in the evidence.
    normal_center = float((low[2] + high[2]) / 2.0)
    half_thickness = 0.01
    bottom = np.column_stack((polygon, np.full(len(polygon),
                                                normal_center - half_thickness)))
    top = np.column_stack((polygon, np.full(len(polygon),
                                             normal_center + half_thickness)))
    local_vertices = np.vstack((bottom, top))
    count = len(polygon)
    if count < 3:
        raise ValueError(f'{label}: thin-piece footprint has fewer than 3 vertices ({error})')
    # Explicit triangles keep the generated archive rectangular for every
    # convex footprint (including pentagons and higher order polygons) and
    # make the outward winding auditable after cache reload.
    faces = []
    for index in range(1, count - 1):
        faces.append([0, index + 1, index])
        faces.append([count, count + index, count + index + 1])
    for index in range(count):
        nxt = (index + 1) % count
        faces.append([index, count + nxt, count + index])
        faces.append([index, nxt, count + nxt])
    transform = np.eye(4)
    transform[:3, :3] = axes.T
    transform[:3, 3] = center
    world_vertices = trimesh.transform_points(local_vertices, transform)
    replacement = trimesh.Trimesh(vertices=world_vertices,
                                  faces=np.asarray(faces, dtype=np.int64),
                                  process=False)
    # scipy's 2D hull orientation is not part of its public contract.  The
    # generated prism is allowed one deterministic orientation correction;
    # unlike source/cached STL validation this does not repair user geometry.
    if np.isfinite(replacement.volume) and replacement.volume < 0.0:
        replacement.invert()
    if (not np.isfinite(replacement.volume) or replacement.volume <= 0.0
            or not replacement.is_watertight or not replacement.is_volume):
        raise ValueError(f'{label}: thin-piece prism is not a finite positive volume ({error})')
    try:
        original_volume = float(mesh.volume)
    except (TypeError, ValueError, AttributeError):
        original_volume = None
    if original_volume is not None and not np.isfinite(original_volume):
        original_volume = None
    replacement_volume = float(replacement.volume)
    expected_prism_volume = float(footprint_area * (2.0 * half_thickness))
    volume_match_tolerance = max(1e-9, expected_prism_volume * 1e-6)
    volume_match = bool(math.isclose(
        replacement_volume, expected_prism_volume,
        rel_tol=1e-6, abs_tol=volume_match_tolerance))
    if not volume_match:
        raise ValueError(
            f'{label}: thin-piece prism volume does not match footprint*thickness ({error})')
    normal_spread_ok = bool(float(extent[2]) <= thin_prism_thickness
                            + thickness_tolerance)
    if not normal_spread_ok:
        raise ValueError(f'{label}: source normal spread exceeds 0.02 mm ({error})')
    return replacement, {
        'original_extents_mm': extent.astype(float).tolist(),
        'relative_normal_thickness': relative_normal_thickness,
        'footprint_area_mm2': footprint_area,
        'footprint_area_ratio_to_svd_box': footprint_area_ratio,
        'footprint_vertex_count': int(count),
        'normal_center_mm': normal_center,
        'normal_half_thickness_mm': half_thickness,
        'extruded_thickness_mm': 2.0 * half_thickness,
        'source_normal_spread_mm': float(extent[2]),
        'source_normal_spread_within_limit': normal_spread_ok,
        'max_normal_offset_mm': float(np.max(np.abs(local[:, 2] - normal_center))),
        'footprint_contains_source_points': footprint_contains_source_points,
        'original_signed_volume_mm3': original_volume,
        'replacement_volume_mm3': replacement_volume,
        'expected_prism_volume_mm3': expected_prism_volume,
        'volume_match_tolerance_mm3': volume_match_tolerance,
        'replacement_volume_matches_footprint_prism': volume_match,
        'reason': str(error),
        'source': 'generated_vhacd_piece_convex_footprint_prism',
        # Keep the projected source points and local frame in the manifest so
        # a post-hoc checker can rebuild the footprint through an independent
        # algorithm.  The checker must not trust footprint_area alone.
        'source_projected_points_2d': local[:, :2].astype(float).tolist(),
        'source_vertices_mm': vertices.astype(float).tolist(),
        'source_vertices_sha256': _array_digest(vertices),
        # Preserve the complete source cell, not just its vertices.  The
        # independent checker can then detect a record whose vertices and
        # topology were replaced together while leaving scalar evidence intact.
        'source_cell_faces': np.asarray(mesh.faces, dtype=np.int64).tolist(),
        'source_cell_mesh_sha256': _mesh_digest(mesh),
        'source_local_normal_bounds_mm': [float(low[2]), float(high[2])],
        'source_center_mm': center.astype(float).tolist(),
        'source_svd_axes': axes.astype(float).tolist(),
    }


def _monotone_chain_hull_2d(points):
    """Pure-Python convex hull used only for independent thin-prism auditing."""
    values = np.asarray(points, dtype=float)
    if values.ndim != 2 or values.shape[1] != 2 or not np.isfinite(values).all():
        raise ValueError('thin-prism projected points must be finite Nx2')
    unique = sorted({(float(x), float(y)) for x, y in values})
    if len(unique) < 3:
        raise ValueError('thin-prism projected points have no area')

    def cross(origin, first, second):
        return ((first[0] - origin[0]) * (second[1] - origin[1])
                - (first[1] - origin[1]) * (second[0] - origin[0]))

    lower = []
    for point in unique:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0.0:
            lower.pop()
        lower.append(point)
    upper = []
    for point in reversed(unique):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0.0:
            upper.pop()
        upper.append(point)
    polygon = np.asarray(lower[:-1] + upper[:-1], dtype=float)
    if polygon.shape[0] < 3:
        raise ValueError('thin-prism projected hull has fewer than three vertices')
    return polygon


def _polygon_area_2d(polygon):
    values = np.asarray(polygon, dtype=float)
    if values.ndim != 2 or values.shape[1] != 2 or len(values) < 3:
        raise ValueError('thin-prism polygon is malformed')
    return float(abs(np.sum(
        values[:, 0] * np.roll(values[:, 1], -1)
        - np.roll(values[:, 0], -1) * values[:, 1])) * 0.5)


def _independent_source_projection(vertices):
    """Project source vertices with a covariance/eigen basis.

    The producer uses ``np.linalg.svd``.  The audit deliberately derives a
    fresh frame from the covariance eigensystem and rebuilds the 2D convex
    footprint with its own monotone-chain implementation, so scalar metadata
    cannot make an enlarged box look like the source cell.
    """
    values = np.asarray(vertices, dtype=float)
    if values.ndim != 2 or values.shape[1] != 3 or len(values) < 4 \
            or not np.isfinite(values).all():
        raise ValueError('thin-prism source vertices are malformed')
    center = values.mean(axis=0)
    centered = values - center
    covariance = (centered.T @ centered) / float(len(values))
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    if (eigenvalues.shape != (3,) or eigenvectors.shape != (3, 3)
            or not np.isfinite(eigenvalues).all()
            or not np.isfinite(eigenvectors).all()):
        raise ValueError('thin-prism source covariance is malformed')
    order = np.argsort(eigenvalues)[::-1]
    axes = eigenvectors[:, order].T
    # Deterministic signs: flip each row so its largest component is positive,
    # then repair handedness without changing the projected area.
    for index in range(3):
        pivot = int(np.argmax(np.abs(axes[index])))
        if axes[index, pivot] < 0.0:
            axes[index] *= -1.0
    if np.linalg.det(axes) < 0.0:
        axes[2] *= -1.0
    local = centered @ axes.T
    if not np.isfinite(local).all():
        raise ValueError('thin-prism independent projection is non-finite')
    polygon = _monotone_chain_hull_2d(local[:, :2])
    return center, axes, local, polygon


def audit_thin_piece_replacement(record, replacement, *, label='thin-prism'):
    """Independently verify a stored projected-footprint prism record.

    The production path uses ``scipy.spatial.ConvexHull``.  This audit rebuilds
    the 2D hull with a monotone-chain implementation, then compares area,
    outline containment, normal envelope and the explicit 0.02 mm prism volume
    against the cached replacement.  It is intentionally independent of the
    producer's single scalar metadata fields.
    """
    if not isinstance(record, dict):
        raise ValueError(f'{label}: replacement record is missing')
    source_vertices = np.asarray(record.get('source_vertices_mm'), dtype=float)
    source_vertices_sha = record.get('source_vertices_sha256')
    if (source_vertices.ndim != 2 or source_vertices.shape[1] != 3
            or len(source_vertices) < 4 or not np.isfinite(source_vertices).all()
            or not _is_sha256(source_vertices_sha)
            or source_vertices_sha != _array_digest(source_vertices)):
        raise ValueError(f'{label}: source vertex evidence is missing or stale')
    source_faces = np.asarray(record.get('source_cell_faces'), dtype=np.int64)
    source_cell_sha = record.get('source_cell_mesh_sha256')
    if (source_faces.ndim != 2 or source_faces.shape[1] != 3
            or len(source_faces) == 0
            or np.any(source_faces < 0)
            or np.any(source_faces >= len(source_vertices))
            or not _is_sha256(source_cell_sha)):
        raise ValueError(f'{label}: source cell topology/hash evidence is missing')
    source_cell = trimesh.Trimesh(vertices=source_vertices,
                                  faces=source_faces, process=False)
    if source_cell_sha != _mesh_digest(source_cell):
        raise ValueError(f'{label}: source cell mesh SHA is stale')
    independent_center, independent_axes, independent_local, independent_polygon = \
        _independent_source_projection(source_vertices)
    independent_area = _polygon_area_2d(independent_polygon)
    independent_low = float(independent_local[:, 2].min())
    independent_high = float(independent_local[:, 2].max())
    points = _monotone_chain_hull_2d(record.get('source_projected_points_2d'))
    area = _polygon_area_2d(points)
    if not math.isclose(area, independent_area, rel_tol=1e-6, abs_tol=1e-8):
        raise ValueError(f'{label}: stored footprint differs from independent source projection')
    declared_area = record.get('footprint_area_mm2')
    if (not isinstance(declared_area, (int, float))
            or isinstance(declared_area, bool)
            or not math.isfinite(float(declared_area))
            or not math.isclose(float(declared_area), area,
                                rel_tol=1e-6, abs_tol=1e-8)):
        raise ValueError(f'{label}: independent projected footprint area mismatch')
    center = np.asarray(record.get('source_center_mm'), dtype=float)
    axes = np.asarray(record.get('source_svd_axes'), dtype=float)
    if center.shape != (3,) or axes.shape != (3, 3) or not np.isfinite(center).all() \
            or not np.isfinite(axes).all():
        raise ValueError(f'{label}: stored thin-prism frame is malformed')
    if not np.allclose(center, independent_center, rtol=0.0, atol=1e-7):
        raise ValueError(f'{label}: stored source center differs from source vertices')
    vertices = np.asarray(replacement.vertices, dtype=float)
    if vertices.ndim != 2 or vertices.shape[1] != 3 or not np.isfinite(vertices).all():
        raise ValueError(f'{label}: replacement vertices are malformed')
    # Validate the replacement against the independently reconstructed frame;
    # the producer's SVD frame is retained above only as provenance metadata.
    local = (vertices - independent_center) @ independent_axes.T
    projected = _monotone_chain_hull_2d(local[:, :2])
    projected_area = _polygon_area_2d(projected)
    if not math.isclose(projected_area, area, rel_tol=1e-6, abs_tol=1e-8):
        raise ValueError(f'{label}: replacement footprint area differs from source')
    # Every replacement projected vertex must be contained by the independently
    # rebuilt source convex polygon.  This rejects an enlarged OBB or fake
    # outline even when its area was edited to look plausible.
    source_polygon = independent_polygon
    orientation = np.sum(
        source_polygon[:, 0] * np.roll(source_polygon[:, 1], -1)
        - np.roll(source_polygon[:, 0], -1) * source_polygon[:, 1])
    next_points = np.roll(source_polygon, -1, axis=0)
    edge = next_points - source_polygon
    cross = (edge[None, :, 0] * (local[:, None, 1] - source_polygon[None, :, 1])
             - edge[None, :, 1] * (local[:, None, 0] - source_polygon[None, :, 0]))
    tolerance = 1e-7 * max(float(np.ptp(source_polygon, axis=0).max()), 1.0)
    if orientation >= 0.0:
        contained = bool(np.all(cross >= -tolerance))
    else:
        contained = bool(np.all(cross <= tolerance))
    if not contained:
        raise ValueError(f'{label}: replacement outline escapes source projection')
    normal_bounds = record.get('source_local_normal_bounds_mm')
    if (not isinstance(normal_bounds, list) or len(normal_bounds) != 2):
        raise ValueError(f'{label}: source normal envelope is missing')
    try:
        low, high = map(float, normal_bounds)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{label}: source normal envelope is malformed') from exc
    if (not math.isfinite(low) or not math.isfinite(high) or high < low
            or high - low > 0.02 + 1e-9):
        raise ValueError(f'{label}: source normal spread exceeds 0.02 mm')
    if not math.isclose(high - low, independent_high - independent_low,
                        rel_tol=0.0, abs_tol=1e-7):
        raise ValueError(f'{label}: source normal envelope differs from source vertices')
    replacement_low = float(local[:, 2].min())
    replacement_high = float(local[:, 2].max())
    if (not math.isclose(replacement_low, -0.01, abs_tol=1e-7)
            or not math.isclose(replacement_high, 0.01, abs_tol=1e-7)):
        raise ValueError(f'{label}: replacement thickness is not 0.02 mm')
    expected_volume = area * 0.02
    volume = float(replacement.volume)
    if (not math.isfinite(volume)
            or not math.isclose(volume, expected_volume,
                                rel_tol=1e-6, abs_tol=max(1e-9, expected_volume * 1e-6))):
        raise ValueError(f'{label}: replacement volume differs from independent prism volume')
    return {
        'status': 'PASS',
        'independent_footprint_area_mm2': area,
        'replacement_footprint_area_mm2': projected_area,
        'source_normal_spread_mm': high - low,
        'replacement_thickness_mm': replacement_high - replacement_low,
        'replacement_volume_mm3': volume,
        'expected_prism_volume_mm3': expected_volume,
        'independent_source_footprint_area_mm2': independent_area,
        'independent_source_normal_spread_mm': independent_high - independent_low,
    }


def _assert_unique_part_keys(parts, *, label='print-first part collector'):
    """Reject duplicate ``(link, part_name)`` rows from a source collector."""
    seen = {}
    duplicates = []
    for link, items in parts.items():
        for _mesh, _color, name in items:
            key = (str(link), str(name))
            if key in seen:
                duplicates.append(f'{key[0]}/{key[1]}')
            else:
                seen[key] = True
    if duplicates:
        raise ValueError(
            f'{label} emitted duplicate link/name rows: {sorted(set(duplicates))}')


def _print_first_component_rows():
    """Return the canonical print-first occupancy rows exactly once."""
    from print_first_assembly import component_meshes

    rows = list(component_meshes())
    seen = set()
    duplicates = []
    for _mesh, _color, name in rows:
        key = ('base_link', str(name))
        if key in seen:
            duplicates.append(f'{key[0]}/{key[1]}')
        seen.add(key)
    if duplicates:
        raise ValueError(
            'print-first component collector emitted duplicate link/name rows: '
            + ', '.join(sorted(set(duplicates)))
        )
    return rows


def parts_with_pad(include_servos=False,foot_candidate_dir=None,*,include_components=True):
    parts=E.collect_all_parts()
    for link,items in parts.items():
        if (not getattr(E.C,'PRINT_FIRST_ACTIVE',False) and link.endswith('_tibia')
                and not any(n == 'foot_pad' for _,_,n in items)):
            pad=E.load('foot_pad');pad.apply_transform(E.trans(0,0,-E.C.TIBIA_LEN))
            items.append((pad,'#333333','foot_pad'))
    if foot_candidate_dir:
        candidate=Path(foot_candidate_dir)
        if not candidate.is_absolute():candidate=ROOT/candidate
        placements={p.instance:p for p in E.KIT.by_link(E.KIT_PLACEMENTS,'leg_foot_bored')
                    if p.part=='Leg_Toe_Black_x12'}
        for leg in E.LEGS:
            items=parts[f'leg_{leg.lower()}_tibia']
            # 旧足パッドが collect_all_parts() に残っていても、旧モデルと
            # 候補靴を同時に接地させない。旧トゥも候補座席へ置換する。
            items[:]=[(m,c,n) for m,c,n in items
                      if n != 'foot_pad' and not n.startswith('Leg_Toe_Black_x12#')]
            for i in range(3):
                transform=E.trans(0,0,-E.C.TIBIA_LEN)@placements[f'{leg}_{i}'].matrix@np.linalg.inv(placements[f'FR_{i}'].matrix)
                for stem,material,name in (
                    ('shoe_fitted','#333333',f'foot_pad#shoe_{leg}_{i}'),
                    ('toe_hidden_seat','#222222',f'Leg_Toe_Black_x12#{leg}_{i}')):
                    mesh=trimesh.load(candidate/f'FR_{i}_{stem}_candidate.stl')
                    mesh.apply_transform(transform);items.append((mesh,material,name))
    if include_servos:
        mounts=[]
        for leg in E.LEGS:
            for kind,frame in E.leg_servo_frames(leg).items():
                link='base_link' if kind=='yaw' else f"leg_{leg.lower()}_{'coxa' if kind=='pitch' else 'femur'}"
                if getattr(E.C,'PRINT_FIRST_ACTIVE',False):
                    # LD-220 has no standard mounting ears. Keep its case and
                    # cable reservation as one Manifold-unioned collision
                    # envelope, and place the assistant disk on the same parent
                    # link as the case. The printed main disk is an integrated
                    # feature of the child link, represented separately below.
                    from make_print_first_leg import (ld220_case_mesh,
                        raw_metal_horn_mesh_for_kind, metal_horn_mesh)
                    case=ld220_case_mesh(kind)
                    case.apply_transform(frame)
                    parts[link].append((case,'#444444',f'leg_{leg.lower()}_{kind}_ld220_case'))
                    assistant=raw_metal_horn_mesh_for_kind(kind, 'assistant')
                    assistant.apply_transform(frame)
                    parts[link].append((assistant,'#6f7680',
                                        f'leg_{leg.lower()}_{kind}_servo_horn_assistant'))
                    child=('leg_'+leg.lower()+'_coxa' if kind=='yaw' else
                           'leg_'+leg.lower()+'_femur' if kind=='pitch' else
                           'leg_'+leg.lower()+'_tibia')
                    main=metal_horn_mesh(kind,'main')
                    if leg in ('FR','RL'):
                        main.vertices[:,1]*=-1.0
                        main.invert()
                    parts[child].append((main,'#6f7680',
                                         f'leg_{leg.lower()}_{kind}_servo_horn_main'))
                else:
                    mounts.append((link,f'leg_{leg.lower()}_{kind}_servo_case',E.C.LEG_SERVO,frame))
        for tag in ('r','l'):
            for kind,frame in E.arm_servo_frames(tag).items():
                link='base_link' if kind=='yaw' else f"arm_{tag}_{'shoulder' if kind=='pitch' else 'upper'}"
                mounts.append((link,f'arm_{tag}_{kind}_servo_case',E.C.ARM_SERVO,frame))
        for idx, tag in ((0,'r'),(2,'l')):
            mounts.append(('base_link', f'eye_{tag}_servo_case', E.C.EYE_SERVO,
                           E.eye_servo_frame(idx)))
            parts['base_link'].append((E.eye_carrier_mesh(idx),'#444444',
                                       f'eye_carrier#{tag}'))
        for link,name,p,frame in mounts:
            cx=p['L']/2-p['SHAFT_OFF']
            m=trimesh.creation.box((p['L'],p['W'],p['TAB_BELOW']))
            m.apply_transform(frame@E.trans(-cx,0,-p['TAB_BELOW']/2))
            parts[link].append((m,'#444444',name))
    if getattr(E.C,'PRINT_FIRST_ACTIVE',False):
        # XIAO基板/カメラの実占有候補は、保持台を含む
        # eye_pod_camera リンクへ一度だけ追加する。基板は対象個体の
        # 実測前なので、既存 head shell の外形を削って「入った」とは扱わず、
        # 同リンク内部の候補として診断へ残す。
        from print_first_assembly import xiao_occupancy_meshes
        parts['eye_pod_camera'].extend(xiao_occupancy_meshes())
        # export_urdf bakes the same electronics reservations into base_link
        # collision meshes. Keep them in the convex replacement source too so
        # the compiled model cannot silently omit an all-link geometry.  This
        # is the sole collector entry point for these rows; callers only
        # select whether the exact rows are included in a comparison.
        component_rows = _print_first_component_rows()
        component_keys = {('base_link', str(_name))
                          for _mesh, _color, _name in component_rows}
        if include_components:
            parts['base_link'].extend(component_rows)
        else:
            for link, items in parts.items():
                items[:] = [row for row in items
                            if (str(link), str(row[2])) not in component_keys]
    _assert_unique_part_keys(parts)
    return parts


def _source_coverage_points(mesh):
    """Return deterministic surface samples used to bind VHACD cells.

    A cache which only contains a small hull inside the source bounds is
    self-consistent at the metadata level but is not a decomposition of the
    source.  Vertices and centroids alone can miss a hole in the middle of a
    large face, so every source triangle also gets a deterministic barycentric
    grid (including edge and interior points).  This is still a finite
    certificate; it is deliberately dense enough to reject under-coverage
    without running VHACD again.
    """
    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(mesh.faces, dtype=np.int64)
    if (vertices.ndim != 2 or vertices.shape[1] != 3
            or faces.ndim != 2 or faces.shape[1] != 3):
        raise ValueError('source coverage mesh arrays are malformed')
    triangles = vertices[faces]
    subdivisions = 4
    barycentric = np.asarray([
        (i / subdivisions, j / subdivisions,
         (subdivisions - i - j) / subdivisions)
        for i in range(subdivisions + 1)
        for j in range(subdivisions + 1 - i)
    ], dtype=np.float64)
    # [triangle, barycentric point, xyz].  The grid includes all three
    # vertices, edge midpoints/quarters, and interior points.
    surface_samples = np.einsum('fkc,pk->fpc', triangles, barycentric)
    centroids = triangles.mean(axis=1)
    points = np.vstack((vertices, centroids, surface_samples.reshape(-1, 3)))
    if not np.isfinite(points).all():
        raise ValueError('source coverage points are non-finite')
    # Duplicate rows do not add evidence and make the serialized digest
    # depend on incidental STL indexing, so keep a stable lexicographic set.
    points = np.unique(points, axis=0)
    return np.ascontiguousarray(points, dtype=np.float64)


def _convex_hull_equations(vertices, label):
    values = np.asarray(vertices, dtype=np.float64)
    if values.ndim != 2 or values.shape[1] != 3 or len(values) < 4:
        raise ValueError(f'{label}: convex hull vertices are malformed')
    try:
        equations = np.asarray(ConvexHull(values).equations, dtype=np.float64)
    except Exception as exc:  # noqa: BLE001 - qhull is a hard validation gate
        raise ValueError(f'{label}: convex hull equations cannot be rebuilt') from exc
    if (equations.ndim != 2 or equations.shape[1] != 4
            or not np.isfinite(equations).all()):
        raise ValueError(f'{label}: convex hull equations are malformed')
    return equations


def _source_coverage_report(source_mesh, hulls, label):
    """Recompute a source-surface coverage certificate from current geometry."""
    points = _source_coverage_points(source_mesh)
    covered = np.zeros(len(points), dtype=bool)
    for index, hull in enumerate(hulls):
        equations = _convex_hull_equations(
            np.asarray(hull.vertices), f'{label}[{index}]')
        distance = points @ equations[:, :3].T + equations[:, 3]
        inside = np.max(distance, axis=1) <= 0.011
        covered |= inside
    covered_count = int(np.count_nonzero(covered))
    report = {
        'status': 'PASS' if covered_count == len(points) else 'FAIL',
        'contract_version': 1,
        'point_count': int(len(points)),
        'covered_point_count': covered_count,
        'coverage_fraction': float(covered_count / max(1, len(points))),
        'source_coverage_points_sha256': _array_digest(points),
        'tolerance_mm': 0.011,
    }
    if covered_count != len(points):
        report['uncovered_point_indices'] = np.flatnonzero(~covered).astype(int).tolist()[:32]
    return report


def _battery_cradle_feature_decomposition(mesh, *, link='base_link',
                                           part='battery_cradle'):
    """Build exact convex cells for the canonical battery-cradle features.

    ``make_chassis.battery_cradle`` is an outer rounded box with four boolean
    feature families.  A generic VHACD run treats the cavity and the narrow
    flange/slot surfaces as one global concavity and, at its 32-hull limit,
    leaves part of the source surface uncovered.  This route reads the
    canonical STL, cuts its exact planar section topology at the z boundaries
    introduced by those CAD booleans, triangulates each material polygon, and
    extrudes every triangle through its feature layer.  A triangle prism is
    convex, and the polygon remains the source boundary including the
    faceted cylinder and bolt-hole walls.

    The helper is intentionally bound to the exact link/part name and to a
    fixed source-file SHA plus processed mesh SHA.  It therefore cannot be
    selected by a renamed part or silently applied to a modified STL.
    """
    if link != 'base_link' or part != 'battery_cradle':
        raise ValueError(
            'battery-cradle feature decomposition requires exact '
            'base_link/battery_cradle binding')
    if not BATTERY_CRADLE_SOURCE_STL.is_file():
        raise FileNotFoundError(
            f'battery-cradle canonical STL is missing: {BATTERY_CRADLE_SOURCE_STL}')
    actual_file_sha = _file_sha256(BATTERY_CRADLE_SOURCE_STL)
    if actual_file_sha != BATTERY_CRADLE_SOURCE_STL_SHA256:
        raise ValueError(
            'battery-cradle canonical STL SHA differs from the reviewed source: '
            f'{actual_file_sha} != {BATTERY_CRADLE_SOURCE_STL_SHA256}')
    try:
        canonical = trimesh.load(BATTERY_CRADLE_SOURCE_STL,
                                 force='mesh', process=True)
    except Exception as exc:  # noqa: BLE001 - source binding is fail-closed
        raise ValueError(f'battery-cradle canonical STL cannot be loaded: {exc}') from exc
    if not isinstance(canonical, trimesh.Trimesh):
        raise ValueError('battery-cradle canonical STL did not load as one mesh')
    _validated_outward_mesh(canonical, 'battery-cradle canonical source', source=True)
    canonical_mesh_sha = _mesh_digest(canonical)
    if canonical_mesh_sha != BATTERY_CRADLE_SOURCE_MESH_SHA256:
        raise ValueError(
            'battery-cradle canonical mesh SHA differs from the reviewed source: '
            f'{canonical_mesh_sha} != {BATTERY_CRADLE_SOURCE_MESH_SHA256}')
    if not isinstance(mesh, trimesh.Trimesh):
        raise ValueError('battery-cradle source binding is not a trimesh mesh')

    # ``export_urdf`` translates the canonical cradle only along z (ZB).  A
    # translation-derived comparison lets the same exact route serve both
    # legacy and print-first link frames while rejecting a moved/rotated or
    # re-meshed part under the same name.
    delta = np.asarray(mesh.bounds, dtype=np.float64).mean(axis=0) \
        - np.asarray(canonical.bounds, dtype=np.float64).mean(axis=0)
    if (delta.shape != (3,) or not np.isfinite(delta).all()
            or not np.allclose(delta[:2], 0.0, rtol=0.0, atol=1e-8)):
        raise ValueError(
            f'battery-cradle source placement is not the canonical z translation: '
            f'{delta.tolist()}')
    translated = canonical.copy()
    translated.apply_translation(delta)
    if (translated.faces.shape != mesh.faces.shape
            or not np.array_equal(translated.faces, mesh.faces)
            or not np.allclose(translated.vertices, mesh.vertices,
                               rtol=0.0, atol=1e-7)):
        raise ValueError(
            'battery-cradle source mesh geometry differs from the canonical '
            'STL after placement binding')

    # Imports are kept local because this route is only needed for one source
    # part.  ``trimesh.creation.triangulate_polygon`` uses the installed
    # mapbox-earcut backend and preserves every source polygon boundary.
    from shapely.affinity import translate as translate_polygon
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    canonical_z = np.asarray(canonical.vertices[:, 2], dtype=np.float64)
    required_levels = np.asarray([
        value for band in BATTERY_CRADLE_FEATURE_BANDS
        for value in band[1:3]
    ], dtype=np.float64)
    available_levels = np.unique(np.round(canonical_z, decimals=8))
    if any(not np.any(np.isclose(available_levels, level,
                                rtol=0.0, atol=1e-8))
           for level in required_levels):
        raise ValueError(
            'battery-cradle source z levels do not match the CAD boolean '
            f'boundaries: required={required_levels.tolist()} '
            f'available={available_levels.tolist()}')

    def section_polygons(section_z):
        section = canonical.section(
            plane_origin=[0.0, 0.0, section_z],
            plane_normal=[0.0, 0.0, 1.0])
        if section is None:
            raise ValueError(
                f'battery-cradle section is missing at z={section_z:g} mm')
        # trimesh 4.12 still exposes this conversion under the deprecated
        # name; suppress only its warning, never a geometry exception.
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', DeprecationWarning)
            planar, to_world = section.to_planar()
        rotation = np.asarray(to_world[:3, :3], dtype=np.float64)
        if not np.allclose(rotation, np.eye(3), rtol=0.0, atol=1e-8):
            raise ValueError(
                f'battery-cradle section frame is not XY at z={section_z:g} mm')
        polygons = []
        for polygon in planar.polygons_full:
            if not isinstance(polygon, Polygon) or not polygon.is_valid \
                    or not np.isfinite(polygon.area) or polygon.area <= 0.0:
                raise ValueError(
                    f'battery-cradle section polygon is invalid at z={section_z:g} mm')
            world = translate_polygon(
                polygon, xoff=float(to_world[0, 3]),
                yoff=float(to_world[1, 3]))
            if not world.is_valid or not np.isfinite(world.area) or world.area <= 0.0:
                raise ValueError(
                    f'battery-cradle world section polygon is invalid at '
                    f'z={section_z:g} mm')
            polygons.append(world)
        # Section entity order is deterministic for the bound source, but a
        # geometry-derived key makes that assumption visible and stable.
        polygons.sort(key=lambda p: (
            tuple(float(value) for value in p.bounds), float(p.area),
            p.wkb_hex))
        return polygons

    def polygon_union(polygons, label):
        """Build one independent 2D union and fail closed on invalid topology."""
        if not polygons:
            raise ValueError(f'{label}: polygon list is empty')
        union = unary_union(polygons)
        if (union.is_empty or not union.is_valid
                or not math.isfinite(float(union.area))
                or float(union.area) <= 0.0):
            raise ValueError(f'{label}: polygon union is invalid')
        return union

    def polygon_equivalence_report(source_union, candidate_union, label):
        """Measure both Boolean directions, independently of triangle areas."""
        source_minus_candidate = source_union.difference(candidate_union)
        candidate_minus_source = candidate_union.difference(source_union)
        symmetric_difference = source_union.symmetric_difference(candidate_union)
        source_minus_area = float(source_minus_candidate.area)
        candidate_minus_area = float(candidate_minus_source.area)
        symmetric_area = float(symmetric_difference.area)
        boundary_distance = float(
            source_union.boundary.hausdorff_distance(candidate_union.boundary))
        values = (source_minus_area, candidate_minus_area, symmetric_area,
                  boundary_distance)
        if not all(math.isfinite(value) and value >= 0.0 for value in values):
            raise ValueError(f'{label}: Boolean equivalence metrics are non-finite')
        status = (
            source_minus_area <= BATTERY_CRADLE_FEATURE_AREA_TOLERANCE_MM2
            and candidate_minus_area <= BATTERY_CRADLE_FEATURE_AREA_TOLERANCE_MM2
            and symmetric_area <= BATTERY_CRADLE_FEATURE_AREA_TOLERANCE_MM2)
        report = {
            'status': 'PASS' if status else 'FAIL',
            'source_minus_candidate_area_mm2': source_minus_area,
            'candidate_minus_source_area_mm2': candidate_minus_area,
            'symmetric_difference_area_mm2': symmetric_area,
            'source_area_mm2': float(source_union.area),
            'candidate_area_mm2': float(candidate_union.area),
            # This is a 2D boundary equality diagnostic.  It is not presented
            # as a 3D outside-distance or as a signed clearance measurement.
            'boundary_hausdorff_distance_mm': boundary_distance,
            'area_tolerance_mm2': BATTERY_CRADLE_FEATURE_AREA_TOLERANCE_MM2,
        }
        if not status:
            raise ValueError(
                f'{label}: source/triangle Boolean symmetric difference exceeds '
                f'{BATTERY_CRADLE_FEATURE_AREA_TOLERANCE_MM2} mm2: {report}')
        return report

    canonical_triangles = canonical.vertices[canonical.faces]
    face_cross = np.cross(
        canonical_triangles[:, 1] - canonical_triangles[:, 0],
        canonical_triangles[:, 2] - canonical_triangles[:, 0])
    face_cross_norm = np.linalg.norm(face_cross, axis=1)
    if (not np.isfinite(face_cross_norm).all()
            or np.any(face_cross_norm <= 1e-12)):
        raise ValueError('battery-cradle canonical source has degenerate faces')
    face_normal_z = np.abs(face_cross[:, 2] / face_cross_norm)
    face_z_min = canonical_triangles[:, :, 2].min(axis=1)
    face_z_max = canonical_triangles[:, :, 2].max(axis=1)
    face_z_span = face_z_max - face_z_min
    if not np.isfinite(face_normal_z).all():
        raise ValueError('battery-cradle canonical source face normals are non-finite')

    def constant_section_proof(feature, z_min, z_max, reference_union,
                                reference_z):
        """Prove that every open z interval in a feature band is prismatic.

        The CAD construction has only vertical side faces and horizontal faces
        at the listed boolean boundaries.  Checking that property on every
        source triangle, then comparing a section in every interval to the
        reference section by two-way Boolean difference, establishes the
        boundary-preserving prism assumption without relying on hull volume
        cancellation.
        """
        tol = BATTERY_CRADLE_FEATURE_FACE_Z_TOLERANCE_MM
        normal_tol = BATTERY_CRADLE_FEATURE_VERTICAL_NORMAL_Z_TOLERANCE
        overlap = ((face_z_max > z_min + tol)
                   & (face_z_min < z_max - tol))
        sloped = (overlap & (face_z_span > tol)
                  & (face_normal_z > normal_tol))
        horizontal_internal = (
            (face_z_span <= tol)
            & (face_z_min > z_min + tol)
            & (face_z_max < z_max - tol))
        if np.any(sloped):
            indices = np.flatnonzero(sloped).astype(int).tolist()[:16]
            raise ValueError(
                f'battery-cradle {feature} has sloped source faces in its '
                f'open z interval: {indices}')
        if np.any(horizontal_internal):
            indices = np.flatnonzero(horizontal_internal).astype(int).tolist()[:16]
            raise ValueError(
                f'battery-cradle {feature} has internal horizontal source faces: '
                f'{indices}')

        levels = np.unique(np.concatenate((
            np.asarray([z_min, z_max], dtype=np.float64),
            canonical_z[(canonical_z >= z_min - tol)
                        & (canonical_z <= z_max + tol)])))
        levels = sorted(float(level) for level in levels
                        if level >= z_min - tol and level <= z_max + tol)
        if not levels or not math.isclose(levels[0], z_min,
                                          rel_tol=0.0, abs_tol=tol) \
                or not math.isclose(levels[-1], z_max,
                                    rel_tol=0.0, abs_tol=tol):
            raise ValueError(
                f'battery-cradle {feature} does not have complete z boundaries: '
                f'{levels}')
        # Normalize tiny floating offsets at the two declared boundaries.
        levels[0], levels[-1] = float(z_min), float(z_max)
        interval_checks = []
        for low, high in zip(levels[:-1], levels[1:]):
            if high - low <= tol:
                continue
            probe = (low + high) / 2.0
            probe_union = polygon_union(
                section_polygons(probe),
                f'battery-cradle {feature} section at z={probe:g} mm')
            interval_report = polygon_equivalence_report(
                reference_union, probe_union,
                f'battery-cradle {feature} constant-section probe z={probe:g} mm')
            interval_checks.append({
                'z_interval_mm': [float(low), float(high)],
                'probe_z_mm': float(probe),
                **interval_report,
            })
        if not interval_checks:
            raise ValueError(f'battery-cradle {feature} has no z intervals')
        max_normal_z = float(face_normal_z[overlap & (face_z_span > tol)].max()) \
            if np.any(overlap & (face_z_span > tol)) else 0.0
        return {
            'status': 'PASS',
            'reference_section_z_mm': float(reference_z),
            'z_boundaries_mm': [float(level) for level in levels],
            'interval_count': len(interval_checks),
            'interval_checks': interval_checks,
            'source_face_check': {
                'sloped_face_count': int(np.count_nonzero(sloped)),
                'internal_horizontal_face_count': int(
                    np.count_nonzero(horizontal_internal)),
                'max_abs_normal_z_for_nonhorizontal_faces': max_normal_z,
                'face_z_tolerance_mm': tol,
                'vertical_normal_z_tolerance': normal_tol,
            },
        }

    def polygon_payload(polygon):
        def ring_payload(ring):
            return np.asarray(ring.coords, dtype=np.float64).tolist()
        return {
            'exterior': ring_payload(polygon.exterior),
            'interiors': [ring_payload(ring) for ring in polygon.interiors],
        }

    def triangle_prism(xy, z_min, z_max):
        xy = np.asarray(xy, dtype=np.float64)
        if xy.shape != (3, 2) or not np.isfinite(xy).all():
            raise ValueError('battery-cradle feature triangle is malformed')
        area2 = float(
            (xy[1, 0] - xy[0, 0]) * (xy[2, 1] - xy[0, 1])
            - (xy[1, 1] - xy[0, 1]) * (xy[2, 0] - xy[0, 0]))
        if not math.isfinite(area2) or abs(area2) <= 1e-10:
            raise ValueError('battery-cradle feature triangle is degenerate')
        if area2 < 0.0:
            xy = xy[::-1]
        vertices = np.vstack((
            np.column_stack((xy, np.full(3, z_min, dtype=np.float64))),
            np.column_stack((xy, np.full(3, z_max, dtype=np.float64))),
        ))
        faces = np.asarray([
            [0, 2, 1], [3, 4, 5],
            [0, 1, 4], [0, 4, 3],
            [1, 2, 5], [1, 5, 4],
            [2, 0, 3], [2, 3, 5],
        ], dtype=np.int64)
        prism = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
        if np.isfinite(prism.volume) and prism.volume < 0.0:
            prism.invert()
        return prism

    hulls = []
    cell_rows = []
    band_rows = []
    for feature, z_min, z_max, section_z in BATTERY_CRADLE_FEATURE_BANDS:
        polygons = section_polygons(section_z)
        source_section_union = polygon_union(
            polygons, f'battery-cradle {feature} source section')
        triangle_polygons = []
        triangle_count = 0
        for polygon_index, polygon in enumerate(polygons):
            try:
                tri_vertices, tri_faces = trimesh.creation.triangulate_polygon(
                    polygon, force_vertices=True)
            except Exception as exc:  # noqa: BLE001 - exact section is required
                raise ValueError(
                    f'battery-cradle section triangulation failed for {feature}: '
                    f'{exc}') from exc
            tri_vertices = np.asarray(tri_vertices, dtype=np.float64)
            tri_faces = np.asarray(tri_faces, dtype=np.int64)
            if (tri_vertices.ndim != 2 or tri_vertices.shape[1] != 2
                    or tri_faces.ndim != 2 or tri_faces.shape[1] != 3):
                raise ValueError(
                    f'battery-cradle triangulation arrays are malformed for {feature}')
            polygon_triangles = []
            area_sum = 0.0
            for triangle_index, indices in enumerate(tri_faces):
                xy = tri_vertices[indices]
                area2 = float(
                    (xy[1, 0] - xy[0, 0]) * (xy[2, 1] - xy[0, 1])
                    - (xy[1, 1] - xy[0, 1]) * (xy[2, 0] - xy[0, 0]))
                if not math.isfinite(area2) or abs(area2) <= 1e-10:
                    continue
                triangle_polygon = Polygon(xy)
                if not polygon.covers(triangle_polygon):
                    raise ValueError(
                        f'battery-cradle triangulation escapes the source polygon '
                        f'for {feature}[{polygon_index}/{triangle_index}]')
                triangle_area = abs(area2) / 2.0
                area_sum += triangle_area
                triangle_polygons.append(triangle_polygon)
                polygon_triangles.append((triangle_index, xy, triangle_area))
            if not math.isclose(area_sum, float(polygon.area),
                                rel_tol=0.0, abs_tol=1e-7):
                raise ValueError(
                    f'battery-cradle triangulation area differs for '
                    f'{feature}[{polygon_index}]: {area_sum} != {polygon.area}')
            polygon_sha = hashlib.sha256(json.dumps(
                polygon_payload(polygon), sort_keys=True,
                separators=(',', ':')).encode()).hexdigest()
            for triangle_index, xy, triangle_area in polygon_triangles:
                hull = triangle_prism(
                    xy, z_min + float(delta[2]), z_max + float(delta[2]))
                hull, _ = _validated_outward_mesh(
                    hull,
                    f'generated battery-cradle feature {feature} '
                    f'[{polygon_index}/{triangle_index}]')
                hull_index = len(hulls)
                hulls.append(hull)
                cell_rows.append({
                    'feature': feature,
                    'z_interval_mm': [z_min + float(delta[2]),
                                      z_max + float(delta[2])],
                    'section_z_mm': section_z + float(delta[2]),
                    'polygon_index': polygon_index,
                    'triangle_index': triangle_index,
                    'polygon_sha256': polygon_sha,
                    'triangle_area_mm2': triangle_area,
                    'hull_index': hull_index,
                    'hull_sha256': _hull_digest(hull),
                })
            triangle_count += len(polygon_triangles)
        triangle_section_union = polygon_union(
            triangle_polygons, f'battery-cradle {feature} triangle section')
        cross_section_boolean = polygon_equivalence_report(
            source_section_union, triangle_section_union,
            f'battery-cradle {feature} source/triangle section')
        constant_proof = constant_section_proof(
            feature, z_min, z_max, source_section_union, section_z)
        band_rows.append({
            'feature': feature,
            'source_z_interval_mm': [z_min, z_max],
            'z_interval_mm': [z_min + float(delta[2]), z_max + float(delta[2])],
            'section_z_mm': section_z + float(delta[2]),
            'polygon_count': len(polygons),
            'polygon_area_mm2': float(source_section_union.area),
            'triangle_count': triangle_count,
            'cross_section_boolean': cross_section_boolean,
            'constant_section_proof': constant_proof,
        })

    if not hulls:
        raise ValueError('battery-cradle feature decomposition is empty')
    source_coverage = _source_coverage_report(
        mesh, hulls, 'battery-cradle feature source coverage')
    if source_coverage.get('status') != 'PASS':
        raise ValueError(
            f'battery-cradle feature source coverage failed: {source_coverage}')
    source_volume = float(mesh.volume)
    hull_volume = float(sum(float(hull.volume) for hull in hulls))
    volume_difference = hull_volume - source_volume
    volume_tolerance = max(1e-6, abs(source_volume) * 1e-9)
    if abs(volume_difference) > volume_tolerance:
        raise ValueError(
            'battery-cradle feature cell volume differs from the source: '
            f'{volume_difference} mm3 > {volume_tolerance} mm3')

    # Centers of every void introduced by the CAD difference operations are
    # sampled independently of the surface certificate.  This catches a
    # future triangulation change that covers the boundary while plugging a
    # cable path or bolt hole.
    void_points = [
        ('inner_cavity', [0.0, -6.0, -16.0]),
        ('belt_slot_front', [0.0, -18.0, -30.5]),
        ('belt_slot_rear', [0.0, 6.0, -30.5]),
        ('top_opening_counterbore_layer', [0.0, -6.0, -3.0]),
        ('top_opening_through_hole_layer', [0.0, -6.0, -1.0]),
        ('bolt_hole_front_left', [-10.0, -20.0, -3.0]),
        ('bolt_hole_front_right', [10.0, -20.0, -3.0]),
        ('bolt_hole_rear_left', [-10.0, 8.0, -3.0]),
        ('bolt_hole_rear_right', [10.0, 8.0, -3.0]),
    ]
    hull_equations = [
        _convex_hull_equations(np.asarray(hull.vertices),
                               f'battery-cradle feature cell {index}')
        for index, hull in enumerate(hulls)
    ]
    void_rows = []
    for name, point in void_points:
        point = np.asarray(point, dtype=np.float64)
        point[2] += float(delta[2])
        occupied = [
            index for index, equations in enumerate(hull_equations)
            if float(np.max(point @ equations[:, :3].T + equations[:, 3])) <= 1e-9
        ]
        row = {'name': name, 'point_mm': point.tolist(),
               'occupied_hull_indices': occupied}
        void_rows.append(row)
        if occupied:
            raise ValueError(
                f'battery-cradle feature cells plug CAD void {name}: {occupied}')

    cell_payload = json.dumps(cell_rows, sort_keys=True,
                               separators=(',', ':')).encode()
    cell_rows_sha = hashlib.sha256(cell_payload).hexdigest()
    contract_payload = {
        'contract_version': BATTERY_CRADLE_FEATURE_CONTRACT_VERSION,
        'source_stl_sha256': BATTERY_CRADLE_SOURCE_STL_SHA256,
        'source_mesh_sha256': _mesh_digest(mesh),
        'canonical_source_mesh_sha256': canonical_mesh_sha,
        'translation_mm': np.asarray(delta, dtype=np.float64).tolist(),
        'tolerance_mm': BATTERY_CRADLE_FEATURE_TOLERANCE_MM,
        'bands': band_rows,
        'cell_count': len(hulls),
        'cell_rows_sha256': cell_rows_sha,
        'hull_sha256': [_hull_digest(hull) for hull in hulls],
        'source_coverage': source_coverage,
        'geometry_equivalence': {
            'status': 'PASS',
            'method': (
                'per-band two-way planar Boolean symmetric difference between '
                'the source section and the union of all feature triangles, '
                'plus source-face constant-section proof over every z interval'),
            'surface_sample_tolerance_mm': BATTERY_CRADLE_FEATURE_TOLERANCE_MM,
            'area_tolerance_mm2': BATTERY_CRADLE_FEATURE_AREA_TOLERANCE_MM2,
            'face_z_tolerance_mm': BATTERY_CRADLE_FEATURE_FACE_Z_TOLERANCE_MM,
            'vertical_normal_z_tolerance': (
                BATTERY_CRADLE_FEATURE_VERTICAL_NORMAL_Z_TOLERANCE),
            'bands': [
                {
                    'feature': row['feature'],
                    'cross_section_boolean': row['cross_section_boolean'],
                    'constant_section_proof': row['constant_section_proof'],
                }
                for row in band_rows
            ],
            'outside_distance_measured_mm': None,
        },
        'source_volume_mm3': source_volume,
        'hulls_sum_volume_mm3': hull_volume,
        'volume_difference_mm3': volume_difference,
        'volume_tolerance_mm3': volume_tolerance,
        'void_points': void_rows,
        'cad_origin': 'hardware/src/make_chassis.py::battery_cradle',
        'construction': (
            'rounded outer box minus inner cavity, top opening, belt slots, '
            'and four counterbored bolt holes; section boundaries are '
            'triangulated into convex prisms'),
    }
    feature_sha = hashlib.sha256(json.dumps(
        contract_payload, sort_keys=True, ensure_ascii=False,
        separators=(',', ':')).encode()).hexdigest()
    return hulls, {
        'status': 'PASS',
        **contract_payload,
        'feature_decomposition_sha256': feature_sha,
    }


def _validate_battery_cradle_feature_cache(path, expected_contract):
    """Check the feature binding stored beside a cached battery split."""
    if not isinstance(expected_contract, dict):
        raise ValueError('battery-cradle feature contract is missing')
    try:
        with np.load(path, allow_pickle=False) as saved:
            version_value = np.asarray(saved['battery_cradle_feature_contract_version'])
            feature_sha = np.asarray(saved['battery_cradle_feature_sha256'])
            source_sha = np.asarray(saved['battery_cradle_source_stl_sha256'])
            if version_value.ndim != 0 or int(version_value.item()) \
                    != BATTERY_CRADLE_FEATURE_CONTRACT_VERSION:
                raise ValueError('battery-cradle feature contract version is stale')
            if feature_sha.ndim != 0 or source_sha.ndim != 0:
                raise ValueError('battery-cradle feature cache metadata is not scalar')
            if feature_sha.item() != expected_contract.get(
                    'feature_decomposition_sha256'):
                raise ValueError('battery-cradle feature decomposition SHA differs')
            if source_sha.item() != BATTERY_CRADLE_SOURCE_STL_SHA256:
                raise ValueError('battery-cradle feature source STL SHA differs')
    except KeyError as exc:
        raise ValueError(
            f'battery-cradle feature cache metadata is missing: {exc.args[0]}') from exc
    except (TypeError, ValueError, OSError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith(
                'battery-cradle feature'):
            raise
        raise ValueError(f'battery-cradle feature cache metadata is malformed: {exc}') from exc


def _battery_cradle_independent_source():
    """Load the current base-link cradle with its only allowed placement.

    A public collision ledger may be replayed without the in-memory
    ``source_parts`` map.  In that case the cache must still be bound to the
    current source geometry and frame, rather than to its own self-reported
    source bounds.  ``export_urdf.base_link_parts`` places this mesh at the
    fixed base-link ZB translation; reproduce that one transform directly so
    this check does not build the rest of the robot.
    """
    try:
        source = E.load('battery_cradle')
        source.apply_transform(E.trans(0.0, 0.0, float(E.ZB)))
    except Exception as exc:  # noqa: BLE001 - independent binding is fail-closed
        raise ValueError(
            f'battery-cradle independent source binding failed: {exc}') from exc
    _validated_outward_mesh(
        source, 'independent battery-cradle source', source=True)
    return source


def convex_parts(mode='parts', cache=None, include_servos=False,foot_candidate_dir=None):
    """(body,part_name,material,hulls_mm)を返す。mode=parts/vhacd。"""
    # ``cache=CACHE`` as a default argument froze the repository path at
    # import time.  A runtime default is required for deterministic replay and
    # for tests that isolate their cache without mutating shared artifacts.
    if cache is None:
        cache = CACHE
    cache=Path(cache);cache.mkdir(parents=True,exist_ok=True)
    result=[];manifest=[]
    # Bump the cache definition whenever collision-cell construction changes;
    # old boxified thin-piece NPZs must never be silently reused.
    definition = collision_cache_definition(mode)
    definition_bytes=json.dumps(definition,sort_keys=True,
                                 separators=(',', ':')).encode()
    definition_sha256 = collision_cache_definition_sha(mode)
    parts=parts_with_pad(include_servos,foot_candidate_dir)
    # print-first の電装占有は parts_with_pad() の単一入口で追加済み。
    # ここで再追加すると、同じ source part が二重になり質量/衝突包絡と
    # geometry inventory の個数がずれるため、convex_parts() では追加しない。
    for link,items in parts.items():
        for mesh,_,name in items:
            # Raw source validation intentionally runs before orientation
            # normalization; a reversed STL must fail closed rather than be
            # silently repaired and counted as a verified input.
            mesh, source_validation = _validated_outward_mesh(
                mesh, f'source {link}/{name}', source=True)
            source_mesh_sha256 = _mesh_digest(mesh)
            # This independently reproducible convex hull binds a cache to
            # the current source geometry.  Bounds alone allow a fabricated
            # but self-consistent hull to masquerade as a cache hit.
            source_convex_hull, source_convex_validation = _validated_outward_mesh(
                mesh.convex_hull, f'source convex hull {link}/{name}')
            source_convex_hull_sha256 = _hull_digest(source_convex_hull)
            feature_hulls = None
            feature_contract = None
            if mode == 'vhacd' and link == 'base_link' \
                    and name == 'battery_cradle':
                feature_hulls, feature_contract = (
                    _battery_cradle_feature_decomposition(
                        mesh, link=link, part=name))
            digest=collision_cache_input_digest(mesh, definition)
            path=cache/(digest+'.npz')
            convex_hull, convex_validation = source_convex_hull, source_convex_validation
            ratio=convex_validation['volume_mm3']/max(source_validation['volume_mm3'],1e-12)
            thin_replacements=[]
            cache_needs_save = not path.exists()
            if path.exists():
                hulls, hull_validations = _load_validated_hulls(
                    path, f'cached hull {link}/{name}',
                    expected_source_mesh_sha256=source_mesh_sha256,
                    expected_definition_sha256=definition_sha256,
                    expected_source_convex_hull_sha256=source_convex_hull_sha256)
                if feature_contract is not None:
                    _validate_battery_cradle_feature_cache(path, feature_contract)
                    expected_hull_sha256 = feature_contract.get('hull_sha256')
                    actual_hull_sha256 = [_hull_digest(hull) for hull in hulls]
                    if actual_hull_sha256 != expected_hull_sha256:
                        raise ValueError(
                            'cached battery-cradle feature hull SHA differs from '
                            'the canonical feature decomposition')
            else:
                if feature_hulls is not None:
                    # The feature route is already a source-bound convex
                    # decomposition.  Do not pass it through generic VHACD or
                    # the thin-cell fallback; narrow curved-edge cells are
                    # intentional positive-volume prisms, not planar repairs.
                    hulls = feature_hulls
                elif mode=='vhacd' and ratio>1.05:
                    # 原点・単位は元STL(mm)のまま。各片の精度は別の監査で検査する。
                    args=trimesh.decomposition.convex_decomposition(mesh,**VHACD_SETTINGS)
                    # vhacdx already returns closed convex cells.  Rebuilding a
                    # second convex hull can turn a very thin, otherwise valid
                    # cell into a numerically open surface (observed for a
                    # battery-cradle cell).  Validate the actual returned cell
                    # below and keep that geometry as the collision hull.
                    hulls=[trimesh.Trimesh(**a) for a in args]
                else:
                    hulls=[convex_hull]
                if not hulls:
                    raise ValueError(f'generated hulls {link}/{name}: empty decomposition')
                checked_hulls=[]
                hull_validations=[]
                for i,hull in enumerate(hulls):
                    hull_label = f'generated hull {link}/{name}[{i}]'
                    try:
                        checked, validation = _validated_outward_mesh(hull, hull_label)
                    except ValueError as exc:
                        # Keep the existing explicit thin-piece policy, but
                        # apply it before strict volume/watertight validation
                        # so a zero-thickness VHACD cell cannot be silently
                        # dropped or abort a valid decomposition.
                        replacement, record = _thin_piece_replacement(hull, hull_label, exc)
                        checked, validation = _validated_outward_mesh(
                            replacement, f'thin-piece replacement {link}/{name}[{i}]')
                        record['hull'] = i
                        thin_replacements.append(record)
                    checked_hulls.append(checked)
                    hull_validations.append(validation)
                hulls=checked_hulls
            for i,h in enumerate(hulls):
                center=h.vertices.mean(axis=0)
                _,_,axes=np.linalg.svd(h.vertices-center,full_matrices=False)
                local=(h.vertices-center)@axes.T
                low,high=local.min(axis=0),local.max(axis=0)
                extent=high-low
                if extent.min()<.02 and feature_contract is None:
                    # Cache re-reads can contain a valid but planar cell as
                    # well.  Use the same footprint-prism helper as fresh
                    # VHACD output; never turn a large plane into an OBB.
                    replacement, record = _thin_piece_replacement(
                        h, f'cached thin-piece {link}/{name}[{i}]',
                        'cached hull has a sub-0.02 mm extent')
                    hulls[i], hull_validations[i] = _validated_outward_mesh(
                        replacement, f'thin-piece replacement {link}/{name}[{i}]')
                    record['hull'] = i
                    thin_replacements.append(record)
                    cache_needs_save = True
            # Recheck after thin-piece replacement and before every aggregate.
            hull_validations=[]
            for i,hull in enumerate(hulls):
                checked, validation = _validated_outward_mesh(
                    hull, f'final hull {link}/{name}[{i}]')
                hulls[i] = checked
                hull_validations.append(validation)
            source_coverage = (
                feature_contract['source_coverage']
                if feature_contract is not None else
                _source_coverage_report(mesh, hulls,
                                        f'source coverage {link}/{name}'))
            if mode == 'vhacd' and source_coverage.get('status') != 'PASS':
                raise ValueError(
                    f'generated/cache VHACD hulls do not cover source surface '
                    f'for {link}/{name}: {source_coverage}')
            # Save only the final validated hulls.  In particular, a generated
            # planar VHACD cell that needed the footprint-prism replacement
            # must not leave a different zero-thickness archive behind.
            if cache_needs_save:
                arrays={
                    'count': np.array(len(hulls)),
                    'cache_format': np.array(CACHE_FORMAT, dtype=np.int64),
                    'source_mesh_sha256': np.array(source_mesh_sha256),
                    'source_convex_hull_sha256': np.array(source_convex_hull_sha256),
                    'cache_definition_sha256': np.array(definition_sha256),
                    'source_bounds_mm': np.asarray(mesh.bounds, dtype=np.float64),
                    'hull_sha256': np.array([_hull_digest(hull) for hull in hulls]),
                }
                if feature_contract is not None:
                    arrays.update({
                        'battery_cradle_feature_contract_version': np.array(
                            BATTERY_CRADLE_FEATURE_CONTRACT_VERSION,
                            dtype=np.int64),
                        'battery_cradle_feature_sha256': np.array(
                            feature_contract['feature_decomposition_sha256']),
                        'battery_cradle_source_stl_sha256': np.array(
                            BATTERY_CRADLE_SOURCE_STL_SHA256),
                    })
                for i,h in enumerate(hulls):
                    arrays[f'v{i}']=h.vertices
                    arrays[f'f{i}']=h.faces
                _atomic_save_hulls(path,arrays)
            # part_material() は # suffix を扱うが、衝突専用の実占有包絡
            # (XIAO基板/カメラ子レンズを含む) は印刷PLAへ分類しない。
            material=collision_material(name)
            result.append((link,name,material,hulls))
            cache_sha256 = _file_sha256(path)
            hull_sha256 = [_hull_digest(hull) for hull in hulls]
            hull_sum=sum(validation['volume_mm3'] for validation in hull_validations)
            source_volume=source_validation['volume_mm3']
            source_bounds=mesh.bounds.tolist()
            hull_bounds=np.vstack([h.bounds for h in hulls]) if hulls else np.empty((0,3))
            source_z=(float(source_bounds[0][2]),float(source_bounds[1][2]))
            hull_z=(float(hull_bounds[:,2].min()),float(hull_bounds[:,2].max())) if len(hull_bounds) else (None,None)
            manifest.append({'link':link,'part':name,'material':material,'cache':_path_key(path),
                'cache_definition':definition,
                'input_mesh_sha256':digest,'source_mesh_sha256':source_mesh_sha256,
                'cache_sha256':cache_sha256,
                'cache_definition_sha256':definition_sha256,
                'source_convex_hull_sha256': source_convex_hull_sha256,
                'hull_sha256':hull_sha256,
                'hull_count':len(hulls),'source_volume_mm3':source_volume,
                'single_hull_volume_ratio':ratio,'hulls_sum_volume_mm3':hull_sum,
                'source_validation':source_validation,
                'source_convex_hull_validation':source_convex_validation,
                'hull_validations':hull_validations,
                # これは凸片体積の和と元体積の差であり、凸片間の重複や
                # 欠損を分離した幾何誤差ではない。名前にもその意味を残す。
                'hulls_sum_minus_source_volume_mm3':hull_sum-source_volume,
                'hulls_sum_minus_source_volume_fraction':(hull_sum-source_volume)/max(source_volume,1e-12),
                # 凹形状を凸包で塞いだ接地の近似量として、TPUだけ局所支持
                # 関数(z軸の最小/最大)を保存する。union体積とは解釈しない。
                'source_bounds_mm':source_bounds,'hulls_bounds_mm':[
                    [float(hull_bounds[:,j].min()),float(hull_bounds[:,j].max())]
                    for j in range(3)] if len(hull_bounds) else [],
                'support_z_source_range_mm':list(source_z),
                'support_z_hulls_range_mm':list(hull_z),
                'support_z_min_error_mm':None if hull_z[0] is None else hull_z[0]-source_z[0],
                'support_z_max_error_mm':None if hull_z[1] is None else hull_z[1]-source_z[1],
                'thin_piece_replacements':thin_replacements,
                'source_coverage': source_coverage,
                'feature_decomposition': feature_contract,
                'source_watertight':source_validation['watertight'],
                'source_winding_consistent':source_validation['winding_consistent'],
                'source_is_volume':source_validation['is_volume']})
    destination=cache/f'manifest-{mode}{"-servos" if include_servos else ""}{"-foot-candidate" if foot_candidate_dir else ""}.json'
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=cache,prefix=destination.name+'.',suffix='.tmp',delete=False) as stream:
            temporary=Path(stream.name);json.dump(manifest,stream,indent=2,ensure_ascii=False)
        os.replace(temporary,destination)
    finally:
        if temporary is not None and temporary.exists():temporary.unlink()
    manifest_sha256 = _file_sha256(destination)
    cache_entries=[]
    for row in manifest:
        cache_path = cache / f"{row['input_mesh_sha256']}.npz"
        # The cache path is known from the deterministic cache key even when
        # the public manifest location is represented as $OUTPUT/repo-relative.
        # Keep the actual Path private to the in-process ledger only.
        cache_entries.append({
            'link': row['link'], 'part': row['part'],
            'path': _cache_public_key(cache_path),
            'location': row['cache'],
            'sha256': row['cache_sha256'],
                'source_mesh_sha256': row['source_mesh_sha256'],
                'source_convex_hull_sha256': row['source_convex_hull_sha256'],
                'input_mesh_sha256': row['input_mesh_sha256'],
                'cache_definition_sha256': row['cache_definition_sha256'],
                'hull_sha256': row['hull_sha256'],
                'source_bounds_mm': row['source_bounds_mm'],
                'cache_definition': row['cache_definition'],
                'source_coverage': row.get('source_coverage'),
                'feature_decomposition': row.get('feature_decomposition'),
                '_cache_path': cache_path,
        })
    ledger={
        'version': 1,
        'manifest': {
            'path': _cache_public_key(destination),
            'location': _path_key(destination),
            'sha256': manifest_sha256,
            '_manifest_path': destination,
        },
        'entries': cache_entries,
        'entry_count': len(cache_entries),
        'definition': definition,
    }
    # 呼び出し側が同じ実行結果へ近似誤差・材料分類・キャッシュ来歴を
    # 転記できるよう、台帳を関数属性へ短時間だけ公開する。
    convex_parts.last_manifest_path = destination
    convex_parts.last_manifest = manifest
    convex_parts.last_cache_ledger = ledger
    return result


if __name__=='__main__':
    import argparse
    ap=argparse.ArgumentParser();ap.add_argument('--mode',choices=['parts','vhacd'],default='vhacd')
    a=ap.parse_args();r=convex_parts(a.mode)
    print('parts',len(r),'convex pieces',sum(len(row[3]) for row in r))
