from pathlib import Path
import hashlib,json,io,zipfile,xml.etree.ElementTree as ET
from datetime import datetime,timezone
import numpy as np,trimesh
ROOT=Path.cwd();B=ROOT/'docs/audits/20260905-round2';rows=[]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
code_findings={'tools/sim_physics.py':['SIM-01','SIM-02','SIM-09','SIM-10','SIM-11','SIM-12'], 'tools/sim_gait.py':['SIM-02','SIM-06'], 'tools/export_urdf.py':['SIM-03','SIM-04','SIM-05','SIM-08','SIM-14'], 'tools/check_urdf.py':['SIM-06'], 'tools/sim_collision.py':['SIM-07','SIM-09','SIM-13'], 'tools/sim_self_collision.py':['SIM-07'], 'tools/sim_stress.py':['SIM-01','SIM-02','SIM-09','SIM-10'], 'tools/sim_yaw_pack_search.py':['SIM-07']}
codepaths=[ROOT/p for p in code_findings]+list((ROOT/'tools/tests').glob('simulation_*'))+list((B/'simulation').glob('*.py'))
for p in sorted(set(codepaths)):
 if not p.is_file():continue
 rel=str(p.relative_to(ROOT));rows.append({'path':rel,'sha256':sha(p),'review_method':'全文通読・入力/座標/単位/判定分岐の追跡。新規コードは実行結果と照合。C++出力は実ヘッダーとのホスト回帰。','findings':code_findings.get(rel,[]),'evidence':['docs/audits/20260905-round2/simulation.md','docs/audits/20260905-round2/simulation/regression-final.log'],'limitations':'物理の実測パラメータ、実I/O、Isaac実行は対象外。'})
for p in sorted((ROOT/'hardware/urdf').rglob('*')):
 if not p.is_file():continue
 rel=str(p.relative_to(ROOT));row={'path':rel,'sha256':sha(p),'findings':[],'evidence':['docs/audits/20260905-round2/simulation/check-urdf-final.log']}
 if p.suffix=='.stl':
  m=trimesh.load(p);row.update(review_method='全STLを解析。有限頂点・面・体積符号・閉じた表面を計算。全ベイク形状とURDF FKはcheck_urdfで照合。',finite=bool(np.isfinite(m.vertices).all()),watertight=bool(m.is_watertight),volume_m3=float(m.volume),faces=len(m.faces));row['findings']=['表示メッシュの接合境界が非多様体'] if not m.is_watertight else []
 elif p.suffix=='.urdf':
  r=ET.parse(p);row.update(review_method='XML全構造・関節木・軸・原点・限界・質量慣性・全メッシュ参照を解析し、実source FKと比較。',joint_count=len(r.findall('joint')),link_count=len(r.findall('link')),mass_kg=sum(float(x.get('value')) for x in r.findall('.//mass')),findings=['SIM-03','SIM-04','SIM-06','SIM-08'])
 elif p.suffix=='.json':
  j=json.loads(p.read_text());row.update(review_method='JSON全文の部品名・リンク割当・個数を読解、生成源と照合。',findings=['SIM-04'])
 elif p.suffix=='.png':row.update(review_method='過去画像を表示して旧姿勢/注記を確認。初期inventoryとのhash一致を保持し、現在画像は別フォルダへ保存。',findings=['過去版表示画像。現行姿勢の根拠に使わない。'],evidence=['docs/audits/20260905-round2/simulation/restore-legacy-images.json'])
 else:row.update(review_method='バイナリの種類・サイズ・ハッシュ確認。内容の工学的保証はしない。')
 rows.append(row)
for p in (B/'current-render').glob('*.png'):
 rows.append({'path':str(p.relative_to(ROOT)),'sha256':sha(p),'review_method':'画像を表示し、firmware停止目標に対応する脚姿勢、表示部品、URDFと参照の同一視点を比較。','findings':[],'evidence':['docs/audits/20260905-round2/simulation.md'],'limitations':'静止描画。PWM量子化後の指令角姿勢はnative実交差診断で別に検証し、描画を内部サーボ収容や物理成立の証明に使わない。'})
for p in [ROOT/'docs/urdf.md',B/'simulation.md',ROOT/'tools/tests/firmware_stubs/Arduino.h',ROOT/'tools/tests/firmware_stubs/Wire.h',ROOT/'tools/tests/firmware_stubs/Adafruit_PWMServoDriver.h']:
 rows.append({'path':str(p.relative_to(ROOT)),'sha256':sha(p),'review_method':'全文通読。文書は今回実行結果へ更新、疑似I/OはPWM時刻/量子化/通信成功モデルを追跡。','findings':['SIM-01'] if p.suffix=='.h' else ['SIM-01','SIM-03','SIM-04','SIM-06','SIM-07','SIM-08','SIM-09'],'evidence':['docs/audits/20260905-round2/simulation.md','docs/audits/20260905-round2/simulation/regression-final.log']})
old=json.loads((B/'simulation/old-distributions.json').read_text())
for archive in old:
 for member in archive['files']:
  row={'path':member['path'],'sha256':member['sha256'],'review_method':member['review_method'],'findings':['履歴版: 現在の形状・慣性・支持・制御の根拠に使わない'],'evidence':['docs/audits/20260905-round2/simulation/old-distributions.json']}
  if row['path'].endswith('.md'):row['review_method']='全文通読。旧標準姿勢、質量仮定、自己衝突OFF、版とUIの断定、旧ライセンス記述を現仕様と対照。'
  if row['path'].endswith('.png'):row['review_method']='全画像を表示して比較。静止姿勢・注記の旧仕様を確認、動画や動力学検証とは扱わない。'
  rows.append(row)
p=ROOT/'tachikoma_urdf_20260822.zip'
rows.append({'path':str(p.relative_to(ROOT)),'sha256':sha(p),'review_method':'ZIPを非破壊で展開読込、全78メンバーのhash・形式・URDF意味差・メッシュ形状・全文文書・画像を検査。','findings':['現在の設計と不一致の過去配布物'],'evidence':['docs/audits/20260905-round2/simulation/old-distributions.json']})
result={'created_utc':datetime.now(timezone.utc).isoformat(),'reviewer':'simulation_audit','file_count':len(rows),'files':rows}
(B/'coverage-simulation.json').write_text(json.dumps(result,indent=2,ensure_ascii=False))
print('coverage',len(rows))
