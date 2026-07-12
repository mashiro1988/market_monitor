import { describe, expect, it, test } from "vitest";
import type { MarketHistoryResponse, NewsItem } from "../api/types";
import { buildNetValueChart, deriveTierLanes, computeNetValueDomain, deriveMarkers, shiftUtcIso } from "./windowNetValue";

function pt(symbol: string, name: string, price: number, utc: string, bj: string) {
  return { symbol, name, price, normalized_pct: 0, source: "test", timestamp_utc: utc, timestamp_bj: bj };
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

test("deriveMarkers 只保留 driver，snap 到最近桶，按时间升序", () => {
  const candidate = [
    news(1, "驱动新闻", "2026-06-14T21:34:00Z"),
    news(2, "冗余新闻", "2026-06-14T21:31:00Z"),
    news(3, "噪音新闻", "2026-06-14T21:33:00Z")
  ];
  const roles = { 1: "driver", 2: "redundant", 3: "noise" };
  const out = deriveMarkers(candidate, roles, buckets);
  expect(out).toEqual([
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


describe("deriveTierLanes", () => {
  // 2026-07-12 用户白板拍板：主图不画红绿，下方 0.3/0.5/0.8 三行速度带——
  // 每个 5min 桶算即时 15min 开收净，落在最高触及档那一行（无锁存，可降档），方向定色。
  const bks = ["13:05", "13:10", "13:15", "13:20", "13:25", "13:30", "13:35", "13:40"].map((m, i) => (
    { time: `t${i}`, utcMinute: `2026-07-08T${m}` }
  ));
  it("routes each bucket to its highest touched tier lane (with de-escalation)", () => {
    // 15min 净（closes[i]/closes[i-3]−1）：t3 +0.35% → 0.3 行；t4 +0.55% → 0.5 行；
    // t5 +0.85% → 0.8 行；t6 +0.40% → 回落 0.3 行（低于 0.5 档滞回线 0.45，真降档可见）；
    // t7 +0.20% → 无带（低于 0.3 档滞回线 0.27）
    const closes = [1.0, 1.0, 1.0, 1.0035, 1.0055, 1.0085, 1.00752, 1.0075];
    const lanes = deriveTierLanes(bks, closes);
    expect(lanes[0].map((b) => [b.x1, b.x2, b.dir])).toEqual([["t2", "t3", 1], ["t5", "t6", 1]]);
    expect(lanes[1].map((b) => [b.x1, b.x2, b.dir])).toEqual([["t3", "t4", 1]]);
    expect(lanes[2].map((b) => [b.x1, b.x2, b.dir])).toEqual([["t4", "t5", 1]]);
  });
  it("hysteresis keeps a grazing run in one lane (2026-07-11 23:40 实弹)", () => {
    // 实弹：-0.535% / -0.495% / -0.520%——中间一桶以 5‰ 之差跌破 0.5 被切成 0.5/0.3/0.5 三明治。
    // 滞回：进档按原阈值，退档需 < 档位×0.9（0.5 档滞回线 0.45）→ 三桶合成一条 0.5 带。
    const closes = [1.0, 1.0, 1.0, 0.99465, 0.99520, 0.99480, 0.9950, 0.9952];
    // t3: -0.535%；t4: -0.480%（≥0.45 滞回保持）；t5: -0.520%；t6/t7 停在低位（15min 净≈0，无带）
    const lanes = deriveTierLanes(bks, closes);
    expect(lanes[1].map((b) => [b.x1, b.x2, b.dir])).toEqual([["t2", "t5", -1]]);
    expect(lanes[0].filter((b) => b.dir === -1)).toEqual([]);   // 中间桶不再掉进 0.3 行
  });
  it("merges consecutive same-tier same-direction buckets into one bar", () => {
    // t3/t4/t5 三桶都在 0.3 档跌速（其后动能衰减到无带）→ 合并为一条 [t2..t5]
    const closes = [1.0, 1.0, 1.0, 0.9965, 0.9962, 0.9960, 0.9958, 0.9957];
    const lanes = deriveTierLanes(bks, closes);
    expect(lanes[0].map((b) => [b.x1, b.x2, b.dir])).toEqual([["t2", "t5", -1]]);
    expect(lanes[1]).toEqual([]);
  });
  it("colors by direction with lane-specific depth", () => {
    const closes = [1.0, 1.0, 1.0, 1.0035, 1.0, 1.0, 1.0, 1.0];
    const lanes = deriveTierLanes(bks, closes);
    expect(lanes[0][0].fill).toContain("94,234,212");      // 涨 = 青
    const closesDn = [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 0.9915, 1.0];
    const dn = deriveTierLanes(bks, closesDn);
    expect(dn[2][0].fill).toContain("251,113,133");        // 0.8 行跌 = 玫红
    expect(dn[2][0].fill).toContain("0.94");               // 0.8 行最深
  });
  it("returns empty lanes without closes", () => {
    expect(deriveTierLanes(bks, undefined)).toEqual([[], [], []]);
  });
});
