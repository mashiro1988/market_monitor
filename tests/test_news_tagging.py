# -*- coding: utf-8 -*-
"""新闻内容标签（news-impact-engine Phase 1）：LLM 批量打 topic/方向/量级 + 解析校验 + 落库。

LLM 调用 mock 掉；只测解析(过滤幻觉id/非法枚举) + 落库(写列+tagged_at) + prompt 含 rubric。
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import config
from database import Base
from models.news import NewsItem
from services import news_tagging


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _news(s, title, ts=None):
    n = NewsItem(timestamp=ts or datetime(2026, 6, 1, 12, 0), source="jin10", title=title, content="", language="zh")
    s.add(n); s.commit()
    return n


def test_parse_filters_hallucination_and_bad_enums():
    raw = json.dumps({"items": [
        {"id": 1, "topic": "地缘冲突", "direction": "利空", "magnitude": "大"},
        {"id": 2, "topic": "不存在的主题", "direction": "利空", "magnitude": "大"},   # 非法 topic
        {"id": 3, "topic": "通胀数据", "direction": "向上", "magnitude": "中"},        # 非法 direction
        {"id": 4, "topic": "通胀数据", "direction": "利多", "magnitude": "巨大"},      # 非法 magnitude
        {"id": 99, "topic": "地缘冲突", "direction": "利空", "magnitude": "大"},       # 幻觉 id
    ]})
    parsed = news_tagging._parse_tagging_response(raw, valid_ids={1, 2, 3, 4})
    assert parsed == {1: {"topic": "地缘冲突", "direction": "利空", "magnitude": "大"}}


def test_tag_news_batch_writes_columns(session, monkeypatch):
    n1 = _news(session, "美军轰炸伊朗")
    n2 = _news(session, "美国CPI高于预期")

    def fake_call(user_content):
        return json.dumps({"items": [
            {"id": n1.id, "topic": "地缘冲突", "direction": "利空", "magnitude": "大"},
            {"id": n2.id, "topic": "通胀数据", "direction": "利空", "magnitude": "大"},
        ]})

    monkeypatch.setattr(news_tagging, "_call_deepseek_tagger", fake_call)
    count = news_tagging.tag_news_batch(session, [n1, n2])
    assert count == 2
    session.refresh(n1); session.refresh(n2)
    assert n1.topic == "地缘冲突" and n1.magnitude_tier == "大" and n1.news_direction == "利空"
    assert n1.tagged_at is not None
    assert n2.topic == "通胀数据"


def test_tag_untagged_only_picks_untagged(session, monkeypatch):
    done = _news(session, "已打标"); done.topic = "其他"; done.tagged_at = datetime(2026, 6, 1, 12, 0)
    todo = _news(session, "美军轰炸伊朗")
    session.commit()

    captured = {}

    def fake_call(user_content):
        captured["content"] = user_content
        return json.dumps({"items": [{"id": todo.id, "topic": "地缘冲突", "direction": "利空", "magnitude": "大"}]})

    monkeypatch.setattr(news_tagging, "_call_deepseek_tagger", fake_call)
    count = news_tagging.tag_untagged(session, limit=50, batch_size=12)
    assert count == 1
    # 只把未打标的喂进去了（精确看 payload 里的 news id 列表，避免子串误撞）
    fed_ids = {item["id"] for item in json.loads(captured["content"].split("\n", 1)[1])["news"]}
    assert fed_ids == {todo.id}


def test_tag_untagged_skips_unelapsed_window(session, monkeypatch):
    """反应窗口没走完的新闻(刚发的)不进打标状态——按用户逻辑'窗口走完才放成可打标'。"""
    from datetime import timedelta
    now = datetime(2026, 6, 20, 12, 0)
    fresh = _news(session, "刚发的", ts=now - timedelta(minutes=5))      # 未走完
    old = _news(session, "美军轰炸伊朗", ts=now - timedelta(hours=2))     # 已走完

    captured = {}

    def fake_call(user_content):
        captured["content"] = user_content
        return json.dumps({"items": [{"id": old.id, "topic": "地缘冲突", "direction": "利空", "magnitude": "大"}]})

    monkeypatch.setattr(news_tagging, "_call_deepseek_tagger", fake_call)
    news_tagging.tag_untagged(session, limit=50, reaction_minutes=30, now=now)
    fed_ids = {item["id"] for item in json.loads(captured["content"].split("\n", 1)[1])["news"]}
    assert fed_ids == {old.id}                  # 只喂了窗口走完的那条


def test_backfill_traditional_open(session):
    """给 traditional_open 为 NULL 的历史新闻补前置条件（纯日历，幂等）。"""
    sat = _news(session, "周末发的", ts=datetime(2026, 6, 13, 16, 0))    # 周六 → NQ 休市
    weekday = _news(session, "周一发的", ts=datetime(2026, 6, 15, 16, 0))  # 周一 12:00 ET → 开
    assert sat.traditional_open is None and weekday.traditional_open is None
    n = news_tagging.backfill_traditional_open(session)
    assert n == 2
    session.refresh(sat); session.refresh(weekday)
    assert sat.traditional_open is False
    assert weekday.traditional_open is True
    assert news_tagging.backfill_traditional_open(session) == 0       # 幂等


def test_prompt_documents_rubric():
    p = news_tagging.TAGGING_SYSTEM_PROMPT
    for topic in config.NEWS_TOPICS:
        assert topic in p
    assert "利多" in p and "利空" in p
    assert "大" in p and "rubric" in p.lower() or "量级" in p
