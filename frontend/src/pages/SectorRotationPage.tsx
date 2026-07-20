import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, RefreshCcw } from "lucide-react";
import { api } from "../api/client";
import type { SectorLeaderboardRow, SectorTokenRow } from "../api/types";
import { Button, PageHeader, SelectControl } from "../components/Controls";
import { EmptyState, ErrorState, LoadingState } from "../components/StateViews";

type SortKey =
  | "ret_1h_median"
  | "ret_24h_median"
  | "ret_168h_median"
  | "ret_720h_median"
  | "ret_1h"
  | "ret_24h"
  | "ret_168h"
  | "ret_720h"
  | "token_count";

const SORT_OPTIONS: { label: string; value: SortKey }[] = [
  { label: "24 小时中位", value: "ret_24h_median" },
  { label: "1 小时中位", value: "ret_1h_median" },
  { label: "7 天中位", value: "ret_168h_median" },
  { label: "30 天中位", value: "ret_720h_median" },
  { label: "24 小时均值", value: "ret_24h" },
  { label: "1 小时均值", value: "ret_1h" },
  { label: "7 天均值", value: "ret_168h" },
  { label: "30 天均值", value: "ret_720h" },
  { label: "成分币数量", value: "token_count" },
];

function fmtPct(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toFixed(2)}%`;
}

function pctClass(v: number | null | undefined): string {
  if (v === null || v === undefined) return "";
  if (v > 0.05) return "ret-up";
  if (v < -0.05) return "ret-down";
  return "ret-flat";
}

function compareDescNullsLast(a: number | null, b: number | null): number {
  if (a === null && b === null) return 0;
  if (a === null) return 1;
  if (b === null) return -1;
  return b - a;
}

export function SectorRotationPage() {
  const queryClient = useQueryClient();
  const [sortBy, setSortBy] = useState<SortKey>("ret_24h_median");
  const [expandedCategory, setExpandedCategory] = useState<string | null>(null);

  const leaderboard = useQuery({
    queryKey: ["sector-leaderboard"],
    queryFn: api.sectorLeaderboard,
    refetchInterval: 60_000, // 每 60s 自动刷新（数据本身 1h 才会变，但刷一下不亏）
  });

  const tokens = useQuery({
    queryKey: ["sector-tokens", expandedCategory],
    queryFn: () => api.sectorTokens(expandedCategory!),
    enabled: expandedCategory !== null,
  });

  const sortedRows: SectorLeaderboardRow[] = useMemo(() => {
    const rows = leaderboard.data?.rows ?? [];
    return [...rows].sort((a, b) =>
      sortBy === "token_count"
        ? b.token_count - a.token_count
        : compareDescNullsLast(a[sortBy], b[sortBy])
    );
  }, [leaderboard.data, sortBy]);

  function toggleRow(cat: string) {
    setExpandedCategory((cur) => (cur === cat ? null : cat));
  }

  function handleRefresh() {
    queryClient.invalidateQueries({ queryKey: ["sector-leaderboard"] });
    if (expandedCategory) {
      queryClient.invalidateQueries({ queryKey: ["sector-tokens", expandedCategory] });
    }
  }

  const snapshotBj = leaderboard.data?.snapshot_at?.timestamp_bj ?? "—";

  return (
    <section>
      <PageHeader
        title="板块轮动"
        subtitle={`最新快照（北京时间）：${snapshotBj}　·　数据来自 BMAC 远程数据中心，CMC 板块映射 7 天 TTL`}
        actions={
          <Button onClick={handleRefresh} disabled={leaderboard.isFetching}>
            <RefreshCcw size={16} />刷新
          </Button>
        }
      />

      <section className="panel">
        <div className="panel-head">
          <h2>板块榜单</h2>
          <SelectControl
            label="排序"
            value={sortBy}
            onChange={(v) => setSortBy(v as SortKey)}
            options={SORT_OPTIONS.map((o) => ({ label: o.label, value: o.value }))}
          />
        </div>

        {leaderboard.isLoading ? (
          <LoadingState />
        ) : leaderboard.error ? (
          <ErrorState error={leaderboard.error} />
        ) : sortedRows.length === 0 ? (
          <EmptyState title="暂无数据">
            <p>板块扫描还没跑过，或服务器数据缓存还没拉到。等首次刷新后再回来看。</p>
          </EmptyState>
        ) : (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th style={{ width: 32 }}></th>
                  <th>板块</th>
                  <th>分组</th>
                  <th style={{ textAlign: "right" }}>成分币</th>
                  <th style={{ textAlign: "right" }}>1h</th>
                  <th style={{ textAlign: "right" }}>24h</th>
                  <th style={{ textAlign: "right" }}>7d</th>
                  <th style={{ textAlign: "right" }}>30d</th>
                </tr>
              </thead>
              <tbody>
                {sortedRows.map((row) => {
                  const expanded = expandedCategory === row.category;
                  return (
                    <RowGroup
                      key={row.category}
                      row={row}
                      expanded={expanded}
                      onToggle={() => toggleRow(row.category)}
                      tokens={expanded ? tokens.data?.tokens ?? null : null}
                      tokensLoading={expanded && tokens.isLoading}
                      tokensError={expanded ? tokens.error : null}
                    />
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </section>
  );
}

function RowGroup({
  row,
  expanded,
  onToggle,
  tokens,
  tokensLoading,
  tokensError,
}: {
  row: SectorLeaderboardRow;
  expanded: boolean;
  onToggle: () => void;
  tokens: SectorTokenRow[] | null;
  tokensLoading: boolean;
  tokensError: unknown;
}) {
  return (
    <>
      <tr onClick={onToggle} style={{ cursor: "pointer" }}>
        <td>{expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</td>
        <td><strong>{row.category}</strong></td>
        <td><span className="muted">{row.group ?? "—"}</span></td>
        <td style={{ textAlign: "right" }}>{row.token_count}</td>
        <ReturnCell median={row.ret_1h_median} mean={row.ret_1h} />
        <ReturnCell median={row.ret_24h_median} mean={row.ret_24h} />
        <ReturnCell median={row.ret_168h_median} mean={row.ret_168h} />
        <ReturnCell median={row.ret_720h_median} mean={row.ret_720h} />
      </tr>
      {expanded && (
        <tr>
          <td colSpan={8} style={{ padding: 0, background: "var(--bg-secondary, #f9fafb)" }}>
            <div style={{ padding: "12px 24px 16px 48px" }}>
              {tokensLoading ? (
                <LoadingState label="加载板块成分币" />
              ) : tokensError ? (
                <ErrorState error={tokensError} />
              ) : tokens && tokens.length > 0 ? (
                <table className="data-table" style={{ marginTop: 0 }}>
                  <thead>
                    <tr>
                      <th>Symbol</th>
                      <th>Binance pair</th>
                      <th>市场</th>
                      <th style={{ textAlign: "right" }}>1h</th>
                      <th style={{ textAlign: "right" }}>24h</th>
                      <th style={{ textAlign: "right" }}>7d</th>
                      <th style={{ textAlign: "right" }}>30d</th>
                    </tr>
                  </thead>
                  <tbody>
                    {tokens.map((t) => (
                      <tr key={`${t.binance_symbol}-${t.market}`}>
                        <td><strong>{t.symbol}</strong></td>
                        <td><code>{t.binance_symbol}</code></td>
                        <td>{t.market}</td>
                        <td style={{ textAlign: "right" }} className={pctClass(t.ret_1h)}>{fmtPct(t.ret_1h)}</td>
                        <td style={{ textAlign: "right" }} className={pctClass(t.ret_24h)}>{fmtPct(t.ret_24h)}</td>
                        <td style={{ textAlign: "right" }} className={pctClass(t.ret_168h)}>{fmtPct(t.ret_168h)}</td>
                        <td style={{ textAlign: "right" }} className={pctClass(t.ret_720h)}>{fmtPct(t.ret_720h)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <EmptyState title="该板块下暂无 BMAC 命中的活跃成分币" />
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

function ReturnCell({ median, mean }: { median: number | null | undefined; mean: number | null | undefined }) {
  return (
    <td style={{ textAlign: "right" }} className={pctClass(median ?? mean)}>
      <div>{fmtPct(median)}</div>
      <div className="muted" style={{ fontSize: 13.5 }}>均 {fmtPct(mean)}</div>
    </td>
  );
}
