"""Tests for price normalization logic used in cross-asset chart."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime

from chart_utils import (
    format_beijing_time,
    normalize_prices,
    today_beijing_anchor_utc,
    to_beijing_time,
)


def test_normalize_base_is_zero():
    """First point is always 0%."""
    prices = [100.0, 110.0, 90.0]
    result = normalize_prices(prices)
    assert result[0] == 0.0


def test_normalize_calculates_pct():
    """Subsequent points are relative % change from base."""
    prices = [100.0, 110.0, 90.0]
    result = normalize_prices(prices)
    assert abs(result[1] - 10.0) < 0.001
    assert abs(result[2] - (-10.0)) < 0.001


def test_normalize_empty_returns_empty():
    assert normalize_prices([]) == []


def test_normalize_single_returns_zero():
    assert normalize_prices([500.0]) == [0.0]


def test_normalize_zero_base_returns_zeros():
    """If first price is 0, avoid division by zero, return all zeros."""
    result = normalize_prices([0.0, 100.0, 200.0])
    assert result == [0.0, 0.0, 0.0]


def test_to_beijing_time_converts_utc_naive():
    result = to_beijing_time(datetime(2026, 4, 23, 0, 30, 0))
    assert result == datetime(2026, 4, 23, 8, 30, 0)


def test_format_beijing_time():
    result = format_beijing_time(datetime(2026, 4, 23, 0, 30, 0), "%m-%d %H:%M")
    assert result == "04-23 08:30"


def test_today_beijing_anchor_utc():
    result = today_beijing_anchor_utc(8, 0, now_utc=datetime(2026, 4, 23, 1, 30, 0))
    assert result == datetime(2026, 4, 23, 0, 0, 0)


def test_today_beijing_anchor_utc_can_be_in_future():
    result = today_beijing_anchor_utc(21, 30, now_utc=datetime(2026, 4, 23, 6, 0, 0))
    assert result == datetime(2026, 4, 23, 13, 30, 0)


def test_normalize_with_explicit_base():
    # base 来自窗口起点（昨收 100），首点已跌到 92 → −8%，不被吃掉
    result = normalize_prices([92.0, 93.0], base=100.0)
    assert abs(result[0] - (-8.0)) < 0.001
    assert abs(result[1] - (-7.0)) < 0.001


def test_normalize_base_none_matches_legacy():
    assert normalize_prices([100.0, 110.0], base=None) == normalize_prices([100.0, 110.0])


def test_normalize_explicit_zero_base_falls_back_to_first_point():
    result = normalize_prices([100.0, 110.0], base=0)
    assert result[0] == 0.0
    assert abs(result[1] - 10.0) < 0.001
