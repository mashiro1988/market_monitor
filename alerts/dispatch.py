from __future__ import annotations

from datetime import datetime, timedelta, timezone

from loguru import logger

from alerts.rules import AlertRule
from models.alert_log import AlertLog


def _get_session():
    # Keep tests and older callers that monkeypatch alerts.engine.get_session working.
    from alerts import engine as engine_module

    return engine_module.get_session()


class AlertDispatchMixin:
    def _delivered_channels_since(
        self,
        rule_name: str,
        cutoff: datetime,
        *,
        channels: list[str] | None = None,
        exact_marker: str | None = None,
    ) -> set[str]:
        session = _get_session()
        try:
            query = session.query(AlertLog.channel, AlertLog.message).filter(
                AlertLog.rule_name == rule_name,
                AlertLog.timestamp >= cutoff,
                AlertLog.delivered == True,
            )
            if channels:
                query = query.filter(AlertLog.channel.in_(channels))
            if exact_marker:
                query = query.filter(AlertLog.message.like(f"%{exact_marker}%"))
            rows = query.all()
            delivered: set[str] = set()
            for channel, message in rows:
                if exact_marker and exact_marker not in (message or "").splitlines():
                    continue
                delivered.add(channel)
            return delivered
        finally:
            session.close()

    def _is_in_cooldown(self, rule_name: str, cooldown_minutes: int, channels: list[str] | None = None) -> bool:
        """检查规则是否在冷却期内"""
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=cooldown_minutes)
        delivered = self._delivered_channels_since(rule_name, cutoff, channels=channels)
        required = set(channels or [])
        return required.issubset(delivered) if required else bool(delivered)

    def _log_alert(self, rule_name: str, message: str, channel: str, delivered: bool):
        """记录告警发送日志"""
        session = _get_session()
        try:
            log = AlertLog(
                timestamp=datetime.now(timezone.utc).replace(tzinfo=None),
                rule_name=rule_name,
                message=message[:8000],
                channel=channel,
                delivered=delivered,
            )
            session.add(log)
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error(f"记录告警日志失败: {e}")
        finally:
            session.close()

    def _dispatch(self, rule: AlertRule, title: str, content: str):
        """分发告警到各通道"""
        for channel_name in rule.channels:
            channel = self.channels.get(channel_name)
            if not channel:
                logger.warning(f"未知告警通道: {channel_name}")
                continue
            delivered = channel.send(title, content)
            self._log_alert(rule.name, f"{title}\n{content}", channel_name, delivered)
