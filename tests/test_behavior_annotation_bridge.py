# -*- coding: utf-8 -*-
"""标注页窗口源固定段化（price-behavior-engine-phase2-plan Task 4）：
窗口 = behavior_segments（0.5 档以上，带段证据与簇拥 0.3 计数）；显式调试参数仍走原始扫描。"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import config
from database import Base
from models.behavior import BehaviorSegment
from models.price import PriceSnapshot
from services import annotation_service


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _seed_flat_prices(session, t0, n=60, symbol="BTC/USDT"):
    """平价序列：原始扫描不出任何窗口，便于区分两条路径。"""
    for i in range(n):
        session.add(PriceSnapshot(timestamp=t0 + timedelta(minutes=5 * i), asset_class="crypto",
                                  symbol=symbol, name="BTC", price=100.0, source="test"))
    session.commit()


def _seg_row(session, t0, tier_idx=1, start_min=30, end_min=45):
    import json as _json
    seg = BehaviorSegment(
        symbol="BTC/USDT", start_dt=t0 + timedelta(minutes=start_min),
        end_dt=t0 + timedelta(minutes=end_min), direction=1,
        tier_idx=tier_idx, tier_max=[0.3, 0.5, 0.8][tier_idx],
        net_pct=0.6, amp_pct=0.7, key_ts=t0 + timedelta(minutes=40),
        classification="pure_resonance" if tier_idx >= 1 else "count_only", class_version="v1",
        s_scores=_json.dumps({"NQ=F": {"s": 0.77, "ess": 4.3, "coverage": 1.0}}) if tier_idx >= 1 else None,
    )
    session.add(seg)
    session.commit()
    return seg


def test_windows_read_segments_with_evidence(session):
    t0 = datetime.utcnow().replace(second=0, microsecond=0) - timedelta(hours=5)
    _seed_flat_prices(session, t0)
    seg = _seg_row(session, t0, tier_idx=1)                              # 0.5 档 → 进待标
    _seg_row(session, t0, tier_idx=0, start_min=100, end_min=115)        # 0.3 档 → 不进列表
    _seg_row(session, t0, tier_idx=0, start_min=0, end_min=10)           # 段 ±1h 内簇拥 0.3
    windows = annotation_service.load_price_windows(session, "BTC/USDT", hours=12)
    assert len(windows) == 1
    w = windows[0]
    assert w.window_start.timestamp_utc.startswith((t0 + timedelta(minutes=30)).isoformat()[:16])
    assert w.annotatable is True                  # 段已远离生长边缘
    assert w.price_start == 100.0 and w.price_end == 100.0
    # 段证据随行
    assert w.tier_idx == 1 and w.tier_max == 0.5
    assert w.machine_class == "pure_resonance"
    assert w.s_scores["NQ=F"]["s"] == 0.77
    assert w.cluster03_count == 2                 # 两个 0.3 段都在 ±1h 内
    assert w.human_class is None


def test_debug_params_bypass_segments(session):
    t0 = datetime.utcnow().replace(second=0, microsecond=0) - timedelta(hours=5)
    _seed_flat_prices(session, t0)
    _seg_row(session, t0)
    # 显式 threshold 调试路径：走原始扫描（平价 → 无窗口），不读段表
    assert annotation_service.load_price_windows(
        session, "BTC/USDT", hours=12, threshold_pct=0.5, window_minutes=15,
    ) == []


def test_annotation_overlap_matching(session):
    """段边界(0.3基座)比旧 0.5 窗口宽：历史标注按重叠≥50% 找回。"""
    from models.news import NewsPriceAnnotation
    t0 = datetime.utcnow().replace(second=0, microsecond=0) - timedelta(hours=5)
    _seed_flat_prices(session, t0)
    _seg_row(session, t0, start_min=20, end_min=60)          # 段 t+20 ~ t+60
    session.add(NewsPriceAnnotation(
        symbol="BTC/USDT",
        window_start=t0 + timedelta(minutes=40),             # 旧 0.5 窗口 t+40 ~ t+55 ⊂ 段
        window_end=t0 + timedelta(minutes=55),
        context_start=t0, context_end=t0 + timedelta(minutes=85),
        threshold_pct=0.5, price_start=100.0, price_end=100.6, change_pct=0.6,
    ))
    session.commit()
    windows = annotation_service.load_price_windows(session, "BTC/USDT", hours=12)
    assert len(windows) == 1
    assert windows[0].annotation_id is not None              # 重叠匹配找回旧标注
