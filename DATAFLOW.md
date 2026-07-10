# 数据流地图 - market_monitor

> 运行时数据形状：磁盘上有什么、模块间运行时传什么、API 契约是什么。最近一次基于代码扫描确认：2026-07-10（**行为引擎 Phase 2**：标注窗口源固定为 0.5 档以上行为段（开关退役）、`PriceWindowSchema` 带段证据（`tier_idx/tier_max/s_scores/machine_class/human_class/cluster03_count`）、保存标注回写段 `human_class`（`AnnotationCreateRequest.window_class` 三类必选）、日汇总构成三类归并、S 读数统一 `rolling_peak`、**同步相关整链删除**（`window_signals.pearson_correlation`/`ReferenceChange.correlation` 均已不存在）、prompt **v12** 输出 `window_class`。承接 2026-07-09 Phase 1：`behavior_segments`/`behavior_daily_summaries`（PIT 追加）、`/api/behavior/*` 三端点、`behavior_cycle`/`behavior_daily_summary` 两个 job、retention `price_snapshots_days` 30→90；2026-07-02：标注 Phase3a 三角色、宏观对标带本身三段）。
>
> **维护契约：** 当运行时数据形状、持久化规则、生产者 / 消费者关系或外部集成发生改变时，必须在同一次 commit 内更新本文件。

## 磁盘布局

| 路径 | 内容 | 备注 |
|---|---|---|
| `market_monitor.db` | 单一 SQLite 数据库（WAL 模式） | 七张活跃表。`python run.py setup` 或首次启动时创建。 |
| `.scan.lock` | 跨进程扫描互斥的 PID 文件 | `_scan_lock()` 创建 `run.py:119`；Windows 通过 `_process_exists()` 检测僵尸 PID。 |
| `.pytest_tmp/` | pytest 临时目录 | `pytest.ini` 使用 `--basetemp=.pytest_tmp`，避免默认用户 Temp 在受限环境不可写；gitignore。 |
| `data/remote_cache/` | 从 BMAC 服务器 SFTP 拉下来的 pkl 缓存 | `remote_puller` 写，`sector_scanner` / `sector_service` 读。原子写（`.tmp` -> `os.replace`）。**gitignore。** 主要文件：`preprocess_1h_resample__30m__market_pivot_{spot,swap}_{YYYY}.pkl`、`exginfo__spot_swap_matches.pkl`。 |
| `data/remote_cache/.manifest.json` | 增量拉取状态 | 记录每个远程文件的 `{mtime, size, fetched_at}`，`remote_fs.Manifest` 维护，下次拉取时跳过未变文件。 |
| `frontend/dist/` | 已构建的 React SPA（assets、index.html） | `npm run build` 产出。FastAPI 在 `/` 提供，带 SPA fallback。`python run.py app` 在缺失时自动构建。 |
| `frontend/node_modules/` | 前端依赖 | 不入库。 |
| `.env` | 密钥 | API key、webhook URL、代理 URL、**REMOTE_\*（SFTP）、CMC_API_KEY**。`config.py` 加载（`remote_fs` 直接读 env）。 |

## 数据库表

所有 datetime 列都是 **UTC naive**（`tzinfo=None`）。北京时间只在展示层通过 `chart_utils.py` 计算。

### `price_snapshots`（`models/price.py`）

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | int PK | |
| `timestamp` | datetime | yfinance/OKX 是已收口 5m bar end；Eastmoney bond 是扫描时间（**不再用源端 `f86`**，避免收盘期停滞造成数据空洞，详见 DECISIONS 2026-05-04）；CoinGecko 兜底是采集时间。**与 `symbol` 联合唯一。** |
| `asset_class` | str | stock / crypto / bond / commodity。 |
| `symbol`, `name` | str | 例如 `BTC/USDT`、`NQ=F`。 |
| `price`, `prev_price`, `change_pct` | float | 源级 5m 变化，**不是**告警窗口。 |
| `volume` | float \| null | |
| `source` | str | `yfinance`、`okx_swap_5m`、`okx_spot_5m`、`coingecko_realtime`、`cnbc_bond_quote`（债券，2026-06-09 起；旧数据可能仍是 `eastmoney_bond_quote`）。 |
| `created_at` | datetime | 落库的物理时间。 |

索引：`(timestamp, symbol)`、`(asset_class, timestamp)`。

**写入方：** `PriceScanner._save_records`（`scanners/price_scanner.py`），实时扫描和回填都写。
**读取方：** `market_service.get_latest_prices` `services/market_service.py:65`、`market_service.get_history` `services/market_service.py:203`、`market_service.get_table` `services/market_service.py:253`、`AlertEngine._price_window_move_from_session` `alerts/engine.py:195`、`annotation_service.load_price_windows`。

### `news_items`（`models/news.py`）

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | int PK | |
| `timestamp` | datetime | **源发布时间**（不是落库时间）。`scanners/news_scanner.py:307`。 |
| `source`, `source_id` | str | `(source, source_id)` 是去重键。 |
| `title`, `content`, `url` | str | |
| `importance` | int \| null | 源侧标志（Jin10 的 "important"）。 |
| `llm_importance` | int \| null | DeepSeek 1-10 分。null 表示未评分（回填默认、或没有 API key）。 |
| `llm_importance_reason` | str \| null | LLM 解释。 |
| `llm_model`, `llm_scored_at` | str / datetime \| null | |
| `language`, `categories` | str \| null | |
| `created_at` | datetime | |

索引：`(timestamp)`、`(source, source_id)` 唯一。

**写入方：** `NewsScanner._save_records` `scanners/news_scanner.py`，加 `NewsScorer.enrich_batch` `scanners/scorer.py:80` 更新 LLM 字段。
**读取方：** `news_service.get_news`、`annotation_service.load_context_news_for_window`、`AlertEngine.evaluate_news`。

### `news_price_annotations`（`models/news.py`）

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | int PK | |
| `symbol`, `asset_class` | str | 当前仅 `BTC/USDT`、`ETH/USDT`、`NQ=F`。 |
| `window_start`, `window_end` | datetime | 标注覆盖的价格告警窗口。 |
| `context_start`, `context_end` | datetime | 取因果新闻的更宽窗口（自 2026-05-04 起：window_start - 15min / window_end + 30min；之前对称 ±30min）。 |
| `threshold_pct`, `price_start`, `price_end`, `change_pct` | float | |
| `causal_news_ids` | str (JSON) | `news_items.id` 列表。**Phase3a 起为派生兼容字段** = `news_roles` 里全部 `driver` 的 id。 |
| `candidate_news_ids` | str (JSON) | 标注时整个 context 窗口里**全部候选**新闻 ID（含未标的，作训练负样本）。2026-05-05 新增。 |
| `auto_reasoning` | str \| null | DeepSeek auto-annotate 的 `reasoning_content` 全文。null = 纯人工标注。2026-05-05 新增。 |
| `auto_summary` | str \| null | DeepSeek 返回的 summary 原文（与人改后的 `notes` 区分，便于后续训练区分 LLM 输出与人工修正）。2026-05-05 新增。 |
| `no_clear_news` | bool | **Phase3a 起为派生兼容字段** ⟺ 无 `driver`；历史兼容请求若显式带 `market_reaction_type = no_news_driver` 也会置 true。 |
| `news_roles` | str (JSON) \| null | **Phase3a**：`{news_id: causal_role}` 三分类（driver / redundant；noise 默认不落库），枚举见 `schemas/annotations.py:NEWS_CAUSAL_ROLES`。`post_hoc_explanation` / `contradictory` 已退场并入 noise。 |
| `market_reaction_type` | str \| null | 历史兼容字段（三分类 macro_policy / event_driven / no_news_driver）；前端新保存不再传，prompt 不再要求输出。 |
| `confidence` | float \| null | **v2**：0-1；新 Phase3a 保存请求（`news_roles` 非空/空字典都算）必填；null 仅表示 v1/legacy 迁移样本（导出时 `schema_version:1` 低保真标记）。 |
| `auto_news_roles` | str (JSON) \| null | AI 原始标注快照（人改前），与 `news_roles` 的差异 = 人机分歧难例。 |
| `prompt_version` | str \| null | 产生 auto_* 的提示词版本（当前 `annotation_service.ANNOTATION_PROMPT_VERSION = "v11-20260709"`，见 `services/annotation_service.py:509`）。 |
| `eval_set` | bool | 评估集冻结标记；训练导出（split=train）默认排除。 |
| `notes`, `labeler` | str | |
| `created_at`, `updated_at` | datetime | |

`(symbol, window_start, window_end)` 唯一。upsert 复用已存在的行。迁移在 `database.migrate_legacy_annotations`（启动时幂等执行三步：v1 二元行 selected 全部→driver、no_clear→no_news_driver；v2.0 旧枚举行按映射表升级到 v2.1；Phase3a 把存量 `post_hoc_explanation` / `contradictory` 从 `news_roles` 移除为 noise）。
窗口生成为**多尺度**（`config.ANNOTATION_WINDOW_SCALES`：15m+60m 档、各带净变动门槛、跨档重叠同向合并，`annotation_service.load_price_windows`）；价格快照缺口由 `services/gap_repair.py` 每小时 :37 自愈（扫描→批量回补→复扫→按回补结果分类→企业微信账目）。

**写入方：** `annotation_service.upsert_annotation`（落库前 `_normalize_v2_labels` 归一化，非法枚举 400；兼容字段由 `_derive_compat_fields` `services/annotation_service.py:795` 派生）。
**读取方：** `annotation_service.load_price_windows`、`export_training_jsonl`（JSONL 训练集导出，`GET /api/annotations/export`，候选全量展开；未标=noise，redundant 不当负样本）。

> 注：`load_price_windows` / `list_annotations` 的响应（`PriceWindowSchema` / `AnnotationListItem`）自 2026-06-08 起带一个**计算字段** `references`——「宏观同期对标」列表（`config.ANNOTATION_REFERENCE_ASSETS`，2026-07-02 起 7 项：纳指 NQ=F / 日经225 ^N225 / 原油 CL=F / 黄金 GC=F / 美债2Y US_2Y / 美元指数 DX-Y.NYB / BTC BTC/USDT），每项按窗口端点最近快照算同期变动（容差 10min）：常规品种 `(end−start)/start` 涨跌%，收益率类（config 三元组标 `"bp"`）按基点 `(end−start)×100`；`ReferenceChange.pre_pct` / `pct` / `post_pct` 在 `services/annotation_service.py` `_reference_changes_for_window` 分别算窗口前1h / 窗口内 / 窗口后1h，标注品种本身 `is_self=true` 且同样保留这三段涨跌；`price_start` / `price_end`（2026-07-10 Phase 2）给窗口内绝对起终点。无数据 `pct=null`。**不落库**，按请求实时算；前端 `fmtRef` 渲染「绝对起点 → 终点 (窗口涨跌)」+ 前/后段（`frontend/src/pages/AnnotationsPage.tsx`）；增减对标资产改 config 一行。~~`ReferenceChange.correlation`（±1h Pearson 同步相关）~~ 2026-07-10 整链删除——对时序错位敏感、判别力≈随机，联动证据一律走 rolling S（`LinkagePanel` 曲线 + 窗口段芯片 max\|S\|）。
> 自 2026-06-10 起，自动标注（单窗口 + 批量）喂给 reasoner 的 payload 带 `reference_changes`（`{标签: "+x.xx%" / "+x.xbp"}`，标注品种自身不列、无数据 null，`annotation_service._reference_changes_for_annotation`）；自 2026-07-02 起同一 payload 和训练导出 `window` 还带 `reference_change_segments`（前1h / 窗口 / 后1h，包含标注品种本身作为比较基准）。**自 2026-07-09（prompt v11）起**，payload 与训练导出 `window` 用共振分证据链取代 ±1h Pearson：`s_scores`（`{标签: {s, ess, coverage}}`，s∈[−1,1] 符号只作方向展示、判级用 |S|）、`max_ref`（最强参照）、`sync_ref_count`（|S|≥0.3 参照数）、`machine_class`（S×新闻十字格机器预分类，推理起点可推翻），由 `annotation_service._window_signals_payload` 调 `resonance_score.rolling_peak` 现算（**2026-07-10 Phase 2 起 rolling 峰值口径**——段窗+后1h 内 30 点拖尾曲线的 \|S\| 峰值，与标注页 `LinkagePanel` 曲线"所见即所判"；v11 时期为事件窗 `s_score`，该函数已删）；`correlations` 从 payload/导出移除，UI `ReferenceChange.correlation` 也于 2026-07-10 随同步相关整链删除。**prompt v12（`v12-20260710`）** 输出契约增 `window_class`（三类建议：有 driver→`news_driven`；无 driver 且 max\|S\|≥0.5→`pure_resonance`；否则→`sentiment_tech`），前端自动标注/批量缓存回填三类选择器供人工改判，经 `AnnotationCreateRequest.window_class` 保存回写段 `human_class`。推理链不变：先 machine_class/max_ref 定性质 → 围绕最强参照找新闻 → 三段方向链交叉验证；无对照（s_scores 空）≠ 无宏观新闻。

### `prediction_markets`（`models/prediction.py`）

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | int PK | |
| `timestamp` | datetime | 快照时间。 |
| `market_id`, `question`, `outcome` | str | `market_id` 是 Polymarket Gamma id。 |
| `probability`, `prev_probability` | float | `prev_probability` 在 insert 时从前一行 DB 记录写入，不是告警时再算（`scanners/prediction_scanner.py:48`）。 |
| `volume` | float \| null | |
| `origin` | str \| null | 来源跟踪项 `"slug:<identifier>"` / `"tag:<identifier>"`，由 `PolymarketSource.fetch` 打标（`scanners/sources/polymarket/source.py:90-98`）；2026-06-10 前的旧快照为 NULL。 |
| `created_at` | datetime | |

索引：`(market_id, timestamp)`。

**写入方：** `PredictionScanner._save_records`。
**读取方：** `prediction_service.load_prediction_rows`、`prediction_service.get_prediction_families`、`AlertEngine.evaluate_predictions`。

> 注：`load_prediction_rows`（`services/prediction_service.py:41`）自 2026-06-10 起只返回「仍在跟踪」的市场，按市场粒度整体保留/剔除，两级判定：
> 1. 快照带 `origin` → 按 `tracked_markets` 软删状态**精确过滤**（`origin ∈ {kind:identifier | dismissed=False}`）：删除跟踪立即从 `/predictions`、`families`、`history` 消失；市场结算（如 CPI 公布后 tag 发现型市场停更）或单 slug 接口抖动导致的断流**不会**误删历史。
> 2. 旧快照（`origin` NULL）→ **断流启发式**兜底：最后一笔快照落后表内最新快照超过 `config.PREDICTION_ACTIVE_GRACE_MINUTES`（默认 30）分钟的市场剔除；基准取表内最新时间而非墙钟，调度器宕机不误杀。

### `alert_logs`（`models/alert_log.py`）

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | int PK | |
| `timestamp` | datetime | |
| `rule_name` | str | 例如 `price_change_BTC/USDT_15m`。 |
| `message` | str | 新闻告警含 `news:<source>:<source_id>` 标记，去重靠它。 |
| `channel` | str | `console` / `wechat_work`。 |
| `delivered` | bool | 派发失败为 False。 |
| `created_at` | datetime | |

索引：`(rule_name, timestamp)`。

**写入方：** `AlertEngine._log_alert` `alerts/engine.py:85`。
**读取方：** `AlertEngine._is_in_cooldown` `alerts/engine.py:71`、`alerts_service.get_logs`。

### `cmc_symbol_categories`（`models/sector.py:17`）

symbol -> CMC 板块 的多对多映射本地缓存。

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | int PK | |
| `symbol` | str(40) | CMC 视角的基础 symbol（如 `ETH`、`BTC`，**不带 USDT 后缀**）。 |
| `category` | str(120) | CMC category name（如 `AI Agents`、`Layer 1`）。 |
| `category_id` | str(40) \| null | CMC 的 category id，改名时按 id 幂等刷新。 |
| `updated_at` | datetime | 驱动 7 天 TTL 检查（`cmc_client.needs_refresh`）。 |

`(symbol, category)` 唯一。索引 `ix_cmc_symbol`、`ix_cmc_category`。

**写入方：** `cmc_client.refresh_categories` `services/cmc_client.py:172`（每板块 DELETE 旧行 + INSERT 新行）。由 `cmc_bootstrap` job（启动 +10s）或 `python run.py refresh-sectors` 触发。
**读取方：** `cmc_client.load_category_to_symbols` `services/cmc_client.py:271`（被 sector_scanner / sector_service 用）。

### `sector_returns`（`models/sector.py:44`）

每个板块在某 snapshot_at 时刻的等权聚合涨跌。

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | int PK | |
| `snapshot_at` | datetime | = BMAC pivot 最新一根 `candle_begin_time`（UTC naive）。 |
| `category` | str(120) | CMC category name。 |
| `group_name` | str(60) \| null | 中文大组名（来自 `config.SECTOR_WHITELIST`，如 `公链龙头`）。 |
| `token_count` | int | 参与计算的活跃 symbol 数（< `MIN_TOKENS_PER_SECTOR=3` 的板块不写）。 |
| `ret_1h / ret_24h / ret_168h / ret_720h` | float \| null | 该板块成员币等权平均涨跌（**百分比**，如 +2.57 = +2.57%）。 |
| `created_at` | datetime | |

`(snapshot_at, category)` 唯一（重跑同 snapshot 幂等：先 DELETE 再 INSERT）。索引 `ix_sector_snapshot`、`ix_sector_category_snapshot`。

**写入方：** `SectorScanner.scan` `scanners/sector_scanner.py:315`，由 `remote_data_cycle` 在拉到新 pivot 后触发。
**读取方：** `sector_service.get_leaderboard` `services/sector_service.py:80`（榜单 API 读这张表）。注意：钻取 API `get_sector_tokens` **不读这张表**，而是从 pivot 缓存现算单币涨跌。

### behavior_segments（2026-07-09，价格行为引擎）

| 字段 | 类型 | 备注 |
|---|---|---|
| `symbol / start_dt / end_dt / direction` | str / dt / dt / int(±1) | `(symbol, start_dt, end_dt)` 唯一；upsert 按 `(symbol, start_dt, direction)` 匹配（段随数据生长更新同一行） |
| `tier_idx / tier_max` | int(0/1/2) / float | 触及最高档（BTC 阶梯 0.3/0.5/0.8，参照按 `config.BEHAVIOR_TIERS` 稀有度锚定值） |
| `net_pct / amp_pct / key_ts` | float / float / dt | 净幅、振幅、段内 \|5min\| 最大 bar（新闻对时锚点） |
| `classification` | str \| null | `count_only`(0.3档) / `macro_news` / `pure_resonance` / `industry_news` / `sentiment` / `no_ref_news`(无对照×新闻命中) / `no_ref_pending`；null=未 settle（段止+后窗1h+`ANNOTATION_SETTLE_MARGIN_MINUTES`） |
| `s_scores / news_ids / class_version` | JSON / JSON / str | `{ref: {s, ess, coverage}}`（Phase 2 起 = `rolling_peak` 段窗内曲线 \|S\| 峰值口径）；±30min 大/中新闻 id；分类口径版本（换版可全历史重跑——段是原始数据） |
| `human_class / human_confirmed_at` | str \| null / dt \| null | 人工审计（2026-07-09；Phase 2 起取值收敛为三类 `news_driven / pure_resonance / sentiment_tech`）：`PATCH /api/behavior/segments/{id}` 确认/改判/撤销，或标注页保存标注时经 `_write_back_window_class` 重叠≥50% 匹配回写；**构成聚合优先 human_class**（机器六类经 `to_window_class` 归并后参与），机器 classification 保留作对照；机器重跑不碰人工结论 |

**写入方：** `behavior_classifier.classify`（APScheduler `behavior_cycle`，5min）；`human_class` 另有 `annotation_service._write_back_window_class`（标注保存）与 `PATCH /api/behavior/segments/{id}`。**读取方：** `behavior_views.list_segments`（API）、`aggregate_day`（日汇总）、`annotation_service.load_price_windows`（**Phase 2 起唯一窗口源**：0.5 档以上段映射为待标窗口、0.3 档段作簇拥上下文；`BEHAVIOR_REPLACES_ANNOTATION_WINDOWS` 开关已退役）。

### behavior_daily_summaries（point-in-time 追加表）

| 字段 | 类型 | 备注 |
|---|---|---|
| `symbol / utc_date / day_type` | str / "YYYY-MM-DD" / weekday\|weekend | UTC 日界 = 北京 8 点；工作日/周末分桶互比 |
| `counts / composition / down_net_sum` | JSON / JSON / float | `{tier: {up,down}}`（0.3 档全量计数）；构成（0.5 档以上，历史 PIT 行可能存六类，读取经 `merge_composition` 归并为三类 + `no_ref` 注记，新写入即三类）；跌段净幅合计 |
| `computed_at` | datetime | **PIT 语义：同日重算=追加新行、绝不覆盖**，读取取最新一条——历史读数永久可回溯（回测校准前提） |

**写入方：** `behavior_classifier.write_daily_summary`（`behavior_daily_summary` job，UTC 00:05 汇总昨日）。**读取方：** `behavior_views.daily_series`（当日无 PIT 行时按同口径现算 `live=true`，不落库）。

## 内存中的数据契约

扫描器和告警引擎之间传递的记录类型。

| 类型 | 生产者 | 消费者 | 关键字段 |
|---|---|---|---|
| `PriceRecord` `scanners/base.py:10` | `PriceScanner.scan()` | `AlertEngine.evaluate_prices`、`run_scan_once` | `asset_class`、`symbol`、`price`、`prev_price`、`change_pct`、`source`、`timestamp`。 |
| `NewsRecord` `scanners/base.py:26` | `NewsScanner.scan()` | `NewsScorer.enrich_batch`、`AlertEngine.evaluate_news`、`run_scan_once` | `source`、`source_id`、`title`、`content`、`importance`、`llm_importance`、`published_at`。 |
| `PredictionRecord` `scanners/base.py:44` | `PredictionScanner.scan()` | `AlertEngine.evaluate_predictions`、`run_scan_once` | `market_id`、`question`、`outcome`、`probability`、`volume`。 |
| `AlertRule` `alerts/rules.py:8` | 由 `AlertEngine._load_rules` 从 `config.ALERT_RULES` 加载 | `AlertEngine.evaluate_*` | `name`、`rule_type`（str）、`params`、`channels`、`cooldown_minutes`、`enabled`。 |
| `PriceWindowMove` `alerts/engine.py:24` | `_price_window_move_from_session` | `evaluate_prices` | `change_pct`、`start_time`、`end_time`、`prices`、`range`。 |

### 进程局部注册表

| 注册表 | 位置 | 生命周期 | 备注 |
|---|---|---|---|
| `task_service._TASKS` | `services/task_service.py:15` | 24 小时，`_cleanup_locked` 在被访问时清理 | 映射 `task_id` -> `TaskRecord`。仅单进程。 |
| `task_service._RUNNING_SCAN_ID` | `services/task_service.py:17` | 任务结束前 | 防止两个 `POST /api/tasks/scan` 并发。配合 `.scan.lock`。 |
| `onchain_service._CACHE` | `services/onchain_service.py:13` | 每个数据集 60 分钟 | 三个键：`top100_netflow`、`daily_stats`、`cex_flows`。`force_refresh=True` 跳过缓存。 |
| `sector_service._pivot_cache` | `services/sector_service.py:55` 内 | 按 pivot 文件 mtime 失效 | `{market: (mtime, pivot_dict)}`。避免每次榜单钻取请求都反序列化 ~20MB pkl。 |
| `remote_puller` 单例 `_puller` | `services/remote_puller.py:267` | 进程生命周期 | 持有每 dataset 的 `last_cutoff_ts` 和 `_next_check_at`（per-dataset 轮询闸门）。 |
| `remote_fs._session` | `services/remote_fs.py:273` | 进程生命周期 | SFTP 长连接单例（paramiko），断线自动重连，`consecutive_failures` 计数。 |

## API 契约

### 响应外壳约定

所有接口返回 JSON 遵循以下规范（定义在 `schemas/`）：

- **分页：** `Page[T]` -> `{ items, total, page, page_size, pages }`（`schemas/common.py`）。
- **时间字段：** 每个时间戳同时输出 `timestamp_utc`（ISO 8601）和 `timestamp_bj`（北京本地时间）（`schemas/common.py.TimeFields`）。
- **错误外壳：** `{ "code", "message", "details" }`，由 `api/errors.py` 的 4 个 handler 设置：`ApiError`、`HTTPException`、`RequestValidationError`、未捕获 `Exception`。

### 端点（路由 -> service -> 响应 schema）

| 路由 | service 入口 | 响应 schema |
|---|---|---|
| `GET /api/health` | `api/routes.py:61` | `dict` |
| `GET /api/status` | `api/routes.py:66` -> `market_service.status_snapshot` + `task_service.all_tasks` | `dict` |
| `POST /api/tasks/scan` | `api/routes.py:80` -> `task_service.create_scan_task` | `TaskStatus` |
| `GET /api/tasks/{task_id}` | `api/routes.py:85` -> `task_service.get_task` | `TaskStatus` |
| `GET /api/market/latest` | `api/routes.py:94` -> `market_service.get_latest_prices` | `MarketLatestResponse` |
| `GET /api/market/symbols` | `api/routes.py:98` -> `market_service.get_symbols` | `list[MarketSymbol]` |
| `GET /api/market/history` | `api/routes.py:103` -> `market_service.get_history` | `MarketHistoryResponse` |
| `GET /api/market/table` | `api/routes.py:120` -> `market_service.get_table` | `Page[MarketTableRow]` |
| `GET /api/market/table.csv` | `api/routes.py:132` -> `market_service.get_table_csv` | text/csv |
| `GET /api/news` | `api/routes.py:147` -> `news_service.get_news` | `NewsResponse` |
| `GET /api/predictions` | `api/routes.py:175` -> `prediction_service.get_predictions` | `PredictionsResponse` |
| `GET /api/predictions/families` | `api/routes.py:180` -> `prediction_service.get_prediction_families` | `list[PredictionFamily]` |
| `GET /api/predictions/{market_id}/history` | `api/routes.py:216` -> `prediction_service.get_market_history` | `list[PredictionRow]` |
| `GET /api/onchain/eth/top100-netflow` | `api/routes.py:221` -> `onchain_service.top100_netflow` | `OnchainDataset` |
| `GET /api/onchain/eth/daily-stats` | `api/routes.py:226` -> `onchain_service.daily_stats` | `OnchainDataset` |
| `GET /api/onchain/eth/cex-flows` | `api/routes.py:231` -> `onchain_service.cex_flows` | `OnchainDataset` |
| `GET /api/alerts/rules` | `api/routes.py:236` -> `alerts_service.get_rules` | `list[AlertRuleSchema]` |
| `GET /api/alerts/webhook-status` | `api/routes.py:241` -> `alerts_service.get_webhook_status` | `AlertWebhookStatus` |
| `GET /api/alerts/logs` | `api/routes.py:246` -> `alerts_service.get_logs` | `Page[AlertLogSchema]` |
| `POST /api/alerts/test-wechat` | `api/routes.py:251` -> `alerts_service.test_wechat` | `AlertTestResponse` |
| `GET /api/annotations/price-rules` | `api/routes.py:256` -> `annotation_service.load_alert_price_rules` | `list[PriceRuleSchema]` |
| `GET /api/annotations/symbols` | `api/routes.py:261` -> `annotation_service.load_symbols` | `list[AnnotationSymbol]` |
| `GET /api/annotations/windows` | `api/routes.py` -> `annotation_service.load_price_windows` | `list[PriceWindowSchema]` |
| `GET /api/annotations/context-news` | `api/routes.py` -> `annotation_service.load_context_news_for_window`（参数改为 `pre_minutes` / `post_minutes`，默认 15/30） | `ContextNewsResponse` |
| `POST /api/annotations` | `api/routes.py:291` -> `annotation_service.upsert_annotation` | `AnnotationResponse` |
| `GET /api/annotations/{id}` | `api/routes.py:` -> `annotation_service.get_annotation_detail` | `AnnotationDetail` |
| `DELETE /api/annotations/{id}` | `api/routes.py:` -> `annotation_service.delete_annotation`（撤销标注） | `DeleteAnnotationResponse` |
| `POST /api/annotations/auto` | `api/routes.py:` -> `annotation_service.auto_annotate`（调 DeepSeek v4-pro thinking 模式，不写库；当前 prompt 输出 Phase3a 标签：news_roles + confidence + summary，解析器仍兼容历史 market_reaction_type） | `AutoAnnotateResponse` |
| `GET /api/behavior/segments` | `api/routes.py` -> `behavior_views.list_segments`（读 `behavior_segments` 表 + 新闻标题 join；0.3 档段 classification=count_only） | `BehaviorSegmentsResponse`（段 + s_scores/ess/coverage/max_abs_s/news/classification） |
| `GET /api/behavior/daily` | `behavior_views.daily_series`（每日最新 PIT 行优先；当日盘中按 `behavior_classifier.aggregate_day` 同口径现算 `live=true`） | `BehaviorDailyResponse`（counts×tier×dir、三类构成（人工优先归并）+ `no_ref` 注记、跌段净幅合计） |
| `GET /api/behavior/linkage` | `behavior_views.linkage`（compute-on-read：`resonance_score.rolling_s`，30 点拖尾窗；纯展示不触发不分类） | `BehaviorLinkageResponse`（逐参照 S 曲线 + 同步参照数 breadth，None=无对照断线） |
| `GET /api/annotations/export` | `api/routes.py:299` -> `annotation_service.export_training_jsonl`（JSONL 训练集，spec §4） | NDJSON 下载 |
| `GET /api/sectors/leaderboard` | `api/routes.py:372` -> `sector_service.get_leaderboard`（读 `sector_returns` 表最新 snapshot） | `SectorLeaderboardResponse` |
| `GET /api/sectors/{category}/tokens` | `api/routes.py` -> `sector_service.get_sector_tokens`（从 pivot 缓存现算成员币涨跌） | `SectorTokensResponse` |

前端 client（`frontend/src/api/client.ts`）消费上述全部接口。板块两个端点在 `client.ts:146-148`。无孤儿端点，无指向不存在端点的前端调用（grep 已确认）。

> 注：本地分支 `feat/remote-data-integration` 另有 `GET /api/factors/pendle-vs-eth`（单币因子，未合并 main），本表不含。

## 外部集成

| 数据源 | 使用方 | 频率 | 失败模式 |
|---|---|---|---|
| yfinance（Yahoo） | `scanners/sources/yfinance_source.py` | 每次扫描 + 回填 | 缺失 symbol 仅记日志，不告警。 |
| OKX（5m 原始 K 线） | `scanners/sources/okx_source.py` | 每次扫描 + 回填 | 加密货币主路径。 |
| CoinGecko（实时） | `scanners/sources/coingecko_source.py` | 仅作兜底，OKX 没数据时用 | 实时 tick，**不是**已收口 bar 时间戳。 |
| CNBC 行情 API（债券收益率） | `scanners/sources/cnbc_bond_source.py` | 每次扫描 | 美 / 日 2Y / 10Y 收益率 + 10Y-2Y 利差（客户端相减）。一个批量请求带浏览器 UA，海外（东京）可达，**2026-06-09 替代东方财富**（境内源境外抓不稳）。timestamp 留空→扫描时落库保连续。`eastmoney_bond_source.py` 保留为 config 可切的备用源。 |
| Jin10 | `scanners/sources/jin10_source.py` | 每次扫描 + 回填 | 请求用北京时间 `max_time`。 |
| CNBC RSS（替代 Bloomberg，2026-05-05） | `scanners/sources/rss_source.py` | 每次扫描 + 回填 | URL 改为 `https://www.cnbc.com/id/100003114/device/rss/rss.html`。Bloomberg RSS 实测产出过少。|
| Polymarket Gamma | `scanners/sources/polymarket/`（`client`、`filters`、`parser`、`source`） | 每次扫描 | slug 列表硬编码在 `config.POLYMARKET`。 |
| DeepSeek（V4 flash） | `scanners/scorer.py` | 实时扫描中每个新闻 batch；回填默认关闭 | 没有 API key 则 `llm_importance` 留 null。 |
| DeepSeek（V4 pro，thinking 模式） | `services/annotation_service.py:auto_annotate` | 用户在标注页点「自动标注」时一次 | 240s read timeout；返回 `reasoning_content`；没有 API key 则 502。 |
| Dune Analytics | `onchain_data/dune_queries.py`（被 `services/onchain_service.py` 加载） | 在 `/api/onchain/eth/*` 请求时调用，60 分钟内存缓存 | 没有 API key 或没有 saved query id 则端点报错。 |
| WeCom webhook | `alerts/channels/wechat_work.py` | 每次告警派发 | HTTP 失败时 `alert_logs` 记 `delivered=False`。 |
| **BMAC 数据中心（SFTP）** | `services/remote_fs.py`（paramiko），由 `remote_puller` 调度 | `remote_data_cycle` job，pivot 每 1h、exginfo 每 1 天（per-dataset poll_interval） | `root@47.243.252.92:/root/data_center/data/`。拉取失败保留上次缓存，scanner 用旧数据继续，不阻塞。pkl 用 numpy 2.x 写，本地用 numpy shim 加载（`remote_fs.py:45`）。 |
| **CoinMarketCap API** | `services/cmc_client.py` | `cmc_bootstrap` job 启动检查 + 手动 `refresh-sectors`，7 天 TTL | **直连不走代理**（`trust_env=False`，2026-05-17 决策）+ 3 次重试。只查 `SECTOR_WHITELIST` 内 ~45 个板块，~2min。 |

## BMAC 远程数据 -> sector_returns 的数据流

**拉取的 pkl 文件**（`remote_puller.PHASE1_DATASETS`，存到 `data/remote_cache/`）：

| 远程相对路径 | 结构 | 用途 |
|---|---|---|
| `preprocess_1h_resample/30m/market_pivot_spot_{YYYY}.pkl` | `dict`，keys `['open','close','vwap1m']`，每个 value 是 DataFrame（index=candle_begin_time UTC，columns=symbol 如 `BTCUSDT`） | 现货板块涨跌 |
| `preprocess_1h_resample/30m/market_pivot_swap_{YYYY}.pkl` | `dict`，keys `['open','close','funding_rate','vwap1m']`，同上 | 永续板块涨跌（现货优先，缺则用） |
| `exginfo/spot_swap_matches.pkl` | DataFrame（spot, swap 列） | 现货↔永续 symbol 映射（暂存，板块计算未直接用） |

**`.ready` flag 约定**：BMAC 写完 pkl 后落 `{basename}_{cutoff_unix_ts}.ready`，内容是写完时间。`remote_fs.find_latest_ready` 取最大 cutoff 判断有没有新数据。BMAC 用 30m 偏移：每小时 :30 落最新一根的 pivot。

**转换链**（`scanners/sector_scanner.py`）：
1. `pivot["close"]` DataFrame（spot + swap）
2. `_compute_returns_for_close` `sector_scanner.py:122`：对每个 symbol 算 `ret_Nh = (close[-1] - close[-(N+1)]) / close[-(N+1)] * 100`，N ∈ {1, 24, 168, 720}
3. `normalize_pivot_symbol` `sector_scanner.py:64`：`ETHUSDT` -> `ETH`（去 USDT 后缀、特例映射 BEAMX->BEAM、去 1000000/1000/1M 数量前缀、过滤乱码列）
4. spot 优先合并 swap
5. `cmc_client.load_category_to_symbols`：查每板块的 symbol 集合
6. 每板块取交集，< 3 个活跃 symbol 跳过，其余等权平均
7. 写 `sector_returns`（同 snapshot_at 先 DELETE 再 INSERT）

**当前重试语义：** `remote_puller.cycle` 只在本轮拉到新 pivot 时触发 `_run_sector_scan()`（`services/remote_puller.py:186`）。`_pull_if_newer()` 下载成功后先更新 `last_cutoff_ts`（`services/remote_puller.py:241`），因此如果随后的 sector scan 失败，同一个 cutoff 不会在下一轮自动重扫；除非出现新的 `.ready` cutoff 或手动触发 scanner。

**pending retry 已实现（2026-07-01）：** pivot 下载成功后先把 cutoff 放进 `DatasetStatus.pending_sector_retry_cutoff_ts`（`services/remote_puller.py:115` / `services/remote_puller.py:289`）。`cycle()` 在 pivot 新下载或存在 pending 时触发 `_run_sector_scan()`（`services/remote_puller.py:187`）；`SectorScanner().scan()` 只有在至少写入一批 `sector_returns` 时才算完成（`services/remote_puller.py:214`）。部分周期算不出来（如 `ret_168h` / `ret_720h` 为 null）、部分 symbol NaN、部分板块 token<3 跳过，都算成功但带 warning。没有写入任何 `sector_returns`，或出现异常 / `skipped_reason`，同 cutoff 会在后续 cycle 继续重试。

## 时间语义总结

| 层 | 格式 |
|---|---|
| DB 中所有 `datetime` 列 | UTC naive（`tzinfo=None`）。 |
| `PriceRecord.timestamp` | 已收口 5m bar end（yfinance / OKX）；采集时间（CoinGecko / Eastmoney bond）。Eastmoney 的源端 `f86` 字段在收盘期停滞，会让连续 5m 扫描全被 `(symbol, timestamp)` 唯一约束跳过，所以现在统一用扫描时间。 |
| `NewsItem.timestamp` | 源发布时间，**不是**落库时间。 |
| API 响应时间戳 | 每条都带 `timestamp_utc`（ISO）和 `timestamp_bj`（北京）双字段。 |
| Jin10 出站 `max_time` | 北京本地。`jin10_source.py` 转换；落库前再转回 UTC naive。 |
| 调度器 tick | UTC。`next_aligned_run_time(...)` 返回下一个 5m 边界 + `SCAN_START_DELAY_SECONDS`。 |

## 改动后最容易引发问题的关键字段

- `(price_snapshots.symbol, timestamp)` 唯一约束 - 去重和窗口查询都依赖它。
- `(news_items.source, source_id)` 唯一约束 - 新闻去重和告警标记 `news:<source>:<source_id>` 都依赖它。
- `prediction_markets.prev_probability` - 预测告警靠它对比；删除或改用途会改变告警语义。
- `chart_utils.to_beijing_time` - 所有 API 响应和 WeCom 推送都过它。
- `config.MARKET_OVERVIEW_DEFAULT_SYMBOLS` - 同时被 React 默认图和整点摘要使用。
- `config.SCAN_INTERVALS` - 任何变更会传导到 `next_aligned_run_time`、`_run_rolling_backfill`、`AlertEngine` 窗口容差。
- `(sector_returns.snapshot_at, category)` 唯一约束 - 板块写入幂等和榜单读取都依赖它。
- `remote_puller` 的 `last_cutoff_ts` - 当前用于跳过已拉取 pivot；如果板块扫描失败，它不会表达"已拉取但下游未成功"。
- `config.SECTOR_WHITELIST` - 决定算哪些板块；改它后必须 `python run.py refresh-sectors` 让 `cmc_symbol_categories` 重新同步，否则 sector_scanner 用过期映射。
- `config.REMOTE_OFFSET`（默认 `30m`）- 决定拉哪个 BMAC 偏移子目录；改它要确认服务器有对应目录。
- BMAC pivot 的 `candle_begin_time` 列时区是 **UTC tz-aware**，入库前 `tz_localize(None)` 转 UTC naive（与全库一致）。
- numpy shim（`remote_fs.py:45`）- 服务器 pkl 用 numpy 2.x 写，本地 1.26.4 靠它加载；BMAC 升级到只在 numpy 2 存在的新 dtype 时会失效。
- `/api/market/history` 的 `normalized_pct`（`market_service.get_history` + `chart_utils.normalize_prices`）自 2026-06-08 起按**窗口起点锚定净值**：每品种以 `timestamp ≤ start` 最后一笔收盘为基准（`config.MARKET_HISTORY_BASELINE_LOOKBACK_DAYS` 回看，默认 7 天），保留隔夜跳空/熔断；无前置数据回退到窗口内首点。改基准取法会改变整张跨资产走势图语义。
