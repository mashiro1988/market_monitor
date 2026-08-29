# -*- coding: utf-8 -*-
"""事件↔预测市场挂接(spec 2026-08-28 §1):tracked_markets(slug)粒度,
留痕模式与 research_event_links 完全对齐——摘下=标记不删行,auto 记置信度与提示词版本。
同一跟踪项可挂多个事件;跟踪项被软删后卡片不展示,挂接行留审计。"""
from datetime import datetime
from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String, Text, Index, UniqueConstraint
from database import Base


class ResearchEventMarket(Base):
    __tablename__ = "research_event_markets"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, nullable=False, index=True)
    tracked_id = Column(Integer, nullable=False)
    link_source = Column(String(8), nullable=False)    # auto=AI 提案人工确认 / human=手动挂
    confidence = Column(Float, nullable=True)          # 三档 0.9/0.65/0.3;仅 auto
    prompt_version = Column(String(40), nullable=True)
    detached = Column(Boolean, nullable=False, default=False)
    detach_reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("event_id", "tracked_id", name="uq_research_event_market"),
        Index("ix_research_event_market_tracked", "tracked_id"),
    )
