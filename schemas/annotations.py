from __future__ import annotations

from pydantic import BaseModel, Field

from schemas.common import TimeFields
from schemas.news import NewsItemSchema


class PriceRuleSchema(BaseModel):
    symbol: str
    threshold_pct: float
    window_minutes: int


class AnnotationSymbol(BaseModel):
    symbol: str
    name: str
    asset_class: str


class ReferenceChange(BaseModel):
    """标注窗口的「宏观同期对标」单项（纳指/原油/黄金…）。"""
    symbol: str
    label: str
    pct: float | None = None   # 同期涨跌%；None=休市/无数据
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
    peak_change_pct: float = 0.0
    low_price: float = 0.0
    high_price: float = 0.0
    segment_count: int = 1
    annotation_id: int | None = None  # 已标注则为对应 NewsPriceAnnotation.id
    is_primary: bool = True            # 合并事件窗口恒 True（不再发 secondary）
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
    created_at: TimeFields
    updated_at: TimeFields


class AutoAnnotateRequest(BaseModel):
    """前端请求自动标注：传窗口（symbol + start + end + threshold），后端拉候选新闻并跑 reasoner。"""
    symbol: str
    window_start_utc: str
    window_end_utc: str
    threshold_pct: float


class AutoAnnotateResponse(BaseModel):
    """自动标注返回：建议的 selected_news_ids + 推理过程 + 摘要。**不写库**，由前端 review 后调 POST /api/annotations 落库。"""
    selected_news_ids: list[int] = Field(default_factory=list)
    no_clear_news: bool = False
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
    labeler: str | None
    notes: str | None
    created_at: TimeFields
    updated_at: TimeFields
