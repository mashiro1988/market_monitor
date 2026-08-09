# -*- coding: utf-8 -*-
"""加密打分四件套:重要性+方向+币圈事务判定+提及币种,一次调用落库。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models.crypto import NewsCoin
from models.news import NewsItem
from services import crypto_tagging


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _news(s, title, market="crypto"):
    n = NewsItem(timestamp=datetime(2026, 8, 9, 12, 0), source="blockbeats", title=title,
                 language="zh", market=market, traditional_open=False)
    s.add(n); s.commit()
    return n


def test_writes_all_four_fields(session, monkeypatch):
    n = _news(session, "币安将上线 XYZ 永续合约")
    monkeypatch.setattr(crypto_tagging, "_call_crypto_tagger", lambda c: json.dumps(
        {"items": [{"id": n.id, "importance": 7, "direction": "利多",
                    "is_crypto_affair": True, "coins": ["XYZ"], "reason": "上新"}]}))
    assert crypto_tagging.tag_untagged_crypto(session) == 1
    session.refresh(n)
    assert n.llm_importance == 7
    assert n.llm_importance_reason == "上新"
    assert n.news_direction == "利多"
    assert n.is_crypto_affair is True
    assert n.tagged_at is not None
    assert n.llm_scored_at is not None
    assert [c.coin for c in session.query(NewsCoin).all()] == ["XYZ"]


def test_macro_passthrough_marked_not_crypto_affair(session, monkeypatch):
    """加密源转载的纯宏观新闻:语义闸判 false,不入加密池但照常留库。"""
    n = _news(session, "美联储维持利率不变")
    monkeypatch.setattr(crypto_tagging, "_call_crypto_tagger", lambda c: json.dumps(
        {"items": [{"id": n.id, "importance": 8, "direction": "中性",
                    "is_crypto_affair": False, "coins": []}]}))
    crypto_tagging.tag_untagged_crypto(session)
    session.refresh(n)
    assert n.is_crypto_affair is False
    assert n.tagged_at is not None
    assert session.query(NewsCoin).count() == 0


def test_coins_normalized_and_deduped(session, monkeypatch):
    n = _news(session, "SOL 生态")
    monkeypatch.setattr(crypto_tagging, "_call_crypto_tagger", lambda c: json.dumps(
        {"items": [{"id": n.id, "importance": 5, "direction": "中性",
                    "is_crypto_affair": True, "coins": [" sol ", "SOL", "arb", "", 123,
                                                        "这不是代码"]}]}))
    crypto_tagging.tag_untagged_crypto(session)
    assert sorted(c.coin for c in session.query(NewsCoin).all()) == ["ARB", "SOL"]


def test_only_crypto_market_selected(session, monkeypatch):
    _news(session, "宏观新闻", market="macro")
    n = _news(session, "币圈新闻")
    seen = []
    monkeypatch.setattr(crypto_tagging, "_call_crypto_tagger",
                        lambda c: seen.append(c) or json.dumps(
                            {"items": [{"id": n.id, "importance": 5, "direction": "中性",
                                        "is_crypto_affair": True, "coins": []}]}))
    crypto_tagging.tag_untagged_crypto(session)
    assert "币圈新闻" in seen[0] and "宏观新闻" not in seen[0]


def test_hallucinated_id_and_bad_enum_dropped(session, monkeypatch):
    n = _news(session, "真新闻")
    monkeypatch.setattr(crypto_tagging, "_call_crypto_tagger", lambda c: json.dumps(
        {"items": [{"id": 999999, "importance": 9, "direction": "利多",
                    "is_crypto_affair": True, "coins": ["FAKE"]},
                   {"id": n.id, "importance": 99, "direction": "暴涨",
                    "is_crypto_affair": True, "coins": []}]}))
    assert crypto_tagging.tag_untagged_crypto(session) == 0
    session.refresh(n)
    assert n.tagged_at is None            # 非法条目不盖章,下轮重试
    assert session.query(NewsCoin).count() == 0


def test_non_bool_affair_rejected(session, monkeypatch):
    """语义闸是硬判定:模型给字符串 "true" 也算非法,宁可重试也不猜。"""
    n = _news(session, "含糊")
    monkeypatch.setattr(crypto_tagging, "_call_crypto_tagger", lambda c: json.dumps(
        {"items": [{"id": n.id, "importance": 5, "direction": "中性",
                    "is_crypto_affair": "true", "coins": []}]}))
    assert crypto_tagging.tag_untagged_crypto(session) == 0
    session.refresh(n)
    assert n.tagged_at is None


def test_retag_replaces_old_coins(session, monkeypatch):
    n = _news(session, "SOL")
    session.add(NewsCoin(news_id=n.id, coin="OLD")); session.commit()
    monkeypatch.setattr(crypto_tagging, "_call_crypto_tagger", lambda c: json.dumps(
        {"items": [{"id": n.id, "importance": 5, "direction": "中性",
                    "is_crypto_affair": True, "coins": ["SOL"]}]}))
    crypto_tagging.tag_untagged_crypto(session)
    assert [c.coin for c in session.query(NewsCoin).all()] == ["SOL"]


def test_batch_failure_does_not_block_next(session, monkeypatch):
    """单片失败不阻断后续片:一片挂了别把整轮拖死。"""
    a = _news(session, "第一条")
    b = _news(session, "第二条")
    calls = []

    def flaky(content):
        calls.append(content)
        if len(calls) == 1:
            raise RuntimeError("DeepSeek 加密打标返回空 content")
        nid = b.id if str(b.id) in content else a.id
        return json.dumps({"items": [{"id": nid, "importance": 5, "direction": "中性",
                                      "is_crypto_affair": True, "coins": []}]})

    monkeypatch.setattr(crypto_tagging, "_call_crypto_tagger", flaky)
    assert crypto_tagging.tag_untagged_crypto(session, batch_size=1) == 1
    assert len(calls) == 2
