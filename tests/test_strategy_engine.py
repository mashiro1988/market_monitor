# -*- coding: utf-8 -*-
"""strategy_engine 纯函数单测。手算基准见各断言旁注释。"""
import math
from datetime import datetime, timedelta

import pytest

from services.strategy_engine import (
    DailyCandle,
    anchor_high,
    batch_state,
    ewma_vol_series,
    simulate_entry,
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


def test_anchor_high_floors_at_entry_price_and_respects_entry_time():
    candles = _mk_candles([0.70, 0.7518, 0.7323], start=datetime(2026, 8, 25))
    # 8-26 23:33 入场：8-25 的 K（收盘时刻 8-26 00:00 < 入场）不算，
    # 8-26 的 K（收盘时刻 8-27 00:00 > 入场）算 => H = max(0.743, 0.7518, 0.7323)
    h = anchor_high(entry_price=0.743, entry_at=datetime(2026, 8, 26, 23, 33), candles=candles)
    assert h == pytest.approx(0.7518)
    # 入场价保底：所有入场后收盘都低于成本时 H = 入场价
    h2 = anchor_high(entry_price=0.80, entry_at=datetime(2026, 8, 26, 23, 33), candles=candles)
    assert h2 == pytest.approx(0.80)


def test_batch_state_lines_occupancy_and_flags():
    candles = _mk_candles([0.70, 0.7518, 0.7323], start=datetime(2026, 8, 25))
    st = batch_state(
        entry_price=0.743, entry_at=datetime(2026, 8, 26, 23, 33), quantity=23590,
        candles=candles, v_used=0.0494, x_soft=4, x_hard=6,
    )
    # soft = 0.7518*(1-4*0.0494)；hard = 0.7518*(1-6*0.0494)
    assert st.soft_stop == pytest.approx(0.7518 * (1 - 4 * 0.0494))
    assert st.hard_stop == pytest.approx(0.7518 * (1 - 6 * 0.0494))
    assert st.breached is False           # 昨收 0.7323 > soft
    assert st.locked is False             # soft < 成本 0.743
    assert st.occupy_usd == pytest.approx(23590 * (0.743 - st.soft_stop))


def test_batch_state_breach_and_lock():
    # 大涨后 soft 抬过成本 => locked、占用归零；再暴跌收盘 < soft => breached
    up = _mk_candles([0.70, 1.00, 0.79], start=datetime(2026, 8, 25))
    st = batch_state(entry_price=0.70, entry_at=datetime(2026, 8, 25, 1, 0), quantity=1000,
                     candles=up, v_used=0.05, x_soft=4, x_hard=6)
    # H=1.00, soft=0.80；昨收 0.79 < 0.80 => breached；soft(0.80) > 成本(0.70) => locked
    assert st.locked is True and st.breached is True
    assert st.occupy_usd == 0.0


def test_simulate_entry_matches_framework_example():
    # 设计稿 §7 沙盘：本金19000*25%=4750 预算、价0.70、vol 5%、X=4、forecast=15
    sim = simulate_entry(price=0.70, forecast=15, budget_usd=4750.0, vol=0.05, x_soft=4)
    assert sim["stop_price"] == pytest.approx(0.56)
    assert sim["stop_distance"] == pytest.approx(0.14)
    # 数量 = 预算*(F/10)/距离 = 4750*1.5/0.14；名义 = 数量*价
    assert sim["quantity"] == pytest.approx(4750 * 1.5 / 0.14)
    assert sim["notional_usd"] == pytest.approx(sim["quantity"] * 0.70)
