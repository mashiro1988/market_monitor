# -*- coding: utf-8 -*-
"""游标同步（2026-07-14 重构）：窗口公式、幂等写入返回、scan 单路径。"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
from models.price import PriceSnapshot
from scanners.price_scanner import sync_window_start, _latest_by_symbol

NOW = datetime(2026, 7, 14, 12, 0, 0)


@pytest.fixture()
def make_session():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)


# ---------- 窗口公式三态 + 种子 ----------

def test_normal_floor_is_24h():
    latest = {"A": NOW - timedelta(minutes=5), "B": NOW - timedelta(minutes=10)}
    assert sync_window_start(latest, NOW, cap_hours=168) == NOW - timedelta(hours=24)


def test_downtime_stretches_to_cursor_minus_30min():
    latest = {"A": NOW - timedelta(hours=70), "B": NOW - timedelta(hours=69)}
    assert sync_window_start(latest, NOW, cap_hours=168) == NOW - timedelta(hours=70, minutes=30)


def test_cap_bounds_the_window():
    latest = {"A": NOW - timedelta(days=10)}
    assert sync_window_start(latest, NOW, cap_hours=72) == NOW - timedelta(hours=72)


def test_empty_symbol_seeds_full_cap():
    latest = {"A": NOW - timedelta(minutes=5), "B": None}
    assert sync_window_start(latest, NOW, cap_hours=72) == NOW - timedelta(hours=72)


# ---------- 幂等写入返回 ----------

from scanners.base import PriceRecord
import scanners.price_scanner as ps_module
from scanners.price_scanner import PriceScanner


def _rec(ts, symbol="NQ=F", price=100.0, source="yfinance"):
    return PriceRecord(asset_class="futures", symbol=symbol, name="纳指期货",
                       price=price, source=source, timestamp=ts)


def test_save_records_returns_only_inserted(make_session, monkeypatch):
    monkeypatch.setattr(ps_module, "get_session", make_session)
    scanner = PriceScanner()
    t1, t2 = NOW - timedelta(minutes=10), NOW - timedelta(minutes=5)
    first = scanner._save_records([_rec(t1)], NOW)
    assert [r.timestamp for r in first] == [t1]
    second = scanner._save_records([_rec(t1), _rec(t2)], NOW)   # t1 已存在
    assert [r.timestamp for r in second] == [t2]
    third = scanner._save_records([_rec(t1), _rec(t2)], NOW)    # 全部已存在 → 幂等
    assert third == []


# ---------- 游标查询 ----------

def test_latest_by_symbol_reads_max_ts_and_none_for_missing(make_session):
    s = make_session()
    for m in (10, 5):
        s.add(PriceSnapshot(timestamp=NOW - timedelta(minutes=m), asset_class="crypto",
                            symbol="BTC/USDT", name="BTC", price=100.0, source="okx_swap_5m"))
    s.commit()
    latest = _latest_by_symbol(s, ["BTC/USDT", "ETH/USDT"])
    assert latest["BTC/USDT"] == NOW - timedelta(minutes=5)
    assert latest["ETH/USDT"] is None
    s.close()
