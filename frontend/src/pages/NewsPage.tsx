import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { NewsItem } from "../api/types";
import { PageHeader, SelectControl, TextInput } from "../components/Controls";
import { EmptyState, ErrorState, LoadingState } from "../components/StateViews";

const sourceOptions = [
  { label: "全部", value: "" },
  { label: "中文 Jin10", value: "jin10" },
  { label: "英文 Bloomberg", value: "bloomberg" }
];

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
  const news = useQuery({
    queryKey: ["news", source, importance, hours, jin10Importance, search],
    queryFn: () => api.news({
      sources: source ? [source] : undefined,
      min_llm_importance: Number(importance),
      hours_back: Number(hours),
      jin10_importance: jin10Importance,
      search,
      page_size: 200
    })
  });

  const zh = news.data?.items.filter((item) => item.source === "jin10" || item.language === "zh") ?? [];
  const en = news.data?.items.filter((item) => item.source === "bloomberg" || item.language === "en") ?? [];

  return (
    <section>
      <PageHeader title="新闻快讯" subtitle={`共 ${news.data?.total ?? 0} 条 · 中文 ${news.data?.zh_count ?? 0} · 英文 ${news.data?.en_count ?? 0}`} />
      <div className="toolbar">
        <SelectControl label="新闻源" value={source} onChange={setSource} options={sourceOptions} />
        <SelectControl label="LLM 分数" value={importance} onChange={setImportance} options={Array.from({ length: 10 }, (_, i) => ({ label: `${i + 1}+`, value: String(i + 1) }))} />
        <SelectControl label="回溯" value={hours} onChange={setHours} options={hourOptions} />
        <SelectControl label="Jin10" value={jin10Importance} onChange={setJin10Importance} options={importantOptions} />
        <TextInput label="关键词" value={search} onChange={setSearch} placeholder="标题或正文" />
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
    </section>
  );
}
