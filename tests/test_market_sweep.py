# -*- coding: utf-8 -*-
"""市场提案管线(spec 2026-08-28 §3):候选归一化、防幻觉解析、run/apply。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import config
from services import market_sweep


def _search_event(**kw):
    base = {
        "slug": "fed-rate-cut-by-629", "title": "Fed rate cut by...?",
        "description": "Resolves Yes if the Fed cuts rates.", "endDate": "2027-01-08T04:59:00Z",
        "active": True, "closed": False, "archived": False, "volume": 3_239_062.96,
        "markets": [{"question": "Fed rate cut by January 2026 meeting?",
                     "outcomes": '["Yes", "No"]', "outcomePrices": '["0.62", "0.38"]'}],
    }
    base.update(kw)
    return base


def test_candidate_normalizes_single_market_event():
    c = market_sweep._candidate(_search_event())
    assert c["slug"] == "fed-rate-cut-by-629"
    assert c["current_probability"] == pytest.approx(0.62)
    assert (c["market_count"], c["end_date"]) == (1, "2027-01-08")


def test_candidate_drops_closed_and_low_volume():
    assert market_sweep._candidate(_search_event(closed=True)) is None
    assert market_sweep._candidate(_search_event(volume=9_999)) is None


def test_candidate_multi_market_has_no_single_probability():
    ev = _search_event(markets=[{"outcomes": '["Yes","No"]', "outcomePrices": '["0.1","0.9"]'},
                                {"outcomes": '["Yes","No"]', "outcomePrices": '["0.2","0.8"]'}])
    c = market_sweep._candidate(ev)
    assert c["current_probability"] is None and c["market_count"] == 2


import json
from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models.event_market import ResearchEventMarket
from models.research import ResearchEvent
from models.tracked_market import TrackedMarket


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _event(s, name="俄乌停火", event_type="macro"):
    e = ResearchEvent(name=name, event_type=event_type, status="active", display_no=1)
    s.add(e); s.commit()
    return e


class FakeGamma:
    def __init__(self, events):
        self._events = events

    def search_events(self, query, limit_per_type=5):
        return self._events


def test_parse_matches_whitelist_and_price_target_drop():
    candidates = {7: {"good-slug": {"title": "t"}, "pt-slug": {"title": "p"}}}
    raw = json.dumps({"matches": [
        {"event_id": 7, "slug": "good-slug", "confidence": 0.9, "price_target": False, "reason": "对"},
        {"event_id": 7, "slug": "pt-slug", "confidence": 0.9, "price_target": True, "reason": "价格影子"},
        {"event_id": 7, "slug": "hallucinated", "confidence": 0.9, "price_target": False},
        {"event_id": 99, "slug": "good-slug", "confidence": 0.9, "price_target": False},
        {"event_id": 7, "slug": "good-slug", "confidence": 0.5, "price_target": False},
    ]})
    matches, dropped = market_sweep._parse_matches(raw, candidates)
    assert matches == [{"event_id": 7, "slug": "good-slug", "confidence": 0.9, "reason": "对"}]
    assert dropped == 1


def test_run_market_sweep_end_to_end(session, monkeypatch):
    e = _event(session)
    canned_terms = json.dumps({"terms": [{"event_id": e.id, "queries": ["russia ukraine ceasefire"]}]})
    canned_pairs = json.dumps({"matches": [{"event_id": e.id, "slug": "ceasefire-2026",
                                            "confidence": 0.9, "price_target": False,
                                            "reason": "就是这件事"}]})
    replies = iter([(canned_terms, 1.0), (canned_pairs, 2.0)])
    monkeypatch.setattr(market_sweep, "_call_market_ai", lambda *a, **k: next(replies))
    gamma = FakeGamma([{
        "slug": "ceasefire-2026", "title": "Russia x Ukraine ceasefire in 2026?",
        "description": "...", "endDate": "2026-12-31T00:00:00Z",
        "active": True, "closed": False, "archived": False, "volume": 500_000,
        "markets": [{"outcomes": '["Yes","No"]', "outcomePrices": '["0.41","0.59"]'}],
    }])
    out = market_sweep.run_market_sweep(session, event_type="macro", client=gamma)
    assert out["scanned_events"] == 1 and out["candidates"] == 1
    p = out["proposals"][0]
    assert (p["slug"], p["event_id"], p["confidence"]) == ("ceasefire-2026", e.id, 0.9)
    assert p["current_probability"] == pytest.approx(0.41)
    # run 全程不落库
    assert session.query(TrackedMarket).count() == 0
    assert session.query(ResearchEventMarket).count() == 0


def test_apply_creates_revives_links_idempotently(session):
    e = _event(session)
    # 预置一个已软删的同名 slug:apply 应复活而非新建
    session.add(TrackedMarket(kind="slug", identifier="revive-me", market="macro",
                              enabled=False, dismissed=True))
    session.commit()
    items = [
        {"event_id": e.id, "slug": "brand-new", "title": "New market", "confidence": 0.9},
        {"event_id": e.id, "slug": "revive-me", "title": "Old market", "confidence": 0.65},
    ]
    out = market_sweep.apply_market_proposals(session, "macro", items)
    assert (out["added"], out["revived"], out["linked"]) == (["brand-new"], ["revive-me"], 2)
    revived = session.query(TrackedMarket).filter_by(identifier="revive-me").one()
    assert (revived.dismissed, revived.enabled) == (False, True)
    # 重复提交:全部跳过、零新增
    out2 = market_sweep.apply_market_proposals(session, "macro", items)
    assert out2["linked"] == 0 and len(out2["skipped"]) == 2
    # 已摘下的不挂回
    link = session.query(ResearchEventMarket).filter_by(tracked_id=revived.id).one()
    link.detached = True
    session.commit()
    out3 = market_sweep.apply_market_proposals(session, "macro", [items[1]])
    assert out3["linked"] == 0 and "已摘下" in out3["skipped"][0]


def test_run_rejects_bad_inputs(session):
    with pytest.raises(ValueError):
        market_sweep.run_market_sweep(session, event_type="stocks")
    with pytest.raises(ValueError):
        market_sweep.run_market_sweep(session, event_type="macro", event_id=12345)
