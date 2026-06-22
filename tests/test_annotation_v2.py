# -*- coding: utf-8 -*-
"""标注 v2.1→Phase3a：causal_role 三分类（driver/redundant/noise；redundant 导出派生，
post_hoc/contradictory 退场并入 noise）+
market_reaction_type 三分类（macro_policy/event_driven/no_news_driver）+
auto_news_roles/prompt_version/eval_set + 迁移 + JSONL 导出（train/eval split）。

契约见 docs/specs/annotation-v2.md。兼容映射：
- no_clear_news ⟺ 无 driver（或 reaction == "no_news_driver"）
- causal_news_ids ⟺ roles 中全部 driver 的 id
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
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


def test_upsert_v21_roundtrip(session):
    n1, n2, n3 = _seed(session)
    resp = annotation_service.upsert_annotation(session, _req(
        [n1, n2, n3],
        news_roles={n1: "driver"},               # Phase3a：只标 driver；n2 行情描述 = noise（不落库）
        market_reaction_type="event_driven",
        confidence=0.85,
        notes="美军打击导致避险",
        auto_news_roles={n1: "driver"},
        auto_summary="AI 摘要",
    ))
    d = annotation_service.get_annotation_detail(session, resp.id)
    assert d.news_roles == {n1: "driver"}
    assert d.market_reaction_type == "event_driven"
    assert d.confidence == pytest.approx(0.85)
    assert d.selected_news_ids == [n1]          # 派生：全部 driver
    assert d.no_clear_news is False
    assert d.auto_news_roles == {n1: "driver"}  # 人机分歧可比对
    assert d.prompt_version == annotation_service.ANNOTATION_PROMPT_VERSION


def test_upsert_no_clear_derivation(session):
    ids = _seed(session)
    resp = annotation_service.upsert_annotation(session, _req(
        ids, news_roles={}, market_reaction_type="no_news_driver", confidence=0.9,
    ))
    d = annotation_service.get_annotation_detail(session, resp.id)
    assert d.no_clear_news is True
    assert d.selected_news_ids == []


def test_upsert_legacy_request_normalized(session):
    """老格式请求（selected/no_clear）：全部 selected → driver；no_clear → no_news_driver。"""
    n1, n2, _ = _seed(session)
    resp = annotation_service.upsert_annotation(session, _req(
        [n1, n2], selected_news_ids=[n1, n2], no_clear_news=False,
    ))
    d = annotation_service.get_annotation_detail(session, resp.id)
    assert d.news_roles == {n1: "driver", n2: "driver"}
    assert sorted(d.selected_news_ids) == sorted([n1, n2])

    resp2 = annotation_service.upsert_annotation(session, _req(
        [n1], selected_news_ids=[], no_clear_news=True,
    ))
    d2 = annotation_service.get_annotation_detail(session, resp2.id)
    assert d2.market_reaction_type == "no_news_driver"
    assert d2.no_clear_news is True


def test_old_v20_enums_rejected_at_api(session):
    """v2.0 旧枚举（primary_driver / risk_sentiment 等）不再是合法输入。"""
    n1, _, _ = _seed(session)
    with pytest.raises(ValueError):
        annotation_service.upsert_annotation(session, _req([n1], news_roles={n1: "primary_driver"}))
    with pytest.raises(ValueError):
        annotation_service.upsert_annotation(session, _req([n1], market_reaction_type="risk_sentiment"))


def test_phase3a_rejects_retired_and_derived_roles(session):
    """Phase3a：退场角色 contradictory/post_hoc_explanation 与派生角色 redundant 都不可手标输入。"""
    n1, _, _ = _seed(session)
    for bad in ("contradictory", "post_hoc_explanation", "redundant"):
        with pytest.raises(ValueError):
            annotation_service.upsert_annotation(session, _req([n1], news_roles={n1: bad}))


def test_migration_v1_and_v20_rows(session):
    """迁移两步：v1 二元行 → driver/no_news_driver；v2.0 旧枚举行 → v2.1 枚举。"""
    n1, n2, _ = _seed(session)
    session.add(NewsPriceAnnotation(                      # v1 行
        symbol="BTC/USDT", window_start=W_START, window_end=W_END,
        context_start=W_START, context_end=W_END,
        causal_news_ids=json.dumps([n1, n2]), no_clear_news=False,
    ))
    session.add(NewsPriceAnnotation(                      # v2.0 行（旧枚举）
        symbol="BTC/USDT", window_start=W_START - timedelta(hours=2), window_end=W_END - timedelta(hours=2),
        context_start=W_START, context_end=W_END,
        news_roles=json.dumps({str(n1): "primary_driver", str(n2): "secondary_driver"}),
        market_reaction_type="risk_sentiment", no_clear_news=False,
        causal_news_ids=json.dumps([n1, n2]),
    ))
    session.commit()

    from database import migrate_legacy_annotations
    changed = migrate_legacy_annotations(session.connection())
    session.commit()
    assert changed == 2

    rows = session.query(NewsPriceAnnotation).order_by(NewsPriceAnnotation.window_start.desc()).all()
    r_v1, r_v20 = rows[0], rows[1]
    assert json.loads(r_v1.news_roles) == {str(n1): "driver", str(n2): "driver"}
    assert r_v1.market_reaction_type is None
    assert json.loads(r_v20.news_roles) == {str(n1): "driver", str(n2): "driver"}
    assert r_v20.market_reaction_type == "event_driven"
    # 幂等
    assert migrate_legacy_annotations(session.connection()) == 0


def test_migrate_drops_retired_roles(session):
    """Phase3a：迁移把存量 post_hoc_explanation / contradictory 从 news_roles 移除（归 noise），幂等。"""
    n1, n2, n3 = _seed(session)
    session.add(NewsPriceAnnotation(
        symbol="BTC/USDT", window_start=W_START, window_end=W_END,
        context_start=W_START, context_end=W_END,
        news_roles=json.dumps({str(n1): "driver", str(n2): "post_hoc_explanation", str(n3): "contradictory"}),
        no_clear_news=False,
    ))
    session.commit()
    from database import migrate_legacy_annotations
    changed = migrate_legacy_annotations(session.connection())
    session.commit()
    assert changed == 1
    row = session.query(NewsPriceAnnotation).first()
    assert json.loads(row.news_roles) == {str(n1): "driver"}      # 退场角色已移除
    assert migrate_legacy_annotations(session.connection()) == 0  # 幂等


def test_parse_auto_v21_filters_and_derives():
    raw = json.dumps({
        "news_roles": {"1": "driver", "2": "post_hoc_explanation", "99": "driver", "3": "primary_driver"},
        "market_reaction_type": "macro_policy",
        "confidence": 1.7,
        "summary": "测试",
    })
    parsed = annotation_service._parse_auto_annotate_v2(raw, {1, 2, 3})
    assert parsed.news_roles == {1: "driver"}   # 2 post_hoc 退场、99 幻觉、3 旧枚举 全被丢
    assert parsed.market_reaction_type == "macro_policy"
    assert parsed.confidence == 1.0
    assert parsed.selected_news_ids == [1]
    assert parsed.no_clear_news is False

    raw2 = json.dumps({"news_roles": {}, "market_reaction_type": "bogus", "confidence": 0.2, "summary": "无"})
    parsed2 = annotation_service._parse_auto_annotate_v2(raw2, {1})
    assert parsed2.market_reaction_type is None
    assert parsed2.no_clear_news is True


def test_export_jsonl_with_auto_labels(session):
    n1, n2, n3 = _seed(session)
    annotation_service.upsert_annotation(session, _req(
        [n1, n2, n3],
        news_roles={n1: "driver"},               # Phase3a：只标 driver
        market_reaction_type="event_driven",
        confidence=0.85,
        notes="归因链",
        labeler="akis",
        auto_news_roles={n1: "driver", n3: "driver"},   # AI 误标了 n3，人改掉了
        auto_summary="AI 摘要",
    ))
    lines = annotation_service.export_training_jsonl(session, days=30)
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["schema_version"] == 2
    assert row["labels"]["market_reaction_type"] == "event_driven"
    roles = {c["id"]: c["causal_role"] for c in row["candidates"]}
    assert roles[n1] == "driver"
    assert roles[n3] == "noise"
    # 人机分歧可见：AI 标了 n3，最终标签没有
    assert row["auto_labels"]["news_roles"] == {str(n1): "driver", str(n3): "driver"}
    assert row["prompt_version"] == annotation_service.ANNOTATION_PROMPT_VERSION
    assert row["eval_set"] is False


def test_export_derives_redundant_from_topic(session):
    """Phase3a：同 topic 两条候选(量级不同)+人标其一 driver → 导出 driver+redundant 各一；代表按量级定。"""
    session.add(PriceSnapshot(timestamp=W_START, asset_class="crypto", symbol="BTC/USDT", name="BTC/USDT", price=100000.0, source="t"))
    session.add(PriceSnapshot(timestamp=W_END, asset_class="crypto", symbol="BTC/USDT", name="BTC/USDT", price=98000.0, source="t"))
    a = NewsItem(timestamp=W_START + timedelta(minutes=5), source="jin10", title="美军打击伊朗",
                 content="x", language="zh", topic="地缘冲突", magnitude_tier="大")
    b = NewsItem(timestamp=W_START + timedelta(minutes=8), source="jin10", title="伊朗回应",
                 content="x", language="zh", topic="地缘冲突", magnitude_tier="中")
    session.add_all([a, b]); session.commit()
    annotation_service.upsert_annotation(session, _req(
        [a.id, b.id], news_roles={b.id: "driver"}, confidence=0.8,     # 人标了"中"量级那条
    ))
    row = json.loads(annotation_service.export_training_jsonl(session, days=30)[0])
    roles = {c["id"]: c["causal_role"] for c in row["candidates"]}
    assert roles[a.id] == "driver"           # 代表 = 量级大的 a（即便人标的是 b）
    assert roles[b.id] == "redundant"        # 同主题非代表 → 冗余（训练排除，不当负样本）
    assert row["labels"]["news_roles"] == {str(b.id): "driver"}   # 人工原始标注保持不变


def test_export_split_excludes_eval(session):
    n1, _, _ = _seed(session)
    resp = annotation_service.upsert_annotation(session, _req([n1], selected_news_ids=[n1]))
    annotation_service.set_eval_set(session, resp.id, True)

    assert annotation_service.export_training_jsonl(session, days=30, split="train") == []
    assert len(annotation_service.export_training_jsonl(session, days=30, split="eval")) == 1
    assert len(annotation_service.export_training_jsonl(session, days=30, split="all")) == 1
    with pytest.raises(ValueError):
        annotation_service.export_training_jsonl(session, days=30, split="bogus")


def test_export_marks_legacy_low_fidelity(session):
    n1, _, _ = _seed(session)
    annotation_service.upsert_annotation(session, _req([n1], selected_news_ids=[n1]))
    row = json.loads(annotation_service.export_training_jsonl(session, days=30)[0])
    assert row["schema_version"] == 1          # 无 confidence → 低保真


def test_context_pre_minutes_respected(session):
    n1, _, _ = _seed(session)
    resp = annotation_service.upsert_annotation(session, _req(
        [n1], selected_news_ids=[n1], context_pre_minutes=60,
    ))
    row = session.query(NewsPriceAnnotation).filter(NewsPriceAnnotation.id == resp.id).first()
    assert row.context_start == W_START - timedelta(minutes=60)
