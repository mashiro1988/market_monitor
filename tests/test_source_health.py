from __future__ import annotations

import os
import sys
from datetime import datetime
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scanners.base import NewsRecord, PredictionRecord
from scanners.news_scanner import NewsScanner
from scanners.prediction_scanner import PredictionScanner
from scanners.price_scanner import PriceScanner
from services.scan_runtime import _source_status_payload


def test_price_fetch_status_distinguishes_empty_and_error():
    scanner = PriceScanner.__new__(PriceScanner)
    scanner.source_statuses = []

    empty_records = scanner._fetch_safe(SimpleNamespace(name="empty_price", fetch=lambda: []))

    def fail():
        raise RuntimeError("price down")

    failed_records = scanner._fetch_safe(SimpleNamespace(name="broken_price", fetch=fail))

    assert empty_records == []
    assert failed_records == []
    assert scanner.source_statuses[0].source == "empty_price"
    assert scanner.source_statuses[0].ok is True
    assert scanner.source_statuses[0].empty is True
    assert scanner.source_statuses[0].error is None
    assert scanner.source_statuses[1].source == "broken_price"
    assert scanner.source_statuses[1].ok is False
    assert scanner.source_statuses[1].empty is False
    assert "RuntimeError: price down" in scanner.source_statuses[1].error


def test_news_scan_records_source_health_for_empty_success_and_failure(monkeypatch):
    scanner = NewsScanner.__new__(NewsScanner)
    scan_time = datetime(2026, 4, 28, 12, 20, 10)

    def fail():
        raise ValueError("rss bad")

    scanner.sources = [
        SimpleNamespace(name="empty_news", fetch=lambda: []),
        SimpleNamespace(name="broken_news", fetch=fail),
    ]
    scanner.scorer = SimpleNamespace(enabled=False)
    monkeypatch.setattr("scanners.news_scanner.datetime", SimpleNamespace(
        now=lambda tz=None: scan_time,
        min=datetime.min,
    ))

    records = scanner.scan()

    assert records == []
    assert [(s.source, s.ok, s.empty, s.record_count) for s in scanner.source_statuses] == [
        ("empty_news", True, True, 0),
        ("broken_news", False, False, 0),
    ]
    assert "ValueError: rss bad" in scanner.source_statuses[1].error


def test_prediction_scan_records_source_health_for_non_empty_success(monkeypatch):
    scanner = PredictionScanner.__new__(PredictionScanner)
    record = PredictionRecord(
        market_id="m1",
        question="Will CPI rise?",
        outcome="Yes",
        probability=0.51,
    )
    scanner.sources = [SimpleNamespace(name="polymarket", fetch=lambda: [record])]
    monkeypatch.setattr(scanner, "_save_records", lambda records, scan_time: None)

    records = scanner.scan()

    assert records == [record]
    assert len(scanner.source_statuses) == 1
    status = scanner.source_statuses[0]
    assert status.source == "polymarket"
    assert status.ok is True
    assert status.empty is False
    assert status.record_count == 1
    assert status.error is None


def test_scan_runtime_serializes_source_health_payload():
    scanner = PriceScanner.__new__(PriceScanner)
    scanner.source_statuses = []
    scanner._record_source_status("empty_price", [], stage="scan")

    payload = _source_status_payload(scanner)

    assert payload == [{
        "source": "empty_price",
        "ok": True,
        "record_count": 0,
        "empty": True,
        "stage": "scan",
        "error": None,
    }]
