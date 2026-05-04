import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Circle, RotateCcw, Save, Sparkles } from "lucide-react";
import { api } from "../api/client";
import type { AnnotationDetail, AutoAnnotateResponse, NewsItem, PriceWindow } from "../api/types";
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

function windowLabel(w: PriceWindow): string {
  const start = w.window_start.timestamp_bj?.slice(5, 16) ?? "";
  const end = w.window_end.timestamp_bj?.slice(11, 16) ?? "";
  const sign = w.change_pct > 0 ? "+" : "";
  return `${start} → ${end} · ${sign}${w.change_pct.toFixed(2)}%`;
}

export function AnnotationsPage() {
  const queryClient = useQueryClient();
  const [hours, setHours] = useState("72");
  const [symbol, setSymbol] = useState("");
  const [activeKey, setActiveKey] = useState<string>("");

  // 编辑表单状态（仅未标注窗口使用）
  const [selectedNews, setSelectedNews] = useState<number[]>([]);
  const [noClearNews, setNoClearNews] = useState(false);
  const [notes, setNotes] = useState("");
  const [labeler, setLabeler] = useState("");
  const [autoResult, setAutoResult] = useState<AutoAnnotateResponse | null>(null);

  const rules = useQuery({ queryKey: ["annotation-rules"], queryFn: api.priceRules });
  const symbols = useQuery({ queryKey: ["annotation-symbols", hours], queryFn: () => api.annotationSymbols(Number(hours)) });
  const currentSymbol = symbol || symbols.data?.[0]?.symbol || "";
  const rule = rules.data?.find((item) => item.symbol === currentSymbol);
  const windowsQuery = useQuery({
    queryKey: ["annotation-windows", currentSymbol, hours],
    queryFn: () => api.annotationWindows({ symbol: currentSymbol, hours: Number(hours) }),
    enabled: Boolean(currentSymbol)
  });

  const { unannotated, annotated } = useMemo(() => {
    const all = windowsQuery.data ?? [];
    return {
      unannotated: all.filter((w) => w.annotation_id == null),
      annotated: all.filter((w) => w.annotation_id != null)
    };
  }, [windowsQuery.data]);

  // 当 windows 列表更新时，如果当前没选中或选中的窗口已不存在，默认选第一条未标注（兜底走第一条已标注）。
  useEffect(() => {
    const all = windowsQuery.data ?? [];
    if (!all.length) {
      setActiveKey("");
      return;
    }
    if (activeKey && all.some((w) => windowKey(w) === activeKey)) return;
    const first = unannotated[0] ?? annotated[0];
    if (first) setActiveKey(windowKey(first));
  }, [windowsQuery.data, activeKey, unannotated, annotated]);

  const activeWindow = useMemo(() => {
    return (windowsQuery.data ?? []).find((w) => windowKey(w) === activeKey);
  }, [windowsQuery.data, activeKey]);

  const isAnnotatedView = Boolean(activeWindow?.annotation_id);

  const annotationDetail = useQuery({
    queryKey: ["annotation-detail", activeWindow?.annotation_id],
    queryFn: () => api.annotationDetail(activeWindow!.annotation_id!),
    enabled: Boolean(activeWindow?.annotation_id)
  });

  const contextNews = useQuery({
    queryKey: ["context-news", activeWindow?.window_start.timestamp_utc, activeWindow?.window_end.timestamp_utc],
    queryFn: () => api.contextNews({
      window_start_utc: activeWindow!.window_start.timestamp_utc!,
      window_end_utc: activeWindow!.window_end.timestamp_utc!,
      pre_minutes: 15,
      post_minutes: 30
    }),
    // 编辑模式或自动标注预填都需要候选新闻；查看模式不需要（已标注详情自带 selected_news）。
    enabled: Boolean(activeWindow && !isAnnotatedView)
  });

  // 切换到不同窗口时，重置编辑表单和上次的自动标注结果。
  useEffect(() => {
    setSelectedNews([]);
    setNoClearNews(false);
    setNotes("");
    setAutoResult(null);
  }, [activeKey]);

  const save = useMutation({
    mutationFn: () => api.saveAnnotation({
      symbol: activeWindow!.symbol,
      window_start_utc: activeWindow!.window_start.timestamp_utc!,
      window_end_utc: activeWindow!.window_end.timestamp_utc!,
      threshold_pct: rule?.threshold_pct ?? 0,
      selected_news_ids: selectedNews,
      no_clear_news: noClearNews,
      notes,
      labeler: autoResult ? `${labeler || ""}${labeler ? " · " : ""}${autoResult.model} (auto, reviewed)` : labeler
    }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["annotation-windows"] });
    }
  });

  const undo = useMutation({
    mutationFn: (id: number) => api.deleteAnnotation(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["annotation-windows"] });
      void queryClient.invalidateQueries({ queryKey: ["annotation-detail"] });
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
    }
  });

  const renderWindowList = (label: string, items: PriceWindow[], emptyText: string) => (
    <div className="window-group">
      <div className="window-group-head">
        <span>{label}</span>
        <strong>{items.length}</strong>
      </div>
      {items.length ? (
        <ul className="window-list">
          {items.map((w) => {
            const key = windowKey(w);
            const isActive = key === activeKey;
            const tone = w.change_pct >= 0 ? "up" : "down";
            const sign = w.change_pct > 0 ? "+" : "";
            return (
              <li key={key}>
                <button
                  type="button"
                  className={`window-item ${tone}${isActive ? " active" : ""}`}
                  onClick={() => setActiveKey(key)}
                >
                  <span className="window-item-icon">
                    {w.annotation_id != null ? <CheckCircle2 size={14} /> : <Circle size={14} />}
                  </span>
                  <span className="window-item-time">
                    {w.window_start.timestamp_bj?.slice(5, 16)} → {w.window_end.timestamp_bj?.slice(11, 16)}
                  </span>
                  <span className="window-item-pct">
                    {sign}{w.change_pct.toFixed(2)}%
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="muted-text small">{emptyText}</p>
      )}
    </div>
  );

  return (
    <section>
      <PageHeader title="新闻标注" subtitle="价格异动窗口与候选新闻关联（已标注 / 未标注 分组）" />
      <div className="annotation-layout">
        <aside className="panel side-panel">
          <SelectControl label="回溯" value={hours} onChange={setHours} options={hoursOptions} />
          <SelectControl
            label="品种"
            value={currentSymbol}
            onChange={(value) => { setSymbol(value); setActiveKey(""); }}
            options={(symbols.data ?? []).map((item) => ({ value: item.symbol, label: `${item.name} (${item.symbol})` }))}
          />
          <TextInput label="标注人" value={labeler} onChange={setLabeler} placeholder="可留空" />

          {windowsQuery.isLoading ? <LoadingState /> : windowsQuery.error ? <ErrorState error={windowsQuery.error} /> : (
            <>
              {renderWindowList("未标注", unannotated, "全部已标注")}
              {renderWindowList("已标注", annotated, "暂无标注")}
            </>
          )}
        </aside>

        <main className="panel">
          <div className="panel-head"><h2>价格窗口</h2></div>
          {!activeWindow ? <EmptyState title="选择一个窗口开始" /> : (
            <>
              <div className="metric-row">
                <Stat label="窗口涨跌" value={`${activeWindow.change_pct > 0 ? "+" : ""}${activeWindow.change_pct.toFixed(2)}%`} tone={activeWindow.change_pct >= 0 ? "up" : "down"} />
                <Stat label="起点价格" value={activeWindow.price_start.toLocaleString()} />
                <Stat label="终点价格" value={activeWindow.price_end.toLocaleString()} />
                <Stat label="窗口分钟" value={`${activeWindow.actual_window_minutes}m`} />
              </div>
              <p className="muted-text">{activeWindow.window_start.timestamp_bj} 至 {activeWindow.window_end.timestamp_bj}</p>

              {isAnnotatedView ? (
                <AnnotatedView
                  detail={annotationDetail.data}
                  isLoading={annotationDetail.isLoading}
                  error={annotationDetail.error}
                  onUndo={() => activeWindow.annotation_id && undo.mutate(activeWindow.annotation_id)}
                  isPending={undo.isPending}
                />
              ) : null}
            </>
          )}
        </main>

        {!isAnnotatedView ? (
          <section className="panel">
            <div className="panel-head">
              <h2>候选新闻</h2>
              <Button
                kind="secondary"
                onClick={() => autoAnnotate.mutate()}
                disabled={!activeWindow || autoAnnotate.isPending}
              >
                <Sparkles size={16} />
                {autoAnnotate.isPending ? "推理中（可能需要 1-3 分钟）..." : "自动标注 (DeepSeek v4-pro)"}
              </Button>
            </div>

            {autoResult ? (
              <details className="reasoning-block" open>
                <summary>
                  <span className="reasoning-tag">推理结果</span>
                  <span>{autoResult.model} · {autoResult.duration_seconds.toFixed(1)}s · 看了 {autoResult.candidate_count} 条候选</span>
                </summary>
                {autoResult.summary ? <p className="reasoning-summary">{autoResult.summary}</p> : null}
                {autoResult.reasoning ? (
                  <pre className="reasoning-content">{autoResult.reasoning}</pre>
                ) : <p className="muted-text small">模型未返回 reasoning_content（thinking 模式可能未生效）。</p>}
              </details>
            ) : null}

            {autoAnnotate.error ? <ErrorState error={autoAnnotate.error} /> : null}

            {!activeWindow ? <EmptyState title="选择一个未标注窗口" /> :
              contextNews.isLoading ? <LoadingState /> :
              contextNews.error ? <ErrorState error={contextNews.error} /> : (
              <DataTable<NewsItem>
                rows={contextNews.data?.items ?? []}
                empty="窗口前 15 / 后 30 分钟没有候选新闻"
                columns={[
                  { key: "select", header: "选择", cell: (row) => <input type="checkbox" checked={selectedNews.includes(row.id)} onChange={(event) => setSelectedNews((ids) => event.target.checked ? [...ids, row.id] : ids.filter((id) => id !== row.id))} /> },
                  { key: "time", header: "时间", cell: (row) => row.timestamp_bj?.slice(5, 16) },
                  { key: "source", header: "来源", cell: (row) => row.source },
                  { key: "score", header: "LLM", cell: (row) => row.llm_importance ?? "—" },
                  { key: "title", header: "标题", cell: (row) => row.title }
                ]}
              />
            )}
            <label className="checkline">
              <input type="checkbox" checked={noClearNews} onChange={(event) => setNoClearNews(event.target.checked)} />
              没有明确新闻触发
            </label>
            <label className="field full">
              <span>备注 / 因果归因</span>
              <textarea value={notes} onChange={(event) => setNotes(event.target.value)} placeholder="自动标注后会自动填入 summary，可手动修改" />
            </label>
            <Button disabled={!activeWindow || save.isPending} onClick={() => save.mutate()}>
              <Save size={16} />保存标注
            </Button>
            {save.data ? <div className="task-banner succeeded">已保存标注 #{save.data.id}</div> : null}
            {save.error ? <ErrorState error={save.error} /> : null}
          </section>
        ) : null}
      </div>
    </section>
  );
}

function AnnotatedView({
  detail,
  isLoading,
  error,
  onUndo,
  isPending
}: {
  detail: AnnotationDetail | undefined;
  isLoading: boolean;
  error: unknown;
  onUndo: () => void;
  isPending: boolean;
}) {
  if (isLoading) return <LoadingState />;
  if (error) return <ErrorState error={error} />;
  if (!detail) return null;

  return (
    <section className="annotated-view">
      <div className="panel-head" style={{ marginTop: 14 }}>
        <h3 style={{ margin: 0, fontSize: 14 }}>已标注详情 #{detail.id}</h3>
        <Button kind="secondary" onClick={onUndo} disabled={isPending}>
          <RotateCcw size={16} />
          {isPending ? "撤销中..." : "撤销标注"}
        </Button>
      </div>

      <p className="muted-text small">
        标注人：{detail.labeler || "—"} · 创建：{detail.created_at.timestamp_bj} · 更新：{detail.updated_at.timestamp_bj}
      </p>

      {detail.no_clear_news ? (
        <p className="reasoning-summary">该窗口被标注为「没有明确新闻触发」。</p>
      ) : null}

      {detail.notes ? (
        <div className="reasoning-summary">{detail.notes}</div>
      ) : null}

      {detail.selected_news.length ? (
        <DataTable<NewsItem>
          rows={detail.selected_news}
          columns={[
            { key: "time", header: "时间", cell: (row) => row.timestamp_bj?.slice(5, 16) },
            { key: "source", header: "来源", cell: (row) => row.source },
            { key: "score", header: "LLM", cell: (row) => row.llm_importance ?? "—" },
            { key: "title", header: "标题", cell: (row) => row.title }
          ]}
        />
      ) : !detail.no_clear_news ? (
        <p className="muted-text small">未关联任何新闻条目。</p>
      ) : null}
    </section>
  );
}
