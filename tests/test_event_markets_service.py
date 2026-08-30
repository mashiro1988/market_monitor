# -*- coding: utf-8 -*-
"""事件↔预测市场挂接(spec 2026-08-28 §1):模型约束、挂接/摘下/归属列表、事件市场卡。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

import config
from database import Base
from models.event_market import ResearchEventMarket
from models.prediction import PredictionMarket
from models.research import ResearchEvent
from models.tracked_market import TrackedMarket
from services import event_markets
from services.time_utils import utc_now_naive


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def test_link_defaults_and_unique(session):
    link = ResearchEventMarket(event_id=1, tracked_id=2, link_source="human")
    session.add(link)
    session.commit()
    assert (link.detached, link.confidence, link.prompt_version) == (False, None, None)
    session.add(ResearchEventMarket(event_id=1, tracked_id=2, link_source="auto"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_tracked_market_line_defaults_macro(session):
    row = TrackedMarket(kind="slug", identifier="some-slug", enabled=True)
    session.add(row)
    session.commit()
    assert row.market == "macro"


def _event(s, name="俄乌停火", event_type="macro", status="active"):
    e = ResearchEvent(name=name, event_type=event_type, status=status, display_no=1)
    s.add(e); s.commit()
    return e


def _tracked(s, slug="ceasefire-2026", market="macro", dismissed=False):
    t = TrackedMarket(kind="slug", identifier=slug, market=market,
                      enabled=True, dismissed=dismissed)
    s.add(t); s.commit()
    return t


def test_attach_detach_and_human_reattach(session):
    e, t = _event(session), _tracked(session)
    link = event_markets.attach_market(session, e.id, t.id)
    assert (link.link_source, link.detached) == ("human", False)
    # 幂等:重复挂返回同一行
    assert event_markets.attach_market(session, e.id, t.id).id == link.id
    assert event_markets.detach_market(session, link.id, "配错了") is True
    session.refresh(link)
    assert link.detached is True and link.detach_reason == "配错了"
    # 人工复挂撤销摘下
    relink = event_markets.attach_market(session, e.id, t.id)
    assert relink.id == link.id and relink.detached is False


def test_attach_rejects_missing_targets(session):
    e, t = _event(session), _tracked(session, dismissed=True)
    with pytest.raises(ValueError):
        event_markets.attach_market(session, e.id, t.id)          # 跟踪项已删
    with pytest.raises(ValueError):
        event_markets.attach_market(session, 999, t.id)           # 事件不存在


def test_list_event_markets_summary_and_settled(session, monkeypatch):
    monkeypatch.setattr(config, "PREDICTION_ACTIVE_GRACE_MINUTES", 150)
    e = _event(session)
    t_live, t_stale = _tracked(session, "live-slug"), _tracked(session, "stale-slug")
    event_markets.attach_market(session, e.id, t_live.id)
    event_markets.attach_market(session, e.id, t_stale.id)
    now = utc_now_naive()
    for minutes_ago, origin, market_id, prob in [
        (30, "slug:live-slug", "m-live", 0.62),
        (400, "slug:stale-slug", "m-stale", 0.30),
    ]:
        session.add(PredictionMarket(timestamp=now - timedelta(minutes=minutes_ago),
                                     market_id=market_id, question="q?", outcome="Yes",
                                     probability=prob, origin=origin))
    session.commit()
    items = {i["slug"]: i for i in event_markets.list_event_markets(session, e.id)}
    assert items["live-slug"]["settled"] is False
    assert items["live-slug"]["markets"][0].outcomes[0].probability == pytest.approx(0.62)
    assert items["stale-slug"]["settled"] is True                 # 落后表内最新超宽限期


def test_links_for_tracked_returns_briefs(session):
    e, t = _event(session), _tracked(session)
    link = event_markets.attach_market(session, e.id, t.id)
    briefs = event_markets.links_for_tracked(session, [t.id])
    assert briefs[t.id][0] == {"link_id": link.id, "event_id": e.id,
                               "display_no": 1, "name": "俄乌停火"}


def test_market_filter_limits_cards_but_lists_all(session):
    """筛档位(2026-08-30):卡片只画保留档,all_markets 仍列全部子市场供编辑器勾选。"""
    import json
    e = _event(session)
    t = _tracked(session, "fed-buckets")
    t.market_filter = json.dumps(["m-keep"])
    session.commit()
    event_markets.attach_market(session, e.id, t.id)
    now = utc_now_naive()
    for market_id, q in [("m-keep", "keep?"), ("m-drop", "drop?")]:
        session.add(PredictionMarket(timestamp=now, market_id=market_id, question=q,
                                     outcome="Yes", probability=0.5, origin="slug:fed-buckets"))
    session.commit()
    item = event_markets.list_event_markets(session, e.id)[0]
    assert [m.market_id for m in item["markets"]] == ["m-keep"]
    assert item["market_filter"] == ["m-keep"]
    assert {a["market_id"] for a in item["all_markets"]} == {"m-keep", "m-drop"}
    assert item["waiting_first_scan"] is False
