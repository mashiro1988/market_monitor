# -*- coding: utf-8 -*-
"""北京日日汇总回算脚本：写入正确、幂等、dry-run 不落库。"""
import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models.behavior import BehaviorDailySummary, BehaviorSegment
from scripts.backfill_behavior_bj_daily import backfill

NOW = datetime(2026, 7, 30, 2, 0)        # 北京 2026-07-30 10:00


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    # 北京 07-28 与 07-29 各一个段
    for start in (datetime(2026, 7, 27, 18, 0), datetime(2026, 7, 28, 18, 0)):
        s.add(BehaviorSegment(
            symbol="BTC/USDT", start_dt=start, end_dt=start + timedelta(minutes=15),
            direction=1, tier_idx=1, tier_max=0.5, net_pct=0.6, amp_pct=0.7,
            key_ts=start + timedelta(minutes=5), classification="pure_resonance",
            class_version="v2", s_scores=json.dumps({}), news_ids=json.dumps([]),
        ))
    s.commit()
    yield s
    s.close()


def test_backfill_writes_bj_rows_for_completed_days(session):
    results = backfill(session, "BTC/USDT", days=3, commit=True, now=NOW)
    assert [r["bj_date"] for r in results] == ["2026-07-27", "2026-07-28", "2026-07-29"]
    assert {r["action"] for r in results} == {"write"}
    rows = session.query(BehaviorDailySummary).filter_by(date_basis="bj").all()
    assert len(rows) == 3
    by_date = {r.bucket_date: json.loads(r.counts) for r in rows}
    assert by_date["2026-07-28"]["0.5"] == {"up": 1, "down": 0}   # UTC 07-27 18:00 = 北京 07-28 02:00
    assert by_date["2026-07-29"]["0.5"] == {"up": 1, "down": 0}
    assert by_date["2026-07-27"] == {}                             # 那天没段


def test_backfill_never_touches_today(session):
    results = backfill(session, "BTC/USDT", days=3, commit=True, now=NOW)
    assert "2026-07-30" not in [r["bj_date"] for r in results]     # 今天还没走完，不写


def test_backfill_is_idempotent(session):
    backfill(session, "BTC/USDT", days=3, commit=True, now=NOW)
    again = backfill(session, "BTC/USDT", days=3, commit=True, now=NOW)
    assert {r["action"] for r in again} == {"skip"}
    assert session.query(BehaviorDailySummary).filter_by(date_basis="bj").count() == 3


def test_backfill_dry_run_writes_nothing(session):
    results = backfill(session, "BTC/USDT", days=3, commit=False, now=NOW)
    assert {r["action"] for r in results} == {"dry-run"}
    assert session.query(BehaviorDailySummary).count() == 0


def test_backfill_leaves_legacy_utc_rows_alone(session):
    session.add(BehaviorDailySummary(
        symbol="BTC/USDT", bucket_date="2026-07-28", date_basis="utc", day_type="weekday",
        counts=json.dumps({"0.3": {"up": 42, "down": 0}}), composition=json.dumps({}),
        down_net_sum=-1.0, computed_at=datetime(2026, 7, 29, 0, 5),
    ))
    session.commit()
    backfill(session, "BTC/USDT", days=3, commit=True, now=NOW)
    legacy = session.query(BehaviorDailySummary).filter_by(date_basis="utc").one()
    assert json.loads(legacy.counts)["0.3"]["up"] == 42            # 旧行原样
