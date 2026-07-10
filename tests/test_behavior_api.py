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
    assert daily["days"][-1]["live"] is True          # 无 PIT 行 → 现算
    # 种子段在 utcnow-6h~-2h，UTC 午夜后运行会落在昨日——按日找而不是赌 today
    seeded = [d for d in daily["days"] if any(v["up"] or v["down"] for v in d["counts"].values())]
    assert len(seeded) == 1 and seeded[0]["live"] is True
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
    # 确认（机器六类归并三类写入：pure_resonance → pure_resonance）
    r = client.patch(f"/api/behavior/segments/{target['id']}", json={"human_class": target["classification"]})
    assert r.status_code == 200 and r.json()["human_class"] == "pure_resonance"
    # 段落在哪个 UTC 日取决于运行时刻（UTC 午夜后 utcnow-6h 落昨日）——按段起点日期取行
    seg_date = target["start"]["timestamp_utc"][:10]

    def _seg_day():
        days = client.get("/api/behavior/daily?days=2").json()["days"]
        return next(d for d in days if d["utc_date"] == seg_date)

    # 改判 → 构成聚合优先人工结论（三类口径）
    r = client.patch(f"/api/behavior/segments/{target['id']}", json={"human_class": "sentiment_tech"})
    assert r.status_code == 200
    day = _seg_day()
    assert day["composition"]["sentiment_tech"] == 1
    assert day["composition"]["pure_resonance"] == 0
    assert "no_ref" in day["composition"]                       # 无对照注记键恒在
    # 撤销 → 回机器类（归并后仍是 pure_resonance）
    r = client.patch(f"/api/behavior/segments/{target['id']}", json={"human_class": None})
    assert r.status_code == 200 and r.json()["human_class"] is None
    assert _seg_day()["composition"]["pure_resonance"] == 1
    # 非法类别 400 / 不存在 404
    assert client.patch(f"/api/behavior/segments/{target['id']}", json={"human_class": "count_only"}).status_code == 400
    assert client.patch("/api/behavior/segments/999999", json={"human_class": "sentiment_tech"}).status_code == 404


def test_linkage_range_follows_window(client_session):
    """联动曲线跟随标注窗口（2026-07-10 拍板：窗口±24h；超出最新数据则贴到最新点）。"""
    client, session = client_session
    _seed(session)
    t0 = datetime.utcnow() - timedelta(hours=6)
    t0 = t0.replace(minute=t0.minute - t0.minute % 5, second=0, microsecond=0)
    start = (t0 + timedelta(minutes=30)).isoformat()
    end = (t0 + timedelta(minutes=90)).isoformat()
    body = client.get(f"/api/behavior/linkage?start_utc={start}&end_utc={end}").json()
    nq = next(s_ for s_ in body["series"] if s_["symbol"] == "NQ=F")
    assert 0 < len(nq["points"]) <= 13            # 60min/5min + 1，区间被尊重
    # end 超出最新数据 → 网格贴到最新点收口，不吐一堆空尾巴
    late_end = (datetime.utcnow() + timedelta(hours=24)).isoformat()
    body2 = client.get(f"/api/behavior/linkage?start_utc={start}&end_utc={late_end}").json()
    nq2 = next(s_ for s_ in body2["series"] if s_["symbol"] == "NQ=F")
    seeded_last = t0 + timedelta(minutes=5 * 44)   # 种子数据最后一根 bar
    assert len(nq2["points"]) <= ((seeded_last - (t0 + timedelta(minutes=30))).total_seconds() / 300) + 2
