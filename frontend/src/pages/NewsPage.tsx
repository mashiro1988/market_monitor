import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { api } from "../api/client";
import type { NewsItem } from "../api/types";
import { Button, PageHeader, SelectControl, TextInput } from "../components/Controls";
import { EmptyState, ErrorState, LoadingState } from "../components/StateViews";

const importantOptions = [
  { label: "全部", value: "all" },
  { label: "仅重要", value: "important" },
  { label: "仅非重要", value: "normal" }
];

const hourOptions = [
  { label: "6小时", value: "6" },
  { label: "24小时", value: "24" },
  { label: "72小时", value: "72" }
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

export function NewsPage() {
  const [source, setSource] = useState("");
  const [importance, setImportance] = useState("5");
  const [hours, setHours] = useState("24");
  const [jin10Importance, setJin10Importance] = useState("all");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const debouncedSearch = useDebouncedValue(search, 350);
  const normalizedSearch = debouncedSearch.trim();
  const sources = useQuery({ queryKey: ["news-sources"], queryFn: api.newsSources });
  const news = useQuery({
    queryKey: ["news", source, importance, hours, jin10Importance, normalizedSearch, page],
    queryFn: () => api.news({
      sources: source ? [source] : undefined,
      min_llm_importance: Number(importance),
      hours_back: Number(hours),
      jin10_importance: jin10Importance,
      search: normalizedSearch || undefined,
      page,
      page_size: PAGE_SIZE
    }),
    placeholderData: (previous) => previous
  });

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
  const zh = news.data?.items.filter((item) => item.language === "zh") ?? [];
  const en = news.data?.items.filter((item) => item.language === "en") ?? [];
  const total = news.data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const currentPage = page;

  return (
    <section>
      <PageHeader title="新闻快讯" subtitle={`共 ${news.data?.total ?? 0} 条 · 中文 ${news.data?.zh_count ?? 0} · 英文 ${news.data?.en_count ?? 0}`} />
      <div className="toolbar">
        <SelectControl label="新闻源" value={source} onChange={(value) => { setSource(value); setPage(1); }} options={sourceOptions} />
        <SelectControl label="LLM 分数" value={importance} onChange={(value) => { setImportance(value); setPage(1); }} options={Array.from({ length: 10 }, (_, i) => ({ label: `${i + 1}+`, value: String(i + 1) }))} />
        <SelectControl label="回溯" value={hours} onChange={(value) => { setHours(value); setPage(1); }} options={hourOptions} />
        <SelectControl label="Jin10" value={jin10Importance} onChange={(value) => { setJin10Importance(value); setPage(1); }} options={importantOptions} />
        <TextInput label="关键词" value={search} onChange={(value) => { setSearch(value); setPage(1); }} placeholder="标题或正文" />
      </div>
      {news.isLoading ? <LoadingState /> : news.error ? <ErrorState error={news.error} /> : null}
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
