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
  // 2026-09-03 每图配净差线（设计稿 docs/superpowers/specs/2026-09-03-behavior-net-line-scale-design.md）
  strongNet: number;                 // 强段(0.5+0.8档)涨−跌段数（第二幅）
  sumNet: number;                    // 涨Σ − |跌Σ|（第三幅）
  sentRatioNet: number | null;       // 情绪涨占比 − 跌占比（第七幅；分母<5 → null，线断开）
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
    const sentUpRatio = comp >= 5 ? Math.round(((d.sent_up ?? 0) / comp) * 100) : null;
    const sentDownRatio = comp >= 5 ? Math.round(((d.sent_down ?? 0) / comp) * 100) : null;
    const strongUp = (d.counts["0.5"]?.up ?? 0) + (d.counts["0.8"]?.up ?? 0);
    const strongDown = (d.counts["0.5"]?.down ?? 0) + (d.counts["0.8"]?.down ?? 0);
    return {
      date: d.bj_date.slice(5),
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
      sentUpRatio,
      sentDownRatio,
      strongNet: strongUp - strongDown,
      sumNet: Math.round((Math.abs(d.up_net_sum ?? 0) - Math.abs(d.down_net_sum ?? 0)) * 1e4) / 1e4,
      // 用四舍五入后的占比相减，与占比柱读数对账一致（原始比值相减会差 1 个百分点）
      sentRatioNet: sentUpRatio != null && sentDownRatio != null ? sentUpRatio - sentDownRatio : null,
    };
  });
}

// 对称值域（2026-09-03 双轴）：[-m, +m]，m = 数据最大绝对值向上取到"整刻度"。
// 柱轴与净差线轴各自对称，两条零线才会重合。integer=true（段数）取到偶数，让 ±m/2 刻度也是整数；
// 否则取 1/1.5/2/3/4/5/6/8/10 × 10^k 中不小于最大值的那个。
const NICE_STEPS = [1, 1.5, 2, 3, 4, 5, 6, 8, 10];
export function symmetricDomain(values: (number | null | undefined)[], integer = false): [number, number] {
  let maxAbs = 0;
  for (const v of values) if (v != null && Number.isFinite(v)) maxAbs = Math.max(maxAbs, Math.abs(v));
  if (maxAbs === 0) return integer ? [-2, 2] : [-1, 1];
  let m: number;
  if (integer) {
    m = Math.ceil(maxAbs);
    if (m % 2) m += 1;
  } else {
    const k = 10 ** Math.floor(Math.log10(maxAbs));
    m = Number(((NICE_STEPS.find((step) => step * k >= maxAbs) ?? 10) * k).toPrecision(3));
  }
  return [-m, m];
}

// 对称轴 props：值域 + 五个对称刻度（±m、±m/2、0）。recharts 对固定值域自己挑刻度时从下限起按"整步"
// 递增（如 [-15, 15] 步 8 → -15/-7/1/9），零线会没有标签、上下也不对称，所以刻度显式给。
export function symmetricAxis(
  values: (number | null | undefined)[], integer = false,
): { domain: [number, number]; ticks: number[] } {
  const domain = symmetricDomain(values, integer);
  const m = domain[1];
  const half = Number((m / 2).toPrecision(4));
  return { domain, ticks: [-m, -half, 0, half, m] };
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

// 读数窗色带（2026-07-31）：面板那个 S 不是"段内的相关性"，而是「段起 → 段止+尾窗」整条
// rolling 曲线的 |S| 峰值（services/resonance_score.py rolling_peak，tail=BIG_WINDOW_MINUTES）。
// 只把段本身涂绿，会让读数落在色带之外——实例：07-30 02:30 那段带内最高 0.705，面板却写 0.90，
// 那个 0.90 在 03:55（段止+1h）。所以色带按读数窗画两段：深=事件段，浅=尾窗。
// 尾窗长度由接口 read_tail_minutes 带下来，不在前端写死。
export type ReadBand = { x1: string; x2: string };
export type ReadWindowBands = { seg: ReadBand; tail: ReadBand | null };

export function readWindowBands(
  resp: BehaviorLinkageResponse,
  windowUtc: { startUtc: string; endUtc: string } | null | undefined,
): ReadWindowBands | null {
  if (!windowUtc) return null;
  const startMs = parseUtc(windowUtc.startUtc);
  const endMs = parseUtc(windowUtc.endUtc);
  if (startMs === null || endMs === null) return null;
  // X 轴是分类轴（recharts 按值精确匹配），色带端点必须是曲线上真实存在的那一格标签
  const grid: { ms: number; label: string }[] = [];
  for (const b of resp.breadth) {
    const ms = parseUtc(b.t.timestamp_utc);
    if (ms !== null && b.t.timestamp_bj) grid.push({ ms, label: b.t.timestamp_bj.slice(5, 16) });
  }
  if (!grid.length) return null;
  const firstAtOrAfter = (ms: number) => grid.find((g) => g.ms >= ms) ?? null;
  const lastAtOrBefore = (ms: number) => {
    for (let i = grid.length - 1; i >= 0; i -= 1) if (grid[i].ms <= ms) return grid[i];
    return null;
  };
  const segStart = firstAtOrAfter(startMs);          // 段起早于图起点 → 贴到第一格
  const segEnd = lastAtOrBefore(endMs);              // 段止晚于图终点 → 贴到最后一格
  if (!segStart || !segEnd || segStart.ms > segEnd.ms) return null;   // 段整个在图外
  const tailEnd = lastAtOrBefore(endMs + resp.read_tail_minutes * 60_000);
  return {
    seg: { x1: segStart.label, x2: segEnd.label },
    // 数据还没长到段止之后（最新段/盘中）→ 没有尾窗可画
    tail: tailEnd && tailEnd.ms > segEnd.ms ? { x1: segEnd.label, x2: tailEnd.label } : null,
  };
}

export function medianOf(values: number[]): number | null {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}
