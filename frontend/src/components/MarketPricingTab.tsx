import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, apiErrorText } from "../api/client";
import type { MarketSearchResult, MarketSweepResponse } from "../api/types";
import { Button, SelectControl, TextInput } from "./Controls";
import { MarketChartCard } from "./EventMarkets";
import { MarketProposalPanel, fmtProb } from "./MarketProposalPanel";
import { PredictionCard } from "./PredictionCard";
import { buildFamilyChart, predictionWindowOptions } from "./predictionChart";
import { TrackedMarketsPanel } from "./TrackedMarketsPanel";
import { EmptyState, ErrorState, LoadingState } from "./StateViews";

/** 池页·市场定价页签(spec 2026-08-28 §5):提案确认制 + 手动搜索 + 常设观测 + 跟踪管理。 */
export function MarketPricingTab({ eventType }: { eventType: "macro" | "crypto" }) {
  const qc = useQueryClient();
  const [hours, setHours] = useState("720");
  const predictions = useQuery({
    queryKey: ["predictions", eventType, hours],
    queryFn: () => api.predictions({ hours: Number(hours), market: eventType })
  });
  const families = useQuery({
    queryKey: ["prediction-families", eventType, hours],
    queryFn: () => api.predictionFamilies({ hours: Number(hours), market: eventType })
  });
  const tracked = useQuery({
    queryKey: ["prediction-tracked", eventType],
    queryFn: () => api.predictionTracked(eventType)
  });
  const activeEvents = useQuery({
    queryKey: ["research-events", "active", eventType],
    queryFn: () => api.researchEvents({ status: "active", event_type: eventType })
  });
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["predictions"] });
    void qc.invalidateQueries({ queryKey: ["prediction-families"] });
    void qc.invalidateQueries({ queryKey: ["prediction-tracked"] });
    void qc.invalidateQueries({ queryKey: ["research-events"] });
  };

  // 找市场提案(确认制,面板复用 MarketProposalPanel)
  const [sweepResult, setSweepResult] = useState<MarketSweepResponse | null>(null);
  const [sweepMeta, setSweepMeta] = useState("");
  const [errorMsg, setErrorMsg] = useState("");
  const sweep = useMutation({
    mutationFn: () => api.researchMarketSweep(eventType),
    onSuccess: (r) => { setErrorMsg(""); setSweepMeta(""); setSweepResult(r); },
    onError: (err) => setErrorMsg(apiErrorText(err, "找市场提案失败"))
  });

  // 手动搜索通道(不剔价格类)
  const [searchQ, setSearchQ] = useState("");
  const [searchResults, setSearchResults] = useState<MarketSearchResult[] | null>(null);
  const search = useMutation({
    mutationFn: () => api.predictionSearch(searchQ),
    onSuccess: (r) => { setErrorMsg(""); setSearchResults(r); },
    onError: (err) => setErrorMsg(apiErrorText(err, "搜索失败"))
  });
  const [attachTarget, setAttachTarget] = useState("");   // "" = 常设(不挂事件)
  const addTracked = useMutation({
    mutationFn: (row: MarketSearchResult) => api.createPredictionTracked({
      kind: "slug",
      identifier: row.slug,
      display_name: row.title || null,
      market: eventType,
      ...(attachTarget ? { event_id: Number(attachTarget) } : {})
    }),
    onSuccess: () => { setErrorMsg(""); refresh(); },
    onError: (err) => setErrorMsg(apiErrorText(err, "添加失败"))
  });

  // 常设观测 = 本线未挂任何事件的跟踪项对应的市场卡(挂了事件的曲线在事件详情里看)
  const standaloneCards = useMemo(() => {
    const unlinkedOrigins = new Set(
      (tracked.data ?? []).filter((t) => !(t.events ?? []).length)
        .map((t) => `${t.kind}:${t.identifier}`));
    const familyIds = new Set(
      (families.data ?? []).flatMap((f) => f.series.map((s) => s.market_id)));
    return (predictions.data?.markets ?? []).filter((m) =>
      !familyIds.has(m.market_id)
      && (m.origin ? unlinkedOrigins.has(m.origin) : eventType === "macro"));
  }, [predictions.data, families.data, tracked.data, eventType]);

  return (
    <section>
      <div className="toolbar">
        <SelectControl label="时间窗口" value={hours} onChange={setHours}
                       options={predictionWindowOptions} />
        <Button kind="secondary" disabled={sweep.isPending}
                onClick={() => {
                  if (window.confirm("找市场提案:AI 把本线进行中事件翻译成英文搜索词,"
                    + "去 Polymarket 找相关市场(价格目标类不提)。提案只展示,勾选采纳才跟踪。开始?"))
                    sweep.mutate();
                }}>
          {sweep.isPending ? "找市场中…" : "找市场提案"}
        </Button>
        <TextInput label="手动搜索 Polymarket" value={searchQ} onChange={setSearchQ}
                   placeholder="fed rate cut / btc etf" />
        <Button onClick={() => searchQ.trim() && search.mutate()} disabled={search.isPending}>
          {search.isPending ? "搜索中…" : "搜索"}
        </Button>
      </div>
      {errorMsg && <div className="panel"><span style={{ color: "var(--danger)" }}>{errorMsg}</span></div>}
      {sweepMeta && !sweepResult && <div className="muted">{sweepMeta}</div>}

      {sweepResult && (
        <MarketProposalPanel eventType={eventType} result={sweepResult}
          onClose={() => setSweepResult(null)}
          onApplied={(summary) => { setSweepResult(null); setSweepMeta(summary); refresh(); }} />
      )}

      {searchResults && (
        <div className="panel">
          <div className="panel-head">
            <h2>搜索结果</h2>
            <SelectControl label="添加时挂到" value={attachTarget} onChange={setAttachTarget}
                           options={[{ label: "常设(不挂事件)", value: "" },
                                     ...(activeEvents.data?.items ?? []).map((e) => ({
                                       label: `#${e.display_no} ${e.name}`, value: String(e.id) }))]} />
            <button type="button" className="link-button" onClick={() => setSearchResults(null)}>收起</button>
          </div>
          {searchResults.length === 0 ? <EmptyState title="没搜到活跃市场" /> : searchResults.map((r) => (
            <div key={r.slug} className="rp-news-item" style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <span className="rp-title">{r.title}</span>
              <span className="s-badge mid">{fmtProb(r.current_probability, r.market_count ?? 1)}</span>
              <span className="muted">量 ${Math.round((r.volume ?? 0) / 1000)}k · 到期 {r.end_date || "—"}</span>
              <span style={{ flex: 1 }} />
              <Button onClick={() => addTracked.mutate(r)} disabled={addTracked.isPending}>添加</Button>
            </div>
          ))}
        </div>
      )}

      <TrackedMarketsPanel eventType={eventType} />

      <section className="panel">
        <div className="panel-head"><h2>常设观测</h2>
          <span className="muted">未挂接事件的跟踪市场(仪表盘类);挂了事件的曲线在事件详情里看</span>
        </div>
        {predictions.isLoading ? <LoadingState /> : predictions.error ? <ErrorState error={predictions.error} /> : (
          <div className="prediction-grid">
            {(families.data ?? []).map((family) => {
              const chart = buildFamilyChart(family);
              return <PredictionCard key={family.id} title={family.name}
                                     subtitle={`${family.series.length} 个分支`}
                                     data={chart.data} keys={chart.keys} />;
            })}
            {standaloneCards.map((m) => (
              <MarketChartCard key={m.market_id} summary={m} hours={Number(hours)} />
            ))}
            {!(families.data ?? []).length && !standaloneCards.length &&
              <EmptyState title="本线暂无常设市场" />}
          </div>
        )}
      </section>
    </section>
  );
}
