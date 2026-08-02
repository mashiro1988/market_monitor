# -*- coding: utf-8 -*-
"""挂接调用(news-research-phase1 spec §4-§5):资格判定 + 解析防幻觉 + 游标语义。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import config
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


def _news(s, title, score=8, source="jin10", content="", tagged=True, ts=None):
    n = NewsItem(timestamp=ts or datetime(2026, 8, 1, 12, 0), source=source, title=title,
                 content=content, language="zh", llm_importance=score,
                 tagged_at=datetime(2026, 8, 1, 12, 1) if tagged else None)
    s.add(n); s.commit()
    return n


def _event(s, name, keywords=None, status="active"):
    e = ResearchEvent(name=name, gate_keywords=keywords, status=status)
    s.add(e); s.commit()
    return e


def test_blacklist_matches_source_and_title(session):
    junk = _news(session, "金十数据整理：每日全球大宗商品要闻", score=7)
    real = _news(session, "据伊朗媒体Fars News:交火升级", score=7)
    other_src = _news(session, "金十数据整理：xxx", score=7, source="cnbc")
    assert event_linking._is_blacklisted(junk) is True
    assert event_linking._is_blacklisted(real) is False
    assert event_linking._is_blacklisted(other_src) is False   # 黑名单绑定来源


def test_gate_score_or_unscored(session):
    assert event_linking.passes_gate(_news(session, "a", score=6), []) is True
    assert event_linking.passes_gate(_news(session, "b", score=5), []) is False
    assert event_linking.passes_gate(_news(session, "c", score=None), []) is True   # 未评分放行


def test_gate_keyword_bypass_any_hit(session):
    kw = ["苹果", "Apple"]
    low = _news(session, "Apple 供应链传出新一轮调价", score=3)
    low2 = _news(session, "苹果公司回应调价传闻", score=2)
    miss = _news(session, "特斯拉降价", score=3)
    assert event_linking.passes_gate(low, kw) is True     # 或的关系:命中任一即免闸
    assert event_linking.passes_gate(low2, kw) is True
    assert event_linking.passes_gate(miss, kw) is False
    # 英文不分大小写;匹配范围=标题+摘要
    body = _news(session, "科技股盘前动态", score=3, content="apple iphone pricing rumor")
    assert event_linking.passes_gate(body, kw) is True


def test_split_keywords_tolerates_commas():
    assert event_linking._split_keywords("苹果、Apple,iPhone，调价") == ["苹果", "Apple", "iPhone", "调价"]
    assert event_linking._split_keywords(None) == []
    assert event_linking._split_keywords(" 、 ") == []


def test_keyword_pool_only_active_events(session):
    _event(session, "苹果调价", keywords="苹果、Apple")
    _event(session, "已关闭的", keywords="客机", status="closed")
    events = event_linking._active_events(session)
    assert event_linking._keyword_pool(events) == ["苹果", "Apple"]   # closed 的词不进免闸(走沉睡监听)


# ---- 解析防幻觉 + link_unprocessed 游标语义(spec §4.3)----

def test_parse_filters_hallucination():
    raw = json.dumps({"items": [
        {"id": 1, "event_id": 17, "confidence": 0.9},     # 合法
        {"id": 2, "event_id": None, "confidence": 0.9},   # 合法"不挂"
        {"id": 3, "event_id": 99, "confidence": 0.9},     # 池外事件 → 丢弃
        {"id": 4, "event_id": 17, "confidence": 0.82},    # 非三档 → 丢弃
        {"id": 88, "event_id": 17, "confidence": 0.9},    # 幻觉新闻 id → 丢弃
    ]})
    out = event_linking._parse_link_response(raw, valid_news_ids={1, 2, 3, 4},
                                             valid_event_ids={17})
    assert out == {1: {"event_id": 17, "confidence": 0.9},
                   2: {"event_id": None, "confidence": None}}


def test_link_unprocessed_stamps_and_links(session, monkeypatch):
    e = _event(session, "苹果调价", keywords="苹果")
    hit = _news(session, "苹果宣布调价", score=8)
    no = _news(session, "无关新闻不挂", score=7)
    low = _news(session, "低分且不命中", score=3)
    junk = _news(session, "金十数据整理：每日热门ETF", score=9)

    def fake_call(user_content):
        assert "苹果调价" in user_content        # 活跃池摘要进了提示词
        return json.dumps({"items": [
            {"id": hit.id, "event_id": e.id, "confidence": 0.9},
            {"id": no.id, "event_id": None, "confidence": 0.9},
        ]})
    monkeypatch.setattr(event_linking, "_call_linker", fake_call)

    stats = event_linking.link_unprocessed(session)
    assert stats["linked"] == 1
    # 四种结果都盖章:挂/不挂/不够格/黑名单
    for n in (hit, no, low, junk):
        session.refresh(n)
        assert n.event_linked_at is not None
    link = session.query(ResearchEventLink).filter_by(news_id=hit.id).one()
    assert (link.event_id, link.link_source, link.auto_event_id, link.confidence) == \
        (e.id, "auto", e.id, 0.9)
    assert link.prompt_version == event_linking.LINK_PROMPT_VERSION


def test_link_unprocessed_empty_pool_skips_everything(session, monkeypatch):
    _news(session, "有新闻但没事件", score=9)
    called = []
    monkeypatch.setattr(event_linking, "_call_linker",
                        lambda c: called.append(c) or "{}")
    stats = event_linking.link_unprocessed(session)
    assert stats == {"processed": 0, "linked": 0, "called": 0}
    assert not called                    # 池空:零调用,游标也不动(spec §4.1)


def test_link_unprocessed_batch_failure_keeps_cursor(session, monkeypatch):
    _event(session, "事件X")
    n = _news(session, "会失败的批", score=9)
    def boom(user_content):
        raise RuntimeError("网络超时")
    monkeypatch.setattr(event_linking, "_call_linker", boom)
    stats = event_linking.link_unprocessed(session)
    session.refresh(n)
    assert n.event_linked_at is None     # 整批失败不盖章,下轮重试
    assert stats["linked"] == 0


def test_link_unprocessed_invalid_item_not_stamped(session, monkeypatch):
    e = _event(session, "事件X")
    good = _news(session, "合法条", score=9)
    bad = _news(session, "被模型漏答的条", score=9)
    monkeypatch.setattr(event_linking, "_call_linker", lambda c: json.dumps(
        {"items": [{"id": good.id, "event_id": None, "confidence": 0.9}]}))
    event_linking.link_unprocessed(session)
    session.refresh(good); session.refresh(bad)
    assert good.event_linked_at is not None
    assert bad.event_linked_at is None   # 未被合法解析:不盖章,下轮重试


def test_untagged_news_not_picked(session, monkeypatch):
    _event(session, "事件X")
    n = _news(session, "还没打标", score=9, tagged=False)
    monkeypatch.setattr(event_linking, "_call_linker", lambda c: json.dumps({"items": []}))
    event_linking.link_unprocessed(session)
    session.refresh(n)
    assert n.event_linked_at is None     # tagged_at 为空的不进挂接(评分未必跑过)


# ---- 回扫=清游标(spec §6.3)+ tick 接线 ----
from datetime import timedelta


def test_clear_link_cursor_scope(session):
    now = datetime(2026, 8, 1, 12, 0)
    e = _event(session, "苹果调价", keywords="苹果")
    stamped = datetime(2026, 8, 1, 11, 0)
    def mk(title, score, ts, linked_to=None):
        n = _news(session, title, score=score, ts=ts)
        n.event_linked_at = stamped
        if linked_to:
            session.add(ResearchEventLink(event_id=linked_to.id, news_id=n.id,
                                          link_source="human"))
        session.commit()
        return n
    in_range_ok = mk("苹果的旧证据", 3, now - timedelta(hours=10))       # 命中关键词 → 清
    in_range_high = mk("高分旧新闻", 8, now - timedelta(hours=10))       # 过闸 → 清
    in_range_low = mk("低分不命中", 3, now - timedelta(hours=10))        # 不够格 → 不清
    out_range = mk("范围外的苹果新闻", 8, now - timedelta(hours=100))    # 超 72h → 不清
    already = mk("已挂过的苹果新闻", 8, now - timedelta(hours=10), linked_to=e)  # 有挂接 → 不清

    cleared = event_linking.clear_link_cursor(session, hours=72, now=now)
    assert cleared == 2
    for n, expect in ((in_range_ok, None), (in_range_high, None),
                      (in_range_low, stamped), (out_range, stamped), (already, stamped)):
        session.refresh(n)
        assert n.event_linked_at == expect


def test_scan_runtime_link_hook(monkeypatch):
    """_link_new_news:开关关/无 key 时静默跳过;异常自吞不影响扫描。"""
    from services import scan_runtime
    calls = []
    monkeypatch.setattr("services.event_linking.link_unprocessed",
                        lambda s, limit=200: calls.append(limit) or {"processed": 0, "linked": 0, "called": 0})
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "k")
    monkeypatch.setattr(config, "EVENT_LINK_ENABLED", True)
    scan_runtime._link_new_news()
    assert calls == [200]
    monkeypatch.setattr(config, "EVENT_LINK_ENABLED", False)
    scan_runtime._link_new_news()
    assert calls == [200]                 # 开关关:没再调


# ---- AI 建议关键词(spec §5.2)----

def test_suggest_keywords_parses_and_caps(session, monkeypatch):
    n = _news(session, "苹果供应链传出调价")
    monkeypatch.setattr(event_linking, "_call_keyword_suggester", lambda c: json.dumps(
        {"keywords": ["苹果", "Apple", "iPhone", "调价", "供应链", "库克", "第七个"]}))
    out = event_linking.suggest_keywords(session, "苹果调价", [n.id])
    assert out == ["苹果", "Apple", "iPhone", "调价", "供应链", "库克"]   # 截 6 个


def test_suggest_keywords_rejects_bad_json(session, monkeypatch):
    n = _news(session, "x")
    monkeypatch.setattr(event_linking, "_call_keyword_suggester", lambda c: "不是JSON")
    with pytest.raises(ValueError):
        event_linking.suggest_keywords(session, "e", [n.id])
