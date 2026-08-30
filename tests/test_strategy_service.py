# -*- coding: utf-8 -*-
"""strategy_service：拉取解析、CRUD、每日检查状态机、推送去重。全程内存 SQLite + 假蜡烛。"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import services.strategy_service as svc
from database import Base
from models.strategy import StrategyEvent, StrategyPosition, StrategySettings, StrategySymbolState
from services.strategy_engine import DailyCandle


@pytest.fixture()
def db(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(svc, "SessionLocal", Session)
    return Session()


def test_parse_okx_candles_keeps_confirmed_ascending():
    raw = {"code": "0", "data": [
        ["1787875200000", "0.73", "0.75", "0.70", "0.716", "1", "1", "1", "0"],   # 未确认，丢弃
        ["1787788800000", "0.752", "0.782", "0.718", "0.7323", "1", "1", "1", "1"],
        ["1787702400000", "0.739", "0.775", "0.710", "0.7518", "1", "1", "1", "1"],
    ]}
    candles = svc._parse_okx_candles(raw)
    assert [c.close for c in candles] == [0.7518, 0.7323]          # 升序 + 只留 confirm=1
    assert candles[0].date == datetime(2026, 8, 26)                 # 1787702400000 = 2026-08-26 00:00 UTC


def test_get_settings_creates_singleton(db):
    s1 = svc.get_settings(db)
    s2 = svc.get_settings(db)
    assert s1.id == s2.id and s1.capital == 13915.0 and s1.x_soft == 4
