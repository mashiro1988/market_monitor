// 行为面板 = 结论页（Phase 2，2026-07-09 用户拍板）：只看结果——日趋势 + 三类构成。
// 证据与动作（段明细/S 曲线/三类标注=人工审核）都在新闻标注页（工作台）。
import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Line,
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
const UP_DIM = "#2f9e88";    // 弱段涨（暗青）
const DOWN_DIM = "#ad4159";  // 弱段跌（暗玫红）
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
        subtitle="新闻驱动 · 纯宏观共振 · 情绪/技术面——证据与标注在「新闻标注」页"
      />

      {daily.isLoading ? <LoadingState /> : daily.error ? <ErrorState error={daily.error} /> : !dailyRows.length ? (
        <EmptyState title="暂无行为数据" />
      ) : (
        <>
          {/* ① 日趋势（保留） */}
          <section className="panel">
            <div className="panel-head"><h2>① 日趋势 · 近 14 个 UTC 日（0.3 档只计数）</h2></div>
            <div className="behavior-daily">
              <div className="mini-title">0.3档 涨跌发散柱 + 净差线（涨−跌）</div>
              <ResponsiveContainer width="100%" height={120}>
                <ComposedChart data={dailyRows} stackOffset="sign" margin={{ top: 4, right: 60, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="2 4" vertical={false} />
                  <XAxis dataKey="date" hide />
                  <YAxis width={34} tick={{ fontSize: 12 }} />
                  <Tooltip contentStyle={TOOLTIP_STYLE} />
                  <ReferenceLine y={0} stroke={INK} />
                  <Bar isAnimationActive={false} dataKey="up" name="涨段" stackId="s" fill={UP} opacity={0.65} />
                  <Bar isAnimationActive={false} dataKey={(r: { down: number }) => -r.down} name="跌段" stackId="s" fill={DOWN} opacity={0.65} />
                  <Line isAnimationActive={false} dataKey="net" name="净差" stroke={TEXT} strokeWidth={2} dot={false} />
                </ComposedChart>
              </ResponsiveContainer>
              <div className="mini-title">强度 · 0.5/0.8 档段数（上=涨 下=跌 · 亮色=0.8档）</div>
              <ResponsiveContainer width="100%" height={100}>
                <ComposedChart data={dailyRows} stackOffset="sign" margin={{ top: 4, right: 60, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="2 4" vertical={false} />
                  <XAxis dataKey="date" hide />
                  <YAxis width={34} tick={{ fontSize: 12 }} allowDecimals={false} />
                  <Tooltip contentStyle={TOOLTIP_STYLE} />
                  <ReferenceLine y={0} stroke={INK} />
                  <Bar isAnimationActive={false} dataKey="t05Up" name="0.5档涨" stackId="t" fill="#b48a3c" />
                  <Bar isAnimationActive={false} dataKey="t08Up" name="0.8档涨" stackId="t" fill="#fbbf24" />
                  <Bar isAnimationActive={false} dataKey={(r: { t05Down: number }) => -r.t05Down} name="0.5档跌" stackId="t" fill="#b48a3c" opacity={0.55} />
                  <Bar isAnimationActive={false} dataKey={(r: { t08Down: number }) => -r.t08Down} name="0.8档跌" stackId="t" fill="#fbbf24" opacity={0.55} />
                </ComposedChart>
              </ResponsiveContainer>
              <div className="mini-title">涨/跌段净幅合计（%）· 亮=强段(0.5档+) 暗=弱段(0.3档)</div>
              <ResponsiveContainer width="100%" height={100}>
                <ComposedChart data={dailyRows} stackOffset="sign" margin={{ top: 4, right: 60, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="2 4" vertical={false} />
                  <XAxis dataKey="date" tick={{ fontSize: 12 }} />
                  <YAxis width={34} tick={{ fontSize: 12 }} />
                  <Tooltip contentStyle={TOOLTIP_STYLE} />
                  <ReferenceLine y={0} stroke={INK} />
                  <Bar isAnimationActive={false} dataKey="upSumStrong" name="涨·强段Σ" stackId="n" fill={UP} />
                  <Bar isAnimationActive={false} dataKey="upSumWeak" name="涨·弱段Σ" stackId="n" fill={UP_DIM} />
                  <Bar isAnimationActive={false} dataKey="downSumStrongNeg" name="跌·强段Σ" stackId="n" fill={DOWN} />
                  <Bar isAnimationActive={false} dataKey="downSumWeakNeg" name="跌·弱段Σ" stackId="n" fill={DOWN_DIM} />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          </section>

          {/* ② 三类构成结论 */}
          <section className="panel">
            <div className="panel-head">
              <h2>② 构成结论 · 三类（0.5 档以上 · 人工优先）</h2>
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
            <div className="mini-title">14 日构成堆叠</div>
            <ResponsiveContainer width="100%" height={150}>
              <ComposedChart data={dailyRows} margin={{ top: 4, right: 60, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="2 4" vertical={false} />
                <XAxis dataKey="date" hide />
                <YAxis width={34} tick={{ fontSize: 12 }} allowDecimals={false} />
                <Tooltip contentStyle={TOOLTIP_STYLE} />
                <Bar isAnimationActive={false} dataKey="nd" name="新闻驱动" stackId="c" fill={C_ND} opacity={0.85} />
                <Bar isAnimationActive={false} dataKey="pr" name="纯共振" stackId="c" fill={C_PR} opacity={0.85} />
                <Bar isAnimationActive={false} dataKey="st" name="情绪·技术面" stackId="c" fill={C_ST} opacity={0.85} />
              </ComposedChart>
            </ResponsiveContainer>
            <div className="mini-title">情绪·技术面 涨/跌段数 + 个数差线</div>
            <ResponsiveContainer width="100%" height={100}>
              <ComposedChart data={dailyRows} stackOffset="sign" margin={{ top: 4, right: 60, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="2 4" vertical={false} />
                <XAxis dataKey="date" hide />
                <YAxis width={34} tick={{ fontSize: 12 }} allowDecimals={false} />
                <Tooltip contentStyle={TOOLTIP_STYLE} />
                <ReferenceLine y={0} stroke={INK} />
                <Bar isAnimationActive={false} dataKey="sentUp" name="情绪涨段" stackId="sc" fill={UP} opacity={0.65} />
                <Bar isAnimationActive={false} dataKey={(r: { sentDown: number }) => -r.sentDown} name="情绪跌段" stackId="sc" fill={DOWN} opacity={0.65} />
                <Line isAnimationActive={false} dataKey="sentNetCount" name="个数差" stroke={TEXT} strokeWidth={2} dot={false} />
              </ComposedChart>
            </ResponsiveContainer>
            <div className="mini-title">情绪·技术面 涨/跌净幅Σ（%）+ 净差线</div>
            <ResponsiveContainer width="100%" height={100}>
              <ComposedChart data={dailyRows} stackOffset="sign" margin={{ top: 4, right: 60, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="2 4" vertical={false} />
                <XAxis dataKey="date" hide />
                <YAxis width={34} tick={{ fontSize: 12 }} />
                <Tooltip contentStyle={TOOLTIP_STYLE} />
                <ReferenceLine y={0} stroke={INK} />
                <Bar isAnimationActive={false} dataKey="sentUpNet" name="情绪涨净幅Σ" stackId="sn" fill={UP} opacity={0.65} />
                <Bar isAnimationActive={false} dataKey="sentDownNet" name="情绪跌净幅Σ" stackId="sn" fill={DOWN} opacity={0.65} />
                <Line isAnimationActive={false} dataKey="sentNetAmp" name="净幅差" stroke={TEXT} strokeWidth={2} dot={false} />
              </ComposedChart>
            </ResponsiveContainer>
            <div className="mini-title">情绪·技术面 占比 %（上=涨 下=跌 · 分母&lt;5 空）</div>
            <ResponsiveContainer width="100%" height={100}>
              <ComposedChart data={dailyRows} stackOffset="sign" margin={{ top: 4, right: 60, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="2 4" vertical={false} />
                <XAxis dataKey="date" tick={{ fontSize: 12 }} />
                <YAxis width={34} domain={[-100, 100]} ticks={[-50, 0, 50]} tick={{ fontSize: 12 }} />
                <Tooltip contentStyle={TOOLTIP_STYLE} />
                <ReferenceLine y={0} stroke={INK} />
                <Bar isAnimationActive={false} dataKey="sentUpRatio" name="情绪涨占比%" stackId="sr" fill={UP} opacity={0.65} />
                <Bar isAnimationActive={false} dataKey={(r: { sentDownRatio: number | null }) => r.sentDownRatio == null ? null : -r.sentDownRatio} name="情绪跌占比%" stackId="sr" fill={DOWN} opacity={0.65} />
              </ComposedChart>
            </ResponsiveContainer>
            <p className="muted-text small">情绪·技术面向下段（个数/占比/净幅）持续抬升 → 崩盘风险关注。</p>
          </section>
        </>
      )}
    </div>
  );
}
