# -*- coding: utf-8 -*-
"""多尺度标注窗口（v2.1）：净变动门槛杀横跳、60m 档捕慢跌、跨档重叠合并。

设计依据：2026-06-10 夜实测——±0.4% 横跳产出 5 个垃圾窗口，而 -1.36% 慢跌
（单个 15min 变动仅 0.38~0.52%）只能擦线碎片化触发。"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import config
from database import Base
from models.price import PriceSnapshot
from services.annotation_service import load_price_windows
from services.time_utils import utc_now_naive


@pytest.fixture
def session(monkeypatch):
    monkeypatch.setattr(config, "ANNOTATION_REFERENCE_ASSETS", [])
    monkeypatch.setattr(config, "ANNOTATION_WINDOW_SCALES", {
        "TEST": [
            {"window_minutes": 15, "threshold_pct": 0.5, "net_min_pct": 1.0, "pre_minutes": 30},
            {"window_minutes": 60, "threshold_pct": 0.8, "net_min_pct": 1.0, "pre_minutes": 60},
        ],
    })
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _series(session, prices: list[float], step_min: int = 5):
    """从 now 往回构造 5m 序列：prices[0] 最早。"""
    now = utc_now_naive().replace(second=0, microsecond=0)
    start = now - timedelta(minutes=step_min * (len(prices) - 1))
    for i, p in enumerate(prices):
        session.add(PriceSnapshot(
            timestamp=start + timedelta(minutes=step_min * i),
            asset_class="futures", symbol="TEST", name="TEST", price=p, source="t",
        ))
    session.commit()


def test_net_filter_kills_chop(session):
    """来回横跳：单段 15min 触发（±0.6%）但净变动归零 → 不产出窗口。"""
    base = 10000.0
    prices = [base]
    for _ in range(6):                       # 上 0.6% 再下 0.6%，反复
        prices += [base * 1.006, base * 1.006, base, base]
    _series(session, prices)
    assert load_price_windows(session, "TEST", hours=24) == []


def test_real_move_passes_net_filter(session):
    """单向急跌 -1.5%：15m 档触发且净达标 → 1 个窗口。"""
    prices = [10000.0] * 6 + [9950.0, 9900.0, 9870.0, 9850.0] + [9850.0] * 6
    _series(session, prices)
    wins = load_price_windows(session, "TEST", hours=24)
    assert len(wins) == 1
    assert wins[0].change_pct == pytest.approx(-1.5, abs=0.05)
    assert wins[0].context_pre_minutes == 60          # -1.5% 同时触发两档 → 合并取大档前置窗


def test_slow_drift_caught_by_60m_scale(session):
    """80 分钟阴跌 -1.2%（每 15min 仅 -0.22%，15m 档无触发）→ 60m 档捕获。"""
    n = 16                                            # 80 分钟、每根 -0.075%
    prices = [10000.0] * 4 + [10000.0 * (1 - 0.0125 * i / n) for i in range(1, n + 1)] + [8875.0 and 10000.0 * (1 - 0.0125)] * 4
    _series(session, prices)
    wins = load_price_windows(session, "TEST", hours=24)
    assert len(wins) == 1
    assert wins[0].change_pct <= -1.0
    assert wins[0].context_pre_minutes == 60          # 60m 档的前置窗
    assert wins[0].configured_window_minutes == 60


def test_fast_spike_not_duplicated_across_scales(session):
    """快速急跌同时触发 15m 与 60m 两档 → 跨档合并为 1 个窗口（不重复）。"""
    prices = [10000.0] * 13 + [9930.0, 9860.0, 9800.0] + [9800.0] * 13
    _series(session, prices)
    wins = load_price_windows(session, "TEST", hours=24)
    assert len(wins) == 1
    assert wins[0].context_pre_minutes == 60          # 合并取大档前置窗


def test_explicit_params_keep_legacy_semantics(session):
    """显式传 threshold/window（调试路径）：单档、无净门槛——横跳也出窗口。"""
    base = 10000.0
    prices = [base]
    for _ in range(3):
        prices += [base * 1.006, base * 1.006, base, base]
    _series(session, prices)
    wins = load_price_windows(session, "TEST", hours=24, threshold_pct=0.5, window_minutes=15)
    assert len(wins) >= 1
