import { describe, expect, it } from "vitest";
import {
  buildDailyRows,
  buildLinkageFrames,
  classMeta,
  fmtS,
  medianOf,
  readWindowBands,
  stripBlocks,
  symmetricAxis,
  symmetricDomain,
  tierName,
} from "./behaviorFormat";

const tf = (utc: string, bj: string) => ({ timestamp_utc: utc, timestamp_bj: bj });

describe("buildDailyRows", () => {
  it("aggregates counts, net diff, tiers and composition", () => {
    const rows = buildDailyRows({
      symbol: "BTC/USDT",
      days: [{
        bj_date: "2026-07-08", day_type: "weekday", live: false,
        counts: { "0.3": { up: 5, down: 8 }, "0.5": { up: 2, down: 3 }, "0.8": { up: 1, down: 1 } },
        composition: { macro_news: 3, pure_resonance: 1, industry_news: 1, sentiment: 2, no_ref_news: 0, no_ref_pending: 0 },
        down_net_sum: -3.87, up_net_sum: 2.41,
        up_net_sum_strong: 1.55, down_net_sum_strong: -0.6,
        sent_up: 1, sent_down: 2, sent_up_net_sum: 0.9, sent_down_net_sum: -1.4,
        computed_at: tf("2026-07-09T00:05:00", "2026-07-09 08:05:00"),
      }],
    } as any);
    expect(rows[0]).toMatchObject({
      date: "07-08", weekend: false, up: 8, down: 12, net: -4,
      t05: 5, t08: 2, sent: 2, comp: 7, downSumNeg: -3.87,
      upSum: 2.41,
      t05Up: 2, t05Down: 3, t08Up: 1, t08Down: 1,
      sentUp: 1, sentDown: 2, sentNetCount: -1,
      sentUpNet: 0.9, sentDownNet: -1.4, sentNetAmp: -0.5,
      sentUpRatio: 14, sentDownRatio: 29,
      strongNet: -1, sumNet: -1.46, sentRatioNet: -15,
      // 宏观净幅：强段 1.55−0.6=0.95，情绪 0.9−1.4=−0.5 → 宏观 1.45；弱段 = −1.46−0.95 = −2.41
      strongAmpNet: 0.95, macroAmpNet: 1.45, weakAmpNet: -2.41, macroShare: -99, macroSharePlot: -99,
    });
    expect(rows[0].upSumStrong).toBe(1.55);
    expect(rows[0].upSumWeak).toBeCloseTo(0.86, 4);
    expect(rows[0].downSumStrongNeg).toBe(-0.6);
    expect(rows[0].downSumWeakNeg).toBeCloseTo(-3.27, 4);
  });

  it("clamps weak layer at zero when rounding residue flips sign", () => {
    const rows = buildDailyRows({
      symbol: "BTC/USDT",
      days: [{
        bj_date: "2026-07-09", day_type: "weekday", live: true,
        counts: {}, composition: {},
        down_net_sum: -0.5, up_net_sum: 1.0,
        up_net_sum_strong: 1.0001, down_net_sum_strong: -0.5001,
        computed_at: tf("2026-07-09T10:00:00", "2026-07-09 18:00:00"),
      }],
    } as any);
    expect(rows[0].upSumWeak).toBe(0);
    expect(rows[0].downSumWeakNeg).toBe(0);
    expect(rows[0].strongNet).toBe(0);
    expect(rows[0].sumNet).toBe(0.5);
    expect(rows[0].sentRatioNet).toBeNull();
    expect(rows[0].strongAmpNet).toBe(0.5);
    expect(rows[0].macroAmpNet).toBe(0.5);
    expect(rows[0].weakAmpNet).toBe(0);
    expect(rows[0].macroShare).toBe(100);
  });

  it("blanks macro share on flat days and clamps the plotted share", () => {
    const day = (up: number, down: number, upStrong: number, downStrong: number) => ({
      bj_date: "2026-09-01", day_type: "weekday", live: true, counts: {}, composition: {},
      up_net_sum: up, down_net_sum: down, up_net_sum_strong: upStrong, down_net_sum_strong: downStrong,
      sent_up: 0, sent_down: 0, sent_up_net_sum: 0, sent_down_net_sum: 0,
      computed_at: tf("2026-09-01T10:00:00", "2026-09-01 18:00:00"),
    });
    const flat = buildDailyRows({ symbol: "BTC/USDT", days: [day(0.3, 0, 0.3, 0)] } as any)[0];
    expect(flat.macroAmpNet).toBe(0.3);
    expect(flat.macroShare).toBeNull();          // 总净幅 0.3 < 0.5 下限
    expect(flat.macroSharePlot).toBeNull();
    const spike = buildDailyRows({ symbol: "BTC/USDT", days: [day(4.0, -3.5, 4.0, 0)] } as any)[0];
    expect(spike.macroShare).toBe(800);          // 宏观 4.0 / 总 0.5
    expect(spike.macroSharePlot).toBe(200);      // 作图钳位
    expect(spike.weakAmpNet).toBe(-3.5);
  });
});

describe("symmetricDomain", () => {
  it("falls back to a unit domain when there is no data", () => {
    expect(symmetricDomain([], true)).toEqual([-2, 2]);
    expect(symmetricDomain([0, null, undefined])).toEqual([-1, 1]);
  });

  it("rounds integer axes up to an even bound so ±m/2 ticks stay whole", () => {
    expect(symmetricDomain([3, -5, 2], true)).toEqual([-6, 6]);
    expect(symmetricDomain([-4, 1], true)).toEqual([-4, 4]);
    expect(symmetricDomain([null, 1], true)).toEqual([-2, 2]);
  });

  it("rounds float axes up to a nice 1/1.5/2/3/4/5/6/8/10 × 10^k bound", () => {
    expect(symmetricDomain([3.87, -1.2])).toEqual([-4, 4]);
    expect(symmetricDomain([12.3])).toEqual([-15, 15]);
    expect(symmetricDomain([-0.13])).toEqual([-0.15, 0.15]);
    expect(symmetricDomain([10])).toEqual([-10, 10]);
  });
});

describe("symmetricAxis", () => {
  it("always ticks zero and mirrors ±m/2, ±m around it", () => {
    expect(symmetricAxis([3, -1], true)).toEqual({ domain: [-4, 4], ticks: [-4, -2, 0, 2, 4] });
    expect(symmetricAxis([12.3])).toEqual({ domain: [-15, 15], ticks: [-15, -7.5, 0, 7.5, 15] });
    expect(symmetricAxis([-0.13])).toEqual({ domain: [-0.15, 0.15], ticks: [-0.15, -0.075, 0, 0.075, 0.15] });
  });
});

describe("stripBlocks", () => {
  const seg = (s: string, e: string, dir: number) => ({
    start: tf(s, ""), end: tf(e, ""), direction: dir, tier_idx: 1,
  }) as any;
  it("maps segments into percent blocks and clips to domain", () => {
    const d0 = Date.parse("2026-07-08T00:00:00Z");
    const d1 = Date.parse("2026-07-08T10:00:00Z");
    const blocks = stripBlocks(
      [seg("2026-07-08T02:00:00", "2026-07-08T03:00:00", 1),
       seg("2026-07-07T23:00:00", "2026-07-08T01:00:00", -1),   // 左越界 → 裁剪
       seg("2026-07-08T11:00:00", "2026-07-08T12:00:00", 1)],   // 域外 → 丢
      d0, d1,
    );
    expect(blocks).toHaveLength(2);
    expect(blocks[0].leftPct).toBeCloseTo(20);
    expect(blocks[0].widthPct).toBeCloseTo(10);
    expect(blocks[1].leftPct).toBeCloseTo(0);
    expect(blocks[1].up).toBe(false);
  });
});

describe("buildLinkageFrames", () => {
  it("builds frames on breadth grid with maxAbs", () => {
    const { frames, symbols } = buildLinkageFrames({
      symbol: "BTC/USDT", hours: 6, rolling_points: 30,
      series: [
        { symbol: "NQ=F", label: "纳指", points: [{ t: tf("a", "2026-07-08 21:30:00"), s: 0.77 }, { t: tf("b", "2026-07-08 21:35:00"), s: null }] },
        { symbol: "DX-Y.NYB", label: "美元指数", points: [{ t: tf("a", ""), s: -0.33 }, { t: tf("b", ""), s: -0.9 }] },
      ],
      breadth: [{ t: tf("a", "2026-07-08 21:30:00"), count: 2 }, { t: tf("b", "2026-07-08 21:35:00"), count: 1 }],
    } as any);
    expect(symbols.map((s) => s.symbol)).toEqual(["NQ=F", "DX-Y.NYB"]);
    expect(frames[0]).toMatchObject({ t: "07-08 21:30", breadth: 2, "NQ=F": 0.77, maxAbs: 0.77 });
    expect(frames[1].maxAbs).toBeCloseTo(0.9);   // 反向也按 |S| 取强
  });
});

describe("readWindowBands", () => {
  // 5min 网格：后端 timestamp_utc 是 naive UTC（无 Z），timestamp_bj 是 UTC+8 空格分隔
  const grid = (fromUtcMs: number, count: number) =>
    Array.from({ length: count }, (_, i) => {
      const ms = fromUtcMs + i * 5 * 60_000;
      return {
        t: {
          timestamp_utc: new Date(ms).toISOString().slice(0, 19),
          timestamp_bj: new Date(ms + 8 * 3600_000).toISOString().slice(0, 19).replace("T", " "),
        },
        count: 0,
      };
    });
  const resp = (breadth: unknown[], tailMinutes = 60) =>
    ({ symbol: "BTC/USDT", hours: 48, rolling_points: 30, read_tail_minutes: tailMinutes, series: [], breadth }) as any;
  // 段 = 2026-07-29 18:30–18:55 UTC（北京 07-30 02:30–02:55），线上实例
  const WIN = { startUtc: "2026-07-29T18:30:00", endUtc: "2026-07-29T18:55:00" };
  const T = (hhmm: string) => Date.parse(`2026-07-29T${hhmm}:00Z`);

  it("splits into segment band and read-tail band", () => {
    const bands = readWindowBands(resp(grid(T("16:30"), 73)), WIN);   // 16:30 ~ 22:30
    expect(bands).toEqual({
      seg: { x1: "07-30 02:30", x2: "07-30 02:55" },
      tail: { x1: "07-30 02:55", x2: "07-30 03:55" },                 // 段止 +60min
    });
  });

  it("clamps the tail band to the last available point", () => {
    const bands = readWindowBands(resp(grid(T("16:30"), 35)), WIN);   // 数据只到 19:20
    expect(bands?.tail).toEqual({ x1: "07-30 02:55", x2: "07-30 03:20" });
  });

  it("drops the tail band when data ends at the segment end", () => {
    const bands = readWindowBands(resp(grid(T("16:30"), 30)), WIN);   // 数据止于 18:55
    expect(bands?.seg.x2).toBe("07-30 02:55");
    expect(bands?.tail).toBeNull();
  });

  it("clamps the segment band to the first point when the window starts off-chart", () => {
    const bands = readWindowBands(resp(grid(T("18:40"), 24)), WIN);   // 图从段中间起
    expect(bands?.seg).toEqual({ x1: "07-30 02:40", x2: "07-30 02:55" });
  });

  it("returns null without a window, without data, or when the window is off-chart", () => {
    expect(readWindowBands(resp(grid(T("16:30"), 73)), null)).toBeNull();
    expect(readWindowBands(resp([]), WIN)).toBeNull();
    expect(readWindowBands(resp(grid(T("06:00"), 12)), WIN)).toBeNull();   // 图早于段
  });

  it("honours the tail length reported by the API", () => {
    const bands = readWindowBands(resp(grid(T("16:30"), 73), 30), WIN);
    expect(bands?.tail?.x2).toBe("07-30 03:25");                      // 段止 +30min
  });
});

describe("small helpers", () => {
  it("formats", () => {
    expect(fmtS(0.774)).toBe("+0.77");
    expect(fmtS(-0.33)).toBe("-0.33");
    expect(fmtS(null)).toBe("—");
    expect(tierName(2)).toBe("0.8档");
    expect(classMeta("sentiment").cls).toBe("k-sent");
    expect(classMeta(null).label).toContain("未分类");
    expect(medianOf([3, 1, 2])).toBe(2);
    expect(medianOf([])).toBeNull();
  });
});
