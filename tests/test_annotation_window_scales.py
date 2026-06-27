# -*- coding: utf-8 -*-
"""单 15min 开收净窗口（news-impact-engine Phase 2）：

触发 = (窗口末收盘 − 窗口初开盘)/初开盘 ≥ threshold（含第一根 bar）；
收口 = 同向且扫描点相邻(≤5min)则合并，变向或断档(>5min)则上一窗走完。
无 60m 档、无跨档合并、无独立 net_min（threshold 即最小净幅度）。"""
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
        "TEST": [{"window_minutes": 15, "threshold_pct": 1.0, "pre_minutes": 30}],
    })
    monkeypatch.setattr(config, "ANNOTATION_EVENT_MERGE_GAP_MINUTES", 5)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _series(session, prices, step_min=5):
    """从 now 往回构造 5m 序列：prices[0] 最早。"""
    now = utc_now_naive().replace(second=0, microsecond=0)
    start = now - timedelta(minutes=step_min * (len(prices) - 1))
    for i, p in enumerate(prices):
        session.add(PriceSnapshot(
            timestamp=start + timedelta(minutes=step_min * i),
            asset_class="futures", symbol="TEST", name="TEST", price=p, source="t",
        ))
    session.commit()


def test_directional_move_one_window(session):
    """单向急跌 -1.5%（15min 净 ≥ 1.0%）→ 1 个窗口，方向为负。"""
    prices = [10000.0] * 6 + [9950.0, 9900.0, 9870.0, 9850.0] + [9850.0] * 6
    _series(session, prices)
    wins = load_price_windows(session, "TEST", hours=24)
    assert len(wins) == 1
    assert wins[0].change_pct == pytest.approx(-1.5, abs=0.1)
    assert wins[0].configured_window_minutes == 15


def test_subthreshold_drift_no_window(session):
    """慢阴跌：每 15min 净仅 ~0.22%（< 1.0% 阈值）→ 不出窗口（删 60m 档的有意取舍）。"""
    n = 16
    prices = [10000.0] * 4 + [10000.0 * (1 - 0.0125 * i / n) for i in range(1, n + 1)] + [9875.0] * 4
    _series(session, prices)
    assert load_price_windows(session, "TEST", hours=24) == []


def test_subthreshold_chop_no_window(session):
    """小振幅横跳：每条腿 ±0.6%（< 1.0% 阈值）→ 不触发、不出窗口。"""
    base = 10000.0
    prices = [base]
    for _ in range(6):
        prices += [base * 1.006, base * 1.006, base, base]
    _series(session, prices)
    assert load_price_windows(session, "TEST", hours=24) == []


def test_single_skipped_bar_merges_no_overlap(session):
    """同向急跌中途只有一根 bar 没触发（15min 净瞬时回到阈下）：两段的覆盖区间仍**重叠**
    （seg2 的 start_dt 早于 seg1 的 end_dt）→ 必须并成 **1** 个窗口，绝不能产出两个重叠窗口。
    线上实测 bug：BTC 20:50→21:15 与 21:10→21:25 重叠却被拆开（end_dt-based 合并判据之误）。"""
    # bar4 跌到 9880(触发 bar4/5/6)；bar7 一根没触发；bar8 再跌到 9760(触发 bar8/9/10)。
    prices = (
        [10000.0, 10000.0, 10000.0, 10000.0]   # bars 0-3
        + [9880.0, 9880.0, 9880.0, 9880.0]      # bars 4-7：-1.2%，bar7 一根没触发
        + [9760.0, 9760.0, 9760.0]              # bars 8-10：再 -1.2%
        + [9760.0, 9760.0, 9760.0]              # bars 11-13 平尾
    )
    _series(session, prices)
    wins = load_price_windows(session, "TEST", hours=24)
    assert len(wins) == 1                       # 覆盖区间重叠 → 并成一个


def test_long_pause_splits_into_two(session):
    """同向两段急跌，中间**真正静默够久**（覆盖区间不再重叠、区间间隔 > 5min）→ 才拆成 2。"""
    prices = (
        [10000.0, 10000.0, 10000.0, 10000.0]                       # bars 0-3
        + [9880.0, 9880.0, 9880.0, 9880.0, 9880.0, 9880.0, 9880.0]  # bars 4-10：-1.2% 后长平台
        + [9760.0, 9760.0, 9760.0]                                 # bars 11-13：再 -1.2%
    )
    _series(session, prices)
    wins = load_price_windows(session, "TEST", hours=24)
    assert len(wins) == 2                       # 覆盖区间不重叠、间隔 > 5min → 拆开


def test_continuous_same_direction_merges(session):
    """连续多根同向急跌(扫描点相邻) → 合并成 1 个窗口、segment_count > 1。"""
    prices = [10000.0] * 3 + [9930.0, 9860.0, 9790.0, 9720.0] + [9720.0] * 3
    _series(session, prices)
    wins = load_price_windows(session, "TEST", hours=24)
    assert len(wins) == 1
    assert wins[0].segment_count >= 2


def test_direction_flip_closes_window(session):
    """急涨后紧接急跌(连续、变向) → 收口成 2 个窗口，符号相反。"""
    prices = [10000.0] * 3 + [10120.0, 10240.0] + [10120.0, 10000.0] + [10000.0] * 3
    _series(session, prices)
    wins = load_price_windows(session, "TEST", hours=24)
    assert len(wins) == 2
    signs = {1 if w.change_pct > 0 else -1 for w in wins}
    assert signs == {1, -1}


def test_explicit_params_single_scale(session):
    """显式传 threshold/window（调试路径）：单档、阈值即净门槛。"""
    prices = [10000.0] * 3 + [9930.0, 9860.0, 9790.0] + [9790.0] * 3
    _series(session, prices)
    wins = load_price_windows(session, "TEST", hours=24, threshold_pct=0.5, window_minutes=15)
    assert len(wins) >= 1
