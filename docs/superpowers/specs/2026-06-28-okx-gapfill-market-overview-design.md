# 市场概览 · OKX 永续休市补点（gap-fill）设计 v3

- 状态：草案（已过两轮对抗式评审，待用户确认）
- 日期：2026-06-28
- 分支：`feat/onchain-market-overview`
- 范围：仅「市场概览页」的跨资产对比图与价格卡片
- 修订史：v2 修 anchor 时序/撞槽/前端模型/建表登记；v3 修 v2 新引入的 blocker（_save_records 覆盖须 UPDATE 非 add）、防呆基准、exchange 来源、anchor 取数窗口、前端连线/阴影带端点等（见各节「评审修正」）。

## 1. 背景与动机

市场概览跟踪的传统资产（纳指期货 `NQ=F`、原油 `CL=F`、黄金 `GC=F`）走 yfinance，取 CME/NYMEX/COMEX 期货 5m K 线收盘价。CME 期货虽接近 23h/天，但**周末整段休市**（周五 17:00 ET → 周日 18:00 ET，约 49h）及每天约 1h 维护断点这些时段 yfinance 无新 bar，对比图断档、卡片停更。

同期这些资产有了 **7×24 的 CEX 永续**：OKX（**已在用**的加密源）挂了 USDT 计价的 `QQQ-USDT-SWAP`（纳指100 ETF）、`CL-USDT-SWAP`（WTI）、`XAU-USDT-SWAP`（黄金）。本设计在**不新增数据源**前提下，用这些永续在休市段补出连续曲线并视觉区分。

### 1.1 关键事实（2026-06-28 实测，UTC 周日 CME 休市，四个 instId 仍出新鲜 5m K 线）

| 真实标的 | OKX 代理 instId | 当时价 | 含义 |
|---|---|---|---|
| `NQ=F` 纳指期货 | `QQQ-USDT-SWAP` | 706.6 | 纳指100 ETF（与 NQ=F **同底层指数**） |
| `CL=F` 原油 | `CL-USDT-SWAP` | 72.5 | WTI |
| `GC=F` 黄金 | `XAU-USDT-SWAP` | 4088 | 现货黄金 |

排雷：`SPX-USDT-SWAP`(≈0.34) 是 SPX6900 meme 币；OKX 指数永续按 ETF 代码命名(SPY/QQQ)。被否决备选：币安(API 地域限制不可达)、xStocks/CoinGecko(需新源、薄盘)、OKX-ICE 代币化指数(H2 2026 未上)。

## 2. 目标与非目标

### 2.1 目标
- `NQ=F`/`CL=F`/`GC=F` 在真实源缺失（休市）时段用 OKX 永续补出连续点，视觉明确区分。
- **零新增数据源**：复用现有 `OkxPriceSource`。
- 真实数据始终优先：真实 bar 可覆盖同槽合成点（§7.3）。

### 2.2 非目标
- 标普(`ES=F`/`^GSPC`)、道指(`YM=F`)、纳斯达克综指(`^IXIC`)；不新增 CoinGecko/xStocks/币安源；不改新闻/告警/标注/预测；不动加密区；不换图表库；不回补历史休市段（前向实时）；补点为「指示性参考价」非交易用途。

## 3. 决策摘要（已拍板）
1. 混合：常规时段保持 yfinance 期货口径，仅休市段补链上。
2. 数据驱动判休市（不用交易日历）。
3. 区分标注（前端 §8，A/B 二选一待定）。
4. 范围仅 `NQ=F→QQQ`、`CL=F→CL`、`GC=F→XAU`，全走 OKX。

## 4. 架构与组件

### 4.1 OKX 取数契约（评审修正：exchange 来源、返回类型）
- `OkxPriceSource` 新增**实例方法**（内部 `_make_exchange()` 建**一次** exchange，循环多个 instId）：
  ```python
  class PerpBar(NamedTuple):
      bar_end: datetime   # UTC naive，5m bar 收盘时刻
      close: float
  def fetch_instrument_bars(self, inst_ids: list[str], limit: int = 12) -> dict[str, list[PerpBar]]:
      ...  # 每个 instId 返回按时间升序的已收盘 bar；只复用 _fetch_candles + _closed_candle_points
  ```
- **绝不调用 `_make_record`**（硬编码 `asset_class="crypto"`、`symbol="X/USDT"`、`source`，见 §6.1）。
- `GapFiller.run(okx_source, scan_time)` 接收 `PriceScanner.self.okx`（一个 `OkxPriceSource` 实例，**不是** ccxt 对象），调 `okx_source.fetch_instrument_bars([...三个 inst...])` 一次拿齐三品种、共用一个 exchange。
- `limit=12`（覆盖 ~60min，吸收 yfinance 落库延迟，保证 §5.3 的 bar_end 对齐能命中；见评审修正）。

### 4.2 每轮扫描数据流
```
PriceScanner.scan():
  yfinance.fetch() → 真实期货 bar（含 NQ=F/CL=F/GC=F）
  okx.fetch()/...  → 加密、债券（既有）
  _save_records(real_records)              # 既有 + §7.3 source-aware 覆盖
  GapFiller.run(self.okx, scan_time):      # 【新】
    bars = self.okx.fetch_instrument_bars([m.okx_inst for m in ONCHAIN_GAPFILL.values()])
    for symbol, m in ONCHAIN_GAPFILL.items():
        series = bars.get(m.okx_inst) or []
        fresh = [b for b in series if scan_time - b.bar_end <= PERP_FRESH_MIN]
        if not fresh: warn; continue                 # perp 空/自身陈旧（§6.3）
        latest = fresh[-1]
        real = latest_real_snapshot(symbol, scan_time)   # §7.1 显式查询，非 MAX
        if real is None: continue
        staleness = scan_time - real.timestamp
        if staleness <= STALENESS_MIN:               # 真实在流
            if real.timestamp > anchor.real_ts(symbol):    # 真实 bar 推进
                pa = pick_bar(series, bar_end == real.timestamp)  # 精确；命中失败见 §5.3 容差
                if pa: upsert_anchor(symbol, real.timestamp, real.price, pa.close)
            continue
        else:                                        # 休市 → 补点
            a = load_anchor(symbol)
            if a is None: continue                   # 冷启动无锚点
            R0 = a.real_close / a.perp_price
            synthetic = latest.close * R0
            if not step_ok(symbol, synthetic, latest.bar_end): warn; continue   # §7.4 步进防呆
            save_synthetic(symbol, price=synthetic, ts=latest.bar_end, source=GAPFILL_SOURCE,
                           asset_class/name=from real snapshot)
```

### 4.3 组件清单
| 组件 | 文件 | 改动 |
|---|---|---|
| OKX 取任意 instId 已收盘 bar | `scanners/sources/okx_source.py` | `fetch_instrument_bars`（§4.1），只复用 `_fetch_candles`+`_closed_candle_points`，不走 `_make_record` |
| 补点编排 | `scanners/gap_filler.py`（新） | 判休市/锚点/比率/步进防呆/写库 |
| 锚点持久化 | `models/gapfill_anchor.py`（新） | 小表 |
| **登记模型** | `models/__init__.py` | **必须**加 `from models.gapfill_anchor import GapfillAnchor`，否则 `create_all` 不建表（§11.1） |
| source-aware 覆盖 | `scanners/price_scanner.py` `_save_records` | **UPDATE 覆盖**同槽合成行（§7.3，非 add） |
| 编排接入 | `scanners/price_scanner.py` | `scan()` 末尾 `GapFiller.run(self.okx, scan_time)` |
| API 透出来源 | `schemas/market.py` + `services/market_service.py` | `MarketHistoryPoint.source`；`get_history` 查询补选 `source` |
| 前端类型/常量 | `frontend/src/api/types.ts` | `MarketHistoryPoint.source`；TS 侧镜像常量 `OKX_GAPFILL_SOURCE`（§11.2） |
| 前端对比图 | `MarketPage.tsx` + `Charts.tsx` | 见 §8（A/B 二选一） |
| 前端卡片 | `MarketPage.tsx` | `source` 以 `okx_gapfill` 开头 → "代理价"角标 |

## 5. 核心机制：比率锚定合成补点

### 5.1 单位错配
QQQ ETF≈706 vs NQ=F≈22000，底层同为纳指100，差固定倍率+期货基差。直接塞会归一化出垃圾值。金/油倍率≈1。

### 5.2 公式（含算例）
```
R0 = anchor.real_close / anchor.perp_price
synthetic(t) = perp_close(t) * R0          # 乘不是除
```
算例：anchor real_close=22000、perp=706 → R0≈31.16；某刻 perp=710 → synthetic≈22124。synthetic 与 NQ=F 同量级。

### 5.3 锚点捕获时序（评审修正核心点）
- **perp 每轮都取**（`GapFiller` 每轮对三品种各取一次；常规扫描的 `okx.fetch()` 只取 crypto，三 perp 从不被拉）。
- **仅真实 bar 推进时更新锚点**（`real_ts > anchor.real_ts`），用 **bar_end 对齐**的 perp：
  - 取 `fetch_instrument_bars(limit=12)` 序列中 `bar_end == real_ts` 的那根做 `perp_price`。
  - **命中失败（两源差 >1 根）**：在 ±1 根（5min）容差内取最近 perp bar；仍无则本轮不更新、保留旧锚点，并在锚点 `updated_at` 距今超过 N 分钟（如 30）时记 **warning**（使"长期不对齐"可观测，非静默退化）。
- **不在"陈旧但未超阈值"窗口更新锚点**（否则把周五 17:00 real_close 配 18:00 perp，R0 偏）。
- **忽略未来戳真实 bar**：staleness/锚点只采纳 `bar_end ≤ scan_time` 的已确认 bar（§7.1 查询保证）。

### 5.4 锚点存储
```python
class GapfillAnchor(Base):
    __tablename__ = "gapfill_anchor"
    symbol     = Column(String(30), primary_key=True)
    real_ts    = Column(DateTime, nullable=False)
    real_close = Column(Float, nullable=False)
    perp_price = Column(Float, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow)
```
持久化以扛周末重启。

### 5.5 基差漂移（诚实声明）
R0 整段（最长 ~49h）冻结，隐含"期货–ETF 基差近似恒定"假设。NQ=F 是 CME 指数**期货**（期货基差+roll），QQQ-perp 跟踪 QQQ ETF 现货（跟踪误差+USDT 资金费/脱锚）。基差会因**分红除息、利率预期、USDT 脱锚**漂移，故合成曲线**绝对价位**随休市时长累积偏离"应开位"，整段形状被冻结基差偏置。处置：前端标"指示性价位"；`change_24h` 等卡片增量同受影响（§7.4）；可选增强：休市越久 proxy 越淡（默认不做）。

## 6. OKX 源改造（零新源的边界）

### 6.1 不碰 `_make_record`
`_fetch_candles`(okx_source.py:61)、`_closed_candle_points`(72-95) 对 instId 无关，可直接复用。但 `_make_record`(232-245) 硬编码 crypto。故 `fetch_instrument_bars` 只返回 `PerpBar(bar_end, close)`，合成记录的 symbol/asset_class/name/price/source 全由 `GapFiller` 按 `ONCHAIN_GAPFILL` + 该 symbol 最近真实快照构造。

### 6.2 exchange 与代理前置
`fetch_instrument_bars` 内部 `_make_exchange()` 建**一次** exchange、循环三 instId（一轮一个，非每品种重建）。`OkxPriceSource.proxy` 在 `__init__` 固定为 `config.PROXY`（仅 `setup_runtime()` 后非空）；`GapFiller` 复用扫描进程内的 `okx` 实例即满足前置。**需确认部署服务器（腾讯云日本）对 OKX 可达**（§1.1 本地观测非部署保证）。

### 6.3 perp 自身陈旧检测
`_pick_last_closed` 无陈旧守卫，OKX 卡顿会返回旧 bar，叠加去重 → 合成静默平直却报成功。故 §4.2 用 `scan_time - bar_end ≤ PERP_FRESH_MIN`（默认 12）过滤；无新鲜 bar 则跳过+warn。

## 7. 判休市、查询、撞槽与防呆

### 7.1 真实快照查询（评审修正：非 MAX）
yfinance 未来戳 fallback 行（yfinance_source.py:91-99）**会落库**。故"最近真实快照"必须：
```sql
WHERE symbol=? AND source NOT LIKE 'okx_gapfill%' AND timestamp <= :scan_time
ORDER BY timestamp DESC LIMIT 1
```
（不能用 `MAX(timestamp)`，否则未来戳行被当 latest，staleness 变负、抑制补点。）

### 7.2 判休市与阈值
```
staleness = scan_time − 上式查到的真实快照.timestamp
≤ STALENESS_MIN → 不补；> 阈值 → 补
```
休市时 yfinance 无新 bar，"数据停更"即信号，**不依赖交易日历**（DST/假期/session 自动免疫）。阈值默认 60min：小阈值会被 yfinance 429/抖动误触发、真实/代理横跳；60min 足以覆盖周末（~49h），代价仅"周五收盘后头 60min 暂不补"，并自然跳过每日 ~1h 维护断点。重开市一根新鲜真实 bar 即令 staleness 回落、下一轮停补。

### 7.3 撞槽：真实覆盖合成（评审修正 blocker：必须 UPDATE 非 add）
盘中 yfinance 中断 → 补点写合成 `NQ=F` 行 → 恢复后 `run.py:281` 滚动回补取回真实 `NQ=F` 行落同槽。现 `_save_records` 撞已存即 `continue`、且唯一索引 `(timestamp,symbol)`(models/price.py:26) → 若按 `add()` 插真实行会 **IntegrityError → 宽 except 回滚整批**（price_scanner.py:205-210），全品种本轮回补全丢。
**决议（真实优先），对 `_save_records` 的具体改法：**
1. existing 投影**增选 `source`（与 `id`）**：现仅选 `(timestamp, price)`（price_scanner.py:159-167），需 `{ts: (price, source, id)}`。
2. 循环内分支：
   - 入库行**为真实**(source≠GAPFILL) 且 **同槽已存为 `okx_gapfill`** → **取该 ORM 行原地 mutate**（或 `session.query().filter_by(id=...).update({...})`）覆盖 price/prev_price/change_pct/source/name/asset_class/volume；**`last_price = r.price`**（推进到真实价，使下一根 prev_price/change_pct 链算基于真实，不基于被覆盖的合成值）。
   - 入库行**为合成**、同槽已存任意行 → 跳过（不覆盖）。
   - 真实↔真实、合成↔无 → 既有行为不变。
3. 这不是"仅加一条 continue 分支"，而是给该函数新增一条**此前不存在的 UPDATE 路径**（含投影扩列 + ORM 取行）。实现与测试按此范围。

### 7.4 合成点参与基线/卡片 + 步进防呆（评审修正：防呆基准改步进）
合成点写入 `price_snapshots`，**同一 symbol**、asset_class/name 取自该 symbol 最近真实快照，`source=GAPFILL_SOURCE`，`timestamp=perp.bar_end`。归一化 `normalize_prices` 零改动（已是期货量级）。须明确：合成点**会**被 `_window_baseline_prices` 选为基准、进入 `get_latest_prices`/`_change_pct_from_latest` 的 5m/1h/24h 卡片增量。
**步进防呆 `step_ok`**（评审修正：不可用"冻结的周五收盘"做基准——周末真出现 >阈值大行情(原油/黄金地缘，正是本项目跟踪对象)会把整段补点毙掉、恰在最该补时留洞）：
- 判**单根 5m 跳变**：`|synthetic / 上一根(合成或真实)同序列点 − 1| > STEP_PCT`（默认 0.05）→ 视为坏 perp tick/坏锚点，跳过+warn。**慢速累积的大漂移由许多正常小步构成，不被毙。**
- 另设**首点 seam 宽松守卫**：补点段第一根理论上 `synthetic≈anchor.real_close`；若首点相对最近真实收盘偏离 > SEAM_PCT（默认 0.15）→ 提示锚点可疑、跳过+warn。
- 边界 `change_pct` 含基差跳，视为可接受（§12 留待定）。

## 8. 前端：区分标注（A/B 二选一，待用户拍板）

> 两轮评审显示 A（双线）反复踩 recharts 坑（颜色按下标移位、connectNulls 全局、图例翻倍、tooltip）。本节按"对 `MultiLineChart` 做**默认行为不变的 opt-in 改造**"设计，**推荐 B**（改动小、零回归）。

### 8.B 方案B：休市时段阴影带（推荐）
- 单 symbol 单线不变（颜色/图例/connectNulls 全不动，零回归）；合成点照常并入序列，曲线连续。
- `MultiLineChart` 加可选 `shadedBands?: {x1: string; x2: string; label?: string}[]` → 渲染 `<ReferenceArea>`。
- **端点必须是类目轴 `time` 显示字符串**（评审修正）：XAxis 是纯类目轴（Charts.tsx:53 仅 `dataKey="time"`），类目值是 `buildHistoryChart` 产出的 BJT 串（MarketPage.tsx:141，如 `"06-15 05:30"`）。`ReferenceArea` 在类目轴上 x1/x2 必须**精确等于数据中存在的 `time` 串**，传 UTC/ISO 会静默不渲染。故 x1/x2 取"`source` 以 GAPFILL_SOURCE 开头的首/末 ChartPoint 行的 `time` 值"，非原始时间戳。
- 取舍：阴影带是**时间维**标注，不区分带内哪条序列是代理（BTC 周末是真实 24/7）→ 图例文案写"灰区＝传统品种(纳指/原油/黄金)休市代理价(OKX 永续)"。
- 测试（vitest）：断言 band 端点 == 序列中存在的 `time` 串。

### 8.A 方案A：逐序列虚线代理（精确，改动大，备选）
- `MarketHistoryPoint.source` 须先逐点透出；`buildHistoryChart` 每 symbol 拆 `key`(真实)/`key__proxy`(代理)，边界点两键各写一份。
- `MultiLineChart` 加**可选** `keyStyles`（未传完全保持现状）：
  - 颜色按 **symbol 稳定解析**（评审修正：直接用 `history.series.symbol` 构造，**不要**正则解析 `name (symbol)` 串——symbol 含 `=`/`/`，如 `NQ=F`/`BTC/USDT` 会解析脆裂）；proxy 键复用 base 同色。
  - **`connectNulls=false` 仅作用于三个 gapfill 真实键**（其余键默认 true 不变）。因所有源 bar_end 对齐同一 5m 网格，session 内每格该真实键非空，`connectNulls=false` 只在该品种休市处断线（正是 proxy 虚线接管处）；唯一残留是丢单根 bar 的 1 格小缺口，可接受。**不可对全部键 connectNulls=false**（会在其它品种贡献的网格分钟处把实线打断，是回归）。
  - proxy 键 `legendType="none"`（图例不翻倍）。
- Tooltip：**本版显式 descope**——hover 代理点显示与真实点相同的默认 tooltip，区分仅靠虚线+卡片角标+图例说明；自定义 `(代理价)` tooltip 列为可选增强。

### 8.C 卡片角标（两方案通用）
`MarketLatestItem.source`（已存在）以 GAPFILL_SOURCE 开头 → 卡片"代理价"角标 + tooltip 注明来源 perp（前端经 `ONCHAIN_GAPFILL` 按 symbol 反查）。

## 9. 错误处理与边界

| 情形 | 行为 |
|---|---|
| perp 取价失败 / 非正空 / **自身 bar 陈旧** | 跳过该 symbol，下轮重试；不写陈旧合成点 |
| 锚点缺失（冷启动/表新建后未历一个交易 session） | 暂不补，待首 session 建锚；记 warning |
| **冷启动前提** | 补点仅在"自 `gapfill_anchor` 表建立以来至少捕获过一次交易 session 锚点"后产出；迁移首次落休市段则该段无补点（同冷部署，可接受） |
| 锚点长期不对齐（bar_end 始终不命中） | ±1 根容差兜底；锚点 `updated_at` 过旧记 warning（§5.3） |
| 停机跨周末重启 | 锚点持久化续补；停机期间那段不追补 |
| 盘中中断后回补撞槽 | 真实行 **UPDATE 覆盖**同槽合成行（§7.3） |
| 未来戳/非单调真实 bar | 仅采纳 `bar_end ≤ scan_time`（§7.1） |
| 合成单步跳变 > STEP_PCT / 首点 seam > SEAM_PCT | 跳过+warn，留洞不污染基线（§7.4） |
| `GAPFILL_ENABLED=False` 或阈值 ≤ 0 | 整体禁用 |

## 10. 测试策略

后端 pytest（`pytest.ini` testpaths=tests）；前端 **vitest**（与 pytest 分开）。**内存库 fixture 必须先 `import models`（或 import `GapfillAnchor`）再 `create_all`**，否则新表 "no such table"。

### 10.1 后端单测（`tests/test_gap_filler.py` 新）
- 比率锚定数值：金/油 R0≈1、纳指 R0≈31；synthetic=perp×R0 方向。
- 判休市三态；真实快照查询取已确认 bar（构造未来戳行验证不被选为 latest、staleness 为正）。
- 锚点：仅真实推进时更新；bar_end 对齐取 perp；real_ts 落后 perp 2-4 根仍能命中（limit=12）；±1 容差；阈值窗口内不更新；锚点过旧告警。
- perp 陈旧/失败/非正；锚点缺失；单步 > STEP_PCT；首点 > SEAM_PCT → 各跳过路径。
- **大行情不被毙**：构造周末 +30% 的渐进小步序列 → 全部写入（验证步进防呆不误杀）。
- 重启后从持久化锚点续补。

### 10.2 集成（扩 `tests/test_price_history.py` / 新建）
- 注入可 mock 的 `okx_source.fetch_instrument_bars`；yfinance 陈旧 → 跑一轮 `GapFiller.run` → 断言新增 `source=GAPFILL_SOURCE`、值=perp×R0、symbol=NQ=F、asset_class=futures、ts 对齐。
- 反向：yfinance 新鲜 → 不产出合成点。
- **撞槽（blocker 回归）**：先有合成 NQ=F@T，再 `backfill_range` 落真实 NQ=F@T → 断言**真实 UPDATE 覆盖**合成（行被更新非新增、整批不回滚、下一根 prev_price 基于真实价）。

### 10.3 服务/前端
- `get_history` 透出 `source`（扩 `tests/test_market_history.py`，其 fixture 已 import models+create_all）。
- 前端（vitest）：选 B → 阴影带端点为序列中存在的 `time` 串；选 A → proxy 键虚线、三 gapfill 真实键 connectNulls=false 且 NQ=F 颜色不因 GC=F/CL=F 增 proxy 键而变。
- 既有前端测试不回归（MultiLineChart 默认路径不变）。

## 11. 建表与配置

### 11.1 建表路径
`database.create_tables()` → `import models` → `Base.metadata.create_all`，**无 Alembic**。`create_all` 只对已 import 注册的模型发 CREATE TABLE，故**必须**在 `models/__init__.py` 加 `from models.gapfill_anchor import GapfillAnchor`。新表无需改 `_ensure_sqlite_schema`（那只补旧表新列）。

### 11.2 配置与常量
```python
ONCHAIN_GAPFILL = {
    "NQ=F": {"okx_inst": "QQQ-USDT-SWAP"},
    "CL=F": {"okx_inst": "CL-USDT-SWAP"},
    "GC=F": {"okx_inst": "XAU-USDT-SWAP"},
}
GAPFILL_SOURCE = "okx_gapfill"   # 后端单一真源；所有 .startswith / source NOT LIKE 均引用本常量
GAPFILL_ENABLED            = os.getenv("GAPFILL_ENABLED", "1") in {"1","true","yes","on"}
GAPFILL_STALENESS_MINUTES  = int(os.getenv("GAPFILL_STALENESS_MINUTES", "60"))
GAPFILL_PERP_FRESH_MINUTES = int(os.getenv("GAPFILL_PERP_FRESH_MINUTES", "12"))
GAPFILL_STEP_PCT           = float(os.getenv("GAPFILL_STEP_PCT", "0.05"))   # 单根 5m 跳变上限
GAPFILL_SEAM_PCT           = float(os.getenv("GAPFILL_SEAM_PCT", "0.15"))   # 首点 seam 上限
```
- 评审修正：`GAPFILL_SOURCE` 无法跨 Py→TS 共享，前端在 `types.ts` 定义**镜像常量** `OKX_GAPFILL_SOURCE = "okx_gapfill"`，注释"须与后端 config.GAPFILL_SOURCE 保持一致"。`String(30)` 容得下（11 字符）。

## 12. 待定项
1. **前端方案 A（精确虚线）vs B（阴影带，推荐）** —— §8。
2. 边界点 `change_pct` 是否容忍含基差跳（§7.4）；默认容忍。
3. 代理段绝对价位是否随休市时长渐淡（§5.5）；默认不做。
