# -*- coding: utf-8 -*-
"""研究事件池·挂接调用(docs/specs/news-research-phase1-event-pool.md §4-§5)。

模型只有挂接权:把过闸新闻挂到某个进行中事件,或判不挂。闸门/黑名单/关键词免闸
全部在代码里判(可审计)。游标 news_items.event_linked_at 四种结果都盖章
(挂/不挂/不够格/黑名单),回扫=清游标。人工挂接无视本文件所有闸门(在 event_pool.py)。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

from loguru import logger
from sqlalchemy.orm import Session

import config
from models.news import NewsItem
from models.research import ResearchEvent, ResearchEventLink
from services.deepseek_client import call_deepseek_chat
from services.time_utils import utc_now_naive


def _is_blacklisted(news: NewsItem) -> bool:
    """固定栏目黑名单(spec §4.4):来源+标题正则都命中才算。"""
    for source, pattern in config.NEWS_EVENT_LINK_BLACKLIST:
        if news.source == source and re.search(pattern, news.title or ""):
            return True
    return False


def _split_keywords(raw: str | None) -> list[str]:
    """顿号分隔(容忍中英文逗号);空白剔除。"""
    if not raw:
        return []
    return [w.strip() for w in re.split(r"[、,，]", raw) if w.strip()]


def _active_events(session: Session) -> list[ResearchEvent]:
    return (session.query(ResearchEvent)
            .filter(ResearchEvent.status == "active")
            .order_by(ResearchEvent.id.asc()).all())


def _keyword_pool(events: list[ResearchEvent]) -> list[str]:
    """全部进行中事件关键词的并集(免闸用;已关闭事件的词走沉睡监听,不在此)。"""
    out: list[str] = []
    for e in events:
        out.extend(_split_keywords(e.gate_keywords))
    return out


def _news_text(news: NewsItem) -> str:
    return f"{news.title or ''} {(news.content or '')[:200]}".lower()


def passes_gate(news: NewsItem, keywords: list[str]) -> bool:
    """闸门(spec §4.1):≥6 或未评分 或命中任一进行中事件关键词。
    免闸≠指定归属——挂到哪仍由模型对整个活跃池判断。"""
    if news.llm_importance is None or news.llm_importance >= config.EVENT_LINK_MIN_IMPORTANCE:
        return True
    text = _news_text(news)
    return any(k.lower() in text for k in keywords)
