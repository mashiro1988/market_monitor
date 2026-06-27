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
  topic: string | null;            // Phase 1 内容标签：主题
  magnitude_tier: string | null;   // a-priori 量级 大/中/小
  news_direction: string | null;   // 应然方向 利多/利空/中性
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

export type TrackedMarket = {
  id: number;
  kind: "slug" | "tag";
  identifier: string;
  display_name: string | null;
  enabled: boolean;
  notes: string | null;
};

export type TrackedMarketCreatePayload = {
  kind: "slug" | "tag";
  identifier: string;
  display_name?: string | null;
  notes?: string | null;
};

export type TrackedMarketUpdatePayload = {
  enabled?: boolean;
  display_name?: string | null;
  notes?: string | null;
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

export type ReferenceChange = {
  symbol: string;
  label: string;
  pct: number | null;        // unit=pct 时为涨跌%，unit=bp 时为基点
  unit?: "pct" | "bp";       // 收益率类品种（美债10Y）用 bp
  is_self: boolean;
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
  segment_count: number;
  annotation_id: number | null;
  annotatable?: boolean;  // Phase3b：已 settle+走完才可标；尾部/暂定窗口 false（置灰）
  is_primary: boolean;  // 合并事件窗口恒 True
  context_pre_minutes?: number;  // 候选前置窗（15m 档 30 / 60m 档 60）
  references?: ReferenceChange[];  // 宏观同期对标（纳指/原油/黄金…）
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
  context_pre_minutes?: number;   // 候选前置窗（按窗口档位）
};

export type AutoAnnotateResponse = {
  selected_news_ids: number[];      // 派生兼容字段（primary+secondary）
  no_clear_news: boolean;           // 派生兼容字段（无 primary）
  news_roles: Record<number, string>;        // v2：{news_id: causal_role}，只含非 noise
  market_reaction_type: string | null;       // v2：八分类
  confidence: number | null;                 // v2：0-1
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
  selected_news_ids: number[];      // 派生兼容字段
  no_clear_news: boolean;           // 派生兼容字段
  news_roles: Record<number, string>;
  market_reaction_type: string | null;
  confidence: number | null;
  summary: string;
  reasoning: string;  // 该窗口专属 reasoning（来自结构化 JSON），与 batch.reasoning（DeepSeek thinking）不同
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
  references?: ReferenceChange[];
  no_clear_news: boolean;
  selected_count: number;
  market_reaction_type?: string | null;
  confidence?: number | null;
  eval_set?: boolean;
  needs_review?: boolean;  // Phase3b：窗口边界被数据回补改动，请重看
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
  // v2 标签（selected_news_ids / no_clear_news 由后端从 news_roles 派生，前端不再传）
  news_roles?: Record<number, string>;
  market_reaction_type?: string | null;
  confidence?: number | null;
  selected_news_ids?: number[];
  no_clear_news?: boolean;
  notes?: string | null;
  labeler?: string | null;
  // 训练数据增强字段：
  candidate_news_ids?: number[] | null;  // 标注时这个 context 窗口里的全部候选新闻 ID（含未标作负样本）
  auto_reasoning?: string | null;        // DeepSeek auto-annotate 的 reasoning_content 全文（纯人工则 null）
  auto_summary?: string | null;          // DeepSeek auto-annotate 的 summary 原文（与人改后的 notes 区分）
  auto_news_roles?: Record<number, string> | null;  // AI 原始角色（人改前快照，人机分歧=难例信号）
  context_pre_minutes?: number | null;   // 候选前置窗分钟（多尺度窗口各档不同）
};

export type AnnotationResponse = {
  id: number;
  saved: boolean;
};

// ============================================================
// 板块轮动（Phase 1 of remote_data_integration）
// ============================================================
export type SectorLeaderboardRow = {
  category: string;
  group: string | null;
  token_count: number;
  ret_1h: number | null;
  ret_24h: number | null;
  ret_168h: number | null;
  ret_720h: number | null;
};

export type SectorLeaderboardResponse = {
  snapshot_at: TimeFields | null;
  rows: SectorLeaderboardRow[];
};

export type SectorTokenRow = {
  symbol: string;
  binance_symbol: string;
  market: "spot" | "swap";
  ret_1h: number | null;
  ret_24h: number | null;
  ret_168h: number | null;
  ret_720h: number | null;
};

export type SectorTokensResponse = {
  category: string;
  group: string | null;
  snapshot_at: TimeFields | null;
  tokens: SectorTokenRow[];
};
