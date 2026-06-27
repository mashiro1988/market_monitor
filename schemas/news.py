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
    # —— Phase 1 内容标签（news_tagging 打，标注页展示用）——
    topic: str | None = None            # 主题（NEWS_TOPICS 之一）
    magnitude_tier: str | None = None   # a-priori 量级 大/中/小
    news_direction: str | None = None   # 对风险资产应然方向 利多/利空/中性


class NewsResponse(BaseModel):
    items: list[NewsItemSchema]
    total: int
    page: int
    page_size: int
    zh_count: int
    en_count: int


class NewsSourceMeta(BaseModel):
    """`/api/news/sources` 返回项；前端用来构造新闻源下拉框，避免硬编码 source key。"""
    key: str
    name: str
    language: str
