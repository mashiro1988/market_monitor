from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ErrorResponse(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class TimeFields(BaseModel):
    timestamp_utc: str | None = None
    timestamp_bj: str | None = None


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
    pages: int


class AppBaseModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)
