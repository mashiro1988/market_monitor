"""CNBC 债券源解析与利差计算（mock HTTP）。"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import config
from scanners.sources.cnbc_bond_source import CnbcBondQuoteSource


CNBC_BONDS = {
    "US_10Y": {"source": "cnbc", "cnbc": "US10Y", "name": "美国10年期国债收益率"},
    "US_2Y": {"source": "cnbc", "cnbc": "US2Y", "name": "美国2年期国债收益率"},
    "JP_10Y": {"source": "cnbc", "cnbc": "JP10Y", "name": "日本10年期国债"},
    "JP_2Y": {"source": "cnbc", "cnbc": "JP2Y", "name": "日本2年期国债"},
}


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _fake_get(payload):
    def _get(*args, **kwargs):
        return _FakeResp(payload)
    return _get


def test_cnbc_parses_yields_and_spread(monkeypatch):
    monkeypatch.setitem(config.PRICE_SOURCES, "bonds", CNBC_BONDS)
    payload = {"FormattedQuoteResult": {"FormattedQuote": [
        {"symbol": "US10Y", "last": "4.554%", "change": "+0.004", "code": 0},
        {"symbol": "US2Y", "last": "3.954%", "change": "-0.010", "code": 0},
        {"symbol": "JP10Y", "last": "1.500%", "change": "+0.002", "code": 0},
        {"symbol": "JP2Y", "last": "0.800%", "change": "0", "code": 0},
    ]}}
    monkeypatch.setattr("scanners.sources.cnbc_bond_source.requests.get", _fake_get(payload))

    records = CnbcBondQuoteSource().fetch()
    by = {r.symbol: r for r in records}

    assert by["US_10Y"].price == pytest.approx(4.554)
    assert by["US_2Y"].price == pytest.approx(3.954)
    assert by["JP_10Y"].price == pytest.approx(1.500)
    assert by["US_10Y"].asset_class == "bond"
    assert by["US_10Y"].timestamp is None          # 留空 → scanner 用 scan_time（保证连续）
    # 利差 = 10Y − 2Y，客户端相减
    assert by["US_SPREAD"].price == pytest.approx(4.554 - 3.954)
    assert by["JP_SPREAD"].price == pytest.approx(1.500 - 0.800)


def test_cnbc_skips_invalid_code(monkeypatch):
    monkeypatch.setitem(config.PRICE_SOURCES, "bonds", {"US_10Y": CNBC_BONDS["US_10Y"]})
    payload = {"FormattedQuoteResult": {"FormattedQuote": [{"symbol": "US10Y", "code": 1}]}}
    monkeypatch.setattr("scanners.sources.cnbc_bond_source.requests.get", _fake_get(payload))

    assert CnbcBondQuoteSource().fetch() == []


def test_cnbc_request_failure_is_safe(monkeypatch):
    monkeypatch.setitem(config.PRICE_SOURCES, "bonds", {"US_10Y": CNBC_BONDS["US_10Y"]})

    def _boom(*args, **kwargs):
        raise RuntimeError("network down")
    monkeypatch.setattr("scanners.sources.cnbc_bond_source.requests.get", _boom)

    assert CnbcBondQuoteSource().fetch() == []
