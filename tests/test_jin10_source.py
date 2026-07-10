"""Tests for Jin10 time handling."""
import os
import sys
from datetime import datetime
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scanners.base import NewsRecord
from scanners.sources.jin10_source import Jin10Source


def test_parse_beijing_time_to_utc_naive():
    result = Jin10Source._parse_beijing_time("2026-04-23 14:42:29")
    assert result == datetime(2026, 4, 23, 6, 42, 29)


def test_parse_beijing_time_invalid_returns_none():
    assert Jin10Source._parse_beijing_time("not-a-date") is None


def test_jin10_importance_is_flag_not_score():
    payload = {
        "data": [
            {
                "id": "important-1",
                "time": "2026-04-26 15:15:00",
                "important": 1,
                "data": {"title": "重要新闻", "content": "内容"},
            },
            {
                "id": "normal-1",
                "time": "2026-04-26 15:16:00",
                "important": 0,
                "data": {"title": "普通新闻", "content": "内容"},
            },
        ]
    }
    response = MagicMock()
    response.status_code = 200          # 新契约：非 200 → Jin10ApiError，mock 必须显式给 200
    response.json.return_value = payload

    with patch("scanners.sources.jin10_source.requests.get", return_value=response):
        records = Jin10Source().fetch()

    assert [r.importance for r in records] == [1, 0]


def test_fetch_handles_null_data_page():
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"data": None}

    with patch("scanners.sources.jin10_source.requests.get", return_value=response):
        assert Jin10Source().fetch() == []


def test_backfill_revisits_same_second_boundary():
    class StubJin10Source(Jin10Source):
        def __init__(self):
            self.cursors = []
            self.pages = [
                [
                    NewsRecord(
                        source="jin10",
                        source_id="a",
                        title="A",
                        published_at=datetime(2026, 1, 1, 10, 0, 1),
                    ),
                    NewsRecord(
                        source="jin10",
                        source_id="b",
                        title="B",
                        published_at=datetime(2026, 1, 1, 10, 0, 0),
                    ),
                ],
                [
                    NewsRecord(
                        source="jin10",
                        source_id="b",
                        title="B",
                        published_at=datetime(2026, 1, 1, 10, 0, 0),
                    ),
                    NewsRecord(
                        source="jin10",
                        source_id="c",
                        title="C",
                        published_at=datetime(2026, 1, 1, 10, 0, 0),
                    ),
                ],
                [],
            ]

        def fetch(self, max_time=None):
            self.cursors.append(max_time)
            return self.pages.pop(0)

    source = StubJin10Source()

    records = source.fetch_backfill(
        start_time=datetime(2026, 1, 1, 9, 59, 0),
        end_time=datetime(2026, 1, 1, 10, 2, 0),
    )

    assert [record.source_id for record in records] == ["a", "b", "c"]
    assert source.cursors[1] == datetime(2026, 1, 1, 10, 0, 0)
