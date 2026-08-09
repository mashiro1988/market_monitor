# -*- coding: utf-8 -*-
"""事件序号按类型各排各的:加密事件池从 #1 开始,不跟宏观共用一套号。

为什么不直接用主键 id:id 是全局自增,两条线混排后加密的第一个事件会显示成 #8,
对用户毫无意义。display_no = 该类型内的第几个,人看的是它;程序内部一律仍用 id。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from database import Base, migrate_event_display_no
from models.news import NewsItem
from models.research import ResearchEvent
from services import event_pool


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _news(s, title="随便"):
    n = NewsItem(timestamp=datetime(2026, 8, 9, 12, 0), source="blockbeats", title=title,
                 language="zh", market="crypto")
    s.add(n); s.commit()
    return n


def test_each_type_numbers_from_one(session):
    n = _news(session)
    m1 = event_pool.create_event(session, "宏观A", [n.id], event_type="macro")
    m2 = event_pool.create_event(session, "宏观B", [n.id], event_type="macro")
    c1 = event_pool.create_event(session, "加密A", [n.id], event_type="crypto")
    c2 = event_pool.create_event(session, "加密B", [n.id], event_type="crypto")

    assert (m1.display_no, m2.display_no) == (1, 2)
    assert (c1.display_no, c2.display_no) == (1, 2)      # 加密自己从 1 排
    assert c1.id != m1.id                                # 主键仍是全局唯一


def test_closed_and_merged_events_keep_their_number(session):
    """序号只增不补:关闭/合并掉的事件仍占着自己的号,免得你记的 #3 变成别人。"""
    n = _news(session)
    c1 = event_pool.create_event(session, "加密A", [n.id], event_type="crypto")
    c2 = event_pool.create_event(session, "加密B", [n.id], event_type="crypto")
    event_pool.close_event(session, c2.id, reason="没下文了")
    event_pool.merge_event(session, source_id=c1.id, target_id=c2.id)

    c3 = event_pool.create_event(session, "加密C", [n.id], event_type="crypto")
    assert c3.display_no == 3        # 前两个号已被占用,不复用
    assert session.get(ResearchEvent, c2.id).display_no == 2


def test_list_events_exposes_display_no(session):
    n = _news(session)
    event_pool.create_event(session, "加密A", [n.id], event_type="crypto")
    row = event_pool.list_events(session, event_type="crypto")[0]
    assert row["display_no"] == 1
    assert row["id"] >= 1


def test_migration_backfills_by_id_order(session):
    """存量行按 id 升序回填;宏观既有事件的号不该变(它们本就是 1..N)。"""
    for i, (name, etype) in enumerate([("宏观1", "macro"), ("宏观2", "macro"),
                                       ("加密1", "crypto"), ("宏观3", "macro"),
                                       ("加密2", "crypto")], start=1):
        session.add(ResearchEvent(id=i, name=name, event_type=etype, status="active",
                                  created_from="manual"))
    session.commit()
    session.execute(text("UPDATE research_events SET display_no = NULL"))
    session.commit()

    migrate_event_display_no(session.connection())
    session.commit()

    got = {e.name: e.display_no for e in session.query(ResearchEvent).all()}
    assert got == {"宏观1": 1, "宏观2": 2, "宏观3": 3, "加密1": 1, "加密2": 2}


def test_migration_is_idempotent(session):
    n = _news(session)
    event_pool.create_event(session, "加密A", [n.id], event_type="crypto")
    before = {e.id: e.display_no for e in session.query(ResearchEvent).all()}
    migrate_event_display_no(session.connection())
    session.commit()
    after = {e.id: e.display_no for e in session.query(ResearchEvent).all()}
    assert before == after
