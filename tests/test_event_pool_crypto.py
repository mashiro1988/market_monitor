# -*- coding: utf-8 -*-
"""事件池 crypto 类型:立案带类型、列表按类型筛、币种读时派生。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models.crypto import NewsCoin
from models.news import NewsItem
from services import event_pool


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _crypto_news(s, title, coins=()):
    n = NewsItem(timestamp=datetime(2026, 8, 9, 12, 0), source="blockbeats", title=title,
                 language="zh", market="crypto", is_crypto_affair=True,
                 tagged_at=datetime(2026, 8, 9, 12, 1))
    s.add(n); s.commit()
    for c in coins:
        s.add(NewsCoin(news_id=n.id, coin=c))
    s.commit()
    return n


def test_create_event_with_crypto_type(session):
    n = _crypto_news(session, "币安上新 XYZ")
    evt = event_pool.create_event(session, name="XYZ 上所", news_ids=[n.id],
                                  created_from="manual", event_type="crypto")
    assert evt.event_type == "crypto"


def test_create_event_defaults_to_macro(session):
    n = _crypto_news(session, "随便")
    evt = event_pool.create_event(session, name="宏观事件", news_ids=[n.id],
                                  created_from="manual")
    assert evt.event_type == "macro"


def test_create_event_rejects_bad_type(session):
    n = _crypto_news(session, "随便")
    with pytest.raises(ValueError, match="event_type"):
        event_pool.create_event(session, name="x", news_ids=[n.id],
                                created_from="manual", event_type="stock")


def test_list_events_filtered_by_type(session):
    n = _crypto_news(session, "币安上新")
    event_pool.create_event(session, name="加密事件", news_ids=[n.id],
                            created_from="manual", event_type="crypto")
    event_pool.create_event(session, name="宏观事件", news_ids=[n.id],
                            created_from="manual", event_type="macro")
    assert [e["name"] for e in event_pool.list_events(session, event_type="crypto")] == ["加密事件"]
    assert [e["name"] for e in event_pool.list_events(session, event_type="macro")] == ["宏观事件"]
    assert len(event_pool.list_events(session)) == 2       # 不传类型=全都要


def test_event_coins_derived_from_timeline(session):
    a = _crypto_news(session, "SOL 生态基金", coins=["SOL"])
    b = _crypto_news(session, "SOL 与 ARB 跨链", coins=["SOL", "ARB"])
    event_pool.create_event(session, name="SOL 生态", news_ids=[a.id, b.id],
                            created_from="manual", event_type="crypto")
    rows = event_pool.list_events(session, event_type="crypto")
    assert rows[0]["coins"] == ["ARB", "SOL"]      # 并集,排序稳定


def test_macro_event_has_no_coins(session):
    n = _crypto_news(session, "随便", coins=["BTC"])
    event_pool.create_event(session, name="宏观事件", news_ids=[n.id],
                            created_from="manual", event_type="macro")
    rows = event_pool.list_events(session, event_type="macro")
    assert rows[0]["coins"] == []


def test_detached_link_coins_excluded(session):
    """摘下的证据不该再贡献币种——事件涉及哪些币要跟着时间轴走。"""
    from models.research import ResearchEventLink

    a = _crypto_news(session, "SOL 新闻", coins=["SOL"])
    b = _crypto_news(session, "误挂的 ARB", coins=["ARB"])
    evt = event_pool.create_event(session, name="SOL 事件", news_ids=[a.id, b.id],
                                  created_from="manual", event_type="crypto")
    link = (session.query(ResearchEventLink)
            .filter_by(event_id=evt.id, news_id=b.id).one())
    link.detached = True
    session.commit()
    assert event_pool.list_events(session, event_type="crypto")[0]["coins"] == ["SOL"]
