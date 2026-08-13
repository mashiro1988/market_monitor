# -*- coding: utf-8 -*-
"""AI 梳理(pool sweep,2026-08-13 design):防幻觉解析、立案/补挂落库、审计口径、早退。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models.news import NewsItem
from models.research import ResearchEvent, ResearchEventLink
from services import pool_sweep
from services.event_pool import create_event
from services.time_utils import utc_now_naive


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _news(s, title, market="macro", score=8, hours_ago=2, crypto=False):
    now = utc_now_naive()
    n = NewsItem(timestamp=now - timedelta(hours=hours_ago), source="jin10",
                 title=title, content="", language="zh", llm_importance=score,
                 tagged_at=now, market=market, is_crypto_affair=crypto)
    s.add(n); s.commit()
    return n


def test_parse_drops_hallucinated_and_clamps():
    raw = json.dumps({
        "new_events": [
            {"name": "真事件", "keywords": ["词一", "词二", "x"], "news_ids": [1, 2, 999]},
            {"name": "成员不足", "news_ids": [1]},
            {"name": "", "news_ids": [1, 2]},
        ],
        "attach": [
            {"news_id": 3, "event_id": 7, "confidence": 0.9},
            {"news_id": 999, "event_id": 7, "confidence": 0.9},   # 幻觉新闻 id
            {"news_id": 3, "event_id": 99, "confidence": 0.9},    # 幻觉事件 id
            {"news_id": 3, "event_id": 7, "confidence": 0.5},     # 非法档位
        ],
    })
    out = pool_sweep._parse_sweep(raw, valid_news_ids={1, 2, 3}, valid_event_ids={7})
    assert [e["name"] for e in out["new_events"]] == ["真事件"]
    assert out["new_events"][0]["news_ids"] == [1, 2]              # 幻觉成员被剔
    assert out["new_events"][0]["keywords"] == ["词一", "词二"]     # 单字被剔(spec §5.2)
    assert out["attach"] == [{"news_id": 3, "event_id": 7, "confidence": 0.9}]


def test_parse_rejects_non_json():
    with pytest.raises(ValueError):
        pool_sweep._parse_sweep("抱歉我做不到", set(), set())


def test_run_sweep_rejects_bad_event_type(session):
    with pytest.raises(ValueError):
        pool_sweep.run_sweep(session, event_type="stocks")


def test_run_sweep_creates_attaches_and_audits(session, monkeypatch):
    exist = create_event(session, "已有事件", news_ids=[_news(session, "种子").id])
    a = _news(session, "同主题一")
    b = _news(session, "同主题二")
    c = _news(session, "漏网证据")
    canned = json.dumps({
        "new_events": [{"name": "新聚出的事件", "keywords": ["主题词"],
                        "news_ids": [a.id, b.id], "why": "同主题反复出现"}],
        "attach": [{"news_id": c.id, "event_id": exist.id, "confidence": 0.65}],
    })
    monkeypatch.setattr(pool_sweep, "_call_sweep", lambda payload: (canned, 9.9))
    out = pool_sweep.run_sweep(session, event_type="macro")
    assert (out["scanned"], out["attached"], len(out["created"])) == (3, 1, 1)
    e = session.query(ResearchEvent).filter_by(name="新聚出的事件").one()
    assert (e.created_from, e.event_type) == ("sweep", "macro")
    links = session.query(ResearchEventLink).filter_by(event_id=e.id).all()
    assert {l.news_id for l in links} == {a.id, b.id}
    # 梳理产物必须进纠错率审计口径:auto + auto_event_id + prompt_version
    assert all((l.link_source, l.auto_event_id, l.prompt_version)
               == ("auto", e.id, pool_sweep.SWEEP_PROMPT_VERSION) for l in links)
    add = session.query(ResearchEventLink).filter_by(event_id=exist.id, news_id=c.id).one()
    assert (add.link_source, add.confidence) == ("auto", 0.65)


def test_run_sweep_same_name_becomes_attach(session, monkeypatch):
    create_event(session, "美联储议息", news_ids=[_news(session, "种子").id])
    a, b = _news(session, "又见议息"), _news(session, "还是议息")
    canned = json.dumps({"new_events": [{"name": "美联储议息", "keywords": ["FOMC"],
                                         "news_ids": [a.id, b.id]}], "attach": []})
    monkeypatch.setattr(pool_sweep, "_call_sweep", lambda payload: (canned, 1.0))
    out = pool_sweep.run_sweep(session, event_type="macro")
    # 模型重新发明现有事件 → 不重复立案,成员降级为补挂证据
    assert out["created"] == [] and out["attached"] == 2
    assert session.query(ResearchEvent).count() == 1


def test_run_sweep_dry_run_writes_nothing(session, monkeypatch):
    a, b = _news(session, "一"), _news(session, "二")
    canned = json.dumps({"new_events": [{"name": "草稿事件", "keywords": ["词词"],
                                         "news_ids": [a.id, b.id], "why": "w"}], "attach": []})
    monkeypatch.setattr(pool_sweep, "_call_sweep", lambda payload: (canned, 1.0))
    out = pool_sweep.run_sweep(session, event_type="macro", dry_run=True)
    assert out["dry_run"] and out["created"][0]["name"] == "草稿事件"
    assert session.query(ResearchEvent).count() == 0


def test_run_sweep_no_news_skips_llm(session, monkeypatch):
    def boom(_):
        raise AssertionError("空输入不该调 LLM")
    monkeypatch.setattr(pool_sweep, "_call_sweep", boom)
    out = pool_sweep.run_sweep(session, event_type="macro")
    assert out["scanned"] == 0 and out["created"] == []


def test_run_sweep_crypto_semantic_gate(session, monkeypatch):
    # 加密线语义闸(web3 二期A design §3):加密源转载的纯宏观不进梳理
    on = _news(session, "币圈事务", market="crypto", score=None, crypto=True)
    _news(session, "转载宏观", market="crypto", score=None, crypto=False)
    seen = {}

    def spy(payload):
        seen["payload"] = payload
        return json.dumps({"new_events": [], "attach": []}), 1.0

    monkeypatch.setattr(pool_sweep, "_call_sweep", spy)
    out = pool_sweep.run_sweep(session, event_type="crypto")
    assert out["scanned"] == 1
    assert f'"id": {on.id}' in seen["payload"]
