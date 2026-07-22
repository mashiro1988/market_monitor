# -*- coding: utf-8 -*-
"""price_dump CSV 往返与行校验（回补脚本共享模块）。"""
from datetime import datetime
from pathlib import Path

import pytest

from scanners.base import PriceRecord
from scripts.price_dump import read_dump, write_dump, ValidationStats

REC = PriceRecord(
    asset_class="futures", symbol="NQ=F", name="纳指期货",
    price=20000.5, volume=123.0, source="yfinance",
    timestamp=datetime(2026, 7, 21, 22, 10),
)


def test_roundtrip(tmp_path: Path):
    p = tmp_path / "dump.csv"
    write_dump(p, [REC])
    rows, stats = read_dump(
        p,
        allowed_symbols={"NQ=F"},
        start=datetime(2026, 7, 21, 20, 0),
        end=datetime(2026, 7, 22, 12, 0),
    )
    assert stats == ValidationStats(total=1, kept=1, bad_symbol=0, bad_price=0, out_of_range=0)
    r = rows[0]
    assert (r.symbol, r.asset_class, r.name) == ("NQ=F", "futures", "纳指期货")
    assert r.price == 20000.5 and r.volume == 123.0
    assert r.timestamp == datetime(2026, 7, 21, 22, 10)
    assert r.source == "yfinance"
    assert r.prev_price is None and r.change_pct is None  # 链条由 _save_records 落库时衔接


def test_validation_drops_bad_rows(tmp_path: Path):
    p = tmp_path / "dump.csv"
    bad_symbol = PriceRecord(asset_class="futures", symbol="EVIL=F", name="x",
                             price=1.0, source="yfinance", timestamp=datetime(2026, 7, 21, 23, 0))
    bad_price = PriceRecord(asset_class="futures", symbol="NQ=F", name="纳指期货",
                            price=0.0, source="yfinance", timestamp=datetime(2026, 7, 21, 23, 0))
    out_of_range = PriceRecord(asset_class="futures", symbol="NQ=F", name="纳指期货",
                               price=1.0, source="yfinance", timestamp=datetime(2026, 7, 30, 0, 0))
    write_dump(p, [REC, bad_symbol, bad_price, out_of_range])
    rows, stats = read_dump(p, allowed_symbols={"NQ=F"},
                            start=datetime(2026, 7, 21, 20, 0), end=datetime(2026, 7, 22, 12, 0))
    assert [r.symbol for r in rows] == ["NQ=F"]
    assert stats == ValidationStats(total=4, kept=1, bad_symbol=1, bad_price=1, out_of_range=1)


def test_volume_none_roundtrip(tmp_path: Path):
    p = tmp_path / "dump.csv"
    rec = PriceRecord(asset_class="stock_index", symbol="^GSPC", name="标普500",
                      price=6000.0, volume=None, source="yfinance",
                      timestamp=datetime(2026, 7, 21, 22, 10))
    write_dump(p, [rec])
    rows, _ = read_dump(p, allowed_symbols={"^GSPC"},
                        start=datetime(2026, 7, 21, 0, 0), end=datetime(2026, 7, 22, 0, 0))
    assert rows[0].volume is None
