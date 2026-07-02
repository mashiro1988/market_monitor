from __future__ import annotations

from pydantic import BaseModel, Field

from schemas.common import TimeFields
from schemas.news import NewsItemSchema

# —— 标注角色（news-impact-engine Phase 3a；取代 annotation-v2.md 四分类）——
# 三值，人/LLM 逐条直接标（noise 为默认值，news_roles 里只存非 noise）。
# post_hoc_explanation / contradictory 退场、并入 noise（综述/解释/离题/矛盾一律默认 noise）。
NEWS_CAUSAL_ROLES = (
    "driver",     # 驱动：触发/推动本窗口异动的主事件（同事件簇里最主要的一条）
    "redundant",  # 同簇冗余：与 driver 同一事件簇的其它相关报道；训练时排除、不当负样本
    "noise",      # 噪音（默认，不落库）
)
# 历史兼容的窗口级市场反应类型。Phase3a 后前端/prompt 不再主动产出；
# 旧数据和旧请求仍按三分类解析。
MARKET_REACTION_TYPES = (
    "macro_policy",          # 宏观数据与政策预期（数据公布/央行/官员/财政）
    "event_driven",          # 其他明确事件驱动（地缘/制裁/监管/行业/标的专属）
    "no_news_driver",        # 无新闻驱动（情绪/仓位/技术/无法归因；确定性由 confidence 表达）
)


class PriceRuleSchema(BaseModel):
    symbol: str
    threshold_pct: float
    window_minutes: int


class AnnotationSymbol(BaseModel):
    symbol: str
    name: str
    asset_class: str


class ReferenceChange(BaseModel):
    """标注窗口的「宏观同期对标」单项（纳指/原油/黄金/美债2Y/美元指数/BTC…）。"""
    symbol: str
    label: str
    pre_pct: float | None = None
    pct: float | None = None   # 同期变动：unit=pct 时为涨跌%，unit=bp 时为基点；None=休市/无数据
    post_pct: float | None = None
    correlation: float | None = None  # 与标注品种在窗口 ±1h 的 5min 收益率 Pearson 相关；None=样本不足/无波动
    unit: str = "pct"          # "pct" | "bp"（收益率类品种用 bp）
    is_self: bool = False      # 标注品种本身（不对标自己）


class PriceWindowSchema(BaseModel):
    symbol: str
    asset_class: str
    name: str
    window_start: TimeFields
    window_end: TimeFields
    configured_window_minutes: int
    actual_window_minutes: float
    price_start: float
    price_end: float
    change_pct: float
    segment_count: int = 1
    annotation_id: int | None = None  # 已标注则为对应 NewsPriceAnnotation.id
    annotatable: bool = True            # Phase3b A策略①：已 settle+走完（window_end ≤ now−余量）才可标
    is_primary: bool = True            # 合并事件窗口恒 True（不再发 secondary）
    context_pre_minutes: int = 30      # 候选新闻前置窗（按档位：15m 档 30 / 60m 档 60）
    references: list[ReferenceChange] = Field(default_factory=list)  # 宏观同期对标（纳指/原油/黄金…）


class AnnotationCreateRequest(BaseModel):
    symbol: str
    window_start_utc: str
    window_end_utc: str
    threshold_pct: float
    selected_news_ids: list[int] = Field(default_factory=list)
    no_clear_news: bool = False
    notes: str | None = None
    labeler: str | None = None
    # 训练用：标注当时这个 context 窗口里的全部候选新闻 ID（含未选中的，作负样本）。
    candidate_news_ids: list[int] | None = None
    # 自动标注流程：LLM 原始推理 + 摘要（与人审后的 notes 分开存）；纯人工标注则两者都为 None。
    auto_reasoning: str | None = None
    auto_summary: str | None = None
    # —— v2 标签（None = 旧格式请求，落库时由 selected/no_clear 归一化派生）——
    news_roles: dict[int, str] | None = None          # {news_id: causal_role}，只含非 noise
    market_reaction_type: str | None = None           # legacy: MARKET_REACTION_TYPES 之一
    confidence: float | None = None                   # 0-1
    # AI 原始标注快照（人改前），用于沉淀人机分歧难例；纯人工标注为 None
    auto_news_roles: dict[int, str] | None = None
    # 候选新闻前置窗分钟数（多尺度窗口各档不同；不传用默认）
    context_pre_minutes: int | None = None


class AnnotationResponse(BaseModel):
    id: int
    saved: bool = True


class ContextNewsResponse(BaseModel):
    items: list[NewsItemSchema]


class AnnotationDetail(BaseModel):
    """已标注窗口的完整信息，给前端 view 模式 / 撤销使用。"""
    id: int
    symbol: str
    asset_class: str | None
    window_start: TimeFields
    window_end: TimeFields
    context_start: TimeFields
    context_end: TimeFields
    threshold_pct: float | None
    price_start: float | None
    price_end: float | None
    change_pct: float | None
    selected_news_ids: list[int] = Field(default_factory=list)
    selected_news: list[NewsItemSchema] = Field(default_factory=list)
    candidate_news_ids: list[int] = Field(default_factory=list)
    no_clear_news: bool = False
    notes: str | None = None
    labeler: str | None = None
    auto_reasoning: str | None = None
    auto_summary: str | None = None
    news_roles: dict[int, str] = Field(default_factory=dict)
    market_reaction_type: str | None = None
    confidence: float | None = None
    auto_news_roles: dict[int, str] = Field(default_factory=dict)
    prompt_version: str | None = None
    eval_set: bool = False
    created_at: TimeFields
    updated_at: TimeFields


class AutoAnnotateRequest(BaseModel):
    """前端请求自动标注：传窗口（symbol + start + end + threshold），后端拉候选新闻并跑 reasoner。"""
    symbol: str
    window_start_utc: str
    window_end_utc: str
    threshold_pct: float
    context_pre_minutes: int | None = None   # 多尺度窗口各档候选前置分钟；不传用默认


class AutoAnnotateRefineRequest(BaseModel):
    """互动重标（annotation-refinements Part C）：把上一轮输出 + 用户纠正当作多轮对话再调 reasoner。"""
    symbol: str
    window_start_utc: str
    window_end_utc: str
    threshold_pct: float
    context_pre_minutes: int | None = None
    prior_news_roles: dict[int, str] | None = None   # 上一轮的 driver/redundant
    prior_summary: str | None = None
    prior_confidence: float | None = None
    user_message: str                                # 用户的纠正意见（自然语言）


class AutoAnnotateResponse(BaseModel):
    """自动标注返回：Phase3a 标签 + 推理过程 + 摘要。**不写库**，由前端 review 后调 POST /api/annotations 落库。
    selected_news_ids / no_clear_news 为派生兼容字段（全部 driver / 无 driver）。"""
    selected_news_ids: list[int] = Field(default_factory=list)
    no_clear_news: bool = False
    news_roles: dict[int, str] = Field(default_factory=dict)
    market_reaction_type: str | None = None
    confidence: float | None = None
    summary: str = ""
    reasoning: str = ""  # DeepSeek 的 message.reasoning_content
    model: str
    duration_seconds: float
    candidate_count: int  # 模型看了多少条候选新闻


class AutoAnnotateBatchRequest(BaseModel):
    """一次喂多个窗口给 reasoner，复用同一份 system prompt + 一次 thinking pass，提高 KV cache 命中率。"""
    windows: list[AutoAnnotateRequest] = Field(default_factory=list)


class AutoAnnotateBatchItem(BaseModel):
    """批量自动标注里单个窗口的结果。"""
    symbol: str
    window_start_utc: str
    window_end_utc: str
    selected_news_ids: list[int] = Field(default_factory=list)
    no_clear_news: bool = False
    news_roles: dict[int, str] = Field(default_factory=dict)
    market_reaction_type: str | None = None
    confidence: float | None = None
    summary: str = ""
    reasoning: str = ""  # 模型对该窗口的逐条解释（来自结构化 JSON，不是 message.reasoning_content）
    candidate_count: int = 0
    candidate_news_ids: list[int] = Field(default_factory=list)


class AutoAnnotateBatchResponse(BaseModel):
    """一次批量调用返回 N 个窗口的结果 + 一份全局 reasoning_content（DeepSeek thinking 思考过程，可选展示）。"""
    results: list[AutoAnnotateBatchItem] = Field(default_factory=list)
    reasoning: str = ""  # DeepSeek message.reasoning_content，整批 N 个窗口共享，仅作 debug
    model: str
    duration_seconds: float
    requested_count: int  # 请求传入的窗口数
    answered_count: int   # 模型实际给出 item 的数量（理论上应该一致）


class DeleteAnnotationResponse(BaseModel):
    id: int
    deleted: bool = True


class AnnotationListItem(BaseModel):
    """已标注列表的轻量行，不包含完整 selected_news（用 GET /api/annotations/{id} 拉详情）。"""
    id: int
    symbol: str
    asset_class: str | None
    window_start: TimeFields
    window_end: TimeFields
    change_pct: float | None
    references: list[ReferenceChange] = Field(default_factory=list)
    no_clear_news: bool
    selected_count: int
    market_reaction_type: str | None = None
    confidence: float | None = None
    eval_set: bool = False
    needs_review: bool = False         # Phase3b A策略③：窗口边界被 backfill 改动、当前重算窗口对不上
    labeler: str | None
    notes: str | None
    created_at: TimeFields
    updated_at: TimeFields
