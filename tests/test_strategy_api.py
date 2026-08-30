# -*- coding: utf-8 -*-
"""strategy 路由冒烟：CRUD + overview + simulate（拉取全程 monkeypatch，不出网）。

注意：TestClient 挂的是真实本地 market_monitor.db（项目 API 测试现状），
所以本文件建的批次要删干净、参数改完要还原。
"""
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

import services.strategy_service as svc
from api.app import create_app
from services.strategy_engine import DailyCandle


def _client():
    # 无调度器模式不跑 create_tables；幂等补建新表（生产由 lifespan 的 create_tables 完成）
    from database import create_tables
    create_tables(run_migrations=False, seed_defaults=False)
    return TestClient(create_app(enable_scheduler=False))


def _fake_candles(symbol):
    start = datetime(2026, 8, 25)
    return [DailyCandle(date=start + timedelta(days=i), open=c, high=c, low=c, close=c)
            for i, c in enumerate([0.70, 0.7518, 0.7323])]


def test_positions_crud_and_overview(monkeypatch):
    monkeypatch.setattr(svc, "fetch_daily_candles", _fake_candles)
    c = _client()

    created = c.post("/api/strategy/positions", json={
        "symbol": "VIRTUAL-USDT-SWAP", "batch_label": "B1-测试",
        "entry_at": "2026-08-26T23:33:00", "entry_price": 0.743,
        "quantity": 23590, "forecast": 10,
    })
    assert created.status_code == 200
    pos_id = created.json()["id"]
    settings_before = None
    try:
        ov = c.get("/api/strategy/overview").json()
        assert ov["verdict"] in ("hold", "breach")           # 真实库可能还有别的批次
        assert any(b["id"] == pos_id for b in ov["batches"])

        patched = c.patch(f"/api/strategy/positions/{pos_id}", json={"forecast": 15})
        assert patched.json()["forecast"] == 15

        sim = c.post("/api/strategy/simulate", json={"price": 0.70, "forecast": 15, "vol": 0.05})
        assert abs(sim.json()["stop_price"] - 0.56) < 1e-9

        settings_before = c.get("/api/strategy/settings").json()
        updated = c.put("/api/strategy/settings", json={**settings_before, "capital": 20000})
        assert updated.json()["capital"] == 20000

        events = c.get("/api/strategy/events")
        assert events.status_code == 200
    finally:
        if settings_before is not None:
            c.put("/api/strategy/settings", json=settings_before)    # 还原参数
        deleted = c.delete(f"/api/strategy/positions/{pos_id}")
        assert deleted.status_code == 200
    assert c.get("/api/strategy/positions").status_code == 200
