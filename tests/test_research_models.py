# -*- coding: utf-8 -*-
"""研究事件池模型与游标迁移(news-research-phase1 spec §3)。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from database import Base
from models.news import NewsItem
from models.research import ResearchEvent, ResearchEventLink


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def test_event_defaults(session):
    e = ResearchEvent(name="日本央行加息预期提前")
    session.add(e); session.commit()
    assert e.event_type == "macro"
    assert e.status == "active"
    assert e.created_from == "manual"
    assert e.gate_keywords is None


def test_link_unique_per_event_news(session):
    e = ResearchEvent(name="x"); session.add(e); session.commit()
    session.add(ResearchEventLink(event_id=e.id, news_id=1, link_source="human"))
    session.commit()
    session.add(ResearchEventLink(event_id=e.id, news_id=1, link_source="auto"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
    # 同一新闻挂另一个事件不受限(人工多挂,spec §3.2)
    e2 = ResearchEvent(name="y"); session.add(e2); session.commit()
    session.add(ResearchEventLink(event_id=e2.id, news_id=1, link_source="human"))
    session.commit()


def test_news_item_has_event_cursor_column(session):
    n = NewsItem(timestamp=datetime(2026, 8, 1), source="jin10", title="t", language="zh")
    session.add(n); session.commit()
    assert n.event_linked_at is None    # 新库新新闻:游标空=待处理


def test_migrate_news_event_cursor_stamps_legacy_rows(tmp_path):
    """旧库(无列)→ 补列 + 存量一次性盖章;幂等(spec §3.3/§13.1)。"""
    from database import migrate_news_event_cursor
    eng = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with eng.begin() as conn:
        conn.execute(text(
            "CREATE TABLE news_items (id INTEGER PRIMARY KEY, timestamp DATETIME, "
            "source VARCHAR(50), title VARCHAR(500), language VARCHAR(5))"
        ))
        conn.execute(text("INSERT INTO news_items (timestamp, source, title, language) "
                          "VALUES ('2026-07-01 00:00:00', 'jin10', '存量新闻', 'zh')"))
    with eng.begin() as conn:
        assert migrate_news_event_cursor(conn) is True
    with eng.connect() as conn:
        cols = {c["name"] for c in inspect(conn).get_columns("news_items")}
        assert "event_linked_at" in cols
        stamped = conn.execute(text(
            "SELECT COUNT(*) FROM news_items WHERE event_linked_at IS NOT NULL")).scalar()
        assert stamped == 1                       # 存量全部盖章(历史默认出池)
    with eng.begin() as conn:
        assert migrate_news_event_cursor(conn) is False   # 第二次跑:无操作


def test_event_config_constants():
    import config
    assert config.EVENT_LINK_MIN_IMPORTANCE == 6
    assert config.EVENT_OBS_REACTION_MINUTES == 10
    assert config.EVENT_OBS_SYMBOLS == ("BTC/USDT",)
    assert config.EVENT_BACKSCAN_DEFAULT_HOURS == 72
    assert ("jin10", r"^金十数据整理：") in config.NEWS_EVENT_LINK_BLACKLIST
