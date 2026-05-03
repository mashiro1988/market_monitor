from __future__ import annotations

from pydantic import BaseModel

from schemas.common import TimeFields


class TaskStatus(BaseModel):
    task_id: str
    status: str
    created_at: TimeFields
    started_at: TimeFields | None = None
    finished_at: TimeFields | None = None
    message: str | None = None
    result: dict | None = None
    error: str | None = None
