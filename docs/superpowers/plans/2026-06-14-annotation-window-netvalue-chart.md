# 标注页面 · 窗口净值图 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在「新闻标注」页点选正在标注的窗口时，展示一张跨资产净值图（区间内每条线归一为 1.000、本标的高亮），并在新闻发布时间点画出标注者勾选为「驱动 / 方向矛盾」的新闻竖线，随勾选实时刷新。

**Architecture:** 纯前端。把净值/标记的纯逻辑抽进可单测的 `windowNetValue.ts`；给现有 recharts 组件 `MultiLineChart` 增加 4 个可选属性（向后兼容）；新增自包含组件 `WindowNetValueChart`（取数 + 篮子多选 + 渲染）；在 `AnnotationsPage` 条件挂载。后端 `GET /api/market/history` 已支持任意 `[start_utc,end_utc]` 窗口，无需改动。

**Tech Stack:** React 18 + TypeScript、@tanstack/react-query v5、recharts v2、vitest + @testing-library/react（jsdom）。

设计依据：[2026-06-14-annotation-window-netvalue-chart-design.md](../specs/2026-06-14-annotation-window-netvalue-chart-design.md)

---

## File Structure

| 文件 | 职责 | 动作 |
|---|---|---|
| `frontend/src/test/setup.ts` | 测试全局 setup | 修改：加 `ResizeObserver` polyfill（recharts 在 jsdom 渲染需要） |
| `frontend/src/components/windowNetValue.ts` | 纯函数：净值归一 + 标记推导（无 React 依赖，全可单测） | 新增 |
| `frontend/src/components/windowNetValue.test.ts` | 纯函数单测 | 新增 |
| `frontend/src/components/Charts.tsx` | `MultiLineChart` 展示组件 | 修改：加 `markers` / `highlightKey` / `baseline` / `valueFormatter` 可选属性 |
| `frontend/src/components/Charts.test.tsx` | `MultiLineChart` 冒烟/回归测 | 新增 |
| `frontend/src/components/WindowNetValueChart.tsx` | 窗口净值图组件（取数+篮子多选+渲染+标记列表） | 新增 |
| `frontend/src/components/WindowNetValueChart.test.tsx` | 组件集成测（mock api，断言标记列表/状态） | 新增 |
| `frontend/src/pages/AnnotationsPage.tsx` | 标注页 | 修改：import + 在窗口区上方条件渲染该组件 |
| `frontend/src/styles.css` | 样式 | 修改：净值块 + 标记列表样式 |

各命令在 `frontend/` 目录下运行。单测某文件：`npx vitest run <path>`；全量：`npm run test`；类型检查：`npm run typecheck`。

---

## Task 1: 测试基建——ResizeObserver polyfill

recharts 的 `ResponsiveContainer` 依赖 `ResizeObserver`，jsdom 不提供；后续组件测渲染图表会抛错。先补一个空实现。

**Files:**
- Modify: `frontend/src/test/setup.ts`

- [ ] **Step 1: 修改 setup.ts**

把 `frontend/src/test/setup.ts` 全文替换为：

```ts
import "@testing-library/jest-dom/vitest";

// recharts 的 ResponsiveContainer 依赖 ResizeObserver，jsdom 不提供。
// 测试不验证图表 SVG 尺寸（只验证非图表 DOM 与纯函数逻辑），空实现即可。
class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

if (!("ResizeObserver" in globalThis)) {
  (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;
}
```

- [ ] **Step 2: 跑现有测试确认未破坏**

Run: `npm run test`
Expected: 现有用例（AppShell / DataTable / StateViews）全部 PASS。

- [ ] **Step 3: Commit**

```bash
git add frontend/src/test/setup.ts
git commit -m "test(frontend): jsdom 补 ResizeObserver polyfill（recharts 渲染前置）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: 纯函数 windowNetValue.ts（净值归一 + 标记推导）

这是本特性的核心逻辑，严格 TDD。

**Files:**
- Create: `frontend/src/components/windowNetValue.ts`
- Test: `frontend/src/components/windowNetValue.test.ts`

- [ ] **Step 1: 写失败的测试**

创建 `frontend/src/components/windowNetValue.test.ts`：

```ts
import { expect, test } from "vitest";
import type { MarketHistoryResponse, NewsItem } from "../api/types";
import { buildNetValueChart, deriveMarkers } from "./windowNetValue";

function pt(symbol: string, name: string, price: number, utc: string, bj: string) {
  return { symbol, name, price, normalized_pct: 0, timestamp_utc: utc, timestamp_bj: bj };
}

const history: MarketHistoryResponse = {
  symbols: ["BTC/USDT", "GC=F"],
  start: { timestamp_utc: null, timestamp_bj: null },
  end: { timestamp_utc: null, timestamp_bj: null },
  series: [
    {
      symbol: "BTC/USDT",
      name: "BTC",
      asset_class: "crypto",
      points: [
        pt("BTC/USDT", "BTC", 100, "2026-06-14T21:30:00Z", "2026-06-15 05:30:00"),
        pt("BTC/USDT", "BTC", 90, "2026-06-14T21:35:00Z", "2026-06-15 05:35:00")
      ]
    },
    {
      symbol: "GC=F",
      name: "黄金",
      asset_class: "commodity",
      points: [
        pt("GC=F", "黄金", 2000, "2026-06-14T21:30:00Z", "2026-06-15 05:30:00"),
        pt("GC=F", "黄金", 2020, "2026-06-14T21:35:00Z", "2026-06-15 05:35:00")
      ]
    }
  ]
};

test("buildNetValueChart 归一到首点 1.000 并标出 highlightKey", () => {
  const out = buildNetValueChart(history, "BTC/USDT");
  expect(out.keys).toEqual(["BTC (BTC/USDT)", "黄金 (GC=F)"]);
  expect(out.highlightKey).toBe("BTC (BTC/USDT)");
  expect(out.data[0]["BTC (BTC/USDT)"]).toBe(1);
  expect(out.data[0]["黄金 (GC=F)"]).toBe(1);
  expect(out.data[1]["BTC (BTC/USDT)"]).toBeCloseTo(0.9);
  expect(out.data[1]["黄金 (GC=F)"]).toBeCloseTo(1.01);
  expect(out.data[0].time).toBe("06-15 05:30");
  expect(out.buckets.map((b) => b.utcMinute)).toEqual(["2026-06-14T21:30", "2026-06-14T21:35"]);
});

test("buildNetValueChart 处理空/undefined", () => {
  expect(buildNetValueChart(undefined, "BTC/USDT")).toEqual({ data: [], keys: [], buckets: [], highlightKey: null });
});

function news(id: number, title: string, utc: string): NewsItem {
  return { id, title, timestamp_utc: utc, timestamp_bj: null } as unknown as NewsItem;
}

const buckets = [
  { time: "06-15 05:30", utcMinute: "2026-06-14T21:30" },
  { time: "06-15 05:35", utcMinute: "2026-06-14T21:35" }
];

test("deriveMarkers 只保留 driver/contradictory，snap 到最近桶，按时间升序", () => {
  const candidate = [
    news(1, "驱动新闻", "2026-06-14T21:34:00Z"),
    news(2, "矛盾新闻", "2026-06-14T21:31:00Z"),
    news(3, "噪音新闻", "2026-06-14T21:33:00Z")
  ];
  const roles = { 1: "driver", 2: "contradictory", 3: "noise" };
  const out = deriveMarkers(candidate, roles, buckets);
  expect(out).toEqual([
    { time: "06-15 05:30", role: "contradictory", title: "矛盾新闻" },
    { time: "06-15 05:35", role: "driver", title: "驱动新闻" }
  ]);
});

test("deriveMarkers 桶为空时返回空", () => {
  expect(deriveMarkers([news(1, "x", "2026-06-14T21:34:00Z")], { 1: "driver" }, [])).toEqual([]);
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run src/components/windowNetValue.test.ts`
Expected: FAIL —「Failed to resolve import "./windowNetValue"」或函数未定义。

- [ ] **Step 3: 实现 windowNetValue.ts**

创建 `frontend/src/components/windowNetValue.ts`：

```ts
import type { MarketHistoryResponse, NewsItem } from "../api/types";
import type { ChartPoint } from "./Charts";

export type ChartMarker = { time: string; role: "driver" | "contradictory"; title: string };

export type NetValueChart = {
  data: ChartPoint[];
  keys: string[];
  buckets: { time: string; utcMinute: string }[];
  highlightKey: string | null;
};

// 把行情历史转成「净值 = price / 显示区间首个可见 price」，每条线左缘精确归一为 1.000。
// 合并键用 UTC 截到分钟（ISO 字典序即时间序），显示用 BJT（与 MarketPage buildHistoryChart 同口径）。
export function buildNetValueChart(
  history: MarketHistoryResponse | undefined,
  annotatedSymbol: string
): NetValueChart {
  if (!history) return { data: [], keys: [], buckets: [], highlightKey: null };
  const byUtcMinute = new Map<string, ChartPoint>();
  const keys: string[] = [];
  let highlightKey: string | null = null;

  history.series.forEach((series) => {
    const key = `${series.name} (${series.symbol})`;
    keys.push(key);
    if (series.symbol === annotatedSymbol) highlightKey = key;
    const base = series.points.find((p) => p.price)?.price ?? null;  // 首个非 0 价作分母
    series.points.forEach((point) => {
      if (!point.timestamp_utc) return;
      const utcMinute = point.timestamp_utc.slice(0, 16);
      const displayTime = point.timestamp_bj?.slice(5, 16) ?? utcMinute;
      const row = byUtcMinute.get(utcMinute) ?? { time: displayTime };
      row[key] = base ? point.price / base : null;
      byUtcMinute.set(utcMinute, row);
    });
  });

  const entries = Array.from(byUtcMinute.entries()).sort(([a], [b]) => a.localeCompare(b));
  const data = entries.map(([, row]) => row);
  const buckets = entries.map(([utcMinute, row]) => ({ time: row.time as string, utcMinute }));
  return { data, keys, buckets, highlightKey };
}

function toMs(utcMinute: string): number {
  const iso = utcMinute.replace(" ", "T");
  return new Date(iso.length <= 16 ? `${iso}:00Z` : iso).getTime();
}

// 从候选新闻 + 角色映射里取出 driver/contradictory，snap 到时间差最小的桶，按时间升序。
export function deriveMarkers(
  candidateNews: NewsItem[],
  newsRoles: Record<number, string>,
  buckets: { time: string; utcMinute: string }[]
): ChartMarker[] {
  if (!buckets.length) return [];
  const collected: { marker: ChartMarker; utc: string }[] = [];

  candidateNews.forEach((item) => {
    const role = newsRoles[item.id];
    if (role !== "driver" && role !== "contradictory") return;
    if (!item.timestamp_utc) return;
    const target = item.timestamp_utc.slice(0, 16);
    const targetMs = toMs(target);
    let best = buckets[0];
    let bestDiff = Math.abs(toMs(best.utcMinute) - targetMs);
    for (const bucket of buckets) {
      const diff = Math.abs(toMs(bucket.utcMinute) - targetMs);
      if (diff < bestDiff) {
        best = bucket;
        bestDiff = diff;
      }
    }
    collected.push({ marker: { time: best.time, role, title: item.title }, utc: target });
  });

  return collected.sort((a, b) => a.utc.localeCompare(b.utc)).map((c) => c.marker);
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `npx vitest run src/components/windowNetValue.test.ts`
Expected: 4 个用例全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/windowNetValue.ts frontend/src/components/windowNetValue.test.ts
git commit -m "feat(annotations): 窗口净值/标记纯函数（归一到1.000 + 角色标记snap）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: 扩展 MultiLineChart（可选 markers / highlightKey / baseline / valueFormatter）

向后兼容：MarketPage 不传新属性 → 行为不变。红/绿门用 `npm run typecheck`（属性契约）。

**Files:**
- Modify: `frontend/src/components/Charts.tsx`
- Test: `frontend/src/components/Charts.test.tsx`

- [ ] **Step 1: 写测试（用到新属性）**

创建 `frontend/src/components/Charts.test.tsx`：

```tsx
import { render } from "@testing-library/react";
import { expect, test } from "vitest";
import { MultiLineChart } from "./Charts";

test("无数据时显示 EmptyState", () => {
  const { getByText } = render(<MultiLineChart data={[]} keys={[]} />);
  expect(getByText("当前区间没有足够数据")).toBeInTheDocument();
});

test("带 markers/highlight/baseline/valueFormatter 渲染不崩溃", () => {
  const { container } = render(
    <MultiLineChart
      data={[
        { time: "06-15 05:30", a: 1 },
        { time: "06-15 05:35", a: 0.9 }
      ]}
      keys={["a"]}
      unit=""
      baseline={1}
      valueFormatter={(v) => v.toFixed(3)}
      markers={[{ time: "06-15 05:35", role: "driver" }]}
      highlightKey="a"
    />
  );
  expect(container.querySelector(".chart-shell")).not.toBeNull();
});
```

- [ ] **Step 2: 跑 typecheck 确认失败（红）**

Run: `npm run typecheck`
Expected: FAIL —「Property 'baseline'/'valueFormatter'/'markers'/'highlightKey' does not exist on type ...」。

- [ ] **Step 3: 实现属性扩展**

把 `frontend/src/components/Charts.tsx` 全文替换为：

```tsx
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis
} from "recharts";
import { EmptyState } from "./StateViews";

const palette = ["#5eead4", "#fbbf24", "#60a5fa", "#fb7185", "#a7f3d0", "#c084fc", "#f97316", "#38bdf8"];

export type ChartPoint = {
  time: string;
  [key: string]: string | number | null;
};

// 结构化标记输入（windowNetValue.ChartMarker 含 title，结构兼容此处可直接传入）。
export type ChartMarkerInput = { time: string; role: "driver" | "contradictory" };

export function MultiLineChart({
  data,
  keys,
  height = 340,
  unit = "%",
  markers = [],
  highlightKey,
  baseline,
  valueFormatter
}: {
  data: ChartPoint[];
  keys: string[];
  height?: number;
  unit?: string;
  markers?: ChartMarkerInput[];
  highlightKey?: string;
  baseline?: number;
  valueFormatter?: (value: number) => string;
}) {
  if (!data.length || !keys.length) {
    return <EmptyState title="当前区间没有足够数据" />;
  }
  return (
    <div className="chart-shell" style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ left: 0, right: 12, top: 8, bottom: 0 }}>
          <CartesianGrid stroke="rgba(148,163,184,0.14)" vertical={false} />
          <XAxis dataKey="time" tick={{ fill: "#94a3b8", fontSize: 11 }} minTickGap={28} />
          <YAxis tick={{ fill: "#94a3b8", fontSize: 11 }} unit={unit} width={48} tickFormatter={valueFormatter} />
          <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid #263142", color: "#e2e8f0" }} />
          <Legend wrapperStyle={{ color: "#cbd5e1", fontSize: 12 }} />
          {baseline != null ? (
            <ReferenceLine y={baseline} stroke="rgba(148,163,184,0.5)" strokeDasharray="4 4" />
          ) : null}
          {markers.map((marker, index) => (
            <ReferenceLine
              key={`marker-${index}`}
              x={marker.time}
              stroke={marker.role === "driver" ? "#22c55e" : "#ef4444"}
              strokeWidth={2}
              strokeDasharray={marker.role === "contradictory" ? "6 4" : undefined}
            />
          ))}
          {keys.map((key, index) => (
            <Line
              key={key}
              dataKey={key}
              type="monotone"
              dot={false}
              stroke={palette[index % palette.length]}
              strokeWidth={key === highlightKey ? 3.4 : 2}
              connectNulls
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
```

- [ ] **Step 4: 跑 typecheck 与测试确认通过（绿）**

Run: `npm run typecheck`
Expected: PASS。

Run: `npx vitest run src/components/Charts.test.tsx`
Expected: 2 个用例 PASS。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/Charts.tsx frontend/src/components/Charts.test.tsx
git commit -m "feat(charts): MultiLineChart 加可选 markers/highlightKey/baseline/valueFormatter（向后兼容）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: WindowNetValueChart 组件（取数 + 篮子多选 + 渲染 + 标记列表）

**Files:**
- Create: `frontend/src/components/WindowNetValueChart.tsx`
- Test: `frontend/src/components/WindowNetValueChart.test.tsx`

- [ ] **Step 1: 写失败的组件测试**

创建 `frontend/src/components/WindowNetValueChart.test.tsx`：

```tsx
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, expect, test, vi } from "vitest";

vi.mock("../api/client", () => ({
  ApiError: class ApiError extends Error {},
  api: {
    marketSymbols: vi.fn(),
    marketHistory: vi.fn()
  }
}));

import { api } from "../api/client";
import type { MarketHistoryResponse, NewsItem, PriceWindow } from "../api/types";
import { WindowNetValueChart } from "./WindowNetValueChart";

const mockedApi = api as unknown as {
  marketSymbols: ReturnType<typeof vi.fn>;
  marketHistory: ReturnType<typeof vi.fn>;
};

function pt(price: number, utc: string, bj: string) {
  return { symbol: "BTC/USDT", name: "BTC", price, normalized_pct: 0, timestamp_utc: utc, timestamp_bj: bj };
}

const history: MarketHistoryResponse = {
  symbols: ["BTC/USDT"],
  start: { timestamp_utc: null, timestamp_bj: null },
  end: { timestamp_utc: null, timestamp_bj: null },
  series: [
    {
      symbol: "BTC/USDT",
      name: "BTC",
      asset_class: "crypto",
      points: [
        pt(100, "2026-06-14T21:30:00Z", "2026-06-15 05:30:00"),
        pt(90, "2026-06-14T21:35:00Z", "2026-06-15 05:35:00")
      ]
    }
  ]
};

const activeWindow = {
  symbol: "BTC/USDT",
  window_start: { timestamp_utc: "2026-06-14T21:32:00Z", timestamp_bj: "2026-06-15 05:32:00" },
  window_end: { timestamp_utc: "2026-06-14T21:34:00Z", timestamp_bj: "2026-06-15 05:34:00" }
} as unknown as PriceWindow;

const candidateNews = [
  { id: 1, title: "驱动新闻标题", timestamp_utc: "2026-06-14T21:34:00Z", timestamp_bj: "2026-06-15 05:34:00" },
  { id: 2, title: "噪音新闻标题", timestamp_utc: "2026-06-14T21:33:00Z", timestamp_bj: "2026-06-15 05:33:00" }
] as unknown as NewsItem[];

function renderChart(newsRoles: Record<number, string>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <WindowNetValueChart
        activeWindow={activeWindow}
        preMinutes={30}
        postMinutes={30}
        candidateNews={candidateNews}
        newsRoles={newsRoles}
      />
    </QueryClientProvider>
  );
}

beforeEach(() => {
  mockedApi.marketSymbols.mockResolvedValue([]);
  mockedApi.marketHistory.mockResolvedValue(history);
});

test("勾选为 driver 的新闻出现在标记列表，noise 不出现", async () => {
  renderChart({ 1: "driver", 2: "noise" });
  expect(await screen.findByText("驱动新闻标题")).toBeInTheDocument();
  expect(screen.queryByText("噪音新闻标题")).not.toBeInTheDocument();
});

test("未勾选驱动/方向矛盾时显示空提示", async () => {
  renderChart({});
  expect(await screen.findByText(/尚未选出驱动\/方向矛盾新闻/)).toBeInTheDocument();
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run src/components/WindowNetValueChart.test.tsx`
Expected: FAIL —「Failed to resolve import "./WindowNetValueChart"」。

- [ ] **Step 3: 实现组件**

创建 `frontend/src/components/WindowNetValueChart.tsx`：

```tsx
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { NewsItem, PriceWindow } from "../api/types";
import { MultiLineChart } from "./Charts";
import { MultiSelectControl, type MultiOption } from "./Controls";
import { ErrorState, LoadingState } from "./StateViews";
import { buildNetValueChart, deriveMarkers } from "./windowNetValue";

// 与 MarketPage 默认篮子一致；此处独立持久化（key 不同），互不影响。
const DEFAULT_BASKET = ["YM=F", "NQ=F", "000001.SS", "^N225", "^KS11", "GC=F", "CL=F", "BTC/USDT"];
const BASKET_STORAGE_KEY = "annotation-chart-symbols";

function loadBasket(): string[] {
  if (typeof window === "undefined") return DEFAULT_BASKET;
  try {
    const raw = window.localStorage.getItem(BASKET_STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed) && parsed.every((x) => typeof x === "string")) return parsed;
    }
  } catch {
    // ignore parse errors
  }
  return DEFAULT_BASKET;
}

function persistBasket(symbols: string[]) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(BASKET_STORAGE_KEY, JSON.stringify(symbols));
  } catch {
    // ignore quota / privacy-mode errors
  }
}

function shiftIso(iso: string, deltaMinutes: number): string {
  return new Date(new Date(iso).getTime() + deltaMinutes * 60_000).toISOString();
}

export function WindowNetValueChart({
  activeWindow,
  preMinutes,
  postMinutes,
  candidateNews,
  newsRoles
}: {
  activeWindow: PriceWindow;
  preMinutes: number;
  postMinutes: number;
  candidateNews: NewsItem[];
  newsRoles: Record<number, string>;
}) {
  const [basket, setBasketState] = useState<string[]>(loadBasket);
  const setBasket = (next: string[]) => {
    setBasketState(next);
    persistBasket(next);
  };

  const startRaw = activeWindow.window_start.timestamp_utc;
  const endRaw = activeWindow.window_end.timestamp_utc;
  const startUtc = startRaw ? shiftIso(startRaw, -preMinutes) : null;
  const endUtc = endRaw ? shiftIso(endRaw, postMinutes) : null;

  // 本标的强制纳入并去重（即使不在篮子里）。
  const fetchSymbols = useMemo(
    () => Array.from(new Set([activeWindow.symbol, ...basket])),
    [activeWindow.symbol, basket]
  );

  const symbolsList = useQuery({ queryKey: ["market-symbols"], queryFn: () => api.marketSymbols() });

  const history = useQuery({
    queryKey: ["annotation-netvalue", startUtc, endUtc, fetchSymbols.join(",")],
    queryFn: () => api.marketHistory({ symbols: fetchSymbols, start_utc: startUtc!, end_utc: endUtc! }),
    enabled: Boolean(startUtc && endUtc)
  });

  const { data, keys, buckets, highlightKey } = useMemo(
    () => buildNetValueChart(history.data, activeWindow.symbol),
    [history.data, activeWindow.symbol]
  );

  const markers = useMemo(
    () => deriveMarkers(candidateNews, newsRoles, buckets),
    [candidateNews, newsRoles, buckets]
  );

  const symbolOptions: MultiOption[] = useMemo(() => {
    const items = symbolsList.data ?? [];
    return items.map((s) => ({ label: `${s.name} (${s.symbol})`, value: s.symbol, group: s.asset_class }));
  }, [symbolsList.data]);

  return (
    <section className="panel annotation-block window-netvalue-block">
      <div className="panel-head">
        <h2>窗口净值走势</h2>
        <div className="window-netvalue-head-controls">
          <span className="muted-text small">区间内净值归一为 1.000 · 竖线为你选出的驱动/方向矛盾新闻</span>
          <MultiSelectControl label="对照品种" values={basket} onChange={setBasket} options={symbolOptions} />
        </div>
      </div>

      {history.isLoading ? (
        <LoadingState />
      ) : history.error ? (
        <ErrorState error={history.error} />
      ) : (
        <>
          <MultiLineChart
            data={data}
            keys={keys}
            unit=""
            baseline={1}
            valueFormatter={(v) => v.toFixed(3)}
            markers={markers}
            highlightKey={highlightKey ?? undefined}
          />
          {markers.length ? (
            <ul className="netvalue-marker-list">
              {markers.map((marker, index) => (
                <li key={`${marker.time}-${index}`} className={`netvalue-marker netvalue-marker-${marker.role}`}>
                  <span className="netvalue-marker-role">{marker.role === "driver" ? "驱动" : "方向矛盾"}</span>
                  <span className="netvalue-marker-time">{marker.time}</span>
                  <span className="netvalue-marker-title">{marker.title}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted-text small netvalue-marker-empty">
              尚未选出驱动/方向矛盾新闻（在右侧候选新闻里勾选角色后会在此标注）
            </p>
          )}
        </>
      )}
    </section>
  );
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `npx vitest run src/components/WindowNetValueChart.test.tsx`
Expected: 2 个用例 PASS。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/WindowNetValueChart.tsx frontend/src/components/WindowNetValueChart.test.tsx
git commit -m "feat(annotations): WindowNetValueChart 组件（取数+篮子多选+净值图+标记列表）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: 接入 AnnotationsPage

**Files:**
- Modify: `frontend/src/pages/AnnotationsPage.tsx`（import + 第 617 行 `<>` 内条件渲染）

- [ ] **Step 1: 加 import**

在 `frontend/src/pages/AnnotationsPage.tsx` 第 13 行（`import { EmptyState, ErrorState, LoadingState } from "../components/StateViews";`）下方新增一行：

```tsx
import { WindowNetValueChart } from "../components/WindowNetValueChart";
```

- [ ] **Step 2: 在未标注窗口区上方条件渲染**

找到（约第 617-618 行）：

```tsx
          <>
            <div className="annotation-pair-grid">
```

替换为：

```tsx
          <>
            {activeWindow ? (
              <WindowNetValueChart
                activeWindow={activeWindow}
                preMinutes={activePre}
                postMinutes={30}
                candidateNews={contextNews.data?.items ?? []}
                newsRoles={newsRoles}
              />
            ) : null}
            <div className="annotation-pair-grid">
```

- [ ] **Step 3: typecheck + 全量测试**

Run: `npm run typecheck`
Expected: PASS。

Run: `npm run test`
Expected: 全部用例 PASS（含新增 windowNetValue / Charts / WindowNetValueChart）。

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/AnnotationsPage.tsx
git commit -m "feat(annotations): 标注页挂载窗口净值图（点选窗口→区间净值+驱动/方向矛盾标记）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: 样式

**Files:**
- Modify: `frontend/src/styles.css`（文件末尾追加）

- [ ] **Step 1: 追加样式**

在 `frontend/src/styles.css` 末尾追加：

```css
/* 标注页 · 窗口净值图 */
.window-netvalue-block .panel-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}
.window-netvalue-head-controls {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  justify-content: flex-end;
}
.netvalue-marker-list {
  list-style: none;
  margin: 10px 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.netvalue-marker {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 10px;
  border-radius: 8px;
  background: rgba(148, 163, 184, 0.08);
  font-size: 13px;
}
.netvalue-marker-role {
  flex: none;
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 6px;
}
.netvalue-marker-driver .netvalue-marker-role {
  color: #22c55e;
  background: rgba(34, 197, 94, 0.15);
}
.netvalue-marker-contradictory .netvalue-marker-role {
  color: #ef4444;
  background: rgba(239, 68, 68, 0.15);
}
.netvalue-marker-time {
  flex: none;
  color: #94a3b8;
  font-variant-numeric: tabular-nums;
}
.netvalue-marker-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.netvalue-marker-empty {
  margin-top: 10px;
}
```

- [ ] **Step 2: 构建确认无报错**

Run: `npm run build`
Expected: `tsc -b && vite build` 成功，无类型/构建错误。

- [ ] **Step 3: Commit**

```bash
git add frontend/src/styles.css
git commit -m "style(annotations): 窗口净值块与驱动/方向矛盾标记列表样式

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: 最终验证（自动 + 手动冒烟）

- [ ] **Step 1: 全量自动验证**

Run（在 `frontend/`）：
```bash
npm run typecheck && npm run test && npm run build
```
Expected: 三者全 PASS / 成功构建。

- [ ] **Step 2: 手动冒烟**

> 注意：本地 DB 自 2026-05-17 起较旧、实时数据只在 mmon.top 服务器（见记忆 local-env）。本地手动测试请选「本地有价格数据的回溯期内」的窗口；若本地无数据，净值图会显示 EmptyState「当前区间没有足够数据」属预期，可改在服务器环境验证。

启动：`npm run dev`（前端）+ 后端按项目常规方式起；打开「新闻标注」页：
- [ ] 选中左侧一个未标注窗口 → 上方出现「窗口净值走势」块；
- [ ] 每条线左缘为 1.000，本标的线明显加粗；y 轴显示 3 位小数，有 1.000 基准虚线；
- [ ] 在右侧候选新闻把某条角色改为「驱动」→ 图上出现绿色实线 + 下方列表多一行「驱动」；改为「方向矛盾」→ 红色虚线；改回「噪音/解释」→ 对应标记消失；
- [ ] 切到另一个窗口 → 图与标记随之刷新；
- [ ] 打开「市场」页确认原跨资产走势图行为不变。

- [ ] **Step 3: 若手动发现问题**，按 superpowers:systematic-debugging 排查后回到对应 Task 修复并补测试；否则本特性完成。

---

## Self-Review

**Spec 覆盖核对：**
- §2 触发来源（正在标注窗口）→ Task 5 条件渲染（`activeWindow ?`）。✓
- §2 品种（篮子+高亮本标的）→ Task 4 `fetchSymbols` 强制并入 + `highlightKey`；Task 3 加粗。✓
- §2 时间范围（窗口±padding，净值从 1.000）→ Task 4 `shiftIso(±pre/post)` + Task 2 `buildNetValueChart` price 比值；与 contextNews 同口径（`preMinutes=activePre / postMinutes=30`，Task 5 传参）。✓
- §2 原图保留 → 未改 MarketPage；MultiLineChart 新属性可选。✓
- §4.1 markers/highlightKey + baseline + valueFormatter → Task 3。✓
- §4.2 纯函数 → Task 2。✓
- §4.3 组件取数/转换/标记 → Task 4。✓
- §4.4 篮子多选独立持久化 + 本标的不可移除 → Task 4（`BASKET_STORAGE_KEY` + `fetchSymbols` 去重并入）。✓
- §4.5 接入位置（pair-grid 上方，仅 activeWindow 时）→ Task 5。✓
- §4.6 样式 → Task 6。✓
- §6 边界（无窗口不渲染 / 无数据 EmptyState / 标记空提示）→ Task 5 条件 + Task 3 EmptyState + Task 4 空提示。✓
- §7 测试 → Task 2/3/4。✓

**占位符扫描：** 无 TBD/TODO；每个改代码步骤均含完整代码与确切命令/预期。

**类型一致性：** `buildNetValueChart` / `deriveMarkers` 签名与返回（`{data,keys,buckets,highlightKey}` / `ChartMarker[]`）在 Task 2 定义、Task 4 使用一致；`ChartMarkerInput`（Task 3）与 `ChartMarker`（Task 2，多一个 `title`）结构兼容，markers 直传成立；`valueFormatter: (value:number)=>string` 在 Task 3 定义、Task 4 传 `(v)=>v.toFixed(3)` 一致；`highlightKey?: string` 接 `highlightKey ?? undefined`（Task 2 返回 `string|null`）一致。

**说明（非严格红/绿处）：** Task 3 是 recharts 展示层薄封装，其属性契约的红/绿由 `npm run typecheck` 把关，运行期仅做「EmptyState + 不崩溃」冒烟；真正的分支逻辑全在 Task 2 纯函数（严格 TDD）与 Task 4 集成测里覆盖。图表 SVG 细节（线宽/虚线/竖线位置）由 Task 7 手动冒烟确认。
