"""板块板块 API 的 Pydantic 模型。"""
from __future__ import annotations

from pydantic import BaseModel

from schemas.common import TimeFields


class SectorLeaderboardRow(BaseModel):
    """板块榜单一行：一个 CMC category 的最新 snapshot 聚合数据。"""
    category: str
    group: str | None = None
    token_count: int
    ret_1h: float | None = None
    ret_24h: float | None = None
    ret_168h: float | None = None
    ret_720h: float | None = None


class SectorLeaderboardResponse(BaseModel):
    snapshot_at: TimeFields | None = None  # 最新 sector_returns 的 snapshot_at
    rows: list[SectorLeaderboardRow]


class SectorTokenRow(BaseModel):
    """单个板块展开后看到的每个 symbol 的当前涨跌。"""
    symbol: str               # 规范化后的 base symbol（CMC 命名），如 "ETH"
    binance_symbol: str       # BMAC pivot 列名，如 "ETHUSDT"
    market: str               # "spot" or "swap"
    ret_1h: float | None = None
    ret_24h: float | None = None
    ret_168h: float | None = None
    ret_720h: float | None = None


class SectorTokensResponse(BaseModel):
    category: str
    group: str | None = None
    snapshot_at: TimeFields | None = None
    tokens: list[SectorTokenRow]
