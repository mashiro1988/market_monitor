# -*- coding: utf-8 -*-
"""加密挂接:语义闸(is_crypto_affair)取代分数闸,且两条线的池子互不越界。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models.news import NewsItem
from models.research import ResearchEvent, ResearchEventLink
from services import event_linking


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _news(s, title, market="crypto", score=3, affair=True, tagged=True):
    n = NewsItem(timestamp=datetime(2026, 8, 9, 12, 0), source="blockbeats", title=title,
                 language="zh", llm_importance=score, market=market,
                 is_crypto_affair=affair,
                 tagged_at=datetime(2026, 8, 9, 12, 1) if tagged else None)
    s.add(n); s.commit()
    return n


def _event(s, name, event_type="crypto"):
    e = ResearchEvent(name=name, event_type=event_type, status="active")
    s.add(e); s.commit()
    return e


def test_low_score_crypto_affair_still_calls_model(session, monkeypatch):
    """小币新闻分数天然低——加密线不设分数闸,3 分照样进模型(design §3)。"""
    e = _event(session, "某小币生态")
    n = _news(session, "XYZ 上线新功能", score=3, affair=True)
    monkeypatch.setattr(event_linking, "_call_linker", lambda c, p=None: json.dumps(
        {"items": [{"id": n.id, "event_id": e.id, "confidence": 0.9}]}))
    stats = event_linking.link_unprocessed(session, market="crypto")
    assert stats["called"] == 1
    assert stats["linked"] == 1
    link = session.query(ResearchEventLink).one()
    assert link.prompt_version == event_linking.CRYPTO_LINK_PROMPT_VERSION


def test_non_crypto_affair_stamped_without_call(session, monkeypatch):
    """加密源转载的纯宏观新闻:语义闸拦下,零调用盖章(不入池≠丢弃)。"""
    _event(session, "某事件")
    n = _news(session, "美联储维持利率不变", score=9, affair=False)
    monkeypatch.setattr(event_linking, "_call_linker",
                        lambda c, p=None: pytest.fail("非币圈事务不该进模型"))
    stats = event_linking.link_unprocessed(session, market="crypto")
    assert stats["called"] == 0
    assert stats["processed"] == 1
    session.refresh(n)
    assert n.event_linked_at is not None


def test_macro_pool_and_crypto_pool_do_not_mix(session, monkeypatch):
    _event(session, "宏观事件", event_type="macro")
    crypto_evt = _event(session, "加密事件", event_type="crypto")
    crypto_news = _news(session, "币安上新", score=3, affair=True)
    seen = {}
    monkeypatch.setattr(event_linking, "_call_linker",
                        lambda c, p=None: seen.setdefault("payload", c) or json.dumps(
                            {"items": [{"id": crypto_news.id, "event_id": crypto_evt.id,
                                        "confidence": 0.9}]}))
    event_linking.link_unprocessed(session, market="crypto")
    assert "加密事件" in seen["payload"]
    assert "宏观事件" not in seen["payload"]


def test_macro_run_ignores_crypto_news(session, monkeypatch):
    _event(session, "宏观事件", event_type="macro")
    _news(session, "币圈新闻", market="crypto", score=9, affair=True)
    monkeypatch.setattr(event_linking, "_call_linker",
                        lambda c, p=None: pytest.fail("宏观轮不该看加密新闻"))
    stats = event_linking.link_unprocessed(session, market="macro")
    assert stats["called"] == 0


def test_untagged_crypto_news_not_selected(session, monkeypatch):
    """未打标 = is_crypto_affair 还没判,不能靠 NULL 蒙混过闸。"""
    _event(session, "加密事件")
    _news(session, "还没打标", affair=None, tagged=False)
    monkeypatch.setattr(event_linking, "_call_linker",
                        lambda c, p=None: pytest.fail("未打标不该进模型"))
    stats = event_linking.link_unprocessed(session, market="crypto")
    assert stats["processed"] == 0


def test_crypto_backscan_uses_semantic_gate(session):
    """回扫的"当前够格"在加密线也走语义闸,别把转载宏观又捞回来。"""
    _event(session, "加密事件")
    affair = _news(session, "币圈事务", score=2, affair=True)
    macro_passthrough = _news(session, "转载宏观", score=9, affair=False)
    now = datetime(2026, 8, 9, 13, 0)
    for n in (affair, macro_passthrough):
        n.event_linked_at = now
    session.commit()

    cleared = event_linking.clear_link_cursor(session, hours=72, now=now, market="crypto")
    assert cleared == 1
    session.refresh(affair); session.refresh(macro_passthrough)
    assert affair.event_linked_at is None
    assert macro_passthrough.event_linked_at is not None
