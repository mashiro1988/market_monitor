# -*- coding: utf-8 -*-
"""价格源故障告警（2026-07-27 P0）：源挂了要主动推企业微信，不能只在卡片上等人看。

判定口径与市场概览卡片**同源**（复用 market_service.freshness_for），保证"推送说的"
和"页面显示的"永远一致。
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import config
from database import Base
from models.alert_log import AlertLog
from models.price import PriceSnapshot
import services.price_source_monitoring as psm

NOW = datetime(2026, 7, 27, 6, 0, 0)


class FakeChannel:
    name = "wechat_work"

    def __init__(self, delivered=True):
        self.delivered = delivered
        self.sent: list[tuple[str, str]] = []

    def send(self, title, content):
        self.sent.append((title, content))
        return self.delivered


@pytest.fixture()
def session(monkeypatch):
    eng = create_engine("sqlite:///:memory:",
                        connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    monkeypatch.setattr(psm.market_sessions, "is_open", lambda sym, now: True)
    monkeypatch.setattr(psm.market_service, "failed_price_scanner_names", lambda: set())
    yield s
    s.close()


def _snap(s, symbol, minutes_ago, source="yfinance", asset_class="futures"):
    s.add(PriceSnapshot(timestamp=NOW - timedelta(minutes=minutes_ago),
                        asset_class=asset_class, symbol=symbol, name=symbol,
                        price=100.0, source=source))
    s.commit()


# ---------- findings 判定 ----------

def test_no_finding_when_all_fresh(session):
    _snap(session, "ES=F", 3)
    _snap(session, "NQ=F", 7)
    assert psm.collect_price_source_findings(session, now=NOW) == []


def test_down_symbols_produce_one_finding_per_source(session):
    _snap(session, "ES=F", 90)                       # >60min → source_down
    _snap(session, "NQ=F", 120)
    _snap(session, "BTC/USDT", 2, source="okx_swap_5m", asset_class="crypto")   # 正常
    findings = psm.collect_price_source_findings(session, now=NOW)
    assert len(findings) == 1
    f = findings[0]
    assert f.kind == "price_source_down"
    assert "yfinance" in f.marker
    assert "ES=F" in f.content and "NQ=F" in f.content
    assert "BTC/USDT" not in f.content


def test_closed_market_never_alerts(session, monkeypatch):
    monkeypatch.setattr(psm.market_sessions, "is_open", lambda sym, now: False)
    _snap(session, "^GSPC", 3000, asset_class="stock_index")
    assert psm.collect_price_source_findings(session, now=NOW) == []


def test_stale_yellow_does_not_alert(session):
    _snap(session, "ES=F", 30)                       # 15<lag<=60 → stale（黄标，不推送）
    assert psm.collect_price_source_findings(session, now=NOW) == []


def test_scanner_exception_is_its_own_finding(session):
    _snap(session, "ES=F", 3)                        # 数据新鲜，但扫描器本轮抛错
    statuses = {"price": [{"source": "yfinance", "ok": False, "empty": False,
                           "error": "YFRateLimitError: Too Many Requests"}]}
    findings = psm.collect_price_source_findings(session, now=NOW, source_statuses=statuses)
    kinds = {f.kind for f in findings}
    assert "price_scanner_error" in kinds
    err = next(f for f in findings if f.kind == "price_scanner_error")
    assert "YFRateLimitError" in err.content


# ---------- 推送 + 冷却 ----------

def test_push_then_cooldown_blocks_repeat(session):
    _snap(session, "ES=F", 90)
    ch = FakeChannel()
    first = psm.check_price_source_health(session=session, channel=ch, now=NOW)
    assert len(first) == 1 and first[0]["delivered"] is True
    assert len(ch.sent) == 1
    assert session.query(AlertLog).filter(AlertLog.rule_name == psm.RULE_NAME).count() == 1

    # 冷却窗内再查 → 不重复推送
    again = psm.check_price_source_health(session=session, channel=ch, now=NOW + timedelta(minutes=5))
    assert again == [] and len(ch.sent) == 1


def test_alert_repeats_after_cooldown(session):
    _snap(session, "ES=F", 90)
    ch = FakeChannel()
    psm.check_price_source_health(session=session, channel=ch, now=NOW)
    later = NOW + timedelta(minutes=config.PRICE_SOURCE_ALERT_COOLDOWN_MINUTES + 1)
    again = psm.check_price_source_health(session=session, channel=ch, now=later)
    assert len(again) == 1 and len(ch.sent) == 2


def test_failed_delivery_does_not_consume_cooldown(session):
    _snap(session, "ES=F", 90)
    dead = FakeChannel(delivered=False)
    psm.check_price_source_health(session=session, channel=dead, now=NOW)
    ok = FakeChannel()
    retry = psm.check_price_source_health(session=session, channel=ok, now=NOW + timedelta(minutes=5))
    assert len(retry) == 1 and len(ok.sent) == 1     # 上次没送达 → 不占冷却，下轮继续尝试


def test_disabled_switch_sends_nothing(session, monkeypatch):
    monkeypatch.setattr(config, "PRICE_SOURCE_MONITORING_ENABLED", False)
    _snap(session, "ES=F", 90)
    ch = FakeChannel()
    assert psm.check_price_source_health(session=session, channel=ch, now=NOW) == []
    assert ch.sent == []
