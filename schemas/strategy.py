# -*- coding: utf-8 -*-
"""持仓策略 API 模型。overview 结构较深且是内部页面专用，用宽松 dict 透传；
写路径（positions/settings/simulate）用严格模型校验。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class StrategyPositionCreate(BaseModel):
    symbol: str = "VIRTUAL-USDT-SWAP"
    batch_label: str
    entry_at: datetime                      # naive UTC ISO 字符串
    entry_price: float = Field(gt=0)
    quantity: float = Field(gt=0)
    forecast: int = Field(ge=-20, le=20, default=10)
    note: str | None = None


class StrategyPositionUpdate(BaseModel):
    batch_label: str | None = None
    entry_at: datetime | None = None
    entry_price: float | None = Field(default=None, gt=0)
    quantity: float | None = Field(default=None, gt=0)
    forecast: int | None = Field(default=None, ge=-20, le=20)
    status: str | None = None               # open / closed
    closed_at: datetime | None = None
    close_price: float | None = None
    note: str | None = None


class StrategyPositionSchema(BaseModel):
    id: int
    symbol: str
    batch_label: str
    entry_at: datetime
    entry_price: float
    quantity: float
    forecast: int
    status: str
    closed_at: datetime | None
    close_price: float | None
    note: str | None

    class Config:
        from_attributes = True


class StrategySettingsSchema(BaseModel):
    capital: float = Field(gt=0)
    risk_budget_pct: float = Field(gt=0, le=1)
    x_soft: int = Field(gt=0)
    x_hard: int = Field(gt=0)
    ewma_alpha: float = Field(gt=0, lt=1)
    vol_update_threshold: float = Field(gt=0, lt=1)

    class Config:
        from_attributes = True


class StrategySimulateRequest(BaseModel):
    price: float = Field(gt=0)
    forecast: int = Field(ge=-20, le=20, default=10)
    vol: float | None = Field(default=None, gt=0)
    budget_pct: float | None = Field(default=None, gt=0, le=1)
    symbol: str = "VIRTUAL-USDT-SWAP"


class StrategyEventSchema(BaseModel):
    id: int
    created_at: datetime
    symbol: str
    position_id: int | None
    kind: str
    message: str
    pushed: bool

    class Config:
        from_attributes = True


# ---------- overview / simulate 响应模型（供 OpenAPI 类型生成器吐前端类型） ----------

class StrategyChartDay(BaseModel):
    date: str
    close: float


class StrategyCostLine(BaseModel):
    label: str
    value: float


class StrategyChartPoint(BaseModel):
    date: str
    value: float


class StrategyEntryMarker(BaseModel):
    date: str
    label: str
    value: float


class StrategyChart(BaseModel):
    days: list[StrategyChartDay]
    soft_line: list[float | None]
    hard_current: float | None
    cost_lines: list[StrategyCostLine]
    anchor_point: StrategyChartPoint | None
    entry_markers: list[StrategyEntryMarker]


class StrategyBatchReadout(BaseModel):
    id: int
    batch_label: str
    entry_at: str
    entry_price: float
    quantity: float
    forecast: int
    note: str | None
    anchor_high: float
    soft_stop: float
    hard_stop: float
    breached: bool
    locked: bool
    occupy_usd: float
    distance_pct: float
    pnl_usd: float


class StrategyReentry(BaseModel):
    level: float
    breached_at: str | None


class StrategyOverview(BaseModel):
    symbol: str
    generated_at: str
    data_stale: bool
    live_price: float | None
    live_price_at: str | None
    vol_latest: float | None
    v_used: float | None
    verdict: str                      # hold / breach / no_position / no_data
    budget_usd: float
    total_occupy_usd: float
    settings: StrategySettingsSchema
    reentry: StrategyReentry | None
    batches: list[StrategyBatchReadout]
    chart: StrategyChart


class StrategySimulateResult(BaseModel):
    stop_price: float
    stop_distance: float
    quantity: float
    notional_usd: float
    vol: float
    budget_usd: float
    leverage: float | None


class StrategyRunCheckResult(BaseModel):
    ok: bool
    events: list[str]
