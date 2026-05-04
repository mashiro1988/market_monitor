import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, Download, Maximize2, Minimize2, Play } from "lucide-react";
import { api } from "../api/client";
import type { MarketHistoryResponse, MarketLatestItem, MarketTableRow } from "../api/types";
import { MultiLineChart, type ChartPoint } from "../components/Charts";
import { Button, MultiSelectControl, PageHeader, SelectControl, Stat } from "../components/Controls";
import type { MultiOption } from "../components/Controls";
import { DataTable } from "../components/DataTable";
import { EmptyState, ErrorState, LoadingState } from "../components/StateViews";

const classNames: Record<string, string> = {
  stock_index: "美股指数",
  futures: "美股期货",
  asian_index: "亚洲指数",
  bond: "债券利率",
  commodity: "商品",
  crypto: "加密货币"
};

const classOrder = ["stock_index", "futures", "asian_index", "bond", "commodity", "crypto"];
const windowOptions = [
  { label: "1小时", value: "1" },
  { label: "4小时", value: "4" },
  { label: "6小时", value: "6" },
  { label: "12小时", value: "12" },
  { label: "24小时", value: "24" },
  { label: "3天", value: "72" },
  { label: "7天", value: "168" },
  { label: "30天", value: "720" }
];

const DEFAULT_CHART_SYMBOLS = [
  "YM=F",       // 道指期货
  "NQ=F",       // 纳指期货
  "000001.SS",  // 上证指数
  "^N225",      // 日经指数
  "^KS11",      // 韩国KOSPI
  "GC=F",       // 黄金
  "CL=F",       // 原油
  "BTC/USDT"    // BTC
];

// 各市场开市时间（北京时间）。美东市场标"夏令"以提示冬令时会整体晚 1 小时。
const MARKET_HOURS_BY_SYMBOL: Record<string, string> = {
  // 美股期货 / 商品期货（CME / NYMEX，几乎 24h，每天 05:00-06:00 BJT 收盘）
  "ES=F": "周一 06:00 — 周六 05:00 (夏令)",
  "NQ=F": "周一 06:00 — 周六 05:00 (夏令)",
  "YM=F": "周一 06:00 — 周六 05:00 (夏令)",
  "CL=F": "周一 06:00 — 周六 05:00 (夏令)",
  "GC=F": "周一 06:00 — 周六 05:00 (夏令)",
  "SI=F": "周一 06:00 — 周六 05:00 (夏令)",
  // 美股指数（NYSE / NASDAQ 常规盘）
  "^DJI": "21:30 — 次日 04:00 (夏令)",
  "^IXIC": "21:30 — 次日 04:00 (夏令)",
  "^GSPC": "21:30 — 次日 04:00 (夏令)",
  // 日经
  "^N225": "08:00 — 14:00 (含午休)",
  // 韩国 KOSPI
  "^KS11": "08:00 — 14:30",
  // A 股
  "000001.SS": "09:30 — 15:00 (含午休)",
  "399001.SZ": "09:30 — 15:00 (含午休)",
  "399006.SZ": "09:30 — 15:00 (含午休)",
  // 美债（CBOT 现货跟美股大体同步）
  "US_10Y": "21:30 — 次日 04:00 (夏令)",
  "US_2Y": "21:30 — 次日 04:00 (夏令)",
  "US_SPREAD": "21:30 — 次日 04:00 (夏令)",
  // 日债（TSE）
  "JP_10Y": "08:00 — 14:00",
  "JP_2Y": "08:00 — 14:00",
  "JP_SPREAD": "08:00 — 14:00"
};

const MARKET_HOURS_BY_CLASS: Record<string, string> = {
  crypto: "全天 24h"
};

function marketHours(symbol: string, assetClass: string): string {
  return MARKET_HOURS_BY_SYMBOL[symbol] ?? MARKET_HOURS_BY_CLASS[assetClass] ?? "—";
}

const CHART_SYMBOLS_STORAGE_KEY = "market-chart-symbols";

function loadChartSymbols(): string[] {
  if (typeof window === "undefined") return DEFAULT_CHART_SYMBOLS;
  try {
    const raw = window.localStorage.getItem(CHART_SYMBOLS_STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed) && parsed.every((x) => typeof x === "string")) return parsed;
    }
  } catch {
    // ignore parse errors and fall back to default
  }
  return DEFAULT_CHART_SYMBOLS;
}

function persistChartSymbols(symbols: string[]) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(CHART_SYMBOLS_STORAGE_KEY, JSON.stringify(symbols));
  } catch {
    // ignore quota / privacy-mode errors
  }
}

function formatPrice(item: MarketLatestItem) {
  if (item.asset_class === "bond") return `${item.price.toFixed(3)}%`;
  if (item.asset_class === "crypto") return `$${item.price.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
  return item.price.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function tone(value: number | null | undefined): "up" | "down" | "neutral" {
  if (value == null) return "neutral";
  if (value > 0) return "up";
  if (value < 0) return "down";
  return "neutral";
}

function pct(value: number | null | undefined) {
  if (value == null) return "—";
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
}

function buildHistoryChart(history?: MarketHistoryResponse): { data: ChartPoint[]; keys: string[] } {
  if (!history) return { data: [], keys: [] };
  // 合并键用 UTC 截到分钟（ISO 8601 字典序即时间序，Map.values() 保留插入顺序，
  // 但不同 series 的点可能错乱所以最后必须按 UTC 显式排序）；显示用 BJT。
  const byUtcMinute = new Map<string, ChartPoint>();
  const keys: string[] = [];
  history.series.forEach((series) => {
    const key = `${series.name} (${series.symbol})`;
    keys.push(key);
    series.points.forEach((point) => {
      if (!point.timestamp_utc) return;
      const utcMinute = point.timestamp_utc.slice(0, 16);
      const displayTime = point.timestamp_bj?.slice(5, 16) ?? utcMinute;
      const row = byUtcMinute.get(utcMinute) ?? { time: displayTime };
      row[key] = point.normalized_pct;
      byUtcMinute.set(utcMinute, row);
    });
  });
  const sorted = Array.from(byUtcMinute.entries())
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([, row]) => row);
  return { data: sorted, keys };
}

export function MarketPage() {
  const queryClient = useQueryClient();
  const [hours, setHours] = useState("4");
  const [chartSymbols, setChartSymbolsState] = useState<string[]>(loadChartSymbols);
  const setChartSymbols = (next: string[]) => {
    setChartSymbolsState(next);
    persistChartSymbols(next);
  };

  const [tableExpanded, setTableExpanded] = useState(false);
  const [tableHours, setTableHours] = useState("24");
  const [tableAssetClasses, setTableAssetClasses] = useState<string[]>([]);
  const [tablePage, setTablePage] = useState(1);

  const chartPanelRef = useRef<HTMLElement>(null);
  const [chartFullscreen, setChartFullscreen] = useState(false);

  useEffect(() => {
    const handler = () => {
      setChartFullscreen(document.fullscreenElement === chartPanelRef.current);
    };
    document.addEventListener("fullscreenchange", handler);
    return () => document.removeEventListener("fullscreenchange", handler);
  }, []);

  const toggleChartFullscreen = () => {
    const el = chartPanelRef.current;
    if (!el) return;
    if (document.fullscreenElement) {
      void document.exitFullscreen();
    } else {
      void el.requestFullscreen();
    }
  };

  const latest = useQuery({ queryKey: ["market-latest"], queryFn: api.marketLatest, refetchInterval: 60_000 });
  const symbolsList = useQuery({ queryKey: ["market-symbols"], queryFn: () => api.marketSymbols() });

  const history = useQuery({
    queryKey: ["market-history", hours, chartSymbols.join(",")],
    queryFn: () => api.marketHistory({ hours: Number(hours), symbols: chartSymbols }),
    enabled: chartSymbols.length > 0
  });

  const table = useQuery({
    queryKey: ["market-table", tableHours, tablePage, tableAssetClasses.join(",")],
    queryFn: () =>
      api.marketTable({
        hours: Number(tableHours),
        page: tablePage,
        page_size: 50,
        asset_classes: tableAssetClasses.length ? tableAssetClasses : undefined
      }),
    enabled: tableExpanded
  });

  const scan = useMutation({
    mutationFn: api.scan,
    onSuccess: () => {
      void queryClient.invalidateQueries();
    }
  });

  const chartSymbolOptions: MultiOption[] = useMemo(() => {
    const items = symbolsList.data ?? [];
    return items.map((s) => ({
      label: `${s.name} (${s.symbol})`,
      value: s.symbol,
      group: classNames[s.asset_class] ?? s.asset_class
    }));
  }, [symbolsList.data]);

  const tableAssetClassOptions: MultiOption[] = classOrder.map((c) => ({
    label: classNames[c] ?? c,
    value: c
  }));

  const tableCsvHref = useMemo(() => {
    const search = new URLSearchParams();
    search.set("hours", tableHours);
    tableAssetClasses.forEach((cls) => search.append("asset_classes", cls));
    return `/api/market/table.csv?${search.toString()}`;
  }, [tableHours, tableAssetClasses]);

  const chart = buildHistoryChart(history.data);

  return (
    <section>
      <PageHeader
        title="市场概览"
        subtitle={`最后更新 ${latest.data?.last_updated?.timestamp_bj ?? "—"}`}
        actions={
          <Button onClick={() => scan.mutate()} disabled={scan.isPending}>
            <Play size={16} />手动扫描
          </Button>
        }
      />

      {latest.isLoading ? <LoadingState /> : latest.error ? <ErrorState error={latest.error} /> : null}
      {scan.data ? <div className={`task-banner ${scan.data.status}`}>{scan.data.status} · {scan.data.message}</div> : null}

      <div className="asset-groups">
        {classOrder.map((assetClass) => {
          const items = latest.data?.items.filter((item) => item.asset_class === assetClass) ?? [];
          if (!items.length) return null;
          return (
            <section className="band" key={assetClass}>
              <h2>{classNames[assetClass] ?? assetClass}</h2>
              <div className="asset-grid">
                {items.map((item) => (
                  <article className="asset-card" key={item.symbol}>
                    <div className="asset-meta">
                      <span>{item.name}</span>
                      <code>{item.symbol}</code>
                    </div>
                    <strong>{formatPrice(item)}</strong>
                    <div className="mini-stats">
                      <Stat label="5m" value={pct(item.change_5m)} tone={tone(item.change_5m)} />
                      <Stat label="1h" value={pct(item.change_1h)} tone={tone(item.change_1h)} />
                      <Stat label="24h" value={pct(item.change_24h)} tone={tone(item.change_24h)} />
                    </div>
                    <div className="card-foot">
                      <small>{item.timestamp_bj}</small>
                      <small className="market-hours">开市 {marketHours(item.symbol, item.asset_class)}</small>
                    </div>
                  </article>
                ))}
              </div>
            </section>
          );
        })}
      </div>

      <section className="panel chart-panel" ref={chartPanelRef}>
        <div className="panel-head">
          <h2>跨资产走势</h2>
          <div className="panel-controls">
            <MultiSelectControl
              label="品种"
              values={chartSymbols}
              onChange={setChartSymbols}
              options={chartSymbolOptions}
              emptyLabel="未选"
            />
            <SelectControl label="走势区间" value={hours} onChange={setHours} options={windowOptions} />
            <Button kind="secondary" onClick={toggleChartFullscreen}>
              {chartFullscreen ? <Minimize2 size={16} /> : <Maximize2 size={16} />}
              {chartFullscreen ? "退出全屏" : "全屏"}
            </Button>
          </div>
        </div>
        {history.isLoading ? (
          <LoadingState />
        ) : history.error ? (
          <ErrorState error={history.error} />
        ) : chartSymbols.length === 0 ? (
          <EmptyState title="请选择至少一个品种" />
        ) : (
          <MultiLineChart data={chart.data} keys={chart.keys} />
        )}
      </section>

      <section className="panel">
        <button
          type="button"
          className="panel-toggle"
          onClick={() => setTableExpanded((v) => !v)}
          aria-expanded={tableExpanded}
        >
          <ChevronDown
            size={18}
            style={{
              transform: tableExpanded ? "rotate(0deg)" : "rotate(-90deg)",
              transition: "transform 0.15s"
            }}
          />
          <h2>明细表</h2>
          <span className="muted-text">{tableExpanded ? "点击收起" : "点击展开"}</span>
        </button>
        {tableExpanded ? (
          <>
            <div className="panel-controls table-filters">
              <SelectControl label="时间窗口" value={tableHours} onChange={setTableHours} options={windowOptions} />
              <MultiSelectControl
                label="资产类别"
                values={tableAssetClasses}
                onChange={(next) => {
                  setTableAssetClasses(next);
                  setTablePage(1);
                }}
                options={tableAssetClassOptions}
                emptyLabel="全部"
              />
              <a className="button secondary" href={tableCsvHref}>
                <Download size={16} />CSV
              </a>
            </div>
            {table.isLoading ? (
              <LoadingState />
            ) : table.error ? (
              <ErrorState error={table.error} />
            ) : (
              <>
                <DataTable<MarketTableRow>
                  rows={table.data?.items ?? []}
                  columns={[
                    { key: "time", header: "北京时间", cell: (row) => row.timestamp_bj },
                    { key: "class", header: "类别", cell: (row) => classNames[row.asset_class] ?? row.asset_class },
                    { key: "name", header: "名称", cell: (row) => `${row.name} (${row.symbol})` },
                    { key: "price", header: "价格", cell: (row) => row.price.toLocaleString() },
                    { key: "chg", header: "涨跌", cell: (row) => pct(row.change_pct), className: "num" },
                    { key: "source", header: "来源", cell: (row) => row.source }
                  ]}
                />
                <div className="pager">
                  <Button kind="ghost" disabled={tablePage <= 1} onClick={() => setTablePage((v) => v - 1)}>上一页</Button>
                  <span>{table.data?.page ?? 1} / {table.data?.pages || 1}</span>
                  <Button kind="ghost" disabled={!table.data || table.data.page >= table.data.pages} onClick={() => setTablePage((v) => v + 1)}>下一页</Button>
                </div>
              </>
            )}
          </>
        ) : null}
      </section>
      {!latest.isLoading && !latest.data?.items.length ? <EmptyState title="暂无价格数据" /> : null}
    </section>
  );
}
