# -*- coding: utf-8 -*-
"""标注 v2：每条新闻 causal_role 六分类 + 窗口 market_reaction_type/confidence + 迁移 + JSONL 导出。

契约见 docs/specs/annotation-v2.md。兼容映射：
- no_clear_news ⟺ market_reaction_type == "no_clear_driver"（派生，老消费方不破）
- causal_news_ids ⟺ roles 中 primary_driver + secondary_driver 的 id
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import config
from database import Base
from models.news import NewsItem, NewsPriceAnnotation
from models.price import PriceSnapshot
from schemas.annotations import AnnotationCreateRequest
from services import annotation_service

W_START = datetime(2026, 6, 9, 17, 0)
W_END = datetime(2026, 6, 9, 17, 30)


@pytest.fixture
def session(monkeypatch):
    monkeypatch.setattr(config, "ANNOTATION_REFERENCE_ASSETS", [("NQ=F", "纳指")])
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _seed(session) -> list[int]:
    for sym, ac, p0, p1 in [("BTC/USDT", "crypto", 100000.0, 98000.0), ("NQ=F", "futures", 20000.0, 19900.0)]:
        session.add(PriceSnapshot(timestamp=W_START, asset_class=ac, symbol=sym, name=sym, price=p0, source="t"))
        session.add(PriceSnapshot(timestamp=W_END, asset_class=ac, symbol=sym, name=sym, price=p1, source="t"))
    news = [
        NewsItem(timestamp=W_START + timedelta(minutes=5), source="jin10", title="美军对伊朗发起打击", content="硬事件", language="zh"),
        NewsItem(timestamp=W_START + timedelta(minutes=10), source="jin10", title="黄金短线下挫，因美元走强", content="行情描述", language="zh"),
        NewsItem(timestamp=W_START + timedelta(minutes=12), source="jin10", title="某分析师评论", content="观点", language="zh"),
    ]
    session.add_all(news)
    session.commit()
    return [n.id for n in news]


def _req(ids, **kw) -> AnnotationCreateRequest:
    return AnnotationCreateRequest(
        symbol="BTC/USDT",
        window_start_utc=W_START.isoformat(),
        window_end_utc=W_END.isoformat(),
        threshold_pct=1.0,
        candidate_news_ids=ids,
        **kw,
    )


def test_upsert_v2_roundtrip(session):
    n1, n2, n3 = _seed(session)
    resp = annotation_service.upsert_annotation(session, _req(
        [n1, n2, n3],
        news_roles={n1: "primary_driver", n2: "post_hoc_explanation"},
        market_reaction_type="risk_sentiment",
        confidence=0.85,
        notes="美军打击导致避险",
    ))
    d = annotation_service.get_annotation_detail(session, resp.id)
    assert d.news_roles == {n1: "primary_driver", n2: "post_hoc_explanation"}
    assert d.market_reaction_type == "risk_sentiment"
    assert d.confidence == pytest.approx(0.85)
    assert d.selected_news_ids == [n1]          # 派生：primary + secondary
    assert d.no_clear_news is False             # 派生：有 primary → False


def test_upsert_v2_no_clear_derivation(session):
    ids = _seed(session)
    resp = annotation_service.upsert_annotation(session, _req(
        ids, news_roles={}, market_reaction_type="no_clear_driver", confidence=0.3,
    ))
    d = annotation_service.get_annotation_detail(session, resp.id)
    assert d.no_clear_news is True
    assert d.selected_news_ids == []
    assert d.market_reaction_type == "no_clear_driver"


def test_upsert_legacy_request_normalized_to_v2(session):
    """老格式请求（只有 selected/no_clear）写入时归一化为 v2：首条 primary、其余 secondary。"""
    n1, n2, _ = _seed(session)
    resp = annotation_service.upsert_annotation(session, _req(
        [n1, n2], selected_news_ids=[n1, n2], no_clear_news=False,
    ))
    d = annotation_service.get_annotation_detail(session, resp.id)
    assert d.news_roles == {n1: "primary_driver", n2: "secondary_driver"}
    assert d.selected_news_ids == [n1, n2]

    resp2 = annotation_service.upsert_annotation(session, _req(
        [n1], selected_news_ids=[], no_clear_news=True,
    ))
    d2 = annotation_service.get_annotation_detail(session, resp2.id)
    assert d2.market_reaction_type == "no_clear_driver"
    assert d2.no_clear_news is True


def test_invalid_roles_and_types_rejected(session):
    n1, _, _ = _seed(session)
    with pytest.raises(ValueError):
        annotation_service.upsert_annotation(session, _req([n1], news_roles={n1: "bogus_role"}))
    with pytest.raises(ValueError):
        annotation_service.upsert_annotation(session, _req([n1], market_reaction_type="bogus_type"))


def test_legacy_rows_migrated(session):
    """库里已有的旧格式行：迁移函数把 causal_news_ids 映射为 roles、no_clear 映射为 reaction_type。"""
    n1, n2, _ = _seed(session)
    session.add(NewsPriceAnnotation(
        symbol="BTC/USDT", window_start=W_START, window_end=W_END,
        context_start=W_START, context_end=W_END,
        causal_news_ids=json.dumps([n1, n2]), no_clear_news=False,
    ))
    session.add(NewsPriceAnnotation(
        symbol="BTC/USDT", window_start=W_START - timedelta(hours=2), window_end=W_END - timedelta(hours=2),
        context_start=W_START, context_end=W_END,
        causal_news_ids=json.dumps([]), no_clear_news=True,
    ))
    session.commit()

    from database import migrate_legacy_annotations
    migrated = migrate_legacy_annotations(session.connection())
    session.commit()
    assert migrated == 2

    rows = session.query(NewsPriceAnnotation).order_by(NewsPriceAnnotation.window_start.desc()).all()
    r_sel, r_noclear = rows[0], rows[1]
    assert json.loads(r_sel.news_roles) == {str(n1): "primary_driver", str(n2): "secondary_driver"}
    assert r_sel.market_reaction_type is None
    assert json.loads(r_noclear.news_roles) == {}
    assert r_noclear.market_reaction_type == "no_clear_driver"
    # 幂等：再跑一遍不重复迁移
    assert migrate_legacy_annotations(session.connection()) == 0


def test_parse_auto_v2_filters_and_derives():
    """v2 解析器：过滤幻觉 id 与非法 role/type，clamp confidence，派生 no_clear/selected。"""
    raw = json.dumps({
        "news_roles": {"1": "primary_driver", "2": "post_hoc_explanation", "99": "primary_driver", "3": "bogus"},
        "market_reaction_type": "risk_sentiment",
        "confidence": 1.7,
        "summary": "测试",
    })
    parsed = annotation_service._parse_auto_annotate_v2(raw, {1, 2, 3})
    assert parsed.news_roles == {1: "primary_driver", 2: "post_hoc_explanation"}   # 99 幻觉、3 非法角色被丢
    assert parsed.market_reaction_type == "risk_sentiment"
    assert parsed.confidence == 1.0
    assert parsed.selected_news_ids == [1]
    assert parsed.no_clear_news is False

    raw2 = json.dumps({"news_roles": {}, "market_reaction_type": "bogus", "confidence": 0.2, "summary": "无"})
    parsed2 = annotation_service._parse_auto_annotate_v2(raw2, {1})
    assert parsed2.market_reaction_type is None
    assert parsed2.no_clear_news is True


def test_export_jsonl(session):
    n1, n2, n3 = _seed(session)
    annotation_service.upsert_annotation(session, _req(
        [n1, n2, n3],
        news_roles={n1: "primary_driver", n2: "post_hoc_explanation"},
        market_reaction_type="risk_sentiment",
        confidence=0.85,
        notes="归因链",
        labeler="akis",
    ))
    lines = annotation_service.export_training_jsonl(session, days=30)
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["schema_version"] == 2
    assert row["window"]["symbol"] == "BTC/USDT"
    assert row["labels"]["market_reaction_type"] == "risk_sentiment"
    assert row["labels"]["confidence"] == pytest.approx(0.85)
    roles = {c["id"]: c["causal_role"] for c in row["candidates"]}
    assert roles[n1] == "primary_driver"
    assert roles[n3] == "noise"                 # 未标条目导出为 noise（负样本）
    assert any(c["title"] for c in row["candidates"])
    assert "reference_changes" in row["window"]


def test_export_marks_legacy_low_fidelity(session):
    n1, _, _ = _seed(session)
    annotation_service.upsert_annotation(session, _req([n1], selected_news_ids=[n1]))
    # 老格式（无 reaction_type/confidence）→ schema_version 1
    row = json.loads(annotation_service.export_training_jsonl(session, days=30)[0])
    assert row["schema_version"] == 1
