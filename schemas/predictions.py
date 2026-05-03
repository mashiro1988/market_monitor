from __future__ import annotations

from pydantic import BaseModel

from schemas.common import TimeFields


class PredictionRow(TimeFields):
    market_id: str
    question: str
    outcome: str
    probability: float
    prev_probability: float | None = None
    probability_pct: float
    delta_pct: float | None = None
    volume: float | None = None


class PredictionMarketSummary(BaseModel):
    market_id: str
    question: str
    volume: float | None = None
    outcomes: list[PredictionRow]
    has_shift: bool


class PredictionFamilySeries(BaseModel):
    market_id: str
    question: str
    label: str
    order: float
    points: list[PredictionRow]


class PredictionFamily(BaseModel):
    id: str
    name: str
    series: list[PredictionFamilySeries]


class PredictionsResponse(BaseModel):
    markets: list[PredictionMarketSummary]
    latest_timestamp: TimeFields | None = None
