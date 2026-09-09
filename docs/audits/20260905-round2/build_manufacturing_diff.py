"""開始時バックアップと現STLを比較。入力/既存3MFは変更しない。"""
import hashlib, io, json, tarfile
from pathlib import Path
import numpy as np
import trimesh
from scipy.spatial import cKDTree
R=Path(__file__).resolve().parents[3];A=Path(__file__).resolve().parent
baseline=json.loads((A/'inventory-initial.json').read_text())['files']
backup=Path('/Users/uratayuuki/Documents/Tachikoma-audit-backups/20260905-153305-round2/round2-start.tar.gz')
def digest(data):return hashlib.sha256(data).hexdigest()
rows=[]
with tarfile.open(backup) as archive:
 for entry in baseline:
  name=entry['path']
  if not name.startswith('hardware/stl/') or not name.endswith('.stl'):continue
  current=(R/name).read_bytes()
  if digest(current)==entry['sha256']:continue
  prior=archive.extractfile(name).read()
  assert digest(prior)==entry['sha256'],name
  before=trimesh.load(io.BytesIO(prior),file_type='stl',force='mesh')
  after=trimesh.load(io.BytesIO(current),file_type='stl',force='mesh')
  delta=after.bounds.mean(axis=0)-before.bounds.mean(axis=0)
  aligned=after.copy();aligned.apply_translation(-delta)
  # 印刷形状の変化とSTL内の平行移動を区別。単なる頂点番号は比較しない。
  av,bv=aligned.vertices,before.vertices
  distance=max(cKDTree(av).query(bv)[0].max(),cKDTree(bv).query(av)[0].max())
  same=distance<=2e-5
  rows.append({'path':name,'before_sha256':entry['sha256'],'after_sha256':digest(current),
    'before_volume_mm3':float(before.volume),'after_volume_mm3':float(after.volume),
    'volume_change_mm3':float(after.volume-before.volume),'bbox_center_change_mm':delta.tolist(),
    'same_centered_vertex_set_0_00002mm':bool(same),'centered_vertex_distance_mm':float(distance),
    'classification':'placement_only' if same and np.linalg.norm(delta)>.001 else 'serialization_only' if same else 'geometry_changed',
    'after_watertight':bool(after.is_watertight),'after_winding_consistent':bool(after.is_winding_consistent),
    'after_components':len(after.split(only_watertight=False))})
(A/'manufacturing-diff.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n')
protected=[]
for entry in baseline:
 name=entry['path']
 if (name.startswith('model/') and name.endswith('.stl')) or entry['kind'] in ('print_project','media'):
  protected.append({'path':name,'kind':entry['kind'],'unchanged':digest((R/name).read_bytes())==entry['sha256']})
summary={'backup':str(backup),'stl_changes':len(rows),'geometry_changes':sum(r['classification']=='geometry_changed' for r in rows),
 'placement_only':sum(r['classification']=='placement_only' for r in rows),'serialization_only':sum(r['classification']=='serialization_only' for r in rows),
 'all_changed_stls_closed':all(r['after_watertight'] and r['after_winding_consistent'] for r in rows),
 'protected_files':len(protected),'all_protected_unchanged':all(r['unchanged'] for r in protected),'protected':protected}
(A/'manufacturing-preservation.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:v for k,v in summary.items() if k!='protected'},ensure_ascii=False,indent=2))
for r in rows:print(r['path'],r['classification'],round(r['volume_change_mm3'],3))
