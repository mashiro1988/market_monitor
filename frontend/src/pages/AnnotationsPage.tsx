import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CornerDownRight, Circle, RotateCcw, Save, Sparkles } from "lucide-react";
import { api } from "../api/client";
import type { AnnotationListItem, AutoAnnotateResponse, NewsItem, PriceWindow } from "../api/types";
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

export function AnnotationsPage() {
  const queryClient = useQueryClient();
  const [hours, setHours] = useState("72");
  const [symbol, setSymbol] = useState("");
  const [activeKey, setActiveKey] = useState<string>("");

  // 编辑表单状态
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

  // 切换窗口时清空表单 + 上次的自动标注结果。
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
      labeler: autoResult ? `${labeler || ""}${labeler ? " · " : ""}${autoResult.model} (auto, reviewed)` : labeler,
      // 训练数据：把当前展示的全部候选新闻 ID 一起存（即使是纯人工标注，也保留负样本信息）。
      candidate_news_ids: (contextNews.data?.items ?? []).map((item) => item.id),
      // 自动标注流程：保存 LLM 原始推理 + 摘要，与人改后的 notes 分开。
      auto_reasoning: autoResult?.reasoning ?? null,
      auto_summary: autoResult?.summary ?? null
    }),
    onSuccess: () => {
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
    }
  });

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

      <section className="panel annotation-block">
        <div className="panel-head">
          <h2>未标注 ({groups.length})</h2>
          <span className="muted-text small">连续异动会被聚合为一个事件，只标第一次；后续延伸窗口（↳）只展示不标注</span>
        </div>

        {windowsQuery.isLoading ? <LoadingState /> :
         windowsQuery.error ? <ErrorState error={windowsQuery.error} /> :
         !groups.length ? <EmptyState title="该回溯期内没有未标注的价格异动事件" /> : (
          <div className="annotation-work">
            <aside className="annotation-work-list">
              <ul className="window-list">
                {groups.map(({ primary, secondaries }) => {
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
                      >
                        <span className="window-item-icon"><Circle size={14} /></span>
                        <span className="window-item-time">
                          {primary.window_start.timestamp_bj?.slice(5, 16)} → {primary.window_end.timestamp_bj?.slice(11, 16)}
                        </span>
                        <span className="window-item-pct">
                          {sign}{primary.change_pct.toFixed(2)}%
                        </span>
                      </button>
                      {secondaries.length ? (
                        <ul className="window-secondary-list">
                          {secondaries.map((s) => {
                            const sTone = s.change_pct >= 0 ? "up" : "down";
                            const sSign = s.change_pct > 0 ? "+" : "";
                            return (
                              <li key={windowKey(s)}>
                                <div className={`window-item secondary ${sTone}`} title="连续异动延伸窗口，已聚合到上方事件，不需单独标注">
                                  <span className="window-item-icon"><CornerDownRight size={12} /></span>
                                  <span className="window-item-time">
                                    {s.window_end.timestamp_bj?.slice(11, 16)}
                                  </span>
                                  <span className="window-item-pct">
                                    {sSign}{s.change_pct.toFixed(2)}%
                                  </span>
                                </div>
                              </li>
                            );
                          })}
                        </ul>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
            </aside>

            <div className="annotation-work-detail">
              {!activeWindow ? <EmptyState title="选择一个窗口" /> : (
                <>
                  <div className="metric-row">
                    <Stat label="窗口涨跌" value={`${activeWindow.change_pct > 0 ? "+" : ""}${activeWindow.change_pct.toFixed(2)}%`} tone={activeWindow.change_pct >= 0 ? "up" : "down"} />
                    <Stat label="起点价格" value={activeWindow.price_start.toLocaleString()} />
                    <Stat label="终点价格" value={activeWindow.price_end.toLocaleString()} />
                    <Stat label="窗口分钟" value={`${activeWindow.actual_window_minutes}m`} />
                  </div>
                  <p className="muted-text small">{activeWindow.window_start.timestamp_bj} 至 {activeWindow.window_end.timestamp_bj}</p>

                  <div className="auto-annotate-bar">
                    <Button
                      kind="secondary"
                      onClick={() => autoAnnotate.mutate()}
                      disabled={autoAnnotate.isPending}
                    >
                      <Sparkles size={16} />
                      {autoAnnotate.isPending ? "推理中（可能需要 1-3 分钟）..." : "自动标注 (DeepSeek v4-pro)"}
                    </Button>
                    <span className="muted-text small">候选新闻取窗口前 15 / 后 30 分钟</span>
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

                  <h3 className="block-subhead">候选新闻</h3>
                  {contextNews.isLoading ? <LoadingState /> :
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
                  <Button disabled={save.isPending} onClick={() => save.mutate()}>
                    <Save size={16} />保存标注
                  </Button>
                  {save.data ? <div className="task-banner succeeded">已保存标注 #{save.data.id}</div> : null}
                  {save.error ? <ErrorState error={save.error} /> : null}
                </>
              )}
            </div>
          </div>
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
    </section>
  );
}
