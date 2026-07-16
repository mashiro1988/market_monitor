import { describe, it, expect } from "vitest";
import { buildOverviewCards, deriveShadedBands } from "./MarketPage";

describe("deriveShadedBands", () => {
  it("derives a band from contiguous gapfill points using existing time strings", () => {
    const history = {
      symbols: ["NQ=F"],
      start: {} as any, end: {} as any,
      series: [{ symbol: "NQ=F", name: "纳指", asset_class: "futures", points: [
        { timestamp_bj: "2026-06-27 04:00:00", timestamp_utc: "2026-06-26 20:00:00", symbol: "NQ=F", name: "纳指", price: 1, normalized_pct: 0, source: "yfinance" },
        { timestamp_bj: "2026-06-27 05:00:00", timestamp_utc: "2026-06-26 21:00:00", symbol: "NQ=F", name: "纳指", price: 1, normalized_pct: 0.5, source: "okx_gapfill" },
        { timestamp_bj: "2026-06-27 06:00:00", timestamp_utc: "2026-06-26 22:00:00", symbol: "NQ=F", name: "纳指", price: 1, normalized_pct: 0.8, source: "okx_gapfill" },
      ]}],
    } as any;
    const bands = deriveShadedBands(history);
    expect(bands.length).toBe(1);
    expect(bands[0].x1).toBe("06-27 05:00");   // 与 buildHistoryChart 的 time 格式一致(slice(5,16))
    expect(bands[0].x2).toBe("06-27 06:00");
  });

  it("returns empty array when no gapfill points", () => {
    const history = { symbols: ["NQ=F"], start: {} as any, end: {} as any,
      series: [{ symbol: "NQ=F", name: "纳指", asset_class: "futures", points: [
        { timestamp_bj: "2026-06-27 04:00:00", timestamp_utc: "2026-06-26 20:00:00", symbol: "NQ=F", name: "纳指", price: 1, normalized_pct: 0, source: "yfinance" },
      ]}] } as any;
    expect(deriveShadedBands(history)).toEqual([]);
  });

  it("splits non-contiguous gapfill runs into separate bands", () => {
    const history = { symbols: ["NQ=F"], start: {} as any, end: {} as any,
      series: [{ symbol: "NQ=F", name: "纳指", asset_class: "futures", points: [
        { timestamp_bj: "2026-06-27 05:00:00", timestamp_utc: "2026-06-26 21:00:00", symbol: "NQ=F", name: "纳指", price: 1, normalized_pct: 0, source: "okx_gapfill" },
        { timestamp_bj: "2026-06-27 06:00:00", timestamp_utc: "2026-06-26 22:00:00", symbol: "NQ=F", name: "纳指", price: 1, normalized_pct: 0, source: "yfinance" },
        { timestamp_bj: "2026-06-27 07:00:00", timestamp_utc: "2026-06-26 23:00:00", symbol: "NQ=F", name: "纳指", price: 1, normalized_pct: 0, source: "okx_gapfill" },
      ]}] } as any;
    const bands = deriveShadedBands(history);
    expect(bands.length).toBe(2);
    expect(bands[0].x1).toBe("06-27 05:00");
    expect(bands[0].x2).toBe("06-27 05:00");
    expect(bands[1].x1).toBe("06-27 07:00");
    expect(bands[1].x2).toBe("06-27 07:00");
  });

  it("returns empty array when history.series is missing", () => {
    expect(deriveShadedBands({} as any)).toEqual([]);
  });
});

describe("buildOverviewCards", () => {
  it("pairs each proxy perp with its corresponding futures card", () => {
    const items = [
      { symbol: "NQ=F", name: "纳指期货", asset_class: "futures" },
      { symbol: "ES=F", name: "标普期货", asset_class: "futures" },
      { symbol: "QQQ-USDT-SWAP", name: "纳指代理永续", asset_class: "perp" }
    ] as any;

    const cards = buildOverviewCards(items, "futures");

    expect(cards).toHaveLength(2);
    expect(cards.find((card) => card.primary.symbol === "NQ=F")?.perp?.symbol).toBe("QQQ-USDT-SWAP");
    expect(cards.find((card) => card.primary.symbol === "ES=F")?.perp).toBeUndefined();
  });

  it("does not render proxy perps as a separate overview band", () => {
    const items = [
      { symbol: "QQQ-USDT-SWAP", name: "纳指代理永续", asset_class: "perp" }
    ] as any;
    expect(buildOverviewCards(items, "futures")).toEqual([]);
  });
});
