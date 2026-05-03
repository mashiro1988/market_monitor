from __future__ import annotations

from pydantic import BaseModel

from schemas.common import TimeFields


class NewsItemSchema(TimeFields):
    id: int
    source: str
    source_id: str | None = None
    title: str
    content: str | None = None
    url: str | None = None
    source_importance: int | None = None
    llm_importance: int | None = None
    llm_importance_reason: str | None = None
    llm_model: str | None = None
    language: str
    categories: str | None = None
    is_jin10_important: bool = False


class NewsResponse(BaseModel):
    items: list[NewsItemSchema]
    total: int
    page: int
    page_size: int
    zh_count: int
    en_count: int
