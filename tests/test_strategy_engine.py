# -*- coding: utf-8 -*-
"""strategy_engine 纯函数单测。手算基准见各断言旁注释。"""
import math
from datetime import datetime, timedelta

import pytest

from services.strategy_engine import (
    DailyCandle,
    ewma_vol_series,
    walk_latch,
)


def _mk_candles(closes, start=datetime(2026, 8, 1)):
    return [
        DailyCandle(date=start + timedelta(days=i), open=c, high=c * 1.02, low=c * 0.98, close=c)
        for i, c in enumerate(closes)
    ]


def test_ewma_vol_matches_hand_calc():
    # 收盘 100 -> 110：r=ln(1.1)；首日方差=r^2（热身种子）
    # 110 -> 99：r2=ln(0.9)；var2 = 0.054*r2^2 + 0.946*r1^2
    closes = [100.0, 110.0, 99.0]
    vols = ewma_vol_series(closes, alpha=0.054)
    r1 = math.log(1.1)
    r2 = math.log(0.9)
    assert vols[0] == pytest.approx(abs(r1))
    assert vols[1] == pytest.approx(math.sqrt(0.054 * r2 * r2 + 0.946 * r1 * r1))
    assert len(vols) == 2


def test_walk_latch_only_updates_beyond_threshold():
    # 起点 5%；5.5% 偏离 10% 不更新；6.5% 偏离 30% 更新
    vols = [0.050, 0.055, 0.065]
    used = walk_latch(vols, threshold=0.25)
    assert used == [pytest.approx(0.050), pytest.approx(0.050), pytest.approx(0.065)]


def test_walk_latch_seed_carries_over():
    # 已持久化在用值 0.04，最新 0.049（+22.5%）不更新；0.051（+27.5%）更新
    assert walk_latch([0.049], threshold=0.25, seed=0.040) == [pytest.approx(0.040)]
    assert walk_latch([0.051], threshold=0.25, seed=0.040) == [pytest.approx(0.051)]


def test_walk_latch_zero_vol_updates_without_crash():
    # 长横盘冷启动会把在用值锁在 0（波动率恰好为 0）；下一个正波动必须直接采用而不是除零。
    assert walk_latch([0.03], threshold=0.25, seed=0.0) == [pytest.approx(0.03)]
    assert walk_latch([0.0, 0.03], threshold=0.25) == [pytest.approx(0.0), pytest.approx(0.03)]
