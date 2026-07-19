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

# 相对时间夹具：写死日期会随真实时间流逝掉出 days=30 的导出/列表窗（2026-07-10 实翻车）。
W_START = (datetime.utcnow() - timedelta(days=5)).replace(minute=0, second=0, microsecond=0)
W_END = W_START + timedelta(minutes=30)


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


def test_upsert_rejects_inconsistent_v21_labels(session):
    n1, n2, _ = _seed(session)
    bad_requests = [
        _req([n1, n2], news_roles={n2: "redundant"}),
        _req([n1, n2], news_roles={}, market_reaction_type="event_driven"),
        _req([n1, n2], news_roles={n1: "driver"}, market_reaction_type="no_news_driver"),
    ]
    for request in bad_requests:
        with pytest.raises(ValueError):
            annotation_service.upsert_annotation(session, request)
        session.rollback()


def test_upsert_v2_requires_confidence(session):
    n1, _, _ = _seed(session)
    with pytest.raises(ValueError, match="归因置信度"):
        annotation_service.upsert_annotation(session, _req([n1], news_roles={n1: "driver"}))


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


def test_phase3a_roles_driver_redundant_noise(session):
    """Phase3a：driver/redundant 可手标/LLM标；contradictory/post_hoc_explanation 退场被拒。"""
    n1, n2, n3 = _seed(session)
    for bad in ("contradictory", "post_hoc_explanation"):
        with pytest.raises(ValueError):
            annotation_service.upsert_annotation(session, _req([n1], news_roles={n1: bad}))
    # driver + redundant 都能落库
    resp = annotation_service.upsert_annotation(session, _req(
        [n1, n2, n3], news_roles={n1: "driver", n2: "redundant"}, confidence=0.8,
    ))
    d = annotation_service.get_annotation_detail(session, resp.id)
    assert d.news_roles == {n1: "driver", n2: "redundant"}


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
        "news_roles": {"1": "driver", "2": "redundant", "99": "driver", "3": "primary_driver"},
        "market_reaction_type": "macro_policy",
        "confidence": 1.7,
        "summary": "测试",
    })
    parsed = annotation_service._parse_auto_annotate_v2(raw, {1, 2, 3})
    assert parsed.news_roles == {1: "driver", 2: "redundant"}   # redundant 保留；99 幻觉、3 旧枚举丢
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
    assert "s_scores" in row["window"]           # v11：共振分 S 取代 ±1h Pearson
    assert "correlations" not in row["window"]
    assert "reference_change_segments" in row["window"]
    assert row["labels"]["market_reaction_type"] == "event_driven"
    roles = {c["id"]: c["causal_role"] for c in row["candidates"]}
    assert roles[n1] == "driver"
    assert roles[n3] == "noise"
    # 人机分歧可见：AI 标了 n3，最终标签没有
    assert row["auto_labels"]["news_roles"] == {str(n1): "driver", str(n3): "driver"}
    assert row["prompt_version"] == annotation_service.ANNOTATION_PROMPT_VERSION
    assert row["eval_set"] is False


def test_export_uses_frozen_reference_changes(session):
    n1, n2, n3 = _seed(session)
    annotation_service.upsert_annotation(session, _req(
        [n1, n2, n3],
        news_roles={n1: "driver"},
        market_reaction_type="event_driven",
        confidence=0.85,
    ))
    annotation = session.query(NewsPriceAnnotation).one()
    frozen = json.loads(annotation.reference_changes)
    assert frozen

    end_ref = (
        session.query(PriceSnapshot)
        .filter(PriceSnapshot.symbol == "NQ=F", PriceSnapshot.timestamp == W_END)
        .one()
    )
    end_ref.price = 21000.0
    session.commit()

    exported = json.loads(annotation_service.export_training_jsonl(session, days=30)[0])
    assert exported["window"]["reference_changes"] == frozen


def test_export_keeps_manual_redundant(session):
    """Phase3a：人/LLM 直接标的 driver/redundant 原样进导出 candidates.causal_role，未标的为 noise。"""
    n1, n2, n3 = _seed(session)
    annotation_service.upsert_annotation(session, _req(
        [n1, n2, n3], news_roles={n1: "driver", n2: "redundant"}, confidence=0.8,
    ))
    row = json.loads(annotation_service.export_training_jsonl(session, days=30)[0])
    roles = {c["id"]: c["causal_role"] for c in row["candidates"]}
    assert roles[n1] == "driver"
    assert roles[n2] == "redundant"          # 同簇冗余：训练排除、非负样本
    assert roles[n3] == "noise"              # 未标 → 默认 noise（负样本）


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


def test_list_annotations_needs_review(session):
    """Phase3b A策略③：已标 (start,end) 对不上当前重算窗口 → needs_review=True；对得上 → False。"""
    now = annotation_service.utc_now_naive().replace(second=0, microsecond=0)
    # BTC 15min 窗口（config scale wm15/threshold0.5）：-2% 急跌
    # 平场引导拉长到 70min：边缘第二道闸（2026-07-12）不入库"切片左缘 30min 内起步"的段，
    # 夹具数据首点即切片首点，异动必须离首点 ≥30min 才能成段
    for mago, p in [(70, 100000.0), (65, 100000.0), (60, 100000.0), (55, 100000.0),
                    (50, 100000.0), (45, 100000.0),
                    (40, 100000.0), (35, 100000.0), (30, 100000.0), (25, 100000.0),
                    (20, 99000.0), (15, 98000.0), (10, 98000.0), (5, 98000.0)]:
        session.add(PriceSnapshot(timestamp=now - timedelta(minutes=mago),
                                  asset_class="crypto", symbol="BTC/USDT", name="BTC/USDT", price=p, source="t"))
    session.commit()
    from services import behavior_classifier as _bc
    _bc.classify(session, "BTC/USDT", now=now)          # Phase 2：窗口源=行为段，先跑一轮段检测
    # 时代锚点（2026-07-19 全量回溯守卫）：needs_review 只对最早行为段之后的标注生效。
    # 补一个更早的空壳段（无边界快照 → 不产窗口）把时代拉到幽灵标注之前。
    from models.behavior import BehaviorSegment
    session.add(BehaviorSegment(
        symbol="BTC/USDT", start_dt=now - timedelta(hours=8), end_dt=now - timedelta(hours=7, minutes=30),
        direction=1, tier_idx=1, tier_max=0.5, net_pct=0.6,
        classification="pure_resonance", class_version="v1",
    ))
    session.commit()
    wins = annotation_service.load_price_windows(session, "BTC/USDT", hours=24)
    assert wins, "需要至少一个窗口来测 needs_review"
    w = wins[0]
    # 正常：按该窗口边界标注 → id 能对上 → needs_review=False
    annotation_service.upsert_annotation(session, AnnotationCreateRequest(
        symbol="BTC/USDT", window_start_utc=w.window_start.timestamp_utc,
        window_end_utc=w.window_end.timestamp_utc, threshold_pct=0.5,
        candidate_news_ids=[], selected_news_ids=[], no_clear_news=True,
    ))
    # 幽灵：边界对不上任何窗口（挪 5 小时）→ needs_review=True
    session.add(NewsPriceAnnotation(
        symbol="BTC/USDT", window_start=now - timedelta(hours=5),
        window_end=now - timedelta(hours=5) + timedelta(minutes=15),
        context_start=now, context_end=now, change_pct=-2.0,
        news_roles=json.dumps({}), no_clear_news=True, created_at=now, updated_at=now,
    ))
    session.commit()
    items = annotation_service.list_annotations(session, symbol="BTC/USDT", hours=24)
    normal = [it for it in items if it.window_end.timestamp_utc == w.window_end.timestamp_utc]
    ghost = [it for it in items if it.window_end.timestamp_utc != w.window_end.timestamp_utc]
    assert normal and normal[0].needs_review is False
    assert ghost and ghost[0].needs_review is True


def test_auto_annotate_refine_multiturn(session, monkeypatch):
    """Part C 互动重标：把 上一轮输出 + 用户纠正 组成 4 轮对话再调 reasoner，新结果套用。"""
    from schemas.annotations import AutoAnnotateRefineRequest
    n1, n2, n3 = _seed(session)

    captured = {}

    def fake_call(messages):
        captured["messages"] = messages
        return json.dumps({"news_roles": {str(n1): "driver"}, "confidence": 0.9, "summary": "改后"}), "推理", 1.0

    monkeypatch.setattr(annotation_service, "_call_deepseek_reasoner_messages", fake_call)
    resp = annotation_service.auto_annotate_refine(session, AutoAnnotateRefineRequest(
        symbol="BTC/USDT", window_start_utc=W_START.isoformat(), window_end_utc=W_END.isoformat(),
        threshold_pct=1.0, prior_news_roles={n2: "driver"}, prior_summary="旧摘要", prior_confidence=0.5,
        user_message="driver 标错了，应该是第一条美军打击",
    ))
    msgs = captured["messages"]
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "user"]
    assert str(n2) in msgs[2]["content"]                          # 上一轮输出进了 assistant 轮
    assert "美军打击" in msgs[3]["content"]                       # 用户纠正进了末轮 user
    assert resp.news_roles == {n1: "driver"}                      # 新结果套用


def test_auto_annotate_refine_requires_message(session):
    from schemas.annotations import AutoAnnotateRefineRequest
    _seed(session)
    with pytest.raises(ValueError):
        annotation_service.auto_annotate_refine(session, AutoAnnotateRefineRequest(
            symbol="BTC/USDT", window_start_utc=W_START.isoformat(), window_end_utc=W_END.isoformat(),
            threshold_pct=1.0, user_message="   ",
        ))


def test_prompts_drop_retired_roles():
    """prompt 现状：无 post_hoc/contradictory/market_reaction_type；含 redundant + confidence + 派生信号；版本已 bump。"""
    for p in (annotation_service.AUTO_ANNOTATE_SYSTEM_PROMPT, annotation_service.AUTO_ANNOTATE_BATCH_SYSTEM_PROMPT):
        assert "post_hoc" not in p and "contradictory" not in p
        assert "market_reaction_type" not in p          # Part A：市场反应类型退场
        assert "redundant" in p                          # redundant 可标角色
        assert "confidence" in p                         # confidence 保留（训模型用）
        assert "s_scores" in p and "max_ref" in p        # v11 派生信号（S 证据链）
        assert "machine_class" not in p                  # v14（2026-07-19）：机器预分类退出 DeepSeek 输入——机器归因不准
        assert "reference_change_segments" in p and "trigger_move_start_bj" in p and "pre_window_move_pct" in p
    assert annotation_service.ANNOTATION_PROMPT_VERSION != "v4-20260612"
