# -*- coding: utf-8 -*-
"""共振分（Phase 2 rolling 统一口径，price-behavior-engine-phase2-plan Task 1）。
Fixture = spec §6 的 25 点数值案例按 rolling_peak（25 点窗、尾窗 60min）重算锁死：
变体 A（纳指跟随）峰值 S≈0.834 / ESS≈3.34（峰在 21:35）；变体 B（纳指没动）≈0.022。
所见即所判：判级数 = 展示曲线的 |S| 峰值。"""
from datetime import datetime, timedelta

from services.resonance_score import chg_map, rolling_peak, rolling_s

T_BTC = 0.30
T_NQ = 0.23
BASE = datetime(2026, 7, 8, 20, 30)   # 25 个 15min 滚动点，5min 步进（20:30 ~ 22:30）

BTC = [0.04, -0.06, 0.02, -0.03, 0.05, -0.02, 0.01, -0.04, 0.03,
       0.12, 0.28, 0.55, 0.72, 0.61, 0.38, 0.22, 0.10,
       -0.05, 0.06, -0.03, 0.02, -0.06, 0.04, -0.02, 0.03]
NQ_A = [-0.02, 0.03, -0.01, 0.02, -0.03, 0.01, -0.02, 0.02, -0.01,
        0.02, 0.08, 0.15, 0.26, 0.21, 0.12, 0.06, 0.03,
        -0.02, 0.01, -0.02, 0.02, -0.01, 0.01, -0.01, 0.02]
NQ_B = [-0.02, 0.03, -0.01, 0.02, -0.03, 0.01, -0.02, 0.02, -0.01,
        0.01, -0.02, 0.02, -0.01, 0.02, -0.02, 0.01, -0.01,
        -0.02, 0.01, -0.02, 0.02, -0.01, 0.01, -0.01, 0.02]


def _map(vals):
    return {BASE + timedelta(minutes=5 * i): v for i, v in enumerate(vals)}

# 段取 21:30–21:30（退化段）→ 大窗口 = ±1h = 恰好 20:30~22:30 的 25 个点
SEG = (datetime(2026, 7, 8, 21, 30), datetime(2026, 7, 8, 21, 30))


def test_spec_case_a_nq_follows():
    s, ess, cov = rolling_peak(_map(BTC), _map(NQ_A), T_BTC, T_NQ, SEG[0], SEG[1], points=25)
    assert abs(s - 0.834) < 1e-3     # 峰值出现在 21:35（窗口恰罩住最重的两根 bar）
    assert abs(ess - 3.34) < 0.01    # 峰值时刻的证据厚度（比全窗更集中，语义正确）
    assert cov == 1.0


def test_spec_case_b_nq_flat():
    s, ess, cov = rolling_peak(_map(BTC), _map(NQ_B), T_BTC, T_NQ, SEG[0], SEG[1], points=25)
    assert abs(s - 0.022) < 1e-3
    assert cov == 1.0


def test_inverse_ref_scores_negative():
    btc = {BASE: 0.60, BASE + timedelta(minutes=5): 0.05, BASE + timedelta(minutes=10): -0.02}
    dxy = {BASE: -0.20, BASE + timedelta(minutes=5): -0.01, BASE + timedelta(minutes=10): 0.00}
    r = rolling_peak(btc, dxy, 0.30, 0.10, BASE, BASE + timedelta(minutes=10),
                     tail_min=0, points=3)
    assert r is not None and r[0] < -0.8   # 反向满档：主权重 bar clip 到 -1，峰取 |S| 最大

def test_peak_is_max_abs_over_grid():
    # 构造：段早期参照反向、后期强跟随 → 峰值应取后期高点而非首点
    btc = _map(BTC)
    nq = dict(_map(NQ_A))
    assert abs(rolling_peak(btc, nq, T_BTC, T_NQ, SEG[0], SEG[1], points=25)[0]) >=            abs(rolling_peak(btc, nq, T_BTC, T_NQ, SEG[0], SEG[0], tail_min=0, points=25)[0]) - 1e-9


def test_low_coverage_returns_none():
    btc = _map(BTC)
    nq = {BASE + timedelta(minutes=5 * i): v for i, v in enumerate(NQ_A) if i < 10}  # 只覆盖前 40%
    assert rolling_peak(btc, nq, T_BTC, T_NQ, SEG[0], SEG[1], points=25) is None


def test_empty_window_returns_none():
    assert rolling_peak({}, _map(NQ_A), T_BTC, T_NQ, SEG[0], SEG[1], points=25) is None


def test_chg_map_15min_exact_span():
    t0 = datetime(2026, 7, 8, 12, 0)
    pts = [(t0 + timedelta(minutes=5 * i), 100 + i) for i in range(5)]
    m = chg_map(pts)
    # 只有存在恰好 t-15min 点的时刻出值
    assert set(m) == {t0 + timedelta(minutes=15), t0 + timedelta(minutes=20)}
    assert abs(m[t0 + timedelta(minutes=15)] - 3.0) < 1e-9


def test_rolling_s_gap_when_ref_missing():
    btc = _map(BTC)
    nq = _map(NQ_A)
    # 参照缺后半段 → 后半的滚动点覆盖不足 → None
    for i in range(12, 25):
        nq.pop(BASE + timedelta(minutes=5 * i))
    series = rolling_s(btc, nq, T_BTC, T_NQ,
                       start=BASE + timedelta(minutes=5 * 24), end=BASE + timedelta(minutes=5 * 24),
                       points=25)
    assert series == [(BASE + timedelta(minutes=120), None)]


def test_rolling_peak_ess_floor_skips_degenerate_points():
    """ESS 地板（2026-07-11 用户实弹：某窗口 ESS 1.0、S=-1.00——峰值被"单根 K 线撑起的
    退化读数"抢走，与联动曲线肉眼峰值对不上）：ess_min 给定时，峰值只在证据厚度达标的
    时点里取；全部不达标才退回无门槛峰值。"""
    from datetime import datetime, timedelta
    t0 = datetime(2026, 7, 9, 1, 0)
    step = timedelta(minutes=5)
    # BTC：前段死平（单根 spike → 早期窗口 ESS≈1），后段持续波动（ESS 高）
    btc, nq = {}, {}
    moves = [0.0] * 9 + [0.9] + [0.0] * 10 + [0.5, -0.4, 0.6, -0.5, 0.45, -0.35, 0.55, -0.45, 0.4, -0.3]
    for i, m in enumerate(moves):
        btc[t0 + step * i] = m
        # 前段 NQ 与 spike 完全反向（把退化点推到 |S|=1）；后段同向跟随（真实共振但 |S|<1）
        nq[t0 + step * i] = (-m if i <= 19 else m * 0.8)
    seg = (t0 + step * 9, t0 + step * 29)
    unfloored = rolling_peak(btc, nq, 0.3, 0.23, seg[0], seg[1], tail_min=0, points=10, coverage_min=0.0)
    floored = rolling_peak(btc, nq, 0.3, 0.23, seg[0], seg[1], tail_min=0, points=10, coverage_min=0.0, ess_min=5.0)
    assert unfloored is not None and floored is not None
    assert unfloored[1] < 5.0 and unfloored[0] < 0     # 无地板：单根反向 spike 抢峰，ESS 薄
    assert floored[1] >= 5.0 and floored[0] > 0        # 有地板：取证据厚的正向共振峰
