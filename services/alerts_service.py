from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import Session

import config
from alerts.channels.wechat_work import WeChatWorkChannel
from models.alert_log import AlertLog
from schemas.alerts import AlertLogSchema, AlertRuleSchema, AlertTestResponse, AlertWebhookStatus
from schemas.common import Page
from services.pagination import clamp_page, page_count
from services.time_utils import timestamp_pair, utc_now_naive


def get_rules() -> list[AlertRuleSchema]:
    return [
        AlertRuleSchema(
            name=rule["name"],
            rule_type=rule["rule_type"],
            params=rule.get("params", {}),
            channels=list(rule.get("channels", [])),
            cooldown_minutes=int(rule.get("cooldown_minutes", 30)),
            enabled=bool(rule.get("enabled", True)),
        )
        for rule in config.ALERT_RULES
    ]


def get_webhook_status() -> AlertWebhookStatus:
    webhook = config.WECHAT_WORK_WEBHOOK
    preview = f"{webhook[:50]}..." if webhook else None
    return AlertWebhookStatus(configured=bool(webhook), preview=preview)


def test_wechat() -> AlertTestResponse:
    if not config.WECHAT_WORK_WEBHOOK:
        return AlertTestResponse(ok=False, message="企业微信 Webhook 未配置")
    channel = WeChatWorkChannel()
    ok = channel.send("测试消息", "Investment Agent 告警系统测试。如果你看到此消息，说明 Webhook 配置正确。")
    return AlertTestResponse(ok=ok, message="测试消息发送成功" if ok else "发送失败，请检查 Webhook URL")


def get_logs(session: Session, hours_back: int = 24, page: int = 1, page_size: int = 50) -> Page[AlertLogSchema]:
    page, page_size = clamp_page(page, page_size)
    cutoff = utc_now_naive() - timedelta(hours=max(1, min(int(hours_back or 24), 24 * 30)))
    query = session.query(AlertLog).filter(AlertLog.timestamp >= cutoff).order_by(AlertLog.timestamp.desc())
    total = query.count()
    rows = query.offset((page - 1) * page_size).limit(page_size).all()
    return Page[AlertLogSchema](
        items=[
            AlertLogSchema(
                id=row.id,
                rule_name=row.rule_name,
                message=row.message,
                channel=row.channel,
                delivered=bool(row.delivered),
                **timestamp_pair(row.timestamp),
            )
            for row in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
        pages=page_count(total, page_size),
    )
