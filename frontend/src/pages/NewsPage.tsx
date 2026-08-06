import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { api } from "../api/client";
import type { NewsItem } from "../api/types";
import { Button, PageHeader, SelectControl, TextInput } from "../components/Controls";
import { EmptyState, ErrorState, LoadingState } from "../components/StateViews";
import { fmtScore, fmtBjShort } from "./ResearchPage";

const importantOptions = [
  { label: "全部", value: "all" },
  { label: "仅重要", value: "important" },
  { label: "仅非重要", value: "normal" }
];

// 7 天/30 天是缓冲区并进来时补的:原缓冲区支持近 30 天,只留 72 小时会丢能力
// (后端 hours_back 上限本就是 24*30)
const hourOptions = [
  { label: "6小时", value: "6" },
  { label: "24小时", value: "24" },
  { label: "72小时", value: "72" },
  { label: "7天", value: "168" },
  { label: "30天", value: "720" }
];

// 0 = 不限:未评分(评分调用失败)也显示;设了门槛才滤掉(buffer-into-news design §0)
const scoreOptions = [
  { label: "不限(含未评分)", value: "0" },
  ...Array.from({ length: 10 }, (_, i) => ({ label: `${i + 1}+`, value: String(i + 1) })),
];

const viewOptions = [
  { label: "卡片", value: "card" },
  { label: "紧凑", value: "compact" },
];

const PAGE_SIZE = 50;

function useDebouncedValue(value: string, delayMs: number) {
  const [debounced, setDebounced] = useState(value);

  useEffect(() => {
    const id = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(id);
  }, [value, delayMs]);

  return debounced;
}

function NewsCard({ item }: { item: NewsItem }) {
  const score = item.llm_importance ?? 0;
  return (
    <details className="news-card">
      <summary>
        <div className="news-line">
          <span className={`score s${Math.min(10, score)}`}>{item.llm_importance ?? "—"}</span>
          <strong>{item.title}</strong>
        </div>
        <div className="news-meta">
          <span>{item.timestamp_bj}</span>
          <span>{item.source}</span>
          {item.is_jin10_important ? <span className="badge hot">Jin10 重要</span> : null}
          {item.categories ? <span>{item.categories}</span> : null}
        </div>
      </summary>
      {item.llm_importance_reason ? <p className="reason">{item.llm_importance_reason}</p> : null}
      {item.content ? <p>{item.content}</p> : null}
      {item.url ? <a href={item.url} target="_blank" rel="noreferrer">原文链接</a> : null}
    </details>
  );
}

/** 立案操作条:勾了几条未挂事件的新闻后,直接在这里立事件或挂到已有事件
 *  (原事件池「缓冲区」页签整体搬来,buffer-into-news design §2.1)。 */
function TriageBar({ picked, onDone }: { picked: number[]; onDone: () => void }) {
  const [name, setName] = useState("");
  const [keywords, setKeywords] = useState("");
  const [target, setTarget] = useState("");
  const events = useQuery({ queryKey: ["research-events", "active"],
                            queryFn: () => api.researchEvents({ status: "active" }) });
  const finish = () => { setName(""); setKeywords(""); setTarget(""); onDone(); };
  const create = useMutation({
    mutationFn: () => api.researchEventCreate({
      name, news_ids: picked, gate_keywords: keywords || null, created_from: "manual" }),
    onSuccess: finish,
  });
  const suggest = useMutation({
    mutationFn: () => api.researchSuggestKeywords({ name, news_ids: picked }),
    onSuccess: (r) => setKeywords((r.keywords ?? []).join("、")),
  });
  const attach = useMutation({
    mutationFn: (eventId: number) =>
      Promise.all(picked.map((nid) => api.researchLinkCreate({ event_id: eventId, news_id: nid }))),
    onSuccess: finish,
  });
  return (
    <div className="rp-pickbar">
      <span>已选 {picked.length} 条 →</span>
      <input placeholder="事件名(一个待重定价的变量;中文短名≤20字)" value={name}
             onChange={(ev) => setName(ev.target.value)} style={{ width: 260 }} />
      <input placeholder="免闸关键词(顿号分隔,可 AI 建议)" value={keywords}
             onChange={(ev) => setKeywords(ev.target.value)} style={{ width: 220 }} />
      <Button kind="secondary" disabled={!name || suggest.isPending}
              onClick={() => suggest.mutate()}>AI 建议</Button>
      <Button disabled={!name || create.isPending} onClick={() => create.mutate()}>立事件</Button>
      <select value={target} onChange={(ev) => {
        const id = Number(ev.target.value);
        setTarget(ev.target.value);
        if (id) attach.mutate(id);
      }}>
        <option value="">挂到事件…</option>
        {(events.data?.items ?? []).map((o) => (
          <option key={o.id} value={o.id}>#{o.id} {o.name}</option>
        ))}
      </select>
    </div>
  );
}

function CompactRow({ item, picked, onPick }:
                    { item: NewsItem; picked: boolean | null; onPick?: (checked: boolean) => void }) {
  const score = fmtScore(item.llm_importance);
  const body = (
    <>
      {picked !== null && (
        <input type="checkbox" checked={picked}
               onChange={(ev) => onPick?.(ev.target.checked)} />
      )}
      <span className="rp-time">{fmtBjShort(item.timestamp_bj)}</span>
      <span className="rp-source">{item.source}</span>
      <span className={score.cls}>{score.text}</span>
      <span className={item.news_direction === "利多" ? "up-text"
                       : item.news_direction === "利空" ? "down-text" : "muted"}>
        {item.news_direction ?? "—"}
      </span>
      <span className="rp-title" title={item.llm_importance_reason ?? item.title}>{item.title}</span>
    </>
  );
  const cls = `rp-row ${picked !== null ? "rp-row-triage" : "rp-row-news"}`;
  return picked !== null ? <label className={cls}>{body}</label> : <div className={cls}>{body}</div>;
}

export function NewsPage() {
  const qc = useQueryClient();
  const [source, setSource] = useState("");
  const [importance, setImportance] = useState("5");
  const [hours, setHours] = useState("24");
  const [jin10Importance, setJin10Importance] = useState("all");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [view, setView] = useState<"card" | "compact">("card");
  const [bufferOnly, setBufferOnly] = useState(false);
  const [picked, setPicked] = useState<number[]>([]);
  const debouncedSearch = useDebouncedValue(search, 350);
  const normalizedSearch = debouncedSearch.trim();
  const sources = useQuery({ queryKey: ["news-sources"], queryFn: api.newsSources });
  const news = useQuery({
    queryKey: ["news", source, importance, hours, jin10Importance, normalizedSearch, page, bufferOnly],
    queryFn: () => api.news({
      sources: source ? [source] : undefined,
      min_llm_importance: Number(importance),
      hours_back: Number(hours),
      jin10_importance: jin10Importance,
      search: normalizedSearch || undefined,
      page,
      page_size: PAGE_SIZE,
      ...(bufferOnly ? { buffer_only: true } : {})
    }),
    placeholderData: (previous) => previous
  });
  // 立事件/挂接后:重取新闻(已挂的会退出缓冲视图)与事件池缓存
  const afterTriage = () => {
    setPicked([]);
    void qc.invalidateQueries({ queryKey: ["news"] });
    void qc.invalidateQueries({ queryKey: ["research-events"] });
  };

  // 下拉选项基于后端 /api/news/sources（即 config.NEWS_SOURCES 启用项），不再硬编码 bloomberg。
  const sourceOptions = useMemo(() => {
    const opts = [{ label: "全部", value: "" }];
    for (const s of sources.data ?? []) {
      const langTag = s.language === "zh" ? "中文" : s.language === "en" ? "英文" : s.language.toUpperCase();
      opts.push({ label: `${langTag} ${s.name}`, value: s.key });
    }
    return opts;
  }, [sources.data]);

  // 中英分栏完全按 language 字段切分；新加源不需要再改这里。
  const items = news.data?.items ?? [];
  const zh = items.filter((item) => item.language === "zh");
  const en = items.filter((item) => item.language === "en");
  const total = news.data?.total ?? 0;
  const canTriage = bufferOnly && view === "compact";   // 勾选立案只在紧凑视图给
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const currentPage = page;

  return (
    <section>
      <PageHeader title="新闻快讯" subtitle={`共 ${news.data?.total ?? 0} 条 · 中文 ${news.data?.zh_count ?? 0} · 英文 ${news.data?.en_count ?? 0}`} />
      <div className="toolbar">
        <SelectControl label="新闻源" value={source} onChange={(value) => { setSource(value); setPage(1); }} options={sourceOptions} />
        <SelectControl label="LLM 分数" value={importance} onChange={(value) => { setImportance(value); setPage(1); }} options={scoreOptions} />
        <SelectControl label="回溯" value={hours} onChange={(value) => { setHours(value); setPage(1); }} options={hourOptions} />
        <SelectControl label="Jin10" value={jin10Importance} onChange={(value) => { setJin10Importance(value); setPage(1); }} options={importantOptions} />
        <TextInput label="关键词" value={search} onChange={(value) => { setSearch(value); setPage(1); }} placeholder="标题或正文" />
        <SelectControl label="视图" value={view} onChange={(value) => setView(value as "card" | "compact")} options={viewOptions} />
        <label className="field">
          <span>范围</span>
          <label className="rp-check">
            <input type="checkbox" checked={bufferOnly}
                   onChange={(ev) => { setBufferOnly(ev.target.checked); setPage(1); }} />
            仅看未挂事件
          </label>
        </label>
      </div>
      {news.isLoading ? <LoadingState /> : news.error ? <ErrorState error={news.error} /> : null}
      {canTriage && picked.length > 0 && <TriageBar picked={picked} onDone={afterTriage} />}
      {bufferOnly && view === "card" && (
        <p className="muted-text small">卡片视图不支持勾选立案,切到「紧凑」视图即可勾选并立事件。</p>
      )}
      {view === "compact" ? (
        <section className="panel">
          <div className="panel-head">
            <h2>{bufferOnly ? "未挂事件的新闻" : "全部新闻"}</h2>
            <span className="muted">{total} 条</span>
          </div>
          {items.length ? items.map((item) => (
            <CompactRow key={item.id} item={item}
                        picked={canTriage ? picked.includes(item.id) : null}
                        onPick={(checked) => setPicked(checked
                          ? [...picked, item.id] : picked.filter((x) => x !== item.id))} />
          )) : <EmptyState title="当前筛选下没有新闻" />}
        </section>
      ) : (
        <div className="two-columns">
          <section className="panel">
            <div className="panel-head"><h2>中文源</h2></div>
            {zh.length ? zh.map((item) => <NewsCard key={item.id} item={item} />) : <EmptyState title="当前筛选下没有中文新闻" />}
          </section>
          <section className="panel">
            <div className="panel-head"><h2>英文源</h2></div>
            {en.length ? en.map((item) => <NewsCard key={item.id} item={item} />) : <EmptyState title="当前筛选下没有英文新闻" />}
          </section>
        </div>
      )}
      <div className="pager">
        <Button kind="ghost" disabled={currentPage <= 1 || news.isFetching} onClick={() => setPage((value) => Math.max(1, value - 1))}>
          <ChevronLeft size={16} />上一页
        </Button>
        <span>{currentPage} / {totalPages}</span>
        <Button kind="ghost" disabled={currentPage >= totalPages || news.isFetching} onClick={() => setPage((value) => value + 1)}>
          下一页<ChevronRight size={16} />
        </Button>
      </div>
    </section>
  );
}
