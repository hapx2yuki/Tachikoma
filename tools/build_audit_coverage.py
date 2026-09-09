#!/usr/bin/env python3
"""担当別の実検査記録を開始時ファイル一覧と照合する。読解した事実は生成しない。"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def current_digest(name):
    path = ROOT / name
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    # 保存URDFアーカイブの内部記録も、現ZIPから読んだ内容で照合する。
    parts = Path(name).parts
    for index, part in enumerate(parts[:-1]):
        archive = ROOT.joinpath(*parts[:index + 1])
        if part.lower().endswith('.zip') and archive.is_file():
            try:
                with zipfile.ZipFile(archive) as bundle:
                    return hashlib.sha256(bundle.read('/'.join(parts[index + 1:]))).hexdigest()
            except (KeyError, zipfile.BadZipFile):
                return None
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audit-dir', type=Path, required=True)
    args = parser.parse_args()
    folder = args.audit_dir.resolve()
    baseline = json.loads((folder / 'inventory-initial.json').read_text())['files']
    records = defaultdict(list)
    for source in sorted(folder.glob('coverage-*.json')):
        if source.name in ('coverage-complete.json', 'coverage-missing-interim.json'):
            continue
        data = json.loads(source.read_text())
        for record in data.get('files', []):
            records[record['path']].append({'record': source.name, **record})
    media_path = folder / 'media-review/manifest.json'
    for record in json.loads(media_path.read_text()):
        records[record['path']].append({'record': 'media-review/manifest.json', **record})
    rows = []
    for entry in baseline:
        digest = current_digest(entry['path'])
        reviews = records[entry['path']]
        current = digest is not None and any(r.get('sha256') == digest for r in reviews)
        rows.append({**entry, 'status': 'reviewed_current' if current else 'refresh_required' if reviews else 'unreviewed',
                     'current_sha256': digest, 'changed_since_start': digest != entry['sha256'],
                     'review_records': reviews, 'has_review_record': bool(reviews),
                     'current_hash_reviewed': current})
    missing = [r['path'] for r in rows if not r['has_review_record']]
    stale = [r['path'] for r in rows if r['has_review_record'] and not r['current_hash_reviewed']]
    old_paths = {r['path'] for r in rows}
    additional = []
    for name in sorted(set(records) - old_paths):
        digest = current_digest(name)
        current = digest is not None and any(r.get('sha256') == digest for r in records[name])
        additional.append({'path': name, 'current_sha256': digest, 'current_hash_reviewed': current,
                           'review_records': records[name]})
    additional_stale = [r['path'] for r in additional if not r['current_hash_reviewed']]
    result = {'baseline_files': len(rows), 'baseline_kinds': dict(Counter(r['kind'] for r in rows)),
              'covered_files': sum(r['has_review_record'] for r in rows),
              'missing_review': missing, 'pending_current_hash_refresh': stale,
              'additional_reviewed_files': additional,
              'additional_pending_current_hash_refresh': additional_stale,
              'method_note': '読解/構造解析/実メッシュ検査/静止画目視/動画抽出を区別。'
              '動画全フレームの目視や不在の元Downloads3MFの確認を主張しない。'
              '実行環境/Git内部は対象外。担当記録の存在は全欠陥の不存在を意味しない。', 'files': rows}
    (folder / 'coverage-complete.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: (len(v) if k == 'additional_reviewed_files' else v)
                      for k, v in result.items() if k != 'files'}, ensure_ascii=False, indent=2))
    return int(bool(missing or stale or additional_stale))


if __name__ == '__main__':
    sys.exit(main())
