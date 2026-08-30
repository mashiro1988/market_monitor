// 持仓策略页纯展示函数：不碰网络与组件，vitest 直测。

export function fmtPct(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  const pct = value * 100;
  const sign = pct > 0 ? "+" : "";
  return `${sign}${pct.toFixed(1)}%`;
}

export function fmtUsd(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  return `$${Math.round(value).toLocaleString("en-US")}`;
}

export function fmtPrice(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  return value.toFixed(4);
}

export type VerdictTone = "ok" | "danger" | "muted";

export function verdictMeta(verdict: string): { label: string; tone: VerdictTone } {
  switch (verdict) {
    case "hold":
      return { label: "持有", tone: "ok" };
    case "breach":
      return { label: "跌破软止损：按框架应清仓", tone: "danger" };
    case "no_position":
      return { label: "无持仓批次", tone: "muted" };
    default:
      return { label: "数据滞后", tone: "muted" };
  }
}

const KIND_LABEL: Record<string, string> = {
  stop_breach: "清仓提示",
  vol_update: "波动率更新",
  reduce_suggest: "减仓建议",
  b2_unlocked: "B2 额度释放",
  reentry_ready: "可评估重入场",
  reentry_expired: "重入场观察过期",
  daily_ok: "收盘检查"
};

export function kindLabel(kind: string): string {
  return KIND_LABEL[kind] ?? kind;
}
