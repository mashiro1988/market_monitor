"""Tests for scheduler scan window helpers."""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run import recent_closed_interval_window


def test_recent_closed_interval_window_matches_2225_example():
    start, end = recent_closed_interval_window(5, 2, datetime(2026, 4, 29, 14, 25, 10))

    assert start == datetime(2026, 4, 29, 14, 15)
    assert end == datetime(2026, 4, 29, 14, 25)


def test_recent_closed_interval_window_matches_2220_example():
    start, end = recent_closed_interval_window(5, 2, datetime(2026, 4, 29, 14, 20, 10))

    assert start == datetime(2026, 4, 29, 14, 10)
    assert end == datetime(2026, 4, 29, 14, 20)


def test_recent_closed_interval_window_crosses_midnight():
    start, end = recent_closed_interval_window(5, 2, datetime(2026, 4, 29, 0, 2, 10))

    assert start == datetime(2026, 4, 28, 23, 50)
    assert end == datetime(2026, 4, 29, 0, 0)
