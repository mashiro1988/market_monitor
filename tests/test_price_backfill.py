"""Tests for startup price history backfill."""
import os
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scanners.base import PriceRecord
from scanners.price_scanner import PriceScanner


def test_price_backfill_caps_window_to_72_hours(monkeypatch):
    scanner = PriceScanner.__new__(PriceScanner)
    end_time = datetime(2026, 4, 28, 12, 0)
    calls = []

    def fake_fetch_history(source_name):
        def _fetch(start_ts, end_ts):
            calls.append((source_name, start_ts, end_ts))
            return [
                PriceRecord(
                    asset_class="crypto",
                    symbol=f"{source_name}/USDT",
                    name=source_name,
                    price=1.0,
                    timestamp=end_ts,
                )
            ]
        return _fetch

    scanner.yfinance = SimpleNamespace(fetch_history=fake_fetch_history("yfinance"))
    scanner.okx = SimpleNamespace(fetch_history=fake_fetch_history("okx"))
    monkeypatch.setattr(scanner, "_save_records", lambda records, scan_time: len(records))

    records = scanner.backfill_missing_history(max_hours=200, end_time=end_time)

    assert len(records) == 2
    assert calls == [
        ("yfinance", end_time - timedelta(hours=72), end_time),
        ("okx", end_time - timedelta(hours=72), end_time),
    ]


def test_price_backfill_zero_hours_disables_sources(monkeypatch):
    scanner = PriceScanner.__new__(PriceScanner)
    scanner.yfinance = SimpleNamespace(fetch_history=lambda start, end: (_ for _ in ()).throw(AssertionError()))
    scanner.okx = SimpleNamespace(fetch_history=lambda start, end: (_ for _ in ()).throw(AssertionError()))
    monkeypatch.setattr(scanner, "_save_records", lambda records, scan_time: len(records))

    assert scanner.backfill_missing_history(max_hours=0, end_time=datetime(2026, 4, 28, 12, 0)) == []


def test_price_backfill_range_uses_exact_window(monkeypatch):
    scanner = PriceScanner.__new__(PriceScanner)
    start_time = datetime(2026, 4, 28, 14, 15)
    end_time = datetime(2026, 4, 28, 14, 25)
    calls = []

    def fake_fetch_history(source_name):
        def _fetch(start_ts, end_ts):
            calls.append((source_name, start_ts, end_ts))
            return [
                PriceRecord(
                    asset_class="crypto",
                    symbol=f"{source_name}/USDT",
                    name=source_name,
                    price=1.0,
                    timestamp=end_ts,
                )
            ]
        return _fetch

    scanner.yfinance = SimpleNamespace(fetch_history=fake_fetch_history("yfinance"))
    scanner.okx = SimpleNamespace(fetch_history=fake_fetch_history("okx"))
    monkeypatch.setattr(scanner, "_save_records", lambda records, scan_time: len(records))

    records = scanner.backfill_range(start_time, end_time)

    assert len(records) == 2
    assert calls == [
        ("yfinance", start_time, end_time),
        ("okx", start_time, end_time),
    ]
