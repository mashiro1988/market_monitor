# Investment Agent — Current State Spec

> **目的**: 现状文档，供个人参考和 AI 上下文使用。
> **最后更新**: 2026-04-04
> **当前分支**: dev

---

## 1. Overview

Investment Agent 是一个**宏观驱动的加密货币投资监控系统**，运行在本地 Windows 机器上。

核心目标：
- 每 5 分钟自动采集全品种价格、多源财经新闻、Polymarket 预测市场数据
- 按规则评估市场异动，通过企业微信机器人推送告警
- Streamlit 多页仪表盘提供可视化查阅

**不是**：回测系统、自动交易系统、公共服务。

---

## 2. Architecture

### 2.1 进程模型

系统有两个独立进程，需分别启动：

```
进程 A: python run.py schedule
  └── APScheduler (BackgroundScheduler)
        ├── scan_cycle()  每 5 分钟
        │     ├── PriceScanner.scan()
        │     ├── NewsScanner.scan()
        │     ├── PredictionScanner.scan()
        │     └── AlertEngine.evaluate_all()
        └── hourly_summary()  每 1 小时

进程 B: python run.py app
  └── streamlit run app.py
        ├── pages/1_市场概览.py
        ├── pages/2_新闻快讯.py
        ├── pages/3_预测市场.py
        ├── pages/4_链上数据.py
        └── pages/5_告警设置.py
```

两个进程共享同一个 SQLite 数据库（WAL 模式支持并发读写）。

### 2.2 模块职责

| 模块 | 路径 | 职责 |
|------|------|------|
| PriceScanner | `scanners/price_scanner.py` | 编排价格源，写 price_snapshots |
| NewsScanner | `scanners/news_scanner.py` | 编排新闻源，双层去重，写 news_items |
| PredictionScanner | `scanners/prediction_scanner.py` | 编排预测市场源，写 prediction_markets |
| AlertEngine | `alerts/engine.py` | 评估规则、冷却判断、分发通知 |
| AlertRule | `alerts/rules.py` | 规则数据类（dataclass，无业务逻辑） |
| WeChatWorkChannel | `alerts/channels/wechat_work.py` | HTTP POST 到企业微信 Webhook |
| ConsoleChannel | `alerts/channels/console.py` | 打印到终端（调试用） |
| BaseSource | `scanners/base.py` | 所有数据源的抽象基类 |
| SignalRegistry | `signals/registry.py` | 交易信号注册和评估（**未接入扫描流程**） |
| config.py | 根目录 | 所有配置中心，包含代理自检逻辑 |
| database.py | 根目录 | SQLAlchemy 引擎 + 会话工厂 |

---

## 3. Data Sources

### 3.1 价格数据

#### yfinance（`scanners/sources/yfinance_source.py`）

- 采集方式：`yf.download(tickers, period="2d", interval="1d")`，**日线级别**，非实时
- change_pct = 今日收盘 vs 昨日收盘

| asset_class | 品种 | Ticker |
|-------------|------|--------|
| stock_index | 道琼斯、纳斯达克、标普500 | ^DJI, ^IXIC, ^GSPC |
| futures | S&P500期货、纳指期货、道指期货 | ES=F, NQ=F, YM=F |
| asian_index | 日经225、韩国KOSPI、上证、深证、创业板 | ^N225, ^KS11, 000001.SS, 399001.SZ, 399006.SZ |
| commodity | WTI原油、黄金、白银 | CL=F, GC=F, SI=F |
| bond | JP_10Y（**配置标签误导**，实为 ^TNX = 美国10年期国债） | ^TNX |

#### ccxt / Binance（`scanners/sources/ccxt_source.py`）

- 采集方式：`fapiPublicGetKlines`（期货API），interval=1h，limit=2
- change_pct = 最新1h收盘 vs 前一根1h收盘
- **注意**：config 中 symbol 格式为 `BTCUSDT`，存入 DB 格式为 `BTC/USDT`

| 品种 | 21个 |
|------|------|
| BTC, ETH, FET, TAO, RNDR, WLD, UNI, ONDO, PENDLE, 1INCH, DOGE, XRP, SOL, DOT, LINK, CFX, ENS, AR, FIL, ARB, OP |

- **Fallback**：Binance 返回空（如 451 地区限制）时自动切换 CoinGecko

#### CoinGecko（`scanners/sources/coingecko_source.py`）

- Binance 失败时使用，覆盖相同 21 个品种
- 使用 CoinGecko 公开 API（无 key）

#### FRED API（`scanners/sources/fred_source.py`）

- 需要 `FRED_API_KEY`，否则跳过
- 获取 DGS10（US 10Y）、DGS2（US 2Y），日频数据
- **自动计算并存储** `US_SPREAD`（10Y - 2Y），asset_class = bond

### 3.2 新闻数据

#### 华尔街见闻（`scanners/sources/wallstreetcn_source.py`）

- API: `https://api.wallstreetcn.com/apiv1/content/lives`
- 参数：channel=global, important=true, limit=100
- importance: important=true → 8，否则 5
- language: zh

#### 金十数据（`scanners/sources/jin10_source.py`）

- API: `https://flash-api.jin10.com/get_flash_list`
- 需要 header `x-app-id: bVBF4FyRTn5NJF5n`（硬编码）
- 无 title 时用 content 前100字代替
- importance: important=1 → 8，否则 4
- language: zh

#### RSS 源（`scanners/sources/rss_source.py`）

| 名称 | URL | language |
|------|-----|----------|
| CoinDesk | `https://www.coindesk.com/arc/outboundfeeds/rss/` | en |
| CoinTelegraph | `https://cointelegraph.com/rss` | en |
| The Block | `https://www.theblock.co/rss.xml` | en |
| Reuters | `https://www.reutersagency.com/feed/` | en |

- 使用 `feedparser` 解析，importance 不赋值（默认 None）

### 3.3 新闻去重策略（双层）

1. **源内去重**：检查 `(source, source_id)` 是否已存在
2. **跨源去重**：对标题 normalize（去标点空白、转小写）后 SHA-256，检查 24 小时窗口内 `content_hash` 是否已存在

### 3.4 预测市场

#### Polymarket（`scanners/sources/polymarket_source.py`）

- **实际只使用 Gamma API**（`https://gamma-api.polymarket.com`）：市场搜索 + 概率获取（`outcomePrices` 字段）
- config 中的 `api_url: https://clob.polymarket.com` 是死配置，`polymarket_source.py` 未调用
- 跟踪标签（30个）：fed, fomc, interest-rate, geopolitics, iran, russia, china, war, gdp, cpi, inflation, unemployment, jobs, election, trump, president, tariff, trade, crypto, bitcoin, sec, etf, recession 等
- `prev_probability` 在 `PredictionScanner._save_records()` 中通过查 DB 上一条记录赋值

---

## 4. Data Models

数据库：`market_monitor.db`（SQLite，WAL 模式）

### 4.1 price_snapshots

```
id            INTEGER  PK
timestamp     DATETIME NOT NULL
asset_class   STRING(20)    # stock_index | futures | asian_index | bond | commodity | crypto
symbol        STRING(30)    # ^DJI, ES=F, BTC/USDT, US_10Y 等
name          STRING(50)    # 道琼斯, S&P500期货 等
price         FLOAT NOT NULL
prev_price    FLOAT nullable
change_pct    FLOAT nullable
volume        FLOAT nullable
source        STRING(30)    # yfinance | ccxt | fred | coingecko
created_at    DATETIME

索引:
  UNIQUE (timestamp, symbol)   → 防重复入库，scan_time 相同则 merge
  (asset_class, timestamp)     → 按类别时序查询
```

数据保留：30 天（配置存在，无自动清理 job）

### 4.2 news_items

```
id            INTEGER  PK
timestamp     DATETIME NOT NULL    # 扫描时间，非发布时间
source        STRING(50)           # wallstreetcn | jin10 | coindesk_rss 等
source_id     STRING(100)          # 源端原始 ID
title         STRING(500)
content       TEXT nullable
url           STRING nullable
importance    INTEGER nullable      # 0-10，None = 未评分（RSS 源）
language      STRING               # zh | en
categories    STRING nullable
content_hash  STRING               # SHA-256(normalize(title))
```

数据保留：90 天（无自动清理 job）

### 4.3 prediction_markets

```
id               INTEGER  PK
timestamp        DATETIME NOT NULL
market_id        STRING               # Polymarket condition_id
question         STRING
outcome          STRING               # "Yes" | "No"
probability      FLOAT                # 0.0 - 1.0
prev_probability FLOAT nullable       # 上一次扫描的概率
volume           FLOAT nullable
```

数据保留：30 天（无自动清理 job）

### 4.4 alert_logs

```
id         INTEGER  PK
timestamp  DATETIME NOT NULL
rule_name  STRING
message    STRING(2000)
channel    STRING     # wechat_work | console
delivered  BOOLEAN
```

数据保留：90 天（无自动清理 job）

### 4.5 Legacy 表（向后兼容）

`models/legacy.py` 定义：StockIndex, BondRate, EconomicData, CryptoData, MarketNews。
`data_collector.py` 写入旧表，由 `run.py collect` 触发。**主流程不再使用旧表。**

---

## 5. Alert System

### 5.1 规则列表

| name | rule_type | 触发条件 | 通道 | 冷却 |
|------|-----------|----------|------|------|
| btc_price_spike | price_change | BTC/USDT ±3% / 15min 窗口 | wechat_work | 30min |
| eth_price_spike | price_change | ETH/USDT ±5% / 15min | wechat_work | 30min |
| us_futures_spike | price_change | ES=F ±2% / 15min | wechat_work | 30min |
| important_news | news_importance | importance ≥ 8 | wechat_work | 5min |
| prediction_shift | prediction_shift | 概率变化 ±5% / 30min | wechat_work | 30min |
| hourly_summary | hourly_summary | 每小时全市场摘要 | wechat_work | 55min |

**注意**：`window_minutes` 参数在 `evaluate_prices()` 和 `evaluate_predictions()` 中均**未实际使用**，两者都直接用当次扫描传入的记录做判断，不按时间窗口查历史数据。详见 G3。

### 5.2 冷却逻辑

每次触发前查询 `alert_logs`：在 `cooldown_minutes` 时间内是否有同 `rule_name` 且 `delivered=True` 的记录。有则跳过。

### 5.3 通道

- **wechat_work**：HTTP POST JSON 到 `WECHAT_WORK_WEBHOOK`；消息格式为企业微信 markdown 类型
- **console**：`logger.info()` 打印（本地调试）

---

## 6. UI Pages

### 6.1 市场概览（pages/1_市场概览.py）

- 数据窗口：2 小时内的 price_snapshots，按 symbol 去重取最新
- 布局：按 asset_class 分组，每组最多 4 列的 `st.metric` 卡片
- 债券显示 `x.xxx%`，加密显示 `$x,xxx.xx`，其余显示原值
- autorefresh：60 秒（需 streamlit-autorefresh）
- 底部可展开完整数据表

### 6.2 新闻快讯（pages/2_新闻快讯.py）

- 筛选器：来源多选、语言（全部/中/英）、回溯时长（1-72h）、关键词搜索
- 最多显示最新 100 条（DB 查询 limit 500）
- importance ≥ 8 → 🔴，≥ 5 → 🟡，其他 → ⚪
- RSS 来源 importance = None，显示为 ⚪
- autorefresh：120 秒

### 6.3 预测市场（pages/3_预测市场.py）

- 数据窗口：2 小时内，按 (market_id, outcome) 去重取最新
- 按 market_id 分组，每组显示各 outcome 的概率 metric
- 概率变化 ≥ 3% 的市场标记 🔥 并默认展开
- autorefresh：120 秒

### 6.4 链上数据（pages/4_链上数据.py）

- 依赖：`市场监控/dune_queries.py`（非标准路径，中文目录名）
- 需要：`DUNE_API_KEY` + 3 个 Query ID 环境变量
- 3 个 Tab：ETH Top100 净买入、ETH 每日统计、ETH CEX 资金流
- 任意配置缺失则显示提示并停止渲染

### 6.5 告警设置（pages/5_告警设置.py）

- 显示 Webhook 配置状态（前50字符）
- 测试按钮：发送固定测试消息
- 规则表：只读，展示 config.ALERT_RULES
- 告警历史：按时段查询 alert_logs，最多 200 条

---

## 7. Config & Env

### 7.1 环境变量（.env）

| 变量 | 必须 | 说明 |
|------|------|------|
| WECHAT_WORK_WEBHOOK | 是（告警功能） | 企业微信机器人 Webhook URL |
| FRED_API_KEY | 否 | 美债利率，缺失则跳过 FRED 采集 |
| DUNE_API_KEY | 否 | 链上数据页，缺失则页面不渲染 |
| DUNE_QUERY_ID_ETH_TOP100_NETFLOW | 否 | Dune Query ID |
| DUNE_QUERY_ID_ETH_DAILY_STATS | 否 | Dune Query ID |
| DUNE_QUERY_ID_ETH_MONTHLY_TX_COUNT | 否 | Dune Query ID（代码存在，页面未使用）|
| DUNE_QUERY_ID_ETH_CEX_DAILY_INOUT | 否 | Dune Query ID |
| PROXY_URL | 否 | 默认 http://127.0.0.1:4780，启动时 TCP 自检 |
| DATABASE_URL | 否 | 默认 sqlite:///market_monitor.db |

### 7.2 关键配置（config.py）

| 配置项 | 当前值 | 说明 |
|--------|--------|------|
| SCAN_INTERVALS.price | 5 分钟 | 同时控制 news 和 prediction |
| DATA_RETENTION.price_snapshots_days | 30 | 配置存在，无清理 job |
| DATA_RETENTION.news_items_days | 90 | 同上 |
| POLYMARKET.tracked_tags | 30 个标签 | 驱动 Polymarket 市场搜索 |

---

## 8. Known Gaps

以下是代码中已存在结构但未完成或有明显缺陷的部分：

### G1. signals/ 模块未接入

`signals/base.py`（BaseSignal, SignalContext, SignalResult）和 `signals/registry.py`（SignalRegistry）定义完整，但：
- 没有任何具体 Signal 实现类
- `run.py` 的 `scan_cycle()` 不调用 SignalRegistry
- 所有 UI 页面无信号展示入口

### G2. 数据清理未调度

`config.DATA_RETENTION` 定义了保留天数，但调度器中没有对应的清理 job。数据库会无限增长。

### G3. window_minutes 参数全面未使用

`price_change` 规则的 `window_minutes` 在 `evaluate_prices()`（`alerts/engine.py`）中未使用，直接用当次扫描传入 price_records 的 change_pct 判断。

`prediction_shift` 规则的 `window_minutes: 30` 同样未使用：`evaluate_predictions()` 仅查 DB 最新一条记录（`ORDER BY timestamp DESC LIMIT 1`），不做时间窗口过滤。

两个 rule_type 的 window_minutes 形同虚设。

### G4. yfinance 为日线级别

yfinance 采集使用 `period="2d", interval="1d"`，仅能得到日级别数据。美股盘中变化无法实时反映，change_pct 是昨今日收盘对比，不是实时涨跌幅。

### G5. Dune 集成路径异常

`市场监控/dune_queries.py` 存放在中文目录名的包中，在 Windows 下可能有路径问题。`DUNE_QUERY_ID_ETH_MONTHLY_TX_COUNT` 在 config 中定义但链上数据页未使用。

### G6. 新闻无 UI 历史价格图表

市场概览仅展示最新快照的 metric 卡片，无任何价格历史折线/K线图。price_snapshots 表已有完整历史数据，UI 层未消费。

### G7. 告警规则仅可文件配置

告警设置页面的规则表只读，注释说"后续版本将支持 UI 动态配置"。修改规则需要编辑 config.py 并重启进程。

### G8. jin10 App-ID 硬编码

金十数据 header `x-app-id: bVBF4FyRTn5NJF5n` 硬编码在源文件中，未来若 API 变更无法通过配置修复。

### G10. JP 债券数据完全缺失，US_10Y 被双重采集

config 的 `bonds` 配置中 `JP_10Y` 指向 `^TNX`（美国10年期国债），与 FRED 的 `US_10Y`（DGS10）采集的是同一标的，仅标签不同。config 注释也明确标注 `"JP_2Y yfinance 暂无可靠 ticker，后续补充"`。

实际效果：系统没有任何日本债券数据，但在 price_snapshots 中会同时写入 `US_10Y`（来自 FRED）和 `JP_10Y`（来自 yfinance，实为 ^TNX）。

### G9. RSS 来源 importance 未赋值

CoinDesk、CoinTelegraph 等 RSS 源的 `importance` 字段为 None，在新闻页面显示为 ⚪，无法参与 important_news 告警规则触发。
