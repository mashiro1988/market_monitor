"""Tests for Eastmoney structured bond quote parsing."""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scanners.base import PriceRecord
from scanners.sources.eastmoney_bond_source import EastmoneyBondQuoteSource


def test_parse_quote_scales_yield_and_timestamp():
    source = EastmoneyBondQuoteSource()
    record = source._parse_quote(
        "US_10Y",
        {"name": "美国10年期国债收益率"},
        {
            "f43": 43254,
            "f58": "美国10年期国债收益率",
            "f60": 43055,
            "f86": 1776935497,
            "f170": 46,
        },
    )

    assert record is not None
    assert record.symbol == "US_10Y"
    assert record.name == "美国10年期国债收益率"
    assert record.price == 4.3254
    assert record.prev_price == 4.3055
    assert record.change_pct == 0.46
    assert record.source == "eastmoney_bond_quote"
    assert record.timestamp == datetime.fromtimestamp(1776935497, timezone.utc).replace(tzinfo=None)


def test_parse_quote_missing_price_returns_none():
    source = EastmoneyBondQuoteSource()
    record = source._parse_quote(
        "JP_2Y",
        {"name": "日本2年期国债"},
        {"f43": "-", "f58": "日本2年期国债"},
    )

    assert record is None


def test_build_spread_records_includes_us_and_japan_spreads():
    source = EastmoneyBondQuoteSource()
    ts = datetime(2026, 4, 23, 9, 0, 0)
    records = [
        PriceRecord("bond", "US_10Y", "美国10年期国债收益率", 4.30, timestamp=ts),
        PriceRecord("bond", "US_2Y", "美国2年期国债收益率", 3.80, timestamp=ts),
        PriceRecord("bond", "JP_10Y", "日本10年期国债", 2.40, timestamp=ts),
        PriceRecord("bond", "JP_2Y", "日本2年期国债", 1.35, timestamp=ts),
    ]

    spreads = source._build_spread_records(records)
    by_symbol = {record.symbol: record for record in spreads}

    assert by_symbol["US_SPREAD"].price == 0.5
    assert by_symbol["US_SPREAD"].name == "美债利差(10Y-2Y)"
    assert round(by_symbol["JP_SPREAD"].price, 6) == 1.05
    assert by_symbol["JP_SPREAD"].name == "日债利差(10Y-2Y)"
