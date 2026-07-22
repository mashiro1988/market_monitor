# -*- coding: utf-8 -*-
"""回补 CSV 的读写与校验：本机拉取端(write) 与服务器导入端(read) 共用一个口径。

列: symbol, timestamp_utc(ISO, naive UTC, bar_end), close, volume, asset_class, name
prev_price/change_pct 不进 CSV——落库时由 PriceScanner._save_records 按库内邻档自动衔接。
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from scanners.base import PriceRecord

FIELDS = ["symbol", "timestamp_utc", "close", "volume", "asset_class", "name"]


@dataclass(frozen=True)
class ValidationStats:
    total: int = 0
    kept: int = 0
    bad_symbol: int = 0
    bad_price: int = 0
    out_of_range: int = 0


def write_dump(path: Path | str, records: list[PriceRecord]) -> int:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in records:
            w.writerow({
                "symbol": r.symbol,
                "timestamp_utc": r.timestamp.isoformat(timespec="seconds"),
                "close": repr(r.price),
                "volume": "" if r.volume is None else repr(r.volume),
                "asset_class": r.asset_class,
                "name": r.name,
            })
    return len(records)


def read_dump(path: Path | str, *, allowed_symbols: set[str],
              start: datetime, end: datetime) -> tuple[list[PriceRecord], ValidationStats]:
    """读 CSV → PriceRecord（source 固定 yfinance）；行校验：白名单/价格>0/时间窗内。"""
    rows: list[PriceRecord] = []
    total = bad_symbol = bad_price = out_of_range = 0
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            total += 1
            if row["symbol"] not in allowed_symbols:
                bad_symbol += 1
                continue
            try:
                price = float(row["close"])
            except ValueError:
                price = float("nan")
            if not price > 0:          # NaN 也走这个分支
                bad_price += 1
                continue
            ts = datetime.fromisoformat(row["timestamp_utc"])
            if not (start <= ts <= end):
                out_of_range += 1
                continue
            volume = float(row["volume"]) if row["volume"] else None
            rows.append(PriceRecord(
                asset_class=row["asset_class"], symbol=row["symbol"], name=row["name"],
                price=price, volume=volume, source="yfinance", timestamp=ts,
            ))
    rows.sort(key=lambda r: (r.symbol, r.timestamp))
    return rows, ValidationStats(total=total, kept=len(rows), bad_symbol=bad_symbol,
                                 bad_price=bad_price, out_of_range=out_of_range)
