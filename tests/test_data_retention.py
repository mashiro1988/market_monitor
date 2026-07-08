from datetime import datetime, timedelta
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models.alert_log import AlertLog
from models.news import NewsItem, NewsPriceAnnotation
from models.prediction import PredictionMarket
from models.price import PriceSnapshot
from services.data_retention import cleanup_retained_data


NOW = datetime(2026, 7, 6, 12, 0)
RETENTION = {
    "price_snapshots_days": 30,
    "news_items_days": 90,
    "prediction_markets_days": 30,
    "alert_logs_days": 90,
}


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _news(session, title: str, ts: datetime) -> NewsItem:
    item = NewsItem(timestamp=ts, source="test", title=title, language="zh")
    session.add(item)
    session.flush()
    return item


def test_cleanup_retained_data_deletes_old_unreferenced_rows_only():
    session = _session()
    try:
        old = NOW - timedelta(days=120)
        recent = NOW - timedelta(days=2)

        session.add_all([
            PriceSnapshot(timestamp=old, asset_class="crypto", symbol="BTC/USDT", name="BTC", price=1, source="t"),
            PriceSnapshot(timestamp=recent, asset_class="crypto", symbol="ETH/USDT", name="ETH", price=2, source="t"),
            PredictionMarket(timestamp=old, market_id="old", question="old?", outcome="Yes", probability=0.4),
            PredictionMarket(timestamp=recent, market_id="new", question="new?", outcome="Yes", probability=0.5),
            AlertLog(timestamp=old, rule_name="old", message="old", channel="console"),
            AlertLog(timestamp=recent, rule_name="new", message="new", channel="console"),
        ])
        _news(session, "old unreferenced", old)
        old_causal = _news(session, "old causal", old)
        old_candidate = _news(session, "old candidate", old)
        old_role = _news(session, "old role", old)
        recent_news = _news(session, "recent", recent)
        session.add(NewsPriceAnnotation(
            symbol="BTC/USDT",
            window_start=recent,
            window_end=recent + timedelta(minutes=30),
            context_start=recent - timedelta(hours=1),
            context_end=recent + timedelta(hours=1),
            causal_news_ids=json.dumps([old_causal.id]),
            candidate_news_ids=json.dumps([old_candidate.id]),
            news_roles=json.dumps({str(old_role.id): "driver"}),
        ))
        session.commit()

        deleted = cleanup_retained_data(session=session, now=NOW, retention=RETENTION)

        assert deleted == {
            "price_snapshots": 1,
            "news_items": 1,
            "prediction_markets": 1,
            "alert_logs": 1,
        }
        remaining_titles = {row.title for row in session.query(NewsItem).all()}
        assert "old unreferenced" not in remaining_titles
        assert remaining_titles == {"old causal", "old candidate", "old role", "recent"}
        assert session.query(PriceSnapshot).count() == 1
        assert session.query(PredictionMarket).count() == 1
        assert session.query(AlertLog).count() == 1
        assert session.query(NewsPriceAnnotation).count() == 1
        assert recent_news.id is not None
    finally:
        session.close()


def test_cleanup_retained_data_ignores_bad_annotation_json():
    session = _session()
    try:
        old = NOW - timedelta(days=120)
        _news(session, "old bad json", old)
        session.add(NewsPriceAnnotation(
            symbol="BTC/USDT",
            window_start=old,
            window_end=old + timedelta(minutes=30),
            context_start=old - timedelta(hours=1),
            context_end=old + timedelta(hours=1),
            causal_news_ids="not-json",
            candidate_news_ids=None,
            news_roles="{bad",
        ))
        session.commit()

        deleted = cleanup_retained_data(session=session, now=NOW, retention=RETENTION)

        assert deleted["news_items"] == 1
        assert session.query(NewsItem).count() == 0
    finally:
        session.close()
