# 标注页面 · 窗口净值图（Annotation Window Net-Value Chart）

- 日期：2026-06-14
- 分支：`feat/annotation-market-chart`
- 状态：设计已确认，待写实现计划
- 范围：**纯前端改动**——不动后端、数据库、API、表结构

## 1. 目标

在「新闻标注」页（[AnnotationsPage.tsx](../../../frontend/src/pages/AnnotationsPage.tsx)）点选**正在标注的窗口**时，展示一张跨资产**净值图**，只覆盖该窗口对应的时间区间（含上下文 padding）。每条线在显示区间左缘归一为 `1.000`，被标注的标的加粗高亮；并在新闻发布时间点画出标注者勾选为**驱动 / 方向矛盾**的新闻竖线标记，随勾选实时刷新。

用途：标注时一边看净值走势、一边核对「驱动」新闻是否真的**领先**于价格变动，「方向矛盾」新闻是否确实与实际走势相悖。

## 2. 已确认的设计选择

| 维度 | 选择 |
|---|---|
| 触发来源 | 正在标注的窗口（左侧未标注窗口列表的 `activeWindow`） |
| 图表品种 | 跨资产篮子 + 强制纳入并高亮「本标的」（`activeWindow.symbol`） |
| 时间范围 | `[窗口起 − pre, 窗口止 + post]`，每条线左缘归一为 `1.000` |
| MarketPage 原图 | 保留不动 |
| 标记 | 驱动＝绿色实线 `#22c55e`；方向矛盾＝红色虚线 `#ef4444`；解释/噪音不画 |

## 3. 现状（来自代码勘察）

- 图表组件 `MultiLineChart`（[Charts.tsx](../../../frontend/src/components/Charts.tsx)）是纯展示的 recharts 组件，签名 `{ data: ChartPoint[]; keys: string[]; height?; unit? }`；`XAxis dataKey="time"` 为**类目轴**（字符串桶）。MarketPage 是它唯一的消费者，通过 `buildHistoryChart()` 把 `MarketHistoryResponse` 转成 `ChartPoint[]`，按 UTC 分钟（`timestamp_utc.slice(0,16)`）合并、用 `timestamp_bj.slice(5,16)` 显示、把 `normalized_pct` 填进序列字段。
- 后端 `GET /api/market/history` 已支持 `start_utc` / `end_utc` 任意窗口，返回每个 series 的 `points`，每点含 `price`（原始价）、`normalized_pct`、`timestamp_utc`、`timestamp_bj`。客户端方法：`api.marketHistory({ symbols?, hours?, start_utc?, end_utc? })`。
- 标注页关键状态（已核实）：
  - `newsRoles: Record<number, string>`（line 124，只存非 noise）；取值来自因果角色枚举 `driver` / `contradictory` / `post_hoc_explanation`。
  - `activeWindow`（line 227）含 `symbol`、`window_start.timestamp_utc`、`window_end.timestamp_utc`、`context_pre_minutes`。
  - `activePre = activeWindow.context_pre_minutes ?? 30`（line 232）。
  - `contextNews`（line 233-241）：`api.contextNews({ window_start_utc, window_end_utc, pre_minutes: activePre, post_minutes: 30 })` → `{ items: NewsItem[] }`，`NewsItem` 含 `id` / `timestamp_utc` / `timestamp_bj` / `title`。
  - 候选新闻表数据源 = `contextNews.data.items`（line 675）；角色按 `newsRoles[row.id] ?? "noise"`（line 528）。

**关键一致性**：图表 padding 复用与 `contextNews` 完全相同的 `pre=activePre / post=30`，使图表 x 轴区间 == 候选新闻时间跨度，保证所有被勾选的驱动/方向矛盾标记必落在图内。

## 4. 架构与组件

### 4.1 `MultiLineChart`（修改，向后兼容）

新增两个**可选**属性，MarketPage 不传 → 行为不变：

- `markers?: ChartMarker[]`，`ChartMarker = { time: string; role: "driver" | "contradictory"; title: string }`。在 `LineChart` 内为每个 marker 渲染一条 `<ReferenceLine x={marker.time} ... />`：驱动＝`stroke="#22c55e"` 实线；方向矛盾＝`stroke="#ef4444"` `strokeDasharray="6 4"`。`x` 取已对齐到图中类目桶的显示时间字符串（见 4.3 snapping），保证 recharts 类目轴能定位。
- `highlightKey?: string`：等于该 key 的 `<Line>` 用更粗的 `strokeWidth`（3.2 vs 2），保留其调色板颜色，最后渲染以叠在上层。

需新增 import：`ReferenceLine`（recharts）。

### 4.2 `windowNetValue.ts`（新增，纯函数，可单测）

抽出与 React 无关的纯逻辑，便于单测：

- `buildNetValueChart(history: MarketHistoryResponse, annotatedSymbol: string): { data: ChartPoint[]; keys: string[]; buckets: { time: string; utcMinute: string }[]; highlightKey: string | null }`
  - 仿 `buildHistoryChart` 按 UTC 分钟合并、`timestamp_bj.slice(5,16)` 显示；
  - **净值 = `price_t / firstVisiblePrice(series)`**（用原始 `price`，每条 series 取首个非空 `price` 作分母 → 左缘精确 `1.000`）；`firstVisiblePrice` 缺失或为 0 → 该 series 全 null；
  - `keys` = `${name} (${symbol})`；`highlightKey` = `symbol === annotatedSymbol` 的 series 标签；
  - 额外返回 `buckets`（每个 x 桶的 `{ time, utcMinute }` 升序）供 snapping 用。
- `deriveMarkers(candidateNews: NewsItem[], newsRoles: Record<number, string>, buckets): ChartMarker[]`
  - 仅保留 `newsRoles[item.id] === "driver" | "contradictory"`；
  - 将 `item.timestamp_utc` snap 到**最近的 bucket**（按 UTC 时间绝对差最小），`time` 取该 bucket 的显示串；无 bucket（空图）则丢弃；
  - 返回 `{ time, role, title }`，并按时间升序。

### 4.3 `WindowNetValueChart.tsx`（新增组件）

负责窗口相关的取数 / 转换 / 渲染。Props：

```ts
{
  activeWindow: PriceWindow;          // 非空（父层条件渲染保证）
  preMinutes: number;                 // = activePre
  postMinutes: number;                // = 30
  basketSymbols: string[];            // 跨资产篮子（见 4.4）
  candidateNews: NewsItem[];          // = contextNews.data?.items ?? []
  newsRoles: Record<number, string>;
}
```

行为：

1. 计算 padding 区间：`startUtc = new Date(window_start.timestamp_utc) − preMinutes*60_000`、`endUtc = new Date(window_end.timestamp_utc) + postMinutes*60_000`，`toISOString()`。
2. `fetchSymbols = unique([activeWindow.symbol, ...basketSymbols])`（本标的强制纳入）。
3. `useQuery(["annotation-netvalue", startUtc, endUtc, fetchSymbols.join(",")], () => api.marketHistory({ symbols: fetchSymbols, start_utc: startUtc, end_utc: endUtc }))`。
4. `buildNetValueChart(...)` → `data/keys/highlightKey/buckets`；`deriveMarkers(...)` → markers（随 `newsRoles` 变化实时重算，useMemo 依赖 `newsRoles` 与 `candidateNews`）。
5. 渲染 `<MultiLineChart data keys unit="" highlightKey markers />`（y 轴 3 位小数；`1.000` 基准线见 4.5）+ 下方**标记列表**（每行：角色徽标 · BJT 时间 `timestamp_bj.slice(11,16)` · 标题，单行省略；多条同桶则在列表中堆叠）。
6. 加载/错误/空：`LoadingState` / `ErrorState` / `MultiLineChart` 自带 `EmptyState`（"当前区间没有足够数据"）。

### 4.4 篮子品种多选

- 新增独立 localStorage key `annotation-chart-symbols`，默认 = MarketPage 的 `DEFAULT_CHART_SYMBOLS`。
- 复用 `MultiSelectControl`（[Controls.tsx](../../../frontend/src/components/Controls.tsx)），选项来自 `api.marketSymbols()`。
- 「本标的」始终并入取数与高亮，不可移除（即使不在篮子里）。

### 4.5 标注页接入（[AnnotationsPage.tsx](../../../frontend/src/pages/AnnotationsPage.tsx)）

- 在 `annotation-pair-grid` 上方新增一个 `annotation-block`，**仅当 `activeWindow` 存在时渲染**，块头含 4.4 的篮子多选。
- 传入：`activeWindow`、`preMinutes={activePre}`、`postMinutes={30}`、`basketSymbols`、`candidateNews={contextNews.data?.items ?? []}`、`newsRoles`。
- y 轴 `1.000` 基准虚线：通过给 `MultiLineChart` 复用现有 grid 实现或在 `WindowNetValueChart` 包一层；MVP 用一条 `ReferenceLine y={1}`（横向，作为 markers 之外的独立基准）——本项作为实现细节，不改变 props 契约。

### 4.6 样式（[styles.css](../../../frontend/src/styles.css)）

新增窗口净值块与标记列表的类（沿用现有 `annotation-block` / `annotation-pair-panel` 暗色 slate 风格；徽标用绿/红底配深色字）。

## 5. 数据流（实时）

```
点选窗口 → activeKey → activeWindow
  ├─ [startUtc, endUtc] = 窗口 ± (activePre / 30)
  │     → api.marketHistory({symbols: 本标的∪篮子, start_utc, end_utc})
  │     → buildNetValueChart (price 比值，左缘=1.000)
  │     → <MultiLineChart .../>
  └─ contextNews.items × newsRoles（仅 driver/contradictory）
        → deriveMarkers（snap 到最近 5 分钟桶）
        → markers → ReferenceLine 竖线 + 下方列表
```

除这一次 `marketHistory` 取数外，无新增网络请求；markers 完全来自页面已有的 `contextNews` 与 `newsRoles`，勾选角色即刷新。

## 6. 边界情形

- 无 `activeWindow` → 整块不渲染。
- 区间内价格点 < 2（如休市）→ `MultiLineChart` 显示自带 `EmptyState`；markers 因无 bucket 而不显示。
- 新闻落在 padding 区间外 → 不显示（设计如此；因 padding 与 contextNews 同口径，正常不会发生）。
- 本标的在区间内无数据 → 仍出现在 keys（图例），线为空。
- 隔夜跳空：因按「显示区间首个可见点」归一，不依赖后端基准，跳空自然体现在首点之后。

## 7. 测试

- 单测 `windowNetValue.test.ts`：
  - `buildNetValueChart`：两条 series → 每个 key 首行 = `1.000`；含 null 价格的缺口正确跳过；`highlightKey` 命中本标的；`firstVisiblePrice=0/缺失` → 该 series 全 null。
  - `deriveMarkers`：只保留 driver/contradictory；snap 到最近桶；空 buckets → 返回空；按时间升序。
- 组件测 `WindowNetValueChart.test.tsx`（vitest + @testing-library，仿现有 `DataTable.test` / `StateViews.test`）：mock `api.marketHistory` → 渲染正确的线数（keys）与 marker 数/样式；改变 `newsRoles` → markers 数量随之变化；mock 失败 → `ErrorState`。
- 回归：现有 `MultiLineChart` 用法（MarketPage，不传新 props）保持通过。

## 8. 改动文件清单

| 文件 | 改动 |
|---|---|
| `frontend/src/components/Charts.tsx` | 扩展 `MultiLineChart`：`markers` / `highlightKey`（可选） |
| `frontend/src/components/windowNetValue.ts` | 新增：`buildNetValueChart` / `deriveMarkers` 纯函数 |
| `frontend/src/components/WindowNetValueChart.tsx` | 新增：取数+转换+渲染组件 |
| `frontend/src/pages/AnnotationsPage.tsx` | 挂载组件 + 篮子状态/多选 |
| `frontend/src/styles.css` | 净值块 + 标记列表样式 |
| `frontend/src/components/windowNetValue.test.ts` | 新增单测 |
| `frontend/src/components/WindowNetValueChart.test.tsx` | 新增组件测 |

## 9. 不做（YAGNI）

- 已完成标注表驱动该图（本期只做「正在标注的窗口」）。
- 任何后端/接口改动。
- 解释（post_hoc_explanation）/ 噪音（noise）标记。
