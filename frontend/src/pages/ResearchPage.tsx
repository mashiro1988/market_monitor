import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FolderSearch, RotateCcw, Sparkles } from "lucide-react";
import { api } from "../api/client";
import type { NewsItem, ObsResult, ResearchEventItem, TimelineItem } from "../api/types";
import { Button, PageHeader } from "../components/Controls";
import { EmptyState, ErrorState, LoadingState } from "../components/StateViews";

// ---- 纯函数(测试覆盖,见 ResearchPage.test.tsx)----

/** 观测值 chip 文案(spec §8.1):pending=计算中 / no_data=— / ok=实际分钟数+净变动。 */
export function fmtObs(obs: ObsResult): { text: string; cls: string } {
  if (obs.status === "pending") return { text: "计算中", cls: "s-badge weak" };
  if (obs.status !== "ok" || obs.net_pct == null) return { text: "—", cls: "s-badge none" };
  const sign = obs.net_pct >= 0 ? "+" : "";
  return {
    text: `${obs.actual_minutes ?? "?"}min ${sign}${obs.net_pct.toFixed(2)}%`,
    cls: `s-badge ${obs.net_pct >= 0 ? "up-text" : "down-text"}`,
  };
}

/** 事件行右侧的派生徽章序列(spec §9.1):今日新增 / 确认徽章数 / N 天无新证据。 */
export function eventRowChips(e: ResearchEventItem): { text: string; cls: string }[] {
  const chips: { text: string; cls: string }[] = [];
  if (e.today_new > 0) chips.push({ text: `今日 +${e.today_new}`, cls: "up-text" });
  if (e.badge_count > 0) chips.push({ text: `徽章 ${e.badge_count}`, cls: "s-badge strong" });
  if (e.days_since_last != null && e.days_since_last >= 3)
    chips.push({ text: `${e.days_since_last} 天无新证据`, cls: "ref-neutral" });
  return chips;
}

// ---- 事件详情(时间轴工作台)----

function EventDetail({ eventId, onChanged }: { eventId: number; onChanged: () => void }) {
  const qc = useQueryClient();
  const timeline = useQuery({ queryKey: ["research-timeline", eventId],
                              queryFn: () => api.researchTimeline(eventId) });
  const events = useQuery({ queryKey: ["research-events", "active"],
                            queryFn: () => api.researchEvents({ status: "active" }) });
  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ["research-timeline", eventId] });
    onChanged();
  };
  const patchLink = useMutation({
    mutationFn: ({ id, body }: { id: number; body: Parameters<typeof api.researchLinkPatch>[1] }) =>
      api.researchLinkPatch(id, body),
    onSuccess: invalidate,
  });
  const patchEvent = useMutation({
    mutationFn: (body: Parameters<typeof api.researchEventPatch>[1]) =>
      api.researchEventPatch(eventId, body),
    onSuccess: invalidate,
  });
  const backscan = useMutation({
    mutationFn: (days: number) => api.researchBackscan(eventId, days),
    onSuccess: invalidate,
  });

  if (timeline.isLoading) return <LoadingState label="加载时间轴" />;
  if (timeline.isError || !timeline.data) return <ErrorState error={timeline.error} />;
  const { event, items, pending_relink } = timeline.data;
  const activeOptions = (events.data?.items ?? []).filter((e) => e.id !== eventId);

  return (
    <div className="subsection">
      <div className="subsection-head" style={{ gap: 8, flexWrap: "wrap" }}>
        <span className="subsection-title">#{event.id} {event.name}</span>
        <span className="s-badge none">{event.status === "active" ? "进行中" : "已关闭"}</span>
        {pending_relink > 0 && <span className="s-badge mid">回扫进行中(剩 {pending_relink} 条)</span>}
        <Button kind="secondary" onClick={() => {
          const name = window.prompt("改名", event.name);
          if (name) patchEvent.mutate({ name });
        }}>改名</Button>
        <Button kind="secondary" onClick={() => {
          const kw = window.prompt("免闸关键词(顿号分隔;每个词单独命中都应与本事件相关)",
                                   event.gate_keywords ?? "");
          if (kw !== null) patchEvent.mutate({ gate_keywords: kw, keywords_backscan: true });
        }}>关键词</Button>
        {event.status === "active" ? (
          <Button kind="secondary" onClick={() => {
            const reason = window.prompt("关闭原因", "");
            if (reason !== null) patchEvent.mutate({ status: "closed", closed_reason: reason });
          }}>关闭</Button>
        ) : (
          <Button kind="secondary" onClick={() => patchEvent.mutate({ status: "active" })}>重开</Button>
        )}
        <select title="合并到…" value="" onChange={(ev) => {
          const target = Number(ev.target.value);
          if (target && window.confirm(`把「${event.name}」合并入 #${target}?`))
            patchEvent.mutate({ merge_into_id: target });
        }}>
          <option value="">合并到…</option>
          {activeOptions.map((o) => <option key={o.id} value={o.id}>#{o.id} {o.name}</option>)}
        </select>
        <Button kind="secondary" onClick={() => {
          const days = Number(window.prompt("深回扫天数", "14"));
          if (days > 0) backscan.mutate(days);
        }}><RotateCcw size={14} />深回扫</Button>
      </div>
      {items.length === 0 && <EmptyState title="时间轴暂无证据" />}
      {items.map((it: TimelineItem) => {
        const obs = fmtObs(it.obs);
        return (
          <div key={it.link.id} className="evidence-row" style={{ alignItems: "center", gap: 6 }}>
            <span style={{ whiteSpace: "nowrap" }}>{it.news.timestamp_bj?.slice(5, 16)}</span>
            <span className="ref-neutral">{it.news.source}</span>
            {it.news.news_direction && (
              <span className={it.news.news_direction === "利多" ? "up-text"
                               : it.news.news_direction === "利空" ? "down-text" : "ref-neutral"}>
                {it.news.news_direction}
              </span>
            )}
            <span className={obs.cls} title={`观测:${it.obs_symbol} 基线→终点实际跨度`}>{obs.text}</span>
            {it.driver_badge && (
              <span className="s-badge strong"
                    title={`人工确认为标注窗口 driver(${it.driver_badge.symbol})`}>
                driver {it.driver_badge.change_pct != null
                  ? `${it.driver_badge.change_pct > 0 ? "+" : ""}${it.driver_badge.change_pct.toFixed(2)}%` : ""}
              </span>
            )}
            {it.score_miss && (
              <span className="s-badge mid"
                    title="llm_importance 低于闸门线却被确认挂上——打分校准素材(spec §8.3)">
                评分失手 {it.news.llm_importance}分
              </span>
            )}
            {it.link.link_source === "auto" && (
              <span className="ref-neutral" title={`模型挂接 conf=${it.link.confidence}`}>auto</span>
            )}
            <span style={{ flex: 1 }}>{it.news.title}</span>
            <select title="改归属" value="" onChange={(ev) => {
              const target = Number(ev.target.value);
              if (target) patchLink.mutate({ id: it.link.id, body: { event_id: target } });
            }}>
              <option value="">改归属…</option>
              {activeOptions.map((o) => <option key={o.id} value={o.id}>#{o.id} {o.name}</option>)}
            </select>
            <Button kind="secondary" onClick={() => {
              const reason = window.prompt("摘下原因", "");
              if (reason !== null)
                patchLink.mutate({ id: it.link.id, body: { detached: true, detach_reason: reason } });
            }}>摘下</Button>
          </div>
        );
      })}
    </div>
  );
}

// ---- 缓冲区(过闸未挂)+ 立案表单 ----

function BufferTab({ onCreated }: { onCreated: () => void }) {
  const [days, setDays] = useState(3);
  const [minScore, setMinScore] = useState<number | "">("");
  const [driversOnly, setDriversOnly] = useState(false);
  const [picked, setPicked] = useState<number[]>([]);
  const [name, setName] = useState("");
  const [keywords, setKeywords] = useState("");
  const buffer = useQuery({
    queryKey: ["research-buffer", days, minScore, driversOnly],
    queryFn: () => api.researchBuffer({
      days, drivers_only: driversOnly,
      ...(minScore === "" ? {} : { min_score: minScore }),
    }),
  });
  const events = useQuery({ queryKey: ["research-events", "active"],
                            queryFn: () => api.researchEvents({ status: "active" }) });
  const create = useMutation({
    mutationFn: () => api.researchEventCreate({
      name, news_ids: picked, gate_keywords: keywords || null, created_from: "manual" }),
    onSuccess: () => { setPicked([]); setName(""); setKeywords(""); onCreated(); },
  });
  const suggest = useMutation({
    mutationFn: () => api.researchSuggestKeywords({ name, news_ids: picked }),
    onSuccess: (r) => setKeywords((r.keywords ?? []).join("、")),
  });
  const attach = useMutation({
    mutationFn: (eventId: number) =>
      Promise.all(picked.map((nid) => api.researchLinkCreate({ event_id: eventId, news_id: nid }))),
    onSuccess: () => { setPicked([]); onCreated(); },
  });
  return (
    <div className="panel">
      <div className="panel-head" style={{ gap: 8, flexWrap: "wrap" }}>
        <h2>缓冲区(过闸未挂)</h2>
        <label>天数 <input type="number" value={days} min={1} max={30} style={{ width: 48 }}
                          onChange={(ev) => setDays(Number(ev.target.value) || 3)} /></label>
        <label>最低分 <input type="number" value={minScore} style={{ width: 48 }}
                            onChange={(ev) => setMinScore(ev.target.value === "" ? "" : Number(ev.target.value))} /></label>
        <label><input type="checkbox" checked={driversOnly}
                      onChange={(ev) => setDriversOnly(ev.target.checked)} /> 仅看已确认 driver</label>
      </div>
      {picked.length > 0 && (
        <div className="subsection" style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
          <span>已选 {picked.length} 条 →</span>
          <input placeholder="事件名(一个待重定价的变量;中文短名≤20字)" value={name}
                 onChange={(ev) => setName(ev.target.value)} style={{ width: 260 }} />
          <input placeholder="免闸关键词(顿号分隔,可 AI 建议)" value={keywords}
                 onChange={(ev) => setKeywords(ev.target.value)} style={{ width: 220 }} />
          <Button kind="secondary" disabled={!name || suggest.isPending}
                  onClick={() => suggest.mutate()}><Sparkles size={14} />AI 建议</Button>
          <Button disabled={!name || create.isPending} onClick={() => create.mutate()}>立事件</Button>
          <select value="" onChange={(ev) => { const id = Number(ev.target.value); if (id) attach.mutate(id); }}>
            <option value="">挂到事件…</option>
            {(events.data?.items ?? []).map((o) => <option key={o.id} value={o.id}>#{o.id} {o.name}</option>)}
          </select>
        </div>
      )}
      {buffer.isLoading && <LoadingState label="加载缓冲区" />}
      {buffer.isError && <ErrorState error={buffer.error} />}
      {(buffer.data?.items ?? []).map((n: NewsItem) => (
        <div key={n.id} className="evidence-row" style={{ gap: 6, alignItems: "center" }}>
          <input type="checkbox" checked={picked.includes(n.id)}
                 onChange={(ev) => setPicked(ev.target.checked
                   ? [...picked, n.id] : picked.filter((x) => x !== n.id))} />
          <span>{n.timestamp_bj?.slice(5, 16)}</span>
          <span className="ref-neutral">{n.source}</span>
          <span className="ref-neutral">{n.llm_importance ?? "—"}分</span>
          <span style={{ flex: 1 }}>{n.title}</span>
        </div>
      ))}
      {!buffer.isLoading && (buffer.data?.items ?? []).length === 0 && <EmptyState title="缓冲区为空" />}
    </div>
  );
}

// ---- 旧事重提(沉睡监听,spec §7)----

function RevivalTab({ onChanged }: { onChanged: () => void }) {
  const revival = useQuery({ queryKey: ["research-revival"], queryFn: () => api.researchRevival() });
  const reopen = useMutation({
    mutationFn: (eventId: number) => api.researchEventPatch(eventId, { status: "active" }),
    onSuccess: onChanged,
  });
  return (
    <div className="panel">
      <div className="panel-head"><h2>旧事重提(近 7 天命中已关闭事件关键词)</h2></div>
      {revival.isLoading && <LoadingState label="扫描沉睡事件" />}
      {(revival.data?.items ?? []).length === 0 && !revival.isLoading &&
        <EmptyState title="没有沉睡事件被唤醒" />}
      {(revival.data?.items ?? []).map((r, i) => (
        <div key={i} className="evidence-row" style={{ gap: 6, alignItems: "center" }}>
          <span>{r.news.timestamp_bj?.slice(5, 16)}</span>
          <span className="s-badge mid">『{r.event_name}』</span>
          <span style={{ flex: 1 }}>{r.news.title}</span>
          <Button kind="secondary" onClick={() => reopen.mutate(r.event_id)}>重开该事件</Button>
        </div>
      ))}
    </div>
  );
}

// ---- 主页面 ----

export function ResearchPage() {
  const qc = useQueryClient();
  const [selected, setSelected] = useState<number | null>(null);
  const [tab, setTab] = useState<"events" | "buffer" | "revival">("events");
  const [q, setQ] = useState("");
  const events = useQuery({ queryKey: ["research-events", "all", q],
                            queryFn: () => api.researchEvents(q ? { q } : {}) });
  const stats = useQuery({ queryKey: ["research-stats"], queryFn: () => api.researchStats(),
                           refetchInterval: 60_000 });
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["research-events"] });
    void qc.invalidateQueries({ queryKey: ["research-buffer"] });
    void qc.invalidateQueries({ queryKey: ["research-revival"] });
  };

  const rows = events.data?.items ?? [];
  const active = rows.filter((r) => r.status === "active");
  const closed = rows.filter((r) => r.status === "closed");

  return (
    <section>
      <PageHeader title="研究" subtitle="宏观事件池:人立案、机器挂证据、价格来验证" />
      {stats.data && (
        <div className="panel" style={{ display: "flex", gap: 16, padding: 8 }}>
          <span title="过闸新闻里模型挂上的占比(并行期观察,spec §13.3)">
            挂接率 {stats.data.link_rate != null ? `${(stats.data.link_rate * 100).toFixed(0)}%` : "—"}
          </span>
          <span title="模型挂的里被人工改归属/摘下的占比;连续3天<20%才删旧 topic 槽位">
            纠错率 {stats.data.correction_rate != null ? `${(stats.data.correction_rate * 100).toFixed(0)}%` : "—"}
          </span>
          {stats.data.pending_relink > 0 && (
            <span className="ref-neutral">回扫待处理 {stats.data.pending_relink} 条</span>
          )}
          <span style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
            <Button kind={tab === "events" ? "primary" : "ghost"} onClick={() => setTab("events")}>事件</Button>
            <Button kind={tab === "buffer" ? "primary" : "ghost"} onClick={() => setTab("buffer")}>缓冲区</Button>
            <Button kind={tab === "revival" ? "primary" : "ghost"} onClick={() => setTab("revival")}>旧事重提</Button>
          </span>
        </div>
      )}
      {tab === "buffer" && <BufferTab onCreated={refresh} />}
      {tab === "revival" && <RevivalTab onChanged={refresh} />}
      {tab === "events" && (
        <div className="panel">
          <div className="panel-head">
            <h2><FolderSearch size={16} /> 进行中({active.length})</h2>
            <input placeholder="搜事件名/关键词(含已关闭)" value={q}
                   onChange={(ev) => setQ(ev.target.value)} />
          </div>
          {events.isLoading && <LoadingState label="加载事件" />}
          {events.isError && <ErrorState error={events.error} />}
          {!events.isLoading && active.length === 0 &&
            <EmptyState title="事件池为空:去缓冲区或标注页立第一个事件" />}
          {active.map((e) => (
            <div key={e.id}
                 className={`evidence-row${selected === e.id ? " self" : ""}`}
                 style={{ cursor: "pointer", alignItems: "center", gap: 8 }}
                 onClick={() => setSelected(selected === e.id ? null : e.id)}>
              <span style={{ flex: 1 }}>#{e.id} {e.name}</span>
              <span className="ref-neutral">证据 {e.evidence_count}</span>
              {eventRowChips(e).map((c, i) => <span key={i} className={c.cls}>{c.text}</span>)}
            </div>
          ))}
          {selected != null && <EventDetail eventId={selected} onChanged={refresh} />}
          {closed.length > 0 && (
            <details>
              <summary>已关闭({closed.length})</summary>
              {closed.map((e) => (
                <div key={e.id} className="evidence-row" style={{ cursor: "pointer", gap: 8 }}
                     onClick={() => setSelected(selected === e.id ? null : e.id)}>
                  <span style={{ flex: 1 }}>#{e.id} {e.name}</span>
                  <span className="ref-neutral">{e.closed_reason ?? ""}</span>
                  {e.merged_into_id != null && <span className="ref-neutral">→ #{e.merged_into_id}</span>}
                </div>
              ))}
            </details>
          )}
        </div>
      )}
    </section>
  );
}

export default ResearchPage;
