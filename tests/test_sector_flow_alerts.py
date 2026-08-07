"""资金流勾稽门失败告警：按市场去重 + 冷却窗内只推一次 + 发送失败不占冷却。"""
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import config
from database import Base
from models.alert_log import AlertLog
from services import sector_flow_monitoring


NOW = datetime(2026, 8, 7, 10, 0)


class FakeChannel:
    name = "wechat_work"

    def __init__(self, delivered: bool = True):
        self.sent: list[tuple[str, str]] = []
        self._delivered = delivered

    def send(self, title: str, content: str) -> bool:
        self.sent.append((title, content))
        return self._delivered


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def test_sends_one_alert_per_failing_market():
    session, channel = _session(), FakeChannel()
    try:
        sent = sector_flow_monitoring.alert_flow_gate_failures(
            {"spot": "缺字段: taker_buy_quote_asset_volume", "swap": "恒等式违规占比 5%"},
            session=session, channel=channel, now=NOW,
        )
        assert len(sent) == 2
        assert len(channel.sent) == 2
        titles = " ".join(title for title, _ in channel.sent)
        assert "spot" in titles and "swap" in titles
        # 正文要带失败原因，人一眼能判是补丁没上还是数据坏了
        assert any("taker_buy_quote_asset_volume" in content for _, content in channel.sent)
    finally:
        session.close()


def test_no_alert_when_all_gates_pass():
    session, channel = _session(), FakeChannel()
    try:
        assert sector_flow_monitoring.alert_flow_gate_failures(
            {}, session=session, channel=channel, now=NOW) == []
        assert channel.sent == []
    finally:
        session.close()


def test_second_failure_within_cooldown_is_suppressed(monkeypatch):
    monkeypatch.setattr(config, "FLOW_GATE_ALERT_COOLDOWN_MINUTES", 60)
    session, channel = _session(), FakeChannel()
    try:
        failures = {"spot": "缺字段: taker_buy_quote_asset_volume"}
        sector_flow_monitoring.alert_flow_gate_failures(
            failures, session=session, channel=channel, now=NOW)
        again = sector_flow_monitoring.alert_flow_gate_failures(
            failures, session=session, channel=channel, now=NOW + timedelta(minutes=30))
        assert again == []
        assert len(channel.sent) == 1
    finally:
        session.close()


def test_alert_resumes_after_cooldown(monkeypatch):
    monkeypatch.setattr(config, "FLOW_GATE_ALERT_COOLDOWN_MINUTES", 60)
    session, channel = _session(), FakeChannel()
    try:
        failures = {"spot": "缺字段: taker_buy_quote_asset_volume"}
        sector_flow_monitoring.alert_flow_gate_failures(
            failures, session=session, channel=channel, now=NOW)
        later = sector_flow_monitoring.alert_flow_gate_failures(
            failures, session=session, channel=channel, now=NOW + timedelta(minutes=61))
        assert len(later) == 1
        assert len(channel.sent) == 2
    finally:
        session.close()


def test_failed_delivery_does_not_consume_cooldown(monkeypatch):
    monkeypatch.setattr(config, "FLOW_GATE_ALERT_COOLDOWN_MINUTES", 60)
    session = _session()
    failing, working = FakeChannel(delivered=False), FakeChannel(delivered=True)
    try:
        failures = {"spot": "缺字段: taker_buy_quote_asset_volume"}
        sector_flow_monitoring.alert_flow_gate_failures(
            failures, session=session, channel=failing, now=NOW)
        retry = sector_flow_monitoring.alert_flow_gate_failures(
            failures, session=session, channel=working, now=NOW + timedelta(minutes=5))
        assert len(retry) == 1
        assert len(working.sent) == 1
    finally:
        session.close()


def test_writes_alert_log_rows():
    session, channel = _session(), FakeChannel()
    try:
        sector_flow_monitoring.alert_flow_gate_failures(
            {"spot": "缺字段: taker_buy_quote_asset_volume"},
            session=session, channel=channel, now=NOW)
        logs = session.query(AlertLog).all()
        assert len(logs) == 1
        assert logs[0].rule_name == sector_flow_monitoring.RULE_NAME
        assert logs[0].delivered is True
    finally:
        session.close()
