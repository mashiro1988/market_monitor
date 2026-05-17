# 远程数据源接入 + 板块/单币管道

> **Spec 版本**：v1（2026-05-16）
> **状态**：待实施，**按阶段交付**，每个阶段单独 PR 可独立 merge
> **前置阅读**：[`docs/remote_data_format.md`](../remote_data_format.md)（服务器数据格式）

---

## 1. 目标

给 market_monitor 加三件事：
1. **接入远程 BMAC 数据源**（`root@47.243.252.92:/root/data_center/data/`），覆盖 425+ 现货 / 531+ 永续 symbol
2. **板块轮动看板** — 用 CMC 板块标签聚合，告诉我哪些板块在涨/跌
3. **单币种深度看板** — 任选 symbol 看 K 线 + 多周期涨跌 + 中性策略因子

同时：市场概览页 **加密货币板块** 只保留 BTC 和 ETH（其它资产类——美股期货、指数、债券、商品——保持原样）。

## 2. 不做的事（YAGNI）

- ❌ 不做"全市场每币种实时因子" — 改为**按需订阅**，首次访问触发回填
- ❌ 不接 funding rate（服务器 funding 目录滞后 2.5 个月，纯价格因子先跑）
- ❌ 不写 SSH 公钥认证自动化（用户手动 ssh-copy-id 一次即可，spec 给步骤）
- ❌ 不切 Postgres（SQLite 10GB 远未到瓶颈）
- ❌ 不重写中性策略框架因子代码（直接 vendor `D:\Quant\币圈中性策略\中性策略框架2.1.11\` 的 factors/）

---

## 3. 架构

```
┌─ 后台守护线程 ────────────────────────────────────────────────────┐
│ remote_puller                                                   │
│   每 60s 轮询服务器 .ready flag                                  │
│   - market_pivot_{spot,swap}_{YYYY}.pkl  (板块用)                │
│   - {SYMBOL}USDT.pkl  (订阅币用，per-symbol 1h)                  │
│   原子写入 data/remote_cache/                                     │
│   自己的锁 _puller_lock，永不阻塞主调度                            │
└──────────────────────────────────────────────────────────────────┘

┌─ APScheduler ───────────────────────────────────────────────────┐
│ job: fast_scan       (cron */5)   lock=_fast_lock     线程       │
│   现有: price + news + prediction + alert                       │
│                                                                  │
│ job: sector_scan     (cron 30 *)  lock=_sector_lock   线程       │
│   读 cache → 板块聚合 → sector_returns 入库 → sector告警         │
│                                                                  │
│ job: factor_refresh  (cron 30 *)  lock=_factor_lock   进程池(2-4)│
│   订阅币每币算因子增量 → factor_values 入库                       │
│                                                                  │
│ job: wal_checkpoint  (cron 0 4)   lock=_wal_lock      线程       │
│   PRAGMA wal_checkpoint(TRUNCATE)  每日凌晨 4 点                 │
└──────────────────────────────────────────────────────────────────┘

┌─ API on-demand ─────────────────────────────────────────────────┐
│ POST /api/focal/subscribe  -> 异步任务 focal_backfill            │
│   ProcessPool 拉 N 月历史 + 算全套因子 (1-3min, 进度返前端)       │
└──────────────────────────────────────────────────────────────────┘
```

锁分离原则：**不同 job 用不同锁**，彼此互不阻塞。同一 job 永远只能有一个实例（`max_instances=1, coalesce=True`）。

---

## 4. 数据模型（新增）

```python
# models/sector.py
class SectorReturn(Base):
    __tablename__ = "sector_returns"
    id            = Column(Integer, primary_key=True)
    snapshot_at   = Column(DateTime, nullable=False, index=True)  # UTC naive
    category      = Column(String, nullable=False)
    token_count   = Column(Integer, nullable=False)
    ret_1h        = Column(Float)
    ret_24h       = Column(Float)
    ret_168h      = Column(Float)
    ret_720h      = Column(Float)
    __table_args__ = (UniqueConstraint("snapshot_at", "category"),)

class CmcSymbolCategory(Base):
    """
    Symbol→板块 映射的本地缓存。
    刷新策略：启动时检查 MAX(updated_at)，距今 ≥ 7 天才调 CMC API（阶段 1）。
    阶段 2 起，APScheduler 每周一凌晨自动跑 refresh job。
    手动刷新：python run.py refresh-sectors
    """
    __tablename__ = "cmc_symbol_categories"
    id            = Column(Integer, primary_key=True)
    symbol        = Column(String, nullable=False, index=True)
    category      = Column(String, nullable=False, index=True)
    updated_at    = Column(DateTime, nullable=False)
    __table_args__ = (UniqueConstraint("symbol", "category"),)

# models/focal.py
class SubscribedSymbol(Base):
    __tablename__ = "subscribed_symbols"
    symbol        = Column(String, primary_key=True)
    market        = Column(String, nullable=False)  # "spot" | "swap"
    subscribed_at = Column(DateTime, nullable=False)
    last_factor_at= Column(DateTime, nullable=True)

class FactorValue(Base):
    __tablename__ = "factor_values"
    id            = Column(Integer, primary_key=True)
    symbol        = Column(String, nullable=False)
    timestamp     = Column(DateTime, nullable=False)  # UTC naive
    factor_name   = Column(String, nullable=False)
    value         = Column(Float)
    __table_args__ = (
        UniqueConstraint("symbol", "timestamp", "factor_name"),
        Index("idx_factor_symbol_ts", "symbol", "timestamp"),
    )
```

新告警规则类型：`sector_spike`（板块 N 小时涨跌阈值）。

---

## 5. 配置

`.env` 新增：
```
REMOTE_HOST=47.243.252.92
REMOTE_PORT=22
REMOTE_USER=root
REMOTE_KEY_PATH=C:\Users\Lenovo\.ssh\id_market_monitor   # 阶段 1 之后切公钥
REMOTE_PASSWORD=                                          # 切公钥后清空
REMOTE_DATA_ROOT=/root/data_center/data/
REMOTE_OFFSET=30m                                         # 默认 BMAC 偏移
CMC_API_KEY=...                                            # 从 ClsBinanceSymbol/cmc_categories.py 搬过来
CMC_CACHE_TTL_DAYS=7                                       # 板块映射本地缓存有效期
```

`config.py` 新增：
```python
LOCAL_CACHE_DIR = "data/remote_cache"
FACTOR_POOL_SIZE = min(os.cpu_count() - 1, 4)
SECTOR_WHITELIST = { ... }     # 见附录 A
```

---

## 6. 分阶段实施

每个阶段独立 PR，独立可验收，可以**做完任意阶段就停**。

### 阶段 1 — SFTP puller + CMC 板块缓存 + 板块榜单（**最小可见价值**）

**目的**：拿到一份"远程数据能用"的证明 + 板块轮动看板能看，**板块映射从真实 CMC 数据来，不是硬编码**。

**工作清单**：
1. `services/remote_fs.py` —— paramiko SFTP 客户端 + numpy shim + 原子写 + 增量拉（按 mtime/size 跳过）
2. 守护线程 `remote_puller`（先用 threading + Event，简单起步） —— 每 60s 拉 `preprocess_1h_resample/30m/market_pivot_*.pkl` 到 `data/remote_cache/`
3. **CMC 板块映射本地缓存**（核心改进，不是简单的"调 API"）：
   - `services/cmc_client.py` —— 调 `/v1/cryptocurrency/categories` + `/v1/cryptocurrency/category?id=...`
   - `cmc_symbol_categories` 表带 `updated_at` 字段
   - **缓存策略**：启动时检查表的 `MAX(updated_at)`，**距今 ≥ 7 天才刷新**，否则用现有数据
   - **只查白名单内的板块**（`SECTOR_WHITELIST` 在 `config.py`）—— 从 CMC ~350 个类别里只取我们关心的 ~50 个，API 调用从 350+ 次降到 ~50 次（≈ 2 分钟），符合 CMC 限速
   - 提供 `python run.py refresh-sectors` CLI 给手动刷新用
4. `scanners/sector_scanner.py` —— 读 cache + 查 `cmc_symbol_categories` → 算 1h/24h/168h/720h 等权涨跌 → 写 `sector_returns`
5. APScheduler 加 `sector_scan` job（cron 30 *），锁分离
6. `services/sector_service.py` + `/api/sectors/leaderboard` + `/api/sectors/{name}/tokens`
7. 前端「板块轮动」页：板块榜单 + 展开看每板块下的 symbol 涨跌

**验收**：
- `python run.py app` 启动后看到 puller 拉到文件
- `cmc_symbol_categories` 表首次启动时被填充（≤2min），后续启动跳过刷新
- `python run.py refresh-sectors` 能手动触发刷新
- 前端「板块轮动」页有数据，按 24h 涨跌排序
- `fast_scan` 在 sector_scan 跑的时候照常 5min 触发，不卡

**预估**：~2 天

**不做**：
- ❌ 板块告警（阶段 2 加）
- ❌ 自动调度 CMC 刷新（按需手动 + 启动检查就够；要自动化的话阶段 2 再加 weekly job）
- ❌ 任何因子计算

---

### 阶段 2 — 市场概览瘦身 + 板块告警 + CMC 自动周更

**目的**：把首页清理干净，板块涨跌能推 WeCom，CMC 映射不再依赖手动刷。

**工作清单**：
1. `pages/1_市场概览`（或 React 对应路由）—— **仅加密货币板块**保留 BTC 和 ETH，移除其它 alt 卡片；其它资产类（美股期货、指数、债券、商品）**完全不动**
2. APScheduler 加 `cmc_refresh` job（cron `0 5 * * 1` —— 每周一凌晨 5 点刷新 CMC 板块映射，调阶段 1 已写好的 `cmc_client`）
3. 新告警规则类型 `sector_spike`：板块 N 小时涨跌 ≥ 阈值 → WeCom
4. `alerts/engine.py` 加 `evaluate_sectors` 分支

**验收**：
- 主页只剩 BTC/ETH + 宏观资产
- `cmc_symbol_categories` 表有数据
- 模拟一个板块涨幅突破阈值 → WeCom 收到消息

**预估**：~0.5 天

---

### 阶段 3 — 单币种深度页 + 按需订阅（**不算因子**）

**目的**：任意 symbol 能看 K 线 + 多周期涨跌，**不上因子**。

**工作清单**：
1. 新表 `subscribed_symbols`
2. `services/focal_service.py`：
   - `list_available_symbols()` —— 从 `exginfo/spot_swap_matches.pkl`（缓存 24h）
   - `subscribe(symbol, market)` —— 写订阅表 + 触发后台拉历史
   - `get_candles(symbol, timeframe, range)` —— 从本地 cache 读
3. `/api/focal/symbols`（列表）、`/api/focal/subscribe`、`/api/focal/{symbol}/candles`
4. 前端「币种深度」页：
   - 搜索框 + 下拉选币（typeahead）
   - K 线图（用现有 `chart_utils.py` 模式）
   - 多周期涨跌表（1h/24h/168h/720h，对比 BTC/ETH）
5. puller 加新数据源：订阅币的 `binance_swap_1h_resample/30m/{SYMBOL}USDT.pkl`

**验收**：
- 前端能搜到/订阅任一币种
- K 线 + 多周期涨跌正常展示
- BTC/ETH 永远默认订阅（启动时自动写入）

**预估**：~1 天

---

### 阶段 4 — 因子库 + 因子计算（**并发模型登场**）

**目的**：单币深度页加因子展示，正式引入 ProcessPool。

**工作清单**：
1. Vendor 因子库：`services/factor_lib/` —— 把 `D:\Quant\币圈中性策略\中性策略框架2.1.11\` 的 `factors/` 目录抠出来（去掉 GUI / backtest 部分，只留计算函数）
2. `services/factor_compute.py` —— 包装函数：传一个 symbol 的 1h DataFrame，返回 dict[factor_name → Series]
3. ProcessPool 封装：`services/pool.py` —— 全局唯一 `_factor_pool`，对外 `submit_factor_job(symbol)` API，**60s hard timeout**
4. APScheduler 加 `factor_refresh` job（cron 30 *）—— 遍历订阅币，每币提交一个进程任务，结果写 `factor_values`
5. `/api/focal/{symbol}/factors` —— 返回最新因子值 + 历史曲线
6. 前端「币种深度」页加因子卡片区
7. on-demand backfill：`/api/focal/subscribe` 现在不止订阅，还触发一次性历史因子回填（在 ProcessPool 跑，前端轮询进度）

**验收**：
- 单币因子能算并展示
- factor_refresh 跟 fast_scan 时间重叠时，两者都正常完成
- 模拟因子 job 超时 → 该轮失败但下一轮正常，fast_scan 完全不受影响

**预估**：~2 天

---

### 阶段 5 — 监控 + 收尾

**目的**：稳态运行 + 文档同步。

**工作清单**：
1. 自监控告警 4 条（独立于业务告警，写入 `alert_logs`）：
   - fast_scan 单轮 > 60s
   - SFTP puller 连续 3 次失败
   - 因子 job 触发 timeout
   - SQLite WAL > 100MB
2. `job: wal_checkpoint`（cron 0 4）—— 每日凌晨 4 点跑 `PRAGMA wal_checkpoint(TRUNCATE)`
3. SSH 公钥认证切换：用户在本地 `ssh-keygen` + `ssh-copy-id`，`.env` 改用 `REMOTE_KEY_PATH`，清空 `REMOTE_PASSWORD`，服务器禁用密码登录
4. 更新 `ARCHITECTURE.md` / `DATAFLOW.md` / `DECISIONS.md` / `PENDING.md`（必须，AGENTS.md 强制要求）

**验收**：
- 跑一周看自监控告警有没有合理触发
- 服务器密码登录禁掉，用 key 还能正常拉数据
- 四份地图文档跟代码同步

**预估**：~0.5 天

---

**总计**：阶段 1-5 ≈ 6 天，可在任意阶段后停止。

---

## 7. 关键技术决策

### 7.1 numpy 兼容性
不升级本地 numpy，用 `sys.modules` shim 加载服务器写的 numpy 2.x pkl。代码片段见 [docs/remote_data_format.md §5](../remote_data_format.md#5-numpy-版本兼容性)。

### 7.2 原子写入
puller 写 cache 文件用 `path.tmp` + `os.replace`，避免 scanner 读到半写文件。

### 7.3 时区
服务器全部 `datetime64[ns, UTC]`，入库前 `.dt.tz_localize(None)` 转 UTC naive，跟现有 DB 一致。

### 7.4 偏移选 30m
不是 10m（最新但可能跟写入边缘竞争），不是 35m（最慢），30m 是 BMAC 4 个偏移里中等新鲜度 + 缓存稳定性最优的选择。

### 7.5 SQLite
当前 WAL 模式 OK，10GB 远未到瓶颈。换 Postgres 的触发条件是多机部署 / 多 uvicorn worker，不是大小。

### 7.6 symbol 过滤
服务器有少量乱码 symbol（中文编码错乱），入库层用 `re.match(r'^[A-Z0-9]+USDT$', name)` 过滤。

---

## 附录 A：板块白名单起步版

放在 `config.py` 里，**用户日后按需增删**：

```python
SECTOR_WHITELIST = {
    "公链龙头": ["Layer 1", "Smart Contracts", "Ethereum Ecosystem",
                "Solana Ecosystem", "BNB Chain Ecosystem", "Avalanche Ecosystem",
                "Tron Ecosystem"],
    "L2 / 扩容": ["Layer 2", "ZK Rollups", "Optimistic Rollups", "Modular Blockchain"],
    "DeFi": ["DEX", "Lending & Borrowing", "Yield Farming", "Liquid Staking",
             "Derivatives", "Perpetuals"],
    "AI 板块": ["AI & Big Data", "AI Agents", "AI Memes", "AI Agent Launchpad"],
    "Meme 主流": ["Memes", "Dog-Themed", "Cat-Themed", "Frog-Themed",
                 "Four.Meme Ecosystem", "Pump.fun Ecosystem"],
    "RWA": ["Real World Assets", "Tokenized Stocks", "xStocks Ecosystem", "Tokenized Gold"],
    "GameFi / 元宇宙": ["Gaming", "Metaverse", "Play To Earn"],
    "隐私": ["Privacy"],
    "DePIN / 存储": ["DePIN", "Filesharing", "Storage", "Cloud Computing"],
    "体育 / IP": ["Sports", "Soccer", "Sports Fan Tokens"],
    "稳定币 / 收益": ["Stablecoin", "Algorithmic Stablecoin", "Yield-Bearing Stablecoins"],
    "聪明钱组合": ["a16z Portfolio", "Multicoin Capital Portfolio", "Paradigm Portfolio",
                  "Binance Labs Portfolio", "Coinbase Ventures Portfolio"],
    "新币 / 上币事件": ["Binance Launchpool", "Binance HODLer Airdrops"],
}
```

## 附录 B：阶段 1 启动后的目录结构

```
D:\market_monitor\
├── data/
│   └── remote_cache/                   ← 新, gitignore
│       ├── market_pivot_spot_2026.pkl
│       ├── market_pivot_swap_2026.pkl
│       └── .manifest.json              ← puller 维护的 mtime/size 表
├── scanners/
│   └── sector_scanner.py               ← 新
├── services/
│   ├── remote_fs.py                    ← 新
│   ├── remote_puller.py                ← 新（守护线程）
│   ├── cmc_client.py                   ← 新（板块映射，启动 + CLI 触发）
│   └── sector_service.py               ← 新
├── models/
│   └── sector.py                       ← 新（SectorReturn, CmcSymbolCategory）
├── api/
│   └── routes.py                       ← 加 /api/sectors/*
└── frontend/src/pages/
    └── SectorRotation.tsx              ← 新
```

---

## 实施纪律

- 每阶段结束跑 `python run.py app` 端到端验证
- 每阶段 commit 信息写明阶段编号
- 每阶段 PR 描述里写"做了 §X 阶段的 1-N 项 + 通过验收 1-M"
- 文档（ARCHITECTURE/DATAFLOW/DECISIONS/PENDING）改动跟代码同 commit
- 任何阶段中途想停，告诉我，下一阶段不开工
