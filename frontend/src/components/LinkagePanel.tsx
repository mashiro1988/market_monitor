// rolling S 联动曲线组（Phase 2：从行为面板迁到标注页——相关性是标注的辅助证据）。
// 纯展示层：max|S| 主曲线（含中位中枢线）+ 分资产小图 + 同步参照数；不触发、不分类、不告警。
import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Line,
  LineChart,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "../api/client";
import { EmptyState, ErrorState, LoadingState } from "./StateViews";
import { buildLinkageFrames } from "../pages/behaviorFormat";

const INK = "#8ea0b6";
const TEXT = "#dbe7f3";
export const REF_COLORS: Record<string, string> = {
  "NQ=F": "#5E86E0",
  "^N225": "#4F9CCB",
  "GC=F": "#C89B3C",
  "US_2Y": "#93691A",
  "DX-Y.NYB": "#9873CC",
  "CL=F": "#2AA38F",
};
const TOOLTIP_STYLE = { background: "#0f172a", border: "1px solid #263142", color: "#e2e8f0" };

export function LinkagePanel({
  symbol,
  hours = 48,
  range,
  highlight,
  refreshMs = 5 * 60_000,
}: {
  symbol: string;
  hours?: number;
  range?: { startUtc: string; endUtc: string } | null;   // 跟随标注窗口（±24h）；不传 = 贴最新回看 hours
  highlight?: { x1: string; x2: string } | null;   // 选中窗口区间（bj MM-DD HH:mm）
  refreshMs?: number;
}) {
  const linkage = useQuery({
    queryKey: ["behavior-linkage", symbol, hours, range?.startUtc ?? null, range?.endUtc ?? null],
    queryFn: () => api.behaviorLinkage(range
      ? { symbol, hours, start_utc: range.startUtc, end_utc: range.endUtc }
      : { symbol, hours }),
    refetchInterval: refreshMs,
  });
  const link = useMemo(
    () => (linkage.data ? buildLinkageFrames(linkage.data) : { frames: [], symbols: [] }),
    [linkage.data],
  );
  if (linkage.isLoading) return <LoadingState />;
  if (linkage.error) return <ErrorState error={linkage.error} />;
  if (!link.frames.length) return <EmptyState title="暂无联动数据" />;

  return (
    <div className="linkage-panel">
      <div className="mini-title">联动强度 max|S|（≥0.5 共振 · 0.3–0.5 弱 · &lt;0.3 独立）</div>
      <ResponsiveContainer width="100%" height={120}>
        <LineChart data={link.frames} syncId={`linkage-${symbol}`} margin={{ top: 4, right: 60, left: 0, bottom: 0 }}>
          <XAxis dataKey="t" hide />
          <YAxis width={34} domain={[0, 1]} tick={{ fontSize: 10 }} />
          <Tooltip contentStyle={TOOLTIP_STYLE} />
          {highlight ? (
            <ReferenceArea x1={highlight.x1} x2={highlight.x2} strokeOpacity={0} fill="rgba(94,234,212,0.10)" />
          ) : null}
          <ReferenceLine y={0.5} strokeDasharray="4 3" stroke={INK} />
          <ReferenceLine y={0.3} strokeDasharray="2 3" stroke={INK} />
          <Line dataKey="maxAbs" name="max|S|" stroke={TEXT} strokeWidth={2} dot={false} connectNulls={false} />
        </LineChart>
      </ResponsiveContainer>
      {link.symbols.map(({ symbol: refSym, label }) => (
        <ResponsiveContainer key={refSym} width="100%" height={52}>
          <LineChart data={link.frames} syncId={`linkage-${symbol}`} margin={{ top: 2, right: 60, left: 0, bottom: 0 }}>
            <XAxis dataKey="t" hide />
            <YAxis width={34} domain={[-1, 1]} ticks={[0]} tick={{ fontSize: 9 }} />
            <Tooltip contentStyle={TOOLTIP_STYLE} />
            {highlight ? (
              <ReferenceArea x1={highlight.x1} x2={highlight.x2} strokeOpacity={0} fill="rgba(94,234,212,0.08)" />
            ) : null}
            <ReferenceLine y={0} stroke="#263142" />
            <Line dataKey={refSym} name={label} stroke={REF_COLORS[refSym] ?? INK} strokeWidth={1.6} dot={false} connectNulls={false} />
          </LineChart>
        </ResponsiveContainer>
      ))}
      <div className="mini-title">同步参照数（|S|≥0.3 的参照个数）</div>
      <ResponsiveContainer width="100%" height={66}>
        <LineChart data={link.frames} syncId={`linkage-${symbol}`} margin={{ top: 2, right: 60, left: 0, bottom: 0 }}>
          <XAxis dataKey="t" tick={{ fontSize: 10 }} minTickGap={60} />
          <YAxis width={34} domain={[0, 6]} ticks={[0, 3, 6]} tick={{ fontSize: 10 }} />
          <Tooltip contentStyle={TOOLTIP_STYLE} />
          {highlight ? (
            <ReferenceArea x1={highlight.x1} x2={highlight.x2} strokeOpacity={0} fill="rgba(94,234,212,0.08)" />
          ) : null}
          <Line dataKey="breadth" name="同步参照数" type="stepAfter" stroke={INK} strokeWidth={1.5} dot={false} connectNulls={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
