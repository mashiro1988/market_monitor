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


def test_delete_event_tombstone_and_frees_links(session):
    n1, n2 = _news(session, "a"), _news(session, "b")
    e = event_pool.create_event(session, "AI立错的事件", news_ids=[n1.id, n2.id])
    no = e.display_no
    freed = event_pool.delete_event(session, e.id)
    assert freed == 2
    session.refresh(e)
    assert e.status == "deleted"
    links = session.query(ResearchEventLink).filter_by(event_id=e.id).all()
    assert all(l.detached and l.detach_reason == "事件已删除" for l in links)
    # 任何列表/搜索口径都不出现(证据因摘下而退回缓冲区)
    assert all(r["id"] != e.id for r in event_pool.list_events(session))
    assert all(r["id"] != e.id for r in event_pool.list_events(session, status="closed"))
    # 墓碑保序号只增不补:下一个事件不顶替被删的号
    e2 = event_pool.create_event(session, "新事件", news_ids=[_news(session, "c").id])
    assert e2.display_no == no + 1
    # 对墓碑的一切操作视同不存在;重复删除幂等返回 0
    with pytest.raises(ValueError):
        event_pool.close_event(session, e.id, reason="x")
    with pytest.raises(ValueError):
        event_pool.event_timeline(session, e.id)
    assert event_pool.delete_event(session, e.id) == 0


def test_score_miss_only_for_macro(session):
    low_c = _news(session, "低分小币新闻", score=3)
    ec = event_pool.create_event(session, "加密事件", news_ids=[low_c.id],
                                 event_type="crypto")
    item = event_pool.event_timeline(session, ec.id)["items"][0]
    # 加密线不设分数闸(语义闸),"评分失手"口径不适用——3 分挂上不是失手是常态
    assert item["score_miss"] is False


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


def test_list_events_today_yesterday_and_bj_time(session):
    """卡片统计行(ui-redesign §2/§6.1):今日/昨日按北京日分桶;最新证据给北京时间字符串。"""
    now = datetime(2026, 8, 3, 8, 0)                          # 北京 8/3 16:00
    today_n = _news(session, "今日证据", ts=datetime(2026, 8, 3, 4, 0))
    yday_n = _news(session, "昨日证据", ts=datetime(2026, 8, 2, 4, 0))
    e = event_pool.create_event(session, "E", news_ids=[today_n.id, yday_n.id], now=now)
    links = session.query(ResearchEventLink).filter_by(event_id=e.id).all()
    by_news = {l.news_id: l for l in links}
    by_news[today_n.id].created_at = datetime(2026, 8, 3, 4, 1)    # 北京 8/3 12:01
    by_news[yday_n.id].created_at = datetime(2026, 8, 2, 4, 1)     # 北京 8/2 12:01
    session.commit()

    row = event_pool.list_events(session, now=now)[0]
    assert (row["today_new"], row["yesterday_new"]) == (1, 1)
    assert row["last_evidence_at"] == datetime(2026, 8, 3, 4, 0)   # naive UTC 原样保留
    assert row["last_evidence_bj"] == "2026-08-03 12:00:00"        # +8h,与 timestamp_bj 同产地


def test_list_events_bj_time_none_without_evidence(session):
    n = _news(session, "seed")
    e = event_pool.create_event(session, "E", news_ids=[n.id])
    event_pool.detach_link(session, session.query(ResearchEventLink)
                           .filter_by(event_id=e.id).one().id, reason="摘光")
    row = event_pool.list_events(session, now=datetime(2026, 8, 3, 8, 0))[0]
    assert (row["last_evidence_at"], row["last_evidence_bj"]) == (None, None)


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


def _timeline_fixture(session):
    """三条证据:高分大波动 / 高分小波动 / 低分无快照(观测 no_data)。返回 (event, news 三元组)。"""
    from models.price import PriceSnapshot
    base = datetime(2026, 8, 5, 10, 0)
    big = _news(session, "大波动", score=8, ts=base + timedelta(minutes=2))
    small = _news(session, "小波动", score=8, ts=base + timedelta(hours=2, minutes=2))
    old = _news(session, "三天前无快照", score=3, ts=base - timedelta(days=3))
    for minutes, price in ((0, 100.0), (10, 100.5),            # big:+0.5%
                           (120, 100.0), (130, 100.1)):        # small:+0.1%
        session.add(PriceSnapshot(timestamp=base + timedelta(minutes=minutes),
                                  asset_class="crypto", symbol="BTC/USDT", name="BTC",
                                  price=price, source="test"))
    session.commit()
    e = event_pool.create_event(session, "E", news_ids=[big.id, small.id, old.id])
    return e, big, small, old


def test_timeline_filters_by_days_score_and_move(session):
    """筛选栏(ui-redesign §3/§6.2):时间窗 / 分数≥ / 10min 波动≥,各自独立生效。"""
    now = datetime(2026, 8, 5, 14, 0)
    e, big, small, old = _timeline_fixture(session)

    all_ids = [it["news"]["id"] for it in event_pool.event_timeline(session, e.id, now=now)["items"]]
    assert set(all_ids) == {big.id, small.id, old.id}

    win = event_pool.event_timeline(session, e.id, now=now, days=1)
    assert {it["news"]["id"] for it in win["items"]} == {big.id, small.id}   # 3 天前的出窗

    scored = event_pool.event_timeline(session, e.id, now=now, min_score=6)
    assert {it["news"]["id"] for it in scored["items"]} == {big.id, small.id}  # 3 分的出局

    moved = event_pool.event_timeline(session, e.id, now=now, min_abs_move=0.3)
    assert [it["news"]["id"] for it in moved["items"]] == [big.id]           # 小波动/无观测出局


def test_timeline_min_score_excludes_unscored(session):
    """未评分(NULL)在设了分数门槛时必须出局——不能当 0 分也不能漏网。"""
    now = datetime(2026, 8, 5, 14, 0)
    unscored = _news(session, "未评分", score=None, ts=datetime(2026, 8, 5, 10, 2))
    e = event_pool.create_event(session, "E", news_ids=[unscored.id])
    assert event_pool.event_timeline(session, e.id, now=now)["total"] == 1
    assert event_pool.event_timeline(session, e.id, now=now, min_score=6)["items"] == []


def test_timeline_pagination_and_service_default_is_full(session):
    """路由层给分页;服务层默认全量(replay 脚本直连,不能被默认 50 条截断)。"""
    now = datetime(2026, 8, 5, 14, 0)
    ids = [_news(session, f"n{i}", ts=datetime(2026, 8, 5, 10, i)).id for i in range(5)]
    e = event_pool.create_event(session, "E", news_ids=ids)

    full = event_pool.event_timeline(session, e.id, now=now)
    assert (len(full["items"]), full["total"]) == (5, 5)

    p1 = event_pool.event_timeline(session, e.id, now=now, page=1, page_size=2)
    p3 = event_pool.event_timeline(session, e.id, now=now, page=3, page_size=2)
    assert (len(p1["items"]), p1["total"], p1["page"], p1["page_size"]) == (2, 5, 1, 2)
    assert len(p3["items"]) == 1                                  # 尾页
    assert [it["news"]["id"] for it in p1["items"]] == [ids[4], ids[3]]   # 时间倒序不变


def test_timeline_total_counts_after_filter_not_before(session):
    now = datetime(2026, 8, 5, 14, 0)
    e, big, _small, _old = _timeline_fixture(session)
    page = event_pool.event_timeline(session, e.id, now=now, min_abs_move=0.3,
                                     page=1, page_size=50)
    assert page["total"] == 1 and page["items"][0]["news"]["id"] == big.id


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
