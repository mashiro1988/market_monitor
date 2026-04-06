"""Tests for Polymarket market filtering logic."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scanners.sources.polymarket_source import PolymarketSource


def _make_market(question: str, volume: float) -> dict:
    return {
        "conditionId": "abc123",
        "question": question,
        "outcomePrices": '["0.6", "0.4"]',
        "outcomes": '["Yes", "No"]',
        "volume": str(volume),
    }


def test_low_volume_market_filtered():
    source = PolymarketSource.__new__(PolymarketSource)
    market = _make_market("Will LA FC beat Orlando City SC?", 500)
    assert source._is_noise_market(market) is True


def test_sports_keyword_filtered():
    source = PolymarketSource.__new__(PolymarketSource)
    market = _make_market("Total Kills Over/Under 19.5 in Game 1?", 50000)
    assert source._is_noise_market(market) is True


def test_macro_market_passes():
    source = PolymarketSource.__new__(PolymarketSource)
    market = _make_market("Will the Fed cut rates in June 2026?", 500000)
    assert source._is_noise_market(market) is False


def test_weather_market_filtered():
    source = PolymarketSource.__new__(PolymarketSource)
    market = _make_market("Will the temperature in Denver exceed 72°F on April 7?", 30000)
    assert source._is_noise_market(market) is True
