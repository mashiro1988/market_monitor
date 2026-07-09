# -*- coding: utf-8 -*-
"""价格行为引擎模型（docs/specs/price-behavior-engine-plan.md Task 3）。

- BehaviorSegment：异动段（归因与计数的唯一单位），S 证据/新闻命中/分类落库——段是原始数据，
  分类规则改版可全历史重跑（classification 可重写，class_version 记口径）。
- BehaviorDailySummary：日汇总 **point-in-time 追加表**——同一 utc_date 每次重算都新增一行，
  读取取 computed_at 最新一条；历史读数永久可回溯（回测校准的前提）。
"""
from datetime import datetime

from sqlalchemy import Column, DateTime, Float, Index, Integer, String, Text

from database import Base


class BehaviorSegment(Base):
    __tablename__ = "behavior_segments"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(30), nullable=False)
    start_dt = Column(DateTime, nullable=False)          # 段起（UTC naive，含首触发 15min 回看）
    end_dt = Column(DateTime, nullable=False)            # 段止（UTC naive）
    direction = Column(Integer, nullable=False)          # +1 / -1
    tier_idx = Column(Integer, nullable=False)           # 0/1/2（跨资产可比档位序）
    tier_max = Column(Float, nullable=False)             # 触及最高档阈值（该资产口径，%）
    net_pct = Column(Float, nullable=False)              # 段首基准 → 段尾净幅（%）
    amp_pct = Column(Float, nullable=True)               # 段内振幅（%）
    key_ts = Column(DateTime, nullable=True)             # |5min| 最大 bar（新闻对时锚点）
    # 分类（compute-then-store，可按 class_version 重跑）：
    # count_only(0.3 档) / macro_news / pure_resonance / industry_news / sentiment
    # / no_ref_news(无对照×新闻命中) / no_ref_pending(无对照×无新闻) / NULL=未分类(未 settle)
    classification = Column(String(30), nullable=True)
    class_version = Column(String(20), nullable=True)
    # 人工审计（2026-07-09 补）：human_class 非空 = 人已确认/改判，构成聚合优先取它；
    # 机器重跑（class_version 换版）只更新 classification，不碰人工结论。
    human_class = Column(String(30), nullable=True)
    human_confirmed_at = Column(DateTime, nullable=True)
    s_scores = Column(Text, nullable=True)               # JSON {ref_symbol: {s, ess, coverage}}
    news_ids = Column(Text, nullable=True)               # JSON [news_id, ...]（±30min 大/中命中）
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_behavior_seg_span", "symbol", "start_dt", "end_dt", unique=True),
        Index("ix_behavior_seg_symbol_end", "symbol", "end_dt"),
    )


class BehaviorDailySummary(Base):
    __tablename__ = "behavior_daily_summaries"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(30), nullable=False)
    utc_date = Column(String(10), nullable=False)        # "YYYY-MM-DD"（UTC 日界 = 北京 8 点）
    day_type = Column(String(10), nullable=False)        # weekday / weekend（分桶互比）
    counts = Column(Text, nullable=False)                # JSON {tier_pct: {up, down}}（0.3 档=计数层全量）
    composition = Column(Text, nullable=False)           # JSON {macro_news/pure_resonance/industry_news/sentiment/no_ref_news/no_ref_pending}
    down_net_sum = Column(Float, nullable=True)          # 跌段净幅合计（%）
    computed_at = Column(DateTime, nullable=False)       # PIT 戳：追加不覆盖，读取取最新

    __table_args__ = (
        Index("ix_behavior_daily_pit", "symbol", "utc_date", "computed_at", unique=True),
    )
