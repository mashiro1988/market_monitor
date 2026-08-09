# -*- coding: utf-8 -*-
"""加密快讯接口:只回 market=crypto,且带币种与币圈事务标记。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models.crypto import NewsCoin
from models.news import NewsItem
from services import news_service
from services.time_utils import utc_now_naive


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _news(s, title, market="crypto", affair=True, coins=(), hours_ago=1,
          source="blockbeats", score=6):
    n = NewsItem(timestamp=utc_now_naive() - timedelta(hours=hours_ago),
                 source=source, title=title, language="zh", market=market,
                 is_crypto_affair=affair, llm_importance=score)
    s.add(n); s.commit()
    for c in coins:
        s.add(NewsCoin(news_id=n.id, coin=c))
    s.commit()
    return n


def test_only_crypto_market_returned(session):
    _news(session, "币圈新闻")
    _news(session, "宏观新闻", market="macro", source="jin10")
    resp = news_service.get_crypto_news(session)
    assert [i.title for i in resp.items] == ["币圈新闻"]


def test_coins_and_affair_exposed(session):
    _news(session, "SOL 生态基金", coins=["SOL", "ARB"])
    item = news_service.get_crypto_news(session).items[0]
    assert item.coins == ["ARB", "SOL"]
    assert item.is_crypto_affair is True


def test_affair_only_filter(session):
    _news(session, "币圈事务", affair=True)
    _news(session, "转载宏观", affair=False)
    resp = news_service.get_crypto_news(session, affair_only=True)
    assert [i.title for i in resp.items] == ["币圈事务"]
    # 默认不筛:转载宏观照常展示(不入池≠不可见)
    assert len(news_service.get_crypto_news(session).items) == 2


def test_coin_filter_case_insensitive(session):
    _news(session, "SOL 新闻", coins=["SOL"])
    _news(session, "ARB 新闻", coins=["ARB"])
    resp = news_service.get_crypto_news(session, coin="sol")
    assert [i.title for i in resp.items] == ["SOL 新闻"]


def test_low_score_news_kept_by_default(session):
    """加密线默认不按分数拦——小币新闻分数天然低,拦了就没得研究。"""
    _news(session, "小币动态", score=2)
    assert len(news_service.get_crypto_news(session).items) == 1


def test_search_matches_title(session):
    _news(session, "币安上新 XYZ")
    _news(session, "其它新闻")
    resp = news_service.get_crypto_news(session, search="币安")
    assert [i.title for i in resp.items] == ["币安上新 XYZ"]


def test_crypto_sources_listed():
    keys = {s.key for s in news_service.list_crypto_sources()}
    assert "blockbeats" in keys and "binance_ann" in keys
