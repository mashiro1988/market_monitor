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
  }) => request<NewsResponse>(`/news${buildQuery(params)}`),
  newsSources: () => request<NewsSourceMeta[]>("/news/sources"),
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
  behaviorLinkage: (params: { symbol?: string; hours?: number }) =>
    request<BehaviorLinkageResponse>(`/behavior/linkage${buildQuery(params)}`),
  priceRules: () => request<PriceRule[]>("/annotations/price-rules"),
  annotationSymbols: (hours = 72) => request<AnnotationSymbol[]>(`/annotations/symbols${buildQuery({ hours })}`),
  annotationWindows: (params: { symbol: string; hours?: number; threshold_pct?: number; window_minutes?: number }) =>
    request<PriceWindow[]>(`/annotations/windows${buildQuery(params)}`),
  contextNews: (params: { window_start_utc: string; window_end_utc: string; pre_minutes?: number; post_minutes?: number }) =>
    request<{ items: import("./types").NewsItem[] }>(`/annotations/context-news${buildQuery(params)}`),
  saveAnnotation: (body: AnnotationCreateRequest) =>
    request<AnnotationResponse>("/annotations", { method: "POST", body: JSON.stringify(body) }),
  annotationsList: (params: { symbol?: string; hours?: number }) =>
    request<AnnotationListItem[]>(`/annotations${buildQuery(params)}`),
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
  tagOptions: () =>
    request<{ topics: string[]; magnitudes: string[]; directions: string[] }>("/annotations/tag-options"),
  updateNewsTags: (id: number, body: { topic?: string | null; magnitude_tier?: string | null; news_direction?: string | null }) =>
    request<import("./types").NewsItem>(`/news/${id}/tags`, { method: "PATCH", body: JSON.stringify(body) }),
  // 板块轮动
  sectorLeaderboard: () => request<SectorLeaderboardResponse>("/sectors/leaderboard"),
  sectorTokens: (category: string) =>
    request<SectorTokensResponse>(`/sectors/${encodeURIComponent(category)}/tokens`)
};
