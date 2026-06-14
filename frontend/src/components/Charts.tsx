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
