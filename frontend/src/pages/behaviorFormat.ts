// 行为面板纯函数层（price-behavior-engine-plan Task 7）：可单测，不碰 React。
import type {
  BehaviorDailyResponse,
  BehaviorLinkageResponse,
  BehaviorSegmentSchema,
} from "../api/types";

export type DailyRow = {
  date: string;        // MM-DD
  weekend: boolean;
  live: boolean;
  up: number;          // 全档段数（0.3 基座）
  down: number;
  net: number;         // 涨−跌（趋势主读数）
  t05: number;         // 触及 0.5 档段数
  t08: number;
  sent: number;        // 情绪·技术面段数（三类口径）
  comp: number;        // 构成段总数（0.5 档以上，分母<5 不读占比）
  nd: number;          // 新闻驱动
  pr: number;          // 纯共振
  st: number;          // 情绪·技术面（=sent）
  noRef: number;       // 无对照注记（已含在三类内，另计）
  sentRatio: number | null;  // 情绪占比%（分母<5 → null 不读）
  downSumNeg: number;  // 跌段净幅合计（负值，柱图向下）
  upSum: number;       // 涨段净幅合计（正值）
  upSumStrong: number;      // 强段(0.5档+)涨净幅Σ（≥0，亮层）
  upSumWeak: number;        // 弱段(0.3档)涨净幅Σ（≥0，暗层=总−强，钳位到 0）
  downSumStrongNeg: number; // 强段跌净幅Σ（≤0，亮层）
  downSumWeakNeg: number;   // 弱段跌净幅Σ（≤0，暗层）
  t05Up: number; t05Down: number;    // 0.5 档 涨/跌段数
  t08Up: number; t08Down: number;
  sentUp: number; sentDown: number;  // 情绪·技术面 涨/跌段数（0.5 档以上）
  sentNetCount: number;              // 情绪涨跌个数差
  sentUpNet: number;                 // 情绪涨段净幅Σ（≥0）
  sentDownNet: number;               // 情绪跌段净幅Σ（≤0）
  sentNetAmp: number;                // 情绪涨跌净幅差
  sentUpRatio: number | null;        // 情绪涨段占构成段 %（分母<5 → null）
  sentDownRatio: number | null;
};

export function buildDailyRows(resp: BehaviorDailyResponse): DailyRow[] {
  return resp.days.map((d) => {
    let up = 0;
    let down = 0;
    for (const v of Object.values(d.counts)) {
      up += v.up ?? 0;
      down += v.down ?? 0;
    }
    const tier = (k: string) => (d.counts[k]?.up ?? 0) + (d.counts[k]?.down ?? 0);
    const three = mergedComposition(d.composition);
    const comp = three.news_driven + three.pure_resonance + three.sentiment_tech;
    const noRef = d.composition["no_ref"] ?? 0;
    return {
      date: d.utc_date.slice(5),
      weekend: d.day_type === "weekend",
      live: d.live,
      up,
      down,
      net: up - down,
      t05: tier("0.5"),
      t08: tier("0.8"),
      sent: three.sentiment_tech,
      comp,
      nd: three.news_driven,
      pr: three.pure_resonance,
      st: three.sentiment_tech,
      noRef,
      sentRatio: comp >= 5 ? Math.round((three.sentiment_tech / comp) * 100) : null,
      downSumNeg: -Math.abs(d.down_net_sum ?? 0),
      upSum: Math.abs(d.up_net_sum ?? 0),
      upSumStrong: Math.abs(d.up_net_sum_strong ?? 0),
      upSumWeak: Math.max(0, Math.abs(d.up_net_sum ?? 0) - Math.abs(d.up_net_sum_strong ?? 0)),
      downSumStrongNeg: -Math.abs(d.down_net_sum_strong ?? 0),
      downSumWeakNeg: Math.min(0, Math.abs(d.down_net_sum_strong ?? 0) - Math.abs(d.down_net_sum ?? 0)),
      t05Up: d.counts["0.5"]?.up ?? 0,
      t05Down: d.counts["0.5"]?.down ?? 0,
      t08Up: d.counts["0.8"]?.up ?? 0,
      t08Down: d.counts["0.8"]?.down ?? 0,
      sentUp: d.sent_up ?? 0,
      sentDown: d.sent_down ?? 0,
      sentNetCount: (d.sent_up ?? 0) - (d.sent_down ?? 0),
      sentUpNet: Math.abs(d.sent_up_net_sum ?? 0),
      sentDownNet: -Math.abs(d.sent_down_net_sum ?? 0),
      sentNetAmp: Math.round((Math.abs(d.sent_up_net_sum ?? 0) - Math.abs(d.sent_down_net_sum ?? 0)) * 1e4) / 1e4,
      sentUpRatio: comp >= 5 ? Math.round(((d.sent_up ?? 0) / comp) * 100) : null,
      sentDownRatio: comp >= 5 ? Math.round(((d.sent_down ?? 0) / comp) * 100) : null,
    };
  });
}

export function fmtS(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toFixed(2)}`;
}

export function tierName(tierIdx: number): string {
  return ["0.3档", "0.5档", "0.8档"][tierIdx] ?? `${tierIdx}`;
}

export const CLASS_META: Record<string, { label: string; cls: string }> = {
  // 三类（窗口级，人工标注/结论页口径）
  news_driven: { label: "新闻驱动", cls: "k-macro" },
  pure_resonance: { label: "纯共振", cls: "k-reso" },
  sentiment_tech: { label: "情绪·技术面 ⚠", cls: "k-sent" },
  // 机器六类（底层保留，展示归并）
  macro_news: { label: "宏观新闻", cls: "k-macro" },
  industry_news: { label: "行业事件", cls: "k-ind" },
  sentiment: { label: "情绪候选 ⚠", cls: "k-sent" },
  no_ref_news: { label: "新闻驱动(无对照)", cls: "k-noref" },
  no_ref_pending: { label: "待定(无对照)", cls: "k-noref" },
  count_only: { label: "计数", cls: "k-count" },
};

const SIX_TO_THREE: Record<string, string> = {
  macro_news: "news_driven", industry_news: "news_driven", no_ref_news: "news_driven",
  pure_resonance: "pure_resonance",
  sentiment: "sentiment_tech", no_ref_pending: "sentiment_tech",
};

export function toWindowClass(cls: string | null | undefined): string | null {
  if (!cls) return null;
  if (cls === "news_driven" || cls === "pure_resonance" || cls === "sentiment_tech") return cls;
  return SIX_TO_THREE[cls] ?? null;
}

// 构成字典归并三类（新旧词表通吃：六类映射、三类透传、count_only/no_ref 注记不进和）
export function mergedComposition(raw: Record<string, number>): Record<string, number> {
  const out: Record<string, number> = { news_driven: 0, pure_resonance: 0, sentiment_tech: 0 };
  for (const [k, v] of Object.entries(raw)) {
    if (k === "no_ref") continue;
    const three = toWindowClass(k);
    if (three) out[three] += v;
  }
  return out;
}

export function classMeta(cls: string | null | undefined): { label: string; cls: string } {
  if (!cls) return { label: "未分类(未settle)", cls: "k-count" };
  return CLASS_META[cls] ?? { label: cls, cls: "k-count" };
}

// 段时间带：把段映射成时间轴上的色块（% 定位）。timestamp_utc 是 naive UTC，必须补 Z 再 parse。
export type StripBlock = { leftPct: number; widthPct: number; up: boolean; tierIdx: number };

export function parseUtc(ts: string | null | undefined): number | null {
  if (!ts) return null;
  return new Date(ts.endsWith("Z") ? ts : `${ts}Z`).getTime();
}

export function stripBlocks(
  segments: BehaviorSegmentSchema[], domainStartMs: number, domainEndMs: number,
): StripBlock[] {
  const span = domainEndMs - domainStartMs;
  if (span <= 0) return [];
  const out: StripBlock[] = [];
  for (const seg of segments) {
    const s = parseUtc(seg.start.timestamp_utc);
    const e = parseUtc(seg.end.timestamp_utc);
    if (s === null || e === null || e < domainStartMs || s > domainEndMs) continue;
    const left = (Math.max(s, domainStartMs) - domainStartMs) / span;
    const width = (Math.min(e, domainEndMs) - Math.max(s, domainStartMs)) / span;
    out.push({
      leftPct: left * 100,
      widthPct: Math.max(width * 100, 0.4),
      up: seg.direction > 0,
      tierIdx: seg.tier_idx,
    });
  }
  return out;
}

// 联动曲线数据帧：以 breadth 的时间网格为准，逐参照展开为宽表 + maxAbs。
export type LinkageFrame = Record<string, string | number | null>;

export function buildLinkageFrames(resp: BehaviorLinkageResponse): {
  frames: LinkageFrame[];
  symbols: { symbol: string; label: string }[];
} {
  const symbols = resp.series.map((s) => ({ symbol: s.symbol, label: s.label }));
  const frames: LinkageFrame[] = resp.breadth.map((b, i) => {
    const frame: LinkageFrame = {
      t: (b.t.timestamp_bj ?? "").slice(5, 16),
      breadth: b.count,
    };
    let maxAbs: number | null = null;
    for (const series of resp.series) {
      const v = series.points[i]?.s ?? null;
      frame[series.symbol] = v;
      if (v !== null && (maxAbs === null || Math.abs(v) > maxAbs)) maxAbs = Math.abs(v);
    }
    frame.maxAbs = maxAbs;
    return frame;
  });
  return { frames, symbols };
}

export function medianOf(values: number[]): number | null {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}
