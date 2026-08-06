# -*- coding: utf-8 -*-
"""补评分扫描:把入库时评分失败/走了不打分侧门的新闻补上 llm_importance。

选取口径(docs/specs/2026-08-06-news-rescore-and-source-cut-design.md §1.1):
llm_importance 为空 + created_at 在窗口内 + 尝试次数未达上限,created_at 倒序
(先补最新——当轮漏网的赶在挂接闸门前拿到分;老积压在空闲轮消化)。
所有被选中行无论成败 rescore_attempts +1,毒条目达上限自动退休。
不看黑名单、不分来源:评分是全量属性,已下线源的存量行照补。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

import config
from models.news import NewsItem
from scanners.base import NewsRecord
from services.time_utils import utc_now_naive


def rescore_unscored(session: Session, limit: int | None = None,
                     window_hours: int | None = None, max_attempts: int | None = None,
                     now: datetime | None = None, scorer=None) -> dict:
    """补扫一批无分新闻,返回 {selected, scored}。scorer 可注入(测试/脚本复用)。"""
    limit = config.NEWS_RESCORE_LIMIT if limit is None else limit
    window_hours = config.NEWS_RESCORE_WINDOW_HOURS if window_hours is None else window_hours
    max_attempts = config.NEWS_RESCORE_MAX_ATTEMPTS if max_attempts is None else max_attempts
    now = now or utc_now_naive()

    if scorer is None:
        from scanners.scorer import NewsScorer
        scorer = NewsScorer()
    if not getattr(scorer, "enabled", False):
        return {"selected": 0, "scored": 0}

    cutoff = now - timedelta(hours=window_hours)
    rows = (session.query(NewsItem)
            .filter(NewsItem.llm_importance.is_(None),
                    NewsItem.created_at >= cutoff,
                    func.coalesce(NewsItem.rescore_attempts, 0) < max_attempts)
            .order_by(NewsItem.created_at.desc())
            .limit(limit).all())
    if not rows:
        return {"selected": 0, "scored": 0}

    records = [NewsRecord(source=r.source, source_id=r.source_id or "",
                          title=r.title or "", content=r.content,
                          importance=r.importance, language=r.language or "zh",
                          published_at=r.timestamp)
               for r in rows]
    scorer.enrich_batch(records)

    scored = 0
    for row, rec in zip(rows, records):
        row.rescore_attempts = (row.rescore_attempts or 0) + 1
        if rec.llm_importance is not None:
            row.llm_importance = rec.llm_importance
            row.llm_importance_reason = rec.llm_importance_reason
            row.llm_model = rec.llm_model
            row.llm_scored_at = rec.llm_scored_at
            scored += 1
    session.commit()
    return {"selected": len(rows), "scored": scored}
