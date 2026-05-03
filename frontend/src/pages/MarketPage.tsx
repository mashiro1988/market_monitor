import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Play, RefreshCw } from "lucide-react";
import { api } from "../api/client";
import type { MarketHistoryResponse, MarketLatestItem, MarketTableRow } from "../api/types";
import { MultiLineChart, type ChartPoint } from "../components/Charts";
import { Button, PageHeader, SelectControl, Stat } from "../components/Controls";
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
  const byTime = new Map<string, ChartPoint>();
  const keys: string[] = [];
  history.series.forEach((series) => {
    const key = `${series.name} (${series.symbol})`;
    keys.push(key);
    series.points.forEach((point) => {
      const time = point.timestamp_bj?.slice(5, 16) ?? "";
      const row = byTime.get(time) ?? { time };
      row[key] = point.normalized_pct;
      byTime.set(time, row);
    });
  });
  return { data: Array.from(byTime.values()), keys };
}

export function MarketPage() {
  const queryClient = useQueryClient();
  const [hours, setHours] = useState("24");
  const [tablePage, setTablePage] = useState(1);
  const latest = useQuery({ queryKey: ["market-latest"], queryFn: api.marketLatest, refetchInterval: 60_000 });
  const symbols = useMemo(() => latest.data?.items.slice(0, 8).map((item) => item.symbol) ?? [], [latest.data]);
  const history = useQuery({
    queryKey: ["market-history", hours, symbols.join(",")],
    queryFn: () => api.marketHistory({ hours: Number(hours), symbols }),
    enabled: symbols.length > 0
  });
  const table = useQuery({
    queryKey: ["market-table", hours, tablePage],
    queryFn: () => api.marketTable({ hours: Number(hours), page: tablePage, page_size: 50 })
  });
  const scan = useMutation({
    mutationFn: api.scan,
    onSuccess: () => {
      void queryClient.invalidateQueries();
    }
  });
  const chart = buildHistoryChart(history.data);

  return (
    <section>
      <PageHeader
        title="市场概览"
        subtitle={`最后更新 ${latest.data?.last_updated?.timestamp_bj ?? "—"}`}
        actions={
          <>
            <Button kind="secondary" onClick={() => void queryClient.invalidateQueries()}>
              <RefreshCw size={16} />刷新
            </Button>
            <Button onClick={() => scan.mutate()} disabled={scan.isPending}>
              <Play size={16} />手动扫描
            </Button>
          </>
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
                    <small>{item.timestamp_bj}</small>
                  </article>
                ))}
              </div>
            </section>
          );
        })}
      </div>

      <section className="panel">
        <div className="panel-head">
          <h2>跨资产走势</h2>
          <SelectControl label="走势区间" value={hours} onChange={setHours} options={windowOptions} />
        </div>
        {history.isLoading ? <LoadingState /> : history.error ? <ErrorState error={history.error} /> : <MultiLineChart data={chart.data} keys={chart.keys} />}
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>明细表</h2>
          <a className="button secondary" href={`/api/market/table.csv?hours=${hours}`}>
            <Download size={16} />CSV
          </a>
        </div>
        {table.isLoading ? <LoadingState /> : table.error ? <ErrorState error={table.error} /> : (
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
      </section>
      {!latest.isLoading && !latest.data?.items.length ? <EmptyState title="暂无价格数据" /> : null}
    </section>
  );
}
