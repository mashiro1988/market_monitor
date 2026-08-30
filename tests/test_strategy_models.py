# -*- coding: utf-8 -*-
"""strategy 四张表能建表、能写读（冒烟）。"""
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models.strategy import (
    StrategyEvent,
    StrategyPosition,
    StrategySettings,
    StrategySymbolState,
)


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def test_tables_roundtrip():
    s = _session()
    s.add(StrategyPosition(
        symbol="VIRTUAL-USDT-SWAP", batch_label="B1",
        entry_at=datetime(2026, 8, 26, 23, 33), entry_price=0.743,
        quantity=23590, forecast=10, status="open",
    ))
    s.add(StrategySettings(capital=13915.0, risk_budget_pct=0.15))
    s.add(StrategySymbolState(symbol="VIRTUAL-USDT-SWAP", v_used=0.0494,
                              v_used_at=datetime(2026, 8, 28)))
    s.add(StrategyEvent(symbol="VIRTUAL-USDT-SWAP", kind="daily_ok",
                        message="未破线", payload_json="{}"))
    s.commit()

    pos = s.query(StrategyPosition).one()
    assert pos.status == "open" and pos.quantity == 23590
    assert s.query(StrategySettings).one().x_soft == 4          # 默认值生效
    assert s.query(StrategySymbolState).one().reentry_level is None
    assert s.query(StrategyEvent).one().pushed is False
