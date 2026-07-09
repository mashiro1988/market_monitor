from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import config
from database import Base
from models.alert_log import AlertLog
from services import remote_monitoring


NOW = datetime(2026, 7, 6, 12, 0)


class FakeChannel:
    name = "wechat_work"

    def __init__(self):
        self.sent: list[tuple[str, str]] = []

    def send(self, title: str, content: str) -> bool:
        self.sent.append((title, content))
        return True


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def test_remote_cycle_exception_alerts_once_with_cooldown(monkeypatch):
    monkeypatch.setattr(config, "REMOTE_MONITORING_ENABLED", True)
    monkeypatch.setattr(config, "REMOTE_MONITOR_ALERT_COOLDOWN_MINUTES", 60)
    monkeypatch.setattr(remote_monitoring.remote_fs, "get_session_status", lambda: {
        "consecutive_failures": 0,
        "last_error": None,
    })
    monkeypatch.setattr(remote_monitoring, "_sqlite_wal_size_bytes", lambda: (Path("market_monitor.db-wal"), 0))
    session = _session()
    channel = FakeChannel()
    try:
        sent = remote_monitoring.check_remote_data_health(
            exception=RuntimeError("pull loop crashed"),
            session=session,
            channel=channel,
            now=NOW,
        )
        sent_again = remote_monitoring.check_remote_data_health(
            exception=RuntimeError("pull loop crashed"),
            session=session,
            channel=channel,
            now=NOW + timedelta(minutes=10),
        )
        sent_after_cooldown = remote_monitoring.check_remote_data_health(
            exception=RuntimeError("pull loop crashed"),
            session=session,
            channel=channel,
            now=NOW + timedelta(minutes=61),
        )

        assert [item["kind"] for item in sent] == ["remote_data_cycle_failed"]
        assert sent_again == []
        assert [item["kind"] for item in sent_after_cooldown] == ["remote_data_cycle_failed"]
        assert len(channel.sent) == 2
        logs = session.query(AlertLog).filter(AlertLog.rule_name == remote_monitoring.RULE_NAME).all()
        assert len(logs) == 2
        assert "remote-monitor:remote_data_cycle_failed" in logs[0].message.splitlines()
    finally:
        session.close()


def test_sftp_threshold_and_sector_scan_error_alert(monkeypatch):
    monkeypatch.setattr(config, "REMOTE_MONITORING_ENABLED", True)
    monkeypatch.setattr(config, "REMOTE_MONITOR_SFTP_FAILURE_THRESHOLD", 3)
    monkeypatch.setattr(remote_monitoring.remote_fs, "get_session_status", lambda: {
        "consecutive_failures": 3,
        "last_error": "TimeoutError",
    })
    monkeypatch.setattr(remote_monitoring, "_sqlite_wal_size_bytes", lambda: (Path("market_monitor.db-wal"), 0))
    session = _session()
    channel = FakeChannel()
    try:
        sent = remote_monitoring.check_remote_data_health(
            stats={"sector_scan": {"error": "RuntimeError('bad pivot')"}},
            session=session,
            channel=channel,
            now=NOW,
        )

        assert {item["kind"] for item in sent} == {"sector_scan_failed", "sftp_consecutive_failures"}
        assert len(channel.sent) == 2
    finally:
        session.close()


def test_large_wal_alert(monkeypatch):
    monkeypatch.setattr(config, "REMOTE_MONITORING_ENABLED", True)
    monkeypatch.setattr(config, "REMOTE_MONITOR_WAL_MAX_MB", 1)
    monkeypatch.setattr(remote_monitoring.remote_fs, "get_session_status", lambda: {
        "consecutive_failures": 0,
        "last_error": None,
    })
    monkeypatch.setattr(remote_monitoring, "_sqlite_wal_size_bytes", lambda: (
        Path("market_monitor.db-wal"),
        2 * 1024 * 1024,
    ))
    session = _session()
    channel = FakeChannel()
    try:
        sent = remote_monitoring.check_remote_data_health(session=session, channel=channel, now=NOW)

        assert [item["kind"] for item in sent] == ["sqlite_wal_large"]
        assert "2.0 MB" in channel.sent[0][1]
    finally:
        session.close()
