from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import alerts.engine as engine_module
from alerts.engine import AlertEngine
from alerts.rules import AlertRule
from database import Base
from models.alert_log import AlertLog
from models.sector import SectorReturn


class FakeChannel:
    name = "wechat_work"

    def __init__(self):
        self.sent: list[tuple[str, str]] = []

    def send(self, title: str, content: str) -> bool:
        self.sent.append((title, content))
        return True


def _session(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(engine_module, "get_session", lambda: Session())
    return Session()


def _sector(
    snapshot_at: datetime,
    category: str,
    ret_24h: float,
    *,
    ret_24h_median: float | None = None,
    token_count: int = 12,
) -> SectorReturn:
    return SectorReturn(
        snapshot_at=snapshot_at,
        category=category,
        group_name="AI",
        token_count=token_count,
        ret_1h=1.0,
        ret_24h=ret_24h,
        ret_168h=ret_24h,
        ret_720h=ret_24h,
        ret_1h_median=1.0,
        ret_24h_median=ret_24h if ret_24h_median is None else ret_24h_median,
        ret_168h_median=ret_24h if ret_24h_median is None else ret_24h_median,
        ret_720h_median=ret_24h if ret_24h_median is None else ret_24h_median,
    )


def test_sector_spike_alerts_latest_snapshot_once(monkeypatch):
    session = _session(monkeypatch)
    latest = datetime(2026, 7, 6, 8, 0)
    try:
        session.add_all([
            _sector(latest - timedelta(hours=1), "Old Leaders", 50.0),
            _sector(latest, "AI Agents", 9.5),
            _sector(latest, "Quiet Sector", 2.0),
            _sector(latest, "Mean Outlier", 30.0, ret_24h_median=1.0),
            _sector(latest, "Too Thin", 20.0, token_count=9),
        ])
        session.commit()
        session.close()

        channel = FakeChannel()
        engine = AlertEngine()
        engine.channels = {"wechat_work": channel}
        engine.rules = [
            AlertRule(
                name="sector_spike",
                rule_type="sector_spike",
                params={"period": "24h", "threshold_pct": 8.0, "top_n": 5},
                channels=["wechat_work"],
                cooldown_minutes=55,
                enabled=True,
            )
        ]

        engine.evaluate_sectors()
        engine.evaluate_sectors()

        assert len(channel.sent) == 1
        title, content = channel.sent[0]
        assert "板块异动" in title
        assert "AI Agents / AI: 24h 中位 +9.50% / 均值 +9.50%" in content
        assert "Old Leaders" not in content
        assert "Mean Outlier" not in content
        assert "Too Thin" not in content

        check = engine_module.get_session()
        try:
            logs = check.query(AlertLog).filter(AlertLog.rule_name == "sector_spike").all()
            assert len(logs) == 1
            assert "sector:24h:AI Agents:2026-07-06T08:00:00" in logs[0].message.splitlines()
        finally:
            check.close()
    finally:
        try:
            session.close()
        except Exception:
            pass


def test_sector_spike_direction_down(monkeypatch):
    session = _session(monkeypatch)
    latest = datetime(2026, 7, 6, 8, 0)
    try:
        session.add_all([
            _sector(latest, "Losers", -10.0),
            _sector(latest, "Winners", 12.0),
        ])
        session.commit()
        session.close()

        channel = FakeChannel()
        engine = AlertEngine()
        engine.channels = {"wechat_work": channel}
        engine.rules = [
            AlertRule(
                name="sector_down",
                rule_type="sector_spike",
                params={"period": "24h", "threshold_pct": 8.0, "direction": "down"},
                channels=["wechat_work"],
                cooldown_minutes=55,
                enabled=True,
            )
        ]

        engine.evaluate_sectors()

        assert len(channel.sent) == 1
        assert "Losers / AI: 24h 中位 -10.00% / 均值 -10.00%" in channel.sent[0][1]
        assert "Winners" not in channel.sent[0][1]
    finally:
        try:
            session.close()
        except Exception:
            pass
