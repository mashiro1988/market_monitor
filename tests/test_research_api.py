# -*- coding: utf-8 -*-
"""研究事件池 API(news-research-phase1 spec §9.3)。

与 test_api.py 不同:这些端点要写库,不能用仓库本地开发库——
用 FastAPI dependency_overrides 把 get_db 指到临时 SQLite(不动 sys.modules,
避免污染同进程里其它测试模块)。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.app import create_app
from api.deps import get_db
from database import Base
from models.news import NewsItem


@pytest.fixture()
def client(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'api.db').as_posix()}")
    Base.metadata.create_all(bind=engine)
    test_session = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    app = create_app(enable_scheduler=False)

    def override_get_db():
        s = test_session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override_get_db
    c = TestClient(app)
    c.test_sessionmaker = test_session          # 给 _mk_news 直插数据用
    return c


def _mk_news(client, title="种子新闻", score=8, ts=datetime(2026, 8, 1, 12, 0)):
    s = client.test_sessionmaker()
    n = NewsItem(timestamp=ts, source="jin10", title=title,
                 language="zh", llm_importance=score, tagged_at=datetime(2026, 8, 1, 12, 1))
    s.add(n); s.commit(); nid = n.id; s.close()
    return nid


def test_create_list_timeline_roundtrip(client):
    nid = _mk_news(client)
    r = client.post("/api/research/events", json={
        "name": "苹果调价", "news_ids": [nid], "gate_keywords": "苹果、Apple",
        "created_from": "manual"})
    assert r.status_code == 200, r.text
    eid = r.json()["id"]
    rows = client.get("/api/research/events").json()["items"]
    assert rows[0]["id"] == eid and rows[0]["evidence_count"] == 1
    tl = client.get(f"/api/research/events/{eid}/timeline").json()
    assert tl["event"]["name"] == "苹果调价"
    assert tl["items"][0]["news"]["id"] == nid


def test_create_requires_news(client):
    r = client.post("/api/research/events", json={"name": "空壳", "news_ids": []})
    assert r.status_code == 400


def test_patch_close_reopen_merge(client):
    n1, n2 = _mk_news(client, "a"), _mk_news(client, "b")
    e1 = client.post("/api/research/events", json={"name": "A", "news_ids": [n1]}).json()["id"]
    e2 = client.post("/api/research/events", json={"name": "B", "news_ids": [n2]}).json()["id"]
    assert client.patch(f"/api/research/events/{e1}",
                        json={"status": "closed", "closed_reason": "测试关闭"}).status_code == 200
    assert client.patch(f"/api/research/events/{e1}",
                        json={"status": "active"}).status_code == 200
    r = client.patch(f"/api/research/events/{e1}", json={"merge_into_id": e2})
    assert r.status_code == 200
    rows = {x["id"]: x for x in client.get("/api/research/events?status=closed").json()["items"]}
    assert rows[e1]["merged_into_id"] == e2


def test_links_attach_detach(client):
    nid = _mk_news(client)
    n2 = _mk_news(client, "第二条")
    eid = client.post("/api/research/events", json={"name": "E", "news_ids": [nid]}).json()["id"]
    r = client.post("/api/research/links", json={"event_id": eid, "news_id": n2})
    assert r.status_code == 200
    link_id = r.json()["id"]
    assert client.patch(f"/api/research/links/{link_id}",
                        json={"detached": True, "detach_reason": "挂错"}).status_code == 200
    briefs = client.get(f"/api/research/news/{n2}/links").json()["items"]
    assert briefs == []                                   # 摘下后不再显示


def test_events_expose_yesterday_and_bj_time(client):
    """卡片字段(ui-redesign §6.1)必须过得了响应模型这一关。"""
    nid = _mk_news(client)
    client.post("/api/research/events", json={"name": "E", "news_ids": [nid]})
    row = client.get("/api/research/events").json()["items"][0]
    assert row["yesterday_new"] == 0                       # 刚建的挂接落在今天
    assert row["last_evidence_bj"] == "2026-08-01 20:00:00"   # 12:00 UTC + 8h
    assert row["last_evidence_at"].startswith("2026-08-01T12:00")


def test_timeline_route_defaults_to_50_per_page(client):
    """路由层默认分页 = 每页 50(服务层仍是全量,replay 脚本不受影响)。"""
    ids = [_mk_news(client, f"n{i}", ts=datetime(2026, 8, 1, 12, i)) for i in range(3)]
    eid = client.post("/api/research/events", json={"name": "E", "news_ids": ids}).json()["id"]
    tl = client.get(f"/api/research/events/{eid}/timeline").json()
    assert (tl["total"], tl["page"], tl["page_size"]) == (3, 1, 50)
    assert len(tl["items"]) == 3


def test_timeline_route_filters_and_pages(client):
    ids = [_mk_news(client, f"n{i}", score=3 if i == 0 else 8,
                    ts=datetime(2026, 8, 1, 12, i)) for i in range(3)]
    eid = client.post("/api/research/events", json={"name": "E", "news_ids": ids}).json()["id"]

    scored = client.get(f"/api/research/events/{eid}/timeline?min_score=6").json()
    assert scored["total"] == 2

    page2 = client.get(f"/api/research/events/{eid}/timeline?page=2&page_size=2").json()
    assert (page2["total"], page2["page"], len(page2["items"])) == (3, 2, 1)

    bad = client.get(f"/api/research/events/{eid}/timeline?page_size=999")
    assert bad.status_code == 422                          # page_size 上限 200


def test_buffer_revival_stats_endpoints(client):
    _mk_news(client)
    assert client.get("/api/research/buffer?days=3").status_code == 200
    assert client.get("/api/research/revival").status_code == 200
    stats = client.get("/api/research/stats").json()
    assert "link_rate" in stats and "correction_rate" in stats


def test_market_sweep_apply_and_event_markets_roundtrip(client, monkeypatch):
    """市场提案 API 闭环(2026-08-28):提案(mock 管线)→ 采纳 → 事件市场卡 → 摘下。"""
    from models.research import ResearchEvent
    from services import market_sweep
    s = client.test_sessionmaker()
    event = ResearchEvent(name="俄乌停火", event_type="macro", status="active", display_no=9)
    s.add(event); s.commit(); eid = event.id; s.close()
    canned = {"event_type": "macro", "scanned_events": 1, "searched_terms": 1,
              "candidates": 1, "dropped_price_targets": 0, "duration_seconds": 1.0,
              "proposals": [{"event_id": eid, "event_name": "俄乌停火",
                             "slug": "ceasefire-2026", "title": "Ceasefire in 2026?",
                             "current_probability": 0.41, "market_count": 1,
                             "volume": 500000.0, "end_date": "2026-12-31",
                             "confidence": 0.9, "reason": "就是这件事"}]}
    monkeypatch.setattr(market_sweep, "run_market_sweep", lambda *a, **k: canned)
    r = client.post("/api/research/market-sweep", json={"event_type": "macro"})
    assert r.status_code == 200 and r.json()["proposals"][0]["slug"] == "ceasefire-2026"

    r = client.post("/api/research/market-sweep/apply",
                    json={"event_type": "macro", "items": canned["proposals"]})
    assert r.status_code == 200
    assert r.json()["added"] == ["ceasefire-2026"] and r.json()["linked"] == 1

    r = client.get(f"/api/research/events/{eid}/markets")
    assert r.status_code == 200
    item = r.json()["items"][0]
    assert item["slug"] == "ceasefire-2026" and item["waiting_first_scan"] is True

    r = client.post(f"/api/research/event-markets/{item['link_id']}/detach",
                    json={"reason": "试摘"})
    assert r.status_code == 200
    assert client.get(f"/api/research/events/{eid}/markets").json()["items"] == []


def test_predictions_search_proxy(client, monkeypatch):
    from services import market_sweep
    monkeypatch.setattr(market_sweep, "search_markets", lambda q: [{
        "slug": "s", "title": "T?", "description": "", "volume": 1.0,
        "end_date": "2026-01-01", "market_count": 1, "current_probability": 0.5}])
    r = client.get("/api/predictions/search", params={"q": "fed"})
    assert r.status_code == 200 and r.json()[0]["slug"] == "s"
