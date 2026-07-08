"""Retention cleanup for time-series tables.

The annotation training set stores news IDs in JSON columns rather than a
database foreign key, so news cleanup must preserve any referenced item.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Iterable

import config
from database import SessionLocal
from models.alert_log import AlertLog
from models.news import NewsItem, NewsPriceAnnotation
from models.prediction import PredictionMarket
from models.price import PriceSnapshot


def cleanup_retained_data(*, session=None, now: datetime | None = None, retention: dict | None = None) -> dict[str, int]:
    """Delete records older than configured retention windows.

    If a session is supplied, the caller owns commit/rollback. Otherwise this
    function opens and commits its own session.
    """
    own_session = session is None
    session = session or SessionLocal()
    now = now or datetime.utcnow()
    retention = retention or config.DATA_RETENTION

    try:
        deleted = {
            "price_snapshots": _delete_older_than(
                session,
                PriceSnapshot,
                PriceSnapshot.timestamp,
                _cutoff(now, retention.get("price_snapshots_days")),
            ),
            "news_items": _delete_old_news(
                session,
                _cutoff(now, retention.get("news_items_days")),
            ),
            "prediction_markets": _delete_older_than(
                session,
                PredictionMarket,
                PredictionMarket.timestamp,
                _cutoff(now, retention.get("prediction_markets_days")),
            ),
            "alert_logs": _delete_older_than(
                session,
                AlertLog,
                AlertLog.timestamp,
                _cutoff(now, retention.get("alert_logs_days")),
            ),
        }
        if own_session:
            session.commit()
        else:
            session.flush()
        return deleted
    except Exception:
        if own_session:
            session.rollback()
        raise
    finally:
        if own_session:
            session.close()


def _cutoff(now: datetime, days: int | None) -> datetime | None:
    if days is None or days <= 0:
        return None
    return now - timedelta(days=days)


def _delete_older_than(session, model, column, cutoff: datetime | None) -> int:
    if cutoff is None:
        return 0
    return (
        session.query(model)
        .filter(column < cutoff)
        .delete(synchronize_session=False)
    )


def _delete_old_news(session, cutoff: datetime | None) -> int:
    if cutoff is None:
        return 0

    protected_ids = _annotation_news_ids(session)
    old_ids = [
        row[0]
        for row in session.query(NewsItem.id).filter(NewsItem.timestamp < cutoff).all()
    ]
    delete_ids = [news_id for news_id in old_ids if news_id not in protected_ids]
    deleted = 0
    for chunk in _chunks(delete_ids, 500):
        deleted += (
            session.query(NewsItem)
            .filter(NewsItem.id.in_(chunk))
            .delete(synchronize_session=False)
        )
    return deleted


def _annotation_news_ids(session) -> set[int]:
    protected: set[int] = set()
    rows = session.query(
        NewsPriceAnnotation.causal_news_ids,
        NewsPriceAnnotation.candidate_news_ids,
        NewsPriceAnnotation.news_roles,
    ).all()
    for causal_ids, candidate_ids, news_roles in rows:
        protected.update(_parse_news_ids(causal_ids))
        protected.update(_parse_news_ids(candidate_ids))
        protected.update(_parse_news_ids(news_roles))
    return protected


def _parse_news_ids(raw: str | None) -> set[int]:
    if not raw:
        return set()
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return set()

    if isinstance(payload, dict):
        values = payload.keys()
    elif isinstance(payload, list):
        values = payload
    else:
        values = [payload]

    ids: set[int] = set()
    for value in values:
        try:
            ids.add(int(value))
        except (TypeError, ValueError):
            continue
    return ids


def _chunks(values: list[int], size: int) -> Iterable[list[int]]:
    for start in range(0, len(values), size):
        yield values[start:start + size]

