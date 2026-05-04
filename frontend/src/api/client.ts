import type {
  AlertLog,
  AlertRule,
  AlertTestResponse,
  AlertWebhookStatus,
  AnnotationCreateRequest,
  AnnotationDetail,
  AnnotationResponse,
  AnnotationSymbol,
  ApiErrorPayload,
  AutoAnnotateRequest,
  AutoAnnotateResponse,
  DeleteAnnotationResponse,
  MarketHistoryResponse,
  MarketLatestResponse,
  MarketSymbol,
  MarketTableRow,
  NewsResponse,
  Page,
  PredictionFamily,
  PredictionRow,
  PredictionsResponse,
  PriceRule,
  PriceWindow,
  TaskStatus
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
  predictions: (params: { hours?: number; search?: string }) =>
    request<PredictionsResponse>(`/predictions${buildQuery(params)}`),
  predictionFamilies: (params: { hours?: number; search?: string }) =>
    request<PredictionFamily[]>(`/predictions/families${buildQuery(params)}`),
  predictionHistory: (marketId: string, hours: number) =>
    request<PredictionRow[]>(`/predictions/${encodeURIComponent(marketId)}/history${buildQuery({ hours })}`),
  alertRules: () => request<AlertRule[]>("/alerts/rules"),
  alertLogs: (params: { hours_back?: number; page?: number; page_size?: number }) =>
    request<Page<AlertLog>>(`/alerts/logs${buildQuery(params)}`),
  webhookStatus: () => request<AlertWebhookStatus>("/alerts/webhook-status"),
  testWechat: () => request<AlertTestResponse>("/alerts/test-wechat", { method: "POST" }),
  priceRules: () => request<PriceRule[]>("/annotations/price-rules"),
  annotationSymbols: (hours = 72) => request<AnnotationSymbol[]>(`/annotations/symbols${buildQuery({ hours })}`),
  annotationWindows: (params: { symbol: string; hours?: number; threshold_pct?: number; window_minutes?: number }) =>
    request<PriceWindow[]>(`/annotations/windows${buildQuery(params)}`),
  contextNews: (params: { window_start_utc: string; window_end_utc: string; pre_minutes?: number; post_minutes?: number }) =>
    request<{ items: import("./types").NewsItem[] }>(`/annotations/context-news${buildQuery(params)}`),
  saveAnnotation: (body: AnnotationCreateRequest) =>
    request<AnnotationResponse>("/annotations", { method: "POST", body: JSON.stringify(body) }),
  annotationDetail: (id: number) =>
    request<AnnotationDetail>(`/annotations/${id}`),
  deleteAnnotation: (id: number) =>
    request<DeleteAnnotationResponse>(`/annotations/${id}`, { method: "DELETE" }),
  autoAnnotate: (body: AutoAnnotateRequest) =>
    request<AutoAnnotateResponse>("/annotations/auto", { method: "POST", body: JSON.stringify(body) })
};
