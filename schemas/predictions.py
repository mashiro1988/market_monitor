from __future__ import annotations

from typing import Literal

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
    # 来源跟踪项 "slug:<identifier>"(2026-08-28):前端据此把市场映射回跟踪项/挂接事件
    origin: str | None = None


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


class TrackedEventBrief(BaseModel):
    """跟踪项挂着哪些事件(跟踪管理表归属列,2026-08-28)。"""
    link_id: int
    event_id: int
    display_no: int
    name: str


class TrackedMarketSchema(BaseModel):
    id: int
    kind: Literal["slug"]
    identifier: str
    display_name: str | None = None
    enabled: bool
    notes: str | None = None
    market: str = "macro"
    events: list[TrackedEventBrief] = []


class TrackedMarketCreate(BaseModel):
    kind: Literal["slug"]
    identifier: str
    display_name: str | None = None
    notes: str | None = None
    market: Literal["macro", "crypto"] = "macro"
    event_id: int | None = None          # 传了=添加即挂接(link_source=human)


class TrackedMarketUpdate(BaseModel):
    enabled: bool | None = None
    display_name: str | None = None
    notes: str | None = None


class EventMarketItem(BaseModel):
    """事件详情市场卡(spec §5):跟踪项 + 旗下市场最新摘要 + 断流语义徽章素材。"""
    link_id: int
    tracked_id: int
    slug: str
    display_name: str | None = None
    market: str = "macro"
    enabled: bool = True
    link_source: str = "human"
    confidence: float | None = None
    settled: bool = False
    waiting_first_scan: bool = False
    markets: list[PredictionMarketSummary] = []


class EventMarketsResponse(BaseModel):
    items: list[EventMarketItem] = []
