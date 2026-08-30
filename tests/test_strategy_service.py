# -*- coding: utf-8 -*-
"""strategy_service：拉取解析、CRUD、每日检查状态机、推送去重。全程内存 SQLite + 假蜡烛。"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import services.strategy_service as svc
from database import Base
from models.strategy import StrategyEvent, StrategyPosition, StrategySettings, StrategySymbolState
from services.strategy_engine import DailyCandle


@pytest.fixture()
def db(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(svc, "SessionLocal", Session)
    return Session()


def test_parse_okx_candles_keeps_confirmed_ascending():
    raw = {"code": "0", "data": [
        ["1787875200000", "0.73", "0.75", "0.70", "0.716", "1", "1", "1", "0"],   # 未确认，丢弃
        ["1787788800000", "0.752", "0.782", "0.718", "0.7323", "1", "1", "1", "1"],
        ["1787702400000", "0.739", "0.775", "0.710", "0.7518", "1", "1", "1", "1"],
    ]}
    candles = svc._parse_okx_candles(raw)
    assert [c.close for c in candles] == [0.7518, 0.7323]          # 升序 + 只留 confirm=1
    assert candles[0].date == datetime(2026, 8, 26)                 # 1787702400000 = 2026-08-26 00:00 UTC


def test_get_settings_creates_singleton(db):
    s1 = svc.get_settings(db)
    s2 = svc.get_settings(db)
    assert s1.id == s2.id and s1.capital == 13915.0 and s1.x_soft == 4


def _seed_b1(db, entry_price=0.70, qty=1000.0, entry_at=datetime(2026, 8, 1, 1, 0)):
    pos = StrategyPosition(symbol="VIRTUAL-USDT-SWAP", batch_label="B1", entry_at=entry_at,
                           entry_price=entry_price, quantity=qty, forecast=10, status="open")
    db.add(pos)
    db.commit()
    return pos


def _candles(closes, start=datetime(2026, 8, 1)):
    return [DailyCandle(date=start + timedelta(days=i), open=c, high=c, low=c, close=c)
            for i, c in enumerate(closes)]


class SpyChannel:
    def __init__(self):
        self.sent = []

    def send(self, title, content):
        self.sent.append((title, content))
        return True


def _run(db, monkeypatch, closes, channel):
    monkeypatch.setattr(svc, "fetch_daily_candles", lambda symbol: _candles(closes))
    return svc.run_daily_check(db=db, channel=channel)


def test_daily_ok_then_breach_pushes_once(db, monkeypatch):
    # 行情设计：40 天横盘热身 + 36 天每日 +1% 缓涨到 ~1.0016（EWMA 波动率 ~1%）。
    # 单日暴跌会同步推高波动率、止损自动放宽（卡弗自适应），所以破线用两日缓跌磨穿：
    # -25% 那天 soft 降到 ~0.7297，收盘 0.75 尚未破；次日 0.72 < 0.7297 => 破线。
    base = [0.70] * 40 + [round(0.70 * (1.01 ** i), 6) for i in range(1, 37)]
    _seed_b1(db, entry_price=0.98)     # 入场在高位附近，避免热身段就锁盈
    ch = SpyChannel()
    _run(db, monkeypatch, base, ch)
    kinds = [e.kind for e in db.query(StrategyEvent).all()]
    assert kinds == ["daily_ok"] and ch.sent == []
    _run(db, monkeypatch, base + [0.75, 0.72], ch)
    assert [e.kind for e in db.query(StrategyEvent).all()][-1] == "stop_breach"
    assert len(ch.sent) == 1
    state = db.query(StrategySymbolState).one()
    assert state.reentry_level is not None and state.reentry_level > 0.72
    # 次日仍破线：不重复推
    _run(db, monkeypatch, base + [0.75, 0.72, 0.71], ch)
    assert len(ch.sent) == 1


def test_b2_unlocked_fires_once(db, monkeypatch):
    base = [0.70] * 40 + [round(0.70 * (1.01 ** i), 6) for i in range(1, 37)]
    _seed_b1(db)                       # 成本 0.70，涨到 ~1.0 后 soft(~0.965) 抬过成本 => 锁盈
    ch = SpyChannel()
    _run(db, monkeypatch, base, ch)
    assert [e.kind for e in db.query(StrategyEvent).all()].count("b2_unlocked") == 1
    _run(db, monkeypatch, base + [round(base[-1] * 1.01, 6)], ch)
    assert [e.kind for e in db.query(StrategyEvent).all()].count("b2_unlocked") == 1


def test_reentry_ready_after_restand(db, monkeypatch):
    base = [0.70] * 40 + [round(0.70 * (1.01 ** i), 6) for i in range(1, 37)]
    pos = _seed_b1(db, entry_price=0.98)
    ch = SpyChannel()
    _run(db, monkeypatch, base + [0.75, 0.72], ch)                  # 破线，进观察态
    pos.status = "closed"                                           # 用户手动平仓
    db.commit()
    level = db.query(StrategySymbolState).one().reentry_level
    _run(db, monkeypatch, base + [0.75, 0.72, round(level * 1.05, 6)], ch)
    events = [e.kind for e in db.query(StrategyEvent).all()]
    assert events[-1] == "reentry_ready"
    assert db.query(StrategySymbolState).one().reentry_level is None   # 一次性，发完清除
    assert len(ch.sent) == 2                                            # breach + reentry


def test_reentry_expires_silently(db, monkeypatch):
    base = [0.70] * 40 + [round(0.70 * (1.01 ** i), 6) for i in range(1, 37)]
    _seed_b1(db, entry_price=0.98)
    ch = SpyChannel()
    _run(db, monkeypatch, base + [0.75, 0.72], ch)
    state = db.query(StrategySymbolState).one()
    state.reentry_breached_at = datetime(2026, 6, 1)                # 拨回 31+ 天前
    db.commit()
    _run(db, monkeypatch, base + [0.75, 0.72, 0.71], ch)
    events = [e.kind for e in db.query(StrategyEvent).all()]
    assert "reentry_expired" in events
    assert len(ch.sent) == 1                                            # 过期不推送
    assert db.query(StrategySymbolState).one().reentry_level is None


def test_vol_update_with_overbudget_suggests_reduce(db, monkeypatch):
    # 首轮冷启动不算"波动率变更"；次轮 -25% 暴跌把闩锁从 ~1% 顶到 ~6.8%（偏离远超 25%）
    # => vol_update；巨仓（20 万枚 @0.95）在新止损下占用远超 15% 预算 => reduce_suggest。
    base = [0.70] * 40 + [round(0.70 * (1.01 ** i), 6) for i in range(1, 37)]
    _seed_b1(db, entry_price=0.95, qty=200000.0)
    ch = SpyChannel()
    _run(db, monkeypatch, base, ch)
    _run(db, monkeypatch, base + [0.75], ch)
    kinds = [e.kind for e in db.query(StrategyEvent).all()]
    assert "vol_update" in kinds and "reduce_suggest" in kinds
