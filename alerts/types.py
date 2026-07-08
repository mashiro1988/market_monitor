from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class PriceWindowMove:
    """Price movement measured over an alert rule window."""
    change_pct: float
    start_time: datetime
    end_time: datetime
    start_price: float
    end_price: float
    low_price: float
    high_price: float


@dataclass(frozen=True)
class PriceThresholdSummary:
    """Aggregated price threshold hits for hourly summary."""
    symbol: str
    name: str
    asset_class: str
    threshold_pct: float
    window_minutes: int
    trigger_count: int
    strongest_move: PriceWindowMove
