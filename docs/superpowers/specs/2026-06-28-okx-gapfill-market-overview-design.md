# 市场概览 · OKX 永续休市补点（gap-fill）设计 v2

- 状态：草案（已过一轮对抗式评审，待用户确认）
- 日期：2026-06-28
- 分支：`feat/onchain-market-overview`
- 范围：仅「市场概览页」的跨资产对比图与价格卡片
- 修订说明：v2 依据 5 维对抗评审修正了 anchor 时序、合成记录构造、回补撞槽、前端颜色/图例/连线模型、建表登记等实现关键点（见各节「评审修正」标注）。

## 1. 背景与动机

市场概览跟踪的传统资产（纳指期货 `NQ=F`、原油 `CL=F`、黄金 `GC=F`）数据走 yfinance，
取 CME/NYMEX/COMEX 期货的 5m K 线收盘价。CME 期货虽接近 23h/天，但**周末整段休市**
（周五 17:00 ET → 周日 18:00 ET，约 49 小时）及每天约 1h 维护断点——这些时段 yfinance 无新 bar，
对比图断档、卡片停更。

同期传统资产有了 **7×24 的 CEX 永续合约**：OKX（**已在用**的加密源）挂了 USDT 计价的
`QQQ-USDT-SWAP`（纳指100 ETF）、`CL-USDT-SWAP`（WTI）、`XAU-USDT-SWAP`（黄金），休市时段照常成交。
本设计在**不新增数据源**前提下，用这些永续在休市段补出连续曲线并视觉区分。

### 1.1 关键事实（2026-06-28 实测，UTC 周日，CME 休市）
四个 instId 周末仍出新鲜 5m K 线（直连可达，代理未开）：

| 真实标的 | OKX 代理 instId | 当时价 | 含义 | 周末更新 |
|---|---|---|---|---|
| `NQ=F` 纳指期货 | `QQQ-USDT-SWAP` | 706.6 | 纳指100 ETF（与 NQ=F **同底层指数**） | ✅ |
| `CL=F` 原油 | `CL-USDT-SWAP` | 72.5 | WTI 原油 | ✅ |
| `GC=F` 黄金 | `XAU-USDT-SWAP` | 4088 | 现货黄金 | ✅ |

排雷：`SPX-USDT-SWAP`(≈0.34) 是 SPX6900 **meme 币**，非标普500；OKX 指数永续按 **ETF 代码**命名(SPY/QQQ)。

被否决备选：币安商品永续（API "restricted location" 不可达）；xStocks/CoinGecko（需新源、指数薄盘）；
OKX-ICE 代币化指数（H2 2026、需监管批，当前不可用）。

## 2. 目标与非目标

### 2.1 目标
- `NQ=F` / `CL=F` / `GC=F` 在真实源缺失（休市）时段用 OKX 永续补出连续点。
- 补点在视觉上与真实行情**明确区分**。
- **零新增数据源**：复用现有 `OkxPriceSource`。
- 真实数据始终优先：真实 bar 可覆盖同槽合成点（见 §7.3）。

### 2.2 非目标
- 标普(`ES=F`/`^GSPC`)、道指(`YM=F`)、纳斯达克综指(`^IXIC`)——本版不补。
- 不新增 CoinGecko/xStocks/币安源。
- 不改新闻/告警/标注/预测引擎；不动加密区(BTC/ETH)；不换图表库。
- **不回补历史休市段**：补点为前向实时；停机跨周末再重启不追补那段（§9）。
- 补点是「参考价」，**绝对价位仅供指示**（§5.5 基差漂移），非交易用途。

## 3. 决策摘要（brainstorming 已拍板）
1. 混合方案：常规时段保持 yfinance 期货口径，仅休市段补链上。
2. 数据驱动判休市：不用交易日历，靠 yfinance 数据停更判定。
3. 区分标注：补点段视觉区分 + 来源说明（前端方案见 §8，含一个待选项）。
4. 范围：仅 `NQ=F→QQQ`、`CL=F→CL`、`GC=F→XAU`，全走 OKX。

## 4. 架构与组件

### 4.1 每轮扫描的数据流（评审修正：perp 每轮都取）
```
PriceScanner.scan():
  yfinance.fetch()  → 真实期货 bar（含 NQ=F/CL=F/GC=F）
  okx.fetch()/...   → 加密、债券（既有）
  _save_records(real_records)            # 既有：写真实快照（含 source-aware 覆盖，见 §7.3）
  GapFiller.run(self.okx, scan_time)     # 【新】复用同一个 ccxt exchange 实例
    exchange = scanner.okx 内部已建的 ccxt 实例（一轮一个，不每品种重建）
    for symbol, m in config.ONCHAIN_GAPFILL.items():
        perp = okx_fetch_instrument_bars(exchange, m.okx_inst, limit=3)  # 返回若干已收盘 bar 的 (bar_end, close)
        if perp 为空 / 最新 perp bar 陈旧(scan_time - bar_end > PERP_FRESH_MIN): 跳过+warn; continue
        real = 最新真实快照(symbol)   # source 不以 'okx_gapfill' 开头；且 bar_end ≤ scan_time
        staleness = scan_time - real.timestamp
        if staleness ≤ 阈值:                     # 真实在流
            if real.timestamp > anchor.real_ts:  # 真实 bar 推进 → 更新锚点
                perp_at_real = perp 中 bar_end == real.timestamp 的那根；取不到则保留旧锚点
                if perp_at_real: upsert_anchor(symbol, real.timestamp, real.close, perp_at_real.close)
            continue                             # 不补
        else:                                    # 真实源缺失 → 补点
            anchor = load_anchor(symbol)
            if anchor is None: continue          # 冷启动无锚点，待首个交易日 session 建锚
            R0 = anchor.real_close / anchor.perp_price
            synthetic = perp.latest.close * R0
            if abs(synthetic/real.close - 1) > SANITY_PCT: 跳过+warn; continue   # §7.4 防呆
            写合成快照(symbol=真实symbol, asset_class/name=取自real, price=synthetic,
                       source='okx_gapfill', timestamp=perp.latest.bar_end)
```

### 4.2 组件清单
| 组件 | 文件 | 改动 |
|---|---|---|
| OKX 取任意 instId 已收盘 bar | `scanners/sources/okx_source.py` | 新增 `fetch_instrument_bars(exchange, inst_id, limit) -> list[(bar_end, close)]`，**只复用 `_fetch_candles`+`_closed_candle_points`，绝不走 `_make_record`**（后者硬编码 crypto，见 §6.1） |
| 补点编排 | `scanners/gap_filler.py`（新） | 判休市 + 锚点 + 比率合成 + 防呆 + 写库 |
| 锚点持久化 | `models/gapfill_anchor.py`（新） | 小表，每品种一行 |
| **登记模型** | `models/__init__.py` | **必须**加 `from models.gapfill_anchor import GapfillAnchor`，否则 `create_all` 不建表（见 §11.1） |
| source-aware 覆盖 | `scanners/price_scanner.py` `_save_records` | 真实行可覆盖同槽 `okx_gapfill` 行；合成行不覆盖任何行（§7.3） |
| 编排接入 | `scanners/price_scanner.py` | `scan()` 末尾调 `GapFiller.run(self.okx, scan_time)` |
| 配置 | `config.py` | `ONCHAIN_GAPFILL` + `GAPFILL_STALENESS_MINUTES` + `GAPFILL_PERP_FRESH_MINUTES` + `GAPFILL_SANITY_PCT` + `GAPFILL_ENABLED` |
| API 透出来源 | `schemas/market.py` + `services/market_service.py` | `MarketHistoryPoint` 加 `source`；`get_history` 查询补选 `source` 并填入 point |
| 前端类型 | `frontend/src/api/types.ts` | `MarketHistoryPoint` 加 `source: string \| null` |
| 前端对比图 | `MarketPage.tsx` + `Charts.tsx` | 见 §8（默认行为保持不变的 opt-in 改造） |
| 前端卡片 | `MarketPage.tsx` | `source` 以 `okx_gapfill` 开头 → "代理价"角标 |

## 5. 核心机制：比率锚定合成补点

### 5.1 单位错配
QQQ ETF≈706 vs NQ=F≈22000（点位），**底层同为纳指100**，差一个固定倍率+期货基差。直接塞会归一化出垃圾值。金/油倍率≈1 但仍有小基差。

### 5.2 公式（含算例）
```
R0 = anchor.real_close / anchor.perp_price
synthetic(t) = perp_close(t) * R0
```
算例（纳指）：anchor 时 real_close=22000、perp=706 → R0≈31.16；某刻 perp=710 →
synthetic = 710 × 31.16 ≈ 22124。**乘不是除**；synthetic 与 NQ=F 同量级，锚在期货价位。

- 休市第一根 `perp(t)≈anchor.perp_price` → `synthetic≈anchor.real_close` → 与期货曲线衔接。
- 重开市真实 bar 接管，锚点随新真实 bar 重算。

### 5.3 锚点捕获时序（评审修正：核心正确性点）
- **perp 每轮都取**（不只在补点分支）。原因：常规扫描的 `okx.fetch()` 只取 crypto(BTC/ETH)，
  三个 perp 在交易时段从不被拉；而锚点必须在交易时段捕获，故 `GapFiller` 每轮对三品种各取一次 perp
  （交易时段 3 次/5min OKX 调用，廉价；复用同一 ccxt 实例）。
- **仅当真实 bar 推进时更新锚点**（`real_ts > anchor.real_ts`），并用 **bar_end 对齐**的 perp：
  取 perp 序列中 `bar_end == real_ts` 的那根做 `perp_price`。取不到匹配（两源差一根 bar）则
  **本轮不更新锚点，保留上一个**（至多 1 根 ~5min 旧）。
- **不在"陈旧但未超阈值"窗口更新锚点**：否则会把周五 17:00 的 real_close 与 18:00 的 perp 配对，R0 偏掉。
- 锚点更新要求：本轮既有推进的真实 bar、又取到 bar_end 对齐的 perp；任一缺失保留旧锚点。
- **忽略未来戳真实 bar**：yfinance 有 `end>now` 的 fallback 分支（`yfinance_source.py:91-99`）。
  staleness/锚点只采纳 `bar_end ≤ scan_time` 的已确认 bar，避免负 staleness 与 real_ts 回退被单调判据拒收。

### 5.4 锚点存储
```python
class GapfillAnchor(Base):
    __tablename__ = "gapfill_anchor"
    symbol     = Column(String(30), primary_key=True)  # "NQ=F"
    real_ts    = Column(DateTime, nullable=False)
    real_close = Column(Float, nullable=False)
    perp_price = Column(Float, nullable=False)          # bar_end 对齐的 perp close
    updated_at = Column(DateTime, default=datetime.utcnow)
```
持久化以扛周末进程重启。

### 5.5 基差漂移（评审修正：诚实声明，原 v1 把它 assert 掉了）
R0 在整段（最长 ~49h 周末）冻结，**隐含假设：期货–ETF 基差在该时段近似恒定**。但 NQ=F 是 CME 指数**期货**
（含期货基差+roll），QQQ-perp 跟踪 QQQ ETF 现货（含 ETF 跟踪误差 + USDT 资金费/脱锚）。基差会因
**分红除息日、利率预期变动、USDT 脱锚/资金费**而漂移，故合成曲线的**绝对价位**会随休市时长累积偏离
"期货真实应开位"，整段形状被冻结基差偏置——不止重开那一跳。
处置：①前端把代理段标为"指示性价位"；②`change_24h` 等跨段卡片增量同受影响（§7.4）；
③可选增强（非本版必须）：休市越久，proxy 线越淡，以视觉传达不确定性递增。

## 6. OKX 源改造（零新源的边界）

### 6.1 不碰 `_make_record`（评审修正）
`_fetch_candles`(okx_source.py:61) 与 `_closed_candle_points`/`_pick_last_closed`(72-114) 对 instId 无关，
`QQQ-USDT-SWAP` 等可直接复用。但 `_make_record`(232-245) **硬编码** `asset_class="crypto"`、
`symbol=f"{x}/USDT"`、`source="okx_swap_5m"`。故新方法 `fetch_instrument_bars` **返回原始 `[(bar_end, close)]`**，
不构造 PriceRecord、不调用 `_make_record`。合成记录的 symbol/asset_class/name/price/source 全部由 `GapFiller`
依 `config.ONCHAIN_GAPFILL` 与"该真实 symbol 的最近真实快照"构造。

### 6.2 exchange 复用与代理前置（评审修正）
- `GapFiller.run(exchange, ...)` 接收 `PriceScanner.self.okx` 内部已建的 ccxt 实例，一轮一个，
  不每品种 `_make_exchange()`。
- `OkxPriceSource.proxy` 在 `__init__` 固定为 `config.PROXY`；`PROXY` 仅在 `setup_runtime()` 后非空。
  `GapFiller` 复用扫描进程内的 `okx` 实例即满足该前置。**需确认部署服务器（腾讯云日本）对 OKX 可达**
  （§1.1 的直连可达是本地观测，非部署保证）。

### 6.3 perp 自身陈旧检测（评审修正）
`_pick_last_closed` 无陈旧守卫，OKX 卡顿时会返回数小时前的旧 bar，叠加 (symbol,ts) 去重 → 合成曲线
静默平直却报成功。故取到 perp 后检查 `scan_time - 最新perp.bar_end ≤ GAPFILL_PERP_FRESH_MINUTES`（默认 12），
超时则本轮跳过补点 + warn。

## 7. 判休市、存储与撞槽

### 7.1 判休市（数据驱动）
```
staleness = scan_time − 最新真实快照.timestamp   # 真实 = source 不以 'okx_gapfill' 开头，且 bar_end ≤ scan_time
staleness ≤ GAPFILL_STALENESS_MINUTES → 真实在流，不补
staleness  > 阈值                      → 真实源缺失 → 按 §4.1 写合成点
```
休市时 yfinance 不返回新 bar，"数据停更"即休市信号，**不依赖任何交易日历**（DST/假期/各 session 自动免疫）。

### 7.2 阈值
- `GAPFILL_STALENESS_MINUTES` 默认 60（env 可覆盖；遵循"不静默改用户校准值"）。
- 不取更小：yfinance 有 429/抖动丢 bar，小阈值会被误触发、真实/代理横跳。
- 60min 足够：周末 ~49h 远超；代价仅"周五收盘后头 60min 暂不补"。CME 每日 ~1h 维护断点被自然跳过。
- 阈值只影响"开始补"延迟；重开市一根新鲜真实 bar 即令 staleness 回落，下一轮停止补点。

### 7.3 撞槽策略（评审修正：原 v1 错称"无冲突"）
盘中 yfinance 中断会触发补点写合成 `NQ=F` 行；yfinance 恢复后 `run.py:281` 的滚动回补
(`backfill_range`→`_save_records`) 会取回**真实** `NQ=F` 行落到同 `(symbol,timestamp)`。现 `_save_records`
(price_scanner.py:176-180) 撞已存即跳过 → 真实行被丢、合成行永久遮蔽真实值。
**决议：真实优先。** `_save_records` 改为 source-aware：
- 入库行为真实源、同槽已存为 `okx_gapfill` 行 → **更新覆盖**为真实值。
- 入库行为 `okx_gapfill`、同槽已存任意行 → 跳过（不覆盖）。
- 真实↔真实、合成↔无 的既有行为不变。
（这是对已校准函数的最小定向改动，仅新增"合成可被真实覆盖"一条路径。）

### 7.4 合成点参与基线与卡片增量（评审修正）
合成点写入既有 `price_snapshots`，**同一 symbol**、`asset_class`/`name` 取自该 symbol 最近真实快照，
`source="okx_gapfill"`，`timestamp=perp.bar_end`（对齐 5m 网格）。归一化 `normalize_prices` 零改动
（合成点已是期货量级）。但须明确：合成点**会**被 `_window_baseline_prices` 选为归一基准、会进入
`get_latest_prices`/`_change_pct_from_latest` 的 5m/1h/24h 卡片增量（这些函数仅特判 crypto，对 futures/commodity 不设防）。
- 防呆（§4.1 `SANITY_PCT`，默认 0.2）：`|synthetic/最近真实close − 1| > 20%` 视为坏锚点/坏价，跳过+warn，
  宁可留洞也不写出会污染基线的垃圾值。
- 边界点 `change_pct`：`_save_records` 按同 symbol 上一行链算，真实→合成边界那根的 `change_pct` 会含基差跳。
  视为可接受（基差小）；如不希望，§12 留待定。

## 8. 前端：区分标注

> 评审指出"同色双线"不是小改：`Charts.tsx` 颜色按数组下标(palette[index])、`connectNulls` 全局、
> `<Legend/>` 自动按 dataKey、默认 `<Tooltip/>`。直接加 proxy 键会**移位所有颜色 + 图例翻倍 +
> 实线 connectNulls 跨周末直连盖住虚线**。本节按"对 MultiLineChart 做**默认行为不变的 opt-in 改造**"来设计，
> 避免回归其它调用方（如标注页）。**§8.A 与 §8.B 二选一，待用户拍板**。

### 8.A 方案A：逐序列虚线代理（精确，改动较大）— 推荐
- `schemas`+`get_history`+`types.ts`：`MarketHistoryPoint.source` 必须先落地并逐点透出，
  buildHistoryChart 才能按 `point.source.startsWith('okx_gapfill')` 路由。
- `buildHistoryChart`(MarketPage.tsx)：每 symbol 拆 `key`(真实) 与 `key__proxy`(代理)；真实点写 `row[key]`，
  代理点写 `row[key__proxy]`；**真实↔代理边界那点两键各写一份**，使虚线与实线在断点相接。
- `MultiLineChart`(Charts.tsx) 新增**可选** `keyStyles?: Record<string,{color?;dashed?;connectNulls?;hideLegend?}>`；
  未传时**完全保持现状**（palette-by-index、实线、connectNulls=true、图例全显）。传入时：
  - 颜色由**按 symbol 稳定解析的色表**给出（不再随 keys 数量移位）；proxy 键复用其 base symbol 同色。
  - 真实键 `connectNulls=false`（休市处实线断开），proxy 键 `connectNulls=true`（桥接自身采样）。
  - proxy 键 `legendType="none"`（图例不翻倍）。
- Tooltip："代理价（OKX QQQ 永续）"需自定义 `Tooltip content`。**为控范围，本版可省略逐点 tooltip 文案**，
  以 虚线 + 卡片角标 + 图表下一行图例说明 表达；自定义 tooltip 列为可选增强。
- 提供 `proxySuffix` 常量 + `key→symbol` 解析助手（单一真源），供色表与 tooltip 共用，避免字符串解析脆裂。

### 8.B 方案B：休市时段阴影带（简单，改动小）— 备选
- 单 symbol 单线不变（颜色/图例/connectNulls 全不动，零回归）。合成点照常并入序列，曲线连续。
- `MultiLineChart` 加可选 `shadedBands?: {x1;x2;label}[]`，渲染 `<ReferenceArea>` 标出"休市代理价时段"。
- buildHistoryChart 从逐点 `source` 推出代理时间区间（min/max gapfill 时间戳）传入。
- 取舍：阴影带是**时间维**标注，不区分"带内哪些序列是代理"（BTC 周末是真实 24/7）；需图例文案说明
  "灰区＝传统品种休市代理价(OKX 永续)"。改动量与回归风险远小于 A。

### 8.C 卡片角标（两方案通用）
`MarketLatestItem.source`（已存在）以 `okx_gapfill` 开头 → 卡片显示"代理价"角标 + tooltip 注明来源 perp
（前端按 symbol 经 `ONCHAIN_GAPFILL` 反查 perp 名）。

## 9. 错误处理与边界

| 情形 | 行为 |
|---|---|
| perp 取价失败 | 本轮跳过该 symbol，下轮重试；不影响其余 |
| perp 返回非正/空 | 跳过本轮补点 |
| **perp 自身 bar 陈旧**（§6.3） | 跳过+warn，不写陈旧合成点 |
| 锚点缺失（冷启动/表新建后未历一个交易 session） | 暂不补，待首个 session 建锚；记 warning（见下条精确前提） |
| **冷启动前提** | 补点仅在"自 `gapfill_anchor` 表建立以来至少捕获过一次交易 session 锚点"后才产出；若迁移/部署首次落在休市段，则该休市段无补点（同冷部署，可接受） |
| 停机跨周末后重启 | 锚点持久化仍在 → 重启即续补；停机期间那段**不追补**（前向实时） |
| 盘中 yfinance 中断后回补撞槽 | 真实行覆盖同槽合成行（§7.3） |
| 未来戳/非单调真实 bar | 仅采纳 `bar_end ≤ scan_time` 的已确认 bar（§5.3） |
| 合成价偏离最近真实 > `SANITY_PCT` | 跳过+warn，留洞不污染基线（§7.4） |
| `GAPFILL_ENABLED=False` 或阈值 ≤ 0 | 整体禁用 |

## 10. 测试策略

后端 pytest（`pytest.ini` testpaths=tests）；前端 vitest（与 pytest 分开，§10.3）。
**内存库 fixture 必须先 `import models`（或直接 import `GapfillAnchor`）再 `create_all`**，否则新表 "no such table"。

### 10.1 后端单测（`tests/test_gap_filler.py` 新）
- 比率锚定数值：金/油 R0≈1、纳指 R0≈31 两组；synthetic=perp×R0 方向正确。
- 判休市三态：新鲜不补 / 阈值内丢一两根不补 / 超阈值补。
- 锚点：仅真实 bar 推进时更新；bar_end 对齐取 perp；取不到匹配则保留旧锚点；阈值窗口内不更新；忽略未来戳 bar。
- perp 陈旧 / perp 失败 / 非正价 / 锚点缺失 / 合成超 SANITY_PCT → 各跳过路径。
- 重启后从持久化锚点续补。

### 10.2 集成（扩 `tests/test_price_*` 若存在，否则新建）
- yfinance 陈旧 + OKX perp 现价 → 跑一轮 `GapFiller.run` → 断言新增 `source="okx_gapfill"`、值=perp×R0、
  symbol=NQ=F、asset_class=futures、timestamp 对齐。
- 反向：yfinance 新鲜 → 不产出合成点。
- **撞槽**：先有合成 NQ=F@T，再 `backfill_range` 落真实 NQ=F@T → 断言真实覆盖合成（§7.3）。

### 10.3 服务/前端
- `get_history` 透出 `source`（扩 `tests/test_market_history.py`；其 fixture 已 `import models`+`create_all`）。
- 前端（vitest）：buildHistoryChart 按 source 路由真实/代理键、边界点双写；选定方案后加对应断言
  （A：proxy 键虚线、真实键 connectNulls=false、NQ=F 颜色不因 GC=F/CL=F 增 proxy 键而变；B：阴影带区间正确）。
- 既有前端测试不回归（MultiLineChart 默认路径不变）。

## 11. 建表与配置

### 11.1 建表路径（评审修正）
本应用经 `database.create_tables()` → `import models` → `Base.metadata.create_all` 建表，**无 Alembic**。
`create_all` 只对"已 import 注册到 `Base.metadata`"的模型发 CREATE TABLE。故**必须**在 `models/__init__.py`
加 `from models.gapfill_anchor import GapfillAnchor`。新表无需改 `_ensure_sqlite_schema`（那只补旧表新列）。

### 11.2 配置默认值
```python
ONCHAIN_GAPFILL = {
    "NQ=F": {"okx_inst": "QQQ-USDT-SWAP"},
    "CL=F": {"okx_inst": "CL-USDT-SWAP"},
    "GC=F": {"okx_inst": "XAU-USDT-SWAP"},
}
GAPFILL_SOURCE = "okx_gapfill"   # 单一哨兵前缀（11 字符，安全 < String(30)）；前端按 symbol 反查 perp 名
GAPFILL_ENABLED            = os.getenv("GAPFILL_ENABLED", "1") in {"1","true","yes","on"}
GAPFILL_STALENESS_MINUTES  = int(os.getenv("GAPFILL_STALENESS_MINUTES", "60"))
GAPFILL_PERP_FRESH_MINUTES = int(os.getenv("GAPFILL_PERP_FRESH_MINUTES", "12"))
GAPFILL_SANITY_PCT         = float(os.getenv("GAPFILL_SANITY_PCT", "0.2"))
```

## 12. 待定项
1. **前端方案 A（精确虚线）vs B（阴影带）** —— §8，影响前端改动量与回归面，待用户定。
2. 边界点 `change_pct` 是否容忍含基差跳（§7.4）；默认容忍。
3. 代理段绝对价位是否随休市时长渐淡以示不确定（§5.5 可选增强）；默认不做。
