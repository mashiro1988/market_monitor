import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { LineChart, ReferenceArea, ReferenceLine, ResponsiveContainer, XAxis, YAxis } from "recharts";
import { api } from "../api/client";
import type { NewsItem, PriceWindow } from "../api/types";
import { MultiLineChart } from "./Charts";
import { MultiSelectControl, type MultiOption } from "./Controls";
import { ErrorState, LoadingState } from "./StateViews";
import { buildNetValueChart, computeNetValueDomain, deriveMarkers, deriveSegmentBands, laneFill, shiftUtcIso } from "./windowNetValue";
import type { SegmentBand, SegmentBandInput } from "./windowNetValue";

// 默认篮子（含美债10Y/美元指数——低波动，走右副轴）；独立持久化，与 MarketPage 互不影响。
const DEFAULT_BASKET = ["YM=F", "NQ=F", "000001.SS", "^N225", "^KS11", "GC=F", "CL=F", "BTC/USDT", "US_10Y", "DX-Y.NYB"];
const BASKET_STORAGE_KEY = "annotation-chart-symbols";
// 这些品种波动比 BTC/股指小一个量级，放净值左轴会被压成平线 → 走右侧自适应副轴（虚线）。
const SECONDARY_AXIS_SYMBOLS = new Set(["US_10Y", "US_2Y", "JP_10Y", "JP_2Y", "DX-Y.NYB"]);

function loadBasket(): string[] {
  if (typeof window === "undefined") return DEFAULT_BASKET;
  try {
    const raw = window.localStorage.getItem(BASKET_STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed) && parsed.every((x) => typeof x === "string")) return parsed;
    }
  } catch {
    // ignore parse errors
  }
  return DEFAULT_BASKET;
}

function persistBasket(symbols: string[]) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(BASKET_STORAGE_KEY, JSON.stringify(symbols));
  } catch {
    // ignore quota / privacy-mode errors
  }
}

// 段档位轨道：主图正下方的窄条，同一批行为段的实色版。
// 双通道编码档位（2026-07-10 用户反馈"深浅仍不够显著"后加高度）：
//   高度 = 档位（0.3 档 1/3 高、0.5 档 2/3、0.8 档满格，像步进表），深浅同步递进；
// 换挡点画背景色分隔线，段内 0.3→0.5→0.8 的演进一眼可辨。
function SegmentTierLane({
  data,
  bands,
  hasSecondary,
}: {
  data: { time: string }[];
  bands: SegmentBand[];
  hasSecondary: boolean;
}) {
  if (!bands.length) return null;
  // 相邻 run 共享边界桶（演进切分特征）→ 在边界画一根背景色细线标"换挡点"
  const shifts = bands
    .filter((b, i) => i > 0 && bands[i - 1].x2 === b.x1 && bands[i - 1].dir === b.dir)
    .map((b) => b.x1);
  return (
    <div className="tier-lane" title="段档位轨道：颜色=方向（青涨/玫红跌），高度+深浅=档位（0.3/0.5/0.8 步进），竖缝=换挡点">
      <ResponsiveContainer width="100%" height={26}>
        <LineChart data={data} margin={{ left: 0, right: 12, top: 2, bottom: 2 }}>
          <XAxis dataKey="time" hide />
          <YAxis yAxisId="left" domain={[0, 1]} width={48} tick={false} axisLine={false} tickLine={false} />
          {hasSecondary ? (
            <YAxis yAxisId="right" orientation="right" domain={[0, 1]} width={48} tick={false} axisLine={false} tickLine={false} />
          ) : null}
          {bands.map((b) => (
            <ReferenceArea key={`lane-${b.x1}-${b.x2}-${b.fill}`} yAxisId="left" x1={b.x1} x2={b.x2}
              y1={0} y2={(b.tier + 1) / 3} strokeOpacity={0} fill={laneFill(b)} />
          ))}
          {shifts.map((x, i) => (
            <ReferenceLine key={`shift-${x}-${i}`} yAxisId="left" x={x} stroke="#090d12" strokeWidth={2} />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

export function WindowNetValueChart({
  activeWindow,
  preMinutes,
  postMinutes,
  candidateNews,
  newsRoles,
  segments = []
}: {
  activeWindow: PriceWindow;
  preMinutes: number;
  postMinutes: number;
  candidateNews: NewsItem[];
  newsRoles: Record<number, string>;
  segments?: SegmentBandInput[];   // 行为段（含 0.3 簇拥）→ 档位色带
}) {
  const [basket, setBasketState] = useState<string[]>(loadBasket);
  const setBasket = (next: string[]) => {
    setBasketState(next);
    persistBasket(next);
  };

  const startRaw = activeWindow.window_start.timestamp_utc;
  const endRaw = activeWindow.window_end.timestamp_utc;
  const startUtc = startRaw ? shiftUtcIso(startRaw, -preMinutes) : null;
  const endUtc = endRaw ? shiftUtcIso(endRaw, postMinutes) : null;

  // 本标的强制纳入并去重（即使不在篮子里）。
  const fetchSymbols = useMemo(
    () => Array.from(new Set([activeWindow.symbol, ...basket])),
    [activeWindow.symbol, basket]
  );

  const symbolsList = useQuery({ queryKey: ["market-symbols"], queryFn: () => api.marketSymbols() });

  const history = useQuery({
    queryKey: ["annotation-netvalue", startUtc, endUtc, fetchSymbols.join(",")],
    queryFn: () => api.marketHistory({ symbols: fetchSymbols, start_utc: startUtc!, end_utc: endUtc! }),
    enabled: Boolean(startUtc && endUtc)
  });

  const { data, keys, buckets, highlightKey } = useMemo(
    () => buildNetValueChart(history.data, activeWindow.symbol),
    [history.data, activeWindow.symbol]
  );

  const markers = useMemo(
    () => deriveMarkers(candidateNews, newsRoles, buckets),
    [candidateNews, newsRoles, buckets]
  );

  // 标注品种净值序列驱动段内档位演进切分（0.3→0.5→0.8 触及时点逐档加深）
  const segmentBands = useMemo(() => {
    const closes = highlightKey
      ? data.map((row) => (typeof row[highlightKey] === "number" ? (row[highlightKey] as number) : null))
      : undefined;
    return deriveSegmentBands(segments, buckets, closes);
  }, [segments, buckets, data, highlightKey]);

  // 美债/美元等低波动品种放右副轴（自适应各自量程，否则被 BTC/股指压成平线）。
  const secondaryKeys = useMemo(
    () => (history.data?.series ?? [])
      .filter((s) => SECONDARY_AXIS_SYMBOLS.has(s.symbol))
      .map((s) => `${s.name} (${s.symbol})`),
    [history.data]
  );
  // 左轴净值聚集在 1.0 附近，按左轴线的实际范围拟合（排除右轴品种，否则量程被带偏）。
  const yDomain = useMemo(
    () => computeNetValueDomain(data, keys.filter((k) => !secondaryKeys.includes(k))),
    [data, keys, secondaryKeys]
  );

  const symbolOptions: MultiOption[] = useMemo(() => {
    const items = symbolsList.data ?? [];
    return items.map((s) => ({ label: `${s.name} (${s.symbol})`, value: s.symbol, group: s.asset_class }));
  }, [symbolsList.data]);

  return (
    <div className="subsection window-netvalue-block">
      <div className="subsection-head">
        <span className="subsection-title">窗口净值走势</span>
        <div className="window-netvalue-head-controls">
          <span className="muted-text small">净值归一 1.000 · 美债/美元右副轴(虚线) · 竖线=驱动新闻 · 底部轨道=段内档位演进(0.3→0.5→0.8 加深)</span>
          <MultiSelectControl label="对照品种" values={basket} onChange={setBasket} options={symbolOptions} />
        </div>
      </div>

      {history.isLoading ? (
        <LoadingState />
      ) : history.error ? (
        <ErrorState error={history.error} />
      ) : (
        <>
          <MultiLineChart
            data={data}
            keys={keys}
            unit=""
            baseline={1}
            valueFormatter={(v) => v.toFixed(3)}
            yDomain={yDomain}
            markers={markers}
            highlightKey={highlightKey ?? undefined}
            secondaryKeys={secondaryKeys}
            shadedBands={segmentBands}
          />
          <SegmentTierLane data={data} bands={segmentBands} hasSecondary={secondaryKeys.length > 0} />
          {markers.length ? (
            <ul className="netvalue-marker-list">
              {markers.map((marker, index) => (
                <li key={`${marker.time}-${index}`} className={`netvalue-marker netvalue-marker-${marker.role}`}>
                  <span className="netvalue-marker-role">驱动</span>
                  <span className="netvalue-marker-time">{marker.time}</span>
                  <span className="netvalue-marker-title">{marker.title}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted-text small netvalue-marker-empty">
              尚未选出驱动新闻（在右侧候选新闻里勾选角色后会在此标注）
            </p>
          )}
        </>
      )}
    </div>
  );
}
