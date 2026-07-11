import { describe, expect, it, test } from "vitest";
import type { MarketHistoryResponse, NewsItem } from "../api/types";
import { buildNetValueChart, deriveLaneBands, deriveSegmentBands, computeNetValueDomain, deriveMarkers, laneFill, shiftUtcIso } from "./windowNetValue";

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


describe("deriveSegmentBands", () => {
  const buckets = [
    { time: "07-08 21:20", utcMinute: "2026-07-08T13:20" },
    { time: "07-08 21:25", utcMinute: "2026-07-08T13:25" },
    { time: "07-08 21:30", utcMinute: "2026-07-08T13:30" },
    { time: "07-08 21:35", utcMinute: "2026-07-08T13:35" },
  ];
  it("maps segments to tier-colored bands snapped to buckets", () => {
    const bands = deriveSegmentBands([
      { start: { timestamp_utc: "2026-07-08T13:24:00" }, end: { timestamp_utc: "2026-07-08T13:32:00" }, direction: 1, tier_idx: 2 },
      { start: { timestamp_utc: "2026-07-08T13:00:00" }, end: { timestamp_utc: "2026-07-08T13:05:00" }, direction: -1, tier_idx: 0 },  // 域外 → 丢
    ], buckets);
    expect(bands).toHaveLength(1);
    expect(bands[0].x1).toBe("07-08 21:25");
    expect(bands[0].x2).toBe("07-08 21:30");
    expect(bands[0].fill).toContain("94,234,212");   // 涨 = 站内青
    expect(bands[0].fill).toContain("0.40");          // 0.8 档不透明度（图内弱背景版）
    expect(bands[0].stroke).toContain("94,234,212"); // 0.5+ 档带同色描边，档位边界可读
    expect(bands[0].tier).toBe(2);
    expect(bands[0].dir).toBe(1);
    expect(laneFill(bands[0])).toContain("0.94");     // 轨道实色：0.8 档最深
  });
  it("clips segment spilling out of the domain", () => {
    const bands = deriveSegmentBands([
      { start: { timestamp_utc: "2026-07-08T13:00:00" }, end: { timestamp_utc: "2026-07-08T13:27:00" }, direction: -1, tier_idx: 0 },
    ], buckets);
    expect(bands[0].x1).toBe("07-08 21:20");
    expect(bands[0].x2).toBe("07-08 21:25");
    expect(bands[0].fill).toContain("251,113,133");  // 跌 = 站内玫红
    expect(bands[0].fill).toContain("0.12");          // 0.3 档：只当簇拥背景
    expect(bands[0].stroke).toBeUndefined();          // 0.3 档不描边，避免噪音
    expect(laneFill(bands[0])).toContain("0.50");     // 轨道实色：0.3 档最浅
  });
  it("lane splits a 0.8-tier segment into escalation runs by rolling 15min net", () => {
    // 段内档位演进：口径与段检测器同源 = **15min 开收净**（close vs 3 桶前 close）滚动峰值锁存，
    // 不是从段起点累计（2026-07-10 实弹：累计口径漏掉了 13:55 擦线 0.505% 的 0.5 档触发）。
    // p0-p2 为段前上下文；段内 b2 的 15min 净 +0.55% 触 0.5 档、b3 的 +0.85% 触 0.8 档。
    const bks = [
      { time: "p0", utcMinute: "2026-07-08T13:05" },
      { time: "p1", utcMinute: "2026-07-08T13:10" },
      { time: "p2", utcMinute: "2026-07-08T13:15" },
      { time: "t0", utcMinute: "2026-07-08T13:20" },
      { time: "t1", utcMinute: "2026-07-08T13:25" },
      { time: "t2", utcMinute: "2026-07-08T13:30" },
      { time: "t3", utcMinute: "2026-07-08T13:35" },
      { time: "t4", utcMinute: "2026-07-08T13:40" },
    ];
    const closes = [1.0, 1.0, 1.0, 1.0, 1.001, 1.0055, 1.0085, 1.0092];
    const bands = deriveLaneBands([
      { start: { timestamp_utc: "2026-07-08T13:20:00" }, end: { timestamp_utc: "2026-07-08T13:40:00" }, direction: 1, tier_idx: 2 },
    ], bks, closes);
    expect(bands.map((b) => [b.x1, b.x2, b.tier])).toEqual([
      ["t0", "t2", 0],   // 起段~触及0.5前：0.3 档色
      ["t2", "t3", 1],   // b2: 1.0055/1.0(15min前)=+0.55% → 0.5 档
      ["t3", "t4", 2],   // b3: 1.0085/1.0=+0.85% → 0.8 档
    ]);
    expect(laneFill(bands[2])).toContain("0.94");
    // 相邻 run 共享边界桶（后画的深色盖住重叠处），保证无缝
  });
  it("keeps a final-bucket escalation visible (borrow one bucket back)", () => {
    // 2026-07-10 实弹（07-09 21:30 段）：0.5 档在段末最后一桶才擦线触发（+0.505%），
    // 单桶 run 零宽不可见 → 图全 0.3 色、芯片 0.5 档打架。末位单桶向前借一桶宽度。
    const bks = ["13:05", "13:10", "13:15", "13:20", "13:25", "13:30"].map((m, i) => (
      { time: `t${i}`, utcMinute: `2026-07-08T${m}` }
    ));
    // 15min 净：t3=+0.1% t4=+0.2% t5=+0.55%（只在最后一桶触 0.5 档）
    const closes = [1.0, 1.0, 1.0, 1.001, 1.002, 1.0055];
    const bands = deriveLaneBands([
      { start: { timestamp_utc: "2026-07-08T13:20:00" }, end: { timestamp_utc: "2026-07-08T13:30:00" }, direction: 1, tier_idx: 1 },
    ], bks, closes);
    expect(bands.map((b) => [b.x1, b.x2, b.tier])).toEqual([
      ["t3", "t5", 0],
      ["t4", "t5", 1],   // 末桶触发：向前借一桶，深色后画盖住重叠处
    ]);
  });
  it("lane keeps uniform band when closes are unavailable", () => {
    const bands = deriveLaneBands([
      { start: { timestamp_utc: "2026-07-08T13:24:00" }, end: { timestamp_utc: "2026-07-08T13:32:00" }, direction: 1, tier_idx: 2 },
    ], buckets, [null, null, null, null]);
    expect(bands).toHaveLength(1);
    expect(bands[0].tier).toBe(2);
  });
  it("main-chart bands stay uniform per segment (2026-07-11 拍板：整段单色)", () => {
    const bands = deriveSegmentBands([
      { start: { timestamp_utc: "2026-07-08T13:24:00" }, end: { timestamp_utc: "2026-07-08T13:32:00" }, direction: 1, tier_idx: 2 },
    ], buckets);
    expect(bands).toHaveLength(1);          // 不做段内切分
    expect(bands[0].tier).toBe(2);
    expect(bands[0].fill).toContain("0.40");
  });
});
