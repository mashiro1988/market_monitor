# 市场概览 · OKX 永续休市补点（gap-fill）设计

- 状态：草案（待评审）
- 日期：2026-06-28
- 分支：`feat/onchain-market-overview`
- 范围：仅「市场概览页」的跨资产对比图与价格卡片

## 1. 背景与动机

市场概览跟踪的传统资产（纳指期货 `NQ=F`、原油 `CL=F`、黄金 `GC=F`）数据走 yfinance，
取 CME/NYMEX/COMEX 期货的 5m K 线收盘价。CME 期货虽然接近 23h/天，但**周末整段休市**
（周五 17:00 ET → 周日 18:00 ET，约 49 小时），以及每天约 1h 维护断点——这些时段
yfinance 没有新 bar，对比图曲线断档、卡片停更。

同时，传统资产现在有了 **7×24 的 CEX 永续合约**：OKX（我们**已经在用**的加密源）挂了
USDT 计价的 `QQQ-USDT-SWAP`（纳指100 ETF）、`CL-USDT-SWAP`（WTI）、`XAU-USDT-SWAP`（黄金），
休市时段照常成交、出 5m K 线。本设计在**不新增任何数据源**的前提下，用这些永续在休市段
补出连续曲线，并在视觉上与真实行情区分。

### 1.1 关键事实（2026-06-28 实测）

探测 OKX 公开 API（直连可达，代理未开），周末时段四个 instId 仍在出新鲜 5m K 线：

| 真实标的 | OKX 代理 instId | 当时价 | 含义 | 周末是否更新 |
|---|---|---|---|---|
| `NQ=F` 纳指期货 | `QQQ-USDT-SWAP` | 706.6 | 纳指100 ETF（与 NQ=F **同底层指数**） | ✅ |
| `CL=F` 原油 | `CL-USDT-SWAP` | 72.5 | WTI 原油 | ✅ |
| `GC=F` 黄金 | `XAU-USDT-SWAP` | 4088 | 现货黄金 | ✅ |

注意排雷：`SPX-USDT-SWAP`（≈0.34）是 SPX6900 **meme 币**，不是标普500；OKX 指数永续按
**ETF 代码**命名（SPY/QQQ），不是指数名（SPX/NDX）。

被否决的备选：
- 币安商品永续（XAUUSDT/CLUSDT 等）：API 受地域限制（"restricted location"），本地与部署环境不可达。
- xStocks / CoinGecko：需新增数据源，违背"减少数据源"，且指数代币薄盘。
- OKX-ICE 代币化指数：H2 2026 才上线、需监管批，当前不可用。

## 2. 目标与非目标

### 2.1 目标
- 市场概览页的 `NQ=F` / `CL=F` / `GC=F` 三个品种，在真实源缺失（休市）时段用 OKX 永续补出连续点。
- 补点在视觉上与真实行情**明确区分**（对比图虚线异色 + 卡片角标）。
- **零新增数据源**：复用现有 `OkxPriceSource`。
- 归一化逻辑零改动（补点处理成与期货曲线绝对价连续）。

### 2.2 非目标（明确不做）
- **标普**（`ES=F` / `^GSPC`）、**道指**（`YM=F`）、**纳斯达克综指**（`^IXIC`）——本版不补。
  - 标普：默认对比图当前根本没有标普品种；道指无 OKX DIA 类 ETF 永续；综指无任何 24/7 标的。
- 不新增 CoinGecko / xStocks / 币安等任何源。
- 不改新闻 / 告警 / 标注 / 预测引擎。
- 不动加密区（BTC/ETH 本就 7×24）。
- 不改图表库（继续 recharts）。
- **不回补历史休市段**：补点是前向实时行为；停机跨周末再重启不追补那段（见 §9）。
- 补点是"参考价"，非交易用途。

## 3. 决策摘要（本次 brainstorming 已拍板）

1. **混合方案**：常规时段保持 yfinance 期货口径，仅休市段补链上。
2. **数据驱动判休市**：不用交易日历，靠 yfinance 数据停更判定。
3. **区分标注**：补点段虚线异色 + hover 注明来源。
4. **范围**：仅 `NQ=F→QQQ`、`CL=F→CL`、`GC=F→XAU`，全走 OKX。

## 4. 架构与组件

```
PriceScanner.scan()                      # 既有：每 5min 一轮
  ├── yfinance.fetch()        → 真实期货 bar（含 NQ=F/CL=F/GC=F）
  ├── okx.fetch() / coingecko / cnbc ...  # 既有加密、债券
  ├── _save_records(...)                  # 既有：写 price_snapshots
  └── GapFiller.run(scan_time)            # 【新】在常规写入之后执行
        for symbol in config.ONCHAIN_GAPFILL:
          real = 最新真实快照(symbol)               # source 不以 gapfill 前缀
          if 有新鲜真实 bar(staleness ≤ 阈值):
              upsert_anchor(symbol, real, perp_now) # 仅在真实 bar 推进时更新锚点
              continue                              # 真实数据在流，不补
          else:                                     # 真实源缺失 → 补点
              anchor = load_anchor(symbol)
              perp_now = okx.fetch_instrument_last(inst_id)
              synthetic = perp_now.price * anchor.real_close / anchor.perp_price
              save synthetic 到 price_snapshots(symbol, source="okx_gapfill", ...)
```

### 4.1 组件清单

| 组件 | 文件 | 改动 |
|---|---|---|
| OKX 取任意 instId 现价 | `scanners/sources/okx_source.py` | 新增 `fetch_instrument_last(inst_id) -> PriceRecord\|None`，复用既有 `_fetch_candles` + `_pick_last_closed` |
| 补点编排 | `scanners/gap_filler.py`（新） | 判休市 + 比率锚定 + 写库 |
| 锚点持久化 | `models/gapfill_anchor.py`（新） | 小表，每品种一行 |
| 配置 | `config.py` | `ONCHAIN_GAPFILL` 映射 + `GAPFILL_STALENESS_MINUTES` + `GAPFILL_ENABLED` |
| 编排接入 | `scanners/price_scanner.py` | `scan()` 末尾调用 `GapFiller.run()` |
| API 透出来源 | `schemas/market.py` + `services/market_service.py` | `MarketHistoryPoint` 加 `source`；`get_history` 查询带上 `source` |
| 前端对比图 | `frontend/src/pages/MarketPage.tsx` + `components/Charts.tsx` | real/proxy 双线拆分 + 虚线异色 |
| 前端卡片 | `MarketPage.tsx` | `source` 以 `okx_gapfill` 开头 → "代理价"角标 |
| 类型 | `frontend/src/api/types.ts` | `MarketHistoryPoint` 加 `source` |

## 5. 核心机制：比率锚定合成补点

### 5.1 单位错配问题
QQQ ETF ≈ 706，但 NQ=F 期货 ≈ 22000（点位）；二者**底层同为纳指100**，仅
"ETF 价 vs 期货点位"的固定倍率 + 期货基差之差。直接把 706 塞进 NQ=F 序列，归一化会算出
约 −97% 的垃圾值。金/油倍率≈1 但仍有小基差。

### 5.2 锚定公式
休市开始的瞬间冻结一个比率 `R0`，整段休市用它把 perp 价缩放到期货价位：

```
R0 = anchor.real_close / anchor.perp_price          # 锚点：最后一根真实 bar 与同刻 perp 价
synthetic(t) = perp_price(t) * R0
            = perp_price(t) * anchor.real_close / anchor.perp_price
```

- 休市第一根：`perp(t) ≈ anchor.perp_price` → `synthetic ≈ anchor.real_close` → **与期货曲线无缝衔接**。
- 休市期间：perp 怎么动，synthetic 就按比例怎么动，但点位锚在期货量级。
- 重开市：yfinance 出新真实 bar，真实点接管；锚点随新真实 bar 重算。唯一跳变 = 重开瞬间的
  真实基差跳空（诚实、且补点段已标为代理价）。

### 5.3 锚点的捕获时机（关键正确性点）
**只在"真实 bar 真正推进时"更新锚点**（`real_ts > anchor.real_ts`），同一轮一并记录当时 perp 价，
保证 `real_close` 与 `perp_price` 时间对齐（同一 5min 扫描）。

反例（错误实现）：若在"staleness ≤ 阈值但无新 bar"的窗口里也更新锚点，会把周五 17:00 的
`real_close` 和 18:00 的 `perp_price` 配成一对，R0 偏掉。阈值窗口内**不更新锚点**。

锚点更新要求：本轮**既有新鲜真实 bar、又成功取到 perp 价**；任一缺失则保留上一个锚点。

### 5.4 锚点存储
新表 `gapfill_anchor`（每品种一行，upsert）：

```python
class GapfillAnchor(Base):
    __tablename__ = "gapfill_anchor"
    symbol      = Column(String(30), primary_key=True)  # "NQ=F"
    real_ts     = Column(DateTime, nullable=False)       # 最后真实 bar 的 timestamp(UTC naive)
    real_close  = Column(Float, nullable=False)
    perp_price  = Column(Float, nullable=False)          # 同刻 OKX perp 价
    updated_at  = Column(DateTime, default=datetime.utcnow)
```

持久化以**扛周末进程重启**：重启后锚点仍在，补点无缝续上。
（不复用 price_snapshots 推导，是为了避免再存一条 perp 原始序列污染概览品种列表。）

## 6. 判"休市"：数据驱动的陈旧检测

### 6.1 信号
休市时 yfinance 不返回新 K 线——`NQ=F` 等最新 bar 停在周五收盘那根。"数据停更"本身即休市信号，
**不依赖任何交易日历**（夏令时/假期/各品种 session 全部自动免疫）。

### 6.2 逐 symbol 算法（每轮扫描）
```
staleness = scan_time − 最新真实快照.timestamp     # 真实 = source 不以 "okx_gapfill" 开头
若 staleness ≤ GAPFILL_STALENESS_MINUTES:
    真实行情在流 → 不补；若本轮真实 bar 推进则更新锚点
若 staleness  > GAPFILL_STALENESS_MINUTES:
    真实源缺失 → 取 perp 价，按 §5.2 写合成点
```

### 6.3 阈值取值
- 默认 `GAPFILL_STALENESS_MINUTES = 60`（环境变量可覆盖；校准值后续可调，遵循"不静默改用户校准配置"）。
- **不取更小值**（如 15min）的原因：yfinance 本身有 429/抓取抖动，偶尔丢一两根 bar；
  小阈值会被误触发，导致真实段/代理段反复横跳、图丑且误标。
- 60min 足以：周末 ~49h 远超阈值；代价仅"周五收盘后头 60min 暂不补"（相对 49h 可忽略）。
- 每天 ~1h 的 CME 维护断点正好被 60min 自然跳过（本就不在乎补那 1h）。
- 阈值只影响"开始补"的延迟；重开市后只要出一根新鲜真实 bar，staleness 立即回落，
  **下一轮即停止补点**，不会"重开了还在补"。

### 6.4 语义放宽（诚实标注）
唯一混淆源：yfinance **整批抓取在开市时段连续失败 > 阈值**，会被当成休市而开始补。
但此时 perp 与期货在开市时段本就贴合，合成值是合理降级，只是"休市"标签不准。

因此把语义从严格"市场休市"放宽为**"实时源缺失 → OKX 永续代理"**：前端标签写
**"代理价（OKX QQQ 永续）"**，不强行说"休市"。这样无论成因是周末还是抓取中断，行为都正确——
不存在真正误判，只是诚实告知"这段是代理价"。

## 7. 存储与标注

- 合成点写入既有 `price_snapshots`，**同一 symbol**（`NQ=F` 等）、同 `asset_class`/`name`：
  - `timestamp` = 本轮 perp 5m bar 的 bar_end（对齐 5min 网格）
  - `price` = synthetic；`prev_price`/`change_pct` 按相邻点算（复用 `_save_records` 逻辑）
  - `source = "okx_gapfill"`（哨兵前缀；具体哪个 perp 由 symbol→映射可反查）
- `(timestamp, symbol)` 唯一索引保证无缝并入序列；休市段无真实 bar，无同刻冲突。
- **归一化代码零改动**：`normalize_prices` 仍以窗口起点基准价归一，合成点已是期货量级。
- 合成点**长期保留**（受既有 30 天 price_snapshots 清理约束），历史周末段会持续显示代理曲线。

## 8. API 与前端

### 8.1 后端透出来源
- `MarketHistoryPoint` 增 `source: str | None`。
- `market_service.get_history` 的查询补选 `PriceSnapshot.source` 并填入每个 point。
- `MarketLatestItem.source` 已存在（卡片角标直接用）。
- CSV 导出已含来源列，无需改。

### 8.2 对比图：同色双线分段（recharts）
recharts 单条 `<Line>` 无法按点分段虚实。方案：每个 symbol 拆成两条线 key：
- `key`（真实）：proxy 点处置 `null`
- `key__proxy`（代理）：真实点处置 `null`，**边界点两侧各塞一份**（真实→代理切换处把该点同时写入两 key），
  靠既有 `connectNulls` 把虚线接上、避免断线。
- `buildHistoryChart`（`MarketPage.tsx`）按 `point.source` 路由到对应 key。
- `MultiLineChart`（`Charts.tsx`）新增入参标记哪些 key 为 proxy → 渲染 `strokeDasharray`（虚线）
  且**与对应真实线同色**（颜色按基础 symbol 解析，proxy key 复用同色）。hover tooltip 显示
  "代理价（OKX QQQ 永续）"。

### 8.3 卡片角标
`MarketLatestItem.source` 以 `okx_gapfill` 开头 → 卡片显示"代理价"小角标 + tooltip 注明来源 perp。

## 9. 错误处理与边界

| 情形 | 行为 |
|---|---|
| 补点轮 OKX perp 取价失败 | 本轮跳过该 symbol（留 5min 洞），下轮重试；不影响其余扫描 |
| perp 返回非正/空价 | 视为无效，跳过本轮补点 |
| 锚点缺失（如全新部署恰逢周末，尚无真实 bar） | 无法定 R0 → 暂不补，待首根真实 bar 建立锚点后再补；记 warning |
| 停机跨周末后重启 | 锚点持久化仍在 → 重启即续补；**但停机期间那段不追补**（前向实时，见 §2.2） |
| 重开市边界 | 真实 bar 出现即停补；timestamp 不与合成点冲突（休市段时间戳不重叠） |
| `GAPFILL_ENABLED=False` 或阈值 ≤ 0 | 整个补点禁用 |
| 既有 `backfill_missing_history` | 仅用 yfinance/OKX 历史补**真实** bar，不产出合成点（口径不变） |

## 10. 测试策略

遵循项目既有 pytest 约定（`pytest.ini`、`tests/test_market_history.py`、`tests/test_price_*`）。

### 10.1 后端单测（`tests/test_gap_filler.py` 新）
- 比率锚定数值：给定 anchor + perp → synthetic 正确（金/油 R0≈1、纳指 R0≈31 两组）。
- 判休市三态：新鲜（不补）、刚丢一两根仍在阈值内（不补）、超阈值（补）。
- 锚点仅在真实 bar 推进时更新；阈值窗口内不更新（§5.3 反例守卫）。
- 重开市后停补、锚点重算。
- 错误路径：perp 失败 / 锚点缺失 / 非正价。
- 重启后从持久化锚点续补。

### 10.2 集成
- 模拟 yfinance 返回陈旧 + OKX perp 返回现价 → 跑一轮 `GapFiller.run` → 断言 price_snapshots
  新增一条 `source="okx_gapfill"`、值 = perp×R0、timestamp 对齐。
- 反向：yfinance 新鲜 → 不产出合成点。

### 10.3 服务/前端
- `get_history` 透出 `source`（扩 `tests/test_market_history.py`）。
- `buildHistoryChart` 把真实/代理点路由到不同 key、边界点两侧落点（`Charts.test` 或新测）。
- 既有前端测试不回归。

## 11. 配置（默认值）

```python
# config.py
ONCHAIN_GAPFILL = {
    "NQ=F": {"okx_inst": "QQQ-USDT-SWAP", "source": "okx_gapfill"},
    "CL=F": {"okx_inst": "CL-USDT-SWAP",  "source": "okx_gapfill"},
    "GC=F": {"okx_inst": "XAU-USDT-SWAP", "source": "okx_gapfill"},
}
GAPFILL_ENABLED = os.getenv("GAPFILL_ENABLED", "1") in {"1","true","yes","on"}
GAPFILL_STALENESS_MINUTES = int(os.getenv("GAPFILL_STALENESS_MINUTES", "60"))
```

## 12. 开放问题 / 待确认
- `source` 哨兵：用单一 `"okx_gapfill"`（前端按 symbol 反查 perp 名）vs 带后缀
  `"okx_gapfill:QQQ"`（表格/CSV 更自解释）。当前倾向单一前缀，前端反查。
- 代理价是否参与 `change_24h` 等卡片涨跌计算：默认参与（同序列连续），如不希望可在 `get_latest_prices` 排除。
```
