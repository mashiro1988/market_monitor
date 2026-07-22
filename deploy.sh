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
  local keep="${MARKET_MONITOR_DB_BACKUP_KEEP:-10}"
  local ts
  ts="$(date -u +%Y%m%dT%H%M%SZ)"
  mkdir -p "$backup_dir"

  local py=".venv/bin/python"
  if [[ ! -x "$py" ]]; then
    py="python3"
  fi

  # 活跃写入下 sqlite3.Connection.backup() 会概率性产出"混时态"损坏快照（2026-07-22
  # 实证，见 docs/superpowers/specs/2026-07-22-deploy-backup-vacuum-into-design.md），
  # 改用 VACUUM INTO 并校验产物；失败则把产物改名 .corrupt 留现场、函数显式非零返回
  # （不依赖调用方 errexit，失败也绝不进入下面的保留清理），set -e 使部署在 git pull 之前中止。
  "$py" - "$db_path" "$backup_dir/market_monitor_${ts}.db" <<'PY' || return 1
import os
import sqlite3
import sys

src, dst = sys.argv[1], sys.argv[2]
try:
    con = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    con.execute("PRAGMA busy_timeout=30000")
    con.execute("VACUUM INTO ?", (dst,))
    con.close()
    chk = sqlite3.connect(f"file:{dst}?mode=ro", uri=True)
    result = [row[0] for row in chk.execute("PRAGMA integrity_check")]
    chk.close()
    if result != ["ok"]:
        raise RuntimeError(f"integrity_check: {result[:5]}")
except Exception:
    if os.path.exists(dst):
        os.replace(dst, dst + ".corrupt")
    raise
print(f"SQLite backup written & verified: {dst} ({os.path.getsize(dst) / 1e6:.0f} MB)")
PY

  # 校验通过后按 mtime 保留最近 keep 份；上面刚写入一份，glob 必有匹配（pipefail 安全），
  # .corrupt 后缀不匹配 *.db，不会被误删也不会被误当好备份。
  ls -1t "$backup_dir"/market_monitor_*.db | tail -n +$((keep + 1)) | xargs -r rm --
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
