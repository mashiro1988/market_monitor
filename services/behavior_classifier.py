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
from services.resonance_score import BIG_WINDOW_MINUTES, chg_map, rolling_peak

CLASS_VERSION = "v2"   # v2 = ESS 地板 + coverage 0.5 定稿口径（2026-07-12）；换版可全历史重跑
DETECT_LOOKBACK_HOURS = 48       # 上下文水库：保证 WRITE_HORIZON 内结束的段起点上下文必然完整
WRITE_HORIZON_HOURS = 6          # settle 写保护（R2）：只写结束时间在此之内的段；历史只读
COUNT_ONLY = "count_only"
COMPOSITION_CLASSES = (
    "macro_news", "pure_resonance", "industry_news", "sentiment",
    "no_ref_news", "no_ref_pending",
)
# 窗口级三类（Phase 2，2026-07-09 用户定）：人工标注与结论页构成的口径。
# 机器六类保留在 classification 底层（无对照信息有用），展示/聚合经 to_window_class 归并。
WINDOW_CLASSES = ("news_driven", "pure_resonance", "sentiment_tech")
_SIX_TO_THREE = {
    "macro_news": "news_driven",
    "industry_news": "news_driven",
    "no_ref_news": "news_driven",
    "pure_resonance": "pure_resonance",
    "sentiment": "sentiment_tech",
    "no_ref_pending": "sentiment_tech",
}


def to_window_class(cls: str | None) -> str | None:
    """六类/三类 → 三类归并（幂等：三类值原样通过；count_only/None → None）。"""
    if cls in WINDOW_CLASSES:
        return cls
    return _SIX_TO_THREE.get(cls)


def merge_composition(raw: dict) -> dict:
    """构成字典归并为三类 + no_ref 注记（兼容历史 PIT 六类行，读取归并、不重写历史）。"""
    if set(raw) <= set(WINDOW_CLASSES) | {"no_ref"}:
        out = {k: int(raw.get(k, 0)) for k in WINDOW_CLASSES}
        out["no_ref"] = int(raw.get("no_ref", 0))
        return out
    out = {k: 0 for k in WINDOW_CLASSES}
    out["no_ref"] = 0
    for k, v in raw.items():
        three = to_window_class(k)
        if three:
            out[three] += int(v)
        if k in ("no_ref_news", "no_ref_pending"):
            out["no_ref"] += int(v)
    return out


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
    # settle 写保护（2026-07-12 架构简化 R2，用户拍板"实时判断 + settle 后冻结"）：
    # 只登记「结束时间在 WRITE_HORIZON 内」的段（实时/生长中的，右缘数据天然完整），
    # 外加已存在但未 settle 的行（停机恢复后需要补 settle）。更早的历史一律只读——
    # 48h 扫描对旧数据爱检出什么都行，它没有笔；空洞/边缘从此与历史行无关。
    # 数据补洞后的历史修正走显式重算（scripts/一次性），不靠扫描顺手改。
    write_cutoff = now - timedelta(hours=WRITE_HORIZON_HOURS)
    unsettled_keys = {
        (r.start_dt, r.direction)
        for r in session.query(BehaviorSegment.start_dt, BehaviorSegment.direction)
        .filter(BehaviorSegment.symbol == symbol, BehaviorSegment.classification.is_(None))
        .all()
    }
    segments = [s for s in segments
                if s.end_dt >= write_cutoff or (s.start_dt, s.direction) in unsettled_keys]
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
        # rolling_peak 的拖尾窗在段起点要回看 (points-1)*5min，参照数据前侧 pad 相应放宽
        roll_points = int(config.BEHAVIOR_ROLLING_POINTS)
        pre_pad = timedelta(minutes=5 * (roll_points - 1) + 15)
        span_start = min(r.start_dt for r in todo) - pre_pad
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
                r = rolling_peak(btc_chg, rchg, t_btc, t_ref, row.start_dt, row.end_dt,
                                 tail_min=BIG_WINDOW_MINUTES, points=roll_points,
                                 coverage_min=config.BEHAVIOR_COVERAGE_MIN,
                                 ess_min=config.BEHAVIOR_ESS_THIN)
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

def aggregate_day(session: Session, symbol: str, utc_date: str) -> tuple[dict, dict, float]:
    """按段的 start_dt 归日聚合 → (counts, composition, down_net_sum)。
    PIT 写入与当日盘中 live 读数（behavior_views）共用同一口径。"""
    day = datetime.strptime(utc_date, "%Y-%m-%d")
    rows = (
        session.query(BehaviorSegment)
        .filter(BehaviorSegment.symbol == symbol,
                BehaviorSegment.start_dt >= day,
                BehaviorSegment.start_dt < day + timedelta(days=1))
        .all()
    )
    counts: dict[str, dict[str, int]] = {}
    composition = {k: 0 for k in WINDOW_CLASSES}
    composition["no_ref"] = 0                            # 注记：无对照段数（已含在三类里，另计不另加）
    down_sum = 0.0
    for r in rows:
        tier_key = f"{r.tier_max:g}"
        bucket = counts.setdefault(tier_key, {"up": 0, "down": 0})
        bucket["up" if r.direction > 0 else "down"] += 1
        effective = to_window_class(r.human_class) or to_window_class(r.classification)  # 人工优先
        if effective in WINDOW_CLASSES:
            composition[effective] += 1
        if r.classification in ("no_ref_news", "no_ref_pending") and r.tier_idx >= 1:
            composition["no_ref"] += 1
        if r.direction < 0 and r.net_pct is not None:
            down_sum += r.net_pct
    return counts, composition, round(down_sum, 4)


def day_direction_extras(session: Session, symbol: str, utc_date: str) -> dict:
    """方向拆分读数（2026-07-10 行为面板重画）：涨段净幅合计 + 情绪·技术面段的
    涨/跌个数与净幅。**compute-on-read**、不进 PIT——净幅只依赖段原始数据（settle 后不变），
    情绪归属按"人工优先"的当前结论（人工改判要立刻反映到趋势图，冻结旧结论反而误导）。"""
    day = datetime.strptime(utc_date, "%Y-%m-%d")
    rows = (
        session.query(BehaviorSegment)
        .filter(BehaviorSegment.symbol == symbol,
                BehaviorSegment.start_dt >= day,
                BehaviorSegment.start_dt < day + timedelta(days=1))
        .all()
    )
    up_sum = 0.0
    sent_up = sent_down = 0
    sent_up_sum = sent_down_sum = 0.0
    for r in rows:
        if r.direction > 0 and r.net_pct is not None:
            up_sum += r.net_pct
        if r.tier_idx is None or r.tier_idx < 1:
            continue                                   # 情绪拆分只看构成段（0.5 档以上）
        effective = to_window_class(r.human_class) or to_window_class(r.classification)
        if effective != "sentiment_tech":
            continue
        if r.direction > 0:
            sent_up += 1
            sent_up_sum += r.net_pct or 0.0
        else:
            sent_down += 1
            sent_down_sum += r.net_pct or 0.0
    return {
        "up_net_sum": round(up_sum, 4),
        "sent_up": sent_up,
        "sent_down": sent_down,
        "sent_up_net_sum": round(sent_up_sum, 4),
        "sent_down_net_sum": round(sent_down_sum, 4),
    }


def day_type_of(utc_date: str) -> str:
    return "weekend" if datetime.strptime(utc_date, "%Y-%m-%d").weekday() >= 5 else "weekday"


def write_daily_summary(session: Session, symbol: str, utc_date: str,
                        now: datetime | None = None) -> BehaviorDailySummary:
    """append 一条 PIT 记录（追加不覆盖，读取取 computed_at 最新）。"""
    now = now or datetime.utcnow()
    counts, composition, down_sum = aggregate_day(session, symbol, utc_date)
    summary = BehaviorDailySummary(
        symbol=symbol, utc_date=utc_date, day_type=day_type_of(utc_date),
        counts=json.dumps(counts), composition=json.dumps(composition),
        down_net_sum=down_sum, computed_at=now,
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
