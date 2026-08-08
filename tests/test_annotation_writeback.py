# -*- coding: utf-8 -*-
"""标注保存回写段 human_class（price-behavior-engine-phase2-plan Task 3）：
重叠≥50% 匹配、非法类别报错、无段/低重叠静默、legacy 请求不受影响。

2026-08-08 追加：三类结论同时落标注自身列 window_class（不再只靠段匹配），
段匹配失败也要留住结论；列表接口带出该列。"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models.behavior import BehaviorSegment
from models.price import PriceSnapshot
from schemas.annotations import AnnotationCreateRequest
from services import annotation_service

T0 = datetime(2026, 7, 8, 9, 0)


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    # 窗口起止点需要价格快照（upsert 前置校验）
    for i in range(30):
        s.add(PriceSnapshot(timestamp=T0 + timedelta(minutes=5 * i), asset_class="crypto",
                            symbol="BTC/USDT", name="BTC", price=100 + i * 0.1, source="test"))
    s.commit()
    yield s
    s.close()


def _seg(session, start_min, end_min, tier_idx=1):
    seg = BehaviorSegment(
        symbol="BTC/USDT", start_dt=T0 + timedelta(minutes=start_min),
        end_dt=T0 + timedelta(minutes=end_min), direction=1,
        tier_idx=tier_idx, tier_max=[0.3, 0.5, 0.8][tier_idx],
        net_pct=0.6, classification="pure_resonance", class_version="v1",
    )
    session.add(seg)
    session.commit()
    return seg


def _req(start_min, end_min, window_class=None):
    return AnnotationCreateRequest(
        symbol="BTC/USDT",
        window_start_utc=(T0 + timedelta(minutes=start_min)).isoformat(),
        window_end_utc=(T0 + timedelta(minutes=end_min)).isoformat(),
        threshold_pct=0.5,
        news_roles={}, market_reaction_type="no_news_driver", confidence=0.9,
        window_class=window_class,
    )


def test_writeback_on_overlap(session):
    seg = _seg(session, 20, 60)                       # 段 09:20–10:00（0.3 基座更宽）
    annotation_service.upsert_annotation(session, _req(40, 55, "sentiment_tech"))  # 窗 09:40–09:55 ⊂ 段
    session.refresh(seg)
    assert seg.human_class == "sentiment_tech"
    assert seg.human_confirmed_at is not None


def test_no_writeback_when_overlap_below_half(session):
    seg = _seg(session, 0, 20)                        # 段 09:00–09:20
    annotation_service.upsert_annotation(session, _req(15, 75, "news_driven"))     # 窗 60min，重叠 5min
    session.refresh(seg)
    assert seg.human_class is None                    # 5/20 = 25% < 50%


def test_invalid_window_class_raises(session):
    _seg(session, 20, 60)
    with pytest.raises(ValueError, match="window_class"):
        annotation_service.upsert_annotation(session, _req(40, 55, "macro_news"))  # 六类不再合法


def test_legacy_request_without_window_class_ok(session):
    seg = _seg(session, 20, 60)
    resp = annotation_service.upsert_annotation(session, _req(40, 55, None))
    assert resp.saved is True or resp.id             # 正常保存
    session.refresh(seg)
    assert seg.human_class is None                   # 不回写


# ---------- 标注自带 window_class 列（2026-08-08） ----------

def _ann_row(session):
    from models.news import NewsPriceAnnotation
    return session.query(NewsPriceAnnotation).one()


def test_window_class_lands_on_annotation_row(session):
    """三类结论落标注自身列——列表要靠它区分纯共振 / 情绪·技术面。"""
    _seg(session, 20, 60)
    annotation_service.upsert_annotation(session, _req(40, 55, "pure_resonance"))
    assert _ann_row(session).window_class == "pure_resonance"


def test_window_class_persists_without_matching_segment(session):
    """段匹配不上（重叠<50%）时段不回写，但标注自身仍留住结论——这正是加列要解决的丢失。"""
    seg = _seg(session, 0, 20)
    annotation_service.upsert_annotation(session, _req(15, 75, "sentiment_tech"))
    session.refresh(seg)
    assert seg.human_class is None                   # 段照旧不回写
    assert _ann_row(session).window_class == "sentiment_tech"


def test_window_class_can_be_cleared_on_resave(session):
    """改判清空要能落回 NULL（不能被上一次的值粘住）。"""
    _seg(session, 20, 60)
    annotation_service.upsert_annotation(session, _req(40, 55, "news_driven"))
    assert _ann_row(session).window_class == "news_driven"
    annotation_service.upsert_annotation(session, _req(40, 55, None))   # 同窗口 upsert
    assert _ann_row(session).window_class is None


def test_invalid_window_class_not_written_to_row(session):
    """非法值必须在落列前被拦下，不能先写进对象再抛错。"""
    _seg(session, 20, 60)
    annotation_service.upsert_annotation(session, _req(40, 55, "pure_resonance"))
    with pytest.raises(ValueError, match="window_class"):
        annotation_service.upsert_annotation(session, _req(40, 55, "macro_news"))
    session.rollback()
    assert _ann_row(session).window_class == "pure_resonance"    # 旧值未被污染


def test_list_annotations_exposes_window_class(session):
    """已标注列表带出该列（前端据此显示纯共振/情绪徽章而非笼统的"无明确诱因"）。"""
    _seg(session, 20, 60)
    annotation_service.upsert_annotation(session, _req(40, 55, "pure_resonance"))
    items = annotation_service.list_annotations(session, symbol="BTC/USDT", hours=0).items
    assert len(items) == 1
    assert items[0].window_class == "pure_resonance"
    assert items[0].no_clear_news is True            # 无 driver：两个信号并存，各答各的问题
