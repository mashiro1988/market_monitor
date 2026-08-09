# -*- coding: utf-8 -*-
"""加密新闻必须被宏观三条路径挡在外面(web3 二期A design §2 零污染)。

护栏的意义:加密源 200-300 条/天全量入同一张表,若宏观的补评分/打标/行为命中
照旧全表扫,就会用宏观口径给币圈新闻打分、让币圈新闻触发 BTC 的 has_news。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models.news import NewsItem
from services import behavior_classifier, news_tagging


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _news(s, title, market="macro", score=8, tagged=False):
    n = NewsItem(timestamp=datetime(2026, 8, 9, 12, 0), source="jin10", title=title,
                 language="zh", llm_importance=score, market=market,
                 traditional_open=True,
                 tagged_at=datetime(2026, 8, 9, 12, 1) if tagged else None)
    s.add(n); s.commit()
    return n


def test_macro_tagging_skips_crypto_news(session, monkeypatch):
    _news(session, "宏观新闻", market="macro")
    _news(session, "币圈新闻", market="crypto")
    seen = []

    def fake_batch(_s, chunk):
        seen.extend(n.title for n in chunk)
        return len(chunk)

    monkeypatch.setattr(news_tagging, "tag_news_batch", fake_batch)
    news_tagging.tag_untagged(session)
    assert seen == ["宏观新闻"]


def test_behavior_has_news_ignores_crypto(session):
    _news(session, "宏观新闻", market="macro")
    _news(session, "币圈新闻", market="crypto")
    ids = behavior_classifier._news_ids(
        session, datetime(2026, 8, 9, 11, 50), datetime(2026, 8, 9, 12, 10))
    titles = [session.get(NewsItem, i).title for i in ids]
    assert titles == ["宏观新闻"]


def test_rescore_skips_crypto_news(session):
    from services import news_rescore

    _news(session, "宏观未评分", market="macro", score=None)
    _news(session, "币圈未评分", market="crypto", score=None)

    class FakeScorer:
        enabled = True

        def __init__(self):
            self.seen = []

        def enrich_batch(self, records):
            self.seen.extend(r.title for r in records)
            for r in records:
                r.llm_importance = 7
            return records

    scorer = FakeScorer()
    news_rescore.rescore_unscored(session, scorer=scorer, now=datetime(2026, 8, 9, 12, 30))
    assert scorer.seen == ["宏观未评分"]


def test_default_market_is_macro(session):
    n = NewsItem(timestamp=datetime(2026, 8, 9, 12, 0), source="jin10",
                 title="默认", language="zh")
    session.add(n); session.commit()
    assert n.market == "macro"
