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
    annotation_id: int | None = None  # 已标注则为对应 NewsPriceAnnotation.id


class AnnotationCreateRequest(BaseModel):
    symbol: str
    window_start_utc: str
    window_end_utc: str
    threshold_pct: float
    selected_news_ids: list[int] = Field(default_factory=list)
    no_clear_news: bool = False
    notes: str | None = None
    labeler: str | None = None


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
    no_clear_news: bool = False
    notes: str | None = None
    labeler: str | None = None
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


class DeleteAnnotationResponse(BaseModel):
    id: int
    deleted: bool = True
