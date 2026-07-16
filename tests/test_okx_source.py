"""Tests for OKX source fallback behavior（游标同步后唯一路径 = fetch_history 族）。"""
from datetime import datetime, timezone

import ccxt
import pytest

from scanners.sources import okx_source
from scanners.sources.okx_source import OkxPriceSource


def _ms(dt: datetime) -> int:
    return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)


def _candles(latest_close: float = 105.0, previous_close: float = 100.0) -> list[list[str]]:
    return [
        [str(_ms(datetime(2026, 1, 1, 0, 5))), "0", "0", "0", str(latest_close), "10", "0", "0", "1"],
        [str(_ms(datetime(2026, 1, 1, 0, 0))), "0", "0", "0", str(previous_close), "8", "0", "0", "1"],
    ]


# start=00:05 让首页 oldest_start(00:00) 触到 start_floor（start−5min），分页在第一页后即终止，
# 断言的调用序列不掺分页噪音。
_START = datetime(2026, 1, 1, 0, 5)
_END = datetime(2026, 1, 1, 0, 15)


def test_history_retries_swap_then_uses_spot(monkeypatch):
    monkeypatch.setattr(okx_source.time, "sleep", lambda *_: None)
    source = OkxPriceSource.__new__(OkxPriceSource)

    class Exchange:
        def __init__(self):
            self.calls: list[str] = []

        def publicGetMarketCandles(self, params):
            inst_id = params["instId"]
            self.calls.append(inst_id)
            if inst_id.endswith("-SWAP"):
                raise ccxt.RequestTimeout("temporary timeout")
            return {"data": _candles()}

    exchange = Exchange()

    records = source._fetch_history_one(exchange, "BTC", "BTCUSDT", _START, _END)

    assert exchange.calls == ["BTC-USDT-SWAP", "BTC-USDT-SWAP", "BTC-USDT"]
    assert records, "现货回退应产出记录"
    assert all(r.source == "okx_spot_5m" for r in records)
    assert records[-1].symbol == "BTC/USDT"
    assert records[-1].change_pct == pytest.approx(5.0)


def test_history_keeps_swap_when_retry_succeeds(monkeypatch):
    monkeypatch.setattr(okx_source.time, "sleep", lambda *_: None)
    source = OkxPriceSource.__new__(OkxPriceSource)

    class Exchange:
        def __init__(self):
            self.calls = 0

        def publicGetMarketCandles(self, params):
            self.calls += 1
            if self.calls == 1:
                raise ccxt.RequestTimeout("temporary timeout")
            return {"data": _candles()}

    exchange = Exchange()

    records = source._fetch_history_one(exchange, "BTC", "BTCUSDT", _START, _END)

    assert exchange.calls == 2
    assert records and all(r.source == "okx_swap_5m" for r in records)


def test_perp_history_uses_exact_inst_id_and_independent_metadata():
    source = OkxPriceSource.__new__(OkxPriceSource)

    class Exchange:
        def __init__(self):
            self.calls: list[str] = []

        def publicGetMarketCandles(self, params):
            self.calls.append(params["instId"])
            return {"data": _candles()}

    exchange = Exchange()
    records = source._fetch_perp_history_one(
        exchange, "纳指代理永续", "QQQ-USDT-SWAP", _START, _END
    )

    assert exchange.calls == ["QQQ-USDT-SWAP"]
    assert records
    assert {r.symbol for r in records} == {"QQQ-USDT-SWAP"}
    assert {r.name for r in records} == {"纳指代理永续"}
    assert {r.asset_class for r in records} == {"perp"}
    assert {r.source for r in records} == {"okx_swap_5m"}
