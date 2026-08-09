import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { api } from "../api/client";
import type { NewsItem } from "../api/types";
import { Button, PageHeader, SelectControl, TextInput } from "../components/Controls";
import { EmptyState, ErrorState, LoadingState } from "../components/StateViews";
import { TriageBar } from "./NewsPage";
import { fmtBjShort, fmtScore } from "./ResearchPage";

const PAGE_SIZE = 50;
const MAX_COIN_CHIPS = 6;

const hourOptions = [
  { label: "6小时", value: "6" },
  { label: "24小时", value: "24" },
  { label: "72小时", value: "72" },
  { label: "7天", value: "168" },
  { label: "30天", value: "720" },
];

// 默认「不限」:加密线不按分数拦——小币新闻分数天然低,正是要研究的对象(design §3)
const scoreOptions = [
  { label: "不限(含未评分)", value: "0" },
  ...Array.from({ length: 10 }, (_, i) => ({ label: `${i + 1}+`, value: String(i + 1) })),
];

/** 币种标签:字母序,超过 6 个折叠成计数,免得一条新闻挂一长串代码把行撑爆。 */
export function coinChips(item: NewsItem): { shown: string[]; more: number } {
  const all = [...(item.coins ?? [])].sort();
  return { shown: all.slice(0, MAX_COIN_CHIPS), more: Math.max(0, all.length - MAX_COIN_CHIPS) };
}

/** 语义闸判定的可见解释:只有「非币圈事务」需要说明——它不入加密事件池,
 *  但仍然照常展示。true 与未判定都不加标签,否则满屏噪音。 */
export function affairLabel(isCryptoAffair: boolean | null | undefined): string {
  return isCryptoAffair === false ? "转载宏观" : "";
}

function CryptoRow({ item, picked, onPick }:
                   { item: NewsItem; picked: boolean | null; onPick?: (checked: boolean) => void }) {
  const [open, setOpen] = useState(false);
  const score = fmtScore(item.llm_importance);
  const chips = coinChips(item);
  const affair = affairLabel(item.is_crypto_affair);
  return (
    <div className="rp-news-item">
      <div className={`rp-row ${picked !== null ? "rp-row-triage" : "rp-row-news"}`}>
        {picked !== null && (
          <input type="checkbox" checked={picked} aria-label="选中以立案"
                 onChange={(ev) => onPick?.(ev.target.checked)} />
        )}
        <span className="rp-time">{fmtBjShort(item.timestamp_bj)}</span>
        <span className="rp-source">{item.source}</span>
        <span className={score.cls}>{score.text}</span>
        <span className={item.news_direction === "利多" ? "up-text"
                         : item.news_direction === "利空" ? "down-text" : "muted"}>
          {item.news_direction ?? "—"}
        </span>
        <button type="button" className="rp-title rp-title-btn" title="点开看理由/正文/原文"
                onClick={() => setOpen((v) => !v)}>
          {affair && <span className="s-badge none">{affair}</span>}
          {chips.shown.map((c) => <span key={c} className="s-badge mid">{c}</span>)}
          {chips.more > 0 && <span className="muted">+{chips.more}</span>}
          {item.title}
        </button>
      </div>
      {open && (
        <div className="rp-news-body">
          {item.llm_importance_reason && <p className="reason">{item.llm_importance_reason}</p>}
          {item.content && <p>{item.content}</p>}
          {item.url && <a href={item.url} target="_blank" rel="noreferrer">原文链接</a>}
        </div>
      )}
    </div>
  );
}

export function CryptoNewsPage() {
  const qc = useQueryClient();
  const [source, setSource] = useState("");
  const [importance, setImportance] = useState("0");
  const [hours, setHours] = useState("24");
  const [coin, setCoin] = useState("");
  const [search, setSearch] = useState("");
  const [affairOnly, setAffairOnly] = useState(false);
  const [triageMode, setTriageMode] = useState(false);
  const [page, setPage] = useState(1);
  const [picked, setPicked] = useState<number[]>([]);

  const sources = useQuery({ queryKey: ["crypto-news-sources"], queryFn: api.cryptoNewsSources });
  const news = useQuery({
    queryKey: ["crypto-news", source, importance, hours, coin, search, affairOnly, page],
    queryFn: () => api.cryptoNews({
      sources: source ? [source] : undefined,
      min_llm_importance: Number(importance),
      hours_back: Number(hours),
      coin: coin.trim() || undefined,
      search: search.trim() || undefined,
      ...(affairOnly ? { affair_only: true } : {}),
      page,
      page_size: PAGE_SIZE,
    }),
    placeholderData: (previous) => previous,
  });

  const afterTriage = () => {
    setPicked([]);
    void qc.invalidateQueries({ queryKey: ["crypto-news"] });
    void qc.invalidateQueries({ queryKey: ["research-events"] });
  };

  const sourceOptions = useMemo(() => {
    const opts = [{ label: "全部", value: "" }];
    for (const s of sources.data ?? []) opts.push({ label: s.name, value: s.key });
    return opts;
  }, [sources.data]);

  const items = news.data?.items ?? [];
  const total = news.data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const resetPage = <T,>(setter: (v: T) => void) => (v: T) => { setter(v); setPage(1); };

  return (
    <section>
      <PageHeader title="加密快讯"
                  subtitle={`共 ${total} 条 · 中文 ${news.data?.zh_count ?? 0} · 英文 ${news.data?.en_count ?? 0}`} />
      <div className="toolbar">
        <SelectControl label="来源" value={source} onChange={resetPage(setSource)} options={sourceOptions} />
        <SelectControl label="LLM 分数" value={importance} onChange={resetPage(setImportance)} options={scoreOptions} />
        <SelectControl label="回溯" value={hours} onChange={resetPage(setHours)} options={hourOptions} />
        <TextInput label="币种" value={coin} onChange={resetPage(setCoin)} placeholder="如 SOL" />
        <TextInput label="关键词" value={search} onChange={resetPage(setSearch)} placeholder="标题或正文" />
        <label className="field">
          <span>范围</span>
          <label className="rp-check">
            <input type="checkbox" checked={affairOnly}
                   onChange={(ev) => { setAffairOnly(ev.target.checked); setPage(1); }} />
            只看币圈事务
          </label>
        </label>
        <label className="field">
          <span>立案</span>
          <label className="rp-check">
            <input type="checkbox" checked={triageMode}
                   onChange={(ev) => { setTriageMode(ev.target.checked); setPicked([]); }} />
            勾选立案
          </label>
        </label>
      </div>
      {news.isLoading ? <LoadingState /> : news.error ? <ErrorState error={news.error} /> : null}
      {triageMode && picked.length > 0 &&
        <TriageBar picked={picked} onDone={afterTriage} eventType="crypto" />}
      <section className="panel">
        <div className="panel-head">
          <h2>{affairOnly ? "币圈事务" : "全部加密快讯"}</h2>
          <span className="muted">{total} 条</span>
        </div>
        {items.length ? items.map((item) => (
          <CryptoRow key={item.id} item={item}
                     picked={triageMode ? picked.includes(item.id) : null}
                     onPick={(checked) => setPicked(checked
                       ? [...picked, item.id] : picked.filter((x) => x !== item.id))} />
        )) : <EmptyState title="当前筛选下没有加密快讯" />}
      </section>
      <div className="pager">
        <Button kind="ghost" disabled={page <= 1 || news.isFetching}
                onClick={() => setPage((v) => Math.max(1, v - 1))}>
          <ChevronLeft size={16} />上一页
        </Button>
        <span>{page} / {totalPages}</span>
        <Button kind="ghost" disabled={page >= totalPages || news.isFetching}
                onClick={() => setPage((v) => v + 1)}>
          下一页<ChevronRight size={16} />
        </Button>
      </div>
    </section>
  );
}
