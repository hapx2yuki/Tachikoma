#!/usr/bin/env bash
# 既存Projectを安全に同期。無引数は差分表示、--applyで不足分だけ追加。
# PROJECT_NUMBER=2 tools/issues/setup_project.sh --apply
# Status/レーンの選択肢、既存の進捗状態は変更しない。
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
exec "$ROOT/.venv/bin/python" "$HERE/sync_project.py" --project "${PROJECT_NUMBER:-2}" "$@"
