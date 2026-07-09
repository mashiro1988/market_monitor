# -*- coding: utf-8 -*-
"""价格行为引擎 API schema（price-behavior-engine-plan Task 6）。"""
from __future__ import annotations

from pydantic import BaseModel, Field

from schemas.common import TimeFields


class SScoreSchema(BaseModel):
    s: float                      # 共振分（符号仅展示：美元指数反向为常态；判级用 |s|）
    ess: float                    # 有效样本数：<BEHAVIOR_ESS_THIN 标"证据薄"
    coverage: float               # 参照覆盖（BTC 权重质量占比）


class BehaviorNewsBrief(BaseModel):
    id: int
    time: TimeFields
    title: str
    magnitude_tier: str | None = None
    topic: str | None = None


class BehaviorSegmentSchema(BaseModel):
    id: int
    symbol: str
    start: TimeFields
    end: TimeFields
    key_ts: TimeFields | None = None      # 段内 |5min| 最大 bar（新闻对时锚点）
    direction: int                        # +1 / -1
    tier_idx: int                         # 0/1/2
    tier_max: float                       # 触及最高档阈值（%）
    net_pct: float
    amp_pct: float | None = None
    classification: str | None = None     # count_only / macro_news / pure_resonance / industry_news
                                          # / sentiment / no_ref_news / no_ref_pending / null=未settle
    class_version: str | None = None
    s_scores: dict[str, SScoreSchema] = Field(default_factory=dict)
    max_abs_s: float | None = None        # 判级依据（最强参照）
    news: list[BehaviorNewsBrief] = Field(default_factory=list)


class BehaviorSegmentsResponse(BaseModel):
    symbol: str
    days: int
    segments: list[BehaviorSegmentSchema]


class BehaviorDailySchema(BaseModel):
    utc_date: str                         # UTC 日界 = 北京 8 点
    day_type: str                         # weekday / weekend（分桶互比）
    counts: dict[str, dict[str, int]]     # {tier: {up, down}}
    composition: dict[str, int]           # 六类构成（0.5 档以上）
    down_net_sum: float | None = None
    computed_at: TimeFields
    live: bool = False                    # True = 无 PIT 行、按需现算（当日盘中）


class BehaviorDailyResponse(BaseModel):
    symbol: str
    days: list[BehaviorDailySchema]


class LinkagePoint(BaseModel):
    t: TimeFields
    s: float | None = None                # None = 无对照（参照休市/覆盖不足）→ 曲线断线


class LinkageSeries(BaseModel):
    symbol: str
    label: str
    points: list[LinkagePoint]


class BreadthPoint(BaseModel):
    t: TimeFields
    count: int | None = None              # |S| ≥ BEHAVIOR_S_MID 的参照数（0~6）；None=BTC 无数据


class BehaviorLinkageResponse(BaseModel):
    symbol: str
    hours: int
    rolling_points: int                   # 滚动窗口点数（config BEHAVIOR_ROLLING_POINTS）
    series: list[LinkageSeries]
    breadth: list[BreadthPoint]
