# -*- coding: utf-8 -*-
"""标注窗口派生信号（annotation-refinements Part B）：首个触发段 / 窗口前净变动。
纯 compute-on-read，从 price_snapshots 的 5min 收盘价算，喂给 auto-annotate reasoner 判 driver。"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models.price import PriceSnapshot
from services import window_signals

T0 = datetime(2026, 6, 20, 12, 0)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _series(session, symbol, start, prices, step=5):
    for i, p in enumerate(prices):
        session.add(PriceSnapshot(timestamp=start + timedelta(minutes=step * i),
                                  asset_class="crypto", symbol=symbol, name=symbol, price=p, source="t"))
    session.commit()


def _from_returns(session, symbol, start, base, returns, step=5):
    prices = [base]
    for r in returns:
        prices.append(prices[-1] * (1 + r))
    _series(session, symbol, start, prices, step)


def test_first_trigger_skips_flat_lead(session):
    # 0-10 平、10-15 猛跌 -0.7%、15-25 平 → 触发段 = 10→15 那根（跳过前面平的）
    _series(session, "X", T0, [100, 100, 100, 99.3, 99.3, 99.3])
    seg = window_signals.first_trigger_segment(session, "X", T0, T0 + timedelta(minutes=25))
    assert seg is not None
    assert seg["start"] == T0 + timedelta(minutes=10)
    assert seg["pct"] < 0


def test_first_trigger_no_move_none(session):
    _series(session, "X", T0, [100, 100, 100, 100])
    assert window_signals.first_trigger_segment(session, "X", T0, T0 + timedelta(minutes=15)) is None


def test_pre_window_move(session):
    _series(session, "X", T0, [100, 101, 102])                   # T0, +5, +10：+2%
    mv = window_signals.pre_window_move(session, "X", T0 + timedelta(minutes=10), minutes=10)
    assert mv is not None and mv == pytest.approx(2.0, abs=0.01)
