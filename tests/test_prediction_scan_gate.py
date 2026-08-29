# -*- coding: utf-8 -*-
"""小时门控(spec 2026-08-28 §2):基准取 DB 最新快照,重启不丢节拍、失败下轮自愈。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import config
from database import Base
from models.prediction import PredictionMarket
from scanners.prediction_scanner import prediction_scan_due
from services.time_utils import utc_now_naive


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _snapshot(s, minutes_ago: int):
    now = utc_now_naive()
    s.add(PredictionMarket(timestamp=now - timedelta(minutes=minutes_ago),
                           market_id="m1", question="q", outcome="Yes", probability=0.5))
    s.commit()


def test_due_on_empty_table(session, monkeypatch):
    monkeypatch.setitem(config.SCAN_INTERVALS, "prediction", 60)
    assert prediction_scan_due(session) is True


def test_not_due_when_fresh(session, monkeypatch):
    monkeypatch.setitem(config.SCAN_INTERVALS, "prediction", 60)
    _snapshot(session, minutes_ago=30)
    assert prediction_scan_due(session) is False


def test_due_when_interval_elapsed(session, monkeypatch):
    monkeypatch.setitem(config.SCAN_INTERVALS, "prediction", 60)
    _snapshot(session, minutes_ago=61)
    assert prediction_scan_due(session) is True
