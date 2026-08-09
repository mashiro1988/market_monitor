import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, FolderSearch, Search } from "lucide-react";
import { api, apiErrorText } from "../api/client";
import type { NewsItem, ObsResult, ResearchEventItem, TimelineItem } from "../api/types";
import { Button, PageHeader } from "../components/Controls";
import { EmptyState, ErrorState, LoadingState } from "../components/StateViews";

// ---- 纯函数(测试覆盖,见 ResearchPage.test.tsx)----

/** 大波动高亮阈值(%):|10min 净变动| ≥ 此值即醒目显示(ui-redesign §3,纯显示常量)。 */
export const OBS_HOT_PCT = 0.3;

export function isHotMove(obs: ObsResult): boolean {
  return obs.status === "ok" && obs.net_pct != null && Math.abs(obs.net_pct) >= OBS_HOT_PCT;
}

/** 观测值 chip 文案(spec §8.1):pending=计算中 / no_data=— / ok=实际分钟数+净变动。 */
export function fmtObs(obs: ObsResult): { text: string; cls: string } {
  if (obs.status === "pending") return { text: "计算中", cls: "s-badge weak" };
  if (obs.status !== "ok" || obs.net_pct == null) return { text: "—", cls: "s-badge none" };
  const sign = obs.net_pct >= 0 ? "+" : "";
  const tone = obs.net_pct >= 0 ? "up-text" : "down-text";
  return {
    text: `${obs.actual_minutes ?? "?"}min ${sign}${obs.net_pct.toFixed(2)}%`,
    cls: `s-badge ${tone}${isHotMove(obs) ? " obs-hot" : ""}`,
  };
}

/** 评分显示:空分是"评分调用失败",写"未评分"——旧的"—分"会被读成"一分"。 */
export function fmtScore(score: number | null | undefined): { text: string; cls: string } {
  return score == null
    ? { text: "未评分", cls: "rp-score muted" }
    : { text: `${score}分`, cls: "rp-score" };
}

/** 事件卡片统计行(ui-redesign §2):徽章固定首位(有才出),今日/昨日恒显示(0 值弱化),
 *  久无证据追加提示。顺序固定 = 跨卡片读数一致。 */
export function eventCardChips(e: ResearchEventItem): { text: string; cls: string }[] {
  const chips: { text: string; cls: string }[] = [];
  if (e.badge_count > 0) chips.push({ text: `徽章 ${e.badge_count}`, cls: "s-badge strong" });
  chips.push({ text: `今日 +${e.today_new}`, cls: e.today_new > 0 ? "up-text" : "muted" });
  chips.push({ text: `昨日 +${e.yesterday_new}`, cls: e.yesterday_new > 0 ? "rp-yday" : "muted" });
  if (e.days_since_last != null && e.days_since_last >= 3)
    chips.push({ text: `${e.days_since_last} 天无新证据`, cls: "ref-neutral" });
  return chips;
}

/** "2026-08-03 12:00:00"(北京时间字符串) → "08-03 12:00";空值给短横。 */
export function fmtBjShort(bj: string | null | undefined): string {
  return bj ? bj.slice(5, 16) : "—";
}

// ---- 通用小件 ----

/** 点外面就收起的下拉菜单(管理菜单/改归属菜单共用)。 */
function Dropdown({ label, children, align = "left" }:
                  { label: React.ReactNode; children: React.ReactNode; align?: "left" | "right" }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (ev: MouseEvent) => {
      if (ref.current && !ref.current.contains(ev.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);
  return (
    <div className="rp-dropdown" ref={ref}>
      <button type="button" className="button secondary" onClick={() => setOpen((v) => !v)}>
        {label}<ChevronDown size={13} />
      </button>
      {open && (
        <div className={`rp-menu ${align === "right" ? "right" : ""}`} onClick={() => setOpen(false)}>
          {children}
        </div>
      )}
    </div>
  );
}

function MenuItem({ children, onClick }: { children: React.ReactNode; onClick: () => void }) {
  return <button type="button" className="rp-menu-item" onClick={onClick}>{children}</button>;
}

function FilterSelect<T extends string | number>({ label, value, options, onChange }:
    { label: string; value: T; options: { label: string; value: T }[]; onChange: (v: T) => void }) {
  return (
    <label className="rp-filter">
      <span>{label}</span>
      <select value={String(value)}
              onChange={(ev) => {
                const opt = options.find((o) => String(o.value) === ev.target.value);
                if (opt) onChange(opt.value);
              }}>
        {options.map((o) => <option key={String(o.value)} value={String(o.value)}>{o.label}</option>)}
      </select>
    </label>
  );
}

const DAY_OPTIONS = [
  { label: "全部", value: 0 }, { label: "近 1 天", value: 1 },
  { label: "近 3 天", value: 3 }, { label: "近 7 天", value: 7 },
];
const SCORE_OPTIONS = [
  { label: "不限", value: 0 }, { label: "≥ 6", value: 6 },
  { label: "≥ 7", value: 7 }, { label: "≥ 8", value: 8 },
];
const MOVE_OPTIONS = [
  { label: "不限", value: 0 }, { label: "≥ 0.1%", value: 0.1 }, { label: "≥ 0.2%", value: 0.2 },
  { label: "≥ 0.3%", value: 0.3 }, { label: "≥ 0.5%", value: 0.5 },
];
const PAGE_SIZE = 50;

// ---- 事件详情(时间轴工作台)----

function EventDetail({ eventId, onChanged }: { eventId: number; onChanged: () => void }) {
  const qc = useQueryClient();
  const [days, setDays] = useState(0);
  const [minScore, setMinScore] = useState(0);
  const [minMove, setMinMove] = useState(0);
  const [page, setPage] = useState(1);
  const filters = {
    ...(days ? { days } : {}),
    ...(minScore ? { min_score: minScore } : {}),
    ...(minMove ? { min_abs_move: minMove } : {}),
    page, page_size: PAGE_SIZE,
  };
  // 缓存键必须含全部筛选与页码,否则改条件会渲染旧缓存页
  const timeline = useQuery({
    queryKey: ["research-timeline", eventId, days, minScore, minMove, page],
    queryFn: () => api.researchTimeline(eventId, filters),
    // 改筛选/翻页时留住上一页,避免整块塌成"加载中"再弹回来
    placeholderData: (previous) => previous,
  });
  const events = useQuery({ queryKey: ["research-events", "active"],
                            queryFn: () => api.researchEvents({ status: "active" }) });
  // 失败必须可见:改名/关键词/关闭/合并/回扫失败若静默,页面看着像"点了没反应"
  const [actionError, setActionError] = useState("");
  const invalidate = () => {
    setActionError("");
    void qc.invalidateQueries({ queryKey: ["research-timeline", eventId] });
    onChanged();
  };
  const resetPage = <T,>(setter: (v: T) => void) => (v: T) => { setter(v); setPage(1); };
  const patchLink = useMutation({
    mutationFn: ({ id, body }: { id: number; body: Parameters<typeof api.researchLinkPatch>[1] }) =>
      api.researchLinkPatch(id, body),
    onSuccess: invalidate,
    onError: (err) => setActionError(apiErrorText(err, "挂接修改失败")),
  });
  const patchEvent = useMutation({
    mutationFn: (body: Parameters<typeof api.researchEventPatch>[1]) =>
      api.researchEventPatch(eventId, body),
    onSuccess: invalidate,
    onError: (err) => setActionError(apiErrorText(err, "事件操作失败")),
  });
  const backscan = useMutation({
    mutationFn: (days_: number) => api.researchBackscan(eventId, days_),
    onSuccess: invalidate,
    onError: (err) => setActionError(apiErrorText(err, "回扫失败")),
  });

  // 加载/报错也必须套在 .rp-detail 里:否则拿不到展开区的上边距,会直接贴住上面的卡片
  if (timeline.isLoading && !timeline.data)
    return <div className="rp-detail"><LoadingState label="加载时间轴" /></div>;
  if (timeline.isError || !timeline.data)
    return <div className="rp-detail"><ErrorState error={timeline.error} /></div>;
  const { event, items, pending_relink, total } = timeline.data;
  const activeOptions = (events.data?.items ?? []).filter((e) => e.id !== eventId);
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="rp-detail">
      <div className="rp-detail-head">
        <span className="subsection-title">#{event.id} {event.name}</span>
        <span className="s-badge none">{event.status === "active" ? "进行中" : "已关闭"}</span>
        {pending_relink > 0 && <span className="s-badge mid">回扫进行中(剩 {pending_relink} 条)</span>}
        {actionError && <span style={{ color: "var(--danger)" }}>{actionError}</span>}
        <span style={{ flex: 1 }} />
        <Dropdown label="管理" align="right">
          <MenuItem onClick={() => {
            const name = window.prompt("改名", event.name);
            if (name) patchEvent.mutate({ name });
          }}>改名</MenuItem>
          <MenuItem onClick={() => {
            const kw = window.prompt("免闸关键词(顿号分隔;每个词单独命中都应与本事件相关)",
                                     event.gate_keywords ?? "");
            if (kw !== null) patchEvent.mutate({ gate_keywords: kw, keywords_backscan: true });
          }}>关键词</MenuItem>
          {event.status === "active" ? (
            <MenuItem onClick={() => {
              const reason = window.prompt("关闭原因", "");
              if (reason !== null) patchEvent.mutate({ status: "closed", closed_reason: reason });
            }}>关闭事件</MenuItem>
          ) : (
            <MenuItem onClick={() => patchEvent.mutate({ status: "active" })}>重开事件</MenuItem>
          )}
          <div className="rp-menu-sep" />
          {activeOptions.length === 0
            ? <div className="rp-menu-hint">没有其它进行中事件可合并</div>
            : <>
                <div className="rp-menu-hint">合并到…</div>
                {activeOptions.map((o) => (
                  <MenuItem key={o.id} onClick={() => {
                    if (window.confirm(`把「${event.name}」合并入 #${o.id} ${o.name}?`))
                      patchEvent.mutate({ merge_into_id: o.id });
                  }}>#{o.id} {o.name}</MenuItem>
                ))}
              </>}
          <div className="rp-menu-sep" />
          <MenuItem onClick={() => {
            const d = Number(window.prompt(
              "深回扫天数(全池重扫:清掉这些天的挂接游标,按每 5 分钟 200 条排队重考)", "14"));
            if (d > 0) backscan.mutate(d);
          }}>深回扫…</MenuItem>
        </Dropdown>
      </div>

      <div className="rp-filters">
        <FilterSelect label="时间窗" value={days} options={DAY_OPTIONS} onChange={resetPage(setDays)} />
        <FilterSelect label="分数" value={minScore} options={SCORE_OPTIONS} onChange={resetPage(setMinScore)} />
        <FilterSelect label="10min 波动" value={minMove} options={MOVE_OPTIONS} onChange={resetPage(setMinMove)} />
        <span style={{ flex: 1 }} />
        <span className="rp-pager">
          <span className="muted">共 {total} 条</span>
          <button type="button" className="link-button" disabled={page <= 1}
                  onClick={() => setPage((p) => Math.max(1, p - 1))}>‹</button>
          <span>{page}/{pageCount}</span>
          <button type="button" className="link-button" disabled={page >= pageCount}
                  onClick={() => setPage((p) => Math.min(pageCount, p + 1))}>›</button>
        </span>
      </div>

      {items.length === 0 && <EmptyState title="没有符合筛选条件的证据" />}
      {items.map((it: TimelineItem) => {
        const obs = fmtObs(it.obs);
        const score = fmtScore(it.news.llm_importance);
        return (
          <div key={it.link.id} className="rp-row rp-row-timeline">
            <span className="rp-time">{fmtBjShort(it.news.timestamp_bj)}</span>
            <span className="rp-source">{it.news.source}</span>
            <span className={score.cls}>{score.text}</span>
            <span className={it.news.news_direction === "利多" ? "up-text"
                             : it.news.news_direction === "利空" ? "down-text" : "muted"}>
              {it.news.news_direction ?? "—"}
            </span>
            <span className={obs.cls} title={`观测:${it.obs_symbol} 基线→终点实际跨度`}>{obs.text}</span>
            <span className="rp-title" title={it.news.title}>{it.news.title}</span>
            <span className="rp-marks">
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
                  评分失手
                </span>
              )}
              {it.link.link_source === "auto" && (
                <span className="muted" title={`模型挂接 conf=${it.link.confidence}`}>auto</span>
              )}
            </span>
            <Dropdown label="改归属" align="right">
              {activeOptions.map((o) => (
                <MenuItem key={o.id}
                          onClick={() => patchLink.mutate({ id: it.link.id, body: { event_id: o.id } })}>
                  #{o.id} {o.name}
                </MenuItem>
              ))}
              {activeOptions.length > 0 && <div className="rp-menu-sep" />}
              <MenuItem onClick={() => {
                const reason = window.prompt("摘回缓冲区的原因(可留空)", "手动摘回");
                if (reason !== null)
                  patchLink.mutate({ id: it.link.id, body: { detached: true, detach_reason: reason } });
              }}>摘回缓冲区</MenuItem>
            </Dropdown>
          </div>
        );
      })}
    </div>
  );
}

// 缓冲区已并入新闻快讯(勾选立案在那边的「紧凑 + 仅看未挂事件」下,
// docs/specs/2026-08-06-buffer-into-news-page-design.md);这里不再有缓冲区页签。

// ---- 旧事重提(沉睡监听,spec §7)----

function RevivalTab({ onChanged }: { onChanged: () => void }) {
  const revival = useQuery({ queryKey: ["research-revival"], queryFn: () => api.researchRevival() });
  const [reopenError, setReopenError] = useState("");
  const reopen = useMutation({
    mutationFn: (eventId: number) => api.researchEventPatch(eventId, { status: "active" }),
    onSuccess: () => { setReopenError(""); onChanged(); },
    onError: (err) => setReopenError(apiErrorText(err, "重开失败")),
  });
  return (
    <div className="panel">
      <div className="rp-filters rp-filters-head">
        <h2>旧事重提</h2>
        <span className="muted">近 7 天命中已关闭事件关键词的新闻</span>
        {reopenError && <span style={{ color: "var(--danger)" }}>{reopenError}</span>}
      </div>
      {revival.isLoading && <LoadingState label="扫描沉睡事件" />}
      {(revival.data?.items ?? []).length === 0 && !revival.isLoading &&
        <EmptyState title="没有沉睡事件被唤醒" />}
      {(revival.data?.items ?? []).map((r, i) => (
        <div key={i} className="rp-row rp-row-revival">
          <span className="rp-time">{fmtBjShort(r.news.timestamp_bj)}</span>
          <span className="s-badge mid">『{r.event_name}』</span>
          <span className="rp-title" title={r.news.title}>{r.news.title}</span>
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
  const [tab, setTab] = useState<"events" | "revival">("events");
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
      <PageHeader title="宏观事件池" />
      <div className="rp-topbar">
        {stats.data?.link_rate != null && (
          <span className="muted" title="过闸新闻里模型挂上的占比(并行期观察,spec §13.3)">
            挂接率 {(stats.data.link_rate * 100).toFixed(0)}%
          </span>
        )}
        {(stats.data?.pending_relink ?? 0) > 0 && (
          <span className="muted" title="回扫队列:每 5 分钟消化 200 条">
            回扫待处理 {stats.data?.pending_relink} 条
          </span>
        )}
        <span style={{ flex: 1 }} />
        <Button kind={tab === "events" ? "primary" : "ghost"} onClick={() => setTab("events")}>事件</Button>
        <Button kind={tab === "revival" ? "primary" : "ghost"} onClick={() => setTab("revival")}>旧事重提</Button>
      </div>

      {tab === "revival" && <RevivalTab onChanged={refresh} />}
      {tab === "events" && (
        <div className="panel">
          <div className="panel-head">
            <h2><FolderSearch size={16} /> 进行中({active.length})</h2>
            <span style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <a href="/news" className="muted" style={{ fontSize: 13 }}
                 title="缓冲区已并入新闻快讯:在那边选「紧凑 + 仅看未挂事件」勾选立案">
                去新闻快讯挑证据 →
              </a>
              <input placeholder="搜事件名/关键词(含已关闭)" value={q}
                     onChange={(ev) => setQ(ev.target.value)} />
            </span>
          </div>
          {events.isLoading && <LoadingState label="加载事件" />}
          {events.isError && <ErrorState error={events.error} />}
          {!events.isLoading && active.length === 0 &&
            <EmptyState title="事件池为空:去缓冲区或标注页立第一个事件" />}
          <div className="rp-cards">
            {active.map((e) => (
              <button type="button" key={e.id}
                      className={`rp-card${selected === e.id ? " selected" : ""}`}
                      onClick={() => setSelected(selected === e.id ? null : e.id)}>
                <span className="rp-card-name" title={e.name}>#{e.id} {e.name}</span>
                <span className="rp-card-chips">
                  {eventCardChips(e).map((c, i) => <span key={i} className={c.cls}>{c.text}</span>)}
                </span>
                <span className="rp-card-foot">
                  证据 {e.evidence_count} · 最新 {fmtBjShort(e.last_evidence_bj)}
                </span>
              </button>
            ))}
          </div>
          {selected != null && <EventDetail eventId={selected} onChanged={refresh} />}
          {closed.length > 0 && (
            <details className="rp-closed">
              <summary>已关闭({closed.length})</summary>
              {closed.map((e) => (
                <div key={e.id} className="rp-row rp-row-closed"
                     onClick={() => setSelected(selected === e.id ? null : e.id)}>
                  <span className="rp-title">#{e.id} {e.name}</span>
                  <span className="muted">{e.closed_reason ?? ""}</span>
                  {e.merged_into_id != null && <span className="muted">→ #{e.merged_into_id}</span>}
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
