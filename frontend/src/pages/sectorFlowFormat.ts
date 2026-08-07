import type { SectorFlows, SectorFlowSide } from "../api/types";

/** 窗口键与后端、DB 列名保持一致；UI 上 168h/720h 显示为 7d/30d。 */
export type FlowWindow = "1h" | "24h" | "168h" | "720h";

export const FLOW_WINDOWS: FlowWindow[] = ["1h", "24h", "168h", "720h"];

/** 榜单排序下拉里的资金流选项 → 取哪个市场哪个窗口的净流入。 */
export const FLOW_SORTS = {
  flow_spot_24h: { market: "spot", window: "24h" },
  flow_swap_24h: { market: "swap", window: "24h" },
  flow_spot_168h: { market: "spot", window: "168h" },
  flow_swap_168h: { market: "swap", window: "168h" },
} as const;

export type FlowSortKey = keyof typeof FLOW_SORTS;

export function isFlowSortKey(key: string): key is FlowSortKey {
  return key in FLOW_SORTS;
}

/** 金额缩写：+$46.2M / -$1.2B / +$842。0 与缺失一律「—」（0 净流入不值得占一格视线）。 */
export function fmtMoney(v: number | null | undefined): string {
  if (v === null || v === undefined || v === 0 || Number.isNaN(v)) return "—";
  const sign = v > 0 ? "+" : "-";
  const abs = Math.abs(v);
  if (abs >= 1e9) return `${sign}$${(abs / 1e9).toFixed(1)}B`;
  if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(1)}M`;
  if (abs >= 1e3) return `${sign}$${(abs / 1e3).toFixed(1)}K`;
  return `${sign}$${Math.round(abs)}`;
}

/** 强度比率（百分数）= 净流入 ÷ 总成交额。成交额为 0/缺失时无从谈起，返回 null。 */
export function flowStrength(
  net: number | null | undefined,
  qv: number | null | undefined,
): number | null {
  if (net === null || net === undefined) return null;
  if (qv === null || qv === undefined || qv <= 0) return null;
  return (net / qv) * 100;
}

export function fmtStrength(pct: number | null): string {
  if (pct === null) return "";
  const rounded = Math.round(pct);
  const sign = rounded > 0 ? "+" : "";
  return `${sign}${rounded}%`;
}

export function sideValue(
  side: SectorFlowSide | null | undefined,
  kind: "net" | "qv",
  window: FlowWindow,
): number | null {
  if (!side) return null;
  return side[`${kind}_${window}` as keyof SectorFlowSide] as number | null;
}

/** 榜单排序取值。整侧缺失返回 null，调用方把 null 排到末尾。 */
export function flowSortValue(
  row: { flows?: SectorFlows | null },
  key: FlowSortKey,
): number | null {
  const { market, window } = FLOW_SORTS[key];
  return sideValue(row.flows?.[market], "net", window);
}
