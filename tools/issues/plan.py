"""96課題の作業計画。sync_github_issues.py が本文・ラベル・依存を同期する。

個別の現在地/次作業/完了条件は audit_plan_data.py が正。
blocked_by は課題全体の完了が必要な条件だけを表す。混載プレートの一部引渡しや
共通本体/プリンタの予約は各本文で管理し、独立した部品の試験を止めない。
実機証拠が無い課題をソフト検証の合格だけでCloseしない。
"""
from copy import deepcopy
from audit_plan_data import (BODY_RESOURCE_KEYS, CLOSED_KEYS, DEPENDENCY_UPDATES, LABELS, ORIGINAL_METADATA,
                             PUBLISHED_EVIDENCE, PURCHASE_LABELS, REVIEW, TITLE_UPDATES)

REPO = 'hapx2yuki/Tachikoma'
MARKER = 'tachikoma-key'
BRANCH = 'codex/audit-20260905'
BASE = f'https://github.com/{REPO}/blob/{BRANCH}'
D = f'{BASE}/docs'
MILESTONES = [
    ('M0 準備完了', '実物の在庫・版・サーボ/基板寸法・許容電圧を照合し、電源/PCA/校正をベンチで確認。寸法の実測反映と部品別の製作条件を記録する。'),
    ('M1 片脚 Go/No-Go', 'FL標準1本を必要部品だけで組み、段階荷重と指定レバー/時間の保持・発熱・締結を実測する。合格前に残り3脚を量産しない。'),
    ('M2 頭無し歩行', '脱着できる仮電装と頭無し条件を明記し、支持台起動から立位/前進/旋回/停止を実測。全装備の成立とは区別する。'),
    ('M3 サブアセンブリ', '左右腕・眼・カメラ・裸音声を個別条件で確認。混載プレート全体の完了を単体試験の前提にせず、部品の版/寸法/保持を各受入側で検査する。'),
    ('M4 フルドレス', '頭内収納/頭支持/首Cabin/ガード/足支持/固定手を成立させ、電装の収納と保守経路を含む全装備を実物で組む。'),
    ('M5 統合・完成', '実重量/重心をモデルへ反映し、全装備歩行・会話・撮影・演出を実機で確認して完成媒体と手順を記録する。'),
]
FOOTER = (
    f'\n---\n運用ルール: [CONTRIBUTING.md]({BASE}/CONTRIBUTING.md) / '
    f'[全体計画]({D}/build_plan.md) / [96課題の見直し記録]({D}/issues-audit-20260905.md)。'
    '着手時に対象部品・使用版・担当と占有する本体/プリンタを記録する。'
    '完了時はこの課題の確認範囲に合う証拠を添付する。実物の試験は写真・測定値・動画、'
    'ソフト修正は再現・回帰・ビルドを区別し、既存担当/コメントを保持する。'
)
_PURCHASE_LABELS = PURCHASE_LABELS
_PRINT_LABELS = {
    'leg-frame': '脚骨格・足', 'leg-shell': '脚装飾', 'arm': '腕',
    'head-camera-audio': '頭・カメラ・音声', 'cabin-decor': '首・Cabin・意匠',
    'candidates': '未採用候補', 'plates': '既存3MFと印刷手順',
}


def _body(spec):
    key = spec['key']
    row = REVIEW[key]
    closed = key in CLOSED_KEYS
    parts = ['## 現在地（2026-09-05 全件見直し）', row['current'],
             f"進捗: **{row['progress']}**。" + ('既存のClosedを維持する。' if closed else 'OPENを維持する。'),
             '## 次の具体作業', row['next_step'],
             '## 完了条件と根拠', f"- [{'x' if closed else ' '}] {row['completion']}"]
    if 'res/プリンタ' in spec['labels']:
        parts += ['プリンタは1台。依存が解けた部品だけ別名3MFへ分離し、対象名/数・STLと3MFのSHA・材・向き・壁/充填・スロットを確認して予約する。元3MFを上書きしない。']
    if 'res/本体' in spec['labels']:
        parts += ['本体を占有する作業は同時に行わず、無通電の組付け/配線と通電試験を引き渡し記録で切り替える。']
    if 'type/エピック' in spec['labels']:
        children = ['{{'+i['key']+'}}' for i in ORIGINAL_METADATA if i['parent']==key]
        parts += ['子課題: '+ ' / '.join(children) + '。親の完了は子の結果を確認して判断する。']
    purchases = ' / '.join(f'[{_PURCHASE_LABELS.get(a,a.upper())}]({D}/additional-purchases.md#{a})' for a in row['purchase'])
    if not purchases:
        purchases = f'[追加購入の全品目]({D}/additional-purchases.md)。この課題だけを理由とする新規購入は確定していない'
    printing = ' / '.join(f'[{_PRINT_LABELS[a]}]({D}/additional-printing.md#{a})' for a in row['printing'])
    if not printing:
        printing = f'[追加印刷一覧]({D}/additional-printing.md)。この単体のソフト/裸配線確認に新規印刷は要求しない'
    parts += ['## 購入・印刷との対応', f'購入の照合先: {purchases}。', f'印刷の照合先: {printing}。',
              '在庫の有無と追加の必要数を区別する。未採用候補は量産対象にしない。',
              f"現在の参照: [監査根拠]({D}/audits/20260905-round2/{row['evidence']}) / "
              f'[組立手順]({D}/assembly.md) / [配線]({D}/wiring.md)。']
    if key in PUBLISHED_EVIDENCE:
        parts += ['## 監査時点の固定根拠（履歴）',
                  ' / '.join(f'[{label}]({url})' for label,url in PUBLISHED_EVIDENCE[key]),
                  '固定コミット時点の結果を保持する。後の現物照合や新たな変更の合格を示すものではない。']
    return '\n\n'.join(parts)


ISSUES = deepcopy(ORIGINAL_METADATA)
for _spec in ISSUES:
    _key = _spec['key']
    _spec['title'] = TITLE_UPDATES.get(_key, _spec['title'])
    _spec['blocked_by'] = list(DEPENDENCY_UPDATES.get(_key, _spec['blocked_by']))
    if _key in BODY_RESOURCE_KEYS:
        _spec['labels'] = [l for l in _spec['labels'] if l != '並行作業OK']
        if 'res/本体' not in _spec['labels']:
            _spec['labels'].append('res/本体')
    _spec['audit_progress'] = REVIEW[_key]['progress']
    _spec['audit_state_proposal'] = 'closed' if _key in CLOSED_KEYS else 'open'
    _spec['audit_project_status_proposal'] = (
        'Done' if _key in CLOSED_KEYS else
        'Blocked' if any(k not in CLOSED_KEYS for k in _spec['blocked_by']) else
        'In Progress' if _key.startswith('RV-') or _key in ('E9', 'I-08', 'H-06', 'I-06') else
        'Todo' if _key.startswith('E') and _key[1:].isdigit() else 'Ready')
    _spec['body'] = _body(_spec)


def _validate():
    keys = [i['key'] for i in ISSUES]
    assert len(keys)==96 and len(keys)==len(set(keys)), '96キーの重複/欠落'
    assert set(keys)==set(REVIEW), '個別見直しの過不足'
    assert CLOSED_KEYS=={'RV-01','RV-02','RV-03','RV-04','RV-12'}, '既存Closedの変更'
    kset=set(keys);mset={m[0] for m in MILESTONES}
    lset={l[0] for l in LABELS}|{'good first issue','help wanted'}
    for i in ISSUES:
        assert i['parent'] is None or i['parent'] in kset
        assert i['milestone'] is None or i['milestone'] in mset
        assert set(i['labels'])<=lset
        assert len(i['blocked_by'])==len(set(i['blocked_by']))
        assert set(i['blocked_by'])<=kset and i['key'] not in i['blocked_by']
        assert '/blob/main/' not in i['body']
    graph={i['key']:i['blocked_by'] for i in ISSUES};state={}
    def dfs(k,path):
        state[k]=1
        for b in graph[k]:
            assert state.get(b)!=1, f"循環依存: {' -> '.join(path+[b])}"
            if state.get(b) is None:dfs(b,path+[b])
        state[k]=2
    for k in graph:
        if state.get(k) is None:dfs(k,[k])


_validate()
