# -*- coding: utf-8 -*-
"""研究事件池模型(news-research-phase1 spec §3):事件 + 时间轴挂接。
铁律:事件=名字+状态+时间轴;时间轴展示的时间/方向标/观测值/徽章全部读时派生,
挂接表不存任何业务数值。gate_keywords 是路由配置(免闸),不是语义字段。"""
from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String, Text, Index, UniqueConstraint
from datetime import datetime
from database import Base


class ResearchEvent(Base):
    """事件主表:两态(active/closed),仅人工立案(spec §6.1)。"""
    __tablename__ = "research_events"

    id = Column(Integer, primary_key=True, index=True)
    event_type = Column(String(16), nullable=False, default="macro")   # macro / crypto(web3 二期A)
    # 人看的序号:每种类型各从 1 排(加密事件池 #1 起),只增不补。
    # 程序内部(挂接、时间轴、合并、路由)一律仍用主键 id——display_no 只负责展示。
    display_no = Column(Integer, nullable=True)
    name = Column(String(80), nullable=False)
    status = Column(String(10), nullable=False, default="active")      # active / closed
    gate_keywords = Column(Text, nullable=True)       # 顿号分隔;空=不免闸;已关闭事件的词走沉睡监听
    merged_into_id = Column(Integer, nullable=True)
    closed_reason = Column(Text, nullable=True)
    created_from = Column(String(12), nullable=False, default="manual")  # annotation / manual
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    status_changed_at = Column(DateTime, nullable=True)


class ResearchEventLink(Base):
    """时间轴挂接:只增不删;摘下=标记(留痕);模型原判 auto_event_id 人改后保留。"""
    __tablename__ = "research_event_links"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, nullable=False, index=True)
    news_id = Column(Integer, nullable=False)
    link_source = Column(String(8), nullable=False)    # auto=模型挂且未经人动 / human=人挂或人改过
    auto_event_id = Column(Integer, nullable=True)     # 模型原判事件;纯人工挂接 NULL
    confidence = Column(Float, nullable=True)          # 三档 0.9/0.65/0.3;仅 auto
    prompt_version = Column(String(40), nullable=True)
    detached = Column(Boolean, nullable=False, default=False)
    detach_reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("event_id", "news_id", name="uq_research_link_event_news"),
        Index("ix_research_link_news", "news_id"),
    )
