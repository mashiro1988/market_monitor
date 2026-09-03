// 行为面板 = 结论页（Phase 2，2026-07-09 用户拍板）：只看结果——日趋势 + 三类构成。
// 证据与动作（段明细/S 曲线/三类标注=人工审核）都在新闻标注页（工作台）。
// 2026-09-03：净差线改读右侧副轴（独立比例尺，两轴对称让零线重合），有涨跌方向的六幅图全部配线；
// 回溯 14 → 30 个北京日。设计稿：docs/superpowers/specs/2026-09-03-behavior-net-line-scale-design.md
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
import { buildDailyRows, symmetricAxis } from "./behaviorFormat";

const SYMBOL = "BTC/USDT";
const DAYS = 30;
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

// 左轴 = 柱（默认轴），右轴 "net" = 净差线。右轴刻度着线色，提示"这把尺子是给线看的"。
const MARGIN = { top: 4, right: 8, left: 0, bottom: 0 };
const MARGIN_NO_NET = { top: 4, right: 48, left: 0, bottom: 0 };   // 无右轴的图补同宽留白，X 轴与其他图对齐
const BAR_AXIS = { width: 34, tick: { fontSize: 12 } };
const NET_AXIS = { yAxisId: "net", orientation: "right" as const, width: 40, tick: { fontSize: 12, fill: TEXT } };
const NET_LINE = { yAxisId: "net", isAnimationActive: false, stroke: TEXT, strokeWidth: 2, dot: false };

export function BehaviorPage() {
  const daily = useQuery({
    queryKey: ["behavior-daily", DAYS],
    queryFn: () => api.behaviorDaily({ symbol: SYMBOL, days: DAYS }),
    refetchInterval: REFRESH_MS,
  });
  const dailyRows = useMemo(() => (daily.data ? buildDailyRows(daily.data) : []), [daily.data]);
  const today = dailyRows[dailyRows.length - 1];
  // 每幅图两把尺子各自对称（[-m, +m]，刻度 ±m/±m/2/0），零线才会重合；柱轴看正负两侧堆叠总高，线轴看净差本身
  const ax = useMemo(() => ({
    count: symmetricAxis(dailyRows.flatMap((r) => [r.up, r.down]), true),
    net: symmetricAxis(dailyRows.map((r) => r.net), true),
    strong: symmetricAxis(dailyRows.flatMap((r) => [r.t05Up + r.t08Up, r.t05Down + r.t08Down]), true),
    strongNet: symmetricAxis(dailyRows.map((r) => r.strongNet), true),
    sum: symmetricAxis(dailyRows.flatMap((r) => [r.upSumStrong + r.upSumWeak, r.downSumStrongNeg + r.downSumWeakNeg])),
    sumNet: symmetricAxis(dailyRows.map((r) => r.sumNet)),
    sent: symmetricAxis(dailyRows.flatMap((r) => [r.sentUp, r.sentDown]), true),
    sentNet: symmetricAxis(dailyRows.map((r) => r.sentNetCount), true),
    sentAmp: symmetricAxis(dailyRows.flatMap((r) => [r.sentUpNet, r.sentDownNet])),
    sentAmpNet: symmetricAxis(dailyRows.map((r) => r.sentNetAmp)),
    ratioNet: symmetricAxis(dailyRows.map((r) => r.sentRatioNet), true),
  }), [dailyRows]);

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
            <div className="panel-head">
              <h2>① 日趋势 · 近 {DAYS} 个北京日（0.3 档只计数）</h2>
              <span className="muted-text small">柱读左轴 · 净差线读右轴（线色刻度）· 两轴零线对齐</span>
            </div>
            <div className="behavior-daily">
              <div className="mini-title">0.3档 涨跌发散柱 + 净差线（涨−跌）</div>
              <ResponsiveContainer width="100%" height={180}>
                <ComposedChart data={dailyRows} stackOffset="sign" margin={MARGIN}>
                  <CartesianGrid strokeDasharray="2 4" vertical={false} />
                  <XAxis dataKey="date" hide />
                  <YAxis {...BAR_AXIS} {...ax.count} />
                  <YAxis {...NET_AXIS} {...ax.net} />
                  <Tooltip contentStyle={TOOLTIP_STYLE} />
                  <ReferenceLine y={0} stroke={INK} />
                  <Bar isAnimationActive={false} dataKey="up" name="涨段" stackId="s" fill={UP} opacity={0.65} />
                  <Bar isAnimationActive={false} dataKey={(r: { down: number }) => -r.down} name="跌段" stackId="s" fill={DOWN} opacity={0.65} />
                  <Line {...NET_LINE} dataKey="net" name="净差" />
                </ComposedChart>
              </ResponsiveContainer>
              <div className="mini-title">强度 · 0.5/0.8 档段数（上=涨 下=跌 · 亮色=0.8档）+ 强段净差线</div>
              <ResponsiveContainer width="100%" height={160}>
                <ComposedChart data={dailyRows} stackOffset="sign" margin={MARGIN}>
                  <CartesianGrid strokeDasharray="2 4" vertical={false} />
                  <XAxis dataKey="date" hide />
                  <YAxis {...BAR_AXIS} {...ax.strong} />
                  <YAxis {...NET_AXIS} {...ax.strongNet} />
                  <Tooltip contentStyle={TOOLTIP_STYLE} />
                  <ReferenceLine y={0} stroke={INK} />
                  <Bar isAnimationActive={false} dataKey="t05Up" name="0.5档涨" stackId="t" fill="#b48a3c" />
                  <Bar isAnimationActive={false} dataKey="t08Up" name="0.8档涨" stackId="t" fill="#fbbf24" />
                  <Bar isAnimationActive={false} dataKey={(r: { t05Down: number }) => -r.t05Down} name="0.5档跌" stackId="t" fill="#b48a3c" opacity={0.55} />
                  <Bar isAnimationActive={false} dataKey={(r: { t08Down: number }) => -r.t08Down} name="0.8档跌" stackId="t" fill="#fbbf24" opacity={0.55} />
                  <Line {...NET_LINE} dataKey="strongNet" name="强段净差" />
                </ComposedChart>
              </ResponsiveContainer>
              <div className="mini-title">涨/跌段净幅合计（%）· 亮=强段(0.5档+) 暗=弱段(0.3档) + 净幅差线</div>
              <ResponsiveContainer width="100%" height={160}>
                <ComposedChart data={dailyRows} stackOffset="sign" margin={MARGIN}>
                  <CartesianGrid strokeDasharray="2 4" vertical={false} />
                  <XAxis dataKey="date" tick={{ fontSize: 12 }} />
                  <YAxis {...BAR_AXIS} {...ax.sum} />
                  <YAxis {...NET_AXIS} {...ax.sumNet} />
                  <Tooltip contentStyle={TOOLTIP_STYLE} />
                  <ReferenceLine y={0} stroke={INK} />
                  <Bar isAnimationActive={false} dataKey="upSumStrong" name="涨·强段Σ" stackId="n" fill={UP} />
                  <Bar isAnimationActive={false} dataKey="upSumWeak" name="涨·弱段Σ" stackId="n" fill={UP_DIM} />
                  <Bar isAnimationActive={false} dataKey="downSumStrongNeg" name="跌·强段Σ" stackId="n" fill={DOWN} />
                  <Bar isAnimationActive={false} dataKey="downSumWeakNeg" name="跌·弱段Σ" stackId="n" fill={DOWN_DIM} />
                  <Line {...NET_LINE} dataKey="sumNet" name="净幅差" />
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
            <div className="mini-title">{DAYS} 日构成堆叠（三类计数无涨跌方向，不设净差线）</div>
            <ResponsiveContainer width="100%" height={220}>
              <ComposedChart data={dailyRows} margin={MARGIN_NO_NET}>
                <CartesianGrid strokeDasharray="2 4" vertical={false} />
                <XAxis dataKey="date" hide />
                <YAxis {...BAR_AXIS} allowDecimals={false} />
                <Tooltip contentStyle={TOOLTIP_STYLE} />
                <Bar isAnimationActive={false} dataKey="nd" name="新闻驱动" stackId="c" fill={C_ND} opacity={0.85} />
                <Bar isAnimationActive={false} dataKey="pr" name="纯共振" stackId="c" fill={C_PR} opacity={0.85} />
                <Bar isAnimationActive={false} dataKey="st" name="情绪·技术面" stackId="c" fill={C_ST} opacity={0.85} />
              </ComposedChart>
            </ResponsiveContainer>
            <div className="mini-title">情绪·技术面 涨/跌段数 + 个数差线</div>
            <ResponsiveContainer width="100%" height={160}>
              <ComposedChart data={dailyRows} stackOffset="sign" margin={MARGIN}>
                <CartesianGrid strokeDasharray="2 4" vertical={false} />
                <XAxis dataKey="date" hide />
                <YAxis {...BAR_AXIS} {...ax.sent} />
                <YAxis {...NET_AXIS} {...ax.sentNet} />
                <Tooltip contentStyle={TOOLTIP_STYLE} />
                <ReferenceLine y={0} stroke={INK} />
                <Bar isAnimationActive={false} dataKey="sentUp" name="情绪涨段" stackId="sc" fill={UP} opacity={0.65} />
                <Bar isAnimationActive={false} dataKey={(r: { sentDown: number }) => -r.sentDown} name="情绪跌段" stackId="sc" fill={DOWN} opacity={0.65} />
                <Line {...NET_LINE} dataKey="sentNetCount" name="个数差" />
              </ComposedChart>
            </ResponsiveContainer>
            <div className="mini-title">情绪·技术面 涨/跌净幅Σ（%）+ 净幅差线</div>
            <ResponsiveContainer width="100%" height={160}>
              <ComposedChart data={dailyRows} stackOffset="sign" margin={MARGIN}>
                <CartesianGrid strokeDasharray="2 4" vertical={false} />
                <XAxis dataKey="date" hide />
                <YAxis {...BAR_AXIS} {...ax.sentAmp} />
                <YAxis {...NET_AXIS} {...ax.sentAmpNet} />
                <Tooltip contentStyle={TOOLTIP_STYLE} />
                <ReferenceLine y={0} stroke={INK} />
                <Bar isAnimationActive={false} dataKey="sentUpNet" name="情绪涨净幅Σ" stackId="sn" fill={UP} opacity={0.65} />
                <Bar isAnimationActive={false} dataKey="sentDownNet" name="情绪跌净幅Σ" stackId="sn" fill={DOWN} opacity={0.65} />
                <Line {...NET_LINE} dataKey="sentNetAmp" name="净幅差" />
              </ComposedChart>
            </ResponsiveContainer>
            <div className="mini-title">情绪·技术面 占比 %（上=涨 下=跌 · 分母&lt;5 空）+ 占比差线</div>
            <ResponsiveContainer width="100%" height={160}>
              <ComposedChart data={dailyRows} stackOffset="sign" margin={MARGIN}>
                <CartesianGrid strokeDasharray="2 4" vertical={false} />
                <XAxis dataKey="date" tick={{ fontSize: 12 }} />
                <YAxis {...BAR_AXIS} domain={[-100, 100]} ticks={[-50, 0, 50]} />
                <YAxis {...NET_AXIS} {...ax.ratioNet} />
                <Tooltip contentStyle={TOOLTIP_STYLE} />
                <ReferenceLine y={0} stroke={INK} />
                <Bar isAnimationActive={false} dataKey="sentUpRatio" name="情绪涨占比%" stackId="sr" fill={UP} opacity={0.65} />
                <Bar isAnimationActive={false} dataKey={(r: { sentDownRatio: number | null }) => r.sentDownRatio == null ? null : -r.sentDownRatio} name="情绪跌占比%" stackId="sr" fill={DOWN} opacity={0.65} />
                <Line {...NET_LINE} dataKey="sentRatioNet" name="占比差" />
              </ComposedChart>
            </ResponsiveContainer>
            <p className="muted-text small">情绪·技术面向下段（个数/占比/净幅）持续抬升 → 崩盘风险关注。</p>
          </section>
        </>
      )}
    </div>
  );
}
