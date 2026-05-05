from __future__ import annotations

from datetime import timedelta

from sqlalchemy import or_
from sqlalchemy.orm import Session

import config
from models.news import NewsItem
from schemas.news import NewsItemSchema, NewsResponse
from services.pagination import clamp_page
from services.time_utils import timestamp_pair, utc_now_naive


def _enabled_news_sources() -> list[str]:
    """白名单：从 `config.NEWS_SOURCES` 取启用的源 key，避免硬编码源名导致漂移。"""
    return [k for k, v in config.NEWS_SOURCES.items() if v.get("enabled")]


def is_jin10_important(item: NewsItem) -> bool:
    return item.source == "jin10" and (item.importance == 1 or (item.importance or 0) >= 8)


def passes_default_importance_filter(item: NewsItem, min_llm_score: int) -> bool:
    return (item.llm_importance or 0) >= min_llm_score or is_jin10_important(item)


def to_news_schema(item: NewsItem) -> NewsItemSchema:
    return NewsItemSchema(
        id=item.id,
        source=item.source,
        source_id=item.source_id,
        title=item.title,
        content=item.content,
        url=item.url,
        source_importance=item.importance,
        llm_importance=item.llm_importance,
        llm_importance_reason=item.llm_importance_reason,
        llm_model=item.llm_model,
        language=item.language,
        categories=item.categories,
        is_jin10_important=is_jin10_important(item),
        **timestamp_pair(item.timestamp),
    )


def get_news(
    session: Session,
    sources: list[str] | None = None,
    min_llm_importance: int = 5,
    hours_back: int = 24,
    jin10_importance: str = "all",
    search: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> NewsResponse:
    page, page_size = clamp_page(page, page_size)
    hours_back = max(1, min(int(hours_back or 24), 24 * 30))
    min_llm_importance = max(1, min(int(min_llm_importance or 1), 10))
    cutoff = utc_now_naive() - timedelta(hours=hours_back)

    query = session.query(NewsItem).filter(NewsItem.timestamp >= cutoff)
    if sources:
        query = query.filter(NewsItem.source.in_(sources))
    else:
        query = query.filter(NewsItem.source.in_(_enabled_news_sources()))

    if search:
        like = f"%{search.strip()}%"
        query = query.filter(or_(NewsItem.title.ilike(like), NewsItem.content.ilike(like)))

    candidates = query.order_by(NewsItem.timestamp.desc()).limit(5000).all()
    filtered = [item for item in candidates if passes_default_importance_filter(item, min_llm_importance)]

    if jin10_importance != "all":
        target = jin10_importance == "important"
        filtered = [item for item in filtered if item.source != "jin10" or is_jin10_important(item) == target]

    total = len(filtered)
    start = (page - 1) * page_size
    page_items = filtered[start:start + page_size]
    return NewsResponse(
        items=[to_news_schema(item) for item in page_items],
        total=total,
        page=page,
        page_size=page_size,
        zh_count=sum(1 for item in filtered if item.language == "zh"),
        en_count=sum(1 for item in filtered if item.language == "en"),
    )
