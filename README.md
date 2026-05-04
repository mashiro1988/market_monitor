# Market Monitor

本地单人使用的宏观市场监控交易台。Python 负责扫描器、告警、数据库和 Dune 封装；FastAPI 提供唯一 REST 数据入口；React/Vite/TypeScript 提供 `http://localhost:8000` 的现代前端界面。

## 功能

- 市场概览：资产卡片、5m/1h/24h 涨跌幅、跨资产走势、明细表和 CSV。
- 新闻快讯：Jin10/Bloomberg 双栏信息流，支持来源、LLM 分数、重要标志、关键词和时间窗口筛选。
- 预测市场：Polymarket 宏观主题 family 图和单市场明细。
- 告警设置：查看规则、发送企业微信测试消息、查看告警日志。
- 新闻标注：用价格告警窗口选择候选新闻并保存人工标注。
- 链上数据：保留 ETH Dune API，前端暂为占位页。

## 快速开始

```bash
pip install -r requirements.txt
python run.py setup
cd frontend
cmd /c npm.cmd install
cd ..
python run.py frontend-build
python run.py app
```

应用地址：`http://localhost:8000`

开发模式：

```bash
python run.py api-dev
cd frontend
cmd /c npm.cmd run dev
```

Vite 开发服务器会把 `/api` 代理到 `http://127.0.0.1:8000`。

## 常用命令

| 命令 | 说明 |
|---|---|
| `python run.py app` | 构建缺失的 `frontend/dist`，启动 FastAPI，服务 React SPA，并打开 `http://localhost:8000` |
| `python run.py api-dev` | FastAPI reload 开发服务 |
| `python run.py frontend-build` | 执行前端 Vite 构建 |
| `python run.py scan` | 执行一次价格、新闻、预测市场扫描并评估告警 |
| `python run.py schedule` | 启动独立定时扫描器 |
| `python run.py setup` | 初始化/补齐数据库表 |

## 配置

复制或创建 `.env`，按需填写：

```bash
DEEPSEEK_API_KEY=
WECHAT_WORK_WEBHOOK=
DUNE_API_KEY=
DUNE_QUERY_ID_ETH_TOP100_NETFLOW=
DUNE_QUERY_ID_ETH_DAILY_STATS=
DUNE_QUERY_ID_ETH_CEX_DAILY_INOUT=
PROXY_URL=http://127.0.0.1:4780
```

主要运行配置在 [config.py](D:/market_monitor/config.py)：

- `SCAN_INTERVALS` 控制价格、新闻、预测市场扫描频率。
- `ALERT_RULES` 控制价格异动、重要新闻、预测市场异动和 hourly summary。
- `MARKET_OVERVIEW_DEFAULT_SYMBOLS` 同时用于市场默认走势图和 hourly summary。
- Dune 查询 ID 用于 `/api/onchain/eth/*`，服务层有 60 分钟内存缓存。

## 架构

```text
React/Vite SPA
  -> /api/*
FastAPI routes
  -> services
services
  -> SQLAlchemy models / config / integrations
scanners + alerts
  -> price_snapshots / news_items / prediction_markets / alert_logs
```

关键目录：

- [api](D:/market_monitor/api)：FastAPI app、routes、统一错误响应、DB 依赖。
- [services](D:/market_monitor/services)：市场、新闻、预测、告警、标注、Dune、任务服务。
- [schemas](D:/market_monitor/schemas)：Pydantic API contract。
- [frontend](D:/market_monitor/frontend)：React/Vite/TypeScript 前端。
- [scanners](D:/market_monitor/scanners)：价格、新闻、预测市场扫描器和外部源。
- [alerts](D:/market_monitor/alerts)：告警规则评估和通道。
- [models](D:/market_monitor/models)：SQLAlchemy ORM。

更细的结构和数据流请看 [ARCHITECTURE.md](D:/market_monitor/ARCHITECTURE.md) 与 [DATAFLOW.md](D:/market_monitor/DATAFLOW.md)。

## API

核心接口：

- `GET /api/health`, `GET /api/status`
- `POST /api/tasks/scan`, `GET /api/tasks/{task_id}`
- `GET /api/market/latest`, `/history`, `/table`, `/table.csv`
- `GET /api/news`
- `GET /api/predictions`, `/predictions/families`
- `GET /api/alerts/rules`, `/alerts/logs`, `POST /api/alerts/test-wechat`
- `GET /api/annotations/price-rules`, `/symbols`, `/windows`, `/context-news`, `POST /api/annotations`
- `GET /api/onchain/eth/top100-netflow`, `/daily-stats`, `/cex-flows`

错误响应统一为：

```json
{ "code": "ERROR_CODE", "message": "Human readable message", "details": {} }
```

时间字段统一包含 UTC 和北京时间：

```json
{ "timestamp_utc": "2026-05-03T00:30:00", "timestamp_bj": "2026-05-03 08:30:00" }
```

## 测试

后端：

```bash
python -m pytest
```

前端：

```bash
cd frontend
cmd /c npm.cmd run typecheck
cmd /c npm.cmd run build
```

Streamlit 清理检查：

```bash
rg -n "streamlit|st\.|streamlit_autorefresh" -g "*.py" -g "requirements.txt"
```

应无结果。

## 说明

`frontend/dist` 和 `frontend/node_modules` 不提交。`python run.py app` 会在 `dist` 缺失时尝试自动构建；如果依赖未安装或构建失败，会明确报错并退出。

本系统仅供本地研究和监控使用，不构成投资建议。
