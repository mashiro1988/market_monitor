"""get_history 窗口起点锚定净值：隔夜跳空不被首点基准吃掉。"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import config
from database import Base
import models  # noqa: F401  注册模型到 Base.metadata
from models.price import PriceSnapshot
from services import market_service
from services.time_utils import utc_now_naive


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    try:
        yield s
    finally:
        s.close()


def _add(s, symbol, ts, price, asset_class="asian_index"):
    s.add(PriceSnapshot(timestamp=ts, asset_class=asset_class, symbol=symbol,
                        name=symbol, price=price, source="test"))


def test_baseline_anchored_at_window_start_preserves_gap(session):
    now = utc_now_naive()
    start = now - timedelta(hours=4)
    _add(session, "^KS11", start - timedelta(hours=1), 100.0)        # 昨收=基准
    _add(session, "^KS11", start + timedelta(minutes=5), 92.0)       # 今开已跌 8%
    _add(session, "^KS11", start + timedelta(minutes=10), 93.0)
    session.commit()
    resp = market_service.get_history(session, symbols=["^KS11"], hours=4)
    pts = resp.series[0].points
    assert pts[0].normalized_pct == pytest.approx(-8.0, abs=0.05)    # 相对昨收，非 0
    assert pts[1].normalized_pct == pytest.approx(-7.0, abs=0.05)


def test_falls_back_to_first_point_without_pre_window_data(session):
    now = utc_now_naive()
    start = now - timedelta(hours=4)
    _add(session, "BTC/USDT", start + timedelta(minutes=5), 50000.0, asset_class="crypto")
    _add(session, "BTC/USDT", start + timedelta(minutes=10), 51000.0, asset_class="crypto")
    session.commit()
    resp = market_service.get_history(session, symbols=["BTC/USDT"], hours=4)
    pts = resp.series[0].points
    assert pts[0].normalized_pct == 0.0                              # 无前置数据 → 回退首点
    assert pts[1].normalized_pct == pytest.approx(2.0, abs=0.05)


def test_latest_prices_filters_unconfigured_crypto(session, monkeypatch):
    monkeypatch.setitem(config.PRICE_SOURCES, "crypto", {"BTC": "BTCUSDT", "ETH": "ETHUSDT"})
    now = utc_now_naive()
    _add(session, "BTC/USDT", now - timedelta(minutes=5), 50000.0, asset_class="crypto")
    _add(session, "ETH/USDT", now - timedelta(minutes=5), 3000.0, asset_class="crypto")
    _add(session, "DOGE/USDT", now - timedelta(minutes=5), 0.1, asset_class="crypto")
    session.commit()
    resp = market_service.get_latest_prices(session)
    symbols = {item.symbol for item in resp.items}
    assert {"BTC/USDT", "ETH/USDT"} <= symbols
    assert "DOGE/USDT" not in symbols                                # 未配置的 alt 被过滤掉


def test_latest_prices_includes_currency_class(session):
    now = utc_now_naive()
    _add(session, "DX-Y.NYB", now - timedelta(minutes=5), 105.0, asset_class="currency")
    session.commit()
    resp = market_service.get_latest_prices(session)
    item = next(i for i in resp.items if i.symbol == "DX-Y.NYB")
    assert item.asset_class == "currency"


def test_symbols_options_match_overview_crypto_filter(session, monkeypatch):
    """跨资产走势的品种选项必须与概览同口径：白名单外的加密（已停采 alt）不出现。"""
    monkeypatch.setitem(config.PRICE_SOURCES, "crypto", {"BTC": "BTCUSDT", "ETH": "ETHUSDT"})
    now = utc_now_naive()
    _add(session, "BTC/USDT", now - timedelta(minutes=5), 100000.0, asset_class="crypto")
    _add(session, "DOGE/USDT", now - timedelta(minutes=5), 0.1, asset_class="crypto")   # 6/9 已停采
    session.commit()
    values = {s.symbol for s in market_service.get_symbols(session)}
    assert "BTC/USDT" in values
    assert "DOGE/USDT" not in values


def test_symbols_renamed_symbol_appears_once_with_latest_meta(session):
    """同一 symbol 历史上改过名/改过资产类（换源）时，选项只出现一次且用最新元数据。"""
    now = utc_now_naive()
    s1 = PriceSnapshot(timestamp=now - timedelta(days=2), asset_class="bond",
                       symbol="US_10Y", name="旧名", price=4.5, source="eastmoney")
    s2 = PriceSnapshot(timestamp=now - timedelta(minutes=5), asset_class="bond",
                       symbol="US_10Y", name="美国10年期国债收益率", price=4.6, source="cnbc")
    session.add_all([s1, s2])
    session.commit()
    matches = [s for s in market_service.get_symbols(session) if s.symbol == "US_10Y"]
    assert len(matches) == 1
    assert matches[0].name == "美国10年期国债收益率"
