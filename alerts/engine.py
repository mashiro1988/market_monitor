"""
告警引擎 - 评估规则、冷却去重、分发通知

支持两种模式：
1. 即时推送：超阈值变化立即推送，超阈值项用 <font color="warning"> 特别标注
2. 定时摘要：每小时汇总全品种概览推送
"""
from database import get_session

import config
from alerts.channels.console import ConsoleChannel
from alerts.channels.wechat_work import WeChatWorkChannel
from alerts.dispatch import AlertDispatchMixin
from alerts.evaluators.news import NewsAlertMixin
from alerts.evaluators.predictions import PredictionAlertMixin
from alerts.evaluators.price import PriceAlertMixin
from alerts.evaluators.sectors import SectorAlertMixin
from alerts.evaluators.summary import HourlySummaryMixin
from alerts.rules import AlertRule
from alerts.types import PriceThresholdSummary, PriceWindowMove
from scanners.base import NewsRecord, PredictionRecord, PriceRecord


class AlertEngine(
    AlertDispatchMixin,
    PriceAlertMixin,
    NewsAlertMixin,
    PredictionAlertMixin,
    SectorAlertMixin,
    HourlySummaryMixin,
):
    """告警引擎"""

    def __init__(self):
        self.channels = {
            "wechat_work": WeChatWorkChannel(),
            "console": ConsoleChannel(),
        }
        self.rules: list[AlertRule] = []
        self._load_rules()

    def _load_rules(self):
        """从配置加载告警规则"""
        for rule_cfg in config.ALERT_RULES:
            self.rules.append(AlertRule(
                name=rule_cfg["name"],
                rule_type=rule_cfg["rule_type"],
                params=rule_cfg.get("params", {}),
                channels=rule_cfg.get("channels", ["wechat_work"]),
                cooldown_minutes=rule_cfg.get("cooldown_minutes", 30),
                enabled=rule_cfg.get("enabled", True),
            ))

    def evaluate_all(
        self,
        price_records: list[PriceRecord] | None = None,
        news_records: list[NewsRecord] | None = None,
        prediction_records: list[PredictionRecord] | None = None,
    ):
        """统一评估所有告警规则"""
        if price_records:
            self.evaluate_prices(price_records)
        if news_records:
            self.evaluate_news(news_records)
        if prediction_records:
            self.evaluate_predictions(prediction_records)
        self.evaluate_sectors()


__all__ = ["AlertEngine", "PriceThresholdSummary", "PriceWindowMove", "get_session"]
