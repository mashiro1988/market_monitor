"""Tests for Polymarket market filtering logic."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scanners.sources.polymarket.filters import PolymarketMarketFilter
from scanners.sources.polymarket.source import PolymarketSource


def _make_market(question: str, volume: float) -> dict:
    return {
        "conditionId": "abc123",
        "question": question,
        "outcomePrices": '["0.6", "0.4"]',
        "outcomes": '["Yes", "No"]',
        "volume": str(volume),
    }


class FakePolymarketClient:
    def __init__(self):
        self.searched = []

    def get_markets_by_slug(self, slug: str) -> list[dict]:
        return [_make_market(f"Will the Fed cut rates via {slug}?", 500000)]

    def search_markets(self, tag: str, limit: int = 10) -> list[dict]:
        self.searched.append((tag, limit))
        return [
            _make_market("Will the Fed cut rates in June 2026?", 500000),
            _make_market("Will Bitcoin hit a new all-time high in 2026?", 500000),
        ]

    def health_check(self) -> bool:
        return True


def test_low_volume_market_filtered():
    market_filter = PolymarketMarketFilter(min_volume=100_000)
    market = _make_market("Will LA FC beat Orlando City SC?", 500)
    assert market_filter.is_noise_market(market) is True


def test_sports_keyword_filtered():
    market_filter = PolymarketMarketFilter(min_volume=100_000)
    market = _make_market("Total Kills Over/Under 19.5 in Game 1?", 500000)
    assert market_filter.is_noise_market(market) is True


def test_macro_market_passes():
    market_filter = PolymarketMarketFilter(min_volume=100_000)
    market = _make_market("Will the Fed cut rates in June 2026?", 500000)
    assert market_filter.is_noise_market(market) is False


def test_weather_market_filtered():
    market_filter = PolymarketMarketFilter(min_volume=100_000)
    market = _make_market("Will the temperature in Denver exceed 72°F on April 7?", 300000)
    assert market_filter.is_noise_market(market) is True


def test_crypto_market_filtered():
    market_filter = PolymarketMarketFilter(min_volume=100_000)
    market = _make_market("Will Bitcoin hit a new all-time high in 2026?", 500000)
    assert market_filter.is_noise_market(market) is True


def test_unrelated_high_volume_market_filtered():
    market_filter = PolymarketMarketFilter(min_volume=100_000)
    market = _make_market("Who will win the next presidential election?", 500000)
    assert market_filter.is_noise_market(market) is True


def test_source_expands_watchlist_slugs():
    source = PolymarketSource(client=FakePolymarketClient())
    source.tracked_slugs = ["fed-decision-in-june-825"]
    source.tracked_tags = []

    records = source.fetch()

    assert len(records) == 2
    assert {r.outcome for r in records} == {"Yes", "No"}
    assert all("fed-decision-in-june-825" in r.question for r in records)


def test_source_discovers_top_volume_tag_candidates_with_filter():
    client = FakePolymarketClient()
    source = PolymarketSource(client=client)
    source.tracked_slugs = []
    source.tracked_tags = ["fed"]
    source.discovery_limit = 5

    records = source.fetch()

    assert client.searched == [("fed", 5)]
    assert len(records) == 2
    assert all("Bitcoin" not in r.question for r in records)
