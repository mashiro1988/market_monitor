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
# 必须先构建再重启：/assets 挂载在 app 导入时决定。
# venv 前置 PATH：npm build 的 generate:api-types 调裸 `python`（Ubuntu 无此命令，且脚本需要项目依赖），
# 不前置的话 set -e 会在构建步中止、永远走不到 restart（2026-07-09 线上踩坑）。
# npm ci 严格按 lockfile 安装且不改写它，避免不同 npm 版本让服务器工作树变脏并阻塞下次 git pull。
( cd frontend && npm ci && PATH="$(pwd)/../.venv/bin:$PATH" npm run build )
sudo systemctl restart market-monitor
sudo systemctl --no-pager status market-monitor
