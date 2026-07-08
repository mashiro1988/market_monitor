#!/usr/bin/env bash
# 服务器更新脚本（见 docs/specs/deployment.md §6）：拉代码 → 装依赖 → 构建前端 → 重启服务。
# 用法：在服务器上 cd /opt/market_monitor && ./deploy.sh
set -euo pipefail
cd "$(dirname "$0")"

backup_sqlite() {
  local db_path="${MARKET_MONITOR_DB_PATH:-market_monitor.db}"
  if [[ ! -f "$db_path" ]]; then
    echo "SQLite 数据库不存在，跳过备份: $db_path"
    return
  fi

  local backup_dir="${MARKET_MONITOR_DB_BACKUP_DIR:-backups}"
  local ts
  ts="$(date -u +%Y%m%dT%H%M%SZ)"
  mkdir -p "$backup_dir"

  local py=".venv/bin/python"
  if [[ ! -x "$py" ]]; then
    py="python3"
  fi

  "$py" - "$db_path" "$backup_dir/market_monitor_${ts}.db" <<'PY'
import sqlite3
import sys

src, dst = sys.argv[1], sys.argv[2]
with sqlite3.connect(src) as source, sqlite3.connect(dst) as target:
    source.backup(target)
print(f"SQLite backup written: {dst}")
PY
}

backup_sqlite
git pull --ff-only
.venv/bin/pip install -r requirements.txt
( cd frontend && npm install && npm run build )   # 必须先构建再重启：/assets 挂载在 app 导入时决定
sudo systemctl restart market-monitor
sudo systemctl --no-pager status market-monitor
