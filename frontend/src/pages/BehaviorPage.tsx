// 行为面板 = 结论页（Phase 2，2026-07-09 用户拍板）：只看结果——日趋势 + 三类构成。
// 证据与动作（段明细/S 曲线/三类标注=人工审核）都在新闻标注页（工作台）。
import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "../api/client";
import { PageHeader } from "../components/Controls";
import { EmptyState, ErrorState, LoadingState } from "../components/StateViews";
import { buildDailyRows } from "./behaviorFormat";

const SYMBOL = "BTC/USDT";
const REFRESH_MS = 5 * 60_000;
const UP = "#5eead4";      // 站内 --up
const DOWN = "#fb7185";    // 站内 --down
const INK = "#8ea0b6";     // 站内 --muted
const TEXT = "#dbe7f3";    // 站内 --text
const C_ND = "#6e97e8";    // 新闻驱动
const C_PR = "#3bb3a0";    // 纯共振
const C_ST = "#fb7185";    // 情绪·技术面
const TOOLTIP_STYLE = { background: "#0f172a", border: "1px solid #263142", color: "#e2e8f0" };

export function BehaviorPage() {
  const daily = useQuery({
    queryKey: ["behavior-daily"],
    queryFn: () => api.behaviorDaily({ symbol: SYMBOL, days: 14 }),
    refetchInterval: REFRESH_MS,
  });
  const dailyRows = useMemo(() => (daily.data ? buildDailyRows(daily.data) : []), [daily.data]);
  const today = dailyRows[dailyRows.length - 1];

  return (
    <div className="page behavior-page">
      <PageHeader
        title="行为面板 · 结论"
        subtitle="BTC 行情由谁驱动：新闻驱动 · 纯宏观共振 · 情绪/技术面 —— 证据与标注动作在「新闻标注」页"
      />

      {daily.isLoading ? <LoadingState /> : daily.error ? <ErrorState error={daily.error} /> : !dailyRows.length ? (
        <EmptyState title="暂无行为数据" />
      ) : (
        <>
          {/* ① 日趋势（保留） */}
          <section className="panel">
            <div className="panel-head"><h2>① 日趋势 · 近 14 个 UTC 日（0.3 档只计数；周末分桶互比）</h2></div>
            <div className="behavior-daily">
              <div className="mini-title">0.3档 涨跌发散柱 + 净差线（涨−跌，趋势主读数）</div>
              <ResponsiveContainer width="100%" height={120}>
                <ComposedChart data={dailyRows} stackOffset="sign" margin={{ top: 4, right: 60, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="2 4" vertical={false} />
                  <XAxis dataKey="date" hide />
                  <YAxis width={34} tick={{ fontSize: 10 }} />
                  <Tooltip contentStyle={TOOLTIP_STYLE} />
                  <ReferenceLine y={0} stroke={INK} />
                  <Bar dataKey="up" name="涨段" stackId="s" fill={UP} opacity={0.65} />
                  <Bar dataKey={(r: { down: number }) => -r.down} name="跌段" stackId="s" fill={DOWN} opacity={0.65} />
                  <Line dataKey="net" name="净差" stroke={TEXT} strokeWidth={2} dot={false} />
                </ComposedChart>
              </ResponsiveContainer>
              <div className="mini-title">强度 · 触及 0.5 / 0.8 档段数（档位右移 = 情绪变猛）</div>
              <ResponsiveContainer width="100%" height={80}>
                <LineChart data={dailyRows} margin={{ top: 4, right: 60, left: 0, bottom: 0 }}>
                  <XAxis dataKey="date" hide />
                  <YAxis width={34} tick={{ fontSize: 10 }} allowDecimals={false} />
                  <Tooltip contentStyle={TOOLTIP_STYLE} />
                  <Line dataKey="t05" name="0.5档" stroke="#b48a3c" strokeWidth={1.8} dot={false} />
                  <Line dataKey="t08" name="0.8档" stroke="#fbbf24" strokeWidth={1.8} dot={false} />
                </LineChart>
              </ResponsiveContainer>
              <div className="mini-title">跌段净幅合计</div>
              <ResponsiveContainer width="100%" height={90}>
                <ComposedChart data={dailyRows} margin={{ top: 4, right: 60, left: 0, bottom: 0 }}>
                  <XAxis dataKey="date" tick={{ fontSize: 10 }} />
                  <YAxis width={34} tick={{ fontSize: 10 }} />
                  <Tooltip contentStyle={TOOLTIP_STYLE} />
                  <Bar dataKey="downSumNeg" name="跌段净幅Σ" fill={DOWN} opacity={0.75} radius={[2, 2, 0, 0]} />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          </section>

          {/* ② 三类构成结论 */}
          <section className="panel">
            <div className="panel-head">
              <h2>② 构成结论 · 三类（人工结论优先；构成段 = 0.5 档以上）</h2>
            </div>
            {today ? (
              <div className="today-comp-row">
                <span className="klass k-macro">新闻驱动 <b className="num-big">{today.nd}</b></span>
                <span className="klass k-reso">纯共振 <b className="num-big">{today.pr}</b></span>
                <span className="klass k-sent">情绪·技术面 <b className="num-big">{today.st}</b></span>
                <span className="muted-text">今日构成段 {today.comp}{today.noRef ? ` · 无对照注记 ${today.noRef}` : ""}{today.comp < 5 ? "（分母<5 不读占比）" : today.sentRatio != null ? ` · 情绪占比 ${today.sentRatio}%` : ""}</span>
                <span className="muted-text small">{today.live ? "盘中现算" : "已固化(PIT)"}</span>
              </div>
            ) : null}
            <div className="mini-title">14 日构成堆叠（新闻驱动 / 纯共振 / 情绪·技术面）</div>
            <ResponsiveContainer width="100%" height={150}>
              <ComposedChart data={dailyRows} margin={{ top: 4, right: 60, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="2 4" vertical={false} />
                <XAxis dataKey="date" hide />
                <YAxis width={34} tick={{ fontSize: 10 }} allowDecimals={false} />
                <Tooltip contentStyle={TOOLTIP_STYLE} />
                <Bar dataKey="nd" name="新闻驱动" stackId="c" fill={C_ND} opacity={0.85} />
                <Bar dataKey="pr" name="纯共振" stackId="c" fill={C_PR} opacity={0.85} />
                <Bar dataKey="st" name="情绪·技术面" stackId="c" fill={C_ST} opacity={0.85} />
              </ComposedChart>
            </ResponsiveContainer>
            <div className="mini-title">情绪·技术面占比趋势（%；分母&lt;5 的日子断线不读）</div>
            <ResponsiveContainer width="100%" height={90}>
              <LineChart data={dailyRows} margin={{ top: 4, right: 60, left: 0, bottom: 0 }}>
                <XAxis dataKey="date" tick={{ fontSize: 10 }} />
                <YAxis width={34} domain={[0, 100]} ticks={[0, 50, 100]} tick={{ fontSize: 10 }} />
                <Tooltip contentStyle={TOOLTIP_STYLE} />
                <Line dataKey="sentRatio" name="情绪占比%" stroke={C_ST} strokeWidth={2} dot={false} connectNulls={false} />
              </LineChart>
            </ResponsiveContainer>
            <p className="muted-text small">
              读法：情绪·技术面向下段的数量与占比持续抬升 → 崩盘风险关注（参照 2026 年初、2026-06 无新闻崩盘）。
              段级证据、rolling S 曲线与三类标注（人工审核）在「新闻标注」页。
            </p>
          </section>
        </>
      )}
    </div>
  );
}
