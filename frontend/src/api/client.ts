import type {
  AlertLog,
  AlertRule,
  BehaviorDailyResponse,
  BehaviorLinkageResponse,
  BehaviorSegmentsResponse,
  AlertTestResponse,
  AlertWebhookStatus,
  AnnotationCreateRequest,
  AnnotationDetail,
  AnnotationListItem,
  AnnotationResponse,
  AnnotationSymbol,
  ApiErrorPayload,
  AutoAnnotateBatchRequest,
  AutoAnnotateBatchResponse,
  AutoAnnotateRequest,
  AutoAnnotateResponse,
  DeleteAnnotationResponse,
  MarketHistoryResponse,
  MarketLatestResponse,
  MarketSymbol,
  MarketTableRow,
  NewsResponse,
  NewsSourceMeta,
  Page,
  SectorLeaderboardResponse,
  SectorTokensResponse,
  PredictionFamily,
  PredictionRow,
  PredictionsResponse,
  PriceRule,
  PriceWindow,
  TaskStatus,
  TrackedMarket,
  TrackedMarketCreatePayload,
  TrackedMarketUpdatePayload
} from "./types";

export class ApiError extends Error {
  payload: ApiErrorPayload;

  constructor(payload: ApiErrorPayload) {
    super(payload.message);
    this.name = "ApiError";
    this.payload = payload;
  }
}

/** 动作失败的用户可见文案:优先后端 message,空/非 ApiError 时退回调用方兜底词。 */
export function apiErrorText(err: unknown, fallback: string): string {
  return err instanceof ApiError && err.payload.message ? err.payload.message : fallback;
}

const API_BASE = "/api";
const AUTH_STORAGE_KEY = "marketMonitor.authToken";

function authHeaders(): Record<string, string> {
  if (typeof window === "undefined") return {};
  const token = window.localStorage.getItem(AUTH_STORAGE_KEY)?.trim();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function buildQuery(params: Record<string, string | number | boolean | null | undefined | string[]> = {}) {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === "") return;
    if (Array.isArray(value)) {
      value.forEach((item) => search.append(key, item));
    } else {
      search.set(key, String(value));
    }
  });
  const text = search.toString();
  return text ? `?${text}` : "";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(),
      ...(init?.headers ?? {})
    },
    ...init
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => ({
      code: "HTTP_ERROR",
      message: response.statusText,
      details: {}
    }))) as ApiErrorPayload;
    throw new ApiError(payload);
  }
  return response.json() as Promise<T>;
}

export const api = {
  health: () => request<{ ok: boolean }>("/health"),
  status: () => request<Record<string, unknown>>("/status"),
  scan: () => request<TaskStatus>("/tasks/scan", { method: "POST" }),
  task: (taskId: string) => request<TaskStatus>(`/tasks/${taskId}`),
  marketLatest: () => request<MarketLatestResponse>("/market/latest"),
  marketSymbols: () => request<MarketSymbol[]>("/market/symbols"),
  marketHistory: (params: { symbols?: string[]; hours?: number; start_utc?: string; end_utc?: string }) =>
    request<MarketHistoryResponse>(`/market/history${buildQuery(params)}`),
  marketTable: (params: { hours?: number; asset_classes?: string[]; symbols?: string[]; page?: number; page_size?: number }) =>
    request<Page<MarketTableRow>>(`/market/table${buildQuery(params)}`),
  news: (params: {
    sources?: string[];
    min_llm_importance?: number;
    hours_back?: number;
    jin10_importance?: string;
    search?: string;
    page?: number;
    page_size?: number;
    buffer_only?: boolean;          // 仅看未挂事件(= 事件池缓冲区口径)
  }) => request<NewsResponse>(`/news${buildQuery(params)}`),
  newsSources: () => request<NewsSourceMeta[]>("/news/sources"),
  // 加密快讯（web3 二期A）：独立页面，与宏观新闻互不干扰
  cryptoNews: (params: {
    sources?: string[];
    hours_back?: number;
    min_llm_importance?: number;
    affair_only?: boolean;          // 只看币圈事务（滤掉加密源转载的纯宏观）
    coin?: string;
    search?: string;
    page?: number;
    page_size?: number;
  }) => request<NewsResponse>(`/crypto/news${buildQuery(params)}`),
  cryptoNewsSources: () => request<NewsSourceMeta[]>("/crypto/news/sources"),
  predictions: (params: { hours?: number; search?: string }) =>
    request<PredictionsResponse>(`/predictions${buildQuery(params)}`),
  predictionFamilies: (params: { hours?: number; search?: string }) =>
    request<PredictionFamily[]>(`/predictions/families${buildQuery(params)}`),
  predictionHistory: (marketId: string, hours: number) =>
    request<PredictionRow[]>(`/predictions/${encodeURIComponent(marketId)}/history${buildQuery({ hours })}`),
  predictionTracked: () => request<TrackedMarket[]>("/predictions/tracked"),
  createPredictionTracked: (payload: TrackedMarketCreatePayload) =>
    request<TrackedMarket>("/predictions/tracked", {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  updatePredictionTracked: (id: number, payload: TrackedMarketUpdatePayload) =>
    request<TrackedMarket>(`/predictions/tracked/${id}`, {
      method: "PATCH",
      body: JSON.stringify(payload)
    }),
  deletePredictionTracked: (id: number) =>
    request<{ ok: boolean }>(`/predictions/tracked/${id}`, { method: "DELETE" }),
  alertRules: () => request<AlertRule[]>("/alerts/rules"),
  alertLogs: (params: { hours_back?: number; page?: number; page_size?: number }) =>
    request<Page<AlertLog>>(`/alerts/logs${buildQuery(params)}`),
  webhookStatus: () => request<AlertWebhookStatus>("/alerts/webhook-status"),
  testWechat: () => request<AlertTestResponse>("/alerts/test-wechat", { method: "POST" }),
  behaviorSegments: (params: { symbol?: string; days?: number }) =>
    request<BehaviorSegmentsResponse>(`/behavior/segments${buildQuery(params)}`),
  behaviorDaily: (params: { symbol?: string; days?: number }) =>
    request<BehaviorDailyResponse>(`/behavior/daily${buildQuery(params)}`),
  behaviorLinkage: (params: { symbol?: string; hours?: number; start_utc?: string; end_utc?: string }) =>
    request<BehaviorLinkageResponse>(`/behavior/linkage${buildQuery(params)}`),
  behaviorReview: (segmentId: number, humanClass: string | null) =>
    request<{ id: number; human_class: string | null }>(`/behavior/segments/${segmentId}`, {
      method: "PATCH",
      body: JSON.stringify({ human_class: humanClass })
    }),
  priceRules: () => request<PriceRule[]>("/annotations/price-rules"),
  annotationSymbols: (hours = 72) => request<AnnotationSymbol[]>(`/annotations/symbols${buildQuery({ hours })}`),
  annotationWindows: (params: { symbol: string; hours?: number; threshold_pct?: number; window_minutes?: number }) =>
    request<PriceWindow[]>(`/annotations/windows${buildQuery(params)}`),
  contextNews: (params: { window_start_utc: string; window_end_utc: string; pre_minutes?: number; post_minutes?: number }) =>
    request<{ items: import("./types").NewsItem[] }>(`/annotations/context-news${buildQuery(params)}`),
  saveAnnotation: (body: AnnotationCreateRequest) =>
    request<AnnotationResponse>("/annotations", { method: "POST", body: JSON.stringify(body) }),
  annotationsList: (params: { symbol?: string; hours?: number; page?: number; page_size?: number }) =>
    request<Page<AnnotationListItem>>(`/annotations${buildQuery(params)}`),
  annotationDetail: (id: number) =>
    request<AnnotationDetail>(`/annotations/${id}`),
  deleteAnnotation: (id: number) =>
    request<DeleteAnnotationResponse>(`/annotations/${id}`, { method: "DELETE" }),
  setAnnotationEvalSet: (id: number, value: boolean) =>
    request<AnnotationResponse>(`/annotations/${id}/eval-set?value=${value}`, { method: "POST" }),
  autoAnnotate: (body: AutoAnnotateRequest) =>
    request<AutoAnnotateResponse>("/annotations/auto", { method: "POST", body: JSON.stringify(body) }),
  autoAnnotateBatch: (body: AutoAnnotateBatchRequest) =>
    request<AutoAnnotateBatchResponse>("/annotations/auto-batch", { method: "POST", body: JSON.stringify(body) }),
  autoAnnotateRefine: (body: import("./types").AutoAnnotateRefineRequest) =>
    request<AutoAnnotateResponse>("/annotations/auto/refine", { method: "POST", body: JSON.stringify(body) }),
  // 内容标签：库 + 人工改
  // 板块轮动
  sectorLeaderboard: () => request<SectorLeaderboardResponse>("/sectors/leaderboard"),
  sectorTokens: (category: string) =>
    request<SectorTokensResponse>(`/sectors/${encodeURIComponent(category)}/tokens`),
  // 研究事件池(news-research-phase1 spec §9.3)
  researchEvents: (params: { status?: string; q?: string; event_type?: string } = {}) =>
    request<import("./types").ResearchEventsResponse>(`/research/events${buildQuery(params)}`),
  researchEventCreate: (body: import("./types").ResearchEventCreateRequest) =>
    request<import("./types").ResearchEventItem>("/research/events", { method: "POST", body: JSON.stringify(body) }),
  researchEventPatch: (id: number, body: import("./types").ResearchEventPatchRequest) =>
    request<import("./types").ResearchEventItem>(`/research/events/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  // 软删除(2026-08-13):事件从界面彻底消失,证据摘下退回缓冲区(留痕,纠错率照审)
  researchEventDelete: (id: number) =>
    request<import("./types").DeleteEventResponse>(`/research/events/${id}`, { method: "DELETE" }),
  researchSuggestKeywords: (body: import("./types").SuggestKeywordsRequest) =>
    request<import("./types").SuggestKeywordsResponse>("/research/events/suggest-keywords", { method: "POST", body: JSON.stringify(body) }),
  researchBackscan: (id: number, days: number) =>
    request<import("./types").BackscanResponse>(`/research/events/${id}/backscan`, { method: "POST", body: JSON.stringify({ days }) }),
  researchTimeline: (id: number, params: { days?: number; min_score?: number; min_abs_move?: number; page?: number; page_size?: number } = {}) =>
    request<import("./types").TimelineResponse>(`/research/events/${id}/timeline${buildQuery(params)}`),
  researchLinkCreate: (body: import("./types").LinkCreateRequest) =>
    request<import("./types").LinkResponse>("/research/links", { method: "POST", body: JSON.stringify(body) }),
  researchLinkPatch: (id: number, body: import("./types").LinkPatchRequest) =>
    request<import("./types").LinkResponse>(`/research/links/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  researchNewsLinks: (newsId: number) =>
    request<import("./types").NewsLinksResponse>(`/research/news/${newsId}/links`),
  researchBuffer: (params: { days?: number; min_score?: number; q?: string; drivers_only?: boolean } = {}) =>
    request<import("./types").BufferResponse>(`/research/buffer${buildQuery(params)}`),
  researchRevival: (eventType?: string) =>
    request<import("./types").RevivalResponse>(`/research/revival${buildQuery({ event_type: eventType })}`),
  researchStats: (eventType?: "macro" | "crypto") =>
    request<import("./types").ResearchStats>(`/research/stats${buildQuery({ event_type: eventType })}`),
  // AI 梳理(2026-08-13,08-15 改提案制):思考模型长调用(1-5 分钟),只出提案+自动补挂
  researchSweep: (eventType: "macro" | "crypto") =>
    request<import("./types").SweepResponse>("/research/sweep", {
      method: "POST",
      body: JSON.stringify({ event_type: eventType })
    }),
  // 采纳勾选的提案(签字环节):只有这一步会真正立案
  researchSweepApply: (eventType: "macro" | "crypto", events: import("./types").SweepProposal[]) =>
    request<import("./types").SweepApplyResponse>("/research/sweep/apply", {
      method: "POST",
      body: JSON.stringify({ event_type: eventType, events })
    })
};
