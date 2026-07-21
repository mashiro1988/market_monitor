import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, Circle, Layers, RotateCcw, Save, Sparkles } from "lucide-react";
import { api } from "../api/client";
import type { AnnotationListItem, AutoAnnotateBatchItem, AutoAnnotateResponse, NewsItem, PriceWindow, ReferenceChange } from "../api/types";

// 2026-07-19 拍板：批量=逐窗口串行循环（每次调用只喂 1 个窗口）。曾用 3/片省时间，但个人站
// 不赶时间——单窗口上下文最小、reasoning 预算最省、失败重试粒度最细。后端
// AUTO_ANNOTATE_BATCH_LIMIT 仍是 10，只是 API 硬上限，不是日常工况。
const AUTO_BATCH_CHUNK = 1;
import { Button, PageHeader } from "../components/Controls";
import { DataTable } from "../components/DataTable";
import { EmptyState, ErrorState, LoadingState } from "../components/StateViews";
import { LinkagePanel, REF_COLORS } from "../components/LinkagePanel";
import { WindowNetValueChart } from "../components/WindowNetValueChart";
import { classMeta } from "./behaviorFormat";

// 2026-07-19 简化：个人站单品种工作台。行为段只产 BTC/USDT（品种下拉曾是假选项）；
// 回溯固定全量（hours=0，后端从最早行为段起算）；标注人输入退役（自动标注仍落 model 名）。
const SYMBOL = "BTC/USDT";
const HOURS_ALL = 0;
// 已标注列表分页（2026-07-20）：全量回溯后只增不减，20 条/页
const ANN_PAGE_SIZE = 20;

function windowKey(w: PriceWindow): string {
  return `${w.symbol}|${w.window_start.timestamp_utc}|${w.window_end.timestamp_utc}`;
}

// 单个宏观对标的展示文本 + 涨跌色类。本身 / 无数据（周末/休市）→ 中性灰。
// 收益率类品种（unit=bp）显示基点变动，其余显示涨跌%。
function fmtRefMove(value: number | null | undefined, unit?: string | null): string {
  if (value == null) return "—";
  const sign = value > 0 ? "+" : "";
  return unit === "bp" ? `${sign}${value.toFixed(1)}bp` : `${sign}${value.toFixed(2)}%`;
}

function fmtRefPrice(value: number | null | undefined, unit?: string | null): string {
  if (value == null) return "—";
  if (unit === "bp") return `${value.toFixed(2)}%`;                 // 收益率本身
  if (Math.abs(value) >= 1000) return value.toLocaleString(undefined, { maximumFractionDigits: 0 });
  if (Math.abs(value) >= 100) return value.toFixed(1);
  return value.toFixed(2);
}

// Phase 2（2026-07-09 拍板）：对标行 = 绝对起点 → 终点 + 窗口内涨跌；
// 同步相关与前/后段展示退役（时序上下文由 rolling S 曲线 + 档位色带承担）。
function fmtRef(ref: ReferenceChange): { text: string; cls: string } {
  const span = ref.price_start != null && ref.price_end != null
    ? `${fmtRefPrice(ref.price_start, ref.unit)} → ${fmtRefPrice(ref.price_end, ref.unit)} `
    : "";
  const text = `${ref.label}${ref.is_self ? "(本身)" : ""} ${span}(${fmtRefMove(ref.pct, ref.unit)})`;
  if (ref.pct == null) return { text, cls: "ref-neutral" };
  return { text, cls: ref.pct >= 0 ? "up-text" : "down-text" };
}

// 品种窗口相关性面板（2026-07-10 设计；2026-07-19 简化：改名 + 三列分块 + ESS 挂 BTC 块）：
// 每格 = 参照的绝对起终点 + 窗口涨跌 + 该参照的 rolling |S| 峰值读数（ESS<5 标证据薄）。
// 这是 DeepSeek 判 driver 用的同一套证据，人工审核所见即所判。
function sBadge(entry: { s: number; ess: number } | undefined): { text: string; cls: string; title: string } {
  if (!entry) return { text: "S —", cls: "s-badge none", title: "无对照（休市/数据缺）" };
  const a = Math.abs(entry.s);
  const cls = a >= 0.5 ? "s-badge strong" : a >= 0.3 ? "s-badge mid" : "s-badge weak";
  const sTxt = a < 0.005 ? "0.00" : `${entry.s > 0 ? "+" : ""}${entry.s.toFixed(2)}`;
  return {
    text: `S ${sTxt}`,
    cls,
    title: "段窗内 rolling |S| 峰值读数 · ≥0.5 共振 / 0.3–0.5 弱 / <0.3 独立",
  };
}

function WindowEvidence({ win }: { win: PriceWindow }) {
  const refs = win.references ?? [];
  if (!refs.length) return null;
  const scores = (win.s_scores ?? {}) as Record<string, { s: number; ess: number }>;
  // ESS 权重来自 BTC 侧，覆盖齐全时各参照相同 → 取最薄值（保守口径），读数挂在 BTC（本身）块上
  const essVals = Object.values(scores).map((v) => v.ess).filter((v) => Number.isFinite(v));
  const essMin = essVals.length ? Math.min(...essVals) : null;
  const ordered = [...refs].sort((a, b) => Number(b.is_self) - Number(a.is_self));   // 本身置顶作基准
  return (
    <div className="subsection">
      <div className="subsection-head">
        <span className="subsection-title">品种窗口相关性</span>
      </div>
      <div className="evidence-grid">
        {ordered.map((ref) => {
          const move = fmtRefMove(ref.pct, ref.unit);
          const span = ref.price_start != null && ref.price_end != null
            ? `${fmtRefPrice(ref.price_start, ref.unit)} → ${fmtRefPrice(ref.price_end, ref.unit)}`
            : "—";
          const badge = ref.is_self ? null : sBadge(scores[ref.symbol]);
          const moveCls = ref.pct == null ? "ref-neutral" : ref.pct >= 0 ? "up-text" : "down-text";
          return (
            <div key={ref.symbol} className={`evidence-row${ref.is_self ? " self" : ""}`}>
              <span className="evidence-label">
                <i className="ref-dot" style={{ background: REF_COLORS[ref.symbol] ?? "#8ea0b6" }} />
                {ref.label}{ref.is_self ? "（本身）" : ""}
              </span>
              <span className="evidence-span">{span}</span>
              <span className={`evidence-move ${moveCls}`}>{move}</span>
              {badge ? (
                <span className={badge.cls} title={badge.title}>{badge.text}</span>
              ) : (
                <span
                  className={`s-badge selfmark${essMin != null && essMin < 5 ? " thin" : ""}`}
                  title="证据厚度 ESS（各参照读数取最薄值）：BTC 异动能量摊在几根 K 线上，<5 = 证据薄"
                >
                  {essMin != null ? `ESS ${essMin.toFixed(1)}${essMin < 5 ? " 薄⚠" : ""}` : "ESS —"}
                </span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// sessionStorage 持久化 in-progress 标注：批量 AI 结果 + 用户对每个窗口的手动修改（角色/反应类型/notes）
// + 当前选中窗口。切到别的页面再回来不会丢；标注保存成功后该 key 会被清理。
// Phase3a：标签体系升级为 driver/redundant/noise；旧 v2 草稿里可能残留 retired roles，直接弃读。
const STORAGE_KEY = "annotations.session.phase3a";

type StoredState = {
  batchByKey: [string, AutoAnnotateBatchItem][];
  batchMeta: { reasoning: string; model: string; duration_seconds: number } | null;
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

// —— 标签字典（Phase3a：人/LLM 逐条标 driver/redundant；post_hoc/contradictory 退场并入 noise）——
const ROLE_OPTIONS = [
  { value: "noise", label: "噪音" },
  { value: "driver", label: "驱动" },
  { value: "redundant", label: "同簇冗余" },
] as const;

// 置信度三档（高/中/低 → 固定数值）；保留——训模型时作样本置信权重用。
// Phase 2 窗口级三类（= 人工审核；保存必选，回写行为段 human_class）
const WINDOW_CLASS_OPTIONS = [
  { value: "news_driven", label: "新闻驱动" },
  { value: "pure_resonance", label: "纯宏观共振" },
  { value: "sentiment_tech", label: "情绪·技术面" },
];

const CONFIDENCE_TIERS = [
  { value: 0.9, label: "高" },
  { value: 0.65, label: "中" },
  { value: 0.3, label: "低" },
] as const;

// AI 置信度吸附到三档（2026-07-20）：DeepSeek 返回 0.85 之类的原始值时对不上三档按钮的
// 固定数值，看起来像"没自动打标"。吸附到最近一档让按钮直接亮起，落库口径也统一为三档。
function snapConfidence(v: number | null | undefined): number | null {
  if (v == null) return null;
  let best: number = CONFIDENCE_TIERS[0].value;
  for (const t of CONFIDENCE_TIERS) {
    if (Math.abs(t.value - v) < Math.abs(best - v)) best = t.value;
  }
  return best;
}


function rolesEqual(a: Record<number, string>, b: Record<number, string>): boolean {
  const ka = Object.keys(a);
  if (ka.length !== Object.keys(b).length) return false;
  for (const k of ka) if (a[Number(k)] !== b[Number(k)]) return false;
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

  const [activeKey, setActiveKey] = useState<string>(initialStored.activeKey ?? "");

  // 编辑表单状态（Phase3a：每条新闻 causal_role + 置信度/summary）
  const [newsRoles, setNewsRoles] = useState<Record<number, string>>({});   // 只存非 noise
  const [confidence, setConfidence] = useState<number | null>(null);
  const [windowClass, setWindowClass] = useState<string | null>(null);
  const [notes, setNotes] = useState("");
  const [autoResult, setAutoResult] = useState<AutoAnnotateResponse | null>(null);
  const [saveValidation, setSaveValidation] = useState("");

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

  const rules = useQuery({ queryKey: ["annotation-rules"], queryFn: api.priceRules });
  const rule = rules.data?.find((item) => item.symbol === SYMBOL);

  const windowsQuery = useQuery({
    queryKey: ["annotation-windows", SYMBOL],
    queryFn: () => api.annotationWindows({ symbol: SYMBOL, hours: HOURS_ALL })
  });

  const [annPage, setAnnPage] = useState(1);
  const annotatedListQuery = useQuery({
    queryKey: ["annotation-list", SYMBOL, annPage],
    queryFn: () => api.annotationsList({ symbol: SYMBOL, hours: HOURS_ALL, page: annPage, page_size: ANN_PAGE_SIZE }),
    placeholderData: (previous) => previous
  });
  const annTotal = annotatedListQuery.data?.total ?? 0;
  const annPages = Math.max(1, Math.ceil(annTotal / ANN_PAGE_SIZE));
  // 撤销把最后一页删空时回退到最后一个有效页
  useEffect(() => {
    if (annPage > annPages) setAnnPage(annPages);
  }, [annPage, annPages]);

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

  const activePre = activeWindow?.context_pre_minutes ?? 60;   // Part B：候选新闻窗口 ±1h
  const contextNews = useQuery({
    queryKey: ["context-news", activeWindow?.window_start.timestamp_utc, activeWindow?.window_end.timestamp_utc, activePre],
    queryFn: () => api.contextNews({
      window_start_utc: activeWindow!.window_start.timestamp_utc!,
      window_end_utc: activeWindow!.window_end.timestamp_utc!,
      pre_minutes: activePre,
      post_minutes: 60
    }),
    enabled: Boolean(activeWindow)
  });

  // 切换窗口时：如果该窗口在批量结果缓存里有，回填表单和 autoResult；否则清空。
  // 关键：每个 setter 都用幂等更新（值未变就返回 prev），让 React 通过 Object.is 跳过 re-render。
  // 否则用户每次改动 → write-back 写 batchByKey → 本 effect 重跑 → autoResult 新对象 → 整个推理面板
  // 子树多余 re-render → 视觉抖动。
  useEffect(() => {
    const cached = batchByKey.get(activeKey);
    // 本 effect 是 windowClass 的唯一所有者（2026-07-20 修复）：此前另有一个"三类预填"effect
    // 在切窗口时把 windowClass 重置为 human_class ?? null，晚于本 effect 执行，
    // 把批量 AI 结果里的 window_class 冲掉——这就是"批量打标后驱动类型没自动填上"的原因。
    const wclassOf = (v: string | null | undefined) => v ?? activeWindow?.human_class ?? null;
    if (cached && batchMeta) {
      setNewsRoles((prev) => rolesEqual(prev, cached.news_roles ?? {}) ? prev : (cached.news_roles ?? {}));
      const conf = snapConfidence(cached.confidence);
      setConfidence((prev) => prev === conf ? prev : conf);
      const wclass = wclassOf(cached.window_class);
      setWindowClass((prev) => prev === wclass ? prev : wclass);
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
          no_clear_news: Object.values(cached.news_roles ?? {}).every((role) => role !== "driver"),
          news_roles: cached.news_roles ?? {},
          market_reaction_type: null,
          confidence: cached.confidence ?? null,
          window_class: cached.window_class ?? null,
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
      setNewsRoles((prev) => rolesEqual(prev, cached.news_roles ?? {}) ? prev : (cached.news_roles ?? {}));
      const conf = snapConfidence(cached.confidence);
      setConfidence((prev) => prev === conf ? prev : conf);
      const wclass = wclassOf(cached.window_class);
      setWindowClass((prev) => prev === wclass ? prev : wclass);
      setNotes((prev) => prev === cached.summary ? prev : cached.summary);
      setAutoResult((prev) => prev === null ? prev : null);
    } else {
      setNewsRoles((prev) => Object.keys(prev).length === 0 ? prev : {});
      setConfidence((prev) => prev === null ? prev : null);
      const wclass = wclassOf(null);
      setWindowClass((prev) => prev === wclass ? prev : wclass);
      setNotes((prev) => prev === "" ? prev : "");
      setAutoResult((prev) => prev === null ? prev : null);
    }
  }, [activeKey, batchByKey, batchMeta, activeWindow?.human_class]);

  // 用户编辑（新闻角色 / 置信度 / notes）在**事件处理器里**同步写回 batchByKey 草稿。
  // 不能用 effect 镜像表单→缓存：activeKey 切换时 hydrate（缓存→表单）和写回（表单→缓存）
  // 会在同一次提交里各自用对方的旧快照互相覆盖，两个存储的值从此每轮渲染互换、
  // 永不收敛（实测 checked 被以 ~6500 次/秒翻转——就是「勾选框抖动」）。
  // 事件驱动写回只在用户真实操作时发生，结构上无环；hydrate 保持唯一的 缓存→表单 方向。
  const updateDraft = useCallback(
    (patch: Partial<Pick<AutoAnnotateBatchItem, "news_roles" | "confidence" | "summary" | "window_class">>) => {
      if (!activeKey || !activeWindow) return;
      setBatchByKey((prev) => {
        const existing = prev.get(activeKey);
        const merged = {
          symbol: activeWindow.symbol,
          window_start_utc: activeWindow.window_start.timestamp_utc!,
          window_end_utc: activeWindow.window_end.timestamp_utc!,
          news_roles: existing?.news_roles ?? {},
          market_reaction_type: null,
          confidence: existing?.confidence ?? null,
          window_class: existing?.window_class ?? null,
          summary: existing?.summary ?? "",
          reasoning: existing?.reasoning ?? "",
          candidate_count: existing?.candidate_count ?? 0,
          candidate_news_ids: existing?.candidate_news_ids ?? [],
          ...patch
        };
        // 派生兼容字段（与后端 _derive_compat_fields 同口径）
        const roleIds = Object.keys(merged.news_roles).map(Number);
        const selected = roleIds.filter((id) => merged.news_roles[id] === "driver");
        const noClear = selected.length === 0 || merged.market_reaction_type === "no_news_driver";
        const full: AutoAnnotateBatchItem = { ...merged, selected_news_ids: selected, no_clear_news: noClear };
        // 用户把窗口清回全空且无 AI 痕迹 → 删草稿而不是存空条目（保持「没动过=无草稿」语义，
        // 批量自动标注的 pending 列表也依赖这一点）。
        const empty = !roleIds.length && !merged.summary && !merged.reasoning
          && merged.confidence == null && !merged.window_class;
        if (empty) {
          if (!prev.has(activeKey)) return prev;
          const next = new Map(prev);
          next.delete(activeKey);
          return next;
        }
        const next = new Map(prev);
        next.set(activeKey, full);
        return next;
      });
    },
    [activeKey, activeWindow]
  );

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
          activeKey
        };
        sessionStorage.setItem(STORAGE_KEY, JSON.stringify(data));
      } catch {
        // sessionStorage 满 / disabled 时静默失败
      }
    }, 300);
    return () => clearTimeout(timer);
  }, [batchByKey, batchMeta, activeKey]);

  const save = useMutation({
    mutationFn: () => api.saveAnnotation({
      symbol: activeWindow!.symbol,
      window_start_utc: activeWindow!.window_start.timestamp_utc!,
      window_end_utc: activeWindow!.window_end.timestamp_utc!,
      threshold_pct: rule?.threshold_pct ?? 0,
      context_pre_minutes: activePre,
      // v2 标签；selected_news_ids / no_clear_news 由后端从 news_roles 派生
      news_roles: newsRoles,
      confidence,
      window_class: windowClass,
      notes,
      labeler: autoResult ? `${autoResult.model} (auto, reviewed)` : null,
      // 训练数据：把当前展示的全部候选新闻 ID 一起存（即使是纯人工标注，也保留负样本信息）。
      candidate_news_ids: (contextNews.data?.items ?? []).map((item) => item.id),
      // 自动标注流程：保存 LLM 原始推理 / 摘要 / 原始角色（与人改后的分开存——人机分歧是难例信号）。
      auto_reasoning: autoResult?.reasoning ?? null,
      auto_summary: autoResult?.summary ?? null,
      auto_news_roles: autoResult?.news_roles ?? null
    }),
    onSuccess: () => {
      setSaveValidation("");
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
      // human_class 已回写行为段 → 结论页构成一并刷新
      void queryClient.invalidateQueries({ queryKey: ["behavior-daily"] });
      void queryClient.invalidateQueries({ queryKey: ["behavior-segments"] });
    }
  });

  const undo = useMutation({
    mutationFn: (id: number) => api.deleteAnnotation(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["annotation-windows"] });
      void queryClient.invalidateQueries({ queryKey: ["annotation-list"] });
      // human_class 已回写行为段 → 结论页构成一并刷新
      void queryClient.invalidateQueries({ queryKey: ["behavior-daily"] });
      void queryClient.invalidateQueries({ queryKey: ["behavior-segments"] });
    }
  });

  const evalToggle = useMutation({
    mutationFn: ({ id, value }: { id: number; value: boolean }) => api.setAnnotationEvalSet(id, value),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["annotation-list"] });
    }
  });

  // 内容标签：库（下拉选项）+ 人工改一条新闻的标签
  const tagOptions = useQuery({ queryKey: ["tag-options"], queryFn: api.tagOptions, staleTime: 60 * 60_000 });
  const updateTags = useMutation({
    mutationFn: ({ id, body }: { id: number; body: { topic?: string; magnitude_tier?: string; news_direction?: string } }) =>
      api.updateNewsTags(id, body),
    onSuccess: () => { void queryClient.invalidateQueries({ queryKey: ["context-news"] }); }
  });

  const applyAutoResult = useCallback((result: AutoAnnotateResponse) => {
      setAutoResult(result);
      setNewsRoles(result.news_roles ?? {});
      setConfidence(snapConfidence(result.confidence));
      if (result.window_class) setWindowClass(result.window_class);
      if (result.confidence != null) setSaveValidation("");
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
            no_clear_news: Object.values(result.news_roles ?? {}).every((role) => role !== "driver"),
            news_roles: result.news_roles ?? {},
            market_reaction_type: null,
            confidence: result.confidence ?? null,
            window_class: result.window_class ?? null,
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
  }, [activeWindow, activeKey]);

  const autoAnnotate = useMutation({
    mutationFn: () => api.autoAnnotate({
      symbol: activeWindow!.symbol,
      window_start_utc: activeWindow!.window_start.timestamp_utc!,
      window_end_utc: activeWindow!.window_end.timestamp_utc!,
      threshold_pct: rule?.threshold_pct ?? 0,
      context_pre_minutes: activePre
    }),
    onSuccess: applyAutoResult,
  });

  // 互动重标：把当前角色/摘要/置信度作为「上一轮」+ 用户纠正，多轮对话再调 reasoner。
  const [refineMessage, setRefineMessage] = useState("");
  const refine = useMutation({
    mutationFn: () => api.autoAnnotateRefine({
      symbol: activeWindow!.symbol,
      window_start_utc: activeWindow!.window_start.timestamp_utc!,
      window_end_utc: activeWindow!.window_end.timestamp_utc!,
      threshold_pct: rule?.threshold_pct ?? 0,
      context_pre_minutes: activePre,
      prior_news_roles: newsRoles,
      prior_summary: notes,
      prior_confidence: confidence,
      user_message: refineMessage,
    }),
    onSuccess: (result) => { applyAutoResult(result); setRefineMessage(""); },
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
            threshold_pct: rule?.threshold_pct ?? 0,
            context_pre_minutes: w.context_pre_minutes ?? 30
          }))
        });
        const chunkEntries = new Map<string, AutoAnnotateBatchItem>();
        for (const item of response.results) {
          accum.set(`${item.symbol}|${item.window_start_utc}|${item.window_end_utc}`, item);
          chunkEntries.set(`${item.symbol}|${item.window_start_utc}|${item.window_end_utc}`, item);
        }
        // 多片时各 chunk 的 reasoning 互相独立，前端只展示最近一片，避免拼太长。
        lastReasoning = response.reasoning;
        lastModel = response.model;
        totalDuration += response.duration_seconds;
        setBatchByKey((prev) => {
          const next = new Map(prev);
          chunkEntries.forEach((item, key) => next.set(key, item));
          return next;
        });
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
  const saveDisabled = save.isPending || contextNews.isLoading || contextNews.isFetching || !contextNews.data;

  // 候选新闻表 columns：必须 useMemo，否则父级每次 re-render（hover、textarea 输入等）都新建
  // columns 数组 + 新 cell 闭包，DataTable 收到新 props → 整张表全部重渲。
  // setNewsRole 用 useCallback 保持稳定；columns 只在 newsRoles 变化时重建（此时角色下拉
  // 的选中值也确实需要更新，是必要的重建）。
  const setNewsRole = useCallback((id: number, role: string) => {
    const next = { ...newsRoles };
    if (role === "noise") delete next[id];   // noise 是默认值，不入草稿
    else next[id] = role;
    setNewsRoles(next);
    updateDraft({ news_roles: next });
  }, [newsRoles, updateDraft]);

  const newsColumns = useMemo(() => [
    {
      key: "role",
      header: "角色",
      cell: (row: NewsItem) => {
        const value = newsRoles[row.id] ?? "noise";
        return (
          <select
            className={`role-select ${value !== "noise" ? "role-active" : ""}`}
            value={value}
            onChange={(event) => setNewsRole(row.id, event.target.value)}
          >
            {ROLE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        );
      }
    },
    { key: "time", header: "时间", cell: (row: NewsItem) => row.timestamp_bj?.slice(5, 16) },
    { key: "source", header: "来源", cell: (row: NewsItem) => row.source },
    { key: "score", header: "LLM", cell: (row: NewsItem) => row.llm_importance ?? "—" },
    {
      key: "tags",
      header: "内容标签（可改）",
      cell: (row: NewsItem) => {
        const opts = tagOptions.data;
        const dirColor =
          row.news_direction === "利多" ? "#16a34a" :
          row.news_direction === "利空" ? "#dc2626" : undefined;
        const sel = (val: string | null, list: string[] | undefined, field: "topic" | "magnitude_tier" | "news_direction", color?: string) => (
          <select
            value={val ?? ""}
            style={{ fontSize: 13, padding: "1px 2px", color, maxWidth: field === "topic" ? 110 : 64 }}
            onChange={(e) => updateTags.mutate({ id: row.id, body: { [field]: e.target.value || null } })}
            title={field === "topic" ? "主题" : field === "magnitude_tier" ? "量级" : "方向"}
          >
            <option value="">{field === "topic" ? "未打标" : "—"}</option>
            {(list ?? []).map((o) => <option key={o} value={o}>{o}</option>)}
          </select>
        );
        return (
          <span style={{ display: "inline-flex", gap: 3, alignItems: "center", flexWrap: "wrap" }}>
            {sel(row.topic, opts?.topics, "topic")}
            {sel(row.magnitude_tier, opts?.magnitudes, "magnitude_tier")}
            {sel(row.news_direction, opts?.directions, "news_direction", dirColor)}
          </span>
        );
      }
    },
    { key: "title", header: "标题", cell: (row: NewsItem) => row.title }
  ], [newsRoles, setNewsRole, tagOptions.data, updateTags]);

  return (
    <section>
      <PageHeader title="新闻标注" />

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

        {/* 互动重标：不认可当前标注/推理时，打一句纠正让模型重标（多轮对话） */}
        {activeWindow && (autoResult || Object.keys(newsRoles).length > 0) ? (
          <div className="annotation-refine-row" style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 8 }}>
            <input
              type="text"
              value={refineMessage}
              onChange={(e) => setRefineMessage(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && refineMessage.trim() && !refine.isPending) refine.mutate(); }}
              placeholder="不认可就纠正模型，例：driver 标错了，应该是「美军打击」那条及其同簇冗余"
              style={{ flex: 1, fontSize: 15, padding: "4px 8px" }}
            />
            <Button disabled={!refineMessage.trim() || refine.isPending} onClick={() => refine.mutate()}>
              {refine.isPending ? "重标中..." : "让模型重标"}
            </Button>
          </div>
        ) : null}
        {refine.error ? <ErrorState error={refine.error} /> : null}

        {autoAnnotate.error ? <ErrorState error={autoAnnotate.error} /> : null}
      </section>

      {/* Section 2: 当前窗口——选中窗口的全部证据集中在此：
          净值图 + 段档位轨道 → 对照×S 证据表 → rolling S 曲线组。列表行只留一行摘要。 */}
      {activeWindow ? (
        <section className="panel annotation-block workbench-block">
          <div className="panel-head">
            <h2>当前窗口</h2>
            <div className="workbench-head-meta">
              <span className="workbench-time">
                {activeWindow.window_start.timestamp_bj?.slice(5, 16)} → {activeWindow.window_end.timestamp_bj?.slice(11, 16)}
              </span>
              <span className={`workbench-pct ${activeWindow.change_pct >= 0 ? "up-text" : "down-text"}`}>
                {activeWindow.change_pct > 0 ? "+" : ""}{activeWindow.change_pct.toFixed(2)}%
              </span>
              {activeWindow.tier_idx != null ? (
                <span className={`tier-chip t${activeWindow.tier_idx}`}>{["0.3档", "0.5档", "0.8档"][activeWindow.tier_idx]}</span>
              ) : null}
              {activeWindow.human_class ? (
                <span className={`klass ${classMeta(activeWindow.human_class).cls}`} title="人工已审">✓{classMeta(activeWindow.human_class).label}</span>
              ) : null}
            </div>
          </div>
          <WindowNetValueChart
            activeWindow={activeWindow}
            preMinutes={activePre}
            postMinutes={60}
            candidateNews={contextNews.data?.items ?? []}
            newsRoles={newsRoles}
          />
          <WindowEvidence win={activeWindow} />
          {activeWindow.symbol === "BTC/USDT" ? (
            <div className="subsection">
              <div className="subsection-head">
                <span className="subsection-title">品种相关性时序图</span>
              </div>
              <LinkagePanel
                symbol="BTC/USDT"
                hours={48}
                windowUtc={activeWindow.window_start.timestamp_utc && activeWindow.window_end.timestamp_utc ? {
                  startUtc: activeWindow.window_start.timestamp_utc,
                  endUtc: activeWindow.window_end.timestamp_utc,
                } : null}
                highlight={activeWindow.window_start.timestamp_bj && activeWindow.window_end.timestamp_bj ? {
                  x1: activeWindow.window_start.timestamp_bj.slice(5, 16),
                  x2: activeWindow.window_end.timestamp_bj.slice(5, 16),
                } : null}
              />
            </div>
          ) : null}
        </section>
      ) : null}

      {/* Section 3: 未标注 —— 左右对称（待标注列表 + 候选新闻），下方表单 */}
      <section className="panel annotation-block">
        <div className="panel-head">
          <h2>未标注 ({groups.length})</h2>
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
                      const locked = primary.annotatable === false;   // Phase3b：未 settle/走完，不可标
                      const tone = primary.change_pct >= 0 ? "up" : "down";
                      const sign = primary.change_pct > 0 ? "+" : "";
                      return (
                        <li key={key}>
                          <button
                            type="button"
                            className={`window-item ${tone}${isActive ? " active" : ""}${locked ? " locked" : ""}`}
                            onClick={() => { if (!locked) setActiveKey(key); }}
                            disabled={locked}
                            title={locked ? "窗口尚未 settle / 走完，稍后再标" : undefined}
                            style={locked ? { opacity: 0.5 } : undefined}
                          >
                            <span className="window-item-icon"><Circle size={14} /></span>
                            <span className="window-item-time">
                              {primary.window_start.timestamp_bj?.slice(5, 16)} → {primary.window_end.timestamp_bj?.slice(11, 16)}
                            </span>
                            {/* 2026-07-20：价格区间起终点直接上行内，替代 hover 浮窗 */}
                            <span className="window-item-price">
                              {fmtRefPrice(primary.price_start)} → {fmtRefPrice(primary.price_end)}
                            </span>
                            <span className="window-item-meta">
                              <span className="window-item-pct">
                                {sign}{primary.change_pct.toFixed(2)}%
                              </span>
                              {primary.tier_idx != null ? (
                                <span className={`tier-chip t${primary.tier_idx}`}>{["0.3", "0.5", "0.8"][primary.tier_idx]}</span>
                              ) : null}
                              {(() => {
                                const vals = Object.values(primary.s_scores ?? {}).map((v) => Math.abs((v as { s: number }).s));
                                return vals.length ? <span className="schip max">S {Math.max(...vals).toFixed(2)}</span> : null;
                              })()}
                              {primary.human_class ? (
                                <span className={`klass ${classMeta(primary.human_class).cls}`} title="人工已审">✓{classMeta(primary.human_class).label}</span>
                              ) : null}
                              {locked ? <span className="window-item-lock" title="尚未 settle">⏳</span> : null}
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
                    {!activeWindow ? "选中窗口后载入" : `${contextNews.data?.items.length ?? 0} 条`}
                  </span>
                </header>
                <div className="annotation-pair-panel-body">
                  {!activeWindow ? <EmptyState title="选择左侧窗口查看候选新闻" /> :
                   contextNews.isLoading ? <LoadingState /> :
                   contextNews.error ? <ErrorState error={contextNews.error} /> : (
                    <DataTable<NewsItem>
                      rows={contextNews.data?.items ?? []}
                      empty="该窗口前后没有候选新闻"
                      columns={newsColumns}
                    />
                  )}
                </div>
              </section>
            </div>

            {activeWindow ? (
              <div className="annotation-save-block">
                <div className="field">
                  <span>窗口驱动类型</span>
                  <div className="confidence-tiers">
                    {WINDOW_CLASS_OPTIONS.map((opt) => (
                      <button
                        key={opt.value}
                        type="button"
                        className={`tier-btn ${windowClass === opt.value ? "active" : ""}`}
                        onClick={() => {
                          setWindowClass(opt.value);
                          setSaveValidation("");
                          // 人工改判写回草稿：否则任何缓存更新（如打字触发 write-back）都会
                          // 让 hydrate 用 AI 的 window_class 盖掉人工选择
                          updateDraft({ window_class: opt.value });
                        }}
                      >
                        {opt.label}
                      </button>
                    ))}
                  </div>
                </div>
                <div className="field">
                  <span>归因置信度</span>
                  <div className="confidence-tiers">
                    {CONFIDENCE_TIERS.map((tier) => (
                      <button
                        key={tier.label}
                        type="button"
                        className={`tier-btn ${confidence === tier.value ? "active" : ""}`}
                        onClick={() => {
                          const next = confidence === tier.value ? null : tier.value;
                          setConfidence(next);
                          setSaveValidation("");
                          updateDraft({ confidence: next });
                        }}
                      >
                        {tier.label}
                      </button>
                    ))}
                    {confidence != null && !CONFIDENCE_TIERS.some((t) => t.value === confidence) ? (
                      <span className="muted-text small">AI: {confidence.toFixed(2)}</span>
                    ) : null}
                  </div>
                </div>
                <label className="field full">
                  <span>备注 / 因果归因</span>
                  <textarea
                    value={notes}
                    onChange={(event) => {
                      setNotes(event.target.value);
                      updateDraft({ summary: event.target.value });
                    }}
                    placeholder="自动标注后会自动填入 summary，可手动修改"
                  />
                </label>
                <div className="annotation-save-row">
                  <Button
                    disabled={saveDisabled}
                    onClick={() => {
                      if (windowClass == null) {
                        setSaveValidation("请先选择窗口驱动类型（新闻驱动 / 纯宏观共振 / 情绪·技术面），再保存。");
                        return;
                      }
                      if (confidence == null) {
                        setSaveValidation("请先选择归因置信度（高 / 中 / 低），再保存标注。");
                        return;
                      }
                      setSaveValidation("");
                      save.mutate();
                    }}
                  >
                    <Save size={16} />保存标注
                  </Button>
                  {saveValidation ? <div className="task-banner failed">{saveValidation}</div> : null}
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
          <h2>已标注 ({annTotal})</h2>
        </div>

        {annotatedListQuery.isLoading ? <LoadingState /> :
         annotatedListQuery.error ? <ErrorState error={annotatedListQuery.error} /> : (
          <DataTable<AnnotationListItem>
            rows={annotatedListQuery.data?.items ?? []}
            empty="还没有标注"
            columns={[
              {
                key: "window",
                header: "时间窗口",
                cell: (row) => (
                  <span>
                    {row.window_start.timestamp_bj?.slice(5, 16)} → {row.window_end.timestamp_bj?.slice(11, 16)}
                    {row.needs_review ? (
                      <span style={{ color: "#d97706", fontWeight: 600, marginLeft: 6 }}
                            title="窗口边界已被数据回补改动，请重看">需复核</span>
                    ) : null}
                  </span>
                )
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
                key: "macro",
                header: "宏观对标",
                cell: (row) => row.references?.length ? (
                  <span className="macro-cell">
                    {row.references.map((ref) => {
                      const f = fmtRef(ref);
                      // 匹配到当前窗口的行随行带 S（与工作台同数）；老标注无 S 就不占位
                      const s = !ref.is_self
                        ? ((row.s_scores ?? {})[ref.symbol] as unknown as { s: number; ess: number } | undefined)
                        : undefined;
                      const badge = s ? sBadge(s) : null;
                      return (
                        <span key={ref.symbol} className={f.cls}>
                          {f.text}
                          {badge ? <span className={`${badge.cls} macro-s`} title={badge.title}>{badge.text}</span> : null}
                        </span>
                      );
                    })}
                  </span>
                ) : "—"
              },
              {
                key: "selected",
                header: "归因",
                cell: (row) => {
                  const briefs = row.news_briefs ?? [];
                  // 2026-07-20：列表只展示 driver；同簇冗余仍全量落库，这里只报条数
                  const drivers = briefs.filter((b) => b.role === "driver");
                  const redundant = briefs.length - drivers.length;
                  const footer = [
                    redundant > 0 ? `+${redundant} 条同簇冗余` : null,
                    row.confidence != null ? `置信 ${row.confidence.toFixed(2)}` : null,
                  ].filter(Boolean).join(" · ");
                  return (
                    <div className="ann-briefs">
                      {drivers.map((b) => (
                        <div key={b.id} className="ann-brief" title={b.title}>
                          <span className="ann-brief-tag driver">驱</span>
                          {b.time_bj ? <span className="ann-brief-time">{b.time_bj}</span> : null}
                          <span className="ann-brief-title">{b.title}</span>
                        </div>
                      ))}
                      {!drivers.length && row.no_clear_news ? <span className="muted-text">无明确诱因</span> : null}
                      {footer ? <span className="muted-text small">{footer}</span> : null}
                    </div>
                  );
                }
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
                  <span className="annotation-row-actions">
                    <button
                      type="button"
                      className="link-button"
                      onClick={() => evalToggle.mutate({ id: row.id, value: !row.eval_set })}
                      disabled={evalToggle.isPending}
                      title="评估集样本不进训练导出，作为提示词/模型迭代的打分基准"
                    >
                      {row.eval_set ? "★ 评估集" : "☆ 设为评估"}
                    </button>
                    <button
                      type="button"
                      className="link-button danger"
                      onClick={() => undo.mutate(row.id)}
                      disabled={undo.isPending && undo.variables === row.id}
                    >
                      <RotateCcw size={14} />
                      撤销
                    </button>
                  </span>
                )
              }
            ]}
          />
        )}
        {annPages > 1 ? (
          <div className="pager">
            <Button kind="ghost" disabled={annPage <= 1 || annotatedListQuery.isFetching}
              onClick={() => setAnnPage((v) => Math.max(1, v - 1))}>
              <ChevronLeft size={16} />上一页
            </Button>
            <span>{annPage} / {annPages}</span>
            <Button kind="ghost" disabled={annPage >= annPages || annotatedListQuery.isFetching}
              onClick={() => setAnnPage((v) => v + 1)}>
              下一页<ChevronRight size={16} />
            </Button>
          </div>
        ) : null}
        {undo.error ? <ErrorState error={undo.error} /> : null}
      </section>

    </section>
  );
}
