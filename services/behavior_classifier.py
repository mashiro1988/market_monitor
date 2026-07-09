# -*- coding: utf-8 -*-
"""价格行为引擎 · 分类 job + 日汇总（docs/specs/price-behavior-engine-plan.md Task 5）。

流程（每 5min 一轮，settle 后分类）：
  detect_segments(近 48h BTC 快照) → upsert behavior_segments
  → 0.3 档段 = count_only（只计数，不归因不喂 LLM）
  → 0.5 档以上且 settle（段止 + 后窗 1h + settle 余量）→ 逐参照算共振分 S + 新闻命中
  → 十字格分类（S × 新闻；全参照无分 = 无对照，其中命中新闻仍标"新闻驱动(无价格对照确认)"）。

分类是机器做的、可整体重跑：段是原始数据，classification 随 class_version 换版全历史重算。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

import config
from models.behavior import BehaviorDailySummary, BehaviorSegment
from models.news import NewsItem
from models.price import PriceSnapshot
from services.behavior_segments import Segment, detect_segments
from services.resonance_score import BIG_WINDOW_MINUTES, chg_map, s_score

CLASS_VERSION = "v1"
DETECT_LOOKBACK_HOURS = 48
COUNT_ONLY = "count_only"
COMPOSITION_CLASSES = (
    "macro_news", "pure_resonance", "industry_news", "sentiment",
    "no_ref_news", "no_ref_pending",
)


# ---------- 取数 ----------

def _points(session: Session, symbol: str, start: datetime, end: datetime) -> list[tuple[datetime, float]]:
    rows = (
        session.query(PriceSnapshot.timestamp, PriceSnapshot.price)
        .filter(PriceSnapshot.symbol == symbol,
                PriceSnapshot.timestamp >= start,
                PriceSnapshot.timestamp <= end)
        .order_by(PriceSnapshot.timestamp.asc())
        .all()
    )
    return [(ts, price) for ts, price in rows if price]


def _news_ids(session: Session, start: datetime, end: datetime) -> list[int]:
    """段窗 ±BEHAVIOR_NEWS_WINDOW_MIN 内 a-priori 量级大/中的新闻（内容判，不看价格）。"""
    pad = timedelta(minutes=config.BEHAVIOR_NEWS_WINDOW_MIN)
    rows = (
        session.query(NewsItem.id)
        .filter(NewsItem.timestamp >= start - pad,
                NewsItem.timestamp <= end + pad,
                NewsItem.magnitude_tier.in_(config.BEHAVIOR_NEWS_MAGNITUDES))
        .order_by(NewsItem.timestamp.asc())
        .all()
    )
    return [r[0] for r in rows]


# ---------- 分类 ----------

def _classify_cell(max_abs_s: float | None, has_ref: bool, has_news: bool,
                   s_hi: float | None = None) -> str:
    """十字格（spec §1.5）：S 答"宏观跟没跟"，新闻命中答"有没有可指认的新闻"，两信号正交。"""
    hi = config.BEHAVIOR_S_HI if s_hi is None else s_hi
    if not has_ref:                      # 无对照 ≠ 无宏观新闻（2026-07-09 用户纠正）
        return "no_ref_news" if has_news else "no_ref_pending"
    if max_abs_s is not None and max_abs_s >= hi:
        return "macro_news" if has_news else "pure_resonance"
    # < HI（含 MID~HI 弱共振带：仅展示证据，类别按新闻命中辅助定）
    return "industry_news" if has_news else "sentiment"


def _settled(seg_end: datetime, now: datetime) -> bool:
    margin = BIG_WINDOW_MINUTES + int(config.ANNOTATION_SETTLE_MARGIN_MINUTES)
    return seg_end + timedelta(minutes=margin) <= now


def _upsert_segment(session: Session, symbol: str, seg: Segment) -> BehaviorSegment:
    """按 (symbol, start_dt, direction) 匹配：段随数据生长时更新同一行，不重复建段。"""
    row = (
        session.query(BehaviorSegment)
        .filter_by(symbol=symbol, start_dt=seg.start_dt, direction=seg.direction)
        .one_or_none()
    )
    if row is None:
        row = BehaviorSegment(symbol=symbol, start_dt=seg.start_dt, direction=seg.direction,
                              end_dt=seg.end_dt, tier_idx=seg.tier_idx, tier_max=seg.tier_max,
                              net_pct=seg.net_pct, amp_pct=seg.amp_pct, key_ts=seg.key_ts)
        session.add(row)
    else:
        row.end_dt = seg.end_dt
        row.tier_idx, row.tier_max = seg.tier_idx, seg.tier_max
        row.net_pct, row.amp_pct, row.key_ts = seg.net_pct, seg.amp_pct, seg.key_ts
    return row


def classify(session: Session, symbol: str = "BTC/USDT", now: datetime | None = None) -> dict:
    """一轮检测 + 分类。返回统计（detected/classified）供日志。"""
    now = now or datetime.utcnow()
    tiers = config.BEHAVIOR_TIERS.get(symbol)
    if not tiers:
        return {"detected": 0, "classified": 0}
    pad = timedelta(minutes=BIG_WINDOW_MINUTES + 15)
    btc_points = _points(session, symbol, now - timedelta(hours=DETECT_LOOKBACK_HOURS) - pad, now)
    segments = detect_segments(btc_points, tiers)
    rows = [_upsert_segment(session, symbol, s) for s in segments]
    session.flush()

    # 0.3 档：只计数
    for row in rows:
        if row.tier_idx == 0 and row.classification != COUNT_ONLY:
            row.classification, row.class_version = COUNT_ONLY, CLASS_VERSION

    todo = [r for r in rows
            if r.tier_idx >= 1 and _settled(r.end_dt, now)
            and not (r.classification in COMPOSITION_CLASSES and r.class_version == CLASS_VERSION)]
    classified = 0
    if todo:
        btc_chg = chg_map(btc_points)
        t_btc = float(tiers[0])
        span_start = min(r.start_dt for r in todo) - pad
        span_end = max(r.end_dt for r in todo) + pad
        ref_chgs: dict[str, tuple[dict, float]] = {}
        for ref in config.BEHAVIOR_REF_SYMBOLS:
            ref_tiers = config.BEHAVIOR_TIERS.get(ref)
            if not ref_tiers:            # None = 未校准 → 整体禁用
                continue
            ref_chgs[ref] = (chg_map(_points(session, ref, span_start, span_end)), float(ref_tiers[0]))
        for row in todo:
            scores: dict[str, dict] = {}
            for ref, (rchg, t_ref) in ref_chgs.items():
                r = s_score(btc_chg, rchg, row.start_dt, row.end_dt, t_btc, t_ref,
                            coverage_min=config.BEHAVIOR_COVERAGE_MIN)
                if r is not None:
                    scores[ref] = {"s": round(r[0], 4), "ess": round(r[1], 2), "coverage": round(r[2], 3)}
            ids = _news_ids(session, row.start_dt, row.end_dt)
            max_abs = max((abs(v["s"]) for v in scores.values()), default=None)
            row.classification = _classify_cell(max_abs, bool(scores), bool(ids))
            row.class_version = CLASS_VERSION
            row.s_scores = json.dumps(scores, ensure_ascii=False)
            row.news_ids = json.dumps(ids)
            classified += 1
    session.commit()
    return {"detected": len(rows), "classified": classified}


# ---------- 日汇总（point-in-time 追加） ----------

def write_daily_summary(session: Session, symbol: str, utc_date: str,
                        now: datetime | None = None) -> BehaviorDailySummary:
    """按段的 start_dt 归日聚合，append 一条 PIT 记录（读取取 computed_at 最新）。"""
    now = now or datetime.utcnow()
    day = datetime.strptime(utc_date, "%Y-%m-%d")
    rows = (
        session.query(BehaviorSegment)
        .filter(BehaviorSegment.symbol == symbol,
                BehaviorSegment.start_dt >= day,
                BehaviorSegment.start_dt < day + timedelta(days=1))
        .all()
    )
    counts: dict[str, dict[str, int]] = {}
    composition = {k: 0 for k in COMPOSITION_CLASSES}
    down_sum = 0.0
    for r in rows:
        tier_key = f"{r.tier_max:g}"
        bucket = counts.setdefault(tier_key, {"up": 0, "down": 0})
        bucket["up" if r.direction > 0 else "down"] += 1
        if r.classification in composition:
            composition[r.classification] += 1
        if r.direction < 0 and r.net_pct is not None:
            down_sum += r.net_pct
    summary = BehaviorDailySummary(
        symbol=symbol, utc_date=utc_date,
        day_type="weekend" if day.weekday() >= 5 else "weekday",
        counts=json.dumps(counts), composition=json.dumps(composition),
        down_net_sum=round(down_sum, 4), computed_at=now,
    )
    session.add(summary)
    session.commit()
    return summary


# ---------- 调度入口（api/app.py 注册；单 worker，与现有 job 同进程） ----------

def run_behavior_cycle() -> dict:
    from database import SessionLocal
    session = SessionLocal()
    try:
        return classify(session)
    finally:
        session.close()


def run_daily_summary() -> dict:
    """UTC 00:05 汇总昨日（PIT 追加）。"""
    from database import SessionLocal
    session = SessionLocal()
    try:
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        row = write_daily_summary(session, "BTC/USDT", yesterday)
        return {"utc_date": row.utc_date, "computed_at": row.computed_at.isoformat()}
    finally:
        session.close()
