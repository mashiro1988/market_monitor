import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { PredictionFamily, PredictionMarketSummary, PredictionRow } from "../api/types";
import { MultiLineChart, type ChartPoint } from "../components/Charts";
import { PageHeader, SelectControl, Stat, TextInput } from "../components/Controls";
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

function MarketDetail({ market, hours }: { market: PredictionMarketSummary; hours: number }) {
  const history = useQuery({
    queryKey: ["prediction-history", market.market_id, hours],
    queryFn: () => api.predictionHistory(market.market_id, hours),
    enabled: Boolean(market.market_id)
  });
  const chart = useMemo(() => {
    const byTime = new Map<string, ChartPoint>();
    const keys = Array.from(new Set((history.data ?? []).map((row: PredictionRow) => row.outcome)));
    (history.data ?? []).forEach((row) => {
      const time = row.timestamp_bj?.slice(5, 16) ?? "";
      const entry = byTime.get(time) ?? { time };
      entry[row.outcome] = row.probability_pct;
      byTime.set(time, entry);
    });
    return { data: Array.from(byTime.values()), keys };
  }, [history.data]);

  return (
    <section className="panel">
      <div className="panel-head"><h2>{market.question}</h2></div>
      <div className="metric-row">
        {market.outcomes.map((outcome) => (
          <Stat key={outcome.outcome} label={outcome.outcome} value={`${outcome.probability_pct.toFixed(1)}%`} tone={(outcome.delta_pct ?? 0) >= 0 ? "up" : "down"} />
        ))}
      </div>
      {history.isLoading ? <LoadingState /> : history.error ? <ErrorState error={history.error} /> : <MultiLineChart data={chart.data} keys={chart.keys} height={300} />}
    </section>
  );
}

export function PredictionsPage() {
  const [hours, setHours] = useState("24");
  const [search, setSearch] = useState("");
  const families = useQuery({ queryKey: ["prediction-families", hours, search], queryFn: () => api.predictionFamilies({ hours: Number(hours), search }) });
  const predictions = useQuery({ queryKey: ["predictions", hours, search], queryFn: () => api.predictions({ hours: Number(hours), search }) });
  const [selectedMarket, setSelectedMarket] = useState("");
  const market = predictions.data?.markets.find((item) => item.market_id === selectedMarket) ?? predictions.data?.markets[0];

  return (
    <section>
      <PageHeader title="预测市场" subtitle={`最后更新 ${predictions.data?.latest_timestamp?.timestamp_bj ?? "—"}`} />
      <div className="toolbar">
        <SelectControl label="时间窗口" value={hours} onChange={setHours} options={hourOptions} />
        <TextInput label="搜索市场" value={search} onChange={setSearch} placeholder="Fed / inflation / hormuz" />
      </div>

      <section className="panel">
        <div className="panel-head"><h2>主题概率对比</h2></div>
        {families.isLoading ? <LoadingState /> : families.error ? <ErrorState error={families.error} /> : (
          families.data?.length ? families.data.map((family) => {
            const chart = buildFamilyChart(family);
            return (
              <details className="family-block" key={family.id} open>
                <summary>{family.name}</summary>
                <MultiLineChart data={chart.data} keys={chart.keys} height={320} />
              </details>
            );
          }) : <EmptyState title="当前窗口内没有可聚合的主题组" />
        )}
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>单市场明细</h2>
          <select value={market?.market_id ?? ""} onChange={(event) => setSelectedMarket(event.target.value)}>
            {(predictions.data?.markets ?? []).slice(0, 300).map((item) => (
              <option key={item.market_id} value={item.market_id}>{item.question.slice(0, 140)}</option>
            ))}
          </select>
        </div>
        {predictions.isLoading ? <LoadingState /> : predictions.error ? <ErrorState error={predictions.error} /> : market ? <MarketDetail market={market} hours={Number(hours)} /> : <EmptyState title="没有匹配市场" />}
      </section>
    </section>
  );
}
