import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { LineChart, ReferenceArea, ReferenceLine, ResponsiveContainer, XAxis, YAxis } from "recharts";
import { api } from "../api/client";
import type { NewsItem, PriceWindow } from "../api/types";
import { MultiLineChart } from "./Charts";
import { MultiSelectControl, type MultiOption } from "./Controls";
import { ErrorState, LoadingState } from "./StateViews";
import { buildNetValueChart, computeNetValueDomain, deriveMarkers, deriveTierLanes, priceKey, shiftUtcIso } from "./windowNetValue";
import type { TierLaneBand } from "./windowNetValue";

// 默认篮子（含美债10Y/美元指数——低波动，走右副轴）；独立持久化，与 MarketPage 互不影响。
const DEFAULT_BASKET = ["YM=F", "NQ=F", "000001.SS", "NIY=F", "^KS11", "GC=F", "CL=F", "BTC/USDT", "US_10Y", "DX-Y.NYB"];
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

// 三行档位速度带（2026-07-12 用户白板拍板）：0.3 上 / 0.5 中 / 0.8 下，各行独占一条轨道，
// 任一时刻的读数只落在最高触及档那一行——不存在跨档区域，也不受段检测嵌套段影响。
// 与主图复用同款轴宽+边距，SVG 逐像素对齐。
const LANE_LABELS = ["0.3%档", "0.5%档", "0.8%档"];

function TierLaneRow({
  data,
  bands,
  label,
  hasSecondary,
  last,
}: {
  data: { time: string }[];
  bands: TierLaneBand[];
  label: string;
  hasSecondary: boolean;
  last: boolean;
}) {
  return (
    <div className={`tier-lane-row${last ? " last" : ""}`}>
      <span className="tier-lane-label">{label}</span>
      <ResponsiveContainer width="100%" height={16}>
        <LineChart data={data} margin={{ left: 0, right: 12, top: 2, bottom: 2 }}>
          <XAxis dataKey="time" hide />
          <YAxis yAxisId="left" domain={[0, 1]} width={48} tick={false} axisLine={false} tickLine={false} />
          {hasSecondary ? (
            <YAxis yAxisId="right" orientation="right" domain={[0, 1]} width={48} tick={false} axisLine={false} tickLine={false} />
          ) : null}
          {bands.map((b) => (
            <ReferenceArea key={`ln-${b.x1}-${b.x2}-${b.fill}`} yAxisId="left" x1={b.x1} x2={b.x2}
              y1={0} y2={1} strokeOpacity={0} fill={b.fill} />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function TierLanes({
  data,
  lanes,
  hasSecondary,
}: {
  data: { time: string }[];
  lanes: TierLaneBand[][];
  hasSecondary: boolean;
}) {
  if (!lanes.some((l) => l.length)) return null;
  return (
    <div className="tier-lane" title="档位速度带：每 5 分钟的即时 15min 开收净落在最高触及档那一行；颜色=方向（青涨/玫红跌）">
      {lanes.map((bands, tier) => (
        <TierLaneRow key={tier} data={data} bands={bands} label={LANE_LABELS[tier]}
          hasSecondary={hasSecondary} last={tier === lanes.length - 1} />
      ))}
    </div>
  );
}

export function WindowNetValueChart({
  activeWindow,
  preMinutes,
  postMinutes,
  candidateNews,
  newsRoles
}: {
  activeWindow: PriceWindow;
  preMinutes: number;
  postMinutes: number;
  candidateNews: NewsItem[];
  newsRoles: Record<number, string>;
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

  // 2026-07-12 拍板：主图无色带；下方三行速度带由标注品种净值序列逐桶现算
  const tierLanes = useMemo(() => {
    const closes = highlightKey && activeWindow.symbol === "BTC/USDT"
      ? data.map((row) => (typeof row[highlightKey] === "number" ? (row[highlightKey] as number) : null))
      : undefined;
    return deriveTierLanes(buckets, closes);
  }, [buckets, data, highlightKey, activeWindow.symbol]);

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
            tooltipFormatter={(value, seriesKey, row) => {
              // 净值 4 位小数 + 原始价格（≤4 位小数），替代默认的全精度浮点串
              const px = row?.[priceKey(seriesKey)];
              const nv = value.toFixed(4);
              return typeof px === "number"
                ? `${nv} · ${px.toLocaleString(undefined, { maximumFractionDigits: 4 })}`
                : nv;
            }}
            yDomain={yDomain}
            markers={markers}
            highlightKey={highlightKey ?? undefined}
            secondaryKeys={secondaryKeys}
          />
          <TierLanes data={data} lanes={tierLanes} hasSecondary={secondaryKeys.length > 0} />
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
          ) : null}
        </>
      )}
    </div>
  );
}
