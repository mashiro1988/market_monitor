import { useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Save } from "lucide-react";
import { api } from "../api/client";
import type { NewsItem, PriceWindow } from "../api/types";
import { Button, PageHeader, SelectControl, Stat, TextInput } from "../components/Controls";
import { DataTable } from "../components/DataTable";
import { EmptyState, ErrorState, LoadingState } from "../components/StateViews";

export function AnnotationsPage() {
  const [hours, setHours] = useState("72");
  const [symbol, setSymbol] = useState("");
  const [windowId, setWindowId] = useState("");
  const [selectedNews, setSelectedNews] = useState<number[]>([]);
  const [noClearNews, setNoClearNews] = useState(false);
  const [notes, setNotes] = useState("");
  const [labeler, setLabeler] = useState("");
  const rules = useQuery({ queryKey: ["annotation-rules"], queryFn: api.priceRules });
  const symbols = useQuery({ queryKey: ["annotation-symbols", hours], queryFn: () => api.annotationSymbols(Number(hours)) });
  const currentSymbol = symbol || symbols.data?.[0]?.symbol || "";
  const rule = rules.data?.find((item) => item.symbol === currentSymbol);
  const windows = useQuery({
    queryKey: ["annotation-windows", currentSymbol, hours],
    queryFn: () => api.annotationWindows({ symbol: currentSymbol, hours: Number(hours) }),
    enabled: Boolean(currentSymbol)
  });
  const selectedWindow = useMemo(() => {
    const id = windowId || "0";
    return windows.data?.[Number(id)] ?? windows.data?.[0];
  }, [windowId, windows.data]);
  const contextNews = useQuery({
    queryKey: ["context-news", selectedWindow?.window_start.timestamp_utc, selectedWindow?.window_end.timestamp_utc],
    queryFn: () => api.contextNews({
      window_start_utc: selectedWindow!.window_start.timestamp_utc!,
      window_end_utc: selectedWindow!.window_end.timestamp_utc!,
      minutes: 30
    }),
    enabled: Boolean(selectedWindow?.window_start.timestamp_utc && selectedWindow?.window_end.timestamp_utc)
  });
  const save = useMutation({
    mutationFn: () => api.saveAnnotation({
      symbol: selectedWindow!.symbol,
      window_start_utc: selectedWindow!.window_start.timestamp_utc!,
      window_end_utc: selectedWindow!.window_end.timestamp_utc!,
      threshold_pct: rule?.threshold_pct ?? 0,
      selected_news_ids: selectedNews,
      no_clear_news: noClearNews,
      notes,
      labeler
    })
  });

  const windowOptions = (windows.data ?? []).map((item: PriceWindow, index) => ({
    value: String(index),
    label: `${item.window_start.timestamp_bj?.slice(5, 16)} → ${item.window_end.timestamp_bj?.slice(11, 16)} · ${item.change_pct > 0 ? "+" : ""}${item.change_pct.toFixed(2)}%`
  }));

  return (
    <section>
      <PageHeader title="新闻标注" subtitle="价格异动窗口与候选新闻关联" />
      <div className="annotation-layout">
        <aside className="panel side-panel">
          <SelectControl label="回溯" value={hours} onChange={setHours} options={[
            { label: "24小时", value: "24" },
            { label: "72小时", value: "72" },
            { label: "7天", value: "168" }
          ]} />
          <SelectControl
            label="品种"
            value={currentSymbol}
            onChange={(value) => { setSymbol(value); setWindowId(""); setSelectedNews([]); }}
            options={(symbols.data ?? []).map((item) => ({ value: item.symbol, label: `${item.name} (${item.symbol})` }))}
          />
          <SelectControl label="窗口" value={windowId || "0"} onChange={(value) => { setWindowId(value); setSelectedNews([]); }} options={windowOptions.length ? windowOptions : [{ label: "暂无窗口", value: "0" }]} />
          <TextInput label="标注人" value={labeler} onChange={setLabeler} placeholder="可留空" />
        </aside>

        <main className="panel">
          <div className="panel-head"><h2>价格窗口</h2></div>
          {windows.isLoading ? <LoadingState /> : windows.error ? <ErrorState error={windows.error} /> : selectedWindow ? (
            <>
              <div className="metric-row">
                <Stat label="窗口涨跌" value={`${selectedWindow.change_pct > 0 ? "+" : ""}${selectedWindow.change_pct.toFixed(2)}%`} tone={selectedWindow.change_pct >= 0 ? "up" : "down"} />
                <Stat label="起点价格" value={selectedWindow.price_start.toLocaleString()} />
                <Stat label="终点价格" value={selectedWindow.price_end.toLocaleString()} />
                <Stat label="窗口分钟" value={`${selectedWindow.actual_window_minutes}m`} />
              </div>
              <p className="muted-text">{selectedWindow.window_start.timestamp_bj} 至 {selectedWindow.window_end.timestamp_bj}</p>
            </>
          ) : <EmptyState title="当前设置下没有价格异动窗口" />}
        </main>

        <section className="panel">
          <div className="panel-head"><h2>候选新闻</h2></div>
          {contextNews.isLoading ? <LoadingState /> : contextNews.error ? <ErrorState error={contextNews.error} /> : (
            <DataTable<NewsItem>
              rows={contextNews.data?.items ?? []}
              empty="窗口前后 30 分钟没有新闻"
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
            <span>备注</span>
            <textarea value={notes} onChange={(event) => setNotes(event.target.value)} />
          </label>
          <Button disabled={!selectedWindow || save.isPending} onClick={() => save.mutate()}>
            <Save size={16} />保存标注
          </Button>
          {save.data ? <div className="task-banner succeeded">已保存标注 #{save.data.id}</div> : null}
          {save.error ? <ErrorState error={save.error} /> : null}
        </section>
      </div>
    </section>
  );
}
