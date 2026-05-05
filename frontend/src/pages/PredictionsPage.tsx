import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { PredictionFamily, PredictionMarketSummary, PredictionRow } from "../api/types";
import { type ChartPoint } from "../components/Charts";
import { PageHeader, SelectControl, TextInput } from "../components/Controls";
import { PredictionCard } from "../components/PredictionCard";
import { TrackedMarketsPanel } from "../components/TrackedMarketsPanel";
import { EmptyState, ErrorState, LoadingState } from "../components/StateViews";

const hourOptions = [
  { label: "2小时", value: "2" },
  { label: "6小时", value: "6" },
  { label: "24小时", value: "24" },
  { label: "7天", value: "168" },
  { label: "30天", value: "720" }
];

function buildFamilyChart(family: PredictionFamily): { data: ChartPoint[]; keys: string[] } {
  const byTime = new Map<string, ChartPoint>();
  const keys: string[] = [];
  family.series.forEach((series) => {
    keys.push(series.label);
    series.points.forEach((point) => {
      const time = point.timestamp_bj?.slice(5, 16) ?? "";
      const row = byTime.get(time) ?? { time };
      row[series.label] = point.probability_pct;
      byTime.set(time, row);
    });
  });
  return { data: Array.from(byTime.values()), keys };
}

function buildMarketChart(history: PredictionRow[]): { data: ChartPoint[]; keys: string[] } {
  const byTime = new Map<string, ChartPoint>();
  const keys = Array.from(new Set(history.map((row) => row.outcome)));
  history.forEach((row) => {
    const time = row.timestamp_bj?.slice(5, 16) ?? "";
    const entry = byTime.get(time) ?? { time };
    entry[row.outcome] = row.probability_pct;
    byTime.set(time, entry);
  });
  return { data: Array.from(byTime.values()), keys };
}

function MarketCard({ market, hours }: { market: PredictionMarketSummary; hours: number }) {
  const history = useQuery({
    queryKey: ["prediction-history", market.market_id, hours],
    queryFn: () => api.predictionHistory(market.market_id, hours),
    enabled: Boolean(market.market_id)
  });
  const chart = useMemo(
    () => buildMarketChart(history.data ?? []),
    [history.data]
  );
  const yes = market.outcomes.find((o) => o.outcome.toLowerCase() === "yes");
  const latestPct = yes?.probability_pct ?? market.outcomes[0]?.probability_pct;
  const updatedAt = market.outcomes[0]?.timestamp_bj ?? null;

  return (
    <PredictionCard
      title={market.question}
      data={chart.data}
      keys={chart.keys}
      meta={{
        volume: market.volume,
        outcomes: market.outcomes.length,
        latestPct,
        updatedAt
      }}
    />
  );
}

export function PredictionsPage() {
  const [hours, setHours] = useState("24");
  const [search, setSearch] = useState("");

  const families = useQuery({
    queryKey: ["prediction-families", hours, search],
    queryFn: () => api.predictionFamilies({ hours: Number(hours), search })
  });
  const predictions = useQuery({
    queryKey: ["predictions", hours, search],
    queryFn: () => api.predictions({ hours: Number(hours), search })
  });

  const familyMarketIds = useMemo(() => {
    const ids = new Set<string>();
    (families.data ?? []).forEach((f) =>
      f.series.forEach((s) => ids.add(s.market_id))
    );
    return ids;
  }, [families.data]);

  const standaloneMarkets = useMemo(() => {
    return (predictions.data?.markets ?? []).filter((m) => !familyMarketIds.has(m.market_id));
  }, [predictions.data, familyMarketIds]);

  return (
    <section>
      <PageHeader
        title="预测市场"
        subtitle={`最后更新 ${predictions.data?.latest_timestamp?.timestamp_bj ?? "—"}`}
      />
      <div className="toolbar">
        <SelectControl label="时间窗口" value={hours} onChange={setHours} options={hourOptions} />
        <TextInput label="搜索市场" value={search} onChange={setSearch} placeholder="Fed / inflation / hormuz" />
      </div>

      <TrackedMarketsPanel />

      <section className="panel">
        <div className="panel-head"><h2>主题概率对比</h2></div>
        {families.isLoading ? (
          <LoadingState />
        ) : families.error ? (
          <ErrorState error={families.error} />
        ) : (families.data ?? []).length ? (
          <div className="prediction-grid">
            {(families.data ?? []).map((family) => {
              const chart = buildFamilyChart(family);
              return (
                <PredictionCard
                  key={family.id}
                  title={family.name}
                  subtitle={`${family.series.length} 个分支`}
                  data={chart.data}
                  keys={chart.keys}
                />
              );
            })}
          </div>
        ) : (
          <EmptyState title="当前窗口内没有可聚合的主题组" />
        )}
      </section>

      <section className="panel">
        <div className="panel-head"><h2>单市场</h2></div>
        {predictions.isLoading ? (
          <LoadingState />
        ) : predictions.error ? (
          <ErrorState error={predictions.error} />
        ) : standaloneMarkets.length ? (
          <div className="prediction-grid">
            {standaloneMarkets.map((m) => (
              <MarketCard key={m.market_id} market={m} hours={Number(hours)} />
            ))}
          </div>
        ) : (
          <EmptyState title="没有不属于任何主题组的单市场" />
        )}
      </section>
    </section>
  );
}
