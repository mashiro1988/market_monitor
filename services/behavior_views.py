# -*- coding: utf-8 -*-
"""价格行为引擎 · API 读层（docs/specs/price-behavior-engine-plan.md Task 6）。

compute-on-read：段/日汇总从库读（日汇总当日无 PIT 行时按同口径现算，live=True）；
rolling S 曲线全程现算（纯展示层——不触发、不分类、不告警）。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

import config
from models.behavior import BehaviorDailySummary, BehaviorSegment
from models.news import NewsItem
from schemas.behavior import (
    BehaviorDailyResponse,
    BehaviorDailySchema,
    BehaviorLinkageResponse,
    BehaviorNewsBrief,
    BehaviorSegmentSchema,
    BehaviorSegmentsResponse,
    BreadthPoint,
    LinkagePoint,
    LinkageSeries,
    SScoreSchema,
)
from schemas.common import TimeFields
from services.behavior_classifier import _points, aggregate_day, day_direction_extras, day_type_of, merge_composition, to_window_class
from services.resonance_score import chg_map, rolling_s
from services.time_utils import timestamp_pair

_REF_LABELS = {t[0]: t[1] for t in config.ANNOTATION_REFERENCE_ASSETS}


def _tf(value: datetime | None) -> TimeFields:
    return TimeFields(**timestamp_pair(value))


def list_segments(session: Session, symbol: str, days: int = 2) -> BehaviorSegmentsResponse:
    cutoff = datetime.utcnow() - timedelta(days=days)
    rows = (
        session.query(BehaviorSegment)
        .filter(BehaviorSegment.symbol == symbol, BehaviorSegment.end_dt >= cutoff)
        .order_by(BehaviorSegment.start_dt.desc())
        .all()
    )
    news_ids: set[int] = set()
    for r in rows:
        if r.news_ids:
            news_ids.update(json.loads(r.news_ids))
    briefs: dict[int, BehaviorNewsBrief] = {}
    if news_ids:
        for n in session.query(NewsItem).filter(NewsItem.id.in_(news_ids)).all():
            briefs[n.id] = BehaviorNewsBrief(
                id=n.id, time=_tf(n.timestamp), title=n.title or "",
                magnitude_tier=n.magnitude_tier, topic=n.topic,
            )
    segments = []
    for r in rows:
        scores = {k: SScoreSchema(**v) for k, v in (json.loads(r.s_scores) if r.s_scores else {}).items()}
        ids = json.loads(r.news_ids) if r.news_ids else []
        segments.append(BehaviorSegmentSchema(
            id=r.id, symbol=r.symbol,
            start=_tf(r.start_dt), end=_tf(r.end_dt),
            key_ts=_tf(r.key_ts) if r.key_ts else None,
            direction=r.direction, tier_idx=r.tier_idx, tier_max=r.tier_max,
            net_pct=r.net_pct, amp_pct=r.amp_pct,
            classification=r.classification, class_version=r.class_version,
            human_class=r.human_class,
            human_confirmed_at=_tf(r.human_confirmed_at) if r.human_confirmed_at else None,
            s_scores=scores,
            max_abs_s=max((abs(v.s) for v in scores.values()), default=None),
            news=[briefs[i] for i in ids if i in briefs],
        ))
    return BehaviorSegmentsResponse(symbol=symbol, days=days, segments=segments)


def daily_series(session: Session, symbol: str, days: int = 14) -> BehaviorDailyResponse:
    """最近 N 个 UTC 日：优先取每日最新 PIT 行；没有（当日盘中/历史缺口）按同口径现算 live=True。"""
    now = datetime.utcnow()
    out: list[BehaviorDailySchema] = []
    for offset in range(days - 1, -1, -1):
        utc_date = (now - timedelta(days=offset)).strftime("%Y-%m-%d")
        row = (
            session.query(BehaviorDailySummary)
            .filter_by(symbol=symbol, utc_date=utc_date)
            .order_by(BehaviorDailySummary.computed_at.desc())
            .first()
        )
        extras = day_direction_extras(session, symbol, utc_date)
        if row is not None:
            out.append(BehaviorDailySchema(
                utc_date=utc_date, day_type=row.day_type,
                counts=json.loads(row.counts),
                composition=merge_composition(json.loads(row.composition)),   # 历史六类 PIT 行读取归并
                down_net_sum=row.down_net_sum, computed_at=_tf(row.computed_at), live=False,
                **extras,
            ))
        else:
            counts, composition, down_sum = aggregate_day(session, symbol, utc_date)
            out.append(BehaviorDailySchema(
                utc_date=utc_date, day_type=day_type_of(utc_date),
                counts=counts, composition=composition,
                down_net_sum=down_sum, computed_at=_tf(now), live=True,
                **extras,
            ))
    return BehaviorDailyResponse(symbol=symbol, days=out)


def linkage(session: Session, symbol: str, hours: int = 48,
            start: datetime | None = None, end: datetime | None = None) -> BehaviorLinkageResponse:
    """rolling S 曲线。默认贴最新数据回看 hours；显式 start/end（标注页跟随窗口 ±1/6/24h
    档位，2026-07-20 起默认 ±6h）时用请求区间，end 超出最新数据则贴到最新点收口。"""
    tiers = config.BEHAVIOR_TIERS.get(symbol)
    points = int(config.BEHAVIOR_ROLLING_POINTS)
    if not tiers:
        return BehaviorLinkageResponse(symbol=symbol, hours=hours, rolling_points=points,
                                       series=[], breadth=[])
    now = datetime.utcnow()
    pad = timedelta(minutes=5 * (points - 1) + 15)
    req_end = end or now
    req_start = start or (req_end - timedelta(hours=hours))
    btc_points = _points(session, symbol, req_start - pad, req_end)
    if not btc_points:
        return BehaviorLinkageResponse(symbol=symbol, hours=hours, rolling_points=points,
                                       series=[], breadth=[])
    btc_chg = chg_map(btc_points)
    t_btc = float(tiers[0])
    data_max = max(ts for ts, _ in btc_points)
    if start is None and end is None:
        end = data_max
        start = end - timedelta(hours=hours)
    else:
        end = min(req_end, data_max)
        start = req_start
    if start >= end:
        return BehaviorLinkageResponse(symbol=symbol, hours=hours, rolling_points=points,
                                       series=[], breadth=[])
    series: list[LinkageSeries] = []
    aligned: list[list[float | None]] = []
    grid: list[datetime] = []
    for ref in config.BEHAVIOR_REF_SYMBOLS:
        ref_tiers = config.BEHAVIOR_TIERS.get(ref)
        if not ref_tiers:                    # None = 未校准 → 禁用
            continue
        ref_chg = chg_map(_points(session, ref, start - pad, end))
        pts = rolling_s(btc_chg, ref_chg, t_btc, float(ref_tiers[0]), start, end,
                        points=points, coverage_min=config.BEHAVIOR_COVERAGE_MIN)
        if not grid:
            grid = [t for t, _ in pts]
        aligned.append([s for _, s in pts])
        series.append(LinkageSeries(
            symbol=ref, label=_REF_LABELS.get(ref, ref),
            points=[LinkagePoint(t=_tf(t), s=None if s is None else round(s, 3)) for t, s in pts],
        ))
    breadth: list[BreadthPoint] = []
    for i, t in enumerate(grid):
        vals = [col[i] for col in aligned if col[i] is not None]
        count = sum(1 for v in vals if abs(v) >= config.BEHAVIOR_S_MID) if vals else None
        breadth.append(BreadthPoint(t=_tf(t), count=count))
    return BehaviorLinkageResponse(symbol=symbol, hours=hours, rolling_points=points,
                                   series=series, breadth=breadth)


def review_segment(session: Session, segment_id: int, human_class: str | None):
    """人工审计（price-behavior-engine 2026-07-09）：确认=写当前机器类，改判=写新类，null=撤销。
    只动 human_*，机器 classification/class_version 原样保留作对照。"""
    from schemas.behavior import REVIEWABLE_CLASSES

    row = session.query(BehaviorSegment).filter_by(id=segment_id).one_or_none()
    if row is None:
        return None
    if human_class is not None:
        human_class = to_window_class(human_class)      # 兼容旧六类入参 → 归并三类
        if human_class not in REVIEWABLE_CLASSES:
            raise ValueError(f"非法类别: {human_class!r}（可选: {', '.join(REVIEWABLE_CLASSES)}）")
    row.human_class = human_class
    row.human_confirmed_at = datetime.utcnow() if human_class is not None else None
    session.commit()
    return row
