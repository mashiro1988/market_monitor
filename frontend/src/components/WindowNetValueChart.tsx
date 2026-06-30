import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { NewsItem, PriceWindow } from "../api/types";
import { MultiLineChart } from "./Charts";
import { MultiSelectControl, type MultiOption } from "./Controls";
import { ErrorState, LoadingState } from "./StateViews";
import { buildNetValueChart, computeNetValueDomain, deriveMarkers, shiftUtcIso } from "./windowNetValue";

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
    <section className="panel annotation-block window-netvalue-block">
      <div className="panel-head">
        <h2>窗口净值走势</h2>
        <div className="window-netvalue-head-controls">
          <span className="muted-text small">区间内净值归一为 1.000 · 美债/美元走右副轴(虚线·自适应量程) · 竖线为驱动新闻</span>
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
          />
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
    </section>
  );
}
