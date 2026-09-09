#!/usr/bin/env python3
"""メーカーSTEPを組立変換込みで計測する。OCPの別環境が必要。

PYTHONPATH=/tmp/tachikoma-step-runtime .venv/bin/python tools/measure_xiao_step.py
STEP/STLは旧カメラ世代の参考であり、現購入品の実測値ではない。
"""
import json,hashlib,sys,zipfile,tempfile
import numpy as np
import trimesh
from pathlib import Path
from OCP.STEPCAFControl import STEPCAFControl_Reader
from OCP.TDocStd import TDocStd_Document
from OCP.TCollection import TCollection_ExtendedString
from OCP.XCAFDoc import XCAFDoc_DocumentTool
from OCP.TDF import TDF_LabelSequence,TDF_Label
from OCP.TDataStd import TDataStd_Name
from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib
from OCP.TopLoc import TopLoc_Location
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.StlAPI import StlAPI_Writer
ROOT=Path(__file__).resolve().parents[1]
ARCHIVE=ROOT/'docs/audits/20260905-round2/primary-sources/xiao-esp32s3-sense-3d_model.zip'
WORK=tempfile.TemporaryDirectory(prefix='tachikoma-step-')
STEP=Path(WORK.name)/'xiao.step'
with zipfile.ZipFile(ARCHIVE) as archive:
 STEP.write_bytes(archive.read('Seeed Studio XIAO-ESP32-S3-Sense.step'))
D=TDocStd_Document(TCollection_ExtendedString('xiao'))
r=STEPCAFControl_Reader();r.ReadFile(str(STEP));r.Transfer(D)
st=XCAFDoc_DocumentTool.ShapeTool_s(D.Main());seq=TDF_LabelSequence();st.GetFreeShapes(seq)
out=ROOT/'docs/audits/20260905-round2/primary-sources/xiao-step-measured';out.mkdir(exist_ok=True)
records=[]
def name(l):
 a=TDataStd_Name()
 return a.Get().ToExtString() if l.FindAttribute(TDataStd_Name.GetID_s(),a) else ''
def walk(l,loc,path):
 if st.IsReference_s(l):
  ref=TDF_Label();st.GetReferredShape_s(l,ref)
  walk(ref,loc.Multiplied(st.GetLocation_s(l)),path);return
 n=name(l);p=path+[n];sh=st.GetShape_s(l).Moved(loc)
 b=Bnd_Box();BRepBndLib.AddOptimal_s(sh,b,False,False);bb=b.Get()
 rec={'path':p,'bounds_mm':[list(bb[:3]),list(bb[3:])],'dimensions_mm':[bb[i+3]-bb[i] for i in range(3)],'assembly':st.IsAssembly_s(l)}
 tr=loc.Transformation();rec['transform']=[[tr.Value(i,j) for j in range(1,5)] for i in range(1,4)]
 records.append(rec)
 if len(p)<=3 or 'JUSHUO' in n or 'Camer' in n:print(p,rec['bounds_mm'],rec['dimensions_mm'])
 if len(p)==2 or 'JUSHUO' in n or 'Camer' in n:
  filename=n.replace(' ','_').replace('/','_').replace('(','').replace(')','')+'.stl'
  BRepMesh_IncrementalMesh(sh,.03,False,.1,True);StlAPI_Writer().Write(sh,str(out/filename));rec['mesh']=filename
 if st.IsAssembly_s(l):
  cs=TDF_LabelSequence();st.GetComponents_s(l,cs)
  for j in range(1,cs.Length()+1):walk(cs.Value(j),loc,p)
for i in range(1,seq.Length()+1):walk(seq.Value(i),TopLoc_Location(),[])
(out/'assembly-bounds.json').write_text(json.dumps({'source_file':'Seeed Studio XIAO-ESP32-S3-Sense.step','sha256':hashlib.sha256(STEP.read_bytes()).hexdigest(),'units':'mm','method':'OCCT STEPCAFControl assembly transforms; BRepBndLib.AddOptimal; STL meshing linear0.03mm angle0.1rad','records':records},indent=2))

# 部品の箱は保守的な予約形状。STEPには開いた面もあり、そのままBoolean入力にしない。
package={}
for with_sd in [False,True]:
 leaves=[r for r in records if not r['assembly'] and 'Camer Module' not in r['path'] and (with_sd or r['path'][-1]!='MicroSD v2')]
 low=np.min([r['bounds_mm'][0] for r in leaves],axis=0);high=np.max([r['bounds_mm'][1] for r in leaves],axis=0)
 key='camera_removed_'+('with_sd' if with_sd else 'without_sd')
 package[key]={'bounds_mm':[low.tolist(),high.tolist()],'dimensions_mm':(high-low).tolist()}
 envelope=trimesh.creation.box(extents=high-low,transform=trimesh.transformations.translation_matrix((high+low)/2));envelope.export(out/(key+'_envelope.stl'))
 # PCBのX-Z平面→XY、厚み+Y→+Z。右手系維持のため元Zを-Yへ。
 T=np.eye(4);T[:3,:3]=[[1,0,0],[0,0,-1],[0,1,0]];T[:3,3]=-T[:3,:3]@((high+low)/2)
 package[key]['source_to_centered_flat_matrix']=T.tolist()
 package[key]['centered_flat_dimensions_mm']=[float(high[0]-low[0]),float(high[2]-low[2]),float(high[1]-low[1])]
connector=next(r for r in records if r['path'][-1]=='JUSHUO AFC01-S24FCA-00')
package['fpc_connector']={'bounds_mm':connector['bounds_mm'],'center_mm':np.mean(connector['bounds_mm'],axis=0).tolist(),'insertion_axis':'STEP X (sign must be checked on actual locking flap / cable; not asserted)','note':'2023 manufacturer assembly; opening-plane exact position and replacement OV3660 FPC free length UNVERIFIED'}
package['limitations']=['STEP contains open surfaces: component STLs are for viewing, box envelopes are watertight','USB protrusion included; plugged-in cable envelope is separate','No camera, no antenna lead, no external header solder joints in these envelopes','Use actual purchased board revision and FPC dimensions before releasing a holder']
(out/'camera-removed-summary.json').write_text(json.dumps(package,indent=2)+'\n')
for key,value in package.items():print(key,value)
WORK.cleanup()
