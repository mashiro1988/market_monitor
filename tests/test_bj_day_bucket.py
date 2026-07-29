# -*- coding: utf-8 -*-
"""北京日界（2026-07-29 口径切换）：换算函数 + 跨界归属 + 口径隔离 + 日报目标日。"""
import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models.behavior import BehaviorSegment
from services import behavior_classifier as bc
from services.time_utils import bj_date_of, bj_day_bounds


def test_bj_date_of_maps_utc_instant_to_beijing_day():
    # 北京 = UTC+8：UTC 15:59 还是当天，UTC 16:00 就翻到次日
    assert bj_date_of(datetime(2026, 7, 28, 15, 59)) == "2026-07-28"
    assert bj_date_of(datetime(2026, 7, 28, 16, 0)) == "2026-07-29"
    assert bj_date_of(datetime(2026, 7, 28, 0, 0)) == "2026-07-28"
    assert bj_date_of(None) is None


def test_bj_day_bounds_spans_utc_16_to_16():
    start, end = bj_day_bounds("2026-07-29")
    assert start == datetime(2026, 7, 28, 16, 0)
    assert end == datetime(2026, 7, 29, 16, 0)


def test_bounds_and_date_of_are_consistent():
    start, end = bj_day_bounds("2026-07-29")
    assert bj_date_of(start) == "2026-07-29"                      # 左闭
    assert bj_date_of(end - timedelta(seconds=1)) == "2026-07-29"
    assert bj_date_of(end) == "2026-07-30"                        # 右开


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _seg(start: datetime, direction: int = 1, tier_idx: int = 1, tier_max: float = 0.5,
         net_pct: float = 0.6, classification: str = "pure_resonance") -> BehaviorSegment:
    return BehaviorSegment(
        symbol="BTC/USDT", start_dt=start, end_dt=start + timedelta(minutes=15),
        direction=direction, tier_idx=tier_idx, tier_max=tier_max,
        net_pct=net_pct, amp_pct=abs(net_pct) + 0.1, key_ts=start + timedelta(minutes=5),
        classification=classification, class_version="v2",
        s_scores=json.dumps({"NQ=F": {"s": 0.7, "ess": 4.0, "coverage": 1.0}}),
        news_ids=json.dumps([]),
    )


def test_segments_split_across_utc_16_land_in_adjacent_beijing_days(session):
    """UTC 15:59 与 16:01 的两个段必须落进相邻两个北京日。"""
    session.add(_seg(datetime(2026, 7, 28, 15, 59)))       # 北京 07-28 23:59
    session.add(_seg(datetime(2026, 7, 28, 16, 1)))        # 北京 07-29 00:01
    session.commit()

    counts_28, _, _ = bc.aggregate_day(session, "BTC/USDT", "2026-07-28")
    counts_29, _, _ = bc.aggregate_day(session, "BTC/USDT", "2026-07-29")
    assert counts_28["0.5"] == {"up": 1, "down": 0}
    assert counts_29["0.5"] == {"up": 1, "down": 0}


def test_day_direction_extras_uses_beijing_bounds(session):
    session.add(_seg(datetime(2026, 7, 28, 15, 59), net_pct=0.6))
    session.add(_seg(datetime(2026, 7, 28, 16, 1), net_pct=0.9))
    session.commit()

    assert bc.day_direction_extras(session, "BTC/USDT", "2026-07-28")["up_net_sum"] == 0.6
    assert bc.day_direction_extras(session, "BTC/USDT", "2026-07-29")["up_net_sum"] == 0.9


def test_day_type_follows_beijing_date(session):
    # 2026-07-25 是周六；UTC 日口径下 07-24 16:30 属于 UTC 周五，北京口径属于周六
    assert bc.day_type_of("2026-07-25") == "weekend"
    assert bc.day_type_of("2026-07-24") == "weekday"
