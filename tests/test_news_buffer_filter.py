# -*- coding: utf-8 -*-
"""新闻快讯的缓冲区筛选(docs/specs/2026-08-06-buffer-into-news-page-design.md §1)。

口径必须与事件池缓冲区完全一致——同一个谓词,不许两处定义。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models.news import NewsItem
from services import event_pool, news_service


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _news(s, title, score=8, minutes_ago=30, source="jin10", lang="zh"):
    n = NewsItem(timestamp=datetime.utcnow() - timedelta(minutes=minutes_ago),
                 source=source, title=title, content="正文", language=lang,
                 llm_importance=score, tagged_at=datetime.utcnow())
    s.add(n); s.commit()
    return n


def _titles(resp):
    return {i.title for i in resp.items}


def test_buffer_only_matches_event_pool_buffer(session):
    """同一批数据,新闻快讯的 buffer_only 与事件池 buffer_news 选出同一批新闻。"""
    keep = _news(session, "过闸未挂")
    _news(session, "低分不命中", score=3)
    _news(session, "金十数据整理：每日ETF", score=9)          # 黑名单
    linked = _news(session, "已挂事件的")
    event_pool.create_event(session, "E", news_ids=[linked.id])

    api_titles = _titles(news_service.get_news(session, buffer_only=True, min_llm_importance=0))
    pool_titles = {r["title"] for r in event_pool.buffer_news(session, days=3)}
    assert api_titles == pool_titles == {keep.title}


def test_buffer_only_keeps_unscored_news(session):
    """未评分 = 评分调用失败,不是不重要:缓冲区口径必须留着它(与闸门规则一致)。"""
    unscored = _news(session, "未评分的", score=None)
    resp = news_service.get_news(session, buffer_only=True, min_llm_importance=0)
    assert _titles(resp) == {unscored.title}


def test_min_importance_zero_means_no_score_gate(session):
    """分数选"不限(含未评分)"时未评分可见;设了 6 分门槛就该消失。"""
    _news(session, "未评分的", score=None)
    _news(session, "六分的", score=6)
    assert _titles(news_service.get_news(session, min_llm_importance=0)) == {"未评分的", "六分的"}
    assert _titles(news_service.get_news(session, min_llm_importance=6)) == {"六分的"}


def test_buffer_only_and_score_filter_compose(session):
    """两个筛选叠加取交集,total 按最终结果计。"""
    _news(session, "高分未挂", score=9)
    _news(session, "未评分未挂", score=None)
    resp = news_service.get_news(session, buffer_only=True, min_llm_importance=8)
    assert _titles(resp) == {"高分未挂"}
    assert resp.total == 1


def test_default_behaviour_unchanged_without_new_params(session):
    """老调用不回归:不传新参数时仍是"5 分以上",未评分照旧不出现。"""
    _news(session, "七分的", score=7)
    _news(session, "两分的", score=2)
    _news(session, "未评分的", score=None)
    assert _titles(news_service.get_news(session)) == {"七分的"}
