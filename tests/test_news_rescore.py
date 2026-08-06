# -*- coding: utf-8 -*-
"""补评分扫描(docs/specs/2026-08-06-news-rescore-and-source-cut-design.md §1.1/§3)。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models.news import NewsItem
from services.news_rescore import rescore_unscored

NOW = datetime(2026, 8, 6, 12, 0)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _news(s, title, score=None, created=None, attempts=None, source="jin10"):
    n = NewsItem(timestamp=NOW - timedelta(hours=1), source=source, title=title,
                 content="正文", language="zh", llm_importance=score,
                 rescore_attempts=attempts, created_at=created or NOW - timedelta(hours=1))
    s.add(n); s.commit()
    return n


class FakeScorer:
    """enrich_batch 的注入替身:按 title 决定成败,记录被扫批次。"""
    enabled = True

    def __init__(self, score=7, fail_titles=()):
        self.score = score
        self.fail_titles = set(fail_titles)
        self.batches: list[list[str]] = []

    def enrich_batch(self, records):
        self.batches.append([r.title for r in records])
        for r in records:
            if r.title in self.fail_titles:
                continue
            r.llm_importance = self.score
            r.llm_importance_reason = "测试理由"
            r.llm_model = "fake-model"
            r.llm_scored_at = NOW
        return records


def test_rescore_writes_back_and_stamps_attempt(session):
    n = _news(session, "无分新闻")
    stats = rescore_unscored(session, scorer=FakeScorer(score=7), now=NOW)
    assert stats == {"selected": 1, "scored": 1}
    session.refresh(n)
    assert (n.llm_importance, n.llm_importance_reason, n.llm_model) == (7, "测试理由", "fake-model")
    assert n.llm_scored_at is not None
    assert n.rescore_attempts == 1          # NULL≈0 起步,+1


def test_failed_item_only_increments_attempts(session):
    n = _news(session, "毒新闻")
    stats = rescore_unscored(session, scorer=FakeScorer(fail_titles={"毒新闻"}), now=NOW)
    assert stats == {"selected": 1, "scored": 0}
    session.refresh(n)
    assert n.llm_importance is None
    assert n.rescore_attempts == 1


def test_max_attempts_retires_poison_rows(session):
    n = _news(session, "重试到顶", attempts=3)
    stats = rescore_unscored(session, scorer=FakeScorer(), now=NOW, max_attempts=3)
    assert stats == {"selected": 0, "scored": 0}
    session.refresh(n)
    assert n.llm_importance is None
    assert n.rescore_attempts == 3


def test_window_excludes_old_rows(session):
    _news(session, "太老", created=NOW - timedelta(hours=100))
    stats = rescore_unscored(session, scorer=FakeScorer(), now=NOW, window_hours=72)
    assert stats == {"selected": 0, "scored": 0}


def test_scored_rows_untouched(session):
    n = _news(session, "已有分", score=5)
    stats = rescore_unscored(session, scorer=FakeScorer(score=9), now=NOW)
    assert stats == {"selected": 0, "scored": 0}
    session.refresh(n)
    assert (n.llm_importance, n.rescore_attempts) == (5, None)


def test_newest_first_and_limit(session):
    _news(session, "旧积压", created=NOW - timedelta(hours=10))
    _news(session, "新漏网", created=NOW - timedelta(hours=1))
    fake = FakeScorer()
    stats = rescore_unscored(session, scorer=fake, now=NOW, limit=1)
    assert stats == {"selected": 1, "scored": 1}
    assert fake.batches == [["新漏网"]]     # created_at 倒序:先补最新


def test_disabled_scorer_is_noop(session):
    n = _news(session, "无分新闻")
    fake = FakeScorer()
    fake.enabled = False
    stats = rescore_unscored(session, scorer=fake, now=NOW)
    assert stats == {"selected": 0, "scored": 0}
    session.refresh(n)
    assert n.rescore_attempts is None       # 没调用就不烧尝试次数
