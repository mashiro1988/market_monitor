"""
告警规则定义
"""
from dataclasses import dataclass, field
from enum import Enum


class AlertRuleType(str, Enum):
    """已知告警规则类型。继承 str 让成员可与原始字符串相等比较，
    现有 config 字典和测试 fixture 不需要改动即可继续工作。"""
    PRICE_CHANGE = "price_change"
    PRICE_LEVEL = "price_level"
    NEWS_IMPORTANCE = "news_importance"
    PREDICTION_SHIFT = "prediction_shift"
    HOURLY_SUMMARY = "hourly_summary"


@dataclass
class AlertRule:
    """告警规则"""
    name: str
    rule_type: str              # 取值见 AlertRuleType；保留 str 类型以兼容直接传字符串的旧调用方
    params: dict = field(default_factory=dict)
    channels: list[str] = field(default_factory=lambda: ["wechat_work"])
    cooldown_minutes: int = 30
    enabled: bool = True
