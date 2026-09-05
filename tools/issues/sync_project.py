#!/usr/bin/env python3
"""既存Projectへ不足するIssueだけ追加する。既存のStatus・選択肢を保持。"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import plan
from sync_github_issues import gh, load_existing


def initial_status(spec, states):
    if states.get(spec['key']) == 'closed':
        return 'Done'
    if 'type/エピック' in spec['labels']:
        return 'Todo'
    if any(states.get(key) != 'closed' for key in spec['blocked_by']):
        return 'Blocked'
    return 'Ready'


def status_change(current, spec, states):
    # 手動のBlocked/Todoも保持する。
    return None if current else initial_status(spec, states)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--project', type=int, default=2, help='既存Project番号。新規作成は行わない')
    ap.add_argument('--apply', action='store_true', help='未指定は差分表示だけ')
    ap.add_argument('--keys', nargs='+', help='対象Issueキー')
    args = ap.parse_args()
    specs = plan.ISSUES
    if args.keys:
        unknown = set(args.keys) - {s['key'] for s in specs}
        if unknown:
            ap.error(f'不明なキー: {sorted(unknown)}')
        specs = [s for s in specs if s['key'] in args.keys]
    owner = plan.REPO.split('/')[0]
    project = json.loads(gh('project', 'view', str(args.project), '--owner', owner, '--format', 'json'))
    fields = json.loads(gh('project', 'field-list', str(args.project), '--owner', owner,
                           '--limit', '100', '--format', 'json'))['fields']
    status = next(f for f in fields if f['name'] == 'Status')
    status_options = {o['name']: o['id'] for o in status['options']}
    snapshot = json.loads(gh('project', 'item-list', str(args.project), '--owner', owner,
                             '--limit', '1000', '--format', 'json'))
    if snapshot['totalCount'] > len(snapshot['items']):
        raise RuntimeError('Projectが1000件を超えるため、取得漏れを防いで停止')
    items = {it.get('content', {}).get('url'): it for it in snapshot['items']}
    existing = load_existing()
    states = {key: value['state'] for key, value in existing.items()}
    changes = 0
    for spec in specs:
        issue = existing.get(spec['key'])
        if issue is None:
            print(f"SKIP {spec['key']}: GitHub Issue未作成")
            continue
        url = f"https://github.com/{plan.REPO}/issues/{issue['number']}"
        item = items.get(url)
        if item is None:
            print(f"ADD {spec['key']} {url}")
            changes += 1
            if args.apply:
                item = json.loads(gh('project', 'item-add', str(args.project), '--owner', owner,
                                      '--url', url, '--format', 'json'))
        desired = status_change((item or {}).get('status'), spec, states)
        if desired is not None:
            option = status_options.get(desired, status_options.get('Todo'))
            if option is None:
                raise RuntimeError('Statusに必要な選択肢が無い。既存フィールドを保持して停止')
            print(f"INIT {spec['key']} Status={desired if desired in status_options else 'Todo'}")
            changes += 1
            if args.apply:
                gh('project', 'item-edit', '--project-id', project['id'], '--id', item['id'],
                   '--field-id', status['id'], '--single-select-option-id', option)
    print(f"{'APPLIED' if args.apply else 'DRY RUN'}: {changes} changes; existing statuses/options preserved")


if __name__ == '__main__':
    main()
