# -*- coding: utf-8 -*-
"""新闻↔币种对照表:同一新闻同一币种只留一行(B 的归因反查地基)。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from database import Base
from models.crypto import NewsCoin


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def test_same_news_same_coin_rejected(session):
    session.add(NewsCoin(news_id=1, coin="SOL"))
    session.commit()
    session.add(NewsCoin(news_id=1, coin="SOL"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_same_news_multiple_coins_ok(session):
    session.add_all([NewsCoin(news_id=1, coin="SOL"), NewsCoin(news_id=1, coin="ARB")])
    session.commit()
    assert session.query(NewsCoin).count() == 2


def test_same_coin_across_news_ok(session):
    session.add_all([NewsCoin(news_id=1, coin="SOL"), NewsCoin(news_id=2, coin="SOL")])
    session.commit()
    assert session.query(NewsCoin).filter(NewsCoin.coin == "SOL").count() == 2
