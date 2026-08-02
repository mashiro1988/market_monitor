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
