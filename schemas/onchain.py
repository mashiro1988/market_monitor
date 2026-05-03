from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from schemas.common import TimeFields


class OnchainDataset(BaseModel):
    name: str
    cached_at: TimeFields | None = None
    ttl_seconds: int
    rows: list[dict[str, Any]]
