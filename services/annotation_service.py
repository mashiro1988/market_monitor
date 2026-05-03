from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

import config
from models.news import NewsItem, NewsPriceAnnotation
from models.price import PriceSnapshot
from schemas.annotations import (
    AnnotationCreateRequest,
    AnnotationResponse,
    AnnotationSymbol,
    ContextNewsResponse,
    PriceRuleSchema,
    PriceWindowSchema,
)
from services.news_service import to_news_schema
from services.time_utils import parse_datetime, timestamp_pair, utc_now_naive

TARGET_PRICE_SYMBOLS = ["BTC/USDT", "ETH/USDT", "NQ=F"]


def load_alert_price_rules() -> list[PriceRuleSchema]:
    rules: list[PriceRuleSchema] = []
    for rule in config.ALERT_RULES:
        if not rule.get("enabled", True) or rule.get("rule_type") != "price_change":
            continue
        params = rule.get("params", {})
        symbol = params.get("symbol")
        threshold = params.get("threshold_pct")
        window_minutes = params.get("window_minutes")
        if symbol in TARGET_PRICE_SYMBOLS and threshold is not None:
            rules.append(
                PriceRuleSchema(
                    symbol=symbol,
                    threshold_pct=float(threshold),
                    window_minutes=int(window_minutes or config.SCAN_INTERVALS["price"]),
                )
            )
    return rules


def load_symbols(session: Session, hours: int = 72) -> list[AnnotationSymbol]:
    cutoff = utc_now_naive() - timedelta(hours=max(1, min(int(hours or 72), 24 * 30)))
    rule_symbols = {rule.symbol for rule in load_alert_price_rules()}
    rows = (
        session.query(PriceSnapshot.symbol, PriceSnapshot.name, PriceSnapshot.asset_class)
        .filter(PriceSnapshot.timestamp >= cutoff, PriceSnapshot.symbol.in_(list(rule_symbols or TARGET_PRICE_SYMBOLS)))
        .distinct()
        .order_by(PriceSnapshot.asset_class, PriceSnapshot.symbol)
        .all()
    )
    return [AnnotationSymbol(symbol=row.symbol, name=row.name, asset_class=row.asset_class) for row in rows]


def _nearest_snapshot(rows: list[PriceSnapshot], target_time: datetime, before_time: datetime, tolerance_minutes: int) -> PriceSnapshot | None:
    candidates = [
        row for row in rows
        if row.timestamp < before_time
        if abs((row.timestamp - target_time).total_seconds()) <= tolerance_minutes * 60
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda row: abs((row.timestamp - target_time).total_seconds()))


def load_price_windows(
    session: Session,
    symbol: str,
    hours: int,
    threshold_pct: float | None = None,
    window_minutes: int | None = None,
) -> list[PriceWindowSchema]:
    rule_map = {rule.symbol: rule for rule in load_alert_price_rules()}
    rule = rule_map.get(symbol)
    if rule is None and (threshold_pct is None or window_minutes is None):
        return []
    threshold_pct = float(threshold_pct if threshold_pct is not None else rule.threshold_pct)
    window_minutes = int(window_minutes if window_minutes is not None else rule.window_minutes)
    hours = max(1, min(int(hours or 72), 24 * 30))
    cutoff = utc_now_naive() - timedelta(hours=hours, minutes=window_minutes + 10)
    rows = (
        session.query(PriceSnapshot)
        .filter(PriceSnapshot.symbol == symbol, PriceSnapshot.timestamp >= cutoff)
        .order_by(PriceSnapshot.timestamp.asc())
        .all()
    )
    display_cutoff = utc_now_naive() - timedelta(hours=hours)
    tolerance_minutes = max(config.SCAN_INTERVALS["price"] * 2, 1)
    windows: list[PriceWindowSchema] = []
    for current in rows:
        if current.timestamp < display_cutoff:
            continue
        baseline_time = current.timestamp - timedelta(minutes=window_minutes)
        baseline = _nearest_snapshot(rows, baseline_time, current.timestamp, tolerance_minutes)
        if baseline is None or not baseline.price:
            continue
        change_pct = ((current.price - baseline.price) / abs(baseline.price)) * 100
        if abs(change_pct) < threshold_pct:
            continue
        windows.append(
            PriceWindowSchema(
                symbol=current.symbol,
                asset_class=current.asset_class,
                name=current.name,
                window_start=timestamp_pair(baseline.timestamp),
                window_end=timestamp_pair(current.timestamp),
                configured_window_minutes=window_minutes,
                actual_window_minutes=round((current.timestamp - baseline.timestamp).total_seconds() / 60, 1),
                price_start=baseline.price,
                price_end=current.price,
                change_pct=change_pct,
            )
        )
    return sorted(windows, key=lambda item: item.window_end.timestamp_utc or "", reverse=True)[:200]


def load_context_news(session: Session, context_start: datetime, context_end: datetime) -> ContextNewsResponse:
    rows = (
        session.query(NewsItem)
        .filter(
            NewsItem.source.in_(["jin10", "bloomberg"]),
            NewsItem.timestamp >= context_start,
            NewsItem.timestamp <= context_end,
        )
        .order_by(NewsItem.timestamp.asc())
        .all()
    )
    return ContextNewsResponse(items=[to_news_schema(row) for row in rows])


def load_context_news_for_window(
    session: Session,
    window_start_utc: str,
    window_end_utc: str,
    minutes: int = 30,
) -> ContextNewsResponse:
    start = parse_datetime(window_start_utc)
    end = parse_datetime(window_end_utc)
    if start is None or end is None:
        return ContextNewsResponse(items=[])
    return load_context_news(session, start - timedelta(minutes=minutes), end + timedelta(minutes=minutes))


def _find_window_snapshot(session: Session, symbol: str, timestamp_value: datetime) -> PriceSnapshot | None:
    return (
        session.query(PriceSnapshot)
        .filter(PriceSnapshot.symbol == symbol, PriceSnapshot.timestamp == timestamp_value)
        .first()
    )


def upsert_annotation(session: Session, request: AnnotationCreateRequest) -> AnnotationResponse:
    window_start = parse_datetime(request.window_start_utc)
    window_end = parse_datetime(request.window_end_utc)
    if window_start is None or window_end is None:
        raise ValueError("window_start_utc/window_end_utc 不能为空")

    start_snapshot = _find_window_snapshot(session, request.symbol, window_start)
    end_snapshot = _find_window_snapshot(session, request.symbol, window_end)
    if end_snapshot is None:
        raise ValueError("找不到窗口终点价格快照")
    if start_snapshot is None:
        raise ValueError("找不到窗口起点价格快照")

    existing = (
        session.query(NewsPriceAnnotation)
        .filter(
            NewsPriceAnnotation.symbol == request.symbol,
            NewsPriceAnnotation.window_start == window_start,
            NewsPriceAnnotation.window_end == window_end,
        )
        .first()
    )
    if existing is None:
        existing = NewsPriceAnnotation(
            symbol=request.symbol,
            window_start=window_start,
            window_end=window_end,
        )
        session.add(existing)

    existing.asset_class = end_snapshot.asset_class
    existing.context_start = window_start - timedelta(minutes=30)
    existing.context_end = window_end + timedelta(minutes=30)
    existing.threshold_pct = request.threshold_pct
    existing.price_start = start_snapshot.price
    existing.price_end = end_snapshot.price
    existing.change_pct = ((end_snapshot.price - start_snapshot.price) / abs(start_snapshot.price)) * 100 if start_snapshot.price else None
    existing.causal_news_ids = json.dumps(request.selected_news_ids, ensure_ascii=False)
    existing.no_clear_news = request.no_clear_news
    existing.notes = (request.notes or "").strip() or None
    existing.labeler = (request.labeler or "").strip() or None
    existing.updated_at = utc_now_naive()
    session.commit()
    return AnnotationResponse(id=existing.id)
