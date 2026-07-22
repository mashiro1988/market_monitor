# -*- coding: utf-8 -*-
"""卡片 freshness 四态：live/stale/source_down/closed + 扫描报错直判。"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
from models.price import PriceSnapshot
import services.market_service as ms

NOW = datetime(2026, 7, 22, 6, 0, 0)


@pytest.fixture()
def session(monkeypatch):
    eng = create_engine("sqlite:///:memory:",
                        connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    monkeypatch.setattr(ms, "utc_now_naive", lambda: NOW)
    monkeypatch.setattr(ms, "_failed_price_scanner_names", lambda: set())
    yield s
    s.close()


def _snap(s, symbol, minutes_ago, source="yfinance", asset_class="futures"):
    s.add(PriceSnapshot(timestamp=NOW - timedelta(minutes=minutes_ago),
                        asset_class=asset_class, symbol=symbol, name=symbol,
                        price=100.0, source=source))
    s.commit()


def _item(resp, symbol):
    return next(i for i in resp.items if i.symbol == symbol)


def test_live_stale_down_by_lag(session, monkeypatch):
    monkeypatch.setattr(ms.market_sessions, "is_open", lambda sym, now: True)
    _snap(session, "ES=F", 5)      # ≤15 → live
    _snap(session, "NQ=F", 30)     # (15,60] → stale
    _snap(session, "GC=F", 90)     # >60 → source_down
    resp = ms.get_latest_prices(session)
    assert _item(resp, "ES=F").freshness == "live"
    nq = _item(resp, "NQ=F")
    assert nq.freshness == "stale" and nq.stale_minutes == 30
    gc = _item(resp, "GC=F")
    assert gc.freshness == "source_down" and gc.stale_minutes == 90


def test_closed_market_is_calm(session, monkeypatch):
    monkeypatch.setattr(ms.market_sessions, "is_open", lambda sym, now: False)
    _snap(session, "^GSPC", 600, asset_class="stock_index")
    resp = ms.get_latest_prices(session)
    item = _item(resp, "^GSPC")
    assert item.freshness == "closed" and item.stale_minutes is None


def test_scanner_error_forces_down_even_if_fresh(session, monkeypatch):
    monkeypatch.setattr(ms.market_sessions, "is_open", lambda sym, now: True)
    monkeypatch.setattr(ms, "_failed_price_scanner_names", lambda: {"yfinance"})
    _snap(session, "ES=F", 5)
    resp = ms.get_latest_prices(session)
    assert _item(resp, "ES=F").freshness == "source_down"


def test_okx_snapshot_maps_to_okx_scanner_name(session, monkeypatch):
    monkeypatch.setattr(ms.market_sessions, "is_open", lambda sym, now: True)
    monkeypatch.setattr(ms, "_failed_price_scanner_names", lambda: {"okx"})
    _snap(session, "BTC/USDT", 3, source="okx_swap_5m", asset_class="crypto")
    resp = ms.get_latest_prices(session)
    assert _item(resp, "BTC/USDT").freshness == "source_down"
