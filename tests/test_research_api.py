# -*- coding: utf-8 -*-
"""研究事件池 API(news-research-phase1 spec §9.3)。

与 test_api.py 不同:这些端点要写库,不能用仓库本地开发库——
用临时 SQLite + 模块重载隔离(config.DATABASE_URL 在 import 时固化,必须重载)。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'api.db').as_posix()}")
    for mod in list(sys.modules):
        if mod in ("database", "config") or mod.startswith(
                ("models", "api", "services", "schemas", "alerts", "scanners")):
            sys.modules.pop(mod)
    from database import create_tables
    create_tables(run_migrations=True, seed_defaults=False)
    from api.app import create_app
    return TestClient(create_app(enable_scheduler=False))


def _mk_news(title="种子新闻"):
    from database import SessionLocal
    from models.news import NewsItem
    s = SessionLocal()
    n = NewsItem(timestamp=datetime(2026, 8, 1, 12, 0), source="jin10", title=title,
                 language="zh", llm_importance=8, tagged_at=datetime(2026, 8, 1, 12, 1))
    s.add(n); s.commit(); nid = n.id; s.close()
    return nid


def test_create_list_timeline_roundtrip(client):
    nid = _mk_news()
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
    n1, n2 = _mk_news("a"), _mk_news("b")
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
    nid = _mk_news()
    n2 = _mk_news("第二条")
    eid = client.post("/api/research/events", json={"name": "E", "news_ids": [nid]}).json()["id"]
    r = client.post("/api/research/links", json={"event_id": eid, "news_id": n2})
    assert r.status_code == 200
    link_id = r.json()["id"]
    assert client.patch(f"/api/research/links/{link_id}",
                        json={"detached": True, "detach_reason": "挂错"}).status_code == 200
    briefs = client.get(f"/api/research/news/{n2}/links").json()["items"]
    assert briefs == []                                   # 摘下后不再显示


def test_buffer_revival_stats_endpoints(client):
    _mk_news()
    assert client.get("/api/research/buffer?days=3").status_code == 200
    assert client.get("/api/research/revival").status_code == 200
    stats = client.get("/api/research/stats").json()
    assert "link_rate" in stats and "correction_rate" in stats
