"""Tests for Jin10 time handling."""
import os
import sys
from datetime import datetime
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
    response.json.return_value = payload

    with patch("scanners.sources.jin10_source.requests.get", return_value=response):
        records = Jin10Source().fetch()

    assert [r.importance for r in records] == [1, 0]
