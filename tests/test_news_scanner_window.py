"""Tests for NewsScanner 5-minute target window selection."""
import os
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scanners.base import NewsRecord
from scanners.news_scanner import NewsScanner


def test_target_scan_window_lags_one_closed_bucket():
    start, end = NewsScanner._target_scan_window(datetime(2026, 4, 26, 7, 28, 13), 5)
    assert start == datetime(2026, 4, 26, 7, 20, 0)
    assert end == datetime(2026, 4, 26, 7, 25, 0)


def test_target_scan_window_on_exact_boundary():
    start, end = NewsScanner._target_scan_window(datetime(2026, 4, 26, 7, 30, 0), 5)
    assert start == datetime(2026, 4, 26, 7, 25, 0)
    assert end == datetime(2026, 4, 26, 7, 30, 0)


def test_target_scan_window_matches_beijing_example():
    start, end = NewsScanner._target_scan_window(datetime(2026, 4, 26, 7, 49, 38), 5)
    assert start == datetime(2026, 4, 26, 7, 40, 0)
    assert end == datetime(2026, 4, 26, 7, 45, 0)


def test_news_scan_saves_with_skip_existing(monkeypatch):
    scanner = NewsScanner.__new__(NewsScanner)
    scan_time = datetime(2026, 4, 28, 12, 20, 10)
    record = NewsRecord(
        source="jin10",
        source_id="1",
        title="news",
        published_at=datetime(2026, 4, 28, 12, 16),
    )
    saved = []

    scanner.sources = [SimpleNamespace(name="jin10", fetch=lambda: [record])]
    scanner.scorer = SimpleNamespace(enabled=False)
    monkeypatch.setattr("scanners.news_scanner.datetime", SimpleNamespace(
        now=lambda tz=None: scan_time,
        min=datetime.min,
    ))

    def save_records(records, save_time, skip_existing=False):
        saved.append((records, save_time, skip_existing))
        return len(records)

    monkeypatch.setattr(scanner, "_save_records", save_records)

    records = scanner.scan()

    assert records == [record]
    assert saved == [([record], scan_time, True)]


def test_news_backfill_caps_window_to_72_hours(monkeypatch):
    scanner = NewsScanner.__new__(NewsScanner)
    end_time = datetime(2026, 4, 28, 12, 0)
    calls = []

    def fetch_backfill(start_ts, end_ts):
        calls.append((start_ts, end_ts))
        return [
            NewsRecord(
                source="jin10",
                source_id="1",
                title="news",
                published_at=end_ts - timedelta(minutes=1),
            )
        ]

    scanner.sources = [SimpleNamespace(name="jin10", fetch_backfill=fetch_backfill)]
    scanner.scorer = SimpleNamespace(enabled=False)
    monkeypatch.setattr(scanner, "_filter_existing_records", lambda records: records)
    monkeypatch.setattr(scanner, "_save_records", lambda records, scan_time, skip_existing=False: len(records))

    records = scanner.backfill_missing_history(max_hours=200, end_time=end_time)

    assert len(records) == 1
    assert calls == [(end_time - timedelta(hours=72), end_time)]


def test_news_backfill_filters_existing_before_scoring(monkeypatch):
    scanner = NewsScanner.__new__(NewsScanner)
    end_time = datetime(2026, 4, 28, 12, 0)
    kept = NewsRecord(
        source="bloomberg",
        source_id="new",
        title="new",
        published_at=end_time - timedelta(minutes=1),
    )
    duplicate = NewsRecord(
        source="bloomberg",
        source_id="old",
        title="old",
        published_at=end_time - timedelta(minutes=2),
    )
    scored = []

    def fetch_backfill(start_ts, end_ts):
        return [duplicate, kept]

    scanner.sources = [SimpleNamespace(name="bloomberg", fetch_backfill=fetch_backfill)]
    scanner.scorer = SimpleNamespace(
        enabled=True,
        enrich_batch=lambda records: scored.extend(records) or records,
    )
    monkeypatch.setattr(scanner, "_filter_existing_records", lambda records: [kept])
    monkeypatch.setattr(scanner, "_save_records", lambda records, scan_time, skip_existing=False: len(records))

    records = scanner.backfill_missing_history(max_hours=72, end_time=end_time, score_records=True)

    assert records == [kept]
    assert scored == [kept]


def test_news_backfill_skips_scoring_by_default(monkeypatch):
    scanner = NewsScanner.__new__(NewsScanner)
    end_time = datetime(2026, 4, 28, 12, 0)
    kept = NewsRecord(
        source="jin10",
        source_id="new",
        title="new",
        published_at=end_time - timedelta(minutes=1),
    )
    scored = []

    scanner.sources = [SimpleNamespace(name="jin10", fetch_backfill=lambda start_ts, end_ts: [kept])]
    scanner.scorer = SimpleNamespace(
        enabled=True,
        enrich_batch=lambda records: scored.extend(records) or records,
    )
    monkeypatch.setattr(scanner, "_filter_existing_records", lambda records: records)
    monkeypatch.setattr(scanner, "_save_records", lambda records, scan_time, skip_existing=False: len(records))

    records = scanner.backfill_missing_history(max_hours=72, end_time=end_time)

    assert records == [kept]
    assert scored == []


def test_news_backfill_range_uses_exact_window_and_skip_existing(monkeypatch):
    scanner = NewsScanner.__new__(NewsScanner)
    start_time = datetime(2026, 4, 28, 14, 15)
    end_time = datetime(2026, 4, 28, 14, 25)
    kept = NewsRecord(
        source="jin10",
        source_id="new",
        title="new",
        published_at=end_time - timedelta(minutes=1),
    )
    calls = []
    saved = []

    def fetch_backfill(start_ts, end_ts):
        calls.append((start_ts, end_ts))
        return [kept]

    def save_records(records, scan_time, skip_existing=False):
        saved.append((records, scan_time, skip_existing))
        return len(records)

    scanner.sources = [SimpleNamespace(name="jin10", fetch_backfill=fetch_backfill)]
    scanner.scorer = SimpleNamespace(enabled=False)
    monkeypatch.setattr(scanner, "_filter_existing_records", lambda records: records)
    monkeypatch.setattr(scanner, "_save_records", save_records)

    records = scanner.backfill_range(start_time, end_time)

    assert records == [kept]
    assert calls == [(start_time, end_time)]
    assert saved == [([kept], end_time, True)]
