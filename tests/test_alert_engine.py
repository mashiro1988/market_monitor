"""Tests for alert message formatting."""
import os
import sys
from datetime import datetime
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import alerts.engine as engine_module
from alerts.engine import AlertEngine, PriceWindowMove
from alerts.rules import AlertRule
from chart_utils import format_beijing_time
from scanners.base import NewsRecord, PredictionRecord, PriceRecord
import config


def test_format_news_line_uses_beijing_time():
    record = NewsRecord(
        source="jin10",
        source_id="1",
        title="重要新闻",
        importance=1,
        published_at=datetime(2026, 4, 23, 6, 42, 29),
    )

    line = AlertEngine._format_news_line(record)

    assert "04-23 14:42 北京时间" in line
    assert "重要新闻" in line
    assert "Jin10重要:是" in line


def test_jin10_source_important_triggers_news_alert():
    record = NewsRecord(
        source="jin10",
        source_id="1",
        title="金十标注重要",
        importance=1,
        llm_importance=3,
    )

    assert AlertEngine._is_important_news(record, 8) is True


def test_non_jin10_low_llm_does_not_trigger_news_alert():
    record = NewsRecord(
        source="bloomberg",
        source_id="1",
        title="普通新闻",
        importance=None,
        llm_importance=3,
    )

    assert AlertEngine._is_important_news(record, 8) is False


def test_format_beijing_time_for_hourly_summary_title():
    assert format_beijing_time(datetime(2026, 4, 23, 6, 42, 29), "%H:%M") == "14:42"


def test_price_window_change_uses_configured_15m_window(monkeypatch):
    base = SimpleNamespace(timestamp=datetime(2026, 4, 26, 18, 0), price=2348.41)

    class FakeQuery:
        def filter(self, *args):
            return self

        def all(self):
            return [base]

    class FakeSession:
        def query(self, model):
            return FakeQuery()

        def close(self):
            pass

    monkeypatch.setattr(engine_module, "get_session", lambda: FakeSession())

    record = PriceRecord(
        asset_class="crypto",
        symbol="ETH/USDT",
        name="ETH",
        price=2361.99,
        change_pct=0.3551960163661949,
        timestamp=datetime(2026, 4, 26, 18, 15),
    )

    assert AlertEngine._price_window_change_pct(record, 15) == pytest.approx(0.5783, rel=1e-4)


def test_price_alert_message_includes_time_window_and_price_range(monkeypatch):
    engine = AlertEngine.__new__(AlertEngine)
    engine.rules = [
        AlertRule(
            name="eth_price_spike",
            rule_type="price_change",
            params={"symbol": "ETH/USDT", "threshold_pct": 0.5, "window_minutes": 15},
            channels=["wechat_work"],
            cooldown_minutes=0,
            enabled=True,
        )
    ]

    move = PriceWindowMove(
        change_pct=0.5782635911105781,
        start_time=datetime(2026, 4, 26, 18, 0),
        end_time=datetime(2026, 4, 26, 18, 15),
        start_price=2348.41,
        end_price=2361.99,
        low_price=2348.41,
        high_price=2361.99,
    )
    monkeypatch.setattr(engine, "_is_in_cooldown", lambda rule_name, cooldown: False)
    monkeypatch.setattr(engine, "_price_window_move", lambda record, window: move)

    sent = []
    monkeypatch.setattr(engine, "_dispatch", lambda rule, title, content: sent.append((title, content)))

    engine.evaluate_prices([
        PriceRecord(
            asset_class="crypto",
            symbol="ETH/USDT",
            name="ETH",
            price=2361.99,
            timestamp=datetime(2026, 4, 26, 18, 15),
        )
    ])

    assert sent
    title, content = sent[0]
    assert "15m / 0.5%" in title
    assert "时间区间: 04-27 02:00-02:15 北京时间" in content
    assert "价格: $2,348.41 → $2,361.99" in content
    assert "区间 $2,348.41-$2,361.99" in content


def test_prediction_alert_uses_saved_prev_probability(monkeypatch):
    engine = AlertEngine.__new__(AlertEngine)
    engine.rules = [
        AlertRule(
            name="prediction_shift",
            rule_type="prediction_shift",
            params={"threshold_pct": 5.0, "window_minutes": 15},
            channels=["wechat_work"],
            cooldown_minutes=0,
            enabled=True,
        )
    ]

    latest = SimpleNamespace(probability=0.495, prev_probability=0.043)

    class FakeQuery:
        def filter(self, *args):
            return self

        def order_by(self, *args):
            return self

        def first(self):
            return latest

    class FakeSession:
        def query(self, model):
            return FakeQuery()

        def close(self):
            pass

    monkeypatch.setattr(engine_module, "get_session", lambda: FakeSession())
    monkeypatch.setattr(engine, "_is_in_cooldown", lambda rule_name, cooldown: False)

    sent = []
    monkeypatch.setattr(engine, "_dispatch", lambda rule, title, content: sent.append((title, content)))

    engine.evaluate_predictions([
        PredictionRecord(
            market_id="m1",
            question="Will inflation reach more than 10% in 2026?",
            outcome="Yes",
            probability=0.495,
        )
    ])

    assert sent
    title, content = sent[0]
    assert "预测市场异动" in title
    assert "4.3% → 49.5%" in content
    assert "45.2%" in content


def test_hourly_threshold_summary_uses_market_overview_default_symbols(monkeypatch):
    engine = AlertEngine.__new__(AlertEngine)
    engine.rules = [
        AlertRule(
            name="nq_price_spike",
            rule_type="price_change",
            params={"symbol": "NQ=F", "threshold_pct": 0.3, "window_minutes": 15},
            channels=["wechat_work"],
            cooldown_minutes=0,
            enabled=True,
        ),
        AlertRule(
            name="eth_price_spike",
            rule_type="price_change",
            params={"symbol": "ETH/USDT", "threshold_pct": 0.5, "window_minutes": 15},
            channels=["wechat_work"],
            cooldown_minutes=0,
            enabled=True,
        ),
    ]

    snapshots = [
        SimpleNamespace(
            symbol="NQ=F",
            name="纳指期货",
            asset_class="futures",
            timestamp=datetime(2026, 4, 28, 1, 0),
            price=100.4,
        ),
        SimpleNamespace(
            symbol="ETH/USDT",
            name="ETH",
            asset_class="crypto",
            timestamp=datetime(2026, 4, 28, 1, 0),
            price=2400.0,
        ),
    ]

    class FakeQuery:
        def __init__(self, rows):
            self.rows = list(rows)

        def filter(self, *conditions):
            for condition in conditions:
                column_name = getattr(condition.left, "name", None)
                value = getattr(condition.right, "value", None)
                op_name = getattr(condition.operator, "__name__", "")
                if column_name == "symbol" and op_name == "eq":
                    self.rows = [row for row in self.rows if row.symbol == value]
                elif column_name == "timestamp" and op_name == "ge":
                    self.rows = [row for row in self.rows if row.timestamp >= value]
                elif column_name == "timestamp" and op_name == "le":
                    self.rows = [row for row in self.rows if row.timestamp <= value]
            return self

        def order_by(self, *args):
            return self

        def all(self):
            return self.rows

    class FakeSession:
        def query(self, model):
            return FakeQuery(snapshots)

    moves = {
        "NQ=F": PriceWindowMove(
            change_pct=0.42,
            start_time=datetime(2026, 4, 28, 0, 45),
            end_time=datetime(2026, 4, 28, 1, 0),
            start_price=100.0,
            end_price=100.4,
            low_price=100.0,
            high_price=100.4,
        ),
        "ETH/USDT": PriceWindowMove(
            change_pct=0.9,
            start_time=datetime(2026, 4, 28, 0, 45),
            end_time=datetime(2026, 4, 28, 1, 0),
            start_price=2380.0,
            end_price=2400.0,
            low_price=2380.0,
            high_price=2400.0,
        ),
    }
    monkeypatch.setattr(
        AlertEngine,
        "_price_window_move_for_snapshot",
        staticmethod(lambda session, snapshot, window: moves.get(snapshot.symbol)),
    )

    summaries = engine._hourly_price_threshold_summaries(
        session=FakeSession(),
        symbols=config.MARKET_OVERVIEW_DEFAULT_SYMBOLS,
        since=datetime(2026, 4, 28, 0, 0),
        until=datetime(2026, 4, 28, 1, 5),
    )

    assert [summary.symbol for summary in summaries] == ["NQ=F"]
    assert summaries[0].trigger_count == 1
    assert summaries[0].strongest_move.change_pct == pytest.approx(0.42)
