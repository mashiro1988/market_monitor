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

export function deriveSegmentBands(
  segments: SegmentBandInput[],
  buckets: { time: string; utcMinute: string }[],
): SegmentBand[] {
  if (!buckets.length) return [];
  const out: SegmentBand[] = [];
  for (const seg of segments) {
    const s = seg.start.timestamp_utc?.slice(0, 16);
    const e = seg.end.timestamp_utc?.slice(0, 16);
    if (!s || !e) continue;
    const first = buckets.find((b) => b.utcMinute >= s);
    const last = [...buckets].reverse().find((b) => b.utcMinute <= e);
    if (!first || !last || first.utcMinute > last.utcMinute) continue;   // 段在图域外
    // 图内色带只做弱背景（0.12/0.26/0.40 + 0.5档以上描边）；高对比读数交给下方档位轨道
    const tier = Math.min(seg.tier_idx, 2);
    const opacity = 0.12 + 0.14 * tier;
    const rgb = seg.direction > 0 ? "94,234,212" : "251,113,133";        // 站内青涨/玫红跌
    out.push({
      x1: first.time,
      x2: last.time,
      fill: `rgba(${rgb},${opacity.toFixed(2)})`,
      stroke: tier >= 1 ? `rgba(${rgb},0.45)` : undefined,
      tier,
      dir: seg.direction > 0 ? 1 : -1,
    });
  }
  return out;
}

// 档位轨道：同一批段的实色版（方向色 × 深浅 0.50/0.72/0.94），画在主图正下方的窄条里。
export function laneFill(band: SegmentBand): string {
  const rgb = band.dir > 0 ? "94,234,212" : "251,113,133";
  return `rgba(${rgb},${(0.5 + 0.22 * band.tier).toFixed(2)})`;
}
