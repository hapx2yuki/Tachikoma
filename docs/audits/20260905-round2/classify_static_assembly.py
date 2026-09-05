"""最終固定リンク内の交差を、独立した接合課題へ対応付ける。交差を免除しない。"""
import json
from pathlib import Path
from collections import Counter
A=Path(__file__).resolve().parent
source=A/'final-geometry/verify/static-all-pairs.json'
data=json.loads(source.read_text());rows=[]
for r in data['intersections']:
 names=' '.join(r['parts']);link=r['link']
 if link.startswith('arm_'):
  issue='RV-17';kind='固定手の接着/圧入代が未確認'
 elif 'Guard' in names and link.startswith('leg_'):
  issue='RV-16';kind='ガードの受け座/挿入口/骨格交差'
 elif link.startswith('leg_'):
  issue='RV-06';kind='トゥと硬足/TPUの接合公差と荷重支持'
 elif 'servo_case' in names or ('Head_Top' in names and 'pod_neck' in names):
  issue='RV-09';kind='頭内機構または頭/梁の実体交差'
 elif 'Head_Top' in names and ('Head_Insert' in names or 'Head_Screw' in names):
  issue='S-08';kind='頭装飾の既存推定配置と接合面'
 elif 'Cabin' in names or 'TailJoint' in names:
  issue='RV-15';kind='Cabin/首/装飾の相対位置と全長嵌合'
 elif 'Mouth' in names:
  issue='RV-08';kind='小さいキー嵌合の公差/保持を要確認'
 else:
  raise ValueError(r)
 rows.append({**r,'issue_key':issue,'classification':kind,'resolved':False})
result={'input':str(source.relative_to(A)),'all_reported_intersections':len(rows),'counts_by_issue':dict(Counter(r['issue_key'] for r in rows)),
 'errors':data['errors'],'note':'接着/圧入/柔軟材料の意図を確定できないものも含む。99件が99個の独立欠陥という意味ではない。部分修正を全体合格へ拡張しない。','rows':rows}
(A/'static-intersection-final-triage.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:v for k,v in result.items() if k!='rows'},ensure_ascii=False,indent=2))
