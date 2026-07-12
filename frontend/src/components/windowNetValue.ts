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


// 三行档位速度带（2026-07-12 用户白板拍板）：主图不画段色带；下方 0.3/0.5/0.8 三行，
// 每个 5min 桶算**即时 15min 开收净**（close vs 3 桶前 close，与段检测触发原语同款），
// 落在最高触及档那一行（分区、无锁存——降档可见），方向定色（青涨/玫红跌）、行位定档。
// 与段检测内部产物（稀释回退嵌套段等）解耦：这是纯速度仪表，段/窗口语义只在列表与证据台。
export type TierLaneBand = {
  x1: string;
  x2: string;
  fill: string;
  tier: number;        // 0/1/2 = 0.3/0.5/0.8 行
  dir: 1 | -1;
};

// BTC 档位阶梯（%）。速度带只对 BTC 窗口渲染；后端阶梯在 config.BEHAVIOR_TIERS，改档两处同步。
const BTC_TIERS = [0.3, 0.5, 0.8];

export function deriveTierLanes(
  buckets: { time: string; utcMinute: string }[],
  closes?: (number | null)[],   // 标注品种在各桶的净值/价格（同刻度即可）
): TierLaneBand[][] {
  const lanes: TierLaneBand[][] = [[], [], []];
  if (!buckets.length || !closes) return lanes;
  // 退档滞回（2026-07-12 实弹修正：23:40 段 -0.535/-0.495/-0.520 中间桶 5‰ 擦线掉档，
  // 连贯下冲被切成 0.5/0.3/0.5 三明治）：进档按原阈值（触及即升，与检测器同语义），
  // 同向连续时退档需跌破 档位×0.95（2026-07-12 用户定参）；变向或断读即重置。
  const marks: ({ tier: number; dir: 1 | -1 } | null)[] = [];
  let last: { tier: number; dir: 1 | -1 } | null = null;
  for (let i = 0; i < buckets.length; i++) {
    const cur = closes[i];
    const prev = i >= 3 ? closes[i - 3] : null;               // 3 桶 = 15min 开收净
    if (cur == null || prev == null || prev === 0) { marks.push(null); last = null; continue; }
    const chg = (cur / prev - 1) * 100;
    const a = Math.abs(chg);
    const dir: 1 | -1 = chg >= 0 ? 1 : -1;
    let tier = -1;
    for (let k = BTC_TIERS.length - 1; k >= 0; k--) {
      const bar = last && last.dir === dir && k <= last.tier ? BTC_TIERS[k] * 0.95 : BTC_TIERS[k];
      if (a >= bar) { tier = k; break; }
    }
    if (tier < 0) { marks.push(null); last = null; continue; }
    last = { tier, dir };
    marks.push(last);
  }
  let i = 0;
  while (i < marks.length) {
    const m = marks[i];
    if (!m) { i += 1; continue; }
    let j = i;
    while (j + 1 < marks.length) {
      const n = marks[j + 1];
      if (!n || n.tier !== m.tier || n.dir !== m.dir) break;
      j += 1;
    }
    // 15min 净覆盖到前 3 桶，条带统一向前借一桶起笔：单桶读数也可见，且相邻 run 无缝
    lanes[m.tier].push({
      x1: buckets[Math.max(0, i - 1)].time,
      x2: buckets[j].time,
      tier: m.tier,
      dir: m.dir,
      fill: `rgba(${m.dir > 0 ? "94,234,212" : "251,113,133"},${(0.5 + 0.22 * m.tier).toFixed(2)})`,
    });
    i = j + 1;
  }
  return lanes;
}
