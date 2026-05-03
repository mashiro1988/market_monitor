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
