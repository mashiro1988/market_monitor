import type { MarketHistoryResponse, NewsItem } from "../api/types";
import type { ChartPoint } from "./Charts";

export type ChartMarker = { time: string; role: "driver"; title: string };

export type NetValueChart = {
  data: ChartPoint[];
  keys: string[];
  buckets: { time: string; utcMinute: string }[];
  highlightKey: string | null;
};

// 把行情历史转成「净值 = price / 显示区间首个可见 price」，每条线左缘精确归一为 1.000。
// 合并键用 UTC 截到分钟（ISO 字典序即时间序），显示用 BJT（与 MarketPage buildHistoryChart 同口径）。
export function buildNetValueChart(
  history: MarketHistoryResponse | undefined,
  annotatedSymbol: string
): NetValueChart {
  if (!history) return { data: [], keys: [], buckets: [], highlightKey: null };
  const byUtcMinute = new Map<string, ChartPoint>();
  const keys: string[] = [];
  let highlightKey: string | null = null;

  history.series.forEach((series) => {
    const key = `${series.name} (${series.symbol})`;
    keys.push(key);
    if (series.symbol === annotatedSymbol) highlightKey = key;
    const base = series.points.find((p) => p.price !== 0)?.price ?? null;  // 首个非 0 价作分母
    series.points.forEach((point) => {
      if (!point.timestamp_utc) return;
      const utcMinute = point.timestamp_utc.slice(0, 16);
      const displayTime = point.timestamp_bj?.slice(5, 16) ?? utcMinute;
      const row = byUtcMinute.get(utcMinute) ?? { time: displayTime };
      row[key] = base !== null ? point.price / base : null;
      byUtcMinute.set(utcMinute, row);
    });
  });

  const entries = Array.from(byUtcMinute.entries()).sort(([a], [b]) => a.localeCompare(b));
  const data = entries.map(([, row]) => row);
  const buckets = entries.map(([utcMinute, row]) => ({ time: row.time as string, utcMinute }));
  return { data, keys, buckets, highlightKey };
}

function toMs(utcMinute: string): number {
  const iso = utcMinute.replace(" ", "T");
  return new Date(iso.length <= 16 ? `${iso}:00Z` : iso).getTime();
}

// 从候选新闻 + 角色映射里取出 driver，snap 到时间差最小的桶，按时间升序。
export function deriveMarkers(
  candidateNews: NewsItem[],
  newsRoles: Record<number, string>,
  buckets: { time: string; utcMinute: string }[]
): ChartMarker[] {
  if (!buckets.length) return [];
  const collected: { marker: ChartMarker; utc: string }[] = [];

  candidateNews.forEach((item) => {
    const role = newsRoles[item.id];
    if (role !== "driver") return;
    if (!item.timestamp_utc) return;
    const target = item.timestamp_utc.slice(0, 16);
    const targetMs = toMs(target);
    let best = buckets[0];
    let bestDiff = Infinity;
    for (const bucket of buckets) {
      const diff = Math.abs(toMs(bucket.utcMinute) - targetMs);
      if (diff < bestDiff) {
        best = bucket;
        bestDiff = diff;
      }
    }
    collected.push({ marker: { time: best.time, role, title: item.title }, utc: target });
  });

  return collected.sort((a, b) => a.utc.localeCompare(b.utc)).map((c) => c.marker);
}

// 后端 timestamp_utc 是 naive UTC（无 Z）；显式按 UTC 解析后再做分钟偏移。
// 否则 new Date(naiveString) 会按浏览器本地时区解释，导致取数窗口整体偏移（UTC+8 → 偏 8 小时）。
export function shiftUtcIso(iso: string, deltaMinutes: number): string {
  const hasTz = /[zZ]$|[+-]\d{2}:?\d{2}$/.test(iso);
  const utc = hasTz ? iso : `${iso.replace(" ", "T")}Z`;
  return new Date(new Date(utc).getTime() + deltaMinutes * 60_000).toISOString();
}

// 净值线聚集在 1.0 附近、波动很小；recharts 默认 [0,'auto'] 会把线压扁。
// 按数据实际 min/max 拟合 Y 轴范围（含最小带宽，避免平线退化），不锚定 0。
export function computeNetValueDomain(
  data: ChartPoint[],
  keys: string[]
): [number, number] | undefined {
  let min = Infinity;
  let max = -Infinity;
  for (const row of data) {
    for (const key of keys) {
      const v = row[key];
      if (typeof v === "number" && Number.isFinite(v)) {
        if (v < min) min = v;
        if (v > max) max = v;
      }
    }
  }
  if (min === Infinity) return undefined;
  const pad = Math.max((max - min) * 0.15, 0.002);
  return [Math.floor((min - pad) * 1000) / 1000, Math.ceil((max + pad) * 1000) / 1000];
}


// Phase 2：行为段（含 0.3 档簇拥）映射为净值图色带——方向色 × 档位深浅，呈现"小推→爆发"的渐进式共振。
export type SegmentBandInput = {
  start: { timestamp_utc: string | null };
  end: { timestamp_utc: string | null };
  direction: number;
  tier_idx: number;
};

export type SegmentBand = {
  x1: string;
  x2: string;
  fill: string;
  stroke?: string;
  tier: number;        // 0/1/2 = 0.3/0.5/0.8 档（档位轨道用它配实色）
  dir: 1 | -1;
};

// BTC 档位阶梯（%）。色带只对 BTC 段渲染（segments 只在 BTC 窗口下发），
// 后端阶梯在 config.BEHAVIOR_TIERS；这里只用于段内演进的视觉切分，改档需两处同步。
const BTC_TIERS = [0.3, 0.5, 0.8];

function bandOf(rgb: string, tier: number, x1: string, x2: string, dir: 1 | -1): SegmentBand {
  // 图内色带只做弱背景（0.12/0.26/0.40 + 0.5档以上描边）；高对比读数交给下方档位轨道
  return {
    x1,
    x2,
    fill: `rgba(${rgb},${(0.12 + 0.14 * tier).toFixed(2)})`,
    stroke: tier >= 1 ? `rgba(${rgb},0.45)` : undefined,
    tier,
    dir,
  };
}

function segmentSpan(seg: SegmentBandInput, buckets: { time: string; utcMinute: string }[]): [number, number] | null {
  const s = seg.start.timestamp_utc?.slice(0, 16);
  const e = seg.end.timestamp_utc?.slice(0, 16);
  if (!s || !e) return null;
  const firstIdx = buckets.findIndex((b) => b.utcMinute >= s);
  let lastIdx = -1;
  for (let i = buckets.length - 1; i >= 0; i--) {
    if (buckets[i].utcMinute <= e) { lastIdx = i; break; }
  }
  if (firstIdx < 0 || lastIdx < 0 || firstIdx > lastIdx) return null;    // 段在图域外
  return [firstIdx, lastIdx];
}

// 主图色带（2026-07-11 用户拍板：整段单色，不做段内切分——嵌套段透明度叠加会糊成渐变）：
// 一段一带，按段的最高档位定深浅，方向定色。段内演进只在下方档位轨道呈现。
export function deriveSegmentBands(
  segments: SegmentBandInput[],
  buckets: { time: string; utcMinute: string }[],
): SegmentBand[] {
  if (!buckets.length) return [];
  const out: SegmentBand[] = [];
  for (const seg of segments) {
    const span = segmentSpan(seg, buckets);
    if (!span) continue;
    const tier = Math.min(seg.tier_idx, 2);
    const dir: 1 | -1 = seg.direction > 0 ? 1 : -1;
    const rgb = dir > 0 ? "94,234,212" : "251,113,133";                  // 站内青涨/玫红跌
    out.push(bandOf(rgb, tier, buckets[span[0]].time, buckets[span[1]].time, dir));
  }
  return out;
}

// 档位轨道色带：段内档位演进，口径与段检测器同源 = 15min 开收净（close vs 3 桶前 close）
// 滚动峰值锁存，触及 0.5/0.8 档的时点切 run（0.3 最低、0.5 高、0.8 最高，高度+深浅双通道）。
// 相邻 run 共享边界桶且按时间序后画深色；末位单桶 run 向前借一桶保证可见（擦线触发常在段末）。
export function deriveLaneBands(
  segments: SegmentBandInput[],
  buckets: { time: string; utcMinute: string }[],
  closes?: (number | null)[],   // 标注品种在各桶的净值/价格（同刻度即可）
): SegmentBand[] {
  if (!buckets.length) return [];
  const out: SegmentBand[] = [];
  for (const seg of segments) {
    const span = segmentSpan(seg, buckets);
    if (!span) continue;
    const firstIdx = span[0];
    const lastIdx = span[1];
    const tierCap = Math.min(seg.tier_idx, 2);
    const dir: 1 | -1 = seg.direction > 0 ? 1 : -1;
    const rgb = dir > 0 ? "94,234,212" : "251,113,133";

    const hasCloses = !!closes && closes.slice(firstIdx, lastIdx + 1).some((v) => v != null);
    if (!hasCloses || tierCap === 0) {
      out.push(bandOf(rgb, tierCap, buckets[firstIdx].time, buckets[lastIdx].time, dir));
      continue;
    }
    const reached: number[] = [];
    let rollMax = 0;
    for (let i = firstIdx; i <= lastIdx; i++) {
      const cur = closes![i];
      const prev = i >= 3 ? closes![i - 3] : null;          // 3 桶 = 15min 开收净（与检测触发原语同款）
      if (cur != null && prev != null && prev !== 0) {
        rollMax = Math.max(rollMax, Math.abs(cur / prev - 1) * 100);
      }
      let r = 0;
      for (let k = BTC_TIERS.length - 1; k >= 1; k--) {
        if (rollMax >= BTC_TIERS[k]) { r = k; break; }
      }
      reached.push(Math.min(r, tierCap));
    }
    let runStart = 0;
    for (let i = 1; i <= reached.length; i++) {
      if (i === reached.length || reached[i] !== reached[runStart]) {
        const endIdx = Math.min(i, reached.length - 1);                   // 延伸到下一 run 的起点桶
        const startIdx = runStart === endIdx ? Math.max(0, runStart - 1) : runStart;
        out.push(bandOf(rgb, reached[runStart], buckets[firstIdx + startIdx].time, buckets[firstIdx + endIdx].time, dir));
        runStart = i;
      }
    }
  }
  return out;
}

// 档位轨道：同一批段的实色版（方向色 × 深浅 0.50/0.72/0.94），画在主图正下方的窄条里。
export function laneFill(band: SegmentBand): string {
  const rgb = band.dir > 0 ? "94,234,212" : "251,113,133";
  return `rgba(${rgb},${(0.5 + 0.22 * band.tier).toFixed(2)})`;
}
