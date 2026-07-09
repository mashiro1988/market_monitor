"""Tests for sector return snapshot alignment."""
from datetime import datetime

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import config
from database import Base
import scanners.sector_scanner as sector_scanner


def _pivot(columns: list[str], rows: list[tuple[datetime, list[float]]]) -> dict:
    return {
        "close": pd.DataFrame(
            [values for _, values in rows],
            index=pd.DatetimeIndex([ts for ts, _ in rows]),
            columns=columns,
        )
    }


def test_per_symbol_returns_use_common_spot_swap_snapshot(monkeypatch):
    spot = _pivot(
        ["BTCUSDT"],
        [
            (datetime(2026, 1, 1, 0, 0), [100.0]),
            (datetime(2026, 1, 1, 1, 0), [110.0]),
        ],
    )
    swap = _pivot(
        ["BTCUSDT", "ETHUSDT"],
        [
            (datetime(2026, 1, 1, 0, 0), [100.0, 100.0]),
            (datetime(2026, 1, 1, 1, 0), [120.0, 105.0]),
            (datetime(2026, 1, 1, 2, 0), [240.0, 110.0]),
        ],
    )

    def load_pivot(market: str):
        return {"spot": spot, "swap": swap}[market]

    monkeypatch.setattr(sector_scanner, "_load_pivot", load_pivot)

    snapshot_at, returns = sector_scanner._load_per_symbol_returns()

    assert snapshot_at == datetime(2026, 1, 1, 1, 0)
    assert returns["BTC"]["ret_1h"] == 10.0
    assert returns["ETH"]["ret_1h"] == 5.0


def test_sector_aggregates_include_median_and_skip_thin_categories(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    symbols = {f"SYM{i}" for i in range(10)}
    thin_symbols = {f"THIN{i}" for i in range(9)}
    returns = {
        f"SYM{i}": {"ret_1h": float(i), "ret_24h": float(i), "ret_168h": float(i), "ret_720h": float(i)}
        for i in range(9)
    }
    returns["SYM9"] = {"ret_1h": 100.0, "ret_24h": 100.0, "ret_168h": 100.0, "ret_720h": 100.0}
    for i in range(9):
        returns[f"THIN{i}"] = {"ret_24h": 50.0}

    monkeypatch.setattr(config, "all_whitelisted_cmc_categories", lambda: ["Good", "Thin"])
    monkeypatch.setattr(config, "cmc_category_to_group", lambda name: "测试")
    monkeypatch.setattr(
        sector_scanner,
        "_load_per_symbol_returns",
        lambda use_pivot_cache=False: (datetime(2026, 1, 1, 1, 0), returns),
    )
    monkeypatch.setattr(
        sector_scanner.cmc_client,
        "load_category_to_symbols",
        lambda session: {"Good": symbols, "Thin": thin_symbols},
    )
    try:
        result = sector_scanner.compute_all_sector_returns(session)

        assert len(result.aggregates) == 1
        aggregate = result.aggregates[0]
        assert aggregate.category == "Good"
        assert aggregate.token_count == 10
        assert aggregate.ret_24h == 13.6
        assert aggregate.ret_24h_median == 4.5
        assert result.skipped_thin == ["Thin(9)"]
    finally:
        session.close()
