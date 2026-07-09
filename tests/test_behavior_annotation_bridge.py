# -*- coding: utf-8 -*-
"""标注流对接开关（price-behavior-engine-plan Task 8）：
默认关 = 原路径不变；开 = 待标窗口读 behavior_segments（0.5 档以上）；显式调试参数仍走原始扫描。"""
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


def _seed_segment(session, t0, tier_idx=1, start_min=30, end_min=45):
    session.add(BehaviorSegment(
        symbol="BTC/USDT", start_dt=t0 + timedelta(minutes=start_min),
        end_dt=t0 + timedelta(minutes=end_min), direction=1,
        tier_idx=tier_idx, tier_max=[0.3, 0.5, 0.8][tier_idx],
        net_pct=0.6, amp_pct=0.7, key_ts=t0 + timedelta(minutes=40),
        classification="pure_resonance", class_version="v1",
    ))
    session.commit()


def test_flag_off_keeps_original_path(session, monkeypatch):
    monkeypatch.setattr(config, "BEHAVIOR_REPLACES_ANNOTATION_WINDOWS", False)
    t0 = datetime.utcnow().replace(second=0, microsecond=0) - timedelta(hours=5)
    _seed_flat_prices(session, t0)
    _seed_segment(session, t0)                    # 有段但开关关 → 不读
    assert annotation_service.load_price_windows(session, "BTC/USDT", hours=12) == []


def test_flag_on_reads_segments(session, monkeypatch):
    monkeypatch.setattr(config, "BEHAVIOR_REPLACES_ANNOTATION_WINDOWS", True)
    t0 = datetime.utcnow().replace(second=0, microsecond=0) - timedelta(hours=5)
    _seed_flat_prices(session, t0)
    _seed_segment(session, t0, tier_idx=1)        # 0.5 档 → 进待标
    _seed_segment(session, t0, tier_idx=0, start_min=100, end_min=115)  # 0.3 档 → 只计数不进
    windows = annotation_service.load_price_windows(session, "BTC/USDT", hours=12)
    assert len(windows) == 1
    w = windows[0]
    assert w.window_start.timestamp_utc.startswith((t0 + timedelta(minutes=30)).isoformat()[:16])
    assert w.annotatable is True                  # 段已远离生长边缘
    assert w.price_start == 100.0 and w.price_end == 100.0


def test_flag_on_debug_params_bypass(session, monkeypatch):
    monkeypatch.setattr(config, "BEHAVIOR_REPLACES_ANNOTATION_WINDOWS", True)
    t0 = datetime.utcnow().replace(second=0, microsecond=0) - timedelta(hours=5)
    _seed_flat_prices(session, t0)
    _seed_segment(session, t0)
    # 显式 threshold 调试路径：走原始扫描（平价 → 无窗口），不读段表
    assert annotation_service.load_price_windows(
        session, "BTC/USDT", hours=12, threshold_pct=0.5, window_minutes=15,
    ) == []
