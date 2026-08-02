# -*- coding: utf-8 -*-
"""事件生命周期(news-research-phase1 spec §6)+ 读取层(§8-§10)。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import config
from database import Base
from models.news import NewsItem, NewsPriceAnnotation
from models.research import ResearchEvent, ResearchEventLink
from services import event_pool


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _news(s, title, score=8, ts=None, source="jin10"):
    n = NewsItem(timestamp=ts or datetime(2026, 8, 1, 12, 0), source=source, title=title,
                 content="", language="zh", llm_importance=score,
                 tagged_at=datetime(2026, 8, 1, 12, 1))
    s.add(n); s.commit()
    return n


def test_create_event_requires_news(session):
    with pytest.raises(ValueError):
        event_pool.create_event(session, "空壳事件", news_ids=[])


def test_create_event_links_seed_and_backscans(session):
    n = _news(session, "种子新闻", score=3)      # 低分:人工立案无视闸门
    old = _news(session, "72h 内的旧证据", score=8, ts=datetime(2026, 8, 1, 2, 0))
    old.event_linked_at = datetime(2026, 8, 1, 3, 0); session.commit()
    e = event_pool.create_event(session, "苹果调价", news_ids=[n.id],
                                gate_keywords="苹果、Apple", created_from="annotation",
                                now=datetime(2026, 8, 1, 13, 0))
    assert (e.status, e.created_from) == ("active", "annotation")
    link = session.query(ResearchEventLink).filter_by(event_id=e.id, news_id=n.id).one()
    assert (link.link_source, link.auto_event_id, link.confidence) == ("human", None, None)
    session.refresh(old)
    assert old.event_linked_at is None            # 立案自动回扫 72h 清了旧证据游标


def test_close_reopen(session):
    n = _news(session, "x")
    e = event_pool.create_event(session, "事件", news_ids=[n.id])
    event_pool.close_event(session, e.id, reason="已定价")
    session.refresh(e)
    assert (e.status, e.closed_reason) == ("closed", "已定价")
    event_pool.reopen_event(session, e.id)
    session.refresh(e)
    assert e.status == "active"


def test_merge_moves_links_keywords_and_closes(session):
    n1, n2, shared = _news(session, "a"), _news(session, "b"), _news(session, "共有")
    a = event_pool.create_event(session, "A", news_ids=[n1.id, shared.id], gate_keywords="苹果")
    b = event_pool.create_event(session, "B", news_ids=[n2.id, shared.id], gate_keywords="Apple、苹果")
    moved = event_pool.merge_event(session, source_id=a.id, target_id=b.id)
    assert moved == 1                              # 只有 n1 迁移;shared 撞唯一索引跳过
    session.refresh(a); session.refresh(b)
    assert (a.status, a.merged_into_id) == ("closed", b.id)
    assert a.closed_reason == f"合并入 #{b.id}"
    assert b.gate_keywords == "Apple、苹果"        # 并入去重(苹果已有)
    b_news = {l.news_id for l in session.query(ResearchEventLink).filter_by(event_id=b.id)}
    assert b_news == {n1.id, n2.id, shared.id}


def test_reassign_keeps_auto_origin(session):
    n = _news(session, "x")
    e1 = event_pool.create_event(session, "E1", news_ids=[_news(session, "seed1").id])
    e2 = event_pool.create_event(session, "E2", news_ids=[_news(session, "seed2").id])
    link = ResearchEventLink(event_id=e1.id, news_id=n.id, link_source="auto",
                             auto_event_id=e1.id, confidence=0.9, prompt_version="link-v1")
    session.add(link); session.commit()
    event_pool.reassign_link(session, link.id, new_event_id=e2.id)
    session.refresh(link)
    assert (link.event_id, link.auto_event_id, link.link_source) == (e2.id, e1.id, "human")


def test_detach_flags_not_deletes(session):
    n = _news(session, "x")
    e = event_pool.create_event(session, "E", news_ids=[n.id])
    link = session.query(ResearchEventLink).filter_by(event_id=e.id).one()
    event_pool.detach_link(session, link.id, reason="挂错了")
    session.refresh(link)
    assert (link.detached, link.detach_reason) == (True, "挂错了")
    assert session.query(ResearchEventLink).count() == 1     # 不删行


def test_attach_news_revives_detached(session):
    n = _news(session, "x")
    e = event_pool.create_event(session, "E", news_ids=[n.id])
    link = session.query(ResearchEventLink).filter_by(event_id=e.id).one()
    event_pool.detach_link(session, link.id, reason="误摘")
    revived = event_pool.attach_news(session, e.id, n.id)
    assert revived.id == link.id and revived.detached is False
