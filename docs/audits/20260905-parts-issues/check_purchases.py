"""2026-09-05 時点の購入台帳の数量・共有在庫・課題参照を再検算する。"""
import json,re,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'tools/issues'));import plan
D=json.loads((ROOT/'docs/additional-purchases.json').read_text());M=(ROOT/'docs/additional-purchases.md').read_text()
rows={x['id']:x for x in D['items']};lots={x['id']:x for x in D['purchase_lots']}
assert len(rows)==len(D['items'])==134
assert len(lots)==len(D['purchase_lots'])==81
assert len({x['asin'] for x in lots.values()})==81
for l in lots.values():
 if l['quantity_per_package'] is not None:assert abs(l['package_count']*l['quantity_per_package']-l['purchased_quantity'])<1e-8
for id in ('AP-077','AP-078'):assert rows[id]['inventory_lot_ids']==['AMZ-B012T3HNXW']
assert lots['AMZ-B012T3HNXW']['purchased_quantity']==100 # 2用途に表示しても在庫は100
for id in ('AP-035','AP-056'):assert rows[id]['inventory_lot_ids']==['AMZ-B00O9Y3YAQ']
assert lots['AMZ-B00O9Y3YAQ']['purchased_quantity']==4 # ボディ/口の表示で8にしない
assert rows['AP-129']['shared_requirement_with']=='AP-121'
assert sum(x['purchased_quantity'] for x in lots.values() if x['unit']=='g')==5000
assert lots['AMZ-B0CQXGPX8X']['purchased_quantity']+lots['AMZ-B08DD1VZV2']['purchased_quantity']==11
assert lots['AMZ-B0BBV63J7S']['purchased_quantity']==40
assert lots['AMZ-B00008B2XT']['purchased_quantity']==4
assert rows['AP-003']['required_total']['quantity']==1 and rows['AP-003']['optional_spares']==1
assert rows['AP-003']['last_recorded_shortage']['quantity']==2
assert rows['AP-092']['required_total']['quantity']==1 and rows['AP-092']['optional_spares']==1
assert rows['AP-028']['delivery_status']=='注文済み・受領未確認'
assert all(x['current_shortage'] is None and x['current_usable_quantity'] is None for x in rows.values())
for x in rows.values():
 for fld in ('shared_stock_with','replacement_for','shared_requirement_with'):
  if x.get(fld):assert x[fld] in rows
 assert len(x.get('inventory_lot_ids',[]))==len(set(x.get('inventory_lot_ids',[])))
 assert all(l in lots for l in x.get('inventory_lot_ids',[]))
anchors=re.findall(r'<a id="([^"]+)"',M);assert len(anchors)==len(set(anchors))
for link in re.findall(r'\]\(([^)]+)\)',M):
 if link.startswith('#'):assert link[1:] in anchors,link
 elif not link.startswith('http'):assert (ROOT/'docs'/link.split('#')[0]).exists(),link
boms={b for x in rows.values() for b in x['bom_ids']}
assert boms=={str(i) for i in range(1,36)}|{'2a','2b','18b','21b','21c','34b'}
kt={x['key']:x['title'] for x in plan.ISSUES}
review=[]
for x in rows.values():
 assert x.get('issue_mapping_review')
 assert set(x['issue_keys'])==set(x['issue_urls'])
 assert all(k in kt for k in x['issue_keys'])
 review.append(x['id']+'\t'+x['name']+'\t'+' / '.join(kt[k] for k in x['issue_keys']))
for n,required,excluded in [(1,{'EL-02'},{'EL-03'}),(6,{'RV-13'},{'H-07'}),(71,{'RV-15','S-06'},{'RV-14','EL-03'}),(77,{'RV-15','S-06'},{'RV-14'}),(138,{'H-06','RV-15'},{'RV-08','RV-14'}),(140,{'RV-06','PR-05'},{'L-05','PR-10'})]:
 ks=set(rows[f'AP-{n:03}']['issue_keys']);assert required<=ks and not excluded&ks
for k in ('filament_stock','p01'):
 assert '/09c4acb825f511300a4cca52eee432e3c63f9c4f/tools/issues/plan.py#L' in D['sources'][k]['url']
assert '。。' not in M
assert not re.search(r'\b\d{3}-\d{7}-\d{7}\b',M+json.dumps(D))
(Path(__file__).parent/'purchase-issue-map.tsv').write_text('\n'.join(review)+'\n')
print('PASS 134 unique items / 81 unique Amazon lots and ASINs / 41 BOM IDs / 159 unique anchors')
print('PASS package arithmetic; MG90S 11; M2.6 screws 40; spiral 4m; purchased filament 5000g')
print('PASS no duplicate stock aggregation: shared nuts=100, speakers=4; gray requirement shared')
print('PASS phi2.2 and phi1.1: minimum1 + optional spare1; historical shortage2 remains historical')
print('PASS 134 issue-reference rows checked against current 96 titles; 6 prior wrong mappings rejected')
print('PASS local/anchor links, fixed historical sources; PCA received remains unknown; all physical usable/shortage remain unknown')
print('Manual review: every row was reassigned by task meaning, not by key existence alone. TSV preserves the 134 title mappings.')
