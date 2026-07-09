"""Tests for OKX source fallback behavior."""
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


def test_current_fetch_retries_swap_then_uses_spot(monkeypatch):
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

    record = source._fetch_one(exchange, "BTC", "BTCUSDT")

    assert record is not None
    assert exchange.calls == ["BTC-USDT-SWAP", "BTC-USDT-SWAP", "BTC-USDT"]
    assert record.source == "okx_spot_5m"
    assert record.symbol == "BTC/USDT"
    assert record.change_pct == pytest.approx(5.0)


def test_current_fetch_keeps_swap_when_retry_succeeds(monkeypatch):
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

    record = source._fetch_one(exchange, "BTC", "BTCUSDT")

    assert record is not None
    assert exchange.calls == 2
    assert record.source == "okx_swap_5m"
