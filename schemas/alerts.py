from __future__ import annotations

from pydantic import BaseModel

from schemas.common import TimeFields


class AlertRuleSchema(BaseModel):
    name: str
    rule_type: str
    params: dict
    channels: list[str]
    cooldown_minutes: int
    enabled: bool


class AlertWebhookStatus(BaseModel):
    configured: bool
    preview: str | None = None


class AlertTestResponse(BaseModel):
    ok: bool
    message: str


class AlertLogSchema(TimeFields):
    id: int
    rule_name: str
    message: str
    channel: str
    delivered: bool
