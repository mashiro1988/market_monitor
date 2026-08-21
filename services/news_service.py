from __future__ import annotations

from datetime import timedelta

from sqlalchemy import or_
from sqlalchemy.orm import Session

import config
from models.news import NewsItem
from schemas.news import NewsItemSchema, NewsResponse, NewsSourceMeta
from services.pagination import clamp_page
from services.time_utils import timestamp_pair, utc_now_naive


def _enabled_news_sources() -> list[str]:
    """白名单：从 `config.NEWS_SOURCES` 取启用的源 key，避免硬编码源名导致漂移。"""
    return [k for k, v in config.NEWS_SOURCES.items() if v.get("enabled")]


def list_sources() -> list[NewsSourceMeta]:
    """枚举当前启用的新闻源给前端构造下拉框用。`name` 优先取 config 里配置的，
    缺省时大写 key。"""
    items: list[NewsSourceMeta] = []
    for key, cfg in config.NEWS_SOURCES.items():
        if not cfg.get("enabled"):
            continue
        items.append(NewsSourceMeta(
            key=key,
            name=cfg.get("name") or key.upper(),
            language=cfg.get("language", "en"),
        ))
    return items


def is_jin10_important(item: NewsItem) -> bool:
    return item.source == "jin10" and (item.importance == 1 or (item.importance or 0) >= 8)


def passes_default_importance_filter(item: NewsItem, min_llm_score: int) -> bool:
    """分数门槛只看分数。

    2026-08-07 起去掉了 `或 金十重要` 这个旁路:它让"8 分以上"里混进 6 分的金十条目,
    口径不干净。金十重要是**独立维度**,由 `jin10_importance` 参数单独筛。
    """
    if min_llm_score <= 0:                       # 不限:未评分也放行(buffer-into-news design §0)
        return True
    return (item.llm_importance or 0) >= min_llm_score


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
        topic=item.topic,
        magnitude_tier=item.magnitude_tier,
        news_direction=item.news_direction,
        **timestamp_pair(item.timestamp),
    )


def list_crypto_sources() -> list[NewsSourceMeta]:
    return [NewsSourceMeta(key=key, name=cfg.get("name") or key.upper(),
                           language=cfg.get("language", "zh"))
            for key, cfg in getattr(config, "CRYPTO_NEWS_SOURCES", {}).items()
            if cfg.get("enabled")]


def get_crypto_news(
    session: Session,
    sources: list[str] | None = None,
    hours_back: int = 24,
    min_llm_importance: int = 0,
    affair_only: bool = False,
    coin: str | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 50,
    unlinked_only: bool = False,
) -> NewsResponse:
    """加密快讯(web3 二期A design §4):独立页面,与宏观新闻页互不干扰。

    min_llm_importance 默认 0 = 不按分数拦:加密线的小币新闻分数天然低,
    正是二期B 异动归因要研究的对象,拦掉等于把原料掐了。
    """
    from models.crypto import NewsCoin

    page, page_size = clamp_page(page, page_size)
    hours_back = max(1, min(int(hours_back or 24), 24 * 30))
    min_llm_importance = max(0, min(int(min_llm_importance or 0), 10))
    cutoff = utc_now_naive() - timedelta(hours=hours_back)

    # 默认视图不按"启用源"过滤:market=crypto 已圈死范围,再按启用源筛只会把
    # 停用源(如 blockbeats 断供)的历史快讯连带藏掉——停采≠灭史(2026-08-21)。
    query = (session.query(NewsItem)
             .filter(NewsItem.market == "crypto", NewsItem.timestamp >= cutoff))
    if sources:
        query = query.filter(NewsItem.source.in_(sources))
    if affair_only:
        query = query.filter(NewsItem.is_crypto_affair.is_(True))
    if coin:
        code = coin.strip().upper()
        query = query.filter(NewsItem.id.in_(
            session.query(NewsCoin.news_id).filter(NewsCoin.coin == code)))
    if search:
        like = f"%{search.strip()}%"
        query = query.filter(or_(NewsItem.title.ilike(like), NewsItem.content.ilike(like)))

    candidates = query.order_by(NewsItem.timestamp.desc()).limit(5000).all()
    filtered = [i for i in candidates
                if passes_default_importance_filter(i, min_llm_importance)]
    if unlinked_only:
        # 与宏观页「只看未挂事件」共用同一个判定函数,口径不会两处漂移
        from services.event_pool import buffer_predicate
        is_buffer = buffer_predicate(session, market="crypto")
        filtered = [i for i in filtered if is_buffer(i)]
    total = len(filtered)
    start = (page - 1) * page_size
    page_items = filtered[start:start + page_size]

    coin_map: dict[int, list[str]] = {}
    if page_items:
        rows = (session.query(NewsCoin.news_id, NewsCoin.coin)
                .filter(NewsCoin.news_id.in_([i.id for i in page_items])).all())
        for news_id, code in rows:
            coin_map.setdefault(news_id, []).append(code)

    items = []
    for item in page_items:
        schema = to_news_schema(item)
        schema.is_crypto_affair = item.is_crypto_affair
        schema.coins = sorted(coin_map.get(item.id, []))
        items.append(schema)

    return NewsResponse(
        items=items, total=total, page=page, page_size=page_size,
        zh_count=sum(1 for i in filtered if i.language == "zh"),
        en_count=sum(1 for i in filtered if i.language == "en"),
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
    buffer_only: bool = False,
) -> NewsResponse:
    page, page_size = clamp_page(page, page_size)
    hours_back = max(1, min(int(hours_back or 24), 24 * 30))
    # 0 = 不限(含未评分):未评分是评分调用失败,不是 0 分,设了门槛才该滤掉
    min_llm_importance = max(0, min(int(min_llm_importance or 0), 10))
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

    if buffer_only:
        # 缓冲区口径(过闸 + 非黑名单 + 无未摘下挂接)与事件池共用同一谓词,不在这里重写一遍
        from services.event_pool import buffer_predicate
        is_buffer = buffer_predicate(session)
        filtered = [item for item in filtered if is_buffer(item)]

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
