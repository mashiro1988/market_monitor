# -*- coding: utf-8 -*-
"""行为引擎模型（price-behavior-engine-plan Task 3）：roundtrip + 唯一约束 + PIT 追加语义。"""
import json
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from database import Base
from models.behavior import BehaviorDailySummary, BehaviorSegment


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _seg(**kw):
    base = dict(
        symbol="BTC/USDT",
        start_dt=datetime(2026, 7, 8, 13, 25),
        end_dt=datetime(2026, 7, 8, 13, 40),
        direction=1,
        tier_idx=2,
        tier_max=0.8,
        net_pct=0.92,
        amp_pct=1.04,
        key_ts=datetime(2026, 7, 8, 13, 30),
        classification="macro_news",
        class_version="v1",
        s_scores=json.dumps({"NQ=F": {"s": 0.77, "ess": 4.3, "coverage": 1.0}}),
        news_ids=json.dumps([101, 102]),
    )
    base.update(kw)
    return BehaviorSegment(**base)


def test_segment_roundtrip(session):
    session.add(_seg())
    session.commit()
    row = session.query(BehaviorSegment).one()
    assert row.classification == "macro_news"
    assert json.loads(row.s_scores)["NQ=F"]["s"] == 0.77
    assert json.loads(row.news_ids) == [101, 102]
    assert row.created_at is not None


def test_segment_unique_span(session):
    session.add(_seg())
    session.commit()
    session.add(_seg(classification="sentiment"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_daily_summary_pit_append(session):
    mk = lambda at: BehaviorDailySummary(
        symbol="BTC/USDT", utc_date="2026-07-08", day_type="weekday",
        counts=json.dumps({"0.3": {"up": 8, "down": 11}}),
        composition=json.dumps({"sentiment": 3}),
        down_net_sum=-3.87, computed_at=at,
    )
    session.add(mk(datetime(2026, 7, 9, 0, 5)))
    session.commit()
    # PIT：同日重算 = 追加新行，不覆盖
    session.add(mk(datetime(2026, 7, 9, 6, 5)))
    session.commit()
    rows = (session.query(BehaviorDailySummary)
            .filter_by(symbol="BTC/USDT", utc_date="2026-07-08")
            .order_by(BehaviorDailySummary.computed_at.desc()).all())
    assert len(rows) == 2
    assert rows[0].computed_at == datetime(2026, 7, 9, 6, 5)   # 读取取最新
