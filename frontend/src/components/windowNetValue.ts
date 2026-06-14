import type { MarketHistoryResponse, NewsItem } from "../api/types";
import type { ChartPoint } from "./Charts";

export type ChartMarker = { time: string; role: "driver" | "contradictory"; title: string };

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
    const base = series.points.find((p) => p.price)?.price ?? null;  // 首个非 0 价作分母
    series.points.forEach((point) => {
      if (!point.timestamp_utc) return;
      const utcMinute = point.timestamp_utc.slice(0, 16);
      const displayTime = point.timestamp_bj?.slice(5, 16) ?? utcMinute;
      const row = byUtcMinute.get(utcMinute) ?? { time: displayTime };
      row[key] = base ? point.price / base : null;
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

// 从候选新闻 + 角色映射里取出 driver/contradictory，snap 到时间差最小的桶，按时间升序。
export function deriveMarkers(
  candidateNews: NewsItem[],
  newsRoles: Record<number, string>,
  buckets: { time: string; utcMinute: string }[]
): ChartMarker[] {
  if (!buckets.length) return [];
  const collected: { marker: ChartMarker; utc: string }[] = [];

  candidateNews.forEach((item) => {
    const role = newsRoles[item.id];
    if (role !== "driver" && role !== "contradictory") return;
    if (!item.timestamp_utc) return;
    const target = item.timestamp_utc.slice(0, 16);
    const targetMs = toMs(target);
    let best = buckets[0];
    let bestDiff = Math.abs(toMs(best.utcMinute) - targetMs);
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
