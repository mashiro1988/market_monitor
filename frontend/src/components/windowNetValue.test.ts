import { expect, test } from "vitest";
import type { MarketHistoryResponse, NewsItem } from "../api/types";
import { buildNetValueChart, computeNetValueDomain, deriveMarkers, shiftUtcIso } from "./windowNetValue";

function pt(symbol: string, name: string, price: number, utc: string, bj: string) {
  return { symbol, name, price, normalized_pct: 0, timestamp_utc: utc, timestamp_bj: bj };
}

const history: MarketHistoryResponse = {
  symbols: ["BTC/USDT", "GC=F"],
  start: { timestamp_utc: null, timestamp_bj: null },
  end: { timestamp_utc: null, timestamp_bj: null },
  series: [
    {
      symbol: "BTC/USDT",
      name: "BTC",
      asset_class: "crypto",
      points: [
        pt("BTC/USDT", "BTC", 100, "2026-06-14T21:30:00Z", "2026-06-15 05:30:00"),
        pt("BTC/USDT", "BTC", 90, "2026-06-14T21:35:00Z", "2026-06-15 05:35:00")
      ]
    },
    {
      symbol: "GC=F",
      name: "黄金",
      asset_class: "commodity",
      points: [
        pt("GC=F", "黄金", 2000, "2026-06-14T21:30:00Z", "2026-06-15 05:30:00"),
        pt("GC=F", "黄金", 2020, "2026-06-14T21:35:00Z", "2026-06-15 05:35:00")
      ]
    }
  ]
};

test("buildNetValueChart 归一到首点 1.000 并标出 highlightKey", () => {
  const out = buildNetValueChart(history, "BTC/USDT");
  expect(out.keys).toEqual(["BTC (BTC/USDT)", "黄金 (GC=F)"]);
  expect(out.highlightKey).toBe("BTC (BTC/USDT)");
  expect(out.data[0]["BTC (BTC/USDT)"]).toBe(1);
  expect(out.data[0]["黄金 (GC=F)"]).toBe(1);
  expect(out.data[1]["BTC (BTC/USDT)"]).toBeCloseTo(0.9);
  expect(out.data[1]["黄金 (GC=F)"]).toBeCloseTo(1.01);
  expect(out.data[0].time).toBe("06-15 05:30");
  expect(out.buckets.map((b) => b.utcMinute)).toEqual(["2026-06-14T21:30", "2026-06-14T21:35"]);
});

test("buildNetValueChart 处理空/undefined", () => {
  expect(buildNetValueChart(undefined, "BTC/USDT")).toEqual({ data: [], keys: [], buckets: [], highlightKey: null });
});

function news(id: number, title: string, utc: string): NewsItem {
  return { id, title, timestamp_utc: utc, timestamp_bj: null } as unknown as NewsItem;
}

const buckets = [
  { time: "06-15 05:30", utcMinute: "2026-06-14T21:30" },
  { time: "06-15 05:35", utcMinute: "2026-06-14T21:35" }
];

test("deriveMarkers 只保留 driver/contradictory，snap 到最近桶，按时间升序", () => {
  const candidate = [
    news(1, "驱动新闻", "2026-06-14T21:34:00Z"),
    news(2, "矛盾新闻", "2026-06-14T21:31:00Z"),
    news(3, "噪音新闻", "2026-06-14T21:33:00Z")
  ];
  const roles = { 1: "driver", 2: "contradictory", 3: "noise" };
  const out = deriveMarkers(candidate, roles, buckets);
  expect(out).toEqual([
    { time: "06-15 05:30", role: "contradictory", title: "矛盾新闻" },
    { time: "06-15 05:35", role: "driver", title: "驱动新闻" }
  ]);
});

test("deriveMarkers 桶为空时返回空", () => {
  expect(deriveMarkers([news(1, "x", "2026-06-14T21:34:00Z")], { 1: "driver" }, [])).toEqual([]);
});

test("buildNetValueChart 全 0 价的 series → 该列全 null", () => {
  const zeroHistory: MarketHistoryResponse = {
    symbols: ["X"],
    start: { timestamp_utc: null, timestamp_bj: null },
    end: { timestamp_utc: null, timestamp_bj: null },
    series: [
      {
        symbol: "X",
        name: "零价",
        asset_class: "stock_index",
        points: [
          pt("X", "零价", 0, "2026-06-14T21:30:00Z", "2026-06-15 05:30:00"),
          pt("X", "零价", 0, "2026-06-14T21:35:00Z", "2026-06-15 05:35:00")
        ]
      }
    ]
  };
  const out = buildNetValueChart(zeroHistory, "X");
  expect(out.keys).toEqual(["零价 (X)"]);
  expect(out.data[0]["零价 (X)"]).toBeNull();
  expect(out.data[1]["零价 (X)"]).toBeNull();
});

test("shiftUtcIso 把后端 naive UTC 串当 UTC 解析（不受浏览器本地时区影响）", () => {
  // 后端 timestamp_utc 形如 "2026-06-14T11:55:00"（无 Z），必须按 UTC 解释，
  // 否则 new Date() 当成浏览器本地时区（如 UTC+8）→ 取数窗口偏移 8 小时。
  expect(shiftUtcIso("2026-06-14T11:55:00", -30)).toBe("2026-06-14T11:25:00.000Z");
  expect(shiftUtcIso("2026-06-14T11:55:00", 30)).toBe("2026-06-14T12:25:00.000Z");
  // 已带 Z 的输入应幂等正确
  expect(shiftUtcIso("2026-06-14T11:55:00Z", 0)).toBe("2026-06-14T11:55:00.000Z");
});

test("computeNetValueDomain 拟合数据范围（不锚定 0），含最小带宽", () => {
  const data = [
    { time: "a", X: 1, Y: 1 },
    { time: "b", X: 0.99, Y: 1.005 }
  ];
  expect(computeNetValueDomain(data, ["X", "Y"])).toEqual([0.987, 1.008]);
});

test("computeNetValueDomain 完全平线也给最小带宽", () => {
  expect(computeNetValueDomain([{ time: "a", X: 1 }, { time: "b", X: 1 }], ["X"])).toEqual([0.998, 1.002]);
});

test("computeNetValueDomain 无数值返回 undefined", () => {
  expect(computeNetValueDomain([], ["X"])).toBeUndefined();
  expect(computeNetValueDomain([{ time: "a", X: null }], ["X"])).toBeUndefined();
});
