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


def test_run_sweep_proposes_and_attaches(session, monkeypatch):
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
    # 提案制(2026-08-15):新事件不落库、只出提案(带成员摘要);补挂照常自动落
    assert (out["scanned"], out["attached"], len(out["proposals"])) == (3, 1, 1)
    p = out["proposals"][0]
    assert p["name"] == "新聚出的事件" and p["news_ids"] == [a.id, b.id]
    assert [n["id"] for n in p["news"]] == [a.id, b.id] and p["news"][0]["title"]
    assert session.query(ResearchEvent).count() == 1          # 只有原来那个
    add = session.query(ResearchEventLink).filter_by(event_id=exist.id, news_id=c.id).one()
    assert (add.link_source, add.confidence, add.prompt_version) == (
        "auto", 0.65, pool_sweep.SWEEP_PROMPT_VERSION)


def test_apply_proposals_creates_with_provenance(session):
    a, b = _news(session, "一"), _news(session, "二")
    out = pool_sweep.apply_proposals(session, "macro", [
        {"name": "人批准的事件", "keywords": ["词词", "x"], "news_ids": [a.id, b.id, a.id]},
    ])
    assert out["skipped_existing"] == [] and out["created"][0]["news_count"] == 2
    e = session.query(ResearchEvent).filter_by(name="人批准的事件").one()
    assert (e.created_from, e.gate_keywords) == ("sweep", "词词")   # 单字剔、重复 id 去重
    links = session.query(ResearchEventLink).filter_by(event_id=e.id).all()
    assert len(links) == 2
    # 采纳落库的种子链必须进纠错率审计口径:auto + auto_event_id + prompt_version
    assert all((l.link_source, l.auto_event_id, l.prompt_version)
               == ("auto", e.id, pool_sweep.SWEEP_PROMPT_VERSION) for l in links)


def test_apply_proposals_skips_existing_and_validates(session):
    create_event(session, "已有事件", news_ids=[_news(session, "种子").id])
    n = _news(session, "x")
    out = pool_sweep.apply_proposals(session, "macro",
                                     [{"name": "已有事件", "news_ids": [n.id]}])
    assert out["created"] == [] and out["skipped_existing"] == ["已有事件"]
    with pytest.raises(ValueError):
        pool_sweep.apply_proposals(session, "macro", [{"name": "", "news_ids": [n.id]}])
    with pytest.raises(ValueError):
        pool_sweep.apply_proposals(session, "stocks", [])


def test_run_sweep_same_name_becomes_attach(session, monkeypatch):
    create_event(session, "美联储议息", news_ids=[_news(session, "种子").id])
    a, b = _news(session, "又见议息"), _news(session, "还是议息")
    canned = json.dumps({"new_events": [{"name": "美联储议息", "keywords": ["FOMC"],
                                         "news_ids": [a.id, b.id]}], "attach": []})
    monkeypatch.setattr(pool_sweep, "_call_sweep", lambda payload: (canned, 1.0))
    out = pool_sweep.run_sweep(session, event_type="macro")
    # 模型重新发明现有事件 → 不进提案,成员降级为补挂证据
    assert out["proposals"] == [] and out["attached"] == 2
    assert session.query(ResearchEvent).count() == 1


def test_run_sweep_dry_run_writes_nothing(session, monkeypatch):
    exist = create_event(session, "已有事件", news_ids=[_news(session, "种子").id])
    a, b, c = _news(session, "一"), _news(session, "二"), _news(session, "三")
    canned = json.dumps({"new_events": [{"name": "草稿事件", "keywords": ["词词"],
                                         "news_ids": [a.id, b.id], "why": "w"}],
                         "attach": [{"news_id": c.id, "event_id": exist.id,
                                     "confidence": 0.9}]})
    monkeypatch.setattr(pool_sweep, "_call_sweep", lambda payload: (canned, 1.0))
    out = pool_sweep.run_sweep(session, event_type="macro", dry_run=True)
    assert out["dry_run"] and out["proposals"][0]["name"] == "草稿事件"
    assert out["attached"] == 1        # dry_run 连补挂也只计数不落库
    assert session.query(ResearchEvent).count() == 1
    assert session.query(ResearchEventLink).filter_by(news_id=c.id).count() == 0


def test_run_sweep_vetoes_deleted_names(session, monkeypatch):
    from services.event_pool import delete_event
    dead = create_event(session, "垃圾主题", news_ids=[_news(session, "种子").id])
    delete_event(session, dead.id)          # 种子被摘下退回缓冲区
    a, b = _news(session, "一"), _news(session, "二")
    canned = json.dumps({"new_events": [{"name": "垃圾主题", "keywords": ["词词"],
                                         "news_ids": [a.id, b.id]}], "attach": []})
    seen = {}

    def spy(payload):
        seen["payload"] = payload
        return canned, 1.0

    monkeypatch.setattr(pool_sweep, "_call_sweep", spy)
    out = pool_sweep.run_sweep(session, event_type="macro")
    # 撞否决清单:不进提案,计数不静默;提示词里也带了否决清单
    assert out["vetoed"] == 1 and out["proposals"] == []
    assert "已否决主题" in seen["payload"] and "垃圾主题" in seen["payload"]
    assert session.query(ResearchEvent).filter_by(status="deleted").count() == 1


def test_run_sweep_no_news_skips_llm(session, monkeypatch):
    def boom(_):
        raise AssertionError("空输入不该调 LLM")
    monkeypatch.setattr(pool_sweep, "_call_sweep", boom)
    out = pool_sweep.run_sweep(session, event_type="macro")
    assert out["scanned"] == 0 and out["proposals"] == []


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
