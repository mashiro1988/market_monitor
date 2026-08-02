# -*- coding: utf-8 -*-
"""研究事件池·生命周期与读取(docs/specs/news-research-phase1-event-pool.md §6-§10)。

全部写操作只走人工入口(API),模型无立案/关闭/重开/合并权。
人工挂接无视闸门与黑名单(spec §1)。留痕规则见 spec §6.2 表。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

import config
from models.news import NewsItem, NewsPriceAnnotation
from models.price import PriceSnapshot
from models.research import ResearchEvent, ResearchEventLink
from services.event_linking import (
    _active_events, _is_blacklisted, _keyword_pool, _split_keywords,
    clear_link_cursor, passes_gate,
)
from services.time_utils import bj_date_of, bj_day_bounds, utc_now_naive


def _get_event(session: Session, event_id: int) -> ResearchEvent:
    e = session.query(ResearchEvent).filter_by(id=event_id).first()
    if e is None:
        raise ValueError(f"事件 #{event_id} 不存在")
    return e


def create_event(session: Session, name: str, news_ids: list[int],
                 gate_keywords: str | None = None, created_from: str = "manual",
                 backscan_hours: float | None = None,
                 now: datetime | None = None) -> ResearchEvent:
    """立案(spec §6.1):仅人工,强制 ≥1 条新闻;出生即 active;自动回扫 72h。"""
    if not news_ids:
        raise ValueError("立案必须至少挂一条新闻")
    if created_from not in ("annotation", "manual"):
        raise ValueError(f"非法 created_from: {created_from!r}")
    found = {int(i) for (i,) in session.query(NewsItem.id)
             .filter(NewsItem.id.in_(news_ids)).all()}
    missing = {int(i) for i in news_ids} - found
    if missing:
        raise ValueError(f"新闻不存在: {sorted(missing)}")
    now = now or utc_now_naive()
    event = ResearchEvent(name=name, status="active",
                          gate_keywords=(gate_keywords or None),
                          created_from=created_from, status_changed_at=now)
    session.add(event)
    session.flush()
    for nid in news_ids:
        session.add(ResearchEventLink(event_id=event.id, news_id=int(nid),
                                      link_source="human"))
    session.commit()
    clear_link_cursor(session, backscan_hours or config.EVENT_BACKSCAN_DEFAULT_HOURS, now=now)
    return event


def rename_event(session: Session, event_id: int, name: str) -> ResearchEvent:
    e = _get_event(session, event_id)
    e.name = name
    session.commit()
    return e


def set_keywords(session: Session, event_id: int, gate_keywords: str | None,
                 backscan: bool = False) -> ResearchEvent:
    """改关键词只对之后的新闻自动生效;backscan=True 追溯最近 72h(spec §5.1)。"""
    e = _get_event(session, event_id)
    e.gate_keywords = gate_keywords or None
    session.commit()
    if backscan:
        clear_link_cursor(session, config.EVENT_BACKSCAN_DEFAULT_HOURS)
    return e


def close_event(session: Session, event_id: int, reason: str | None) -> ResearchEvent:
    e = _get_event(session, event_id)
    e.status = "closed"
    e.closed_reason = reason
    e.status_changed_at = utc_now_naive()
    session.commit()
    return e


def reopen_event(session: Session, event_id: int) -> ResearchEvent:
    """重开(spec §6.2/§7):closed→active,免闸恢复,自动回扫 72h。时间轴同一条。"""
    e = _get_event(session, event_id)
    e.status = "active"
    e.status_changed_at = utc_now_naive()
    session.commit()
    clear_link_cursor(session, config.EVENT_BACKSCAN_DEFAULT_HOURS)
    return e


def merge_event(session: Session, source_id: int, target_id: int) -> int:
    """合并 A→B(spec §6.2):未摘下挂接迁移(撞唯一索引跳过保 B 现有),关键词并入去重,
    A 关闭并记 merged_into;A 的已摘下记录留在 A(审计痕迹不迁移)。返回迁移条数。"""
    if source_id == target_id:
        raise ValueError("不能合并到自身")
    src = _get_event(session, source_id)
    dst = _get_event(session, target_id)
    dst_news = {row[0] for row in session.query(ResearchEventLink.news_id)
                .filter_by(event_id=target_id).all()}
    moved = 0
    for link in (session.query(ResearchEventLink)
                 .filter_by(event_id=source_id, detached=False).all()):
        if link.news_id in dst_news:
            continue
        link.event_id = target_id
        moved += 1
    merged_kw = _split_keywords(dst.gate_keywords)
    for k in _split_keywords(src.gate_keywords):
        if k not in merged_kw:
            merged_kw.append(k)
    dst.gate_keywords = "、".join(merged_kw) or None
    src.status = "closed"
    src.merged_into_id = target_id
    src.closed_reason = f"合并入 #{target_id}"
    src.status_changed_at = utc_now_naive()
    session.commit()
    return moved


def attach_news(session: Session, event_id: int, news_id: int) -> ResearchEventLink:
    """人工挂接:无视闸门/黑名单;同 (event,news) 已有记录则复活(detached→False)。"""
    _get_event(session, event_id)
    existing = (session.query(ResearchEventLink)
                .filter_by(event_id=event_id, news_id=news_id).first())
    if existing:
        existing.detached = False
        existing.detach_reason = None
        existing.link_source = "human"
        session.commit()
        return existing
    link = ResearchEventLink(event_id=event_id, news_id=news_id, link_source="human")
    session.add(link)
    session.commit()
    return link


def reassign_link(session: Session, link_id: int, new_event_id: int) -> ResearchEventLink:
    """改归属:auto_event_id 保模型原判,link_source 变 human(spec §6.2)。"""
    link = session.query(ResearchEventLink).filter_by(id=link_id).first()
    if link is None:
        raise ValueError(f"挂接 #{link_id} 不存在")
    _get_event(session, new_event_id)
    dup = (session.query(ResearchEventLink)
           .filter_by(event_id=new_event_id, news_id=link.news_id).first())
    if dup is not None and dup.id != link.id:
        raise ValueError(f"目标事件已有这条新闻的挂接(#{dup.id})")
    link.event_id = new_event_id
    link.link_source = "human"
    session.commit()
    return link


def detach_link(session: Session, link_id: int, reason: str | None) -> ResearchEventLink:
    """摘下=标记不删行(留痕,spec §6.2)。"""
    link = session.query(ResearchEventLink).filter_by(id=link_id).first()
    if link is None:
        raise ValueError(f"挂接 #{link_id} 不存在")
    link.detached = True
    link.detach_reason = reason
    link.link_source = "human"
    session.commit()
    return link
