import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, apiErrorText } from "../api/client";
import type { MarketSweepResponse } from "../api/types";
import { Button } from "./Controls";
import { MarketProposalPanel } from "./MarketProposalPanel";
import { TrackedMarketsPanel } from "./TrackedMarketsPanel";

/** 池页·市场定价页签(2026-08-29 用户反馈精简):找市场提案 + 跟踪管理,没了。
 *  用户哲学=先有事件才有概率观测:曲线只在事件详情里出现,常设观测区与手动搜索
 *  均已退役(手动通道=跟踪管理里贴 slug);未挂事件的跟踪项只在管理表里躺着。 */
export function MarketPricingTab({ eventType }: { eventType: "macro" | "crypto" }) {
  const qc = useQueryClient();
  const [sweepResult, setSweepResult] = useState<MarketSweepResponse | null>(null);
  const [sweepMeta, setSweepMeta] = useState("");
  const [errorMsg, setErrorMsg] = useState("");
  const sweep = useMutation({
    mutationFn: () => api.researchMarketSweep(eventType),
    onSuccess: (r) => { setErrorMsg(""); setSweepMeta(""); setSweepResult(r); },
    onError: (err) => setErrorMsg(apiErrorText(err, "找市场提案失败"))
  });
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["prediction-tracked"] });
    void qc.invalidateQueries({ queryKey: ["research-events"] });
    void qc.invalidateQueries({ queryKey: ["event-markets"] });
  };

  return (
    <section>
      <div className="toolbar">
        <Button kind="secondary" disabled={sweep.isPending}
                onClick={() => {
                  if (window.confirm("找市场提案:AI 把本线进行中事件翻译成英文搜索词,"
                    + "去 Polymarket 找相关市场(价格目标类不提)。提案只展示,勾选采纳才跟踪。开始?"))
                    sweep.mutate();
                }}>
          {sweep.isPending ? "找市场中…" : "找市场提案"}
        </Button>
        {sweepMeta && !sweepResult && <span className="muted">{sweepMeta}</span>}
        {errorMsg && <span style={{ color: "var(--danger)" }}>{errorMsg}</span>}
      </div>

      {sweepResult && (
        <MarketProposalPanel eventType={eventType} result={sweepResult}
          onClose={() => setSweepResult(null)}
          onApplied={(summary) => { setSweepResult(null); setSweepMeta(summary); refresh(); }} />
      )}

      <TrackedMarketsPanel eventType={eventType} />
    </section>
  );
}
