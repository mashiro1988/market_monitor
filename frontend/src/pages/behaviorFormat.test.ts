import { describe, expect, it } from "vitest";
import {
  buildDailyRows,
  buildLinkageFrames,
  classMeta,
  fmtS,
  medianOf,
  stripBlocks,
  tierName,
} from "./behaviorFormat";

const tf = (utc: string, bj: string) => ({ timestamp_utc: utc, timestamp_bj: bj });

describe("buildDailyRows", () => {
  it("aggregates counts, net diff, tiers and composition", () => {
    const rows = buildDailyRows({
      symbol: "BTC/USDT",
      days: [{
        utc_date: "2026-07-08", day_type: "weekday", live: false,
        counts: { "0.3": { up: 5, down: 8 }, "0.5": { up: 2, down: 3 }, "0.8": { up: 1, down: 1 } },
        composition: { macro_news: 3, pure_resonance: 1, industry_news: 1, sentiment: 2, no_ref_news: 0, no_ref_pending: 0 },
        down_net_sum: -3.87, up_net_sum: 2.41,
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
    });
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
