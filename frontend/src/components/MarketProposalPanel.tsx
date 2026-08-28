import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { api, apiErrorText } from "../api/client";
import type { MarketProposal, MarketSweepResponse } from "../api/types";
import { Button } from "./Controls";
import { EmptyState } from "./StateViews";

export function fmtProb(p: number | null | undefined, count: number): string {
  if (p != null) return `${(p * 100).toFixed(0)}%`;
  return count > 1 ? `${count} 个子市场` : "—";
}

/** 市场提案确认面板(spec 2026-08-28 §3 提案确认制):勾选采纳才写库,交互仿 AI 梳理。
 *  池页整线提案与事件详情单事件提案(含已结算市场的"找后继")共用。 */
export function MarketProposalPanel({ eventType, result, onClose, onApplied }: {
  eventType: "macro" | "crypto";
  result: MarketSweepResponse;
  onClose: () => void;
  onApplied: (summary: string) => void;
}) {
  const [checked, setChecked] = useState<boolean[]>(result.proposals.map(() => true));
  const [errorMsg, setErrorMsg] = useState("");
  const apply = useMutation({
    mutationFn: (items: MarketProposal[]) => api.researchMarketSweepApply(eventType, items),
    onSuccess: (r) => onApplied(
      `已采纳:新建 ${r.added.length} · 复活 ${r.revived.length} · 挂接 ${r.linked}`
      + (r.skipped.length ? ` · 跳过 ${r.skipped.length}` : "")),
    onError: (err) => setErrorMsg(apiErrorText(err, "采纳失败"))
  });
  const meta = `事件 ${result.scanned_events} · 搜索词 ${result.searched_terms} · 候选 ${result.candidates}`
    + (result.dropped_price_targets ? ` · 剔价格类 ${result.dropped_price_targets}` : "");
  return (
    <div className="panel">
      <div className="panel-head">
        <h2>市场提案</h2>
        <span className="muted">{meta}</span>
        <button type="button" className="link-button" onClick={onClose}>全部忽略</button>
      </div>
      {errorMsg && <span style={{ color: "var(--danger)" }}>{errorMsg}</span>}
      {result.proposals.length === 0
        ? <EmptyState title="没找到可挂的市场(叙事类事件在 Polymarket 常无对应盘,属正常)" />
        : (
          <>
            {result.proposals.map((p, i) => (
              <label key={`${p.event_id}-${p.slug}`} className="rp-news-item"
                     style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}>
                <input type="checkbox" checked={checked[i] ?? true}
                       onChange={(ev) => {
                         const next = [...checked];
                         next[i] = ev.target.checked;
                         setChecked(next);
                       }} />
                <span className="s-badge none">#{p.event_id} {p.event_name}</span>
                <span className="rp-title">{p.title}</span>
                <span className="s-badge mid">{fmtProb(p.current_probability, p.market_count ?? 1)}</span>
                <span className="muted">
                  量 ${Math.round((p.volume ?? 0) / 1000)}k · 到期 {p.end_date || "—"} · {p.reason}
                </span>
              </label>
            ))}
            <div style={{ display: "flex", justifyContent: "flex-end", paddingTop: 8 }}>
              <Button kind="primary"
                      disabled={apply.isPending || checked.filter(Boolean).length === 0}
                      onClick={() => apply.mutate(result.proposals.filter((_, i) => checked[i]))}>
                {apply.isPending ? "写入中…" : `采纳选中(${checked.filter(Boolean).length})并跟踪`}
              </Button>
            </div>
          </>
        )}
    </div>
  );
}
