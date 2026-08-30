// 持仓策略专用图。不复用 MultiLineChart：需要 stepAfter/散点/横向参考区，塞进共享组件会污染它。
import {
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceArea,
  ReferenceDot,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  Tooltip,
  XAxis,
  YAxis
} from "recharts";
import type { StrategyOverview } from "../api/types";
import { EmptyState } from "./StateViews";

export function StrategyChart({ overview, height = 380 }: { overview: StrategyOverview; height?: number }) {
  const { chart } = overview;
  if (!chart.days.length) {
    return <EmptyState title="暂无日K数据（数据滞后或标的无历史）" />;
  }
  // 入场标记并进主数据行：分类 X 轴上给 Scatter 独立 data 会劫持轴域（只剩标记那一天）
  const entryByDate = new Map(chart.entry_markers.map((m) => [m.date, m.value]));
  const rows = chart.days.map((d, i) => ({
    date: d.date,
    close: d.close,
    soft: chart.soft_line[i] ?? null,
    entry: entryByDate.get(d.date) ?? null
  }));
  const values = rows.flatMap((r) => [r.close, r.soft ?? r.close]);
  if (chart.hard_current != null) values.push(chart.hard_current);
  chart.cost_lines.forEach((c) => values.push(c.value));
  const yMin = Math.min(...values) * 0.96;
  const yMax = Math.max(...values) * 1.03;

  return (
    <div className="chart-shell" style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={rows} margin={{ left: 0, right: 16, top: 8, bottom: 0 }}>
          <CartesianGrid stroke="rgba(148,163,184,0.14)" vertical={false} />
          <XAxis dataKey="date" tick={{ fill: "#94a3b8", fontSize: 12 }} minTickGap={28} />
          <YAxis
            tick={{ fill: "#94a3b8", fontSize: 12 }}
            domain={[yMin, yMax]}
            width={56}
            tickFormatter={(v: number) => v.toFixed(3)}
            allowDataOverflow
          />
          <Tooltip
            contentStyle={{ background: "#0f172a", border: "1px solid #263142", color: "#e2e8f0" }}
            formatter={(value, name) => [
              typeof value === "number" ? value.toFixed(4) : String(value),
              name === "close" ? "日收盘" : name === "soft" ? "软止损" : name === "entry" ? "入场" : String(name)
            ]}
          />
          {chart.hard_current != null ? (
            <ReferenceArea
              y1={yMin}
              y2={chart.hard_current}
              fill="#7f1d1d"
              fillOpacity={0.22}
              label={{
                value: `6×ATR 硬防线 ${chart.hard_current.toFixed(4)}`,
                position: "insideBottomLeft",
                fill: "#f87171",
                fontSize: 12
              }}
            />
          ) : null}
          {chart.cost_lines.map((c) => (
            <ReferenceLine
              key={`cost-${c.label}`}
              y={c.value}
              stroke="#22d3ee"
              strokeDasharray="3 3"
              label={{
                value: `${c.label} 成本 ${c.value.toFixed(4)}`,
                position: "insideTopLeft",
                fill: "#22d3ee",
                fontSize: 12
              }}
            />
          ))}
          <Line dataKey="soft" type="stepAfter" stroke="#f59e0b" strokeWidth={2.4} strokeDasharray="7 4" dot={false} connectNulls name="soft" />
          <Line dataKey="close" type="monotone" stroke="#5eead4" strokeWidth={2.4} dot={false} name="close" />
          <Scatter dataKey="entry" fill="#22d3ee" shape="triangle" name="入场" />
          {chart.anchor_point ? (
            <ReferenceDot
              x={chart.anchor_point.date}
              y={chart.anchor_point.value}
              r={5}
              fill="#fbbf24"
              stroke="none"
              label={{ value: "最高收盘（移动基准）", position: "top", fill: "#fbbf24", fontSize: 12 }}
            />
          ) : null}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
