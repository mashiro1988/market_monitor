import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Circle, Layers, RotateCcw, Save, Sparkles } from "lucide-react";
import { api } from "../api/client";
import type { AnnotationListItem, AutoAnnotateBatchItem, AutoAnnotateResponse, NewsItem, PriceWindow } from "../api/types";

// 实测 5 窗口 × reasoning_effort=max 经常把 max_tokens 预算用完导致空 content（模型
// 还在思考没产出 JSON），所以再保守一档到 3。后端 AUTO_ANNOTATE_BATCH_LIMIT 仍是 10,
// 是给 API 留的硬上限，不是日常工况。
const AUTO_BATCH_CHUNK = 3;
import { Button, PageHeader, SelectControl, Stat, TextInput } from "../components/Controls";
import { DataTable } from "../components/DataTable";
import { EmptyState, ErrorState, LoadingState } from "../components/StateViews";

const hoursOptions = [
  { label: "24小时", value: "24" },
  { label: "72小时", value: "72" },
  { label: "7天", value: "168" }
];

function windowKey(w: PriceWindow): string {
  return `${w.symbol}|${w.window_start.timestamp_utc}|${w.window_end.timestamp_utc}`;
}

// sessionStorage 持久化 in-progress 标注：批量 AI 结果 + 用户对每个窗口的手动修改（勾选/no_clear_news/notes）
// + 当前选中窗口 + 标注人。切到别的页面再回来不会丢；标注保存成功后该 key 会被清理。
// 不持久化 hours/symbol：那些是浏览偏好，下一次会话默认值更合理。
const STORAGE_KEY = "annotations.session.v1";

type StoredState = {
  batchByKey: [string, AutoAnnotateBatchItem][];
  batchMeta: { reasoning: string; model: string; duration_seconds: number } | null;
  labeler: string;
  activeKey: string;
};

function loadStored(): Partial<StoredState> {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as Partial<StoredState>) : {};
  } catch {
    return {};
  }
}

function arraysEqual(a: number[], b: number[]): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
}

// 拆出 reasoning panel 作为独立 memo 组件，让 AnnotationsPage 父级因任何无关 state（hover、切换品种、
// 表单输入）re-render 时，只要 props 引用没变，这块就不重新渲染。reasoning <pre> 内容可能很长，
// 让它频繁参与父级 reconciliation 是抖动主因之一。
type ReasoningPanelProps = {
  model: string;
  duration_seconds: number;
  candidate_count: number;
  summary: string;
  reasoning: string;
};

const ReasoningPanel = memo(function ReasoningPanel({ model, duration_seconds, candidate_count, summary, reasoning }: ReasoningPanelProps) {
  return (
    <details className="reasoning-block" open>
      <summary>
        <span className="reasoning-tag">推理结果</span>
        <span>{model} · {duration_seconds.toFixed(1)}s · 看了 {candidate_count} 条候选</span>
      </summary>
      {summary ? <p className="reasoning-summary">{summary}</p> : null}
      {reasoning ? (
        <pre className="reasoning-content">{reasoning}</pre>
      ) : <p className="muted-text small">模型未返回 reasoning_content（thinking 模式可能未生效）。</p>}
    </details>
  );
});

export function AnnotationsPage() {
  const queryClient = useQueryClient();
  // 仅在 mount 时读一次 sessionStorage，避免 useState lazy initializer 被多次评估。
  const initialStored = useRef<Partial<StoredState>>(loadStored()).current;

  const [hours, setHours] = useState("72");
  const [symbol, setSymbol] = useState("");
  const [activeKey, setActiveKey] = useState<string>(initialStored.activeKey ?? "");

  // 编辑表单状态
  const [selectedNews, setSelectedNews] = useState<number[]>([]);
  const [noClearNews, setNoClearNews] = useState(false);
  const [notes, setNotes] = useState("");
  const [labeler, setLabeler] = useState(initialStored.labeler ?? "");
  const [autoResult, setAutoResult] = useState<AutoAnnotateResponse | null>(null);

  // 批量自动标注的结果缓存。Key = windowKey(window)。一次「批量自动标注」可能产生多片
  // batch 调用（每片 ≤10 窗口），每个窗口的结果按 key 暂存，等用户点中某个窗口时回填表单。
  // 用户的手动修改（勾选/no_clear_news/notes）也会写回到这里，作为 in-progress 单一来源。
  const [batchByKey, setBatchByKey] = useState<Map<string, AutoAnnotateBatchItem>>(
    () => new Map(initialStored.batchByKey ?? [])
  );
  // 当前/最近一次批量调用的全局元数据（reasoning + 模型名 + 总耗时），用于附给每个窗口的 autoResult。
  const [batchMeta, setBatchMeta] = useState<{ reasoning: string; model: string; duration_seconds: number } | null>(
    initialStored.batchMeta ?? null
  );
  // 批处理进度：{ done, total } 用来显示「3/12」之类的状态。
  const [batchProgress, setBatchProgress] = useState<{ done: number; total: number } | null>(null);
  const [batchError, setBatchError] = useState<unknown>(null);

  // 价格区间 hover tooltip：window-item 在 .annotation-pair-panel(overflow:hidden)
  // 里，原生 title 太丑且 absolute 子元素会被裁；改用 position: fixed 渲染到顶层，
  // 用 React state 记录鼠标对应行的元数据 + viewport 坐标。
  const [pricePeek, setPricePeek] = useState<{
    startPrice: number;
    endPrice: number;
    tone: "up" | "down";
    x: number;
    y: number;
  } | null>(null);

  const showPricePeek = (event: React.MouseEvent<HTMLElement>, w: PriceWindow) => {
    const rect = event.currentTarget.getBoundingClientRect();
    // 默认放右侧；如果靠近视口右边缘就 fallback 到左侧，避免溢出。
    const tooltipWidth = 200;
    const wantedLeft = rect.right + 10;
    const x = wantedLeft + tooltipWidth > window.innerWidth - 12
      ? rect.left - tooltipWidth - 10
      : wantedLeft;
    setPricePeek({
      startPrice: w.price_start,
      endPrice: w.price_end,
      tone: w.change_pct >= 0 ? "up" : "down",
      x,
      y: rect.top
    });
  };

  const hidePricePeek = () => setPricePeek(null);

  const rules = useQuery({ queryKey: ["annotation-rules"], queryFn: api.priceRules });
  const symbols = useQuery({ queryKey: ["annotation-symbols", hours], queryFn: () => api.annotationSymbols(Number(hours)) });
  const currentSymbol = symbol || symbols.data?.[0]?.symbol || "";
  const rule = rules.data?.find((item) => item.symbol === currentSymbol);

  const windowsQuery = useQuery({
    queryKey: ["annotation-windows", currentSymbol, hours],
    queryFn: () => api.annotationWindows({ symbol: currentSymbol, hours: Number(hours) }),
    enabled: Boolean(currentSymbol)
  });

  const annotatedListQuery = useQuery({
    queryKey: ["annotation-list", currentSymbol, hours],
    queryFn: () => api.annotationsList({ symbol: currentSymbol, hours: Number(hours) }),
    enabled: Boolean(currentSymbol)
  });

  // 把后端按 run 排好的窗口分组。后端保证排序：每个 primary 后紧跟它的 secondaries（按时间升序）。
  // 已标注 primary 的整个 group 在这里被丢掉——primary 进了下方"已标注"块，
  // secondaries 没必要孤悬展示（它们属于已经处理过的事件）。
  const groups = useMemo(() => {
    const all: { primary: PriceWindow; secondaries: PriceWindow[] }[] = [];
    for (const w of windowsQuery.data ?? []) {
      if (w.is_primary) {
        all.push({ primary: w, secondaries: [] });
      } else if (all.length) {
        all[all.length - 1].secondaries.push(w);
      }
    }
    return all.filter((g) => g.primary.annotation_id == null);
  }, [windowsQuery.data]);

  const unannotatedPrimaries = useMemo(() => groups.map((g) => g.primary), [groups]);

  // 还没有 batchByKey 缓存结果的 primary 子集——批量自动标注每次只对这些发请求，
  // 避免用户多次点按钮时把已经推理过的窗口重新喂一遍。手动 per-window 自动标注会更新
  // 同一个 batchByKey entry，所以也会把对应窗口从 pending 中移除。
  const pendingForBatch = useMemo(
    () => unannotatedPrimaries.filter((w) => !batchByKey.has(windowKey(w))),
    [unannotatedPrimaries, batchByKey]
  );

  // 默认选第一条未标注 primary；切换 symbol/hours 后若上次的窗口没了也走这里。
  useEffect(() => {
    if (!unannotatedPrimaries.length) {
      setActiveKey("");
      return;
    }
    if (activeKey && unannotatedPrimaries.some((w) => windowKey(w) === activeKey)) return;
    setActiveKey(windowKey(unannotatedPrimaries[0]));
  }, [unannotatedPrimaries, activeKey]);

  const activeWindow = useMemo(
    () => unannotatedPrimaries.find((w) => windowKey(w) === activeKey),
    [unannotatedPrimaries, activeKey]
  );

  const contextNews = useQuery({
    queryKey: ["context-news", activeWindow?.window_start.timestamp_utc, activeWindow?.window_end.timestamp_utc],
    queryFn: () => api.contextNews({
      window_start_utc: activeWindow!.window_start.timestamp_utc!,
      window_end_utc: activeWindow!.window_end.timestamp_utc!,
      pre_minutes: 15,
      post_minutes: 30
    }),
    enabled: Boolean(activeWindow)
  });

  // 切换窗口时：如果该窗口在批量结果缓存里有，回填表单和 autoResult；否则清空。
  // 关键：每个 setter 都用幂等更新（值未变就返回 prev），让 React 通过 Object.is 跳过 re-render。
  // 否则用户每次改动 → write-back 写 batchByKey → 本 effect 重跑 → autoResult 新对象 → 整个推理面板
  // 子树多余 re-render → 视觉抖动。
  useEffect(() => {
    const cached = batchByKey.get(activeKey);
    if (cached && batchMeta) {
      setSelectedNews((prev) => arraysEqual(prev, cached.selected_news_ids) ? prev : cached.selected_news_ids);
      setNoClearNews((prev) => prev === cached.no_clear_news ? prev : cached.no_clear_news);
      setNotes((prev) => prev === cached.summary ? prev : cached.summary);
      setAutoResult((prev) => {
        const expectedReasoning = cached.reasoning || batchMeta.reasoning;
        if (
          prev &&
          prev.reasoning === expectedReasoning &&
          prev.model === batchMeta.model &&
          prev.candidate_count === cached.candidate_count &&
          prev.duration_seconds === batchMeta.duration_seconds
        ) {
          return prev;  // AI 元数据未变 → 不创建新对象，不触发 re-render
        }
        return {
          selected_news_ids: cached.selected_news_ids,
          no_clear_news: cached.no_clear_news,
          summary: cached.summary,
          // 优先用本窗口结构化输出里的 reasoning；为空时退回到整批 thinking trace（debug 用）
          reasoning: expectedReasoning,
          model: batchMeta.model,
          duration_seconds: batchMeta.duration_seconds,
          candidate_count: cached.candidate_count
        };
      });
    } else if (cached) {
      // 缓存里有但没 batchMeta（重新加载页面后 batchMeta 也持久化了，正常路径会走上面分支；
      // 这里是兜底：纯人工编辑过的窗口没有 AI 元数据，但表单内容还是要还原）
      setSelectedNews((prev) => arraysEqual(prev, cached.selected_news_ids) ? prev : cached.selected_news_ids);
      setNoClearNews((prev) => prev === cached.no_clear_news ? prev : cached.no_clear_news);
      setNotes((prev) => prev === cached.summary ? prev : cached.summary);
      setAutoResult((prev) => prev === null ? prev : null);
    } else {
      setSelectedNews((prev) => prev.length === 0 ? prev : []);
      setNoClearNews((prev) => prev === false ? prev : false);
      setNotes((prev) => prev === "" ? prev : "");
      setAutoResult((prev) => prev === null ? prev : null);
    }
  }, [activeKey, batchByKey, batchMeta]);

  // 用户对当前窗口的勾选 / no_clear_news / notes 改动写回 batchByKey，让其成为 in-progress 单一来源。
  // 内置幂等：值未变时早退避免与上面 hydrate 形成循环。
  useEffect(() => {
    if (!activeKey || !activeWindow) return;
    setBatchByKey((prev) => {
      const existing = prev.get(activeKey);
      const sameSelected = existing
        ? arraysEqual(existing.selected_news_ids, selectedNews)
        : selectedNews.length === 0;
      const sameNoClear = (existing?.no_clear_news ?? false) === noClearNews;
      const sameSummary = (existing?.summary ?? "") === notes;
      if (existing && sameSelected && sameNoClear && sameSummary) return prev;
      // 没 existing + 用户也没动过 → 不创建空 entry
      if (!existing && !selectedNews.length && !noClearNews && !notes) return prev;

      const next = new Map(prev);
      next.set(activeKey, {
        symbol: activeWindow.symbol,
        window_start_utc: activeWindow.window_start.timestamp_utc!,
        window_end_utc: activeWindow.window_end.timestamp_utc!,
        selected_news_ids: selectedNews,
        no_clear_news: noClearNews,
        summary: notes,
        reasoning: existing?.reasoning ?? "",
        candidate_count: existing?.candidate_count ?? 0,
        candidate_news_ids: existing?.candidate_news_ids ?? []
      });
      return next;
    });
  }, [activeKey, activeWindow, selectedNews, noClearNews, notes]);

  // 把 batchByKey / batchMeta / labeler / activeKey 持久化到 sessionStorage。
  // **必须 debounce** —— reasoning 能有几 KB，每次 JSON.stringify + sessionStorage.setItem 是
  // 同步阻塞操作（~1-3ms）。在 textarea 里快速打字时，每个 keystroke 触发 write-back → batchByKey
  // 变化 → 这个 effect 跑一次，累积起来卡主线程，导致输入跟不上、textarea 抖动。
  // 等用户停止操作 300ms 再写，正常 idle 状态下感受不到延迟。
  useEffect(() => {
    const timer = setTimeout(() => {
      try {
        const data: StoredState = {
          batchByKey: Array.from(batchByKey.entries()),
          batchMeta,
          labeler,
          activeKey
        };
        sessionStorage.setItem(STORAGE_KEY, JSON.stringify(data));
      } catch {
        // sessionStorage 满 / disabled 时静默失败
      }
    }, 300);
    return () => clearTimeout(timer);
  }, [batchByKey, batchMeta, labeler, activeKey]);

  const save = useMutation({
    mutationFn: () => api.saveAnnotation({
      symbol: activeWindow!.symbol,
      window_start_utc: activeWindow!.window_start.timestamp_utc!,
      window_end_utc: activeWindow!.window_end.timestamp_utc!,
      threshold_pct: rule?.threshold_pct ?? 0,
      selected_news_ids: selectedNews,
      no_clear_news: noClearNews,
      notes,
      labeler: autoResult ? `${labeler || ""}${labeler ? " · " : ""}${autoResult.model} (auto, reviewed)` : labeler,
      // 训练数据：把当前展示的全部候选新闻 ID 一起存（即使是纯人工标注，也保留负样本信息）。
      candidate_news_ids: (contextNews.data?.items ?? []).map((item) => item.id),
      // 自动标注流程：保存 LLM 原始推理 + 摘要，与人改后的 notes 分开。
      auto_reasoning: autoResult?.reasoning ?? null,
      auto_summary: autoResult?.summary ?? null
    }),
    onSuccess: () => {
      // 已落库 → 从 in-progress 缓存里清掉，避免下次回到该 symbol 时还显示已经保存过的草稿
      const savedKey = activeKey;
      setBatchByKey((prev) => {
        if (!prev.has(savedKey)) return prev;
        const next = new Map(prev);
        next.delete(savedKey);
        return next;
      });
      void queryClient.invalidateQueries({ queryKey: ["annotation-windows"] });
      void queryClient.invalidateQueries({ queryKey: ["annotation-list"] });
    }
  });

  const undo = useMutation({
    mutationFn: (id: number) => api.deleteAnnotation(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["annotation-windows"] });
      void queryClient.invalidateQueries({ queryKey: ["annotation-list"] });
    }
  });

  const autoAnnotate = useMutation({
    mutationFn: () => api.autoAnnotate({
      symbol: activeWindow!.symbol,
      window_start_utc: activeWindow!.window_start.timestamp_utc!,
      window_end_utc: activeWindow!.window_end.timestamp_utc!,
      threshold_pct: rule?.threshold_pct ?? 0
    }),
    onSuccess: (result) => {
      setAutoResult(result);
      setSelectedNews(result.selected_news_ids);
      setNoClearNews(result.no_clear_news);
      setNotes(result.summary);
      // 单窗口自动标注的结果也写入 batchByKey，让批量按钮把这条视为已处理。
      if (activeWindow) {
        setBatchByKey((prev) => {
          const next = new Map(prev);
          next.set(activeKey, {
            symbol: activeWindow.symbol,
            window_start_utc: activeWindow.window_start.timestamp_utc!,
            window_end_utc: activeWindow.window_end.timestamp_utc!,
            selected_news_ids: result.selected_news_ids,
            no_clear_news: result.no_clear_news,
            summary: result.summary,
            reasoning: result.reasoning,  // 单窗口直接拿 DeepSeek thinking 全文做该窗口 reasoning
            candidate_count: result.candidate_count,
            candidate_news_ids: []  // 单窗口接口未返回 candidate_news_ids，留空；保存时前端从 contextNews 重算
          });
          return next;
        });
        setBatchMeta({
          reasoning: result.reasoning,
          model: result.model,
          duration_seconds: result.duration_seconds
        });
      }
    }
  });

  // 批量自动标注：把**还没缓存过结果**的未标注 primary 分片调 /api/annotations/auto-batch，
  // 每片 ≤AUTO_BATCH_CHUNK 个窗口；结果累积到 batchByKey，用户点开任一窗口都能预填。
  const runBatchAutoAnnotate = async () => {
    setBatchError(null);
    const targets = pendingForBatch;
    if (!targets.length) return;

    const chunks: PriceWindow[][] = [];
    for (let i = 0; i < targets.length; i += AUTO_BATCH_CHUNK) {
      chunks.push(targets.slice(i, i + AUTO_BATCH_CHUNK));
    }

    setBatchProgress({ done: 0, total: chunks.length });
    const accum = new Map(batchByKey);
    let lastReasoning = batchMeta?.reasoning ?? "";
    let lastModel = batchMeta?.model ?? "";
    let totalDuration = batchMeta?.duration_seconds ?? 0;

    try {
      for (let i = 0; i < chunks.length; i++) {
        const chunk = chunks[i];
        const response = await api.autoAnnotateBatch({
          windows: chunk.map((w) => ({
            symbol: w.symbol,
            window_start_utc: w.window_start.timestamp_utc!,
            window_end_utc: w.window_end.timestamp_utc!,
            threshold_pct: rule?.threshold_pct ?? 0
          }))
        });
        for (const item of response.results) {
          accum.set(`${item.symbol}|${item.window_start_utc}|${item.window_end_utc}`, item);
        }
        // 多片时各 chunk 的 reasoning 互相独立，前端只展示最近一片，避免拼太长。
        lastReasoning = response.reasoning;
        lastModel = response.model;
        totalDuration += response.duration_seconds;
        setBatchByKey(new Map(accum));
        setBatchMeta({ reasoning: lastReasoning, model: lastModel, duration_seconds: totalDuration });
        setBatchProgress({ done: i + 1, total: chunks.length });
      }
    } catch (err) {
      setBatchError(err);
    } finally {
      // 不管成功 / 失败 / 中断，进度条都清掉，让按钮可以再次点击重试。
      // 已经成功落入 batchByKey 的窗口下次会被 pendingForBatch 自动跳过——
      // 用户重新点 batch 只会处理失败 / 未尝试的剩余部分。
      setBatchProgress(null);
    }
  };

  const batchInFlight = batchProgress != null && batchProgress.done < batchProgress.total;

  // 候选新闻表 columns：必须 useMemo，否则父级每次 re-render（hover、textarea 输入等）都新建
  // columns 数组 + 新 cell 闭包，DataTable 收到新 props → 整张表 + 每个 checkbox 全部重渲，
  // 是抖动主因之二。toggleNews 用 useCallback 保持稳定；columns 只在 selectedNews 变化时重建（
  // 此时 checkbox 的 checked 也确实需要更新，是必要的重建）。
  const toggleNews = useCallback((id: number, checked: boolean) => {
    setSelectedNews((ids) => checked ? [...ids, id] : ids.filter((x) => x !== id));
  }, []);

  const newsColumns = useMemo(() => [
    {
      key: "select",
      header: "选择",
      cell: (row: NewsItem) => (
        <input
          type="checkbox"
          checked={selectedNews.includes(row.id)}
          onChange={(event) => toggleNews(row.id, event.target.checked)}
        />
      )
    },
    { key: "time", header: "时间", cell: (row: NewsItem) => row.timestamp_bj?.slice(5, 16) },
    { key: "source", header: "来源", cell: (row: NewsItem) => row.source },
    { key: "score", header: "LLM", cell: (row: NewsItem) => row.llm_importance ?? "—" },
    { key: "title", header: "标题", cell: (row: NewsItem) => row.title }
  ], [selectedNews, toggleNews]);

  return (
    <section>
      <PageHeader title="新闻标注" subtitle="价格异动窗口与候选新闻关联（未标注 / 已标注 上下分栏）" />

      <div className="annotation-filter">
        <SelectControl label="回溯" value={hours} onChange={setHours} options={hoursOptions} />
        <SelectControl
          label="品种"
          value={currentSymbol}
          onChange={(value) => { setSymbol(value); setActiveKey(""); }}
          options={(symbols.data ?? []).map((item) => ({ value: item.symbol, label: `${item.name} (${item.symbol})` }))}
        />
        <TextInput label="标注人" value={labeler} onChange={setLabeler} placeholder="可留空" />
      </div>

      {/* Section 1: 自动标注 —— LLM 触发按钮 + 当前窗口元信息 + 推理面板 */}
      <section className="panel annotation-block">
        <div className="panel-head">
          <h2>自动标注</h2>
          <div className="annotation-actions">
            <Button
              kind="secondary"
              onClick={() => void runBatchAutoAnnotate()}
              disabled={!pendingForBatch.length || batchInFlight}
            >
              <Layers size={16} />
              {batchInFlight
                ? `批量推理中 ${batchProgress!.done}/${batchProgress!.total}`
                : pendingForBatch.length === 0 && groups.length > 0
                  ? `已全部推理 (${groups.length})`
                  : `批量自动标注 (剩余 ${pendingForBatch.length}${groups.length !== pendingForBatch.length ? `/${groups.length}` : ""})`}
            </Button>
            <Button
              kind="secondary"
              onClick={() => autoAnnotate.mutate()}
              disabled={!activeWindow || autoAnnotate.isPending}
            >
              <Sparkles size={16} />
              {autoAnnotate.isPending ? "推理中..." : "自动标注当前窗口"}
            </Button>
          </div>
        </div>
        {batchError ? <ErrorState error={batchError} /> : null}

        {autoResult ? (
          <ReasoningPanel
            model={autoResult.model}
            duration_seconds={autoResult.duration_seconds}
            candidate_count={autoResult.candidate_count}
            summary={autoResult.summary}
            reasoning={autoResult.reasoning}
          />
        ) : null}

        {autoAnnotate.error ? <ErrorState error={autoAnnotate.error} /> : null}
      </section>

      {/* Section 2: 未标注 —— 左右对称（待标注列表 + 候选新闻），下方表单 */}
      <section className="panel annotation-block">
        <div className="panel-head">
          <h2>未标注 ({groups.length})</h2>
          <span className="muted-text small">
            连续异动会聚合为一个事件，只标第一次（↳ 续发窗口只展示不标）。候选新闻取窗口前 15 / 后 30 分钟。
          </span>
        </div>

        {windowsQuery.isLoading ? <LoadingState /> :
         windowsQuery.error ? <ErrorState error={windowsQuery.error} /> :
         !groups.length ? <EmptyState title="该回溯期内没有未标注的价格异动事件" /> : (
          <>
            <div className="annotation-pair-grid">
              <section className="annotation-pair-panel">
                <header className="annotation-pair-panel-head">
                  <span>待标注事件</span>
                  <span>{groups.length} 条</span>
                </header>
                <div className="annotation-pair-panel-body">
                  <ul className="window-list">
                    {groups.map(({ primary }) => {
                      const key = windowKey(primary);
                      const isActive = key === activeKey;
                      const tone = primary.change_pct >= 0 ? "up" : "down";
                      const sign = primary.change_pct > 0 ? "+" : "";
                      return (
                        <li key={key}>
                          <button
                            type="button"
                            className={`window-item ${tone}${isActive ? " active" : ""}`}
                            onClick={() => setActiveKey(key)}
                            onMouseEnter={(e) => showPricePeek(e, primary)}
                            onMouseLeave={hidePricePeek}
                          >
                            <span className="window-item-icon"><Circle size={14} /></span>
                            <span className="window-item-time">
                              {primary.window_start.timestamp_bj?.slice(5, 16)} → {primary.window_end.timestamp_bj?.slice(11, 16)}
                            </span>
                            <span className="window-item-pct">
                              {sign}{primary.change_pct.toFixed(2)}%
                            </span>
                            <span className="window-item-pct" title="峰值（相对起点价的最大偏离）">
                              峰 {primary.peak_change_pct >= 0 ? "+" : ""}{primary.peak_change_pct.toFixed(2)}%
                            </span>
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                </div>
              </section>

              <section className="annotation-pair-panel">
                <header className="annotation-pair-panel-head">
                  <span>候选新闻</span>
                  <span>
                    {!activeWindow ? "选中窗口后载入" : `${contextNews.data?.items.length ?? 0} 条 · 前15/后30 分钟`}
                  </span>
                </header>
                <div className="annotation-pair-panel-body">
                  {!activeWindow ? <EmptyState title="选择左侧窗口查看候选新闻" /> :
                   contextNews.isLoading ? <LoadingState /> :
                   contextNews.error ? <ErrorState error={contextNews.error} /> : (
                    <DataTable<NewsItem>
                      rows={contextNews.data?.items ?? []}
                      empty="窗口前 15 / 后 30 分钟没有候选新闻"
                      columns={newsColumns}
                    />
                  )}
                </div>
              </section>
            </div>

            {activeWindow ? (
              <div className="annotation-save-block">
                <div className="annotation-form-row">
                  <label className="checkline">
                    <input type="checkbox" checked={noClearNews} onChange={(event) => setNoClearNews(event.target.checked)} />
                    没有明确新闻触发
                  </label>
                </div>
                <label className="field full">
                  <span>备注 / 因果归因</span>
                  <textarea value={notes} onChange={(event) => setNotes(event.target.value)} placeholder="自动标注后会自动填入 summary，可手动修改" />
                </label>
                <div className="annotation-save-row">
                  <Button disabled={save.isPending} onClick={() => save.mutate()}>
                    <Save size={16} />保存标注
                  </Button>
                  {save.data ? <div className="task-banner succeeded">已保存标注 #{save.data.id}</div> : null}
                  {save.error ? <ErrorState error={save.error} /> : null}
                </div>
              </div>
            ) : null}
          </>
        )}
      </section>

      <section className="panel annotation-block">
        <div className="panel-head">
          <h2>已标注 ({annotatedListQuery.data?.length ?? 0})</h2>
          <span className="muted-text small">点击「撤销」可移除标注，对应窗口会回到上方未标注列表</span>
        </div>

        {annotatedListQuery.isLoading ? <LoadingState /> :
         annotatedListQuery.error ? <ErrorState error={annotatedListQuery.error} /> : (
          <DataTable<AnnotationListItem>
            rows={annotatedListQuery.data ?? []}
            empty="该回溯期内还没有标注"
            columns={[
              {
                key: "window",
                header: "时间窗口",
                cell: (row) => `${row.window_start.timestamp_bj?.slice(5, 16)} → ${row.window_end.timestamp_bj?.slice(11, 16)}`
              },
              {
                key: "chg",
                header: "涨跌",
                cell: (row) => {
                  const pct = row.change_pct;
                  if (pct == null) return "—";
                  const sign = pct > 0 ? "+" : "";
                  return <span className={pct >= 0 ? "up-text" : "down-text"}>{sign}{pct.toFixed(2)}%</span>;
                },
                className: "num"
              },
              {
                key: "selected",
                header: "选中新闻",
                cell: (row) => row.no_clear_news
                  ? <span className="muted-text">无明确诱因</span>
                  : `${row.selected_count} 条`
              },
              { key: "labeler", header: "标注人", cell: (row) => row.labeler || "—" },
              { key: "notes", header: "备注摘要", cell: (row) => row.notes || "—" },
              {
                key: "updated",
                header: "更新时间",
                cell: (row) => row.updated_at.timestamp_bj?.slice(5, 16) || "—"
              },
              {
                key: "action",
                header: "操作",
                cell: (row) => (
                  <button
                    type="button"
                    className="link-button danger"
                    onClick={() => undo.mutate(row.id)}
                    disabled={undo.isPending && undo.variables === row.id}
                  >
                    <RotateCcw size={14} />
                    撤销
                  </button>
                )
              }
            ]}
          />
        )}
        {undo.error ? <ErrorState error={undo.error} /> : null}
      </section>

      {pricePeek ? (
        <div
          className="price-peek"
          style={{ left: pricePeek.x, top: pricePeek.y }}
          role="tooltip"
        >
          <div className={`price-peek-head ${pricePeek.tone}`}>
            {pricePeek.tone === "up" ? "↑" : "↓"} 价格区间
          </div>
          <div className="price-peek-row">
            <span>起点</span>
            <strong>{pricePeek.startPrice.toLocaleString()}</strong>
          </div>
          <div className="price-peek-row">
            <span>终点</span>
            <strong>{pricePeek.endPrice.toLocaleString()}</strong>
          </div>
        </div>
      ) : null}
    </section>
  );
}
