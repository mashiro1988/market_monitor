import type { PredictionRow } from "../api/types";
import { type ChartPoint } from "./Charts";

// 小时级快照(2026-08-28 降频)下 2h/6h 窗口只剩 2-6 个点,窗口档位重定
export const predictionWindowOptions = [
  { label: "24小时", value: "24" },
  { label: "7天", value: "168" },
  { label: "30天", value: "720" },
  { label: "1年", value: "8760" }
];

export function buildMarketChart(history: PredictionRow[]): { data: ChartPoint[]; keys: string[] } {
  const byTime = new Map<string, ChartPoint>();
  const keys = Array.from(new Set(history.map((row) => row.outcome)));
  history.forEach((row) => {
    const time = row.timestamp_bj?.slice(5, 16) ?? "";
    const entry = byTime.get(time) ?? { time, sort_key: row.timestamp_utc ?? row.timestamp_bj ?? time };
    entry[row.outcome] = row.probability_pct;
    byTime.set(time, entry);
  });
  const data = Array.from(byTime.values())
    .sort((a, b) => String(a.sort_key ?? a.time).localeCompare(String(b.sort_key ?? b.time)))
    .map(({ sort_key, ...row }) => row);
  return { data, keys };
}
