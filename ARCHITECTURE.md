# 架构地图 - market_monitor

> 本地单用户宏观市场监控台。Python 扫描器 + 告警写 SQLite；FastAPI 在 `http://localhost:8000` 上服务 React/Vite SPA。最近一次基于代码扫描确认：2026-07-02（**标注 Phase3a**：`causal_role = driver / redundant / noise`，`post_hoc_explanation` / `contradictory` 退场并入 noise；`market_reaction_type` 仅历史兼容；自动标注 prompt `v8-20260702`；宏观对标 `references` 带同期涨跌 + 相关性；前端只画 driver marker；pytest 根目录收集固定到 `tests/`；板块扫描 pending retry 已实现；独立 `schedule` CLI 已移除；代理仍为 `config.py:32` import-time 探测）。
>
> **维护契约：** 结构性变更（新增模块、移动函数、改变数据形状、改变依赖边、改入口点）必须在**同一次 commit** 内更新本文件。基于全新代码扫描更新，不要凭记忆。

## 分层

```
React/Vite SPA  (frontend/)  页面：Market、板块轮动(Sectors)、News、Predictions、Alerts、Annotations、Onchain
      v fetch /api/*
FastAPI app     (api/app.py, api/routes.py)
      v
services/       (读侧：market、news、prediction、alerts、annotation、onchain、task、sector
                 + 远程数据：remote_fs、remote_puller、cmc_client)
      v
models/  +  scanners/(price、news、prediction、sector)  +  alerts/  +  config.py  +  database.py
      v
SQLite (market_monitor.db) + 外部 API (yfinance、OKX、CoinGecko、Eastmoney、Jin10、CNBC RSS、Polymarket Gamma、DeepSeek、Dune、WeCom webhook)
      + 远程 BMAC 数据中心 (SFTP root@47.243.252.92) + CoinMarketCap API
```

依赖方向：**frontend -> api -> services -> {models, scanners, alerts, config, database, remote_fs}**。

**两条并行的数据摄入路径**：
1. **5min 扫描循环**（原有）：`run_scan_once()` -> price/news/prediction scanner -> SQLite -> alert。
2. **远程数据周期**（新增，独立 APScheduler job）：`remote_data_cycle` -> SFTP 拉 BMAC pkl -> sector_scanner 算板块涨跌 -> `sector_returns` 表。两条路径用各自的 `max_instances=1` 锁，互不阻塞。
3. **FastAPI 每小时 settle job**：`gap_repair_cycle` 每小时 :37 跑缺口自愈 + `traditional_open` 回填 + `news_tagging`，由 FastAPI lifespan 注册（`api/app.py:168`）。

已知违规：`api/app.py -> run.py`（见"已知结构问题"）；板块管道内有受控的延迟 import 环（见"已知结构问题"第 11 条）。

## 模块清单

### 顶层 Python 文件

| 文件 | 行数 | 职责 |
|---|---|---|
| `run.py` | ~486 | CLI 入口；扫描锁；`app` / `api-dev` / `frontend-build` / `setup` / `scan` / `refresh-sectors` 子命令；`app` 启 FastAPI lifespan 调度器，`scan` 只跑单次扫描。 |
| `config.py` | ~486 | 静态配置（API key、扫描间隔、价格 / 新闻源、Polymarket、告警规则、保留策略、**REMOTE_\* / CMC_\* / SECTOR_WHITELIST**）；代理仍在 import 时探测 `config.py:32`，入口读取 `config.PROXY`。 |
| `database.py` | ~229 | SQLAlchemy engine + WAL pragma + 建表 + 轻量 SQLite schema 修补 + 标注标签迁移（v1/v2/v2.1 -> Phase3a）。 |
| `chart_utils.py` | 39 | UTC <-> 北京时间互转、价格归一化辅助函数。 |

### 包

| 包 | 文件 | 职责 |
|---|---|---|
| `alerts/` | `engine.py` (614)、`rules.py`、`channels/{console,wechat_work}.py` | 规则加载、规则评估、冷却、派发、写告警日志。 |
| `api/` | `app.py` (271)、`routes.py`、`deps.py`、`errors.py` | FastAPI 工厂、约 25 条路由、DB 依赖、统一错误响应；lifespan 里启动扫描 / 远程数据 / gap_repair / cmc_bootstrap。 |
| `models/` | `price.py`、`news.py`、`prediction.py`、`alert_log.py`、`sector.py` | SQLAlchemy ORM：`PriceSnapshot`、`NewsItem`、`NewsPriceAnnotation`、`PredictionMarket`、`AlertLog`、**`CmcSymbolCategory`、`SectorReturn`**。 |
| `scanners/` | `base.py`、`price_scanner.py` (212)、`news_scanner.py` (338)、`prediction_scanner.py`、`scorer.py` (217)、**`sector_scanner.py` (369)**、`sources/{yfinance,okx,coingecko,eastmoney_bond,jin10,rss,polymarket}` | 价格 / 新闻 / 预测 fetch -> 标准化 -> 持久化；scorer 用 DeepSeek 评分；**sector_scanner 读本地 BMAC pivot 缓存算板块等权涨跌**。 |
| `services/` | `market_service.py` (274)、`news_service.py`、`prediction_service.py`、`alerts_service.py`、`annotation_service.py`、`onchain_service.py`、`task_service.py`、`pagination.py`、`time_utils.py`、**`sector_service.py` (223)、`remote_fs.py` (456)、`remote_puller.py` (286)、`cmc_client.py` (286)** | 读侧查询、schema 映射、涨跌幅、family 分组、任务注册表、Dune 缓存；**sector_service 板块榜单/钻取读侧；remote_fs SFTP 客户端；remote_puller 远程数据周期 job；cmc_client CMC 板块映射**。 |
| `schemas/` | `common`、`market`、`news`、`predictions`、`alerts`、`annotations`、`tasks`、`onchain`、**`sectors`** | Pydantic API 契约；`Page[T]`、双 UTC + 北京时间字段。 |
| `frontend/` | React 18 + TS + Vite + TanStack Query + Recharts；页面：Market、**板块轮动(Sectors)**、News、Predictions、Alerts、Annotations、Onchain | 单页应用，从 `frontend/dist` 静态托管。 |
| `onchain_data/` | `__init__.py`、`dune_queries.py` (412) | Dune Analytics 封装（ETH top-100 净流入、日统计、CEX 流量、月度交易数）。被 `services/onchain_service.py` 通过 `importlib` 加载。2026-05-04 从 `市场监控/` 重命名而来。 |
| `tests/` | 31 个 pytest 模块 | 告警、源、扫描器、过滤、回填、评分、标注、预测、API 的单元 + 集成测试；`pytest.ini` 把根目录收集限定到 `tests/`。 |

## 入口点

| 命令 | 行为 | 代码 |
|---|---|---|
| `python run.py app` | 必要时构建 `frontend/dist`，打开浏览器，对 `api.app:app` 启 uvicorn（**调度器开**）。 | `run.py:226` |
| `python run.py api-dev` | 对 `api.app:dev_app` 启 uvicorn，开 reload，**调度器关**。 | `run.py:241` |
| `python run.py frontend-build` | 在 `frontend/` 下跑 `npm run build`。 | `run.py:203` |
| `python run.py setup` | 仅建表。 | `run.py:248` |
| `python run.py scan` | 单次扫描周期（价格 -> 新闻 -> 预测 -> 告警 -> 滚动回填）。 | `run.py:256` |
| `python run.py refresh-sectors` | 强制刷新 CMC 板块映射缓存（无视 7 天 TTL，调 `cmc_client.refresh_categories(force=True)`，~2min）。改 `SECTOR_WHITELIST` 后必须跑。 | `run.py:394` |
| 无参 | 交互式菜单分发以上命令。 | `run.py:432` |
| `npm run dev`（frontend/ 下） | Vite 开发服务器 `:5173`，`/api` 反代到 `:8000`。 | `frontend/package.json` |

代码中只有一处 `if __name__ == "__main__":`，在 `run.py`。`api/app.py:193` 的 FastAPI lifespan 在 `python run.py app` 模式下执行同样的扫描 + 调度逻辑。

## 主调用链（一次扫描周期）

1. CLI 分发 -> `execute("scan")` -> `run_scan_once()` `run.py:256`
2. 获取跨进程文件锁 `_scan_lock()` `run.py:119`
3. `PriceScanner().scan()` `scanners/price_scanner.py:26` -> 各源（yfinance、OKX、CoinGecko 兜底、Eastmoney）-> `_save_records` -> `price_snapshots`
4. `NewsScanner().scan()` `scanners/news_scanner.py:36` -> Jin10 + CNBC RSS -> 上一个已收口 5m 窗口 -> `NewsScorer.enrich_batch()` `scanners/scorer.py:80`（DeepSeek）-> `_save_records` -> `news_items`
5. `PredictionScanner().scan()` `scanners/prediction_scanner.py:21` -> Polymarket Gamma -> `_save_records` -> `prediction_markets`（同时把上一条 DB 记录的概率写入 `prev_probability`）
6. `AlertEngine().evaluate_all(...)` `alerts/engine.py:48` -> price_change / price_level / news_importance / prediction_shift -> 查 `alert_logs` 做冷却 -> `_dispatch` 到 console + WeCom -> 落 `alert_logs`
7. `_run_rolling_backfill()` `run.py:303` -> 价格 + 新闻回填最近 `SCAN_ROLLING_BACKFILL_INTERVALS` 个已收口窗口（仅落库，不触发告警）

## 主调用链（一次远程数据周期 remote_data_cycle）

并行于 5min 扫描，FastAPI lifespan 内的独立 APScheduler job（默认 1h 触发；`api/app.py:158`）。

1. APScheduler 触发 `remote_data_cycle` job（`api/app.py:159`）-> `run_remote_data_cycle()` `services/remote_puller.py:316`
2. `get_puller().cycle()` `services/remote_puller.py:147`：遍历 `PHASE1_DATASETS`（`remote_puller.py:76`），每个 dataset 按自己的 `poll_interval_seconds` 用 `_next_check_at` 闸门判断是否到期（pivot 1h，spot_swap_matches 1 天）
3. 到期的 dataset 走 `_pull_if_newer()` `remote_puller.py:209`：`remote_fs.find_latest_ready()` 看 `.ready` cutoff -> 比上次记录新才 `remote_fs.pull()` `services/remote_fs.py:361`（SFTP + 原子写 `os.replace`）
4. 若 `market_pivot_spot/swap` 有更新（`PIVOT_DATASETS_TRIGGERING_SCAN` `remote_puller.py:102`）或上次同 cutoff 下游失败留有 `pending_sector_retry_cutoff_ts`（`remote_puller.py:115`）-> 同步调 `_run_sector_scan()` `remote_puller.py:196`（延迟 import 避免环）；只有 `sectors_written > 0` 才清 pending（`remote_puller.py:214`）
5. `SectorScanner().scan()` `scanners/sector_scanner.py:315` -> `compute_all_sector_returns()` `sector_scanner.py:233`：读本地 pivot 缓存 + 查 `cmc_symbol_categories` -> `normalize_pivot_symbol()` 归一 -> 各板块等权涨跌 -> DELETE 同 snapshot_at 旧行 + 写 `sector_returns`
6. CMC 映射由独立的 `cmc_bootstrap` job（启动 +10s 一次性）保证：`cmc_client.needs_refresh()` 检查 7 天 TTL，过期才 `refresh_categories()` `services/cmc_client.py:172`

## 主调用链（一次板块榜单 API 请求，以 /api/sectors/leaderboard 为例）

1. 前端 `api.sectorLeaderboard()` `frontend/src/api/client.ts:146` -> `GET /api/sectors/leaderboard`
2. 路由 `sectors_leaderboard` `api/routes.py:372` -> `sector_service.get_leaderboard(db)` `services/sector_service.py:80`
3. **读 `sector_returns` 表**最新 snapshot_at 的所有行（不现算），按 ret_24h 降序 -> `SectorLeaderboardResponse`
4. 钻取 `/api/sectors/{category}/tokens` -> `get_sector_tokens()` `sector_service.py:134`：**现算** —— 从 pivot 缓存（mtime cache）算该板块成员币的当前涨跌

## 主调用链（一次 API 请求，以 /api/market/latest 为例）

1. 浏览器从 `frontend/src/api/client.ts:69` 发 `fetch('/api/market/latest')`
2. uvicorn -> FastAPI 路由 `market_latest` `api/routes.py:94`
3. 依赖 `get_db()` `api/deps.py:10` 打开 SQLAlchemy session
4. `market_service.get_latest_prices(db)` `services/market_service.py:67` 查 `price_snapshots`，计算 5m / 1h / 24h 涨跌幅，映射成 `MarketLatestResponse` `schemas/market.py`
5. 返回 JSON，含双 UTC + 北京时间字段

## 已知结构问题

| # | 问题 | 位置 |
|---|---|---|
| 1 | **反向依赖：`api/` import 自 `run.py`。** `api/app.py` 不应该依赖 CLI 入口。把 `next_aligned_run_time` / scan / startup-backfill 抽到一个共享模块，让两边都 import 它。独立 `schedule` CLI 已移除，调度只在 FastAPI lifespan。 | `api/app.py:27`、`run.py:256` |
| 2 | **God file：`alerts/engine.py` 614 行**，把规则加载、三个 evaluator、对 DB 算价格窗口、冷却、派发、整点摘要全揉在一起。 | `alerts/engine.py` |
| 3 | **God file：`run.py` ~486 行**，把 CLI、锁、扫描编排、回填全揉在一起。 | `run.py` |
| 4 | **扫描器直接写 DB**，没走 services / 持久层。当前可接受，但把扫描器和 ORM 耦在一起了。 | `scanners/price_scanner.py`、`scanners/news_scanner.py`、`scanners/prediction_scanner.py` |
| 5 | **`AlertEngine` 直接 `get_session()` 读 `price_snapshots`**，没走 `services/`。 | `alerts/engine.py:71`、`alerts/engine.py:195` |
| 6 | **用 `importlib` 动态 import**：`services/onchain_service.py` 通过 `importlib.import_module("onchain_data.dune_queries")` 加载 Dune 模块。目录已 ASCII 化且是正规包，可以改为静态 `from onchain_data import dune_queries`。 | `services/onchain_service.py:27` |
| 7 | **代理探测仍在 `config.py` import 时发生。** `config.py:32` 会探测代理，`run.py:25` 根据 `config.PROXY` 设置 HTTP(S)_PROXY；这是全局进程状态，测试或一次性 import 仍会付出 socket 探测代价。 | `config.py:32`、`run.py:25` |
| 8 | **任务注册表只在内存里。** `task_service._TASKS` 和 `_RUNNING_SCAN_ID` 是进程局部全局变量；多 worker uvicorn 会丢失任务。本地单用户假设下没问题，但限制了部署形态。 | `services/task_service.py:15` |
| 9 | **没有正式 migration。** `database._ensure_sqlite_schema()` 用裸 `ALTER TABLE` 修补 schema。本地 SQLite 单用户场景下可接受；如要变就先文档化。 | `database.py:34` |
| 10 | **评分硬绑 DeepSeek。** 没有 protocol / ABC 让 LLM scorer 可替换。 | `scanners/scorer.py:46` |
| 11 | **板块管道有受控的延迟 import 环。** `remote_puller._run_sector_scan()` 在函数体内 `import SectorScanner`；`sector_scanner._load_per_symbol_returns()` 在函数体内 `import sector_service._load_pivot_cached`。两处都是为了打破 `remote_puller -> sector_scanner -> sector_service -> remote_fs` 的模块加载环，故意延迟到运行时。能用，但说明这三个模块的边界耦得偏紧。 | `services/remote_puller.py:196`、`scanners/sector_scanner.py` 内 `_load_per_symbol_returns` |
| 12 | **`remote_fs` 直接 `os.getenv` 读 SFTP 凭据**，没走 `config.py`（`config.REMOTE_*` 也存在但 remote_fs 没用它，独立读 env 以不依赖 config import 顺序）。两处真相源，改 env 名时要同时改。 | `services/remote_fs.py`（`_connect_kwargs`）、`config.py:308` |
| 13 | **板块/category 管道全套地图与代码都是 2026-05-17 新增，但因子页（FactorsPage / services/factors.py）只在本地分支 `feat/remote-data-integration`，未合并 main，地图不覆盖。** 见 PENDING.md。 | 本地分支 |
| 14 | **板块扫描失败没有同 cutoff 重试。** `remote_puller._pull_if_newer()` 在下载成功后先推进 `last_cutoff_ts`，随后 `_run_sector_scan()` 失败只返回错误；若没有新 `.ready` cutoff，下一轮不会重跑同一份 pivot。 | `services/remote_puller.py:186`、`services/remote_puller.py:196`、`services/remote_puller.py:241` |

这些不是阻塞问题。记录在这里只是让结构变更是有意识的，而不是无意中发生的。

## 另见

- [DATAFLOW.md](DATAFLOW.md) - 数据形状、磁盘布局、生产者 / 消费者表。
- [DECISIONS.md](DECISIONS.md) - 日期排序的 ADR 风格架构决策日志。
- [PENDING.md](PENDING.md) - 跨会话交接：按风险分级的待办。
- [AGENTS.md](AGENTS.md) - 维护规则。
- [ARCHITECTURE.html](ARCHITECTURE.html) / [DATAFLOW.html](DATAFLOW.html) - 浏览器可视化（Mermaid 单文件，双击打开）。
