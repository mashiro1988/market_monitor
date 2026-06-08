# 跨资产净值曲线 + 标注页纳指对标 设计

> 两个独立改动合并为一份 spec：
> **A.** 「市场概览/跨资产走势」图从「各品种以窗口内自己第一个点为基准」改为「**窗口起点锚定的净值曲线**」，让隔夜跳空 / 熔断（如韩国 KOSPI 今早开盘熔断）能如实体现。
> **B.** 「新闻标注」页对每个价格异动窗口**加显同期纳斯达克（NQ=F）涨跌幅**作参照；该时段休市（周末等）则显示「无」。
> 创建：2026-06-08。

## 1. 目标

- **A**：跨资产走势对比要在**统一时间锚点**上比较各资产的累计收益率。当前每个品种各自归一到「本窗口内第一个数据点」，对有交易时段缺口的资产（KOSPI/日经/A股/美股指）会把**昨收→今开的隔夜跳空吃掉**——KOSPI 今早熔断 −8% 的跳空在图上看不到。
- **B**：标注 BTC（或 NQ）价格异动时，想要一个宏观参照——同一时间段纳指动没动，用来快速区分"宏观驱动"还是"个体异动"。休市时段明确写「无」。

## 2. 背景与现状

### 2.1 跨资产走势（改动 A）
- 后端 `services/market_service.py: get_history()`（L118–184）：按 `timestamp ∈ [start, end]` 查快照，按 symbol 分组，每组 `prices=[row.price]` → `normalize_prices(prices)`。
- `chart_utils.py: normalize_prices(prices)`（L7–14）：`base = prices[0]`；返回 `[(p/base − 1)×100]`。**已是价格比值（净值口径），并非"绝对收益率相加"**。
- 前端 `frontend/src/pages/MarketPage.tsx`：`buildHistoryChart()`（L126–148）按 UTC 分钟合并多 series 叠加；「跨资产走势」面板（L283–310）渲染 `normalized_pct`。默认走势区间 `hours="4"`（L152）。
- **病根**：基准 = 窗口内该品种第一个点。KOSPI 的熔断是隔夜跳空，今天第一根 in-window bar 已是跌完的开盘价 → 被设为 0% 基准 → 跳空不可见。BTC 24h 无缺口，于是两者失去公平对比基准。

### 2.2 标注窗口（改动 B）
- `services/annotation_service.py: load_price_windows()`（L183–297）产出 `list[PriceWindowSchema]`（消费方 `GET /api/annotations/windows`）；`list_annotations()`（L441–466）产出 `list[AnnotationListItem]`（消费方 `GET /api/annotations/list`，前端「已标注」表）。
- 每个窗口/标注都有 `window_start`/`window_end`（UTC naive）。
- 纳指数据：`NQ=F`（纳指期货，`config.PRICE_SOURCES` 内「纳指期货」）已按 5m 存在 `price_snapshots`。**选期货而非现货指数 `^IXIC` 的理由**：NQ=F 走 CME 时段（周一 06:00–周六 05:00 BJT，近全天），与 BTC 各时段窗口重叠远多于只有 21:30–04:00 的现货指数，作"同窗口参照"才有意义；现货指数会大量落「无」。
- 已有可复用件：`_nearest_snapshot(rows, target, before_time, tolerance)`（L172，但带 `before_time` 约束）；端点容差惯例 `max(config.SCAN_INTERVALS["price"]*2, 1)=10min`（L205）。

## 3. 设计 A：窗口起点锚定的净值曲线

### 3.1 `chart_utils.normalize_prices` 加可选基准（向后兼容）
```python
def normalize_prices(prices: list[float], base: float | None = None) -> list[float]:
    if not prices:
        return []
    if base is None or base == 0:
        base = prices[0]          # 不传 base → 完全保持旧行为
    if base == 0:
        return [0.0] * len(prices)
    return [(p / base - 1) * 100 for p in prices]
```
- `base=None` 时与现状逐字节等价（唯一现有调用方 `get_history` 之外的任何调用方都不受影响）。

### 3.2 `get_history` 取每品种"窗口起点基准价"
- 新增配置 `config.MARKET_HISTORY_BASELINE_LOOKBACK_DAYS = int(os.getenv("MARKET_HISTORY_BASELINE_LOOKBACK_DAYS", "7"))`（覆盖周末+假期，足够回看到上一笔收盘）。
- 新增 helper `_window_baseline_prices(session, symbols, start, lookback_days) -> dict[str, float]`：查 `timestamp ∈ [start − lookback_days, start]`（**含 start**）的快照，按 symbol 取**最大 timestamp** 那笔的 `price`。
- `get_history` 改动：拿到 in-window `rows`、分组后，对**实际出现的 symbol 集合**调 `_window_baseline_prices`；每组归一改为
  ```python
  base = baselines.get(symbol)            # 无前置数据 → None
  normalized = normalize_prices(prices, base=base)   # None 时回退到组内首点（旧行为）
  ```
- **基准取 `timestamp ≤ start` 的最后一笔**：若恰好有 `=start` 的快照，它既是基准又是首个 in-window 点，归一 = 0%（正确）；通常无 `=start` 点时基准是 start 之前最后一笔收盘，首个 in-window 点相对它归一，**隔夜跳空被保留**。

### 3.3 效果与前端
- KOSPI：基准 = 昨日 14:30 收盘；今天首个 in-window bar（08:00，已跌 8%）归一 = **−8%**，曲线从 08:00 直接落在 −8%。**只要所选区间覆盖开盘时刻就显示**（默认 4h 看不到早盘，需调大区间——这是窗口选择问题，非 bug）。
- **前端无需改**：仍只渲染 `normalized_pct`；缺口期 KOSPI 无点、复盘后从正确净值处起线。

### 3.4 边界（A）
- 品种在 `start` 前完全无数据 → `baselines` 无该键 → 回退组内首点（现有行为），不报错。
- 基准价为 0/None → `normalize_prices` 回退首点。
- `symbols=None`（全品种）调用：基准查询作用于 in-window 出现的 symbol 集合，不会全表扫历史。

## 4. 设计 B：标注窗口同期纳指对标

### 4.1 Schema（`schemas/annotations.py`，均为仅响应模型，加默认值字段安全）
- `PriceWindowSchema` 加 `nasdaq_pct: float | None = None`。
- `AnnotationListItem` 加 `nasdaq_pct: float | None = None`。

### 4.2 计算（`services/annotation_service.py`）
- 常量 `NASDAQ_REFERENCE_SYMBOL = "NQ=F"`。
- 新 helper `_nearest_snapshot_any(rows, target, tolerance_minutes) -> PriceSnapshot | None`：在 `rows` 里取与 `target` 时间差绝对值最小且 ≤ 容差者（**不带** `before_time` 约束，区别于现有 `_nearest_snapshot`）。
- 新 helper `_reference_change_for_window(nq_rows, window_start, window_end, tolerance_minutes) -> float | None`：
  `s=_nearest_snapshot_any(nq_rows, window_start)`、`e=_nearest_snapshot_any(nq_rows, window_end)`；两者皆有且 `s.price` 非 0 → `(e.price − s.price)/abs(s.price)×100`，否则 `None`。
- `load_price_windows`：构造每个事件窗口时——
  - `symbol == NASDAQ_REFERENCE_SYMBOL` → `nasdaq_pct=None`（前端显「本身」）。
  - 否则先一次性查 NQ=F 在本回溯期的快照（复用现有 `cutoff`），逐窗口 `_reference_change_for_window` 求值。
- `list_annotations`：同理一次性查 NQ=F 快照，逐 `NewsPriceAnnotation` 行（已有 `window_start/end`）求 `nasdaq_pct`；`symbol==NQ=F` → None。

### 4.3 前端（`frontend/`）
- `src/api/types.ts`：`PriceWindow`、`AnnotationListItem` 镜像 `nasdaq_pct?: number | null`（实现时确认这两个接口在 types.ts 的实际导出名）。
- `src/pages/AnnotationsPage.tsx`：
  - 统一格式化 `fmtNasdaq(symbol, pct)`：`symbol==="NQ=F"` → 「纳指 本身」；`pct==null` → 「纳指 无」；否则 `纳指 {+}{pct.toFixed(2)}%`。
  - 「待标注事件」行（L556–561）：在 `峰 +X%` 后加一段 `· {fmtNasdaq(primary.symbol, primary.nasdaq_pct)}`。
  - 「已标注」表（L627–673）：加一列「纳指对标」，`cell: row => fmtNasdaq(row.symbol, row.nasdaq_pct)`。
- `npm run typecheck` + `npm run build` 通过。

### 4.4 边界（B）
- 周末/休市（端点附近无 NQ 快照）→ `None` → 「无」。**（用户明确要求）**
- 标注品种本身是 NQ=F → `None` → 「本身」（前端按 symbol 区分 None 的两种语义）。
- 半窗口休市（仅一端有 NQ）→ `None` → 「无」。
- 不持久化、不喂 DeepSeek：纳指涨跌随时可从 `price_snapshots` 重算（YAGNI）。

## 5. 测试（TDD）

> 复用「事件合并」spec 建立的真实内存 SQLite session fixture 先例（`tests/test_annotation_windows.py`）：`create_engine("sqlite:///:memory:")` → `Base.metadata.create_all` → `Session` → 造 `PriceSnapshot`。时间戳一律**相对 `utc_now_naive()` 倒推**，否则落在 `display_cutoff` 外扫不出。

### 5.1 后端 A
- `normalize_prices` 的 `base=` 用例**扩展现有 `tests/test_price_history.py`**（该文件已覆盖 normalize_prices 无 base 情形，勿另起文件重复）：`base=100,[100,110,92]→[0,10,-8]`；`base=None,[100,110]→[0,10]`（与旧行为等价）；显式 `base=0` → 回退首点。
- `get_history` 窗口起点基准测试 → **新建 `tests/test_market_history.py`**（DB 集成测试，复用内存 SQLite fixture；`test_price_history.py` 是纯 chart_utils 单测，不混入 DB）：造某品种 `start−1h` 收盘=100，缺口后 in-window 首点=92、次点=93 → 断言首个 `normalized_pct≈−8`（相对 100，**非 0**）；另造一无前置数据品种 → 回退首点（首点=0）。

### 5.2 后端 B — 扩展 `tests/test_annotation_windows.py`
- 造 BTC/USDT 异动窗口（沿用现有造数法）+ 覆盖同期的 NQ=F 快照 → 断言 `window.nasdaq_pct≈` 预期值。
- 无 NQ=F 快照（或端点附近无）→ `nasdaq_pct is None`。
- `symbol=="NQ=F"` → `nasdaq_pct is None`（本身）。
- `list_annotations`：插一条标注 + NQ=F 快照 → 断言 `AnnotationListItem.nasdaq_pct` 求值；无快照 → None。

### 5.3 前端
- `npm run typecheck` + `npm run build`。

## 6. 不在本次范围（YAGNI）
- 纳指对标的持久化 / 喂入自动标注 prompt（可重算，留作后续增强）。
- A 改动扩展到市场概览卡片（其 `_change_pct_from_latest` 已是两点净值口径，无需改）。
- 「昨收锚定（当日涨跌）」式基准（需各市场收盘时段逻辑 + 24h 加密的昨收定义，复杂度高；已选窗口起点锚定）。

## 7. 涉及文件
- `config.py` — 新增 `MARKET_HISTORY_BASELINE_LOOKBACK_DAYS`
- `chart_utils.py` — `normalize_prices` 加可选 `base`
- `services/market_service.py` — `get_history` + 新 `_window_baseline_prices`
- `services/annotation_service.py` — `NASDAQ_REFERENCE_SYMBOL` + `_nearest_snapshot_any` + `_reference_change_for_window` + `load_price_windows` / `list_annotations` 接线
- `schemas/annotations.py` — `PriceWindowSchema`、`AnnotationListItem` 各加 `nasdaq_pct`
- `frontend/src/api/types.ts` — 镜像两处 `nasdaq_pct`
- `frontend/src/pages/AnnotationsPage.tsx` — `fmtNasdaq` + 待标注行 + 已标注列
- `tests/test_price_history.py`（扩展 `normalize_prices` base= 用例）、`tests/test_market_history.py`（新建，`get_history` DB 测试）、`tests/test_annotation_windows.py`（扩展纳指对标）
- **地图同步（同次 commit）**：`DATAFLOW.md`（windows/list 端点加 `nasdaq_pct`、走势图归一语义改为窗口起点锚定）、`ARCHITECTURE.md`（修 L77 调用链 Bloomberg→CNBC、推进"最近确认"日期、补事件合并/陈旧守卫漂移）、`DECISIONS.md`（追加本次两条 ADR）、`PENDING.md`（推进确认日期）。
</content>
</invoke>
