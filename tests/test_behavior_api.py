# -*- coding: utf-8 -*-
"""行为引擎三端点（price-behavior-engine-plan Task 6）：形状 + 空库不 500 + live 日汇总。"""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.app import create_app
from api.deps import get_db
from database import Base
from models.price import PriceSnapshot
from services import behavior_classifier as bc


@pytest.fixture()
def client_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    app = create_app(enable_scheduler=False)

    def _override_db():
        yield session

    app.dependency_overrides[get_db] = _override_db
    yield TestClient(app), session
    session.close()


def _seed(session):
    t0 = datetime.utcnow() - timedelta(hours=6)
    t0 = t0.replace(minute=t0.minute - t0.minute % 5, second=0, microsecond=0)
    btc = [100.0] * 18 + [100.2, 100.4, 100.6] + [100.6] * 24
    nq = [100.0] * 18 + [100.1, 100.2, 100.25] + [100.25] * 24
    for i, p in enumerate(btc):
        session.add(PriceSnapshot(timestamp=t0 + timedelta(minutes=5 * i), asset_class="crypto",
                                  symbol="BTC/USDT", name="BTC", price=p, source="test"))
    for i, p in enumerate(nq):
        session.add(PriceSnapshot(timestamp=t0 + timedelta(minutes=5 * i), asset_class="futures",
                                  symbol="NQ=F", name="纳指", price=p, source="test"))
    session.commit()
    bc.classify(session, "BTC/USDT", now=t0 + timedelta(minutes=5 * len(btc) + 160))


def test_endpoints_empty_db_no_500(client_session):
    client, _ = client_session
    for url in ["/api/behavior/segments", "/api/behavior/daily?days=3", "/api/behavior/linkage?hours=6"]:
        resp = client.get(url)
        assert resp.status_code == 200, url


def test_segments_shape(client_session):
    client, session = client_session
    _seed(session)
    body = client.get("/api/behavior/segments?days=2").json()
    assert body["symbol"] == "BTC/USDT"
    composed = [s for s in body["segments"] if s["tier_idx"] >= 1]
    assert composed
    seg = composed[0]
    assert seg["classification"] == "pure_resonance"
    assert seg["s_scores"]["NQ=F"]["s"] >= 0.5
    assert seg["max_abs_s"] >= 0.5
    assert seg["start"]["timestamp_utc"] and seg["start"]["timestamp_bj"]


def test_daily_live_and_linkage_shape(client_session):
    client, session = client_session
    _seed(session)
    daily = client.get("/api/behavior/daily?days=2").json()
    assert len(daily["days"]) == 2
    today = daily["days"][-1]
    assert today["live"] is True                      # 无 PIT 行 → 现算
    assert any(v["up"] or v["down"] for v in today["counts"].values())
    linkage = client.get("/api/behavior/linkage?hours=6").json()
    assert linkage["rolling_points"] >= 10
    syms = [s["symbol"] for s in linkage["series"]]
    assert "NQ=F" in syms
    nq = next(s for s in linkage["series"] if s["symbol"] == "NQ=F")
    assert len(nq["points"]) == len(linkage["breadth"]) > 0


def test_review_confirm_override_and_daily_priority(client_session):
    client, session = client_session
    _seed(session)
    seg = client.get("/api/behavior/segments?days=2").json()["segments"]
    target = next(s for s in seg if s["tier_idx"] >= 1)
    assert target["human_class"] is None
    # 确认（写机器类）
    r = client.patch(f"/api/behavior/segments/{target['id']}", json={"human_class": target["classification"]})
    assert r.status_code == 200 and r.json()["human_class"] == "pure_resonance"
    # 改判 → 构成聚合优先人工结论
    r = client.patch(f"/api/behavior/segments/{target['id']}", json={"human_class": "sentiment"})
    assert r.status_code == 200
    today = client.get("/api/behavior/daily?days=1").json()["days"][-1]
    assert today["composition"]["sentiment"] == 1
    assert today["composition"]["pure_resonance"] == 0
    # 撤销 → 回机器类
    r = client.patch(f"/api/behavior/segments/{target['id']}", json={"human_class": None})
    assert r.status_code == 200 and r.json()["human_class"] is None
    today = client.get("/api/behavior/daily?days=1").json()["days"][-1]
    assert today["composition"]["pure_resonance"] == 1
    # 非法类别 400 / 不存在 404
    assert client.patch(f"/api/behavior/segments/{target['id']}", json={"human_class": "count_only"}).status_code == 400
    assert client.patch("/api/behavior/segments/999999", json={"human_class": "sentiment"}).status_code == 404
