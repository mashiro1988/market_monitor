"""Tests for price normalization logic used in cross-asset chart."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chart_utils import normalize_prices


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
