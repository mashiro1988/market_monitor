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


# ---- 读取层(spec §8-§10)----

def _annotation(s, news_id, symbol="BTC/USDT", change=1.8):
    a = NewsPriceAnnotation(symbol=symbol, window_start=datetime(2026, 8, 1, 10, 0),
                            window_end=datetime(2026, 8, 1, 10, 15),
                            context_start=datetime(2026, 8, 1, 9, 30),
                            context_end=datetime(2026, 8, 1, 10, 15),
                            change_pct=change,
                            news_roles=json.dumps({str(news_id): "driver"}))
    s.add(a); s.commit()
    return a


def test_list_events_sort_and_derived(session):
    early, late = (_news(session, "早", ts=datetime(2026, 7, 20, 8, 0)),
                   _news(session, "晚", ts=datetime(2026, 8, 1, 8, 0)))
    e1 = event_pool.create_event(session, "老事件", news_ids=[early.id])
    e2 = event_pool.create_event(session, "新事件", news_ids=[late.id])
    _annotation(session, late.id)
    rows = event_pool.list_events(session, now=datetime(2026, 8, 3, 8, 0))
    assert [r["id"] for r in rows] == [e2.id, e1.id]      # 最新证据倒序
    top = rows[0]
    assert top["evidence_count"] == 1
    assert top["badge_count"] == 1
    assert top["days_since_last"] == 2
    assert rows[1]["days_since_last"] == 14


def test_timeline_obs_badge_and_score_miss(session):
    from models.price import PriceSnapshot
    n = _news(session, "低分driver", score=3, ts=datetime(2026, 8, 1, 10, 2))
    for m, p in ((0, 100.0), (5, 102.0), (10, 103.0)):
        session.add(PriceSnapshot(timestamp=datetime(2026, 8, 1, 10, 0) + timedelta(minutes=m),
                                  asset_class="crypto", symbol="BTC/USDT", name="BTC",
                                  price=p, source="test"))
    session.commit()
    e = event_pool.create_event(session, "E", news_ids=[n.id])
    _annotation(session, n.id, change=1.8)
    tl = event_pool.event_timeline(session, e.id, now=datetime(2026, 8, 1, 11, 0))
    item = tl["items"][0]
    assert item["news"]["id"] == n.id
    assert item["obs"]["status"] == "ok" and abs(item["obs"]["net_pct"] - 3.0) < 1e-9
    assert item["driver_badge"] == {"symbol": "BTC/USDT", "change_pct": 1.8}
    assert item["score_miss"] is True                     # 3 分 < 闸门线且已挂(spec §8.3)
    assert item["link"]["link_source"] == "human"


def test_buffer_excludes_linked_and_junk(session):
    e = event_pool.create_event(session, "E", news_ids=[_news(session, "seed").id],
                                gate_keywords="苹果")
    now = datetime(2026, 8, 1, 13, 0)
    plain = _news(session, "过闸未挂", score=7, ts=datetime(2026, 8, 1, 12, 0))
    kw = _news(session, "苹果低分未挂", score=3, ts=datetime(2026, 8, 1, 12, 0))
    low = _news(session, "低分不命中", score=3, ts=datetime(2026, 8, 1, 12, 0))
    junk = _news(session, "金十数据整理：每日ETF", score=9, ts=datetime(2026, 8, 1, 12, 0))
    ids = {r["id"] for r in event_pool.buffer_news(session, days=3, now=now)}
    assert plain.id in ids and kw.id in ids
    assert low.id not in ids and junk.id not in ids
    seed_id = session.query(ResearchEventLink.news_id).filter_by(event_id=e.id).first()[0]
    assert seed_id not in ids                              # 已挂的不在缓冲区


def test_revival_matches_closed_event_keywords(session):
    e = event_pool.create_event(session, "苹果调价", news_ids=[_news(session, "seed").id],
                                gate_keywords="苹果、Apple")
    event_pool.close_event(session, e.id, reason="退潮")
    hit = _news(session, "苹果再次传出调价", score=3, ts=datetime(2026, 8, 1, 9, 0))
    _news(session, "无关", score=3, ts=datetime(2026, 8, 1, 9, 0))
    rows = event_pool.revival_matches(session, days=7, now=datetime(2026, 8, 1, 12, 0))
    assert [(r["news"]["id"], r["event_id"]) for r in rows] == [(hit.id, e.id)]


def test_daily_brief_text(session):
    now = datetime(2026, 8, 2, 0, 10)                      # 北京 08:10
    y = _news(session, "昨日证据", score=9, ts=datetime(2026, 8, 1, 6, 0))
    hot = _news(session, "昨日高分未挂", score=8, ts=datetime(2026, 8, 1, 7, 0))
    hot.event_linked_at = datetime(2026, 8, 1, 7, 5)
    e = event_pool.create_event(session, "事件A", news_ids=[y.id], now=datetime(2026, 8, 1, 8, 0))
    # link.created_at 默认真实时间;日报按 created_at 落在昨日北京日内计数,改到昨日
    for l in session.query(ResearchEventLink).filter_by(event_id=e.id).all():
        l.created_at = datetime(2026, 8, 1, 8, 0)
    session.commit()
    title, content = event_pool.daily_brief_text(session, now=now)
    assert "事件池" in title
    assert "事件A" in content and "+1" in content
    assert "≥8 分未挂 1 条" in content

    title2, content2 = event_pool.daily_brief_text(
        session, now=datetime(2026, 9, 1, 0, 10))          # 无动静的一天
    assert "无动静" in content2
