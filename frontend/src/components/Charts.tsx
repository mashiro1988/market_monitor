import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceArea,
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
export type ChartMarkerInput = { time: string; role: "driver" };

export function MultiLineChart({
  data,
  keys,
  height = 340,
  unit = "%",
  markers = [],
  highlightKey,
  baseline,
  valueFormatter,
  tooltipFormatter,
  yDomain,
  shadedBands,
  secondaryKeys
}: {
  data: ChartPoint[];
  keys: string[];
  height?: number;
  unit?: string;
  markers?: ChartMarkerInput[];
  highlightKey?: string;
  baseline?: number;
  valueFormatter?: (value: number) => string;
  // tooltip 逐条目格式化：返回该系列的展示串（可借 row 附带原始价格等上下文）。
  // 不传则用 recharts 默认（原始浮点全精度）。
  tooltipFormatter?: (value: number, seriesKey: string, row: ChartPoint | undefined) => string;
  yDomain?: [number, number];
  shadedBands?: { x1: string; x2: string; label?: string; fill?: string; stroke?: string }[];
  secondaryKeys?: string[];   // 放到右侧副轴的线（如美债/美元等低波动品种，自适应各自量程）
}) {
  if (!data.length || !keys.length) {
    return <EmptyState title="当前区间没有足够数据" />;
  }
  const secondary = new Set(secondaryKeys ?? []);
  const hasSecondary = secondary.size > 0;
  return (
    <div className="chart-shell" style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ left: 0, right: 12, top: 8, bottom: 0 }}>
          <CartesianGrid stroke="rgba(148,163,184,0.14)" vertical={false} />
          <XAxis dataKey="time" tick={{ fill: "#94a3b8", fontSize: 11 }} minTickGap={28} />
          <YAxis yAxisId="left" tick={{ fill: "#94a3b8", fontSize: 11 }} unit={valueFormatter ? "" : unit} width={48} tickFormatter={valueFormatter} domain={yDomain} allowDataOverflow={yDomain != null} />
          {hasSecondary ? (
            <YAxis yAxisId="right" orientation="right" tick={{ fill: "#fb7185", fontSize: 11 }} width={48} tickFormatter={valueFormatter} domain={["auto", "auto"]} />
          ) : null}
          <Tooltip
            contentStyle={{ background: "#0f172a", border: "1px solid #263142", color: "#e2e8f0" }}
            formatter={tooltipFormatter ? (value, name, item) => [
              tooltipFormatter(
                typeof value === "number" ? value : Number(value),
                String(name),
                item?.payload as ChartPoint | undefined
              ),
              String(name),
            ] : undefined}
          />
          <Legend wrapperStyle={{ color: "#cbd5e1", fontSize: 12 }} />
          {baseline != null ? (
            <ReferenceLine yAxisId="left" y={baseline} stroke="rgba(148,163,184,0.5)" strokeDasharray="4 4" />
          ) : null}
          {markers.map((marker, index) => (
            <ReferenceLine
              key={`${marker.time}-${marker.role}-${index}`}
              yAxisId="left"
              x={marker.time}
              stroke="#22c55e"
              strokeWidth={2}
            />
          ))}
          {(shadedBands ?? []).map((b) => (
            <ReferenceArea key={`band-${b.x1}-${b.x2}-${b.fill ?? "d"}`} yAxisId="left" x1={b.x1} x2={b.x2}
              stroke={b.stroke} strokeOpacity={b.stroke ? 1 : 0} fill={b.fill ?? "rgba(148,163,184,0.14)"}
              label={b.label ? { value: b.label, position: "insideTop", fill: "#94a3b8", fontSize: 11 } : undefined} />
          ))}
          {keys.map((key, index) => (
            <Line
              key={key}
              yAxisId={secondary.has(key) ? "right" : "left"}
              dataKey={key}
              type="monotone"
              dot={false}
              stroke={palette[index % palette.length]}
              strokeWidth={key === highlightKey ? 3.4 : 2}
              strokeDasharray={secondary.has(key) ? "5 3" : undefined}
              connectNulls
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
