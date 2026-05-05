import { MultiLineChart, type ChartPoint } from "./Charts";

type PredictionCardMeta = {
  volume?: number | null;
  outcomes?: number;
  updatedAt?: string | null;
  latestPct?: number | null;
};

export function PredictionCard({
  title,
  subtitle,
  data,
  keys,
  meta,
  height = 240
}: {
  title: string;
  subtitle?: string;
  data: ChartPoint[];
  keys: string[];
  meta?: PredictionCardMeta;
  height?: number;
}) {
  const footers: string[] = [];
  if (meta?.outcomes !== undefined) footers.push(`${meta.outcomes} 个分支`);
  if (meta?.volume !== undefined && meta.volume !== null) {
    footers.push(`成交 $${(meta.volume / 1000).toFixed(0)}k`);
  }
  if (meta?.latestPct !== undefined && meta.latestPct !== null) {
    footers.push(`最新 ${meta.latestPct.toFixed(1)}%`);
  }
  if (meta?.updatedAt) footers.push(`更新 ${meta.updatedAt.slice(5, 16)}`);

  return (
    <article className="prediction-card">
      <header className="prediction-card-head">
        <h3>{title}</h3>
        {subtitle ? <span className="muted-text">{subtitle}</span> : null}
      </header>
      <MultiLineChart data={data} keys={keys} height={height} />
      {footers.length > 0 ? (
        <footer className="prediction-card-foot">
          {footers.map((f) => (
            <span key={f}>{f}</span>
          ))}
        </footer>
      ) : null}
    </article>
  );
}
