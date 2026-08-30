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
  // 筛档位(2026-08-30):分桶类市场只保留想看的档;空列表=清除筛选(全保留)。
  // 采集端同时少采,下轮起被剔的档不再产生新快照。
  const [editingFilter, setEditingFilter] = useState<number | null>(null);
  const [draftIds, setDraftIds] = useState<string[]>([]);
  const saveFilter = useMutation({
    mutationFn: ({ trackedId, ids, total }: { trackedId: number; ids: string[]; total: number }) =>
      api.updatePredictionTracked(trackedId, { market_filter: ids.length === total ? [] : ids }),
    onSuccess: () => {
      setActionError("");
      setEditingFilter(null);
      void qc.invalidateQueries({ queryKey: ["event-markets", eventId] });
      void qc.invalidateQueries({ queryKey: ["prediction-tracked"] });
    },
    onError: (err) => setActionError(apiErrorText(err, "保存档位失败"))
  });

  if (markets.isLoading) return <LoadingState label="加载关联市场" />;
  if (markets.isError) return <ErrorState error={markets.error} />;
  const items = markets.data?.items ?? [];

  return (
    <div className="panel">
      <div className="panel-head">
        <h2>市场定价</h2>
        <SelectControl label="" value={hours} onChange={setHours}
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
          尚未关联市场——点「找市场提案」让 AI 去 Polymarket 找;手动路径:去市场定价页签贴 slug 添加,再用「挂接→」选本事件。
        </div>
      )}
      {items.map((item) => (
        <div key={item.link_id} style={{ marginBottom: 12 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span className="rp-title">{item.display_name || item.slug}</span>
            {linkBadges(item).map((b, i) => <span key={i} className={b.cls}>{b.text}</span>)}
            <span style={{ flex: 1 }} />
            {item.all_markets.length > 1 && (
              <button type="button" className="link-button"
                      title="分桶类市场只保留想看的档位(同时停采其余档)"
                      onClick={() => {
                        if (editingFilter === item.link_id) {
                          setEditingFilter(null);
                        } else {
                          setEditingFilter(item.link_id);
                          setDraftIds(item.market_filter ?? item.all_markets.map((m) => m.market_id));
                        }
                      }}>
                筛档位({item.markets.length}/{item.all_markets.length})
              </button>
            )}
            <button type="button" className="link-button danger"
                    disabled={detach.isPending}
                    onClick={() => {
                      if (window.confirm(`把 ${item.slug} 从本事件摘下?(跟踪照旧,留痕可查)`))
                        detach.mutate(item.link_id);
                    }}>
              摘下
            </button>
          </div>
          {editingFilter === item.link_id && (
            <div className="panel" style={{ margin: "8px 0" }}>
              <div style={{ display: "flex", flexWrap: "wrap", gap: "6px 14px", padding: "6px 0" }}>
                {item.all_markets.map((m) => (
                  <label key={m.market_id} style={{ display: "flex", alignItems: "center",
                                                    gap: 4, cursor: "pointer" }}>
                    <input type="checkbox" checked={draftIds.includes(m.market_id)}
                           onChange={(ev) => setDraftIds(ev.target.checked
                             ? [...draftIds, m.market_id]
                             : draftIds.filter((x) => x !== m.market_id))} />
                    <span className="muted">{m.question}</span>
                  </label>
                ))}
              </div>
              <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
                <button type="button" className="link-button"
                        onClick={() => setDraftIds(item.all_markets.map((m) => m.market_id))}>
                  全选
                </button>
                <Button kind="primary" disabled={saveFilter.isPending || draftIds.length === 0}
                        onClick={() => saveFilter.mutate({ trackedId: item.tracked_id,
                                                           ids: draftIds,
                                                           total: item.all_markets.length })}>
                  {saveFilter.isPending ? "保存中…" : `保存(留 ${draftIds.length} 档)`}
                </Button>
              </div>
            </div>
          )}
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
