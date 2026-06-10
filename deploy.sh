#!/usr/bin/env bash
# 服务器更新脚本（见 docs/specs/deployment.md §6）：拉代码 → 装依赖 → 构建前端 → 重启服务。
# 用法：在服务器上 cd /opt/market_monitor && ./deploy.sh
set -euo pipefail
cd "$(dirname "$0")"

git pull --ff-only
.venv/bin/pip install -r requirements.txt
( cd frontend && npm install && npm run build )   # 必须先构建再重启：/assets 挂载在 app 导入时决定
sudo systemctl restart market-monitor
sudo systemctl --no-pager status market-monitor
