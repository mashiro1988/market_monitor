export type TimeFields = {
  timestamp_utc: string | null;
  timestamp_bj: string | null;
};

export type Page<T> = {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
};

export type ApiErrorPayload = {
  code: string;
  message: string;
  details: Record<string, unknown>;
};

export type MarketLatestItem = TimeFields & {
  name: string;
  symbol: string;
  asset_class: string;
  source: string;
  price: number;
  prev_price: number | null;
  change_pct: number | null;
  change_5m: number | null;
  change_1h: number | null;
  change_24h: number | null;
};

export type MarketLatestResponse = {
  items: MarketLatestItem[];
  last_updated: TimeFields | null;
};

export type MarketHistoryPoint = TimeFields & {
  symbol: string;
  name: string;
  price: number;
  normalized_pct: number | null;
};

export type MarketHistorySeries = {
  symbol: string;
  name: string;
  asset_class: string | null;
  points: MarketHistoryPoint[];
};

export type MarketHistoryResponse = {
  symbols: string[];
  start: TimeFields;
  end: TimeFields;
  series: MarketHistorySeries[];
};

export type MarketTableRow = TimeFields & {
  asset_class: string;
  name: string;
  symbol: string;
  price: number;
  prev_price: number | null;
  change_pct: number | null;
  volume: number | null;
  source: string;
};

export type MarketSymbol = {
  symbol: string;
  name: string;
  asset_class: string;
};

export type NewsItem = TimeFields & {
  id: number;
  source: string;
  source_id: string | null;
  title: string;
  content: string | null;
  url: string | null;
  source_importance: number | null;
  llm_importance: number | null;
  llm_importance_reason: string | null;
  llm_model: string | null;
  language: string;
  categories: string | null;
  is_jin10_important: boolean;
};

export type NewsResponse = {
  items: NewsItem[];
  total: number;
  page: number;
  page_size: number;
  zh_count: number;
  en_count: number;
};

export type NewsSourceMeta = {
  key: string;
  name: string;
  language: string;
};

export type PredictionRow = TimeFields & {
  market_id: string;
  question: string;
  outcome: string;
  probability: number;
  prev_probability: number | null;
  probability_pct: number;
  delta_pct: number | null;
  volume: number | null;
};

export type PredictionMarketSummary = {
  market_id: string;
  question: string;
  volume: number | null;
  outcomes: PredictionRow[];
  has_shift: boolean;
};

export type PredictionFamilySeries = {
  market_id: string;
  question: string;
  label: string;
  order: number;
  points: PredictionRow[];
};

export type PredictionFamily = {
  id: string;
  name: string;
  series: PredictionFamilySeries[];
};

export type PredictionsResponse = {
  markets: PredictionMarketSummary[];
  latest_timestamp: TimeFields | null;
};

export type AlertRule = {
  name: string;
  rule_type: string;
  params: Record<string, unknown>;
  channels: string[];
  cooldown_minutes: number;
  enabled: boolean;
};

export type AlertWebhookStatus = {
  configured: boolean;
  preview: string | null;
};

export type AlertTestResponse = {
  ok: boolean;
  message: string;
};

export type AlertLog = TimeFields & {
  id: number;
  rule_name: string;
  message: string;
  channel: string;
  delivered: boolean;
};

export type TaskStatus = {
  task_id: string;
  status: "queued" | "running" | "succeeded" | "skipped" | "failed";
  created_at: TimeFields;
  started_at: TimeFields | null;
  finished_at: TimeFields | null;
  message: string | null;
  result: Record<string, unknown> | null;
  error: string | null;
};

export type PriceRule = {
  symbol: string;
  threshold_pct: number;
  window_minutes: number;
};

export type AnnotationSymbol = {
  symbol: string;
  name: string;
  asset_class: string;
};

export type PriceWindow = {
  symbol: string;
  asset_class: string;
  name: string;
  window_start: TimeFields;
  window_end: TimeFields;
  configured_window_minutes: number;
  actual_window_minutes: number;
  price_start: number;
  price_end: number;
  change_pct: number;
  annotation_id: number | null;
  is_primary: boolean;  // 连续异动 run 的第一个；false 时为 continuation，不可标注
};

export type AnnotationDetail = {
  id: number;
  symbol: string;
  asset_class: string | null;
  window_start: TimeFields;
  window_end: TimeFields;
  context_start: TimeFields;
  context_end: TimeFields;
  threshold_pct: number | null;
  price_start: number | null;
  price_end: number | null;
  change_pct: number | null;
  selected_news_ids: number[];
  selected_news: NewsItem[];
  candidate_news_ids: number[];
  no_clear_news: boolean;
  notes: string | null;
  labeler: string | null;
  auto_reasoning: string | null;
  auto_summary: string | null;
  created_at: TimeFields;
  updated_at: TimeFields;
};

export type AutoAnnotateRequest = {
  symbol: string;
  window_start_utc: string;
  window_end_utc: string;
  threshold_pct: number;
};

export type AutoAnnotateResponse = {
  selected_news_ids: number[];
  no_clear_news: boolean;
  summary: string;
  reasoning: string;
  model: string;
  duration_seconds: number;
  candidate_count: number;
};

export type AutoAnnotateBatchRequest = {
  windows: AutoAnnotateRequest[];
};

export type AutoAnnotateBatchItem = {
  symbol: string;
  window_start_utc: string;
  window_end_utc: string;
  selected_news_ids: number[];
  no_clear_news: boolean;
  summary: string;
  candidate_count: number;
  candidate_news_ids: number[];
};

export type AutoAnnotateBatchResponse = {
  results: AutoAnnotateBatchItem[];
  reasoning: string;
  model: string;
  duration_seconds: number;
  requested_count: number;
  answered_count: number;
};

export type DeleteAnnotationResponse = {
  id: number;
  deleted: boolean;
};

export type AnnotationListItem = {
  id: number;
  symbol: string;
  asset_class: string | null;
  window_start: TimeFields;
  window_end: TimeFields;
  change_pct: number | null;
  no_clear_news: boolean;
  selected_count: number;
  labeler: string | null;
  notes: string | null;
  created_at: TimeFields;
  updated_at: TimeFields;
};

export type AnnotationCreateRequest = {
  symbol: string;
  window_start_utc: string;
  window_end_utc: string;
  threshold_pct: number;
  selected_news_ids: number[];
  no_clear_news: boolean;
  notes?: string | null;
  labeler?: string | null;
  // 训练数据增强字段：
  candidate_news_ids?: number[] | null;  // 标注时这个 context 窗口里的全部候选新闻 ID（含未选中作负样本）
  auto_reasoning?: string | null;        // DeepSeek auto-annotate 的 reasoning_content 全文（纯人工则 null）
  auto_summary?: string | null;          // DeepSeek auto-annotate 的 summary 原文（与人改后的 notes 区分）
};

export type AnnotationResponse = {
  id: number;
  saved: boolean;
};
