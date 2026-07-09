"""Tests for Polymarket market filtering logic."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scanners.sources.polymarket.filters import PolymarketMarketFilter
from scanners.sources.polymarket.parser import parse_market
from scanners.sources.polymarket.source import PolymarketSource


def _make_market(question: str, volume: float) -> dict:
    return {
        "conditionId": "abc123",
        "question": question,
        "outcomePrices": '["0.6", "0.4"]',
        "outcomes": '["Yes", "No"]',
        "volume": str(volume),
        "active": True,
        "closed": False,
    }


class FakePolymarketClient:
    def __init__(self):
        self.searched = []

    def get_markets_by_slug(self, slug: str) -> list[dict]:
        return [_make_market(f"Will the Fed cut rates via {slug}?", 500000)]

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


def test_oil_market_passes():
    market_filter = PolymarketMarketFilter(min_volume=100_000)
    market = _make_market("Will OPEC cut crude oil production in 2026?", 500000)
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

    records = source.fetch()

    assert len(records) == 2
    assert {r.outcome for r in records} == {"Yes", "No"}
    assert all("fed-decision-in-june-825" in r.question for r in records)


def test_source_skips_closed_or_inactive_markets():
    class ClosedMarketClient(FakePolymarketClient):
        def get_markets_by_slug(self, slug: str) -> list[dict]:
            closed = _make_market("Will the Fed cut rates in June 2026?", 500000)
            closed["closed"] = True
            inactive = _make_market("Will the Fed hike rates in June 2026?", 500000)
            inactive["active"] = False
            open_market = _make_market("Will CPI fall in June 2026?", 500000)
            open_market["conditionId"] = "open123"
            return [closed, inactive, open_market]

    source = PolymarketSource(client=ClosedMarketClient())
    source.tracked_slugs = ["fed-decision-in-june-825"]

    records = source.fetch()

    assert len(records) == 2
    assert {record.market_id for record in records} == {"open123"}


def test_parser_rejects_mismatched_outcome_prices():
    market = _make_market("Will the Fed cut rates in June 2026?", 500000)
    market["outcomePrices"] = '["0.6"]'

    assert parse_market(market) == []


def test_parser_requires_condition_id_for_stable_history_key():
    market = _make_market("Will the Fed cut rates in June 2026?", 500000)
    market.pop("conditionId")
    market["id"] = "gamma-row-123"

    assert parse_market(market) == []


def test_parser_rejects_out_of_range_probability():
    market = _make_market("Will the Fed cut rates in June 2026?", 500000)
    market["outcomePrices"] = '["1.2", "-0.2"]'

    assert parse_market(market) == []
