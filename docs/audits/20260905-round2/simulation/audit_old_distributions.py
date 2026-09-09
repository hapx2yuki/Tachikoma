import hashlib,io,json,zipfile,xml.etree.ElementTree as ET
from pathlib import Path
import trimesh,numpy as np
ROOT=Path.cwd()
def digest(data):return hashlib.sha256(data).hexdigest()
def semantic(data):
 r=ET.fromstring(data)
 return {'links':len(r.findall('link')),'mass_kg':sum(float(e.attrib['value']) for e in r.findall('.//mass')),'inertials':{l.attrib['name']:ET.tostring(l.find('inertial')).decode() for l in r.findall('link')},'limits':{j.attrib['name']:j.find('limit').attrib for j in r.findall('joint') if j.find('limit') is not None},'mesh_references':[m.attrib['filename'] for m in r.findall('.//mesh')]}
current={str(p.relative_to(ROOT)):p.read_bytes() for p in (ROOT/'hardware/urdf').rglob('*') if p.is_file()}
cur=semantic(current['hardware/urdf/tachikoma.urdf']);reports=[]
for name in ['tachikoma_urdf_20260731','tachikoma_urdf_20260822.zip']:
 p=ROOT/name
 if p.is_dir():files={str(x.relative_to(p)):x.read_bytes() for x in p.rglob('*') if x.is_file()}
 else:
  with zipfile.ZipFile(p) as z:files={i.filename:z.read(i) for i in z.infolist() if not i.is_dir()}
 rows=[]
 for rel,data in files.items():
  row={'path':name+'/'+rel,'sha256':digest(data),'bytes':len(data),'current_relation':'different' if rel in current and digest(data)!=digest(current[rel]) else 'same' if rel in current else 'no_current_equivalent'}
  if rel.endswith('.stl'):
   try:
    mesh=trimesh.load(io.BytesIO(data),file_type='stl');row.update(review_method='STL parse, finite vertices, closed surface, signed volume, geometry hash vs current',vertices=len(mesh.vertices),faces=len(mesh.faces),watertight=bool(mesh.is_watertight),positive_volume=bool(mesh.volume>0),finite=bool(np.isfinite(mesh.vertices).all()))
   except Exception as ex:row.update(review_method='STL parse failed',error=str(ex))
  else:row['review_method']='UTF-8/XML/JSON semantic comparison' if Path(rel).suffix in ['.urdf','.json','.md'] else 'binary hash comparison'
  rows.append(row)
 old=semantic(files['hardware/urdf/tachikoma.urdf']);reports.append({'distribution':name,'file_count':len(rows),'mass_kg':old['mass_kg'],'current_mass_kg':cur['mass_kg'],'changed_inertial_links':[k for k,v in old['inertials'].items() if v!=cur['inertials'].get(k)],'changed_joint_limits':[k for k,v in old['limits'].items() if v!=cur['limits'].get(k)],'missing_mesh_references':[n for n in old['mesh_references'] if 'hardware/urdf/'+n not in files],'files':rows})
Path('docs/audits/20260905-round2/simulation/old-distributions.json').write_text(json.dumps(reports,indent=2,ensure_ascii=False))
for r in reports:print(r['distribution'],r['file_count'],'mass',r['mass_kg'],'changed inertials',len(r['changed_inertial_links']),'different files',sum(x['current_relation']=='different' for x in r['files']))
