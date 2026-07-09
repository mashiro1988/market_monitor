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
import type { BehaviorSegmentSchema } from "../api/types";
import { PageHeader } from "../components/Controls";
import { EmptyState, ErrorState, LoadingState } from "../components/StateViews";
import {
  buildDailyRows,
  buildLinkageFrames,
  classMeta,
  fmtS,
  medianOf,
  parseUtc,
  stripBlocks,
  tierName,
} from "./behaviorFormat";

const SYMBOL = "BTC/USDT";
const REFRESH_MS = 5 * 60_000;
const UP = "#5eead4";      // 站内 --up
const DOWN = "#fb7185";    // 站内 --down
const INK = "#8ea0b6";     // 站内 --muted（次级线/网格）
const TEXT = "#dbe7f3";    // 站内 --text（主线）
const REF_COLORS: Record<string, string> = {   // dark 主题组，过 CVD 六项校验
  "NQ=F": "#5E86E0",
  "^N225": "#4F9CCB",
  "GC=F": "#C89B3C",
  "US_2Y": "#93691A",
  "DX-Y.NYB": "#9873CC",
  "CL=F": "#2AA38F",
};
const TOOLTIP_STYLE = { background: "#0f172a", border: "1px solid #263142", color: "#e2e8f0" };

function bjShort(ts: string | null | undefined): string {
  return (ts ?? "").slice(5, 16);
}

function SegRow({ seg }: { seg: BehaviorSegmentSchema }) {
  const meta = classMeta(seg.classification);
  const scores = Object.entries(seg.s_scores).sort((a, b) => Math.abs(b[1].s) - Math.abs(a[1].s));
  const maxSym = scores[0]?.[0];
  const ess = scores[0]?.[1]?.ess ?? null;
  return (
    <tr>
      <td className="mono">{bjShort(seg.start.timestamp_bj)} ~ {(seg.end.timestamp_bj ?? "").slice(11, 16)}</td>
      <td><span className={`tier-chip t${seg.tier_idx}`}>{tierName(seg.tier_idx)}</span></td>
      <td className={seg.direction > 0 ? "ret-up" : "ret-down"}>
        {seg.direction > 0 ? "↑" : "↓"} {seg.net_pct > 0 ? "+" : ""}{seg.net_pct.toFixed(2)}%
      </td>
      <td>
        {scores.length === 0 ? <span className="muted-text">全参照无分（无对照）</span> : scores.slice(0, 3).map(([sym, v]) => (
          <span key={sym} className={`schip ${sym === maxSym ? "max" : ""}`}>
            {sym} <b>{fmtS(v.s)}</b>
          </span>
        ))}
      </td>
      <td className="mono">
        {ess === null ? "—" : ess.toFixed(1)}
        {ess !== null && ess < 5 ? <span className="thin-flag">证据薄</span> : null}
      </td>
      <td>{seg.news.length ? `${seg.news[0].title}${seg.news.length > 1 ? ` (+${seg.news.length - 1})` : ""}` : <span className="muted-text">无</span>}</td>
      <td><span className={`klass ${meta.cls}`}>{meta.label}</span></td>
    </tr>
  );
}

export function BehaviorPage() {
  const daily = useQuery({ queryKey: ["behavior-daily"], queryFn: () => api.behaviorDaily({ symbol: SYMBOL, days: 14 }), refetchInterval: REFRESH_MS });
  const segs = useQuery({ queryKey: ["behavior-segments"], queryFn: () => api.behaviorSegments({ symbol: SYMBOL, days: 2 }), refetchInterval: REFRESH_MS });
  const linkage = useQuery({ queryKey: ["behavior-linkage"], queryFn: () => api.behaviorLinkage({ symbol: SYMBOL, hours: 48 }), refetchInterval: REFRESH_MS });

  const dailyRows = useMemo(() => (daily.data ? buildDailyRows(daily.data) : []), [daily.data]);
  const link = useMemo(() => (linkage.data ? buildLinkageFrames(linkage.data) : { frames: [], symbols: [] }), [linkage.data]);
  const centre = useMemo(
    () => medianOf(link.frames.map((f) => f.maxAbs).filter((v): v is number => typeof v === "number")),
    [link.frames],
  );
  const allSegs = segs.data?.segments ?? [];
  const composedSegs = allSegs.filter((s) => s.tier_idx >= 1);
  const countOnly = allSegs.length - composedSegs.length;
  const strip = useMemo(() => {
    const first = linkage.data?.breadth[0]?.t.timestamp_utc;
    const last = linkage.data?.breadth[linkage.data.breadth.length - 1]?.t.timestamp_utc;
    const s = parseUtc(first);
    const e = parseUtc(last);
    return s !== null && e !== null ? stripBlocks(allSegs, s, e) : [];
  }, [linkage.data, allSegs]);

  const today = dailyRows[dailyRows.length - 1];

  return (
    <div className="page behavior-page">
      <PageHeader
        title="行为面板"
        subtitle="判断 BTC 行情由谁驱动：技术面（情绪/庄家）· 行业事件 · 宏观新闻 · 纯共振 —— 段是唯一单位，S 是联动证据"
      />

      {/* ① 日趋势 */}
      <section className="panel">
        <div className="panel-head"><h2>① 日趋势 · 近 14 个 UTC 日（周末底纹分桶；0.3 档只计数）</h2></div>
        {daily.isLoading ? <LoadingState /> : daily.error ? <ErrorState error={daily.error} /> : (
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
            <div className="mini-title">情绪候选向下段（红） vs 构成段总数（灰虚，分母&lt;5 不读占比）</div>
            <ResponsiveContainer width="100%" height={80}>
              <LineChart data={dailyRows} margin={{ top: 4, right: 60, left: 0, bottom: 0 }}>
                <XAxis dataKey="date" hide />
                <YAxis width={34} tick={{ fontSize: 10 }} allowDecimals={false} />
                <Tooltip contentStyle={TOOLTIP_STYLE} />
                <Line dataKey="comp" name="构成段" stroke={INK} strokeDasharray="4 4" strokeWidth={1.5} dot={false} />
                <Line dataKey="sent" name="情绪候选" stroke="#fb7185" strokeWidth={2} dot={false} />
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
        )}
      </section>

      {/* ② 时间轴 */}
      <section className="panel">
        <div className="panel-head">
          <h2>② 时间轴 · 近 48h — 段带 / max|S| 主曲线（虚线=48h 中位中枢） / 分资产 S 小图 / 同步参照数</h2>
        </div>
        {linkage.isLoading ? <LoadingState /> : linkage.error ? <ErrorState error={linkage.error} /> : link.frames.length === 0 ? <EmptyState title="暂无联动数据" /> : (
          <div>
            <div className="behavior-strip" title="段带：绿涨红跌，深浅=档位">
              {strip.map((b, i) => (
                <span key={i}
                  className={`strip-block ${b.up ? "up" : "down"}`}
                  style={{ left: `${b.leftPct}%`, width: `${b.widthPct}%`, opacity: 0.3 + 0.3 * b.tierIdx }} />
              ))}
            </div>
            <div className="mini-title">联动强度 max|S|（判级：≥0.5 共振 · 0.3–0.5 弱 · &lt;0.3 独立）</div>
            <ResponsiveContainer width="100%" height={130}>
              <LineChart data={link.frames} syncId="linkage" margin={{ top: 4, right: 60, left: 0, bottom: 0 }}>
                <XAxis dataKey="t" hide />
                <YAxis width={34} domain={[0, 1]} tick={{ fontSize: 10 }} />
                <Tooltip contentStyle={TOOLTIP_STYLE} />
                <ReferenceLine y={0.5} strokeDasharray="4 3" stroke={INK} />
                <ReferenceLine y={0.3} strokeDasharray="2 3" stroke={INK} />
                {centre !== null ? <ReferenceLine y={centre} stroke="#6e97e8" strokeDasharray="6 4" label={{ value: `中枢 ${centre.toFixed(2)}`, fontSize: 10, position: "right" }} /> : null}
                <Line dataKey="maxAbs" name="max|S|" stroke={TEXT} strokeWidth={2} dot={false} connectNulls={false} />
              </LineChart>
            </ResponsiveContainer>
            {link.symbols.map(({ symbol, label }) => (
              <ResponsiveContainer key={symbol} width="100%" height={56}>
                <LineChart data={link.frames} syncId="linkage" margin={{ top: 2, right: 60, left: 0, bottom: 0 }}>
                  <XAxis dataKey="t" hide />
                  <YAxis width={34} domain={[-1, 1]} ticks={[0]} tick={{ fontSize: 9 }} />
                  <Tooltip contentStyle={TOOLTIP_STYLE} />
                  <ReferenceLine y={0} stroke="#263142" />
                  <Line dataKey={symbol} name={label} stroke={REF_COLORS[symbol] ?? INK} strokeWidth={1.6} dot={false} connectNulls={false} />
                </LineChart>
              </ResponsiveContainer>
            ))}
            <div className="mini-title">同步参照数（|S| ≥ 0.3 的参照个数：区分"跟一两个资产走"与"全市场一起动"）</div>
            <ResponsiveContainer width="100%" height={70}>
              <LineChart data={link.frames} syncId="linkage" margin={{ top: 2, right: 60, left: 0, bottom: 0 }}>
                <XAxis dataKey="t" tick={{ fontSize: 10 }} minTickGap={60} />
                <YAxis width={34} domain={[0, 6]} ticks={[0, 3, 6]} tick={{ fontSize: 10 }} />
                <Tooltip contentStyle={TOOLTIP_STYLE} />
                <Line dataKey="breadth" name="同步参照数" type="stepAfter" stroke={INK} strokeWidth={1.5} dot={false} connectNulls={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </section>

      {/* ③ 段明细 + ④ 侧栏 */}
      <div className="behavior-grid">
        <section className="panel">
          <div className="panel-head">
            <h2>③ 段明细 · 0.5 档以上（近 2 天；另有 {countOnly} 个 0.3 档段只计数）— 未确认段全部留存、随时可审</h2>
          </div>
          {segs.isLoading ? <LoadingState /> : segs.error ? <ErrorState error={segs.error} /> : composedSegs.length === 0 ? <EmptyState title="近 2 天没有 0.5 档以上的段" /> : (
            <div className="table-scroll">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>时间(BJ)</th><th>档位</th><th>净幅</th>
                    <th>共振证据 S（蓝框=最强参照=判级 max|S|）</th>
                    <th>证据厚度 ESS</th><th>新闻命中</th><th>分类</th>
                  </tr>
                </thead>
                <tbody>
                  {composedSegs.map((seg) => <SegRow key={seg.id} seg={seg} />)}
                </tbody>
              </table>
            </div>
          )}
          <p className="muted-text small">
            证据厚度 ESS = 分数由几根 K 线撑起（&lt;5 标"证据薄"：插针也能造出来，先看一眼再信）。
            覆盖不足 50% 的参照不出分（无对照）。S 符号仅展示：美元指数反向为常态。
          </p>
        </section>

        <aside className="behavior-aside">
          <section className="panel">
            <div className="panel-head"><h2>今日构成（UTC 日）</h2></div>
            {today ? (
              <div className="today-comp">
                <div><b className="num-big">{today.up + today.down}</b> 段 · <span className="ret-up">{today.up}↑</span> <span className="ret-down">{today.down}↓</span></div>
                <div>构成段 <b>{today.comp}</b> · 情绪候选↓ <b className="k-sent-text">{today.sent}</b>{today.comp < 5 ? <span className="muted-text small">（分母&lt;5 不读占比）</span> : null}</div>
                <div className="muted-text small">{today.live ? "盘中现算（live）" : "已固化（PIT）"}</div>
              </div>
            ) : <EmptyState title="暂无数据" />}
          </section>
          <section className="panel">
            <div className="panel-head"><h2>读图语法</h2></div>
            <ul className="grammar">
              <li>段 + max|S| 高 → <b>宏观共振</b>（有新闻=新闻驱动）</li>
              <li>段 + max|S| 低 + 无新闻 → <b>情绪独舞候选</b>（人判）</li>
              <li>无段 + max|S| 持续高 → <b>同步阴跌 regime</b></li>
              <li>max|S| 长期贴中枢线下 + 情绪跌段↑ → <b>脱钩预警</b></li>
            </ul>
          </section>
        </aside>
      </div>
    </div>
  );
}
