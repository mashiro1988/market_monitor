import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, apiErrorText } from "../api/client";
import type { EventMarketItem, MarketSweepResponse, PredictionMarketSummary } from "../api/types";
import { Button, SelectControl } from "./Controls";
import { MarketProposalPanel } from "./MarketProposalPanel";
import { PredictionCard } from "./PredictionCard";
import { buildMarketChart, predictionWindowOptions } from "./predictionChart";
import { ErrorState, LoadingState } from "./StateViews";

export function MarketChartCard({ summary, hours }: { summary: PredictionMarketSummary; hours: number }) {
  const history = useQuery({
    queryKey: ["prediction-history", summary.market_id, hours],
    queryFn: () => api.predictionHistory(summary.market_id, hours)
  });
  const chart = buildMarketChart(history.data ?? []);
  const yes = summary.outcomes.find((o) => o.outcome.toLowerCase() === "yes");
  return (
    <PredictionCard
      title={summary.question}
      data={chart.data}
      keys={chart.keys}
      meta={{
        volume: summary.volume,
        outcomes: summary.outcomes.length,
        latestPct: yes?.probability_pct ?? summary.outcomes[0]?.probability_pct,
        updatedAt: summary.outcomes[0]?.timestamp_bj ?? null
      }}
    />
  );
}

function linkBadges(item: EventMarketItem): { cls: string; text: string }[] {
  const badges: { cls: string; text: string }[] = [];
  // 三种断流语义分开打(spec §5):摘下不在此(摘了就不显示),停用/结算各一枚
  if (!item.enabled) badges.push({ cls: "s-badge weak", text: "已停用" });
  if (item.settled) badges.push({ cls: "s-badge mid", text: "已结算/断流" });
  if (item.link_source === "auto")
    badges.push({ cls: "s-badge none", text: `AI ${item.confidence ?? "—"}` });
  return badges;
}

/** 事件详情·市场定价区块(spec §5):新闻叙事 vs 市场定价对照看。
 *  永远渲染:空事件也有「找市场提案」入口(spec 入口②);
 *  已结算市场的"找后继"=同一按钮(提案素材=事件,入口③收敛于此)。 */
export function EventMarkets({ eventId, eventType }: {
  eventId: number; eventType: "macro" | "crypto" }) {
  const qc = useQueryClient();
  const [hours, setHours] = useState("720");
  const markets = useQuery({
    queryKey: ["event-markets", eventId],
    queryFn: () => api.researchEventMarkets(eventId)
  });
  const [actionError, setActionError] = useState("");
  const [applied, setApplied] = useState("");
  const [sweepResult, setSweepResult] = useState<MarketSweepResponse | null>(null);
  const sweep = useMutation({
    mutationFn: () => api.researchMarketSweep(eventType, eventId),
    onSuccess: (r) => { setActionError(""); setSweepResult(r); },
    onError: (err) => setActionError(apiErrorText(err, "找市场提案失败"))
  });
  const detach = useMutation({
    mutationFn: (linkId: number) => api.researchEventMarketDetach(linkId),
    onSuccess: () => {
      setActionError("");
      void qc.invalidateQueries({ queryKey: ["event-markets", eventId] });
      void qc.invalidateQueries({ queryKey: ["prediction-tracked"] });
    },
    onError: () => setActionError("摘下失败")
  });

  if (markets.isLoading) return <LoadingState label="加载关联市场" />;
  if (markets.isError) return <ErrorState error={markets.error} />;
  const items = markets.data?.items ?? [];

  return (
    <div className="panel">
      <div className="panel-head">
        <h2>市场定价</h2>
        <SelectControl label="窗口" value={hours} onChange={setHours}
                       options={predictionWindowOptions} />
        <Button kind="secondary" disabled={sweep.isPending} onClick={() => sweep.mutate()}>
          {sweep.isPending ? "找市场中…" : "找市场提案"}
        </Button>
        {actionError && <span style={{ color: "var(--danger)" }}>{actionError}</span>}
        {applied && <span className="muted">{applied}</span>}
      </div>
      {sweepResult && (
        <MarketProposalPanel eventType={eventType} result={sweepResult}
          onClose={() => setSweepResult(null)}
          onApplied={(summary) => {
            setSweepResult(null);
            setApplied(summary);
            void qc.invalidateQueries({ queryKey: ["event-markets", eventId] });
            void qc.invalidateQueries({ queryKey: ["prediction-tracked"] });
          }} />
      )}
      {!items.length && !sweepResult && (
        <div className="muted">
          尚未关联市场——点「找市场提案」让 AI 去 Polymarket 找,或去市场定价页签手动搜索。
        </div>
      )}
      {items.map((item) => (
        <div key={item.link_id} style={{ marginBottom: 12 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span className="rp-title">{item.display_name || item.slug}</span>
            {linkBadges(item).map((b, i) => <span key={i} className={b.cls}>{b.text}</span>)}
            <span style={{ flex: 1 }} />
            <button type="button" className="link-button danger"
                    disabled={detach.isPending}
                    onClick={() => {
                      if (window.confirm(`把 ${item.slug} 从本事件摘下?(跟踪照旧,留痕可查)`))
                        detach.mutate(item.link_id);
                    }}>
              摘下
            </button>
          </div>
          {item.waiting_first_scan
            ? <div className="muted">等待首轮采集(最长 1 小时)…</div>
            : (
              <div className="prediction-grid">
                {item.markets.map((m) => (
                  <MarketChartCard key={m.market_id} summary={m} hours={Number(hours)} />
                ))}
              </div>
            )}
        </div>
      ))}
    </div>
  );
}
