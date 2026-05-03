from __future__ import annotations

from pydantic import BaseModel

from schemas.common import TimeFields


class MarketLatestItem(TimeFields):
    name: str
    symbol: str
    asset_class: str
    source: str
    price: float
    prev_price: float | None = None
    change_pct: float | None = None
    change_5m: float | None = None
    change_1h: float | None = None
    change_24h: float | None = None


class MarketLatestResponse(BaseModel):
    items: list[MarketLatestItem]
    last_updated: TimeFields | None = None


class MarketHistoryPoint(TimeFields):
    symbol: str
    name: str
    price: float
    normalized_pct: float | None = None


class MarketHistorySeries(BaseModel):
    symbol: str
    name: str
    asset_class: str | None = None
    points: list[MarketHistoryPoint]


class MarketHistoryResponse(BaseModel):
    symbols: list[str]
    start: TimeFields
    end: TimeFields
    series: list[MarketHistorySeries]


class MarketTableRow(TimeFields):
    asset_class: str
    name: str
    symbol: str
    price: float
    prev_price: float | None = None
    change_pct: float | None = None
    volume: float | None = None
    source: str


class MarketSymbol(BaseModel):
    symbol: str
    name: str
    asset_class: str
