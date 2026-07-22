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
    human_class: str | None = None        # 人工确认/改判后的类别；null=未审。构成聚合优先取它
    human_confirmed_at: TimeFields | None = None
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
    # 方向拆分（2026-07-10 行为面板重画）：compute-on-read 不进 PIT——净幅由段原始数据决定，
    # 情绪归属按人工优先的当前结论（详见 behavior_classifier.day_direction_extras）
    up_net_sum: float | None = None
    sent_up: int | None = None            # 情绪·技术面涨段数（0.5 档以上）
    sent_down: int | None = None
    sent_up_net_sum: float | None = None
    sent_down_net_sum: float | None = None
    up_net_sum_strong: float | None = None    # 强段（tier_idx≥1）涨净幅Σ ≥0（净幅分层 2026-07-22）
    down_net_sum_strong: float | None = None  # 强段跌净幅Σ ≤0（负值约定同 down_net_sum；弱段=总−强 由前端求）
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


# 人工审核只标三类（Phase 2）；机器六类经 to_window_class 归并展示。
REVIEWABLE_CLASSES = ("news_driven", "pure_resonance", "sentiment_tech")


class BehaviorReviewRequest(BaseModel):
    """人工审计一个段：human_class=某类 → 确认/改判；null → 撤销人工结论（回到机器类）。"""
    human_class: str | None = None
