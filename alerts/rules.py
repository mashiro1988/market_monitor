"""
告警规则定义
"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AlertRule:
    """告警规则"""
    name: str
    rule_type: str              # price_change, price_level, news_importance, prediction_shift, hourly_summary
    params: dict = field(default_factory=dict)
    channels: list[str] = field(default_factory=lambda: ["wechat_work"])
    cooldown_minutes: int = 30
    enabled: bool = True
